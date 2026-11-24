"""Managed script imports, host execution, and actual Qt user paths; no user scenes."""
import json
import os
import shutil
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ui import user_scripts as store
from maya_actions.user_scripts import user_script, _Output


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / "工具's"
        self.source.mkdir()
        self.script = self.source / 'main.py'
        self.script.write_text('print("hello")\n', encoding='utf-8')
        self.root = self.folder / 'managed'
        self.path_patch = patch('paths.user_plugins_dir', side_effect=lambda dcc='maya':
                                str(self.root if dcc == 'maya' else self.folder / f'{dcc}-managed'))
        self.path_patch.start()
        self.saved_path = list(sys.path)

    def tearDown(self):
        sys.path[:] = self.saved_path
        self.path_patch.stop()
        self.temp.cleanup()

    def add(self, **kwargs):
        return store.add_script('我的脚本', str(self.script), **kwargs)

    def run_script(self, data):
        return user_script(filepath=data['filepath'], entry=data['entry'], script_id=data['id'])


class StorageTests(Fixture):
    def test_plugin_named_folders_resolve_by_manifest_id_after_directory_rename(self):
        data = self.add()
        self.assertEqual(Path(data['folder']).name, '我的脚本')
        renamed = self.root / 'renamed_tool'
        Path(data['folder']).rename(renamed)
        loaded = store.read_script(data['id'])
        self.assertEqual(Path(loaded['folder']), renamed)
        changed = store.update_script(data['id'], name='新的显示名称', entry='main')
        self.assertEqual(changed['id'], data['id'])
        self.assertEqual(store.scan_scripts()[0][0].id, data['id'])
        self.assertEqual(Path(changed['folder']), renamed)

    def test_same_plugin_name_uses_numbered_folder_without_overwriting_existing_files(self):
        first = self.add()
        self.script.write_text('print("second")\n', encoding='utf-8')
        second = self.add()
        self.assertEqual(Path(first['folder']).name, '我的脚本')
        self.assertEqual(Path(second['folder']).name, '我的脚本_2')
        self.assertEqual(Path(first['filepath']).read_text(encoding='utf-8'), 'print("hello")\n')
        self.assertEqual(Path(second['filepath']).read_text(encoding='utf-8'), 'print("second")\n')
        self.assertNotEqual(first['id'], second['id'])

    def test_plugin_name_is_used_independently_of_script_filename_and_entry(self):
        source = self.source / '创建圆锥.py'
        source.write_text('def create_cone(): pass\n', encoding='utf-8')
        data = store.add_script('创建圆锥', str(source), entry='create_cone')
        self.assertEqual(Path(data['folder']).name, '创建圆锥')
        self.assertEqual(Path(data['filepath']).name, source.name)
        fallback = store.add_script('中文脚本', str(source))
        self.assertEqual(Path(fallback['folder']).name, '中文脚本')
        safe = store.add_script('con', str(source), entry='create_cone')
        self.assertEqual(Path(safe['folder']).name, 'tool_con')

    def test_plugin_name_cannot_escape_storage_or_create_hidden_windows_folder(self):
        for name in ('../a/b', 'C:\\outside', '..', '.hidden.', 'CON.txt', 'tool?'):
            data = store.add_script(name, str(self.script))
            folder = Path(data['folder'])
            self.assertEqual(folder.parent, self.root)
            self.assertFalse(folder.name.startswith('.'))
            self.assertFalse(any(char in folder.name for char in '<>:"/\\|?*'))

    def test_directory_source_automatically_copies_helpers_and_resources(self):
        (self.source / 'helper.py').write_text('VALUE=1\n', encoding='utf-8')
        (self.source / 'resource.txt').write_text('resource', encoding='utf-8')
        data = store.add_script('整个工具', str(self.source))
        self.assertEqual(Path(data['filepath']).name, 'main.py')
        self.assertTrue((Path(data['directory']) / 'helper.py').is_file())
        self.assertTrue((Path(data['directory']) / 'resource.txt').is_file())

    def test_blender_imports_and_discovers_only_python_entry_files(self):
        for suffix, language in (('.mel', 'mel'), ('.ms', 'maxscript')):
            native = self.source / ('main' + suffix)
            native.write_text('print("unsupported")', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Python'):
                store.add_script('不支持的类型', str(native), dcc='blender')
            with self.assertRaisesRegex(ValueError, '脚本语言'):
                store.add_code('不支持的类型', 'pass', language=language, dcc='blender')
        self.assertEqual(store.script_files(self.source, 'blender'), [Path('main.py')])
        tool = store.add_script('目录工具', str(self.source), dcc='blender')
        self.assertEqual(tool['dcc'], 'blender')
        self.assertEqual(tool['script'], 'main.py')
        self.assertEqual([info.key for info in store.scan_all_scripts()[0]], [('blender', tool['id'])])

    def test_ambiguous_folder_requires_entry_and_rejects_outside_paths(self):
        self.script.rename(self.source / 'first.py')
        (self.source / 'second.mel').write_text('print("second");', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '入口脚本'):
            store.add_script('多文件工具', str(self.source))
        with self.assertRaises(ValueError):
            store.add_script('越界', str(self.source), entry_file='../outside.py')
        self.assertEqual(store.scan_scripts(), ([], []))
        data = store.add_script('多文件工具', str(self.source), entry_file='second.mel')
        self.assertEqual(data['language'], 'mel')
        self.assertTrue((Path(data['directory']) / 'first.py').exists())

    def test_pasted_code_saves_utf8_without_running_and_bad_code_is_not_published(self):
        marker = self.folder / 'pasted-ran.txt'
        code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n'
        data = store.add_code('粘贴工具', code)
        self.assertFalse(marker.exists())
        self.assertEqual(Path(data['filepath']).read_text(encoding='utf-8'), code)
        self.assertEqual(data['script'], 'main.py')
        for invalid in ('', '   ', 'def broken('):
            with self.assertRaises((ValueError, SyntaxError)):
                store.add_code('不应发布', invalid)
        self.assertEqual(len(store.scan_scripts()[0]), 1)
        mel = store.add_code('MEL粘贴', 'global proc runTool() {}', 'runTool', 'mel')
        self.assertEqual(Path(mel['filepath']).suffix, '.mel')

    def test_entry_suggestion_avoids_duplicate_top_level_execution_and_required_arguments(self):
        self.assertEqual(store.suggest_entry('import maya.cmds as cmds\ndef create_cone():\n    cmds.polyCone()\n'), 'create_cone')
        self.assertEqual(store.suggest_entry('def main(): pass\ndef helper(): pass\n'), 'main')
        self.assertEqual(store.suggest_entry('def run(value): pass\n'), '')
        self.assertEqual(store.suggest_entry('def run(*, value): pass\n'), '')
        self.assertEqual(store.suggest_entry('def run(): pass\nrun()\n'), '')
        self.assertEqual(store.suggest_entry('def run('), '')

    def test_old_id_folder_remains_readable_and_manageable(self):
        data = self.add()
        legacy = self.root / data['id']
        Path(data['folder']).rename(legacy)
        self.assertEqual(Path(store.read_script(data['id'])['folder']), legacy)
        self.assertEqual(store.scan_scripts()[0][0].id, data['id'])
        store.remove_script(data['id'])
        self.assertEqual(store.scan_scripts(), ([], []))
        self.assertFalse(legacy.exists())

    def test_duplicate_manifest_ids_are_reported_and_cannot_execute(self):
        data = self.add()
        shutil.copytree(data['folder'], self.root / 'duplicate')
        plugins, errors = store.scan_scripts()
        self.assertEqual(plugins, [])
        self.assertEqual(len(errors), 1)
        with self.assertRaisesRegex(ValueError, '编号重复'):
            store.read_script(data['id'])

    def test_import_is_a_copy_and_does_not_execute(self):
        marker = self.folder / 'executed.txt'
        self.script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n', encoding='utf-8')
        data = self.add()
        self.assertFalse(marker.exists())
        self.assertNotEqual(data['filepath'], str(self.script))
        self.script.write_text('changed', encoding='utf-8')
        self.assertIn('executed.txt', Path(data['filepath']).read_text(encoding='utf-8'))
        plugins, errors = store.scan_scripts()
        self.assertEqual((len(plugins), errors), (1, []))
        self.assertEqual(plugins[0].category, 'user')
        self.assertFalse(marker.exists())

    def test_folder_import_preserves_helpers_and_resources(self):
        (self.source / 'helper.py').write_text('VALUE = 42\n', encoding='utf-8')
        (self.source / 'icon.svg').write_text('<svg/>', encoding='utf-8')
        (self.source / '__pycache__').mkdir()
        (self.source / '__pycache__/stale.pyc').write_bytes(b'old')
        data = self.add(tool_directory=str(self.source))
        self.assertEqual((Path(data['directory']) / 'icon.svg').read_text(), '<svg/>')
        self.assertTrue((Path(data['directory']) / 'helper.py').is_file())
        self.assertFalse((Path(data['directory']) / '__pycache__').exists())

    def test_bad_import_never_publishes_partial_tool(self):
        self.script.write_text('def broken(', encoding='utf-8')
        with self.assertRaises(SyntaxError):
            self.add()
        self.assertEqual(store.scan_scripts(), ([], []))
        self.script.write_text('pass\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.add(tool_directory=str(self.folder / 'elsewhere'))

    def test_name_and_entry_validation(self):
        for name in ('', '   ', 'x\ny', 'x' * 81):
            with self.assertRaises(ValueError):
                store.add_script(name, str(self.script))
        for entry in ('main()', 'main;print(1)', 'class', 'x[0]'):
            with self.assertRaises(ValueError):
                self.add(entry=entry)
        self.assertEqual(store.validate_entry(' tools.run ', 'python'), 'tools.run')

    def test_same_display_names_have_distinct_persistent_ids(self):
        first, second = self.add(), self.add()
        self.assertNotEqual(first['id'], second['id'])
        changed = store.update_script(first['id'], name='重命名', entry='main')
        self.assertEqual(changed['id'], first['id'])
        self.assertEqual(store.read_script(first['id'])['name'], '重命名')
        self.assertEqual(len(store.scan_scripts()[0]), 2)

    def test_remove_deletes_managed_resources_and_registration_but_preserves_original_and_other_tools(self):
        data = self.add(tool_directory=str(self.source))
        other = self.add()
        cache = Path(data['directory']) / '__pycache__'
        cache.mkdir()
        (cache / 'generated.pyc').write_bytes(b'cache')
        store.remove_script(data['id'])
        self.assertFalse(Path(data['folder']).exists())
        self.assertTrue(self.script.is_file())
        self.assertTrue(Path(other['filepath']).is_file())
        self.assertEqual([info.id for info in store.scan_scripts()[0]], [other['id']])
        with self.assertRaises(FileNotFoundError):
            store.read_script(data['id'])
        replacement = self.add()
        self.assertEqual(Path(replacement['folder']).name, Path(data['folder']).name)

    def test_remove_failure_keeps_registration_for_retry(self):
        data = self.add()
        with patch('ui.user_scripts.shutil.rmtree', side_effect=PermissionError('file in use')):
            with self.assertRaises(PermissionError):
                store.remove_script(data['id'])
        self.assertTrue((Path(data['folder']) / 'manifest.json').exists())
        self.assertEqual(store.scan_scripts()[0][0].id, data['id'])
        self.assertTrue(Path(data['filepath']).is_file())

    def test_remove_rejects_root_or_external_directory(self):
        data = self.add()
        for target in (self.root, self.source):
            with patch('ui.user_scripts._folder', return_value=target):
                with self.assertRaises(ValueError):
                    store.remove_script(data['id'])
        self.assertTrue(self.script.is_file())
        self.assertTrue(Path(data['filepath']).is_file())

    def test_missing_script_is_visible_and_can_be_removed(self):
        data = self.add()
        Path(data['filepath']).unlink()
        plugins, errors = store.scan_scripts()
        self.assertEqual(len(plugins), 1)
        self.assertTrue(plugins[0].user_script['missing'])
        self.assertEqual(errors, [])
        store.remove_script(data['id'])
        self.assertEqual(store.scan_scripts(), ([], []))
        self.assertFalse(Path(data['folder']).exists())

    def test_malformed_registration_is_reported_without_hiding_other_tools(self):
        first, second = self.add(), self.add()
        (Path(first['folder']) / 'manifest.json').write_text('{bad', encoding='utf-8')
        plugins, errors = store.scan_scripts()
        self.assertEqual([info.id for info in plugins], [second['id']])
        self.assertEqual(len(errors), 1)

    def test_manifest_path_cannot_escape_its_tool(self):
        data = self.add()
        path = Path(data['folder']) / 'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['script'] = str(self.script)
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            store.read_script(data['id'])
        with self.assertRaises(ValueError):
            store.update_script('../elsewhere', name='bad')


class ExecutionTests(Fixture):
    def test_main_guard_stdout_and_window_callbacks_survive_return(self):
        self.script.write_text('value=41\ndef callback(): return value+1\n'
                               'if __name__ == "__main__": print(callback())\n', encoding='utf-8')
        data = self.add()
        original_main = sys.modules['__main__']
        result = self.run_script(data)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['output'], '42\n')
        self.assertIs(sys.modules['__main__'], original_main)
        self.assertEqual(sys.modules['_qz_' + data['id']].callback(), 42)

    def test_function_entry_skips_main_guard_and_reads_fresh_source(self):
        self.script.write_text('def main(): print("entry")\n'
            'if __name__ == "__main__": raise RuntimeError("guard")\n', encoding='utf-8')
        data = self.add(entry='main')
        self.assertEqual(self.run_script(data)['output'], 'entry\n')
        Path(data['filepath']).write_text('def main(): print("edited")\n', encoding='utf-8')
        self.assertEqual(self.run_script(data)['output'], 'edited\n')

    def test_folder_local_import_and_file_relative_resource(self):
        (self.source / 'qz_unique_helper.py').write_text('VALUE=17\n', encoding='utf-8')
        (self.source / 'resource.txt').write_text('resource', encoding='utf-8')
        self.script.write_text('from pathlib import Path\nimport qz_unique_helper\n'
            'print(qz_unique_helper.VALUE, Path(__file__).with_name("resource.txt").read_text())\n', encoding='utf-8')
        result = self.run_script(self.add(tool_directory=str(self.source)))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['output'], '17 resource\n')
        sys.modules.pop('qz_unique_helper', None)

    def test_traceback_includes_managed_filename_and_line(self):
        self.script.write_text('print("before")\nraise RuntimeError("broken tool")\n', encoding='utf-8')
        data = self.add()
        result = self.run_script(data)
        self.assertFalse(result['success'])
        self.assertEqual(result['output'], 'before\n')
        self.assertIn(data['filepath'], result['error'])
        self.assertIn('line 2', result['error'])
        self.assertIn('broken tool', result['error'])

    def test_sys_exit_cannot_terminate_host_and_main_namespace_is_restored(self):
        self.script.write_text('import sys\nsys.exit(9)\n', encoding='utf-8')
        main = sys.modules['__main__']
        result = self.run_script(self.add())
        self.assertFalse(result['success'])
        self.assertIn('SystemExit', result['error'])
        self.assertIs(sys.modules['__main__'], main)

    def test_output_is_bounded_and_only_marks_actual_truncation(self):
        output = _Output()
        output.write('x' * 20000)
        output.write('y' * 10000)
        self.assertFalse(output.truncated)
        output.write('z' * 10000)
        self.assertTrue(output.truncated)
        self.assertEqual(len(output.getvalue()), 32768)

    def test_mel_sources_the_copied_file_and_calls_validated_procedure(self):
        source = self.source / "tool 'quote'.mel"
        source.write_text('global proc runTool() {}', encoding='utf-8')
        data = store.add_script('MEL', str(source), 'runTool')
        commands = []
        with patch.dict(sys.modules, {'maya': types.ModuleType('maya'),
                                     'maya.mel': types.SimpleNamespace(eval=commands.append)}), patch.dict(os.environ):
            result = self.run_script(data)
            self.assertIn(str(Path(data['filepath']).parent), os.environ['MAYA_SCRIPT_PATH'].split(os.pathsep))
        self.assertTrue(result['success'], result)
        self.assertEqual(commands, ['rehash;', 'source "' + data['filepath'].replace('\\', '/') + '";', 'runTool();'])


class Client:
    connected = True
    def __init__(self, stale=False, failure=False):
        self.calls, self.stale, self.failure = [], stale, failure
    def call(self, action, **params):
        self.calls.append((action, params))
        if action == 'reload_actions':
            self.stale = False
            return dict(ok=True, data=[])
        if params.get('op') == 'info':
            return dict(ok=False, error='unknown action: user_script') if self.stale else dict(ok=True, data=dict(version=1))
        if self.failure:
            raise RuntimeError('connection timed out')
        time.sleep(.02)
        return dict(ok=True, data=dict(success=True, output='script output\n', elapsed_seconds=.02))


class CoordinationTests(Fixture):
    def test_old_actions_are_refreshed_before_exactly_one_run(self):
        client = Client(stale=True)
        result = store.run_script(client, self.add())
        self.assertTrue(result['ok'], result)
        self.assertEqual([action for action, _ in client.calls], ['user_script', 'reload_actions', 'user_script', 'user_script'])
        self.assertEqual(sum(params.get('op') == 'run' for _, params in client.calls), 1)

    def test_timeout_is_never_retried(self):
        client = Client(failure=True)
        result = store.run_script(client, self.add())
        self.assertFalse(result['ok'])
        self.assertEqual(sum(params.get('op') == 'run' for _, params in client.calls), 1)

    def test_disconnected_and_removed_tools_never_execute(self):
        client = Client()
        data = self.add()
        client.connected = False
        self.assertFalse(store.run_script(client, data)['ok'])
        self.assertFalse(client.calls)
        client.connected = True
        store.remove_script(data['id'])
        self.assertFalse(store.run_script(client, data)['ok'])
        self.assertFalse(any(params.get('op') == 'run' for _, params in client.calls))


class QtTests(Fixture):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)

    def test_dialog_actual_file_selection_add_and_inline_validation(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        dialog = AddScriptDialog()
        dialog.show()
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(self.script), '')):
            QtTest.QTest.mouseClick(dialog.browse_button, QtCore.Qt.LeftButton)
        self.assertEqual(dialog.name_edit.text(), 'main')
        dialog.name_edit.clear()
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertTrue(dialog.error.text())
        self.assertIsNone(dialog.script)
        dialog.name_edit.setText('添加测试')
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(len(store.scan_scripts()[0]), 1)
        dialog.close()

    def test_dialog_folder_selection_requires_entry_when_ambiguous(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        self.script.rename(self.source / 'first.py')
        (self.source / 'second.mel').write_text('print("second");', encoding='utf-8')
        dialog = AddScriptDialog()
        dialog.show()
        with patch.object(QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(self.source)):
            QtTest.QTest.mouseClick(dialog.folder_button, QtCore.Qt.LeftButton)
        self.assertTrue(dialog.entry_file_combo.isVisible())
        self.assertIsNone(dialog.entry_file_combo.currentData())
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertIn('入口脚本', dialog.error.text())
        self.assertIsNone(dialog.script)
        dialog.entry_file_combo.setCurrentIndex(dialog.entry_file_combo.findData('second.mel'))
        self.assertTrue(dialog.mel_radio.isChecked())
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dialog.script['language'], 'mel')
        self.assertTrue((Path(dialog.script['directory']) / 'first.py').is_file())
        dialog.close()

    def test_dialog_pasted_cone_code_creates_chinese_tool_and_executes_entry_only_on_run(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        dialog = AddScriptDialog()
        dialog.show()
        dialog.name_edit.setText('创建圆锥')
        self.assertTrue(dialog.file_edit.isVisible())
        self.assertTrue(dialog.code_edit.isVisible())
        self.assertTrue(dialog.python_radio.isChecked())
        code = 'import maya.cmds as cmds\n\ndef create_cone():\n    cmds.polyCone()\n'
        calls = []
        maya = types.ModuleType('maya')
        maya.__path__ = []
        cmds = types.ModuleType('maya.cmds')
        cmds.polyCone = lambda: calls.append('cone')
        maya.cmds = cmds
        with patch.dict(sys.modules, {'maya': maya, 'maya.cmds': cmds}):
            dialog.code_edit.setPlainText(code)
            self.assertEqual(dialog.entry_edit.text(), 'create_cone')
            QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
            self.assertEqual(calls, [])
            data = dialog.script
            self.assertEqual(Path(data['folder']).name, '创建圆锥')
            self.assertEqual(Path(data['filepath']).read_text(encoding='utf-8'), code)
            self.assertTrue(self.run_script(data)['success'])
            self.assertEqual(calls, ['cone'])
        dialog.close()

    def test_dialog_paste_validation_and_manual_entry_survive_edits(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        dialog = AddScriptDialog()
        dialog.show()
        dialog.name_edit.setText('粘贴检查')
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertIn('粘贴', dialog.error.text())
        dialog.code_edit.setPlainText('def broken(')
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertIsNone(dialog.script)
        self.assertTrue(dialog.error.text())
        dialog.code_edit.setPlainText('def main(): pass\n')
        dialog.entry_edit.setText('custom')
        dialog.code_edit.setPlainText('def main(): pass\ndef custom(): pass\n')
        self.assertEqual(dialog.entry_edit.text(), 'custom')
        QtTest.QTest.mouseClick(dialog.mel_radio, QtCore.Qt.LeftButton)
        self.assertFalse(dialog.python_radio.isChecked())
        dialog.code_edit.setPlainText('global proc custom() {}')
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dialog.script['language'], 'mel')
        dialog.close()

    def test_dialog_file_and_folder_take_priority_over_pasted_code(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        (self.source / 'resource.txt').write_text('resource', encoding='utf-8')
        for source in (self.script, self.source):
            dialog = AddScriptDialog()
            dialog.show()
            dialog.name_edit.setText('来源检查')
            dialog.code_edit.setPlainText('def pasted_function(): pass\n')
            self.assertEqual(dialog.entry_edit.text(), 'pasted_function')
            dialog.file_edit.setText(str(source))
            self.assertEqual(dialog.entry_edit.text(), '')
            dialog.code_edit.setPlainText('def invalid pasted code(')
            QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
            self.assertEqual(Path(dialog.script['filepath']).read_text(encoding='utf-8'),
                             self.script.read_text(encoding='utf-8'))
            self.assertEqual((Path(dialog.script['directory']) / 'resource.txt').exists(), source.is_dir())
            self.assertTrue(dialog.code_edit.toPlainText())
            dialog.close()

    def test_dialog_invalid_source_is_reported_without_falling_back_to_pasted_code(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.user_script_dialog import AddScriptDialog
        dialog = AddScriptDialog()
        dialog.show()
        dialog.name_edit.setText('无效来源')
        dialog.file_edit.setText(str(self.folder / 'missing.py'))
        dialog.code_edit.setPlainText('print("pasted")\n')
        QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        self.assertIsNone(dialog.script)
        self.assertTrue(dialog.error.text())
        self.assertEqual(store.scan_scripts(), ([], []))
        dialog.close()

    def test_card_real_click_runs_in_background_and_logs_on_ui_thread(self):
        from PySide6 import QtCore, QtTest
        data = self.add()
        info = store.scan_scripts()[0][0]
        logs = []
        log = types.SimpleNamespace(**{level: lambda value: logs.append((threading.current_thread(), value))
                                      for level in ('info', 'warn', 'error')})
        client = Client()
        widget = info.load_factory()(client, log)
        widget.show()
        QtTest.QTest.mouseClick(widget.run_button, QtCore.Qt.LeftButton)
        self.assertFalse(widget.run_button.isEnabled())
        deadline = time.monotonic() + 3
        while widget._task is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertIsNone(widget._task)
        self.assertTrue(widget.run_button.isEnabled())
        self.assertTrue(all(thread is threading.current_thread() for thread, _ in logs))
        self.assertTrue(any('script output' in value for _, value in logs))
        self.assertFalse(widget.run_button.toolTip())
        widget.close()

    def test_script_owned_window_callback_still_works_after_execution_returns(self):
        from PySide6 import QtCore, QtTest
        self.script.write_text('from PySide6 import QtWidgets\nvalue = 0\n'
            'def clicked():\n    global value\n    value += 1\n'
            'window = QtWidgets.QPushButton("Callback")\nwindow.clicked.connect(clicked)\nwindow.show()\n', encoding='utf-8')
        data = self.add()
        result = self.run_script(data)
        self.assertTrue(result['success'], result)
        module = sys.modules['_qz_' + data['id']]
        QtTest.QTest.mouseClick(module.window, QtCore.Qt.LeftButton)
        self.assertEqual(module.value, 1)
        module.window.close()

    def test_main_window_add_rename_search_restart_and_remove_actual_ui_flow(self):
        self._main_window_flow('maya')

    def test_max_main_window_add_rename_search_restart_and_remove_actual_ui_flow(self):
        self._main_window_flow('max')

    def test_blender_main_window_add_rename_search_restart_and_remove_actual_ui_flow(self):
        self._main_window_flow('blender')

    def _main_window_flow(self, dcc):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        from ui.main_window import MainWindow
        from ui import layout_state
        from ui.user_script_dialog import AddScriptDialog
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
            stack.enter_context(patch.object(MainWindow, '_restore_layout'))
            stack.enter_context(patch.object(MainWindow, '_restore_window', lambda win: win.resize(1200, 800)))
            stack.enter_context(patch('ui.main_window.layout_state.save_open'))
            stack.enter_context(patch('ui.layout_state._default_path', return_value=str(self.folder / 'layout.json')))
            window = MainWindow()
            try:
                window.show()
                window._side.show()
                self.app.processEvents()
                tree = window._side._tree

                def custom_group():
                    group = tree._groups[dcc]
                    return next(group.child(i) for i in range(group.childCount())
                                if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', dcc, 'user'))

                completed = []
                def fill_dialog():
                    dialog = self.app.activeModalWidget()
                    if not isinstance(dialog, AddScriptDialog):
                        completed.append('dialog missing')
                        return
                    dialog.name_edit.setText('新工具')
                    dialog.file_edit.setText(str(self.script))
                    QtTest.QTest.mouseClick(dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
                    completed.append(dialog.result())
                custom = custom_group()
                self.assertEqual(custom.text(0), '用户自定义 (0)')
                custom.setExpanded(True)
                tree.scrollToItem(custom.child(0))
                self.app.processEvents()
                QtCore.QTimer.singleShot(30, fill_dialog)
                QtCore.QTimer.singleShot(3000, lambda: self.app.activeModalWidget().reject()
                                         if isinstance(self.app.activeModalWidget(), AddScriptDialog) else None)
                QtTest.QTest.mouseClick(tree.viewport(), QtCore.Qt.LeftButton,
                                        pos=tree.visualItemRect(custom.child(0)).center())
                self.assertEqual(completed, [QtWidgets.QDialog.Accepted])
                self.assertEqual(custom_group().text(0), '用户自定义 (1)')
                info = next(info for info in window._all_plugins if info.user_script)
                self.assertIn(info.key, window._canvas.open_ids())
                proxy = window._canvas._proxies[info.key]
                position = proxy.pos()
                self.assertEqual(proxy.node().title_text, '新工具')
                self.assertEqual(window._clients[dcc], proxy.node().content().client)
                QtTest.QTest.keyClick(window._canvas, QtCore.Qt.Key_F, QtCore.Qt.ControlModifier)
                self.app.processEvents()
                self.assertIn(info.key, window._canvas._search._by_key)
                window._canvas._search.hide()

                restarted = MainWindow()
                self.assertIn(info.key, restarted._plugins_by_key)
                restarted.close()
                restarted.deleteLater()

                chosen = []
                def context_action(label):
                    item = custom_group().child(0)
                    tree.scrollToItem(item)
                    self.app.processEvents()
                    point = tree.visualItemRect(item).center()
                    def select():
                        menu = self.app.activePopupWidget()
                        if not isinstance(menu, QtWidgets.QMenu):
                            chosen.append('menu missing')
                            return
                        action = next(action for action in menu.actions() if action.text() == label)
                        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(action).center())
                        chosen.append(label)
                    QtCore.QTimer.singleShot(20, select)
                    QtCore.QTimer.singleShot(3000, lambda: self.app.activePopupWidget().close()
                                             if isinstance(self.app.activePopupWidget(), QtWidgets.QMenu) else None)
                    event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point,
                                                   tree.viewport().mapToGlobal(point))
                    self.app.sendEvent(tree.viewport(), event)

                with patch.object(QtWidgets.QInputDialog, 'getText', return_value=('新的名称', True)):
                    context_action('改名…')
                self.assertEqual(proxy.node().title_text, '新的名称')
                self.assertEqual(proxy.pos(), position)
                self.assertIs(proxy, window._canvas._proxies[info.key])
                self.assertEqual(window._canvas._search._by_key[info.key].name, '新的名称')
                with patch.object(QtWidgets.QInputDialog, 'getText', return_value=('main', True)):
                    context_action('修改入口…')
                self.assertEqual(proxy.node().content().script['entry'], 'main')
                with patch.object(QtGui.QDesktopServices, 'openUrl', return_value=True) as opened:
                    context_action('打开脚本目录')
                    self.assertEqual(opened.call_count, 1)
                layout_state.save_all({'version': 4, 'open_plugins': [
                    {'dcc': dcc, 'id': info.id, 'x': 12, 'y': 34},
                    {'dcc': 'max', 'id': 'create_box', 'x': 56, 'y': 78}]})
                answers = []
                def remove_with_confirmation(label):
                    def answer():
                        box = self.app.activeModalWidget()
                        if not isinstance(box, QtWidgets.QMessageBox):
                            answers.append('confirmation missing')
                            return
                        answers.append((label, box.defaultButton().text(), box.informativeText()))
                        button = next(button for button in box.buttons() if button.text() == label)
                        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                    timer = QtCore.QTimer(window)
                    timer.setSingleShot(True)
                    timer.timeout.connect(answer)
                    timer.start(80)
                    try:
                        context_action('移除')
                    finally:
                        timer.stop()
                    self.app.processEvents()

                remove_with_confirmation('取消')
                self.assertIn(info.key, window._canvas.open_ids())
                self.assertTrue(Path(info.user_script['folder']).is_dir())
                self.assertIn(info.key, window._plugins_by_key)
                self.assertIn(info.key, [key for key, _, _ in layout_state.load_open()[0]])
                with patch('ui.main_window.user_scripts.remove_script', side_effect=PermissionError('file in use')):
                    with patch.object(window._log, 'error') as report:
                        remove_with_confirmation('移除')
                        report.assert_called_once()
                self.assertIn(info.key, window._canvas.open_ids())
                self.assertTrue(Path(info.user_script['folder']).is_dir())
                remove_with_confirmation('移除')
                self.assertEqual(chosen, ['改名…', '修改入口…', '打开脚本目录', '移除', '移除', '移除'])
                self.assertEqual([answer[:2] for answer in answers], [('取消', '取消'), ('移除', '取消'), ('移除', '取消')])
                self.assertTrue(all(info.user_script['folder'] in answer[2] for answer in answers))
                self.assertNotIn(info.key, window._canvas.open_ids())
                self.assertNotIn(info.key, window._plugins_by_key)
                self.assertNotIn(info.key, window._canvas._search._by_key)
                self.assertFalse(Path(info.user_script['folder']).exists())
                self.assertTrue(self.script.is_file())
                self.assertEqual([key for key, _, _ in layout_state.load_open()[0]], [('max', 'create_box')])
                self.assertNotIn(info.key, [plugin.key for plugin in store.scan_scripts(dcc)[0]])
                self.assertEqual(custom_group().text(0), '用户自定义 (0)')
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_empty_custom_groups_have_add_entry_and_emit_their_dcc(self):
        from PySide6 import QtCore, QtTest
        from ui.plugin_tree import PluginTree
        tree = PluginTree()
        tree.set_plugins([])
        tree.resize(400, 400)
        tree.show()
        called = []
        tree.add_script_requested.connect(called.append)
        for dcc in ('maya', 'max', 'blender'):
            group = tree._groups[dcc]
            group.setExpanded(True)
            custom = next(group.child(i) for i in range(group.childCount())
                          if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', dcc, 'user'))
            custom.setExpanded(True)
            self.app.processEvents()
            QtTest.QTest.mouseClick(tree.viewport(), QtCore.Qt.LeftButton,
                                   pos=tree.visualItemRect(custom.child(0)).center())
        self.assertEqual(called, ['maya', 'max', 'blender'])
        tree.close()


if __name__ == '__main__':
    unittest.main()
