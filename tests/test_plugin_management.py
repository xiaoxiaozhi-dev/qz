"""Official plugins are read-only in the UI; user-script menus stay editable."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ui import plugin_loader, plugin_management as manage


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.plugins = self.folder / 'plugins'
        self.stack = ExitStack()
        self.stack.enter_context(patch('paths.plugins_dir', return_value=str(self.plugins)))
        self.stack.enter_context(patch('paths.user_plugins_dir', side_effect=lambda dcc='maya': str(self.folder / 'users' / dcc)))
        for dcc in ('maya', 'max'):
            folder = self.plugins / dcc / 'demo'
            folder.mkdir(parents=True)
            (folder / 'manifest.json').write_text(json.dumps(dict(id='demo', dcc=dcc, name='官方工具', widget_factory='widget.create_widget')), encoding='utf-8')
            (folder / 'widget.py').write_text('def create_widget(*args, **kwargs): pass', encoding='utf-8')
        self.infos = plugin_loader.scan(str(self.plugins))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def window(self):
        from ui.main_window import MainWindow
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_layout'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_window', lambda w: w.resize(1200, 800)))
        self.stack.enter_context(patch('ui.layout_state._default_path', return_value=str(self.folder / 'layout.json')))
        window = MainWindow()
        window.show()
        window._side.show()
        self.app.processEvents()
        return window

    def test_official_menus_only_open_actual_folder_for_both_hosts(self):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        window = self.window()
        try:
            tree = window._side._tree
            for info in self.infos:
                group = tree._groups[info.dcc]
                item = next(group.child(i) for i in range(group.childCount())
                            if group.child(i).data(0, QtCore.Qt.UserRole) == ('plugin', info.key))
                tree.scrollToItem(item)
                self.app.processEvents()
                labels = []
                def choose():
                    menu = self.app.activePopupWidget()
                    if isinstance(menu, QtWidgets.QMenu):
                        labels.extend(a.text() for a in menu.actions())
                        action = menu.actions()[0]
                        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(action).center())
                with patch.object(QtGui.QDesktopServices, 'openUrl', return_value=True) as opened:
                    QtCore.QTimer.singleShot(25, choose)
                    point = tree.visualItemRect(item).center()
                    self.app.sendEvent(tree.viewport(), QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point)))
                    self.assertEqual(labels, ['打开插件位置'])
                    self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), Path(info._dir))
        finally:
            window.close()
            window.deleteLater()

    def test_direct_edit_operations_are_ignored_and_registration_unchanged(self):
        from PySide6 import QtWidgets
        window = self.window()
        try:
            for info in self.infos:
                manifest = Path(info._dir) / 'manifest.json'
                original = manifest.read_bytes()
                with patch.object(QtWidgets.QInputDialog, 'getText') as rename, patch.object(QtWidgets.QMessageBox, 'exec') as confirm:
                    window._manage_plugin('rename', info)
                    window._manage_plugin('delete', info)
                    rename.assert_not_called()
                    confirm.assert_not_called()
                self.assertEqual(manifest.read_bytes(), original)
                self.assertTrue((Path(info._dir) / 'widget.py').is_file())
        finally:
            window.close()
            window.deleteLater()

    def test_invalid_registration_and_outside_folder_are_rejected(self):
        info = self.infos[0]
        with self.assertRaises(ValueError):
            manage.plugin_folder(replace(info, _dir=str(self.folder)))
        manifest = Path(info._dir) / 'manifest.json'
        manifest.write_text('{"dcc":"maya","id":"another"}')
        with self.assertRaises(ValueError):
            manage.plugin_folder(info)


if __name__ == '__main__':
    unittest.main()
