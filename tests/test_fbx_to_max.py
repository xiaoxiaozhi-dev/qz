"""Offline contracts and actual Qt interaction; never connects to a user's DCC."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
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
from maya_actions import fbx_to_max as exporter
from max_actions import fbx_from_maya as receiver
from ui import maya_fbx_transfer as transfer


class Maya:
    def __init__(self):
        self.selection = ['|Body.vtx[1]']
        self.calls, self.commands = [], []
        self.fail = False

    def ls(self, *args, **kwargs):
        if kwargs.get('selection'):
            return ['|Body|Shape'] if kwargs.get('objectsOnly') else list(self.selection)
        if kwargs.get('type') == 'skinCluster':
            return ['skin']
        return [args[0]]

    def objectType(self, *args, **kwargs):
        return False

    def listRelatives(self, node, **kwargs):
        return ['|Body|Shape'] if kwargs.get('shapes') else ['|Body']

    def listHistory(self, *args, **kwargs):
        return ['skin']

    def skinCluster(self, *args, **kwargs):
        return ['|Root', '|ZeroWeight', '|Root']

    def pluginInfo(self, *args, **kwargs):
        return True

    def playbackOptions(self, **kwargs):
        return 0.5 if kwargs.get('minTime') else 10.5

    def upAxis(self, **kwargs):
        return 'y'

    def select(self, nodes=None, **kwargs):
        self.selection = list(nodes or [])

    def file(self, filepath, **kwargs):
        self.calls.append((filepath, kwargs, list(self.selection)))
        if self.fail:
            raise RuntimeError('export failed')
        Path(filepath).write_bytes(b'fbx')


class Max:
    def __init__(self):
        self.objects = [types.SimpleNamespace(name='Body', handle=1)]
        self.params, self.calls = {}, []
        self.pluginManager = types.SimpleNamespace(loadClass=lambda cls: None)
        self.FbxImporter, self.FBXIMP = 'FbxImporter', 'FBXIMP'
        self.units = types.SimpleNamespace(SystemType='centimeters', SystemScale=1.)
        self.frameRate = 24
        self.animationRange = types.SimpleNamespace(start=types.SimpleNamespace(frame=0), end=types.SimpleNamespace(frame=10))
        self.fail = False

    def Name(self, value):
        return value

    def FBXImporterSetParam(self, key, *values):
        self.calls.append(('param', key, values))
        if values:
            self.params[key] = values[0]
        return 'OK'

    def FBXImporterGetParam(self, key):
        return self.params.get(key)

    def resetMaxFile(self, *args):
        self.calls.append(('reset',))
        self.objects = []
        return True

    def getHandleByAnim(self, node):
        return node.handle

    def importFile(self, path, *args, **kwargs):
        self.calls.append(('import', path))
        if self.fail:
            return False
        self.objects.extend([types.SimpleNamespace(name='Body', handle=len(self.objects)+10),
                             types.SimpleNamespace(name='Root', handle=len(self.objects)+20)])
        return True

    def uniqueName(self, name):
        return name + '001'

    def Array(self, *nodes):
        return list(nodes)

    def select(self, nodes):
        self.selected = nodes

    def execute(self, command):
        raise AssertionError('导入不应调用视口框选：' + command)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "角色' 空格.fbx")
        self.maya, self.max = Maya(), Max()
        maya_module = types.ModuleType('maya')
        maya_module.cmds = self.maya
        maya_module.mel = types.SimpleNamespace(eval=self.maya.commands.append)
        self.modules = patch.dict(sys.modules, {'maya': maya_module, 'maya.cmds': self.maya,
                                  'maya.mel': maya_module.mel,
                                  'pymxs': types.SimpleNamespace(runtime=self.max)})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.temp.cleanup()

    def prepare(self):
        Path(self.path).write_bytes(b'fbx')

    def test_export_adds_all_influences_deduplicates_and_restores_components(self):
        result = exporter.export_max(self.path)
        self.assertEqual(self.maya.selection, ['|Body.vtx[1]'])
        self.assertEqual(self.maya.calls[0][2], ['|Body', '|Root', '|ZeroWeight'])
        self.assertEqual((result['count'], result['skin_influence_count'], result['added_influence_count']), (1, 2, 2))
        self.assertTrue(any('子帧' in w for w in result['warnings']))
        self.assertIn('FBXExportBakeComplexStart -v 0;', self.maya.commands)
        self.assertIn('FBXExportBakeComplexEnd -v 11;', self.maya.commands)
        self.assertTrue(self.maya.calls[0][1]['exportSelected'])

    def test_export_failure_restores_original_selection(self):
        self.maya.fail = True
        with self.assertRaises(RuntimeError):
            exporter.export_max(self.path)
        self.assertEqual(self.maya.selection, ['|Body.vtx[1]'])

    def test_model_without_history_does_not_collect_unrelated_skin_clusters(self):
        self.maya.listHistory = lambda *a, **k: []
        result = exporter.export_max(self.path)
        self.assertEqual(result['skin_count'], 0)
        self.assertEqual(result['added_influence_count'], 0)
        self.assertEqual(self.maya.calls[0][2], ['|Body'])

    def test_static_disables_animation_without_disabling_skin(self):
        exporter.export_max(self.path, animation=False)
        self.assertIn('FBXProperty "Export|IncludeGrp|Animation" -v false;', self.maya.commands)
        self.assertIn('FBXExportSkins -v true;', self.maya.commands)
        self.assertIn('FBXExportIncludeChildren -v false;', self.maya.commands)
        self.assertIn('FBXExportInputConnections -v false;', self.maya.commands)
        self.assertIn('FBXExportFileVersion -v "FBX201800";', self.maya.commands)
        self.assertFalse(any(self.path in command for command in self.maya.commands))

    def test_fixed_export_options_and_sampling_stay_in_maya_action(self):
        exporter.export_max(self.path)
        for command in ('FBXExportSmoothingGroups -v true;', 'FBXExportTangents -v true;',
                        'FBXExportTriangulate -v false;', 'FBXExportBakeComplexAnimation -v true;',
                        'FBXExportBakeResampleAnimation -v true;', 'FBXExportBakeComplexStep -v 1;'):
            self.assertIn(command, self.maya.commands)

    def test_invalid_animation_does_not_touch_exporter_or_selection(self):
        for value in (1, None, 'false', {}):
            with self.assertRaises(ValueError):
                exporter.export_max(self.path, animation=value)
        self.assertEqual(self.maya.commands, [])
        self.assertEqual(self.maya.selection, ['|Body.vtx[1]'])

    def test_static_export_does_not_query_animation_range(self):
        self.maya.playbackOptions = lambda **kwargs: self.fail('Static export must not query animation range')
        exporter.export_max(self.path, animation=False)
        self.assertFalse(any('BakeComplexStart' in command for command in self.maya.commands))

    def test_existing_file_is_not_overwritten(self):
        self.prepare()
        with self.assertRaises(ValueError):
            exporter.export_max(self.path)
        self.assertEqual(self.maya.calls, [])

    def test_empty_selection_fails_before_export_settings(self):
        self.maya.selection = []
        original = self.maya.ls
        self.maya.ls = lambda *a, **k: [] if k.get('selection') else original(*a, **k)
        with self.assertRaises(ValueError):
            exporter.export_max(self.path)
        self.assertEqual(self.maya.commands, [])

    def test_import_adds_only_and_renames_new_conflicts(self):
        self.prepare()
        old = self.max.objects[0]
        result = receiver.receive_maya(self.path)
        self.assertEqual(old.name, 'Body')
        self.assertEqual(self.max.objects[1].name, 'Body001')
        self.assertEqual(result['nodes'], 2)
        self.assertEqual(result['renamed_count'], 1)
        self.assertEqual(self.max.params['Mode'], 'create')
        self.assertNotIn(('reset',), self.max.calls)
        self.assertNotIn('KeepFrameRate', self.max.params)

    def test_open_resets_once_without_save_or_prompt(self):
        self.prepare()
        result = receiver.receive_maya(self.path, mode='open')
        self.assertEqual(self.max.calls.count(('reset',)), 1)
        self.assertEqual(result['renamed_count'], 0)
        self.assertEqual([node.name for node in self.max.objects], ['Body', 'Root'])

    def test_static_controls_animation_and_take_only(self):
        self.prepare()
        receiver.receive_maya(self.path, animation=False)
        for key, value in [('TakeIndex', 0), ('Animation', False)]:
            self.assertEqual(self.max.params[key], value)
        self.assertNotIn('FillTimeline', self.max.params)
        self.assertNotIn('KeepFrameRate', self.max.params)

    def test_bad_file_and_bad_mode_never_reset_scene(self):
        for args in [dict(mode='open'), dict(mode='bad'), dict(animation=1)]:
            with self.assertRaises(ValueError):
                receiver.receive_maya(self.path, **args)
        self.assertEqual(self.max.calls, [])

    def test_unsupported_parameter_prevents_open(self):
        self.prepare()
        self.max.FBXImporterSetParam = lambda *a: 'unsupplied'
        with self.assertRaises(RuntimeError):
            receiver.receive_maya(self.path, mode='open')
        self.assertEqual(len(self.max.objects), 1)

    def test_wrong_parameter_readback_prevents_open(self):
        self.prepare()
        self.max.FBXImporterGetParam = lambda key: None
        with self.assertRaises(RuntimeError):
            receiver.receive_maya(self.path, mode='open')
        self.assertNotIn(('reset',), self.max.calls)

    def test_old_bridge_fails_before_scene_change(self):
        self.prepare()
        from max_bridge import server
        with patch.object(server, 'SUPPORTS_EXECUTION_TIMEOUT', False):
            with self.assertRaises(RuntimeError):
                receiver.receive_maya(self.path, mode='open')
        self.assertEqual(self.max.calls, [])

    def test_import_failure_is_not_retried(self):
        self.prepare()
        self.max.fail = True
        with self.assertRaises(RuntimeError):
            receiver.receive_maya(self.path)
        self.assertEqual(sum(c[0] == 'import' for c in self.max.calls), 1)

    def test_preflight_leaves_existing_importer_options_untouched(self):
        self.max.params = dict(Animation=False, AxisConversion=False, FillTimeline=False)
        before = self.max.params.copy()
        ready = receiver.preflight()
        self.assertEqual(self.max.params, before)
        self.assertEqual(self.max.calls, [])
        self.assertEqual(ready['receiver_version'], 2)

    def test_both_animation_modes_preserve_unmanaged_options_and_skip_viewport_calls(self):
        self.prepare()
        for animation in (False, True):
            options = dict(AxisConversion=False, UpAxis='Y', ScaleConversion=False,
                           ScaleFactor=2.5, ConvertUnit='m', FillTimeline=False, KeepFrameRate=True)
            self.max.params.update(options)
            self.max.calls.clear()
            result = receiver.receive_maya(self.path, animation=animation)
            self.assertEqual({key: self.max.params[key] for key in options}, options)
            self.assertFalse(any(call[0] == 'param' and call[1] in set(options) | {'ResetImport'}
                                 for call in self.max.calls))
            self.assertEqual(result['nodes'], 2)


class Client:
    connected = True
    def __init__(self, side):
        self.side, self.calls, self.stale, self.fail = side, [], False, None

    def call(self, action, **params):
        self.calls.append((action, params))
        if self.fail == action:
            return dict(ok=False, error='timed out')
        if action == 'fbx_export_max_preflight':
            return dict(ok=True, data=dict(exporter_version=2, settings_policy='animation_only'))
        if action == 'fbx_max_preflight':
            return dict(ok=True, data=dict(ready=True, receiver_version=1 if self.stale else 2,
                                          open_policy='discard_unsaved', scene_settings_policy='existing_import_settings'))
        if action == 'reload_actions':
            self.stale = False
        if action == 'fbx_export_max':
            Path(params['filepath']).write_bytes(b'fbx')
        return dict(ok=True, data=dict(count=1, nodes=2))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch('paths.local_data_dir', return_value=self.temp.name)
        self.root_patch.start()
        self.maya, self.max = Client('maya'), Client('max')

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_both_modes_dispatch_with_execution_timeout_and_cleanup(self):
        for mode in ('open', 'import'):
            result = transfer.run_transfer(self.maya, self.max, mode, animation=False)
            self.assertTrue(result['ok'], result)
            self.assertEqual(list(Path(self.temp.name).iterdir()), [])
            action, args = self.max.calls[-1]
            self.assertEqual(action, 'fbx_receive_maya')
            self.assertEqual((args['mode'], args['animation'], args['execution_timeout']), (mode, False, 600))
            exported = next(params for action, params in self.maya.calls if action == 'fbx_export_max')
            self.assertEqual(set(exported) - {'timeout', 'connect_timeout'}, {'filepath', 'animation'})
            self.assertIs(exported['animation'], False)

    def test_old_maya_exporter_reloads_before_export(self):
        original, events = self.maya.call, []
        def call(action, **params):
            events.append(action)
            if action == 'fbx_export_max_preflight' and len(events) == 1:
                return dict(ok=False, error='unknown action: ' + action)
            if action == 'reload_actions':
                self.assertFalse(list(Path(self.temp.name).iterdir()))
            return original(action, **params)
        with patch.object(self.maya, 'call', side_effect=call):
            result = transfer.run_transfer(self.maya, self.max, 'import')
        self.assertTrue(result['ok'], result)
        self.assertEqual(events[:3], ['fbx_export_max_preflight', 'reload_actions', 'fbx_export_max_preflight'])

    def test_invalid_maya_exporter_contract_stops_before_export_and_receive(self):
        with patch.object(self.maya, 'call', return_value=dict(ok=True, data={})):
            result = transfer.run_transfer(self.maya, self.max, 'open')
        self.assertFalse(result['ok'])
        self.assertFalse(any(action == 'fbx_receive_maya' for action, _ in self.max.calls))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_invalid_animation_stops_before_bridge_calls(self):
        for value in (1, None, 'false', {}):
            self.assertFalse(transfer.run_transfer(self.maya, self.max, 'import', animation=value)['ok'])
        self.assertEqual(self.maya.calls + self.max.calls, [])

    def test_disconnected_fails_before_file_creation_or_export(self):
        self.max.connected = False
        self.assertFalse(transfer.run_transfer(self.maya, self.max, 'open')['ok'])
        self.assertEqual(self.maya.calls + self.max.calls, [])

    def test_stale_receiver_reloads_only_before_export(self):
        self.max.stale = True
        self.assertTrue(transfer.run_transfer(self.maya, self.max, 'import')['ok'])
        self.assertEqual([c[0] for c in self.max.calls[:3]], ['fbx_max_preflight', 'reload_actions', 'fbx_max_preflight'])

    def test_export_failure_never_calls_receive_or_creates_empty_subdirectories(self):
        self.maya.fail = 'fbx_export_max'
        result = transfer.run_transfer(self.maya, self.max, 'open')
        self.assertFalse(result['ok'])
        self.assertNotIn('retained_path', result)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertFalse(any(c[0] == 'fbx_receive_maya' for c in self.max.calls))

    def test_receive_timeout_retains_fbx_and_does_not_retry(self):
        self.max.fail = 'fbx_receive_maya'
        result = transfer.run_transfer(self.maya, self.max, 'open')
        self.assertFalse(result['ok'])
        self.assertTrue(Path(result['retained_path']).is_file())
        self.assertEqual(Path(result['retained_path']).parent, Path(self.temp.name))
        self.assertEqual(sum(c[0] == 'fbx_receive_maya' for c in self.max.calls), 1)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        from ui.app_style import apply_application_style
        from ui.plugin_loader import scan
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)
        info = next(i for i in scan(str(ROOT / 'plugins')) if i.key == ('maya', 'fbx_to_max'))
        cls.factory = staticmethod(info.load_factory())

    def setUp(self):
        from PySide6 import QtCore
        self.temp = tempfile.TemporaryDirectory()
        self.store = QtCore.QSettings(str(Path(self.temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
        self.settings_patch = patch('PySide6.QtCore.QSettings', return_value=self.store)
        self.settings_patch.start()
        self.logs = []
        log = lambda message: self.logs.append((threading.current_thread(), message))
        self.widget = self.factory(Client('maya'), types.SimpleNamespace(info=log, warn=log, error=log))
        self.max = Client('max')
        self.widget.set_dcc_clients({'max': self.max})
        self.widget.show()
        self.app.processEvents()

    def tearDown(self):
        if self.widget._dialog is not None:
            self.widget._dialog.close()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def test_clicks_both_buttons_use_shared_clients_background_and_ui_log(self):
        from PySide6 import QtCore, QtTest
        calls = []
        def run(*args):
            calls.append((args, threading.current_thread()))
            time.sleep(.02)
            return dict(ok=True, mode=args[2], data=dict(nodes=2))
        with patch.dict(self.factory.__globals__, run_transfer=run):
            for button in (self.widget.open_button, self.widget.import_button):
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                self.assertFalse(button.isEnabled())
                deadline = time.monotonic() + 4
                while self.widget._task is not None and time.monotonic() < deadline:
                    QtTest.QTest.qWait(10)
                self.assertIsNone(self.widget._task)
        self.assertEqual([args[2] for args, thread in calls], ['open', 'import'])
        self.assertTrue(all(args[1] is self.max and thread is not threading.current_thread() for args, thread in calls))
        self.assertTrue(all(thread is threading.current_thread() for thread, message in self.logs))
        self.assertTrue(all(not b.toolTip() for b in (self.widget.open_button, self.widget.import_button, self.widget.settings_button)))

    def test_settings_real_click_and_escape_save_animation(self):
        from PySide6 import QtCore, QtTest
        QtTest.QTest.mouseClick(self.widget.settings_button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        dialog = self.widget._dialog
        self.assertTrue(dialog.animation_checkbox.isChecked())
        QtTest.QTest.mouseClick(dialog.animation_checkbox, QtCore.Qt.LeftButton)
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.widget._settings['animation'])
        self.assertEqual(json.loads(self.store.value('export_settings')), dict(animation=False))
        second = self.factory(Client('maya'), self.widget.log)
        self.assertFalse(second._settings['animation'])
        second.deleteLater()

    def test_disconnected_button_logs_and_does_not_start_task(self):
        from PySide6 import QtCore, QtTest
        self.max.connected = False
        QtTest.QTest.mouseClick(self.widget.import_button, QtCore.Qt.LeftButton)
        self.assertIsNone(self.widget._task)
        self.assertTrue(any('Max 未连接' in message for _, message in self.logs))


if __name__ == '__main__':
    unittest.main()
