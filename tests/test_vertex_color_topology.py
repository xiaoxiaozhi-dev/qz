# -*- coding: utf-8 -*-
"""Topology recognition and actual action integration, Python 2.7 and 3."""
from __future__ import unicode_literals
import copy
import json
import os
import random
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from max_actions import vertex_color as action
from max_actions._vertex_color_topology import check_chain, complete_row, complete_row_from_reference, face_edges, row_weights
from test_vertex_color_host import Runtime, Model, rounded


def strip(length=7, width=3, triangles=False, mixed=False, seed=1):
    rows = [[i*(width+1)+j+1 for j in range(width+1)] for i in range(length)]
    faces, rng = [], random.Random(seed)
    for i in range(length-1):
        for j in range(width):
            a,b,c,d = rows[i][j],rows[i][j+1],rows[i+1][j+1],rows[i+1][j]
            if not triangles or (mixed and rng.randrange(2)):
                faces.append((a,b,c,d))
            elif rng.randrange(2):
                faces.extend(((a,b,c),(a,c,d)))
            else:
                faces.extend(((a,b,d),(b,c,d)))
    return faces, rows


def row_edges(row):
    return [tuple(sorted((a,b))) for a,b in zip(row,row[1:])]


def ring_edges(row):
    return row_edges(row+[row[0]])


def tube(length=7, width=6, triangles=False, mixed=False, seed=1):
    faces,old_rows=strip(length,width,triangles,mixed,seed)
    rows=[[i*width+j+1 for j in range(width)] for i in range(length)]
    mapping={v:rows[i][j%width] for i,row in enumerate(old_rows) for j,v in enumerate(row)}
    return [tuple(mapping[v] for v in f) for f in faces],rows


def mixed_panel(triangles=False, mixed=False, seed=1):
    faces,rows=tube(length=7,width=8)
    rows[4]=rows[4][:6]
    rows[5]=rows[5][1:5]
    rows[6]=rows[6][2:4]
    used=set(v for row in rows for v in row)
    faces=[f for f in faces if all(v in used for v in f)]
    if triangles:
        result=[]
        rng=random.Random(seed)
        for a,b,c,d in faces:
            if mixed and rng.randrange(2): result.append((a,b,c,d))
            elif rng.randrange(2): result.extend(((a,b,c),(a,c,d)))
            else: result.extend(((a,b,d),(b,c,d)))
        faces=result
    return faces,rows


def synthetic_mixed_panel():
    faces, rows = mixed_panel(triangles=True, mixed=True, seed=1)
    return dict(faces=faces, starts=[ring_edges(rows[1]), ring_edges(rows[2])],
                end=row_edges(rows[-1]), rows=[rows[1:], rows[2:]])


class TopologyRuntime(Runtime):
    def __init__(self, triangles=True, mixed=False, closed=False):
        Runtime.__init__(self)
        self.closed=closed
        faces,self.rows = (tube if closed else strip)(triangles=triangles,mixed=mixed)
        self.faces = {i+1:list(f) for i,f in enumerate(faces)}
        self.positions = {v:(float(i*i),float(j),float(i)) for i,row in enumerate(self.rows) for j,v in enumerate(row)}
        self.obj.mapping = {i:[1]*len(f) for i,f in self.faces.items()}
        self.edge_selected=[]
        self.valid_nodes=[self.obj]
    def edge_table(self):
        return {i+1:e for i,e in enumerate(sorted(set(e for f in self.faces.values() for e in face_edges(f))))}
    def getNumVerts(self,obj): return len(self.positions)
    def getNumFaces(self,obj): return max(self.faces)
    def getNumEdges(self,obj): return len(self.edge_table())
    def getEdgeVerts(self,obj,index): return self.edge_table()[index]
    def getEdgeSelection(self,obj): return self.edge_selected
    def setEdgeSelection(self,obj,ids): self.edge_selected=list(ids)
    def isValidNode(self,obj): return any(obj is node for node in self.valid_nodes)
    def getVert(self,*args): raise AssertionError('Topology must not read positions')
    def select_row(self,index):
        desired=set((ring_edges if self.closed else row_edges)(self.rows[index]))
        self.subObjectLevel=2
        self.edge_selected=[i for i,e in self.edge_table().items() if e in desired]


class TopologyTests(unittest.TestCase):
    def test_single_edge_completes_quad_rows_at_every_position(self):
        for closed in (False,True):
            faces,rows=(tube if closed else strip)()
            for row in rows:
                expected=(ring_edges if closed else row_edges)(row)
                for edge in expected:
                    self.assertEqual(set(complete_row(faces,[edge])),set(expected))
            self.assertEqual(complete_row(faces,expected),expected)

    def test_single_edge_does_not_guess_triangle_poles_or_invalid_seeds(self):
        faces,rows=strip(triangles=True)
        with self.assertRaises(ValueError): complete_row(faces,[row_edges(rows[3])[1]])
        with self.assertRaises(ValueError): complete_row(faces,[(1000,1001)])
        faces,rows=strip()
        edge=row_edges(rows[3])[1]
        # An attached third incidence or branch must not pick an arbitrary route.
        with self.assertRaises(ValueError): complete_row(faces+[(edge[0],edge[1],900)], [edge])

    def test_mixed_panel_types_reverse_and_random_triangles(self):
        for triangles,mixed in ((False,False),(True,False),(True,True)):
            for seed in range(20):
                faces,rows=mixed_panel(triangles,mixed,seed)
                start,end=ring_edges(rows[1]),row_edges(rows[-1])
                for first,last,reverse in ((start,end,False),(end,start,True)):
                    result=row_weights(faces,first,last)
                    expected=rows[1:] if not reverse else list(reversed(rows[1:]))
                    self.assertEqual(result['rows'],len(expected))
                    self.assertEqual(result['values'],{v:i/float(len(expected)-1) for i,row in enumerate(expected) for v in row})

    def test_synthetic_panel_mixed_rows_and_partial_endpoint_rejection(self):
        fixture=synthetic_mixed_panel()
        faces=fixture['faces']
        for first,rows in zip(fixture['starts'],fixture['rows']):
            last=fixture['end']
            for reverse in (False,True):
                result=row_weights(faces,last if reverse else first,first if reverse else last)
                expected=list(reversed(rows)) if reverse else rows
                self.assertEqual(result['values'],{v:i/float(len(rows)-1) for i,row in enumerate(expected) for v in row})
            for a,b in ((first[:-1],last),(first,last[:-1])):
                with self.assertRaises(ValueError): row_weights(faces,a,b)

    def test_reference_resolves_every_mixed_panel_open_end_seed_and_triangle_row(self):
        fixture=synthetic_mixed_panel()
        for reference in fixture['starts']:
            for seed in fixture['end']:
                self.assertEqual(set(complete_row_from_reference(fixture['faces'],seed,reference)),set(fixture['end']))
        faces,rows=strip(triangles=True)
        for index in (0,3,6):
            reference=row_edges(rows[6 if index==0 else 0])
            expected=row_edges(rows[index])
            for seed in expected:
                self.assertEqual(set(complete_row_from_reference(faces,seed,reference)),set(expected))
        with self.assertRaises(ValueError):
            complete_row_from_reference(faces,reference[0],reference)

    def test_mixed_panel_rejects_holes_and_row_branches(self):
        faces,rows=mixed_panel()
        start,end=ring_edges(rows[1]),row_edges(rows[-1])
        missing=list(faces); missing.pop(20)
        for changed in (missing,faces+[(rows[2][0],rows[2][1],900)]):
            with self.assertRaises(ValueError): row_weights(changed,start,end)

    def test_closed_rings_are_connected_and_unbranched(self):
        self.assertTrue(check_chain(ring_edges([1,2,3,4])))
        self.assertFalse(check_chain(row_edges([1,2,3,4])))
        for edges in (ring_edges([1,2,3])+ring_edges([4,5,6]),
                      ring_edges([1,2,3])+[(1,4)],
                      ring_edges([1,2,3])+ring_edges([1,4,5])):
            with self.assertRaises(ValueError): check_chain(edges)

    def test_random_tube_triangle_quad_mixed_rows_and_numbering(self):
        rng=random.Random(912)
        for iteration in range(120):
            length,width=rng.randint(3,15),rng.randint(3,10)
            faces,rows=tube(length,width,iteration%3!=0,iteration%3==2,iteration)
            labels=list(range(100,100+length*width))
            rng.shuffle(labels)
            mapping={v:labels[v-1] for row in rows for v in row}
            rows=[[mapping[v] for v in row] for row in rows]
            faces=[tuple(mapping[v] for v in (f if rng.randrange(2) else tuple(reversed(f)))) for f in faces]
            rng.shuffle(faces)
            a,b=rng.sample(list(range(length)),2)
            first,last=ring_edges(rows[a]),ring_edges(rows[b])
            rng.shuffle(first); rng.shuffle(last)
            result=row_weights(faces,first,last)
            expected=rows[a:b+1] if a<b else list(reversed(rows[b:a+1]))
            self.assertEqual(result['rows'],len(expected))
            self.assertEqual(result['values'],{v:i/float(len(expected)-1) for i,row in enumerate(expected) for v in row})
            self.assertEqual(result['faces'],{i for i,f in enumerate(faces) if all(v in result['values'] for v in f)})

    def test_tube_caps_and_other_elements_do_not_change_interval(self):
        faces,rows=tube()
        caps=[]
        for index,row in enumerate((rows[0],rows[-1])):
            caps.extend((row[j],row[(j+1)%len(row)],1000+index) for j in range(len(row)))
        first,last=ring_edges(rows[1]),ring_edges(rows[5])
        expected=row_weights(faces,first,last)
        self.assertEqual(row_weights(faces+caps+[(2000,2001,2002,2003,2004)],first,last),expected)

    def test_tube_rejects_partial_mixed_hole_branch_and_two_routes(self):
        faces,rows=tube()
        first,last=ring_edges(rows[1]),ring_edges(rows[5])
        missing=list(faces); missing.pop(15)
        torus=list(faces)
        for j in range(len(rows[0])):
            k=(j+1)%len(rows[0])
            torus.append((rows[-1][j],rows[-1][k],rows[0][k],rows[0][j]))
        cases=[(faces,first[:-1],last), (faces,first[:-1],last[:-1]),
               (missing,first,last), (torus,first,last),
               (faces,first,first),
               (faces+[(rows[3][0],rows[3][1],900)],first,last)]
        for geometry,start,end in cases:
            with self.assertRaises(ValueError): row_weights(geometry,start,end)

    def test_unrelated_ngons_degenerates_and_nonmanifold_faces_do_not_block_panel(self):
        faces,rows=strip(triangles=True)
        unrelated=[(100,101,102,103,104),(110,111,112,110),(),
                   (120,121,122),(121,120,123),(120,121,124)]
        baseline=row_weights(faces,row_edges(rows[0]),row_edges(rows[-1]))
        result=row_weights(faces+unrelated,row_edges(rows[0]),row_edges(rows[-1]))
        self.assertEqual(result,baseline)

    def test_ngon_on_same_element_is_checked_even_beyond_start_or_end(self):
        faces,rows=strip()
        # Same connected element, but the unsupported polygons are beyond seeds.
        outside=[(rows[0][1],rows[0][0],100,101,102),
                 (rows[-1][0],rows[-1][1],103,104,105)]
        for a,b in ((0,6),(1,5),(5,1)):
            with self.assertRaises(ValueError) as caught:
                row_weights(faces+outside,row_edges(rows[a]),row_edges(rows[b]))
            self.assertIn('所在元素',str(caught.exception) if sys.version_info[0]>=3 else unicode(caught.exception))

    def test_two_lines_on_different_elements_have_explicit_warning(self):
        faces,rows=strip()
        other=[tuple(v+1000 for v in face) for face in faces]
        with self.assertRaises(ValueError) as caught:
            row_weights(faces+other,row_edges(rows[0]),row_edges([v+1000 for v in rows[-1]]))
        self.assertIn('同一个元素',str(caught.exception) if sys.version_info[0]>=3 else unicode(caught.exception))

    def test_unsupported_or_nonmanifold_faces_inside_target_are_still_rejected(self):
        faces,rows=strip()
        for defect in ('ngon','duplicate_vertex','nonmanifold'):
            changed=list(faces)
            a,b,c,d=changed[7]
            if defect=='ngon': changed[7]=(a,b,100,c,d)
            elif defect=='duplicate_vertex': changed[7]=(a,b,c,a)
            else: changed.append((a,b,101))
            with self.assertRaises(ValueError):
                row_weights(changed,row_edges(rows[0]),row_edges(rows[-1]))

    def test_random_triangle_quad_rows_and_numbering(self):
        rng=random.Random(421)
        for iteration in range(150):
            length,width=rng.randint(3,18),rng.randint(1,6)
            faces,rows=strip(length,width,True,bool(iteration%2),iteration)
            labels=list(range(100,100+length*(width+1)))
            rng.shuffle(labels)
            mapping={v:labels[v-1] for row in rows for v in row}
            rows=[[mapping[v] for v in row] for row in rows]
            faces=[tuple(mapping[v] for v in (f if rng.randrange(2) else tuple(reversed(f)))) for f in faces]
            rng.shuffle(faces)
            a,b=rng.sample(list(range(length)),2)
            result=row_weights(faces,row_edges(rows[a]),row_edges(rows[b]))
            expected=rows[a:b+1] if a<b else list(reversed(rows[b:a+1]))
            self.assertEqual(result['rows'],len(expected))
            self.assertEqual(result['values'],{v:i/float(len(expected)-1) for i,row in enumerate(expected) for v in row})

    def test_variable_triangle_row_counts(self):
        rows,faces=[],[]
        for count in (6,5,4,3,2):
            offset=sum(len(row) for row in rows)+1
            rows.append(list(range(offset,offset+count)))
        for top,bottom in zip(rows,rows[1:]):
            i,j=0,0
            while i<len(top)-1 or j<len(bottom)-1:
                if j==len(bottom)-1 or (i<len(top)-1 and (i+1)/float(len(top)-1)<=(j+1)/float(len(bottom)-1)):
                    faces.append((top[i],top[i+1],bottom[j])); i+=1
                else:
                    faces.append((top[i],bottom[j+1],bottom[j])); j+=1
        result=row_weights(faces,row_edges(rows[0]),row_edges(rows[-1]))
        self.assertEqual(result['values'],{v:i/4. for i,row in enumerate(rows) for v in row})

    def test_invalid_partial_hole_branch_center_and_two_routes(self):
        faces,rows=strip(triangles=True)
        start,end=row_edges(rows[0]),row_edges(rows[-1])
        cases=[(faces,start[:2],end[:2]),(faces,start,start)]
        missing=list(faces); missing.pop(8)
        cases.append((missing,start,end))
        cases.append((faces+[(rows[2][0],rows[3][0],900)],start,end))
        cases.append((faces+[(rows[2][1],rows[2][2],901)],start,end))
        center=list(faces); f=center.pop(8)
        center.extend((f[i],f[(i+1)%3],902) for i in range(3))
        cases.append((center,start,end))
        loop=list(faces)
        for j in range(3):
            a,b,c,d=rows[-1][j],rows[-1][j+1],rows[0][j+1],rows[0][j]
            loop.extend(((a,b,c),(a,c,d)))
        cases.append((loop,row_edges(rows[1]),row_edges(rows[4])))
        for geometry,first,last in cases:
            with self.assertRaises(ValueError): row_weights(geometry,first,last)


class TopologyActionTests(unittest.TestCase):
    def setUp(self):
        self.rt=TopologyRuntime()
        self.previous=sys.modules.get('pymxs')
        module=types.ModuleType(str('pymxs'))
        module.runtime=self.rt
        class Undo(object):
            def __enter__(self): pass
            def __exit__(self,*args): return False
        module.undo=lambda enabled: Undo()
        sys.modules['pymxs']=module
        action._TOPOLOGY_RECORDS.clear()
    def tearDown(self):
        action._TOPOLOGY_RECORDS.clear()
        if self.previous is None: sys.modules.pop('pymxs',None)
        else: sys.modules['pymxs']=self.previous
    def snapshot(self):
        return copy.deepcopy((self.rt.obj.colors,self.rt.obj.mapping,self.rt.obj.support,
                              {ch:vars(value) for ch,value in self.rt.obj.channels.items()},
                              action._display_state(self.rt.obj)))
    def record(self,index):
        self.rt.select_row(index)
        result=action.vertex_color_gradient_edges()
        self.assertNotIn('warning',result)
        return json.loads(json.dumps(result))['record']
    def endpoints(self,a=1,b=5):
        return self.record(a),self.record(b)
    def apply(self,start,end,**params):
        options=dict(direction='topology',start=start,end=end,mask=[True,False,True])
        options.update(params)
        return action.vertex_color_gradient(**options)
    def test_single_edge_capture_highlights_completed_chain_without_color_writes(self):
        for closed in (False,True):
            self.rt=TopologyRuntime(triangles=False,closed=closed)
            sys.modules['pymxs'].runtime=self.rt
            self.rt.select_row(3)
            expected=list(self.rt.edge_selected)
            self.rt.edge_selected=expected[1:2]
            before=self.snapshot()
            result=action.vertex_color_gradient_edges()
            self.assertEqual(result['edges'],len(expected))
            self.assertEqual(set(self.rt.edge_selected),set(expected))
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.rt.writes,0)
            self.assertEqual(action._TOPOLOGY_RECORDS[result['record']]['edges'],
                             sorted(self.rt.edge_table()[i] for i in expected))

    def test_ambiguous_single_edge_preserves_selection_records_and_colors(self):
        self.rt.select_row(3)
        self.rt.edge_selected=self.rt.edge_selected[1:2]
        selected=list(self.rt.edge_selected)
        before=self.snapshot()
        records=list(action._TOPOLOGY_RECORDS)
        result=action.vertex_color_gradient_edges()
        self.assertIn('完整横线',result['warning'])
        self.assertEqual(self.rt.edge_selected,selected)
        self.assertEqual(list(action._TOPOLOGY_RECORDS),records)
        self.assertEqual(self.snapshot(),before)

    def test_triangle_seed_uses_other_record_and_stale_reference_is_rejected(self):
        reference=self.record(0)
        self.rt.select_row(5)
        expected=list(self.rt.edge_selected)
        self.rt.edge_selected=expected[1:2]
        result=action.vertex_color_gradient_edges(reference=reference)
        self.assertTrue(result['expanded'])
        self.assertEqual(set(self.rt.edge_selected),set(expected))
        self.assertEqual(self.apply(reference,result['record'])['rows'],6)
        self.rt.select_row(5)
        self.rt.edge_selected=expected[1:2]
        self.rt.faces[1]=list(reversed(self.rt.faces[1]))
        before=list(self.rt.edge_selected)
        self.assertIn('参考横线',action.vertex_color_gradient_edges(reference=reference)['warning'])
        self.assertEqual(self.rt.edge_selected,before)

    def test_tube_capture_five_channels_reverse_curve_and_endpoint_continuity(self):
        for triangles,mixed in ((False,False),(True,False),(True,True)):
            self.rt=TopologyRuntime(triangles,mixed,closed=True)
            sys.modules['pymxs'].runtime=self.rt
            before=self.snapshot()
            start,end=self.endpoints()
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.rt.writes,0)
            self.rt.subObjectLevel=0
            self.rt.edge_selected=[]
            for ch in (0,-1,-2,17,18):
                old_green=.234567891 if ch==0 else 1.
                result=self.apply(end,start,channel=ch,minimum=32,maximum=224,curve=75)
                self.assertEqual((result['rows'],result['vertices']),(5,30))
                target=self.rt.channel(self.rt.obj,ch)
                for fid,face in self.rt.faces.items():
                    for v,mv in zip(face,target.mapping[fid]):
                        row=next(i for i,vertices in enumerate(self.rt.rows) if v in vertices)
                        color=target.colors[mv]
                        if 1<=row<=5:
                            value=action.gradient_value((5-row)/4.,32,224,75)
                            self.assertEqual(color,(value,value if ch==-2 else old_green,value))
                        else:
                            self.assertEqual(color,(.123456789,.234567891,.345678912) if ch==0 else (1.,)*3)
                saved=copy.deepcopy((target.colors,target.mapping))
                self.assertEqual(self.apply(end,start,channel=ch,minimum=32,maximum=224,curve=75)['split'],0)
                self.assertEqual((target.colors,target.mapping),saved)

    def test_tube_partial_and_mixed_records_reject_without_channel_allocation(self):
        self.rt=TopologyRuntime(closed=True)
        sys.modules['pymxs'].runtime=self.rt
        first,last=self.endpoints()
        self.rt.select_row(1)
        self.rt.edge_selected.pop()
        partial=action.vertex_color_gradient_edges()['record']
        before=self.snapshot()
        self.assertIn('warning',self.apply(partial,last,channel=18))
        self.rt.select_row(5)
        self.rt.edge_selected.pop()
        other=action.vertex_color_gradient_edges()['record']
        self.assertIn('warning',self.apply(partial,other,channel=18))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.rt.writes,0)

    def test_capture_read_only_and_wrong_level_or_chain_rejected(self):
        before=self.snapshot()
        self.assertIn('warning',action.vertex_color_gradient_edges())
        self.rt.select_row(1)
        self.rt.edge_selected=[]
        self.assertIn('warning',action.vertex_color_gradient_edges())
        self.record(1)
        self.assertEqual(self.snapshot()[0:3],before[0:3])
        self.assertEqual(action._display_state(self.rt.obj),before[-1])
        self.assertEqual(self.rt.writes,0)
        self.rt.edge_selected=list(self.rt.edge_table())
        self.assertIn('warning',action.vertex_color_gradient_edges())
    def test_topology_row_values_are_explicit_integer_bytes(self):
        start,end=self.endpoints()
        self.apply(start,end,minimum=0,maximum=255,curve=50,mask=[True]*3)
        expected={v:value/255. for row,value in zip(self.rt.rows[1:6],(0,64,128,191,255)) for v in row}
        for fid,face in self.rt.faces.items():
            for gv,mv in zip(face,self.rt.obj.mapping[fid]):
                if gv in expected:self.assertEqual(self.rt.obj.colors[mv],(expected[gv],)*3)
    def test_applies_without_selection_and_updates_both_sides_of_endpoint_rows(self):
        start,end=self.endpoints()
        self.rt.subObjectLevel=0
        self.rt.selected=[]
        self.rt.edge_selected=[]
        result=self.apply(start,end)
        self.assertEqual(result['rows'],5)
        self.assertEqual(result['vertices'],20)
        for fid,face in self.rt.faces.items():
            for v,mv in zip(face,self.rt.obj.mapping[fid]):
                old=(.123456789,.234567891,.345678912)
                row=next(i for i,vertices in enumerate(self.rt.rows) if v in vertices)
                if 1 <= row <= 5:
                    t=rounded((row-1)/4.)
                    self.assertEqual(self.rt.obj.colors[mv],(t,old[1],t))
                else:
                    self.assertEqual(self.rt.obj.colors[mv],old)
        saved=copy.deepcopy(self.rt.obj.mapping),copy.deepcopy(self.rt.obj.colors)
        self.assertEqual(self.apply(start,end)['split'],0)
        self.assertEqual((self.rt.obj.mapping,self.rt.obj.colors),saved)
    def test_endpoint_existing_color_seams_agree_in_written_components(self):
        # Separate color IDs on every face corner reproduce a seam inherited
        # from a previous face-scoped gradient, including both endpoint rows.
        for fid,face in self.rt.faces.items():
            mapping=[]
            for index,v in enumerate(face):
                mv=len(self.rt.obj.colors)+1
                self.rt.obj.colors[mv]=(.8,fid/100.,.9)
                mapping.append(mv)
            self.rt.obj.mapping[fid]=mapping
        before=copy.deepcopy(self.rt.obj.colors)
        mappings=copy.deepcopy(self.rt.obj.mapping)
        start,end=self.endpoints()
        self.apply(start,end)
        found={}
        for fid,face in self.rt.faces.items():
            for index,(v,mv) in enumerate(zip(face,self.rt.obj.mapping[fid])):
                row=next(i for i,vertices in enumerate(self.rt.rows) if v in vertices)
                color=self.rt.obj.colors[mv]
                old=before[mappings[fid][index]]
                if 1 <= row <= 5:
                    value=rounded((row-1)/4.)
                    self.assertEqual(color,(value,old[1],value))
                    found.setdefault(v,set()).add((color[0],color[2]))
                else:
                    self.assertEqual(color,old)
        self.assertTrue(all(len(values)==1 for values in found.values()))
    def test_five_channels_curves_reverse_and_alpha(self):
        start,end=self.endpoints(0,6)
        original_green=self.rt.obj.colors[1][1]
        for channel in (0,-1,-2,17,18):
            result=self.apply(end,start,channel=channel,minimum=32,maximum=224,curve=25)
            self.assertEqual((result['rows'],result['channel']),(7,channel))
            target=self.rt.channel(self.rt.obj,channel)
            for fid,face in self.rt.faces.items():
                for v,mv in zip(face,target.mapping[fid]):
                    index=next(i for i,row in enumerate(self.rt.rows) if v in row)
                    value=action.gradient_value((6-index)/6.,32,224,25)
                    color=target.colors[mv]
                    self.assertEqual(color[0],value)
                    self.assertEqual(color[2],value)
                    self.assertEqual(color[1],value if channel==-2 else original_green if channel==0 else 1.)
    def test_same_name_other_object_and_deleted_object_rejected(self):
        start,end=self.endpoints()
        original=self.rt.obj
        other=Model()
        self.rt.obj=other; self.rt.selection=[other]; self.rt.current=other
        self.assertIn('warning',self.apply(start,end))
        self.rt.obj=original; self.rt.selection=[original]; self.rt.current=original
        self.rt.valid_nodes=[]
        self.assertIn('warning',self.apply(start,end))
        self.assertEqual(self.rt.writes,0)
    def test_replaced_base_or_changed_topology_invalidates_records(self):
        start,end=self.endpoints()
        self.rt.obj.baseObject=object(); self.rt.current=self.rt.obj.baseObject
        self.assertIn('warning',self.apply(start,end))
        self.rt.obj.baseObject=self.rt.obj; self.rt.current=self.rt.obj
        self.rt.faces[1]=list(reversed(self.rt.faces[1]))
        self.assertIn('拓扑',self.apply(start,end)['warning'])
        self.assertEqual(self.rt.writes,0)
    def test_deformation_and_mapping_edits_do_not_invalidate_records(self):
        start,end=self.endpoints()
        self.rt.positions={v:(float('nan'),0.,0.) for v in self.rt.positions}
        self.rt.obj.colors[1]=(.2,.987654321,.4)
        result=self.apply(start,end)
        self.assertNotIn('warning',result)
        self.assertTrue(all(c[1]==.987654321 for c in self.rt.obj.colors.values()))
    def test_unknown_expired_and_invalid_tokens_are_noops(self):
        start,end=self.endpoints()
        for missing in (None,[],{},True,123,'unknown'):
            self.assertIn('warning',self.apply(missing,end))
        action._TOPOLOGY_RECORDS.clear()
        self.assertIn('warning',self.apply(start,end))
        self.assertEqual(self.rt.writes,0)
    def test_bounded_records_and_stale_capture_are_reported(self):
        first=self.record(0)
        for unused in range(64): latest=self.record(6)
        self.assertEqual(len(action._TOPOLOGY_RECORDS),64)
        self.assertIn('warning',self.apply(first,latest))
    def test_partial_invalid_and_mismatched_recording_never_allocates_missing_channel(self):
        self.rt.select_row(0); self.rt.edge_selected=self.rt.edge_selected[:2]
        first=action.vertex_color_gradient_edges()['record']
        end=self.record(6)
        before=self.snapshot()
        self.assertIn('warning',self.apply(first,end,channel=18))
        self.assertFalse(self.rt.obj.channels[18].support)
        self.assertEqual(self.rt.obj.colors,before[0])
        self.assertEqual(self.rt.writes,0)
    def test_failure_restores_shared_maps_outside_seams_and_display(self):
        start,end=self.endpoints()
        original_write=action._write
        def fail(*args):
            original_write(*args)
            raise RuntimeError('topology post-write failure')
        before=self.snapshot()
        action._write=fail
        try:
            for channel in (0,-1,-2,17,18):
                with self.assertRaises(RuntimeError): self.apply(start,end,channel=channel)
        finally:
            action._write=original_write
        self.assertEqual(self.rt.obj.colors,before[0])
        self.assertEqual(self.rt.obj.mapping,before[1])
        self.assertEqual(action._display_state(self.rt.obj),before[-1])
        self.assertFalse(any(c.support for c in self.rt.obj.channels.values()))
    def test_existing_seams_and_empty_mask(self):
        start,end=self.endpoints()
        self.rt.obj.colors[2]=(.2,.99999999,.4)
        self.rt.obj.mapping[7][0]=2
        self.assertIn('warning',self.apply(start,end,mask=[False]*3))
        self.assertEqual(self.rt.writes,0)
        self.apply(start,end)
        self.assertEqual(self.rt.obj.colors[self.rt.obj.mapping[7][0]][1],.99999999)
        self.assertEqual(self.rt.obj.colors[self.rt.obj.mapping[8][0]][1],.234567891)
    def test_registered_capture_action(self):
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_gradient_edges'],action.vertex_color_gradient_edges)

    def test_clothing_object_with_other_ngon_keeps_outside_colors(self):
        ids=list(range(len(self.rt.positions)+1,len(self.rt.positions)+6))
        self.rt.positions.update({v:(0.,0.,0.) for v in ids})
        fid=max(self.rt.faces)+1
        self.rt.faces[fid]=ids
        self.rt.obj.mapping[fid]=[1]*5
        start,end=self.endpoints()
        old=self.rt.obj.colors[1]
        result=self.apply(start,end)
        self.assertEqual(result.get('rows'),5)
        self.assertEqual(self.rt.obj.mapping[fid],[1]*5)
        self.assertEqual(self.rt.obj.colors[1],old)


if __name__=='__main__': unittest.main(verbosity=2)
