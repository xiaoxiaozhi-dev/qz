"""Max 2020 蒙皮任务协调；数值计算及缓存均在中台执行。"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

import paths
from ui.skin_weights import process_snapshot, format_log_messages
from ui.skin_transfer import prepare_transfer
from ui.skin_weight_files import (cache_filename, _unique_meshes, read_archive, MEMBERS, GEOMETRY_MEMBERS,
                                  FORMAT, POSITION_MATCH_DECIMALS, format_file_messages)
from ui.skin_weight_mapping import interpolate_weights
import numpy as np


def _call(client, action, _timings=None, **kwargs):
    if not getattr(client, "connected", False):
        raise RuntimeError("Max 未连接，请先在中台连接 Max")
    started = time.perf_counter()
    try:
        response = client.call(action, timeout=300, connect_timeout=1, **kwargs)
    except Exception:
        if _timings is not None:
            _timings["max_unprofiled"] = _timings.get("max_unprofiled", 0.) + time.perf_counter()-started
        raise
    elapsed = time.perf_counter()-started
    if _timings is not None:
        data = response.get("data", {}) if response.get("ok") else {}
        if "action_seconds" in data:
            reported = data.get("timings_seconds")
            if reported:
                for key, value in reported.items():
                    _timings[key] = _timings.get(key, 0.) + value
            else:
                _timings["max_unprofiled"] = _timings.get("max_unprofiled", 0.) + data["action_seconds"]
            # Remainder includes serialization, socket handling and main-thread queueing.
            _timings["bridge_wait"] = _timings.get("bridge_wait", 0.) + max(0.,elapsed-data["action_seconds"])
        else:
            _timings["max_unprofiled"] = _timings.get("max_unprofiled", 0.) + elapsed
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Max 操作失败"))
    return response["data"]


def run_operation(client, operation, threshold=4, mode=0):
    started, token, timings = time.perf_counter(), None, {}
    try:
        with tempfile.TemporaryDirectory(prefix="qz-skin-max-", ignore_cleanup_errors=True) as directory:
            try:
                snapshot = _call(client, "skin_weights_max", op="snapshot", directory=directory,
                                 retain_task=operation != "check", _timings=timings)
                token = snapshot.get("token")
                meshes = snapshot.get("meshes", [])
                if not meshes:
                    result = dict(count=0, max_influences=None, reports=[], errors=snapshot.get("errors", []),
                                  warning=snapshot.get("warning", "未选中可处理的蒙皮模型"))
                else:
                    reports, patches = process_snapshot(directory, meshes, operation, threshold, mode, timings=timings)
                    # The exchange format is zero-based; user-facing Max vertices are one-based.
                    for report in reports:
                        for issue in report["issue_examples"]:
                            vertex = int(issue["vertex"].rsplit("[", 1)[1][:-1]) + 1
                            issue["vertex"] = f'{report["mesh"]} 顶点 {vertex}'
                    errors, applied = list(snapshot.get("errors", [])), 0
                    if operation != "check":
                        response = _call(client, "skin_weights_max", op=operation, token=token,
                                         patches=patches, threshold=threshold, _timings=timings)
                        token = None  # Apply consumes the Max job; no extra queued release is needed.
                        errors.extend(response.get("errors", []))
                        applied = response.get("count", 0)
                        failed = {error.get("mesh") for error in response.get("errors", [])}
                        for report in reports:
                            if report["mesh"] in failed:
                                report.update(modified_vertices=0, max_after=None, excess_after=None)
                    result = dict(count=sum(r["valid_vertices"] for r in reports) if operation == "check" else applied,
                                  mesh_count=len(reports), reports=reports, errors=errors,
                                  max_influences=None if errors else max(r["max_after"] for r in reports),
                                  invalid_vertices=sum(r["invalid_vertices"] for r in reports))
                response = dict(ok=True, data=result)
            finally:
                if token:
                    try: _call(client, "skin_weights_max", op="release", token=token, _timings=timings)
                    except Exception: pass
    except Exception as exc:
        response = dict(ok=False, error=str(exc))
    data = response["data"] if response.get("ok") else response
    elapsed = time.perf_counter()-started
    timings["local_other"] = max(0., elapsed-sum(timings.values()))
    data.update(operation=operation, threshold=threshold, elapsed_seconds=round(elapsed, 6),
                timings_seconds={key:round(value,6) for key,value in timings.items()})
    return response


def _write_archive(directory, cache_dir, mesh, source):
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / cache_filename(source["name"])
    metadata = {key: mesh[key] for key in ("vertices", "influences", "nonzero", "skinning_method")}
    metadata.update(format=FORMAT, version=3, geometry=source["geometry"], source="max", name=source["name"],
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    bones=mesh["bones"], max_bone_limit=source["max_bone_limit"],
                    max_unnormalized_vertices=source["max_unnormalized_vertices"])
    manifest = json.dumps(metadata, ensure_ascii=False)
    if len(manifest.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("权重文件描述超过格式上限")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".skin-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("manifest.json", manifest)
            for suffix, member in {**MEMBERS, **GEOMETRY_MEMBERS}.items():
                archive.write(Path(directory) / f'mesh-{mesh["index"]}.{suffix}', member)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()
    return destination


def _read_archive(path, directory, target, transfer_mode="vertex_id"):
    entry = read_archive(path, directory, target, transfer_mode)
    with zipfile.ZipFile(path) as archive:
        metadata = json.loads(archive.read("manifest.json"))
    flags = metadata.get("max_unnormalized_vertices", [])
    limit = metadata.get("max_bone_limit")
    if (not isinstance(flags, list) or any(type(v) is not int or not 0 <= v < entry["vertices"] for v in flags)
            or flags != sorted(set(flags))):
        raise ValueError("权重文件未归一化顶点记录无效")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("权重文件最大影响数记录无效")
    entry.update(node_handle=target["node_handle"], max_unnormalized_vertices=flags)
    if limit is not None: entry["max_bone_limit"] = limit
    return entry


def _interpolate_max(directory, entry, report):
    prefix = Path(directory) / f'mesh-{entry["index"]}'
    ids = np.fromfile(prefix.with_suffix(".match_ids"), dtype="<u4").reshape(entry["target_vertices"], 3)
    factors = np.fromfile(prefix.with_suffix(".match_factors"), dtype="<f8").reshape(entry["target_vertices"], 3)
    source_flags = np.isin(ids, entry.get("max_unnormalized_vertices", []))
    flags = np.flatnonzero((source_flags & (factors > 0)).any(axis=1)).tolist()
    mapped = interpolate_weights(directory, entry, report)
    mapped["max_unnormalized_vertices"] = flags
    return mapped


def run_file_operation(client, operation, transfer_mode="vertex_id"):
    started = time.perf_counter()
    cache_dir = Path(paths.project_dir()) / "temp" / "skin_weights"
    result = dict(operation=operation, count=0, vertices=0, reports=[], errors=[], directory=str(cache_dir))
    try:
        if operation not in ("export_file", "import_file"): raise ValueError("未知权重文件操作")
        if transfer_mode not in ("vertex_id", "closest_point"): raise ValueError("未知权重导入方式")
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-max-", ignore_cleanup_errors=True) as directory:
            context = _call(client, "skin_weight_files_max", op="export" if operation == "export_file" else "context",
                            **(dict(directory=directory) if operation == "export_file" else {}))
            result["errors"].extend(context.get("errors", []))
            meshes, errors = _unique_meshes(context.get("meshes", []))
            result["errors"].extend(errors)
            if not meshes: result["warning"] = "没有选中可处理的模型"
            if operation == "export_file":
                ready, errors = prepare_transfer(directory, meshes, {}, decode_names=False)
                result["errors"].extend(errors)
                sources = {mesh["index"]: mesh for mesh in meshes}
                for mesh in ready:
                    try:
                        destination = _write_archive(directory, cache_dir, mesh, sources[mesh["index"]])
                        result["reports"].append(dict(name=mesh["name"], vertices=mesh["vertices"],
                                                      skinning_method=mesh["skinning_method"], path=str(destination)))
                    except Exception as exc:
                        result["errors"].append(dict(mesh=mesh["mesh"], error=str(exc), status="skipped"))
            else:
                ready = []
                for target in meshes:
                    try: ready.append(_read_archive(cache_dir / cache_filename(target["name"]), directory, target, transfer_mode))
                    except Exception as exc:
                        result["errors"].append(dict(mesh=target["mesh"], error=str(exc), status="skipped"))
                if ready and transfer_mode == "closest_point":
                    mapped = _call(client, "skin_weight_files_max", op="map", directory=directory, meshes=ready,
                                   position_decimals=POSITION_MATCH_DECIMALS)
                    result["errors"].extend(mapped.get("errors", []))
                    reports = {report["index"]: report for report in mapped.get("reports", [])}
                    interpolated = []
                    for entry in ready:
                        if entry["index"] not in reports: continue
                        try: interpolated.append(_interpolate_max(directory, entry, reports[entry["index"]]))
                        except Exception as exc:
                            result["errors"].append(dict(mesh=entry["mesh"], error=str(exc), status="skipped"))
                    ready = interpolated
                if ready:
                    imported = _call(client, "skin_weight_files_max", op="import", directory=directory, meshes=ready)
                    result["selection_changed"] = imported.get("selection_changed", False)
                    result["selected_vertices"] = imported.get("selected_vertices", 0)
                    result["reports"].extend(imported.get("reports", []))
                    result["errors"].extend(imported.get("errors", []))
            result.update(count=len(result["reports"]), vertices=sum(r["vertices"] for r in result["reports"]))
        response = dict(ok=True, data=result)
    except Exception as exc:
        response = dict(ok=False, error=str(exc), **result)
    data = response["data"] if response.get("ok") else response
    data["elapsed_seconds"] = round(time.perf_counter()-started, 6)
    return response


def format_result(response):
    data = response.get("data", {}) if response.get("ok") else response
    if data.get("operation") in ("export_file", "import_file"):
        messages = format_file_messages(response)
        for report in data.get("reports", []):
            if report.get("selection_warning"):
                messages.insert(-1, ("warn", report["name"] + "：" + report["selection_warning"]))
        return messages
    return format_log_messages(response)
