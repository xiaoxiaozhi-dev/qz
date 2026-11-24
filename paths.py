"""
paths.py — 全项目**唯一**的路径解析入口。

⚠️ 为什么必须有这个文件（2026-10-20 打包时立的规矩）：

   打包前项目里有 **4 处**各自写着"怎么找项目根"：
       os.path.dirname(os.path.dirname(__file__))
   只有 `main.py` 和 `maya_setup.py` / `max_setup.py` 认了 `sys.frozen`，
   `main_window.py` 和 `settings_panel.py` 没认。

   而 PyInstaller 的 `--onefile` 模式下，模块被解包到
   `%TEMP%\\_MEIxxxxxx\\`，`__file__` 指向那个临时目录。
   于是打包后：
       config.json 读不到      → 端口回落默认值
       plugins/ 扫不到         → 资源树两个分支全空（**且不报错**）
       Max 安装路径指向临时目录 → 装出来的自动启动必然坏

   两套实现并存 = 迟早有一个漏改。现在**只有这一个文件**说了算。

★ 三种运行形态 ★
   1. 源码直接跑   → 项目根 = 本文件的上级目录
   2. 打包后运行   → 项目根 = exe 所在目录
   3. exe 拷到源码目录跑 → 同 2（资源从 exe 旁边读）
"""

import os
import sys
import json
import tempfile


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def project_dir() -> str:
    """项目根目录（绝对路径）。

    打包后 = exe 所在目录。外置资源（config.json / plugins/ /
    maya_bridge/ / max_bridge/ / ui/resources/）都放在那里。

    ⚠️ 用 `os.path.dirname(sys.executable)` 而不是 `sys._MEIPASS` ——
       `_MEIPASS` 是 onefile 的临时解包目录，进程结束就没了，
       写进 Maya userSetup.py 的路径会指向一个不存在的临时目录。

    ⚠️ 本文件在**项目根**（不在 ui/ 下），所以源码模式只退**一层**。
       早先写成退两层 → project_dir 变成了 Documents/，
       plugins/ 和 config.json 全找不到（而且不报错）。
       这正是"路径推导必须实测"的第四次教训。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """UI 资源目录（SVG 图标等）。

    外置，与其它资源同规则 —— 走 project_dir() 拼出来，
    不再用 `Path(__file__).parent`（frozen 后会指向 _internal）。
    """
    return os.path.join(project_dir(), "ui", "resources")


def resource(*parts) -> str:
    """资源文件完整路径。例：resource("lock.svg")"""
    return os.path.join(resource_dir(), *parts)


def plugins_dir() -> str:
    """插件目录。"""
    return os.path.join(project_dir(), "plugins")


def user_plugins_dir(dcc='maya') -> str:
    """用户工具按宿主分开，源码和打包模式均保存在程序目录内。"""
    if dcc not in ('maya', 'max', 'blender'):
        raise ValueError('不支持的用户脚本宿主')
    root = os.environ.get('QZ_USER_PLUGINS_DIR') or os.path.join(project_dir(), 'user_plugins')
    return os.path.join(root, dcc)


def local_data_dir(*parts) -> str:
    """本地用户数据；Git worktree 可读取主工作区未纳入版本库的 temp 数据。"""
    local = os.path.join(project_dir(), "temp", *parts)
    if os.path.isdir(local) or is_frozen():
        return local
    git_file = os.path.join(project_dir(), ".git")
    try:
        with open(git_file, encoding="utf-8-sig") as stream:
            marker = stream.read().strip()
        if not marker.startswith("gitdir:"):
            return local
        git_dir = os.path.abspath(os.path.join(project_dir(), marker[7:].strip()))
        with open(os.path.join(git_dir, "commondir"), encoding="utf-8") as stream:
            common = os.path.abspath(os.path.join(git_dir, stream.read().strip()))
        if os.path.basename(common) == ".git":
            shared = os.path.join(os.path.dirname(common), "temp", *parts)
            if os.path.isdir(shared):
                return shared
    except (OSError, ValueError):
        pass
    return local


def config_path() -> str:
    """User overrides; the official defaults are never edited by the user."""
    destination = os.path.join(user_data_dir(), 'settings.json')
    _migrate_json(os.path.join(project_dir(), 'config.json'), destination)
    if not os.path.exists(destination):
        _atomic_json(destination, {})
    return destination


def defaults_path() -> str:
    return os.path.join(project_dir(), 'defaults.json')


def user_data_dir() -> str:
    """Portable per-installation settings; tests can use a disposable directory."""
    directory = os.path.abspath(os.environ.get('QZ_DATA_DIR') or os.path.join(project_dir(), 'data'))
    os.makedirs(directory, exist_ok=True)
    return directory


def _atomic_json(destination, data):
    descriptor, staged = tempfile.mkstemp(prefix='.qz-settings-', dir=os.path.dirname(destination))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(staged, destination)
    finally:
        if os.path.exists(staged):
            os.remove(staged)


def _migrate_json(source, destination):
    if os.path.isfile(destination) or not os.path.isfile(source):
        return
    if os.environ.get('QZ_DATA_DIR'):
        return  # Never import real preferences into test overrides.
    try:
        with open(source, encoding='utf-8-sig') as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            _atomic_json(destination, data)
    except (OSError, ValueError) as error:
        print('[settings] 旧设置保留原处，迁移未完成：%s' % error)


def user_state_path(filename) -> str:
    if filename not in ('layout.json', 'theme.json'):
        raise ValueError('未知设置文件')
    destination = os.path.join(user_data_dir(), filename)
    _migrate_json(os.path.join(os.path.expanduser('~'), '.qz-tool', filename), destination)
    return destination


def dcc_dir(name: str) -> str:
    """DCC 端目录（maya_bridge / max_bridge / maya_actions / max_actions）。

    这些必须以**真实文件**形式存在于磁盘上 ——
    Maya 端 `from maya_bridge.server import run`、
    Max 端 `python.ExecuteFile(".../max_bridge/start_max.py")`
    写进 userSetup.py / startup.ms 的都是绝对路径。
    所以它们**不能**内嵌进 exe。
    """
    return os.path.join(project_dir(), name)
