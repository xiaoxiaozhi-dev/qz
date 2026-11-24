"""
3ds Max · 创建 Box

在 Max 场景里创建一个 Box（pymxs / MAXScript）。

⚠️ 这是 Max 侧独立的插件，与 Maya 的 create_box 无任何共享。
   UI 长得像是因为操作逻辑相同，不是复用同一份代码。
   真正的区分靠中台注入的 client —— 它连的是 Max 的 bridge(17024)。
"""

from PySide6 import QtWidgets
from ui.plugin_widget import BasePluginWidget


# Max 里 Box 是三维 boxSize，这三项对应长宽高
_AXIS_NAMES = ["X（长）", "Y（宽）", "Z（高）"]


class _CreateBoxWidget(QtWidgets.QWidget, BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log
        self.action_label = "创建 Box"

        size = QtWidgets.QDoubleSpinBox()
        size.setRange(0.01, 10000.0)
        size.setValue(10.0)
        size.setSingleStep(1.0)
        size.setSuffix(" Max 单位")

        pos_x = QtWidgets.QDoubleSpinBox(); pos_x.setRange(-1e6, 1e6)
        pos_y = QtWidgets.QDoubleSpinBox(); pos_y.setRange(-1e6, 1e6)
        pos_z = QtWidgets.QDoubleSpinBox(); pos_z.setRange(-1e6, 1e6)
        for s in (pos_x, pos_y, pos_z):
            s.setValue(0.0)
            s.setSingleStep(1.0)

        btn = QtWidgets.QPushButton("创建 Box")
        btn.clicked.connect(self._create)

        def lbl(text):
            q = QtWidgets.QLabel(text)
            return q

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addWidget(lbl("尺寸："), 0, 0)
        form.addWidget(size, 0, 1)
        form.addWidget(lbl("位置 X："), 1, 0)
        form.addWidget(pos_x, 1, 1)
        form.addWidget(lbl("位置 Y："), 2, 0)
        form.addWidget(pos_y, 2, 1)
        form.addWidget(lbl("位置 Z："), 3, 0)
        form.addWidget(pos_z, 3, 1)
        form.addWidget(btn, 4, 0, 1, 2)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        lay.addStretch(1)

        self._size = size
        self._pos = (pos_x, pos_y, pos_z)

    def _create(self):
        pos = [s.value() for s in self._pos]
        self.log_run("create_box", size=self._size.value(), pos=pos,
                     name="qz_box")


def create_widget(client, log, parent=None):
    return _CreateBoxWidget(client, log, parent)
