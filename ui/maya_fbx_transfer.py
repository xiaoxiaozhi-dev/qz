"""Coordinate Maya -> Max FBX with the clients owned by MainWindow."""
from pathlib import Path
import uuid
import time

import paths
from ui.fbx_transfer import _call, _ensure_exporter


def _ready(data):
    return (data.get('ready') is True and data.get('receiver_version') == 2
            and data.get('open_policy') == 'discard_unsaved'
            and data.get('scene_settings_policy') == 'existing_import_settings')


def run_transfer(maya_client, max_client, mode, animation=True):
    started, filepath, stage = time.perf_counter(), None, '检查连接'
    try:
        if not isinstance(animation, bool):
            raise ValueError('导出动画必须为布尔值')
        if mode not in ('open', 'import'):
            raise ValueError('不支持的发送方式')
        for label, client in (('Maya', maya_client), ('Max', max_client)):
            if client is None or not getattr(client, 'connected', False):
                raise RuntimeError(label + ' 未连接，请先在中台连接')
        stage = 'Max 接收预检'
        try:
            ready = _call(max_client, 'fbx_max_preflight', timeout=30, connect_timeout=2, execution_timeout=25)
        except RuntimeError as exc:
            if 'unknown action' not in str(exc):
                raise
            ready = {}
        if not _ready(ready):
            _call(max_client, 'reload_actions', timeout=30, connect_timeout=2, execution_timeout=25)
            ready = _call(max_client, 'fbx_max_preflight', timeout=30, connect_timeout=2, execution_timeout=25)
        if not _ready(ready):
            raise RuntimeError('Max 接收动作版本未更新，请检查桥接加载目录：' + ready.get('receiver_path', '未知'))
        stage = 'Maya 导出预检'
        _ensure_exporter(maya_client, 'fbx_export_max_preflight', timeout=30, connect_timeout=2)
        root = Path(paths.local_data_dir('maya_to_max'))
        root.mkdir(parents=True, exist_ok=True)
        filepath = root / ('selection-' + uuid.uuid4().hex + '.fbx')
        stage = 'Maya 导出'
        exported = _call(maya_client, 'fbx_export_max', filepath=str(filepath), animation=animation,
                         timeout=600, connect_timeout=2)
        if not filepath.is_file() or not filepath.stat().st_size:
            raise RuntimeError('Maya 没有生成有效 FBX 文件')
        stage = 'Max 接收'
        received = _call(max_client, 'fbx_receive_maya', filepath=str(filepath), mode=mode,
                         animation=animation, timeout=605, connect_timeout=2, execution_timeout=600)
        warnings = list(exported.get('warnings', [])) + list(received.get('warnings', []))
        received.update(warnings=warnings, selected_count=exported.get('count', 0),
                        skin_influence_count=exported.get('skin_influence_count', 0),
                        added_influence_count=exported.get('added_influence_count', 0))
        try:
            filepath.unlink()
        except OSError as exc:
            warnings.append('已传输，临时文件未能完全清理：' + str(exc))
        response = dict(ok=True, data=received)
    except Exception as exc:
        response = dict(ok=False, error='{}：{}'.format(stage, exc))
        if stage in ('Maya 导出', 'Max 接收'):
            response['error'] += '；任务可能仍在执行或场景已有部分变化，请检查完成情况后再发送'
    response.update(mode=mode, elapsed_seconds=round(time.perf_counter() - started, 3))
    if filepath is not None and filepath.exists():
        response['retained_path'] = str(filepath)
    return response


def format_messages(response):
    if not response.get('ok'):
        messages = [('error', response.get('error', '发送失败'))]
    else:
        data = response.get('data', {})
        messages = [('info', '{}到 Max 完成：Maya 选中 {} 个对象，Max 新增 {} 个对象。'.format(
            '打开' if response['mode'] == 'open' else '导入', data.get('selected_count', 0), data.get('nodes', 0)))]
        if data.get('skin_influence_count'):
            messages.append(('info', '蒙皮列表共 {} 个影响节点，临时追加 {} 个；Maya 原选择已恢复。'.format(
                data['skin_influence_count'], data.get('added_influence_count', 0))))
        if data.get('renamed_count'):
            messages.append(('info', '已为 {} 个新增同名对象分配唯一名称。'.format(data['renamed_count'])))
        messages.extend(('warn', message) for message in data.get('warnings', []))
    if response.get('retained_path'):
        messages.append(('warn', '交换文件保留在：' + response['retained_path']))
    messages.append(('info', '耗时 {:.3f} 秒。'.format(response.get('elapsed_seconds', 0))))
    return messages
