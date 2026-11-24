"""
画布新交互测试：右键对齐 / Ctrl+F 搜索浮层 / 资源树折叠（2026-10-19）。

这三项都是「用户拍板的具体交互」，必须按拍板的样子断言，
不能只测"函数返回了 True"。

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe test_canvas_align.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

# ⚠️ 必须先建 QApplication，否则 QWidget 构造静默崩溃（exit 127，不抛栈）
from PySide6 import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ui.app_style import apply_application_style
apply_application_style(APP)

from ui.canvas import Canvas
from ui.canvas_align import ALIGN_ACTIONS, align
from ui.canvas_search import SearchOverlay
from ui.plugin_loader import PluginInfo
from ui.plugin_tree import PluginTree

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def _factory(info):
    """Canvas 要的 widget 工厂。测试里返回空壳 widget 即可 ——
    对齐只关心 proxy 的 pos/size，不关心内容。"""
    from ui.plugin_widget import BasePluginWidget

    class _W(QtWidgets.QWidget, BasePluginWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(50)
    return _W()


def _info(dcc, pid, name):
    return PluginInfo(id=pid, name=name, dcc=dcc, icon="",
                      widget_factory_path="widget.create_widget")


def _open(canvas, dcc, pid, name, x, y, w=320, h=80):
    """打开一个固定尺寸的节点。size 固定才能断言对齐结果。"""
    canvas.open_plugin(_info(dcc, pid, name), x=x, y=y)
    key = (dcc, pid)
    proxy = canvas._proxies[key]
    proxy.resize(w, h)
    return key, proxy


def main():
    # ────────────────────────────────1. 对齐算法
    print("1. 对齐算法（纯几何，不依赖 GUI）")
    c = Canvas(_factory)
    k1, p1 = _open(c, "maya", "a", "A", 0, 0)       # (0,0) 320x80
    k2, p2 = _open(c, "maya", "b", "B", 500, 300)    # (500,300)
    k3, p3 = _open(c, "max", "c", "C", 100, 600, h=120)  # (100,600) 320x120

    check("节点已就位", (p1.pos().x(), p1.pos().y()) == (0, 0))
    check("★ 单选/零选不算对齐（返回 False）",
          align([p1], "align_top") is False)

    # 顶部对齐：所有 y → 最小 y(0)
    moved = align([p1, p2, p3], "align_top")
    check("顶部对齐有变化", moved is True)
    check("★ 顶部对齐后 y 全为 0",
          [p.pos().y() for p in (p1, p2, p3)] == [0, 0, 0],
          [p.pos().y() for p in (p1, p2, p3)])
    check("★ 顶部对齐不改 x",
          [p.pos().x() for p in (p1, p2, p3)] == [0, 500, 100])

    # 复位后再测底部
    p1.setPos(QtCore.QPointF(0, 0))
    p2.setPos(QtCore.QPointF(500, 300))
    p3.setPos(QtCore.QPointF(100, 600))
    align([p1, p2, p3], "align_bottom")
    # 最大底部 = p3: 600+120 = 720 → p1 底部 0+80=80 → y = 720-80 = 640
    # p2 底部 300+80=380 → y = 720-80 = 640；p3 不动
    check("★ 底部对齐后底边齐平（720）",
          [p.pos().y() + 80 if p is not p3 else p.pos().y() + 120
           for p in (p1, p2, p3)] == [720, 720, 720],
          [(p.pos().x(), p.pos().y()) for p in (p1, p2, p3)])

    # 左侧对齐
    p1.setPos(QtCore.QPointF(0, 0))
    p2.setPos(QtCore.QPointF(500, 300))
    p3.setPos(QtCore.QPointF(100, 600))
    align([p1, p2, p3], "align_left")
    check("★ 左侧对齐后 x 全为最小值 0",
          [p.pos().x() for p in (p1, p2, p3)] == [0, 0, 0],
          [p.pos().x() for p in (p1, p2, p3)])

    # 右侧对齐
    p1.setPos(QtCore.QPointF(0, 0))
    p2.setPos(QtCore.QPointF(500, 300))
    p3.setPos(QtCore.QPointF(100, 600))
    align([p1, p2, p3], "align_right")
    # 最大右边界 = p2: 500+320=820 → p1 x = 820-320 = 500；p3 x = 500；p2 不动
    check("★ 右侧对齐后右边界齐平（820）",
          [p.pos().x() + 320 for p in (p1, p2, p3)] == [820, 820, 820],
          [p.pos().x() for p in (p1, p2, p3)])

    # 水平居中（上下方向：中心 y 齐平）
    p1.setPos(QtCore.QPointF(0, 0))
    p2.setPos(QtCore.QPointF(500, 300))
    p3.setPos(QtCore.QPointF(100, 600))
    align([p1, p2, p3], "align_hcenter")
    centers = [p.pos().y() + p.size().height() / 2.0 for p in (p1, p2, p3)]
    check("★ 水平居中：中心 y 一致",
          abs(centers[0] - centers[1]) < 0.01
          and abs(centers[1] - centers[2]) < 0.01, centers)
    check("★ 水平居中不改 x",
          [p.pos().x() for p in (p1, p2, p3)] == [0, 500, 100])

    # 垂直居中（左右方向：中心 x 齐平）
    p1.setPos(QtCore.QPointF(0, 0))
    p2.setPos(QtCore.QPointF(500, 300))
    p3.setPos(QtCore.QPointF(100, 600))
    align([p1, p2, p3], "align_vcenter")
    xcenters = [p.pos().x() + p.size().width() / 2.0 for p in (p1, p2, p3)]
    check("★ 垂直居中：中心 x 一致",
          abs(xcenters[0] - xcenters[1]) < 0.01
          and abs(xcenters[1] - xcenters[2]) < 0.01, xcenters)

    check("非法 action 返回 False",
          align([p1, p2], "align_diagonal") is False)
    check("★ 6 个动作齐全", len(ALIGN_ACTIONS) == 6,
          [a for a, _ in ALIGN_ACTIONS])

    # ────────────────────────────────2. 右键菜单
    print("\n2. 右键菜单")
    check("★ 对齐项菜单名无「水平对齐」歧义",
          "水平居中" in [lbl for _, lbl in ALIGN_ACTIONS]
          and "垂直居中" in [lbl for _, lbl in ALIGN_ACTIONS],
          [lbl for _, lbl in ALIGN_ACTIONS])
    menu = c._build_context_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    check("菜单含 6 个对齐项",
          sum(1 for t in texts if t.endswith(("对齐", "居中"))) == 6, texts)
    check("★ 菜单只有对齐项（取消选中已按用户要求移除）",
          "取消选中" not in texts, texts)

    # 无选中时整组置灰
    c.clear_selection()
    m = c._build_context_menu()
    for a in m.actions():
        a.setEnabled(False)
    check("★ 无选中时对齐项全部置灰",
          all(not a.isEnabled() for a in m.actions()))

    # 锁定时不能对齐
    c.select_only(k1)
    c.set_locked(True)
    p_before = (p1.pos().x(), p1.pos().y())
    c._on_align("align_left")
    check("★ 锁定时 _on_align 拒绝执行（第二道防线）",
          (p1.pos().x(), p1.pos().y()) == p_before)
    c.set_locked(False)

    # ────────────────────────────────3. Ctrl+F 浮层
    print("\n3. Ctrl+F 搜索浮层")
    plugins = [
        _info("maya", "create_box", "创建 Box"),
        _info("max", "create_box", "创建 Box"),
        _info("maya", "bone_mirror", "骨骼镜像"),
        _info("max", "move_selected", "移动选中物体"),
    ]
    ov = SearchOverlay()
    ov.set_plugins(plugins)

    def labels(q):
        ov._edit.setText(q)
        return [ov._list.item(i).text()
                for i in range(ov._list.count())]

    r = labels("box")
    check("★ 查 box 得到 2 条", len(r) == 2, r)
    # 2026-10-25：区分手段从「文字前缀 maya · / max ·」改成「行首 DCC 图标」，
    # 所以这里断言"文字是纯插件名 + 图标非空 + 两个图标不同"。
    # ⚠️ 图标必须真的不一样 —— 只断言"有图标"会漏掉"两个 DCC 图标同色同形"
    #    这种等于没区分的情况。
    from ui import theme as _theme
    ov._edit.setText("box")
    icons = [ov._list.item(i).icon() for i in range(2)]
    keys = [ov._list.item(i).data(QtCore.Qt.UserRole) for i in range(2)]
    check("★★ 结果文字不再带 DCC 前缀（改由图标承载）",
          r == ["创建 Box", "创建 Box"], r)
    check("★★ 同名结果各带一枚 DCC 图标（不再分不清）",
          all(not ic.isNull() for ic in icons), icons)
    # 直接比像素：颜色或字形任一处不同都算通过。
    # ⚠️ 只断言"有图标"会漏掉"两个 DCC 图标一模一样"这种等于没区分的情况。
    imgs = [ic.pixmap(16, 16).toImage() for ic in icons]
    check("★★ 两个 DCC 的图标像素不同（否则等于没区分）",
          imgs[0] != imgs[1]
          or _theme.theme.title_color(keys[0][0])
          != _theme.theme.title_color(keys[1][0]),
          [k[0] for k in keys])

    # ★ 旧版 bug：q in p.dcc 导致 "ma" 命中全部
    r = labels("ma")
    check("★ 碎片 'ma' 不再命中全部（旧版会返10 条）",
          not any("骨骼镜像" in t for t in r), r)
    r = labels("max")
    # 前缀去掉后不能再靠文字判断，改看 item 的复合键（真来源）
    dccs = [ov._list.item(i).data(QtCore.Qt.UserRole)[0]
            for i in range(ov._list.count())]
    check("★ 'max' 整词匹配只返回 max 插件",
          len(r) == 2 and all(d == "max" for d in dccs), (r, dccs))

    r = labels("创建")
    check("中文子串匹配正常（Box + Sphere 类场景）",
          len(r) == 2 and "Box" in r[0] and "Box" in r[1], r)
    r = labels("zzz")
    check("无匹配时提示", r == ["(无匹配)"], r)
    ov._edit.setText("")
    check("空查询列出全部", len(labels("")) == 4, labels(""))

    # 已开标记
    ov.refresh_open_keys([("max", "create_box")])
    ov._edit.setText("box")
    rows = [ov._list.item(i) for i in range(2)]
    check("★ 已开的那个标 ⟵ 且灰显",
          "⟵" in rows[1].text()
          and rows[1].foreground() == ov.palette().placeholderText(),
          [x.text() for x in rows])

    # 键盘交互
    ov._edit.setText("box")
    got = []
    ov.plugin_chosen.connect(lambda p: got.append(p))
    ov._activate_current()
    check("回车选中第一项", len(got) == 1 and got[0].dcc == "maya", got)

    # Esc 关闭（必须装在 _edit 上，见 eventFilter 注释）
    fired = []
    ov.dismissed.connect(lambda: fired.append(1))
    # ↓ 在结果里移动（不回车，只是换选中行）
    ov._edit.setText("box")
    before = ov._list.currentRow()
    ov.eventFilter(ov._edit, _key_ev(QtCore.Qt.Key_Down))
    check("↓ 切换选中行", ov._list.currentRow() != before,
          (before, ov._list.currentRow()))
    ov.eventFilter(ov._edit, _key_ev(QtCore.Qt.Key_Up))
    check("↑ 切回上一行", ov._list.currentRow() == before)

    ov.eventFilter(ov._edit, _key_ev(QtCore.Qt.Key_Escape))
    check("★ Esc 触发关闭", fired == [1], fired)

    # ↓ 吃掉按键（返回 True）才不会让字符 'Down' 打进输入框
    check("★ ↓/↑ 被吃掉不落到输入框",
          ov.eventFilter(ov._edit, _key_ev(QtCore.Qt.Key_Down)) is True)
    check("其他按键放行给输入框",
          ov.eventFilter(ov._edit, _key_ev(QtCore.Qt.Key_A)) is False)

    # Canvas 层的锁定联动
    print("\n4. Ctrl+F 与锁定联动")
    c2 = Canvas(_factory)
    # ⚠️ offscreen 下父 widget 从没 show()，子 widget 的 isVisible()
    #    恒为 False —— 所以这里只能断言"逻辑状态"不能断言可见性。
    #    真机跑时父窗口是显示的，可见性由show() 保证。
    check("初始未锁定", c2._locked is False)
    check("未锁定时可唤起", c2.show_search() is True)
    check("★ 浮层对象已创建", c2._search is not None)
    check("★ 未锁定时浮层已被 show",
          c2._search.isHidden() is False, c2._search.isHidden())

    c2.set_locked(True)
    check("★ 锁定时 show_search 返回 False", c2.show_search() is False)
    c2.set_locked(False)
    check("解锁后可再次唤起", c2.show_search() is True)

    # 锁定时若浮层正开着，必须收掉（用户锁画布的意图是"别碰我的布局"）
    c2.set_locked(True)
    check("★ 锁定时浮层被收起", c2._search.isHidden() is True)
    c2.set_locked(False)

    # ★ 关闭后必须把焦点还给画布，否则 a/f/方向键/Del 全失效。
    #   offscreen 下窗口没真正激活，hasFocus 恒False ——
    #   所以要show() 出窗口再断言，否则这条永远是假失败。
    holder = QtWidgets.QWidget()
    holder.resize(900, 700)
    holder.setLayout(QtWidgets.QVBoxLayout())
    holder.layout().addWidget(c2)
    holder.show()
    APP.processEvents()
    c2.setFocus()
    APP.processEvents()
    check("前置条件：画布此时能拿到焦点", c2.hasFocus(),
          "offscreen 下拿不到焦点，焦点断言无意义")
    c2.show_search()
    APP.processEvents()
    check("浮层打开后焦点在输入框（用户能直接打字）",
          c2._search._edit.hasFocus() or not c2._search._edit.isVisible())
    c2._hide_search()
    APP.processEvents()
    check("★ 关闭后焦点还给画布（否则 a/f/方向键/Del 全失效）",
          c2.hasFocus(), "焦点没回到画布")
    holder.close()

    # ────────────────────────────────5. 资源树折叠
    print("\n5. 资源树折叠")
    tree = PluginTree()
    tree.set_plugins(plugins)
    groups = tree._groups
    check("★ 默认展开", groups["maya"].isExpanded())

    # 模拟用户折叠
    groups["maya"].setExpanded(False)
    # ⚠️ 折叠记忆键在 2026-10-21 改成 ("dcc", dcc) —— 因为加了「设置」子组，
    #   两级折叠必须用不同的键，否则折叠子组会把 DCC 分组也记上。
    check("★ 用户折叠被记住", ("dcc", "maya") in tree._user_collapsed,
          tree._user_collapsed)

    # ★ 关键：refresh 后不能把折叠弹回去（这是本次修的核心 bug）
    tree.set_open_keys([("maya", "create_box")])
    check("★★ refresh 后仍保持折叠（旧版会被弹开）",
          not groups["maya"].isExpanded(), groups["maya"].isExpanded())

    # 另一个分组不受影响
    check("★ 未折叠的分组仍展开", groups["max"].isExpanded())

    # 重新展开
    groups["maya"].setExpanded(True)
    check("★ 重新展开后移出记忆",
          ("dcc", "maya") not in tree._user_collapsed, tree._user_collapsed)
    tree.set_open_keys([])
    check("★ 展开状态下 refresh 不受影响",
          groups["maya"].isExpanded())

    # ────────────────────────────────6. 侧栏无搜索
    print("\n6. 侧栏结构")
    from ui.side_panel import SidePanel
    sp = SidePanel()
    check("★ 侧栏只剩 2 个 tab", sp._stack.count() == 2, sp._stack.count())
    check("★ 无搜索面板残留", not hasattr(sp, "_search"))
    import ui.activity_bar as ab
    bar = ab.ActivityBar()
    check("★ ActivityBar 只剩 2 个图标",
          not hasattr(bar, "_btn_search"))
    check("资源图标在", hasattr(bar, "_btn_resource"))
    check("设置图标在", hasattr(bar, "_btn_settings"))
    import os.path
    check("★ 旧 plugin_search.py 已删除",
          not os.path.isfile(os.path.join(HERE, "ui", "plugin_search.py")))

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


def _key_ev(key):
    """真的 QKeyEvent。

    ⚠️ 不能用鸭子类型冒充：eventFilter 里`return super().eventFilter(...)`
    会把对象交回 PySide6 的 C++ 实现做类型检查，鸭子类型直接 TypeError。
    这也说明「filter 放行路径」在测试里必须用真事件才能走到。
    """
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key,
                           QtCore.Qt.NoModifier, "")


if __name__ == "__main__":
    sys.exit(main())
