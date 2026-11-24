# -*- coding: utf-8 -*-
"""
test_max_bridge.py — Max bridge 的本地验证（不启动 Max）

在普通 Python 里模拟「socket 线程收请求 → 队列 → 主线程 worker 消费」，
验证架构正确性。Max 里唯一不同的只是 worker 由 MAXScript timer 拉起，
而这里用一个后台线程模拟那个 timer。

跑法：
    .venv/Scripts/python.exe test_max_bridge.py
"""

import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import max_bridge.server as srv  # noqa: E402

def _free_port() -> int:
    """问系统要一个当前空闲的端口。

    ⚠️ 绝不能用固定的 17024 ⚠️
    server 改用 SO_EXCLUSIVEADDRUSE 后（这是正确的做法，能揭出端口被占），
    真 Max 一旦开着 bridge，测试就会因为端口被占而失败 ——
    但那是环境冲突，不是代码有问题。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ⚠️ 用空闲随机端口，不要用 srv.MAX_PORT（真 Max 开着时会被独占模式挡下）
PORT = _free_port()
MAIN_TID = threading.get_ident()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("  -> " + str(detail)) if detail else ""))


def fake_timer(stop_evt, interval=0.05):
    """模拟 MAXScript rollout timer：周期性在「主线程」跑 worker.pump()。

    真实环境里是 MAXScript 在主线程 ExecuteFile(worker.py)，
    这里用后台线程直接调 pump()，逻辑等价。
    """
    import max_bridge.worker as worker
    while not stop_evt.is_set():
        worker.pump()
        time.sleep(interval)


def client(payload, timeout=10):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", PORT))
    s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8").strip())


def main():
    print("=" * 62)
    print("Max Bridge 本地验证（不启动 Max）")
    print("=" * 62)
    print("端口 %d / 主线程 id %d\n" % (PORT, MAIN_TID))

    # ── 1. action 重扫 ─────────────────────────
    print("1. reload_actions")
    names = srv.reload_actions()
    check("注册到 action", "ping" in names, names)

    # ── 2. socket server ──────────────────────
    print("\n2. socket server")
    # ⚠️ 端口必须显式传进去。start_server 不传参会用默认的 17024，
    #   而真 Max 开着时那个端口是被占的 → 服务端起不来 → 连不上。
    t = threading.Thread(target=srv.start_server, args=(PORT,), daemon=True)
    t.start()
    time.sleep(0.3)
    check("端口已监听", srv._server_sock is not None)

    # ── 3. 业务仍走主线程队列；心跳独立回复等待状态。
    print("\n3. 无 worker 时业务超时，心跳仍能回复")
    # 服务端默认等 10s，测试里调短，否则客户端会先超时
    srv.REQUEST_TIMEOUT = 1.5
    t0 = time.time()
    r = client({"action": "queue_business_probe"}, timeout=6)
    dt = time.time() - t0
    check("请求未成功（说明在等主线程）", r.get("ok") is False, r)
    check("服务端返回超时错误", "timeout" in str(r.get("error", "")), r)
    check("超时耗时接近设定值", 1.0 < dt < 3.0, "%.2fs" % dt)
    t0 = time.time()
    r = client({"action": "ping"}, timeout=2)
    check("心跳独立确认桥接存活", r.get('ok') and r['data']['bridge_alive'], r)
    check("心跳报告主线程等待响应", r.get('data', {}).get('main_thread_ready') is False, r)
    check("心跳不等待业务超时", time.time() - t0 < 1, time.time() - t0)
    srv.REQUEST_TIMEOUT = 10.0

    # ── 4. 启动「timer」后应立即可用 ──────────
    print("\n4. 启动模拟 timer 后")
    stop_evt = threading.Event()
    tt = threading.Thread(target=fake_timer, args=(stop_evt,), daemon=True)
    tt.start()
    time.sleep(0.3)

    r = client({"action": "ping"}, timeout=5)
    check("ping 返回 ok", r.get("ok") is True, r)
    if r.get("ok"):
        d = r.get("data", {})
        check("ping 内容正确", d.get("pong") is True, d)
        print("     (ping 自报线程: %s / is_main=%s)"
              % (d.get("thread"), d.get("is_main")))

    # ── 5. action 在 worker 线程执行 ──────────
    print("\n5. action 确实跑在消费线程（timer 线程）上")

    @srv.register_action("where_am_i")
    def where_am_i():
        return {"thread": threading.current_thread().name,
                "is_main": threading.current_thread() is threading.main_thread(),
                "tid": threading.get_ident()}

    r = client({"action": "where_am_i"}, timeout=5)
    if r.get("ok"):
        d = r["data"]
        check("action 在非主线程执行（真实环境=Max 主线程）",
              d["is_main"] is False, d)
    else:
        check("action 在非主线程执行", False, r)

    # ── 6. 未知 action ────────────────────────
    print("\n6. 错误处理")
    r = client({"action": "no_such_action_xyz"}, timeout=5)
    check("未知 action 返回错误", r.get("ok") is False, r)
    check("错误信息可读", "unknown action" in str(r.get("error", "")), r)

    # ── 7. action 抛异常不应影响后续请求 ──────
    print("\n7. action 异常隔离")

    @srv.register_action("boom")
    def boom():
        raise ValueError("故意炸的")

    r = client({"action": "boom"}, timeout=5)
    check("异常被捕获为错误响应", r.get("ok") is False, r)
    r = client({"action": "ping"}, timeout=5)
    check("异常后 bridge 仍可用", r.get("ok") is True, r)

    # ── 8. 并发多请求 ─────────────────────────
    print("\n8. 并发 20 个请求")
    results = []

    def hit():
        results.append(client({"action": "ping"}, timeout=10))

    ths = [threading.Thread(target=hit) for _ in range(20)]
    for x in ths:
        x.start()
    for x in ths:
        x.join()
    ok_n = sum(1 for r in results if r.get("ok"))
    check("20 个请求全部成功", ok_n == 20, "%d/20" % ok_n)

    # ── 9. worker 状态 ────────────────────────
    print("\n9. worker 状态")
    import max_bridge.worker as worker
    st = worker.status()
    print("     %s" % st)
    check("worker tick 过", st["ticks"] > 0, st["ticks"])
    check("worker 处理过任务", st["handled"] > 0, st["handled"])
    check("worker 无错误", st["errors"] == 0, st["last_error"])

    # ── 10. reload 能刷出新 action ────────────
    print("\n10. Reload 刷出新 action")
    real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "max_actions", "tmp_probe.py")
    with open(real, "w", encoding="utf-8") as f:
        f.write("from max_bridge.server import register_action\n"
                "@register_action('tmp_probe')\n"
                "def tmp_probe():\n    return {'v': 1}\n")
    try:
        r = client({"action": "tmp_probe"}, timeout=5)
        check("新 action 未 reload 时不可见（复现旧问题）",
              r.get("ok") is False, r)
        srv.reload_actions()
        r = client({"action": "tmp_probe"}, timeout=5)
        check("reload 后新 action 可用", r.get("ok") is True, r)
    finally:
        if os.path.exists(real):
            os.remove(real)
        srv.reload_actions()

    stop_evt.set()
    time.sleep(0.2)

    # ── 汇总 ──────────────────────────────────
    print()
    print("=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败：")
        for f in FAIL:
            print("  - " + f)
    print("=" * 62)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
