# -*- coding: utf-8 -*-
"""
max_actions/move_selected.py —— 把选中的物体沿某轴移动一段距离。

与 Maya 侧同名 action 行为一致（一个 action 用 axis 参数区分方向），
这样中台的同一个插件 UI 可以复用于两个 DCC。

⚠️ 用 pymxs 原生 API 遍历 selection，不要拼字符串交给 rt.execute()：
   数字拼错/类型不合法会让 MAXScript 在主线程上抛异常，
   原生 API 出错时抛的是 Python 异常，更好定位。
"""

from max_bridge.server import register_action

# Maya 用 x/y/z，Max 的 position 也用 x/y/z，但旋转是 z-up 的差异
_AXES = {"x": 0, "y": 1, "z": 2}


@register_action("move_selected")
def move_selected(axis="x", distance=1.0):
    """沿 axis 方向移动所有选中物体 distance 个单位。

    axis     : x / y / z（也接受 -x 形式表示负方向）
    distance : 距离，可为负
    """
    import pymxs
    rt = pymxs.runtime

    a = str(axis).strip().lower().lstrip("+-")
    if a not in _AXES:
        return {"error": "axis 必须是 x / y / z（收到 %r）" % axis}

    try:
        distance = float(distance)
    except Exception:
        return {"error": "distance 必须是数字"}

    sign = -1.0 if str(axis).strip().startswith("-") else 1.0
    idx = _AXES[a]

    # 原生遍历：rt 是 MAXScript runtime，selection 可直接当 Python 序列迭代。
    # 逐个取节点再改 position —— 比拼 MAXScript 字符串稳，且能定位到具体是哪个物体失败。
    try:
        targets = list(rt.selection)
    except Exception as e:
        return {"error": "读取选择失败: %s" % e}

    if not targets:
        return {"moved": 0, "error": "没有选中任何物体"}

    delta = [0.0, 0.0, 0.0]
    delta[idx] = distance * sign
    step = rt.Point3(delta[0], delta[1], delta[2])

    moved, failed = 0, []
    for obj in targets:
        try:
            p = obj.position
            obj.position = rt.Point3(p.x + step.x, p.y + step.y, p.z + step.z)
            moved += 1
        except Exception as e:
            failed.append("%s: %s" % (getattr(obj, "name", "?"), e))

    out = {
        "moved": moved,
        "axis": ("-" if sign < 0 else "") + a,
        "distance": distance * sign,
    }
    if failed:
        out["error"] = "部分物体移动失败 -> " + "; ".join(failed)
    return out
