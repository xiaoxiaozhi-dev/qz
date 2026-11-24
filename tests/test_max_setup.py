# -*- coding: utf-8 -*-
"""
test_max_setup.py — max_setup.py 的验证（不碰真实 Max startup 目录）

重点验证「只动自己的标记块，绝不碰用户其他内容」——
这与 maya_setup 的测试思路一致。
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths  # noqa: E402
import max_setup  # noqa: E402  (ui/max_setup.py)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("  -> " + str(detail)) if detail else ""))


def _src_of(fn) -> str:
    """函数源码（用于「实现方式」这类断言，如是否用了 lambda 替换）。"""
    import inspect
    try:
        return inspect.getsource(fn)
    except Exception:
        return ""


def _has_broken_string(code: str) -> bool:
    """字符串字面量是否跨了行。

    MAXScript 没有隐式续行，一个 "..." 里如果出现了真实换行就是语法错。
    这里用最简规则：逐行数引号，出现奇数个引号的行就是没收尾。
    """
    for line in code.splitlines():
        if line.count('"') % 2 == 1:
            return True
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="qz_max_setup_")
    # 用一个「已有用户内容」的文件，模拟真实 startup 脚本
    target = os.path.join(tmp, "user_stuff.ms")
    original = (
        "-- 用户的公司工具链，千万别动\n"
        "fn companyTool = true\n"
        "\n"
        "callbacks.addScript #postRender \"fn companyTool\"\n"
    )
    with open(target, "w", encoding="utf-8") as f:
        f.write(original)

    try:
        print("1. 首次写入")
        msg, state = max_setup.write_script(target)
        check("状态为 created", state == "created", msg)
        check("has_script 为 True", max_setup.has_script(target))
        with open(target, encoding="utf-8") as f:
            text = f.read()
        check("保留了用户原有内容", "fn companyTool = true" in text)
        check("包含 Python2.7 runpy 启动入口", 'python.Execute ' in text and 'runpy.run_path' in text)

        print("\n2. 重复写入（应就地替换，不重复追加）")
        msg, state = max_setup.write_script(target)
        check("状态为 updated", state == "updated", msg)
        with open(target, encoding="utf-8") as f:
            text2 = f.read()
        check("标记块只有一份", text2.count(max_setup.BLOCK_START) == 1,
              text2.count(max_setup.BLOCK_START))
        check("用户内容仍在", "fn companyTool = true" in text2)

        print("\n3. 路径正确性")
        check("project_dir 指向 qz 根",
              os.path.normcase(os.path.realpath(paths.project_dir())) ==
              os.path.normcase(os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))),
              paths.project_dir())
        blk = max_setup.build_block()
        real_start = os.path.join(paths.project_dir(), "max_bridge",
                                  "start_max.py")
        check("block 里的 start_max.py 路径真实存在",
              os.path.isfile(real_start), real_start)
        import json
        execute_line = next(line.strip() for line in blk.splitlines() if line.strip().startswith('python.Execute '))
        decoded = json.loads(execute_line[len('python.Execute '):])
        check("MAXScript 字符串完整保留启动代码与路径", decoded == max_setup.snippet())

        # ★ MAXScript 语法断言 ★
        # 踩过两次同款错误：try(...) catch(...) 写成两个表达式会报
        #   语法错误: 位于 catch，需要 <因子>
        # 第一次在 max_bridge/bootstrap.ms，第二次就在这个 build_block 里
        # —— 而当时测试里只有"路径正确性"，没有一条语法断言，所以放过去了。
        code = "\n".join(l for l in blk.splitlines()
                         if not l.strip().startswith("--"))
        # ⚠️ 断言必须基于**剥掉注释**的 code。
        #   注释里为了记录这条坑，本身就写着 try(...) catch(...)，
        #   直接在原始文本里搜会自我误判（同一个坑，test_max_ms.py 犯过）。
        code_nospace = code.replace(" ", "")
        print("\n3b. MAXScript 语法（build_block 生成的内容）")
        check("try 后面是独立块，不是 try(",
              "try(" not in code_nospace, code)
        check("catch 是独立块，不是 )catch(",
              ")catch(" not in code_nospace, code)
        check("有裸的 try 关键字独占一行",
              any(l.strip() == "try" for l in code.splitlines()), code)
        check("有裸的 catch 关键字独占一行",
              any(l.strip() == "catch" for l in code.splitlines()), code)
        # 括号配平（少了右括号 Max 也会报语法错）
        check("圆括号配平",
              code.count("(") == code.count(")"),
              "( =%d ) =%d" % (code.count("("), code.count(")")))
        # ⚠️ format 里的 \n 必须落成**字面两字符** \n，
        #   不能是真实换行 —— MAXScript 字符串跨行且没有隐式续行，又会语法错。
        #   踩过一次：Python 侧少写一层转义，生成的 .ms 里就成了真换行。
        fmt_lines = [l for l in code.splitlines() if "format" in l]
        check("format 的 \\n 是字面两字符（未被转成真实换行）",
              all("\\n" in l for l in fmt_lines) and fmt_lines, fmt_lines)
        check("字符串字面量内没有裸换行（MAXScript 无隐式续行）",
              not _has_broken_string(code), code)
        # ★★ 元凶级回归：re.sub 的「替换串」会解释反斜杠转义 ★★
        #   _PATTERN.sub(block, existing) 传字符串时，块里的 \n 被解成
        #   真实换行 → 生成的 .ms 字符串跨行 → Max 报语法错。
        #   传 lambda 才不做任何转义解释。
        #   表现：build_block() 单元测试全过，但写出去的文件是坏的。
        rewritten_probe = "%s\nPRE\n%s\n" % (max_setup.BLOCK_START,
                                               max_setup.BLOCK_END)
        good = max_setup._PATTERN.sub(lambda _m: blk, rewritten_probe)
        bad = max_setup._PATTERN.sub(blk, rewritten_probe)
        g_fmt = [l for l in good.splitlines() if "format" in l]
        b_fmt = [l for l in bad.splitlines() if "format" in l]
        check("★ lambda 替换：\\n 保持字面", g_fmt
              and all("\\n" in l for l in g_fmt), g_fmt)
        # 反向确认：字符串替换确实会破坏 —— 否则上面的断言可能恒真
        check("★ 反向确认：字符串替换确实破坏 \\n（断言抓得住真问题）",
              b_fmt and any("\\n" not in l for l in b_fmt), b_fmt)
        check("★ write_script 用 lambda 替换（不能直接传字符串）",
              "_PATTERN.sub(lambda" in _src_of(max_setup.write_script),
              "write_script 里有 _PATTERN.sub(<字符串>) 会被转义破坏")
        check("只有一次 Python 启动调用（避免重复起 bridge）",
              code.count('python.Execute ') == 1 and 'python.ExecuteFile' not in code)

        print("\n4. snippet 可执行")
        import ast
        snip = max_setup.snippet()
        try:
            ast.parse(snip)
            check("snippet 语法正确", True)
        except SyntaxError as e:
            check("snippet 语法正确", False, e)

        print("\n5. 卸载")
        msg, state = max_setup.remove_script(target)
        check("状态为 removed", state == "removed", msg)
        with open(target, encoding="utf-8") as f:
            after = f.read()
        check("与原文件逐字节一致", after == original, repr(after[:80]))

        print("\n6. 重复卸载 / 不存在")
        msg, state = max_setup.remove_script(target)
        check("重复卸载返回 absent", state == "absent", msg)
        msg, state = max_setup.remove_script(
            os.path.join(tmp, "nope.ms"))
        check("文件不存在返回 absent", state == "absent", msg)

        print("\n7. ★ 路径不再推导：接口收窄为显式传参")
        # ⚠️ 这里曾有一整套「猜 startup 目录」的逻辑（max_startup_dir /
        #   _sorted_dirs / _max_version_dirs / _tail_num），**已全部删除**。
        #   原因是我靠猜连错三次（详见 ui/max_setup.py 模块注释）：
        #     1. 看到 <版本>\ 下只有 cache/CHS/ENU 就断定是缓存目录 —— 错，
        #        CHS/ENU 是**语言子文件夹**，脚本就在 CHS\scripts\ 下
        #     2. 看到 Documents\3ds Max <版本> 有场景目录，推断 startup 跟着搬
        #     3. 凭空编 getDir #systemScripts / #startup，Max 直接报错
        #   而且版本层/语言层都用 reverse **字符串**排序，ASCII 下大写 < 小写，
        #   语言层会把 ENU 排在 CHS 前面 —— 哪天 ENU 下建出 scripts\startup，
        #   装的位置会悄悄跳走。
        # 现在路径一律由调用方从 max_actions/get_startup_dir 拿（Max 自报）。
        check("★ max_startup_dir 已删除",
              not hasattr(max_setup, "max_startup_dir"))
        check("★ 猜目录的辅助函数已删除",
              not hasattr(max_setup, "_sorted_dirs")
              and not hasattr(max_setup, "_max_version_dirs")
              and not hasattr(max_setup, "_tail_num"))
        check("★ script_path 无默认参数",
              max_setup.script_path.__defaults__ is None,
              max_setup.script_path.__defaults__)
        check("★ write_script / remove_script / has_script 都无默认参数",
              max_setup.write_script.__defaults__ is None
              and max_setup.remove_script.__defaults__ is None
              and max_setup.has_script.__defaults__ is None)
        # 反向验证：不传参数必须 TypeError（没有偷偷藏 fallback）
        for name, fn in (("script_path", max_setup.script_path),
                         ("write_script", max_setup.write_script),
                         ("remove_script", max_setup.remove_script),
                         ("has_script", max_setup.has_script)):
            try:
                fn()
                check("★ %s() 不传参数会 TypeError" % name, False,
                      "竟然有默认值")
            except TypeError:
                check("★ %s() 不传参数会 TypeError" % name, True)

        print("\n7b. script_path 拼接")
        sp = max_setup.script_path(os.path.join("C:", "a", "b", "startup"))
        check("拼出 qz_bridge_startup.ms",
              sp.endswith("qz_bridge_startup.ms"), sp)
        check("父目录保持不变",
              os.path.basename(os.path.dirname(sp)) == "startup", sp)

        print("\n7c. scan_startup_dirs：认到语言子目录，只收真实存在的")
        fake_ld = os.path.join(tmp, "ld2")
        lang_dir = os.path.join(fake_ld, "Autodesk", "3dsMax",
                                "2027 - 64bit", "CHS", "scripts", "startup")
        os.makedirs(lang_dir, exist_ok=True)
        # 同版本下的 cache 也建出来 —— 它没有 scripts/startup，不该被收
        os.makedirs(os.path.join(fake_ld, "Autodesk", "3dsMax",
                                 "2027 - 64bit", "cache"), exist_ok=True)
        # 另一个版本，只有一半结构（没有 scripts/startup），不该被收
        os.makedirs(os.path.join(fake_ld, "Autodesk", "3dsMax",
                                 "2025 - 64bit", "ENU"), exist_ok=True)
        orig_local = os.environ.get("LOCALAPPDATA")
        try:
            os.environ["LOCALAPPDATA"] = fake_ld
            got = max_setup.scan_startup_dirs()
            check("只找到真实存在的 startup 目录", got == [lang_dir], got)
        finally:
            if orig_local is not None:
                os.environ["LOCALAPPDATA"] = orig_local

        print("\n7d. scan_startup_dirs 对空/坏输入不抛异常")
        try:
            os.environ["LOCALAPPDATA"] = os.path.join(tmp, "no_such_ld")
            check("不存在的 LOCALAPPDATA 返回空",
                  max_setup.scan_startup_dirs() == [])
            os.environ.pop("LOCALAPPDATA", None)
            check("没有 LOCALAPPDATA 返回空",
                  max_setup.scan_startup_dirs() == [])
        finally:
            if orig_local is not None:
                os.environ["LOCALAPPDATA"] = orig_local

        print("\n7e. remove_all_scripts：逐个目录删，不需要 Max 连接")
        fake_ld2 = os.path.join(tmp, "ld3")
        d1 = os.path.join(fake_ld2, "Autodesk", "3dsMax",
                          "2027 - 64bit", "CHS", "scripts", "startup")
        d2 = os.path.join(fake_ld2, "Autodesk", "3dsMax",
                          "2026 - 64bit", "CHS", "scripts", "startup")
        os.makedirs(d1)
        os.makedirs(d2)
        # d1 装了，d2 没装
        with open(max_setup.script_path(d1), "w", encoding="utf-8") as f:
            f.write(max_setup.build_block())
        try:
            os.environ["LOCALAPPDATA"] = fake_ld2
            msg, details, state = max_setup.remove_all_scripts()
            check("★ 状态 removed（至少删掉一个）", state == "removed", msg)
            check("★ 有 qz 配置的那个被清空",
                  not max_setup.has_script(max_setup.script_path(d1)), d1)
            check("★ 没装的目录跳过而不是报错",
                  len(details) == 2
                  and sum(1 for x in details if x["state"] == "absent") == 1,
                  details)
            check("★ 消息里区分了跳过数", "跳过" in msg, msg)
            msg2, _d2, state2 = max_setup.remove_all_scripts()
            check("★ 重复调用返回 absent", state2 == "absent", msg2)
        finally:
            if orig_local is not None:
                os.environ["LOCALAPPDATA"] = orig_local

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  失败: " + f)
    print("=" * 62)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
