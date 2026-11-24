# -*- coding: utf-8 -*-
"""Run run() in a disposable 3ds Max batch session."""
from __future__ import unicode_literals

import sys


def run():
    from pymxs import runtime as rt
    from max_bridge import server

    assert rt.maxOps.isInNonInteractiveMode()
    assert "rename_tool" in server.reload_actions()

    def call(op, **params):
        response = server.dispatch("rename_tool", op=op, **params)
        assert response["ok"], response
        return response["data"]

    root = rt.Dummy(name="Qz Rename Root")
    child = rt.Box(name="Qz Rename Child", parent=root)
    leaf = rt.Point(name="Qz Rename Leaf", parent=child)
    blocked = rt.Box(name="l_bone_01_geo")
    suffix_blocked = rt.Box(name="L_bone_01_geo_001")
    rt.select(rt.Array(root, child))
    assert call("rename", hierarchy=True, base="bone_", prefix="L_", suffix="_geo",
                use_prefix=True, use_suffix=True)["names"] == [
                    "L_bone_01_geo_002", "L_bone_02_geo", "L_bone_03_geo"]
    assert leaf.name == "L_bone_01_geo_002" and root.name == "L_bone_03_geo"
    assert blocked.name == "l_bone_01_geo" and suffix_blocked.name == "L_bone_01_geo_001"
    assert len(rt.selection) == 2

    rt.select(root)
    assert call("prefix", prefix="前 缀 ")["names"] == ["前 缀 L_bone_03_geo"]
    assert call("suffix", suffix=" 尾")["names"] == ["前 缀 L_bone_03_geo 尾"]
    assert call("replace", search="L_bone_03_geo", replace="")["names"] == ["前 缀  尾"]
    assert call("delete_index", index=-1)["names"] == ["前 缀  "]
    assert call("delete_index", index=0)["names"] == [" 缀  "]
    assert call("delete_index", index=99)["skipped"] == 1
    assert call("replace", search=" 缀  ", replace="")["skipped"] == 1
    assert call("replace", search="缀", replace="组")["names"] == [" 组  "]
    assert child.name == "L_bone_02_geo"

    root.name = "Qz Shared"
    leaf.name = "qz shared"
    rt.clearSelection()
    assert call("suffix", suffix="_x")["count"] == 0
    assert call("check_duplicates")["count"] == 2
    assert set(rt.getHandleByAnim(node) for node in rt.selection) == {
        rt.getHandleByAnim(root), rt.getHandleByAnim(leaf)}
    assert call("replace", search="qz shared", replace="QZ SHARED")["names"] == ["QZ SHARED_001"]
    assert call("check_duplicates")["count"] == 0 and len(rt.selection) == 0
    return {"ok": True, "python": sys.version}
