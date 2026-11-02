"""Root review: real original + bounded overlay reads, never a full changed image."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from darkslide import film_inplace_complete as reader
from darkslide import inplace_generation as planner, film_inplace_integrity as integrity
from darkslide import film_profiles


class BoundedRealReadback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.environ.get('DARKSLIDE_READBACK_ORIGINAL')
        if not source:
            # Not silent: reported as a skip with this reason (the release script runs tests without originals).
            raise unittest.SkipTest('需要 DARKSLIDE_READBACK_ORIGINAL（已解码 GR III 官方原件）；按标准测试流程单独运行')
        cls.raw = Path(source).read_bytes()

    def plan(self, selection, profiles=None):
        changes = {str(s): {'film': ident} for s, ident in selection.items()}
        prepared = planner.prepare(self.raw, {'changes': changes}, film_profiles=profiles)
        overlay = {}
        for w in prepared['windows']:
            at = reader.offset(w.address, len(w.replacement), len(self.raw))
            for i, v in enumerate(w.replacement):
                self.assertNotIn(at+i, overlay)
                overlay[at+i] = v
        edits = [dict(kind='film', slot=s, film=ident) for s, ident in selection.items()]
        return prepared, overlay, edits

    def bounded(self, overlay):
        def read(at, n):
            self.assertIs(type(at), int)
            self.assertTrue(0 < n <= 512)
            self.assertTrue(0 <= at < at+n <= len(self.raw)-4)
            return bytes(overlay.get(at+i, v) for i, v in enumerate(self.raw[at:at+n]))
        return read

    def verify(self, overlay, edits, profiles=None):
        checks, spans = reader.readback_reader(self.raw, self.bounded(overlay), edits, profiles)
        self.assertIs(checks['data_verified'], True)
        self.assertIs(checks['shared_bytes_match_reference'], True)
        self.assertIs(checks['complete_window_coverage_verified'], True)
        self.assertIs(checks['production_approved'], False)
        self.assertIs(checks['shared_code_verified'], False)
        self.assertLessEqual(sum(len(v) for _, v in spans), 8192)
        return checks

    def test_all_install_masks_and_single_replacement(self):
        names = ['gold200', 'portra400', 'ektar100', '5219']
        for mask in range(16):
            selected = {s:names[s-8] for s in range(8,12) if mask & (1 << (s-8))}
            with self.subTest(mask=mask):
                p, overlay, edits = self.plan(selected)
                self.verify(overlay, edits)
                for slot in selected:
                    changed = dict(selected); changed[slot] = 'xtra400_portraendura'
                    q, alternate, requests = self.plan(changed)
                    self.verify(alternate, requests)
                    # Independent permitted data addresses: every other byte,
                    # including other rolls and all shared code, stays identical.
                    i=slot-8; base=reader.data.BASE
                    spans=[(base+84*i,84),(base+336+20*i,18),
                           (base+480+198*i,198),(base+416+16*i,16),
                           (base+1272+24*i,24)]
                    spans += [(0x55018d94+4*a,4) for a in reader.data.ALIASES[slot]]
                    allowed={a for start,n in spans for a in range(start,start+n)}
                    offsets = {reader.offset(a, 1, len(self.raw)) for a in allowed}
                    diffs = {a for a in set(overlay)|set(alternate) if overlay.get(a) != alternate.get(a)}
                    self.assertTrue(diffs <= offsets, sorted(diffs-offsets)[:5])
                    self.assertEqual(len(overlay), len(alternate))

    def test_shared_and_curve_corruption_rejected(self):
        p, overlay, edits = self.plan({8:'gold200', 10:'ektar100'})
        data_addresses = integrity.data_addresses({8:'gold200',10:'ektar100'})
        shared = next(w.address for w in p['windows'] if w.address not in data_addresses)
        for address in (shared, reader.data.BASE+480):
            bad=dict(overlay); at=reader.offset(address,1,len(self.raw)); bad[at]^=1
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.verify(bad, edits)
        with self.assertRaises(ValueError):
            reader.readback_reader(self.raw, lambda a,n:b'', edits)

    def test_user_profile_and_wrong_selection(self):
        ident='user_lut_independent'
        profile=copy.deepcopy(film_profiles.load_profile('gold200')); profile['id']=ident
        profiles={ident:profile}
        p, overlay, edits=self.plan({9:ident,11:'portra400'},profiles)
        self.verify(overlay,edits,profiles)
        with self.assertRaises(ValueError):
            self.verify(overlay,[dict(kind='film',slot=9,film=ident)],profiles)
        with self.assertRaises(ValueError):
            self.verify(overlay,edits,{})

    def test_fixed_range_corruption_rejected(self):
        p,overlay,edits=self.plan({8:'gold200'})
        original=json.loads(reader.RANGES.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ranges.json'
            for mutation in ('missing','extra','bool','alias'):
                doc=copy.deepcopy(original); rows=doc['masks']['1']
                if mutation=='missing':rows.pop()
                elif mutation=='extra':rows.append(dict(rows[0]))
                elif mutation=='bool':rows[0]['length']=True
                else:rows[0]['address']=0x5407b540
                path.write_text(json.dumps(doc))
                with self.subTest(mutation=mutation),patch.object(reader,'RANGES',path),self.assertRaises(ValueError):
                    self.verify(overlay,edits)



    def test_range_file_errors_fail_closed(self):
        p, overlay, edits=self.plan({8:'gold200'})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ranges.json'
            for content in (None, '[]', '{', '{"schema":1,"layout":"'+reader.LAYOUT+'","masks":{}}'):
                if content is not None:path.write_text(content)
                with self.subTest(content=content),patch.object(reader,'RANGES',path),self.assertRaises(reader.data.ReadbackError):
                    self.verify(overlay,edits)



class SyntheticCompleteVerifier(unittest.TestCase):
    """Only a 256-byte toy container; actual decoder is trapped by a stub."""
    def check(self, outside=False, overlap=False, metadata_lie=False, wrong_reader_layout=False):
        import hashlib
        from contextlib import ExitStack
        from unittest.mock import Mock
        from darkslide import composite_verify as cv, fwread, template
        official=bytes(256); before=bytes(256); after=bytearray(before)
        after[100:104]=b'film'
        if outside:after[108]=1
        after[-4:]=((-cv._sum32(after[:-4])) & 0xffffffff).to_bytes(4,'little')
        after=bytes(after)
        fake=Mock();fake.off.return_value=0
        fake.icon_pixels.return_value=({'payload_offset':0},b'')
        fake.string.return_value={'text':''}
        proof={k:True for k in ('data_verified','shared_bytes_match_reference','complete_window_coverage_verified')}
        proof['layout']='inplace_history_cache_exit_vivid5_v1' if wrong_reader_layout else reader.LAYOUT
        spans=((100,b'film'),(101,b'x')) if overlap else ((100,b'film'),)
        result=dict(output=official,output_sha256=hashlib.sha256(official).hexdigest(),
                    payload_sha256_after=hashlib.sha256(after).hexdigest(),
                    film_layout='four_curve_v1' if metadata_lie else reader.LAYOUT,
                    payload_changes=[{'offset':0,'length':256}])
        with ExitStack() as stack:
            stack.enter_context(patch.object(fwread,'V210_INPUT_SHA256',hashlib.sha256(official).hexdigest()))
            decode=stack.enter_context(patch.object(fwread,'unpack',side_effect=[before,after]))
            stack.enter_context(patch.object(fwread,'Payload',return_value=fake))
            stack.enter_context(patch.object(cv,'_container',return_value=((1,2,3,4,5),{},256)))
            stack.enter_context(patch.object(cv,'_resources',return_value=[]))
            rb=stack.enter_context(patch.object(reader,'readback',return_value=(proof,spans)))
            checks=cv.verify(official,result,[dict(kind='film',slot=8,film='gold200')],template.load(),film_layout=reader.LAYOUT)
            self.assertEqual(rb.call_count,1)
            self.assertEqual(decode.call_count,2)
            self.assertEqual(rb.call_args.args[1],after)
            return checks

    def test_complete_verifier_checks_all_other_bytes(self):
        from darkslide import composite_verify as cv
        self.assertTrue(cv.all_ok(self.check()))
        self.assertFalse(cv.all_ok(self.check(outside=True)))
        self.assertFalse(cv.all_ok(self.check(overlap=True)))
        self.assertFalse(cv.all_ok(self.check(wrong_reader_layout=True)))

    def test_result_cannot_choose_layout_or_authorize_spans(self):
        from darkslide import composite_verify as cv
        # Caller-selected v29 still used even when metadata claims an old layout.
        self.assertTrue(cv.all_ok(self.check(metadata_lie=True)))
        self.assertFalse(cv.all_ok(self.check(outside=True,metadata_lie=True)))

class IndependentAppGate(unittest.TestCase):
    def test_truthy_and_second_check_cannot_open_output(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from darkslide import server, generate, models, template
        t=template.load(); project=dict(id='p',changes={'8':{'film':'gold200'}})
        for values in ([False], [1], ['yes'], [True,True,False]):
            store=Mock();store.get_project.return_value=project
            req=SimpleNamespace(store=store,app=Mock())
            with self.subTest(values=values),patch.object(planner,'production_available',side_effect=values),patch.object(server,'project_context',return_value=(project,models.GR3,t)),patch.object(generate,'run_inplace',side_effect=AssertionError('producer')):
                with self.assertRaises(server.ApiError) as err:server.api_generate(req,'p')
                self.assertEqual(err.exception.status,422)
                store.input_path.assert_not_called()
                store.put_build.assert_not_called()

if __name__=='__main__':unittest.main()
