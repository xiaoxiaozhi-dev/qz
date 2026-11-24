# -*- coding: utf-8 -*-
"""Export selected nodes plus their complete Skin influence lists, without parents."""
from __future__ import unicode_literals

import math
import os
import time
from contextlib import contextmanager

from max_bridge.server import register_action

try:
    text_type = unicode
except NameError:
    text_type = str


def _set(rt, key, value, required=True):
    result = rt.FBXExporterSetParam(key, value)
    if str(result).lower() in ('unsupplied', 'undefined', 'false'):
        if required:
            raise RuntimeError('FBX parameter unsupported: ' + key)
        return False
    return True


def _verify(rt, key, expected):
    actual = rt.FBXExporterGetParam(key)
    matches = str(actual).lower() == str(expected).lower()
    if not matches:
        raise RuntimeError('FBX parameter did not take effect: {0} = {1}'.format(key, actual))


def _skin_nodes(rt, skin):
    # List IDs are contiguous, but bone system IDs can have holes after removal.
    ids = [int(rt.skinOps.GetBoneIDByListID(skin, index))
           for index in range(1, int(rt.skinOps.GetNumberBones(skin)) + 1)]
    references = {}
    for target in rt.refs.dependsOn(skin):
        if rt.isValidNode(target):
            references.setdefault(text_type(target.name), {})[int(rt.getHandleByAnim(target))] = target
    result = []
    for index in ids:
        name = text_type(rt.skinOps.GetBoneName(skin, index, 0))
        candidates = list(references.get(name, {}).values())
        if not candidates:
            candidates = list(rt.getNodeByName(name, exact=True, ignoreCase=False, all=True) or [])
        if len(candidates) != 1:
            raise ValueError('Skin 影响节点无法唯一定位：{0}（找到 {1} 个）'.format(name, len(candidates)))
        result.append(candidates[0])
    return result


@contextmanager
def _export_selection(rt):
    original = list(rt.selection)
    previous_mode = rt.getCommandPanelTaskMode()
    previous_modifier = rt.modPanel.getCurrentObject() if previous_mode == rt.Name('modify') else None
    previous_level = rt.subObjectLevel  # undefined/None outside Modify mode
    nodes = list(original)
    selected = set(int(rt.getHandleByAnim(node)) for node in original)
    influences, skins, added = set(), 0, []
    try:
        # Inspect only the explicitly selected nodes. Do not walk parents,
        # children, controllers, or other meshes sharing the same skeleton.
        for node in original:
            for skin in getattr(node, 'modifiers', []):
                if rt.classOf(skin) != rt.Skin:
                    continue
                skins += 1
                if list(rt.selection) != [node]:
                    rt.select(node)
                if rt.getCommandPanelTaskMode() != rt.Name('modify'):
                    rt.setCommandPanelTaskMode(rt.Name('modify'))
                rt.modPanel.setCurrentObject(skin, node=node)
                for influence in _skin_nodes(rt, skin):
                    handle = int(rt.getHandleByAnim(influence))
                    influences.add(handle)
                    if handle not in selected:
                        selected.add(handle)
                        nodes.append(influence)
                        added.append(text_type(influence.name))
        if list(rt.selection) != nodes:
            rt.select(rt.Array(*nodes))
        actual = set(int(rt.getHandleByAnim(node)) for node in rt.selection)
        if actual != selected:
            raise RuntimeError('部分 Skin 影响节点未能加入导出选择，已停止导出')
        yield dict(skin_count=skins, skin_influence_count=len(influences),
                   added_influence_count=len(added), added_influence_names=added,
                   exported_count=len(nodes))
    finally:
        valid = [node for node in original if rt.isValidNode(node)]
        if list(rt.selection) != valid:
            rt.select(rt.Array(*valid))
        if rt.getCommandPanelTaskMode() != previous_mode:
            rt.setCommandPanelTaskMode(previous_mode)
        if previous_modifier is not None and rt.modPanel.getCurrentObject() != previous_modifier:
            rt.modPanel.setCurrentObject(previous_modifier)
        if previous_level is not None and rt.subObjectLevel != previous_level:
            rt.subObjectLevel = previous_level


@register_action('fbx_selection_info')
def selection_info():
    """Read-only summary for checking the selection before a diagnostic export."""
    from pymxs import runtime as rt
    return dict(nodes=[dict(name=text_type(node.name), handle=int(rt.getHandleByAnim(node)),
                            skins=sum(1 for modifier in getattr(node, 'modifiers', [])
                                      if rt.classOf(modifier) == rt.Skin))
                       for node in rt.selection])


@register_action('fbx_export_maya_preflight')
def export_preflight():
    return dict(exporter_version=2, settings_policy='animation_only')


@register_action('fbx_export_maya')
def export_maya(filepath, animation=True):
    from pymxs import runtime as rt
    if not isinstance(animation, bool):
        raise ValueError('Animation must be boolean')
    if not rt.selection.count:
        raise ValueError('Max 没有选中对象')
    filepath = os.path.abspath(filepath)
    if not filepath.lower().endswith('.fbx') or not os.path.isdir(os.path.dirname(filepath)):
        raise ValueError('Invalid FBX output path')
    if os.path.exists(filepath):
        raise ValueError('FBX 输出文件已经存在，停止覆盖')
    started = time.time()
    units = dict(millimeters='mm', centimeters='cm', meters='m',
                 kilometers='km', inches='in', feet='ft', miles='mi')
    kind = text_type(rt.units.SystemType).lower().lstrip('#')
    if kind not in units:
        raise ValueError('Unsupported Max system unit: ' + kind)
    warnings = []
    if animation:
        first, last = float(rt.animationRange.start.frame), float(rt.animationRange.end.frame)
        if any(math.isnan(value) or math.isinf(value) for value in (first, last)) or first > last:
            raise ValueError('Invalid Max animation range')
        start, end = int(math.floor(first)), int(math.ceil(last))
        if (start, end) != (first, last):
            warnings.append('Max 范围包含子帧，FBX 烘焙范围已向外取整')
    rt.pluginManager.loadClass(rt.FbxExporter)
    reset = rt.FBXExporterSetParam('ResetExport')
    if str(reset).lower() in ('unsupplied', 'undefined', 'false'):
        raise RuntimeError('无法重置 FBX 导出设置')
    # Keep the user's parameter-based workflow. Export settings stay applied.
    params = [('SmoothingGroups', True),
              ('TangentSpaceExport', True),
              ('Triangulate', False),
              ('Skin', True), ('Shape', False), ('Animation', animation),
              ('BakeAnimation', animation),
              ('BakeResampleAnimation', animation),
              ('FilterKeyReducer', False), ('Removesinglekeys', False),
              ('ExportAnimationOnly', False), ('Cameras', False), ('Lights', False),
              ('EmbedTextures', False), ('PointCache', False), ('UpAxis', 'Z'),
              ('AxisConversionMethod', 'Animation'), ('ScaleFactor', 1.0),
              ('ConvertUnit', units[kind])]
    if animation:
        params.extend([('BakeFrameStart', start),
                       ('BakeFrameEnd', end), ('BakeFrameStep', 1)])
    for key, value in params:
        _set(rt, key, value)
    for key in ('PreserveEdgeOrientation', 'SmoothMeshExport', 'Preserveinstances',
                'SelectionSetExport', 'GeomAsBone', 'NormalsPerPoly', 'Convert2Tiff',
                'GenerateLog', 'ShowWarnings'):
        if not _set(rt, key, False, required=False):
            warnings.append('当前 FBX 插件不支持可选参数：' + key)
    for key, value in params:
        if key in ('Skin', 'Animation', 'BakeAnimation', 'BakeResampleAnimation', 'UpAxis', 'ConvertUnit'):
            _verify(rt, key, value)
    count = rt.selection.count
    names = [text_type(node.name) for node in rt.selection]
    with _export_selection(rt) as selection_report:
        result = rt.exportFile(filepath, rt.Name('noPrompt'), selectedOnly=True, using=rt.FBXEXP)
    if not result or not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        raise RuntimeError('Max FBX 导出失败或生成了空文件')
    report = dict(filepath=filepath, count=count, names=names,
                  warnings=warnings, elapsed_seconds=time.time() - started)
    report.update(selection_report)
    return report
