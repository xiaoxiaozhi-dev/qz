"""Protocol 3 server. Only the application timer touches Blender scene data."""

from concurrent.futures import Future, TimeoutError
import importlib
import json
from pathlib import Path
import runpy
import socket
import socketserver
import threading
import time

import blender_actions

BRIDGE_PROTOCOL = 3
BLENDER_PORT = 17025
TIMER_INTERVAL = 0.05
READY_WINDOW = 0.25
MAIN_THREAD_TIMEOUT = 2
LONG_ACTION_TIMEOUT = 600
MAX_REQUEST_BYTES = 65536
DEFAULT_USER_ROOT = Path(__file__).resolve().parents[1] / "user_plugins" / "blender"
ACTIONS = {}
_bridge = None


def reload_actions():
    """Read current action source and publish the registry after a successful scan."""
    global ACTIONS
    importlib.invalidate_caches()
    actions = {"reload_actions": reload_actions}
    folder = Path(blender_actions.__file__).parent
    for source in sorted(folder.glob("*.py")):
        if source.name.startswith("_"):
            continue
        exports = runpy.run_path(str(source), run_name=f"blender_actions.{source.stem}").get("ACTIONS", {})
        for name, action in exports.items():
            if name in actions or name in ("ping", "bridge_connect", "bridge_disconnect") or not callable(action):
                raise ValueError(f"无效或重复的 Blender 动作：{name}")
            actions[name] = action
    ACTIONS = actions
    return {"actions": sorted(actions)}


def _ok(**data):
    return {"ok": True, "data": data}


def _error(message):
    return {"ok": False, "error": message}


class _RequestHandler(socketserver.StreamRequestHandler):
    timeout = 2

    def handle(self):
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except OSError:
            return
        if not line:
            return
        try:
            if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
                raise ValueError("无效的桥接请求长度")
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict) or not isinstance(request.get("action"), str):
                raise ValueError("无效的桥接请求")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("无效的动作参数")
            response = self.server.dispatch(request["action"], params)
        except Exception as exc:
            response = _error(str(exc))
        try:
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Task:
    def __init__(self, action, params):
        self.action = action
        self.params = params
        self.future = Future()
        self.started = threading.Event()


class _Bridge(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def __init__(self, port, version, user_root=None):
        self.version = version
        self.user_root = Path(user_root or DEFAULT_USER_ROOT).resolve()
        self.lock = threading.Lock()
        self.stopping = False
        self.last_tick = float("-inf")
        self.interface_locked = False
        self.client_id = None
        self.task = None
        super().__init__(("127.0.0.1", port), _RequestHandler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def _ready(self):
        return not self.interface_locked and time.monotonic() - self.last_tick <= READY_WINDOW

    def dispatch(self, action, params):
        with self.lock:
            if self.stopping:
                return _error("Blender 桥接正在关闭")
            if action == "ping":
                busy = self.task is not None and self.task.future.running()
                return _ok(version=self.version, bridge_protocol=BRIDGE_PROTOCOL,
                           busy=busy, main_thread_ready=self._ready() and not busy)
            if action not in ("bridge_connect", "bridge_disconnect") and action not in ACTIONS:
                return _error("unknown action: " + action)
            if self.task is not None:
                return _error("Blender 桥接繁忙，请稍后重试")
            if not self._ready():
                return _error("Blender 主线程等待响应，请恢复后重试")
            if action == "bridge_connect":
                client_id = params.get("client_id")
                if not isinstance(client_id, str) or not client_id:
                    return _error("缺少中台连接标识")
                changed = self.client_id != client_id
                self.client_id = client_id
                if changed:
                    print("[qz-bridge] Blender 已连接中台")
                return _ok(connected=True)
            if action == "bridge_disconnect":
                self.stopping = True
            else:
                if action == "user_script_blender":
                    params["user_root"] = self.user_root
                long_running = (action in {"fbx_export_maya", "fbx_receive_maya", "skin_weights_receive_maya",
                                           "vertex_positions_receive_maya"}
                                or (action == "user_script_blender" and params.get("op", "run") == "run"))
                task = self.task = _Task(ACTIONS[action], params)

        if action == "bridge_disconnect":
            self.shutdown()
            self.server_close()
            return _ok(stopped=True)

        if long_running and not task.started.wait(MAIN_THREAD_TIMEOUT):
            with self.lock:
                if task.future.cancel():
                    self.task = None
                    return _error("等待 Blender 主线程超时")
        try:
            return task.future.result(timeout=LONG_ACTION_TIMEOUT if long_running else MAIN_THREAD_TIMEOUT)
        except TimeoutError:
            with self.lock:
                if task.future.cancel():
                    self.task = None
            message = ("等待 Blender 任务完成超时，任务可能仍在执行，请检查 Blender 中的结果"
                       if long_running else "等待 Blender 主线程超时")
            return _error(message)

    def tick(self):
        import bpy

        with self.lock:
            if self.stopping:
                return None
            manager = bpy.context.window_manager
            self.interface_locked = bool(manager and manager.is_interface_locked)
            self.last_tick = time.monotonic()
            if self.interface_locked or self.task is None:
                return TIMER_INTERVAL
            task = self.task
            task.future.set_running_or_notify_cancel()
            task.started.set()

        try:
            response = _ok(**task.action(**task.params))
        except Exception as exc:
            response = _error(str(exc))
        finally:
            with self.lock:
                self.task = None
                self.last_tick = time.monotonic()
        task.future.set_result(response)
        return TIMER_INTERVAL


def run(port=BLENDER_PORT, user_root=None):
    """Start listening from Blender's main thread. Repeated startup is safe."""
    global _bridge
    import bpy

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("请在 Blender 主线程启动桥接")
    if _bridge is not None and not _bridge.stopping:
        if port != _bridge.server_address[1]:
            raise RuntimeError("Blender 桥接已在其他端口运行，请先断开")
        return

    reload_actions()
    bridge = _Bridge(port, bpy.app.version_string, user_root)
    tick = bridge.tick
    try:
        bpy.app.timers.register(tick, persistent=True)
        thread = threading.Thread(target=bridge.serve_forever, args=(0.05,), daemon=True)
        thread.start()
    except Exception:
        if bpy.app.timers.is_registered(tick):
            bpy.app.timers.unregister(tick)
        bridge.server_close()
        raise
    _bridge = bridge
    print("[qz-bridge] Blender 桥接已就绪，等待中台确认连接")
