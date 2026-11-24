"""Blender batch rename card."""

from PySide6 import QtCore, QtWidgets

from ui.plugin_widget import BasePluginWidget


class _RenameSignals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _RenameTask(QtCore.QRunnable):
    def __init__(self, client, params):
        super().__init__()
        self.client, self.params = client, params
        self.signals = _RenameSignals()

    def run(self):
        try:
            result = self.client.call("rename_tool", timeout=3, connect_timeout=1, **self.params)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self.signals.finished.emit(result)


class _RenameWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "批量重命名"
        self._task = None

        self.sel_rb = QtWidgets.QRadioButton("仅选中")
        self.sel_rb.setChecked(True)
        self.hier_rb = QtWidgets.QRadioButton("含层级")
        mode = QtWidgets.QHBoxLayout()
        mode.addWidget(self.sel_rb)
        mode.addWidget(self.hier_rb)
        mode.addStretch()

        self.prefix_edit = QtWidgets.QLineEdit(placeholderText="如 L_")
        self.prefix_btn = QtWidgets.QPushButton("加前缀")
        self.suffix_edit = QtWidgets.QLineEdit(placeholderText="如 _geo")
        self.suffix_btn = QtWidgets.QPushButton("加后缀")

        self.search_edit = QtWidgets.QLineEdit(placeholderText="查找")
        self.replace_edit = QtWidgets.QLineEdit(placeholderText="替换为（留空即删除）")
        self.replace_btn = QtWidgets.QPushButton("替换")
        self.replace_btn.setSizePolicy(self.replace_btn.sizePolicy().horizontalPolicy(),
                                       QtWidgets.QSizePolicy.Policy.Expanding)

        self.base_edit = QtWidgets.QLineEdit(placeholderText="如 bone_ → bone_01")
        self.rename_btn = QtWidgets.QPushButton("重命名")
        self.add_prefix_cb = QtWidgets.QCheckBox("套用前缀", checked=True)
        self.add_suffix_cb = QtWidgets.QCheckBox("套用后缀", checked=True)
        affixes = QtWidgets.QHBoxLayout()
        affixes.addWidget(self.add_prefix_cb)
        affixes.addWidget(self.add_suffix_cb)
        affixes.addStretch()

        self.index_spin = QtWidgets.QSpinBox(minimum=1, maximum=255,
                                             alignment=QtCore.Qt.AlignCenter)
        self.del_index_btn = QtWidgets.QPushButton("删该位")
        self.del_start_btn = QtWidgets.QPushButton("删首字")
        self.del_end_btn = QtWidgets.QPushButton("删尾字")
        delete = QtWidgets.QHBoxLayout()
        delete.setSpacing(4)
        for widget in (self.index_spin, self.del_index_btn, self.del_start_btn, self.del_end_btn):
            delete.addWidget(widget)

        self.check_btn = QtWidgets.QPushButton("检查同名或同基名（选中对象）")

        self._form_widget = QtWidgets.QWidget()
        form = QtWidgets.QGridLayout(self._form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)
        for row, text in enumerate(("范围", "前缀", "后缀", "查找", "替换", "新名")):
            form.addWidget(QtWidgets.QLabel(text), row, 0)
        form.addLayout(mode, 0, 1, 1, 2)
        for row, edit, button in ((1, self.prefix_edit, self.prefix_btn),
                                  (2, self.suffix_edit, self.suffix_btn),
                                  (5, self.base_edit, self.rename_btn)):
            form.addWidget(edit, row, 1)
            form.addWidget(button, row, 2)
        form.addWidget(self.search_edit, 3, 1)
        form.addWidget(self.replace_edit, 4, 1)
        form.addWidget(self.replace_btn, 3, 2, 2, 1)
        form.addLayout(affixes, 6, 1, 1, 2)
        form.addWidget(QtWidgets.QLabel("删字"), 7, 0)
        form.addLayout(delete, 7, 1, 1, 2)
        form.addWidget(self.check_btn, 8, 0, 1, 3)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._form_widget)
        layout.addStretch()

        self.prefix_btn.clicked.connect(lambda: self._start("prefix", prefix=self.prefix_edit.text()))
        self.suffix_btn.clicked.connect(lambda: self._start("suffix", suffix=self.suffix_edit.text()))
        self.replace_btn.clicked.connect(lambda: self._start(
            "replace", search=self.search_edit.text(), replace=self.replace_edit.text()))
        self.rename_btn.clicked.connect(self._numbered)
        self.del_index_btn.clicked.connect(lambda: self._start("delete_index", index=self.index_spin.value() - 1))
        self.del_start_btn.clicked.connect(lambda: self._start("delete_index", index=0))
        self.del_end_btn.clicked.connect(lambda: self._start("delete_index", index=-1))
        self.check_btn.clicked.connect(lambda: self._start("check_duplicates"))

    def _numbered(self):
        self._start("rename", base=self.base_edit.text(),
                    prefix=self.prefix_edit.text(), suffix=self.suffix_edit.text(),
                    use_prefix=self.add_prefix_cb.isChecked(), use_suffix=self.add_suffix_cb.isChecked())

    def _start(self, op, **params):
        self._form_widget.setEnabled(False)
        task = self._task = _RenameTask(self.client, dict(op=op, hierarchy=self.hier_rb.isChecked(), **params))
        task.signals.finished.connect(self._finished, QtCore.Qt.QueuedConnection)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _finished(self, result):
        self._task = None
        self._form_widget.setEnabled(True)
        self.log_result("rename_tool", result)

    def format_log_result(self, action, result):
        if not result.get("ok"):
            error = result.get("error", "Blender 批量重命名失败")
            if "unknown action: rename_tool" in error:
                return [("warn", "请在 Blender → 设置中运行 Reload Plug 后重试")]
            return [("error", error)]

        data = result["data"]
        count = data["count"]
        names = "、".join(data["names"])
        skipped = data.get("skipped", 0)
        messages = []
        if "groups" in data and data["groups"]:
            messages.append(("info", f"找到 {data['groups']} 组同名或同基名对象，已选中 {count} 个"))
            if skipped:
                messages.append(("warn", f"其中 {skipped} 个对象在当前视图层不可选"))
        elif "groups" not in data:
            messages.append(("info", f"已改名 {count} 个对象，跳过 {skipped} 个"))
            if data.get("readonly"):
                messages.append(("warn", f"其中 {data['readonly']} 个对象只读"))
            if data.get("adjusted"):
                preview = "、".join(f"{requested} → {actual}" for requested, actual in data["adjustments"])
                messages.append(("warn", f"{data['adjusted']} 项使用 Blender 实际名称（{preview}）"))
        if names:
            messages.append(("info", names))
        if data.get("warning"):
            messages.append(("warn", data["warning"]))
        return messages


def create_widget(client, log, parent=None):
    return _RenameWidget(client, log, parent)
