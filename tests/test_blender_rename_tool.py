"""Open the Blender rename card from the resource tree and exercise its Qt controls."""

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from blender_actions.rename_tool import _delete_character
from ui.main_window import MainWindow


class BlenderRenameTests(unittest.TestCase):
    def test_delete_character_uses_unicode_characters(self):
        self.assertEqual(_delete_character("A骨骼B", 1), "A骼B")
        self.assertEqual(_delete_character("A骨骼B", -1), "A骨骼")
        self.assertEqual(_delete_character("A骨骼B", -2), "A骨B")
        self.assertEqual(_delete_character("A骨骼B", 255), "A骨骼B")
        self.assertEqual(_delete_character("骨", 0), "")

    def test_resource_entry_layout_and_clicks(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            env = {"QZ_DATA_DIR": directory, "QZ_USER_PLUGINS_DIR": str(Path(directory) / "scripts")}
            with patch.dict(os.environ, env), patch.object(MainWindow, "_start_bridge_timer"):
                window = MainWindow()
                try:
                    self.exercise_card(window, app)
                finally:
                    window.close()
                    app.processEvents()

    def exercise_card(self, window, app):
        calls = []

        def call(action, *, timeout, connect_timeout, **params):
            calls.append((action, params, threading.current_thread()))
            if params["op"] == "check_duplicates":
                return {"ok": False, "error": "Blender 桥接繁忙，请稍后重试"}
            return {"ok": True, "data": {"count": 1, "names": ["骨骼.001"],
                                         "adjusted": 1, "adjustments": [["骨骼", "骨骼.001"]]}}

        key = ("blender", "rename_tool")
        client = window._clients["blender"]
        with patch.object(client, "call", side_effect=call):
            window.resize(1000, 720)
            window.show()
            window._activity.show_panel(0)
            QtTest.QTest.qWait(80)
            tree = window._side._tree
            group = tree._groups["blender"]
            group.setExpanded(True)
            item = next(group.child(i) for i in range(group.childCount())
                        if group.child(i).data(0, QtCore.Qt.UserRole) == ("plugin", key))
            self.assertEqual(item.text(0), "批量重命名")
            tree.scrollToItem(item)
            QtTest.QTest.mouseDClick(tree.viewport(), QtCore.Qt.LeftButton,
                                     pos=tree.visualItemRect(item).center())
            app.processEvents()
            card = window._canvas._proxies[key].widget().content()
            self.assertIs(card.client, client)
            self.assertTrue(card.isVisible())
            self.assertTrue(card.sel_rb.isChecked())

            search, replace = card.search_edit.geometry(), card.replace_edit.geometry()
            button = card.replace_btn.geometry()
            self.assertLess(search.bottom(), replace.top())
            self.assertEqual(search.left(), replace.left())
            self.assertGreater(button.left(), search.right())
            self.assertEqual(button.top(), search.top())
            self.assertEqual(button.bottom(), replace.bottom())

            def click(widget, **expected):
                before = len(calls)
                QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton)
                self.assertFalse(widget.isEnabled())
                deadline = QtCore.QDeadlineTimer(3000)
                while not widget.isEnabled() and not deadline.hasExpired():
                    QtTest.QTest.qWait(5)
                self.assertTrue(widget.isEnabled())
                self.assertEqual(len(calls), before + 1)
                action, params, worker = calls[-1]
                self.assertEqual(action, "rename_tool")
                self.assertEqual(params, expected)
                self.assertIsNot(worker, threading.main_thread())

            card.prefix_edit.setText("L_")
            card.suffix_edit.setText("_geo")
            click(card.prefix_btn, op="prefix", hierarchy=False, prefix="L_")
            click(card.suffix_btn, op="suffix", hierarchy=False, suffix="_geo")
            QtTest.QTest.mouseClick(card.hier_rb, QtCore.Qt.LeftButton,
                                     pos=QtCore.QPoint(8, card.hier_rb.height() // 2))
            card.search_edit.setText("本体")
            card.replace_edit.setText("骨骼")
            click(card.replace_btn, op="replace", hierarchy=True, search="本体", replace="骨骼")
            card.base_edit.setText("arm_")
            QtTest.QTest.mouseClick(card.add_suffix_cb, QtCore.Qt.LeftButton,
                                     pos=QtCore.QPoint(8, card.add_suffix_cb.height() // 2))
            click(card.rename_btn, op="rename", hierarchy=True, base="arm_", prefix="L_", suffix="_geo",
                  use_prefix=True, use_suffix=False)
            card.index_spin.setValue(3)
            click(card.del_index_btn, op="delete_index", hierarchy=True, index=2)
            click(card.del_start_btn, op="delete_index", hierarchy=True, index=0)
            click(card.del_end_btn, op="delete_index", hierarchy=True, index=-1)
            click(card.check_btn, op="check_duplicates", hierarchy=True)
            log = window._log.toPlainText()
            self.assertIn("骨骼 → 骨骼.001", log)
            self.assertIn("Blender 桥接繁忙", log)


if __name__ == "__main__":
    unittest.main()
