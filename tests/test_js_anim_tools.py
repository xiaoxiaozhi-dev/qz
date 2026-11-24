"""旧动作数据、场景事务和真实 Qt 事件路径；不需要 Maya。"""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from maya_actions import _js_anim_core as core
import paths


LEGACY = {"nodes": {
    "Armature": {"parent": None, "ch": {"rx": [[0, -90]]}},
    "hip": {"parent": None, "ch": {"tx": [[0, 1], [10, 5]]}},
    "hand": {"parent": "hip", "ch": {"ry": [[0, 12]]}},
}}


class Scene:
    """只模拟 Maya 命令边界；用来验证数据解释和事务，不替代真机测试。"""
    def __init__(self):
        root = "|角色:Armature"
        hip = root + "|角色:hip"
        self.nodes = {root: ("transform", None), hip: ("joint", root),
                      hip + "|角色:hand": ("joint", hip)}
        self.selection = [root]
        self.layers = {}
        self.networks = set()
        self.minimum, self.maximum, self.time, self.suspended = 2, 50, 8, False
        self.fail_key = False
        self.locked = set()
        self.undo_enabled = True
        self.deletions = []
        self.snapshot = None
        self.scene = "scene.ma"

    def ls(self, name=None, selection=False, type=None, **kwargs):
        if selection:
            return list(self.selection)
        candidates = list(self.layers) if type == "animLayer" else list(self.nodes)
        if name:
            candidates = [node for node in candidates if node == name or node.rsplit("|", 1)[-1] == name]
        return candidates

    def listRelatives(self, node, parent=False, **kwargs):
        if parent:
            return [self.nodes[node][1]] if self.nodes[node][1] else []
        return [child for child in self.nodes if child.startswith(node + "|")]

    def objectType(self, node):
        return self.nodes[node][0]

    def objExists(self, node):
        return node in self.nodes or node in self.layers or node in self.networks

    def getAttr(self, plug, lock=False):
        node, attr = plug.rsplit(".", 1)
        if lock:
            return plug in self.locked
        if node in self.layers:
            return self.layers[node]["attrs"][attr]
        return (self.time * 0.00006 if attr == "translateX" else 0)

    def setAttr(self, plug, value, **kwargs):
        node, attr = plug.rsplit(".", 1)
        self.layers[node]["attrs"][attr] = value

    def attributeQuery(self, attr, node, **kwargs):
        return attr in self.layers.get(node, {}).get("attrs", {})

    def addAttr(self, node, longName, **kwargs):
        self.layers[node]["attrs"][longName] = None

    def animLayer(self, name, q=False, edit=False, **kwargs):
        if q:
            return self.layers[name]["flags"].get(next(iter(kwargs)), 0)
        if edit:
            self.layers[name]["flags"].update(kwargs)
        else:
            self.layers[name] = {"attrs": {}, "keys": {}, "flags": {"weight": 1, **kwargs}}
        return name

    def setKeyframe(self, node, attribute, time, value, animLayer, **kwargs):
        if self.fail_key:
            raise RuntimeError("simulated write failure")
        self.layers[animLayer]["keys"].setdefault((node, attribute), []).append([time, value])
        return 1

    def undoInfo(self, q=False, state=False, openChunk=False, closeChunk=False, **kwargs):
        if q:
            return self.undo_enabled
        if openChunk:
            self.snapshot = copy.deepcopy((self.layers, self.networks, self.selection))

    def undo(self):
        self.layers, self.networks, self.selection = copy.deepcopy(self.snapshot)

    def createNode(self, type, name, **kwargs):
        self.networks.add(name)
        return name

    def delete(self, node):
        self.deletions.append(node)
        self.layers.pop(node, None)
        self.networks.discard(node)

    def rename(self, old, new):
        new = new.lstrip(":")
        self.layers[new] = self.layers.pop(old)
        return new

    def refresh(self, q=False, suspend=False):
        if q:
            return self.suspended
        self.suspended = suspend

    def playbackOptions(self, q=False, minTime=None, maxTime=None):
        if q:
            return self.minimum if minTime else self.maximum
        if minTime is not None:
            self.minimum = minTime
        if maxTime is not None:
            self.maximum = maxTime

    def currentTime(self, value=None, q=False, **kwargs):
        if q:
            return self.time
        self.time = value

    def currentUnit(self, q=False, **kwargs):
        return getattr(self, 'units', {'time': 'film', 'linear': 'cm', 'angle': 'deg'})[next(iter(kwargs))]

    def file(self, **kwargs):
        return self.scene

    def select(self, nodes=None, clear=False, **kwargs):
        self.selection = [] if clear else list(nodes)


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "中文动作.json"
        self.write(LEGACY)
        self.scene = Scene()
        self.patcher = patch.object(core, "_cmds", return_value=self.scene)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_original_corpus(self):
        folder = Path(paths.local_data_dir("js_anim_pose_json"))
        files = sorted(folder.glob("*.json"))
        if not files:
            self.skipTest("本机未配置旧动作库")
        total_nodes, total_keys = 0, 0
        for path in files:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                data = core.read_animation(path)
                total_nodes += len(data["nodes"])
                total_keys += sum(len(keys) for node in data["nodes"].values() for keys in node["ch"].values())
                self.assertEqual(original, path.read_bytes(), "读取不可改写原文件")
        print("LEGACY CORPUS: %d files, %d nodes, %d keys" % (len(files), total_nodes, total_keys))

    def test_namespace_parent_and_missing_zero_channels(self):
        before = self.path.read_bytes()
        result = core.import_animation(str(self.path))
        self.assertEqual(result["imported_nodes"], 3)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["total_keys"], 19)
        layer = self.scene.layers[result["layer"]]
        hip = "|角色:Armature|角色:hip"
        self.assertEqual(layer["keys"][(hip, "translateX")], [[0, 1], [10, 5]])
        self.assertEqual(layer["keys"][(hip, "rotateZ")], [[0, 0]])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual((self.scene.minimum, self.scene.maximum, self.scene.time), (0, 10, 0))
        self.assertFalse(self.scene.suspended)
        self.assertFalse(self.scene.networks)

    def test_all_legacy_files_import_into_matching_namespaced_hierarchies(self):
        files = sorted(Path(paths.local_data_dir("js_anim_pose_json")).glob("*.json"))
        if not files:
            self.skipTest("本机未配置旧动作库")
        for path in files:
            with self.subTest(path=path.name):
                data = core.read_animation(path)
                scene = Scene()
                scene.units = {flag: data.get(key) or scene.currentUnit(q=True, **{flag: True})
                               for flag, key in (('time', 'time_unit'), ('linear', 'linear_unit'),
                                                 ('angle', 'angular_unit'))}
                root = "|角色:Armature"
                scene.nodes = {root: ("transform", None)}
                node_paths = {"Armature": root}
                def ensure(name):
                    if name not in node_paths:
                        parent_name = data["nodes"].get(name, {}).get("parent")
                        parent = ensure(parent_name) if parent_name else root
                        node_paths[name] = parent + "|角色:" + name
                        scene.nodes[node_paths[name]] = ("joint", parent)
                    return node_paths[name]
                for name in data["nodes"]:
                    ensure(name)
                core._cmds.return_value = scene
                result = core.import_animation(str(path))
                self.assertEqual(result["imported_nodes"], len(data["nodes"]))
                self.assertEqual(result["skipped"], [])
                written = scene.layers[result["layer"]]["keys"]
                for name, record in data["nodes"].items():
                    for channel, attr in core.CHANNELS.items():
                        expected = record.get("ch", {}).get(channel, [[data["start_frame"], 0]])
                        self.assertEqual(written[(node_paths[name], attr)], expected)
        print("LEGACY IMPORT SIMULATION: %d files, all nodes and channel values preserved" % len(files))

    def test_parent_mismatch_and_missing_reported(self):
        data = copy.deepcopy(LEGACY)
        data["nodes"]["hand"]["parent"] = "other"
        data["nodes"]["missing"] = {"parent": None, "ch": {}}
        self.write(data)
        result = core.import_animation(str(self.path))
        self.assertEqual(result["imported_nodes"], 2)
        self.assertEqual(len(result["skipped"]), 2)

    def test_duplicate_scene_names_rejected_before_edit(self):
        root = self.scene.selection[0]
        self.scene.nodes[root + "|other:hip"] = ("joint", root)
        with self.assertRaisesRegex(ValueError, "重名"):
            core.import_animation(str(self.path))
        self.assertFalse(self.scene.layers)

    def test_failed_reimport_preserves_existing_layer_and_preview(self):
        core.import_animation(str(self.path))
        before = copy.deepcopy(self.scene.layers)
        self.scene.minimum, self.scene.maximum, self.scene.time = 3, 40, 12
        self.scene.fail_key = True
        with self.assertRaisesRegex(RuntimeError, "write failure"):
            core.import_animation(str(self.path))
        self.assertEqual(before, self.scene.layers)
        self.assertEqual((self.scene.minimum, self.scene.maximum, self.scene.time), (3, 40, 12))
        self.assertFalse(self.scene.networks)
        self.assertFalse(self.scene.suspended)

    def test_corrupt_file_does_not_delete_old_layer(self):
        core.import_animation(str(self.path))
        before = copy.deepcopy(self.scene.layers)
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            core.import_animation(str(self.path))
        self.assertEqual(before, self.scene.layers)

    def test_locked_target_rejected_before_edit(self):
        self.scene.locked.add(self.scene.selection[0] + ".rotateX")
        with self.assertRaisesRegex(ValueError, "锁定"):
            core.import_animation(str(self.path))
        self.assertFalse(self.scene.layers)

    def test_undo_disabled_rejected_before_edit(self):
        self.scene.undo_enabled = False
        with self.assertRaisesRegex(RuntimeError, "撤销"):
            core.import_animation(str(self.path))
        self.assertFalse(self.scene.layers)

    def test_bridge_registers_actions_without_importing_maya(self):
        from maya_bridge.server import dispatch, reload_actions
        registered = reload_actions()
        self.assertIn("js_anim_import", registered)
        self.assertIn("js_anim_export_fbx", registered)
        from maya_actions import _js_anim_core as active_core
        with patch.object(active_core, "_cmds", return_value=self.scene):
            result = dispatch("js_anim_import", json_path=str(self.path))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["imported_nodes"], 3)
        json.dumps(result, allow_nan=False)

    def test_base_range_restored_after_switch_and_last_delete(self):
        result = core.import_animation(str(self.path))
        core.activate_layer()
        self.assertEqual((self.scene.minimum, self.scene.maximum, self.scene.time), (2, 50, 8))
        core.activate_layer(result["layer"])
        core.delete_layer(result["layer"])
        self.assertEqual((self.scene.minimum, self.scene.maximum, self.scene.time), (2, 50, 8))
        self.assertFalse(self.scene.layers)

    def test_export_preserves_scene_and_small_cumulative_motion(self):
        self.scene.minimum, self.scene.maximum, self.scene.time = 0, 4, 3
        selection = list(self.scene.selection)
        output = Path(self.tmp.name) / "export.json"
        core.export_animation(str(output))
        data = core.read_animation(output)
        self.assertEqual(data["end_frame"], 4)
        self.assertEqual(data["nodes"]["hip"]["ch"]["tx"][-1], [4, 0.00024])
        self.assertEqual(self.scene.time, 3)
        self.assertEqual(self.scene.selection, selection)
        self.assertFalse(self.scene.deletions)
        self.assertFalse(self.scene.suspended)
        with self.assertRaises(FileExistsError):
            core.export_animation(str(output))

    def test_plateaus_keep_their_boundaries(self):
        self.assertEqual(core.compress_samples([[0, 0], [1, 1], [2, 1], [3, 1], [4, 2], [5, 2]]),
                         [[0, 0], [1, 1], [3, 1], [4, 2], [5, 2]])

    def test_fbx_batch_preserves_saved_scene_between_files(self):
        scene = self.scene
        scene.nodes = {name.replace("角色:", ""): (kind, parent.replace("角色:", "") if parent else None)
                       for name, (kind, parent) in scene.nodes.items()}
        scene.selection = ["|Armature"]
        original = Path(self.tmp.name) / "original.ma"
        original.touch()
        scene.scene, scene.modified, scene.opened = str(original), False, []
        def file(path=None, **options):
            if options.get("q"):
                return scene.modified if options.get("modified") else scene.scene
            if options.get("open"):
                scene.opened.append(path)
                scene.scene, scene.modified = path, False
                if str(path).endswith("bad.fbx"):
                    raise RuntimeError("FBX open failed")
            if "modified" in options:
                scene.modified = options["modified"]
        scene.file = file
        scene.pluginInfo = lambda *args, **kwargs: True
        original_playback = scene.playbackOptions
        def playback(**options):
            result = original_playback(**options)
            if not options.get("q"):
                scene.modified = True
            return result
        scene.playbackOptions = playback
        for name in ("one.fbx", "two.fbx"):
            source = Path(self.tmp.name) / name
            source.touch()
            result = core.export_fbx(str(source), self.tmp.name)
            self.assertTrue(Path(result["json_path"]).is_file())
            self.assertEqual(scene.scene, str(original))
            self.assertFalse(scene.modified)
            self.assertEqual((scene.minimum, scene.maximum, scene.time), (2, 50, 8))
        source = Path(self.tmp.name) / "bad.fbx"
        source.touch()
        with self.assertRaisesRegex(RuntimeError, "FBX open failed"):
            core.export_fbx(str(source), self.tmp.name)
        self.assertEqual(scene.scene, str(original))
        self.assertFalse(scene.modified)
        before = list(scene.opened)
        scene.modified = True
        with self.assertRaisesRegex(RuntimeError, "先保存"):
            core.export_fbx(str(source), self.tmp.name)
        self.assertEqual(before, scene.opened)

    def test_bad_schema_finite_values_and_frame_ranges(self):
        for data in ({}, {"nodes": []}, {"nodes": {"hip": {"ch": {"tx": [[0, float("nan")]]}}}},
                     {**LEGACY, "start_frame": 4}, {**LEGACY, "format_version": 99}):
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ValueError):
                    core.read_animation(self.path)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtCore, QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            from ui.app_style import apply_application_style
        except ImportError:
            pass
        else:
            apply_application_style(cls.app)
        from ui.plugin_loader import scan
        cls.info = next(plugin for plugin in scan(paths.plugins_dir()) if plugin.key == ("maya", "js_anim_tools"))
        cls.factory = staticmethod(cls.info.load_factory())

    def setUp(self):
        from PySide6 import QtCore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = QtCore.QSettings(str(Path(directory.name, 'settings.ini')), QtCore.QSettings.IniFormat)
        settings_patch = patch.object(QtCore, 'QSettings', return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def test_real_events_search_import_switch_and_worker_lifetime(self):
        from PySide6 import QtCore, QtTest
        class Client:
            connected = False
            def __init__(self):
                self.calls = []
            def call(self, action, **params):
                self.calls.append((action, params))
                time.sleep(0.03)
                if action == "js_anim_import":
                    return {"ok": True, "data": {"layers": [{"layer": "AJT_test", "name": "走路", "start": 0,
                            "end": 10, "weight": 1}], "imported_nodes": 3, "total_keys": 19, "skipped": []}}
                return {"ok": True, "data": {"layers": []}}
        client = Client()
        with tempfile.TemporaryDirectory() as folder:
            for name in ("走路动画.json", "其他.JSON"):
                Path(folder, name).write_text(json.dumps(LEGACY), encoding="utf-8")
            with patch.object(paths, "local_data_dir", return_value=folder):
                widget = self.factory(client, None)
            self.addCleanup(widget.close)
            widget.resize(320, 500)
            widget.show()
            self.app.processEvents()
            self.assertEqual(widget.file_list.count(), 2)
            widget.search_edit.setFocus()
            widget.search_edit.setText("走路")
            QtTest.QTest.qWait(200)
            self.assertEqual(widget.file_list.count(), 1)
            QtTest.QTest.mouseClick(widget.import_button, QtCore.Qt.LeftButton)
            self.assertTrue(widget._busy)
            self.assertFalse(widget.controls.isEnabled())
            self._wait(widget)
            self.assertEqual(client.calls[0][0], "js_anim_import")
            self.assertTrue(client.calls[0][1]["json_path"].endswith("走路动画.json"))
            self.assertIsNone(client.calls[0][1]["root_node"])
            self.assertEqual(widget.layer_list.count(), 2)
            self.assertEqual(widget.tabs.currentIndex(), 1)
            rectangle = widget.layer_list.visualItemRect(widget.layer_list.item(0))
            QtTest.QTest.mouseClick(widget.layer_list.viewport(), QtCore.Qt.LeftButton, pos=rectangle.center())
            self._wait(widget)
            self.assertEqual(client.calls[-1][0], "js_anim_activate")
            self.assertIsNone(client.calls[-1][1]["layer"])
            widget.deleteLater()
            self.app.processEvents()

    def test_offline_widget_can_browse_original_library(self):
        widget = self.factory(None, None)
        if Path(widget.library_dir).is_dir():
            self.assertGreater(widget.file_list.count(), 0)
        self.assertNotIn("maya.cmds", sys.modules)
        screenshot = os.environ.get("QZ_ANIM_SCREENSHOT")
        if screenshot:
            from PySide6 import QtGui
            from ui.plugin_node import PluginNode
            font_file = Path("C:/Windows/Fonts/msyh.ttc")
            if font_file.is_file():
                font_id = QtGui.QFontDatabase.addApplicationFont(str(font_file))
                families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    self.app.setFont(QtGui.QFont(families[0], 9))
            self.app.setStyle("Fusion")
            node = PluginNode("JS 动作工具", widget)
            node.resize(320, node.sizeHint().height())
            node.show()
            self.app.processEvents()
            node.grab().save(screenshot)
            node.close()
        widget.deleteLater()
        self.app.processEvents()

    def test_initial_layer_query_does_not_disable_controls(self):
        from PySide6 import QtTest
        class Client:
            connected = True
            def call(self, action, **params):
                time.sleep(0.05)
                return {"ok": True, "data": {"layers": []}}
        widget = self.factory(Client(), None)
        self.app.processEvents()
        self.assertTrue(widget.controls.isEnabled())
        self.assertFalse(widget._busy)
        deadline = time.monotonic() + 5
        while widget._initial_job is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertIsNone(widget._initial_job)
        widget.deleteLater()
        self.app.processEvents()

    def test_immediate_search_enter_does_not_import_stale_selection(self):
        from PySide6 import QtCore, QtTest
        class Client:
            calls = []
            def call(self, action, **params):
                self.calls.append(params)
                return {"ok": False, "error": "file not used in this event test"}
        client = Client()
        with tempfile.TemporaryDirectory() as folder:
            for name in ("A.json", "Z.json"):
                Path(folder, name).write_text("{}", encoding="utf-8")
            with patch.object(paths, "local_data_dir", return_value=folder):
                widget = self.factory(client, None)
            widget.show()
            widget.search_edit.setFocus()
            widget.search_edit.setText("Z")
            QtTest.QTest.keyClick(widget.search_edit, QtCore.Qt.Key_Return)
            self._wait(widget)
            self.assertEqual(len(client.calls), 1)
            self.assertTrue(client.calls[0]["json_path"].endswith("Z.json"))
        widget.deleteLater()
        self.app.processEvents()

    def test_transport_failure_stops_batch_and_releases_controls(self):
        messages = []
        class Log:
            def info(self, message):
                messages.append(message)
            error = info
        class Client:
            calls = 0
            def call(self, action, **params):
                self.calls += 1
                raise RuntimeError("connection closed")
        client = Client()
        widget = self.factory(client, Log())
        widget._start([("js_anim_import", {"json_path": name}, name) for name in ("first", "second")])
        self._wait(widget)
        self.assertEqual(client.calls, 1)
        self.assertTrue(widget.controls.isEnabled())
        self.assertTrue(any("停止后续任务" in message for message in messages))
        widget.deleteLater()
        self.app.processEvents()

    def test_export_buttons_use_fixed_library_without_root_picker(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        class Client:
            def __init__(self):
                self.calls = []
            def call(self, action, **params):
                self.calls.append((action, params))
                output = params.get("json_path") or os.path.join(
                    params["output_folder"], Path(params["fbx_path"]).stem + ".json")
                return {"ok": True, "data": {"node_count": 3, "frame_count": 4, "json_path": output}}
        client = Client()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(paths, "local_data_dir", return_value=folder):
                widget = self.factory(client, None)
            template = Path(folder, "F1_标准.fbx")
            template.write_bytes(b'fbx')
            widget.template_edit.setText(str(template))
            widget.show()
            self.app.processEvents()
            self.assertTrue(widget.export_panel.isHidden())
            self.assertEqual(widget.export_toggle.text(), "")
            QtTest.QTest.mouseClick(widget.export_toggle, QtCore.Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(widget.export_panel.isVisible())
            self.assertEqual(client.calls, [])
            with patch.object(QtWidgets.QInputDialog, "getText", return_value=("测试导出", True)):
                QtTest.QTest.mouseClick(widget.export_button, QtCore.Qt.LeftButton)
            self._wait(widget)
            self.assertEqual(client.calls[0][0], "js_anim_export")
            self.assertEqual(client.calls[0][1]["json_path"], os.path.join(folder, "测试导出.json"))
            self.assertIsNone(client.calls[0][1]["root_node"])
            self.assertEqual(client.calls[0][1]["template_path"], str(template))
            source_files = [str(Path(folder) / name) for name in ("first.fbx", "second.fbx")]
            with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=(source_files, "")), \
                    patch.object(widget, "_confirm", return_value=True):
                QtTest.QTest.mouseClick(widget.batch_export_button, QtCore.Qt.LeftButton)
            self._wait(widget)
            self.assertEqual(len(client.calls), 3)
            for action, params in client.calls[1:]:
                self.assertEqual(action, "js_anim_export_fbx")
                self.assertEqual(params["output_folder"], folder)
                self.assertEqual(params["template_path"], str(template))
            QtTest.QTest.mouseClick(widget.export_toggle, QtCore.Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(widget.export_panel.isHidden())
            self.assertEqual(len(client.calls), 3)
            widget.deleteLater()
            self.app.processEvents()

    def test_template_browse_remembers_path_and_cancel_preserves_it(self):
        from PySide6 import QtWidgets
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, "local_data_dir", return_value=folder):
            template = str(Path(folder, "M1_标准.fbx"))
            Path(template).write_bytes(b'fbx')
            widget = self.factory(None, None)
            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(template, "")) as browse:
                widget.template_browse.trigger()
            self.assertIs(browse.call_args.args[0], widget.window())
            self.assertEqual(widget.template_edit.text(), template)
            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
                widget.template_browse.trigger()
            self.assertEqual(widget.template_edit.text(), template)
            reopened = self.factory(None, None)
            self.assertEqual(reopened.template_edit.text(), template)
            self.assertTrue(reopened.export_panel.isHidden())
            self.assertIs(reopened.template_edit.parentWidget(), reopened.export_panel)
            for card in (widget, reopened):
                card.deleteLater()
            self.app.processEvents()

    def test_optional_template_does_not_block_either_export(self):
        from PySide6 import QtWidgets
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, "local_data_dir", return_value=folder):
            unknown = Path(folder, "standard.fbx"); unknown.write_bytes(b'fbx')
            widget = self.factory(None, None)
            with patch.object(widget, "_start") as start, patch.object(widget, "_confirm", return_value=True), \
                    patch.object(QtWidgets.QInputDialog, "getText", return_value=('walk', True)), \
                    patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=([str(Path(folder, 'source.fbx'))], '')):
                for template in ('', str(Path(folder, 'F1_missing.fbx')), str(unknown)):
                    widget.template_edit.setText(template)
                    widget._export_current()
                    self.assertEqual(start.call_args.args[0][0][0], 'js_anim_export')
                    self.assertEqual(start.call_args.args[0][0][1]['template_path'], template or None)
                    widget._export_fbx()
                    self.assertEqual(start.call_args.args[0][0][0], 'js_anim_export_fbx')
                    self.assertEqual(start.call_args.args[0][0][1]['template_path'], template or None)
                self.assertEqual(start.call_count, 6)
            widget.deleteLater()
            self.app.processEvents()

    def test_batch_locks_root_after_first_success(self):
        class Client:
            def __init__(self):
                self.calls = []
            def call(self, action, **params):
                self.calls.append(params)
                return {"ok": True, "data": {"imported_nodes": 1, "total_keys": 6,
                        "skipped": [], "root_node": "|角色:Armature"}}
        client = Client()
        widget = self.factory(client, None)
        widget._start([("js_anim_import", {"json_path": name, "root_node": None}, name)
                       for name in ("first", "second")])
        self._wait(widget)
        self.assertEqual(client.calls[1]["root_node"], "|角色:Armature")
        widget.deleteLater()
        self.app.processEvents()

    def _wait(self, widget):
        from PySide6 import QtTest
        deadline = time.monotonic() + 5
        while widget._busy and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertFalse(widget._busy)


class LibraryPathTests(unittest.TestCase):
    def test_worktree_uses_primary_library_and_local_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            primary, worktree = base / "primary", base / "isolated"
            admin = primary / ".git" / "worktrees" / "task"
            admin.mkdir(parents=True)
            worktree.mkdir()
            (admin / "commondir").write_text("../..", encoding="utf-8")
            (worktree / ".git").write_text("gitdir: " + str(admin), encoding="utf-8")
            shared = primary / "temp" / "js_anim_pose_json"
            shared.mkdir(parents=True)
            with patch.object(paths, "project_dir", return_value=str(worktree)):
                self.assertEqual(Path(paths.local_data_dir("js_anim_pose_json")), shared)
                local = worktree / "temp" / "js_anim_pose_json"
                local.mkdir(parents=True)
                self.assertEqual(Path(paths.local_data_dir("js_anim_pose_json")), local)

    def test_frozen_uses_own_data_directory(self):
        with patch.object(paths, "project_dir", return_value="C:/QzTools"), patch.object(paths, "is_frozen", return_value=True):
            self.assertEqual(os.path.normpath(paths.local_data_dir("js_anim_pose_json")),
                             os.path.normpath("C:/QzTools/temp/js_anim_pose_json"))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("PASS %d / %d" % (result.testsRun - len(result.failures) - len(result.errors), result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
