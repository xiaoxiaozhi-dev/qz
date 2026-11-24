"""JS 动作 JSON 的 Maya 端实现。下划线前缀避免被当作独立 action 自动加载。

Maya 依赖延迟加载，桥接注册和文件格式校验不要求安装 Maya。
旧文件保留原帧号和 Maya 默认切线；新版逐帧采样并明确使用线性插值。
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
import tempfile
import uuid


CHANNELS = {
    "tx": "translateX", "ty": "translateY", "tz": "translateZ",
    "rx": "rotateX", "ry": "rotateY", "rz": "rotateZ",
}
LAYER_PREFIX = "AJT_"


def _cmds():
    import maya.cmds as cmds
    return cmds


def short_name(node):
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s 必须是有限数字" % label)
    return value


def read_animation(json_path):
    """先完整校验，再允许场景修改；不改写旧文件或补造旧文件缺失的数据。"""
    with open(json_path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict) or not data["nodes"]:
        raise ValueError("不是有效的动作 JSON：需要非空 nodes 字典")
    version = data.get("format_version", 1)
    if version not in (1, 2):
        raise ValueError("不支持的动作格式版本：%r" % version)
    first, last = math.inf, -math.inf
    for name, node in data["nodes"].items():
        if not isinstance(name, str) or not name or not isinstance(node, dict):
            raise ValueError("无效的节点记录：%r" % name)
        if node.get("parent") is not None and not isinstance(node["parent"], str):
            raise ValueError("%s 的 parent 必须是名称或 null" % name)
        channels = node.get("ch", {})
        if not isinstance(channels, dict):
            raise ValueError("%s 的 ch 必须是字典" % name)
        for channel, keys in channels.items():
            if channel not in CHANNELS:
                raise ValueError("%s 包含不支持的通道 %s" % (name, channel))
            if not isinstance(keys, list) or not keys:
                raise ValueError("%s.%s 的关键帧列表为空或无效" % (name, channel))
            previous = -math.inf
            for key in keys:
                if not isinstance(key, (list, tuple)) or len(key) != 2:
                    raise ValueError("%s.%s 的关键帧必须是 [帧, 值]" % (name, channel))
                frame = _number(key[0], "帧号")
                _number(key[1], "通道值")
                if frame <= previous:
                    raise ValueError("%s.%s 的帧号必须递增且不重复" % (name, channel))
                previous = frame
                first, last = min(first, frame), max(last, frame)
    if not math.isfinite(first):
        raise ValueError("动作 JSON 没有关键帧")
    start = _number(data.get("start_frame", first), "起始帧")
    end = _number(data.get("end_frame", last), "结束帧")
    if start > first or end < last or start > end:
        raise ValueError("动作帧范围没有包含全部关键帧")
    data["start_frame"], data["end_frame"] = start, end
    return data


def _root(cmds, root_node=None, for_export=False):
    if root_node:
        roots = cmds.ls(root_node, long=True, type="transform") or []
    else:
        roots = cmds.ls(selection=True, long=True, type="transform") or []
        if not roots and for_export:
            roots = cmds.ls("Armature", long=True, type="transform") or []
    if len(roots) != 1:
        raise ValueError("请在 Maya 中选择一个骨架根节点，或填写唯一的根节点路径")
    return roots[0]


def selected_root():
    return {"root_node": _root(_cmds())}


def _hierarchy(cmds, root):
    return [root] + sorted(cmds.listRelatives(
        root, allDescendents=True, type="transform", fullPath=True) or [])


def _parent_name(cmds, node):
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    # 旧版仅记录 joint 父级；transform 包装组仍按 null 匹配。
    if parents and cmds.objectType(parents[0]) == "joint":
        return short_name(parents[0])
    return None


def _scene_map(cmds, root):
    mapping = {}
    for node in _hierarchy(cmds, root):
        mapping.setdefault(short_name(node), []).append(node)
    return mapping


def _import_plan(cmds, data, root):
    mapping = _scene_map(cmds, root)
    matched, skipped = [], []
    for name, record in data["nodes"].items():
        targets = mapping.get(name, [])
        if len(targets) > 1:
            raise ValueError("骨架中存在重名节点，无法确定目标：%s" % name)
        if not targets:
            skipped.append("%s（未找到）" % name)
        elif record.get("parent") != _parent_name(cmds, targets[0]):
            skipped.append("%s（父级不匹配）" % name)
        else:
            for attr in CHANNELS.values():
                if cmds.getAttr(targets[0] + "." + attr, lock=True):
                    raise ValueError("目标通道已锁定：%s.%s" % (targets[0], attr))
            matched.append((targets[0], record))
    if not matched:
        raise ValueError("没有可匹配节点，请检查所选根节点、节点命名和骨架结构")
    return matched, skipped


def _attr(cmds, node, name, default=None):
    if cmds.attributeQuery(name, node=node, exists=True):
        return cmds.getAttr(node + "." + name)
    return default


def _write_attr(cmds, node, name, value):
    string = isinstance(value, str)
    if not cmds.attributeQuery(name, node=node, exists=True):
        if string:
            cmds.addAttr(node, longName=name, dataType="string")
        else:
            cmds.addAttr(node, longName=name, attributeType="double")
    if string:
        cmds.setAttr(node + "." + name, value, type="string")
    else:
        cmds.setAttr(node + "." + name, value)


def _layers(cmds):
    return sorted(layer for layer in (cmds.ls(type="animLayer") or [])
                  if layer.startswith(LAYER_PREFIX))


def _base_range(cmds):
    for layer in _layers(cmds):
        if cmds.attributeQuery("qzBaseStart", node=layer, exists=True):
            return (_attr(cmds, layer, "qzBaseStart"), _attr(cmds, layer, "qzBaseEnd"),
                    _attr(cmds, layer, "qzBaseTime", 0))
    return (cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True), cmds.currentTime(q=True))


def list_layers():
    cmds = _cmds()
    records = []
    for layer in _layers(cmds):
        records.append({
            "layer": layer, "name": _attr(cmds, layer, "displayName", layer),
            "start": _attr(cmds, layer, "startFrame", 1),
            "end": _attr(cmds, layer, "endFrame", 100),
            "weight": cmds.animLayer(layer, q=True, weight=True),
            "root": _attr(cmds, layer, "qzRoot", ""),
        })
    return {"layers": records, "scene": cmds.file(q=True, sn=True)}


@contextmanager
def _scene_edit(cmds, label):
    if not cmds.undoInfo(q=True, state=True):
        raise RuntimeError("请先开启 Maya 撤销，以保证导入失败时能恢复场景")
    selection = cmds.ls(selection=True, long=True) or []
    old_range = (cmds.playbackOptions(q=True, minTime=True),
                 cmds.playbackOptions(q=True, maxTime=True), cmds.currentTime(q=True))
    marker = None
    failed = False
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        # 保证块内至少有一个可撤销命令，失败时不会撤销用户之前的操作。
        marker = cmds.createNode("network", name=":qzAnimTransaction#", skipSelect=True)
        yield
        remaining = [node for node in selection if cmds.objExists(node)]
        if (cmds.ls(selection=True, long=True) or []) != remaining:
            cmds.select(remaining, replace=True) if remaining else cmds.select(clear=True)
        cmds.delete(marker)
    except Exception:
        failed = True
        raise
    finally:
        cmds.undoInfo(closeChunk=True)
        if failed and marker is not None:
            cmds.undo()
            if (cmds.playbackOptions(q=True, minTime=True), cmds.playbackOptions(q=True, maxTime=True)) != old_range[:2]:
                cmds.playbackOptions(minTime=old_range[0], maxTime=old_range[1])
            if cmds.currentTime(q=True) != old_range[2]:
                cmds.currentTime(old_range[2])


def _activate(cmds, layer=None, base_range=None):
    if layer is not None and layer not in _layers(cmds):
        raise ValueError("动作图层不存在：%s" % layer)
    base_range = base_range or _base_range(cmds)
    for item in _layers(cmds):
        on = item == layer
        cmds.animLayer(item, edit=True, weight=1.0 if on else 0.0,
                       selected=on, preferred=on)
        if on:
            cmds.animLayer(item, edit=True, mute=False)
    if layer:
        start, end = _attr(cmds, layer, "startFrame", 1), _attr(cmds, layer, "endFrame", 100)
        time = start
    else:
        start, end, time = base_range
    cmds.playbackOptions(minTime=start, maxTime=end)
    cmds.currentTime(time)


def activate_layer(layer=None):
    cmds = _cmds()
    with _scene_edit(cmds, "JS 动作切换"):
        _activate(cmds, layer)
    return list_layers()


def delete_layer(layer):
    cmds = _cmds()
    if layer not in _layers(cmds):
        raise ValueError("只能删除 JS 动作工具管理的图层")
    base_range = _base_range(cmds)
    with _scene_edit(cmds, "JS 删除动作图层"):
        cmds.delete(layer)
        _activate(cmds, base_range=base_range)
    return list_layers()


def import_animation(json_path, root_node=None):
    data = read_animation(json_path)
    cmds = _cmds()
    root = _root(cmds, root_node)
    matched, skipped = _import_plan(cmds, data, root)
    # 旧 JSON 没有单位信息，继续按当前 Maya 单位解释。
    for key, flag in (("time_unit", "time"), ("linear_unit", "linear"), ("angular_unit", "angle")):
        if data.get(key) and data[key] != cmds.currentUnit(q=True, **{flag: True}):
            raise ValueError("动作的 %s 与当前场景不同，请先统一单位" % key)
    name = os.path.splitext(os.path.basename(json_path))[0]
    layer = LAYER_PREFIX + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    if cmds.objExists(layer):
        if layer not in _layers(cmds):
            raise ValueError("同名节点不是动画图层：%s" % layer)
        bound_root = _attr(cmds, layer, "qzRoot", "")
        if bound_root and bound_root != root:
            raise ValueError("同名动作已用于其他骨架，请先删除旧动作图层或使用不同动作名")
    base_start, base_end, base_time = _base_range(cmds)
    total_keys = 0
    with _scene_edit(cmds, "JS 导入动作"):
        temporary = cmds.animLayer(":AJT_import_" + uuid.uuid4().hex[:12], override=True)
        cmds.animLayer(temporary, edit=True, lock=False, mute=False, selected=True, preferred=True, weight=1.0)
        for node, record in matched:
            for channel, attr in CHANNELS.items():
                cmds.animLayer(temporary, edit=True, attribute=node + "." + attr)
                keys = record.get("ch", {}).get(channel, [[data["start_frame"], 0]])
                for frame, value in keys:
                    options = {}
                    if data.get("interpolation") == "linear":
                        options.update(inTangentType="linear", outTangentType="linear")
                    count = cmds.setKeyframe(node, attribute=attr, value=value, time=frame,
                                             animLayer=temporary, noResolve=True, **options)
                    if not count:
                        raise RuntimeError("关键帧写入失败：%s.%s @ %s" % (node, attr, frame))
                    total_keys += count
        for key, value in {
            "displayName": name, "startFrame": data["start_frame"],
            "endFrame": data["end_frame"], "qzRoot": root,
            "qzSourcePath": os.path.abspath(json_path), "qzBaseStart": base_start,
            "qzBaseEnd": base_end, "qzBaseTime": base_time,
        }.items():
            _write_attr(cmds, temporary, key, value)
        if cmds.objExists(layer):
            cmds.delete(layer)
        layer = cmds.rename(temporary, ":" + layer)
        _activate(cmds, layer)
    result = list_layers()
    result.update(layer=layer, name=name, imported_nodes=len(matched),
                  skipped=skipped, total_keys=total_keys, root_node=root)
    return result


def compress_samples(samples):
    """仅移除常值区间内部帧，保留平台两端和每次变化，不丢累计小变化。"""
    if not samples:
        return []
    if all(value == samples[0][1] for _, value in samples):
        return [samples[0]]
    result = [samples[0]]
    for index in range(1, len(samples)):
        if samples[index][1] != samples[index - 1][1]:
            if result[-1][0] != samples[index - 1][0]:
                result.append(samples[index - 1])
            result.append(samples[index])
    if result[-1][0] != samples[-1][0]:
        result.append(samples[-1])
    return result


def _write_json_atomic(path, data, overwrite):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".qz_anim_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if overwrite:
            os.replace(temporary, path)
        else:
            # Windows rename 不覆盖已有文件，避免在漫长采样后覆盖新出现的文件。
            if os.path.exists(path):
                raise FileExistsError("动作文件已存在：%s" % path)
            os.rename(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_animation(json_path, root_node=None, overwrite=False):
    cmds = _cmds()
    root = _root(cmds, root_node, for_export=True)
    if not str(json_path).lower().endswith(".json"):
        raise ValueError("输出文件必须使用 .json 扩展名")
    if os.path.exists(json_path) and not overwrite:
        raise FileExistsError("动作文件已存在：%s" % json_path)
    mapping = _scene_map(cmds, root)
    duplicates = [name for name, nodes in mapping.items() if len(nodes) > 1]
    if duplicates:
        raise ValueError("骨架中存在重名节点：%s" % ", ".join(duplicates))
    start = cmds.playbackOptions(q=True, minTime=True)
    end = cmds.playbackOptions(q=True, maxTime=True)
    frames = [start + i for i in range(int(math.floor(end - start)) + 1)]
    if frames[-1] != end:
        frames.append(end)
    data = {"format_version": 2, "start_frame": start, "end_frame": end,
            "time_unit": cmds.currentUnit(q=True, time=True),
            "linear_unit": cmds.currentUnit(q=True, linear=True),
            "angular_unit": cmds.currentUnit(q=True, angle=True),
            "interpolation": "linear", "nodes": {}}
    samples = {name: {channel: [] for channel in CHANNELS} for name in mapping}
    previous_time = cmds.currentTime(q=True)
    try:
        # 按时间正向评估，约束/表达式等间接动画同样采样；不删除曲线或改缩放。
        for frame in frames:
            cmds.currentTime(frame, edit=True, update=True)
            for name, nodes in mapping.items():
                for channel, attr in CHANNELS.items():
                    value = _number(cmds.getAttr(nodes[0] + "." + attr), name + "." + attr)
                    samples[name][channel].append([frame, round(value, 6)])
        for name, channels in samples.items():
            data["nodes"][name] = {
                "parent": _parent_name(cmds, mapping[name][0]),
                "ch": {channel: compress_samples(values) for channel, values in channels.items()},
            }
    finally:
        cmds.currentTime(previous_time, edit=True)
    _write_json_atomic(json_path, data, overwrite)
    return {"json_path": os.path.abspath(json_path), "node_count": len(mapping),
            "frame_count": len(frames), "start": start, "end": end}


def export_fbx(fbx_path, output_folder, overwrite=False):
    """一次处理一个 FBX 并恢复原场景，供中台逐项显示进度。"""
    cmds = _cmds()
    if not os.path.isfile(fbx_path) or not fbx_path.lower().endswith(".fbx"):
        raise ValueError("FBX 文件不存在：%s" % fbx_path)
    if cmds.file(q=True, modified=True):
        raise RuntimeError("请先保存当前 Maya 场景，再执行 FBX 批量导出")
    destination = os.path.join(output_folder, os.path.splitext(os.path.basename(fbx_path))[0] + ".json")
    if os.path.exists(destination) and not overwrite:
        raise FileExistsError("动作文件已存在：%s" % destination)
    original = cmds.file(q=True, sn=True)
    if original and not os.path.isfile(original):
        raise RuntimeError("当前场景文件不存在，无法在批处理后恢复，请先另存场景")
    selection = cmds.ls(selection=True, long=True) or []
    timeline = (cmds.playbackOptions(q=True, minTime=True),
                cmds.playbackOptions(q=True, maxTime=True), cmds.currentTime(q=True))
    if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)
    try:
        cmds.file(fbx_path, open=True, force=True, ignoreVersion=True)
        roots = cmds.ls("Armature", long=True, type="transform") or []
        if len(roots) != 1:
            raise ValueError("FBX 中没有唯一的 Armature 根节点")
        return export_animation(destination, root_node=roots[0], overwrite=overwrite)
    finally:
        if original:
            cmds.file(original, open=True, force=True, ignoreVersion=True)
        else:
            cmds.file(new=True, force=True)
        restored_modified = cmds.file(q=True, modified=True)
        cmds.playbackOptions(minTime=timeline[0], maxTime=timeline[1])
        cmds.currentTime(timeline[2])
        if selection:
            cmds.select(selection, replace=True)
        cmds.file(modified=restored_modified)
