"""
Maya Bridge Server
监听端口，接收 JSON 指令，主线程执行，返回结果。
"""

import socket
import threading
import json
import sys
import os
import time

# 从 __file__ 推导项目根目录
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAYA_PORT = 17023
BRIDGE_PROTOCOL = 3
_server_lock = threading.RLock()
_business_requests = 0
_disconnecting = False
_session_id = None
_server_thread = None
_heartbeat_lock = threading.Lock()
_heartbeat_probe = None
_heartbeat_data = {}
HEARTBEAT_GRACE = .2

# 把项目目录和 maya_core 加入 path
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)
maya_core_dir = os.path.join(PROJECT_DIR, "maya_core")
if maya_core_dir not in sys.path:
    sys.path.insert(0, maya_core_dir)

# ── 动作注册表 ──────────────────────────────────
ACTIONS = {}

def register_action(name):
    def decorator(fn):
        ACTIONS[name] = fn
        return fn
    return decorator

def dispatch(action, **kwargs):
    global _session_id
    if action == 'bridge_connect':
        session = kwargs.get('client_id', '')
        if session != _session_id:
            _session_id = session
            notify('已连接 Qz 中台')
        return {'ok': True, 'data': {'connected': True, 'bridge_protocol': BRIDGE_PROTOCOL}}
    if action == 'bridge_disconnect':
        stop()
        notify('已断开 Qz 中台，桥接端口已关闭')
        return {'ok': True, 'data': {'stopped': True}}
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown action: {action}"}
    try:
        result = ACTIONS[action](**kwargs)
        return {"ok": True, "data": result}
    except Exception as e:
        import traceback
        return {"ok": False, "error": traceback.format_exc()}

# ── 主线程执行（Maya 内必需；非 Maya 环境直接同步执行以便本地测试）──
def _run_in_main(fn):
    try:
        import maya.utils
        return maya.utils.executeInMainThreadWithResult(fn)
    except ImportError:
        return fn()


def _heartbeat():
    """Socket liveness is independent of Maya APIs; only one main probe waits."""
    global _heartbeat_probe
    with _heartbeat_lock:
        probe = _heartbeat_probe
        if probe is None or probe['done'].is_set():
            probe = {'done': threading.Event(), 'result': None, 'created': time.monotonic()}
            _heartbeat_probe = probe

            def measure():
                try:
                    result = _run_in_main(lambda: dispatch('ping'))
                except Exception as exc:
                    result = {'ok': False, 'error': str(exc)}
                with _heartbeat_lock:
                    probe['result'] = result
                    if _heartbeat_probe is probe and result.get('ok'):
                        _heartbeat_data.update(result.get('data') or {})
                    probe['done'].set()

            thread = threading.Thread(target=measure, name='qz-maya-heartbeat')
            thread.daemon = True
            thread.start()
    probe['done'].wait(HEARTBEAT_GRACE)
    with _heartbeat_lock:
        result = probe['result']
        data = dict(_heartbeat_data)
    if result is not None and not result.get('ok'):
        return dict(result, data=dict(data, bridge_alive=True, bridge_protocol=BRIDGE_PROTOCOL))
    ready = result is not None
    data.update(pong=True, bridge_alive=True, bridge_protocol=BRIDGE_PROTOCOL,
                main_thread_ready=ready, busy=bool(_business_requests))
    return {'ok': True, 'data': data}

# ── 客户端处理 ──────────────────────────────────
def notify(message):
    """Called on Maya's main thread; a non-modal message never blocks startup."""
    print('[qz-bridge] ' + message)
    try:
        from maya import cmds
        if not cmds.about(batch=True):
            cmds.inViewMessage(amg=message, pos='topCenter', fade=True)
    except Exception:
        pass


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

        payload = json.loads(data.decode('utf-8').strip())
        action = payload.get("action")
        params = payload.get("params", {})

        with _server_lock:
            if action == 'bridge_disconnect':
                if _business_requests or _disconnecting:
                    raise RuntimeError('桥接仍有任务执行中，请完成后再断开')
                if (_heartbeat_probe is not None and not _heartbeat_probe['done'].is_set()
                        and time.monotonic() - _heartbeat_probe['created'] >= HEARTBEAT_GRACE):
                    raise RuntimeError('Maya 主线程正在等待响应，请恢复后再断开')
                _disconnecting = True
                disconnect = True
            elif _disconnecting:
                raise RuntimeError('桥接正在断开，请稍后重新连接')
            elif action not in ('ping', 'bridge_connect'):
                business = True
                _business_requests += 1
        result = (_heartbeat() if action == 'ping' else
                  _run_in_main(lambda: dispatch(action, **params)))

        response = json.dumps(result, ensure_ascii=False) + "\n"
        conn.sendall(response.encode('utf-8'))
        if disconnect:
            # Let the client close first so this listening port can be rebound.
            try:
                conn.settimeout(1)
                conn.recv(1)
            except OSError:
                pass

    except Exception as e:
        try:
            conn.sendall((json.dumps({"ok": False, "error": str(e)}) + '\n').encode('utf-8'))
        except:
            pass
    finally:
        with _server_lock:
            if business:
                _business_requests -= 1
            if disconnect and _server_sock is not None:
                _disconnecting = False
        conn.close()

# ── 启动服务器 ──────────────────────────────────
_server_sock = None      # 已监听的 socket，用于避免重复绑定端口


def _open_server(port):
    """独占监听端口并 serve。

    ⚠️ 不要用 SO_REUSEADDR ⚠️
    Windows 上它允许**抢占**已 listen 的端口：bind/listen 都成功、
    日志照样打印 listening，但新 socket 根本不服务，新连接全被旧 socket 吃掉 ——
    表现是「日志一切正常，但中台永远连不上」，没有任何报错。
    独占模式下端口被占会明确抛 10048，可排查得多。

    ⚠️ 同 DCC 多版本共用端口是**有意设计**（Maya 2024/2027 都用 17023，
    先开的接到请求）。此时第二个 Maya 的 bridge 会 bind 失败并报错 ——
    这是符合预期的，日志会提示端口已占用，不代表出错。

    ⚠️ 这里刻意不用 f-string：这套代码可能被 MAXScript/MEL 侧的
    老 Python 执行，f-string 在 3.5 以下不兼容。统一用 % 格式化。
    """
    global _server_sock, _disconnecting, _session_id
    with _server_lock:
        if _server_sock is not None:
            return _server_sock, False
        server = _bind_server(port)
        _server_sock = server
        _disconnecting = False
        _session_id = None
        return server, True


def _bind_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 独占：端口被占时 bind 直接抛 10048，不会静默抢占
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    except (AttributeError, OSError):
        pass    # 非 Windows / 内核不支持，退回默认独占行为

    try:
        server.bind(('127.0.0.1', port))
    except OSError as e:
        server.close()
        raise RuntimeError(
            "端口 %d 已被占用（%s）—— Maya bridge 没有启动。"
            "如果本机开了多个 Maya，只有先启动的那个能连上，"
            "这是预期行为。" % (port, e))

    server.listen(5)
    server.settimeout(.2)
    print(">>> Maya Bridge listening on port %d" % port)
    return server


def _serve(server):
    global _server_sock, _server_thread
    _server_thread = threading.current_thread()
    try:
        while _server_sock is server:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client_thread = threading.Thread(target=handle_client, args=(conn,))
            client_thread.daemon = True
            client_thread.start()
    finally:
        with _server_lock:
            if _server_sock is server:
                _server_sock = None
        server.close()


def start_server(port=MAYA_PORT):
    server, created = _open_server(port)
    if created:
        _serve(server)


def stop():
    global _server_sock, _session_id, _disconnecting, _heartbeat_probe
    with _server_lock:
        server = _server_sock
        _server_sock = None
        _session_id = None
        _disconnecting = True
    with _heartbeat_lock:
        _heartbeat_probe = None
        _heartbeat_data.clear()
    if server is not None:
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()
    thread = _server_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(1)

# ── 入口（在 Maya 里 import 后调用）────────────
def reload_actions():
    """重新扫描 maya_actions/ 目录并注册所有 action。

    为什么需要这个：`maya_actions/__init__.py` 用 glob 在 **import 时**扫一次
    目录，之后 Python 就缓存了模块对象。运行期间新增的 action 文件不会被
    扫到 → 中台报 "unknown action"，只能重启 Maya。

    这里先清掉所有 maya_actions.* 的模块缓存再重新 import，等于手动
    再扫一次目录，新旧action 都会正确注册（同名会覆盖）。

    返回注册到的 action 名列表。
    """
    import importlib

    # 清缓存：包本身 + 所有子模块
    for name in list(sys.modules.keys()):
        if name == "maya_actions" or name.startswith("maya_actions."):
            del sys.modules[name]

    importlib.invalidate_caches()
    import maya_actions  # noqa: F401  重新执行 __init__，触发 glob

    return sorted(ACTIONS.keys())


def run(port=MAYA_PORT):
    """Maya 端启动入口。重复调用安全：已监听则只重扫 action。"""
    names = reload_actions()
    server, created = _open_server(port)
    if created:
        server_thread = threading.Thread(target=_serve, args=(server,), daemon=True)
        server_thread.start()
    notify('Maya 桥接已就绪，等待中台确认连接')
    return names
