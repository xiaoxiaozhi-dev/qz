"""Expose the current Blender profile's startup location to the Qz installer."""

from pathlib import Path


def get_startup_dir():
    import bpy

    directory = bpy.utils.user_resource("SCRIPTS", path="startup")
    if not directory or not Path(directory).is_absolute():
        raise RuntimeError("无法获取 Blender 用户启动脚本目录")
    return {"path": directory}


ACTIONS = {"get_startup_dir": get_startup_dir}
