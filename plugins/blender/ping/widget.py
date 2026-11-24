"""Check Blender's bridge version and main-thread availability."""

from ui.plugin_widget import AsyncActionWidget


class _PingWidget(AsyncActionWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(client, log, "ping", "Ping Blender", parent)

    def format_log_result(self, action, result):
        if not result.get("ok"):
            return [("error", result.get("error", "Blender 连接测试失败"))]
        data = result["data"]
        state = ("繁忙" if data.get("busy") else
                 "等待响应" if data.get("main_thread_ready") is False else "已连接")
        return [("info", f"Blender {data.get('version', '')} · {state}")]


def create_widget(client, log, parent=None):
    return _PingWidget(client, log, parent)
