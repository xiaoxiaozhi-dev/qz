# -*- coding: utf-8 -*-
"""
max_actions/ping.py —— 连通性测试。

对应 Maya 侧的 ping。中台的连接检测靠它，
同时它也是验证「socket 线程 → 队列 → 主线程 worker」整条链是否通畅的最快方式。
"""

import threading  # noqa: F401  保留给排障时手动调用

from max_bridge.server import register_action

# 动作库由主线程加载；Python 2.7 没有 threading.main_thread()。
_MAIN_THREAD = threading.current_thread()

@register_action("ping")
def ping():
    """回显一条信息，并带上执行线程信息。

    返回的 thread 应该恒为 "MainThread" —— 这是整条链路正确性的关键证据。
    """
    import threading as _t
    version = 'unknown'
    try:
        import pymxs
        version = str(int(pymxs.runtime.maxVersion()[0]) // 1000 + 1998)
    except Exception:
        pass
    from max_bridge import server
    return {
        "pong": True,
        "max_version": version,
        "bridge_protocol": getattr(server, 'BRIDGE_PROTOCOL', 0),
        "thread": _t.current_thread().name,
        "is_main": _t.current_thread() is _MAIN_THREAD,
    }
