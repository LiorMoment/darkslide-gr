"""New firmware App routes and saved sources; never generate a camera image."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from darkslide import (compositor, composite_verify, film_profiles, film_requests,
                       fwaddr, generate, iiix_prepare, inplace_generation, models, rescue_gate)
from darkslide.store import Store, StoreError

LAYOUTS = {
    models.GR3: 'inplace_history_cache_exit_vivid5_v1',
    models.GR3_220: 'inplace_history_cache_exit_vivid5_220_v1',
    models.GR3X: 'gr3x160_inplace_cache_exit_vivid5_v1',
    models.GR3X_170: 'gr3x170_inplace_cache_exit_vivid5_v1',
}
FIVE = {str(s): {'film': f} for s, f in zip((2, 8, 9, 10, 11),
        ('portra160', 'gold200', 'portra400', 'ektar100', '5219'))}


class AppLayouts(unittest.TestCase):
    def test_all_masks_choose_the_exact_firmware_and_keep_the_old_four_route(self):
        for model, vivid in LAYOUTS.items():
            module = iiix_prepare if models.is_iiix(model) else inplace_generation
            legacy = module.planner.LAYOUT if models.is_iiix(model) else module.LAYOUT
            for mask in range(32):
                requests = film_requests.from_changes({str(s): FIVE[str(s)] for bit, s in
                    enumerate((8, 9, 10, 11, 2)) if mask & (1 << bit)})
                with self.subTest(model=model.id, mask=mask):
                    want = vivid if mask & 16 else legacy
                    self.assertEqual(module.layout_for(requests, model), want)
                    with fwaddr.firmware(model):
                        self.assertEqual(module.layout_for(requests), want)
        with self.assertRaises(ValueError):
            inplace_generation.layout_for([], models.GR3X_170)
        with self.assertRaises(ValueError):
            iiix_prepare.layout_for([], models.GR3_220)

    def test_compositor_selects_new_layout_before_entering_address_context(self):
        requests = film_requests.from_changes(FIVE)
        for model in (models.GR3, models.GR3_220):
            with self.subTest(model=model.id), patch.object(compositor, '_build', return_value='sentinel') as build:
                self.assertIsNone(fwaddr.current())
                self.assertEqual(compositor.build_inplace_composite(b'', requests,
                    models.template_for(model)), 'sentinel')
                self.assertEqual(build.call_args.kwargs['film_layout'], LAYOUTS[model])
                self.assertEqual(build.call_args.args[1], requests)

    def test_pipeline_uses_same_new_layout_for_verification_and_manifest(self):
        # All producer/reader boundaries return tiny synthetic sentinels.
        model = models.GR3_220
        template = models.template_for(model)
        project = dict(id='toy', title='toy', target_body=model.name, changes=copy.deepcopy(FIVE))
        result = dict(output=b'synthetic', input_sha256='i', output_sha256='o',
            payload_sha256_before='b', payload_sha256_after='a', version_word='v',
            payload_changes=[], container={}, builder='toy', features=['film'])
        with patch.object(compositor, 'build_inplace_composite', return_value=result) as writer, \
                patch.object(composite_verify, 'verify', return_value=[dict(id='toy', ok=True)]) as verifier, \
                patch.object(rescue_gate, 'gate', return_value=[dict(id='rescue', ok=True)]), \
                patch.object(generate, 'body_check', return_value=([dict(id='body', ok=True)], {})):
            output, manifest = generate._run(None, project, template, b'toy source', inplace=True)
        self.assertEqual(output, b'synthetic')
        self.assertEqual(manifest['film_layout'], LAYOUTS[model])
        self.assertEqual(verifier.call_args.kwargs['film_layout'], LAYOUTS[model])
        self.assertEqual(writer.call_args.args[1], list(film_requests.from_changes(FIVE)))

    def test_saved_prepare_keeps_model_profiles_and_rejects_wrong_reader_identity(self):
        for model in models.NEW_220:
            module = iiix_prepare if models.is_iiix(model) else inplace_generation
            planner = module.planner if models.is_iiix(model) else module.film_inplace_cache
            reader = module.reader if models.is_iiix(model) else module.film_inplace_integrity
            project = dict(input_sha256=model.input_sha, template_id=model.id, changes=copy.deepcopy(FIVE))
            layout = LAYOUTS[model]
            plan = dict(layout=layout, decoded_growth_bytes=0, windows=[], full_image_created=False,
                        production_integration_allowed=False, storage_approved=False)
            proof = dict(layout=layout, selected_slots=[2, 8, 9, 10, 11], data_verified=True,
                         shared_bytes_match_reference=True, complete_window_coverage_verified=True,
                         production_approved=False, shared_code_verified=False)
            for mutation in (None, 'layout', 'selected_slots'):
                checked = copy.deepcopy(proof)
                if mutation == 'layout': checked['layout'] = LAYOUTS[models.film_base(model)]
                if mutation == 'selected_slots': checked['selected_slots'] = [8, 9, 10, 11]
                with self.subTest(model=model.id, mutation=mutation), fwaddr.firmware(model), \
                        patch.object(planner, 'plan_offline', return_value=copy.deepcopy(plan)) as planned, \
                        patch.object(reader, 'verify_project', return_value=checked):
                    if mutation:
                        with self.assertRaises(ValueError): module.prepare(b'synthetic', project)
                    else:
                        prepared = module.prepare(b'synthetic', project)
                        self.assertEqual(prepared['layout'], layout)
                    supplied = planned.call_args.args[2]
                    self.assertEqual(set(supplied), {entry['film'] for entry in FIVE.values()})
                    self.assertEqual({p['firmware'] for p in supplied.values()}, {model.firmware})


class SavedNewFirmware(unittest.TestCase):
    def test_five_positions_reopen_replace_unload_and_keep_user_lut_source(self):
        cube = b'LUT_3D_SIZE 2\n' + b''.join(('%d %d %d\n' % (r, g, b)).encode()
            for b in (0, 1) for g in (0, 1) for r in (0, 1))
        for model in models.NEW_220:
            with self.subTest(model=model.id), tempfile.TemporaryDirectory() as folder:
                store = Store(folder)
                template = models.template_for(model)
                with patch.object(store, 'has_input', return_value=True):
                    pid = store.create_project('five films', model.input_sha, template)['id']
                def save(changes):
                    store.put_draft(pid, changes, template)
                    store.save(pid, template)
                    reopened = Store(folder).get_project(pid)
                    self.assertEqual(reopened['changes'], changes)
                    self.assertEqual(models.for_project(reopened), model)
                save(FIVE)
                for key in FIVE:
                    changed = copy.deepcopy(FIVE)
                    changed[key]['film'] = 'pro400h'
                    save(changed)
                    del changed[key]
                    save(changed)
                ident = store.put_film_lut(pid, cube, 'own.cube')['id']
                changes = {**copy.deepcopy(FIVE), '2': {'film': ident}}
                save(changes)
                self.assertEqual(Store(folder).film_lut_path(pid, ident).read_bytes(), cube)
                self.assertEqual(store._lut_model(pid), model)
                with self.assertRaises(StoreError): store.delete_film_lut(pid, ident)
                with self.assertRaises(StoreError): store.film_lut_profiles(pid, [ident])
                save({})
                self.assertEqual(store.delete_film_lut(pid, ident), [])
                self.assertEqual(store.builds(), [])

    def test_old_and_new_version_profiles_cannot_be_substituted(self):
        for newer in models.NEW_220:
            older = models.film_base(newer)
            for source, target in ((older, newer), (newer, older)):
                with self.subTest(source=source.id, target=target.id):
                    profile = film_profiles.load_profile('gold200', source.id)
                    with self.assertRaises(ValueError):
                        film_profiles.validate_profile(profile, 'gold200', target.id)


if __name__ == '__main__':
    unittest.main()
