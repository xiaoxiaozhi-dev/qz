"""Export selected Maya transforms and their Skin influences on Maya's main thread."""
import math
import os
import time

from maya_bridge import register_action


def _selection(cmds, include_descendants=False):
    original = cmds.ls(selection=True, long=True) or []
    nodes = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if not cmds.objectType(node, isAType='transform'):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if len(parents) != 1:
                raise ValueError('无法确定选中对象所属模型：' + node)
            node = parents[0]
        if node not in nodes:
            nodes.append(node)
    if not nodes:
        raise ValueError('Maya 没有选中对象')
    selected_count = len(nodes)
    if include_descendants:
        for root in list(nodes):
            descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
            if not descendants:
                continue
            for node in cmds.ls(descendants, long=True, type='transform') or []:
                if node not in nodes:
                    nodes.append(node)
    skins, bones, added = set(), set(), []
    for node in list(nodes):
        shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or []
        history = (cmds.listHistory(shapes, pruneDagObjects=True) or []) if shapes else []
        for skin in (cmds.ls(history, type='skinCluster') or []) if history else []:
            if skin in skins:
                continue
            skins.add(skin)
            for influence in cmds.skinCluster(skin, query=True, influence=True) or []:
                matches = cmds.ls(influence, long=True) or []
                if len(matches) != 1:
                    raise ValueError('蒙皮影响节点无法唯一定位：' + influence)
                bone = matches[0]
                bones.add(bone)
                if bone not in nodes:
                    nodes.append(bone)
                    added.append(bone)
    return original, nodes, dict(count=selected_count, skin_count=len(skins),
                                 skin_influence_count=len(bones), added_influence_count=len(added))


@register_action('fbx_export_max_preflight')
def export_preflight():
    return dict(exporter_version=2, settings_policy='animation_only')


@register_action('fbx_export_max')
def export_max(filepath, animation=True):
    return _export_selection(filepath, animation=animation)


def _export_selection(filepath, animation, include_descendants=False):
    import maya.cmds as cmds
    import maya.mel as mel
    started = time.perf_counter()
    if not isinstance(animation, bool):
        raise ValueError('导出动画必须为布尔值')
    filepath = os.path.abspath(filepath)
    if not filepath.lower().endswith('.fbx') or not os.path.isdir(os.path.dirname(filepath)):
        raise ValueError('无效的 FBX 输出路径')
    if os.path.exists(filepath):
        raise ValueError('FBX 输出文件已经存在，停止覆盖')
    original, nodes, report = _selection(cmds, include_descendants=include_descendants)
    if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)
    warnings = []
    if animation:
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        if not math.isfinite(start) or not math.isfinite(end) or start > end:
            raise ValueError('Maya 动画范围无效')
        if start != math.floor(start) or end != math.ceil(end):
            warnings.append('Maya 范围包含子帧，FBX 烘焙范围已向外取整')
    mel.eval('FBXResetExport;')
    options = [('FBXExportSmoothingGroups', True),
               ('FBXExportTangents', True),
               ('FBXExportTriangulate', False),
               ('FBXExportSkins', True), ('FBXExportShapes', False),
               ('FBXExportAnimationOnly', False),
               ('FBXExportBakeComplexAnimation', animation),
               ('FBXExportBakeResampleAnimation', animation),
               ('FBXExportApplyConstantKeyReducer', False),
               ('FBXExportCameras', False), ('FBXExportLights', False),
               ('FBXExportConstraints', False), ('FBXExportCacheFile', False),
               ('FBXExportEmbeddedTextures', False), ('FBXExportInputConnections', False),
               ('FBXExportIncludeChildren', False), ('FBXExportInAscii', False)]
    for command, value in options:
        mel.eval('{} -v {};'.format(command, 'true' if value else 'false'))
    # FBXExportAnimationOnly excludes geometry; this property controls animation itself.
    mel.eval('FBXProperty "Export|IncludeGrp|Animation" -v {};'.format('true' if animation else 'false'))
    mel.eval('FBXExportFileVersion -v "FBX201800";')
    mel.eval('FBXExportUpAxis {};'.format(cmds.upAxis(query=True, axis=True)))
    if animation:
        mel.eval('FBXExportBakeComplexStart -v {};'.format(math.floor(start)))
        mel.eval('FBXExportBakeComplexEnd -v {};'.format(math.ceil(end)))
        mel.eval('FBXExportBakeComplexStep -v 1;')
    try:
        cmds.select(nodes, replace=True)
        # Pass paths as data, including Unicode, quotes and backslashes.
        cmds.file(filepath, force=True, options='v=0;', type='FBX export', exportSelected=True)
    finally:
        cmds.select(original, replace=True) if original else cmds.select(clear=True)
    if not os.path.isfile(filepath) or not os.path.getsize(filepath):
        raise RuntimeError('Maya 没有生成有效 FBX 文件')
    report.update(filepath=filepath, warnings=warnings, elapsed_seconds=time.perf_counter() - started)
    return report
