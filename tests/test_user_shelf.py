"""Shelf installation and launcher tests with temporary tools and preferences."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_user_scripts import Fixture
from maya_actions import user_shelf
from ui import user_scripts as store


class ShelfCommands:
    def __init__(self, folder):
        self.folder = folder
        self.current = 'CurrentShelf'
        self.shelves = {'CurrentShelf': ['existing'], 'OtherShelf': []}
        self.buttons = {'existing': dict(docTag='user-owned', label='Do not change')}
        self.warnings = []
        self.fail_save = False

    def shelfTabLayout(self, top, **kwargs):
        return True if kwargs.get('exists') else self.current

    def shelfLayout(self, shelf, **kwargs):
        return shelf in self.shelves if kwargs.get('exists') else list(self.shelves[shelf])

    def shelfButton(self, button=None, **kwargs):
        if kwargs.get('exists'):
            return button in self.buttons
        if kwargs.pop('query', False):
            if kwargs.get('fullPathName'):
                return button
            return self.buttons[button].get(next(key for key, value in kwargs.items() if value))
        if kwargs.pop('edit', False):
            self.buttons[button].update(kwargs)
            return button
        shelf = kwargs.pop('parent')
        button = 'qzButton%d' % len(self.buttons)
        self.buttons[button] = kwargs
        self.shelves[shelf].append(button)
        return button

    def internalVar(self, **kwargs):
        return str(self.folder)

    def saveShelf(self, shelf, filename):
        if self.fail_save:
            return False
        Path(filename + '.mel').write_text('shelf snapshot', encoding='utf-8')
        return '1'

    def deleteUI(self, button):
        self.buttons.pop(button)
        for items in self.shelves.values():
            if button in items:
                items.remove(button)

    def warning(self, text):
        self.warnings.append(text)


class ShelfTests(Fixture):
    def setUp(self):
        super().setUp()
        self.commands = ShelfCommands(self.folder / 'prefs')
        maya = types.ModuleType('maya')
        maya.cmds = self.commands
        maya.mel = types.SimpleNamespace(eval=lambda value: 'ShelfTabs')
        self.modules = patch.dict(sys.modules, {'maya': maya, 'maya.cmds': maya.cmds, 'maya.mel': maya.mel})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        super().tearDown()

    def pin(self, tool):
        return user_shelf.user_script_shelf(root=str(self.root), script_id=tool['id'])

    def test_pin_current_shelf_saves_without_running_and_repin_updates_in_place(self):
        marker = self.folder / 'ran.txt'
        tool = store.add_code('中文工具', 'from pathlib import Path\nPath(%r).write_text("ran")\n' % str(marker))
        pinned = self.pin(tool)
        self.assertFalse(marker.exists())
        self.assertFalse(pinned['updated'])
        self.assertEqual(self.commands.shelves['OtherShelf'], [])
        self.assertEqual(self.commands.buttons['existing']['docTag'], 'user-owned')
        self.assertTrue(Path(pinned['saved']).is_file())
        store.update_script(tool['id'], name='新的名称')
        repeated = self.pin(tool)
        self.assertTrue(repeated['updated'])
        self.assertEqual(repeated['button'], pinned['button'])
        self.assertEqual(self.commands.buttons[pinned['button']]['label'], '新的名称')
        self.assertEqual(len(self.commands.shelves['CurrentShelf']), 2)

    def test_launcher_runs_latest_entry_after_directory_rename_without_bridge_or_ui(self):
        marker = self.folder / 'ran.txt'
        tool = store.add_code("工具's", 'from pathlib import Path\n'
            'def first(): Path(%r).write_text("first")\n'
            'def second(): Path(%r).write_text("second")\n' % (str(marker), str(marker)), 'first')
        pinned = self.pin(tool)
        command = self.commands.buttons[pinned['button']]['command']
        compile(command, '<shelf-button>', 'single')
        self.assertNotIn('socket', command)
        self.assertNotIn('ui.user_scripts', command)
        Path(tool['folder']).rename(self.root / '目录新名称')
        store.update_script(tool['id'], entry='second')
        exec(command, {})
        self.assertEqual(marker.read_text(), 'second')

    def test_removed_tool_reports_missing_and_does_not_execute_stale_code(self):
        tool = self.add()
        pinned = self.pin(tool)
        command = self.commands.buttons[pinned['button']]['command']
        store.remove_script(tool['id'])
        exec(command, {})
        self.assertTrue(self.commands.warnings)
        self.assertIn('移除', self.commands.warnings[-1])

    def test_save_failure_rolls_back_own_button_and_preserves_existing_file(self):
        tool = self.add()
        self.commands.fail_save = True
        with self.assertRaises(RuntimeError):
            self.pin(tool)
        self.assertEqual(self.commands.shelves['CurrentShelf'], ['existing'])
        self.commands.fail_save = False
        pinned = self.pin(tool)
        snapshot = dict(self.commands.buttons[pinned['button']])
        store.update_script(tool['id'], name='new')
        self.commands.fail_save = True
        with self.assertRaises(RuntimeError):
            self.pin(tool)
        self.assertEqual(self.commands.buttons[pinned['button']], snapshot)
        self.assertEqual(Path(pinned['saved']).read_text(), 'shelf snapshot')
        self.commands.fail_save = False
        with patch.object(user_shelf.os, 'replace', side_effect=PermissionError('locked shelf')):
            with self.assertRaises(PermissionError):
                self.pin(tool)
        self.assertEqual(self.commands.buttons[pinned['button']], snapshot)
        self.assertEqual(Path(pinned['saved']).read_text(), 'shelf snapshot')

    def test_preflight_refreshes_then_pins_once_and_disconnected_never_requests(self):
        tool = self.add()
        calls = []
        class Client:
            dcc, connected, stale = 'maya', True, True
            def call(self, action, **params):
                calls.append((action, params))
                if action == 'reload_actions':
                    self.stale = False
                    return dict(ok=True)
                if params.get('op') == 'info':
                    return dict(ok=False, error='unknown action') if self.stale else dict(ok=True, data=dict(version=1))
                return dict(ok=True, data=dict(shelf='CurrentShelf'))
        client = Client()
        self.assertTrue(store.pin_script_to_maya_shelf(client, tool)['ok'])
        self.assertEqual([action for action, _ in calls], ['user_script_shelf', 'reload_actions', 'user_script_shelf', 'user_script_shelf'])
        self.assertEqual(calls[-1][1]['script_id'], tool['id'])
        calls.clear()
        client.connected = False
        self.assertFalse(store.pin_script_to_maya_shelf(client, tool)['ok'])
        self.assertEqual(calls, [])

    def test_failure_not_retried_and_max_not_sent_to_maya(self):
        tool = self.add()
        calls = []
        def call(action, **params):
            calls.append((action, params))
            if params.get('op') == 'info':
                return dict(ok=True, data=dict(version=1))
            raise RuntimeError('timeout')
        client = types.SimpleNamespace(dcc='maya', connected=True, call=call)
        self.assertFalse(store.pin_script_to_maya_shelf(client, tool)['ok'])
        self.assertEqual(sum(params.get('op') == 'pin' for _, params in calls), 1)
        calls.clear()
        self.assertFalse(store.pin_script_to_maya_shelf(client, dict(tool, dcc='max'))['ok'])
        self.assertEqual(calls, [])


class ShelfQtTests(Fixture):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_right_click_installs_asynchronously_and_logs_on_main_thread(self):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        from contextlib import ExitStack
        from ui.main_window import MainWindow
        tool = self.add()
        with ExitStack() as stack:
            stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
            stack.enter_context(patch.object(MainWindow, '_restore_layout'))
            stack.enter_context(patch.object(MainWindow, '_restore_window', lambda w: w.resize(1200, 800)))
            stack.enter_context(patch('ui.layout_state._default_path', return_value=str(self.folder / 'layout.json')))
            stack.enter_context(patch.object(MainWindow, '_connection_notice'))
            workers = []
            def pin(client, script):
                workers.append(threading.current_thread())
                return dict(ok=True, data=dict(shelf='CurrentShelf', name=script['name']))
            stack.enter_context(patch.object(store, 'pin_script_to_maya_shelf', side_effect=pin))
            window = MainWindow()
            try:
                window.show()
                window._side.show()
                self.app.processEvents()
                tree = window._side._tree
                group = tree._groups['maya']
                custom = next(group.child(i) for i in range(group.childCount())
                              if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', 'maya', 'user'))
                item = custom.child(0)
                tree.scrollToItem(item)
                self.app.processEvents()
                selected = []
                def choose():
                    menu = self.app.activePopupWidget()
                    if isinstance(menu, QtWidgets.QMenu):
                        action = next(a for a in menu.actions() if a.text() == '固定到 Maya 当前工具架')
                        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(action).center())
                        selected.append(True)
                QtCore.QTimer.singleShot(30, choose)
                point = tree.visualItemRect(item).center()
                event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point))
                self.app.sendEvent(tree.viewport(), event)
                for _ in range(100):
                    if not window._shelf_tasks:
                        break
                    QtTest.QTest.qWait(10)
                self.assertEqual(selected, [True])
                self.assertTrue(workers and all(worker is not threading.current_thread() for worker in workers))
                self.assertFalse(window._shelf_tasks)
                self.assertIn('CurrentShelf', window._log.toPlainText())
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
