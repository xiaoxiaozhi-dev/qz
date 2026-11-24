# -*- coding: utf-8 -*-
"""Apply world-space positions to existing base meshes, Max2020/Python2.7."""
from __future__ import unicode_literals

from array import array
import math
import os
import sys

from max_bridge.server import register_action

try:
    text_type = unicode
    integer_types = (int, long)
except NameError:
    text_type = str
    integer_types = (int,)

VERSION = 1


def _finite(value):
    return not (math.isnan(value) or math.isinf(value))


def _check_bridge():
    from max_bridge import server
    if not getattr(server, 'SUPPORTS_EXECUTION_TIMEOUT', False):
        raise RuntimeError('Max 桥接需要更新，请重新启动新版桥接')


@register_action('vertex_positions_max_preflight')
def preflight():
    _check_bridge()
    return dict(version=VERSION, space='world', target='base_mesh', path=os.path.abspath(__file__))


def _read(directory, mesh):
    index, count = mesh.get('index'), mesh.get('vertices')
    if any(isinstance(value, bool) or not isinstance(value, integer_types) for value in (index, count)) or index < 0 or count < 1:
        raise ValueError('顶点文件描述无效')
    path = os.path.join(directory, 'mesh-%d.points' % index)
    if os.path.getsize(path) != count * 24:
        raise ValueError('顶点文件长度与数量不符')
    values = array('d')
    with open(path, 'rb') as stream:
        values.fromfile(stream, count * 3)
    if sys.byteorder != 'little':
        values.byteswap()
    if not all(_finite(value) for value in values):
        raise ValueError('世界顶点位置包含非有限数')
    return values


def _operations(rt, base):
    if rt.isKindOf(base, rt.Editable_Poly):
        return rt.polyop
    if rt.isKindOf(base, rt.Editable_mesh):
        return rt.meshop
    raise ValueError('目标基础对象需要为 Editable Poly 或 Editable Mesh，不自动转换或塌陷')


def _local_points(rt, node, values):
    matrix = node.objectTransform
    rows = [[float(value) for value in row] for row in (matrix.row1, matrix.row2, matrix.row3)]
    if not all(_finite(value) for row in rows for value in row):
        raise ValueError('目标对象变换包含非有限数')
    a, b, c = rows
    determinant = a[0] * (b[1]*c[2]-b[2]*c[1]) - a[1] * (b[0]*c[2]-b[2]*c[0]) + a[2] * (b[0]*c[1]-b[1]*c[0])
    length = math.sqrt(sum(v*v for v in a)) * math.sqrt(sum(v*v for v in b)) * math.sqrt(sum(v*v for v in c))
    if not _finite(determinant) or not _finite(length) or not length or abs(determinant) <= length * 1e-12:
        raise ValueError('目标对象变换不可逆，无法应用世界顶点位置')
    inverse = rt.inverse(matrix)
    points = []
    for start in range(0, len(values), 3):
        point = rt.Point3(*values[start:start + 3]) * inverse
        if not all(_finite(float(value)) for value in point):
            raise ValueError('转换后的顶点位置超出有效范围')
        restored_world = point * matrix
        if any(not _finite(float(a)) or abs(float(a)-b) > max(1e-4, abs(b)*1e-6)
               for a, b in zip(restored_world, values[start:start + 3])):
            raise ValueError('目标变换精度不足，无法准确还原世界顶点位置')
        points.append(point)
    return rt.Array(*points)


def _apply(rt, node, base, ops, points):
    all_vertices = rt.Name('all')
    original = ops.getVerts(base, all_vertices)
    if len(original) != len(points):
        raise ValueError('读取目标顶点时数量发生变化')
    try:
        ops.setVert(base, all_vertices, points)
        rt.update(node)
        actual = ops.getVerts(base, all_vertices)
        if len(actual) != len(points) or any(
                not _finite(float(a)) or abs(float(a) - float(b)) > max(1e-5, abs(float(b))*1e-6)
                for current, expected in zip(actual, points) for a, b in zip(current, expected)):
            raise RuntimeError('基础网格顶点位置写入校验失败')
    except Exception as exc:
        try:
            ops.setVert(base, all_vertices, original)
            rt.update(node)
            restored = ops.getVerts(base, all_vertices)
            if len(restored) != len(original) or any(
                    not _finite(float(a)) or abs(float(a)-float(b)) > max(1e-5, abs(float(b))*1e-6)
                    for current, expected in zip(restored, original) for a, b in zip(current, expected)):
                raise RuntimeError('恢复顶点位置后的校验失败')
        except Exception as restore:
            raise RuntimeError('%s；恢复原顶点失败，目标可能部分更新：%s' % (text_type(exc), text_type(restore)))
        raise RuntimeError('%s；已恢复原基础网格顶点' % text_type(exc))


@register_action('vertex_positions_import')
def import_positions(directory, meshes):
    import pymxs
    rt = pymxs.runtime
    _check_bridge()
    directory = os.path.realpath(directory)
    if not os.path.isdir(directory) or not os.path.basename(directory).startswith('qz-vertex-transfer-'):
        raise ValueError('无效的顶点位置交换目录')
    objects = list(rt.objects)
    names = {}
    for node in objects:
        names.setdefault(text_type(node.name), []).append(node)
    source_names = [mesh['name'] for mesh in meshes]
    reports, errors = [], []
    rt.disableSceneRedraw()
    try:
        with pymxs.animate(False):
            for mesh in meshes:
                writing = False
                try:
                    if source_names.count(mesh['name']) != 1:
                        raise ValueError('发送列表中有同名模型，无法唯一匹配')
                    candidates = names.get(mesh['name'], [])
                    if len(candidates) != 1:
                        raise ValueError('Max 中同名模型数量为 %d，需要唯一匹配' % len(candidates))
                    node = candidates[0]
                    base = node.baseObject
                    if sum(1 for other in objects if other.baseObject == base) != 1:
                        raise ValueError('目标基础网格被其他实例共用，已跳过以免修改其他对象')
                    ops = _operations(rt, base)
                    values = _read(directory, mesh)
                    if int(ops.getNumVerts(base)) != mesh['vertices']:
                        raise ValueError('Max 基础网格顶点数量与 Maya 不一致')
                    points = _local_points(rt, node, values)
                    writing = True
                    _apply(rt, node, base, ops, points)
                    reports.append(dict(name=mesh['name'], vertices=mesh['vertices']))
                except Exception as exc:
                    errors.append(dict(mesh=mesh.get('mesh', mesh['name']), error=text_type(exc),
                                       status='failed' if writing else 'skipped'))
    finally:
        rt.enableSceneRedraw()
        rt.redrawViews()
    return dict(count=len(reports), vertices=sum(report['vertices'] for report in reports),
                reports=reports, errors=errors)
