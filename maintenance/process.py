"""Start independent bundled programs without inheriting the parent's runtime."""
import os
import subprocess


def launch(arguments, **options):
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith(('_PYI_', 'PYTHON', 'QT_', 'PYSIDE', 'SHIBOKEN')):
            environment.pop(name)
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    supplied = options.pop('env', None)
    if supplied:
        environment.update(supplied)
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        kernel.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(arguments, env=environment, creationflags=subprocess.CREATE_NO_WINDOW, **options)
        finally:
            kernel.SetDllDirectoryW(buffer.value or None)
    return subprocess.Popen(arguments, env=environment, **options)
