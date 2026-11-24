"""
项目命名一致性测试（2026-10-20）。

背景：项目里曾经有**三个名字并存**——
    exe    QzAnimTool   （build_exe.py，10-10 定的，最早）
    README Qz Anim Tool
    标题   Qz Tools     （ui/main_window.py，10-18 改的，最晚）
打包配置一直没跟着改。这个测试防止它们再次跑偏。

⚠️ 断言只查**代码与文档**，不查 memory/ 里的历史记录 ——
   那些是"曾经叫什么"的档案，本来就该保留旧名。

跑法：
    .venv/Scripts/python.exe test_naming.py
"""

import io
import os
import re
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


def _read(rel):
    return io.open(os.path.join(HERE, rel), encoding="utf-8").read()


def main():
    # ── 1. 三处正式名称 ─────────────────────────────
    print("1. ★ 正式名称")
    build = _read("build.py")
    readme = _read("README.md")
    mw = _read("ui/main_window.py")

    # ⚠️ build.py 里 exe 名是 APP_NAME + '--name=%s' % APP_NAME 的形式，
    #   不是字面量 '--name=QzTools'。
    #   ⚠️ 而且 '--name=([^']+)' 这个正则会先匹配到 '--name=%s'（模板串），
    #     所以必须**优先**用 APP_NAME 的定义，取不到再退回正则。
    m2 = re.search(r'APP_NAME\s*=\s*["\']([^"\']+)["\']', build)
    if m2:
        exe_name = m2.group(1)
    else:
        m = re.search(r"'--name=([^']+)'", build)
        exe_name = m.group(1) if m else ""
    check("★ build.py 能解析出 exe 名", bool(exe_name), exe_name)
    check("★ exe 名 = QzTools", exe_name == "QzTools", exe_name)
    check("★ exe 名不含空格（命令行要处处转义）",
          " " not in exe_name, exe_name)

    from maintenance.version import read_version
    from ui.main_window import APP_TITLE
    from ui.settings_panel import APP_VERSION
    title = APP_TITLE
    check("★ 窗口标题使用唯一版本源", title == 'Qz Tools v' + read_version()['version'], title)
    check("★ 设置页与窗口版本一致", APP_VERSION == 'v' + read_version()['version'])
    check("★ 标题以 'Qz Tools' 开头", title.startswith("Qz Tools"), title)

    check("★ README 标题 = Qz Tools",
          readme.splitlines()[0].startswith("# Qz Tools"),
          readme.splitlines()[0])

    # ── 2. 数据目录不动 ─────────────────────────────
    print("\n2. ★ 数据目录统一，保留旧数据迁移")
    check("★ 保留旧 .qz-tool 迁移入口", '.qz-tool' in _read('paths.py'))
    ls = _read("ui/layout_state.py")
    check("★ layout_state 统一走用户数据路径", 'paths.user_state_path' in ls)

    # ── 3. 旧名不得出现在代码/文档里 ─────────────────
    print("\n3. ★ 旧名不再出现在代码与文档中")
    targets = ["build.py", "README.md", "ui/main_window.py",
               "ui/maya_setup.py", "ui/max_setup.py",
               "ui/settings_panel.py", "main.py"]
    # ⚠️ 必须用 tokenize 剥掉 docstring/注释 ——
    #   build_exe.py 的 docstring 里**故意**记录了"之前这里写的是 QzAnimTool"，
    #   那是给人看的历史说明，不是残留。直接 grep 会把自己的说明判为失败
    #   （这个坑在 test_zorder.py 已经踩过一次）。
    import io as _io
    import tokenize

    def _code_only_text(text):
        """剥掉注释和字符串字面量，只留真正的代码。"""
        out = []
        try:
            for tok in tokenize.generate_tokens(_io.StringIO(text).readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                out.append(tok.string)
        except tokenize.TokenError:
            return text
        return "".join(out)

    for t in targets:
        txt = _read(t)
        body = _code_only_text(txt)
        hit = ("QzAnimTool" in body) or ("Qz Anim" in body)
        check("★ %s 代码里无旧名" % t, not hit,
              [ln for ln in body.splitlines()
               if "QzAnimTool" in ln or "Qz Anim" in ln][:2])

    # ── 4. 全项目扫一遍（排除 memory/ 历史档案）──
    print("\n4. 全项目扫描（排除 memory/ 历史档案与本文件）")
    leftovers = []
    for root, dirs, files in os.walk(HERE):
        # ★ 排除规则集中在 tests/scan_config.py ——
        #   原来这里硬编码 ".venv"，换个 venv 名字就漏（2026-10-22踩过）
        dirs[:] = scan_config.filter_dirs(dirs)
        for f in files:
            if not f.endswith((".py", ".md", ".json", ".txt", ".ms")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, HERE)
            # ⚠️ 跳过本文件 —— 它自己就包含旧名字符串（用来搜索的）。
            #   ★ 用**文件名**判断而不是相对路径：2026-10-22 测试文件
            #   从项目根移到了 tests/，相对路径变成 "tests/test_naming.py"，
            #   写死 "test_naming.py" 就不再匹配 → 自己把自己判为失败。
            #   按文件名判断则文件搬去哪都成立。
            if f == "test_naming.py":
                continue
            try:
                txt = io.open(p, encoding="utf-8").read()
            except Exception:
                continue
            for i, ln in enumerate(txt.splitlines(), 1):
                if "QzAnimTool" in ln or "Qz Anim" in ln:
                    # 说明性注释/文档字符串不算（历史记录该保留）
                    stripped = ln.strip()
                    if (stripped.startswith("#")
                            or stripped.startswith("*")
                            or stripped.startswith('"')
                            or stripped.startswith("'")
                            or "之前" in ln or "曾" in ln
                            or "统一" in ln or "并存" in ln
                            or "旧名" in ln):
                        continue
                    leftovers.append("%s:%d" % (rel, i))
    check("★ 全项目无旧名残留", not leftovers, leftovers[:5])

    # ── 5. 窗口标题与 exe 名的对应关系写在 README ────
    print("\n5. 命名约定已记录")
    check("★ README 里有命名约定表",
          "QzTools.exe" in readme and "version.json" in readme)
    check("★ README 说明了用户数据", 'data/' in readme and 'user_plugins' in readme)

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
