"""App generation dispatch on mock inputs; production gate never opens for users."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from darkslide import server, generate, inplace_generation, models, template

class InplaceRouting(unittest.TestCase):
    def fixture(self, grade=False):
        t=template.load()
        p=dict(id='toy',changes={'8':{'grade':{'warmth':1}} if grade else {'film':'gold200'}},input_sha256='toy',template_id=t['id'])
        store=Mock();store.get_project.side_effect=lambda pid:copy.deepcopy(p)
        store.input_path.return_value.exists.return_value=True
        store.input_path.return_value.read_bytes.return_value=b'toy'
        store.put_build.return_value={'id':'toy-build'}
        return SimpleNamespace(app=Mock(),store=store),p,t

    def test_closed_gate_never_reaches_producer(self):
        req,p,t=self.fixture()
        with patch.object(inplace_generation,'production_available',return_value=False), patch.object(generate,'run_inplace',side_effect=AssertionError('must not produce')) as producer:
            with self.assertRaises(server.ApiError) as caught:server.api_generate(req,'toy')
            self.assertEqual(caught.exception.status,422)
            producer.assert_not_called();req.store.put_build.assert_not_called()
        self.assertTrue(inplace_generation.production_available())

    def test_future_enabled_dispatch_preserves_saved_project(self):
        for grade in (False,True):
            req,p,t=self.fixture(grade)
            with patch.object(server,'project_context',return_value=(copy.deepcopy(p),models.GR3,t)),patch.object(generate,'run_inplace',return_value=(b'toy-result',{'toy':True,'features':['film']})) as producer,patch.object(generate,'run',side_effect=AssertionError('legacy producer')):
                self.assertEqual(server.api_generate(req,'toy'),{'id':'toy-build'})
                self.assertEqual(producer.call_args.args[1],p)
                req.store.film_changes_for_generate.assert_not_called()
                req.store.put_build.assert_called_once_with(b'toy-result',{'toy':True,'features':['film']})

    def test_internal_failure_never_saved(self):
        req,p,t=self.fixture()
        with patch.object(inplace_generation,'production_available',return_value=True),patch.object(server,'project_context',return_value=(copy.deepcopy(p),models.GR3,t)),patch.object(generate,'run_inplace',side_effect=generate.GenerateError('toy failure')):
            reply=server.api_generate(req,'toy');self.assertEqual(reply.status,422)
            req.store.put_build.assert_not_called()

    def test_iiix_stays_rejected_with_v29_gate_mocked(self):
        req,p,t=self.fixture()
        with patch.object(inplace_generation,'production_available',return_value=True),patch.object(server,'project_context',return_value=(copy.deepcopy(p),models.GR3X,t)),patch.object(generate,'run_inplace') as producer:
            with self.assertRaises(server.ApiError) as caught:server.api_generate(req,'toy')
            self.assertEqual(caught.exception.status,422)
            producer.assert_not_called();req.store.put_build.assert_not_called()

    def test_repeated_project_guard_rejects_changes(self):
        req,p,t=self.fixture();old=copy.deepcopy(p);old['changes']={}
        with patch.object(inplace_generation,'production_available',return_value=True),patch.object(server,'project_context',return_value=(old,models.GR3,t)),patch.object(generate,'run_inplace') as producer:
            with self.assertRaises(server.ApiError) as caught:server.api_generate(req,'toy')
            self.assertEqual(caught.exception.status,409)
            producer.assert_not_called();req.store.put_build.assert_not_called()

if __name__=='__main__':unittest.main()
