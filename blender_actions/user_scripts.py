"""Run managed Python scripts from the Blender bridge's main-thread timer."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import keyword
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid
from types import ModuleType


class _Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.remaining = 32768
        self.truncated = False

    def write(self, value):
        written = super().write(value[:self.remaining])
        self.remaining -= written
        self.truncated |= written < len(value)
        return len(value)


def _managed_file(filepath, directory, script_id, entry, user_root):
    if (not isinstance(script_id, str) or not re.fullmatch(r"user_[0-9a-f]{32}", script_id)
            or not all(isinstance(text, str) for text in (filepath, directory, entry))):
        raise ValueError("用户脚本路径、编号或入口无效")
    if entry and not all(part.isidentifier() and not keyword.iskeyword(part) for part in entry.split(".")):
        raise ValueError("Python 入口应为函数名称，不包含括号或参数")
    raw = Path(filepath)
    filepath = raw.resolve()
    payload = Path(directory).resolve()
    if payload == user_root:
        if (raw.is_symlink() or filepath.parent != user_root or not filepath.is_file()
                or filepath.name.startswith('.') or filepath.suffix.lower() != '.py' or entry
                or script_id != 'user_' + uuid.uuid5(uuid.NAMESPACE_URL, filepath.name).hex):
            raise ValueError("请选择 Blender 用户插件目录内的 Python 脚本")
        return filepath, payload
    if (payload.name != "files" or payload.parent.parent != user_root
            or not filepath.is_relative_to(payload) or filepath.suffix.lower() != ".py"):
        raise ValueError("请选择 Blender 用户插件目录内已导入的 Python 脚本")
    record = json.loads((payload.parent / "manifest.json").read_text(encoding="utf-8-sig"))
    if (not isinstance(record, dict) or record.get("version") != 1 or record.get("id") != script_id
            or record.get("dcc") != "blender" or record.get("language") != "python"
            or record.get("removed") or record.get("entry", "") != entry
            or not isinstance(record.get("script"), str) or Path(record["script"]).is_absolute()
            or (payload / record["script"]).resolve() != filepath):
        raise ValueError("用户脚本登记已变更，请刷新中台后重试")
    return filepath, payload


def _run_python(filepath, entry, script_id, directory):
    code = compile(filepath.read_bytes(), str(filepath), "exec")
    module_name = "_qz_blender_" + script_id
    module = ModuleType(module_name)
    module.__file__ = str(filepath)
    module.__package__ = ""
    sys.modules[module_name] = module
    # Retain the module and import paths for callbacks and delayed helper imports.
    for path in (str(filepath.parent), str(directory)):
        if path not in sys.path:
            sys.path.append(path)
    if entry:
        exec(code, module.__dict__)
        target = module
        for part in entry.split("."):
            target = getattr(target, part)
        if not callable(target):
            raise ValueError("指定入口不是可调用函数：" + entry)
        target()
        return
    previous_main = sys.modules.get("__main__")
    module.__name__ = "__main__"
    sys.modules["__main__"] = module
    try:
        exec(code, module.__dict__)
    finally:
        if previous_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = previous_main


def user_script(op="run", filepath=None, entry="", script_id="", directory=None, *, user_root):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("用户脚本必须在 Blender 主线程执行")
    if op == "info":
        return {"version": 1, "flat_files": True, "languages": ["python"], "python_version": list(sys.version_info[:3])}
    if op != "run":
        raise ValueError("未知用户脚本操作")
    filepath, payload = _managed_file(filepath, directory, script_id, entry, user_root)
    import bpy

    output, started = _Output(), time.perf_counter()
    error = ""
    with redirect_stdout(output), redirect_stderr(output):
        try:
            context = bpy.context
            window = context.window
            if window is None:
                manager = context.window_manager
                window = next(iter(manager.windows), None) if manager else None
            if window is None:
                raise RuntimeError("Blender 当前没有可用的窗口")
            with context.temp_override(window=window):
                _run_python(filepath, entry, script_id, payload)
        except BaseException:
            # A tool's SystemExit or KeyboardInterrupt must leave the bridge usable.
            error = traceback.format_exc()
    return dict(success=not error, output=output.getvalue(), truncated=output.truncated,
                error=error, elapsed_seconds=round(time.perf_counter() - started, 3))


ACTIONS = {"user_script_blender": user_script}
