"""Exercise the Blender send card and its direction-specific workflow."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtTest, QtWidgets

from ui.plugin_loader import scan
from ui.plugin_node import PluginNode

run_transfer = runpy.run_path(str(ROOT / 'plugins/blender/fbx_to_maya/transfer.py'))['run_transfer']


class Client:
    connected = True

    def __init__(self, host, calls):
        self.host, self.calls = host, calls
        self.stale = self.fail = None
        self.write_file = True

    def call(self, action, *, timeout=0, connect_timeout=0, **params):
        self.calls.append((self.host, action, params, threading.current_thread()))
        if action == self.fail:
            return dict(ok=False, error='host error')
        if action == self.stale:
            self.stale = None
            return dict(ok=False, error='unknown action: ' + action)
        if action == 'fbx_export_maya' and self.write_file:
            Path(params['filepath']).write_bytes(b'FBX fixture')
        data = dict(ready=True, animation_option=True, confirm_open=True, count=1, objects=3)
        return dict(ok=True, data=data)


class Log:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)

    warn = error = info


class BlenderToMayaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.exchange = Path(self.directory.name) / 'exchange'
        self.calls = []
        self.blender = Client('blender', self.calls)
        self.maya = Client('maya', self.calls)
        self.log = Log()
        local_path = patch('paths.local_data_dir', return_value=str(self.exchange))
        local_path.start()
        self.addCleanup(local_path.stop)
        self.store = QtCore.QSettings(str(Path(self.directory.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
        settings = patch.object(QtCore, 'QSettings', return_value=self.store)
        settings.start()
        self.addCleanup(settings.stop)
        self.plugin = next(info for info in scan(str(ROOT / 'plugins'))
                           if info.key == ('blender', 'fbx_to_maya'))

    def make_card(self):
        widget = self.plugin.load_factory()(self.blender, self.log)
        widget.set_dcc_clients({'maya': self.maya})
        node = PluginNode(self.plugin.name, widget, dcc='blender')
        scene = QtWidgets.QGraphicsScene()
        proxy = scene.addWidget(node)
        proxy.setPos(-60, -8)
        view = QtWidgets.QGraphicsView(scene)
        view.setSceneRect(0, 0, 500, 370)
        view.resize(500, 370)
        view.scale(1.3, 1.3)
        view.show()
        self.app.processEvents()

        def close_card():
            if widget._dialog is not None:
                widget._dialog.close()
            proxy.setWidget(None)
            scene.removeItem(proxy)
            view.close()
            node.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

        self.addCleanup(close_card)
        return view, widget

    def wait_for_send(self, widget):
        deadline = QtCore.QDeadlineTimer(3000)
        while not widget.import_button.isEnabled() and not deadline.hasExpired():
            QtTest.QTest.qWait(5)
        self.assertTrue(widget.import_button.isEnabled())

    def test_settings_popup_persists_and_import_uses_worker(self):
        view, widget = self.make_card()
        self.assertFalse(widget.settings_button.icon().isNull())
        for button in (widget.open_button, widget.import_button, widget.settings_button):
            self.assertEqual(button.toolTip(), '')
        QtTest.QTest.mouseClick(widget.settings_button, QtCore.Qt.LeftButton)
        popup = widget._dialog
        self.assertIs(popup.parentWidget(), view)
        rectangle = QtCore.QRect(view.viewport().mapToGlobal(QtCore.QPoint()), view.viewport().size())
        self.assertTrue(rectangle.contains(popup.geometry()), (rectangle, popup.geometry()))
        for checkbox in (popup.animation_checkbox, popup.display_checkbox):
            self.assertTrue(checkbox.isChecked())
            QtTest.QTest.mouseClick(checkbox, QtCore.Qt.LeftButton,
                                     pos=QtCore.QPoint(8, checkbox.height() // 2))
            self.assertFalse(checkbox.isChecked())
        QtTest.QTest.keyClick(popup, QtCore.Qt.Key_Escape)

        _, restored = self.make_card()
        QtTest.QTest.mouseClick(restored.import_button, QtCore.Qt.LeftButton)
        self.assertFalse(restored.settings_button.isEnabled())
        self.wait_for_send(restored)
        self.assertEqual(self.calls[-2][1], 'fbx_export_maya')
        self.assertIs(self.calls[-2][2]['animation'], False)
        self.assertEqual(self.calls[-1][1], 'fbx_receive_blender_selection')
        self.assertEqual(self.calls[-1][2], dict(filepath=self.calls[-2][2]['filepath'],
                         mode='import', animation=False, optimize_display=False, discard_unsaved=False))
        self.assertTrue(all(worker is not threading.main_thread() for _, _, _, worker in self.calls))
        self.assertTrue(any('导入到 Maya 完成' in text for text in self.log.messages))
        self.assertFalse(Path(self.calls[-2][2]['filepath']).exists())

    def test_open_dialog_default_cancels_and_explicit_replace_sends(self):
        _, widget = self.make_card()
        observed = []

        def choose(replace):
            box = self.app.activeModalWidget()
            if not isinstance(box, QtWidgets.QMessageBox):
                return
            observed.append((box.defaultButton().text(), box.text()))
            if replace:
                confirm = next(button for button in box.buttons() if button.text() == '替换场景')
                QtTest.QTest.mouseClick(confirm, QtCore.Qt.LeftButton)
            else:
                QtTest.QTest.keyClick(box, QtCore.Qt.Key_Return)

        for replace in (False, True):
            QtCore.QTimer.singleShot(0, lambda replace=replace: choose(replace))
            QtTest.QTest.mouseClick(widget.open_button, QtCore.Qt.LeftButton)
            self.assertEqual(observed[-1][0], '取消')
            self.assertIn('未保存的修改会丢失', observed[-1][1])
            if not replace:
                self.assertEqual(self.calls, [])
        self.wait_for_send(widget)
        self.assertEqual(self.calls[-1][1], 'fbx_receive_blender_selection')
        self.assertEqual(self.calls[-1][2]['mode'], 'open')
        self.assertIs(self.calls[-1][2]['discard_unsaved'], True)
        self.assertIs(self.calls[-1][2]['animation'], True)
        self.assertIs(self.calls[-1][2]['optimize_display'], True)

    def test_refreshes_preflights_before_export_and_keeps_failed_exchange(self):
        self.blender.stale = 'fbx_blender_preflight'
        self.maya.stale = 'fbx_maya_blender_receive_preflight'
        self.maya.fail = 'fbx_receive_blender_selection'
        result = run_transfer(self.blender, self.maya, 'open', discard_unsaved=True)
        self.assertFalse(result['ok'], result)
        self.assertEqual([action for _, action, _, _ in self.calls],
                         ['fbx_maya_blender_receive_preflight', 'reload_actions',
                          'fbx_maya_blender_receive_preflight', 'fbx_blender_preflight',
                          'reload_actions', 'fbx_blender_preflight', 'fbx_export_maya',
                          'fbx_receive_blender_selection'])
        self.assertTrue((Path(result['retained_path']) / 'selection.fbx').is_file())

    def test_no_fbx_never_touches_maya_scene(self):
        self.blender.write_file = False
        result = run_transfer(self.blender, self.maya, 'open', discard_unsaved=True)
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.calls[-1][1], 'fbx_export_maya')


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=2).result
    print(f'PASS {result.testsRun - len(result.failures) - len(result.errors)} / {result.testsRun}')
    sys.exit(0 if result.wasSuccessful() else 1)
