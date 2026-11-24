"""
maya_setup.py — 管理 Maya 端的userSetup.py 自动启动配置。

背景：中台要连Maya，Maya 端必须跑起 maya_bridge/server。
两种方式：
  1. 手动：复制代码到 Maya 脚本编辑器运行（一次性，不用重启 Maya）
  2. 自动：写一段到 userSetup.py，以后每次开 Maya 自动启动

userSetup.py 是用户的全局脚本，可能已有公司工具链（本机就有 vaccine），
所以**只追加/替换我们自己那一个标记块，绝不碰其他内容**。

标记块形如：
    # >>> qz-bridge v1 >>>
    ...代码...
    # <<< qz-bridge v1 <<<

策略：标记块存在 → 就地替换（路径变了也能自愈）；不存在 → 追加到末尾。
不做备份（用户明确要求），因为只动标记块内的内容。
"""

import os
import re

import paths


# 标记块起止标记。用带版本的正则，v1→v2 升级时也能识别旧块
BLOCK_START = "# >>> qz-bridge"
BLOCK_END = "# <<< qz-bridge"
_PATTERN = re.compile(
    re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"[^\n]*\n?",
    re.DOTALL,
)


def user_setup_path() -> str:
    """Maya userSetup.py 路径。

    标准位置是 Documents/maya/scripts/userSetup.py（不区分 Maya 版本），
    优先用 MAYA_APP_DIR 推导，拿不到再退回标准路径。
    """
    app_dir = os.environ.get("MAYA_APP_DIR", "")
    if app_dir:
        p = os.path.join(app_dir, "scripts", "userSetup.py")
        if os.path.isfile(p):
            return p
    home = os.path.expanduser("~")
    return os.path.join(home, "Documents", "maya", "scripts", "userSetup.py")


def build_block() -> str:
    """生成标记块内容。

    用try/except 包起来：userSetup.py 里抛异常会影响 Maya 后续启动流程，
    我们的代码不能把用户的公司工具链搞崩。
    """
    p = paths.project_dir().replace("\\", "/")
    return (
        f"{BLOCK_START} v1 >>>\n"
        "# 由 Qz Tools 设置面板自动生成，请勿手动编辑本块内容\n"
        "try:\n"
        "    import sys\n"
        f'    _qz = r"{p}"\n'
        "    if _qz not in sys.path:\n"
        "        sys.path.insert(0, _qz)\n"
        "    from maya_bridge.server import run\n"
        "    run()\n"
        "except Exception as _e:\n"
        '    print("[qz-bridge] 启动失败: %s" % _e)\n'
        f"{BLOCK_END} v1 <<<\n"
    )


def snippet() -> str:
    """可直接粘贴到 Maya Python 输入区执行的单行启动代码。"""
    p = paths.project_dir().replace("\\", "/")
    code = (
        f'import sys\n'
        f'sys.path.insert(0, {p!r})\n'
        f'try:\n'
        f'    import maya_bridge.server as _s\n'
        f'    if getattr(_s, "BRIDGE_PROTOCOL", 0) < 3:\n'
        f'        raise RuntimeError("桥接核心需要更新，请保存场景并重启 Maya 后再连接")\n'
        f'    _s.run()\n'
        f'except Exception as _e:\n'
        f'    print("[qz-bridge] 启动失败: %s" % _e)\n'
    )
    # Preserve the protocol check and error handler inside one input statement.
    return 'exec(%s)' % ascii(code)


def has_block(path: str = None) -> bool:
    """userSetup 里是否已有我们的标记块。"""
    p = path or user_setup_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            return bool(_PATTERN.search(f.read()))
    except Exception:
        return False


def remove_block(path: str = None):
    """移除标记块，返回 (结果消息, 状态)。

    状态: "removed" 已移除 / "absent" 本来就没有 / "failed" 失败
    只删自己的标记块，用户其他内容一律不动。
    """
    p = path or user_setup_path()
    try:
        if not os.path.isfile(p):
            return "userSetup 不存在，无需卸载", "absent"
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"读取 userSetup 失败：{e}", "failed"

    if not _PATTERN.search(text):
        return "userSetup 中没有 qz-bridge 配置，无需卸载", "absent"

    new_text = _PATTERN.sub("", text)
    # 收拾一下可能留下的多余空行
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)

    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return f"写入 userSetup 失败：{e}", "failed"

    return f"已从 {p} 移除", "removed"


def write_block(path: str = None):
    """写入/替换标记块，返回 (结果消息, 状态)。

    状态: "created" 新建 / "updated" 替换 / "failed" 失败
    """
    p = path or user_setup_path()
    block = build_block()

    try:
        existing = ""
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                existing = f.read()
    except Exception as e:
        return f"读取 userSetup 失败：{e}", "failed"

    if _PATTERN.search(existing):
        new_text = _PATTERN.sub(block, existing)
        state = "updated"
    else:
        # 追加到末尾，中间空一行隔开
        sep = "\n" if existing and not existing.endswith("\n") else ""
        new_text = existing + sep + ("\n" if existing else "") + block
        state = "created"

    try:
        # 先确保目录存在（Maya 没起过时 scripts 目录可能还没建）
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return f"写入 userSetup 失败：{e}", "failed"

    verb = "已写入" if state == "created" else "已更新"
    return f"{verb} {p}", state
