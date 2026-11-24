"""
paths.py 的路径解析测试（2026-10-20 打包改造）。

★ 为什么这个文件必须单独测 ★

   路径解析的失败模式是**静默**的：
   - project_dir 算错一层 → config.json 读不到 → 端口回落默认值（无报错）
   - plugins_dir 算错 → 资源树两个分支全空（**无报错**，
     因为 plugin_loader.scan() 对不存在的目录直接 return []）
   - resource_dir 算错 → 图标全灰（无报错）

   打包前项目里有 **4 处**各自写着"怎么找项目根"，只有一半认了
   `sys.frozen`。改成集中到 paths.py 之后，必须确保：
     1. 源码模式下算出的路径**真实存在**（不是"看起来对"）
     2. frozen 模式下走 exe 目录
     3. 全项目**再没有第二处**路径推导

跑法：
    .venv/Scripts/python.exe test_paths.py
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # scan_config

import scan_config  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def main():
    import paths

    # ── 1. 源码模式：路径必须真实存在 ────────────────
    print("1. ★ 源码模式：算出的路径必须真实存在")
    pd = paths.project_dir()
    print("    project_dir = %s" % pd)
    check("★ project_dir 存在", os.path.isdir(pd), pd)
    check("★ project_dir 就是本项目根",
          os.path.normcase(os.path.abspath(pd))
          == os.path.normcase(os.path.abspath(HERE)),
          (pd, HERE))
    check("★ project_dir 里能看到 main.py",
          os.path.isfile(os.path.join(pd, "main.py")))
    check("★ project_dir 里能看到 paths.py",
          os.path.isfile(os.path.join(pd, "paths.py")))

    check("★ plugins_dir 存在", os.path.isdir(paths.plugins_dir()),
          paths.plugins_dir())
    check("★ config_path 存在", os.path.isfile(paths.config_path()),
          paths.config_path())
    check("★ resource_dir 存在", os.path.isdir(paths.resource_dir()),
          paths.resource_dir())
    check("★ resource('lock.svg') 存在",
          os.path.isfile(paths.resource("lock.svg")),
          paths.resource("lock.svg"))

    # ⚠️ 这条最关键：project_dir 多退一层就会变成 Documents/，
    #   plugins/ 和 config.json 全找不到（而且不报错）。
    #   ⚠️ 判据是「basename 是不是 documents」而不是「路径里含不含 documents」——
    #      本项目**真的**在 C://Users//...//Documents//qz// 下，
    #      用 contains 判会永远假失败。
    check("★★ project_dir 的 basename 不是 documents（多退一层的经典错误）",
          os.path.basename(pd).lower() != "documents", pd)
    check("★★ plugins_dir 的 basename 是 plugins",
          os.path.basename(paths.plugins_dir()).lower() == "plugins",
          paths.plugins_dir())
    check("★★ config_path 的 basename 是 settings.json",
          os.path.basename(paths.config_path()).lower() == "settings.json",
          paths.config_path())

    # ── 2. frozen 模式模拟 ──────────────────────────
    print("\n2. ★ frozen 模式：走 exe 所在目录")
    import types
    orig_frozen = paths.is_frozen
    orig_executable = sys.executable
    fake_exe = os.path.join(HERE, "dist", "QzTools", "QzTools.exe")
    try:
        # 模拟 frozen：patch is_frozen 与 sys.executable
        paths.is_frozen = lambda: True
        sys.executable = fake_exe
        pd2 = paths.project_dir()
        check("★ frozen 时 project_dir = exe 目录",
              os.path.normcase(os.path.abspath(pd2))
              == os.path.normcase(os.path.dirname(fake_exe)),
              (pd2, fake_exe))
        check("★ frozen 时 config_path 位于用户数据目录",
              os.path.basename(paths.config_path()) == "settings.json"
              and os.path.dirname(paths.config_path()) == paths.user_data_dir(),
              paths.config_path())
        check("★ frozen 时 plugins_dir 在 exe 旁边",
              "QzTools" in paths.plugins_dir(), paths.plugins_dir())
        # ★ 不能是 sys._MEIPASS（onefile 临时目录，进程结束就没了）
        check("★★ 不返回 _MEIPASS 临时目录",
              "_MEI" not in pd2.upper(), pd2)
    finally:
        paths.is_frozen = orig_frozen
        sys.executable = orig_executable

    # ── 3. dcc_dir ──────────────────────────────────
    print("\n3. ★ dcc_dir：DCC 端目录")
    for d in ("maya_bridge", "max_bridge", "maya_actions", "max_actions"):
        p = paths.dcc_dir(d)
        check("★ dcc_dir(%s) 在项目根下" % d,
              os.path.dirname(os.path.normcase(os.path.abspath(p)))
              == os.path.normcase(os.path.abspath(HERE)),
              p)

    # ── 4. 全项目只有一处路径推导 ───────────────────
    print("\n4. ★ 全项目只有 paths.py 一处定义 project_dir")
    offenders = []
    for root, dirs, files in os.walk(HERE):
        # ★ 排除规则集中在 tests/scan_config.py ——
        #   原来这里硬编码 ".venv"，换个 venv 名字就漏（2026-10-22踩过）
        dirs[:] = scan_config.filter_dirs(dirs)
        for f in files:
            if not f.endswith(".py"):
                continue
            if f == "paths.py" or f == "test_paths.py":
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, HERE)
            txt = io.open(p, encoding="utf-8").read()
            # 找 "自己实现 project_dir" 的痕迹
            if "def project_dir(" in txt:
                offenders.append("%s: 自带 project_dir 实现" % rel)
            # 找裸的 dirname(dirname(__file__)) 推导
            if "dirname(os.path.dirname(__file__))" in txt:
                offenders.append("%s: 裸的 dirname(dirname(__file__))" % rel)
    check("★★ 没有第二处 project_dir 实现", not offenders, offenders)

    # ── 5. 关键调用点都走了 paths ───────────────────
    print("\n5. ★ 关键调用点都走 paths")
    for rel, needle in [
        ("maintenance/configuration.py", "paths.config_path()"),
        ("ui/main_window.py", "paths.plugins_dir()"),
        ("ui/settings_panel.py", "paths.config_path()"),
        ("ui/maya_setup.py", "paths.project_dir()"),
        ("ui/max_setup.py", "paths.project_dir()"),
        ("ui/activity_bar.py", "paths.resource("),
        ("ui/canvas.py", "paths.resource("),
    ]:
        txt = io.open(os.path.join(HERE, rel), encoding="utf-8").read()
        check("★ %s 用 %s" % (rel, needle), needle in txt)

    # ── 6. 图标外置：resources 必须在 ui/ 下且是真实目录
    print("\n6. ★ 图标外置")
    check("★ ui/resources 存在", os.path.isdir(paths.resource_dir()))
    svgs = [f for f in os.listdir(paths.resource_dir())
             if f.endswith(".svg")]
    check("★ 至少有 2 个 SVG", len(svgs) >= 2, svgs)
    for f in svgs:
        check("  %s 可读" % f, os.access(
            os.path.join(paths.resource_dir(), f), os.R_OK))

    # ── 7. 外置插件的依赖必须 hidden-import ─────────
    print("\n7. ★ 外置插件的依赖必须 hidden-import")
    # ⚠️ 这是打包最容易漏的地方（2026-10-20 真 bug）：
    #   插件 widget.py 是运行时从磁盘加载的，PyInstaller 静态分析看不见，
    #   所以它 import 的模块不会自动进 PYZ。
    #   实测漏了 ui.plugin_widget → 打包后点开任何插件都报
    #   "No module named 'ui.plugin_widget'"。
    b = io.open(os.path.join(HERE, "build.py"), encoding="utf-8").read()
    # 收集外置插件实际 import 的 ui.* 模块
    needed = set()
    for root, dirs, files in os.walk(os.path.join(HERE, "plugins")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f != "widget.py":
                continue
            txt = io.open(os.path.join(root, f), encoding="utf-8").read()
            import re as _re
            for m in _re.finditer(r"from ui\.(\w+) import", txt):
                needed.add("ui." + m.group(1))
            for m in _re.finditer(r"from ui import (\w+)", txt):
                needed.add("ui." + m.group(1))
    check("★ 外置插件确实 import 了 ui 模块", bool(needed), needed)
    for mod in sorted(needed):
        check("★ build.py 有 --hidden-import=%s" % mod,
              ("--hidden-import=%s" % mod) in b, mod)
    check("★ build.py 有 ui.plugin_widget（这次真 bug 的根因）",
          "--hidden-import=ui.plugin_widget" in b)

    # ── 8. build.py 存在且配置正确 ──────────────────
    print("\n8. ★ build.py")
    check("★ build.py 存在", os.path.isfile(os.path.join(HERE, "build.py")))
    check("★ 旧 build_exe.py 已删除",
          not os.path.isfile(os.path.join(HERE, "build_exe.py")))
    b = io.open(os.path.join(HERE, "build.py"), encoding="utf-8").read()
    check("★ 中台onedir，独立更新助手onefile", "'--onedir'" in b and "'--onefile'" in b)
    check("★ 拷贝外置资源的目录清单完整",
          all(d in b for d in ("plugins", "maya_bridge", "max_bridge",
                               "maya_actions", "max_actions")))
    check("★ 排除 __pycache__（旧 .pyc 会遮蔽新源码）",
          "__pycache__" in b)
    check("★ 拷贝 defaults.json", "defaults.json" in b)
    check("★ 拷贝 ui/resources", "ui" in b and "resources" in b)

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
