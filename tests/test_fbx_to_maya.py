"""FBX send orchestration, host API contracts and real Qt interaction."""
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from max_actions import fbx_to_maya as max_action
from maya_actions import fbx_from_max as maya_action
from ui.fbx_transfer import run_transfer, format_messages


class Selection(list):
    @property
    def count(self):
        return len(self)


class FakeSkin:
    def __init__(self, bones):
        self.bones = bones  # system ID -> node; includes zero-weight entries


class FakeMax:
    Skin = FakeSkin

    def __init__(self):
        self.selection = Selection([types.SimpleNamespace(name='身体模型')])
        self.units = types.SimpleNamespace(SystemType='Centimeters', SystemScale=1.0)
        self.frameRate = 30
        self.animationRange = types.SimpleNamespace(start=types.SimpleNamespace(frame=-10),
                                                   end=types.SimpleNamespace(frame=100))
        self.sliderTime = types.SimpleNamespace(frame=15)
        self.FbxExporter, self.FBXEXP = object(), object()
        self.pluginManager = types.SimpleNamespace(loadClass=lambda value: None)
        self.params, self.writes, self.exports, self.reject = {}, [], [], None
        self.fail_export, self.bad_readback = False, None
        self.export_selections, self.raise_export = [], False
        self.panel_mode, self.current_modifier, self.subObjectLevel = 'create', None, 0
        self.objects = list(self.selection)
        self.modPanel = types.SimpleNamespace(getCurrentObject=lambda: self.current_modifier,
                                             setCurrentObject=self._set_modifier)
        self.refs = types.SimpleNamespace(dependsOn=lambda skin: list(skin.bones.values()))
        self.skinOps = types.SimpleNamespace(GetNumberBones=lambda skin: len(skin.bones),
            GetBoneIDByListID=lambda skin, index: sorted(skin.bones)[index - 1],
            GetBoneName=lambda skin, index, flag: skin.bones[index].name)

    def _set_modifier(self, skin, **kwargs):
        self.current_modifier = skin

    def getCommandPanelTaskMode(self): return self.panel_mode
    def setCommandPanelTaskMode(self, value): self.panel_mode = value
    def getHandleByAnim(self, node): return id(node)
    def isValidNode(self, node): return node in self.objects
    def classOf(self, modifier): return type(modifier)
    def Array(self, *nodes): return list(nodes)
    def select(self, nodes): self.selection = Selection(nodes if isinstance(nodes, list) else [nodes])
    def getNodeByName(self, name, **kwargs): return [node for node in self.objects if node.name == name]

    def FBXExporterSetParam(self, key, *values):
        self.writes.append((key, values))
        if key == self.reject:
            return 'unsupplied'
        if key == 'ResetExport':
            self.params.clear()
        else:
            self.params[key] = values[0]
        return 'OK'

    def FBXExporterGetParam(self, key):
        return 'wrong' if key == self.bad_readback else self.params[key]

    def Name(self, name):
        return name

    def exportFile(self, filepath, prompt, **kwargs):
        self.exports.append((filepath, prompt, kwargs))
        self.export_selections.append(list(self.selection))
        if self.raise_export:
            raise RuntimeError('simulated native FBX exception')
        if self.fail_export:
            return False
        Path(filepath).write_bytes(b'FBX test fixture')
        return True


class FakeMaya:
    def __init__(self):
        self.events, self.mel_commands, self.namespaces = [], [], set()
        self.modified, self.choice, self.scene = False, '取消', ''
        self.loaded, self.fail_import = False, False
        self.save_paths = None
        self.linear, self.fps, self.axis = 'cm', '24fps', 'y'
        self.roots = ['|rig', '|body']
        self.take, self.imported_animation = 0, []
        self.display_scale, self.attributes, self.viewports = None, {}, []
        self.auto_key = True
        self.panels, self.focused_panel = ['modelPanel4'], 'outlinerPanel1'
        self.locked_attributes = set()
        self.current = 7
        self.playback = dict(animationStartTime=1, animationEndTime=24, minTime=1, maxTime=24)
        self.file_fps, self.file_range, self.file_key_range = '30fps', (-10, 100), (0, 90)
        self.import_options = {}

    def eval_mel(self, command):
        self.mel_commands.append(command)
        self.events.append(('mel', command))
        if command == 'FBXResetImport;':
            self.take = 0
            self.import_options.clear()
        elif command.startswith('FBXImportSetTake -takeIndex '):
            self.take = int(command.split()[-1].rstrip(';'))
        elif command.startswith('FBXProperty '):
            self.import_options[command.split('"')[1]] = command.endswith('true;')
        else:
            parts = command.rstrip(';').split()
            self.import_options[parts[0]] = parts[-1].strip('"')

    def pluginInfo(self, *args, **kwargs):
        return self.loaded

    def loadPlugin(self, *args, **kwargs):
        self.loaded = True

    def confirmDialog(self, **kwargs):
        self.events.append(('prompt', kwargs))
        return self.choice

    def fileDialog2(self, **kwargs):
        return self.save_paths

    def file(self, *args, **kwargs):
        if kwargs.get('query'):
            return self.modified if kwargs.get('modified') else self.scene
        self.events.append(('file', args, kwargs))
        if kwargs.get('rename'):
            self.scene = kwargs['rename']
        if kwargs.get('save'):
            self.modified = False
        if kwargs.get('new'):
            self.scene, self.modified = '', False
            self.fps = '24fps'
        if kwargs.get('i'):
            if self.fail_import:
                raise RuntimeError('simulated import failure')
            assert self.import_options['Import|AdvOptGrp|UnitsGrp|DynamicScaleConversion']
            assert self.import_options['FBXImportUpAxis'] == self.axis
            # The FBX plug-in owns timing conversion, not Python scene setters.
            if kwargs.get('importFrameRate') and self.import_options.get('FBXImportSetMayaFrameRate') == 'true':
                self.fps = self.file_fps
            if kwargs.get('importTimeRange') == 'override' and self.import_options.get('FBXImportFillTimeline') == 'true':
                self.playback.update(minTime=self.file_key_range[0], maxTime=self.file_key_range[1])
                if self.import_options['Import|IncludeGrp|Animation|ExtraGrp|TimeLineSpan']:
                    self.playback.update(animationStartTime=self.file_range[0], animationEndTime=self.file_range[1])
            ns = kwargs['namespace']
            if ns != ':':
                self.namespaces.add(ns)
            self.modified = True
            # Match Maya's script-import behavior: take 0 silently skips keys.
            self.imported_animation.append(self.take == -1)
            return self.roots + ['|rig|joint', '|body|bodyShape', 'skinCluster1']

    def currentUnit(self, **kwargs):
        if kwargs.get('query'):
            return self.linear if kwargs.get('linear') else self.fps
        self.events.append(('units', kwargs))
        if 'linear' in kwargs:
            self.linear = kwargs['linear']
        if 'time' in kwargs:
            self.fps = kwargs['time']

    def upAxis(self, **kwargs):
        if kwargs.get('query'):
            return self.axis
        self.events.append(('axis', kwargs))
        self.axis = kwargs['axis']

    def namespace(self, **kwargs):
        return kwargs['exists'] in self.namespaces

    def ls(self, nodes, **kwargs):
        if kwargs.get('type') == 'joint':
            return [node for node in nodes if node.endswith('|joint')]
        return self.roots + ['|rig|joint']

    def listRelatives(self, node, **kwargs):
        return ['|rig'] if node == '|rig|joint' else []

    def select(self, roots=None, **kwargs):
        self.events.append(('select', roots, kwargs))
        self.selected = [] if kwargs.get('clear') else roots

    def autoKeyframe(self, **kwargs):
        self.auto_key = kwargs['state']

    def jointDisplayScale(self, value):
        self.display_scale = value

    def setAttr(self, name, value):
        if name in self.locked_attributes:
            raise RuntimeError('attribute is locked')
        self.attributes[name] = value

    def getAttr(self, name):
        return self.attributes.get(name, 0)

    def getPanel(self, **kwargs):
        return self.focused_panel if kwargs.get('withFocus') else self.panels

    def modelEditor(self, panel, **kwargs):
        self.viewports.append((panel, kwargs))

    def viewFit(self, **kwargs):
        self.events.append(('view_fit', kwargs))

    def playbackOptions(self, **kwargs):
        if kwargs.get('query'):
            return self.playback['minTime' if kwargs.get('minTime') else 'maxTime']
        self.events.append(('playback', kwargs))
        self.playback.update(kwargs)

    def currentTime(self, value):
        self.events.append(('time', value))
        self.current = value


class Client:
    connected = True

    def __init__(self, dcc):
        self.dcc, self.calls, self.fail = dcc, [], None

    def call(self, action, timeout=10, connect_timeout=None, execution_timeout=None, **params):
        self.calls.append((action, params, threading.current_thread()))
        if self.fail == action:
            return dict(ok=False, error='simulated failure')
        try:
            actions = {'fbx_export_maya': max_action.export_maya,
                       'fbx_export_maya_preflight': max_action.export_preflight,
                       'fbx_maya_preflight': maya_action.preflight,
                       'fbx_receive_max': maya_action.receive_max}
            return dict(ok=True, data=actions[action](**params))
        except Exception as exc:
            return dict(ok=False, error=str(exc))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.max, self.maya = FakeMax(), FakeMaya()
        self.max_client, self.maya_client = Client('max'), Client('maya')
        self.patch_modules = patch.dict(sys.modules, {
            'pymxs': types.SimpleNamespace(runtime=self.max),
            'maya': types.ModuleType('maya'), 'maya.cmds': self.maya,
            'maya.mel': types.SimpleNamespace(eval=self.maya.eval_mel)})
        self.patch_modules.start()
        self.paths = patch('ui.fbx_transfer.paths.local_data_dir', return_value=str(self.directory))
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.patch_modules.stop()
        self.temp.cleanup()

    def fbx(self):
        filepath = self.directory / "角色 '测试'.fbx"
        filepath.write_bytes(b'fbx')
        return str(filepath)

    def test_complete_import_uses_native_fbx_settings_without_editing_units_axis_or_time(self):
        self.max.units.SystemType = 'Meters'
        original = list(self.max.selection)
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.max.selection, original)
        self.assertEqual(self.maya.linear, 'cm')
        self.assertEqual((self.maya.axis, self.maya.fps), ('y', '30fps'))
        self.assertEqual(self.maya.playback['animationStartTime'], -10)
        self.assertEqual(self.maya.playback['minTime'], 0)
        self.assertEqual(self.maya.current, 7)
        self.assertFalse(any(event[0] in ('units', 'axis', 'time', 'playback') for event in self.maya.events))
        self.assertEqual(result['data']['scene'], dict(linear_unit='cm', time_unit='30fps',
                                                     up_axis='y', start=0, end=90))
        self.assertEqual(self.maya.selected, [])
        self.assertEqual(self.max.exports[0][2]['selectedOnly'], True)
        self.assertFalse(list(self.directory.iterdir()), 'Successful transfer should release FBX')
        self.assertTrue(result['data']['namespace'].startswith('max_'))

    def test_successful_send_frames_all_after_import_with_or_without_display_optimization(self):
        for mode in ('open', 'import'):
            for optimize in (True, False):
                with self.subTest(mode=mode, optimize_display=optimize):
                    self.maya.events.clear()
                    result = run_transfer(self.max_client, self.maya_client, mode,
                                          optimize_display=optimize)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(self.maya.events[-1], ('view_fit', dict(allObjects=True)))
                    self.assertEqual(sum(event[0] == 'view_fit' for event in self.maya.events), 1)

    def test_view_fit_failure_warns_but_keeps_successful_import_and_releases_fbx(self):
        with patch.object(self.maya, 'viewFit', side_effect=RuntimeError('no active view')):
            result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertTrue(any('框选全部未完成' in warning for warning in result['data']['warnings']))
        self.assertFalse(list(self.directory.iterdir()))

    def test_settings_enable_skin_and_bake_but_exclude_other_payloads(self):
        max_action.export_maya(str(self.directory / 'test.fbx'))
        for key in ('Skin', 'Animation', 'BakeAnimation', 'BakeResampleAnimation'):
            self.assertIs(self.max.params[key], True)
        for key in ('Cameras', 'Lights', 'EmbedTextures', 'PointCache', 'Shape'):
            self.assertIs(self.max.params[key], False)
        self.assertEqual(self.max.params['UpAxis'], 'Z')
        self.assertEqual(self.max.params['ConvertUnit'], 'cm')
        self.assertEqual(self.max.params['BakeFrameStart'], -10)
        self.assertFalse(any(key in ('PushSettings', 'PopSettings') for key, _ in self.max.writes))

    def test_max_export_unit_mapping_stays_local_to_exporter(self):
        for kind, unit in (('Millimeters', 'mm'), ('Centimeters', 'cm'), ('Meters', 'm'),
                           ('Kilometers', 'km'), ('Inches', 'in'), ('Feet', 'ft'), ('Miles', 'mi')):
            self.max.units.SystemType = kind
            max_action.export_maya(str(self.directory / (kind + '.fbx')))
            self.assertEqual(self.max.params['ConvertUnit'], unit)

    def test_only_adjustable_switches_cross_the_bridge(self):
        result = run_transfer(self.max_client, self.maya_client, 'import', animation=False, optimize_display=False)
        self.assertTrue(result['ok'], result)
        exported = next(args for action, args, _ in self.max_client.calls if action == 'fbx_export_maya')
        received = next(args for action, args, _ in self.maya_client.calls if action == 'fbx_receive_max')
        self.assertEqual(set(exported), {'filepath', 'animation'})
        self.assertEqual(set(received), {'filepath', 'mode', 'animation', 'optimize_display'})
        self.assertIs(exported['animation'], False)
        self.assertIs(received['optimize_display'], False)

    def test_old_max_exporter_reloads_before_any_export(self):
        original, events = self.max_client.call, []
        def call(action, **params):
            events.append(action)
            if action == 'fbx_export_maya_preflight' and len(events) == 1:
                return dict(ok=False, error='unknown action: ' + action)
            if action == 'reload_actions':
                self.assertFalse(self.max.exports)
                return dict(ok=True, data=[])
            return original(action, **params)
        with patch.object(self.max_client, 'call', side_effect=call):
            result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertEqual(events[:3], ['fbx_export_maya_preflight', 'reload_actions', 'fbx_export_maya_preflight'])
        self.assertEqual(len(self.max.exports), 1)

    def test_invalid_max_exporter_contract_after_reload_stops_before_files(self):
        with patch.object(self.max_client, 'call', return_value=dict(ok=True, data={})):
            result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertFalse(result['ok'])
        self.assertFalse(self.max.exports)
        self.assertFalse(self.maya.events)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_static_export_does_not_read_unused_scene_metadata_or_frame_range(self):
        del self.max.frameRate, self.max.sliderTime, self.max.animationRange
        del self.max.units.SystemScale
        result = run_transfer(self.max_client, self.maya_client, 'import', animation=False)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('BakeFrameStart', self.max.params)

    def test_invalid_animation_range_fails_before_resetting_exporter(self):
        self.max.animationRange.start.frame = float('nan')
        with self.assertRaises(ValueError):
            max_action.export_maya(str(self.directory / 'invalid.fbx'))
        self.assertFalse(self.max.writes)
        self.assertFalse(self.max.exports)

    def test_animation_checkbox_controls_take_for_open_and_import_after_reset(self):
        for mode in ('open', 'import'):
            for enabled in (True, False):
                with self.subTest(mode=mode, animation=enabled):
                    self.maya.take = 0 if enabled else -1
                    result = run_transfer(self.max_client, self.maya_client, mode,
                                          animation=enabled)
                    self.assertTrue(result['ok'], result)
                    self.assertIs(self.max.params['Animation'], enabled)
                    self.assertIs(self.maya.imported_animation[-1], enabled)
                    received = [call for call in self.maya_client.calls if call[0] == 'fbx_receive_max'][-1]
                    self.assertIs(received[1]['animation'], enabled)
                    self.assertEqual(self.maya.mel_commands[-1],
                                     'FBXImportSetTake -takeIndex {};'.format(-1 if enabled else 0))
                    imported = [event for event in self.maya.events
                                if event[0] == 'file' and event[2].get('i')][-1]
                    self.assertEqual(imported[2]['options'], 'fbx')

    def test_display_option_applies_only_to_new_joints_for_both_modes(self):
        for mode in ('open', 'import'):
            with self.subTest(mode=mode):
                self.maya.attributes['|existing|joint.radius'] = 3
                self.maya.attributes['|existing|joint.drawStyle'] = 2
                self.maya.attributes['|rig|joint.drawStyle'] = 2
                event_start = len(self.maya.events)
                result = run_transfer(self.max_client, self.maya_client, mode)
                self.assertTrue(result['ok'], result)
                self.assertEqual(self.maya.display_scale, .3)
                self.assertEqual(self.maya.attributes, {'|existing|joint.radius': 3,
                    '|existing|joint.drawStyle': 2, '|rig|joint.drawStyle': 0,
                    '|rig|joint.radius': 1, 'hardwareRenderingGlobals.multiSampleEnable': True})
                self.assertFalse(self.maya.auto_key)
                self.assertEqual(self.maya.selected, [])
                self.assertEqual(self.maya.viewports[-1], ('modelPanel4', dict(edit=True,
                    jointXray=True, useDefaultMaterial=True, wireframeOnShaded=True)))
                self.assertEqual(self.maya.current, 7)
                self.assertEqual(self.maya.playback['minTime'], 0)
                self.assertEqual([event[1] for event in self.maya.events[event_start:] if event[0] == 'select'], [None])
                self.assertFalse(result['data']['warnings'])

    def test_native_timing_comes_from_file_without_source_metadata_in_both_modes(self):
        for mode, unit, axis in (('open', 'cm', 'y'), ('import', 'm', 'z')):
            with self.subTest(mode=mode):
                self.maya.linear, self.maya.axis = unit, axis
                result = maya_action.receive_max(self.fbx(), mode=mode)
                self.assertNotIn('source', result)
                self.assertEqual((self.maya.linear, self.maya.axis), (unit, axis))
                self.assertEqual(self.maya.fps, '30fps')
                self.assertEqual(self.maya.current, 7)
                self.assertEqual(self.maya.playback, dict(animationStartTime=-10,
                    animationEndTime=100, minTime=0, maxTime=90))
                self.assertEqual(self.maya.import_options['FBXImportResamplingRateSource'], 'File')
                self.assertEqual(result['scene']['start'], 0)
                imported = [event for event in self.maya.events if event[0] == 'file' and event[2].get('i')][-1]
                self.assertIs(imported[2]['importFrameRate'], True)
                self.assertEqual(imported[2]['importTimeRange'], 'override')
                self.assertFalse(any(event[0] in ('units', 'axis', 'time', 'playback') for event in self.maya.events))

    def test_static_transfer_preserves_scene_timing_and_has_no_animation_take(self):
        before = self.maya.playback.copy()
        result = run_transfer(self.max_client, self.maya_client, 'import', animation=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.maya.fps, self.maya.current), ('24fps', 7))
        self.assertEqual(self.maya.playback, before)
        self.assertEqual(self.maya.take, 0)
        self.assertEqual(self.maya.import_options['FBXImportFillTimeline'], 'false')
        self.assertEqual(self.maya.import_options['FBXImportSetMayaFrameRate'], 'false')
        self.assertFalse(self.maya.import_options['Import|IncludeGrp|Animation|ExtraGrp|TimeLineSpan'])
        imported = next(event for event in self.maya.events if event[0] == 'file' and event[2].get('i'))
        self.assertIs(imported[2]['importFrameRate'], False)
        self.assertEqual(imported[2]['importTimeRange'], 'keep')

    def test_open_reapplies_file_fps_after_new_scene_resets_manual_fps(self):
        self.maya.fps = '30fps'
        original = self.maya.file
        stages = []
        def file(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('new') or kwargs.get('i'):
                stages.append(('new' if kwargs.get('new') else 'import', self.maya.fps))
            return result
        with patch.object(self.maya, 'file', side_effect=file):
            result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertTrue(result['ok'], result)
        self.assertEqual(stages, [('new', '24fps'), ('import', '30fps')])
        self.assertFalse(any(event[0] in ('units', 'axis', 'time', 'playback') for event in self.maya.events))

    def test_log_counts_scene_objects_and_omits_internal_nodes_and_scene_settings(self):
        for mode, label in (('open', '打开'), ('import', '导入')):
            with self.subTest(mode=mode):
                result = run_transfer(self.max_client, self.maya_client, mode)
                self.assertTrue(result['ok'], result)
                # Two roots plus one child joint; shape and skinCluster are not objects.
                self.assertEqual(result['data']['objects'], 3)
                self.assertEqual(result['data']['count'], 2)
                self.assertEqual(result['data']['nodes'], 5)
                messages = '\n'.join(message for _, message in format_messages(result))
                self.assertIn(label + '到 Maya 完成：Max 选中 1 个对象，Maya 新增 3 个对象。', messages)
                for text in ('个节点', 'Maya 实际设置', '长度单位', '-up', '时间单位', '播放范围'):
                    self.assertNotIn(text, messages)

    def test_draw_style_only_writes_nonzero_values_and_warns_on_locked_changes(self):
        attribute = '|rig|joint.drawStyle'
        for style, locked in ((0, True), (1, False), (2, False), (2, True)):
            with self.subTest(style=style, locked=locked):
                self.maya.attributes[attribute] = style
                self.maya.locked_attributes = {attribute} if locked else set()
                result = run_transfer(self.max_client, self.maya_client, 'import')
                self.assertTrue(result['ok'], result)
                blocked = locked and style != 0
                self.assertEqual(self.maya.attributes[attribute], style if blocked else 0)
                self.assertEqual(len(result['data']['warnings']), int(blocked))
                if blocked:
                    self.assertIn('drawStyle', result['data']['warnings'][0])
                self.assertFalse(list(self.directory.iterdir()))

    def test_display_option_off_preserves_display_and_selects_imported_roots(self):
        result = run_transfer(self.max_client, self.maya_client, 'import',
                              optimize_display=False)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(self.maya.display_scale)
        self.assertFalse(self.maya.attributes)
        self.assertFalse(self.maya.viewports)
        self.assertTrue(self.maya.auto_key)
        self.assertEqual(self.maya.selected, ['|rig', '|body'])
        exported = next(call[1] for call in self.max_client.calls if call[0] == 'fbx_export_maya')
        self.assertEqual(set(exported), {'filepath', 'animation'})

    def test_display_failures_warn_without_failing_transfer_or_retaining_fbx(self):
        self.maya.locked_attributes.add('|rig|joint.radius')
        self.maya.panels = []
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['data']['warnings']), 2)
        self.assertFalse(self.maya.auto_key)
        self.assertEqual(self.maya.selected, [])
        self.assertFalse(list(self.directory.iterdir()))

    def test_display_uses_focused_model_panel_and_falls_back_if_panel4_missing(self):
        self.maya.panels = ['customView', 'modelPanel4']
        self.maya.focused_panel = 'customView'
        self.assertTrue(run_transfer(self.max_client, self.maya_client, 'import')['ok'])
        self.assertEqual(self.maya.viewports[-1][0], 'customView')
        self.maya.panels = ['otherView']
        self.assertTrue(run_transfer(self.max_client, self.maya_client, 'import')['ok'])
        self.assertEqual(self.maya.viewports[-1][0], 'otherView')

    def test_invalid_display_setting_stops_before_export(self):
        for value in (1, None, 'yes', {}):
            result = run_transfer(self.max_client, self.maya_client, 'import', optimize_display=value)
            self.assertFalse(result['ok'])
        self.assertFalse(self.max_client.calls)

    def add_skin_fixture(self):
        model = self.max.selection[0]
        parent = types.SimpleNamespace(name='outside_parent')
        used = types.SimpleNamespace(name='骨骼', parent=parent)
        zero = types.SimpleNamespace(name='零权重节点', parent=parent)
        other = types.SimpleNamespace(name='unrelated_controller')
        model.modifiers = [FakeSkin({1: used, 7: zero})]
        self.max.objects.extend([parent, used, zero, other])
        return model, used, zero, parent, other

    def test_adds_all_skin_entries_including_zero_weights_and_sparse_ids_but_no_parents(self):
        model, used, zero, parent, other = self.add_skin_fixture()
        result = max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual(self.max.export_selections[0], [model, used, zero])
        self.assertEqual(list(self.max.selection), [model])
        self.assertEqual(self.max.panel_mode, 'create')
        self.assertEqual((result['count'], result['exported_count']), (1, 3))
        self.assertEqual(result['added_influence_count'], 2)
        self.assertEqual(result['skin_influence_count'], 2)
        self.assertNotIn(parent, self.max.export_selections[0])
        self.assertNotIn(other, self.max.export_selections[0])

    def test_shared_influences_and_already_selected_bone_are_not_duplicated(self):
        model, used, zero, _, _ = self.add_skin_fixture()
        second = types.SimpleNamespace(name='second_mesh', modifiers=[FakeSkin({3: used, 9: zero})])
        self.max.objects.append(second)
        self.max.select([model, used, second])
        result = max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual(self.max.export_selections[0], [model, used, second, zero])
        self.assertEqual(list(self.max.selection), [model, used, second])
        self.assertEqual((result['added_influence_count'], result['skin_count']), (1, 2))

    def test_same_name_elsewhere_uses_actual_skin_reference(self):
        model, used, _, _, _ = self.add_skin_fixture()
        duplicate = types.SimpleNamespace(name=used.name, parent=None)
        self.max.objects.append(duplicate)
        result = max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual(result['added_influence_count'], 2)
        self.assertTrue(any(node is used for node in self.max.export_selections[0]))
        self.assertFalse(any(node is duplicate for node in self.max.export_selections[0]))

    def test_native_export_exception_restores_selection_modifier_and_subobject_level(self):
        model, _, _, _, _ = self.add_skin_fixture()
        original_modifier = object()
        self.max.panel_mode, self.max.current_modifier, self.max.subObjectLevel = 'modify', original_modifier, 1
        self.max.raise_export = True
        with self.assertRaisesRegex(RuntimeError, 'native FBX'):
            max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual(list(self.max.selection), [model])
        self.assertIs(self.max.current_modifier, original_modifier)
        self.assertEqual(self.max.subObjectLevel, 1)

    def test_ambiguous_skin_nodes_fail_before_export_and_restore_original_selection(self):
        model, used, _, _, _ = self.add_skin_fixture()
        duplicate = types.SimpleNamespace(name=used.name, parent=None)
        self.max.objects.append(duplicate)
        self.max.refs.dependsOn = lambda skin: []
        with self.assertRaisesRegex(ValueError, '无法唯一定位'):
            max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertFalse(self.max.exports)
        self.assertEqual(list(self.max.selection), [model])
        self.assertEqual(self.max.panel_mode, 'create')

    def test_bake_uses_scene_range_fixed_step_and_reports_no_metadata(self):
        self.max.animationRange.start.frame = -20.5
        self.max.animationRange.end.frame = 60.25
        result = max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual((self.max.params['BakeFrameStart'], self.max.params['BakeFrameEnd'],
                          self.max.params['BakeFrameStep']), (-21, 61, 1))
        self.assertNotIn('source', result)
        self.assertNotIn('settings', result)
        self.assertTrue(any('子帧' in warning for warning in result['warnings']))

    def test_no_animation_disables_bake_but_keeps_skin(self):
        max_action.export_maya(str(self.directory / 'test.fbx'), animation=False)
        self.assertTrue(self.max.params['Skin'])
        self.assertFalse(self.max.params['Animation'])
        self.assertFalse(self.max.params['BakeResampleAnimation'])

    def test_undefined_subobject_level_in_create_panel_is_supported(self):
        self.max.subObjectLevel = None
        result = max_action.export_maya(str(self.directory / 'test.fbx'))
        self.assertEqual(result['added_influence_count'], 0)
        self.assertIsNone(self.max.subObjectLevel)

    def test_no_selection_stops_before_export_settings_and_maya_mutation(self):
        self.max.selection.clear()
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertFalse(result['ok'])
        self.assertFalse(self.max.writes)
        self.assertFalse(self.maya.events)
        self.assertNotIn('仍在执行', result['error'])

    def test_missing_connection_creates_no_files(self):
        self.maya_client.connected = False
        result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertFalse(result['ok'])
        self.assertFalse(self.max_client.calls)
        self.assertFalse(list(self.directory.iterdir()))

    def test_preflight_failure_stops_export(self):
        self.maya_client.fail = 'fbx_maya_preflight'
        self.assertFalse(run_transfer(self.max_client, self.maya_client, 'import')['ok'])
        self.assertFalse(self.max.exports)

    def test_stale_maya_receiver_is_reloaded_before_any_export(self):
        original = self.maya_client.call
        events, preflights = [], []
        def call(action, **kwargs):
            events.append(action)
            if action == 'fbx_maya_preflight':
                preflights.append(action)
                if len(preflights) == 1:
                    return dict(ok=True, data=dict(ready=True))
            if action == 'reload_actions':
                self.assertFalse(self.max.exports)
                self.assertFalse(self.maya.events)
                return dict(ok=True, data=['fbx_receive_max', 'fbx_maya_preflight'])
            return original(action, **kwargs)
        with patch.object(self.maya_client, 'call', side_effect=call):
            result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertTrue(result['ok'], result)
        self.assertEqual(events[:3], ['fbx_maya_preflight', 'reload_actions', 'fbx_maya_preflight'])
        self.assertEqual(events.count('fbx_receive_max'), 1)

    def test_wrong_receiver_version_after_reload_stops_before_scene_changes(self):
        def call(action, **kwargs):
            return dict(ok=True, data=dict(ready=True))
        with patch.object(self.maya_client, 'call', side_effect=call):
            result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertFalse(result['ok'])
        self.assertIn('版本未更新', result['error'])
        self.assertFalse(self.max.exports)
        self.assertFalse(self.maya.events)

    def test_version_three_receiver_without_object_count_is_refreshed_before_export(self):
        original = self.maya_client.call
        calls = []
        def call(action, **kwargs):
            calls.append(action)
            if action == 'fbx_maya_preflight' and len(calls) == 1:
                return dict(ok=True, data=dict(receiver_version=3, open_policy='discard_unsaved',
                    animation_take_policy='explicit', display_settings_version=1,
                    scene_settings_policy='fbx_native'))
            if action == 'reload_actions':
                self.assertFalse(self.max.exports)
                return dict(ok=True, data=[])
            return original(action, **kwargs)
        with patch.object(self.maya_client, 'call', side_effect=call):
            result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertEqual(calls[:3], ['fbx_maya_preflight', 'reload_actions', 'fbx_maya_preflight'])

    def test_required_parameter_failure_stops_export(self):
        self.max.reject = 'Skin'
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertFalse(result['ok'])
        self.assertFalse(self.max.exports)
        self.assertFalse(self.maya.events)

    def test_wrong_parameter_readback_stops_export(self):
        self.max.bad_readback = 'UpAxis'
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertFalse(result['ok'])
        self.assertFalse(self.max.exports)

    def test_optional_unsupported_setting_is_logged(self):
        self.max.reject = 'GenerateLog'
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertTrue(any('GenerateLog' in msg for msg in result['data']['warnings']))

    def test_failed_export_never_opens_maya(self):
        self.max.fail_export = True
        result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertFalse(result['ok'])
        self.assertFalse(self.maya.events)

    def test_failed_receive_retains_fbx_and_never_retries(self):
        self.maya.fail_import = True
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertFalse(result['ok'])
        self.assertFalse(any(event[0] == 'view_fit' for event in self.maya.events))
        self.assertTrue((Path(result['retained_path']) / 'selection.fbx').exists())
        self.assertEqual(len(self.max.exports), 1)
        self.assertEqual(sum(call[0] == 'fbx_receive_max' for call in self.maya_client.calls), 1)

    def test_ambiguous_timeout_retains_file_and_does_not_retry(self):
        original = self.maya_client.call
        def call(action, **kwargs):
            if action == 'fbx_receive_max':
                raise RuntimeError('Maya bridge 已连接但没有返回内容')
            return original(action, **kwargs)
        with patch.object(self.maya_client, 'call', side_effect=call):
            result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertFalse(result['ok'])
        self.assertIn('仍在执行', result['error'])
        self.assertTrue((Path(result['retained_path']) / 'selection.fbx').exists())
        self.assertEqual(len(self.max.exports), 1)

    def test_open_discards_unsaved_scene_without_prompt_or_save(self):
        self.maya.modified = True
        self.maya.scene = str(self.directory / 'original.ma')
        result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertTrue(result['ok'], result)
        files = [event for event in self.maya.events if event[0] == 'file']
        self.assertTrue(files[0][2]['new'])
        self.assertTrue(files[0][2]['force'])
        self.assertEqual(files[1][2]['namespace'], ':')
        self.assertEqual(self.maya.scene, '')
        self.assertTrue(self.maya.modified)
        self.assertFalse(any(event[0] == 'prompt' for event in self.maya.events))
        self.assertFalse(any(event[2].get('save') for event in files))

    def test_open_never_queries_save_dialog(self):
        self.maya.modified = True
        self.maya.confirmDialog = lambda **kwargs: self.fail('Open must never prompt')
        self.maya.fileDialog2 = lambda **kwargs: self.fail('Open must never ask for a save path')
        result = run_transfer(self.max_client, self.maya_client, 'open')
        self.assertTrue(result['ok'], result)
        self.assertFalse(any(event[0] in ('units', 'axis', 'time', 'playback') for event in self.maya.events))

    def test_repeated_import_uses_unique_namespace(self):
        filepath = self.fbx()
        first = maya_action.receive_max(filepath, namespace='max_test')
        second = maya_action.receive_max(filepath, namespace='max_test')
        self.assertEqual((first['namespace'], second['namespace']), ('max_test', 'max_test_1'))
        self.assertIn('FBXImportMode -v "add";', self.maya.mel_commands)

    def test_quoted_unicode_path_is_never_evaluated_as_mel(self):
        filepath = self.fbx()
        maya_action.receive_max(filepath)
        self.assertFalse(any(filepath in command for command in self.maya.mel_commands))
        imported = next(event for event in self.maya.events if event[0] == 'file')
        self.assertEqual(imported[1][0], filepath)

    def test_missing_file_or_invalid_switch_does_not_touch_scene(self):
        for filepath, options in ((str(self.directory / 'missing.fbx'), {}),
                                  (self.fbx(), dict(animation=1)),
                                  (self.fbx(), dict(optimize_display='false'))):
            with self.assertRaises(ValueError):
                maya_action.receive_max(filepath, mode='open', **options)
        self.assertFalse(self.maya.events)

    def test_custom_source_unit_does_not_force_maya_unit_or_report_a_manual_scale(self):
        self.max.units.SystemScale = 10
        self.maya.linear, self.maya.axis = 'mm', 'z'
        result = run_transfer(self.max_client, self.maya_client, 'import')
        self.assertTrue(result['ok'], result)
        self.assertNotIn('source', result['data'])
        self.assertEqual(self.max.params['ConvertUnit'], 'cm')
        self.assertNotIn('source', next(args for action, args, _ in self.maya_client.calls
                                       if action == 'fbx_receive_max'))
        self.assertEqual(self.maya.linear, 'mm')
        self.assertEqual(self.maya.axis, 'z')
        self.assertEqual(result['data']['scene']['linear_unit'], 'mm')
        self.assertFalse(result['data']['warnings'])

    def test_existing_export_path_is_not_overwritten(self):
        filepath = self.fbx()
        with self.assertRaises(ValueError):
            max_action.export_maya(filepath)
        self.assertEqual(Path(filepath).read_bytes(), b'fbx')

    def test_invalid_animation_stops_before_export_or_scene_changes(self):
        for value in (1, None, 'false', {}):
            with self.assertRaises(ValueError):
                max_action.export_maya(str(self.directory / 'test.fbx'), animation=value)
            self.assertFalse(run_transfer(self.max_client, self.maya_client, 'open', animation=value)['ok'])
        self.assertFalse(self.max.writes)
        self.assertFalse(self.maya.events)


class DistributionTests(unittest.TestCase):
    def test_retired_external_module_is_removed_on_distribution_refresh(self):
        import build
        self.assertNotIn('fbx_transfer_settings.py', build.COPY_FILES)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / build.APP_NAME
            output.mkdir()
            for name in ('fbx_transfer_settings.py', 'fbx_transfer_settings.pyc', 'unrelated.txt'):
                (output / name).write_text('fixture', encoding='utf-8')
            with patch.object(build, 'STAGE_DIST', directory):
                build._clean()
            self.assertFalse((output / 'fbx_transfer_settings.py').exists())
            self.assertFalse((output / 'fbx_transfer_settings.pyc').exists())
            self.assertTrue((output / 'unrelated.txt').is_file())


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)
        from ui.plugin_loader import scan
        cls.info = next(info for info in scan(str(ROOT / 'plugins'))
                        if info.key == ('max', 'fbx_to_maya'))
        cls.factory = staticmethod(cls.info.load_factory())

    def setUp(self):
        from PySide6 import QtCore
        self.temp = tempfile.TemporaryDirectory()
        self.store = QtCore.QSettings(str(Path(self.temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
        self.settings_patch = patch('PySide6.QtCore.QSettings', return_value=self.store)
        self.settings_patch.start()
        self.logs, self.calls = [], []
        def log(message):
            self.logs.append((threading.current_thread(), message))
        self.log = types.SimpleNamespace(info=log, warn=log, error=log)
        self.widget = self.factory(Client('max'), self.log)
        self.widget.set_dcc_clients({'maya': Client('maya')})
        self.widget.show()
        self.app.processEvents()

    def tearDown(self):
        if self.widget._dialog is not None:
            self.widget._dialog.close()
            self.app.processEvents()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def pump_until_done(self):
        from PySide6 import QtTest
        deadline = time.monotonic() + 5
        while self.widget._task is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.widget._task)

    def test_both_buttons_dispatch_in_background_and_log_on_ui_thread(self):
        from PySide6 import QtCore, QtTest
        def run(max_client, maya_client, mode, animation, optimize_display):
            self.calls.append((mode, animation, threading.current_thread(), optimize_display))
            time.sleep(.03)
            return dict(ok=True, mode=mode, data=dict(nodes=5), elapsed_seconds=.03)
        with patch.dict(self.factory.__globals__, run_transfer=run):
            for button in (self.widget.open_button, self.widget.import_button):
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                self.assertFalse(self.widget.open_button.isEnabled())
                self.assertFalse(self.widget.import_button.isEnabled())
                self.assertFalse(self.widget.settings_button.isEnabled())
                self.pump_until_done()
        self.assertEqual([call[0] for call in self.calls], ['open', 'import'])
        self.assertTrue(all(call[2] is not threading.current_thread() for call in self.calls))
        self.assertTrue(all(call[1] is True and call[3] is True for call in self.calls))
        self.assertTrue(all(thread is threading.current_thread() for thread, _ in self.logs))

    def test_settings_checkbox_auto_saves_on_escape_and_reloads(self):
        from PySide6 import QtCore, QtTest
        QtTest.QTest.mouseClick(self.widget.settings_button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        dialog = self.widget._dialog
        QtTest.QTest.qWait(20)
        check = dialog.animation_checkbox
        QtTest.QTest.mouseClick(check, QtCore.Qt.LeftButton, pos=QtCore.QPoint(8, check.height() // 2))
        self.assertFalse(check.isChecked())
        self.assertEqual(self.store.value('export_settings'), None)
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        self.app.processEvents()
        self.assertIsNone(self.widget._dialog)
        self.assertFalse(self.widget._settings['animation'])
        self.assertEqual(json.loads(self.store.value('export_settings')), {'animation': False})
        other = self.factory(Client('max'), self.log)
        self.assertFalse(other._settings['animation'])
        other.deleteLater()

    def test_display_checkbox_saves_and_dispatches_disabled_value(self):
        from PySide6 import QtCore, QtTest
        QtTest.QTest.mouseClick(self.widget.settings_button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        dialog = self.widget._dialog
        check = dialog.display_checkbox
        self.assertTrue(check.isChecked())
        QtTest.QTest.mouseClick(check, QtCore.Qt.LeftButton, pos=QtCore.QPoint(8, check.height() // 2))
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(json.loads(self.store.value('import_settings')), dict(optimize_display=False))
        other = self.factory(Client('max'), self.log)
        self.assertFalse(other._import_settings['optimize_display'])
        other.deleteLater()
        def run(*args):
            self.calls.append(args)
            return dict(ok=True, mode=args[2], data={}, elapsed_seconds=0)
        with patch.dict(self.factory.__globals__, run_transfer=run):
            QtTest.QTest.mouseClick(self.widget.import_button, QtCore.Qt.LeftButton)
            self.pump_until_done()
        self.assertIs(self.calls[0][4], False)

    def test_card_row_icon_and_minimal_popup(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        self.assertEqual(self.info.name, '选中物体发送到Maya')
        buttons = (self.widget.open_button, self.widget.import_button, self.widget.settings_button)
        centers = [button.geometry().center().y() for button in buttons]
        self.assertLessEqual(max(centers) - min(centers), 1)
        self.assertLess(buttons[0].x(), buttons[1].x())
        self.assertLess(buttons[1].x(), buttons[2].x())
        self.assertFalse(self.widget.settings_button.text())
        self.assertFalse(self.widget.settings_button.icon().isNull())
        QtTest.QTest.mouseClick(self.widget.settings_button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        dialog = self.widget._dialog
        self.assertEqual(len(dialog.findChildren(QtWidgets.QCheckBox)), 2)
        self.assertEqual(len(dialog.findChildren(QtWidgets.QAbstractButton)), 2)
        self.assertFalse(dialog.findChildren(QtWidgets.QSpinBox))
        self.assertFalse(dialog.findChildren(QtWidgets.QComboBox))

    def test_previous_advanced_settings_are_ignored(self):
        self.store.setValue('export_settings', json.dumps(dict(animation=False, bake=False,
            smoothing=False, tangents=False, triangulate=True, scene_range=False, start=3, end=7)))
        other = self.factory(Client('max'), self.log)
        self.assertEqual(other._settings, dict(animation=False))
        other.deleteLater()

    def test_popup_uses_card_screen_center_and_stays_in_zoomed_canvas(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from ui.plugin_node import PluginNode
        self.widget.hide()
        scene = QtWidgets.QGraphicsScene()
        view = QtWidgets.QGraphicsView(scene)
        view.setGeometry(30, 40, 640, 420)
        view.setSceneRect(-5000, -5000, 10000, 10000)
        node = PluginNode(self.info.name, self.widget, dcc='max')
        proxy = scene.addWidget(node)
        proxy.setPos(1800, -1200)
        view.scale(0.7, 0.7)
        view.show()
        self.widget.show()
        self.app.processEvents()
        view.centerOn(proxy)
        self.app.processEvents()
        try:
            point = proxy.mapToScene(proxy.subWidgetRect(self.widget.settings_button).center())
            QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton, pos=view.mapFromScene(point))
            self.app.processEvents()
            dialog = self.widget._dialog
            self.assertIsNotNone(dialog)
            self.assertIsNone(dialog.graphicsProxyWidget())
            self.assertIs(dialog.parentWidget(), view.window())
            visible = QtCore.QRect(view.viewport().mapToGlobal(QtCore.QPoint()), view.viewport().size())
            self.assertTrue(visible.contains(dialog.frameGeometry()), (visible, dialog.frameGeometry()))
            center = view.viewport().mapToGlobal(view.mapFromScene(proxy.mapToScene(proxy.boundingRect().center())))
            self.assertLessEqual((dialog.frameGeometry().center() - center).manhattanLength(), 4)
            QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
            self.app.processEvents()
            # Move the card partly outside the viewport and verify clamping.
            scene_center = view.mapToScene(view.viewport().rect().center())
            target = view.mapToScene(QtCore.QPoint(-10, view.viewport().height() // 2))
            proxy.setPos(target - proxy.boundingRect().center())
            view.centerOn(scene_center)
            self.app.processEvents()
            # The rightmost setting button remains visible while the card
            # center is beyond the left edge of the viewport.
            point = proxy.mapToScene(proxy.subWidgetRect(self.widget.settings_button).center())
            QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton, pos=view.mapFromScene(point))
            self.app.processEvents()
            dialog = self.widget._dialog
            self.assertIsNotNone(dialog)
            self.assertTrue(visible.contains(dialog.frameGeometry()), (visible, dialog.frameGeometry()))
            dialog.close()
            self.app.processEvents()
        finally:
            if self.widget._dialog is not None:
                self.widget._dialog.close()
            # Detach before destroying the temporary scene; tearDown owns widget.
            node.layout().removeWidget(self.widget)
            self.widget.setParent(None)
            proxy.setWidget(None)
            scene.removeItem(proxy)
            view.close()
            node.deleteLater()

    def test_disconnected_button_logs_without_starting_task(self):
        from PySide6 import QtCore, QtTest
        self.widget.set_dcc_clients({})
        QtTest.QTest.mouseClick(self.widget.import_button, QtCore.Qt.LeftButton)
        self.assertIsNone(self.widget._task)
        self.assertTrue(any('Maya 未连接' in message for _, message in self.logs))


if __name__ == '__main__':
    unittest.main(verbosity=2)
