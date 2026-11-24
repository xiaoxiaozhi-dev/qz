"""
auto_setup 插件卡片测试（不起 Max / 不起 Maya，纯逻辑）。

为什么单独测：
    这两个卡片承担「安装/卸载自动启动」，一旦路径算错，
    用户会遇到「装了没生效」或「卸了还残留」，而且极难自查。
    关键不变量是：
      - Max 版**绝不**自己推导路径，必须问 action
      - Max 版的「卸载所有」**不碰** action（不需要 Max 连接）
      - Maya 版**完全不碰** action（路径是常量）
      - widget 在 Max 未连接时不预判状态，统一走 log_run

跑法：
    .venv/Scripts/python.exe test_auto_setup.py
"""

import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def fake_client(calls, ret):
    """最小 client：只提供 log_run 走到的 call()。"""
    cl = types.SimpleNamespace()
    def call(action, **kw):
        calls.append((action, kw))
        return ret
    cl.call = call
    return cl


class FakeLog:
    def __init__(self):
        self.lines = []
    def info(self, m): self.lines.append(("info", m))
    def warn(self, m): self.lines.append(("warn", m))
    def error(self, m): self.lines.append(("error", m))
    def text(self):
        return "\n".join(m for _k, m in self.lines)


def main():
    # ⚠️ 必须先建 QApplication —— 没有它，任何 QWidget 构造都直接崩
    #   （而且是 exit code 127 的**静默**崩溃，不抛异常、不打栈，
    #    第一次跑就撞上了，看起来像"测试文件坏了"）。
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    tmp = tempfile.mkdtemp(prefix="qz_autosetup_")
    try:
        # ── 1. Max 版：安装必须问 action，不能自己猜 ──
        print("1. Max 版卡片：安装流程")
        import max_setup
        good_dir = os.path.join(tmp, "2027 - 64bit", "CHS",
                                "scripts", "startup")
        os.makedirs(good_dir)
        calls = []
        client = fake_client(calls, {"ok": True, "data": good_dir})
        log = FakeLog()
        w = _load_max_widget(client, log)

        w._install()
        check("★ 调了 get_startup_dir action",
              calls and calls[0][0] == "get_startup_dir", calls)
        check("★ 写到了 action 报的目录里",
              max_setup.has_script(max_setup.script_path(good_dir)), good_dir)
        check("★ 日志里有写入成功提示", "已写入" in log.text(), log.text())

        print("\n2. Max 版卡片：安装时 Max 未连接")
        calls2 = []
        # log_run 拿不到 ok -> 返回 None，widget 必须安静退出
        # 用一个全新的空目录，避免第 1 步留下的文件干扰断言
        empty_dir = os.path.join(tmp, "empty", "scripts", "startup")
        os.makedirs(empty_dir)
        client2 = fake_client(calls2, {"ok": False, "error": "socket timeout"})
        log2 = FakeLog()
        w2 = _load_max_widget(client2, log2)
        w2._install()
        check("仍然调了 action（不预判连接状态）",
              calls2 and calls2[0][0] == "get_startup_dir", calls2)
        check("★ 失败时不写任何文件（目录仍是空的）",
              not os.listdir(empty_dir), os.listdir(empty_dir))
        check("★ 失败提示进了日志", "socket timeout" in log2.text(),
              log2.text())

        print("\n3. Max 版卡片：卸载当前版本")
        calls3 = []
        client3 = fake_client(calls3, {"ok": True, "data": good_dir})
        log3 = FakeLog()
        w3 = _load_max_widget(client3, log3)
        w3._remove_current()
        check("问 action 拿目录", calls3 and calls3[0][0] == "get_startup_dir",
              calls3)
        check("★ 标记块被清掉了",
              not max_setup.has_script(max_setup.script_path(good_dir)),
              good_dir)

        print("\n4. Max 版卡片：卸载所有版本（不需要 Max）")
        # 造第二个版本，也装上
        other = os.path.join(tmp, "2026 - 64bit", "CHS", "scripts", "startup")
        os.makedirs(other)
        for d in (good_dir, other):
            with open(max_setup.script_path(d), "w", encoding="utf-8") as f:
                f.write(max_setup.build_block())

        # 指向 tmp 的假 LOCALAPPDATA
        fake_ld = os.path.join(tmp, "ld")
        shutil.move(good_dir,
                    os.path.join(fake_ld, "Autodesk", "3dsMax",
                                 "2027 - 64bit", "CHS", "scripts", "startup"))
        good2 = os.path.join(fake_ld, "Autodesk", "3dsMax", "2027 - 64bit",
                             "CHS", "scripts", "startup")
        os.makedirs(os.path.join(fake_ld, "Autodesk", "3dsMax",
                                 "2026 - 64bit", "CHS", "scripts",
                                 "startup"))
        other2 = os.path.join(fake_ld, "Autodesk", "3dsMax", "2026 - 64bit",
                              "CHS", "scripts", "startup")
        for d in (good2, other2):
            with open(max_setup.script_path(d), "w", encoding="utf-8") as f:
                f.write(max_setup.build_block())

        orig_ld = os.environ.get("LOCALAPPDATA")
        calls4 = []
        client4 = fake_client(calls4, {"ok": True, "data": good2})
        log4 = FakeLog()
        w4 = _load_max_widget(client4, log4)
        try:
            os.environ["LOCALAPPDATA"] = fake_ld
            w4._remove_all()
            check("★ 完全没调 action（不需要 Max 连接）",
                  calls4 == [], calls4)
            check("★ 两个版本都被清了",
                  not max_setup.has_script(max_setup.script_path(good2))
                  and not max_setup.has_script(max_setup.script_path(other2)),
                  good2)
            check("★ 日志里逐条列了目录",
                  "removed" in log4.text(), log4.text())
        finally:
            if orig_ld is not None:
                os.environ["LOCALAPPDATA"] = orig_ld

        # ── 5. Maya 版：完全不碰 action ──
        print("\n5. Maya 版卡片：不碰 action")
        import maya_setup
        calls5 = []
        client5 = fake_client(calls5, {"ok": True, "data": "SHOULD NOT HAPPEN"})
        log5 = FakeLog()
        wm = _load_maya_widget(client5, log5)

        fake_home = os.path.join(tmp, "mayauser")
        os.makedirs(os.path.join(fake_home, "Documents", "maya", "scripts"))
        usp = os.path.join(fake_home, "Documents", "maya", "scripts",
                           "userSetup.py")
        with open(usp, "w", encoding="utf-8") as f:
            f.write("# 用户原有内容\nfn companyTool = true\n")
        orig_home = os.path.expanduser("~")
        try:
            _patch_maya_path(usp)
            wm._install()
            check("★ 没调任何 action", calls5 == [], calls5)
            check("★ 标记块写进了 userSetup.py",
                  maya_setup.has_block(usp), usp)
            with open(usp, encoding="utf-8") as f:
                txt = f.read()
            check("★ 用户原有内容还在", "companyTool" in txt, txt[:120])
            wm._remove()
            with open(usp, encoding="utf-8") as f:
                txt2 = f.read()
            check("★ 卸载后只剩用户内容",
                  "companyTool" in txt2 and "qz-bridge" not in txt2, txt2)
        finally:
            _patch_maya_path(None)

        # ── 6. 两份 widget 不共享代码 ──
        print("\n6. Maya / Max 两份 UI 独立")
        import ui.maya_setup as ms
        import ui.max_setup as xs
        m_src = _read(os.path.join(HERE, "plugins", "maya", "auto_setup",
                                   "widget.py"))
        x_src = _read(os.path.join(HERE, "plugins", "max", "auto_setup",
                                   "widget.py"))
        check("两份 widget.py 都存在且非空",
              len(m_src) > 100 and len(x_src) > 100,
              (len(m_src), len(x_src)))
        check("Max 版引用 max_setup", "max_setup" in x_src)
        check("Maya 版引用 maya_setup", "maya_setup" in m_src)
        check("★ Max 版引用 get_startup_dir action",
              "get_startup_dir" in x_src)
        check("★ Maya 版不引用任何 action",
              "log_run" not in m_src and "get_startup_dir" not in m_src)
        # ★ 用户要求：不做安装/卸载切换，每个动作一个独立按钮
        #   Max 3 个（安装 / 卸载当前 / 卸载所有）、Maya 2 个（安装 / 卸载）
        check("★ Maya 版 2 个按钮、Max 版 3 个按钮（不做切换）",
              m_src.count("QPushButton(") == 2
              and x_src.count("QPushButton(") == 3,
              (m_src.count("QPushButton("), x_src.count("QPushButton(")))
        # ★ 用户要求去掉按钮 tooltip：按钮名已经说清作用了
        check("★ Max 版无按钮 tooltip",
              "setToolTip" not in x_src,
              [l for l in x_src.splitlines() if "setToolTip" in l])
        check("★ Maya 版无按钮 tooltip", "setToolTip" not in m_src)
        # 删了 tooltip 不能把「需要连接」这条信息也弄丢 ——
        # 它是这两个按钮失败的头号原因，所以挪到下方常驻显示
        check("★ Max 版仍有常驻提示说明哪些按钮要连接",
              "前两个按钮需要先连接" in x_src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


def _read(p):
    import io
    return io.open(p, encoding="utf-8").read()


def _patch_maya_path(usp):
    """临时把 maya_setup.user_setup_path 指向测试文件。"""
    import ui.maya_setup as ms
    ms.user_setup_path = (lambda: usp) if usp else _orig_user_setup_path


_orig_user_setup_path = None


def _load_max_widget(client, log):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_w_max_as",
        os.path.join(HERE, "plugins", "max", "auto_setup", "widget.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.create_widget(client, log)


def _load_maya_widget(client, log):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_w_maya_as",
        os.path.join(HERE, "plugins", "maya", "auto_setup", "widget.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.create_widget(client, log)


if __name__ == "__main__":
    import ui.maya_setup as _ms
    _orig_user_setup_path = _ms.user_setup_path
    sys.exit(main())
