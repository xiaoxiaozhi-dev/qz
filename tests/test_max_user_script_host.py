# -*- coding: utf-8 -*-
"""Executor tests runnable under real Python2.7 and Python3; no Max scene."""
from __future__ import print_function, unicode_literals
import io
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from max_actions.user_scripts import user_script


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix='qz_max_user_')
        self.folder = os.path.join(self.temporary, "中文 工具's")
        os.mkdir(self.folder)
        self.filepath = os.path.join(self.folder, 'main.py')
        self.script_id = 'user_' + 'a' * 32
        self.old_path = list(sys.path)
        self.old_main = sys.modules.get('__main__')

    def tearDown(self):
        sys.path[:] = self.old_path
        sys.modules.pop('_qz_max_' + self.script_id, None)
        shutil.rmtree(self.temporary)

    def write(self, code):
        with io.open(self.filepath, 'w', encoding='utf-8') as source:
            source.write('# -*- coding: utf-8 -*-\n' + code)

    def run_tool(self, **params):
        values = dict(filepath=self.filepath, script_id=self.script_id, directory=self.folder)
        values.update(params)
        return user_script(**values)

    def test_actual_interpreter_syntax_and_main_namespace_isolation(self):
        statement = 'print "PY2"\n' if sys.version_info[0] == 2 else 'print(f"PY{3}")\n'
        self.write(statement + 'if __name__ == "__main__":\n    print(u"中文完成")\n')
        result = self.run_tool()
        self.assertTrue(result['success'], result)
        self.assertIn('中文完成', result['output'])
        self.assertIs(sys.modules.get('__main__'), self.old_main)
        self.assertNotIn('_qz_max_' + self.script_id, self.old_main.__dict__)

    def test_explicit_entry_resources_helpers_and_retained_callback_globals(self):
        with io.open(os.path.join(self.folder, 'qz_test_helper_unique.py'), 'w', encoding='utf-8') as stream:
            stream.write('VALUE = 7\n')
        self.write('import qz_test_helper_unique\nvalue=0\n'
                   'def callback():\n    global value\n    value += 1\n'
                   'def main():\n    callback()\n    print(qz_test_helper_unique.VALUE)\n')
        result = self.run_tool(entry='main')
        self.assertTrue(result['success'], result)
        module = sys.modules['_qz_max_' + self.script_id]
        self.assertEqual(module.value, 1)
        self.assertEqual(module.__file__, self.filepath)
        module.callback()
        self.assertEqual(module.value, 2)
        self.assertIn('7', result['output'])
        sys.modules.pop('qz_test_helper_unique', None)

    def test_output_limit_exit_exception_and_stream_restoration(self):
        stdout, stderr = sys.stdout, sys.stderr
        self.write('import sys\nprint(u"日志")\nsys.stderr.write("x"*40000)\nraise SystemExit(5)\n')
        result = self.run_tool()
        self.assertFalse(result['success'])
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['output']), 32768)
        self.assertIn('SystemExit', result['error'])
        self.assertIn('main.py', result['error'])
        self.assertIs(sys.stdout, stdout)
        self.assertIs(sys.stderr, stderr)

    def test_target_compile_reports_invalid_syntax_without_running_anything(self):
        self.write('def invalid(:\n    pass\n')
        result = self.run_tool()
        self.assertFalse(result['success'])
        self.assertIn('SyntaxError', result['error'])
        self.assertIs(sys.modules.get('__main__'), self.old_main)

    def test_mxs_file_and_global_entry_use_runtime_without_text_interpolation(self):
        self.filepath = os.path.join(self.folder, 'main.ms')
        with io.open(self.filepath, 'w', encoding='utf-8-sig') as source:
            source.write('global qzUserRun\nfn qzUserRun = ()\n')
        module = types.ModuleType(str('pymxs'))
        calls = []
        class Runtime(object):
            def fileIn(self, path):
                calls.append(path)
            def qzUserRun(self):
                calls.append('entry')
        module.runtime = Runtime()
        original = sys.modules.get('pymxs')
        sys.modules['pymxs'] = module
        try:
            result = self.run_tool(entry='qzUserRun')
            self.assertTrue(result['success'], result)
            self.assertEqual(calls, [self.filepath, 'entry'])
        finally:
            if original is None:
                sys.modules.pop('pymxs', None)
            else:
                sys.modules['pymxs'] = original

    def test_wrong_directory_entry_and_background_thread_rejected(self):
        self.write('print("never")\n')
        for params in (dict(directory=os.path.join(self.temporary, 'other')),
                       dict(entry='main();'), dict(script_id='../bad')):
            with self.assertRaises(ValueError):
                self.run_tool(**params)
        errors = []
        def background():
            try:
                self.run_tool()
            except RuntimeError:
                errors.append(True)
        thread = threading.Thread(target=background)
        thread.start()
        thread.join(1)
        self.assertEqual(errors, [True])

    def test_info_is_read_only_and_reports_actual_python_version(self):
        result = user_script(op='info')
        self.assertEqual(result['python_version'], list(sys.version_info[:3]))
        self.assertEqual(result['languages'], ['python', 'maxscript'])
        self.assertFalse(os.path.exists(self.filepath))


if __name__ == '__main__':
    unittest.main(verbosity=2)
