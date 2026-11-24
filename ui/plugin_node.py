"""
plugin_node.py — 画布上的"节点"，一个简化版的卡片。

无折叠 / 无关闭 / 无 ⋮ 菜单。删除用 Delete 键，关闭入口在右键菜单。
标题一行（暗色背景条），下面是 widget 自身内容。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .plugin_widget import apply_plugin_style

NODE_WIDTH = 320
# 标题栏实际高度由 QLabel + stylesheet 的 padding 决定（实测 ~21px）。
# 这里只作为"还没 layout 完成"时的兜底，运行时一律用 title_label.height()。
TITLE_HEIGHT = 24


class PluginNode(QtWidgets.QFrame):
    """节点容器：标题栏 + 内容。"""

    def __init__(self, title: str, content: QtWidgets.QWidget,
                 dcc: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("PluginNode")
        self._title = title
        self._content = content
        apply_plugin_style(content)
        self._selected = False
        # ★ dcc 只用来取标题栏配色，不参与任何逻辑判断
        #   （复合键仍然是 plugin_key）
        self._dcc = dcc or ""

        self.setStyleSheet(self._stylesheet(selected=False))

        title_bar = QtWidgets.QLabel(title)
        title_bar.setObjectName("PluginNodeTitle")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(title_bar)
        lay.addWidget(content, 1)

        self._title_label = title_bar

        # 设置面板改色 → 所有已开的卡片跟着重画
        theme.theme.changed.connect(self._on_theme_changed)

    # ── API ──────────────────────────────────
    @property
    def title_text(self) -> str:
        return self._title

    def set_title(self, title: str):
        self._title = title
        self._title_label.setText(title)
        self.updateGeometry()

    def title_height(self) -> int:
        """标题栏真实高度（含 stylesheet padding）。

        画布判定"点在不在标题栏"必须用这个，不能用常量——
        常量和实际绘制高度不一致时，会出现"看着是标题栏但被当内容区"的错位。
        """
        h = self._title_label.height()
        if h > 0:
            return h
        return TITLE_HEIGHT

    def set_plugin_id(self, plugin_key: tuple):
        """存 (dcc, id) 复合键。

        用普通属性而不是 setProperty：QVariant 其实能装 Python tuple
        （PySide6 会包成 PyObject 变体），但动态属性每次读写都要过一遍
        元对象系统，这里只是内部记一个键，普通属性更直接。
        """
        self._plugin_key = tuple(plugin_key)

    def plugin_id(self) -> tuple:
        return getattr(self, "_plugin_key", ())

    def content(self) -> QtWidgets.QWidget:
        return self._content

    def set_selected(self, on: bool):
        if on == self._selected:
            return
        self._selected = on
        self.setStyleSheet(self._stylesheet(selected=on))

    def is_selected(self) -> bool:
        return self._selected

    def dcc(self) -> str:
        return self._dcc

    def set_dcc(self, dcc: str):
        """换 DCC 配色（当前只在测试/重建时用，正常走 __init__）。"""
        dcc = dcc or ""
        if dcc == self._dcc:
            return
        self._dcc = dcc
        self.setStyleSheet(self._stylesheet(selected=self._selected))

    def sizeHint(self) -> QtCore.QSize:
        h = self._content.sizeHint().height() + 32
        return QtCore.QSize(NODE_WIDTH, max(h, 80))

    # ── private ──────────────────────────────
    def _on_theme_changed(self):
        self.setStyleSheet(self._stylesheet(selected=self._selected))

    def _stylesheet(self, selected: bool) -> str:
        """卡片样式。

        ⚠️ 只有标题栏的 `background` 跟 DCC 走。为什么只动这一处：
           - 内容区固定 #2d2d2d：插件 widget 内部有大量自己的 QLabel/QSS，
             给内容区染色会顺着继承链污染它们（文字/边框全跑偏）
           - `padding` 一个字都不改：`title_height()` 靠实际绘制高度，
             一改高度画布的"点标题栏才开始拖动"判定就整体错位
        """
        border = theme.theme.border_color(self._dcc, selected)
        # 未登记 dcc 时回落成原来的中性灰，行为与加配色前完全一致
        title_bg = theme.theme.title_color(self._dcc)
        title_fg = theme.theme.title_text_color(self._dcc)
        # 选中时标题栏下沿也提亮，让"整张卡被框住"的观感成立
        title_sep = border if selected else "#3e3e3e"
        return f"""
            #PluginNode {{
                background: #2d2d2d;
                border: 1px solid {border};
                border-radius: 4px;
            }}
            #PluginNodeTitle {{
                background: {title_bg};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border-bottom: 1px solid {title_sep};
                color: {title_fg};
                font-size: 12px;
                /* padding 4px→7px：标题栏从21px 长到 ~28px，
                   给鼠标一个更宽的拖动把手（判定用title_height() 自适应）
                   ⚠️ 这行是拖动判定的一部分，改色时别顺手改它 */
                padding: 7px 10px;
            }}
        """
