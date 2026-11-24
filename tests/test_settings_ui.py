# -*- coding: utf-8 -*-
"""
test_settings_ui.py — 设置面板的布局与 Reload 卡片的连接态行为。

覆盖 2026-10-21 的三项改动：
  1. 两个桥接状态行使用统一的连接/断开按钮
  2. 设置面板的 Reload 按钮移除（改成画布卡片）
  3. Reload 卡片未连接时按钮置灰 + 显示「未连接到 Maya」
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

from PySide6 import QtCore, QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QT_QPA_PLATFORM = os.environ.get("QT_QPA_PLATFORM", "offscreen")

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


class FakeLog:
    def __init__(self):
        self.lines = []

    def info(self, m):
        self.lines.append(("info", m))

    def warn(self, m):
        self.lines.append(("warn", m))

    def error(self, m):
        self.lines.append(("error", m))


class FakeClient:
    """假 client —— 只实现卡片用到的那几个接口。"""

    def __init__(self, connected=False, response=None, raises=False):
        self.connected = connected
        self.dcc = "maya"
        self.calls = []
        self._response = response if response is not None else {
            "ok": True, "data": ["a", "b", "c"]}
        self._raises = raises

    def call(self, action, timeout=10, **params):
        self.calls.append((action, timeout, params))
        if self._raises:
            raise RuntimeError("模拟超时")
        return self._response


def load_widget(path, name):
    """按插件的方式动态加载 widget.py（跟 plugin_loader 一样）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    from ui.settings_panel import SettingsPanel

    print("=" * 62)
    print("1. 连接按钮放在各自桥接状态行")
    print("=" * 62)

    panel = SettingsPanel()
    APP.processEvents()

    b1 = panel._connection_buttons['maya']
    b2 = panel._connection_buttons['max']
    check("★ 两个状态行按钮统一显示连接",
          b1.text() == b2.text() == "连接",
          (b1.text(), b2.text()))

    # ★ 关键：两个按钮必须在**同一个父 widget** 里（即同一行）
    check("★★ 两个按钮分别位于对应状态行",
          b1.parent() is panel._state_labels['maya'].parent()
          and b2.parent() is panel._state_labels['max'].parent())

    # 几何验证：y 坐标相同 = 同一行
    panel.set_bridge_states({'maya': True, 'max': False}, '127.0.0.1', {}, {'maya': '2024'})
    check("★★ 已连接显示断开、未连接显示连接", b1.text() == '断开' and b2.text() == '连接')

    # 旧的 _build_connect_maya / _build_connect_max 应该没了
    check("★ 旧的分开的两节已删除",
          not hasattr(panel, "_build_connect_maya")
          and not hasattr(panel, "_build_connect_max"))

    print()
    print("=" * 62)
    print("2. 设置面板的 Reload 按钮已移除")
    print("=" * 62)

    check("★ 没有 _btn_reload", not hasattr(panel, "_btn_reload"))
    check("★ 没有 _btn_reload_max", not hasattr(panel, "_btn_reload_max"))
    check("★ reload_actions signal 已移除",
          not hasattr(SettingsPanel, "reload_actions"))
    check("★ reload_max_actions signal 已移除",
          not hasattr(SettingsPanel, "reload_max_actions"))

    # 界面上确实找不到 "Reload" 按钮
    froms = panel.findChildren(QtWidgets.QPushButton)
    texts = [b.text() for b in froms]
    check("★ 界面上没有 Reload 按钮",
          not any("Reload" in t for t in texts), texts)
    check("★ 界面上不再有临时启动按钮",
          not any('临时启动' in t for t in texts), texts)

    print()
    print("=" * 62)
    print("3. Reload 卡片：未连接时按钮置灰")
    print("=" * 62)

    for dcc, label in (("maya", "Maya"), ("max", "3ds Max")):
        path = os.path.join(HERE, "plugins", dcc, "reload", "widget.py")
        mod = load_widget(path, "qz_test_reload_%s" % dcc)

        # --- 未连接 ---
        client = FakeClient(connected=False)
        log = FakeLog()
        w = mod.create_widget(client, log)
        w.show()
        APP.processEvents()

        btn = w.findChild(QtWidgets.QPushButton)
        check("★ %s 卡片：未连接时按钮置灰" % label,
              not btn.isEnabled(), btn.isEnabled())
        status_texts = [lb.text() for lb in w.findChildren(QtWidgets.QLabel)]
        check("★ %s 卡片：未连接时显示「未连接到 %s」" % (label, label),
              any("未连接到 %s" % label in t for t in status_texts),
              status_texts)

        # 未连接时点 Reload → 应该有警告，且**不发请求**
        w._reload()
        APP.processEvents()
        check("★ %s 卡片：未连接时不发请求" % label,
              not client.calls, client.calls)
        check("★ %s 卡片：未连接时记一条警告" % label,
              any(k == "warn" for k, _ in log.lines), log.lines)

        # --- 已连接 ---
        client2 = FakeClient(connected=True)
        log2 = FakeLog()
        w2 = mod.create_widget(client2, log2)
        w2.show()
        APP.processEvents()

        btn2 = w2.findChild(QtWidgets.QPushButton)
        check("★ %s 卡片：已连接时按钮可用" % label, btn2.isEnabled())

        w2._reload()
        APP.processEvents()
        check("★ %s 卡片：已连接时发 reload_actions 请求" % label,
              client2.calls and client2.calls[0][0] == "reload_actions",
              client2.calls)
        check("★ %s 卡片：成功时记一条 info" % label,
              any(k == "info" for k, _ in log2.lines), log2.lines)

        for x in (w, w2):
            x.close()

    print()
    print("=" * 62)
    print("4. Reload 卡片：识别循环依赖并给出可操作指引")
    print("=" * 62)

    for dcc, label in (("maya", "Maya"), ("max", "3ds Max")):
        path = os.path.join(HERE, "plugins", dcc, "reload", "widget.py")
        mod = load_widget(path, "qz_test_reload2_%s" % dcc)

        # DCC 端没加载过 reload → "unknown action: reload_actions"
        client = FakeClient(
            connected=True,
            response={"ok": False,
                      "error": "unknown action: reload_actions"})
        log = FakeLog()
        w = mod.create_widget(client, log)
        w.show()
        APP.processEvents()

        w._reload()
        APP.processEvents()

        status_texts = [lb.text() for lb in w.findChildren(QtWidgets.QLabel)]
        joined = " ".join(status_texts)
        check("★ %s 卡片：识别出循环依赖" % label,
              "还没加载过 Reload" in joined, status_texts)
        check("★ %s 卡片：指引用户用「临时启动」" % label,
              "临时启动" in joined, status_texts)
        check("★ %s 卡片：循环依赖用 warn 不是 error" % label,
              any(k == "warn" for k, _ in log.lines)
              and not any(k == "error" for k, _ in log.lines),
              log.lines)

        # 其它错误 → 应该是 error
        client2 = FakeClient(
            connected=True, response={"ok": False, "error": "别的错"})
        log2 = FakeLog()
        w2 = mod.create_widget(client2, log2)
        w2.show()
        APP.processEvents()
        w2._reload()
        APP.processEvents()
        check("★ %s 卡片：普通错误记为 error" % label,
              any(k == "error" for k, _ in log2.lines), log2.lines)

        for x in (w, w2):
            x.close()

    print()
    print("=" * 62)
    print("5. 插件卡片也能识别循环依赖（ping 不需要）")
    print("=" * 62)

    # ping 卡片不应该有连接态置灰（它的作用就是测连接）
    for dcc in ("maya", "max"):
        path = os.path.join(HERE, "plugins", dcc, "ping", "widget.py")
        mod = load_widget(path, "qz_test_ping_%s" % dcc)
        client = FakeClient(connected=False)
        log = FakeLog()
        w = mod.create_widget(client, log)
        w.show()
        APP.processEvents()
        btn = w.findChild(QtWidgets.QPushButton)
        check("★ %s ping 卡片：未连接时按钮**仍可用**（它就是用来测连接的）" % dcc,
              btn.isEnabled())
        w.close()

    print()
    print("=" * 62)
    print("6. client.connected 属性存在")
    print("=" * 62)

    from dcc_bridge import DccClient
    c = DccClient(dcc="maya")
    check("★ DccClient 有 connected 属性", hasattr(c, "connected"))
    check("★ 初始为 False", c.connected is False)

    print()
    print("=" * 62)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
