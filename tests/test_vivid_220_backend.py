"""New-version producer contracts: bounded windows only, never complete images."""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from darkslide import film_vivid_fifth as five
from darkslide import film_precompiled as compiled
from darkslide import film_inplace_cache,gr3x_inplace,film_profiles,fwread,models,fwaddr


class VersionDispatch(unittest.TestCase):
    def test_context_and_family_are_bound(self):
        for family in ('GRIII-2.20','GRIIIx-1.70'):
            with self.assertRaises(ValueError):five.build_code(16,family)
        with fwaddr.firmware(models.GR3_220):
            self.assertEqual(five.family_name('GRIII-2.10'),'GRIII-2.20')
            with self.assertRaises(ValueError):five.build_code(16,'GRIIIx-1.60')
            with self.assertRaises(ValueError):five.build_code(16,'GRIIIx-1.70')
        with fwaddr.firmware(models.GR3X_170):
            self.assertEqual(five.family_name('GRIIIx-1.60'),'GRIIIx-1.70')
            with self.assertRaises(ValueError):five.build_code(16,'GRIII-2.20')
        self.assertEqual(five.family_name('GRIII-2.10'),'GRIII-2.10')

    def test_new_resource_is_separate_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            missing=Path(root)/'missing.json'
            with fwaddr.firmware(models.GR3_220),patch.object(compiled,'VIVID_BUNDLE_220',missing),patch('darkslide.film_arm_assemble.assemble',side_effect=AssertionError('no compiler')):
                with self.assertRaises(ValueError):five.build_code(16,'GRIII-2.20')
                missing.write_bytes(b'{}')
                with self.assertRaises(ValueError):five.build_code(31,'GRIII-2.20')
            # Corrupting the new resource cannot select it for an old model.
            with patch.object(compiled,'VIVID_BUNDLE_220',missing):
                self.assertEqual(sum(map(len,five.build_code(16,'GRIII-2.10')[1].values())),1184)

    def test_data_layout_moves_once_and_stays_inside_old_arena(self):
        old=five.data_layout()
        for model in models.NEW_220:
            with fwaddr.firmware(model):
                rows,lookup,post=five.data_layout()
                self.assertEqual((lookup,post),(0x53dc3ab0,0x53dc3b00))
                self.assertEqual(rows[4]['record'],0x53dc3980)
                self.assertEqual(rows[4]['matrix'],0x53dc39d4)
                self.assertEqual(rows[4]['knots'],(0x53dc39e8,0x53dc3a2a,0x53dc3a6c))
                self.assertEqual(post+120,0x53dc3b78)
                legacy=film_inplace_cache.data_layout()[0]
                for expected,actual,prior in zip(legacy,rows[:4],old[0][:4]):
                    for field in ('record','matrix'):
                        self.assertEqual(actual[field],expected[field])
                        self.assertEqual(actual[field]-prior[field],0x138)
                    self.assertEqual(tuple(actual['knots']),tuple(expected['knots']))
        self.assertEqual(five.data_layout(),old)


@unittest.skipUnless(os.environ.get('DARKSLIDE_GRIII_220') and os.environ.get('DARKSLIDE_GRIIIX_170'),'new official originals required')
class NewSparse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=[]
        for key,model,planner,family in (
            ('DARKSLIDE_GRIII_220',models.GR3_220,film_inplace_cache,'GRIII-2.20'),
            ('DARKSLIDE_GRIIIX_170',models.GR3X_170,gr3x_inplace,'GRIIIx-1.70')):
            source=Path(os.environ[key]).read_bytes()
            if hashlib.sha256(source).hexdigest()==model.payload_sha:raw=source
            else:
                if hashlib.sha256(source).hexdigest()!=model.input_sha:raise ValueError('official file identity')
                raw=bytes(fwread.unpack(source))
            cls.cases.append((model,planner,family,raw))

    def test_all_masks_preserve_native_vivid_positive_negative_and_uninstalled_slots(self):
        aliases={2:(12,),8:(18,22,23),9:(19,),10:(20,24),11:(21,)}
        for model,planner,family,raw in self.cases:
            with fwaddr.firmware(model):
                profiles={'gold200':film_profiles.load_profile('gold200',model.id)}
                for mask in range(32):
                    slots=[s for i,s in enumerate(five.ORDER) if mask&(1<<i)]
                    edits=[dict(kind='film',slot=s,film='gold200') for s in slots]
                    plan=planner.plan_offline(raw,edits,profiles)
                    self.assertEqual(plan['layout'],five.LAYOUTS[family] if mask&16 else planner.LAYOUT)
                    self.assertEqual(plan['decoded_growth_bytes'],0)
                    self.assertFalse(plan['full_image_created'])
                    covered={w.address+i for w in plan['windows'] for i in range(len(w.replacement))}
                    protected=[(0x550196e0,84),(0x53dc2814,18),(0x536db808,4),
                               (0x55018d9c+17*4,4),(0x55018d9c+25*4,4)]
                    for slot in set(five.ORDER)-set(slots):
                        protected.extend((0x55018d9c+4*index,4) for index in aliases[slot])
                    for start,n in protected:self.assertFalse(covered.intersection(range(start,start+n)))
                    if mask&16:
                        self.assertEqual(plan['code_bytes'],1184)
                        branch=next(w for w in plan['windows'] if w.address==0x536db7e0)
                        word=int.from_bytes(branch.replacement,'little');delta=word&0xffffff
                        if delta&0x800000:delta-=1<<24
                        self.assertEqual(branch.address+8+delta*4,0x536dba7c)
                        self.assertEqual(branch.expected,struct.pack('<I',0x0a00003b))
                        self.assertFalse(covered.intersection(range(0x53dc2d30+416,0x53dc2d30+480)))
                        self.assertFalse(covered.intersection(range(0x53dc2d30+1272,0x53dc2d30+1368)))

    def test_family_and_original_mismatch_refused_before_plan(self):
        for model,planner,family,raw in self.cases:
            edits=[dict(kind='film',slot=2,film='gold200')]
            with self.assertRaises(ValueError):planner.plan_offline(raw,edits,{})
            other=models.GR3X_170 if model==models.GR3_220 else models.GR3_220
            with fwaddr.firmware(other):
                with self.assertRaises(ValueError):planner.plan_offline(raw,edits,{})
