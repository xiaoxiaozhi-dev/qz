"""Only in a separate mayapy/mayabatch process; creates a disposable scene."""
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    import maya.cmds as cmds
    if not cmds.about(batch=True):
        raise RuntimeError('This fixture requires a separate Maya batch process')
    from maya_actions.fbx_to_max import export_max
    output = Path(os.environ['QZ_FBX_TEST_OUTPUT'])
    folder = output / ('maya-fixture-' + str(os.getpid()))
    folder.mkdir()
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear='cm', time='30fps')
    cmds.upAxis(axis='y')
    cmds.playbackOptions(minTime=0, maxTime=10, animationStartTime=0, animationEndTime=10)
    body = cmds.polyCube(name='Body', width=2, height=3, depth=4)[0]
    cmds.xform(body, worldSpace=True, translation=(100, 300, -200))
    cmds.select(clear=True)
    bone = cmds.joint(name='Root', position=(100, 300, -200))
    unused = cmds.joint(name='ZeroWeight', position=(100, 310, -200))
    skin = cmds.skinCluster([bone, unused], body, toSelectedBones=True)[0]
    cmds.skinPercent(skin, body + '.vtx[*]', transformValue=[(bone, 1), (unused, 0)])
    cmds.setKeyframe(bone, attribute='translateX', time=0, value=100)
    cmds.setKeyframe(bone, attribute='translateX', time=10, value=110)
    cmds.currentTime(0)
    cmds.select(body, replace=True)
    original = cmds.ls(selection=True, long=True)
    static = export_max(str(folder / 'static.fbx'), animation=False)
    animated = export_max(str(folder / 'skin.fbx'))
    assert cmds.ls(selection=True, long=True) == original
    assert animated['skin_influence_count'] == 2
    samples = []
    for frame in range(11):
        cmds.currentTime(frame)
        values = cmds.xform(body + '.vtx[*]', query=True, worldSpace=True, translation=True)
        samples.append(dict(frame=frame, vertices=[values[i:i+3] for i in range(0, len(values), 3)]))
    return dict(ok=True, static=static, animated=animated, samples=samples)


if __name__ == '__main__':
    try:
        result = run()
    except Exception:
        result = dict(ok=False, error=traceback.format_exc())
    (Path(os.environ['QZ_FBX_TEST_OUTPUT']) / 'maya-send-result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if not result['ok']:
        raise RuntimeError(result['error'])
