"""Max binding utilities; use the injected bridge, no extra connections."""
import time

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget

BUTTONS = (
    ('reset_xform', '重置模型变换'),
    ('bind_pose', '骨骼回到绑定姿势'),
    ('apply_last_pose', '应用上次姿势'),
    ('prune_bones', '移除无权重骨骼'),
    ('remove_influences', '移除影响（先骨骼，后模型）'),
    ('add_influences', '添加影响（先骨骼，后模型）'),
    ('transfer_influences', '补充影响列表（先 A，后 B）'),
)
LABELS = dict(BUTTONS)


def run_operation(client, operation):
    started = time.perf_counter()
    try:
        if client is None or not getattr(client, 'connected', False):
            raise RuntimeError('Max 未连接，请先在中台连接 Max')
        def call(action, **kwargs):
            response = client.call(action, timeout=305, connect_timeout=2, execution_timeout=300, **kwargs)
            if not response.get('ok'):
                raise RuntimeError(response.get('error') or 'Max 操作失败')
            return response.get('data') or {}
        def execute():
            return call('rigging_toolbox_max_execute', operation=operation, expected_version=10)
        try:
            ready = execute()
        except RuntimeError as exc:
            if str(exc).strip() != 'unknown action: rigging_toolbox_max_execute':
                raise
            ready = {}
        if ready.get('version') != 10 and 'result' in ready:
            raise RuntimeError('动作返回版本不一致；操作可能已执行，请检查 Max 完成情况后再操作')
        if ready.get('version') != 10:
            call('reload_actions')
            ready = execute()
        if ready.get('version') != 10 or 'result' not in ready:
            raise RuntimeError('绑定工具箱动作未更新，请核对 Max 动作加载目录')
        response = dict(ok=True, data=ready['result'])
    except Exception as exc:
        message = str(exc)
        if any(token in message.lower() for token in ('timeout', 'timed out', '超时', '断开', '无响应', 'empty response')):
            message += '；Max 操作可能仍在执行，请检查完成情况后再操作'
        response = dict(ok=False, error=message)
    response.update(operation=operation, elapsed_seconds=round(time.perf_counter()-started, 3))
    return response


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, client, operation):
        super().__init__()
        self.client, self.operation = client, operation
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(run_operation(self.client, self.operation))


class RiggingToolboxWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = '绑定工具箱'
        self._task = None
        self.buttons = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        for operation, label in BUTTONS:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda checked=False, operation=operation: self._start(operation))
            layout.addWidget(button)
            self.buttons[operation] = button

    def _start(self, operation):
        if self._task is not None:
            return
        task = _Task(self.client, operation)
        self._task = task
        for button in self.buttons.values():
            button.setEnabled(False)
        self.log.info('[绑定工具箱] 开始' + LABELS[operation] + '…')
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _finished(self, response):
        self._task = None
        for button in self.buttons.values():
            button.setEnabled(True)
        self.log_result('rigging_toolbox_max', response)

    def format_log_result(self, action, response):
        if not response.get('ok'):
            messages = [('error', response.get('error', '操作失败'))]
        else:
            data = response['data']
            messages = ([('error', LABELS[response['operation']] + '失败。')] if data.get('failed') else
                        [('info', '{}完成：处理 {} 个模型。'.format(LABELS[response['operation']], data.get('count', 0)))])
            for report in data.get('reports', []):
                if 'bones' in report:
                    if report.get('pose') == 'last':
                        message = '{}：已应用上次姿势{}，共 {} 个骨骼。'.format(
                            report['mesh'], '（仅局部旋转）' if report.get('rotation_only') else '', report['bones'])
                    elif not report.get('changed', True):
                        message = report['mesh'] + '：已在绑定姿势，未更新姿势缓存。'
                    else:
                        message = '{}：已恢复 {} 个骨骼的绑定姿势。'.format(report['mesh'], report['bones'])
                        if report.get('pose_cached'):
                            message += '执行前 Pose 已缓存在 Max 内存中，可跨文件应用。'
                elif 'added' in report:
                    message = '{}：新增 {} 个影响，移除 {} 个影响。'.format(report['mesh'], report['added'], report['removed'])
                else:
                    message = report['mesh'] + '：已重置变换。'
                messages.append(('info', message))
            messages.extend(('error' if data.get('failed') else 'warn', '{}：{}'.format(item['mesh'], item['error']))
                            for item in data.get('errors', []))
            messages.extend(('warn', message) for message in data.get('warnings', []))
        messages.append(('info', '耗时 {:.3f} 秒。'.format(response.get('elapsed_seconds', 0))))
        return messages


def create_widget(client, log, parent=None):
    return RiggingToolboxWidget(client, log, parent)
