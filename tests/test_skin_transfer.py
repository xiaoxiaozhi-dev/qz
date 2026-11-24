"""Full source transfer pipeline with Maya/Max API doubles and real Qt clicks."""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from dcc_bridge import DccClient
from maya_actions import skin_transfer as maya_action
from max_actions import skin_transfer as max_action
from ui.skin_transfer import prepare_transfer, run_transfer, format_transfer_messages
from test_skin_weights import FakeMaya


class Node:
    def __init__(self, name, vertices=0):
        self.name, self.vertices, self.modifiers = name, vertices, []


class Skin:
    def __init__(self, owner=None, bones=()):
        self.owner, self.bones = owner, list(bones)
        self.bone_limit = 20
        self.weights = [] if owner is None else [{1: 1.}] * owner.vertices
        self.dq = [] if owner is None else [0.] * owner.vertices
        self.unnormalized = [] if owner is None else [False] * owner.vertices
        self.selected, self.selected_bone = [], 1
        self.bulk_initialized = False
        self.modified = [] if owner is None else [True] * owner.vertices
        self.weight_tool_open, self.weight_table_open = False, False
    @property
    def enableDQ(self):
        raise AssertionError("Must not inspect or switch DQ mode")


class FakeMax:
    Skin = Skin

    def __init__(self, with_skin=True):
        self.model = Node("Body Mesh", 2)
        self.left, self.right = Node("Left Arm"), Node("Right Arm")
        self.objects = [self.model, self.left, self.right]
        self.selection, self.writes, self.adds = [], 0, []
        self.selection_calls, self.panel_calls = 0, 0
        self.panel_mode, self.current_modifier = "create", None
        self.redraw_depth, self.created, self.fail_write = 0, 0, None
        self.bulk_calls, self.fail_bulk, self.fail_restore = 0, False, False
        self.seed_calls, self.fail_seed = [], False
        self.window_closes, self.fail_close_tool = [], False
        if with_skin:
            self.model.modifiers = [Skin(self.model, [self.right])]
        scene = self
        class Ops:
            def isWeightToolOpen(self, skin): return skin.weight_tool_open
            def isWeightTableOpen(self, skin): return skin.weight_table_open
            def closeWeightTool(self, skin):
                if scene.fail_close_tool:
                    raise RuntimeError("simulated Weight Tool close failure")
                skin.weight_tool_open = False
                scene.window_closes.append("tool")
            def closeWeightTable(self, skin):
                skin.weight_table_open = False
                scene.window_closes.append("table")
            def GetNumberBones(self, skin): return len(skin.bones)
            def GetBoneName(self, skin, index, flag): return skin.bones[index - 1].name
            def GetNumberVertices(self, skin): return skin.owner.vertices
            def GetVertexWeightCount(self, skin, vertex): return len(skin.weights[vertex - 1])
            def GetVertexWeightBoneID(self, skin, vertex, slot): return list(skin.weights[vertex - 1])[slot - 1]
            def isUnNormalizeVertex(self, skin, vertex): return skin.unnormalized[vertex - 1]
            def IsVertexModified(self, skin, vertex): return int(skin.modified[vertex - 1])
            def unNormalizeVertex(self, skin, vertex, value):
                if not value and scene.fail_restore:
                    raise RuntimeError("simulated normalize restore failure")
                skin.unnormalized[vertex - 1] = value
                row = skin.weights[vertex - 1]
                if not value and sum(row.values()) > 0:
                    total = sum(row.values())
                    skin.weights[vertex - 1] = {bone: weight / total for bone, weight in row.items()}
            def SetBoneWeights(self, skin, bone, vertices, weights):
                assert not skin.weight_tool_open and not skin.weight_table_open
                scene.bulk_calls += 1
                for vertex, value in zip(vertices, weights):
                    self_row = skin.weights[vertex - 1]
                    if bone not in self_row and not skin.bulk_initialized:
                        raise RuntimeError("missing influence requires stable initialization")
                    self_row[bone] = value
                if scene.fail_bulk:
                    raise RuntimeError("simulated bulk write failure")
            def getSelectedVertices(self, skin): return list(skin.selected)
            def GetSelectedBone(self, skin): return skin.selected_bone
            def selectVertices(self, skin, vertices): skin.selected = list(vertices)
            def selectBone(self, skin, bone): skin.selected_bone = bone
            def SetWeight(self, skin, value):
                scene.seed_calls.append((skin.selected_bone, value))
                for vertex in skin.selected:
                    row = skin.weights[vertex - 1]
                    if not skin.unnormalized[vertex - 1]:
                        total = sum(w for b, w in row.items() if b != skin.selected_bone)
                        row = {b: w * (1. - value) / total if total else 0.
                               for b, w in row.items() if b != skin.selected_bone}
                    row[skin.selected_bone] = value
                    skin.weights[vertex - 1] = row
                    skin.modified[vertex - 1] = True
                skin.bulk_initialized = True
                if scene.fail_seed:
                    raise RuntimeError("simulated initialization failure")
            def addBone(self, skin, node, update):
                assert not skin.weight_tool_open and not skin.weight_table_open
                scene.adds.append((node.name, update))
                skin.bones.append(node)
                if len(skin.bones) == 1:
                    skin.weights = [row or {1: 1.} for row in skin.weights]
            def ReplaceVertexWeights(self, skin, vertex, ids, weights):
                assert not skin.weight_tool_open and not skin.weight_table_open
                scene.writes += 1
                skin.weights[vertex - 1] = dict(zip(ids, weights))
                skin.modified[vertex - 1] = True
                if scene.fail_write == scene.writes:
                    raise RuntimeError("simulated partial Max write")
            def setVertexDQWeight(self, skin, vertex, value): skin.dq[vertex - 1] = value
        self.skinOps = Ops()
        def set_current(skin, **kwargs):
            self.panel_calls += 1
            self.current_modifier = skin
        self.modPanel = types.SimpleNamespace(setCurrentObject=set_current,
                                             getCurrentObject=lambda: self.current_modifier)
    def classOf(self, modifier): return type(modifier)
    def getNumVerts(self, node): return node.vertices
    def select(self, node):
        self.selection_calls += 1
        self.selection = node if isinstance(node, list) else [node]
    def setCommandPanelTaskMode(self, mode): self.panel_mode = mode
    def getCommandPanelTaskMode(self): return self.panel_mode
    def Name(self, name): return name
    def maxVersion(self): return (22000, 0, 0)
    def Array(self, *values): return list(values)
    def addModifier(self, node, skin):
        self.created += 1
        skin.owner = node
        skin.weights, skin.dq = [{} for _ in range(node.vertices)], [0.] * node.vertices
        skin.unnormalized = [False] * node.vertices
        skin.modified = [False] * node.vertices
        node.modifiers.append(skin)
    def deleteModifier(self, node, skin): node.modifiers.remove(skin)
    def disableSceneRedraw(self): self.redraw_depth += 1
    def enableSceneRedraw(self): self.redraw_depth -= 1
    def redrawViews(self): pass


class MayaClient:
    connected = True
    def __init__(self): self.calls = []
    def call(self, action, timeout=10, **params):
        self.calls.append((action, params.copy(), threading.current_thread().name))
        try:
            return dict(ok=True, data=maya_action.export_max(**params))
        except Exception as exc:
            return dict(ok=False, error=str(exc))


class MaxClient:
    connected = True
    def __init__(self): self.calls = []
    def call(self, action, timeout=10, connect_timeout=None, execution_timeout=None, **params):
        self.calls.append((action, params.copy(), (timeout, connect_timeout, execution_timeout),
                           threading.current_thread().name))
        try:
            return dict(ok=True, data=max_action.import_max(**params))
        except Exception as exc:
            return dict(ok=False, error=str(exc))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.modules = {name: sys.modules.get(name) for name in
                        ("maya", "maya.cmds", "maya.api", "maya.api.OpenMaya", "maya.api.OpenMayaAnim", "pymxs")}
        self.maya = FakeMaya([[.5, .5, 0.], [.3, .7, 0.]]).install()
        self.maya.influence_names = ["|rig|LeftFBXASC032Arm", "|rig|RightFBXASC032Arm", "|unusedJoint"]
        self.dq = [.2, .8]
        self.maya.settings["skin.skinningMethod"] = 2
        self.maya.cmds = sys.modules["maya.cmds"]
        original_relatives = self.maya.cmds.listRelatives
        self.maya.cmds.listRelatives = lambda node, **kw: (
            ["|group|BodyFBXASC032Mesh"] if kw.get("parent") else original_relatives(node, **kw))
        skin_class = sys.modules["maya.api.OpenMayaAnim"].MFnSkinCluster
        skin_class.getBlendWeights = lambda fn, path, ids: [self.dq[index] for index in ids]
        self.max = FakeMax()
        sys.modules["pymxs"] = types.SimpleNamespace(runtime=self.max)
        self.maya_client, self.max_client = MayaClient(), MaxClient()
    def tearDown(self):
        for name, module in self.modules.items():
            if module is None: sys.modules.pop(name, None)
            else: sys.modules[name] = module

    def test_full_transfer_decodes_names_adds_scene_bone_and_preserves_all_weights_and_dq(self):
        original = copy.deepcopy(self.maya.rows)
        response = run_transfer(self.maya_client, self.max_client, 1)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["count"], 1)
        self.assertEqual(response["data"]["vertices"], 2)
        self.assertFalse(response["data"]["errors"])
        skin = self.max.model.modifiers[0]
        # Right already occupies Max ID 1; newly added Left becomes ID 2.
        self.assertEqual(skin.weights, [{2: .5, 1: .5}, {2: .3, 1: .7}])
        self.assertEqual(skin.dq, self.dq)
        self.assertEqual(skin.bone_limit, 1)  # Does not prune sender data first.
        self.assertEqual(self.max.adds, [("Left Arm", 1)])
        self.assertEqual(self.max.selection, [self.max.model])
        self.assertEqual(self.maya.rows, original)
        self.assertEqual(self.maya.writes, 0)
        self.assertEqual(self.max.redraw_depth, 0)
        self.assertFalse(Path(self.max_client.calls[0][1]["directory"]).exists())
        self.assertEqual(self.max_client.calls[0][2], (305, 1, 300))

    def test_new_skin_created_when_scene_bones_exist(self):
        self.max.model.modifiers = []
        data = run_transfer(self.maya_client, self.max_client, 6)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(self.max.created, 1)
        skin = self.max.model.modifiers[0]
        self.assertEqual([bone.name for bone in skin.bones], ["Left Arm", "Right Arm"])
        self.assertEqual(self.max.adds, [("Left Arm", 0), ("Right Arm", 1)])
        self.assertEqual(skin.dq, self.dq)
        self.assertEqual(skin.bone_limit, 6)

    def test_send_closes_weight_tool_and_table_before_adding_or_writing(self):
        skin = self.max.model.modifiers[0]
        skin.weight_tool_open = skin.weight_table_open = True
        response = run_transfer(self.maya_client, self.max_client)
        self.assertTrue(response['ok'], response)
        self.assertEqual(response['data']['count'], 1, response)
        self.assertEqual(self.max.window_closes, ['tool', 'table'])
        self.assertFalse(skin.weight_tool_open or skin.weight_table_open)
        self.assertEqual(skin.weights, [{2: .5, 1: .5}, {2: .3, 1: .7}])
        self.assertEqual(skin.dq, self.dq)

    def test_send_closes_editors_for_existing_skin_bulk_path_too(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        skin.weight_tool_open = skin.weight_table_open = True
        self.maya.rows = [[.2, .8, 0.], [.7, .3, 0.]]
        with patch.object(max_action, '_BULK_MIN_VERTICES', 0):
            response = run_transfer(self.maya_client, self.max_client)
        self.assertEqual(response['data']['count'], 1, response)
        self.assertEqual(response['data']['reports'][0]['write_mode'], 'bone_groups')
        self.assertEqual(self.max.window_closes, ['tool', 'table'])
        self.assertFalse(skin.weight_tool_open or skin.weight_table_open)
        self.assertEqual(skin.weights, [{2: .2, 1: .8}, {2: .7, 1: .3}])

    def test_send_close_failure_stops_before_mutating_skin(self):
        skin = self.max.model.modifiers[0]
        before = copy.deepcopy(skin.weights)
        skin.weight_tool_open, self.max.fail_close_tool = True, True
        response = run_transfer(self.maya_client, self.max_client)
        self.assertEqual(response['data']['count'], 0, response)
        self.assertIn('Weight Tool close failure', response['data']['errors'][0]['error'])
        self.assertEqual(skin.weights, before)
        self.assertFalse(self.max.adds)
        self.assertEqual(self.max.writes, 0)
        self.assertEqual(self.max.redraw_depth, 0)

    def test_existing_skin_bulk_updates_weights_and_dq_and_restores_vertex_flags(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        skin.unnormalized = [False, True]
        before = self.max.writes
        self.maya.rows = [[.2, .8, 0.], [.7, .3, 0.]]
        self.dq[:] = [.9, .1]
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "bone_groups")
        self.assertEqual(self.max.writes, before)
        self.assertEqual(self.max.bulk_calls, 2)
        self.assertEqual(skin.weights, [{2: .2, 1: .8}, {2: .7, 1: .3}])
        self.assertEqual(skin.dq, self.dq)
        self.assertEqual(skin.unnormalized, [False, True])

    def test_bulk_zeros_old_influences_without_leaving_positive_old_weights(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        self.maya.rows = [[1., 0., 0.], [0., 1., 0.]]
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(skin.weights, [{2: 1., 1: 0.}, {2: 0., 1: 1.}])

    def test_missing_vertex_influence_is_initialized_then_bulk_written(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        skin.weights[1] = {1: 1.}
        skin.selected, skin.selected_bone = [2], 2
        before = self.max.writes
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "bone_groups")
        self.assertEqual(self.max.writes, before)
        self.assertEqual(self.max.seed_calls, [(1, 1.), (2, .5)])
        self.assertEqual(skin.weights, [{1: .5, 2: .5}, {1: .7, 2: .3}])
        self.assertEqual(skin.selected, [2])
        self.assertEqual(skin.selected_bone, 2)
        self.assertEqual(skin.unnormalized, [False, False])

    def test_new_skin_and_added_bones_use_initialized_bulk_writer(self):
        for fresh in (True, False):
            self.max = FakeMax(with_skin=not fresh)
            sys.modules["pymxs"].runtime = self.max
            with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
                data = run_transfer(self.maya_client, self.max_client, 1)["data"]
            self.assertEqual(data["count"], 1, data)
            self.assertEqual(data["reports"][0]["write_mode"], "bone_groups")
            self.assertEqual(self.max.writes, 0)
            self.assertEqual(self.max.seed_calls, [(1, 1.), (2, .5)])
            skin = self.max.model.modifiers[0]
            expected = [{1: .5, 2: .5}, {1: .3, 2: .7}] if fresh else [{2: .5, 1: .5}, {2: .3, 1: .7}]
            self.assertEqual(skin.weights, expected)
            self.assertEqual(skin.dq, self.dq)
            self.assertEqual(skin.bone_limit, 1)

    def test_initialization_failure_restores_selection_without_retry(self):
        skin = self.max.model.modifiers[0]
        skin.selected, skin.selected_bone = [2], 1
        self.max.fail_seed = True
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0, data)
        self.assertIn("initialization failure", data["errors"][0]["error"])
        self.assertEqual(skin.selected, [2])
        self.assertEqual(skin.selected_bone, 1)
        self.assertEqual(self.max.writes, 0)
        self.assertEqual(self.max.bulk_calls, 0)
        self.assertEqual(skin.bone_limit, 20)

    def test_initialized_bulk_clears_temporary_bones_for_single_influence_rows(self):
        self.max.model.modifiers = []
        self.maya.rows = [[1., 0., 0.], [0., 1., 0.]]
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client, 1)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "bone_groups")
        rows = self.max.model.modifiers[0].weights
        self.assertEqual([{b: w for b, w in row.items() if w > 0} for row in rows],
                         [{1: 1.}, {2: 1.}])

    def test_failed_initialization_removes_new_skin(self):
        self.max.model.modifiers = []
        self.max.fail_seed = True
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0, data)
        self.assertFalse(self.max.model.modifiers)
        self.assertIn("新建的 Skin 已移除", data["errors"][0]["error"])

    def test_new_skin_rejects_bulk_source_before_initializing(self):
        self.max.model.modifiers = []
        self.maya.rows = [[.2, .2, 0.], [.6, .3, 0.]]
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "vertex")
        self.assertFalse(self.max.seed_calls)

    def test_envelope_controlled_vertices_are_initialized_even_with_all_source_ids(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        skin.modified[1] = False
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "bone_groups")
        self.assertEqual(self.max.seed_calls, [(1, 1.), (2, .5)])
        self.assertEqual(skin.weights, [{1: .5, 2: .5}, {1: .7, 2: .3}])

    def test_empty_target_or_large_union_uses_original_writer_without_initialization(self):
        run_transfer(self.maya_client, self.max_client)
        skin = self.max.model.modifiers[0]
        skin.weights[1] = {}
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["reports"][0]["write_mode"], "vertex")
        self.assertFalse(self.max.seed_calls)
        # Four source entries fit, but their union with the old third bone does not.
        self.max.objects.append(Node("unusedOldBone"))
        skin.bones.append(self.max.objects[-1])
        skin.weights[0][3] = .1
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0), patch.object(max_action, "_BULK_MAX_ENTRIES", 4):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["reports"][0]["write_mode"], "vertex")
        self.assertFalse(self.max.seed_calls)

    def test_bulk_write_failure_restores_flags_and_does_not_retry_with_vertex_writer(self):
        run_transfer(self.maya_client, self.max_client)
        self.max.fail_bulk = True
        before = self.max.writes
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0, data)
        self.assertIn("bulk write failure", data["errors"][0]["error"])
        self.assertIn("可能部分更新", data["errors"][0]["error"])
        self.assertEqual(self.max.writes, before)
        self.assertEqual(self.max.model.modifiers[0].unnormalized, [False, False])
        self.assertEqual(self.max.redraw_depth, 0)

    def test_bulk_restore_failure_is_reported_as_failure(self):
        run_transfer(self.maya_client, self.max_client)
        self.max.fail_restore = True
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0, data)
        self.assertIn("恢复顶点归一化设置失败", data["errors"][0]["error"])

    def test_bulk_cap_or_other_max_version_keeps_original_writer(self):
        run_transfer(self.maya_client, self.max_client)
        for setting in (patch.object(max_action, "_BULK_MAX_ENTRIES", 1),
                        patch.object(self.max, "maxVersion", return_value=(23000, 0, 0))):
            with patch.object(max_action, "_BULK_MIN_VERTICES", 0), setting:
                data = run_transfer(self.maya_client, self.max_client)["data"]
            self.assertEqual(data["count"], 1, data)
            self.assertEqual(data["reports"][0]["write_mode"], "vertex")
        self.assertEqual(self.max.bulk_calls, 0)

    def test_non_normalized_source_preserves_original_writer_semantics(self):
        run_transfer(self.maya_client, self.max_client)
        self.maya.rows = [[.2, .2, 0.], [.6, .3, 0.]]
        with patch.object(max_action, "_BULK_MIN_VERTICES", 0):
            data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(data["reports"][0]["write_mode"], "vertex")
        self.assertEqual(self.max.bulk_calls, 0)

    def test_existing_skin_is_found_among_other_modifiers_without_class_id(self):
        skin = self.max.model.modifiers[0]
        other = object()
        self.max.model.modifiers.insert(0, other)
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 1, data)
        self.assertEqual(self.max.created, 0)
        self.assertEqual(self.max.model.modifiers, [other, skin])
        self.assertEqual(skin.dq, self.dq)

    def test_missing_weighted_scene_bone_skips_model_before_skin_creation(self):
        self.max.model.modifiers = []
        self.max.objects.remove(self.max.left)
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0)
        self.assertIn("Left Arm", data["errors"][0]["error"])
        self.assertEqual(self.max.created, 0)
        self.assertEqual(self.max.writes, 0)

    def test_ambiguous_model_bone_count_mismatch_and_multiple_skin_are_skipped(self):
        cases = (
            lambda: self.max.objects.append(Node("Body Mesh", 2)),
            lambda: self.max.objects.append(Node("Left Arm")),
            lambda: setattr(self.max.model, "vertices", 3),
            lambda: self.max.model.modifiers.append(Skin(self.max.model)),
        )
        for change in cases:
            self.max = FakeMax()
            sys.modules["pymxs"].runtime = self.max
            change()
            data = run_transfer(self.maya_client, self.max_client)["data"]
            self.assertEqual(data["count"], 0, data)
            self.assertEqual(data["errors"][0]["status"], "skipped")
            self.assertEqual(self.max.writes, 0)
            self.assertFalse(self.max.adds)

    def test_invalid_source_or_dq_skips_before_max_is_called(self):
        for bad in ([0., 0., 0.], [-.1, 1.1, 0.], [float("nan"), 1., 0.]):
            self.maya.rows[0] = bad
            data = run_transfer(self.maya_client, self.max_client)["data"]
            self.assertEqual(data["count"], 0)
            self.assertTrue(data["errors"])
        self.maya.rows[0] = [.5, .5, 0.]
        self.dq[0] = float("inf")
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertIn("DQ", data["errors"][0]["error"])
        self.assertFalse(self.max_client.calls)

    def test_disconnected_max_fails_without_network_or_export_or_tempfiles(self):
        self.max_client.connected = False
        with patch("ui.skin_transfer.tempfile.TemporaryDirectory", side_effect=AssertionError("must fail first")):
            response = run_transfer(self.maya_client, self.max_client)
        self.assertFalse(response["ok"])
        self.assertIn("Max 未连接", response["error"])
        self.assertFalse(self.maya_client.calls)
        self.assertFalse(self.max_client.calls)

    def test_maya_disconnected_and_max_disconnect_during_export(self):
        self.maya_client.connected = False
        self.assertIn("Maya 未连接", run_transfer(self.maya_client, self.max_client)["error"])
        self.maya_client.connected = True
        actual = self.maya_client.call
        def export_then_disconnect(*args, **kwargs):
            result = actual(*args, **kwargs)
            self.max_client.connected = False
            return result
        self.maya_client.call = export_then_disconnect
        response = run_transfer(self.maya_client, self.max_client)
        self.assertIn("Max 已断开", response["error"])
        self.assertFalse(self.max_client.calls)

    def test_no_selection_and_partial_max_failure_do_not_report_success(self):
        self.maya.selection = []
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0)
        self.assertIn("没有选中", data["warning"])
        self.maya.selection = ["|body"]
        self.max.fail_write = 2
        response = run_transfer(self.maya_client, self.max_client)
        self.assertEqual(response["data"]["count"], 0)
        self.assertIn("可能部分更新", response["data"]["errors"][0]["error"])
        self.assertEqual(self.max.redraw_depth, 0)
        self.assertTrue(any(level == "error" for level, _ in format_transfer_messages(response)))

    def test_failed_new_skin_is_removed(self):
        self.max.model.modifiers = []
        self.max.fail_write = 1
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0)
        self.assertFalse(self.max.model.modifiers)
        self.assertIn("新建的 Skin 已移除", data["errors"][0]["error"])

    def test_corrupt_exchange_is_rejected_before_max_weight_write(self):
        actual = self.max_client.call
        def corrupt(*args, **kwargs):
            (Path(kwargs["directory"]) / "mesh-0.values").write_bytes(b"bad")
            return actual(*args, **kwargs)
        self.max_client.call = corrupt
        data = run_transfer(self.maya_client, self.max_client)["data"]
        self.assertEqual(data["count"], 0)
        self.assertIn("文件长度", data["errors"][0]["error"])
        self.assertEqual(self.max.writes, 0)
        self.assertFalse(self.max.adds)

    def test_sparse_blocks_and_full_precision_keep_vertex_offsets_and_only_used_bones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = np.array([[.123456789123, .876543210877, 0.], [0., 1., 0.], [.4, .6, 0.]], dtype="<f8")
            rows.tofile(root / "mesh-0.weights")
            np.array([0., .3, 1.], dtype="<f8").tofile(root / "mesh-0.dq")
            mesh = dict(index=0, mesh="|g|shape", name="BodyFBXASC032Mesh", vertices=3, influences=3,
                        bone_names=["ns:LeftFBXASC032Arm", "RightFBXASC032Arm", "missingUnused"], skinning_method=2)
            with patch("ui.skin_transfer.BLOCK_VALUES", 3):
                ready, errors = prepare_transfer(directory, [mesh], {})
            self.assertFalse(errors)
            self.assertEqual(ready[0]["name"], "Body Mesh")
            self.assertEqual([bone["name"] for bone in ready[0]["bones"]], ["ns:Left Arm", "Right Arm"])
            np.testing.assert_array_equal(np.fromfile(root / "mesh-0.offsets", "<u4"), [0, 2, 3, 5])
            np.testing.assert_array_equal(np.fromfile(root / "mesh-0.cols", "<u4"), [0, 1, 1, 0, 1])
            np.testing.assert_array_equal(np.fromfile(root / "mesh-0.values", "<f8"), rows[rows > 0])
            _, errors = prepare_transfer(directory, [mesh, dict(mesh)], {})
            self.assertEqual(len(errors), 2)

    def test_multiple_models_skip_missing_bone_but_import_valid_model(self):
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-") as directory:
            root = Path(directory)
            meshes = []
            for index, name, bones in ((0, "Body Mesh", ["Left Arm", "Right Arm"]),
                                       (1, "Other", ["Missing", "Right Arm"])):
                np.array([[.5, .5], [.3, .7]], dtype="<f8").tofile(root / f"mesh-{index}.weights")
                np.array([.2, .8], dtype="<f8").tofile(root / f"mesh-{index}.dq")
                meshes.append(dict(index=index, mesh=name, name=name, vertices=2, influences=2, bone_names=bones, skinning_method=2))
            ready, errors = prepare_transfer(directory, meshes, {})
            self.assertFalse(errors)
            other = Node("Other", 2)
            self.max.objects.append(other)
            data = max_action.import_max(directory, ready, 3)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["vertices"], 2)
            self.assertEqual(data["errors"][0]["mesh"], "Other")
            self.assertFalse(other.modifiers)
            self.assertEqual(self.max.selection, [self.max.model])

    def test_source_mode_gates_dq_for_vertex_bulk_and_new_skin(self):
        original_max_call = self.max_client.call
        exported_modes = []
        def observe_files(*args, **params):
            exported_modes.extend(mesh["skinning_method"] for mesh in params["meshes"])
            np.testing.assert_array_equal(np.fromfile(Path(params["directory"]) / "mesh-0.dq", "<f8"), self.dq)
            return original_max_call(*args, **params)
        self.max_client.call = observe_files
        for minimum in (0, 10000):
            for method in (0, 1, 2):
                for existing in (True, False):
                    with self.subTest(minimum=minimum, method=method, existing=existing):
                        self.max = FakeMax(with_skin=existing)
                        sys.modules["pymxs"].runtime = self.max
                        old_dq = [.17, .63] if existing else [0., 0.]
                        if existing: self.max.model.modifiers[0].dq = list(old_dq)
                        self.maya.settings["skin.skinningMethod"] = method
                        setter = self.max.skinOps.setVertexDQWeight
                        with patch.object(max_action, "_BULK_MIN_VERTICES", minimum), \
                                patch.object(self.max.skinOps, "setVertexDQWeight", wraps=setter) as write_dq:
                            response = run_transfer(self.maya_client, self.max_client)
                        self.assertEqual(response["data"]["count"], 1, response)
                        report = response["data"]["reports"][0]
                        self.assertEqual(report["write_mode"], "bone_groups" if minimum == 0 else "vertex")
                        self.assertEqual(report["skinning_method"], method)
                        self.assertEqual(report["dq_imported"], method != 0)
                        self.assertEqual(write_dq.call_count, 2 if method != 0 else 0)
                        self.assertEqual(self.max.model.modifiers[0].dq, self.dq if method != 0 else old_dq)
                        messages = [message for _, message in format_transfer_messages(response)]
                        self.assertTrue(any(("已同步 DQ" if method else "保留 Max 原有 DQ") in message for message in messages))
        self.assertEqual(exported_modes, [mode for _ in (0, 10000) for mode in (0, 1, 2) for _ in (True, False)])

    def test_missing_or_invalid_source_mode_does_not_reach_max(self):
        actual = self.maya_client.call
        for method in (None, True, 99):
            def corrupt(*args, **kwargs):
                response = actual(*args, **kwargs)
                for mesh in response.get("data", {}).get("meshes", []):
                    if method is None: mesh.pop("skinning_method", None)
                    else: mesh["skinning_method"] = method
                return response
            self.maya_client.call = corrupt
            response = run_transfer(self.maya_client, self.max_client)
            self.assertEqual(response["data"]["count"], 0)
            self.assertIn("蒙皮方式", response["data"]["errors"][0]["error"])
        self.assertFalse(self.max_client.calls)

    def test_each_model_uses_its_own_source_mode(self):
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-") as directory:
            root = Path(directory)
            other = Node("Other", 2)
            other.modifiers = [Skin(other, [self.max.left, self.max.right])]
            self.max.objects.append(other)
            self.max.model.modifiers[0].dq = [.15, .65]
            other.modifiers[0].dq = [.33, .44]
            meshes = []
            for index, name, method in ((0, "Body Mesh", 0), (1, "Other", 2)):
                np.array([[.5, .5], [.3, .7]], dtype="<f8").tofile(root / f"mesh-{index}.weights")
                np.array([.2, .8], dtype="<f8").tofile(root / f"mesh-{index}.dq")
                meshes.append(dict(index=index, mesh=name, name=name, vertices=2, influences=2,
                                   bone_names=["Left Arm", "Right Arm"], skinning_method=method))
            ready, errors = prepare_transfer(directory, meshes, {})
            self.assertFalse(errors)
            data = max_action.import_max(directory, ready)
            self.assertEqual(data["count"], 2)
            self.assertEqual(self.max.model.modifiers[0].dq, [.15, .65])
            self.assertEqual(other.modifiers[0].dq, [.2, .8])
            self.assertEqual([report["dq_imported"] for report in data["reports"]], [False, True])

    def test_old_max_bridge_is_rejected_before_any_weight_write(self):
        import max_bridge.server as server
        with patch.object(server, "SUPPORTS_EXECUTION_TIMEOUT", False):
            response = run_transfer(self.maya_client, self.max_client)
        self.assertFalse(response["ok"])
        self.assertIn("桥接版本需要更新", response["error"])
        self.assertEqual(self.max.writes, 0)

    def test_real_widget_send_click_uses_shared_max_client_background_and_ui_logs(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_loader import scan
        from ui.main_window import MainWindow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        logs, threads = [], []
        class Log:
            def info(self, text): logs.append(text); threads.append(threading.current_thread().name)
            warn = error = info
        clients = dict(maya=self.maya_client, max=self.max_client)
        info = next(info for info in scan(str(ROOT / "plugins")) if info.key == ("maya", "skin_weights"))
        widget = MainWindow._make_widget(types.SimpleNamespace(_clients=clients, _log=Log()), info)
        self.assertIs(widget._dcc_clients, clients)
        widget.show()
        widget.inf_lab.setText("4")
        self.max_client.connected = False
        QtTest.QTest.mouseClick(widget.send_btn, QtCore.Qt.LeftButton)
        self.assertIsNone(widget._task)
        self.assertFalse(self.maya_client.calls)
        self.assertTrue(any("Max 未连接" in text for text in logs))
        self.max_client.connected = True
        widget.max_inf_sbox.setValue(7)
        QtTest.QTest.mouseClick(widget.send_btn, QtCore.Qt.LeftButton)
        self.assertFalse(widget.send_btn.isEnabled())
        deadline = time.monotonic() + 10
        while widget._task is not None and time.monotonic() < deadline:
            app.processEvents()
            QtTest.QTest.qWait(2)
        self.assertIsNone(widget._task)
        self.assertTrue(widget.send_btn.isEnabled())
        self.assertEqual(widget.inf_lab.text(), "4")
        self.assertEqual(self.max.model.modifiers[0].bone_limit, 7)
        self.assertTrue(any("发送到 Max 完成" in text for text in logs))
        self.assertTrue(any("总耗时" in text for text in logs))
        self.assertFalse(any("分阶段" in text or "NumPy处理" in text for text in logs))
        self.assertTrue(all(thread == "MainThread" for thread in threads))
        self.assertTrue(all(call[2] != "MainThread" for call in self.maya_client.calls))
        self.assertTrue(all(call[3] != "MainThread" for call in self.max_client.calls))
        self.assertFalse(widget.styleSheet())
        widget.close()
        widget.deleteLater()
        app.processEvents()


class TransportTests(unittest.TestCase):
    def test_client_separates_connection_and_execution_wait_and_keeps_action_params_clean(self):
        from unittest.mock import MagicMock
        sock = MagicMock()
        sock.recv.return_value = b'{"ok":true,"data":{}}\n'
        with patch("dcc_bridge.socket.socket", return_value=sock):
            DccClient.for_dcc("max").call("skin_weights_import_max", timeout=305,
                                           connect_timeout=1, execution_timeout=300, directory="temp")
        self.assertEqual([call.args[0] for call in sock.settimeout.call_args_list], [1, 305])
        packet = json.loads(sock.sendall.call_args.args[0])
        self.assertEqual(packet["execution_timeout"], 300)
        self.assertEqual(packet["params"], dict(directory="temp"))
        self.assertTrue(sock.close.called)

    def test_max_server_honors_explicit_wait_default_still_unchanged(self):
        import max_bridge.server as server
        for requested in (None, 300, False, -1, float("inf")):
            outgoing, incoming = socket.socketpair()
            packet = dict(action="skin_weights_import_max", params={})
            if requested is not None: packet["execution_timeout"] = requested
            with patch.object(server, "submit", return_value=dict(ok=True, data={})) as submit:
                thread = threading.Thread(target=server.handle_client, args=(incoming,), daemon=True)
                thread.start()
                outgoing.settimeout(2)
                outgoing.sendall((json.dumps(packet) + "\n").encode())
                raw = b""
                while b"\n" not in raw: raw += outgoing.recv(4096)
                thread.join(2)
                response = json.loads(raw)
            outgoing.close()
            incoming.close()
            if requested is None or requested == 300:
                self.assertTrue(response["ok"])
                self.assertEqual(submit.call_args.kwargs["timeout"], server.REQUEST_TIMEOUT if requested is None else 300)
            else:
                self.assertFalse(response["ok"])
                self.assertFalse(submit.called)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    print(f"PASS {result.result.testsRun - len(result.result.failures) - len(result.result.errors)} / {result.result.testsRun}")
    sys.exit(0 if result.result.wasSuccessful() else 1)
