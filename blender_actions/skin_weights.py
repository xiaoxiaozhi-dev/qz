"""Schedule Blender skin jobs on its main thread without changing the bridge."""
import time
import uuid

from blender_actions._skin_weight_ops import run


def skin_weights(op, token, operation=None, **params):
    import bpy

    if not isinstance(token, str) or uuid.UUID(token).hex != token:
        raise ValueError("蒙皮权重任务标识无效")
    jobs = bpy.app.driver_namespace.setdefault("_qz_skin_weights_jobs", {})
    now = time.monotonic()
    for key in [key for key, value in jobs.items() if value[0] is not None and now - value[0] > 300]:
        del jobs[key]
    if op == "status":
        return jobs[token][1] if token in jobs else dict(state="missing")
    if op != "submit":
        raise ValueError("未知蒙皮权重请求")
    if token in jobs:
        return jobs[token][1]
    if any(value[0] is None for value in jobs.values()):
        raise RuntimeError("Blender 正在处理蒙皮权重，请稍后重试")
    context = bpy.context
    window = context.window or next(iter(context.window_manager.windows), None)
    if window is None:
        raise RuntimeError("Blender 当前没有可用窗口")
    scene = window.scene.session_uid

    def execute():
        manager = bpy.context.window_manager
        if manager is not None and manager.is_interface_locked:
            return 0.05
        try:
            response = dict(state="done", data=run(operation, scene=scene, **params))
        except Exception as exc:
            response = dict(state="failed", error=str(exc))
        jobs[token] = (time.monotonic(), response)
        return None

    bpy.app.timers.register(execute)
    jobs[token] = (None, dict(state="pending"))
    return jobs[token][1]


ACTIONS = {"skin_weights_blender": skin_weights}
