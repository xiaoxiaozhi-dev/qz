# -*- coding: utf-8 -*-
"""Isolated 3ds Max 2020/Python 2.7 side of the six-direction test."""
from __future__ import print_function, unicode_literals

import io
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.environ["QZ_SKIN_MATRIX_OUTPUT"]
PHASE = os.environ["QZ_SKIN_MATRIX_PHASE"]


def read(path):
    with io.open(path, encoding="utf-8") as stream:
        return json.load(stream)


def run():
    from pymxs import runtime as rt
    from max_actions import skin_weights as actions

    assert int(rt.maxVersion()[0]) == 22000
    rt.resetMaxFile(rt.Name("noPrompt"))
    rt.units.SystemType = rt.Name("Centimeters")
    sample = read(os.path.join(ROOT, "sample.json"))
    points = rt.Array(*(rt.Point3(*point) for point in sample["points"]))
    faces = rt.Array(*(rt.Point3(*(index + 1 for index in face)) for face in sample["faces"]))
    mesh = rt.mesh(name=sample["name"], vertices=points, faces=faces)
    joints = [rt.Point(name=name, pos=rt.Point3(index, 0, 0)) for index, name in enumerate(sample["bones"])]
    skin = rt.Skin()
    rt.select(mesh)
    rt.addModifier(mesh, skin)
    actions._activate(rt, mesh, skin)
    for joint in joints:
        rt.skinOps.addBone(skin, joint, 1)
    skin.enableDQ = False
    mapping = dict(zip(*reversed(actions._bones(rt, skin))))
    for vertex, row in enumerate(sample["rows"], 1):
        weights = row if PHASE == "export" else {sample["bones"][0]: 1.}
        ordered = sorted((mapping[name], value) for name, value in weights.items())
        rt.skinOps.unNormalizeVertex(skin, vertex, True)
        rt.skinOps.ReplaceVertexWeights(skin, vertex, rt.Array(*(bone for bone, _ in ordered)),
                                        rt.Array(*(value for _, value in ordered)))
        rt.skinOps.unNormalizeVertex(skin, vertex, abs(sum(weights.values()) - 1.) > 1e-6)
        rt.skinOps.setVertexDQWeight(skin, vertex, 0.)

    def selected():
        rt.select(mesh)

    def capture(directory):
        selected()
        result = actions.skin_weight_files(op="export", directory=directory)
        assert len(result["meshes"]) == 1 and not result["errors"], result
        return result["meshes"][0]

    if PHASE == "export":
        return dict(ok=True, mesh=capture(os.path.join(ROOT, "qz-skin-transfer-max-export")))
    targets = {}
    for source in ("maya", "blender"):
        directory = os.path.join(ROOT, "qz-skin-transfer-{}-max".format(source))
        entry = read(os.path.join(directory, "entry.json"))
        entry["node_handle"] = int(rt.getHandleByAnim(mesh))
        selected()
        result = actions.skin_weight_files(op="import", directory=directory, meshes=[entry])
        assert len(result["reports"]) == 1 and not result["errors"], result
        targets[source] = capture(directory)
    return dict(ok=True, targets=targets)


try:
    result = run()
except Exception:
    result = dict(ok=False, error=traceback.format_exc())
with io.open(os.path.join(ROOT, "max-{}.json".format(PHASE)), "w", encoding="utf-8") as stream:
    stream.write(unicode(json.dumps(result, ensure_ascii=True, indent=2)))
print("SKIN_MATRIX_MAX: " + str(result["ok"]))
