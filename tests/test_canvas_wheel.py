"""Real viewport wheel/click events: embedded controls own their wheel area."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from ui.canvas import Canvas
from ui.plugin_loader import PluginInfo, scan


class Content(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        self.label = QtWidgets.QLabel("Wheel interaction")
        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(1, 10)
        self.spin.setValue(4)
        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.double = QtWidgets.QDoubleSpinBox()
        self.double.setRange(0, 10)
        self.double.setValue(4)
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems([f"Option {i}" for i in range(40)])
        self.combo.setMaxVisibleItems(6)
        self.combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.button = QtWidgets.QPushButton("Focus elsewhere")
        self.list = QtWidgets.QListWidget()
        self.list.addItems([f"Row {i}" for i in range(60)])
        self.list.setFixedHeight(100)
        self.text = QtWidgets.QPlainTextEdit("\n".join(f"Line {i}" for i in range(60)))
        self.text.setFixedHeight(80)
        self.area = QtWidgets.QScrollArea()
        inner = QtWidgets.QWidget()
        inner_layout = QtWidgets.QVBoxLayout(inner)
        for i in range(20):
            inner_layout.addWidget(QtWidgets.QLabel(f"Scroll label {i}"))
        self.area.setWidget(inner)
        self.area.setFixedHeight(80)
        for widget in (self.label, self.spin, self.double, self.combo, self.button,
                       self.list, self.text, self.area):
            layout.addWidget(widget)


class WheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.contents = {}
        def factory(info):
            content = Content()
            self.contents[info.id] = content
            return content
        self.canvas = Canvas(factory)
        self.canvas.resize(1100, 900)
        self.canvas.show()
        self.canvas.activateWindow()
        self.app.processEvents()
        self.open("one", 0, 0)
        self.content = self.contents["one"]
        self.canvas.centerOn(self.canvas._proxies[("maya", "one")])
        self.app.processEvents()
        self.click(self.content.button)

    def tearDown(self):
        for content in self.contents.values():
            content.combo.hidePopup()
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def open(self, name, x, y):
        self.canvas.open_plugin(PluginInfo(id=name, name=name, dcc="maya", icon="",
                                          widget_factory_path="unused.create_widget"), x=x, y=y)
        self.app.processEvents()

    def position(self, widget):
        root = widget.window()
        proxy = root.graphicsProxyWidget()
        point = widget.mapTo(root, widget.rect().center())
        return self.canvas.mapFromScene(proxy.mapToScene(QtCore.QPointF(point)))

    def click(self, widget):
        QtTest.QTest.mouseClick(self.canvas.viewport(), QtCore.Qt.LeftButton,
                               QtCore.Qt.NoModifier, self.position(widget))
        self.app.processEvents()

    def wheel(self, widget=None, delta=-120, modifiers=QtCore.Qt.NoModifier, pos=None):
        pos = pos if pos is not None else self.position(widget)
        QtTest.QTest.mouseMove(self.canvas.viewport(), pos)
        event = QtGui.QWheelEvent(QtCore.QPointF(pos),
                                 QtCore.QPointF(self.canvas.viewport().mapToGlobal(pos)),
                                 QtCore.QPoint(), QtCore.QPoint(0, delta),
                                 QtCore.Qt.NoButton, modifiers, QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(self.canvas.viewport(), event)
        self.app.processEvents()
        self.assertTrue(event.isAccepted())

    def view_state(self):
        return (self.canvas.transform(), self.canvas.horizontalScrollBar().value(),
                self.canvas.verticalScrollBar().value())

    def test_background_label_button_and_title_zoom(self):
        for widget in (self.content.label, self.content.button,
                       self.canvas._proxies[("maya", "one")].node()._title_label):
            previous = self.canvas.transform().m11()
            self.wheel(widget, delta=120)
            self.assertAlmostEqual(self.canvas.transform().m11(), previous * 1.15)
        previous = self.canvas.transform().m11()
        self.wheel(pos=QtCore.QPoint(20, 20), delta=-120)
        self.assertAlmostEqual(self.canvas.transform().m11(), previous / 1.15)

    def test_unfocused_spin_zooms_even_after_it_previously_had_focus(self):
        spin = self.content.spin
        self.click(spin)
        self.click(self.content.button)
        self.assertFalse(spin.hasFocus())
        state = self.view_state()
        self.wheel(spin, delta=120)
        self.assertEqual(spin.value(), 4)
        self.assertAlmostEqual(self.canvas.transform().m11(), state[0].m11() * 1.15)
        self.assertFalse(spin.hasFocus())

    def test_focused_numeric_controls_use_wheel_after_canvas_scaling(self):
        for scale in (.6, 1., 1.7):
            self.canvas.resetTransform()
            self.canvas.scale(scale, scale)
            self.canvas.centerOn(self.canvas._proxies[("maya", "one")])
            self.app.processEvents()
            for spin in (self.content.spin, self.content.double):
                spin.setValue(4)
                self.click(spin)
                state = self.view_state()
                self.wheel(spin, delta=120)
                self.assertEqual(spin.value(), 5)
                self.assertEqual(self.view_state(), state)

    def test_focused_numeric_limit_is_consumed_and_disabled_unfocused_zooms(self):
        spin = self.content.spin
        self.click(spin)
        for value, delta in ((10, 120), (1, -120)):
            spin.setValue(value)
            state = self.view_state()
            self.wheel(spin, delta=delta)
            self.assertEqual(spin.value(), value)
            self.assertEqual(self.view_state(), state)
        spin.setEnabled(False)
        self.assertFalse(spin.hasFocus())
        state = self.view_state()
        self.wheel(spin)
        self.assertAlmostEqual(self.canvas.transform().m11(), state[0].m11() / 1.15)

    def test_list_text_and_scroll_area_scroll_without_zoom_or_canvas_pan(self):
        for area in (self.content.list, self.content.text, self.content.area):
            bar = area.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            bar.setValue(0)
            state = self.view_state()
            self.wheel(area.viewport())
            self.assertGreater(bar.value(), 0)
            self.assertEqual(self.view_state(), state)
            bar.setValue(bar.maximum())
            self.wheel(area.viewport())
            self.assertEqual(self.view_state(), state)

    def test_closed_combo_requires_focus_and_ctrl_does_not_override_control(self):
        combo = self.content.combo
        state = self.view_state()
        self.wheel(combo)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertAlmostEqual(self.canvas.transform().m11(), state[0].m11() / 1.15)
        self.assertFalse(combo.hasFocus())
        state = self.view_state()
        self.click(combo)
        combo.hidePopup()
        self.app.processEvents()
        self.wheel(combo)
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual(self.view_state(), state)
        self.click(self.content.spin)
        state = self.view_state()
        self.wheel(self.content.spin, delta=120, modifiers=QtCore.Qt.ControlModifier)
        self.assertGreater(self.content.spin.value(), 4)
        self.assertEqual(self.view_state(), state)

    def test_popup_scrolls_its_list_and_never_zooms_canvas(self):
        combo = self.content.combo
        self.click(combo)
        view = combo.view()
        self.assertTrue(view.isVisible())
        bar = view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        state = self.view_state()
        self.wheel(view.viewport())
        self.assertGreater(bar.value(), 0)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(self.view_state(), state)
        bar.setValue(bar.maximum())
        self.wheel(view.viewport())
        self.assertEqual(self.view_state(), state)
        self.wheel(pos=QtCore.QPoint(20, 20))
        self.assertEqual(self.view_state(), state)
        self.content.text.verticalScrollBar().setValue(0)
        self.wheel(self.content.text.viewport())
        self.assertEqual(self.content.text.verticalScrollBar().value(), 0)
        self.assertEqual(self.view_state(), state)

    def test_topmost_card_owns_wheel_without_passthrough_or_z_changes(self):
        self.click(self.content.spin)
        self.open("two", 0, 0)
        front = self.contents["two"]
        self.click(front.spin)
        zs = [proxy.zValue() for proxy in self.canvas._proxies.values()]
        state = self.view_state()
        self.wheel(front.spin, delta=120)
        self.assertEqual(front.spin.value(), 5)
        self.assertEqual(self.content.spin.value(), 4)
        self.assertEqual([p.zValue() for p in self.canvas._proxies.values()], zs)
        self.assertEqual(self.view_state(), state)
        front.spin.setEnabled(False)
        self.wheel(front.spin, delta=120)
        self.assertEqual(self.content.spin.value(), 4)
        self.assertAlmostEqual(self.canvas.transform().m11(), state[0].m11() * 1.15)

    def test_real_skin_maximum_control_wheel_uses_the_canvas_event_path(self):
        import types
        info = next(info for info in scan(str(ROOT / "plugins"))
                    if info.key == ("maya", "skin_weights"))
        self.canvas._factory = lambda info: info.load_factory()(
            types.SimpleNamespace(connected=True), types.SimpleNamespace())
        self.canvas.open_plugin(info, x=400, y=0)
        proxy = self.canvas._proxies[info.key]
        self.canvas.centerOn(proxy)
        self.app.processEvents()
        spin = proxy.node().content().max_inf_sbox
        self.click(spin)
        state = self.view_state()
        self.wheel(spin, delta=120)
        self.assertEqual(spin.value(), 5)
        self.assertEqual(self.view_state(), state)

    def test_real_skin_closed_combo_switches_after_popup_selection(self):
        import types
        info = next(info for info in scan(str(ROOT / "plugins"))
                    if info.key == ("maya", "skin_weights"))
        self.canvas._factory = lambda info: info.load_factory()(
            types.SimpleNamespace(connected=True), types.SimpleNamespace())
        self.canvas.open_plugin(info, x=400, y=0)
        proxy = self.canvas._proxies[info.key]
        self.canvas.centerOn(proxy)
        self.app.processEvents()
        combo = proxy.node().content().mode_cbox
        self.click(combo)
        self.assertTrue(combo.view().isVisible())
        QtTest.QTest.qWait(250)  # Qt popup ignores the opening click release briefly.
        index = combo.model().index(0, 0)
        view = combo.view()
        point = view.visualRect(index).center()
        root = view.window()
        local = view.viewport().mapTo(root, point)
        pos = self.canvas.mapFromScene(root.graphicsProxyWidget().mapToScene(QtCore.QPointF(local)))
        QtTest.QTest.mouseMove(self.canvas.viewport(), pos)
        self.app.processEvents()
        QtTest.QTest.mouseClick(self.canvas.viewport(), QtCore.Qt.LeftButton,
                               QtCore.Qt.NoModifier, pos)
        self.app.processEvents()
        self.assertFalse(view.isVisible())
        self.assertTrue(combo.hasFocus())
        self.assertEqual(combo.currentIndex(), 0)
        state = self.view_state()
        self.wheel(combo)
        option = QtWidgets.QStyleOptionComboBox()
        combo.initStyleOption(option)
        hint = combo.style().styleHint(QtWidgets.QStyle.SH_ComboBox_AllowWheelScrolling, option, combo)
        self.assertEqual(combo.currentIndex(), 1, f"focused={combo.hasFocus()}, wheel_style_hint={hint}")
        self.assertEqual(self.view_state(), state)
        self.wheel(combo, delta=120)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(self.view_state(), state)
        self.click(proxy.node().content().max_inf_sbox)
        self.assertFalse(combo.hasFocus())
        self.wheel(combo)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertAlmostEqual(self.canvas.transform().m11(), state[0].m11() / 1.15)

    def test_layout_lock_still_allows_control_wheel(self):
        self.canvas.set_locked(True)
        self.click(self.content.spin)
        state = self.view_state()
        self.wheel(self.content.spin, delta=120)
        self.assertEqual(self.content.spin.value(), 5)
        self.assertEqual(self.view_state(), state)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    print(f"PASS {result.result.testsRun - len(result.result.failures) - len(result.result.errors)} / {result.result.testsRun}")
    sys.exit(0 if result.result.wasSuccessful() else 1)
