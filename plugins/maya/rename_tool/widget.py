"""
重命名插件 — 批量改名：前缀 / 后缀 / 替换 / 编号重命名 / 删字 / 查重名。

卡片只放输入控件，结果统一进日志（同 bone_mirror）。
「选中 / 含层级」是整张卡的范围开关，所有操作共用。
"""

from PySide6 import QtCore, QtGui, QtWidgets
from ui.plugin_widget import BasePluginWidget


class _RenameWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "重命名"

        # ── 范围 ──
        self.sel_rb = QtWidgets.QRadioButton("仅选中")
        self.sel_rb.setChecked(True)
        self.hier_rb = QtWidgets.QRadioButton("含层级")
        mode_row = QtWidgets.QWidget()
        mode_lay = QtWidgets.QHBoxLayout(mode_row)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.setSpacing(4)
        mode_lay.addWidget(self.sel_rb)
        mode_lay.addWidget(self.hier_rb)
        mode_lay.addStretch(1)

        # ── 前缀 / 后缀 ──
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText("如 L_")
        self.prefix_btn = QtWidgets.QPushButton("加前缀")

        self.suffix_edit = QtWidgets.QLineEdit()
        self.suffix_edit.setPlaceholderText("如 _geo")
        self.suffix_btn = QtWidgets.QPushButton("加后缀")

        # ── 查找替换 ──
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("查找")
        self.replace_edit = QtWidgets.QLineEdit()
        self.replace_edit.setPlaceholderText("替换为（留空 = 删掉）")
        self.replace_btn = QtWidgets.QPushButton("替换")
        self.replace_btn.setSizePolicy(self.replace_btn.sizePolicy().horizontalPolicy(),
                                       QtWidgets.QSizePolicy.Policy.Minimum)

        # ── 编号重命名 ──
        self.base_edit = QtWidgets.QLineEdit()
        self.base_edit.setPlaceholderText("如 bone_ → bone_01")
        self.rename_btn = QtWidgets.QPushButton("重命名")
        self.add_prefix_cb = QtWidgets.QCheckBox("套用前缀")
        self.add_suffix_cb = QtWidgets.QCheckBox("套用后缀")
        self.add_prefix_cb.setChecked(True)
        self.add_suffix_cb.setChecked(True)

        # ── 删字 ──
        self.index_spin = QtWidgets.QSpinBox()
        self.index_spin.setRange(1, 64)
        self.index_spin.setValue(1)
        self.index_spin.setFixedWidth(48)
        self.index_spin.setAlignment(QtCore.Qt.AlignCenter)
        self.del_index_btn = QtWidgets.QPushButton("删该位")
        self.del_start_btn = QtWidgets.QPushButton("删首字")
        self.del_end_btn = QtWidgets.QPushButton("删尾字")

        # ── 查重名 ──
        self.check_btn = QtWidgets.QPushButton("检查重名（选中冲突物体）")


        def lbl(text):
            w = QtWidgets.QLabel(text)
            return w

        del_row = QtWidgets.QWidget()
        del_lay = QtWidgets.QHBoxLayout(del_row)
        del_lay.setContentsMargins(0, 0, 0, 0)
        # 四个控件共用一行，使用较紧的间距；卡片随内容自适应宽度。
        del_lay.setSpacing(4)
        del_lay.addWidget(self.index_spin)
        del_lay.addWidget(self.del_index_btn)
        del_lay.addWidget(self.del_start_btn)
        del_lay.addWidget(self.del_end_btn)

        cb_row = QtWidgets.QWidget()
        cb_lay = QtWidgets.QHBoxLayout(cb_row)
        cb_lay.setContentsMargins(0, 0, 0, 0)
        cb_lay.setSpacing(10)
        cb_lay.addWidget(self.add_prefix_cb)
        cb_lay.addWidget(self.add_suffix_cb)
        cb_lay.addStretch(1)

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)

        form.addWidget(lbl("范围："), 0, 0);  form.addWidget(mode_row, 0, 1, 1, 2)
        form.addWidget(lbl("前缀："), 1, 0)
        form.addWidget(self.prefix_edit, 1, 1); form.addWidget(self.prefix_btn, 1, 2)
        form.addWidget(lbl("后缀："), 2, 0)
        form.addWidget(self.suffix_edit, 2, 1); form.addWidget(self.suffix_btn, 2, 2)
        form.addWidget(lbl("查找："), 3, 0); form.addWidget(self.search_edit, 3, 1)
        form.addWidget(lbl("替换："), 4, 0)
        form.addWidget(self.replace_edit, 4, 1); form.addWidget(self.replace_btn, 3, 2, 2, 1)
        form.addWidget(lbl("新名："), 5, 0)
        form.addWidget(self.base_edit, 5, 1); form.addWidget(self.rename_btn, 5, 2)
        form.addWidget(cb_row, 6, 1, 1, 2)
        form.addWidget(lbl("删字："), 7, 0); form.addWidget(del_row, 7, 1, 1, 2)
        form.addWidget(self.check_btn, 8, 0, 1, 3)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        lay.addStretch(1)

        # ── 连接 ──
        self.prefix_btn.clicked.connect(
            lambda: self._run("prefix", prefix=self.prefix_edit.text()))
        self.suffix_btn.clicked.connect(
            lambda: self._run("suffix", suffix=self.suffix_edit.text()))
        self.replace_btn.clicked.connect(
            lambda: self._run("replace", search=self.search_edit.text(),
                              replace=self.replace_edit.text()))
        self.rename_btn.clicked.connect(self._rename)
        self.del_index_btn.clicked.connect(
            # UI 是 1 起，action 是 0 起
            lambda: self._run("delete_index", index=self.index_spin.value() - 1))
        self.del_start_btn.clicked.connect(
            lambda: self._run("delete_index", index=0))
        self.del_end_btn.clicked.connect(
            lambda: self._run("delete_index", index=-1))
        self.check_btn.clicked.connect(
            lambda: self._run("check_duplicates"))

    # ── 内部 ──
    def _run(self, op, **params):
        """所有操作都带同一个范围开关，日志由 log_run 统一处理。"""
        params["hierarchy"] = self.hier_rb.isChecked()
        self.log_run("rename_tool", op=op, **params)

    def _rename(self):
        self._run("rename", base=self.base_edit.text(),
                  prefix=self.prefix_edit.text(),
                  suffix=self.suffix_edit.text(),
                  use_prefix=self.add_prefix_cb.isChecked(),
                  use_suffix=self.add_suffix_cb.isChecked())


def create_widget(client, log, parent=None):
    return _RenameWidget(client, log, parent)
