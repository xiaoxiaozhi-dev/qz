"""Offline numerical/transaction contracts plus real seven-button Qt interaction."""
import copy
import math
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from max_actions import rigging_toolbox as tool


class LogicTests(unittest.TestCase):
    def test_skin_lookup_does_not_select_or_activate_the_modifier(self):
        skin = object()
        with patch.object(tool,'_skin_modifiers',return_value=[skin]),patch.object(tool,'_activate',side_effect=AssertionError('UI activation')):
            self.assertIs(tool._skin(None,object(),exclusive=False),skin)

    def test_direct_influence_edits_skip_weight_validation_and_target_the_node(self):
        @contextmanager
        def undo(*args):
            yield
        node = types.SimpleNamespace(name='Model',handle=100)
        first = types.SimpleNamespace(handle=1)
        second = types.SimpleNamespace(handle=2)
        fresh = types.SimpleNamespace(handle=3)
        skin = object()
        removed,added = [],[]
        rt = types.SimpleNamespace(getHandleByAnim=lambda n:n.handle,Array=lambda *a:a,
            skinOps=types.SimpleNamespace(removeBone=lambda *a,**k:removed.append((a,k))))
        with patch.dict(sys.modules,{'pymxs':types.SimpleNamespace(undo=undo)}),\
             patch.object(tool,'_skin',return_value=skin),\
             patch.object(tool,'_bones',return_value=[(2,first),(7,second)]),\
             patch.object(tool,'_batch_redraw',lambda rt:undo()),\
             patch.object(tool,'_snapshot',side_effect=AssertionError('snapshot validation')),\
             patch.object(tool,'_matches',side_effect=AssertionError('read-back')),\
             patch.object(tool,'_toolbox_native',side_effect=lambda rt,name:
                          (lambda *a: (removed if name=='qzRigRemoveInfluencesV8' else added).append(a))):
            noop=tool._edit_influences(rt,node,add=[first])
            self.assertFalse(noop['changed'])
            result=tool._edit_influences(rt,node,add=[first,fresh,fresh])
            self.assertEqual(result['added'],1)
            self.assertEqual(added,[(skin,node,(fresh,))])
            result=tool._edit_influences(rt,node,remove=[1,2])
            self.assertEqual(result['removed'],2)
            self.assertEqual(removed,[(skin,node,(7,2))])

    def test_execute_version_mismatch_cannot_modify_and_matching_version_executes_once(self):
        with patch.object(tool,'run',return_value={'count':1}) as mutation:
            self.assertEqual(tool.execute('bind_pose',tool.VERSION-1),dict(version=tool.VERSION))
            mutation.assert_not_called()
            self.assertEqual(tool.execute('bind_pose',tool.VERSION),dict(version=tool.VERSION,result={'count':1}))
            mutation.assert_called_once_with('bind_pose')

    def test_pose_graph_reuses_ancestors_and_remains_local_to_one_operation(self):
        class Node:
            reads = 0
            def __init__(self,name,handle,parent=None):
                self.name,self.handle,self._parent = name,handle,parent
            @property
            def parent(self):
                self.reads += 1
                return self._parent
        root = Node('Root',1)
        child = Node('Child',2,root)
        handle_reads = []
        def read_handle(node):
            handle_reads.append(node.handle)
            return node.handle
        rt = types.SimpleNamespace(getHandleByAnim=read_handle)
        graph = tool._PoseGraph(rt)
        for _ in range(20):
            self.assertEqual(graph.path(child),('Root','Child'))
            self.assertEqual(graph.depth(child),1)
        self.assertEqual((root.reads,child.reads),(1,1))
        self.assertEqual(handle_reads,[2,1])
        root.name = 'NewSceneRoot'
        self.assertEqual(tool._PoseGraph(rt).path(child),('NewSceneRoot','Child'))

    def test_pose_noop_skips_recovery_snapshot_and_transaction(self):
        report = dict(changed=True)
        with patch.object(tool,'_atomic',side_effect=AssertionError('unneeded undo transaction')):
            result = tool._pose_transaction(None,[(None,None)],'Pose',report,
                                           graph=object(),limits=(1,1,1),changed=False)
        self.assertIs(result,report)
        self.assertFalse(result['changed'])

    def test_pose_memory_lives_outside_the_reloadable_actions_package(self):
        with patch.dict(sys.modules):
            memory = tool._pose_memory()
            self.assertIs(memory,tool._pose_memory())
            self.assertIs(sys.modules['_qz_rigging_toolbox_pose_memory'],memory)

    def test_pose_capture_contains_only_scene_independent_values(self):
        import json
        parent = types.SimpleNamespace(name='角色组',parent=None,handle=1)
        matrix = types.SimpleNamespace(row1=(1,0,0),row2=(0,1,0),row3=(0,0,1),row4=(2,3,4))
        bone = types.SimpleNamespace(name='骨骼',parent=parent,handle=2,transform=matrix)
        rt = types.SimpleNamespace(getHandleByAnim=lambda n:n.handle,
                                   units=types.SimpleNamespace(decodeValue=lambda value: .1),copy=copy.deepcopy)
        with patch.object(tool,'_rig_pose_nodes',return_value=[bone]),patch.object(tool,'_pose_kind',return_value='bone'),patch.object(tool,'_local_rotation',return_value=dict(mode=0,order=1,angles=(1.,2.,3.),quat=(0.,0.,0.,1.))):
            cached = tool._capture_pose(rt,[bone])
        self.assertEqual(cached['mode'],'local_rotation')
        self.assertEqual(cached['rows'][0]['rotation']['angles'],(1.,2.,3.))
        self.assertEqual(cached['rows'][0]['path'],('角色组','骨骼'))
        self.assertEqual(cached['rows'][0]['matrix'][-3:],(2.,3.,4.))
        self.assertEqual(json.loads(json.dumps(cached))['rows'][0]['kind'],'bone')
        self.assertNotIn('handle',repr(cached))

    def test_roundtrip_reuses_original_cache_only_for_exact_unedited_applied_pose(self):
        original = {'rows': [1., 2., 3.]}
        observed = {'rows': [1., 2., 3.00000001]}
        memory = types.SimpleNamespace(last_pose=original, applied_source=original,
                                       applied_pose=observed)
        self.assertIs(tool._reuse_applied_pose(memory, copy.deepcopy(observed)), original)
        edited = {'rows': [1., 2., 3.00000002]}
        self.assertIs(tool._reuse_applied_pose(memory, edited), edited)
        memory.last_pose = copy.deepcopy(original)
        self.assertIs(tool._reuse_applied_pose(memory, observed), observed)

    def test_pose_matching_rejects_identical_names_at_the_same_hierarchy_path(self):
        parent = types.SimpleNamespace(name='Rig',parent=None,handle=1)
        first = types.SimpleNamespace(name='Bone',parent=parent,handle=2)
        duplicate = types.SimpleNamespace(name='Bone',parent=parent,handle=3)
        rt = types.SimpleNamespace(getHandleByAnim=lambda n:n.handle)
        with self.assertRaisesRegex(ValueError,'不唯一'):
            tool._pose_index(rt,[first,duplicate])

    def test_apply_without_memory_rejects_before_accessing_scene(self):
        with patch.object(tool,'_pose_memory',return_value=types.SimpleNamespace()):
            with self.assertRaisesRegex(ValueError,'还没有缓存姿势'):
                tool._apply_last_pose(None,[])

    def test_local_rotation_quaternion_sign_and_euler_turns(self):
        first = dict(mode=1, order=0, angles=(0.,0.,0.), quat=(0.,0.,0.,1.))
        antipodal = dict(first, quat=(0.,0.,0.,-1.))
        quarter = dict(first, quat=(0.,0.,math.sqrt(.5),math.sqrt(.5)))
        self.assertAlmostEqual(tool._rotation_error(first,antipodal),0.)
        self.assertAlmostEqual(tool._rotation_error(first,quarter),90.)
        self.assertFalse(tool._rotation_changed(first,antipodal))
        euler = dict(first, mode=0, order=1)
        turned = dict(euler, angles=(0.,0.,360.))
        self.assertTrue(tool._rotation_changed(euler,turned))

    def test_old_world_pose_cache_requires_recapture_before_scene_access(self):
        memory = types.SimpleNamespace(last_pose=dict(schema=1,rows=[]))
        with patch.object(tool,'_pose_memory',return_value=memory):
            with self.assertRaisesRegex(ValueError,'旧姿势缓存'):
                tool._apply_last_pose(None,[])

    def test_redraw_batch_balances_nested_and_failed_writes_without_consuming_outer_counter(self):
        class Runtime:
            depth = 0
            def disableSceneRedraw(self):
                self.depth += 1
            def enableSceneRedraw(self):
                self.depth -= 1
        rt = Runtime()
        for outer in (0,1):
            rt.depth = outer
            with self.assertRaisesRegex(RuntimeError,'write failed'):
                with tool._batch_redraw(rt):
                    self.assertEqual(rt.depth,outer+1)
                    with tool._batch_redraw(rt):
                        self.assertEqual(rt.depth,outer+2)
                    self.assertEqual(rt.depth,outer+1)
                    raise RuntimeError('write failed')
            self.assertEqual(rt.depth,outer)

    def test_prs_pose_preserves_an_already_correct_scale_track(self):
        bone = types.SimpleNamespace(transform='before')
        with patch.object(tool, '_pose_error', return_value=(1., 20., 1e-6)), \
             patch.object(tool, '_is_biped', return_value=False), \
             patch.object(tool, '_set_prs_components', return_value=True) as components:
            tool._set_pose(None, bone, 'target', (.0002, .001, 2e-5))
        self.assertEqual(bone.transform, 'before')
        components.assert_called_once_with(None, bone, 'target', True, True)

    def test_pose_real_scale_changes_and_unsupported_controllers_use_matrix_path(self):
        for error, supported in [((1., 20., .2), True), ((1., 20., 1e-6), False)]:
            bone = types.SimpleNamespace(transform='before')
            with patch.object(tool, '_pose_error', return_value=error), \
                 patch.object(tool, '_is_biped', return_value=False), \
                 patch.object(tool, '_set_prs_components', return_value=supported):
                tool._set_pose(None, bone, 'target', (.0002, .001, 2e-5))
            self.assertEqual(bone.transform, 'target')

    def test_pose_errors_separate_distance_angle_scale_and_accept_biped_roundoff(self):
        def matrix(rows):
            return types.SimpleNamespace(**dict(zip(('row1','row2','row3','row4'),rows)))
        rows = [(1.,0.,0.),(0.,1.,0.),(0.,0.,1.),(0.,0.,0.)]
        target = matrix(rows)
        limits = (.0002,.001,2e-5)
        self.assertTrue(tool._pose_equal(matrix(rows[:3]+[(0.,.0000112,0.)]),target,limits))
        angle = math.radians(.1)
        for changed in [rows[:3]+[(.01,0.,0.)],
                        [(math.cos(angle),math.sin(angle),0.),(-math.sin(angle),math.cos(angle),0.)]+rows[2:],
                        [(1.001,0.,0.)]+rows[1:],
                        [(1.,.01,0.)]+rows[1:],
                        [(-1.,0.,0.)]+rows[1:],
                        [(0.,0.,0.)]+rows[1:],
                        rows[:3]+[(float('nan'),0.,0.)]]:
            self.assertFalse(tool._pose_equal(matrix(changed),target,limits),changed)

    def test_removal_renormalizes_normal_vertices_and_preserves_unnormalized_values(self):
        rows = [{11:.2,23:.3,99:.5},{11:.2,23:.3,99:.5}]
        original = copy.deepcopy(rows)
        result = tool._remaining_rows(rows,[False,True],{99})
        self.assertEqual(result,[{11:.4,23:.6},{11:.2,23:.3}])
        self.assertEqual(rows,original)

    def test_pruning_unused_bones_does_not_drop_tiny_positive_weights_or_renormalize(self):
        rows = [{3:1e-10,97:1.-1e-10}]
        result = tool._remaining_rows(rows,[False],{42})
        self.assertEqual(result,rows)
        self.assertIsNot(result[0],rows[0])

    def test_removing_last_effective_influence_rejects_the_entire_model(self):
        with self.assertRaisesRegex(ValueError,'顶点 2'):
            tool._remaining_rows([{1:.5,2:.5},{1:1.}],[False,False],{1})

    def test_weight_equality_checks_new_positive_influences_flags_dq_and_counts(self):
        before = dict(rows=[{1:.4,2:.6}], flags=[False],dq=[.375])
        for changed in [dict(rows=[{1:.4,2:.6,9:1e-10}],flags=[False],dq=[.375]),
                        dict(rows=[{1:.4,2:.6}],flags=[True],dq=[.375]),
                        dict(rows=[{1:.4,2:.6}],flags=[False],dq=[.5]),
                        dict(rows=[],flags=[],dq=[])]:
            self.assertFalse(tool._weights_equal(before,changed))
        self.assertTrue(tool._weights_equal(before,copy.deepcopy(before)))

    def test_nonfinite_numbers_are_not_accepted_as_equal(self):
        for value in (float('nan'),float('inf'),float('-inf')):
            self.assertFalse(tool._close(value,value))

    def test_suppressed_native_undo_exception_is_still_reported_and_restore_is_checked(self):
        @contextmanager
        def undo(*args):
            try:
                yield
            except Exception:
                pass  # Real pymxs.undo suppresses handled exceptions.
        restores = []
        def change():
            raise RuntimeError('native write failed')
        with patch.dict(sys.modules,{'pymxs':types.SimpleNamespace(undo=undo)}):
            with self.assertRaisesRegex(RuntimeError,'native write failed.*已恢复'):
                tool._atomic('test',change,lambda: restores.append(True) or True)
            with self.assertRaisesRegex(RuntimeError,'场景可能已有部分变化'):
                tool._atomic('test',change,lambda: False)
            self.assertEqual(tool._atomic('test',lambda: 'success',lambda: self.fail('unexpected restore')),'success')
        self.assertEqual(restores,[True])


class Client:
    connected = True
    def __init__(self):
        self.calls = []
        self.version = 10
        self.timeout = False
        self.delay = 0.
    def call(self,action,**kwargs):
        self.calls.append((action,kwargs,threading.current_thread()))
        if action.endswith('_preflight'):
            return dict(ok=True,data=dict(version=self.version))
        if action == 'reload_actions':
            self.version = 10
            return dict(ok=True,data={})
        if action == 'rigging_toolbox_max_execute' and kwargs['expected_version'] != self.version:
            return dict(ok=True,data=dict(version=self.version))
        if self.timeout:
            raise RuntimeError('timed out')
        time.sleep(self.delay)
        operation = kwargs['operation']
        report = dict(mesh='Body',changed=True)
        if operation in ('bind_pose','apply_last_pose'):
            report['bones'] = 3
            report['pose'] = 'last' if operation == 'apply_last_pose' else 'bind'
        elif operation != 'reset_xform':
            report.update(added=1,removed=0)
        result = dict(count=1,reports=[report],errors=[],warnings=[])
        return dict(ok=True,data=dict(version=self.version,result=result))


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        from ui.plugin_loader import scan
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)
        cls.plugin = next(p for p in scan(str(ROOT/'plugins')) if p.key == ('max','rigging_toolbox'))
        cls.factory = staticmethod(cls.plugin.load_factory())
        cls.module = cls.factory.__globals__

    def test_preflight_updates_old_actions_using_existing_client(self):
        client = Client()
        client.version = 7
        response = self.module['run_operation'](client,'transfer_influences')
        self.assertTrue(response['ok'],response)
        self.assertEqual([a for a,_,_ in client.calls],['rigging_toolbox_max_execute','reload_actions',
                                                     'rigging_toolbox_max_execute'])
        self.assertEqual(client.calls[-1][1]['operation'],'transfer_influences')

    def test_current_actions_use_one_request_and_unknown_old_entrypoint_is_safe_to_refresh(self):
        client = Client()
        self.assertTrue(self.module['run_operation'](client,'bind_pose')['ok'])
        self.assertEqual([a for a,_,_ in client.calls],['rigging_toolbox_max_execute'])
        class LegacyClient(Client):
            def call(self,action,**kwargs):
                if action=='rigging_toolbox_max_execute' and self.version<8:
                    self.calls.append((action,kwargs,threading.current_thread()))
                    return dict(ok=False,error='unknown action: rigging_toolbox_max_execute')
                return super().call(action,**kwargs)
        legacy = LegacyClient()
        legacy.version = 6
        self.assertTrue(self.module['run_operation'](legacy,'bind_pose')['ok'])
        self.assertEqual([a for a,_,_ in legacy.calls],['rigging_toolbox_max_execute','reload_actions',
                                                    'rigging_toolbox_max_execute'])

    def test_inconsistent_version_with_a_result_never_replays_the_mutation(self):
        class InconsistentClient(Client):
            def call(self,action,**kwargs):
                self.calls.append((action,kwargs,threading.current_thread()))
                return dict(ok=True,data=dict(version=6,result=dict(count=1)))
        client = InconsistentClient()
        result = self.module['run_operation'](client,'bind_pose')
        self.assertFalse(result['ok'])
        self.assertIn('可能已执行',result['error'])
        self.assertEqual([a for a,_,_ in client.calls],['rigging_toolbox_max_execute'])

    def test_bind_pose_business_failure_logs_readable_details_without_success(self):
        widget = self.factory(Client(),types.SimpleNamespace(info=lambda x: None))
        try:
            response = dict(ok=True,operation='bind_pose',data=dict(failed=True,count=0,reports=[],warnings=[],
                errors=[dict(mesh='所选模型',error='小腿（Biped）：位置差 0.5 系统单位；已恢复并读回核对')]))
            messages = widget.format_log_result('rigging_toolbox_max',response)
            self.assertTrue(any(level=='error' and '小腿' in message for level,message in messages))
            self.assertFalse(any('完成：' in message or '\\u' in message for _,message in messages))
        finally:
            widget.deleteLater()
            self.app.processEvents()

    def test_disconnected_client_stops_and_timeout_never_retries_mutation(self):
        client = Client()
        client.connected = False
        self.assertFalse(self.module['run_operation'](client,'bind_pose')['ok'])
        self.assertEqual(client.calls,[])
        client.connected,client.timeout = True,True
        result = self.module['run_operation'](client,'bind_pose')
        self.assertFalse(result['ok'])
        self.assertIn('可能仍在执行',result['error'])
        self.assertEqual(sum(a=='rigging_toolbox_max_execute' for a,_,_ in client.calls),1)

    def test_real_seven_buttons_background_execution_disable_state_and_ui_thread_logs(self):
        from PySide6 import QtCore,QtTest,QtWidgets
        logs = []
        client = Client()
        client.delay = .025
        main_thread = threading.current_thread()
        def log(message):
            logs.append((message,threading.current_thread()))
        widget = self.factory(client,types.SimpleNamespace(info=log,warn=log,error=log))
        widget.show()
        self.app.processEvents()
        try:
            self.assertEqual(self.plugin.name,'绑定工具箱')
            self.assertEqual(len(widget.findChildren(QtWidgets.QPushButton)),7)
            self.assertEqual(len(widget.findChildren(QtWidgets.QComboBox)),0)
            self.assertEqual(set(widget.buttons),set(tool.OPERATIONS))
            for operation,button in widget.buttons.items():
                self.assertFalse(button.toolTip())
                QtTest.QTest.mouseClick(button,QtCore.Qt.LeftButton)
                self.assertTrue(all(not b.isEnabled() for b in widget.buttons.values()))
                deadline = time.monotonic()+5
                while widget._task is not None and time.monotonic()<deadline:
                    QtTest.QTest.qWait(10)
                self.assertIsNone(widget._task)
                self.assertTrue(all(b.isEnabled() for b in widget.buttons.values()))
            mutations = [(params,thread) for action,params,thread in client.calls if action=='rigging_toolbox_max_execute']
            self.assertEqual([p['operation'] for p,_ in mutations],list(widget.buttons))
            self.assertTrue(all(t is not main_thread for _,t in mutations))
            self.assertTrue(all(t is main_thread for _,t in logs))
            self.assertEqual(sum('完成：处理 1 个模型' in text for text,_ in logs),7)
            self.assertTrue(any('已应用上次姿势' in text for text,_ in logs))
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
