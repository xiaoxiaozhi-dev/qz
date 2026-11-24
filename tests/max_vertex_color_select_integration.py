"""Selection-only native checks; called only by the isolated Max harness."""
from __future__ import print_function
from pymxs import runtime as rt
from max_actions import vertex_color as action


def run():
    checks=[]
    channels=(0,-1,-2,17,18)
    def check(name,condition):
        if not condition: raise AssertionError(name)
        checks.append('select: '+name)
    obj=rt.mesh(vertices=rt.Array(),faces=rt.Array())
    rt.convertToPoly(obj)
    obj.name='TEMP_QZ_SELECT_BY_COLOR'
    for p in ((0,0,0),(1,0,0),(1,1,0),(0,1,0),(-1,1,0),(-1,0,0)):
        rt.polyop.createVert(obj,rt.Point3(*p))
    rt.polyop.createPolygon(obj,rt.Array(1,2,3,4))
    rt.polyop.createPolygon(obj,rt.Array(1,4,5,6))
    rt.select(obj)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(obj.baseObject)
    def selection(values=tuple(range(1,7)),level=1):
        rt.polyop.setVertSelection(obj,rt.Array(*values))
        rt.subObjectLevel=level
    def picked(): return action._ids(rt,rt.polyop.getVertSelection(obj))
    def snapshot():
        result={}
        for ch in channels:
            if not rt.polyop.getMapSupport(obj,ch): result[ch]=None; continue
            result[ch]=dict(colors=[tuple(rt.polyop.getMapVert(obj,ch,i)) for i in range(1,int(rt.polyop.getNumMapVerts(obj,ch))+1)],
                            faces=[list(rt.polyop.getMapFace(obj,ch,i)) for i in (1,2)])
        return result,action._display_state(obj)
    def choose(ch=0,**params):
        options=dict(channel=ch)
        if ch==-2: options['alpha']=200
        else: options.update(rgb=[200,50,100],mask=[True,False,False])
        options.update(params)
        return action.vertex_color_select(**options)
    def colors(ch):
        values=(190,200,210,199.5,210.5,189.5,80)
        for i,value in enumerate(values,1):
            rt.polyop.setMapVert(obj,ch,i,rt.Point3(value/255.,50/255.,100/255.))
    for ch in channels:
        rt.polyop.setMapSupport(obj,ch,True)
        rt.polyop.setNumMapVerts(obj,ch,7,keep=False)
        rt.polyop.setMapFace(obj,ch,1,rt.Array(1,2,3,4))
        rt.polyop.setMapFace(obj,ch,2,rt.Array(7,4,5,6))
        colors(ch)
    geometry=([tuple(rt.polyop.getVert(obj,i)) for i in range(1,7)],
              [list(rt.polyop.getFaceVerts(obj,i)) for i in (1,2)])
    for ch in channels:
        for tolerance,expected in ((0,[2]),(1,[2,4]),(10,[1,2,3,4])):
            selection()
            before=snapshot()
            result=choose(ch,tolerance=tolerance)
            check('channel %s tolerance %s exact inclusive fractional matches'%(ch,tolerance),picked()==expected and result['count']==len(expected))
            check('channel %s tolerance %s maps and display unchanged'%(ch,tolerance),snapshot()==before)
        selection([1])
        rt.polyop.setMapVert(obj,ch,7,rt.Point3(200/255.,150/255.,0))
        before=snapshot()
        choose(ch)
        check('channel %s any seam matches and stays in geometry scope'%ch,picked()==[1] and snapshot()==before)
        colors(ch)
    for ch in (0,-1,17,18):
        rt.polyop.setMapVert(obj,ch,1,rt.Point3(200/255.,20/255.,0))
        rt.polyop.setMapVert(obj,ch,7,rt.Point3(10/255.,150/255.,0))
        selection([1])
        choose(ch,rgb=[200,150,0],mask=[True,True,False])
        check('channel %s checked components cannot match different seam corners'%ch,picked()==[])
        colors(ch)
    for value in range(256):
        rt.polyop.setMapVert(obj,0,2,rt.Point3(value/255.,0,0))
        selection([2])
        choose(rgb=[value,0,0])
        check('zero tolerance stored integer '+str(value),picked()==[2])
    rt.polyop.setMapVert(obj,0,2,rt.Point3(199.6/255.,0,0))
    selection([2])
    choose()
    check('zero tolerance does not round 199.6 to 200',picked()==[])
    colors(0)
    for target,tolerance,values,expected in (
            (5,10,(-.1,0,5,15,15.01,40,80),[2,3,4]),
            (250,10,(239.99,240,250,255,255.01,50,80),[2,3,4])):
        for i,value in enumerate(values,1): rt.polyop.setMapVert(obj,0,i,rt.Point3(value/255.,0,0))
        selection()
        choose(rgb=[target,0,0],tolerance=tolerance)
        check('clipped tolerance bounds target '+str(target),picked()==expected)
    colors(0)
    edge=next(i for i in range(1,int(rt.polyop.getNumEdges(obj))+1)
              if set(int(v) for v in rt.polyop.getEdgeVerts(obj,i))==set([2,3]))
    for level in (1,2,3,4,5):
        selection([2,5],level)
        rt.polyop.setEdgeSelection(obj,rt.Array(edge))
        rt.polyop.setFaceSelection(obj,rt.Array(1))
        expected=sorted(action._selection(rt,obj,level))
        edges=action._ids(rt,rt.polyop.getEdgeSelection(obj))
        faces=action._ids(rt,rt.polyop.getFaceSelection(obj))
        before=snapshot()
        result=choose(tolerance=255)
        check('subobject level %s filters its geometry selection'%level,picked()==expected and result['searched']==len(expected) and int(rt.subObjectLevel)==1)
        check('subobject level %s preserves other selections maps display'%level,
              edges==action._ids(rt,rt.polyop.getEdgeSelection(obj)) and faces==action._ids(rt,rt.polyop.getFaceSelection(obj)) and snapshot()==before)
    selection(level=4)
    rt.polyop.setMapSupport(obj,17,False)
    before=snapshot()
    previous=picked()
    result=choose(17)
    check('missing channel is not created and preserves selection and level',bool(result.get('warning')) and snapshot()==before and picked()==previous and int(rt.subObjectLevel)==4)
    selection(level=0)
    check('object level does not change selection',bool(choose().get('warning')) and picked()==previous and int(rt.subObjectLevel)==0)
    selection([])
    check('empty geometry selection stays empty',bool(choose().get('warning')) and picked()==[])
    check('native geometry unchanged',geometry==([tuple(rt.polyop.getVert(obj,i)) for i in range(1,7)],
                                                [list(rt.polyop.getFaceVerts(obj,i)) for i in (1,2)]))
    rt.delete(obj)
    return checks
