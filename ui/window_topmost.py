"""Change window stacking without moving, resizing or activating the window."""
import sys

from PySide6 import QtCore, QtGui


def set_window_topmost(window, enabled: bool):
    if sys.platform == 'win32' and QtGui.QGuiApplication.platformName() == 'windows':
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        set_position = user32.SetWindowPos
        set_position.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        set_position.restype = wintypes.BOOL
        # HWND_TOPMOST / HWND_NOTOPMOST; preserve geometry and focus.
        # Let Windows update owned dialogs' stacking along with their owner.
        flags = 0x0001 | 0x0002 | 0x0010
        if not set_position(int(window.winId()), -1 if enabled else -2,
                            0, 0, 0, 0, flags):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    # Qt fallback for other platforms and headless development checks.
    visible = window.isVisible()
    geometry = window.normalGeometry() if window.isMaximized() or window.isMinimized() else window.geometry()
    state = window.windowState()
    window.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, enabled)
    window.setGeometry(geometry)
    window.setWindowState(state)
    if visible:
        window.show()
