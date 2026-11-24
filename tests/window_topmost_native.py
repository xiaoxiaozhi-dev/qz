"""Verify Windows stacking on this process's disposable Qt windows only."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
os.environ['QT_QPA_PLATFORM'] = 'windows'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6 import QtCore, QtTest, QtWidgets
from ui.window_topmost import set_window_topmost


def run():
    app = QtWidgets.QApplication([])
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    def topmost(window):
        return bool(user32.GetWindowLongPtrW(int(window.winId()), -20) & 0x00000008)
    window = QtWidgets.QMainWindow()
    window.setWindowTitle('Qz disposable topmost verification')
    window.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
    window.setGeometry(120, 120, 500, 350)
    window.show()
    QtTest.QTest.qWait(40)
    report = {}
    dialog = None
    try:
        assert not topmost(window)
        geometry, handle = window.geometry(), int(window.winId())
        foreground = user32.GetForegroundWindow()
        set_window_topmost(window, True)
        assert topmost(window)
        assert user32.GetForegroundWindow() == foreground, 'Pin changed focus'
        assert int(window.winId()) == handle
        assert window.geometry() == geometry
        report['toggle_preserves_geometry_handle_focus'] = True
        window.showMinimized()
        QtTest.QTest.qWait(40)
        assert window.isMinimized() and topmost(window)
        window.showNormal()
        QtTest.QTest.qWait(40)
        assert topmost(window)
        window.hide()
        window.show()
        QtTest.QTest.qWait(40)
        assert topmost(window)
        report['minimize_restore_hide_show'] = True
        window.showMaximized()
        QtTest.QTest.qWait(40)
        normal = window.normalGeometry()
        set_window_topmost(window, False)
        assert window.isMaximized() and not topmost(window)
        set_window_topmost(window, True)
        assert window.isMaximized() and window.normalGeometry() == normal
        report['maximized_state'] = True
        dialog = QtWidgets.QDialog(window)
        dialog.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        dialog.setWindowTitle('Qz disposable child dialog')
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.show()
        QtTest.QTest.qWait(40)
        assert dialog.isVisible() and topmost(dialog)
        assert user32.GetWindow(int(dialog.winId()), 4) == int(window.winId())
        report['owned_modal_dialog_topmost'] = True
        set_window_topmost(window, False)
        assert not topmost(window) and not topmost(dialog)
        report['unpin'] = True
        return dict(ok=True, reports=report)
    finally:
        if dialog is not None:
            dialog.close()
        set_window_topmost(window, False)
        window.close()
        app.processEvents()


if __name__ == '__main__':
    result = run()
    output = ROOT / 'build' / 'window-topmost-native-result.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
