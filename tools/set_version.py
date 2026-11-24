"""Show or set the sole product version; this does not commit or tag Git."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance.version import read_version, version_tuple


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', nargs='?')
    args = parser.parse_args()
    data = read_version(ROOT)
    if args.version is not None:
        version_tuple(args.version)
        data['version'] = args.version
        (ROOT / 'version.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(data['version'])


if __name__ == '__main__':
    main()
