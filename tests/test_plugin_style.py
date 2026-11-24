"""插件卡片自动继承公共样式；覆盖真实控件、状态切换与作用域。"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from ui.plugin_loader import scan
from ui.plugin_node import PluginNode
from ui.plugin_widget import set_role


class Client:
    connected = True
    response = {"ok": True, "data": ["ping", "reload_actions"]}

    def call(self, *args, **kwargs):
        return self.response


class Log:
    def info(self, *args): pass
    def warn(self, *args): pass
    def error(self, *args): pass


class PluginStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setStyle("Fusion")
        cls.plugins = {p.key: p for p in scan(str(ROOT / "plugins"))}

    def setUp(self):
        self.nodes = []

    def tearDown(self):
        for node in self.nodes:
            node.close()
            node.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def card(self, key, client=None):
        info = self.plugins[key]
        content = info.load_factory()(client or Client(), Log())
        node = PluginNode(info.name, content, dcc=info.dcc)
        self.nodes.append(node)
        node.show()
        self.app.processEvents()
        return node, content

    def color(self, widget, role):
        return widget.palette().color(role).name()

    def test_every_plugin_gets_common_buttons_and_inputs(self):
        for key in self.plugins:
            with self.subTest(plugin=key):
                node, content = self.card(key)
                self.assertTrue(content.property("qzPluginContent"))
                for button in content.findChildren(QtWidgets.QPushButton):
                    self.assertFalse(button.styleSheet())
                    background, foreground = (("#4a4a4a", "#e8e8e8") if button.isEnabled()
                                              else ("#3a3a3a", "#888888"))
                    self.assertEqual(self.color(button, QtGui.QPalette.Button), background)
                    self.assertEqual(self.color(button, QtGui.QPalette.ButtonText), foreground)
                    if key == ("max", "vertex_color") and button in content.preset_buttons:
                        self.assertFalse(button.text())
                        self.assertEqual(button.toolTip(), "R:{} G:{} B:{}".format(*button.rgb))
                    elif key == ("max", "vertex_color") and button is content.scale_button:
                        self.assertEqual(button.toolTip(), "每次点击按当前颜色乘倍率，结果四舍五入为 0–255 整数。")
                    else:
                        self.assertFalse(button.toolTip())
                for control in content.findChildren(QtWidgets.QWidget):
                    if not isinstance(control, (QtWidgets.QLineEdit, QtWidgets.QComboBox)):
                        continue
                    if isinstance(control.parent(), QtWidgets.QAbstractSpinBox):
                        continue
                    self.assertEqual(self.color(control, QtGui.QPalette.Base), "#383838")

    def test_real_reload_clicks_change_semantic_status_color(self):
        client = Client()
        node, content = self.card(("maya", "reload"), client)
        button = content._btn
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(content._status.property("qzRole"), "success")
        self.assertEqual(self.color(content._status, QtGui.QPalette.WindowText), "#5ec269")
        client.response = {"ok": False, "error": "test failure"}
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(content._status.property("qzRole"), "error")
        self.assertEqual(self.color(content._status, QtGui.QPalette.WindowText), "#e05555")
        client.connected = False
        QtTest.QTest.qWait(1100)
        self.assertFalse(button.isEnabled())
        self.assertEqual(content._status.property("qzRole"), "warning")
        self.assertEqual(self.color(button, QtGui.QPalette.Button), "#3a3a3a")

    def test_skin_spin_editor_and_combo_still_work(self):
        node, content = self.card(("maya", "skin_weights"))
        spin = content.max_inf_sbox
        spin.setValue(100000)
        self.app.processEvents()
        editor = spin.findChild(QtWidgets.QLineEdit)
        self.assertGreaterEqual(editor.width(), editor.fontMetrics().horizontalAdvance("100000"))
        QtTest.QTest.mouseClick(editor, QtCore.Qt.LeftButton)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        QtTest.QTest.keyClicks(editor, "5")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Enter)
        self.assertEqual(spin.value(), 5)
        combo = content.mode_cbox
        QtTest.QTest.mouseClick(combo, QtCore.Qt.LeftButton)
        self.app.processEvents()
        view = combo.view()
        self.assertTrue(view.isVisible())
        QtTest.QTest.qWait(250)
        index = combo.model().index(1, 0)
        QtTest.QTest.mouseMove(view.viewport(), view.visualRect(index).center())
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton,
                               pos=view.visualRect(index).center())
        self.app.processEvents()
        self.assertEqual(combo.currentIndex(), 1)
        self.assertFalse(view.isVisible())

    def test_scope_and_future_children_do_not_require_plugin_qss(self):
        outside = QtWidgets.QPushButton("Outside")
        outside.ensurePolished()
        original = self.color(outside, QtGui.QPalette.Button)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        node = PluginNode("Future plugin", content, dcc="maya")
        self.nodes.append(node)
        button = QtWidgets.QPushButton("Added after card creation")
        layout.addWidget(button)
        label = QtWidgets.QLabel("Warning")
        layout.addWidget(label)
        set_role(label, "warning")
        node.show()
        self.app.processEvents()
        self.assertEqual(self.color(button, QtGui.QPalette.Button), "#4a4a4a")
        self.assertEqual(self.color(label, QtGui.QPalette.WindowText), "#e0a33a")
        node.set_selected(True)
        node.set_dcc("max")
        self.app.processEvents()
        self.assertEqual(self.color(button, QtGui.QPalette.Button), "#4a4a4a")
        self.assertEqual(self.color(outside, QtGui.QPalette.Button), original)
        self.assertEqual(self.color(label, QtGui.QPalette.WindowText), "#e0a33a")
        outside.deleteLater()

    def test_rename_choice_indicators_are_distinct_and_clickable(self):
        node, content = self.card(("maya", "rename_tool"))

        def bright_mark_pixels(control):
            option = QtWidgets.QStyleOptionButton()
            control.initStyleOption(option)
            element = (QtWidgets.QStyle.SE_RadioButtonIndicator
                       if isinstance(control, QtWidgets.QRadioButton)
                       else QtWidgets.QStyle.SE_CheckBoxIndicator)
            rect = control.style().subElementRect(element, option, control).adjusted(3, 3, -3, -3)
            image = control.grab().toImage().copy(rect)
            return sum(image.pixelColor(x, y).red() > 200
                       and image.pixelColor(x, y).green() > 200
                       and image.pixelColor(x, y).blue() > 200
                       for y in range(image.height()) for x in range(image.width()))

        self.assertGreater(bright_mark_pixels(content.sel_rb), 8)
        self.assertEqual(bright_mark_pixels(content.hier_rb), 0)
        QtTest.QTest.mouseClick(content.hier_rb, QtCore.Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(content.hier_rb.isChecked())
        self.assertFalse(content.sel_rb.isChecked())
        self.assertGreater(bright_mark_pixels(content.hier_rb), 8)
        self.assertEqual(bright_mark_pixels(content.sel_rb), 0)

        for checkbox in (content.add_prefix_cb, content.add_suffix_cb):
            checkbox.setChecked(False)  # 像素场景显式设未选中，允许插件自定义默认值。
            self.app.processEvents()
            self.assertEqual(bright_mark_pixels(checkbox), 0)
            QtTest.QTest.mouseClick(checkbox, QtCore.Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(checkbox.isChecked())
            self.assertGreater(bright_mark_pixels(checkbox), 8)
        QtTest.QTest.mouseClick(content.add_suffix_cb, QtCore.Qt.LeftButton)
        self.assertFalse(content.add_suffix_cb.isChecked())
        self.assertTrue(content.add_prefix_cb.isChecked())
        self.assertEqual(bright_mark_pixels(content.add_suffix_cb), 0)
        content.add_prefix_cb.setEnabled(False)
        QtTest.QTest.mouseClick(content.add_prefix_cb, QtCore.Qt.LeftButton)
        self.assertTrue(content.add_prefix_cb.isChecked())
        self.assertEqual(bright_mark_pixels(content.add_prefix_cb), 0)

if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    print(f"PASS {result.testsRun - len(result.failures) - len(result.errors)} / {result.testsRun}")
    sys.exit(0 if result.wasSuccessful() else 1)
