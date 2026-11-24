"""
canvas_search.py — 画布上的搜索浮层（Ctrl+F 唤起）。

为什么从侧栏搬到画布：
    侧栏只有 160px 起，搜索面板整块占着位置，却和资源树
    指向同一份数据、连到同一个 plugin_activated 信号。
    搬上画布后不占侧栏宽度，也让右键菜单能专用于场景操作。

⚠️ 用「DCC 图标」区分同名插件，不再用文字前缀 ⚠️
    旧侧栏版只显示 p.name，于是 maya 和 max 的「创建 Box」
    在结果里长得**一模一样**，双击开错就是连错 DCC。
    中间一版统一渲染成 "maya · 创建 Box" —— 能区分，但每个结果
    都多占 8~10 个字符，本来 300px 宽的行更挤。
    现在改成行首一枚 DCC 小图标（与资源树、卡片标题栏同色系，
    字母 M / X 形状不同），文字恢复成纯插件名。
    ⚠️ 图标**不能**单独承担区分责任：颜色之外必须有字形差异，
       否则红绿色弱用户（约 8% 男性）分不出 maya / max。

⚠️ 匹配用 `q == p.dcc` 而不是 `q in p.dcc` ⚠️
    旧版写的是子串匹配，导致查 "ma" 命中全部（'ma' 在 'maya' 和 'max' 里
    都能匹配上），搜索框等于失效。现在 DCC 要么整词匹配，
    要么走 name / id 的子串匹配。
"""

from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .plugin_loader import PluginInfo

# 匹配的字段：名称和 id 用子串（中文名输入方便），DCC 只能整词。
# 用子串匹配 name 的另一个好处是支持拼音首字母之外的模糊，
# 代价是 "创建" 会同时命中「创建 Box」和「创建 Sphere」—— 这是预期的。
_MAX_H = 8


class SearchOverlay(QtWidgets.QFrame):
    """搜索浮层。无边框卡片 + 输入框 + 结果列表。"""

    #: 选中某一项并关闭（回车 / 双击）
    plugin_chosen = QtCore.Signal(object)      # PluginInfo
    #: 请求关闭（Esc / 点击外部）
    dismissed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CanvasSearchOverlay")
        self.setFixedWidth(300)

        self._edit = QtWidgets.QLineEdit(self)
        self._edit.setObjectName("CanvasSearchEdit")
        self._edit.setPlaceholderText("查找插件…")
        self._edit.setClearButtonEnabled(True)

        self._list = QtWidgets.QListWidget(self)
        self._list.setObjectName("CanvasSearchResults")
        self._list.setFixedHeight(_MAX_H * 26)
        # 与资源树同尺寸，保证两处图标看起来是同一个东西
        self._list.setIconSize(QtCore.QSize(16, 16))

        # 搜索和日志的内部滚动条直接匹配应用 controls.qss，共用10px规格。

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self._edit)
        lay.addWidget(self._list)
        self.adjustSize()

        self._plugins: List[PluginInfo] = []
        self._by_key: Dict[tuple, PluginInfo] = {}

        self._edit.textChanged.connect(self._refresh)
        self._edit.returnPressed.connect(self._activate_current)
        # ★★ 鼠标**单击**打开，不只是双击 ★★
        #   只连了 itemDoubleClicked 的后果：用户单击结果项没反应，
        #   必须精确双击才打开（实测：单击 plugin_chosen 完全不触发，
        #   双击才触发）。而这是搜索浮层，单击就该是主路径 ——
        #   双击对 8 行小列表来说是多余的一层手势。
        #   ★ 两种都留着：双击时 Qt 会先发单击再发双击，
        #     于是 plugin_chosen 被emit 两次。
        #     而 _on_search_chosen 第二次会被 open_plugin 的
        #     "已存在则置顶"分支吸收，不会开重复节点。
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        # ★ 点结果列表不能抢走输入框焦点，否则继续打字就没反应了
        self._list.setFocusPolicy(QtCore.Qt.NoFocus)
        # ★ Esc 关闭浮层。必须装在**输入框上**而不是浮层根widget ——
        #   按下 Esc 时焦点在 _edit，事件不冒泡到父 widget，
        #   装在根上会完全不触发（且不会报错，很难发现）。
        self._edit.installEventFilter(self)
        # ★ 失焦关闭（2026-10-19 用户拍板）。
        #   判据是「焦点是否还在浮层内部」，不是「是否还在输入框」——
        #   后者会在点结果列表时立刻关掉，用户根本点不中。
        #
        #   用 QApplication.focusChanged 而不是 QWidget.focusOutEvent：
        #   后者只在 widget 自身失去焦点时触发，
        #   而焦点跳到**另一个**widget 时收不到通知。
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        # 设置面板改色 → 结果图标跟着换（浮层通常是懒创建的，
        # 挂在实例上而不是类上，析构时 Qt 自动断开）
        theme.theme.changed.connect(self._on_theme_changed)

    def _on_focus_changed(self, _old, new):
        # 没打开时不做任何事，也不在焦点变化时偷偷 show
        if self.isHidden():
            return
        # 焦点进了本浮层（含输入框和结果列表）→ 不关
        if new is not None and self.isAncestorOf(new):
            return
        # 焦点到了浮层之外（画布/资源树/其它窗口）→ 关
        self.close_overlay()

    # ── 数据 ──────────────────────────────────────
    def set_plugins(self, plugins: List[PluginInfo]):
        self._plugins = plugins
        # ★ 按复合键索引 —— maya/max 都有 create_box，裸 id 会互相覆盖
        self._by_key = {p.key: p for p in plugins}
        self._refresh(self._edit.text())

    def set_open_keys(self, keys):
        """画布上已打开的节点。结果里标出来，免得和资源树打架。"""
        self._open_keys = set(keys)
        self._refresh(self._edit.text())

    def refresh_open_keys(self, keys):
        """从外部同步已打开集合（浮层懒创建后 Canvas 才能补上）。"""
        self.set_open_keys(keys)

    _open_keys: set = set()

    # ── 匹配 ──────────────────────────────────────
    def _matches(self, q: str) -> List[PluginInfo]:
        if not q:
            # 空查询时列出全部（与旧侧栏版行为一致）：
            # 刚唤起就能看到所有插件，输入即收敛。
            # 显示"(无匹配)"对刚打开的空输入框毫无意义。
            return list(self._plugins)
        return [p for p in self._plugins
                if q in p.name.lower() or q in p.id.lower() or q == p.dcc]

    def _refresh(self, text: str):
        q = (text or "").strip().lower()
        self._list.clear()
        items = self._matches(q)
        for p in items:
            # 左图标承载 DCC 信息，文字只放插件名 ——
            # 两个 DCC 的同名插件靠图标颜色 + 字形区分
            label = p.name
            if p.key in self._open_keys:
                label += "  ⟵"
            it = QtWidgets.QListWidgetItem(theme.dcc_icon(p.dcc), label)
            # item data 走 QVariant，PySide6 会把 Python tuple 包成 PyObject
            # 变体存下来，取回时还是 tuple —— 可以直接存复合键。
            it.setData(QtCore.Qt.UserRole, p.key)
            if p.key in self._open_keys:
                it.setForeground(self.palette().placeholderText())
            self._list.addItem(it)
        if not items:
            ph = QtWidgets.QListWidgetItem("(无匹配)")
            ph.setFlags(QtCore.Qt.NoItemFlags)
            ph.setForeground(self.palette().placeholderText())
            self._list.addItem(ph)
        elif items:
            self._list.setCurrentRow(0)

    # ── 交互 ──────────────────────────────────────
    def _on_theme_changed(self):
        """配色变了：按当前输入重刷一遍（图标是 item 级对象，只能重建）。"""
        self._refresh(self._edit.text())

    def _activate_current(self):
        it = self._list.currentItem()
        if it is None:
            return
        key = it.data(QtCore.Qt.UserRole)
        p = self._by_key.get(key)
        if p is not None:
            self.plugin_chosen.emit(p)

    def _on_item_clicked(self, item):
        """单击结果项 = 打开该插件（鼠标主路径）。"""
        self._emit_chosen(item)

    def _on_double_clicked(self, item):
        self._emit_chosen(item)

    def _emit_chosen(self, item):
        key = item.data(QtCore.Qt.UserRole)
        p = self._by_key.get(key)
        if p is not None:
            self.plugin_chosen.emit(p)

    def focus_input(self):
        self._edit.setFocus()
        self._edit.selectAll()

    def close_overlay(self):
        self.dismissed.emit()

    # ── Esc 关闭 ──────────────────────────────────
    def eventFilter(self, obj, event):
        if obj is self._edit and event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_Escape:
                self.close_overlay()
                return True
            # ↓ / ↑ 在结果里移动，回车选中
            if event.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                self._move_current(event.key() == QtCore.Qt.Key_Down)
                return True
        return super().eventFilter(obj, event)

    def _move_current(self, forward: bool):
        n = self._list.count()
        if n == 0:
            return
        cur = self._list.currentRow()
        nxt = cur + 1 if forward else cur - 1
        # 环形
        if nxt >= n:
            nxt = 0
        elif nxt < 0:
            nxt = n - 1
        self._list.setCurrentRow(nxt)
