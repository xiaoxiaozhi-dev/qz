"""Run user tools on Maya's bridge main thread. Listing tools never imports them."""
from contextlib import redirect_stderr, redirect_stdout
import io
import keyword
import os
from pathlib import Path
import re
import sys
import time
import traceback
import types

from maya_bridge import register_action


class _Output(io.StringIO):
    """Bound retained output while preserving print's file-like interface."""
    def __init__(self):
        super().__init__()
        self.remaining = 32768
        self.truncated = False

    def write(self, text):
        length = len(text)
        available = self.remaining
        super().write(text[:available])
        self.remaining = max(0, available - length)
        self.truncated |= length > available
        return length


def _python(filepath, entry, script_id, directory):
    # Retain a real module so callbacks/windows keep their globals after return.
    # The unique module is separate from Maya's persistent __main__ namespace.
    module_name = '_qz_' + script_id
    module = types.ModuleType(module_name)
    module.__file__ = str(filepath)
    module.__package__ = ''
    sys.modules[module_name] = module
    # Keep paths available for callbacks that lazily import a tool's helpers.
    for path in (str(filepath.parent), str(directory)):
        if path not in sys.path:
            sys.path.append(path)
    if not entry:
        previous_main = sys.modules.get('__main__')
        module.__dict__['__name__'] = '__main__'
        sys.modules['__main__'] = module
        try:
            exec(compile(filepath.read_bytes(), str(filepath), 'exec'), module.__dict__)
        finally:
            if previous_main is None:
                sys.modules.pop('__main__', None)
            else:
                sys.modules['__main__'] = previous_main
    else:
        exec(compile(filepath.read_bytes(), str(filepath), 'exec'), module.__dict__)
        target = module
        for part in entry.split('.'):
            target = getattr(target, part)
        if not callable(target):
            raise ValueError('指定入口不是可调用函数：' + entry)
        target()


@register_action('user_script')
def user_script(op='run', filepath=None, entry='', script_id='', directory=None):
    if op == 'info':
        return dict(version=1, languages=['python', 'mel'])
    if op != 'run':
        raise ValueError('未知用户脚本操作')
    if not isinstance(script_id, str) or not re.fullmatch(r'user_[0-9a-f]{32}', script_id):
        raise ValueError('用户脚本编号无效')
    if not isinstance(filepath, str) or not isinstance(entry, str):
        raise ValueError('脚本路径或入口无效')
    filepath = Path(filepath).resolve()
    if not filepath.is_file() or filepath.suffix.lower() not in ('.py', '.mel'):
        raise ValueError('脚本文件不存在或类型不受支持')
    directory = Path(directory).resolve() if directory is not None else filepath.parent
    try:
        filepath.relative_to(directory)
    except ValueError:
        raise ValueError('脚本文件不在该工具目录内')
    if entry:
        if filepath.suffix.lower() == '.py':
            valid = all(part.isidentifier() and not keyword.iskeyword(part) for part in entry.split('.'))
        else:
            valid = re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', entry)
        if not valid:
            raise ValueError('入口应为函数或过程名称，不包含括号、参数或命令')
    output, started = _Output(), time.perf_counter()
    success, error = True, ''
    with redirect_stdout(output), redirect_stderr(output):
        try:
            if filepath.suffix.lower() == '.py':
                _python(filepath, entry, script_id, directory)
            else:
                import maya.mel as mel
                folders = os.environ.get('MAYA_SCRIPT_PATH', '').split(os.pathsep)
                for folder in (str(filepath.parent), str(directory)):
                    if folder not in folders:
                        folders.append(folder)
                os.environ['MAYA_SCRIPT_PATH'] = os.pathsep.join(filter(None, folders))
                mel.eval('rehash;')
                # MEL's source takes a quoted path; escape it as string data.
                path = str(filepath).replace('\\', '/').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
                mel.eval('source "' + path + '";')
                if entry:
                    mel.eval(entry + '();')
        except BaseException:
            # SystemExit/KeyboardInterrupt from a tool must not exit the bridge.
            success, error = False, traceback.format_exc()
    return dict(success=success, output=output.getvalue(), truncated=output.truncated,
                error=error, elapsed_seconds=round(time.perf_counter() - started, 3))
