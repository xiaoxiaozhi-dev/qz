"""
打包产物自检 —— 验证 dist/QzTools/ 里的东西是不是齐全、能用。

★ 为什么需要这个 ★
   打包的失败模式几乎全是**静默**的：
     - config.json 读不到 → 端口回落默认值（界面完全正常）
     - plugins/ 扫不到   → 资源树两个分支全空（不报错，
                          因为 plugin_loader.scan() 对不存在的目录直接 return []）
     - SVG 找不到       → 图标全灰（不报错）
   所以"exe 能启动"**远远不够**，必须逐项断言资源真的能用。

用法：
    py -3 selfcheck_dist.py            # 检查 dist/QzTools/
    py -3 selfcheck_dist.py <目录>      # 检查指定目录

⚠️ 这个脚本用**系统 python**跑（跟 build.py 同一个），
   但它不需要 PySide6 —— 只用标准库 + import 项目的模块。
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def main():
    target = sys.argv[1] if len(sys.argv) > 1 \
        else os.path.join(HERE, "dist", "QzTools")
    target = os.path.abspath(target)
    print("=" * 62)
    print("检查打包产物: %s" % target)
    print("=" * 62)

    if not os.path.isdir(target):
        print("✗ 目录不存在。先跑 py -3 build.py")
        return 1

    # ── 1. exe 存在 ─────────────────────────────────
    print("\n1. exe")
    exe = os.path.join(target, "QzTools.exe")
    check("★ QzTools.exe 存在", os.path.isfile(exe), exe)
    check("★ exe 不为空", os.path.isfile(exe)
          and os.path.getsize(exe) > 100 * 1024,
          os.path.getsize(exe) if os.path.isfile(exe) else None)

    # ── 2. 外置配置 ─────────────────────────────────
    print("\n2. defaults.json / version / updater")
    cfg = os.path.join(target, "defaults.json")
    check("★ defaults.json 存在", os.path.isfile(cfg), cfg)
    check("★ QzUpdater.exe 存在", os.path.isfile(os.path.join(target, 'QzUpdater.exe')))
    from maintenance.release import verify_directory
    try:
        release = verify_directory(target)
        check('★ 官方文件 SHA256 清单完整', True, release['version'])
    except Exception as error:
        check('★ 官方文件 SHA256 清单完整', False, str(error))
    if os.path.isfile(cfg):
        try:
            with io.open(cfg, encoding="utf-8-sig") as f:
                data = json.load(f)
            check("★ defaults.json 能解析", isinstance(data, dict))
            check("★ 有 bridge 段", "bridge" in data, list(data))
            ports = data.get("bridge", {}).get("ports", {})
            check("★ ports 有 maya/max",
                  set(ports) == {"maya", "max"}, ports)
        except Exception as e:
            check("★ defaults.json 能解析", False, repr(e))

    # ── 3. 插件目录 ─────────────────────────────────
    print("\n3. plugins/")
    plugins = os.path.join(target, "plugins")
    check("★ plugins/ 存在", os.path.isdir(plugins), plugins)
    n_manifest = 0
    for dcc in ("maya", "max"):
        d = os.path.join(plugins, dcc)
        check("★ plugins/%s/ 存在" % dcc, os.path.isdir(d), d)
        if not os.path.isdir(d):
            continue
        for pid in sorted(os.listdir(d)):
            sub = os.path.join(d, pid)
            mf = os.path.join(sub, "manifest.json")
            wf = os.path.join(sub, "widget.py")
            if not os.path.isdir(sub):
                continue
            ok = os.path.isfile(mf) and os.path.isfile(wf)
            n_manifest += 1 if ok else 0
            check("  plugins/%s/%s/ 完整" % (dcc, pid), ok, sub)
    check("★★ 至少 5 个完整插件", n_manifest >= 5, n_manifest)

    # ── 4. 图标资源 ─────────────────────────────────
    print("\n4. ui/resources/（图标外置）")
    res = os.path.join(target, "ui", "resources")
    check("★ ui/resources/ 存在", os.path.isdir(res), res)
    if os.path.isdir(res):
        svgs = [f for f in os.listdir(res) if f.endswith(".svg")]
        check("★ 至少 4 个 SVG", len(svgs) >= 4, svgs)
        for need in ("explorer.svg", "settings.svg", "pin.svg", "lock.svg"):
            check("  %s 存在" % need, need in svgs, svgs)
        check("  plugin.qss 存在（插件公共控件样式）",
              os.path.isfile(os.path.join(res, "plugin.qss")))
        for indicator in ("check.svg", "check-disabled.svg", "radio-dot.svg", "radio-dot-disabled.svg"):
            check("  控件标记 %s 存在" % indicator,
                  os.path.isfile(os.path.join(res, "controls", indicator)))

    # ── 5. DCC 端文件 ───────────────────────────────
    print("\n5. DCC 端文件（由宿主直接加载）")
    dcc_files = [
        ("maya_bridge", "__init__.py"),
        ("maya_bridge", "server.py"),
        ("maya_bridge", "client.py"),
        ("maya_actions", "create_box.py"),
        ("maya_actions", "user_scripts.py"),
        ("maya_actions", "user_shelf.py"),
        ("max_actions", "user_scripts.py"),
        ("max_actions", "user_toolbar.py"),
        ("max_bridge", "server.py"),
        ("max_bridge", "worker.py"),
        ("max_bridge", "bootstrap.ms"),
        ("max_bridge", "start_max.py"),
        ("max_actions", "create_box.py"),
        ("max_actions", "get_startup_dir.py"),
        ("blender_bridge", "__init__.py"),
        ("blender_bridge", "server.py"),
        ("blender_actions", "__init__.py"),
        ("blender_actions", "get_startup_dir.py"),
        ("blender_actions", "read_selection.py"),
        ("blender_actions", "user_scripts.py"),
    ]
    for d, f in dcc_files:
        p = os.path.join(target, d)
        if f is None:
            check("★ %s/ 存在" % d, os.path.isdir(p), p)
        else:
            check("★ %s/%s 存在" % (d, f), os.path.isfile(
                os.path.join(p, f)), os.path.join(p, f))

    # ⚠️ 绝对不能有 __pycache__ —— 旧 .pyc 会遮蔽新源码
    print("\n6. 无 __pycache__ 残留")
    stale = []
    for root, dirs, files in os.walk(target):
        if "_internal" in root:
            dirs[:] = []
            continue
        if "__pycache__" in dirs:
            stale.append(os.path.relpath(
                os.path.join(root, "__pycache__"), target))
            dirs.remove("__pycache__")
    check("★ 外置资源里没有 __pycache__", not stale, stale)

    # ── 7. 关键：路径解析指向产物目录 ─────────────────
    print("\n7. ★ 路径解析（模拟 frozen 模式）")
    # 模拟打包后：project_dir = exe 所在目录
    fake_exe = exe
    orig_exec = sys.executable
    try:
        sys.executable = fake_exe
        sys.path.insert(0, target)
        import paths
        # 强制走 frozen 分支
        orig_is_frozen = paths.is_frozen
        paths.is_frozen = lambda: True
        try:
            pd = paths.project_dir()
            check("★ project_dir = exe 目录",
                  os.path.normcase(os.path.abspath(pd))
                  == os.path.normcase(target), (pd, target))
            check("★ config_path 在产物里",
                  os.path.isfile(paths.config_path()),
                  paths.config_path())
            check("★ plugins_dir 在产物里",
                  os.path.isdir(paths.plugins_dir()),
                  paths.plugins_dir())
            check("★ resource_dir 在产物里",
                  os.path.isdir(paths.resource_dir()),
                  paths.resource_dir())
            check("★ 锁图标能找到",
                  os.path.isfile(paths.resource("lock.svg")),
                  paths.resource("lock.svg"))

            # ★ 插件扫描 —— 这是最容易静默失败的地方
            sys.path.insert(0, os.path.join(target, "ui"))
            from ui import plugin_loader
            found = plugin_loader.scan(paths.plugins_dir())
            check("★★ 扫到插件（资源树不会空）", len(found) >= 5,
                  [p.key for p in found])
            by_dcc = {}
            for p in found:
                by_dcc.setdefault(p.dcc, []).append(p.id)
            check("★★ maya 分支有插件", len(by_dcc.get("maya", [])) >= 3,
                  by_dcc.get("maya"))
            check("★★ max 分支有插件", len(by_dcc.get("max", [])) >= 3,
                  by_dcc.get("max"))
            # 复合键唯一
            check("★★ 复合键无歧义",
                  len({p.key for p in found}) == len(found))

            # ★★★ 真正**加载每个插件的工厂** ★★★
            #   只扫到 manifest 不够 —— widget.py 是运行时从磁盘加载的，
            #   它 import 的模块（ui.plugin_widget）必须打进 exe，
            #   否则点开插件才炸 "No module named 'ui.plugin_widget'"。
            #   这条断言就是为那个 bug 写的。
            print("\n7b. ★ 真正加载每个插件的工厂（模块必须打进 exe）")
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
            # 真的实例化一个 widget（能加载 ≠ 能构造）
            # ⚠️⚠️ 必须**持有引用**（_app / _widgets）——
            #   局部变量在函数返回时被回收，QApplication 先于它创建的
            #   widget 析构，进程退出时 segfault（exit 139）。
            #   症状是"报告已经写完了才崩"，很容易被误判成自检本身有问题。
            #   （同一个坑在 selftest.py 里也踩过一次。）
            try:
                from PySide6 import QtWidgets
                _app = (QtWidgets.QApplication.instance()
                        or QtWidgets.QApplication([]))
                _widgets = []
                made = 0
                inst_fail = []
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

            # ── 8. DCC 端脚本里的路径是真实存在的 ──────
            # ⚠️ 必须在 frozen 模拟**之内**做这项检查 ——
            #   build_block() 内部调 paths.project_dir()，
            #   出了 frozen 模拟它就指向源码目录了，
            #   那样测出来的"引用路径"根本不是打包后真实的路径。
            print("\n8. ★ DCC 端安装脚本引用的路径真实存在")
            start_max = os.path.join(target, "max_bridge", "start_max.py")
            check("★ start_max.py 存在", os.path.isfile(start_max))
            from ui import max_setup
            block = max_setup.build_block()
            import ast
            lines = [line.strip() for line in block.splitlines() if line.strip().startswith('python.Execute ')]
            check("★ startup.ms 使用 Python2.7 runpy 入口", len(lines) == 1)
            if lines:
                code = json.loads(lines[0][len('python.Execute '):])
                assignment = next(node for node in ast.parse(code).body if isinstance(node, ast.Assign)
                                  and any(isinstance(target, ast.Name) and target.id == '_qz_start' for target in node.targets))
                p = ast.literal_eval(assignment.value).replace('/', os.sep)
                check("★★ 引用的 start_max.py 真实存在",
                      os.path.isfile(p), p)
                check("★★ 引用路径在产物目录内",
                      os.path.normcase(os.path.abspath(p)).startswith(
                          os.path.normcase(target)), p)
        finally:
            paths.is_frozen = orig_is_frozen
    finally:
        sys.executable = orig_exec

    print("\n" + "=" * 62)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        print("=" * 62)
        return 1
    print("ALL PASS")
    print("=" * 62)
    print()
    print("★ 自动检查已过。还需**真机**验证：")
    print("  1. 双击 QzTools.exe，资源树不是空的、图标正常")
    print("  2. 在 Max 里点「安装（当前版本）」→ 看日志里的路径")
    print("  3. 在 Maya 里装自动启动 → 重启 Maya 看是否自动连")
    print("  4. 连一次 Maya + 一次 Max")
    return 0


if __name__ == "__main__":
    sys.exit(main())
