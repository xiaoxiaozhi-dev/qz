# -*- coding: utf-8 -*-
"""
test_plugin_tree.py — 资源树（PluginTree）的专项测试。

为什么单独一个文件：
  资源树是唯一一处「样式表会静默吃掉 native 绘制」的地方 ——
  Qt 只要在 QSS 里看到任何 `::branch` 规则，就会关掉 native branch
  indicator 接管绘制。实测漏掉 `image:` 的后果是**折叠箭头整个消失**，
  而界面上不会报任何错。
  这类问题必须用**像素级断言**守着，源码 grep 抓不到。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtGui, QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ui.app_style import apply_application_style
apply_application_style(APP)
QT_QPA_PLATFORM = os.environ.get("QT_QPA_PLATFORM", "offscreen")

from ui import plugin_loader
from ui.plugin_tree import PluginTree

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (label, "" if extra is None else extra))


def branch_pixels(tree, ymax=22):
    """数分支区（indentation 宽度内）里有多少非背景像素。

    背景色 #252526。箭头是 #8a8a8a 的描边 SVG，
    与背景差值远大于阈值 20，所以这个计数能可靠地反映"箭头在不在"。
    """
    img = tree.grab().toImage()
    indent = tree.indentation()
    n = 0
    for y in range(min(ymax, img.height())):
        for x in range(min(indent, img.width())):
            c = img.pixelColor(x, y)
            if not (abs(c.red() - 0x25) <= 20
                    and abs(c.green() - 0x25) <= 20
                    and abs(c.blue() - 0x26) <= 20):
                n += 1
    return n


def _branch_signature(tree, ymax=22):
    """分支区里非背景像素的**位置集合**（不含颜色）。

    用来区分"图形不同但笔画数相同"的两种箭头 ——
    只比像素数量会漏判。
    """
    img = tree.grab().toImage()
    indent = tree.indentation()
    pts = []
    for y in range(min(ymax, img.height())):
        for x in range(min(indent, img.width())):
            c = img.pixelColor(x, y)
            if not (abs(c.red() - 0x25) <= 20
                    and abs(c.green() - 0x25) <= 20
                    and abs(c.blue() - 0x26) <= 20):
                pts.append((x, y))
    return tuple(pts)


def build_tree():
    t = PluginTree()
    t.set_plugins(plugin_loader.scan(os.path.join(HERE, "plugins")))
    t.setFixedSize(240, 320)
    t.show()
    APP.processEvents()
    return t


# Qt 默认的 Highlight 蓝（深色主题下会是一条突兀的蓝杠）
QT_HIGHLIGHT = (0x30, 0x8c, 0xc6)


def count_color(img, color, x0, x1, y0, y1):
    """统计指定区域里有多少像素是某个颜色。"""
    n = 0
    for y in range(max(0, y0), min(y1, img.height())):
        for x in range(max(0, x0), min(x1, img.width())):
            c = img.pixelColor(x, y)
            if (c.red(), c.green(), c.blue()) == color:
                n += 1
    return n


def main():
    print("=" * 62)
    print("1. 折叠箭头（★ 像素级断言）")
    print("=" * 62)

    # ⚠️ 必须有**真实子项**才谈得上箭头 —— 空的分组没有 branch 可画。
    #   我第一次测时用空树，得出"箭头还是 0"的错误结论。
    tree = build_tree()
    g = tree.topLevelItem(0)
    g.setExpanded(True)
    APP.processEvents()

    n = branch_pixels(tree)
    check("★★ 展开态的分组有折叠箭头（像素 > 0）", n > 0,
          "分支区像素数 = %d" % n)
    check("★ 箭头像素数在合理范围（不是背景噪声）", 5 < n < 200,
          "像素数 = %d" % n)

    # 折叠态也要有（closed 箭头）
    g.setExpanded(False)
    APP.processEvents()
    n_closed = branch_pixels(tree)
    check("★ 折叠态也有箭头（closed 图）", n_closed > 0,
          "像素数 = %d" % n_closed)

    # 展开/折叠的箭头**应该不同**（一个是 v 一个是 >）
    # ⚠️ 比较【像素集合】而不是数量 —— 两个图的笔画粗细可能一样，
    #    数量相同不代表图形相同。
    g.setExpanded(True)
    APP.processEvents()
    open_sig = _branch_signature(tree)
    g.setExpanded(False)
    APP.processEvents()
    closed_sig = _branch_signature(tree)
    check("★ 展开/折叠的箭头图形不同", open_sig != closed_sig,
          "open=%s closed=%s" % (open_sig, closed_sig))

    # 样式表里必须有 image: —— 这是箭头出现的**必要条件**
    ss = APP.styleSheet() + tree.styleSheet()
    check("★ 样式表里 ::branch 带了 image: url", "image: url" in ss)
    check("★ 样式表引用了 arrow_closed.svg", "arrow_closed.svg" in ss)
    check("★ 样式表引用了 arrow_open.svg", "arrow_open.svg" in ss)

    # ⚠️ 关键回归：QSS 里**不能**有 border-image: none ——
    #   那条规则会连 native 箭头一起抹掉。
    #   （当年就是因为它，箭头一直是空白的。）
    check("★★ 样式表里没有 border-image: none",
          "border-image: none" not in ss)

    # SVG 资源必须真实存在（外置资源，打包后路径会变）
    import paths
    for name in ("arrow_closed.svg", "arrow_open.svg"):
        p = paths.resource(name)
        check("★ 资源文件存在：%s" % name, os.path.isfile(p), p)

    # 资源必须是外置的（走 paths），不能用 Path(__file__).parent
    tree.close()

    print()
    print("=" * 62)
    print("1b. 选中项的蓝底（★ 像素级断言）")
    print("=" * 62)
    # 背景：QSS 的 `::item:selected { background }` **只管 item 自己那一格**
    #（从文字开始往右），**管不到左边的缩进/分支区**。
    # 那块区域由 QTreeWidget 用 palette 的 Highlight 角色绘制，
    # 默认是 Qt 的蓝 #308cc6 —— 深色主题下就是一条突兀的蓝杠。
    # 修法：QSS 里加 `selection-background-color`。
    # ⚠️ 改 palette.Highlight **无效**（QSS 优先级更高，实测仍 560 蓝像素）。

    tree = build_tree()
    g = tree.topLevelItem(0)
    g.setExpanded(True)
    APP.processEvents()
    child = g.child(0)
    child.setSelected(True)
    tree.setCurrentItem(child)
    APP.processEvents()

    img = tree.grab().toImage()
    row = tree.visualItemRect(child)
    n_blue = count_color(img, QT_HIGHLIGHT, 0, row.x(),
                         row.y(), row.y() + row.height())
    check("★★ 选中项的缩进区没有 Qt 默认蓝", n_blue == 0,
          "蓝像素 = %d（缩进区宽 %d）" % (n_blue, row.x()))

    # 样式表里必须有这条属性
    check("★ 样式表里有 selection-background-color",
          "selection-background-color" in APP.styleSheet() + tree.styleSheet())

    # 选中【顶层组】时也一样（它的箭头区紧贴左边缘）
    child.setSelected(False)
    g.setSelected(True)
    tree.setCurrentItem(g)
    APP.processEvents()
    img2 = tree.grab().toImage()
    row2 = tree.visualItemRect(g)
    n_blue2 = count_color(img2, QT_HIGHLIGHT, 0, row2.x(),
                          row2.y(), row2.y() + row2.height())
    check("★★ 选中顶层组时也没有蓝底", n_blue2 == 0,
          "蓝像素 = %d" % n_blue2)

    # 选中项**自身**的底色仍应是 #37373d（不是蓝）
    n_sel = count_color(img2, (0x37, 0x37, 0x3d),
                        row2.x(), row2.x() + 40,
                        row2.y(), row2.y() + row2.height())
    check("★ 选中项自身底色仍是 #37373d", n_sel > 0, "#37373d 像素 = %d" % n_sel)

    tree.close()

    print()
    print("=" * 62)
    print("2. 分组结构（category）")
    print("=" * 62)

    tree = build_tree()
    groups = tree._groups
    check("★ 有 maya 分组", "maya" in groups)
    check("★ 有 max 分组", "max" in groups)

    # 「设置」组应该存在，且在对应 DCC 下面
    for dcc in ("maya", "max"):
        g = groups[dcc]
        sub_titles = []
        for i in range(g.childCount()):
            ch = g.child(i)
            data = ch.data(0, QtCore.Qt.UserRole)
            if data and data[0] == "category":
                sub_titles.append(ch.text(0))
        check("★ %s 下有「设置」分组" % dcc, "设置" in sub_titles,
              sub_titles)

    # 「设置」组里的插件
    for dcc in ("maya", "max"):
        g = groups[dcc]
        cat = None
        for i in range(g.childCount()):
            ch = g.child(i)
            data = ch.data(0, QtCore.Qt.UserRole)
            if data and data[0] == "category" and ch.text(0) == "设置":
                cat = ch
                break
        if cat is None:
            check("★ %s 的设置组存在" % dcc, False)
            continue
        names = [cat.child(i).text(0) for i in range(cat.childCount())]
        check("★ %s 设置组有 3 个插件" % dcc, len(names) == 3, names)
        keys = [cat.child(i).data(0, QtCore.Qt.UserRole)[1] for i in range(cat.childCount())]
        check("★ %s 设置组含自动启动安装" % dcc, (dcc, 'auto_setup') in keys, keys)
        check("★ %s 设置组含连接测试" % dcc, (dcc, 'ping') in keys, keys)
        check("★ %s 设置组含重载动作库" % dcc, (dcc, 'reload') in keys, keys)

    # 普通插件不该出现在「设置」组里
    g = groups["max"]
    direct = []
    for i in range(g.childCount()):
        ch = g.child(i)
        data = ch.data(0, QtCore.Qt.UserRole)
        if data and data[0] == "plugin":
            direct.append(ch.text(0))
    catalog = plugin_loader.scan(os.path.join(HERE, 'plugins'))
    expected = [p for p in catalog if p.dcc == 'max' and not p.category]
    check("★ 普通插件仍平铺在 DCC 下", sorted(direct) == sorted(p.name for p in expected), direct)
    check("★ 普通插件不包含设置类",
          not any("ping" in d.lower() or "连接测试" in d for d in direct),
          direct)

    # 计数：DCC 的计数应该是**全部**插件数（含设置组里的）
    for dcc in ('max', 'maya'):
        count = sum(p.dcc == dcc for p in catalog)
        check("★ %s 分组计数包含全部插件" % dcc,
              groups[dcc].text(0).endswith('(%d)' % count), groups[dcc].text(0))

    tree.close()

    print()
    print("=" * 62)
    print("3. category 字段（manifest → PluginInfo）")
    print("=" * 62)

    plugins = plugin_loader.scan(os.path.join(HERE, "plugins"))
    by_key = {p.key: p for p in plugins}

    for dcc in ("maya", "max"):
        for pid in ("auto_setup", "ping", "reload"):
            k = (dcc, pid)
            if k not in by_key:
                check("★ 插件存在：%s/%s" % (dcc, pid), False)
                continue
            p = by_key[k]
            check("★ %s/%s 有 category='settings'" % (dcc, pid),
                  getattr(p, "category", "") == "settings",
                  repr(getattr(p, "category", "<无该字段>")))

    # 普通插件不该有 category
    for dcc, pid in (("max", "create_box"), ("maya", "create_box")):
        p = by_key.get((dcc, pid))
        if p:
            check("★ %s/%s 无 category" % (dcc, pid),
                  getattr(p, "category", "") == "",
                  repr(getattr(p, "category", "<无该字段>")))

    # manifest 解析层单测
    info = plugin_loader._read_manifest(
        os.path.join(HERE, "plugins", "max", "ping"), "max")
    check("★ _read_manifest 能读出 category",
          info is not None and info.category == "settings",
          repr(info.category if info else None))

    print()
    print("=" * 62)
    print("4. 分组折叠状态")
    print("=" * 62)

    tree = build_tree()
    g = groups = tree._groups
    # 找到 maya 的设置组
    cat = None
    for i in range(g["maya"].childCount()):
        ch = g["maya"].child(i)
        data = ch.data(0, QtCore.Qt.UserRole)
        if data and data[0] == "category" and ch.text(0) == "设置":
            cat = ch
            break
    check("★ 找到 maya 的设置组", cat is not None)
    if cat is not None:
        check("★ 设置组默认展开", cat.isExpanded())
        cat.setExpanded(False)
        APP.processEvents()
        # ★ 折叠记忆键是 ("cat", dcc, category) —— 与 DCC 分组的 ("dcc", dcc) 区分开
        #   共用一个键会导致折叠子组时把整个 DCC 分支也记上，刷新后全收起来。
        check("★ 设置组折叠被记住（键含 dcc+category）",
              ("cat", "maya", "settings") in tree._user_collapsed,
              tree._user_collapsed)
        check("★ 折叠子组不会把 DCC 分组也记上",
              ("dcc", "maya") not in tree._user_collapsed,
              tree._user_collapsed)

        # refresh 后不能弹回去
        tree.set_open_keys([("maya", "create_box")])
        APP.processEvents()
        check("★★ 设置组 refresh 后仍保持折叠",
              not cat.isExpanded(), cat.isExpanded())

    tree.close()

    print()
    print("=" * 62)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
