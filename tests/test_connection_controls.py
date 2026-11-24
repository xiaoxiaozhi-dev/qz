"""Real random-port lifecycle and isolated Qt connection/preferences regression."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dcc_bridge import DccClient
from maya_bridge import server as maya_server
from max_bridge import server as max_server


def wait_until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for test condition')
        time.sleep(.005)


class ServerTests(unittest.TestCase):
    def test_maya_single_line_runs_and_preserves_old_core_error(self):
        from contextlib import redirect_stdout
        import io
        from ui import maya_setup
        with patch.object(maya_setup.paths, 'project_dir', return_value="C:\\中文 路径's\\qz"), patch.object(sys, 'path', list(sys.path)):
            code = maya_setup.snippet()
            self.assertEqual(len(code.splitlines()), 1)
            statement = compile(code, '<maya-input>', 'single')
            with patch.object(maya_server, 'run') as run:
                exec(statement, {})
                run.assert_called_once_with()
                self.assertEqual(sys.path[0], "C:/中文 路径's/qz")
            with patch.object(maya_server, 'BRIDGE_PROTOCOL', 1), patch.object(maya_server, 'run') as run:
                output = io.StringIO()
                with redirect_stdout(output):
                    exec(statement, {})
                run.assert_not_called()
                self.assertIn('请保存场景并重启 Maya', output.getvalue())

    def lifecycle(self, server, dcc):
        server.stop()
        actions = dict(server.ACTIONS)
        server.ACTIONS['ping'] = lambda: {'pong': True, 'bridge_protocol': 2}
        notifications = []
        stop_pump = threading.Event()
        def pump():
            while not stop_pump.wait(.005):
                server.drain()
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, 'notify', side_effect=notifications.append))
            if dcc == 'max':
                stack.enter_context(patch.object(server, '_stop_worker'))
                worker = threading.Thread(target=pump, daemon=True)
                worker.start()
            try:
                for cycle in range(2):
                    listener = threading.Thread(target=server.start_server, args=(0 if cycle == 0 else port,), daemon=True)
                    listener.start()
                    wait_until(lambda: server._server_sock is not None)
                    port = server._server_sock.getsockname()[1]
                    client = DccClient(port=port, dcc=dcc)
                    self.assertTrue(client.ping()['ok'])
                    for _ in range(2):
                        self.assertTrue(client.call('bridge_connect', client_id='test-session')['ok'])
                    self.assertEqual(notifications.count('已连接 Qz 中台'), cycle + 1)
                    result = client.call('bridge_disconnect')
                    self.assertTrue(result['data']['stopped'])
                    listener.join(1)
                    self.assertFalse(listener.is_alive())
                    self.assertIsNone(server._server_sock)
                    with socket.socket() as probe:
                        probe.settimeout(.2)
                        self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
                    # The same client can be used after a new listener is started.
                    self.assertFalse(client._closed)
                    wait_until(lambda: not server._disconnecting or server._server_sock is None)
                    time.sleep(.02)
            finally:
                server.stop()
                stop_pump.set()
                if dcc == 'max':
                    worker.join(1)
                server.ACTIONS.clear()
                server.ACTIONS.update(actions)

    def test_maya_disconnect_releases_port_and_can_restart(self):
        self.lifecycle(maya_server, 'maya')

    def test_max_disconnect_releases_port_and_can_restart(self):
        self.lifecycle(max_server, 'max')

    def test_busy_requests_reject_disconnect_without_stopping_either_server(self):
        for server, dcc in ((maya_server, 'maya'), (max_server, 'max')):
            server.stop()
            listener = threading.Thread(target=server.start_server, args=(0,), daemon=True)
            listener.start()
            wait_until(lambda: server._server_sock is not None)
            port = server._server_sock.getsockname()[1]
            server._business_requests = 1
            try:
                reply = DccClient(port=port, dcc=dcc).call('bridge_disconnect')
                self.assertFalse(reply['ok'])
                self.assertIn('任务执行中', reply['error'])
                self.assertIsNotNone(server._server_sock)
                self.assertFalse(server._disconnecting)
            finally:
                server._business_requests = 0
                server.stop()
                listener.join(1)

    def test_bind_failure_never_announces_startup_success(self):
        for server in (maya_server, max_server):
            server.stop()
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1', 0))
                occupied.listen()
                with patch.object(server, 'reload_actions', return_value=[]), patch.object(server, 'notify') as notify:
                    with self.assertRaises(RuntimeError):
                        server.run(port=occupied.getsockname()[1])
                    notify.assert_not_called()
                    self.assertIsNone(server._server_sock)


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from ui.main_window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.stack.enter_context(patch('ui.layout_state._default_path', return_value=str(Path(self.temp.name) / 'layout.json')))
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_layout'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_window', lambda window: window.resize(1200, 800)))
        self.stack.enter_context(patch.object(MainWindow, '_connection_notice'))
        self.windows = []

    def window(self):
        from ui.main_window import MainWindow
        window = MainWindow()
        self.windows.append(window)
        window.show()
        window._side.show()
        window._side.show_index(1)
        self.app.processEvents()
        return window

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.stack.close()
        self.temp.cleanup()

    def test_status_row_buttons_versions_and_stale_ping_after_disconnect(self):
        from PySide6 import QtCore, QtTest
        window = self.window()
        window._clients['maya'].last_ping_data = {'maya_version': '2024'}
        window._clients['max'].last_ping_data = {'max_version': '2020'}
        window._attached.update(maya=True, max=True)
        window._on_ping_result(True, 'maya')
        window._on_ping_result(True, 'max')
        settings = window._side.settings
        self.assertIn('2020', settings._state_labels['max'].text())
        self.assertIn('2024', settings._state_labels['maya'].text())
        self.assertEqual(settings._connection_buttons['maya'].text(), '断开')
        epoch = window._connection_epochs['maya']
        with patch.object(window._bridges['maya'], 'connection_async') as request:
            QtTest.QTest.mouseClick(settings._connection_buttons['maya'], QtCore.Qt.LeftButton)
            self.assertFalse(settings._connection_buttons['maya'].isEnabled())
            self.assertEqual(request.call_args.args[1], 'disconnect')
            current = window._connection_epochs['maya']
            window._on_connection_result('maya', 'disconnect', current, json.dumps({'ok': True, 'data': {'stopped': True}}))
        window._on_ping_epoch(True, 'maya', epoch)
        self.assertFalse(window._bridge_ok['maya'])
        self.assertFalse(window._clients['maya'].connected)
        self.assertTrue(window._bridge_ok['max'])
        self.assertEqual(settings._connection_buttons['maya'].text(), '连接')
        self.assertEqual(settings._connection_buttons['max'].text(), '断开')

    def test_connect_copies_single_line_without_request_dialog_or_pending_state(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui import maya_setup, max_setup
        window = self.window()
        for dcc, setup in (('maya', maya_setup), ('max', max_setup)):
            with self.subTest(dcc=dcc), patch.object(window._bridges[dcc], 'connection_async') as request, patch.object(window._bridges[dcc], 'ping_async') as ping:
                before = window.findChildren(QtWidgets.QDialog)
                epoch = window._connection_epochs[dcc]
                button = window._side.settings._connection_buttons[dcc]
                for _ in range(2):
                    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                    code = QtWidgets.QApplication.clipboard().text()
                    self.assertEqual(code, setup.snippet())
                    self.assertEqual(len(code.splitlines()), 1)
                    compile(code, '<listener-input>', 'single')
                    self.assertIsNone(window._connection_pending[dcc])
                    self.assertEqual(window._connection_epochs[dcc], epoch)
                    self.assertTrue(button.isEnabled())
                    self.assertEqual(button.text(), '连接')
                request.assert_not_called()
                ping.assert_not_called()
                self.assertEqual(window.findChildren(QtWidgets.QDialog), before)

    def test_max_worker_error_copies_recovery_for_running_package(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        window = self.window()
        window._attached['max'] = True
        window._on_ping_result(True, 'max')
        window._on_health_result(dict(state='error', reachable=True, reason='worker stopped'), 'max')
        settings = window._side.settings
        button = settings._connection_buttons['max']
        self.assertIn('桥接异常', settings._state_labels['max'].text())
        self.assertEqual(button.text(), '重连')
        with patch('paths.project_dir', return_value=str(Path(self.temp.name) / 'QzTools')), \
                patch.object(window._bridges['max'], 'connection_async') as request:
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            code = QtWidgets.QApplication.clipboard().text()
            self.assertIn('/QzTools/max_bridge/start_max.py', code)
            compile(code, '<max listener>', 'single')
            request.assert_not_called()
            self.assertIsNone(window._connection_pending['max'])
            window._on_ping_result(True, 'max')
            self.assertEqual(request.call_args.args[1], 'attach')
            epoch = window._connection_epochs['max']
            window._on_connection_result('max', 'attach', epoch, json.dumps({'ok': True}))
        self.assertEqual(button.text(), '断开')

    def test_failed_disconnect_keeps_connection_and_recovers_button(self):
        window = self.window()
        window._bridge_ok['max'] = True
        window._clients['max'].connected = True
        window._connection_pending['max'] = 'disconnect'
        window._on_connection_result('max', 'disconnect', 0, json.dumps({'ok': False, 'error': '任务执行中'}))
        self.assertTrue(window._bridge_ok['max'])
        self.assertTrue(window._clients['max'].connected)
        self.assertTrue(window._side.settings._connection_buttons['max'].isEnabled())
        self.assertIn('任务执行中', window._log.toPlainText())

    def test_actual_async_connection_and_disconnect_round_trip(self):
        from PySide6 import QtCore, QtTest
        maya_server.stop()
        actions = dict(maya_server.ACTIONS)
        maya_server.ACTIONS['ping'] = lambda: {'pong': True, 'maya_version': '2024', 'bridge_protocol': 2}
        listener = threading.Thread(target=maya_server.start_server, args=(0,), daemon=True)
        with patch.object(maya_server, 'notify'):
            try:
                listener.start()
                wait_until(lambda: maya_server._server_sock is not None)
                window = self.window()
                window._clients['maya'].port = maya_server._server_sock.getsockname()[1]
                button = window._side.settings._connection_buttons['maya']
                # Startup happens in the DCC; regular detection discovers it,
                # then performs the actual handshake independently of copying.
                window._kick_ping('maya')
                deadline = time.monotonic() + 3
                while (not window._attached['maya'] or window._connection_pending['maya']) and time.monotonic() < deadline:
                    QtTest.QTest.qWait(10)
                    # A scheduling delay may legitimately produce a bridge-only
                    # waiting reply. Keep probing as the production timer does.
                    if (not window._attached['maya'] and not window._ping_inflight['maya']
                            and not window._connection_pending['maya']):
                        window._kick_ping('maya')
                self.assertTrue(window._attached['maya'], window._log.toPlainText())
                self.assertTrue(window._bridge_ok['maya'])
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                deadline = time.monotonic() + 3
                while window._connection_pending['maya'] and time.monotonic() < deadline:
                    QtTest.QTest.qWait(10)
                self.assertIsNone(window._connection_pending['maya'])
                self.assertFalse(window._bridge_ok['maya'], window._log.toPlainText())
                self.assertIsNone(maya_server._server_sock)
            finally:
                maya_server.stop()
                listener.join(1)
                maya_server.ACTIONS.clear()
                maya_server.ACTIONS.update(actions)

    def test_blender_connection_row_uses_real_handshake_and_disconnect(self):
        from PySide6 import QtCore, QtTest
        from blender_bridge.server import _Bridge
        from ui import blender_setup

        bridge = _Bridge(0, '5.2.4')
        self.stack.enter_context(patch.object(bridge, '_ready', return_value=True))
        listener = threading.Thread(target=bridge.serve_forever, args=(.01,), daemon=True)
        listener.start()
        try:
            window = self.window()
            client = window._clients['blender']
            client.port = bridge.server_address[1]
            settings = window._side.settings
            button = settings._connection_buttons['blender']
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.assertEqual(self.app.clipboard().text(), blender_setup.snippet(client.port))
            self.assertIsNone(window._connection_pending['blender'])

            window._kick_ping('blender')
            deadline = time.monotonic() + 3
            while not window._attached['blender'] and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertTrue(window._attached['blender'])
            self.assertIn('5.2.4', settings._state_labels['blender'].text())
            self.assertEqual(button.text(), '断开')
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.assertEqual(window._connection_pending['blender'], 'disconnect')
            deadline = time.monotonic() + 3
            while window._connection_pending['blender'] and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertFalse(client.connected, window._log.toPlainText())
            self.assertEqual(button.text(), '连接')
            self.assertEqual(client.probe()['state'], 'offline')
        finally:
            bridge.shutdown()
            bridge.server_close()
            listener.join(1)

    def test_log_limit_persists_immediately_and_across_close_and_restart(self):
        from ui import layout_state
        first = self.window()
        first._side.settings._log_spin.setValue(2300)
        self.assertEqual(first._log.max_lines(), 2300)
        self.assertEqual(layout_state.load_log_limit(), 2300)
        first.close()
        second = self.window()
        self.assertEqual(second._side.settings.log_limit(), 2300)
        self.assertEqual(second._log.max_lines(), 2300)
        layout_state.save_window({'w': 1200, 'h': 800})
        layout_state.forget_plugin(('maya', 'unused'))
        self.assertEqual(layout_state.load_log_limit(), 2300)


if __name__ == '__main__':
    unittest.main()
