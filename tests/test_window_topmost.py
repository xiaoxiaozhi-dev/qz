"""Isolated pin controls and preference persistence; no DCC connections."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6 import QtCore, QtTest, QtWidgets
from ui import layout_state
from ui.main_window import MainWindow
from ui.window_topmost import set_window_topmost


class TopmostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.path = Path(self.temp.name) / 'layout.json'
        self.stack.enter_context(patch.object(layout_state, '_default_path', return_value=str(self.path)))
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_layout'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_window', lambda w: w.resize(1100, 700)))
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.stack.close()
        self.temp.cleanup()

    def window(self):
        window = MainWindow()
        self.windows.append(window)
        window.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        window.show()
        self.app.processEvents()
        return window

    def test_pin_is_independent_of_resource_and_settings_panels(self):
        window = self.window()
        bar = window._activity
        pin = bar._btn_pin
        self.assertFalse(pin.icon().isNull())
        self.assertLess(pin.y(), bar._btn_settings.y())
        self.assertNotIn(pin, bar._group.buttons())
        with patch('ui.main_window.set_window_topmost') as apply:
            QtTest.QTest.mouseClick(pin, QtCore.Qt.LeftButton)
            apply.assert_called_once_with(window, True)
            self.assertTrue(pin.isChecked())
            self.assertEqual(pin.toolTip(), '取消置顶')
            self.assertEqual(bar._current, -1)
            bar.show_panel(0)
            bar.show_panel(1)
            self.assertTrue(pin.isChecked())
            QtTest.QTest.mouseClick(pin, QtCore.Qt.LeftButton)
            self.assertFalse(pin.isChecked())
            self.assertEqual(pin.toolTip(), '窗口置顶')
            self.assertTrue(bar.is_showing_settings())

    def test_preference_is_immediate_and_survives_other_layout_writes_and_restart(self):
        with patch('ui.main_window.set_window_topmost') as apply:
            first = self.window()
            first._set_always_on_top(True)
            self.assertTrue(json.loads(self.path.read_text(encoding='utf-8'))['always_on_top'])
            layout_state.save_log_limit(2300)
            layout_state.save_open([(('maya', 'example'), 10, 20)], False)
            layout_state.save_window(dict(layout_state.DEFAULT_WINDOW, maximized=True))
            layout_state.forget_plugin(('maya', 'example'))
            self.assertTrue(layout_state.load_always_on_top())
            first.close()
            second = self.window()
            self.assertTrue(second._always_on_top)
            self.assertTrue(second._activity._btn_pin.isChecked())
            apply.assert_called_with(second, True)
            self.assertEqual(layout_state.load_log_limit(), 2300)
            second._set_always_on_top(False)
            second.close()
            third = self.window()
            self.assertFalse(third._always_on_top)
            self.assertFalse(layout_state.load_always_on_top())

    def test_default_and_invalid_preferences_are_off(self):
        self.assertFalse(layout_state.load_always_on_top())
        for value in (None, 'false', 'true', 1, [], {}):
            self.path.write_text(json.dumps({'version': 4, 'always_on_top': value}), encoding='utf-8')
            self.assertFalse(layout_state.load_always_on_top())

    def test_os_failure_restores_button_and_does_not_save_false_success(self):
        window = self.window()
        with patch('ui.main_window.set_window_topmost', side_effect=OSError('test denied')):
            QtTest.QTest.mouseClick(window._activity._btn_pin, QtCore.Qt.LeftButton)
        self.assertFalse(window._always_on_top)
        self.assertFalse(window._activity._btn_pin.isChecked())
        self.assertFalse(layout_state.load_always_on_top())
        self.assertIn('切换窗口置顶失败', window._log.toPlainText())

    def test_fallback_preserves_visibility_size_and_maximized_state(self):
        window = self.window()
        with patch('ui.window_topmost.sys.platform', 'test'):
            before = window.geometry()
            set_window_topmost(window, True)
            self.assertTrue(window.windowFlags() & QtCore.Qt.WindowStaysOnTopHint)
            self.assertTrue(window.isVisible())
            self.assertEqual(window.geometry(), before)
            window.showMaximized()
            self.app.processEvents()
            set_window_topmost(window, False)
            self.assertTrue(window.isMaximized())
            self.assertFalse(window.windowFlags() & QtCore.Qt.WindowStaysOnTopHint)


if __name__ == '__main__':
    unittest.main()
