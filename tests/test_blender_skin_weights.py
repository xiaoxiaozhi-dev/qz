"""Real Qt card events and common archive behavior for the Blender weight card."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6 import QtCore, QtTest, QtWidgets
from blender_actions import skin_weights as blender_action
from ui.plugin_loader import scan
from ui.plugin_widget import apply_plugin_style


OPERATIONS = runpy.run_path(str(ROOT / "plugins/blender/skin_weights/operations.py"))
ROWS = np.array([[.225678987, .325432156, .448888857], [.4, .8, 0], [.56, 0, .44]], dtype="<f8")
MESH = dict(index=0, mesh="Mesh", name="Mesh", vertices=3, identity={"object": 1})
BONES = dict(influences=3, bone_names=["A", "B", "C"])
GEOMETRY = dict(triangles=1, space="object", positions="skin_input")


class Client:
    connected = True

    def __init__(self):
        self.jobs = {}
        self.calls = []

    def call(self, action, timeout=4, connect_timeout=1, *, op, token, **options):
        assert action == "skin_weights_blender", action
        if op == "status":
            return dict(ok=True, data=dict(state="done", data=self.jobs.pop(token)))
        operation = options["operation"]
        self.calls.append((operation, options.get("meshes", []), threading.current_thread()))
        prefix = Path(options["directory"]) / "mesh-0"
        data = dict(errors=[], reports=[], selection_changed=False, selected_vertices=0)
        if operation == "context":
            data["meshes"] = [MESH]
        elif operation in ("snapshot", "export"):
            ROWS.tofile(prefix.with_suffix(".weights"))
            entry = dict(MESH, **BONES)
            if operation == "export":
                np.zeros(3, dtype="<f8").tofile(prefix.with_suffix(".dq"))
                np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0)], dtype="<f8").tofile(prefix.with_suffix(".points"))
                np.array([0, 1, 2], dtype="<u4").tofile(prefix.with_suffix(".triangles"))
                entry.update(geometry=GEOMETRY, skinning_method=0)
            data["meshes"] = [entry]
        elif operation == "map":
            entry, = options["meshes"]
            report = dict(index=0, target_geometry="valid", target_rig=2, target_names={0: "A", 1: "B", 2: "C"})
            if entry["transfer_mode"] == "closest_point":
                np.array([[0, 0, 0], [1, 1, 1], [0, 1, 2]], dtype="<u4").tofile(prefix.with_suffix(".match_ids"))
                np.array([[1, 0, 0], [1, 0, 0], [.25, .25, .5]], dtype="<f8").tofile(prefix.with_suffix(".match_factors"))
                report.update(direct_vertices=2, interpolated_vertices=1)
            data["reports"] = [report]
        elif operation == "import":
            entry, = options["meshes"]
            data["reports"] = [dict(name=entry["name"], vertices=entry["vertices"],
                                    transfer_mode=entry["transfer_mode"], direct_vertices=2, interpolated_vertices=1)]
        else:
            data["count"] = sum(entry["count"] for entry in options["meshes"])
        self.jobs[token] = data
        return dict(ok=True, data=dict(state="pending"))


class Log:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)

    warn = error = info


class BlenderSkinWeightsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        root = patch("paths.project_dir", return_value=directory.name)
        root.start()
        self.addCleanup(root.stop)
        self.client = Client()
        self.archive = self.directory / "temp" / "skin_weights" / "Mesh.qzskin"

    def test_card_controls_through_real_mouse_events(self):
        plugin = next(info for info in scan(str(ROOT / "plugins")) if info.key == ("blender", "skin_weights"))
        self.assertEqual(plugin.name, "蒙皮权重")
        log = Log()
        card = plugin.load_factory()(self.client, log)
        with patch("paths.resource_dir", return_value=str(ROOT / "ui" / "resources")):
            apply_plugin_style(card)
        card.show()
        def close_card():
            deadline = time.monotonic() + 5
            while card._task is not None and time.monotonic() < deadline:
                time.sleep(.01)
                self.app.processEvents()
            card.close()

        self.addCleanup(close_card)
        card.max_inf_sbox.setValue(2)
        card.mode_cbox.setCurrentIndex(1)

        def click(button):
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.assertFalse(button.isEnabled())
            deadline = time.monotonic() + 5
            while not button.isEnabled() and time.monotonic() < deadline:
                time.sleep(.005)
                self.app.processEvents()
            self.assertTrue(button.isEnabled())

        click(card.check_btn)
        self.assertEqual(card.inf_lab.text(), "3")
        click(card.select_btn)
        click(card.fix_btn)
        self.assertEqual(card.inf_lab.text(), "2")
        click(card.export_btn)
        self.assertEqual(card.inf_lab.text(), "2")
        card.import_mode_cbox.setCurrentIndex(1)
        click(card.import_btn)
        self.assertEqual(card.inf_lab.text(), "需重新检测")
        self.assertEqual([entry[0] for entry in self.client.calls],
                         ["snapshot", "snapshot", "select_excess", "snapshot", "limit",
                          "export", "context", "map", "import"])
        self.assertTrue(all(thread is not threading.main_thread() for _, _, thread in self.client.calls))
        self.assertEqual(self.client.calls[-1][1][0]["transfer_mode"], "closest_point")
        self.assertTrue(any("表面插值 1" in message for message in log.messages))

    def test_full_archive_precision_and_rejection_before_mapping(self):
        exported = OPERATIONS["run_operation"](self.client, "export_file", threshold=1)
        self.assertEqual(exported["data"]["count"], 1, exported)
        with zipfile.ZipFile(self.archive) as archive:
            content = {key: archive.read(key) for key in archive.namelist()}
        manifest = json.loads(content["manifest.json"])
        self.assertEqual((manifest["source"], manifest["version"], manifest["skinning_method"]), ("blender", 3, 0))
        self.assertEqual(set(content), {"manifest.json", "offsets.bin", "indices.bin", "weights.bin",
                                        "dq.bin", "points.bin", "triangles.bin"})
        np.testing.assert_array_equal(np.frombuffer(content["weights.bin"], dtype="<f8"), ROWS[ROWS > 0])
        self.assertFalse(np.frombuffer(content["dq.bin"], dtype="<f8").any())

        for values, fields, error in (
            ({}, {"version": 2}, "没有几何证明"),
            ({}, {"skinning_method": 2}, "无法无损"),
            ({"dq.bin": np.array([0, .25, 0], dtype="<f8").tobytes()}, {}, "无法无损"),
            ({"weights.bin": np.full(7, 1.1, dtype="<f8").tobytes()}, {}, "范围或精度"),
        ):
            with self.subTest(error=error):
                entries = dict(content, **values)
                entries["manifest.json"] = json.dumps(dict(manifest, **fields)).encode("utf-8")
                with zipfile.ZipFile(self.archive, "w") as archive:
                    for member, value in entries.items():
                        archive.writestr(member, value)
                before = len(self.client.calls)
                result = OPERATIONS["run_operation"](self.client, "import_file")
                self.assertEqual(result["data"]["count"], 0, result)
                self.assertIn(error, result["data"]["errors"][0]["error"])
                self.assertEqual([call[0] for call in self.client.calls[before:]], ["context"])

    def test_pending_host_action_waits_for_interface_to_unlock(self):
        callbacks = []
        manager = SimpleNamespace(is_interface_locked=False)
        context = SimpleNamespace(window=SimpleNamespace(scene=SimpleNamespace(session_uid=7)),
                                  window_manager=manager)
        fake = SimpleNamespace(context=context, app=SimpleNamespace(driver_namespace={},
                               timers=SimpleNamespace(register=callbacks.append)))
        token = uuid.uuid4().hex
        with patch.dict(sys.modules, {"bpy": fake}), patch.object(blender_action, "run", return_value={}) as run:
            request = blender_action.skin_weights
            self.assertEqual(request("submit", token, "import", directory=str(self.directory)), {"state": "pending"})
            manager.is_interface_locked = True
            callback, = callbacks
            self.assertEqual(callback(), .05)
            run.assert_not_called()
            self.assertEqual(request("status", token), {"state": "pending"})
            manager.is_interface_locked = False
            self.assertIsNone(callback())
            run.assert_called_once_with("import", scene=7, directory=str(self.directory))
            self.assertEqual(request("status", token), {"state": "done", "data": {}})


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    print(f"PASS {result.testsRun - len(result.failures) - len(result.errors)} / {result.testsRun}")
    sys.exit(0 if result.wasSuccessful() else 1)
