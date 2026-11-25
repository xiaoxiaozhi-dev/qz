"""
canvas.py — QGraphicsView 画布。

操作：
- 按 a         = fit-to-view（全部节点）
- 按 f         = focus 当前节点（中心化 + fit-to-node）
- 按 Del       = 删除所有选中节点（批量）
- Ctrl + A     = 全选 / Ctrl + D 或 Esc = 取消选择
- Ctrl + L     = 切换锁定
- Ctrl + F     = 画布上弹出搜索浮层（锁定时不可用）
- Ctrl + 空格  = 专注模式（只留画布，隐藏侧栏+日志；锁定时仍可用）
  ★ 浮层**出现在光标位置**（视口正中是旧行为，用户改掉了），
    且**夹在视口内**：靠右/靠下时翻到光标另一侧，再不行就贴边
  ★ 浮层**焦点离开即关**（判据是"焦点是否还在浮层内部"，
    点结果列表不关，点浮层外才关）
- 右键         = 对齐菜单（**只有 6 个对齐项**，无其它条目；
                  锁定或选中<2 时整组置灰）
  ★ 右键**不清选中**（Qt 默认会清，必须在 mousePressEvent 里拦下+accept）
  ★ 菜单启用状态读**按下瞬间的快照** `_ctx_sel_snapshot`，不是现读 selected_ids()
- 中键拖动      = 平移视口
- 滚轮         = 控件区域交给控件；空白/普通卡片区域以光标为中心缩放
- 左键拖标题栏  = 选中 + 移动节点（多选时全部一起移动）
- 左键点标题栏  = 选中并置顶（方案 A：当前使用的在前面）
- Ctrl + 点标题栏 = 切换选中（加选 / 减选）
- 空白处拖拽    = 框选（矩形碰到即选中）
- 左键点内容区  = 触发插件内部控件

锁定状态（_locked）：不允许拖动/删除/框选节点，但允许中键平移和缩放。
  锁定时Ctrl+F 也不可用，且已开着的搜索浮层会被收起。

⚠️ 右键只做场景操作（目前是对齐），搜索走 Ctrl+F 浮层——
   一块屏幕上一块鼠标右键只能有一个含义，别混用。

对齐的基准是**最大选区**（方案 A，2026-10-19 用户拍板），
算法在 ui/canvas_align.py。注意那是**节点在画布上的位置**对齐，
不是在 DCC 场景里对齐物体（后者要发 bridge action）。
z 序：**置顶是显式行为**，只在明确知道"用户要用这个节点"的地方调_bump_z。

| 操作 | z 序 |
|---|---|
| 单击标题栏 | 置顶 |
| 点内容区（输入框/按钮） | 置顶 |
| 打开节点（含 `Ctrl+F` 搜索打开） | 置顶 |
| **框选** | **不动** |
| **`Ctrl` + 点击加选** | **不动** |
| **`Ctrl+A` 全选** | **不动** |

⚠️ 置顶**不能**放在 `_Proxy.itemChange` 里（那是"选中即置顶"的老做法）：
`itemChange` 拿不到"这次选中从哪来"，单击 / Ctrl 加选 / 框选 / Ctrl+A
对它都是同一个 `ItemSelectedChange`。后果是框选时 Qt 逐个设选中态，
每个都自增一次 z，叠放顺序整个翻转。现在显式化，批量操作不再偷偷改z 序。
详见 `test_zorder.py`。
"""

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Dict, Optional

import paths

from .plugin_loader import PluginInfo
from .plugin_node import PluginNode
from .canvas_align import ALIGN_ACTIONS, align
from .canvas_search import SearchOverlay


DEFAULT_OPEN_OFFSET = (200, 200)

# 视为"正在打字"的控件类型。
#
# 只含 QLineEdit 系：这三类需要接收任意可打印字符，a/f 必须在里面打出来。
# QSpinBox / QComboBox **有意排除** —— 它们只吃数字、箭头、回车，
# 字母和 Ctrl 组合键对它们无意义，拦下来只会让画布快捷键整体失灵
# （用户反馈：下拉框聚焦时按什么键都没反应）。
_INPUT_TYPES = (
    QtWidgets.QLineEdit,
    QtWidgets.QTextEdit,
    QtWidgets.QPlainTextEdit,
)

# 内部含QLineEdit 的复合控件。它们的"内部文本框"不算真正在打字，
# 否则数字框 / 下拉框聚焦时画布快捷键会整体失灵。
_COMPOSITE_INPUT_TYPES = (
    QtWidgets.QAbstractSpinBox,     # 覆盖 QSpinBox / QDoubleSpinBox
    QtWidgets.QComboBox,
)


class _Proxy(QtWidgets.QGraphicsProxyWidget):
    """节点的代理 widget：绑定 plugin_id，自管标题栏选中+拖动。

    设计动机：
      - QGraphicsProxyWidget 的默认 mousePressEvent 会把事件转给内嵌 widget，
        widget accept 后 scene 拿不到事件 → 节点既不能选中也不能拖。
      - QGraphicsItem.mousePressEvent 只 event.accept()，不会启动默认 drag。

    因此这里关掉 Qt 的 ItemIsMovable，自己接管标题栏的 mousePress/Move/Release。
    选中态通过 itemChange(ItemSelectedChange) 同步给 PluginNode，让边框颜色变化。
    """

    def __init__(self, plugin_key: tuple, locked_fn, canvas=None, parent=None):
        super().__init__(parent)
        self._plugin_id = plugin_key          # (dcc, id) 复合键
        self._node: Optional[PluginNode] = None
        self._is_locked = locked_fn          # () -> bool
        self._canvas = canvas                # 借用 Canvas._bump_z
        self._dragging = False
        self._drag_start_scene: Optional[QtCore.QPointF] = None
        self._drag_origins: Dict[tuple, QtCore.QPointF] = {}
        # ★ 按在**已选中**节点上时置顶是**延迟**的：
        #   按下时还分不清「单击」和「拖拽起点」。
        #   mouseRelease 时若确实没移动 → 视为单击 → 才置顶。
        #   （2026-10-22 修：立即置顶会打乱多选组的相对顺序）
        self._pending_bump = False
        # 按下时的场景坐标 —— 用来在 mouseRelease 判断"有没有移动过"
        self._press_scene: Optional[QtCore.QPointF] = None

        # 关闭 Qt 默认 drag（我们自己在 mouseMoveEvent 里 setPos）
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsFocusable, True)

    def attach_node(self, node: PluginNode):
        self._node = node
        node.set_plugin_id(self._plugin_id)

    def plugin_id(self) -> tuple:
        return self._plugin_id

    def node(self) -> Optional[PluginNode]:
        return self._node

    # ── 选中态 → 节点边框（**置顶已移出**）────────────
    def itemChange(self, change, value):
        """这里只管边框颜色，**不再置顶**。

        ⚠️ 为什么置顶从这里挪走（2026-10-19）：
        #   原来这里是 `if on: self.bring_to_front()`，而 `itemChange`
        #   拿不到"这次选中从哪来" —— 单击 / Ctrl 加选 / 框选 / Ctrl+A
        #   对它都是同一个 ItemSelectedChange。结果**框选时 Qt 逐个
        #   设选中态，每个都自增一次 z**，A→B→C→D 被扫过就依次抬到最前，
        #   叠放顺序整个翻转（用户反馈："每框选到一个新节点就被置顶"）。
        #
        #   置顶现在是**显式行为**，由调用方在明确知道意图的地方调
        #   `bring_to_front()`。代价是多写几个调用点，收益是
        #   批量操作不再偷偷改动z 序 —— 那个副作用极难察觉且不可逆。
        """
        if change == QtWidgets.QGraphicsItem.ItemSelectedChange and self._node is not None:
            self._node.set_selected(bool(value))
        return super().itemChange(change, value)

    def bring_to_front(self):
        """把该 proxy 的 z 抬到当前全局最高。"""
        canvas = self._canvas
        if canvas is None:
            return
        canvas._bump_z(self)

    def _bump_unless_moved(self, release_scene=None) -> bool:
        """「没移动过」才置顶。返回是否置顶了。

        ★ 为什么要区分「单击」和「拖拽起点」：
          按下已选中节点时，可能是
            (a) 纯单击 —— 意图是"我要用这个节点" → 应该置顶
            (b) 按住拖动整组 —— 意图是"移动这一组" → **不该动 z**，
                否则组内相对顺序会被打乱（起始节点被抬到最前）。

          按下的那一刻无法区分，所以置顶必须**延迟到 mouseRelease**。

        ⚠️ 判据是「release 的场景坐标 vs press 的场景坐标」，
           **不是**节点中心的位置 ——
           点标题栏时 press 点在标题栏（节点中心在下方），
           两者本来就差一整个标题栏高度，用节点中心比永远判定为"移动过"。
        """
        moved = False
        if self._press_scene is not None and release_scene is not None:
            moved = (release_scene - self._press_scene).manhattanLength() > 1.0
        if not moved:
            self.bring_to_front()
            return True
        return False

    # ── 事件 ────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._node is not None:
            # 用标题栏真实高度判定，不能用硬编码常量——
            # 常量与实际绘制高度不一致时，看着点的是标题栏、实际会被当内容区。
            local_y = event.scenePos().y() - self.scenePos().y()
            if 0 <= local_y < self._node.title_height():
                if self._is_locked():
                    # 锁定：标题栏也不接管，但必须 accept（阻断下层穿透）
                    event.accept()
                    return
                # 未锁定：自己接管
                canvas = self._canvas
                already_selected = self.isSelected()
                # 记下按下位置（用于 mouseRelease 判断"有没有移动过"）
                self._press_scene = QtCore.QPointF(event.scenePos())
                if already_selected and canvas is not None:
                    # 按在已选中节点上 → 保留整组多选，一起拖
                    self._drag_origins = self._capture_drag_origins()
                else:
                    # 按在未选中节点上（或单选）→ 只拖自己
                    self._drag_origins = {self._plugin_id: QtCore.QPointF(self.pos())}

                if self.flags() & QtWidgets.QGraphicsItem.ItemIsSelectable:
                    if canvas is not None:
                        if event.modifiers() & QtCore.Qt.ControlModifier:
                            # Ctrl = 切换选中（加选 / 减选）
                            # ★ **不置顶**（用户拍板）：加选属于"构建选区"，
                            #   不是"我要用这个节点"。
                            canvas.toggle_select(self._plugin_id)
                        elif already_selected:
                            # 已在选中组内 → 不改选中（保留多选）
                            # ★ **这里不能立即置顶**。
                            #   置顶的意思是"我要用这个节点"，但按下的
                            #   这一刻还分不清是「单击」还是「拖拽起点」。
                            #   立即 bring_to_front() 会导致：
                            #     框选 3 个 → 按住 A 拖动整组
                            #     → A 被抬到最前 → 组内相对顺序被打乱
                            #   （这正是 2026-10-22 发现的回归）
                            # ⇒ 改为**延迟到 mouseRelease**，且只在
                            #   「确实没移动」时才算单击、才置顶。
                            self._pending_bump = True
                        else:
                            # 普通点击未选中节点 = 独占选中。
                            # ★ **置顶**：这是"我要用这个节点"的唯一信号，
                            #   原来由 itemChange 兜的底，现在显式补上。
                            canvas.select_only(self._plugin_id, bump_z=True)
                    else:
                        self.setSelected(True)
                self._dragging = True
                self._drag_start_scene = event.scenePos()
                event.accept()
                return
        # 内容区：交给 widget
        super().mousePressEvent(event)
        # 内容区也要置顶——点输入框 / 按钮时同样算"正在使用这个节点"。
        # 不影响功能，纯视觉直觉：操作哪个节点哪个就在最前面。
        if not self._is_locked() and self._canvas is not None:
            self._canvas._bump_z(self)
        # 关键：QGraphicsProxyWidget 转发后不会 accept，
        # 导致 scene 继续把 press 下发给下层重叠节点（事件穿透）。
        # 这里显式 accept，阻断分发链。
        if event.button() == QtCore.Qt.LeftButton:
            event.accept()

    def _capture_drag_origins(self) -> Dict[tuple, QtCore.QPointF]:
        """快照所有选中节点的位置。多选批量移动靠它算增量。"""
        canvas = self._canvas
        if canvas is None:
            return {self._plugin_id: self.pos()}
        sel = canvas.selected_ids()
        if not sel:
            return {self._plugin_id: self.pos()}
        out = {}
        for pid in sel:
            p = canvas._proxies.get(pid)
            if p is not None:
                out[pid] = QtCore.QPointF(p.pos())
        return out

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & QtCore.Qt.LeftButton):
            # 用「从按下点到当前点的总位移」，不是增量。
            # 增量写法（origin + 增量delta）会因为 origin 是快照而永远停在
            # 第一个增量的距离上—— 表现为"只能微微拖动"。
            total = event.scenePos() - self._drag_start_scene
            canvas = self._canvas
            if canvas is not None:
                for pid, origin in self._drag_origins.items():
                    p = canvas._proxies.get(pid)
                    if p is not None:
                        p.setPos(origin + total)
            else:
                self.setPos(self._drag_origins.get(self._plugin_id, self.pos()) + total)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            # 通知 canvas 整批移动结束（落盘用）
            canvas = self._canvas
            if canvas is not None and len(self._drag_origins) > 1:
                for pid in self._drag_origins:
                    p = canvas._proxies.get(pid)
                    if p is not None:
                        canvas.node_moved.emit(pid, p.pos().x(), p.pos().y())
            self._dragging = False
            self._drag_start_scene = None
            self._drag_origins = {}
            # ★ 延迟置顶的结算点：如果这次按下**确实没移动**
            #   （纯单击，没拖动），那它就是"我要用这个节点" → 置顶。
            #   移动过 → 是拖拽 → 不置顶（保持多选组的相对顺序）。
            if self._pending_bump and self._bump_unless_moved(
                    event.scenePos()):
                pass
            self._pending_bump = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Canvas(QtWidgets.QGraphicsView):
    # ★ 这三个信号传的是 (dcc, id) 复合键 = Python tuple。
    #   PySide6 的 Signal(str) 在 emit 时会按 str 去 C++ 拷贝，
    #   遇到 tuple 直接报 "Cannot copy-convert (tuple) to C++"。
    #   改用 object 让它走 PyObject 通道。（注意 QVariant 本身能装 tuple，
    #   setData/setProperty 都没问题，只有 Signal 的类型声明会卡）
    node_opened   = QtCore.Signal(object)     # (dcc, id)
    node_removed  = QtCore.Signal(object)     # (dcc, id)
    user_script_activated = QtCore.Signal(object)
    node_moved    = QtCore.Signal(object, float, float)   # (dcc, id), x, y

    def __init__(self, factory, parent=None):
        """
        factory: callable(info) -> QWidget. 用于把 plugin info 实例化成 widget。
        """
        super().__init__(parent)
        self._factory = factory
        # ★ Ctrl+空格 的回调由 MainWindow 注入。
        #   Canvas 自己不知道"专注模式"要藏哪些 widget（那是主窗口的事），
        #   所以只留一个回调口子。测试里可传lambda 计数。
        self._focus_mode_cb = None
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self._scene.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#1e1e1e")))

        self._proxies: Dict[tuple, _Proxy] = {}   # (dcc, id) -> proxy
        self._info_by_key: Dict[tuple, PluginInfo] = {}
        self._locked = False
        self._z_counter = 0                  # z 序自增器（选中即置顶）
        self._pan_last: Optional[QtCore.QPointF] = None  # 中键平移的按下点
        self._pan_accum_x = 0.0                  # 浮点累积（避免 int截断吃掉慢速位移）
        self._pan_accum_y = 0.0
        self._search: Optional[SearchOverlay] = None   # Ctrl+F 浮层，懒创建
        # 右键按下瞬间的选区快照，供 contextMenuEvent 判定启用状态
        self._ctx_sel_snapshot: list = []

        # 画布背景加点淡网格
        self._draw_grid()
        self._scene.setSceneRect(-10000, -10000, 20000, 20000)

        # 配置 view
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        # 框选：空白处拖拽画橡皮筋（Qt 只会对背景启动，按在节点上仍走节点自身逻辑）
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(QtCore.Qt.IntersectsItemShape)  # 碰到就算
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        # Qt 的框选会直接改 item 的 selected → 同步回_Proxy 内部的 _proxy_id 映射
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)

        # 左下角锁定指示（仅锁定时显示，纯展示不可点，解锁用 Ctrl+L）
        self._lock_indicator = QtWidgets.QLabel(self)
        self._lock_indicator.setObjectName("CanvasLockIndicator")
        self._lock_indicator.setPixmap(
            # ★ 走 paths.resource() —— frozen 后 Path(__file__).parent
            #   指向 _internal/，锁图标会找不到。
            QtGui.QIcon(paths.resource("lock.svg")).pixmap(14, 14))
        self._lock_indicator.setToolTip("布局已锁定 · Ctrl+L 解锁")
        self._lock_indicator.setFixedSize(20, 20)
        self._lock_indicator.setAlignment(QtCore.Qt.AlignCenter)
        self._lock_indicator.hide()
        # 初始化显示状态
        self.set_locked(False)

    # ── 状态 ──────────────────────────────────
    def set_locked(self, on: bool):
        self._locked = bool(on)
        # 锁定时收起搜索浮层 —— 放在这里而不是 Ctrl+L 的 handler 里，
        # 这样无论锁定是被谁触发的（快捷键 / 持久化恢复 / 将来加按钮）都一致。
        # 用户锁画布的意图是"别碰我的东西"，浮层凭空留着不对。
        #
        # ⚠️ 判据用 isHidden()（显式 show/hide 状态）而不是 isVisible()
        #    —— isVisible() 还要看整条父链是否可见，画布自身没被 show() 时
        #    它恒为 False，这个守卫就永远不触发。
        if self._locked and self._search is not None and not self._search.isHidden():
            self._hide_search()
        for p in self._proxies.values():
            # ItemIsSelectable 影响"点空白能否取消选中"等行为；自己拖动不依赖它
            p.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, not self._locked)
            # 只锁定布局；内嵌输入框仍需获得焦点来编辑参数。
            p.setFlag(QtWidgets.QGraphicsItem.ItemIsFocusable, True)
            # 停止任何进行中的拖动
            p._dragging = False
            p._drag_start_scene = None
            p._drag_origins = {}
        # 锁定时清空选中（itemChange 会同步给 node），避免外观错觉
        if self._locked:
            self.clear_selection()
            # 禁用框选（锁定态只保留中键平移 + 滚轮缩放）
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        else:
            # 解锁恢复框选
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
            self.setRubberBandSelectionMode(QtCore.Qt.IntersectsItemShape)
        # 左下角锁指示：只在锁定时显示
        self._lock_indicator.setVisible(self._locked)
        self._position_lock_btn()

    def _position_lock_btn(self):
        self._lock_indicator.move(8, self.height() - self._lock_indicator.height() - 8)

    # ── 右键菜单（2026-10-19）─────────────────────────
    # 画布此前**完全没有**右键处理，所以这是纯新增。
    #
    # ⚠️ 右键只做"场景操作"（目前是对齐），搜索走 Ctrl+F 浮层，
    #    一块屏幕上一块鼠标右键只能有一个含义，别混用。
    #
    # 无选中时**照样弹菜单但整组置灰**（用户拍板）——
    # "点了没反应"会让人以为程序卡了，置灰才是明确反馈。
    #
    # ⚠️ 2026-06 用户要求移除「取消选中」项：
    #    取消选中本来就是右键按在空白处的副作用，而右键已经被
    #    mousePressEvent 拦下（不清选中），所以那一项是多余的。
    #    保留它等于给一个已经不存在的行为留入口。
    def _build_context_menu(self):
        menu = QtWidgets.QMenu(self)
        for action, label in ALIGN_ACTIONS:
            act = menu.addAction(label)
            # Qt 的 triggered 会把 checked 传进来，第一个参数要忽略
            act.triggered.connect(lambda _c=False, a=action: self._on_align(a))
        return menu

    def _on_align(self, action: str):
        """从右键菜单触发的对齐入口。"""
        # 锁定时直接拒绝。菜单本来就会置灰，这里是第二道防线
        # （快捷键 / 程序化调用也可能走到这里）。
        if self._locked:
            return
        sel = self.selected_ids()
        proxies = [self._proxies[k] for k in sel if k in self._proxies]
        if len(proxies) < 2:
            return
        if align(proxies, action):
            # 对齐算一次"正在使用"，抬一下 z 保持交互直觉一致
            for p in proxies:
                self._bump_z(p)

    def contextMenuEvent(self, event):
        """画布右键。弹出对齐菜单（全部 6 项，无其它条目）。

        ⚠️ 用**按下时刻的快照**判定启用状态，而不是现读 selected_ids()。
           contextMenuEvent 在 Qt 事件流里晚于 mousePressEvent，
           中间任何变化都会让它读到不同的 selected_ids()。
        """
        sel = self._ctx_sel_snapshot
        menu = self._build_context_menu()
        # 锁定 或 少于 2 个选中 → 整组置灰（现在菜单里全是对齐项，可以一刀切）
        enable = (not self._locked) and len(sel) >= 2
        for act in menu.actions():
            act.setEnabled(enable)
        menu.exec(event.globalPos())

    # ── 搜索浮层（Ctrl+F）─────────────────────────
    #⚠️ 锁定时**不能**唤起（2026-10-19 用户拍板）——
    #   搜索本身不改布局，但用户锁画布的意图通常是"别碰我的布局"，
    #   让浮层在锁定时凭空出现会破坏这个预期。
    def show_search(self):
        if self._locked:
            return False
        if self._search is None:
            self._search = SearchOverlay(self)
            self._search.plugin_chosen.connect(self._on_search_chosen)
            self._search.dismissed.connect(self._hide_search)
            self._search.set_plugins(list(self._info_by_key.values()))
        # ★ 出现在**光标位置**（2026-06 用户要求），不是视口正中 ——
        #   画布可能已经平移/缩放到很远的地方，正中位置常常在"看起来
        #   像空白"的地方，浮层弹在那儿像是随机出现的。
        #   光标位置符合直觉："我在这儿，你在这儿出现"。
        #
        # ⚠️ 必须夹到视口内：光标可能在视口边缘甚至刚滚出去，
        #   不夹的话浮层会有一半露在画布外面。
        vp = self.viewport().rect()
        cur = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        w = self._search.width()
        h = self._search.height()
        # 光标点下时给一点偏移，别贴着边框（视觉上像"卡住了"）
        OFFSET = 8
        x = cur.x() + OFFSET
        y = cur.y() + OFFSET
        # 右边/下边放不下就翻到另一侧，而不是硬塞出去
        if x + w > vp.width():
            x = cur.x() - w - OFFSET
        if y + h > vp.height():
            y = cur.y() - h - OFFSET
        x = max(0, min(x, vp.width() - w))
        y = max(0, min(y, vp.height() - h))
        self._search.move(x, y)
        self._search.refresh_open_keys(self.selected_ids())
        self._search.show()
        self._search.raise_()
        # ★ 焦点必须落在输入框，否则"按 Ctrl+F 直接打字"不成立。
        #   单纯 setFocus() 在窗口刚激活时可能不生效（窗口管理器还没把
        #   键盘焦点交给我们），所以先 activateWindow 再抢焦点。
        self.activateWindow()
        self._search.focus_input()
        return True

    def _hide_search(self):
        if self._search is not None:
            self._search.hide()
        # ★ 关闭后必须把焦点还给画布，
        #   否则 a / f / 方向键 / Del 全都收不到 —— 用户会以为快捷键坏了。
        self.setFocus(QtCore.Qt.OtherFocusReason)

    def _on_search_chosen(self, info):
        self._hide_search()
        if info.user_script is not None:
            self.user_script_activated.emit(info)
        else:
            self.open_plugin(info)

    def is_locked(self) -> bool:
        return self._locked

    # ── z 序（方案 A：选中/新开即置顶）────────────
    def _bump_z(self, proxy: _Proxy):
        """把 proxy 抬到当前最高层。"""
        self._z_counter += 1
        proxy.setZValue(self._z_counter)

    def bring_to_front(self, plugin_id: str):
        """外部接口：主动置顶某个节点。"""
        p = self._proxies.get(plugin_id)
        if p is not None:
            self._bump_z(p)

    # ── 选中管理（多选）────────────────────────
    def selected_ids(self):
        """返回当前选中的所有 plugin_id。"""
        return [pid for pid, p in self._proxies.items() if p.isSelected()]

    def select_only(self, plugin_id: str, bump_z: bool = False):
        """只选中指定节点（普通点击）。

        ⚠️ bump_z 默认 **False** —— 选中本身不隐含置顶。
        只有明确的"用户要用这个节点"场景才传 True（如标题栏单击）。
        之前置顶藏在 itemChange 里，框选会被逐个抬起，把叠放顺序搞乱。
        """
        for pid, p in self._proxies.items():
            p.setSelected(pid == plugin_id)
        if bump_z:
            p = self._proxies.get(plugin_id)
            if p is not None:
                self._bump_z(p)

    def toggle_select(self, plugin_id: str):
        """切换选中状态（Ctrl + 点击）。**不置顶**。

        加选是"构建选区"，不是"我要用它" —— 与框选保持一致。
        取消选中时 Qt 的 setSelected(False) 是空操作，这里什么都不用补。
        """
        p = self._proxies.get(plugin_id)
        if p is None:
            return
        p.setSelected(not p.isSelected())

    def select_all(self):
        """Ctrl+A 全选。**不置顶**（用户拍板）。

        全部抬到最前会把整屏节点的重叠关系彻底打乱，
        而全选本身没有任何"要用某个节点"的意图。
        """
        if self._locked:
            return
        for p in self._proxies.values():
            p.setSelected(True)

    def clear_selection(self):
        for p in self._proxies.values():
            p.setSelected(False)

    def _on_scene_selection_changed(self):
        """Qt 框选直接改 item 选中态。

        ⚠️ 现在**什么都不做**（2026-10-19）。
        原来这里有个"只抬最高选中项"的补救：
            if len(sel) == 1: self._bump_z(sel[0])
        它是针对"框选多选时 itemChange 逐个置顶"的打补丁，
        但只覆盖了单选，框选多选照样翻转 —— 而根因已经通过
        把置顶移出 itemChange 彻底解决了，留着反而是隐藏行为。
        保留这个空壳是为了让 `selectionChanged` 仍有连接，
        方便将来要做"选中变化 → 更新资源树标记"之类。
        """
        return

    # ── 画布数据 ──────────────────────────────
    def set_all_plugins(self, plugins):
        """main 启动时调一次，存全量插件用来 open。"""
        # ★ 按复合键索引 —— maya 的 create_box 和 max 的 create_box
        #   可以同时开在画布上，互不覆盖 ★
        self._info_by_key = {p.key: p for p in plugins}
        if self._search is not None:
            self._search.set_plugins(plugins)
        for key, proxy in self._proxies.items():
            info = self._info_by_key.get(key)
            if info is not None:
                proxy.node().set_title(info.name)
                content = proxy.node().content()
                content.action_label = info.name
                if info.user_script is not None:
                    content.set_script(info.user_script)

    def plugin_content(self, key):
        proxy = self._proxies.get(key)
        return proxy.node().content() if proxy is not None else None

    def open_plugin(self, info: PluginInfo, x: Optional[int] = None, y: Optional[int] = None):
        """打开插件到画布。x/y=None 表示中央。

        节点用 (dcc, id) 复合键索引，所以同名插件在两个 DCC 下
        是两个独立节点，可以同时存在。
        """
        if info.user_script is not None:
            return
        key = info.key
        if key in self._proxies:
            proxy = self._proxies[key]
            self._scene.setFocusItem(proxy)
            # 已存在 → 再次激活也要置顶
            self._bump_z(proxy)
            return

        widget = self._factory(info)
        if widget is None:
            return
        # dcc 只用来取标题栏配色（卡片一眼区分 maya / max）
        node = PluginNode(info.name, widget, dcc=info.dcc)
        proxy = _Proxy(key, locked_fn=lambda: self._locked, canvas=self)
        proxy.attach_node(node)
        proxy.setWidget(node)
        proxy.setPos(x if x is not None else 0, y if y is not None else 0)
        self._scene.addItem(proxy)
        self._proxies[key] = proxy

        # 新开的节点在已有节点之上
        self._bump_z(proxy)

        # Center on what the user currently sees, regardless of pan, zoom or card count.
        if x is None and y is None:
            view = self.view_state()
            if view is not None:
                center = view['center']
                proxy.setPos(QtCore.QPointF(center['x'], center['y']) - proxy.boundingRect().center())

        # 新开的节点独占选中（其余取消）—— 保证"当前使用的在最前"
        self.select_only(key)
        self.node_opened.emit(key)

    def close_plugin(self, key):
        proxy = self._proxies.pop(key, None)
        if proxy is None:
            return
        self._scene.removeItem(proxy)
        self.node_removed.emit(key)

    def open_ids(self):
        return list(self._proxies.keys())

    def get_node_positions(self) -> Dict[tuple, tuple]:
        return {pid: (p.pos().x(), p.pos().y()) for pid, p in self._proxies.items()}

    # ── 视图操作 ──────────────────────────────
    def view_state(self):
        # Use the geometric viewport center, including half pixels. rect.center()
        # rounds down and would shift the saved view on repeated restarts.
        inverse, valid = self.viewportTransform().inverted()
        if not valid:
            return None
        center = inverse.map(QtCore.QPointF(self.viewport().width() / 2.0,
                                            self.viewport().height() / 2.0))
        return dict(center=dict(x=center.x(), y=center.y()), scale=self.transform().m11())

    def restore_view(self, state):
        # Called after nodes and the window layout are restored; never fit the
        # scene or derive a center from plugin positions.
        self.setTransform(QtGui.QTransform.fromScale(state['scale'], state['scale']))
        center = state['center']
        self.centerOn(center['x'], center['y'])

    def fit_all(self):
        if not self._proxies:
            return
        items = list(self._proxies.values())
        rect = QtCore.QRectF()
        for p in items:
            rect = rect.united(p.sceneBoundingRect())
        self.fitInView(rect.adjusted(-40, -40, 40, 40), QtCore.Qt.KeepAspectRatio)

    def focus_plugin(self, plugin_id: str):
        """聚焦单个节点（外部接口，如侧栏点插件）。"""
        proxy = self._proxies.get(plugin_id)
        if not proxy:
            return
        proxy.setSelected(True)
        rect = proxy.sceneBoundingRect()
        self.fitInView(rect.adjusted(-40, -40, 40, 40), QtCore.Qt.KeepAspectRatio)
        self.centerOn(rect.center())

    def focus_selected(self):
        """把当前所有选中的节点框进视图（多选时是它们的并集）。"""
        rect = QtCore.QRectF()
        for pid in self.selected_ids():
            p = self._proxies.get(pid)
            if p is not None:
                rect = rect.united(p.sceneBoundingRect())
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40), QtCore.Qt.KeepAspectRatio)
        self.centerOn(rect.center())

    # ── 事件 ──────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_lock_btn()

    @staticmethod
    def _is_text_entry(child) -> bool:
        """该控件是否真的在接收文字输入。

        QSpinBox / QComboBox 内部**本身就含一个 QLineEdit**（findChildren 会
        递归找到它，且它确实持有焦点）。若只按类型判断，数字框会被误判成
        "正在打字"，导致画布快捷键整体失灵。

        所以额外检查父级：嵌在 QSpinBox / QComboBox 里的 QLineEdit 不算。
        """
        if not isinstance(child, _INPUT_TYPES):
            return False
        parent = child.parentWidget()
        while parent is not None:
            if isinstance(parent, _COMPOSITE_INPUT_TYPES):
                return False
            parent = parent.parentWidget()
        return True

    def _focused_input(self):
        """返回当前真正获得焦点、且在接收文字的控件，没有则 None。

        不能用 QApplication.focusWidget()：proxy 内嵌控件聚焦时它返回的是
        Canvas 自己，永远判不出"正在输入"，于是 a/f 被当成快捷键吃掉。
        实测子控件的 hasFocus() 才准。

        这里刻意用遍历而非缓存：焦点钩子挂不全（QLineEdit 的 focusInEvent
        不经过 eventFilter），且"聚焦"与"按键"存在时序竞争，缓存会漏键。
        每次重新查也顺带避免了节点删除后的悬垂指针。
        """
        for proxy in self._proxies.values():
            node = proxy.node()
            if node is None:
                continue
            # findChildren 不接受"类型元组"，先取所有子控件再按类型过滤
            for child in node.findChildren(QtWidgets.QWidget):
                try:
                    if child.hasFocus() and self._is_text_entry(child):
                        return child
                except RuntimeError:
                    # 控件已被销毁（节点刚被删）
                    continue
        return None

    def keyPressEvent(self, event):
        # 焦点在输入框时不消费按键——让它沿 Qt 原生链路走到控件。
        # 非 a/f 的字符本来就能进去，a/f 被这里消费掉才是 bug。
        if self._focused_input() is not None:
            return super().keyPressEvent(event)

        # Ctrl+L：切换锁定
        if event.modifiers() == QtCore.Qt.ControlModifier and event.key() == QtCore.Qt.Key_L:
            self.set_locked(not self._locked)
            return

        # Ctrl+F：唤起画布上的搜索浮层
        if event.modifiers() == QtCore.Qt.ControlModifier and event.key() == QtCore.Qt.Key_F:
            # 锁定时直接静默忽略（不弹、不给提示）：
            # 与其他锁定态禁用的操作保持一致的沉默。
            if self.show_search():
                event.accept()
            return

        # Ctrl+空格：专注模式（只留画布）
        # ⚠️ 锁定时也允许 —— 它不改画布内容，只改布局，
        #和"不许拖动节点"的锁定意图不冲突。
        if event.modifiers() == QtCore.Qt.ControlModifier and event.key() == QtCore.Qt.Key_Space:
            cb = self._focus_mode_cb
            if cb is not None:
                cb()
            event.accept()
            return

        # Ctrl+A：全选 / Ctrl+D：取消选择
        if event.modifiers() == QtCore.Qt.ControlModifier:
            if event.key() == QtCore.Qt.Key_A:
                self.select_all()
                return
            if event.key() == QtCore.Qt.Key_D:
                self.clear_selection()
                return

        key = event.key()
        if key == QtCore.Qt.Key_Escape:
            self.clear_selection()
            return
        if key == QtCore.Qt.Key_A:
            self.fit_all()
            return
        if key == QtCore.Qt.Key_F:
            self.focus_selected()
            return
        if key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            if self._locked:
                return
            # 批量删除所有选中节点
            for pid in self.selected_ids():
                self.close_plugin(pid)
            return
        if key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            self._arrow_move(key)
            return
        super().keyPressEvent(event)

    def _wheel_target(self, position):
        """按画面最上层命中控件，坐标经 proxy 映射，适用于缩放后的卡片。"""
        item = self.itemAt(position)
        while item is not None and not isinstance(item, QtWidgets.QGraphicsProxyWidget):
            item = item.parentItem()
        if item is None or item.widget() is None:
            return None, None
        root = item.widget()
        local = item.mapFromScene(self.mapToScene(position)).toPoint()
        child = root.childAt(local) or root
        while child is not None:
            if isinstance(child, (_COMPOSITE_INPUT_TYPES + (
                    QtWidgets.QAbstractScrollArea, QtWidgets.QAbstractSlider))):
                return item, child
            if child is root:
                break
            child = child.parentWidget()
        return item, None

    def _send_wheel_to_proxy(self, proxy, event):
        # 走 Qt 原生 proxy -> QWidget 转发，只发给命中的最上层卡片。
        # 不调用 view 的默认处理，避免控件忽略事件时回退到画布滚动。
        wheel = QtWidgets.QGraphicsSceneWheelEvent(QtCore.QEvent.GraphicsSceneWheel)
        scene_pos = self.mapToScene(event.position().toPoint())
        wheel.setScenePos(scene_pos)
        wheel.setPos(proxy.mapFromScene(scene_pos))
        wheel.setScreenPos(event.globalPosition().toPoint())
        wheel.setButtons(event.buttons())
        wheel.setModifiers(event.modifiers())
        delta = event.angleDelta()
        horizontal = abs(delta.x()) > abs(delta.y())
        wheel.setDelta(delta.x() if horizontal else delta.y())
        wheel.setOrientation(QtCore.Qt.Horizontal if horizontal else QtCore.Qt.Vertical)
        wheel.setPixelDelta(event.pixelDelta())
        wheel.setPhase(event.phase())
        wheel.setInverted(event.inverted())
        wheel.setTimestamp(event.timestamp())
        self._scene.sendEvent(proxy, wheel)

    def wheelEvent(self, event):
        proxy, control = self._wheel_target(event.position().toPoint())
        popup = (proxy is not None and proxy.widget().windowType() == QtCore.Qt.Popup)
        # 弹窗接管输入时，也不能滚动它后面的卡片控件。
        grabber = self._scene.mouseGrabberItem()
        if not popup and (QtWidgets.QApplication.activePopupWidget() is not None or (
                isinstance(grabber, QtWidgets.QGraphicsProxyWidget)
                and grabber.widget() is not None
                and grabber.widget().windowType() == QtCore.Qt.Popup)):
            event.accept()
            return
        if popup or control is not None:
            # 数字/收起的下拉框聚焦后改参数；未聚焦则继续缩放画布。
            focused_child = control.focusWidget() if control is not None else None
            focused = control is not None and (control.hasFocus() or (
                focused_child is not None and focused_child.hasFocus()))
            if popup or not isinstance(control, _COMPOSITE_INPUT_TYPES) or focused:
                self._send_wheel_to_proxy(proxy, event)
                event.accept()
                return
        event.accept()
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        new_scale = self.transform().m11() * factor
        if new_scale < 0.2 or new_scale > 4.0:
            return
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton:
            # 中键平移：自己做视口平移，不用 ScrollHandDrag。
            #
            # 为什么不用 Qt 的 ScrollHandDrag + 伪造事件的老写法：
            # 伪造 QMouseEvent 转给 super() 会污染 QGraphicsView 的按压状态机，
            # 之后左键拖节点就收不到 mouseMove 事件（拖不动）。
            # 自己做平移既不碰状态机，也不依赖 dragMode 切换。
            self._pan_last = event.position()
            event.accept()
            return
        # ★★ 右键必须在这里拦下来并 accept ★★
        #   漏进 super() 的后果：QGraphicsView 默认把右键当"点空白"处理 →
        #   **清掉全部选中**。而右键菜单的 enable 判定读的是 selected_ids()，
        #   于是顺序变成「右键 → 清选中 → contextMenuEvent → len(sel)==0 →
        #   对齐项全灰」。实测确认：框选 N 个后右键，无论点在空白还是
        #   点在节点上，选中都会归0，菜单必然全灰。
        #   accept() 后事件不再下传，Qt 那套清选中的逻辑就不会跑。
        if event.button() == QtCore.Qt.RightButton:
            # ★ 在按下这一刻就把选中快照下来。
            #   contextMenuEvent 在 Qt 的事件流里晚于 mousePressEvent，
            #   中间任何变化（模式切换、事件被accept、后续 scene 动作）
            #   都会让它读到不同的 selected_ids()。
            #   快照让「按右键时选中了什么」和「菜单据此判定」严格一致。
            self._ctx_sel_snapshot = list(self.selected_ids())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.MiddleButton and self._pan_last is not None:
            # 增量法：只加这一帧的差值，避免重复累加
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            # 实测标定：scrollbar 1 单位 = 1 视图像素（= 1/scale 场景单位），
            # 所以光标位移可直接用，**不要**再除 scale。
            # 用浮点累积，避免慢速拖动时 int() 把不足 1 单位的位移截断成 0。
            self._pan_accum_x += delta.x()
            self._pan_accum_y += delta.y()
            hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
            if abs(self._pan_accum_x) >= 1.0:
                step = int(self._pan_accum_x)
                hb.setValue(hb.value() - step)
                self._pan_accum_x -= step
            if abs(self._pan_accum_y) >= 1.0:
                step = int(self._pan_accum_y)
                vb.setValue(vb.value() - step)
                self._pan_accum_y -= step
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton:
            self._pan_last = None
            self._pan_accum_x = 0.0
            self._pan_accum_y = 0.0
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── 选中态 ──────────────────────────────────
    def _selected_plugin_id(self) -> Optional[tuple]:
        """兼容旧接口：返回第一个选中的复合键。"""
        sel = self.selected_ids()
        return sel[0] if sel else None

    def _arrow_move(self, key):
        """选中节点的方向键微调（5px）。多选时全部一起移动。"""
        sel = self.selected_ids()
        if not sel:
            # 没有选中时：选中第一个节点
            if self._proxies:
                first = next(iter(self._proxies.values()))
                first.setSelected(True)
                self.centerOn(first.sceneBoundingRect().center())
            return
        if self._locked:
            return
        dx, dy = 0, 0
        if key == QtCore.Qt.Key_Left:
            dx = -5
        elif key == QtCore.Qt.Key_Right:
            dx = 5
        elif key == QtCore.Qt.Key_Up:
            dy = -5
        elif key == QtCore.Qt.Key_Down:
            dy = 5
        for pid in sel:
            proxy = self._proxies.get(pid)
            if proxy is None:
                continue
            proxy.setPos(proxy.pos().x() + dx, proxy.pos().y() + dy)
            self.node_moved.emit(pid, proxy.pos().x(), proxy.pos().y())

    # ── 节点拖动结束 ──────────────────────────────
    def _on_item_moved(self, plugin_id: str, x: float, y: float):
        self.node_moved.emit(plugin_id, x, y)

    # ── 画网格 ──────────────────────────────────
    def _draw_grid(self):
        """添加一个超大的 grid line item 撑满 scene。"""
        # 简单做法：paintEvent 自画。下面用 graphics item 做一次
        from PySide6.QtWidgets import QGraphicsLineItem
        from PySide6.QtGui import QPen, QColor
        # 50px 网格
        pen = QPen(QColor(58, 58, 58, 60))
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setWidth(0)
        # 把细网格画在 scene 的前景层
        # 其实最简洁的做法是 view 的 drawBackground，下面用 prepare
        self.setStyleSheet("")
        self._grid_pen = pen

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF):
        super().drawBackground(painter, rect)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        pen = QtGui.QPen(QtGui.QColor(58, 58, 58, 80))
        pen.setWidth(0)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        step = 60
        left = int(rect.left()) - (int(rect.left()) % step)
        top = int(rect.top()) - (int(rect.top()) % step)
        x = left
        while x < rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += step
