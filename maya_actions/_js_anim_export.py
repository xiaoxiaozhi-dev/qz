"""Export existing-format JSON, optionally normalizing onto a body template."""
from contextlib import contextmanager
import math
import os
import re
import tempfile
import uuid

from maya_actions import _js_anim_core as core


BODIES = ('F1', 'F2', 'M1', 'M2')
CHANNELS = list(core.CHANNELS.values())
ALL_CHANNELS = CHANNELS + ['scale' + axis for axis in 'XYZ']


def identify_body(*names, body_type=None):
    """Only use explicit body prefixes; similar skeletons are not reliable IDs."""
    bodies = set()
    for name in names:
        stem = os.path.splitext(os.path.basename(str(name or '').replace('\\', '/')))[0]
        match = re.match(r'^(F1|F2|M1|M2)(?![0-9])', stem, re.IGNORECASE)
        if match:
            bodies.add(match.group(1).upper())
    if body_type is not None:
        selected = str(body_type).upper()
        if selected not in BODIES:
            raise ValueError('体型必须是 F1、F2、M1 或 M2')
        if bodies and bodies != {selected}:
            raise ValueError('指定体型与文件名前缀不一致：' + ', '.join(sorted(bodies)))
        return selected
    if len(bodies) > 1:
        raise ValueError('模板的体型前缀不一致：' + ', '.join(sorted(bodies)))
    if not bodies:
        raise ValueError('无法识别模板体型，请让模板文件名以 F1、F2、M1、M2 开头')
    return bodies.pop()


def resolve_template(path, body_type=None):
    if not path or not str(path).strip():
        return None, None
    path = os.path.abspath(os.path.expanduser(str(path).strip()))
    if not path.lower().endswith('.fbx') or not os.path.isfile(path) or not os.path.getsize(path):
        return None, None
    if body_type is not None:
        return path, identify_body(path, body_type=body_type)
    try:
        return path, identify_body(path)
    except ValueError:
        return None, None


def _key(node):
    return core.short_name(node).replace('FBXASC032', ' ')


def _mapping(cmds, nodes):
    result = {}
    for node in cmds.ls(nodes, long=True, type='transform') or []:
        name = _key(node)
        if name in result:
            raise ValueError('骨架中存在重名节点，无法确定对应关系：' + name)
        result[name] = node
    return result


def _template_root(cmds, mapping):
    members = set(mapping.values())
    roots = [node for node in mapping.values() if not any(
        parent in members for parent in (cmds.listRelatives(node, parent=True, fullPath=True) or []))]
    if len(roots) != 1:
        raise ValueError('体型模板必须有唯一的骨架根节点')
    return roots[0]


def _plan(sources, targets, body):
    common = sorted(set(sources) & set(targets), key=lambda name: targets[name].count('|'))
    missing = set(targets) - set(sources)
    forearms = []
    if body == 'F1':
        for side in ('L', 'R'):
            name = 'Bip01 ' + side + ' Forearmnub'
            if name in missing:
                needed = ['Bip01 ' + side + suffix for suffix in (' ForeTwist', ' Hand', ' UpperArm')]
                if any(node not in sources for node in needed):
                    raise ValueError('无法重建前臂，源骨架缺少：' + ', '.join(node for node in needed if node not in sources))
                forearms.append(side)
                missing.remove(name)
    # These are fixed template wrapper nodes, not missing animated influences.
    missing.discard('Bone_Allnub')
    if body == 'M1':
        missing.difference_update(('Point01', 'Point02', 'Point03', 'Point04'))
    if missing:
        raise ValueError('源骨架与所选体型模板不匹配，缺少节点：' + ', '.join(sorted(missing)))
    if not common or 'Bip01' not in sources:
        raise ValueError('没有找到可用的源骨架 Bip01')
    return common, forearms


def _validate_output(path, overwrite):
    if not str(path).lower().endswith('.json'):
        raise ValueError('输出文件必须使用 .json 扩展名')
    if os.path.exists(path) and not overwrite:
        raise FileExistsError('动作文件已存在：' + str(path))


@contextmanager
def _copies(cmds):
    """Own all new nodes by UUID; never identify original nodes by short name."""
    original_ids = set(cmds.ls(uuid=True) or [])
    original = dict(selection=cmds.ls(selection=True, long=True) or [],
                    time=cmds.currentTime(query=True), unit=cmds.currentUnit(query=True, time=True),
                    namespace=cmds.namespaceInfo(currentNamespace=True),
                    scene=cmds.file(query=True, sceneName=True), modified=cmds.file(query=True, modified=True),
                    auto=cmds.autoKeyframe(query=True, state=True), undo=cmds.undoInfo(query=True, state=True),
                    playback={flag: cmds.playbackOptions(query=True, **{flag: True})
                              for flag in ('minTime', 'maxTime', 'animationStartTime', 'animationEndTime')})
    namespaces = []
    errors = []
    try:
        cmds.undoInfo(stateWithoutFlush=False)
        cmds.autoKeyframe(state=False)
        cmds.namespace(setNamespace=':')
        yield namespaces
    finally:
        try:
            cmds.namespace(setNamespace=':')
            added = set(cmds.ls(uuid=True) or []) - original_ids
            nodes = cmds.ls(list(added), long=True) if added else []
            # FBX can instantiate shared USD infrastructure. It is not part of
            # our imported skeleton and must not be removed with the copies.
            owned = [node for node in nodes or [] if cmds.nodeType(node) != 'mayaUsdLayerManager']
            if owned:
                cmds.delete(owned)
            for namespace in reversed(namespaces):
                if cmds.namespace(exists=namespace):
                    cmds.namespace(removeNamespace=namespace)
        except Exception as exc:
            errors.append('清理临时节点：' + str(exc))
        try:
            # FBX native timing changes preserve seconds. Rescale existing keys
            # back when restoring the original time unit as well.
            if cmds.currentUnit(query=True, time=True) != original['unit']:
                cmds.currentUnit(time=original['unit'], updateAnimation=True)
            cmds.playbackOptions(**original['playback'])
            cmds.currentTime(original['time'], edit=True)
            cmds.select(original['selection'], replace=True) if original['selection'] else cmds.select(clear=True)
            cmds.namespace(setNamespace=original['namespace'])
            if cmds.file(query=True, sceneName=True) != original['scene']:
                cmds.file(rename=original['scene'])
            if not errors:
                cmds.file(modified=original['modified'])
        except Exception as exc:
            errors.append('恢复场景状态：' + str(exc))
        finally:
            cmds.autoKeyframe(state=original['auto'])
            cmds.undoInfo(stateWithoutFlush=original['undo'])
        if errors:
            raise RuntimeError('；'.join(errors))


def _import_fbx(cmds, mel, path, namespaces, timing):
    namespace = 'QZ_ANIM_EXPORT_' + uuid.uuid4().hex[:12]
    namespaces.append(namespace)
    cmds.namespace(setNamespace=':')
    mel.eval('FBXResetImport;')
    option = 'true' if timing else 'false'
    for command in ('FBXImportMode -v "add";', 'FBXImportCameras -v false;',
                    'FBXImportLights -v false;', 'FBXImportConstraints -v false;',
                    'FBXImportSetMayaFrameRate -v {};'.format(option),
                    'FBXImportFillTimeline -v {};'.format(option),
                    'FBXProperty "Import|IncludeGrp|Animation|ExtraGrp|TimeLineSpan" -v {};'.format(option),
                    'FBXProperty "Import|AdvOptGrp|UnitsGrp|DynamicScaleConversion" -v true;',
                    'FBXImportAxisConversionEnable -v true;',
                    'FBXImportUpAxis {};'.format(cmds.upAxis(query=True, axis=True)),
                    'FBXImportResamplingRateSource -v "File";',
                    'FBXImportSetTake -takeIndex -1;'):
        mel.eval(command)
    nodes = cmds.file(path, i=True, type='FBX', options='fbx', namespace=namespace,
                      mergeNamespacesOnClash=False, returnNewNodes=True, ignoreVersion=True,
                      importFrameRate=timing, importTimeRange='override' if timing else 'keep') or []
    mapping = _mapping(cmds, nodes)
    if not mapping or any(namespace + ':' not in node for node in mapping.values()):
        raise RuntimeError('FBX 没有载入独立的临时命名空间，已停止导出')
    return nodes, mapping, namespace


def _strip_copy_animation(cmds, nodes, targets):
    values = {node: {attr: cmds.getAttr(node + '.' + attr) for attr in ALL_CHANNELS} for node in targets}
    for node in targets:
        for attr in ['translate', 'rotate', 'scale'] + ALL_CHANNELS:
            plug = node + '.' + attr
            if cmds.connectionInfo(plug, isExactDestination=True):
                cmds.disconnectAttr(cmds.connectionInfo(plug, sourceFromDestination=True), plug)
    curves = cmds.ls(nodes, type='animCurve') or []
    if curves:
        cmds.delete(curves)
    for node, records in values.items():
        for attr, value in records.items():
            plug = node + '.' + attr
            if cmds.getAttr(plug, lock=True):
                cmds.setAttr(plug, lock=False)
            cmds.setAttr(plug, value)


def _drive(cmds, sources, targets, body, namespace):
    common, forearms = _plan(sources, targets, body)
    constraints = []
    for index, name in enumerate(common):
        constraints += cmds.parentConstraint(sources[name], targets[name], maintainOffset=False,
                                             name=namespace + ':Transfer_' + str(index))
    for side in forearms:
        forearm = targets['Bip01 ' + side + ' Forearmnub']
        constraints += cmds.pointConstraint(sources['Bip01 ' + side + ' ForeTwist'], forearm,
                                             maintainOffset=False, name=namespace + ':Elbow_' + side)
        constraints += cmds.aimConstraint(sources['Bip01 ' + side + ' Hand'], forearm,
                                           maintainOffset=False, aimVector=(1, 0, 0), upVector=(0, 0, 1),
                                           worldUpType='objectrotation',
                                           worldUpObject=sources['Bip01 ' + side + ' UpperArm'],
                                           worldUpVector=(0, 0, 1), name=namespace + ':Forearm_' + side)
    return constraints, common, forearms


def _bake(cmds, targets, start, end):
    endpoint = None
    if not math.isclose(end - start, round(end - start), abs_tol=1e-8):
        # bakeResults samples at a fixed interval; explicitly preserve an end
        # frame which does not land on that interval, as the JSON sampler does.
        cmds.currentTime(end, edit=True)
        endpoint = {node: {attr: cmds.getAttr(node + '.' + attr) for attr in CHANNELS}
                    for node in targets}
    cmds.bakeResults(targets, attribute=CHANNELS, animation='objects',
                     time=(start, end), sampleBy=1, simulation=True, minimizeRotation=True,
                     preserveOutsideKeys=False, sparseAnimCurveBake=False, disableImplicitControl=True)
    if endpoint:
        for node, attrs in endpoint.items():
            for attr, value in attrs.items():
                if attr.startswith('rotate'):
                    baked = cmds.getAttr(node + '.' + attr, time=end)
                    value += 360 * round((baked - value) / 360)
                cmds.setKeyframe(node, attribute=attr, time=end, value=value)


def _convert(cmds, mel, namespaces, sources, template, body, start, end, staged):
    if not all(math.isfinite(value) for value in (start, end)) or end < start:
        raise ValueError('源动画范围无效')
    nodes, targets, namespace = _import_fbx(cmds, mel, template, namespaces, False)
    root = _template_root(cmds, targets)
    _plan(sources, targets, body)  # Reject wrong templates before touching even the copy's channels.
    # The supplied templates contain old animation. Always retain their frame-0
    # baseline scales/wrappers, independent of the source clip's cropped start.
    cmds.currentTime(0, edit=True)
    _strip_copy_animation(cmds, nodes, list(targets.values()))
    cmds.currentTime(start, edit=True)
    cmds.namespace(setNamespace=namespace)
    constraints, common, forearms = _drive(cmds, sources, targets, body, namespace)
    _bake(cmds, list(targets.values()), start, end)
    cmds.delete([node for node in constraints if cmds.objExists(node)])
    cmds.playbackOptions(minTime=start, maxTime=end)
    result = core.export_animation(staged, root_node=root)
    data = core.read_animation(staged)
    matched, skipped = core._import_plan(cmds, data, root)
    if skipped or len(matched) != len(targets):
        raise RuntimeError('转换结果与标准模板不匹配：' + str(skipped))
    result.update(body_type=body, template_path=template, matched_nodes=len(common),
                  reconstructed_forearms=forearms, warnings=[], export_mode='template')
    return result, data


def _export(json_path, root_node=None, fbx_path=None, overwrite=False, body_type=None, template_path=None):
    _validate_output(json_path, overwrite)
    template, body = resolve_template(template_path, body_type)
    if not template and not fbx_path:
        result = core.export_animation(json_path, root_node=root_node, overwrite=overwrite)
        result.update(export_mode='regular', template_path=None, body_type=None)
        return result
    cmds = core._cmds()
    import maya.mel as mel
    if fbx_path:
        if not str(fbx_path).lower().endswith('.fbx') or not os.path.isfile(fbx_path):
            raise ValueError('FBX 文件不存在：' + str(fbx_path))
        sources = None
    else:
        root = core._root(cmds, root_node, for_export=True)
        sources = _mapping(cmds, core._hierarchy(cmds, root))
        start, end = (cmds.playbackOptions(query=True, **{flag: True}) for flag in ('minTime', 'maxTime'))
    if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)
    # Publish only after all temporary nodes have been cleaned and scene state
    # restored. Failures cannot replace an existing library animation.
    with tempfile.TemporaryDirectory(prefix='qz_anim_export_') as directory:
        with _copies(cmds) as namespaces:
            if fbx_path:
                nodes, sources, _namespace = _import_fbx(cmds, mel, fbx_path, namespaces, True)
                curves = cmds.ls(nodes, type='animCurve') or []
                times = cmds.keyframe(curves, query=True, timeChange=True) if curves else []
                if not times:
                    raise ValueError('FBX 没有动画关键帧：' + str(fbx_path))
                # Preserve the file's full take span, including held end frames.
                start = cmds.playbackOptions(query=True, animationStartTime=True)
                end = cmds.playbackOptions(query=True, animationEndTime=True)
                start, end = min(start, min(times)), max(end, max(times))
            staged = os.path.join(directory, 'animation.json')
            if template:
                result, data = _convert(cmds, mel, namespaces, sources, template, body, start, end, staged)
            else:
                # Keep the source hierarchy and local channels. Import into a
                # disposable namespace instead of replacing the user's scene.
                root = sources.get('Armature') or _template_root(cmds, sources)
                cmds.playbackOptions(minTime=start, maxTime=end)
                result = core.export_animation(staged, root_node=root)
                result.update(export_mode='regular', template_path=None, body_type=None)
                data = core.read_animation(staged)
        core._write_json_atomic(json_path, data, overwrite)
    result['json_path'] = os.path.abspath(json_path)
    return result


def export_animation(json_path, root_node=None, overwrite=False, body_type=None, template_path=None):
    return _export(json_path, root_node=root_node, overwrite=overwrite, body_type=body_type,
                   template_path=template_path)


def export_fbx(fbx_path, output_folder, overwrite=False, body_type=None, template_path=None):
    destination = os.path.join(output_folder, os.path.splitext(os.path.basename(fbx_path))[0] + '.json')
    return _export(destination, fbx_path=fbx_path, overwrite=overwrite, body_type=body_type,
                   template_path=template_path)
