"""Unified desktop flows; synthetic DNG and sparse checks, never full generation."""
import copy, hashlib, io, json, os, struct, tempfile, unittest, zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from darkslide import server,models,film_profiles as F,photo_recipe as R,photo_recipe_dng as D
from darkslide import film_fit,base_curve,gr3x_camera_preview as P,gr3x_factory_envelope as E,legacy_import as L
from darkslide.store import Store,StoreError,write_json

CUBE=b'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n'

def request(app,body=None,data=b''):
    return SimpleNamespace(app=app,store=app.store,headers={},read_json=lambda:body,read_body=lambda:data)

def dng(packet):
    raw=bytearray(1024);raw[:8]=b'II'+struct.pack('<HI',42,8)
    entries=[(272,2,14,128),(50706,1,4,0x00000401)]
    struct.pack_into('<H',raw,8,len(entries))
    for i,row in enumerate(entries):struct.pack_into('<HHII',raw,10+12*i,*row)
    raw[128:142]=b'RICOH GR IIIx\0\0'
    return D.embed(bytes(raw),packet)


class Desktop(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=server.App(self.tmp.name)
        with patch.object(self.app.store,'has_input',return_value=True):
            self.pid=self.app.store.create_project('IIIx',models.GR3X.input_sha,models.template_for(models.GR3X),camera_variant='GR IIIx')['id']
            self.gr3=self.app.store.create_project('III',models.GR3.input_sha,models.template_for(models.GR3))['id']
        self.profile=F.load_profile('gold200',models.GR3X.id)
        self.packet=R.encode(self.profile,8)

    def test_recipe_import_save_export_and_original_independence(self):
        restored=server.api_import_photo_recipe(request(self.app,data=dng(self.packet)),self.pid)
        rid=restored['id'];self.assertEqual(restored['curves_u14'],self.profile['curves_u14'])
        self.assertIn('SPEKTRAFILM_LICENSE.txt',restored['data_notices'])
        self.app.store.put_draft(self.pid,{'8':{'film':rid}},models.template_for(models.GR3X))
        self.app.store.save(self.pid,models.template_for(models.GR3X))
        with patch.object(F,'load_profile',side_effect=AssertionError('current catalog lookup')):
            got=self.app.store.film_lut_profiles(self.pid,[rid])[rid]
            self.assertEqual(got['curves_u14'],self.profile['curves_u14'])
            response=server.api_photo_recipe_export(request(self.app,{'cube':CUBE.decode(),'grade':{}}),self.pid,rid)
        with zipfile.ZipFile(io.BytesIO(response.body)) as z:
            self.assertIn('film_data/source-notices/SPEKTRAFILM_LICENSE.txt',z.namelist())
            self.assertEqual(R.decode(z.read('film_data/restored-profile.dsfr'))['profile']['curves_u14'],self.profile['curves_u14'])
        with self.assertRaises(server.ApiError):server.api_import_photo_recipe(request(self.app,data=dng(self.packet)),self.gr3)
        self.assertEqual(self.app.store.builds(),[])

    def test_export_roundtrip_preserves_exact_packet_identity_and_notices(self):
        restored = server.api_import_photo_recipe(request(self.app, data=dng(self.packet)), self.pid)
        rid = restored['id']
        for _ in range(3):
            with patch.object(R, 'encode', side_effect=AssertionError('must export original bytes')):
                response = server.api_photo_recipe_export(
                    request(self.app, {'cube': CUBE.decode(), 'grade': {'exp': 10}}), self.pid, rid)
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                packet = archive.read('film_data/restored-profile.dsfr')
                self.assertEqual(packet, self.packet)
                self.assertEqual(json.loads(archive.read('film_data/profile.json')), restored)
                for name, contents in restored['data_notices'].items():
                    self.assertEqual(archive.read('film_data/source-notices/' + name).decode(), contents)
                self.assertIn('"exp": 10', archive.read('film_data/CHANGELOG.txt').decode())
            reopened = server.api_import_photo_recipe(request(self.app, data=dng(packet)), self.pid)
            self.assertEqual(reopened, restored)
            self.assertEqual(reopened['photo_snapshot']['original_id'], 'gold200')
            self.assertEqual(len(self.app.store.list_photo_recipes(self.pid)), 1)

    def test_export_revalidates_packet_and_model_before_returning_bytes(self):
        restored = self.app.store.put_photo_recipe(self.pid, self.packet)
        rid = restored['id']
        path = self.app.store._dir(self.pid) / 'photo_recipes' / (rid + '.dsfr')
        other = R.encode(F.load_profile('5203', models.GR3X.id), 8)
        for invalid in (self.packet[:-1], other):
            path.write_bytes(invalid)
            with self.assertRaises(StoreError):
                server.api_photo_recipe_export(request(self.app, {'cube': CUBE.decode()}), self.pid, rid)
        path.unlink()
        with self.assertRaises(StoreError):
            server.api_photo_recipe_export(request(self.app, {'cube': CUBE.decode()}), self.pid, rid)
        with self.assertRaises(server.ApiError):
            server.api_photo_recipe_export(request(self.app, {'cube': CUBE.decode()}), self.gr3, rid)

    def test_corrupt_or_missing_recipe_rejected_and_no_candidate_routes(self):
        for data in (b'not dng',dng(self.packet)[:-4]):
            with self.assertRaises(server.ApiError):server.api_import_photo_recipe(request(self.app,data=data),self.pid)
        self.assertFalse(any('photo-recipe-check' in str(route) for route in server.ROUTES))
        with patch.object(server.generate,'run',side_effect=AssertionError('producer called')):
            with self.assertRaises(server.ApiError):server.api_generate(request(self.app,{}),self.pid)

    def test_base_model_wrong_identity_and_iiix_settings_rejected(self):
        with self.assertRaises(base_curve.BaseCurveError):base_curve._model(models.GR3X.input_sha,{'key':0})
        with self.assertRaises(film_fit.FilmFitError):film_fit.contract_model({'official_sha256':models.GR3.input_sha},models.GR3X.id)
        self.assertTrue(models.capabilities(models.GR3X)['camera_fit'])
        self.assertFalse(models.capabilities(models.GR3X)['full_firmware'])


class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=Store(self.root/'old');self.dest=Store(self.root/'new')
        raw=b'synthetic original';self.model=replace(models.GR3X,input_sha=L.digest(raw),input_size=len(raw))
        self.addCleanup(patch.stopall)
        patch.object(models,'GR3X',self.model).start();patch.object(models,'ALL',(models.GR3,self.model)).start()
        self.source.put_input(raw)
        p=self.source.create_project('old',self.model.input_sha,models.template_for(self.model));self.pid=p['id']
        self.source.put_film_lut(self.pid,CUBE,'identity.cube')
        self.bid='b_123456789abc';folder=self.source.root/'builds'/self.bid;folder.mkdir(parents=True)
        (folder/self.model.filename).write_bytes(b'historical output')
        write_json(folder/'manifest.json',dict(build_id=self.bid,project_id=self.pid,input_sha256=self.model.input_sha,output_filename=self.model.filename,output_sha256=L.digest(b'historical output'),checks=[{'ok':True}],features=['name'],created_at='fixture'))

    def test_copy_idempotence_original_preservation_and_history_download(self):
        before=L.tree(self.source.root)
        plan=L.import_store(self.dest,self.source.root)
        self.assertFalse((self.dest.root/'projects').exists())
        result=L.import_store(self.dest,self.source.root,apply=True,expected_digest=plan['source_digest'])
        new=result['projects'][0]['id'];self.assertNotEqual(new,self.pid)
        self.dest.rename_project(new,'edited after migration')
        L.import_store(self.dest,self.source.root,apply=True,expected_digest=plan['source_digest'])
        self.assertEqual(self.dest.get_project(new)['title'],'edited after migration')
        self.assertEqual(L.tree(self.source.root),before)
        app=SimpleNamespace(store=self.dest)
        response=server.api_build_firmware(request(app),result['builds'][0]['id'])
        self.assertEqual(response.body,b'historical output')
        self.assertIn(self.model.filename,str(response.headers))
        self.assertEqual(len(self.dest.builds()),1)

    def test_imported_build_with_uncertain_or_held_features_stays_stored_but_cannot_download(self):
        source_manifest = self.source.root / 'builds' / self.bid / 'manifest.json'
        bad_list = '保存的构建缺少可识别的功能清单，暂时不能下载固件'
        held = '保存的构建包含尚未完成更新和恢复验证的功能，暂时不能下载固件'
        cases = ((None, bad_list), ([], bad_list), (['date_stamp'], held), (['name', 'date_stamp'], held))
        for index, (features, message) in enumerate(cases):
            with self.subTest(features=features):
                record = json.loads(source_manifest.read_text())
                if features is None:
                    record.pop('features', None)
                else:
                    record['features'] = features
                write_json(source_manifest, record)
                before = L.tree(self.source.root)
                destination = Store(self.root / ('blocked_%d' % index))
                plan = L.import_store(destination, self.source.root)
                result = L.import_store(destination, self.source.root, apply=True,
                                        expected_digest=plan['source_digest'])
                self.assertEqual(L.tree(self.source.root), before)
                imported = destination.build_path(result['builds'][0]['id'], 'manifest.json')
                saved_record = json.loads(imported.read_text())
                self.assertEqual(saved_record.get('features'), features)
                self.assertEqual('features' in saved_record, features is not None)
                self.assertEqual(saved_record['build_id'], result['builds'][0]['id'])
                self.assertEqual(saved_record['project_id'], result['projects'][0]['id'])
                saved_bytes = imported.parent / self.model.filename
                self.assertEqual(saved_bytes.read_bytes(), b'historical output')
                with self.assertRaises(server.ApiError) as error:
                    server.api_build_firmware(request(SimpleNamespace(store=destination)), result['builds'][0]['id'])
                self.assertEqual((error.exception.status, str(error.exception)), (422, message))
                self.assertEqual(saved_bytes.read_bytes(), b'historical output')
                self.assertEqual(json.loads(imported.read_text()), saved_record)

    def test_changed_source_conflict_symlink_and_wrong_build_refused(self):
        plan=L.import_store(self.dest,self.source.root)
        self.source.rename_project(self.pid,'changed')
        with self.assertRaises(StoreError):L.import_store(self.dest,self.source.root,apply=True,expected_digest=plan['source_digest'])
        plan=L.import_store(self.dest,self.source.root)
        target=self.dest.root/'projects'/plan['projects'][0]['id'];target.mkdir(parents=True)
        with self.assertRaises(StoreError):L.import_store(self.dest,self.source.root,apply=True,expected_digest=plan['source_digest'])
        link=self.source.root/'projects'/self.pid/'link';link.symlink_to(self.root/'new')
        with self.assertRaises(StoreError):L.import_store(self.dest,self.source.root)
        link.unlink()
        (self.source.root/'builds'/self.bid/self.model.filename).write_bytes(b'changed')
        with self.assertRaises(StoreError):L.import_store(self.dest,self.source.root)


@unittest.skipUnless(os.environ.get('DARKSLIDE_DESKTOP_IIIX'),'optional readonly IIIx original')
class OfficialDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=Path(os.environ['DARKSLIDE_DESKTOP_IIIX']);cls.contract=base_curve.base_curve(cls.path)
        cls.limits=E.factory_envelope(cls.path)

    def test_actual_fit_preview_source_binding_and_sparse_prepare(self):
        with tempfile.TemporaryDirectory() as folder:
            app=server.App(folder);app.store.put_input(self.path.read_bytes())
            pid=app.store.create_project('test',models.GR3X.input_sha,models.template_for(models.GR3X),camera_variant='GR IIIx')['id']
            lid=app.store.put_film_lut(pid,CUBE,'identity.cube')['id'];req=request(app)
            with patch.object(server,'_camera_model',return_value=self.contract):
                result=server._fit_and_keep(req,pid,lid)
                self.assertTrue(result['fit']['current_install']['numeric_installable'])
                profile=app.store.film_lut_profiles(pid,[lid])[lid]
                self.assertEqual(profile['model'],'GR IIIx')
                self.assertEqual(server.api_film_lut_camera(req,pid,lid).body,P.preview(self.contract['values'],profile,self.limits))
                for ident in F.ready_profile_ids():
                    self.assertEqual(server.api_film_camera(req,pid,ident).body,P.preview(self.contract['values'],F.load_profile(ident,models.GR3X.id),self.limits))
                packet=R.encode(F.load_profile('gold200',models.GR3X.id),9)
                saved=server.api_import_photo_recipe(request(app,data=dng(packet)),pid)
                self.assertEqual(server.api_photo_recipe_camera(req,pid,saved['id']).body,film_fit.camera_cube(self.contract['values'],saved,exact=True))
            changes={'8':{'film':lid},'9':{'film':saved['id']},'10':{'film':'5203'}}
            app.store.put_draft(pid,changes,models.template_for(models.GR3X));app.store.save(pid,models.template_for(models.GR3X))
            report=server.api_film_prepare(request(app,dict(expected_changes=changes,request_id='desktop')),pid)
            self.assertTrue(report['data_verified']);self.assertFalse(report['full_image_created'])
            index=app.store.film_luts(pid);index[lid]['profile']['provenance']['official_sha256']=models.GR3.input_sha
            write_json(app.store._dir(pid)/'film_luts.json',index)
            with self.assertRaises(StoreError):app.store.film_lut_profiles(pid,[lid])
            self.assertEqual(app.store.builds(),[])

# Exercise the real HTTP adapter, in addition to direct route tests above.
from test_http import Served
class DesktopHTTP(Served):
    def test_recipe_routes_and_family_isolation(self):
        with patch.object(self.store,'has_input',return_value=True):
            pid=self.store.create_project('IIIx',models.GR3X.input_sha,models.template_for(models.GR3X))['id']
        route='/api/projects/'+pid+'/photo-recipes'
        packet=R.encode(F.load_profile('gold200',models.GR3X.id),8)
        profile=self.assertJson(self.call('POST',route,dng(packet)),200)
        result=self.assertJson(self.call('GET',route),200)
        self.assertEqual(result['recipes'][0]['id'],profile['id'])
        exported=self.call('POST',route+'/'+profile['id']+'/export',dict(cube=CUBE.decode(),grade={}))
        self.assertEqual(exported.status,200)
        with zipfile.ZipFile(io.BytesIO(exported.body)) as z:
            original = z.read('film_data/restored-profile.dsfr')
            self.assertEqual(original, packet)
        reopened = self.assertJson(self.call('POST', route, dng(original)), 200)
        self.assertEqual(reopened['id'], profile['id'])
        self.assertEqual(reopened['photo_snapshot']['original_id'], 'gold200')
        self.assertEqual(self.call('POST',route,b'corrupt').status,422)
        gr3=self.new_project()
        self.assertEqual(self.call('GET','/api/projects/'+gr3+'/photo-recipes').status,409)
        self.assertEqual(self.call('POST','/api/legacy-import',{'apply':True}).status,400)
        with patch.object(server.generate,'run',side_effect=AssertionError('producer')):
            self.assertEqual(self.call('POST','/api/projects/'+pid+'/generate',{}).status,422)

class HistoricalRecipes(unittest.TestCase):
    setUp = Desktop.setUp
    def test_sixteen_current_and_eight_previous_recipes_in_unified_store(self):
        legacy=Path(__file__).parent/'fixtures/film_legacy_v1'
        notices={n:(legacy/n).read_text() for n in ('README.md','SPEKTRAFILM_LICENSE.txt','CHANGELOG.txt')}
        profiles=[F.load_profile(n,models.GR3X.id) for n in F.ready_profile_ids()]
        for file in sorted((legacy/'camera').glob('*.json')):
            old=json.loads(file.read_text());old.update(model=models.GR3X.name,firmware=models.GR3X.firmware,data_notices=notices)
            self.assertNotEqual(old['curves_u14'],F.load_profile(old['id'],models.GR3X.id)['curves_u14'])
            profiles.append(old)
        self.assertEqual(len(profiles),24)
        for profile in profiles:
            for slot in range(8,12):
                packet=R.encode(profile,slot)
                with patch.object(F,'load_profile',side_effect=AssertionError('current catalog used for recovery')):
                    restored=server.api_import_photo_recipe(request(self.app,data=dng(packet)),self.pid)
                    reopened=Store(self.tmp.name).film_lut_profiles(self.pid,[restored['id']])[restored['id']]
                    for field in R.FIELDS:self.assertEqual(reopened[field],profile[field])
                    if 'data_notices' in profile:self.assertEqual(reopened['data_notices'],profile['data_notices'])
