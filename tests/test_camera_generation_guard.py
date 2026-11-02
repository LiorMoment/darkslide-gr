"""Synthetic tests of proposed temporary output refusal; no firmware or photos."""
import unittest
import itertools
from unittest.mock import patch
from darkslide import camera_features,generate,gr3x_generation,body_target,menu_order

class ReachedFurther(Exception):pass

class TestGate(unittest.TestCase):
 def test_wide_only_allowed(self):
  self.assertIsNone(camera_features.refusal({'wide_ratio':True}))
 def test_date_combo_refused(self):
  msg=camera_features.refusal({'wide_ratio':True,'soft_glow':True,'date_stamp':True})
  self.assertIsInstance(msg,str);self.assertTrue(msg)
 def test_soft_only_remains_available(self):
  self.assertIsNone(camera_features.refusal({'soft_glow':True}))
 def test_false_values_remain_available(self):
  self.assertIsNone(camera_features.refusal({'wide_ratio':False,'date_stamp':False}))
 def test_editing_and_validity_unaffected(self):
  x={'wide_ratio':True,'soft_glow':True,'date_stamp':True}
  self.assertEqual(camera_features.clean(x),x)
 def test_gr3_date_rejected_before_edit_planning(self):
  for f in ({'date_stamp':True},{'wide_ratio':True,'soft_glow':True,'date_stamp':True}):
   with self.subTest(features=f),patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
    with self.assertRaises(generate.GenerateError):generate._run(None,{'id':'synthetic','changes':{},'camera_features':f},{},b'')
 def test_gr3_soft_reaches_next_step_without_building(self):
  with patch.object(camera_features,'edits',side_effect=ReachedFurther('reached edit planning')):
   with self.assertRaises(ReachedFurther):generate._run(None,{'id':'synthetic','changes':{},'camera_features':{'soft_glow':True}},{},b'')
 def test_gr3x_date_rejected_before_body_or_compositor(self):
  for f in ({'date_stamp':True},{'wide_ratio':True,'soft_glow':True,'date_stamp':True}):
   with self.subTest(features=f),patch.object(body_target,'of',side_effect=ReachedFurther('reached body')):
    with self.assertRaises(generate.GenerateError):gr3x_generation._run(None,{'id':'synthetic','changes':{},'camera_features':f},{},b'',None,False)
 def test_gr3x_soft_reaches_next_step_without_building(self):
  with patch.object(menu_order,'normalize',side_effect=ReachedFurther('reached menu order')):
   with self.assertRaises(ReachedFurther):gr3x_generation._run(None,{'id':'synthetic','changes':{},'camera_features':{'soft_glow':True}},{},b'',None,False)


class Matrix(unittest.TestCase):
 def test_all_boolean_combinations(self):
  for soft,wide,date in itertools.product((False,True),repeat=3):
   with self.subTest(soft=soft,wide=wide,date=date):
    x={'soft_glow':soft,'wide_ratio':wide,'date_stamp':date}
    msg=camera_features.refusal(x)
    if date:
     self.assertIsInstance(msg,str);self.assertIn('更新/恢复',msg)
    else:self.assertIsNone(msg)
 def test_unsupported_still_refused(self):
  saved=camera_features.WRITTEN
  try:
   camera_features.WRITTEN=tuple(x for x in saved if x!='soft_glow')
   self.assertIn('没有接进',camera_features.refusal({'soft_glow':True}))
  finally:camera_features.WRITTEN=saved
 def test_clean_still_rejects_bad_types(self):
  for x in ({'wide_ratio':1},{'wide_ratio':'yes'}, {'unknown':True}, {'date_stamp':True}):
   with self.subTest(value=x),self.assertRaises(camera_features.FeatureError):camera_features.clean(x)


if __name__ == "__main__":
 unittest.main()
