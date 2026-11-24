"""Disposable native Maya UI: shelf commands, persistence and live launchers."""
import json
import os
from pathlib import Path
import tempfile


def run():
    from maya import cmds, mel
    from maya_actions import user_shelf
    from ui import user_scripts as store
    from unittest.mock import patch
    assert os.environ.get('QZ_NATIVE_TOKEN'), 'Requires isolated native runner'
    with tempfile.TemporaryDirectory(prefix='qz-shelf-native-') as temporary:
        temporary = Path(temporary)
        root = temporary / 'managed'
        with patch('paths.user_plugins_dir', return_value=str(root)):
            marker = temporary / 'ran.txt'
            tool = store.add_code('中文工具', 'from pathlib import Path\n'
                'def first(): Path(%r).write_text("first")\n'
                'def second(): Path(%r).write_text("second")\n' % (str(marker), str(marker)), 'first')
            original_top = mel.eval('$qzOriginalShelfTop = $gShelfTopLevel')
            window = cmds.window('QzShelfNativeWindow')
            tabs = cmds.shelfTabLayout('QzShelfNativeTabs', parent=window)
            shelf = cmds.shelfLayout('QzShelfNativeCurrent', parent=tabs)
            other = cmds.shelfLayout('QzShelfNativeOther', parent=tabs)
            keep = cmds.shelfButton(parent=shelf, label='UserExisting', command='print("keep")', sourceType='python')
            cmds.shelfTabLayout(tabs, edit=True, selectTab=shelf)
            mel.eval('global string $gShelfTopLevel; $gShelfTopLevel = ' + json.dumps(tabs) + ';')
            try:
                pinned = user_shelf.user_script_shelf(root=str(root), script_id=tool['id'])
                assert not pinned['updated'] and not marker.exists(), pinned
                assert cmds.shelfButton(keep, exists=True)
                assert not cmds.shelfLayout(other, query=True, childArray=True)
                saved = Path(pinned['saved'])
                assert saved.is_file()
                saved_bytes = saved.read_bytes()
                assert b'run_from_shelf' in saved_bytes and b'UserExisting' in saved_bytes
                command = cmds.shelfButton(pinned['button'], query=True, command=True)
                assert cmds.shelfButton(pinned['button'], query=True, sourceType=True) == 'python'
                exec(command, {})
                assert marker.read_text() == 'first'
                store.update_script(tool['id'], name='新的名称', entry='second')
                Path(tool['folder']).rename(root / "中文 新目录's")
                again = user_shelf.user_script_shelf(root=str(root), script_id=tool['id'])
                assert again['updated'] and again['button'] == pinned['button'], (again, pinned)
                assert cmds.shelfButton(again['button'], query=True, label=True) == '新的名称'
                exec(command, {})
                assert marker.read_text() == 'second'
                mel_tool = store.add_code('MEL工具', 'global proc qzShelfNativeMel() { '
                    'global string $qzShelfNativeValue; $qzShelfNativeValue="MEL_SHELF_OK"; }',
                    'qzShelfNativeMel', 'mel')
                mel_pin = user_shelf.user_script_shelf(root=str(root), script_id=mel_tool['id'])
                exec(cmds.shelfButton(mel_pin['button'], query=True, command=True), {})
                value = mel.eval('global string $qzShelfNativeValue; string $qzShelfNativeRead=$qzShelfNativeValue;')
                assert value == 'MEL_SHELF_OK', value
                # Load the actual saved shelf MEL into another disposable shelf,
                # exercising the same serialized callback used after restart.
                restored = cmds.shelfLayout('QzShelfNativeRestored', parent=tabs)
                cmds.setParent(restored)
                mel.eval('source ' + json.dumps(saved.as_posix()) + '; shelf_QzShelfNativeCurrent;')
                buttons = cmds.shelfLayout(restored, query=True, childArray=True) or []
                restored_button = next(button for button in buttons
                    if cmds.shelfButton(button, query=True, docTag=True) == 'qz/user_script/' + tool['id'])
                marker.unlink()
                exec(cmds.shelfButton(restored_button, query=True, command=True), {})
                assert marker.read_text() == 'second'
                store.remove_script(tool['id'])
                missing = user_shelf.run_from_shelf(str(root), tool['id'])
                assert not missing['success']
                return dict(ok=True, batch=cmds.about(batch=True), current_shelf=pinned['shelf'],
                            saved=True, saved_callback_reloaded=True, python=True, mel=True, repeat_updates=True,
                            renamed_folder_and_entry=True, removal_feedback=True)
            finally:
                if window and cmds.window(window, exists=True):
                    cmds.deleteUI(window)
                mel.eval('global string $gShelfTopLevel; $gShelfTopLevel = ' + json.dumps(original_top or '') + ';')
