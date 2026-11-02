"""GR III 2.20 / GR IIIx 1.70, batches 2-4: films, 柔光 and the shutdown image through fwaddr.

The pure tests run everywhere. With DARKSLIDE_GR3_220_BIN / DARKSLIDE_GR3X_170_BIN pointing at
the official files the features are built for real (in memory only) and checked independently,
with the rescue gate and both bodies' protected windows. The 2.10 / 1.60 outputs are covered by
the existing tests, which run with the relocation layer present but inactive.
"""
import hashlib
import os
import struct
import unittest
from pathlib import Path

from darkslide import (a_shutdown, body_target, composite_verify, compositor, film_inplace_integrity,
                       film_precompiled, film_writer, fwaddr, fwread, gr3x_generation, hdf_guard, models,
                       rescue_gate, shared_shutdown, soft_glow, soft_glow_verify)

BINS = {models.GR3_220: Path(os.environ.get("DARKSLIDE_GR3_220_BIN", "/nonexistent/fwdc239b.bin")),
        models.GR3X_170: Path(os.environ.get("DARKSLIDE_GR3X_170_BIN", "/nonexistent/fwdc243b.bin"))}
FILMS = {8: "portra400", 9: "pro400h", 10: "gold200", 11: "ektar100"}
JPEG = Path(__file__).parent / "fixtures" / "shutdown_gradient.jpg"


class Relocation(unittest.TestCase):
    def test_inactive_by_default(self):
        self.assertIsNone(fwaddr.current())
        self.assertEqual(fwaddr.A(0x53DC2BF8), 0x53DC2BF8)
        self.assertEqual(fwaddr.layout(), fwread.V210)
        self.assertEqual(film_writer.hdr_fixes(), film_writer.HDR_FIXES)
        with fwaddr.firmware(models.GR3):
            self.assertIsNone(fwaddr.current())

    def test_relocated_and_refused(self):
        with fwaddr.firmware(models.GR3_220):
            self.assertEqual(fwaddr.A(0x53DC2BF8), 0x53DC2D30)        # reclaimed film data (+0x138)
            self.assertEqual(fwaddr.A(0x55002BAC), 0x55002BB4)        # menu text table (+8)
            self.assertEqual(fwaddr.A((0x536B93D0, 0xE5D4309B)), (0x536B94C0, 0xE5D4309B))  # words pass
            with self.assertRaises(fwaddr.RelocationError):
                fwaddr.A(0x536BF670)                                  # HDF collabo window: structure changed
            self.assertEqual(fwaddr.family('GRIII-2.10-v29'), 'GRIII-2.20-v29')
        self.assertEqual(fwaddr.A(0x53DC2BF8), 0x53DC2BF8)

    def test_pinned_data(self):
        fwaddr._table()                                               # SHA-pinned table loads
        self.assertTrue(film_precompiled.BUNDLE_220.exists())
        path, digest = (film_inplace_integrity.REFERENCE_220, film_inplace_integrity.REFERENCE_220_SHA256)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(len(soft_glow_verify.windows(True)), 79)
        self.assertEqual(len(soft_glow_verify.windows(False)), 79)

    def test_shutdown_layout(self):
        L = shared_shutdown.layout(models.GR3_220)
        self.assertEqual(L['request_ids'], (2, 3, 10, 11, 12, 13, 14))
        self.assertEqual([i + 1 for i in L['table_indices']], list(L['request_ids']))
        self.assertEqual(L['expected'][11], L['diary_desc'])
        self.assertIs(shared_shutdown.layout(models.GR3), shared_shutdown.L210)
        self.assertEqual(shared_shutdown.L210['request_ids'], shared_shutdown.REQUEST_IDS)
        self.assertEqual(body_target.shutdown_report("GR III", models.GR3_220)["missing_requests"], [])


@unittest.skipUnless(all(p.is_file() for p in BINS.values()), "GR III 2.20 / GR IIIx 1.70 originals required")
class Real(unittest.TestCase):
    def check(self, model, official, output):
        before, after = fwread.unpack(official), fwread.unpack(output)
        self.assertEqual(len(after), model.payload_size)
        self.assertTrue(rescue_gate.all_ok(rescue_gate.gate(official, output, model.id)))
        bodies = ("GR III", "GR III HDF") if model.name == "GR III" else ("GR IIIx", "GR IIIx HDF")
        for body in bodies:
            self.assertEqual(hdf_guard.inspect(before, body, after)["status"], "protected_windows_unchanged")
        p = fwread.Payload(after, models.layout(model), pinned=False)
        return p

    def test_gr3_220_films_glow_shutdown(self):
        model, official = models.GR3_220, BINS[models.GR3_220].read_bytes()
        t, jpeg = models.template_for(model), JPEG.read_bytes()
        for slots, glow, picture in (([8], False, None), ([10], True, None), ([8, 9, 10, 11], True, jpeg)):
            with self.subTest(slots=slots, glow=glow, shutdown=picture is not None):
                edits = [dict(kind="film", slot=s, film=FILMS[s]) for s in slots] + ([soft_glow.edit()] if glow else [])
                r = compositor.build_inplace_composite(official, edits, t, picture)
                checks = composite_verify.verify(official, r, edits, t, picture, film_layout="inplace_history_cache_exit_v29")
                self.assertTrue(checks and all(c["ok"] for c in checks), [c["id"] for c in checks if not c["ok"]])
                ids = {c["id"] for c in checks}
                self.assertIn("film_inplace_shared_bytes_match_reference", ids)
                p = self.check(model, official, r["output"])
                if picture is not None:
                    self.assertIn("a_path_seven_requests_fixed_slot_jpeg", ids)
                    self.assertEqual({p.u32(0x55002B78 + 4 * q) for q in (2, 3, 10, 11, 12, 13, 14)}, {0x53B5EE38})

    def test_gr3x_170_candidate(self):
        model, official = models.GR3X_170, BINS[models.GR3X_170].read_bytes()
        t, jpeg = models.template_for(model), JPEG.read_bytes()
        edits = [dict(kind="film", slot=s, film=f) for s, f in FILMS.items()] + [soft_glow.edit()]
        with fwaddr.firmware(model):
            output, checks = gr3x_generation.curve_candidate(official, edits, t, {}, jpeg, model)
        self.assertTrue(checks and all(c["ok"] for c in checks), [c["id"] for c in checks if not c["ok"]])
        p = self.check(model, official, output)
        self.assertEqual({p.u32(0x55002B78 + 4 * q) for q in (2, 3, 10, 11, 12, 13, 14)}, {0x53B5EE38})

    def test_film_prepare_route(self):
        """The read-only /film-prepare check runs at relocated addresses on 2.20 / 1.70 (both bodies)."""
        import http.client, json, tempfile, threading
        from darkslide import server
        changes = {str(s): {"film": f} for s, f in FILMS.items()}
        with tempfile.TemporaryDirectory() as root:
            httpd = server.LocalServer(("127.0.0.1", 0), server.App(root))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            port = httpd.server_address[1]

            def call(method, path, body, filename=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=900)
                headers = {"Host": "127.0.0.1:%d" % port, "Origin": "http://127.0.0.1:%d" % port}
                if isinstance(body, dict):
                    body = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
                if filename:
                    headers["X-Filename"] = filename
                conn.request(method, path, body=body, headers=headers)
                r = conn.getresponse(); data = r.read(); conn.close()
                return r.status, json.loads(data)
            try:
                for model in models.NEW_220:
                    self.assertEqual(call("POST", "/api/firmware", BINS[model].read_bytes(), model.filename)[0], 200)
                    for body in (model.name, model.name + " HDF"):
                        with self.subTest(model=model.id, body=body):
                            st, p = call("POST", "/api/projects", dict(title="t", input_sha256=model.input_sha, camera_variant=body))
                            self.assertEqual(st, 200, p)
                            call("PUT", "/api/projects/%s/draft" % p["id"], dict(changes=changes))
                            st, saved = call("POST", "/api/projects/%s/save" % p["id"], b"")
                            self.assertEqual(st, 200, saved)
                            st, r = call("POST", "/api/projects/%s/film-prepare" % p["id"],
                                         dict(expected_changes=saved["changes"], request_id="t"))
                            self.assertEqual(st, 200, r)
                            self.assertTrue(r["data_verified"] and r["shared_reference_verified"] and r["coverage_verified"])
                            self.assertEqual(r["body"]["status"], "protected_windows_unchanged")
                            self.assertEqual(r["slots"], [dict(slot=s, film=FILMS.get(s), factory=s not in FILMS)
                                                         for s in (2, 8, 9, 10, 11)])
            finally:
                httpd.shutdown()

    def test_hdf_sparse_2_20_still_guards(self):
        """inspect_sparse on 2.20 / 1.70 still flags a write into a protected HDF window."""
        for model in models.NEW_220:
            with self.subTest(model=model.id):
                before = fwread.unpack(BINS[model].read_bytes())
                name, a, b = hdf_guard.WINDOWS_220[0]
                old = bytes(before[a - 0x53000000 + 0x10870:a - 0x53000000 + 0x10870 + 4])
                w = (dict(address=a, expected=old, replacement=bytes(x ^ 1 for x in old)),)
                r = hdf_guard.inspect_sparse(before, model.name + " HDF", w)
                self.assertEqual(r["status"], "review_required")
                self.assertIn(name, r["changed_windows"])

    def test_shutdown_only_seven_routes(self):
        for model in models.NEW_220:
            with self.subTest(model=model.id):
                before = fwread.unpack(BINS[model].read_bytes())
                after, details = a_shutdown.apply_a_shutdown(before, JPEG.read_bytes(), model.id)
                self.assertEqual(details["request_ids"], [2, 3, 10, 11, 12, 13, 14])
                self.assertEqual(len(after), len(before))


if __name__ == "__main__":
    unittest.main()
