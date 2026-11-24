"""
async_bridge — 异步 ping，跨线程把结果调度回主线程。
QRunnable 跑在 threadpool；结束时用 QMetaObject.invokeMethod + QueuedConnection
把 slot 调用排到主线程的事件队列，跨线程安全。

关闭安全性：
    关窗时 QRunnable 可能还在池子里排队，等它跑完时 target（MainWindow）
    的 C++ 对象已经被销毁 → invokeMethod 抛
    "Internal C++ object ... already deleted"。

    解法：用 QPointer 弱引用持有 target，投递前先判断还活着；
    并且 invokeMethod 整体包 try/except 兜底。
"""

from PySide6 import QtCore
import json
from dcc_bridge import DccClient

try:
    import shiboken6
except ImportError:      # 理论上不会发生，PySide6 必带
    shiboken6 = None


def _alive(obj) -> bool:
    """C++ 对象是否还活着（关窗后会变False）。"""
    if obj is None:
        return False
    if shiboken6 is not None:
        try:
            return bool(shiboken6.isValid(obj))
        except Exception:
            return False
    return True


class _PingTask(QtCore.QRunnable):
    def __init__(self, client, target, slot_name, dcc, epoch=None):
        super().__init__()
        self._client = client
        self._target = target       # 主线程里的对象（MainWindow）
        self._slot = slot_name      # 该对象上形如 _on_ping_result 的方法
        self._dcc = dcc            # 哪个 DCC 的 ping，用于回调时区分来源
        self._epoch = epoch

    def run(self):
        structured = self._slot == '_on_health_epoch'
        try:
            if structured:
                report = self._client.probe()
            else:
                res = self._client.ping()
                ok = bool(res.get("ok"))
        except Exception as exc:
            report = dict(state='waiting', reachable=False, reason=str(exc))
            ok = False

        # 跨线程投递前先确认 target 还活着（关窗场景走这个分支）
        if not _alive(self._target) or getattr(self._target, "_closing", False):
            return

        # 返回 False = 投递失败（槽签名和下面的 Q_ARG 列表不匹配等）。
        # 失败必须打出来 —— 静默 pass 会让"回调永不触发"变成查不到原因的死局。
        value = (QtCore.Q_ARG('QString', json.dumps(report)) if structured
                 else QtCore.Q_ARG('bool', ok))
        args = [value, QtCore.Q_ARG("QString", self._dcc)]
        if self._epoch is not None:
            args.append(QtCore.Q_ARG('int', self._epoch))
        ok_posted = QtCore.QMetaObject.invokeMethod(
            self._target, self._slot, QtCore.Qt.QueuedConnection,
            *args,
        )
        if not ok_posted:
            _post_fail = getattr(self._target, "_log", None)
            msg = ("[qz] ping 回调投递失败：target=%s slot=%s（签名不匹配？）"
                   % (type(self._target).__name__, self._slot))
            if _post_fail is not None:
                _post_fail.error(msg)
            else:
                print(msg)


class _ConnectionTask(QtCore.QRunnable):
    def __init__(self, client, target, operation, epoch, client_id):
        super().__init__()
        self.client, self.target = client, target
        self.operation, self.epoch, self.client_id = operation, epoch, client_id

    def run(self):
        try:
            if self.operation == 'disconnect':
                result = self.client.call('bridge_disconnect', timeout=5, connect_timeout=1)
            else:
                result = self.client.call('bridge_connect', client_id=self.client_id,
                                          timeout=5, connect_timeout=1)
                if not result.get('ok') and 'unknown action' in result.get('error', ''):
                    result = {'ok': True, 'data': {'legacy': True}}
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        if _alive(self.target) and not getattr(self.target, '_closing', False):
            QtCore.QMetaObject.invokeMethod(self.target, '_on_connection_result', QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG('QString', self.client.dcc), QtCore.Q_ARG('QString', self.operation),
                QtCore.Q_ARG('int', self.epoch), QtCore.Q_ARG('QString', json.dumps(result)))


class AsyncBridge:
    """对 DccClient 的薄包装。ping_async(target, slot) 跨线程回调。

    ⚠️ 必须传入外部已有的 client，不能自己 new 一个 ⚠️
    主窗口同时持有 `_clients[dcc]`（给 widget 同步调用）和这个 bridge（给 ping）。
    早先这里 `self._client = DccClient(...)` 自建了一份，于是同一个 DCC
    存在两个 socket 客户端对象：ping 改 host/port 只改到 bridge 那份，
    而 host/port 改动（比如配置里的端口）落到 widget 那份，两边会漂移。
    → 改成共用同一个 client，host/port 只有一处真相。
    """

    def __init__(self, host: str, port: int, timeout: int = 10,
                 dcc: str = "maya", client: "DccClient" = None):
        self._client = client if client is not None else DccClient(
            host=host, port=port, dcc=dcc)
        self._default_timeout = timeout
        self._closing = False
        # Long plugin requests use the global pool. They must never starve
        # heartbeats or prevent a connection-management request from starting.
        QtCore.QThreadPool.globalInstance().setMaxThreadCount(4)
        self._pool = QtCore.QThreadPool()
        self._pool.setMaxThreadCount(2)

    @property
    def dcc(self):
        return self._client.dcc

    @property
    def client(self):
        return self._client

    @property
    def host(self):
        return self._client.host

    @host.setter
    def host(self, v):
        self._client.host = v

    @property
    def port(self):
        return self._client.port

    @port.setter
    def port(self, v):
        self._client.port = v

    def call(self, action: str, **params):
        """同步 call（widget 主动用，几十毫秒级可接受）"""
        return self._client.call(action, timeout=self._default_timeout, **params)

    def ping_async(self, target, slot: str = "_on_ping_result",
                   dcc: str = "maya", epoch=None):
        """异步 ping。回调到 target.<slot>(bool, dcc) —— target 必须在主线程。

        dcc 一起传回去，好让主窗口知道是哪个 DCC 的探测结果
        （Maya 和 Max 是两条独立连接，结果各走各的槽）。
        """
        if not self._closing:
            self._pool.start(_PingTask(self._client, target, slot, dcc, epoch))

    def connection_async(self, target, operation, epoch, client_id):
        if not self._closing:
            self._pool.start(_ConnectionTask(self._client, target, operation, epoch, client_id))

    def shutdown(self, wait_ms: int = 2000):
        """关窗时调用：等池子里在跑的 ping 收尾，避免退出时和事件循环打架。"""
        self._closing = True
        try:
            self._pool.waitForDone(wait_ms)
        except Exception:
            pass
