"""Independent mayapy/batch verification of tests/max_fbx_integration.py output.

Never execute this fixture inside an interactive Maya session.
QZ_FBX_TEST_OUTPUT must point to the folder containing max_result.json.
"""
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def maya_point(point):
    # Max Z-up -> Maya Y-up, using FBX's root axis conversion.
    return (point[0], point[2], -point[1])


def run():
    import maya.cmds as cmds
    if not cmds.about(batch=True):
        raise RuntimeError('This disposable fixture requires a separate Maya batch process')
    from maya_actions.fbx_from_max import receive_max
    output = Path(os.environ['QZ_FBX_TEST_OUTPUT'])
    native = json.loads((output / 'max_result.json').read_text(encoding='utf-8'))
    if not native['ok']:
        raise RuntimeError(native.get('error'))
    reports = []
    for label in ('static', 'animated'):
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear='cm', time='24fps')
        cmds.upAxis(axis='y')
        exported = native[label]
        received = receive_max(exported['filepath'], mode='open',
                               animation=(label == 'animated'))
        if cmds.upAxis(query=True, axis=True) != 'y':
            raise AssertionError('Receiver must preserve Maya Y-up')
        if cmds.currentUnit(query=True, linear=True) != 'cm':
            raise AssertionError('Receiver must preserve Maya centimeters')
        meshes = [mesh for mesh in cmds.ls(type='mesh', long=True)
                  if not cmds.getAttr(mesh + '.intermediateObject')]
        if len(meshes) != 1:
            raise AssertionError('Expected exactly one test mesh')
        body = cmds.listRelatives(meshes[0], parent=True, fullPath=True)[0]
        skin = cmds.ls(cmds.listHistory(meshes[0]), type='skinCluster') or []
        max_error, bone_error = 0.0, 0.0
        if label == 'static':
            position = cmds.xform(body, query=True, worldSpace=True, translation=True)
            max_error = max(abs(a - b) for a, b in zip(position, maya_point((100, 200, 300))))
        else:
            if len(skin) != 1:
                raise AssertionError('Expected native Skin to become one Maya skinCluster')
            joints = cmds.ls(type='joint', long=True)
            root = next(joint for joint in joints if joint.rsplit('|', 1)[-1] == 'Root')
            for sample in native['samples']:
                cmds.currentTime(sample['frame'])
                position = cmds.xform(root, query=True, worldSpace=True, translation=True)
                bone_error = max(bone_error, max(abs(a - b) for a, b in zip(position, maya_point(sample['bone']))))
                values = cmds.xform(meshes[0] + '.vtx[*]', query=True, worldSpace=True, translation=True)
                actual = sorted(tuple(values[index:index + 3]) for index in range(0, len(values), 3))
                expected = sorted(maya_point(point) for point in sample['vertices'])
                if len(actual) != len(expected):
                    raise AssertionError('Vertex count changed')
                max_error = max(max_error, max(abs(a - b) for p, q in zip(actual, expected) for a, b in zip(p, q)))
        if max_error > 1e-3 or bone_error > 1e-3:
            raise AssertionError('World-space mismatch: mesh={}, bone={}'.format(max_error, bone_error))
        reports.append(dict(case=label, received=received, mesh_error_cm=max_error,
                            bone_error_cm=bone_error, skins=len(skin)))
    return dict(ok=True, reports=reports)


if __name__ == '__main__':
    try:
        import maya.standalone
        maya.standalone.initialize(name='python')
        result = run()
    except Exception:
        result = dict(ok=False, error=traceback.format_exc())
    output = Path(os.environ['QZ_FBX_TEST_OUTPUT']) / 'maya_result.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    sys.exit(0 if result['ok'] else 1)
