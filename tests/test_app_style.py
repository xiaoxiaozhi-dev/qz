"""Check the real Qt theme on shell controls, popups and embedded plugins."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from ui.app_style import apply_application_style
from ui.activity_bar import ActivityBar
from ui.canvas_search import SearchOverlay
from ui.plugin_loader import scan
from ui.plugin_node import PluginNode
from ui.settings_panel import SettingsPanel
from ui.widgets.log_panel import LogPanel


class AppStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)

    def setUp(self):
        self.windows = []

    def tearDown(self):
        QtWidgets.QToolTip.hideText()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def show(self, widget, size=(420, 950)):
        self.windows.append(widget)
        widget.resize(*size)
        widget.show()
        self.app.processEvents()
        return widget

    def color(self, widget, role):
        return widget.palette().color(role).name()

    def test_settings_buttons_and_spinbox_remain_readable_under_main_background(self):
        root = QtWidgets.QWidget()
        root.setObjectName('MainCentral')
        layout = QtWidgets.QVBoxLayout(root)
        panel = SettingsPanel()
        layout.addWidget(panel)
        self.show(root)
        for button in panel.findChildren(QtWidgets.QPushButton):
            if not button.text():
                continue
            self.assertFalse(button.styleSheet())
            self.assertEqual(self.color(button, QtGui.QPalette.Button), '#4a4a4a' if button.isEnabled() else '#3a3a3a')
            self.assertEqual(self.color(button, QtGui.QPalette.ButtonText), '#e8e8e8' if button.isEnabled() else '#888888')
            self.assertGreaterEqual(button.height(), button.fontMetrics().height() + 6)
        self.assertIs(panel._connection_buttons['maya'].parent(), panel._state_labels['maya'].parent())
        self.assertIs(panel._connection_buttons['max'].parent(), panel._state_labels['max'].parent())
        self.assertEqual(self.color(panel._log_spin, QtGui.QPalette.Text), '#dddddd')
        self.assertEqual(self.color(panel._log_spin, QtGui.QPalette.Base), '#383838')
        states = dict(maya=True, max=False)
        panel.set_bridge_states(states, '127.0.0.1', dict(maya=17023, max=17024), {})
        self.assertEqual(self.color(panel._dots['maya'], QtGui.QPalette.Window), '#4ec9b0')
        self.assertEqual(self.color(panel._dots['max'], QtGui.QPalette.Window), '#f14c4c')

    def test_tooltip_uses_light_text_and_padding_with_its_real_owner(self):
        panel = self.show(SettingsPanel())
        owner = panel._connection_buttons['maya']
        QtWidgets.QToolTip.showText(QtCore.QPoint(100, 100), 'Maya 2024 已连接', owner)
        self.app.processEvents()
        tip = next(w for w in self.app.topLevelWidgets() if w.objectName() == 'qtooltip_label')
        self.assertEqual(self.color(tip, QtGui.QPalette.ToolTipText), '#eeeeee')
        self.assertEqual(self.color(tip, QtGui.QPalette.ToolTipBase), '#303033')
        self.assertGreater(tip.width(), tip.fontMetrics().horizontalAdvance(tip.text().splitlines()[0]))
        self.assertLess(tip.width(), 480)
        self.assertEqual(self.app.palette().color(QtGui.QPalette.Inactive, QtGui.QPalette.ToolTipText).name(), '#eeeeee')

    def test_spinbox_arrows_and_keyboard_editing_work(self):
        panel = self.show(SettingsPanel())
        spin = panel._log_spin
        changes = []
        panel.log_limit_changed.connect(changes.append)
        spin.setValue(1000)
        option = QtWidgets.QStyleOptionSpinBox()
        spin.initStyleOption(option)
        up = spin.style().subControlRect(QtWidgets.QStyle.CC_SpinBox, option, QtWidgets.QStyle.SC_SpinBoxUp, spin)
        self.assertFalse(up.isEmpty())
        QtTest.QTest.mouseClick(spin, QtCore.Qt.LeftButton, pos=up.center())
        self.assertEqual(spin.value(), 1100)
        editor = spin.findChild(QtWidgets.QLineEdit)
        QtTest.QTest.mouseClick(editor, QtCore.Qt.LeftButton)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        QtTest.QTest.keyClicks(editor, '100000')
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Enter)
        self.assertEqual(spin.value(), 100000)
        self.assertIn(1100, changes)
        self.assertGreaterEqual(editor.width(), editor.fontMetrics().horizontalAdvance('100000'))

    def test_standard_edit_menu_has_same_dark_theme_as_custom_menus(self):
        panel = self.show(SettingsPanel())
        menu = panel._log_spin.findChild(QtWidgets.QLineEdit).createStandardContextMenu()
        self.windows.append(menu)
        menu.ensurePolished()
        self.assertEqual(self.color(menu, QtGui.QPalette.Window), '#252526')
        self.assertEqual(self.color(menu, QtGui.QPalette.WindowText), '#dddddd')
        self.assertTrue(any(not action.isEnabled() for action in menu.actions()))

    def test_search_and_log_internal_scrollbars_share_dimensions(self):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        search, log = SearchOverlay(), LogPanel()
        layout.addWidget(search)
        layout.addWidget(log)
        search._list.addItems(['item %d' % i for i in range(40)])
        log._view.setPlainText('\n'.join('line %d' % i for i in range(200)))
        self.show(root, (420, 650))
        for view in (search._list, log._view):
            self.assertTrue(view.verticalScrollBar().isVisible())
            self.assertEqual(view.verticalScrollBar().width(), 10)

    def test_activity_icons_have_distinct_selected_and_disabled_states(self):
        bar = self.show(ActivityBar(), (48, 400))
        icon = bar._btn_settings.icon()
        def brightness(mode, state):
            image = icon.pixmap(22, 22, mode, state).toImage()
            values = [image.pixelColor(x, y).lightness() for y in range(image.height())
                      for x in range(image.width()) if image.pixelColor(x, y).alpha() > 128]
            return sum(values) / len(values)
        normal = brightness(QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.assertGreater(brightness(QtGui.QIcon.Normal, QtGui.QIcon.On), normal + 40)
        self.assertLess(brightness(QtGui.QIcon.Disabled, QtGui.QIcon.Off), normal - 30)

    def test_embedded_plugin_keeps_controls_and_title_geometry(self):
        info = next(p for p in scan(str(ROOT/'plugins')) if p.key == ('maya', 'rename_tool'))
        class Client:
            connected = True
        class Log:
            def info(self, *args): pass
            warn = error = info
        content = info.load_factory()(Client(), Log())
        node = PluginNode(info.name, content, dcc='maya')
        scene = QtWidgets.QGraphicsScene()
        scene.addWidget(node)
        view = self.show(QtWidgets.QGraphicsView(scene), (850, 600))
        self.assertEqual(self.color(content.add_prefix_cb, QtGui.QPalette.WindowText), '#dddddd')
        self.assertGreater(node.title_height(), node._title_label.fontMetrics().height())
        before = node.title_height()
        QtTest.QTest.mouseClick(content.hier_rb, QtCore.Qt.LeftButton)
        self.assertTrue(content.hier_rb.isChecked())
        node.set_selected(True)
        self.app.processEvents()
        self.assertEqual(node.title_height(), before)
        self.assertEqual(self.color(node._title_label, QtGui.QPalette.Window), '#0f6e56')
        view.setScene(None)
        scene.clear()


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=2).result
    print('PASS %d / %d' % (result.testsRun-len(result.failures)-len(result.errors), result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
