"""Pin managed user scripts to Maya shelves; launch without the middle tier."""
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from maya_bridge import register_action

_ID = re.compile(r'user_[0-9a-f]{32}\Z')
_TAG_PREFIX = 'qz/user_script/'


def _read_tool(root, script_id):
    if not isinstance(script_id, str) or not _ID.fullmatch(script_id):
        raise ValueError('用户脚本编号无效')
    root = Path(root).resolve(strict=True)
    matches = []
    for folder in root.iterdir():
        language = {'.py': 'python', '.mel': 'mel'}.get(folder.suffix.lower())
        if (language and not folder.name.startswith('.') and not folder.is_symlink()
                and folder.is_file() and 'user_' + uuid.uuid5(uuid.NAMESPACE_URL, folder.name).hex == script_id):
            return dict(id=script_id, name=folder.stem, language=language, entry='',
                        filepath=str(folder), directory=str(root))
        if folder.name.startswith('.') or not folder.is_dir() or folder.is_symlink():
            continue
        try:
            data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('id') == script_id:
            matches.append((folder, data))
    if len(matches) != 1:
        raise ValueError('用户脚本已移除或登记不存在，请在中台检查' if not matches else '用户脚本编号重复')
    folder, data = matches[0]
    if (folder.resolve().parent != root or data.get('version') != 1
            or data.get('dcc', 'maya') != 'maya' or data.get('removed')
            or data.get('language') not in ('python', 'mel')):
        raise ValueError('Maya 用户脚本登记无效')
    payload = folder / 'files'
    if payload.is_symlink() or payload.resolve().parent != folder:
        raise ValueError('工具文件夹不在该用户插件目录内')
    relative = Path(data['script'])
    filepath = (payload / relative).resolve()
    expected = '.py' if data['language'] == 'python' else '.mel'
    if (relative.is_absolute() or not filepath.is_relative_to(payload.resolve())
            or not filepath.is_file() or filepath.suffix.lower() != expected):
        raise ValueError('脚本文件不存在或不在该工具目录内')
    if not isinstance(data.get('name'), str) or not data['name'].strip():
        raise ValueError('用户脚本名称无效')
    data.update(filepath=str(filepath), directory=str(payload), entry=data.get('entry', ''))
    return data


def run_from_shelf(root, script_id):
    """Read fresh registration/source on each click; no socket or UI imports."""
    from maya import cmds
    from maya_actions.user_scripts import user_script
    try:
        tool = _read_tool(root, script_id)
        result = user_script(filepath=tool['filepath'], directory=tool['directory'],
                             entry=tool['entry'], script_id=script_id)
    except Exception as error:
        cmds.warning('用户脚本无法运行：%s' % error)
        return dict(success=False, error=str(error))
    if result.get('output'):
        print(result['output'], end='')
    if result.get('truncated'):
        print('[用户自定义] 脚本输出较长，已截取。')
    if not result.get('success'):
        cmds.warning(result.get('error', '用户脚本运行失败'))
    return result


def _command(root, script_id):
    project = str(Path(__file__).resolve().parents[1]).replace('\\', '/')
    return ('import sys; sys.path.insert(0, %s); '
            'from maya_actions.user_shelf import run_from_shelf; '
            'run_from_shelf(%s, %s)' % (ascii(project), ascii(str(root)), ascii(script_id)))


def _current_shelf():
    from maya import cmds, mel
    top = mel.eval('$qzUserShelfTop = $gShelfTopLevel')
    if not top or not cmds.shelfTabLayout(top, exists=True):
        raise RuntimeError('Maya 工具架不可用，请先显示并选中一个工具架')
    shelf = cmds.shelfTabLayout(top, query=True, selectTab=True)
    if not shelf or not cmds.shelfLayout(shelf, exists=True):
        raise RuntimeError('请先在 Maya 中选中要添加按钮的工具架')
    return shelf


def _save_shelf(shelf):
    from maya import cmds
    short_name = shelf.rsplit('|', 1)[-1]
    if not short_name or any(char in short_name for char in '/\\'):
        raise ValueError('工具架名称无效')
    directory = Path(cmds.internalVar(userShelfDir=True)).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = 'shelf_' + short_name + '.mel'
    # Keep the basename (and generated MEL procedure name) unchanged while
    # staging the complete shelf; an IO failure preserves the previous file.
    with tempfile.TemporaryDirectory(prefix='.qz-shelf-', dir=directory) as staging:
        staged = Path(staging) / filename
        # Maya appends .mel itself and returns "1" on some versions.
        saved = cmds.saveShelf(shelf, staged.with_suffix('').as_posix())
        if saved not in (True, 1, '1') or not staged.is_file():
            raise RuntimeError('Maya 未能保存当前工具架')
        os.replace(staged, directory / filename)
    return str(directory / filename)


@register_action('user_script_shelf')
def user_script_shelf(op='pin', root=None, script_id=''):
    if op == 'info':
        return dict(version=1, flat_files=True)
    if op != 'pin':
        raise ValueError('未知工具架操作')
    from maya import cmds
    tool = _read_tool(root, script_id)
    shelf = _current_shelf()
    tag = _TAG_PREFIX + script_id
    matches = [item for item in cmds.shelfLayout(shelf, query=True, childArray=True) or []
               if cmds.shelfButton(item, exists=True)
               and cmds.shelfButton(item, query=True, docTag=True) == tag]
    properties = dict(label=tool['name'], annotation='运行用户脚本：' + tool['name'],
                      imageOverlayLabel=tool['name'][:4], docTag=tag,
                      sourceType='python', command=_command(root, script_id))
    button, previous = None, None
    try:
        if matches:
            button = cmds.shelfButton(matches[0], query=True, fullPathName=True)
            previous = {key: cmds.shelfButton(button, query=True, **{key: True}) for key in properties}
            cmds.shelfButton(button, edit=True, **properties)
        else:
            button = cmds.shelfButton(parent=shelf, image='commandButton.png', **properties)
        saved = _save_shelf(shelf)
    except Exception:
        if button:
            if previous is None:
                cmds.deleteUI(button)
            else:
                cmds.shelfButton(button, edit=True, **previous)
        raise
    return dict(shelf=shelf, button=button, updated=previous is not None, saved=saved, name=tool['name'])
