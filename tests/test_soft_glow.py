"""柔光 (soft glow) v22.6: request shape, the pinned window file, the independent readback, and (with the
real official files, DARKSLIDE_OFFICIAL_BIN / DARKSLIDE_IIIX_BIN) every generation route, alone and with
every other change. GR III with only 柔光 must decode to exactly the delivered v22.6 test firmware. In
memory or in temporary folders only; nothing is flashed or written to a camera card."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from darkslide import (body_target, composite_verify, compositor, edit_core, fwread, generate, gr3x_generation,
                       menu_order, models, rescue_gate, soft_glow, soft_glow_verify, template as tpl)
from darkslide.store import Store

OFFICIAL = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))
IIIX = Path(os.environ.get("DARKSLIDE_IIIX_BIN", "/nonexistent/fwdc243b.bin"))
JPEG = Path(__file__).parent / "fixtures" / "shutdown_gradient.jpg"
REVERSED = list(menu_order.FACTORY_STILL[::-1])
ORDER = dict(schema=1, still_slots=REVERSED, video_slots=menu_order.video_for(REVERSED))


class Shape(unittest.TestCase):
    def test_request(self):
        soft_glow.validate_request(soft_glow.edit())
        for bad in (dict(kind="soft_glow"), dict(kind="soft_glow", version="v22.4"),
                    dict(kind="soft_glow", version="v22.6", level=2), dict(kind="glow", version="v22.6")):
            with self.assertRaises(edit_core.BuildError, msg=repr(bad)):
                soft_glow.validate_request(bad)

    def test_window_file_is_pinned_ordered_and_fixed(self):
        rows = soft_glow_verify.windows()
        data = json.loads(soft_glow.DATA.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], soft_glow.VERSION)
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(len(old) == len(new) and old != new for _, old, new in rows))
        # The two glow modules are in it (effect 5216 B, platform 5044 B).
        sizes = {at: len(new) for at, _, new in rows}
        self.assertEqual(sizes[0x53DF2668 - 0x53000000 + 0x10870], 5216)
        self.assertEqual(sizes[0x53E01B68 - 0x53000000 + 0x10870], 5044)
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp) / "w.json"
            changed.write_bytes(soft_glow.DATA.read_bytes().replace(b'"windows":[[', b'"windows":[ ['))
            with patch.object(soft_glow_verify, "DATA", changed):
                with self.assertRaisesRegex(soft_glow_verify.SoftGlowError, "changed"):
                    soft_glow_verify.windows()

    def test_compositor_refuses_before_decoding(self):
        with patch.object(edit_core, "_decode", side_effect=AssertionError("must not decode")):
            for edits in ([soft_glow.edit(), soft_glow.edit()], [dict(kind="soft_glow", version="v1")]):
                with self.assertRaises(compositor.CompositeError):
                    compositor.build_composite(b"", edits, tpl.load())

    def test_verifier_refuses_other_payloads(self):
        with self.assertRaises(soft_glow_verify.SoftGlowError):
            soft_glow_verify.verify(bytes(soft_glow_verify.PAYLOAD_SIZE), bytes(soft_glow_verify.PAYLOAD_SIZE))


class Real:
    """Shared checks; subclasses set raw/template/model/bodies."""

    def check(self, edits, jpeg=None, inplace=False, layout="legacy_arena_v1"):
        build = compositor.build_inplace_composite if inplace else compositor.build_composite
        result = build(self.raw, edits, self.t, jpeg)
        checks = composite_verify.verify(self.raw, result, edits, self.t, jpeg, film_layout=layout)
        self.assertTrue(composite_verify.all_ok(checks), [c for c in checks if not c["ok"]])
        self.assertIn("soft_glow_windows", [c["id"] for c in checks])
        gate = rescue_gate.gate(self.raw, result["output"], self.model.id)
        self.assertTrue(rescue_gate.all_ok(gate), [c for c in gate if not c["ok"]])
        after = bytes(fwread.unpack(result["output"]))
        self.assertEqual(len(after), len(self.before))
        for body in self.bodies:
            report = body_target.build_report(self.before, body, after)
            self.assertEqual(report["status"], "protected_windows_unchanged", (body, report["changed_windows"]))
        for at, _, new in soft_glow_verify.windows():
            self.assertEqual(after[at:at + len(new)], new)
        return result, after

    def others(self):
        stock = fwread.Payload(self.before, models.layout(self.model))
        edits = []
        for slot in self.t["slots"][:12]:
            _, native = stock.icon_pixels(slot["icon_id"])
            px = bytearray(fwread.icon_to_rgba(native))
            px[(20 * 40 + 20) * 4] ^= 0x33
            edits.append(dict(kind="icon", slot=slot["no"], icon_id=slot["icon_id"], pixels=bytes(px)))
        for slot in self.t["slots"][1:12]:
            limit = slot.get("name_max", len(slot["factory_en"]))
            edits.append(dict(kind="name", slot=slot["no"], string_id=slot["string_id"],
                              text=("N%d" % slot["no"] + "Z" * 16)[:limit]))
        return edits

    def test_alone(self):
        result, after = self.check([soft_glow.edit()])
        self.assertEqual(result["features"], ["soft_glow"])
        changed = sum(a != b for a, b in zip(self.before[:-4], after[:-4]))
        self.assertEqual(changed, sum(sum(x != y for x, y in zip(old, new)) for _, old, new in soft_glow_verify.windows()))
        return after

    def test_with_every_other_change_and_the_order(self):
        edits = self.others() + [menu_order.edit_for(ORDER), soft_glow.edit()]
        if self.model == models.GR3:
            edits.append(dict(kind="grain_menu", slot=12, mode=22))
        result, _ = self.check(edits, JPEG.read_bytes())
        self.assertIn("soft_glow", result["features"])
        self.assertIn("menu_order", result["features"])

    def saved(self, body, changes, features, run):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            sha = store.put_input(self.raw)
            pid = store.create_project("柔光", sha, self.t, target_body=body)["id"]
            store.put_draft(pid, changes, self.t, menu_order=ORDER, camera_features=features)
            project = store.save(pid, self.t)
            output, manifest = run(store, project, self.t, self.raw)
            self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
            self.assertIn("soft_glow_windows", [c["id"] for c in manifest["checks"]])
            self.assertIn("soft_glow", manifest["features"])
            self.assertEqual(manifest["summary"][0]["preset"], "柔光")
            self.assertIn("v22.6", manifest["verification"])
            self.assertEqual(manifest["body"]["status"], "protected_windows_unchanged")
            after = bytes(fwread.unpack(output))
            for at, _, new in soft_glow_verify.windows():
                self.assertEqual(after[at:at + len(new)], new)
            self.assertEqual(store.builds(), [])
            return manifest

    def test_wide_ratio_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            sha = store.put_input(self.raw)
            pid = store.create_project("宽幅", sha, self.t, target_body=self.bodies[0])["id"]
            caps = models.capabilities
            with patch.object(models, "capabilities", lambda m: dict(caps(m), wide_ratio=True)):
                store.put_draft(pid, {}, self.t, camera_features=dict(soft_glow=True, wide_ratio=True))
                project = store.save(pid, self.t)
            run = generate.run if self.model == models.GR3 else gr3x_generation.run
            with self.assertRaisesRegex(generate.GenerateError, "65:24 宽幅还没有接进固件生成"):
                run(store, project, self.t, self.raw)


@unittest.skipUnless(OFFICIAL.exists(), "需要 DARKSLIDE_OFFICIAL_BIN（GR III 2.10 官方原件）")
class RealGR3(Real, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.t, cls.model = OFFICIAL.read_bytes(), tpl.load(), models.GR3
        cls.before = bytes(fwread.unpack(cls.raw))
        cls.bodies = ("GR III", "GR III HDF")

    def test_alone(self):
        after = super().test_alone()
        # Exactly the delivered v22.6 test firmware, decoded (checksum word included).
        self.assertEqual(hashlib.sha256(after).hexdigest(), soft_glow_verify.V226_PAYLOAD_SHA256)

    def test_with_four_films_on_the_1_0_route(self):
        films = [dict(kind="film", slot=s, film=f) for s, f in zip((8, 9, 10, 11), ("gold200", "portra400", "ektar100", "5219"))]
        result, _ = self.check(films + [soft_glow.edit(), menu_order.edit_for(ORDER)], inplace=True,
                               layout="inplace_history_cache_exit_v29")
        self.assertEqual(result["film_layout"], "inplace_history_cache_exit_v29")

    def test_saved_projects_through_both_routes(self):
        self.saved("GR III", {}, dict(soft_glow=True), generate.run)
        self.saved("GR III HDF", {"8": {"film": "gold200"}, "9": {"film": "portra400"}}, dict(soft_glow=True),
                   generate.run_inplace)


@unittest.skipUnless(IIIX.exists(), "需要 DARKSLIDE_IIIX_BIN（GR IIIx 1.60 官方原件）")
class RealIIIx(Real, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.t, cls.model = IIIX.read_bytes(), tpl.load("gr3x_v160.json"), models.GR3X
        cls.before = bytes(fwread.unpack(cls.raw))
        cls.bodies = ("GR IIIx", "GR IIIx HDF")

    def test_saved_projects_through_the_candidate_route(self):
        manifest = self.saved("GR IIIx", {}, dict(soft_glow=True), gr3x_generation.run)
        self.assertEqual(manifest["decoded_growth_bytes"], 0)
        manifest = self.saved("GR IIIx HDF", {"8": {"film": "gold200"}, "9": {"film": "portra400"}},
                              dict(soft_glow=True), gr3x_generation.run)
        self.assertIn("iiix_complete_curve_readback", [c["id"] for c in manifest["checks"]])


if __name__ == "__main__":
    unittest.main()
