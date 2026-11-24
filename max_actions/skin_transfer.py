# -*- coding: utf-8 -*-
"""Max 主线程接收 Maya 全量权重；本模块使用 Python 2 兼容语法。"""
from array import array
import os
import sys
import time

from max_bridge.server import register_action
from max_bridge import server as _bridge_server

try:
    text_type = unicode
except NameError:
    text_type = str

_clock = getattr(time, "perf_counter", time.time)
_BULK_MIN_VERTICES = 1024
_BULK_MAX_ENTRIES = 1000000


def _progress(message):
    report = getattr(_bridge_server, "set_progress", None)
    if report is not None:
        report(message)


def _array_file(path, count, code):
    result = array(code)
    if os.path.getsize(path) != count * result.itemsize:
        raise ValueError(u"权重文件长度不匹配：%s" % os.path.basename(path))
    with open(path, "rb") as handle:
        result.fromfile(handle, count)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def _payload(directory, mesh):
    prefix = os.path.join(directory, "mesh-%d" % mesh["index"])
    vertices, nonzero, width = mesh["vertices"], mesh["nonzero"], mesh["influences"]
    offsets = _array_file(prefix + ".offsets", vertices + 1, "I")
    columns = _array_file(prefix + ".cols", nonzero, "I")
    weights = _array_file(prefix + ".values", nonzero, "d")
    dq = _array_file(prefix + ".dq", vertices, "d")
    if offsets[0] != 0 or offsets[-1] != nonzero or any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError(u"权重分段数量错误，或存在全零顶点")
    allowed = set(bone["index"] for bone in mesh["bones"])
    if any(column >= width or column not in allowed for column in columns):
        raise ValueError(u"权重骨骼编号不匹配")
    return offsets, columns, weights, dq


def _skin_modifiers(rt, node):
    return [modifier for modifier in node.modifiers if rt.classOf(modifier) == rt.Skin]


def _activate(rt, node, skin=None):
    _progress(u"选择目标模型")
    rt.select(node)
    _progress(u"切换修改面板")
    rt.setCommandPanelTaskMode(rt.Name("modify"))
    if skin is not None:
        _progress(u"激活目标 Skin")
        rt.modPanel.setCurrentObject(skin, node=node)


def _bone_map(rt, skin):
    return {text_type(rt.skinOps.GetBoneName(skin, index, 0)): index
            for index in range(1, rt.skinOps.GetNumberBones(skin) + 1)}


def _close_weight_windows(rt, skin):
    # Redraw suppression does not stop these floating editors updating for
    # every Skin write. Keep them closed, as the local file importer does.
    for label, query, close in (
            (u"权重工具", rt.skinOps.isWeightToolOpen, rt.skinOps.closeWeightTool),
            (u"权重表", rt.skinOps.isWeightTableOpen, rt.skinOps.closeWeightTable)):
        if query(skin):
            _progress(u"关闭 Max " + label)
            close(skin)


def _supports_bone_groups(rt, vertices):
    if vertices < _BULK_MIN_VERTICES:
        return False
    try:
        # Max 2020's tested primitive takes skin/bone/vertex-array/weight-array.
        # Other versions have different signatures; keep the original writer.
        return int(rt.maxVersion()[0]) == 22000 and callable(rt.skinOps.SetBoneWeights)
    except Exception:
        return False


def _bulk_source_ok(payload):
    offsets, columns, weights, dq = payload
    if len(weights) > _BULK_MAX_ENTRIES or any(value > 1. for value in weights):
        return False
    return all(abs(sum(weights[first:last]) - 1.) <= 1e-6
               for first, last in zip(offsets, offsets[1:]))


def _bone_groups(rt, skin, payload, columns_to_ids):
    offsets, columns, weights, dq = payload
    if not _bulk_source_ok(payload):
        return None
    groups, flags, seeds, entries, unmodified = {}, [], {}, 0, False
    get_count = rt.skinOps.GetVertexWeightCount
    get_bone = rt.skinOps.GetVertexWeightBoneID
    get_flag = rt.skinOps.isUnNormalizeVertex
    get_modified = rt.skinOps.IsVertexModified
    for vertex in range(len(dq)):
        count = int(get_count(skin, vertex + 1))
        if count == 0:
            return None
        entries += count
        if entries > _BULK_MAX_ENTRIES:
            return None
        existing = set(int(get_bone(skin, vertex + 1, slot)) for slot in range(1, count + 1))
        wanted = {columns_to_ids[columns[index]]: weights[index]
                  for index in range(offsets[vertex], offsets[vertex + 1])}
        if abs(sum(wanted.values()) - 1.) > 1e-6:
            return None
        missing = set(wanted) - existing
        entries += len(missing)
        if entries > _BULK_MAX_ENTRIES:
            return None
        for bone_id in missing:
            seeds.setdefault(bone_id, {})[vertex + 1] = wanted[bone_id]
        for bone_id in existing | set(wanted):
            groups.setdefault(bone_id, {})[vertex + 1] = wanted.get(bone_id, 0.)
        flags.append(bool(get_flag(skin, vertex + 1)))
        if not int(get_modified(skin, vertex + 1)):
            unmodified = True
    return groups, flags, seeds, unmodified


def _initialize_bone_groups(rt, skin, vertices):
    """Establish a stable base in two native batches before adding memberships.

    Direct SetBoneWeights on a new/single-influence vertex can retain old values;
    adding memberships while clearing its old weights can raise native errors.
    The ordinary selected-vertex writer establishes the base safely. It is only
    temporary: the complete source weights and DQ are written afterwards.
    """
    ops = rt.skinOps
    selected = rt.Array(*list(ops.getSelectedVertices(skin)))
    selected_bone = int(ops.GetSelectedBone(skin))
    bones = int(ops.GetNumberBones(skin))
    skin.bone_limit = max(int(skin.bone_limit), bones)
    try:
        _progress(u"批量初始化 Skin 顶点影响")
        ops.selectVertices(skin, rt.Array(*range(1, vertices + 1)))
        ops.selectBone(skin, 1)
        ops.SetWeight(skin, 1.)
        if bones > 1:
            ops.selectBone(skin, 2)
            ops.SetWeight(skin, .5)
    finally:
        try:
            ops.selectVertices(skin, selected)
        finally:
            if selected_bone > 0:
                ops.selectBone(skin, selected_bone)


def _write_bone_groups(rt, skin, mesh, plan, dq, write_dq=None):
    # File import also restores latent DQ; sending keeps its old source-mode rule.
    groups, flags, seeds, unmodified = plan
    changed, applied, error, restore_error = [], 0, None, None
    set_flag = rt.skinOps.unNormalizeVertex
    set_bone = rt.skinOps.SetBoneWeights
    import_dq = mesh["skinning_method"] != 0 if write_dq is None else write_dq
    set_dq = rt.skinOps.setVertexDQWeight if import_dq else None
    to_array = rt.Array
    try:
        _progress(u"暂停逐点归一化")
        for vertex, flag in enumerate(flags, 1):
            if not flag:
                changed.append(vertex)
                set_flag(skin, vertex, True)
        # Add positive source memberships before clearing any obsolete influence.
        # The stable base was established via SetWeight, not this bulk primitive.
        for bone_id, values in seeds.items():
            _progress(u"按骨骼批量补入顶点影响：%d" % bone_id)
            vertices = list(values)
            set_bone(skin, bone_id, to_array(*vertices), to_array(*(values[v] for v in vertices)))
        for bone_id, values in groups.items():
            _progress(u"按骨骼批量写入权重：%d" % bone_id)
            vertices = list(values)
            set_bone(skin, bone_id, to_array(*vertices), to_array(*(values[v] for v in vertices)))
        applied = mesh["vertices"]
        if set_dq is not None:
            for vertex, value in enumerate(dq, 1):
                _progress(u"%s：写入第 %d 个顶点 DQ" % (mesh["name"], vertex))
                set_dq(skin, vertex, float(value))
    except Exception as exc:
        error = text_type(exc)
    finally:
        _progress(u"恢复顶点归一化设置")
        for vertex in changed:
            try:
                set_flag(skin, vertex, False)
            except Exception as exc:
                if restore_error is None:
                    restore_error = text_type(exc)
    if restore_error is not None:
        error = (error + u"；" if error else u"") + u"恢复顶点归一化设置失败：" + restore_error
    return applied, error


def _preflight(rt, directory, mesh, nodes):
    method = mesh.get("skinning_method")
    if isinstance(method, bool) or not isinstance(method, int) or method not in (0, 1, 2):
        raise ValueError(u"缺少有效的 Maya 源蒙皮方式，请重载 Maya 和 Max 动作库")
    matches = nodes.get(mesh["name"], [])
    if len(matches) != 1:
        raise ValueError(u"Max 中找不到唯一同名模型：%s（找到 %d 个）" % (mesh["name"], len(matches)))
    node = matches[0]
    skins = _skin_modifiers(rt, node)
    if len(skins) > 1:
        raise ValueError(u"目标有多个 Skin，无法确定导入位置")
    skin = skins[0] if skins else None
    if skin is not None:
        _activate(rt, node, skin)
        _progress(u"读取 Skin 顶点数")
        count = int(rt.skinOps.GetNumberVertices(skin))
    else:
        count = int(rt.getNumVerts(node))
    if count != mesh["vertices"]:
        raise ValueError(u"顶点数不一致：Maya %d，Max %d" % (mesh["vertices"], count))
    bone_nodes, missing, ambiguous = {}, [], []
    names = [bone["name"] for bone in mesh["bones"]]
    if len(names) != len(set(names)):
        raise ValueError(u"有权重的骨骼名称重复，无法唯一对应")
    for bone in mesh["bones"]:
        found = nodes.get(bone["name"], [])
        if not found:
            missing.append(bone["name"])
        elif len(found) > 1:
            ambiguous.append(bone["name"])
        else:
            bone_nodes[bone["name"]] = found[0]
    if missing or ambiguous:
        raise ValueError(u"场景缺少骨骼：%s；场景重名骨骼：%s" %
                         (u"、".join(missing) or u"无", u"、".join(ambiguous) or u"无"))
    payload = _payload(directory, mesh)
    return node, skin, bone_nodes, payload


def _import_mesh(rt, node, skin, bone_nodes, payload, mesh, max_influences):
    created, applied, added = False, 0, 0
    old_limit = int(skin.bone_limit) if skin is not None else None
    try:
        _activate(rt, node, skin)
        if skin is None:
            _progress(u"创建 Skin 修改器")
            skin = rt.Skin()
            rt.addModifier(node, skin)
            created = True
            _activate(rt, node, skin)
        _close_weight_windows(rt, skin)
        _progress(u"读取 Skin 骨骼列表")
        mapping = _bone_map(rt, skin)
        new_bones = [bone["name"] for bone in mesh["bones"] if bone["name"] not in mapping]
        for index, name in enumerate(new_bones):
            _progress(u"添加 Skin 骨骼：%s" % name)
            rt.skinOps.addBone(skin, bone_nodes[name], 1 if index == len(new_bones) - 1 else 0)
            added += 1
        mapping = _bone_map(rt, skin)
        columns_to_ids = {bone["index"]: mapping[bone["name"]] for bone in mesh["bones"]}
        offsets, columns, weights, dq = payload
        # 写入阶段先容纳源影响数，避免旧 Skin 的小上限干扰完整导入。
        largest = max(b - a for a, b in zip(offsets, offsets[1:]))
        _progress(u"设置写入影响数上限")
        skin.bone_limit = max(int(skin.bone_limit), largest)
        plan = None
        if _supports_bone_groups(rt, mesh["vertices"]) and _bulk_source_ok(payload):
            _progress(u"准备 Skin 骨骼权重分组")
            # Fresh Skin / newly added source bones need initialization anyway;
            # do not scan the old per-vertex memberships only to discard them.
            if created or added:
                _initialize_bone_groups(rt, skin, mesh["vertices"])
            plan = _bone_groups(rt, skin, payload, columns_to_ids)
            if not (created or added) and plan is not None and (plan[2] or plan[3]):
                _initialize_bone_groups(rt, skin, mesh["vertices"])
                # Initialization changes memberships; rebuild the sparse plan.
                # Release the old dictionaries before collecting a second plan.
                plan = None
                plan = _bone_groups(rt, skin, payload, columns_to_ids)
        if plan is not None:
            applied, error = _write_bone_groups(rt, skin, mesh, plan, dq)
            if error:
                raise RuntimeError(error)
        else:
            for vertex in range(mesh["vertices"]):
                start, end = offsets[vertex], offsets[vertex + 1]
                bone_ids = [columns_to_ids[column] for column in columns[start:end]]
                values = list(weights[start:end])
                _progress(u"%s：写入第 %d 个顶点权重" % (mesh["name"], vertex + 1))
                rt.skinOps.ReplaceVertexWeights(skin, vertex + 1, rt.Array(*bone_ids), rt.Array(*values))
                applied = vertex + 1
                if mesh["skinning_method"] != 0:
                    _progress(u"%s：写入第 %d 个顶点 DQ" % (mesh["name"], vertex + 1))
                    rt.skinOps.setVertexDQWeight(skin, vertex + 1, float(dq[vertex]))
        # 仅设置参数，不截断或量化源权重；DQ由源Maya方式决定，不读/切换Max模式。
        _progress(u"设置最终最大影响数")
        skin.bone_limit = max_influences
        return dict(name=mesh["name"], vertices=mesh["vertices"], added_bones=added,
                    write_mode="bone_groups" if plan is not None else "vertex",
                    skinning_method=mesh["skinning_method"], dq_imported=mesh["skinning_method"] != 0)
    except Exception as exc:
        if created:
            try:
                rt.deleteModifier(node, skin)
            except Exception as cleanup:
                raise RuntimeError(u"%s；新建 Skin 清理失败：%s" % (text_type(exc), text_type(cleanup)))
            raise RuntimeError(u"%s；本次新建的 Skin 已移除" % text_type(exc))
        try:
            skin.bone_limit = old_limit
        except Exception:
            pass
        raise RuntimeError(u"%s；已处理 %d 个顶点，已有 Skin 可能部分更新" % (text_type(exc), applied))


@register_action("skin_weights_import_max")
def import_max(directory, meshes, max_influences=4):
    import pymxs
    rt = pymxs.runtime
    from max_bridge import server
    if not getattr(server, "SUPPORTS_EXECUTION_TIMEOUT", False):
        raise RuntimeError(u"Max 桥接版本需要更新：请重启 Max 或重新启动新版桥接后再发送")
    started, timings = _clock(), {}
    directory = os.path.realpath(directory)
    if not os.path.isdir(directory) or not os.path.basename(directory).startswith("qz-skin-transfer-"):
        raise ValueError(u"无效的权重发送临时目录")
    if isinstance(max_influences, bool) or not isinstance(max_influences, int) or max_influences < 1:
        raise ValueError(u"最大影响数必须为正整数")
    nodes = {}
    _progress(u"匹配 Max 场景模型与骨骼")
    for node in rt.objects:
        nodes.setdefault(text_type(node.name), []).append(node)
    reports, errors, plans = [], [], []
    source_names = [mesh["name"] for mesh in meshes]
    for mesh in meshes:
        stage = _clock()
        try:
            if source_names.count(mesh["name"]) != 1:
                raise ValueError(u"发送列表中存在同名模型")
            plan = _preflight(rt, directory, mesh, nodes)
            plans.append((mesh, plan))
        except Exception as exc:
            errors.append(dict(mesh=mesh["mesh"], error=text_type(exc), status="skipped"))
        timings["max_prepare"] = timings.get("max_prepare", 0.) + _clock() - stage
    if plans:
        _progress(u"暂停视图刷新")
        rt.disableSceneRedraw()
        try:
            for mesh, (node, skin, bone_nodes, payload) in plans:
                stage = _clock()
                try:
                    reports.append(_import_mesh(rt, node, skin, bone_nodes, payload, mesh, max_influences))
                except Exception as exc:
                    errors.append(dict(mesh=mesh["mesh"], error=text_type(exc), status="failed"))
                timings["max_write"] = timings.get("max_write", 0.) + _clock() - stage
        finally:
            _progress(u"恢复视图刷新")
            rt.enableSceneRedraw()
            _progress(u"刷新 Max 视图")
            rt.redrawViews()
        successful = [nodes[report["name"]][0] for report in reports]
        if successful:
            _progress(u"选择已导入模型")
            rt.select(rt.Array(*successful))
    elapsed = _clock() - started
    timings["max_other"] = max(0., elapsed - sum(timings.values()))
    return dict(count=len(reports), vertices=sum(report["vertices"] for report in reports),
                reports=reports, errors=errors, action_seconds=elapsed, timings_seconds=timings)
