"""Scene operations for Blender's project skin-weight card."""
from array import array
from collections import defaultdict
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
import struct

from blender_actions import _skin_weight_geometry as geo
from blender_actions.skin_transfer_maya import _fbx_name


def _result(mesh, error):
    return dict(mesh=mesh, error=str(error), status="skipped" if isinstance(error, ValueError) else "failed")


def _identity(scene, obj):
    return dict(scene=scene.session_uid, object=obj.session_uid, data=obj.data.session_uid)


def _check_mesh(obj):
    if obj.library or obj.override_library or obj.data.library or obj.data.users != 1:
        raise ValueError("需要可写的本地单用户网格")
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    if not obj.data.vertices:
        raise ValueError("网格没有顶点")


def _binding(scene, obj, required=True):
    skins = [modifier for modifier in obj.modifiers if modifier.type == "ARMATURE"]
    if not skins and not required:
        return None, None
    if len(skins) != 1:
        raise ValueError("需要恰好一个骨架修改器")
    skin = skins[0]
    rig = skin.object
    if rig is None or rig.type != "ARMATURE" or scene.objects.get(rig.name) != rig:
        raise ValueError("骨架修改器必须指向当前场景中的骨架")
    if not skin.use_vertex_groups or skin.use_bone_envelopes or skin.vertex_group or skin.use_multi_modifier:
        raise ValueError("需要仅使用骨骼顶点组的单层骨架修改器")
    return skin, rig


def _deform_bones(rig):
    return [bone for bone in rig.data.bones if bone.use_deform]


def _match_bones(rig, names):
    candidates = defaultdict(set)
    for bone in _deform_bones(rig):
        candidates[bone.name].add(bone.name)
        candidates[_fbx_name(bone.name)].add(bone.name)
    matches = []
    for name in names:
        short = name.rsplit("|", 1)[-1]
        found = candidates[short] | candidates[short.rsplit(":", 1)[-1]]
        if not found:
            raise ValueError(f"目标骨架缺少文件骨骼：{name}")
        if len(found) != 1:
            raise ValueError(f"无法唯一匹配文件骨骼 {name}：" + "、".join(sorted(found)))
        matches.append(next(iter(found)))
    if not matches or len(set(matches)) != len(matches):
        raise ValueError("文件骨骼无法与目标骨骼逐一对应")
    return matches


def _find_rig(scene, rig, bones):
    names = [bone["name"] for bone in bones]
    if rig is not None:
        mapped = _match_bones(rig, names)
        return rig, dict(zip((bone["index"] for bone in bones), mapped))
    matches = []
    details = []
    for candidate in scene.objects:
        if candidate.type != "ARMATURE":
            continue
        try:
            mapped = _match_bones(candidate, names)
        except ValueError as exc:
            details.append(f"{candidate.name}：{exc}")
            continue
        matches.append((candidate, mapped))
    if len(matches) != 1:
        if matches:
            raise ValueError("有多个骨架匹配文件骨骼：" + "、".join(candidate.name for candidate, _ in matches))
        raise ValueError("场景中没有可唯一匹配文件骨骼的骨架" + ("；" + "；".join(details[:3]) if details else ""))
    rig, mapped = matches[0]
    return rig, dict(zip((bone["index"] for bone in bones), mapped))


def _linear(skin):
    if skin is not None and skin.use_deform_preserve_volume:
        raise ValueError("骨架启用了保留体积，无法与此文件的线性蒙皮无损互通")


def _prefix(directory, entry):
    index = entry["index"]
    if type(index) is not int or index < 0:
        raise ValueError("模型编号无效")
    return directory / f"mesh-{index}"


def _capture(obj, bone_names, prefix):
    columns = {group.index: column for column, name in enumerate(bone_names)
               if (group := obj.vertex_groups.get(name)) is not None}
    width = len(bone_names)
    vertices = obj.data.vertices
    batch = max(1, min(2048, 262144 // width))
    with prefix.with_suffix(".weights").open("wb") as stream:
        for first in range(0, len(vertices), batch):
            block = vertices[first:first + batch]
            weights = array("d", [0.]) * (len(block) * width)
            for row, vertex in enumerate(block):
                for item in vertex.groups:
                    column = columns.get(item.group)
                    if column is not None:
                        weights[row * width + column] = item.weight
            geo.write_values(stream, weights)


def _snapshot(context, operation, directory):
    meshes, errors = [], []
    scene, layer = context.scene, context.view_layer
    for obj in layer.objects:
        if obj.type != "MESH" or not obj.select_get(view_layer=layer):
            continue
        try:
            _check_mesh(obj)
            entry = dict(index=len(meshes), mesh=obj.name, name=obj.name,
                         vertices=len(obj.data.vertices), identity=_identity(scene, obj))
            if operation != "context":
                skin, rig = _binding(scene, obj)
                names = [bone.name for bone in _deform_bones(rig)]
                if not names:
                    raise ValueError("骨架没有变形骨骼")
                prefix = _prefix(directory, entry)
                if operation == "export":
                    _linear(skin)
                    entry["geometry"] = geo.save_geometry(prefix, geo.geometry(obj, skin))
                    geo.write_array(prefix.with_suffix(".dq"), array("d", [0.]) * entry["vertices"])
                    entry["skinning_method"] = 0
                _capture(obj, names, prefix)
                entry.update(bone_names=names, influences=len(names), rig=rig.session_uid)
            meshes.append(entry)
        except (ValueError, RuntimeError, OSError) as exc:
            errors.append(_result(obj.name, exc))
    return dict(meshes=meshes, errors=errors)


def _target(context, entry, vertex_key):
    obj = context.view_layer.objects.get(entry["mesh"])
    if obj is None or obj.type != "MESH" or _identity(context.scene, obj) != entry["identity"]:
        raise ValueError("目标场景或网格已变化，请重试")
    _check_mesh(obj)
    if len(obj.data.vertices) != entry[vertex_key]:
        raise ValueError("目标顶点数已变化，请重试")
    return obj


def _map(context, directory, entry, decimals):
    obj = _target(context, entry, "target_vertices")
    skin, rig = _binding(context.scene, obj, required=False)
    _linear(skin)
    rig, names = _find_rig(context.scene, rig, entry.get("match_bones", entry["bones"]))
    prefix = _prefix(directory, entry)
    source = geo.read_geometry(prefix, entry)
    target = geo.geometry(obj, skin)
    if entry["transfer_mode"] == "vertex_id":
        geo.verify_indices(source, target, decimals)
        report = {}
    elif entry["transfer_mode"] == "closest_point":
        report = geo.map_surface(prefix, source, target, decimals)
    else:
        raise ValueError("未知权重导入方式")
    return dict(report, index=entry["index"], target_geometry=geo.signature(target),
                target_rig=rig.session_uid, target_names=names)


@contextmanager
def _object_mode(context):
    import bpy

    edited = context.mode == "EDIT_MESH"
    if edited:
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if edited:
            bpy.ops.object.mode_set(mode="EDIT")


def _write_groups(obj, weights, ids):
    groups = obj.vertex_groups
    existing = {groups[name].index: name for name in weights if name in groups}
    previous = {name: defaultdict(list) for name in existing.values()}
    for index in ids:
        for item in obj.data.vertices[index].groups:
            name = existing.get(item.group)
            if name is not None:
                previous[name][item.weight].append(index)
    changed = {name: values for name, values in weights.items() if previous.get(name) != values}
    locked = [name for name in changed if name in groups and groups[name].lock_weight]
    if locked:
        raise ValueError("骨骼顶点组已锁定：" + "、".join(locked))
    active = groups.active_index
    created, touched = [], []
    try:
        for name, rows in changed.items():
            group = groups.get(name)
            if group is None:
                group = groups.new(name=name)
                created.append(group)
                if group.name != name:
                    raise RuntimeError(f"无法创建精确骨骼顶点组：{name}")
            else:
                touched.append(name)
                group.remove(ids)
            for weight, vertices in rows.items():
                group.add(vertices, weight, "REPLACE")
    except Exception as exc:
        try:
            for group in reversed(created):
                groups.remove(group)
            for name in touched:
                group = groups[name]
                group.remove(ids)
                for weight, vertices in previous[name].items():
                    group.add(vertices, weight, "REPLACE")
        except Exception as rollback:
            raise RuntimeError(f"权重写入失败，恢复也失败：{exc}；{rollback}") from exc
        raise RuntimeError(f"权重写入失败，已恢复原权重：{exc}") from exc
    finally:
        if groups:
            groups.active_index = active


def _check_ids(ids, vertices):
    if any(index >= vertices for index in ids) or any(a >= b for a, b in zip(ids, ids[1:])):
        raise ValueError("顶点编号越界或重复")


def _read_skin(prefix, entry, names):
    vertices, count = entry["vertices"], entry["nonzero"]
    dq = geo.read_array(prefix.with_suffix(".dq"), vertices)
    if entry["skinning_method"] != 0 or any(dq):
        raise ValueError("此文件含 DQ 或混合蒙皮数据，Blender 无法无损导入")
    offsets = geo.read_array(prefix.with_suffix(".offsets"), vertices + 1, "I")
    columns = geo.read_array(prefix.with_suffix(".cols"), count, "I")
    values = geo.read_array(prefix.with_suffix(".values"), count)
    if offsets[0] != 0 or offsets[-1] != count or any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("权重文件的顶点行偏移无效")
    if any(column not in names for column in columns):
        raise ValueError("权重文件包含未匹配的骨骼列")
    if any(not isfinite(value) or not 0. < value <= 1. + 1e-12 for value in values):
        raise ValueError("Blender 顶点组不能表示该权重值")
    stored = array("f", values)
    if any(not 0. < value <= 1. for value in stored):
        raise ValueError("权重超出 Blender 顶点组精度或范围")
    weights = {name: defaultdict(list) for name in names.values()}
    for vertex, (first, last) in enumerate(zip(offsets, offsets[1:])):
        row = columns[first:last]
        if any(a >= b for a, b in zip(row, row[1:])):
            raise ValueError("同一顶点的骨骼列重复或顺序无效")
        for column, weight in zip(row, stored[first:last]):
            weights[names[column]][weight].append(vertex)
    return weights


def _import(context, directory, entry):
    obj = _target(context, entry, "target_vertices")
    skin, rig = _binding(context.scene, obj, required=False)
    _linear(skin)
    rig, names = _find_rig(context.scene, rig, entry.get("match_bones", entry["bones"]))
    previous_names = {int(column): name for column, name in entry["target_names"].items()}
    if rig.session_uid != entry["target_rig"] or any(previous_names.get(c) != n for c, n in names.items()):
        raise ValueError("目标匹配骨架已变化，请重新导入")
    if geo.signature(geo.geometry(obj, skin)) != entry["target_geometry"]:
        raise ValueError("目标蒙皮输入几何已变化，请重新导入")
    if entry["vertices"] != entry["target_vertices"]:
        raise ValueError("目标权重行数不匹配")
    prefix = _prefix(directory, entry)
    weights = _read_skin(prefix, entry, {bone["index"]: names[bone["index"]] for bone in entry["bones"]})
    for bone in _deform_bones(rig):
        if bone.name in obj.vertex_groups:
            weights.setdefault(bone.name, {})
    interpolated = None
    if entry["transfer_mode"] == "closest_point":
        interpolated = geo.read_array(prefix.with_suffix(".interpolated_ids"), entry["interpolated_vertices"], "I")
        _check_ids(interpolated, entry["vertices"])
    created = None
    with _object_mode(context):
        try:
            if skin is None:
                created = obj.modifiers.new(name="Armature", type="ARMATURE")
                created.object = rig
            _write_groups(obj, weights, list(range(entry["vertices"])))
        except Exception:
            if created is not None:
                obj.modifiers.remove(created)
            raise
    report = dict(name=obj.name, vertices=entry["vertices"], skinning_method=0, created_skin=created is not None,
                  transfer_mode=entry["transfer_mode"])
    if interpolated is not None:
        report.update(direct_vertices=entry["direct_vertices"], interpolated_vertices=len(interpolated))
    return report, (obj, interpolated) if interpolated is not None else None


def _patch(context, directory, entry, operation):
    obj = _target(context, entry, "vertices")
    _, rig = _binding(context.scene, obj)
    names = [bone.name for bone in _deform_bones(rig)]
    if rig.session_uid != entry["rig"] or names != entry["bone_names"]:
        raise ValueError("目标骨架或影响顺序已变化，请重新检测")
    prefix = _prefix(directory, entry)
    ids = geo.read_array(prefix.with_suffix(".ids"), entry["count"], "I")
    _check_ids(ids, entry["vertices"])
    if operation == "select_excess":
        return len(ids), (obj, ids)
    if not ids:
        return 0, None
    width = entry["width"]
    if type(width) is not int or not 0 < width <= len(names):
        raise ValueError("修复补丁宽度无效")
    columns = geo.read_array(prefix.with_suffix(".cols"), len(ids) * width, "I")
    values = geo.read_array(prefix.with_suffix(".values"), len(ids) * width)
    if any(column >= len(names) for column in columns) or any(not 0. <= v <= 1. for v in values):
        raise ValueError("修复补丁包含无效骨骼列或权重")
    desired = {name: defaultdict(list) for name in names if name in obj.vertex_groups}
    current_cols = {group.index: column for column, name in enumerate(names)
                    if (group := obj.vertex_groups.get(name)) is not None}
    read_row = struct.Struct(f"<{len(names)}d")
    with prefix.with_suffix(".weights").open("rb") as stream:
        for row, index in enumerate(ids):
            stream.seek(index * read_row.size)
            previous = read_row.unpack(stream.read(read_row.size))
            current = [0.] * len(names)
            for item in obj.data.vertices[index].groups:
                column = current_cols.get(item.group)
                if column is not None:
                    current[column] = item.weight
            if tuple(current) != previous:
                raise ValueError("超限顶点的权重已变化，请重新检测")
            start = row * width
            for column, weight in zip(columns[start:start + width], values[start:start + width]):
                if weight:
                    desired.setdefault(names[column], defaultdict(list))[weight].append(index)
    with _object_mode(context):
        _write_groups(obj, desired, list(ids))
    return len(ids), None


def _select(context, items):
    import bmesh
    import bpy

    layer = context.view_layer
    for obj, _ in items:
        if not obj.visible_get(view_layer=layer) or obj.hide_select:
            raise ValueError(f"{obj.name} 在当前视图层不可选")
    if context.mode != "EDIT_MESH" or any(obj.mode != "EDIT" for obj, _ in items):
        if context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj, _ in items:
            obj.select_set(True, view_layer=layer)
        if layer.objects.active not in {obj for obj, _ in items}:
            layer.objects.active = items[0][0]
        bpy.ops.object.mode_set(mode="EDIT")
    context.tool_settings.mesh_select_mode = (True, False, False)
    selected = 0
    for obj, ids in items:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.select_mode = {"VERT"}
        bm.select_history.clear()
        for elements in (bm.faces, bm.edges, bm.verts):
            for element in elements:
                element.select_set(False)
        for index in ids:
            vertex = bm.verts[index]
            if not vertex.hide:
                vertex.select_set(True)
                selected += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected


def _run(context, operation, directory, meshes, position_decimals):
    if operation in ("snapshot", "export", "context"):
        return _snapshot(context, operation, directory)
    if type(position_decimals) is not int or not 0 <= position_decimals <= 12:
        raise ValueError("坐标匹配小数位无效")
    reports, errors, selection = [], [], []
    count = 0
    for entry in meshes:
        try:
            if operation == "map":
                reports.append(_map(context, directory, entry, position_decimals))
                continue
            if operation == "import":
                report, item = _import(context, directory, entry)
                reports.append(report)
            else:
                affected, item = _patch(context, directory, entry, operation)
                count += affected
            if item is not None:
                selection.append(item)
        except (ValueError, RuntimeError, OSError, KeyError, struct.error) as exc:
            errors.append(_result(entry["mesh"], exc))
    selected, selection_changed = 0, False
    if selection:
        try:
            selected = _select(context, selection)
            selection_changed = True
            if selected < sum(len(ids) for _, ids in selection):
                errors.append(_result("选择", ValueError("部分目标顶点处于隐藏状态")))
        except (ValueError, RuntimeError) as exc:
            errors.append(_result("选择", exc))
    return dict(reports=reports, errors=errors, count=selected if operation == "select_excess" else count,
                selection_changed=selection_changed, selected_vertices=selected)


def run(operation, directory, meshes=(), position_decimals=3, scene=None):
    import bpy

    if operation not in ("snapshot", "export", "context", "map", "import", "select_excess", "limit"):
        raise ValueError("未知蒙皮权重动作")
    path = Path(directory).resolve()
    if not path.is_dir() or not path.name.startswith("qz-skin-transfer-"):
        raise ValueError("无效的蒙皮权重交换目录")
    context = bpy.context
    window = context.window or next(iter(context.window_manager.windows), None)
    if window is None or (scene is not None and scene != window.scene.session_uid):
        raise ValueError("Blender 场景或窗口已变化")
    with context.temp_override(window=window):
        if context.mode not in ("OBJECT", "EDIT_MESH"):
            raise ValueError("请切换到 Blender 物体模式或网格编辑模式")
        return _run(context, operation, path, meshes, position_decimals)
