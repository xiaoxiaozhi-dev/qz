"""
max_setup.py — 管理 3ds Max 端的自动启动配置。

对应 maya_setup.py，但机制不同：
  Maya  : 往 userSetup.py 写一个标记块（Python）
  Max   : 往 startup 目录丢一个 .ms 脚本（MAXScript）

Max 没有 userSetup.py 这种全局 Python 钩子，startup 目录只吃 .ms。
所以我们丢一个 .ms，它通过 python.Execute 执行单行启动代码，
再由 runpy.run_path 读取 start_max.py，兼容 Max2020 / Python2.7。

⚠️⚠️⚠️ 本文件曾有一整套「猜 startup 目录」的逻辑，已**全部删除**。
   原因是它连错三次：
     1. 看到 %LOCALAPPDATA%\\...\\3dsMax\\<版本>\\ 下只有 cache/CHS/ENU，
        断定「这是缓存目录」—— 错。CHS/ENU 是**语言子文件夹**，
        脚本就在 CHS\\scripts\\ 下。我把整棵树当缓存给砍了。
     2. 看到 Documents\\3ds Max 2027 下有 scenes/export/previews，
        推断 startup 跟着场景目录搬了 —— 也错，那是场景目录。
     3. 凭空编 getDir #systemScripts / #startup 两个 key，
        Max 报「该函数需要带有值的参数」并列出合法 key 列表。
   还有两个潜伏 bug：版本层和语言层都用 reverse **字符串**排序，
   而 ASCII 下大写字母 < 小写，所以语言层会把 ENU 排在 CHS 前面 ——
   哪天 ENU 下建出了 scripts\\startup，装的位置会悄悄跳走。

⇒ 教训：软件把配置放哪，**用它的 API 问它**，别看目录内容推断。
   现在安装/卸载「当前版本」的路径一律来自
   max_actions/get_startup_dir.py（Max 自己回答），这里不再推导。

【为什么「卸载所有版本」仍然扫目录】
   问 Max 解决的是「**写**」的风险 —— 写错版本代价大。
   而「卸载所有」是「我知道我要删什么，顺着目录找到它」，
   删不存在的目录顶多重说一句「没找到」，无害。
   所以 scan_startup_dirs() 只用于删除，**不用于写入**。
   ⚠️ 已知噪音：%LOCALAPPDATA% 下卸载 Max 后不会清目录，
      会扫到已卸载版本的目录，删除时说「该目录没有 qz 配置」并跳过。

标记块策略与 Maya 侧一致：只动我们自己那块，绝不碰用户其他脚本。
"""

import os
import json
import re

import paths

BLOCK_START = "-- >>> qz-bridge"
BLOCK_END = "-- <<< qz-bridge"
_PATTERN = re.compile(
    re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"[^\n]*\n?",
    re.DOTALL,
)

SCRIPT_NAME = "qz_bridge_startup.ms"


def script_path(startup_dir: str) -> str:
    """startup 目录 + 固定脚本名。

    ⚠️ startup_dir **必须**由调用方从 Max 的 get_startup_dir action 拿到。
       本模块不再提供任何推导或默认值 —— 宁可让调用方显式传，
       也不要在这里藏一个「猜的」fallback（那是三次连错的源头）。
    """
    return os.path.join(startup_dir, SCRIPT_NAME)


def scan_startup_dirs():
    """扫描本机**所有** Max 版本的 startup 目录。

    ⚠️ 只用于「卸载所有版本」，**绝不用于写入**。
       写入必须走 get_startup_dir 问 Max 本人。

    形状（2027 实测）：%LOCALAPPDATA%\\Autodesk\\3dsMax\\<版本>\\<语言>\\scripts\\startup
    这里遍历的是「存在什么」而不是「应该是谁」，
    所以不排序、不猜语义、不做版本过滤 ——
    删不存在的目录只是一句提示，无害。

    返回存在的 startup 目录列表；一个都没有返回 []。
    """
    ld = os.environ.get("LOCALAPPDATA", "")
    if not ld:
        return []
    root = os.path.join(ld, "Autodesk", "3dsMax")
    try:
        versions = os.listdir(root)
    except OSError:
        return []

    found = []
    for ver in versions:
        vdir = os.path.join(root, ver)
        if not os.path.isdir(vdir):
            continue
        try:
            langs = os.listdir(vdir)
        except OSError:
            continue
        for lang in langs:
            cand = os.path.join(vdir, lang, "scripts", "startup")
            if os.path.isdir(cand):
                found.append(cand)
    return found


def build_block() -> str:
    """生成 startup 脚本内容。

    ⚠️ try 和 catch 必须是**两个独立的块**，
       写成 try(...) catch(...) 是语法错误：
           语法错误: 位于 catch，需要 <因子>
       这条坑我早写在 MAXScript 坑位表里了，结果自己在下面又犯了一次。
       （同一个错误在 max_bridge/bootstrap.ms 犯过一次，这里是第二次。）
       以后凡是生成 .ms 文本，改完都跑 test_max_setup.py 的语法断言。

    整个 body 包在 try/catch 里：startup 脚本抛异常可能影响 Max 启动流程，
    不能把用户的其他工具链搞崩。

    路径用正斜杠：MAXScript 字符串里反斜杠是转义符，
    "C:\\Users\\..." 得写成 "C:/Users/..."，少一层出错的机会。
    """
    # ExecuteFile compiles Unicode source in Max2020/Python2.7, which rejects
    # encoding declarations. Reuse the proven runpy snippet as a quoted string.
    python_code = json.dumps(snippet(), ensure_ascii=True)
    # ⚠️ 这里的反斜杠必须是**最终 .ms 文件里的单个反斜杠**。
    #   MAXScript 字符串里 \n 是转义换行符，写成真实换行会让字符串跨行
    #   （而 MAXScript 又没有隐式续行）→ 又是一个语法错。
    #   Python 源码里要用 \\n 表示那一个反斜杠 + 一个 n。
    nl = "\\n"
    return (
        f"{BLOCK_START} v1 >>>\n"
        "-- 由 Qz Tools 设置面板自动生成，请勿手动编辑本块内容\n"
        "-- try / catch 必须是两个独立的块，不能写成 try(...) catch(...)\n"
        "try\n"
        "(\n"
        f'    python.Execute {python_code}\n'
        ")\n"
        "catch\n"
        "(\n"
        '    format "[qz-bridge] Max 端启动失败: %' + nl + '" '
        "(getCurrentException())\n"
        ")\n"
        f"{BLOCK_END} v1 <<<\n"
    )


def snippet() -> str:
    """给用户粘贴到 Max Python 侦听器的单行启动代码。

    ⚠️ 不要用 `exec(open(path).read())` ——
    那样代码跑在 `<string>` 命名空间里，**没有 __file__**，
    start_max.py 推导路径时会直接 NameError。
    用 Python 2.7 / 3 都支持的 runpy.run_path 设置真实 __file__，
    以 __main__ 执行脚本，由脚本入口启动一次。
    """
    project = paths.project_dir().replace("\\", "/")
    start_py = os.path.join(project, "max_bridge",
                            "start_max.py").replace("\\", "/")
    return (
        'import sys, runpy; '
        f'sys.path.insert(0, u{ascii(project)}); '
        f'_qz_start = u{ascii(start_py)}; '
        '_qz_start = _qz_start.encode(sys.getfilesystemencoding() or "mbcs") if sys.version_info[0] < 3 else _qz_start; '
        '_qz_bridge = runpy.run_path(_qz_start, run_name="__main__")'
    )


def has_script(path: str) -> bool:
    """path 必传 —— 不再提供默认路径，见 script_path() 的说明。"""
    p = path
    try:
        with open(p, "r", encoding="utf-8") as f:
            return bool(_PATTERN.search(f.read()))
    except Exception:
        return False


def write_script(path: str):
    """写入/替换 startup 脚本，返回 (消息, 状态)。

    path 必传（startup 目录由调用方从 Max 问来）。
    """
    p = path
    block = build_block()

    try:
        existing = ""
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                existing = f.read()
    except Exception as e:
        return "读取 startup 脚本失败：%s" % e, "failed"

    if _PATTERN.search(existing):
        # ⚠️ 必须用 lambda 做替换，不能直接传字符串 ——
        #   re.sub 的「替换串」里反斜杠会被当转义序列处理，
        #   而我们的块里有 MAXScript 的 \n（format 字符串里要用）。
        #   传字符串时它被解成真实换行，生成的 .ms 字符串就跨行了
        #   （而 MAXScript 没有隐式续行 → 又一个语法错）。
        #   传 lambda 时 re 不做任何转义解释，原样写入。
        new_text = _PATTERN.sub(lambda _m: block, existing)
        state = "updated"
    else:
        sep = "\n" if existing and not existing.endswith("\n") else ""
        new_text = existing + sep + ("\n" if existing else "") + block
        state = "created"

    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return "写入 startup 脚本失败：%s" % e, "failed"

    verb = "已写入" if state == "created" else "已更新"
    return "%s %s（重启 Max 后自动生效）" % (verb, p), state


def remove_script(path: str):
    """移除我们的块。返回 (消息, 状态)。path 必传。"""
    p = path
    try:
        if not os.path.isfile(p):
            return "startup 脚本不存在，无需卸载", "absent"
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return "读取 startup 脚本失败：%s" % e, "failed"

    if not _PATTERN.search(text):
        return "startup 脚本中没有 qz-bridge 配置，无需卸载", "absent"

    new_text = _PATTERN.sub("", text)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    new_text = new_text.rstrip("\n") + "\n"

    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return "写入 startup 脚本失败：%s" % e, "failed"

    return "已从 %s 移除" % p, "removed"


def remove_all_scripts():
    """卸载所有版本：扫描全部 Max startup 目录，逐个移除我们的块。

    ⚠️ 与 scan_startup_dirs() 一样，**只删不写**。
       不需要 Max 连接 —— 它不针对「当前这个实例」。

    返回 (消息, 详情列表, 状态)：
      状态 "removed" 至少删掉一个 / "absent" 一个都没删到 / "failed" 有失败
    """
    dirs = scan_startup_dirs()
    if not dirs:
        return ("没找到任何 3ds Max 的 startup 目录"
                "（%LOCALAPPDATA%/Autodesk/3dsMax 下为空）",
                [], "absent")

    details, removed, failed = [], 0, 0
    for d in dirs:
        msg, state = remove_script(script_path(d))
        details.append({"path": d, "state": state, "message": msg})
        if state == "removed":
            removed += 1
        elif state == "failed":
            failed += 1

    parts = []
    if removed:
        parts.append("已移除 %d 个" % removed)
    if failed:
        parts.append("失败 %d 个" % failed)
    skipped = len(dirs) - removed - failed
    if skipped:
        parts.append("%d 个目录无 qz 配置（跳过）" % skipped)
    summary = "卸载完成：" + "，".join(parts) if parts else "卸载完成"
    state = "failed" if failed else ("removed" if removed else "absent")
    return (summary, details, state)
