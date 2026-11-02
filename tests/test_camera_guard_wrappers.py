"""Exercise public generation wrappers with synthetic inputs and blocked outputs."""
import hashlib,itertools,unittest
from types import SimpleNamespace
from unittest.mock import patch
from darkslide import generate,gr3x_generation,models,body_target,film_profiles,camera_features,menu_order

class ReachedFurther(Exception):pass

class FakePath:
 def exists(self):return True
 def read_bytes(self):return b'abc'

class FakeStore:
 def __init__(self,p):self.p=p
 def get_project(self,pid):return self.p
 def input_path(self,sha):return FakePath()
 def film_changes_for_generate(self,pid,changes):return changes
 def film_luts(self,pid):return {}

class GuardPublicWrappers(unittest.TestCase):
 def flags(self):
  return tuple({'soft_glow':soft,'wide_ratio':wide,'date_stamp':date} for soft,wide,date in itertools.product((False,True),repeat=3) if date)
 def test_gr3_public_run(self):
  for f in self.flags():
   with self.subTest(features=f),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')),patch.object(generate.compositor,'build_composite',side_effect=AssertionError('must not build')):
    with self.assertRaisesRegex(generate.GenerateError,'更新/恢复'):
     generate.run(None,{'id':'fake','changes':{},'camera_features':f},{},b'')
 def test_gr3_inplace_wrapper_with_synthetic_source(self):
  fake=SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(),input_size=3)
  template={'model':'GR III','firmware':'2.10','reviewed':True,'id':'test'}
  for f in self.flags():
   project={'id':'fake','changes':{},'camera_features':f,'template_id':'test','input_sha256':fake.input_sha}
   with self.subTest(features=f),patch.object(models,'GR3',fake),patch.object(body_target,'of',return_value='synthetic'),patch.object(body_target,'require_family',return_value='synthetic'),patch.object(film_profiles,'data_revision',return_value='synthetic'),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')),patch.object(generate.compositor,'build_composite',side_effect=AssertionError('must not build')):
    with self.assertRaisesRegex(generate.GenerateError,'更新/恢复'):
     generate.run_inplace(FakeStore(project),project,template,b'abc')
 def test_gr3x_public_run(self):
  for f in self.flags():
   project={'id':'fake','changes':{},'camera_features':f}
   with self.subTest(features=f),patch.object(models,'for_project',return_value=models.GR3X),patch.object(models,'for_template',return_value=models.GR3X),patch.object(body_target,'of',side_effect=ReachedFurther('reached body')),patch.object(menu_order,'normalize',side_effect=ReachedFurther('reached menu order')),patch.object(gr3x_generation.compositor,'build_composite',side_effect=AssertionError('must not build')):
    with self.assertRaisesRegex(generate.GenerateError,'更新/恢复'):
     gr3x_generation.run(FakeStore(project),project,{},b'')


 def test_gr3_public_run_soft_only_passes_guard(self):
  with patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaises(ReachedFurther):generate.run(None,{'id':'fake','changes':{},'camera_features':{'soft_glow':True}},{},b'')
 def test_gr3_inplace_soft_only_passes_guard(self):
  fake=SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(),input_size=3)
  template={'model':'GR III','firmware':'2.10','reviewed':True,'id':'test'}
  project={'id':'fake','changes':{},'camera_features':{'soft_glow':True},'template_id':'test','input_sha256':fake.input_sha}
  with patch.object(models,'GR3',fake),patch.object(body_target,'of',return_value='synthetic'),patch.object(body_target,'require_family',return_value='synthetic'),patch.object(film_profiles,'data_revision',return_value='synthetic'),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaises(ReachedFurther):generate.run_inplace(FakeStore(project),project,template,b'abc')
 def test_gr3x_public_run_soft_only_passes_guard(self):
  project={'id':'fake','changes':{},'camera_features':{'soft_glow':True}}
  with patch.object(models,'for_project',return_value=models.GR3X),patch.object(models,'for_template',return_value=models.GR3X),patch.object(menu_order,'normalize',side_effect=ReachedFurther('reached menu order')):
   with self.assertRaises(ReachedFurther):gr3x_generation.run(FakeStore(project),project,{},b'')


 def test_gr3_public_run_no_features_passes_guard(self):
  with patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaises(ReachedFurther):generate.run(None,{'id':'fake','changes':{},'camera_features':{}},{},b'')
 def test_gr3_inplace_no_features_passes_guard(self):
  fake=SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(),input_size=3)
  template={'model':'GR III','firmware':'2.10','reviewed':True,'id':'test'}
  project={'id':'fake','changes':{},'camera_features':{},'template_id':'test','input_sha256':fake.input_sha}
  with patch.object(models,'GR3',fake),patch.object(body_target,'of',return_value='synthetic'),patch.object(body_target,'require_family',return_value='synthetic'),patch.object(film_profiles,'data_revision',return_value='synthetic'),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaises(ReachedFurther):generate.run_inplace(FakeStore(project),project,template,b'abc')
 def test_gr3x_public_run_no_features_passes_guard(self):
  project={'id':'fake','changes':{},'camera_features':{}}
  with patch.object(models,'for_project',return_value=models.GR3X),patch.object(models,'for_template',return_value=models.GR3X),patch.object(menu_order,'normalize',side_effect=ReachedFurther('reached menu order')):
   with self.assertRaises(ReachedFurther):gr3x_generation.run(FakeStore(project),project,{},b'')
 def test_gr3_220_inplace_is_blocked_before_build(self):
  fake=SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(),input_size=3)
  template={'model':'GR III','firmware':'2.20','reviewed':True,'id':'test'}
  project={'id':'fake','changes':{},'camera_features':{'date_stamp':True},'template_id':'test','input_sha256':fake.input_sha}
  with patch.object(models,'GR3_220',fake),patch.object(body_target,'of',return_value='synthetic'),patch.object(body_target,'require_family',return_value='synthetic'),patch.object(film_profiles,'data_revision',return_value='synthetic'),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaisesRegex(generate.GenerateError,'更新/恢复'):generate.run_inplace(FakeStore(project),project,template,b'abc')
 def test_gr3x_170_is_blocked_before_build(self):
  project={'id':'fake','changes':{},'camera_features':{'date_stamp':True}}
  with patch.object(models,'for_project',return_value=models.GR3X_170),patch.object(models,'for_template',return_value=models.GR3X_170),patch.object(body_target,'of',side_effect=ReachedFurther('reached body')):
   with self.assertRaisesRegex(generate.GenerateError,'更新/恢复'):gr3x_generation.run(FakeStore(project),project,{},b'')

if __name__=='__main__':unittest.main()
