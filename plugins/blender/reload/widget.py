"""Reload the Blender bridge's action modules."""

from ui.blender_setup import LEGACY_BRIDGE_MESSAGE
from ui.plugin_widget import AsyncActionWidget


class _ReloadWidget(AsyncActionWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(client, log, "reload_actions", "重载 Blender 动作库", parent, timeout=15)

    def format_log_result(self, action, result):
        if result.get("ok"):
            names = result["data"]["actions"]
            return [("info", f"已加载 {len(names)} 个动作：{', '.join(names)}")]
        error = result.get("error", "Blender 动作库重载失败")
        if "unknown action: reload_actions" in error:
            return [("warn", LEGACY_BRIDGE_MESSAGE)]
        return [("error", error)]


def create_widget(client, log, parent=None):
    return _ReloadWidget(client, log, parent)
