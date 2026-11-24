"""Coordinate Maya-to-Blender FBX and vertex-position operations."""
from pathlib import Path
import tempfile

import paths
from ui.fbx_transfer import _call
from ui.maya_blender_skin_transfer import _with_actions


def _fbx_preflight(client):
    params = dict(direction='import', timeout=30, connect_timeout=2)
    ready = _with_actions(client, 'fbx_blender_preflight', **params)
    if ready.get('receiver_version') != 2:
        _call(client, 'reload_actions', timeout=30, connect_timeout=2)
        ready = _call(client, 'fbx_blender_preflight', **params)
    if ready.get('ready') is not True or ready.get('receiver_version') != 2:
        raise RuntimeError('Blender FBX 接收动作版本未更新，请重启后重新连接')
    return ready['scene']


def run_transfer(maya, blender, mode, animation=False, discard_unsaved=False):
    folder, applying, stage = None, False, '检查参数和连接'
    try:
        if mode not in ('open', 'import', 'vertices') or type(animation) is not bool:
            raise ValueError('发送模式或导出动画参数无效')
        if mode == 'open' and discard_unsaved is not True:
            raise ValueError('打开需要确认丢弃未保存的 Blender 更改')
        for label, client in (('Maya', maya), ('Blender', blender)):
            if not getattr(client, 'connected', False):
                raise RuntimeError(f'{label} 未连接，请先在中台连接')
        if mode != 'vertices':
            stage = 'Blender 接收预检'
            target = _fbx_preflight(blender)

        stage = '准备交换目录'
        root = Path(paths.local_data_dir('maya_to_blender')).resolve()
        root.mkdir(parents=True, exist_ok=True)
        prefix = 'qz-vertex-transfer-' if mode == 'vertices' else 'qz-fbx-transfer-'
        folder = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
        stage = 'Maya 采集' if mode == 'vertices' else 'Maya 导出'
        if mode == 'vertices':
            source = _with_actions(maya, 'vertex_positions_export_blender', directory=str(folder),
                                   timeout=300, connect_timeout=2)
            stage = '按名称定位 Blender 目标'
            if not source.get('name'):
                raise RuntimeError('Maya 顶点动作未提供 transform 名称，请重载 Maya 动作库')
            target = _with_actions(blender, 'vertex_positions_target_maya', name=source['name'],
                                   timeout=30, connect_timeout=2)
            receive = 'vertex_positions_receive_maya'
            payload = dict(directory=str(folder), source=source, target=target)
        else:
            filepath = folder / 'selection.fbx'
            source = _with_actions(maya, 'fbx_export_to_blender', filepath=str(filepath), animation=animation,
                                   timeout=600, connect_timeout=2)
            if not filepath.is_file() or not filepath.stat().st_size:
                raise RuntimeError('Maya 没有生成有效 FBX 文件')
            receive = 'fbx_receive_maya'
            payload = dict(filepath=str(filepath), mode=mode, animation=animation,
                           scene=target, discard_unsaved=discard_unsaved)

        stage, applying = 'Blender 接收', True
        data = _call(blender, receive, timeout=605, connect_timeout=2, **payload)
    except Exception as exc:
        message = f'{stage}：{exc}'
        if applying and ('超时' in message or 'timeout' in message.lower()):
            message += '；请先确认 Blender 中的结果，再决定是否重发'
        return dict(ok=False, mode=mode, error=message, retained_path=str(folder) if folder else None)

    warnings = list(source.get('warnings', []))
    try:
        files = ('mesh-0.points', 'mesh-0.faces', 'mesh-0.edges') if mode == 'vertices' else ('selection.fbx',)
        for name in files:
            (folder / name).unlink()
        if mode != 'open':
            folder.rmdir()
    except OSError as exc:
        warnings.append(f'交换文件未能清理（{folder}）：{exc}')
    return dict(ok=True, mode=mode, data=data, warnings=warnings)


def format_messages(response):
    if not response.get('ok'):
        messages = [('error', response['error'])]
    else:
        data, mode = response['data'], response['mode']
        if mode == 'vertices':
            messages = [('info', f'已更新 Blender 网格 {data["name"]} 的 {data["vertices"]} 个基础顶点。')]
        elif mode == 'open':
            messages = [('info', f'已打开 {data["objects"]} 个对象。当前 Blender 文件：{data["opened_path"]}')]
        else:
            messages = [('info', f'已向 Blender 当前场景导入 {data["objects"]} 个对象。')]
        messages.extend(('warn', warning) for warning in response['warnings'])
    if response.get('retained_path'):
        messages.append(('warn', f'交换文件保留在：{response["retained_path"]}'))
    return messages
