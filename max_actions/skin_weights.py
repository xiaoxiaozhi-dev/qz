# -*- coding: utf-8 -*-
"""3ds Max 2020 / Python 2.7 local Skin actions. No NumPy in the host."""
from __future__ import unicode_literals

from array import array
from contextlib import contextmanager
import math
import os
import sys
import time
import uuid

from max_bridge.server import register_action
from max_actions.skin_transfer import _skin_modifiers, _array_file, _progress
from max_actions import skin_transfer as _transfer

try:
    text_type = unicode
    integer_types = (int, long)
except NameError:
    text_type = str
    integer_types = (int,)

_clock = getattr(time, 'perf_counter', time.time)
_JOBS = {}
BLOCK_VALUES = 262144
FILE_BULK_MIN_VERTICES = 256


@contextmanager
def _stage(timings, key):
    if timings is None:
        yield
        return
    started = _clock()
    try:
        yield
    finally:
        timings[key] = timings.get(key, 0.) + _clock() - started


def _timed(timings, key, fn, *args, **kwargs):
    with _stage(timings, key):
        return fn(*args, **kwargs)


@contextmanager
def _capture_file(path, timings):
    handle = _timed(timings, 'max_file_write', open, path, 'wb')
    try:
        yield handle
    finally:
        _timed(timings, 'max_file_write', handle.close)


def _runtime():
    import pymxs
    rt = pymxs.runtime
    if int(rt.maxVersion()[0]) != 22000:
        raise ValueError('本插件仅支持 3ds Max 2020 / Python 2.7 宿主')
    return rt


def _integer(value, minimum=0):
    return isinstance(value, integer_types) and not isinstance(value, bool) and value >= minimum


def _directory(directory, prefix):
    path = os.path.realpath(directory)
    if not os.path.isdir(path) or not os.path.basename(path).startswith(prefix):
        raise ValueError('无效的蒙皮任务交换目录')
    return path


def _write(path, values, code='d'):
    data = array(code, values)
    if sys.byteorder != 'little':
        data.byteswap()
    with open(path, 'wb') as handle:
        data.tofile(handle)


def _bones(rt, skin):
    # Skin system IDs can contain holes after a bone is removed.
    ids = sorted(int(rt.skinOps.GetBoneIDByListID(skin, i))
                 for i in range(1, int(rt.skinOps.GetNumberBones(skin)) + 1))
    return ids, [text_type(rt.skinOps.GetBoneName(skin, i, 0)) for i in ids]


def _activate(rt, node, skin=None):
    # select/setCurrentObject rebuild the Modify panel even for the same target.
    # Keep the required context, but change only the parts that actually differ.
    selection = list(rt.selection)
    changed = len(selection) != 1 or selection[0] != node
    if changed:
        _progress('选择目标模型')
        rt.select(node)
    if rt.getCommandPanelTaskMode() != rt.Name('modify'):
        _progress('切换修改面板')
        rt.setCommandPanelTaskMode(rt.Name('modify'))
    if skin is not None and (changed or rt.modPanel.getCurrentObject() != skin):
        _progress('激活目标 Skin')
        rt.modPanel.setCurrentObject(skin, node=node)


def _restore_selection(rt, previous):
    valid = [node for node in previous if rt.isValidNode(node)]
    if list(rt.selection) != valid:
        rt.select(rt.Array(*valid))


def _selected(rt, require_skin):
    entries, errors = [], []
    for node in list(rt.selection):
        name = text_type(node.name)
        try:
            skins = _skin_modifiers(rt, node)
            if len(skins) > 1 or (require_skin and not skins):
                raise ValueError('需要恰好一个 Skin 修改器')
            skin = skins[0] if skins else None
            if skin is not None:
                _activate(rt, node, skin)
                vertices = int(rt.skinOps.GetNumberVertices(skin))
            else:
                vertices = int(rt.getNumVerts(node))
            if vertices < 1:
                raise ValueError('模型没有可处理的顶点')
            entries.append(dict(index=len(entries), mesh=name, name=name, vertices=vertices,
                                node_handle=int(rt.getHandleByAnim(node)), node=node, skin=skin))
        except Exception as exc:
            errors.append(dict(mesh=name, error=text_type(exc), status='skipped'))
    return entries, errors


def _public(entry):
    return {key: value for key, value in entry.items() if key not in ('node', 'skin', 'bone_ids')}


def _capture(rt, directory, entry, include_dq, timings=None):
    with _stage(timings, 'max_prepare'):
        node, skin = entry['node'], entry['skin']
        _activate(rt, node, skin)
        bone_ids, names = _bones(rt, skin)
        if not bone_ids:
            raise ValueError('Skin 没有影响骨骼')
        width = len(bone_ids)
        columns = {bone: index for index, bone in enumerate(bone_ids)}
        prefix = os.path.join(directory, 'mesh-%d' % entry['index'])
        batch = max(1, min(2048, BLOCK_VALUES // width))
        ops = rt.skinOps
        # Resolving pymxs interface attributes in the inner loop is expensive.
        # Cache callables once per capture; preserve every value returned by Skin.
        get_count = ops.GetVertexWeightCount
        get_bone = ops.GetVertexWeightBoneID
        get_weight = ops.GetVertexWeight
        get_dq = ops.getVertexDQWeight if include_dq else None
        get_unnormalized = ops.isUnNormalizeVertex if include_dq else None
        dq, unnormalized = [], []
    with _capture_file(prefix + '.weights', timings) as handle:
        for start in range(0, entry['vertices'], batch):
            _progress('读取 %s 顶点权重：%d/%d' % (entry['name'], start, entry['vertices']))
            with _stage(timings, 'max_read_expand'):
                rows = array('d', [0.]) * (min(batch, entry['vertices'] - start) * width)
                for row, vertex in enumerate(range(start + 1, min(start + batch, entry['vertices']) + 1)):
                    for slot in range(1, int(get_count(skin, vertex)) + 1):
                        bone = int(get_bone(skin, vertex, slot))
                        if bone not in columns:
                            raise ValueError('Skin 返回了未知骨骼编号')
                        rows[row * width + columns[bone]] = float(get_weight(skin, vertex, slot))
                    if include_dq:
                        dq.append(float(get_dq(skin, vertex)))
                        if get_unnormalized(skin, vertex):
                            unnormalized.append(vertex - 1)
            with _stage(timings, 'max_file_write'):
                if sys.byteorder != 'little':
                    rows.byteswap()
                rows.tofile(handle)
    entry.update(influences=width, bone_names=names, bone_ids=bone_ids)
    if include_dq:
        _write(prefix + '.dq', dq)
        entry.update(skinning_method=2 if bool(skin.enableDQ) else 0,
                     max_unnormalized_vertices=unnormalized, max_bone_limit=int(skin.bone_limit))


def _patch(rt, entry, directory, patch, threshold):
    count, width, index = patch['count'], patch['width'], patch['index']
    if not _integer(count) or width != threshold or count > entry['vertices']:
        raise ValueError('补丁数量或宽度无效')
    prefix = os.path.join(directory, 'mesh-%d' % index)
    ids = _array_file(prefix + '.ids', count, 'I')
    cols = _array_file(prefix + '.cols', count * width, 'I')
    values = _array_file(prefix + '.values', count * width, 'd')
    if any(v >= entry['vertices'] for v in ids) or any(a >= b for a, b in zip(ids, ids[1:])):
        raise ValueError('补丁顶点编号越界、重复或无序')
    if any(c >= entry['influences'] for c in cols):
        raise ValueError('补丁骨骼编号越界')
    if any(math.isnan(v) or math.isinf(v) or v < 0 for v in values):
        raise ValueError('补丁权重包含异常数值')
    if any(abs(sum(values[row * width:(row + 1) * width]) - 1.) > 1e-6 for row in range(count)):
        raise ValueError('补丁权重未归一化')
    node, skin = entry['node'], entry['skin']
    if not rt.isValidNode(node):
        raise ValueError('目标模型已不存在')
    _activate(rt, node, skin)
    if int(rt.skinOps.GetNumberVertices(skin)) != entry['vertices']:
        raise ValueError('目标 Skin 顶点数已变化')
    if not ids:
        return 0
    old_limit, attempted = int(skin.bone_limit), []
    try:
        # Do not initialize all vertices: this command only edits excessive rows.
        skin.bone_limit = max(old_limit, threshold)
        for row, vertex in enumerate(ids):
            flag = bool(rt.skinOps.isUnNormalizeVertex(skin, vertex + 1))
            attempted.append((vertex, flag))
            rt.skinOps.unNormalizeVertex(skin, vertex + 1, True)
            first, last = row * width, (row + 1) * width
            pairs = [(entry['bone_ids'][column], value) for column, value in zip(cols[first:last], values[first:last])
                     if value > 0]
            rt.skinOps.ReplaceVertexWeights(skin, vertex + 1, rt.Array(*(p[0] for p in pairs)),
                                           rt.Array(*(p[1] for p in pairs)))
            rt.skinOps.unNormalizeVertex(skin, vertex + 1, flag)
        skin.bone_limit = threshold
    except Exception as exc:
        failures = []
        source = os.path.join(directory, 'mesh-%d.weights' % index)
        for vertex, flag in reversed(attempted):
            try:
                original = array('d')
                with open(source, 'rb') as handle:
                    handle.seek(vertex * entry['influences'] * 8)
                    original.fromfile(handle, entry['influences'])
                if sys.byteorder != 'little': original.byteswap()
                pairs = [(bone, value) for bone, value in zip(entry['bone_ids'], original) if value != 0]
                rt.skinOps.unNormalizeVertex(skin, vertex + 1, True)
                rt.skinOps.ReplaceVertexWeights(skin, vertex + 1, rt.Array(*(p[0] for p in pairs)),
                                               rt.Array(*(p[1] for p in pairs)))
                rt.skinOps.unNormalizeVertex(skin, vertex + 1, flag)
            except Exception as error:
                failures.append(text_type(error))
        try: skin.bone_limit = old_limit
        except Exception as error: failures.append(text_type(error))
        detail = '；恢复未完成，请检查模型：' + '；'.join(failures) if failures else '；已执行原权重及设置恢复'
        raise RuntimeError(text_type(exc) + detail)
    return count


@register_action('skin_weights_max')
def skin_weights(op='snapshot', directory=None, token=None, patches=None, threshold=4, retain_task=True):
    started, timings = _clock(), {}
    result = _skin_weights(op, directory, token, patches, threshold, retain_task, timings)
    elapsed = _clock() - started
    timings['max_other'] = max(0., elapsed - sum(timings.values()))
    result['action_seconds'] = elapsed  # Includes finally/selection restoration.
    result['timings_seconds'] = timings
    return result


def _skin_weights(op, directory, token, patches, threshold, retain_task, timings):
    rt = _runtime()
    started = _clock()
    for key in list(_JOBS):
        if started - _JOBS[key]['created'] > 1800: del _JOBS[key]
    if op == 'release':
        _JOBS.pop(token, None)
        return dict(count=0)
    previous = list(rt.selection)
    selection_applied = False
    try:
        if op == 'snapshot':
            directory = _directory(directory, 'qz-skin-')
            entries, errors = _timed(timings, 'max_prepare', _selected, rt, True)
            ready = []
            for entry in entries:
                try:
                    _capture(rt, directory, entry, False, timings)
                    ready.append(entry)
                except Exception as exc:
                    errors.append(dict(mesh=entry['mesh'], error=text_type(exc)))
            token = uuid.uuid4().hex if ready and retain_task else None
            if token: _JOBS[token] = dict(created=started, directory=directory, entries=ready)
            return dict(token=token, meshes=[_public(e) for e in ready], errors=errors,
                        warning='未选中可处理的蒙皮模型' if not ready else '', action_seconds=_clock() - started)
        if op not in ('limit', 'select_excess') or token not in _JOBS:
            raise ValueError('蒙皮任务不存在或已过期，请重新检测')
        if not _integer(threshold, 1): raise ValueError('最大影响数必须为正整数')
        job = _JOBS.pop(token)
        entries = {entry['index']: entry for entry in job['entries']}
        indices = [p['index'] for p in patches or []]
        if len(indices) != len(entries) or set(indices) != set(entries):
            raise ValueError('补丁对象集合不完整、重复或未知')
        count, errors, selected = 0, [], []
        rt.disableSceneRedraw()
        try:
            for patch in patches or []:
                entry = entries[patch['index']]
                try:
                    if op == 'limit':
                        count += _timed(timings, 'max_apply', _patch, rt, entry, job['directory'], patch, threshold)
                    else:
                        if not _integer(patch['count']) or patch['count'] > entry['vertices']:
                            raise ValueError('选择顶点数量无效')
                        ids = _array_file(os.path.join(job['directory'], 'mesh-%d.ids' % patch['index']), patch['count'], 'I')
                        if any(v >= entry['vertices'] for v in ids) or any(a >= b for a, b in zip(ids, ids[1:])):
                            raise ValueError('选择顶点编号无效')
                        _activate(rt, entry['node'], entry['skin'])
                        rt.subObjectLevel = 1
                        rt.skinOps.selectVertices(entry['skin'], rt.Array(*(v + 1 for v in ids)))
                        selected.append(entry)
                        count += len(ids)
                except Exception as exc:
                    errors.append(dict(mesh=entry['mesh'], error=text_type(exc)))
        finally:
            rt.enableSceneRedraw()
            rt.redrawViews()
        if op == 'select_excess' and selected:
            selection_applied = True
        return dict(count=count, errors=errors, action_seconds=_clock() - started)
    finally:
        if not selection_applied:
            _timed(timings, 'max_restore', _restore_selection, rt, previous)



def _new_skin_defaults(skin):
    # Display rollout matches the user's preferred setup; existing Skin is untouched.
    skin.draw_vertices = False
    skin.shadeweights = False
    skin.colorAllWeights = False
    skin.draw_all_envelopes = False
    skin.draw_all_vertices = False
    skin.draw_all_gizmos = False
    skin.showNoEnvelopes = True
    skin.showHiddenVertices = True
    skin.bone_limit = 4



def _file_bulk_supported(rt, vertices, bone_ids):
    if vertices < FILE_BULK_MIN_VERTICES:
        return False
    # Shared initialization selects IDs 1 and 2. Removed-bone holes stay on the
    # complete vertex writer, which already supports arbitrary Skin system IDs.
    if bone_ids != list(range(1, len(bone_ids) + 1)):
        return False
    try:
        return int(rt.maxVersion()[0]) == 22000 and callable(rt.skinOps.SetBoneWeights)
    except Exception:
        return False


def _import_one(rt, directory, entry, nodes):
    node = rt.getAnimByHandle(entry['node_handle'])
    if node is None or not rt.isValidNode(node): raise ValueError('目标模型已不存在')
    skins = _skin_modifiers(rt, node)
    if len(skins) > 1: raise ValueError('目标有多个 Skin，无法确定导入位置')
    skin = skins[0] if skins else None
    _activate(rt, node, skin)
    # Stop the floating Weight Tool from refreshing for every imported weight.
    # Leave it closed after import; the user can reopen it when needed.
    if skin is not None and rt.skinOps.isWeightToolOpen(skin):
        rt.skinOps.closeWeightTool(skin)
    vertices = int(rt.skinOps.GetNumberVertices(skin)) if skin is not None else int(rt.getNumVerts(node))
    if vertices != entry['vertices']: raise ValueError('目标顶点数与权重文件不一致')
    resolved = {}
    for bone in entry['bones']:
        matches = nodes.get(bone['name'].replace('FBXASC032', ' '), [])
        if len(matches) != 1:
            raise ValueError(('场景缺少骨骼：' if not matches else '场景骨骼名称不唯一：') + bone['name'])
        resolved[bone['index']] = matches[0]
    if len(set(int(rt.getHandleByAnim(n)) for n in resolved.values())) != len(resolved):
        raise ValueError('骨骼名称解码后重复')
    prefix = os.path.join(directory, 'mesh-%d' % entry['index'])
    offsets = _array_file(prefix + '.offsets', vertices + 1, 'I')
    cols = _array_file(prefix + '.cols', entry['nonzero'], 'I')
    values = _array_file(prefix + '.values', entry['nonzero'], 'd')
    dq = _array_file(prefix + '.dq', vertices, 'd')
    flags = set(entry.get('max_unnormalized_vertices', []))
    interpolated = None
    if entry.get('transfer_mode') == 'closest_point':
        count = entry['interpolated_vertices']
        if not _integer(count) or count > vertices:
            raise ValueError('插值顶点数量无效')
        interpolated = _array_file(prefix + '.interpolated_ids', count, 'I')
        if any(v >= vertices for v in interpolated) or any(a >= b for a,b in zip(interpolated, interpolated[1:])):
            raise ValueError('插值顶点编号无效')
    if (offsets[0] != 0 or offsets[-1] != len(cols) or any(a >= b for a,b in zip(offsets, offsets[1:]))
            or any(c not in resolved for c in cols)):
        raise ValueError('权重索引无效')
    created, applied, added = False, 0, 0
    write_mode = 'vertex'
    old_limit = int(skin.bone_limit) if skin is not None else None
    try:
        if skin is None:
            skin = rt.Skin()
            rt.addModifier(node, skin)
            created = True
            _new_skin_defaults(skin)
            _activate(rt, node, skin)
        ids, names = _bones(rt, skin)
        existing = dict(zip(names, ids))
        missing = [bone_node for bone_node in resolved.values() if text_type(bone_node.name) not in existing]
        add_bone = rt.skinOps.addBone
        for index, bone_node in enumerate(missing):
            add_bone(skin, bone_node, 1 if index == len(missing) - 1 else 0)
            added += 1
        ids, names = _bones(rt, skin)
        mapping = dict(zip(names, ids))
        bone_ids = {col: mapping[text_type(n.name)] for col,n in resolved.items()}
        largest = max(b-a for a,b in zip(offsets, offsets[1:]))
        skin.bone_limit = max(int(skin.bone_limit), largest)
        payload = offsets, cols, values, dq
        source_flags = [vertex in flags or abs(sum(values[first:last]) - 1.) > 1e-6
                        for vertex, (first, last) in enumerate(zip(offsets, offsets[1:]))]
        plan = None
        eligible = (_file_bulk_supported(rt, vertices, ids) and not any(source_flags)
                    and _transfer._bulk_source_ok(payload))
        if eligible and not created:
            # Max bulk setters can miswrite already-unnormalized vertices.
            # Preserve all such sources/targets through the cached vertex writer.
            get_flag = rt.skinOps.isUnNormalizeVertex
            eligible = not any(bool(get_flag(skin, vertex)) for vertex in range(1, vertices + 1))
        if eligible:
            write_mode = 'bone_groups'
            # File/third-party vertex setters can report IsVertexModified=1
            # without establishing the native bulk writer's stable base.
            _transfer._initialize_bone_groups(rt, skin, vertices)
            plan = _transfer._bone_groups(rt, skin, payload, bone_ids)
        if plan is not None:
            applied, error = _transfer._write_bone_groups(rt, skin, entry, plan, dq, write_dq=True)
            if error: raise RuntimeError(error)
        else:
            write_mode = 'vertex'
            ops = rt.skinOps
            get_flag, set_flag = ops.isUnNormalizeVertex, ops.unNormalizeVertex
            replace_weights, set_dq = ops.ReplaceVertexWeights, ops.setVertexDQWeight
            to_array = rt.Array
            for vertex in range(vertices):
                if vertex % 2048 == 0: _progress('导入 %s：%d/%d' % (entry['name'], vertex, vertices))
                first, last = offsets[vertex], offsets[vertex + 1]
                original_flag = bool(get_flag(skin, vertex + 1))
                complete = False
                try:
                    set_flag(skin, vertex + 1, True)
                    applied = vertex + 1
                    replace_weights(skin, vertex + 1, to_array(*(bone_ids[c] for c in cols[first:last])),
                                    to_array(*values[first:last]))
                    set_dq(skin, vertex + 1, float(dq[vertex]))
                    complete = True
                finally:
                    set_flag(skin, vertex + 1, source_flags[vertex] if complete else original_flag)
        method = entry.get('skinning_method')
        if method is not None: skin.enableDQ = method != 0
        # Never use the UI threshold to truncate or change a complete file import.
        skin.bone_limit = 4 if created else entry.get('max_bone_limit', max(old_limit, largest))
        report = dict(name=entry['name'], vertices=vertices, added_bones=added, created_skin=created,
                      skinning_method=method, write_mode=write_mode)
        if interpolated is not None:
            report.update(transfer_mode='closest_point', direct_vertices=entry['direct_vertices'],
                          interpolated_vertices=entry['interpolated_vertices'])
            try:
                rt.subObjectLevel = 1
                rt.skinOps.selectVertices(skin, rt.Array(*(int(vertex) + 1 for vertex in interpolated)))
                report['selection_changed'] = True
            except Exception as exc:
                report['selection_warning'] = '权重已导入，插值点选择失败：' + text_type(exc)
        return report
    except Exception as exc:
        if created:
            rt.deleteModifier(node, skin)
            raise RuntimeError(text_type(exc) + '；本次新建 Skin 已移除')
        try: skin.bone_limit = old_limit
        except Exception: pass
        if write_mode == 'bone_groups':
            raise RuntimeError(text_type(exc) + '；批量导入中断，已有 Skin 可能部分更新')
        raise RuntimeError('%s；已处理 %d 个顶点，已有 Skin 可能部分更新' % (text_type(exc), applied))


@register_action('skin_weight_files_max')
def skin_weight_files(op='context', directory=None, meshes=None, position_decimals=3):
    rt = _runtime()
    previous = list(rt.selection)
    selection_changed = False
    try:
        directory = _directory(directory, 'qz-skin-transfer-') if op in ('import', 'export', 'map') else None
        if op == 'map':
            from max_actions.skin_weight_geometry import map_geometry
            return map_geometry(rt, directory, meshes or [], position_decimals, _skin_modifiers, _write)
        if op in ('context', 'export'):
            entries, errors = _selected(rt, op == 'export')
            ready = []
            for entry in entries:
                try:
                    if op == 'export':
                        _capture(rt, directory, entry, True)
                        from max_actions.skin_weight_geometry import export_geometry
                        export_geometry(rt, directory, entry, _write)
                    ready.append(_public(entry))
                except Exception as exc:
                    errors.append(dict(mesh=entry['mesh'], error=text_type(exc), status='skipped'))
            return dict(meshes=ready, errors=errors)
        if op != 'import': raise ValueError('未知权重文件操作')
        groups = {}
        for node in rt.objects:
            groups.setdefault(text_type(node.name).replace('FBXASC032', ' '), []).append(node)
        reports, errors = [], []
        rt.disableSceneRedraw()
        try:
            for entry in meshes or []:
                try:
                    report = _import_one(rt, directory, entry, groups)
                    reports.append(report)
                    selection_changed = selection_changed or report.get('selection_changed', False)
                except Exception as exc:
                    errors.append(dict(mesh=entry['mesh'], error=text_type(exc),
                                       status='skipped' if isinstance(exc, ValueError) else 'failed'))
        finally:
            rt.enableSceneRedraw()
            rt.redrawViews()
        if selection_changed:
            # A later skipped model must not hide the successful interpolation selection.
            last = next(report for report in reversed(reports) if report.get('selection_changed'))
            entry = next(entry for entry in meshes if entry['name'] == last['name'])
            node = rt.getAnimByHandle(entry['node_handle'])
            skin = _skin_modifiers(rt, node)[0]
            _activate(rt, node, skin)
            rt.subObjectLevel = 1
        return dict(reports=reports, errors=errors, selection_changed=selection_changed,
                    selected_vertices=sum(r.get('interpolated_vertices', 0) for r in reports if r.get('selection_changed')))
    finally:
        if not selection_changed: _restore_selection(rt, previous)
