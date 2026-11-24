"""Prepare a disposable updater executable so the installed helper can update too."""
from pathlib import Path
import os
import shutil
import tempfile

from .process import launch


def start_update(installation, package, wait_pid=None, headless=False, restart=True):
    root = Path(installation).resolve(strict=True)
    helper = root / 'QzUpdater.exe'
    if not helper.is_file():
        raise RuntimeError('更新助手不存在，请使用完整的发布目录')
    worker = Path(tempfile.mkdtemp(prefix='qz-updater-')) / 'QzUpdater.exe'
    shutil.copy2(helper, worker)
    args = [str(worker), '--worker', '--target', str(root), '--package', str(Path(package).resolve(strict=True))]
    if wait_pid:
        args.extend(['--wait-pid', str(wait_pid)])
    if headless:
        args.append('--headless')
    if not restart:
        args.append('--no-restart')
    try:
        return launch(args, cwd=str(root))
    except Exception:
        worker.unlink(missing_ok=True)
        worker.parent.rmdir()
        raise
