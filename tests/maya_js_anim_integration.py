"""独立 Maya 进程集成测试，不连接现有 Maya。

mayapy.exe tests/maya_js_anim_integration.py
也可由 mayabatch 运行本文件并传入 --initialized。
"""

import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


class MayaIntegration(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.currentUnit(time="film", linear="cm", angle="deg")
        cmds.namespace(add="角色")
        self.root = cmds.createNode("transform", name="角色:Armature")
        self.hip = cmds.createNode("joint", name="角色:hip", parent=self.root)
        self.hand = cmds.createNode("joint", name="角色:hand", parent=self.hip)
        cmds.playbackOptions(minTime=2, maxTime=40)
        cmds.currentTime(12)
        cmds.select(self.root, replace=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "中文动作.json"
        self.path.write_text(json.dumps({"nodes": {
            "Armature": {"parent": None, "ch": {"rx": [[0, -90]]}},
            "hip": {"parent": None, "ch": {"tx": [[0, 1], [10, 5]]}},
            "hand": {"parent": "hip", "ch": {"ry": [[0, 12]]}},
        }}), encoding="utf-8")

    def test_legacy_namespace_zero_fill_and_layer_values(self):
        result = core.import_animation(str(self.path))
        self.assertEqual(result["imported_nodes"], 3)
        self.assertEqual(result["skipped"], [])
        self.assertAlmostEqual(cmds.getAttr(self.root + ".rx", time=0), -90)
        self.assertAlmostEqual(cmds.getAttr(self.hip + ".tx", time=0), 1)
        self.assertAlmostEqual(cmds.getAttr(self.hip + ".tx", time=10), 5)
        self.assertAlmostEqual(cmds.getAttr(self.hip + ".rz", time=0), 0)
        self.assertAlmostEqual(cmds.getAttr(self.hand + ".ry", time=0), 12)
        self.assertEqual(cmds.playbackOptions(q=True, minTime=True), 0)
        self.assertEqual(cmds.playbackOptions(q=True, maxTime=True), 10)
        self.assertEqual(cmds.ls(selection=True, long=True), cmds.ls(self.root, long=True))

    def test_import_is_one_undo(self):
        result = core.import_animation(str(self.path))
        cmds.undo()
        self.assertFalse(cmds.objExists(result["layer"]))
        self.assertFalse(cmds.ls("qzAnimTransaction*"))

    def test_non_root_current_namespace_still_creates_root_layer(self):
        cmds.namespace(setNamespace=":角色")
        try:
            result = core.import_animation(str(self.path), root_node=cmds.ls(self.root, long=True)[0])
            self.assertTrue(result["layer"].startswith("AJT_"))
            self.assertTrue(cmds.objExists(":" + result["layer"]))
        finally:
            cmds.namespace(setNamespace=":")

    def test_failed_replacement_restores_old_layer_and_curves(self):
        result = core.import_animation(str(self.path))
        before_curves = sorted(cmds.ls(type="animCurve") or [])
        old_command = cmds.setKeyframe
        calls = [0]
        def fail_after_five(*args, **kwargs):
            calls[0] += 1
            if calls[0] > 5:
                raise RuntimeError("injected key failure")
            return old_command(*args, **kwargs)
        with patch.object(cmds, "setKeyframe", side_effect=fail_after_five):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                core.import_animation(str(self.path))
        self.assertTrue(cmds.objExists(result["layer"]))
        self.assertEqual(before_curves, sorted(cmds.ls(type="animCurve") or []))
        self.assertAlmostEqual(cmds.getAttr(self.hip + ".tx", time=10), 5)
        self.assertFalse(cmds.ls("AJT_import_*"))
        self.assertFalse(cmds.ls("qzAnimTransaction*"))

    def test_export_preserves_scale_curves_locks_selection_and_time(self):
        cmds.setKeyframe(self.hip, attribute="tx", time=0, value=0)
        cmds.setKeyframe(self.hip, attribute="tx", time=4, value=0.00024,
                        inTangentType="linear", outTangentType="linear")
        cmds.setKeyframe(self.hip, attribute="sx", time=0, value=1.5)
        cmds.setKeyframe(self.hip, attribute="sx", time=4, value=2)
        cmds.setAttr(self.hand + ".scaleY", 1.7, lock=True)
        cmds.playbackOptions(minTime=0, maxTime=4)
        cmds.currentTime(3)
        curves = sorted(cmds.ls(type="animCurve") or [])
        selection = cmds.ls(selection=True, long=True)
        output = Path(self.tmp.name) / "export.json"
        core.export_animation(str(output))
        data = core.read_animation(output)
        self.assertEqual(data["end_frame"], 4)
        self.assertEqual(data["nodes"]["hip"]["ch"]["tx"][-1], [4, 0.00024])
        self.assertEqual(curves, sorted(cmds.ls(type="animCurve") or []))
        self.assertEqual(selection, cmds.ls(selection=True, long=True))
        self.assertEqual(cmds.currentTime(q=True), 3)
        self.assertTrue(cmds.getAttr(self.hand + ".scaleY", lock=True))
        self.assertAlmostEqual(cmds.getAttr(self.hand + ".scaleY"), 1.7)

    def test_base_animation_and_delete_restore_original_range(self):
        result = core.import_animation(str(self.path))
        core.activate_layer()
        self.assertEqual(cmds.playbackOptions(q=True, minTime=True), 2)
        self.assertEqual(cmds.playbackOptions(q=True, maxTime=True), 40)
        self.assertEqual(cmds.currentTime(q=True), 12)
        core.activate_layer(result["layer"])
        core.delete_layer(result["layer"])
        self.assertEqual(cmds.playbackOptions(q=True, maxTime=True), 40)

    def test_actual_legacy_file_on_matching_namespaced_skeleton(self):
        import paths
        files = list(Path(paths.local_data_dir("js_anim_pose_json")).glob("*.json"))
        if not files:
            self.skipTest("未配置用户旧动作库")
        source = min(files, key=lambda path: path.stat().st_size)
        data = core.read_animation(source)
        cmds.file(new=True, force=True)
        cmds.namespace(add="测试角色")
        created = {}
        root = cmds.createNode("transform", name="测试角色:Armature")
        created["Armature"] = root
        def ensure(name):
            if name not in created:
                record = data["nodes"].get(name, {})
                parent_name = record.get("parent")
                parent = ensure(parent_name) if parent_name else root
                created[name] = cmds.createNode("joint", name="测试角色:" + name, parent=parent)
            return created[name]
        for name in data["nodes"]:
            ensure(name)
        cmds.select(root, replace=True)
        result = core.import_animation(str(source))
        self.assertEqual(result["imported_nodes"], len(data["nodes"]))
        self.assertEqual(result["skipped"], [])
        for name, record in list(data["nodes"].items())[:8]:
            for channel, attr in core.CHANNELS.items():
                frame, value = record.get("ch", {}).get(channel, [[data["start_frame"], 0]])[0]
                self.assertAlmostEqual(cmds.getAttr(created[name] + "." + attr, time=frame), value, places=3)
        print("REAL MAYA LEGACY FILE:", source.name, result["imported_nodes"], "nodes", flush=True)


if __name__ == "__main__":
    initialized = "--initialized" in sys.argv
    if not initialized:
        import maya.standalone
        maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from maya_actions import _js_anim_core as core
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MayaIntegration))
    status = 0 if result.wasSuccessful() else 1
    print("MAYA PASS %d / %d" % (result.testsRun - len(result.failures) - len(result.errors), result.testsRun), flush=True)
    if initialized:
        cmds.quit(force=True, exitCode=status)
    else:
        import maya.standalone
        maya.standalone.uninitialize()
        sys.exit(status)
