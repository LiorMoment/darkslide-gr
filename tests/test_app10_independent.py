"""Independent integration checks; all full producers trapped, no output image."""
import copy
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from darkslide import generate, server, film_requests, fwread, models, inplace_generation
from dataclasses import replace

class ClosedRoutes(unittest.TestCase):
    def test_every_selection_and_grade_blocks_before_any_producer(self):
        choices=[{str(s): {"film":"gold200"} for s in range(8,12) if mask & (1<<(s-8))} for mask in range(1,16)]
        choices += [{str(s):{"grade":{"temp":5}},"2":{"name":"Test"}} for s in range(8,12)]
        for changes in choices:
            for switch in (False,True):
                with patch.object(inplace_generation,"production_available",return_value=False), self.subTest(changes=changes,switch=switch), patch.dict(os.environ,{"DARKSLIDE_FILM_WRITER":"1"}), patch.object(film_requests,"FILM_WRITER_AVAILABLE",switch), patch.object(generate,"edits_from_changes",side_effect=AssertionError("legacy entry")) as edits, patch.object(generate.compositor,"build_composite",side_effect=AssertionError("producer")) as producer:
                    with self.assertRaises(generate.GenerateError):
                        generate.run(Mock(),dict(id="p_audit",changes=changes),{},b"never decode")
                    edits.assert_not_called(); producer.assert_not_called()
                    store=Mock(); store.get_project.return_value=dict(id="p_audit",input_sha256="none",changes=changes)
                    req=SimpleNamespace(store=store,app=SimpleNamespace(template={"reviewed":True}))
                    try:
                        response=server.api_generate(req,"p_audit")
                        self.assertEqual(response.status,422)
                    except server.ApiError as exc:
                        self.assertEqual(exc.status,422)
                    store.input_path.assert_not_called(); store.put_build.assert_not_called()
                    edits.assert_not_called(); producer.assert_not_called()

    def test_nonfilm_route_remains_reachable_without_execution(self):
        class Stop(Exception):pass
        with patch.object(generate,"edits_from_changes",side_effect=Stop):
            with self.assertRaises(Stop):
                generate.run(Mock(),dict(id="p_audit",changes={"8":{"name":"Test"}}),{},b"never decode")

    def test_existing_get_contract_retained(self):
        handler, kwargs = server.find_route("GET","/api/projects/p_123456789abc/film-check")
        self.assertIs(handler,server.api_film_check)
        from darkslide import models
        store=Mock(); store.get_project.return_value=dict(input_sha256=models.GR3.input_sha,template_id=models.GR3.id)
        req=SimpleNamespace(store=store)
        with patch.object(server,"_factory_envelope",return_value={"model_id":"fixture"}), patch.object(server.film_fit,"envelope_violations",return_value=[]):
            result=server.api_film_check(req,"p_123456789abc")
        self.assertEqual(set(result),{"model_id","films"}); self.assertEqual(len(result["films"]),16)



import hashlib, json, tempfile
from darkslide.store import Store, StoreError
from darkslide import template as tpl, film_profiles, inplace_generation

class PersistedSources(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name); self.template=tpl.load()
        with patch.object(self.store,'has_input',return_value=True):
            self.pid=self.store.create_project('audit',fwread.V210_INPUT_SHA256,self.template)['id']
        self.cube=b'LUT_3D_SIZE 2\n'+b''.join(('%s %s %s\n'%(r,g,b)).encode() for b in (0,1) for g in (0,1) for r in (0,1))
        self.ident=self.store.put_film_lut(self.pid,self.cube,'audit.cube')['id']
        # Synthetic binding fixture, NOT a fitted identity LUT or fit-quality proof.
        self.profile=film_profiles.load_profile('gold200'); self.profile['id']=self.ident
        self.profile['provenance']=dict(kind='user_lut',fitter='darkslide-film-fit-v2',source_sha256=hashlib.sha256(self.cube).hexdigest(),official_sha256=fwread.V210_INPUT_SHA256,base_model_id='gr3-210-upstream-neutral-stubbed-v2',base_values_sha256='a'*64,target_input_encoding='srgb',target_ev=0.0,mid_index=7.8)
        self.fit=dict(fitter='darkslide-film-fit-v2',base_model_id='gr3-210-upstream-neutral-stubbed-v2',layout='four_curve_v1',envelope=dict(violations=[]))
        self.store.set_film_lut_fit(self.pid,self.ident,self.profile,self.fit,self.cube)
        self.index=self.store._dir(self.pid)/'film_luts.json'
    def save(self,changes):
        self.store.put_draft(self.pid,changes,self.template);return self.store.save(self.pid,self.template)
    def test_existing_save_reopen_delete_contract(self):
        changes={'8':{'film':self.ident},'9':{'film':'gold200'},'10':{'film':'ektar100'},'11':{'film':'xtra400_portraendura'}}
        self.save(changes);again=Store(self.tmp.name)
        self.assertEqual(again.get_project(self.pid)['changes'],changes)
        self.assertEqual(again.film_lut_profiles(self.pid,[self.ident])[self.ident],self.profile)
        with self.assertRaises(StoreError):again.delete_film_lut(self.pid,self.ident)
        modified=copy.deepcopy(changes);modified['8']={'film':'portra400'};self.save(modified)
        self.assertEqual({k:v for k,v in again.get_project(self.pid)['changes'].items() if k!='8'},{k:v for k,v in changes.items() if k!='8'})
        self.assertEqual(again.delete_film_lut(self.pid,self.ident),[])
        self.assertFalse((again._dir(self.pid)/'user_lut_sources.json').exists())
    def test_changed_cube_and_provenance_rejected_after_reopen(self):
        original=self.index.read_bytes()
        for key,value in [('source_sha256','0'*64),('official_sha256','0'*64),('base_model_id','unknown')]:
            data=json.loads(original);data[self.ident]['profile']['provenance'][key]=value;self.index.write_text(json.dumps(data))
            with self.assertRaises(StoreError):Store(self.tmp.name).film_lut_profiles(self.pid,[self.ident])
        self.index.write_bytes(original);self.store.film_lut_path(self.pid,self.ident).write_bytes(self.cube+b'\n')
        with self.assertRaises(StoreError):Store(self.tmp.name).film_lut_profiles(self.pid,[self.ident])
    def test_bake_resolves_only_matching_current_adjustments(self):
        grade={'temp':5};data=json.loads(self.index.read_text());data[self.ident]['bake']={'film':'gold200','grade':grade};self.index.write_text(json.dumps(data))
        changes={'8':{'film':'gold200','grade':grade},'10':{'film':'portra400'}}
        effective=self.store.film_changes_for_generate(self.pid,changes)
        self.assertEqual(effective['8']['film'],self.ident);self.assertEqual(effective['10'],changes['10']);self.assertEqual(changes['8']['film'],'gold200')
        changed=copy.deepcopy(changes);changed['8']['grade']['temp']=6
        with self.assertRaises(StoreError):self.store.film_changes_for_generate(self.pid,changed)
    @unittest.skipUnless(os.environ.get('DARKSLIDE_API_ORIGINAL'),'official original required')
    def test_real_api_reopened_lut_and_bake_partial_slots(self):
        source=Path(os.environ['DARKSLIDE_API_ORIGINAL']);data=json.loads(self.index.read_text());data[self.ident]['bake']={'film':'gold200','grade':{'temp':5}};self.index.write_text(json.dumps(data))
        for changes in ({'8':{'film':self.ident}},{'8':{'film':'gold200','grade':{'temp':5}},'11':{'film':'ektar100'}}):
            self.store.set_target_body(self.pid,'GR III',self.template);self.save(changes);store=Store(self.tmp.name)
            req=SimpleNamespace(store=store,app=SimpleNamespace(template=self.template),read_json=lambda:dict(expected_changes=copy.deepcopy(changes),request_id='real'))
            with patch.object(store,'input_path',return_value=source),patch.object(store,'put_build',side_effect=AssertionError('output')),patch.object(generate,'run',side_effect=AssertionError('generation')):
                result=server.api_film_prepare(req,self.pid)
            self.assertIs(result['data_verified'],True);self.assertIs(result['coverage_verified'],True)
            self.assertIs(result['production_approved'],False);self.assertIs(result['full_image_created'],False);self.assertEqual(result['decoded_growth_bytes'],0)
            rows={r['slot']:r for r in result['slots']};self.assertEqual(rows[8]['film'],self.ident);self.assertTrue(rows[9]['factory']);self.assertTrue(rows[10]['factory'])
            self.assertEqual(store.get_project(self.pid)['changes'],changes)

class RealSparse(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('DARKSLIDE_READBACK_ORIGINAL'),'decoded official required')
    def test_all_sixteen_install_masks_actual_integrated_backend(self):
        raw=Path(os.environ['DARKSLIDE_READBACK_ORIGINAL']).read_bytes()
        names=['ektar100','xtra400_portraendura','5219','portra400']
        for mask in range(16):
            changes={str(s):dict(film=names[s-8]) for s in range(8,12) if mask&(1<<(s-8))}
            result=inplace_generation.prepare(raw,dict(input_sha256=fwread.V210_INPUT_SHA256,changes=changes))
            self.assertEqual(result['mask'],mask);self.assertEqual(result['decoded_growth_bytes'],0)
            for k in ('data_verified','shared_bytes_match_reference','complete_window_coverage_verified'):self.assertIs(result['checks'][k],True)
            for k in ('production_approved','exportable','full_image_created','write_gate_open'):self.assertIs(result[k],False)
            self.assertEqual(result['checks']['selected_slots'],[s for s in range(8,12) if str(s) in changes])
            self.assertEqual(result['current_slots'],tuple(dict(slot=s,film=changes.get(str(s),{}).get('film')) for s in (2,8,9,10,11)))

class SnapshotRejection(PersistedSources):
    # Reuse fixture only; inherited checks also exercise independent fresh projects.
    def test_source_changes_during_prepare_rejected(self):
        changes={'8':{'film':self.ident}};self.store.set_target_body(self.pid,'GR III',self.template);self.save(changes)
        req=SimpleNamespace(store=self.store,app=SimpleNamespace(template=self.template),read_json=lambda:dict(expected_changes=changes,request_id='race'))
        def changed(*args,**kwargs):
            data=json.loads(self.index.read_text());data[self.ident]['fit']['extra']='changed';self.index.write_text(json.dumps(data));return {}
        real_sha=hashlib.sha256
        fixture = replace(models.GR3, input_size=len(b"official-placeholder"))
        with patch.object(models, "ALL", (fixture, models.GR3X)),patch.object(models, "GR3", fixture),patch.object(self.store,'input_path') as path,patch.object(server.hashlib,'sha256') as sha,patch.object(server.fwread,'unpack',return_value=b'raw'),patch.object(inplace_generation,'prepare',side_effect=changed):
            path.return_value.read_bytes.return_value=b'official-placeholder'
            digest=Mock();digest.hexdigest.return_value=fwread.V210_INPUT_SHA256
            sha.side_effect=lambda data=b"": digest if data==b'official-placeholder' else real_sha(data)
            # Only mock the official-file identity calculation; cube digest uses identify.sha256.
            with self.assertRaisesRegex(server.ApiError,'来源改变'):server.api_film_prepare(req,self.pid)

    def test_unsaved_or_mismatched_expected_rejected_before_prepare(self):
        self.save({'8':{'film':'gold200'}})
        for body in ({'expected_changes':{},'request_id':'stale'}, {'expected_changes':{},'request_id':'x','profiles':{}}):
            req=SimpleNamespace(store=self.store,app=SimpleNamespace(template=self.template),read_json=lambda:body)
            with patch.object(inplace_generation,'prepare',side_effect=AssertionError('must not prepare')):
                with self.assertRaises(server.ApiError):server.api_film_prepare(req,self.pid)

if __name__=="__main__": unittest.main()
