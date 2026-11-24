"""Run ONLY in a separate 3dsmaxbatch process: creates disposable test scenes."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUTPUT = os.environ['QZ_FBX_TEST_OUTPUT']


def run():
    import pymxs
    from pymxs import runtime as rt
    from max_actions.fbx_to_maya import export_maya, export_preflight
    assert export_preflight() == dict(exporter_version=2, settings_policy='animation_only')
    case_dir = tempfile.mkdtemp(prefix='case-', dir=OUTPUT)
    # This script is for an isolated batch host, never the user's running scene.
    rt.resetMaxFile(rt.Name('noPrompt'))
    rt.units.SystemType = rt.Name('Centimeters')
    rt.units.SystemScale = 1.0
    rt.frameRate = 30
    rt.animationRange = rt.interval(0, 10)
    rt.sliderTime = 0
    body = rt.box(name='Body', length=2, width=3, height=4, pos=rt.Point3(100, 200, 300))
    rt.select(body)
    static = export_maya(os.path.join(case_dir, 'static.fbx'), animation=False)
    assert not rt.FBXExporterGetParam('Animation')
    assert not rt.FBXExporterGetParam('BakeAnimation')
    assert 'source' not in static and 'settings' not in static
    bone = rt.boneSys.createBone(rt.Point3(100, 200, 300), rt.Point3(100, 200, 310), rt.Point3(1, 0, 0))
    bone.name = 'Root'
    skin = rt.Skin()
    rt.addModifier(body, skin)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(skin)
    rt.skinOps.addBone(skin, bone, 1)
    unused = rt.boneSys.createBone(rt.Point3(120, 200, 300), rt.Point3(120, 200, 310), rt.Point3(1, 0, 0))
    unused.name = 'ZeroWeight'
    rt.skinOps.addBone(skin, unused, 1)
    for vertex in range(1, rt.skinOps.GetNumberVertices(skin) + 1):
        rt.skinOps.ReplaceVertexWeights(skin, vertex, rt.Array(1), rt.Array(1.0))
    with pymxs.animate(True):
        with pymxs.attime(10):
            bone.pos = rt.Point3(110, 220, 330)
    rt.select(body)  # Exclude both Skin entries; the exporter must append them.
    animated = export_maya(os.path.join(case_dir, 'skin.fbx'))
    assert rt.FBXExporterGetParam('Animation')
    assert rt.FBXExporterGetParam('BakeAnimation')
    assert int(rt.FBXExporterGetParam('BakeFrameStep')) == 1
    assert 'source' not in animated and 'settings' not in animated
    assert 'fbx_transfer_settings' not in sys.modules
    if animated['added_influence_count'] != 2 or list(rt.selection) != [body]:
        raise AssertionError('Skin influence selection was not expanded/restored correctly')
    samples = []
    for frame in range(11):
        with pymxs.attime(frame):
            snapshot = rt.snapshotAsMesh(body)
            points = []
            for index in range(1, rt.getNumVerts(snapshot) + 1):
                point = rt.getVert(snapshot, index) * body.objectTransform
                points.append([point.x, point.y, point.z])
            samples.append(dict(frame=frame, bone=list(bone.transform.row4), vertices=points))
    report = dict(ok=True, python=sys.version, static=static, animated=animated, samples=samples)
    return report


try:
    result = run()
except Exception:
    result = dict(ok=False, error=traceback.format_exc())
with io.open(os.path.join(OUTPUT, 'max_result.json'), 'w', encoding='utf-8') as stream:
    payload = json.dumps(result, ensure_ascii=True, indent=2)
    if not isinstance(payload, type(u'')):
        payload = payload.decode('ascii')
    stream.write(payload)
print('FBX_NATIVE_RESULT: ' + str(result['ok']))
