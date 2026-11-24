# -*- coding: utf-8 -*-
"""
max_actions/get_startup_dir.py —— 问 Max 自己要 startup 脚本目录。

⚠️ 这个 action 存在的唯一理由：**不要猜路径**。
   我曾凭目录结构猜过三次，全错（详见 ui/max_setup.py 的注释）：

     1. 看到 ...\\3dsMax\\<版本>\\ 下只有 cache/CHS/ENU，
        断定"这是缓存目录" —— 错，CHS/ENU 是语言子文件夹，
        脚本就在 CHS\\scripts\\ 下。
     2. 看到 Documents\\3ds Max 2027 下有 scenes/export/previews，
        推断 startup 跟着场景目录搬了 —— 错，那是场景目录。
     3. 凭空编 getDir #systemScripts / #startup —— Max 直接报错
        「该函数需要带有值的参数」。

   ⇒ 软件把配置放哪，用它的 API 问它，别看目录内容推断。

⚠️ 为什么用 rt.execute 而不是 rt.getDir(字符串)：
   2027 实测 —— `rt.getDir("#userStartupScripts")` 抛 MAXScript 异常
   （Python 字符串到 MAXScript 的 name 字面量转换不成立），
   而 `rt.execute("getDir #userStartupScripts")` 正常返回路径。
   所以下面这行是**实测能跑**的写法，不要"简化"成 rt.getDir。

【为什么只问 #userStartupScripts 一个 key】
  另一个合法 key #startupScripts 指向安装目录
  （C:\\Program Files\\Autodesk\\3ds Max <版本>\\scripts\\startup），
  那里需要管理员权限，而且升级会被覆盖 —— 用户级目录才是装的地方。
"""

from max_bridge.server import register_action


@register_action("get_startup_dir")
def get_startup_dir():
    """返回 startup 脚本目录的绝对路径字符串。

    ⚠️ 这里只取路径，**文件由中台侧写**。
       Max 侧写文件要多绕一层 pymxs 文件 API，而我们要写的 .ms 里
       有中文注释（实测 393 字节里 3 行非 ASCII），
       UTF-8 编码 + 路径反斜杠转义的坑我们已经吃过三回。
       中台侧写就是 open(path, "w", encoding="utf-8")，一行。
    """
    import pymxs
    path = pymxs.runtime.execute("getDir #userStartupScripts")
    if not path:
        raise RuntimeError(
            "Max 返回的 #userStartupScripts 为空。\n"
            "请在 Max 的 MAXScript Listener 里手动跑一次确认：\n"
            "    getDir #userStartupScripts"
        )
    return str(path)
