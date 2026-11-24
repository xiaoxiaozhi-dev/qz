# -*- coding: utf-8 -*-
"""Disposable cross-file coverage for local-rotation-only pose application."""
from __future__ import unicode_literals
import importlib,io,json,os,sys

def run(rt,tool,output):
    memory=tool._pose_memory()
    if hasattr(memory,'last_pose'):
        del memory.last_pose
    rt.resetMaxFile(rt.Name('noPrompt'))
    def character(padding=0):
        for i in range(padding):
            rt.Dummy(name='Padding%d'%i)
        group=rt.Dummy(name='角色组')
        group.pos=rt.Point3(10,5,2)
        root=rt.boneSys.createBone(rt.Point3(0,0,0),rt.Point3(0,0,10),rt.Point3(1,0,0))
        root.name,root.parent='RootBone',group
        child=rt.boneSys.createBone(rt.Point3(0,0,10),rt.Point3(0,0,18),rt.Point3(1,0,0))
        child.name,child.parent='SpineBone',root
        unused=rt.boneSys.createBone(rt.Point3(0,0,10),rt.Point3(5,0,10),rt.Point3(1,0,0))
        unused.name,unused.parent='UnusedBone',root
        com=rt.biped.createNew(100.,-90.,rt.Point3(0,0,0))
        com.parent=group
        biped=[n for n in rt.objects if tool._is_biped(rt,n) and n!=com and 'Footsteps' not in unicode(n.name)]
        mesh=rt.plane(name='CharacterMesh')
        skin=rt.Skin();rt.addModifier(mesh,skin);tool._activate(rt,mesh,skin)
        influences=[root,child]+biped
        for bone in influences:
            rt.skinOps.addBone(skin,bone,1)
        for v in range(1,int(rt.skinOps.GetNumberVertices(skin))+1):
            rt.skinOps.ReplaceVertexWeights(skin,v,rt.Array(1,2),rt.Array(.4,.6))
        arm=next(n for n in biped if unicode(n.name).endswith('L UpperArm'))
        return mesh,skin,root,child,unused,com,arm,influences,group
    target=character();rt.select(target[0])
    rejected=tool.run('apply_last_pose')
    assert rejected['failed'] and '还没有缓存姿势' in rejected['errors'][0]['error'],rejected
    target_file=os.path.join(output,'default-character.max')
    rt.saveMaxFile(target_file,quiet=True)
    rt.resetMaxFile(rt.Name('noPrompt'))
    mesh,skin,root,child,unused,com,arm,influences,group=character(7)
    rt.rotate(root,rt.eulerAngles(20,10,15));rt.rotate(child,rt.eulerAngles(5,25,10))
    rt.rotate(unused,rt.eulerAngles(10,5,8))
    rt.biped.setTransform(com,rt.Name('rotation'),rt.quat(20.,rt.Point3(0,0,1))*rt.biped.getTransform(com,rt.Name('rotation')),False)
    rt.biped.setTransform(arm,rt.Name('rotation'),rt.quat(25.,rt.Point3(1,0,0))*rt.biped.getTransform(arm,rt.Name('rotation')),False)
    expected=tool._capture_pose(rt,influences)
    handles={tool._pose_path(rt,n):tool._handle(rt,n) for n in tool._rig_pose_nodes(rt,influences)}
    rt.select(mesh);bound=tool.run('bind_pose')
    assert not bound['failed'] and memory.last_pose==expected,bound
    cached=memory.last_pose
    assert cached['mode']=='local_rotation'
    repeated=tool.run('bind_pose')
    assert not repeated['failed'] and memory.last_pose is cached,repeated
    json.dumps(cached,ensure_ascii=True)
    com_name=unicode(com.name)
    rt.loadMaxFile(target_file,quiet=True)
    mesh=rt.getNodeByName('CharacterMesh');rt.select(mesh)
    root=rt.getNodeByName('RootBone');child=rt.getNodeByName('SpineBone')
    group=rt.getNodeByName('角色组')
    group.pos=rt.Point3(35,50,-4)
    group.rotation=rt.quat(35.,rt.Point3(0,0,1));group.scale=rt.Point3(1.25,1.25,1.25)
    com=rt.getNodeByName(com_name)
    rt.biped.setTransform(com,rt.Name('pos'),rt.Point3(100,200,10),False)
    skin=tool._skin(rt,mesh,exclusive=False)
    before_weights=tool._snapshot(rt,mesh,skin)
    influences=tool._skin_nodes(rt,skin)
    index=tool._pose_index(rt,tool._rig_pose_nodes(rt,influences))
    assert any(tool._handle(rt,index[p])!=h for p,h in handles.items())
    targets=[(index[tuple(r['path'])],r['rotation']) for r in cached['rows']]
    originals=[(n,rt.copy(n.transform)) for n in index.values()]
    limits=tool._pose_limits(rt,originals)
    rt.execute('''fn qzLocalPositionScaleV10 n = (
        local p=[0,0,0], s=[1,1,1]
        if classof n.controller==PRS then (p=n.position.controller.value;s=n.scale.controller.value)
        else (
            local m=if n.parent==undefined then copy n.transform else n.transform*inverse n.parent.transform
            p=m.translationpart;s=m.scalepart
        )
        #(p.x,p.y,p.z,s.x,s.y,s.z)
    )''')
    def channels():
        return {p:tuple(float(v) for v in rt.qzLocalPositionScaleV10(n)) for p,n in index.items()}
    before_channels=channels()
    def same_channels():
        after=channels()
        failures=[]
        for path,a in before_channels.items():
            b=after[path]
            if any(abs(x-y)>limits[0] for x,y in zip(a[:3],b[:3])) or any(abs(x-y)>max(abs(x),1e-12)*2e-5 for x,y in zip(a[3:],b[3:])):
                failures.append((path,a,b))
        assert not failures,failures
    child.name='RenamedSpine';mismatch=tool.run('apply_last_pose')
    assert mismatch['failed'] and '不匹配' in mismatch['errors'][0]['error'],mismatch
    assert not tool._pose_failures(rt,originals,limits)
    child.name='SpineBone'
    duplicate=rt.boneSys.createBone(rt.Point3(0,0,10),rt.Point3(0,0,18),rt.Point3(1,0,0))
    duplicate.name,duplicate.parent='SpineBone',root
    ambiguous=tool.run('apply_last_pose')
    assert ambiguous['failed'] and '不唯一' in ambiguous['errors'][0]['error'],ambiguous
    rt.delete(duplicate)
    setter=tool._set_local_rotation;calls=[0]
    def fail_once(*args):
        calls[0]+=1
        if calls[0]==3:
            raise RuntimeError('injected local rotation failure')
        return setter(*args)
    before_rotations=[(n,tool._local_rotation(rt,n)) for n,_ in targets]
    tool._set_local_rotation=fail_once
    try: rejected=tool.run('apply_last_pose')
    finally: tool._set_local_rotation=setter
    assert rejected['failed'] and '已恢复并读回核对' in rejected['errors'][0]['error'],(rejected,tool._pose_failures(rt,originals,limits),tool._rotation_failures(rt,before_rotations))
    assert not tool._pose_failures(rt,originals,limits)
    same_channels();assert memory.last_pose is cached
    restored=tool.run('apply_last_pose')
    assert not restored['failed'] and restored['count']==1,restored
    assert not tool._rotation_failures(rt,targets),tool._rotation_failures(rt,targets)
    same_channels();assert tool._matches(rt,mesh,skin,before_weights)
    assert memory.last_pose is cached
    sys.modules.pop('max_actions.rigging_toolbox')
    reloaded=importlib.import_module('max_actions.rigging_toolbox')
    again=reloaded.run('apply_last_pose')
    assert not again['failed'] and not reloaded._rotation_failures(rt,targets),again
    same_channels();assert reloaded._pose_memory().last_pose is cached
    return dict(cross_file=True,bones=len(targets),changed_handles=True,empty_rejected=True,
                mismatch_rejected=True,duplicates_rejected=True,rollback_verified=True,
                cache_survives_reload=True,weights_preserved=True,reusable=True,
                local_rotation_only=True,position_scale_preserved=True,parent_transform_changed=True)
