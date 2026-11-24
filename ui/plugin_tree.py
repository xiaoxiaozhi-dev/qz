"""
plugin_tree.py — 资源视图。

结构（VSCode Explorer 风）：
  资源
  ├ maya (8)
  │   ├ 骨骼镜像     ← (已开) 灰显
  │   ├ ...
  │   └ 设置 (3)          ← 调试/设置类插件的子组（2026-10-21 加）
  │       ├ 自动启动安装（安装）
  │       ├ 连接测试（ping）
  │       └ 重载动作库（reload plug）
  └ max (5)
      ├ 创建 Box
      └ 设置 (3)
          └ ...

分组来自 manifest 的 `category` 字段（空 = 平铺在 DCC 下）。
⚠️ 两级折叠记忆用**不同的键**：
   DCC 分组 ("dcc", dcc) / 「设置」子组 ("cat", dcc, category)
   —— 共用一个键会导致折叠子组时把整个 DCC 分支也收起来。
"""

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Dict, List

from . import theme
from .plugin_loader import PluginInfo


from dcc_bridge import DCC_PORTS

DCC_LABELS = {"maya": "maya", "max": "3ds Max", "blender": "Blender"}
DCC_ORDER = tuple(DCC_PORTS)

# manifest 的 category 内部标识 → 树上显示的名字
# ⚠️ manifest 里存英文标识，显示名在这里映射 ——
#   改名只动这一处，不散落在 6 个 manifest 里。
CATEGORY_LABELS = {"settings": "设置", "user": "用户自定义"}


class PluginTree(QtWidgets.QTreeWidget):
    """资源树。"""

    # 双击 / 拖拽到画布都触发这个
    plugin_activated = QtCore.Signal(PluginInfo)
    add_script_requested = QtCore.Signal(str)
    manage_script_requested = QtCore.Signal(str, object)
    manage_plugin_requested = QtCore.Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PluginTree")
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setExpandsOnDoubleClick(False)
        # ★ 插件前的 DCC 小图标：不设 iconSize 时 Qt 用 style 默认值
        #   （实测 16px，但不同 style 会变），显式钉死才能保证
        #   与 QPainter 画的字形尺寸匹配
        self.setIconSize(QtCore.QSize(16, 16))
        # ::branch 必须显式指定SVG，否则Qt会隐藏箭头；规则在style.qss。
        self._plugins_by_key: Dict[tuple, PluginInfo] = {}
        self._open_keys: set = set()
        # 用户主动折叠过的 DCC 分支（2026-10-19）。集合里 = 保持折叠。
        self._user_collapsed: set = set()
        # ★ refresh 期间的哨兵：_refresh_groups 里自己调setExpanded 也会触发
        #   itemExpanded/itemCollapsed，不挡住的话会被当成"用户手动折叠"记进去
        #   （比如某分组插件数临时变 0 被自动折叠，之后就再也弹不开了）。
        self._in_refresh: bool = False
        self._build()
        # ★ 折叠变化要用 itemExpanded/itemCollapsed 捕获，
        #   不能在 _refresh_groups 里推断 —— 那里每轮都重建 item，
        #   拿不到"这一轮是谁被点了"。
        self.itemExpanded.connect(self._on_item_expanded)
        self.itemCollapsed.connect(self._on_item_collapsed)
        self.itemClicked.connect(self._on_add_clicked)
        self.itemActivated.connect(self._on_add_clicked)
        # 设置面板改色 → 树图标跟着换。
        # 图标缓存在 theme 单例里，改色时已失效，这里只需重建 item。
        theme.theme.changed.connect(self._on_theme_changed)

    def _on_item_expanded(self, item):
        if self._in_refresh:
            return
        key = self._item_collapse_key(item)
        if key:
            self._user_collapsed.discard(key)

    def _on_add_clicked(self, item, column=0):
        data = item.data(0, QtCore.Qt.UserRole)
        if data and data[0] == 'user_add':
            self.add_script_requested.emit(data[1])

    def _on_item_collapsed(self, item):
        if self._in_refresh:
            return
        key = self._item_collapse_key(item)
        if key:
            self._user_collapsed.add(key)

    @staticmethod
    def _item_collapse_key(item):
        """取出这个 item 的「折叠记忆键」。

        两级结构有两种可折叠节点，各用不同的键：
          DCC 分组      ("dcc", "maya")
          「设置」子组  ("cat", "maya", "settings")

        ⚠️ 之前只有 DCC 一级，键是裸的 dcc 字符串。
           加了子组后必须区分，否则折叠「设置」会连带把 DCC 分组也记上，
           刷新后整个分支都收起来。
        """
        data = item.data(0, QtCore.Qt.UserRole)
        if not isinstance(data, (tuple, list)) or not data:
            return None
        tag = data[0]
        if tag == "group" and len(data) == 2:
            return ("dcc", data[1])
        if tag == "category" and len(data) == 3:
            return ("cat", data[1], data[2])
        return None

    @staticmethod
    def _item_dcc(item):
        """从 item 的 UserRole 里取出 dcc。

        ⚠️ 存的是 ("group", dcc) 二元组，**dcc 在下标 1**。
           之前我写成 data[0]，结果把字面量 "group" 当成了 dcc ——
           测试直接报出 {'group'}，一眼看出是这个错。
           ⚠️ 2026-10-21 加了子组，UserRole 可能有两种形状：
              ("group", dcc) / ("category", dcc, cat)
           所以这里按长度判断，别写死下标。
        """
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, (tuple, list)) and len(data) >= 2 \
                and data[0] in ("group", "category"):
            return data[1]
        return None

    # ── 构建 ──────────────────────────────────
    def _build(self):
        self.clear()
        root = self.invisibleRootItem()
        for dcc in DCC_ORDER:
            label = DCC_LABELS.get(dcc, dcc)
            group = QtWidgets.QTreeWidgetItem([label])
            group.setData(0, QtCore.Qt.UserRole, ("group", dcc))
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setForeground(0, QtGui.QBrush(QtGui.QColor("#cccccc")))
            self.addTopLevelItem(group)
            # 初始展开
            group.setExpanded(True)

        # DCC groups 的索引（dcc -> QTreeWidgetItem）
        self._groups: Dict[str, QtWidgets.QTreeWidgetItem] = {
            dcc: self.topLevelItem(i) for i, dcc in enumerate(DCC_ORDER)
        }

    # ── public ────────────────────────────────
    def set_plugins(self, plugins: List[PluginInfo]):
        """设置全部插件（不分 DCC，内部按 dcc 字段归类）。"""
        # ★ 按复合键索引，不能按裸 id ——
        #   maya 和 max 都有 create_box，裸 id 会互相覆盖 ★
        self._plugins_by_key = {p.key: p for p in plugins}
        self._refresh_groups()

    def set_open_keys(self, open_keys):
        """画布里已经打开的插件复合键。列表中显示为已开。"""
        self._open_keys = set(open_keys)
        self._refresh_groups()

    def collapsed_groups(self):
        """Stable DCC/category keys, including child choices under a closed parent."""
        return [list(key) for key in sorted(self._user_collapsed)]

    def set_collapsed_groups(self, keys):
        self._user_collapsed = {tuple(key) for key in keys}
        self._refresh_groups()

    # ── 内部刷新 ──────────────────────────────
    def _refresh_groups(self):
        # ⚠️ 整个刷新过程都要挡住 itemExpanded/itemCollapsed ——
        #   下面 takeChildren() + setExpanded() 是程序自己做的，
        #   不挡就会被 _on_item_* 当成"用户手动折叠"记进 _user_collapsed。
        prev = self._in_refresh
        self._in_refresh = True
        try:
            self._refresh_groups_inner()
        finally:
            # 用 try/finally：_refresh_groups_inner 里任何异常都不能把哨兵留下，
            # 否则这个树再也响应不了用户折叠。
            self._in_refresh = prev

    def _refresh_groups_inner(self):
        for dcc, group in self._groups.items():
            group.takeChildren()
            plugins = [p for p in self._plugins_by_key.values()
                       if p.dcc == dcc]
            # sort by name
            plugins.sort(key=lambda x: x.name)

            # ★ 分两类：无 category 的平铺，有 category 的进子组。
            #   「设置」组排在**最后** —— 它是调试/设置性质的功能，
            #   不该跟日常用的建模插件混在一起。
            plain = [p for p in plugins if not getattr(p, "category", "")]
            grouped: Dict[str, List[PluginInfo]] = {}
            for p in plugins:
                cat = getattr(p, "category", "")
                if cat:
                    grouped.setdefault(cat, []).append(p)

            grouped.setdefault('user', [])

            for p in plain:
                group.addChild(self._make_plugin_item(p))

            for cat in sorted(grouped, key=lambda category: (category == 'settings', category)):
                items = grouped[cat]
                items.sort(key=lambda x: x.name)
                sub = QtWidgets.QTreeWidgetItem(
                    [f"用户自定义 ({len(items)})" if cat == 'user' else CATEGORY_LABELS.get(cat, cat)])
                sub.setData(0, QtCore.Qt.UserRole, ("category", dcc, cat))
                font = sub.font(0)
                font.setBold(True)
                sub.setFont(0, font)
                sub.setForeground(0, QtGui.QBrush(QtGui.QColor("#bbbbbb")))
                for p in items:
                    sub.addChild(self._make_plugin_item(p))
                if cat == 'user':
                    add = QtWidgets.QTreeWidgetItem(['添加脚本…'])
                    add.setData(0, QtCore.Qt.UserRole, ('user_add', dcc))
                    sub.addChild(add)
                group.addChild(sub)
                # 子组的折叠状态也尊重用户的选择
                key = ("cat", dcc, cat)
                sub.setExpanded(key not in self._user_collapsed)

            # 计数：DCC 的计数是**全部**插件数（含子组里的）
            group.setText(0, f"{DCC_LABELS.get(dcc, dcc)} ({len(plugins)})")
            # ⚠️ 折叠状态：**只在首次构建时**展开一次。
            #   之前这里每次 refresh 都无条件 setExpanded(True)，
            #   结果是用户手动折叠的分支立刻被弹回去 —— 折叠等于没生效。
            #   现在记下用户的折叠选择，refresh 时尊重它。
            #   ⚠️ 存"用户是否主动折叠过"，而不是"是否展开"：
            #   只有用户点过才进这个集合，默认展开的分支不受影响。
            if not plugins:
                group.setExpanded(False)
            else:
                group.setExpanded(("dcc", dcc) not in self._user_collapsed)

    def _make_plugin_item(self, p: PluginInfo):
        """造一个插件 item（带 DCC 图标、已开标记与 tooltip）。"""
        item = QtWidgets.QTreeWidgetItem([p.name])
        item.setData(0, QtCore.Qt.UserRole, ("plugin", p.key))
        item.setToolTip(0, f"{p.dcc}/{p.id}")
        # ★ DCC 图标：底色跟卡片标题栏一致，字母 M / X 形状也不同 ——
        #   颜色 + 字形双保险（只靠颜色，红绿色弱用户分不出来）。
        #   ⚠️ 图标与文字都放在第 0 列，icon 会挤掉文字左侧空间，
        #      这是 QTreeWidget 的正常行为，不用额外排版。
        item.setIcon(0, theme.dcc_icon(p.dcc))
        # 已开启标记
        if p.key in self._open_keys:
            item.setForeground(0, self.palette().placeholderText())
            item.setText(0, f"{p.name}  ⟵")
        return item

    def _on_theme_changed(self):
        """配色变了：重建 item（图标是 item 级对象，改不了得重建）。"""
        self._refresh_groups()

    # ── 事件 ──────────────────────────────────
    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent):
        item = self.itemAt(event.pos())
        if not item:
            return
        data = item.data(0, QtCore.Qt.UserRole)
        if data and data[0] == 'user_add':
            return
        if data and data[0] == "plugin":
            plugin = self._plugins_by_key.get(data[1])
            if plugin:
                self.plugin_activated.emit(plugin)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        if not data:
            return
        plugin = self._plugins_by_key.get(data[1]) if data[0] == 'plugin' else None
        if plugin and not plugin.user_script:
            menu = QtWidgets.QMenu(self)
            menu.addAction('打开插件位置', lambda checked=False: self.manage_plugin_requested.emit('open', plugin))
            menu.exec(event.globalPos())
            return
        user_group = data[0] == 'category' and data[2] == 'user'
        dcc_group = data[0] == 'group' and data[1] in DCC_ORDER
        if not (user_group or dcc_group or data[0] == 'user_add' or (plugin and plugin.user_script)):
            return
        menu = QtWidgets.QMenu(self)
        dcc = plugin.dcc if plugin else data[1]
        menu.addAction('添加脚本…', lambda checked=False: self.add_script_requested.emit(dcc))
        if plugin and plugin.user_script:
            menu.addSeparator()
            if plugin.dcc == 'maya':
                menu.addAction('固定到 Maya 当前工具架',
                    lambda checked=False: self.manage_script_requested.emit('pin_shelf', plugin))
            elif plugin.dcc == 'max':
                menu.addAction('固定到 Max 工具栏',
                    lambda checked=False: self.manage_script_requested.emit('pin_toolbar', plugin))
            for label, operation in [('改名…', 'rename'), ('修改入口…', 'entry'),
                                     ('打开脚本目录', 'open'), ('移除', 'remove')]:
                menu.addAction(label, lambda checked=False, op=operation: self.manage_script_requested.emit(op, plugin))
        menu.exec(event.globalPos())
