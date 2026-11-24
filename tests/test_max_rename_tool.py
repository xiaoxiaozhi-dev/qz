"""Exercise the Max card through normal plugin discovery and real Qt layout."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6 import QtWidgets
from ui import plugin_loader
from ui.app_style import apply_application_style
from ui.plugin_widget import apply_plugin_style


class MaxRenameCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)

    def setUp(self):
        info = plugin_loader.index_by_key(plugin_loader.scan(str(ROOT / "plugins")))[
            ("max", "rename_tool")]
        self.assertEqual(info.name, "批量重命名")
        self.client = Mock()
        self.client.call.return_value = {"ok": True, "data": {"count": 1}}
        self.widget = info.load_factory()(self.client, Mock())
        self.addCleanup(self.widget.close)
        apply_plugin_style(self.widget)

    def test_buttons_route_max_actions(self):
        card = self.widget
        card.prefix_edit.setText("L_")
        card.prefix_btn.click()
        self.client.call.assert_called_once_with("rename_tool", op="prefix", prefix="L_", hierarchy=False)
        card.hier_rb.setChecked(True)
        card.suffix_edit.setText("_geo")
        card.suffix_btn.click()
        self.client.call.assert_called_with("rename_tool", op="suffix", suffix="_geo", hierarchy=True)
        card.search_edit.setText("test")
        card.replace_btn.click()
        self.client.call.assert_called_with("rename_tool", op="replace", search="test", replace="", hierarchy=True)
        card.base_edit.setText("bone_")
        card.add_suffix_cb.setChecked(False)
        card.rename_btn.click()
        self.client.call.assert_called_with("rename_tool", op="rename", base="bone_", prefix="L_",
                                           suffix="_geo", use_prefix=True, use_suffix=False, hierarchy=True)
        card.index_spin.setValue(3)
        for button, index in [(card.del_index_btn, 2), (card.del_start_btn, 0), (card.del_end_btn, -1)]:
            button.click()
            self.client.call.assert_called_with("rename_tool", op="delete_index", index=index, hierarchy=True)
        card.check_btn.click()
        self.client.call.assert_called_with("rename_tool", op="check_duplicates")

    def test_replace_button_fills_both_rows(self):
        card = self.widget
        card.show()
        for width, height in [(card.minimumSizeHint().width(), card.sizeHint().height()), (550, 480)]:
            card.resize(width, height)
            self.app.processEvents()
            grid = card.layout().itemAt(0).layout()
            search = grid.cellRect(3, 1)
            replace = grid.cellRect(4, 1)
            button = card.replace_btn.geometry()
            self.assertLess(search.bottom(), replace.top())
            self.assertGreater(button.left(), search.right())
            self.assertEqual(button.top(), search.top())
            self.assertEqual(button.bottom(), replace.bottom())


if __name__ == "__main__":
    unittest.main()
