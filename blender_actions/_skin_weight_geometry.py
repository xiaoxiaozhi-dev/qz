"""Blender skin-input geometry and the existing qz.skin_weights correspondence."""
from array import array
from hashlib import sha256
from math import isclose, isfinite
import sys


def write_array(path, values, code="d"):
    with path.open("wb") as stream:
        write_values(stream, values, code)


def write_values(stream, values, code="d"):
    values = array(code, values)
    if sys.byteorder != "little":
        values.byteswap()
    values.tofile(stream)


def read_array(path, count, code="d"):
    values = array(code)
    if count < 0 or path.stat().st_size != count * values.itemsize:
        raise ValueError(f"{path.name} 数据长度不匹配")
    with path.open("rb") as stream:
        values.fromfile(stream, count)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def geometry(obj, skin):
    """Read the base mesh when it is also the input of the armature modifier."""
    for modifier in obj.modifiers:
        if modifier == skin:
            break
        if modifier.show_viewport:
            raise ValueError("骨架修改器前有启用的修改器，无法用基础网格验证蒙皮输入")
    data = obj.data
    keys = data.shape_keys
    if keys and (not keys.use_relative or any(key.value != 0.0 and not key.mute
                                             for key in keys.key_blocks[1:])):
        raise ValueError("当前形态键改变了蒙皮输入，请使用基础形状")
    data.calc_loop_triangles()
    points = [tuple(vertex.co) for vertex in data.vertices]
    triangles = [tuple(triangle.vertices) for triangle in data.loop_triangles]
    if not points or not triangles or not all(isfinite(value) for p in points for value in p):
        raise ValueError("网格没有有效的蒙皮输入几何")
    return points, triangles


def save_geometry(prefix, mesh):
    points, triangles = mesh
    write_array(prefix.with_suffix(".points"), (n for point in points for n in point))
    write_array(prefix.with_suffix(".triangles"), (n for face in triangles for n in face), "I")
    return dict(triangles=len(triangles), space="object", positions="skin_input")


def read_geometry(prefix, entry):
    points = read_array(prefix.with_suffix(".points"), entry["vertices"] * 3)
    dimensions = entry["geometry"]
    if (dimensions["space"], dimensions["positions"]) != ("object", "skin_input"):
        raise ValueError("权重文件的坐标基准无效")
    indices = read_array(prefix.with_suffix(".triangles"), dimensions["triangles"] * 3, "I")
    if not points or not indices or not all(map(isfinite, points)) or max(indices) >= entry["vertices"]:
        raise ValueError("权重文件的坐标或三角形编号无效")
    return list(zip(points[::3], points[1::3], points[2::3])), list(zip(indices[::3], indices[1::3], indices[2::3]))


def signature(mesh):
    points, triangles = mesh
    stamp = sha256()
    for code, rows in (("d", points), ("I", triangles)):
        data = array(code, (value for row in rows for value in row))
        if sys.byteorder != "little":
            data.byteswap()
        stamp.update(data)
    return stamp.hexdigest()


def _key(point, decimals):
    return tuple(round(float(value), decimals) for value in point)


def verify_indices(source, target, decimals):
    source_points, source_faces = source
    target_points, target_faces = target
    source_keys = [_key(point, decimals) for point in source_points]
    if source_keys != [_key(point, decimals) for point in target_points]:
        raise ValueError("同索引顶点的局部坐标不一致，请核对几何、轴向和单位")
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("存在重合或无法区分的顶点，无法唯一验证顶点编号")
    source_faces = sorted(tuple(sorted(face)) for face in source_faces)
    target_faces = sorted(tuple(sorted(face)) for face in target_faces)
    if source_faces != target_faces:
        raise ValueError("同索引三角面拓扑不一致，无法验证顶点对应")


def _barycentric(point, vertices):
    a, b, c = vertices
    first = tuple(v - u for u, v in zip(a, b))
    second = tuple(v - u for u, v in zip(a, c))
    relative = tuple(v - u for u, v in zip(a, point))

    def dot(x, y):
        return sum(u * v for u, v in zip(x, y))

    uu, uv, vv = dot(first, first), dot(first, second), dot(second, second)
    denominator = uu * vv - uv * uv
    if denominator <= 1e-14 * uu * vv:
        raise ValueError("最近三角面退化，无法确认插值比例")
    a_dot, b_dot = dot(relative, first), dot(relative, second)
    weight_b = (vv * a_dot - uv * b_dot) / denominator
    weight_c = (uu * b_dot - uv * a_dot) / denominator
    weights = [max(0., min(1., v)) for v in (1. - weight_b - weight_c, weight_b, weight_c)]
    total = sum(weights)
    return tuple(value / total for value in weights)


def _nearest(tree, point, points, triangles):
    location, _, face, distance = tree.find_nearest(point)
    if location is None:
        raise ValueError("无法找到源模型最近表面点")
    corners = triangles[face]
    factors = _barycentric(location, [points[index] for index in corners])
    reference = dict(zip(corners, factors))
    tolerance = max(1e-6, distance * 1e-6)
    for other, _, index, other_distance in tree.find_nearest_range(point, distance + tolerance):
        if index == face or other_distance > distance + tolerance:
            continue
        ids = triangles[index]
        alternative = dict(zip(ids, _barycentric(other, [points[key] for key in ids])))
        if any(not isclose(reference.get(key, 0.), alternative.get(key, 0.), abs_tol=1e-6)
               for key in reference.keys() | alternative.keys()):
            raise ValueError("存在多个等距表面，无法唯一确认插值对应")
    return corners, factors


def map_surface(prefix, source, target, decimals):
    from mathutils.bvhtree import BVHTree

    points, triangles = source
    lookup = {}
    for index, point in enumerate(points):
        key = _key(point, decimals)
        lookup[key] = None if key in lookup else index
    ids, factors, interpolated = [], [], []
    tree = None
    for index, point in enumerate(target[0]):
        key = _key(point, decimals)
        if key in lookup:
            match = lookup[key]
            if match is None:
                raise ValueError(f"目标顶点 {index} 匹配到多个重合的源顶点")
            ids.extend((match, match, match))
            factors.extend((1., 0., 0.))
            continue
        if tree is None:
            tree = BVHTree.FromPolygons(points, triangles, all_triangles=True)
        corners, barycentric = _nearest(tree, point, points, triangles)
        interpolated.append(index)
        ids.extend(corners)
        factors.extend(barycentric)
    write_array(prefix.with_suffix(".match_ids"), ids, "I")
    write_array(prefix.with_suffix(".match_factors"), factors)
    write_array(prefix.with_suffix(".interpolated_ids"), interpolated, "I")
    return dict(direct_vertices=len(target[0]) - len(interpolated), interpolated_vertices=len(interpolated))
