"""Remove regenerable source caches; never walk environments or user assets."""
import argparse
import json
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', 'venv', 'env', 'build', 'dist', 'user_plugins', 'temp',
        'backups', 'data', 'updates', 'memory', '.workbuddy', '.codex', '.agents'}
CACHES = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def candidates(root=ROOT):
    for child in root.iterdir():
        if linked(child):
            continue
        if child.is_dir():
            if child.name in CACHES:
                yield child
            elif child.name not in SKIP and not (child / 'pyvenv.cfg').is_file():
                yield from candidates(child)
        elif child.suffix in ('.pyc', '.pyo') or child.name == 'test_screenshot.png':
            yield child


def clean(apply=False):
    records = []
    for target in candidates():
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(ROOT) or resolved == ROOT:
            raise ValueError('清理目标不在开发目录内：' + str(resolved))
        size = sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and not p.is_symlink()) if target.is_dir() else target.stat().st_size
        records.append(dict(path=target.relative_to(ROOT).as_posix(), bytes=size))
        if apply:
            if target.is_dir():
                # Refuse any nested reparse point before recursive removal.
                if any(linked(p) for p in target.rglob('*')):
                    raise ValueError('缓存目录包含链接：' + str(target))
                shutil.rmtree(target)
            else:
                target.unlink()
    report = dict(applied=apply, files=records, bytes=sum(row['bytes'] for row in records))
    if apply:
        destination = ROOT / 'build' / 'reports' / 'cleanup.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report if not apply else dict(applied=apply, targets=len(records), bytes=report['bytes']), ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='实际清理；默认仅预览')
    clean(parser.parse_args().apply)
