"""Locate release-owned plugins; their names and files are not editable in the UI."""
import json
from pathlib import Path

import paths
from .plugin_loader import DCC_DIRS


def plugin_folder(info):
    if info.user_script is not None or info.dcc not in DCC_DIRS:
        raise ValueError('请选择普通插件')
    root = Path(paths.plugins_dir()).resolve(strict=True)
    host = root / info.dcc
    folder = Path(info._dir)
    if (host.is_symlink() or folder.is_symlink() or host.resolve().parent != root
            or folder.resolve(strict=True).parent != host.resolve()):
        raise ValueError('插件目录不在该软件的插件根目录内')
    folder = folder.resolve(strict=True)
    data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or (data.get('dcc'), data.get('id')) != info.key:
        raise ValueError('插件登记与当前插件不一致，请刷新后重试')
    return folder

