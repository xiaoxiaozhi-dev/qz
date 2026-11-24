"""Native transfer checks, run only by the isolated Max batch harness."""
from __future__ import print_function
import json
import math
import struct
import os
import random
import time
from pymxs import runtime as rt
from max_actions import vertex_color as action
from max_actions._vertex_color_nearest import NearestIndex


def run():
    checks=[]
    channels=(0,-1,-2,17,18)
    def rounded(value):
        # Independent oracle: compare normalized Point3 input against the
        # normalized float32 half-byte threshold, rather than multiplying
        # the source's float32 error in Python double precision.
        value=min(1.,max(0.,value))
        byte=int(math.floor(value*255.0))
        threshold=struct.unpack('f',struct.pack('f',(byte+.5)/255.0))[0]
        if value>=threshold:byte+=1
        return struct.unpack('f',struct.pack('f',byte/255.0))[0]
    def quantized(color):return tuple(rounded(v) for v in color)
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append('transfer: '+name)
    def create(name,points,faces):
        obj=rt.mesh(vertices=rt.Array(),faces=rt.Array())
        rt.convertToPoly(obj);obj.name=name
        for p in points:rt.polyop.createVert(obj,rt.Point3(*p))
        for f in faces:rt.polyop.createPolygon(obj,rt.Array(*f))
        return obj
    source=create('TEMP_QZ_TRANSFER_SOURCE',((0,0,0),(10,0,0),(10,10,0),(0,10,0)),((1,2,3),(1,3,4)))
    target=create('TEMP_QZ_TRANSFER_TARGET',((0,0,0),(5,0,0),(10,0,0),(0,10,0),(5,10,0),(10,10,0)),((1,2,5,4),(2,3,6,5)))
    def activate(obj,ids):
        rt.select(obj);rt.setCommandPanelTaskMode(rt.Name('modify'))
        rt.modPanel.setCurrentObject(obj.baseObject);rt.subObjectLevel=1
        rt.polyop.setVertSelection(obj,rt.Array(*ids))
    def snapshot(obj):
        result={}
        for ch in channels:
            if not rt.polyop.getMapSupport(obj,ch):result[ch]=None;continue
            result[ch]=dict(colors=[tuple(rt.polyop.getMapVert(obj,ch,i)) for i in range(1,int(rt.polyop.getNumMapVerts(obj,ch))+1)],
                            faces=[list(rt.polyop.getMapFace(obj,ch,i)) for i in (1,2)])
        return result
    def geometry(obj):
        return ([tuple(rt.polyop.getVert(obj.baseObject,i)) for i in range(1,int(rt.polyop.getNumVerts(obj))+1)],
                [list(rt.polyop.getFaceVerts(obj,i)) for i in (1,2)],str(obj.transform))
    def corner(obj,ch,fid,k):
        mid=int(list(rt.polyop.getMapFace(obj,ch,fid))[k])
        return tuple(rt.polyop.getMapVert(obj,ch,mid))
    def paste(ch,mask=(True,False,True)):
        return action.vertex_color_paste(channel=ch,mask=None if ch==-2 else list(mask))
    raw=((.123456789,.234567891,.345678912),(.8,.3,.9),(.4,.5,.6),(.6,.7,.8),(.9,.8,.7))
    for ch in channels:
        rt.polyop.setMapSupport(source,ch,True);rt.polyop.setNumMapVerts(source,ch,5,keep=False)
        for i,value in enumerate(raw,1):rt.polyop.setMapVert(source,ch,i,rt.Point3(*value))
        rt.polyop.setMapFace(source,ch,1,rt.Array(5,2,3))
        rt.polyop.setMapFace(source,ch,2,rt.Array(1,3,4))
        rt.polyop.setMapSupport(target,ch,True);rt.polyop.setNumMapVerts(target,ch,1,keep=False)
        rt.polyop.setMapVert(target,ch,1,rt.Point3(.71,.61,.51))
        for fid in (1,2):rt.polyop.setMapFace(target,ch,fid,rt.Array(1,1,1,1))
    source_geo,target_geo=geometry(source),geometry(target)
    for ch in channels:
        activate(source,[1,2,3,4]);before=snapshot(source);display=action._display_state(source)
        copied=action.vertex_color_copy(channel=ch)
        src1=quantized(tuple(rt.polyop.getMapVert(source,ch,1)));src2=quantized(tuple(rt.polyop.getMapVert(source,ch,2)))
        check('channel %s copy is read only and all four geo vertices'%ch,copied['count']==4 and snapshot(source)==before and action._display_state(source)==display
              and action._ids(rt,rt.polyop.getVertSelection(source))==[1,2,3,4])
        index=action._COLOR_CLIPBOARD['index']
        activate(target,[1,2,3]);old=snapshot(target)
        result=paste(ch)
        expected1=src1 if ch==-2 else (src1[0],old[ch]['colors'][0][1],src1[2])
        expected2=src2 if ch==-2 else (src2[0],old[ch]['colors'][0][1],src2[2])
        check('channel %s minimum source map ID and tied source geo ID'%ch,
              corner(target,ch,1,0)==expected1 and corner(target,ch,1,1)==expected1 and corner(target,ch,2,0)==expected1)
        check('channel %s different topology exact nearest point'%ch,corner(target,ch,2,1)==expected2 and result['vertices']==3)
        check('channel %s outside corners retain shared map value'%ch,
              all(corner(target,ch,fid,k)==old[ch]['colors'][0] for fid,k in ((1,2),(1,3),(2,2),(2,3))))
        check('channel %s other channels and source unchanged'%ch,
              all(snapshot(target)[c]==old[c] for c in channels if c!=ch) and snapshot(source)==before)
        check('channel %s selection level retained'%ch,
              action._ids(rt,rt.polyop.getVertSelection(target))==[1,2,3] and int(rt.subObjectLevel)==1)
        saved=snapshot(target)
        again=paste(ch)
        check('channel %s repeat reuses index and does not grow maps'%ch,
              again['split']==0 and snapshot(target)==saved and action._COLOR_CLIPBOARD['index'] is index)
        # Existing distinct target map IDs are unified only in enabled components.
        current=int(rt.polyop.getNumMapVerts(target,ch));rt.polyop.setNumMapVerts(target,ch,current+1,keep=True)
        rt.polyop.setMapVert(target,ch,current+1,rt.Point3(.8,.987654321,.6))
        mapping=[int(v) for v in rt.polyop.getMapFace(target,ch,2)];mapping[0]=current+1
        rt.polyop.setMapFace(target,ch,2,rt.Array(*mapping))
        old_green=corner(target,ch,2,0)[1]
        paste(ch)
        a,b=corner(target,ch,1,1),corner(target,ch,2,0)
        check('channel %s seams unified with unchecked component preserved'%ch,
              a==b if ch==-2 else a[0]==b[0] and a[2]==b[2] and b[1]==old_green)
        rt.polyop.setMapSupport(target,ch,False)
        initialized=paste(ch)
        check('channel %s missing target initializes white outside'%ch,
              initialized['initialized'] and all(corner(target,ch,fid,k)==(1.,)*3 for fid,k in ((1,2),(1,3),(2,2),(2,3))))
        original=action._write
        def failed(*args):
            original(*args);raise RuntimeError('native transfer post-write failure')
        for missing in (False,True):
            if missing:rt.polyop.setMapSupport(target,ch,False)
            saved,display=snapshot(target),action._display_state(target)
            action._write=failed;caught=False
            try:paste(ch,mask=(True,True,True))
            except RuntimeError:caught=True
            finally:action._write=original
            check('channel %s restores data and display after failure missing=%s'%(ch,missing),
                  caught and snapshot(target)==saved and action._display_state(target)==display)
    for source_ch in channels:
        activate(source,[1,2,3,4]);action.vertex_color_copy(channel=source_ch)
        raw_source=quantized(tuple(rt.polyop.getMapVert(source,source_ch,1)))
        for target_ch in channels:
            rt.polyop.setMapSupport(target,target_ch,True)
            rt.polyop.setNumMapVerts(target,target_ch,1,keep=False)
            rt.polyop.setMapVert(target,target_ch,1,rt.Point3(.71,.61,.51))
            for fid in (1,2):rt.polyop.setMapFace(target,target_ch,fid,rt.Array(1,1,1,1))
            activate(target,[1]);before=snapshot(target)
            result=paste(target_ch)
            expected=raw_source if target_ch==-2 else (raw_source[0],before[target_ch]['colors'][0][1],raw_source[2])
            label='cross channel %s to %s'%(source_ch,target_ch)
            check(label+' rounded triple or destination RGB mask',corner(target,target_ch,1,0)==expected)
            check(label+' source and target metadata',result['source_channel']==source_ch and result['channel']==target_ch)
            check(label+' other channels and unselected corners preserved',
                  all(snapshot(target)[c]==before[c] for c in channels if c!=target_ch)
                  and corner(target,target_ch,1,1)==before[target_ch]['colors'][0])
    check('source and target geometry unchanged by all transfers',geometry(source)==source_geo and geometry(target)==target_geo)
    activate(source,[1,2,3,4]);previous=action._COLOR_CLIPBOARD
    rt.polyop.setMapSupport(source,17,False)
    check('missing source channel keeps clipboard',bool(action.vertex_color_copy(channel=17).get('warning')) and action._COLOR_CLIPBOARD is previous)
    activate(target,[1]);before=snapshot(target)
    cross=action.vertex_color_paste(channel=0,mask=[True]*3)
    check('different clipboard channel pastes raw data',cross.get('source_channel')==18 and
          corner(target,0,1,0)==quantized(tuple(rt.polyop.getMapVert(source,18,1))))
    before=snapshot(target)
    action._COLOR_CLIPBOARD=None
    check('empty clipboard never initializes target',bool(action.vertex_color_paste(channel=0,mask=[True]*3).get('warning')) and snapshot(target)==before)
    activate(source,[1]);action.vertex_color_copy(channel=0)
    activate(source,[2]);action.vertex_color_paste(channel=0,mask=[True]*3)
    check('same-object paste uses copied snapshot',corner(source,0,1,1)==quantized(tuple(rt.polyop.getMapVert(source,0,1))))
    # Explicitly compare transformed source coordinates with Max's world oracle.
    source.transform=rt.Matrix3(rt.Point3(0,2,0),rt.Point3(-3,0,0),rt.Point3(0,0,4),rt.Point3(100,-50,25))
    source.objectOffsetPos=rt.Point3(2,-3,4)
    activate(source,[1,3,4]);action.vertex_color_copy(channel=0)
    copied_color=quantized(tuple(rt.polyop.getMapVert(source,0,1)))
    world=rt.execute('(fn qz_transfer_world_vertex obj v = (in coordsys world (polyop.getVert obj v)))')
    target_position=world(source,1)
    rt.polyop.setVert(target,1,target_position)
    rt.polyop.setMapVert(source,0,1,rt.Point3(.9,.9,.9))
    rt.delete(source)
    activate(target,[1]);action.vertex_color_paste(channel=0,mask=[True]*3)
    check('world position transform pivot and deleted source snapshot',corner(target,0,1,0)==copied_color)
    rt.delete(target)

    source=create('TEMP_QZ_MIRROR_SOURCE',((0,0,0),(10,0,0),(10,10,0),(0,10,0)),((1,2,3),(1,3,4)))
    target=create('TEMP_QZ_MIRROR_TARGET',((0,0,0),(5,0,0),(10,0,0),(0,10,0),(5,10,0),(10,10,0)),((1,2,5,4),(2,3,6,5)))
    source.transform=rt.Matrix3(rt.Point3(0,2,0),rt.Point3(-3,0,0),rt.Point3(0,0,4),rt.Point3(100,-50,25))
    source.objectOffsetPos=rt.Point3(2,-3,4)
    target.transform=rt.Matrix3(rt.Point3(0,-2,0),rt.Point3(3,0,0),rt.Point3(0,0,2),rt.Point3(-250,80,-10))
    target.objectOffsetPos=rt.Point3(-3,2,1)
    set_world=rt.execute('(fn qz_mirror_set_world obj v p = (in coordsys world (polyop.setVert obj v p)))')
    source_world=[tuple(world(source,i)) for i in range(1,5)]
    for vertex,source_vertex in enumerate((1,2,2,4,3,3),1):
        p=source_world[source_vertex-1]
        set_world(target,vertex,rt.Point3(-p[0],p[1],p[2]))
        q=tuple(world(target,vertex))
        check('mirror transformed target oracle vertex %s'%vertex,
              all(abs(a-b)<.0001 for a,b in zip(q,(-p[0],p[1],p[2]))))
    for ch in channels:
        rt.polyop.setMapSupport(source,ch,True);rt.polyop.setNumMapVerts(source,ch,5,keep=False)
        for i,value in enumerate(raw,1):rt.polyop.setMapVert(source,ch,i,rt.Point3(*value))
        rt.polyop.setMapFace(source,ch,1,rt.Array(5,2,3))
        rt.polyop.setMapFace(source,ch,2,rt.Array(1,3,4))
    source_geo,target_geo=geometry(source),geometry(target)
    for source_ch in channels:
        activate(source,[1,2,3,4]);action.vertex_color_copy(channel=source_ch)
        clipboard=action._COLOR_CLIPBOARD
        src1=quantized(tuple(rt.polyop.getMapVert(source,source_ch,1)))
        src2=quantized(tuple(rt.polyop.getMapVert(source,source_ch,2)))
        for target_ch in channels:
            rt.polyop.setMapSupport(target,target_ch,True);rt.polyop.setNumMapVerts(target,target_ch,1,keep=False)
            rt.polyop.setMapVert(target,target_ch,1,rt.Point3(.71,.61,.51))
            for fid in (1,2):rt.polyop.setMapFace(target,target_ch,fid,rt.Array(1,1,1,1))
            activate(target,[1,2,3]);before=snapshot(target)
            result=action.vertex_color_paste(channel=target_ch,mask=[True,False,True],mirror_x=True)
            a=src1 if target_ch==-2 else (src1[0],before[target_ch]['colors'][0][1],src1[2])
            b=src2 if target_ch==-2 else (src2[0],before[target_ch]['colors'][0][1],src2[2])
            label='mirror channel %s to %s'%(source_ch,target_ch)
            check(label+' transformed world X=0 and rounded components',corner(target,target_ch,1,0)==a
                  and corner(target,target_ch,1,1)==b and corner(target,target_ch,2,0)==b and corner(target,target_ch,2,1)==b)
            check(label+' outside and other channels preserved',
                  all(corner(target,target_ch,fid,k)==before[target_ch]['colors'][0] for fid,k in ((1,2),(1,3),(2,2),(2,3)))
                  and all(snapshot(target)[ch]==before[ch] for ch in channels if ch!=target_ch))
            check(label+' mirror and channel metadata',result['mirror_x'] and result['source_channel']==source_ch and result['channel']==target_ch)
            saved=snapshot(target)
            repeat=action.vertex_color_paste(channel=target_ch,mask=[True,False,True],mirror_x=True)
            check(label+' index reused and maps stable',repeat['split']==0 and snapshot(target)==saved and action._COLOR_CLIPBOARD is clipboard)
        # Each missing destination channel also exercises mirror failure rollback.
        rt.polyop.setMapSupport(target,source_ch,False)
        original=action._write
        def failed_mirror(*args):
            original(*args);raise RuntimeError('native mirror post-write failure')
        saved,display=snapshot(target),action._display_state(target)
        action._write=failed_mirror;caught=False
        try:action.vertex_color_paste(channel=source_ch,mask=[True]*3,mirror_x=True)
        except RuntimeError:caught=True
        finally:action._write=original
        check('mirror missing channel %s rollback'%source_ch,caught and snapshot(target)==saved and action._display_state(target)==display)
        result=action.vertex_color_paste(channel=source_ch,mask=[True]*3,mirror_x=True)
        check('mirror missing channel %s initialized'%source_ch,result['initialized'] and corner(target,source_ch,1,0)==src1)
    check('mirror preserves geometry and transforms',geometry(source)==source_geo and geometry(target)==target_geo)
    activate(source, [1, 2])
    action.vertex_color_copy(channel=0)
    expected_1 = quantized(tuple(rt.polyop.getMapVert(source, 0, 1)))
    expected_2 = quantized(tuple(rt.polyop.getMapVert(source, 0, 2)))
    initial = (.71, .61, .51)
    rt.polyop.setNumMapVerts(target, 17, 1, keep=False)
    rt.polyop.setMapVert(target, 17, 1, rt.Point3(*initial))
    for fid in (1, 2):
        rt.polyop.setMapFace(target, 17, fid, rt.Array(1, 1, 1, 1))
    shared = corner(target, 17, 1, 0)
    other_before = snapshot(target)
    activate(target, [1, 2, 3])
    result = action.vertex_color_paste(channel=17, mask=[True, False, True], by_vertex_id=True)
    check('ID paste on different vertex counts matches source IDs 1 and 2',
          result['vertices'] == 2 and result['skipped'] == 1
          and corner(target, 17, 1, 0) == (expected_1[0], shared[1], expected_1[2])
          and corner(target, 17, 1, 1) == (expected_2[0], shared[1], expected_2[2])
          and corner(target, 17, 2, 0) == corner(target, 17, 1, 1))
    check('ID paste protects shared unmatched vertex, outside selection and other channels',
          all(corner(target, 17, fid, k) == shared for fid, k in ((2, 1), (2, 2), (2, 3), (1, 2), (1, 3)))
          and all(snapshot(target)[ch] == other_before[ch] for ch in channels if ch != 17))
    activate(target, [3])
    saved = snapshot(target)
    result = action.vertex_color_paste(channel=17, mask=[True]*3, by_vertex_id=True)
    check('ID paste with no matching ID reports skipped and does not write',
          result.get('warning') and result['skipped'] == 1 and snapshot(target) == saved)
    # The copied world-space snapshot remains valid after deleting its source.
    activate(source, [1, 2, 3, 4])
    action.vertex_color_copy(channel=18)
    activate(target,[1]);expected=quantized(tuple(rt.polyop.getMapVert(source,18,1)));rt.delete(source)
    action.vertex_color_paste(channel=0,mask=[True]*3,mirror_x=True)
    check('mirror after source deletion',corner(target,0,1,0)==expected)
    rt.delete(target)

    # Timings isolate index construction/query, excluding Max mesh API and writes.
    timings=[]
    rng=random.Random(5301)
    for count in (10000,50000):
        samples=[(i+1,tuple(rng.uniform(-500,500) for unused in range(3)),(.123456789,.234567891,.345678912)) for i in range(count)]
        queries=[tuple(rng.uniform(-520,520) for unused in range(3)) for i in range(10000)]
        started=time.time();index=NearestIndex(samples);built=time.time()-started
        started=time.time();results=[index.nearest(q) for q in queries];queried=time.time()-started
        accurate=True
        for q,actual in zip(queries[:10],results[:10]):
            expected=min(samples,key=lambda s:(sum((q[i]-s[1][i])**2 for i in range(3)),s[0]))
            accurate=accurate and actual==expected
        check('index benchmark %s sources 10000 queries agrees with brute force'%count,accurate and len(results)==10000)
        timings.append(dict(sources=count,queries=10000,build_seconds=built,query_seconds=queried))
    root=os.environ.get('QZ_VERTEX_COLOR_PROJECT_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root,'temp','vertex-color-transfer-performance.json'),'w') as stream:
        json.dump(timings,stream,indent=2)
    return checks
