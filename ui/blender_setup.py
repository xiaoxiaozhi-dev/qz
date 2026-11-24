"""Generate and manage Blender's user-profile bridge startup script."""

import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

import paths
from dcc_bridge import default_port

LEGACY_BRIDGE_MESSAGE = "请保存场景并重启 Blender，再运行连接代码以加载新版桥接。"
SCRIPT_NAME = "qz_bridge_startup.py"
SCRIPT_MARKER = "# Qz Tools managed Blender bridge startup"


def snippet(port=None):
    root = paths.project_dir().replace("\\", "/")
    user_root = os.path.abspath(paths.user_plugins_dir("blender")).replace("\\", "/")
    port = default_port("blender") if port is None else int(port)
    code = (
        "import sys\n"
        f"root = {root!r}\n"
        "if root not in sys.path:\n"
        "    sys.path.insert(0, root)\n"
        "from blender_bridge import server\n"
        "if not hasattr(server, 'reload_actions'):\n"
        f"    raise RuntimeError({LEGACY_BRIDGE_MESSAGE!r})\n"
        f"server.run(port={port}, user_root={user_root!r})\n"
    )
    return "exec(%s, {})" % ascii(code)


def _startup_source(port):
    return f'''{SCRIPT_MARKER}
import bpy


def _start_bridge():
    try:
        {snippet(port)}
    except Exception:
        import traceback
        traceback.print_exc()
    return None


def register():
    if not bpy.app.background and not bpy.app.timers.is_registered(_start_bridge):
        bpy.app.timers.register(_start_bridge, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_start_bridge):
        bpy.app.timers.unregister(_start_bridge)
'''


def _script_path(startup_dir):
    directory = Path(startup_dir)
    if not directory.is_absolute() or directory.name.casefold() != "startup":
        raise ValueError("Blender 返回的启动目录无效")
    return directory.resolve() / SCRIPT_NAME


def _is_installed(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError(f"启动脚本目标不是普通文件：{path}")
    with path.open(encoding="utf-8-sig") as stream:
        if stream.readline().rstrip("\r\n") != SCRIPT_MARKER:
            raise FileExistsError(f"存在同名脚本，无法修改：{path}")
    return True


def write_script(startup_dir, port=None):
    """Install in the startup directory reported by Blender."""
    path = _script_path(startup_dir)
    source = _startup_source(port)
    _is_installed(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                dir=path.parent, prefix=".qz-startup-", suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(source)
        _is_installed(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def remove_script(startup_dir):
    """Remove the Qz-managed file in the current Blender profile."""
    path = _script_path(startup_dir)
    if not _is_installed(path):
        return None
    path.unlink()
    return path
