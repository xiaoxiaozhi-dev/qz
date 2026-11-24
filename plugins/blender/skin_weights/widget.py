"""Blender skin-weight card with the existing check, repair and file workflows."""
from pathlib import Path
import runpy

from PySide6 import QtCore, QtWidgets

from ui.plugin_widget import BasePluginWidget


_operations = runpy.run_path(str(Path(__file__).with_name("operations.py")))
run_operation = _operations["run_operation"]
format_result = _operations["format_result"]


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, client, op, threshold, mode, transfer_mode):
        super().__init__()
        self.args = (client, op, threshold, mode, transfer_mode)
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(run_operation(*self.args))


class SkinWeightsWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "蒙皮权重"
        self._task = None
        self.mode_cbox = QtWidgets.QComboBox()
        self.mode_cbox.addItems(["按比例分配", "平均分配"])
        self.max_inf_sbox = QtWidgets.QSpinBox(minimum=1, maximum=100000, value=4)
        self.max_inf_sbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.inf_lab = QtWidgets.QLabel("未检测")
        self.check_btn = QtWidgets.QPushButton("检测最大影响")
        self.select_btn = QtWidgets.QPushButton("选择超出顶点")
        self.fix_btn = QtWidgets.QPushButton("修改超出影响")
        self.import_mode_cbox = QtWidgets.QComboBox()
        self.import_mode_cbox.addItem("按顶点编号", "vertex_id")
        self.import_mode_cbox.addItem("按最近表面点", "closest_point")
        self.export_btn = QtWidgets.QPushButton("导出权重")
        self.import_btn = QtWidgets.QPushButton("导入权重")

        weights = QtWidgets.QGroupBox()
        body = QtWidgets.QVBoxLayout(weights)
        for label, control in (("分配方式", self.mode_cbox), ("设置最大骨骼影响数", self.max_inf_sbox)):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(control)
            body.addLayout(row)
        for row in (self._row(QtWidgets.QLabel("当前最大骨骼影响数"), self.inf_lab),
                    self._row(self.check_btn, self.select_btn, self.fix_btn)):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            body.addWidget(line)
            body.addLayout(row)
        files = QtWidgets.QGroupBox()
        file_layout = QtWidgets.QVBoxLayout(files)
        file_layout.addLayout(self._row(QtWidgets.QLabel("导入方式"), self.import_mode_cbox))
        file_layout.addLayout(self._row(self.export_btn, self.import_btn))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(weights)
        layout.addWidget(files)
        actions = ((self.check_btn, "check"), (self.select_btn, "select_excess"),
                   (self.fix_btn, "limit"), (self.export_btn, "export_file"), (self.import_btn, "import_file"))
        self._controls = (
            *(button for button, _ in actions), self.max_inf_sbox, self.mode_cbox, self.import_mode_cbox,
        )
        for button, operation in actions:
            button.clicked.connect(lambda checked=False, op=operation: self._start(op))

    @staticmethod
    def _row(*widgets):
        row = QtWidgets.QHBoxLayout()
        for widget in widgets:
            row.addWidget(widget)
        return row

    def _start(self, operation):
        if self._task is not None:
            return
        if operation not in ("export_file", "import_file"):
            self.inf_lab.setText("处理中…")
        for control in self._controls:
            control.setEnabled(False)
        task = self._task = _Task(self.client, operation, self.max_inf_sbox.value(),
                                  self.mode_cbox.currentIndex(), self.import_mode_cbox.currentData())
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def format_log_result(self, action, response):
        return format_result(response)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        for control in self._controls:
            control.setEnabled(True)
        data = self.log_result("skin_weights_blender", result)
        if (data or result)["operation"] == "export_file":
            return
        maximum = data.get("max_influences") if data else None
        text = "需重新检测" if maximum is None else str(maximum)
        if maximum is not None and data.get("invalid_vertices"):
            text += f'（异常 {data["invalid_vertices"]}）'
        self.inf_lab.setText(text)


def create_widget(client, log, parent=None):
    return SkinWeightsWidget(client, log, parent)
