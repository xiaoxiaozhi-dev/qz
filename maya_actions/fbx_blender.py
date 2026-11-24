"""Maya actions for exchanging selected geometry and rigs with Blender."""
from maya_bridge import register_action
from maya_actions.fbx_from_max import _load_plugin, receive_max
from maya_actions.fbx_to_max import _export_selection


@register_action('fbx_maya_blender_preflight')
def preflight():
    import maya.cmds as cmds

    _load_plugin(cmds)
    return dict(ready=True)


@register_action('fbx_export_blender')
def export_blender(filepath):
    return _export_selection(filepath, animation=False, include_descendants=True)


@register_action('fbx_receive_blender')
def receive_blender(filepath):
    import uuid

    return receive_max(filepath, namespace='blender_' + uuid.uuid4().hex[:10],
                       animation=False, optimize_display=False)
