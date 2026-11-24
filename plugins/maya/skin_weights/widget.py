"""参考 SkinClusterIO 的检查/限制布局；数值任务后台运行。"""

from PySide6 import QtCore, QtWidgets
from ui.plugin_widget import BasePluginWidget
from ui.maya_blender_skin_transfer import run_blender_transfer, format_blender_transfer_messages
from ui.skin_weights import run_operation, format_log_messages
from ui.skin_transfer import run_transfer, format_transfer_messages
from ui.skin_weight_files import run_file_operation, format_file_messages


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, client, operation, threshold, mode, max_client=None, transfer_mode="vertex_id",
                 issue_token=None, blender_client=None):
        super().__init__()
        self.client, self.operation = client, operation
        self.threshold, self.mode = threshold, mode
        self.max_client = max_client
        self.blender_client = blender_client
        self.transfer_mode = transfer_mode
        self.issue_token = issue_token
        self.signals = _Signals()

    def run(self):
        try:
            if self.operation == "send_max":
                result = run_transfer(self.client, self.max_client, self.threshold)
            elif self.operation == "send_blender":
                result = run_blender_transfer(self.client, self.blender_client)
            elif self.operation in ("export_file", "import_file"):
                result = run_file_operation(self.client, self.operation, self.transfer_mode)
            else:
                result = run_operation(self.client, self.operation, self.threshold, self.mode,
                                       issue_token=self.issue_token)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "operation": self.operation}
        self.signals.finished.emit(result)


class SkinWeightsWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "蒙皮权重"
        self._task = None
        self._issue_token = None
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
        self.select_issues_btn = QtWidgets.QPushButton("选中问题顶点")
        self.fix_negative_btn = QtWidgets.QPushButton("修复负权重顶点")
        self.hammer_issues_btn = QtWidgets.QPushButton("锤式修复问题顶点")
        self.import_mode_cbox = QtWidgets.QComboBox()
        self.import_mode_cbox.addItem("按顶点编号", "vertex_id")
        self.import_mode_cbox.addItem("按最近表面点", "closest_point")
        self.export_btn = QtWidgets.QPushButton("导出权重")
        self.import_btn = QtWidgets.QPushButton("导入权重")
        self.send_btn = QtWidgets.QPushButton("发送权重到 Max")
        self.send_blender_btn = QtWidgets.QPushButton("发送权重到 Blender")
        self.check_btn.clicked.connect(lambda: self._start("check"))
        self.select_btn.clicked.connect(lambda: self._start("select_excess"))
        self.fix_btn.clicked.connect(lambda: self._start("limit"))
        self.select_issues_btn.clicked.connect(lambda: self._start("select_issues"))
        self.fix_negative_btn.clicked.connect(lambda: self._start("fix_negative"))
        self.hammer_issues_btn.clicked.connect(lambda: self._start("hammer_issues"))
        self.export_btn.clicked.connect(lambda: self._start("export_file"))
        self.import_btn.clicked.connect(lambda: self._start("import_file"))
        self.send_btn.clicked.connect(lambda: self._start("send_max"))
        self.send_blender_btn.clicked.connect(lambda: self._start("send_blender"))

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
        issues_group = QtWidgets.QGroupBox("")
        issue_buttons = QtWidgets.QHBoxLayout(issues_group)
        issue_buttons.addWidget(self.select_issues_btn)
        issue_buttons.addWidget(self.fix_negative_btn)
        issue_buttons.addWidget(self.hammer_issues_btn)
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
        transfer_group = QtWidgets.QGroupBox("")
        transfer_layout = QtWidgets.QVBoxLayout(transfer_group)
        transfer_layout.addWidget(self.send_btn)
        transfer_layout.addWidget(self.send_blender_btn)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(group)
        layout.addWidget(issues_group)
        layout.addWidget(file_group)
        layout.addWidget(transfer_group)
        self._set_busy(False)

    def set_dcc_clients(self, clients):
        self._dcc_clients = clients

    def _start(self, operation):
        if self._task is not None:
            return
        if operation in ("select_issues", "fix_negative", "hammer_issues") and not self._issue_token:
            return
        if operation in ("check", "limit", "import_file"):
            self._issue_token = None
        max_client = self._dcc_clients.get("max")
        if operation == "send_max":
            if max_client is None or not getattr(max_client, "connected", False):
                self.log_result("skin_weights", dict(ok=False, operation="send_max",
                                error="Max 未连接，请先在中台连接 Max"))
                return
        elif operation not in ("send_blender", "export_file", "import_file", "select_issues"):
            self.inf_lab.setText("处理中…")
        self._set_busy(True)
        task = _Task(self.client, operation, self.max_inf_sbox.value(),
                     self.mode_cbox.currentIndex(), max_client=max_client,
                     transfer_mode=self.import_mode_cbox.currentData(), issue_token=self._issue_token,
                     blender_client=self._dcc_clients.get("blender"))
        self._task = task
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    def _set_busy(self, busy):
        for control in (self.check_btn, self.select_btn, self.fix_btn,
                        self.mode_cbox, self.max_inf_sbox, self.send_btn, self.send_blender_btn,
                        self.export_btn, self.import_btn, self.import_mode_cbox):
            control.setEnabled(not busy)
        for control in (self.select_issues_btn, self.fix_negative_btn, self.hammer_issues_btn):
            control.setEnabled(not busy and bool(self._issue_token))

    def format_log_result(self, action, response):
        data = response.get("data", {}) if response.get("ok") else response
        if data.get("operation") == "send_max":
            return format_transfer_messages(response)
        if data.get("operation") == "send_blender":
            return format_blender_transfer_messages(response)
        if data.get("operation") in ("export_file", "import_file"):
            return format_file_messages(response)
        return format_log_messages(response)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        data = self.log_result("skin_weights", result)
        result_data = result.get("data", {}) if result.get("ok") else result
        operation = result_data.get("operation")
        if operation == "check":
            self._issue_token = data.get("issue_token") if data else None
        elif operation in ("fix_negative", "hammer_issues") or (operation == "select_issues" and
                                               (not data or data.get("errors"))):
            self._issue_token = None
        self._set_busy(False)
        if operation == "select_issues":
            return
        if operation in ("send_max", "send_blender", "export_file"):
            return  # 发送/导出不修改 Maya；保留原来的检测结果。
        if not data or data.get("max_influences") is None:
            self.inf_lab.setText("需重新检测")
            return
        text = str(data["max_influences"])
        if data.get("invalid_vertices"):
            text += f'（异常 {data["invalid_vertices"]}）'
        self.inf_lab.setText(text)


def create_widget(client, log, parent=None):
    return SkinWeightsWidget(client, log, parent)
