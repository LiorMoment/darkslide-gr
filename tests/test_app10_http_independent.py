"""Actual localhost transport through the new route; no generation is permitted."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from test_http import Served
from darkslide import fwread, template, generate, inplace_generation

@unittest.skipUnless(os.environ.get('DARKSLIDE_API_ORIGINAL'),'official original required')
class ActualReadonlyHttp(Served):
    synthetic_identity = False
    def test_saved_partial_check_and_closed_generate_over_http(self):
        self.app.template=template.load()
        with patch.object(self.store,'has_input',return_value=True):
            pid=self.store.create_project('transport',fwread.V210_INPUT_SHA256,self.app.template,'GR III')['id']
        changes={'8':{'film':'ektar100'},'11':{'film':'xtra400_portraendura'}}
        self.assertEqual(self.call('PUT','/api/projects/'+pid+'/draft',{'changes':changes}).status,200)
        self.assertEqual(self.call('POST','/api/projects/'+pid+'/save',{}).status,200)
        with patch.object(inplace_generation,'production_available',return_value=False),patch.object(generate,'run_inplace',side_effect=AssertionError('no real generation')),patch.object(self.store,'input_path',return_value=Path(os.environ['DARKSLIDE_API_ORIGINAL'])),patch.object(generate,'run',side_effect=AssertionError('generation')),patch.object(self.store,'put_build',side_effect=AssertionError('output')):
            response=self.call('POST','/api/projects/'+pid+'/film-prepare',dict(expected_changes=changes,request_id='transport'))
            self.assertEqual(response.status,200,response.body)
            result=response.json();self.assertTrue(result['data_verified']);self.assertEqual(result['decoded_growth_bytes'],0)
            self.assertFalse(result['production_approved']);self.assertFalse(result['full_image_created'])
            self.assertEqual([row['slot'] for row in result['slots'] if row['factory']],[2,9,10])
            rejected=self.call('POST','/api/projects/'+pid+'/generate',{})
            self.assertEqual(rejected.status,422,rejected.body)
            stale=self.call('POST','/api/projects/'+pid+'/film-prepare',dict(expected_changes={},request_id='stale'))
            self.assertEqual(stale.status,409,stale.body)
