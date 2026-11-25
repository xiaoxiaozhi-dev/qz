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
        import threading
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.main_window import MainWindow
        from ui import layout_state, user_scripts

        extra = self.folder / 'extra_tool.py'
        extra.write_text('print("manual")', encoding='utf-8')
        duplicate = self.folder / 'duplicates' / self.script.name
        duplicate.parent.mkdir()
        duplicate.write_text('raise AssertionError()', encoding='utf-8')
        with (patch.object(MainWindow, '_start_bridge_timer'),
              patch.object(MainWindow, '_restore_window', lambda win: win.resize(1200, 800)),
              patch('ui.layout_state._default_path', return_value=str(self.folder / 'layout.json'))):
            window = MainWindow()
            try:
                window.show()
                window._activity.show_panel(0)
                self.app.processEvents()
                tree = window._side._tree

                def custom_group():
                    group = tree._groups[dcc]
                    return next(group.child(i) for i in range(group.childCount())
                                if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', dcc, 'user'))

                with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames',
                                  return_value=([str(self.script), str(extra), str(duplicate)], '')) as picker:
                    item = custom_group().child(0)
                    tree.scrollToItem(item)
                    self.app.processEvents()
                    QtTest.QTest.mouseClick(tree.viewport(), QtCore.Qt.LeftButton,
                                            pos=tree.visualItemRect(item).center())
                    picker.assert_called_once()
                self.assertEqual(custom_group().text(0), '用户自定义 (2)')
                root = user_scripts._root(dcc)
                self.assertEqual({p.name for p in root.iterdir()}, {self.script.name, extra.name})
                self.assertEqual((root / self.script.name).read_bytes(), self.script.read_bytes())
                info = next(p for p in window._all_plugins if p.user_script and p.name == self.script.stem)
                self.assertNotIn(info.key, window._canvas.open_ids())

                manual = root / 'manual_tool.py'
                manual.write_text('pass', encoding='utf-8')
                QtTest.QTest.mouseClick(window._side._refresh_button, QtCore.Qt.LeftButton)
                self.assertEqual(custom_group().text(0), '用户自定义 (3)')
                tree._groups[dcc].setExpanded(False)
                window._side._search.setText(info.name)
                self.assertTrue(tree._groups[dcc].isExpanded())
                self.assertEqual(custom_group().childCount(), 1)
                target = custom_group().child(0)
                tree.scrollToItem(target)
                self.app.processEvents()

                window._clients[dcc].connected = True
                called = threading.Event()
                release = threading.Event()
                def execute(client, script):
                    called.set()
                    release.wait(2)
                    return dict(ok=True, data=dict(output='ran'))

                with patch('ui.main_window.user_scripts.run_script', side_effect=execute) as run:
                    try:
                        for _ in range(2):
                            QtTest.QTest.mouseDClick(tree.viewport(), QtCore.Qt.LeftButton,
                                                    pos=tree.visualItemRect(target).center())
                        self.assertTrue(called.wait(2))
                        run.assert_called_once()
                        self.assertEqual(run.call_args.args[1]['filepath'], str(root / self.script.name))
                        self.assertNotIn(info.key, window._canvas.open_ids())
                    finally:
                        release.set()
                    for _ in range(100):
                        if not window._script_tasks:
                            break
                        QtTest.QTest.qWait(10)
                    self.assertFalse(window._script_tasks)
                    layout_state.save_all({'version': 4, 'open_plugins': [
                        {'dcc': dcc, 'id': info.id, 'x': 12, 'y': 34}]})
                    window._restore_layout()
                    self.assertNotIn(info.key, window._canvas.open_ids())
                    run.assert_called_once()

                window._side._search.clear()
                self.assertFalse(tree._groups[dcc].isExpanded())
                renamed = user_scripts.update_script(info.id, name='新的名称', dcc=dcc)
                self.assertTrue((root / ('新的名称' + self.script.suffix)).is_file())
                user_scripts.remove_script(renamed['id'], dcc=dcc)
                window._side._refresh_button.click()
                self.assertEqual(custom_group().text(0), '用户自定义 (2)')
                self.assertTrue(self.script.is_file())
            finally:
                window.close()

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
