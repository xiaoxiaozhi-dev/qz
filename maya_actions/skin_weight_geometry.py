"""仅文件导出/最近点导入使用的蒙皮前几何；无 NumPy，不创建场景节点。"""
import math
from pathlib import Path

from maya_actions.skin_weights import _Mesh, _read, _bytes, BLOCK_VALUES


def input_mesh(shape):
    from maya import cmds
    import maya.api.OpenMaya as om
    skins = list(dict.fromkeys(node for node in cmds.listHistory(shape, pruneDagObjects=True) or []
                               if cmds.nodeType(node) == "skinCluster"))
    if len(skins) > 1:
        raise ValueError("多层 skinCluster 暂不支持最近表面点匹配")
    if skins:
        mesh = _Mesh(shape)
        index = mesh.fn.indexForOutputShape(mesh.path.node())
        plug = mesh.fn.findPlug("input", False).elementByLogicalIndex(index)
        return om.MFnMesh(plug.child(mesh.fn.attribute("inputGeometry")).asMObject())
    selection = om.MSelectionList()
    selection.add(shape)
    return om.MFnMesh(selection.getDagPath(0))


def _write(path, values, code="d"):
    with path.open("wb") as file:
        for start in range(0, len(values), BLOCK_VALUES):
            file.write(_bytes(values[start:start + BLOCK_VALUES], code))


def export_geometry(directory, entries):
    ready, errors = [], []
    for entry in entries:
        try:
            fn = input_mesh(entry["mesh"])
            if fn.numVertices != entry["vertices"]:
                raise ValueError("蒙皮前后顶点数不同，无法保存最近点几何")
            points = [value for point in fn.getPoints() for value in (point.x, point.y, point.z)]
            if not all(math.isfinite(value) for value in points):
                raise ValueError("几何坐标包含非有限数")
            triangles = list(fn.getTriangles()[1])
            if not triangles or len(triangles) % 3:
                raise ValueError("模型没有可用的三角形表面")
            prefix = Path(directory) / f'mesh-{entry["index"]}'
            _write(prefix.with_suffix(".points"), points)
            _write(prefix.with_suffix(".triangles"), triangles, "I")
            ready.append(dict(entry, geometry=dict(triangles=len(triangles) // 3,
                                                   space="object", positions="skin_input")))
        except Exception as exc:
            errors.append(dict(mesh=entry["mesh"], error=str(exc), status="skipped"))
    return ready, errors


def position_key(point, decimals):
    return tuple(round(float(value), decimals) for value in point)


def unique_positions(points, decimals):
    lookup = {}
    for index, point in enumerate(points):
        key = position_key(point, decimals)
        lookup[key] = None if key in lookup else index
    return lookup


def map_geometry(directory, entries, position_decimals):
    import maya.api.OpenMaya as om
    if type(position_decimals) is not int or not 0 <= position_decimals <= 12:
        raise ValueError("坐标匹配小数位必须在 0～12 之间")
    directory = Path(directory)
    reports, errors = [], []
    for entry in entries:
        try:
            prefix = directory / f'mesh-{entry["index"]}'
            with prefix.with_suffix(".points").open("rb") as file:
                flat = _read(file, entry["vertices"] * 3)
            points = [tuple(flat[index:index + 3]) for index in range(0, len(flat), 3)]
            with prefix.with_suffix(".triangles").open("rb") as file:
                triangles = _read(file, entry["geometry"]["triangles"] * 3, "I")
            target_fn = input_mesh(entry["mesh"])
            if target_fn.numVertices != entry["target_vertices"]:
                raise ValueError("目标顶点数与开始导入时不一致")
            target = target_fn.getPoints()
            lookup = unique_positions(points, position_decimals)
            ids, factors, direct, interpolated = [], [], 0, []
            intersector = None
            # 保留 mesh_data/source_object 的生命期，查询对象仅存在于内存。
            mesh_data = source_object = None
            for vertex, point in enumerate(target):
                xyz = (point.x, point.y, point.z)
                if not all(math.isfinite(value) for value in xyz):
                    raise ValueError("目标几何坐标包含非有限数")
                match = lookup.get(position_key(xyz, position_decimals))
                if match is not None:
                    ids.extend((match, match, match))
                    factors.extend((1., 0., 0.))
                    direct += 1
                    continue
                if intersector is None:
                    mesh_data = om.MFnMeshData().create()
                    source_object = om.MFnMesh().create([om.MPoint(*p) for p in points],
                                                        [3] * (len(triangles) // 3),
                                                        list(triangles), parent=mesh_data)
                    intersector = om.MMeshIntersector()
                    intersector.create(source_object, om.MMatrix())
                closest = intersector.getClosestPoint(om.MPoint(*xyz))
                if closest is None:
                    raise ValueError("无法找到源模型最近表面点")
                face = int(closest.face)
                if not 0 <= face < len(triangles) // 3:
                    raise ValueError("Maya 返回的三角形编号无效")
                u, v = closest.barycentricCoords
                bary = [max(0., min(1., float(value))) for value in (u, v, 1. - u - v)]
                total = sum(bary)
                if not math.isfinite(total) or total <= 0:
                    raise ValueError("Maya 返回的插值比例无效")
                interpolated.append(vertex)
                ids.extend(triangles[face * 3:face * 3 + 3])
                factors.extend(value / total for value in bary)
            _write(prefix.with_suffix(".match_ids"), ids, "I")
            _write(prefix.with_suffix(".match_factors"), factors)
            _write(prefix.with_suffix(".interpolated_ids"), interpolated, "I")
            reports.append(dict(index=entry["index"], direct_vertices=direct,
                                interpolated_vertices=entry["target_vertices"] - direct))
        except Exception as exc:
            errors.append(dict(mesh=entry["mesh"], error=str(exc), status="skipped"))
    return dict(reports=reports, errors=errors)
