"""Coordinate Maya world points -> Max base meshes using existing clients."""
from pathlib import Path
import shutil
import tempfile
import time

import paths
from ui.fbx_transfer import _call


def _compatible(data, side):
    if side == 'maya':
        return (data.get('version') == 2 and data.get('space') == 'world'
                and data.get('positions') == 'skin_input_or_evaluated'
                and data.get('scale_policy') == 'local_scale_one')
    return (data.get('version') == 1 and data.get('space') == 'world'
            and data.get('target') == 'base_mesh')


def _ready(client, side):
    action = 'vertex_positions_{}_preflight'.format(side)
    kwargs = dict(timeout=30, connect_timeout=2)
    if side == 'max':
        kwargs['execution_timeout'] = 25
    def read():
        try:
            return _call(client, action, **kwargs)
        except RuntimeError as exc:
            if 'unknown action' not in str(exc):
                raise
            return {}
    ready = read()
    if not _compatible(ready, side):
        _call(client, 'reload_actions', **kwargs)
        ready = read()
    if not _compatible(ready, side):
        raise RuntimeError('{} 顶点位置动作未更新，请检查加载目录：{}'.format(side, ready.get('path', '未知')))


def run_transfer(maya_client, max_client):
    started, directory, stage = time.perf_counter(), None, '检查连接'
    try:
        for label, client in (('Maya', maya_client), ('Max', max_client)):
            if client is None or not getattr(client, 'connected', False):
                raise RuntimeError(label + ' 未连接，请先在中台连接')
        stage = '检查顶点位置动作'
        _ready(max_client, 'max')
        _ready(maya_client, 'maya')
        root = Path(paths.local_data_dir('vertex_positions')).resolve()
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='qz-vertex-transfer-', dir=str(root)))
        stage = 'Maya 读取顶点'
        exported = _call(maya_client, 'vertex_positions_export', directory=str(directory), timeout=300, connect_timeout=2)
        errors, meshes = list(exported.get('errors', [])), []
        if not _compatible(exported, 'maya'):
            raise RuntimeError('Maya 顶点位置格式不匹配，请重载动作库')
        for mesh in exported.get('meshes', []):
            meshes.append(dict(mesh, name=mesh['name'].replace('FBXASC032', ' ')))
        if meshes:
            if not getattr(max_client, 'connected', False):
                raise RuntimeError('Max 已断开，尚未开始应用顶点位置')
            stage = 'Max 应用顶点'
            received = _call(max_client, 'vertex_positions_import', directory=str(directory), meshes=meshes,
                             timeout=305, connect_timeout=2, execution_timeout=300)
            errors.extend(received.get('errors', []))
            received['errors'] = errors
        else:
            received = dict(count=0, vertices=0, reports=[], errors=errors)
            if not errors:
                received['warning'] = 'Maya 没有选中可发送的模型'
        response = dict(ok=True, data=received)
        # Preserve the exchange on partial failure for diagnosis; no automatic retry.
        if not errors:
            try:
                if directory.resolve().parent != root or not directory.name.startswith('qz-vertex-transfer-'):
                    raise OSError('顶点临时目录超出本次交换范围')
                shutil.rmtree(directory)
            except OSError as exc:
                received['warnings'] = ['临时顶点文件未能清理：' + str(exc)]
    except Exception as exc:
        response = dict(ok=False, error='{}：{}'.format(stage, exc))
        if stage == 'Max 应用顶点':
            response['error'] += '；超时或断开不代表 Max 任务已取消，请检查结果后再发送'
    response.update(operation='vertex_positions', elapsed_seconds=round(time.perf_counter() - started, 3))
    if directory is not None and directory.exists():
        response['retained_path'] = str(directory)
    return response


def format_messages(response):
    if not response.get('ok'):
        messages = [('error', response.get('error', '发送顶点位置失败'))]
    else:
        data = response.get('data', {})
        errors = data.get('errors', [])
        messages = [('info', '顶点位置发送{}：成功 {} 个模型、{} 个顶点，跳过或失败 {} 个模型。'.format(
            '完成' if not errors else '部分完成' if data.get('count') else '未完成',
            data.get('count', 0), data.get('vertices', 0), len(errors)))]
        for report in data.get('reports', []):
            messages.append(('info', '{}：已应用 {} 个基础网格顶点。'.format(report['name'], report['vertices'])))
        for error in errors:
            messages.append(('warn' if error.get('status') == 'skipped' else 'error',
                             '{}：{}'.format(error.get('mesh', '未知模型'), error['error'])))
        if data.get('warning'):
            messages.append(('warn', data['warning']))
        messages.extend(('warn', item) for item in data.get('warnings', []))
    if response.get('retained_path'):
        messages.append(('warn', '顶点交换文件保留在：' + response['retained_path']))
    messages.append(('info', '耗时 {:.3f} 秒。'.format(response.get('elapsed_seconds', 0))))
    return messages
