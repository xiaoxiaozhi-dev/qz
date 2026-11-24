"""Run with run_maya_native.py and QZ_TEST_BLENDER pointing to Blender."""
from copy import deepcopy
import json
from math import isclose
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BONES = ('Space Bone', 'Dot.Bone', '中文骨', '左手.指节 01')
POINTS = [(0, 0, 0), (1, 0, .4), (2, 1, .2), (1, 2, .6), (0, 1, .3)]


def _wait(predicate, process=None):
    deadline = time.monotonic() + 20
    while not predicate():
        if time.monotonic() >= deadline or (process is not None and process.poll() is not None):
            raise RuntimeError('Blender fixture stopped or timed out; see blender.log')
        time.sleep(.02)


def _make_scene():
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature = bpy.data.armatures.new('OriginalArmature')
    rig = bpy.data.objects.new('OriginalRig', armature)
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
    data = bpy.data.meshes.new('OriginalMesh')
    data.from_pydata(POINTS, [], [(0, 1, 2), (0, 2, 3, 4)])
    mesh = bpy.data.objects.new('OriginalMesh', data)
    bpy.context.collection.objects.link(mesh)
    mesh.location = (2, -3, .5)
    mesh.rotation_euler.z = .3
    modifier = mesh.modifiers.new('Skin', 'ARMATURE')
    modifier.object = rig
    for index, name in enumerate(BONES):
        mesh.vertex_groups.new(name=name).add([index] if index else [0, 4], 1, 'REPLACE')
    mesh.vertex_groups.new(name='Unrelated').add([0, 4], .25, 'REPLACE')
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.context.view_layer.update()
    return mesh, rig


def _weights(mesh):
    return [{mesh.vertex_groups[item.group].name: item.weight for item in vertex.groups}
            for vertex in mesh.data.vertices]


def blender_stage(folder):
    import struct

    import bpy
    from blender_actions.fbx_maya import export_maya
    from blender_actions.skin_transfer_maya import receive_maya
    from blender_bridge import server

    mesh, rig = _make_scene()
    export_maya(str(folder / 'from-blender.fbx'))
    # Exercise both creation of a missing mapped group and clearing stale weights.
    mesh.vertex_groups.remove(mesh.vertex_groups[BONES[2]])
    initial = _weights(mesh)
    checks = []
    server.reload_actions()

    def receive(directory, source, target):
        original = _weights(mesh)

        def reject(label, message, changed_source=source):
            try:
                receive_maya(directory, changed_source, target)
            except ValueError as exc:
                assert message in str(exc), (label, str(exc))
            else:
                raise AssertionError(label + ' should have been rejected')
            assert _weights(mesh) == original, label
            checks.append(label)

        previous = mesh.data.vertices[1].co.copy()
        mesh.data.vertices[1].co.x += .1
        reject('same count, different coordinates', '世界坐标不一致')
        mesh.data.vertices[1].co = previous

        # Preserve the corner count while changing the indexed topology in the payload.
        topology = Path(directory) / 'mesh-0.faces'
        original_topology = topology.read_bytes()
        try:
            changed_topology = bytearray(original_topology)
            struct.pack_into('<I', changed_topology, 4 * (source['faces'] + 2), 4)
            topology.write_bytes(changed_topology)
            reject('same counts, different faces', '同索引面拓扑不一致')
        finally:
            topology.write_bytes(original_topology)

        points = Path(directory) / 'mesh-0.points'
        original_points = points.read_bytes()
        try:
            mesh.data.vertices[1].co = mesh.data.vertices[0].co
            points.write_bytes(original_points[:24] + original_points[:24] + original_points[48:])
            reject('coincident vertices', '重合或过近')
        finally:
            points.write_bytes(original_points)
            mesh.data.vertices[1].co = previous

        changed = deepcopy(source)
        changed['bone_names'][0] = '|Missing'
        reject('missing bone', '候选：无', changed)
        changed['bone_names'][0] = changed['bone_names'][1]
        reject('two influences for one bone', '均匹配骨骼', changed)

        bpy.context.view_layer.objects.active = rig
        reject('target selection changed', '激活并选中原网格')
        bpy.context.view_layer.objects.active = mesh
        mesh.vertex_groups[BONES[0]].lock_weight = True
        reject('locked group', '已锁定')
        mesh.vertex_groups[BONES[0]].lock_weight = False

        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bone = rig.data.edit_bones.new('SpaceFBXASC032Bone')
        bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.select_set(False)
        bpy.context.view_layer.objects.active = mesh
        reject('FBX escape collision', '候选：Space Bone、SpaceFBXASC032Bone')
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones.remove(rig.data.edit_bones['SpaceFBXASC032Bone'])
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.select_set(False)
        bpy.context.view_layer.objects.active = mesh

        result = receive_maya(directory, source, target)
        for index, row in enumerate(_weights(mesh)):
            expected = {BONES[index % len(BONES)]: .25, BONES[(index + 1) % len(BONES)]: .75}
            if 'Unrelated' in initial[index]:
                expected['Unrelated'] = initial[index]['Unrelated']
            assert expected.keys() == row.keys(), (index, row, expected)
            assert all(isclose(row[name], value, abs_tol=1e-6) for name, value in expected.items())
        assert set(obj.name for obj in bpy.context.scene.objects) == {mesh.name, rig.name}
        # Hold the same live TCP request beyond the ordinary action deadline.
        time.sleep(server.MAIN_THREAD_TIMEOUT + .1)
        return result

    server.ACTIONS['skin_weights_receive_maya'] = receive
    with server._Bridge(0, bpy.app.version_string) as bridge:
        worker = threading.Thread(target=bridge.serve_forever, args=(.01,), daemon=True)
        worker.start()
        try:
            bridge.tick()
            port = folder / 'port.tmp'
            port.write_text(str(bridge.server_address[1]))
            port.replace(folder / 'port')
            _wait(lambda: bridge.tick() and (folder / 'stop').exists())
        finally:
            bridge.shutdown()
            worker.join(2)
    (folder / 'checks.json').write_text(json.dumps(checks), encoding='utf-8')


def run():
    from maya import cmds
    from dcc_bridge import DccClient
    from maya_actions.fbx_blender import receive_blender
    from maya_bridge import ACTIONS
    from ui.maya_blender_skin_transfer import run_blender_transfer

    folder = Path(os.environ['QZ_NATIVE_RESULT']).parent
    executable = Path(os.environ['QZ_TEST_BLENDER']).resolve(strict=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('MAYA', 'BLENDER'))}
    env['BLENDER_USER_RESOURCES'] = str(folder / 'blender-profile')
    with (folder / 'blender.log').open('wb') as log:
        process = subprocess.Popen([str(executable), '--background', '--factory-startup', '--python-exit-code',
                                    '1', '--python', __file__, '--', str(folder)], env=env,
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            _wait(lambda: (folder / 'port').exists(), process)
            cmds.file(new=True, force=True)
            receive_blender(str(folder / 'from-blender.fbx'))
            shape, = cmds.ls(type='mesh', long=True, noIntermediate=True)
            skin, = cmds.ls(cmds.listHistory(shape), type='skinCluster')
            names = [cmds.skinPercent(skin, f'{shape}.vtx[{i}]', query=True, transform=None,
                                      ignoreBelow=.99)[0] for i in range(len(BONES))]
            for index in range(len(POINTS)):
                weights = [(names[index % len(names)], .25), (names[(index + 1) % len(names)], .75)]
                cmds.skinPercent(skin, f'{shape}.vtx[{index}]', transformValue=weights, zeroRemainingInfluences=True)
            cmds.select(shape, replace=True)

            def maya_call(action, timeout, connect_timeout, **params):
                return dict(ok=True, data=ACTIONS[action](**params))

            blender = DccClient.for_dcc('blender', port=int((folder / 'port').read_text()))
            blender.connected = True
            result = run_blender_transfer(SimpleNamespace(connected=True, call=maya_call), blender)
            assert result['ok'], result
        finally:
            (folder / 'stop').touch()
            if process.wait(timeout=10):
                raise RuntimeError('Blender fixture failed; see blender.log')
    checks = json.loads((folder / 'checks.json').read_text(encoding='utf-8'))
    assert len(checks) == 8, checks
    return dict(ok=True, result=result, unchanged_on_failure=checks)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    blender_stage(Path(sys.argv[sys.argv.index('--') + 1]))
