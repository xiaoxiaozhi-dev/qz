"""项目内的单模型 .qzskin 文件；NumPy 留在中台，Maya 只接收二进制。"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import quote
import zipfile

import numpy as np

import paths

from ui.skin_transfer import prepare_transfer
from ui.skin_weight_mapping import interpolate_weights

FORMAT = "qz.skin_weights"
VERSION = 3
POSITION_MATCH_DECIMALS = 3  # 坐标匹配键的小数位；不量化存储的坐标、权重和DQ。
MEMBERS = {"offsets": "offsets.bin", "cols": "indices.bin",
           "values": "weights.bin", "dq": "dq.bin"}
GEOMETRY_MEMBERS = {"points": "points.bin", "triangles": "triangles.bin"}


def cache_filename(name):
    """普通名字直接可读；命名空间等 Windows 禁用字符用可逆编码。"""
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("模型名称为空或无效")
    stem = "".join(quote(char, safe="") if char in '<>:"/\\|?*%' or ord(char) < 32 else char
                   for char in name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if stem.split(".", 1)[0].upper() in reserved:
        stem = f"%{ord(stem[0]):02X}" + stem[1:]
    filename = stem + ".qzskin"
    if len(filename.encode("utf-8")) > 240:
        raise ValueError("模型名称过长，无法直接用作权重文件名，请缩短模型名称")
    return filename


def _unique_meshes(meshes):
    keys, errors = {}, []
    for mesh in meshes:
        try:
            keys[mesh["index"]] = cache_filename(mesh["name"]).casefold()
        except Exception as exc:
            errors.append(dict(mesh=mesh["mesh"], error=str(exc), status="skipped"))
    counts = Counter(keys.values())
    ready = []
    for mesh in meshes:
        key = keys.get(mesh["index"])
        if key is None:
            continue
        if counts[key] != 1:
            errors.append(dict(mesh=mesh["mesh"], error="选中模型同名或文件名大小写冲突，无法唯一保存或读取", status="skipped"))
        else:
            ready.append(mesh)
    return ready, errors


def write_archive(directory, cache_dir, mesh, source, *, source_host="maya"):
    """同目录暂存后原子替换；任何打包失败都保留上次成功的文件。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / cache_filename(source["name"])
    metadata = {key: mesh[key] for key in ("vertices", "influences", "nonzero")}
    metadata.update(format=FORMAT, version=VERSION, name=source["name"], source=source_host,
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    skinning_method=mesh["skinning_method"], geometry=source["geometry"],
                    bones=[dict(index=bone["index"], name=source["bone_names"][bone["index"]])
                           for bone in mesh["bones"]])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".skin-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("manifest.json", json.dumps(metadata, ensure_ascii=False))
            for suffix, member in {**MEMBERS, **GEOMETRY_MEMBERS}.items():
                archive.write(Path(directory) / f'mesh-{mesh["index"]}.{suffix}', member)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def read_archive(path, directory, target, transfer_mode="vertex_id"):
    """先完整检查文件和稀疏索引，再交给 Maya；不重新量化或归一化。"""
    if not path.is_file():
        raise ValueError(f"没有找到该模型的权重文件：{path}")
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)) or "manifest.json" not in members:
            raise ValueError("权重文件内容不完整或包含重复条目")
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ValueError("权重文件描述过大")
        metadata = json.loads(archive.read("manifest.json"))
        if metadata.get("format") != FORMAT or type(metadata.get("version")) is not int or metadata["version"] not in (1, 2, VERSION):
            raise ValueError("不支持的权重文件格式或版本")
        expected = {"manifest.json", *MEMBERS.values()}
        if metadata["version"] >= 3:
            expected.update(GEOMETRY_MEMBERS.values())
        if set(members) != expected:
            raise ValueError("权重文件内容不完整或包含未知条目")
        if transfer_mode == "closest_point" and metadata["version"] < 3:
            raise ValueError("旧权重文件没有几何数据，请重新导出后使用最近表面点导入")
        method = metadata.get("skinning_method")
        if (metadata["version"] >= 2 or method is not None) and (type(method) is not int or method not in (0, 1, 2)):
            raise ValueError("权重文件缺少有效的蒙皮方式")
        if metadata.get("name") != target["name"]:
            raise ValueError("文件内模型名称与选中模型不匹配")
        vertices, width, count = (metadata.get(key) for key in ("vertices", "influences", "nonzero"))
        if any(type(n) is not int or not 0 < n <= 4294967295 for n in (vertices, width, count)):
            raise ValueError("权重文件尺寸无效")
        if transfer_mode == "vertex_id" and vertices != target["vertices"]:
            raise ValueError(f'顶点数不匹配：文件 {vertices}，当前模型 {target["vertices"]}')
        bones = metadata.get("bones")
        if not isinstance(bones, list) or not bones:
            raise ValueError("权重文件缺少骨骼信息")
        indices, names = set(), set()
        for bone in bones:
            index, name = bone.get("index"), bone.get("name")
            if (type(index) is not int or not 0 <= index < width or index in indices
                    or not isinstance(name, str) or not name or name in names):
                raise ValueError("权重文件骨骼名称或编号无效、重复")
            indices.add(index)
            names.add(name)
        lengths = dict(offsets=(vertices + 1) * 4, cols=count * 4, values=count * 8, dq=vertices * 8)
        arrays = {}
        for suffix, member in MEMBERS.items():
            if archive.getinfo(member).file_size != lengths[suffix]:
                raise ValueError("权重文件数据长度不匹配")
            out = Path(directory) / f'mesh-{target["index"]}.{suffix}'
            with archive.open(member) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            arrays[suffix] = np.fromfile(out, dtype="<u4" if suffix in ("offsets", "cols") else "<f8")
        offsets, columns, values, dq = (arrays[key] for key in MEMBERS)
        if offsets[0] != 0 or offsets[-1] != count or not (offsets[1:] > offsets[:-1]).all():
            raise ValueError("权重文件顶点索引无效或存在全零顶点")
        if not np.isin(columns, list(indices)).all() or set(np.unique(columns).tolist()) != indices:
            raise ValueError("权重文件骨骼编号越界或与描述不一致")
        unsorted = columns[1:] <= columns[:-1]
        unsorted[offsets[1:-1].astype(np.int64) - 1] = False
        if unsorted.any():
            raise ValueError("权重文件中同一顶点的骨骼编号重复或顺序无效")
        if not (np.isfinite(values) & (values > 0)).all():
            raise ValueError("权重文件包含负值、全零或非有限权重")
        if not (np.isfinite(dq) & (dq >= 0) & (dq <= 1)).all():
            raise ValueError("权重文件 DQ 包含非有限数或超出 0～1 范围")
        geometry = metadata.get("geometry")
        if transfer_mode == "closest_point":
            if (not isinstance(geometry, dict) or type(geometry.get("triangles")) is not int
                    or geometry["triangles"] < 1 or geometry.get("space") != "object"
                    or geometry.get("positions") != "skin_input"):
                raise ValueError("权重文件的几何描述无效")
            for suffix, member in GEOMETRY_MEMBERS.items():
                length = vertices * 3 * 8 if suffix == "points" else geometry["triangles"] * 3 * 4
                if archive.getinfo(member).file_size != length:
                    raise ValueError("权重文件几何数据长度不匹配")
                out = Path(directory) / f'mesh-{target["index"]}.{suffix}'
                with archive.open(member) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                data = np.fromfile(out, dtype="<f8" if suffix == "points" else "<u4")
                if suffix == "points" and not np.isfinite(data).all():
                    raise ValueError("权重文件包含非有限几何坐标")
                if suffix == "triangles" and (data >= vertices).any():
                    raise ValueError("权重文件三角形顶点编号越界")
    return dict(index=target["index"], mesh=target["mesh"], name=target["name"],
                vertices=vertices, target_vertices=target["vertices"], influences=width, nonzero=count,
                bones=bones, skinning_method=method, geometry=geometry, transfer_mode=transfer_mode)


def run_file_operation(client, operation, transfer_mode="vertex_id"):
    started = time.perf_counter()
    result = dict(operation=operation, count=0, vertices=0, reports=[], errors=[])
    try:
        if operation not in ("export_file", "import_file"):
            raise ValueError("未知权重文件操作")
        if transfer_mode not in ("vertex_id", "closest_point"):
            raise ValueError("未知权重导入方式")
        if not getattr(client, "connected", False):
            raise RuntimeError("Maya 未连接，请先在中台连接 Maya")
        with tempfile.TemporaryDirectory(prefix="qz-skin-transfer-", ignore_cleanup_errors=True) as directory:
            response = client.call("skin_weights_files", op="export" if operation == "export_file" else "context",
                                   directory=directory, timeout=300, connect_timeout=1)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Maya 获取模型失败"))
            context = response["data"]
            cache_dir = Path(paths.project_dir()) / "temp" / "skin_weights"
            result["directory"] = str(cache_dir)
            result["errors"].extend(context.get("errors", []))
            meshes, errors = _unique_meshes(context.get("meshes", []))
            result["errors"].extend(errors)
            if not meshes and not result["errors"]:
                result["warning"] = "Maya 没有选中可处理的网格模型"
            if operation == "export_file":
                ready, errors = prepare_transfer(directory, meshes, {})
                result["errors"].extend(errors)
                sources = {mesh["index"]: mesh for mesh in meshes}
                for mesh in ready:
                    try:
                        source = sources[mesh["index"]]
                        path = write_archive(directory, cache_dir, mesh, source)
                        result["reports"].append(dict(name=source["name"], vertices=mesh["vertices"], path=str(path),
                                                      skinning_method=mesh["skinning_method"]))
                    except Exception as exc:
                        result["errors"].append(dict(mesh=mesh["mesh"], error=str(exc), status="failed"))
            else:
                ready = []
                for target in meshes:
                    try:
                        ready.append(read_archive(cache_dir / cache_filename(target["name"]), directory, target, transfer_mode))
                    except Exception as exc:
                        result["errors"].append(dict(mesh=target["mesh"], error=str(exc), status="skipped"))
                if ready and transfer_mode == "closest_point":
                    mapped = client.call("skin_weights_files", op="map", directory=directory, meshes=ready,
                                         position_decimals=POSITION_MATCH_DECIMALS, timeout=300, connect_timeout=1)
                    if not mapped.get("ok"):
                        raise RuntimeError(mapped.get("error", "Maya 最近点匹配失败"))
                    result["errors"].extend(mapped["data"].get("errors", []))
                    reports = {report["index"]: report for report in mapped["data"].get("reports", [])}
                    interpolated = []
                    for entry in ready:
                        if entry["index"] not in reports:
                            continue
                        try:
                            interpolated.append(interpolate_weights(directory, entry, reports[entry["index"]]))
                        except Exception as exc:
                            result["errors"].append(dict(mesh=entry["mesh"], error=str(exc), status="skipped"))
                    ready = interpolated
                if ready:
                    response = client.call("skin_weights_files", op="import", directory=directory,
                                           meshes=ready, timeout=300, connect_timeout=1)
                    if not response.get("ok"):
                        raise RuntimeError(response.get("error", "Maya 导入权重失败"))
                    result["reports"].extend(response["data"].get("reports", []))
                    result["errors"].extend(response["data"].get("errors", []))
                    result["selection_changed"] = response["data"].get("selection_changed", False)
                    result["selected_vertices"] = response["data"].get("selected_vertices", 0)
            result["count"] = len(result["reports"])
            result["vertices"] = sum(report["vertices"] for report in result["reports"])
        response = dict(ok=True, data=result)
    except Exception as exc:
        response = dict(ok=False, error=str(exc), **result)
    data = response["data"] if response.get("ok") else response
    data["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return response


def format_file_messages(response):
    data = response.get("data", {}) if response.get("ok") else response
    verb = "导出" if data.get("operation") == "export_file" else "导入"
    messages = []
    if not response.get("ok"):
        messages.append(("error", f'{verb}权重失败：{response.get("error", "未知错误")}'))
    else:
        count, errors = data.get("count", 0), data.get("errors", [])
        status = "完成" if not errors else "部分完成" if count else "未完成"
        messages.append(("info", f'{verb}权重{status}：成功 {count} 个模型，{data.get("vertices", 0)} 个顶点，跳过或失败 {len(errors)} 个模型。'))
    if data.get("directory"):
        messages.append(("info", f'权重目录：{data["directory"]}'))
    if data.get("warning"):
        messages.append(("warn", data["warning"]))
    for report in data.get("reports", []):
        method = report.get("skinning_method")
        mode = {0: "线性蒙皮", 1: "双四元数蒙皮", 2: "混合蒙皮"}.get(method)
        detail = f"，蒙皮方式：{mode}" if mode else ""
        messages.append(("info", f'{report["name"]}：{verb} {report["vertices"]} 个顶点，包含 DQ 权重{detail}。'))
        if report.get("transfer_mode") == "closest_point":
            messages.append(("info", f'按位置直接复制 {report["direct_vertices"]} 个顶点，'
                             f'最近表面点插值 {report["interpolated_vertices"]} 个顶点。'))
        if report.get("interpolation_set"):
            messages.append(("info", f'插值顶点选择集：{report["interpolation_set"]}'))
        if verb == "导入" and method is None:
            messages.append(("warn", f'{report["name"]}：旧文件未记录蒙皮方式，已保留当前方式；重新导出可记录。'))
    if data.get("selection_changed"):
        messages.append(("info", f'已选中 {data.get("selected_vertices", 0)} 个表面插值顶点。'))
    for error in data.get("errors", []):
        messages.append(("warn" if error.get("status") == "skipped" else "error",
                         f'{error.get("mesh", "未知模型")}：{error["error"]}'))
    if "elapsed_seconds" in data:
        messages.append(("info", f'总耗时 {data["elapsed_seconds"]:.3f} 秒。'))
    return messages
