"""Synthetic-only complete wiring tests: never construct a camera image."""
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
from darkslide import compositor, edit_core, film_inplace_apply, generate, template, models

class CompositeWiring(unittest.TestCase):
    def build(self, bad=False, overlap=False, slot=8):
        source=b'synthetic input'; before=bytes(64)
        film=bytearray(before); film[8:12]=b'film'
        if bad:film[20]=1
        at=8 if overlap else 4
        name=bytearray(before);name[at:at+4]=b'name'
        with ExitStack() as stack:
            for key,value in [('INPUT_SIZE',len(source)),('INPUT_SHA',edit_core._sha(source)),('PAYLOAD_SHA',edit_core._sha(before))]:
                stack.enter_context(patch.object(edit_core,key,value))
            stack.enter_context(patch.object(edit_core,'_decode',return_value=(before,[],0,b'')))
            stack.enter_context(patch.object(edit_core,'_validate_edits'))
            stack.enter_context(patch.object(edit_core,'apply_edits',return_value=(bytes(name),[dict(offset=at,length=4)])))
            writer=stack.enter_context(patch.object(film_inplace_apply,'apply',return_value=(bytes(film),[dict(offset=8,length=4,kind='film')])))
            pack=stack.enter_context(patch.object(edit_core,'_pack_payload',side_effect=lambda *args:dict(output=args[-2])))
            result=compositor.build_inplace_composite(source,[dict(kind='film',slot=slot,film='portra400'),dict(kind='name',slot=8,string_id=275,text='New')],template.load())
            self.assertEqual(writer.call_count,1);self.assertEqual(pack.call_count,1)
            return result

    def test_v29_merges_regular_once(self):
        result=self.build()
        self.assertEqual(result['output'][4:12],b'namefilm')
        self.assertEqual(result['film_layout'],'inplace_history_cache_exit_v29')
        self.assertEqual(result['decoded_growth_bytes'],0)

    def test_vivid_uses_five_slot_adapter_and_still_merges_regular_once(self):
        result=self.build(slot=2)
        self.assertEqual(result['output'][4:12],b'namefilm')
        self.assertEqual(result['film_layout'],'inplace_history_cache_exit_vivid5_v1')
        self.assertEqual(result['decoded_growth_bytes'],0)

    def test_overlap_and_undeclared_changes_reject(self):
        for bad,overlap in ((True,False),(False,True)):
            with self.assertRaises(compositor.CompositeError):self.build(bad,overlap)

    def test_default_entry_stays_closed(self):
        with patch.object(edit_core,'_decode',side_effect=AssertionError('must not decode')):
            with self.assertRaises(compositor.CompositeError):
                compositor.build_composite(b'', [dict(kind='film',slot=8,film='portra400')], template.load(),film_layout='inplace_history_cache_exit_v29')

class SavedWiring(unittest.TestCase):
    def fixture(self):
        tpl=template.load();project=dict(id='toy',title='toy',template_id=tpl['id'],input_sha256=models.GR3.input_sha,target_body='GR III',changes={'8':{'film':'portra400'}})
        store=Mock();store.get_project.return_value=copy.deepcopy(project)
        store.input_path.return_value.read_bytes.return_value=b'toy original'
        store.film_changes_for_generate.side_effect=lambda pid,changes:copy.deepcopy(changes)
        store.film_luts.return_value={}
        return store,project,tpl

    def call(self, store, project, tpl, mutate=False):
        # Patch only source identity and final _run; never execute a real producer.
        fake=Mock(input_sha='toysha',input_size=12)
        project['input_sha256']='toysha';store.get_project.return_value=copy.deepcopy(project)
        with patch.object(models,'GR3',fake),patch.object(generate.hashlib,'sha256') as sha,patch.object(generate,'_run') as run:
            sha.return_value.hexdigest.return_value='toysha'
            def finish(*a,**kw):
                if mutate:store.get_project.return_value['changes']['8']['film']='gold200'
                return b'synthetic output',{}
            run.side_effect=finish
            out=generate.run_inplace(store,project,tpl,b'toy original')
            self.assertTrue(run.call_args.kwargs['inplace'])
            self.assertEqual(run.call_args.kwargs['film_profiles'],{})
            store.put_build.assert_not_called()
            return out

    def test_saved_contract(self):self.call(*self.fixture())

    def test_concurrent_save_rejected(self):
        with self.assertRaisesRegex(generate.GenerateError,'期间'):
            self.call(*self.fixture(),mutate=True)

    def test_default_project_gate_stays_closed(self):
        store,project,tpl=self.fixture()
        with patch.object(generate,'_run',side_effect=AssertionError('no producer')):
            with self.assertRaisesRegex(generate.GenerateError,'尚未开放'):
                generate.run(store,project,tpl,b'')


class ApplyAdapter(unittest.TestCase):
    def test_windows_checked_before_mutation(self):
        from types import SimpleNamespace
        from darkslide import inplace_generation, fwread
        raw=bytes(64)
        for kind in ('ok','overlap','expected','bounds','alias','size'):
            windows=[SimpleNamespace(address=8,expected=bytes(4),replacement=b'film')]
            if kind=='overlap':windows.append(windows[0])
            if kind=='expected':windows[0].expected=b'nope'
            if kind=='bounds':windows[0].address=60
            if kind=='size':windows[0].replacement=b'oversize'
            mapper=lambda a: a+1 if kind=='alias' and a==11 else a
            with patch.object(inplace_generation,'prepare',return_value={'windows':windows}),patch.object(fwread,'Payload') as payload:
                payload.return_value.off.side_effect=mapper
                if kind=='ok':
                    out,decl=film_inplace_apply.apply(raw,[dict(kind='film',slot=8,film='portra400')])
                    self.assertEqual(out,bytes(8)+b'film'+bytes(52))
                    self.assertEqual(decl,[dict(offset=8,length=4,kind='film')])
                else:
                    with self.assertRaises(ValueError):film_inplace_apply.apply(raw,[dict(kind='film',slot=8,film='portra400')])

class InternalPipeline(unittest.TestCase):
    def execute(self, fail=None):
        from darkslide import composite_verify, rescue_gate
        t=template.load();p=dict(id='toy',title='Toy',target_body='GR III',changes={'8':{'film':'portra400'}})
        result=dict(output=b'toy',input_sha256='i',output_sha256='o',payload_sha256_before='b',payload_sha256_after='a',version_word='v',payload_changes=[],container={},builder='toy',features=['film'])
        with patch.object(compositor,'build_inplace_composite',return_value=result) as writer,patch.object(composite_verify,'verify',return_value=[dict(id='toy',ok=fail!='readback')]) as verify,patch.object(rescue_gate,'gate',return_value=[dict(id='rescue',ok=fail!='rescue')]),patch.object(generate,'body_check',return_value=([dict(id='body',ok=fail!='body')],{})):
            output,manifest=generate._run(None,p,t,b'toy input',inplace=True)
            writer.assert_called_once()
            self.assertEqual(verify.call_args.kwargs['film_layout'],'inplace_history_cache_exit_v29')
            self.assertEqual(writer.call_args.args[1],[dict(kind='film',slot=8,film='portra400')])
            self.assertEqual(manifest['skipped'],[])
            self.assertEqual(manifest['summary'][0]['what'],'胶片')
            self.assertEqual(manifest['film_layout'],'inplace_history_cache_exit_v29')
            self.assertEqual(manifest['output_filename'],'fwdc239b.bin')
            self.assertEqual(manifest['input_sha256'],'i')
            self.assertEqual(manifest['output_sha256'],'o')
            self.assertTrue(manifest['checks'])
            self.assertTrue(all(c['ok'] is True for c in manifest['checks']))
            return output

    def test_same_layout_reaches_reader_and_manifest(self):self.assertEqual(self.execute(),b'toy')

    def test_each_failed_gate_refuses_return(self):
        for fail in ('readback','rescue','body'):
            with self.assertRaises(generate.GenerateError):self.execute(fail)

if __name__=='__main__':unittest.main()
