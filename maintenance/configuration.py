"""Official defaults plus persistent, recursively merged user overrides."""
import json
from pathlib import Path

import paths


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge(base, extra):
    result = dict(base)
    for key, value in extra.items():
        result[key] = _merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else value
    return result


def load():
    return _merge(_read(paths.defaults_path()), _read(paths.config_path()))
