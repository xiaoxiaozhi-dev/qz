"""Validate correspondence before writing Blender base-mesh coordinates."""
from collections import Counter, defaultdict
from math import dist, floor, isfinite
from pathlib import Path

from blender_actions.skin_transfer_maya import NEIGHBORS, POSITION_TOLERANCE, _face_key, _read


def _target(name):
    import bpy

    context = bpy.context
    layer = context.view_layer
    if layer is None or context.mode != 'OBJECT':
        raise ValueError('请先切换到 Blender 物体模式')
    meshes = [obj for obj in context.scene.objects if obj.type == 'MESH']
    candidates = [obj for obj in meshes if obj.name == name]
    fallback = name.rsplit(':', 1)[-1]
    if not candidates and fallback != name:
        candidates = [obj for obj in meshes if obj.name == fallback]
    if not candidates:
        tried = f'{name}、{fallback}' if fallback != name else name
        raise ValueError(f'Blender 当前场景找不到同名网格（查找：{tried}）')
    if len(candidates) != 1:
        raise ValueError(f'Blender 当前场景有 {len(candidates)} 个同名网格：{candidates[0].name}（Maya：{name}）')
    obj, = candidates
    data = obj.data
    if obj.library or data.library or obj.override_library or data.users != 1 or data.shape_keys or data.is_editmode:
        raise ValueError(f'Blender 网格 {obj.name} 不可写：需要本地单用户网格，且不能有形态键或处于编辑状态')
    token = dict(scene=context.scene.as_pointer(), object=obj.as_pointer(),
                 data=data.as_pointer(), name=obj.name)
    return obj, token


def target_maya(name):
    return _target(name)[1]


def _topology(data, folder, source):
    if (len(data.polygons), len(data.loops), len(data.edges)) != (
            source['faces'], source['corners'], source['edges']):
        raise ValueError('Maya 与 Blender 的拓扑数量不一致')
    topology = _read(folder / 'mesh-0.faces', source['faces'] + source['corners'], 'I')
    sizes, vertex_ids = topology[:source['faces']], topology[source['faces']:]
    start = 0
    faces = Counter()
    for count in sizes:
        face = vertex_ids[start:start + count]
        if count < 3 or len(face) != count or len(set(face)) != count or max(face) >= len(data.vertices):
            raise ValueError('Maya 面拓扑无效')
        faces[_face_key(face)] += 1
        start += count
    if start != len(vertex_ids) or faces != Counter(_face_key(list(face.vertices)) for face in data.polygons):
        raise ValueError('Maya 与 Blender 的同索引面拓扑不一致')
    edges = _read(folder / 'mesh-0.edges', 2 * source['edges'], 'I')
    if Counter(tuple(sorted(pair)) for pair in zip(edges[::2], edges[1::2])) != Counter(
            tuple(sorted(edge.vertices)) for edge in data.edges):
        raise ValueError('Maya 与 Blender 的同索引边拓扑不一致')


def _cell(point):
    return tuple(floor(value / POSITION_TOLERANCE) for value in point)


def _buckets(points):
    result = defaultdict(list)
    for index, point in enumerate(points):
        result[_cell(point)].append((index, point))
    return result


def _unique_match(point, candidates, expected):
    cell = _cell(point)
    found = False
    for delta in NEIGHBORS:
        adjacent = tuple(value + offset for value, offset in zip(cell, delta))
        for index, candidate in candidates.get(adjacent, ()):
            if dist(point, candidate) <= POSITION_TOLERANCE:
                if index != expected or found:
                    return False
                found = True
    return found


def _verify_indices(data, source, target):
    # Assign unique labels only to unchanged vertices with mutual unique spatial
    # matches. The common indexed graph then propagates these labels to moved vertices.
    source_cells, target_cells = _buckets(source), _buckets(target)
    labels = [index + 1 if dist(point, target[index]) <= POSITION_TOLERANCE
              and _unique_match(point, target_cells, index)
              and _unique_match(target[index], source_cells, index) else 0
              for index, point in enumerate(source)]
    classes = len(set(labels))
    if classes == len(labels):
        return
    adjacency = [[] for _ in labels]
    for edge in data.edges:
        a, b = edge.vertices
        adjacency[a].append(b)
        adjacency[b].append(a)

    # A bounded refinement is conservative on unresolved symmetries and large
    # regions without anchors. It never chooses one vertex from a tied class.
    for _ in range(32):
        palette = {}
        labels = [palette.setdefault((label, tuple(sorted(labels[j] for j in neighbors))), len(palette))
                  for label, neighbors in zip(labels, adjacency)]
        if len(palette) == len(labels):
            return
        if len(palette) == classes:
            break
        classes = len(palette)
    raise ValueError('拓扑和未移动的坐标锚点无法唯一确认全部顶点编号')


def receive_maya(directory, source, target):
    import bpy
    from mathutils import Vector

    folder = Path(directory).resolve()
    if not folder.is_dir() or not folder.name.startswith('qz-vertex-transfer-'):
        raise ValueError('无效的顶点交换目录')
    obj, token = _target(source['name'])
    if token != target:
        raise ValueError(f'Blender 目标网格 {target["name"]} 已变化（当前匹配：{obj.name}）')
    data = obj.data
    count = len(data.vertices)
    if not count or count != source['vertices']:
        raise ValueError('Maya 与 Blender 的顶点数量不一致')
    _topology(data, folder, source)
    flat = _read(folder / 'mesh-0.points', count * 3, 'd')
    points = list(zip(flat[::3], flat[1::3], flat[2::3]))
    units = bpy.context.scene.unit_settings
    scale = units.scale_length if units.system != 'NONE' else 1.0
    world = obj.matrix_world.copy()
    target_points = [tuple(value * scale for value in (world @ vertex.co)) for vertex in data.vertices]
    if not all(isfinite(value) for point in points + target_points for value in point):
        raise ValueError('顶点位置包含非有限数值')
    _verify_indices(data, points, target_points)
    inverse = world.inverted()
    output = [value for point in points for value in inverse @ Vector([value / scale for value in point])]
    if not all(map(isfinite, output)):
        raise ValueError('目标局部坐标超出 Blender 可用范围')

    previous = [0.] * (count * 3)
    data.vertices.foreach_get('co', previous)
    try:
        data.vertices.foreach_set('co', output)
        data.update()
    except Exception:
        data.vertices.foreach_set('co', previous)
        data.update()
        raise
    return dict(name=obj.name, vertices=count)


ACTIONS = {'vertex_positions_target_maya': target_maya,
           'vertex_positions_receive_maya': receive_maya}
