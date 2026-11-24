"""
骨骼镜像插件 — 结果统一进日志，卡片内只保留输入控件。
"""

from PySide6 import QtCore, QtGui, QtWidgets
from ui.plugin_widget import BasePluginWidget


class _MirrorWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "骨骼镜像"

        prefix = QtWidgets.QLineEdit("L_")
        axis = QtWidgets.QComboBox(); axis.addItems(["X", "Y", "Z", "-X", "-Y", "-Z"])
        run_btn = QtWidgets.QPushButton("执行镜像")

        prefix_lbl = QtWidgets.QLabel("前缀：")
        axis_lbl = QtWidgets.QLabel("轴向：")

        run_btn.clicked.connect(lambda: self.log_run("bone_mirror", prefix=prefix.text(), axis=axis.currentText()))

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addWidget(prefix_lbl, 0, 0); form.addWidget(prefix, 0, 1)
        form.addWidget(axis_lbl, 1, 0); form.addWidget(axis, 1, 1)
        form.addWidget(run_btn, 0, 2, 2, 1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        lay.addStretch(1)


def create_widget(client, log, parent=None):
    return _MirrorWidget(client, log, parent)
