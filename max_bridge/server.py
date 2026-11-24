# -*- coding: utf-8 -*-
"""
max_bridge/server.py
3ds Max 端桥接服务器。

⚠️ 与 maya_bridge/server.py 的关键差异 ⚠️
Max 的 pymxs **只能在主线程调用**，且**后台线程调 pymxs 会破坏 Max 内部状态**
（实测报 DirtyNotificationEventMonitor call depth count < 0）。
所以这里的分工是硬性的：

    socket 线程（本模块的 start_server）只做：
        accept / recv / send / JSON 解析 / 队列投递
        【绝不 import 或调用 pymxs】

    主线程（worker.py，被 MAXScript timer 拉起）做：
        从队列取任务 → 调 action → 回填结果

跨线程通信靠 queue.Queue + threading.Event，不用共享内存对象。

实测依据见 docs/max_bridge_design.md
"""

from __future__ import unicode_literals

import json
import os
try:
    import queue
except ImportError:  # Python 2.7
    import Queue as queue
import socket
import sys
import tempfile
import threading
import time

# 从 __file__ 推导项目根目录
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_PORT = 17024
BRIDGE_PROTOCOL = 3
_server_lock = threading.RLock()
_business_requests = 0
_disconnecting = False
_session_id = None
_server_thread = None

# 项目目录进 path，让 max_actions / max_bridge 能被 import
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# ── 动作注册表 ──────────────────────────────────
# 注意：这个字典住在 server.py（主线程侧），不在 max_actions 包里。
# 所以 worker 清 max_actions.* 的模块缓存重扫时，字典本身毫发无损。
ACTIONS = {}
WORKER_FAILURE_PATH = os.path.join(tempfile.gettempdir(), "qz-max-worker-%d.error" % os.getpid())
SUPPORTS_WORKER_FAILURE = True
_active_task = None
_heartbeat_task = None
_heartbeat_data = {}
HEARTBEAT_GRACE = .2


def reset_worker_failure():
    """Only called on the main thread before rebuilding the rollout."""
    global _active_task, _heartbeat_task
    if os.path.isfile(WORKER_FAILURE_PATH):
        os.remove(WORKER_FAILURE_PATH)
    while True:
        try:
            task = _TASK_QUEUE.get_nowait()
        except queue.Empty:
            break
        task.cancel("Max 桥接已重新启动，旧请求已失效；已开始的操作请检查场景结果")
    if _active_task is not None:
        _active_task.cancel("Max 桥接已重新启动，请检查上次操作结果")
    _active_task = None
    _heartbeat_task = None
    state = getattr(sys, "_qz_worker_state", None)
    if state is not None:
        state["busy"] = False


def worker_failure(task=None):
    # MAXScript writes this marker if ExecuteFile fails outside Python.
    # Socket threads must never query pymxs to inspect the rollout.
    if os.path.isfile(WORKER_FAILURE_PATH):
        message = "Max 主线程 worker 已异常停止，请重新启动桥接；已开始的导入可能部分更新，请检查目标模型，勿自动重试"
        if task is not None and task.progress:
            message += "；最后执行阶段：" + task.progress
        return message
    return ""


def set_progress(message):
    if _active_task is not None:
        _active_task.progress = message


def register_action(name):
    def decorator(fn):
        ACTIONS[name] = fn
        return fn
    return decorator


def dispatch(action, **kwargs):
    """在【主线程】执行 action。本函数只允许被 worker 调用。"""
    global _session_id
    if action == 'bridge_connect':
        session = kwargs.get('client_id', '')
        if session != _session_id:
            _session_id = session
            notify('已连接 Qz 中台')
        return {'ok': True, 'data': {'connected': True, 'bridge_protocol': BRIDGE_PROTOCOL}}
    if action == 'bridge_disconnect':
        _stop_worker()
        stop()
        notify('已断开 Qz 中台，桥接端口已关闭')
        return {'ok': True, 'data': {'stopped': True}}
    if action not in ACTIONS:
        return {"ok": False, "error": "unknown action: %s" % action}
    try:
        result = ACTIONS[action](**kwargs)
        return {"ok": True, "data": result}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()}


# ── 跨线程任务队列 ──────────────────────────────
# socket 线程投递 (action, kwargs, result_slot)，worker 在主线程消费
_TASK_QUEUE = queue.Queue()


class _Task(object):
    """一次待主线程执行的调用。"""

    __slots__ = ("action", "kwargs", "_event", "result", "done", "cancelled", "progress", "_lock", "created")

    def __init__(self, action, kwargs):
        self.action = action
        self.kwargs = kwargs
        self._event = threading.Event()
        self.result = None
        self.done = False
        self.cancelled = False
        self.progress = ""
        self._lock = threading.Lock()
        self.created = time.time()

    def set_result(self, value):
        with self._lock:
            if self.done:
                return
            self.result = value
            self.done = True
            self._event.set()

    def cancel(self, message):
        with self._lock:
            self.cancelled = True
            if not self.done:
                self.result = {"ok": False, "error": message}
                self.done = True
                self._event.set()

    def begin(self):
        with self._lock:
            return not self.cancelled and not self.done

    def wait(self, timeout):
        """socket 线程等主线程执行完。超时返回 None（不阻塞中台太久）。"""
        return self._event.wait(timeout)


def submit(action, kwargs=None, timeout=10.0):
    """投递一个任务到主线程并等待结果。

    只能在【socket 线程】调用 —— 这里不碰任何 pymxs，绝对安全。
    """
    task = _Task(action, kwargs or {})
    error = worker_failure()
    if error:
        return {"ok": False, "error": error}
    _TASK_QUEUE.put(task)
    deadline = time.time() + timeout
    while True:
        if task.done:
            return task.result
        error = worker_failure(task)
        if error:
            task.cancel(error)
            return task.result
        remaining = deadline - time.time()
        if remaining <= 0:
            task.cancel("timeout: 主线程 %s 秒内未完成该任务；已开始的操作不会自动取消" % timeout)
            return task.result
        task.wait(min(0.1, remaining))


def pending_count():
    return _TASK_QUEUE.qsize()


# socket 线程等待主线程执行的上限（秒）
REQUEST_TIMEOUT = 10.0
SUPPORTS_EXECUTION_TIMEOUT = True


def _heartbeat():
    """Socket-only reply; a single read-only probe checks main-thread readiness."""
    global _heartbeat_task
    error = worker_failure()
    if error:
        return {"ok": False, "error": error,
                "data": {"bridge_alive": True, "worker_failed": True,
                         "bridge_protocol": BRIDGE_PROTOCOL}}
    with _server_lock:
        active = _active_task
        busy = active is not None and active.action not in ('ping', 'bridge_connect')
        task = _heartbeat_task
        if task is not None and task.done and task.result and task.result.get('ok'):
            _heartbeat_data.update(task.result.get('data') or {})
        if not busy and (task is None or task.done):
            task = _Task('ping', {})
            _heartbeat_task = task
            _TASK_QUEUE.put(task)
    if not busy:
        task.wait(HEARTBEAT_GRACE)
    with _server_lock:
        data = dict(_heartbeat_data)
        result = task.result if task is not None and task.done else None
        if result and result.get('ok'):
            data.update(result.get('data') or {})
            _heartbeat_data.update(data)
    if result is not None and not result.get('ok'):
        return dict(result, data=dict(data, bridge_alive=True, bridge_protocol=BRIDGE_PROTOCOL))
    data.update(pong=True, bridge_alive=True, bridge_protocol=BRIDGE_PROTOCOL,
                main_thread_ready=not busy and result is not None,
                busy=bool(busy or _business_requests))
    if not data['main_thread_ready']:
        data.pop('is_main', None)
        data.pop('thread', None)
    return {"ok": True, "data": data}


# ── 客户端处理（socket 线程）────────────────────
def handle_client(conn):
    global _business_requests, _disconnecting
    business = False
    disconnect = False
    try:
        conn.settimeout(5)
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk

        if not data:
            return

        payload = json.loads(data.decode("utf-8").strip())
        action = payload.get("action")
        params = payload.get("params", {})
        with _server_lock:
            if action == 'bridge_disconnect':
                active_business = (_active_task is not None and
                                   _active_task.action not in ('ping', 'bridge_connect'))
                if _business_requests or active_business or _disconnecting:
                    raise RuntimeError('桥接仍有任务执行中，请完成后再断开')
                if (_heartbeat_task is not None and not _heartbeat_task.done
                        and time.time() - _heartbeat_task.created >= HEARTBEAT_GRACE):
                    raise RuntimeError('Max 主线程正在等待响应，请恢复后再断开')
                _disconnecting = True
                disconnect = True
            elif _disconnecting:
                raise RuntimeError('桥接正在断开，请稍后重新连接')
            elif action not in ('ping', 'bridge_connect'):
                business = True
                _business_requests += 1

        # 连接超时与执行等待分开；旧请求仍采用 REQUEST_TIMEOUT。
        execution_timeout = payload.get("execution_timeout", REQUEST_TIMEOUT)
        if isinstance(execution_timeout, bool):
            raise ValueError("invalid execution_timeout")
        execution_timeout = float(execution_timeout)
        if not 0 < execution_timeout <= 3600:
            raise ValueError("execution_timeout must be within (0, 3600]")
        # ★ 只解析 JSON、投递队列，不碰 pymxs ★
        if action == "ping":
            result = _heartbeat()
        else:
            result = submit(action, params, timeout=execution_timeout)

        response = json.dumps(result, ensure_ascii=False) + "\n"
        conn.sendall(response.encode("utf-8"))
        if disconnect:
            try:
                conn.settimeout(1)
                conn.recv(1)
            except (OSError, socket.error):
                pass

    except Exception as e:
        try:
            try:
                message = type(u'')(e)
            except UnicodeError:
                message = repr(e)
            err = json.dumps({"ok": False, "error": message},
                             ensure_ascii=False) + "\n"
            conn.sendall(err.encode("utf-8"))
        except Exception:
            pass
    finally:
        with _server_lock:
            if business:
                _business_requests -= 1
            if disconnect and _server_sock is not None:
                _disconnecting = False
        try:
            conn.close()
        except Exception:
            pass


# ── 启动服务器（socket 线程）────────────────────
_server_sock = None


def _log(msg):
    """Write once to the Python Listener without touching pymxs on socket threads."""
    # This helper also runs on socket threads: never access pymxs here.
    try:
        print('[qz-bridge] ' + msg)
    except Exception:
        pass


def notify(message):
    """Main thread only; show a temporary status prompt without modal alerts."""
    _log(message)
    try:
        import pymxs
        pymxs.runtime.displayTempPrompt(message, 5000)
    except Exception:
        pass


def _stop_worker():
    try:
        import pymxs
    except ImportError:
        return
    # Do not destroy a rollout from inside its own timer callback.
    pymxs.runtime.execute('global qzBridgeEnabled = false\n'
                         'if qzBridgeRollout != undefined do qzBridgeRollout.tmrTick.active = false')


def _bind_server(port):
    """独占监听端口并 serve。

    ⚠️ 端口占用这个坑比想象中难缠 ⚠️
    Windows 的 socket 选项有两种互斥语义：
      - `SO_REUSEADDR` 允许**抢占**已 listen 的端口。bind 成功、listen 成功、
        日志照样打印 listening，但新 socket 根本不服务，新连接全被旧 socket 吃掉 ——
        表现是「日志一切正常，但中台永远连不上」，**没有任何报错**。
      - `SO_EXCLUSIVEADDRUSE` 则相反，端口被占时 bind 直接失败。

    我就栽在第一种上：一个测试脚本的残留 socket 占着 17024，
    Max bridge 照样"启动成功"，中台连过来永远超时，排查了很久。

    结论：**不要用 SO_REUSEADDR**。独占模式下端口被占会明确报 10048，
    比静默抢占好排查得多。重复启动走 run() 的幂等分支，根本不会走到这里。

    （别再试图"先 REUSEADDR 再用 EXCLUSIVE 探测" —— 两者在 Windows 上互斥，
      探测必然失败，会变成永远误报"端口被占"。）
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 独占：端口被占时 bind 直接抛 10048，不会静默抢占
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    except (AttributeError, OSError, socket.error):
        pass    # 非 Windows / 内核不支持，退回默认独占行为

    try:
        server.bind(("127.0.0.1", port))
    except (OSError, socket.error) as e:
        server.close()
        raise RuntimeError(
            "端口 %d 已被占用（%s）—— bridge 没有启动。"
            "请关掉那个 Max 窗口，或用 netstat -ano | findstr %d 找到 PID 后结束进程。"
            % (port, e, port))
    server.listen(5)
    server.settimeout(.2)

    _log("Max Bridge listening on port %d" % port)
    return server


def _open_server(port):
    global _server_sock, _disconnecting, _session_id
    with _server_lock:
        if _server_sock is not None:
            return _server_sock, False
        server = _bind_server(port)
        _server_sock = server
        _disconnecting = False
        _session_id = None
        return server, True


def _serve(server):
    global _server_sock, _server_thread
    _server_thread = threading.current_thread()
    try:
        while _server_sock is server:
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            t = threading.Thread(target=handle_client, args=(conn,))
            t.daemon = True
            t.start()
    finally:
        with _server_lock:
            if _server_sock is server:
                _server_sock = None
        server.close()


def start_server(port=MAX_PORT):
    server, created = _open_server(port)
    if created:
        _serve(server)


def stop():
    global _server_sock, _session_id, _disconnecting, _heartbeat_task
    with _server_lock:
        server = _server_sock
        _server_sock = None
        _session_id = None
        _disconnecting = True
        _heartbeat_task = None
        _heartbeat_data.clear()
    if server is not None:
        try:
            server.shutdown(socket.SHUT_RDWR)
        except (OSError, socket.error):
            pass
        server.close()
    thread = _server_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(1)
    while True:
        try:
            task = _TASK_QUEUE.get_nowait()
        except queue.Empty:
            break
        task.cancel('Max 桥接已断开，请重新连接后再执行')


# ── action 重扫 ─────────────────────────────────
def reload_actions():
    """重新扫描 max_actions/ 并注册所有 action。

    与 Maya 侧同样的原因：`max_actions/__init__.py` 用 glob 在 import 时扫一次，
    之后 Python 缓存了模块对象，运行期新增的 action 文件扫不到 →
    中台报 "unknown action"，只能重启 Max。

    ACTIONS 在本模块里，不在 max_actions 包中，所以清缓存不会把注册表清掉。
    """
    import importlib

    for name in list(sys.modules.keys()):
        if name == "max_actions" or name.startswith("max_actions."):
            del sys.modules[name]

    invalidate = getattr(importlib, "invalidate_caches", None)
    if invalidate is not None:
        invalidate()
    import max_actions  # noqa: F401  重新执行 __init__，触发 glob

    return sorted(ACTIONS.keys())


# ── 主线程侧：给 worker 用的消费函数 ────────────
def drain(max_items=64):
    """在【主线程】调用：把队列里的任务取出来执行。

    由 worker.py 每 100ms 调一次。返回处理了多少条。
    """
    global _active_task
    handled = 0
    while handled < max_items:
        try:
            task = _TASK_QUEUE.get_nowait()
        except queue.Empty:
            break
        if not task.begin():
            handled += 1
            continue
        _active_task = task
        try:
            task.set_result(dispatch(task.action, **task.kwargs))
        except Exception:
            task.set_result({"ok": False, "error": "dispatch 内部异常"})
        finally:
            _active_task = None
        handled += 1
    return handled


# ── 入口（在 Max 里 import 后调用）──────────────
def run(port=MAX_PORT):
    """Max 端启动入口。重复调用安全：已监听则只重扫 action。"""
    names = reload_actions()
    server, created = _open_server(port)
    if created:
        t = threading.Thread(target=_serve, args=(server,))
        t.daemon = True
        t.start()
    _log("Max Bridge started! Actions: %s" % names)
    return names
