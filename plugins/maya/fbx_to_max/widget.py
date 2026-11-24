"""Maya-side FBX sender. Scene work runs through the existing DCC clients."""
import json

from PySide6 import QtCore, QtGui, QtWidgets

import paths
from ui.maya_fbx_transfer import run_transfer, format_messages
from ui.vertex_positions import run_transfer as run_vertex_transfer, format_messages as vertex_messages
from ui.plugin_widget import BasePluginWidget


class ExportSettingsDialog(QtWidgets.QDialog):
    closed = QtCore.Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setWindowTitle('FBX 发送设置')
        self.animation_checkbox = QtWidgets.QCheckBox('导出动画')
        self.animation_checkbox.setChecked(settings['animation'])
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(self.animation_checkbox)

    def values(self):
        return dict(animation=self.animation_checkbox.isChecked())

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, maya_client, max_client, mode, settings):
        super().__init__()
        self.args = maya_client, max_client, mode, settings['animation']
        self.signals = _Signals()

    def run(self):
        try:
            result = (run_vertex_transfer(*self.args[:2]) if self.args[2] == 'vertices'
                      else run_transfer(*self.args))
        except Exception as exc:
            result = dict(ok=False, error=str(exc))
        self.signals.finished.emit(result)


class FbxToMaxWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = '选中物体发送到Max'
        self._dcc_clients, self._task, self._dialog = {}, None, None
        self._store = QtCore.QSettings('QzTools', 'MayaToMax')
        self._settings = dict(animation=True)
        try:
            saved = json.loads(self._store.value('export_settings', '{}'))
            if isinstance(saved, dict) and isinstance(saved.get('animation'), bool):
                self._settings['animation'] = saved['animation']
        except (TypeError, ValueError):
            pass
        self.open_button = QtWidgets.QPushButton('打开到 Max')
        self.import_button = QtWidgets.QPushButton('导入到 Max')
        self.vertices_button = QtWidgets.QPushButton('发送顶点位置到 Max')
        self.settings_button = QtWidgets.QPushButton()
        self.settings_button.setIcon(QtGui.QIcon(paths.resource('settings.svg')))
        icon_size = self.open_button.fontMetrics().height()
        self.settings_button.setIconSize(QtCore.QSize(icon_size, icon_size))
        self.settings_button.setAccessibleName('发送设置')
        self.open_button.clicked.connect(lambda: self._start('open'))
        self.import_button.clicked.connect(lambda: self._start('import'))
        self.vertices_button.clicked.connect(lambda: self._start('vertices'))
        self.settings_button.clicked.connect(self._show_settings)
        group = QtWidgets.QGroupBox('')
        row = QtWidgets.QHBoxLayout(group)
        row.addWidget(self.open_button, 1)
        row.addWidget(self.import_button, 1)
        row.addWidget(self.settings_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)
        vertex_group = QtWidgets.QGroupBox('')
        vertex_layout = QtWidgets.QHBoxLayout(vertex_group)
        vertex_layout.addWidget(self.vertices_button)
        layout.addWidget(vertex_group)

    def set_dcc_clients(self, clients):
        self._dcc_clients = clients

    def _show_settings(self):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        parent, center, bounds = self._popup_anchor()
        # Parenting to the real application window avoids Qt embedding a child
        # dialog back inside the card's QGraphicsProxyWidget.
        dialog = ExportSettingsDialog(self._settings, parent)
        self._dialog = dialog
        dialog.closed.connect(self._settings_closed)
        dialog.adjustSize()
        size = dialog.size()
        left = max(bounds.left(), min(center.x() - size.width() // 2,
                                      bounds.right() - size.width() + 1))
        top = max(bounds.top(), min(center.y() - size.height() // 2,
                                    bounds.bottom() - size.height() + 1))
        dialog.move(left, top)
        dialog.show()
        dialog.animation_checkbox.setFocus()

    def _popup_anchor(self):
        widget, proxy = self, None
        while widget is not None and proxy is None:
            proxy = widget.graphicsProxyWidget()
            widget = widget.parentWidget()
        parent = self.window() if proxy is None else None
        center = self.mapToGlobal(self.rect().center()) if proxy is None else QtGui.QCursor.pos()
        bounds = None
        if proxy is not None and proxy.scene() is not None:
            views = [view for view in proxy.scene().views() if view.isVisible()]
            if views:
                view = next((view for view in views if view.viewport().underMouse()), views[0])
                viewport = view.viewport()
                parent = view.window()
                center = viewport.mapToGlobal(view.mapFromScene(proxy.mapToScene(proxy.boundingRect().center())))
                bounds = QtCore.QRect(viewport.mapToGlobal(QtCore.QPoint()), viewport.size())
        screen_point = center if bounds is None else QtCore.QPoint(
            max(bounds.left(), min(center.x(), bounds.right())),
            max(bounds.top(), min(center.y(), bounds.bottom())))
        screen = QtGui.QGuiApplication.screenAt(screen_point) or QtGui.QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        bounds = available if bounds is None else bounds.intersected(available)
        if bounds.isEmpty():
            bounds = available
        return parent, center, bounds

    def _settings_closed(self):
        dialog, self._dialog = self._dialog, None
        if dialog is None:
            return
        self._settings = dialog.values()
        self._store.setValue('export_settings', json.dumps(dict(animation=self._settings['animation'])))
        self._store.sync()
        dialog.deleteLater()

    def _start(self, mode):
        if self._task is not None:
            return
        max_client = self._dcc_clients.get('max')
        for label, client in (('Maya', self.client), ('Max', max_client)):
            if client is None or not getattr(client, 'connected', False):
                self.log_result('fbx_to_max', dict(ok=False, error=label + ' 未连接，请先在中台连接'))
                return
        self._set_busy(True)
        self.log.info('[选中物体发送到Max] 开始发送顶点位置…' if mode == 'vertices' else
                      '[选中物体发送到Max] 开始从 Maya {} FBX…'.format('打开' if mode == 'open' else '导入'))
        task = _Task(self.client, max_client, mode, self._settings.copy())
        self._task = task
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy):
        for control in (self.open_button, self.import_button, self.settings_button, self.vertices_button):
            control.setEnabled(not busy)

    def format_log_result(self, action, response):
        return vertex_messages(response) if response.get('operation') == 'vertex_positions' else format_messages(response)

    @QtCore.Slot(object)
    def _finished(self, response):
        self._task = None
        self._set_busy(False)
        self.log_result('fbx_to_max', response)


def create_widget(client, log, parent=None):
    return FbxToMaxWidget(client, log, parent)
