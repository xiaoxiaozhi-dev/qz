"""Maya 主线程蒙皮读写。不依赖 NumPy，不解除绑定，不改 blendWeights。"""

from array import array
from contextlib import contextmanager
import math
from pathlib import Path
import struct
import sys
import time
import uuid

from maya_bridge import register_action

BLOCK_VALUES = 262144
VERIFY_AFTER_WRITE = False  # 仅开发调试时开启；正常成功写入不重复读取权重。
_JOBS = {}
_ISSUE_JOBS = {}  # 完整问题顶点编号留在 Maya 内存，不依赖已删除的交换文件。


@contextmanager
def _stage(timings, key):
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[key] = timings.get(key, 0.0) + time.perf_counter() - started


def _timed(timings, key, function, *args, **kwargs):
    with _stage(timings, key):
        return function(*args, **kwargs)


def _write_snapshot(file, weights, timings):
    payload = _timed(timings, "maya_encode", _bytes, weights)
    _timed(timings, "maya_file_write", file.write, payload)


def _bytes(values, code="d"):
    # Maya 2024 实测批量打包更快；保持 little-endian float64，不改权重精度。
    # 调用方已按 BLOCK_VALUES 分块，临时参数序列不会随整个模型增长。
    return struct.pack(f"<{len(values)}{code}", *values)


def _read(file, count, code="d"):
    data = array(code)
    data.fromfile(file, count)
    if sys.byteorder != "little":
        data.byteswap()
    return data


def _selected_meshes(cmds):
    meshes, errors = [], []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        shapes = ([node] if cmds.nodeType(node) == "mesh" else
                  cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or [])
        shapes = [s for s in shapes if cmds.nodeType(s) == "mesh"
                  and not cmds.getAttr(s + ".intermediateObject")]
        if not shapes:
            errors.append({"mesh": node, "error": "不是多边形网格"})
        for shape in shapes:
            if shape not in meshes:
                meshes.append(shape)
    return meshes, errors


class _Mesh:
    def __init__(self, shape):
        from maya import cmds
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma
        self.cmds, self.om = cmds, om
        self.shape = shape
        skins = list(dict.fromkeys(n for n in (cmds.listHistory(shape, pruneDagObjects=True) or [])
                                   if cmds.nodeType(n) == "skinCluster"))
        if len(skins) != 1:
            raise ValueError("需要恰好一个 skinCluster（无蒙皮或多层蒙皮暂不支持）")
        self.skin = skins[0]
        selection = om.MSelectionList()
        selection.add(shape)
        self.path = selection.getDagPath(0)
        selection = om.MSelectionList()
        selection.add(self.skin)
        self.fn = oma.MFnSkinCluster(selection.getDependNode(0))
        mesh_fn = om.MFnMesh(self.path)
        self.vertices = mesh_fn.numVertices
        self.influences = self.fn.influenceObjects()
        self.width = len(self.influences)
        if not self.vertices or not self.width:
            raise ValueError("网格没有顶点或 influence")
        self.batch_size = max(1, min(2048, BLOCK_VALUES // self.width))

    def component(self, ids):
        fn = self.om.MFnSingleIndexedComponent()
        obj = fn.create(self.om.MFn.kMeshVertComponent)
        fn.addElements(ids)
        return obj

    def read(self, ids):
        weights, width = self.fn.getWeights(self.path, self.component(ids))
        if width != self.width or len(weights) != len(ids) * width:
            raise ValueError("Maya 返回的权重矩阵尺寸不匹配")
        return weights

    def write(self, ids, values):
        self.fn.setWeights(self.path, self.component(ids), self.om.MIntArray(range(self.width)),
                           self.om.MDoubleArray(values), normalize=False, returnOldWeights=False)

    def settings(self):
        plugs = [self.skin + ".normalizeWeights", self.skin + ".maintainMaxInfluences"]
        plugs.extend(p.fullPathName() + ".liw" for p in self.influences
                     if self.cmds.objExists(p.fullPathName() + ".liw"))
        return [(p, self.cmds.getAttr(p), self.cmds.getAttr(p, lock=True)) for p in plugs]

    def set_settings(self, settings, restore=False):
        errors = []
        for plug, value, locked in (reversed(settings) if restore else settings):
            try:
                if self.cmds.getAttr(plug, lock=True):
                    self.cmds.setAttr(plug, lock=False)
                self.cmds.setAttr(plug, value if restore else 0)
                if restore and locked:
                    self.cmds.setAttr(plug, lock=True)
            except Exception as exc:
                errors.append(f"{plug}: {exc}")
        if errors:
            raise RuntimeError("临时写入设置处理失败：" + "; ".join(errors))


def _patch_files(directory, patch, mesh, limit):
    index, count, width = patch["index"], patch["count"], patch["width"]
    if type(count) is not int or count < 0 or width != limit:
        raise ValueError("权重补丁参数错误")
    paths = [directory / f"mesh-{index}.{suffix}" for suffix in ("ids", "cols", "values")]
    for path, length in zip(paths, (count * 4, count * width * 4, count * width * 8)):
        if path.stat().st_size != length:
            raise ValueError("权重补丁文件长度错误")
    return paths


def _patch_batches(directory, patch, mesh, limit, paths=None):
    paths = _patch_files(directory, patch, mesh, limit) if paths is None else paths
    count, width = patch["count"], patch["width"]
    previous = -1
    with paths[0].open("rb") as ids_file, paths[1].open("rb") as cols_file, paths[2].open("rb") as val_file:
        for start in range(0, count, mesh.batch_size):
            size = min(mesh.batch_size, count - start)
            ids = list(_read(ids_file, size, "I"))
            cols = _read(cols_file, size * width, "I")
            values = _read(val_file, size * width)
            dense = array("d", [0.0]) * (size * mesh.width)
            for row, vertex in enumerate(ids):
                if not previous < vertex < mesh.vertices:
                    raise ValueError("补丁顶点编号越界、重复或顺序错误")
                previous = vertex
                columns = cols[row * width:(row + 1) * width]
                weights = values[row * width:(row + 1) * width]
                if any(c >= mesh.width for c in columns):
                    raise ValueError("补丁 influence 编号越界")
                for column, weight in zip(columns, weights):
                    dense[row * mesh.width + column] = weight
            yield ids, dense


def _original_rows(source, ids, width):
    result = array("d")
    with source.open("rb") as file:
        for vertex in ids:
            file.seek(vertex * width * 8)
            result.extend(_read(file, width))
    return result


def _verify(mesh, ids, expected):
    actual = mesh.read(ids)
    if len(actual) != len(expected) or any(not math.isfinite(a) or abs(a - b) > 1e-10
                                         for a, b in zip(actual, expected)):
        raise RuntimeError("Maya 写回后的权重与计算结果不一致")


def _apply_patch(mesh, entry, directory, patch, threshold, timings):
    source = directory / f'mesh-{entry["index"]}.weights'
    # 只确认接口所需的文件尺寸，不重新读取场景或预扫描整份补丁。
    with _stage(timings, "maya_match"):
        paths = _patch_files(directory, patch, mesh, threshold)
    if not patch["count"]:
        return 0
    settings, attempted = _timed(timings, "maya_write", mesh.settings), []
    try:
        with _stage(timings, "maya_write"):
            mesh.set_settings(settings)
            for ids, dense in _patch_batches(directory, patch, mesh, threshold, paths):
                attempted.append(ids)  # 当前批次部分写入后抛异常，也必须恢复。
                mesh.write(ids, dense)
            mesh.set_settings(settings, restore=True)
        if VERIFY_AFTER_WRITE:
            with _stage(timings, "maya_verify"):
                for ids, dense in _patch_batches(directory, patch, mesh, threshold, paths):
                    _verify(mesh, ids, dense)
    except Exception as exc:
        recovery_started = time.perf_counter()
        recovery_errors = []
        try:
            mesh.set_settings(settings)
        except Exception as recovery:
            recovery_errors.append(str(recovery))
        for ids in reversed(attempted):
            try:
                mesh.write(ids, _original_rows(source, ids, mesh.width))
            except Exception as recovery:
                recovery_errors.append(str(recovery))
        try:
            mesh.set_settings(settings, restore=True)
        except Exception as recovery:
            recovery_errors.append(str(recovery))
        for ids in attempted:
            try:
                _verify(mesh, ids, _original_rows(source, ids, mesh.width))
            except Exception as recovery:
                recovery_errors.append(str(recovery))
        timings["recovery"] = timings.get("recovery", 0.0) + time.perf_counter() - recovery_started
        if recovery_errors:
            raise RuntimeError(f"{exc}；恢复未能完成，请检查场景：{'；'.join(recovery_errors)}") from exc
        raise RuntimeError(f"{exc}；当前物体已恢复原权重和设置") from exc
    return patch["count"]


def _ranges(shape, ids):
    if not ids:
        return []
    result, start, end = [], ids[0], ids[0]
    for vertex in ids[1:]:
        if vertex == end + 1:
            end = vertex
        else:
            result.append(f"{shape}.vtx[{start}:{end}]" if end != start else f"{shape}.vtx[{start}]")
            start = end = vertex
    result.append(f"{shape}.vtx[{start}:{end}]" if end != start else f"{shape}.vtx[{start}]")
    return result


def _issue_identity(mesh):
    counts, indices = mesh.om.MFnMesh(mesh.path).getVertices()
    return (mesh.cmds.ls(mesh.shape, uuid=True)[0], mesh.cmds.ls(mesh.skin, uuid=True)[0],
            mesh.vertices, tuple(counts), tuple(indices))


def _remember_issues(job, patches):
    entries = {entry['index']: entry for entry in job['meshes']}
    if (len(patches or []) != len(entries)
            or {p['index'] for p in patches} != set(entries)):
        raise ValueError("问题顶点对象集合不完整，请重新检测")
    targets = []
    for patch in patches:
        count = patch['invalid_count']
        if type(count) is not int or count < 0:
            raise ValueError("问题顶点数量错误")
        entry = entries[patch['index']]
        mesh = entry['handle']
        path = job['directory'] / f'mesh-{patch["index"]}.issues'
        if path.stat().st_size != count * 4:
            raise ValueError("问题顶点文件长度错误")
        with path.open('rb') as file:
            ids = list(_read(file, count, 'I'))
        if ids != sorted(set(ids)) or any(v >= mesh.vertices for v in ids):
            raise ValueError("问题顶点编号错误")
        if ids:
            targets.append(dict(mesh=mesh.shape, identity=_issue_identity(mesh), ids=ids))
    token = uuid.uuid4().hex
    # 限制旧卡片/关闭未释放的检测记录数量。
    while len(_ISSUE_JOBS) >= 16:
        del _ISSUE_JOBS[next(iter(_ISSUE_JOBS))]
    _ISSUE_JOBS[token] = dict(created=time.monotonic(), targets=targets)
    return {'issue_token': token}


def _resolve_issue_mesh(target, cmds):
    names = cmds.ls(target['identity'][0], long=True) or []
    if len(names) != 1:
        raise ValueError("检测对象已删除或替换，请重新检测")
    mesh = _Mesh(names[0])
    if _issue_identity(mesh) != target['identity']:
        raise ValueError("模型拓扑或蒙皮已改变，请重新检测")
    return mesh


def _invalid_issue_ids(mesh, ids):
    invalid = []
    for start in range(0, len(ids), mesh.batch_size):
        batch = ids[start:start + mesh.batch_size]
        weights = mesh.read(batch)
        for row, vertex in enumerate(batch):
            values = weights[row * mesh.width:(row + 1) * mesh.width]
            if (any(not math.isfinite(w) or w < 0 for w in values)
                    or not any(w > 0 for w in values)):
                invalid.append(vertex)
    return invalid


def _redistribute_negative(values):
    """负项清零，从现有正项等额扣减；小项扣尽后继续分摊，不量化。"""
    if any(not math.isfinite(w) for w in values) or not any(w > 0 for w in values):
        return None
    if not any(w < 0 for w in values):
        return None
    try:
        total = math.fsum(values)
        if total <= 0:
            return None  # 无法保持总和且生成有效权重，交给锤式修复。
        active = [i for i, w in enumerate(values) if w > 0]
        result = [0.0] * len(values)
        while active:
            if len(active) == 1:
                result[active[0]] = total
                return result
            # 每轮由原总和计算余额，避免反复扣减累计误差。
            share = math.fsum([values[i] for i in active] + [-total]) / len(active)
            survivors = [i for i in active if values[i] > share]
            if len(survivors) == len(active):
                for i in active:
                    result[i] = values[i] - share
                # 双精度舍入余量交给最大项，原本为零的影响不参与。
                largest = max(active, key=lambda i: result[i])
                result[largest] += total - math.fsum(result)
                return result
            active = survivors
    except OverflowError:
        return None
    return None


def _operate_negative(meshes, errors, token, cmds):
    attempted, repaired, remaining, unverified = 0, 0, 0, 0
    cmds.undoInfo(openChunk=True, chunkName='QzRepairNegativeSkinWeights')
    try:
        for mesh, ids in meshes:
            settings = None
            try:
                names = [p.fullPathName() for p in mesh.influences]
                try:
                    for start in range(0, len(ids), mesh.batch_size):
                        batch = ids[start:start + mesh.batch_size]
                        weights = mesh.read(batch)
                        for row, vertex in enumerate(batch):
                            values = weights[row * mesh.width:(row + 1) * mesh.width]
                            if (all(math.isfinite(w) and w >= 0 for w in values)
                                    and any(w > 0 for w in values)):
                                continue  # 已手工修好的点不再修改。
                            fixed = _redistribute_negative(values)
                            if fixed is None:
                                remaining += 1  # 交由独立的锤式按钮处理，不自动修改。
                                continue
                            if settings is None:
                                settings = mesh.settings()
                                mesh.set_settings(settings)
                            attempted += 1
                            try:
                                # 原生命令支持 Undo；显式目标，不选择或查询邻居。
                                cmds.skinPercent(mesh.skin, f'{mesh.shape}.vtx[{vertex}]',
                                                 transformValue=list(zip(names, fixed)), normalize=False)
                            except Exception:
                                unverified += 1
                                raise
                            repaired += 1
                finally:
                    if settings is not None:
                        mesh.set_settings(settings, restore=True)
            except Exception as exc:
                errors.append(dict(mesh=mesh.shape,
                                   error=f"负权重修复未完成，可能已有部分修改，可撤销后重新检测：{exc}"))
    finally:
        try:
            cmds.undoInfo(closeChunk=True)
        finally:
            _ISSUE_JOBS.pop(token, None)
    return dict(count=repaired, attempted=attempted, remaining=remaining,
                unverified=unverified, errors=errors)


def _operate_issues(op, token, cmds):
    job = _ISSUE_JOBS.get(token)
    if job is None:
        raise ValueError("问题顶点记录不存在或已过期，请重新检测")
    errors, selection, meshes = [], [], []
    for target in job['targets']:
        try:
            mesh = _resolve_issue_mesh(target, cmds)
            meshes.append((mesh, target['ids']))
            selection.extend(_ranges(mesh.shape, target['ids']))
        except Exception as exc:
            errors.append(dict(mesh=target['mesh'], error=str(exc)))
    if op == 'select_issues':
        if selection:
            cmds.select(selection, replace=True)
        return dict(count=sum(len(ids) for _, ids in meshes), errors=errors)
    if op == 'fix_negative':
        return _operate_negative(meshes, errors, token, cmds)
    from maya import mel
    attempted, repaired, remaining, unverified = 0, 0, 0, 0
    previous = cmds.ls(selection=True, long=True) or []
    cmds.undoInfo(openChunk=True, chunkName='QzHammerProblemSkinWeights')
    try:
        for mesh, ids in meshes:
            active = []
            try:
                # 只锤仍有数值异常的已检测顶点，手工修好的顶点不再改写。
                active = _invalid_issue_ids(mesh, ids)
                if not active:
                    continue
                cmds.select(_ranges(mesh.shape, active), replace=True)
                attempted += len(active)
                mel.eval('weightHammerVerts;')
                left = _invalid_issue_ids(mesh, active)
                remaining += len(left)
                repaired += len(active) - len(left)
            except Exception as exc:
                unverified += len(active)
                errors.append(dict(mesh=mesh.shape,
                                   error=f"锤式修复未完成，可能已有部分修改，请重新检测：{exc}"))
    finally:
        try:
            if previous:
                cmds.select(previous, replace=True)
            else:
                cmds.select(clear=True)
        finally:
            cmds.undoInfo(closeChunk=True)
        # 修复可能部分完成，禁止重复消费旧检测结果。
        _ISSUE_JOBS.pop(token, None)
    return dict(count=repaired, attempted=attempted, remaining=remaining,
                unverified=unverified, errors=errors)


@register_action("skin_weights")
def skin_weights(op="snapshot", directory=None, token=None, patches=None, threshold=4):
    started, timings = time.perf_counter(), {}
    result = _skin_weights(op, directory, token, patches, threshold, timings)
    elapsed = time.perf_counter() - started
    timings["maya_other"] = max(0.0, elapsed - sum(timings.values()))
    result["action_seconds"] = elapsed
    result["timings_seconds"] = timings
    return result


def _skin_weights(op, directory, token, patches, threshold, timings):
    from maya import cmds
    # reload 或异常断连之后，旧任务不无限保留；数值文件由中台临时目录管理。
    for old in list(_JOBS):
        if time.monotonic() - _JOBS[old]["created"] > 1800:
            del _JOBS[old]
    for old in list(_ISSUE_JOBS):
        if time.monotonic() - _ISSUE_JOBS[old]['created'] > 1800:
            del _ISSUE_JOBS[old]
    if op in ('select_issues', 'fix_negative', 'hammer_issues'):
        return _operate_issues(op, token, cmds)
    if op == 'remember_issues':
        if token not in _JOBS:
            raise ValueError("蒙皮任务不存在或已过期，请重新检测")
        return _remember_issues(_JOBS[token], patches)
    if op == "snapshot":
        path = Path(directory).resolve()
        if not path.is_dir() or not path.name.startswith("qz-skin-"):
            raise ValueError("无效的蒙皮任务临时目录")
        shapes, errors = _selected_meshes(cmds)
        entries, public = [], []
        for shape in shapes:
            try:
                mesh = _timed(timings, "maya_read", _Mesh, shape)
                index = len(entries)
                file = _timed(timings, "maya_file_write", (path / f"mesh-{index}.weights").open, "wb")
                try:
                    for start in range(0, mesh.vertices, mesh.batch_size):
                        ids = list(range(start, min(mesh.vertices, start + mesh.batch_size)))
                        weights = _timed(timings, "maya_read", mesh.read, ids)
                        _write_snapshot(file, weights, timings)
                finally:
                    _timed(timings, "maya_file_write", file.close)
                # 固定操作开始时的 Maya 目标，不随当前选择变化重新解析。
                entry = {"index": index, "mesh": shape, "handle": mesh}
                entries.append(entry)
                public.append({"index": index, "mesh": shape, "vertices": mesh.vertices,
                               "influences": mesh.width})
            except Exception as exc:
                errors.append({"mesh": shape, "error": str(exc)})
        if not entries:
            return {"count": 0, "meshes": [], "errors": errors,
                    "warning": "未选中可处理的蒙皮网格"}
        token = uuid.uuid4().hex
        _JOBS[token] = {"created": time.monotonic(), "directory": path, "meshes": entries}
        return {"token": token, "meshes": public, "errors": errors}
    if op == "release":
        _JOBS.pop(token, None)
        return {"count": 0}
    if op not in ("limit", "select_excess") or token not in _JOBS:
        raise ValueError("蒙皮任务不存在或已过期，请重新检测")
    if type(threshold) is not int or threshold < 1:
        raise ValueError("最大影响数必须为正整数")
    job = _JOBS[token]
    entries = {entry["index"]: entry for entry in job["meshes"]}
    indices = [patch["index"] for patch in (patches or [])]
    if len(indices) != len(entries) or set(indices) != set(entries):
        raise ValueError("补丁对象集合不完整、重复或未知，未开始修改")
    _JOBS.pop(token)  # 一次性任务，禁止重复应用旧补丁。
    seen, errors, selection, count = set(), [], [], 0
    for patch in patches or []:
        index = patch["index"]
        if index in seen or index not in entries:
            raise ValueError("重复或未知的蒙皮补丁")
        seen.add(index)
        entry = entries[index]
        try:
            mesh = entry["handle"]
            if op == "limit":
                count += _apply_patch(mesh, entry, job["directory"], patch, threshold, timings)
            else:
                with _stage(timings, "maya_match"):
                    file_path = job["directory"] / f"mesh-{index}.ids"
                    if type(patch["count"]) is not int or patch["count"] < 0 or file_path.stat().st_size != patch["count"] * 4:
                        raise ValueError("顶点选择文件长度错误")
                    with file_path.open("rb") as file:
                        ids = list(_read(file, patch["count"], "I"))
                    if any(v >= mesh.vertices for v in ids) or ids != sorted(set(ids)):
                        raise ValueError("顶点选择编号错误")
                    selection.extend(_ranges(mesh.shape, ids))
                    count += len(ids)
        except Exception as exc:
            errors.append({"mesh": entry["mesh"], "error": str(exc)})
    if selection:
        _timed(timings, "maya_select", cmds.select, selection, replace=True)
    return {"count": count, "errors": errors}
