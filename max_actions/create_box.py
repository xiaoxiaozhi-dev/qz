# -*- coding: utf-8 -*-
"""
max_actions/create_box.py —— 在 Max 里创建 Box。

Max 侧 action 的写法与 Maya 侧对应，但 API 不同：
    Maya : cmds.createNode / cmds.getAttr
    Max  : pymxs.runtime（本质是 MAXScript）

⚠️ 本文件跑在 Max 进程的【主线程】里（由 timer 拉起），
   所以可以放心 import pymxs —— 这正是整套架构存在的意义。

⚠️ 用 pymxs 原生 API，**不要拼字符串交给 rt.execute()**：
   1. `rt.execute('delete (getNodeByName "%s")' % name)` 在节点不存在时会炸 ——
      getNodeByName 返回 undefined，delete undefined 直接抛 MAXScript 异常：
          没有与以下项对应的"delete"函数 undefined。
      首次创建必然找不到，所以这个 action 100% 失败（2026-10-18 实测）。
   2. name 来自 widget 的用户输入，拼进脚本带引号/空格就会破坏语句，
      极端情况能注入任意 MAXScript。原生 API 没有这个问题。
"""

from max_bridge.server import register_action


@register_action("create_box")
def create_box(size=10.0, pos=None, name="qz_box"):
    """创建一个 Box。

    size : 边长（Max 的 Box 是 boxSize 三维，默认立方）
    pos  : 位置 [x,y,z]，默认原点
    name : 物体名
    """
    import pymxs
    rt = pymxs.runtime

    try:
        size = float(size)
    except Exception:
        return {"error": "size 必须是数字"}
    if size <= 0:
        return {"error": "size 必须大于 0"}

    if pos is None:
        pos = [0.0, 0.0, 0.0]
    try:
        pos = [float(p) for p in pos]
    except Exception:
        return {"error": "pos 必须是 [x, y, z] 三个数字"}
    if len(pos) != 3:
        return {"error": "pos 必须是 [x, y, z] 三个数字"}

    name = str(name).strip()
    if not name:
        return {"error": "name 不能为空"}

    # 清掉同名物体（避免重名），**必须先判存在**。
    # 原生 API：不存在时 getNodeByName 返回 None，不抛异常。
    replaced = False
    old = rt.getNodeByName(name)
    if old is not None:
        try:
            rt.delete(old)
            replaced = True
        except Exception as e:
            return {"error": "删除同名物体 %r 失败: %s" % (name, e)}

    # pymxs 的构造器接受关键字参数，Point3 要用 rt.Point3 而非 list
    box = rt.Box(name=name,
                 boxSize=rt.Point3(size, size, size),
                 pos=rt.Point3(pos[0], pos[1], pos[2]))

    return {
        "created": name,
        "size": size,
        "pos": pos,
        "replaced": replaced,      # 之前有同名物体被顶掉了
        "node": rt.getNodeByName(name).name,
    }
