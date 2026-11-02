"""Five-position request/save/UI contract; no firmware producer or device."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from darkslide import film_requests as requests, film_slots, models
from darkslide.store import Store, StoreError


class SlotContract(unittest.TestCase):
    def test_all_masks_keep_the_old_four_bits_and_vivid_bit_four(self):
        ordered = (8, 9, 10, 11, 2)
        for mask in range(32):
            slots = [slot for bit, slot in enumerate(ordered) if mask & (1 << bit)]
            self.assertEqual(film_slots.mask_slots(slots), mask)
            changes = {str(slot): {'film': 'portra160'} for slot in slots}
            edits = requests.from_changes(changes)
            self.assertEqual([edit['slot'] for edit in edits], sorted(slots))
            self.assertEqual(requests.current_slots(changes), tuple(
                {'slot': slot, 'film': 'portra160' if slot in slots else None}
                for slot in (2, 8, 9, 10, 11)))
        self.assertEqual(requests.status()['supported_slots'], [2, 8, 9, 10, 11])

    def test_rejects_ambiguous_slots_and_keeps_positive_negative_out(self):
        for slot in (True, False, '2', 2.0, 1, 7, 12, 13, 14):
            with self.subTest(slot=slot), self.assertRaises(requests.FilmRequestError):
                requests.validate_request({'kind': 'film', 'slot': slot, 'film': 'gold200'})
            with self.assertRaises(ValueError):
                film_slots.mask_slots([slot])
        for bad in ([2, 2], {2}, None):
            with self.assertRaises(ValueError):
                film_slots.mask_slots(bad)
        for key in ('02', '+2', '2.0', 2, '7', '12'):
            with self.subTest(key=key), self.assertRaises(requests.FilmRequestError):
                requests.from_changes({key: {'film': 'portra160'}})
        edit = {'kind': 'film', 'slot': 2, 'film': 'gold200'}
        for bad in ([edit, edit], [edit] * 6):
            with self.assertRaises(requests.FilmRequestError):
                requests.validate_batch(bad)

    def test_vivid_grade_cannot_bypass_the_output_gate(self):
        for changes in ({'2': {'film': 'portra160'}}, {'2': {'grade': {'temp': 4}}}):
            self.assertTrue(requests.needs_closed_output(changes))
        self.assertFalse(requests.needs_closed_output({'2': {'grade': {}}}))
        self.assertFalse(requests.needs_closed_output({'7': {'grade': {'temp': 4}}, '12': {'grade': {'con': 4}}}))


class SavedChoices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def project(self, model):
        template = models.template_for(model)
        sha = self.store.put_input(b'synthetic official identity')
        pid = self.store.create_project('five films', sha, template)['id']
        return pid, template

    def save(self, pid, template, changes):
        self.store.put_draft(pid, changes, template)
        self.store.save(pid, template)
        return Store(self.tmp.name).get_project(pid)['changes']

    def test_five_choices_reopen_and_replacing_each_one_keeps_the_others(self):
        for model in (models.GR3, models.GR3X):
            with self.subTest(model=model.id):
                pid, template = self.project(model)
                original = {str(s): {'film': f} for s, f in zip(
                    (2, 8, 9, 10, 11), ('portra160', 'gold200', 'portra400', 'ektar100', '5219'))}
                self.assertEqual(self.save(pid, template, original), original)
                for key in original:
                    changed = copy.deepcopy(original)
                    changed[key]['film'] = 'pro400h'
                    self.assertEqual(self.save(pid, template, changed), changed)
                    del changed[key]
                    self.assertEqual(self.save(pid, template, changed), changed)
                self.assertEqual(self.save(pid, template, {}), {})

    def test_vivid_user_lut_keeps_its_source_and_cannot_be_deleted_while_selected(self):
        pid, template = self.project(models.GR3)
        cube = b'LUT_3D_SIZE 2\n' + b''.join(('%d %d %d\n' % (r, g, b)).encode()
            for b in (0, 1) for g in (0, 1) for r in (0, 1))
        ident = self.store.put_film_lut(pid, cube, 'my-film.cube')['id']
        changes = {'2': {'film': ident}, '8': {'film': 'gold200'}}
        self.assertEqual(self.save(pid, template, changes), changes)
        reopened = Store(self.tmp.name)
        self.assertEqual(reopened.film_lut_path(pid, ident).read_bytes(), cube)
        with self.assertRaises(StoreError):
            reopened.film_lut_profiles(pid, [ident])  # saved does not mean fitted
        with self.assertRaises(StoreError):
            reopened.delete_film_lut(pid, ident)
        self.save(pid, template, {'8': {'film': 'gold200'}})
        self.assertEqual(reopened.delete_film_lut(pid, ident), [])

    def test_vivid_grade_resolves_saved_bake_and_rejects_stale_bake(self):
        pid, _ = self.project(models.GR3)
        changes = {'2': {'film': 'portra160', 'grade': {'temp': 4}},
                   '8': {'film': 'gold200'}, '7': {'grade': {'temp': 4}}, '12': {'grade': {'temp': 4}}}
        before = copy.deepcopy(changes)
        with patch.object(self.store, 'bake_for', return_value='user_lut_123456789abc') as bake:
            effective = self.store.film_changes_for_generate(pid, changes)
        bake.assert_called_once_with(pid, 'portra160', {'temp': 4})
        self.assertEqual(effective['2']['film'], 'user_lut_123456789abc')
        self.assertEqual({k: v for k, v in effective.items() if k != '2'},
                         {k: v for k, v in changes.items() if k != '2'})
        self.assertEqual(changes, before)
        with patch.object(self.store, 'bake_for', return_value=None), self.assertRaises(StoreError):
            self.store.film_changes_for_generate(pid, changes)

    def test_positive_negative_cannot_save_film_changes(self):
        pid, template = self.project(models.GR3)
        for slot in ('7', '12'):
            with self.assertRaises(StoreError):
                self.store.put_draft(pid, {slot: {'film': 'gold200'}}, template)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class Picker(unittest.TestCase):
    def test_real_picker_vivid_selection_fitting_and_factory_positions(self):
        script = Path(__file__).with_name('vivid_app_ui.cjs')
        done = subprocess.run([shutil.which('node'), str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result['slots'], list(requests.SUPPORTED_SLOTS))
        self.assertEqual(result['selection'], [[2, 'portra160'], [2, None]])
        self.assertEqual(result['fitted'], 1)
        self.assertEqual(result['jobs'], [['p_test', 'film-fit', {'lut': 'user_lut_123456789abc'}]])


if __name__ == '__main__':
    unittest.main()
