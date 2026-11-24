"""
3ds Max · 重载动作库

改了 max_actions/ 下的脚本后点它，不用重启 Max。

⚠️ 与 plugins/maya/reload/ **不是同一个文件** —— 按项目约定，
   Maya 和 Max 的 UI 各写各的。

【为什么这里要处理"未连接"】
   Reload 是画布上的卡片，卡片本身要靠 client 才能发请求 ——
   DCC 没连上时按钮点了也没用。所以未连接时直接置灰并说明原因，
   免得用户点了没反应还以为程序卡了。
   连接状态由 client.connected 提供（MainWindow 的 ping 回调维护）。

【关于"reload_actions 本身是 action"造成的循环依赖】
   reload_actions 自己也是一个 action，要先进 action 表才能被调用。
   所以 DCC 端**从没加载过**它的时候（刚装好、还没跑过一次启动代码），
   调用会返回 "unknown action: reload_actions"。
   这时唯一的出路是【重跑一次启动代码】让它重扫一遍 ——
   所以这里把这条指引显示出来，而不是只报一句"失败"。
"""

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget, set_role


# 循环依赖的识别串 —— DCC 端 dispatch 找不到 action 时的原文
_UNKNOWN_ACTION = "unknown action: reload_actions"


class _ReloadWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "重载动作库"

        btn = QtWidgets.QPushButton("重新扫描 max_actions/")
        btn.clicked.connect(self._reload)
        self._btn = btn

        self._status = QtWidgets.QLabel("")
        set_role(self._status, "muted")
        self._status.setWordWrap(True)

        hint = QtWidgets.QLabel(
            "新增或修改 max_actions/ 下的脚本后点它。\n"
            "修改本插件的界面代码需要重启中台。")
        set_role(hint, "hint")
        hint.setWordWrap(True)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(btn)
        lay.addWidget(self._status)
        lay.addWidget(hint)
        lay.addStretch(1)

        # 连接状态轮询：client.connected 由 MainWindow 的 ping 回调维护，
        # 插件卡片拿不到信号，只能定时问一次。
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_state)
        self._timer.start(1000)
        self._refresh_state()

    def _refresh_state(self):
        """按连接状态更新按钮与提示文字。"""
        ok = bool(getattr(self.client, "connected", False))
        self._btn.setEnabled(ok)
        if not ok:
            self._status.setText("未连接到 3ds Max")
            set_role(self._status, "warning")
        elif not self._status.text():
            self._status.setText("已连接 3ds Max")
            set_role(self._status, "muted")

    def _reload(self):
        if not getattr(self.client, "connected", False):
            self._status.setText("未连接到 3ds Max")
            set_role(self._status, "warning")
            self.log.warn("3ds Max 未连接，无法 Reload")
            return

        # ★ 不用 log_run —— 它把错误吞进日志了，
        #   而这里需要**识别** "unknown action" 并给出可操作的指引。
        try:
            res = self.client.call("reload_actions", timeout=15)
        except Exception as e:
            self._status.setText("✗ Reload 失败：%s" % e)
            set_role(self._status, "error")
            self.log.error("3ds Max Reload 失败：%s" % e)
            return

        if res.get("ok"):
            names = res.get("data") or []
            if isinstance(names, list):
                self._status.setText("✓ 已加载 %d 个动作" % len(names))
                self.log.info("Reload 完成，3ds Max 端共 %d 个动作：%s"
                             % (len(names), ", ".join(names)))
            else:
                self._status.setText("✓ 已加载")
                self.log.info("Reload 完成：%s" % names)
            set_role(self._status, "success")
            return

        err = str(res.get("error", ""))
        if _UNKNOWN_ACTION in err:
            # 循环依赖：reload 本身还没被加载，唯一出路是重跑启动代码
            self._status.setText(
                "3ds Max 端还没加载过 Reload ——\n"
                "请用设置面板的「临时启动」复制代码，\n"
                "到 3ds Max 脚本编辑器粘贴运行一次。")
            set_role(self._status, "warning")
            self.log.warn(
                "3ds Max 端还没加载过 Reload —— 请用「临时启动」复制代码，"
                "到 3ds Max 脚本编辑器粘贴运行一次，之后 Reload 就能一直用了")
        else:
            self._status.setText("✗ Reload 失败")
            set_role(self._status, "error")
            self.log.error("3ds Max Reload 失败：%s" % err)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


def create_widget(client, log, parent=None):
    return _ReloadWidget(client, log, parent)
