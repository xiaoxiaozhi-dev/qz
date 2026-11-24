"""Blender → Maya FBX send card."""
from pathlib import Path
import runpy

from PySide6 import QtCore, QtGui, QtWidgets

import paths
from ui.plugin_widget import BasePluginWidget


# The external plugin loader opens this file directly in development and in dist.
_transfer = runpy.run_path(str(Path(__file__).with_name('transfer.py')))
run_transfer = _transfer['run_transfer']
format_messages = _transfer['format_messages']


class _OptionsPopup(QtWidgets.QDialog):
    closed = QtCore.Signal()

    def __init__(self, animation, optimize_display, parent):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setWindowTitle('FBX 发送设置')
        self.animation_checkbox = QtWidgets.QCheckBox('导出动画', checked=animation)
        self.display_checkbox = QtWidgets.QCheckBox('导入后优化显示', checked=optimize_display)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(self.animation_checkbox)
        layout.addWidget(self.display_checkbox)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _SendTask(QtCore.QRunnable):
    def __init__(self, blender, maya, mode, animation, optimize_display, discard_unsaved):
        super().__init__()
        self.args = blender, maya, mode, animation, optimize_display, discard_unsaved
        self.signals = _Signals()

    def run(self):
        try:
            result = run_transfer(*self.args)
        except Exception as exc:
            result = dict(ok=False, error=str(exc))
        self.signals.finished.emit(result)


class FbxToMayaWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = '选中物体发送到 Maya'
        self._maya = self._task = self._dialog = None
        self._store = QtCore.QSettings('QzTools', 'BlenderToMaya')
        self._animation = self._store.value('animation', True, type=bool)
        self._optimize_display = self._store.value('optimize_display', True, type=bool)

        self.open_button = QtWidgets.QPushButton('打开到 Maya')
        self.import_button = QtWidgets.QPushButton('导入到 Maya')
        self.settings_button = QtWidgets.QPushButton()
        self.settings_button.setIcon(QtGui.QIcon(paths.resource('settings.svg')))
        icon_size = self.open_button.fontMetrics().height()
        self.settings_button.setIconSize(QtCore.QSize(icon_size, icon_size))
        self.settings_button.setAccessibleName('发送设置')
        self.open_button.clicked.connect(lambda: self._start('open'))
        self.import_button.clicked.connect(lambda: self._start('import'))
        self.settings_button.clicked.connect(self._show_settings)

        group = QtWidgets.QGroupBox()
        row = QtWidgets.QHBoxLayout(group)
        row.addWidget(self.open_button, 1)
        row.addWidget(self.import_button, 1)
        row.addWidget(self.settings_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)

    def set_dcc_clients(self, clients):
        self._maya = clients.get('maya')

    def _popup_anchor(self):
        ancestor = self
        while ancestor is not None:
            proxy = ancestor.graphicsProxyWidget()
            if proxy is not None and proxy.scene() is not None:
                views = [view for view in proxy.scene().views() if view.isVisible()]
                if views:
                    view = next((view for view in views if view.viewport().underMouse()), views[0])
                    viewport = view.viewport()
                    position = view.mapFromScene(proxy.mapToScene(proxy.boundingRect().center()))
                    bounds = QtCore.QRect(viewport.mapToGlobal(QtCore.QPoint()), viewport.size())
                    return view.window(), viewport.mapToGlobal(position), bounds
            ancestor = ancestor.parentWidget()
        return self.window(), self.mapToGlobal(self.rect().center()), None

    def _show_settings(self):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        parent, center, bounds = self._popup_anchor()
        dialog = self._dialog = _OptionsPopup(self._animation, self._optimize_display, parent)
        dialog.closed.connect(self._save_settings)
        dialog.adjustSize()
        screen = QtGui.QGuiApplication.screenAt(center) or QtGui.QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        bounds = available if bounds is None else bounds.intersected(available)
        if bounds.isEmpty():
            bounds = available
        left = max(bounds.left(), min(center.x() - dialog.width() // 2, bounds.right() - dialog.width() + 1))
        top = max(bounds.top(), min(center.y() - dialog.height() // 2, bounds.bottom() - dialog.height() + 1))
        dialog.move(left, top)
        dialog.show()
        dialog.animation_checkbox.setFocus()

    @QtCore.Slot()
    def _save_settings(self):
        dialog, self._dialog = self._dialog, None
        self._animation = dialog.animation_checkbox.isChecked()
        self._optimize_display = dialog.display_checkbox.isChecked()
        self._store.setValue('animation', self._animation)
        self._store.setValue('optimize_display', self._optimize_display)
        self._store.sync()
        dialog.deleteLater()

    def _confirm_open(self):
        parent, _, _ = self._popup_anchor()
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle('打开到 Maya')
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText('打开将替换 Maya 当前场景，未保存的修改会丢失。')
        box.setInformativeText('选择「导入到 Maya」可保留当前场景。')
        confirm = box.addButton('替换场景', QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton('取消', QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        allowed = box.clickedButton() is confirm
        box.deleteLater()
        return allowed

    def _start(self, mode):
        if self._task is not None:
            return
        for label, client in (('Blender', self.client), ('Maya', self._maya)):
            if not getattr(client, 'connected', False):
                self.log_result('fbx_to_maya', dict(ok=False, error=f'{label} 未连接，请先在中台连接'))
                return
        discard_unsaved = mode == 'open' and self._confirm_open()
        if mode == 'open' and not discard_unsaved:
            return
        self._set_busy(True)
        self.log.info(f'[{self.action_label}] 正在发送 FBX…')
        task = self._task = _SendTask(
            self.client, self._maya, mode, self._animation, self._optimize_display, discard_unsaved)
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy):
        for control in (self.open_button, self.import_button, self.settings_button):
            control.setEnabled(not busy)

    def format_log_result(self, action, response):
        return format_messages(response)

    @QtCore.Slot(object)
    def _finished(self, response):
        self._task = None
        self._set_busy(False)
        self.log_result('fbx_to_maya', response)


def create_widget(client, log, parent=None):
    return FbxToMayaWidget(client, log, parent)
