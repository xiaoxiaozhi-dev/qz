# -*- coding: utf-8 -*-
"""Disposable Max process only; native toolbar/Python2/MAXScript/startup checks."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import sys
import traceback

OUTPUT = os.environ['QZ_RIG_NATIVE_OUTPUT']
sys.path.insert(0, os.environ['QZ_RIG_PROJECT'])


def run():
    from pymxs import runtime as rt
    from max_actions import user_toolbar as action
    assert rt.maxOps.isInNonInteractiveMode(), 'Disposable batch only'
    QtCore, QtWidgets = action._qt()
    main = action._main_window()
    assert main is not None, 'Native main window unavailable'
    config, startup = action._locations()
    # The launcher redirects all script/settings paths into its own directory.
    assert os.path.realpath(config).startswith(os.path.realpath(OUTPUT) + os.sep), config
    assert os.path.realpath(startup).startswith(os.path.realpath(OUTPUT) + os.sep), startup
    root = os.path.join(OUTPUT, "中文 工具's")
    if not os.path.isdir(root):
        os.makedirs(root)
    def save(path, text, encoding='utf-8'):
        with io.open(path, 'w', encoding=encoding) as stream:
            stream.write(text if isinstance(text, type(u'')) else text.decode('utf-8'))
    def make(number, name, language, source, entry=''):
        script_id = 'user_' + number * 32
        folder = os.path.join(root, name)
        payload = os.path.join(folder, 'files')
        if not os.path.isdir(payload):
            os.makedirs(payload)
        filename = 'main.py' if language == 'python' else 'main.ms'
        manifest = dict(version=1, id=script_id, dcc='max', name=name, language=language, script=filename, entry=entry)
        save(os.path.join(folder, 'manifest.json'), json.dumps(manifest, ensure_ascii=True))
        save(os.path.join(payload, filename), source, 'utf-8' if language == 'python' else 'utf-8-sig')
        return script_id, folder
    if os.environ.get('QZ_TOOLBAR_PHASE') == 'restore':
        bar = main.findChild(QtWidgets.QToolBar, action._OBJECT)
        automatic = bar is not None
        # Some batch modes skip startup scripts. Test the actual saved .ms too,
        # recording this distinction rather than claiming automatic GUI startup.
        if bar is None:
            rt.fileIn(startup)
            bar = main.findChild(QtWidgets.QToolBar, action._OBJECT)
        assert bar is not None and len(bar.actions()) == 2
        assert bar.actions()[0].text() == '最新名称', [a.text() for a in bar.actions()]
        bar.actions()[0].trigger()
        bar.actions()[1].trigger()
        assert rt.getNodeByName('QzToolbarPythonEntry') is not None
        assert rt.getNodeByName('QzToolbarMaxscript') is not None
        return dict(ok=True, phase='restore', python=sys.version, automatic_startup=automatic,
                    saved_ms_executed=True, buttons=2, config=config, startup=startup)
    syntax = 'print "native Python2"\n' if sys.version_info[0] == 2 else 'print("native Python3")\n'
    py, pyfolder = make('a', '原始名称', 'python', '# -*- coding: utf-8 -*-\nfrom pymxs import runtime as rt\n' + syntax +
        'def first(): rt.box(name="QzToolbarWrongEntry")\n'
        'def second(): rt.box(name="QzToolbarPythonEntry")\n', 'first')
    ms, _ = make('b', '原生工具', 'maxscript',
        'global qzToolbarNativeEntry\nfn qzToolbarNativeEntry = (sphere name:"QzToolbarMaxscript")\n', 'qzToolbarNativeEntry')
    def pin(script_id):
        return action.user_script_toolbar(root=root, script_id=script_id)
    first = pin(py)
    assert not first['updated']
    assert rt.getNodeByName('QzToolbarWrongEntry') is None
    pin(ms)
    bar = main.findChild(QtWidgets.QToolBar, action._OBJECT)
    assert len(bar.actions()) == 2
    path = os.path.join(pyfolder, 'manifest.json')
    with io.open(path, encoding='utf-8') as stream:
        data = json.load(stream)
    data.update(name='最新名称', entry='second')
    save(path, json.dumps(data, ensure_ascii=True))
    os.rename(pyfolder, os.path.join(root, '目录改名'))
    assert pin(py)['updated']
    assert len(bar.actions()) == 2 and bar.actions()[0].text() == '最新名称'
    bar.actions()[0].trigger()
    bar.actions()[1].trigger()
    assert rt.getNodeByName('QzToolbarPythonEntry') is not None
    assert rt.getNodeByName('QzToolbarWrongEntry') is None
    assert rt.getNodeByName('QzToolbarMaxscript') is not None
    # Remove only our toolbar and execute the exact persisted MXS launcher.
    main.removeToolBar(bar)
    bar.setParent(None)
    bar.deleteLater()
    QtWidgets.QApplication.processEvents()
    rt.fileIn(startup)
    restored = main.findChild(QtWidgets.QToolBar, action._OBJECT)
    assert restored is not None and len(restored.actions()) == 2
    # Exercise the actual tool-button right-click, not the private unpin helper.
    try:
        from PySide6 import QtGui
    except ImportError:
        from PySide2 import QtGui
    third, thirdfolder = make('c', '右键删除测试', 'python', 'print("not executed")\n')
    pin(third)
    QtWidgets.QApplication.processEvents()
    selected = []
    def choose():
        menu = QtWidgets.QApplication.activePopupWidget()
        if isinstance(menu, QtWidgets.QMenu):
            remove = menu.actions()[0]
            selected.append(remove.text())
            remove.trigger()
            menu.close()
        else:
            selected.append('no button menu')
    button = restored.widgetForAction(restored.actions()[-1])
    QtCore.QTimer.singleShot(100, choose)
    # Safety: close only the test's popup if an unexpected Max menu opens.
    def close_popup():
        popup = QtWidgets.QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
    QtCore.QTimer.singleShot(1500, close_popup)
    for kind, buttons in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.RightButton),
                          (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(button.rect().center()),
                                 QtCore.Qt.RightButton, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(button, event)
    assert selected == ['取消固定“右键删除测试”'], selected
    assert len(restored.actions()) == 2
    assert len(action._read_config(config)) == 2
    assert os.path.isfile(os.path.join(thirdfolder, 'files', 'main.py'))
    assert not rt.execute('globalVars.isGlobal #qzBridgeRollout'), 'No running bridge required'
    return dict(ok=True, phase='pin', python=sys.version, max_version=int(rt.maxVersion()[0]),
                buttons=2, python_entry=True, maxscript_entry=True, unicode=True,
                directory_rename=True, repin_no_duplicate=True, saved_ms_executed=True,
                bridge_not_running=True, right_click_unpin=True, unpin_preserves_script=True,
                config=config, startup=startup)


try:
    result = run()
except BaseException:
    error = traceback.format_exc()
    if not isinstance(error, type(u'')):
        error = error.decode(sys.getfilesystemencoding() or 'mbcs', 'replace')
    result = dict(ok=False, error=error)
name = 'result-restore.json' if os.environ.get('QZ_TOOLBAR_PHASE') == 'restore' else 'result.json'
with io.open(os.path.join(OUTPUT, name), 'w', encoding='utf-8') as stream:
    text = json.dumps(result, ensure_ascii=True, indent=2)
    stream.write(text if isinstance(text, type(u'')) else text.decode('ascii'))
print('MAX_USER_TOOLBAR_RESULT: ' + str(result['ok']))
