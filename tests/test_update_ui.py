"""Update entry exits only after confirmation and rejects active tools."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from ui.main_window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_layout'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_window'))
        self.stack.enter_context(patch('paths.user_plugins_dir', return_value=str(Path(self.temp.name) / 'users')))
        self.stack.enter_context(patch('ui.layout_state._default_path', return_value=str(Path(self.temp.name) / 'layout.json')))
        self.window = MainWindow()

    def tearDown(self):
        self.window._shelf_tasks.clear()
        self.window._canvas._proxies.clear()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.stack.close()
        self.temp.cleanup()

    def test_developer_entry_does_not_start_updater(self):
        with patch('maintenance.client.start_update') as start:
            self.window._on_update_requested()
            start.assert_not_called()

    def test_busy_tool_blocks_package_selection(self):
        from PySide6 import QtWidgets
        self.window._shelf_tasks[('max', 'test')] = object()
        with patch('paths.is_frozen', return_value=True), patch.object(QtWidgets.QFileDialog, 'getOpenFileName') as choose:
            self.window._on_update_requested()
            choose.assert_not_called()

    def test_confirmation_starts_detached_helper_then_requests_graceful_close(self):
        from PySide6 import QtWidgets
        with patch('paths.is_frozen', return_value=True), \
                patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('update.zip', '')), \
                patch('maintenance.release.inspect_package', return_value=dict(version='0.2.0')), \
                patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.Yes), \
                patch('maintenance.client.start_update') as start, patch.object(self.window, 'close') as close:
            self.window._on_update_requested()
            start.assert_called_once()
            self.assertEqual(start.call_args.kwargs['wait_pid'], os.getpid())
            close.assert_called_once()

    def test_cancel_and_helper_start_failure_leave_middle_tier_open(self):
        from PySide6 import QtWidgets
        with patch('paths.is_frozen', return_value=True), \
                patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('update.zip', '')), \
                patch('maintenance.release.inspect_package', return_value=dict(version='0.2.0')), \
                patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.Cancel) as confirm, \
                patch('maintenance.client.start_update', side_effect=OSError('failed to launch')) as start, \
                patch.object(self.window, 'close') as close:
            self.window._on_update_requested()
            start.assert_not_called()
            close.assert_not_called()
            confirm.return_value = QtWidgets.QMessageBox.Yes
            self.window._on_update_requested()
            close.assert_not_called()
            self.assertIn('failed to launch', self.window._log.toPlainText())


if __name__ == '__main__':
    unittest.main()
