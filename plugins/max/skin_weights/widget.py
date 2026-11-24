"""Max 蒙皮检查、限制与编号/最近表面点文件导入；任务后台运行。"""

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget
from ui.max_skin_weights import run_operation, run_file_operation, format_result


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, client, operation, threshold, mode, transfer_mode="vertex_id"):
        super().__init__()
        self.client, self.operation = client, operation
        self.threshold, self.mode = threshold, mode
        self.transfer_mode = transfer_mode
        self.signals = _Signals()

    def run(self):
        try:
            if self.operation in ("export_file", "import_file"):
                result = run_file_operation(self.client, self.operation, self.transfer_mode)
            else:
                result = run_operation(self.client, self.operation, self.threshold, self.mode)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "operation": self.operation}
        self.signals.finished.emit(result)


class SkinWeightsWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "蒙皮权重"
        self._task = None
        self._dcc_clients = {}
        self.mode_cbox = QtWidgets.QComboBox()
        self.mode_cbox.addItems(["按比例分配", "平均分配"])
        self.max_inf_sbox = QtWidgets.QSpinBox()
        self.max_inf_sbox.setRange(1, 100000)
        self.max_inf_sbox.setValue(4)
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
        self.check_btn.clicked.connect(lambda: self._start("check"))
        self.select_btn.clicked.connect(lambda: self._start("select_excess"))
        self.fix_btn.clicked.connect(lambda: self._start("limit"))
        self.export_btn.clicked.connect(lambda: self._start("export_file"))
        self.import_btn.clicked.connect(lambda: self._start("import_file"))

        group = QtWidgets.QGroupBox("")
        form = QtWidgets.QVBoxLayout(group)
        for label, control in (("分配方式", self.mode_cbox),
                               ("设置最大骨骼影响数", self.max_inf_sbox)):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(control)
            form.addLayout(row)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        form.addWidget(line)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("当前最大骨骼影响数"))
        row.addWidget(self.inf_lab)
        form.addLayout(row)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        form.addWidget(line)
        buttons = QtWidgets.QHBoxLayout()
        for button in (self.check_btn, self.select_btn, self.fix_btn):
            buttons.addWidget(button)
        form.addLayout(buttons)
        file_group = QtWidgets.QGroupBox("")
        file_layout = QtWidgets.QVBoxLayout(file_group)
        import_mode = QtWidgets.QHBoxLayout()
        import_mode.addWidget(QtWidgets.QLabel("导入方式"))
        import_mode.addWidget(self.import_mode_cbox)
        file_layout.addLayout(import_mode)
        file_buttons = QtWidgets.QHBoxLayout()
        file_buttons.addWidget(self.export_btn)
        file_buttons.addWidget(self.import_btn)
        file_layout.addLayout(file_buttons)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)
        layout.addWidget(file_group)

    def set_dcc_clients(self, clients):
        self._dcc_clients = clients

    def _start(self, operation):
        if self._task is not None:
            return
        if operation not in ("export_file", "import_file"):
            self.inf_lab.setText("处理中…")
        self._set_busy(True)
        task = _Task(self.client, operation, self.max_inf_sbox.value(), self.mode_cbox.currentIndex(),
                     self.import_mode_cbox.currentData())
        self._task = task
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy):
        for control in (self.check_btn, self.select_btn, self.fix_btn,
                        self.mode_cbox, self.max_inf_sbox,
                        self.export_btn, self.import_btn, self.import_mode_cbox):
            control.setEnabled(not busy)

    def format_log_result(self, action, response):
        return format_result(response)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        self._set_busy(False)
        data = self.log_result("skin_weights", result)
        result_data = result.get("data", {}) if result.get("ok") else result
        if result_data.get("operation") == "export_file":
            return  # 导出不修改 Max；保留原来的检测结果。
        if not data or data.get("max_influences") is None:
            self.inf_lab.setText("需重新检测")
            return
        text = str(data["max_influences"])
        if data.get("invalid_vertices"):
            text += f'（异常 {data["invalid_vertices"]}）'
        self.inf_lab.setText(text)


def create_widget(client, log, parent=None):
    return SkinWeightsWidget(client, log, parent)
