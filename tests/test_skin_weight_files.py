"""项目文件、真实 Maya 动作替身、Qt 两按钮异步流程。"""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from maya_actions import skin_weight_files as action
from ui import skin_weight_files as files
from test_skin_weights import FakeMaya


class MayaClient:
    connected = True
    def __init__(self): self.calls = []
    def call(self, name, timeout=10, connect_timeout=None, **params):
        self.calls.append((name, params.copy(), threading.current_thread().name))
        try:
            return dict(ok=True, data=action.skin_weights_files(**params))
        except Exception as exc:
            return dict(ok=False, error=str(exc))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.old_modules = {name: sys.modules.get(name) for name in
                            ("maya", "maya.cmds", "maya.api", "maya.api.OpenMaya", "maya.api.OpenMayaAnim")}
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "middle"
        root_patch = patch("ui.skin_weight_files.paths.project_dir", return_value=str(self.project))
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.maya = FakeMaya([[.123456789123, .876543210877, 0.], [.7, .3, 0.]]).install()
        self.cmds = sys.modules["maya.cmds"]
        self.dq = [.25, .75]
        self.maya.settings["skin.skinningMethod"] = 2
        self.created, self.added, self.deleted = [], [], []
        self.selection_sets, self.set_connections = {}, {}
        self.cmds.sets = self.selection_set
        self.cmds.listConnections = lambda plug, **kw: list(self.set_connections.get(plug, []))
        self.cmds.addAttr = lambda *args, **kw: None
        self.cmds.connectAttr = lambda source, dest: self.set_connections.setdefault(source, []).append(dest)
        self.skin_exists = True
        self.available = list(self.maya.influence_names)
        original_select = self.cmds.select
        self.cmds.select = lambda items=None, **kw: original_select(items or [], **kw)
        original_ls = self.cmds.ls
        original_relatives = self.cmds.listRelatives
        self.cmds.ls = lambda node=None, **kw: (
            list(self.available) if kw.get("type") == "joint" else [] if kw.get("type") else original_ls(node, **kw))
        self.cmds.workspace = lambda **kw: (_ for _ in ()).throw(AssertionError("Must not query Maya workspace"))
        self.cmds.polyEvaluate = lambda shape, **kw: len(self.maya.rows)
        self.cmds.listRelatives = lambda node, **kw: (
            ["|body"] if kw.get("parent") else original_relatives(node, **kw))
        self.cmds.listHistory = lambda *args, **kw: ["skin"] if self.skin_exists else []
        self.cmds.skinCluster = self.skin_cluster
        self.cmds.delete = self.delete
        skin_class = sys.modules["maya.api.OpenMayaAnim"].MFnSkinCluster
        skin_class.getBlendWeights = lambda fn, path, ids: [self.dq[index] for index in ids]
        def set_dq(fn, path, ids, values):
            for vertex, value in zip(ids, values): self.dq[vertex] = value
        skin_class.setBlendWeights = set_dq
        # 接口替身支持目标 influence 顺序和数量变化，使用独立字典核对语义。
        def set_weights(fn, path, ids, columns, values, **kw):
            self.assertFalse(kw["normalize"])
            plugs = ["skin.normalizeWeights", "skin.maintainMaxInfluences"]
            plugs.extend(bone + ".liw" for bone in self.maya.influence_names)
            self.assertFalse(any(self.maya.settings.get(plug, False) for plug in plugs))
            width = len(self.maya.influence_names)
            self.maya.writes += 1
            for row, vertex in enumerate(ids):
                self.maya.rows[vertex] = list(values[row * width:(row + 1) * width])
            if self.maya.fail_write == self.maya.writes:
                raise RuntimeError("simulated partial write")
        skin_class.setWeights = set_weights
        geometry_patch = patch("maya_actions.skin_weight_geometry.input_mesh", side_effect=self.geometry_fn)
        geometry_patch.start()
        self.addCleanup(geometry_patch.stop)
        self.client = MayaClient()
        self.archive = self.project / "temp" / "skin_weights" / "body.qzskin"

    def geometry_fn(self, shape):
        points = getattr(self, "geometry_points", [(float(i), 0., 0.) for i in range(len(self.maya.rows))])
        return types.SimpleNamespace(numVertices=len(points),
            getPoints=lambda: [types.SimpleNamespace(x=x, y=y, z=z) for x,y,z in points],
            getTriangles=lambda: ([1], [0, 1, 0]))

    def skin_cluster(self, *args, **kw):
        if kw.get("edit"):
            bone = kw["addInfluence"]
            self.added.append(bone)
            self.maya.influence_names.append(bone)
            for row in self.maya.rows: row.append(0.)
            self.maya.settings.setdefault(bone + ".liw", False)
            return
        self.created.append(args)
        self.skin_exists = True
        self.maya.influence_names = list(args[0])
        self.maya.rows = [[1.] + [0.] * (len(args[0]) - 1) for row in self.maya.rows]
        return ["skin"]

    def selection_set(self, items=None, **kw):
        if kw.get("empty"):
            base = kw["name"]
            node, index = base, 1
            while node in self.selection_sets:
                node, index = base + str(index), index + 1
            self.selection_sets[node] = []
            return node
        if "clear" in kw:
            self.selection_sets[kw["clear"]] = []
        elif "addElement" in kw:
            self.selection_sets[kw["addElement"]].extend(items)
        else:
            raise AssertionError(kw)

    def delete(self, node):
        self.deleted.append(node)
        self.skin_exists = False

    def tearDown(self):
        for name, module in self.old_modules.items():
            if module is None: sys.modules.pop(name, None)
            else: sys.modules[name] = module
        self.temporary.cleanup()

    def export(self):
        response = files.run_file_operation(self.client, "export_file")
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["count"], 1, response)
        return response

    def mutate_archive(self, change):
        with zipfile.ZipFile(self.archive) as archive:
            content = {name: archive.read(name) for name in archive.namelist()}
        change(content)
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, value in content.items(): archive.writestr(name, value)

    def test_roundtrip_full_precision_dq_and_constraints_without_reread(self):
        rows, dq = copy.deepcopy(self.maya.rows), list(self.dq)
        settings, locks = self.maya.settings.copy(), self.maya.locks.copy()
        result = self.export()
        self.assertEqual(Path(result["data"]["directory"]), self.archive.parent)
        self.assertTrue(self.archive.is_file())
        self.maya.rows, self.dq = [[0., 0., 1.]] * 2, [1., 0.]
        reads = len(self.maya.read_calls)
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        np.testing.assert_array_equal(self.maya.rows, rows)
        self.assertEqual(self.dq, dq)
        self.assertEqual(self.maya.settings, settings)
        self.assertEqual(self.maya.locks, locks)
        self.assertEqual(len(self.maya.read_calls), reads)
        self.assertEqual(self.maya.selection, ["|body"])

    def test_archive_restores_linear_dq_and_blended_modes_including_locked_attribute(self):
        for method in (0, 1, 2):
            with self.subTest(method=method):
                self.maya.settings["skin.skinningMethod"] = method
                self.export()
                with zipfile.ZipFile(self.archive) as archive:
                    metadata = json.loads(archive.read("manifest.json"))
                    self.assertEqual(metadata["version"], 3)
                    self.assertEqual(metadata["skinning_method"], method)
                    np.testing.assert_array_equal(np.frombuffer(archive.read("dq.bin"), "<f8"), [.25, .75])
                self.maya.settings["skin.skinningMethod"] = (method + 1) % 3
                self.maya.locks["skin.skinningMethod"] = True
                self.dq = [0., 0.]
                response = files.run_file_operation(self.client, "import_file")
                self.assertEqual(response["data"]["count"], 1, response)
                self.assertEqual(self.maya.settings["skin.skinningMethod"], method)
                self.assertTrue(self.maya.locks["skin.skinningMethod"])
                self.assertEqual(self.dq, [.25, .75])

    def test_v1_archive_keeps_current_mode_and_reports_missing_metadata(self):
        self.export()
        def old_format(content):
            metadata = json.loads(content["manifest.json"])
            metadata["version"] = 1
            metadata.pop("geometry")
            content.pop("points.bin")
            content.pop("triangles.bin")
            metadata.pop("skinning_method")
            content["manifest.json"] = json.dumps(metadata).encode()
        self.mutate_archive(old_format)
        self.maya.settings["skin.skinningMethod"] = 0
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(self.maya.settings["skin.skinningMethod"], 0)
        self.assertEqual(self.dq, [.25, .75])
        self.assertTrue(any(level == "warn" and "旧文件未记录蒙皮方式" in message
                            for level, message in files.format_file_messages(response)))

    def test_v2_archive_without_valid_mode_does_not_write(self):
        self.export()
        original = self.archive.read_bytes()
        for method in (None, True, 99):
            self.archive.write_bytes(original)
            def corrupt(content):
                metadata = json.loads(content["manifest.json"])
                if method is None: metadata.pop("skinning_method")
                else: metadata["skinning_method"] = method
                content["manifest.json"] = json.dumps(metadata).encode()
            self.mutate_archive(corrupt)
            response = files.run_file_operation(self.client, "import_file")
            self.assertEqual(response["data"]["count"], 0)
            self.assertIn("蒙皮方式", response["data"]["errors"][0]["error"])
            self.assertEqual(self.maya.writes, 0)

    def test_cache_uses_middle_project_root_and_ignores_maya_project(self):
        original = self.client.call
        maya_project = Path(self.temporary.name) / "maya-project"
        def with_old_maya_path(*args, **kwargs):
            response = original(*args, **kwargs)
            if response.get("ok"):
                response["data"]["project_directory"] = str(maya_project)
            return response
        self.client.call = with_old_maya_path
        self.export()
        self.assertTrue(self.archive.is_file())
        self.assertFalse(maya_project.exists())
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(Path(response["data"]["directory"]), self.project / "temp" / "skin_weights")

    def test_per_model_replacement_preserves_other_cache_files(self):
        self.export()
        other = self.archive.with_name("clothes.qzskin")
        other.write_bytes(b"other model")
        self.maya.rows[0] = [.8, .2, 0.]
        self.export()
        self.maya.rows[0] = [0., 1., 0.]
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1)
        self.assertEqual(self.maya.rows[0], [.8, .2, 0.])
        self.assertEqual(other.read_bytes(), b"other model")
        self.assertFalse(list(self.archive.parent.glob("*.tmp")))

    def test_failed_replace_or_invalid_export_keeps_previous_archive(self):
        self.export()
        original = self.archive.read_bytes()
        with patch.object(files.os, "replace", side_effect=OSError("disk error")):
            response = files.run_file_operation(self.client, "export_file")
        self.assertEqual(response["data"]["count"], 0)
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse(list(self.archive.parent.glob("*.tmp")))
        for bad in ([0., 0., 0.], [-.1, .1, 1.], [float("nan"), 0., 1.]):
            self.maya.rows[0] = bad
            response = files.run_file_operation(self.client, "export_file")
            self.assertEqual(response["data"]["count"], 0)
            self.assertEqual(self.archive.read_bytes(), original)

    def test_missing_file_and_vertex_mismatch_do_not_modify_scene(self):
        response = files.run_file_operation(self.client, "import_file")
        self.assertIn("没有找到", response["data"]["errors"][0]["error"])
        self.assertFalse(self.archive.parent.exists())
        self.export()
        self.maya.rows.append([1., 0., 0.])
        response = files.run_file_operation(self.client, "import_file")
        self.assertIn("顶点数不匹配", response["data"]["errors"][0]["error"])
        self.assertEqual(self.maya.writes, 0)

    def test_missing_or_ambiguous_bone_skips_before_skin_creation(self):
        self.export()
        self.skin_exists = False
        self.available = ["|j0", "|j2"]
        response = files.run_file_operation(self.client, "import_file")
        self.assertIn("场景缺少骨骼", response["data"]["errors"][0]["error"])
        self.available = ["|j0", "|j1", "|other|j1"]
        response = files.run_file_operation(self.client, "import_file")
        self.assertIn("不唯一", response["data"]["errors"][0]["error"])
        self.assertFalse(self.created)
        self.assertEqual(self.maya.writes, 0)

    def test_unused_missing_bone_is_ignored_and_new_skin_created(self):
        rows = copy.deepcopy(self.maya.rows)
        self.export()
        self.available = ["|j0", "|j1"]
        self.skin_exists = False
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.maya.rows, [row[:2] for row in rows])
        self.assertEqual(self.dq, [.25, .75])

    def test_reordered_influences_added_bone_and_zeroed_extra_influence(self):
        rows = copy.deepcopy(self.maya.rows)
        self.export()
        self.maya.influence_names = ["|j2", "|j0"]
        self.maya.rows = [[1., 0.], [1., 0.]]
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(self.added, ["|j1"])
        self.assertEqual(self.maya.rows, [[0., row[0], row[1]] for row in rows])

    def test_failed_existing_skin_reports_partial_update_and_restores_settings(self):
        self.export()
        settings = self.maya.settings.copy()
        self.maya.fail_write = 1
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 0)
        self.assertIn("可能部分更新", response["data"]["errors"][0]["error"])
        self.assertEqual(self.maya.settings, settings)
        self.assertTrue(any(level == "error" for level, _ in files.format_file_messages(response)))

    def test_failed_new_skin_is_removed(self):
        self.export()
        self.skin_exists, self.maya.fail_write = False, 1
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 0)
        self.assertEqual(self.deleted, ["skin"])

    def test_corrupt_archive_variants_are_rejected_before_maya_write(self):
        self.export()
        original = self.archive.read_bytes()
        def change_metadata(content, **changes):
            value = json.loads(content["manifest.json"])
            value.update(changes)
            content["manifest.json"] = json.dumps(value).encode()
        changes = (
            lambda content: change_metadata(content, version=99),
            lambda content: change_metadata(content, name="other"),
            lambda content: content.pop("dq.bin"),
            lambda content: content.update({"weights.bin": b"bad"}),
            lambda content: content.update({"offsets.bin": np.array([0, 0, 4], "<u4").tobytes()}),
            lambda content: content.update({"indices.bin": np.array([0, 0, 0, 1], "<u4").tobytes()}),
            lambda content: content.update({"weights.bin": np.array([float("nan"), .5, .7, .3], "<f8").tobytes()}),
            lambda content: content.update({"dq.bin": np.array([0., 2.], "<f8").tobytes()}),
        )
        for change in changes:
            with self.subTest(change=change):
                self.archive.write_bytes(original)
                self.mutate_archive(change)
                response = files.run_file_operation(self.client, "import_file")
                self.assertEqual(response["data"]["count"], 0, response)
                self.assertTrue(response["data"]["errors"])
                self.assertEqual(self.maya.writes, 0)
                self.assertFalse(any(params["op"] == "import" for _, params, _ in self.client.calls))

    def test_no_selection_or_disconnection_is_clear_and_creates_no_cache(self):
        self.maya.selection = []
        for op in ("export_file", "import_file"):
            response = files.run_file_operation(self.client, op)
            self.assertIn("没有选中", response["data"]["warning"])
        self.assertFalse(self.archive.parent.exists())
        self.client.calls.clear()
        self.client.connected = False
        response = files.run_file_operation(self.client, "export_file")
        self.assertFalse(response["ok"])
        self.assertIn("Maya 未连接", response["error"])
        self.assertFalse(self.client.calls)

    def test_safe_filenames_and_duplicate_selection_do_not_overwrite(self):
        self.assertEqual(files.cache_filename("body"), "body.qzskin")
        self.assertEqual(files.cache_filename("ns:body"), "ns%3Abody.qzskin")
        for name in ("CON", "LPT1", "a/b", "a\\b", "a%3Ab", "骨骼模型"):
            filename = files.cache_filename(name)
            self.assertNotIn("/", filename)
            self.assertNotIn("\\", filename)
            self.assertNotIn(":", filename)
        meshes = [dict(index=0, name="body", mesh="|a|bodyShape"),
                  dict(index=1, name="Body", mesh="|b|bodyShape")]
        ready, errors = files._unique_meshes(meshes)
        self.assertFalse(ready)
        self.assertEqual(len(errors), 2)


    def test_multiple_blocks_preserve_last_partial_batch(self):
        rows = [[.123456789123, .876543210877, 0.], [0., 1., 0.], [.7, .3, 0.]] * 1367
        self.maya.rows = copy.deepcopy(rows)
        self.dq = [(index % 5) / 4 for index in range(len(rows))]
        expected_dq = list(self.dq)
        self.export()
        self.maya.rows = [[0., 0., 1.] for _ in rows]
        self.dq = [0.] * len(rows)
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(self.maya.rows, rows)
        self.assertEqual(self.dq, expected_dq)
        self.assertEqual(self.maya.writes, 3)

    def test_failed_bind_restores_original_bone_locks(self):
        self.export()
        self.skin_exists = False
        settings, locks = self.maya.settings.copy(), self.maya.locks.copy()
        def fail(*args, **kwargs):
            self.assertFalse(self.maya.settings["|j0.liw"])
            self.assertFalse(self.maya.locks["|j0.liw"])
            raise RuntimeError("binding failed")
        self.cmds.skinCluster = fail
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 0)
        self.assertEqual(self.maya.settings, settings)
        self.assertEqual(self.maya.locks, locks)

    def test_multi_model_export_and_partial_import(self):
        original_call = self.client.call
        def two_models(action_name, **params):
            response = original_call(action_name, **params)
            if response.get("ok") and params["op"] in ("export", "context"):
                entry = dict(response["data"]["meshes"][0], index=1, name="clothes", mesh="|clothes|clothesShape")
                response["data"]["meshes"].append(entry)
                if params["op"] == "export":
                    directory = Path(params["directory"])
                    for suffix in ("weights", "dq", "points", "triangles"):
                        (directory / f"mesh-1.{suffix}").write_bytes((directory / f"mesh-0.{suffix}").read_bytes())
            return response
        self.client.call = two_models
        response = files.run_file_operation(self.client, "export_file")
        self.assertEqual(response["data"]["count"], 2, response)
        clothes = self.archive.with_name("clothes.qzskin")
        self.assertTrue(clothes.is_file())
        clothes.unlink()
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(len(response["data"]["errors"]), 1)
        self.assertIn("clothes", response["data"]["errors"][0]["error"])
        self.assertTrue(any("部分完成" in message for _, message in files.format_file_messages(response)))

    def test_closest_mode_reorders_by_position_without_changing_weight_bits(self):
        original = copy.deepcopy(self.maya.rows)
        self.export()
        self.geometry_points = [(1., 0., 0.), (0., 0., 0.)]
        self.maya.rows = [[0., 0., 1.]] * 2
        self.dq = [0., 0.]
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 1, response)
        np.testing.assert_array_equal(self.maya.rows, original[::-1])
        self.assertEqual(self.dq, [.75, .25])
        self.assertEqual(response["data"]["reports"][0]["direct_vertices"], 2)
        self.assertEqual(response["data"]["reports"][0]["interpolated_vertices"], 0)

    def test_closest_all_direct_clears_selection_and_reports_zero(self):
        self.export()
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertTrue(response["data"]["selection_changed"])
        self.assertEqual(response["data"]["selected_vertices"], 0)
        self.assertEqual(self.maya.selection, [])
        self.assertEqual(self.selection_sets, {"body_skinInterpolatedVertices_set": []})
        self.assertTrue(any("已选中 0 个表面插值顶点" in message
                            for _, message in files.format_file_messages(response)))

    def test_moved_point_projecting_to_source_vertex_is_still_selected(self):
        original = copy.deepcopy(self.maya.rows)
        self.export()
        self.geometry_points = [(0., .2, 0.), (1., 0., 0.)]
        om = sys.modules["maya.api.OpenMaya"]
        mesh_type = om.MFnMesh
        class Mesh(mesh_type):
            def __init__(self, path=None): super().__init__(path)
            def create(self, *args, **kwargs): return object()
        class Intersector:
            def create(self, *args): pass
            def getClosestPoint(self, point):
                return types.SimpleNamespace(face=0, barycentricCoords=(1., 0.))
        with patch.object(om, "MFnMesh", Mesh), \
                patch.object(om, "MFnMeshData", lambda: types.SimpleNamespace(create=object), create=True), \
                patch.object(om, "MPoint", lambda *xyz: xyz, create=True), \
                patch.object(om, "MMatrix", object, create=True), \
                patch.object(om, "MMeshIntersector", Intersector, create=True):
            response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertEqual(response["data"]["reports"][0]["direct_vertices"], 1)
        self.assertEqual(response["data"]["selected_vertices"], 1)
        self.assertEqual(self.maya.selection, ["|body|bodyShape.vtx[0]"])
        self.assertEqual(self.selection_sets["body_skinInterpolatedVertices_set"], self.maya.selection)
        self.assertEqual(response["data"]["reports"][0]["interpolation_set"], "body_skinInterpolatedVertices_set")
        self.assertEqual(self.maya.rows, original)
        self.assertEqual(self.dq, [.25, .75])
        self.assertTrue(any("已选中 1 个表面插值顶点" in message
                            for _, message in files.format_file_messages(response)))

    def test_failed_closest_import_restores_previous_selection(self):
        self.export()
        self.maya.fail_write = 1
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 0, response)
        self.assertFalse(response["data"]["selection_changed"])
        self.assertEqual(self.maya.selection, ["|body"])
        self.assertFalse(any("已选中" in message for _, message in files.format_file_messages(response)))

    def test_partial_success_combines_only_successful_mesh_interpolation_ids(self):
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-") as temp:
            directory = Path(temp)
            entries = [dict(index=i, name="mesh%d" % i, mesh="|mesh%d|shape" % i, vertices=8,
                            transfer_mode="closest_point", interpolated_vertices=3) for i in range(3)]
            for entry in entries:
                np.array([1, 2, 6], dtype="<u4").tofile(directory / ("mesh-%d.interpolated_ids" % entry["index"]))
            def write(cmds, directory, entry, groups):
                cmds.select(["binding-side-effect"], replace=True)
                if entry["index"] == 1:
                    raise ValueError("missing bone")
                return dict(index=entry["index"])
            with patch.object(action, "_import_one", side_effect=write):
                result = action.skin_weights_files(op="import", directory=temp, meshes=entries)
        self.assertEqual(len(result["reports"]), 2)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["selected_vertices"], 6)
        self.assertEqual(self.maya.selection, ["|mesh0|shape.vtx[1:2]", "|mesh0|shape.vtx[6]",
                                              "|mesh2|shape.vtx[1:2]", "|mesh2|shape.vtx[6]"])

    def test_invalid_interpolation_ids_rejected_before_weights_written(self):
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-") as temp:
            directory = Path(temp)
            entry = dict(index=0, mesh="|body|bodyShape", vertices=4,
                         transfer_mode="closest_point", interpolated_vertices=2)
            for ids in ([0], [0, 4], [2, 2], [2, 1]):
                with self.subTest(ids=ids):
                    np.array(ids, dtype="<u4").tofile(directory / "mesh-0.interpolated_ids")
                    with patch.object(action, "_import_one") as write:
                        result = action.skin_weights_files(op="import", directory=temp, meshes=[entry])
                    write.assert_not_called()
                    self.assertEqual(len(result["errors"]), 1)
                    self.assertFalse(result["selection_changed"])
                    self.assertEqual(self.maya.selection, ["|body"])

    def test_set_updates_after_rename_without_overwriting_unrelated_same_name_set(self):
        entry = dict(name="body", mesh="|body|bodyShape")
        base = "body_skinInterpolatedVertices_set"
        self.selection_sets[base] = ["user-owned-components"]
        owned = action._interpolated_set(self.cmds, entry, ["|body|bodyShape.vtx[0]"])
        self.assertNotEqual(owned, base)
        entry["name"] = "renamedBody"
        updated = action._interpolated_set(self.cmds, entry, ["|body|bodyShape.vtx[1]"])
        self.assertEqual(updated, owned)
        self.assertEqual(self.selection_sets[owned], ["|body|bodyShape.vtx[1]"])
        action._interpolated_set(self.cmds, entry, [])
        self.assertEqual(self.selection_sets[owned], [])
        self.assertEqual(self.selection_sets[base], ["user-owned-components"])
        self.assertEqual(len(self.selection_sets), 2)

    def test_failed_or_id_import_preserves_previous_interpolation_set(self):
        self.export()
        entry = dict(name="body", mesh="|body|bodyShape")
        node = action._interpolated_set(self.cmds, entry, ["|body|bodyShape.vtx[1]"])
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertFalse(response["data"]["selection_changed"])
        self.assertEqual(self.selection_sets[node], ["|body|bodyShape.vtx[1]"])
        self.maya.fail_write = self.maya.writes + 1
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 0, response)
        self.assertEqual(self.selection_sets[node], ["|body|bodyShape.vtx[1]"])

    def test_set_failure_reports_weights_imported_and_still_selects_vertices(self):
        self.export()
        with patch.object(action, "_interpolated_set", side_effect=RuntimeError("set locked")):
            response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertTrue(response["data"]["selection_changed"])
        self.assertIn("权重已导入", response["data"]["errors"][0]["error"])
        self.assertNotIn("interpolation_set", response["data"]["reports"][0])

    def test_legacy_files_load_by_id_but_closest_requires_geometry(self):
        self.export()
        def legacy(content):
            metadata = json.loads(content["manifest.json"])
            metadata["version"] = 2
            metadata.pop("geometry")
            content["manifest.json"] = json.dumps(metadata).encode()
            content.pop("points.bin")
            content.pop("triangles.bin")
        self.mutate_archive(legacy)
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        before = self.maya.writes
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 0, response)
        self.assertIn("重新导出", response["data"]["errors"][0]["error"])
        self.assertEqual(self.maya.writes, before)

    def test_id_mode_does_not_read_or_query_geometry(self):
        self.export()
        self.mutate_archive(lambda content: content.update({"points.bin": b"bad"}))
        response = files.run_file_operation(self.client, "import_file")
        self.assertEqual(response["data"]["count"], 1, response)
        self.assertFalse(any(params["op"] == "map" for _, params, _ in self.client.calls))
        before = self.maya.writes
        response = files.run_file_operation(self.client, "import_file", "closest_point")
        self.assertEqual(response["data"]["count"], 0)
        self.assertIn("几何数据长度", response["data"]["errors"][0]["error"])
        self.assertEqual(self.maya.writes, before)

    def test_geometry_export_preserves_full_coordinate_precision(self):
        self.geometry_points = [(.123456789123, 0., 0.), (1.876543210877, 0., 0.)]
        self.export()
        with zipfile.ZipFile(self.archive) as archive:
            actual = np.frombuffer(archive.read("points.bin"), dtype="<f8").reshape(-1,3)
        np.testing.assert_array_equal(actual, self.geometry_points)

    def test_qt_closest_mode_click_runs_mapping_and_restores_reordered_data(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_loader import scan
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        info = next(p for p in scan(str(ROOT / "plugins")) if p.key == ("maya", "skin_weights"))
        messages=[]
        class Log:
            def info(self,text): messages.append(text)
            warn=error=info
        self.export()
        rows=copy.deepcopy(self.maya.rows)
        self.geometry_points=[(1.,0.,0.),(0.,0.,0.)]
        self.maya.rows=[[0.,0.,1.]]*2
        widget=info.load_factory()(self.client,Log())
        widget.show()
        app.processEvents()
        self.assertEqual(widget.import_mode_cbox.currentData(),"vertex_id")
        QtTest.QTest.mouseClick(widget.import_mode_cbox,QtCore.Qt.LeftButton)
        QtTest.QTest.keyClick(widget.import_mode_cbox,QtCore.Qt.Key_Down)
        QtTest.QTest.keyClick(widget.import_mode_cbox,QtCore.Qt.Key_Return)
        self.assertEqual(widget.import_mode_cbox.currentData(),"closest_point")
        QtTest.QTest.mouseClick(widget.import_btn,QtCore.Qt.LeftButton)
        self.assertFalse(widget.import_mode_cbox.isEnabled())
        deadline=time.monotonic()+5
        while widget._task is not None and time.monotonic()<deadline:
            app.processEvents()
            QtTest.QTest.qWait(2)
        self.assertIsNone(widget._task)
        self.assertTrue(widget.import_mode_cbox.isEnabled())
        self.assertEqual(self.maya.rows,rows[::-1])
        self.assertTrue(any("按位置直接复制 2" in message for message in messages))
        self.assertTrue(any(params.get("position_decimals")==3 for _,params,_ in self.client.calls))
        widget.close()

    def test_qt_buttons_group_order_busy_state_and_ui_thread_logging(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_loader import scan
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        info = next(p for p in scan(str(ROOT / "plugins")) if p.key == ("maya", "skin_weights"))
        messages = []
        class Log:
            def info(self, text): messages.append(("info", text, threading.current_thread().name))
            warn = error = info
        widget = info.load_factory()(self.client, Log())
        widget.show()
        app.processEvents()
        self.assertEqual(widget.layout().indexOf(widget.fix_negative_btn.parent()), 1)
        self.assertIs(widget.export_btn.parent(), widget.import_btn.parent())
        self.assertEqual(widget.layout().indexOf(widget.export_btn.parent()), 2)
        self.assertEqual(widget.layout().indexOf(widget.send_btn.parent()), 3)
        widget.inf_lab.setText("4")
        for button in (widget.export_btn, widget.import_btn):
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.assertFalse(widget.send_btn.isEnabled())
            self.assertFalse(widget.export_btn.isEnabled())
            self.assertFalse(widget.import_btn.isEnabled())
            end = time.monotonic() + 5
            while widget._task is not None and time.monotonic() < end:
                app.processEvents()
                QtTest.QTest.qWait(2)
            self.assertIsNone(widget._task)
            self.assertTrue(widget.send_btn.isEnabled())
            if button is widget.export_btn:
                self.assertEqual(widget.inf_lab.text(), "4")
                self.maya.rows = [[0., 0., 1.]] * 2
            else:
                self.assertEqual(widget.inf_lab.text(), "需重新检测")
        self.assertEqual(self.maya.rows[0], [.123456789123, .876543210877, 0.])
        self.assertTrue(messages)
        self.assertTrue(all(thread == threading.current_thread().name for _, _, thread in messages))
        self.assertTrue(all(thread != threading.current_thread().name for _, _, thread in self.client.calls))
        self.assertFalse(widget.export_btn.toolTip())
        widget.close()


if __name__ == "__main__":
    unittest.main()
