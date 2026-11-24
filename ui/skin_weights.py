"""中台蒙皮数值处理：NumPy 在中台运行，JSON 只传任务及统计信息。"""

from pathlib import Path
from contextlib import contextmanager
import tempfile
import time

import numpy as np

WEIGHT_DECIMALS = 3  # 仅“修改超出影响”生成的权重；导出/DQ不量化。
PRECISION = 10 ** WEIGHT_DECIMALS
BLOCK_VALUES = 262144
ISSUE_LIMIT = 12


def _timed(timings, key, function, *args, **kwargs):
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        timings[key] = timings.get(key, 0.0) + time.perf_counter() - started


@contextmanager
def _file(path, mode, timings, key):
    handle = _timed(timings, key, path.open, mode)
    try:
        yield handle
    finally:
        _timed(timings, key, handle.close)


def process_snapshot(directory, meshes, operation, threshold=4, mode=0, timings=None):
    timings = {} if timings is None else timings
    before_io = timings.get("host_file_read", 0.0) + timings.get("host_file_write", 0.0)
    started = time.perf_counter()
    try:
        return _process_snapshot(directory, meshes, operation, threshold, mode, timings)
    finally:
        io_seconds = timings.get("host_file_read", 0.0) + timings.get("host_file_write", 0.0) - before_io
        timings["numpy_compute"] = timings.get("numpy_compute", 0.0) + max(
            0.0, time.perf_counter() - started - io_seconds)


def _process_snapshot(directory, meshes, operation, threshold, mode, timings):
    """流式读取 Maya 的 little-endian float64 快照，生成小型稀疏补丁。"""
    if operation not in ("check", "select_excess", "limit"):
        raise ValueError("未知蒙皮操作")
    if type(threshold) is not int or threshold < 1:
        raise ValueError("最大影响数必须为正整数")
    if mode not in (0, 1):
        raise ValueError("未知分配方式")
    directory = Path(directory)
    reports, patches = [], []
    for mesh in meshes:
        index, vertices, influences = mesh["index"], mesh["vertices"], mesh["influences"]
        source = directory / f"mesh-{index}.weights"
        if _timed(timings, "host_file_read", source.stat).st_size != vertices * influences * 8:
            raise ValueError("权重快照长度不匹配")
        report = dict(mesh=mesh["mesh"], vertices=vertices, valid_vertices=0,
                      invalid_vertices=0, max_before=0, max_after=0,
                      excess_before=0, excess_after=0, modified_vertices=0,
                      issue_counts={}, issue_examples=[])
        count = 0
        block_rows = max(1, min(2048, BLOCK_VALUES // influences))
        with _file(source, "rb", timings, "host_file_read") as src, \
                _file(directory / f"mesh-{index}.ids", "wb", timings, "host_file_write") as ids_file, \
                _file(directory / f"mesh-{index}.issues", "wb", timings, "host_file_write") as issues_file, \
                _file(directory / f"mesh-{index}.cols", "wb", timings, "host_file_write") as cols_file, \
                _file(directory / f"mesh-{index}.values", "wb", timings, "host_file_write") as values_file:
            for start in range(0, vertices, block_rows):
                size = min(block_rows, vertices - start)
                weights = _timed(timings, "host_file_read", np.fromfile, src, dtype="<f8", count=size * influences).reshape(size, influences)
                finite = np.isfinite(weights).all(axis=1)
                negative = (weights < 0).any(axis=1) & finite
                counts = np.count_nonzero(weights > 0, axis=1)
                zero = (counts == 0) & finite & ~negative
                valid = finite & ~negative & ~zero
                if operation == "check" and (~valid).any():
                    _timed(timings, "host_file_write", (np.flatnonzero(~valid) + start).astype("<u4").tofile, issues_file)
                for reason, mask in (("非有限数", ~finite), ("负权重", negative), ("全零", zero)):
                    number = int(mask.sum())
                    if number:
                        report["issue_counts"][reason] = report["issue_counts"].get(reason, 0) + number
                        room = ISSUE_LIMIT - len(report["issue_examples"])
                        report["issue_examples"].extend(
                            {"vertex": f'{mesh["mesh"]}.vtx[{start + int(v)}]', "reason": reason}
                            for v in np.flatnonzero(mask)[:max(0, room)])
                report["valid_vertices"] += int(valid.sum())
                report["invalid_vertices"] += int((~valid).sum())
                if valid.any():
                    report["max_before"] = max(report["max_before"], int(counts[valid].max()))
                excess = valid & (counts > threshold)
                report["excess_before"] += int(excess.sum())
                after = counts.copy()
                if operation != "check" and excess.any():
                    rows = np.flatnonzero(excess)
                    _timed(timings, "host_file_write", (rows + start).astype("<u4").tofile, ids_file)
                    count += len(rows)
                    if operation == "limit":
                        selected = weights[rows]
                        # argpartition 找边界，再按 influence 顺序选择相同权重，保证并列时稳定。
                        partition = np.argpartition(-selected, threshold - 1, axis=1)[:, :threshold]
                        cutoff = np.take_along_axis(selected, partition, axis=1).min(axis=1)
                        larger = selected > cutoff[:, None]
                        tied = selected == cutoff[:, None]
                        slots = threshold - larger.sum(axis=1)
                        keep = larger | (tied & (np.cumsum(tied, axis=1) <= slots[:, None]))
                        _, columns = np.nonzero(keep)
                        columns = columns.reshape(len(rows), threshold)
                        top = np.take_along_axis(selected, columns, axis=1)
                        # 先缩放再求和，避免合法的大数求和溢出。
                        scale = selected.max(axis=1, keepdims=True)
                        if mode == 0:
                            adjusted = top / scale
                            adjusted /= adjusted.sum(axis=1, keepdims=True)
                        else:
                            adjusted = (top / scale) / (selected / scale).sum(axis=1, keepdims=True)
                            adjusted += (1.0 - adjusted.sum(axis=1, keepdims=True)) / threshold
                        adjusted /= adjusted.sum(axis=1, keepdims=True)
                        scaled = adjusted * PRECISION
                        units = np.floor(scaled).astype(np.int64)
                        remaining = PRECISION - units.sum(axis=1)
                        if (remaining < 0).any() or (remaining > threshold).any():
                            raise ArithmeticError(f"{WEIGHT_DECIMALS} 位精度的归一化舍入失败")
                        order = np.argsort(-(scaled - units), axis=1, kind="stable")
                        increment = np.arange(threshold)[None, :] < remaining[:, None]
                        units[np.arange(len(rows))[:, None], order] += increment
                        values = units / PRECISION
                        _timed(timings, "host_file_write", columns.astype("<u4").tofile, cols_file)
                        _timed(timings, "host_file_write", values.astype("<f8").tofile, values_file)
                        after[rows] = np.count_nonzero(units, axis=1)
                        report["modified_vertices"] += len(rows)
                if valid.any():
                    report["max_after"] = max(report["max_after"], int(after[valid].max()))
                report["excess_after"] += int((valid & (after > threshold)).sum())
        reports.append(report)
        patches.append({"index": index, "count": count, "width": threshold,
                        "invalid_count": report["invalid_vertices"]})
    return reports, patches


def _call(client, timings, **params):
    started = time.perf_counter()
    try:
        response = client.call("skin_weights", **params)
    except Exception:
        timings["maya_unprofiled"] = timings.get("maya_unprofiled", 0.0) + time.perf_counter() - started
        raise
    elapsed = time.perf_counter() - started
    data = response.get("data") if response.get("ok") else None
    if isinstance(data, dict) and "action_seconds" in data:
        for key, seconds in data.get("timings_seconds", {}).items():
            timings[key] = timings.get(key, 0.0) + seconds
        timings["bridge_wait"] = timings.get("bridge_wait", 0.0) + max(0.0, elapsed - data["action_seconds"])
    else:
        # 旧 Maya 动作库、连接错误或主线程动作直接失败时，不能伪造细分耗时。
        timings["maya_unprofiled"] = timings.get("maya_unprofiled", 0.0) + elapsed
    return response


def run_operation(client, operation, threshold=4, mode=0, issue_token=None):
    """总耗时包含释放任务及临时文件清理，分段互不重复累计。"""
    started, timings = time.perf_counter(), {}
    try:
        if operation in ("select_issues", "fix_negative", "hammer_issues"):
            if not issue_token:
                raise ValueError("请先检测问题顶点")
            response = _call(client, timings, timeout=300, op=operation, token=issue_token)
        else:
            response = _run_operation(client, operation, threshold, mode, timings)
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    elapsed = time.perf_counter() - started
    timings["local_other"] = max(0.0, elapsed - sum(timings.values()))
    target = response["data"] if response.get("ok") else response
    target["timings_seconds"] = {key: round(value, 6) for key, value in timings.items()}
    target["elapsed_seconds"] = round(elapsed, 6)
    target["operation"] = operation
    target["threshold"] = threshold
    return response


def _run_operation(client, operation, threshold, mode, timings):
    """后台任务入口。TemporaryDirectory 仅用于进程间交换，不是用户导入导出。"""
    token = None
    with tempfile.TemporaryDirectory(prefix="qz-skin-", ignore_cleanup_errors=True) as directory:
        try:
            response = _call(client, timings, timeout=300, op="snapshot", directory=directory)
            if not response.get("ok"):
                return response
            snapshot = response["data"]
            token = snapshot.get("token")
            meshes = snapshot.get("meshes", [])
            if not meshes:
                return {"ok": True, "data": dict(count=0, max_influences=None,
                        warning=snapshot.get("warning", "没有可处理的蒙皮网格"),
                        errors=snapshot.get("errors", []))}
            reports, patches = process_snapshot(directory, meshes, operation, threshold, mode, timings)
            errors = list(snapshot.get("errors", []))
            applied = 0
            if operation != "check":
                result = _call(client, timings, timeout=300, op=operation, token=token,
                                     patches=patches, threshold=threshold)
                if not result.get("ok"):
                    return result
                operation_errors = result["data"].get("errors", [])
                errors.extend(operation_errors)
                applied = result["data"].get("count", 0)
                failed = {error.get("mesh") for error in operation_errors}
                for report in reports:
                    if report["mesh"] in failed:
                        report["planned_modified_vertices"] = report["modified_vertices"]
                        report["modified_vertices"] = 0
                        report["max_after"] = None
                        report["excess_after"] = None
                        report["status"] = "未应用，需重新检测"
            invalid = sum(r["invalid_vertices"] for r in reports)
            issue_token = None
            if operation == "check" and invalid:
                remembered = _call(client, timings, timeout=300, op="remember_issues",
                                   token=token, patches=patches)
                if not remembered.get("ok"):
                    return remembered
                issue_token = remembered["data"]["issue_token"]
            result = dict(count=(sum(r["valid_vertices"] for r in reports)
                                 if operation == "check" else applied),
                          mesh_count=len(reports), operation=operation,
                          max_influences=(None if errors else max(r["max_after"] for r in reports)),
                          invalid_vertices=invalid, reports=reports, errors=errors)
            if operation == "check":
                result["issue_token"] = issue_token
            if invalid or errors:
                result["warning"] = f"异常顶点 {invalid} 个；失败对象 {len(errors)} 个，详见统计与示例"
            return {"ok": True, "data": result}
        except Exception as exc:
            return {"ok": False, "error": f"{exc}；若发生连接超时，Maya 操作可能仍在执行，请完成后重新检测"}
        finally:
            if token:
                try:
                    _call(client, timings, timeout=5, op="release", token=token)
                except Exception:
                    pass  # 不掩盖操作结果；Maya 端有过期任务回收。


def format_log_messages(response):
    """仅产生中文日志文字及级别；实际日志控件仍由 UI 线程统一写入。"""
    data = response.get("data", {}) if response.get("ok") else response
    operation = data.get("operation", "check")
    label = {"check": "检测", "select_excess": "选择", "limit": "修改",
             "select_issues": "选择问题顶点", "fix_negative": "修复负权重顶点", "hammer_issues": "锤式修复"}.get(operation, "操作")
    messages = []
    if not response.get("ok"):
        messages.append(("error", f'{label}失败：{response.get("error", "未知错误")}'))
    elif operation in ("select_issues", "fix_negative", "hammer_issues"):
        errors = data.get("errors", [])
        status = "部分完成" if errors or data.get('remaining', 0) else "完成"
        if operation == "select_issues":
            text = f'{label}{status}：选中 {data.get("count", 0)} 个问题顶点'
        else:
            text = (f'{label}{status}：尝试 {data.get("attempted", 0)} 个顶点，'
                    f'修复 {data.get("count", 0)} 个，仍异常 {data.get("remaining", 0)} 个；'
                    '请重新检测以更新整体统计')
            if data.get('unverified'):
                text += f'；另有 {data["unverified"]} 个顶点未能确认修复结果'
        if operation == "fix_negative" and data.get("remaining"):
            text += '；未修复项包含全零、非有限值或无法保持总和的权重，可重新检测后使用锤式修复'
        messages.append(("warn" if operation == "fix_negative" and data.get("remaining") else "info", text + "。"))
        messages.extend(("error", f'{error.get("mesh", "未知对象")}：{error["error"]}') for error in errors)
    else:
        reports, errors = data.get("reports", []), data.get("errors", [])
        models = sum(r.get("max_after") is not None for r in reports)
        vertices = sum(r.get("vertices", 0) for r in reports)
        excess = sum(r.get("excess_before", 0) for r in reports)
        invalid = data.get("invalid_vertices", 0)
        status = "未执行" if not reports else "部分完成" if errors else "完成"
        text = f"{label}{status}：处理 {models} 个模型，扫描 {vertices} 个顶点"
        if operation == "check":
            text += f"，超限 {excess} 个，异常 {invalid} 个"
        elif operation == "select_excess":
            text += f'，选中 {data.get("count", 0)} 个超限顶点，异常 {invalid} 个'
        else:
            text += f'，修改 {data.get("count", 0)} 个顶点，跳过 {invalid} 个异常顶点'
        after = data.get("max_influences")
        if after is None:
            text += "；整体结果需重新检测"
        elif operation == "limit":
            before = max((r.get("max_before", 0) for r in reports), default=0)
            text += f"；最大有效影响数 {before} → {after}"
        else:
            text += f"；最大有效影响数 {after}"
        if "threshold" in data:
            text += f'，上限 {data["threshold"]}'
        if errors:
            text += f"；失败对象 {len(errors)} 个"
        messages.append(("info", text + "。"))
        if not reports and data.get("warning"):
            messages.append(("warn", data["warning"]))
        for report in reports:
            if report.get("issue_counts"):
                counts = "、".join(f"{reason} {count} 个" for reason, count in report["issue_counts"].items())
                examples = report.get("issue_examples", [])
                messages.append(("warn", f'{report["mesh"]}：{counts}；以下为 {len(examples)} 个异常示例。'))
                messages.extend(("warn", f'{issue["vertex"]}：{issue["reason"]}') for issue in examples)
        messages.extend(("error", f'{error.get("mesh", "未知对象")}：{error.get("error", "未知错误")}')
                        for error in errors)
    if "elapsed_seconds" in data:
        messages.append(("info", f'总耗时 {data["elapsed_seconds"]:.3f} 秒。'))
    return messages
