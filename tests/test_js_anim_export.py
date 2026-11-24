"""Body routing, conversion rules and transactional export; no legacy library reads."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maya_actions import _js_anim_export as export


class FakeScene:
    def __init__(self):
        self.nodes = {'old': 'joint', 'usd': 'mayaUsdLayerManager'}
        self.selection, self.time, self.unit = ['old'], 17, 'film'
        self.current_namespace, self.namespaces = ':', set()
        self.auto, self.undo, self.modified, self.scene = True, True, False, 'original.ma'
        self.playback = dict(minTime=1, maxTime=24, animationStartTime=1, animationEndTime=24)
        self.deleted, self.unit_writes = [], []

    def ls(self, nodes=None, **kwargs):
        if kwargs.get('selection'):
            return list(self.selection)
        return list(self.nodes) if nodes is None else [node for node in nodes if node in self.nodes]

    def namespaceInfo(self, **kwargs): return self.current_namespace
    def namespace(self, **kwargs):
        if 'setNamespace' in kwargs: self.current_namespace = kwargs['setNamespace']
        if 'exists' in kwargs: return kwargs['exists'] in self.namespaces
        if 'removeNamespace' in kwargs: self.namespaces.remove(kwargs['removeNamespace'])
    def currentTime(self, value=None, **kwargs):
        if kwargs.get('query'): return self.time
        self.time = value
    def currentUnit(self, **kwargs):
        if kwargs.get('query'): return self.unit
        self.unit_writes.append(kwargs)
        self.unit = kwargs['time']
    def file(self, **kwargs):
        if kwargs.get('query'):
            return self.scene if kwargs.get('sceneName') else self.modified
        if 'rename' in kwargs: self.scene = kwargs['rename']
        if 'modified' in kwargs: self.modified = kwargs['modified']
    def autoKeyframe(self, **kwargs):
        if kwargs.get('query'): return self.auto
        self.auto = kwargs['state']
    def undoInfo(self, **kwargs):
        if kwargs.get('query'): return self.undo
        self.undo = kwargs['stateWithoutFlush']
    def playbackOptions(self, **kwargs):
        if kwargs.pop('query', False): return self.playback[next(iter(kwargs))]
        self.playback.update(kwargs)
    def select(self, nodes=None, **kwargs): self.selection = [] if kwargs.get('clear') else list(nodes)
    def nodeType(self, node): return self.nodes[node]
    def delete(self, nodes):
        self.deleted.extend(nodes)
        for node in nodes: self.nodes.pop(node)
    def pluginInfo(self, *args, **kwargs): return True


def rig(names):
    return {name: '|copy|' + name for name in names}


class ExportTests(unittest.TestCase):
    def test_identifies_all_body_prefixes_and_windows_paths(self):
        for body in export.BODIES:
            self.assertEqual(export.identify_body('C:\\动作\\'+body.lower()+'b02待机.fbx'), body)
        self.assertEqual(export.identify_body('unknown.ma', 'F1_测试.json'), 'F1')
        self.assertEqual(export.identify_body('unknown.ma', body_type='M2'), 'M2')

    def test_rejects_unknown_ambiguous_or_conflicting_bodies(self):
        for names in (('F10test.fbx',), ('unknown.ma',), ('F1a.ma', 'M1a.json')):
            with self.subTest(names=names), self.assertRaises(ValueError): export.identify_body(*names)
        with self.assertRaises(ValueError): export.identify_body('F2a.fbx', body_type='M2')

    def test_explicit_template_can_live_anywhere_and_controls_body(self):
        with tempfile.TemporaryDirectory(prefix='模板 path ') as folder:
            for body in export.BODIES:
                template = Path(folder, body.lower() + '_标准.FBX')
                template.write_bytes(b'fbx')
                self.assertEqual(export.resolve_template(template), (str(template), body))
            with self.assertRaises(ValueError):
                export.resolve_template(template, body_type='F1')

    def test_absent_or_unrecognized_template_falls_back_to_regular_scene_export(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder, 'F1_empty.fbx'); empty.touch()
            other = Path(folder, 'F1.txt'); other.write_bytes(b'fbx')
            unknown = Path(folder, 'standard.fbx'); unknown.write_bytes(b'fbx')
            for template in (None, '', ' ', folder, empty, other, unknown, Path(folder, 'M2_missing.fbx')):
                with self.subTest(template=template), \
                        patch.object(export.core, 'export_animation', return_value={}) as regular, \
                        patch.object(export, '_convert') as convert:
                    destination = str(Path(folder, 'walk.json'))
                    result = export.export_animation(destination, root_node='selected', template_path=template)
                    regular.assert_called_once_with(destination, root_node='selected', overwrite=False)
                    self.assertEqual(result['export_mode'], 'regular')
                    self.assertIsNone(result['template_path'])
                    convert.assert_not_called()

    def test_regular_batch_samples_source_without_template_conversion_and_restores_scene(self):
        scene = FakeScene()
        before = copy.deepcopy(scene.__dict__)
        modules = {'maya': types.ModuleType('maya'), 'maya.mel': types.SimpleNamespace()}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'walk.fbx'); source.write_bytes(b'fbx')
            unknown = Path(folder, 'standard.fbx'); unknown.write_bytes(b'fbx')
            with patch.dict(sys.modules, modules), patch.object(export.core, '_cmds', return_value=scene), \
                    patch.object(export, '_import_fbx', return_value=(['old'], {'Armature': 'old'}, 'source')) as load, \
                    patch.object(scene, 'keyframe', return_value=[1, 24], create=True), \
                    patch.object(export, '_convert') as convert, \
                    patch.object(export.core, 'export_animation', return_value={}) as regular, \
                    patch.object(export.core, 'read_animation', return_value={'nodes': {}}):
                for template in (None, str(unknown), str(Path(folder, 'F1_missing.fbx'))):
                    result = export.export_fbx(str(source), folder, overwrite=True, template_path=template)
                    self.assertEqual(result['export_mode'], 'regular')
                    self.assertEqual(regular.call_args.kwargs, {'root_node': 'old'})
                    self.assertEqual(load.call_args.args[2], str(source))
                self.assertEqual(load.call_count, 3)
                convert.assert_not_called()
            for key in ('selection', 'time', 'unit', 'current_namespace', 'auto', 'undo', 'modified', 'scene', 'playback'):
                self.assertEqual(getattr(scene, key), before[key], key)
            self.assertTrue(Path(folder, 'walk.json').is_file())

    def test_both_exports_use_selected_template_ignoring_motion_and_output_names(self):
        scene = FakeScene()
        scene.scene = 'M2_source.ma'
        modules = {'maya': types.ModuleType('maya'), 'maya.mel': types.SimpleNamespace()}
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder, 'F1_标准.fbx'); template.write_bytes(b'fbx')
            source = Path(folder, 'walk.fbx'); source.write_bytes(b'fbx')
            with patch.dict(sys.modules, modules), patch.object(export.core, '_cmds', return_value=scene), \
                    patch.object(export.core, '_root', return_value='old'), \
                    patch.object(export.core, '_hierarchy', return_value=['old']), \
                    patch.object(export, '_mapping', return_value={'Bip01': 'old'}), \
                    patch.object(export, '_import_fbx', return_value=(['old'], {'Bip01': 'old'}, 'source')), \
                    patch.object(scene, 'keyframe', return_value=[1, 24], create=True), \
                    patch.object(export, '_convert', side_effect=lambda *args: ({}, {'nodes': {}})) as convert:
                export.export_animation(str(Path(folder, 'M1_free_name.json')), template_path=str(template))
                export.export_fbx(str(source), folder, template_path=str(template))
                self.assertEqual(len(convert.call_args_list), 2)
                for call in convert.call_args_list:
                    self.assertEqual(call.args[4:6], (str(template), 'F1'))
            self.assertTrue(Path(folder, 'M1_free_name.json').is_file())
            self.assertTrue(Path(folder, 'walk.json').is_file())

    def test_f1_reconstructs_only_missing_main_forearms(self):
        names = ['Bip01'] + ['Bip01 '+side+part for side in ('L', 'R') for part in (' UpperArm', ' ForeTwist', ' Hand')]
        source = rig(names)
        target = rig(names + ['Bone_Allnub', 'Bip01 L Forearmnub', 'Bip01 R Forearmnub'])
        common, sides = export._plan(source, target, 'F1')
        self.assertEqual(set(common), set(names))
        self.assertEqual(sides, ['L', 'R'])
        self.assertEqual(export._plan(target, target, 'F1')[1], [])

    def test_other_bodies_use_existing_forearm_and_m1_keeps_point_wrappers(self):
        source = rig(['Bip01', 'Bip01 L Forearm', 'Bip01 R Forearm', 'Bip01 L Hand', 'Bip01 R Hand', 'ADD_L101'])
        for body in ('F2', 'M1', 'M2'):
            target = dict(source)
            target.update(rig(['Point01', 'Point02', 'Point03', 'Point04'] if body=='M1' else ['Bone_Allnub']))
            common, sides = export._plan(source, target, body)
            self.assertEqual(set(common), set(source))
            self.assertEqual(sides, [])

    def test_rejects_missing_animated_bones_instead_of_silent_static_fallback(self):
        with self.assertRaisesRegex(ValueError, '缺少节点'):
            export._plan(rig(['Bip01']), rig(['Bip01', 'Bip01 L Hand']), 'F2')
        with self.assertRaisesRegex(ValueError, '无法重建前臂'):
            export._plan(rig(['Bip01']), rig(['Bip01', 'Bip01 L Forearmnub']), 'F1')

    def test_root_discovery_handles_bip01_and_wrapped_skeletons(self):
        for root in ('|copy:Bone_Allnub', '|copy:Bip01'):
            child=root+'|copy:Hand'
            cmds=types.SimpleNamespace(listRelatives=lambda node,**kwargs: [root] if node==child else [])
            self.assertEqual(export._template_root(cmds,{'root':root,'child':child}),root)

    def test_copy_scope_restores_scene_even_on_failure_and_preserves_shared_nodes(self):
        scene=FakeScene()
        before=copy.deepcopy(scene.__dict__)
        with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
            with export._copies(scene) as namespaces:
                scene.nodes.update(new_joint='joint', new_curve='animCurveTL', new_usd='mayaUsdLayerManager')
                namespaces.append('copy');scene.namespaces.add('copy')
                scene.unit,scene.time,scene.selection='ntsc',80,['new_joint']
                scene.scene,scene.modified='changed.ma',True
                scene.playback.update(minTime=0,maxTime=80)
                raise RuntimeError('conversion failed')
        self.assertEqual(set(scene.nodes), {'old','usd','new_usd'})
        for key in ('selection','time','unit','current_namespace','auto','undo','modified','scene','playback'):
            self.assertEqual(getattr(scene,key),before[key],key)
        self.assertEqual(scene.unit_writes,[dict(time='film',updateAnimation=True)])
        self.assertEqual(scene.namespaces,set())

    def test_cleanup_failure_is_reported(self):
        scene=FakeScene()
        with self.assertRaisesRegex(RuntimeError, '清理临时节点'):
            with patch.object(scene,'delete',side_effect=RuntimeError('locked node')):
                with export._copies(scene): scene.nodes['new']='joint'
        self.assertTrue(scene.undo)
        self.assertTrue(scene.auto)

    def test_failed_cleanup_never_overwrites_existing_json(self):
        scene=FakeScene()
        @contextmanager
        def failed_cleanup(_cmds):
            yield []
            raise RuntimeError('cleanup failed')
        modules={'maya':types.ModuleType('maya'),'maya.mel':types.SimpleNamespace()}
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder,'F1_test.json');destination.write_text('original',encoding='utf-8')
            template=Path(folder,'F1.fbx');template.write_bytes(b'fbx')
            with patch.dict(sys.modules,modules), patch.object(export.core,'_cmds',return_value=scene), \
                    patch.object(export.core,'_root',return_value='old'), \
                    patch.object(export.core,'_hierarchy',return_value=['old']), \
                    patch.object(export,'_mapping',return_value={'Bip01':'old'}), \
                    patch.object(export,'_copies',failed_cleanup), \
                    patch.object(export,'_convert',return_value=({},{})):
                with self.assertRaisesRegex(RuntimeError,'cleanup failed'):
                    export.export_animation(str(destination),overwrite=True,template_path=str(template))
            self.assertEqual(destination.read_text(encoding='utf-8'),'original')

    def test_existing_output_is_rejected_before_maya_access(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'F1_test.json');path.write_text('original',encoding='utf-8')
            with patch.object(export.core,'_cmds') as cmds, self.assertRaises(FileExistsError):
                export.export_animation(str(path))
            cmds.assert_not_called()

    def test_fractional_bake_preserves_last_sample_and_euler_continuity(self):
        from unittest.mock import Mock
        scene = Mock()
        scene.getAttr.side_effect = lambda plug, **kwargs: (179 if 'time' in kwargs else -179) if '.rotate' in plug else 1.5
        export._bake(scene, ['joint'], 0, 1.5)
        self.assertEqual(scene.setKeyframe.call_count, 6)
        calls = {call.kwargs['attribute']: call.kwargs for call in scene.setKeyframe.call_args_list}
        self.assertEqual(calls['translateX']['time'], 1.5)
        self.assertEqual(calls['translateX']['value'], 1.5)
        self.assertEqual(calls['rotateX']['value'], 181)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ExportTests))
    print('PASS %d / %d' % (result.testsRun-len(result.failures)-len(result.errors),result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
