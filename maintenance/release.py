"""An explicit official-file inventory shared by build checks and updates."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import os
import zipfile

from .version import read_version, version_tuple

MANIFEST = 'release-manifest.json'
FILES = {'QzTools.exe', 'QzUpdater.exe', 'version.json', 'defaults.json'}
DIRECTORIES = {
    '_internal', 'plugins', 'maya_bridge', 'maya_actions', 'max_bridge', 'max_actions',
    'blender_bridge', 'blender_actions',
}
REQUIRED = {'QzTools.exe', 'QzUpdater.exe', 'version.json', 'defaults.json'}


def is_link(path):
    try:
        flags = getattr(Path(path).lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        return False
    return Path(path).is_symlink() or bool(flags & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def safe_relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('更新包文件路径无效')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in ('', '.', '..') for part in path.parts):
        raise ValueError('更新包不能包含绝对路径或上级目录')
    for part in path.parts:
        if (part.endswith((' ', '.')) or re.search(r'[<>"|?*]', part)
                or re.fullmatch(r'con|prn|aux|nul|com[1-9]|lpt[1-9]', part.split('.')[0], re.I)):
            raise ValueError('更新包包含无效的 Windows 文件名')
    allowed = (value in FILES or (len(path.parts) > 1 and path.parts[0] in DIRECTORIES)
               or (len(path.parts) > 2 and path.parts[:2] == ('ui', 'resources')))
    if not allowed or any(part in ('__pycache__', '.git', '.venv') for part in path.parts):
        raise ValueError('更新清单包含非官方文件：' + value)
    return path


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def official_files(root):
    root = Path(root)
    result = []
    for path in sorted(root.rglob('*')):
        if is_link(path):
            raise ValueError('发布目录中不能包含链接：' + str(path))
        if not path.is_file() or path.name == MANIFEST:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            safe_relative(relative)
        except ValueError:
            raise ValueError('发布目录混入运行时数据或未知文件：' + relative)
        if path.suffix.lower() in ('.pyc', '.pyo') and not relative.startswith('_internal/'):
            raise ValueError('外置脚本不应带缓存：' + relative)
        result.append(dict(path=relative, size=path.stat().st_size, sha256=digest(path)))
    return result


def validate_manifest(data):
    if not isinstance(data, dict) or data.get('app_id') != 'QzTools' or data.get('format') != 1:
        raise ValueError('不是受支持的 Qz 更新包')
    version_tuple(data.get('version', ''))
    rows = data.get('files')
    if not isinstance(rows, list) or len(rows) > 50000:
        raise ValueError('更新清单无效')
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('更新清单文件条目无效')
        relative = row.get('path')
        safe_relative(relative)
        if (relative.casefold() in found or type(row.get('size')) is not int or row['size'] < 0
                or not re.fullmatch(r'[0-9a-f]{64}', row.get('sha256', ''))):
            raise ValueError('更新清单重复或校验信息无效')
        found[relative.casefold()] = row
    if not REQUIRED.issubset({row['path'] for row in rows}):
        raise ValueError('更新包缺少程序、更新助手、版本或默认配置')
    # A file may not also be the parent directory of another entry.
    for row in rows:
        if any(str(parent).casefold() in found for parent in PurePosixPath(row['path']).parents if str(parent) != '.'):
            raise ValueError('更新清单文件和目录冲突')
    return data


def create_manifest(root):
    root = Path(root)
    info = read_version(root)
    data = validate_manifest(dict(app_id=info['app_id'], format=1, version=info['version'], files=official_files(root)))
    (root / MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def verify_directory(root):
    root = Path(root)
    data = validate_manifest(json.loads((root / MANIFEST).read_text(encoding='utf-8')))
    if official_files(root) != data['files']:
        raise ValueError('发布目录与清单不一致，不能发布')
    if read_version(root)['version'] != data['version']:
        raise ValueError('程序版本与发布清单不一致')
    return data


def inspect_package(package):
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        manifests = [info for info in infos if info.filename == MANIFEST]
        if len(manifests) != 1 or manifests[0].file_size > 16 * 1024 * 1024:
            raise ValueError('更新包缺少唯一有效的发布清单')
        data = validate_manifest(json.loads(archive.read(MANIFEST).decode('utf-8')))
        expected = {row['path']: row for row in data['files']}
        seen = set()
        for info in infos:
            name = info.filename
            if name == MANIFEST:
                continue
            safe_relative(name)
            if (name not in expected or name.casefold() in seen or info.is_dir()
                    or info.file_size != expected[name]['size']
                    or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1):
                raise ValueError('更新包内容与清单不一致：' + name)
            seen.add(name.casefold())
        if len(seen) != len(expected):
            raise ValueError('更新包文件不完整')
        return data


def create_package(root, destination):
    root, destination = Path(root), Path(destination)
    data = verify_directory(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_suffix(destination.suffix + '.part')
    try:
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(root / MANIFEST, MANIFEST)
            for row in data['files']:
                archive.write(root / row['path'], row['path'])
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
    return dict(version=data['version'], filename=destination.name,
                size=destination.stat().st_size, sha256=digest(destination))
