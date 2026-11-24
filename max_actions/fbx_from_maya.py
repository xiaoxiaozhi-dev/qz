# -*- coding: utf-8 -*-
"""Receive FBX on the existing Max 2020/Python 2.7 main-thread worker."""
from __future__ import unicode_literals

import os

from max_bridge.server import register_action

RECEIVER_VERSION = 2
try:
    text_type = unicode
except NameError:
    text_type = str


def _load_plugin(rt):
    from max_bridge import server
    if not getattr(server, 'SUPPORTS_EXECUTION_TIMEOUT', False):
        raise RuntimeError('Max 桥接版本需要更新，请重新启动新版桥接')
    rt.pluginManager.loadClass(rt.FbxImporter)


def _set(rt, key, value):
    result = rt.FBXImporterSetParam(key, value)
    if text_type(result).lower() in ('unsupplied', 'undefined', 'false'):
        raise RuntimeError('Max FBX 导入参数不受支持：' + key)


def _settings(rt, animation):
    # Leave units, axes and timing to the user's current FBX importer settings.
    # ResetImport would indirectly overwrite those unmanaged options as well.
    params = [('Mode', rt.Name('create')), ('Skin', True), ('Shape', False),
              ('Animation', animation), ('TakeIndex', 1 if animation else 0),
              ('Cameras', False), ('Lights', False), ('PointCache', False),
              ('SmoothingGroups', True), ('ImportBoneAsDummy', False)]
    for key, value in params:
        _set(rt, key, value)
    for key in ('Mode', 'Skin', 'Animation'):
        expected = dict(params)[key]
        actual = rt.FBXImporterGetParam(key)
        if text_type(actual).lower() != text_type(expected).lower():
            raise RuntimeError('Max FBX 导入参数未生效：' + key)


@register_action('fbx_max_preflight')
def preflight():
    from pymxs import runtime as rt
    _load_plugin(rt)
    return dict(ready=True, receiver_version=RECEIVER_VERSION,
                open_policy='discard_unsaved', scene_settings_policy='existing_import_settings',
                receiver_path=os.path.abspath(__file__))


@register_action('fbx_receive_maya')
def receive_maya(filepath, mode='import', animation=True):
    from pymxs import runtime as rt
    if mode not in ('open', 'import') or not isinstance(animation, bool):
        raise ValueError('无效的 FBX 接收方式或动画设置')
    filepath = os.path.abspath(filepath)
    if not filepath.lower().endswith('.fbx') or not os.path.isfile(filepath) or not os.path.getsize(filepath):
        raise ValueError('FBX 文件不存在或为空')
    _load_plugin(rt)
    _settings(rt, animation)
    if mode == 'open':
        # Symmetric with the existing Open-to-Maya operation explicitly requested.
        if not rt.resetMaxFile(rt.Name('noPrompt')):
            raise RuntimeError('Max 未能新建场景')
        _settings(rt, animation)
    before = set(int(rt.getHandleByAnim(node)) for node in rt.objects)
    result = rt.importFile(filepath, rt.Name('noPrompt'), using=rt.FBXIMP)
    if not result:
        raise RuntimeError('Max FBX 导入失败，场景可能已部分更新')
    nodes = [node for node in rt.objects if int(rt.getHandleByAnim(node)) not in before]
    if not nodes:
        raise RuntimeError('Max 没有从 FBX 创建任何对象')
    # Add mode never updates old objects. Resolve names only on the new nodes.
    renamed, warnings = 0, []
    names = {}
    for node in rt.objects:
        key = text_type(node.name).lower()
        names[key] = names.get(key, 0) + 1
    for node in nodes:
        key = text_type(node.name).lower()
        if names[key] > 1:
            node.name = rt.uniqueName(text_type(node.name))
            names[key] -= 1
            renamed += 1
    rt.select(rt.Array(*nodes))
    return dict(nodes=len(nodes), names=[text_type(node.name) for node in nodes],
                renamed_count=renamed, mode=mode, warnings=warnings,
                scene=dict(system_type=text_type(rt.units.SystemType), system_scale=float(rt.units.SystemScale),
                           fps=float(rt.frameRate), start=float(rt.animationRange.start.frame),
                           end=float(rt.animationRange.end.frame)))
