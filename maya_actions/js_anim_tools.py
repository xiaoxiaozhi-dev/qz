"""JS 动作工具桥接入口；所有 Maya 操作由 server 在 Maya 主线程调度。"""

import importlib

from maya_bridge import register_action
from maya_actions import _js_anim_core as anim_json
from maya_actions import _js_anim_export as anim_export

# server 的 Reload 会重载此 action 模块，同时刷新共享实现。
importlib.reload(anim_json)
importlib.reload(anim_export)


def _core():
    return anim_json


@register_action("js_anim_selection")
def selection():
    return _core().selected_root()


@register_action("js_anim_layers")
def layers():
    return _core().list_layers()


@register_action("js_anim_import")
def import_animation(json_path, root_node=None):
    return _core().import_animation(json_path, root_node)


@register_action("js_anim_activate")
def activate(layer=None):
    return _core().activate_layer(layer)


@register_action("js_anim_delete")
def delete(layer):
    return _core().delete_layer(layer)


@register_action("js_anim_export")
def export_animation(json_path, root_node=None, overwrite=False, body_type=None, template_path=None):
    return anim_export.export_animation(json_path, root_node=root_node, overwrite=overwrite,
                                      body_type=body_type, template_path=template_path)


@register_action("js_anim_export_fbx")
def export_fbx(fbx_path, output_folder, overwrite=False, body_type=None, template_path=None):
    return anim_export.export_fbx(fbx_path, output_folder, overwrite=overwrite,
                                body_type=body_type, template_path=template_path)
