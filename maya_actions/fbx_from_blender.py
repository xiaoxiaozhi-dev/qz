"""Maya receiver for the Blender send card."""
import uuid

from maya_actions.fbx_from_max import _load_plugin, receive_max
from maya_bridge import register_action


@register_action('fbx_maya_blender_receive_preflight')
def preflight():
    import maya.cmds as cmds

    _load_plugin(cmds)
    return dict(ready=True, confirm_open=True)


@register_action('fbx_receive_blender_selection')
def receive_blender(filepath, mode='import', animation=False, optimize_display=False,
                    discard_unsaved=False):
    import maya.cmds as cmds

    if not isinstance(discard_unsaved, bool):
        raise ValueError('打开确认必须为布尔值')
    if mode == 'open' and not discard_unsaved and cmds.file(query=True, anyModified=True):
        return dict(cancelled=True, reason='Maya 当前场景有未保存的修改')
    return receive_max(filepath, mode=mode, namespace='blender_' + uuid.uuid4().hex[:10],
                       animation=animation, optimize_display=optimize_display)
