"""Managed user script copies and metadata. Importing/listing never executes scripts."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

import paths
from .blender_setup import LEGACY_BRIDGE_MESSAGE
from .plugin_loader import PluginInfo
from dcc_bridge import dcc_label

ID_PATTERN = re.compile(r"user_[0-9a-f]{32}\Z")
SCRIPT_TYPES = {
    'maya': {'python': '.py', 'mel': '.mel'},
    'max': {'python': '.py', 'maxscript': '.ms'},
    'blender': {'python': '.py'},
}
SCRIPT_ACTIONS = {'maya': 'user_script', 'max': 'user_script_max', 'blender': 'user_script_blender'}


def _make_writable(path):
    # Only called on managed copies, never the user's import source.
    path = Path(path)
    if path.is_symlink():
        return
    mode = path.stat().st_mode
    if not mode & stat.S_IWUSR:
        path.chmod(mode | stat.S_IWUSR)


def _copy_managed_file(source, destination):
    result = shutil.copy2(source, destination)
    _make_writable(result)
    return result


def _root(dcc='maya'):
    if dcc not in SCRIPT_TYPES:
        raise ValueError('不支持的用户脚本宿主')
    return Path(paths.user_plugins_dir(dcc)).resolve()


def _folder(script_id, dcc='maya'):
    if not isinstance(script_id, str) or not ID_PATTERN.fullmatch(script_id):
        raise ValueError("用户脚本编号无效")
    matches = []
    for folder in _folders(dcc):
        try:
            with (folder / 'manifest.json').open(encoding='utf-8-sig') as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('id') == script_id:
            matches.append(folder)
    if len(matches) > 1:
        raise ValueError("用户脚本编号重复，请检查脚本目录中的 manifest.json")
    if not matches:
        raise FileNotFoundError("用户脚本登记不存在")
    return matches[0]


def _folders(dcc='maya'):
    root = _root(dcc)
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.name.startswith('.') and folder.is_dir():
                yield folder


def _destination(name, dcc='maya'):
    # Use the display name, including Chinese, while keeping a valid Windows folder.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'plugin'
    if re.fullmatch(r'con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]', stem.split('.')[0], re.I):
        stem = 'tool_' + stem
    root = _root(dcc)
    destination = root / stem
    number = 2
    while os.path.lexists(destination):
        destination = root / f'{stem}_{number}'
        number += 1
    return destination


def script_files(directory, dcc='maya'):
    """List tool entry candidates without importing code or following links."""
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError('请选择脚本文件或工具文件夹')
    extensions = SCRIPT_TYPES[dcc].values()
    result = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [item for item in dirs if item not in ('.git', '.venv', 'venv', '__pycache__')]
        if any((Path(current) / item).is_symlink() for item in dirs + files):
            raise ValueError('工具文件夹中包含链接，请改为导入实际文件')
        result.extend((Path(current) / item).relative_to(directory) for item in files
                      if Path(item).suffix.lower() in extensions)
    return sorted(result, key=lambda path: path.as_posix().casefold())


def default_script(candidates):
    if len(candidates) == 1:
        return candidates[0]
    defaults = [path for path in candidates if path.as_posix().lower() in ('main.py', 'main.mel', 'main.ms')]
    return defaults[0] if len(defaults) == 1 else None


def suggest_entry(code):
    """Suggest a synchronous no-argument function; never execute pasted code."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return ''
    # A file with executable top-level statements should run as a whole.
    if any(not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef))
           and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)) for node in tree.body):
        return ''
    candidates = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                  and not node.name.startswith('_') and not node.decorator_list
                  and len(node.args.posonlyargs + node.args.args) <= len(node.args.defaults)
                  and all(value is not None for value in node.args.kw_defaults)]
    if 'main' in candidates:
        return 'main'
    return candidates[0] if len(candidates) == 1 else ''


def validate_name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("插件名称请填写 1～80 个字符")
    if any(ord(char) < 32 for char in name):
        raise ValueError("插件名称不能包含换行或控制字符")
    return name.strip()


def validate_entry(entry, language):
    if not isinstance(entry, str):
        raise ValueError("入口名称无效")
    entry = entry.strip()
    if not entry:
        return ""
    if language == "python":
        import keyword
        if not all(part.isidentifier() and not keyword.iskeyword(part) for part in entry.split('.')):
            raise ValueError("Python 入口请填函数名，例如 main，不包含括号或参数")
    elif not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", entry):
        raise ValueError("%s 入口请填函数或过程名，不包含括号或参数" % ('MAXScript' if language == 'maxscript' else 'MEL'))
    return entry


def _write(folder, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                         prefix='.manifest-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, folder / 'manifest.json')
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_script(script_id, require_file=True, *, dcc='maya'):
    for data in _flat_scripts(dcc):
        if data['id'] == script_id:
            return data
    folder = _folder(script_id, dcc)
    return _read_folder(folder, require_file, dcc=dcc)


def _flat_scripts(dcc):
    root = _root(dcc)
    if not root.is_dir():
        return
    languages = {suffix: language for language, suffix in SCRIPT_TYPES[dcc].items()}
    for path in sorted(root.iterdir()):
        language = languages.get(path.suffix.lower())
        if (not language or path.name.startswith('.') or path.is_symlink()
                or not path.is_file() or path.resolve().parent != root):
            continue
        script_id = 'user_' + uuid.uuid5(uuid.NAMESPACE_URL, path.name).hex
        yield dict(id=script_id, name=path.stem, dcc=dcc, language=language,
                   filepath=str(path), directory=str(root), folder=str(root),
                   entry='', flat=True)


def _check_flat_name(root, name, dcc, except_path=None):
    if any(p.stem.casefold() == name.casefold() and p != except_path
           and p.suffix.lower() in SCRIPT_TYPES[dcc].values() for p in root.iterdir()):
        raise FileExistsError(f'已有同名脚本：{name}')
    for folder in _folders(dcc):
        try:
            legacy = _read_folder(folder, require_file=False, dcc=dcc)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not legacy.get('removed') and legacy['name'].casefold() == name.casefold():
            raise FileExistsError(f'已有同名脚本：{name}')


def add_flat_script(filepath, *, dcc):
    source = Path(filepath)
    if (not source.is_file() or source.name.startswith('.')
            or source.suffix.lower() not in SCRIPT_TYPES[dcc].values()):
        raise ValueError('请选择当前宿主支持的脚本文件')
    root = _root(dcc)
    root.mkdir(parents=True, exist_ok=True)
    _check_flat_name(root, source.stem, dcc)
    destination = root / source.name
    created = False
    try:
        with source.open('rb') as original, destination.open('xb') as copy:
            created = True
            shutil.copyfileobj(original, copy)
    except OSError:
        if created:
            destination.unlink()
        raise


def _read_folder(folder, require_file=True, *, dcc='maya'):
    if folder.is_symlink() or folder.resolve().parent != _root(dcc):
        raise ValueError("用户脚本目录无效")
    with (folder / 'manifest.json').open(encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('id'), str) or not ID_PATTERN.fullmatch(data['id'])
            or data.get('dcc', 'maya') != dcc
            or data.get('language') not in SCRIPT_TYPES[dcc]):
        raise ValueError("用户脚本配置无效")
    data['name'] = validate_name(data['name'])
    data['entry'] = validate_entry(data.get('entry', ''), data['language'])
    relative = Path(data['script'])
    payload = folder / 'files'
    if payload.is_symlink() or payload.resolve().parent != folder:
        raise ValueError('工具文件夹不在该用户插件目录内')
    target = (payload / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(payload.resolve()):
        raise ValueError("脚本文件不存在或不在该工具目录内")
    if require_file and not target.is_file():
        raise ValueError('脚本文件已丢失，请打开脚本目录检查')
    expected = SCRIPT_TYPES[dcc][data['language']]
    if target.suffix.lower() != expected:
        raise ValueError("脚本类型与文件扩展名不匹配")
    data.update(dcc=dcc, filepath=str(target), directory=str(payload), folder=str(folder), missing=not target.is_file())
    return data


def scan_scripts(dcc='maya'):
    plugins, errors = [], []
    seen, duplicate_ids = set(), set()
    for folder in _folders(dcc):
        try:
            data = _read_folder(folder, require_file=False, dcc=dcc)
            if data['id'] in seen:
                duplicate_ids.add(data['id'])
                raise ValueError("用户脚本编号重复，请检查脚本目录中的 manifest.json")
            seen.add(data['id'])
            if data.get('removed', False):
                continue
            plugins.append(PluginInfo(id=data['id'], name=data['name'], dcc=dcc, icon='',
                                      widget_factory_path='', category='user', _dir=str(folder),
                                      user_script=data))
        except Exception as exc:
            errors.append(f"用户脚本 {folder.name}：{exc}")
    for data in _flat_scripts(dcc):
        plugins.append(PluginInfo(id=data['id'], name=data['name'], dcc=dcc, icon='',
                                  widget_factory_path='', category='user', _dir=data['directory'],
                                  user_script=data))
    return [info for info in plugins if info.id not in duplicate_ids], errors


def scan_all_scripts():
    plugins, errors = [], []
    for dcc in SCRIPT_TYPES:
        found, problems = scan_scripts(dcc)
        plugins.extend(found)
        errors.extend('%s：%s' % (dcc_label(dcc), problem) for problem in problems)
    return plugins, errors


def add_script(name, filepath, entry='', tool_directory=None, *, entry_file=None, dcc='maya'):
    name = validate_name(name)
    root = _root(dcc)
    languages = {suffix: language for language, suffix in SCRIPT_TYPES[dcc].items()}
    extensions = ' 或 '.join(languages)
    source = Path(filepath).expanduser().resolve()
    if source.is_dir():
        tool_directory = str(source)
        candidates = script_files(source, dcc)
        relative = Path(entry_file) if entry_file else default_script(candidates)
        if relative is None:
            raise ValueError('请选择工具文件夹中的入口脚本' if candidates else '工具文件夹中没有 %s 脚本' % extensions)
        if relative not in candidates:
            raise ValueError('入口脚本必须位于所选工具文件夹内')
        source = (source / relative).resolve()
    language = languages.get(source.suffix.lower())
    if not source.is_file() or language is None:
        allowed = ' 或 '.join('Python（.py）' if suffix == '.py' else suffix for suffix in languages)
        raise ValueError("请选择 %s 脚本" % allowed)
    entry = validate_entry(entry, language)
    if language == 'python' and dcc == 'maya':
        # Syntax validation only. Script code must never run while importing.
        compile(source.read_bytes(), str(source), 'exec')
    # Max and Blender compile Python in the host's interpreter at run time.
    source_root = None
    if tool_directory:
        source_root = Path(tool_directory).expanduser().resolve()
        if not source_root.is_dir() or not source.is_relative_to(source_root):
            raise ValueError("脚本文件必须位于所选工具文件夹内")
        if root.is_relative_to(source_root):
            raise ValueError("请选择工具自身的文件夹，不能包含用户插件保存目录")
        for directory, dirs, files in os.walk(source_root, followlinks=False):
            dirs[:] = [item for item in dirs if item not in ('.git', '.venv', 'venv', '__pycache__')]
            if any((Path(directory) / item).is_symlink() for item in dirs + files):
                raise ValueError("工具文件夹中包含链接，请改为导入实际文件")
    root.mkdir(parents=True, exist_ok=True)
    script_id = 'user_' + uuid.uuid4().hex
    destination = _destination(name, dcc)
    with tempfile.TemporaryDirectory(prefix='.import-', dir=root) as staging:
        staging = Path(staging)
        payload = staging / 'files'
        if source_root is not None:
            shutil.copytree(source_root, payload, copy_function=_copy_managed_file, ignore=shutil.ignore_patterns(
                '.git', '.venv', 'venv', '__pycache__', '*.pyc', '*.pyo'))
            relative = source.relative_to(source_root)
        else:
            payload.mkdir()
            _copy_managed_file(source, payload / source.name)
            relative = Path(source.name)
        _write(staging, dict(version=1, dcc=dcc, id=script_id, name=name, language=language,
                             script=relative.as_posix(), entry=entry, removed=False))
        # Publish only complete imports. TemporaryDirectory then removes its empty staging directory.
        os.rename(staging, destination)
    return read_script(script_id, dcc=dcc)


def add_code(name, code, entry='', language='python', *, dcc='maya'):
    """Save pasted code using the same validated, atomic import path as files."""
    validate_name(name)
    extension = SCRIPT_TYPES.get(dcc, {}).get(language)
    if extension is None:
        raise ValueError('脚本语言无效')
    if not isinstance(code, str) or not code.strip():
        raise ValueError('请粘贴脚本代码')
    with tempfile.TemporaryDirectory(prefix='qz-code-') as temporary:
        source = Path(temporary) / ('main' + extension)
        if dcc == 'max' and language == 'python':
            # Pasted text is Unicode, so its saved bytes always use UTF8.
            lines = code.splitlines(True)
            cookie = re.compile(r'(^[ \t\f]*#.*?coding[:=][ \t]*)([-\w.]+)')
            found = False
            for index in range(min(2, len(lines))):
                if cookie.search(lines[index]):
                    lines[index] = cookie.sub(r'\g<1>utf-8', lines[index], count=1)
                    found = True
                    break
            if not found:
                lines.insert(1 if lines and lines[0].startswith('#!') else 0, '# -*- coding: utf-8 -*-\n')
            code = ''.join(lines)
        source.write_text(code, encoding='utf-8-sig' if language == 'maxscript' else 'utf-8', newline='\n')
        return add_script(name, str(source), entry, dcc=dcc)


def update_script(script_id, *, name=None, entry=None, removed=None, dcc='maya'):
    data = read_script(script_id, require_file=False, dcc=dcc)
    if data.get('flat'):
        if entry is not None or removed is not None or name is None:
            raise ValueError('独立脚本仅支持修改文件名')
        name = validate_name(name)
        if name.startswith('.') or name.endswith('.') or re.search(r'[<>:"/\\|?*]', name):
            raise ValueError('脚本文件名无效')
        source = Path(data['filepath'])
        destination = source.with_name(name + source.suffix)
        _check_flat_name(source.parent, name, dcc, except_path=source)
        if source.name != destination.name:
            source.rename(destination)
        return next(item for item in _flat_scripts(dcc) if Path(item['filepath']) == destination)
    if name is not None:
        data['name'] = validate_name(name)
    if entry is not None:
        data['entry'] = validate_entry(entry, data['language'])
    if removed is not None:
        data['removed'] = bool(removed)
    for key in ('filepath', 'directory', 'folder', 'missing'):
        data.pop(key, None)
    _write(_folder(script_id, dcc), data)
    return read_script(script_id, require_file=False, dcc=dcc)


def remove_script(script_id, *, dcc='maya'):
    """Permanently remove only this tool's managed directory, never its source."""
    data = read_script(script_id, require_file=False, dcc=dcc)
    if data.get('flat'):
        target = Path(data['filepath'])
        _make_writable(target)
        target.unlink()
        return
    folder = Path(data['folder'])
    root = _root(dcc)
    # Verify the final absolute deletion target before any recursive removal.
    if folder.is_symlink() or folder.resolve(strict=True).parent != root:
        raise ValueError('只能移除用户插件目录内的工具')
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs:
            child = Path(current) / name
            if child.is_symlink() or not child.resolve().is_relative_to(folder):
                raise ValueError('工具目录中包含目录链接，请先移除链接后再试')
        # Old imports may have inherited read-only flags from their source.
        # Clear those flags only inside the already validated managed folder.
        # Real sharing/ACL failures still propagate and retain registration.
        for name in files:
            _make_writable(Path(current) / name)
        _make_writable(current)
    # Keep the registration until payload deletion succeeds, so a locked file
    # leaves the tool visible for retry instead of silently hiding its remains.
    for child in folder.iterdir():
        if child.name == 'manifest.json':
            continue
        if child.is_dir():
            if child.resolve().parent != folder:
                raise ValueError('工具目录无效')
            shutil.rmtree(child)
        else:
            child.unlink()
    (folder / 'manifest.json').unlink()
    folder.rmdir()


def run_script(client, script):
    """Only the run request executes user code. A failed run is never retried."""
    executing = False
    dcc = script.get('dcc', 'maya')
    label = dcc_label(dcc)
    try:
        if dcc not in SCRIPT_ACTIONS or getattr(client, 'dcc', dcc) != dcc:
            raise ValueError('脚本与连接的宿主不一致')
        action = SCRIPT_ACTIONS[dcc]
        if client is None or not getattr(client, 'connected', False):
            raise RuntimeError("%s 未连接，请先在中台连接 %s" % (label, label))
        ready = client.call(action, op='info', timeout=15, connect_timeout=1)
        if not ready.get('ok') and 'unknown action' not in ready.get('error', ''):
            return ready
        def supported(response):
            data = response.get('data') or {}
            return (response.get('ok') and data.get('version') == 1
                    and (dcc != 'blender' or not script.get('flat') or data.get('flat_files')))

        if not supported(ready):
            reloaded = client.call('reload_actions', timeout=30, connect_timeout=1)
            if not reloaded.get('ok'):
                if dcc == 'blender' and 'unknown action' in reloaded.get('error', ''):
                    raise RuntimeError(LEGACY_BRIDGE_MESSAGE)
                return reloaded
            ready = client.call(action, op='info', timeout=15, connect_timeout=1)
        if not supported(ready):
            raise RuntimeError("%s 用户脚本执行器未加载，请重载该软件动作库" % label)
        current = read_script(script['id'], dcc=dcc)
        if current.get('removed'):
            raise ValueError("该用户脚本已移除")
        executing = True
        wait_options = dict(execution_timeout=600) if dcc == 'max' else {}
        result = client.call(action, op='run', filepath=current['filepath'], directory=current['directory'],
                             entry=current['entry'], script_id=current['id'], timeout=600 if dcc == 'maya' else 605,
                             connect_timeout=1, **wait_options)
        if result.get('ok') and not result.get('data', {}).get('success', False):
            return dict(ok=False, error=result['data'].get('error', '脚本执行失败'), data=result['data'])
        return result
    except Exception as exc:
        error = str(exc)
        if executing:
            error += '；脚本可能已开始执行，请检查 %s 中的结果后再运行' % label
        return dict(ok=False, error=error)


def _pin_ready(response, version, script):
    data = response.get('data') or {}
    return (response.get('ok') and data.get('version') == version
            and (not script.get('flat') or data.get('flat_files')))


def pin_script_to_maya_shelf(client, script):
    """Only install a persistent launcher; never execute the user's script."""
    try:
        if script.get('dcc', 'maya') != 'maya' or getattr(client, 'dcc', 'maya') != 'maya':
            raise ValueError('当前固定功能仅支持 Maya 用户脚本')
        if client is None or not getattr(client, 'connected', False):
            raise RuntimeError('Maya 未连接，请先连接后再固定到工具架')
        current = read_script(script['id'], dcc='maya')
        ready = client.call('user_script_shelf', op='info', timeout=15, connect_timeout=1)
        if not ready.get('ok') and 'unknown action' not in ready.get('error', ''):
            return ready
        if not _pin_ready(ready, 1, current):
            reloaded = client.call('reload_actions', timeout=30, connect_timeout=1)
            if not reloaded.get('ok'):
                return reloaded
            ready = client.call('user_script_shelf', op='info', timeout=15, connect_timeout=1)
        if not _pin_ready(ready, 1, current):
            raise RuntimeError('Maya 工具架执行器未加载，请重载 Maya 动作库')
        return client.call('user_script_shelf', op='pin', script_id=current['id'],
                           root=str(_root('maya')), timeout=30, connect_timeout=1)
    except Exception as error:
        return dict(ok=False, error=str(error))


def pin_script_to_max_toolbar(client, script):
    """Install only. Preflight may reload; never retry a possibly completed pin."""
    try:
        if script.get('dcc') != 'max' or getattr(client, 'dcc', 'max') != 'max':
            raise ValueError('当前固定功能仅支持 Max 用户脚本')
        if client is None or not getattr(client, 'connected', False):
            raise RuntimeError('Max 未连接，请先连接后再固定到工具栏')
        current = read_script(script['id'], dcc='max')
        ready = client.call('user_script_toolbar', op='info', timeout=15, connect_timeout=1)
        if not ready.get('ok') and 'unknown action' not in ready.get('error', ''):
            return ready
        if not _pin_ready(ready, 2, current):
            reloaded = client.call('reload_actions', timeout=30, connect_timeout=1)
            if not reloaded.get('ok'):
                return reloaded
            ready = client.call('user_script_toolbar', op='info', timeout=15, connect_timeout=1)
        if not _pin_ready(ready, 2, current):
            raise RuntimeError('Max 工具栏执行器未加载，请重载 Max 动作库')
        return client.call('user_script_toolbar', op='pin', script_id=current['id'],
                           root=str(_root('max')), timeout=35, connect_timeout=1, execution_timeout=30)
    except Exception as error:
        return dict(ok=False, error=str(error))
