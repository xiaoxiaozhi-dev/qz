"""Max Skin Python 2 action semantics, middle NumPy/files and actual Qt callbacks."""
import copy
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
from max_actions import skin_weights as action
from ui import max_skin_weights as middle
from max_actions import skin_weight_geometry as geometry
from test_skin_transfer import FakeMax, Skin, Node


class LocalSkin(Skin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dq_enabled = True
    @property
    def enableDQ(self): return self._dq_enabled
    @enableDQ.setter
    def enableDQ(self, value): self._dq_enabled = bool(value)


class Client:
    connected = True
    def __init__(self): self.calls = []
    def call(self, name, timeout=10, connect_timeout=None, **kwargs):
        self.calls.append((name, kwargs, threading.current_thread().name))
        try:
            fn = action.skin_weights if name == "skin_weights_max" else action.skin_weight_files
            return dict(ok=True, data=fn(**kwargs))
        except Exception as exc: return dict(ok=False, error=str(exc))


class MaxSkinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rt = FakeMax()
        self.rt.Skin = LocalSkin
        self.rt.model.vertices = 4
        self.third = Node("Third Bone")
        self.rt.objects.append(self.third)
        self.skin = LocalSkin(self.rt.model, [self.rt.left, self.rt.right, self.third])
        self.rt.model.modifiers = [self.skin]
        self.skin.weights = [{1:.5,2:.3,3:.2}, {1:.123456789,2:.876543211}, {2:2.}, {3:1.}]
        self.skin.dq = [.123456789,.25,.5,.875]
        self.skin.unnormalized = [False,False,True,False]
        self.rt.selection = [self.rt.model]
        self.handles = {i+1:n for i,n in enumerate(self.rt.objects)}
        self.rt.getHandleByAnim = lambda node: next(i for i,n in self.handles.items() if n is node)
        self.rt.getAnimByHandle = self.handles.get
        self.rt.isValidNode = lambda node: node in self.rt.objects
        ops = self.rt.skinOps
        ops.isWeightToolOpen = lambda skin: False
        ops.closeWeightTool = lambda skin: None
        ops.GetBoneIDByListID = lambda skin,index: sorted(range(1,len(skin.bones)+1),key=lambda i:skin.bones[i-1].name)[index-1]
        ops.GetVertexWeight = lambda skin,vertex,slot: list(skin.weights[vertex-1].values())[slot-1]
        ops.getVertexDQWeight = lambda skin,vertex: skin.dq[vertex-1]
        self.module_patch = patch.dict(sys.modules,{"pymxs":types.SimpleNamespace(runtime=self.rt)})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        root_patch = patch.object(middle.paths,"project_dir",return_value=str(self.root))
        root_patch.start()
        self.addCleanup(root_patch.stop)
        def input_geometry(rt,node,skin,vertices):
            points=getattr(node,"points",[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(1.,1.,0.)]
                           + [(float(i),0.,0.) for i in range(4,vertices)])
            if len(points)!=vertices: raise ValueError("目标几何顶点数不一致")
            return points,[0,1,2]
        self.original_input_geometry=geometry.input_geometry
        geometry_patch=patch.object(geometry,"input_geometry",side_effect=input_geometry)
        geometry_patch.start()
        self.addCleanup(geometry_patch.stop)
        self.client = Client()
        self.archive = self.root/"temp/skin_weights/Body Mesh.qzskin"
        action._JOBS.clear()

    def tearDown(self):
        action._JOBS.clear()
        self.temp.cleanup()

    def export(self):
        result=middle.run_file_operation(self.client,"export_file")
        self.assertEqual(result["data"]["count"],1,result)
        return result

    def test_check_counts_positive_influences_without_modification(self):
        before=copy.deepcopy(self.skin.weights)
        self.skin.weights[1][3]=0.
        result=middle.run_operation(self.client,"check",2)
        self.assertEqual(result["data"]["max_influences"],3,result)
        self.assertEqual(result["data"]["reports"][0]["excess_before"],1)
        self.assertEqual(self.rt.writes,0)
        self.assertEqual(self.rt.selection,[self.rt.model])
        self.assertFalse(action._JOBS)

    def test_selection_uses_one_based_skin_vertices_and_clears_when_none(self):
        result=middle.run_operation(self.client,"select_excess",2)
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.skin.selected,[1])
        self.assertEqual(self.rt.subObjectLevel,1)
        result=middle.run_operation(self.client,"select_excess",3)
        self.assertEqual(self.skin.selected,[])
        self.assertEqual(self.rt.writes,0)

    def test_limit_changes_only_excessive_rows_preserving_dq_and_flags(self):
        before=copy.deepcopy(self.skin.weights)
        dq,flags=list(self.skin.dq),list(self.skin.unnormalized)
        result=middle.run_operation(self.client,"limit",2,0)
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.rt.writes,1)
        self.assertEqual(self.skin.weights[0],{1:.625,2:.375})
        self.assertEqual(self.skin.weights[1:],before[1:])
        self.assertEqual(self.skin.dq,dq)
        self.assertEqual(self.skin.unnormalized,flags)
        self.assertEqual(self.skin.bone_limit,2)
        self.assertEqual(self.rt.redraw_depth,0)

    def test_average_limit_reuses_middle_algorithm(self):
        result=middle.run_operation(self.client,"limit",2,1)
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.skin.weights[0],{1:.6,2:.4})

    def test_invalid_rows_report_one_based_indices_and_are_not_written(self):
        self.skin.weights=[{1:0.},{1:-.1,2:1.1},{1:float("nan")},{1:.5,2:.3,3:.2}]
        result=middle.run_operation(self.client,"limit",2)
        self.assertEqual(result["data"]["invalid_vertices"],3,result)
        self.assertEqual(result["data"]["count"],1)
        messages=middle.format_result(result)
        self.assertTrue(any("Body Mesh 顶点 1" in message for _,message in messages))
        self.assertTrue(any("Body Mesh 顶点 3" in message for _,message in messages))
        self.assertEqual(self.rt.writes,1)

    def test_failed_limit_attempts_recovery_and_releases_job(self):
        weights=copy.deepcopy(self.skin.weights)
        self.rt.fail_write=1
        result=middle.run_operation(self.client,"limit",2)
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("恢复",result["data"]["errors"][0]["error"])
        self.assertEqual(self.skin.weights,weights)
        self.assertIsNone(result["data"]["max_influences"])
        self.assertFalse(action._JOBS)

    def test_file_import_closes_open_weight_tool_before_writing_and_leaves_it_closed(self):
        self.export()
        for initially_open in (True, False):
            with self.subTest(initially_open=initially_open):
                state = {"open": initially_open}
                replace = self.rt.skinOps.ReplaceVertexWeights
                def close(skin):
                    self.assertIs(skin, self.skin)
                    state["open"] = False
                def write(*args):
                    self.assertFalse(state["open"], "Weight Tool must close before writes")
                    return replace(*args)
                with patch.object(self.rt.skinOps, "isWeightToolOpen", side_effect=lambda skin: state["open"]), \
                        patch.object(self.rt.skinOps, "closeWeightTool", side_effect=close) as closer, \
                        patch.object(self.rt.skinOps, "ReplaceVertexWeights", side_effect=write):
                    result = middle.run_file_operation(self.client, "import_file")
                self.assertEqual(result["data"]["count"], 1, result)
                self.assertEqual(closer.call_count, int(initially_open))
                self.assertFalse(state["open"])

    def test_full_file_roundtrip_preserves_precision_dq_mode_and_nonunit_rows(self):
        weights,dq,flags=copy.deepcopy(self.skin.weights),list(self.skin.dq),list(self.skin.unnormalized)
        self.export()
        self.assertTrue(self.archive.is_file())
        self.assertFalse((self.archive.parent/"max").exists())
        with zipfile.ZipFile(self.archive) as archive:
            metadata=json.loads(archive.read("manifest.json"))
            self.assertEqual(metadata["source"],"max")
            self.assertEqual(metadata["version"],3)
            self.assertEqual(metadata["max_unnormalized_vertices"],[2])
            self.assertIn("points.bin",archive.namelist())
            self.assertIn(.123456789,np.frombuffer(archive.read("weights.bin"),"<f8"))
        self.skin.weights=[{3:1.} for _ in range(4)]
        self.skin.dq=[0.]*4
        self.skin.enableDQ=False
        self.skin.bone_limit=1
        self.skin.showNoEnvelopes=False
        self.skin.draw_vertices=True
        self.skin.showHiddenVertices=False
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.skin.weights,weights)
        self.assertEqual(self.skin.dq,dq)
        self.assertEqual(self.skin.unnormalized,flags)
        self.assertTrue(self.skin.enableDQ)
        self.assertEqual(self.skin.bone_limit,20)
        self.assertFalse(self.skin.showNoEnvelopes)
        self.assertTrue(self.skin.draw_vertices)
        self.assertFalse(self.skin.showHiddenVertices)
        self.assertEqual(self.rt.selection,[self.rt.model])

    def test_maya_export_and_max_export_share_same_archive_and_bone_encoding(self):
        from ui import skin_weight_files as maya_files
        self.rt.model.name="BodyMesh"
        self.archive=self.root/"temp/skin_weights/BodyMesh.qzskin"
        weights,dq=copy.deepcopy(self.skin.weights),list(self.skin.dq)
        self.export()
        target=dict(index=0,name="BodyMesh",mesh="BodyMesh",vertices=4,
                    node_handle=self.rt.getHandleByAnim(self.rt.model))
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-") as directory:
            entry=maya_files.read_archive(self.archive,directory,target,"closest_point")
            self.assertEqual(entry["vertices"],4)
            self.assertEqual(entry["geometry"]["space"],"object")
            source=dict(name="BodyMesh",geometry=entry["geometry"],
                        bone_names=[b.name.replace(" ","FBXASC032") for b in self.skin.bones])
            destination=maya_files.write_archive(directory,self.archive.parent,entry,source)
            self.assertEqual(destination,self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            metadata=json.loads(archive.read("manifest.json"))
            self.assertEqual(metadata["source"],"maya")
            self.assertNotIn("max_bone_limit",metadata)
        self.skin.weights=[{3:1.} for _ in range(4)]
        self.skin.dq=[0.]*4
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(result["data"]["directory"],str(self.archive.parent))
        self.assertEqual(self.skin.weights,weights)
        self.assertEqual(self.skin.dq,dq)
        self.export()
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(json.loads(archive.read("manifest.json"))["source"],"max")
        with tempfile.TemporaryDirectory() as directory:
            entry=maya_files.read_archive(self.archive,directory,target,"closest_point")
            self.assertEqual(entry["vertices"],4)
        self.assertEqual(list(self.archive.parent.glob("*.qzskin")),[self.archive])
        self.assertFalse((self.archive.parent/"max").exists())

    def _unit_file_for_bulk(self):
        self.skin.weights[2]={2:1.}
        self.skin.unnormalized=[False]*4
        self.skin.enableDQ=False
        weights,dq,flags=copy.deepcopy(self.skin.weights),list(self.skin.dq),list(self.skin.unnormalized)
        self.export()
        return weights,dq,flags

    def test_file_bulk_restores_source_flags_and_latent_dq_in_linear_mode(self):
        weights,dq,flags=self._unit_file_for_bulk()
        self.skin.weights=[{1:.4,2:.3,3:.3} for _ in range(4)]
        self.skin.unnormalized=[False]*4
        self.skin.dq=[0.]*4
        self.skin.enableDQ=True
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(result["data"]["reports"][0]["write_mode"],"bone_groups")
        self.assertEqual(self.skin.weights,[{1:.5,2:.3,3:.2},{1:.123456789,2:.876543211,3:0.},
                                            {1:0.,2:1.,3:0.},{1:0.,2:0.,3:1.}])
        self.assertEqual(self.skin.dq,dq)
        self.assertEqual(self.skin.unnormalized,flags)
        self.assertFalse(self.skin.enableDQ)
        self.assertEqual(self.rt.writes,0)
        self.assertGreater(self.rt.bulk_calls,0)

    def test_file_bulk_new_skin_initializes_and_keeps_new_defaults(self):
        weights,dq,flags=self._unit_file_for_bulk()
        self.rt.model.modifiers=[]
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(result["data"]["reports"][0]["write_mode"],"bone_groups")
        skin=self.rt.model.modifiers[0]
        self.assertEqual(skin.bone_limit,4)
        self.assertTrue(skin.showNoEnvelopes)
        self.assertTrue(skin.showHiddenVertices)
        self.assertFalse(skin.shadeweights)
        self.assertEqual(skin.dq,dq)
        self.assertEqual(skin.unnormalized,flags)
        for actual,expected in zip(skin.weights,weights):
            self.assertEqual({b:w for b,w in actual.items() if w>0},expected)
        self.assertEqual(self.rt.writes,0)
        self.assertEqual(len(self.rt.seed_calls),2)
        self.assertEqual([update for _,update in self.rt.adds],[0,0,1])

    def test_file_bulk_missing_membership_initializes_and_clears_old_influence(self):
        weights,dq,flags=self._unit_file_for_bulk()
        self.skin.weights=[{3:1.} for _ in range(4)]
        self.skin.modified=[False]*4
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(result["data"]["reports"][0]["write_mode"],"bone_groups")
        for actual,expected in zip(self.skin.weights,weights):
            self.assertEqual({b:w for b,w in actual.items() if w>0},expected)
        self.assertEqual(self.skin.unnormalized,flags)
        self.assertEqual(self.skin.dq,dq)
        self.assertEqual(len(self.rt.seed_calls),2)

    def test_file_bulk_failure_restores_target_flags_without_vertex_retry(self):
        self._unit_file_for_bulk()
        original_flags=[False]*4
        self.skin.unnormalized=list(original_flags)
        self.skin.weights=[{1:.4,2:.3,3:.3} for _ in range(4)]
        self.rt.fail_bulk=True
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("批量导入中断",result["data"]["errors"][0]["error"])
        self.assertEqual(self.skin.unnormalized,original_flags)
        self.assertEqual(self.rt.writes,0)
        self.assertGreater(self.rt.bulk_calls,0)

    def test_file_bulk_capacity_and_nonunit_source_use_cached_vertex_fallback(self):
        weights,dq,flags=self._unit_file_for_bulk()
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0), \
                patch.object(action._transfer,"_BULK_MAX_ENTRIES",1):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(result["data"]["reports"][0]["write_mode"],"vertex")
        self.assertEqual(self.rt.bulk_calls,0)
        self.assertEqual(self.rt.writes,4)
        self.skin.weights[2]={2:2.}
        self.skin.unnormalized[2]=True
        self.export()
        self.skin.weights[2]={1:1.}
        with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["reports"][0]["write_mode"],"vertex")
        self.assertEqual(self.skin.weights[2],{2:2.})
        self.assertTrue(self.skin.unnormalized[2])
        self.assertEqual(self.rt.bulk_calls,0)

    def test_unit_source_or_target_unnormalized_flags_keep_vertex_path(self):
        for source_flags,target_flags in (([True,False,False,True],[False]*4),
                                          ([False]*4,[True,False,False,True])):
            weights,dq,_=self._unit_file_for_bulk()
            self.skin.unnormalized=source_flags
            self.export()
            self.skin.unnormalized=target_flags
            before_bulk=self.rt.bulk_calls
            with patch.object(action,"FILE_BULK_MIN_VERTICES",0):
                result=middle.run_file_operation(self.client,"import_file")
            self.assertEqual(result["data"]["count"],1,result)
            self.assertEqual(result["data"]["reports"][0]["write_mode"],"vertex")
            self.assertEqual(self.rt.bulk_calls,before_bulk)
            self.assertEqual(self.skin.unnormalized,source_flags)
            self.assertEqual(self.skin.dq,dq)
            self.assertEqual(self.skin.weights,weights)

    def test_linear_mode_still_preserves_and_restores_dq_values(self):
        self.skin.enableDQ=False
        self.export()
        self.skin.enableDQ=True
        self.skin.dq=[0.]*4
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertFalse(self.skin.enableDQ)
        self.assertEqual(self.skin.dq,[.123456789,.25,.5,.875])

    def test_new_skin_and_missing_influence_are_supported(self):
        self.export()
        self.rt.model.modifiers=[]
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertTrue(result["data"]["reports"][0]["created_skin"])
        skin=self.rt.model.modifiers[0]
        self.assertEqual(len(skin.bones),3)
        self.assertEqual(skin.bone_limit,4)
        for name in ("draw_vertices","shadeweights","colorAllWeights","draw_all_envelopes",
                     "draw_all_vertices","draw_all_gizmos"):
            self.assertIs(getattr(skin,name),False,name)
        self.assertIs(skin.showNoEnvelopes,True)
        self.assertIs(skin.showHiddenVertices,True)

    def test_missing_scene_bone_skips_before_skin_creation(self):
        self.export()
        self.rt.model.modifiers=[]
        self.rt.objects.remove(self.third)
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("场景缺少骨骼",result["data"]["errors"][0]["error"])
        self.assertEqual(self.rt.model.modifiers,[])
        self.assertEqual(self.rt.writes,0)

    def test_vertex_mismatch_skips_before_write(self):
        self.export()
        self.rt.model.vertices=5
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("顶点数",result["data"]["errors"][0]["error"])
        self.assertEqual(self.rt.writes,0)

    def test_invalid_export_or_atomic_replace_failure_keeps_old_file(self):
        self.export()
        original=self.archive.read_bytes()
        with patch.object(middle.os,"replace",side_effect=OSError("disk full")):
            result=middle.run_file_operation(self.client,"export_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertEqual(self.archive.read_bytes(),original)
        self.skin.weights[0]={1:-1.,2:2.}
        result=middle.run_file_operation(self.client,"export_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertEqual(self.archive.read_bytes(),original)
        self.assertFalse(list(self.archive.parent.glob("*.tmp")))

    def test_corrupt_file_is_rejected_before_max_write(self):
        self.export()
        with zipfile.ZipFile(self.archive) as archive: contents={n:archive.read(n) for n in archive.namelist()}
        for bad in ([-1],[4],[2,2],True):
            metadata=json.loads(contents["manifest.json"])
            metadata["max_unnormalized_vertices"]=bad
            with zipfile.ZipFile(self.archive,"w") as archive:
                for n,data in contents.items(): archive.writestr(n,json.dumps(metadata) if n=="manifest.json" else data)
            result=middle.run_file_operation(self.client,"import_file")
            self.assertEqual(result["data"]["count"],0,result)
            self.assertEqual(self.rt.writes,0)

    def test_failed_new_skin_is_removed(self):
        self.export()
        self.rt.model.modifiers=[]
        self.rt.fail_write=1
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertEqual(self.rt.model.modifiers,[])
        self.assertIn("已移除",result["data"]["errors"][0]["error"])

    def test_no_selection_or_disconnection_is_clear(self):
        self.rt.selection=[]
        for fn,operation in ((middle.run_operation,"check"),(middle.run_file_operation,"import_file")):
            result=fn(self.client,operation)
            self.assertIn("选中",result["data"]["warning"])
        self.client.connected=False
        result=middle.run_operation(self.client,"check")
        self.assertIn("Max 未连接",result["error"])
        self.assertFalse(self.archive.parent.exists())

    def test_action_duration_includes_selection_restoration_without_double_counting(self):
        clock=[10.]
        original_restore=action._restore_selection
        def restore(*args):
            clock[0]+=.7
            return original_restore(*args)
        with tempfile.TemporaryDirectory(prefix="qz-skin-max-") as directory:
            with patch.object(action,"_clock",lambda:clock[0]), \
                    patch.object(action,"_restore_selection",side_effect=restore):
                result=action.skin_weights(op="snapshot",directory=directory,retain_task=False)
        self.assertAlmostEqual(result["action_seconds"],.7)
        self.assertAlmostEqual(result["timings_seconds"]["max_restore"],.7)
        self.assertAlmostEqual(sum(result["timings_seconds"].values()),result["action_seconds"])

    def test_bridge_remainder_excludes_reported_max_execution(self):
        response=dict(ok=True,data=dict(action_seconds=.3,timings_seconds=dict(max_prepare=.05,max_read_expand=.25)))
        client=types.SimpleNamespace(connected=True,call=lambda *args,**kw:response)
        timings={}
        with patch.object(middle.time,"perf_counter",side_effect=[10.,10.9]):
            middle._call(client,"skin_weights_max",_timings=timings,op="snapshot")
        self.assertAlmostEqual(timings["bridge_wait"],.6)
        self.assertAlmostEqual(sum(timings.values()),.9)

    def test_legacy_max_does_not_invent_phase_breakdown(self):
        response=dict(ok=True,data=dict(action_seconds=.2))
        client=types.SimpleNamespace(connected=True,call=lambda *args,**kw:response)
        timings={}
        with patch.object(middle.time,"perf_counter",side_effect=[10.,10.5]):
            middle._call(client,"skin_weights_max",_timings=timings,op="snapshot")
        self.assertAlmostEqual(timings["max_unprofiled"],.2)
        self.assertAlmostEqual(timings["bridge_wait"],.3)
        self.assertNotIn("max_read_expand",timings)

    def test_numeric_operation_exposes_non_overlapping_max_and_middle_phases(self):
        response=middle.run_operation(self.client,"check",4)
        self.assertTrue(response["ok"],response)
        data=response["data"]
        self.assertIn("max_read_expand",data["timings_seconds"])
        self.assertIn("host_file_read",data["timings_seconds"])
        self.assertIn("numpy_compute",data["timings_seconds"])
        self.assertLess(abs(sum(data["timings_seconds"].values())-data["elapsed_seconds"]),.001)
        messages=middle.format_result(response)
        self.assertEqual(messages[-1],("info",f'总耗时 {data["elapsed_seconds"]:.3f} 秒。'))
        self.assertFalse(any("分阶段" in message for _,message in messages))

    def test_active_skin_check_avoids_panel_rebuild_and_extra_bridge_request(self):
        self.rt.panel_mode="modify"
        self.rt.current_modifier=self.skin
        selected_calls,panel_calls=self.rt.selection_calls,self.rt.panel_calls
        result=middle.run_operation(self.client,"check",4)
        self.assertTrue(result["ok"],result)
        self.assertEqual(self.rt.selection_calls,selected_calls)
        self.assertEqual(self.rt.panel_calls,panel_calls)
        self.assertEqual([params["op"] for _,params,_ in self.client.calls],["snapshot"])
        self.assertFalse(action._JOBS)

    def test_inactive_skin_is_activated_once_and_reused_during_capture(self):
        result=middle.run_operation(self.client,"check",4)
        self.assertTrue(result["ok"],result)
        self.assertEqual(self.rt.panel_calls,1)
        self.assertEqual(self.rt.selection_calls,0)
        self.assertIs(self.rt.current_modifier,self.skin)

    def test_successful_apply_consumes_job_without_release_request(self):
        result=middle.run_operation(self.client,"limit",2)
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual([params["op"] for _,params,_ in self.client.calls],["snapshot","limit"])
        self.assertFalse(action._JOBS)

    def test_different_node_reactivates_even_for_the_same_instanced_skin(self):
        self.rt.panel_mode="modify"
        self.rt.current_modifier=self.skin
        other=Node("Other Mesh",4)
        self.rt.objects.append(other)
        action._activate(self.rt,other,self.skin)
        self.assertEqual(self.rt.selection,[other])
        self.assertEqual(self.rt.selection_calls,1)
        self.assertEqual(self.rt.panel_calls,1)

    def test_numpy_error_releases_retained_task(self):
        with patch.object(middle,"process_snapshot",side_effect=RuntimeError("compute failed")):
            result=middle.run_operation(self.client,"limit",2)
        self.assertFalse(result["ok"])
        self.assertEqual([params["op"] for _,params,_ in self.client.calls],["snapshot","release"])
        self.assertFalse(action._JOBS)

    def test_last_partial_block_roundtrip_preserves_every_row(self):
        count=4105
        self.rt.model.vertices=count
        self.skin.weights=[{1:.123456789,2:.876543211} for _ in range(count)]
        self.skin.dq=[(i%7)/6 for i in range(count)]
        self.skin.unnormalized=[i%5==0 for i in range(count)]
        self.skin.modified=[True]*count
        expected=copy.deepcopy(self.skin.weights)
        dq=list(self.skin.dq)
        self.export()
        self.skin.weights=[{3:1.} for _ in range(count)]
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual([{b:w for b,w in row.items() if w>0} for row in self.skin.weights],expected)
        self.assertEqual(self.skin.dq,dq)

    def test_reordered_bones_and_extra_target_influence_are_mapped_and_zeroed(self):
        expected=[{self.skin.bones[i-1].name:w for i,w in row.items()} for row in self.skin.weights]
        self.export()
        extra=Node("Unused Extra")
        self.rt.objects.append(extra)
        self.skin.bones=[self.third,self.rt.left,extra,self.rt.right]
        self.skin.weights=[{3:1.} for _ in range(4)]
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        actual=[{self.skin.bones[i-1].name:w for i,w in row.items()} for row in self.skin.weights]
        self.assertEqual(actual,expected)

    def test_file_import_restores_saved_small_bone_limit_without_truncation(self):
        self.skin.bone_limit=1
        expected=copy.deepcopy(self.skin.weights)
        self.export()
        self.skin.bone_limit=20
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.skin.bone_limit,1)
        self.assertEqual(self.skin.weights,expected)

    def test_export_preserves_literal_max_names(self):
        self.rt.left.name="Left FBXASC032 literal"
        self.export()
        with zipfile.ZipFile(self.archive) as archive:
            metadata=json.loads(archive.read("manifest.json"))
        self.assertIn("Left FBXASC032 literal",[b["name"] for b in metadata["bones"]])

    def test_case_insensitive_model_name_collision_does_not_create_archive(self):
        other=Node("body mesh",4)
        other.modifiers=[LocalSkin(other,[self.rt.left])]
        other.modifiers[0].weights=[{1:1.} for _ in range(4)]
        self.rt.objects.append(other)
        self.handles[99]=other
        self.rt.selection=[self.rt.model,other]
        result=middle.run_file_operation(self.client,"export_file")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertEqual(len(result["data"]["errors"]),2)
        self.assertFalse(self.archive.exists())


    def test_skin_input_snapshot_restores_disabled_and_enabled_modifiers_on_error(self):
        top=types.SimpleNamespace(enabled=False)
        self.skin.enabled=True
        lower=types.SimpleNamespace(enabled=True)
        self.rt.model.modifiers=[top,self.skin,lower]
        def snapshot(node):
            self.assertFalse(top.enabled)
            self.assertFalse(self.skin.enabled)
            self.assertTrue(lower.enabled)
            raise RuntimeError("snapshot failure")
        self.rt.snapshotAsMesh=snapshot
        with self.assertRaisesRegex(RuntimeError,"snapshot failure"):
            self.original_input_geometry(self.rt,self.rt.model,self.skin,4)
        self.assertFalse(top.enabled)
        self.assertTrue(self.skin.enabled)
        self.assertTrue(lower.enabled)

    def test_max_position_keys_match_python3_maya_rounding(self):
        for decimals in (0,3,5):
            for value in (-2.675,-.0025,-.0015,-.0005,-0.,.0005,.0015,.0025,2.675,12345.67891):
                xyz=(value,value/3.,value*2.)
                self.assertEqual(geometry.position_key(xyz,decimals),tuple(round(v,decimals) for v in xyz))

    def test_spatial_import_only_selects_vertices_without_named_set_or_appdata(self):
        self.export()
        self.skin.selected=[1,2]
        def unexpected(*args,**kw): raise AssertionError("selection set storage must not be accessed")
        self.rt.getAppData=unexpected
        self.rt.setAppData=unexpected
        self.rt.model.baseObject=types.SimpleNamespace()
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertTrue(result["data"]["selection_changed"])
        self.assertEqual(self.skin.selected,[])
        self.assertFalse(hasattr(geometry,"interpolation_selection"))
        self.assertNotIn("interpolation_set",result["data"]["reports"][0])
        self.assertNotIn("selection_warning",result["data"]["reports"][0])

    def _write_legacy_file(self):
        self.export()
        with zipfile.ZipFile(self.archive) as archive:
            contents={name:archive.read(name) for name in archive.namelist()}
        metadata=json.loads(contents.pop("manifest.json"))
        metadata["version"]=2
        metadata.pop("geometry")
        with zipfile.ZipFile(self.archive,"w") as archive:
            archive.writestr("manifest.json",json.dumps(metadata))
            for name,data in contents.items():
                if name not in ("points.bin","triangles.bin"): archive.writestr(name,data)

    def test_old_file_keeps_id_import_and_rejects_space_before_writing(self):
        self._write_legacy_file()
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("重新导出",result["data"]["errors"][0]["error"])
        self.assertEqual(self.rt.writes,0)
        result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)

    def test_id_import_does_not_query_geometry_or_change_selection(self):
        self.export()
        self.rt.selection=[self.rt.model]
        with patch.object(geometry,"input_geometry",side_effect=AssertionError("unexpected geometry query")):
            result=middle.run_file_operation(self.client,"import_file")
        self.assertEqual(result["data"]["count"],1,result)
        self.assertEqual(self.rt.selection,[self.rt.model])
        self.assertFalse(any(params.get("op")=="map" for _,params,_ in self.client.calls))

    def test_spatial_reorder_duplicates_preserve_exact_weights_dq_and_flags(self):
        self.export()
        weights,dq=copy.deepcopy(self.skin.weights),list(self.skin.dq)
        self.rt.model.points=[(0.,1.,0.),(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(1.,1.,0.)]
        self.rt.model.vertices=5
        self.skin.weights=[{1:1.} for _ in range(5)]
        self.skin.modified=[True]*5
        self.skin.dq=[0.]*5
        self.skin.unnormalized=[False]*5
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],1,result)
        order=[2,0,1,2,3]
        self.assertEqual(self.skin.weights,[weights[i] for i in order])
        self.assertEqual(self.skin.dq,[dq[i] for i in order])
        self.assertEqual(self.skin.unnormalized,[True,False,False,True,False])
        self.assertEqual(self.skin.selected,[])
        self.assertEqual(result["data"]["reports"][0]["direct_vertices"],5)
        self.assertEqual(result["data"]["reports"][0]["interpolated_vertices"],0)
        self.assertTrue(result["data"]["selection_changed"])

    def test_native_mapping_interpolates_ordinary_dq_and_tracks_projected_corner(self):
        self.export()
        self.rt.model.points=[(.25,.25,.5),(0.,0.,1.),(1.,0.,0.),(0.,0.,0.)]
        self.skin.weights=[{1:1.} for _ in range(4)]
        self.skin.dq=[0.]*4
        self.skin.unnormalized=[False]*4
        class Point:
            def __init__(self,x,y,z): self.x,self.y,self.z=x,y,z
        nodes=[]
        class Intersector:
            built=False
            initialized=False
            freed=False
            def SetNode(self,node): nodes.append(node)
            def Build(self): self.built=True
            def IntersectRay(self,*args,**kwargs): self.initialized=True
            def ClosestFace(self,p,**kwargs):
                assert self.built and self.initialized and kwargs["doubleSided"]
                self.bary=Point(.5,.25,.25) if p.x else Point(1.,0.,0.)
                return True
            def GetHitFace(self): return 0
            def GetHitBary(self): return self.bary
            def Free(self): self.freed=True
        intersector=Intersector()
        deleted=[]
        self.rt.Point3=Point
        self.rt.mesh=lambda **kw:types.SimpleNamespace(isHidden=False)
        self.rt.update=lambda node:None
        self.rt.MeshProjIntersect=lambda:intersector
        self.rt.delete=deleted.append
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],1,result)
        expected={1:.5*.5+.25*.123456789,2:.5*.3+.25*.876543211+.25*2.,3:.5*.2}
        for bone,value in expected.items(): self.assertAlmostEqual(self.skin.weights[0][bone],value)
        self.assertAlmostEqual(self.skin.dq[0],.5*.123456789+.25*.25+.25*.5)
        self.assertEqual(self.skin.weights[1],{1:.5,2:.3,3:.2})
        self.assertEqual(self.skin.selected,[1,2])
        self.assertEqual(result["data"]["selected_vertices"],2)
        self.assertEqual(result["data"]["reports"][0]["direct_vertices"],2)
        self.assertTrue(intersector.freed)
        self.assertEqual(deleted,nodes)

    def test_failed_space_query_cleans_temporary_mesh_without_writing(self):
        self.export()
        self.rt.model.points=[(0.,0.,1.)]*4
        class Point:
            def __init__(self,x,y,z): self.x,self.y,self.z=x,y,z
        self.rt.Point3=Point
        temporary=types.SimpleNamespace(isHidden=False)
        self.rt.mesh=lambda **kw:temporary
        self.rt.update=lambda node:None
        freed,deleted=[],[]
        self.rt.delete=deleted.append
        self.rt.MeshProjIntersect=lambda:types.SimpleNamespace(SetNode=lambda n:None,Build=lambda:None,
            IntersectRay=lambda *args,**kw:None,ClosestFace=lambda *args,**kw:False,Free=lambda:freed.append(True))
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertIn("最近表面点",result["data"]["errors"][0]["error"])
        self.assertEqual(self.rt.writes,0)
        self.assertEqual(deleted,[temporary])
        self.assertTrue(freed)

    def test_invalid_geometry_rejected_before_native_mapping(self):
        self.export()
        with zipfile.ZipFile(self.archive) as archive: contents={n:archive.read(n) for n in archive.namelist()}
        contents["points.bin"]=np.array([float("nan")]*12,dtype="<f8").tobytes()
        with zipfile.ZipFile(self.archive,"w") as archive:
            for name,value in contents.items(): archive.writestr(name,value)
        result=middle.run_file_operation(self.client,"import_file","closest_point")
        self.assertEqual(result["data"]["count"],0,result)
        self.assertEqual(self.rt.writes,0)
        self.assertFalse(any(params.get("op")=="map" for _,params,_ in self.client.calls))

    def test_qt_card_is_independent_and_all_buttons_use_max_client(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_loader import scan
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        plugins={p.key:p for p in scan(str(ROOT/"plugins"))}
        messages=[]
        class Log:
            def info(self,text): messages.append((text,threading.current_thread().name))
            warn=error=info
        widget=plugins[("max","skin_weights")].load_factory()(self.client,Log())
        widget.show()
        app.processEvents()
        self.assertFalse(hasattr(widget,"send_btn"))
        self.assertEqual(widget.import_mode_cbox.currentData(),"vertex_id")
        self.assertEqual(widget.import_mode_cbox.itemData(1),"closest_point")
        for button in (widget.check_btn,widget.select_btn,widget.fix_btn,widget.export_btn,widget.import_btn):
            QtTest.QTest.mouseClick(button,QtCore.Qt.LeftButton)
            self.assertFalse(widget.check_btn.isEnabled())
            end=time.monotonic()+5
            while widget._task is not None and time.monotonic()<end:
                app.processEvents()
                QtTest.QTest.qWait(2)
            self.assertIsNone(widget._task)
            self.assertTrue(widget.check_btn.isEnabled())
            self.assertFalse(button.toolTip())
        self.assertTrue(messages)
        self.assertTrue(all(t==threading.current_thread().name for _,t in messages))
        self.assertTrue(all(t!=threading.current_thread().name for _,_,t in self.client.calls))
        self.assertTrue(all(name.endswith("_max") for name,_,_ in self.client.calls))
        widget.close()


if __name__=="__main__": unittest.main()
