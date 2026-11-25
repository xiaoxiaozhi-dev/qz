"""
main_window.py — 中台 2.0 主窗口。

布局：
  QSplitter(H)
  ├ ActivityBar (48px)
  ├ SidePanel (资源 / 搜索 StackedWidget)
  └ QSplitter(V)
     ├ Canvas (QGraphicsView)
     └ LogPanel (VSCode 底部 panel)

桥接 / 持久化 / 设置 / 插件加载 / 全局 log 走原模式。
"""

import json
import os
import sys
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from dcc_bridge import DCC_PORTS, DccClient, default_port, dcc_label

import paths

from . import blender_setup, layout_state, plugin_loader, plugin_management, maya_setup, max_setup
from .activity_bar import ActivityBar
from .side_panel import SidePanel
from .canvas import Canvas
from .widgets.log_panel import LogPanel
from .async_bridge import AsyncBridge
from .connection_health import ConnectionHealth
from .app_style import apply_application_style
from .window_topmost import set_window_topmost
from . import user_scripts
from maintenance import configuration, online_update
from maintenance.version import read_version


VERSION = read_version()['version']
APP_TITLE = "Qz Tools v" + VERSION


class _ShelfSignals(QtCore.QObject):
    finished = QtCore.Signal(object, object)


class _ShelfTask(QtCore.QRunnable):
    def __init__(self, client, info):
        super().__init__()
        self.client, self.key, self.script = client, info.key, dict(info.user_script)
        self.signals = _ShelfSignals()

    def run(self):
        pin = (user_scripts.pin_script_to_max_toolbar if self.key[0] == 'max'
               else user_scripts.pin_script_to_maya_shelf)
        self.signals.finished.emit(self.key, pin(self.client, self.script))


class _ScriptTask(_ShelfTask):
    def run(self):
        self.signals.finished.emit(self.key, user_scripts.run_script(self.client, self.script))


class _UpdateSignals(QtCore.QObject):
    finished = QtCore.Signal(str, object, str)
    progress = QtCore.Signal(int)


class _UpdateTask(QtCore.QRunnable):
    def __init__(self, phase, value):
        super().__init__()
        self.phase, self.value = phase, value
        self.signals = _UpdateSignals()

    def run(self):
        try:
            if self.phase == 'check':
                result = online_update.check_latest(self.value)
            else:
                result = online_update.download_update(
                    self.value, paths.local_data_dir('updates'), self.signals.progress.emit)
        except Exception as error:
            self.signals.finished.emit(self.phase, None, str(error))
        else:
            self.signals.finished.emit(self.phase, result, '')

# ── 配置 ─────────────────────────────────────────
# ⚠️ 配置用 JSON 不用 YAML（2026-10-19）
# 全项目 11 个配置文件里只有 1 个曾经是 YAML（config.yaml），
# 为了它引入 PyYAML 不划算 —— 依赖 + 两种格式 + PyYAML 的 CVE 历史。
# 注释统一搬到 docs/config.md，配置和文档分离。
# 见 docs/config.md 开头的「为什么改成 JSON」。
CONFIG_FILE = "settings.json"


def _load_config(path=None):
    """读 config.json。

    ⚠️ 用 `encoding="utf-8-sig"` 而不是 "utf-8"：
       Windows 上用记事本存 JSON 可能带上 UTF-8 BOM，
       而 json.load 遇到 BOM 直接抛 JSONDecodeError —— 表现为
       "配置文件读到了但所有设置都回落默认值"，极难察觉。
       utf-8-sig 对有无 BOM 都能正常解析。

    ⚠️ 异常一律吞掉返回 {}。配置读不出来**不能打断启动** ——
       界面照常能用（默认值），只少了几项自定义设置。
       代价是「静默回落」，所以 test_config.py 必须盯住这条。

    path 参数只为测试存在（临时目录里的坏文件），正常运行不传。
    """
    if path is None:
        # ★ 走 paths.project_dir() —— 打包后这里是 exe 所在目录。
        #   原来这里写的是 dirname(dirname(__file__))，frozen 后指向
        #   %TEMP%\_MEIxxxxxx\，config.json 直接读不到。
        return configuration.load()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception as e:
        print(f"[config] load failed: {e}")
        return {}


# ── 窗口几何工具 ───────────────────────────────
def _screens_geometry():
    """返回所有屏幕的可用区域并集 (QRect)，用于校验窗口位置。"""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    rects = [s.availableGeometry() for s in app.screens()]
    if not rects:
        return None
    united = rects[0]
    for r in rects[1:]:
        united = united.united(r)
    return united


def app_screens():
    app = QtWidgets.QApplication.instance()
    return app.screens() if app is not None else []


def app_primary_geometry():
    """主屏幕可用区域；拿不到就退回所有屏幕的并集。"""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    pm = app.primaryScreen()
    if pm is not None:
        return pm.availableGeometry()
    return _screens_geometry()


def _clamp_to_screens(win: dict) -> dict:
    """窗口位置/大小落回可见区域（显示器拔掉 / 分辨率变了的情况）。

    原则：**只在窗口真的找不到时**才纠正（标题栏完全不可见）。
    窗口比屏幕大、或部分超出屏幕，都不动——那是用户自己拖出来的意图，
    应该尊重，只有极端情况（比如上次在 4K 屏，这次换 1080p 且完全出界）
    才把左上角拉回来，避免用户找不着窗口。
    """
    out = dict(win)
    screens = _screens_geometry()
    if screens is None:
        return out

    w = max(int(out.get("w", 1400)), layout_state.MIN_WINDOW_W)
    h = max(int(out.get("h", 880)), layout_state.MIN_WINDOW_H)
    x = int(out.get("x", 100))
    y = int(out.get("y", 80))

    # 标题栏那一行是否落在任一屏幕内（多屏：任一即可）
    title_h = 30
    def _reachable(px, py, pw):
        return screens.intersects(QtCore.QRect(px, py, pw, title_h))

    if _reachable(x, y, w):
        # 位置可用，只把尺寸收敛到"至少能在某屏上操作"
        biggest = max((s.availableGeometry() for s in app_screens()),
                      key=lambda r: r.width() * r.height(), default=None)
        if biggest is not None:
            w = min(w, max(biggest.width(), layout_state.MIN_WINDOW_W))
            h = min(h, max(biggest.height(), layout_state.MIN_WINDOW_H))
        out.update({"x": x, "y": y, "w": w, "h": h})
        return out

    # 标题栏完全不可见 → 必须拉回来
    prim = app_primary_geometry()
    if prim is None:
        prim = screens
    w = min(w, prim.width())
    h = min(h, prim.height())
    x = max(screens.left(), min(x, prim.left() + prim.width() - w))
    y = max(screens.top(), min(y, prim.bottom() - title_h))
    out.update({"x": int(x), "y": int(y), "w": int(w), "h": int(h)})
    return out


# ── 主窗口 ───────────────────────────────────────
class MainWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        apply_application_style(QtWidgets.QApplication.instance())
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1024, 640)

        self._restoring = True          # 恢复期间不触发保存
        self._closing = False            # 关闭过程中忽略异步回调
        self._always_on_top = False
        self._shelf_tasks = {}
        self._script_tasks = {}
        self._update_task = None
        # 专注模式（Ctrl+空格）状态。★ **不持久化**：
        # 这是临时状态，开机就进专注模式会让人看不到资源树。
        self._focus_mode = False
        # 进入专注模式前侧栏/日志的可见状态，退出时精确还原。
        # ★ 必须记：侧栏可能是隐藏的（首次运行默认隐藏），
        #   所以"恢复成进来时的样子"不等于"无脑 show()"。
        self._pre_focus_side_visible = False
        self._pre_focus_log_visible = True
        # 进专注模式前的日志高度（用户没手动拖过时用它恢复）
        self._pre_focus_log_height = 70
        # 用户在专注模式里手动拖过日志吗？
        # ★ 只有用户真的拖了才为 True —— 程序调 setSizes() **不触发**
        #   splitterMoved（实测确认），所以这个标志能干净地区分
        #   "程序收的 0" 和 "用户拖的 0"。
        self._log_user_adjusted = False
        self._pre_focus_log_visible = True

        self._config = _load_config()
        bridge_cfg = self._config.get("bridge", {}) or {}
        self._host = bridge_cfg.get("host", "127.0.0.1")
        _timeout = int(bridge_cfg.get("timeout", 10))
        _ports = bridge_cfg.get("ports", {}) or {}

        # ★ 双桥接并列 ★
        # 各宿主独立维护 socket 端口、心跳定时器及连接状态。
        self._bridges = {}
        self._clients = {}
        self._bridge_ok = {}     # dcc -> bool
        self._versions = {}      # dcc -> str
        self._bridge_timers = {}  # dcc -> QTimer
        self._connection_epochs = dict.fromkeys(DCC_PORTS, 0)
        self._connection_pending = dict.fromkeys(DCC_PORTS)
        self._ping_inflight = dict.fromkeys(DCC_PORTS, False)
        self._attached = dict.fromkeys(DCC_PORTS, False)
        self._health = {dcc: ConnectionHealth() for dcc in DCC_PORTS}
        self._connection_announced = dict.fromkeys(DCC_PORTS, False)
        self._legacy_warned = set()
        self._connection_id = uuid.uuid4().hex

        for dcc in DCC_PORTS:
            port = int(_ports.get(dcc, default_port(dcc)))
            # ★ client 只建一份，bridge 和 widget 共用 ★
            #   早先 bridge 内部又 new 了一个 DccClient，于是同一个 DCC 有两个
            #   socket 客户端：ping 用这个、widget 用那个，host/port 会漂移。
            client = DccClient(self._host, port, dcc=dcc)
            self._clients[dcc] = client
            self._bridges[dcc] = AsyncBridge(
                host=self._host, port=port, timeout=_timeout,
                dcc=dcc, client=client)
            self._bridge_ok[dcc] = False
            self._versions[dcc] = ""
            timer = QtCore.QTimer(self)
            timer.setSingleShot(False)
            timer.timeout.connect(lambda d=dcc: self._kick_ping(d))
            self._bridge_timers[dcc] = timer

        # 插件列表 + 同步 client（widget 直接用）
        # 只列出当前 DCC 的插件（dcc: maya / max / both）
        _scanned = plugin_loader.scan(paths.plugins_dir())
        _user_plugins, _script_errors = user_scripts.scan_all_scripts()
        _scanned.extend(_user_plugins)
        # ★ 不过滤 —— maya / max 是树上的平级分支，
        #   两个分支的插件都在，未连接的 DCC 也照样能打开成卡片 ★
        self._all_plugins = _scanned
        self._plugins_by_key = plugin_loader.index_by_key(_scanned)

        # ── UI ──
        self._activity = ActivityBar()
        self._activity.panel_changed.connect(self._on_panel_changed)
        self._activity.settings_clicked.connect(self._on_settings_opened)
        self._activity.always_on_top_changed.connect(self._set_always_on_top)

        self._side = SidePanel()
        self._side.set_plugins(self._all_plugins)
        self._side._tree.set_collapsed_groups(layout_state.load_collapsed_groups())
        self._side.plugin_activated.connect(self._on_plugin_activated)
        self._side.refresh_requested.connect(self._refresh_plugins)
        # 设置面板：日志上限即时生效
        self._side.settings.log_limit_changed.connect(self._on_log_limit_changed)
        self._side.settings.connection_requested.connect(self._on_connection_clicked)
        self._side.settings.update_requested.connect(self._on_update_requested)
        self._side.settings.check_update_requested.connect(self._on_check_update)
        # ⚠️ Reload 的两个 signal 连接已移除（2026-10-21）——
        #   Reload 现在是画布插件卡片，自己调 client，不需要中台转发。
        self._side.settings.set_log_limit(layout_state.load_log_limit())
        self._side.set_bridge_states(self._bridge_ok, self._host,
                                     {d: self._bridges[d].port
                                      for d in self._bridges},
                                     self._versions)

        self._log = LogPanel()
        self._log.set_max_lines(self._side.settings.log_limit())
        self._log.setObjectName("AppLogPanel")
        for error in _script_errors:
            self._log.error(error)

        def _factory(info):
            return self._make_widget(info)
        self._canvas = Canvas(factory=_factory)
        self._canvas.set_all_plugins(self._all_plugins)
        self._canvas.user_script_activated.connect(self._on_plugin_activated)
        self._side._tree.add_script_requested.connect(self._add_user_script)
        self._side._tree.manage_script_requested.connect(self._manage_user_script)
        self._side._tree.manage_plugin_requested.connect(self._manage_plugin)
        self._canvas.node_opened.connect(self._on_node_opened)
        self._canvas.node_removed.connect(self._on_node_removed)
        self._canvas.node_opened.connect(lambda _pid: self._refresh_side())
        self._canvas.node_removed.connect(lambda _pid: self._refresh_side())
        # Ctrl+空格 → 专注模式。Canvas 只负责识别按键，具体藏哪些
        # widget 由主窗口决定（Canvas 不该知道侧栏的存在）。
        self._canvas._focus_mode_cb = self.toggle_focus_mode

        # 右侧 splitter（日志区高度靠手柄拖动，无折叠/无按钮）
        right_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_split.setHandleWidth(4)
        right_split.addWidget(self._canvas)
        right_split.addWidget(self._log)
        right_split.setStretchFactor(0, 1)
        right_split.setStretchFactor(1, 0)
        # 日志区可拖没（Qt 默认行为）；拖没了靠再拖手柄边缘找回来
        right_split.setCollapsible(1, True)
        # 高度：优先用上次保存的，没有则用默认 70
        self._log_h = layout_state.load_log_height()
        right_split.setSizes([700, self._log_h])
        right_split.splitterMoved.connect(self._on_splitter_moved)
        self._right_split = right_split

        # 主 splitter
        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_split.setHandleWidth(1)
        main_split.addWidget(self._activity)
        main_split.addWidget(self._side)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 0)
        main_split.setStretchFactor(2, 1)
        main_split.setSizes([48, 240, 1000])

        # 先恢复侧栏尺寸布局，再恢复画布视口；恢复设置页不触发连接查询。
        sidebar = layout_state.load_sidebar()
        self._side.show_index(sidebar['page'])
        self._activity.set_panel(sidebar['page'] if sidebar['visible'] else -1)

        central = QtWidgets.QWidget()
        central.setObjectName("MainCentral")
        lay = QtWidgets.QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(main_split)
        self.setCentralWidget(central)

        # 恢复窗口几何（必须在 show 之前）
        self._restore_window()
        self._set_always_on_top(layout_state.load_always_on_top(), persist=False)

        # 启动：两个 DCC 的心跳同时开始
        self._start_bridge_timer()
        QtCore.QTimer.singleShot(50, self._restore_layout)

    @QtCore.Slot(bool)
    def _set_always_on_top(self, enabled: bool, persist=True):
        if self._closing:
            return
        if enabled != self._always_on_top:
            try:
                set_window_topmost(self, enabled)
            except OSError as error:
                self._activity.set_always_on_top(self._always_on_top)
                self._log.warn('切换窗口置顶失败：%s' % error)
                return
            self._always_on_top = enabled
        self._activity.set_always_on_top(enabled)
        if persist:
            layout_state.save_always_on_top(enabled)

    # ── 窗口几何持久化 ────────────────────────
    def _restore_window(self):
        """启动时读取上次的窗口位置/大小（含最大化状态）。"""
        win = layout_state.load_window()
        win = _clamp_to_screens(win)
        try:
            self.setGeometry(win["x"], win["y"], win["w"], win["h"])
            if win.get("maximized"):
                self.showMaximized()
            else:
                self.showNormal()
        except Exception as e:
            print(f"[layout] restore window failed: {e}")

    def _current_window_geometry(self) -> dict:
        """取当前窗口几何；最大化时保留正常态尺寸（用 normalGeometry）。"""
        try:
            maximized = self.isMaximized()
            if maximized:
                g = self.normalGeometry()
            else:
                g = self.geometry()
            return {
                "x": int(g.x()),
                "y": int(g.y()),
                "w": int(g.width()),
                "h": int(g.height()),
                "maximized": bool(maximized),
            }
        except Exception:
            return dict(layout_state.DEFAULT_WINDOW, maximized=False)

    @QtCore.Slot(int, int, int)
    def _on_splitter_moved(self, _pos: int, _index: int):
        """拖动日志区手柄 → 记下新高度。

        ★ 这个槽**只在用户真的拖了手柄时才被调用** ——
          程序调 setSizes() 不触发 splitterMoved（实测确认）。
          所以它是"用户手动调过日志"的唯一可靠信号，
          专注模式靠它区分「程序收的 0」和「用户拖的 0」。
        """
        if self._restoring:
            return
        sizes = self._right_split.sizes()
        if len(sizes) >= 2:
            self._log_h = int(sizes[1])
        # 标记"用户手动拖过"（专注模式退出时据此决定恢复策略）
        if self._focus_mode:
            self._log_user_adjusted = True

    # ── 专注模式（Ctrl + 空格）────────────────
    def toggle_focus_mode(self):
        """只留画布。

        ★ 侧栏/ActivityBar 用 hide()，**日志用收尺寸**（2026-10-19 改）★

        原来的实现三个都 hide()，但用户指出：**日志要能手动拉回来**。
        实测（Qt 语义，offscreen 验证过）：
          - QSplitter 的 widget 默认 collapsible=True，
            拖手柄拖过相邻 widget 时 Qt 做的是**折叠**（把 widget 移到画布外），
            不是压宽度。所以「手动拖到 0」= 折叠隐藏 = hide() 的效果，
            侧栏那侧**不需要改**。
          - 但垂直 splitter（right_split）里，日志高度设 0 时它停在
            **窗口底边**（几何 = (0, 窗口高, 宽, 0)），
            那条 4px 的 QSplitterHandle 仍在画布内 → **能拖回来**。

        所以：
          侧栏/ActivityBar → hide()（移出画布，和手动拖一样）
          日志             → setSizes([全部, 0])（停在底边，手柄可拖）

        ★ 退出时日志高度用**当前实际值**，不是进专注模式前的旧值 ——
          用户在专注模式里手动把日志拖大/拖小，那个值要被尊重
          （2026-10-19 用户拍板：保持，不还原）。
        """
        if self._focus_mode:
            # 退出：侧栏按进入前的可见状态还原
            self._activity.show()
            if self._pre_focus_side_visible:
                self._side.show()
            else:
                self._side.hide()
            # 日志：恢复它**现在**的高度
            # ★ 区分两种 0（2026-10-19 用户拍板）：
            #   - 程序收的 0（进专注模式时压的）→ 退出时恢复成进专注前的高度
            #   - 用户手动拖的 0 → **保持 0**（用户是故意的）
            #   靠 _log_user_adjusted 区分：程序调 setSizes() 不触发
            #   splitterMoved，只有用户真的拖了才置位。
            cur = self._right_split.sizes()
            log_h = int(cur[1]) if len(cur) >= 2 else self._log_h
            if not self._log_user_adjusted:
                # 用户没动过 → 恢复成进专注模式前的高度
                log_h = self._pre_focus_log_height
            self._log.setVisible(True)
            self._set_log_height(log_h)
            self._log_user_adjusted = False
            self._focus_mode = False
        else:
            # 进入：先记住当前状态
            self._pre_focus_side_visible = self._side.isVisible() and self._side.width() > 0
            self._pre_focus_log_visible = self._log.isVisible()
            cur = self._right_split.sizes()
            self._pre_focus_log_height = int(cur[1]) if len(cur) >= 2 \
                else self._log_h
            # ★ 复位标志：进专注模式时清掉，
            #   这样"用户有没有手动拖过日志"能从头判断。
            self._log_user_adjusted = False
            self._activity.hide()
            self._side.hide()
            # 日志：收成 0（不是 hide！hide 会连手柄一起带走，拉不回来）
            self._set_log_height(0)
            self._focus_mode = True
        # 视图不自动 fit —— 自动调会让节点位置"跳一下"，反而干扰。
        # 焦点还给画布：隐藏侧栏后焦点可能落在已销毁/不可见控件上，
        # 不还回来的话 Ctrl+空格 第二次就按不到了。
        self._canvas.setFocus(QtCore.Qt.OtherFocusReason)

    def _set_log_height(self, h: int):
        """设置日志区高度（内部用）。

        ★ 为什么需要这个而不是直接 right_split.setSizes()：
           日志被收成 0 时，splitter 那一项记的是 0，
           但**要恢复**时得知道该给多少。这里统一走一个口，
           顺便处理"恢复时高度为 0"的边界。

        ★ 不会污染 _log_h —— 用户手动拖动才会走 _on_splitter_moved，
           程序调 setSizes() **不触发** splitterMoved（实测确认）。
        """
        sizes = self._right_split.sizes()
        if len(sizes) < 2:
            return
        total = sizes[0] + sizes[1]
        h = max(0, int(h))
        self._right_split.setSizes([total - h, h])

    # ── 插件实例化 ──────────────────────────
    def _make_widget(self, info):
        try:
            factory = info.load_factory()
        except Exception as e:
            self._log.error(f"加载 {info.name} 失败: {e}")
            return None
        # ★ 按 dcc 注入对应的 client ——
        #   widget 自己不感知 DCC，它只知道「我有一个 client」，
        #   所以同一份 UI 代码连到哪个 DCC 完全由这里决定。
        #   maya 插件和 max 插件可以在画布上同时开着，互不干扰。
        client = self._clients[info.dcc]
        widget = factory(client, log=self._log, parent=None)
        # 跨 DCC 插件按需借用现有客户端，不创建第二份连接配置。
        set_clients = getattr(widget, "set_dcc_clients", None)
        if callable(set_clients):
            set_clients(self._clients)
        widget.action_label = info.name
        # 记一笔来源，排障时能一眼看出这个卡片连的是哪个 DCC
        widget._qz_dcc = info.dcc
        widget._qz_key = info.key
        return widget

    def _refresh_plugins(self):
        custom, errors = user_scripts.scan_all_scripts()
        self._all_plugins = plugin_loader.scan(paths.plugins_dir()) + custom
        self._plugins_by_key = plugin_loader.index_by_key(self._all_plugins)
        self._side.set_plugins(self._all_plugins)
        self._canvas.set_all_plugins(self._all_plugins)
        self._refresh_side()
        for error in errors:
            self._log.error(error)

    def _can_update(self):
        if self._shelf_tasks or self._script_tasks or any(
            getattr(proxy.node().content(), '_task', None) is not None
            or getattr(proxy.node().content(), '_busy', False)
            for proxy in self._canvas._proxies.values()
        ):
            self._log.warn('有工具正在执行，请完成后再更新中台。')
            return False
        return True

    def _on_check_update(self):
        if not paths.is_frozen() or self._update_task is not None or self._closing:
            return
        self._side.settings.set_update_state('正在检查 GitHub 发行版…', busy=True)
        self._start_update_task('check', VERSION)

    def _start_update_task(self, phase, value):
        task = self._update_task = _UpdateTask(phase, value)
        task.signals.finished.connect(self._on_online_update_result, QtCore.Qt.QueuedConnection)
        task.signals.progress.connect(self._on_update_progress, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(int)
    def _on_update_progress(self, percent):
        if not self._closing and self._update_task is not None:
            text = '正在校验更新包…' if percent == 100 else '正在下载更新包…'
            self._side.settings.set_update_state(text, busy=True, progress=percent)

    @QtCore.Slot(str, object, str)
    def _on_online_update_result(self, phase, value, error):
        self._update_task = None
        if self._closing:
            return
        settings = self._side.settings
        if error:
            settings.set_update_state('更新失败，可尝试从本地更新包更新。')
            self._log.error('在线更新失败：' + error)
        elif phase == 'check':
            if value is None:
                settings.set_update_state('当前已是最新版本。')
                return
            settings.set_update_state(f'发现新版本 v{value.version}。')
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle('检查更新')
            box.setTextFormat(QtCore.Qt.PlainText)
            box.setText(f'发现新版本 v{value.version}（{value.size / 1024**2:.1f} MB）')
            box.setInformativeText('下载并校验后，中台将退出并自动安装更新。')
            if value.notes:
                box.setDetailedText(value.notes)
            download = box.addButton('下载并更新', QtWidgets.QMessageBox.AcceptRole)
            cancel = box.addButton('取消', QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.setEscapeButton(cancel)
            box.exec()
            if box.clickedButton() is download and self._can_update():
                settings.set_update_state('正在下载更新包…', busy=True, progress=0)
                self._start_update_task('download', value)
        elif self._can_update():
            try:
                self._launch_update(value)
            except Exception as exc:
                settings.set_update_state('更新包已下载，无法启动更新助手。')
                self._log.error(f'无法开始更新：{exc}。更新包：{value}')
        else:
            settings.set_update_state('更新包已下载，可稍后从本地更新。')
            self._log.info('已验证更新包：' + value)

    def _launch_update(self, package):
        from maintenance.client import start_update
        start_update(paths.project_dir(), package, wait_pid=os.getpid())
        self.close()

    def _on_update_requested(self):
        if not paths.is_frozen() or self._update_task is not None or not self._can_update():
            return
        package, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择 Qz 更新包', '', 'Qz 更新包 (*.zip)')
        if not package:
            return
        try:
            from maintenance.release import inspect_package
            info = inspect_package(package)
            answer = QtWidgets.QMessageBox.question(self, '更新中台',
                '将安装版本 %s。中台会保存设置并退出，更新完成后自动打开。\n是否继续？' % info['version'],
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            self._launch_update(package)
        except Exception as error:
            self._log.error('无法开始更新：' + str(error))

    @QtCore.Slot(str)
    def _add_user_script(self, dcc='maya'):
        extensions = ' '.join('*' + ext for ext in user_scripts.SCRIPT_TYPES[dcc].values())
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, '添加脚本', '', f'{dcc_label(dcc)} 脚本 ({extensions})')
        if not files:
            return
        for filepath in files:
            try:
                user_scripts.add_flat_script(filepath, dcc=dcc)
                self._log.info(f'[用户自定义] 已添加 {os.path.basename(filepath)}。')
            except FileExistsError as exc:
                self._log.warn(f'[用户自定义] 跳过：{exc}')
            except (OSError, ValueError) as exc:
                self._log.error(f'[用户自定义] {os.path.basename(filepath)}：{exc}')
        self._refresh_plugins()

    @QtCore.Slot(str, object)
    def _manage_plugin(self, operation, info):
        # Official plugins are release-owned; only user scripts are editable.
        if info.user_script is not None or operation != 'open':
            return
        try:
            folder = plugin_management.plugin_folder(info)
            if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder))):
                raise RuntimeError('无法打开插件位置')
        except Exception as error:
            self._log.error('[插件管理] ' + str(error))

    @QtCore.Slot(str, object)
    def _manage_user_script(self, operation, info):
        if not info.user_script:
            return
        try:
            if operation != 'open' and (info.key in self._shelf_tasks or info.key in self._script_tasks):
                self._log.warn('[用户自定义] 脚本正在执行或固定按钮，请完成后再修改或移除。')
                return
            if operation in ('pin_shelf', 'pin_toolbar'):
                if info.dcc != ('maya' if operation == 'pin_shelf' else 'max'):
                    raise ValueError('固定操作与脚本宿主不一致')
                task = _ShelfTask(self._clients[info.dcc], info)
                self._shelf_tasks[info.key] = task
                task.signals.finished.connect(self._on_shelf_pinned, QtCore.Qt.QueuedConnection)
                QtCore.QThreadPool.globalInstance().start(task)
                return
            if operation == 'open':
                if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(info.user_script['directory'])):
                    raise RuntimeError('无法打开脚本目录')
                return
            if operation == 'rename':
                name, accepted = QtWidgets.QInputDialog.getText(self, '修改插件名称', '插件名称', text=info.name)
                if not accepted:
                    return
                user_scripts.update_script(info.id, name=name, dcc=info.dcc)
            elif operation == 'entry':
                entry, accepted = QtWidgets.QInputDialog.getText(self, '修改入口',
                    '函数或过程名称（留空执行整个文件）', text=info.user_script['entry'])
                if not accepted:
                    return
                user_scripts.update_script(info.id, entry=entry, dcc=info.dcc)
            elif operation == 'remove':
                current = user_scripts.read_script(info.id, require_file=False, dcc=info.dcc)
                confirmation = QtWidgets.QMessageBox(self)
                confirmation.setWindowTitle('移除用户插件')
                confirmation.setIcon(QtWidgets.QMessageBox.Warning)
                confirmation.setTextFormat(QtCore.Qt.PlainText)
                confirmation.setText(f'确定完整移除“{current["name"]}”？')
                if current.get('flat'):
                    details = '将永久删除此脚本文件：\n' + current['filepath']
                else:
                    details = '将永久删除中台保存的脚本、资源和插件登记。\n\n工具目录：' + current['folder']
                confirmation.setInformativeText(details + '\n导入前的原始文件不会删除。')
                remove_button = confirmation.addButton('移除', QtWidgets.QMessageBox.DestructiveRole)
                cancel_button = confirmation.addButton('取消', QtWidgets.QMessageBox.RejectRole)
                confirmation.setDefaultButton(cancel_button)
                confirmation.setEscapeButton(cancel_button)
                confirmation.exec()
                if confirmation.clickedButton() != remove_button:
                    return
                user_scripts.remove_script(info.id, dcc=info.dcc)
                layout_state.forget_plugin(info.key)
            else:
                return
            self._refresh_plugins()
            self._log.info(f"[用户自定义] 已{'移除' if operation == 'remove' else '更新'} {info.name}。")
        except Exception as exc:
            self._log.error(f"[用户自定义] {exc}")

    @QtCore.Slot(object, object)
    def _on_shelf_pinned(self, key, result):
        self._shelf_tasks.pop(key, None)
        if self._closing:
            return
        if result.get('ok'):
            data = result.get('data') or {}
            target = 'Max 工具栏' if key[0] == 'max' else 'Maya 工具架'
            message = '已%s %s“%s”的“%s”按钮。' % (
                '更新' if data.get('updated') else '固定到', target,
                data.get('toolbar', data.get('shelf', '')), data.get('name', ''))
            self._log.info('[用户自定义] ' + message)
            self._connection_notice(message)
        else:
            self._log.error('[用户自定义] 固定按钮失败：' + result.get('error', '未知错误'))

    # ── Activity 面板切换 ───────────────────
    def _on_panel_changed(self, index: int):
        if index < 0:
            self._side.hide()
            return
        self._side.show()
        self._side.show_index(index)

    # ── 设置面板 ─────────────────────────────
    def _on_settings_opened(self):
        """点开设置 tab 时刷新一次桥接状态（面板是懒更新的）。"""
        for dcc in self._bridges:
            if self._bridge_ok.get(dcc):
                self._versions[dcc] = self._last_ping_version(dcc)
        self._sync_bridge_settings()

    @QtCore.Slot(int)
    def _on_log_limit_changed(self, n: int):
        """日志行数上限即时生效。"""
        self._log.set_max_lines(n)
        layout_state.save_log_limit(self._log.max_lines())
        self._log.info(f"日志上限设为 {self._log.max_lines()} 行")

    def _sync_bridge_settings(self):
        self._side.set_bridge_states(self._bridge_ok, self._host,
            {d: bridge.port for d, bridge in self._bridges.items()},
            self._versions, self._connection_pending)
        self._side.settings.set_bridge_health(
            {dcc: (health.state, health.reason) for dcc, health in self._health.items()})

    def _connection_notice(self, message):
        QtWidgets.QToolTip.showText(self.mapToGlobal(QtCore.QPoint(64, 48)),
                                   message, self, self.rect(), 5000)

    @QtCore.Slot(str)
    def _on_connection_clicked(self, dcc):
        if self._closing or self._connection_pending.get(dcc):
            return
        recovering = dcc == 'max' and self._health[dcc].state == 'error'
        if recovering or not self._bridge_ok.get(dcc):
            if dcc == 'blender':
                code = blender_setup.snippet(self._clients[dcc].port)
            else:
                code = (maya_setup if dcc == 'maya' else max_setup).snippet()
            QtWidgets.QApplication.clipboard().setText(code)
            if recovering:
                message = '已复制 3ds Max 桥接恢复代码，请在 Max 的 Python 侦听器执行，无需先断开。'
            elif dcc == 'blender':
                message = '已复制 Blender 单行启动代码，请在 Blender 的 Python 控制台粘贴并执行。'
            else:
                message = '已复制 %s 单行启动代码，请在该软件的 Python 输入区粘贴并执行。' % dcc_label(dcc)
            self._log.info(message)
            self._connection_notice(message)
            return
        operation = 'disconnect'
        self._connection_epochs[dcc] += 1
        self._ping_inflight[dcc] = False
        self._connection_pending[dcc] = operation
        self._sync_bridge_settings()
        self._bridges[dcc].connection_async(self, operation, self._connection_epochs[dcc], self._connection_id)

    @QtCore.Slot(str, str, int, str)
    def _on_connection_result(self, dcc, operation, epoch, payload):
        if self._closing or epoch != self._connection_epochs.get(dcc):
            return
        self._connection_pending[dcc] = None
        result = json.loads(payload)
        if result.get('ok') and operation == 'disconnect' and result.get('data', {}).get('stopped'):
            self._bridge_ok[dcc] = False
            self._clients[dcc].connected = False
            self._clients[dcc].last_ping_data = {}
            self._versions[dcc] = ''
            self._attached[dcc] = False
            self._health[dcc].disconnect()
            self._connection_announced[dcc] = False
            message = '%s 已断开，桥接端口已关闭' % dcc_label(dcc)
            self._log.info(message)
            self._connection_notice(message)
        elif result.get('ok') and operation != 'disconnect':
            self._attached[dcc] = True
            self._on_ping_result(True, dcc)
            if result.get('data', {}).get('legacy') and dcc not in self._legacy_warned:
                self._legacy_warned.add(dcc)
                self._log.warn('%s 桥接核心为旧版；使用新版断开功能前，请保存场景并重启该软件。' % dcc_label(dcc))
        else:
            error = result.get('error', '桥接没有确认端口关闭')
            if 'unknown action' in error:
                error = '桥接核心为旧版，请保存场景并重启 %s 后再连接' % dcc_label(dcc)
            self._log.warn('%s %s失败：%s' % (dcc_label(dcc), '断开' if operation == 'disconnect' else '连接', error))
        self._sync_bridge_settings()
        self._start_bridge_timer(dcc)

    # ── 插件从树/搜索被激活 ─────────────────
    def _on_plugin_activated(self, info):
        if info.user_script is None:
            self._canvas.open_plugin(info)
            return
        if info.key in self._script_tasks or self._closing:
            return
        client = self._clients[info.dcc]
        if not client.connected:
            self._log.warn(f'[{info.name}] {dcc_label(info.dcc)} 未连接，请先在中台连接。')
            return
        task = _ScriptTask(client, info)
        self._script_tasks[info.key] = task
        task.signals.finished.connect(self._on_script_finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object, object)
    def _on_script_finished(self, key, result):
        task = self._script_tasks.pop(key, None)
        if self._closing or task is None:
            return
        prefix = f"[{task.script['name']}] "
        data = result.get('data') or {}
        for line in data.get('output', '').splitlines():
            self._log.info(prefix + line)
        if data.get('truncated'):
            self._log.info(prefix + '脚本输出较长，日志已截取前 32 KB。')
        if result.get('ok'):
            self._log.info(prefix + f"执行完成，耗时 {data.get('elapsed_seconds', 0):.3f} 秒。")
        else:
            self._log.error(prefix + result.get('error', '脚本执行失败'))

    # ── 节点事件 ───────────────────────────
    def _plugin_label(self, key) -> str:
        """复合键 -> 可读文案，如 "3dsmax/create_box"。"""
        if not isinstance(key, (tuple, list)) or len(key) != 2:
            return str(key)
        info = self._canvas._info_by_key.get(tuple(key))
        name = info.name if info is not None else key[1]
        return "%s/%s" % (key[0], name)

    def _on_node_opened(self, plugin_key):
        self._log.info(f"打开插件 {self._plugin_label(plugin_key)}")
        self._refresh_side()

    def _on_node_removed(self, plugin_key):
        self._log.info(f"移除插件 {self._plugin_label(plugin_key)}")
        self._refresh_side()

    def _refresh_side(self):
        self._side.set_open_keys(self._canvas.open_ids())

    # ── 桥接（双 DCC 并列）─────────────────
    def _kick_ping(self, dcc: str):
        if self._closing or self._connection_pending.get(dcc) or self._ping_inflight.get(dcc):
            return
        self._ping_inflight[dcc] = True
        # 不再从 bridge 往 client 抄 host/port —— 两者是同一个对象了
        self._bridges[dcc].ping_async(self, slot="_on_health_epoch",
                                     dcc=dcc, epoch=self._connection_epochs[dcc])

    @QtCore.Slot(str, str, int)
    def _on_health_epoch(self, payload, dcc, epoch):
        if self._closing or epoch != self._connection_epochs.get(dcc):
            return
        self._ping_inflight[dcc] = False
        if not self._connection_pending.get(dcc):
            self._on_health_result(json.loads(payload), dcc)

    @QtCore.Slot(bool, str, int)
    def _on_ping_epoch(self, ok, dcc, epoch):
        if self._closing or epoch != self._connection_epochs.get(dcc):
            return
        self._ping_inflight[dcc] = False
        if not self._connection_pending.get(dcc):
            self._on_ping_result(ok, dcc)

    # ★ 签名必须和 async_bridge 里的 Q_ARG 列表逐个对齐 ★
    #   async_bridge 投的是 (bool, QString) 两个参数，
    #   这里只写 @Slot(bool) 会让 invokeMethod 因签名不匹配**静默失败**，
    #   回调永不触发 → `_bridge_ok` 永远是 False → 两个 DCC 都显示"未连接"，
    #   而 socket 其实完全正常（这个坑极难从表象看出来）。
    @QtCore.Slot(bool, str)
    def _on_ping_result(self, ok: bool, dcc: str = "maya"):
        # Backward compatible entry for old callbacks and successful handshakes.
        self._on_health_result(dict(state='ready' if ok else 'offline',
                                    reachable=bool(ok)), dcc)

    def _on_health_result(self, report, dcc):
        if self._closing:
            return
        label = dcc_label(dcc)
        was_ok = self._bridge_ok.get(dcc, False)
        health = self._health[dcc]
        previous = health.state
        state = health.update(report)
        self._bridge_ok[dcc] = health.connected
        # ★ 同步给 client —— 插件卡片靠 client.connected 决定
        #   「未连接时按钮置灰」（Reload 卡片要）。
        #   卡片拿不到 MainWindow 的信号，只能问 client 要状态。
        client = self._clients.get(dcc)
        if client is not None:
            client.connected = health.connected

        if state in ('ready', 'busy', 'waiting', 'unresponsive', 'error'):
            self._versions[dcc] = self._last_ping_version(dcc)
            # 断开过才提示"已恢复"，避免每次心跳都刷日志
            if not self._connection_announced[dcc] and state == 'ready':
                version = self._versions[dcc]
                message = '%s%s 已连接' % (label, ' ' + version if version and version != 'unknown' else '')
                self._log.info(message)
                self._connection_notice(message)
                self._connection_announced[dcc] = True
            elif previous in ('waiting', 'unresponsive', 'error') and state == 'ready':
                self._log.info('%s 已恢复响应' % label)
            if state == 'unresponsive' and previous != state:
                self._log.warn('%s 持续无响应，将继续检测' % label)
            elif state == 'error' and previous != state:
                if dcc == 'max':
                    self._attached[dcc] = False
                self._log.warn('%s 桥接异常：%s' % (label, health.reason))
            if state == 'ready' and not self._attached[dcc] and not self._connection_pending[dcc]:
                self._connection_pending[dcc] = 'attach'
                self._bridges[dcc].connection_async(self, 'attach', self._connection_epochs[dcc], self._connection_id)
        else:
            self._versions[dcc] = ""
            self._attached[dcc] = False
            self._connection_announced[dcc] = False
            # 之前是连着的 → 说明 DCC 被关了，值得单独记一条
            if was_ok:
                self._log.warn("%s 桥接端口不可连接，将继续检测" % label)
            elif not self._bridge_timers[dcc].isActive():
                self._log.warn("%s 未连接，将持续尝试" % label)

        # 连上/断开都用同一个 timer 继续探测
        self._start_bridge_timer(dcc)

        # 设置面板的桥接状态灯实时跟随
        self._sync_bridge_settings()

    def _start_bridge_timer(self, dcc: str = None):
        """启动定期探测。连上（心跳）与断开（重连）都用它，间隔一致。"""
        targets = [dcc] if dcc else list(self._bridge_timers.keys())
        seconds = int(self._config.get("bridge", {}).get("interval", 10))
        for d in targets:
            timer = self._bridge_timers.get(d)
            if timer is None or timer.isActive():
                continue
            if seconds > 0:
                timer.start(seconds * 1000)
            QtCore.QTimer.singleShot(300, lambda x=d: self._kick_ping(x))

    def _last_ping_version(self, dcc: str = "maya") -> str:
        """读取后台心跳缓存；UI 线程不额外等待 DCC。"""
        cached = self._versions.get(dcc, "")
        data = getattr(self._clients[dcc], "last_ping_data", {})
        return str(data.get("maya_version", "") or data.get("max_version", "")
                   or data.get("version", "") or cached)

    # ── Layout 持久化 ──────────────────────
    def _restore_layout(self):
        if self._closing:
            return
        items, locked = layout_state.load_open()
        self._canvas.set_locked(locked)
        for key, x, y in items:
            # ★ 按复合键 (dcc, id) 还原 ——
            #   maya 的 create_box 和 max 的 create_box 各自还原到自己的位置
            info = self._plugins_by_key.get(key)
            if info and info.user_script is None:
                self._canvas.open_plugin(info, x=int(x), y=int(y))
        self._refresh_side()
        view = layout_state.load_canvas_view()
        if view is not None:
            self._canvas.restore_view(view)
        self._restoring = False

    def closeEvent(self, event):
        # 先停重连 + 等在跑的 ping 收尾，避免 QRunnable 回调打到已销毁的窗口
        self._closing = True
        for timer in self._bridge_timers.values():
            if timer.isActive():
                timer.stop()
        # 先唤醒最长可等待 305 秒的插件请求，再等线程池收尾。
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                close()
        for bridge in self._bridges.values():
            bridge.shutdown(wait_ms=1500)

        # 布局
        ids = self._canvas.open_ids()
        positions = self._canvas.get_node_positions()
        items = [(pid, *positions.get(pid, (0, 0))) for pid in ids]

        # 日志区高度（拖没了就存 0，下次也是隐藏）
        # ★ 2026-10-19 改：专注模式不再用"旧值兜底"。
        #   旧实现担心的是"程序把日志收起来 → 存进配置 → 下次日志永久消失"，
        #   那是 hide() 时代的顾虑。
        #   现在日志是**收尺寸**（停在窗口底边、手柄可拖），
        #   用户在专注模式里拖到 0 是**故意的**（2026-10-19 用户拍板：
        #   "拖到 0 后关程序，下次启动日志要保持隐藏"）。
        #   所以专注模式下也照实存 sizes[1]。
        log_h = self._log_h
        sizes = self._right_split.sizes()
        if len(sizes) >= 2:
            log_h = int(sizes[1])

        try:
            layout_state.save_open(
                items,
                locked=self._canvas.is_locked(),
                log_height=log_h,
                window=self._current_window_geometry(),
                collapsed_groups=self._side._tree.collapsed_groups(),
                canvas_view=self._canvas.view_state(),
                sidebar=dict(
                    visible=(self._pre_focus_side_visible if self._focus_mode
                             else not self._side.isHidden() and self._side.width() > 0),
                    page=self._side.current_index(),
                ),
            )
        except Exception as e:
            print(f"[layout] save failed: {e}")
        super().closeEvent(event)


def run():
    """提供 main.py 调用：启 Qt app + MainWindow。"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if "PYTEST_CURRENT_TEST" not in os.environ and len(app.arguments()) <= 1:
        sys.exit(app.exec())
    return app, win
