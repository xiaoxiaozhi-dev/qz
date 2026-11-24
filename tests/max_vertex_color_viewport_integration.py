"""Read-only numeric viewport labels in an independent Max process."""
from __future__ import print_function, unicode_literals
import os
import time
from pymxs import runtime as rt
from max_actions import vertex_color as action
from max_actions import _vertex_color_viewport as numbers


def run():
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append('viewport: '+name)
    owner='a'*32
    obj=rt.mesh(name='TEMP_QZ_VIEWPORT_NUMBERS',vertices=rt.Array(rt.Point3(-20,0,0),rt.Point3(20,0,0),rt.Point3(20,40,0),rt.Point3(-20,40,0)),
                faces=rt.Array(rt.Point3(1,2,3),rt.Point3(1,3,4)))
    rt.convertToPoly(obj);rt.select(obj);rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(obj.baseObject);rt.subObjectLevel=1
    rt.polyop.setVertSelection(obj,rt.Array(1,2,3,4))
    for ch in (0,-1,-2,17,18):
        rt.polyop.setMapSupport(obj,ch,True);rt.polyop.setNumMapVerts(obj,ch,5,keep=False)
        for i,color in enumerate(((128/255.,64/255.,1.),(.1,.2,.3),(.4,.5,.6),(.7,.8,.9),(0.,0.,0.)),1):
            rt.polyop.setMapVert(obj,ch,i,rt.Point3(*color))
        rt.polyop.setMapFace(obj,ch,1,rt.Array(5,2,3));rt.polyop.setMapFace(obj,ch,2,rt.Array(1,3,4))
    def snapshot():
        return [(ch,[tuple(rt.polyop.getMapVert(obj,ch,i)) for i in range(1,int(rt.polyop.getNumMapVerts(obj,ch))+1)],
                 [list(rt.polyop.getMapFace(obj,ch,i)) for i in (1,2)]) for ch in (0,-1,-2,17,18)]
    before=snapshot();display=action._display_state(obj)
    for ch in (0,-1,-2,17,18):
        result=action.vertex_color_numbers(enabled=True,channel=ch,mask=[True]*3,owner=owner)
        check('native source compiles channel %s: %r'%(ch,result),result.get('enabled'))
        rows=list(rt.qz_vc_numbers_collect())
        check('all selected vertices sampled channel %s rows=%r'%(ch,rows),len(rows)==4)
        first=rows[0]
        check('smallest map ID and three independent integer components channel %s'%ch,list(first[2])==[128,64,255])
    check('display does not modify colors or viewport properties',snapshot()==before and action._display_state(obj)==display)
    rt.qz_vc_numbers_draw()
    check('draw completes without exception: %s'%rt.qz_vc_numbers_state.error,not str(rt.qz_vc_numbers_state.error))
    # Selection, edited map values and changed map faces are read afresh.
    rt.polyop.setVertSelection(obj,rt.Array(1))
    check('selection updates without restarting display',len(list(rt.qz_vc_numbers_collect()))==1)
    rt.polyop.setMapVert(obj,18,1,rt.Point3(12.49/255.,12.5/255.,12.51/255.))
    rows=list(rt.qz_vc_numbers_collect())
    check('external fractional values round for display only',list(rows[0][2])==[12,13,13])
    saved=tuple(rt.polyop.getMapVert(obj,18,1))
    rt.qz_vc_numbers_draw()
    check('reading fractions does not write rounded values',tuple(rt.polyop.getMapVert(obj,18,1))==saved)
    rt.polyop.setMapFace(obj,18,2,rt.Array(5,3,4))
    check('changed seam mapping is not stale',list(list(rt.qz_vc_numbers_collect())[0][2])==[0,0,0])
    rt.polyop.setMapFace(obj,18,2,rt.Array(1,3,4))
    rt.polyop.setMapVert(obj,18,1,rt.Point3(128/255.,64/255.,1.))

    # World coordinates include both object transform and pivot offsets.
    obj.transform=rt.Matrix3(rt.Point3(0,2,0),rt.Point3(-3,0,0),rt.Point3(0,0,4),rt.Point3(100,-50,25))
    obj.objectOffsetPos=rt.Point3(2,-3,4)
    row=list(rt.qz_vc_numbers_collect())[0]
    oracle=rt.execute('(fn qz_vc_numbers_world obj v = (in coordsys world (polyop.getVert obj v)))')
    check('sample positions match Max world oracle including pivot',all(abs(x-y)<1e-4 for x,y in zip(tuple(row[1]),tuple(oracle(obj,1)))))
    for level,expected in ((1,1),(2,2),(3,2),(4,3),(5,3)):
        rt.subObjectLevel=level
        if level in (2,3):rt.polyop.setEdgeSelection(obj,rt.Array(1))
        elif level in (4,5):rt.polyop.setFaceSelection(obj,rt.Array(1))
        check('subobject expansion level %s'%level,len(list(rt.qz_vc_numbers_collect()))==expected)
    rt.subObjectLevel=0
    check('object level draws no stale selection',len(list(rt.qz_vc_numbers_collect()))==0)
    rt.subObjectLevel=1;rt.polyop.setVertSelection(obj,rt.Array(1,2,3,4))
    rt.polyop.setMapSupport(obj,18,False)
    check('missing channel never initialized',len(list(rt.qz_vc_numbers_collect()))==0 and not rt.polyop.getMapSupport(obj,18))

    # Real viewport projection and capture, rather than a Qt mockup.
    obj.transform=rt.Matrix3(1);obj.objectOffsetPos=rt.Point3(0,0,0)
    rt.viewport.setLayout(rt.Name('layout_1'))
    rt.viewport.setType(rt.Name('view_top'))
    rt.execute('max zoomext sel all')
    action.vertex_color_numbers(enabled=True,channel=-2,mask=[True]*3,owner=owner)
    rt.completeRedraw();rt.qz_vc_numbers_draw()
    state=action.vertex_color_numbers_status(owner=owner)
    check('actual viewport draws selected labels: %r'%state,state['enabled'] and state['count']>0 and not state['error'])
    root=os.environ['QZ_VERTEX_COLOR_PROJECT_ROOT']
    image=rt.gw.getViewportDib()
    image.filename=os.path.join(root,'temp','vertex-color-numbers-native-alpha.png')
    rt.save(image);rt.close(image)
    action.vertex_color_numbers(enabled=True,channel=-2,mask=[True,False,True],owner=owner)
    check('alpha uses requested RGB display mask',list(rt.qz_vc_numbers_state.mask)==[True,False,True])
    rt.completeRedraw()
    image=rt.gw.getViewportDib()
    image.filename=os.path.join(root,'temp','vertex-color-numbers-native-mask.png')
    rt.save(image);rt.close(image)

    # Stale cards cannot switch off or renew another card's owned callback.
    last_ping=int(rt.qz_vc_numbers_state.lastPing)
    check('other owner cannot renew',not action.vertex_color_numbers_status(owner='b'*32)['enabled'] and int(rt.qz_vc_numbers_state.lastPing)==last_ping)
    action.vertex_color_numbers(enabled=False,mask=[True]*3,owner='b'*32)
    check('other owner cannot stop display',action.vertex_color_numbers_status(owner=owner)['enabled'])
    rt.qz_vc_numbers_state.lastPing=int(rt.timeStamp())-31000
    rt.qz_vc_numbers_draw()
    check('lost controller lease removes callback',not action.vertex_color_numbers_status(owner=owner)['enabled'])

    # Sampling is bounded even with a much larger current selection.
    points=[rt.Point3(x,y,0) for y in range(32) for x in range(16)]
    faces=[]
    for y in range(31):
        for x in range(15):
            a=y*16+x+1;b=a+1;c=a+17;d=a+16
            faces.extend((rt.Point3(a,b,c),rt.Point3(a,c,d)))
    grid=rt.mesh(name='TEMP_QZ_NUMBERS_LIMIT',vertices=rt.Array(*points),faces=rt.Array(*faces))
    rt.convertToPoly(grid);rt.select(grid);rt.modPanel.setCurrentObject(grid.baseObject);rt.subObjectLevel=1
    rt.polyop.setVertSelection(grid,rt.Array(*range(1,513)))
    rt.polyop.setMapSupport(grid,0,True);rt.polyop.setNumMapVerts(grid,0,1,keep=False)
    rt.polyop.setMapVert(grid,0,1,rt.Point3(.2,.4,.6))
    for fid in range(1,int(rt.polyop.getNumFaces(grid))+1):rt.polyop.setMapFace(grid,0,fid,rt.Array(1,1,1))
    action.vertex_color_numbers(enabled=True,channel=0,mask=[True]*3,owner=owner)
    started=time.time();rows=list(rt.qz_vc_numbers_collect());seconds=time.time()-started
    check('500 vertex cap and ascending geometry IDs',len(rows)==500 and int(rows[-1][0])==500 and rt.qz_vc_numbers_state.limited)
    with open(os.path.join(root,'temp','vertex-color-numbers-performance.json'),'w') as stream:
        import json
        json.dump(dict(selected=512,sampled=len(rows),seconds=seconds,native_ms=int(rt.qz_vc_numbers_state.sampleMs)),stream)
    rt.delete(grid)
    check('deleted selected node yields no stale labels',len(list(rt.qz_vc_numbers_collect()))==0)

    # Read-only display must not mark an existing scene dirty.
    rt.select(obj);rt.modPanel.setCurrentObject(obj.baseObject);rt.subObjectLevel=1
    rt.setSaveRequired(False)
    action.vertex_color_numbers(enabled=True,channel=0,mask=[True]*3,owner=owner)
    rt.qz_vc_numbers_collect();rt.qz_vc_numbers_draw()
    check('display does not mark scene dirty',not rt.getSaveRequired())
    action.vertex_color_numbers(enabled=False,mask=[True]*3,owner=owner)
    check('disabled callback',not action.vertex_color_numbers_status(owner=owner)['enabled'])
    rt.delete(obj)
    # A redraw failure disables only this display and remains inspectable.
    action.vertex_color_numbers(enabled=True,channel=0,mask=[True]*3,owner=owner)
    rt.execute('fn qz_vc_numbers_collect = (throw "viewport test exception")')
    rt.qz_vc_numbers_draw()
    state=action.vertex_color_numbers_status(owner=owner)
    check('callback exception stops display and records cause',not state['enabled'] and 'viewport test exception' in state['error'])
    # Use the production reload path: native callbacks outlive Python modules.
    from max_bridge.server import reload_actions
    reload_actions()
    check('action reload disables old native callback',not rt.qz_vc_numbers_state.enabled)
    from max_actions import vertex_color as reloaded
    reloaded.vertex_color_numbers(enabled=True,channel=0,mask=[True]*3,owner=owner)
    rt.resetMaxFile(rt.Name('noPrompt'))
    check('scene reset disables nonpersistent display',not rt.qz_vc_numbers_state.enabled)
    return checks
