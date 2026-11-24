# -*- coding: utf-8 -*-
"""Persistent Max user-tool buttons. Python2/3, host Qt only, no bridge needed."""
from __future__ import print_function, unicode_literals

import io
import json
import os
import re
import sys
import tempfile

from max_bridge.server import register_action
from max_actions.user_scripts import _path, _text, string_types

_ID = re.compile(r'user_[0-9a-f]{32}\Z')
_TITLE = 'Qz 自定义工具'
_OBJECT = 'Qz.UserTools.Toolbar'
_MARKER = '-- Qz user tools startup v1'


def _inside(child, parent):
    return os.path.normcase(child).startswith(os.path.normcase(parent.rstrip('/\\') + os.sep))


def _read_tool(root, script_id):
    if not isinstance(script_id, string_types) or not _ID.match(script_id):
        raise ValueError('用户脚本编号无效')
    root = _path(root)
    matches = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            folder = os.path.join(root, name)
            if name.startswith('.') or not os.path.isdir(folder) or os.path.islink(folder):
                continue
            if os.path.dirname(os.path.realpath(folder)) != root:
                continue
            try:
                with io.open(os.path.join(folder, 'manifest.json'), encoding='utf-8-sig') as stream:
                    data = json.load(stream)
            except (IOError, ValueError):
                continue
            if isinstance(data, dict) and data.get('id') == script_id:
                matches.append((folder, data))
    if len(matches) != 1:
        raise ValueError('用户脚本已移除或登记不存在，请在中台检查' if not matches else '用户脚本编号重复')
    folder, data = matches[0]
    if (data.get('version') != 1 or data.get('dcc') != 'max' or data.get('removed')
            or data.get('language') not in ('python', 'maxscript')
            or not isinstance(data.get('name'), string_types) or not data['name'].strip()):
        raise ValueError('Max 用户脚本登记无效')
    payload = os.path.realpath(os.path.join(folder, 'files'))
    relative = data.get('script')
    if (os.path.dirname(payload) != folder or not isinstance(relative, string_types)
            or os.path.isabs(relative)):
        raise ValueError('工具文件夹或脚本路径无效')
    filepath = os.path.realpath(os.path.join(payload, relative))
    extension = '.py' if data['language'] == 'python' else '.ms'
    if not _inside(filepath, payload) or not os.path.isfile(filepath) or os.path.splitext(filepath)[1].lower() != extension:
        raise ValueError('脚本文件不存在或不在该工具目录内')
    data.update(filepath=filepath, directory=payload, entry=data.get('entry', ''))
    return data


def _qt():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


def _main_window():
    try:
        import qtmax
    except ImportError:
        import MaxPlus
        return MaxPlus.GetQMaxMainWindow()
    return qtmax.GetQMaxMainWindow()


def _locations():
    from pymxs import runtime as rt
    directory = os.path.join(_text(rt.getDir(rt.name('userScripts'))), 'qz_user_tools')
    startup = os.path.join(_text(rt.getDir(rt.name('userStartupScripts'))), 'qz_user_tools.ms')
    return os.path.join(directory, 'toolbar.json'), startup


def _read_config(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('tools'), list):
        raise ValueError('Qz 工具栏配置无效，请检查：' + path)
    tools, seen = [], set()
    for item in data['tools']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), string_types)
                or not _ID.match(item['id']) or item['id'] in seen
                or not isinstance(item.get('root'), string_types)
                or not isinstance(item.get('name'), string_types)):
            raise ValueError('Qz 工具栏配置条目无效：' + path)
        seen.add(item['id'])
        tools.append(item)
    return tools


def _replace(source, destination):
    if hasattr(os, 'replace'):
        os.replace(source, destination)
    elif os.name == 'nt':
        import ctypes
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        move.restype = ctypes.c_int
        if not move(source, destination, 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.rename(source, destination)


def _write(path, data):
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    descriptor, staged = tempfile.mkstemp(prefix='.qz-toolbar-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        _replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.remove(staged)


def _bytes(path):
    if not os.path.exists(path):
        return None
    with io.open(path, 'rb') as stream:
        return stream.read()


def _startup(config):
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # ASCII .ms and ASCII Python Unicode literals work under both interpreters,
    # including quotes, Chinese paths and Python2's Execute Unicode restriction.
    literal = lambda value: 'u' + json.dumps(value.replace('\\', '/'), ensure_ascii=True)
    command = ('import sys; sys.path.insert(0, %s); '
               'from max_actions.user_toolbar import restore_toolbar; restore_toolbar(%s)' %
               (literal(project), literal(config)))
    escaped = command.replace('\\', '\\\\').replace('"', '\\"')
    return (_MARKER + '\ntry (python.Execute "' + escaped + '") '
            'catch (format "[Qz user tools] startup failed: %\\n" (getCurrentException()))\n').encode('ascii')


def _persist(config, startup, tools):
    previous = _bytes(startup)
    if previous is not None and not previous.startswith(_MARKER.encode('ascii')):
        raise ValueError('同名启动文件不属于 Qz，未覆盖：' + startup)
    _write(startup, _startup(config))
    try:
        _write(config, json.dumps(dict(version=1, tools=tools), ensure_ascii=True, indent=2).encode('ascii'))
    except Exception:
        if previous is None:
            os.remove(startup)
        else:
            _write(startup, previous)
        raise


def _report(error):
    from pymxs import runtime as rt
    message = '用户脚本无法运行：' + _text(error)
    print('[Qz] ' + message)
    rt.messageBox(message, title=_TITLE)


def run_from_toolbar(root, script_id):
    """Fresh registration/source per click. No socket, worker, or middle-tier UI."""
    from max_actions.user_scripts import user_script
    try:
        tool = _read_tool(root, script_id)
        result = user_script(filepath=tool['filepath'], directory=tool['directory'],
                             entry=tool['entry'], script_id=script_id)
        if result.get('output'):
            print(result['output'], end='')
        if result.get('truncated'):
            print('[用户自定义] 脚本输出较长，已截取。')
        if not result.get('success'):
            _report(result.get('error', '用户脚本运行失败'))
        return result
    except Exception as error:
        _report(error)
        return dict(success=False, error=_text(error))


def _clicked(toolbar, config, item):
    if getattr(toolbar, '_qz_running', False):
        return
    toolbar._qz_running = True
    toolbar.setEnabled(False)
    try:
        run_from_toolbar(item['root'], item['id'])
    finally:
        toolbar._qz_running = False
        toolbar.setEnabled(True)


def _install_button_menu(toolbar, config):
    QtCore, QtWidgets = _qt()
    app = QtWidgets.QApplication.instance()
    previous = getattr(toolbar, '_qz_button_menu_filter', None)
    if previous is not None:
        app.removeEventFilter(previous)
        previous.deleteLater()

    class ButtonMenuFilter(QtCore.QObject):
        pressed = None

        def eventFilter(self, target, event):
            # Max consumes toolbar context events at application level before
            # QToolBar.contextMenuEvent. Intercept only our actual tool buttons.
            kind = event.type()
            if kind not in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                            QtCore.QEvent.ContextMenu):
                return False
            if not isinstance(target, QtWidgets.QToolButton) or not toolbar.isAncestorOf(target):
                return False
            action = target.defaultAction()
            if action is None or not _ID.match(_text(action.data())):
                return False
            if kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton:
                self.pressed = target
                event.accept()
                return True
            if kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.RightButton:
                if self.pressed is target:
                    self.pressed = None
                    menu = QtWidgets.QMenu(toolbar)
                    remove = menu.addAction('取消固定“%s”' % action.text())
                    remove.setEnabled(not getattr(toolbar, '_qz_running', False))
                    script_id = _text(action.data())
                    remove.triggered.connect(lambda checked=False: _unpin(config, script_id))
                    execute = getattr(menu, 'exec', None) or menu.exec_
                    point = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                    execute(target.mapToGlobal(point))
                    menu.deleteLater()
                event.accept()
                return True
            if kind == QtCore.QEvent.ContextMenu:
                event.accept()
                return True
            return False

    filter_object = ButtonMenuFilter(toolbar)
    toolbar._qz_button_menu_filter = filter_object
    app.installEventFilter(filter_object)


def _render(config, tools, reveal=True):
    QtCore, QtWidgets = _qt()
    main = _main_window()
    if main is None:
        raise RuntimeError('Max 主窗口不可用，无法创建工具栏')
    toolbar = main.findChild(QtWidgets.QToolBar, _OBJECT)
    if toolbar is None:
        class UserToolbar(QtWidgets.QToolBar):
            def contextMenuEvent(self, event):
                menu = QtWidgets.QMenu(self)
                action = self.actionAt(event.pos())
                if action is not None:
                    remove = menu.addAction('取消固定“%s”' % action.text())
                    remove.triggered.connect(lambda checked=False: _unpin(config, action.data()))
                menu.addAction(self.toggleViewAction())
                execute = getattr(menu, 'exec', None) or menu.exec_
                execute(event.globalPos())
        toolbar = UserToolbar(_TITLE, main)
        toolbar.setObjectName(_OBJECT)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        toolbar.setFloatable(True)
        main.addToolBar(QtCore.Qt.BottomToolBarArea, toolbar)
        # Retain the Python subclass across action-library module reloads.
        main._qz_user_toolbar = toolbar
    if getattr(toolbar, '_qz_running', False):
        raise RuntimeError('工具栏脚本正在执行，请完成后再修改固定按钮')
    _install_button_menu(toolbar, config)
    toolbar.clear()
    for item in tools:
        label = item['name']
        try:
            label = _read_tool(item['root'], item['id'])['name']
        except (IOError, OSError, ValueError):
            pass  # Keep removed tools visible; click explains instead of running stale code.
        action = toolbar.addAction(label)
        action.setData(item['id'])
        action.setToolTip('运行用户脚本：' + label)
        action.triggered.connect(lambda checked=False, tool=dict(item): _clicked(toolbar, config, tool))
    if reveal:
        toolbar.setVisible(bool(tools))
    return toolbar


def _unpin(config, script_id):
    try:
        previous = _read_config(config)
        tools = [item for item in previous if item['id'] != script_id]
        _render(config, tools)
        try:
            _write(config, json.dumps(dict(version=1, tools=tools), ensure_ascii=True, indent=2).encode('ascii'))
        except Exception:
            _render(config, previous)
            raise
    except Exception as error:
        _report(error)


def restore_toolbar(config):
    try:
        config = _path(config)
        tools = _read_config(config)
        if tools:
            return _render(config, tools)
    except Exception as error:
        # Startup failures must not open modal dialogs or interrupt Max startup.
        print('[Qz user tools] ' + _text(error))
    return None


@register_action('user_script_toolbar')
def user_script_toolbar(op='pin', root=None, script_id=''):
    if op == 'info':
        return dict(version=2)
    if op != 'pin':
        raise ValueError('未知工具栏操作')
    tool = _read_tool(root, script_id)
    config, startup = _locations()
    previous = _read_config(config)
    updated = any(item['id'] == script_id for item in previous)
    item = dict(root=_path(root), id=script_id, name=tool['name'])
    tools = [item if entry['id'] == script_id else entry for entry in previous]
    if not updated:
        tools.append(item)
    # Verify the UI before writing persistent files; restore UI if IO fails.
    toolbar = _render(config, tools)
    try:
        _persist(config, startup, tools)
    except Exception:
        _render(config, previous)
        raise
    return dict(toolbar=_TITLE, name=tool['name'], updated=updated, saved=config, startup=startup)
