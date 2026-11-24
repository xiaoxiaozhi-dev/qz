"""
3ds Max · 连接测试

调一次 ping，顺带显示 action 执行线程 —— 必须是 MainThread，
这是 Max 端「socket 线程 → 队列 → 主线程 worker」链路正确的铁证。

排查 Max 桥接问题可以从这里开始：连不上 → 看设置面板；
连上了但线程不对 → 说明 timer 没在主线程跑。
"""

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget, set_role


class _PingWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "连接测试"

        btn = QtWidgets.QPushButton("Ping 3ds Max")
        btn.clicked.connect(self._ping)

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
            self._result.setText("✗ 无法连接 3ds Max")
            set_role(self._result, "error")
            self.log.error("3ds Max ping 失败：%s" % e)
            return

        if not res.get("ok"):
            self._result.setText("✗ bridge 返回错误")
            set_role(self._result, "error")
            self.log.error("3ds Max ping 返回错误：%s" % res.get("error"))
            return

        data = res.get("data", {})
        thread = data.get("thread", "?")
        is_main = data.get("is_main")
        mark = "✓" if is_main else "⚠"
        self._result.setText("%s 已连接\n执行线程：%s" % (mark, thread))
        set_role(self._result, "success" if is_main else "warning")
        self.log.info("3ds Max ping → 线程 %s（is_main=%s）" % (thread, is_main))


def create_widget(client, log, parent=None):
    return _PingWidget(client, log, parent)
