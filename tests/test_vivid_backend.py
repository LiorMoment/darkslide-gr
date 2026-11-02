"""Five-slot code resource and bounded sparse planning; no complete image creation."""
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from darkslide import film_vivid_fifth as five
from darkslide import film_precompiled as compiled
from darkslide import film_inplace_cache, gr3x_inplace, film_profiles, fwread, models, fwaddr

class FiveCode(unittest.TestCase):
    def test_both_families_all_vivid_masks_without_compiler(self):
        with patch('darkslide.film_arm_assemble.assemble',side_effect=AssertionError('compiler forbidden')):
            for family in five.FAMILIES:
                model={'GRIII-2.10':models.GR3,'GRIIIx-1.60':models.GR3X,
                       'GRIII-2.20':models.GR3_220,'GRIIIx-1.70':models.GR3X_170}[family]
                with fwaddr.firmware(model):
                    start,end=fwaddr.A(five.CODE)[0]
                    self.assertEqual((start,end),(0x53dc3350,0x53dc3980) if models.is_220(model) else (0x53dc3218,0x53dc3848))
                    for mask in range(16,32):
                        with self.subTest(family=family,mask=mask):
                            addresses,code=five.build_code(mask,family)
                            self.assertEqual(sum(map(len,code.values())),1184)
                            for name,blob in code.items():
                                self.assertTrue(start<=addresses[name]<addresses[name]+len(blob)<=end)
                            self.assertEqual(set(addresses),set(code))

    def test_only_vivid_masks_and_known_families_allowed(self):
        for mask in (-1,0,1,15,32,True,'16'):
            with self.assertRaises(ValueError):five.build_code(mask,'GRIII-2.10')
        with self.assertRaises(ValueError):five.build_code(16,'unknown')

    def test_new_resource_missing_or_corrupt_never_falls_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bundle.json'
            with patch.object(compiled,'VIVID_BUNDLE',path),patch('darkslide.film_arm_assemble.assemble',side_effect=AssertionError('compiler forbidden')):
                with self.assertRaises(ValueError):five.build_code(16,'GRIII-2.10')
                path.write_bytes(b'{}')
                with self.assertRaises(ValueError):five.build_code(31,'GRIIIx-1.60')

    def test_new_shared_tables_do_not_move_old_records_or_overlap_code(self):
        old=film_inplace_cache.data_layout()[0]
        new,lookup,post=five.data_layout()
        for a,b in zip(old,new[:4]):
            for field in ('slot','record','matrix','knots'):
                self.assertEqual(tuple(a[field]) if field=='knots' else a[field],tuple(b[field]) if field=='knots' else b[field])
        self.assertEqual(lookup+80,post)
        self.assertEqual(post+120,0x53dc3a40)
        self.assertEqual(new[4]['record'],five.CODE[0][1])
        self.assertEqual(new[4]['knots'][-1]+66+2,lookup)


@unittest.skipUnless(os.environ.get('DARKSLIDE_VIVID_GRIII') and os.environ.get('DARKSLIDE_VIVID_IIIX'), 'official originals required for sparse planning')
class FiveSparse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=[]
        for key,model,module in [('DARKSLIDE_VIVID_GRIII',models.GR3,film_inplace_cache),('DARKSLIDE_VIVID_IIIX',models.GR3X,gr3x_inplace)]:
            cls.cases.append((model,module,fwread.unpack(Path(os.environ[key]).read_bytes())))

    def test_all_thirty_two_masks_and_factory_records_not_overwritten(self):
        for model,module,raw in self.cases:
            profile=film_profiles.load_profile('gold200',model.id)
            for mask in range(32):
                slots=[s for i,s in enumerate((8,9,10,11,2)) if mask&(1<<i)]
                edits=[dict(kind='film',slot=s,film='gold200') for s in slots]
                plan=module.plan_offline(raw,edits,{'gold200':profile})
                self.assertEqual(plan['mask'],mask)
                self.assertEqual(plan['layout'],five.LAYOUTS['GRIII-2.10' if model==models.GR3 else 'GRIIIx-1.60'] if mask&16 else module.LAYOUT)
                self.assertFalse(plan['full_image_created'])
                # Native Vivid record/matrix remain intact for secondary consumers.
                protected=((0x550196d8,84),(0x53dc26dc,18),(0x55018d94+17*4,4),(0x55018d94+25*4,4))
                for w in plan['windows']:
                    for at,n in protected:
                        self.assertTrue(w.address+len(w.replacement)<=at or w.address>=at+n)
                if mask&16:
                    self.assertEqual(plan['code_bytes'],1184)
                    for w in plan['windows']:
                        # Old shared lookup/post kept original under five-slot layout.
                        for at,n in ((0x53dc2bf8+416,64),(0x53dc2bf8+1272,96)):
                            self.assertTrue(w.address+len(w.replacement)<=at or w.address>=at+n)
