"""ping 回调链路端到端回归（离���，不起任何 DCC）。

为什么需要这个测试：
    历史上出现过「socket 完全正常、DCC 端日志一切正常，但中台一直显示未连接」。
    断点在**回调解码**这一环 —— MainWindow 的槽声明成 @Slot(bool)，
    而 async_bridge 投的是 (bool, QString) 两个参数，
    PySide6 的 invokeMethod 因签名不匹配**静默失败**，
    于是 `_bridge_ok` 永远是 False。
    症状和「端口被占 / 服务端没起 / 协议错了」几乎一模一样，
    靠读代码看不出来，必须靠端到端断言抓。

这里做的：
    起两个假 socket server（冒充 Maya / Max），让中台真的 ping 通，
    然后断言 `_bridge_ok["maya"]` / `_bridge_ok["max"]` 最终都变成 True。
    只要槽签名和 Q_ARG 列表对不上，这个测试必挂。
"""

import os
import socket
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6 import QtCore, QtWidgets  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


# ── 假 DCC 服务端 ──────────────────────────────────
class FakeDcc:
    """最小 bridge 服务端：认 ping / 任意 action，回一行 JSON。"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.hits = 0
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.hits += 1
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn):
        try:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            import json
            req = json.loads(data.decode("utf-8").strip())
            if req.get("action") == "ping":
                # 对齐真实 bridge 的 ping 返回结构：版本号在 data 里
                resp = {"ok": True,
                        "data": {"maya_version": "2026.5",
                                 "max_version": "2027.0"}}
            else:
                resp = {"ok": True}
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + extra) if extra and not cond else ""))


def spin(ms=600, step=30):
    """转事件循环，让 QueuedConnection 的回调落地。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(step / 1000.0)


def wait_until(fn, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
        if fn():
            return True
    return False


def main():
    app = QtWidgets.QApplication(sys.argv)

    maya_fake = FakeDcc()
    max_fake = FakeDcc()

    # MainWindow 无参构造、内部自读 config → 构造完再改端点。
    # 改 client 即可，bridge 与 widget 共用同一实例（下面有断言查这一点）。
    win = MainWindow()
    win._clients["maya"].port = maya_fake.port
    win._clients["max"].port = max_fake.port
    win._host = "127.0.0.1"
    win.show()

    print("\n--- 槽签名与 invokeMethod 参数一致性（静态）---")
    from PySide6 import QtCore as _QtCore
    mo = win.metaObject()
    idx = mo.indexOfMethod("_on_ping_result(bool,QString)")
    check("槽签名确认为 (bool,QString)",
          idx != -1,
          "实际签名不匹配 invokeMethod 投递的 Q_ARG 列表，回调会静默失败")

    print("\n--- 双 DCC ping 端到端 ---")
    win._kick_ping("maya")
    win._kick_ping("max")

    got_maya = wait_until(lambda: win._bridge_ok.get("maya") is True)
    check("maya 回调被触发且 _bridge_ok 置 True",
          got_maya,
          "回调链路断了（socket 是通的）")
    got_max = wait_until(lambda: win._bridge_ok.get("max") is True)
    check("max 回调被触发且 _bridge_ok 置 True", got_max)

    check("假服务端确实收到了 ping", maya_fake.hits >= 1 and max_fake.hits >= 1,
          "maya hits=%d max hits=%d" % (maya_fake.hits, max_fake.hits))

    # _versions 只在设置面板打开时才真去 ping 取（省一次连接的设计），
    # 所以必须先切到 settings tab 再断言。
    win._activity.show_panel(1)
    spin(200)
    win._kick_ping("maya")
    ok_ver = wait_until(lambda: win._versions.get("maya") not in ("", None))
    check("设置面板打开时 _versions 被回填", ok_ver,
          "version=%r" % win._versions.get("maya"))

    print("\n--- 断开后回落为 False ---")
    maya_fake.close()
    win._kick_ping("maya")
    # 端口关闭后 connect 会被拒，但要等线程池里那次跑完 → 给足超时
    back = wait_until(lambda: win._bridge_ok.get("maya") is False, timeout=15)
    check("服务端关掉后 maya 回落为未连接", back)
    check("另一个 DCC 不受影响（max 仍为 True）",
          win._bridge_ok.get("max") is True)

    print("\n--- widget 用的 client 与 ping 用的 client 是同一份 ---")
    check("maya client 同一实例",
          win._bridges["maya"].client is win._clients["maya"])
    check("max client 同一实例",
          win._bridges["max"].client is win._clients["max"])

    print("\n--- 独立桥接对象（不经 MainWindow）投递也能落地 ---")
    from dcc_bridge import DccClient
    from ui.async_bridge import AsyncBridge

    # ⚠️ 槽必须是**类体内**定义的 @Slot 才会进 metaObject，
    #   局部函数 + @Slot 装饰器再挂到实例上，PySide6 不会注册它，
    #   invokeMethod 直接返回 False（这是另一个容易踩的坑）。
    class _Recv(QtCore.QObject):
        def __init__(self):
            super().__init__()
            self.got = None

        @QtCore.Slot(bool, str)
        def _on_cb(self, ok, dcc):
            self.got = (ok, dcc)

    standalone = _Recv()
    port = FakeDcc().port
    br = AsyncBridge(host="127.0.0.1", port=port, dcc="maya",
                     client=DccClient("127.0.0.1", port, dcc="maya"))
    br.ping_async(standalone, slot="_on_cb", dcc="maya")
    ok = wait_until(lambda: standalone.got is not None)
    check("独立对象 (bool,str) 槽能收到结果", ok,
          "invokeMethod 投递失败：槽不在类体内定义 / 签名不匹配")

    print("\n--- 反向验证：签名故意写错时投递必须失败（证明断言有效）---")

    class _BadRecv(QtCore.QObject):
        def __init__(self):
            super().__init__()
            self.called = False

        @QtCore.Slot(bool)          # ← 故意少一个参数
        def _on_cb(self, ok):
            self.called = True

    bad = _BadRecv()
    bad_port = FakeDcc().port
    bad_bridge = AsyncBridge(host="127.0.0.1", port=bad_port, dcc="maya",
                             client=DccClient("127.0.0.1", bad_port,
                                              dcc="maya"))
    bad_bridge.ping_async(bad, slot="_on_cb", dcc="maya")
    spin(1200)
    check("槽签名不匹配时回调不落地（这正是老 bug 的形态）",
          bad.called is False)

    win._closing = True
    win.close()
    maya_fake.close()
    max_fake.close()

    print("\n" + "=" * 50)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
