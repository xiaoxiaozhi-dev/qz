# -*- coding: utf-8 -*-
"""Max-side snapshot and guarded vertex channel write for topology paste (Python 2.7)."""
from __future__ import unicode_literals

import hashlib
import math
import uuid

from max_bridge.server import register_action
from max_actions import vertex_color as color

# Keep one short-lived target record. Only apply consumes it. A fresh snapshot
# replaces it; reload/disconnect clears action state along with other clipboard data.
_TARGET = None


def _context(rt):
    objects = list(rt.selection)
    if len(objects) != 1:
        raise ValueError('请只选择一个 Editable Poly 模型')
    node = objects[0]
    if rt.classOf(node) != rt.Editable_Poly or rt.modPanel.getCurrentObject() != node.baseObject:
        raise ValueError('请在修改面板激活 Editable Poly 基础对象')
    level = int(rt.subObjectLevel)
    if level not in (1, 2, 3, 4, 5):
        raise ValueError('请进入子对象层级并选择要复制或粘贴的区域')
    selected = sorted(color._selection(rt, node, level))
    if not selected:
        raise ValueError('当前子对象选区为空，未修改颜色')
    return node, selected


def _geometry(rt, node, selected):
    obj = node.baseObject
    ops = rt.polyop
    faces, _ = color._topology_snapshot(rt, obj)
    count = int(ops.getNumVerts(obj))
    points = []
    for i in range(1, count + 1):
        p = ops.getVert(obj, i)
        points.append(color.validate_gradient_point([float(p.x), float(p.y), float(p.z)]))
    uv = None
    if ops.getMapSupport(obj, 1):
        map_count = int(ops.getNumMapVerts(obj, 1))
        if map_count:
            coordinates, uv = {}, []
            for fid, face in faces:
                mids = [int(m) for m in ops.getMapFace(obj, 1, fid)]
                if len(face) != len(mids) or any(not 1 <= m <= map_count for m in mids):
                    uv = None
                    break
                row = []
                for m in mids:
                    if m not in coordinates:
                        p = ops.getMapVert(obj, 1, m)
                        if any(math.isnan(v) or math.isinf(v) for v in (float(p.x), float(p.y))):
                            break
                        coordinates[m] = (float(p.x), float(p.y))
                    row.append(coordinates[m])
                if len(row) != len(face):
                    uv = None
                    break
                uv.append(row)
    stamp = hashlib.sha256(repr((selected, faces, points, uv)).encode('ascii')).hexdigest()
    return dict(points=points, faces=[face for _, face in faces], uv=uv, selected=selected), stamp


@register_action('vertex_color_match_snapshot')
def vertex_color_match_snapshot(source=False, channel=0):
    if type(source) is not bool:
        raise ValueError('无效的拓扑复制操作')
    channel = color.validate_channel(channel)
    channel_name = color.CHANNELS[channel][0]
    from pymxs import runtime as rt
    global _TARGET
    if not source:
        _TARGET = None
    try:
        node, selected = _context(rt)
        result = dict(object=node.name, channel=channel, channel_name=channel_name)
        if source:
            if not rt.polyop.getMapSupport(node, channel) or not int(rt.polyop.getNumMapVerts(node, channel)):
                raise ValueError('源模型没有 %s 通道的数据' % channel_name)
            plan = color._plan(rt, node, set(selected), channel)
            values = {}
            selected_set = set(selected)
            for _, face, mids in plan['faces']:
                for v, m in zip(face, mids):
                    if v not in selected_set:
                        continue
                    rgb = plan['colors'][m]
                    if any(math.isnan(c) or math.isinf(c) for c in rgb):
                        raise ValueError('选区含无效的 %s 数值，未复制' % channel_name)
                    if values.setdefault(v, rgb) != rgb:
                        raise ValueError('选区有顶点在不同面上使用不同颜色，拓扑复制暂不支持颜色接缝')
            if not values:
                raise ValueError('选区没有带颜色的面顶点')
            result.update(colors=sorted(values.items()), count=len(values), skipped=len(selected) - len(values))
        mesh, stamp = _geometry(rt, node, selected)
    except ValueError as exc:
        return dict(warning=color.text_type(exc))
    result.update(mesh)
    if not source:
        token = uuid.uuid4().hex
        _TARGET = token, node, stamp, channel
        result['token'] = token
    return result


@register_action('vertex_color_match_apply')
def vertex_color_match_apply(token=None, patch=None, mask=None):
    from pymxs import runtime as rt
    global _TARGET
    if _TARGET is None or token != _TARGET[0]:
        return dict(warning='目标模型的拓扑快照已失效，请重新粘贴')
    _, saved_node, stamp, channel = _TARGET
    _TARGET = None
    mask = (True, True, True) if channel == -2 else color.validate_mask(mask)
    if not any(mask) or not isinstance(patch, list) or not patch:
        raise ValueError('没有有效的拓扑颜色写入')
    try:
        node, selected = _context(rt)
        if not rt.isValidNode(saved_node) or node != saved_node or _geometry(rt, node, selected)[1] != stamp:
            raise ValueError('计算期间目标模型或选区已变化，未粘贴')
    except ValueError as exc:
        return dict(warning=color.text_type(exc))
    selected = set(selected)
    values = {}
    for row in patch:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError('拓扑颜色数据无效，未粘贴')
        v, rgb = row
        if (isinstance(v, bool) or not isinstance(v, color.integer_types)
                or v not in selected or v in values):
            raise ValueError('拓扑颜色超出目标选区或有重复，未粘贴')
        values[v] = color.validate_gradient_point(rgb)
    plan = color._plan(rt, node, set(values), channel)
    if plan['geo_vertices'] != len(values):
        return dict(warning='目标选区有无法写入的面顶点，未粘贴')
    color._plan_corner_colors(plan, values, mask, quantize=False)
    color._commit_plan(rt, node, plan, '按拓扑粘贴')
    return dict(object=node.name, vertices=len(values), skipped=len(selected) - len(values),
                channel=channel, channel_name=color.CHANNELS[channel][0])
