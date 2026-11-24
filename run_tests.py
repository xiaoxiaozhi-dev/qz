"""
run_tests.py — 跑全部测试。

用法：
    .venv/Scripts/python.exe run_tests.py            # 跑全部
    .venv/Scripts/python.exe run_tests.py zorder     # 只跑名字含 zorder 的
    .venv/Scripts/python.exe run_tests.py -v          # 失败时打印完整输出

⚠️ 为什么要这个文件：
   之前每次回归都要手写一长串 for 循环 + grep 提取通过数，
   21 个文件复制粘贴很容易漏掉某一个、或者看错哪个 FAIL。
   这里统一成一条命令，输出对齐，一眼能看出结果。

⚠️ GUI 测试必须设 QT_QPA_PLATFORM=offscreen —— 否则会弹真窗口，
   在无人值守的环境下会卡住。本文件在 import 任何 Qt 代码**之前**
   设置这个环境变量（Qt 在 import 时读它）。
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(HERE, "tests")

# ★ 必须在 import Qt 之前设好
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

# 提取通过数。⚠️ 各测试文件的输出格式不统一（本项目历史遗留），
#   所以要同时认好几种：
#     "通过 30 / 30"        ← 大多数
#     "PASS 24 / 24"        ← test_auto_setup / test_canvas_align / ...
#     "结果：44 通过 / 0 失败"  ← test_plugin_tree / test_settings_ui
#     "ALL PASS"            ← test_canvas / test_multiselect 等
#   认不全就会出现 "?"，看起来像没跑其实跑过了。
_COUNT_RE = re.compile(r"(?:通过|PASS)\s*(\d+)\s*/\s*(\d+)")
_RESULT_RE = re.compile(r"结果：\s*(\d+)\s*通过\s*/\s*(\d+)\s*失败")


def run_one(path):
    """跑一个测试文件，返回 (文件名, 状态, 摘要, 完整输出)。

    状态： "ok" / "fail" / "err"
      ok   = 退出码 0
      fail = 退出码非 0（有断言失败）
      err  = 脚本本身崩了（连输出都没有）
    """
    try:
        with tempfile.TemporaryDirectory(prefix='qz-test-') as directory:
            environment = dict(os.environ, QZ_DATA_DIR=directory,
                               QZ_USER_PLUGINS_DIR=os.path.join(directory, 'user_plugins'),
                               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
            p = subprocess.run(
                [PY, '-B', path], cwd=HERE, env=environment,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return (os.path.basename(path), "err", "超时(180s)", "")

    out = (p.stdout or "") + (p.stderr or "")
    summary = "?"
    m = None
    for m in _COUNT_RE.finditer(out):     # 取最后一个匹配
        pass
    if m:
        summary = "%s / %s" % (m.group(1), m.group(2))
    else:
        m2 = _RESULT_RE.search(out)
        if m2:
            summary = "%s / %s" % (m2.group(1), m2.group(2))
        elif "ALL PASS" in out:
            summary = "ALL PASS"
        elif "HAS FAILURES" in out:
            summary = "HAS FAILURES"

    status = "ok" if p.returncode == 0 else "fail"
    return (os.path.basename(path), status, summary, out)


def main():
    argv = sys.argv[1:]
    verbose = False
    pat = None
    for a in argv:
        if a in ("-v", "--verbose"):
            verbose = True
        else:
            pat = a

    if not os.path.isdir(TESTS_DIR):
        print("找不到 tests/ 目录：%s" % TESTS_DIR)
        return 2

    files = sorted(f for f in os.listdir(TESTS_DIR)
                   if f.startswith("test_") and f.endswith(".py"))
    if pat:
        files = [f for f in files if pat in f]

    if not files:
        print("没有匹配 '%s' 的测试文件" % pat)
        return 2

    print("跑 %d 个测试文件" % len(files))
    print("-" * 62)

    failures = []
    total_ok = 0
    for f in files:
        name, status, summary, out = run_one(os.path.join(TESTS_DIR, f))
        if status == "ok":
            total_ok += 1
            print("  OK   %-28s %s" % (name, summary))
        else:
            failures.append((name, out))
            print("  FAIL %-28s %s" % (name, summary))

    print("-" * 62)
    print("%d / %d 个文件通过" % (total_ok, len(files)))

    if failures:
        print()
        print("=" * 62)
        print("失败详情")
        print("=" * 62)
        for name, out in failures:
            print()
            print("--- %s ---" % name)
            if verbose:
                print(out)
            else:
                # 只打失败行和上下文，够定位就行
                lines = out.splitlines()
                for i, ln in enumerate(lines):
                    if "[FAIL]" in ln or "✗" in ln or "Traceback" in ln:
                        lo = max(0, i - 2)
                        hi = min(len(lines), i + 4)
                        for j in range(lo, hi):
                            print("   " + lines[j])
                        print("   ...")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
