"""Read skin-input geometry with model scale set to one in memory only."""
import math
from pathlib import Path
import struct

from maya_bridge import register_action
from maya_actions.skin_weights import _selected_meshes

VERSION = 2
POSITIONS = 'skin_input_or_evaluated'
SCALE_POLICY = 'local_scale_one'


@register_action('vertex_positions_maya_preflight')
def preflight():
    return dict(version=VERSION, space='world', positions=POSITIONS,
                scale_policy=SCALE_POLICY, path=str(Path(__file__).resolve()))


def _source_mesh(shape, path, cmds, om):
    skins = list(dict.fromkeys(node for node in cmds.listHistory(shape, pruneDagObjects=True) or []
                               if cmds.nodeType(node) == 'skinCluster'))
    if len(skins) > 1:
        raise ValueError('多层 skinCluster 无法唯一确定绑定源网格，已跳过')
    output = om.MFnMesh(path)
    if not skins:
        return output
    import maya.api.OpenMayaAnim as oma
    selection = om.MSelectionList()
    selection.add(skins[0])
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    index = skin.indexForOutputShape(path.node())
    plug = skin.findPlug('input', False).elementByLogicalIndex(index)
    mesh = om.MFnMesh(plug.child(skin.attribute('inputGeometry')).asMObject())
    if mesh.numVertices != output.numVertices:
        raise ValueError('蒙皮输入与输出顶点数量不同，已跳过')
    return mesh


def _scale_one_world(path, om):
    transform = om.MFnTransform(path)
    scale = transform.scale()
    pivot = transform.scalePivot(om.MSpace.kTransform)
    if not all(math.isfinite(value) and value != 0 for value in scale):
        raise ValueError('模型自身缩放为零或无效，无法计算缩放为 1 的顶点位置')
    x, y, z = [1. / value for value in scale]
    # Maya local matrix starts with SP^-1 * S * SH * SP ...
    # Prepending SP^-1 * S^-1 * SP cancels only S, including signed scales.
    # Keep the actual world matrix so rotation, pivots, shear, parent/OPM and
    # inheritsTransform retain their original contribution. No scene setters.
    correction = om.MMatrix(((x, 0., 0., 0.), (0., y, 0., 0.), (0., 0., z, 0.),
                            (pivot.x * (1. - x), pivot.y * (1. - y), pivot.z * (1. - z), 1.)))
    return correction * path.inclusiveMatrix()


@register_action('vertex_positions_export')
def export_positions(directory):
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    directory = Path(directory).resolve()
    if not directory.is_dir() or not directory.name.startswith('qz-vertex-transfer-'):
        raise ValueError('无效的顶点位置交换目录')
    shapes, errors = _selected_meshes(cmds)
    meshes = []
    for index, shape in enumerate(shapes):
        try:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if len(parents) != 1:
                raise ValueError('无法确定模型所属transform')
            selection = om.MSelectionList()
            selection.add(shape)
            path = selection.getDagPath(0)
            mesh = _source_mesh(shape, path, cmds, om)
            points = mesh.getPoints(om.MSpace.kObject)
            if not len(points) or len(points) != mesh.numVertices:
                raise ValueError('网格顶点数量无效')
            selection = om.MSelectionList()
            selection.add(parents[0])
            matrix = _scale_one_world(selection.getDagPath(0), om)
            points = [point * matrix for point in points]
            values = [value for point in points for value in (point.x, point.y, point.z)]
            if not all(math.isfinite(value) for value in values):
                raise ValueError('世界顶点位置包含非有限数')
            with (directory / f'mesh-{index}.points').open('wb') as stream:
                for start in range(0, len(values), 98304):
                    batch = values[start:start + 98304]
                    stream.write(struct.pack(f'<{len(batch)}d', *batch))
            meshes.append(dict(index=index, name=parents[0].rsplit('|', 1)[-1],
                               mesh=shape, vertices=len(points)))
        except Exception as exc:
            errors.append(dict(mesh=shape, error=str(exc), status='skipped'))
    return dict(meshes=meshes, errors=errors, space='world', version=VERSION,
                positions=POSITIONS, scale_policy=SCALE_POLICY)
