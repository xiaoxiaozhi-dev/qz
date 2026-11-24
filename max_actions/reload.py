# -*- coding: utf-8 -*-
"""
max_actions/reload.py —— 一键重载 Max 端动作库，不用重启 Max。

与 maya_actions/reload.py 同构，都是 server 层的薄封装：
    逻辑唯一实现在 max_bridge/server.py::reload_actions，
    这里只做一层 action 包装，保证「中台点 Reload」和
    「粘贴 bootstrap 代码」走的是同一套逻辑，不会漂移。
"""

from max_bridge.server import register_action
from max_bridge.server import reload_actions as _reload


@register_action("reload_actions")
def reload_actions():
    return _reload()
