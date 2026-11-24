"""Run only in a separate 3dsmaxbatch process with the output opt-in set."""
from __future__ import print_function, unicode_literals
import io
import json
import math
import os
import sys
import traceback

OUTPUT = os.environ['QZ_VERTEX_COLOR_NATIVE_OUTPUT']
ROOT = os.environ.get('QZ_VERTEX_COLOR_PROJECT_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run():
    import pymxs
    from pymxs import runtime as rt
    from max_actions.vertex_color import vertex_color, vertex_color_display, vertex_color_scale, vertex_color_gradient, vertex_color_gradient_point
    checks = []
    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)
    def color(node, index, channel=0):
        p = rt.polyop.getMapVert(node, channel, index)
        return (float(p.x), float(p.y), float(p.z))
    def rounded(value): return min(255,max(0,int(math.floor(value*255.0+.5))))/255.0
    def close(a, b): return all(abs(x-y) < 1e-6 for x, y in zip(a, b))
    obj = rt.mesh(name='qz_vertex_color_native',
                  vertices=rt.Array(rt.Point3(0,0,0), rt.Point3(10,0,0), rt.Point3(10,10,0), rt.Point3(0,10,0)),
                  faces=rt.Array(rt.Point3(1,2,3), rt.Point3(1,3,4)))
    rt.convertToPoly(obj)
    rt.select(obj)
    rt.setCommandPanelTaskMode(rt.Name('modify'))
    rt.modPanel.setCurrentObject(obj.baseObject)
    rt.subObjectLevel = 1
    rt.polyop.setVertSelection(obj, rt.execute('#{1}'))
    if rt.polyop.getMapSupport(obj, 0): rt.polyop.setMapSupport(obj, 0, False)
    result = vertex_color([128,64,32], [True,False,True])
    check('empty channel initialized: ' + repr(result), result.get('initialized') and result.get('vertices') == 1)
    check('selected RGB with G preserved', close(color(obj, 1), (128/255., 1., 32/255.)))
    check('outside selection white', close(color(obj, 2), (1.,1.,1.)))
    # Shared mapping deliberately exercises selected/unselected corner separation.
    rt.polyop.setNumMapVerts(obj, 0, 1, keep=False)
    rt.polyop.setMapVert(obj, 0, 1, rt.Point3(.123456789,.234567891,.345678912))
    for face in range(1,3): rt.polyop.setMapFace(obj, 0, face, rt.Array(1,1,1))
    original = color(obj,1)
    result = vertex_color([255,0,128], [True,False,True])
    check('shared colors split once', result.get('split') == 1 and int(rt.polyop.getNumMapVerts(obj,0)) == 2)
    check('unselected data unchanged', color(obj,1) == original)
    check('masked component exact', color(obj,2)[1] == original[1])
    check('selected corners remapped', [int(i) for i in rt.polyop.getMapFace(obj,0,1)] == [2,1,1])
    import max_actions.vertex_color as module
    original_write = module._write
    before_failure = color(obj,2)
    def fail_after_write(*args):
        original_write(*args)
        raise RuntimeError('native rollback test')
    module._write = fail_after_write
    caught = False
    try:
        vertex_color([0,0,0], [True]*3)
    except RuntimeError:
        caught = True
    finally:
        module._write = original_write
    check('failed write reported and explicitly restored', caught and color(obj,2) == before_failure and
          color(obj,1) == original and int(rt.polyop.getNumMapVerts(obj,0)) == 2)
    before = color(obj,2)
    result = vertex_color([0,0,0], [False,False,False])
    check('empty mask does not write', bool(result.get('warning')) and color(obj,2) == before)
    rt.subObjectLevel = 0
    check('object level rejected', bool(vertex_color([0,0,0], [True]*3).get('warning')))
    rt.subObjectLevel = 2
    rt.polyop.setEdgeSelection(obj, rt.execute('#{1}'))
    check('edge selection expanded', vertex_color([0,255,0], [True]*3).get('vertices') == 2)
    rt.subObjectLevel = 4
    rt.polyop.setFaceSelection(obj, rt.execute('#{2}'))
    check('face selection expanded', vertex_color([42,43,44], [True]*3).get('vertices') == 3)

    def snapshot(channel):
        if not rt.polyop.getMapSupport(obj, channel): return None
        return dict(colors=[color(obj,i,channel) for i in range(1,int(rt.polyop.getNumMapVerts(obj,channel))+1)],
                    faces=[[int(i) for i in rt.polyop.getMapFace(obj,channel,face)] for face in range(1,3)])
    def shared_channel(channel):
        rt.polyop.setNumMapVerts(obj, channel, 1, keep=False)
        rt.polyop.setMapVert(obj, channel, 1, rt.Point3(.2,.3,.4))
        for face in range(1,3): rt.polyop.setMapFace(obj,channel,face,rt.Array(1,1,1))
    rt.subObjectLevel = 1
    rt.polyop.setVertSelection(obj, rt.execute('#{1}'))
    # Keep an unrelated map channel as a sentinel alongside the existing RGB.
    rt.polyop.setMapSupport(obj, 17, True)
    rt.polyop.setNumMapVerts(obj,17,4,keep=False)
    for index in range(1,5): rt.polyop.setMapVert(obj,17,index,rt.Point3(.13,.27,.59))
    for face in range(1,3): rt.polyop.setMapFace(obj,17,face,rt.polyop.getFaceVerts(obj,face))
    stable_rgb, stable_map = snapshot(0), snapshot(17)
    for channel in (-1,-2):
        if rt.polyop.getMapSupport(obj,channel): rt.polyop.setMapSupport(obj,channel,False)
    result = vertex_color([32,64,128],[True,False,True],channel=-1)
    check('illumination initializes only its own channel', result['initialized'] and snapshot(-2) is None)
    check('illumination preserves unchecked component and outside selection',
          close(color(obj,1,-1),(32/255.,1.,128/255.)) and close(color(obj,2,-1),(1.,1.,1.)))
    check('illumination display selected', obj.vertexColorType == rt.Name('illum'))
    stable_illum = snapshot(-1)
    result = vertex_color(channel=-2,alpha=64)
    check('alpha scalar initializes', result['initialized'] and close(color(obj,1,-2),(64/255.,)*3))
    check('alpha leaves outside selection opaque', close(color(obj,2,-2),(1.,1.,1.)))
    check('alpha display selected', obj.vertexColorType == rt.Name('alpha'))
    check('alpha does not modify illumination or RGB or map17',
          snapshot(-1)==stable_illum and snapshot(0)==stable_rgb and snapshot(17)==stable_map)
    for channel in (-1,-2):
        shared_channel(channel)
        old = color(obj,1,channel)
        parameters = dict(channel=channel,alpha=128) if channel == -2 else dict(channel=channel,rgb=[255,0,0],mask=[True,False,True])
        result = vertex_color(**parameters)
        check('shared hidden selection split ' + str(channel),
              result['split']==1 and color(obj,1,channel)==old and
              [int(i) for i in rt.polyop.getMapFace(obj,channel,1)]==[2,1,1])
        expected = (128/255.,)*3 if channel == -2 else (1.,old[1],0.)
        check('hidden channel values ' + str(channel), close(color(obj,2,channel),expected))
        saved = {ch:snapshot(ch) for ch in (0,-1,-2,17)}
        display = (obj.showVertexColors,obj.vertexColorsShaded,obj.vertexColorType)
        module._write = fail_after_write
        caught = False
        try:
            vertex_color(**parameters)
        except RuntimeError:
            caught = True
        finally:
            module._write = original_write
        check('hidden failure explicitly restores ' + str(channel), caught and
              saved=={ch:snapshot(ch) for ch in (0,-1,-2,17)} and
              display==(obj.showVertexColors,obj.vertexColorsShaded,obj.vertexColorType))
    for channel in (-1,-2):
        rt.polyop.setMapSupport(obj,channel,False)
        module._write = fail_after_write
        caught = False
        try:
            vertex_color([0]*3,[True]*3,channel=channel,alpha=0)
        except RuntimeError:
            caught = True
        finally:
            module._write = original_write
        check('failed hidden initialization removed ' + str(channel), caught and snapshot(channel) is None)
    check('RGB and unrelated map remain unchanged throughout hidden edits', snapshot(0)==stable_rgb and snapshot(17)==stable_map)

    channels = (0,-1,-2,17,18)
    saved = {ch:snapshot(ch) for ch in channels}
    rt.subObjectLevel = 0
    rt.polyop.setVertSelection(obj,rt.execute('#{}'))
    for channel, kind in ((0,'color'),(-2,'alpha'),(-1,'illum'),(17,'map_channel'),(18,'map_channel')):
        result = vertex_color_display(channel)
        check('switch display ' + str(channel), result.get('display_only') and
              obj.vertexColorType == rt.Name(kind) and bool(obj.showVertexColors) and
              (channel not in (17,18) or int(obj.vertexColorMapChannel)==channel))
        check('display neither paints nor allocates ' + str(channel),
              saved=={ch:snapshot(ch) for ch in channels} and int(rt.subObjectLevel)==0)

    rt.subObjectLevel = 1
    rt.polyop.setVertSelection(obj,rt.execute('#{1}'))
    for channel in (17,18):
        others = {ch:snapshot(ch) for ch in channels if ch != channel}
        old_g = color(obj,1,channel)[1] if rt.polyop.getMapSupport(obj,channel) else 1.
        result = vertex_color([64,0,192],[True,False,True],channel=channel)
        check('paint M' + str(channel), result['channel_name']=='M'+str(channel) and
              close(color(obj,1,channel),(64/255.,old_g,192/255.)) and
              obj.vertexColorType==rt.Name('map_channel') and int(obj.vertexColorMapChannel)==channel)
        check('map paint keeps other channels ' + str(channel),
              others=={ch:snapshot(ch) for ch in channels if ch != channel})
        shared_channel(channel)
        old = color(obj,1,channel)
        result = vertex_color([255,0,128],[True,False,True],channel=channel)
        check('shared map selection protected ' + str(channel), result['split']==1 and
              color(obj,1,channel)==old and close(color(obj,2,channel),(1.,old[1],128/255.)))
        saved = {ch:snapshot(ch) for ch in channels}
        obj.vertexColorType = rt.Name('color')
        obj.vertexColorMapChannel = 37
        display = module._display_state(obj)
        module._write = fail_after_write
        caught = False
        try:
            vertex_color([0]*3,[True]*3,channel=channel)
        except RuntimeError:
            caught = True
        finally:
            module._write = original_write
        check('map failure restores data and previous display ' + str(channel), caught and
              saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))

    saved, display = {ch:snapshot(ch) for ch in channels}, module._display_state(obj)
    original_display = module._set_display
    def fail_after_display(*args):
        original_display(*args)
        raise RuntimeError('native display recovery test')
    module._set_display = fail_after_display
    caught = False
    try:
        vertex_color_display(18)
    except RuntimeError:
        caught = True
    finally:
        module._set_display = original_display
    check('display failure restores display without painting', caught and
          saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))

    # Scaling reads current floating-point values; shared map IDs multiply once.
    geometry = [tuple(rt.polyop.getVert(obj, i)) for i in range(1, 5)]
    for channel in channels:
        rt.polyop.setMapSupport(obj, channel, True)
        shared_channel(channel)
        others = {ch:snapshot(ch) for ch in channels if ch != channel}
        old = color(obj, 1, channel)
        result = vertex_color_scale(.5, [True,False,True], channel=channel)
        expected = (rounded(old[0]*.5),)*3 if channel == -2 else (rounded(old[0]*.5),old[1],rounded(old[2]*.5))
        check('scale selected shared map once ' + str(channel), result['split']==1 and result['count']==1 and
              color(obj,1,channel)==old and close(color(obj,2,channel),expected) and
              [int(i) for i in rt.polyop.getMapFace(obj,channel,1)]==[2,1,1])
        check('scale keeps other channels and selection ' + str(channel),
              others=={ch:snapshot(ch) for ch in channels if ch != channel} and
              [int(i) for i in module._ids(rt,rt.polyop.getVertSelection(obj))]==[1])
        vertex_color_scale(.5, [True,False,True], channel=channel)
        expected = (rounded(rounded(old[0]*.5)*.5),)*3 if channel == -2 else (rounded(rounded(old[0]*.5)*.5),old[1],rounded(rounded(old[2]*.5)*.5))
        check('scale repeats from latest float colors ' + str(channel), close(color(obj,2,channel),expected))
        # Two map vertices at the same geometric vertex must retain their seam.
        rt.polyop.setNumMapVerts(obj,channel,3,keep=True)
        rt.polyop.setMapVert(obj,channel,3,rt.Point3(.6,.7654321,.8))
        rt.polyop.setMapFace(obj,channel,2,rt.Array(3,1,1))
        before2, before3 = color(obj,2,channel), color(obj,3,channel)
        result = vertex_color_scale(.5,[True,False,True],channel=channel)
        expect2 = (rounded(before2[0]*.5),)*3 if channel == -2 else (rounded(before2[0]*.5),before2[1],rounded(before2[2]*.5))
        expect3 = (rounded(before3[0]*.5),)*3 if channel == -2 else (rounded(before3[0]*.5),before3[1],rounded(before3[2]*.5))
        check('scale preserves distinct seam colors ' + str(channel), result['count']==2 and
              close(color(obj,2,channel),expect2) and close(color(obj,3,channel),expect3) and
              color(obj,1,channel)==old)
        saved = {ch:snapshot(ch) for ch in channels}
        obj.vertexColorType = rt.Name('color')
        obj.vertexColorMapChannel = 37
        display = module._display_state(obj)
        module._write = fail_after_write
        caught = False
        try:
            vertex_color_scale(2,[True]*3,channel=channel)
        except RuntimeError:
            caught = True
        finally:
            module._write = original_write
        check('scale failure restores colors maps display ' + str(channel), caught and
              saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))
        rt.polyop.setMapSupport(obj,channel,False)
        result = vertex_color_scale(1,[True]*3,channel=channel)
        check('scale identity initializes integer white channel ' + str(channel),
              result.get('initialized') and close(color(obj,1,channel),(1.,)*3))
        rt.polyop.setMapSupport(obj,channel,False)
        result = vertex_color_scale(.5,[True,False,True],channel=channel)
        expected = (128/255.,)*3 if channel == -2 else (128/255.,1.,128/255.)
        check('scale missing channel starts white outside selection ' + str(channel), result['initialized'] and
              close(color(obj,1,channel),expected) and close(color(obj,2,channel),(1.,)*3))
        rt.polyop.setMapSupport(obj,channel,False)
        module._write = fail_after_write
        caught = False
        try:
            vertex_color_scale(.5,[True]*3,channel=channel)
        except RuntimeError:
            caught = True
        finally:
            module._write = original_write
        check('scale failed initialization removed ' + str(channel), caught and snapshot(channel) is None)
    vertex_color([128,64,192],[True]*3)
    vertex_color_scale(10,[True,False,True])
    check('scale clamps enabled values only', close(color(obj,1),(1.,64/255.,1.)))
    vertex_color_scale(0,[True,False,True])
    check('scale zero preserves unchecked component', close(color(obj,1),(0.,64/255.,0.)))
    for level, count in ((2,2),(3,2),(4,3),(5,3)):
        rt.subObjectLevel = level
        if level in (2,3): rt.polyop.setEdgeSelection(obj,rt.execute('#{1}'))
        else: rt.polyop.setFaceSelection(obj,rt.execute('#{2}'))
        check('scale subobject selection ' + str(level), vertex_color_scale(.5,[True]*3)['vertices']==count)
    rt.subObjectLevel = 0
    saved = {ch:snapshot(ch) for ch in channels}
    check('scale rejects object level without changes', bool(vertex_color_scale(.5,[True]*3).get('warning')) and
          saved=={ch:snapshot(ch) for ch in channels})
    check('scale preserves geometry', geometry==[tuple(rt.polyop.getVert(obj,i)) for i in range(1,5)])

    # Disposable geometry with a midpoint and nonzero Z extent.
    rt.polyop.setVert(obj,2,rt.Point3(5,5,5))
    rt.subObjectLevel = 1
    gradient_geometry = [tuple(rt.polyop.getVert(obj.baseObject,i)) for i in range(1,5)]
    def corner(face,index,channel=0):
        mapping = [int(i) for i in rt.polyop.getMapFace(obj,channel,face)]
        return color(obj,mapping[index],channel)
    def gradient(channel=0,**params):
        defaults = dict(channel=channel,direction='X',minimum=32,maximum=224,curve=50,mask=[True,False,True])
        defaults.update(params)
        return vertex_color_gradient(**defaults)
    for channel in channels:
        rt.polyop.setVertSelection(obj,rt.execute('#{1..4}'))
        rt.polyop.setMapSupport(obj,channel,True)
        shared_channel(channel)
        others = {ch:snapshot(ch) for ch in channels if ch != channel}
        old_g = color(obj,1,channel)[1]
        result = gradient(channel)
        start,middle,end = ((32/255.,)*3,(128/255.,)*3,(224/255.,)*3) if channel == -2 else (
            (32/255.,old_g,32/255.),(128/255.,old_g,128/255.),(224/255.,old_g,224/255.))
        check('world gradient separates selected positions '+str(channel),result['split']==2 and result['count']==3 and
              close(corner(1,0,channel),start) and close(corner(1,1,channel),middle) and close(corner(1,2,channel),end))
        check('world gradient preserves other channels '+str(channel),
              others=={ch:snapshot(ch) for ch in channels if ch != channel})
        first = snapshot(channel)
        result = gradient(channel)
        check('world gradient repeats without map growth '+str(channel),result['split']==0 and snapshot(channel)==first)
        gradient(channel,direction='-X')
        check('negative world axis reverses progression '+str(channel),close(corner(1,0,channel),end) and
              close(corner(1,1,channel),middle) and close(corner(1,2,channel),start))
        for curve,scalar in ((25,80/255.),(75,176/255.)):
            gradient(channel,curve=curve)
            expected = (scalar,)*3 if channel == -2 else (scalar,old_g,scalar)
            check('source curve midpoint '+str(channel)+' '+str(curve),close(corner(1,1,channel),expected))
        outside = (corner(1,2,channel),corner(2,2,channel))
        rt.polyop.setVertSelection(obj,rt.execute('#{1,2}'))
        gradient(channel)
        check('world selected bounds and outside corners '+str(channel),close(corner(1,0,channel),start) and
              close(corner(1,1,channel),end) and outside==(corner(1,2,channel),corner(2,2,channel)))
        seam = int(rt.polyop.getNumMapVerts(obj,channel))+1
        rt.polyop.setNumMapVerts(obj,channel,seam,keep=True)
        rt.polyop.setMapVert(obj,channel,seam,rt.Point3(.6,.7654321,.8))
        seam_g = color(obj,seam,channel)[1]
        mapping = [int(i) for i in rt.polyop.getMapFace(obj,channel,2)]
        mapping[0] = seam
        rt.polyop.setMapFace(obj,channel,2,rt.Array(*mapping))
        gradient(channel)
        check('world gradient keeps seams and masked float '+str(channel),
              int(rt.polyop.getMapFace(obj,channel,1)[0]) != int(rt.polyop.getMapFace(obj,channel,2)[0]) and
              (close(corner(2,0,channel),start) if channel == -2 else corner(2,0,channel)[1]==seam_g))
        shared_channel(channel)
        rt.polyop.setVertSelection(obj,rt.execute('#{1,2,3}'))
        saved = {ch:snapshot(ch) for ch in channels}
        obj.vertexColorType = rt.Name('color')
        obj.vertexColorMapChannel = 37
        display = module._display_state(obj)
        module._write = fail_after_write
        caught = False
        try: gradient(channel,direction='custom',start=[0,0,0],end=[10,10,0],curve=75)
        except RuntimeError: caught = True
        finally: module._write = original_write
        check('custom curved gradient restores splits data and display '+str(channel),caught and
              saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))
        rt.polyop.setMapSupport(obj,channel,False)
        rt.polyop.setVertSelection(obj,rt.execute('#{1}'))
        check('world flat selection does not allocate '+str(channel),bool(gradient(channel).get('warning')) and snapshot(channel) is None)
        rt.polyop.setVertSelection(obj,rt.execute('#{1,2,3}'))
        result = gradient(channel)
        expected = (128/255.,)*3 if channel == -2 else (128/255.,1.,128/255.)
        check('world missing channel stays white outside '+str(channel),result['initialized'] and
              close(corner(1,1,channel),expected) and close(corner(2,2,channel),(1.,)*3))
        if channel == -2:
            gradient(channel,mask=[False]*3)
            check('alpha gradient ignores RGB mask',close(corner(1,1,channel),(128/255.,)*3))
        rt.polyop.setMapSupport(obj,channel,False)
        module._write = fail_after_write
        caught = False
        try: gradient(channel)
        except RuntimeError: caught = True
        finally: module._write = original_write
        check('world failed initialization removed '+str(channel),caught and snapshot(channel) is None)
    rt.polyop.setVertSelection(obj,rt.execute('#{1..4}'))
    vertex_color_gradient(mask=[True]*3)
    check('default minus Z is top to bottom',close(corner(1,1),(0.,)*3) and close(corner(1,0),(1.,)*3))
    obj.transform = rt.Matrix3(rt.Point3(0,2,0),rt.Point3(-3,0,0),rt.Point3(0,0,4),rt.Point3(100,-50,25))
    transform = str(obj.transform)
    for direction,expected in (('X',(1.,.5,0.,0.)),('-X',(0.,.5,1.,1.)),
                               ('Y',(0.,.5,1.,0.)),('-Y',(1.,.5,0.,1.)),
                               ('Z',(0.,1.,0.,0.)),('-Z',(1.,0.,1.,1.))):
        gradient(direction=direction,minimum=0,maximum=255,mask=[True]*3)
        actual = (corner(1,0),corner(1,1),corner(1,2),corner(2,2))
        # Max's transformed vertices can differ from ideal integer positions
        # by a few float32 units. Quantize the actual projection: an ideal .5
        # can be just below .5 and correctly round to 127 rather than 128.
        axis='XYZ'.index(direction[-1]);sign=-1 if direction.startswith('-') else 1
        distances=[sign*module._world_position(rt,obj,v)[axis] for v in range(1,5)]
        low,high=min(distances),max(distances)
        projected=[(distance-low)/(high-low) for distance in distances]
        check('world axis ignores rotated local axis '+direction,
              all(abs(t-ideal)<1e-6 for t,ideal in zip(projected,expected)))
        check('world axis rounds actual Max projections '+direction+' actual='+repr(actual),
              all(close(value,(rounded(t),)*3) for value,t in zip(actual,projected)))
    rt.polyop.setVertSelection(obj,rt.execute('#{1,2,3}'))
    saved,display = {ch:snapshot(ch) for ch in channels},module._display_state(obj)
    capture = vertex_color_gradient_point()
    check('captured center is transformed world mean',capture['vertices']==3 and close(capture['point'],(85.,-40.,95/3.)))
    check('capturing center does not write colors or display',saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))
    rt.polyop.setVertSelection(obj,rt.execute('#{1,4}'))
    point1 = vertex_color_gradient_point()['point']
    rt.polyop.setVertSelection(obj,rt.execute('#{2,3}'))
    point2 = vertex_color_gradient_point()['point']
    check('two selected groups capture independent world centroids',close(point1,(85.,-50.,25.)) and close(point2,(77.5,-35.,35.)))
    rt.polyop.setVertSelection(obj,rt.execute('#{1,2,3}'))
    for curve,mid in ((25,.25),(50,.5),(75,.75)):
        gradient(direction='custom',start=[94,-46,25],end=[76,-34,25],minimum=0,maximum=255,curve=curve,mask=[True]*3)
        check('custom world projection curve and clamped endpoints '+str(curve),close(corner(1,0),(0.,)*3) and
              close(corner(1,1),(rounded(mid),)*3) and close(corner(1,2),(1.,)*3))
    rt.polyop.setVertSelection(obj,rt.execute('#{2}'))
    gradient(direction='custom',start=[94,-46,25],end=[76,-34,25],minimum=0,maximum=255,mask=[True]*3)
    check('custom fixed anchors work on single selected vertex',close(corner(1,1),(128/255.,)*3))
    saved,display = {ch:snapshot(ch) for ch in channels},module._display_state(obj)
    for params in (dict(direction='custom'),dict(direction='custom',start=[1,2,3],end=[1,2,3])):
        check('unset or coincident custom points do not write '+str(len(params)),bool(gradient(**params).get('warning')) and
              saved=={ch:snapshot(ch) for ch in channels} and display==module._display_state(obj))
    obj.objectOffsetPos = rt.Point3(2,-3,4)
    rt.polyop.setVertSelection(obj,rt.execute('#{1}'))
    capture = vertex_color_gradient_point()['point']
    oracle = rt.execute('(in coordsys world (polyop.getVert $ 1))')
    check('world capture includes object pivot offsets',close(capture,(float(oracle.x),float(oracle.y),float(oracle.z))))
    for level,count in ((2,2),(3,2),(4,3),(5,3)):
        rt.subObjectLevel = level
        if level in (2,3): rt.polyop.setEdgeSelection(obj,rt.execute('#{1}'))
        else: rt.polyop.setFaceSelection(obj,rt.execute('#{2}'))
        check('world gradient subobject level '+str(level),gradient()['vertices']==count)
        check('world centroid subobject level '+str(level),vertex_color_gradient_point()['vertices']==count)
    check('world gradient preserves geometry and transform',gradient_geometry==[tuple(rt.polyop.getVert(obj.baseObject,i)) for i in range(1,5)] and transform==str(obj.transform))
    rt.subObjectLevel = 0
    check('world gradient and centroid reject object level',bool(gradient().get('warning')) and bool(vertex_color_gradient_point().get('warning')))

    # Explicit integer oracle catches half-value errors caused by Point3 float32
    # and Python's different built-in round semantics. Also verify zero-tolerance
    # selection can find every rounded write using the corresponding integer.
    rt.subObjectLevel=1
    rt.polyop.setVertSelection(obj,rt.Array(1))
    for channel in channels:
        for value in range(256):
            vertex_color([value]*3,[True]*3,channel=channel,alpha=value)
            vertex_color_scale(.5,[True]*3,channel=channel)
            expected=((value+1)//2)/255.0
            check('byte half rounds up channel %s value %s'%(channel,value),close(corner(1,0,channel),(expected,)*3))
            matched=module.vertex_color_select(rgb=[(value+1)//2]*3,mask=[True]*3,channel=channel,alpha=(value+1)//2)
            check('rounded color exact selection channel %s value %s'%(channel,value),matched.get('count')==1)
        # External fractional input, and scale factor 1 as explicit rounding.
        ids=[int(v) for v in rt.polyop.getMapFace(obj,channel,1)]
        rt.polyop.setMapVert(obj,channel,ids[0],rt.Point3(12.49/255.,12.5/255.,12.51/255.))
        vertex_color_scale(1,[True]*3,channel=channel)
        expected=(12/255.,)*3 if channel==-2 else (12/255.,13/255.,13/255.)
        check('identity rounds external fractional values channel %s'%channel,close(corner(1,0,channel),expected))

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from max_vertex_color_topology_integration import run as run_topology
    checks.extend(run_topology())
    from max_vertex_color_select_integration import run as run_select
    checks.extend(run_select())
    from max_vertex_color_transfer_integration import run as run_transfer
    checks.extend(run_transfer())
    from max_vertex_color_viewport_integration import run as run_viewport
    checks.extend(run_viewport())
    return dict(ok=True, python=list(sys.version_info[:3]), checks=checks)


try:
    report = run()
except Exception:
    report = dict(ok=False, error=traceback.format_exc())
with io.open(OUTPUT, 'w', encoding='utf-8') as stream:
    payload = json.dumps(report, ensure_ascii=True, indent=2)
    if not isinstance(payload, type(u'')): payload = payload.decode('ascii')
    stream.write(payload)
print('VERTEX_COLOR_NATIVE_RESULT: ' + str(report['ok']))
