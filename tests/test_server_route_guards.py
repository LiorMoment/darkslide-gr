"""In-process route-function tests; synthetic bytes only, no HTTP or output."""
import copy
import hashlib
import itertools
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from darkslide import body_target, camera_features, compositor, film_profiles, gr3x_generation, menu_order, models, server
from darkslide import film_requests, inplace_generation


class FakePath:
    def exists(self): return True
    def read_bytes(self): return b'abc'


class FakeStore:
    def __init__(self, project):
        self.project = project
        self.saved_builds = 0

    def get_project(self, pid): return copy.deepcopy(self.project)
    def input_path(self, sha): return FakePath()
    def film_changes_for_generate(self, pid, changes): return copy.deepcopy(changes)
    def film_luts(self, pid): return {}
    def put_build(self, output, manifest):
        self.saved_builds += 1
        raise AssertionError('build must not be saved')


class Request:
    def __init__(self, project, body=None):
        self.store = FakeStore(project)
        lock = threading.Lock()
        self.app = SimpleNamespace(iiix_generation_lock=lock, generation_lock=lock, store=self.store,
                                   long_jobs={}, long_jobs_lock=threading.Lock())
        self.body = body or {}

    def read_json(self): return copy.deepcopy(self.body)


class ReachedFurther(Exception): pass


class ImmediateJobs:
    """Synthetic scheduler: runs a job inline; no thread or network."""
    def __init__(self): self.jobs = {}
    def forget(self, key): self.jobs.pop(key, None)
    def submit(self, key, run, total, lane): self.jobs[key] = dict(state='done', result=run(None))
    def status(self, key): return self.jobs.get(key)


def refused_flags():
    for soft, wide, date in itertools.product((False, True), repeat=3):
        if date:
            yield dict(soft_glow=soft, wide_ratio=wide, date_stamp=date)


class RouteGuards(unittest.TestCase):
    def test_all_three_save_routes_mark_new_wide_without_date(self):
        template = dict(reviewed=True)
        flags = dict(wide_ratio=True, soft_glow=True)
        digest = hashlib.sha256(b'abc').hexdigest()
        for route in ('gr3', 'gr3_inplace', 'iiix'):
            project = dict(id='fake', changes={}, camera_features=flags, input_sha256='fake')
            req = Request(project, dict(expected_changes={}, recipe_extension=False))
            manifest = dict(features=['wide_ratio'], output_sha256=digest)
            model = models.GR3X if route == 'iiix' else models.GR3
            with self.subTest(route=route), \
                 patch.object(server, 'project_context', return_value=(project, model, template)), \
                 patch.object(req.store, 'put_build', side_effect=lambda output, record: record), \
                 patch.object(inplace_generation, 'production_available', return_value=True), \
                 patch.object(film_requests, 'needs_closed_output', return_value=route == 'gr3_inplace'), \
                 patch.object(film_requests, 'available', return_value=False), \
                 patch.object(server.generate, 'run', return_value=(b'abc', manifest)), \
                 patch.object(server.generate, 'run_inplace', return_value=(b'abc', manifest)), \
                 patch.object(gr3x_generation, 'run', return_value=(b'abc', manifest)):
                result = server.api_iiix_candidate(req, 'fake') if route == 'iiix' else server.api_generate(req, 'fake')
                self.assertEqual(result['download_policy'], dict(schema=1, date_stamp=False, output_sha256=digest))

    def assert_refused(self, response, req):
        self.assertIsInstance(response, server.Reply)
        self.assertEqual(response.status, 422)
        self.assertIn('更新/恢复证据还没齐', response.body.get('error', ''))
        self.assertEqual(req.store.saved_builds, 0)

    def test_gr3_generate_plain_and_long_call_reject(self):
        template = dict(reviewed=True)
        for flags in refused_flags():
            project = dict(id='fake', changes={}, camera_features=flags, input_sha256='fake')
            for long_call in (False, True):
                with self.subTest(flags=flags, long_call=long_call):
                    req = Request(project)
                    with patch.object(server, 'project_context', return_value=(project, models.GR3, template)), \
                         patch.object(compositor, 'build_composite', side_effect=AssertionError('must not build')), \
                         patch.object(compositor, 'build_inplace_composite', side_effect=AssertionError('must not build')):
                        reply = server._long_call('generate', req, 'fake', None) if long_call else server.api_generate(req, 'fake')
                    self.assert_refused(reply, req)

    def test_gr3_film_inplace_rejects_before_builder(self):
        fake_model = SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(), input_size=3)
        template = dict(model='GR III', firmware='2.10', reviewed=True, id='test')
        for flags in refused_flags():
            project = dict(id='fake', changes={'2': {'film': 'portra400'}}, camera_features=flags,
                           input_sha256=fake_model.input_sha, template_id='test')
            req = Request(project)
            with self.subTest(flags=flags), \
                 patch.object(models, 'GR3', fake_model), \
                 patch.object(models, 'capabilities', return_value={'full_firmware': True}), \
                 patch.object(server, 'project_context', return_value=(project, fake_model, template)), \
                 patch.object(inplace_generation, 'production_available', return_value=True), \
                 patch.object(body_target, 'of', return_value='synthetic'), \
                 patch.object(body_target, 'require_family', return_value='synthetic'), \
                 patch.object(film_profiles, 'data_revision', return_value='synthetic'), \
                 patch.object(compositor, 'build_inplace_composite', side_effect=AssertionError('must not build')):
                reply = server.api_generate(req, 'fake')
            self.assert_refused(reply, req)

    def test_iiix_candidate_plain_and_long_call_reject(self):
        template = dict(reviewed=True)
        for flags in refused_flags():
            project = dict(id='fake', changes={}, camera_features=flags)
            for long_call in (False, True):
                with self.subTest(flags=flags, long_call=long_call):
                    req = Request(project, dict(expected_changes={}, recipe_extension=False))
                    with patch.object(server, 'project_context', return_value=(project, models.GR3X, template)), \
                         patch.object(models, 'for_project', return_value=models.GR3X), \
                         patch.object(models, 'for_template', return_value=models.GR3X), \
                         patch.object(compositor, 'build_composite', side_effect=AssertionError('must not build')):
                        reply = server._long_call('iiix-candidate', req, 'fake', None) if long_call else server.api_iiix_candidate(req, 'fake')
                    self.assert_refused(reply, req)
                    self.assertFalse(req.app.iiix_generation_lock.locked())

    def test_gr3_control_reaches_post_guard_without_building(self):
        project = dict(id='fake', changes={}, camera_features={'soft_glow': True}, input_sha256='fake')
        req = Request(project)
        with patch.object(server, 'project_context', return_value=(project, models.GR3, dict(reviewed=True))), \
             patch.object(camera_features, 'edits', side_effect=ReachedFurther('past guard')), \
             patch.object(compositor, 'build_composite', side_effect=AssertionError('must not build')):
            with self.assertRaisesRegex(ReachedFurther, 'past guard'):
                server.api_generate(req, 'fake')
        self.assertEqual(req.store.saved_builds, 0)

    def test_iiix_soft_only_reaches_past_guard_without_building(self):
        project = dict(id='fake', changes={}, camera_features={'soft_glow': True})
        req = Request(project, dict(expected_changes={}, recipe_extension=False))
        with patch.object(server, 'project_context', return_value=(project, models.GR3X, dict(reviewed=True))), \
             patch.object(models, 'for_project', return_value=models.GR3X), \
             patch.object(models, 'for_template', return_value=models.GR3X), \
             patch.object(menu_order, 'normalize', side_effect=ReachedFurther('past guard')), \
             patch.object(compositor, 'build_composite', side_effect=AssertionError('must not build')):
            with self.assertRaisesRegex(ReachedFurther, 'past guard'):
                server.api_iiix_candidate(req, 'fake')
        self.assertEqual(req.store.saved_builds, 0)
        self.assertFalse(req.app.iiix_generation_lock.locked())

    def test_job_route_reports_refusal_and_releases_lock(self):
        from darkslide import lut_ranges
        for task in ('generate', 'iiix-candidate'):
            project = dict(id='fake', changes={}, camera_features={'date_stamp': True}, input_sha256='fake')
            inner = {} if task == 'generate' else dict(expected_changes={}, recipe_extension=False)
            req = Request(project, dict(task=task, body=inner))
            model = models.GR3 if task == 'generate' else models.GR3X
            with self.subTest(task=task), \
                 patch.object(server, 'project_context', return_value=(project, model, dict(reviewed=True))), \
                 patch.object(models, 'for_project', return_value=models.GR3X), \
                 patch.object(models, 'for_template', return_value=models.GR3X), \
                 patch.object(lut_ranges, 'JOBS', ImmediateJobs()), \
                 patch.object(compositor, 'build_composite', side_effect=AssertionError('must not build')):
                answer = server.api_start_job(req, 'fake')
            self.assertEqual(answer['state'], 'failed')
            self.assertEqual(answer['status'], 422)
            self.assertIn('更新/恢复证据还没齐', answer['error'])
            self.assertEqual(req.store.saved_builds, 0)
            self.assertFalse(req.app.generation_lock.locked())


if __name__ == '__main__': unittest.main()
