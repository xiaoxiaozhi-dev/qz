"""Max management/storage/UI and dispatch, using disposable imported copies."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import stat
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_user_scripts import Fixture
from ui import user_scripts as store


class MaxStorageTests(Fixture):
    def add_max(self, **kwargs):
        return store.add_script('自定义 Max 工具', str(self.script), dcc='max', **kwargs)

    def test_two_hosts_same_name_stay_separate_and_maya_legacy_still_loads(self):
        maya = self.add()
        maximum = self.add_max()
        self.assertNotEqual(Path(maya['folder']).parent, Path(maximum['folder']).parent)
        all_tools, errors = store.scan_all_scripts()
        self.assertFalse(errors)
        self.assertEqual({tool.dcc for tool in all_tools}, {'maya', 'max'})
        with self.assertRaises(FileNotFoundError):
            store.read_script(maximum['id'])
        store.update_script(maximum['id'], name='改名', entry='main', dcc='max')
        self.assertEqual(store.read_script(maximum['id'], dcc='max')['name'], '改名')
        store.remove_script(maximum['id'], dcc='max')
        self.assertTrue(Path(maya['folder']).is_dir())
        self.assertTrue(self.script.is_file())
        self.assertEqual(store.scan_scripts('max'), ([], []))

    def test_py2_and_py3_source_can_both_be_added_without_middle_tier_execution(self):
        for text in ('print "Python2"\n', 'print(f"Python{3}")\n', 'def invalid(:'):
            self.script.write_text(text, encoding='utf-8')
            tool = self.add_max()
            self.assertEqual(Path(tool['filepath']).read_text(encoding='utf-8'), text)
        self.assertEqual(len(store.scan_scripts('max')[0]), 3)

    def test_folder_entry_and_resources_preserved_and_wrong_host_types_rejected(self):
        self.script.unlink()
        (self.source / 'main.ms').write_text('box()', encoding='utf-8')
        (self.source / 'texture.txt').write_text('resource', encoding='utf-8')
        tool = store.add_script('文件夹', str(self.source), dcc='max')
        self.assertEqual(tool['language'], 'maxscript')
        self.assertTrue((Path(tool['directory']) / 'texture.txt').is_file())
        with self.assertRaises(ValueError):
            store.add_script('错误类型', str(self.source / 'main.ms'), dcc='maya')

    def test_pasted_code_encodings_are_usable_by_max(self):
        from max_actions.user_scripts import user_script
        for source in ('print(u"中文")\n', '# coding: gbk\nprint(u"中文")\n', '#!/usr/bin/python\nprint(u"中文")\n'):
            tool = store.add_code('粘贴', source, dcc='max')
            content = Path(tool['filepath']).read_bytes()
            self.assertIn(b'coding', content)
            result = user_script(filepath=tool['filepath'], directory=tool['directory'], script_id=tool['id'])
            self.assertTrue(result['success'], result)
            self.assertIn('中文', result['output'])
        native = store.add_code('原生', 'print "中文"', language='maxscript', dcc='max')
        self.assertTrue(Path(native['filepath']).read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_missing_entry_and_deletion_failure_remain_manageable(self):
        tool = self.add_max()
        Path(tool['filepath']).unlink()
        self.assertTrue(store.scan_scripts('max')[0][0].user_script['missing'])
        with patch('ui.user_scripts.shutil.rmtree', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                store.remove_script(tool['id'], dcc='max')
        self.assertTrue((Path(tool['folder']) / 'manifest.json').is_file())
        store.remove_script(tool['id'], dcc='max')
        self.assertFalse(Path(tool['folder']).exists())

    def test_readonly_source_stays_readonly_and_imported_file_is_removable(self):
        source = self.source / 'readonly.ms'
        source.write_text('box()', encoding='utf-8')
        source.chmod(source.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        try:
            for imported in (source, self.source):
                tool = store.add_script('只读来源', str(imported), dcc='max',
                                        entry_file='readonly.ms' if imported.is_dir() else None)
                copy = Path(tool['directory']) / 'readonly.ms'
                self.assertTrue(copy.stat().st_mode & stat.S_IWUSR)
                self.assertFalse(source.stat().st_mode & stat.S_IWUSR)
                store.remove_script(tool['id'], dcc='max')
                self.assertFalse(Path(tool['folder']).exists())
                self.assertTrue(source.is_file())
        finally:
            source.chmod(source.stat().st_mode | stat.S_IWUSR)

    def test_legacy_readonly_payload_resources_and_manifest_can_be_removed(self):
        tool = self.add_max()
        folder = Path(tool['folder'])
        resource = Path(tool['directory']) / 'nested' / 'texture.txt'
        resource.parent.mkdir()
        resource.write_text('resource', encoding='utf-8')
        for path in (Path(tool['filepath']), resource, folder / 'manifest.json'):
            path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        store.remove_script(tool['id'], dcc='max')
        self.assertFalse(folder.exists())
        self.assertTrue(self.script.is_file())


class CoordinationTests(Fixture):
    def test_max_action_preflight_refreshes_before_exactly_one_run(self):
        tool = store.add_script('Max', str(self.script), dcc='max')
        calls = []
        class Client:
            connected, dcc, stale = True, 'max', True
            def call(self, action, **params):
                calls.append((action, params))
                if action == 'reload_actions':
                    self.stale = False
                    return dict(ok=True)
                if params.get('op') == 'info':
                    return dict(ok=False, error='unknown action') if self.stale else dict(ok=True, data=dict(version=1))
                return dict(ok=True, data=dict(success=True, output='done'))
        result = store.run_script(Client(), tool)
        self.assertTrue(result['ok'], result)
        self.assertEqual([call[0] for call in calls], ['user_script_max', 'reload_actions', 'user_script_max', 'user_script_max'])
        self.assertEqual(calls[-1][1]['filepath'], tool['filepath'])
        self.assertEqual(calls[-1][1]['execution_timeout'], 600)

    def test_timeout_not_retried_and_wrong_host_not_dispatched(self):
        tool = store.add_script('Max', str(self.script), dcc='max')
        calls = []
        def call(action, **params):
            calls.append((action, params))
            if params.get('op') == 'info':
                return dict(ok=True, data=dict(version=1))
            raise RuntimeError('timeout')
        client = types.SimpleNamespace(connected=True, dcc='max', call=call)
        result = store.run_script(client, tool)
        self.assertFalse(result['ok'])
        self.assertIn('3ds Max', result['error'])
        self.assertEqual(sum(params.get('op') == 'run' for _, params in calls), 1)
        calls.clear()
        client.dcc = 'maya'
        self.assertFalse(store.run_script(client, tool)['ok'])
        self.assertFalse(calls)


class MaxQtTests(Fixture):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)

    def test_dialog_file_and_folder_win_over_paste_and_languages_follow_entry(self):
        from PySide6 import QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        for folder in (False, True):
            dialog = AddScriptDialog(dcc='max')
            self.assertIn('3ds Max', dialog.windowTitle())
            self.assertTrue(dialog.python_radio.isChecked())
            native = self.source / 'main.ms'
            native.write_text('box()', encoding='utf-8')
            dialog.name_edit.setText('添加 Max')
            dialog.code_edit.setPlainText('def broken(')
            dialog.file_edit.setText(str(self.source if folder else native))
            if folder:
                dialog._source_changed()
                dialog.entry_file_combo.setCurrentIndex(dialog.entry_file_combo.findData('main.ms'))
            dialog._add()
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
            self.assertEqual(dialog.script['dcc'], 'max')
            self.assertEqual(dialog.script['language'], 'maxscript')
            self.assertTrue(dialog.native_radio.isChecked())
            dialog.close()

    def test_paste_py2_or_ms_does_not_execute_and_uses_correct_language(self):
        from PySide6 import QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        for native in (False, True):
            dialog = AddScriptDialog(dcc='max')
            dialog.name_edit.setText('粘贴 Max')
            if native:
                dialog.native_radio.setChecked(True)
            dialog.code_edit.setPlainText('box()' if native else 'print "Python2"')
            dialog._add()
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
            self.assertEqual(dialog.script['language'], 'maxscript' if native else 'python')
            dialog.close()


if __name__ == '__main__':
    unittest.main()
