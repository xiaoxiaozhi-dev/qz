"""Receive a Max FBX on Maya's main thread. Maya imports are deliberately lazy."""
import os
import uuid

from maya_bridge import register_action

RECEIVER_VERSION = 4


def _load_plugin(cmds):
    if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
        cmds.loadPlugin('fbxmaya', quiet=True)


@register_action('fbx_maya_preflight')
def preflight():
    import maya.cmds as cmds
    _load_plugin(cmds)
    return dict(ready=True, receiver_version=RECEIVER_VERSION,
                animation_take_policy='explicit',
                display_settings_version=1,
                scene_settings_policy='fbx_native',
                open_policy='discard_unsaved', receiver_path=os.path.abspath(__file__))


def _optimize_display(cmds, nodes):
    warnings = []

    def attempt(label, operation):
        try:
            return operation()
        except Exception as exc:
            warnings.append('显示优化未完成（{}）：{}'.format(label, exc))

    # Disable auto key before editing joint attributes to avoid accidental keys.
    attempt('自动关键帧', lambda: cmds.autoKeyframe(state=False))
    attempt('骨骼整体显示', lambda: cmds.jointDisplayScale(0.3))
    attempt('抗锯齿', lambda: cmds.setAttr('hardwareRenderingGlobals.multiSampleEnable', True))
    joints = attempt('查找导入骨骼', lambda: cmds.ls(nodes, long=True, type='joint')) or []

    def reset_draw_style(joint):
        attribute = joint + '.drawStyle'
        if cmds.getAttr(attribute) != 0:
            cmds.setAttr(attribute, 0)

    for joint in joints:
        attempt(joint + '.radius', lambda joint=joint: cmds.setAttr(joint + '.radius', 1))
        attempt(joint + '.drawStyle', lambda joint=joint: reset_draw_style(joint))

    def viewport():
        panels = cmds.getPanel(type='modelPanel') or []
        focused = cmds.getPanel(withFocus=True)
        visible = cmds.getPanel(visiblePanels=True) or []
        panel = next((item for item in [focused, 'modelPanel4'] + visible + panels
                      if item in panels), None)
        if panel is None:
            warnings.append('显示优化：没有可用模型视口，已跳过视口显示设置')
            return
        cmds.modelEditor(panel, edit=True, jointXray=True, useDefaultMaterial=True,
                         wireframeOnShaded=True)

    attempt('视口', viewport)
    attempt('清空选择', lambda: cmds.select(clear=True))
    return warnings


@register_action('fbx_receive_max')
def receive_max(filepath, mode='import', namespace=None, animation=True,
                optimize_display=False):
    import maya.cmds as cmds
    import maya.mel as mel
    if mode not in ('open', 'import'):
        raise ValueError('Unsupported FBX receive mode')
    if not isinstance(animation, bool) or not isinstance(optimize_display, bool):
        raise ValueError('动画和显示优化设置必须为布尔值')
    filepath = os.path.abspath(filepath)
    if not filepath.lower().endswith('.fbx') or not os.path.isfile(filepath) or not os.path.getsize(filepath):
        raise ValueError('FBX 文件不存在或为空')
    _load_plugin(cmds)
    namespace = namespace or 'max_' + uuid.uuid4().hex[:10]
    if mode == 'import' and (not namespace.replace('_', '').isalnum() or namespace[0].isdigit()):
        raise ValueError('Invalid import namespace')
    if mode == 'import':
        base, index = namespace, 1
        while cmds.namespace(exists=namespace):
            namespace, index = '{}_{}'.format(base, index), index + 1
    if mode == 'open':
        # User explicitly requested discarding unsaved work without a prompt.
        # An untitled scene also prevents later saving over the temporary FBX.
        cmds.file(new=True, force=True)
    target_axis = cmds.upAxis(query=True, axis=True)
    animation_option = 'true' if animation else 'false'
    mel.eval('FBXResetImport;')
    for command in ('FBXImportMode -v "add";', 'FBXImportSkins -v true;',
                    'FBXImportShapes -v false;', 'FBXImportCameras -v false;',
                    'FBXImportLights -v false;', 'FBXImportConstraints -v false;',
                    'FBXProperty "Import|AdvOptGrp|UnitsGrp|DynamicScaleConversion" -v true;',
                    'FBXImportAxisConversionEnable -v true;',
                    'FBXImportUpAxis {};'.format(target_axis),
                    'FBXImportSetMayaFrameRate -v {};'.format(animation_option),
                    'FBXImportFillTimeline -v {};'.format(animation_option),
                    'FBXProperty "Import|IncludeGrp|Animation|ExtraGrp|TimeLineSpan" -v {};'.format(animation_option),
                    'FBXImportResamplingRateSource -v "File";'):
        mel.eval(command)
    # Scripted file imports do not initialize the take as the FBX dialog does.
    # Select it after ResetImport: 0 means no animation; -1 selects the last take.
    mel.eval('FBXImportSetTake -takeIndex {};'.format(-1 if animation else 0))
    kwargs = dict(i=True, type='FBX', options='fbx', ignoreVersion=True, returnNewNodes=True,
                  importFrameRate=animation, importTimeRange='override' if animation else 'keep',
                  mergeNamespacesOnClash=False, namespace=namespace if mode == 'import' else ':')
    # Paths are passed as data, never interpolated into MEL.
    nodes = cmds.file(filepath, **kwargs) or []
    if not nodes:
        raise RuntimeError('Maya 没有从 FBX 创建任何节点')
    transforms = cmds.ls(nodes, long=True, type='transform') or []
    node_set = set(transforms)
    roots = [node for node in transforms
             if not any(parent in node_set for parent in (cmds.listRelatives(node, parent=True, fullPath=True) or []))]
    if roots and not optimize_display:
        cmds.select(roots, replace=True)
    warnings = []
    if optimize_display:
        warnings.extend(_optimize_display(cmds, nodes))
    try:
        cmds.viewFit(allObjects=True)
    except Exception as exc:
        warnings.append('框选全部未完成：{}'.format(exc))
    return dict(count=len(roots), nodes=len(nodes), objects=len(set(transforms)), roots=roots, mode=mode,
                namespace=namespace if mode == 'import' else '', warnings=warnings,
                scene=dict(linear_unit=cmds.currentUnit(query=True, linear=True),
                           time_unit=cmds.currentUnit(query=True, time=True),
                           up_axis=cmds.upAxis(query=True, axis=True),
                           start=cmds.playbackOptions(query=True, minTime=True),
                           end=cmds.playbackOptions(query=True, maxTime=True)))
