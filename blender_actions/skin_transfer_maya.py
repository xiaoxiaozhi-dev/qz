"""Replace mapped weights on an existing Blender mesh after full validation."""
from array import array
from collections import Counter, defaultdict
from itertools import product
from math import dist, floor, isfinite
from pathlib import Path
import sys


POSITION_TOLERANCE = 1e-5  # meters, in the original world placement
NEIGHBORS = tuple(product((-1, 0, 1), repeat=3))


def _target():
    import bpy

    context = bpy.context
    layer = context.view_layer
    if layer is None or context.mode != 'OBJECT':
        raise ValueError('请切换到 Blender 物体模式')
    obj = layer.objects.active
    if obj is None or obj.type != 'MESH' or not obj.select_get(view_layer=layer):
        raise ValueError('请在 Blender 当前场景中激活并选中原网格')
    if obj.library or obj.data.library or obj.override_library or obj.data.users != 1:
        raise ValueError('Blender 原网格必须是可写的本地单用户网格')
    modifiers = [modifier for modifier in obj.modifiers if modifier.type == 'ARMATURE']
    if len(modifiers) != 1 or not modifiers[0].use_vertex_groups:
        raise ValueError('原网格需要一个使用顶点组的骨架修改器')
    rig = modifiers[0].object
    if rig is None or rig.type != 'ARMATURE' or context.scene.objects.get(rig.name) != rig:
        raise ValueError('原网格必须绑定到当前场景中的骨架')
    identity = dict(scene=context.scene.as_pointer(), mesh=obj.as_pointer(), data=obj.data.as_pointer(),
                    rig=rig.as_pointer(), name=obj.name, armature=rig.name)
    return obj, rig, identity


def target_maya():
    return _target()[2]


def _read(path, count, code):
    values = array(code)
    if path.stat().st_size != count * values.itemsize:
        raise ValueError(f'{path.name} 文件尺寸不匹配')
    with path.open('rb') as stream:
        values.fromfile(stream, count)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def _face_key(face):
    start = face.index(min(face))
    rotated = tuple(face[start:]) + tuple(face[:start])
    return min(rotated, rotated[:1] + rotated[:0:-1])


def _verify_geometry(obj, path, source):
    import bpy

    vertices = obj.data.vertices
    if not len(vertices) or len(vertices) != source['vertices']:
        raise ValueError('Maya 与 Blender 的顶点数量不一致')
    points = _read(path / 'mesh-0.points', 3 * len(vertices), 'd')
    units = bpy.context.scene.unit_settings
    scale = units.scale_length if units.system != 'NONE' else 1.0
    buckets = defaultdict(list)
    spacing = 2 * POSITION_TOLERANCE
    for index, vertex in enumerate(vertices):
        target = tuple(value * scale for value in (obj.matrix_world @ vertex.co))
        point = points[3 * index:3 * index + 3]
        if not all(map(isfinite, (*point, *target))) or dist(point, target) > POSITION_TOLERANCE:
            raise ValueError(f'顶点 {index} 的同索引世界坐标不一致')
        # A source point within tolerance of its index cannot match a second
        # target if all targets are separated by more than twice the tolerance.
        cell = tuple(floor(value / spacing) for value in target)
        for offset in NEIGHBORS:
            adjacent = tuple(value + step for value, step in zip(cell, offset))
            if any(dist(target, other) <= spacing for other in buckets.get(adjacent, ())):
                raise ValueError(f'顶点 {index} 与其他顶点重合或过近，无法唯一验证编号')
        buckets[cell].append(target)

    polygons = obj.data.polygons
    face_count, corners = source['faces'], source['corners']
    if face_count != len(polygons) or corners != len(obj.data.loops) or not face_count:
        raise ValueError('Maya 与 Blender 的面拓扑数量不一致')
    topology = _read(path / 'mesh-0.faces', face_count + corners, 'I')
    counts, ids = topology[:face_count], topology[face_count:]
    maya_faces = Counter()
    start = 0
    for count in counts:
        face = ids[start:start + count]
        if count < 3 or len(face) != count or len(set(face)) != count or max(face) >= len(vertices):
            raise ValueError('Maya 面拓扑包含无效顶点编号')
        maya_faces[_face_key(face)] += 1
        start += count
    if start != corners or maya_faces != Counter(_face_key(list(poly.vertices)) for poly in polygons):
        raise ValueError('Maya 与 Blender 的同索引面拓扑不一致')


def _fbx_name(name):
    return ''.join(f'FBXASC{byte:03d}' if byte in (32, 46) or byte >= 128 else chr(byte)
                   for byte in name.encode('utf-8'))


def _match_bones(bones, influences):
    candidates = defaultdict(set)
    for bone in bones:
        candidates[bone.name].add(bone.name)
        candidates[_fbx_name(bone.name)].add(bone.name)
    matched = {}
    for influence in influences:
        short = influence.rsplit('|', 1)[-1]
        names = candidates.get(short, set()) | candidates.get(short.rsplit(':', 1)[-1], set())
        if len(names) != 1:
            detail = '、'.join(sorted(names)) if names else '无'
            raise ValueError(f'Maya influence {influence} 无法唯一匹配绑定骨架中的骨骼（候选：{detail}）')
        name = next(iter(names))
        if name in matched:
            raise ValueError(f'Maya influence {matched[name]} 和 {influence} 均匹配骨骼 {name}')
        matched[name] = influence
    if not matched:
        raise ValueError('Maya 没有可发送的 influence')
    return list(matched)


def _weight_rows(path, vertices, names):
    values = _read(path / 'mesh-0.weights', vertices * len(names), 'd')
    groups = {name: defaultdict(list) for name in names}
    for vertex in range(vertices):
        start = vertex * len(names)
        row = values[start:start + len(names)]
        if not any(row) or any(not isfinite(value) or not 0 <= value <= 1 for value in row):
            raise ValueError(f'顶点 {vertex} 包含全零、非有限或超出 0～1 范围的权重')
        for name, weight in zip(names, row):
            if weight:
                groups[name][weight].append(vertex)
    return groups


def _write_groups(obj, new_weights):
    groups = obj.vertex_groups
    existing = {groups[name].index: name for name in new_weights if name in groups}
    if any(groups[name].lock_weight for name in existing.values()):
        raise ValueError('目标骨骼顶点组已锁定')
    previous = {name: defaultdict(list) for name in existing.values()}
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            if item.group in existing:
                previous[existing[item.group]][item.weight].append(vertex.index)
    active = groups.active_index
    created = []
    touched = []
    all_ids = list(range(len(obj.data.vertices)))
    try:
        for name, weights in new_weights.items():
            group = groups.get(name)
            if group is None:
                group = groups.new(name=name)
                created.append(group)
                if group.name != name:
                    raise RuntimeError(f'无法按骨骼名称创建顶点组 {name}')
            else:
                touched.append(name)
                group.remove(all_ids)
            for weight, ids in weights.items():
                group.add(ids, weight, 'REPLACE')
    except Exception as exc:
        try:
            for group in reversed(created):
                groups.remove(group)
            for name in touched:
                group = groups[name]
                group.remove(all_ids)
                for weight, ids in previous[name].items():
                    group.add(ids, weight, 'REPLACE')
        except Exception as rollback:
            raise RuntimeError(f'写入失败且恢复失败：{exc}；{rollback}') from exc
        raise RuntimeError(f'写入失败，已恢复原权重：{exc}') from exc
    finally:
        if groups:
            groups.active_index = active


def receive_maya(directory, source, target):
    path = Path(directory).resolve()
    if not path.is_dir() or not path.name.startswith('qz-skin-transfer-'):
        raise ValueError('无效的权重发送临时目录')
    obj, rig, identity = _target()
    if identity != target:
        raise ValueError(f'Blender 活动目标已变化（预检目标：{target["name"]}）')
    _verify_geometry(obj, path, source)
    names = _match_bones(rig.data.bones, source['bone_names'])
    weights = _weight_rows(path, source['vertices'], names)
    _write_groups(obj, weights)
    return dict(name=obj.name, armature=rig.name, vertices=source['vertices'], groups=len(names))


ACTIONS = {'skin_weights_target_maya': target_maya, 'skin_weights_receive_maya': receive_maya}
