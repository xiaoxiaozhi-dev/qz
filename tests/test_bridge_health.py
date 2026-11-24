"""Random-port busy hosts and real queued Qt callbacks, without user DCCs."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import queue
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
from dcc_bridge import DccClient, BridgeUnavailable
from maya_bridge import server as maya
from max_bridge import server as max_server
from ui.connection_health import ConnectionHealth


def wait_until(predicate, seconds=2):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('Test condition did not become true')
        time.sleep(.005)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = tempfile.TemporaryDirectory()
        self.stack.enter_context(patch.object(max_server, '_TASK_QUEUE', queue.Queue()))
        self.stack.enter_context(patch.object(max_server, 'WORKER_FAILURE_PATH', str(Path(self.temp.name) / 'error')))
        self.stack.enter_context(patch.object(max_server, '_active_task', None))
        for server, version in ((maya, 'maya_version'), (max_server, 'max_version')):
            self.stack.enter_context(patch.dict(server.ACTIONS, ping=lambda v=version: {'pong': True, v: '2024' if v == 'maya_version' else '2020'}))
        self.release = threading.Event()
        self.listeners = []

    def tearDown(self):
        self.release.set()
        for server, thread in self.listeners:
            server.stop()
            thread.join(1)
        self.stack.close()
        self.temp.cleanup()

    def listener(self, server, dcc):
        server.stop()
        thread = threading.Thread(target=server.start_server, args=(0,), daemon=True)
        thread.start()
        self.listeners.append((server, thread))
        wait_until(lambda: server._server_sock is not None)
        return DccClient(port=server._server_sock.getsockname()[1], dcc=dcc)

    def test_maya_unrelated_main_thread_work_keeps_one_probe_and_recovers(self):
        calls = []
        def main(fn):
            calls.append(threading.current_thread().name)
            self.release.wait(5)
            return fn()
        with patch.object(maya, '_run_in_main', side_effect=main):
            client = self.listener(maya, 'maya')
            started = time.monotonic()
            for _ in range(5):
                report = client.probe()
                self.assertEqual(report['state'], 'waiting')
                self.assertTrue(report['reachable'])
                self.assertFalse(report['data']['main_thread_ready'])
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(calls), 1, 'Pending main-thread probes accumulated')
            detached = client.call('bridge_disconnect')
            self.assertFalse(detached['ok'])
            self.assertIn('等待响应', detached['error'])
            self.assertIsNotNone(maya._server_sock)
            self.release.set()
            wait_until(lambda: maya._heartbeat_probe['done'].is_set())
            self.assertEqual(client.probe()['state'], 'ready')
            self.assertEqual(client.last_ping_data['maya_version'], '2024')

    def test_maya_business_request_continues_once_while_heartbeats_report_busy(self):
        entered, calls, results = threading.Event(), [], []
        main_lock = threading.Lock()
        def main(fn):
            with main_lock:
                return fn()
        def slow():
            calls.append('write')
            entered.set()
            self.release.wait(5)
            return {'finished': True}
        with patch.object(maya, '_run_in_main', side_effect=main), patch.dict(maya.ACTIONS, slow=slow):
            client = self.listener(maya, 'maya')
            worker = threading.Thread(target=lambda: results.append(client.call('slow', timeout=2)), daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1))
            for _ in range(3):
                self.assertEqual(client.probe()['state'], 'busy')
            self.assertEqual(calls, ['write'])
            self.release.set()
            worker.join(1)
            self.assertTrue(results[0]['ok'])
            self.assertEqual(calls, ['write'])

    def test_max_unrelated_script_keeps_port_alive_and_single_worker_probe(self):
        client = self.listener(max_server, 'max')
        for _ in range(5):
            reply = client.probe()
            self.assertEqual(reply['state'], 'waiting')
            self.assertTrue(reply['reachable'])
        self.assertEqual(max_server.pending_count(), 1)
        detached = client.call('bridge_disconnect')
        self.assertFalse(detached['ok'])
        self.assertIn('等待响应', detached['error'])
        self.assertIsNotNone(max_server._server_sock)
        stop = threading.Event()
        def pump():
            while not stop.wait(.005):
                max_server.drain()
        worker = threading.Thread(target=pump, daemon=True)
        worker.start()
        try:
            wait_until(lambda: max_server._heartbeat_task.done)
            self.assertEqual(client.probe()['state'], 'ready')
            self.assertEqual(client.last_ping_data['max_version'], '2020')
        finally:
            stop.set()
            worker.join(1)
        reply = client.probe()
        self.assertEqual(reply['state'], 'waiting')
        self.assertEqual(reply['data']['max_version'], '2020')
        self.assertEqual(max_server.pending_count(), 1)

    def test_max_active_task_and_worker_failure_are_distinct(self):
        client = self.listener(max_server, 'max')
        max_server._active_task = max_server._Task('weight_import', {})
        self.assertEqual(client.probe()['state'], 'busy')
        self.assertEqual(max_server.pending_count(), 0)
        Path(max_server.WORKER_FAILURE_PATH).write_text('failed')
        report = client.probe()
        self.assertEqual(report['state'], 'error')
        self.assertTrue(report['reachable'])
        self.assertIn('worker', report['reason'])

    def test_real_silent_peer_timeout_is_not_connection_refused(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            def silent():
                conn, _ = listener.accept()
                with conn:
                    conn.recv(4096)
                    self.release.wait(2)
            threading.Thread(target=silent, daemon=True).start()
            client = DccClient(port=port)
            with patch.object(client, 'ping', side_effect=lambda: client.call('ping', timeout=.05)):
                report = client.probe()
            self.assertEqual(report['state'], 'waiting')
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            port = unused.getsockname()[1]
        report = DccClient(port=port).probe()
        self.assertEqual(report['state'], 'offline')


class StateTests(unittest.TestCase):
    def test_timeouts_never_prove_disconnection_and_refusals_require_confirmation(self):
        health = ConnectionHealth()
        health.update(dict(state='ready'))
        for index in range(6):
            state = health.update(dict(state='waiting', reachable=False))
            self.assertEqual(state, 'waiting' if index < 2 else 'unresponsive')
            self.assertTrue(health.connected)
        health.update(dict(state='ready'))
        self.assertEqual(health.update(dict(state='offline')), 'waiting')
        self.assertEqual(health.update(dict(state='offline')), 'offline')
        self.assertFalse(health.connected)
        health.update(dict(state='ready'))
        health.disconnect()
        self.assertFalse(health.connected)
        self.assertEqual(health.failures, 0)


class QtHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from ui.main_window import MainWindow
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch.object(MainWindow, '_restore_layout'))
        self.notice = self.stack.enter_context(patch.object(MainWindow, '_connection_notice'))
        self.window = MainWindow()
        self.window._attached.update(maya=True, max=True)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.stack.close()

    def test_both_hosts_keep_versions_and_do_not_reconnect_during_long_work(self):
        w = self.window
        for dcc, key, version in (('maya', 'maya_version', '2024'), ('max', 'max_version', '2020')):
            w._clients[dcc].last_ping_data = {key: version}
            with patch.object(w._bridges[dcc], 'connection_async') as connection:
                w._on_health_result(dict(state='ready'), dcc)
                w._on_health_result(dict(state='busy'), dcc)
                self.assertIn('繁忙', w._side.settings._state_labels[dcc].text())
                for _ in range(5):
                    w._on_health_result(dict(state='waiting', reachable=False), dcc)
                self.assertTrue(w._bridge_ok[dcc])
                self.assertTrue(w._clients[dcc].connected)
                self.assertEqual(w._versions[dcc], version)
                self.assertIn('持续无响应', w._side.settings._state_labels[dcc].text())
                self.assertEqual(w._side.settings._connection_buttons[dcc].text(), '断开')
                w._on_health_result(dict(state='ready'), dcc)
                connection.assert_not_called()
        text = w._log.toPlainText()
        self.assertNotIn('已断开', text)
        self.assertEqual(text.count('持续无响应'), 2)
        self.assertEqual(self.notice.call_count, 2)

    def test_confirmed_closed_port_and_stale_probe_after_manual_disconnect(self):
        w = self.window
        w._on_health_result(dict(state='ready'), 'maya')
        w._on_health_result(dict(state='offline'), 'maya')
        self.assertTrue(w._bridge_ok['maya'])
        w._on_health_result(dict(state='offline'), 'maya')
        self.assertFalse(w._bridge_ok['maya'])
        epoch = w._connection_epochs['max']
        w._connection_epochs['max'] += 1
        w._on_health_epoch(json.dumps(dict(state='ready')), 'max', epoch)
        self.assertFalse(w._bridge_ok['max'])

    def test_worker_error_is_visible_and_does_not_claim_port_is_closed(self):
        w = self.window
        w._on_health_result(dict(state='ready'), 'max')
        for _ in range(3):
            w._on_health_result(dict(state='error', reason='worker stopped', reachable=True), 'max')
        self.assertTrue(w._bridge_ok['max'])
        self.assertIn('桥接异常', w._side.settings._state_labels['max'].text())
        self.assertEqual(w._log.toPlainText().count('worker stopped'), 1)

    def test_first_discovered_busy_host_announces_connection_after_ready(self):
        w = self.window
        w._attached['maya'] = False
        with patch.object(w._bridges['maya'], 'connection_async') as connection:
            w._on_health_result(dict(state='busy'), 'maya')
            connection.assert_not_called()
            self.notice.assert_not_called()
            w._on_health_result(dict(state='ready'), 'maya')
            connection.assert_called_once()
            self.notice.assert_called_once()

    def test_global_plugin_pool_cannot_starve_actual_queued_heartbeat_callback(self):
        from PySide6 import QtCore, QtTest
        w = self.window
        pool = QtCore.QThreadPool.globalInstance()
        previous = pool.maxThreadCount()
        release, entered = threading.Event(), threading.Event()
        class LongPlugin(QtCore.QRunnable):
            def run(self):
                entered.set()
                release.wait(3)
        pool.setMaxThreadCount(1)
        pool.start(LongPlugin())
        self.assertTrue(entered.wait(1))
        try:
            with patch.object(w._clients['maya'], 'probe', return_value=dict(state='ready', reachable=True)):
                w._kick_ping('maya')
                deadline = time.monotonic() + 1
                while w._ping_inflight['maya'] and time.monotonic() < deadline:
                    QtTest.QTest.qWait(5)
            self.assertFalse(w._ping_inflight['maya'])
            self.assertTrue(w._bridge_ok['maya'])
        finally:
            release.set()
            pool.waitForDone(1000)
            pool.setMaxThreadCount(previous)


if __name__ == '__main__':
    unittest.main(verbosity=2)
