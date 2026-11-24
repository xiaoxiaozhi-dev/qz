"""中台项目权重文件的 Maya 动作：恢复完整权重、DQ 和源蒙皮方式。"""
from array import array
from pathlib import Path

from maya_bridge import register_action
from maya_actions.skin_transfer import export_max
from maya_actions import skin_weight_geometry as geometry
from maya_actions.skin_weights import _Mesh, _read, _selected_meshes, _ranges


def _name(path):
    return path.rsplit("|", 1)[-1]


def _bone_key(name):
    return name.replace("FBXASC032", " ")


def _scene_bones(cmds):
    groups = {}
    nodes = set(cmds.ls(type="joint", long=True) or [])
    nodes.update(cmds.ls(type="transform", long=True) or [])
    for node in nodes:
        groups.setdefault(_bone_key(_name(node)), []).append(node)
    return groups


def _resolve_bones(bones, groups):
    mapping, seen, problems = {}, set(), []
    for bone in bones:
        key = _bone_key(bone["name"])
        matches = groups.get(key, [])
        if len(matches) != 1:
            reason = "场景缺少骨骼" if not matches else "场景骨骼名称不唯一"
            problems.append(f'{reason}：{bone["name"]}')
            continue
        if matches[0] in seen:
            raise ValueError(f'文件骨骼名称解码后重复：{bone["name"]}')
        seen.add(matches[0])
        mapping[bone["index"]] = matches[0]
    if problems:
        raise ValueError("；".join(problems))
    return mapping


def _write_rows(mesh, entry, directory, mapping):
    paths = {suffix: directory / f'mesh-{entry["index"]}.{suffix}'
             for suffix in ("offsets", "cols", "values", "dq")}
    lengths = dict(offsets=(entry["vertices"] + 1) * 4, cols=entry["nonzero"] * 4,
                   values=entry["nonzero"] * 8, dq=entry["vertices"] * 8)
    if any(path.stat().st_size != lengths[suffix] for suffix, path in paths.items()):
        raise ValueError("权重交换文件长度不匹配")
    with paths["offsets"].open("rb") as offsets_file, paths["cols"].open("rb") as cols_file, \
            paths["values"].open("rb") as values_file, paths["dq"].open("rb") as dq_file:
        for start in range(0, mesh.vertices, mesh.batch_size):
            size = min(mesh.batch_size, mesh.vertices - start)
            offsets_file.seek(start * 4)
            offsets = _read(offsets_file, size + 1, "I")
            count = offsets[-1] - offsets[0]
            columns, values = _read(cols_file, count, "I"), _read(values_file, count)
            dense = array("d", [0.]) * (size * mesh.width)
            for row in range(size):
                for pos in range(offsets[row] - offsets[0], offsets[row + 1] - offsets[0]):
                    dense[row * mesh.width + mapping[columns[pos]]] = values[pos]
            ids = list(range(start, start + size))
            mesh.write(ids, dense)
            mesh.fn.setBlendWeights(mesh.path, mesh.component(ids), mesh.om.MDoubleArray(_read(dq_file, size)))


def _interpolated_components(directory, entry):
    if entry.get("transfer_mode") != "closest_point":
        return []
    count = entry.get("interpolated_vertices", 0)
    if type(count) is not int or not 0 <= count <= entry["vertices"]:
        raise ValueError("表面插值顶点数量无效")
    path = directory / f'mesh-{entry["index"]}.interpolated_ids'
    if path.stat().st_size != count * 4:
        raise ValueError("表面插值顶点文件长度不匹配，请重载 Maya 动作库后重试")
    with path.open("rb") as file:
        ids = _read(file, count, "I")
    if any(vertex >= entry["vertices"] for vertex in ids) or any(a >= b for a,b in zip(ids, ids[1:])):
        raise ValueError("表面插值顶点编号越界、重复或顺序无效")
    return _ranges(entry["mesh"], ids)


def _interpolated_set(cmds, entry, components):
    # 用 message 连接认领本插件的记录，避免覆盖用户同名 set；模型改名后仍可复用。
    owner_attr = "qzSkinWeightMesh"
    destinations = cmds.listConnections(entry["mesh"] + ".message", source=False,
                                        destination=True, type="objectSet", plugs=True) or []
    node = next((plug.rsplit(".", 1)[0] for plug in destinations
                 if plug.endswith("." + owner_attr)), None)
    created = node is None
    try:
        if created:
            node = cmds.sets(empty=True, name=entry["name"] + "_skinInterpolatedVertices_set")
            cmds.addAttr(node, longName=owner_attr, attributeType="message", hidden=True)
            cmds.connectAttr(entry["mesh"] + ".message", node + "." + owner_attr)
        cmds.sets(clear=node)
        if components:
            cmds.sets(components, addElement=node)
        return node
    except Exception:
        if created and node:
            cmds.delete(node)
        raise


def _import_one(cmds, directory, entry, groups):
    # 缺骨骼、顶点数和多层蒙皮在任何场景写入前集中检查。
    method = entry.get("skinning_method")
    if method is not None and (type(method) is not int or method not in (0, 1, 2)):
        raise ValueError("权重文件蒙皮方式无效")
    resolved = _resolve_bones(entry["bones"], groups)
    shape = entry["mesh"]
    if cmds.polyEvaluate(shape, vertex=True) != entry["vertices"]:
        raise ValueError("当前模型顶点数与权重文件不一致")
    skins = list(dict.fromkeys(n for n in (cmds.listHistory(shape, pruneDagObjects=True) or [])
                               if cmds.nodeType(n) == "skinCluster"))
    if len(skins) > 1:
        raise ValueError("多层 skinCluster 暂不支持")
    bone_settings = [(node + ".liw", cmds.getAttr(node + ".liw"), cmds.getAttr(node + ".liw", lock=True))
                     for node in resolved.values() if cmds.objExists(node + ".liw")]
    created, settings, mesh, changed = None, [], None, False
    try:
        if not skins:
            # 新建绑定也会设置 liw；原来锁住的骨骼属性必须先暂时解除。
            for plug, _, locked in bone_settings:
                if locked:
                    cmds.setAttr(plug, lock=False)
                cmds.setAttr(plug, 0)
            created = cmds.skinCluster(list(resolved.values()), shape, toSelectedBones=True, normalizeWeights=1)[0]
            changed = True
        mesh = _Mesh(shape)
        initial = {item[0]: item for item in bone_settings}
        settings = [initial.get(item[0], item) for item in mesh.settings()]
        known = {item[0] for item in settings}
        settings.extend(item for item in bone_settings if item[0] not in known)
        mesh.set_settings(settings)
        existing = {bone.fullPathName() for bone in mesh.influences}
        missing = [bone for bone in resolved.values() if bone not in existing]
        for bone in missing:
            changed = True
            cmds.skinCluster(mesh.skin, edit=True, addInfluence=bone, weight=0., lockWeights=False)
        if missing:
            mesh = _Mesh(shape)
            original_plugs = {plug for plug, _, _ in settings}
            settings.extend(item for item in mesh.settings() if item[0] not in original_plugs)
            mesh.set_settings(settings)
        target = {bone.fullPathName(): index for index, bone in enumerate(mesh.influences)}
        mapping = {column: target[node] for column, node in resolved.items()}
        changed = True
        _write_rows(mesh, entry, directory, mapping)
        if method is not None:
            plug = mesh.skin + ".skinningMethod"
            locked = cmds.getAttr(plug, lock=True)
            try:
                if locked:
                    cmds.setAttr(plug, lock=False)
                cmds.setAttr(plug, method)
            finally:
                if locked:
                    cmds.setAttr(plug, lock=True)
        mesh.set_settings(settings, restore=True)
        return dict(name=entry["name"], vertices=entry["vertices"], added_bones=len(missing),
                    created_skin=bool(created), skinning_method=method,
                    transfer_mode=entry.get("transfer_mode", "vertex_id"),
                    direct_vertices=entry.get("direct_vertices", 0),
                    interpolated_vertices=entry.get("interpolated_vertices", 0))
    except Exception as exc:
        recovery = []
        if mesh is not None and settings:
            try:
                mesh.set_settings(settings, restore=True)
            except Exception as error:
                recovery.append(f"恢复锁定和蒙皮设置失败：{error}")
        if mesh is None:
            for plug, value, locked in reversed(bone_settings):
                try:
                    if cmds.getAttr(plug, lock=True):
                        cmds.setAttr(plug, lock=False)
                    cmds.setAttr(plug, value)
                    if locked:
                        cmds.setAttr(plug, lock=True)
                except Exception as error:
                    recovery.append(f"恢复骨骼锁定失败：{plug}: {error}")
        if created:
            try:
                cmds.delete(created)
                recovery.append("本次新建的 skinCluster 已移除")
            except Exception as error:
                recovery.append(f"移除新建 skinCluster 失败：{error}")
        elif changed:
            recovery.append("已有蒙皮可能部分更新，请检查该模型；修正错误后可重新导入")
        if recovery:
            raise RuntimeError(str(exc) + "；" + "；".join(recovery)) from exc
        raise


@register_action("skin_weights_files")
def skin_weights_files(op="context", directory=None, meshes=None, position_decimals=3):
    from maya import cmds
    if op in ("import", "map"):
        path = Path(directory).resolve()
        if not path.is_dir() or not path.name.startswith("qz-skin-transfer-"):
            raise ValueError("无效的权重导入交换目录")
        if op == "map":
            return geometry.map_geometry(path, meshes or [], position_decimals)
        reports, errors = [], []
        selection, closest_succeeded, selected_vertices, selection_changed = [], False, 0, False
        previous = cmds.ls(selection=True, long=True) or []
        groups = _scene_bones(cmds)
        try:
            for entry in meshes or []:
                try:
                    components = _interpolated_components(path, entry)
                    report = _import_one(cmds, path, entry, groups)
                    reports.append(report)
                    if entry.get("transfer_mode") == "closest_point":
                        closest_succeeded = True
                        selection.extend(components)
                        selected_vertices += entry.get("interpolated_vertices", 0)
                        try:
                            report["interpolation_set"] = _interpolated_set(cmds, entry, components)
                        except Exception as exc:
                            errors.append(dict(mesh=entry["mesh"], error=f"权重已导入，但更新插值顶点选择集失败：{exc}",
                                               status="failed"))
                except Exception as exc:
                    errors.append(dict(mesh=entry["mesh"], error=str(exc),
                                       status="skipped" if isinstance(exc, ValueError) else "failed"))
        finally:
            try:
                if closest_succeeded:
                    cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
                    selection_changed = True
                else:
                    cmds.select(previous, replace=True) if previous else cmds.select(clear=True)
            except Exception as exc:
                errors.append(dict(mesh="选择", error=f"设置导入后选择失败：{exc}", status="failed"))
        return dict(reports=reports, errors=errors, selection_changed=selection_changed,
                    selected_vertices=selected_vertices if selection_changed else 0)
    if op not in ("context", "export"):
        raise ValueError("未知权重文件动作")
    if op == "export":
        result = export_max(directory)
        result["meshes"], errors = geometry.export_geometry(directory, result.get("meshes", []))
        result.setdefault("errors", []).extend(errors)
    else:
        shapes, errors = _selected_meshes(cmds)
        targets = []
        for index, shape in enumerate(shapes):
            try:
                parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                if len(parents) != 1:
                    raise ValueError("无法确定模型名称")
                targets.append(dict(index=index, mesh=shape, name=_name(parents[0]),
                                    vertices=int(cmds.polyEvaluate(shape, vertex=True))))
            except Exception as exc:
                errors.append(dict(mesh=shape, error=str(exc), status="skipped"))
        result = dict(meshes=targets, errors=errors)
    return result
