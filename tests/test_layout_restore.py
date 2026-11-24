"""Real window close/reopen preserves tree groups and the viewport's scene center."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6 import QtCore, QtTest, QtWidgets
from ui import layout_state
from ui.main_window import MainWindow


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'layout.json'
        self.windows = []
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(layout_state, '_default_path', return_value=str(self.path)))
        self.stack.enter_context(patch.object(MainWindow, '_start_bridge_timer'))
        self.stack.enter_context(patch('paths.user_plugins_dir', return_value=str(self.root / 'users')))
        self.stack.enter_context(patch('paths.plugins_dir', return_value=str(self.root / 'plugins')))
        for dcc in ('maya', 'max'):
            for plugin_id, category in (('demo', ''), ('settings_demo', 'settings')):
                folder = self.root / 'plugins' / dcc / plugin_id
                folder.mkdir(parents=True)
                (folder / 'manifest.json').write_text(json.dumps(dict(id=plugin_id, dcc=dcc,
                    name=plugin_id, category=category, widget_factory='widget.create_widget')))
                (folder / 'widget.py').write_text('from PySide6 import QtWidgets\n'
                    'def create_widget(client, log, parent=None):\n'
                    '    return QtWidgets.QLabel("Test card", parent)\n')

    def tearDown(self):
        for window in self.windows:
            if not window._closing:
                window.close()
            window.deleteLater()
        self.app.processEvents()
        self.stack.close()
        self.temp.cleanup()

    def window(self):
        window = MainWindow()
        self.windows.append(window)
        window.show()
        QtTest.QTest.qWait(100)
        self.assertFalse(window._restoring)
        return window

    @staticmethod
    def center(canvas):
        # Independent geometric check; a viewport center, not sceneRect/items.
        return canvas.mapToScene(canvas.viewport().rect()).boundingRect().center()

    def assert_view(self, canvas, point, scale):
        actual = self.center(canvas)
        self.assertAlmostEqual(canvas.transform().m11(), scale, places=8)
        self.assertLessEqual(abs(actual.x() - point.x()), 1.1 / scale)
        self.assertLessEqual(abs(actual.y() - point.y()), 1.1 / scale)

    @staticmethod
    def category(tree, dcc, category):
        group = tree._groups[dcc]
        return next(group.child(i) for i in range(group.childCount())
                    if group.child(i).data(0, QtCore.Qt.UserRole) == ('category', dcc, category))

    def collapse_with_keyboard(self, tree, item):
        tree.setCurrentItem(item)
        tree.setFocus()
        QtTest.QTest.keyClick(tree, QtCore.Qt.Key_Left)
        self.assertFalse(item.isExpanded())

    def test_groups_and_panned_zoomed_view_survive_close_and_restart(self):
        first = self.window()
        first._activity.show_panel(0)
        self.app.processEvents()
        tree = first._side._tree
        self.collapse_with_keyboard(tree, self.category(tree, 'maya', 'settings'))
        self.collapse_with_keyboard(tree, self.category(tree, 'max', 'user'))
        self.collapse_with_keyboard(tree, tree._groups['max'])
        canvas = first._canvas
        canvas.open_plugin(first._plugins_by_key[('maya', 'demo')], -400, 230)
        canvas.open_plugin(first._plugins_by_key[('max', 'demo')], 3100, -800)
        canvas.scale(0.73, 0.73)
        canvas.centerOn(-3260.25, 2180.75)
        self.app.processEvents()
        origin = canvas.viewport().rect().center()
        QtTest.QTest.mousePress(canvas.viewport(), QtCore.Qt.MiddleButton, pos=origin)
        QtTest.QTest.mouseMove(canvas.viewport(), origin + QtCore.QPoint(73, -41))
        QtTest.QTest.mouseRelease(canvas.viewport(), QtCore.Qt.MiddleButton, pos=origin + QtCore.QPoint(73, -41))
        point = self.center(canvas)
        scale = canvas.transform().m11()
        positions = canvas.get_node_positions()
        first.close()
        persisted = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIn(['dcc', 'max'], persisted['collapsed_groups'])
        self.assertAlmostEqual(persisted['canvas_view']['center']['x'], point.x(), places=7)
        second = self.window()
        self.assertEqual(second._canvas.get_node_positions(), positions)
        self.assert_view(second._canvas, point, scale)
        tree = second._side._tree
        self.assertTrue(tree._groups['maya'].isExpanded())
        self.assertFalse(tree._groups['max'].isExpanded())
        self.assertFalse(self.category(tree, 'maya', 'settings').isExpanded())
        self.assertFalse(self.category(tree, 'max', 'user').isExpanded())
        self.assertTrue(self.category(tree, 'maya', 'user').isExpanded())
        self.assertTrue(second._side.isVisible())
        second._activity.show_panel(0)  # Close, then reopen the restored sidebar.
        self.app.processEvents()
        self.assert_view(second._canvas, point, scale)
        second._activity.show_panel(0)
        self.app.processEvents()
        self.assert_view(second._canvas, point, scale)
        second._refresh_plugins()
        self.assertFalse(self.category(tree, 'maya', 'settings').isExpanded())
        self.assertFalse(tree._groups['max'].isExpanded())

    def test_empty_canvas_and_repeated_restarts_do_not_drift_to_scene_center(self):
        first = self.window()
        first._canvas.scale(1.33, 1.33)
        first._canvas.centerOn(2134.75, -1876.25)
        original = self.center(first._canvas)
        scale = first._canvas.transform().m11()
        first.close()
        for _ in range(3):
            window = self.window()
            self.assertEqual(window._canvas.open_ids(), [])
            self.assert_view(window._canvas, original, scale)
            window.close()

    def test_maximized_view_restores_after_window_geometry_is_ready(self):
        first = self.window()
        first.showMaximized()
        self.app.processEvents()
        first._canvas.scale(0.61, 0.61)
        first._canvas.centerOn(-1300, 1700)
        point = self.center(first._canvas)
        first.close()
        second = self.window()
        self.assertTrue(second.isMaximized())
        self.assert_view(second._canvas, point, 0.61)

    def test_new_fields_survive_other_settings_writes_and_legacy_defaults(self):
        state = dict(version=4, open_plugins=[], collapsed_groups=[['cat', 'maya', 'settings']],
                     sidebar=dict(visible=True, page=1),
                     canvas_view=dict(center=dict(x=123.5, y=-456.25), scale=0.8))
        layout_state.save_all(state)
        for write in (lambda: layout_state.save_log_limit(2345),
                      lambda: layout_state.save_log_height(150),
                      lambda: layout_state.save_window(dict(x=1, y=2, w=1100, h=700)),
                      lambda: layout_state.save_always_on_top(True),
                      lambda: layout_state.forget_plugin(('maya', 'missing')),
                      lambda: layout_state.save_open([], False)):
            write()
            current = layout_state.load_all()
            self.assertEqual(current['canvas_view'], state['canvas_view'])
            self.assertEqual(current['collapsed_groups'], state['collapsed_groups'])
            self.assertEqual(current['sidebar'], state['sidebar'])
        self.path.write_text('{"version":3,"open_plugins":[{"id":"demo","x":1,"y":2}]}')
        self.assertEqual(layout_state.load_open()[0], [(('maya', 'demo'), 1.0, 2.0)])
        self.assertEqual(layout_state.load_collapsed_groups(), [])
        self.assertIsNone(layout_state.load_canvas_view())
        self.assertEqual(layout_state.load_sidebar(), dict(visible=False, page=0))

    def test_sidebar_pages_visibility_and_view_survive_restart(self):
        with patch.object(MainWindow, '_on_settings_opened') as settings_opened:
            for page, visible in ((0, True), (1, True), (1, False), (0, False)):
                with self.subTest(page=page, visible=visible):
                    first = self.window()
                    first._activity.set_panel(-1)
                    button = (first._activity._btn_resource if page == 0
                              else first._activity._btn_settings)
                    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                    if not visible:
                        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                    self.app.processEvents()
                    first._canvas.scale(0.83, 0.83)
                    first._canvas.centerOn(2300, -1900)
                    point = self.center(first._canvas)
                    scale = first._canvas.transform().m11()
                    first.close()
                    self.assertEqual(layout_state.load_sidebar(), dict(visible=visible, page=page))
                    settings_opened.reset_mock()
                    second = self.window()
                    settings_opened.assert_not_called()
                    self.assertEqual(second._side.isVisible(), visible)
                    self.assertEqual(second._side.current_index(), page)
                    self.assertEqual(second._activity._current, page if visible else -1)
                    self.assertEqual(second._activity._btn_resource.isChecked(), visible and page == 0)
                    self.assertEqual(second._activity._btn_settings.isChecked(), visible and page == 1)
                    self.assert_view(second._canvas, point, scale)
                    second.close()

    def test_focus_mode_preserves_prior_sidebar_on_restart(self):
        for page, visible in ((0, True), (1, True), (1, False)):
            with self.subTest(page=page, visible=visible):
                layout_state.save_all(dict(version=4, sidebar=dict(visible=visible, page=page)))
                first = self.window()
                first.toggle_focus_mode()
                self.app.processEvents()
                self.assertTrue(first._side.isHidden())
                first._canvas.centerOn(-1500, 2700)
                point = self.center(first._canvas)
                first.close()
                self.assertEqual(layout_state.load_sidebar(), dict(visible=visible, page=page))
                second = self.window()
                self.assertFalse(second._focus_mode)
                self.assertEqual(second._side.isVisible(), visible)
                self.assertEqual(second._side.current_index(), page)
                self.assert_view(second._canvas, point, 1.0)
                second.close()

    def test_panel_restore_is_idempotent_and_click_still_toggles(self):
        window = self.window()
        bar = window._activity
        opened = QtTest.QSignalSpy(bar.settings_clicked)
        bar.set_panel(1)
        bar.set_panel(1)
        self.assertEqual(opened.count(), 0)
        self.assertTrue(window._side.isVisible())
        QtTest.QTest.mouseClick(bar._btn_settings, QtCore.Qt.LeftButton)
        self.assertTrue(window._side.isHidden())
        self.assertFalse(bar._btn_settings.isChecked())
        QtTest.QTest.mouseClick(bar._btn_settings, QtCore.Qt.LeftButton)
        self.assertEqual(opened.count(), 1)
        self.assertTrue(window._side.isVisible())

    def test_invalid_sidebar_values_use_safe_defaults(self):
        for raw, expected in ((None, dict(visible=False, page=0)),
                              ([], dict(visible=False, page=0)),
                              (dict(visible='false', page=True), dict(visible=False, page=0)),
                              (dict(visible=True, page=9), dict(visible=True, page=0))):
            layout_state.save_all(dict(version=4, sidebar=raw, log_max_lines=2345))
            self.assertEqual(layout_state.load_sidebar(), expected)
            self.assertEqual(layout_state.load_log_limit(), 2345)

    def test_invalid_values_fall_back_without_breaking_other_state(self):
        invalid = [None, [], {}, dict(center={}, scale=1),
                   dict(center=dict(x=float('nan'), y=0), scale=1),
                   dict(center=dict(x=0, y=0), scale=0),
                   dict(center=dict(x=0, y=0), scale=True)]
        for view in invalid:
            layout_state.save_all(dict(version=4, log_max_lines=4321, canvas_view=view,
                collapsed_groups=[['dcc', 'max'], ['dcc', 'unknown'], ['cat', 'maya', 'user'],
                                  ['cat', 'max', {}], 'bad', None]))
            state = layout_state.load_all()
            self.assertIsNone(state['canvas_view'])
            self.assertEqual(state['log_max_lines'], 4321)
            self.assertEqual(state['collapsed_groups'], [['cat', 'maya', 'user'], ['dcc', 'max']])


if __name__ == '__main__':
    unittest.main()
