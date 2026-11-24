# -*- coding: utf-8 -*-
"""Selection-by-color behavior, including Max float32 precision and seams."""
from __future__ import unicode_literals
import copy
import os
import struct
import sys
import types
import unittest

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from max_actions import vertex_color as action
from test_vertex_color_host import Runtime


def stored(value):
    return struct.unpack('f',struct.pack('f',value/255.))[0]


class SelectionRuntime(Runtime):
    def __init__(self):
        Runtime.__init__(self)
        self.faces={1:[1,2,3,4],2:[1,4,5,6]}
        self.positions.update({5:(0.,0.,0.),6:(0.,0.,0.)})
        self.obj.mapping={1:[1,2,3,4],2:[7,4,5,6]}
        self.obj.colors={i:(stored(value),stored(50),stored(100))
                         for i,value in enumerate((190,200,210,199.5,210.5,189.5,80),1)}
        self.selected=list(range(1,7))
        self.fail_selection=False
        self.dead=[]
    def getNumVerts(self,obj): return len(self.positions)
    def getDeadFaces(self,obj): return self.dead
    def setVertSelection(self,obj,values):
        self.selected=list(values)
        if self.fail_selection:
            self.fail_selection=False
            raise RuntimeError('selection write failed')


class ColorSelectionTests(unittest.TestCase):
    def setUp(self):
        self.rt=SelectionRuntime()
        self.previous=sys.modules.get('pymxs')
        module=types.ModuleType(str('pymxs'))
        module.runtime=self.rt
        sys.modules['pymxs']=module
    def tearDown(self):
        if self.previous is None: sys.modules.pop('pymxs',None)
        else: sys.modules['pymxs']=self.previous
    def apply(self,**params):
        options=dict(rgb=[200,50,100],mask=[True,False,False])
        options.update(params)
        return action.vertex_color_select(**options)
    def snapshot(self):
        obj=self.rt.obj
        return copy.deepcopy((obj.colors,obj.mapping,obj.support,
                              {ch:vars(v) for ch,v in obj.channels.items()},action._display_state(obj),self.rt.faces))
    def test_default_zero_matches_only_target_not_rounded_neighbors(self):
        self.rt.obj.colors[4]=(stored(199.6),0.,0.)
        result=self.apply()
        self.assertEqual(self.rt.selected,[2])
        self.assertEqual((result['count'],result['searched'],result['tolerance']),(1,6,0))
    def test_tolerance_ten_includes_endpoints_and_fractional_values(self):
        self.apply(tolerance=10)
        self.assertEqual(self.rt.selected,[1,2,3,4])
    def test_tolerance_one_differs_from_zero(self):
        self.apply(tolerance=1)
        self.assertEqual(self.rt.selected,[2,4])
    def test_exact_matching_all_256_stored_values_and_every_channel(self):
        for ch in (0,-1,-2,17,18):
            target=self.rt.channel(self.rt.obj,ch)
            target.support=True
            target.mapping=copy.deepcopy(self.rt.obj.mapping)
            for value in range(256):
                self.rt.selected=[2]
                target.colors={i:(stored(value),.19,.83) for i in range(1,8)}
                result=self.apply(channel=ch,rgb=[value,0,0],alpha=value)
                self.assertEqual(self.rt.selected,[2])
                self.assertEqual(result['count'],1)
    def test_clipped_bounds_do_not_clamp_out_of_range_source_colors(self):
        values=(-.1,0.,5.,15.,15.01,255.,255.1)
        self.rt.obj.colors={i:(stored(v),0.,0.) for i,v in enumerate(values,1)}
        self.apply(rgb=[5,0,0],tolerance=10)
        self.assertEqual(self.rt.selected,[2,3,4])
        self.rt.selected=list(range(1,7))
        self.apply(rgb=[250,0,0],tolerance=10)
        self.assertEqual(self.rt.selected,[6])
    def test_every_checked_component_must_match_the_same_corner(self):
        self.rt.selected=[1]
        self.rt.obj.colors[1]=(stored(200),stored(20),0.)
        self.rt.obj.colors[7]=(stored(10),stored(150),0.)
        self.apply(rgb=[200,150,0],mask=[True,True,False])
        self.assertEqual(self.rt.selected,[])
        self.rt.selected=[1]
        self.rt.obj.colors[7]=(stored(200),stored(150),0.)
        self.apply(rgb=[200,150,0],mask=[True,True,False])
        self.assertEqual(self.rt.selected,[1])
    def test_any_seam_corner_matches_without_selecting_shared_map_neighbors(self):
        self.rt.selected=[1]
        self.rt.obj.mapping[2][0]=2
        self.apply()
        self.assertEqual(self.rt.selected,[1])
    def test_alpha_compares_x_only_and_ignores_rgb_mask(self):
        ch=self.rt.obj.channels[-2]
        ch.support=True
        ch.mapping=copy.deepcopy(self.rt.obj.mapping)
        ch.colors={i:(stored(v),float('nan'),float('inf'))
                   for i,v in enumerate((190,200,210,199.5,210.5,189.5,80),1)}
        result=self.apply(channel=-2,alpha=200,mask=None,rgb=None,tolerance=10)
        self.assertEqual(self.rt.selected,[1,2,3,4])
        self.assertEqual(result['alpha'],200)
    def test_all_subobject_levels_filter_current_geometry_only(self):
        for level,expected in ((1,[1,2]),(2,[2,3]),(3,[2,3]),(4,[1,3,4]),(5,[1,3,4])):
            self.rt.subObjectLevel=level
            self.rt.selected=[1,2]
            result=self.apply(tolerance=255)
            self.assertEqual(self.rt.selected,expected)
            self.assertEqual(result['searched'],len(expected))
            self.assertEqual(self.rt.subObjectLevel,1)
    def test_no_match_clears_vertex_selection_and_switches_level(self):
        self.rt.subObjectLevel=4
        result=self.apply(rgb=[40,0,0])
        self.assertEqual(result['count'],0)
        self.assertEqual(self.rt.selected,[])
        self.assertEqual(self.rt.subObjectLevel,1)
    def test_missing_or_empty_channel_preserves_selection_and_level(self):
        for ch in (-1,17):
            self.rt.subObjectLevel=2
            before=self.snapshot()
            selected=list(self.rt.selected)
            result=self.apply(channel=ch)
            self.assertIn('warning',result)
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.rt.selected,selected)
            self.assertEqual(self.rt.subObjectLevel,2)
        self.rt.obj.colors={}
        self.assertIn('warning',self.apply())
    def test_mask_and_tolerance_inputs_rejected_before_changes(self):
        before=self.snapshot()
        for tolerance in (-1,256,True,1.5,'10',None):
            with self.assertRaises(ValueError): self.apply(tolerance=tolerance)
        for params in (dict(rgb=[256,0,0]),dict(mask=None),dict(channel=True),dict(channel=-2,alpha=300)):
            with self.assertRaises(ValueError): self.apply(**params)
        self.assertIn('warning',self.apply(mask=[False]*3))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.rt.selected,list(range(1,7)))
    def test_empty_object_or_wrong_context_preserves_selection(self):
        self.rt.subObjectLevel=0
        self.assertIn('warning',self.apply())
        self.rt.subObjectLevel=1
        self.rt.selected=[]
        self.assertIn('warning',self.apply())
        self.rt.selected=[1]
        self.rt.current=object()
        self.assertIn('warning',self.apply())
        self.assertEqual(self.rt.selected,[1])
    def test_invalid_maps_or_enabled_values_never_partially_select(self):
        for defect in ('length','id','nan'):
            self.setUp_data_for_defect(defect)
            before=list(self.rt.selected)
            with self.assertRaises(ValueError): self.apply()
            self.assertEqual(self.rt.selected,before)
            self.assertEqual(self.rt.subObjectLevel,1)
    def setUp_data_for_defect(self,defect):
        self.rt.obj.mapping={1:[1,2,3,4],2:[7,4,5,6]}
        self.rt.obj.colors[7]=(stored(80),0.,0.)
        if defect=='length': self.rt.obj.mapping[2].pop()
        elif defect=='id': self.rt.obj.mapping[2][-1]=0
        else: self.rt.obj.colors[7]=(float('nan'),0.,0.)
    def test_invalid_unchecked_component_is_ignored(self):
        self.rt.selected=[2]
        self.rt.obj.colors[2]=(stored(200),float('nan'),float('inf'))
        self.apply()
        self.assertEqual(self.rt.selected,[2])
    def test_dead_faces_and_isolated_vertices_are_not_color_matches(self):
        self.rt.dead=[2]
        self.rt.positions[7]=(0.,0.,0.)
        self.rt.selected=[2,5,6,7]
        self.apply(tolerance=255)
        self.assertEqual(self.rt.selected,[2])
    def test_selection_write_failure_restores_original_selection_and_level(self):
        self.rt.subObjectLevel=2
        self.rt.fail_selection=True
        before=list(self.rt.selected)
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rt.selected,before)
        self.assertEqual(self.rt.subObjectLevel,2)
    def test_redraw_failure_after_level_switch_restores_original_state(self):
        self.rt.subObjectLevel=4
        before=list(self.rt.selected)
        calls=[]
        def redraw():
            calls.append(1)
            if len(calls)==1: raise RuntimeError('redraw failed')
        self.rt.redrawViews=redraw
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rt.selected,before)
        self.assertEqual(self.rt.subObjectLevel,4)
    def test_query_preserves_geometry_all_channels_and_display(self):
        before=self.snapshot()
        self.apply(tolerance=10)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.rt.writes,0)
    def test_action_is_registered(self):
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_select'],action.vertex_color_select)


if __name__=='__main__': unittest.main(verbosity=2)
