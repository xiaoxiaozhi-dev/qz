"""Read selected objects in the current Blender view layer."""


def read_selection():
    import bpy

    # Application timers can run without an editor area in their context.
    context = bpy.context
    view_layer = context.view_layer
    if view_layer is None and context.window_manager:
        windows = context.window_manager.windows
        if len(windows):
            view_layer = windows[0].view_layer
    if view_layer is None:
        raise RuntimeError("Blender 当前没有可用的视图层")
    names = sorted(obj.name for obj in view_layer.objects
                   if obj.select_get(view_layer=view_layer))
    return {"count": len(names), "names": names}


ACTIONS = {"read_selection": read_selection}
