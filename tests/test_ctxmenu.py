"""
右键菜单 + 搜索浮层失焦关闭的**端到端**测试（2026-10-19 修 bug 专用）。

⚠️ 为什么这个文件单独存在，而不能塞进 test_canvas_align.py：

   上一版 test_canvas_align 直接调 `contextMenuEvent()` 来测菜单启用状态，
   **绕过了 mousePressEvent** —— 而 bug 恰恰出在 mousePressEvent：
   右键漏进 super() → QGraphicsView 默认清掉全部选中 →
   菜单读到 len(sel)==0 → 全灰。
   菜单"能弹出"这件事测试通过了，"弹之前发生了什么"一条都没测。

   所以这里的铁律：**必须走 mousePressEvent → contextMenuEvent 完整路径**，
   不能直接调 contextMenuEvent，否则又是在测一个假的世界。

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe test_ctxmenu.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from ui.canvas import Canvas
from ui.plugin_widget import BasePluginWidget
from ui.plugin_loader import PluginInfo

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def _factory(_info):
    class _W(QtWidgets.QWidget, BasePluginWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(60)
            QtWidgets.QVBoxLayout(self).addWidget(QtWidgets.QLabel("x"))
    return _W()


def _open(cv, dcc, pid, x, y, w=320, h=80):
    info = PluginInfo(id=pid, name=pid, dcc=dcc, icon="",
                      widget_factory_path="widget.create_widget")
    cv.open_plugin(info, x=x, y=y)
    k = (dcc, pid)
    cv._proxies[k].resize(w, h)
    return k


def _rmb(cv, scene_x, scene_y):
    """模拟一次真实右键（先 press，再触发 contextMenuEvent）。"""
    ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        cv.mapFromScene(QtCore.QPointF(scene_x, scene_y)),
        QtCore.Qt.RightButton, QtCore.Qt.RightButton, QtCore.Qt.NoModifier)
    cv.mousePressEvent(ev)


def _menu_enabled(cv):
    """不真正 exec（会阻塞），只复现 contextMenuEvent 的判定逻辑。"""
    sel = cv._ctx_sel_snapshot
    enable = (not cv._locked) and len(sel) >= 2
    return enable, len(sel)


def main():
    # ── 1. 右键不再清选中（本 bug 的核心）────────────
    print("1. ★ 右键不清选中（这是菜单全灰的根因）")
    host = QtWidgets.QWidget()
    host.resize(1000, 700)
    lay = QtWidgets.QVBoxLayout(host)
    c = Canvas(_factory)
    lay.addWidget(c)
    host.show()
    APP.processEvents()

    k1 = _open(c, "maya", "a", 0, 0)
    k2 = _open(c, "maya", "b", 400, 300)
    k3 = _open(c, "max", "c", 100, 600, h=120)
    APP.processEvents()

    c.select_only(k1)
    for k in (k2, k3):
        c._proxies[k].setSelected(True)
    APP.processEvents()
    check("前置：选中 3 个", len(c.selected_ids()) == 3,
          len(c.selected_ids()))

    # 右键按在**空白**处（这是旧版清掉选中的地方）
    _rmb(c, 900, 100)
    APP.processEvents()
    check("★★ 右键按空白后选中仍是 3（旧版会变 0）",
          len(c.selected_ids()) == 3, len(c.selected_ids()))

    # 右键按在**节点**上
    _rmb(c, 500, 330)
    APP.processEvents()
    check("★★ 右键按节点后选中仍是 3",
          len(c.selected_ids()) == 3, len(c.selected_ids()))

    # 左键点空白**仍然**要清选中（用户保留了这个习惯）
    _rmb(c, 900, 100)
    lb = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress, c.mapFromScene(QtCore.QPointF(950, 120)),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    c.mousePressEvent(lb)
    APP.processEvents()
    check("★ 左键点空白仍然取消选中（用户保留的习惯）",
          len(c.selected_ids()) == 0, len(c.selected_ids()))

    # ── 2. 走完整路径后菜单应该可用 ──────────────────
    print("\n2. 完整路径（右键 → 菜单启用状态）")
    c.select_only(k1)
    c._proxies[k2].setSelected(True)
    APP.processEvents()
    _rmb(c, 900, 100)
    APP.processEvents()
    enable, n = _menu_enabled(c)
    check("★ 快照里选中了 2 个", n == 2, n)
    check("★★ 右键后对齐项应该可用（旧版这里必灰）", enable is True,
          (enable, n))

    c.select_only(k1)
    APP.processEvents()
    _rmb(c, 900, 100)
    enable1, n1 = _menu_enabled(c)
    check("单选时置灰", enable1 is False and n1 == 1, (enable1, n1))

    c.clear_selection()
    APP.processEvents()
    _rmb(c, 900, 100)
    enable0, n0 = _menu_enabled(c)
    check("零选时置灰", enable0 is False and n0 == 0, (enable0, n0))

    # ── 3. 锁定 ──────────────────────────────────────
    print("\n3. 锁定时对齐项不可用")
    c.set_locked(False)
    c.select_only(k1)
    c._proxies[k2].setSelected(True)
    APP.processEvents()
    _rmb(c, 900, 100)
    APP.processEvents()
    c.set_locked(True)          # 锁定会清选中
    APP.processEvents()
    before = {k: c._proxies[k].pos() for k in c._proxies}
    c._on_align("align_left")
    check("★ 锁定时位置不变",
          all(c._proxies[k].pos() == before[k] for k in before),
          {k: (before[k], c._proxies[k].pos()) for k in before})
    c.set_locked(False)

    # ── 4. 对齐真的生效（走菜单路径）─────────────────
    print("\n4. 通过菜单路径真正执行一次对齐")
    c.select_only(k1)
    c._proxies[k2].setSelected(True)
    APP.processEvents()
    _rmb(c, 900, 100)
    APP.processEvents()
    # 摆成不同 y
    c._proxies[k1].setPos(QtCore.QPointF(0, 0))
    c._proxies[k2].setPos(QtCore.QPointF(500, 300))
    c._on_align("align_top")
    check("★ 对齐真的生效（y 齐平）",
          c._proxies[k1].pos().y() == c._proxies[k2].pos().y() == 0,
          (c._proxies[k1].pos(), c._proxies[k2].pos()))

    # ── 5. 搜索浮层失焦关闭 ─────────────────────────
    print("\n5. ★ 失焦关闭（焦点离开浮层才关）")
    c2 = Canvas(_factory)
    lay.addWidget(c2)
    APP.processEvents()
    c2.setFocus()
    APP.processEvents()
    c2.show_search()
    APP.processEvents()
    check("浮层已开", not c2._search.isHidden())
    check("★ 焦点在输入框（按 Ctrl+F 能直接打字）",
          c2._search._edit.hasFocus())

    # 5a. 点浮层内部（结果列表）→ 不该关
    n_before = c2._search.isHidden()
    c2._search._list.setFocus(QtCore.Qt.OtherFocusReason)
    APP.processEvents()
    check("★★ 焦点进结果列表时不关闭",
          c2._search.isHidden() == n_before, c2._search.isHidden())

    # 5b. 焦点到浮层之外 → 关闭
    c2._search.focus_input()
    APP.processEvents()
    check("前置：焦点回到输入框", c2._search._edit.hasFocus())
    c2.setFocus()                      # 焦点跳回画布 = 浮层之外
    APP.processEvents()
    check("★★★ 焦点离开浮层后自动关闭",
          c2._search.isHidden(), "浮层没关")
    check("★ 关闭后焦点在画布（a/f/方向键/Del 仍可用）",
          c2.hasFocus())

    # 5c. 浮层没开时，焦点变化不该凭空把它打开
    c3 = Canvas(_factory)
    lay.addWidget(c3)
    APP.processEvents()
    c2.setFocus()
    APP.processEvents()
    check("★ 未打开时焦点乱跳不会凭空显示浮层",
          c3._search is None or c3._search.isHidden(),
          c3._search)

    host.close()
    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())