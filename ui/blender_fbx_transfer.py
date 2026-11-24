"""Coordinate Maya and Blender FBX exchange without blocking the desktop UI."""
from functools import partial
from pathlib import Path
import tempfile

from PySide6 import QtCore, QtWidgets

import paths
from ui.fbx_transfer import _call
from ui.plugin_widget import BasePluginWidget

LABELS = {'maya': 'Maya', 'blender': 'Blender'}


def _preflight(client, host, direction):
    action = 'fbx_blender_preflight' if host == 'blender' else 'fbx_maya_blender_preflight'
    params = dict(direction=direction) if host == 'blender' else {}
    try:
        ready = _call(client, action, timeout=30, connect_timeout=2, **params)
    except RuntimeError as exc:
        if 'unknown action' not in str(exc):
            raise
        _call(client, 'reload_actions', timeout=30, connect_timeout=2)
        ready = _call(client, action, timeout=30, connect_timeout=2, **params)
    if not ready.get('ready'):
        raise RuntimeError('FBX 动作未就绪')


def run_transfer(source, source_client, target_client):
    target = 'blender' if source == 'maya' else 'maya'
    folder = None
    stage = '检查连接'
    try:
        for host, client in ((source, source_client), (target, target_client)):
            if not getattr(client, 'connected', False):
                raise RuntimeError(LABELS[host] + ' 未连接，请先在中台连接')
        stage = LABELS[target] + ' 接收预检'
        _preflight(target_client, target, 'import')
        stage = LABELS[source] + ' 导出预检'
        _preflight(source_client, source, 'export')
        stage = '准备临时文件'
        root = Path(paths.local_data_dir('maya_blender_fbx'))
        root.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=source + '-', dir=root))
        filepath = folder / 'selection.fbx'
        stage = LABELS[source] + ' 导出'
        exported = _call(source_client, 'fbx_export_' + target, filepath=str(filepath),
                         timeout=605, connect_timeout=2)
        if not filepath.is_file() or not filepath.stat().st_size:
            raise RuntimeError('没有生成有效 FBX 文件')
        stage = LABELS[target] + ' 导入'
        received = _call(target_client, 'fbx_receive_' + source, filepath=str(filepath),
                         timeout=605, connect_timeout=2)
    except Exception as exc:
        result = dict(ok=False, error=f'{stage}：{exc}')
        if folder is not None:
            result['retained_path'] = str(folder)
        if stage.endswith('导入'):
            result['error'] += '。重新发送前，请检查目标场景是否已导入部分物体。'
        return result
    warnings = list(exported.get('warnings', [])) + list(received.get('warnings', []))
    result = dict(ok=True, data=received, selected_count=exported.get('count', 0), warnings=warnings)
    try:
        filepath.unlink()
        folder.rmdir()
    except OSError as exc:
        warnings.append(f'临时文件清理失败：{exc}')
        result['retained_path'] = str(folder)
    return result


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _TransferTask(QtCore.QRunnable):
    def __init__(self, source, client, target_client):
        super().__init__()
        self.transfer = partial(run_transfer, source, client, target_client)
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(self.transfer())


class FbxSendWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, source, parent=None):
        super().__init__(parent)
        self.client, self.log, self.source = client, log, source
        self.target = 'blender' if source == 'maya' else 'maya'
        self.action_label = f'发送到 {LABELS[self.target]}'
        self._task = None
        self._clients = {}
        self.button = QtWidgets.QPushButton(f'选中物体{self.action_label}')
        self.button.setToolTip('将选中物体及关联骨架和蒙皮导入目标当前场景，不发送动画。')
        self.button.clicked.connect(self._start)
        group = QtWidgets.QGroupBox()
        QtWidgets.QHBoxLayout(group).addWidget(self.button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)

    def set_dcc_clients(self, clients):
        self._clients = clients

    def _start(self):
        if self._task is not None:
            return
        task = self._task = _TransferTask(self.source, self.client, self._clients.get(self.target))
        self.button.setEnabled(False)
        self.log.info(f'[{self.action_label}] 正在传输 FBX…')
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def format_log_result(self, action, result):
        if result.get('ok'):
            data = result['data']
            yield 'info', f'发送完成：源端选中 {result["selected_count"]} 个物体，目标新增 {data["objects"]} 个场景物体。'
            if data.get('namespace'):
                yield 'info', '命名空间：' + data['namespace']
            for message in result['warnings']:
                yield 'warn', message
        else:
            yield 'error', result['error']
        if result.get('retained_path'):
            yield 'warn', '交换目录保留在：' + result['retained_path']

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        self.button.setEnabled(True)
        self.log_result('fbx_send', result)
