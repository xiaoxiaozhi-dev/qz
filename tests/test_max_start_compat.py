# -*- coding: utf-8 -*-
"""Max 启动链在 Python 2.7 / 3 的运行时回归，不启动 Max 或监听真实端口。

Python 2 宿主通过 QZ_MAX_SNIPPET_TEMPLATE 读取由中台生成的模板，
避免为了验证 Max 端而在 Python 2 内导入使用 Python 3 的中台模块。
"""
from __future__ import print_function, unicode_literals

import contextlib
import io
import json
import os
import runpy
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def snippet_for(root):
    fixture = os.environ.get("QZ_MAX_SNIPPET_TEMPLATE")
    if fixture:
        with io.open(fixture, encoding="utf-8") as source:
            template = json.load(source)
    else:
        from ui import max_setup
        original = max_setup.paths.project_dir
        try:
            max_setup.paths.project_dir = lambda: "__QZ_TEST_ROOT__"
            template = max_setup.snippet()
        finally:
            max_setup.paths.project_dir = original
    def literal(value):
        return "u" + json.dumps(value, ensure_ascii=True)
    start = os.path.join(root, "max_bridge", "start_max.py").replace("\\", "/")
    return template.replace("u'__QZ_TEST_ROOT__/max_bridge/start_max.py'", literal(start)).replace(
        "u'__QZ_TEST_ROOT__'", literal(root))


@contextlib.contextmanager
def fake_runtime(server):
    import max_bridge
    messages, executed = [], []
    runtime = types.ModuleType(str("pymxs"))
    runtime.runtime = types.ModuleType(str("test_runtime"))
    runtime.runtime.format = lambda *args: messages.append(args)
    runtime.runtime.execute = lambda text: executed.append(text)
    ctypes = types.ModuleType(str("ctypes"))
    ctypes.windll = types.ModuleType(str("windll"))
    ctypes.windll.user32 = types.ModuleType(str("user32"))
    ctypes.windll.user32.FindWindowW = lambda *args: 0
    mapping = {"max_bridge.server": server, "pymxs": runtime, "ctypes": ctypes}
    previous = {name: sys.modules.get(name) for name in mapping}
    previous_server = getattr(max_bridge, "server", None)
    sys.modules.update(mapping)
    max_bridge.server = server
    try:
        yield messages, executed
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_server is None:
            del max_bridge.server
        else:
            max_bridge.server = previous_server


class MaxStartCompatTests(unittest.TestCase):
    def test_worker_rejects_nested_execute_file_until_outer_action_finishes(self):
        import max_bridge.server as server
        import max_bridge.worker as worker
        state = worker._state()
        saved, original_drain = dict(state), server.drain
        calls = []
        path = os.path.join(ROOT, "max_bridge", "worker.py")
        with io.open(path, encoding="ascii") as source:
            nested = compile(source.read(), path, "exec")
        def drain(max_items):
            calls.append(max_items)
            exec(nested, {})
            self.assertTrue(state["busy"])
            return 1
        try:
            state["busy"] = False
            before = state.get("reentrant_skips", 0)
            server.drain = drain
            self.assertEqual(worker.pump(max_items=3), 1)
            self.assertEqual(calls, [3])
            self.assertFalse(state["busy"])
            self.assertEqual(state["reentrant_skips"], before + 1)
        finally:
            server.drain = original_drain
            state.clear()
            state.update(saved)

    def test_worker_error_records_traceback_and_releases_busy_flag(self):
        import max_bridge.server as server
        import max_bridge.worker as worker
        state = worker._state()
        saved, original_drain = dict(state), server.drain
        def fail(max_items):
            raise ValueError("worker regression probe")
        try:
            state["busy"] = False
            server.drain = fail
            self.assertEqual(worker.pump(), 0)
            self.assertFalse(state["busy"])
            self.assertIn("Traceback", state["last_error"])
            self.assertIn("worker regression probe", state["last_error"])
            server.drain = lambda max_items: 1
            self.assertEqual(worker.pump(), 1)
        finally:
            server.drain = original_drain
            state.clear()
            state.update(saved)

    def test_worker_supports_max_execute_file_unicode_source(self):
        path = os.path.join(ROOT, "max_bridge", "worker.py")
        with io.open(path, encoding="ascii") as source:
            text = source.read()
        compiled = compile(text, path, "exec")
        exec(compiled, {"__name__": "__main__"})
        self.assertTrue(hasattr(sys, "_qz_worker_state"))

    def test_all_max_modules_compile_with_current_interpreter(self):
        for folder in ("max_bridge", "max_actions"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    path = os.path.join(ROOT, folder, name)
                    with io.open(path, "rb") as source:
                        compile(source.read(), path, "exec")

    def test_real_snippet_starts_once_and_restarts_without_duplicate_server(self):
        server = types.ModuleType(str("max_bridge.server"))
        server.MAX_PORT = 17024
        server._server_sock = None
        server.SUPPORTS_WORKER_FAILURE = True
        server.BRIDGE_PROTOCOL = 3
        server.notify = lambda message: None
        server.WORKER_FAILURE_PATH = os.path.join(tempfile.gettempdir(), "qz-test-worker.error")
        server.reset_worker_failure = lambda: None
        calls = []
        def run():
            calls.append("run")
            server._server_sock = object()
            return ["ping"]
        def reload_actions():
            calls.append("reload")
            return ["ping"]
        server.run, server.reload_actions = run, reload_actions
        with fake_runtime(server) as (messages, executed):
            code = snippet_for(ROOT)
            self.assertEqual(len(code.splitlines()), 1)
            snippet = compile(code, "<max-start-snippet>", "single")
            for _ in range(2):
                namespace = {}
                exec(snippet, namespace)
                self.assertIn("_qz_bridge", namespace)
            self.assertEqual(calls, ["run", "reload"])
            self.assertEqual(len(executed), 2)
            self.assertTrue(all("@WORKER@" not in text and "@PROJECT@" not in text and "@FAILURE@" not in text
                                for text in executed))
            self.assertIn(ROOT.replace("\\", "/"), executed[0])

    def test_snippet_handles_unicode_spaces_and_quotes_in_project_path(self):
        temporary = tempfile.mkdtemp(prefix="qz_max_unicode_")
        original_path = list(sys.path)
        try:
            folder = os.path.join(temporary, "中文 路径's")
            os.makedirs(os.path.join(folder, "max_bridge"))
            target = os.path.join(folder, "max_bridge", "start_max.py")
            with io.open(target, "w", encoding="utf-8") as script:
                script.write("import sys\nsys._qz_start_probe = (__file__, __name__)\n")
            exec(compile(snippet_for(folder), "<unicode-snippet>", "single"), {})
            actual = sys._qz_start_probe[0]
            if sys.version_info[0] < 3 and isinstance(actual, bytes):
                actual = actual.decode(sys.getfilesystemencoding() or "mbcs")
            self.assertEqual(os.path.normpath(actual), os.path.normpath(target))
            self.assertEqual(sys._qz_start_probe[1], "__main__")
        finally:
            sys.path[:] = original_path
            if hasattr(sys, "_qz_start_probe"):
                del sys._qz_start_probe
            shutil.rmtree(temporary)

    def test_real_server_reload_daemon_and_worker_ping(self):
        import max_bridge.server as server
        import max_bridge.worker as worker
        original_thread = server.threading.Thread
        original_log = server._log
        original_socket = server._server_sock
        threads = []
        class Thread:
            def __init__(self, target, args=()):
                self.target, self.daemon, self.started = target, False, False
                threads.append(self)
            def start(self):
                self.started = True
        try:
            server.threading.Thread = Thread
            server._log = lambda *args: None
            server._server_sock = None
            names = server.run(port=0)
            self.assertIn("ping", names)
            self.assertIn("reload_actions", names)
            self.assertEqual(len(threads), 1)
            self.assertTrue(threads[0].daemon and threads[0].started)
            task = server._Task("ping", {})
            server._TASK_QUEUE.put(task)
            self.assertEqual(worker.pump(max_items=1), 1)
            self.assertTrue(task.done)
            self.assertTrue(task.result["ok"])
            self.assertTrue(task.result["data"]["is_main"])
        finally:
            server.threading.Thread = original_thread
            server._log = original_log
            if server._server_sock is not None and server._server_sock is not original_socket:
                server._server_sock.close()
            server._server_sock = original_socket

    def test_json_request_keeps_explicit_and_default_wait_times(self):
        import max_bridge.server as server
        original_submit = server.submit
        waits = []
        class Connection:
            def __init__(self, payload):
                self.payload = json.dumps(payload).encode("utf-8") + b"\n"
                self.response = b""
            def recv(self, size):
                data, self.payload = self.payload, b""
                return data
            def sendall(self, data):
                self.response += data
            def close(self): pass
            def settimeout(self, value): pass
        def submit(action, params, timeout):
            waits.append(timeout)
            return {"ok": True, "data": "连接正常"}
        try:
            server.submit = submit
            for payload in ({"action": "business_probe"}, {"action": "business_probe", "execution_timeout": 300}):
                connection = Connection(payload)
                server.handle_client(connection)
                result = json.loads(connection.response.decode("utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"], "连接正常")
            self.assertEqual(waits, [10, 300])
        finally:
            server.submit = original_submit


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    if not result.testsRun:
        raise RuntimeError("No startup compatibility tests were collected")
    print("PASS %d / %d" % (result.testsRun-len(result.failures)-len(result.errors), result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
