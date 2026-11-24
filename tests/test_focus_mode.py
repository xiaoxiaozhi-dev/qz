"""
专注模式（Ctrl+空格）回归测试。

三条要验的行为（用户 2026-10-19 拍板）：
  1. 进入 → ActivityBar / SidePanel / 日志面板全隐藏，只留画布
  2. 退出 → **按进入前的可见状态精确还原**，而不是无脑 show()
  3. ★ closeEvent 不能把"日志隐藏"写成 log_height=0 ——
     否则下次启动日志永久消失

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe test_focus_mode.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

PASS, FAIL = [], []


def _settle(ms=400):
    """等 MainWindow 的 _restore_layout（50ms 定时器）跑完。

    ⚠️ 必须等 —— 恢复期间 _restoring=True，
       而 _on_splitter_moved 会被它挡住（不记 _log_h / _log_user_adjusted）。
       测试里直接 emit splitterMoved 的话，不等就测不到。
    """
    import time
    t = time.time()
    while (time.time() - t) * 1000 < ms:
        APP.processEvents()
        time.sleep(0.01)
    APP.processEvents()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def main():
    # GUI 测试必须先删 layout.json，否则上轮保存的节点会被恢复
    from ui.layout_state import _default_path
    lay = _default_path()
    if os.path.isfile(lay):
        os.remove(lay)

    from ui.main_window import MainWindow

    w = MainWindow()
    APP.processEvents()
    w.resize(1200, 800)
    APP.processEvents()

    # ── 1. 默认状态 ─────────────────────────────────
    print("1. 前置：默认状态")
    check("默认侧栏是隐藏的（这是本测试的关键前提）",
          w._side.isHidden(), w._side.isVisible())
    check("ActivityBar 可见", w._activity.isVisible())
    check("日志可见", w._log.isVisible())
    check("画布可见", w._canvas.isVisible())

    # ── 2. Ctrl+空格 进入专注模式 ─────────────────────
    print("\n2. ★ 进入专注模式（只留画布）")
    w._activity.show_panel(0)
    APP.processEvents()
    check("前置：侧栏可见", w._side.isVisible())
    pre_log_h = w._right_split.sizes()[1]
    check("前置：日志有高度", pre_log_h > 0, pre_log_h)

    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space,
                         QtCore.Qt.ControlModifier, "")
    w._canvas.keyPressEvent(ev)
    APP.processEvents()
    check("★ _focus_mode 标记为 True", w._focus_mode is True, w._focus_mode)
    check("★ ActivityBar 已隐藏", w._activity.isHidden())
    check("★ 侧栏已隐藏", w._side.isHidden())
    check("★ 画布仍然可见（这是唯一保留的）", w._canvas.isVisible())
    check("★ 进入前侧栏状态已记住 = 可见",
          w._pre_focus_side_visible is True, w._pre_focus_side_visible)
    check("★ 进入前日志高度已记住",
          w._pre_focus_log_height == pre_log_h,
          (w._pre_focus_log_height, pre_log_h))

    # ★★ 日志是【收成 0】而不是 hide —— 关键区别 ★★
    check("★★ 日志高度 = 0", w._right_split.sizes()[1] == 0,
          w._right_split.sizes())
    check("★★ 日志【没有被隐藏】（hide 会连手柄一起带走，拉不回来）",
          not w._log.isHidden(), w._log.isVisible())
    check("★ _log_user_adjusted = False（是程序收的，不是用户拖的）",
          w._log_user_adjusted is False)

    print("\n3. ★ 再按一次退出：精确还原")
    w._canvas.keyPressEvent(
        QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space,
                        QtCore.Qt.ControlModifier, ""))
    APP.processEvents()
    check("★ _focus_mode 标记为 False", w._focus_mode is False)
    check("★ ActivityBar 恢复可见", w._activity.isVisible())
    check("★ 侧栏恢复可见（进来前是可见）", w._side.isVisible())
    check("★★ 日志高度恢复成进专注前的高度",
          w._right_split.sizes()[1] == pre_log_h,
          (w._right_split.sizes()[1], pre_log_h))
    check("★ _log_user_adjusted 已复位",
          w._log_user_adjusted is False)

    print("\n4. 侧栏本来是打开的情况")
    # ⚠️ show_panel(0) 是**切换**语义（再点一次会收起来）——
    #   第 2 节已经打开过了，这里再调会把侧栏关掉。
    #   所以先判状态，隐藏才打开。
    if not w._side.isVisible():
        w._activity.show_panel(0)
        APP.processEvents()
    check("前置：侧栏现在可见", w._side.isVisible())
    w.toggle_focus_mode()
    APP.processEvents()
    check("进入后侧栏隐藏", w._side.isHidden())
    w.toggle_focus_mode()
    APP.processEvents()
    check("★★ 退出后侧栏恢复可见（还原进入前状态）",
          w._side.isVisible(), w._side.isVisible())

    # ── 5. 日志本来是隐藏的情况 ─────────────────────
    print("\n5. 用户在专注模式里手动拖日志")
    w2 = MainWindow()
    w2.resize(1200, 800)
    w2.show()
    _settle()
    w2.toggle_focus_mode()
    APP.processEvents()
    check("进专注后日志高度 = 0",
          w2._right_split.sizes()[1] == 0, w2._right_split.sizes())

    # 模拟用户真的拖了手柄（splitterMoved 只在用户拖时触发）
    w2._right_split.splitterMoved.emit(100, 1)
    w2._right_split.setSizes([600, 200])
    APP.processEvents()
    check("★ _log_user_adjusted = True（用户拖过了）",
          w2._log_user_adjusted is True)
    check("日志高度 = 200", w2._right_split.sizes()[1] == 200,
          w2._right_split.sizes())

    w2.toggle_focus_mode()
    APP.processEvents()
    check("★★ 退出后保持用户拖的高度 200（用户拍板：保持，不还原）",
          w2._right_split.sizes()[1] == 200, w2._right_split.sizes())

    print("\n5b. 用户在专注模式里把日志拖到 0")
    w3 = MainWindow()
    w3.resize(1200, 800)
    w3.show()
    _settle()
    w3.toggle_focus_mode()
    APP.processEvents()
    w3._right_split.splitterMoved.emit(100, 1)
    w3._right_split.setSizes([800, 0])
    APP.processEvents()
    check("拖到 0，_log_user_adjusted = True",
          w3._log_user_adjusted is True)
    w3.toggle_focus_mode()
    APP.processEvents()
    check("★★ 退出后保持 0（用户是故意的）",
          w3._right_split.sizes()[1] == 0, w3._right_split.sizes())

    # ── 6. ★★ 关键：拖到 0 后关程序，下次启动日志保持隐藏 ──
    print("\n6. ★★ 关键：拖到 0 后关程序，log_height 存 0")
    # closeEvent 里现在**不再**有 "not self._focus_mode" 的守卫 ——
    # 因为日志改成收尺寸后，用户在专注模式里拖到 0 是故意的，
    # 值得写进持久化配置（2026-10-19 用户拍板）。
    w4 = MainWindow()
    w4.resize(1200, 800)
    w4.show()
    _settle()
    w4.toggle_focus_mode()
    APP.processEvents()
    w4._right_split.splitterMoved.emit(100, 1)
    w4._right_split.setSizes([800, 0])
    APP.processEvents()

    # 复现 closeEvent 算 log_h 的那几行
    log_h = w4._log_h
    sizes = w4._right_split.sizes()
    if len(sizes) >= 2:
        log_h = int(sizes[1])
    check("★★ 专注模式下也照实存 sizes[1] = 0",
          log_h == 0, log_h)

    # 非专注模式下也一样取 sizes[1]
    w4.toggle_focus_mode()
    APP.processEvents()
    w4._log_h = 137
    log_h2 = w4._log_h
    sizes2 = w4._right_split.sizes()
    if len(sizes2) >= 2:
        log_h2 = int(sizes2[1])
    check("★ 非专注模式下正常取 sizes[1]",
          log_h2 != 137 or len(sizes2) < 2, (log_h2, sizes2))

    # 真的走一遍 closeEvent → 落盘 → 重启恢复
    import layout_state
    w5 = MainWindow()
    w5.resize(1200, 800)
    w5.show()
    _settle()
    w5.toggle_focus_mode()
    APP.processEvents()
    w5._right_split.splitterMoved.emit(100, 1)
    w5._right_split.setSizes([800, 0])
    APP.processEvents()
    w5.close()
    APP.processEvents()
    saved = layout_state.load_log_height()
    check("★★ 落盘的 log_height = 0", saved == 0, saved)

    w6 = MainWindow()
    w6.resize(1200, 800)
    w6.show()
    _settle()
    check("★★ 重启后日志高度 = 0（保持隐藏）",
          w6._right_split.sizes()[1] == 0, w6._right_split.sizes())
    # 清理，别影响后面的测试
    w6._right_split.setSizes([700, 70])
    layout_state.save_log_height(70)

    # ── 7. 快捷键回调注入 ───────────────────────────
    print("\n7. Canvas 侧的回调接线")
    check("★ Canvas._focus_mode_cb 已注入",
          w._canvas._focus_mode_cb is not None)
    check("★ 回调绑定的是主窗口的 toggle_focus_mode",
          w._canvas._focus_mode_cb == w.toggle_focus_mode)
    # 未注入时不应崩
    from ui.canvas import Canvas
    from ui.plugin_widget import BasePluginWidget

    def f(_i):
        class W(QtWidgets.QWidget, BasePluginWidget):
            def __init__(self):
                super().__init__()
        return W()
    c2 = Canvas(factory=f)
    check("★ 默认 _focus_mode_cb 为 None（不崩的前提）",
          c2._focus_mode_cb is None)
    try:
        c2.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space,
                            QtCore.Qt.ControlModifier, ""))
        check("★ 未注入回调时按 Ctrl+空格 不抛异常", True)
    except Exception as e:
        check("★ 未注入回调时按 Ctrl+空格 不抛异常", False, repr(e))

    # ── 8. 锁定时仍可用 ─────────────────────────────
    print("\n8. 锁定时 Ctrl+空格 仍可用（只改布局，不动内容）")
    w2 = MainWindow()
    APP.processEvents()
    w2._canvas.setFocus()
    w2._canvas.set_locked(True)
    APP.processEvents()
    w2._canvas.keyPressEvent(
        QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space,
                        QtCore.Qt.ControlModifier, ""))
    APP.processEvents()
    check("★ 锁定时也能进入专注模式", w2._focus_mode is True)
    check("★ 锁定状态没被顺便改掉", w2._canvas.is_locked() is True)

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
