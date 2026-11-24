"""
scan_config.py — 全项目扫描时该跳过哪些目录。

**为什么要有这个文件**
`test_paths.py` 和 `test_naming.py` 都要"扫全项目找违规项"，
两边原本各自硬编码了一份排除列表：

    dirs[:] = [d for d in dirs if d not in (".venv", "memory", ...)]

问题在2026-10-22 暴露：把 venv 换成 `.venv311` 之后，
那个 `".venv"` 就**不再匹配**了 → venv 内部的pip 源码
被当成项目源码扫进去 → 报了个莫名其妙的假失败
（`.venv311\Lib\site-packages\pip\__main__.py: 裸的 dirname(dirname(__file__))`）。

⇒ **venv 的名字会变**（`.venv` / `.venv311` / `venv` / `env`），
写死一个名字就是埋雷。
⇒ 改成**前缀通配** + 集中定义，两边共用一份。

用法：

    import scan_config
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if not scan_config.is_skippable(d)]
"""
import os
import re

# ★ 前缀通配的目录名 —— 这些不管叫什么、带不带后缀都跳过。
#   （`.venv` / `.venv311` / `venv` / `env` / `venv_old313_tmp` 全部命中）
_VENV_PREFIXES = (".venv", "venv", "env")

# venv 变体里可能出现的「可剥离片段」。按长度降序排列 ——
# 先剥长的，否则 "backup" 会被 "bak" 提前吃掉，剩下 "up" 剥不掉。
_VENV_TOKENS = tuple(sorted(
    {"old", "new", "bak", "backup", "tmp", "temp", "copy",
     "venv", "py", "prev", "orig"},
    key=len, reverse=True))

# 精确匹配的名字
_SKIP_EXACT = frozenset({
    "memory",         # 本地开发笔记，不属于当前源码
    ".workbuddy",     # 兼容旧工作区
    "__pycache__",
    "build",          # PyInstaller 中间产物
    "dist",           # 打包产物（含源码副本，会污染搜索结果）
    ".git",
    "temp", "backups", "data", "updates", "user_plugins",
})


def is_skippable(name: str) -> bool:
    """这个目录名该不该在扫描时跳过。

    ★ venv 的判据是「**反复剥掉修饰词后剩下的必须是空的**」。
      这样既能覆盖各种变体，又不会误伤真实源码目录：

        .venv            → 空 → 跳
        .venv311         → 剥掉数字 → 空 → 跳
        .venv_old313_tmp → 剥掉 old / 313 / tmp → 空 → 跳
        venv-3.bak       → 剥掉 3 / bak → 空 → 跳

      对照（**不能跳**，它们是真实目录）：
        venv_helper      → 剥掉后还剩 helper → 留
        environment      → 剥掉后还剩 ironment → 留
        my_env           → 不是 venv 开头 → 留
    """
    if name in _SKIP_EXACT:
        return True
    low = name.lower()
    for p in _VENV_PREFIXES:
        if not (low == p or low.startswith(p)):
            continue
        rest = low[len(p):]
        # ⚠️ 「裸接」不算变体：`.venv311` / `venv3` 可以，
        #   但 `venvold` / `venvnew` 不行 —— 那是普通单词，
        #   很可能真是源码目录。裸接只允许**纯数字**。
        #   带分隔符（`.venv_old` / `venv-3`）则允许任意修饰词组合。
        bare_digits = rest.isdigit()
        if bare_digits or (rest[:1] in "._-"):
            # 循环剥：修饰词可以带数字（old313）、可以多个（old313_tmp）、
            # 可以用不同分隔符连（old-313-tmp）
            # ⚠️ 数字**也要能剥**，否则 `.venv311` 剥完修饰词还剩 "311"，
            #   会被判成"不是 venv"而漏掉。
            changed = True
            while changed and rest:
                changed = False
                # 先剥纯数字段
                digits = len(rest) - len(rest.lstrip("0123456789"))
                if digits:
                    rest = rest[digits:]
                    changed = True
                # 再剥修饰词
                for tok in _VENV_TOKENS:
                    if rest.startswith(tok):
                        rest = rest[len(tok):]
                        changed = True
                        break
                # 剥掉开头的分隔符
                if rest and rest[0] in "._-":
                    rest = rest[1:]
                    changed = True
            if rest == "":
                return True
    return False


def filter_dirs(names):
    """给 os.walk 的 `dirs[:] =` 用的现成过滤器。

    ★ 注意用 `[:]` 赋值（原地改），不是重新绑定 ——
      os.walk 读的是那个列表对象。
    """
    return [d for d in names if not is_skippable(d)]
