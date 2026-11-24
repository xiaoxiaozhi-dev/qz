"""
log_panel.py — 纯日志正文，无标题 / 无折叠 / 无按钮。

- 整片单一背景色 #181818
- 只有 QTextEdit 正文，高度由外部 splitter 手柄拖动决定
- 所有操作走右键菜单（VSCode 风格）：复制全部 / 清空
- 行数上限（默认 1000），超出后丢弃最旧的行
"""

from PySide6 import QtCore, QtGui, QtWidgets
from datetime import datetime


DEFAULT_HEIGHT = 70
DEFAULT_MAX_LINES = 1000
MIN_MAX_LINES = 100
MAX_MAX_LINES = 100000


class LogPanel(QtWidgets.QWidget):
    """底部日志面板。高度完全由 QSplitter 拖动控制，无任何按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogPanel")
        self._max_lines = DEFAULT_MAX_LINES
        # 累计写入行数（不跟着裁剪走，用于判断是否需要裁剪）
        self._written = 0

        # body — 只有正文
        self._view = QtWidgets.QTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        self._view.setFrameShape(QtWidgets.QFrame.NoFrame)
        # 关键：QTextEdit 自带 minimumSizeHint=84px，会把日志区地板卡在 84，
        # 导致 DEFAULT_HEIGHT=70 / 拖到很小都失效。这里显式清零。
        self._view.setMinimumHeight(0)
        self._view.setMinimumWidth(0)
        self._view.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored
        )
        self._view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_menu)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._view)

        # 最小高度 0 → splitter 可以把手柄拖到底，日志区完全隐藏；
        # 找回来的方式：再拖 4px 手柄边缘
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)

    # ── public API ────────────────────────────
    def info(self, msg: str):
        self._append("INFO", msg)

    def warn(self, msg: str):
        self._append("WARN", msg)

    def error(self, msg: str):
        self._append("ERROR", msg)

    def clear(self):
        self._view.clear()
        self._written = 0

    def max_lines(self) -> int:
        return self._max_lines

    def set_max_lines(self, n: int):
        """设置行数上限（超出后丢弃最旧的）。立即生效。"""
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        self._max_lines = max(MIN_MAX_LINES, min(n, MAX_MAX_LINES))
        self._trim()

    def line_count(self) -> int:
        return self._view.document().blockCount()

    def copy_all(self):
        QtWidgets.QApplication.clipboard().setText(self._view.toPlainText())

    def toPlainText(self) -> str:
        return self._view.toPlainText()

    # ── private ───────────────────────────────
    def _show_menu(self, pos: QtCore.QPoint):
        """VSCode 风格右键菜单。"""
        menu = QtWidgets.QMenu(self)
        has_text = bool(self._view.toPlainText().strip())
        act_copy = menu.addAction("复制全部")
        act_copy.setEnabled(has_text)
        menu.addSeparator()
        act_clear = menu.addAction("清空日志")
        act_clear.setEnabled(has_text)

        chosen = menu.exec(self._view.mapToGlobal(pos))
        if chosen == act_copy:
            self.copy_all()
        elif chosen == act_clear:
            self.clear()

    def _append(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        self._view.append(line)
        self._written += 1
        self._trim()
        sb = self._view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _trim(self):
        """超出上限时从最旧的行开始删。

        QTextDocument 的 block 0 是空行，所以可删行数是 blockCount() - 1。
        删除量留 1% 余量，避免每写一行就裁剪一次。
        """
        doc = self._view.document()
        excess = doc.blockCount() - 1 - self._max_lines
        if excess <= 0:
            return
        # 多删一点留余量，减少裁剪频率
        excess += max(1, self._max_lines // 100)

        cursor = self._view.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(
            QtGui.QTextCursor.Down,
            QtGui.QTextCursor.KeepAnchor,   # 必须 KeepAnchor，否则选不中任何东西
            excess,
        )
        cursor.removeSelectedText()
        self._view.setTextCursor(cursor)
