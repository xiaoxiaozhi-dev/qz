"""Test the Qt entry point and coordinator failure behavior."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtTest, QtWidgets

from plugins.maya.skin_weights.widget import SkinWeightsWidget
from ui.maya_blender_skin_transfer import run_blender_transfer


class TransferTests(unittest.TestCase):
    def test_timeout_is_reported_without_resending(self):
        maya = Mock(connected=True)
        maya.call.return_value = dict(ok=True, data=dict(vertices=1))
        blender = Mock(connected=True)
        blender.call.side_effect = [dict(ok=True, data=dict(name='Original')),
                                    dict(ok=False, error='等待 Blender 任务完成超时')]

        result = run_blender_transfer(maya, blender)

        self.assertFalse(result['ok'])
        self.assertIn('Original', result['error'])
        self.assertIn('再决定是否重发', result['error'])
        self.assertEqual(blender.call.call_count, 2)
        action, = blender.call.call_args.args
        params = blender.call.call_args.kwargs
        self.assertEqual(action, 'skin_weights_receive_maya')
        self.assertEqual(params['timeout'], 605)
        self.assertEqual(params['target'], dict(name='Original'))
        self.assertFalse(Path(params['directory']).exists())

    def test_card_uses_injected_blender_client_and_preserves_maya_result(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        maya = Mock(connected=True)
        maya.call.return_value = dict(ok=True, data=dict(vertices=5))
        blender = Mock(connected=True)
        blender.call.side_effect = [dict(ok=True, data=dict(name='Original')),
                                    dict(ok=True, data=dict(name='Original', armature='Rig', vertices=5, groups=4))]
        log = SimpleNamespace(info=Mock(), error=Mock())
        card = SkinWeightsWidget(maya, log)
        card.set_dcc_clients({'blender': blender})
        card.inf_lab.setText('4')
        card.show()
        try:
            QtTest.QTest.mouseClick(card.send_blender_btn, QtCore.Qt.LeftButton)
            self.assertFalse(card.send_btn.isEnabled())
            for _ in range(150):
                if card.send_blender_btn.isEnabled():
                    break
                QtTest.QTest.qWait(10)
            self.assertTrue(card.send_btn.isEnabled())
            self.assertEqual(card.inf_lab.text(), '4')
            log.error.assert_not_called()
            self.assertIn('Original', log.info.call_args.args[0])
            self.assertEqual(maya.call.call_args.args, ('skin_weights_export_blender',))
        finally:
            card.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
