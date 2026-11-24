"""Collect ordinary skin weights and undeformed, indexed geometry for Blender."""
from pathlib import Path

from maya_actions.skin_weight_geometry import _write
from maya_actions.skin_weights import _Mesh, _selected_meshes, _write_snapshot
from maya_bridge import register_action


@register_action('skin_weights_export_blender')
def export_blender(directory):
    from maya import cmds

    path = Path(directory).resolve()
    if not path.is_dir() or not path.name.startswith('qz-skin-transfer-'):
        raise ValueError('无效的权重发送临时目录')
    shapes, errors = _selected_meshes(cmds)
    if len(shapes) != 1 or errors:
        raise ValueError('请在 Maya 中只选择一张蒙皮网格')
    if cmds.upAxis(query=True, axis=True) != 'y':
        raise ValueError('请使用 Maya Y 向上场景')
    mesh = _Mesh(shapes[0])
    index = mesh.fn.indexForOutputShape(mesh.path.node())
    source = cmds.listConnections(mesh.shape + '.inMesh', source=True, destination=False) or []
    if source != [mesh.skin]:
        raise ValueError('网格必须直接接收 skinCluster 输出，无法验证后续变形器的顶点编号')
    plug = mesh.fn.findPlug('input', False).elementByLogicalIndex(index)
    geometry = mesh.om.MFnMesh(plug.child(mesh.fn.attribute('inputGeometry')).asMObject())
    faces, indices = map(list, geometry.getVertices())
    output_faces, output_indices = map(list, mesh.om.MFnMesh(mesh.path).getVertices())
    if (geometry.numVertices != mesh.vertices or not faces or
            (faces, indices) != (output_faces, output_indices)):
        raise ValueError('蒙皮前后网格的顶点编号或面拓扑不一致')

    # OpenMaya uses centimeters. Convert Maya Y-up to Blender Z-up in meters.
    matrix = mesh.path.inclusiveMatrix()
    points = []
    for point in geometry.getPoints():
        world = point * matrix
        points.extend((world.x / 100, -world.z / 100, world.y / 100))
    _write(path / 'mesh-0.points', points)
    _write(path / 'mesh-0.faces', faces + indices, 'I')

    timings = {}
    with (path / 'mesh-0.weights').open('wb') as stream:
        for start in range(0, mesh.vertices, mesh.batch_size):
            ids = list(range(start, min(mesh.vertices, start + mesh.batch_size)))
            _write_snapshot(stream, mesh.read(ids), timings)
    names = [bone.fullPathName() for bone in mesh.influences]
    return dict(mesh=mesh.shape, vertices=mesh.vertices, faces=len(faces),
                corners=len(indices), bone_names=names)
