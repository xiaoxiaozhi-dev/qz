"""Use run_maya_native.py --gui with QZ_TEST_BLENDER set to the Blender 5.2 executable."""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from maya_blender_fbx_integration import BONES, POINTS, assert_vertex_order


def start_blender():
    import bpy
    from blender_bridge import server

    assert bpy.app.version[:2] == (5, 2), bpy.app.version_string
    bpy.context.preferences.view.show_splash = False

    def clear():
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        return dict(objects=len(bpy.data.objects))

    def selection():
        return dict(names=[obj.name for obj in bpy.context.selected_objects],
                    start=bpy.context.scene.frame_start, end=bpy.context.scene.frame_end)

    def roundtrip():
        mesh, = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
        assert mesh.find_armature() is not None
        vertices = mesh.data.vertices
        assert_vertex_order([mesh.matrix_world @ vertex.co for vertex in vertices])
        names = []
        for vertex in vertices:
            group, = vertex.groups
            assert math.isclose(group.weight, 1, abs_tol=1e-5), vertex.index
            names.append(mesh.vertex_groups[group.group].name.rsplit(':', 1)[-1])
        assert names[:4] == BONES[:4] and len(set(names)) == len(BONES), names
        return dict(names=names)

    def finish():
        def quit_blender():
            bpy.ops.wm.quit_blender()
            return None

        bpy.app.timers.register(quit_blender, first_interval=1)
        return dict(scheduled=True)

    def initialize():
        clear()
        scene = bpy.context.scene
        scene.render.fps = 30
        scene.frame_start = 1
        scene.frame_end = 12
        armature = bpy.data.armatures.new('QzArmature')
        rig = bpy.data.objects.new('QzRig', armature)
        scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for index, name in enumerate(BONES):
            bone = armature.edit_bones.new(name)
            bone.head = (index, 0, 0)
            bone.tail = (index, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        animated = rig.pose.bones[BONES[0]]
        animated.location.x = 0
        animated.keyframe_insert(data_path='location', frame=1)
        animated.location.x = 1
        animated.keyframe_insert(data_path='location', frame=12)
        scene.frame_set(1)

        geometry = bpy.data.meshes.new('QzMesh')
        geometry.from_pydata(POINTS, [], [(0, 1, 2, 3, 4)])
        mesh = bpy.data.objects.new('QzMesh', geometry)
        scene.collection.objects.link(mesh)
        mesh.modifiers.new('Skin', 'ARMATURE').object = rig
        for index, name in enumerate(BONES):
            mesh.vertex_groups.new(name=name).add([index], 1, 'REPLACE')
        rig.select_set(False)
        mesh.select_set(True)
        bpy.context.view_layer.objects.active = mesh
        server.run(port=int(os.environ['QZ_BLENDER_SEND_TEST_PORT']))
        server.ACTIONS.update(test_fbx_selection=selection, test_fbx_clear=clear,
                              test_fbx_roundtrip=roundtrip, test_fbx_finish=finish)
        return None

    bpy.app.timers.register(initialize)


@contextmanager
def blender_process(folder):
    executable = Path(os.environ['QZ_TEST_BLENDER']).resolve(strict=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    scratch = folder / 'blender-temp'
    scratch.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('MAYA', 'BLENDER_USER_', 'PYTHON'))}
    environment.update(BLENDER_USER_RESOURCES=str(folder / 'blender-profile'),
                       TMP=str(scratch), TEMP=str(scratch), PYTHONDONTWRITEBYTECODE='1',
                       PYTHONUNBUFFERED='1', QZ_BLENDER_SEND_TEST_PORT=str(port))
    command = [str(executable), '--factory-startup', '--offline-mode', '--no-window-focus',
               '--python-exit-code', '1', '--python', __file__]
    with (folder / 'blender.log').open('wb') as output:
        process = subprocess.Popen(command, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            yield port, process
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def run():
    from maya import cmds, utils
    from maya_bridge import server
    from dcc_bridge import DccClient
    import paths

    assert not cmds.about(batch=True), 'Run with --gui to test Maya model editor flags'
    folder = Path(os.environ['QZ_NATIVE_RESULT']).parent
    paths.local_data_dir = lambda *parts: str(folder.joinpath(*parts))
    run_transfer = runpy.run_path(str(ROOT / 'plugins/blender/fbx_to_maya/transfer.py'))['run_transfer']

    def in_worker(operation, *args, **kwargs):
        result = {}

        def worker():
            try:
                result['value'] = operation(*args, **kwargs)
            except BaseException as exc:
                result['error'] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        deadline = time.monotonic() + 12
        while thread.is_alive() and time.monotonic() < deadline:
            utils.processIdleEvents()
            time.sleep(.005)
        thread.join(.1)
        assert not thread.is_alive(), 'Bridge request did not finish'
        if 'error' in result:
            raise result['error']
        return result['value']

    def call(client, action, **params):
        response = in_worker(client.call, action, timeout=10, connect_timeout=1, **params)
        assert response['ok'], (action, response)
        return response['data']

    maya_state = {}
    with blender_process(folder) as (port, process):
        blender = DccClient.for_dcc('blender', port=port)
        deadline = time.monotonic() + 15
        while blender.probe()['state'] != 'ready':
            assert process.poll() is None and time.monotonic() < deadline, 'Blender did not become ready'
            time.sleep(.05)
        blender_version = blender.last_ping_data['version']
        server.run(port=0)
        maya = DccClient.for_dcc('maya', port=server._server_sock.getsockname()[1])
        try:
            for client in (maya, blender):
                assert call(client, 'bridge_connect', client_id='blender-to-maya-native')['connected']
                client.connected = True
            initial = call(blender, 'test_fbx_selection')
            assert initial == dict(names=['QzMesh'], start=1, end=12), initial

            def send(mode, animation, display, confirm=False):
                result = in_worker(run_transfer, blender, maya, mode, animation, display, confirm)
                assert result['ok'], result
                assert call(blender, 'test_fbx_selection') == initial
                if not result['data'].get('cancelled'):
                    assert result['selected_count'] == 1 and 'retained_path' not in result, result
                return result

            cmds.file(new=True, force=True)
            cmds.createNode('transform', name='SavedMarker')
            cmds.select(clear=True)
            old_joint = cmds.joint(name='ExistingJoint', radius=2.4)
            original = folder / 'original.ma'
            cmds.file(rename=str(original))
            cmds.file(save=True, type='mayaAscii')
            original_bytes = original.read_bytes()
            cmds.createNode('transform', name='UnsavedMarker')
            assert cmds.file(query=True, anyModified=True)
            blocked = send('open', False, False)
            assert blocked['data']['cancelled'], blocked
            assert cmds.objExists('SavedMarker') and cmds.objExists('UnsavedMarker')
            assert Path(cmds.file(query=True, sceneName=True)) == original

            panels = cmds.getPanel(type='modelPanel') or []
            panel = next(item for item in [cmds.getPanel(withFocus=True), 'modelPanel4'] +
                         (cmds.getPanel(visiblePanels=True) or []) + panels if item in panels)

            def set_display_baseline():
                cmds.autoKeyframe(state=True)
                cmds.jointDisplayScale(0.8)
                cmds.setAttr('hardwareRenderingGlobals.multiSampleEnable', False)
                cmds.modelEditor(panel, edit=True, jointXray=False, useDefaultMaterial=False,
                                 wireframeOnShaded=False)

            def check_display(optimized):
                assert cmds.modelEditor(panel, query=True, jointXray=True) is optimized
                assert cmds.modelEditor(panel, query=True, useDefaultMaterial=True) is optimized
                assert cmds.modelEditor(panel, query=True, wireframeOnShaded=True) is optimized
                assert bool(cmds.getAttr('hardwareRenderingGlobals.multiSampleEnable')) is optimized
                assert cmds.autoKeyframe(query=True, state=True) is not optimized
                assert math.isclose(cmds.jointDisplayScale(query=True), 0.3 if optimized else 0.8,
                                    abs_tol=1e-5)

            set_display_baseline()
            imported = send('import', False, False)['data']
            first_ns = imported['namespace']
            assert first_ns.startswith('blender_') and cmds.objExists('UnsavedMarker')
            assert Path(cmds.file(query=True, sceneName=True)) == original
            check_display(False)
            _, maya_state['static_names'] = inspect_rig(first_ns, animation=False)
            assert cmds.ls(selection=True), 'Display-off import should keep imported roots selected'

            animated = send('import', True, True)['data']
            second_ns = animated['namespace']
            assert second_ns != first_ns and cmds.namespace(exists=first_ns), animated
            assert cmds.objExists('UnsavedMarker') and cmds.objExists('SavedMarker')
            assert Path(cmds.file(query=True, sceneName=True)) == original
            assert math.isclose(cmds.getAttr(old_joint + '.radius'), 2.4)
            check_display(True)
            assert not cmds.ls(selection=True)
            _, maya_state['animated_names'] = inspect_rig(second_ns, animation=True, optimized=True)
            maya_state['namespaces'] = [first_ns, second_ns]

            opened = send('open', False, True, confirm=True)['data']
            assert opened['mode'] == 'open' and not opened['namespace'], opened
            assert not cmds.objExists('SavedMarker') and not cmds.objExists('UnsavedMarker')
            assert not cmds.file(query=True, sceneName=True)
            assert original.read_bytes() == original_bytes, 'Open wrote to the original scene'
            check_display(True)
            inspect_rig('', animation=False, optimized=True)

            cmds.createNode('transform', name='SecondUnsavedMarker')
            opened = send('open', True, False, confirm=True)['data']
            assert opened['mode'] == 'open' and not cmds.objExists('SecondUnsavedMarker'), opened
            assert not cmds.file(query=True, sceneName=True)
            assert cmds.ls(selection=True), 'Display-off open should keep imported roots selected'
            mesh, maya_state['open_names'] = inspect_rig('', animation=True)
            cmds.currentTime(1)
            cmds.select(mesh, replace=True)
            filepath = folder / 'roundtrip.fbx'
            assert call(maya, 'fbx_export_blender', filepath=str(filepath))['skin_influence_count'] == len(BONES)
            assert call(blender, 'test_fbx_clear')['objects'] == 0
            assert call(blender, 'fbx_receive_maya', filepath=str(filepath))['objects'] >= 2
            names = call(blender, 'test_fbx_roundtrip')['names']
            maya_state['roundtrip'] = dict(zip(BONES, names))
            assert call(blender, 'test_fbx_finish')['scheduled']
            assert call(blender, 'bridge_disconnect')['stopped']
            assert call(maya, 'bridge_disconnect')['stopped']
            assert process.wait(timeout=8) == 0
        finally:
            server.stop()
    report = dict(ok=True, blender=blender_version, maya=cmds.about(version=True), **maya_state)
    # Schedule a Python callable so this isolated runner exits after writing its report.
    utils.executeDeferred(cmds.quit, force=True, exitCode=0)
    return report


def inspect_rig(namespace, *, animation, optimized=False):
    from maya import cmds

    pattern = namespace + ':*' if namespace else '*'
    mesh, = cmds.ls(pattern, type='mesh', long=True, noIntermediate=True)
    target, = cmds.listRelatives(mesh, parent=True, fullPath=True)
    skin, = cmds.ls(cmds.listHistory(mesh), type='skinCluster')
    cmds.currentTime(1)
    coordinates = cmds.xform(target + '.vtx[*]', query=True, worldSpace=True, translation=True)
    assert_vertex_order(list(zip(coordinates[::3], coordinates[1::3], coordinates[2::3])))
    names = []
    joints = []
    for index in range(len(BONES)):
        vertex = f'{target}.vtx[{index}]'
        joint, = cmds.skinPercent(skin, vertex, query=True, transform=None, ignoreBelow=0.99)
        joints.append(joint)
        names.append(joint.rsplit('|', 1)[-1].rsplit(':', 1)[-1])
        if optimized:
            assert math.isclose(cmds.getAttr(joint + '.radius'), 1, abs_tol=1e-6)
            assert cmds.getAttr(joint + '.drawStyle') == 0
    assert len(set(names)) == len(BONES), names
    keyframes = cmds.keyframe(joints[0], query=True, keyframeCount=True) or 0
    if animation:
        assert keyframes > 1 and cmds.currentUnit(query=True, time=True) == 'ntsc', keyframes
        start = cmds.xform(target + '.vtx[0]', query=True, worldSpace=True, translation=True)
        cmds.currentTime(12)
        end = cmds.xform(target + '.vtx[0]', query=True, worldSpace=True, translation=True)
        assert math.dist(start, end) > 0.1, (start, end)
    else:
        assert keyframes == 0, keyframes
    return target, dict(zip(BONES, names))


if __name__ == '__main__':
    start_blender()
