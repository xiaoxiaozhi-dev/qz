"""
create_sphere action — 在 Maya 中创建一个多边形球。
"""

from maya_bridge import register_action


@register_action("create_sphere")
def create_sphere(radius: float = 1.0, subdiv: int = 20, name: str = "pSphere1"):
    try:
        import maya.cmds as cmds
        result = cmds.polySphere(r=radius, sx=subdiv, sy=subdiv, name=name)
        return {"created": result[0], "radius": radius, "subdiv": subdiv}
    except ImportError:
        return {"created": f"|{name}", "radius": radius, "subdiv": subdiv, "mock": True}
