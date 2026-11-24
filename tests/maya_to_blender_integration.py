"""Maya action registry and real Blender TCP bridge, using disposable scenes."""
import json
from math import isclose
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
POINTS = [(0, 0, 0), (1, 0, .4), (2, 1, .2), (1, 2, .6), (0, 1, .3)]


def _wait(predicate, process=None):
    deadline = time.monotonic() + 20
    while not predicate():
        if time.monotonic() >= deadline or (process is not None and process.poll() is not None):
            raise RuntimeError('Blender fixture stopped or timed out; see blender.log')
        time.sleep(.02)


def blender_stage(folder):
    import bpy
    from blender_actions.fbx_maya import export_maya
    from blender_bridge import server

    bpy.ops.wm.read_factory_settings(use_empty=True)
    geometry = bpy.data.meshes.new('VertexTarget')
    geometry.from_pydata(POINTS, [], [(0, 1, 2), (0, 2, 3, 4)])
    mesh = bpy.data.objects.new('VertexTarget', geometry)
    bpy.context.collection.objects.link(mesh)
    mesh.vertex_groups.new(name='KeepWeights').add([0, 1], .25, 'REPLACE')
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    export_maya(str(folder / 'source.fbx'))

    original_file = folder / 'original.blend'
    bpy.context.scene.render.fps = 30
    bpy.ops.wm.save_as_mainfile(filepath=str(original_file))
    sentinel = bpy.data.objects.new('UnsavedObject', None)
    bpy.context.collection.objects.link(sentinel)
    mesh.select_set(False)
    bpy.context.view_layer.objects.active = None
    bpy.context.view_layer.update()
    server.reload_actions()
    receive_vertices = server.ACTIONS['vertex_positions_receive_maya']
    target_vertices = server.ACTIONS['vertex_positions_target_maya']
    receive_fbx = server.ACTIONS['fbx_receive_maya']
    checks = []

    def has_motion(objects):
        return any(max(values) - min(values) > .01
                   for obj in objects if obj.animation_data and obj.animation_data.action
                   for curve in obj.animation_data.action.fcurves if curve.data_path == 'location'
                   if (values := [key.co.y for key in curve.keyframe_points]))

    def vertex_receive(directory, source, target):
        import struct

        def coords():
            return [tuple(vertex.co) for vertex in mesh.data.vertices]

        def selection():
            return set(bpy.context.selected_objects), bpy.context.view_layer.objects.active

        assert source['name'].rsplit(':', 1)[-1] == mesh.name == target['name'], source
        prior_selection = selection()
        first = 'vertex positions without selection' not in checks
        assert prior_selection == ((set(), None) if first else ({sentinel}, sentinel))

        def reject(label, message):
            original = coords()
            try:
                receive_vertices(directory, source, target)
            except ValueError as exc:
                assert message in str(exc), (label, str(exc))
            else:
                raise AssertionError(label + ' was accepted')
            assert coords() == original, label
            assert selection() == prior_selection
            checks.append(label)

        if not first:
            result = receive_vertices(directory, source, target)
            assert selection() == prior_selection and isclose(coords()[1][0], 1.3, abs_tol=1e-6)
            checks.append('vertex positions with another active object')
            return result

        mesh.name = 'VertexTarget.001'
        try:
            reject('missing exact target', '找不到同名网格')
            replacement = bpy.data.objects.new('VertexTarget', mesh.data.copy())
            bpy.context.scene.collection.objects.link(replacement)
            try:
                original_replacement = [tuple(vertex.co) for vertex in replacement.data.vertices]
                reject('changed target', '目标网格 VertexTarget 已变化')
                assert [tuple(vertex.co) for vertex in replacement.data.vertices] == original_replacement
            finally:
                bpy.data.objects.remove(replacement, do_unlink=True)
        finally:
            mesh.name = 'VertexTarget'

        library = folder / 'duplicate.blend'
        bpy.data.libraries.write(str(library), {mesh})
        with bpy.data.libraries.load(str(library), link=True) as (available, linked):
            linked.objects = ['VertexTarget']
        duplicate, = linked.objects
        # Equal names can exist across separate libraries and local data.
        bpy.context.scene.collection.objects.link(duplicate)
        try:
            assert duplicate.name == mesh.name
            reject('ambiguous target', '2 个同名网格：VertexTarget')
        finally:
            bpy.data.objects.remove(duplicate, do_unlink=True)

        peer = bpy.data.objects.new(source['name'], mesh.data)
        bpy.context.scene.collection.objects.link(peer)
        try:
            reject('exact match takes precedence even if unwritable', f'{source["name"]} 不可写')
        finally:
            bpy.data.objects.remove(peer, do_unlink=True)
        assert target_vertices(source['name']) == target
        path = Path(directory) / 'mesh-0.faces'
        original_faces = path.read_bytes()
        try:
            topology = bytearray(original_faces)
            struct.pack_into('<I', topology, 4 * (source['faces'] + 2), 4)
            path.write_bytes(topology)
            reject('changed indexed topology', '同索引面拓扑不一致')
        finally:
            path.write_bytes(original_faces)

        path = Path(directory) / 'mesh-0.points'
        original_points = path.read_bytes()
        try:
            values = struct.unpack('<15d', original_points)
            path.write_bytes(struct.pack('<15d', *(value + 10 for value in values)))
            reject('ambiguous correspondence', '无法唯一确认')
        finally:
            path.write_bytes(original_points)
        result = receive_vertices(directory, source, target)
        assert selection() == prior_selection
        expected = list(POINTS)
        expected[1] = (1.2, 0, .4)
        for actual, point in zip(coords(), expected):
            assert all(isclose(a, b, abs_tol=1e-6) for a, b in zip(actual, point)), (actual, point)
        assert [mesh.vertex_groups['KeepWeights'].weight(index) for index in (0, 1)] == [.25, .25]
        checks.append('vertex positions without selection')
        sentinel.select_set(True)
        bpy.context.view_layer.objects.active = sentinel
        time.sleep(server.MAIN_THREAD_TIMEOUT + .1)
        return result

    def fbx_receive(filepath, mode, animation, scene, discard_unsaved):
        target_scene = bpy.context.scene
        params = dict(mode=mode, animation=animation, scene=scene, discard_unsaved=discard_unsaved)
        if mode == 'import':
            before = set(target_scene.objects)
            result = receive_fbx(filepath, **params)
            assert set(target_scene.objects) > before
            assert bpy.context.scene == target_scene and bpy.data.filepath == str(original_file)
            assert target_scene.render.fps == 30
            imported = set(target_scene.objects) - before
            keyed = [obj for obj in imported if obj.animation_data and obj.animation_data.action]
            assert bool(keyed) == animation, [obj.name for obj in keyed]
            assert has_motion(imported) == animation
            checks.append('import animated' if animation else 'import static')
            return result

        ids = set(bpy.data.user_map())
        selection = set(bpy.context.selected_objects)
        before = set(target_scene.objects)

        def unchanged(label):
            assert bpy.context.scene == target_scene and bpy.data.filepath == str(original_file)
            assert set(target_scene.objects) == before and sentinel in before
            assert set(bpy.data.user_map()) == ids and set(bpy.context.selected_objects) == selection
            checks.append(label)

        try:
            receive_fbx(filepath, **dict(params, discard_unsaved=False))
        except ValueError as exc:
            assert '确认打开' in str(exc)
        else:
            raise AssertionError('unconfirmed open succeeded')
        unchanged('unconfirmed open')

        invalid = Path(filepath).with_name('invalid.fbx')
        invalid.write_bytes(b'invalid FBX')
        try:
            receive_fbx(str(invalid), **params)
        except RuntimeError:
            pass
        else:
            raise AssertionError('bad FBX opened')
        finally:
            invalid.unlink()
        unchanged('staged import failure')

        real_data = bpy.data

        class FailingWrite:
            def __getattr__(self, name):
                return getattr(real_data, name)

            @property
            def libraries(self):
                def write(*args):
                    assert any(scene.name.startswith('Maya') for scene in real_data.scenes)
                    raise OSError('simulated .blend write failure')
                return SimpleNamespace(write=write)

        bpy_proxy = SimpleNamespace(data=FailingWrite(), ops=bpy.ops, context=bpy.context)
        with patch.dict(sys.modules, bpy=bpy_proxy):
            try:
                receive_fbx(filepath, **params)
            except OSError as exc:
                assert 'write failure' in str(exc)
            else:
                raise AssertionError('failed .blend write replaced the scene')
        unchanged('staged blend write failure')

        result = receive_fbx(filepath, **params)
        assert bpy.data.filepath == result['opened_path'], (bpy.data.filepath, result)
        assert len(bpy.data.scenes) == 1
        assert not bpy.data.objects.get('UnsavedObject')
        objects = list(bpy.context.scene.objects)
        assert objects and has_motion(objects)
        checks.append('open animated')
        return result

    server.ACTIONS['vertex_positions_receive_maya'] = vertex_receive
    server.ACTIONS['fbx_receive_maya'] = fbx_receive
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
    from ui import maya_to_blender

    folder = Path(os.environ['QZ_NATIVE_RESULT']).parent
    executable = Path(os.environ['QZ_TEST_BLENDER']).resolve(strict=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('MAYA', 'BLENDER'))}
    env['BLENDER_USER_RESOURCES'] = str(folder / 'blender-profile')
    with (folder / 'blender.log').open('wb') as log:
        process = subprocess.Popen([str(executable), '--background', '--factory-startup', '--python-exit-code',
                                    '1', '--python', __file__, '--', str(folder)], env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            _wait(lambda: (folder / 'port').exists(), process)
            cmds.file(new=True, force=True)
            receive_blender(str(folder / 'source.fbx'))
            mesh, = cmds.ls(type='mesh', long=True, noIntermediate=True)
            transform, = cmds.listRelatives(mesh, parent=True, fullPath=True)
            cmds.currentUnit(linear='cm', time='film')
            cmds.select(transform, replace=True)
            cmds.move(20, 0, 0, f'{mesh}.vtx[1]', relative=True, worldSpace=True)

            def maya_call(action, timeout, connect_timeout, **params):
                return dict(ok=True, data=ACTIONS[action](**params))

            maya = SimpleNamespace(connected=True, call=maya_call)
            blender = DccClient.for_dcc('blender', port=int((folder / 'port').read_text()))
            blender.connected = True
            original = (folder / 'original.blend').read_bytes()
            with patch.object(maya_to_blender.paths, 'local_data_dir', return_value=folder / 'exchange'):
                result = maya_to_blender.run_transfer(maya, blender, 'vertices')
                assert result['ok'], result
                cmds.move(10, 0, 0, f'{mesh}.vtx[1]', relative=True, worldSpace=True)
                result = maya_to_blender.run_transfer(maya, blender, 'vertices')
                assert result['ok'], result
                cmds.playbackOptions(minTime=1, maxTime=8)
                cmds.setKeyframe(transform, attribute='translateX', time=1, value=0)
                cmds.setKeyframe(transform, attribute='translateX', time=8, value=50)
                for mode, animation in (('import', False), ('import', True), ('open', True)):
                    result = maya_to_blender.run_transfer(maya, blender, mode, animation, mode == 'open')
                    assert result['ok'], result
            assert Path(result['data']['opened_path']).is_file()
            assert (folder / 'original.blend').read_bytes() == original
            response = blender.call('fbx_blender_preflight', direction='import')
            assert response['ok'] and response['data']['scene']['path'] == result['data']['opened_path'], response
            assert cmds.ls(selection=True, long=True) == [transform]
        finally:
            (folder / 'stop').touch()
            if process.wait(timeout=10):
                raise RuntimeError('Blender fixture failed; see blender.log')
    checks = json.loads((folder / 'checks.json').read_text(encoding='utf-8'))
    assert len(checks) == 14, checks
    return dict(ok=True, native_checks=checks, bridge_usable_after_open=True)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    blender_stage(Path(sys.argv[sys.argv.index('--') + 1]))
