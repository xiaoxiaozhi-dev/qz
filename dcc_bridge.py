"""
dcc_bridge.py — DCC 无关的 socket 客户端

Maya、Max 和 Blender 共用「一行 JSON 请求 + 一行 JSON 响应」的桥接协议。

各 DCC 端口（按 DCC 区分，同 DCC 多版本共用端口、先开先得）：
    maya : 17023
    max  : 17024
    blender : 17025

这个类跑在中台进程里，**纯 socket + json，不依赖任何 DCC**，
所以可以安全地在 QRunnable 的线程池里调用。
"""

import json
import socket
import sys
import threading
import errno


class BridgeUnavailable(RuntimeError):
    """A transport failure with a stable category for connection monitoring."""

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind

# 各 DCC 的默认端口
DCC_PORTS = {
    "maya": 17023,
    "max": 17024,
    "blender": 17025,
}

DCC_LABELS = {
    "maya": "Maya",
    "max": "3ds Max",
    "blender": "Blender",
}


def dcc_label(dcc: str) -> str:
    return DCC_LABELS.get(dcc, dcc)


def default_port(dcc: str) -> int:
    return DCC_PORTS.get(dcc, 17023)


class DccClient:
    """对任意 DCC bridge 的调用封装。

    与旧的 MayaClient 完全兼容（同样的方法签名），
    只是多了 dcc 参数用于决定默认端口和错误提示文案。
    """

    def __init__(self, host="127.0.0.1", port=17023, dcc="maya"):
        self.host = host
        self.port = port
        self.dcc = dcc
        # ★ 连接状态（2026-10-21 加）。
        #   由 MainWindow 的 ping 回调维护 —— 插件卡片靠它决定
        #   「未连接时按钮置灰」。DccClient 自己是**每次调用现连**的，
        #   内部没有持久连接，所以状态只能由外面告知。
        self.connected = False
        self._socket_lock = threading.Lock()
        self._sockets = set()
        self._closed = False
        self.last_ping_data = {}

    def close(self):
        """停止本客户端的网络等待；不会取消 DCC 中已经开始的操作。"""
        with self._socket_lock:
            self._closed = True
            self.connected = False
            sockets = list(self._sockets)
        for active in sockets:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                active.close()
            except OSError:
                pass

    @classmethod
    def for_dcc(cls, dcc: str, host="127.0.0.1", port=None):
        port = default_port(dcc) if port is None else port
        return cls(host=host, port=port, dcc=dcc)

    def call(self, action, timeout=10, connect_timeout=None, execution_timeout=None, **params):
        """发送 action + params，返回结果 dict。

        行为与 MayaClient 一致：正常返回 {'ok': True, 'data': ...}，
        连不上则抛 RuntimeError。

        ⚠️ 收到完整响应后必须立刻停止 recv ⚠️
        协议是「一行 JSON + \\n」，所以读到换行就结束。
        早先写成「一直 recv 到 socket 超时」有两个后果：
          1. Max 端的连接会一直开着等响应 → 每次调用白等满 timeout
             （ping timeout=3 → 每次心跳都慢 3 秒，界面完全感知不到延迟）
          2. 服务端若不及时 close，日志会一直在刷「未响应」
        Maya 侧以前没暴露这个问题，是因为它的 handler 每次响应后都 close 了连接，
        `recv` 返回空串才 break —— 靠对方的实现细节兜底，不可取。
        """
        with self._socket_lock:
            if self._closed:
                raise RuntimeError("中台连接已关闭，已停止等待；DCC 中已开始的操作不会被取消")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sockets.add(s)
        try:
            s.settimeout(timeout if connect_timeout is None else connect_timeout)
            try:
                s.connect((self.host, self.port))
            except OSError as e:
                refused = (getattr(e, 'winerror', None) == 10061
                           or e.errno in (errno.ECONNREFUSED, 10061))
                raise BridgeUnavailable("%s 未响应（%s:%d）：%s"
                                        % (dcc_label(self.dcc), self.host, self.port, e),
                                        'refused' if refused else 'timeout' if isinstance(e, socket.timeout)
                                        else 'transport') from e
            s.settimeout(timeout)
            request = {"action": action, "params": params}
            if execution_timeout is not None:
                request["execution_timeout"] = execution_timeout
            payload = json.dumps(request) + "\n"
            s.sendall(payload.encode("utf-8"))

            result = b""
            timed_out = False
            while b"\n" not in result:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    timed_out = True
                    break
                if not chunk:
                    # 对端关闭：数据可能已经收全，也可能没收全
                    break
                result += chunk
        finally:
            with self._socket_lock:
                self._sockets.discard(s)
            s.close()

        if self._closed:
            raise RuntimeError("中台连接已关闭，已停止等待；DCC 中已开始的操作不会被取消")

        if not result:
            raise BridgeUnavailable("%s 桥接暂时没有响应" % dcc_label(self.dcc),
                                    'timeout' if timed_out else 'transport')
        if timed_out:
            raise BridgeUnavailable("%s 桥接响应尚未完成" % dcc_label(self.dcc), 'timeout')

        return json.loads(result.decode("utf-8").strip())

    def ping(self):
        """快捷 ping"""
        result = self.call("ping", timeout=3)
        data = result.get("data") or {}
        if result.get("ok"):
            # A bridge-only busy reply must not erase the last measured version.
            self.last_ping_data.update(data)
        return result

    def probe(self):
        """Connection monitoring only: never retries or dispatches business actions."""
        try:
            response = self.ping()
        except BridgeUnavailable as exc:
            return dict(state='offline' if exc.kind == 'refused' else 'waiting',
                        reachable=False, reason=str(exc))
        except (OSError, ValueError, RuntimeError) as exc:
            return dict(state='waiting', reachable=False, reason=str(exc))
        data = response.get('data') or {}
        if not response.get('ok'):
            return dict(state='error', reachable=True, reason=response.get('error', '桥接异常'), data=data)
        state = ('busy' if data.get('busy') else
                 'waiting' if data.get('main_thread_ready') is False else 'ready')
        return dict(state=state, reachable=True, data=data)


class MayaClient(DccClient):
    """向后兼容的别名。语义没变，只是多了 dcc 维度。"""

    def __init__(self, host="127.0.0.1", port=17023):
        super().__init__(host=host, port=port, dcc="maya")
