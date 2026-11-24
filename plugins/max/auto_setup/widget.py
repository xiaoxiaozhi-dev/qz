"""
3ds Max · 自动启动安装

往 Max 的 startup 目录丢一个 .ms，让以后每次启动 Max 自动起 bridge。

⚠️ 这是 Max 侧独立的插件，与 plugins/maya/auto_setup/ 无任何共享。
   两边逻辑同构但 UI 各写各的（项目约定），且 Max 版多两件事：
     1. startup 目录**必须问 Max 要**（getDir #userStartupScripts），
        绝不靠目录结构推导 —— 我推导过三次，全错。
     2. 多一个「卸载所有版本」，它扫目录即可，不需要 Max 连接。

【三个按钮的连接要求不一样，这是有意的】
  安装 / 卸载当前版本 → 需要 Max 已连接（得问路径）
  卸载所有版本       → 不需要（扫目录，它不针对「当前这个实例」）

【不在 widget 里预判连接状态】
  未连接时点「安装」，统一走 log_run 让 action 自己报错，
  这样 widget 和其他 Max 插件完全同构，不引入额外的连接状态判断。
"""

from PySide6 import QtWidgets

from ui import max_setup
from ui.plugin_widget import BasePluginWidget, set_role


class _AutoSetupWidget(QtWidgets.QWidget, BasePluginWidget):
    """安装 / 卸载当前版本 / 卸载所有版本。

    ⚠️ 用户明确要求**不做安装卸载切换**，每个动作一个独立按钮。
    """

    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "自动启动"

        install = QtWidgets.QPushButton("安装（当前版本）")
        install.clicked.connect(self._install)

        remove_cur = QtWidgets.QPushButton("卸载（当前版本）")
        remove_cur.clicked.connect(self._remove_current)

        remove_all = QtWidgets.QPushButton("卸载（所有版本）")
        remove_all.clicked.connect(self._remove_all)

        # ⚠️ 按钮不加 tooltip（用户要求：按钮名已经说清作用了）。
        #   但"安装 / 卸载当前版本需要先连接 Max"这条不能丢 ——
        #   它是这两个按钮失败的头号原因，所以在下方常驻显示一行。
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        set_role(self._status, "muted")

        self._hint = QtWidgets.QLabel(
            "前两个按钮需要先连接 3ds Max（它得自己报出路径）")
        self._hint.setWordWrap(True)
        set_role(self._hint, "hint")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(install)
        lay.addWidget(remove_cur)
        lay.addWidget(remove_all)
        lay.addWidget(self._status)
        lay.addWidget(self._hint)
        lay.addStretch(1)

        self._refresh()

    # ── 状态 ────────────────────────────────────────
    def _refresh(self):
        """显示本地扫描到的目录数量（不需要 Max 连接）。"""
        dirs = max_setup.scan_startup_dirs()
        if not dirs:
            self._status.setText("未扫描到 3ds Max startup 目录。")
            return
        hit = [d for d in dirs if max_setup.has_script(max_setup.script_path(d))]
        if hit:
            self._status.setText(
                "已安装 %d / %d 个目录：" % (len(hit), len(dirs)))
            for d in hit:
                self._status.setText(self._status.text() + "\n" + d)
        else:
            self._status.setText(
                "未安装。扫描到 %d 个 startup 目录。" % len(dirs))

    def _ask_dir(self):
        """问 Max 要 startup 目录。返回路径或 None（失败时已写日志）。"""
        return self.log_run("get_startup_dir")

    # ── 三个动作 ────────────────────────────────────
    def _install(self):
        startup_dir = self._ask_dir()
        if not startup_dir:
            return
        path = max_setup.script_path(startup_dir)
        msg, state = max_setup.write_script(path)
        self.log.info("[自动启动] %s" % msg)
        self._refresh()

    def _remove_current(self):
        startup_dir = self._ask_dir()
        if not startup_dir:
            return
        path = max_setup.script_path(startup_dir)
        msg, state = max_setup.remove_script(path)
        self.log.info("[自动启动] %s" % msg)
        self._refresh()

    def _remove_all(self):
        msg, details, state = max_setup.remove_all_scripts()
        self.log.info("[自动启动] %s" % msg)
        for d in details:
            self.log.info("    [%s] %s" % (d["state"], d["path"]))
        self._refresh()


def create_widget(client, log, parent=None):
    return _AutoSetupWidget(client, log, parent)
