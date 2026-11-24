"""
create_box action — 在 Maya 中创建一个多边形立方体。
"""

from maya_bridge import register_action


@register_action("create_box")
def create_box(size: float = 1.0, name: str = "pCube1"):
    try:
        import maya.cmds as cmds
        result = cmds.polyCube(w=size, h=size, d=size, name=name)
        return {"created": result[0], "size": size}
    except ImportError:
        # 非 Maya 环境：mock 返回
        return {"created": f"|{name}", "size": size, "mock": True}
