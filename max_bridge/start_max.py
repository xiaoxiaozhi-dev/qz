# -*- coding: utf-8 -*-
"""
max_bridge/start_max.py
在 Max 里运行这个文件，一键起 bridge（socket + 主线程 timer）。

用法（Max → 脚本编辑器 → 新建文本 → 粘贴 → 运行 Python）：
    复制中台设置中的「Max 临时启动」代码，兼容 Python 2.7 / 3。

⚠️ 别用 `exec(open(path).read())` —— 那样跑在 `<string>` 命名空间里，
   没有 __file__，下面推导路径会直接 NameError。
   （本文件已对 __file__ 缺失做了兜底，但优先照上面的写法。）

它会：
  1. 把项目根目录加进 sys.path
  2. 启动 socket server（17024）在后台线程 —— 该线程不碰 pymxs
  3. 建一个不可见的 MAXScript rollout timer，每 100ms 拉起 worker.py
     来在主线程消费任务队列

幂等：重复运行不会重复 bind 端口，也不会叠 timer。
"""

from __future__ import unicode_literals

import io
import os
import sys


def _bridge_dir():
    """定位 max_bridge 目录，兼容三种调用方式。

    ⚠️ 被 `exec(open(path).read())` 执行时 __file__ 不存在，
       直接用会 NameError。所以拿不到就退回 sys.path 里搜。
    """
    try:
        filename = __file__
        if sys.version_info[0] < 3 and isinstance(filename, bytes):
            filename = filename.decode(sys.getfilesystemencoding() or "mbcs")
        return os.path.dirname(os.path.abspath(filename))
    except NameError:
        pass
    for entry in sys.path:
        cand = os.path.join(entry, "max_bridge")
        if os.path.isfile(os.path.join(cand, "worker.py")):
            return cand
    # 最后兜底：cwd 往上找两层
    cwd = os.path.dirname(os.path.abspath(os.getcwd()))
    for _ in range(3):
        cand = os.path.join(cwd, "max_bridge")
        if os.path.isfile(os.path.join(cand, "worker.py")):
            return cand
        cwd = os.path.dirname(cwd)
    raise RuntimeError("找不到 max_bridge 目录，请确认 sys.path 里有项目根")


BRIDGE_DIR = _bridge_dir()
PROJECT_DIR = os.path.dirname(BRIDGE_DIR)
WORKER = os.path.join(BRIDGE_DIR, "worker.py")
BOOTSTRAP = os.path.join(BRIDGE_DIR, "bootstrap.ms")


def _log(msg):
    """写日志。

    ⚠️ 只走 pymxs.runtime.format，不额外 print ——
    Max 里 print 和 runtime.format 都落到 Listener，两边都写会**每条打两遍**。
    """
    try:
        import pymxs
        pymxs.runtime.format("[qz-max] %\n", msg)
    except Exception:
        # 不在 Max 里跑（本地测试）时退回 print
        try:
            print("[qz-max] " + msg)
        except Exception:
            pass


WINDOW_TITLE = "Qz Bridge"


def _hide_rollout_window():
    """把 rollout 窗口藏起来。

    为什么需要：MAXScript 没有 #style_hidden，`#style_no_caption +
    #style_toolwindow` 只能去掉标题栏，那个 1x1 的空框仍然会在视口里
    （2027 实测），用户看得见又不知道是什么，徒增困惑。

    为什么用 Win32 的 ShowWindow(SW_HIDE) 而不是别的办法：
      - ★ **隐藏不影响 timer**。Max 主循环照常驱动隐藏窗口里的控件，
        实测隐藏后 worker ticks 继续增长、58 个任务全部处理、0 错误。
      - **destroyDialog 会停 timer** → bridge 立刻死，绝不能用来"隐藏"。
      - MAXScript 里设 rollout.pos = [-32000,-32000] 无效，
        Max 会把窗口拉回可见区域。

    失败也不影响 bridge —— 顶多是用户看见那个小框。
    """
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            owner = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
            if owner.value != os.getpid():
                hwnd = None
        if not hwnd:
            _log("提示：没找到 rollout 窗口（可能已被隐藏/销毁）")
            return False
        ctypes.windll.user32.ShowWindow(hwnd, 0)      # 0 = SW_HIDE
        _log("rollout 窗口已隐藏（不影响 timer）")
        return True
    except Exception as e:
        _log("隐藏 rollout 窗口失败（不影响 bridge）：%r" % (e,))
        return False


def start(port=None):
    # 1. sys.path
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    _log("项目路径: %s" % PROJECT_DIR)

    # 2. socket server（后台线程）
    import max_bridge.server as srv

    if not getattr(srv, "SUPPORTS_WORKER_FAILURE", False):
        _log("桥接核心需要更新，请保存场景并重新启动 Max；仅重载动作库不能更新核心")
        return False
    if getattr(srv, 'BRIDGE_PROTOCOL', 0) < 3:
        _log('桥接核心需要更新，请保存场景并重启 Max 后再连接')
        return False
    srv.reset_worker_failure()

    if srv._server_sock is not None:
        _log("Bridge 已在运行，仅刷新 action")
        names = srv.reload_actions()
    else:
        names = srv.run() if port is None else srv.run(port=port)
    _log("已加载 %d 个 action: %s" % (len(names), ", ".join(names)))

    # 3. 主线程 timer
    for label, path in (("bootstrap.ms", BOOTSTRAP), ("worker.py", WORKER)):
        if not os.path.isfile(path):
            _log("找不到 %s: %s" % (label, path))
            return False

    with io.open(BOOTSTRAP, "r", encoding="utf-8") as f:
        ms = f.read()
    # 路径统一转正斜杠：MAXScript 字符串里反斜杠是转义符
    ms = ms.replace("@WORKER@", WORKER.replace("\\", "/"))
    ms = ms.replace("@PROJECT@", PROJECT_DIR.replace("\\", "/"))
    ms = ms.replace("@FAILURE@", srv.WORKER_FAILURE_PATH.replace("\\", "/"))

    import pymxs
    ok = True
    try:
        pymxs.runtime.execute(ms)
    except Exception as e:
        # socket server 已经起来了，只是主线程 timer 没建成。
        # 直接抛出去的话用户只看到一大段 MAXScript 堆栈，
        # 不知道「连接能用、只是不能调 action」，所以这里给明确提示。
        ok = False
        _log("★ bootstrap.ms 执行失败：%r" % (e,))
        _log("  socket server 已在 %d 端口运行，但主线程 timer 没起来 —— "
             "此时中台能连上，但所有 action 调用都会超时" % srv.MAX_PORT)
        _log("  排障：Max 主线程执行 max_bridge/print_status.py，"
             "若 ticks=0 就是 timer 没建起来")

    # 4. 把 1x1 的空 rollout 藏起来。
    #    ★ 不跟着 return：即使 bootstrap 抛异常也要试一次 ——
    #      CreateDialog 是在异常点之前就成功的，窗口已经存在；
    #      如果这里提前 return，窗口就会留在视口里，用户只看到
    #      「有报错 + 有个莫名其妙的小框」而查不到两者的关联。
    _hide_rollout_window()

    if not ok:
        srv.stop()
        try:
            srv._stop_worker()
        except Exception:
            pass
        _log('桥接启动未完成，监听端口已关闭，请检查上方错误')
        return False

    _log("主线程 timer 已启动（interval=100ms）")
    _log("端口 %d，等待中台连接…" % srv.MAX_PORT)
    srv.notify('Max 桥接已就绪，等待中台确认连接')
    return True


def stop():
    """Main-thread stop: disable the timer and release the listening port."""
    import max_bridge.server as srv
    srv._stop_worker()
    srv.stop()
    srv.notify('Max 桥接已停止，端口已关闭')
    return True


def status():
    """排障用：在主线程打印 worker / 队列 / action 状态。"""
    try:
        import pymxs
        pymxs.runtime.execute(
            "python.ExecuteFile @%s"
            % os.path.join(BRIDGE_DIR, "print_status.py").replace("\\", "/"))
    except Exception as e:
        _log("status 失败: %r" % (e,))


if __name__ == "__main__":
    start()
