# -*- coding: utf-8 -*-
"""Exact nearest lookup and copy/paste with deterministic seam unification."""
from __future__ import unicode_literals
import copy
import os
import random
import sys
import types
import unittest

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from max_actions import vertex_color as action
from max_actions._vertex_color_nearest import NearestIndex
from test_vertex_color_host import Runtime,Model,rounded


class NearestTests(unittest.TestCase):
    def test_exact_lookup_matches_brute_force_for_cloud_plane_line_and_clusters(self):
        rng=random.Random(1941)
        for shape in ('cloud','plane','line','cluster'):
            samples=[]
            for i in range(350):
                p=tuple(rng.uniform(-100,100) for unused in range(3))
                if shape=='plane':p=(p[0],p[1],0.)
                if shape=='line':p=(p[0],0.,0.)
                if shape=='cluster':p=tuple(v*.001+(1000 if i%2 else 0) for v in p)
                samples.append((i+1,p,(i/350.,.2,.3)))
            index=NearestIndex(samples)
            for unused in range(100):
                q=tuple(rng.uniform(-120,120) for unused in range(3))
                expected=min(samples,key=lambda s:(sum((q[i]-s[1][i])**2 for i in range(3)),s[0]))
                self.assertEqual(index.nearest(q),expected)
    def test_global_nearest_can_be_outside_fixed_neighbor_cells(self):
        a=(1,(4.01,0.,0.),(.1,.2,.3));b=(2,(7.,0.,0.),(.9,.8,.7))
        self.assertEqual(NearestIndex([a,b]).nearest((5.99,0,0)),b)
    def test_equal_distances_and_identical_positions_choose_lowest_geo_id(self):
        samples=[(9,(1.,0.,0.),(.9,)*3),(3,(-1.,0.,0.),(.3,)*3)]
        self.assertEqual(NearestIndex(samples).nearest((0.,0.,0.))[0],3)
        coincident=[(i,(1.,2.,3.),(i/10000.,)*3) for i in range(10000,0,-1)]
        index=NearestIndex(coincident)
        self.assertEqual(index.size,1)
        self.assertEqual(index.nearest((8,9,10))[0],1)
    def test_snapshot_preserves_full_precision_and_does_not_reference_mutable_input(self):
        color=[.123456789,.234567891,.345678912]
        point=[.000000123456,2.,3.]
        index=NearestIndex([(1,point,color)])
        color[0]=.8;point[0]=100.
        self.assertEqual(index.nearest((0,2,3)),(1,(.000000123456,2.,3.),(.123456789,.234567891,.345678912)))
    def test_empty_or_nonfinite_position_rejected(self):
        with self.assertRaises(ValueError): NearestIndex([])
        with self.assertRaises(ValueError): NearestIndex([(1,(float('nan'),0,0),(1,1,1))])
        index=NearestIndex([(1,(0,0,0),(1,1,1))])
        with self.assertRaises(ValueError):index.nearest((0,float('inf'),0))


class TransferRuntime(Runtime):
    def getNumVerts(self,obj):return len(self.positions)
    def setMapSupport(self,obj,channel,support):
        Runtime.setMapSupport(self,obj,channel,support)
        if not support:self.channel(obj,channel).mapping={fid:[] for fid in self.faces}
    def activate(self,obj,positions,selected=None):
        self.obj=obj;self.selection=[obj];self.current=obj.baseObject
        self.positions=dict(positions)
        self.selected=list(sorted(positions) if selected is None else selected)
        self.subObjectLevel=1


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.rt=TransferRuntime()
        self.source=Model();self.source.name='Source'
        self.source.colors={1:(.123456789,.234567891,.345678912),2:(.8,.9,.7),
                            3:(.314159265,.271828182,.161803398),4:(-.125,1.5,.654321987)}
        self.source.mapping={1:[2,3,4],2:[1,4,3]}
        self.target=Model();self.target.name='Target'
        self.source_positions={1:(0.,0.,0.),2:(100.,0.,0.),3:(0.,100.,0.),4:(100.,100.,0.)}
        self.target_positions={1:(0.,0.,0.),2:(100.1,0.,0.),3:(100.,100.,0.),4:(0.,100.,0.)}
        self.rt.activate(self.source,self.source_positions)
        self.previous=sys.modules.get('pymxs')
        module=types.ModuleType(str('pymxs'));module.runtime=self.rt
        class Undo(object):
            def __enter__(self):pass
            def __exit__(self,*args):return False
        module.undo=lambda enabled:Undo()
        sys.modules['pymxs']=module
        self.clipboard=action._COLOR_CLIPBOARD;action._COLOR_CLIPBOARD=None
    def tearDown(self):
        action._COLOR_CLIPBOARD=self.clipboard
        if self.previous is None:sys.modules.pop('pymxs',None)
        else:sys.modules['pymxs']=self.previous
    def target_context(self,selected=None):
        self.rt.activate(self.target,self.target_positions,[1,2] if selected is None else selected)
    def paste(self,**params):
        options=dict(mask=[True,False,True]);options.update(params)
        return action.vertex_color_paste(**options)
    def snapshot(self,obj):
        return copy.deepcopy((obj.colors,obj.mapping,obj.support,{ch:vars(v) for ch,v in obj.channels.items()},action._display_state(obj)))
    def corner(self,face,index,ch=0):
        obj=self.rt.channel(self.target,ch)
        return obj.colors[obj.mapping[face][index]]
    def test_copy_uses_minimum_map_id_not_first_encountered_and_is_read_only(self):
        before=self.snapshot(self.source)
        result=action.vertex_color_copy()
        self.assertEqual(result['count'],4)
        self.assertEqual(action._COLOR_CLIPBOARD['index'].nearest((0,0,0))[2],self.source.colors[1])
        self.assertEqual(self.snapshot(self.source),before)
        self.assertEqual(self.rt.selected,[1,2,3,4])
        self.assertEqual(self.rt.writes,0)
    def test_paste_unifies_selected_components_at_all_corners_and_protects_outside(self):
        action.vertex_color_copy();self.target_context()
        old=self.target.colors[1]
        result=self.paste()
        expected=(31/255.,old[1],88/255.)
        self.assertEqual(self.corner(1,0),expected)
        self.assertEqual(self.corner(2,0),expected)
        self.assertEqual(self.corner(1,1),(80/255.,old[1],41/255.))
        self.assertEqual(self.corner(1,2),old)
        self.assertEqual(self.corner(2,2),old)
        self.assertEqual(result['vertices'],2)
        self.assertEqual(result['source'],'Source')
        self.assertEqual(self.rt.selected,[1,2])
        self.assertEqual(self.rt.subObjectLevel,1)
    def test_existing_target_seams_preserve_unchecked_float_components(self):
        action.vertex_color_copy();self.target_context([1])
        self.target.colors[2]=(.6,.987654321,.8)
        self.target.mapping[2][0]=2
        self.paste()
        a,b=self.corner(1,0),self.corner(2,0)
        self.assertEqual((a[0],a[2]),(b[0],b[2]))
        self.assertEqual(a[1],.234567891);self.assertEqual(b[1],.987654321)
    def test_five_channels_masks_alpha_and_missing_target_initialization(self):
        for ch in (0,-1,-2,17,18):
            src=self.rt.channel(self.source,ch)
            src.support=True
            src.mapping=copy.deepcopy(self.source.mapping)
            src.colors=copy.deepcopy(self.source.colors)
            self.rt.activate(self.source,self.source_positions)
            source_before=self.snapshot(self.source)
            action.vertex_color_copy(channel=ch)
            self.target_context()
            before=self.snapshot(self.target)
            old_green=self.target.colors[1][1] if ch==0 else 1.
            result=self.paste(channel=ch,mask=None if ch==-2 else [True,False,True])
            color=tuple(rounded(v) for v in self.source.colors[1])
            self.assertEqual(self.corner(1,0,ch),color if ch==-2 else (color[0],old_green,color[2]))
            self.assertEqual(result['initialized'],ch!=0)
            self.assertEqual(self.snapshot(self.source),source_before)
            for other in (-1,-2,17,18):
                if other!=ch:self.assertEqual(vars(self.target.channels[other]),before[3][other])
    def test_world_transforms_and_source_snapshot_survive_edit_or_delete(self):
        self.source.objectTransform=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.),(1000.,20.,0.))
        action.vertex_color_copy()
        self.source.colors[1]=(.9,.9,.9)
        self.source.baseObject=object()
        self.target_positions[1]=(1000.,20.,0.)
        self.target_context([1])
        self.paste(mask=[True]*3)
        self.assertEqual(self.corner(1,0),(31/255.,60/255.,88/255.))
    def test_copy_is_snapshot_and_recopied_data_replaces_index(self):
        action.vertex_color_copy();old=action._COLOR_CLIPBOARD
        self.source.colors[1]=(.43,.54,.65)
        action.vertex_color_copy()
        self.assertIsNot(action._COLOR_CLIPBOARD,old)
        self.target_context([1]);self.paste(mask=[True]*3)
        self.assertEqual(self.corner(1,0),(110/255.,138/255.,166/255.))
    def test_repeated_paste_reuses_index_and_does_not_grow_mapping(self):
        action.vertex_color_copy();index=action._COLOR_CLIPBOARD['index']
        self.target_context();self.paste()
        before=self.snapshot(self.target)
        self.assertEqual(self.paste()['split'],0)
        self.assertEqual(self.snapshot(self.target),before)
        self.assertIs(action._COLOR_CLIPBOARD['index'],index)
    def test_clipboard_empty_or_reset_and_empty_mask_do_not_write(self):
        self.target_context();before=self.snapshot(self.target)
        self.assertIn('warning',self.paste())
        self.rt.activate(self.source,self.source_positions);action.vertex_color_copy()
        self.target_context()
        self.assertIn('warning',self.paste(mask=[False]*3))
        action._COLOR_CLIPBOARD=None
        self.assertIn('warning',self.paste())
        self.assertEqual(self.snapshot(self.target),before)

    def test_id_paste_keeps_coincident_source_ids_and_skips_shared_unmatched_corners(self):
        self.source_positions[2] = self.source_positions[1]
        self.rt.activate(self.source, self.source_positions, [1, 2])
        action.vertex_color_copy()
        self.source.colors[1] = (.9, .9, .9)
        self.target_positions[1] = (float('nan'), 0., 0.)
        self.target_context([1, 2, 3])
        old = self.target.colors[1]
        result = self.paste(by_vertex_id=True)
        self.assertEqual((result['vertices'], result['skipped']), (2, 1))
        self.assertEqual(self.corner(1, 0), (31/255., old[1], 88/255.))
        self.assertEqual(self.corner(2, 0), self.corner(1, 0))
        self.assertEqual(self.corner(1, 1), (80/255., old[1], 41/255.))
        self.assertEqual(self.corner(1, 2), old)  # Selected, without a copied ID.
        self.assertEqual(self.corner(2, 2), old)  # Outside the selection.
        self.assertEqual(self.rt.selected, [1, 2, 3])
        saved = self.snapshot(self.target)
        self.assertEqual(self.paste(by_vertex_id=True)['split'], 0)
        self.assertEqual(self.snapshot(self.target), saved)

    def test_id_paste_without_matching_ids_leaves_missing_channel_uninitialized(self):
        self.rt.activate(self.source, self.source_positions, [1])
        action.vertex_color_copy()
        self.target_context([3, 4])
        saved = self.snapshot(self.target)
        result = self.paste(channel=17, by_vertex_id=True)
        self.assertIn('warning', result)
        self.assertEqual((result['vertices'], result['skipped']), (0, 2))
        self.assertEqual(self.rt.writes, 0)
        self.assertEqual(self.snapshot(self.target), saved)

    def test_mirror_world_plane_reuses_snapshot_and_normal_paste_is_independent(self):
        self.source.objectTransform=((0.,2.,0.),(-3.,0.,0.),(0.,0.,4.),(1000.,20.,25.))
        action.vertex_color_copy();snapshot=action._COLOR_CLIPBOARD
        raw={k:tuple(rounded(v) for v in value) for k,value in self.source.colors.items()}
        self.source.colors[1]=(.9,.9,.9)
        self.target.objectTransform=((0.,1.,0.),(-1.,0.,0.),(0.,0.,1.),(-1200.,-30.,5.))
        # Local (50,-200,20) is world (-1000,20,25), mirrored source vertex 1.
        self.target_positions[1]=(50.,-200.,20.)
        self.target_context([1])
        mirrored=self.paste(mask=[True]*3,mirror_x=True)
        self.assertEqual(self.corner(1,0),raw[1])
        self.assertEqual(self.corner(2,0),raw[1])
        self.assertTrue(mirrored['mirror_x'])
        before=self.snapshot(self.target)
        self.assertEqual(self.paste(mask=[True]*3,mirror_x=True)['split'],0)
        self.assertEqual(self.snapshot(self.target),before)
        normal=self.paste(mask=[True]*3)
        self.assertFalse(normal['mirror_x'])
        self.assertEqual(self.corner(1,0),raw[4])
        self.assertIs(action._COLOR_CLIPBOARD,snapshot)
    def test_mirror_both_directions_centerline_ties_and_same_object(self):
        self.source_positions={1:(-10.,0.,0.),2:(10.,0.,0.),3:(-10.,10.,0.),4:(10.,10.,0.)}
        self.rt.activate(self.source,self.source_positions)
        raw={k:tuple(rounded(v) for v in value) for k,value in self.source.colors.items()};action.vertex_color_copy()
        self.target_positions={1:(10.,0.,0.),2:(-10.,0.,0.),3:(0.,0.,0.),4:(0.,10.,0.)}
        self.target_context([1,2,3,4]);self.paste(mask=[True]*3,mirror_x=True)
        self.assertEqual([self.corner(1,0),self.corner(1,1),self.corner(1,2),self.corner(2,2)],
                         [raw[1],raw[3],raw[1],raw[4]])
        self.rt.activate(self.source,self.source_positions,[1])
        self.paste(mask=[True]*3,mirror_x=True)
        self.assertEqual(self.source.colors[self.source.mapping[1][0]],raw[3])
    def test_mirror_rejects_invalid_switch_and_empty_clipboard_without_writes(self):
        self.target_context();before=self.snapshot(self.target)
        self.assertIn('warning',self.paste(mirror_x=True))
        for value in (0,1,None,'X',[],{}):
            with self.assertRaises(ValueError):self.paste(mirror_x=value)
        self.assertEqual(self.snapshot(self.target),before)
        self.assertEqual(self.rt.writes,0)
    def test_all_25_channel_pairs_round_each_component_and_preserve_destination_masks(self):
        for source_ch in (0,-1,-2,17,18):
            src=self.rt.channel(self.source,source_ch)
            src.support=True;src.mapping=copy.deepcopy(self.source.mapping);src.colors=copy.deepcopy(self.source.colors)
            self.rt.activate(self.source,self.source_positions)
            action.vertex_color_copy(channel=source_ch)
            for target_ch in (0,-1,-2,17,18):
                self.target=Model();self.target.name='Target'
                self.target_context([1])
                before=self.snapshot(self.target)
                # Mirror query at X=0 also checks all cross-channel rules.
                result=self.paste(channel=target_ch,mask=[False]*3 if target_ch==-2 else [True,False,True],mirror_x=True)
                color=tuple(rounded(v) for v in self.source.colors[1])
                green=self.target.colors[1][1] if target_ch==0 else 1.
                expected=color if target_ch==-2 else (color[0],green,color[2])
                self.assertEqual(self.corner(1,0,target_ch),expected)
                self.assertEqual((result['source_channel'],result['channel']),(source_ch,target_ch))
                self.assertEqual(result['source_channel_name'],action.CHANNELS[source_ch][0])
                for other in (-1,-2,17,18):
                    if other!=target_ch:self.assertEqual(vars(self.target.channels[other]),before[3][other])
    def test_color_to_alpha_to_map17_round_trip_keeps_three_rounded_values(self):
        action.vertex_color_copy(channel=0)
        self.target_context([1])
        self.paste(channel=-2)
        raw=(31/255.,60/255.,88/255.)
        self.assertEqual(self.corner(1,0,-2),raw)
        action.vertex_color_copy(channel=-2)
        self.paste(channel=17,mask=[True]*3)
        self.assertEqual(self.corner(1,0,17),raw)
    def test_paste_rounds_halves_up_clamps_and_keeps_clipboard_read_only(self):
        for raw,expected in (((12.49/255.,12.5/255.,12.51/255.),(12/255.,13/255.,13/255.)),
                             ((-.125,1.5,127.5/255.),(0.,1.,128/255.))):
            self.source.colors[1]=raw
            self.rt.activate(self.source,self.source_positions);action.vertex_color_copy()
            clipboard=action._COLOR_CLIPBOARD
            self.assertEqual(clipboard['index'].nearest((0,0,0))[2],raw)
            for mirror in (False,True):
                for ch in (0,-1,-2,17,18):
                    self.target_context([1])
                    self.paste(channel=ch,mask=[True]*3,mirror_x=mirror)
                    self.assertEqual(self.corner(1,0,ch),expected)
                    self.assertEqual(self.corner(2,0,ch),expected)
            self.assertIs(action._COLOR_CLIPBOARD,clipboard)
    def test_failed_copy_preserves_previous_clipboard(self):
        action.vertex_color_copy();previous=action._COLOR_CLIPBOARD
        self.assertIn('warning',action.vertex_color_copy(channel=17))
        self.source.colors[1]=(float('nan'),0.,0.)
        with self.assertRaises(ValueError):action.vertex_color_copy()
        self.assertIs(action._COLOR_CLIPBOARD,previous)
    def test_invalid_target_position_or_mapping_is_rejected_before_writes(self):
        action.vertex_color_copy();self.target_positions[1]=(float('inf'),0,0)
        self.target_context();before=self.snapshot(self.target)
        with self.assertRaises(ValueError):self.paste()
        self.assertEqual(self.snapshot(self.target),before)
        self.target_positions[1]=(0,0,0);self.target_context()
        self.target.mapping[2][0]=0
        with self.assertRaises(ValueError):self.paste()
        self.assertEqual(self.rt.writes,0)
    def test_paste_failure_restores_all_five_channels_and_display(self):
        original=action._write
        def failed(*args):
            original(*args)
            raise RuntimeError('post-paste failure')
        for ch in (0,-1,-2,17,18):
            src=self.rt.channel(self.source,ch)
            if ch!=0:
                src.support=True;src.mapping=copy.deepcopy(self.source.mapping);src.colors=copy.deepcopy(self.source.colors)
            self.rt.activate(self.source,self.source_positions);action.vertex_color_copy(channel=ch)
            self.target_context();before=self.snapshot(self.target)
            action._write=failed
            try:
                with self.assertRaises(RuntimeError):self.paste(channel=ch,mirror_x=True)
            finally:action._write=original
            self.assertEqual(self.snapshot(self.target),before)
    def test_isolated_source_vertices_are_skipped_and_isolated_target_not_initialized(self):
        self.source_positions[5]=(1,2,3)
        self.rt.activate(self.source,self.source_positions)
        result=action.vertex_color_copy()
        self.assertEqual((result['count'],result['skipped']),(4,1))
        self.target_positions[5]=(1,2,3);self.target_context([5])
        before=self.snapshot(self.target)
        self.assertIn('warning',self.paste())
        self.assertEqual(self.snapshot(self.target),before)
    def test_copy_and_paste_expand_each_subobject_level(self):
        for level,count in ((1,4),(2,2),(3,2),(4,3),(5,3)):
            self.rt.activate(self.source,self.source_positions);self.rt.subObjectLevel=level
            self.assertEqual(action.vertex_color_copy()['count'],count)
            self.target_context([1,2,3,4]);self.rt.subObjectLevel=level
            self.assertEqual(self.paste()['vertices'],count)
            self.assertEqual(self.rt.subObjectLevel,level)
    def test_invalid_context_or_parameters_do_not_replace_clipboard(self):
        action.vertex_color_copy();previous=action._COLOR_CLIPBOARD
        self.rt.subObjectLevel=0
        self.assertIn('warning',action.vertex_color_copy())
        self.assertIn('warning',self.paste())
        self.assertIs(action._COLOR_CLIPBOARD,previous)
        for params in (dict(channel=True),dict(channel=1)):
            with self.assertRaises(ValueError):action.vertex_color_copy(**params)
        with self.assertRaises(ValueError):self.paste(mask=None)
    def test_registered_actions(self):
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_copy'],action.vertex_color_copy)
        self.assertIs(ACTIONS['vertex_color_paste'],action.vertex_color_paste)


if __name__=='__main__':unittest.main(verbosity=2)
