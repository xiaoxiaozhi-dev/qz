"""Maya card for opening, importing, and updating positions in Blender."""
from PySide6 import QtCore, QtGui, QtWidgets

import paths
from ui.maya_to_blender import format_messages, run_transfer
from ui.plugin_widget import BasePluginWidget


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, maya, blender, mode, animation, confirmed):
        super().__init__()
        self.args = maya, blender, mode, animation, confirmed
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(run_transfer(*self.args))


class FbxToBlenderWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = '选中物体发送到 Blender'
        self._dcc_clients, self._task = {}, None
        self._store = QtCore.QSettings('QzTools', 'MayaToBlender')
        self.open_button = QtWidgets.QPushButton('打开到 Blender')
        self.import_button = QtWidgets.QPushButton('导入到 Blender')
        self.vertices_button = QtWidgets.QPushButton('发送顶点位置到 Blender')
        self.settings_button = QtWidgets.QPushButton()
        self.settings_button.setIcon(QtGui.QIcon(paths.resource('settings.svg')))
        size = self.open_button.fontMetrics().height()
        self.settings_button.setIconSize(QtCore.QSize(size, size))
        self.settings_button.setAccessibleName('发送设置')
        self.settings_menu = QtWidgets.QMenu(self.settings_button)
        self.animation_action = self.settings_menu.addAction('导出动画')
        self.animation_action.setCheckable(True)
        self.animation_action.setChecked(self._store.value('animation', False, type=bool))
        self.animation_action.toggled.connect(lambda value: self._store.setValue('animation', value))
        # Popup in screen coordinates so it also works within a canvas proxy.
        self.settings_button.clicked.connect(
            lambda: self.settings_menu.popup(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft())))

        group = QtWidgets.QGroupBox()
        row = QtWidgets.QHBoxLayout(group)
        row.addWidget(self.open_button, 1)
        row.addWidget(self.import_button, 1)
        row.addWidget(self.settings_button)

        vertex_group = QtWidgets.QGroupBox()
        vertex_layout = QtWidgets.QHBoxLayout(vertex_group)
        vertex_layout.addWidget(self.vertices_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)
        layout.addWidget(vertex_group)
        self.open_button.clicked.connect(lambda: self._start('open'))
        self.import_button.clicked.connect(lambda: self._start('import'))
        self.vertices_button.clicked.connect(lambda: self._start('vertices'))

    def set_dcc_clients(self, clients):
        self._dcc_clients = clients

    def _confirm_open(self):
        dialog = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, '打开到 Blender',
                                       '当前 Blender 文件将关闭，未保存的更改将丢弃。',
                                       parent=QtWidgets.QApplication.activeWindow())
        proceed = dialog.addButton('丢弃更改并打开', QtWidgets.QMessageBox.AcceptRole)
        cancel = dialog.addButton(QtWidgets.QMessageBox.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() == proceed

    def _start(self, mode):
        if self._task is not None:
            return
        blender = self._dcc_clients.get('blender')
        for label, client in (('Maya', self.client), ('Blender', blender)):
            if not getattr(client, 'connected', False):
                self.log_result('fbx_to_blender', dict(ok=False, error=f'{label} 未连接，请先在中台连接'))
                return
        if mode == 'open' and not self._confirm_open():
            return
        self._set_busy(True)
        task = _Task(self.client, blender, mode, self.animation_action.isChecked(), mode == 'open')
        self._task = task
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy):
        for button in (self.open_button, self.import_button, self.vertices_button, self.settings_button):
            button.setEnabled(not busy)

    def format_log_result(self, action, response):
        return format_messages(response)

    @QtCore.Slot(object)
    def _finished(self, response):
        self._task = None
        self._set_busy(False)
        self.log_result('fbx_to_blender', response)


def create_widget(client, log, parent=None):
    return FbxToBlenderWidget(client, log, parent)
