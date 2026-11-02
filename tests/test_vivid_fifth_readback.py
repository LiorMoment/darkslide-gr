"""Reader adapters and artifact boundaries; no camera image generation."""
import hashlib
import json
import unittest
from unittest.mock import patch
from darkslide import film_vivid_fifth_readback as fifth
from darkslide import film_inplace_integrity as gr3,gr3x_inplace_integrity as gr3x,film_inplace_complete as complete

class FifthReaderContracts(unittest.TestCase):
    def test_private_reference_identity_and_bounds(self):
        raw=fifth.REFERENCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),fifth.REFERENCE_SHA256)
        families=json.loads(raw)['families']
        self.assertEqual(set(families),{'GRIII-2.10','GRIIIx-1.60'})
        for cases in families.values():
            self.assertEqual(set(cases),{str(x) for x in range(16,32)})
            for roles in cases.values():
                owned=set()
                for row in roles.values():
                    a=row['address'];b=bytes.fromhex(row['hex'])
                    self.assertEqual(a%4,0);self.assertEqual(len(b)%4,0)
                    self.assertTrue(0x53dc3218<=a<a+len(b)<=0x53dc3848)
                    occupied=set(range(a,a+len(b)));self.assertFalse(owned&occupied);owned|=occupied
                self.assertEqual(len(owned),1184)

    def test_integrity_dispatches_by_layout_and_retains_matching(self):
        for module,layout in ((gr3,'inplace_history_cache_exit_vivid5_v1'),(gr3x,'gr3x160_inplace_cache_exit_vivid5_v1')):
            with patch.object(fifth,'verify',return_value={'sentinel':True}) as called:
                self.assertEqual(module.verify(b'',(),{2:'portra160'},{},layout=layout),{'sentinel':True})
                called.assert_called_once_with(b'',(),{2:'portra160'},{},layout=layout)
            with patch.object(fifth,'verify_project',return_value={'sentinel':True}) as called:
                module.verify_project(b'',(),{'2':{'film':'portra160'}},{},planned_layout=layout,checked_layout=layout)
                self.assertEqual(called.call_count,1)
        with self.assertRaises(ValueError):
            fifth.verify_project(b'',(),{'2':{'film':'portra160'}},{},planned_layout='inplace_history_cache_exit_vivid5_v1',checked_layout='inplace_history_cache_exit_v29')

    def test_complete_uses_actual_reader_for_fifth_slot(self):
        edits=[dict(kind='film',slot=2,film='portra160')]
        read=lambda at,n:b''
        with patch.object(fifth,'readback_reader',return_value=({'sentinel':True},())) as called:
            self.assertEqual(complete.readback_reader(b'',read,edits),({'sentinel':True},()))
            called.assert_called_once_with(b'',read,edits,None,layout=None,model_id=None)

    def test_malformed_inputs_close_without_planner(self):
        for edits in (None,[],[dict(kind='film',slot=True,film='portra160')],[dict(kind='film',slot=2,film='')]):
            with self.subTest(edits=edits),self.assertRaises(fifth.ReadbackError):
                fifth.readback_reader(b'',lambda a,n:b'',edits)
        with self.assertRaises(fifth.ReadbackError):fifth.offset(0x5407b540,4,60000000)
        with self.assertRaises(fifth.ReadbackError):fifth.offset(True,4,60000000)
