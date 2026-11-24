"""
selftest.py — 打包产物自检（**在 exe 内部**跑）。

★ 为什么必须在 exe 内部验 ★

   `selfcheck_dist.py` 是用**源码** python 跑的，它验的是
   "源码树能不能加载插件"。但打包后模块在 PYZ 里 ——
   **源码树能 import ≠ PYZ 里有**。

   实测踩到（2026-10-20）：
   `ui.plugin_widget` 只被**外置**插件 import，exe 内部一处都没有
   → PyInstaller 静态分析看不见 → 没打进 PYZ
   → 打包后点开任何插件都报 "No module named 'ui.plugin_widget'"
   而 selfcheck_dist.py 当时**全绿**。

   这类 bug 极静默：exe 能启动、资源树不空、图标正常，
   只有真正点开插件才炸。

用法：
    dist/QzTools/QzTools.exe --selftest
    → 结果写到 dist/QzTools/selftest.log（--windowed 的 exe 没有控制台）
"""

import io
import json
import os
import sys


def run(out=None):
    """跑自检，返回 (失败项列表, 报告文本)。"""
    buf = io.StringIO()

    def w(s=""):
        buf.write(s + "\n")

    here = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))

    w("=" * 62)
    w("QzTools 产物自检")
    w("  exe     = %s" % sys.executable)
    w("  here    = %s" % here)
    w("  frozen  = %s" % getattr(sys, "frozen", False))
    w("=" * 62)

    failures = []

    def check(name, cond, detail=""):
        w("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond
                         else ""))
        if not cond:
            failures.append(name)

    # ── 1. 路径解析 ───────────────────────────────
    w("\n1. 路径解析")
    try:
        import paths
        check("project_dir = exe 目录",
              os.path.normcase(os.path.abspath(paths.project_dir()))
              == os.path.normcase(here), paths.project_dir())
        check("defaults.json 能读到", os.path.isfile(paths.defaults_path()), paths.defaults_path())
        check("settings.json 能读到", os.path.isfile(paths.config_path()), paths.config_path())
        check("plugins/ 存在", os.path.isdir(paths.plugins_dir()),
              paths.plugins_dir())
        check("ui/resources/ 存在", os.path.isdir(paths.resource_dir()),
              paths.resource_dir())
    except Exception as e:
        check("paths 可 import", False, repr(e))
        paths = None

    # ── 2. 插件加载（★ 这次真 bug 的核心）────────
    w("\n2. ★ 插件加载（外置插件 import 的模块必须在 PYZ 里）")
    if paths is not None:
        try:
            from ui import plugin_loader
            found = plugin_loader.scan(paths.plugins_dir())
            check("扫到插件", len(found) >= 5, [p.key for p in found])

            load_fail = []
            for p in found:
                try:
                    fn = p.load_factory()
                    if not callable(fn):
                        load_fail.append("%s: 工厂不可调用" % (p.key,))
                except Exception as e:
                    load_fail.append("%s: %s: %s"
                                     % (p.key, type(e).__name__, e))
            check("★★ 所有插件的工厂都能加载", not load_fail, load_fail)
            if load_fail:
                w()
                w("  ↑ 这就是 bug：外置插件 import 的模块没被打进 exe。")
                w("    修法：在 build.py 里加 --hidden-import=<模块>")

            # 能加载 ≠ 能构造，再实例化一遍
            # ⚠️ 必须先有 QApplication —— 没有它构造 QWidget 会**静默崩溃**
            #   （exit 127，不抛异常不打栈，看起来像"脚本坏了"）。
            #   这条坑在 MEMORY.md 的 PySide6 坑位表里。
            #
            # ⚠️⚠️ 而且**必须持有引用**（_app / _widgets）——
            #   局部变量在函数返回时被回收，QApplication 先于它创建的
            #   widget 析构，进程退出时 segfault（exit 139）。
            #   症状是"报告已经写完了才崩"，很容易被误判成自检本身有问题。
            inst_fail = []
            made = 0
            try:
                from PySide6 import QtWidgets
                _app = (QtWidgets.QApplication.instance()
                        or QtWidgets.QApplication([]))
                _widgets = []
                for p in found:
                    try:
                        _widgets.append(p.load_factory()(None, None))
                        made += 1
                    except Exception as e:
                        inst_fail.append("%s: %s" % (p.key, e))
                check("★★ 所有插件都能实例化 widget", not inst_fail, inst_fail)
                check("★ 至少实例化 5 个", made >= 5, made)
                # 主动销毁：先 widget 后 app，顺序对了就不 segfault
                for _wdg in _widgets:
                    _wdg.deleteLater()
                _app.processEvents()
            except Exception as e:
                check("★★ 所有插件都能实例化 widget", False, repr(e))
        except Exception as e:
            check("插件加载", False, repr(e))

    # ── 3. 关键模块可 import ──────────────────────
    w("\n3. 关键模块可 import")
    for mod in ("ui.plugin_widget", "ui.plugin_loader",
                "ui.maya_setup", "ui.max_setup", "ui.theme"):
        try:
            __import__(mod)
            check("%s 可 import" % mod, True)
        except Exception as e:
            check("%s 可 import" % mod, False, repr(e))

    # 数值依赖必须在真正的 exe 内测试，不能只验证开发环境可 import。
    try:
        import numpy as np
        from ui.skin_weights import process_snapshot, format_log_messages
        check("NumPy 二进制运算可用", float(np.array([0.25, 0.75]).sum()) == 1.0)
        check("蒙皮数值模块可加载", callable(process_snapshot))
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="qz-skin-selftest-") as directory:
            np.array([[0.5, 0.3, 0.2], [0.0, 0.0, 0.0]], dtype="<f8").tofile(
                Path(directory) / "mesh-0.weights")
            reports, patches = process_snapshot(directory, [
                {"index": 0, "mesh": "selftest", "vertices": 2, "influences": 3}], "limit", 2)
            values = np.fromfile(Path(directory) / "mesh-0.values", dtype="<f8")
            check("蒙皮二进制计算可用", reports[0]["invalid_vertices"] == 1
                  and patches[0]["count"] == 1 and values.tolist() == [0.625, 0.375])
            messages = format_log_messages({"ok": True, "data": {
                "operation": "limit", "count": 1, "max_influences": 2,
                "invalid_vertices": 1, "reports": reports, "errors": []}})
            check("蒙皮中文摘要可用", any("修改 1 个顶点" in message for _, message in messages))
    except Exception as e:
        check("NumPy / 蒙皮数值模块可用", False, repr(e))

    # ── 4. 资源 ──────────────────────────────────
    w("\n4. 资源")
    if paths is not None:
        try:
            from maintenance.configuration import load
            cfg = load()
            check("默认配置与用户设置能合并", isinstance(cfg, dict))
            check("ports 有 maya/max/blender",
                  set(cfg.get("bridge", {}).get("ports", {}))
                  == {"maya", "max", "blender"},
                  cfg.get("bridge", {}).get("ports"))
        except Exception as e:
            check("默认配置与用户设置能合并", False, repr(e))
        check("lock.svg 存在", os.path.isfile(paths.resource("lock.svg")),
              paths.resource("lock.svg"))
        check("plugin.qss 存在（插件公共控件样式）",
              os.path.isfile(paths.resource("plugin.qss")), paths.resource("plugin.qss"))
        for indicator in ("check.svg", "check-disabled.svg", "radio-dot.svg", "radio-dot-disabled.svg"):
            check("控件标记 %s 存在" % indicator,
                  os.path.isfile(paths.resource("controls", indicator)))
        # ★ 主程序图标：窗口 / 任务栏靠 main.py 读这个 svg，缺了不会报错只会变白纸
        check("★ qz.svg 存在（窗口图标）", os.path.isfile(paths.resource("qz.svg")),
              paths.resource("qz.svg"))
        # ★ 配色表齐全（缺一个 DCC 就会回落成灰，界面上不报错）
        try:
            from ui import theme as _tm
            check("★★ 配色表覆盖 maya/max",
                  set(_tm.DEFAULT_TITLE) == set(_tm.DCC_ORDER),
                  _tm.DEFAULT_TITLE)
            check("★ 字形 M/X 不同形（色弱也能分辨）",
                  len(set(_tm.DCC_GLYPH.values())) == len(_tm.DCC_GLYPH),
                  _tm.DCC_GLYPH)
        except Exception as e:
            check("★★ 配色表覆盖 maya/max", False, repr(e))

    # ── 5. DCC 端文件 ────────────────────────────
    w("\n5. DCC 端文件（被 Maya/Max 用绝对路径引用）")
    if paths is not None:
        for d, f in [("max_bridge", "start_max.py"),
                     ("max_bridge", "bootstrap.ms"),
                     ("max_bridge", "worker.py"),
                     ("maya_bridge", "server.py")]:
            check("%s/%s 存在" % (d, f),
                  os.path.isfile(os.path.join(paths.dcc_dir(d), f)),
                  os.path.join(paths.dcc_dir(d), f))

    w()
    w("=" * 62)
    if failures:
        w("FAIL %d 项: %s" % (len(failures), ", ".join(failures)))
    else:
        w("ALL PASS")
    w("=" * 62)

    text = buf.getvalue()
    if out is None and getattr(sys, "frozen", False):
        directory = os.path.join(paths.user_data_dir(), 'logs')
        os.makedirs(directory, exist_ok=True)
        out = os.path.join(directory, "selftest.log")
    if out:
        try:
            with io.open(out, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
    return failures, text
