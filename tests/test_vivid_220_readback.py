"""New five-slot reader contracts; actual originals are in the sparse audit."""
import hashlib,json,unittest
from unittest.mock import patch
from darkslide import film_vivid_fifth_readback as reader,film_inplace_complete as complete
from darkslide import film_inplace_integrity as gr3,gr3x_inplace_integrity as gr3x,fwaddr,models

class VividNewReader(unittest.TestCase):
 def test_reference_identity_and_independent_size_partition(self):
  raw=reader.REFERENCE_220.read_bytes()
  self.assertEqual(hashlib.sha256(raw).hexdigest(),reader.REFERENCE_220_SHA256)
  families=json.loads(raw)['families'];self.assertEqual(set(families),{'GRIII-2.20','GRIIIx-1.70'})
  for family,cases in families.items():
   self.assertEqual(set(cases),{str(m) for m in range(16,32)})
   for roles in cases.values():
    covered=set()
    for row in roles.values():
     a=row['address'];b=bytes.fromhex(row['hex']);self.assertEqual(a%4,0)
     self.assertTrue(0x53dc3350<=a<a+len(b)<=0x53dc3980)
     occupied=set(range(a,a+len(b)));self.assertFalse(occupied&covered);covered|=occupied
    self.assertEqual(len(covered),1184);self.assertEqual(max(covered)+1,0x53dc37f0)

 def test_complete_preserves_explicit_identity_or_auto_detection(self):
  edits=[dict(kind='film',slot=2,film='portra160')];read=lambda a,n:b''
  for model,layout in [(models.GR3_220,'inplace_history_cache_exit_vivid5_220_v1'),(models.GR3X_170,'gr3x170_inplace_cache_exit_vivid5_v1')]:
   with patch.object(reader,'readback_reader',return_value=({'layout':layout},())) as call:
    complete.readback_reader(b'',read,edits,model_id=model.id,layout=layout)
    call.assert_called_once_with(b'',read,edits,None,layout=layout,model_id=model.id)
  with patch.object(reader,'readback_reader',return_value=({},())) as call:
   complete.readback_reader(b'',read,edits)
   call.assert_called_once_with(b'',read,edits,None,layout=None,model_id=None)

 def test_new_integrity_dispatch_and_wrong_model_close(self):
  for module,layout,model in [(gr3,'inplace_history_cache_exit_vivid5_220_v1',models.GR3_220),(gr3x,'gr3x170_inplace_cache_exit_vivid5_v1',models.GR3X_170)]:
   with patch.object(reader,'verify',return_value={'layout':layout}) as call:
    module.verify(b'',(),{2:'portra160'},{},layout=layout)
    call.assert_called_once_with(b'',(),{2:'portra160'},{},layout=layout)
   with patch.object(reader,'verify_project',return_value={'layout':layout}) as call:
    module.verify_project(b'',(),{'2':{'film':'portra160'}},{},planned_layout=layout,checked_layout=layout)
    self.assertEqual(call.call_count,1)
   with self.assertRaisesRegex(reader.ReadbackError,'model/layout'):
    reader.expected_bytes(b'',{2:'portra160'},{},layout,model_id=models.GR3.id)
  with self.assertRaisesRegex(reader.ReadbackError,'App layout'):
   reader.verify_project(b'',(),{'2':{'film':'portra160'}},{},planned_layout='inplace_history_cache_exit_vivid5_220_v1',checked_layout='inplace_history_cache_exit_vivid5_v1')

 def test_physical_mapping_does_not_use_ambient_relocator(self):
  for model in models.ALL:
   with fwaddr.firmware(model):
    self.assertEqual(reader.offset(0x550196e0,84,59798532),0x550196e0-0x55000000+0x5407b640-0x53000000+0x10870)
    self.assertEqual(reader.offset(0x550196d8,84,59718388),0x550196d8-0x55000000+0x5407b540-0x53000000+0x10870)
    with self.assertRaises(reader.ReadbackError):reader.offset(0x5407b640,4,59798532)

 def test_positive_branch_not_confused_with_old_vivid_address(self):
  self.assertEqual(reader.NEW_POINTS[0x536db808],0x536db7e0)
  self.assertNotEqual(reader.NEW_POINTS[0x536db808],0x536db808)
  self.assertEqual(reader.NEW_POINTS[0x53d36400],0x53d35de0)
