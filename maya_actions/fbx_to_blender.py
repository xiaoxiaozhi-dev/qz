"""Maya-side actions for sending FBX and base vertex positions to Blender."""
from math import isfinite
from pathlib import Path

from maya_actions.fbx_to_max import _export_selection
from maya_actions.skin_weight_geometry import _write
from maya_actions.skin_weights import _selected_meshes
from maya_actions.vertex_positions import _source_mesh
from maya_bridge import register_action


@register_action('fbx_export_to_blender')
def export_blender(filepath, animation=False):
    return _export_selection(filepath, animation=animation, include_descendants=True)


@register_action('vertex_positions_export_blender')
def export_vertices(directory):
    from maya import cmds
    import maya.api.OpenMaya as om

    folder = Path(directory).resolve()
    if not folder.is_dir() or not folder.name.startswith('qz-vertex-transfer-'):
        raise ValueError('无效的顶点交换目录')
    shapes, errors = _selected_meshes(cmds)
    if len(shapes) != 1 or errors:
        raise ValueError('请在 Maya 中只选择一张网格')
    if cmds.upAxis(query=True, axis=True) != 'y':
        raise ValueError('请使用 Maya Y 向上场景')
    shape = shapes[0]
    selection = om.MSelectionList()
    selection.add(shape)
    path = selection.getDagPath(0)
    transform_name = path.fullPathName().rsplit('|', 2)[-2]
    source = _source_mesh(shape, path, cmds, om)
    counts, corners = map(list, source.getVertices())
    if not counts or (counts, corners) != tuple(map(list, om.MFnMesh(path).getVertices())):
        raise ValueError('源网格与 Maya 当前网格的编号或面拓扑不一致')
    matrix = path.inclusiveMatrix()
    points = []
    for point in source.getPoints(om.MSpace.kObject):
        world = point * matrix
        points.extend((world.x / 100, -world.z / 100, world.y / 100))
    if not all(map(isfinite, points)):
        raise ValueError('Maya 世界顶点坐标包含非有限数值')
    _write(folder / 'mesh-0.points', points)
    _write(folder / 'mesh-0.faces', counts + corners, 'I')
    edges = [vertex for index in range(source.numEdges) for vertex in source.getEdgeVertices(index)]
    _write(folder / 'mesh-0.edges', edges, 'I')
    return dict(mesh=shape, name=transform_name, vertices=source.numVertices, faces=len(counts), corners=len(corners),
                edges=source.numEdges)
