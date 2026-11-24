"""Real Qt event paths for the Max vertex color card."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from ui.app_style import apply_application_style
from ui.plugin_loader import scan
from ui.plugin_node import PluginNode


class Client:
    def __init__(self):
        self.calls = []
        self.response = dict(ok=True, data=dict(count=2, vertices=1, object="Test", mask=[True]*3))
    def call(self, name, **params):
        self.calls.append((name, params))
        if (name == 'vertex_color_display' and self.response.get('ok')
                and not self.response.get('data', {}).get('warning')):
            names = {0:'Color',-2:'Alpha',-1:'Illumination',17:'M17',18:'M18'}
            return dict(ok=True,data=dict(count=1,object='Test',channel=params['channel'],
                                         channel_name=names[params['channel']],display_only=True))
        return self.response


class Log:
    def __init__(self): self.messages = []
    def info(self, message): self.messages.append(("info", message))
    def warn(self, message): self.messages.append(("warn", message))
    def error(self, message): self.messages.append(("error", message))


class VertexColorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_application_style(cls.app)
        cls.info = next(p for p in scan(str(ROOT / "plugins")) if p.key == ("max", "vertex_color"))
    def setUp(self):
        self.client, self.log = Client(), Log()
        self.widget = self.info.load_factory()(self.client, self.log)
        self.node = PluginNode(self.info.name, self.widget, dcc="max")
        self.node.show()
        self.app.processEvents()
    def tearDown(self):
        QtWidgets.QToolTip.hideText()
        self.node.close()
        self.node.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    def click(self, button):
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.app.processEvents()
    def test_set_color_and_keyboard_slider_sync(self):
        spin = self.widget.spins[0]
        spin.setFocus()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, "42")
        QtTest.QTest.keyClick(spin, QtCore.Qt.Key_Enter)
        self.assertEqual(self.widget.sliders[0].value(), 42)
        slider = self.widget.sliders[2]
        slider.setFocus()
        QtTest.QTest.keyClick(slider, QtCore.Qt.Key_Home)
        self.assertEqual(self.widget.spins[2].value(), 0)
        self.click(self.widget.checks[1])
        self.assertFalse(self.widget.spins[1].isEnabled())
        self.assertFalse(self.widget.sliders[1].isEnabled())
        self.click(self.widget.set_button)
        self.assertEqual(self.client.calls[-1], ("vertex_color", dict(channel=0, rgb=[42,255,0], mask=[True,False,True])))

    def test_numeric_display_toggle_is_explicit_and_preserves_input_values(self):
        self.assertFalse(self.widget.numbers_check.isChecked())
        self.assertFalse(self.widget.numbers_timer.isActive())
        self.assertFalse(self.client.calls)
        self.widget.spins[0].setValue(42)
        self.client.response=dict(ok=True,data=dict(enabled=True,channel=0,channel_name='Color',limit=500))
        self.click(self.widget.numbers_check)
        owner=self.widget._numbers_session.owner
        self.assertEqual(self.client.calls[-1],('vertex_color_numbers',dict(enabled=True,channel=0,mask=[True]*3,owner=owner)))
        self.assertEqual(len(owner),32)
        self.assertTrue(self.widget.numbers_timer.isActive())
        self.assertEqual(self.widget.spins[0].value(),42)
        self.client.response=dict(ok=True,data=dict(enabled=False))
        self.click(self.widget.numbers_check)
        self.assertFalse(self.widget.numbers_timer.isActive())
        self.assertEqual(self.client.calls[-1][1]['enabled'],False)

    def test_numeric_display_tracks_channel_and_alpha_component_mask_without_enabling_rgb_editors(self):
        self.client.response=dict(ok=True,data=dict(enabled=True))
        self.click(self.widget.numbers_check)
        self.click(self.widget.channel_buttons['alpha'])
        self.assertEqual(self.client.calls[-1][1]['channel'],-2)
        self.assertTrue(all(check.isEnabled() for check in self.widget.checks))
        self.click(self.widget.checks[0])
        self.assertEqual(self.client.calls[-1][1]['mask'],[False,True,True])
        self.assertFalse(any(spin.isEnabled() for spin in self.widget.spins))
        self.assertFalse(any(slider.isEnabled() for slider in self.widget.sliders))
        self.assertTrue(self.widget.alpha_spin.isEnabled())
        self.click(self.widget.set_button)
        self.assertEqual(self.client.calls[-1],('vertex_color',dict(channel=-2,alpha=255)))
        self.client.response=dict(ok=True,data=dict(enabled=False))
        self.click(self.widget.numbers_check)
        self.assertFalse(any(check.isEnabled() for check in self.widget.checks))

    def test_numeric_display_failure_reload_disconnect_and_limit_warning(self):
        self.client.response=dict(ok=False,error='Max未连接')
        self.click(self.widget.numbers_check)
        self.assertFalse(self.widget.numbers_check.isChecked())
        self.assertIn('切换数值显示失败',self.log.messages[-1][1])
        self.client.response=dict(ok=True,data=dict(enabled=True))
        self.click(self.widget.numbers_check)
        self.client.response=dict(ok=True,data=dict(enabled=True,limited=True,limit=500))
        self.widget._poll_numbers();count=len(self.log.messages)
        self.widget._poll_numbers()
        self.assertEqual(len(self.log.messages),count)
        self.assertIn('超过 500',self.log.messages[-1][1])
        self.client.response=dict(ok=True,data=dict(enabled=False,error=''))
        self.widget._poll_numbers()
        self.assertFalse(self.widget.numbers_check.isChecked())
        self.assertFalse(self.widget.numbers_timer.isActive())
        self.client.response=dict(ok=True,data=dict(enabled=True))
        self.click(self.widget.numbers_check)
        self.client.response=dict(ok=False,error='连接中断')
        self.widget._poll_numbers()
        self.assertFalse(self.widget.numbers_check.isChecked())

    def test_numeric_display_stops_when_canvas_card_is_removed(self):
        from ui.canvas import Canvas
        client=Client();client.response=dict(ok=True,data=dict(enabled=True))
        canvas=Canvas(lambda plugin:plugin.load_factory()(client,Log()))
        canvas.show();canvas.open_plugin(self.info,0,0)
        widget=canvas._proxies[self.info.key].widget().content()
        self.click(widget.numbers_check)
        owner=widget._numbers_session.owner
        del widget
        canvas.close_plugin(self.info.key)
        self.app.sendPostedEvents(None,QtCore.QEvent.DeferredDelete);self.app.processEvents()
        self.assertEqual(client.calls[-1],('vertex_color_numbers',dict(enabled=False,channel=0,mask=[True]*3,owner=owner)))
        canvas.close();canvas.deleteLater()

    def test_copy_current_channel_and_status_preserve_previous_on_failure(self):
        self.assertEqual(self.widget.transfer_group.title(),'')
        self.assertIn('未复制',self.widget.clipboard_status.text())
        self.client.response=dict(ok=True,data=dict(count=8,source='Source',channel=17,channel_name='M17',skipped=1))
        self.click(self.widget.channel_buttons['map17'])
        self.click(self.widget.copy_button)
        self.assertEqual(self.client.calls[-1],('vertex_color_copy',dict(channel=17)))
        self.assertIn('Source · M17',self.widget.clipboard_status.text())
        self.assertIn('8',self.widget.clipboard_status.text())
        before=self.widget.clipboard_status.text()
        self.client.response=dict(ok=True,data=dict(warning='源通道没有颜色数据'))
        self.click(self.widget.copy_button)
        self.assertEqual(self.widget.clipboard_status.text(),before)
        self.client.response=dict(ok=False,error='Max 未连接')
        self.click(self.widget.copy_button)
        self.assertEqual(self.widget.clipboard_status.text(),before)
        self.assertIn('复制顶点色失败',self.log.messages[-1][1])

    def test_paste_reuses_current_channel_mask_and_alpha_without_changing_controls(self):
        for spin,value in zip(self.widget.spins,(17,42,89)):spin.setValue(value)
        self.widget.alpha_spin.setValue(65)
        self.widget.tolerance_spin.setValue(10)
        self.widget.scale_spin.setValue(.6)
        self.widget.gradient_value_spins['curve'].setValue(75)
        self.click(self.widget.checks[1])
        for key,ch in (('color',0),('illumination',-1),('map17',17),('map18',18),('alpha',-2)):
            self.click(self.widget.channel_buttons[key])
            self.client.response=dict(ok=True,data=dict(source='Source',object='Target',vertices=4,
                                      channel=ch,channel_name='Alpha' if ch==-2 else 'Color',mask=[True,False,True]))
            self.click(self.widget.paste_button)
            self.assertEqual(self.client.calls[-1],('vertex_color_paste',dict(channel=ch) if ch==-2 else dict(channel=ch,mask=[True,False,True])))
            self.assertEqual([s.value() for s in self.widget.spins],[17,42,89])
            self.assertEqual(self.widget.alpha_spin.value(),65)
            self.assertEqual(self.widget.tolerance_spin.value(),10)
            self.assertEqual(self.widget.scale_spin.value(),.6)
            self.assertEqual(self.widget.gradient_value_spins['curve'].value(),75)
            self.assertIn('Source → Target',self.log.messages[-1][1])
            self.assertIn('修改分量 A' if ch==-2 else '修改分量 R/B',self.log.messages[-1][1])

    def test_paste_warning_failure_and_repeated_requests_are_explicit(self):
        self.client.response=dict(ok=True,data=dict(warning='颜色剪贴板为空，请先复制顶点色'))
        self.click(self.widget.paste_button)
        self.assertEqual(self.log.messages[-1][0],'warn')
        self.client.response=dict(ok=False,error='写入失败，已恢复')
        self.click(self.widget.paste_button)
        self.assertIn('粘贴顶点色失败',self.log.messages[-1][1])
        self.click(self.widget.paste_button)
        self.assertEqual([name for name,params in self.client.calls],['vertex_color_paste']*3)

    def test_mirror_paste_uses_current_channel_mask_and_does_not_change_normal_paste(self):
        self.assertEqual(self.widget.mirror_paste_button.text(),'粘贴（镜像 X）')
        self.click(self.widget.checks[1])
        for key,ch in (('color',0),('illumination',-1),('map17',17),('map18',18),('alpha',-2)):
            self.click(self.widget.channel_buttons[key])
            self.client.response=dict(ok=True,data=dict(source='Source',object='Target',vertices=4,
                channel=ch,channel_name='Alpha' if ch==-2 else 'Color',source_channel_name='M18',
                mask=[True,False,True],mirror_x=True))
            status=self.widget.clipboard_status.text()
            self.click(self.widget.mirror_paste_button)
            expected=dict(channel=ch,mirror_x=True)
            if ch!=-2:expected['mask']=[True,False,True]
            self.assertEqual(self.client.calls[-1],('vertex_color_paste',expected))
            self.assertIn('世界 X=0 镜像最近点',self.log.messages[-1][1])
            self.assertEqual(self.widget.clipboard_status.text(),status)
            self.click(self.widget.paste_button)
            expected.pop('mirror_x')
            self.assertEqual(self.client.calls[-1],('vertex_color_paste',expected))
        self.client.response=dict(ok=True,data=dict(warning='颜色剪贴板为空，请先复制顶点色'))
        self.click(self.widget.mirror_paste_button)
        self.assertEqual(self.log.messages[-1][0],'warn')

    def test_paste_logs_source_channel_and_current_target_channel(self):
        self.click(self.widget.channel_buttons['alpha'])
        self.client.response=dict(ok=True,data=dict(source='Source',object='Target',vertices=4,
            channel=-2,channel_name='Alpha',source_channel=0,source_channel_name='Color',mask=[True]*3))
        self.click(self.widget.paste_button)
        self.assertEqual(self.client.calls[-1],('vertex_color_paste',dict(channel=-2)))
        self.assertIn('Color → Alpha',self.log.messages[-1][1])

    def test_id_paste_button_passes_destination_mask_and_logs_skipped_total(self):
        self.click(self.widget.channel_buttons['map17'])
        self.click(self.widget.checks[1])
        self.client.response = dict(ok=True, data=dict(
            object='Target', source='Source', channel=17, channel_name='M17',
            source_channel_name='Color', vertices=2, skipped=3,
            by_vertex_id=True, mask=[True, False, True]))
        self.click(self.widget.id_paste_button)
        self.assertEqual(self.client.calls[-1], ('vertex_color_paste',
                         dict(channel=17, by_vertex_id=True, mask=[True, False, True])))
        self.assertIn('已按顶点 ID 粘贴 2 个几何顶点，跳过 3 个顶点', self.log.messages[-1][1])

    def test_select_tolerance_defaults_zero_and_keyboard_sync_does_not_run(self):
        self.assertEqual(self.widget.select_group.title(),'')
        self.assertEqual((self.widget.tolerance_spin.minimum(),self.widget.tolerance_spin.maximum()),(0,255))
        self.assertEqual(self.widget.tolerance_spin.value(),0)
        self.assertEqual(self.widget.tolerance_slider.value(),0)
        spin=self.widget.tolerance_spin
        spin.setFocus(); spin.selectAll()
        QtTest.QTest.keyClicks(spin,'10')
        QtTest.QTest.keyClick(spin,QtCore.Qt.Key_Enter)
        self.assertEqual(self.widget.tolerance_slider.value(),10)
        self.widget.tolerance_slider.setFocus()
        QtTest.QTest.keyClick(self.widget.tolerance_slider,QtCore.Qt.Key_End)
        self.assertEqual(self.widget.tolerance_spin.value(),255)
        self.assertFalse(self.client.calls)

    def test_select_color_reuses_targets_mask_all_channels_and_preserves_controls(self):
        for spin,value in zip(self.widget.spins,(200,50,100)): spin.setValue(value)
        self.widget.alpha_spin.setValue(200)
        self.widget.tolerance_spin.setValue(10)
        self.click(self.widget.checks[1])
        self.widget.scale_spin.setValue(.75)
        self.widget.gradient_value_spins['curve'].setValue(75)
        for key,ch in (('color',0),('illumination',-1),('map17',17),('map18',18),('alpha',-2)):
            self.click(self.widget.channel_buttons[key])
            self.click(self.widget.select_button)
            expected=dict(channel=ch,tolerance=10)
            if ch==-2: expected['alpha']=200
            else: expected.update(rgb=[200,50,100],mask=[True,False,True])
            self.assertEqual(self.client.calls[-1],('vertex_color_select',expected))
            self.assertEqual([s.value() for s in self.widget.spins],[200,50,100])
            self.assertEqual(self.widget.alpha_spin.value(),200)
            self.assertEqual(self.widget.scale_spin.value(),.75)
            self.assertEqual(self.widget.gradient_value_spins['curve'].value(),75)
            self.assertEqual(self.widget.tolerance_spin.value(),10)

    def test_select_matching_zero_matching_warning_and_failure_log(self):
        self.client.response=dict(ok=True,data=dict(channel=0,channel_name='Color',object='Panel',
            rgb=[200,50,100],mask=[True,False,False],tolerance=10,searched=20,count=0))
        self.click(self.widget.select_button)
        self.assertIn('目标 R=200',self.log.messages[-1][1])
        self.assertNotIn('G=50',self.log.messages[-1][1])
        self.assertIn('容差 10',self.log.messages[-1][1])
        self.assertIn('搜索 20',self.log.messages[-1][1])
        self.assertIn('匹配 0',self.log.messages[-1][1])
        self.assertEqual(self.log.messages[-1][0],'info')
        self.client.response['data'].update(channel=-2,channel_name='Alpha',alpha=200,count=5)
        self.click(self.widget.select_button)
        self.assertIn('目标 A=200',self.log.messages[-1][1])
        self.client.response=dict(ok=True,data=dict(warning='当前子对象选区为空'))
        self.click(self.widget.select_button)
        self.assertEqual(self.log.messages[-1][0],'warn')
        self.client.response=dict(ok=False,error='未连接 Max')
        self.click(self.widget.select_button)
        self.assertIn('按颜色选择顶点失败',self.log.messages[-1][1])

    def test_select_tolerance_is_independent_of_presets_settings_and_channel_switch(self):
        self.widget.tolerance_spin.setValue(23)
        self.click(self.widget.preset_buttons[0])
        self.click(self.widget.set_button)
        self.click(self.widget.scale_button)
        self.click(self.widget.gradient_button)
        self.click(self.widget.channel_buttons['alpha'])
        self.assertEqual(self.widget.tolerance_spin.value(),23)
        self.assertTrue(self.widget.tolerance_spin.isEnabled())
        self.assertTrue(self.widget.select_button.isEnabled())
    def test_every_preset_sends_values_and_respects_mask(self):
        from plugins.max.vertex_color.widget import PRESETS
        self.click(self.widget.checks[2])
        original = [17,42,89]
        for spin, value in zip(self.widget.spins, original): spin.setValue(value)
        self.widget.alpha_spin.setValue(63)
        for key, channel in (("color", 0), ("illumination", -1), ("map17",17), ("map18",18)):
            self.click(self.widget.channel_buttons[key])
            for rgb, button in zip(PRESETS, self.widget.preset_buttons):
                self.click(button)
                self.assertEqual(self.client.calls[-1], ("vertex_color", dict(channel=channel, rgb=list(rgb), mask=[True,True,False])))
                self.assertEqual([s.value() for s in self.widget.spins], original)
                self.assertEqual([s.value() for s in self.widget.sliders], original)
                self.assertEqual(self.widget.alpha_spin.value(), 63)
        self.click(self.widget.set_button)
        self.assertEqual(self.client.calls[-1][1]['rgb'], original)
    def test_warning_and_failure_go_to_log(self):
        self.client.response = dict(ok=True, data=dict(count=0, warning="没有选区"))
        self.click(self.widget.set_button)
        self.assertEqual(self.log.messages[-1], ("warn", "[顶点颜色] 没有选区"))
        self.client.response = dict(ok=False, error="未连接 Max")
        self.click(self.widget.set_button)
        self.assertEqual(self.log.messages[-1][0], "error")
        self.assertIn("未连接 Max", self.log.messages[-1][1])
    def test_topology_buttons_follow_all_channels(self):
        self.assertEqual(self.widget.match_group.title(), "")
        self.assertTrue(self.widget.match_copy_button.isEnabled())
        self.assertFalse(self.widget.match_paste_button.isEnabled())
        for key, channel, name in (("alpha", -2, "Alpha"), ("illumination", -1, "Illumination"),
                                   ("map17", 17, "M17"), ("map18", 18, "M18"), ("color", 0, "Color")):
            with self.subTest(channel=name):
                self.click(self.widget.channel_buttons[key])
                self.assertTrue(self.widget.match_copy_button.isEnabled())
                self.assertFalse(self.widget.match_paste_button.isEnabled())
                self.client.response = dict(ok=True, data=dict(object="Source", channel=channel, channel_name=name, count=3))
                self.click(self.widget.match_copy_button)
                self.assertTrue(QtCore.QThreadPool.globalInstance().waitForDone(2000))
                self.app.processEvents()
                self.assertEqual(self.client.calls[-1][0], "vertex_color_match_snapshot")
                self.assertEqual(self.client.calls[-1][1]["channel"], channel)
                self.assertIn(name, self.widget.match_status.text())
                self.assertTrue(self.widget.match_paste_button.isEnabled())

    def test_manifest_factory_and_public_button_style(self):
        self.assertEqual(self.info.id, "vertex_color")
        self.assertEqual(self.info.dcc, "max")
        self.assertTrue(self.widget.property("qzPluginContent"))
        self.assertEqual(len(self.widget.preset_buttons), 20)
        self.assertEqual(self.widget.preset_group.title(), '')
        self.assertEqual(self.widget.channel_buttons['color'].text(), 'Color')
        self.assertEqual([self.widget.channel_buttons[k].text() for k in ('map17','map18')], ['M17','M18'])
        for button in self.widget.findChildren(QtWidgets.QPushButton):
            self.assertFalse(button.styleSheet())
            if button in self.widget.preset_buttons:
                self.assertFalse(button.text())
                self.assertEqual(button.toolTip(), "R:{} G:{} B:{}".format(*button.rgb))
                pixel = button.grab().toImage().pixelColor(button.rect().center())
                self.assertEqual(pixel.getRgb()[:3], button.rgb)
            elif button is self.widget.scale_button:
                self.assertEqual(button.toolTip(), "每次点击按当前颜色乘倍率，结果四舍五入为 0–255 整数。")
            else:
                self.assertFalse(button.toolTip())
        self.assertFalse(self.client.calls)

    def test_channel_switching_preserves_controls_and_only_changes_display(self):
        self.widget.spins[0].setValue(37)
        self.widget.alpha_spin.setValue(90)
        self.click(self.widget.checks[1])
        self.click(self.widget.channel_buttons['alpha'])
        self.assertTrue(self.widget.alpha_spin.isEnabled())
        self.assertFalse(any(c.isEnabled() for c in self.widget.checks))
        self.assertFalse(any(s.isEnabled() for s in self.widget.spins))
        for button in self.widget.preset_buttons:
            self.assertFalse(button.isEnabled())
        self.click(self.widget.preset_buttons[0])
        self.click(self.widget.channel_buttons['illumination'])
        self.assertFalse(self.widget.alpha_spin.isEnabled())
        self.assertTrue(self.widget.spins[0].isEnabled())
        self.assertFalse(self.widget.spins[1].isEnabled())
        self.assertEqual(self.widget.spins[0].value(), 37)
        self.assertEqual(self.widget.alpha_spin.value(), 90)
        self.assertEqual(self.client.calls, [('vertex_color_display',dict(channel=-2)),
                                             ('vertex_color_display',dict(channel=-1))])

    def test_alpha_uses_a_slider_without_rgb_parameters(self):
        self.click(self.widget.checks[0])
        self.click(self.widget.channel_buttons['alpha'])
        spin = self.widget.alpha_spin
        spin.setFocus()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, '64')
        QtTest.QTest.keyClick(spin, QtCore.Qt.Key_Enter)
        self.assertEqual(self.widget.alpha_slider.value(), 64)
        self.click(self.widget.set_button)
        self.assertEqual(self.client.calls[-1], ('vertex_color', dict(channel=-2, alpha=64)))
        self.client.response = dict(ok=True, data=dict(count=1, vertices=1, object='Test', channel=-2,
                                                      channel_name='Alpha', alpha=64))
        self.click(self.widget.set_button)
        self.assertIn('Alpha', self.log.messages[-1][1])
        self.assertIn('A=64', self.log.messages[-1][1])

    def test_requested_button_tooltips_use_real_help_events(self):
        button = self.widget.preset_buttons[0]
        point = button.rect().center()
        event = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, point, button.mapToGlobal(point))
        self.app.sendEvent(button, event)
        self.app.processEvents()
        self.assertTrue(QtWidgets.QToolTip.isVisible())
        self.assertEqual(QtWidgets.QToolTip.text(), 'R:144 G:238 B:144')
        self.assertEqual(self.widget.scale_group.title(), '')
        self.assertEqual([label.text() for label in self.widget.scale_group.findChildren(QtWidgets.QLabel)], ['倍率'])
        button = self.widget.scale_button
        point = button.rect().center()
        event = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, point, button.mapToGlobal(point))
        self.app.sendEvent(button, event)
        self.app.processEvents()
        self.assertTrue(QtWidgets.QToolTip.isVisible())
        self.assertEqual(QtWidgets.QToolTip.text(), '每次点击按当前颜色乘倍率，结果四舍五入为 0–255 整数。')

    def test_each_actual_channel_switch_sends_exactly_one_display_request(self):
        self.widget.spins[0].setValue(37)
        self.widget.alpha_spin.setValue(90)
        sequence = [('alpha',-2),('illumination',-1),('map17',17),('map18',18),('color',0)]
        for count, (key, channel) in enumerate(sequence,1):
            self.click(self.widget.channel_buttons[key])
            self.assertEqual(len(self.client.calls), count)
            self.assertEqual(self.client.calls[-1], ('vertex_color_display',dict(channel=channel)))
            self.click(self.widget.channel_buttons[key])
            self.assertEqual(len(self.client.calls), count)
            self.assertEqual(self.widget.spins[0].value(), 37)
            self.assertEqual(self.widget.alpha_spin.value(), 90)
        self.assertIn('Color 通道显示', self.log.messages[-1][1])

    def test_display_error_is_logged_without_color_write(self):
        self.client.response = dict(ok=False,error='Max 未连接')
        self.click(self.widget.channel_buttons['map18'])
        self.assertEqual(self.client.calls, [('vertex_color_display',dict(channel=18))])
        self.assertEqual(self.log.messages[-1][0], 'error')
        self.assertIn('切换通道显示失败', self.log.messages[-1][1])

    def test_scale_controls_sync_without_applying_until_clicked(self):
        self.assertEqual(self.widget.scale_spin.value(), 1.0)
        self.assertEqual(self.widget.scale_slider.value(), 100)
        spin = self.widget.scale_spin
        spin.setFocus()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, '0.57')
        QtTest.QTest.keyClick(spin, QtCore.Qt.Key_Enter)
        self.assertEqual(self.widget.scale_slider.value(), 57)
        slider = self.widget.scale_slider
        slider.setFocus()
        QtTest.QTest.keyClick(slider, QtCore.Qt.Key_Right)
        self.assertEqual(self.widget.scale_spin.value(), .58)
        QtTest.QTest.keyClick(slider, QtCore.Qt.Key_End)
        self.assertEqual(self.widget.scale_spin.value(), 10.0)
        QtTest.QTest.keyClick(slider, QtCore.Qt.Key_Home)
        self.assertEqual(self.widget.scale_spin.value(), 0.0)
        self.assertFalse(self.client.calls)
        self.click(self.widget.checks[1])
        self.click(self.widget.scale_button)
        self.assertEqual(self.client.calls, [('vertex_color_scale', dict(channel=0, factor=0., mask=[True, False, True]))])

    def test_scale_all_channels_share_factor_and_preserve_color_controls(self):
        for spin, value in zip(self.widget.spins, [17, 42, 89]): spin.setValue(value)
        self.widget.alpha_spin.setValue(63)
        self.widget.scale_spin.setValue(.5)
        self.click(self.widget.checks[2])
        for key, channel in (('color', 0), ('illumination', -1), ('map17', 17), ('map18', 18), ('alpha', -2)):
            self.click(self.widget.channel_buttons[key])
            previous = len(self.client.calls)
            self.assertTrue(self.widget.scale_button.isEnabled())
            self.click(self.widget.scale_button)
            self.assertEqual(len(self.client.calls), previous + 1)
            expected = dict(channel=channel, factor=.5)
            if channel != -2: expected['mask'] = [True, True, False]
            self.assertEqual(self.client.calls[-1], ('vertex_color_scale', expected))
            self.assertEqual([s.value() for s in self.widget.spins], [17, 42, 89])
            self.assertEqual(self.widget.alpha_spin.value(), 63)
            self.assertEqual(self.widget.scale_spin.value(), .5)

    def test_scale_and_presets_are_independent_and_repeat_only_on_click(self):
        self.widget.scale_spin.setValue(.5)
        self.click(self.widget.preset_buttons[0])
        self.assertEqual(self.widget.scale_spin.value(), .5)
        self.assertEqual(self.client.calls[-1][0], 'vertex_color')
        self.click(self.widget.scale_button)
        self.click(self.widget.scale_button)
        self.assertEqual([name for name, params in self.client.calls],
                         ['vertex_color', 'vertex_color_scale', 'vertex_color_scale'])
        self.assertEqual(self.client.calls[-1], self.client.calls[-2])

    def test_scale_success_warning_and_failure_are_logged(self):
        self.client.response = dict(ok=True, data=dict(count=2, vertices=1, object='Test',
                                    channel=18, channel_name='M18', factor=.5, mask=[True, False, True]))
        self.click(self.widget.scale_button)
        self.assertIn('M18：已缩放', self.log.messages[-1][1])
        self.assertIn('×0.50', self.log.messages[-1][1])
        self.assertIn('R/B', self.log.messages[-1][1])
        self.client.response['data'].update(channel=-2, channel_name='Alpha')
        self.click(self.widget.scale_button)
        self.assertIn('修改分量 A', self.log.messages[-1][1])
        self.client.response = dict(ok=True, data=dict(count=0, warning='倍率为 1.00，未修改颜色'))
        self.click(self.widget.scale_button)
        self.assertEqual(self.log.messages[-1], ('warn', '[顶点颜色] 倍率为 1.00，未修改颜色'))
        self.client.response = dict(ok=False, error='Max 未连接')
        self.click(self.widget.scale_button)
        self.assertIn('缩放颜色失败：Max 未连接', self.log.messages[-1][1])

    def choose_gradient_direction(self, direction):
        channel = self.widget.channel_group.checkedButton()
        self.click(self.widget.gradient_direction_buttons[direction])
        self.assertTrue(self.widget.gradient_direction_buttons[direction].isChecked())
        self.assertEqual(sum(b.isChecked() for b in self.widget.gradient_direction_buttons.values()), 1)
        self.assertIs(self.widget.channel_group.checkedButton(), channel)

    def test_gradient_defaults_remove_picker_reverse_and_extra_component_controls(self):
        self.assertEqual(self.widget.gradient_group.title(), '')
        self.assertTrue(self.widget.gradient_direction_buttons['-Z'].isChecked())
        self.assertEqual(list(self.widget.gradient_direction_buttons),
                         ['X','Y','Z','-X','-Y','-Z','custom','topology'])
        self.assertEqual({key:s.value() for key,s in self.widget.gradient_value_spins.items()},
                         dict(minimum=0,maximum=255,curve=50))
        self.assertFalse(self.widget.gradient_custom.isVisible())
        self.assertEqual(len(self.widget.gradient_group.findChildren(QtWidgets.QRadioButton)), 8)
        self.assertFalse(self.widget.gradient_group.findChildren(QtWidgets.QComboBox))
        self.assertFalse(self.widget.gradient_group.findChildren(QtWidgets.QCheckBox))
        self.assertFalse(self.widget.findChildren(QtWidgets.QDialog))
        self.assertFalse(any('选择颜色' in b.text() for b in self.widget.findChildren(QtWidgets.QPushButton)))
        self.assertFalse(self.client.calls)

    def test_gradient_range_curve_sync_and_all_color_channels_use_shared_mask(self):
        self.widget.spins[0].setValue(37)
        self.widget.scale_spin.setValue(.57)
        spin = self.widget.gradient_value_spins['minimum']
        spin.setFocus(); spin.selectAll()
        QtTest.QTest.keyClicks(spin,'32')
        QtTest.QTest.keyClick(spin,QtCore.Qt.Key_Enter)
        self.assertEqual(self.widget.gradient_sliders['minimum'].value(),32)
        slider = self.widget.gradient_sliders['curve']
        slider.setFocus()
        QtTest.QTest.keyClick(slider,QtCore.Qt.Key_Home)
        self.assertEqual(self.widget.gradient_value_spins['curve'].value(),25)
        self.click(self.widget.checks[1])
        for direction in ('X','Y','Z','-X','-Y','-Z'):
            self.choose_gradient_direction(direction)
        self.assertFalse(self.client.calls)
        for key,channel in (('color',0),('illumination',-1),('map17',17),('map18',18)):
            self.click(self.widget.channel_buttons[key])
            self.click(self.widget.gradient_button)
            self.assertEqual(self.client.calls[-1],('vertex_color_gradient',dict(channel=channel,
                direction='-Z',minimum=32,maximum=255,curve=25,mask=[True,False,True])))
        self.assertEqual(self.widget.spins[0].value(),37)
        self.assertEqual(self.widget.scale_spin.value(),.57)

    def test_gradient_alpha_reuses_range_and_preserves_rgb_checks(self):
        self.click(self.widget.checks[0])
        self.click(self.widget.checks[1])
        self.widget.alpha_spin.setValue(63)
        self.widget.gradient_value_spins['minimum'].setValue(64)
        self.widget.gradient_value_spins['curve'].setValue(75)
        self.click(self.widget.channel_buttons['alpha'])
        self.click(self.widget.gradient_button)
        self.assertEqual(self.client.calls[-1],('vertex_color_gradient',dict(channel=-2,
            direction='-Z',minimum=64,maximum=255,curve=75)))
        self.assertFalse(any(check.isEnabled() for check in self.widget.checks))
        self.assertEqual(self.widget.alpha_spin.value(),63)
        self.click(self.widget.channel_buttons['color'])
        self.assertEqual([c.isChecked() for c in self.widget.checks],[False,False,True])
        self.assertEqual(self.widget.gradient_value_spins['minimum'].value(),64)
        self.assertEqual(self.widget.gradient_value_spins['curve'].value(),75)

    def test_gradient_custom_capture_only_reads_points_and_preserves_precision(self):
        self.choose_gradient_direction('custom')
        self.assertTrue(self.widget.gradient_custom.isVisible())
        self.assertIn('未设置',self.widget.gradient_point_status.text())
        points = [[.123456789,-2.,30.],[10.,20.,0.]]
        for index,point in enumerate(points):
            self.client.response = dict(ok=True,data=dict(point=point,vertices=2,object='Test'))
            self.click(self.widget.gradient_point_buttons[index])
            self.assertEqual(self.client.calls[-1],('vertex_color_gradient_point',{}))
        self.assertEqual(self.widget.gradient_points,points)
        self.assertEqual([name for name,params in self.client.calls],['vertex_color_gradient_point']*2)
        self.assertIn('0.123',self.widget.gradient_point_status.text())
        self.client.response = dict(ok=True,data=dict(count=2,vertices=2,channel=0,channel_name='Color',mask=[True]*3,direction='custom'))
        self.click(self.widget.gradient_button)
        self.assertEqual(self.client.calls[-1],('vertex_color_gradient',dict(channel=0,direction='custom',
            minimum=0,maximum=255,curve=50,mask=[True]*3,start=points[0],end=points[1])))
        self.choose_gradient_direction('-Z')
        self.assertFalse(self.widget.gradient_custom.isVisible())
        self.click(self.widget.gradient_button)
        self.assertNotIn('start',self.client.calls[-1][1])
        self.choose_gradient_direction('custom')
        self.assertEqual(self.widget.gradient_points,points)

    def test_gradient_capture_missing_or_failed_keeps_previous_points_and_logs(self):
        self.choose_gradient_direction('custom')
        self.client.response = dict(ok=True,data=dict(count=0,warning='请先设置自定义渐变的起点和终点'))
        self.click(self.widget.gradient_button)
        self.assertEqual(self.client.calls[-1][1]['start'],None)
        self.assertEqual(self.log.messages[-1][0],'warn')
        self.client.response = dict(ok=True,data=dict(point=[1.,2.,3.],vertices=1,object='Test'))
        self.click(self.widget.gradient_point_buttons[0])
        status = self.widget.gradient_point_status.text()
        self.client.response = dict(ok=True,data=dict(count=0,warning='当前子对象选区为空'))
        self.click(self.widget.gradient_point_buttons[0])
        self.assertEqual(self.widget.gradient_point_status.text(),status)
        self.client.response = dict(ok=False,error='Max 未连接')
        self.click(self.widget.gradient_point_buttons[1])
        self.assertEqual(self.widget.gradient_points,[[1.,2.,3.],None])
        self.assertIn('读取选区中心失败',self.log.messages[-1][1])

    def test_gradient_settings_do_not_change_when_presets_setting_or_scaling_apply(self):
        self.widget.gradient_value_spins['minimum'].setValue(17)
        self.widget.gradient_value_spins['curve'].setValue(75)
        self.widget.spins[0].setValue(37)
        self.widget.scale_spin.setValue(.57)
        for button in (self.widget.preset_buttons[0],self.widget.set_button,self.widget.scale_button):
            self.click(button)
            self.assertEqual(self.widget.gradient_value_spins['minimum'].value(),17)
            self.assertEqual(self.widget.gradient_value_spins['curve'].value(),75)
        self.click(self.widget.gradient_button)
        self.assertEqual(self.widget.spins[0].value(),37)
        self.assertEqual(self.widget.scale_spin.value(),.57)

    def test_gradient_success_warning_and_failure_use_middle_log(self):
        self.client.response = dict(ok=True,data=dict(count=3,vertices=3,object='Test',channel=17,
            channel_name='M17',direction='-Y',minimum=32,maximum=224,curve=75,mask=[True,False,True]))
        self.click(self.widget.gradient_button)
        self.assertIn('M17：已应用世界 -Y渐变',self.log.messages[-1][1])
        self.assertIn('R/B',self.log.messages[-1][1])
        self.assertIn('范围 32→224，曲线 75',self.log.messages[-1][1])
        self.client.response['data'].update(channel=-2,channel_name='Alpha',direction='custom')
        self.click(self.widget.gradient_button)
        self.assertIn('自定义两点',self.log.messages[-1][1])
        self.assertIn('修改分量 A',self.log.messages[-1][1])
        self.client.response = dict(ok=True,data=dict(count=0,warning='选区在世界 -Z 方向上没有跨度'))
        self.click(self.widget.gradient_button)
        self.assertEqual(self.log.messages[-1][0],'warn')
        self.client.response = dict(ok=False,error='Max 未连接')
        self.click(self.widget.gradient_button)
        self.assertIn('应用渐变失败：Max 未连接',self.log.messages[-1][1])

    def test_topology_option_to_right_of_custom_and_exclusive_visibility(self):
        buttons=self.widget.gradient_direction_buttons
        custom=buttons['custom'].mapTo(self.widget,QtCore.QPoint(0,0))
        topology=buttons['topology'].mapTo(self.widget,QtCore.QPoint(0,0))
        self.assertEqual(custom.y(),topology.y())
        self.assertGreater(topology.x(),custom.x())
        self.choose_gradient_direction('topology')
        self.assertTrue(self.widget.gradient_topology.isVisible())
        self.assertFalse(self.widget.gradient_custom.isVisible())
        self.assertIn('两横线之间',self.widget.gradient_button.text())
        self.assertFalse(self.client.calls)
        self.choose_gradient_direction('custom')
        self.assertFalse(self.widget.gradient_topology.isVisible())
        self.assertTrue(self.widget.gradient_custom.isVisible())

    def test_topology_records_separate_endpoints_and_reuses_all_channel_controls(self):
        self.choose_gradient_direction('topology')
        self.widget.gradient_points=[[1.,2.,3.],[4.,5.,6.]]
        for i,token in enumerate(('first-token','last-token')):
            self.client.response=dict(ok=True,data=dict(record=token,edges=3,vertices=4,object='Hair'))
            self.click(self.widget.gradient_edge_buttons[i])
            self.assertEqual(self.client.calls[-1],('vertex_color_gradient_edges',{} if i==0 else {'reference':'first-token'}))
        self.assertEqual(self.widget.gradient_edge_records,['first-token','last-token'])
        self.assertIn('Hair（3 条边）',self.widget.gradient_edge_status.text())
        self.click(self.widget.checks[1])
        self.widget.gradient_value_spins['curve'].setValue(75)
        for key,ch in (('color',0),('illumination',-1),('map17',17),('map18',18),('alpha',-2)):
            self.click(self.widget.channel_buttons[key])
            self.client.response=dict(ok=True,data=dict(direction='topology',rows=7,channel=ch,mask=[True,False,True]))
            self.click(self.widget.gradient_button)
            expected=dict(direction='topology',start='first-token',end='last-token',channel=ch,
                          minimum=0,maximum=255,curve=75)
            if ch!=-2: expected['mask']=[True,False,True]
            self.assertEqual(self.client.calls[-1],('vertex_color_gradient',expected))
            self.assertIn('沿拓扑（7 排）',self.log.messages[-1][1])
        self.assertEqual(self.widget.gradient_points,[[1.,2.,3.],[4.,5.,6.]])
        self.choose_gradient_direction('-Z')
        self.assertEqual(self.widget.gradient_edge_records,['first-token','last-token'])
        self.assertIn('当前选区',self.widget.gradient_button.text())

    def test_topology_failed_capture_preserves_old_records_and_warns_on_apply(self):
        self.choose_gradient_direction('topology')
        self.client.response=dict(ok=True,data=dict(record='valid',object='Hair',edges=3,vertices=4))
        self.click(self.widget.gradient_edge_buttons[0])
        status=self.widget.gradient_edge_status.text()
        self.client.response=dict(ok=True,data=dict(warning='横线不连续'))
        self.click(self.widget.gradient_edge_buttons[0])
        self.assertEqual(self.widget.gradient_edge_status.text(),status)
        self.client.response=dict(ok=False,error='Max 未连接')
        self.click(self.widget.gradient_edge_buttons[1])
        self.assertEqual(self.widget.gradient_edge_records,['valid',None])
        self.assertIn('记录横线失败',self.log.messages[-1][1])
        self.client.response=dict(ok=True,data=dict(warning='请重新记录横线'))
        self.click(self.widget.gradient_button)
        self.assertEqual(self.client.calls[-1][1]['end'],None)
        self.assertEqual(self.log.messages[-1][0],'warn')

    def test_topology_record_references_other_endpoint_and_reports_auto_completion(self):
        self.choose_gradient_direction('topology')
        self.client.response=dict(ok=True,data=dict(record='start',object='Hair',edges=6,vertices=6,expanded=True))
        self.click(self.widget.gradient_edge_buttons[0])
        self.assertEqual(self.client.calls[-1],('vertex_color_gradient_edges',{}))
        self.assertIn('自动补全并记录',self.log.messages[-1][1])
        self.client.response=dict(ok=True,data=dict(record='end',object='Hair',edges=5,vertices=6,expanded=True))
        self.click(self.widget.gradient_edge_buttons[1])
        self.assertEqual(self.client.calls[-1],('vertex_color_gradient_edges',{'reference':'start'}))
        self.client.response=dict(ok=True,data=dict(record='start-new',object='Hair',edges=6,vertices=6))
        self.click(self.widget.gradient_edge_buttons[0])
        self.assertEqual(self.client.calls[-1],('vertex_color_gradient_edges',{'reference':'end'}))
        self.assertEqual(self.widget.gradient_edge_records,['start-new','end'])


if __name__ == "__main__": unittest.main(verbosity=2)
