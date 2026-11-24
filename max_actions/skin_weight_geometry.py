# -*- coding: utf-8 -*-
"""Max 2020 skin-input geometry and native closest-surface mapping (Python 2.7)."""
from __future__ import unicode_literals

import math
import os

from max_actions.skin_transfer import _array_file, _progress

try:
    text_type = unicode
except NameError:
    text_type = str


def _finite(value):
    return not (math.isnan(value) or math.isinf(value))


def _xyz(point):
    return tuple(float(value) for value in (point.x, point.y, point.z))


def input_geometry(rt, node, skin, vertices):
    """Evaluate the input of Skin; restore every modifier even if evaluation fails."""
    modifiers = list(node.modifiers)
    disabled = []
    try:
        if skin is not None:
            index = next(i for i, modifier in enumerate(modifiers) if modifier == skin)
            for modifier in modifiers[:index + 1]:
                disabled.append((modifier, bool(modifier.enabled)))
                modifier.enabled = False
        mesh = rt.snapshotAsMesh(node)
    finally:
        for modifier, enabled in reversed(disabled):
            modifier.enabled = enabled
    if int(rt.getNumVerts(mesh)) != vertices:
        raise ValueError('蒙皮输入顶点数不一致，无法进行空间匹配')
    inverse = rt.inverse(node.objectTransform)
    get_vertex, get_face = rt.getVert, rt.getFace
    points = [_xyz(get_vertex(mesh, index) * inverse) for index in range(1, vertices + 1)]
    if not all(_finite(value) for point in points for value in point):
        raise ValueError('几何坐标包含非有限数')
    triangles = [int(value) - 1 for index in range(1, int(rt.getNumFaces(mesh)) + 1)
                 for value in _xyz(get_face(mesh, index))]
    if not triangles or any(value < 0 or value >= vertices for value in triangles):
        raise ValueError('模型没有可用的三角形表面或顶点编号无效')
    return points, triangles


def export_geometry(rt, directory, entry, write):
    points, triangles = input_geometry(rt, entry['node'], entry['skin'], entry['vertices'])
    prefix = os.path.join(directory, 'mesh-%d' % entry['index'])
    write(prefix + '.points', [value for point in points for value in point])
    write(prefix + '.triangles', triangles, 'I')
    entry['geometry'] = dict(triangles=len(triangles) // 3, space='object', positions='skin_input')


def position_key(point, decimals):
    # float formatting uses ties-to-even on both Python 2 and Python 3.
    # Python 2 round() itself uses a different midpoint rule.
    return tuple(float(format(float(value), '.%df' % decimals)) for value in point)


def map_geometry(rt, directory, entries, decimals, skin_modifiers, write):
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 12:
        raise ValueError('坐标匹配小数位必须在 0～12 之间')
    reports, errors = [], []
    for entry in entries:
        temporary = intersector = None
        try:
            node = rt.getAnimByHandle(entry['node_handle'])
            if node is None or not rt.isValidNode(node):
                raise ValueError('目标模型已不存在')
            skins = skin_modifiers(rt, node)
            if len(skins) > 1:
                raise ValueError('多层 Skin 暂不支持最近表面点匹配')
            points_target, _ = input_geometry(rt, node, skins[0] if skins else None, entry['target_vertices'])
            prefix = os.path.join(directory, 'mesh-%d' % entry['index'])
            flat = _array_file(prefix + '.points', entry['vertices'] * 3, 'd')
            points = [tuple(flat[index:index + 3]) for index in range(0, len(flat), 3)]
            triangles = _array_file(prefix + '.triangles', entry['geometry']['triangles'] * 3, 'I')
            if not all(_finite(value) for value in flat) or any(v >= len(points) for v in triangles):
                raise ValueError('源几何坐标或三角形编号无效')
            lookup = {}
            for index, point in enumerate(points):
                key = position_key(point, decimals)
                lookup[key] = None if key in lookup else index
            ids, factors, interpolated = [], [], []
            for vertex, point in enumerate(points_target):
                if vertex % 2048 == 0:
                    _progress('空间匹配 %s：%d/%d' % (entry['name'], vertex, len(points_target)))
                match = lookup.get(position_key(point, decimals))
                if match is not None:
                    ids.extend((match, match, match))
                    factors.extend((1., 0., 0.))
                    continue
                if intersector is None:
                    # Query in object coordinates on an identity-transform temporary mesh.
                    temporary = rt.mesh(vertices=rt.Array(*(rt.Point3(*p) for p in points)),
                                        faces=rt.Array(*(rt.Point3(*(int(v) + 1 for v in triangles[i:i+3]))
                                                         for i in range(0, len(triangles), 3))))
                    rt.update(temporary)
                    temporary.isHidden = True
                    intersector = rt.MeshProjIntersect()
                    intersector.SetNode(temporary)
                    intersector.Build()
                    # Required native initialization; ray result is deliberately unused.
                    intersector.IntersectRay(rt.Point3(*points[0]), rt.Point3(0, 0, 1), doubleSided=True)
                if not intersector.ClosestFace(rt.Point3(*point), doubleSided=True):
                    raise ValueError('无法找到源模型最近表面点')
                face = int(intersector.GetHitFace())
                if not 0 <= face < len(triangles) // 3:
                    raise ValueError('Max 返回的三角形编号无效')
                raw_bary = _xyz(intersector.GetHitBary())
                if not all(_finite(value) for value in raw_bary):
                    raise ValueError('Max 返回了非有限插值比例')
                bary = [max(0., min(1., value)) for value in raw_bary]
                total = sum(bary)
                if not all(_finite(value) for value in bary) or not _finite(total) or total <= 0:
                    raise ValueError('Max 返回的插值比例无效')
                ids.extend(triangles[face * 3:face * 3 + 3])
                factors.extend(value / total for value in bary)
                interpolated.append(vertex)
            write(prefix + '.match_ids', ids, 'I')
            write(prefix + '.match_factors', factors)
            write(prefix + '.interpolated_ids', interpolated, 'I')
            reports.append(dict(index=entry['index'], direct_vertices=len(points_target) - len(interpolated),
                                interpolated_vertices=len(interpolated)))
        except Exception as exc:
            errors.append(dict(mesh=entry['mesh'], error=text_type(exc), status='skipped'))
        finally:
            try:
                if intersector is not None: intersector.Free()
            finally:
                if temporary is not None: rt.delete(temporary)
    return dict(reports=reports, errors=errors)

