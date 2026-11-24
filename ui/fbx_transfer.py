"""Coordinate a one-shot Max -> Maya FBX transfer using the injected clients."""
from pathlib import Path
import tempfile
import time

import paths

MAYA_RECEIVER_VERSION = 4


def _call(client, action, **params):
    response = client.call(action, **params)
    if not response.get('ok'):
        raise RuntimeError(response.get('error') or action + ' failed')
    return response.get('data') or {}


def _ensure_exporter(client, action, **timeouts):
    """Refresh cached DCC actions before using the animation-only interface."""
    try:
        ready = _call(client, action, **timeouts)
    except RuntimeError as exc:
        if 'unknown action' not in str(exc):
            raise
        ready = {}
    if ready.get('exporter_version') != 2 or ready.get('settings_policy') != 'animation_only':
        _call(client, 'reload_actions', **timeouts)
        ready = _call(client, action, **timeouts)
    if ready.get('exporter_version') != 2 or ready.get('settings_policy') != 'animation_only':
        raise RuntimeError('FBX 导出动作版本未更新，请重载对应 DCC 动作库')


def run_transfer(max_client, maya_client, mode, animation=True, optimize_display=True):
    started, directory, stage = time.perf_counter(), None, '检查连接'
    try:
        if mode not in ('open', 'import'):
            raise ValueError('不支持的发送方式')
        if not isinstance(animation, bool) or not isinstance(optimize_display, bool):
            raise ValueError('动画和显示优化设置必须为布尔值')
        for label, client in (('Max', max_client), ('Maya', maya_client)):
            if client is None or not getattr(client, 'connected', False):
                raise RuntimeError(label + ' 未连接，请先在中台连接')
        ready = _call(maya_client, 'fbx_maya_preflight', timeout=30, connect_timeout=2)
        if (ready.get('receiver_version') != MAYA_RECEIVER_VERSION
                or ready.get('animation_take_policy') != 'explicit'
                or ready.get('display_settings_version') != 1
                or ready.get('scene_settings_policy') != 'fbx_native'):
            # The desktop can restart while Maya still holds old action objects.
            # Refresh before exporting; never retry a scene-changing operation.
            _call(maya_client, 'reload_actions', timeout=30, connect_timeout=2)
            ready = _call(maya_client, 'fbx_maya_preflight', timeout=30, connect_timeout=2)
        if (ready.get('receiver_version') != MAYA_RECEIVER_VERSION
                or ready.get('open_policy') != 'discard_unsaved'
                or ready.get('animation_take_policy') != 'explicit'
                or ready.get('display_settings_version') != 1
                or ready.get('scene_settings_policy') != 'fbx_native'):
            raise RuntimeError('Maya 接收动作版本未更新，请检查桥接加载目录：' + ready.get('receiver_path', '未知'))
        stage = 'Max 导出预检'
        _ensure_exporter(max_client, 'fbx_export_maya_preflight',
                         timeout=30, connect_timeout=2, execution_timeout=25)
        root = Path(paths.local_data_dir('max_to_maya'))
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='send-', dir=str(root)))
        filepath = directory / 'selection.fbx'
        stage = 'Max 导出'
        exported = _call(max_client, 'fbx_export_maya', filepath=str(filepath), animation=animation,
                         timeout=605, connect_timeout=2, execution_timeout=600)
        if not filepath.is_file() or filepath.stat().st_size == 0:
            raise RuntimeError('Max 没有生成有效 FBX 文件')
        stage = 'Maya 接收'
        received = _call(maya_client, 'fbx_receive_max', filepath=str(filepath),
                         mode=mode, animation=animation,
                         optimize_display=optimize_display, timeout=600, connect_timeout=2)
        warnings = list(exported.get('warnings', [])) + list(received.get('warnings', []))
        received.update(warnings=warnings, selected_count=exported.get('count', 0),
                        skin_count=exported.get('skin_count', 0),
                        skin_influence_count=exported.get('skin_influence_count', 0),
                        added_influence_count=exported.get('added_influence_count', 0))
        if not received.get('cancelled'):
            try:
                filepath.unlink()
                directory.rmdir()
            except OSError as exc:
                warnings.append('已传输，临时文件未能完全清理：' + str(exc))
        result = dict(ok=True, data=received)
    except Exception as exc:
        result = dict(ok=False, error='{}：{}'.format(stage, exc))
        if directory is not None:
            result['retained_path'] = str(directory)
        if stage in ('Max 导出', 'Maya 接收') and any(
                token in str(exc).lower() for token in ('timeout', 'timed out', '超时', '无响应', '没有返回内容', 'empty response')):
            result['error'] += '；DCC 中的任务可能仍在执行，请确认完成情况后再发送'
        elif stage == 'Maya 接收':
            result['error'] += '；请检查 Maya 是否已有部分导入内容后再发送'
    result.update(mode=mode, elapsed_seconds=round(time.perf_counter() - started, 3))
    if directory is not None and directory.exists():
        result['retained_path'] = str(directory)
    return result


def format_messages(response):
    if not response.get('ok'):
        messages = [('error', response.get('error', '发送失败'))]
    else:
        data = response.get('data', {})
        if data.get('cancelled'):
            messages = [('warn', '已取消打开，Maya 当前场景未替换。')]
        else:
            mode = '打开' if response['mode'] == 'open' else '导入'
            messages = [('info', '{}到 Maya 完成：Max 选中 {} 个对象，Maya 新增 {} 个对象。'.format(
                mode, data.get('selected_count', 0), data.get('objects', 0)))]
            if data.get('namespace'):
                messages.append(('info', '命名空间：' + data['namespace']))
            if data.get('skin_count'):
                messages.append(('info', 'Skin 列表共 {} 个影响节点，临时追加 {} 个；未额外补父级，Max 原选择已恢复。'.format(
                    data.get('skin_influence_count', 0), data.get('added_influence_count', 0))))
        messages.extend(('warn', value) for value in data.get('warnings', []))
    if response.get('retained_path'):
        messages.append(('warn', '交换文件保留在：' + response['retained_path']))
    messages.append(('info', '耗时 {:.3f} 秒。'.format(response.get('elapsed_seconds', 0))))
    return messages
