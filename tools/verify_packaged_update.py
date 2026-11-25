"""Exercise real updater + real exe selftest in disposable installations only."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance.client import start_update
from maintenance.release import create_manifest, create_package, digest, inspect_package
from maintenance.version import read_version


def run():
    published = ROOT / 'dist' / 'QzTools'
    version = read_version(published)['version']
    package = ROOT / 'dist' / 'releases' / ('QzTools-' + version + '-windows-x64.zip')
    work = ROOT / 'build' / 'test-artifacts' / ('packaged-update-' + uuid.uuid4().hex[:8])
    install = work / 'installation'
    shutil.copytree(published, install)
    retired = install / 'plugins' / 'maya' / 'retired-test' / 'widget.py'
    retired.parent.mkdir()
    retired.write_text('retired')
    create_manifest(install)
    user_files = ['data/settings.json', 'data/layout.json', 'user_plugins/max/保留工具/files/main.ms',
                  'temp/skin_weights/保留权重.qzskin', 'backups/用户备份.txt']
    for relative in user_files:
        path = install / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'USER DATA - DO NOT REPLACE')
    report = dict(installation=str(install))
    def execute(zip_path):
        process = start_update(install, zip_path, headless=True, restart=False)
        try:
            code = process.wait(timeout=180)
        except Exception:
            process.kill()
            process.wait()
            raise
        result = json.loads((install / 'updates' / 'last-result.json').read_text(encoding='utf-8'))
        return code, result
    print('Testing successful update in ' + str(work), flush=True)
    code, success = execute(package)
    assert code == 0 and success['ok'], success
    assert not retired.exists()
    inventory = inspect_package(package)
    for row in inventory['files']:
        assert digest(install / row['path']) == row['sha256'], row['path']
    for relative in user_files:
        assert (install / relative).read_bytes() == b'USER DATA - DO NOT REPLACE', relative
    report['success'] = success
    # Also exercise the native process-wait handle used by in-app updates.
    import subprocess
    waiting = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(1)'])
    try:
        worker = start_update(install, package, wait_pid=waiting.pid, headless=True, restart=False)
        waiting.wait(timeout=5)
        wait_code = worker.wait(timeout=180)
        wait_result = json.loads((install / 'updates' / 'last-result.json').read_text(encoding='utf-8'))
        assert wait_code == 0 and wait_result['ok'], wait_result
        report['wait_pid'] = True
    finally:
        if waiting.poll() is None:
            waiting.kill()
            waiting.wait()
    print('Testing failed new-plugin selftest and rollback', flush=True)
    bad = work / 'bad-release'
    shutil.copytree(published, bad)
    broken = bad / 'plugins' / 'maya' / 'rename_tool' / 'widget.py'
    broken.write_text('def broken(:\n', encoding='utf-8')
    create_manifest(bad)
    bad_package = work / 'invalid-plugin.zip'
    create_package(bad, bad_package)
    code, failure = execute(bad_package)
    assert code != 0 and not failure['ok'], failure
    for row in inventory['files']:
        assert digest(install / row['path']) == row['sha256'], 'rollback: ' + row['path']
    for relative in user_files:
        assert (install / relative).read_bytes() == b'USER DATA - DO NOT REPLACE', relative
    report.update(failure=failure, official_hashes_restored=True, user_files_preserved=True, ok=True)
    output = work / 'result.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(ok=True, report=str(output)), ensure_ascii=False), flush=True)
    return output


if __name__ == '__main__':
    run()
