"""
z 序显式化回归测试（2026-10-19）。

背景：原来`_Proxy.itemChange` 里有 `if on: self.bring_to_front()`，
但 `itemChange` 拿不到"这次选中从哪来"—— 单击 / Ctrl 加选 / 框选 /
Ctrl+A 对它都是同一个 ItemSelectedChange。后果是**框选时 Qt 逐个设选中态，
每个都自增一次 z**，A→B→C→D 被扫过就依次抬到最前，叠放顺序整个翻转。

现在置顶改成**显式行为**，只有"用户明确要用这个节点"才调`_bump_z`：
  置顶：单击标题栏 / 点内容区 / 打开新节点（含 Ctrl+F 搜索打开）
  不置顶：框选 / Ctrl 加选 / Ctrl+A 全选

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe test_zorder.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from ui.canvas import Canvas
from ui.plugin_widget import BasePluginWidget
from ui.plugin_loader import PluginInfo

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
    return _W()


def _build():
    host = QtWidgets.QWidget()
    host.resize(1100, 800)
    lay = QtWidgets.QVBoxLayout(host)
    c = Canvas(_factory)
    lay.addWidget(c)
    host.show()
    APP.processEvents()
    # 依次打开 → z 依次 1,2,3,4
    for i, (pid, x, y) in enumerate([("a", 0, 0), ("b", 300, 0),
                                     ("c", 600, 0), ("d", 150, 300)]):
        c.open_plugin(PluginInfo(id=pid, name=pid, dcc="maya", icon="",
                                 widget_factory_path="w.create_widget"),
                      x=x, y=y)
        c._proxies[("maya", pid)].resize(320, 80)
        APP.processEvents()
    return host, c


def _zs(c):
    return [c._proxies[("maya", p)].zValue() for p in "abcd"]


def _keys():
    return [("maya", p) for p in "abcd"]


def main():
    # ── 1. itemChange 不再置顶（根因层）───────────────
    print("1. ★ itemChange 里没有置顶（根因已移除）")
    from ui.canvas import _Proxy
    import inspect
    src = inspect.getsource(_Proxy.itemChange)
    # ⚠️ 必须用 tokenize 剥掉 docstring —— 注释里为了记录坑，
    #    恰好写着"原来这里是 if on: self.bring_to_front()"。
    #    直接 grep 会被自己的说明判为失败（这个坑踩过好几次了）。
    import io as _io
    import tokenize
    def _code_only(fn):
        s = inspect.getsource(fn)
        out = []
        for tok in tokenize.generate_tokens(_io.StringIO(s).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return "".join(out)
    code = _code_only(_Proxy.itemChange)
    check("★ itemChange 代码里不含 bring_to_front",
          "bring_to_front" not in code, code)
    check("★ itemChange 代码里不含 _bump_z",
          "_bump_z" not in code, code)
    check("★ itemChange 仍负责边框颜色",
          "set_selected" in code, code)

    # ── 2. 框选不改变 z序 ────────────────────────────
    print("\n2. ★ 框选完全不改变 z 序")
    host, c = _build()
    before = _zs(c)
    check("前置：初始 z 递增", before == sorted(before), before)

    # 框选的本质就是 Qt 逐个设选中态 —— 这里直接模拟它
    for k in _keys():
        c._proxies[k].setSelected(True)
    APP.processEvents()
    after = _zs(c)
    check("★★ 逐个选中后 z 一个都没动", after == before,
          (before, after))
    check("★★ 4 个都选中了（确认选中本身生效了）",
          len(c.selected_ids()) == 4, len(c.selected_ids()))

    # 反向对照：手动逐个 _bump_z（模拟旧 itemChange 的副作用）确实会抬 z。
    # 这条证明"框选时 z 被抬高"这件事本身是可能发生的 ——
    # 修复前itemChange 就在做这件事，实测能把 z 从 [1,2,3,4] 抬到 [5,6,7,8]。
    host2, c2 = _build()
    base2 = _zs(c2)
    for k in _keys():
        c2._bump_z(c2._proxies[k])
    APP.processEvents()
    raised2 = _zs(c2)
    check("（反向对照）逐个 _bump_z 会把 z 全部抬高 = 旧行为",
          raised2 != base2 and min(raised2) > max(base2),
          (base2, raised2))

    # ── 3. Ctrl+A 全选不置顶 ─────────────────────────
    print("\n3. ★ Ctrl+A 全选不置顶")
    host, c = _build()
    before = _zs(c)
    c.select_all()
    APP.processEvents()
    check("全选了 4 个", len(c.selected_ids()) == 4,
          len(c.selected_ids()))
    check("★★ z 未改变", _zs(c) == before, (before, _zs(c)))

    # ── 4. Ctrl 加选不置顶 ───────────────────────────
    print("\n4. ★ Ctrl + 点击加选不置顶")
    host, c = _build()
    before = _zs(c)
    c.clear_selection()
    APP.processEvents()
    c.toggle_select(("maya", "a"))
    APP.processEvents()
    c.toggle_select(("maya", "c"))
    APP.processEvents()
    check("加选了 2 个", len(c.selected_ids()) == 2,
          c.selected_ids())
    check("★★ z 未改变", _zs(c) == before, (before, _zs(c)))

    # 取消选中也不该动 z
    c.toggle_select(("maya", "a"))
    APP.processEvents()
    check("★ 取消选中后 z 仍未变", _zs(c) == before, _zs(c))

    # ── 5. 单击标题栏 / select_only(bump_z=True) 置顶 ─
    print("\n5. ★ 单击（bump_z=True）仍然置顶 —— 不能把功能一起删掉")
    host, c = _build()
    before = _zs(c)
    c.select_only(("maya", "b"), bump_z=True)
    APP.processEvents()
    check("只选了 b", c.selected_ids() == [("maya", "b")],
          c.selected_ids())
    check("★★ b 被抬到最前", _zs(c)[1] == max(_zs(c)),
          (before, _zs(c)))
    check("★ 其余相对顺序不变",
          [_zs(c)[i] for i in (0, 2, 3)] == [before[0], before[2], before[3]],
          (before, _zs(c)))

    # ── 6. select_only 默认不置顶 ─────────────────────
    print("\n6. select_only 默认不置顶（批量场景安全）")
    host, c = _build()
    before = _zs(c)
    c.select_only(("maya", "c"))
    APP.processEvents()
    check("默认调用不抬 z", _zs(c) == before, (before, _zs(c)))

    # ── 7. 打开新节点（含 Ctrl+F 搜索打开）置顶 ──────
    print("\n7. ★ 打开节点置顶（含 Ctrl+F 搜索路径）")
    host, c = _build()
    before_max = max(_zs(c))
    # 搜索打开走的就是 open_plugin，这里直接验它
    info = PluginInfo(id="e", name="搜索打开的", dcc="maya", icon="",
                      widget_factory_path="w.create_widget")
    c.open_plugin(info, x=200, y=500)
    APP.processEvents()
    z_new = c._proxies[("maya", "e")].zValue()
    check("★★ 新节点在原有之上", z_new > before_max,
          (before_max, z_new))
    check("★ 原有节点 z 未被改动",
          _zs(c) == before, (before, _zs(c)))

    # 重复打开同一节点 → 应复用并置顶，不重复创建
    cnt = len(c._proxies)
    c.open_plugin(info, x=200, y=500)
    APP.processEvents()
    check("★ 重复打开不新增节点", len(c._proxies) == cnt,
          (cnt, len(c._proxies)))

    # ── 8. _on_scene_selection_changed 已是空壳 ───────
    print("\n8. 旧的『只抬最高选中项』补救已移除")
    import inspect
    from ui.canvas import Canvas
    import io as _io
    import tokenize
    s2 = inspect.getsource(Canvas._on_scene_selection_changed)
    code2 = "".join(tok.string for tok in
                    tokenize.generate_tokens(_io.StringIO(s2).readline)
                    if tok.type not in (tokenize.COMMENT, tokenize.STRING))
    check("★ 不再有 len(sel) == 1 的置顶补救",
          "_bump_z" not in code2, code2)
    # 空壳也不该报错
    c.clear_selection()
    try:
        c._on_scene_selection_changed()
        check("★ 空调用不抛异常", True)
    except Exception as e:
        check("★ 空调用不抛异常", False, repr(e))

    # ── 9. ★ 真实鼠标路径：单击置顶 / 拖拽不置顶 ─────
    # ⚠️ 这一节是 2026-10-22 补的 —— 前面 8 节全都**直接调方法**
    #   （select_only / toggle_select / open_plugin），没发过真实鼠标事件。
    #   而「延迟置顶」这个机制**只在真实 press→release 路径上**才存在，
    #   直接调方法根本走不到 —— 也就是说前面的测试全绿，
    #   照样可能有一个真实交互是坏的（这次就是）。
    #   ⇒ 交互测试必须走完整事件路径。
    print("\n9. ★ 真实鼠标：单击置顶、拖拽不置顶（延迟置顶机制）")
    from PySide6 import QtGui
    from PySide6.QtTest import QTest

    def _title_vp(c, pr):
        """节点的标题栏中心（视口坐标）。"""
        node = pr._node
        local = QtCore.QPointF(pr.rect().width() / 2.0,
                               node.title_height() / 2.0)
        p = pr.mapToScene(local)
        return c.mapFromScene(p)

    # 9a. 单击一个**已选中**的节点 → 应当置顶
    host, c = _build()
    c.select_all()
    APP.processEvents()
    before = _zs(c)
    target = c._proxies[("maya", "a")]
    vp = _title_vp(c, target)
    QTest.mousePress(c.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, vp)
    APP.processEvents()
    QTest.mouseRelease(c.viewport(), QtCore.Qt.LeftButton,
                       QtCore.Qt.NoModifier, vp)
    APP.processEvents()
    after = _zs(c)
    check("★ 单击已选中节点 → 它被抬到最前",
          after[0] == max(after), (before, after))
    check("★ 其余节点 z 不变",
          after[1:] == before[1:], (before, after))

    # 9b. 按住已选中节点**拖动** → z 完全不动（多选组相对顺序保留）
    host, c = _build()
    c.select_all()
    APP.processEvents()
    before = _zs(c)
    target = c._proxies[("maya", "a")]
    vp = _title_vp(c, target)
    QTest.mousePress(c.viewport(), QtCore.Qt.LeftButton,
                     QtCore.Qt.NoModifier, vp)
    APP.processEvents()
    # 分几步移动（真实拖动）
    for step in range(1, 6):
        QTest.mouseMove(c.viewport(),
                        QtCore.QPoint(vp.x() + step * 8, vp.y() + step * 4))
        APP.processEvents()
    QTest.mouseRelease(c.viewport(), QtCore.Qt.LeftButton,
                       QtCore.Qt.NoModifier,
                       QtCore.QPoint(vp.x() + 40, vp.y() + 20))
    APP.processEvents()
    after = _zs(c)
    check("★★ 拖拽多选组 → z 完全不变（相对顺序保留）",
          after == before, (before, after))
    check("★ 节点确实移动了（说明拖拽真的发生）",
          target.pos() != QtCore.QPointF(0, 0), target.pos())

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())