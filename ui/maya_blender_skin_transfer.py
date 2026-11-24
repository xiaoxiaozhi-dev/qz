"""Coordinate ordinary weight transfer between the existing DCC clients."""
import tempfile

from ui.fbx_transfer import _call


def _with_actions(client, action, **params):
    try:
        return _call(client, action, **params)
    except RuntimeError as exc:
        if 'unknown action' not in str(exc).lower():
            raise
    _call(client, 'reload_actions', timeout=30, connect_timeout=2)
    return _call(client, action, **params)


def run_blender_transfer(maya_client, blender_client):
    operation, stage = 'send_blender', '检查连接'
    try:
        for client, label in ((maya_client, 'Maya'), (blender_client, 'Blender')):
            if not getattr(client, 'connected', False):
                raise RuntimeError(f'{label} 未连接，请先在中台连接 {label}')
        stage = '定位 Blender 原网格'
        target = _with_actions(blender_client, 'skin_weights_target_maya', timeout=30, connect_timeout=2)
        with tempfile.TemporaryDirectory(prefix='qz-skin-transfer-', ignore_cleanup_errors=True) as directory:
            stage = '采集 Maya 权重和几何'
            source = _with_actions(maya_client, 'skin_weights_export_blender', timeout=300,
                                   connect_timeout=2, directory=directory)
            stage = f'Blender 接收（{target["name"]}）'
            try:
                data = _call(blender_client, 'skin_weights_receive_maya', timeout=605, connect_timeout=2,
                             directory=directory, source=source, target=target)
            except RuntimeError as exc:
                if 'timeout' in str(exc).lower() or '超时' in str(exc):
                    raise RuntimeError(f'{exc}；请先确认 Blender 权重结果，再决定是否重发') from exc
                raise
        return dict(ok=True, data=dict(data, operation=operation))
    except Exception as exc:
        return dict(ok=False, operation=operation, error=f'{stage}：{exc}')


def format_blender_transfer_messages(response):
    if not response.get('ok'):
        return [('error', f'发送权重到 Blender 失败：{response["error"]}')]
    data = response['data']
    return [('info', f'已更新 Blender 网格 {data["name"]}（骨架 {data["armature"]}）的普通权重：'
                     f'{data["vertices"]} 个顶点，{data["groups"]} 个骨骼顶点组。')]
