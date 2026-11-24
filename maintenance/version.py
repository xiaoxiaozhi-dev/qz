"""One version source for application UI, release packages and updater."""
import json
from pathlib import Path
import re

import paths


def read_version(root=None):
    folder = Path(root or paths.project_dir())
    data = json.loads((folder / 'version.json').read_text(encoding='utf-8-sig'))
    if (data.get('app_id') != 'QzTools' or data.get('update_format') != 1
            or not re.fullmatch(r'\d+\.\d+\.\d+', data.get('version', ''))):
        raise ValueError('version.json 的产品或版本格式无效')
    return data


def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('版本号应为 major.minor.patch')
    return tuple(map(int, value.split('.')))
