"""Real Qt buttons + temporary tools/preferences; no user Max instance."""
import importlib
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import threading
import types
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_user_scripts import Fixture
from ui import user_scripts as store
from max_actions import user_toolbar as toolbar


class ToolbarTests(Fixture):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        from PySide6 import QtWidgets
        self.main = QtWidgets.QMainWindow()
        self.existing = self.main.addToolBar('User toolbar')
        self.existing.addAction('Keep')
        self.config = self.folder / 'prefs' / 'toolbar.json'
        self.startup = self.folder / 'startup' / 'qz_user_tools.ms'
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(toolbar, '_main_window', return_value=self.main))
        self.stack.enter_context(patch.object(toolbar, '_locations', return_value=(str(self.config), str(self.startup))))
        self.report = self.stack.enter_context(patch.object(toolbar, '_report'))

    def tearDown(self):
        self.main.close()
        self.main.deleteLater()
        self.app.processEvents()
        self.stack.close()
        super().tearDown()

    def add_max(self, code='print("max")\n', entry=''):
        return store.add_code("中文 工具's", code, entry, dcc='max')

    def pin(self, tool):
        return toolbar.user_script_toolbar(root=str(store._root('max')), script_id=tool['id'])

    def bar(self):
        from PySide6 import QtWidgets
        return self.main.findChild(QtWidgets.QToolBar, toolbar._OBJECT)

    def test_pin_without_execution_repin_updates_and_preserves_other_toolbar(self):
        marker = self.folder / 'marker'
        tool = self.add_max('with open(%r,"w") as f: f.write("ran")\n' % str(marker))
        first = self.pin(tool)
        self.assertFalse(first['updated'])
        self.assertFalse(marker.exists())
        self.assertEqual(self.bar().actions()[0].text(), tool['name'])
        store.update_script(tool['id'], name='新的名称', dcc='max')
        second = self.pin(tool)
        self.assertTrue(second['updated'])
        self.assertEqual(len(self.bar().actions()), 1)
        self.assertEqual(self.bar().actions()[0].text(), '新的名称')
        self.assertEqual(self.existing.actions()[0].text(), 'Keep')
        self.assertTrue(self.startup.read_bytes().isascii())
        self.assertEqual(json.loads(self.config.read_text())['tools'][0]['id'], tool['id'])
        self.bar().actions()[0].trigger()
        self.assertEqual(marker.read_text(), 'ran')

    def test_click_reads_latest_entry_and_directory_after_rename_without_bridge(self):
        marker = self.folder / 'marker'
        code = 'def first():\n    with open(%r,"w") as f: f.write("first")\ndef second():\n    with open(%r,"w") as f: f.write("second")\n' % (str(marker), str(marker))
        tool = self.add_max(code, 'first')
        self.pin(tool)
        Path(tool['folder']).rename(store._root('max') / '重命名目录')
        store.update_script(tool['id'], entry='second', dcc='max')
        self.bar().actions()[0].trigger()
        self.assertEqual(marker.read_text(), 'second')
        self.report.assert_not_called()

    def test_restore_and_reload_keep_one_toolbar_and_one_callback(self):
        marker = self.folder / 'marker'
        tool = self.add_max('with open(%r,"a") as f: f.write("x")\n' % str(marker))
        self.pin(tool)
        bar = self.bar()
        importlib.reload(toolbar)
        with patch.object(toolbar, '_main_window', return_value=self.main):
            self.assertIs(toolbar.restore_toolbar(str(self.config)), bar)
            self.assertIs(toolbar.restore_toolbar(str(self.config)), bar)
            bar.actions()[0].trigger()
        self.assertEqual(marker.read_text(), 'x')

    def test_removed_tool_never_runs_stale_code_unpin_keeps_source(self):
        marker = self.folder / 'marker'
        tool = self.add_max('with open(%r,"w") as f: f.write("ran")\n' % str(marker))
        self.pin(tool)
        store.remove_script(tool['id'], dcc='max')
        toolbar.restore_toolbar(str(self.config))
        self.bar().actions()[0].trigger()
        self.assertFalse(marker.exists())
        self.report.assert_called_once()
        other = self.add_max()
        self.pin(other)
        toolbar._unpin(str(self.config), other['id'])
        self.assertTrue(Path(other['filepath']).is_file())
        self.assertEqual([a.data() for a in self.bar().actions()], [tool['id']])

    def test_real_button_right_click_unpins_and_stays_removed_after_restore(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        tool = self.add_max()
        self.pin(tool)
        bar = self.bar()
        self.main.show()
        self.app.processEvents()
        button = bar.widgetForAction(bar.actions()[0])
        chosen = []
        def choose():
            menu = self.app.activePopupWidget()
            if isinstance(menu, QtWidgets.QMenu):
                remove = menu.actions()[0]
                chosen.append(remove.text())
                QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(remove).center())
        QtCore.QTimer.singleShot(30, choose)
        QtTest.QTest.mouseClick(button, QtCore.Qt.RightButton)
        self.assertEqual(chosen, ['取消固定“%s”' % tool['name']])
        self.assertEqual(bar.actions(), [])
        self.assertEqual(toolbar._read_config(str(self.config)), [])
        self.assertTrue(Path(tool['filepath']).is_file())
        toolbar.restore_toolbar(str(self.config))
        self.assertEqual(bar.actions(), [])
        self.report.assert_not_called()

    def test_io_failure_rolls_back_new_button_and_existing_startup_and_registration(self):
        tool = self.add_max()
        self.pin(tool)
        previous = self.config.read_bytes(), self.startup.read_bytes()
        store.update_script(tool['id'], name='改名后', dcc='max')
        real_replace = toolbar._replace
        def fail_config(source, destination):
            if Path(destination) == self.config:
                raise PermissionError('locked config')
            real_replace(source, destination)
        with patch.object(toolbar, '_replace', side_effect=fail_config):
            with self.assertRaises(PermissionError):
                self.pin(tool)
        self.assertEqual((self.config.read_bytes(), self.startup.read_bytes()), previous)
        self.assertEqual(len(self.bar().actions()), 1)
        self.assertFalse(list(self.config.parent.glob('.qz-toolbar-*')))
        second = self.add_max()
        with patch.object(toolbar, '_persist', side_effect=PermissionError('locked startup')):
            with self.assertRaises(PermissionError):
                self.pin(second)
        self.assertEqual(len(self.bar().actions()), 1)

    def test_busy_foreign_startup_invalid_config_and_escaped_source_do_not_overwrite(self):
        tool = self.add_max()
        self.pin(tool)
        previous = self.config.read_bytes()
        self.bar()._qz_running = True
        with self.assertRaises(RuntimeError):
            self.pin(tool)
        self.bar()._qz_running = False
        self.assertEqual(self.config.read_bytes(), previous)
        self.startup.write_text('-- user file')
        with self.assertRaises(ValueError):
            self.pin(tool)
        self.assertEqual(self.startup.read_text(), '-- user file')
        self.config.write_text('{bad')
        with self.assertRaises(ValueError):
            self.pin(tool)
        self.assertEqual(self.config.read_text(), '{bad')
        manifest = Path(tool['folder']) / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['script'] = '../../outside.py'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            toolbar._read_tool(str(store._root('max')), tool['id'])


class DispatchTests(Fixture):
    def test_preflight_refreshes_pins_once_and_rejects_wrong_or_disconnected_host(self):
        tool = store.add_code('Max工具', 'print("ok")', dcc='max')
        calls = []
        class Client:
            dcc, connected, stale = 'max', True, True
            def call(self, action, **params):
                calls.append((action, params))
                if action == 'reload_actions':
                    self.stale = False
                    return dict(ok=True)
                if params.get('op') == 'info':
                    return dict(ok=True, data=dict(version=1 if self.stale else 2))
                return dict(ok=True, data=dict(toolbar='Qz 自定义工具'))
        client = Client()
        self.assertTrue(store.pin_script_to_max_toolbar(client, tool)['ok'])
        self.assertEqual([action for action, _ in calls], ['user_script_toolbar', 'reload_actions', 'user_script_toolbar', 'user_script_toolbar'])
        self.assertEqual(calls[-1][1]['root'], str(store._root('max')))
        calls.clear()
        client.connected = False
        self.assertFalse(store.pin_script_to_max_toolbar(client, tool)['ok'])
        self.assertEqual(calls, [])
        client.connected, client.dcc = True, 'maya'
        self.assertFalse(store.pin_script_to_max_toolbar(client, tool)['ok'])
        self.assertEqual(calls, [])
        client.dcc = 'max'
        def timeout(action, **params):
            calls.append(params['op'])
            if params['op'] == 'info':
                return dict(ok=True, data=dict(version=2))
            raise RuntimeError('timeout')
        client.call = timeout
        self.assertFalse(store.pin_script_to_max_toolbar(client, tool)['ok'])
        self.assertEqual(calls, ['info', 'pin'])

    def test_actual_max_context_menu_installs_asynchronously_and_logs(self):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        from ui.main_window import MainWindow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        tool = store.add_code('Max工具', 'print("ok")', dcc='max')
        with ExitStack() as stack:
            stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
            stack.enter_context(patch.object(MainWindow, '_restore_layout'))
            stack.enter_context(patch.object(MainWindow, '_restore_window', lambda w: w.resize(1200, 800)))
            stack.enter_context(patch('ui.layout_state._default_path', return_value=str(self.folder / 'layout.json')))
            stack.enter_context(patch.object(MainWindow, '_connection_notice'))
            workers = []
            def pin(client, script):
                self.assertEqual(client.dcc, 'max')
                workers.append(threading.current_thread())
                return dict(ok=True, data=dict(toolbar='Qz 自定义工具', name=script['name']))
            stack.enter_context(patch.object(store, 'pin_script_to_max_toolbar', side_effect=pin))
            window = MainWindow()
            try:
                window.show()
                window._side.show()
                app.processEvents()
                tree = window._side._tree
                group = tree._groups['max']
                custom = next(group.child(i) for i in range(group.childCount())
                              if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', 'max', 'user'))
                item = custom.child(0)
                tree.scrollToItem(item)
                app.processEvents()
                def choose():
                    menu = app.activePopupWidget()
                    if isinstance(menu, QtWidgets.QMenu):
                        self.assertNotIn('固定到 Maya 当前工具架', [a.text() for a in menu.actions()])
                        action = next(a for a in menu.actions() if a.text() == '固定到 Max 工具栏')
                        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(action).center())
                QtCore.QTimer.singleShot(30, choose)
                point = tree.visualItemRect(item).center()
                app.sendEvent(tree.viewport(), QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point)))
                for _ in range(100):
                    if not window._shelf_tasks:
                        break
                    QtTest.QTest.qWait(10)
                self.assertTrue(workers and workers[0] is not threading.current_thread())
                self.assertFalse(window._shelf_tasks)
                self.assertIn('Max 工具栏', window._log.toPlainText())
            finally:
                window.close()
                window.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
