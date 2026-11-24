# -*- coding: utf-8 -*-
"""
test_max_ms.py — 静态校验 max_bridge/bootstrap.ms 的 MAXScript 源码

为什么需要它：MAXScript 的语法错误只在 Max 运行时才暴露，而且报错信息
几乎不给上下文（"语法错误: 位于 +，需要 <因子>" 这种）。与其每次都开 Max 试错，
不如在提交前把已知踩过的坑静态拦下来。

检查项对应 docs/max_bridge_design.md 踩坑表里的 MAXScript 部分。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("  -> " + str(detail)) if detail else ""))


def render():
    """按 start_max.py 的真实逻辑渲染 bootstrap.ms（替换占位符）。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_qz_start_max", os.path.join(here, "max_bridge", "start_max.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with open(mod.BOOTSTRAP, "r", encoding="utf-8") as f:
        ms = f.read()
    ms = ms.replace("@WORKER@", mod.WORKER.replace("\\", "/"))
    ms = ms.replace("@PROJECT@", mod.PROJECT_DIR.replace("\\", "/"))
    from max_bridge import server
    ms = ms.replace("@FAILURE@", server.WORKER_FAILURE_PATH.replace("\\", "/"))
    return ms, mod


def code_lines(ms):
    """剥掉块注释和行注释，只留真正的 MAXScript 代码行。

    不剥的话，注释里为了记录坑而写的示例文本会被误判成真代码
    （比如第 6 条坑说明里就出现了 try(...)catch(...) 字样）。
    """
    out, in_block = [], False
    for raw in ms.splitlines():
        line = raw
        if in_block:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block = False
            else:
                continue
        while "/*" in line:
            before, _, rest = line.partition("/*")
            if "*/" in rest:
                line = before + rest.split("*/", 1)[1]
            else:
                line = before
                in_block = True
                break
        # 去掉行注释（MAXScript 用 -- ）
        line = re.split(r"--(?![^\"\']*[\"\'])", line, maxsplit=1)[0]
        out.append(line)
    return out


def _py_lines(py_src):
    """剥掉 Python 注释（含 docstring），只留代码行。

    code_lines() 剥的是 MAXScript 的 -- 注释和 /* */ 块注释，
    对 Python 的 # 注释和三引号 docstring 无效 ——
    docstring 里为了记录坑会写着 try(...) catch(...)，直接搜会自我误判。
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(py_src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string)
    except Exception:
        # tokenize 失败就退回朴素剥注释
        for raw in py_src.splitlines():
            line = raw.split("#", 1)[0] if not raw.strip().startswith(
                ('"', "'")) else ""
            out.append(line)
    return out


def _odd_quotes(lines):
    return [l for l in lines if l.count('"') % 2 == 1]


def main():
    ms, mod = render()
    lines = ms.splitlines()
    code = code_lines(ms)

    print("=" * 60)
    print("bootstrap.ms 静态校验（不启动 Max）")
    print("=" * 60)

    # ── 1. 纯 ASCII ────────────────────────────────
    print("\n1. 编码")
    bad = [(i + 1, l) for i, l in enumerate(lines)
           if any(ord(c) > 127 for c in l)]
    check("全文件纯 ASCII", not bad,
          "非 ASCII 行: %s" % [b[0] for b in bad] if bad else "")

    # ── 2. 占位符 ──────────────────────────────────
    print("\n2. 占位符替换")
    check("@WORKER@ 已替换", "@WORKER@" not in ms)
    check("@PROJECT@ 已替换（当前无引用，应已消失）", "@PROJECT@" not in ms)
    check("worker 路径用正斜杠（MAXScript 字符串里反斜杠是转义符）",
          mod.WORKER.replace("\\", "/") in ms and mod.WORKER.replace("\\", "/") != mod.WORKER)

    # ── 3. 无跨行字符串拼接 ─────────────────────────
    print("\n3. 字符串续行（MAXScript 不支持隐式续行）")
    cont = [(i + 1, l.strip()) for i, l in enumerate(code)
            if l.strip().startswith("+")]
    check("没有以 + 开头的续行", not cont, cont)

    # ── 4. try/catch 必须是两个块 ────────────────────
    print("\n4. try / catch 写法")
    inline_trycatch = [(i + 1, l.strip()) for i, l in enumerate(code)
                       if re.search(r"\)\s*catch\s*\(", l)]
    check("没有 try(...)catch(...) 单表达式写法", not inline_trycatch,
          inline_trycatch)

    # ── 5. 不嵌 Python 源码 ─────────────────────────
    print("\n5. 不在 MAXScript 里嵌多行 Python")
    py_calls = [(i + 1, l.strip()) for i, l in enumerate(code)
                if "python.Execute" in l and not
                re.search(r'\bpython\.ExecuteFile\s+"', l)]
    check("没有 python.Execute 调 Python 源码", not py_calls, py_calls)

    # ── 6. rollout 必须顶层 + timer 带 name ───────────
    print("\n6. rollout / timer 结构")
    # ★ 用 code（已剥注释）而不是 lines ★
    #   注释里为了记录坑会写出 CreateDialog / rollout 等字样，
    #   用 lines 会把注释当成真代码统计，断言结果毫无意义。
    top_rollout = [l.strip() for l in code
                   if l.strip().startswith("rollout ")]
    check("rollout 声明在顶层（行首无缩进）", len(top_rollout) == 1, top_rollout)
    check("rollout 只出现一次", len(top_rollout) == 1)
    timer_lines = [l.strip() for l in code if "timer tmrTick" in l]
    real_timer = [l for l in timer_lines if l.startswith("timer tmrTick \"qzClock\"")]
    check("timer 带必需的 name 参数", len(real_timer) >= 1, timer_lines)
    check("on tmrTick tick do 是独立块",
          any(l.strip() == "on tmrTick tick do" for l in code))

    tick_body = "\n".join(code)
    tick_start = tick_body.index("on tmrTick tick do")
    tick_end = tick_body.index("CreateDialog", tick_start)
    tick_body = tick_body[tick_start:tick_end]
    check("执行 worker 前停用 timer，防止处理 UI 消息时重入",
          tick_body.index("tmrTick.active = false") < tick_body.index("python.ExecuteFile"))
    check("busy 与 failed 状态阻止重复进入 worker",
          "if not workerBusy and not workerFailed do" in tick_body)
    check("SDK 异常被 MAXScript 捕获并保留错误原因",
          "catch" in tick_body and "lastWorkerError = getCurrentException()" in tick_body)
    check("worker 失败时停止轮询，仅成功时恢复 timer",
          re.search(r"if workerFailed then\s*\([\s\S]*worker stopped after error[^\n]*\n\s*\)\s*else\s*\(\s*tmrTick.active = qzBridgeEnabled", tick_body) is not None)
    check("SDK 异常向网络等待线程写失败标记，不再调用 Python",
          "@FAILURE@" not in ms and "createFile" in tick_body)

    # ── 7. 必需的安全样式 ────────────────────────────
    print("\n7. 对话框样式")
    create = [l for l in code if "CreateDialog" in l]
    check("CreateDialog 隐藏窗口（防露出小窗）",
          create and "#style_no_caption" in create[0]
          and "#style_toolwindow" in create[0], create)
    # ★ MAXScript 没有 #style_hidden，光靠上面两个样式仍会在视口留一个
    #   1x1 边框小框（2027 实测）。挪出屏幕（.pos）实测**无效**，Max 会拉回
    #   可见区域；.size 会直接抛「未知属性」。所以 bootstrap.ms 里不做任何
    #   窗口位置/尺寸赋值，隐藏统一交给 start_max.py 的 ShowWindow(SW_HIDE)。
    check("bootstrap.ms 不试图用 MAXScript 挪动/缩放 rollout",
          not any((".pos" in l or ".size" in l or "moveTo" in l or "resize" in l)
                  for l in code),
          [l for l in code if ".pos" in l or ".size" in l])

    # ── 8. 幂等：先关旧实例 ─────────────────────────
    print("\n8. 幂等")
    check("执行前 destroyDialog 旧实例（防 timer 叠加）",
          "destroyDialog" in ms)
    check("用 undefined 判断而非裸变量（避免未定义报错）",
          "qzBridgeRollout != undefined" in ms)

    # ── 9. start_max.py 的前置检查 ───────────────────
    print("\n9. start_max.py 前置检查")
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "max_bridge", "start_max.py"),
              "r", encoding="utf-8") as f:
        sm = f.read()
    check("检查 worker.py 存在", "worker.py" in sm and "WORKER" in sm)
    check("bootstrap 执行失败有兜底提示", "bootstrap.ms 执行失败" in sm)
    check("失败时提示 ticks=0 的排障方向", "ticks=0" in sm)

    # ── 10. start_max.py 里内联 MAXScript 的语法 ────────
    # ⚠️ 这段是同一个坑的第四次现身：
    #   stop() 里曾写成 try(destroyDialog ...) catch()，
    #   和 bootstrap.ms、build_block() 里的错误一模一样。
    #   内联 MAXScript 散在 Python 字符串里，没有 .ms 可供静态校验，
    #   所以这里专门扫一遍 —— 凡是 "try(" / ")catch(" 出现即失败。
    print("\n10. start_max.py 内联 MAXScript 语法")
    sm_code = _py_lines(sm)
    sm_ns = "".join(sm_code).replace(" ", "").replace("\\n", "")
    check("没有 try(...) 写法（必须是两个独立块）",
          "try(" not in sm_ns,
          [l.strip() for l in sm_code if "try(" in l.replace(" ", "")])
    check("没有 )catch( 写法",
          ")catch(" not in sm_ns,
          [l.strip() for l in sm_code if ")catch(" in l.replace(" ", "")])
    check("内联 MAXScript 里无未闭合引号",
          not _odd_quotes(sm_code),
          [l for l in sm_code if l.count('"') % 2 == 1])
    check("★ 用 ShowWindow(SW_HIDE) 隐藏窗口",
          "ShowWindow" in sm and "FindWindowW" in sm)
    check("★ 注释说明了 destroyDialog 会停 timer（防后人改回）",
          "destroyDialog 会停 timer" in sm or "会停 timer" in sm)

    print("\n11. Rollout 窗口隐藏：不得依赖 MAXScript 属性赋值")
    # ★ Rollout 没有 .size 属性（2027 实测报「未知属性:"size"」），
    #   赋值抛异常会中断 bootstrap 后续语句，且 start() 提前 return
    #   导致 ShowWindow 从未执行 —— 小框残留 + timer 没起来，一个根因两个现象。
    ms_body = "".join(l.strip() for l in code_lines(ms))
    check("bootstrap.ms 不含 .size 赋值（Rollout 无此属性）",
          ".size" not in ms_body,
          [l.strip() for l in code_lines(ms) if ".size" in l])
    check("bootstrap.ms 不含 .pos 赋值（实测 Max 会拉回可见区域）",
          ".pos" not in ms_body,
          [l.strip() for l in code_lines(ms) if ".pos" in l])
    # start() 里隐藏调用必须在 bootstrap 的 except 分支**之后**执行
    _exc = sm.index("bootstrap.ms 执行失败") if "bootstrap.ms 执行失败" in sm else -1
    _hide = sm.index("_hide_rollout_window()", sm.index("def start("))
    check("★ 隐藏窗口不在 bootstrap 失败分支内被 return 跳过",
          _exc >= 0 and _hide > _exc)
    check("★ bootstrap 失败时不再提前 return（在隐藏之后才 return）",
          re.search(r"_hide_rollout_window\(\)\s*\n\s*if not ok:\s*\n\s*srv.stop\(\)", sm) is not None)

    print("\n" + "=" * 60)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
    print("=" * 60)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
