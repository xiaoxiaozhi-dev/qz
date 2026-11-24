"""Run with tests/run_maya_native.py and QZ_TEST_BLENDER set to Blender's exe."""
import json
from math import dist, isclose
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BONES = ['Space Bone', 'Dot.Bone', '中文骨', '左手.指节 01', 'SpaceFBXASC032Bone']
POINTS = [(0, 0, 0), (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0)]


def assert_vertex_order(points):
    assert len(points) == len(POINTS)
    scale = dist(points[0], points[1]) / dist(POINTS[0], POINTS[1])
    assert scale > 0
    for i, point in enumerate(points):
        for j in range(i):
            assert isclose(dist(point, points[j]) / scale, dist(POINTS[i], POINTS[j]),
                           rel_tol=1e-5), (i, j, points)


def blender_stage(stage, folder):
    import bpy
    from blender_actions.fbx_maya import export_maya, receive_maya

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if stage == 'export':
        armature = bpy.data.armatures.new('QzArmature')
        rig = bpy.data.objects.new('QzRig', armature)
        bpy.context.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for index, name in enumerate(BONES):
            bone = armature.edit_bones.new(name)
            bone.head = (index, 0, 0)
            bone.tail = (index, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.select_set(False)
        geometry = bpy.data.meshes.new('QzMesh')
        geometry.from_pydata(POINTS, [], [(0, 1, 2, 3, 4)])
        mesh = bpy.data.objects.new('QzMesh', geometry)
        bpy.context.collection.objects.link(mesh)
        modifier = mesh.modifiers.new('Skin', 'ARMATURE')
        modifier.object = rig
        for index, name in enumerate(BONES):
            mesh.vertex_groups.new(name=name).add([index], 1, 'REPLACE')
        mesh.select_set(True)
        bpy.context.view_layer.objects.active = mesh
        assert export_maya(str(folder / 'from-blender.fbx'))['count'] == 1
        assert bpy.context.selected_objects == [mesh]
    else:
        result = receive_maya(str(folder / 'from-maya.fbx'))
        assert result['objects'] >= 2, result
        mesh, = [obj for obj in bpy.data.objects if obj.type == 'MESH']
        assert mesh.find_armature() is not None
        vertices = mesh.data.vertices
        assert_vertex_order([mesh.matrix_world @ vertex.co for vertex in vertices])
        names = []
        for vertex in vertices:
            group, = vertex.groups
            assert abs(group.weight - 1) < 1e-5
            names.append(mesh.vertex_groups[group.group].name.rsplit(':', 1)[-1])
        (folder / 'roundtrip.json').write_text(json.dumps(names, ensure_ascii=False), encoding='utf-8')
        assert len(set(names)) == len(BONES), names
        assert names[:4] == BONES[:4], names


def run():
    from maya import cmds
    from maya_actions.fbx_blender import export_blender, receive_blender

    folder = Path(os.environ['QZ_NATIVE_RESULT']).parent
    executable = Path(os.environ['QZ_TEST_BLENDER']).resolve(strict=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('MAYA', 'BLENDER'))}
    env['BLENDER_USER_RESOURCES'] = str(folder / 'blender-profile')
    _run_blender(executable, 'export', folder, env)
    cmds.file(new=True, force=True)
    existing = cmds.createNode('transform', name='ExistingSceneObject')
    result = receive_blender(str(folder / 'from-blender.fbx'))
    assert cmds.objExists(existing)
    mesh, = cmds.ls(type='mesh', long=True, noIntermediate=True)
    target, = cmds.listRelatives(mesh, parent=True, fullPath=True)
    skin, = cmds.ls(cmds.listHistory(mesh), type='skinCluster')
    xyz = cmds.xform(target + '.vtx[*]', query=True, worldSpace=True, translation=True)
    assert_vertex_order(list(zip(xyz[::3], xyz[1::3], xyz[2::3])))
    mapping = {}
    for index, original in enumerate(BONES):
        vertex = f'{target}.vtx[{index}]'
        names = cmds.skinPercent(skin, vertex, query=True, transform=None, ignoreBelow=0.99)
        assert len(names) == 1, (vertex, names)
        mapping[original] = names[0].rsplit('|', 1)[-1].rsplit(':', 1)[-1]
    assert len(set(mapping.values())) == len(BONES), mapping
    cmds.select(target, replace=True)
    assert export_blender(str(folder / 'from-maya.fbx'))['skin_influence_count'] == len(BONES)
    assert cmds.ls(sl=True, long=True) == [target]
    _run_blender(executable, 'import', folder, env)
    roundtrip = json.loads((folder / 'roundtrip.json').read_text(encoding='utf-8'))
    return dict(ok=True, mapping=mapping, roundtrip=dict(zip(BONES, roundtrip)))


def _run_blender(executable, stage, folder, env):
    with (folder / f'blender-{stage}.log').open('wb') as log:
        subprocess.run([str(executable), '--background', '--factory-startup',
                        '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
                        '--', stage, str(folder)], env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=20)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    stage, folder = sys.argv[sys.argv.index('--') + 1:]
    blender_stage(stage, Path(folder))
