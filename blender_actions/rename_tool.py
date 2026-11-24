"""Batch rename Blender objects from the bridge's main-thread timer."""

from collections import defaultdict
from contextlib import nullcontext
import re
import threading


_NAME_PREVIEW = 8
_NUMERIC_SUFFIX = re.compile(r"\.[0-9]{3,}\Z")


def _empty(warning):
    return {"count": 0, "names": [], "warning": warning}


def _delete_character(name, index):
    if index < 0:
        index += len(name)
    if 0 <= index < len(name):
        return name[:index] + name[index + 1:]
    return name


def _targets(view_layer, scene, hierarchy):
    objects = list(view_layer.objects)
    selected = [obj for obj in objects if obj.select_get(view_layer=view_layer)]
    if not hierarchy or not selected:
        return selected

    children = defaultdict(list)
    for obj in scene.objects:
        if obj.parent is not None:
            children[obj.parent].append(obj)

    # Visit descendants first, including across excluded intermediate parents.
    # A target still has to belong to the current view layer.
    in_layer = set(objects)
    seen = set()
    ordered = []
    for root in selected:
        stack = [(root, False)]
        while stack:
            obj, expanded = stack.pop()
            if expanded:
                if obj in in_layer:
                    ordered.append(obj)
            elif obj not in seen:
                seen.add(obj)
                stack.append((obj, True))
                stack.extend((child, False) for child in reversed(children[obj]))
    return ordered


def _check_duplicates(context):
    if context.mode != "OBJECT":
        return _empty("请切换到物体模式后检查重名")

    view_layer = context.view_layer
    groups = defaultdict(list)
    for obj in view_layer.objects:
        stem = _NUMERIC_SUFFIX.sub("", obj.name)
        groups[stem or obj.name].append(obj)
    duplicates = [obj for group in groups.values() if len(group) > 1 for obj in group]

    selectable = set(context.selectable_objects)
    selected = [obj for obj in duplicates if obj in selectable]
    selected_set = set(selected)
    for obj in view_layer.objects:
        desired = obj in selected_set
        if obj.select_get(view_layer=view_layer) != desired:
            obj.select_set(desired, view_layer=view_layer)
    if view_layer.objects.active not in selected_set:
        view_layer.objects.active = next(iter(selected), None)

    count = len(selected)
    result = {"count": count, "names": [obj.name_full for obj in selected[:_NAME_PREVIEW]],
              "groups": sum(len(group) > 1 for group in groups.values())}
    if count < len(duplicates):
        result["skipped"] = len(duplicates) - count
    if not duplicates:
        result["warning"] = "当前视图层没有同名或带数字后缀的同基名对象组"
    return result


def _apply_names(targets, make_name):
    names = []
    adjustments = []
    count = adjusted = readonly = 0
    for number, obj in enumerate(targets, start=1):
        old = obj.name
        requested = make_name(old, number)
        if not requested or requested == old:
            continue
        if not obj.is_editable or obj.is_property_readonly("name"):
            readonly += 1
            continue

        # Keep Blender's native collision suffixes and leave the occupying ID alone.
        obj.rename(requested, mode="NEVER")
        actual = obj.name
        if actual != requested:
            adjusted += 1
            if len(adjustments) < _NAME_PREVIEW:
                adjustments.append([requested, actual])
        if actual != old:
            count += 1
            if len(names) < _NAME_PREVIEW:
                names.append(actual)

    result = {"count": count, "names": names}
    if count < len(targets):
        result["skipped"] = len(targets) - count
    if readonly:
        result["readonly"] = readonly
    if adjusted:
        result.update(adjusted=adjusted, adjustments=adjustments)
    if not count:
        result["warning"] = "没有对象被改名"
    return result


def rename_tool(op="", prefix="", suffix="", base="", search="", replace="",
                index=0, hierarchy=False, use_prefix=False, use_suffix=False):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("批量重命名必须在 Blender 主线程执行")
    import bpy

    context = bpy.context
    window = context.window
    if window is None and context.window_manager:
        window = next(iter(context.window_manager.windows), None)
    with context.temp_override(window=window) if window else nullcontext():
        if context.view_layer is None:
            return _empty("Blender 当前没有可用的视图层")
        if op == "check_duplicates":
            return _check_duplicates(context)

        numbered_prefix = prefix if use_prefix else ""
        numbered_suffix = suffix if use_suffix else ""
        builders = {
            "prefix": (not prefix and "前缀为空", lambda name, _: prefix + name),
            "suffix": (not suffix and "后缀为空", lambda name, _: name + suffix),
            "replace": (not search and "查找内容为空", lambda name, _: name.replace(search, replace)),
            "rename": (not base and "新名为空", lambda _, i: f"{numbered_prefix}{base}{i:02d}{numbered_suffix}"),
            "delete_index": ("", lambda name, _: _delete_character(name, index)),
        }
        if op not in builders:
            return _empty(f"未知操作 {op}")
        warning, make_name = builders[op]
        if warning:
            return _empty(warning)

        targets = _targets(context.view_layer, context.scene, hierarchy)
        if not targets:
            return _empty("当前视图层没有选中对象")
        return _apply_names(targets, make_name)


ACTIONS = {"rename_tool": rename_tool}
