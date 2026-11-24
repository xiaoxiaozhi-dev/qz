"""Blender card coordination and use of the common .qzskin reader and writer."""
import json
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import zipfile

import numpy as np

import paths
from ui.skin_transfer import prepare_transfer
from ui.skin_weight_files import (
    POSITION_MATCH_DECIMALS, _unique_meshes, cache_filename, read_archive, write_archive,
)
from ui.skin_weight_mapping import interpolate_weights
from ui.skin_weights import format_log_messages, process_snapshot


ACTION = "skin_weights_blender"


class PendingJobError(RuntimeError):
    """A request may still need its exchange files."""


def _request(client, action, **params):
    return client.call(action, timeout=4, connect_timeout=1, **params)


def _host(client, operation, directory, **params):
    token = uuid.uuid4().hex

    def submit():
        try:
            return _request(client, ACTION, op="submit", token=token, operation=operation,
                            directory=str(directory), **params)
        except (OSError, RuntimeError) as exc:
            raise PendingJobError(f"无法确认 Blender 是否已接收任务：{exc}") from exc

    response = submit()
    if not response.get("ok") and f"unknown action: {ACTION}" in response.get("error", ""):
        reload_result = _request(client, "reload_actions")
        if not reload_result.get("ok"):
            raise RuntimeError(reload_result.get("error") or "无法加载 Blender 动作库")
        response = submit()
    if not response.get("ok"):
        error = response.get("error") or "Blender 蒙皮请求失败"
        if "超时" in error:
            raise PendingJobError(error)
        raise RuntimeError(error)

    deadline = time.monotonic() + 300
    data = response["data"]
    while data.get("state") == "pending":
        if time.monotonic() >= deadline:
            raise PendingJobError("等待 Blender 蒙皮任务超时，请检查宿主中的结果")
        time.sleep(.08)
        try:
            response = _request(client, ACTION, op="status", token=token)
        except (OSError, RuntimeError) as exc:
            raise PendingJobError(f"无法查询 Blender 蒙皮任务结果：{exc}") from exc
        if not response.get("ok"):
            message = response.get("error", "")
            if any(text in message for text in ("等待响应", "繁忙", "超时")):
                continue
            raise PendingJobError(message or "无法查询 Blender 蒙皮任务")
        data = response["data"]
    if data.get("state") == "done":
        return data["data"]
    if data.get("state") == "failed":
        raise RuntimeError(data["error"])
    raise PendingJobError("Blender 蒙皮任务已失效，请在宿主确认结果")


def _error(mesh, exc, status="skipped"):
    return dict(mesh=mesh, error=str(exc), status=status)


def _read_blender_archive(path, directory, target, mode):
    if not path.is_file():
        raise ValueError(f"没有找到该模型的权重文件：{path}")
    with zipfile.ZipFile(path) as archive:
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ValueError("权重文件描述过大")
        metadata = json.loads(archive.read("manifest.json"))
    if metadata.get("version") in (1, 2):
        raise ValueError("旧权重文件没有几何证明，请从源宿主重新导出 v3 文件")
    # The common reader extracts and validates geometry when requested. Blender
    # also needs that payload to prove correspondence in vertex-ID mode.
    entry = read_archive(path, directory, target, "closest_point")
    if mode == "vertex_id" and entry["vertices"] != entry["target_vertices"]:
        raise ValueError("顶点数不匹配，无法按顶点编号导入")
    prefix = Path(directory) / f'mesh-{entry["index"]}'
    if entry["skinning_method"] != 0 or np.any(np.fromfile(prefix.with_suffix(".dq"), dtype="<f8")):
        raise ValueError("文件包含 DQ 或混合蒙皮数据，Blender 无法无损导入")
    values = np.fromfile(prefix.with_suffix(".values"), dtype="<f8")
    if np.any(values > 1.) or np.any(values.astype(np.float32) == 0.):
        raise ValueError("文件包含超出 Blender 顶点组范围或精度的权重")
    flags = metadata.get("max_unnormalized_vertices", [])
    limit = metadata.get("max_bone_limit")
    if (not isinstance(flags, list) or any(type(v) is not int or not 0 <= v < entry["vertices"] for v in flags)
            or flags != sorted(set(flags))):
        raise ValueError("文件未归一化顶点记录无效")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("文件最大影响数记录无效")
    notices = []
    if limit is not None:
        notices.append(f"Max 最大影响数设置 {limit} 在 Blender 无对应属性，权重未限权")
    if flags:
        notices.append(f"{len(flags)} 个 Max 未归一化标记在 Blender 无对应属性，保留数值权重")
    return dict(entry, transfer_mode=mode, identity=target["identity"]), notices


def _export(directory, cache_dir, models, data):
    ready, errors = prepare_transfer(directory, models, {}, decode_names=False)
    data["errors"].extend(errors)
    source = {model["index"]: model for model in models}
    for entry in ready:
        try:
            path = write_archive(directory, cache_dir, entry, source[entry["index"]], source_host="blender")
            data["reports"].append(dict(name=entry["name"], vertices=entry["vertices"], path=str(path)))
        except Exception as exc:
            data["errors"].append(_error(entry["mesh"], exc, "failed"))


def _import(client, directory, cache_dir, models, data, transfer_mode):
    ready, notices = [], {}
    for target in models:
        try:
            filename = cache_filename(target["name"])
            entry, notes = _read_blender_archive(cache_dir / filename, directory, target, transfer_mode)
            ready.append(entry)
            notices[entry["name"]] = notes
        except Exception as exc:
            data["errors"].append(_error(target["mesh"], exc))
    if not ready:
        return
    mapped = _host(client, "map", directory, meshes=ready, position_decimals=POSITION_MATCH_DECIMALS)
    data["errors"].extend(mapped["errors"])
    matches = {report["index"]: report for report in mapped["reports"]}
    prepared = []
    for entry in ready:
        if entry["index"] not in matches:
            continue
        report = matches[entry["index"]]
        try:
            matched = dict(entry, **report)
            if transfer_mode == "closest_point":
                matched = interpolate_weights(directory, matched, report)
                matched["match_bones"] = entry["bones"]
            prepared.append(matched)
        except Exception as exc:
            data["errors"].append(_error(entry["mesh"], exc))
    if prepared:
        imported = _host(client, "import", directory, meshes=prepared)
        data["reports"].extend(imported["reports"])
        data["errors"].extend(imported["errors"])
        data["selection_changed"] = imported["selection_changed"]
        data["selected_vertices"] = imported["selected_vertices"]
        for report in imported["reports"]:
            report["notices"] = notices[report["name"]]


def _files(client, directory, operation, transfer_mode, data):
    if transfer_mode not in ("vertex_id", "closest_point"):
        raise ValueError("未知权重导入方式")
    cache_dir = Path(paths.project_dir()) / "temp" / "skin_weights"
    data["directory"] = str(cache_dir)
    exporting = operation == "export_file"
    result = _host(client, "export" if exporting else "context", directory)
    data["errors"].extend(result["errors"])
    models, errors = _unique_meshes(result["meshes"])
    data["errors"].extend(errors)
    if exporting:
        _export(directory, cache_dir, models, data)
    else:
        _import(client, directory, cache_dir, models, data, transfer_mode)
    data.update(count=len(data["reports"]), vertices=sum(report["vertices"] for report in data["reports"]))
    if not models and not data["errors"]:
        data["warning"] = "Blender 没有选中可处理的网格"


def _inspect(client, directory, operation, threshold, mode, data):
    snapshot = _host(client, "snapshot", directory)
    data["errors"].extend(snapshot["errors"])
    models = snapshot["meshes"]
    if not models:
        data.update(max_influences=None, warning="Blender 没有选中可处理的蒙皮网格")
        return
    reports, patches = process_snapshot(directory, models, operation, threshold, mode)
    count = sum(report["valid_vertices"] for report in reports)
    if operation != "check":
        applied = _host(client, operation, directory, meshes=[dict(mesh, **patch) for mesh, patch in zip(models, patches)])
        count = applied["count"]
        data["errors"].extend(applied["errors"])
        failures = {error["mesh"] for error in applied["errors"]}
        for report in reports:
            if report["mesh"] in failures:
                report.update(modified_vertices=0, max_after=None, excess_after=None)
    data.update(count=count, mesh_count=len(reports), reports=reports,
                invalid_vertices=sum(report["invalid_vertices"] for report in reports),
                max_influences=None if data["errors"] else max(report["max_after"] for report in reports))


def run_operation(client, operation, threshold=4, mode=0, transfer_mode="vertex_id"):
    started = time.perf_counter()
    directory = None
    data = dict(operation=operation, threshold=threshold, count=0, reports=[], errors=[])
    try:
        if not getattr(client, "connected", False):
            raise RuntimeError("Blender 未连接，请先在中台连接 Blender")
        if operation not in ("check", "select_excess", "limit", "export_file", "import_file"):
            raise ValueError("未知蒙皮权重操作")
        directory = Path(tempfile.mkdtemp(prefix="qz-skin-transfer-blender-"))
        if operation in ("export_file", "import_file"):
            _files(client, directory, operation, transfer_mode, data)
        else:
            _inspect(client, directory, operation, threshold, mode, data)
        result = dict(ok=True, data=data)
    except Exception as exc:
        if isinstance(exc, PendingJobError):
            data["retained_path"] = str(directory)
        result = dict(ok=False, error=str(exc), **data)
    finally:
        if directory is not None and not data.get("retained_path"):
            shutil.rmtree(directory, ignore_errors=True)
    (result["data"] if result["ok"] else result)["elapsed_seconds"] = time.perf_counter() - started
    return result


def format_result(result):
    data = result["data"] if result["ok"] else result
    if data["operation"] not in ("export_file", "import_file"):
        yield from format_log_messages(result)
    else:
        verb = "导出" if data["operation"] == "export_file" else "导入"
        if result["ok"]:
            yield "info", f'{verb}权重成功 {data["count"]} 个模型、{data["vertices"]} 个顶点'
        else:
            yield "error", f'{verb}权重失败：{result["error"]}'
        if data.get("directory"):
            yield "info", f'权重目录：{data["directory"]}'
        if data.get("warning"):
            yield "warn", data["warning"]
        for report in data["reports"]:
            if report.get("transfer_mode") == "closest_point":
                yield "info", (f'{report["name"]} 直接复制 {report["direct_vertices"]} 个顶点，'
                               f'表面插值 {report["interpolated_vertices"]} 个顶点')
            for notice in report.get("notices", []):
                yield "warn", f'{report["name"]}：{notice}'
        if data.get("selection_changed"):
            yield "info", f'已选中 {data["selected_vertices"]} 个插值顶点'
        for error in data["errors"]:
            yield ("warn" if error.get("status") == "skipped" else "error",
                   f'{error["mesh"]}：{error["error"]}')
        yield "info", f'总耗时 {data["elapsed_seconds"]:.3f} 秒'
    if data.get("retained_path"):
        yield "warn", f'交换目录保留在：{data["retained_path"]}'
