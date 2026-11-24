"""
创建 Box 插件 —— 结果进日志。
"""

from PySide6 import QtCore, QtGui, QtWidgets
from ui.plugin_widget import BasePluginWidget


class _BoxWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "Box"

        size = QtWidgets.QDoubleSpinBox(); size.setRange(0.01, 1000); size.setValue(1.0); size.setSingleStep(0.1)
        name = QtWidgets.QLineEdit("pCube1")
        run = QtWidgets.QPushButton("创建 Box")

        size_lbl = QtWidgets.QLabel("边长：")
        name_lbl = QtWidgets.QLabel("名称：")

        run.clicked.connect(lambda: self.log_run("create_box", size=size.value(), name=name.text()))

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addWidget(name_lbl, 0, 0); form.addWidget(name, 0, 1, 1, 2)
        form.addWidget(size_lbl, 1, 0); form.addWidget(size, 1, 1, 1, 2)
        form.addWidget(run, 0, 3, 2, 1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        lay.addStretch(1)


def create_widget(client, log, parent=None):
    return _BoxWidget(client, log, parent)
