"""
activity_bar.py — 主窗口左侧 48px 极窄栏。

3 个图标：
- 资源管理器（树）
- 窗口置顶（图钉，设置上方，独立开关）
- 设置（齿轮，bottom）

⚠️ 原来还有第 2 个「搜索」图标（2026-10-19 移除）：
   搜索改为画布上的 Ctrl+F 浮层（ui/canvas_search.py）。
   移除它的收益不只是少一个图标 —— 侧栏从 3 个 tab 降到 2 个，
   而搜索本身在旧位置既占宽度又和资源树指向同一份数据。
激活视觉：右侧细竖条（VSCode 风）+ 图标变亮。
点击已激活图标 = 隐藏对应 panel。
设置在SidePanel内显示。
"""

from PySide6 import QtCore, QtGui, QtWidgets

import paths
from .app_style import activity_icon


def _make_icon(name: str) -> QtGui.QIcon:
    """读取 SVG，并提供普通、悬停、选中和禁用图标。

    ⚠️ 走 paths.resource() 而不是 `Path(__file__).parent / "resources"`：
       frozen 后 __file__ 指向 _internal/，图标会找不到（全灰图标）。
       SVG 与其它资源一样**外置**在 exe 同级的 ui/resources/。
    """
    return activity_icon(paths.resource(f"{name}.svg"))


class _BarButton(QtWidgets.QToolButton):
    """单个图标按钮 + 激活竖条。"""

    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.setObjectName(f"ab_btn_{icon_name}")
        self.setProperty("qzRole", "activity")
        self.setIcon(_make_icon(icon_name))
        self.setIconSize(QtCore.QSize(22, 22))
        self.setFixedSize(48, 48)
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)


class ActivityBar(QtWidgets.QFrame):
    """发出 panel_changed(index) 信号，index = -1 表示关闭。"""

    panel_changed = QtCore.Signal(int)   # 0=资源, 1=设置, -1=无
    settings_clicked = QtCore.Signal()
    always_on_top_changed = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ActivityBar")
        self.setFixedWidth(48)

        self._btn_resource = _BarButton("explorer", "资源")
        self._btn_settings = _BarButton("settings", "设置")
        self._btn_pin = _BarButton("pin", "窗口置顶")
        self._btn_pin.setAccessibleName("窗口置顶")

        # 设置不放 top，单独 stretch 把按钮顶到顶部
        top = QtWidgets.QVBoxLayout(self)
        top.setContentsMargins(0, 8, 0, 8)
        top.setSpacing(0)
        top.addWidget(self._btn_resource)
        top.addStretch(1)
        top.addWidget(self._btn_pin)
        top.addWidget(self._btn_settings)

        # Only panel buttons are exclusive; the pin is independent.
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self._btn_resource, 0)
        self._group.addButton(self._btn_settings, 1)

        self._current = -1
        self._btn_resource.clicked.connect(self._on_resource_clicked)
        self._btn_settings.clicked.connect(self._on_settings_clicked)
        self._btn_pin.toggled.connect(self.always_on_top_changed.emit)

    # ── public API ─────────────────────────────────
    def set_always_on_top(self, enabled: bool):
        blocker = QtCore.QSignalBlocker(self._btn_pin)
        self._btn_pin.setChecked(enabled)
        self._btn_pin.setToolTip("取消置顶" if enabled else "窗口置顶")
        del blocker

    def show_panel(self, index: int):
        """模拟点击对应图标；当前页再次点击会关闭。"""
        mapping = {
            0: (self._btn_resource, self._on_resource_clicked),
            1: (self._btn_settings, self._on_settings_clicked),
        }
        pair = mapping.get(index)
        if pair is None:
            self._close()
            return
        btn, handler = pair
        btn.setChecked(True)
        handler()

    def set_panel(self, index: int):
        """恢复指定页及高亮，不切换开关、不发出设置页点击信号。"""
        self._current = index if index in (0, 1) else -1
        self._group.setExclusive(False)
        for button in self._group.buttons():
            button.setChecked(self._group.id(button) == self._current)
        self._group.setExclusive(True)
        self.panel_changed.emit(self._current)

    def is_showing_settings(self) -> bool:
        return self._current == 1

    # ── slots ──────────────────────────────────────
    def _on_resource_clicked(self):
        if self._current == 0:
            # toggle off
            self._btn_resource.setChecked(False)
            self._close()
        else:
            self.set_panel(0)

    def _on_settings_clicked(self):
        if self._current == 1:
            self._btn_settings.setChecked(False)
            self._close()
        else:
            self.set_panel(1)
            self.settings_clicked.emit()

    def _close(self):
        self.set_panel(-1)
