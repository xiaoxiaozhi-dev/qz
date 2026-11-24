"""Coordinate one Blender → Maya FBX transfer with the existing clients."""
from pathlib import Path
import tempfile
import time

import paths
from ui.fbx_transfer import _call


def _ensure_ready(client, action, capability, **params):
    timeouts = dict(timeout=30, connect_timeout=2)
    try:
        ready = _call(client, action, **timeouts, **params)
    except RuntimeError as exc:
        if f'unknown action: {action}' not in str(exc):
            raise
        ready = {}
    if ready.get(capability) is not True:
        _call(client, 'reload_actions', **timeouts)
        ready = _call(client, action, **timeouts, **params)
    if ready.get('ready') is not True or ready.get(capability) is not True:
        raise RuntimeError('FBX 动作未就绪，请检查桥接加载目录并重载动作库')


def run_transfer(blender, maya, mode, animation=False, optimize_display=False,
                 discard_unsaved=False):
    started = time.perf_counter()
    directory, stage = None, '检查连接'
    try:
        if mode not in ('open', 'import'):
            raise ValueError('不支持的发送方式')
        if not all(isinstance(value, bool) for value in (animation, optimize_display, discard_unsaved)):
            raise ValueError('动画、显示优化和打开确认必须为布尔值')
        for label, client in (('Blender', blender), ('Maya', maya)):
            if not getattr(client, 'connected', False):
                raise RuntimeError(f'{label} 未连接，请先在中台连接')
        stage = 'Maya 接收预检'
        _ensure_ready(maya, 'fbx_maya_blender_receive_preflight', 'confirm_open')
        stage = 'Blender 导出预检'
        _ensure_ready(blender, 'fbx_blender_preflight', 'animation_option', direction='export')

        stage = '准备交换文件'
        root = Path(paths.local_data_dir('blender_to_maya'))
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='send-', dir=root))
        filepath = directory / 'selection.fbx'
        stage = 'Blender 导出'
        exported = _call(blender, 'fbx_export_maya', filepath=str(filepath), animation=animation,
                         timeout=605, connect_timeout=2)
        if not filepath.is_file() or not filepath.stat().st_size:
            raise RuntimeError('Blender 没有生成有效 FBX 文件')
        stage = 'Maya 接收'
        received = _call(maya, 'fbx_receive_blender_selection', filepath=str(filepath), mode=mode,
                         animation=animation, optimize_display=optimize_display,
                         discard_unsaved=mode == 'open' and discard_unsaved,
                         timeout=605, connect_timeout=2)
        warnings = list(exported.get('warnings', [])) + list(received.get('warnings', []))
        if not received.get('cancelled'):
            try:
                filepath.unlink()
                directory.rmdir()
            except OSError as exc:
                warnings.append(f'临时文件清理失败：{exc}')
        result = dict(ok=True, data=received, selected_count=exported.get('count', 0), warnings=warnings)
    except Exception as exc:
        result = dict(ok=False, error=f'{stage}：{exc}')
        if stage == 'Maya 接收':
            if mode == 'open':
                result['error'] += '。请在 Maya 确认当前场景和接收结果后再试。'
            else:
                result['error'] += '。请检查 Maya 是否已导入部分内容后再试。'
    if directory is not None and directory.exists():
        result['retained_path'] = str(directory)
    return dict(result, mode=mode, elapsed_seconds=time.perf_counter() - started)


def format_messages(result):
    if not result.get('ok'):
        yield 'error', result['error']
    else:
        data = result['data']
        if data.get('cancelled'):
            yield 'warn', '已取消打开，' + data['reason'] + '。'
        else:
            mode = '打开' if result['mode'] == 'open' else '导入'
            yield 'info', (f'{mode}到 Maya 完成。Blender 选中 {result["selected_count"]} 个对象，'
                           f'Maya 新增 {data["objects"]} 个对象。')
            if data.get('namespace'):
                yield 'info', '命名空间：' + data['namespace']
        for warning in result['warnings']:
            yield 'warn', warning
    if result.get('retained_path'):
        yield 'warn', '交换文件保留在：' + result['retained_path']
    if 'elapsed_seconds' in result:
        yield 'info', f'耗时 {result["elapsed_seconds"]:.3f} 秒。'
