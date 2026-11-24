"""World-coordinate transfer, rejection/rollback, and real Qt clicks; offline."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maya_actions import vertex_positions as source
from max_actions import vertex_positions as target
from ui import vertex_positions as coordinator


class Point:
    def __init__(self, *values):
        self.values = np.asarray(values, dtype=float)
        self.x, self.y, self.z = values

    def __iter__(self):
        return iter(self.values)

    def __mul__(self, matrix):
        return Point(*(np.append(self.values, 1.) @ matrix.data)[:3])


class Matrix:
    def __init__(self, values=None):
        self.data = np.eye(4) if values is None else np.array(values, dtype=float)
        self.row1, self.row2, self.row3 = [Point(*row[:3]) for row in self.data[:3]]

    def __mul__(self, other):
        return Matrix(self.data @ other.data)


class Base:
    def __init__(self, kind='poly', count=3):
        self.kind = kind
        self.points = [Point(i, 2*i, 3*i) for i in range(count)]


def node(name='Body', kind='poly', count=3):
    return types.SimpleNamespace(name=name, baseObject=Base(kind, count), objectTransform=Matrix(),
                                 modifiers=[object()])


class Runtime:
    Editable_Poly, Editable_mesh = 'poly', 'mesh'

    def __init__(self):
        self.objects = [node()]
        self.polyop = self.meshop = self
        self.animating, self.redraw = True, True
        self.writes, self.updates = [], []
        self.fail_once = False
        self.silent_once = False

    def classOf(self, base):
        return base.kind

    def isKindOf(self, base, kind):
        return base.kind == kind

    def Name(self, name):
        return name

    def Array(self, *values):
        return list(values)

    Point3 = Point

    def inverse(self, matrix):
        return Matrix(np.linalg.inv(matrix.data))

    def getNumVerts(self, base):
        return len(base.points)

    def getVerts(self, base, vertices):
        assert vertices == 'all'
        return [Point(*point) for point in base.points]

    def setVert(self, base, vertices, positions):
        assert not self.animating, 'AutoKey must be disabled temporarily'
        self.writes.append(base)
        if self.fail_once:
            self.fail_once = False
            base.points[0] = Point(999, 999, 999)
            raise RuntimeError('simulated partial write')
        if self.silent_once:
            self.silent_once = False
            return
        base.points = [Point(*point) for point in positions]

    @contextmanager
    def animate(self, value):
        old, self.animating = self.animating, value
        try:
            yield
        finally:
            self.animating = old

    def update(self, obj):
        self.updates.append(obj)

    def disableSceneRedraw(self):
        self.redraw = False

    def enableSceneRedraw(self):
        self.redraw = True

    def redrawViews(self):
        pass


def write_points(directory, points, index=0, name='Body'):
    values = [float(value) for point in points for value in point]
    (Path(directory) / f'mesh-{index}.points').write_bytes(struct.pack(f'<{len(values)}d', *values))
    return dict(index=index, vertices=len(points), name=name, mesh='|group|' + name)


class MaxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qz-vertex-transfer-')
        self.rt = Runtime()
        self.modules = patch.dict(sys.modules, {'pymxs': types.SimpleNamespace(runtime=self.rt, animate=self.rt.animate)})
        self.modules.start()
        self.points = [(11.125, 22.25, -33.5), (4.5, -5.25, 6.125), (-7.5, 8.125, 9.75)]
        self.mesh = write_points(self.temp.name, self.points)

    def tearDown(self):
        self.modules.stop()
        self.temp.cleanup()

    def run_import(self, meshes=None):
        result = target.import_positions(self.temp.name, meshes or [self.mesh])
        self.assertTrue(self.rt.redraw)
        self.assertTrue(self.rt.animating)
        return result

    def test_poly_and_mesh_world_points_account_for_rotation_scale_translation_and_pivot(self):
        for kind in ('poly', 'mesh'):
            obj = node(kind=kind)
            obj.objectTransform = Matrix([[0, 2, 0, 0], [-3, 0, 0, 0], [0, 0, -4, 0], [100, -50, 25, 1]])
            self.rt.objects = [obj]
            modifiers = list(obj.modifiers)
            result = self.run_import()
            self.assertEqual(result['count'], 1, result)
            self.assertEqual(result['vertices'], 3)
            self.assertEqual(obj.modifiers, modifiers)
            actual = [list(point * obj.objectTransform) for point in obj.baseObject.points]
            np.testing.assert_allclose(actual, self.points, atol=1e-6)

    def test_missing_duplicate_target_or_source_and_mismatched_count_skip_without_writing(self):
        for objects, meshes in [([], [self.mesh]), ([node(), node()], [self.mesh]),
                                ([node(count=4)], [self.mesh]), ([node()], [self.mesh, self.mesh])]:
            self.rt.objects = objects
            result = self.run_import(meshes)
            self.assertEqual(result['count'], 0)
            self.assertTrue(result['errors'])
            self.assertEqual(self.rt.writes, [])

    def test_instanced_base_and_unsupported_primitive_skip(self):
        first, other = node(), node('Other')
        other.baseObject = first.baseObject
        for objects in ([first, other], [node(kind='Box')]):
            self.rt.objects = objects
            result = self.run_import()
            self.assertEqual(result['count'], 0)
            self.assertEqual(self.rt.writes, [])

    def test_singular_transform_is_rejected_before_writes(self):
        self.rt.objects[0].objectTransform = Matrix(np.zeros((4, 4)))
        self.assertEqual(self.run_import()['count'], 0)
        self.assertEqual(self.rt.writes, [])

    def test_bad_file_or_nonfinite_points_reject_before_writes(self):
        path = Path(self.temp.name) / 'mesh-0.points'
        valid = path.read_bytes()
        for data in (valid[:-1], struct.pack('<9d', *([float('nan')] + [0.]*8))):
            path.write_bytes(data)
            self.assertEqual(self.run_import()['count'], 0)
            self.assertEqual(self.rt.writes, [])

    def test_bad_descriptor_cannot_traverse_paths(self):
        for index in (-1, True, '../outside'):
            self.mesh['index'] = index
            self.assertEqual(self.run_import()['count'], 0)
        self.assertEqual(self.rt.writes, [])

    def test_failed_or_silently_ignored_write_restores_original_points(self):
        original = [list(point) for point in self.rt.objects[0].baseObject.points]
        for flag in ('fail_once', 'silent_once'):
            setattr(self.rt, flag, True)
            result = self.run_import()
            self.assertEqual(result['count'], 0)
            self.assertIn('已恢复', result['errors'][0]['error'])
            self.assertEqual([list(point) for point in self.rt.objects[0].baseObject.points], original)

    def test_multiple_meshes_continue_after_one_skipped_model(self):
        self.mesh['name'] = 'Missing'
        second = write_points(self.temp.name, self.points, 1)
        result = self.run_import([self.mesh, second])
        self.assertEqual((result['count'], len(result['errors'])), (1, 1))

    def test_old_bridge_stops_before_redraw_or_geometry_change(self):
        from max_bridge import server
        with patch.object(server, 'SUPPORTS_EXECUTION_TIMEOUT', False):
            with self.assertRaises(RuntimeError):
                self.run_import()
        self.assertTrue(self.rt.redraw)
        self.assertEqual(self.rt.writes, [])


def translation(values):
    result = np.eye(4)
    result[3, :3] = values
    return result


class Transform:
    """Component transform fake; no Maya scene setter is provided."""
    def __init__(self, other=None):
        self.scale = [1.]*3
        self.translation = self.scale_pivot = self.rotate_pivot = self.pivot_translation = [0.]*3
        self.angle = self.shear = 0.
        if other is not None:
            self.__dict__ = copy.deepcopy(other.__dict__)

    def asMatrix(self):
        c, s = np.cos(self.angle), np.sin(self.angle)
        rotation = np.array([[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        shear = np.eye(4)
        shear[1, 0] = self.shear
        return Matrix(translation(-np.array(self.scale_pivot)) @ np.diag([*self.scale, 1.])
                      @ shear @ translation(self.scale_pivot) @ translation(self.pivot_translation)
                      @ translation(-np.array(self.rotate_pivot)) @ rotation
                      @ translation(self.rotate_pivot) @ translation(self.translation))


class MayaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qz-vertex-transfer-')
        self.points = [Point(1.2345678912345, 2, 3), Point(4, 5, 6)]
        self.input_points = [Point(7, 8, 9), Point(10, 11, 12)]
        self.skins = []
        self.input_count = 2
        self.parent = Matrix()
        self.transform = Transform()
        self.spaces = []
        owner = self
        class DagPath:
            def __init__(self, name):
                self.name = name
            def node(self):
                return self.name
            def inclusiveMatrix(self):
                return owner.transform.asMatrix() * owner.parent
        class Selection:
            def add(self, shape):
                self.shape = shape
            def getDagPath(self, index):
                return DagPath(self.shape)
            def getDependNode(self, index):
                return self.shape
        class Mesh:
            def __init__(self, path):
                self.input = path == 'input-data'
                self.numVertices = owner.input_count if self.input else len(owner.points)
            def getPoints(self, space):
                owner.spaces.append(space)
                return owner.input_points if self.input else owner.points
        class Skin:
            def __init__(self, dependency):
                assert dependency == 'skin1'
            def indexForOutputShape(self, shape):
                assert shape == '|group|Body|Shape'
                return 7
            def findPlug(self, name, networked):
                assert name == 'input'
                return self
            def elementByLogicalIndex(self, index):
                assert index == 7
                return self
            def attribute(self, name):
                return name
            def child(self, attribute):
                assert attribute == 'inputGeometry'
                return self
            def asMObject(self):
                return 'input-data'
        self.om = types.SimpleNamespace(MSelectionList=Selection, MFnMesh=Mesh, MMatrix=Matrix,
                                       MFnTransform=lambda path: types.SimpleNamespace(
                                           scale=lambda: self.transform.scale,
                                           scalePivot=lambda space: Point(*self.transform.scale_pivot)),
                                       MSpace=types.SimpleNamespace(kObject=0, kWorld=99, kTransform=1))
        self.cmds = types.SimpleNamespace(listRelatives=lambda *a, **k: ['|group|Body'],
                                         listHistory=lambda *a, **k: self.skins,
                                         nodeType=lambda node: 'skinCluster')
        maya = types.ModuleType('maya')
        maya.cmds = self.cmds
        api = types.ModuleType('maya.api')
        api.OpenMaya = self.om
        api.OpenMayaAnim = types.SimpleNamespace(MFnSkinCluster=Skin)
        maya.api = api
        self.modules = patch.dict(sys.modules, {'maya': maya, 'maya.cmds': self.cmds, 'maya.api': api,
                                               'maya.api.OpenMaya': self.om,
                                               'maya.api.OpenMayaAnim': api.OpenMayaAnim})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.temp.cleanup()

    def export(self):
        with patch.object(source, '_selected_meshes', return_value=(['|group|Body|Shape'], [])):
            return source.export_positions(self.temp.name)

    def values(self):
        return struct.unpack('<6d', (Path(self.temp.name)/'mesh-0.points').read_bytes())

    def test_object_api_points_are_float64_in_vertex_order_and_do_not_edit_scene(self):
        result = self.export()
        self.assertEqual(self.spaces, [0])
        self.assertEqual(result['meshes'][0]['name'], 'Body')
        self.assertEqual(self.values(), tuple(v for p in self.points for v in p))
        self.assertTrue(coordinator._compatible(source.preflight(), 'maya'))
        self.assertTrue(coordinator._compatible(result, 'maya'))

    def test_scale_254_is_removed_without_scaling_translation(self):
        self.transform.scale = [2.54]*3
        self.transform.translation = [100, -50, 25]
        result = self.export()
        self.assertFalse(result['errors'], result)
        expected = np.asarray([list(p) for p in self.points]) + [100, -50, 25]
        np.testing.assert_allclose(np.asarray(self.values()).reshape(-1, 3), expected, atol=1e-12)
        self.assertEqual(self.transform.scale, [2.54]*3)

    def test_nonuniform_negative_scale_pivots_rotation_shear_and_parent_are_preserved(self):
        self.transform.scale = [-2.54, 3., .5]
        self.transform.translation = [100, -50, 25]
        self.transform.scale_pivot = [2, 3, 4]
        self.transform.rotate_pivot = [-3, 5, -1]
        self.transform.pivot_translation = [9, 2, 1]
        self.transform.angle = .73
        self.transform.shear = .25
        self.parent = Matrix([[0, 2, 0, 0], [-3, 0, 0, 0], [0, 0, 4, 0], [30, 20, 10, 1]])
        before = self.transform.asMatrix().data.copy()
        expected_transform = Transform(self.transform)
        expected_transform.scale = [1.]*3
        expected = [list(p * (expected_transform.asMatrix() * self.parent)) for p in self.points]
        result = self.export()
        self.assertFalse(result['errors'], result)
        np.testing.assert_allclose(np.asarray(self.values()).reshape(-1, 3), expected, atol=1e-11)
        np.testing.assert_array_equal(self.transform.asMatrix().data, before)

    def test_skin_input_not_posed_output_is_exported_and_roundtrips_to_max_base(self):
        self.skins = ['skin1', 'skin1']
        self.transform.scale = [2.54]*3
        self.transform.translation = [11, 22, 33]
        result = self.export()
        self.assertFalse(result['errors'], result)
        expected = np.asarray([list(p) for p in self.input_points]) + [11, 22, 33]
        np.testing.assert_allclose(np.asarray(self.values()).reshape(-1, 3), expected, atol=1e-12)
        rt = Runtime()
        obj = node(count=2)
        obj.objectTransform = Matrix([[0, 2, 0, 0], [-3, 0, 0, 0], [0, 0, 4, 0], [10, 20, 30, 1]])
        rt.objects = [obj]
        modifiers = list(obj.modifiers)
        with patch.dict(sys.modules, {'pymxs': types.SimpleNamespace(runtime=rt, animate=rt.animate)}):
            received = target.import_positions(self.temp.name, result['meshes'])
        self.assertEqual(received['count'], 1, received)
        np.testing.assert_allclose([list(p * obj.objectTransform) for p in obj.baseObject.points], expected)
        self.assertEqual(obj.modifiers, modifiers)

    def test_multi_skin_mismatch_or_zero_scale_skip_before_creating_file(self):
        for skins, count, scale, message in [(['skin1', 'skin2'], 2, 1, '多层'),
                                              (['skin1'], 3, 1, '数量'),
                                              ([], 2, 0, '缩放为零')]:
            self.skins, self.input_count = skins, count
            self.transform.scale = [scale]*3
            result = self.export()
            self.assertFalse(result['meshes'])
            self.assertIn(message, result['errors'][0]['error'])
            self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_nonfinite_points_skip_model_without_creating_file(self):
        self.points[0] = Point(float('inf'), 0, 0)
        result = self.export()
        self.assertEqual(result['meshes'], [])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


class Client:
    connected = True
    def __init__(self, side):
        self.side, self.calls, self.stale, self.fail = side, [], False, None
        self.wrong_policy = self.bad_export = self.refuse_reload = False
    def call(self, action, **params):
        self.calls.append((action, params))
        if action == self.fail:
            return dict(ok=False, error='timed out')
        if action.endswith('_preflight'):
            data = source.preflight() if self.side == 'maya' else dict(version=1, space='world', target='base_mesh')
            if self.stale:
                data['version'] = 1 if self.side == 'maya' else 0
            if self.wrong_policy:
                data['scale_policy'] = 'evaluated_scale'
            return dict(ok=True, data=data)
        if action == 'reload_actions':
            if not self.refuse_reload:
                self.stale = self.wrong_policy = False
        if action == 'vertex_positions_export':
            entry = write_points(params['directory'], [(1, 2, 3)], name='BodyFBXASC032Mesh')
            data = dict(source.preflight(), meshes=[entry], errors=[])
            if self.bad_export:
                data['positions'] = 'evaluated'
            return dict(ok=True, data=data)
        return dict(ok=True, data=dict(count=1, vertices=1, reports=[dict(name='Body Mesh', vertices=1)], errors=[]))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch('paths.local_data_dir', return_value=self.temp.name)
        self.root_patch.start()
        self.maya, self.max = Client('maya'), Client('max')
    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()
    def test_success_uses_existing_clients_decodes_space_and_cleans_exchange(self):
        result = coordinator.run_transfer(self.maya, self.max)
        self.assertTrue(result['ok'], result)
        action, params = self.max.calls[-1]
        self.assertEqual((action, params['meshes'][0]['name']), ('vertex_positions_import', 'Body Mesh'))
        self.assertEqual(params['execution_timeout'], 300)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_disconnected_client_stops_before_export_and_files(self):
        self.max.connected = False
        self.assertFalse(coordinator.run_transfer(self.maya, self.max)['ok'])
        self.assertEqual(self.maya.calls + self.max.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_stale_actions_reload_before_export(self):
        self.maya.stale = self.max.stale = True
        self.assertTrue(coordinator.run_transfer(self.maya, self.max)['ok'])
        for client in (self.maya, self.max):
            self.assertEqual(client.calls[1][0], 'reload_actions')
    def test_wrong_source_policy_reloads_and_unchanged_policy_stops_before_files(self):
        self.maya.wrong_policy = True
        self.assertTrue(coordinator.run_transfer(self.maya, self.max)['ok'])
        self.assertIn('reload_actions', [a for a, _ in self.maya.calls])
        self.maya.calls.clear()
        self.maya.wrong_policy = self.maya.refuse_reload = True
        result = coordinator.run_transfer(self.maya, self.max)
        self.assertFalse(result['ok'])
        self.assertNotIn('vertex_positions_export', [a for a, _ in self.maya.calls])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_export_policy_is_rechecked_before_max_write(self):
        self.maya.bad_export = True
        result = coordinator.run_transfer(self.maya, self.max)
        self.assertFalse(result['ok'])
        self.assertNotIn('vertex_positions_import', [a for a, _ in self.max.calls])
    def test_timeout_keeps_exchange_and_never_retries_writes(self):
        self.max.fail = 'vertex_positions_import'
        result = coordinator.run_transfer(self.maya, self.max)
        self.assertFalse(result['ok'])
        self.assertTrue((Path(result['retained_path'])/'mesh-0.points').is_file())
        self.assertEqual(sum(a == 'vertex_positions_import' for a, _ in self.max.calls), 1)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        from ui.plugin_loader import scan
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)
        info = next(i for i in scan(str(ROOT/'plugins')) if i.key == ('maya', 'fbx_to_max'))
        cls.factory = staticmethod(info.load_factory())
    def test_real_vertex_button_click_shares_clients_disables_inputs_and_logs_on_ui_thread(self):
        from PySide6 import QtCore, QtTest
        calls, logs = [], []
        main_thread = threading.current_thread()
        def log(message):
            logs.append((threading.current_thread(), message))
        def run(maya, max_client):
            calls.append((maya, max_client, threading.current_thread()))
            time.sleep(.02)
            return dict(ok=True, operation='vertex_positions', data=dict(count=1, vertices=3, errors=[]))
        with tempfile.TemporaryDirectory() as folder:
            settings = QtCore.QSettings(str(Path(folder)/'settings.ini'), QtCore.QSettings.IniFormat)
            with patch('PySide6.QtCore.QSettings', return_value=settings):
                maya, max_client = Client('maya'), Client('max')
                widget = self.factory(maya, types.SimpleNamespace(info=log, warn=log, error=log))
                widget.set_dcc_clients({'max': max_client})
                widget.show()
                self.app.processEvents()
                try:
                    with patch.dict(self.factory.__globals__, run_vertex_transfer=run):
                        QtTest.QTest.mouseClick(widget.vertices_button, QtCore.Qt.LeftButton)
                        self.assertTrue(all(not b.isEnabled() for b in (widget.open_button, widget.import_button, widget.vertices_button, widget.settings_button)))
                        deadline = time.monotonic() + 5
                        while widget._task is not None and time.monotonic() < deadline:
                            QtTest.QTest.qWait(10)
                        self.assertIsNone(widget._task)
                    self.assertEqual(len(calls), 1)
                    self.assertIs(calls[0][0], maya)
                    self.assertIs(calls[0][1], max_client)
                    self.assertIsNot(calls[0][2], main_thread)
                    self.assertTrue(all(thread is main_thread for thread, _ in logs))
                    self.assertTrue(any('顶点位置发送完成' in message for _, message in logs))
                    self.assertFalse(widget.vertices_button.toolTip())
                finally:
                    widget.close()
                    widget.deleteLater()
                    self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
