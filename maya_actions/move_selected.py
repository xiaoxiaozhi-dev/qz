"""
move_selected action — 把选中物体沿指定轴平移。

一个 action 承担两个方向：用 axis 参数区分（X / -X / Y / -Y / Z / -Z），
而不是写成 move_left / move_right 两个 action。
好处是轴向换成 Z 只是传参，不用多写一半代码。
"""

from maya_bridge import register_action

# 允许的方向：单字母 + 可选负号
_VALID_AXES = ("x", "-x", "y", "-y", "z", "-z")


@register_action("move_selected")
def move_selected(axis: str = "x", distance: float = 1.0, use_world: bool = True):
    """沿 axis 方向移动 distance 个单位。

    axis:      "x"/"-x"/"y"/"-y"/"z"/"-z"，大小写均可
    distance:  移动距离，默认 1.0（负值会自动取反）
    use_world: True 世界坐标 / False 物体本地坐标
    返回 {"moved": [物体名...], "axis": ..., "distance": ...}
    """
    a = (axis or "x").strip().lower()
    if a not in _VALID_AXES:
        return {"error": f"axis 必须是 {_VALID_AXES} 之一，收到 {axis!r}"}

    sign = -1.0 if a.startswith("-") else 1.0
    ai = "xyz".index(a[-1])
    delta = [0.0, 0.0, 0.0]
    delta[ai] = sign * abs(float(distance))

    try:
        import maya.cmds as cmds
    except ImportError:
        # 非 Maya 环境（本地测试）伪造返回
        return {"moved": ["mock|pCube1"], "axis": a, "distance": delta[ai], "mock": True}

    sel = cmds.ls(selection=True, flatten=True)
    if not sel:
        return {"moved": [], "axis": a, "distance": delta[ai], "message": "没有选中物体"}

    space = "world" if use_world else "object"
    cmds.xform(sel, translation=delta, relative=True, worldSpace=(space == "world"))
    return {"moved": sel, "axis": a, "distance": delta[ai]}