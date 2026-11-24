# -*- coding: utf-8 -*-
"""Native regression for PRS scale preservation and repeated pose operations."""
from __future__ import unicode_literals

def run(rt, tool):
    import pymxs
    rt.resetMaxFile(rt.Name('noPrompt'))
    group=rt.Dummy(name='PoseGroup')
    group.scale=rt.Point3(1.000002,.999999,1.000001)
    bones=[]
    for i in range(6):
        bone=rt.boneSys.createBone(rt.Point3(0,0,i*4.),rt.Point3(0,0,(i+1)*4.),rt.Point3(1,0,0))
        bone.name='RoundTripBone%d'%i
        bone.parent=bones[-1] if bones else group
        bone.scale=rt.Point3(1.000001,.999998,1.000003)
        bones.append(bone)
    mesh=rt.plane(name='PoseMesh')
    skin=rt.Skin()
    rt.addModifier(mesh,skin)
    tool._activate(rt,mesh,skin)
    for bone in bones:
        rt.skinOps.addBone(skin,bone,1)
    with pymxs.animate(True):
        with pymxs.attime(0):
            rt.rotate(bones[0],rt.eulerAngles(0,0,0))
        with pymxs.attime(10):
            rt.rotate(bones[0],rt.eulerAngles(20,15,10))
            rt.rotate(bones[2],rt.eulerAngles(5,20,8))
    rt.sliderTime=10
    rt.execute('''fn qzPoseTrackStateV9 n = (
        local ids=for c in #(n.controller,n.position.controller,n.rotation.controller,n.scale.controller) collect getHandleByAnim c
        local times=for c in #(n.position.controller,n.rotation.controller,n.scale.controller) collect (for k=1 to numKeys c collect (getKeyTime c k) as integer)
        #(ids,times)
    )''')
    def tracks():
        return [unicode(rt.qzPoseTrackStateV9(n)) for n in bones]
    expected_tracks=tracks()
    initial=[(n,rt.copy(n.transform)) for n in bones]
    bind=[(n,rt.copy(rt.skinUtils.GetBoneBindTM(mesh,n))) for n in bones]
    weights=tool._snapshot(rt,mesh,skin)
    limits=tool._pose_limits(rt,bind)
    rt.select(mesh)
    maximum=[0.,0.,0.]
    activate=tool._activate
    def forbidden(*args,**kwargs):
        raise AssertionError('Pose operation activated Skin')
    tool._activate=forbidden
    try:
        first_cache=None
        for cycle in range(30):
            result=tool.run('bind_pose')
            assert not result['failed'],result
            if first_cache is None:
                first_cache=tool._pose_memory().last_pose
            assert tool._pose_memory().last_pose is first_cache
            assert not tool._pose_failures(rt,bind,limits)
            result=tool.run('apply_last_pose')
            assert not result['failed'],result
            assert not tool._pose_failures(rt,initial,limits),(cycle,tool._pose_failures(rt,initial,limits))
            assert tracks()==expected_tracks
            for node,matrix in initial:
                errors=tool._pose_error(node.transform,matrix)
                maximum=[max(a,b) for a,b in zip(maximum,errors)]
        # Partial write failure must keep the cache and restore the full rig.
        cached=tool._pose_memory().last_pose
        setter=tool._set_pose
        calls=[0]
        def fail_once(*args):
            calls[0]+=1
            if calls[0]==3:
                raise RuntimeError('injected PRS round-trip failure')
            return setter(*args)
        tool._set_pose=fail_once
        try:
            rejected=tool.run('bind_pose')
        finally:
            tool._set_pose=setter
        assert rejected['failed'] and '已恢复并读回核对' in rejected['errors'][0]['error'],rejected
        assert not tool._pose_failures(rt,initial,limits),(cycle,tool._pose_failures(rt,initial,limits))
        assert tool._pose_memory().last_pose is cached
        assert tracks()==expected_tracks
        assert tool._matches(rt,mesh,skin,weights)
        # A real user edit must replace the cache instead of freezing it.
        with pymxs.animate(False):
            rt.rotate(bones[-1],rt.eulerAngles(2,3,4))
        edited=tool._capture_pose(rt,bones)
        bound=tool.run('bind_pose')
        assert not bound['failed'],bound
        assert tool._pose_memory().last_pose == edited
        assert tool._pose_memory().last_pose is not cached
        applied=tool.run('apply_last_pose')
        assert not applied['failed'],applied
    finally:
        tool._activate=activate
    # A genuine scale change still uses the existing world-matrix path.
    node=rt.Dummy(name='RealScaleChange')
    target=rt.scaleMatrix(rt.Point3(1.3,.8,1.5))*rt.rotateYMatrix(20)*rt.transMatrix(rt.Point3(4,5,6))
    scale_limits=tool._pose_limits(rt,[(node,target)])
    tool._set_pose(rt,node,target,scale_limits)
    assert tool._pose_equal(node.transform,target,scale_limits)
    return dict(cycles=30,original_tolerance=True,maximum_vs_initial=maximum,
                controllers_and_key_times_preserved=True,skin_untouched=True,
                weights_preserved=True,rollback_verified=True,real_scale_change=True,
                original_cache_reused=True,user_edit_updates_cache=True)
