"""Maya 主线程导出完整权重和每顶点 DQ 数据；不改变场景。"""
from pathlib import Path
import time

from maya_bridge import register_action
from maya_actions.skin_weights import _Mesh, _selected_meshes, _timed, _write_snapshot


@register_action("skin_weights_export_max")
def export_max(directory):
    from maya import cmds
    started, timings = time.perf_counter(), {}
    path = Path(directory).resolve()
    if not path.is_dir() or not path.name.startswith("qz-skin-transfer-"):
        raise ValueError("无效的权重发送临时目录")
    shapes, errors = _selected_meshes(cmds)
    meshes = []
    for index, shape in enumerate(shapes):
        try:
            mesh = _timed(timings, "maya_read", _Mesh, shape)
            method = cmds.getAttr(mesh.skin + ".skinningMethod")
            if type(method) is not int or method not in (0, 1, 2):
                raise ValueError("不支持的 Maya 蒙皮方式")
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if len(parents) != 1:
                raise ValueError("无法确定网格所属模型名称")
            name = parents[0].rsplit("|", 1)[-1]
            bones = [bone.fullPathName().rsplit("|", 1)[-1] for bone in mesh.influences]
            weights_file = _timed(timings, "maya_file_write", (path / f"mesh-{index}.weights").open, "wb")
            try:
                dq_file = _timed(timings, "maya_file_write", (path / f"mesh-{index}.dq").open, "wb")
                try:
                    for start in range(0, mesh.vertices, mesh.batch_size):
                        ids = list(range(start, min(mesh.vertices, start + mesh.batch_size)))
                        weights = _timed(timings, "maya_read", mesh.read, ids)
                        dq = _timed(timings, "maya_read", mesh.fn.getBlendWeights,
                                    mesh.path, mesh.component(ids))
                        if len(dq) != len(ids):
                            raise ValueError("Maya 返回的 DQ 权重数量不匹配")
                        _write_snapshot(weights_file, weights, timings)
                        _write_snapshot(dq_file, dq, timings)
                finally:
                    _timed(timings, "maya_file_write", dq_file.close)
            finally:
                _timed(timings, "maya_file_write", weights_file.close)
            meshes.append(dict(index=index, mesh=shape, name=name, vertices=mesh.vertices,
                               influences=mesh.width, bone_names=bones, skinning_method=method))
        except Exception as exc:
            errors.append(dict(mesh=shape, error=str(exc), status="skipped"))
    elapsed = time.perf_counter() - started
    timings["maya_other"] = max(0., elapsed - sum(timings.values()))
    return dict(meshes=meshes, errors=errors, action_seconds=elapsed, timings_seconds=timings)
