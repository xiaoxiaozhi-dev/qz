"""NumPy 数值/二进制交换、真实 action + Maya API 替身、Qt 点击回调。"""

import copy
import importlib
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from ui.skin_weights import process_snapshot, run_operation, format_log_messages
from maya_actions import skin_weights as action


def snapshot(directory, rows):
    weights = np.asarray(rows, dtype="<f8")
    weights.tofile(Path(directory) / "mesh-0.weights")
    return [{"index": 0, "mesh": "|body|bodyShape", "vertices": len(rows),
             "influences": weights.shape[1]}]


class EncodingTests(unittest.TestCase):
    def test_float64_transport_preserves_bits_including_invalid_weights(self):
        import struct
        # 原始IEEE754位模式作为独立基准，包含负零、极小值、负值、非有限数与NaN负载。
        patterns = (0x0000000000000000, 0x8000000000000000,
                    0x0000000000000001, 0x3FD5555555555555,
                    0x7FEFFFFFFFFFFFFF, 0xBFF0000000000000,
                    0x7FF0000000000000, 0xFFF0000000000000,
                    0x7FF8000000001234, 0x7FF0000000001234)
        expected = b"".join(bits.to_bytes(8, "little") for bits in patterns)
        values = struct.unpack(f"<{len(patterns)}d", expected)
        for sequence in (values, list(values)):
            self.assertEqual(action._bytes(sequence), expected)
        self.assertEqual(action._bytes([]), b"")
        self.assertEqual(action._bytes([0, 4294967295], "I"), b"\x00" * 4 + b"\xff" * 4)

    def test_snapshot_encoding_across_full_and_final_partial_blocks(self):
        from array import array
        import io
        block = [0., .125, .5, .375] * (action.BLOCK_VALUES // 4)
        tail = [-0., 1e-300, float("inf"), float("nan")]
        expected = array("d", block + tail)
        if sys.byteorder != "little":
            expected.byteswap()
        file, timings = io.BytesIO(), {}
        for values in (block, tail):
            action._write_snapshot(file, values, timings)
        self.assertEqual(file.getvalue(), expected.tobytes())
        self.assertEqual(set(timings), {"maya_encode", "maya_file_write"})


class NumericTests(unittest.TestCase):
    def test_proportional_and_three_digit_sum(self):
        with tempfile.TemporaryDirectory() as directory:
            meshes = snapshot(directory, [[.5, .3, .2], [1/3, 1/3, 1/3], [.9, .1, 0]])
            reports, patches = process_snapshot(directory, meshes, "limit", 2, 0)
            self.assertEqual(patches[0]["count"], 2)
            self.assertEqual(reports[0]["max_after"], 2)
            self.assertEqual(np.fromfile(Path(directory) / "mesh-0.ids", "<u4").tolist(), [0, 1])
            values = np.fromfile(Path(directory) / "mesh-0.values", "<f8").reshape(2, 2)
            np.testing.assert_allclose(values, [[.625, .375], [.5, .5]], atol=1e-15)
            self.assertTrue(np.all(np.rint(values * 1000).sum(axis=1) == 1000))
            self.assertEqual(np.fromfile(Path(directory) / "mesh-0.cols", "<u4").tolist(), [0, 1, 0, 1])

    def test_rounding_remainder_ties_and_extreme_values(self):
        for mode in (0, 1):
            with tempfile.TemporaryDirectory() as directory:
                rows = [[.25]*4, [1e308]*4, [1e-310]*4, [.99999998, 1e-8, 1e-8, 0]]
                meshes = snapshot(directory, rows)
                reports, _ = process_snapshot(directory, meshes, "limit", 3, mode)
                values = np.fromfile(Path(directory) / "mesh-0.values", "<f8").reshape(-1, 3)
                np.testing.assert_array_equal(values[:3], [[.334, .333, .333]]*3)
                self.assertEqual(reports[0]["modified_vertices"], 3)
                self.assertEqual(reports[0]["max_after"], 3)

    def test_average_is_residual_and_handles_non_normalized_input(self):
        with tempfile.TemporaryDirectory() as directory:
            meshes = snapshot(directory, [[.5, .3, .2], [2., .5, .1]])
            process_snapshot(directory, meshes, "limit", 2, 1)
            values = np.fromfile(Path(directory) / "mesh-0.values", "<f8").reshape(2, 2)
            np.testing.assert_allclose(values[0], [.6, .4])
            self.assertTrue((values >= 0).all())
            self.assertTrue(np.all(np.rint(values * 1000).sum(axis=1) == 1000))

    def test_invalid_vertices_keep_original_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            meshes = snapshot(directory, [[0, 0, 0], [0, 0, 0], [-.1, .5, .6],
                                          [np.nan, .5, .5], [np.inf, 0, 1], [.5, .3, .2]])
            reports, _ = process_snapshot(directory, meshes, "limit", 2)
            self.assertEqual(reports[0]["invalid_vertices"], 5)
            self.assertEqual(reports[0]["issue_counts"], {"非有限数": 2, "负权重": 1, "全零": 2})
            self.assertEqual(np.fromfile(Path(directory) / "mesh-0.ids", "<u4").tolist(), [5])

    def test_tiny_weights_count_until_modified_and_k_one(self):
        with tempfile.TemporaryDirectory() as directory:
            meshes = snapshot(directory, [[.999999999, 1e-9, 0]])
            reports, _ = process_snapshot(directory, meshes, "check", 1)
            self.assertEqual(reports[0]["max_before"], 2)
            process_snapshot(directory, meshes, "limit", 1)
            self.assertEqual(np.fromfile(Path(directory) / "mesh-0.values", "<f8").tolist(), [1.])

    def test_random_rows_both_modes_multiple_blocks(self):
        rng = np.random.default_rng(5)
        rows = rng.random((70, 17))
        rows[rows < .3] = 0
        from ui import skin_weights as numeric
        old = numeric.BLOCK_VALUES
        numeric.BLOCK_VALUES = 85
        try:
            for mode in (0, 1):
                with tempfile.TemporaryDirectory() as directory:
                    meshes = snapshot(directory, rows)
                    reports, patches = process_snapshot(directory, meshes, "limit", 4, mode)
                    values = np.fromfile(Path(directory) / "mesh-0.values", "<f8").reshape(-1, 4)
                    self.assertEqual(patches[0]["count"], 70)
                    self.assertEqual(reports[0]["max_after"], 4)
                    self.assertTrue((values >= 0).all())
                    np.testing.assert_array_equal(np.rint(values * 1000).sum(axis=1), np.full(70, 1000))
        finally:
            numeric.BLOCK_VALUES = old

    def test_validation_and_bounded_anomaly_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            meshes = snapshot(directory, [[0., 0.]]*30)
            reports, _ = process_snapshot(directory, meshes, "check")
            self.assertEqual(reports[0]["invalid_vertices"], 30)
            self.assertEqual(len(reports[0]["issue_examples"]), 12)
            for threshold in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    process_snapshot(directory, meshes, "limit", threshold)
            (Path(directory) / "mesh-0.weights").write_bytes(b"bad")
            with self.assertRaises(ValueError):
                process_snapshot(directory, meshes, "check")


class FakeMaya:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows if rows is not None else [[.5, .3, .2], [.9, .1, 0], [0, 0, 0]])
        self.selection = ["|body"]
        self.settings = {"skin.normalizeWeights": 2, "skin.maintainMaxInfluences": True,
                         "|j0.liw": True, "|j1.liw": False, "|j2.liw": True}
        self.locks = {p: (p == "|j0.liw") for p in self.settings}
        self.writes = 0
        self.read_calls = []
        self.topology_reads = 0
        self.fail_write = None
        self.corrupt = False
        self.topology = [0, 1, 2]
        self.influence_names = ["|j0", "|j1", "|j2"]
        self.uuid_nodes = {'uuid-|body|bodyShape': '|body|bodyShape'}
        self.hammer_calls = []
        self.percent_calls = []
        self.undo_chunks = []

    def install(self):
        scene = self
        class Cmds:
            def ls(self, node=None, **kw):
                if kw.get("selection"):
                    return list(dict.fromkeys(n.split('.vtx[')[0] for n in scene.selection)) if kw.get('objectsOnly') else list(scene.selection)
                if kw.get("uuid"):
                    return ["uuid-" + node]
                return [scene.uuid_nodes[node]] if node in scene.uuid_nodes else []
            def nodeType(self, node):
                return "skinCluster" if node == "skin" else "mesh" if node.endswith("Shape") else "transform"
            def listRelatives(self, node, **kw):
                return ["|body|bodyShape"] if node == "|body" else []
            def listHistory(self, node, **kw):
                return ["skin"]
            def getAttr(self, plug, **kw):
                if plug.endswith(".intermediateObject"):
                    return False
                return scene.locks.get(plug, False) if kw.get("lock") else scene.settings[plug]
            def setAttr(self, plug, value=None, **kw):
                if "lock" in kw:
                    scene.locks[plug] = kw["lock"]
                else:
                    if scene.locks.get(plug):
                        raise RuntimeError("plug locked")
                    scene.settings[plug] = value
            def objExists(self, plug):
                return plug in scene.settings
            def select(self, items=(), **kw):
                scene.selection = list(items)
            def skinPercent(self, skin, component, **kw):
                assert skin == 'skin'
                assert kw['normalize'] is False
                assert not any(scene.settings.values()), '写入期间未关闭锁定/自动约束'
                vertex = int(component.rsplit('[', 1)[1][:-1])
                scene.percent_calls.append((component, kw))
                scene.writes += 1
                for name, value in kw['transformValue']:
                    scene.rows[vertex][scene.influence_names.index(name)] = value
                    if scene.fail_write == scene.writes:
                        raise RuntimeError('simulated partial write')
            def undoInfo(self, **kw):
                scene.undo_chunks.append(kw)
        class Dag:
            def __init__(self, name): self.name = name
            def fullPathName(self): return self.name
        class Selection:
            def add(self, name): self.name = name
            def getDagPath(self, index): return Dag(self.name)
            def getDependNode(self, index): return self.name
        class Component:
            def create(self, kind): self.ids = []; return self.ids
            def addElements(self, ids): self.ids.extend(ids)
        class MeshFn:
            def __init__(self, path): pass
            @property
            def numVertices(self): return len(scene.rows)
            def getVertices(self):
                scene.topology_reads += 1
                return [3], scene.topology
        class SkinFn:
            def __init__(self, node): pass
            def influenceObjects(self): return [Dag(n) for n in scene.influence_names]
            def getWeights(self, path, ids):
                scene.read_calls.append(list(ids))
                return [w for vertex in ids for w in scene.rows[vertex]], len(scene.influence_names)
            def setWeights(self, path, ids, columns, values, **kw):
                scene.writes += 1
                assert kw["normalize"] is False
                assert not any(scene.settings.values()), "写入期间未关闭锁定/自动约束"
                for row, vertex in enumerate(ids):
                    scene.rows[vertex] = list(values[row*3:(row+1)*3])
                    if scene.fail_write == scene.writes:
                        raise RuntimeError("simulated partial write")
                if scene.corrupt and scene.writes == 1:
                    scene.rows[ids[0]][0] = .12345
        cmds = Cmds()
        maya = types.ModuleType("maya")
        api = types.ModuleType("maya.api")
        om = types.ModuleType("maya.api.OpenMaya")
        oma = types.ModuleType("maya.api.OpenMayaAnim")
        om.MSelectionList, om.MFnMesh, om.MFnSingleIndexedComponent = Selection, MeshFn, Component
        om.MFn = types.SimpleNamespace(kMeshVertComponent=1)
        om.MIntArray = om.MDoubleArray = list
        oma.MFnSkinCluster = SkinFn
        maya.cmds, maya.api = cmds, api
        mel = types.ModuleType('maya.mel')
        def hammer(command):
            import re
            assert command == 'weightHammerVerts;'
            scene.hammer_calls.append(list(scene.selection))
            for component in scene.selection:
                match = re.search(r'\.vtx\[(\d+)(?::(\d+))?\]', component)
                for vertex in range(int(match[1]), int(match[2] or match[1]) + 1):
                    scene.rows[vertex] = [.5, .5, 0.]
        mel.eval = hammer
        maya.mel = mel
        api.OpenMaya, api.OpenMayaAnim = om, oma
        sys.modules.update({"maya": maya, "maya.cmds": cmds, "maya.mel": mel, "maya.api": api,
                            "maya.api.OpenMaya": om, "maya.api.OpenMayaAnim": oma})
        return self


class Client:
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate
    def call(self, name, timeout=10, **params):
        self.calls.append((name, params.copy(), threading.current_thread().name))
        try:
            if params.get("op") in ("limit", "select_excess") and self.mutate:
                self.mutate()
            return {"ok": True, "data": action.skin_weights(**params)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.saved = {n: sys.modules.get(n) for n in ("maya", "maya.cmds", "maya.mel", "maya.api",
                      "maya.api.OpenMaya", "maya.api.OpenMayaAnim")}
        self.scene = FakeMaya().install()
        action._JOBS.clear()
        action._ISSUE_JOBS.clear()
    def tearDown(self):
        action._JOBS.clear()
        action._ISSUE_JOBS.clear()
        for name, module in self.saved.items():
            if module is None: sys.modules.pop(name, None)
            else: sys.modules[name] = module

    def test_stage_timings_split_encoding_writing_reading_and_compute(self):
        from unittest.mock import patch
        clock = [100.0]
        actual_read, actual_encode = action._Mesh.read, action._bytes
        actual_fromfile, actual_count, actual_open = np.fromfile, np.count_nonzero, Path.open
        def maya_read(mesh, ids):
            clock[0] += .2
            return actual_read(mesh, ids)
        def encode(*args, **kwargs):
            clock[0] += .1
            return actual_encode(*args, **kwargs)
        class Writer:
            def __init__(self, file): self.file = file
            def write(self, payload):
                clock[0] += .2
                return self.file.write(payload)
            def close(self): self.file.close()
        def open_file(path, mode="r", *args, **kwargs):
            file = actual_open(path, mode, *args, **kwargs)
            return Writer(file) if mode == "wb" and path.suffix == ".weights" else file
        def fromfile(*args, **kwargs):
            clock[0] += .3
            return actual_fromfile(*args, **kwargs)
        def count(*args, **kwargs):
            clock[0] += .4
            return actual_count(*args, **kwargs)
        with patch("time.perf_counter", lambda: clock[0]), \
             patch.object(action._Mesh, "read", maya_read), \
             patch.object(action, "_bytes", encode), \
             patch.object(Path, "open", open_file), \
             patch.object(np, "fromfile", fromfile), \
             patch.object(np, "count_nonzero", count):
            data = run_operation(Client(), "check", 2)["data"]
        timings = data["timings_seconds"]
        for key, expected in (("maya_read", .2), ("maya_encode", .1), ("maya_file_write", .2),
                              ("host_file_read", .3), ("numpy_compute", .4)):
            self.assertAlmostEqual(timings[key], expected)
        self.assertAlmostEqual(data["elapsed_seconds"], 1.2)
        self.assertAlmostEqual(sum(timings.values()), 1.2)
        self.assertNotIn("file_exchange", timings)

    def test_timings_cover_normal_write_and_recovery_without_verification(self):
        result = run_operation(Client(), "limit", 2)["data"]
        for key in ("maya_read", "maya_encode", "maya_file_write", "host_file_read",
                    "host_file_write", "numpy_compute", "maya_match", "maya_write",
                    "bridge_wait", "local_other"):
            self.assertGreaterEqual(result["timings_seconds"][key], 0)
        self.assertNotIn("maya_verify", result["timings_seconds"])
        self.assertEqual(self.scene.read_calls, [[0, 1, 2]])
        self.assertFalse(any("Maya写后复查" in message for _, message in
                             format_log_messages({"ok": True, "data": result})))
        self.assertAlmostEqual(sum(result["timings_seconds"].values()), result["elapsed_seconds"], places=4)
        self.scene = FakeMaya().install()
        self.scene.fail_write = 1
        result = run_operation(Client(), "limit", 2)["data"]
        self.assertIn("recovery", result["timings_seconds"])
        messages = format_log_messages({"ok": True, "data": result})
        self.assertTrue(any("部分完成" in message for _, message in messages))
        self.assertTrue(any("修改 0 个顶点" in message for _, message in messages))
        self.assertFalse(any("3 → 2" in message for _, message in messages))
        self.assertTrue(any(level == "error" and "已恢复" in message for level, message in messages))

    def test_legacy_maya_timings_are_not_fabricated(self):
        class LegacyClient(Client):
            def call(self, *args, **kwargs):
                response = super().call(*args, **kwargs)
                if response.get("ok"):
                    response["data"].pop("timings_seconds", None)
                    response["data"].pop("action_seconds", None)
                return response
        data = run_operation(LegacyClient(), "check", 2)["data"]
        self.assertIn("maya_unprofiled", data["timings_seconds"])
        self.assertNotIn("maya_read", data["timings_seconds"])
        self.assertFalse(any("未分段" in message for _, message in format_log_messages({"ok": True, "data": data})))

    def test_empty_and_disconnected_operations_have_chinese_logs(self):
        self.scene.selection = []
        messages = format_log_messages(run_operation(Client(), "limit", 2))
        self.assertTrue(any("修改未执行" in message for _, message in messages))
        self.assertTrue(any("未选中" in message for _, message in messages))
        class Disconnected:
            def call(self, *args, **kwargs):
                raise RuntimeError("连接超时")
        response = run_operation(Disconnected(), "limit", 2)
        messages = format_log_messages(response)
        self.assertFalse(response["ok"])
        self.assertTrue(any(level == "error" and "修改失败" in message for level, message in messages))
        self.assertTrue(any("总耗时" in message for _, message in messages))

    def test_check_and_select_use_actual_snapshot_and_no_weight_write(self):
        client = Client()
        result = run_operation(client, "check", 2)["data"]
        self.assertEqual(result["max_influences"], 3)
        self.assertEqual(result["invalid_vertices"], 1)
        self.assertEqual(self.scene.writes, 0)
        result = run_operation(client, "select_excess", 2)["data"]
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.scene.selection, ["|body|bodyShape.vtx[0]"])
        self.assertEqual(self.scene.writes, 0)
        self.assertFalse(action._JOBS)

    def test_limit_ignores_influence_locks_restores_flags_and_keeps_untouched(self):
        old_settings, old_locks = self.scene.settings.copy(), self.scene.locks.copy()
        untouched = copy.deepcopy(self.scene.rows[1:])
        result = run_operation(Client(), "limit", 2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["count"], 1)
        self.assertEqual(result["data"]["max_influences"], 2)
        self.assertEqual(self.scene.rows[0], [.625, .375, 0.])
        self.assertEqual(self.scene.rows[1:], untouched)
        self.assertEqual(self.scene.settings, old_settings)
        self.assertEqual(self.scene.locks, old_locks)

    def test_problem_selection_retains_every_id_not_just_log_examples(self):
        from unittest.mock import patch
        self.scene = FakeMaya([[0, 0, 0]] * 30 + [[1, 0, 0], [-.1, .5, .6]]).install()
        with patch('ui.skin_weights.BLOCK_VALUES', 15):
            checked = run_operation(Client(), 'check')['data']
        token = checked['issue_token']
        self.assertEqual(len(checked['reports'][0]['issue_examples']), 12)
        self.assertFalse(action._JOBS)
        client = Client()
        self.scene.selection = ['|unrelated']
        selected = run_operation(client, 'select_issues', issue_token=token)
        self.assertTrue(selected['ok'], selected)
        self.assertEqual(selected['data']['count'], 31)
        self.assertEqual(self.scene.selection, ['|body|bodyShape.vtx[0:29]', '|body|bodyShape.vtx[31]'])
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.scene.hammer_calls)
        self.assertEqual(self.scene.writes, 0)

    def test_negative_weights_use_equal_redistribution_and_no_hammer(self):
        rows = [[-.000002, .95, .050002], [-.4, .1, 1.3], [-.2, 1.2, 0],
                [.5, .5, 0], [-.01, .51, .5]]
        self.scene = FakeMaya(rows).install()
        settings, locks = self.scene.settings.copy(), self.scene.locks.copy()
        token = run_operation(Client(), 'check')['data']['issue_token']
        self.scene.selection = ['|unrelated']
        self.scene.read_calls.clear()
        result = run_operation(Client(), 'fix_negative', issue_token=token)
        data = result['data']
        self.assertEqual((data['attempted'], data['count'], data['remaining']), (4, 4, 0))
        self.assertFalse(self.scene.hammer_calls)
        self.assertEqual(len(self.scene.percent_calls), 4)
        self.assertEqual(self.scene.read_calls, [[0, 1, 2, 4]])
        self.assertEqual(self.scene.selection, ['|unrelated'])
        self.assertEqual(self.scene.settings, settings)
        self.assertEqual(self.scene.locks, locks)
        np.testing.assert_allclose(self.scene.rows[:3], [[0, .949999, .050001], [0, 0, 1], [0, 1, 0]], atol=1e-15)
        for before, after in zip(rows, self.scene.rows):
            self.assertAlmostEqual(math.fsum(before), math.fsum(after), places=15)
            self.assertTrue(all(w >= 0 for w in after))
            self.assertTrue(all(w == 0 for old, w in zip(before, after) if old == 0))
        self.assertEqual(self.scene.rows[3], rows[3])
        self.assertNotIn(token, action._ISSUE_JOBS)
        self.assertTrue(any('修复 4 个' in text for _, text in format_log_messages(result)))

    def test_negative_skips_other_issues_then_separate_hammer_repairs_them(self):
        self.scene = FakeMaya([[-.01, .51, .5], [0, 0, 0], [float('nan'), 0, 1],
                               [float('inf'), 0, 1], [-.5, .1, 0], [-1, 1, 0]]).install()
        token = run_operation(Client(), 'check')['data']['issue_token']
        result = run_operation(Client(), 'fix_negative', issue_token=token)
        data = result['data']
        self.assertEqual((data['attempted'], data['count'], data['remaining']), (1, 1, 5))
        self.assertFalse(self.scene.hammer_calls)
        self.assertEqual(self.scene.selection, ['|body'])
        self.assertTrue(math.isnan(self.scene.rows[2][0]))
        self.assertTrue(math.isinf(self.scene.rows[3][0]))
        self.assertEqual(self.scene.rows[1], [0, 0, 0])
        self.assertTrue(any('可重新检测后使用锤式修复' in text for _, text in format_log_messages(result)))
        token = run_operation(Client(), 'check')['data']['issue_token']
        result = run_operation(Client(), 'hammer_issues', issue_token=token)
        self.assertEqual(result['data']['count'], 5)
        self.assertEqual(self.scene.hammer_calls, [['|body|bodyShape.vtx[1:5]']])

    def test_redistribution_numeric_invariants_and_no_magnitude_threshold(self):
        rng = np.random.default_rng(34)
        for size in (2, 3, 20, 163):
            for _ in range(100):
                positives = rng.random(size)
                positives /= positives.sum()
                values = [-float(rng.uniform(1e-10, .9))] + positives.tolist() + [0.]
                fixed = action._redistribute_negative(values)
                self.assertIsNotNone(fixed, values)
                self.assertGreaterEqual(min(fixed), 0.)
                self.assertEqual(fixed[-1], 0.)
                self.assertAlmostEqual(math.fsum(fixed), math.fsum(values), places=15)
                decrements = [a-b for a,b in zip(values, fixed) if b > 0]
                self.assertLess(max(decrements)-min(decrements), 2e-15)
        for values in ([0, 0], [float('nan'), 1], [float('inf'), 1], [-1, .1], [-1, 1], [-1, 0]):
            self.assertIsNone(action._redistribute_negative(values))
        fixed = action._redistribute_negative([-1e16, 1e16, 1.])
        self.assertEqual(fixed, [0., 1., 0.])

    def test_redistribution_failure_preserves_settings_and_reports_partial_result(self):
        self.scene = FakeMaya([[-.01, .51, .5], [-.1, .8, .3]]).install()
        settings, locks = self.scene.settings.copy(), self.scene.locks.copy()
        token = run_operation(Client(), 'check')['data']['issue_token']
        self.scene.fail_write = 2
        result = run_operation(Client(), 'fix_negative', issue_token=token)
        data = result['data']
        self.assertEqual((data['attempted'], data['count'], data['unverified']), (2, 1, 1))
        self.assertIn('部分修改', data['errors'][0]['error'])
        self.assertEqual(self.scene.settings, settings)
        self.assertEqual(self.scene.locks, locks)
        self.assertTrue(self.scene.undo_chunks[-1]['closeChunk'])
        self.assertNotIn(token, action._ISSUE_JOBS)

    def test_hammer_repairs_cached_problems_only_and_preserves_selection(self):
        self.scene = FakeMaya([[-.1, .5, .6], [.1, .2, .7], [0, 0, 0],
                               [float('nan'), 0, 1], [float('inf'), 0, 1]]).install()
        token = run_operation(Client(), 'check')['data']['issue_token']
        self.scene.rows[0] = [.9, .1, 0]  # 已手工修复，不再锤。
        self.scene.selection = ['|unrelated']
        original = copy.deepcopy(self.scene.rows[:2])
        result = run_operation(Client(), 'hammer_issues', issue_token=token)
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual((data['attempted'], data['count'], data['remaining']), (3, 3, 0))
        self.assertEqual(self.scene.rows[:2], original)
        self.assertEqual(self.scene.selection, ['|unrelated'])
        self.assertEqual(self.scene.hammer_calls, [['|body|bodyShape.vtx[2:4]']])
        self.assertEqual(len(self.scene.undo_chunks), 2)
        self.assertTrue(self.scene.undo_chunks[-1]['closeChunk'])
        self.assertNotIn(token, action._ISSUE_JOBS)
        self.assertTrue(any('修复 3 个' in message for _, message in format_log_messages(result)))

    def test_problem_targets_reject_topology_changes_deletion_and_expiry(self):
        for mutation in ('topology', 'deleted', 'expired'):
            self.scene = FakeMaya().install()
            token = run_operation(Client(), 'check')['data']['issue_token']
            if mutation == 'topology':
                self.scene.topology.reverse()
            elif mutation == 'deleted':
                self.scene.uuid_nodes.clear()
            else:
                action._ISSUE_JOBS[token]['created'] -= 1801
            self.scene.selection = ['|unrelated']
            response = run_operation(Client(), 'hammer_issues', issue_token=token)
            self.assertTrue(not response['ok'] or response['data']['errors'], response)
            self.assertFalse(self.scene.hammer_calls)
            self.assertEqual(self.scene.selection, ['|unrelated'])

    def test_hammer_reports_unresolved_vertices_and_closes_chunk_after_failure(self):
        from unittest.mock import patch
        for fail in (False, True):
            self.scene = FakeMaya().install()
            token = run_operation(Client(), 'check')['data']['issue_token']
            with patch('maya.mel.eval', side_effect=RuntimeError('native hammer error') if fail else None):
                response = run_operation(Client(), 'hammer_issues', issue_token=token)
            data = response['data']
            self.assertEqual(data['count'], 0)
            self.assertEqual(data['attempted'], 1)
            if fail:
                self.assertIn('部分修改', data['errors'][0]['error'])
            else:
                self.assertEqual(data['remaining'], 1)
                self.assertTrue(any('仍异常 1 个' in text for _, text in format_log_messages(response)))
            self.assertTrue(self.scene.undo_chunks[-1]['closeChunk'])
            self.assertNotIn(token, action._ISSUE_JOBS)

    def test_snapshot_overwrites_changed_weights_without_topology_comparison(self):
        for change in (lambda: self.scene.rows[0].__setitem__(0, .51),
                       lambda: self.scene.topology.reverse()):
            self.scene = FakeMaya().install()
            data = run_operation(Client(change), "limit", 2)["data"]
            self.assertEqual(data["count"], 1)
            self.assertFalse(data["errors"])
            self.assertEqual(self.scene.rows[0], [.625, .375, 0.])
            # 正常成功写入只读一次初始快照，不再写前或写后重读。
            self.assertEqual(self.scene.read_calls, [[0, 1, 2]])
            self.assertEqual(self.scene.topology_reads, 0)
            self.assertNotIn("maya_validate", data["timings_seconds"])

    def test_selection_uses_original_target_without_full_weight_reread(self):
        def change_selection_and_weights():
            self.scene.selection = ["|anotherObject"]
            self.scene.rows[0] = [.9, .1, 0.]
        data = run_operation(Client(change_selection_and_weights), "select_excess", 2)["data"]
        self.assertEqual(data["count"], 1)
        self.assertFalse(data["errors"])
        self.assertEqual(self.scene.selection, ["|body|bodyShape.vtx[0]"])
        self.assertEqual(self.scene.read_calls, [[0, 1, 2]])
        self.assertEqual(self.scene.writes, 0)

    def test_debug_verification_only_rereads_modified_vertices(self):
        from unittest.mock import patch
        with patch.object(action, "VERIFY_AFTER_WRITE", True):
            data = run_operation(Client(), "limit", 2)["data"]
        self.assertFalse(data["errors"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(self.scene.read_calls, [[0, 1, 2], [0]])
        self.assertIn("maya_verify", data["timings_seconds"])
        self.assertFalse(any("Maya写后复查" in message for _, message in
                            format_log_messages({"ok": True, "data": data})))

    def test_partial_failure_and_debug_verification_failure_restore_original(self):
        from unittest.mock import patch
        for kind in ("partial", "verify"):
            self.scene = FakeMaya([[.5, .3, .2], [.4, .3, .3], [0, 0, 0]]).install()
            original = copy.deepcopy(self.scene.rows)
            settings, locks = self.scene.settings.copy(), self.scene.locks.copy()
            if kind == "partial": self.scene.fail_write = 1
            else: self.scene.corrupt = True
            with patch.object(action, "VERIFY_AFTER_WRITE", kind == "verify"):
                result = run_operation(Client(), "limit", 2)["data"]
            self.assertEqual(result["count"], 0)
            self.assertIn("已恢复", result["errors"][0]["error"])
            self.assertEqual(self.scene.rows, original)
            self.assertEqual(self.scene.settings, settings)
            self.assertEqual(self.scene.locks, locks)

    def test_failure_in_later_batch_restores_earlier_batches(self):
        self.scene = FakeMaya([[.5, .3, .2]]*7).install()
        original = copy.deepcopy(self.scene.rows)
        settings, locks = self.scene.settings.copy(), self.scene.locks.copy()
        self.scene.fail_write = 2
        old = action.BLOCK_VALUES
        action.BLOCK_VALUES = 6
        try:
            result = run_operation(Client(), "limit", 2)["data"]
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["reports"][0]["modified_vertices"], 0)
            self.assertIsNone(result["reports"][0]["max_after"])
            self.assertEqual(self.scene.rows, original)
            self.assertEqual(self.scene.settings, settings)
            self.assertEqual(self.scene.locks, locks)
        finally:
            action.BLOCK_VALUES = old

    def test_bad_patch_rejected_before_scene_write(self):
        with tempfile.TemporaryDirectory(prefix="qz-skin-") as directory:
            data = action.skin_weights(op="snapshot", directory=directory)
            _, patches = process_snapshot(directory, data["meshes"], "limit", 2)
            (Path(directory)/"mesh-0.values").write_bytes(b"bad")
            result = action.skin_weights(op="limit", token=data["token"], patches=patches, threshold=2)
            self.assertEqual(result["count"], 0)
            self.assertTrue(result["errors"])
            self.assertEqual(self.scene.writes, 0)
        with tempfile.TemporaryDirectory(prefix="qz-skin-") as directory:
            data = action.skin_weights(op="snapshot", directory=directory)
            with self.assertRaises(ValueError):
                action.skin_weights(op="limit", token=data["token"], patches=[])
            self.assertEqual(self.scene.writes, 0)

    def test_multiple_batches_and_compact_json_response(self):
        self.scene = FakeMaya([[.5, .3, .2]]*7).install()
        old = action.BLOCK_VALUES
        action.BLOCK_VALUES = 6
        try:
            result = run_operation(Client(), "limit", 2)
            self.assertEqual(result["data"]["count"], 7)
            self.assertEqual(self.scene.writes, 4)
            import json
            self.assertLess(len(json.dumps(result)), 3000)
        finally:
            action.BLOCK_VALUES = old

    def test_no_selection_and_unknown_task_report(self):
        self.scene.selection = []
        result = run_operation(Client(), "check")["data"]
        self.assertEqual(result["count"], 0)
        self.assertIn("warning", result)
        with self.assertRaises(ValueError):
            action.skin_weights(op="limit", token="missing")

    def test_widget_real_clicks_worker_parameters_and_ui_log_thread(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_loader import scan
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        info = next(p for p in scan(str(HERE / "plugins")) if p.key == ("maya", "skin_weights"))
        self.assertEqual(info.name, "蒙皮权重")
        log_threads, log_messages = [], []
        class Log:
            def info(self, message):
                log_threads.append(threading.current_thread().name)
                log_messages.append(message)
            def warn(self, message): self.info(message)
            def error(self, message): self.info(message)
        client = Client()
        widget = info.load_factory()(client, Log())
        widget.show()
        self.assertFalse(widget.select_issues_btn.isEnabled())
        self.assertFalse(widget.hammer_issues_btn.isEnabled())
        self.assertFalse(widget.fix_negative_btn.isEnabled())
        group = widget.fix_negative_btn.parentWidget()
        self.assertIsInstance(group, QtWidgets.QGroupBox)
        self.assertIs(widget.select_issues_btn.parentWidget(), group)
        self.assertIs(widget.hammer_issues_btn.parentWidget(), group)
        self.assertIsNot(widget.check_btn.parentWidget(), group)
        self.assertEqual([group.layout().itemAt(i).widget().text() for i in range(3)],
                         ['选中问题顶点', '修复负权重顶点', '锤式修复问题顶点'])
        self.assertEqual(widget.max_inf_sbox.value(), 4)
        widget.max_inf_sbox.setValue(2)
        def click(button):
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.assertFalse(widget.check_btn.isEnabled())
            deadline = time.monotonic() + 10
            while widget._task is not None and time.monotonic() < deadline:
                app.processEvents()
                QtTest.QTest.qWait(2)
            self.assertIsNone(widget._task)
            self.assertTrue(widget.check_btn.isEnabled())
        click(widget.check_btn)
        self.assertEqual(widget.inf_lab.text(), "3（异常 1）")
        self.assertTrue(widget.select_issues_btn.isEnabled())
        self.assertTrue(widget.hammer_issues_btn.isEnabled())
        self.scene.selection = ['|unrelated']
        click(widget.select_issues_btn)
        self.assertEqual(self.scene.selection, ['|body|bodyShape.vtx[2]'])
        self.assertEqual(widget.inf_lab.text(), "3（异常 1）")
        self.scene.selection = ['|body']
        click(widget.select_btn)
        self.scene.selection = ["|body"]
        widget.mode_cbox.setCurrentIndex(1)
        click(widget.fix_btn)
        self.assertFalse(widget.hammer_issues_btn.isEnabled())
        self.assertEqual(self.scene.rows[0], [.6, .4, 0.])
        self.assertEqual(widget.inf_lab.text(), "2（异常 1）")
        self.assertTrue(any("检测完成：处理 1 个模型" in message for message in log_messages))
        self.assertTrue(any("选择完成" in message and "选中 1 个" in message for message in log_messages))
        self.assertTrue(any("修改完成" in message and "3 → 2" in message for message in log_messages))
        self.assertTrue(any("总耗时" in message for message in log_messages))
        self.assertTrue(any("bodyShape.vtx[2]：全零" in message for message in log_messages))
        self.assertFalse(any(label in message for message in log_messages for label in
                             ("分阶段", "NumPy处理", "Maya编码", "Maya写文件", "中台读文件", "中台写结果")))
        self.assertFalse(any("Maya写前校验" in message or "交换文件读写" in message for message in log_messages))
        self.assertFalse(any("max_influences" in message or "timings_seconds" in message for message in log_messages))
        self.assertTrue(all(name == "MainThread" for name in log_threads))
        self.assertTrue(all(thread != "MainThread" for _, _, thread in client.calls))
        self.scene.selection = ['|body']
        click(widget.check_btn)
        click(widget.hammer_issues_btn)
        self.assertEqual(widget.inf_lab.text(), '需重新检测')
        self.assertFalse(widget.select_issues_btn.isEnabled())
        self.assertEqual(self.scene.rows[2], [.5, .5, 0.])
        self.scene = FakeMaya([[-.000002, .95, .050002], [.5, .5, 0], [0, 0, 0]]).install()
        click(widget.check_btn)
        self.assertTrue(widget.fix_negative_btn.isEnabled())
        click(widget.fix_negative_btn)
        self.assertFalse(widget.fix_negative_btn.isEnabled())
        self.assertFalse(widget.hammer_issues_btn.isEnabled())
        self.assertFalse(self.scene.hammer_calls)
        np.testing.assert_allclose(self.scene.rows[0], [0, .949999, .050001], atol=1e-15)
        self.assertEqual(self.scene.rows[2], [0, 0, 0])
        self.assertEqual(client.calls[-1][1]['op'], 'fix_negative')
        self.assertFalse(any(b.toolTip() for b in widget.findChildren(QtWidgets.QPushButton)))
        widget.close()
        widget.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    result = unittest.main(exit=False)
    print(f"PASS {result.result.testsRun - len(result.result.failures) - len(result.result.errors)} / {result.result.testsRun}")
    sys.exit(0 if result.result.wasSuccessful() else 1)
