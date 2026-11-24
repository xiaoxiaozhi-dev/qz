"""The common card for imported DCC tools. User UI stays inside its host."""
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from .plugin_widget import BasePluginWidget, set_role
from .user_scripts import run_script


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, client, script):
        super().__init__()
        self.client, self.script = client, dict(script)
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(run_script(self.client, self.script))


class UserScriptWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, script, parent=None):
        super().__init__(parent)
        self.client, self.log, self._task = client, log, None
        self.detail = QtWidgets.QLabel()
        set_role(self.detail, 'hint')
        self.run_button = QtWidgets.QPushButton('运行脚本')
        self.run_button.clicked.connect(self._start)
        self.set_script(script)
        group = QtWidgets.QGroupBox('')
        body = QtWidgets.QVBoxLayout(group)
        body.addWidget(self.detail)
        body.addWidget(self.run_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)

    def set_script(self, script):
        self.script = dict(script)
        self.action_label = script['name']
        text = {'python': 'Python', 'mel': 'MEL', 'maxscript': 'MAXScript'}[script['language']] + ' · ' + Path(script['filepath']).name
        if script['entry']:
            text += ' · ' + script['entry']
        if script.get('missing'):
            text += '（文件缺失）'
        self.detail.setText(text)

    def _start(self):
        if self._task is not None:
            return
        self.run_button.setEnabled(False)
        task = _Task(self.client, self.script)
        self._task = task
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        self.run_button.setEnabled(True)
        self.log_result('user_script', result)

    def format_log_result(self, action, result):
        data = result.get('data') or {}
        messages = []
        for line in data.get('output', '').splitlines():
            messages.append(('info', line))
        if data.get('truncated'):
            messages.append(('info', '脚本输出较长，日志已截取前 32 KB。'))
        if result.get('ok'):
            messages.append(('info', f"执行完成，耗时 {data.get('elapsed_seconds', 0):.3f} 秒。"))
        else:
            messages.append(('error', result.get('error', '脚本执行失败')))
        return messages


def create_widget(client, log, parent=None, *, script):
    return UserScriptWidget(client, log, script, parent)
