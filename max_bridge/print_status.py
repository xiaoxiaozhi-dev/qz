# -*- coding: utf-8 -*-
"""
max_bridge/print_status.py
排障用：打印 bridge / worker / 队列 / action 的完整状态。

必须在【主线程】执行（pymxs 限制），所以走 python.ExecuteFile 由 timer 拉起，
或在 Max 脚本编辑器里直接运行。

用法（Max 主线程）：
    import sys, runpy
    _path = u"<项目路径>/max_bridge/print_status.py"
    if sys.version_info[0] < 3:
        _path = _path.encode(sys.getfilesystemencoding() or "mbcs")
    runpy.run_path(_path)
"""

from __future__ import unicode_literals

import os
import sys


def _bridge_dir():
    """定位 max_bridge 目录。

    ⚠️ `python.ExecuteFile` / `exec(open(...).read())` 都可能不给 __file__，
       直接用会 NameError。拿不到就从 sys.path 搜。
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
    return ""


BRIDGE_DIR = _bridge_dir()
PROJECT_DIR = os.path.dirname(BRIDGE_DIR) if BRIDGE_DIR else os.getcwd()

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


def _say(s=""):
    try:
        print("[qz-max] " + s)
    except Exception:
        pass
    try:
        import pymxs
        pymxs.runtime.format("[qz-max] %\n", s)
    except Exception:
        pass


def main():
    import threading
    _say("=" * 60)
    _say("Qz Max Bridge 状态")
    _say("=" * 60)
    _say("当前线程   : %s" % threading.current_thread().name)
    main_thread = getattr(threading, "main_thread", None)
    is_main = (threading.current_thread() is main_thread() if main_thread
               else threading.current_thread().name == "MainThread")
    _say("是主线程   : %s" % is_main)
    _say("")

    try:
        import max_bridge.server as srv
    except Exception as e:
        _say("✗ 无法导入 max_bridge.server: %r" % (e,))
        _say("  sys.path 里有没有项目根目录？")
        return
    _say("项目路径   : %s" % PROJECT_DIR)
    _say("socket 端口: %d" % srv.MAX_PORT)
    _say("socket 状态: %s"
         % ("已监听" if srv._server_sock is not None else "未启动"))
    _say("待处理队列 : %d 条" % srv.pending_count())
    _say("已注册 action (%d):" % len(srv.ACTIONS))
    for name in sorted(srv.ACTIONS):
        _say("    - %s" % name)

    _say("")
    try:
        import max_bridge.worker as w
        st = w.status()
        _say("worker tick 次数  : %s" % st.get("ticks"))
        _say("worker 已处理任务: %s" % st.get("handled"))
        _say("worker 错误次数  : %s" % st.get("errors"))
        if st.get("last_error"):
            _say("worker 最后错误  : %s" % st["last_error"])
        if st.get("ticks", 0) == 0:
            _say("")
            _say("⚠ worker 从没被 timer 拉起过 → timer 没在跑，"
                 "检查 MAXScript rollout 是否创建成功")
    except Exception as e:
        _say("✗ 无法读取 worker 状态: %r" % (e,))

    _say("")
    _say("--- Max 自己的目录（权威值，别靠猜）---")
    # ⚠️ 只用 Max 报出来的合法 key。
    #   我曾凭空编出 "#systemScripts" / "#startup"，Max 直接报
    #   「该函数需要带有值的参数」并列出全部合法值 —— 那一刻才发现
    #   之前所有关于路径的结论都是猜的。
    #   合法 key（取自 Max 2027 的报错原文）：
    #     #startupScripts  #userStartupScripts  #scripts  #userScripts
    #     #macroScripts    #userMacros         #plugcfg  #maxData
    #     #maxroot         #defaults           #plugins
    _DIR_KEYS = (
        "#startupScripts", "#userStartupScripts",   # ★ 这两个才是 startup
        "#scripts", "#userScripts", "#macroScripts", "#userMacros",
        "#plugcfg", "#maxData", "#maxroot", "#defaults",
        "#plugindata", "#presets", "#userSettings", "#temp",
    )
    try:
        import pymxs
        rt = pymxs.runtime
        for key in _DIR_KEYS:
            try:
                v = rt.execute("getDir %s" % key)
            except Exception as e:
                v = "<%s: %s>" % (type(e).__name__, e)
            _say("  %-22s %s" % (key, v))

        _say("")
        _say("★ 要装自动启动脚本，请用上面 #userStartupScripts 那行的路径")
        _say("  （#startupScripts 是安装目录，覆盖它升级会丢、要管理员权限）")
    except Exception as e:
        _say("  ✗ 无法查询 Max 目录: %r" % (e,))
    _say("")
    _say("=" * 60)


main()
