"""Only a separate mayapy/mayabatch process; never the interactive scene."""
import json
import os
from pathlib import Path
import sys
import struct
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    import maya.cmds as cmds
    if not cmds.about(batch=True):
        raise RuntimeError('This fixture requires a separate Maya batch process')
    from maya_actions.vertex_positions import export_positions
    cmds.file(new=True, force=True)
    reports = []
    for index, (scale, skinned, inherits) in enumerate([
            ((2.54,)*3, False, True), ((-2.54, 3, .5), False, True),
            ((2.54,)*3, True, True), ((2.54,)*3, True, False)]):
        body = cmds.polyPlane(name='Body%d' % index, subdivisionsX=1, subdivisionsY=1)[0]
        cmds.move(.25, .5, .75, body + '.vtx[0]', relative=True, objectSpace=True)
        group = cmds.group(body, name='Group%d' % index)
        cmds.setAttr(group + '.translate', 30, -20, 10)
        cmds.setAttr(group + '.rotate', 13, -9, 21)
        cmds.setAttr(group + '.scale', 1.5, .7, 2)
        cmds.setAttr(body + '.translate', 100, 20, -35)
        cmds.setAttr(body + '.rotate', 10, 25, 17)
        cmds.setAttr(body + '.rotateAxis', 5, 7, 11)
        cmds.setAttr(body + '.shear', .1, -.2, .3)
        cmds.setAttr(body + '.scalePivot', 2, 3, 4)
        cmds.setAttr(body + '.rotatePivot', -3, 1, 5)
        cmds.setAttr(body + '.scalePivotTranslate', 1, -2, 3)
        cmds.setAttr(body + '.rotatePivotTranslate', -2, 3, 1)
        cmds.setAttr(body + '.offsetParentMatrix',
                     0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 6, 7, 8, 1, type='matrix')
        cmds.setAttr(body + '.inheritsTransform', inherits)
        cmds.setAttr(body + '.scale', *scale)
        skin = None
        if skinned:
            # Upstream blendshape must be present in the skin input, while the pose is excluded.
            blend_target = cmds.duplicate(body, name='Target%d' % index)[0]
            cmds.move(0, 1.5, .5, blend_target + '.vtx[1]', relative=True, objectSpace=True)
            blend = cmds.blendShape(blend_target, body, name='Blend%d' % index)[0]
            cmds.setAttr(blend + '.weight[0]', .6)
            cmds.select(clear=True)
            joint = cmds.joint(name='Joint%d' % index, position=(1, 2, 3))
            skin = cmds.skinCluster(joint, body, toSelectedBones=True)[0]
            cmds.setAttr(joint + '.translateY', 9)
            cmds.setAttr(joint + '.rotateZ', 32)
        posed = cmds.xform(body + '.vtx[*]', query=True, worldSpace=True, translation=True)
        # Independent oracle: change only this isolated fixture, capture Maya's own result,
        # then restore BEFORE running the action. Never use this script in an interactive scene.
        try:
            if skin:
                cmds.setAttr(skin + '.envelope', 0)
            cmds.setAttr(body + '.scale', 1, 1, 1)
            expected = cmds.xform(body + '.vtx[*]', query=True, worldSpace=True, translation=True)
        finally:
            cmds.setAttr(body + '.scale', *scale)
            if skin:
                cmds.setAttr(skin + '.envelope', 1)
        assert max(abs(a-b) for a, b in zip(posed, expected)) > .1
        cmds.select(body + '.vtx[1]', replace=True)
        def snapshot():
            return dict(selection=cmds.ls(selection=True, long=True), time=cmds.currentTime(query=True),
                        matrix=cmds.getAttr(body + '.matrix'), scale=cmds.getAttr(body + '.scale'),
                        nodes=sorted(cmds.ls(long=True)), envelope=cmds.getAttr(skin + '.envelope') if skin else None,
                        world=cmds.xform(body + '.vtx[*]', query=True, worldSpace=True, translation=True))
        original = snapshot()
        directory = tempfile.mkdtemp(prefix='qz-vertex-transfer-', dir=os.environ['QZ_VERTEX_TEST_OUTPUT'])
        result = export_positions(directory)
        assert len(result['meshes']) == 1 and not result['errors'], result
        assert snapshot() == original, 'Exporter changed the source scene'
        entry = result['meshes'][0]
        actual = struct.unpack('<%dd' % (entry['vertices']*3), (Path(directory)/'mesh-0.points').read_bytes())
        error = max(abs(a-b) for a, b in zip(actual, expected))
        assert error < 1e-6, (index, error, actual, expected)
        reports.append(dict(scale=scale, skinned=skinned, inherits=inherits, max_error=error, result=result))
    return dict(ok=True, reports=reports)


if __name__ == '__main__':
    try:
        result = run()
    except Exception:
        result = dict(ok=False, error=traceback.format_exc())
    (Path(os.environ['QZ_VERTEX_TEST_OUTPUT'])/'maya-vertex-result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if not result['ok']:
        raise RuntimeError(result['error'])
