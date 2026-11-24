"""
settings_panel.py — VSCode 风格的设置面板（SidePanel 设置页）。

设计原则：只读优先，不给"开发者常量"做输入框。
- 桥接：状态、软件版本与连接/断开按钮
- 未连接时直接复制单行启动代码；安装自动启动仍在插件卡片中
- 日志：行数上限，立即生效并保存到用户设置
- 快捷键：只读速查表（快捷键硬编码在代码里，这里是文档不是配置器）
- 插件：目录 + 已加载清单，只读
- 关于：版本 + 打开配置文件

无搜索框、无保存按钮——改完即生效。
"""

import os
import subprocess
import sys

from PySide6 import QtCore, QtGui, QtWidgets

import paths
from maintenance.version import read_version
from dcc_bridge import DCC_PORTS, dcc_label

from . import theme
from .app_style import set_style_property


APP_VERSION = 'v' + read_version()['version']
CONFIG_PATH = "settings.json"

# 只读快捷键速查表：(动作, 快捷键)
# 只列不直观的组合键；框选/删除/方向键等基本操作不列
SHORTCUTS = [
    ("切换锁定", "Ctrl + L"),
    ("查找插件", "Ctrl + F"),
    ("专注模式（只留画布）", "Ctrl + 空格"),
    ("聚焦选中节点", "F"),
    ("适配全部节点", "A"),
]


def _section(title: str) -> QtWidgets.QWidget:
    """带标题的分区容器。"""
    box = QtWidgets.QWidget()
    box.setObjectName("SettingsSection")
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    label = QtWidgets.QLabel(title)
    label.setObjectName("SettingsSectionTitle")
    lay.addWidget(label)
    return box, lay


def _compact_button(text: str) -> QtWidgets.QPushButton:
    btn = QtWidgets.QPushButton(text)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setMinimumHeight(28)
    btn.setProperty("qzDensity", "compact")
    return btn


class SettingsPanel(QtWidgets.QWidget):
    """设置面板。桥接状态由外部 set_bridge_state 更新。"""

    log_limit_changed = QtCore.Signal(int)
    # 安装/卸载仍放在插件卡片中；连接操作位于桥接状态行。
    #   maya/auto_setup（不需连接，路径是常量）
    #   max/auto_setup （需连接才能问 Max 要 startup 目录）
    # ⚠️ Reload 信号也一并移除（2026-10-21）——
    #   Reload 现在是画布插件卡片（plugins/<dcc>/reload/），
    #   在资源树的「设置」组里。
    connection_requested = QtCore.Signal(str)
    update_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPanel")

        scroll = QtWidgets.QScrollArea(self)
        scroll.setObjectName("SettingsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        inner = QtWidgets.QWidget()
        inner.setObjectName("SettingsInner")
        outer = QtWidgets.QVBoxLayout(inner)
        outer.setContentsMargins(16, 16, 16, 24)
        outer.setSpacing(20)

        # 标题
        title = QtWidgets.QLabel("设置")
        title.setObjectName("SettingsTitle")
        sub = QtWidgets.QLabel("改完立即生效，无需保存")
        sub.setObjectName("SettingsSub")
        outer.addWidget(title)
        outer.addWidget(sub)

        outer.addWidget(self._build_bridge())
        outer.addWidget(self._build_colors())
        outer.addWidget(self._build_log())
        outer.addWidget(self._build_shortcuts())
        outer.addWidget(self._build_plugins())
        outer.addWidget(self._build_about())
        outer.addStretch(1)

        scroll.setWidget(inner)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)


    # ── 分区 ────────────────────────────────────
    def _build_bridge(self) -> QtWidgets.QWidget:
        box, lay = _section("桥接")

        self._dots = {}
        self._state_labels = {}
        self._connection_buttons = {}

        for dcc in DCC_PORTS:
            row = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)

            dot = QtWidgets.QLabel()
            dot.setFixedSize(8, 8)
            dot.setProperty("qzRole", "connection")
            dot.setProperty("qzState", "unknown")

            state = QtWidgets.QLabel("%s 检测中…" % dcc_label(dcc))
            state.setObjectName("SettingsValue")

            button = _compact_button('连接')
            button.setToolTip('复制 %s 单行 Python 启动代码' % dcc_label(dcc))
            button.clicked.connect(lambda _checked=False, d=dcc: self.connection_requested.emit(d))

            rl.addWidget(dot)
            rl.addWidget(state)
            rl.addStretch(1)
            rl.addWidget(button)
            lay.addWidget(row)

            self._dots[dcc] = dot
            self._state_labels[dcc] = state
            self._connection_buttons[dcc] = button
        return box

    def _build_colors(self) -> QtWidgets.QWidget:
        """给各宿主独立设置配色。

        颜色只有**一个**语义：卡片标题栏底色 + 插件图标底色。
        ⚠️ 内容区固定 #2d2d2d 不跟着变 —— 插件 widget 内部有大量
           自己的 QSS，给内容区染色会顺着继承链污染它们。
        ⚠️ 选了浅色时标题文字会自动转深（`theme.title_text_color`），
           否则 #e6e6e6 的字压在浅底上等于看不见。
        """
        box, lay = _section("配色")

        self._swatches = {}
        self._hex_labels = {}

        for dcc in theme.DCC_ORDER:
            row = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)

            lbl = QtWidgets.QLabel(dcc_label(dcc))
            lbl.setObjectName("SettingsRowLabel")
            lbl.setFixedWidth(72)

            sw = QtWidgets.QPushButton()
            sw.setProperty("qzRole", "swatch")
            sw.setFixedSize(38, 18)
            sw.setCursor(QtCore.Qt.PointingHandCursor)
            sw.clicked.connect(lambda _checked=False, d=dcc: self._pick_color(d))

            hex_label = QtWidgets.QLabel()
            hex_label.setObjectName("SettingsMono")

            rl.addWidget(lbl)
            rl.addWidget(sw)
            rl.addWidget(hex_label)
            rl.addStretch(1)
            lay.addWidget(row)

            self._swatches[dcc] = sw
            self._hex_labels[dcc] = hex_label

        hint = QtWidgets.QLabel("点色块选择颜色，只影响卡片标题栏与插件图标")
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        reset = _compact_button("恢复默认配色")
        reset.clicked.connect(self._reset_colors)
        reset_row = QtWidgets.QWidget()
        rll = QtWidgets.QHBoxLayout(reset_row)
        rll.setContentsMargins(0, 0, 0, 0)
        rll.addWidget(reset)
        rll.addStretch(1)
        lay.addWidget(reset_row)

        self._sync_swatches()
        # 别处改色（比如将来别的入口 / 手动编辑 theme.json 后重置）也要同步显示
        theme.theme.changed.connect(self._sync_swatches)
        return box

    def _sync_swatches(self):
        """把当前配色画到色块上。"""
        for dcc, sw in self._swatches.items():
            color = theme.theme.title_color(dcc)
            sw.setStyleSheet(
                'QPushButton[qzRole="swatch"] {'
                " background: %s;"
                "}" % color)
            self._hex_labels[dcc].setText(color)

    def _pick_color(self, dcc: str):
        cur = QtGui.QColor(theme.theme.title_color(dcc))
        color = QtWidgets.QColorDialog.getColor(
            cur, self, "选择 %s 的配色" % dcc_label(dcc),
            options=QtWidgets.QColorDialog.DontUseNativeDialog)
        if color.isValid():
            # set_title_color 内部会 save + emit changed，
            # 卡片/资源树/搜索浮层各自接信号重画，这里不用挨个通知
            theme.theme.set_title_color(dcc, color.name())

    def _reset_colors(self):
        theme.theme.reset()

    def _build_log(self) -> QtWidgets.QWidget:
        box, lay = _section("日志")

        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)

        lbl = QtWidgets.QLabel("行数上限")
        lbl.setObjectName("SettingsRowLabel")
        lbl.setFixedWidth(72)

        self._log_spin = QtWidgets.QSpinBox()
        self._log_spin.setRange(100, 100000)
        self._log_spin.setSingleStep(100)
        self._log_spin.setValue(1000)
        self._log_spin.setFixedWidth(96)
        self._log_spin.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._log_spin.setCursor(QtCore.Qt.PointingHandCursor)
        self._log_spin.valueChanged.connect(self._on_log_limit)

        rl.addWidget(lbl)
        rl.addStretch(1)
        rl.addWidget(self._log_spin)
        lay.addWidget(row)
        return box

    def _build_shortcuts(self) -> QtWidgets.QWidget:
        box, lay = _section("快捷键")
        grid_host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)
        for i, (action, key) in enumerate(SHORTCUTS):
            a = QtWidgets.QLabel(action)
            a.setObjectName("SettingsRowLabel")
            k = QtWidgets.QLabel(key)
            k.setObjectName("SettingsMono")
            k.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(a, i, 0)
            grid.addWidget(k, i, 1)
        lay.addWidget(grid_host)
        return box

    def _build_plugins(self) -> QtWidgets.QWidget:
        box, lay = _section("插件")

        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        lbl = QtWidgets.QLabel("目录")
        lbl.setObjectName("SettingsRowLabel")
        lbl.setFixedWidth(72)
        val = QtWidgets.QLabel("./plugins")
        val.setObjectName("SettingsMono")
        rl.addWidget(lbl)
        rl.addStretch(1)
        rl.addWidget(val)
        lay.addWidget(row)

        self._plugin_count = QtWidgets.QLabel("已加载 0 个")
        self._plugin_count.setObjectName("SettingsHint")
        lay.addWidget(self._plugin_count)

        # ⚠️ 这里原来的两个 Reload 按钮已移除（2026-10-21）——
        #   Reload 现在是画布上的插件卡片（plugins/<dcc>/reload/），
        #   跟 ping / 自动启动安装 一起归在资源树的「设置」组里。
        #   卡片版还多做了两件设置面板做不到的事：
        #     1. 未连接时按钮置灰 + 显示「未连接到 Maya」
        #     2. 识别 "unknown action: reload_actions" 这个循环依赖，
        #        给出「用临时启动重跑一次」的可操作指引
        #        （reload_actions 自己也是 action，要先进 action 表才能被调用）
        return box

    def _build_about(self) -> QtWidgets.QWidget:
        box, lay = _section("关于")

        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        lbl = QtWidgets.QLabel("版本")
        lbl.setObjectName("SettingsRowLabel")
        lbl.setFixedWidth(72)
        ver = QtWidgets.QLabel(APP_VERSION)
        ver.setObjectName("SettingsMono")

        btn = _compact_button("打开配置文件")
        btn.clicked.connect(self._open_config)

        rl.addWidget(lbl)
        rl.addWidget(ver)
        rl.addStretch(1)
        rl.addWidget(btn)
        lay.addWidget(row)
        update = _compact_button('从更新包更新…')
        update.setEnabled(paths.is_frozen())
        update.setToolTip('选择已下载的 Qz 更新包' if paths.is_frozen() else '发布版支持从更新包更新')
        update.clicked.connect(lambda checked=False: self.update_requested.emit())
        lay.addWidget(update)
        return box

    # ── 数据更新 ─────────────────────────────────
    def set_bridge_states(self, states: dict, host: str, ports: dict,
                          versions: dict, pending=None):
        """按宿主刷新各行的连接状态和版本。"""
        for dcc, dot in self._dots.items():
            label = dcc_label(dcc)
            ver = str(versions.get(dcc, "") or "")
            ver = ' ' + ver if ver and ver != 'unknown' else ''
            if states.get(dcc):
                set_style_property(dot, "qzState", "connected")
                text = "%s%s 已连接" % (label, ver)
            else:
                set_style_property(dot, "qzState", "disconnected")
                text = "%s%s 未连接" % (label, ver)
            self._state_labels[dcc].setText(text)
            self._state_labels[dcc].setProperty('qzHostLabel', label + ver)
            self._state_labels[dcc].setToolTip('')
            operation = (pending or {}).get(dcc)
            button = self._connection_buttons[dcc]
            button.setToolTip('断开 %s 并关闭桥接端口' % label if states.get(dcc)
                              else '复制 %s 单行 Python 启动代码' % label)
            button.setEnabled(not operation)
            if operation:
                button.setText('断开中…' if operation == 'disconnect' else '连接中…')
            else:
                button.setText('断开' if states.get(dcc) else '连接')

    def set_bridge_health(self, states):
        labels = {'busy': '繁忙', 'waiting': '等待响应',
                  'unresponsive': '持续无响应', 'error': '桥接异常'}
        for dcc, (state, reason) in states.items():
            if state not in labels:
                continue
            label = self._state_labels[dcc]
            name = label.property('qzHostLabel') or dcc_label(dcc)
            label.setText('%s %s' % (name, labels[state]))
            label.setToolTip(reason)
            set_style_property(self._dots[dcc], 'qzState',
                               'disconnected' if state == 'error' else 'waiting')
            button = self._connection_buttons[dcc]
            if dcc == 'max' and state == 'error' and button.isEnabled():
                button.setText('重连')
                button.setToolTip('复制当前中台版本的 Max 桥接恢复代码')

    def set_plugin_summary(self, plugins):
        n = len(plugins)
        if n == 0:
            self._plugin_count.setText("未加载到插件")
            return
        # 每个宿主独立统计插件数量。
        parts = []
        for dcc in DCC_PORTS:
            k = sum(1 for p in plugins if p.dcc == dcc)
            parts.append("%s %d" % (dcc_label(dcc), k))
        self._plugin_count.setText(
            "已加载 %d 个（%s）" % (n, "，".join(parts)))

    def set_log_limit(self, n: int):
        """外部初始化（避免触发 changed 信号）。"""
        self._log_spin.blockSignals(True)
        self._log_spin.setValue(int(n))
        self._log_spin.blockSignals(False)

    def log_limit(self) -> int:
        return int(self._log_spin.value())

    # ── private ─────────────────────────────────
    def _on_log_limit(self, value: int):
        self.log_limit_changed.emit(int(value))

    def _open_config(self):
        """用系统默认程序打开 config.json。"""
        # ★ 走 paths.config_path() —— 打包后指向 exe 同级的 config.json
        path = paths.config_path()
        if not os.path.isfile(path):
            QtWidgets.QToolTip.showText(
                self.cursor().pos(), f"找不到配置文件：\n{path}", self
            )
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)          # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QtWidgets.QToolTip.showText(
                self.cursor().pos(), f"打开失败：{e}", self
            )
