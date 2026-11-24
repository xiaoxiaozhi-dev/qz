"""Bridge failures and application shutdown must not wait for a 300s task."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dcc_bridge import DccClient
from max_bridge import server


class SilentPeer:
    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.received = threading.Event()
        self.release = threading.Event()
        self.connections = []
        def serve():
            while not self.release.is_set():
                try:
                    connection, _ = self.sock.accept()
                except OSError:
                    break
                self.connections.append(connection)
                def hold(conn):
                    try:
                        conn.recv(4096)
                        self.received.set()
                        self.release.wait(10)
                    except OSError:
                        pass
                    finally:
                        conn.close()
                threading.Thread(target=hold, args=(connection,), daemon=True).start()
        threading.Thread(target=serve, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.sock.close()
        for connection in self.connections:
            try:
                connection.close()
            except OSError:
                pass


class ClientLifecycleTests(unittest.TestCase):
    def test_version_lookup_uses_cached_ping_without_ui_thread_network_call(self):
        from types import SimpleNamespace
        from ui.main_window import MainWindow
        client = DccClient(dcc="max")
        with patch.object(client, "call", side_effect=[
                {"ok": True, "data": {"max_version": "2020"}},
                {"ok": True, "data": {"pong": True, "busy": True}}]):
            client.ping()
            client.ping()
        target = SimpleNamespace(_clients={"max": client}, _versions={})
        with patch.object(client, "ping", side_effect=AssertionError("UI must not query network")):
            self.assertEqual(MainWindow._last_ping_version(target, "max"), "2020")

    def test_close_interrupts_long_socket_wait_and_rejects_late_requests(self):
        with SilentPeer() as peer:
            client = DccClient("127.0.0.1", peer.port, dcc="max")
            errors = []
            def request():
                try:
                    client.call("skin_weights_import_max", timeout=305)
                except Exception as exc:
                    errors.append(str(exc))
            thread = threading.Thread(target=request, daemon=True)
            thread.start()
            self.assertTrue(peer.received.wait(2))
            started = time.monotonic()
            client.close()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(errors)
            self.assertFalse(client._sockets)
            with self.assertRaisesRegex(RuntimeError, "连接已关闭"):
                client.ping()

    def test_window_can_exit_and_reopen_while_max_keeps_connection_open(self):
        script = '''
import json, sys, threading
from unittest.mock import patch
from PySide6 import QtCore, QtWidgets
from ui.main_window import MainWindow
from ui import layout_state
app = QtWidgets.QApplication([])
with patch.object(MainWindow, '_start_bridge_timer'), patch.object(layout_state, 'save_open'):
    win = MainWindow()
    win.show()
    client = win._clients['max']
    client.port = int(sys.argv[1])
    finished = threading.Event()
    class Request(QtCore.QRunnable):
        def run(self):
            try:
                client.call('skin_weights_import_max', timeout=305)
            except Exception:
                pass
            finally:
                finished.set()
    QtCore.QThreadPool.globalInstance().start(Request())
    QtCore.QTimer.singleShot(500, win.close)
    app.exec()
    print(json.dumps({'finished': finished.is_set()}))
'''
        with SilentPeer() as peer, tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, USERPROFILE=home, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
            for _ in range(2):
                result = subprocess.run([sys.executable, "-B", "-c", script, str(peer.port)],
                                        cwd=str(ROOT), env=env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout.strip().splitlines()[-1])["finished"])
            self.assertTrue(peer.received.is_set())


class MaxFailureTests(unittest.TestCase):
    def setUp(self):
        import queue
        self.directory = tempfile.TemporaryDirectory()
        self.queue_patch = patch.object(server, "_TASK_QUEUE", queue.Queue())
        self.marker_patch = patch.object(server, "WORKER_FAILURE_PATH", str(Path(self.directory.name) / "worker.error"))
        self.active_patch = patch.object(server, "_active_task", None)
        self.queue_patch.start()
        self.marker_patch.start()
        self.active_patch.start()

    def tearDown(self):
        self.active_patch.stop()
        self.marker_patch.stop()
        self.queue_patch.stop()
        self.directory.cleanup()

    def test_native_worker_failure_finishes_waiter_and_does_not_run_stale_task(self):
        results = []
        thread = threading.Thread(target=lambda: results.append(server.submit("probe", timeout=300)), daemon=True)
        thread.start()
        task = server._TASK_QUEUE.get(timeout=1)
        task.progress = "writing vertex 3 DQ"
        server._TASK_QUEUE.put(task)
        Path(server.WORKER_FAILURE_PATH).write_text("worker-failed\n")
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0]["ok"])
        self.assertIn("writing vertex 3 DQ", results[0]["error"])
        with patch.object(server, "dispatch", side_effect=AssertionError("must not run cancelled task")):
            server.drain()
        self.assertTrue(task.cancelled)
        self.assertFalse(server.submit("ping")["ok"])
        server.reset_worker_failure()
        self.assertFalse(server.worker_failure())

    def test_timeout_before_dispatch_does_not_execute_after_worker_recovers(self):
        result = server.submit("probe", timeout=0.01)
        self.assertFalse(result["ok"])
        with patch.object(server, "dispatch", side_effect=AssertionError("timed-out task must not execute")):
            server.drain()

    def test_restarting_worker_releases_old_queued_waiters(self):
        results = []
        thread = threading.Thread(target=lambda: results.append(server.submit("old_request", timeout=300)), daemon=True)
        thread.start()
        deadline = time.monotonic() + 1
        while server._TASK_QUEUE.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        server.reset_worker_failure()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0]["ok"])
        self.assertIn("旧请求已失效", results[0]["error"])

    def test_busy_worker_answers_heartbeat_without_queuing_behind_long_action(self):
        outgoing, incoming = socket.socketpair()
        try:
            server._active_task = server._Task("skin_weights_import_max", {})
            outgoing.sendall(b'{"action":"ping"}\n')
            with patch.object(server, "submit", side_effect=AssertionError("heartbeat must not queue")):
                server.handle_client(incoming)
            outgoing.settimeout(1)
            result = json.loads(outgoing.recv(4096))
            self.assertTrue(result["ok"])
            self.assertTrue(result["data"]["busy"])
            self.assertNotIn("is_main", result["data"])
        finally:
            outgoing.close()
            incoming.close()


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    print("PASS %d / %d" % (result.testsRun - len(result.failures) - len(result.errors), result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
