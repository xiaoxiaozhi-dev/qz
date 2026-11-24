# -*- coding: utf-8 -*-
"""
test_bridge_client.py — DccClient / MayaClient 的收发行为

重点防一个真实踩过的回归：客户端 `while True: recv()` 读到换行却不 break，
非要等到 socket 超时才停。
- Maya 侧因为服务端每次响应后 close，能侥幸 break → 一直没人发现
- Max 侧服务端保持连接等响应 → 每次调用白等满 timeout
  实测 ping 耗时 3.111s（正好卡在 timeout=3 上），修完 0.098s

所以这里直接断言**耗时**，而不只是断言结果对不对。
"""

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import max_bridge.server as srv   # noqa: E402
import max_bridge.worker as wk    # noqa: E402

from dcc_bridge import DccClient  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("  -> " + str(detail)) if detail else ""))


def main():
    print("=" * 62)
    print("Bridge 客户端收发行为验证（不启动 Max / Maya）")
    print("=" * 62)

    srv.REQUEST_TIMEOUT = 5.0
    names = srv.reload_actions()
    # Let the OS allocate a test-only port; never probe a user's live DCC bridge.
    threading.Thread(target=srv.start_server, args=(0,), daemon=True).start()
    deadline = time.monotonic() + 3
    while srv._server_sock is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if srv._server_sock is None:
        raise RuntimeError("Test bridge failed to start")
    bridge_port = srv._server_sock.getsockname()[1]
    stop = threading.Event()

    # 扮演 MAXScript timer（真实 interval=100ms）
    def fake_timer():
        while not stop.is_set():
            wk.pump()
            time.sleep(0.1)

    threading.Thread(target=fake_timer, daemon=True).start()
    time.sleep(0.3)

    client = DccClient("127.0.0.1", bridge_port, dcc="max")

    # ── 1. 基本连通 ─────────────────────────────
    print("\n1. ping 基本连通")
    r = client.ping()
    check("ping 返回 ok", r.get("ok") is True, r)
    check("ping 内容有 pong", r.get("data", {}).get("pong") is True,
          r.get("data"))

    # ── 2. ★ 关键：响应延迟不能吃满 timeout ★
    print("\n2. ★ 响应延迟（回归：while True recv 不 break）")
    samples = []
    for _ in range(5):
        t0 = time.time()
        client.ping()
        samples.append(time.time() - t0)
    worst = max(samples)
    avg = sum(samples) / len(samples)
    print("     5 次 ping: avg=%.3fs worst=%.3fs" % (avg, worst))
    check("平均延迟 < 0.5s（timer interval 是 0.1s）", avg < 0.5, "%.3fs" % avg)
    check("最坏延迟 < 1s（远小于 ping timeout=3s）", worst < 1.0, "%.3fs" % worst)
    check("没有出现吃满 timeout 的情况（< 3s）", worst < 3.0, "%.3fs" % worst)

    # ── 3. 服务端保持连接时也能立刻返回 ───────────
    print("\n3. 服务端不 close 连接时仍应立即返回")
    # 自己起一个「响应后不 close」的极简服务端，模拟 Max 的行为
    srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv2.bind(("127.0.0.1", 0))
    port = srv2.getsockname()[1]
    srv2.listen(1)
    released = threading.Event()

    def hold_open():
        conn, _ = srv2.accept()
        conn.recv(4096)
        conn.sendall(b'{"ok": true, "data": {"held": true}}\n')
        # ★ 故意不 close，等客户端先走
        released.wait(5)
        try:
            conn.close()
        except Exception:
            pass

    th = threading.Thread(target=hold_open, daemon=True)
    th.start()
    c2 = DccClient("127.0.0.1", port, dcc="max")
    t0 = time.time()
    r2 = c2.call("ping", timeout=5)
    dt = time.time() - t0
    check("对端不 close 也能立刻返回", dt < 1.0, "%.3fs" % dt)
    check("响应内容正确", r2.get("data", {}).get("held") is True, r2)
    released.set()
    srv2.close()

    # ── 4. 超时确实生效（对端不回包）────────────────
    print("\n4. 对端不回包时应按 timeout 报错，不该永久挂死")
    srv3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv3.bind(("127.0.0.1", 0))
    port3 = srv3.getsockname()[1]
    srv3.listen(1)

    def silent():
        conn, _ = srv3.accept()
        conn.recv(4096)
        time.sleep(4)          # 比客户端 timeout 长
        try:
            conn.close()
        except Exception:
            pass

    threading.Thread(target=silent, daemon=True).start()
    c3 = DccClient("127.0.0.1", port3, dcc="max")
    t0 = time.time()
    try:
        c3.call("ping", timeout=1.0)
        check("超时应抛错", False, "竟然返回了")
    except RuntimeError as e:
        dt = time.time() - t0
        check("超时应抛 RuntimeError", True, str(e)[:60])
        check("超时时长符合设定（1s 左右）", 0.8 < dt < 2.0, "%.3fs" % dt)
    srv3.close()

    # ── 5. 端口未监听时的错误可读 ───────────────────
    print("\n5. 端口未监听")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unopened:
        unopened.bind(("127.0.0.1", 0))
        try:
            DccClient("127.0.0.1", unopened.getsockname()[1], dcc="max").ping()
            check("未监听应抛错", False, "竟然成功了")
        except RuntimeError as e:
            check("未监听抛出可读错误", "未响应" in str(e), str(e)[:60])

    # ── 6. 双 DCC 并发 ping ────────────────────────
    print("\n6. 双 DCC 并发 ping 不互相拖累")
    maya_like = DccClient("127.0.0.1", bridge_port, dcc="maya")
    max_like = DccClient("127.0.0.1", bridge_port, dcc="max")
    out = {}

    def one(tag, c):
        t0 = time.time()
        try:
            c.ping()
            out[tag] = time.time() - t0
        except Exception as e:
            out[tag] = repr(e)

    t0 = time.time()
    ts = [threading.Thread(target=one, args=a) for a in
          (("maya", maya_like), ("max", max_like))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    total = time.time() - t0
    print("     并发总耗时 %.3fs  (%s)" % (total, out))
    check("并发总耗时应接近单次（不串行）", total < 1.0, "%.3fs" % total)

    # ── 7. client 属性可写（host/port 单一真相）──────
    print("\n7. client 字段")
    c = DccClient("127.0.0.1", 1234, dcc="max")
    check("dcc 保留", c.dcc == "max", c.dcc)
    c.host = "1.2.3.4"
    c.port = 4321
    check("host 可写", c.host == "1.2.3.4", c.host)
    check("port 可写", c.port == 4321, c.port)
    check("for_dcc 默认端口正确",
          DccClient.for_dcc("max").port == 17024
          and DccClient.for_dcc("maya").port == 17023,
          (DccClient.for_dcc("maya").port, DccClient.for_dcc("max").port))

    stop.set()
    srv._server_sock.close()
    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
    print("=" * 62)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
