"""Display Blender's current object selection in the shared log."""

from ui.plugin_widget import AsyncActionWidget


class _SelectionWidget(AsyncActionWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(client, log, "read_selection", "读取选择", parent)

    def format_log_result(self, action, result):
        if not result.get("ok"):
            return [("error", result.get("error", "读取选择失败"))]
        data = result["data"]
        if not data["count"]:
            return [("info", "当前视图层没有选中对象")]
        return [("info", f"已选中 {data['count']} 个对象：{'、'.join(data['names'])}")]


def create_widget(client, log, parent=None):
    return _SelectionWidget(client, log, parent)
