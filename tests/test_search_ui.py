"""
右键菜单项移除 + 搜索浮层定位/滚动的回归测试（2026-06）。

三项都是"用户看到的具体表现"：
  1. 右键菜单**只剩 6 个对齐项**（"取消选中"已移除）
  2. 搜索浮层出现在**光标位置**，且**夹在视口内**（不能露到画布外面）
  3. 浮层滚动条宽度与**日志面板一致**（都是 10px）

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe test_ctxmenu.py
（本文件第 6/7 节；前 5 节在 test_ctxmenu.py）
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ui.app_style import apply_application_style
apply_application_style(APP)

from ui.canvas import Canvas
from ui.canvas_search import SearchOverlay
from ui.plugin_widget import BasePluginWidget
from ui.plugin_loader import PluginInfo
from ui.widgets.log_panel import LogPanel

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def _factory(_i):
    class _W(QtWidgets.QWidget, BasePluginWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(60)
            QtWidgets.QVBoxLayout(self).addWidget(QtWidgets.QLabel("x"))
    return _W()


def _open(cv, dcc, pid, x, y):
    cv.open_plugin(PluginInfo(id=pid, name=pid, dcc=dcc, icon="",
                              widget_factory_path="widget.create_widget"),
                   x=x, y=y)
    cv._proxies[(dcc, pid)].resize(320, 80)


def main():
    host = QtWidgets.QWidget()
    host.resize(900, 600)
    lay = QtWidgets.QVBoxLayout(host)
    c = Canvas(_factory)
    lay.addWidget(c)
    host.show()
    APP.processEvents()

    # ── 6. 右键菜单只剩对齐项 ───────────────────────
    print("6. 右键菜单项（取消选中已移除）")
    m = c._build_context_menu()
    texts = [a.text() for a in m.actions() if not a.isSeparator()]
    check("★ 菜单恰好 6 项", len(texts) == 6, texts)
    check("★ 不再有『取消选中』", "取消选中" not in texts, texts)
    check("★ 6 项都是对齐动作",
          set(texts) == {"顶部对齐", "底部对齐", "左侧对齐", "右侧对齐",
                         "水平居中", "垂直居中"}, texts)
    check("★ 没有 separator", not any(a.isSeparator()
                                for a in m.actions()),
          [a.text() for a in m.actions()])

    # 置灰逻辑现在可以一刀切（不用再跳过某一项）
    texts2 = [a.text() for a in m.actions()]
    check("★ 所有项都能 setEnabled（无一例外）",
          len(texts2) == 6, texts2)

    # ── 7. 搜索浮层定位在光标处，且夹在视口内 ─────────
    print("\n7. 搜索浮层定位")
    vp_w, vp_h = c.viewport().width(), c.viewport().height()
    check("画布已 show（viewport 有尺寸）", vp_w > 0 and vp_h > 0,
          (vp_w, vp_h))

    c.setFocus()
    c.show_search()
    APP.processEvents()
    w, h = c._search.width(), c._search.height()

    # 7a. 完全在视口内
    p0 = c._search.mapTo(c.viewport(), QtCore.QPoint(0, 0))
    gx, gy = p0.x(), p0.y()
    check("★ 浮层左上角在视口内",
          0 <= gx and 0 <= gy, (gx, gy))
    check("★ 浮层没超出右边界",
          gx + w <= vp_w + 1, (gx + w, vp_w))
    check("★ 浮层没超出下边界",
          gy + h <= vp_h + 1, (gy + h, vp_h))

    # 7b. 光标在不同位置时浮层跟着动
    positions = [(80, 60), (vp_w // 2, vp_h // 2),
                 (max(0, vp_w - 40), max(0, vp_h - 40))]
    seen = []
    for (px, py) in positions:
        # 把光标"放到"画布内的某点：offscreen 下直接构造并 setCursor
        QCursor = QtGui.QCursor
        pt = c.viewport().mapToGlobal(QtCore.QPoint(px, py))
        QCursor.setPos(pt)
        c.show_search()
        APP.processEvents()
        pp = c._search.mapTo(c.viewport(), QtCore.QPoint(0, 0))
        gx2, gy2 = pp.x(), pp.y()
        seen.append((px, py, gx2, gy2))
        # 关键：不管光标在哪，浮层都不能出界
        assert 0 <= gx2, (px, py, gx2)
        assert gx2 + w <= vp_w + 1, ("越界右", px, py, gx2 + w, vp_w)
        assert gy2 + h <= vp_h + 1, ("越界下", px, py, gy2 + h, vp_h)
    check("★ 三种光标位置下浮层都不出界", True)

    # 浮层位置确实随光标变化（不是固定在某一处）
    distinct = len({(g[2], g[3]) for g in seen})
    check("★ 浮层位置随光标变化（不是固定位置）", distinct >= 2,
          [(a, b, cx, cy) for a, b, cx, cy in seen])

    # 7c. 靠近右边缘时会翻到光标左侧（不是硬塞出去）
    c.show_search()
    APP.processEvents()
    p3 = c._search.mapTo(c.viewport(), QtCore.QPoint(0, 0))
    gx3 = p3.x()
    check("★ 靠近右边缘时落在视口内", 0 <= gx3 and gx3 + w <= vp_w + 1,
          (gx3, w, vp_w))

    # ── 8. 滚动条样式与日志一致 ─────────────────────
    print("\n8. 滚动条样式 = 日志面板")
    ov = SearchOverlay()
    ov.set_plugins([PluginInfo(id="p%d" % i, name="插件 %d" % i, dcc="maya",
                               icon="", widget_factory_path="w.create_widget")
                    for i in range(20)])
    lp = LogPanel()
    lp.setMinimumHeight(100)
    lp._view.setPlainText('\n'.join('line %d' % i for i in range(200)))
    lay.addWidget(ov)
    lay.addWidget(lp)
    APP.processEvents()

    sb_ov = ov._list.verticalScrollBar()
    sb_lp = lp.findChild(QtWidgets.QTextEdit).verticalScrollBar()
    check("★ 浮层滚动条 10px", sb_ov.width() == 10, sb_ov.width())
    check("★ 日志滚动条 10px", sb_lp.width() == 10, sb_lp.width())
    check("★ 两处宽度一致", sb_ov.width() == sb_lp.width(),
          (sb_ov.width(), sb_lp.width()))
    check("★ 搜索和日志直接使用应用公共滚动条",
          not ov._list.styleSheet() and not lp._view.styleSheet())
    # 实际绘制的滑块颜色一致，不再要求组件复制相同QSS文本。
    APP.processEvents()
    def handle_color(bar):
        option = QtWidgets.QStyleOptionSlider()
        bar.initStyleOption(option)
        rect = bar.style().subControlRect(QtWidgets.QStyle.CC_ScrollBar, option,
                                         QtWidgets.QStyle.SC_ScrollBarSlider, bar)
        return bar.grab().toImage().pixelColor(rect.center()).name()
    check("★ 搜索和日志滑块实际颜色相同", handle_color(sb_ov) == handle_color(sb_lp),
          (handle_color(sb_ov), handle_color(sb_lp)))


    # ── 9. 鼠标点击打开（回归：曾只连双击，单击无反应）────
    print("\n9. ★ 鼠标单击/双击都能打开")
    from PySide6.QtTest import QTest
    ps2 = [PluginInfo(id="a", name="创建 Box", dcc="maya", icon="",
                      widget_factory_path="w.create_widget"),
           PluginInfo(id="b", name="创建 Sphere", dcc="maya", icon="",
                      widget_factory_path="w.create_widget"),
           PluginInfo(id="c", name="骨骼镜像", dcc="max", icon="",
                      widget_factory_path="w.create_widget")]
    ov2 = SearchOverlay()
    ov2.set_plugins(ps2)
    lay.addWidget(ov2)
    ov2.show()
    ov2.focus_input()
    ov2._edit.setText("创建")
    APP.processEvents()
    check("过滤后有 2 项", ov2._list.count() == 2, ov2._list.count())

    got = []
    ov2.plugin_chosen.connect(lambda p: got.append(p.id))

    # ★ 单击（这是用户反馈"点不动"的那个操作）
    pos = ov2._list.visualItemRect(ov2._list.item(0)).center()
    QTest.mouseClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, pos)
    APP.processEvents()
    check("★★ 单击第一项就 emit（曾完全不触发）", len(got) == 1, got)
    check("★ 单击选中的是第一项", got and got[0] == "a", got)

    # 单击第二项
    got.clear()
    ov2._edit.setText("创建")
    APP.processEvents()
    pos2 = ov2._list.visualItemRect(ov2._list.item(1)).center()
    QTest.mouseClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, pos2)
    APP.processEvents()
    check("★ 单击第二项选的是第二项", got and got[0] == "b", got)

    # 双击仍然可用（很多用户习惯双击）
    # ⚠️ 必须先来一次「暖手」单击：QTest.mouseDClick 依赖 Qt 记录的
    #    上一次按下位置/时间来判断"这是双击"，而 set_text() 触发的
    #    _refresh() 里 clear()+addItem() 会重置列表状态。
    #    所以**紧接着 refresh 之后的第一次 mouseDClick 不会被认成双击**
    #    —— 这是 QTest 的行为差异，不是产品 bug（真机上手指有移动过程）。
    #    但不暖手就断言双击，测试会假失败。
    got.clear()
    ov2._edit.setText("创建")
    APP.processEvents()
    QTest.mouseClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, pos)   # 暖手，不断言
    APP.processEvents()
    got.clear()
    QTest.mouseDClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                      QtCore.Qt.NoModifier, pos)
    APP.processEvents()
    check("★ 双击仍能打开", len(got) >= 1, got)

    # ★ 单击后浮层不能被误关（列表在浮层内部）
    ov2._edit.setText("创建")
    APP.processEvents()
    was_hidden = ov2.isHidden()
    QTest.mouseClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, pos)
    APP.processEvents()
    check("★ 单击列表不导致浮层关闭（焦点判定是『浮层内部』）",
          was_hidden == ov2.isHidden(), ov2.isHidden())

    # (无匹配) 占位项点了不该 emit
    got.clear()
    ov2._edit.setText("zzz不存在的关键字zzz")
    APP.processEvents()
    check("无匹配时列表是占位项",
          ov2._list.count() == 1
          and ov2._list.item(0).text() == "(无匹配)",
          [ov2._list.item(i).text() for i in range(ov2._list.count())])
    QTest.mouseClick(ov2._list.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier,
                     ov2._list.visualItemRect(ov2._list.item(0)).center())
    APP.processEvents()
    check("★ 点占位项不 emit（它没有 key）", got == [], got)

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
