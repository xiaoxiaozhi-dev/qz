"""Blender main-thread FBX actions. No names are rewritten before export."""
from pathlib import Path


def _operator(direction):
    import bpy

    operation = (bpy.ops.export_scene.fbx if direction == 'export'
                 else bpy.ops.import_scene.fbx)
    try:
        operation.get_rna_type()
    except (KeyError, RuntimeError):
        raise RuntimeError('Blender 的 FBX 功能不可用，请启用 FBX 导入导出支持')
    return operation


def _preflight(direction):
    import bpy

    _operator(direction)
    if bpy.context.view_layer is None or bpy.context.mode != 'OBJECT':
        raise RuntimeError('请先切换到 Blender 物体模式')
    if direction == 'import':
        return dict(ready=True, receiver_version=2,
                    scene=dict(path=bpy.data.filepath, scene=bpy.context.scene.as_pointer()))
    if direction == 'export':
        return dict(ready=True, animation_option=True)
    return dict(ready=True)


def _filepath(filepath):
    path = Path(filepath)
    if not path.is_absolute() or path.suffix.lower() != '.fbx':
        raise ValueError('需要绝对 FBX 文件路径')
    return path


def export_maya(filepath, animation=False):
    import bpy

    if not isinstance(animation, bool):
        raise ValueError('导出动画设置必须为布尔值')
    path = _filepath(filepath)
    operation = _operator('export')
    layer = bpy.context.view_layer
    if layer is None or bpy.context.mode != 'OBJECT':
        raise RuntimeError('请先切换到 Blender 物体模式')
    if path.exists() or not path.parent.is_dir():
        raise ValueError('FBX 导出路径无效或文件已存在')
    selected = list(bpy.context.selected_objects or [])
    if not selected:
        raise ValueError('Blender 没有选中物体')
    objects = dict.fromkeys(selected)
    for obj in selected:
        objects.update(dict.fromkeys(obj.children_recursive))
    types = {'EMPTY', 'MESH', 'ARMATURE'}
    objects = {obj: None for obj in objects if obj.type in types}
    if not objects:
        raise ValueError('选中物体中没有可导出的网格、骨架或空物体')
    for obj in list(objects):
        if obj.type != 'MESH':
            continue
        armature = obj.find_armature()
        if armature is not None:
            objects[armature] = None
        for modifier in obj.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object is not None:
                objects[modifier.object] = None
    with bpy.context.temp_override(selected_objects=list(objects)):
        result = operation(filepath=str(path), use_selection=True, object_types=types,
                           use_mesh_modifiers=False, use_triangles=False, add_leaf_bones=False,
                           bake_anim=animation, bake_anim_use_nla_strips=False,
                           bake_anim_use_all_actions=False, path_mode='AUTO')
    if 'FINISHED' not in result or not path.is_file() or not path.stat().st_size:
        raise RuntimeError('Blender 没有生成有效 FBX 文件')
    return dict(count=len(selected))


def _import_maya(operation, path, animation):
    import bpy

    before = {obj.as_pointer() for obj in bpy.context.scene.objects}
    result = operation(filepath=str(path), use_anim=animation, use_image_search=False)
    imported = sum(obj.as_pointer() not in before for obj in bpy.context.scene.objects)
    if 'FINISHED' not in result or not imported:
        raise RuntimeError('Blender 没有从 FBX 导入物体')
    return imported


def _remove_staging_data(data, previous):
    staged = set(data.user_map()) - previous
    if staged:
        data.batch_remove(ids=staged)


def _import_into_current_scene(operation, path, animation):
    import bpy

    scene, layer = bpy.context.scene, bpy.context.view_layer
    fps, fps_base = scene.render.fps, scene.render.fps_base
    selected = set(bpy.context.selected_objects)
    active = layer.objects.active
    previous = set(bpy.data.user_map())
    try:
        return _import_maya(operation, path, animation)
    except Exception:
        _remove_staging_data(bpy.data, previous)
        for obj in layer.objects:
            obj.select_set(obj in selected, view_layer=layer)
        layer.objects.active = active
        raise
    finally:
        # Blender's FBX importer assigns the FBX frame rate even with animation off.
        # Keep this scene's timebase and leave imported keys at their FBX frame numbers.
        scene.render.fps, scene.render.fps_base = fps, fps_base


def _make_open_file(operation, path, destination, animation):
    import bpy

    # A failed import or write stays in a disposable scene. The open operator
    # is reached only after a complete, standalone .blend has been written.
    previous = set(bpy.data.user_map())
    stage = bpy.data.scenes.new('Maya')
    try:
        with bpy.context.temp_override(scene=stage, view_layer=stage.view_layers[0]):
            imported = _import_maya(operation, path, animation)
        bpy.data.libraries.write(str(destination), {stage})
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError('Blender 没有生成有效的暂存 .blend')
        return imported
    finally:
        # Remove only staging data; none of the existing scene IDs are owned here.
        _remove_staging_data(bpy.data, previous)


def receive_maya(filepath, mode='import', animation=False, discard_unsaved=False, scene=None):
    import bpy

    if mode not in ('open', 'import') or type(animation) is not bool:
        raise ValueError('FBX 接收模式或动画参数无效')
    path = _filepath(filepath)
    operation = _operator('import')
    if bpy.context.view_layer is None or bpy.context.mode != 'OBJECT':
        raise RuntimeError('请先切换到 Blender 物体模式')
    if not path.is_file() or not path.stat().st_size:
        raise ValueError('FBX 文件不存在或为空')
    current = dict(path=bpy.data.filepath, scene=bpy.context.scene.as_pointer())
    if mode == 'import':
        if scene is not None and scene != current:
            raise ValueError('Blender 当前场景已变化，请重新发送')
        return dict(objects=_import_into_current_scene(operation, path, animation))
    if discard_unsaved is not True:
        raise ValueError('请先确认打开将关闭当前 Blender 文件并丢弃未保存的更改')
    if scene != current:
        raise ValueError('Blender 当前场景已变化，请重新确认打开')
    destination = path.with_suffix('.blend')
    if destination.exists():
        raise ValueError('目标 .blend 已存在，停止覆盖')
    imported = _make_open_file(operation, path, destination, animation)
    result = bpy.ops.wm.open_mainfile(filepath=str(destination), load_ui=False, use_scripts=False)
    if 'FINISHED' not in result:
        raise RuntimeError('Blender 无法打开暂存 .blend')
    return dict(objects=imported, opened_path=str(destination))


ACTIONS = {
    'fbx_blender_preflight': _preflight,
    'fbx_export_maya': export_maya,
    'fbx_receive_maya': receive_maya,
}
