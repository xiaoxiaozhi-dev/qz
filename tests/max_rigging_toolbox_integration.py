# -*- coding: utf-8 -*-
"""Disposable Max 2020 batch only. Run with build/launch_rigging_native.py."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import sys
import traceback
from contextlib import contextmanager

OUTPUT = os.environ['QZ_RIG_NATIVE_OUTPUT']
sys.path.insert(0, os.environ['QZ_RIG_PROJECT'])
sys.path.insert(0, os.path.join(os.environ['QZ_RIG_PROJECT'],'tests'))


def run():
    import pymxs
    from pymxs import runtime as rt
    from max_actions import rigging_toolbox as tool
    rt.resetMaxFile(rt.Name('noPrompt'))
    assert tool.execute('bind_pose',tool.VERSION-1) == dict(version=tool.VERSION)
    # Instrument actual native actions: setup and final checks stay outside
    # redraw suppression, only dependent write batches own a paired counter.
    native_run, native_batch = tool.run, tool._batch_redraw
    native_skin, native_matches = tool._skin, tool._matches
    redraw_checks, active = [], [None]
    @contextmanager
    def checked_batch(runtime):
        before = bool(runtime.isSceneRedrawDisabled())
        if active[0] is not None:
            active[0]['batches'] += 1
        try:
            with native_batch(runtime):
                assert runtime.isSceneRedrawDisabled()
                yield
        finally:
            assert bool(runtime.isSceneRedrawDisabled()) == before
    def checked_skin(*args,**kwargs):
        if active[0] is not None:
            assert bool(rt.isSceneRedrawDisabled()) == active[0]['outer'],active[0]
            active[0]['setup_checks'] += 1
        return native_skin(*args,**kwargs)
    def checked_matches(*args,**kwargs):
        if active[0] is not None:
            assert bool(rt.isSceneRedrawDisabled()) == active[0]['outer'],active[0]
            active[0]['final_checks'] += 1
        return native_matches(*args,**kwargs)
    def checked_run(operation):
        with io.open(os.path.join(OUTPUT,'progress.log'),'a',encoding='utf-8') as stream:
            stream.write(operation+' start\n')
        check = dict(operation=operation,outer=bool(rt.isSceneRedrawDisabled()),
                     batches=0,setup_checks=0,final_checks=0)
        active[0] = check
        view = rt.copy(rt.viewport.getTM())
        mode = rt.getCommandPanelTaskMode()
        selected_before = list(rt.selection)
        activate = tool._activate
        snapshot, matches = tool._snapshot, tool._matches
        def unexpected_activate(*args,**kwargs):
            raise AssertionError('Product action activated the Skin UI')
        tool._activate = unexpected_activate
        if operation in ('add_influences','remove_influences','transfer_influences'):
            tool._snapshot = unexpected_activate
            tool._matches = unexpected_activate
        try:
            return native_run(operation)
        finally:
            tool._activate = activate
            tool._snapshot, tool._matches = snapshot, matches
            assert bool(rt.isSceneRedrawDisabled()) == check['outer'],check
            assert tool._matrix_equal(rt.viewport.getTM(),view),check
            assert list(rt.selection) == selected_before and rt.getCommandPanelTaskMode() == mode,check
            redraw_checks.append(check)
            active[0] = None
            with io.open(os.path.join(OUTPUT,'progress.log'),'a',encoding='utf-8') as stream:
                stream.write(operation+' end\n')
    tool.run, tool._batch_redraw = checked_run, checked_batch
    tool._skin, tool._matches = checked_skin, checked_matches
    parent = rt.Dummy(name='RigParent')
    parent.pos = rt.Point3(17, -8, 3)
    bones = []
    for index in range(5):
        bone = rt.boneSys.createBone(rt.Point3(index*5, 0, 0), rt.Point3(index*5, 0, 4), rt.Point3(1, 0, 0))
        bone.name = 'Bone%d' % index
        bone.parent = bones[0] if index == 1 else parent
        bones.append(bone)
    def make(name, influences, rows, segments=1, primitive=False):
        node = rt.plane(name=name, lengthsegs=segments, widthsegs=segments)
        if not primitive:
            rt.convertToPoly(node)
        skin = rt.Skin()
        rt.addModifier(node, skin)
        tool._activate(rt, node, skin)
        for bone in influences:
            rt.skinOps.addBone(skin, bone, 1)
        for vertex in range(1, int(rt.skinOps.GetNumberVertices(skin))+1):
            pairs = rows[vertex % len(rows)]
            rt.skinOps.ReplaceVertexWeights(skin, vertex, rt.Array(*(p[0] for p in pairs)),rt.Array(*(p[1] for p in pairs)))
            rt.skinOps.setVertexDQWeight(skin, vertex, .375)
        return node, skin
    # B is created first; source-target order must follow sequential selection, not creation order.
    b, bs = make('Low_B', [bones[0],bones[1],bones[4]], [[(1,.4),(3,.6)]], 2)
    a, ass = make('High_A', bones[:4], [[(1,.7),(2,.3)]])
    tool._activate(rt, b, bs)
    # Native removal can zero normalized survivors. Use raw weights here so
    # the later prune case remains valid; compare normalized native behavior below.
    for vertex in range(1,int(rt.skinOps.GetNumberVertices(bs))+1):
        rt.skinOps.unNormalizeVertex(bs,vertex,True)
    rt.skinOps.ReplaceVertexWeights(bs, 1, rt.Array(1,3), rt.Array(.2,.4))
    before_a = tool._snapshot(rt,a,ass)
    before_b = tool._snapshot(rt,b,bs)
    rt.select(a)
    rt.selectMore(b)
    selected = list(rt.selection)
    assert selected == [a,b], [n.name for n in selected]
    rt.setCommandPanelTaskMode(rt.Name('create'))
    rt.animButtonState = True
    transferred = tool.run('transfer_influences')
    assert transferred['count'] == 1 and transferred['reports'][0]['added'] == 2, transferred
    assert list(rt.selection) == selected and rt.animButtonState
    rt.animButtonState = False
    after_b = tool._snapshot(rt,b,bs)
    assert tool._weights_equal(before_b,after_b), 'Target weights changed'
    assert set(after_b['bones']) == set(before_b['bones']) | set(before_a['bones'])
    assert tool._matches(rt,a,ass,before_a), 'Source was changed'
    rt.select(a)
    rt.selectMore(b)
    again = tool.run('transfer_influences')
    assert again['count'] == 0 and again['reports'][0]['added'] == 0, again
    assert redraw_checks[-1]['batches'] == 0,redraw_checks[-1]
    rt.select(a)
    one_selected = tool.run('transfer_influences')
    assert one_selected['failed'] and one_selected['count'] == 0,one_selected
    selection_error = one_selected['errors'][0]['error']
    assert 'A、B' in selection_error and '1 个对象' in selection_error,selection_error
    assert '\\u' not in selection_error and 'Traceback' not in selection_error,selection_error
    assert redraw_checks[-1]['batches'] == 0,redraw_checks[-1]

    # Adding selected influences follows the same no-weight-change contract.
    fresh = rt.boneSys.createBone(rt.Point3(40,0,0),rt.Point3(40,0,5),rt.Point3(1,0,0))
    fresh.name = 'NewInfluence'
    before_add = tool._snapshot(rt,b,bs)
    rt.select(fresh)
    rt.selectMore(b)
    added = tool.run('add_influences')
    assert added['reports'][0]['added'] == 1 and tool._weights_equal(before_add,tool._snapshot(rt,b,bs)), added
    rt.select(fresh)
    rt.selectMore(b)
    removed = tool.run('remove_influences')
    assert removed['reports'][0]['removed'] == 1, removed
    assert tool._weights_equal(before_add,tool._snapshot(rt,b,bs))
    # Re-adding a deleted bone may reuse its old slot and shift other IDs.
    # Preserve weights by node identity, without relying on the Modify list.
    sparse, sparse_skin = make('SparseAdd',[bones[0],bones[1],bones[2]], [[(1,.4),(3,.6)]])
    rt.skinOps.removeBone(sparse_skin,2,node=sparse)
    sparse_before = tool._snapshot(rt,sparse,sparse_skin)
    rt.select(bones[1])
    rt.selectMore(sparse)
    rt.setCommandPanelTaskMode(rt.Name('create'))
    sparse_added = tool.run('add_influences')
    assert sparse_added['count'] == 1 and sparse_added['reports'][0]['added'] == 1,sparse_added
    assert tool._weights_equal(sparse_before,tool._snapshot(rt,sparse,sparse_skin))
    # Direct removal follows Skin's native behavior, with no corrective scan.
    rt.select(bones[4])
    rt.selectMore(b)
    weighted = tool.run('remove_influences')
    assert weighted['reports'][0]['removed'] == 1, weighted
    weights = tool._snapshot(rt,b,bs)
    handle0 = tool._handle(rt,bones[0])
    assert tool._close(weights['rows'][0][handle0],.2) and weights['flags'][0]
    assert all(tool._close(row[handle0],.4) for row in weights['rows'][1:])
    # Remove all unused list entries; tiny positive weight must remain.
    rt.select(b)
    pruned = tool.run('prune_bones')
    assert not pruned['errors'] and pruned['reports'][0]['removed'] == 3, pruned
    assert len(tool._snapshot(rt,b,bs)['bones']) == 1
    # Direct removal has exactly Max's behavior, including zero survivors.
    native_node,reference_skin = make('NativeRemove',[bones[0],bones[4]], [[(1,.4),(2,.6)]])
    direct_node,direct_skin = make('DirectRemove',[bones[0],bones[4]], [[(1,.4),(2,.6)]])
    tool._activate(rt,native_node,reference_skin)
    rt.skinOps.selectBone(reference_skin,2,node=native_node)
    rt.skinOps.removeBone(reference_skin,2,node=native_node)
    native_data=list(tool._toolbox_native(rt,'qzRigReadWeightDataV8')(reference_skin,native_node))
    rt.select(bones[4])
    rt.selectMore(direct_node)
    rt.setCommandPanelTaskMode(rt.Name('create'))
    direct_removed=tool.run('remove_influences')
    assert direct_removed['count']==1,direct_removed
    direct_data=list(tool._toolbox_native(rt,'qzRigReadWeightDataV8')(direct_skin,direct_node))
    assert native_data==direct_data,(native_data,direct_data)
    assert len(tool._bones(rt,direct_skin))==1
    tiny, ts = make('TinyWeights',[bones[0],bones[1],bones[2]], [[(1,1e-7),(2,1.-1e-7)]])
    rt.select(tiny)
    tiny_result = tool.run('prune_bones')
    assert tiny_result['reports'][0]['removed'] == 1, tiny_result
    assert len(tool._snapshot(rt,tiny,ts)['bones']) == 2

    # Undo helper must retain and report exceptions even though pymxs.undo suppresses them.
    above = rt.Bend(name='AboveSkin')
    below = rt.Bend(name='BelowSkin')
    rt.addModifier(a,below,before=1)
    rt.addModifier(a,above)
    assert [tool._handle(rt,m) for m in a.modifiers] == [tool._handle(rt,m) for m in [above,ass,below]]
    snapshot = tool._snapshot(rt,a,ass)
    original_writer = tool._write_weights
    calls = [0]
    def failing_writer(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError('injected failure')
        return original_writer(*args, **kwargs)
    rt.select(a)
    tool._write_weights = failing_writer
    try:
        failed_result = tool.run('prune_bones')
    finally:
        tool._write_weights = original_writer
    ass = tool._skin(rt,a)
    assert failed_result['count'] == 0 and 'injected failure' in failed_result['errors'][0]['error'],failed_result
    assert '已恢复并读回核对' in failed_result['errors'][0]['error'],failed_result
    assert tool._matches(rt,a,ass,snapshot), 'Failed operation did not restore Skin'
    assert [tool._handle(rt,m) for m in a.modifiers] == [tool._handle(rt,m) for m in [above,ass,below]], 'Restoration changed stack position'
    alias = rt.instance(a)
    rt.select(a)
    shared = tool.run('prune_bones')
    assert shared['count'] == 0 and shared['errors'],shared
    rt.delete(alias)

    bind = [(bone,rt.copy(rt.skinUtils.GetBoneBindTM(a,bone))) for bone in bones[:4]]
    parent.pos = rt.Point3(31, -22, 17)
    rt.rotate(bones[0],rt.eulerAngles(15,20,35))
    rt.rotate(bones[1],rt.eulerAngles(10,45,25))
    assert any(not tool._matrix_equal(bone.transform,matrix) for bone,matrix in bind)
    rt.select(a)
    rt.animButtonState = True
    original_pose_cache = tool._capture_pose(rt,bones[:4])
    pose = tool.run('bind_pose')
    assert rt.animButtonState and list(rt.selection) == [a]
    rt.animButtonState = False
    assert not pose['failed'] and pose['reports'][0]['bones'] == 4, pose
    assert redraw_checks[-1]['batches'] == 1,redraw_checks[-1]
    assert all(tool._matrix_equal(bone.transform,matrix) for bone,matrix in bind)
    cached_pose = tool._pose_memory().last_pose
    assert cached_pose == original_pose_cache
    # Stored Skin pose can differ from initial bind pose; implementation must use bind matrices.
    assert tool._matches(rt,a,ass,snapshot)
    pose_again = tool.run('bind_pose')
    assert not pose_again['failed'] and redraw_checks[-1]['batches'] == 0,pose_again
    assert tool._pose_memory().last_pose is cached_pose
    applied_pose_reply = tool.execute('apply_last_pose',tool.VERSION)
    assert applied_pose_reply['version'] == tool.VERSION
    applied_pose = applied_pose_reply['result']
    assert not applied_pose['failed'] and applied_pose['count'] == 1,applied_pose
    pose_nodes = tool._pose_index(rt,tool._rig_pose_nodes(rt,bones[:4]))
    pose_targets = [(pose_nodes[tuple(row['path'])],row['rotation']) for row in cached_pose['rows']]
    assert not tool._rotation_failures(rt,pose_targets)
    assert tool._pose_memory().last_pose is cached_pose
    assert tool._matches(rt,a,ass,snapshot)
    tool.run('bind_pose')
    conflict = rt.copy(rt.skinUtils.GetBoneBindTM(b,bones[0]))
    conflict.row4 = conflict.row4 + rt.Point3(10,0,0)
    rt.skinUtils.SetBoneBindTM(b,bones[0],conflict)
    old_tm = rt.copy(bones[0].transform)
    rt.select(a)
    rt.selectMore(b)
    blocked_pose = tool.run('bind_pose')
    assert blocked_pose['failed'] and '冲突' in blocked_pose['errors'][0]['error'],blocked_pose
    assert redraw_checks[-1]['batches'] == 0,redraw_checks[-1]
    assert tool._matrix_equal(bones[0].transform,old_tm)

    plain = rt.box(name='ResetModel')
    plain.parent = parent
    plain.pos = rt.Point3(20,30,40)
    rt.rotate(plain,rt.eulerAngles(15,25,35))
    plain.scale = rt.Point3(2,3,-.5)
    plain.objectOffsetPos = rt.Point3(1,2,3)
    reset_alias = rt.instance(plain)
    alias_points = tool._world_points(rt,reset_alias)
    alias_tm = rt.copy(reset_alias.transform)
    plain_handle = tool._handle(rt,plain)
    child = rt.box(name='ResetChild')
    child.parent = plain
    child.pos = rt.Point3(5,6,7)
    child_tm = rt.copy(child.transform)
    points = tool._world_points(rt,plain)
    bbox = rt.nodeGetBoundingBox(plain,rt.matrix3(1))
    for axis in range(3):
        assert tool._close(min(p[axis] for p in points),float(bbox[0][axis]))
        assert tool._close(max(p[axis] for p in points),float(bbox[1][axis]))
    rt.select(plain)
    reset_object_count = len(list(rt.objects))
    reset_view = rt.copy(rt.viewport.getTM())
    reset_mode = rt.getCommandPanelTaskMode()
    original_reset = tool._reset
    def reset_with_redraw_check(runtime,node):
        assert not runtime.isSceneRedrawDisabled()
        return original_reset(runtime,node)
    tool._reset = reset_with_redraw_check
    try:
        reset = tool.run('reset_xform')
    finally:
        tool._reset = original_reset
    assert reset['count'] == 1 and not reset['errors'], reset
    assert tool._points_equal(points,tool._world_points(rt,plain))
    assert len(plain.modifiers) == 0 and rt.isKindOf(plain.baseObject,rt.Editable_Poly)
    assert all(abs(float(v)) < 1e-5 for v in plain.pivot)
    assert tool._matrix_equal(child.transform,child_tm)
    identity = rt.matrix3(1)
    assert tool._matrix_equal(plain.transform,identity),plain.transform
    assert tool._handle(rt,plain) == plain_handle and len(list(rt.objects)) == reset_object_count
    assert list(rt.selection) == [plain] and rt.getCommandPanelTaskMode() == reset_mode
    assert not rt.isSceneRedrawDisabled() and tool._matrix_equal(rt.viewport.getTM(),reset_view)
    assert tool._points_equal(alias_points,tool._world_points(rt,reset_alias)), dict(before=alias_points,after=tool._world_points(rt,reset_alias),tm=tool._matrix_values(reset_alias.transform),base=unicode(rt.classOf(reset_alias.baseObject)))
    assert tool._matrix_equal(reset_alias.transform,alias_tm)
    assert rt.isKindOf(reset_alias.baseObject,rt.Box),reset_alias.baseObject
    assert int(rt.polyop.getNumVerts(plain)) == 8 and int(rt.polyop.getNumFaces(plain)) == 6
    # Reset neither disables redraw internally nor consumes an outer counter.
    redraw_probe = rt.box(name='ResetOuterRedraw')
    redraw_probe.scale = rt.Point3(2,3,4)
    probe_points = tool._world_points(rt,redraw_probe)
    rt.select(redraw_probe)
    rt.disableSceneRedraw()
    try:
        outer_reset = tool.run('reset_xform')
        assert outer_reset['count'] == 1 and not outer_reset['errors'],outer_reset
        assert rt.isSceneRedrawDisabled()
        assert tool._points_equal(probe_points,tool._world_points(rt,redraw_probe))
    finally:
        rt.enableSceneRedraw()
    assert not rt.isSceneRedrawDisabled()
    # Native undo must restore a collapsed pipeline on a validation failure.
    rollback_model = rt.box(name='ResetRollback')
    rollback_model.scale = rt.Point3(2,3,4)
    rb_bend = rt.Bend()
    rt.addModifier(rollback_model,rb_bend)
    rb_points = tool._world_points(rt,rollback_model)
    rb_tm = rt.copy(rollback_model.transform)
    original_equal = tool._points_equal
    point_calls = [0]
    def failing_point_check(first,second):
        point_calls[0] += 1
        return False if point_calls[0] == 2 else original_equal(first,second)
    tool._points_equal = failing_point_check
    rt.select(rollback_model)
    try:
        reset_rollback = tool.run('reset_xform')
    finally:
        tool._points_equal = original_equal
    assert reset_rollback['count'] == 0 and '已恢复并读回核对' in reset_rollback['errors'][0]['error'],reset_rollback
    assert tool._matrix_equal(rollback_model.transform,rb_tm)
    assert tool._points_equal(rb_points,tool._world_points(rt,rollback_model))
    assert [tool._handle(rt,m) for m in rollback_model.modifiers] == [tool._handle(rt,rb_bend)]
    skinned = rt.box(name='SkinnedReset')
    skinned.scale = rt.Point3(2,3,.5)
    reset_skin = rt.Skin()
    rt.addModifier(skinned,reset_skin)
    tool._activate(rt,skinned,reset_skin)
    rt.skinOps.addBone(reset_skin,bones[0],1)
    skinned.scale = rt.Point3(3,2,.75)
    skin_before = tool._snapshot(rt,skinned,reset_skin)
    rt.select(skinned)
    # Selection itself can re-evaluate a newly bound, subsequently scaled Skin.
    # Establish the baseline after the user's selection, before invoking us.
    rt.redrawViews()
    bound_points = tool._world_points(rt,skinned)
    assert tool._points_equal(bound_points,tool._world_points(rt,skinned)), 'Fixture evaluation is unstable'
    reset_bound = tool.run('reset_xform')
    assert reset_bound['count'] == 0 and len(reset_bound['errors']) == 1,reset_bound
    assert 'Skin' in reset_bound['errors'][0]['error'],reset_bound
    assert tool._points_equal(bound_points,tool._world_points(rt,skinned)), (bound_points,tool._world_points(rt,skinned))
    assert tool._matches(rt,skinned,reset_skin,skin_before)
    # skinOps also supports Skin above a parametric primitive. update(node) does
    # not: force a weight write/removal without converting the source object.
    primitive, ps = make('PrimitiveSkin',bones[:2],[[(1,.4),(2,.6)]],primitive=True)
    for vertex in range(1,int(rt.skinOps.GetNumberVertices(ps))+1):
        rt.skinOps.unNormalizeVertex(ps,vertex,True)
    rt.select(bones[1])
    rt.selectMore(primitive)
    primitive_result = tool.run('remove_influences')
    assert primitive_result['count'] == 1,primitive_result
    primitive_after = tool._snapshot(rt,primitive,ps)
    assert all(set(row)=={handle0} and tool._close(row[handle0],.4) for row in primitive_after['rows'])
    # Biped COM is deliberately NOT in Skin: the common character setup.
    bip_root = rt.biped.createNew(100.,-90.,rt.Point3(0,0,0))
    bip_nodes = [n for n in rt.objects if rt.isKindOf(n,rt.Biped_Object)
                 and 'Footsteps' not in unicode(n.name) and n != bip_root]
    hand = next(n for n in bip_nodes if unicode(n.name).endswith('L Hand'))
    extra_bone = rt.boneSys.createBone(rt.Point3(0,0,10),rt.Point3(0,0,14),rt.Point3(1,0,0))
    extra_bone.name = 'BoneAttachedToBiped'
    extra_bone.parent = hand
    mixed_nodes = bip_nodes + [extra_bone]
    mixed, ms = make('MixedBipedAndBone',mixed_nodes,[[(1,.4),(2,.6)]])
    mixed_before = tool._snapshot(rt,mixed,ms)
    mixed_bind = [(bone,rt.copy(rt.skinUtils.GetBoneBindTM(mixed,bone))) for bone in mixed_nodes]
    mixed_limits = tool._pose_limits(rt,mixed_bind)
    arm = next(n for n in bip_nodes if unicode(n.name).endswith('L UpperArm'))
    original_rotation = rt.biped.getTransform(arm,rt.Name('rotation'))
    rt.biped.setTransform(arm,rt.Name('rotation'),rt.quat(30.,rt.Point3(1,0,0))*original_rotation,False)
    rt.rotate(extra_bone,rt.eulerAngles(15,10,5))
    rt.select(mixed)
    mixed_pose = tool.run('bind_pose')
    assert not mixed_pose['failed'] and mixed_pose['count'] == 1,mixed_pose
    assert not tool._pose_failures(rt,mixed_bind,mixed_limits)
    assert tool._matches(rt,mixed,ms,mixed_before)
    # An unreachable Biped bind position must remain a real failure, with
    # readable per-bone differences, and restore the full pre-operation pose.
    old_hand_bind = rt.copy(rt.skinUtils.GetBoneBindTM(mixed,hand))
    bad_hand_bind = rt.copy(old_hand_bind)
    bad_hand_bind.row4 = bad_hand_bind.row4+rt.Point3(500,0,0)
    rt.skinUtils.SetBoneBindTM(mixed,hand,bad_hand_bind)
    failure_before = [(bone,rt.copy(bone.transform)) for bone in [bip_root]+mixed_nodes]
    rt.select(mixed)
    rejected_pose = tool.run('bind_pose')
    assert rejected_pose['failed'] and rejected_pose['count'] == 0,rejected_pose
    rejection = rejected_pose['errors'][0]['error']
    assert unicode(hand.name) in rejection and '位置差' in rejection and '\\u' not in rejection,rejection
    assert '已恢复并读回核对' in rejection,rejection
    assert not tool._pose_failures(rt,failure_before,mixed_limits),tool._pose_failures(rt,failure_before,mixed_limits)
    rt.skinUtils.SetBoneBindTM(mixed,hand,old_hand_bind)
    assert tool._matches(rt,mixed,ms,mixed_before)
    # Move/turn the whole Biped and restore to Skin, rather than its current root.
    rt.biped.setTransform(bip_root,rt.Name('pos'),rt.biped.getTransform(bip_root,rt.Name('pos'))+rt.Point3(10,20,5),False)
    root_rotation = rt.biped.getTransform(bip_root,rt.Name('rotation'))
    rt.biped.setTransform(bip_root,rt.Name('rotation'),rt.quat(20.,rt.Point3(0,0,1))*root_rotation,False)
    rt.select(mixed)
    moved_biped = tool.run('bind_pose')
    assert not moved_biped['failed'] and moved_biped['count'] == 1,moved_biped
    assert not tool._pose_failures(rt,mixed_bind,mixed_limits)
    assert tool._matches(rt,mixed,ms,mixed_before)
    # Every other operation must also preserve a caller-owned redraw counter.
    outer_source, outer_ss = make('OuterSource',bones[:3],[[(1,.4),(2,.6)]])
    outer_target, outer_ts = make('OuterTarget',bones[:2],[[(1,.4),(2,.6)]])
    for operation,selection in [
            ('transfer_influences',[outer_source,outer_target]),
            ('remove_influences',[bones[2],outer_target]),
            ('add_influences',[fresh,outer_target]),
            ('prune_bones',[outer_target]),
            ('bind_pose',[outer_target]),
            ('apply_last_pose',[outer_target])]:
        if operation == 'bind_pose':
            rt.rotate(bones[0],rt.eulerAngles(5,10,15))
        rt.select(rt.Array(*selection))
        rt.disableSceneRedraw()
        try:
            outer_result = tool.run(operation)
            assert not outer_result['failed'] and outer_result['count'] == 1,outer_result
            assert redraw_checks[-1]['outer'] and redraw_checks[-1]['batches'] == 1,redraw_checks[-1]
            assert rt.isSceneRedrawDisabled()
        finally:
            rt.enableSceneRedraw()
        assert not rt.isSceneRedrawDisabled()
    for operation in tool.OPERATIONS:
        checks = [c for c in redraw_checks if c['operation'] == operation and not c['outer']]
        assert checks,operation
        if operation == 'reset_xform':
            assert all(c['batches'] == 0 for c in checks),checks
        else:
            assert any(c['batches'] > 0 for c in checks),checks
    # Opening a different .max file and reloading actions must retain only
    # value-based pose data and resolve its completely new scene nodes.
    from max_pose_memory_integration import run as pose_memory_checks
    cross_file_pose = pose_memory_checks(rt,tool,OUTPUT)
    from max_pose_prs_roundtrip_integration import run as prs_roundtrip_checks
    prs_roundtrip = prs_roundtrip_checks(rt,tool)
    return dict(ok=True,python=sys.version, transfer=transferred,add=added,remove=removed,
                weighted=weighted,prune=pruned,tiny=tiny_result,pose=pose,reset=reset,rollback=True,
                primitive=primitive_result,reset_bound=reset_bound,reset_rollback=reset_rollback,
                mixed_pose=mixed_pose,moved_biped=moved_biped,rejected_pose=rejected_pose,one_selected=one_selected,
                source_vertices=len(before_a['rows']),target_vertices=len(before_b['rows']),redraw_checks=redraw_checks,
                cross_file_pose=cross_file_pose,prs_roundtrip=prs_roundtrip)


try:
    result = run()
except Exception:
    result = dict(ok=False,error=traceback.format_exc())
with io.open(os.path.join(OUTPUT,'result.json'),'w',encoding='utf-8') as stream:
    data = json.dumps(result,ensure_ascii=True,indent=2)
    stream.write(data if isinstance(data,type(u'')) else data.decode('ascii'))
print('RIGGING_TOOLBOX_RESULT: ' + str(result['ok']))
