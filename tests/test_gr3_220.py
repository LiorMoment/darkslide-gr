"""GR III 2.20 / GR IIIx 1.70 (Ricoh, 2026-10-21), first adaptation batch.

Presets (icons, names, grain menu), the Image Control order and the rescue / HDF gates are
adapted (batch 1); films, fitting, 柔光 and the shutdown image follow in batches 2-4 (test_gr3_220_films). The pure tests run everywhere; with
DARKSLIDE_GR3_220_BIN / DARKSLIDE_GR3X_170_BIN pointing at the official files the batch is
built for real (in memory only), checked independently and the refusals are confirmed.
"""
import os
import unittest
from pathlib import Path

from darkslide import (body_target, composite_verify, compositor, fwread, grain_menu, hdf_guard, menu_order,
                       menu_order_verify, models, rescue_gate, soft_glow)

BINS = {models.GR3_220: Path(os.environ.get("DARKSLIDE_GR3_220_BIN", "/nonexistent/fwdc239b.bin")),
        models.GR3X_170: Path(os.environ.get("DARKSLIDE_GR3X_170_BIN", "/nonexistent/fwdc243b.bin"))}
ORDER = dict(schema=1, still_slots=[12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], video_slots=[12, 1, 2, 3, 5, 6, 7, 8, 9, 11])
JPEG = Path(__file__).parent / "fixtures" / "shutdown_gradient.jpg"


class Identities(unittest.TestCase):
    def test_models(self):
        for model in models.NEW_220:
            self.assertIs(models.from_sha(model.input_sha), model)
            self.assertIs(models.get(model.id), model)
            self.assertEqual(model.payload_size, 59798532)
            self.assertTrue(models.is_220(model))
            t = models.template_for(model)
            self.assertEqual((t["id"], t["model"], t["firmware"]), (model.id, model.name, model.firmware))
            self.assertEqual(t["official_inputs"][0]["sha256"], model.input_sha)
        for model in (models.GR3, models.GR3X):
            self.assertFalse(models.is_220(model))
            self.assertEqual(model.payload_size, 59718388)

    def test_closed_features(self):
        for model in models.NEW_220:
            caps = models.capabilities(model)
            self.assertTrue(caps["menu_order_write"])
            self.assertEqual(caps["full_firmware"], model == models.GR3_220)   # 1.70: IIIx candidate route, like 1.60
            # batches 2-3: films (relocated v29 writer / IIIx candidate route), LUT fitting and 柔光 are open
            for key in ("films", "soft_glow", "camera_fit", "shutdown", "new_firmware"):
                self.assertTrue(caps[key], key)
            self.assertTrue(caps["sparse_prepare"])
            for key in ("candidate_recipe_extension",):
                self.assertFalse(caps[key], key)
            self.assertEqual(caps["film_output"], model == models.GR3_220)
            self.assertEqual(caps["candidate_generation"], model == models.GR3X_170)
        for model in (models.GR3, models.GR3X):
            caps = models.capabilities(model)
            self.assertTrue(caps["films"] and caps["shutdown"])
            self.assertFalse(caps["new_firmware"])

    def test_layouts(self):
        self.assertEqual(models.layout(models.GR3_220)["string_array_ram"], 0x55002BB4)
        self.assertEqual(models.layout(models.GR3)["string_array_ram"], 0x55002BAC)
        self.assertEqual(set(rescue_gate.WINDOWS_BY_LENGTH), {59718388, 59798532})
        self.assertEqual([n for n, *_ in hdf_guard.WINDOWS_220], [n for n, *_ in hdf_guard.WINDOWS])

    def test_menu_tables_agree(self):
        # writer (virtual addresses) and independent reader (payload offsets) name the same seven tables
        offsets = tuple(v - 0x53000000 + 0x10870 for v in menu_order.STILL_VA_220 + menu_order.VIDEO_VA_220 + (menu_order.RAW_VA_220,))
        self.assertEqual(offsets, menu_order_verify.STILL_OFFSETS_220 + menu_order_verify.VIDEO_OFFSETS_220 +
                         (menu_order_verify.RAW_OFFSET_220,))
        for model in models.NEW_220:
            self.assertTrue(menu_order.supported(models.template_for(model)))

    def test_body_family(self):
        self.assertEqual(body_target.require_family("GR III HDF", "GR III", "2.20"), "GR III HDF")
        self.assertEqual(body_target.require_family("GR IIIx", "GR IIIx", "1.70"), "GR IIIx")
        with self.assertRaises(body_target.BodyTargetError):
            body_target.require_family("GR IIIx", "GR III", "2.20")


def icon_edit(p, slot):
    _, native = p.icon_pixels(slot["icon_id"])
    px = bytearray(fwread.icon_to_rgba(native))
    px[(20 * 40 + 20) * 4] ^= 0x5A
    return dict(kind="icon", slot=slot["no"], icon_id=slot["icon_id"], pixels=bytes(px))


class RealFirmware(unittest.TestCase):
    def each(self):
        found = [(m, p) for m, p in BINS.items() if p.exists()]
        if not found:
            self.skipTest("official GR III 2.20 / GR IIIx 1.70 files not available on this machine")
        for model, path in found:
            yield model, path.read_bytes(), models.template_for(model)

    def test_batch_builds_and_reads_back(self):
        for model, raw, t in self.each():
            with self.subTest(model=model.id):
                p = fwread.Payload(fwread.unpack(raw), models.layout(model))
                s = {x["no"]: x for x in t["slots"]}
                edits = [icon_edit(p, s[1]), icon_edit(p, s[12]),
                         dict(kind="name", slot=3, string_id=s[3]["string_id"], text="Mono2", menu_language="ja"),
                         dict(kind="name", slot=10, string_id=s[10]["string_id"], text="HDRTONE-XL12", menu_language="ko"),
                         dict(kind="grain_menu", slot=12, mode=22),
                         menu_order.edit_for(menu_order.normalize(ORDER))]
                r = compositor.build_composite(raw, edits, t)
                checks = composite_verify.verify(raw, r, edits, t)
                self.assertTrue(composite_verify.all_ok(checks), [c for c in checks if not c["ok"]])
                gate = rescue_gate.gate(raw, r["output"], model.id)
                self.assertTrue(rescue_gate.all_ok(gate), [c for c in gate if not c["ok"]])
                before, after = fwread.unpack(raw), fwread.unpack(r["output"])
                self.assertEqual(len(after), model.payload_size)
                for body in (("GR III", "GR III HDF") if model.name == "GR III" else ("GR IIIx", "GR IIIx HDF")):
                    self.assertEqual(body_target.build_report(before, body, after)["status"], "protected_windows_unchanged")
                q = fwread.Payload(after, models.layout(model), pinned=False)
                self.assertEqual(q.string(s[10]["string_id"], "en")["text"], "HDRTONE-XL12")
                self.assertEqual(q.string(s[3]["string_id"], "ja")["text"], "Mono2")
                self.assertEqual(q.string(s[3]["string_id"], "zh-CN")["text"], p.string(s[3]["string_id"], "zh-CN")["text"])
                menu_order_verify.verify_tables(before, after, ORDER)

    def test_refusals(self):
        jpeg = JPEG.read_bytes()
        for model, raw, t in self.each():
            with self.subTest(model=model.id):
                p = fwread.Payload(fwread.unpack(raw), models.layout(model))
                one = [icon_edit(p, t["slots"][0])]
                # films outside the in-place / IIIx candidate routes
                for edits, picture in (([dict(kind="film", slot=8, film="gold200")], None),):
                    with self.assertRaises(compositor.CompositeError):
                        compositor.build_composite(raw, edits, t, picture)

    def test_grain_and_hdf_tables_match_firmware(self):
        for model, raw, t in self.each():
            with self.subTest(model=model.id):
                before = fwread.unpack(raw)
                changed, spans = grain_menu.apply(before, model.id)
                self.assertEqual(len(spans), 2)
                report = hdf_guard.inspect(before, "GR III HDF" if model.name == "GR III" else "GR IIIx HDF")
                self.assertEqual(report["firmware"], model.firmware)
                self.assertTrue(report["direct_call_candidates"])
