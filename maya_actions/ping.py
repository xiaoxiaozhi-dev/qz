"""
ping — 中台启动时探测 Maya 桥接是否可用。
返回 {"pong": True} 表示 Maya 端 + bridge + 业务代码都正常。

带 maya_version 让设置面板能显示「Maya 2024 已连接」，
而不是干巴巴一句「已连接」。非 Maya 环境返回 unknown。
"""

from maya_bridge import register_action


@register_action("ping")
def ping():
    version = "unknown"
    try:
        import maya.cmds as cmds
        version = str(cmds.about(version=True))
    except Exception:
        # 非 Maya 环境 / cmds 不可用 → 保持 unknown，不能让 ping 挂掉
        pass
    from maya_bridge import server
    return {"pong": True, "maya_version": version,
            "bridge_protocol": getattr(server, 'BRIDGE_PROTOCOL', 0)}
