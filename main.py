"""
main.py — 中台入口。

打包说明（2026-10-20）：
  项目根目录由 `paths.project_dir()` 决定 ——
    源码直接跑   → 项目根 = 源码目录
    打包后运行   → 项目根 = exe 所在目录（外置资源都在那儿）

  ⚠️ 所以**把 exe 拷到源码目录直接跑也是支持的** ——
     它会从 exe 旁边读 plugins/ 和 config.json。
     开发时想免打包就拷过去，但要注意 exe 是**快照**，
     改了源码要重新拷才生效。
"""

import os
import sys
sys.dont_write_bytecode = True

# 打包后 sys.path 里没有项目根，得手动加 ——
# 否则 `import paths` / `from ui.main_window import ...` 都找不到。
if getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from PySide6 import QtGui, QtWidgets

import paths
from ui.main_window import MainWindow
from ui.app_style import apply_application_style


def main():
    # ★ 自检模式：不建 QApplication，跑完就退。
    #   --windowed 的 exe 没有控制台，stdout 是空的，
    #   所以结果写到 exe 同级的 selftest.log。
    if "--selftest" in sys.argv:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        import selftest
        failures, text = selftest.run()
        try:
            print(text)
        except Exception:
            pass
        return 1 if failures else 0

    app = QtWidgets.QApplication(sys.argv)

    # 设置应用样式
    apply_application_style(app)

    # ★ 窗口 / 任务栏图标（`ui/resources/qz.svg`，由 build.py 的 --icon 生成 exe 图标）
    #   ⚠️ 与 exe 文件图标是**两件独立的事**：--icon 只管资源管理器里那个，
    #      运行中的窗口 / 任务栏靠这一行。两者都要，缺一个就会出现
    #      "exe 有图标、开起来变白纸" 的诡异现象。
    #   走 paths.resource() —— frozen 后 main.py 的 __file__ 指向 _internal/。
    #   失败静默忽略：图标缺失不该拦着程序启动。
    try:
        app.setWindowIcon(QtGui.QIcon(paths.resource("qz.svg")))
    except Exception:
        pass

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())
