"""Extra guard checks using only small synthetic projects and mocked inputs."""
import hashlib
import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from darkslide import body_target, camera_features, film_profiles, generate, gr3x_generation, menu_order, models
from test_camera_guard_wrappers import FakeStore, ReachedFurther


class AdditionalGuardTests(unittest.TestCase):
    def test_later_versions_reject_all_six_switch_combinations(self):
        fake = SimpleNamespace(input_sha=hashlib.sha256(b'abc').hexdigest(), input_size=3)
        template = {'model': 'GR III', 'firmware': '2.20', 'reviewed': True, 'id': 'test'}
        for soft, wide, date in itertools.product((False, True), repeat=3):
            if not date:
                continue
            features = {'soft_glow': soft, 'wide_ratio': wide, 'date_stamp': date}
            with self.subTest(model='GR III 2.20', features=features):
                project = {'id': 'fake', 'changes': {}, 'camera_features': features,
                           'template_id': 'test', 'input_sha256': fake.input_sha}
                with patch.object(models, 'GR3_220', fake), \
                     patch.object(body_target, 'of', return_value='synthetic'), \
                     patch.object(body_target, 'require_family', return_value='synthetic'), \
                     patch.object(film_profiles, 'data_revision', return_value='synthetic'), \
                     patch.object(generate.compositor, 'build_composite', side_effect=AssertionError('must not build')):
                    with self.assertRaisesRegex(generate.GenerateError, '更新/恢复证据还没齐'):
                        generate.run_inplace(FakeStore(project), project, template, b'abc')
            with self.subTest(model='GR IIIx 1.70', features=features):
                project = {'id': 'fake', 'changes': {}, 'camera_features': features}
                with patch.object(models, 'for_project', return_value=models.GR3X_170), \
                     patch.object(models, 'for_template', return_value=models.GR3X_170), \
                     patch.object(gr3x_generation.compositor, 'build_composite', side_effect=AssertionError('must not build')):
                    with self.assertRaisesRegex(generate.GenerateError, '更新/恢复证据还没齐'):
                        gr3x_generation.run(FakeStore(project), project, {}, b'')

    def test_nonempty_changes_and_menu_order_cannot_bypass_guard(self):
        project = {'id': 'fake', 'changes': {'example': {}}, 'menu_order': ['example'],
                   'camera_features': {'date_stamp': True}}
        with patch.object(menu_order, 'normalize', return_value=['example']), \
             patch.object(menu_order, 'supported', return_value=True), \
             patch.object(generate.compositor, 'build_composite', side_effect=AssertionError('must not build')):
            with self.assertRaisesRegex(generate.GenerateError, '更新/恢复证据还没齐'):
                generate._run(None, project, {}, b'')
        with patch.object(body_target, 'of', side_effect=ReachedFurther('must not reach body')), \
             patch.object(gr3x_generation.compositor, 'build_composite', side_effect=AssertionError('must not build')):
            with self.assertRaisesRegex(generate.GenerateError, '更新/恢复证据还没齐'):
                gr3x_generation._run(None, project, {}, b'', None, False)


if __name__ == '__main__':
    unittest.main()
