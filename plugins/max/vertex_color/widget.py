"""Max vertex channels, color scaling and curved world/topology gradients."""
import uuid
from PySide6 import QtCore, QtGui, QtWidgets
from ui.plugin_widget import BasePluginWidget, set_role
from ui.vertex_color_match import run_match_operation


PRESETS = (
    (144, 238, 144), (248, 222, 126), (255, 181, 197), (139, 231, 231),
    (104, 146, 104), (179, 140, 88), (249, 132, 229), (162, 139, 231),
    (0, 136, 86), (204, 119, 34), (244, 112, 103), (81, 137, 231),
    (0, 255, 0), (255, 255, 0), (255, 0, 255), (0, 255, 255),
    (255, 0, 0), (255, 255, 255), (128, 128, 128), (0, 0, 0),
)


class _NumbersSession:
    """Outlive the QWidget just long enough to stop its owned Max callback."""
    def __init__(self, client, log):
        self.client, self.log = client, log
        self.owner = uuid.uuid4().hex
        self.enabled = False
        self.app = QtWidgets.QApplication.instance()
        self.app.aboutToQuit.connect(self.stop)

    def stop(self, *_):
        if not self.enabled:
            return
        self.enabled = False
        try:
            result = self.client.call('vertex_color_numbers', enabled=False, channel=0,
                                      mask=[True]*3, owner=self.owner)
            if not result.get('ok'):
                self.log.warn('[顶点颜色] 关闭数值显示未完成，Max 将在连接超时后自动清理。')
        except Exception:
            self.log.warn('[顶点颜色] 连接已断开，Max 将在连接超时后自动清理数值显示。')

    def dispose(self, *_):
        self.stop()
        self.app.aboutToQuit.disconnect(self.stop)


class ColorPresetButton(QtWidgets.QPushButton):
    """A plain color swatch inside the shared button border and focus style."""
    def __init__(self, rgb, parent=None):
        super().__init__(parent)
        self.rgb = tuple(rgb)
        self.setProperty("qzRole", "swatch")
        self.setToolTip("R:{} G:{} B:{}".format(*rgb))
        self.setAccessibleName("应用预设 R{} G{} B{}".format(*rgb))

    def sizeHint(self):
        return QtCore.QSize(64, self.fontMetrics().height() + 12)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QtCore.QEvent.Polish, QtCore.QEvent.StyleChange, QtCore.QEvent.FontChange):
            # The shared swatch QSS allows a zero minimum; retain a clickable
            # font-sized swatch when the growing card is laid out in the canvas.
            self.setMinimumHeight(self.sizeHint().height())
        return result

    def paintEvent(self, event):
        super().paintEvent(event)
        color = QtGui.QColor(*self.rgb)
        if not self.isEnabled():
            color.setAlphaF(0.35)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(3, 3, -3, -3), 2, 2)


class _MatchSignals(QtCore.QObject):
    finished = QtCore.Signal(str, object)


class _MatchTask(QtCore.QRunnable):
    def __init__(self, client, operation, clipboard, mask, channel):
        super().__init__()
        self.client, self.operation, self.clipboard = client, operation, clipboard
        self.mask, self.channel = mask, channel
        self.signals = _MatchSignals()

    def run(self):
        result = run_match_operation(self.client, self.operation, self.clipboard, self.mask, self.channel)
        self.signals.finished.emit(self.operation, result)


class VertexColorWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "顶点颜色"
        self._match_clipboard = None
        self._match_task = None
        self._height_timer = QtCore.QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(0)
        self._height_timer.timeout.connect(self._fit_card_height)
        self._numbers_session = _NumbersSession(client, log)
        self.destroyed.connect(self._numbers_session.dispose)
        self.numbers_timer = QtCore.QTimer(self)
        self.numbers_timer.setInterval(2000)
        self.numbers_timer.timeout.connect(self._poll_numbers)
        self._numbers_limited = False
        self.checks, self.sliders, self.spins = [], [], []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setAlignment(QtCore.Qt.AlignTop)
        channel_row = QtWidgets.QHBoxLayout()
        self.channel_group = QtWidgets.QButtonGroup(self)
        self.channel_buttons = {}
        for key, text, channel in (("color", "Color", 0), ("alpha", "Alpha", -2),
                                   ("illumination", "Illumination", -1),
                                   ("map17", "M17", 17), ("map18", "M18", 18)):
            button = QtWidgets.QRadioButton(text)
            button.setProperty("channel", channel)
            self.channel_group.addButton(button)
            self.channel_buttons[key] = button
            channel_row.addWidget(button)
        self.channel_buttons["color"].setChecked(True)
        channel_row.addStretch()
        layout.addLayout(channel_row)
        hint = QtWidgets.QLabel("Editable Poly 当前子对象选区")
        set_role(hint, "hint")
        hint_row = QtWidgets.QHBoxLayout()
        hint_row.addWidget(hint)
        hint_row.addStretch()
        self.numbers_check = QtWidgets.QCheckBox("显示数值")
        self.numbers_check.toggled.connect(self._toggle_numbers)
        hint_row.addWidget(self.numbers_check)
        layout.addLayout(hint_row)
        grid = QtWidgets.QGridLayout()
        for row, channel in enumerate("RGB"):
            check = QtWidgets.QCheckBox(channel)
            check.setChecked(True)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 255)
            slider.setValue(255)
            slider.setAccessibleName(f"{channel} 分量滑块")
            spin = QtWidgets.QSpinBox()
            spin.setRange(0, 255)
            spin.setValue(255)
            spin.setAccessibleName(f"{channel} 分量数值")
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            check.toggled.connect(self._component_toggled)
            grid.addWidget(check, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(spin, row, 2)
            self.checks.append(check)
            self.sliders.append(slider)
            self.spins.append(spin)
        grid.setColumnStretch(1, 1)
        self.alpha_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.alpha_spin = QtWidgets.QSpinBox()
        for control in (self.alpha_slider, self.alpha_spin):
            control.setRange(0, 255)
            control.setValue(255)
            control.setAccessibleName("Alpha A 分量")
        self.alpha_slider.valueChanged.connect(self.alpha_spin.setValue)
        self.alpha_spin.valueChanged.connect(self.alpha_slider.setValue)
        self.alpha_label = QtWidgets.QLabel("A")
        grid.addWidget(self.alpha_label, 3, 0)
        grid.addWidget(self.alpha_slider, 3, 1)
        grid.addWidget(self.alpha_spin, 3, 2)
        layout.addLayout(grid)
        self.set_button = QtWidgets.QPushButton("设置颜色（当前选区）")
        self.set_button.clicked.connect(self._apply)
        layout.addWidget(self.set_button)
        self.select_group = QtWidgets.QGroupBox("")
        select_layout = QtWidgets.QGridLayout(self.select_group)
        select_layout.setContentsMargins(10, 10, 10, 10)
        select_layout.addWidget(QtWidgets.QLabel("容差"), 0, 0)
        self.tolerance_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.tolerance_spin = QtWidgets.QSpinBox()
        for control in (self.tolerance_slider, self.tolerance_spin):
            control.setRange(0, 255)
            control.setValue(0)
        self.tolerance_slider.setAccessibleName("按颜色选择容差滑块")
        self.tolerance_spin.setAccessibleName("按颜色选择容差数值")
        self.tolerance_slider.valueChanged.connect(self.tolerance_spin.setValue)
        self.tolerance_spin.valueChanged.connect(self.tolerance_slider.setValue)
        select_layout.addWidget(self.tolerance_slider, 0, 1)
        select_layout.addWidget(self.tolerance_spin, 0, 2)
        select_layout.setColumnStretch(1, 1)
        self.select_button = QtWidgets.QPushButton("按颜色选择顶点（当前选区）")
        self.select_button.clicked.connect(self._select_by_color)
        select_layout.addWidget(self.select_button, 1, 0, 1, 3)
        layout.addWidget(self.select_group)
        self.preset_group = QtWidgets.QGroupBox("")
        preset_layout = QtWidgets.QGridLayout(self.preset_group)
        preset_layout.setContentsMargins(10, 10, 10, 10)
        self.preset_buttons = []
        for index, rgb in enumerate(PRESETS):
            button = ColorPresetButton(rgb)
            button.clicked.connect(lambda checked=False, color=rgb: self._preset(color))
            preset_layout.addWidget(button, index // 4, index % 4)
            self.preset_buttons.append(button)
        layout.addWidget(self.preset_group)
        self.scale_group = QtWidgets.QGroupBox("")
        scale_layout = QtWidgets.QGridLayout(self.scale_group)
        scale_layout.setContentsMargins(10, 10, 10, 10)
        scale_layout.addWidget(QtWidgets.QLabel("倍率"), 0, 0)
        self.scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale_slider.setRange(0, 1000)
        self.scale_slider.setValue(100)
        self.scale_slider.setAccessibleName("颜色缩放倍率滑块")
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setDecimals(2)
        self.scale_spin.setRange(0.0, 10.0)
        self.scale_spin.setSingleStep(0.01)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setAccessibleName("颜色缩放倍率数值")
        self.scale_slider.valueChanged.connect(self._scale_slider_changed)
        self.scale_spin.valueChanged.connect(self._scale_spin_changed)
        scale_layout.addWidget(self.scale_slider, 0, 1)
        scale_layout.addWidget(self.scale_spin, 0, 2)
        scale_layout.setColumnStretch(1, 1)
        self.scale_button = QtWidgets.QPushButton("应用缩放（当前选区）")
        self.scale_button.setToolTip("每次点击按当前颜色乘倍率，结果四舍五入为 0–255 整数。")
        self.scale_button.clicked.connect(self._scale)
        scale_layout.addWidget(self.scale_button, 1, 0, 1, 3)
        layout.addWidget(self.scale_group)
        self.gradient_group = QtWidgets.QGroupBox("")
        gradient_layout = QtWidgets.QGridLayout(self.gradient_group)
        gradient_layout.setContentsMargins(10, 10, 10, 10)
        direction_grid = QtWidgets.QGridLayout()
        direction_grid.addWidget(QtWidgets.QLabel("方向"), 0, 0)
        self.gradient_direction_group = QtWidgets.QButtonGroup(self)
        self.gradient_direction_group.setExclusive(True)
        self.gradient_direction_buttons = {}
        for index, direction in enumerate(("X", "Y", "Z", "-X", "-Y", "-Z", "custom", "topology")):
            text = {"custom": "自定义（两点）", "topology": "沿拓扑"}.get(direction, direction)
            button = QtWidgets.QRadioButton(text)
            button.setProperty("direction", direction)
            button.setAccessibleName(f"渐变方向 {text}")
            self.gradient_direction_group.addButton(button)
            self.gradient_direction_buttons[direction] = button
            if direction == "custom":
                direction_grid.addWidget(button, 1, 1, 1, 3)
            elif direction == "topology":
                direction_grid.addWidget(button, 1, 4, 1, 3)
            else:
                direction_grid.addWidget(button, 0, index + 1)
        self.gradient_direction_buttons["-Z"].setChecked(True)
        gradient_layout.addLayout(direction_grid, 0, 0, 1, 3)
        self.gradient_points = [None, None]
        self.gradient_custom = QtWidgets.QWidget()
        custom_layout = QtWidgets.QVBoxLayout(self.gradient_custom)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        point_row = QtWidgets.QHBoxLayout()
        self.gradient_point_buttons = []
        for endpoint, name in enumerate(("起点", "终点")):
            button = QtWidgets.QPushButton(f"设置{name}（选区中心）")
            button.clicked.connect(lambda checked=False, index=endpoint: self._capture_gradient_point(index))
            point_row.addWidget(button)
            self.gradient_point_buttons.append(button)
        custom_layout.addLayout(point_row)
        self.gradient_point_status = QtWidgets.QLabel()
        self.gradient_point_status.setWordWrap(True)
        set_role(self.gradient_point_status, "hint")
        custom_layout.addWidget(self.gradient_point_status)
        anchor_layout = QtWidgets.QVBoxLayout()
        anchor_layout.setContentsMargins(0, 0, 0, 0)
        anchor_layout.addWidget(self.gradient_custom)
        self.gradient_edge_records = [None, None]
        self.gradient_edge_labels = [None, None]
        self.gradient_topology = QtWidgets.QWidget()
        topology_layout = QtWidgets.QVBoxLayout(self.gradient_topology)
        topology_layout.setContentsMargins(0, 0, 0, 0)
        edge_row = QtWidgets.QHBoxLayout()
        self.gradient_edge_buttons = []
        for endpoint, name in enumerate(("起始", "结束")):
            button = QtWidgets.QPushButton(f"记录{name}横线")
            button.clicked.connect(lambda checked=False, index=endpoint: self._capture_gradient_edges(index))
            edge_row.addWidget(button)
            self.gradient_edge_buttons.append(button)
        topology_layout.addLayout(edge_row)
        self.gradient_edge_status = QtWidgets.QLabel()
        self.gradient_edge_status.setWordWrap(True)
        set_role(self.gradient_edge_status, "hint")
        topology_layout.addWidget(self.gradient_edge_status)
        anchor_layout.addWidget(self.gradient_topology)
        gradient_layout.addLayout(anchor_layout, 1, 0, 1, 3)
        self.gradient_sliders, self.gradient_value_spins = {}, {}
        for row, (key, label, lower, upper, default) in enumerate((
                ("minimum", "最小值", 0, 255, 0), ("maximum", "最大值", 0, 255, 255),
                ("curve", "曲线", 25, 75, 50)), 2):
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(lower, upper)
            slider.setValue(default)
            slider.setAccessibleName(f"渐变{label}滑块")
            spin = QtWidgets.QSpinBox()
            spin.setRange(lower, upper)
            spin.setValue(default)
            spin.setAccessibleName(f"渐变{label}数值")
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            gradient_layout.addWidget(QtWidgets.QLabel(label), row, 0)
            gradient_layout.addWidget(slider, row, 1)
            gradient_layout.addWidget(spin, row, 2)
            self.gradient_sliders[key] = slider
            self.gradient_value_spins[key] = spin
        gradient_layout.setColumnStretch(1, 1)
        self.gradient_button = QtWidgets.QPushButton("应用渐变（当前选区）")
        self.gradient_button.clicked.connect(self._gradient)
        gradient_layout.addWidget(self.gradient_button, 5, 0, 1, 3)
        self.gradient_direction_group.buttonToggled.connect(self._on_gradient_direction_toggled)
        self._update_gradient_direction()
        layout.addWidget(self.gradient_group)
        self.transfer_group = QtWidgets.QGroupBox("")
        transfer_layout = QtWidgets.QVBoxLayout(self.transfer_group)
        transfer_layout.setContentsMargins(10, 10, 10, 10)
        transfer_grid = QtWidgets.QGridLayout()
        self.copy_button = QtWidgets.QPushButton("复制顶点色")
        self.paste_button = QtWidgets.QPushButton("粘贴（最近点）")
        self.mirror_paste_button = QtWidgets.QPushButton("粘贴（镜像 X）")
        self.id_paste_button = QtWidgets.QPushButton("粘贴（顶点 ID）")
        self.copy_button.clicked.connect(self._copy_colors)
        self.paste_button.clicked.connect(self._paste_colors)
        self.mirror_paste_button.clicked.connect(self._paste_mirror_colors)
        self.id_paste_button.clicked.connect(self._paste_id_colors)
        for index, button in enumerate((self.copy_button, self.id_paste_button,
                                        self.paste_button, self.mirror_paste_button)):
            transfer_grid.addWidget(button, index // 2, index % 2)
        transfer_layout.addLayout(transfer_grid)
        self.clipboard_status = QtWidgets.QLabel("剪贴板：未复制")
        self.clipboard_status.setWordWrap(True)
        set_role(self.clipboard_status, "hint")
        transfer_layout.addWidget(self.clipboard_status)
        layout.addWidget(self.transfer_group)

        self.match_group = QtWidgets.QGroupBox("")
        match_layout = QtWidgets.QVBoxLayout(self.match_group)
        match_buttons = QtWidgets.QHBoxLayout()
        self.match_copy_button = QtWidgets.QPushButton("复制（拓扑）")
        self.match_paste_button = QtWidgets.QPushButton("粘贴（拓扑）")
        self.match_copy_button.clicked.connect(lambda: self._start_match("copy"))
        self.match_paste_button.clicked.connect(lambda: self._start_match("paste"))
        match_buttons.addWidget(self.match_copy_button)
        match_buttons.addWidget(self.match_paste_button)
        match_layout.addLayout(match_buttons)
        self.match_status = QtWidgets.QLabel("拓扑剪贴板：未复制")
        self.match_status.setWordWrap(True)
        set_role(self.match_status, "hint")
        match_layout.addWidget(self.match_status)
        layout.addWidget(self.match_group)
        self.undo_note = QtWidgets.QLabel("改色不支持 Ctrl+Z。")
        self.undo_note.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        set_role(self.undo_note, "hint")
        layout.addWidget(self.undo_note)
        for button in self.channel_buttons.values():
            button.toggled.connect(self._on_channel_toggled)
        self._update_channel()

    def _channel(self):
        return int(self.channel_group.checkedButton().property("channel"))

    def _fit_card_height(self):
        # The canvas proxy grows with a larger minimum size but does not shrink
        # when an optional gradient panel is hidden. Keep its width and position.
        self.layout().activate()
        card = self.window()
        card.layout().invalidate()
        card.layout().activate()
        height = card.layout().totalHeightForWidth(card.width())
        if height < 0:
            height = card.sizeHint().height()
        if height > 0 and card.height() != height:
            card.resize(card.width(), height)

    def _on_channel_toggled(self, checked):
        if checked:
            self._update_channel()
            self.log_run("vertex_color_display", channel=self._channel())
            if self._numbers_session.enabled:
                self._sync_numbers()

    def _component_toggled(self, *_):
        self._update_channel()
        if self._numbers_session.enabled:
            self._sync_numbers()

    def _toggle_numbers(self, *_):
        self._sync_numbers()

    def _sync_numbers(self):
        data = self.log_run('vertex_color_numbers', enabled=self.numbers_check.isChecked(),
                            channel=self._channel(), mask=[check.isChecked() for check in self.checks],
                            owner=self._numbers_session.owner)
        active = (bool(data.get('enabled')) if data and 'enabled' in data
                  else self._numbers_session.enabled)
        self._set_numbers_active(active)

    def _set_numbers_active(self, active):
        self._numbers_session.enabled = active
        with QtCore.QSignalBlocker(self.numbers_check):
            self.numbers_check.setChecked(active)
        self.numbers_timer.start() if active else self.numbers_timer.stop()
        self._update_channel()

    def _poll_numbers(self):
        try:
            result = self.client.call('vertex_color_numbers_status', owner=self._numbers_session.owner)
        except Exception as exc:
            result = dict(ok=False, error=str(exc))
        data = result.get('data') or {}
        if not result.get('ok'):
            self.log_result('vertex_color_numbers', result)
            self._set_numbers_active(False)
        elif not data.get('enabled'):
            self._set_numbers_active(False)
            self.log.warn('[顶点颜色] 数值显示已停止' + ('：' + data['error'] if data.get('error') else '，可重新开启。'))
        elif data.get('limited') and not self._numbers_limited:
            self.log.warn(f"[顶点颜色] 选中顶点超过 {data.get('limit', 500)} 个，仅显示前 {data.get('limit', 500)} 个。")
        self._numbers_limited = bool(data.get('limited'))

    def _update_channel(self, *_):
        is_alpha = self._channel() == -2
        for check, slider, spin in zip(self.checks, self.sliders, self.spins):
            check.setEnabled(not is_alpha or self.numbers_check.isChecked())
            slider.setEnabled(not is_alpha and check.isChecked())
            spin.setEnabled(not is_alpha and check.isChecked())
        for control in (self.alpha_label, self.alpha_slider, self.alpha_spin):
            control.setEnabled(is_alpha)
        for button in self.preset_buttons:
            button.setEnabled(not is_alpha)
        name = {0: "Color", -2: "Alpha", -1: "Illumination", 17: "M17", 18: "M18"}[self._channel()]
        self.set_button.setText(f"设置 {name}（当前选区）")
        self._update_match_buttons()

    def _apply(self):
        if self._channel() == -2:
            self.log_run("vertex_color", channel=-2, alpha=self.alpha_spin.value())
        else:
            self._apply_rgb([s.value() for s in self.spins])

    def _apply_rgb(self, rgb):
        self.log_run("vertex_color", channel=self._channel(), rgb=list(rgb),
                     mask=[c.isChecked() for c in self.checks])

    def _select_by_color(self):
        params = dict(channel=self._channel(), tolerance=self.tolerance_spin.value())
        if self._channel() == -2:
            params['alpha'] = self.alpha_spin.value()
        else:
            params.update(rgb=[s.value() for s in self.spins], mask=[c.isChecked() for c in self.checks])
        self.log_run("vertex_color_select", **params)

    def _copy_colors(self):
        data = self.log_run("vertex_color_copy", channel=self._channel())
        if data and not data.get('warning') and data.get('count', 0):
            self.clipboard_status.setText(f"{data.get('source', '')} · {data.get('channel_name', 'Color')}："
                                          f"已复制 {data['count']} 个顶点")
            self._height_timer.start()

    def _paste_mirror_colors(self):
        self._paste_colors(mirror_x=True)

    def _paste_id_colors(self):
        self._paste_colors(by_vertex_id=True)

    def _paste_colors(self, mirror_x=False, by_vertex_id=False):
        params = dict(channel=self._channel())
        if mirror_x:
            params['mirror_x'] = True
        if by_vertex_id:
            params['by_vertex_id'] = True
        if self._channel() != -2:
            params['mask'] = [c.isChecked() for c in self.checks]
        self.log_run("vertex_color_paste", **params)

    def _update_match_buttons(self):
        channel = self._channel()
        enabled = self._match_task is None
        self.match_copy_button.setEnabled(enabled)
        clip = self._match_clipboard
        self.match_paste_button.setEnabled(enabled and clip is not None and clip["channel"] == channel)

    def _start_match(self, operation):
        channel = self._channel()
        if self._match_task is not None:
            return
        mask = [True, True, True] if channel == -2 else [c.isChecked() for c in self.checks]
        if operation == "paste" and not any(mask):
            self.log.warn("[顶点颜色] 请至少勾选一个 RGB 分量")
            return
        task = _MatchTask(self.client, operation, self._match_clipboard, mask, channel)
        self._match_task = task
        self.match_status.setText("正在复制拓扑选区…" if operation == "copy" else "正在匹配并粘贴…")
        self._update_match_buttons()
        task.signals.finished.connect(self._match_finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(str, object)
    def _match_finished(self, operation, result):
        self._match_task = None
        data = result.get("data") or {}
        if not result.get("ok"):
            self.log.error(f"[顶点颜色] 拓扑操作失败：{result.get('error', '')}")
        elif data.get("warning"):
            self.log.warn(f"[顶点颜色] {data['warning']}")
        elif operation == "copy":
            self._match_clipboard = data
            self.log.info(f"[顶点颜色] {data['object']} · {data['channel_name']}：已复制 {data['count']} 个顶点用于拓扑匹配")
        else:
            self.log.info(f"[顶点颜色] {data['source']} → {data['object']} · {data['channel_name']}：按拓扑粘贴 "
                          f"{data['vertices']} 个顶点，跳过 {data['skipped']} 个顶点")
        if self._match_clipboard is None:
            self.match_status.setText("拓扑剪贴板：未复制")
        else:
            clip = self._match_clipboard
            self.match_status.setText(f"{clip['object']} · {clip['channel_name']}：已复制 {clip['count']} 个顶点用于拓扑匹配")
        self._update_match_buttons()
        self._height_timer.start()

    def _preset(self, rgb):
        if self._channel() != -2:
            self._apply_rgb(rgb)

    def _scale(self):
        params = dict(channel=self._channel(), factor=self.scale_spin.value())
        if self._channel() != -2:
            params['mask'] = [c.isChecked() for c in self.checks]
        self.log_run("vertex_color_scale", **params)

    def _scale_slider_changed(self, value):
        with QtCore.QSignalBlocker(self.scale_spin):
            self.scale_spin.setValue(value / 100.0)

    def _scale_spin_changed(self, value):
        # Do not feed setValue back into an editor while the user is typing.
        with QtCore.QSignalBlocker(self.scale_slider):
            self.scale_slider.setValue(round(value * 100))

    def _gradient_direction(self):
        return self.gradient_direction_group.checkedButton().property("direction")

    def _on_gradient_direction_toggled(self, button, checked):
        if checked:
            self._update_gradient_direction()

    def _update_gradient_direction(self):
        self.gradient_custom.setVisible(self._gradient_direction() == "custom")
        self.gradient_topology.setVisible(self._gradient_direction() == "topology")
        self.gradient_button.setText("应用渐变（两横线之间）" if self._gradient_direction() == "topology" else
                                     "应用渐变（当前选区）")
        self._update_gradient_point_status()
        self._update_gradient_edge_status()

    def _update_gradient_point_status(self):
        lines = []
        for label, point in zip(("起点", "终点"), self.gradient_points):
            text = "未设置" if point is None else "({:.3f}, {:.3f}, {:.3f})".format(*point)
            lines.append(f"{label}：{text}")
        self.gradient_point_status.setText("\n".join(lines))
        self._height_timer.start()

    def _capture_gradient_point(self, endpoint):
        data = self.log_run("vertex_color_gradient_point")
        if data and not data.get("warning") and data.get("point") is not None:
            self.gradient_points[endpoint] = list(data["point"])
            self._update_gradient_point_status()

    def _update_gradient_edge_status(self):
        self.gradient_edge_status.setText("\n".join(
            f"{label}：{text or '未记录'}" for label, text in zip(("起始", "结束"), self.gradient_edge_labels)))
        self._height_timer.start()

    def _capture_gradient_edges(self, endpoint):
        other = self.gradient_edge_records[1 - endpoint]
        data = self.log_run("vertex_color_gradient_edges", **({"reference": other} if other else {}))
        if data and not data.get("warning") and data.get("record"):
            self.gradient_edge_records[endpoint] = data["record"]
            self.gradient_edge_labels[endpoint] = f"{data.get('object', '')}（{data.get('edges', 0)} 条边）"
            self._update_gradient_edge_status()

    def _gradient(self):
        params = dict(channel=self._channel(), direction=self._gradient_direction(),
                      **{key: spin.value() for key, spin in self.gradient_value_spins.items()})
        if self._channel() != -2:
            params['mask'] = [check.isChecked() for check in self.checks]
        if params['direction'] == "custom":
            params.update(start=self.gradient_points[0], end=self.gradient_points[1])
        elif params['direction'] == "topology":
            params.update(start=self.gradient_edge_records[0], end=self.gradient_edge_records[1])
        self.log_run("vertex_color_gradient", **params)

    def format_log_result(self, action, result):
        if not result.get("ok"):
            operation = {"vertex_color_display": "切换通道显示",
                         "vertex_color_numbers": "切换数值显示",
                         "vertex_color_select": "按颜色选择顶点",
                         "vertex_color_copy": "复制顶点色",
                         "vertex_color_paste": "粘贴顶点色",
                         "vertex_color_scale": "缩放颜色",
                         "vertex_color_gradient": "应用渐变",
                         "vertex_color_gradient_edges": "记录横线",
                         "vertex_color_gradient_point": "读取选区中心"}.get(action, "设置颜色")
            return [("error", operation + "失败：" + str(result.get("error", "")))]
        data = result.get("data") or {}
        if data.get("warning"):
            return [("warn", data["warning"])]
        name = data.get("channel_name", "Color")
        if action == 'vertex_color_numbers':
            return [('info', f"已开启 {name} 通道数值显示，最多 {data.get('limit', 500)} 个选中顶点" if data.get('enabled')
                     else '已关闭数值显示')]
        if action == "vertex_color_copy":
            return [("info", f"{data.get('source', '')} · {name}：已复制 {data.get('count', 0)} 个顶点，"
                     f"跳过 {data.get('skipped', 0)} 个无颜色顶点")]
        if action == "vertex_color_paste":
            components = ("A" if data.get('channel') == -2 else
                          "/".join(c for c, enabled in zip("RGB", data.get('mask', [])) if enabled))
            source_name = data.get('source_channel_name', name)
            skipped = ""
            if data.get('by_vertex_id'):
                method = "按顶点 ID 粘贴"
                skipped = f"，跳过 {data.get('skipped', 0)} 个顶点"
            else:
                method = "按世界 X=0 镜像最近点粘贴" if data.get('mirror_x') else "按最近点粘贴"
            return [("info", f"{data.get('source', '')} → {data.get('object', '')} · {source_name} → {name}："
                     f"已{method} {data.get('vertices', 0)} 个几何顶点{skipped}，修改分量 {components}")]
        if action == "vertex_color_select":
            target = (f"A={data.get('alpha')}" if data.get('channel') == -2 else
                      "/".join(f"{c}={value}" for c, value, enabled in
                               zip("RGB", data.get('rgb', []), data.get('mask', [])) if enabled))
            return [("info", f"{data.get('object', '')} · {name}：按颜色选择顶点，目标 {target}，"
                     f"容差 {data.get('tolerance', 0)}，搜索 {data.get('searched', 0)} 个顶点，"
                     f"匹配 {data.get('count', 0)} 个，已切换到顶点层级")]
        if action == "vertex_color_display":
            return [("info", f"{data.get('object', '')}：已切换为 {name} 通道显示")]
        if action == "vertex_color_gradient_point":
            point = "({:.3f}, {:.3f}, {:.3f})".format(*data['point'])
            return [("info", f"{data.get('object', '')}：已读取 {data.get('vertices', 0)} 个顶点的世界坐标中心 {point}")]
        if action == "vertex_color_gradient_edges":
            operation = "已自动补全并记录横线" if data.get("expanded") else "已记录横线"
            return [("info", f"{data.get('object', '')}：{operation}，{data.get('edges', 0)} 条边、"
                     f"{data.get('vertices', 0)} 个顶点")]
        if action == "vertex_color_gradient":
            components = ("A" if data.get("channel") == -2 else
                          "/".join(c for c, enabled in zip("RGB", data.get("mask", [])) if enabled))
            direction = "自定义两点" if data.get("direction") == "custom" else "世界 " + data.get("direction", "-Z")
            if data.get("direction") == "topology":
                direction = f"沿拓扑（{data.get('rows', 0)} 排）"
            return [("info", f"{data.get('object', '')} · {name}：已应用{direction}渐变，"
                     f"{data.get('vertices', 0)} 个几何顶点、{data.get('count', 0)} 个颜色顶点，修改分量 {components}，"
                     f"范围 {data.get('minimum', 0)}→{data.get('maximum', 255)}，曲线 {data.get('curve', 50)}")]
        if action == "vertex_color_scale":
            components = ("A" if data.get("channel") == -2 else
                          "/".join(c for c, enabled in zip("RGB", data.get("mask", [])) if enabled))
            return [("info", f"{data.get('object', '')} · {name}：已缩放 {data.get('vertices', 0)} 个几何顶点、"
                     f"{data.get('count', 0)} 个颜色顶点，倍率 ×{data.get('factor', 1):.2f}，修改分量 {components}")]
        components = (f"A={data.get('alpha')}" if data.get("channel") == -2 else
                      "/".join(c for c, enabled in zip("RGB", data.get("mask", [])) if enabled))
        message = (f"{data.get('object', '')} · {name}：已设置 {data.get('vertices', 0)} 个几何顶点、"
                   f"{data.get('count', 0)} 个颜色顶点，修改分量 {components}")
        return [("info", message)]


def create_widget(client, log, parent=None):
    return VertexColorWidget(client, log, parent)
