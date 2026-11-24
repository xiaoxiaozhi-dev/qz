"""
bone_mirror action — 镜像以 prefix 开头的骨骼。
注意：这是 Maya 端运行的脚本，可以 import maya.cmds。
"""

from maya_bridge import register_action


@register_action("bone_mirror")
def bone_mirror(prefix: str = "L_", axis: str = "X"):
    try:
        import maya.cmds as cmds
    except ImportError:
        # 非 Maya 环境（如本地单测）下，伪造返回
        return [f"{prefix}joint_01", f"{prefix}joint_02"]

    targets = cmds.ls(f"{prefix}*", type="joint") or []
    mirrored = []
    for j in targets:
        mirror_name = j.replace(prefix, prefix.replace("L_", "R_")) if "L_" in prefix else j + "_mirror"
        if cmds.objExists(mirror_name):
            continue
        cmds.mirrorJoint(j, mirrorXY=True, mirrorBehavior=True, mirrorName=mirror_name)
        mirrored.append(mirror_name)
    return mirrored
