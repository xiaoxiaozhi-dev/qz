"""Exercise animation confirmations in a real Qt graphics canvas, without Maya/JSON."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
import paths

spec = importlib.util.spec_from_file_location("anim_dialog_widget", HERE / "plugins/maya/js_anim_tools/widget.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            from ui.app_style import apply_application_style
        except ImportError:
            pass
        else:
            apply_application_style(cls.app)
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        if font_path.is_file():
            font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
            if families:
                cls.app.setFont(QtGui.QFont(families[0], 9))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = QtCore.QSettings(str(Path(self.directory.name, 'settings.ini')), QtCore.QSettings.IniFormat)
        settings_patch = patch.object(QtCore, 'QSettings', return_value=settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        with patch.object(paths, "local_data_dir", return_value=self.directory.name):
            self.widget = module.create_widget(None, None)
        self.widget._fill_layers({"layers": [dict(layer="AJT_test", name="测试动作<角色>",
                                                  start=0, end=80, weight=1)]})
        self.widget.tabs.setCurrentIndex(1)
        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setGeometry(40, 50, 720, 500)
        self.view.setSceneRect(-5000, -5000, 10000, 10000)
        from ui.plugin_node import PluginNode
        self.node = PluginNode("JS 动作工具", self.widget)
        self.proxy = self.scene.addWidget(self.node)
        self.proxy.setPos(1800, -1200)
        self.view.show()
        self.app.processEvents()
        self.view.centerOn(self.proxy)
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.scene.clear()
        self.view.deleteLater()
        self.scene.deleteLater()
        self.app.processEvents()

    def _delete(self, answer, screenshot=False, centered=False):
        failures = []
        inspected = []

        def inspect_and_answer():
            inspected.append(True)
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog, QtWidgets.QMessageBox)
                self.assertIsNone(dialog.graphicsProxyWidget())
                self.assertIs(dialog.parentWidget(), self.view.window())
                self.assertEqual(dialog.windowTitle(), "删除动作图层")
                self.assertIn("测试动作<角色>", dialog.text())
                self.assertIn("可在 Maya 中撤销", dialog.text())
                self.assertEqual(dialog.textFormat(), QtCore.Qt.PlainText)
                labels = [label for label in dialog.findChildren(QtWidgets.QLabel)
                          if label.isVisible() and "测试动作" in label.text()]
                self.assertEqual(len(labels), 1)
                self.assertGreater(labels[0].width(), 0)
                for button, caption in ((QtWidgets.QMessageBox.Yes, "确定"),
                                        (QtWidgets.QMessageBox.No, "取消")):
                    self.assertTrue(dialog.button(button).isVisible())
                    self.assertEqual(dialog.button(button).text(), caption)
                self.assertIs(dialog.defaultButton(), dialog.button(QtWidgets.QMessageBox.No))
                bounds = QtCore.QRect(self.view.viewport().mapToGlobal(QtCore.QPoint()),
                                      self.view.viewport().size())
                self.assertTrue(bounds.contains(dialog.frameGeometry()), (bounds, dialog.frameGeometry()))
                if centered:
                    center = self.view.viewport().mapToGlobal(self.view.mapFromScene(
                        self.proxy.mapToScene(self.proxy.boundingRect().center())))
                    self.assertLessEqual((dialog.frameGeometry().center() - center).manhattanLength(), 4)
                if screenshot and os.environ.get("QZ_ANIM_DIALOG_SCREENSHOT"):
                    dialog.grab().save(os.environ["QZ_ANIM_DIALOG_SCREENSHOT"])
                if answer == "enter":
                    QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Return)
                elif answer == "escape":
                    QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
                elif answer == "close":
                    dialog.close()
                else:
                    QtTest.QTest.mouseClick(dialog.button(answer), QtCore.Qt.LeftButton)
            except BaseException as error:
                failures.append(error)
                if dialog is not None:
                    dialog.reject()

        timer = QtCore.QTimer(self.view)
        timer.setSingleShot(True)
        timer.timeout.connect(inspect_and_answer)
        timer.start(0)
        with patch.object(self.widget, "_start") as start:
            # Click through the graphics viewport, as the production card does.
            point = self.proxy.mapToScene(self.proxy.subWidgetRect(self.widget.delete_button).center())
            QtTest.QTest.mouseClick(self.view.viewport(), QtCore.Qt.LeftButton,
                                   pos=self.view.mapFromScene(point))
            timer.stop()
            self.assertTrue(inspected, "删除按钮未打开确认窗口")
            if failures:
                raise failures[0]
            if answer == QtWidgets.QMessageBox.Yes:
                start.assert_called_once_with([("js_anim_delete", {"layer": "AJT_test"}, "删除图层")])
            else:
                start.assert_not_called()
        self.app.processEvents()

    def test_confirm_visible_at_card_center_after_zoom_and_pan(self):
        for scale in (0.65, 1.25):
            with self.subTest(scale=scale):
                self.view.resetTransform()
                self.view.scale(scale, scale)
                self.view.centerOn(self.proxy)
                self.app.processEvents()
                self._delete(QtWidgets.QMessageBox.Yes, screenshot=True, centered=True)

    def test_clamp_when_card_is_partly_outside_canvas(self):
        self.view.scale(0.7, 0.7)
        self.view.centerOn(self.proxy)
        self.app.processEvents()
        scene_center = self.view.mapToScene(self.view.viewport().rect().center())
        target = self.view.mapToScene(QtCore.QPoint(-10, self.view.viewport().height() // 2))
        self.proxy.setPos(target - self.proxy.boundingRect().center())
        self.view.centerOn(scene_center)
        self.app.processEvents()
        self._delete(QtWidgets.QMessageBox.No)

    def test_cancel_enter_escape_and_close_do_not_delete(self):
        for answer in (QtWidgets.QMessageBox.No, "enter", "escape", "close"):
            with self.subTest(answer=answer):
                self._delete(answer)

    def test_base_animation_does_not_open_delete_confirmation(self):
        self.widget.layer_list.setCurrentRow(0)
        with patch.object(self.widget, "_confirm") as confirm, patch.object(self.widget, "_start") as start:
            self.widget._delete_layer()
            confirm.assert_not_called()
            start.assert_not_called()

    def test_template_picker_belongs_to_real_window_in_canvas(self):
        template = str(Path(self.directory.name, 'F1_标准.fbx'))
        Path(template).write_bytes(b'fbx')
        self.widget.export_toggle.setChecked(True)
        self.app.processEvents()
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(template, '')) as browse:
            self.widget.template_browse.trigger()
        self.assertIs(browse.call_args.args[0], self.view.window())
        self.assertEqual(self.widget.template_edit.text(), template)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DialogTests))
    print("PASS %d / %d" % (result.testsRun - len(result.failures) - len(result.errors), result.testsRun))
    sys.exit(0 if result.wasSuccessful() else 1)
