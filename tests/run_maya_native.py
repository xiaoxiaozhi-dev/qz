"""Run a fixture's run() in a disposable, isolated Maya executable process.

Usage: .venv/Scripts/python.exe tests/run_maya_native.py tests/maya_user_scripts_integration.py
No connection to an existing Maya process is made. Installation files are unchanged.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path, help='Python fixture exposing run() -> {ok: bool, ...}')
    parser.add_argument('--maya', type=Path, default=Path(r'C:\Program Files\Autodesk\Maya2024\bin\maya.exe'))
    parser.add_argument('--timeout', type=int, default=60, choices=range(1, 61), metavar='1..60')
    parser.add_argument('--gui', action='store_true', help='Use an isolated GUI process for native UI commands')
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    executable = args.maya.resolve()
    if not fixture.is_file() or not executable.is_file():
        parser.error('Fixture or Maya executable does not exist')
    output = ROOT / 'build' / 'maya-native' / (fixture.stem + '-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    profile = output / 'profile'
    profile.mkdir()
    result_path = output / 'result.json'
    token = uuid.uuid4().hex
    worker = output / 'worker.py'
    worker.write_text('''import json, os, runpy, sys, traceback
from pathlib import Path
from maya import cmds
report = dict(ok=False, pid=os.getpid(), executable=sys.executable,
              token=os.environ['QZ_NATIVE_TOKEN'], batch=cmds.about(batch=True),
              maya=cmds.about(version=True))
try:
    if report['batch'] == (os.environ.get('QZ_NATIVE_GUI') == '1'):
        raise RuntimeError('Native test process mode does not match this isolated runner')
    sys.path.insert(0, os.environ['QZ_NATIVE_PROJECT'])
    fixture = runpy.run_path(os.environ['QZ_NATIVE_FIXTURE'], run_name='qz_native_fixture')
    result = fixture['run']()
    report.update(ok=isinstance(result, dict) and result.get('ok') is True, result=result)
except BaseException:
    report['error'] = traceback.format_exc()
path = Path(os.environ['QZ_NATIVE_RESULT'])
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
temporary.replace(path)
if os.environ.get('QZ_NATIVE_GUI') == '1':
    # Return from the Python idle callback before Maya tears down its UI.
    cmds.evalDeferred('quit -force -exitCode %d;' % (0 if report['ok'] else 1), lowestPriority=True)
else:
    cmds.quit(force=True, exitCode=0 if report['ok'] else 1)
''', encoding='utf-8')
    expression = 'exec(compile(open(' + repr(worker.as_posix()) + ', "rb").read(), ' + repr(worker.as_posix()) + ', "exec"))'
    if args.gui:
        expression = 'import maya.utils; maya.utils.executeDeferred(' + repr(expression) + ')'
    mel = output / 'worker.mel'
    mel.write_text('python(' + json.dumps(expression) + ');\n', encoding='utf-8')
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('MAYA', 'PYTHON', 'QT_', 'PYSIDE', 'SHIBOKEN')):
            env.pop(key)
    env.update(MAYA_LOCATION=str(executable.parent.parent), MAYA_APP_DIR=str(profile),
               MAYA_SKIP_USERSETUP_PY='1', QZ_NATIVE_TOKEN=token, QZ_NATIVE_PROJECT=str(ROOT),
               QZ_NATIVE_FIXTURE=str(fixture), QZ_NATIVE_RESULT=str(result_path),
               QZ_NATIVE_GUI='1' if args.gui else '0')
    env['PATH'] = os.pathsep.join([str(executable.parent), os.path.join(os.environ['SystemRoot'], 'System32'),
                                 os.environ['SystemRoot']])
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    command = [str(executable)] + ([] if args.gui else ['-batch']) + ['-noAutoloadPlugins', '-script', str(mel)]
    start = time.monotonic()
    timed_out = False
    with (output / 'process.log').open('wb') as log:
        process = subprocess.Popen(command, env=env, cwd=str(executable.parent), stdout=log,
                                   stderr=subprocess.STDOUT, startupinfo=startup,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        print(f'Native Maya test started: pid={process.pid}, output={output}', flush=True)
        try:
            process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only this freshly spawned test process and its descendants.
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.wait(timeout=5)
    report = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
    ok = (not timed_out and process.returncode == 0 and isinstance(report, dict)
          and report.get('ok') is True and report.get('batch') is (not args.gui)
          and report.get('pid') == process.pid and report.get('token') == token)
    launch = dict(ok=ok, returncode=process.returncode, timed_out=timed_out,
                  elapsed_seconds=time.monotonic()-start, fixture=str(fixture),
                  result=str(result_path), report=report)
    (output / 'launch.json').write_text(json.dumps(launch, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(launch, ensure_ascii=True, indent=2), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
