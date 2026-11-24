"""中台协调 Maya -> Max；数值数组经临时文件传递，JSON 只携带描述。"""
from pathlib import Path
import tempfile
import time

import numpy as np

from ui.skin_weights import BLOCK_VALUES, _timed


def decode_name(name):
    return name.replace("FBXASC032", " ")


def prepare_transfer(directory, meshes, timings, decode_names=True):
    directory = Path(directory)
    ready, errors = [], []
    origin = "Maya" if decode_names else "Max"
    convert_name = decode_name if decode_names else lambda value: value
    names = [convert_name(mesh["name"]) for mesh in meshes]
    for mesh, name in zip(meshes, names):
        try:
            if names.count(name) != 1:
                raise ValueError(f"多个 {origin} 模型同名：{name}")
            method = mesh.get("skinning_method")
            if type(method) is not int or method not in (0, 1, 2):
                raise ValueError(f"缺少有效的 {origin} 蒙皮方式，请重载动作库后重试")
            index, vertices, width = mesh["index"], mesh["vertices"], mesh["influences"]
            if vertices < 1 or width < 1 or len(mesh["bone_names"]) != width:
                raise ValueError(f"{origin} 权重描述尺寸不匹配")
            source, dq_path = directory / f"mesh-{index}.weights", directory / f"mesh-{index}.dq"
            if source.stat().st_size != vertices * width * 8 or dq_path.stat().st_size != vertices * 8:
                raise ValueError("权重或 DQ 文件长度不匹配")
            dq = _timed(timings, "host_file_read", np.fromfile, dq_path, dtype="<f8")
            if not (np.isfinite(dq) & (dq >= 0) & (dq <= 1)).all():
                raise ValueError("DQ 权重包含非有限数或超出 0～1 范围")
            used, total = np.zeros(width, dtype=bool), 0
            batch = max(1, min(2048, BLOCK_VALUES // width))
            with source.open("rb") as src, \
                    (directory / f"mesh-{index}.offsets").open("wb") as offsets_file, \
                    (directory / f"mesh-{index}.cols").open("wb") as cols_file, \
                    (directory / f"mesh-{index}.values").open("wb") as values_file:
                _timed(timings, "host_file_write", np.array([0], dtype="<u4").tofile, offsets_file)
                for start in range(0, vertices, batch):
                    size = min(batch, vertices - start)
                    weights = _timed(timings, "host_file_read", np.fromfile, src,
                                     dtype="<f8", count=size * width).reshape(size, width)
                    mask = weights > 0
                    valid = np.isfinite(weights).all(axis=1) & (weights >= 0).all(axis=1) & mask.any(axis=1)
                    if not valid.all():
                        sample = (np.flatnonzero(~valid)[:12] + start).tolist()
                        raise ValueError(f"存在全零、负值或非有限权重，跳过整个模型；异常顶点示例：{sample}")
                    used |= mask.any(axis=0)
                    columns = np.nonzero(mask)[1].astype("<u4")
                    offsets = np.cumsum(mask.sum(axis=1), dtype=np.uint64) + total
                    total = int(offsets[-1])
                    if total > 4294967295:
                        raise ValueError("单个模型的非零权重数量超过交换格式上限")
                    _timed(timings, "host_file_write", offsets.astype("<u4").tofile, offsets_file)
                    _timed(timings, "host_file_write", columns.tofile, cols_file)
                    _timed(timings, "host_file_write", weights[mask].astype("<f8").tofile, values_file)
            bones = [dict(index=int(column), name=convert_name(mesh["bone_names"][column]))
                     for column in np.flatnonzero(used)]
            bone_names = [bone["name"] for bone in bones]
            if len(set(bone_names)) != len(bone_names):
                raise ValueError("有权重的骨骼解码后重名，无法唯一匹配")
            ready.append(dict(index=index, mesh=mesh["mesh"], name=name,
                              vertices=vertices, influences=width, nonzero=total, bones=bones,
                              skinning_method=method))
        except Exception as exc:
            errors.append(dict(mesh=mesh["mesh"], error=str(exc), status="skipped"))
    return ready, errors


def _call(client, action, timings, side, **kwargs):
    started = time.perf_counter()
    response = client.call(action, **kwargs)
    elapsed = time.perf_counter() - started
    data = response.get("data", {}) if response.get("ok") else {}
    if "action_seconds" in data:
        for key, seconds in data.get("timings_seconds", {}).items():
            timings[key] = timings.get(key, 0.) + seconds
        timings["bridge_wait"] = timings.get("bridge_wait", 0.) + max(0., elapsed - data["action_seconds"])
    else:
        timings[side + "_unprofiled"] = timings.get(side + "_unprofiled", 0.) + elapsed
    return response


def run_transfer(maya_client, max_client, max_influences=4):
    started, timings = time.perf_counter(), {}
    try:
        if max_client is None or not getattr(max_client, "connected", False):
            raise RuntimeError("Max 未连接，请先在中台连接 Max")
        if not getattr(maya_client, "connected", False):
            raise RuntimeError("Maya 未连接，请先在中台连接 Maya")
        if type(max_influences) is not int or max_influences < 1:
            raise ValueError("最大影响数必须为正整数")
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-", ignore_cleanup_errors=True) as directory:
            response = _call(maya_client, "skin_weights_export_max", timings, "maya", timeout=300, directory=directory)
            if response.get("ok"):
                exported = response["data"]
                stage_started = time.perf_counter()
                io_before = timings.get("host_file_read", 0.) + timings.get("host_file_write", 0.)
                ready, errors = prepare_transfer(directory, exported.get("meshes", []), timings)
                io_elapsed = timings.get("host_file_read", 0.) + timings.get("host_file_write", 0.) - io_before
                timings["numpy_compute"] = max(0., time.perf_counter() - stage_started - io_elapsed)
                errors = list(exported.get("errors", [])) + errors
                if ready:
                    if not getattr(max_client, "connected", False):
                        raise RuntimeError("Max 已断开，未开始导入，请重新连接 Max")
                    response = _call(max_client, "skin_weights_import_max", timings, "max",
                                     timeout=305, connect_timeout=1, execution_timeout=300,
                                     directory=directory, meshes=ready, max_influences=max_influences)
                    if not response.get("ok") and "timeout" in response.get("error", "").lower():
                        response["error"] += "；超时不代表 Max 已停止导入，请等待完成后确认，勿重复发送"
                    if response.get("ok"):
                        errors.extend(response["data"].get("errors", []))
                        response["data"]["errors"] = errors
                else:
                    response = dict(ok=True, data=dict(count=0, vertices=0, reports=[], errors=errors))
                if response.get("ok") and not exported.get("meshes") and not errors:
                    response["data"]["warning"] = "Maya 没有选中可发送的蒙皮模型"
    except Exception as exc:
        response = dict(ok=False, error=str(exc))
    elapsed = time.perf_counter() - started
    timings["local_other"] = max(0., elapsed - sum(timings.values()))
    data = response["data"] if response.get("ok") else response
    data.update(operation="send_max", threshold=max_influences, elapsed_seconds=round(elapsed, 6),
                timings_seconds={key: round(value, 6) for key, value in timings.items()})
    return response


def format_transfer_messages(response):
    data = response.get("data", {}) if response.get("ok") else response
    messages = []
    if not response.get("ok"):
        messages.append(("error", f'发送到 Max 失败：{response.get("error", "未知错误")}'))
    else:
        errors, count = data.get("errors", []), data.get("count", 0)
        status = "完成" if not errors else "部分完成" if count else "未完成"
        messages.append(("info", f'发送到 Max {status}：成功 {count} 个模型，导入 {data.get("vertices", 0)} 个顶点，'
                         f'跳过或失败 {len(errors)} 个模型；Skin 最大影响数设置为 {data.get("threshold", 4)}。'))
        if data.get("warning"):
            messages.append(("warn", data["warning"]))
        for report in data.get("reports", []):
            dq_status = "已同步 DQ 权重" if report.get("dq_imported") else "未覆盖 DQ 权重"
            if report.get("skinning_method") == 0:
                dq_status = "源为线性蒙皮，保留 Max 原有 DQ 权重"
            messages.append(("info", f'{report["name"]}：导入 {report["vertices"]} 个顶点，'
                             f'新增 {report["added_bones"]} 个 Skin 影响骨骼，{dq_status}。'))
        for error in errors:
            messages.append(("warn" if error.get("status") == "skipped" else "error",
                             f'{error.get("mesh", "未知模型")}：{error["error"]}'))
    if "elapsed_seconds" in data:
        messages.append(("info", f'总耗时 {data["elapsed_seconds"]:.3f} 秒。'))
    return messages
