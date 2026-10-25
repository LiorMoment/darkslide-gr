"""F31 menu order on the real official files, in memory or in a temporary data folder only.

GR III 2.10 (DARKSLIDE_OFFICIAL_BIN): every build goes through the composite checker (whose menu step
is the independent menu_order_verify), the rescue gate and the HDF protected windows of both bodies;
sorting alone is also checked with the stricter sorting-only readback. The HTTP run stores its build
in the test's temporary folder, which is removed afterwards. GR IIIx 1.60 (DARKSLIDE_IIIX_BIN, 2026-10-25):
the same seven tables are written and read back the same way, alone and with other changes, through the
compositor and through the saved-project IIIx candidate route, with the IIIx rescue gate and both IIIx
body protected windows. Nothing here is flashed or written to a camera card."""
import hashlib
import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path

import test_http
from darkslide import (body_target, composite_verify, compositor, fwread, generate, menu_order,
                       menu_order_verify, models, rescue_gate, template as tpl)
from darkslide.store import Store

OFFICIAL = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))
IIIX = Path(os.environ.get("DARKSLIDE_IIIX_BIN", "/nonexistent/fwdc243b.bin"))
JPEG = Path(__file__).parent / "fixtures" / "shutdown_gradient.jpg"
FACTORY = list(menu_order.FACTORY_STILL)
OFF = lambda va: va - 0x53000000 + 0x10870
REVERSED = FACTORY[::-1]
NEGA_FIRST = [12] + [s for s in FACTORY if s != 12]
POSI_LAST = [s for s in FACTORY if s != 7] + [7]


def order(still):
    return dict(schema=1, still_slots=list(still), video_slots=menu_order.video_for(still))


def icon_edit(p, slot, flip):
    _, native = p.icon_pixels(slot["icon_id"])
    px = bytearray(fwread.icon_to_rgba(native))
    px[(20 * 40 + 20) * 4] ^= flip  # one visible pixel, alpha untouched
    return dict(kind="icon", slot=slot["no"], icon_id=slot["icon_id"], pixels=bytes(px))


def longest_name(slot):
    limit = slot.get("name_max", len(slot["factory_en"]))
    return dict(kind="name", slot=slot["no"], string_id=slot["string_id"], text=("N%d" % slot["no"] + "Z" * 16)[:limit])


@unittest.skipUnless(OFFICIAL.exists(), "需要 DARKSLIDE_OFFICIAL_BIN（GR III 2.10 官方原件）")
class RealGR3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = OFFICIAL.read_bytes()
        cls.t = tpl.load()
        cls.before = fwread.unpack(cls.raw)
        cls.stock = fwread.Payload(cls.before)

    def check(self, edits, jpeg=None, inplace=False):
        build = compositor.build_inplace_composite if inplace else compositor.build_composite
        result = build(self.raw, edits, self.t, jpeg)
        layout = "inplace_history_cache_exit_v29" if inplace else "legacy_arena_v1"
        checks = composite_verify.verify(self.raw, result, edits, self.t, jpeg, film_layout=layout)
        self.assertTrue(composite_verify.all_ok(checks), [c for c in checks if not c["ok"]])
        self.assertEqual([c["id"] for c in checks].count("menu_order_tables"),
                         sum(e["kind"] == "menu_order" for e in edits))
        gate = rescue_gate.gate(self.raw, result["output"])
        self.assertTrue(rescue_gate.all_ok(gate), [c for c in gate if not c["ok"]])
        after = fwread.unpack(result["output"])
        self.assertEqual(len(after), len(self.before))
        for body in ("GR III", "GR III HDF"):
            report = body_target.build_report(self.before, body, after)
            self.assertEqual(report["status"], "protected_windows_unchanged", (body, report["changed_windows"]))
        return result, after

    def tables(self, after, still):
        tone = {s: s + 10 for s in FACTORY}
        raw = {s: i + 1 for i, s in enumerate(FACTORY)}   # RAW option numbers 1..12 in the factory row order
        for va in menu_order.STILL_VA:
            self.assertEqual(list(after[OFF(va):OFF(va) + 12]), [tone[s] for s in still])
        for va in menu_order.VIDEO_VA:
            self.assertEqual(list(after[OFF(va):OFF(va) + 10]), [tone[s] for s in menu_order.video_for(still)])
        self.assertEqual(list(after[OFF(menu_order.RAW_VA):OFF(menu_order.RAW_VA) + 12]), [raw[s] for s in still])

    def test_factory_tables_in_the_official_file(self):
        self.tables(self.before, FACTORY)

    def test_sorting_alone_changes_only_the_seven_tables(self):
        for still in (REVERSED, NEGA_FIRST, POSI_LAST):
            with self.subTest(order=still):
                request = order(still)
                result, after = self.check([menu_order.edit_for(request)])
                self.assertEqual(result["features"], ["menu_order"])
                self.tables(after, still)
                got = menu_order_verify.verify_sorting_only(self.before, after, request, finalized=True)
                self.assertTrue(got["checksum_verified"] and got["outside_authorized_spans_unchanged"])

    def test_with_every_other_change(self):
        slots = self.t["slots"]
        edits = [icon_edit(self.stock, s, 0x33) for s in slots[:12]]
        edits += [longest_name(s) for s in slots[1:12]]
        edits.append(dict(kind="grain_menu", slot=12, mode=22))
        edits.append(menu_order.edit_for(order(REVERSED)))
        result, after = self.check(edits, JPEG.read_bytes())
        self.assertEqual(set(result["features"]), {"icon", "name", "grain_menu", "shutdown", "menu_order"})
        self.tables(after, REVERSED)
        q = fwread.Payload(after, pinned=False)
        for e in edits:
            if e["kind"] == "name":
                self.assertEqual(q.string(e["string_id"], "en")["text"], e["text"])

    def test_with_four_films_on_the_1_0_route(self):
        films = [dict(kind="film", slot=s, film=f) for s, f in zip((8, 9, 10, 11), ("gold200", "portra400", "ektar100", "5219"))]
        names = [dict(kind="name", slot=8, string_id=275, text="Gold 200")]
        result, after = self.check(films + names + [menu_order.edit_for(order(NEGA_FIRST))], inplace=True)
        self.assertEqual(result["film_layout"], "inplace_history_cache_exit_v29")
        self.tables(after, NEGA_FIRST)

    def test_saved_projects_through_both_generation_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            sha = store.put_input(self.raw)
            for body, changes, still in (("GR III", {}, REVERSED), ("GR III HDF", {"7": {"name": "Posi Warm"}}, POSI_LAST),
                                         ("GR III HDF", {"8": {"film": "gold200"}, "9": {"film": "portra400"}}, NEGA_FIRST)):
                with self.subTest(body=body, changes=changes):
                    pid = store.create_project("排序", sha, self.t, target_body=body)["id"]
                    store.put_draft(pid, changes, self.t, menu_order=order(still))
                    project = store.save(pid, self.t)
                    films = any("film" in c for c in changes.values())
                    run = generate.run_inplace if films else generate.run
                    output, manifest = run(store, project, self.t, self.raw)
                    self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
                    titles = [c["title"] for c in manifest["checks"]]
                    self.assertIn(generate.CHECK_TITLES["menu_order_tables"], titles)
                    self.assertEqual(manifest["menu_order"], dict(order(still), video_policy="follow_still"))
                    self.assertEqual(manifest["summary"][0]["what"], "菜单顺序")
                    self.assertIn("菜单的新顺序在普通 GR III 上已实测，其他机身还没有", manifest["verification"])
                    self.assertEqual(manifest["body"]["status"], "protected_windows_unchanged")
                    self.tables(fwread.unpack(output), still)
            self.assertEqual(store.builds(), [])   # generate.run returns bytes; only the HTTP route stores builds


class Patient(test_http.Served):
    """Served with the real identities and a long wait: importing a real file and building take
    minutes on a busy machine, while Served.call gives up after 10 seconds."""
    synthetic_identity = False

    def call(self, method, path, body=None, headers=None):
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        merged = {"Host": "127.0.0.1:%d" % self.port}
        if data is not None:
            merged["Content-Length"] = str(len(data))
        merged.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1800)
        try:
            conn.request(method, path, body=data, headers=merged)
            resp = conn.getresponse()
            return test_http.Res(resp.status, resp.headers, resp.read())
        finally:
            conn.close()


@unittest.skipUnless(OFFICIAL.exists(), "需要 DARKSLIDE_OFFICIAL_BIN（GR III 2.10 官方原件）")
class RealHttp(Patient):
    """One real build over HTTP into the test's temporary data folder (removed afterwards)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app.template = tpl.load()

    def test_sort_save_generate_download(self):
        raw = OFFICIAL.read_bytes()
        r = self.call("POST", "/api/firmware", raw, {"X-Filename": "fwdc239b.bin"})
        self.assertEqual(r.status, 200, r.body)
        r = self.call("POST", "/api/projects", dict(input_sha256=models.GR3.input_sha, title="排序", camera_variant="GR III HDF"))
        self.assertEqual(r.status, 200, r.body)
        p = r.json()
        self.assertTrue(p["capabilities"]["menu_order"])
        url = "/api/projects/%s" % p["id"]
        o = order(REVERSED)
        r = self.call("PUT", url + "/draft", dict(changes={"2": {"name": "Vivid+"}}, menu_order=o))
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.call("POST", url + "/save", {}).status, 200)
        r = self.call("POST", url + "/generate", {})
        self.assertEqual(r.status, 200, r.body)
        build = r.json()
        self.assertEqual(build["menu_order"]["still_slots"], REVERSED)
        firmware = self.call("GET", "/api/builds/%s/firmware" % build["build_id"])
        self.assertEqual(firmware.status, 200)
        self.assertEqual(test_http.sha256(firmware.body), build["output_sha256"])
        after = fwread.unpack(firmware.body)
        self.assertEqual(list(after[OFF(menu_order.STILL_VA[0]):OFF(menu_order.STILL_VA[0]) + 12]), [s + 10 for s in REVERSED])
        self.assertEqual(len(self.store.builds()), 1)   # inside this test's temporary folder only


@unittest.skipUnless(IIIX.exists(), "需要 DARKSLIDE_IIIX_BIN（GR IIIx 1.60 官方原件）")
class RealIIIx(RealGR3):
    """GR IIIx 1.60: same tables, same bytes as GR III 2.10 (checked 2026-10-25), so the same checks apply."""

    @classmethod
    def setUpClass(cls):
        cls.raw = IIIX.read_bytes()
        cls.t = tpl.load("gr3x_v160.json")
        cls.before = fwread.unpack(cls.raw)
        cls.stock = fwread.Payload(cls.before, models.layout(models.GR3X))

    def check(self, edits, jpeg=None, inplace=False):
        self.assertFalse(inplace)
        result = compositor.build_composite(self.raw, edits, self.t, jpeg)
        checks = composite_verify.verify(self.raw, result, edits, self.t, jpeg)
        self.assertTrue(composite_verify.all_ok(checks), [c for c in checks if not c["ok"]])
        self.assertEqual([c["id"] for c in checks].count("menu_order_tables"),
                         sum(e["kind"] == "menu_order" for e in edits))
        gate = rescue_gate.gate(self.raw, result["output"], models.GR3X.id)
        self.assertTrue(rescue_gate.all_ok(gate), [c for c in gate if not c["ok"]])
        after = fwread.unpack(result["output"])
        self.assertEqual(len(after), len(self.before))
        for body in ("GR IIIx", "GR IIIx HDF"):
            report = body_target.build_report(self.before, body, after)
            self.assertEqual(report["status"], "protected_windows_unchanged", (body, report["changed_windows"]))
        return result, after

    def test_payload_identity(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(), models.GR3X.payload_sha)
        self.assertEqual(menu_order_verify.IIIX_PAYLOAD_SHA256, models.GR3X.payload_sha)

    def test_with_every_other_change(self):
        slots = self.t["slots"]
        edits = [icon_edit(self.stock, s, 0x33) for s in slots[:12]]
        edits += [longest_name(s) for s in slots[1:12]]
        edits.append(menu_order.edit_for(order(REVERSED)))
        result, after = self.check(edits, JPEG.read_bytes())
        self.assertEqual(set(result["features"]), {"icon", "name", "shutdown", "menu_order"})
        self.tables(after, REVERSED)
        q = fwread.Payload(after, models.layout(models.GR3X), pinned=False)
        for e in edits:
            if e["kind"] == "name":
                self.assertEqual(q.string(e["string_id"], "en")["text"], e["text"])

    def test_with_four_films_on_the_1_0_route(self):
        self.skipTest("IIIx 胶片走候选路线，见 test_saved_projects_through_both_generation_routes")

    def test_gr3_writer_refuses_the_iiix_payload_with_the_gr3_template(self):
        with self.assertRaisesRegex(menu_order.MenuOrderError, "官方 GR III 2.10"):
            menu_order.apply(self.before, order(REVERSED), tpl.load())

    def test_saved_projects_through_both_generation_routes(self):
        from darkslide import gr3x_generation
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            sha = store.put_input(self.raw)
            for body, changes, still in (("GR IIIx", {}, REVERSED), ("GR IIIx HDF", {"7": {"name": "Posi Warm"}}, POSI_LAST),
                                         ("GR IIIx", {"8": {"film": "gold200"}, "9": {"film": "portra400"}}, NEGA_FIRST)):
                with self.subTest(body=body, changes=changes):
                    pid = store.create_project("排序", sha, self.t, target_body=body)["id"]
                    store.put_draft(pid, changes, self.t, menu_order=order(still))
                    project = store.save(pid, self.t)
                    output, manifest = gr3x_generation.run(store, project, self.t, self.raw)
                    self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
                    ids = [c["id"] for c in manifest["checks"]]
                    self.assertIn("menu_order_tables", ids)
                    if any("film" in c for c in changes.values()):
                        self.assertIn("iiix_complete_curve_readback", ids)
                    self.assertEqual(manifest["menu_order"], dict(order(still), video_policy="follow_still"))
                    self.assertEqual(manifest["summary"][0]["what"], "菜单顺序")
                    self.assertIn("还没在 GR IIIx 上实测", manifest["verification"])
                    self.assertEqual(manifest["body"]["status"], "protected_windows_unchanged")
                    self.assertEqual(len(fwread.unpack(output)), len(self.before))   # decoded payload never grows
                    self.tables(fwread.unpack(output), still)
                    gate = rescue_gate.gate(self.raw, output, models.GR3X.id)
                    self.assertTrue(rescue_gate.all_ok(gate), [c for c in gate if not c["ok"]])
            self.assertEqual(store.builds(), [])


@unittest.skipUnless(IIIX.exists(), "需要 DARKSLIDE_IIIX_BIN（GR IIIx 1.60 官方原件）")
class RealIIIxHttp(Patient):

    def test_iiix_project_sorts_saves_and_writes(self):
        raw = IIIX.read_bytes()
        r = self.call("POST", "/api/firmware", raw, {"X-Filename": "fwdc243b.bin"})
        self.assertEqual(r.status, 200, r.body)
        r = self.call("POST", "/api/projects", dict(input_sha256=models.GR3X.input_sha, title="IIIx", camera_variant="GR IIIx HDF"))
        self.assertEqual(r.status, 200, r.body)
        p = r.json()
        self.assertTrue(p["capabilities"]["menu_order"])
        self.assertTrue(p["capabilities"]["menu_order_write"])
        url = "/api/projects/%s" % p["id"]
        r = self.call("PUT", url + "/draft", dict(changes={}, menu_order=order(REVERSED)))
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.json()["draft"]["menu_order"]["still_slots"], order(REVERSED)["still_slots"])
        self.assertEqual(self.call("POST", url + "/save", {}).status, 200)
        r = self.call("POST", url + "/iiix-candidate", dict(expected_changes={}, recipe_extension=False))
        self.assertEqual(r.status, 200, r.body)
        build = r.json()
        self.assertEqual(build["menu_order"]["still_slots"], REVERSED)
        firmware = self.call("GET", "/api/builds/%s/firmware" % build["build_id"])
        self.assertEqual(firmware.status, 200)
        self.assertEqual(test_http.sha256(firmware.body), build["output_sha256"])
        after = fwread.unpack(firmware.body)
        self.assertEqual(list(after[OFF(menu_order.STILL_VA[0]):OFF(menu_order.STILL_VA[0]) + 12]), [s + 10 for s in REVERSED])
        # factory order again: the draft carries none
        r = self.call("PUT", url + "/draft", dict(changes={}, menu_order=None))
        self.assertEqual(r.status, 200, r.body)
        self.assertIsNone(r.json()["draft"]["menu_order"])


if __name__ == "__main__":
    unittest.main()
