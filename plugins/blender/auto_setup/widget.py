"""Install or remove the bridge startup script for the connected Blender profile."""

from PySide6 import QtWidgets

from ui import blender_setup
from ui.plugin_widget import set_role


class _AutoSetupWidget(QtWidgets.QWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log

        self.install_button = QtWidgets.QPushButton("安装自动启动")
        self.remove_button = QtWidgets.QPushButton("卸载自动启动")
        self.install_button.clicked.connect(self._install)
        self.remove_button.clicked.connect(self._remove)
        self.status = QtWidgets.QLabel("先连接 Blender，再操作当前配置。重启 Blender 后生效。")
        self.status.setWordWrap(True)
        set_role(self.status, "hint")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.install_button)
        layout.addWidget(self.remove_button)
        layout.addWidget(self.status)

    def _install(self):
        self._change_installation(install=True)

    def _remove(self):
        self._change_installation(install=False)

    def _change_installation(self, *, install):
        try:
            if not self.client.connected:
                raise RuntimeError("请先连接 Blender")
            reply = self.client.call("get_startup_dir", timeout=3, connect_timeout=1)
            if not reply.get("ok"):
                error = reply.get("error", "无法读取 Blender 启动目录")
                if "unknown action: get_startup_dir" in error:
                    error = "请先在 Reload Plug 卡片重载动作库，再操作。"
                raise RuntimeError(error)
            directory = reply["data"]["path"]
            if install:
                path = blender_setup.write_script(directory, self.client.port)
                message = "已安装。重启 Blender 后自动启动桥接。"
            else:
                path = blender_setup.remove_script(directory)
                message = "已卸载。重启 Blender 后停止自动启动。" if path else "当前 Blender 配置尚未安装自动启动。"
        except Exception as exc:
            message = str(exc)
            self.status.setText(message)
            set_role(self.status, "error")
            self.log.error(f"[Blender 自动启动] {message}")
            return
        self.status.setText(message)
        set_role(self.status, "success")
        self.log.info(f"[Blender 自动启动] {message}" + (f" {path}" if path else ""))


def create_widget(client, log, parent=None):
    return _AutoSetupWidget(client, log, parent)
