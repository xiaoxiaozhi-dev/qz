"""
plugin_widget — 给所有插件 widget 提供一些 helper。
create_widget(client, log, parent=None) 拿到 client + log 之后，
可以直接继承 BasePluginWidget，或在 widget 内调 PluginApi 静态方法。
"""

from typing import Optional

from PySide6 import QtCore, QtWidgets

from .app_style import load_stylesheet, set_style_property


def apply_plugin_style(widget):
    """由 PluginNode 自动调用；公共 QSS 仅作用于插件内容，不影响标题和侧栏。"""
    widget.setProperty("qzPluginContent", True)
    widget.setStyleSheet(load_stylesheet("controls.qss", "plugin.qss"))


def set_role(widget, role: str):
    """设置提示/成功/警告/错误等文字角色；颜色由公共 QSS 决定。"""
    set_style_property(widget, "qzRole", role)


class BasePluginWidget:
    """插件 widget 建议继承这个 mixin；它要求 widget 在 __init__ 里设 self.client 和 self.log。"""

    client = None
    log = None
    action_label: str = ""        # 例如 "骨骼镜像"，用于日志前缀

    def log_run(self, action: str, **params) -> Optional[dict]:
        """调 action + 把结果写日志。返回 data 或 None。"""
        try:
            res = self.client.call(action, **params)
            return self.log_result(action, res)
        except Exception as e:
            prefix = f"[{self.action_label}] " if self.action_label else ""
            self.log.error(f"{prefix}{action} 异常：{e}")
            return None


    def log_result(self, action: str, res: dict) -> Optional[dict]:
        """同步调用或后台结果统一从 UI 线程记录；不在工作线程操作日志控件。"""
        prefix = f"[{self.action_label}] " if self.action_label else ""
        formatter = getattr(self, "format_log_result", None)
        if callable(formatter):
            for level, message in formatter(action, res):
                writer = getattr(self.log, level, self.log.info)
                writer(f"{prefix}{message}")
            return res.get("data") if res.get("ok") else None
        if res.get("ok"):
            data = res.get("data")
            self.log.info(f"{prefix}{action} → {data}")
            return data
        self.log.error(f"{prefix}{action} 失败：{res.get('error', '')}")
        return None


class _ActionSignals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _ActionTask(QtCore.QRunnable):
    def __init__(self, client, action, timeout):
        super().__init__()
        self.client, self.action, self.timeout = client, action, timeout
        self.signals = _ActionSignals()

    def run(self):
        try:
            result = self.client.call(self.action, timeout=self.timeout, connect_timeout=1)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self.signals.finished.emit(result)


class AsyncActionWidget(QtWidgets.QWidget, BasePluginWidget):
    """A single bridge action button with asynchronous logging."""

    def __init__(self, client, log, action, label, parent=None, *, timeout=3):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action, self.action_label, self.timeout = action, label, timeout
        self._task = None
        self.button = QtWidgets.QPushButton(label)
        self.button.clicked.connect(self._start)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.button)

    def _start(self):
        self.button.setEnabled(False)
        task = self._task = _ActionTask(self.client, self.action, self.timeout)
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        self.button.setEnabled(True)
        self.log_result(self.action, result)
