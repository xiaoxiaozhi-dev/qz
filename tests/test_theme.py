# -*- coding: utf-8 -*-
"""
test_theme.py — DCC 配色单一来源（`ui/theme.py`）。

覆盖 2026-10-25 的配色改造：
  1. 默认色 / 未知 dcc 回落 / 浅色判定 / 选中描边跟 DCC 走
  2. `dcc_icon()` 非空、尺寸正确、两个 DCC 像素不同（**字形必须不同形**，
     否则红绿色弱用户分不出 DCC）
  3. theme.json 读写（含 BOM 兼容）、changed 信号、reset、缓存失效

⚠️ 本文件会临时搬走用户的 `~/.qz-tool/theme.json`，结束时**原样还原**。
   （踩过：GUI 集成测试不清理状态，下一轮断言被上轮残留污染。）
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtGui, QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import theme as tm

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


# ── theme.json 隔离 ───────────────────────────
_PATH = tm.theme_path()
_BAK = _PATH + ".testbak"
_HAD = os.path.isfile(_PATH)


def isolate():
    """搬走用户配置，从默认态开始。"""
    if os.path.isfile(_BAK):
        os.remove(_BAK)
    if _HAD:
        shutil.move(_PATH, _BAK)
    elif os.path.isfile(_PATH):
        os.remove(_PATH)
    tm.theme.load()


def restore():
    if os.path.isfile(_BAK):
        if os.path.isfile(_PATH):
            os.remove(_PATH)
        shutil.move(_BAK, _PATH)
    elif not _HAD and os.path.isfile(_PATH):
        os.remove(_PATH)


def sig(icon, size=16):
    """图标的像素签名 —— 用来判断两枚图标是不是真的不一样。"""
    pm = icon.pixmap(size, size)
    img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    return bytes(img.constBits())


def main():
    print("\n== 1. 默认配色 ==")
    check("maya 默认色 = %s" % tm.DEFAULT_TITLE["maya"],
          tm.theme.title_color("maya") == tm.DEFAULT_TITLE["maya"],
          tm.theme.title_color("maya"))
    check("max 默认色 = %s" % tm.DEFAULT_TITLE["max"],
          tm.theme.title_color("max") == tm.DEFAULT_TITLE["max"],
          tm.theme.title_color("max"))
    check("两个 DCC 默认色不同（否则上色等于没做）",
          tm.theme.title_color("maya") != tm.theme.title_color("max"))
    check("初始处于默认态", tm.theme.is_default("maya") and tm.theme.is_default("max"))

    print("\n== 2. 未登记 dcc 回落 ==")
    check("未知 dcc 回落 NEUTRAL_TITLE",
          tm.theme.title_color("c4d") == tm.NEUTRAL_TITLE,
          tm.theme.title_color("c4d"))
    check("空串 dcc 也回落（手工构造 widget 时可能传空）",
          tm.theme.title_color("") == tm.NEUTRAL_TITLE)
    check("未登记 dcc 文字色是浅色（深底浅字，与改造前一致）",
          tm.theme.title_text_color("c4d") == tm.TITLE_TEXT)

    print("\n== 3. 颜色工具 ==")
    check("lighten(黑, 1.0) = 纯白", tm.lighten("#000000", 1.0) == "#ffffff",
          tm.lighten("#000000", 1.0))
    check("lighten(黑, 0) = 原色", tm.lighten("#000000", 0.0) == "#000000")
    check("is_light 认白", tm.is_light("#ffffff") is True)
    check("is_light 认深青绿（maya 默认）", tm.is_light("#0f6e56") is False)
    check("is_light 认深蓝（max 默认）", tm.is_light("#185fa5") is False)
    check("深色标题 → 浅字", tm.theme.title_text_color("maya") == tm.TITLE_TEXT)
    check("非法颜色字符串不炸（_to_rgb 回落到 NEUTRAL）",
          tm.lighten("not-a-color", 0.5).startswith("#"))

    print("\n== 4. 选中描边 ==")
    check("未选中 = 固定描边色",
          tm.theme.border_color("maya", False) == tm.CARD_BORDER)
    sel = tm.theme.border_color("maya", True)
    check("选中描边跟着 DCC 走，不是固定蓝", sel != "#0e639c", sel)
    base = QtGui.QColor(tm.theme.title_color("maya"))
    hi = QtGui.QColor(sel)
    check("选中描边比标题底色更亮（提亮 0.45）",
          hi.red() >= base.red() and hi.green() >= base.green()
          and hi.blue() >= base.blue(), (sel, base.name()))
    check("max 与 maya 的选中描边不同（固定蓝会和 max 蓝标题栏糊一起）",
          tm.theme.border_color("maya", True) != tm.theme.border_color("max", True))

    print("\n== 5. 图标工厂 ==")
    ic_m = tm.dcc_icon("maya")
    ic_x = tm.dcc_icon("max")
    check("dcc_icon 非空", not ic_m.isNull() and not ic_x.isNull())
    # ⚠️ 必须用 pixmap(QSize, dpr) 这个重载来验：QIcon::pixmap(w, h) 会按
    #    逻辑尺寸把 dpr 抹平（拿回来是 16×16 / dpr=1），看上去像"没生效"。
    #    真实路径（QTreeWidget / QListWidget 在高 DPI 下）正是按 dpr 请求的。
    pm2 = ic_m.pixmap(QtCore.QSize(16, 16), 2.0)
    check("16px 图标在 dpr=2 下取到 32 物理像素（防高 DPI 发虚）",
          pm2.width() == 32 and int(pm2.devicePixelRatio()) == 2,
          (pm2.width(), pm2.devicePixelRatio()))
    check("逻辑尺寸仍是 16（不会显示成两倍大）",
          ic_m.pixmap(QtCore.QSize(16, 16), 1.0).width() == 16)
    check("图标尺寸可指定（24 逻辑 = 48 物理 @2x）",
          tm.dcc_icon("maya", 24).pixmap(QtCore.QSize(24, 24), 2.0).width() == 48)
    check("★★ 两 DCC 图标像素不同（颜色 + 字形）", sig(ic_m) != sig(ic_x))
    check("未知 dcc 也画得出图标（不崩）", not tm.dcc_icon("c4d").isNull())
    check("同一 (dcc,size) 命中缓存（同一对象）", tm.dcc_icon("maya") is ic_m)

    print("\n== 6. 改色：缓存失效 + 持久化 + 信号 ==")
    hits = []
    tm.theme.changed.connect(lambda: hits.append(1))

    tm.theme.set_title_color("maya", "#ff0000")
    check("改色立即生效", tm.theme.title_color("maya") == "#ff0000",
          tm.theme.title_color("maya"))
    check("颜色统一成小写 #rrggbb", tm.theme.title_color("maya") == "#ff0000")
    check("改了色就不再是默认态", tm.theme.is_default("maya") is False)
    check("发出一次 changed", len(hits) == 1, len(hits))
    check("★ 缓存被清（同一 dcc 拿到新对象）", tm.dcc_icon("maya") is not ic_m)
    check("改色后图标像素变了（图标真的跟着颜色走）",
          sig(tm.dcc_icon("maya")) != sig(ic_m))

    hits[:] = []
    tm.theme.set_title_color("maya", "#ff0000")
    check("设成同色不重复 emit（避免无谓重建树）", hits == [], hits)

    tm.theme.set_title_color("maya", "not-a-color")
    check("非法颜色被忽略，不写入", tm.theme.title_color("maya") == "#ff0000")

    tm.theme.set_title_color("c4d", "#00ff00")
    check("未登记 dcc 不允许改色", tm.theme.title_color("c4d") == tm.NEUTRAL_TITLE)

    check("theme.json 落盘", os.path.isfile(_PATH), _PATH)
    with open(_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    check("落盘结构 {version, dcc_title}", data.get("version") == 1
          and data.get("dcc_title", {}).get("maya") == "#ff0000", data)
    check("没被改的 dcc 保留默认值",
          data.get("dcc_title", {}).get("max") == tm.DEFAULT_TITLE["max"], data)

    print("\n== 7. 读回 / BOM 兼容 ==")
    tm.theme.load()
    check("load() 读回自定义色", tm.theme.title_color("maya") == "#ff0000")
    check("load() 后缓存清空（新对象）", tm.dcc_icon("maya") is not ic_m)

    # 记事本存 UTF-8 会带 BOM —— 直接 json.load 会 JSONDecodeError，
    # 表现为"配色全部回默认"，非常难查。
    with open(_PATH, "w", encoding="utf-8-sig") as f:
        json.dump({"version": 1, "dcc_title": {"maya": "#123456", "max": "#654321"}},
                  f)
    tm.theme.load()
    check("带 BOM 的 theme.json 也能读（否则静默回默认）",
          tm.theme.title_color("maya") == "#123456"
          and tm.theme.title_color("max") == "#654321",
          (tm.theme.title_color("maya"), tm.theme.title_color("max")))

    with open(_PATH, "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    tm.theme.load()
    check("坏文件全吞、回落默认（不抛异常）",
          tm.theme.title_color("maya") == tm.DEFAULT_TITLE["maya"])

    print("\n== 8. reset 恢复默认 ==")
    tm.theme.set_title_color("max", "#abcdef")
    hits[:] = []
    tm.theme.reset()
    check("reset 后两个 DCC 都回默认",
          tm.theme.title_color("maya") == tm.DEFAULT_TITLE["maya"]
          and tm.theme.title_color("max") == tm.DEFAULT_TITLE["max"])
    check("reset 发 changed", len(hits) == 1, len(hits))
    with open(_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    check("reset 也落盘", data.get("dcc_title", {}).get("max") == tm.DEFAULT_TITLE["max"])

    tm.theme.changed.disconnect()

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    isolate()
    try:
        code = main()
    finally:
        restore()
    sys.exit(code)
