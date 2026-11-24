"""
Build and verify the application plus standalone updater, then publish an
official-file inventory and a full offline update ZIP. Always use the project
venv. Generated spec/cache/logs stay in build/, user assets are never shipped.
See docs/build-guide.md and docs/packaging.md.
"""

import os
import shutil
import struct
import sys
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from maintenance.release import create_manifest, create_package, verify_directory
from maintenance.version import read_version

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
APP_NAME = "QzTools"
STAGE_DIST = os.path.join(BUILD_DIR, 'staging')

# 图标：SVG 是唯一可编辑源，exe 用的 .ico 每次打包现生成
ICON_SVG = os.path.join(PROJECT_DIR, "ui", "resources", "qz.svg")
ICON_ICO = os.path.join(BUILD_DIR, "qz.ico")

# ── 外置资源：exe 同级 ──────────────────────────────
# 目录（整份拷过去）
COPY_DIRS = [
    "plugins",
    "maya_bridge",
    "maya_actions",
    "max_bridge",
    "max_actions",
    "blender_bridge",
    "blender_actions",
]
# 单文件
COPY_FILES = [
    "defaults.json",
    "version.json",
]


def _clean():
    """拷贝前清掉目标，避免上轮残留的旧文件混进来。

    ⚠️ 旧文件残留比缺文件更危险 ——
       比如上一版有 plugins/foo/，这一版删了，
       残留的 foo/ 仍会被扫到并出现在资源树里。
    """
    for d in COPY_DIRS:
        p = os.path.join(STAGE_DIST, APP_NAME, d)
        if os.path.isdir(p):
            shutil.rmtree(p)
    # Remove the retired root FBX module from an existing distribution as well.
    for f in COPY_FILES + ["fbx_transfer_settings.py", "fbx_transfer_settings.pyc"]:
        p = os.path.join(STAGE_DIST, APP_NAME, f)
        if os.path.isfile(p):
            os.remove(p)
    # ui/resources 里的旧图标同理
    res_dst = os.path.join(STAGE_DIST, APP_NAME, "ui", "resources")
    if os.path.isdir(res_dst):
        shutil.rmtree(res_dst)


def _copy_resources():
    """把外置资源拷到 dist/<APP_NAME>/ 下。

    ⚠️ 必须排除 __pycache__ —— 里面是 .pyc，
       拷过去可能比源码还旧（Python 会优先用 .pyc），
       导致"改了源码没生效"这种极难查的问题。
    """
    out = os.path.join(STAGE_DIST, APP_NAME)
    for d in COPY_DIRS:
        src = os.path.join(PROJECT_DIR, d)
        if not os.path.isdir(src):
            print("  跳过（不存在）: %s" % d)
            continue
        dst = os.path.join(out, d)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "*.pyo"))
        print("  拷贝目录 %s" % d)

    # ui/resources/*.svg（图标外置，与其它资源同规则）
    res_src = os.path.join(PROJECT_DIR, "ui", "resources")
    res_dst = os.path.join(out, "ui", "resources")
    if os.path.isdir(res_src):
        shutil.copytree(res_src, res_dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print("  拷贝目录 ui/resources")

    for f in COPY_FILES:
        src = os.path.join(PROJECT_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out, f))
            print("  拷贝文件 %s" % f)


def _make_ico(sizes=(16, 32, 48, 64, 128, 256)) -> str:
    """用 QtSvg 把 ui/resources/qz.svg 渲染成多尺寸 .ico。

    为什么现生成而不是提交一个 qz.ico：
        用户说了"图标后期可以替换"。只留 SVG 一个源，
        换掉 SVG 重新打包，exe 图标就跟着变 ——
        否则会出现"我换了图怎么没生效"（改了 SVG，ico 还是旧的）。

    为什么手写 ICO：
        .venv 里没有 Pillow，不能 PIL.Image.save(".ico")。
        ICO 容器本身极简：6 字节头 + 每个尺寸 16 字节目录项 + 图片数据。
        Vista 起支持**直接内嵌 PNG**，所以每帧用 QImage.save(PNG) 就够，
        不需要拼 BMP + AND mask。

    返回 ico 路径；失败返回 ""（不影响打包，只是没有图标）。
    """
    try:
        from PySide6 import QtCore, QtGui, QtSvg
    except Exception as e:
        print("  跳过图标（PySide6 不可用：%s）" % e)
        return ""

    if not os.path.isfile(ICON_SVG):
        print("  跳过图标（找不到 %s）" % ICON_SVG)
        return ""

    # ⚠️ QGuiApplication 必须**被引用住**（局部变量在函数内一直存活），
    #   否则渲染中途被回收 → 直接崩，且无栈。
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])  # noqa: F841

    renderer = QtSvg.QSvgRenderer(ICON_SVG)
    if not renderer.isValid():
        print("  跳过图标（SVG 解析失败）")
        return ""

    blobs = []
    for s in sizes:
        img = QtGui.QImage(s, s, QtGui.QImage.Format_ARGB32)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        renderer.render(p)
        p.end()

        buf = QtCore.QByteArray()
        qbuf = QtCore.QBuffer(buf)
        qbuf.open(QtCore.QIODevice.WriteOnly)
        img.save(qbuf, "PNG")
        qbuf.close()
        blobs.append(bytes(buf))

    os.makedirs(os.path.dirname(ICON_ICO), exist_ok=True)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    payload = b""
    for s, blob in zip(sizes, blobs):
        # ⚠️ ICO 目录项里 256 要写成 0（字段只有 1 字节）
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(blob), offset)
        offset += len(blob)
        payload += blob

    with open(ICON_ICO, "wb") as f:
        f.write(header + entries + payload)
    print("  生成图标 %s（%d 个尺寸，%d 字节）"
          % (os.path.relpath(ICON_ICO, PROJECT_DIR), len(sizes),
             len(header) + len(entries) + len(payload)))
    return ICON_ICO


def _run_logged(command, label, environment):
    log = Path(BUILD_DIR) / 'logs' / (label + '.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    print('  %s -> %s' % (label, log), flush=True)
    with log.open('wb') as stream:
        result = subprocess.run(command, cwd=PROJECT_DIR, env=environment,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=900)
    if result.returncode:
        print(log.read_text(encoding='utf-8', errors='replace')[-6000:])
        raise RuntimeError('%s 未通过，日志：%s' % (label, log))


def _publish(stage, environment):
    stage = Path(stage)
    updater = Path(BUILD_DIR) / 'updater-dist' / 'QzUpdater.exe'
    _run_logged([sys.executable, '-m', 'PyInstaller', 'tools/updater_main.py',
                 '--name=QzUpdater', '--onefile', '--windowed', '--noconfirm', '--clean',
                 '--paths=' + PROJECT_DIR, '--distpath=' + str(updater.parent),
                 '--workpath=' + str(Path(BUILD_DIR) / 'pyinstaller' / 'updater'),
                 '--specpath=' + str(Path(BUILD_DIR) / 'specs'),
                 '--exclude-module=PySide6', '--exclude-module=numpy', '--exclude-module=matplotlib'],
                'build-updater', environment)
    shutil.copy2(updater, stage / updater.name)
    manifest = create_manifest(stage)
    test_directory = Path(BUILD_DIR) / 'test-data' / uuid.uuid4().hex
    test_environment = dict(environment, QZ_DATA_DIR=str(test_directory),
                            QZ_USER_PLUGINS_DIR=str(test_directory / 'user_plugins'),
                            QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1')
    _run_logged([str(stage / 'QzUpdater.exe'), '--selftest'], 'updater-selftest', test_environment)
    _run_logged([str(stage / 'QzTools.exe'), '--selftest'], 'app-selftest', test_environment)
    _run_logged([sys.executable, '-B', 'selfcheck_dist.py', str(stage)], 'dist-check', test_environment)
    verify_directory(stage)
    output = Path(DIST_DIR) / APP_NAME
    # Build destinations are artifacts. Never silently displace a user's data.
    if output.exists() and any((output / name).exists() for name in ('data', 'user_plugins', 'temp', 'updates', 'config.json')):
        raise RuntimeError('dist/QzTools 中含用户数据，已保留原目录；新产物在 build/staging/QzTools')
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if output.exists():
        previous = Path(BUILD_DIR) / 'previous-dist' / uuid.uuid4().hex
        previous.parent.mkdir(parents=True, exist_ok=True)
        output.rename(previous)
    try:
        stage.rename(output)
    except Exception:
        if previous is not None:
            previous.rename(output)
        raise
    directory = Path(DIST_DIR) / 'releases'
    package = directory / ('QzTools-' + manifest['version'] + '-windows-x64.zip')
    package_info = create_package(output, package)
    (directory / 'latest.json').write_text(json.dumps(dict(app_id=APP_NAME, format=1, **package_info),
                                                     ensure_ascii=False, indent=2), encoding='utf-8')
    (package.with_suffix('.zip.sha256')).write_text(package_info['sha256'] + '  ' + package.name + '\n', encoding='ascii')
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT_DIR, capture_output=True, text=True)
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=PROJECT_DIR, capture_output=True, text=True, encoding='utf-8')
    except OSError:
        commit = status = None  # Source ZIPs/build machines need not have Git installed.
    report = dict(version=manifest['version'], files=len(manifest['files']), package=package_info,
                  installation=str(output), previous=str(previous) if previous else None,
                  git_commit=commit.stdout.strip() if commit and commit.returncode == 0 else None,
                  working_tree_dirty=bool(status.stdout.strip()) if status and status.returncode == 0 else None)
    report_path = Path(BUILD_DIR) / 'reports' / 'release.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('发布目录：%s\n更新包：%s' % (output, package), flush=True)


def build():
    try:
        import PyInstaller.__main__
    except ImportError:
        # ⚠️ 2026-10-22：venv 改成继承系统包（--system-site-packages），
        #   所以 .venv 里现在**也有** PyInstaller 了 ——
        #   「必须用系统 python」这个前提已消失，统一走 .venv。
        print("✗ 当前解释器没装 PyInstaller。装一下：")
        print(r"    .venv\Scripts\python.exe -m pip install -r requirements-build.txt")
        print("或用装了 PyInstaller 的解释器跑本脚本。")
        print("当前用的：%s" % sys.executable)
        return 1

    print("=" * 60)
    print('  version = %s' % read_version()['version'])
    print("打包 %s" % APP_NAME)
    print("  python = %s" % sys.executable)
    print("  PyInstaller = %s" % PyInstaller.__version__)
    print("=" * 60)

    # ★ 图标要在 PyInstaller 之前生成（--icon 指向它）
    ico = _make_ico()

    args = [
        'main.py',
        '--name=%s' % APP_NAME,
        # ★ onedir 不是 onefile —— 理由见模块 docstring
        '--onedir',
        '--windowed',
        f'--distpath={STAGE_DIST}',
        '--workpath=' + str(Path(BUILD_DIR) / 'pyinstaller' / 'app'),
        '--specpath=' + str(Path(BUILD_DIR) / 'specs'),
        # ★★★ 外置插件的依赖必须显式 hidden-import ★★★
        #
        # 插件 widget.py 是**运行时从磁盘加载**的外置文件，
        # PyInstaller 的静态分析**看不见它们**，所以不会把它们
        # import 的模块打进 exe。
        #
        # 实测踩到的：所有插件都 `from ui.plugin_widget import BasePluginWidget`，
        # 而 exe 内部**没有任何一处** import ui.plugin_widget
        # （BasePluginWidget 只被外置插件用），
        # 于是它没被打进 exe → 打包后打开任何插件都报
        #     "No module named 'ui.plugin_widget'"
        #
        # ⚠️ 这类 bug 极静默：exe 能启动、资源树不空、图标正常，
        #    只有真正点开插件才炸。而且用户第一反应会是
        #    "是不是资源没拷全"（其实是模块没打进 exe）。
        #
        # → 凡是**外置插件可能 import** 的模块，都要显式列出来。
        #   下面的清单来自 plugins/*/*/widget.py 的实际 import。
        '--hidden-import=ui.plugin_widget',
        '--hidden-import=ui.blender_setup',
          '--hidden-import=ui.user_scripts',
          '--hidden-import=ui.user_script_widget',
          '--hidden-import=ui.user_script_dialog',
        '--hidden-import=ui.skin_weights',
        '--hidden-import=ui.max_skin_weights',
        '--hidden-import=ui.skin_transfer',
        '--hidden-import=ui.maya_blender_skin_transfer',
        '--hidden-import=ui.maya_to_blender',
        '--hidden-import=ui.fbx_transfer',
        '--hidden-import=ui.maya_fbx_transfer',
        '--hidden-import=ui.blender_fbx_transfer',
        '--hidden-import=ui.vertex_positions',
        '--hidden-import=ui.vertex_color_match',
        '--hidden-import=ui.skin_weight_files',
        '--hidden-import=ui.skin_weight_mapping',
        '--hidden-import=numpy',
        '--hidden-import=ui.plugin_loader',
        '--hidden-import=ui.maya_setup',
        '--hidden-import=ui.max_setup',
        # maya_bridge 只有 client 是中台侧要的；
        # server.py 会被 maya_actions 拉进来，而它需要 maya 模块。
        '--hidden-import=maya_bridge.client',
        # ★ PySide6 只收 Essentials + Addons 里我们要的模块。
        #   原来用 `--collect-all=PySide6`，结果产物 **682MB** ——
        #   里面塞了 QtWebEngineCore.dll(195M)、qml(26M)、
        #   translations(61M)、avcodec(14M)… 全是用不上的东西。
        #   这个中台只用 QtCore/QtGui/QtWidgets/QtSvg。
        '--collect-submodules=PySide6.QtCore',
        '--collect-submodules=PySide6.QtGui',
        '--collect-submodules=PySide6.QtWidgets',
        '--collect-submodules=PySide6.QtSvg',
        # ★ 明确排除用不到的大件（省 300MB+）
        '--exclude-module=PySide6.QtWebEngineCore',
        '--exclude-module=PySide6.QtWebEngineWidgets',
        '--exclude-module=PySide6.QtWebEngineQuick',
        '--exclude-module=PySide6.QtQml',
        '--exclude-module=PySide6.QtQuick',
        '--exclude-module=PySide6.QtQuick3D',
        '--exclude-module=PySide6.QtMultimedia',
        '--exclude-module=PySide6.QtMultimediaWidgets',
        '--exclude-module=PySide6.Qt3DCore',
        '--exclude-module=PySide6.Qt3DRender',
        '--exclude-module=PySide6.QtCharts',
        '--exclude-module=PySide6.QtDataVisualization',
        '--exclude-module=PySide6.QtPdf',
        '--exclude-module=PySide6.QtPdfWidgets',
        '--exclude-module=PySide6.QtDesigner',
        '--exclude-module=PySide6.QtBluetooth',
        '--exclude-module=PySide6.QtNfc',
        '--exclude-module=PySide6.QtPositioning',
        '--exclude-module=PySide6.QtSql',
        '--exclude-module=PySide6.QtTest',
        '--exclude-module=tkinter',
        '--exclude-module=matplotlib',
        # ★ 不打包 DCC 端的东西 —— 它们外置
        '--exclude-module=maya_bridge.server',
        '--exclude-module=maya_actions',
        '--exclude-module=max_actions',
        '--exclude-module=max_bridge',
        '--exclude-module=blender_bridge',
        '--exclude-module=blender_actions',
        '--noconfirm',
    ]

    # ★ exe 文件图标（资源管理器/任务栏看到的那个）。
    #   窗口内图标由 main.py 的 app.setWindowIcon 负责，两处是独立的 ——
    #   只做前者时 exe 有图标但运行中的窗口没有，反之亦然。
    if ico:
        args.append('--icon=%s' % ico)

    # DLL 解析须优先使用 Windows 系统库，避免 PATH 中 Poppler 等工具的
    # 同名 ICU 覆盖 Qt 实际使用的系统 ICU。仅影响本次打包，不改用户环境。
    previous_path = os.environ.get("PATH", "")
    if os.name == "nt":
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        os.environ["PATH"] = system32 + os.pathsep + previous_path
    try:
        environment = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                           PYINSTALLER_CONFIG_DIR=str(Path(BUILD_DIR) / 'pyinstaller-cache'))
        for key in list(environment):
            if key.startswith(('QZ_RIG_', 'QZ_TOOLBAR_')):
                environment.pop(key)
        _run_logged([sys.executable, '-m', 'PyInstaller'] + args + ['--clean'], 'build-app', environment)
    finally:
        os.environ["PATH"] = previous_path

    print()
    print("拷贝外置资源…")
    _clean()
    _copy_resources()

    out = os.path.join(STAGE_DIST, APP_NAME)
    _publish(out, environment)
    out = os.path.join(DIST_DIR, APP_NAME)
    print()
    print("=" * 60)
    print("✓ 打包完成: %s" % out)
    print("=" * 60)
    print()
    print("目录结构：")
    for root, dirs, files in os.walk(out):
        # 跳过 _internal（PyInstaller 的依赖目录）
        dirs[:] = [d for d in dirs if d != "_internal"]
        rel = os.path.relpath(root, out)
        if rel == ".":
            print("  %s/" % APP_NAME)
            continue
        depth = rel.count(os.sep)
        print("  %s%s/" % ("  " * depth, os.path.basename(root)))
        for f in sorted(files):
            print("  %s%s" % ("  " * (depth + 1), f))
    print()
    print("★ 这个文件夹要**整个**拷给用户（不是只拷 exe）")
    print("★ 用户的 Maya / 3ds Max 装自动启动时，写进去的是这个绝对路径")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except Exception as error:
        print('构建失败：%s' % error, flush=True)
        sys.exit(1)
