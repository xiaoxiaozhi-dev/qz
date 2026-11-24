"""
scan_config 测试 — 目录排除规则的正确性。

**为什么值得单独测**
这个规则的核心是「哪些目录该跳过」。两类错误都很隐蔽：

- **漏跳**：venv 内部几千个文件被当项目源码扫进去 →
  报出莫名其妙的假失败（2026-10-22 实际发生过：
  `.venv311\...\pip\__main__.py: 裸的 dirname(dirname(__file__))`）
- **误伤**：真实源码目录被跳过 → 该查的没查 → **假绿**（更危险）

⇒ 两边都要测，而且「误伤」比「漏跳」更值得盯。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_config  # noqa: E402

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    # ⚠️ detail 可能是 list（调试时直接传结果），转成 str 再拼 ——
    #    否则 `"" + list` 直接 TypeError，测试自己崩了。
    if not isinstance(detail, str):
        detail = str(detail)
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", label,
                           (" — " + detail) if detail else ""))


# ── 1. 必须跳过的 ──────────────────────────────
print("1. ★ venv 各种变体都要跳过")
VENVS = [".venv", ".venv311", ".venv3", "venv", "env",
         ".venv_old", ".venv_old313_tmp", ".venv_new2",
         "venv-3", "venv_new", ".venv.bak", ".venv_backup",
         "venv-copy", "venv-old", ".venv_py", "VENV", ".Venv"]
for n in VENVS:
    check("跳过 %s" % n, scan_config.is_skippable(n))

# ── 2. 必须保留的（真实源码目录）────────────────
print("\n2. ★★ 真实目录绝不能被误伤（误伤 = 假绿，更危险）")
KEEP = [
    ("ui", "项目源码目录"),
    ("plugins", "插件目录"),
    ("tests", "测试目录"),
    ("docs", "文档"),
    ("max_actions", "Max action"),
    ("maya_bridge", "Maya 桥"),
    ("src", "通用"),
    # ↓ 这些只是「碰巧以 venv/env 开头」，必须保留
    ("venv_helper", "以 venv 开头的源码目录"),
    ("venvtools", "以 venv 开头的工具目录"),
    ("venvold", "裸接修饰词，没有分隔符"),
    ("venvnew", "裸接修饰词，没有分隔符"),
    ("environment", "以 env 开头的真实词"),
    ("env_utils", "以 env 开头的工具目录"),
    ("envy", "以 env 开头的普通词"),
    ("envelope", "以 env 开头的普通词"),
    ("my_env", "venv 不是开头"),
    (".venvx", "结尾多一个字母"),
    ("venv_helper_x", "带后缀"),
]
for n, why in KEEP:
    check("保留 %-14s （%s）" % (n, why), not scan_config.is_skippable(n))

# ── 3. 产物 / 工具目录 ──────────────────────────
print("\n3. 产物与工具目录")
for n in ["__pycache__", "build", "dist", ".git", "memory", ".workbuddy"]:
    check("跳过 %s" % n, scan_config.is_skippable(n))

# ── 4. 反向对照：硬编码列表会漏 ─────────────────
print("\n4. ★ 反向对照：原来硬编码 \".venv\" 会漏掉哪些")
HARDCODE = {".venv", ".workbuddy", "__pycache__", "build", "dist", ".git"}
missed_by_hardcode = [n for n in VENVS if n not in HARDCODE]
check("★ 硬编码列表确实会漏（证明改动的必要性）",
      len(missed_by_hardcode) > 0,
      "%d 个漏: %s" % (len(missed_by_hardcode), missed_by_hardcode[:6]))
check("★ 新的规则一个都不漏",
      all(scan_config.is_skippable(n) for n in VENVS))

# ── 5. filter_dirs 的接口形态 ───────────────────
print("\n5. filter_dirs（给 os.walk 的 dirs[:] 用）")
dirs = [".venv", "ui", "dist", "plugins", ".venv311", "docs", ".git"]
kept = scan_config.filter_dirs(dirs)
check("过滤掉 venv / 产物", ".venv" not in kept and ".venv311" not in kept
      and "dist" not in kept and ".git" not in kept, kept)
check("保留源码目录", all(k in kept for k in ("ui", "plugins", "docs")), kept)
# ⚠️ os.walk 读的是那个列表对象，所以必须返回新 list 而不是原地改
check("返回的是新 list（不原地改传入的）", kept is not dirs)

print()
print("=" * 58)
print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("FAIL: " + ", ".join(FAIL))
    sys.exit(1)
print("ALL PASS")