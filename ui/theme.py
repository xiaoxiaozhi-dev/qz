"""
theme.py — DCC 配色 + 图标工厂（全项目唯一配色来源）。

为什么单独一个模块：
    卡片标题栏、资源树图标、搜索浮层图标、设置面板选色，
    四处用的是**同一份** DCC 颜色。分散写死会出现"设置面板改了、
    树图标不变"这种半生效状态。

颜色存 `~/.qz-tool/theme.json`：
    ⚠️ 不写 `config.json` —— exe 同级目录可能只读，且全项目没有
       写 config 的代码路径（`_load_config()` 异常全吞 = 静默回落默认值）。
    ⚠️ 不写 `layout.json` —— `layout_state._normalize()` 是白名单，
       未列入的字段保存后被静默抹掉。

⚠️ 默认色只用在**卡片标题栏**。内容区固定 #2d2d2d 不跟着变：
   插件 widget 内部有一堆自己的 QLabel/QSS，染色会顺着继承链污染它们。

⚠️ 图标字母必须是**不同字形**（M / X），不能同形换色：
   颜色成了唯一区分手段时，红绿色弱（约 8% 男性）就分不出 DCC 了。
"""

import json
import os
import paths
from dcc_bridge import DCC_PORTS

from PySide6 import QtCore, QtGui

# 与 plugin_tree.DCC_ORDER 一致；这里只用来定默认值，不做业务判断
DCC_ORDER = tuple(DCC_PORTS)

# 标题栏默认底色（深色主题下调过：既明显带色相，又不至于让 #cccccc 文字发灰）
DEFAULT_TITLE = {
    "maya": "#0f6e56",   # 青绿
    "blender": "#9a4818",
    "max": "#185fa5",    # 蓝
}

# 未登记的 dcc（理论上不存在，防手写 widget 传入空串）
NEUTRAL_TITLE = "#262626"

# 图标字形：M / X —— 形状差异是色弱用户的唯一线索
DCC_GLYPH = {"maya": "M", "max": "X", "blender": "B"}

TITLE_TEXT = "#e6e6e6"
CARD_BORDER = "#3e3e3e"


# ── 颜色小工具 ────────────────────────────────
def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _to_rgb(color: str):
    c = QtGui.QColor(color)
    if not c.isValid():
        c = QtGui.QColor(NEUTRAL_TITLE)
    return c.red(), c.green(), c.blue()


def lighten(color: str, factor: float) -> str:
    """向白色混合（factor=0 原色，1 纯白）。"""
    r, g, b = _to_rgb(color)
    return "#%02x%02x%02x" % (
        _clamp(r + (255 - r) * factor),
        _clamp(g + (255 - g) * factor),
        _clamp(b + (255 - b) * factor),
    )


def is_light(color: str) -> bool:
    """感知亮度够不够浅 —— 浅色下标题文字要转深，否则看不见。"""
    r, g, b = _to_rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150


# ── 配置文件 ──────────────────────────────────
def data_dir() -> str:
    """Portable personal data directory, shared with layout and settings."""
    return paths.user_data_dir()


def theme_path() -> str:
    return paths.user_state_path('theme.json')


# ── 单例 ──────────────────────────────────────
class _Theme(QtCore.QObject):
    """配色单例。改色后 emit changed，各处自己重画。"""

    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._title = dict(DEFAULT_TITLE)
        self._icon_cache = {}
        self.load()

    # ── 读写 ──────────────────────────────────
    def load(self):
        self._title = dict(DEFAULT_TITLE)
        path = theme_path()
        if not os.path.isfile(path):
            return
        try:
            # ⚠️ utf-8-sig：记事本存过的文件带 BOM，
            #    直接 json.load 会 JSONDecodeError，表现成"配色全部回默认"
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:
            return
        colors = (data or {}).get("dcc_title") or {}
        for dcc, value in colors.items():
            if dcc not in DEFAULT_TITLE:
                continue
            if QtGui.QColor(str(value)).isValid():
                self._title[dcc] = str(value)
        self._icon_cache.clear()

    def save(self):
        path = theme_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "dcc_title": self._title},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            # 写不进去不影响当前会话已生效的颜色
            pass

    # ── 查询 ──────────────────────────────────
    def title_color(self, dcc: str) -> str:
        return self._title.get(dcc, NEUTRAL_TITLE)

    def is_default(self, dcc: str) -> bool:
        return self.title_color(dcc).lower() == DEFAULT_TITLE.get(
            dcc, NEUTRAL_TITLE).lower()

    def border_color(self, dcc: str, selected: bool) -> str:
        """选中描边 = 本 DCC 色的提亮版。

        ⚠️ 原来选中统一用 #0e639c（蓝）。max 的标题栏本身就是蓝的，
           两者会糊成一片；跟着 DCC 走还有个好处 ——
           选中状态下仍然能一眼看出这张卡片属于哪个 DCC。
        """
        if not selected:
            return CARD_BORDER
        return lighten(self.title_color(dcc), 0.45)

    def title_text_color(self, dcc: str) -> str:
        return "#1e1e1e" if is_light(self.title_color(dcc)) else TITLE_TEXT

    # ── 修改 ──────────────────────────────────
    def set_title_color(self, dcc: str, color: str):
        if dcc not in DEFAULT_TITLE:
            return
        c = QtGui.QColor(str(color))
        if not c.isValid():
            return
        value = c.name()          # 统一成 #rrggbb 小写
        if value == self.title_color(dcc):
            return
        self._title[dcc] = value
        self._icon_cache.clear()
        self.save()
        self.changed.emit()

    def reset(self):
        self._title = dict(DEFAULT_TITLE)
        self._icon_cache.clear()
        self.save()
        self.changed.emit()

    # ── 图标 ──────────────────────────────────
    def icon(self, dcc: str, size: int = 16) -> QtGui.QIcon:
        """圆角方块 + 字母，底色随 DCC。

        运行时用 QPainter 画而不是放 SVG：
          - 颜色要能跟着设置面板变，SVG 得每次重写文件
          - 16px 下 `<text>` 依赖系统字体，不同机器渲染结果不一致
        QIcon 带缓存；每次改色/加载配置都会 clear。
        """
        key = (dcc, int(size))
        hit = self._icon_cache.get(key)
        if hit is not None:
            return hit
        icon = self._render_icon(dcc, int(size))
        self._icon_cache[key] = icon
        return icon

    def _render_icon(self, dcc: str, size: int) -> QtGui.QIcon:
        # setDevicePixelRatio(2)：高 DPI 下按 2 倍像素画，否则字母边缘发虚
        dpr = 2
        pm = QtGui.QPixmap(size * dpr, size * dpr)
        pm.fill(QtCore.Qt.transparent)
        pm.setDevicePixelRatio(dpr)

        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

            rect = QtCore.QRectF(0.5, 0.5, size - 1, size - 1)
            radius = max(2.0, size * 0.22)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(self.title_color(dcc)))
            p.drawRoundedRect(rect, radius, radius)

            glyph = DCC_GLYPH.get(dcc)
            if glyph:
                font = QtGui.QFont()
                font.setBold(True)
                font.setPixelSize(max(9, int(size * 0.74)))
                p.setFont(font)
                p.setPen(QtGui.QColor(self.title_text_color(dcc)))
                # 用字形实际包围盒居中：drawText(rect, AlignCenter) 会算上
                # 行高与 descent，单字母看着偏下 1~2px
                fm = QtGui.QFontMetricsF(font)
                br = fm.boundingRect(glyph)
                x = rect.x() + (rect.width() - br.width()) / 2.0 - br.left()
                y = rect.y() + (rect.height() - br.height()) / 2.0 - br.top()
                p.drawText(QtCore.QPointF(x, y), glyph)
        finally:
            p.end()

        icon = QtGui.QIcon()
        icon.addPixmap(pm, QtGui.QIcon.Normal, QtGui.QIcon.Off)
        return icon


# 模块级单例：import theme 后直接用 theme.title_color(...)
theme = _Theme()


def dcc_icon(dcc: str, size: int = 16) -> QtGui.QIcon:
    """便捷入口（树/搜索浮层共用）。"""
    return theme.icon(dcc, size)
