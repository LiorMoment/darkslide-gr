"""Synthetic checks for the saved-build download boundary; no real firmware."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from darkslide import camera_features, legacy_import, server
from darkslide.store import Store


class FakeManifestPath:
    parent = object()
    def __init__(self, manifest): self.manifest = manifest
    def read_text(self, encoding): return json.dumps(self.manifest)


class FakeOutput:
    def read_bytes(self): return b'abc'


def request(manifest):
    return SimpleNamespace(store=SimpleNamespace(build_path=lambda *args: FakeManifestPath(manifest)))


def manifest(features):
    return dict(features=features, output_sha256=hashlib.sha256(b'abc').hexdigest())


class DownloadGuard(unittest.TestCase):
    def test_legacy_wide_may_hide_date_and_is_refused(self):
        edit = camera_features.edits(dict(wide_ratio=True, soft_glow=True, date_stamp=True))
        self.assertTrue(edit[0]['date'])
        flags = sorted({item['kind'] for item in edit})
        self.assertEqual(flags, ['wide_ratio'])
        with patch.object(legacy_import, 'checked_build') as checked:
            with self.assertRaises(server.ApiError) as error:
                server.api_build_firmware(request(manifest(flags)), 'fake')
            self.assertEqual(error.exception.status, 422)
            checked.assert_not_called()

    def test_wide_policy_must_be_complete_and_bind_output(self):
        digest = hashlib.sha256(b'abc').hexdigest()
        for policy in (None, {}, {'schema': True, 'date_stamp': False, 'output_sha256': digest},
                       {'schema': 1, 'date_stamp': True, 'output_sha256': digest},
                       {'schema': 1, 'date_stamp': False, 'output_sha256': 'wrong'},
                       {'schema': 1, 'date_stamp': False, 'output_sha256': digest, 'extra': True}):
            data = dict(manifest(['wide_ratio']), download_policy=policy)
            with self.subTest(policy=policy), patch.object(legacy_import, 'checked_build') as checked:
                with self.assertRaises(server.ApiError): server.api_build_firmware(request(data), 'fake')
                checked.assert_not_called()
        data = dict(manifest(['wide_ratio']), download_policy=dict(schema=1, date_stamp=False, output_sha256=digest))
        with patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())):
            self.assertEqual(server.api_build_firmware(request(data), 'fake').body, b'abc')

    def test_new_wide_provenance_and_date_are_checked_before_saving(self):
        class Store:
            def __init__(self): self.calls = []
            def put_build(self, output, record): self.calls.append((output, record)); return record
        store = Store()
        project = dict(camera_features=dict(wide_ratio=True, soft_glow=True))
        original = manifest(['wide_ratio', 'film'])
        saved = server._save_generated_build(store, b'abc', original, project)
        self.assertNotIn('download_policy', original)
        self.assertEqual(saved['download_policy'], dict(schema=1, date_stamp=False, output_sha256=original['output_sha256']))
        with patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())):
            self.assertEqual(server.api_build_firmware(request(saved), 'fake').status, 200)
        for features, selection in ((['wide_ratio'], dict(wide_ratio=True, soft_glow=True, date_stamp=True)),
                                    (['wide_ratio'], {}), (['film'], dict(wide_ratio=True)),
                                    (['wide_ratio'], dict(wide_ratio=1)), (None, {})):
            with self.subTest(features=features, selection=selection), self.assertRaises(server.ApiError):
                server._save_generated_build(store, b'abc', manifest(features), dict(camera_features=selection))
        self.assertEqual(len(store.calls), 1)

    def test_real_store_roundtrip_with_synthetic_bytes(self):
        model = SimpleNamespace(filename='fwdc239b.bin')
        with tempfile.TemporaryDirectory() as td, \
             patch.object(legacy_import.models, 'from_sha', return_value=model):
            store = Store(td)
            record = dict(manifest(['wide_ratio']), input_sha256='synthetic', output_filename=model.filename,
                          checks=[dict(ok=True)], project_id='p_000000000000', created_at='test')
            new = server._save_generated_build(store, b'abc', record, dict(camera_features={'wide_ratio': True}))
            req = SimpleNamespace(store=store)
            self.assertEqual(server.api_build_firmware(req, new['build_id']).body, b'abc')
            old = store.put_build(b'abc', record)
            with self.assertRaises(server.ApiError) as error:
                server.api_build_firmware(req, old['build_id'])
            self.assertEqual(error.exception.status, 422)

    def test_recorded_unverified_build_cannot_be_downloaded(self):
        for flags in (['date_stamp'], ['soft_glow', 'date_stamp'], ['film', 'date_stamp']):
            with self.subTest(flags=flags), patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())) as checked:
                with self.assertRaises(server.ApiError) as error:
                    server.api_build_firmware(request(manifest(flags)), 'fake')
                self.assertEqual(error.exception.status, 422)
                checked.assert_not_called()

    def test_missing_or_malformed_feature_list_is_not_treated_as_safe(self):
        for flags in (None, [], 'wide_ratio', {}, [True], [None], ['unknown_future_feature']):
            with self.subTest(flags=flags), patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())) as checked:
                with self.assertRaises(server.ApiError) as error:
                    server.api_build_firmware(request(manifest(flags)), 'fake')
                self.assertEqual(error.exception.status, 422)
                checked.assert_not_called()
        missing = {'output_sha256': hashlib.sha256(b'abc').hexdigest()}
        with patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())) as checked:
            with self.assertRaises(server.ApiError): server.api_build_firmware(request(missing), 'fake')
            checked.assert_not_called()

    def test_recorded_nonwide_build_keeps_existing_verification(self):
        for flags in (['name'], ['soft_glow'], ['film']):
            with self.subTest(flags=flags), \
                 patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())) as checked:
                response = server.api_build_firmware(request(manifest(flags)), 'fake')
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body, b'abc')
                checked.assert_called_once()

    def test_download_rechecks_actual_return_bytes(self):
        # Model a change after checked_build has returned but before bytes are sent.
        with patch.object(legacy_import, 'checked_build', return_value=(SimpleNamespace(filename='fake.bin'), FakeOutput())):
            changed = manifest(['name'])
            changed['output_sha256'] = hashlib.sha256(b'other').hexdigest()
            with self.assertRaises(server.ApiError) as error:
                server.api_build_firmware(request(changed), 'fake')
            self.assertEqual(error.exception.status, 409)

    def test_nonobject_manifest_is_rejected(self):
        with patch.object(legacy_import, 'checked_build') as checked:
            with self.assertRaises(server.ApiError): server.api_build_firmware(request([]), 'fake')
            checked.assert_not_called()

    def test_allowed_download_still_checks_recorded_hash(self):
        # Tiny artificial bytes only: this is not firmware or a modified payload.
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            output = folder / 'fwdc239b.bin'
            output.write_bytes(b'abc')
            data = dict(features=['name'], input_sha256='fake', output_filename=output.name,
                        output_sha256=hashlib.sha256(b'abc').hexdigest(), checks=[dict(ok=True)])
            record = folder / 'manifest.json'
            record.write_text(json.dumps(data))
            req = SimpleNamespace(store=SimpleNamespace(build_path=lambda *args: record))
            model = SimpleNamespace(filename=output.name)
            with patch.object(legacy_import.models, 'from_sha', return_value=model):
                self.assertEqual(server.api_build_firmware(req, 'fake').body, b'abc')
                output.write_bytes(b'changed')
                with self.assertRaises(legacy_import.StoreError):
                    server.api_build_firmware(req, 'fake')


if __name__ == '__main__': unittest.main()
