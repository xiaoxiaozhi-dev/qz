# -*- coding: utf-8 -*-
"""Viewport action validation, ownership and read-only runtime configuration."""
from __future__ import unicode_literals
import os
import sys
import types
import unittest

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from max_actions import vertex_color as action
from max_actions import _vertex_color_viewport as numbers


class Runtime(object):
    Array=staticmethod(lambda *values:list(values))
    def __init__(self):
        self.sources=[];self.configured=[];self.owner='';self.enabled=False
        self.channel=0;self.mask=[True]*3;self.redraws=0
    def maxVersion(self):return [22000]
    def execute(self,source):self.sources.append(source)
    def redrawViews(self):self.redraws+=1
    def qz_vc_numbers_configure(self,enabled,channel,mask,owner):
        self.configured.append((enabled,channel,mask,owner))
        if enabled:self.enabled=True;self.owner=owner;self.channel=channel;self.mask=mask
        elif owner==self.owner:self.enabled=False
    def qz_vc_numbers_status(self,owner):
        return [self.enabled and self.owner==owner,self.channel,self.mask,4,False,'',1]


class ViewportActionTests(unittest.TestCase):
    def setUp(self):
        self.rt=Runtime();self.owner='a'*32
        self.previous=sys.modules.get('pymxs');self.installed=numbers._INSTALLED
        module=types.ModuleType(str('pymxs'));module.runtime=self.rt
        sys.modules['pymxs']=module;numbers._INSTALLED=False
    def tearDown(self):
        numbers._INSTALLED=self.installed
        if self.previous is None:sys.modules.pop('pymxs',None)
        else:sys.modules['pymxs']=self.previous
    def configure(self,**params):
        options=dict(enabled=True,channel=0,mask=[True,False,True],owner=self.owner)
        options.update(params);return action.vertex_color_numbers(**options)
    def test_all_channels_including_alpha_keep_independent_three_component_mask(self):
        for channel in (0,-1,-2,17,18):
            result=self.configure(channel=channel)
            self.assertEqual(self.rt.configured[-1],(True,channel,[True,False,True],self.owner))
            self.assertEqual(result['channel_name'],action.CHANNELS[channel][0])
            self.assertTrue(result['enabled'])
        self.assertEqual(self.rt.sources,[numbers.SOURCE])
    def test_disable_is_owned_and_status_does_not_install_a_callback(self):
        self.assertFalse(action.vertex_color_numbers_status(owner=self.owner)['enabled'])
        self.assertEqual(self.rt.sources,[])
        self.configure()
        self.assertFalse(self.configure(enabled=False,owner='b'*32)['enabled'])
        self.assertTrue(action.vertex_color_numbers_status(owner=self.owner)['enabled'])
        self.assertFalse(self.configure(enabled=False)['enabled'])
    def test_empty_display_mask_can_clear_all_labels(self):
        self.assertTrue(self.configure(mask=[False]*3)['enabled'])
        self.assertEqual(self.rt.mask,[False]*3)
    def test_status_preserves_non_ascii_callback_error(self):
        self.configure()
        self.rt.qz_vc_numbers_status=lambda owner:[False,0,[True]*3,0,False,'视口异常',0]
        self.assertEqual(action.vertex_color_numbers_status(owner=self.owner)['error'],'视口异常')
    def test_invalid_parameters_rejected_before_runtime_changes(self):
        for params in (dict(enabled=1),dict(enabled=None),dict(channel=True),dict(channel=19),
                       dict(mask=None),dict(mask=[1,0,1]),dict(mask=[True]),dict(owner=None),
                       dict(owner='A'*32),dict(owner='bad'),dict(owner='a'*31+'"')):
            with self.assertRaises(ValueError):self.configure(**params)
        self.assertEqual(self.rt.sources,[]);self.assertEqual(self.rt.configured,[])
    def test_wrong_host_and_reset_and_registration(self):
        self.rt.maxVersion=lambda:[29000]
        self.assertIn('warning',self.configure());self.assertEqual(self.rt.sources,[])
        self.rt.maxVersion=lambda:[22000];self.configure()
        numbers.reset(self.rt)
        self.assertFalse(numbers._INSTALLED)
        self.assertEqual(self.rt.sources[-1],numbers.RESET_SOURCE)
        from max_bridge.server import ACTIONS
        self.assertIs(ACTIONS['vertex_color_numbers'],action.vertex_color_numbers)
        self.assertIs(ACTIONS['vertex_color_numbers_status'],action.vertex_color_numbers_status)


if __name__=='__main__':unittest.main()
