"""Only a separate 3dsmaxbatch process; disposable meshes and scenes."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import struct
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUTPUT = os.environ['QZ_VERTEX_TEST_OUTPUT']


def run():
    from pymxs import runtime as rt
    from max_actions.vertex_positions import import_positions, preflight
    rt.resetMaxFile(rt.Name('noPrompt'))
    directory = tempfile.mkdtemp(prefix='qz-vertex-transfer-', dir=OUTPUT)
    report = dict(ready=preflight(), python=sys.version, cases=[])
    parent = rt.Dummy(name='Parent')
    parent.pos = rt.Point3(10, -30, 70)
    rt.rotate(parent, rt.eulerAngles(13, -22, 31))
    parent.scale = rt.Point3(1.2, 1.4, .8)
    anchor = rt.Dummy(name='OriginalSelection')
    expected = [(100.25, -20.5, 5), (-2, 7.125, 9), (80.5, 6, 10.5), (3.25, -9.75, .125)]

    def write(name, index=0, points=None):
        points = expected if points is None else points
        values = [value for point in points for value in point]
        with open(os.path.join(directory, 'mesh-%d.points' % index), 'wb') as stream:
            stream.write(struct.pack('<%dd' % len(values), *values))
        return dict(index=index, name=name, mesh='|group|' + name, vertices=len(points))

    def operations(obj):
        return rt.polyop if rt.isKindOf(obj.baseObject, rt.Editable_Poly) else rt.meshop

    def raw(obj):
        return [tuple(point) for point in operations(obj).getVerts(obj.baseObject, rt.Name('all'))]

    def check_world(obj, points):
        actual = [point * obj.objectTransform for point in operations(obj).getVerts(obj.baseObject, rt.Name('all'))]
        assert len(actual) == len(points)
        for point, wanted in zip(actual, points):
            assert max(abs(float(a)-b) for a, b in zip(point, wanted)) < .001, (list(point), wanted)

    for kind in ['mesh', 'poly', 'skin']:
        obj = rt.mesh(name='Target_' + kind,
                      vertices=rt.Array(rt.Point3(0,0,0),rt.Point3(1,0,0),rt.Point3(1,1,0),rt.Point3(0,1,0)),
                      faces=rt.Array(rt.Point3(1,2,3),rt.Point3(1,3,4)))
        if kind != 'mesh':
            rt.convertToPoly(obj)
        obj.parent = parent
        obj.pos = rt.Point3(40, 50, -60)
        rt.rotate(obj, rt.eulerAngles(21, 33, -17))
        obj.scale = rt.Point3(2, 3, -.75)
        obj.objectOffsetPos = rt.Point3(3, -2, 1)
        if kind == 'skin':
            bone = rt.boneSys.createBone(rt.Point3(0,0,0), rt.Point3(0,0,10), rt.Point3(1,0,0))
            skin = rt.Skin()
            rt.addModifier(obj, skin)
            rt.select(obj)
            rt.setCommandPanelTaskMode(rt.Name('modify'))
            rt.modPanel.setCurrentObject(skin)
            rt.skinOps.addBone(skin, bone, 1)
        modifiers = list(obj.modifiers)
        tm = str(obj.objectTransform)
        rt.select(anchor)
        rt.animButtonState = True
        result = import_positions(directory, [write(obj.name)])
        assert result['count'] == 1 and not result['errors'], result
        assert list(obj.modifiers) == modifiers and str(obj.objectTransform) == tm
        assert list(rt.selection) == [anchor] and rt.animButtonState
        rt.animButtonState = False
        check_world(obj, expected)
        report['cases'].append(dict(kind=kind, result=result, base_vertices=raw(obj)))

    obj = rt.getNodeByName('Target_poly')
    old = raw(obj)
    bad = write(obj.name)
    bad['vertices'] = 3
    assert import_positions(directory, [bad])['count'] == 0 and raw(obj) == old
    alias = rt.instance(obj)
    alias.name = 'InstanceAlias'
    assert import_positions(directory, [write(obj.name)])['count'] == 0 and raw(obj) == old
    rt.delete(alias)
    obj.scale = rt.Point3(0, 1, 1)
    degenerate = import_positions(directory, [write(obj.name)])
    # Max may clamp zero scale to a tiny nonzero value: accept only accurate
    # world positions, or an explicit rejection with the old base untouched.
    if degenerate['count']:
        check_world(obj, expected)
    else:
        assert raw(obj) == old
    report['zero_scale'] = dict(result=degenerate, transform=str(obj.objectTransform))

    large = rt.plane(name='Large', lengthsegs=100, widthsegs=100)
    rt.convertToPoly(large)
    count = int(rt.polyop.getNumVerts(large.baseObject))
    points = [(float(i % 97), float(i % 53), float(i % 13)*.125) for i in range(count)]
    entry = write(large.name, 1, points)
    started = time.time()
    result = import_positions(directory, [entry])
    elapsed = time.time() - started
    assert result['count'] == 1 and not result['errors'], result
    check_world(large, points)
    report.update(ok=True, large_vertices=count, large_seconds=elapsed, exchange=directory)
    return report


try:
    result = run()
except Exception:
    result = dict(ok=False, error=traceback.format_exc())
with io.open(os.path.join(OUTPUT, 'max-vertex-result.json'), 'w', encoding='utf-8') as stream:
    payload = json.dumps(result, ensure_ascii=True, indent=2)
    stream.write(payload if isinstance(payload, type(u'')) else payload.decode('ascii'))
print('MAX_VERTEX_POSITIONS_RESULT: ' + str(result['ok']))
