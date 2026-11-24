# -*- coding: utf-8 -*-
"""
test_app_icon.py — 主程序图标（"Qz"）的三条链路。

  1. 源文件 `ui/resources/qz.svg` 能被 QtSvg 解析、渲染出内容、**不含 `<text>`**
  2. `main.py` 设了窗口/任务栏图标，且走 `paths.resource()`（frozen 后 __file__ 会变）
  3. `build.py` 在 PyInstaller **之前**用 `--icon` 传入现生成的 ICO，且 ICO 结构合法

⚠️ 为什么不用 grep 找代码：注释里就写着 `setWindowIcon(...)` /
   `--icon=` 这些字（用于说明），直接搜文本会**自我命中**（踩过 5 次）。
   这里用 `ast` 解析 —— 注释和 docstring 天然不参与。
"""
import ast
import importlib.util
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # 必须持引用

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (label, "" if extra is None else extra))


def _dotted(node):
    """把 `a.b.c` 这样的属性链还原成字符串。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def find_call(tree, method):
    """找第一个 `xxx.method(...)` 调用节点。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == method:
            return node
    return None


def main():
    svg = os.path.join(HERE, "ui", "resources", "qz.svg")
    main_py = os.path.join(HERE, "main.py")
    build_py = os.path.join(HERE, "build.py")

    print("\n== 1. ui/resources/qz.svg ==")
    check("图标源文件存在", os.path.isfile(svg), svg)
    with open(svg, "r", encoding="utf-8") as f:
        text = f.read()
    check("是非空的 SVG", text.lstrip().startswith("<svg") and len(text) > 400,
          len(text))
    # ⚠️ 先剥 XML 注释再判断 —— 文件里恰好有条注释写着"不用 <text>（依赖系统字体）"，
    #    直接搜会**自我命中**（代码注释里记着坏写法，这坑踩过 5 次）。
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S).lower()
    check("★★ 不含 <text>（不依赖系统字体，16px 下跨机器才一致）",
          "<text" not in body)
    check("用 <path>/<rect> 画字形", "<path" in body)

    renderer = QtSvg.QSvgRenderer(svg)
    check("QtSvg 能解析（语法没坏）", renderer.isValid())
    check("有默认尺寸", renderer.defaultSize().width() > 0,
          renderer.defaultSize())

    img = QtGui.QImage(256, 256, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    renderer.render(p)
    p.end()
    # 采样中间一列，确认真的画出了不透明像素（不是"能解析但全透明"）
    opaque = 0
    for y in range(0, 256, 8):
        for x in range(0, 256, 8):
            if img.pixelColor(x, y).alpha() > 0:
                opaque += 1
    check("★ 渲染出不透明像素（不是空白图）", opaque > 100, opaque)
    check("四角是透明的（圆角底，不铺满整块方形）",
          img.pixelColor(1, 1).alpha() == 0, img.pixelColor(1, 1).alpha())

    print("\n== 2. main.py 窗口 / 任务栏图标 ==")
    tree = parse(main_py)
    call = find_call(tree, "setWindowIcon")
    check("调用了 setWindowIcon", call is not None)
    if call is not None:
        arg = ast.unparse(call.args[0]) if call.args else ""
        check("★ 图标走 paths.resource(...)（frozen 后 __file__ 指向 _internal/）",
              "paths.resource(" in arg, arg)
        check("用的是 qz.svg", "qz.svg" in arg, arg)
    check("import 了 paths",
          any(isinstance(n, ast.Import) and any(a.name == "paths" for a in n.names)
              or isinstance(n, ast.ImportFrom) and n.module == "paths"
              for n in ast.walk(tree)))
    check("setWindowIcon 在 QApplication 构造之后",
          call is not None
          and call.lineno > min(n.lineno for n in ast.walk(tree)
                                if isinstance(n, ast.Call)
                                and "QApplication" in _dotted(n.func)), )

    print("\n== 3. build.py 生成并使用 ICO ==")
    btree = parse(build_py)
    funcs = {n.name for n in ast.walk(btree) if isinstance(n, ast.FunctionDef)}
    check("定义了 _make_ico()", "_make_ico" in funcs)

    make_line = None
    for node in ast.walk(btree):
        if isinstance(node, ast.Call) and _dotted(node.func) == "_make_ico":
            make_line = node.lineno
            break
    check("打包流程里调用了 _make_ico()", make_line is not None)

    run_line = None
    for node in ast.walk(btree):
        if (isinstance(node, ast.Call) and _dotted(node.func) == '_run_logged'
                and node.args and 'PyInstaller' in ast.unparse(node.args[0])):
            run_line = node.lineno
    check("PyInstaller 在独立构建进程中调用", run_line is not None)
    # --icon 是 append 到 args 列表上的，不在 run(...) 的实参里 →
    # 必须在整个模块的**字符串字面量**中找（ast 天然不含注释）。
    icon_str = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and "--icon=" in n.value for n in ast.walk(btree))
    check("★★ 传了 --icon（exe 文件图标靠它；和窗口图标是两件事）", icon_str)
    check("★★ 图标在 PyInstaller 之前生成（否则 --icon 指向不存在的文件）",
          make_line is not None and run_line is not None and make_line < run_line,
          (make_line, run_line))

    # 资源外置：qz.svg 必须跟着 ui/resources 一起被拷进 dist
    src = ast.unparse(btree)
    check("资源目录完整复制（含svg和controls子目录）", 'shutil.copytree(res_src, res_dst' in src)

    print("\n== 4. ICO 结构（真跑一次 _make_ico） ==")
    spec = importlib.util.spec_from_file_location("qz_build_under_test", build_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sizes = (16, 32, 48, 256)
    ico = mod._make_ico(sizes=sizes)
    check("生成了 ico 文件", bool(ico) and os.path.isfile(ico), ico)
    if ico:
        with open(ico, "rb") as f:
            data = f.read()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        check("ICO 头合法（reserved=0 / type=1）", reserved == 0 and kind == 1,
              (reserved, kind))
        check("尺寸数量对得上", count == len(sizes), count)
        ok_dim = True
        ok_png = True
        for i, s in enumerate(sizes):
            base = 6 + 16 * i
            w = data[base]
            n, off = struct.unpack("<II", data[base + 8:base + 16])
            expect = 0 if s >= 256 else s          # ⚠️ 256 在目录项里必须写 0
            if w != expect:
                ok_dim = False
            if data[off:off + 8] != b"\x89PNG\r\n\x1a\n":   # Vista+ 允许内嵌 PNG
                ok_png = False
        check("★ 每项尺寸正确（256 写成 0，字段只有 1 字节）", ok_dim)
        check("★ 每帧是合法 PNG 数据", ok_png)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
