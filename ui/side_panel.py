"""
side_panel.py — splitter 内的 stacked panel。
切换资源 / 设置。

⚠️ 原来有第三个 tab「搜索」（2026-10-19 移除）：
   搜索已改为画布上的 Ctrl+F 浮层（ui/canvas_search.py）。
   原因：侧栏只 160px 起，搜索面板整块占位置，
   却和资源树指向同一份数据、连到同一个 plugin_activated。
   浮层版顺带修了旧版两个 bug（DCC 前缀缺失 / "ma" 命中全部）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

import paths

from .plugin_loader import PluginInfo
from .plugin_tree import PluginTree
from .settings_panel import SettingsPanel


class SidePanel(QtWidgets.QWidget):
    plugin_activated = QtCore.Signal(PluginInfo)
    refresh_requested = QtCore.Signal()

    # tab index（搜索已移到画布浮层，见模块 docstring）
    TAB_RESOURCE = 0
    TAB_SETTINGS = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setMinimumWidth(160)

        self._stack = QtWidgets.QStackedWidget(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._stack)

        self._tree = PluginTree()
        self._settings = SettingsPanel()
        resource = QtWidgets.QWidget()
        resource_layout = QtWidgets.QVBoxLayout(resource)
        resource_layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 0)
        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("搜索插件或脚本")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._tree.set_filter)
        self._refresh_button = QtWidgets.QToolButton()
        self._refresh_button.setObjectName("PluginTreeRefresh")
        self._refresh_button.setIcon(QtGui.QIcon(paths.resource("refresh.svg")))
        self._refresh_button.setIconSize(QtCore.QSize(14, 14))
        self._refresh_button.setFixedWidth(24)
        self._refresh_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        self._refresh_button.setToolTip("刷新资源树")
        self._refresh_button.setAccessibleName("刷新资源树")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        toolbar.addWidget(self._search, 1)
        toolbar.addWidget(self._refresh_button)
        resource_layout.addLayout(toolbar)
        resource_layout.addWidget(self._tree)
        self._stack.addWidget(resource)
        self._stack.addWidget(self._settings)

        self._tree.plugin_activated.connect(self.plugin_activated.emit)

    def show_index(self, index: int):
        """-1 = 隐藏（外部把 widget hide() 即可）"""
        self._stack.setCurrentIndex(max(0, index))
        self._update_minimum_width()

    def _update_minimum_width(self):
        width = self._settings.content_width() if self.current_index() == self.TAB_SETTINGS else 0
        self.setMinimumWidth(max(160, width))

    def current_index(self) -> int:
        return self._stack.currentIndex()

    def set_plugins(self, plugins):
        self._tree.set_plugins(plugins)
        self._settings.set_plugin_summary(plugins)

    def set_open_keys(self, keys):
        self._tree.set_open_keys(keys)

    # ── 设置面板代理接口 ─────────────────────────
    @property
    def settings(self) -> SettingsPanel:
        return self._settings

    def set_bridge_states(self, states, host, ports, versions, pending=None):
        self._settings.set_bridge_states(states, host, ports, versions, pending)
        if self.current_index() == self.TAB_SETTINGS:
            self._update_minimum_width()
