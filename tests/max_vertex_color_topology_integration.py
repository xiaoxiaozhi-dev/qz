"""Only called inside the isolated vertex-color native batch harness."""
from __future__ import print_function
import math
from pymxs import runtime as rt
from max_actions import vertex_color as action
from test_vertex_color_topology import strip, tube, row_edges, ring_edges, real_mixed_panel


def run():
    checks = []
    channels = (0,-1,-2,17,18)
    def check(name,condition):
        if not condition:
            raise AssertionError(name)
        checks.append('topology: '+name)
    def close(a,b): return all(abs(x-y)<1e-6 for x,y in zip(a,b))
    def faces_of(obj):
        return [(i,[int(v) for v in rt.polyop.getFaceVerts(obj,i)])
                for i in range(1,int(rt.polyop.getNumFaces(obj))+1)]
    def geometry(obj):
        return ([tuple(rt.polyop.getVert(obj,i)) for i in range(1,int(rt.polyop.getNumVerts(obj))+1)],
                faces_of(obj),str(obj.transform))
    def snapshot(obj,ch):
        if not rt.polyop.getMapSupport(obj,ch): return None
        return dict(colors=[tuple(rt.polyop.getMapVert(obj,ch,i)) for i in range(1,int(rt.polyop.getNumMapVerts(obj,ch))+1)],
                    faces=[list(rt.polyop.getMapFace(obj,ch,i)) for i in range(1,int(rt.polyop.getNumFaces(obj))+1)])
    def snapshots(obj): return {ch:snapshot(obj,ch) for ch in channels}
    def shared(obj,ch):
        rt.polyop.setMapSupport(obj,ch,True)
        rt.polyop.setNumMapVerts(obj,ch,1,keep=False)
        rt.polyop.setMapVert(obj,ch,1,rt.Point3(.123456789,.345678912,.567891234))
        for fid,face in faces_of(obj): rt.polyop.setMapFace(obj,ch,fid,rt.Array(*([1]*len(face))))
    def create(triangles,mixed=False,hole=False,closed=False):
        faces,rows=(tube if closed else strip)(triangles=triangles,mixed=mixed)
        # Keep the defect in an interior band for both strip and tube widths.
        # Removing a rim triangle can be a valid open/closed transition.
        if hole: faces.pop(len(faces)//2)
        obj=rt.mesh(vertices=rt.Array(),faces=rt.Array())
        rt.convertToPoly(obj)
        obj.name='TEMP_QZ_TOPOLOGY_GRADIENT'
        for i,row in enumerate(rows):
            for j,v in enumerate(row):
                angle=i*math.pi/2.
                radius=12+j*.7
                if closed:
                    around=2.*math.pi*j/len(row)
                    position=rt.Point3(2*i+math.cos(around)*5,math.sin(around)*5,-i*i)
                else:
                    position=rt.Point3(radius*math.cos(angle),radius*math.sin(angle),-i*i)
                assert int(rt.polyop.createVert(obj,position))==v
        for face in faces: rt.polyop.createPolygon(obj,rt.Array(*face))
        rt.select(obj)
        rt.setCommandPanelTaskMode(rt.Name('modify'))
        rt.modPanel.setCurrentObject(obj.baseObject)
        rt.subObjectLevel=2
        if rt.polyop.getMapSupport(obj,1): rt.polyop.setMapSupport(obj,1,False)
        return obj,rows
    def select_edges(obj,edges):
        wanted=set(edges)
        ids=[i for i in range(1,int(rt.polyop.getNumEdges(obj))+1)
             if tuple(sorted(int(v) for v in rt.polyop.getEdgeVerts(obj,i))) in wanted]
        rt.subObjectLevel=2
        rt.polyop.setEdgeSelection(obj,rt.execute('#{'+','.join(str(i) for i in ids)+'}'))
    def record(obj,row,closed=False):
        select_edges(obj,(ring_edges if closed else row_edges)(row))
        result=action.vertex_color_gradient_edges()
        assert result.get('record'),repr(result)
        return result['record']
    def apply(first,last,ch=0,**params):
        options=dict(direction='topology',start=first,end=last,minimum=32,maximum=224,curve=25,
                     mask=[True,False,True],channel=ch)
        options.update(params)
        return action.vertex_color_gradient(**options)
    cases=((False,False,1,5,False),(True,False,1,5,False),
           (True,True,5,1,False),(True,False,0,6,False),
           (False,False,1,5,True),(True,False,1,5,True),
           (True,True,5,1,True),(True,False,0,6,True))
    for case,(triangles,mixed,a,b,closed) in enumerate(cases):
        obj,rows=create(triangles,mixed,closed=closed)
        for ch in channels: shared(obj,ch)
        saved,display,geo=snapshots(obj),action._display_state(obj),geometry(obj)
        first,last=record(obj,rows[a],closed),record(obj,rows[b],closed)
        check('case %s record is read only'%case,snapshots(obj)==saved and display==action._display_state(obj) and geometry(obj)==geo)
        check('case %s no UV required'%case,not rt.polyop.getMapSupport(obj,1))
        expected_rows=rows[a:b+1] if a<b else list(reversed(rows[b:a+1]))
        expected={v:action.gradient_value(i/float(len(expected_rows)-1),32,224,25)
                  for i,row in enumerate(expected_rows) for v in row}
        local_faces=faces_of(obj)
        rt.subObjectLevel=0
        rt.polyop.setEdgeSelection(obj,rt.execute('#{}'))
        for ch in channels:
            before=snapshots(obj)
            result=apply(first,last,ch)
            check('case %s channel %s apply uses records not current selection'%(case,ch),result.get('rows')==len(expected_rows))
            actual=snapshot(obj,ch)
            values_ok,outside_ok,masked_ok=True,True,True
            for fid,face in local_faces:
                mids=actual['faces'][fid-1]
                for k,(v,mid) in enumerate(zip(face,mids)):
                    color=actual['colors'][int(mid)-1]
                    old=before[ch]['colors'][int(before[ch]['faces'][fid-1][k])-1]
                    if v in expected:
                        value=expected[v]
                        values_ok=values_ok and close(color,(value,value,value) if ch==-2 else (value,old[1],value))
                        masked_ok=masked_ok and (ch==-2 or color[1]==old[1])
                    else:
                        outside_ok=outside_ok and color==old
            check('case %s channel %s equal rows range and curve'%(case,ch),values_ok)
            check('case %s channel %s unselected vertices preserved'%(case,ch),outside_ok)
            boundary_ok=True
            for v in expected_rows[0]+expected_rows[-1]:
                values=[actual['colors'][int(actual['faces'][fid-1][k])-1]
                        for fid,face in local_faces for k,gv in enumerate(face) if gv==v]
                boundary_ok=boundary_ok and all(close(color,(expected[v],)*3) if ch==-2 else
                                               abs(color[0]-expected[v])<1e-6 and abs(color[2]-expected[v])<1e-6
                                               for color in values)
            check('case %s channel %s both sides of endpoints continuous'%(case,ch),boundary_ok)
            check('case %s channel %s masked float preserved'%(case,ch),masked_ok)
            check('case %s channel %s other channels isolated'%(case,ch),all(snapshot(obj,c)==before[c] for c in channels if c!=ch))
            again=apply(first,last,ch)
            check('case %s channel %s repeat no map growth'%(case,ch),again['split']==0 and snapshot(obj,ch)==actual)
        check('case %s geometry and selection intact'%case,geometry(obj)==geo and int(rt.subObjectLevel)==0 and not action._ids(rt,rt.polyop.getEdgeSelection(obj)))
        # Color changes and deformations leave endpoint topology snapshots valid.
        rt.polyop.setVert(obj,rows[3][1],rt.Point3(100.,-80.,40.))
        check('case %s deformation preserves endpoint records'%case,apply(first,last).get('rows')==len(expected_rows))
        original_write=action._write
        def failed(*args):
            original_write(*args)
            raise RuntimeError('native topology post-write failure')
        for ch in channels:
            before=snapshots(obj)
            display=action._display_state(obj)
            action._write=failed
            caught=False
            try: apply(first,last,ch,minimum=0,maximum=255)
            except RuntimeError: caught=True
            finally: action._write=original_write
            check('case %s channel %s restores shared data and display after failure'%(case,ch),
                  caught and snapshots(obj)==before and action._display_state(obj)==display)
        # Changing topology after recording must reject before any map allocation.
        rt.polyop.createVert(obj,rt.Point3(0,0,0))
        before=snapshots(obj)
        check('case %s topology change invalidates endpoints'%case,bool(apply(first,last).get('warning')) and snapshots(obj)==before)
        rt.delete(obj)
        replacement,new_rows=create(triangles,mixed,closed=closed)
        check('case %s deleted node and same-name replacement rejected'%case,bool(apply(first,last).get('warning')))
        rt.delete(replacement)

    # A regular clothing panel inside an object with other n-gons. Also test
    # an n-gon attached to the same element: element-scoped checks must reject.
    for attached in (False,True):
        obj,rows=create(False)
        if attached:
            extra=[int(rt.polyop.createVert(obj,rt.Point3(*p)))
                   for p in ((12.,-3.,0.),(12.35,-5.,0.),(12.7,-3.,0.))]
            ngon=[rows[0][1],rows[0][0]]+extra
        else:
            ngon=[int(rt.polyop.createVert(obj,rt.Point3(*p)))
                  for p in ((100.,0.,0.),(102.,0.,0.),(103.,1.,0.),(101.,3.,0.),(99.,1.,0.))]
        fid=int(rt.polyop.createPolygon(obj,rt.Array(*ngon)))
        check('clothing attached=%s actual five-sided face exists'%attached,
              len(list(rt.polyop.getFaceVerts(obj,fid)))==5)
        element=action._ids(rt,rt.polyop.getElementsUsingFace(obj,rt.Array(1)))
        check('clothing attached=%s Max native element matches expected scope'%attached,(fid in element)==attached)
        for ch in channels: shared(obj,ch)
        a,b=(0,6) if attached else (1,5)
        first,last=record(obj,rows[a]),record(obj,rows[b])
        original=geometry(obj)
        for ch in channels:
            before=snapshot(obj,ch)
            result=apply(first,last,ch)
            if attached:
                check('clothing channel %s same-element ngon rejected even beyond seeds'%ch,
                      bool(result.get('warning')) and snapshot(obj,ch)==before)
            else:
                check('clothing channel %s other-element ngon ignored'%ch,result.get('rows')==b-a+1)
            after=snapshot(obj,ch)
            check('clothing attached=%s channel %s outside ngon colors intact'%(attached,ch),
                  after['faces'][fid-1]==before['faces'][fid-1] and
                  all(after['colors'][int(mid)-1]==before['colors'][int(mid)-1]
                      for mid in before['faces'][fid-1]))
        check('clothing attached=%s geometry including ngon unchanged'%attached,geometry(obj)==original)
        if not attached:
            other=record(obj,ngon[:3])
            before=snapshots(obj)
            check('cross-element line records rejected before writing',
                  bool(apply(first,other).get('warning')) and snapshots(obj)==before)
        rt.delete(obj)

    obj,rows=create(True)
    first,last=record(obj,rows[0]),record(obj,rows[-1])
    for ch in channels:
        if rt.polyop.getMapSupport(obj,ch): rt.polyop.setMapSupport(obj,ch,False)
    for ch in channels:
        before=snapshots(obj)
        original_write=action._write
        def failed_initialization(*args):
            original_write(*args)
            raise RuntimeError('native topology failed initialization')
        action._write=failed_initialization
        caught=False
        try: apply(first,last,ch)
        except RuntimeError: caught=True
        finally: action._write=original_write
        check('missing channel %s failure removes allocation'%ch,caught and snapshots(obj)==before)
        result=apply(first,last,ch)
        check('missing channel %s initializes successfully'%ch,result['initialized'] and result['rows']==7)
    select_edges(obj,row_edges(rows[0])[:2])
    partial=action.vertex_color_gradient_edges()['record']
    before=snapshots(obj)
    check('partial-width seed rejected before writing',bool(apply(partial,last).get('warning')) and snapshots(obj)==before)
    action._TOPOLOGY_RECORDS.clear()
    check('action-session reset invalidates old records',bool(apply(first,last).get('warning')) and snapshots(obj)==before)
    rt.delete(obj)
    obj,rows=create(True,hole=True)
    first,last=record(obj,rows[0]),record(obj,rows[-1])
    before=snapshots(obj)
    check('interior topology defect rejected before allocation',bool(apply(first,last).get('warning')) and snapshots(obj)==before)
    rt.delete(obj)
    for defect in ('mixed','partial','hole'):
        obj,rows=create(True,hole=defect=='hole',closed=True)
        first,last=record(obj,rows[0],True),record(obj,rows[-1],True)
        if defect in ('mixed','partial'):
            select_edges(obj,ring_edges(rows[0])[:-1])
            first=action.vertex_color_gradient_edges()['record']
            if defect=='partial':
                select_edges(obj,ring_edges(rows[-1])[:-1])
                last=action.vertex_color_gradient_edges()['record']
        before=snapshots(obj)
        check('tube %s rejected without map writes'%defect,
              bool(apply(first,last).get('warning')) and snapshots(obj)==before)
        rt.delete(obj)

    for closed in (False,True):
        obj,rows=create(False,closed=closed)
        wanted=(ring_edges if closed else row_edges)(rows[3])
        select_edges(obj,[wanted[1]])
        saved,geo,display=snapshots(obj),geometry(obj),action._display_state(obj)
        result=action.vertex_color_gradient_edges()
        picked=action._ids(rt,rt.polyop.getEdgeSelection(obj))
        actual=set(tuple(sorted(int(v) for v in rt.polyop.getEdgeVerts(obj,i))) for i in picked)
        check('single edge closed=%s completes and highlights exact row'%closed,
              result.get('expanded') and actual==set(wanted) and result['edges']==len(wanted))
        check('single edge closed=%s preserves geometry maps display and level'%closed,
              snapshots(obj)==saved and geometry(obj)==geo and action._display_state(obj)==display and int(rt.subObjectLevel)==2)
        rt.delete(obj)

    obj,rows=create(True)
    select_edges(obj,[row_edges(rows[3])[1]])
    saved=snapshots(obj)
    picked=action._ids(rt,rt.polyop.getEdgeSelection(obj))
    records=list(action._TOPOLOGY_RECORDS)
    result=action.vertex_color_gradient_edges()
    check('ambiguous triangle single edge preserves selection maps records',
          bool(result.get('warning')) and snapshots(obj)==saved and picked==action._ids(rt,rt.polyop.getEdgeSelection(obj))
          and records==list(action._TOPOLOGY_RECORDS))
    reference=record(obj,rows[0])
    select_edges(obj,[row_edges(rows[3])[1]])
    saved=snapshots(obj)
    result=action.vertex_color_gradient_edges(reference=reference)
    check('triangle single edge uses other recorded row and highlights',
          result.get('expanded') and result.get('edges')==len(row_edges(rows[3])) and snapshots(obj)==saved)
    rt.delete(obj)

    # Reconstruct the user's actual closed-to-open panel in this isolated Max.
    fixture=real_mixed_panel()
    obj=rt.mesh(vertices=rt.Array(),faces=rt.Array())
    rt.convertToPoly(obj)
    obj.name='TEMP_QZ_REAL_MIXED_PANEL'
    for position in fixture['positions']: rt.polyop.createVert(obj,rt.Point3(*position))
    for face in fixture['faces']: rt.polyop.createPolygon(obj,rt.Array(*face))
    rt.select(obj)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(obj.baseObject)
    rt.subObjectLevel=2
    for ch in channels: shared(obj,ch)
    original=geometry(obj)
    check('real mixed panel reconstructed as 171 native faces',len(faces_of(obj))==171)
    for index,first_edges in enumerate(fixture['starts']):
        select_edges(obj,first_edges)
        first=action.vertex_color_gradient_edges()['record']
        for seed in fixture['end']:
            select_edges(obj,[seed])
            before=snapshots(obj)
            result=action.vertex_color_gradient_edges(reference=first)
            picked=action._ids(rt,rt.polyop.getEdgeSelection(obj))
            selected=set(tuple(sorted(int(v) for v in rt.polyop.getEdgeVerts(obj,i))) for i in picked)
            check('real panel reference %s seed %s highlights full open row'%(index,seed),
                  result.get('expanded') and selected==set(fixture['end']) and snapshots(obj)==before)
            last=result['record']
        for reverse in (False,True):
            row_list=list(reversed(fixture['rows'][index])) if reverse else fixture['rows'][index]
            expected={v:action.gradient_value(i/float(len(row_list)-1),32,224,25)
                      for i,row in enumerate(row_list) for v in row}
            for ch in channels:
                before=snapshots(obj)
                result=apply(last if reverse else first,first if reverse else last,ch)
                label='real mixed %s reverse=%s channel=%s'%(index,reverse,ch)
                check(label+' row count',result.get('rows')==len(row_list))
                actual=snapshot(obj,ch)
                values_ok,outside_ok=True,True
                for fid,face in faces_of(obj):
                    for k,v in enumerate(face):
                        color=actual['colors'][int(actual['faces'][fid-1][k])-1]
                        old=before[ch]['colors'][int(before[ch]['faces'][fid-1][k])-1]
                        if v in expected:
                            value=expected[v]
                            values_ok=values_ok and close(color,(value,)*3 if ch==-2 else (value,old[1],value))
                        else: outside_ok=outside_ok and color==old
                check(label+' all incident corners including endpoints',values_ok)
                check(label+' other vertices preserved',outside_ok)
                check(label+' other channels preserved',all(snapshot(obj,c)==before[c] for c in channels if c!=ch))
                again=apply(last if reverse else first,first if reverse else last,ch)
                check(label+' repeated apply no map growth',again['split']==0 and snapshot(obj,ch)==actual)
    check('real mixed native geometry unchanged',geometry(obj)==original)
    rt.delete(obj)
    return checks
