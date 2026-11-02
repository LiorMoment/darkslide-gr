"""Model-bound project flows and HDF failure handling. Never build an image."""
import copy
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from darkslide import server, models, template, body_target, fwzip, fwread, generate, inplace_generation, iiix_prepare
from darkslide.film_patch_plan import Window


def req(app, body=None):
    return SimpleNamespace(app=app, store=app.store, read_json=lambda: body)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app = server.App(self.tmp.name)

    def create(self, variant='GR III HDF', model=models.GR3):
        with patch.object(self.app.store, 'has_input', return_value=True):
            return server.api_create_project(req(self.app, dict(title='audit', input_sha256=model.input_sha,
                                                        camera_variant=variant)))

    def test_all_seven_variants_persist_and_reopen(self):
        for variant, body in body_target.VARIANTS.items():
            model = models.GR3X if variant.startswith('GR IIIx') else models.GR3
            p = self.create(variant, model)
            reopened = server.api_get_project(req(self.app), p['id'])
            self.assertEqual((reopened['camera_variant'], reopened['target_body']), (variant, body))
            self.assertEqual(reopened['template']['id'], model.id)
            self.assertEqual(self.app.template['id'], models.GR3.id)

    def test_wrong_family_and_conflicting_identity_rejected(self):
        with self.assertRaises(ValueError): self.create('GR IIIx HDF', models.GR3)
        with patch.object(self.app.store, 'has_input', return_value=True), self.assertRaises(ValueError):
            server.api_create_project(req(self.app, dict(input_sha256=models.GR3.input_sha,
                camera_variant='GR III HDF', target_body='GR III')))

    def test_old_project_stays_unspecified_until_explicit_selection(self):
        with patch.object(self.app.store, 'has_input', return_value=True):
            p=self.app.store.create_project('old',models.GR3.input_sha,template.load())
        self.assertNotIn('target_body',p)
        with self.assertRaises(server.ApiError) as e:
            server.api_film_prepare(req(self.app,dict(expected_changes={},request_id='old')),p['id'])
        self.assertEqual(e.exception.status,409)
        updated=server.api_set_target_body(req(self.app,dict(camera_variant='GR III Diary Edition')),p['id'])
        self.assertEqual(updated['target_body'],'GR III')
        self.assertEqual(updated['camera_variant'],'GR III Diary Edition')
        self.assertIsNone(body_target.shutdown_report(updated['target_body'])['collabo_mode_observed'])

    def test_alternating_templates_do_not_change_app_state(self):
        p=self.create(); x=self.create('GR IIIx HDF',models.GR3X)
        for project,model in ((p,models.GR3),(x,models.GR3X),(p,models.GR3)):
            server.api_put_draft(req(self.app,{'changes':{'8':{'film':'gold200'}}}),project['id'])
            saved=server.api_save_project(req(self.app,{}),project['id'])
            self.assertEqual(saved['template']['id'],model.id)
            self.assertEqual(saved['detail']['template_id'],model.id)
        self.assertEqual(self.app.template['id'],models.GR3.id)

    def test_iiix_generation_never_reaches_producer(self):
        p=self.create('GR IIIx',models.GR3X)
        with patch.object(generate,'run',side_effect=AssertionError('producer')),patch.object(self.app.store,'put_build',side_effect=AssertionError('build')):
            with self.assertRaises(server.ApiError) as e:server.api_generate(req(self.app),p['id'])
        self.assertEqual(e.exception.status,422)

    def test_zip_ambiguous_candidates_refused(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            z.writestr('one/fwdc239b.bin',b'fake'); z.writestr('two/fwdc243b.bin',b'fake')
        with self.assertRaises(fwzip.ZipError): fwzip.unwrap(b.getvalue(),'both.zip')


@unittest.skipUnless(os.environ.get('DARKSLIDE_API_ORIGINAL') and os.environ.get('DARKSLIDE_IIIX_ZIP'), 'official inputs required')
class OfficialFlows(Contracts):
    def setUp(self):
        super().setUp()
        self.raw3=Path(os.environ['DARKSLIDE_API_ORIGINAL']).read_bytes()
        with zipfile.ZipFile(os.environ['DARKSLIDE_IIIX_ZIP']) as z:
            self.rawx=z.read(next(n for n in z.namelist() if n.lower().endswith('fwdc243b.bin')))

    def test_import_both_then_prepare_actual_saved_hdf_projects(self):
        for raw,model,variant in ((self.raw3,models.GR3,'GR III HDF'),(self.rawx,models.GR3X,'GR IIIx HDF')):
            imported=self.app.import_firmware(raw,'renamed.bin')
            self.assertEqual(imported['model'],model.name)
            self.assertEqual(imported['template']['id'],model.id)
            p=self.create(variant,model)
            changes={'8':{'film':'ektar100'},'11':{'film':'xtra400_portraendura'}}
            server.api_put_draft(req(self.app,{'changes':changes}),p['id'])
            server.api_save_project(req(self.app,{}),p['id'])
            with patch.object(generate,'run',side_effect=AssertionError('producer')):
                result=server.api_film_prepare(req(self.app,dict(expected_changes=changes,request_id=model.id)),p['id'])
            self.assertEqual(result['body']['status'],'protected_windows_unchanged')
            self.assertTrue(result['coverage_verified']);self.assertFalse(result['full_image_created'])
            self.assertEqual(self.app.store.builds(),[])
        self.assertEqual(self.app.template['id'],models.GR3.id)

    def test_real_guard_fault_is_rejected_for_both_families(self):
        for raw,model,variant,module in ((self.raw3,models.GR3,'GR III HDF',inplace_generation),(self.rawx,models.GR3X,'GR IIIx HDF',iiix_prepare)):
            self.app.store.put_input(raw);p=self.create(variant,model)
            payload=bytes(fwread.unpack(raw));offset=0x537097b4-0x53000000+0x10870
            expected=payload[offset:offset+1]
            plan=dict(windows=(Window(0x537097b4,expected,bytes([expected[0]^1])),),checks=dict(data_verified=True,
                shared_bytes_match_reference=True,complete_window_coverage_verified=True),current_slots=(),layout='fault')
            with patch.object(module,'prepare',return_value=plan),self.assertRaises(server.ApiError) as e:
                server.api_film_prepare(req(self.app,dict(expected_changes={},request_id='fault')),p['id'])
            self.assertEqual(e.exception.status,422)
            self.assertIn('white_balance_factory',str(e.exception))

    def test_concurrent_variant_edit_rejects_result(self):
        self.app.store.put_input(self.raw3);p=self.create();original=inplace_generation.prepare
        def change(*args,**kwargs):
            result=original(*args,**kwargs)
            self.app.store.set_target_body(p['id'],None,template.load(), 'GR III')
            return result
        with patch.object(inplace_generation,'prepare',side_effect=change),self.assertRaises(server.ApiError) as e:
            server.api_film_prepare(req(self.app,dict(expected_changes={},request_id='race')),p['id'])
        self.assertEqual(e.exception.status,409)

from test_http import Served

@unittest.skipUnless(os.environ.get('DARKSLIDE_API_ORIGINAL') and os.environ.get('DARKSLIDE_IIIX_ZIP'), 'official inputs required')
class Transport(Served):
    synthetic_identity = False   # real official inputs: no synthetic test identity

    def test_dual_family_import_and_saved_prepare(self):
        raw3=Path(os.environ['DARKSLIDE_API_ORIGINAL']).read_bytes()
        rawx=Path(os.environ['DARKSLIDE_IIIX_ZIP']).read_bytes()
        projects=[]
        with patch.object(generate,'run',side_effect=AssertionError('producer')),patch.object(self.store,'put_build',side_effect=AssertionError('output')):
            for blob,filename,model,variant in ((raw3,'renamed.bin',models.GR3,'GR III HDF'),(rawx,'gr3x_v160.zip',models.GR3X,'GR IIIx HDF')):
                r=self.call('POST','/api/firmware',blob,{'X-Filename':filename})
                self.assertEqual(r.status,200,r.body);self.assertEqual(r.json()['model'],model.name)
                r=self.call('POST','/api/projects',dict(input_sha256=model.input_sha,title='HTTP',camera_variant=variant))
                self.assertEqual(r.status,200,r.body);p=r.json();projects.append(p)
                changes={'8':{'film':'gold200'}}
                r=self.call('PUT','/api/projects/'+p['id']+'/draft',dict(changes=changes));self.assertEqual(r.status,200,r.body)
                self.assertEqual(r.json()['capabilities']['model'],model.name)
                self.assertEqual(self.call('POST','/api/projects/'+p['id']+'/save',{}).status,200)
                r=self.call('POST','/api/projects/'+p['id']+'/film-prepare',dict(expected_changes=changes,request_id='http'))
                self.assertEqual(r.status,200,r.body);self.assertEqual(r.json()['body']['status'],'protected_windows_unchanged')
                # Since 1.0 GR III films are generated for real, so /generate is no longer a 422 here; what this
                # test guards is that the read-only check produced no output at all.
                self.assertEqual(self.store.builds(),[])
            for p in reversed(projects):
                r=self.call('GET','/api/projects/'+p['id']);self.assertEqual(r.json()['template_id'],p['template_id'])
