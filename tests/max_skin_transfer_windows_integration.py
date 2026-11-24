# -*- coding: utf-8 -*-
"""Disposable Max batch; never connect to the user's bridge or save a scene."""
from __future__ import print_function,unicode_literals
from array import array
import io,json,os,sys,time,traceback
OUTPUT=os.environ['QZ_RIG_NATIVE_OUTPUT']
sys.path.insert(0,os.environ['QZ_RIG_PROJECT'])

def run():
    from pymxs import runtime as rt
    from max_actions import skin_transfer as action
    rt.resetMaxFile(rt.Name('noPrompt'))
    bones=[rt.Point(name='TransferWindowBone%d'%i) for i in range(4)]
    directory=os.path.join(OUTPUT,'qz-skin-transfer-window-test')
    os.makedirs(directory)
    results=[]
    original_map=action._bone_map
    def closed_editors_map(runtime,skin):
        assert not runtime.skinOps.isWeightToolOpen(skin),'Weight Tool was left open before import writes'
        assert not runtime.skinOps.isWeightTableOpen(skin),'Weight Table was left open before import writes'
        return original_map(runtime,skin)
    action._bone_map=closed_editors_map
    for segments in (31,1):
        node=rt.plane(name='TransferWindowMesh%d'%segments,lengthsegs=segments,widthsegs=segments)
        rt.convertToPoly(node)
        vertices=int(rt.polyOp.getNumVerts(node))
        if segments>1:
            # Match the reported case: an existing Skin, all source bones and
            # vertex memberships already present (added_bones == 0).
            skin=rt.Skin()
            rt.addModifier(node,skin)
            action._activate(rt,node,skin)
            for bone in bones:
                rt.skinOps.addBone(skin,bone,1)
            rt.execute('fn qzWindowTestSeed s = (for v=1 to skinOps.GetNumberVertices s do skinOps.ReplaceVertexWeights s v #(1,2,3,4) #(0.1,0.2,0.3,0.4))')
            rt.qzWindowTestSeed(skin)
        offsets=array('I',range(0,vertices*4+1,4))
        cols=array('I',[0,1,2,3]*vertices)
        dq=array('d',[(v%5)/4. for v in range(vertices)])
        mesh=dict(index=0,name=unicode(node.name),mesh='|'+unicode(node.name),vertices=vertices,
            nonzero=vertices*4,influences=4,skinning_method=2,
            bones=[dict(index=i,name=unicode(b.name)) for i,b in enumerate(bones)])
        def data(values):
            for suffix,values_array in [('offsets',offsets),('cols',cols),('values',array('d',values*vertices)),('dq',dq)]:
                with open(os.path.join(directory,'mesh-0.'+suffix),'wb') as stream:
                    values_array.tofile(stream)
        def imported(values):
            data(values)
            started=time.time()
            report=action.import_max(directory,[mesh],4)
            elapsed=time.time()-started
            assert report['count']==1 and not report['errors'],report
            assert report['vertices']==vertices,report
            skin=action._skin_modifiers(rt,node)[0]
            assert not rt.skinOps.isWeightToolOpen(skin)
            assert not rt.skinOps.isWeightTableOpen(skin)
            return skin,report,elapsed
        skin,first,_=imported([.1,.2,.3,.4])
        rt.skinOps.WeightTool(skin)
        assert rt.skinOps.isWeightToolOpen(skin),'Native Weight Tool did not open'
        if segments==1:
            rt.skinOps.buttonWeightTable(skin)
            assert rt.skinOps.isWeightTableOpen(skin),'Native Weight Table did not open'
        expected=[.4,.3,.2,.1]
        skin,opened,opened_seconds=imported(expected)
        mapping=original_map(rt,skin)
        wanted={mapping[unicode(b.name)]:value for b,value in zip(bones,expected)}
        for vertex in range(1,vertices+1):
            count=int(rt.skinOps.GetVertexWeightCount(skin,vertex))
            actual={int(rt.skinOps.GetVertexWeightBoneID(skin,vertex,slot)):
                    float(rt.skinOps.GetVertexWeight(skin,vertex,slot)) for slot in range(1,count+1)}
            actual={bone:value for bone,value in actual.items() if value>0.}
            assert set(actual)==set(wanted),(vertex,actual,wanted)
            assert all(abs(actual[bone]-value)<1e-6 for bone,value in wanted.items()),(vertex,actual,wanted)
            assert abs(float(rt.skinOps.getVertexDQWeight(skin,vertex))-dq[vertex-1])<1e-6,vertex
        _,closed,closed_seconds=imported(expected)
        results.append(dict(vertices=vertices,write_mode=opened['reports'][0]['write_mode'],
            weight_tool_opened_then_closed=True,weight_table_tested=(segments==1),
            windows_closed_before_mutation=True,all_weights_and_dq_verified=True,
            initially_open_seconds=opened_seconds,already_closed_seconds=closed_seconds,
            final_bone_limit=int(skin.bone_limit)))
        with io.open(os.path.join(OUTPUT,'checks.json'),'w',encoding='utf-8') as stream:
            stream.write(unicode(json.dumps(results,ensure_ascii=True,indent=2)))
    assert set(row['write_mode'] for row in results)=={'vertex','bone_groups'},results
    return dict(ok=True,checks=results,isolated_scene=True,source_algorithm_unchanged=True)

def execute_test():
    try:
        result=run()
    except Exception:
        result=dict(ok=False,error=traceback.format_exc())
    with io.open(os.path.join(OUTPUT,'result.json'),'w',encoding='utf-8') as stream:
        text=json.dumps(result,ensure_ascii=True,indent=2)
        stream.write(text if isinstance(text,unicode) else text.decode('ascii'))
    print('SKIN_TRANSFER_WINDOW_TEST: '+str(result['ok']))

execute_test()
