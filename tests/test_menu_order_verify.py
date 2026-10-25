import struct
import unittest
from darkslide import menu_order_verify as v


class MenuOrderReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Synthetic bytes only; this is not a modified camera firmware.
        b = bytearray(0xB6C000)
        for off in v.STILL_OFFSETS:
            b[off:off+12] = bytes([11,12,13,14,15,16,22,17,18,19,20,21])
        for off in v.VIDEO_OFFSETS:
            b[off:off+10] = bytes([11,12,13,15,16,22,17,18,19,21])
        b[v.RAW_OFFSET:v.RAW_OFFSET+12] = bytes(range(1,13))
        cls._checksum(b)
        cls.original = bytes(b)

    @staticmethod
    def _checksum(b):
        b[-4:] = bytes(4)
        checksum = (-sum(x[0] for x in struct.iter_unpack('<I', b))) & 0xFFFFFFFF
        b[-4:] = struct.pack('<I',checksum)

    def setUp(self):
        self.request = {'schema':1, 'still_slots':[11,10,9,8,7,12,6,5,4,3,2,1],
                        'video_slots':[12,1,2,3,5,6,7,8,9,11]}
        b = bytearray(self.original)
        for off in v.STILL_OFFSETS:
            b[off:off+12] = bytes([21,20,19,18,17,22,16,15,14,13,12,11])
        for off in v.VIDEO_OFFSETS:
            b[off:off+10] = bytes([22,11,12,13,15,16,17,18,19,21])
        b[v.RAW_OFFSET:v.RAW_OFFSET+12] = bytes(range(12,0,-1))
        self.stage = bytes(b)
        self._checksum(b)
        self.final = bytes(b)

    def check(self, data=None, finalized=False):
        return v._compare_sorting_only(self.original, data or self.stage,
                                       self.request, finalized)

    def test_stage(self):
        r=self.check()
        self.assertEqual(r['tables'],7)
        self.assertEqual(r['table_bytes'],78)
        self.assertFalse(r['checksum_verified'])

    def test_finalized(self):
        self.assertNotEqual(self.stage[-4:],self.final[-4:])
        self.assertTrue(self.check(self.final,True)['checksum_verified'])

    def test_finalized_wrong_sum(self):
        with self.assertRaisesRegex(v.MenuOrderError,'checksum'): self.check(self.stage,True)

    def test_finalized_not_stage(self):
        with self.assertRaisesRegex(v.MenuOrderError,'outside'): self.check(self.final)

    def test_invalid_original_checksum(self):
        b=bytearray(self.original);b[-1]^=1
        with self.assertRaisesRegex(v.MenuOrderError,'Original payload checksum'):
            v._compare_sorting_only(bytes(b),self.final,self.request,True)

    def test_noop_both_stages(self):
        req={'schema':1,'still_slots':list(v.DEFAULT_STILL),'video_slots':list(v.DEFAULT_VIDEO)}
        for final in (False,True):
            r=v._compare_sorting_only(self.original,self.original,req,final)
            self.assertEqual(r['changed_table_bytes'],0)

    def test_slot_tone_and_raw_domains(self):
        for off,wrong in ((v.STILL_OFFSETS[0],bytes(self.request['still_slots'])),
                          (v.RAW_OFFSET,bytes([21,20,19,18,17,22,16,15,14,13,12,11])),
                          (v.RAW_OFFSET,bytes(self.request['still_slots']))):
            b=bytearray(self.stage);b[off:off+12]=wrong
            with self.subTest(offset=off,wrong=wrong), self.assertRaises(v.MenuOrderError):
                self.check(bytes(b))

    def test_missing_group_or_raw(self):
        for off,n in [(x,12) for x in v.STILL_OFFSETS]+[(x,10) for x in v.VIDEO_OFFSETS]+[(v.RAW_OFFSET,12)]:
            b=bytearray(self.stage);b[off:off+n]=self.original[off:off+n]
            with self.subTest(offset=off), self.assertRaises(v.MenuOrderError): self.check(bytes(b))

    def test_unauthorized_change_with_valid_checksum(self):
        for off in (0,0x2D333C,0xB6B524,len(self.stage)-8):
            b=bytearray(self.final);b[off]^=1;self._checksum(b)
            with self.subTest(offset=off), self.assertRaisesRegex(v.MenuOrderError,'outside'):
                self.check(bytes(b),True)

    def test_table_only_does_not_claim_global_check(self):
        b=bytearray(self.stage);b[0]^=1
        r=v._compare_tables(self.original,bytes(b),self.request)
        self.assertEqual(r['scope'],'menu_tables_only')
        self.assertNotIn('outside_authorized_spans_unchanged',r)

    def test_types_and_duplicates(self):
        for bad in (True,1.0,'1',13):
            req=dict(self.request);req['still_slots']=[bad]+self.request['still_slots'][1:]
            with self.subTest(value=bad), self.assertRaises(v.MenuOrderError):v.validate_request(req)
        for bad in ([1]*12,list(range(1,12)),list(range(1,14))):
            req=dict(self.request);req['still_slots']=bad
            with self.assertRaises(v.MenuOrderError):v.validate_request(req)
        for schema in (True,1.0,2):
            req=dict(self.request);req['schema']=schema
            with self.assertRaises(v.MenuOrderError):v.validate_request(req)

    def test_video_disallowed_and_fields(self):
        for key in ('video_slots','extra'):
            req=dict(self.request)
            if key=='extra':req[key]=0
            else:req[key]=list(v.DEFAULT_STILL)
            with self.assertRaises(v.MenuOrderError):v.validate_request(req)
        req=dict(self.request);del req['video_slots']
        with self.assertRaises(v.MenuOrderError):v.validate_request(req)

    def test_bad_payload_size_and_stage_flag(self):
        for data in (self.stage[:-1],bytearray(self.stage)):
            with self.assertRaises(v.MenuOrderError):self.check(data)
        with self.assertRaises(v.MenuOrderError):self.check(finalized=1)
        with self.assertRaises(v.MenuOrderError):
            v._compare_tables(b'',b'',self.request)

    def test_public_source_gate(self):
        for fn in (v.verify_tables,v.verify_sorting_only):
            with self.assertRaisesRegex(v.MenuOrderError,'immutable original'):
                fn(self.original,self.stage,self.request)


if __name__ == '__main__':
    unittest.main()
