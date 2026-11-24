# -*- coding: utf-8 -*-
"""Disposable Max batch: Python, MAXScript, Unicode and retained UI callbacks."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

OUTPUT = os.environ['QZ_RIG_NATIVE_OUTPUT']
sys.path.insert(0, os.environ['QZ_RIG_PROJECT'])


def run():
    import pymxs
    from pymxs import runtime as rt
    from max_actions.user_scripts import user_script
    assert rt.maxOps.isInNonInteractiveMode(), 'Disposable batch only'
    temporary = tempfile.mkdtemp(prefix='qz_user_native_')
    folder = os.path.join(temporary, "中文 工具's")
    os.mkdir(folder)
    script_id = 'user_' + 'b' * 32
    def execute(path, entry=''):
        result = user_script(filepath=path, script_id=script_id, directory=folder, entry=entry)
        assert result['success'], result
        return result
    def save(filename, text, encoding='utf-8'):
        path = os.path.join(folder, filename)
        with io.open(path, 'w', encoding=encoding) as stream:
            stream.write(text)
        return path
    try:
        version = user_script(op='info')
        assert version['python_version'][0] == sys.version_info[0]
        syntax = 'print "native Python2"\n' if sys.version_info[0] == 2 else 'print(f"native Python{3}")\n'
        py = save('工具.py', '# -*- coding: utf-8 -*-\nfrom pymxs import runtime as rt\n' + syntax +
                  'if __name__ == "__main__":\n    rt.box(name="QzUserPythonBox")\n    print(u"中文完成")\n')
        before_main = sys.modules.get('__main__')
        python = execute(py)
        assert '中文完成' in python['output']
        assert sys.modules.get('__main__') is before_main
        assert rt.getNodeByName('QzUserPythonBox') is not None
        py = save('入口.py', '# -*- coding: utf-8 -*-\nfrom pymxs import runtime as rt\n'
                  'def main():\n    rt.sphere(name="QzUserEntrySphere")\n')
        explicit = execute(py, 'main')
        assert rt.getNodeByName('QzUserEntrySphere') is not None
        ms = save('原生.ms', 'global qzUserNativeText = "中文原生"\nbox name:"QzUserMxsBox"\n', 'utf-8-sig')
        maximum = execute(ms)
        assert rt.getNodeByName('QzUserMxsBox') is not None
        assert rt.qzUserNativeText == '中文原生', repr(rt.qzUserNativeText)
        ms = save('入口.ms', 'global qzUserNativeEntry\nfn qzUserNativeEntry = (sphere name:"QzUserMxsSphere")\n', 'utf-8-sig')
        native_entry = execute(ms, 'qzUserNativeEntry')
        assert rt.getNodeByName('QzUserMxsSphere') is not None
        py = save('界面.py', '# -*- coding: utf-8 -*-\ntry:\n    from PySide6 import QtWidgets\nexcept ImportError:\n    from PySide2 import QtWidgets\n'
                  'value=0\ndef clicked():\n    global value\n    value += 1\n'
                  'window=QtWidgets.QPushButton("Callback")\nwindow.clicked.connect(clicked)\n')
        execute(py)
        module = sys.modules['_qz_max_' + script_id]
        module.window.click()
        assert module.value == 1
        module.window.close()
        py = save('退出.py', 'raise SystemExit(7)\n')
        failed = user_script(filepath=py, directory=folder, script_id=script_id)
        assert not failed['success'] and 'SystemExit' in failed['error']
        assert user_script(op='info')['version'] == 1
        return dict(ok=True, python=sys.version, max_version=int(rt.maxVersion()[0]),
                    python_script=python, python_entry=explicit, maxscript=maximum,
                    maxscript_entry=native_entry, unicode=True, callback=True,
                    exit_caught=True, main_restored=True)
    finally:
        shutil.rmtree(temporary)


try:
    result = run()
except BaseException:
    result = dict(ok=False, error=traceback.format_exc())
with io.open(os.path.join(OUTPUT, 'result.json'), 'w', encoding='utf-8') as stream:
    text = json.dumps(result, ensure_ascii=True, indent=2)
    stream.write(text if isinstance(text, type(u'')) else text.decode('ascii'))
print('MAX_USER_SCRIPTS_RESULT: ' + str(result['ok']))
