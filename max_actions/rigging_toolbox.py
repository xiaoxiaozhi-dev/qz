# -*- coding: utf-8 -*-
"""Max 2020 / Python 2.7 binding utilities, executed on the main thread."""
from __future__ import unicode_literals

from contextlib import contextmanager
import math
import os
import sys
import time
import types

from max_bridge.server import register_action

try:
    text_type = unicode
except NameError:
    text_type = str

VERSION = 10
OPERATIONS = ('reset_xform', 'bind_pose', 'apply_last_pose', 'prune_bones', 'remove_influences',
              'add_influences', 'transfer_influences')


def _pose_memory():
    # Outside max_actions: bridge reload removes that package's modules.
    # Only Python strings/numbers live here, never old-scene pymxs wrappers.
    key = '_qz_rigging_toolbox_pose_memory'
    if key not in sys.modules:
        sys.modules[key] = types.ModuleType(str(key))
    return sys.modules[key]


@register_action('rigging_toolbox_max_preflight')
def preflight():
    return dict(version=VERSION, path=os.path.abspath(__file__))


@register_action('rigging_toolbox_max_execute')
def execute(operation, expected_version):
    # One request both checks the implementation and executes it. A mismatched
    # version cannot mutate anything, so only that reply is safe to refresh.
    if expected_version != VERSION:
        return dict(version=VERSION)
    return dict(version=VERSION, result=run(operation))


def _handle(rt, node):
    return int(rt.getHandleByAnim(node))


def _skin_modifiers(rt, node):
    # classOf(Bend) == rt.Skin goes through Python 2 wrapper ordering as well.
    return [modifier for modifier in node.modifiers if rt.isKindOf(modifier, rt.Skin)]


def _activate(rt, node, skin=None):
    changed = list(rt.selection) != [node]
    if changed:
        rt.select(node)
    if rt.getCommandPanelTaskMode() != rt.Name('modify'):
        rt.setCommandPanelTaskMode(rt.Name('modify'))
    if skin is not None:
        current = rt.modPanel.getCurrentObject()
        # Python 2 pymxs compares unrelated modifier classes through MAXScript
        # ordering (e.g. Bend > Skin), producing errors even for == / list.index.
        if changed or current is None or _handle(rt, current) != _handle(rt, skin):
            rt.modPanel.setCurrentObject(skin, node=node)


def _finite(value):
    return not (math.isnan(value) or math.isinf(value))


def _close(a, b):
    return _finite(a) and _finite(b) and abs(a-b) <= max(2e-6, abs(a)*2e-6)


def _matrix_values(matrix):
    return tuple(float(value) for row in (matrix.row1, matrix.row2, matrix.row3, matrix.row4)
                 for value in row)


def _matrix_equal(first, second):
    return all(_close(a, b) for a, b in zip(_matrix_values(first), _matrix_values(second)))


def _pose_error(first, second):
    """World distance, maximum axis angle (degrees), relative axis scale error.

    Compare normalized axes as well as their lengths, so shear/reflections are
    checked too. A raw element tolerance mixes units and rejects Biped roundoff.
    """
    a, b = _matrix_values(first), _matrix_values(second)
    if not all(_finite(v) for v in a+b):
        return (float('inf'),)*3
    position = math.sqrt(sum((x-y)**2 for x, y in zip(a[9:], b[9:])))
    angle, scale = 0., 0.
    for offset in (0, 3, 6):
        p, q = a[offset:offset+3], b[offset:offset+3]
        lp, lq = math.sqrt(sum(v*v for v in p)), math.sqrt(sum(v*v for v in q))
        if min(lp, lq) < 1e-12:
            return position, float('inf'), float('inf')
        cross = (p[1]*q[2]-p[2]*q[1], p[2]*q[0]-p[0]*q[2], p[0]*q[1]-p[1]*q[0])
        angle = max(angle, math.degrees(math.atan2(math.sqrt(sum(v*v for v in cross)),
                                                sum(x*y for x, y in zip(p, q)))))
        scale = max(scale, abs(lp-lq)/lq)
    return position, angle, scale


def _pose_limits(rt, targets):
    points = [_matrix_values(matrix)[9:] for _, matrix in targets]
    extent = math.sqrt(sum((max(p[i] for p in points)-min(p[i] for p in points))**2 for i in range(3)))
    magnitude = max(abs(v) for p in points for v in p)
    # 0.001 mm minimum plus float32 precision for character size/world position.
    return (max(float(rt.units.decodeValue('0.001mm')), extent*2e-6, magnitude*2e-7), .001, 2e-5)


def _pose_equal(first, second, limits):
    return all(error <= limit for error, limit in zip(_pose_error(first, second), limits))


def _is_biped(rt, node):
    return bool(rt.isKindOf(node, rt.Biped_Object))


def _pose_failures(rt, targets, limits):
    failures = []
    for bone, matrix in targets:
        errors = _pose_error(bone.transform, matrix)
        if any(error > limit for error, limit in zip(errors, limits)):
            kind = 'Biped' if _is_biped(rt, bone) else text_type(rt.classOf(bone.controller))
            failures.append('%s（%s）：位置差 %.7g 系统单位（容差 %.7g），轴向角差 %.7g°，缩放相对差 %.7g%%'
                            % (text_type(bone.name), kind, errors[0], limits[0], errors[1], errors[2]*100.))
    return failures


def _set_prs_components(rt, bone, matrix, move, rotate):
    """Avoid touching an already-correct scale track during a pose round trip.

    Repeated Matrix3 assignment can introduce scale/axis drift even when
    all scale axes are effectively unchanged. Work on the
    existing writable local position/rotation tracks; leave other controllers
    and selective inheritance to the normal world-matrix path.
    """
    fn = getattr(rt, 'qzRigSetPRSComponentsV9', None)
    if fn is None:
        rt.execute('''
fn qzRigSetPRSComponentsV9 n target move rotate = (
    if classof n.controller != PRS or (getInheritanceFlags n).numberSet != 9 do return false
    if classof n.position.controller != Position_XYZ do return false
    if findItem #(Euler_XYZ, TCB_Rotation, Bezier_Rotation, Linear_Rotation) (classof n.rotation.controller) == 0 do return false
    local m = if n.parent == undefined then copy target else target * inverse n.parent.transform
    local rotation = undefined
    if rotate do (
        -- Retain the existing oriented/signed scale, including mirrored rigs.
        local stretch = copy n.controller.value
        stretch.row4 = [0,0,0]
        stretch = stretch * inverse (n.rotation.controller.value as matrix3)
        if abs (dot (cross stretch.row1 stretch.row2) stretch.row3) < 1e-12 do return false
        rotation = ((inverse stretch) * m).rotationpart
    )
    if move do n.position.controller.value = m.translationpart
    if rotate do n.rotation.controller.value = rotation
    true
)
''')
        fn = rt.qzRigSetPRSComponentsV9
    return bool(fn(bone, matrix, move, rotate))


def _set_pose(rt, bone, matrix, limits):
    error = _pose_error(bone.transform, matrix)
    if all(value <= limit for value, limit in zip(error, limits)):
        return
    if not _is_biped(rt, bone):
        if error[2] <= limits[2] and _set_prs_components(
                rt, bone, matrix, True, error[1] > limits[1]):
            return
        bone.transform = matrix
        return
    controller = bone.controller.rootNode.controller
    # Figure mode must already be active to change Biped proportions. Do not
    # silently replace the stored figure or switch modes for an animation pose.
    if error[2] > limits[2]:
        if not controller.figureMode:
            raise ValueError('%s（Biped）的绑定缩放与当前比例不同；当前不是 Figure Mode，未修改骨骼比例'
                             % text_type(bone.name))
        rt.biped.setTransform(bone, rt.Name('scale'), matrix.scalePart, False)
    if error[1] > limits[1]:
        rt.biped.setTransform(bone, rt.Name('rotation'), matrix.rotationPart, False)
    if _pose_error(bone.transform, matrix)[0] > limits[0]:
        rt.biped.setTransform(bone, rt.Name('pos'), matrix.translationPart, False)


def _depth(node, rt):
    count, seen = 0, set()
    while node.parent is not None:
        handle = _handle(rt, node)
        if handle in seen:
            raise ValueError('骨骼父级关系异常')
        seen.add(handle)
        count += 1
        node = node.parent
    return count


def _skin(rt, node, exclusive=True):
    skins = _skin_modifiers(rt, node)
    if len(skins) != 1:
        raise ValueError('请选择具有且仅具有一个 Skin 的模型')
    skin = skins[0]
    if exclusive and any(other != node and any(_handle(rt, m) == _handle(rt, skin)
                                             for m in other.modifiers) for other in rt.objects):
        raise ValueError('Skin 被其他模型实例共用，已跳过')
    return skin


def _bones(rt, skin):
    # GetBoneIDByListID reads the Modify panel listbox. GetBoneNodes returns
    # the actual Skin system order and works without activating that panel.
    return [(index,bone) for index,bone in enumerate(rt.skinOps.GetBoneNodes(skin),1) if bone is not None]


def _skin_nodes(rt, skin):
    return [bone for _,bone in _bones(rt,skin)]


def _toolbox_native(rt, name):
    fn = getattr(rt, name, None)
    if fn is None:
        rt.execute('''
fn qzRigReadWeightDataV8 s target = (
    local data = #()
    for v=1 to skinOps.GetNumberVertices s node:target do (
        local count = skinOps.GetVertexWeightCount s v node:target
        append data v
        append data count
        append data (skinOps.isUnNormalizeVertex s v node:target)
        append data (skinOps.getVertexDQWeight s v node:target)
        for slot=1 to count do (
            append data (skinOps.GetVertexWeightBoneID s v slot node:target)
            append data (skinOps.GetVertexWeight s v slot node:target)
        )
    )
    data
)
fn qzRigRestoreWeightDataV8 s target data remap:undefined = (
    local i = 1
    while i <= data.count do (
        local v = data[i]
        local count = data[i+1]
        local flag = data[i+2]
        local dq = data[i+3]
        local ids = #()
        local weights = #()
        i += 4
        for slot=1 to count do (
            append ids (if remap == undefined then data[i] else remap[data[i]])
            append weights data[i+1]
            i += 2
        )
        if count > 0 do (
            skinOps.unNormalizeVertex s v true node:target
            skinOps.ReplaceVertexWeights s v ids weights node:target
        )
        skinOps.unNormalizeVertex s v flag node:target
        skinOps.setVertexDQWeight s v dq node:target
    )
    true
)
fn qzRigAddInfluencesV8 s target bones = (
    local oldBones = skinOps.GetBoneNodes s
    local data = qzRigReadWeightDataV8 s target
    for i=1 to bones.count do skinOps.addBone s bones[i] (if i == bones.count then 1 else 0) node:target
    local newBones = skinOps.GetBoneNodes s
    local mapping = for bone in oldBones collect (findItem newBones bone)
    qzRigRestoreWeightDataV8 s target data remap:mapping
)
fn qzRigRemoveInfluencesV8 s target ids = (
    for id in ids do (
        -- Max2020 can silently skip explicit removals when its internal
        -- selected bone is stale. This sets only that bone index, no UI.
        skinOps.selectBone s id node:target
        skinOps.removeBone s id node:target
    )
    true
)
''')
        fn = getattr(rt, name)
    return fn


def _snapshot(rt, node, skin, bones=None):
    bones = _bones(rt, skin) if bones is None else bones
    handles = {index: _handle(rt, bone) for index, bone in bones}
    rows, flags, dq = [], [], []
    data = iter(list(_toolbox_native(rt, 'qzRigReadWeightDataV8')(skin,node)))
    for vertex in range(1, int(rt.skinOps.GetNumberVertices(skin,node=node))+1):
        next(data)  # Native vertex index, used by the shared restore format.
        count, flag, value = int(next(data)), bool(next(data)), float(next(data))
        row = {}
        for slot in range(count):
            bone, weight = int(next(data)), float(next(data))
            if not _finite(weight) or weight < 0:
                raise ValueError('顶点 %d 的权重无效' % vertex)
            if weight:
                if bone not in handles:
                    raise ValueError('顶点 %d 的骨骼引用 %d 无效（权重 %.9g）' % (vertex, bone, weight))
                row[handles[bone]] = row.get(handles[bone], 0.) + weight
        if not row:
            raise ValueError('顶点 %d 没有可保留的有效权重，请先初始化 Skin' % vertex)
        rows.append(row)
        flags.append(flag)
        if not _finite(value):
            raise ValueError('顶点 DQ 权重无效')
        dq.append(value)
    if not rows:
        raise ValueError('模型没有可处理的蒙皮顶点')
    return dict(bones={_handle(rt, bone): bone for _, bone in bones}, rows=rows, flags=flags, dq=dq,
                bind={_handle(rt, bone): rt.copy(rt.skinUtils.GetBoneBindTM(node, bone)) for _, bone in bones},
                mesh_bind=rt.copy(rt.skinUtils.GetMeshBindTM(node)))


def _weights_equal(first, second):
    return (len(first['rows']) == len(second['rows']) and first['flags'] == second['flags']
            and all(_close(a, b) for a, b in zip(first['dq'], second['dq']))
            and all(set(a) == set(b) and all(_close(a[key], b[key]) for key in a)
                    for a, b in zip(first['rows'], second['rows'])))


def _matches(rt, node, skin, expected, actual=None):
    actual = _snapshot(rt, node, skin) if actual is None else actual
    return (set(actual['bones']) == set(expected['bones']) and _weights_equal(actual, expected)
            and _matrix_equal(actual['mesh_bind'], expected['mesh_bind'])
            and all(_matrix_equal(actual['bind'][key], value) for key, value in expected['bind'].items()))


def _write_weights(rt, node, skin, expected, force=False):
    current = None if force else _snapshot(rt, node, skin)
    if current is not None and _weights_equal(current, expected):
        return current
    ids = {_handle(rt, bone): index for index, bone in _bones(rt, skin)}
    data = []
    for vertex, row in enumerate(expected['rows'], 1):
        old = current['rows'][vertex-1] if current is not None else None
        if (old is not None and set(old) == set(row) and all(_close(old[key], row[key]) for key in row)
                and current['flags'][vertex-1] == expected['flags'][vertex-1]
                and _close(current['dq'][vertex-1], expected['dq'][vertex-1])):
            continue
        pairs = sorted((ids[handle], weight) for handle, weight in row.items())
        data.extend((vertex,len(pairs),expected['flags'][vertex-1],expected['dq'][vertex-1]))
        for bone,weight in pairs:
            data.extend((bone,weight))
    _toolbox_native(rt, 'qzRigRestoreWeightDataV8')(skin,node,rt.Array(*data))
    # skinOps invalidates the modifier itself. Max's update() is mesh-only and
    # raises for a parametric Box/Plane with Skin; read-back evaluates the stack.


def _atomic(label, change, restored):
    import pymxs
    failure, result = [None], [None]
    try:
        with pymxs.undo(True, label):
            try:
                result[0] = change()
            except Exception as exc:
                # pymxs.undo may SUPPRESS the exception after undoing. Keep it explicitly.
                # Save the Unicode message before native undo handles the
                # exception; its MaxScript round-trip may alter exception repr.
                failure[0] = text_type(exc)
                raise
    except Exception:
        if failure[0] is None:
            raise
    if failure[0] is not None:
        try:
            valid = restored()
        except Exception:
            valid = False
        suffix = '；已恢复并读回核对' if valid else '；恢复未能验证，场景可能已有部分变化，请检查后再操作'
        raise RuntimeError(failure[0] + suffix)
    return result[0]


def _remaining_rows(rows, flags, removed):
    result = []
    for vertex, (row, unnormalized) in enumerate(zip(rows, flags), 1):
        survivors = {key: value for key, value in row.items() if key not in removed}
        if not survivors:
            raise ValueError('移除后顶点 %d 将失去全部有效影响，已跳过模型' % vertex)
        if not unnormalized and set(survivors) != set(row):
            total = sum(survivors.values())
            survivors = {key: value/total for key, value in survivors.items()}
        result.append(survivors)
    return result


@contextmanager
def _batch_redraw(rt):
    # Keep suppression local to dependent native writes. Selection, panel
    # setup, initial snapshots and final verification stay outside this scope.
    rt.disableSceneRedraw()
    try:
        yield
    finally:
        rt.enableSceneRedraw()


def _edit_influences(rt, node, add=None, remove=None):
    """User-facing direct edit: no weight validation or post-edit read-back."""
    import pymxs
    skin = _skin(rt, node, exclusive=False)
    bones = _bones(rt, skin)
    existing = {_handle(rt,bone): index for index,bone in bones}
    seen, additions = set(existing), []
    for bone in add or []:
        handle = _handle(rt,bone)
        if handle not in seen:
            seen.add(handle)
            additions.append(bone)
    removed = sorted((existing[handle] for handle in set(remove or []) if handle in existing),reverse=True)
    if not additions and not removed:
        return dict(mesh=text_type(node.name),changed=False,added=0,removed=0)
    failure = [None]
    try:
        with pymxs.undo(True,'Qz Edit Influences'):
            try:
                with _batch_redraw(rt):
                    if removed:
                        _toolbox_native(rt,'qzRigRemoveInfluencesV8')(skin,node,rt.Array(*removed))
                    if additions:
                        # Functional data preservation, not a validation pass:
                        # adding envelopes can otherwise change old weights.
                        _toolbox_native(rt,'qzRigAddInfluencesV8')(skin,node,rt.Array(*additions))
            except Exception as exc:
                failure[0] = text_type(exc)
                raise
    except Exception:
        if failure[0] is None:
            raise
    if failure[0] is not None:
        raise RuntimeError(failure[0]+'；操作中断，请检查 Max 当前结果')
    return dict(mesh=text_type(node.name),changed=True,added=len(additions),removed=len(removed))


def _change_bones(rt, node, add=None, remove=None, prune=False):
    skin = _skin(rt, node)
    before = _snapshot(rt, node, skin)
    if prune:
        remove = set(before['bones'])-set(handle for row in before['rows'] for handle in row)
    additions = { _handle(rt, bone): bone for bone in (add or []) if _handle(rt, bone) not in before['bones'] }
    removed = set(remove or []) & set(before['bones'])
    if _handle(rt, node) in additions:
        raise ValueError('模型不能作为自己的影响骨骼')
    if not additions and not removed:
        return dict(mesh=text_type(node.name), changed=False, added=0, removed=0)
    expected = dict(before, bones=dict(before['bones']), bind=dict(before['bind']))
    expected['bones'].update(additions)
    for handle in removed:
        del expected['bones'][handle]
        del expected['bind'][handle]
    expected['rows'] = _remaining_rows(before['rows'], before['flags'], removed)
    # Skin's per-vertex local data is not copied by copy(modifier) or reliably
    # restored by native undo. Keep BOTH the modifier copy and explicit weights.
    backup = rt.copy(skin)
    position = [_handle(rt, m) for m in node.modifiers].index(_handle(rt, skin))

    def restore():
        current_skin = _skin(rt, node)
        try:
            if _matches(rt, node, current_skin, before):
                return True
        except Exception:
            pass  # The failed native removal can leave stale IDs until restored.
        with _batch_redraw(rt):
            rt.deleteModifier(node, current_skin)
            rt.addModifier(node, backup, before=position)
            rt.skinUtils.SetMeshBindTM(node, before['mesh_bind'])
            for handle, matrix in before['bind'].items():
                rt.skinUtils.SetBoneBindTM(node, before['bones'][handle], matrix)
            _write_weights(rt, node, backup, before, force=True)
        return _matches(rt, node, backup, before)

    def change():
        # Adding/removing a bone can auto-change envelopes. Keep list changes
        # and required weight restoration together; never present that interim.
        with _batch_redraw(rt):
            ids = {_handle(rt, bone): index for index, bone in _bones(rt, skin)}
            indices = sorted((ids[handle] for handle in removed),reverse=True)
            if indices:
                _toolbox_native(rt,'qzRigRemoveInfluencesV8')(skin,node,rt.Array(*indices))
            for i, bone in enumerate(additions.values()):
                rt.skinOps.addBone(skin, bone, 1 if i == len(additions)-1 else 0,node=node)
            actual = _write_weights(rt, node, skin, expected, force=any(set(row) & removed for row in before['rows']))
        if not _matches(rt, node, skin, expected, actual=actual):
            raise RuntimeError('骨骼列表或权重读回校验失败')
        return dict(mesh=text_type(node.name), changed=True, added=len(additions), removed=len(removed))
    return _atomic('Qz Binding Toolbox', change, restore)


def _world_points(rt, node):
    mesh = rt.snapshotAsMesh(node)
    # Max 2020 snapshotAsMesh already bakes the world state, including the node
    # transform and object offset. Multiplying objectTransform again is incorrect.
    try:
        return [tuple(float(value) for value in rt.getVert(mesh, vertex))
                for vertex in range(1, int(rt.getNumVerts(mesh))+1)]
    finally:
        rt.delete(mesh)


def _points_equal(first, second):
    return len(first) == len(second) and all(_close(a, b) for p, q in zip(first, second) for a, b in zip(p, q))


def _reset(rt, node):
    import pymxs
    if _skin_modifiers(rt, node):
        raise ValueError('模型已有 Skin，重置变换仅处理绑定前模型，已跳过')
    if _is_biped(rt, node) or (rt.isProperty(node, 'boneEnable') and node.boneEnable):
        raise ValueError('请选择模型，骨骼不适用模型重置变换')
    before = _world_points(rt, node)
    if not before:
        raise ValueError('模型没有可处理的网格顶点')
    modifiers = list(node.modifiers)
    modifier_ids = [_handle(rt, m) for m in modifiers]
    transform = rt.copy(node.transform)
    offsets = {key: rt.copy(getattr(node, key)) for key in ('objectOffsetPos','objectOffsetRot','objectOffsetScale')}
    children = [(child, rt.copy(child.transform)) for child in node.children]
    # Validate on a separate COPY. Keep an INSTANCE of the old full pipeline,
    # including modifier local data, for exact restoration of this node only.
    # Detach the destination with MakeObjectsUnique before instanceReplace:
    # Max2020 otherwise replaces its shared pipeline (including siblings).
    with pymxs.undo(False):
        backup = rt.instance(node, isHidden=True)
        work = rt.copy(node, isHidden=True)
    retained = [False]
    def intact():
        return ([_handle(rt, m) for m in node.modifiers] == modifier_ids and _matrix_equal(node.transform, transform)
                and all(_close(float(getattr(getattr(node, key), component)), float(getattr(value, component)))
                        for key, value in offsets.items()
                        for component in (('x','y','z','w') if key == 'objectOffsetRot' else ('x','y','z')))
                and all(_matrix_equal(child.transform, tm) for child, tm in children)
                and _points_equal(_world_points(rt, node), before))
    def restored():
        if not intact():
            rt.instanceReplace(node, backup)
            node.transform = transform
            for key, value in offsets.items():
                setattr(node, key, value)
            for child, tm in children:
                child.transform = tm
        valid = intact()
        retained[0] = not valid
        return valid
    def change():
        work.pivot = rt.Point3(0, 0, 0)
        rt.resetXForm(work)
        rt.convertToPoly(work)
        if not _points_equal(_world_points(rt, work), before):
            raise RuntimeError('重置变换改变了模型世界顶点位置，未应用到原模型')
        rt.InstanceMgr.MakeObjectsUnique(rt.Array(node), rt.Name('individual'))
        if rt.areNodesInstances(node, backup):
            raise RuntimeError('未能解除目标模型实例关系，未替换原模型管线')
        rt.instanceReplace(node, work)
        node.transform = rt.copy(work.transform)
        for key in offsets:
            setattr(node, key, rt.copy(getattr(work, key)))
        for child, tm in children:
            child.transform = tm
        if not _points_equal(_world_points(rt, node), before):
            raise RuntimeError('重置变换改变了模型世界顶点位置')
        if len(node.modifiers) or not rt.isKindOf(node.baseObject, rt.Editable_Poly):
            raise RuntimeError('重置变换后未能完成 Editable Poly 塌陷')
        if any(abs(float(v)) > 1e-5 for v in node.pivot):
            raise RuntimeError('重置变换后轴心未能回到世界原点')
        if not all(_matrix_equal(child.transform, tm) for child, tm in children):
            raise RuntimeError('重置变换改变了子物体世界变换')
        return dict(mesh=text_type(node.name), changed=True)
    try:
        return _atomic('Qz Reset XForm', change, restored)
    except Exception as exc:
        # If restoration itself fails, leave the original pipeline available
        # in the hidden backup instead of discarding the recovery source.
        try:
            retained[0] = not intact()
        except Exception:
            retained[0] = True
        if retained[0]:
            raise RuntimeError(text_type(exc) + '；原始管线备份保留在隐藏对象：' + text_type(backup.name))
        raise
    finally:
        with pymxs.undo(False):
            if rt.isValidNode(work):
                rt.delete(work)
            if not retained[0] and rt.isValidNode(backup):
                rt.delete(backup)


class _PoseGraph(object):
    """Per-operation metadata only: no scene references survive the action."""
    def __init__(self, rt):
        self.rt, self.nodes, self.wrappers = rt, {}, {}

    def handle(self, node):
        # Hold a strong reference for this operation so wrapper ids cannot be
        # reused. New wrappers still resolve through the native node handle.
        key = id(node)
        if key not in self.wrappers:
            self.wrappers[key] = (node, _handle(self.rt, node))
        return self.wrappers[key][1]

    def info(self, node):
        handle = self.handle(node)
        if handle not in self.nodes:
            self.nodes[handle] = dict(node=node, parent=node.parent)
        return self.nodes[handle]

    def path(self, node):
        info = self.info(node)
        if 'path' not in info:
            if info.get('walking'):
                raise ValueError('骨骼父级关系异常')
            info['walking'] = True
            parent = self.path(info['parent']) if info['parent'] is not None else ()
            info['path'] = parent + (text_type(node.name),)
            info['walking'] = False
        return info['path']

    def depth(self, node):
        return len(self.path(node))-1

    def kind(self, node):
        info = self.info(node)
        if 'kind' not in info:
            info['kind'] = _pose_kind(self.rt, node)
        return info['kind']

    def root(self, node):
        info = self.info(node)
        if 'root' not in info:
            if self.kind(node) == 'biped':
                info['root'] = node.controller.rootNode
            else:
                parent = info['parent']
                if parent is not None and self.kind(parent) == 'bone':
                    # path() checks cycles before following this parent chain.
                    self.path(node)
                    info['root'] = self.root(parent)
                else:
                    info['root'] = node
        return info['root']

    def original(self, node):
        info = self.info(node)
        if 'original' not in info:
            info['original'] = self.rt.copy(node.transform)
        return info['original']


def _pose_path(rt, node):
    path, seen = [], set()
    while node is not None:
        handle = _handle(rt, node)
        if handle in seen:
            raise ValueError('骨骼父级关系异常')
        seen.add(handle)
        path.append(text_type(node.name))
        node = node.parent
    return tuple(reversed(path))


def _pose_kind(rt, node):
    if _is_biped(rt, node):
        # Footsteps and driven nodes without editable controllers are not pose bones.
        if (text_type(rt.classOf(node.controller)).lower() == 'footsteps'
                or not rt.isProperty(node.controller, rt.Name('rootNode'))):
            return 'biped_aux'
        return 'biped'
    if rt.isProperty(node, 'boneEnable') and node.boneEnable:
        return 'bone'
    return text_type(rt.classOf(node))


def _rig_pose_nodes(rt, influences, graph=None):
    """Whole Bone/Biped trees, plus any non-bone Skin influences."""
    graph = graph or _PoseGraph(rt)
    found, visited = {}, set()
    def remember(node):
        handle = graph.handle(node)
        if handle in visited:
            return
        visited.add(handle)
        if graph.kind(node) in ('bone', 'biped') or handle in explicit:
            found[handle] = node
        for child in node.children:
            remember(child)
    explicit = {graph.handle(node) for node in influences}
    for node in influences:
        remember(graph.root(node))
    return sorted(found.values(), key=graph.depth)


def _pose_index(rt, nodes, graph=None):
    graph = graph or _PoseGraph(rt)
    index = {}
    for node in nodes:
        key = graph.path(node)
        if key in index:
            raise ValueError('骨骼名称及层级不唯一：' + '/'.join(key))
        index[key] = node
    return index


def _rotation_native(rt, name):
    fn = getattr(rt, name, None)
    if fn is None:
        rt.execute('''
fn qzRigParentRotationV10 n = (
    if n.parent == undefined then quat 0 0 0 1
    else n.parent.transform.rotationpart
)
fn qzRigReadLocalRotationV10 n = (
    local mode=1, axis=0, angles=[0,0,0], q=quat 0 0 0 1
    if isKindOf n Biped_Object then (
        if classof n.controller == Footsteps or not isProperty n.controller #rootNode do throw "Biped auxiliary nodes are not pose bones"
        mode=2
        local world=n.transform.rotationpart
        q=((world as matrix3) * inverse ((qzRigParentRotationV10 n) as matrix3)).rotationpart
    ) else (
        local c=n.rotation.controller
        if findItem #(Euler_XYZ, TCB_Rotation, Bezier_Rotation, Linear_Rotation) (classof c) == 0 do throw "Unsupported local rotation controller"
        q=c.value
        if classof c == Euler_XYZ do (
            mode=0; axis=c.axisOrder
            angles=[c.x_rotation,c.y_rotation,c.z_rotation]
        )
    )
    #(mode,axis,angles.x,angles.y,angles.z,q.x,q.y,q.z,q.w)
)
fn qzRigSetLocalRotationV10 n mode axis a b c qx qy qz qw = (
    local q=quat qx qy qz qw
    if isKindOf n Biped_Object then (
        local world=(q as matrix3) * ((qzRigParentRotationV10 n) as matrix3)
        local root=n.controller.rootNode
        if root.parent != undefined do world = world * inverse (root.parent.transform.rotationpart as matrix3)
        biped.setTransform n #rotation world.rotationpart false
    ) else (
        local track=n.rotation.controller
        if findItem #(Euler_XYZ, TCB_Rotation, Bezier_Rotation, Linear_Rotation) (classof track) == 0 do throw "Unsupported local rotation controller"
        if mode == 0 and classof track == Euler_XYZ and track.axisOrder == axis then (
            track.x_rotation=a; track.y_rotation=b; track.z_rotation=c
        ) else track.value=q
    )
    true
)
''')
        fn = getattr(rt, name)
    return fn


def _local_rotation(rt, node):
    try:
        raw = _rotation_native(rt, 'qzRigReadLocalRotationV10')(node)
        result = dict(mode=int(raw[0]), order=int(raw[1]),
                      angles=tuple(float(raw[i]) for i in (2,3,4)),
                      quat=tuple(float(raw[i]) for i in (5,6,7,8)))
        if not all(_finite(v) for v in result['angles'] + result['quat']):
            raise ValueError('旋转数据包含无效数')
        return result
    except Exception as exc:
        raise ValueError('%s：无法读取局部旋转；%s' % (text_type(node.name), text_type(exc)))


def _rotation_error(first, second):
    a, b = first['quat'], second['quat']
    la, lb = math.sqrt(sum(v*v for v in a)), math.sqrt(sum(v*v for v in b))
    if min(la, lb) < 1e-12:
        return float('inf')
    a, b = [v/la for v in a], [v/lb for v in b]
    sign = 1. if sum(x*y for x,y in zip(a,b)) >= 0. else -1.
    difference = math.sqrt(sum((x-sign*y)**2 for x,y in zip(a,b)))
    total = math.sqrt(sum((x+sign*y)**2 for x,y in zip(a,b)))
    return math.degrees(4.*math.atan2(difference, total))


def _rotation_changed(first, second):
    if first['mode'] == second['mode'] == 0 and first['order'] == second['order']:
        return first['angles'] != second['angles']
    return _rotation_error(first, second) > .001


def _set_local_rotation(rt, node, data):
    return _rotation_native(rt, 'qzRigSetLocalRotationV10')(
        node, data['mode'], data['order'], *(tuple(data['angles']) + tuple(data['quat'])))


def _rotation_failures(rt, targets):
    failures = []
    for bone, data in targets:
        angle = _rotation_error(_local_rotation(rt, bone), data)
        if angle > .001:
            failures.append('%s：局部旋转角差 %.7g°' % (text_type(bone.name), angle))
    return failures


def _observed_pose(rt, ordered, graph):
    rows = [dict(path=graph.path(bone), kind=graph.kind(bone),
                 matrix=_matrix_values(bone.transform), rotation=_local_rotation(rt,bone))
            for bone, _ in ordered]
    return dict(schema=1, mode='local_rotation', units_per_mm=float(rt.units.decodeValue('1mm')),
                rows=sorted(rows, key=lambda row: row['path']))


def _rotation_transaction(rt, ordered, report, graph):
    before = [(bone, _local_rotation(rt,bone)) for bone, _ in ordered]
    changed = any(_rotation_changed(a,b) for (_,a),(_,b) in zip(before,ordered))
    report['changed'] = changed
    observed = [None]
    if not changed:
        observed[0] = _observed_pose(rt, ordered, graph)
        return report, observed[0]
    # A native undo can also re-evaluate the Biped's parent transform. Include
    # ancestors in recovery so parented rigs return to the exact prior frame.
    affected = {}
    for bone, _ in ordered:
        current = bone
        while current is not None and graph.handle(current) not in affected:
            affected[graph.handle(current)] = current
            current = graph.info(current)['parent']
    originals = [(bone, graph.original(bone))
                 for bone in sorted(affected.values(), key=graph.depth)]
    limits = _pose_limits(rt, originals)
    def restored():
        if _pose_failures(rt, originals, limits) or _rotation_failures(rt,before):
            target_handles = {graph.handle(bone) for bone, _ in before}
            with _batch_redraw(rt):
                for bone, matrix in originals:
                    if graph.handle(bone) not in target_handles:
                        _set_pose(rt,bone,matrix,limits)
                for bone, data in before:
                    if _rotation_changed(_local_rotation(rt,bone),data):
                        _set_local_rotation(rt,bone,data)
        return not _pose_failures(rt, originals, limits) and not _rotation_failures(rt,before)
    def change():
        with _batch_redraw(rt):
            for (bone, target), (_, original) in zip(ordered, before):
                if _rotation_changed(original,target):
                    _set_local_rotation(rt,bone,target)
        failures = _rotation_failures(rt,ordered)
        if failures:
            raise RuntimeError('局部旋转读回不符：\n' + '\n'.join(failures))
        observed[0] = _observed_pose(rt,ordered,graph)
        return report
    result = _atomic('Qz Apply Last Rotation Pose',change,restored)
    return result, observed[0]


def _capture_pose(rt, influences, graph=None):
    graph = graph or _PoseGraph(rt)
    nodes = _pose_index(rt, _rig_pose_nodes(rt, influences, graph), graph)
    rows = []
    for path, node in sorted(nodes.items()):
        values = _matrix_values(graph.original(node))
        if not all(_finite(value) for value in values):
            raise ValueError('骨骼姿势包含无效数：' + text_type(node.name))
        rows.append(dict(path=path, kind=graph.kind(node), matrix=values,
                         rotation=_local_rotation(rt,node)))
    return dict(schema=1, mode='local_rotation',
                units_per_mm=float(rt.units.decodeValue('1mm')), rows=rows)


def _pose_matrix(rt, values, translation_factor=1.):
    # Construct inside MaxScript instead of allocating four temporary pymxs
    # Point3 wrappers for every bone. No names or data are embedded in code.
    fn = getattr(rt, 'qzRigPoseMatrixV7', None)
    if fn is None:
        rt.execute('fn qzRigPoseMatrixV7 v s = (matrix3 '
                   '[v[1],v[2],v[3]] [v[4],v[5],v[6]] [v[7],v[8],v[9]] '
                   '([v[10],v[11],v[12]] * s))')
        fn = rt.qzRigPoseMatrixV7
    return fn(rt.Array(*values), translation_factor)


def _pose_transaction(rt, ordered, label, report, align_roots=None, graph=None, limits=None, changed=None):
    # Biped solvers may move the COM/siblings; ordinary bones also move their
    # descendants. Snapshot all affected nodes for verified failure recovery.
    graph = graph or _PoseGraph(rt)
    limits = limits or _pose_limits(rt, ordered)
    if changed is None:
        changed = any(not _pose_equal(graph.original(bone),matrix,limits) for bone,matrix in ordered)
    report['changed'] = changed
    if not changed:
        return report
    affected = {}
    def remember(node):
        handle = graph.handle(node)
        if handle in affected:
            return
        affected[handle] = node
        for child in node.children:
            remember(child)
    for bone, _ in ordered:
        remember(bone.controller.rootNode if graph.kind(bone) == 'biped' else bone)
    originals = [(bone, graph.original(bone)) for bone in sorted(affected.values(), key=graph.depth)]
    def apply(items):
        for bone, matrix in items:
            try:
                _set_pose(rt, bone, matrix, limits)
            except Exception as exc:
                raise RuntimeError('%s：%s' % (text_type(bone.name), text_type(exc)))
    def restored():
        if _pose_failures(rt, originals, limits):
            with _batch_redraw(rt):
                apply(originals)
        return not _pose_failures(rt, originals, limits)
    def change():
        if changed:
            with _batch_redraw(rt):
                if align_roots is not None:
                    align_roots()
                apply(ordered)
        failures = _pose_failures(rt, ordered, limits)
        if failures:
            raise RuntimeError('姿势读回不符：\n' + '\n'.join(failures))
        return report
    return _atomic(label, change, restored)


def _reuse_applied_pose(memory, candidate):
    # Compare the exact observed post-apply values, never a relaxed tolerance.
    # User edits (including edits on non-Skin bones) produce a new snapshot.
    previous = getattr(memory, 'last_pose', None)
    if (previous is not None and getattr(memory, 'applied_source', None) is previous
            and candidate == getattr(memory, 'applied_pose', None)):
        return previous
    return candidate


def _apply_last_pose(rt, nodes):
    cached = getattr(_pose_memory(), 'last_pose', None)
    if not cached or cached.get('schema') != 1:
        raise ValueError('还没有缓存姿势；请先在有 Pose 的角色上执行“骨骼回到绑定姿势”')
    if cached.get('mode') != 'local_rotation' or any('rotation' not in row for row in cached['rows']):
        raise ValueError('旧姿势缓存没有局部旋转数据；请在有 Pose 的角色上重新执行“骨骼回到绑定姿势”')
    graph = _PoseGraph(rt)
    influences = {}
    for node in nodes:
        for bone in _skin_nodes(rt, _skin(rt, node, exclusive=False)):
            influences[graph.handle(bone)] = bone
    if not influences:
        raise ValueError('所选模型没有影响骨骼')
    index = _pose_index(rt, _rig_pose_nodes(rt, list(influences.values()), graph), graph)
    cached_paths = {tuple(row['path']) for row in cached['rows']}
    problems, targets = [], []
    for row in cached['rows']:
        path = tuple(row['path'])
        bone = index.get(path)
        if bone is None:
            problems.append('缺少骨骼：' + '/'.join(path))
        elif graph.kind(bone) != row['kind']:
            problems.append('骨骼类型不同：' + '/'.join(path))
        else:
            targets.append((bone, row['rotation']))
    for bone in influences.values():
        if graph.path(bone) not in cached_paths:
            problems.append('缓存中没有所选角色的骨骼：' + text_type(bone.name))
    if problems:
        raise ValueError('角色骨架与上次姿势不匹配，未应用：\n' + '\n'.join(problems[:12]))
    ordered = sorted(targets, key=lambda item: graph.depth(item[0]))
    report = dict(mesh='、'.join(text_type(n.name) for n in nodes), changed=True,
                  bones=len(ordered), models=len(nodes), pose='last', rotation_only=True)
    result, observed = _rotation_transaction(rt, ordered, report, graph)
    memory = _pose_memory()
    memory.applied_pose = observed
    memory.applied_source = cached
    return result


def _bind_pose(rt, nodes):
    graph = _PoseGraph(rt)
    targets, names = {}, []
    for node in nodes:
        skin = _skin(rt, node, exclusive=False)
        for bone in _skin_nodes(rt, skin):
            handle = graph.handle(bone)
            matrix = rt.copy(rt.skinUtils.GetBoneBindTM(node, bone))
            if not all(_finite(value) for value in _matrix_values(matrix)):
                raise ValueError('骨骼绑定矩阵包含无效数：' + text_type(bone.name))
            if handle in targets and not _matrix_equal(targets[handle][1], matrix):
                raise ValueError('所选模型记录的骨骼绑定姿势冲突：' + text_type(bone.name))
            targets[handle] = (bone, matrix)
        names.append(text_type(node.name))
    if not targets:
        raise ValueError('所选模型没有影响骨骼')
    ordered = sorted(targets.values(), key=lambda item: graph.depth(item[0]))
    limits = _pose_limits(rt, ordered)
    changed = any(not _pose_equal(graph.original(bone),matrix,limits) for bone,matrix in ordered)
    # Do not replace the user's original Pose with another bind-pose snapshot.
    candidate = _capture_pose(rt, [bone for bone, _ in ordered], graph) if changed else None
    memory = _pose_memory() if candidate is not None else None
    if candidate is not None:
        candidate = _reuse_applied_pose(memory, candidate)
    def align_roots():
        # COM is usually absent from Skin. Its direct child (pelvis) supplies
        # an unambiguous translation anchor; child setTransform(#pos) cannot
        # move the COM. Do not infer a root pose from a posed hand/foot.
        aligned = set()
        for bone, matrix in ordered:
            if graph.kind(bone) != 'biped':
                continue
            root = bone.controller.rootNode
            handle = graph.handle(root)
            if handle in targets or handle in aligned or bone.parent != root:
                continue
            delta = matrix.translationPart - bone.transform.translationPart
            if math.sqrt(sum(float(v)**2 for v in delta)) > limits[0]:
                position = rt.biped.getTransform(root, rt.Name('pos'))
                rt.biped.setTransform(root, rt.Name('pos'), position+delta, False)
            aligned.add(handle)
    report = dict(mesh='、'.join(names), changed=changed, bones=len(ordered),
                  models=len(nodes), pose='bind', pose_cached=changed)
    result = _pose_transaction(rt, ordered, 'Qz Restore Bind Pose', report, align_roots,
                               graph=graph, limits=limits, changed=changed)
    if candidate is not None:
        memory.last_pose = candidate
        memory.applied_pose = None
        memory.applied_source = None
    return result


@contextmanager
def _session(rt, warnings, quiet_reset=False):
    import pymxs
    selected = list(rt.selection)
    mode = rt.getCommandPanelTaskMode()
    modifier = rt.modPanel.getCurrentObject() if mode == rt.Name('modify') else None
    level = rt.subObjectLevel
    suspended = False
    try:
        if quiet_reset and mode in (rt.Name('modify'), rt.Name('hierarchy'), rt.Name('motion')):
            # Pause panel rebuilds without disabling the viewport itself.
            rt.suspendEditing(which=mode)
            suspended = True
        with pymxs.animate(False):
            yield selected
    finally:
        def attempt(label, fn):
            try:
                fn()
            except Exception as exc:
                warnings.append(label + '未完成：' + text_type(exc))
        def restore_selection():
            if list(rt.selection) == selected:
                return
            valid = [n for n in selected if rt.isValidNode(n)]
            rt.select(rt.Array(*valid))
        def restore_panel():
            if rt.getCommandPanelTaskMode() != mode:
                rt.setCommandPanelTaskMode(mode)
            current = rt.modPanel.getCurrentObject() if modifier is not None else None
            if modifier is not None and (current is None or _handle(rt,current) != _handle(rt,modifier)):
                modifier_handle = _handle(rt,modifier)
                available = any(modifier_handle in [_handle(rt,m) for m in [n.baseObject]+list(n.modifiers)]
                                for n in selected if rt.isValidNode(n))
                if available:
                    rt.modPanel.setCurrentObject(modifier)
            if level is not None and rt.subObjectLevel != level:
                rt.subObjectLevel = level
        # Rebuilding an unchanged Modify panel can re-evaluate Skin. In
        # particular, a skipped Reset must not unnecessarily touch its context.
        attempt('恢复选择', restore_selection)
        attempt('恢复面板', restore_panel)
        if suspended:
            attempt('恢复面板刷新', lambda: rt.resumeEditing(which=mode))
        if rt.isSceneRedrawDisabled():
            # An outer caller owns this counter; leave its redraw policy intact.
            rt.setNeedsRedraw(complete=False)
        else:
            rt.redrawViews()


def _run(operation, warnings):
    import pymxs
    rt = pymxs.runtime
    if int(rt.maxVersion()[0]) != 22000:
        raise ValueError('绑定工具箱当前支持 3ds Max 2020')
    if operation not in OPERATIONS:
        raise ValueError('未知的绑定工具箱操作')
    started, reports, errors = time.time(), [], []
    with _session(rt, warnings, quiet_reset=(operation == 'reset_xform')) as selected:
        if not selected:
            raise ValueError('请先在 Max 中选择模型')
        if operation in ('add_influences', 'remove_influences', 'transfer_influences'):
            if len(selected) < 2 or (operation == 'transfer_influences' and len(selected) != 2):
                raise ValueError(('请依次选择 A、B 两个模型；当前选中了 %d 个对象。' if operation == 'transfer_influences'
                                  else '请先选骨骼，最后追加选择目标模型；当前选中了 %d 个对象。') % len(selected))
            target, influences = selected[-1], selected[:-1]
            if operation == 'transfer_influences':
                source = selected[0]
                influences = _skin_nodes(rt, _skin(rt, source, exclusive=False))
                if not influences:
                    raise ValueError('源模型的 Skin 没有影响骨骼')
            if operation == 'remove_influences':
                reports.append(_edit_influences(rt, target, remove=[_handle(rt, n) for n in influences]))
            else:
                reports.append(_edit_influences(rt, target, add=influences))
        elif operation in ('bind_pose', 'apply_last_pose'):
            try:
                reports.append((_bind_pose if operation == 'bind_pose' else _apply_last_pose)(rt, selected))
            except Exception as exc:
                # Return readable Unicode business errors, rather than Python2
                # traceback repr's escaped \uXXXX text through the bridge.
                errors.append(dict(mesh='所选模型', error=text_type(exc)))
        else:
            for node in selected:
                try:
                    if operation == 'reset_xform':
                        reports.append(_reset(rt, node))
                    else:
                        reports.append(_change_bones(rt, node, prune=True))
                except Exception as exc:
                    errors.append(dict(mesh=text_type(node.name), error=text_type(exc)))
    return dict(operation=operation, failed=(operation in ('bind_pose', 'apply_last_pose') and bool(errors)),
                count=sum(r.get('models', 1) for r in reports if r['changed']),
                reports=reports, errors=errors, warnings=warnings, elapsed_seconds=round(time.time()-started, 3))


@register_action('rigging_toolbox_max')
def run(operation):
    started, warnings = time.time(), []
    try:
        return _run(operation, warnings)
    except (ValueError, RuntimeError) as exc:
        # Expected operation/selection errors belong in user-facing text. A
        # Python2 traceback repr converts Chinese exceptions to unreadable \u.
        return dict(operation=operation, failed=True, count=0, reports=[],
                    errors=[dict(mesh='操作检查', error=text_type(exc))], warnings=warnings,
                    elapsed_seconds=round(time.time()-started, 3))
