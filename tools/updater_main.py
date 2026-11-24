"""Standalone update helper entry; built separately with its own small runtime."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.release import inspect_package
from maintenance.updater import apply_update
from maintenance.process import launch


def _wait(pid):
    if not pid:
        return
    if os.name != 'nt':
        raise RuntimeError('此更新助手目前只支持 Windows')
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return  # The outgoing process already exited.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError('中台尚未退出，请关闭后重试；不会强制结束程序')
    finally:
        kernel.CloseHandle(handle)


def _verify(root):
    with tempfile.TemporaryDirectory(prefix='qz-update-check-') as directory:
        process = launch([str(root / 'QzTools.exe'), '--selftest'], cwd=str(root),
                         env={'QZ_DATA_DIR': directory, 'QT_QPA_PLATFORM': 'offscreen'},
                         stdout=-3, stderr=-3)
        try:
            result = process.wait(timeout=90)
        except Exception:
            process.kill()
            process.wait()
            raise RuntimeError('新版程序自检未完成')
        if result != 0:
            raise RuntimeError('新版程序自检失败，已停止更新')


def _perform(args, progress):
    progress('正在等待中台退出…')
    _wait(args.wait_pid)
    result = apply_update(args.package, args.target, progress=progress, verify_installed=_verify)
    if not args.no_restart:
        try:
            launch([str(Path(args.target) / 'QzTools.exe')], cwd=args.target)
        except Exception as error:
            result['restart_error'] = str(error)
    return result


def main():
    parser = argparse.ArgumentParser(description='Qz Tools 独立更新助手')
    parser.add_argument('--target')
    parser.add_argument('--package')
    parser.add_argument('--wait-pid', type=int)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--no-restart', action='store_true')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        from maintenance.release import safe_relative
        safe_relative('plugins/maya/example/widget.py')
        # Exercise the bundled Tcl/Tk runtime without displaying a window.
        import tkinter as tk
        check_window = tk.Tk()
        check_window.withdraw()
        check_window.update_idletasks()
        check_window.destroy()
        try:
            safe_relative('user_plugins/maya/tool/main.py')
        except ValueError:
            return 0
        return 1
    if not args.target:
        args.target = str(Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT / 'dist' / 'QzTools')
    if args.headless:
        if not args.worker:
            raise ValueError('--headless 需要 --worker；安装目录中的助手不能原地替换自身')
        try:
            result = _perform(args, lambda message: None)
            result['ok'] = True
        except Exception as error:
            result = dict(ok=False, error=str(error))
        report = Path(args.target) / 'updates' / 'last-result.json'
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if result['ok'] else 1
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    window = tk.Tk()
    window.title('Qz Tools 更新')
    window.geometry('440x150')
    window.resizable(False, False)
    message = tk.StringVar(value='选择更新包，更新前请先关闭中台。')
    ttk.Label(window, textvariable=message, wraplength=400).pack(padx=20, pady=18)
    progressbar = ttk.Progressbar(window, mode='indeterminate')
    progressbar.pack(fill='x', padx=20)
    messages = queue.Queue()
    status = {'running': False, 'code': 0}
    def perform():
        try:
            result = _perform(args, lambda text: messages.put(('progress', text)))
            messages.put(('complete', result))
        except Exception as error:
            messages.put(('error', str(error)))
    def begin():
        if not args.worker:
            package = args.package or filedialog.askopenfilename(parent=window, title='选择 Qz 更新包', filetypes=[('Qz 更新包', '*.zip')])
            if not package:
                return
            try:
                info = inspect_package(package)
                if not messagebox.askokcancel('准备更新', '将更新到 %s。请先关闭中台，完成后会重新打开。' % info['version'], parent=window):
                    return
                from maintenance.client import start_update
                start_update(args.target, package, restart=not args.no_restart)
                window.destroy()
            except Exception as error:
                messagebox.showerror('无法开始更新', str(error), parent=window)
            return
        status['running'] = True
        button.configure(state='disabled')
        progressbar.start()
        threading.Thread(target=perform, daemon=True).start()
    def poll():
        try:
            kind, value = messages.get_nowait()
        except queue.Empty:
            window.after(100, poll)
            return
        if kind == 'progress':
            message.set(value)
            window.after(50, poll)
            return
        status['running'] = False
        progressbar.stop()
        if kind == 'complete':
            message.set('更新完成。')
            if value.get('restart_error'):
                messagebox.showinfo('更新完成', '更新已完成，请手动打开中台。', parent=window)
            window.after(1200, window.destroy)
        else:
            status['code'] = 1
            message.set('更新未完成。')
            messagebox.showerror('更新未完成', value, parent=window)
            button.configure(text='关闭', state='normal', command=window.destroy)
    button = ttk.Button(window, text='选择更新包', command=begin)
    button.pack(pady=14)
    window.protocol('WM_DELETE_WINDOW', lambda: None if status['running'] else window.destroy())
    window.after(100, poll)
    if args.worker:
        window.after(50, begin)
    window.mainloop()
    return status['code']


if __name__ == '__main__':
    sys.exit(main())
