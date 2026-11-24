"""Exercise the Maya card's actual Qt controls and bridge call contracts."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtTest, QtWidgets

from plugins.maya.fbx_to_blender import widget
from ui.maya_to_blender import run_transfer


class TransferTests(unittest.TestCase):
    def test_timeout_retains_payload_and_never_retries(self):
        maya = Mock(connected=True)
        maya.call.return_value = dict(ok=True, data=dict(vertices=5, name='ns:Target'))
        blender = Mock(connected=True)
        blender.call.side_effect = [dict(ok=True, data=dict(name='target')),
                                    dict(ok=False, error='等待 Blender 任务完成超时')]
        with tempfile.TemporaryDirectory() as root, patch('ui.maya_to_blender.paths.local_data_dir', return_value=root):
            result = run_transfer(maya, blender, 'vertices')
            self.assertFalse(result['ok'])
            self.assertIn('再决定是否重发', result['error'])
            self.assertTrue(Path(result['retained_path']).is_dir())
        self.assertEqual(blender.call.call_count, 2)
        self.assertEqual(blender.call.call_args_list[0].kwargs['name'], 'ns:Target')
        self.assertEqual(blender.call.call_args.args, ('vertex_positions_receive_maya',))
        self.assertEqual(blender.call.call_args.kwargs['timeout'], 605)

    def test_open_without_consent_never_touches_the_bridge(self):
        maya, blender = Mock(connected=True), Mock(connected=True)
        result = run_transfer(maya, blender, 'open', True)
        self.assertFalse(result['ok'])
        maya.call.assert_not_called()
        blender.call.assert_not_called()

    def test_card_default_cancel_and_animation_setting(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        maya, blender = Mock(connected=True), Mock(connected=True)
        log = SimpleNamespace(info=Mock(), error=Mock())
        settings = Mock()
        settings.value.return_value = False
        with patch.object(widget.QtCore, 'QSettings', return_value=settings):
            card = widget.FbxToBlenderWidget(maya, log)
        card.set_dcc_clients({'blender': blender})
        self.assertFalse(card.findChildren(QtWidgets.QLabel))
        card.show()

        def click(control):
            QtTest.QTest.mouseClick(control, QtCore.Qt.LeftButton)

        def wait_for_task():
            for _ in range(150):
                if card._task is None:
                    break
                QtTest.QTest.qWait(10)
            self.assertIsNone(card._task)

        def reply_to_confirmation(confirm):
            dialog = app.activeModalWidget()
            if not isinstance(dialog, QtWidgets.QMessageBox):
                return
            default = dialog.defaultButton()
            default_codes.append(dialog.standardButton(default))
            button = next(button for button in dialog.buttons() if dialog.buttonRole(button) ==
                          QtWidgets.QMessageBox.AcceptRole) if confirm else default
            click(button)

        def receive(maya, blender, mode, animation, confirmed):
            return dict(ok=True, mode=mode, data=dict(objects=1, opened_path='sample.blend',
                                                      vertices=5, name='target'), warnings=[])

        default_codes = []
        try:
            with patch.object(widget, 'run_transfer', side_effect=receive) as transfer:
                QtCore.QTimer.singleShot(10, lambda: reply_to_confirmation(False))
                click(card.open_button)
                transfer.assert_not_called()
                self.assertEqual(default_codes, [QtWidgets.QMessageBox.Cancel])
                self.assertFalse(card.animation_action.isChecked())
                click(card.settings_button)
                self.assertTrue(card.settings_menu.isVisible())
                click_action = card.settings_menu.actionGeometry(card.animation_action).center()
                QtTest.QTest.mouseClick(card.settings_menu, QtCore.Qt.LeftButton, pos=click_action)
                self.assertTrue(card.animation_action.isChecked())
                settings.setValue.assert_called_once_with('animation', True)
                click(card.import_button)
                wait_for_task()
                transfer.assert_called_with(maya, blender, 'import', True, False)
                QtCore.QTimer.singleShot(10, lambda: reply_to_confirmation(True))
                click(card.open_button)
                wait_for_task()
                transfer.assert_called_with(maya, blender, 'open', True, True)
                click(card.vertices_button)
                wait_for_task()
                transfer.assert_called_with(maya, blender, 'vertices', True, False)
                log.error.assert_not_called()
        finally:
            card.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
