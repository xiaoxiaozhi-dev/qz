"""
rename_tool action — 批量重命名（前缀 / 后缀 / 替换 / 编号 / 删字 / 查重名）。

⚠️ 这个文件跑在 **Maya 进程内**，Maya 2020 及更早还是 Python 2.7 ——
   所以这里不能出现 f-string、类型注解、keyword-only 参数。
   名字生成那部分抽成纯函数，不依赖 maya.cmds，方便本地单测。

⚠️ 所有改名都按 **uuid** 定位（不是名字）：
   批量改名的典型坑是「父级改名后子级路径变了」，
   每一轮重新用 uuid 取当前长名就天然免疫，也不需要担心顺序。
"""

from maya_bridge import register_action

# 日志里最多列几个名字（一次改 200 个的话日志会刷屏）
_NAME_PREVIEW = 8


# ── 纯函数：只算名字，不碰 Maya（单测直接测这几个）──────────
def apply_prefix(name, prefix):
    return "%s%s" % (prefix, name)


def apply_suffix(name, suffix):
    return "%s%s" % (name, suffix)


def apply_replace(name, search, replace):
    """查找替换。search 为空时返回原名（调用方需先拦住空查找）。"""
    if not search:
        return name
    return name.replace(search, replace)


def build_numbered(base, i, prefix="", suffix=""):
    """前缀 + 基名 + 两位序号 + 后缀，如 base="bone_" i=1 -> bone_01。"""
    return "%s%s%02d%s" % (prefix, base, i, suffix)


def pop_char(name, index):
    """删掉第 index 个字符（0 起，支持负数从尾部数）。

    返回 None 表示「不该改」：名字被删空（Maya 不接受空名）或下标越界。
    """
    chars = list(name)
    if not chars:
        return None
    try:
        chars.pop(index)
    except IndexError:
        return None
    result = "".join(chars)
    return result or None


# ── Maya 侧 ──────────────────────────────────────────
def _maya():
    import maya.cmds as cmds
    return cmds


def _uuid_of(cmds, name):
    """名字 → uuid。物体已不存在时返回 None。"""
    hits = cmds.ls(name, uuid=True) or []
    return hits[0] if hits else None


def _short_of(cmds, uuid_):
    """uuid → 当前短名（每次现取，所以父级改名也不会失效）。"""
    hits = cmds.ls(uuid_, l=True) or []
    if not hits:
        return None
    return hits[0].split("|")[-1]


def _rename_by_uuid(cmds, uuid_, new_name):
    """按 uuid 改名。返回 True / False（不存在、失败、名字没变化都算 False）。"""
    hits = cmds.ls(uuid_, l=True) or []
    if not hits:
        return False
    long_name = hits[0]
    if long_name.split("|")[-1] == new_name:
        return False
    try:
        cmds.rename(long_name, new_name)
    except Exception:
        return False
    return True


def _target_uuids(cmds, hierarchy):
    """当前选择 → uuid 列表。

    hierarchy=True 时摊平每个选中物体的所有后代（后代在前、父级在后，
    保持原有的「先改子后改父」习惯）。
    """
    selected = cmds.ls(selection=True) or []
    if not selected:
        return []

    names = []
    if hierarchy:
        for obj in selected:
            names.extend(cmds.listRelatives(obj, allDescendents=True,
                                            fullPath=True) or [])
            names.append(obj)
    else:
        names = list(selected)

    ordered = []
    seen = set()
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)

    uuids = []
    for n in ordered:
        u = _uuid_of(cmds, n)
        if u is not None:
            uuids.append(u)
    return uuids


def _empty(warning):
    return {"count": 0, "names": [], "warning": warning}


def _check_duplicates(cmds):
    """Maya 里重名的物体，ls 会返回带 '|' 的部分路径 —— 用它做判据。"""
    duplicates = [n for n in (cmds.ls(objectsOnly=True) or []) if "|" in n]
    cmds.select(duplicates)
    if not duplicates:
        return _empty("没有重名物体")
    return {"count": len(duplicates), "names": duplicates[:_NAME_PREVIEW]}


@register_action("rename_tool")
def rename_tool(op="", prefix="", suffix="", base="", search="", replace="",
                index=0, hierarchy=False, use_prefix=False, use_suffix=False):
    """op 取值：

    prefix / suffix        加前缀、加后缀
    replace                查找替换（search → replace）
    rename                 基名 + 两位序号，可选用 prefix/suffix 字段
    delete_index           删第 index 个字符（0 起，UI 里是 1 起）
    check_duplicates       找出重名物体并选中
    """
    try:
        cmds = _maya()
    except ImportError:
        # 非 Maya 环境（本地单测）：只验证参数能走到这里，不做事
        return {"count": 0, "names": [], "mock": True,
                "warning": "非 Maya 环境，未执行"}

    if op == "check_duplicates":
        return _check_duplicates(cmds)

    uuids = _target_uuids(cmds, bool(hierarchy))
    if not uuids:
        return _empty("未选中任何物体")

    # 每个 op 只是「短名 + 序号 → 新短名」的差别，统一进一个循环
    if op == "prefix":
        if not prefix:
            return _empty("前缀为空，未执行")

        def make_name(short, i):
            return apply_prefix(short, prefix)

    elif op == "suffix":
        if not suffix:
            return _empty("后缀为空，未执行")

        def make_name(short, i):
            return apply_suffix(short, suffix)

    elif op == "replace":
        if not search:
            return _empty("查找内容为空，未执行")

        def make_name(short, i):
            return apply_replace(short, search, replace)

    elif op == "rename":
        if not base:
            return _empty("新名为空，未执行")
        p = prefix if use_prefix else ""
        s = suffix if use_suffix else ""

        def make_name(short, i):
            return build_numbered(base, i, p, s)

    elif op == "delete_index":
        def make_name(short, i):
            return pop_char(short, index)

    else:
        return _empty("未知操作：%s" % op)

    renamed = []
    skipped = 0
    for i, u in enumerate(uuids):
        short = _short_of(cmds, u)
        if short is None:
            skipped += 1
            continue
        new_name = make_name(short, i + 1)
        if not new_name or new_name == short:
            # 空名 / 没变化（比如替换词根本没出现）都算跳过
            skipped += 1
            continue
        if _rename_by_uuid(cmds, u, new_name):
            renamed.append(new_name)
        else:
            skipped += 1

    result = {"count": len(renamed), "names": renamed[:_NAME_PREVIEW]}
    if skipped:
        result["skipped"] = skipped
    if not renamed:
        result["warning"] = "没有物体被改名"
    return result
