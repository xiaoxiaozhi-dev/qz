# -*- coding: utf-8 -*-
"""Batch rename scene nodes on the Max main thread (Python 2.7 and 3)."""
from __future__ import unicode_literals

from collections import Counter

from max_bridge.server import register_action

try:
    text_type = unicode
except NameError:
    text_type = str

_NAME_PREVIEW = 8


def _name(node):
    value = node.name
    return value.decode("utf-8") if isinstance(value, bytes) else text_type(value)


def _targets(rt, roots, hierarchy):
    """Postorder traversal in selection/child order, using stable node handles."""
    stack = [(node, False) for node in reversed(roots)]
    seen = set()
    while stack:
        node, expanded = stack.pop()
        if expanded:
            yield node
            continue
        handle = int(rt.getHandleByAnim(node))
        if handle in seen:
            continue
        seen.add(handle)
        if not hierarchy:
            yield node
            continue
        stack.append((node, True))
        stack.extend((child, False) for child in reversed(list(node.children)))


def _empty(warning):
    return {"count": 0, "names": [], "warning": warning}


def _check_duplicates(rt):
    nodes = [(node, _name(node)) for node in rt.objects]
    counts = Counter(name.lower() for _, name in nodes)
    duplicates = [(node, name) for node, name in nodes if counts[name.lower()] > 1]
    if not duplicates:
        rt.clearSelection()
        return _empty("没有重名物体")
    rt.select(rt.Array(*(node for node, _ in duplicates)))
    return {"count": len(duplicates),
            "names": [name for _, name in duplicates[:_NAME_PREVIEW]]}


def _delete_char(name, index):
    if -len(name) <= index < len(name):
        index %= len(name)
        return name[:index] + name[index + 1:]
    return name


@register_action("rename_tool")
def rename_tool(op="", prefix="", suffix="", base="", search="", replace="",
                index=0, hierarchy=False, use_prefix=False, use_suffix=False):
    from pymxs import runtime as rt

    if op == "check_duplicates":
        return _check_duplicates(rt)

    selected = list(rt.selection)
    if not selected:
        return _empty("未选中任何物体")

    if op == "prefix":
        if not prefix:
            return _empty("前缀为空，未执行")

        def make_name(name, number):
            return prefix + name

    elif op == "suffix":
        if not suffix:
            return _empty("后缀为空，未执行")

        def make_name(name, number):
            return name + suffix

    elif op == "replace":
        if not search:
            return _empty("查找内容为空，未执行")

        def make_name(name, number):
            return name.replace(search, replace)

    elif op == "rename":
        if not base.strip():
            return _empty("新名为空，未执行")

        def make_name(name, number):
            return "%s%s%02d%s" % (prefix if use_prefix else "", base, number,
                                  suffix if use_suffix else "")

    elif op == "delete_index":
        def make_name(name, number):
            return _delete_char(name, index)

    else:
        return _empty("未知操作：%s" % op)

    targets = list(_targets(rt, selected, hierarchy))
    occupied = Counter(_name(node).lower() for node in rt.objects)
    renamed = []
    skipped = 0
    for number, node in enumerate(targets, 1):
        try:
            old = _name(node)
            requested = make_name(old, number)
            if requested == old or not requested.strip() or "\0" in requested:
                skipped += 1
                continue

            # Max permits duplicates anywhere. Ignore this node's own slot,
            # but protect all other names, including targets still to be processed.
            candidate = requested
            suffix_number = 1
            old_key = old.lower()
            while occupied[candidate.lower()] > int(candidate.lower() == old_key):
                candidate = "%s_%03d" % (requested, suffix_number)
                suffix_number += 1
            if candidate == old:
                skipped += 1
                continue

            node.name = candidate
            actual = _name(node)
        except RuntimeError:
            skipped += 1
            continue

        if actual == old:
            skipped += 1
            continue
        occupied[old_key] -= 1
        occupied[actual.lower()] += 1
        renamed.append(actual)

    result = {"count": len(renamed), "names": renamed[:_NAME_PREVIEW]}
    if skipped:
        result["skipped"] = skipped
    if not renamed:
        result["warning"] = "没有物体被改名"
    return result
