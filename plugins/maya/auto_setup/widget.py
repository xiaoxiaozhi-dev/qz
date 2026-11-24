"""
Maya · 自动启动安装

往 userSetup.py 写一个标记块，以后每次启动 Maya 自动起 bridge。

⚠️ 与 plugins/max/auto_setup/ **不是同一个文件** —— 按项目约定，
   Maya 和 Max 的 UI 各写各的，Max 那边还要问 Max 要 startup 路径，
   这里不需要（userSetup.py 路径是常量）。逻辑同构，代码不共享。

【为什么不检查 Maya 连接】
   userSetup.py 的位置固定：Documents/maya/scripts/userSetup.py，
   任何 Maya 版本都读它，不存在「装错版本」的可能。
   所以这两个按钮纯本地文件操作，**不需要 Maya 开着**。
   （Max 版不行 —— 它得问 Max 要 startup 目录。）
"""

from PySide6 import QtWidgets

from ui import maya_setup
from ui.plugin_widget import set_role


class _AutoSetupWidget(QtWidgets.QWidget):
    """安装 / 卸载 两个按钮。

    ⚠️ 用户明确要求**不做安装卸载切换**，每个动作一个独立按钮。
    """

    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client = client
        self.log = log

        install = QtWidgets.QPushButton("安装（写入 userSetup.py）")
        install.clicked.connect(self._install)

        remove = QtWidgets.QPushButton("卸载（移除标记块）")
        remove.clicked.connect(self._remove)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        set_role(self._status, "muted")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(install)
        lay.addWidget(remove)
        lay.addWidget(self._status)
        lay.addStretch(1)

        self._refresh()

    def _refresh(self):
        path = maya_setup.user_setup_path()
        installed = maya_setup.has_block(path)
        self._status.setText(
            ("已安装：%s" % path) if installed
            else ("未安装。路径：%s" % path))

    def _install(self):
        path = maya_setup.user_setup_path()
        msg, state = maya_setup.write_block(path)
        self.log.info("[自动启动] %s" % msg)
        self._refresh()

    def _remove(self):
        path = maya_setup.user_setup_path()
        msg, state = maya_setup.remove_block(path)
        self.log.info("[自动启动] %s" % msg)
        self._refresh()


def create_widget(client, log, parent=None):
    return _AutoSetupWidget(client, log, parent)
