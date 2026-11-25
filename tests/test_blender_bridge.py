"""Blender protocol over a real TCP port and the Qt selection card."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from blender_bridge import server
from dcc_bridge import DccClient
import paths
from ui import blender_setup, plugin_loader


def wait_until(predicate, pump=lambda: time.sleep(.005)):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for Blender bridge test")
        pump()


class BlenderBridgeTests(unittest.TestCase):
    def test_startup_code_uses_project_directory_and_configured_port(self):
        user_root = Path.cwd() / "中文 路径's" / "user_plugins" / "blender"
        with (patch.object(blender_setup.paths, 'project_dir', return_value="C:\\中文 路径's\\qz"),
              patch.object(blender_setup.paths, 'user_plugins_dir', return_value=str(user_root)),
              patch.object(sys, 'path', list(sys.path)),
              patch.object(server, 'run') as run):
            code = blender_setup.snippet(27125)
            self.assertEqual(len(code.splitlines()), 1)
            exec(compile(code, '<blender-console>', 'single'), {})
            run.assert_called_once_with(port=27125, user_root=user_root.as_posix())
            self.assertEqual(sys.path[0], "C:/中文 路径's/qz")

    def test_reload_reads_latest_action_source_and_leaves_registry_intact_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "example.py"
            with (patch.object(server.blender_actions, "__file__", str(source.with_name("__init__.py"))),
                  patch.object(server, "ACTIONS")):
                for value in (1, 2):
                    source.write_text(f"ACTIONS = {{'example': lambda: {{'value': {value}}}}}\n", encoding="utf-8")
                    self.assertIn("example", server.reload_actions()["actions"])
                    self.assertEqual(server.ACTIONS["example"](), {"value": value})
                source.write_text("ACTIONS = {", encoding="utf-8")
                with self.assertRaises(SyntaxError):
                    server.reload_actions()
                self.assertEqual(server.ACTIONS["example"](), {"value": 2})
                source.unlink()
                self.assertEqual(server.reload_actions()["actions"], ["reload_actions"])

    def test_selection_waiting_busy_disconnect_and_restart(self):
        server.reload_actions()
        main = threading.current_thread()
        layer = SimpleNamespace()

        def selected(view_layer):
            self.assertIs(threading.current_thread(), main)
            self.assertIs(view_layer, layer)
            return True

        layer.objects = [SimpleNamespace(name="立方体", select_get=selected)]
        manager = SimpleNamespace(is_interface_locked=False,
                                  windows=[SimpleNamespace(view_layer=layer)])
        bpy = SimpleNamespace(context=SimpleNamespace(view_layer=None, window_manager=manager))
        with patch.dict(sys.modules, bpy=bpy):
            port = 0
            for _ in range(2):
                bridge = server._Bridge(port, "5.2.4")
                port = bridge.server_address[1]
                thread = threading.Thread(target=bridge.serve_forever, args=(.01,), daemon=True)
                thread.start()
                client = DccClient.for_dcc("blender", port=port)
                try:
                    self.assertLess(0, bridge.tick())
                    self.assertTrue(client.call("bridge_connect", client_id="test")["ok"])
                    with ThreadPoolExecutor(1) as worker:
                        request = worker.submit(client.call, "read_selection")
                        wait_until(lambda: bridge.task is not None)
                        bridge.tick()
                        self.assertEqual(request.result()["data"], {"count": 1, "names": ["立方体"]})
                    manager.is_interface_locked = True
                    bridge.tick()
                    self.assertEqual(client.probe()["state"], "waiting")
                    self.assertEqual(client.last_ping_data["version"], "5.2.4")
                    self.assertFalse(client.call("read_selection")["ok"])
                    self.assertFalse(client.call("bridge_disconnect")["ok"])
                    manager.is_interface_locked = False
                    bridge.tick()

                    def busy_action():
                        self.assertEqual(client.probe()["state"], "busy")
                        self.assertFalse(client.call("bridge_disconnect")["ok"])
                        return {"done": True}

                    with patch.dict(server.ACTIONS, test_busy=busy_action), ThreadPoolExecutor(1) as worker:
                        request = worker.submit(client.call, "test_busy")
                        wait_until(lambda: bridge.task is not None)
                        bridge.tick()
                        self.assertEqual(request.result()["data"], {"done": True})

                    def slow_action():
                        time.sleep(.25)
                        return {"count": 1}

                    with (patch.dict(server.ACTIONS, fbx_export_maya=slow_action,
                                     skin_weights_receive_maya=slow_action),
                          patch.object(server, "MAIN_THREAD_TIMEOUT", .1),
                          ThreadPoolExecutor(1) as worker):
                        for action in ("fbx_export_maya", "skin_weights_receive_maya"):
                            with self.subTest(action=action):
                                bridge.tick()
                                request = worker.submit(client.call, action)
                                wait_until(lambda: bridge.task is not None)
                                bridge.tick()
                                self.assertEqual(request.result()["data"], {"count": 1})
                    self.assertTrue(client.call("bridge_disconnect")["data"]["stopped"])
                    self.assertEqual(client.probe()["state"], "offline")
                    self.assertIsNone(bridge.tick())
                finally:
                    bridge.shutdown()
                    bridge.server_close()
                    thread.join(2)

    def test_settings_cards_call_blender_and_log_results(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        plugins = {p.id: p for p in plugin_loader.scan(paths.plugins_dir()) if p.dcc == "blender"}
        messages = []
        log = SimpleNamespace(info=messages.append, error=messages.append, warn=messages.append)
        responses = {
            "ping": {"ok": True, "data": {"version": "5.2.4", "busy": True}},
            "reload_actions": {"ok": True, "data": {"actions": ["user_script_blender"]}},
        }
        client = SimpleNamespace(call=lambda action, **params: responses[action])
        for plugin_id, expected in (("ping", "5.2.4 · 繁忙"), ("reload", "user_script_blender")):
            info = plugins[plugin_id]
            self.assertEqual(info.category, "settings")
            card = info.load_factory()(client, log)
            card.show()
            messages.clear()
            QtTest.QTest.mouseClick(card.button, QtCore.Qt.LeftButton)
            wait_until(lambda: messages, lambda: QtTest.QTest.qWait(5))
            self.assertIn(expected, messages[0])
            card.close()
        app.processEvents()

    def test_auto_setup_card_installs_updates_and_removes_the_current_profile_script(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        info = next(p for p in plugin_loader.scan(paths.plugins_dir()) if p.key == ("blender", "auto_setup"))
        self.assertEqual(info.category, "settings")
        messages = []
        log = SimpleNamespace(info=messages.append, error=messages.append)
        with tempfile.TemporaryDirectory() as directory:
            startup = Path(directory) / "profile" / "scripts" / "startup"
            calls = []

            def call(action, **params):
                calls.append(action)
                return {"ok": True, "data": {"path": str(startup)}}

            client = SimpleNamespace(connected=False, port=27125, call=call)
            card = info.load_factory()(client, log)
            card.show()
            QtTest.QTest.mouseClick(card.install_button, QtCore.Qt.LeftButton)
            self.assertIn("请先连接 Blender", messages[-1])
            self.assertFalse(startup.exists())
            self.assertEqual(calls, [])
            client.connected = True
            QtTest.QTest.mouseClick(card.install_button, QtCore.Qt.LeftButton)
            script = startup / blender_setup.SCRIPT_NAME
            first = script.read_text(encoding="utf-8")
            self.assertTrue(first.startswith(blender_setup.SCRIPT_MARKER))
            self.assertIn("port=27125", first)
            client.port = 27126
            QtTest.QTest.mouseClick(card.install_button, QtCore.Qt.LeftButton)
            self.assertIn("port=27126", script.read_text(encoding="utf-8"))
            other = startup / "other_tool.py"
            other.write_text("# another user script", encoding="utf-8")
            QtTest.QTest.mouseClick(card.remove_button, QtCore.Qt.LeftButton)
            self.assertFalse(script.exists())
            self.assertIn("已卸载", messages[-1])
            self.assertEqual(other.read_text(encoding="utf-8"), "# another user script")
            self.assertEqual(calls, ["get_startup_dir"] * 3)
            card.close()
        app.processEvents()

    def test_auto_setup_preserves_an_unrelated_script_with_the_same_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            startup = Path(directory) / "startup"
            startup.mkdir()
            self.assertIsNone(blender_setup.remove_script(startup))
            script = startup / blender_setup.SCRIPT_NAME
            foreign = b"# another tool\r\n"
            script.write_bytes(foreign)
            for change in (blender_setup.write_script, blender_setup.remove_script):
                with self.assertRaises(FileExistsError):
                    change(startup)
                self.assertEqual(script.read_bytes(), foreign)


if __name__ == "__main__":
    unittest.main()
