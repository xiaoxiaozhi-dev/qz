"""Release integrity, transactional updates and migration, using disposable installs."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import paths
from maintenance import configuration, release, updater
from maintenance.version import read_version


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.make('installed', '0.1.0')
        self.new = self.make('new', '0.2.0')
        self.package = self.root / 'update.zip'

    def tearDown(self):
        self.temp.cleanup()

    def make(self, name, version):
        folder = self.root / name
        folder.mkdir()
        for filename in ('QzTools.exe', 'QzUpdater.exe'):
            (folder / filename).write_bytes((name + filename).encode())
        (folder / 'version.json').write_text(json.dumps(dict(app_id='QzTools', version=version, update_format=1)))
        (folder / 'defaults.json').write_text(json.dumps(dict(bridge=dict(ports=dict(maya=17023, max=17024)))))
        plugin = folder / 'plugins' / 'maya' / 'demo'
        plugin.mkdir(parents=True)
        (plugin / 'widget.py').write_text(name)
        release.create_manifest(folder)
        return folder

    def pack(self):
        release.create_manifest(self.new)
        return release.create_package(self.new, self.package)

    def test_package_integrity_and_version_have_one_source(self):
        info = self.pack()
        self.assertEqual(info['sha256'], release.digest(self.package))
        self.assertEqual(release.inspect_package(self.package)['version'], read_version(self.new)['version'])
        (self.new / 'QzTools.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            release.verify_directory(self.new)

    def test_update_replaces_official_files_removes_obsolete_and_retains_user_data(self):
        stale = self.install / 'plugins' / 'maya' / 'retired' / 'widget.py'
        stale.parent.mkdir()
        stale.write_text('old')
        release.create_manifest(self.install)
        preserved = ['data/settings.json', 'user_plugins/max/我的工具/files/main.ms',
                     'temp/skin_weights/weights.qzskin', 'plugins/maya/local-note.txt', 'config.json']
        for relative in preserved:
            path = self.install / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('user-owned')
        self.pack()
        result = updater.apply_update(self.package, self.install)
        self.assertTrue(result['ok'])
        self.assertEqual(read_version(self.install)['version'], '0.2.0')
        self.assertEqual((self.install / 'QzTools.exe').read_bytes(), b'newQzTools.exe')
        self.assertFalse(stale.exists())
        for relative in preserved:
            self.assertEqual((self.install / relative).read_text(), 'user-owned')
        self.assertEqual((Path(result['backup']) / 'QzTools.exe').read_bytes(), b'installedQzTools.exe')

    def test_replacement_failure_restores_exact_previous_files_and_registration(self):
        self.pack()
        originals = {p.relative_to(self.install).as_posix(): p.read_bytes() for p in self.install.rglob('*') if p.is_file()}
        original_replace = updater._replace
        failed = []
        def fail_once(source, destination):
            if str(destination).endswith('widget.py') and not failed:
                failed.append(True)
                raise PermissionError('locked test file')
            original_replace(source, destination)
        with patch.object(updater, '_replace', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, '恢复'):
                updater.apply_update(self.package, self.install)
        for relative, data in originals.items():
            self.assertEqual((self.install / relative).read_bytes(), data)
        result = json.loads(next((self.install / 'updates').glob('install-*/result.json')).read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'rolled_back')

    def test_failed_new_executable_selftest_rolls_back(self):
        self.pack()
        with self.assertRaises(RuntimeError):
            updater.apply_update(self.package, self.install, verify_installed=lambda root: (_ for _ in ()).throw(RuntimeError('selftest failed')))
        self.assertEqual(read_version(self.install)['version'], '0.1.0')
        self.assertEqual((self.install / 'QzTools.exe').read_bytes(), b'installedQzTools.exe')

    def test_corrupt_payload_is_rejected_before_any_replacement(self):
        self.pack()
        altered = self.root / 'altered.zip'
        with zipfile.ZipFile(self.package) as source, zipfile.ZipFile(altered, 'w') as output:
            for info in source.infolist():
                payload = source.read(info.filename)
                output.writestr(info, b'X' * len(payload) if info.filename == 'QzTools.exe' else payload)
        with self.assertRaisesRegex(RuntimeError, '校验失败'):
            updater.apply_update(altered, self.install)
        self.assertEqual((self.install / 'QzTools.exe').read_bytes(), b'installedQzTools.exe')

    def test_path_traversal_user_data_paths_case_aliases_and_file_directory_conflicts_rejected(self):
        for relative in ('../QzTools.exe', '/QzTools.exe', 'C:/QzTools.exe', 'plugins/../data/x',
                         'user_plugins/max/script.py', 'data/settings.json', 'plugins/maya/CON.py',
                         'plugins\\maya\\x.py', 'plugins/maya/x.py:stream', 'plugins/maya/x. '):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                release.safe_relative(relative)
        data = release.create_manifest(self.new)
        data['files'].append(dict(data['files'][0]))
        with self.assertRaises(ValueError):
            release.validate_manifest(data)
        data = release.create_manifest(self.new)
        data['files'].append(dict(path='plugins/maya', size=0, sha256='0' * 64))
        with self.assertRaises(ValueError):
            release.validate_manifest(data)

    def test_unlisted_zip_members_and_missing_files_rejected(self):
        self.pack()
        with zipfile.ZipFile(self.package, 'a') as archive:
            archive.writestr('user_plugins/max/overwrite.ms', 'bad')
        with self.assertRaises(ValueError):
            updater.apply_update(self.package, self.install)
        self.assertEqual(read_version(self.install)['version'], '0.1.0')

    def test_source_install_and_downgrade_are_rejected(self):
        self.pack()
        (self.install / 'main.py').write_text('source')
        with self.assertRaisesRegex(ValueError, '开发'):
            updater.apply_update(self.package, self.install)
        (self.install / 'main.py').unlink()
        info = read_version(self.install)
        info['version'] = '0.3.0'
        (self.install / 'version.json').write_text(json.dumps(info))
        release.create_manifest(self.install)
        with self.assertRaisesRegex(ValueError, '旧版本'):
            updater.apply_update(self.package, self.install)

    def test_release_builder_refuses_user_assets_and_includes_nested_control_icons(self):
        import build
        stage = self.root / 'stage'
        stage.mkdir()
        with patch.object(build, 'STAGE_DIST', str(stage)):
            build._clean()
            build._copy_resources()
        self.assertTrue((stage / 'QzTools/ui/resources/controls/check.svg').is_file())
        self.assertFalse((stage / 'QzTools/user_plugins').exists())
        for name in ('data', 'temp', 'user_plugins'):
            folder = self.new / name
            folder.mkdir()
            (folder / 'user.txt').write_text('never ship')
            with self.assertRaises(ValueError):
                release.create_manifest(self.new)
            shutil.rmtree(folder)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'home'
        (self.home / '.qz-tool').mkdir(parents=True)
        self.environment = patch.dict(os.environ, {'QZ_DATA_DIR': ''})
        self.environment.start()
        self.project_patch = patch.object(paths, 'project_dir', return_value=str(self.project))
        self.project_patch.start()
        self.home_patch = patch.object(paths.os.path, 'expanduser', return_value=str(self.home))
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.project_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_legacy_layout_theme_and_config_migrate_once_keep_sources_and_newer_preferences(self):
        legacy = self.home / '.qz-tool' / 'layout.json'
        legacy.write_text('{"log_max_lines":4567,"always_on_top":true}')
        theme = self.home / '.qz-tool' / 'theme.json'
        theme.write_text('{"dcc_title":{"max":"#123456"}}')
        config = self.project / 'config.json'
        config.write_text('{"bridge":{"ports":{"max":17099}}}')
        self.assertEqual(json.loads(Path(paths.user_state_path('layout.json')).read_text())['log_max_lines'], 4567)
        self.assertEqual(json.loads(Path(paths.user_state_path('theme.json')).read_text())['dcc_title']['max'], '#123456')
        self.assertEqual(json.loads(Path(paths.config_path()).read_text())['bridge']['ports']['max'], 17099)
        self.assertTrue(legacy.is_file() and theme.is_file() and config.is_file())
        target = Path(paths.user_state_path('layout.json'))
        target.write_text('{"log_max_lines":9999}')
        legacy.write_text('{"log_max_lines":1}')
        self.assertEqual(json.loads(Path(paths.user_state_path('layout.json')).read_text())['log_max_lines'], 9999)

    def test_defaults_are_merged_with_user_overrides_and_new_defaults_appear(self):
        (self.project / 'defaults.json').write_text('{"bridge":{"host":"127.0.0.1","ports":{"maya":17023,"max":17024}},"new":1}')
        settings = Path(paths.config_path())
        settings.write_text('{"bridge":{"ports":{"max":17100}}}')
        result = configuration.load()
        self.assertEqual(result['bridge']['ports'], {'maya': 17023, 'max': 17100})
        self.assertEqual(result['new'], 1)
        self.assertEqual(json.loads(settings.read_text()), {'bridge': {'ports': {'max': 17100}}})

    def test_test_override_never_migrates_actual_home_settings(self):
        (self.home / '.qz-tool' / 'layout.json').write_text('{"private":true}')
        with patch.dict(os.environ, QZ_DATA_DIR=str(self.root / 'isolated')):
            self.assertFalse(Path(paths.user_state_path('layout.json')).exists())
            self.assertEqual(json.loads(Path(paths.config_path()).read_text()), {})


if __name__ == '__main__':
    unittest.main()
