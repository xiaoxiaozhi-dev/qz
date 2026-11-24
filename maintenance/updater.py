"""Offline transactional replacement. Never imports Qt or a DCC runtime."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import zipfile

from .release import MANIFEST, digest, inspect_package, safe_relative, validate_manifest, is_link
from .version import read_version, version_tuple


def _target(root, relative):
    if relative != MANIFEST:
        safe_relative(relative)
    path = root / relative
    for ancestor in (root, *path.relative_to(root).parents):
        candidate = ancestor if ancestor == root else root / ancestor
        if is_link(candidate):
            raise ValueError('安装目录包含链接：' + str(candidate))
    if is_link(path):
        raise ValueError('更新目标不能是链接：' + str(path))
    if not path.resolve().is_relative_to(root):
        raise ValueError('更新目标越过安装目录')
    return path


def _replace(source, destination):
    # Briefly tolerate the outgoing bootloader releasing its executable handle.
    for attempt in range(20):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


@contextmanager
def _lock(root):
    path = root / 'updates'
    _target(root, 'QzTools.exe')
    if is_link(path):
        raise ValueError('updates 目录不能是链接')
    path.mkdir(exist_ok=True)
    lockfile = path / 'update.lock'
    if is_link(lockfile):
        raise ValueError('更新锁文件不能是链接')
    with lockfile.open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('已有更新正在执行')
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def apply_update(package, installation, progress=None, verify_installed=None):
    root = Path(installation).absolute()
    if is_link(root):
        raise ValueError('安装目录不能是链接')
    root = root.resolve(strict=True)
    if (root / '.git').exists() or (root / 'main.py').exists():
        raise ValueError('不能向开发源码目录应用发布更新')
    old = validate_manifest(json.loads(_target(root, MANIFEST).read_text(encoding='utf-8')))
    current = read_version(root)
    new = inspect_package(package)
    if old['version'] != current['version']:
        raise ValueError('当前安装版本与清单不一致')
    if version_tuple(new['version']) < version_tuple(current['version']):
        raise ValueError('不能使用旧版本覆盖当前版本')
    progress = progress or (lambda message: None)
    with _lock(root):
        work = root / 'updates' / ('install-' + uuid.uuid4().hex)
        stage, backup = work / 'stage', work / 'backup'
        stage.mkdir(parents=True)
        backup.mkdir()
        journal = dict(status='preparing', previous=old['version'], version=new['version'], changed=[])
        applied = []
        def record():
            (work / 'result.json').write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
        record()
        try:
            progress('正在检查并准备更新文件…')
            with zipfile.ZipFile(package) as archive:
                for row in new['files']:
                    destination = stage / row['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(row['path']) as source, destination.open('wb') as stream:
                        shutil.copyfileobj(source, stream)
                    if digest(destination) != row['sha256']:
                        raise ValueError('文件校验失败：' + row['path'])
                (stage / MANIFEST).write_bytes(archive.read(MANIFEST))
            if read_version(stage)['version'] != new['version']:
                raise ValueError('更新包版本与清单不一致')
            new_files = {row['path'] for row in new['files']}
            old_files = {row['path'] for row in old['files']}
            new_names = {name.casefold() for name in new_files}
            affected = sorted(new_files | {name for name in old_files if name.casefold() not in new_names}) + [MANIFEST]
            # Check every target before starting any changes; back up all originals.
            for relative in affected:
                target = _target(root, relative)
                if target.exists() and not target.is_file():
                    raise ValueError('更新文件被同名目录占用：' + relative)
                original = backup / relative
                if target.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, original)
            journal['status'] = 'installing'
            record()
            progress('正在替换官方程序和插件…')
            for relative in affected:
                target = _target(root, relative)
                incoming = stage / relative
                # Persist intent before the mutation so recovery has its full scope.
                journal['changed'].append(relative)
                record()
                if incoming.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _replace(incoming, target)
                elif target.exists():
                    target.unlink()  # Only obsolete paths from the previous official inventory.
                applied.append(relative)
            if verify_installed is not None:
                progress('正在验证更新后的程序…')
                verify_installed(root)
            journal['status'] = 'complete'
            record()
            shutil.rmtree(stage)
            progress('更新完成')
            return dict(ok=True, version=new['version'], backup=str(backup), report=str(work / 'result.json'))
        except Exception as error:
            recovery_errors = []
            for relative in reversed(applied):
                target = _target(root, relative)
                original = backup / relative
                try:
                    if original.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        recovery = target.with_name('.qz-restore-' + uuid.uuid4().hex)
                        shutil.copy2(original, recovery)
                        _replace(recovery, target)
                    elif target.exists():
                        target.unlink()
                except Exception as recovery_error:
                    recovery_errors.append('%s: %s' % (relative, recovery_error))
            journal.update(status='recovery_failed' if recovery_errors else 'rolled_back',
                           error=str(error), recovery_errors=recovery_errors)
            record()
            suffix = ('；部分文件未能恢复，请保留备份：' + str(backup)) if recovery_errors else '；原版本已保留或恢复'
            raise RuntimeError(str(error) + suffix) from error
