# -*- coding: utf-8 -*-
"""Vertex channel editing and display on Max 2020's main thread."""
from __future__ import unicode_literals

import math
import hashlib
import uuid
import struct
from collections import OrderedDict

from max_bridge.server import register_action
from max_actions._vertex_color_topology import check_chain, complete_row, complete_row_from_reference, face_edges, row_weights
from max_actions._vertex_color_nearest import NearestIndex
from max_actions import _vertex_color_viewport as viewport_numbers

try:
    integer_types = (int, long)
    text_type = unicode
except NameError:
    integer_types = (int,)
    text_type = str

CHANNELS = {0: ('Color', 'color'), -2: ('Alpha', 'alpha'), -1: ('Illumination', 'illum'),
            17: ('M17', 'map_channel'), 18: ('M18', 'map_channel')}
GRADIENT_AXES = {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1),
                 '-X': (-1, 0, 0), '-Y': (0, -1, 0), '-Z': (0, 0, -1)}
# Only read-only endpoint snapshots, never a geometry-to-map cache. Clearing
# action modules on reload also invalidates tokens from the previous session.
_TOPOLOGY_RECORDS = OrderedDict()
# One immutable source-color snapshot and its position index, replaced on copy.
_COLOR_CLIPBOARD = None


def validate_channel(channel):
    if isinstance(channel, bool) or not isinstance(channel, integer_types) or channel not in CHANNELS:
        raise ValueError('仅支持 Color、Alpha、Illumination、M17、M18 通道')
    return channel


def _validate_numbers_owner(owner):
    if not isinstance(owner, (text_type, str)) or len(owner) != 32 or any(c not in '0123456789abcdef' for c in owner):
        raise ValueError('数值显示会话无效，请重新打开卡片')
    return owner


@register_action('vertex_color_numbers')
def vertex_color_numbers(enabled=False, channel=0, mask=None, owner=None):
    if type(enabled) is not bool:
        raise ValueError('数值显示开关必须是布尔值')
    channel, mask, owner = validate_channel(channel), validate_mask(mask), _validate_numbers_owner(owner)
    import pymxs
    rt = pymxs.runtime
    if int(rt.maxVersion()[0]) != 22000:
        return dict(enabled=False, warning='此插件仅支持 3ds Max 2020')
    result = viewport_numbers.configure(rt, enabled, channel, mask, owner)
    result['channel_name'] = CHANNELS[result['channel']][0]
    if result.get('error'):
        result['warning'] = '数值显示已停止：%s' % result['error']
    return result


@register_action('vertex_color_numbers_status')
def vertex_color_numbers_status(owner=None):
    owner = _validate_numbers_owner(owner)
    import pymxs
    return viewport_numbers.status(pymxs.runtime, owner)


def validate_alpha(alpha):
    if isinstance(alpha, bool) or not isinstance(alpha, integer_types) or not 0 <= alpha <= 255:
        raise ValueError('Alpha 必须是 0–255 的整数')
    return alpha


def validate_color(rgb, mask):
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
        raise ValueError('RGB 必须包含三个 0–255 的整数')
    if any(isinstance(v, bool) or not isinstance(v, integer_types) or not 0 <= v <= 255 for v in rgb):
        raise ValueError('RGB 必须包含三个 0–255 的整数')
    return tuple(rgb), validate_mask(mask)


def validate_mask(mask):
    if not isinstance(mask, (list, tuple)) or len(mask) != 3 or any(type(v) is not bool for v in mask):
        raise ValueError('RGB 分量开关必须包含三个布尔值')
    return tuple(mask)


def validate_factor(factor):
    if (isinstance(factor, bool) or not isinstance(factor, integer_types + (float,))
            or not 0 <= factor <= 10):
        raise ValueError('缩放倍率必须是 0.00–10.00 的有限数值')
    return float(factor)


def merge_color(old, rgb, mask):
    # Do not round or clamp untouched components: preserve their exact values.
    return tuple(rgb[i] / 255.0 if mask[i] else old[i] for i in range(3))


def quantize_component(value):
    """Clamp to byte range and round halves up, then store as normalized color."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError('颜色包含无效数值，未执行写入')
    # Point3 input is float32. Round in the same precision in byte space so
    # e.g. stored 13/255 scaled by 0.5 remains the intended 6.5, not 6.4999999.
    byte_value = _float32(min(1.0, max(0.0, value)) * 255.0)
    return int(math.floor(byte_value + 0.5)) / 255.0


def scale_color(old, factor, mask, channel):
    # Alpha is scalar: use x as the source and keep display grayscale.
    source = (old[0],) * 3 if channel == -2 else old
    result = []
    for value, enabled in zip(source, mask):
        if enabled:
            if math.isnan(value) or math.isinf(value):
                raise ValueError('目标分量包含无效颜色数值，未执行缩放')
            value = quantize_component(value * factor)
        result.append(value)
    return tuple(result)


def _display_state(obj):
    return (obj.showVertexColors, obj.vertexColorsShaded,
            obj.vertexColorType, obj.vertexColorMapChannel)


def _restore_display(obj, state):
    obj.vertexColorMapChannel = state[3]
    obj.vertexColorType = state[2]
    obj.vertexColorsShaded = state[1]
    obj.showVertexColors = state[0]


def _set_display(rt, obj, channel):
    obj.showVertexColors = True
    obj.vertexColorsShaded = False
    obj.vertexColorType = rt.Name(CHANNELS[channel][1])
    if channel in (17, 18):
        obj.vertexColorMapChannel = channel


@register_action('vertex_color_display')
def vertex_color_display(channel=0):
    """Switch viewport properties only; never allocate or change map data."""
    channel = validate_channel(channel)
    import pymxs
    rt = pymxs.runtime
    if int(rt.maxVersion()[0]) != 22000:
        return dict(count=0, warning='此插件仅支持 3ds Max 2020')
    objects = list(rt.selection)
    if len(objects) != 1:
        return dict(count=0, warning='请只选择一个 Editable Poly 对象以切换通道显示')
    obj = objects[0]
    if rt.classOf(obj) != rt.Editable_Poly:
        return dict(count=0, warning='请选择 Editable Poly 对象以切换通道显示')
    previous = _display_state(obj)
    failure = None
    with pymxs.undo(False):
        try:
            _set_display(rt, obj, channel)
            rt.update(obj)
        except Exception as exc:
            try:
                _restore_display(obj, previous)
                rt.update(obj)
                failure = RuntimeError('切换通道显示失败，已恢复原显示：%s' % exc)
            except Exception as restore_error:
                failure = RuntimeError('切换通道显示失败：%s；恢复显示也失败：%s' % (exc, restore_error))
    rt.redrawViews()
    if failure is not None:
        raise failure
    return dict(count=1, object=obj.name, channel=channel,
                channel_name=CHANNELS[channel][0], display_only=True)


def _ids(rt, bits):
    # Fixed source only; no object names or user input is inserted into MAXScript.
    convert = rt.execute('(fn qz_vertex_color_indices bits = (bits as array))')
    return [int(i) for i in convert(bits)]


def _selection(rt, obj, level):
    ops = rt.polyop
    if level == 1:
        bits = ops.getVertSelection(obj)
    elif level in (2, 3):
        bits = ops.getVertsUsingEdge(obj, ops.getEdgeSelection(obj))
    else:
        bits = ops.getVertsUsingFace(obj, ops.getFaceSelection(obj))
    return set(_ids(rt, bits))


def _plan(rt, obj, selected, channel=0, selected_faces=None):
    """Read fresh topology and the target channel before any scene writes."""
    ops = rt.polyop
    count = int(ops.getNumVerts(obj))
    if any(v < 1 or v > count for v in selected):
        raise ValueError('选区包含失效顶点，请重新选择')
    supported = bool(ops.getMapSupport(obj, channel))
    map_count = int(ops.getNumMapVerts(obj, channel)) if supported else 0
    initialize = map_count == 0
    dead = set(_ids(rt, ops.getDeadFaces(obj)))
    faces, targets, outside = [], set(), set()
    active_geo = set()
    for face in range(1, int(ops.getNumFaces(obj)) + 1):
        if face in dead:
            continue
        geo = [int(v) for v in ops.getFaceVerts(obj, face)]
        mapping = list(geo) if initialize else [int(v) for v in ops.getMapFace(obj, channel, face)]
        if len(geo) != len(mapping) or (not initialize and any(v < 1 or v > map_count for v in mapping)):
            raise ValueError('%s 通道的面映射无效，未执行写入' % CHANNELS[channel][0])
        for gv, mv in zip(geo, mapping):
            if gv in selected and (selected_faces is None or face in selected_faces):
                targets.add(mv)
                active_geo.add(gv)
            else:
                outside.add(mv)
        faces.append((face, geo, mapping))
    colors = {}
    for mv in targets:
        p = None if initialize else ops.getMapVert(obj, channel, mv)
        colors[mv] = (1.0, 1.0, 1.0) if initialize else (float(p.x), float(p.y), float(p.z))
    return dict(channel=channel, initialize=initialize, supported=supported, geo_count=count,
                map_count=map_count, faces=faces, targets=targets,
                shared=targets & outside, colors=colors, geo_vertices=len(active_geo), selected_faces=selected_faces)


def _world_position(rt, obj, vertex):
    # Base-poly coordinates are local; objectTransform includes node and pivot offsets.
    point = rt.polyop.getVert(obj.baseObject, vertex) * obj.objectTransform
    value = (float(point.x), float(point.y), float(point.z))
    if any(math.isnan(v) or math.isinf(v) for v in value):
        raise ValueError('选区包含无效世界坐标，未执行操作')
    return value


def validate_gradient_point(point):
    if (not isinstance(point, (list, tuple)) or len(point) != 3 or
            any(isinstance(v, bool) or not isinstance(v, integer_types + (float,)) for v in point)):
        raise ValueError('渐变起终点必须包含三个有限世界坐标')
    try:
        result = tuple(float(v) for v in point)
    except OverflowError:
        raise ValueError('渐变起终点必须包含三个有限世界坐标')
    if any(math.isnan(v) or math.isinf(v) for v in result):
        raise ValueError('渐变起终点必须包含三个有限世界坐标')
    return result


def gradient_value(t, minimum, maximum, curve):
    t = min(1.0, max(0.0, t))
    mid = curve / 100.0
    curved = min(1.0, max(0.0, (2 - 4 * mid) * t * t + (4 * mid - 1) * t))
    return quantize_component((minimum + (maximum - minimum) * curved) / 255.0)


def _plan_gradient(rt, obj, plan, gradient, mask):
    """Assign map IDs by original map ID and final color before any writes."""
    active = set(gv for face, geo, mapping in plan['faces'] for gv in geo if gv in plan['selected'])
    positions = {} if gradient['direction'] == 'topology' else {gv: _world_position(rt, obj, gv) for gv in sorted(active)}
    if gradient['direction'] == 'topology':
        projections = gradient['projections']
    elif gradient['direction'] == 'custom':
        start, end = gradient['start'], gradient['end']
        vector = tuple(b - a for a, b in zip(start, end))
        length_sq = sum(v * v for v in vector)
        projections = {gv: sum((p[i] - start[i]) * vector[i] for i in range(3)) / length_sq
                       for gv, p in positions.items()}
    else:
        vector = GRADIENT_AXES[gradient['direction']]
        distances = {gv: sum(p[i] * vector[i] for i in range(3)) for gv, p in positions.items()}
        low, high = min(distances.values()), max(distances.values())
        span = high - low
        if span == 0:
            return '当前选区在世界 %s 方向上没有跨度，无法生成渐变' % gradient['direction']
        if math.isinf(span):
            raise ValueError('选区坐标范围无效，未执行渐变')
        projections = {gv: (distance - low) / span for gv, distance in distances.items()}
    colors = {}
    for gv, t in projections.items():
        if math.isnan(t) or math.isinf(t):
            raise ValueError('渐变投影范围无效，未执行渐变')
        value = gradient_value(t, gradient['minimum'], gradient['maximum'], gradient['curve'])
        colors[gv] = (value,) * 3

    _plan_corner_colors(plan, colors, mask)


def _plan_corner_colors(plan, colors, mask, quantize=True):
    """Isolate the changed face corners for gradients and color paste."""

    if quantize:
        colors = {gv: tuple(quantize_component(value) if mask[i] else value
                            for i, value in enumerate(color)) for gv, color in colors.items()}
    base_count = plan['geo_count'] if plan['initialize'] else plan['map_count']
    next_id = base_count + 1
    assignments, values, changes = {}, {}, []
    for face, geo, mapping in plan['faces']:
        changed = list(mapping)
        for index, (gv, mv) in enumerate(zip(geo, mapping)):
            if gv not in colors or (plan['selected_faces'] is not None and face not in plan['selected_faces']):
                continue
            old = plan['colors'][mv]
            value = tuple(colors[gv][i] if mask[i] else old[i] for i in range(3))
            by_color = assignments.setdefault(mv, {})
            if value not in by_color:
                # Keep outside corners on the original ID. Also split two
                # selected positions when they require different final colors.
                if not by_color and mv not in plan['shared']:
                    target = mv
                else:
                    target = next_id
                    next_id += 1
                by_color[value] = target
                values[target] = value
            changed[index] = by_color[value]
        if changed != mapping:
            changes.append((face, changed))
    plan.update(gradient_values=values, gradient_faces=changes,
                gradient_map_count=next_id - 1, gradient_split=next_id - 1 - base_count)


def _write(rt, obj, plan):
    ops = rt.polyop
    channel = plan['channel']
    if plan['initialize']:
        if not plan['supported']:
            ops.setMapSupport(obj, channel, True)
        ops.setNumMapVerts(obj, channel, plan['geo_count'], keep=False)
        for mv in range(1, plan['geo_count'] + 1):
            ops.setMapVert(obj, channel, mv, rt.Point3(1, 1, 1))
        for face, geo, mapping in plan['faces']:
            ops.setMapFace(obj, channel, face, rt.Array(*mapping))

    if 'gradient_values' in plan:
        base_count = plan['geo_count'] if plan['initialize'] else plan['map_count']
        if plan['gradient_map_count'] > base_count:
            ops.setNumMapVerts(obj, channel, plan['gradient_map_count'], keep=True)
        for face, mapping in plan['gradient_faces']:
            ops.setMapFace(obj, channel, face, rt.Array(*mapping))
        for mv, value in sorted(plan['gradient_values'].items()):
            ops.setMapVert(obj, channel, mv, rt.Point3(*value))
        _set_display(rt, obj, channel)
        rt.update(obj)
        return

    clones = {}
    if plan['shared']:
        start = plan['map_count']
        ops.setNumMapVerts(obj, channel, start + len(plan['shared']), keep=True)
        for offset, mv in enumerate(sorted(plan['shared']), 1):
            clones[mv] = start + offset
        # Separate only selected corners that share colors with unselected geometry.
        for face, geo, mapping in plan['faces']:
            changed = [clones[mv] if gv in plan['selected'] and mv in clones else mv
                       for gv, mv in zip(geo, mapping)]
            if changed != mapping:
                ops.setMapFace(obj, channel, face, rt.Array(*changed))

    for mv in sorted(plan['targets']):
        value = plan['values'][mv]
        ops.setMapVert(obj, channel, clones.get(mv, mv), rt.Point3(*value))
    _set_display(rt, obj, channel)
    rt.update(obj)


def _restore(rt, obj, plan, display):
    """Raw polyop mapping writes do not register native undo in Max 2020."""
    ops = rt.polyop
    channel = plan['channel']
    if plan['initialize']:
        ops.setMapSupport(obj, channel, False)
        if plan['supported']:
            ops.setMapSupport(obj, channel, True)
    else:
        for face, geo, mapping in plan['faces']:
            ops.setMapFace(obj, channel, face, rt.Array(*mapping))
        ops.setNumMapVerts(obj, channel, plan['map_count'], keep=True)
        for mv, old in plan['colors'].items():
            ops.setMapVert(obj, channel, mv, rt.Point3(*old))
    _restore_display(obj, display)
    rt.update(obj)


def _commit_plan(rt, obj, plan, operation):
    import pymxs
    display = _display_state(obj)
    failure = None
    # Raw mapping writes do not have complete native undo; restore on failure.
    with pymxs.undo(False):
        try:
            _write(rt, obj, plan)
        except Exception as exc:
            try:
                _restore(rt, obj, plan, display)
                failure = RuntimeError('%s失败，已恢复本次修改：%s' % (operation, exc))
            except Exception as restore_error:
                failure = RuntimeError('%s失败：%s；恢复也失败，颜色可能部分更新：%s' %
                                       (operation, exc, restore_error))
    rt.redrawViews()
    if failure is not None:
        raise failure


@register_action('vertex_color_copy')
def vertex_color_copy(channel=0):
    """Snapshot one channel; each source vertex uses its lowest map-vertex ID."""
    global _COLOR_CLIPBOARD
    channel = validate_channel(channel)
    import pymxs
    rt = pymxs.runtime
    context = _edit_context(rt)
    if context.get('warning'):
        return context
    obj, selected = context['obj'], context['selected']
    if (not rt.polyop.getMapSupport(obj, channel) or
            int(rt.polyop.getNumMapVerts(obj, channel)) == 0):
        return dict(count=0, warning='%s 通道没有可复制的颜色数据，保留原剪贴板' % CHANNELS[channel][0])
    plan = _plan(rt, obj, selected, channel)
    chosen = {}
    for face, geo, mapping in plan['faces']:
        for gv, mv in zip(geo, mapping):
            if gv in selected:
                chosen[gv] = min(chosen.get(gv, mv), mv)
    if not chosen:
        return dict(count=0, warning='选区中没有可复制颜色的面顶点，保留原剪贴板')
    samples = []
    for gv, mv in sorted(chosen.items()):
        color = plan['colors'][mv]
        if any(math.isnan(v) or math.isinf(v) for v in color):
            raise ValueError('源颜色包含无效数值，保留原剪贴板')
        samples.append((gv, _world_position(rt, obj, gv), color))
    index = NearestIndex(samples)
    snapshot = dict(channel=channel, source=obj.name, count=len(samples), index=index,
                    colors_by_id={gv: color for gv, point, color in samples})
    _COLOR_CLIPBOARD = snapshot
    return dict(count=len(samples), skipped=len(selected) - len(samples), source=obj.name,
                channel=channel, channel_name=CHANNELS[channel][0])


@register_action('vertex_color_paste')
def vertex_color_paste(channel=0, mask=None, mirror_x=False, by_vertex_id=False):
    """Paste copied colors by world proximity or matching geometry vertex ID."""
    if type(mirror_x) is not bool:
        raise ValueError('镜像 X 开关必须是布尔值')
    if type(by_vertex_id) is not bool:
        raise ValueError('顶点 ID 开关必须是布尔值')
    if by_vertex_id and mirror_x:
        raise ValueError('按顶点 ID 粘贴不能与镜像 X 同时使用')
    channel = validate_channel(channel)
    mask = (True, True, True) if channel == -2 else validate_mask(mask)
    if not any(mask):
        return dict(count=0, warning='请至少勾选一个 RGB 分量')
    snapshot = _COLOR_CLIPBOARD
    if snapshot is None:
        return dict(count=0, warning='颜色剪贴板为空或已失效，请先复制顶点色')
    import pymxs
    rt = pymxs.runtime
    context = _edit_context(rt)
    if context.get('warning'):
        return context
    obj, selected = context['obj'], context['selected']
    colors = {}
    if by_vertex_id:
        source_colors = snapshot['colors_by_id']
        colors = {gv: source_colors[gv] for gv in selected if gv in source_colors}
        if not colors:
            return dict(count=0, vertices=0, skipped=len(selected),
                        warning='按顶点 ID 粘贴未找到匹配，跳过 %d 个顶点' % len(selected))
    # Unmatched corners are outside the write plan, even if they are selected
    # and share a map vertex with a matching corner.
    targets = set(colors) if by_vertex_id else selected
    plan = _plan(rt, obj, targets, channel)
    if not plan['targets']:
        if by_vertex_id:
            return dict(count=0, vertices=0, skipped=len(selected),
                        warning='没有匹配且可写入颜色的面顶点，跳过 %d 个顶点' % len(selected))
        return dict(count=0, warning='目标选区中没有可粘贴颜色的面顶点')
    if not by_vertex_id:
        active = set(gv for face, geo, mapping in plan['faces'] for gv in geo if gv in selected)
        for gv in sorted(active):
            point = _world_position(rt, obj, gv)
            # Reflecting the query is equivalent to reflecting the source across world X=0.
            if mirror_x:
                point = (-point[0], point[1], point[2])
            colors[gv] = snapshot['index'].nearest(point)[2]
    plan['selected'] = targets
    _plan_corner_colors(plan, colors, mask)
    _commit_plan(rt, obj, plan, '粘贴顶点色')
    result = dict(count=len(plan['gradient_values']), vertices=plan['geo_vertices'], object=obj.name,
                  source=snapshot['source'], source_vertices=snapshot['count'], channel=channel,
                  source_channel=snapshot['channel'], source_channel_name=CHANNELS[snapshot['channel']][0],
                  channel_name=CHANNELS[channel][0], mask=list(mask), initialized=plan['initialize'],
                  split=plan['gradient_split'], mirror_x=mirror_x)
    if by_vertex_id:
        result.update(by_vertex_id=True, skipped=len(selected) - plan['geo_vertices'])
    return result


@register_action('vertex_color')
def vertex_color(rgb=None, mask=None, channel=0, alpha=None):
    channel = validate_channel(channel)
    if channel == -2:
        alpha = validate_alpha(alpha)
        # Alpha is scalar (x); write a grayscale triplet to match Max display.
        rgb, mask = (alpha, alpha, alpha), (True, True, True)
    else:
        rgb, mask = validate_color(rgb, mask)
        if not any(mask):
            return dict(count=0, warning='请至少勾选一个 RGB 分量')
    return _edit_color(rgb, mask, channel, alpha=alpha)


def _float32(value):
    # Max Point3 stores components as float32. Compare in that same format,
    # without rounding to 8-bit values or adding a user-visible tolerance.
    return struct.unpack('f', struct.pack('f', value))[0]


@register_action('vertex_color_select')
def vertex_color_select(rgb=None, mask=None, channel=0, alpha=None, tolerance=0):
    """Filter the current geometric selection by existing map-corner values."""
    channel = validate_channel(channel)
    if isinstance(tolerance, bool) or not isinstance(tolerance, integer_types) or not 0 <= tolerance <= 255:
        raise ValueError('容差必须是 0–255 的整数')
    if channel == -2:
        alpha = validate_alpha(alpha)
        rgb, mask = (alpha, alpha, alpha), (True, False, False)
    else:
        rgb, mask = validate_color(rgb, mask)
        if not any(mask):
            return dict(count=0, warning='请至少勾选一个 RGB 分量用于比较')
    import pymxs
    rt = pymxs.runtime
    context = _edit_context(rt)
    if context.get('warning'):
        return context
    obj, selected = context['obj'], context['selected']
    ops = rt.polyop
    count = int(ops.getNumVerts(obj))
    if any(v < 1 or v > count for v in selected):
        raise ValueError('选区包含失效顶点，未改变选区')
    supported = bool(ops.getMapSupport(obj, channel))
    map_count = int(ops.getNumMapVerts(obj, channel)) if supported else 0
    if not map_count:
        return dict(count=0, warning='%s 通道没有颜色数据，未改变选区' % CHANNELS[channel][0])
    bounds = [(i, _float32(max(0, rgb[i] - tolerance) / 255.0),
               _float32(min(255, rgb[i] + tolerance) / 255.0)) for i in range(3) if mask[i]]
    dead = set(_ids(rt, ops.getDeadFaces(obj)))
    matching, verdicts = set(), {}
    for face in range(1, int(ops.getNumFaces(obj)) + 1):
        if face in dead:
            continue
        geo = [int(v) for v in ops.getFaceVerts(obj, face)]
        if not selected.intersection(geo):
            continue
        mapping = [int(v) for v in ops.getMapFace(obj, channel, face)]
        if len(mapping) != len(geo):
            raise ValueError('%s 通道的面映射无效，未改变选区' % CHANNELS[channel][0])
        for gv, mv in zip(geo, mapping):
            if gv not in selected:
                continue
            if not 1 <= mv <= map_count:
                raise ValueError('%s 通道的颜色顶点无效，未改变选区' % CHANNELS[channel][0])
            if mv not in verdicts:
                p = ops.getMapVert(obj, channel, mv)
                values = (float(p.x), float(p.y), float(p.z))
                match = True
                for i, low, high in bounds:
                    value = values[i]
                    if math.isnan(value) or math.isinf(value):
                        raise ValueError('比较分量包含无效颜色数值，未改变选区')
                    value = _float32(value)
                    match = match and low <= value <= high
                verdicts[mv] = match
            if verdicts[mv]:
                matching.add(gv)
    # Read and validate everything before changing either selection or level.
    previous = _ids(rt, ops.getVertSelection(obj))
    level = int(rt.subObjectLevel)
    try:
        ops.setVertSelection(obj, rt.Array(*sorted(matching)))
        rt.subObjectLevel = 1
        rt.redrawViews()
    except Exception as exc:
        try:
            ops.setVertSelection(obj, rt.Array(*previous))
            rt.subObjectLevel = level
            rt.redrawViews()
        except Exception as restore_error:
            raise RuntimeError('按颜色选择失败：%s；恢复选区也失败：%s' % (exc, restore_error))
        raise RuntimeError('按颜色选择失败，已恢复原选区：%s' % exc)
    return dict(count=len(matching), searched=len(selected), object=obj.name, channel=channel,
                channel_name=CHANNELS[channel][0], rgb=list(rgb), mask=list(mask),
                alpha=alpha if channel == -2 else None, tolerance=tolerance)


@register_action('vertex_color_scale')
def vertex_color_scale(factor=1.0, mask=None, channel=0):
    channel = validate_channel(channel)
    factor = validate_factor(factor)
    mask = (True, True, True) if channel == -2 else validate_mask(mask)
    if not any(mask):
        return dict(count=0, warning='请至少勾选一个 RGB 分量')
    return _edit_color(None, mask, channel, factor=factor)


@register_action('vertex_color_gradient')
def vertex_color_gradient(direction='-Z', minimum=0, maximum=255, curve=50,
                          mask=None, channel=0, start=None, end=None):
    channel = validate_channel(channel)
    if direction not in ('X', 'Y', 'Z', '-X', '-Y', '-Z', 'custom', 'topology'):
        raise ValueError('渐变方向必须是世界 X/Y/Z、-X/-Y/-Z、自定义两点或沿拓扑')
    for value, label, lower, upper in ((minimum, '最小值', 0, 255), (maximum, '最大值', 0, 255), (curve, '曲线', 25, 75)):
        if isinstance(value, bool) or not isinstance(value, integer_types) or not lower <= value <= upper:
            raise ValueError('%s 必须是 %s–%s 的整数' % (label, lower, upper))
    mask = (True, True, True) if channel == -2 else validate_mask(mask)
    if not any(mask):
        return dict(count=0, warning='请至少勾选一个 RGB 分量')
    if direction == 'custom':
        if start is None or end is None:
            return dict(count=0, warning='请先设置自定义渐变的起点和终点')
        start, end = validate_gradient_point(start), validate_gradient_point(end)
        vector = tuple(b - a for a, b in zip(start, end))
        length_sq = sum(v * v for v in vector)
        if length_sq == 0:
            return dict(count=0, warning='起点和终点重合，无法生成渐变')
        if math.isinf(length_sq) or math.isnan(length_sq):
            raise ValueError('起点和终点的距离无效，未执行渐变')
    gradient = dict(direction=direction, minimum=minimum, maximum=maximum, curve=curve,
                    start=start if direction in ('custom', 'topology') else None,
                    end=end if direction in ('custom', 'topology') else None)
    return _edit_color(None, mask, channel, gradient=gradient)


def _edit_context(rt, require_selection=True):
    if int(rt.maxVersion()[0]) != 22000:
        return dict(count=0, warning='此插件首版仅支持 3ds Max 2020')
    objects = list(rt.selection)
    if len(objects) != 1:
        return dict(count=0, warning='请只选择一个 Editable Poly 对象')
    obj = objects[0]
    if rt.classOf(obj) != rt.Editable_Poly:
        return dict(count=0, warning='请选择 Editable Poly 对象，不自动转换模型')
    if rt.modPanel.getCurrentObject() != obj.baseObject:
        return dict(count=0, warning='请在修改面板激活 Editable Poly 基础对象')
    level = int(rt.subObjectLevel)
    if require_selection and level not in (1, 2, 3, 4, 5):
        return dict(count=0, warning='请进入顶点、边、边界、面或元素层级并选择')
    selected = _selection(rt, obj, level) if require_selection else set()
    if require_selection and not selected:
        return dict(count=0, warning='当前子对象选区为空，未修改颜色')
    return dict(obj=obj, selected=selected)


def _topology_snapshot(rt, obj):
    ops = rt.polyop
    dead = set(_ids(rt, ops.getDeadFaces(obj)))
    faces = [(i, tuple(int(v) for v in ops.getFaceVerts(obj, i)))
             for i in range(1, int(ops.getNumFaces(obj)) + 1) if i not in dead]
    payload = (int(ops.getNumVerts(obj)), int(ops.getNumEdges(obj)), int(ops.getNumFaces(obj)), faces)
    return faces, hashlib.sha256(repr(payload).encode('ascii')).hexdigest()


@register_action('vertex_color_gradient_edges')
def vertex_color_gradient_edges(reference=None):
    """Record a chain/ring; expand and highlight an unambiguous single edge."""
    import pymxs
    rt = pymxs.runtime
    context = _edit_context(rt, require_selection=False)
    if context.get('warning'):
        return context
    if int(rt.subObjectLevel) not in (2, 3):
        return dict(count=0, warning='请进入边层级，选择一条完整的起始或结束横线')
    obj = context['obj']
    ids = _ids(rt, rt.polyop.getEdgeSelection(obj))
    if not ids:
        return dict(count=0, warning='当前没有选中横线，请在边层级选择完整横线')
    faces, signature = _topology_snapshot(rt, obj)
    try:
        if any(i < 1 or i > int(rt.polyop.getNumEdges(obj)) for i in ids):
            raise ValueError('横线包含失效边，请重新选择')
        edges = [tuple(sorted(int(v) for v in rt.polyop.getEdgeVerts(obj, i))) for i in ids]
        check_chain(edges)
        live = set(edge for unused, face in faces for edge in face_edges(face))
        if any(edge not in live for edge in edges):
            raise ValueError('横线包含失效边，请重新选择')
        try:
            edges = complete_row([face for unused, face in faces], edges)
        except ValueError:
            record = _TOPOLOGY_RECORDS.get(reference) if isinstance(reference, (type(u''), type(b''))) else None
            if len(ids) != 1 or record is None:
                raise ValueError('单条边方向不明确，请先记录另一端完整横线，或选择本端完整横线')
            if (not rt.isValidNode(record['node']) or record['node'] != obj or record['base'] != obj.baseObject or
                    record['signature'] != signature):
                raise ValueError('参考横线已失效或不属于当前对象，请重新记录另一端完整横线')
            edges = complete_row_from_reference([face for unused, face in faces], edges[0], record['edges'])
    except ValueError as exc:
        return dict(count=0, warning=text_type(exc))
    if len(edges) > len(ids):
        wanted = set(edges)
        expanded = [i for i in range(1, int(rt.polyop.getNumEdges(obj)) + 1)
                    if tuple(sorted(int(v) for v in rt.polyop.getEdgeVerts(obj, i))) in wanted]
        if len(expanded) != len(edges):
            return dict(count=0, warning='自动补全包含失效边，请重新选择')
        try:
            rt.polyop.setEdgeSelection(obj, rt.Array(*expanded))
            rt.redrawViews()
        except Exception:
            rt.polyop.setEdgeSelection(obj, rt.Array(*ids))
            raise
    token = uuid.uuid4().hex
    _TOPOLOGY_RECORDS[token] = dict(node=obj, base=obj.baseObject, signature=signature, edges=edges)
    while len(_TOPOLOGY_RECORDS) > 64:
        _TOPOLOGY_RECORDS.popitem(last=False)
    return dict(record=token, object=obj.name, edges=len(edges), vertices=len(set(v for edge in edges for v in edge)),
                expanded=len(edges) > len(ids))


def _topology_selection(rt, obj, gradient):
    records = []
    for token in (gradient['start'], gradient['end']):
        if not isinstance(token, type(u'')) and not isinstance(token, type(b'')):
            raise ValueError('请先记录沿拓扑渐变的起始和结束横线')
        record = _TOPOLOGY_RECORDS.get(token)
        if record is None:
            raise ValueError('横线记录已失效，请重新记录起始和结束横线')
        if not rt.isValidNode(record['node']) or record['node'] != obj or record['base'] != obj.baseObject:
            raise ValueError('当前对象与横线记录不一致，请重新记录起始和结束横线')
        records.append(record)
    faces, signature = _topology_snapshot(rt, obj)
    if any(record['signature'] != signature for record in records):
        raise ValueError('模型拓扑已经变化，请重新记录起始和结束横线')
    result = row_weights([face for unused, face in faces], records[0]['edges'], records[1]['edges'])
    gradient.update(projections=result['values'], rows=result['rows'])
    return set(result['values']), set(faces[i][0] for i in result['faces'])


@register_action('vertex_color_gradient_point')
def vertex_color_gradient_point():
    """Read-only world-space centroid of the current expanded selection."""
    import pymxs
    rt = pymxs.runtime
    context = _edit_context(rt)
    if context.get('warning'):
        return context
    obj, selected = context['obj'], context['selected']
    count = int(rt.polyop.getNumVerts(obj))
    if any(v < 1 or v > count for v in selected):
        raise ValueError('选区包含失效顶点，请重新选择')
    positions = [_world_position(rt, obj, gv) for gv in sorted(selected)]
    center = [math.fsum(p[i] for p in positions) / len(positions) for i in range(3)]
    return dict(point=center, vertices=len(selected), object=obj.name)


def _edit_color(rgb, mask, channel, alpha=None, factor=None, gradient=None):
    import pymxs
    rt = pymxs.runtime
    topology = gradient is not None and gradient['direction'] == 'topology'
    context = _edit_context(rt, require_selection=not topology)
    if context.get('warning'):
        return context
    obj, selected = context['obj'], context['selected']
    selected_faces = None
    if topology:
        try:
            selected, unused_region = _topology_selection(rt, obj, gradient)
            # Scope the gradient by geometric vertices, including both sides
            # of each endpoint row. Face-scoping leaves the outside corner of
            # the very same vertex at its old color and creates a visible seam.
            # _plan still isolates map IDs shared with unselected vertices.
        except ValueError as exc:
            return dict(count=0, warning=text_type(exc))
    plan = _plan(rt, obj, selected, channel, selected_faces=selected_faces)
    if not plan['targets']:
        return dict(count=0, warning='选区中没有可写入颜色的面顶点')
    plan['selected'] = selected
    # Read fresh colors and round only the components being written.
    if gradient is not None:
        warning = _plan_gradient(rt, obj, plan, gradient, mask)
        if warning:
            return dict(count=0, warning=warning)
        operation = '应用渐变'
    else:
        plan['values'] = {mv: (merge_color(old, rgb, mask) if factor is None else
                               scale_color(old, factor, mask, channel))
                          for mv, old in plan['colors'].items()}
        operation = '设置颜色' if factor is None else '缩放颜色'
    _commit_plan(rt, obj, plan, operation)
    result = dict(count=len(plan.get('gradient_values', plan['targets'])), vertices=plan['geo_vertices'],
                object=obj.name, mask=list(mask),
                channel=channel, channel_name=CHANNELS[channel][0], alpha=alpha if channel == -2 else None,
                initialized=plan['initialize'], split=plan.get('gradient_split', len(plan['shared'])))
    if gradient is not None:
        result.update(direction=gradient['direction'], minimum=gradient['minimum'],
                      maximum=gradient['maximum'], curve=gradient['curve'],
                      start=gradient['start'], end=gradient['end'])
        if topology:
            result['rows'] = gradient['rows']
    elif factor is None:
        result['rgb'] = list(rgb)
    else:
        result['factor'] = factor
    return result
