"""Launch a disposable Max2020 profile; never connects to a running Max."""
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
    out = ROOT / 'build' / 'max-bridge-health' / uuid.uuid4().hex[:8]
    out.mkdir(parents=True)
    maxroot = Path(r'C:\Program Files\Autodesk\3ds Max 2020')
    fixture = ROOT / 'tests' / 'max_bridge_health_integration.py'
    (out / 'run.py').write_text('import sys\nsys.path.insert(0, ' + ascii(str(fixture.parent)) + ')\n'
                               'import max_bridge_health_integration as fixture\nfixture.begin()\n', encoding='ascii')
    prefix = out.as_posix()
    script = ('global qzBridgeEnabled\nglobal qzBridgeRollout\n(\n'
              'python.ExecuteFile @"' + (out / 'run.py').as_posix() + '"\n'
              'local deadline = timeStamp() + 30000\n'
              'while not doesFileExist @"' + prefix + '/ready.json" and timeStamp() < deadline do '
              '(windows.processPostedMessages(); sleep 0.01)\n'
              'if not doesFileExist @"' + prefix + '/ready.json" do throw "Initial heartbeat timed out"\n'
              'python.Execute "import time; time.sleep(12)"\n'
              'python.Execute "import max_bridge_health_integration as health; health.released.set()"\n'
              'deadline = timeStamp() + 30000\n'
              'while not doesFileExist @"' + prefix + '/result.json" and timeStamp() < deadline do '
              '(windows.processPostedMessages(); sleep 0.01)\n'
              'if not doesFileExist @"' + prefix + '/result.json" do throw "Health fixture timed out"\n'
              'try (destroyDialog qzBridgeRollout) catch ()\nquitMAX quiet:true exitCode:0\n)\n')
    (out / 'run.ms').write_text(script, encoding='ascii')
    directories = ['[Directories]']
    for key, name in [('Additional Macros', 'macros'), ('Additional Scripts', 'scripts'),
                      ('Additional Startup Scripts', 'startup'), ('MaxData', 'appdata'),
                      ('PlugCFG', 'plugcfg'), ('PlugCFG_ln', 'plugcfg_ln'), ('Additional Icons', 'icons')]:
        (out / name).mkdir()
        directories.append(key + '=' + str(out / name))
    (out / '3dsmax.ini').write_text('\n'.join(directories) + '\n', encoding='ascii')
    (out / 'plugins.ini').write_text('[Directories]\nStandard MAX plug-ins=' + str(maxroot / 'stdplugs') + '\n', encoding='ascii')
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('PYTHON', 'QT_', 'PYSIDE', 'SHIBOKEN', 'ADSK_3DSMAX_')):
            env.pop(key)
    env.update(QZ_HEALTH_OUTPUT=str(out), QZ_HEALTH_PROJECT=str(ROOT),
               ADSK_3DSMAX_APPDATA_DIR=str(out / 'appdata'),
               ADSK_3DSMAX_USERSETTINGS_DIR=str(out / 'settings'))
    (out / 'settings').mkdir()
    command = [str(maxroot / '3dsmaxbatch.exe'), str(out / 'run.ms'), '-i', str(out / '3dsmax.ini'),
               '-p', str(out / 'plugins.ini'), '-dm', 'on', '-log', str(out / 'system.log'),
               '-listenerlog', str(out / 'listener.log')]
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    started = time.monotonic()
    with (out / 'console.log').open('wb') as file:
        process = subprocess.Popen(command, env=env, cwd=maxroot, stdout=file, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        print(json.dumps(dict(pid=process.pid, output=str(out))), flush=True)
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            process.wait(timeout=10)
    result_path = out / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
    report = dict(ok=process.returncode == 0 and bool(result and result.get('ok')),
                  returncode=process.returncode, seconds=time.monotonic() - started, result=result)
    (out / 'launch.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
