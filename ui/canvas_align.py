"""
canvas_align.py — 画布节点对齐算法。

⚠️ 这里对齐的是**节点在画布上的位置**，不是在 DCC 场景里对齐物体。
   两者完全不同的东西：前者只是 setPos，后者要走 bridge 发 action。

对齐基准（2026-10-19 用户拍板）：**方案 A —— 按最大选区对齐**。
   即所有选中节点对齐到它们合起来的最外边界。
   理由：不需要用户先指定"基准节点"，多选完直接点一下就能用。

六个操作统一为「求一个基准值 + 给每个节点算偏移」：
  顶部对齐   → 所有节点的 y 最小值
  底部对齐   → 所有节点底边的最大值
  左侧对齐   → 所有节点的 x 最小值
  右侧对齐   → 所有节点右边界的最大值
  水平对齐   → 让所有节点的**中心 y** 一致（用整体中心，不逐个对齐）
  垂直对齐   → 让所有节点的**中心 x** 一致

⚠️ 「水平对齐」这个名字有歧义，容易理解成"沿水平线排"（那是顶部/底部对齐）。
   这里的定义是"水平居中对齐"，即上下方向上中心线齐平。
   菜单上用「垂直居中」「水平居中」两个名字，避免歧义。
"""

from typing import Dict, Iterable, List, Tuple

from PySide6 import QtCore

# 菜单标识 → 中文名。注意「水平居中」是上下方向齐平，「垂直居中」是左右方向齐平。
ALIGN_ACTIONS = (
    ("align_top", "顶部对齐"),
    ("align_bottom", "底部对齐"),
    ("align_left", "左侧对齐"),
    ("align_right", "右侧对齐"),
    ("align_hcenter", "水平居中"),
    ("align_vcenter", "垂直居中"),
)


def _bounds(proxy):
    """节点的场景坐标矩形（不含 z 序影响）。"""
    return proxy.sceneBoundingRect()


def align(proxies: Iterable, action: str) -> bool:
    """对一组 proxy 执行对齐。返回是否真的动了。

    ★ 只改 x/y，不动 z —— 对齐不改变"谁在最前"的语义。
    ★ 不管单个还是多个：单选时对齐到自己，等于不动，
      返回 False 让调用方可以据此决定要不要给用户反馈。
    """
    items = [p for p in proxies if p is not None]
    if len(items) < 2:
        # ★ 少于 2 个节点没有"对齐"可言 —— 对齐到自己 = 不动。
        #   返回 False，菜单置灰，不给用户"点了没反应"的困惑。
        return False

    rects = [_bounds(p) for p in items]
    left = min(r.left() for r in rects)
    right = max(r.right() for r in rects)
    top = min(r.top() for r in rects)
    bottom = max(r.bottom() for r in rects)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0

    moved = False
    for p, r in zip(items, rects):
        nx, ny = p.pos().x(), p.pos().y()
        if action == "align_top":
            ny += top - r.top()
        elif action == "align_bottom":
            ny += bottom - r.bottom()
        elif action == "align_left":
            nx += left - r.left()
        elif action == "align_right":
            nx += right - r.right()
        elif action == "align_hcenter":
            ny += cy - r.center().y()
        elif action == "align_vcenter":
            nx += cx - r.center().x()
        else:
            return False
        if nx != p.pos().x() or ny != p.pos().y():
            p.setPos(QtCore.QPointF(nx, ny))
            moved = True
    return moved