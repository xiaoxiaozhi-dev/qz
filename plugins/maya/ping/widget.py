"""
Maya · 连接测试

调一次 ping，顺带显示 Maya 版本与连接状态。

排查 Maya 桥接问题可以从这里开始。

⚠️ 与 plugins/max/ping/ **不是同一个文件** —— 按项目约定，
   Maya 和 Max 的 UI 各写各的。
   差异：Maya 的 ping 返回 maya_version（cmds.about(version=True)），
   Max 的 ping 返回执行线程名（必须是 MainThread，
   那是 Max 端「socket 线程 → 队列 → 主线程 worker」链路正确的铁证）。
"""

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget, set_role


class _PingWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "连接测试"

        btn = QtWidgets.QPushButton("Ping Maya")
        btn.clicked.connect(self._ping)
        self._btn = btn

        self._result = QtWidgets.QLabel("尚未测试")
        set_role(self._result, "muted")
        self._result.setWordWrap(True)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(btn)
        lay.addWidget(self._result)
        lay.addStretch(1)

    def _ping(self):
        try:
            res = self.client.call("ping", timeout=5)
        except Exception as e:
            self._result.setText("✗ 无法连接 Maya")
            set_role(self._result, "error")
            self.log.error("Maya ping 失败：%s" % e)
            return

        if not res.get("ok"):
            self._result.setText("✗ bridge 返回错误")
            set_role(self._result, "error")
            self.log.error("Maya ping 返回错误：%s" % res.get("error"))
            return

        data = res.get("data", {})
        version = data.get("maya_version", "?")
        self._result.setText("✓ 已连接\nMaya 版本：%s" % version)
        set_role(self._result, "success")
        self.log.info("Maya ping → 版本 %s" % version)


def create_widget(client, log, parent=None):
    return _PingWidget(client, log, parent)
