"""
reload_actions — 重新扫描 maya_actions/ 目录，让新增/修改的脚本立即生效。

为什么需要它：maya_actions/__init__.py 用 glob **在 import 时**扫一次目录，
之后 Python 就缓存了模块对象。Maya 运行期间新增的 .py 永远扫不到 →
中台报 "unknown action"。这个 action 就是手动再扫一次。

实现本体在 maya_bridge.server.reload_actions()，这里只是薄封装——
这样"首次 bootstrap"和"日常 reload"走的是同一份逻辑，不会漂移。

死锁说明：reload_actions 自己也是 action，需要先进 action 表才能被调用。
所以**新增/修改 action 后第一次要用「临时启动」粘贴一次代码**（或重启 Maya），
之后 Reload 按钮就能一直自助用了。
"""

from maya_bridge import register_action
from maya_bridge.server import reload_actions as _reload


@register_action("reload_actions")
def reload_actions():
    """重扫动作库，返回当前注册的动作名列表。"""
    return _reload()
