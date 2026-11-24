# -*- coding: utf-8 -*-
"""Execute the actual action with a scene substitute, on Python 2.7 and 3."""
from __future__ import unicode_literals
import copy
import math
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from max_actions import vertex_color as action


def rounded(value):
    """Independent expected byte rounding for ordinary non-boundary fixtures."""
    return min(255, max(0, int(math.floor(value * 255.0 + 0.5)))) / 255.0


class Point(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z
    def __mul__(self, matrix):
        values = (self.x, self.y, self.z)
        return Point(*(sum(values[i] * matrix[i][j] for i in range(3)) + matrix[3][j] for j in range(3)))


class Channel(object):
    def __init__(self):
        self.support = False
        self.colors = {}
        self.mapping = {1: [], 2: []}


class Model(object):
    name = 'Test'
    def __init__(self):
        self.baseObject = self
        self.objectTransform = ((1.,0.,0.), (0.,1.,0.), (0.,0.,1.), (0.,0.,0.))
        self.colors = {1: (.123456789, .234567891, .345678912)}
        self.mapping = {1: [1, 1, 1], 2: [1, 1, 1]}
        self.support = True
        self.showVertexColors = False
        self.vertexColorsShaded = True
        self.vertexColorType = 2
        self.vertexColorMapChannel = 1
        self.channels = {-1: Channel(), -2: Channel(), 17: Channel(), 18: Channel()}


class Runtime(object):
    Editable_Poly = object()
    Point3 = Point
    Name = staticmethod(lambda value: value)
    Array = staticmethod(lambda *values: list(values))
    def __init__(self):
        self.obj = Model()
        self.selection = [self.obj]
        self.subObjectLevel = 1
        self.polyop = self
        self.modPanel = self
        self.theHold = self
        self.selected = [1]
        self.faces = {1: [1, 2, 3], 2: [1, 3, 4]}
        self.positions = {1: (0., 0., 0.), 2: (5., 10., 15.), 3: (10., 20., 30.), 4: (0., 20., 10.)}
        self.current = self.obj
        self.fail_write = False
        self.writes = 0
        self.holding, self.begun, self.accepted, self.cancelled = False, 0, 0, 0
    def execute(self, source):
        if source != '(fn qz_vertex_color_indices bits = (bits as array))':
            raise AssertionError('Unexpected dynamic MAXScript')
        return lambda values: values
    def maxVersion(self): return [22000]
    def classOf(self, obj): return self.Editable_Poly
    def getCurrentObject(self): return self.current
    def getVertSelection(self, obj): return self.selected
    def getEdgeSelection(self, obj): return [8]
    def getFaceSelection(self, obj): return [2]
    def getVertsUsingEdge(self, obj, bits): return [2, 3]
    def getVertsUsingFace(self, obj, bits): return [1, 3, 4]
    def getNumVerts(self, obj): return 4
    def getNumFaces(self, obj): return 2
    def getDeadFaces(self, obj): return []
    def getFaceVerts(self, obj, face): return self.faces[face]
    def getVert(self, obj, vertex):
        if obj is not self.obj.baseObject: raise AssertionError('Gradient must read base-poly local coordinates')
        return Point(*self.positions[vertex])
    def channel(self, obj, channel):
        if channel == 0: return obj
        return obj.channels[channel]
    def getMapSupport(self, obj, channel):
        return self.channel(obj, channel).support
    def getNumMapVerts(self, obj, channel): return len(self.channel(obj, channel).colors)
    def getMapFace(self, obj, channel, face): return self.channel(obj, channel).mapping[face]
    def getMapVert(self, obj, channel, mv): return Point(*self.channel(obj, channel).colors[mv])
    def setMapSupport(self, obj, channel, support):
        obj = self.channel(obj, channel)
        obj.support = support
        if not support: obj.colors = {}
    def setNumMapVerts(self, obj, channel, count, keep=False):
        obj = self.channel(obj, channel)
        if not keep: obj.colors = {}
        for mv in list(obj.colors):
            if mv > count: del obj.colors[mv]
        for mv in range(1, count + 1): obj.colors.setdefault(mv, (0., 0., 0.))
    def setMapVert(self, obj, channel, mv, point):
        self.writes += 1
        if self.fail_write:
            self.fail_write = False
            raise RuntimeError('write failed')
        self.channel(obj, channel).colors[mv] = (point.x, point.y, point.z)
    def setMapFace(self, obj, channel, face, values): self.channel(obj, channel).mapping[face] = list(values)
    def update(self, obj): pass
    def redrawViews(self): pass
    def Holding(self): return self.holding
    def Begin(self):
        self.begun += 1
        self.holding = True
        self.saved = copy.deepcopy({k: v for k, v in self.obj.__dict__.items() if k != 'baseObject'})
    def Accept(self, label): self.accepted += 1; self.holding = False
    def Cancel(self):
        self.cancelled += 1
        self.obj.__dict__.update(self.saved)
        self.holding = False


class VertexColorHostTests(unittest.TestCase):
    def setUp(self):
        self.rt = Runtime()
        self.previous = sys.modules.get('pymxs')
        module = types.ModuleType(str('pymxs'))
        module.runtime = self.rt
        class Undo(object):
            def __enter__(self): pass
            def __exit__(self, kind, value, tb): return False
        module.undo = lambda enabled: Undo()
        sys.modules['pymxs'] = module
    def tearDown(self):
        if self.previous is None: sys.modules.pop('pymxs', None)
        else: sys.modules['pymxs'] = self.previous
    def run_color(self, rgb=(255, 0, 128), mask=(True, False, True)):
        return action.vertex_color(list(rgb), list(mask))
    def test_preserves_exact_unchecked_component_and_unselected_colors(self):
        result = self.run_color()
        self.assertEqual(self.rt.obj.colors[1], (.123456789, .234567891, .345678912))
        self.assertEqual(self.rt.obj.colors[2], (1., .234567891, 128 / 255.))
        self.assertEqual(self.rt.obj.mapping, {1: [2, 1, 1], 2: [2, 1, 1]})
        self.assertEqual((result['count'], result['vertices'], result['split']), (1, 1, 1))
        self.assertEqual((self.rt.begun, self.rt.accepted), (0, 0))
    def test_shared_selected_map_vertex_written_once(self):
        self.rt.selected = [1, 2, 3, 4]
        result = self.run_color(mask=(True, True, True))
        self.assertEqual(result['count'], 1)
        self.assertEqual(self.rt.writes, 1)
        self.assertEqual(self.rt.obj.colors[1], (1., 0., 128 / 255.))
    def test_preserves_existing_color_seams(self):
        self.rt.obj.colors[2] = (.8, .9, .7)
        self.rt.obj.mapping[2][0] = 2
        self.run_color()
        self.assertEqual(self.rt.obj.colors[2], (1., .9, 128 / 255.))
        self.assertEqual(self.rt.obj.colors[3], (1., .234567891, 128 / 255.))
    def test_initializes_missing_color_channel_white_outside_selection(self):
        self.rt.obj.support = False
        self.rt.obj.colors = {}
        result = self.run_color()
        self.assertTrue(result['initialized'])
        self.assertEqual(self.rt.obj.colors[1], (1., 1., 128 / 255.))
        self.assertEqual(self.rt.obj.colors[2], (1., 1., 1.))
        self.assertEqual(self.rt.obj.mapping[2], [1, 3, 4])
    def test_no_component_is_noop(self):
        result = self.run_color(mask=(False, False, False))
        self.assertIn('warning', result)
        self.assertEqual(self.rt.begun, 0)
    def test_empty_selection_is_noop(self):
        self.rt.selected = []
        self.assertIn('warning', self.run_color())
        self.assertEqual(self.rt.writes, 0)
    def test_object_level_is_noop(self):
        self.rt.subObjectLevel = 0
        self.assertIn('warning', self.run_color())
        self.assertEqual(self.rt.begun, 0)
    def test_multiple_objects_are_not_modified(self):
        self.rt.selection.append(Model())
        self.assertIn('warning', self.run_color())
        self.assertEqual(self.rt.writes, 0)
    def test_modifier_is_not_mistaken_for_base_selection(self):
        self.rt.current = object()
        self.assertIn('warning', self.run_color())
        self.assertEqual(self.rt.begun, 0)
    def test_unsupported_type_or_version_is_noop(self):
        self.rt.classOf = lambda obj: object()
        self.assertIn('warning', self.run_color())
        self.rt.maxVersion = lambda: [29000]
        self.assertIn('warning', self.run_color())
    def test_all_subobject_levels_expand(self):
        for level, expected in ((1, {1}), (2, {2, 3}), (3, {2, 3}), (4, {1, 3, 4}), (5, {1, 3, 4})):
            self.assertEqual(action._selection(self.rt, self.rt.obj, level), expected)
    def test_invalid_mapping_rejected_before_write(self):
        self.rt.obj.mapping[1] = [999]
        with self.assertRaises(ValueError): self.run_color()
        self.assertEqual(self.rt.begun, 0)
    def test_failed_write_restores_original_mapping_and_colors(self):
        original = copy.deepcopy(self.rt.obj.mapping)
        self.rt.fail_write = True
        with self.assertRaises(RuntimeError): self.run_color()
        self.assertEqual(self.rt.obj.mapping, original)
        self.assertEqual(self.rt.obj.colors, {1: (.123456789, .234567891, .345678912)})
        self.assertEqual((self.rt.accepted, self.rt.cancelled), (0, 0))
    def test_failed_restore_is_reported_explicitly(self):
        def fail(*args): raise RuntimeError('persistent SDK error')
        self.rt.setMapVert = fail
        with self.assertRaises(RuntimeError) as caught:
            self.run_color()
        self.assertIn('恢复也失败', caught.exception.args[0])
    def test_invalid_parameters_rejected(self):
        for rgb in ((256, 0, 0), (-1, 0, 0), (True, 0, 0), (1.2, 0, 0), (1, 2), None):
            with self.assertRaises(ValueError): action.validate_color(rgb, [True] * 3)
        with self.assertRaises(ValueError): action.validate_color([0] * 3, [1, 1, 1])
    def test_failed_initialization_restores_missing_channel(self):
        self.rt.obj.support = False
        self.rt.obj.colors = {}
        self.rt.fail_write = True
        with self.assertRaises(RuntimeError): self.run_color()
        self.assertFalse(self.rt.obj.support)
        self.assertEqual(self.rt.obj.colors, {})

    def existing_channel(self, channel):
        data = self.rt.obj.channels[channel]
        data.support = True
        data.colors = {1: (.2, .3, .4)}
        data.mapping = {1: [1,1,1], 2: [1,1,1]}
        return data

    def test_illumination_masks_and_other_channels_are_preserved(self):
        data = self.existing_channel(-1)
        color = copy.deepcopy(self.rt.obj.colors)
        result = action.vertex_color([128,0,255], [True,False,True], channel=-1)
        self.assertEqual(data.colors, {1: (.2,.3,.4), 2: (128/255.,.3,1.)})
        self.assertEqual(self.rt.obj.colors, color)
        self.assertFalse(self.rt.obj.channels[-2].support)
        self.assertEqual(result['channel_name'], 'Illumination')
        self.assertEqual(self.rt.obj.vertexColorType, 'illum')

    def test_alpha_initialization_and_independent_scalar(self):
        color = copy.deepcopy(self.rt.obj.colors)
        result = action.vertex_color(channel=-2, alpha=64)
        data = self.rt.obj.channels[-2]
        self.assertEqual(data.colors[1], (64/255.,64/255.,64/255.))
        self.assertEqual(data.colors[2], (1.,1.,1.))
        self.assertEqual(self.rt.obj.colors, color)
        self.assertFalse(self.rt.obj.channels[-1].support)
        self.assertEqual((result['channel'], result['alpha'], self.rt.obj.vertexColorType), (-2,64,'alpha'))

    def test_alpha_ignores_rgb_mask_and_protects_outside_selection(self):
        data = self.existing_channel(-2)
        action.vertex_color([255]*3, [False]*3, channel=-2, alpha=0)
        self.assertEqual(data.colors[1], (.2,.3,.4))
        self.assertEqual(data.colors[2], (0.,0.,0.))
        self.assertEqual(data.mapping[1], [2,1,1])

    def test_hidden_channel_failure_restores_only_target(self):
        for channel in (-1, -2):
            data = self.existing_channel(channel)
            original = copy.deepcopy(data.__dict__)
            color = copy.deepcopy(self.rt.obj.colors)
            self.rt.fail_write = True
            with self.assertRaises(RuntimeError):
                action.vertex_color([0]*3, [True]*3, channel=channel, alpha=42)
            self.assertEqual(data.__dict__, original)
            self.assertEqual(self.rt.obj.colors, color)

    def test_failed_hidden_initialization_does_not_create_other_channel(self):
        for channel in (-1, -2):
            self.rt.fail_write = True
            with self.assertRaises(RuntimeError):
                action.vertex_color([0]*3, [True]*3, channel=channel, alpha=42)
            self.assertFalse(self.rt.obj.channels[channel].support)
            self.assertEqual(self.rt.obj.channels[channel].colors, {})

    def test_hidden_no_rgb_components_is_noop(self):
        result = action.vertex_color([0]*3, [False]*3, channel=-1)
        self.assertIn('warning', result)
        self.assertFalse(self.rt.obj.channels[-1].support)

    def test_invalid_channels_and_alpha_are_rejected_before_writes(self):
        for channel in (True, 1, 16, 19, -3, 'alpha', 0.0):
            with self.assertRaises(ValueError):
                action.vertex_color([0]*3, [True]*3, channel=channel)
        for alpha in (None, True, -1, 256, 1.2, '42'):
            with self.assertRaises(ValueError): action.vertex_color(channel=-2, alpha=alpha)
        self.assertEqual(self.rt.writes, 0)

    def test_rgb_does_not_create_hidden_channels(self):
        self.run_color()
        self.assertFalse(self.rt.obj.channels[-2].support)
        self.assertFalse(self.rt.obj.channels[-1].support)

    def map_snapshot(self):
        return copy.deepcopy(dict(color=(self.rt.obj.support,self.rt.obj.colors,self.rt.obj.mapping),
                                  maps={ch:data.__dict__ for ch,data in self.rt.obj.channels.items()}))

    def test_all_display_channels_switch_without_editing_or_initializing_maps(self):
        before = self.map_snapshot()
        self.rt.subObjectLevel = 0
        self.rt.selected = []
        self.rt.current = object()  # Display does not require an editing panel.
        for channel, name in ((0,'color'),(-2,'alpha'),(-1,'illum'),(17,'map_channel'),(18,'map_channel')):
            result = action.vertex_color_display(channel)
            self.assertTrue(result['display_only'])
            self.assertEqual(self.rt.obj.vertexColorType, name)
            self.assertTrue(self.rt.obj.showVertexColors)
            self.assertFalse(self.rt.obj.vertexColorsShaded)
            if channel > 0: self.assertEqual(self.rt.obj.vertexColorMapChannel, channel)
            self.assertEqual(self.map_snapshot(), before)
        self.assertEqual(self.rt.writes, 0)

    def test_display_requires_one_poly_but_not_selected_vertices(self):
        self.rt.selection = []
        self.assertIn('warning', action.vertex_color_display(17))
        self.rt.selection = [self.rt.obj, Model()]
        self.assertIn('warning', action.vertex_color_display(17))
        self.rt.selection = [self.rt.obj]
        self.rt.classOf = lambda obj: object()
        self.assertIn('warning', action.vertex_color_display(17))
        self.assertEqual(self.rt.writes, 0)

    def test_display_failure_restores_type_map_number_and_flags(self):
        self.rt.obj.vertexColorMapChannel = 33
        before, maps = action._display_state(self.rt.obj), self.map_snapshot()
        calls = []
        def update(obj):
            calls.append(obj)
            if len(calls) == 1: raise RuntimeError('display refresh failed')
        self.rt.update = update
        with self.assertRaises(RuntimeError): action.vertex_color_display(18)
        self.assertEqual(action._display_state(self.rt.obj), before)
        self.assertEqual(self.map_snapshot(), maps)
        self.assertEqual(self.rt.writes, 0)

    def test_map17_and_map18_masks_seams_and_display_are_independent(self):
        for channel in (17,18):
            data = self.existing_channel(channel)
            before_color = copy.deepcopy(self.rt.obj.colors)
            result = action.vertex_color([128,0,255],[True,False,True],channel=channel)
            self.assertEqual(data.colors, {1:(.2,.3,.4),2:(128/255.,.3,1.)})
            self.assertEqual(data.mapping[1], [2,1,1])
            self.assertEqual(self.rt.obj.colors, before_color)
            self.assertEqual(self.rt.obj.vertexColorType, 'map_channel')
            self.assertEqual(self.rt.obj.vertexColorMapChannel, channel)
            self.assertEqual(result['channel_name'], 'M'+str(channel))

    def test_missing_map17_and_map18_initialize_only_when_painted(self):
        for channel in (17,18):
            action.vertex_color_display(channel)
            self.assertFalse(self.rt.obj.channels[channel].support)
            result = action.vertex_color([0,128,255],[True]*3,channel=channel)
            self.assertTrue(result['initialized'])
            self.assertEqual(self.rt.obj.channels[channel].colors[1], (0.,128/255.,1.))
            self.assertEqual(self.rt.obj.channels[channel].colors[2], (1.,1.,1.))

    def test_failed_map_paint_restores_previous_display_map_number(self):
        original_write = action._write
        def fail_after_write(*args):
            original_write(*args)
            raise RuntimeError('post-write failure')
        for channel in (17,18):
            self.existing_channel(channel)
            self.rt.obj.vertexColorMapChannel = 42
            before, display = self.map_snapshot(), action._display_state(self.rt.obj)
            action._write = fail_after_write
            try:
                with self.assertRaises(RuntimeError):
                    action.vertex_color([0]*3,[True]*3,channel=channel)
            finally:
                action._write = original_write
            self.assertEqual(self.map_snapshot(), before)
            self.assertEqual(action._display_state(self.rt.obj), display)

    def test_map_empty_rgb_mask_does_not_allocate(self):
        result = action.vertex_color([0]*3,[False]*3,channel=18)
        self.assertIn('warning', result)
        self.assertFalse(self.rt.obj.channels[18].support)

    def test_display_action_is_registered_and_validates_channel_and_host(self):
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_display'], action.vertex_color_display)
        with self.assertRaises(ValueError): action.vertex_color_display(19)
        self.rt.maxVersion = lambda: [29000]
        self.assertIn('warning', action.vertex_color_display(17))

    def test_scale_preserves_distinct_seams_and_exact_unchecked_components(self):
        old = self.rt.obj.colors[1]
        seam = (.7, .876543219, .4)
        self.rt.obj.colors[2] = seam
        self.rt.obj.mapping[2][0] = 2
        result = action.vertex_color_scale(.5, [True, False, True])
        self.assertEqual(self.rt.obj.colors[1], old)
        self.assertEqual(self.rt.obj.colors[2], (89/255., seam[1], 51/255.))
        self.assertEqual(self.rt.obj.colors[3], (rounded(old[0] * .5), old[1], rounded(old[2] * .5)))
        self.assertEqual((result['count'], result['split'], result['factor']), (2, 1, .5))

    def test_scale_deduplicates_and_repeated_clicks_read_current_rounded_values(self):
        self.rt.selected = [1, 2, 3, 4]
        old = self.rt.obj.colors[1]
        result = action.vertex_color_scale(.5, [True] * 3)
        self.assertEqual((result['count'], self.rt.writes), (1, 1))
        self.assertEqual(self.rt.obj.colors[1], tuple(rounded(v * .5) for v in old))
        action.vertex_color_scale(.5, [True] * 3)
        self.assertEqual(self.rt.obj.colors[1], tuple(rounded(rounded(v * .5) * .5) for v in old))
        self.assertEqual(self.rt.writes, 2)

    def test_scale_zero_and_clamping_do_not_touch_unchecked_values(self):
        self.rt.obj.colors[1] = (-.2, 1.234567891, .8)
        action.vertex_color_scale(2, [True, False, True])
        self.assertEqual(self.rt.obj.colors[2], (0., 1.234567891, 1.))
        action.vertex_color_scale(0, [True, False, True])
        self.assertEqual(self.rt.obj.colors[2], (0., 1.234567891, 0.))

    def test_scale_all_channels_preserves_outside_and_other_channel_data(self):
        for channel in (0, -1, -2, 17, 18):
            data = self.rt.obj if channel == 0 else self.existing_channel(channel)
            old = copy.deepcopy(data.colors)
            others = {ch: copy.deepcopy(self.rt.channel(self.rt.obj, ch).__dict__)
                      for ch in (-1, -2, 17, 18) if ch != channel}
            rgb = copy.deepcopy(self.rt.obj.colors)
            result = action.vertex_color_scale(2, [True, False, True], channel=channel)
            expected = (rounded(old[1][0] * 2),) * 3 if channel == -2 else (rounded(old[1][0] * 2), old[1][1], rounded(old[1][2] * 2))
            self.assertEqual(data.colors[1], old[1])
            self.assertEqual(data.colors[2], expected)
            self.assertEqual(data.mapping[1], [2, 1, 1])
            self.assertEqual(result['channel'], channel)
            if channel != 0: self.assertEqual(self.rt.obj.colors, rgb)
            for ch, before in others.items():
                self.assertEqual(self.rt.channel(self.rt.obj, ch).__dict__, before)
            if channel in (17, 18): self.assertEqual(self.rt.obj.vertexColorMapChannel, channel)

    def test_scale_alpha_uses_scalar_x_and_ignores_rgb_mask(self):
        data = self.existing_channel(-2)
        action.vertex_color_scale(.5, [False] * 3, channel=-2)
        self.assertEqual(data.colors, {1: (.2, .3, .4), 2: (26/255.,)*3})
        action.vertex_color_scale(10, channel=-2)
        self.assertEqual(data.colors[2], (1., 1., 1.))

    def test_scale_missing_channels_start_from_white(self):
        self.rt.obj.support, self.rt.obj.colors = False, {}
        for channel in (0, -1, -2, 17, 18):
            before = self.map_snapshot()
            result = action.vertex_color_scale(.5, [True, False, True], channel=channel)
            data = self.rt.channel(self.rt.obj, channel)
            self.assertTrue(result['initialized'])
            self.assertEqual(data.colors[1], (128/255.,)*3 if channel == -2 else (128/255., 1., 128/255.))
            self.assertEqual(data.colors[2], (1., 1., 1.))
            if channel != 0: self.assertEqual(self.map_snapshot()['color'], before['color'])

    def test_scale_one_quantizes_existing_values_and_initializes_missing_channels(self):
        self.rt.obj.colors[1] = (1.2, -.1, .3)
        for channel in (0, -1, -2, 17, 18):
            result = action.vertex_color_scale(1, [True] * 3, channel=channel)
            self.assertNotIn('warning', result)
            data = self.rt.channel(self.rt.obj, channel)
            self.assertEqual(data.colors[data.mapping[1][0]], (1.,0.,77/255.) if channel == 0 else (1.,)*3)

    def test_every_stored_byte_scaled_by_half_rounds_ties_up(self):
        for value in range(256):
            old = action._float32(value / 255.0)
            expected = ((value + 1) // 2) / 255.0
            self.assertEqual(action.scale_color((old,old,old), .5, (True,)*3, 0), (rounded(expected),)*3)
            self.assertEqual(action.quantize_component(old), value / 255.0)

    def test_rounding_boundaries_clamping_and_nonfinite_values(self):
        for value,expected in ((-.2,0), (0,0), (12.49,12), (12.5,13), (12.51,13),
                               (13.5,14), (127.5,128), (254.5,255), (255,255), (300,255)):
            self.assertEqual(action.quantize_component(value / 255.), expected / 255.)
        for value in (float('nan'),float('inf'),-float('inf')):
            with self.assertRaises(ValueError):action.quantize_component(value)

    def test_scale_invalid_parameters_rejected_before_write(self):
        for factor in (True, None, -.01, 10.01, '0.5', float('nan'), float('inf'), -float('inf'), [], 10 ** 1000):
            with self.assertRaises(ValueError): action.vertex_color_scale(factor, [True] * 3)
        for mask in (None, [True], [1, 1, 1], 'RGB'):
            with self.assertRaises(ValueError): action.vertex_color_scale(.5, mask)
        for channel in (True, 1, -3, 0.0, '17'):
            with self.assertRaises(ValueError): action.vertex_color_scale(.5, [True] * 3, channel=channel)
        self.assertEqual(self.rt.writes, 0)

    def test_scale_empty_mask_does_not_allocate(self):
        before = self.map_snapshot()
        self.assertIn('warning', action.vertex_color_scale(0, [False] * 3, channel=18))
        self.assertEqual(self.map_snapshot(), before)
        self.assertEqual(self.rt.writes, 0)

    def test_scale_respects_host_object_panel_and_selection_restrictions(self):
        cases = [dict(selection=[]), dict(selection=[self.rt.obj, Model()]),
                 dict(current=object()), dict(subObjectLevel=0), dict(selected=[]),
                 dict(maxVersion=lambda: [29000]), dict(classOf=lambda obj: object())]
        for changes in cases:
            saved = {key: getattr(self.rt, key) for key in changes}
            self.rt.__dict__.update(changes)
            try:
                self.assertIn('warning', action.vertex_color_scale(.5, [True] * 3))
            finally:
                self.rt.__dict__.update(saved)
        self.assertEqual(self.rt.writes, 0)

    def test_scale_invalid_color_is_rejected_before_any_scene_write(self):
        self.rt.obj.colors[2] = (float('nan'), .2, .3)
        self.rt.obj.mapping[2][0] = 2
        mapping = copy.deepcopy(self.rt.obj.mapping)
        with self.assertRaises(ValueError): action.vertex_color_scale(.5, [True] * 3)
        self.assertEqual(self.rt.writes, 0)
        self.assertEqual(self.rt.obj.mapping, mapping)

    def test_scale_failure_restores_mapping_colors_and_display_on_all_channels(self):
        original_write = action._write
        def fail_after_write(*args):
            original_write(*args)
            raise RuntimeError('scale post-write failure')
        for channel in (0, -1, -2, 17, 18):
            if channel != 0: self.existing_channel(channel)
            self.rt.obj.vertexColorMapChannel = 42
            before, display = self.map_snapshot(), action._display_state(self.rt.obj)
            action._write = fail_after_write
            try:
                with self.assertRaises(RuntimeError) as caught:
                    action.vertex_color_scale(.5, [True] * 3, channel=channel)
                self.assertIn('缩放颜色失败，已恢复', caught.exception.args[0])
            finally:
                action._write = original_write
            self.assertEqual(self.map_snapshot(), before)
            self.assertEqual(action._display_state(self.rt.obj), display)

    def test_scale_failed_initialization_removes_missing_channels(self):
        self.rt.obj.support, self.rt.obj.colors = False, {}
        for channel in (0, -1, -2, 17, 18):
            self.rt.fail_write = True
            with self.assertRaises(RuntimeError):
                action.vertex_color_scale(.5, [True] * 3, channel=channel)
            data = self.rt.channel(self.rt.obj, channel)
            self.assertFalse(data.support)
            self.assertEqual(data.colors, {})

    def test_scale_failed_restore_and_registration(self):
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_scale'], action.vertex_color_scale)
        def fail(*args): raise RuntimeError('persistent SDK error')
        self.rt.setMapVert = fail
        with self.assertRaises(RuntimeError) as caught:
            action.vertex_color_scale(.5, [True] * 3)
        self.assertIn('缩放颜色失败', caught.exception.args[0])
        self.assertIn('恢复也失败', caught.exception.args[0])

    def run_gradient(self, **params):
        defaults = dict(direction='X', mask=[True]*3)
        defaults.update(params)
        return action.vertex_color_gradient(**defaults)

    def corner_color(self, face, corner, channel=0):
        data = self.rt.channel(self.rt.obj, channel)
        return data.colors[data.mapping[face][corner]]

    def test_gradient_splits_positions_reuses_equal_values_and_repeats_without_growth(self):
        self.rt.selected = [1,2,3,4]
        result = self.run_gradient()
        self.assertEqual(self.rt.obj.mapping, {1:[1,2,3],2:[1,3,1]})
        self.assertEqual(self.rt.obj.colors, {1:(0.,)*3,2:(128/255.,)*3,3:(1.,)*3})
        self.assertEqual((result['vertices'],result['count'],result['split'],self.rt.writes),(4,3,2,3))
        before = self.map_snapshot()
        self.assertEqual(self.run_gradient()['split'],0)
        self.assertEqual(self.map_snapshot(),before)

    def test_gradient_default_negative_z_goes_from_top_to_bottom(self):
        self.rt.selected = [1,2,3]
        result = action.vertex_color_gradient(mask=[True]*3)
        self.assertEqual(result['direction'],'-Z')
        self.assertEqual(self.corner_color(1,2),(0.,)*3)
        self.assertEqual(self.corner_color(1,1),(128/255.,)*3)
        self.assertEqual(self.corner_color(1,0),(1.,)*3)

    def test_gradient_all_six_world_axes_and_masks(self):
        self.rt.selected = [1,2,3]
        old_g = self.rt.obj.colors[1][1]
        for direction in ('X','Y','Z','-X','-Y','-Z'):
            result = self.run_gradient(direction=direction, minimum=32,maximum=224,mask=[True,False,True])
            first,last = (224/255.,32/255.) if direction.startswith('-') else (32/255.,224/255.)
            self.assertEqual(self.corner_color(1,0),(first,old_g,first))
            self.assertEqual(self.corner_color(1,1),(128/255.,old_g,128/255.))
            self.assertEqual(self.corner_color(1,2),(last,old_g,last))
            self.assertEqual(result['direction'],direction)

    def test_gradient_uses_world_transform_not_local_axes(self):
        self.rt.selected = [1,2,3]
        self.rt.obj.objectTransform = ((0.,2.,0.),(-3.,0.,0.),(0.,0.,4.),(100.,-50.,25.))
        before = copy.deepcopy(self.rt.positions)
        self.run_gradient()
        self.assertEqual(self.corner_color(1,0),(1.,)*3)
        self.assertEqual(self.corner_color(1,1),(128/255.,)*3)
        self.assertEqual(self.corner_color(1,2),(0.,)*3)
        self.assertEqual(self.rt.positions,before)

    def test_gradient_curves_round_to_integer_bytes(self):
        self.rt.selected = [1,2,3]
        for curve,expected in ((25,.25),(50,.5),(75,.75)):
            self.run_gradient(curve=curve)
            self.assertEqual(self.corner_color(1,1),(rounded(expected),)*3)
            self.assertEqual(self.corner_color(1,0),(0.,)*3)
            self.assertEqual(self.corner_color(1,2),(1.,)*3)
        self.run_gradient(minimum=255,maximum=0)
        self.assertEqual(self.corner_color(1,0),(1.,)*3)
        self.assertEqual(self.corner_color(1,2),(0.,)*3)

    def test_gradient_custom_projection_clamps_and_keeps_fixed_point_range(self):
        self.rt.selected = [1,2,3,4]
        self.run_gradient(direction='custom',start=[2,4,6],end=[8,16,24])
        self.assertEqual(self.corner_color(1,0),(0.,)*3)
        self.assertEqual(self.corner_color(1,1),(128/255.,)*3)
        self.assertEqual(self.corner_color(1,2),(1.,)*3)
        self.assertEqual(self.corner_color(2,2),(128/255.,)*3)  # Off-axis point projects onto the same midpoint.
        self.rt.selected = [2]
        self.run_gradient(direction='custom',start=[2,4,6],end=[8,16,24],curve=25)
        self.assertEqual(self.corner_color(1,1),(64/255.,)*3)
        self.run_gradient(direction='custom',start=[8,16,24],end=[2,4,6],minimum=32,maximum=224)
        self.assertEqual(self.corner_color(1,1),(128/255.,)*3)

    def test_gradient_selection_bounds_and_outside_shared_corners(self):
        self.rt.selected = [1,2]
        original = self.rt.obj.colors[1]
        result = self.run_gradient()
        self.assertEqual(self.corner_color(1,0),(0.,)*3)
        self.assertEqual(self.corner_color(1,1),(1.,)*3)
        self.assertEqual(self.corner_color(1,2),original)
        self.assertEqual(self.corner_color(2,2),original)
        self.assertEqual((result['vertices'],result['split']),(2,2))
        self.assertEqual(self.rt.selected,[1,2])

    def test_gradient_preserves_seams_and_exact_unchecked_components(self):
        self.rt.selected = [1,2]
        self.rt.obj.colors[2] = (.6,.876543219,.8)
        self.rt.obj.mapping[2][0] = 2
        old_g = self.rt.obj.colors[1][1]
        self.run_gradient(mask=[True,False,True])
        self.assertEqual(self.corner_color(1,0),(0.,old_g,0.))
        self.assertEqual(self.corner_color(2,0),(0.,.876543219,0.))
        self.assertNotEqual(self.rt.obj.mapping[1][0],self.rt.obj.mapping[2][0])

    def test_gradient_all_channels_isolated_and_alpha_ignores_rgb_mask(self):
        self.rt.selected = [1,2,3]
        for channel in (0,-1,-2,17,18):
            data = self.rt.obj if channel == 0 else self.existing_channel(channel)
            others = {ch:copy.deepcopy(self.rt.channel(self.rt.obj,ch).colors) for ch in (0,-1,-2,17,18) if ch != channel}
            old = data.colors[1]
            mask = [False]*3 if channel == -2 else [True,False,True]
            result = self.run_gradient(channel=channel,minimum=32,maximum=224,curve=25,mask=mask)
            value = 80/255.
            self.assertEqual(self.corner_color(1,1,channel),(value,)*3 if channel == -2 else (value,old[1],value))
            self.assertEqual(self.corner_color(2,2,channel),old)
            for ch,colors in others.items(): self.assertEqual(self.rt.channel(self.rt.obj,ch).colors,colors)
            self.assertEqual(result['channel'],channel)
            if channel in (17,18): self.assertEqual(self.rt.obj.vertexColorMapChannel,channel)

    def test_gradient_missing_channels_white_outside_selection(self):
        self.rt.selected = [1,2]
        self.rt.obj.support,self.rt.obj.colors = False,{}
        for channel in (0,-1,-2,17,18):
            result = self.run_gradient(channel=channel,mask=[True,False,True])
            self.assertTrue(result['initialized'])
            self.assertEqual(self.corner_color(1,0,channel),(0.,)*3 if channel == -2 else (0.,1.,0.))
            self.assertEqual(self.corner_color(1,1,channel),(1.,)*3)
            self.assertEqual(self.corner_color(1,2,channel),(1.,)*3)

    def test_gradient_missing_coincident_custom_points_and_flat_axis_are_noops(self):
        before,display = self.map_snapshot(),action._display_state(self.rt.obj)
        for params in (dict(direction='custom'),dict(direction='custom',start=[0,0,0],end=[0,0,0]),dict(channel=18)):
            self.assertIn('warning',self.run_gradient(**params))
        self.rt.selected = [1,2,3]
        self.assertIn('warning',self.run_gradient(mask=[False]*3))
        self.rt.selected = []
        self.assertIn('warning',self.run_gradient())
        self.assertEqual(self.map_snapshot(),before)
        self.assertEqual(action._display_state(self.rt.obj),display)
        self.assertEqual(self.rt.writes,0)

    def test_gradient_invalid_parameters_fail_before_writes(self):
        for direction in ('x','local','',None,True,[],'XY'):
            with self.assertRaises(ValueError): self.run_gradient(direction=direction)
        for key,values in (('minimum',(-1,256,True,.5,None)),('maximum',(-1,256,True,.5,None)),('curve',(24,76,True,50.,None))):
            for value in values:
                with self.assertRaises(ValueError): self.run_gradient(**{key:value})
        for point in ([0,0],[True,0,0],[float('nan'),0,0],[float('inf'),0,0],['1',0,0],[10**1000,0,0]):
            with self.assertRaises(ValueError): self.run_gradient(direction='custom',start=point,end=[1,2,3])
        with self.assertRaises(ValueError): self.run_gradient(direction='custom',start=[-1e308,0,0],end=[1e308,0,0])
        with self.assertRaises(ValueError): self.run_gradient(mask=[1]*3)
        with self.assertRaises(ValueError): self.run_gradient(channel=19)
        self.assertEqual(self.rt.writes,0)

    def test_gradient_invalid_world_positions_mapping_and_fresh_bounds(self):
        self.rt.selected = [1,2,3]
        original = self.rt.positions[2]
        self.rt.positions[2] = (float('nan'),10.,15.)
        with self.assertRaises(ValueError): self.run_gradient()
        self.assertEqual(self.rt.writes,0)
        self.rt.positions[2] = original
        self.run_gradient()
        self.rt.positions[3] = (20.,20.,30.)
        self.run_gradient()
        self.assertEqual(self.corner_color(1,1),(64/255.,)*3)
        self.rt.obj.mapping[1] = [999]
        with self.assertRaises(ValueError): self.run_gradient()

    def test_gradient_subobject_levels_base_poly_and_restrictions(self):
        self.rt.obj.baseObject = object()
        self.rt.current = self.rt.obj.baseObject
        self.rt.selected = [1,2,3]
        for level,count in ((1,3),(2,2),(3,2),(4,3),(5,3)):
            self.rt.subObjectLevel = level
            self.assertEqual(self.run_gradient()['vertices'],count)
        self.rt.subObjectLevel = 0
        self.assertIn('warning',self.run_gradient())
        self.rt.subObjectLevel = 1
        self.rt.current = object()
        self.assertIn('warning',self.run_gradient())

    def test_gradient_failure_restores_splits_five_channels_and_display(self):
        self.rt.selected = [1,2,3]
        original_write = action._write
        def fail_after_write(*args):
            original_write(*args)
            raise RuntimeError('gradient post-write failure')
        for channel in (0,-1,-2,17,18):
            if channel != 0: self.existing_channel(channel)
            self.rt.obj.vertexColorMapChannel = 42
            before,display = self.map_snapshot(),action._display_state(self.rt.obj)
            action._write = fail_after_write
            try:
                with self.assertRaises(RuntimeError) as caught: self.run_gradient(channel=channel,direction='custom',start=[0,0,0],end=[10,20,30],curve=75)
                self.assertIn('应用渐变失败，已恢复',caught.exception.args[0])
            finally: action._write = original_write
            self.assertEqual(self.map_snapshot(),before)
            self.assertEqual(action._display_state(self.rt.obj),display)

    def test_gradient_failure_during_allocation_remapping_values_or_display(self):
        self.rt.selected = [1,2,3]
        for name in ('setNumMapVerts','setMapFace','setMapVert','update'):
            original = getattr(self.rt,name); failed = [False]
            def fail_once(*args,**kwargs):
                result = original(*args,**kwargs)
                if not failed[0]:
                    failed[0] = True
                    raise RuntimeError('failure at '+name)
                return result
            before,display = self.map_snapshot(),action._display_state(self.rt.obj)
            setattr(self.rt,name,fail_once)
            try:
                with self.assertRaises(RuntimeError): self.run_gradient()
            finally: setattr(self.rt,name,original)
            self.assertEqual(self.map_snapshot(),before)
            self.assertEqual(action._display_state(self.rt.obj),display)

    def test_gradient_failed_initialization_and_restore_reporting(self):
        self.rt.selected = [1,2,3]
        self.rt.obj.support,self.rt.obj.colors = False,{}
        for channel in (0,-1,-2,17,18):
            self.rt.fail_write = True
            with self.assertRaises(RuntimeError): self.run_gradient(channel=channel)
            self.assertFalse(self.rt.channel(self.rt.obj,channel).support)
        self.rt.obj.support,self.rt.obj.colors = True,{1:(.2,.3,.4)}
        def fail(*args): raise RuntimeError('persistent SDK error')
        self.rt.setMapVert = fail
        with self.assertRaises(RuntimeError) as caught: self.run_gradient()
        self.assertIn('恢复也失败',caught.exception.args[0])

    def test_gradient_point_capture_world_mean_is_read_only_and_registration(self):
        self.rt.selected = [1,2,3]
        self.rt.obj.objectTransform = ((0.,2.,0.),(-3.,0.,0.),(0.,0.,4.),(100.,-50.,25.))
        before,display = self.map_snapshot(),action._display_state(self.rt.obj)
        result = action.vertex_color_gradient_point()
        self.assertEqual(result['point'],[70.,-40.,85.])
        self.assertEqual(result['vertices'],3)
        self.assertEqual(self.map_snapshot(),before)
        self.assertEqual(action._display_state(self.rt.obj),display)
        self.assertEqual(self.rt.writes,0)
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_gradient'],action.vertex_color_gradient)
        self.assertIs(ACTIONS['vertex_color_gradient_point'],action.vertex_color_gradient_point)

    def test_gradient_capture_all_subobject_levels_and_invalid_selection(self):
        self.rt.selected = [1,2,3]
        for level,count in ((1,3),(2,2),(3,2),(4,3),(5,3)):
            self.rt.subObjectLevel = level
            self.assertEqual(action.vertex_color_gradient_point()['vertices'],count)
        self.rt.subObjectLevel = 1
        for changes in (dict(selection=[]),dict(selection=[self.rt.obj,Model()]),dict(current=object()),dict(subObjectLevel=0),dict(selected=[]),dict(maxVersion=lambda:[29000]),dict(classOf=lambda obj:object())):
            saved = {key:getattr(self.rt,key) for key in changes}
            self.rt.__dict__.update(changes)
            try: self.assertIn('warning',action.vertex_color_gradient_point())
            finally: self.rt.__dict__.update(saved)
        self.rt.subObjectLevel = 1
        self.rt.selected = [999]
        with self.assertRaises(ValueError): action.vertex_color_gradient_point()
        self.assertEqual(self.rt.writes,0)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(VertexColorHostTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.testsRun or not result.wasSuccessful(): raise SystemExit(1)
