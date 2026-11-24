"""ONLY a separate 3dsmaxbatch process; never run in an interactive work scene."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUTPUT = os.environ['QZ_FBX_TEST_OUTPUT']


def run():
    import pymxs
    from pymxs import runtime as rt
    from max_actions.fbx_to_maya import export_maya
    from max_actions.fbx_from_maya import receive_maya, preflight
    rt.resetMaxFile(rt.Name('noPrompt'))
    rt.units.SystemType = rt.Name('Centimeters')
    rt.units.SystemScale = 1.0
    rt.frameRate = 30
    rt.animationRange = rt.interval(0, 10)
    body = rt.box(name='Body', length=2, width=3, height=4, pos=rt.Point3(100, 200, 300))
    bone = rt.boneSys.createBone(rt.Point3(100, 200, 300), rt.Point3(100, 200, 310), rt.Point3(1, 0, 0))
    bone.name = 'Root'
    skin = rt.Skin()
    rt.addModifier(body, skin)
    rt.select(body)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(skin)
    rt.skinOps.addBone(skin, bone, 1)
    for i in range(1, rt.skinOps.GetNumberVertices(skin) + 1):
        rt.skinOps.ReplaceVertexWeights(skin, i, rt.Array(1), rt.Array(1.0))
    with pymxs.animate(True):
        with pymxs.attime(10):
            bone.pos = rt.Point3(110, 220, 330)

    def sample(node, frame):
        with pymxs.attime(frame):
            mesh = rt.snapshotAsMesh(node)
            # snapshotAsMesh already returns world-space vertices in Max2020.
            points = [rt.getVert(mesh, i) for i in range(1, rt.getNumVerts(mesh) + 1)]
            return sorted([list(p) for p in points])

    samples = [sample(body, frame) for frame in range(11)]

    def check_samples(node, cm_per_unit):
        for frame in range(11):
            actual = sample(node, frame)
            for actual_point, expected_point in zip(actual, samples[frame]):
                assert all(abs(a * cm_per_unit - b) < .001 for a, b in zip(actual_point, expected_point)), (frame, actual_point, expected_point, cm_per_unit)
    folder = os.path.join(OUTPUT, 'max-fixture')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    filepath = os.path.join(folder, 'source-%d.fbx' % os.getpid())
    export_maya(filepath)
    ready = preflight()
    # These are fixture-owned settings, not production receiver behavior.
    rt.FBXImporterSetParam('ResetImport')
    for key, value in [('AxisConversion', True), ('UpAxis', 'Z'), ('ScaleConversion', True),
                       ('ScaleFactor', 1.0), ('KeepFrameRate', True), ('FillTimeline', True)]:
        rt.FBXImporterSetParam(key, value)

    option_keys = ['AxisConversion', 'UpAxis', 'ScaleConversion', 'ScaleFactor', 'KeepFrameRate', 'FillTimeline']

    def current_options():
        return dict((key, str(rt.FBXImporterGetParam(key))) for key in option_keys)

    def receive(**kwargs):
        before = current_options()
        result = receive_maya(filepath, **kwargs)
        assert current_options() == before, (before, current_options())
        return result

    rt.resetMaxFile(rt.Name('noPrompt'))
    rt.units.SystemType = rt.Name('Centimeters')
    rt.frameRate = 24
    rt.animationRange = rt.interval(0, 100)
    old = rt.box(name='Body', pos=rt.Point3(9, 8, 7))
    old_handle = int(rt.getHandleByAnim(old))
    first = receive(animation=True)
    assert rt.isValidNode(old) and old.name == 'Body' and abs(old.pos.x - 9) < 1e-6
    assert first['nodes'] >= 2 and first['renamed_count'] >= 1
    assert abs(rt.frameRate - 30) < 1e-6
    imported = [n for n in rt.objects if int(rt.getHandleByAnim(n)) != old_handle]
    imported_body = next(n for n in imported if any(rt.classOf(m) == rt.Skin for m in n.modifiers))
    imported_skin = next(m for m in imported_body.modifiers if rt.classOf(m) == rt.Skin)
    rt.select(imported_body)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(imported_skin)
    assert rt.skinOps.GetNumberVertices(imported_skin) == 8
    check_samples(imported_body, 1.0)
    for vertex in range(1, 9):
        assert abs(rt.skinOps.GetVertexWeight(imported_skin, vertex, 1) - 1) < 1e-6
    rt.frameRate = 25
    rt.animationRange = rt.interval(5, 90)
    rt.FBXImporterSetParam('KeepFrameRate', False)
    rt.FBXImporterSetParam('FillTimeline', False)
    second = receive(animation=False)
    assert rt.frameRate == 25 and abs(rt.animationRange.start.frame - 5) < 1e-6 and abs(rt.animationRange.end.frame - 90) < 1e-6
    rt.FBXImporterSetParam('KeepFrameRate', True)
    rt.FBXImporterSetParam('FillTimeline', True)
    opened = receive(mode='open', animation=True)
    assert opened['nodes'] == len(list(rt.objects))
    assert rt.maxFileName == ''
    open_body = next(n for n in rt.objects if any(rt.classOf(m) == rt.Skin for m in n.modifiers))
    check_samples(open_body, 2.54)
    unit_reports = []
    for kind, scale, factor in [('Inches', 2.0, 5.08), ('Meters', 1.0, 100.0)]:
        rt.resetMaxFile(rt.Name('noPrompt'))
        rt.units.SystemType = rt.Name(kind)
        rt.units.SystemScale = scale
        unit_report = receive(animation=True)
        unit_body = next(n for n in rt.objects if any(rt.classOf(m) == rt.Skin for m in n.modifiers))
        check_samples(unit_body, factor)
        assert float(rt.units.SystemScale) == scale
        unit_reports.append(unit_report)
    preservation_reports = []
    for animation in [False, True]:
        for key, value in [('AxisConversion', False), ('UpAxis', 'Y'), ('ScaleConversion', False),
                           ('ScaleFactor', 2.0), ('KeepFrameRate', False), ('FillTimeline', False)]:
            rt.FBXImporterSetParam(key, value)
        before = current_options()
        untouched = receive(animation=animation)
        preservation_reports.append(dict(animation=animation, options=before, result=untouched))
    return dict(ok=True, python=sys.version, ready=ready, animated=first, static=second, opened=opened,
                source_file=filepath, imported_skin_vertices=8, sampled_frames=11, unit_reports=unit_reports,
                preservation_reports=preservation_reports)


try:
    result = run()
except Exception:
    result = dict(ok=False, error=traceback.format_exc())
with io.open(os.path.join(OUTPUT, 'max-receive-result.json'), 'w', encoding='utf-8') as stream:
    payload = json.dumps(result, ensure_ascii=True, indent=2)
    stream.write(payload if isinstance(payload, type(u'')) else payload.decode('ascii'))
print('FBX_RECEIVE_NATIVE_RESULT: ' + str(result['ok']))
