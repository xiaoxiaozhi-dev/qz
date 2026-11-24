# -*- coding: utf-8 -*-
"""Execute user tools on the bridge main thread, in Python2.7 or Python3."""
from __future__ import unicode_literals

import io
import keyword
import os
import re
import sys
import threading
import time
import traceback
import types

from max_bridge.server import register_action

try:
    text_type = unicode
    string_types = (basestring,)
except NameError:
    text_type = str
    string_types = (str,)

_MAIN_THREAD = threading.current_thread()
_ID = re.compile(r'user_[0-9a-f]{32}\Z')
_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')


def _text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def _path(value):
    if not isinstance(value, string_types):
        raise ValueError('脚本路径无效')
    if not isinstance(value, text_type):
        value = value.decode(sys.getfilesystemencoding() or 'mbcs')
    return os.path.realpath(os.path.abspath(value))


def _filename(value):
    return value.encode(sys.getfilesystemencoding() or 'mbcs') if sys.version_info[0] < 3 else value


class _Output(object):
    encoding = 'utf-8'

    def __init__(self):
        self.remaining = 32768
        self.truncated = False
        self.parts = []

    def write(self, value):
        text = _text(value)
        if self.remaining:
            self.parts.append(text[:self.remaining])
        self.truncated = self.truncated or len(text) > self.remaining
        self.remaining = max(0, self.remaining - len(text))
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def getvalue(self):
        return ''.join(self.parts)


def _python(filepath, entry, script_id, directory):
    # Read bytes, honoring the source encoding and avoiding Max2020's
    # ExecuteFile Unicode-compilation/encoding-declaration restriction.
    with io.open(filepath, 'rb') as source:
        code = compile(source.read(), _filename(filepath), 'exec')
    module_name = str('_qz_max_' + script_id)
    module = types.ModuleType(module_name)
    module.__file__ = filepath
    module.__package__ = str('')
    sys.modules[module_name] = module
    for path in (os.path.dirname(filepath), directory):
        if path not in sys.path:
            sys.path.append(path)
    if entry:
        eval(code, module.__dict__)
        target = module
        for part in entry.split('.'):
            target = getattr(target, part)
        if not callable(target):
            raise ValueError('指定入口不是可调用函数：' + entry)
        target()
    else:
        original_main = sys.modules.get('__main__')
        module.__dict__['__name__'] = str('__main__')
        sys.modules['__main__'] = module
        try:
            eval(code, module.__dict__)
        finally:
            if original_main is None:
                sys.modules.pop('__main__', None)
            else:
                sys.modules['__main__'] = original_main


@register_action('user_script_max')
def user_script(op='run', filepath=None, entry='', script_id='', directory=None):
    if threading.current_thread() is not _MAIN_THREAD:
        raise RuntimeError('用户脚本必须在 Max 主线程执行')
    if op == 'info':
        return dict(version=1, languages=['python', 'maxscript'],
                    python_version=list(sys.version_info[:3]))
    if op != 'run':
        raise ValueError('未知用户脚本操作')
    if not isinstance(script_id, string_types) or not _ID.match(script_id):
        raise ValueError('用户脚本编号无效')
    if not isinstance(entry, string_types):
        raise ValueError('入口名称无效')
    filepath = _path(filepath)
    directory = _path(directory) if directory is not None else os.path.dirname(filepath)
    extension = os.path.splitext(filepath)[1].lower()
    if extension not in ('.py', '.ms') or not os.path.isfile(filepath):
        raise ValueError('脚本文件不存在或类型不受支持')
    if not os.path.normcase(filepath).startswith(os.path.normcase(directory.rstrip('/\\') + os.sep)):
        raise ValueError('脚本文件不在该工具目录内')
    if entry:
        parts = entry.split('.') if extension == '.py' else [entry]
        if any(not (part.isidentifier() if extension == '.py' and sys.version_info[0] >= 3 else _IDENTIFIER.match(part))
               or keyword.iskeyword(part) for part in parts):
            raise ValueError('入口应为函数名称，不包含括号、参数或命令')
    output = _Output()
    started = getattr(time, 'perf_counter', time.time)()
    original_stdout, original_stderr = sys.stdout, sys.stderr
    success, error = True, ''
    try:
        sys.stdout = sys.stderr = output
        if extension == '.py':
            _python(filepath, entry, script_id, directory)
        else:
            from pymxs import runtime as rt
            rt.fileIn(filepath)
            if entry:
                target = getattr(rt, entry)
                if not callable(target):
                    raise ValueError('指定入口不是可调用的 MAXScript 函数：' + entry)
                target()
    except BaseException:
        # A user's SystemExit/KeyboardInterrupt must not stop the bridge.
        success = False
        error = _text(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
    elapsed = getattr(time, 'perf_counter', time.time)() - started
    return dict(success=success, output=output.getvalue(), truncated=output.truncated,
                error=error, elapsed_seconds=round(elapsed, 3))
