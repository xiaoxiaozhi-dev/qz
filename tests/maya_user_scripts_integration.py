"""Run via tests/run_maya_native.py in a separate batch process, never the interactive scene."""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    from maya import cmds, mel
    if not cmds.about(batch=True):
        raise RuntimeError('Requires a separate Maya batch process')
    from maya_actions.user_scripts import user_script
    from ui import user_scripts as store
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix='qz-user-script-native-') as folder:
        folder = Path(folder)
        script = folder / 'native.py'
        script.write_text('from maya import cmds\nprint("MAYA=" + cmds.about(version=True))\n'
                          'def entry(): print("ENTRY_OK")\n', encoding='utf-8')
        python = user_script(filepath=str(script), script_id='user_' + '1' * 32)
        assert python['success'] and 'MAYA=' in python['output'], python
        entry = user_script(filepath=str(script), entry='entry', script_id='user_' + '1' * 32)
        assert entry['success'] and 'ENTRY_OK' in entry['output'], entry
        script = folder / 'native.mel'
        script.write_text('global string $qzUserScriptNative;\n'
                          'global proc qzUserScriptEntry() { global string $qzUserScriptNative; '
                          '$qzUserScriptNative = "MEL_OK"; }\n', encoding='utf-8')
        result = user_script(filepath=str(script), entry='qzUserScriptEntry', script_id='user_' + '2' * 32)
        assert result['success'], result
        assert mel.eval('global string $qzUserScriptNative; string $qzNativeReadback; '
                        '$qzNativeReadback = $qzUserScriptNative;') == 'MEL_OK'
        code = 'import maya.cmds as cmds\n\ndef create_cone():\n    cmds.polyCone()\n'
        managed = folder / 'managed'
        with patch('paths.user_plugins_dir', return_value=str(managed)):
            meshes = set(cmds.ls(type='mesh', long=True))
            tool = store.add_code('创建圆锥', code, store.suggest_entry(code))
            assert Path(tool['folder']).name == '创建圆锥'
            assert set(cmds.ls(type='mesh', long=True)) == meshes, 'Saving code executed it'
            cone = user_script(filepath=tool['filepath'], directory=tool['directory'],
                               entry=tool['entry'], script_id=tool['id'])
            assert cone['success'], cone
            created = set(cmds.ls(type='mesh', long=True)) - meshes
            assert len(created) == 1, created
            shape = next(iter(created))
            vertices = cmds.polyEvaluate(shape, vertex=True)
            assert vertices > 0
            cmds.delete(cmds.listRelatives(shape, parent=True, fullPath=True))
            store.remove_script(tool['id'])
            assert not Path(tool['folder']).exists()
            mel_tool = store.add_code('中文MEL工具', 'global proc qzPastedNativeEntry() { '
                'global string $qzPastedNative; $qzPastedNative = "PASTED_MEL_OK"; }',
                'qzPastedNativeEntry', 'mel')
            pasted_mel = user_script(filepath=mel_tool['filepath'], directory=mel_tool['directory'],
                                    entry=mel_tool['entry'], script_id=mel_tool['id'])
            assert pasted_mel['success'], pasted_mel
            assert mel.eval('global string $qzPastedNative; string $qzPastedReadback; '
                            '$qzPastedReadback = $qzPastedNative;') == 'PASTED_MEL_OK'
            store.remove_script(mel_tool['id'])
            assert store.scan_scripts() == ([], [])
        return dict(ok=True, maya=cmds.about(version=True), python=python, entry=entry, mel=result,
                    pasted_cone=dict(ok=True, vertices=vertices, chinese_path=True, removed=True),
                    pasted_mel=pasted_mel)


if __name__ == '__main__':
    try:
        import maya.standalone
        maya.standalone.initialize(name='python')
        result = run()
    except BaseException:
        result = dict(ok=False, error=traceback.format_exc())
    Path(os.environ['QZ_USER_SCRIPTS_RESULT']).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(0 if result['ok'] else 1)
