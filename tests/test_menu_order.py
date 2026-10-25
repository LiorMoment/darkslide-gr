"""F31 menu order: request rules, the seven-table writer, compositor/verifier wiring,
project storage and the draft API. Synthetic payloads only; no camera image is built."""
import copy
import json
import random
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_http
from darkslide import (composite_verify, compositor, edit_core, film_inplace_apply, generate,
                       gr3x_generation, menu_order, menu_order_verify, models, template as tpl)
from darkslide.identify import digest_of as sha256
from darkslide.store import KEEP, Store, StoreError, read_json, write_json

FACTORY = list(menu_order.FACTORY_STILL)
OFF = lambda va: va - 0x53000000 + 0x10870
SIZE = OFF(menu_order.RAW_VA) + 0x200
TONE = {no: no + 10 for no in range(1, 13)}


def order(still, video=None):
    return dict(schema=1, still_slots=list(still),
                video_slots=list(video) if video is not None else menu_order.video_for(still))


def synthetic_payload():
    """Zero bytes with the factory tables at their 2.10 offsets, plus a little noise elsewhere."""
    data = bytearray(SIZE)
    for va in menu_order.STILL_VA:
        data[OFF(va):OFF(va) + 12] = bytes(TONE[s] for s in menu_order.FACTORY_STILL)
    for va in menu_order.VIDEO_VA:
        data[OFF(va):OFF(va) + 10] = bytes(TONE[s] for s in menu_order.FACTORY_VIDEO)
    data[OFF(menu_order.RAW_VA):OFF(menu_order.RAW_VA) + 12] = bytes(range(1, 13))
    rng = random.Random(31)
    for at in rng.sample(range(0, SIZE - 4, 4096), 64):
        data[at] = rng.randrange(1, 256)
    return bytes(data)


def samples():
    rng = random.Random(2026)
    out = [FACTORY[::-1], [12] + [s for s in FACTORY if s != 12], [s for s in FACTORY if s != 12] + [12],
           [7] + [s for s in FACTORY if s != 7], [s for s in FACTORY if s != 7] + [7],
           FACTORY[1:] + FACTORY[:1], FACTORY[-1:] + FACTORY[:-1]]
    for _ in range(25):
        p = FACTORY[:]
        rng.shuffle(p)
        out.append(p)
    return [p for p in out if p != FACTORY]


class Requests(unittest.TestCase):
    def test_factory_and_none_are_no_change(self):
        self.assertIsNone(menu_order.normalize(None))
        self.assertIsNone(menu_order.normalize(order(FACTORY)))

    def test_valid_order_round_trips(self):
        o = order(FACTORY[::-1])
        self.assertEqual(menu_order.normalize(o), o)
        self.assertEqual(o["video_slots"], [11, 9, 8, 7, 12, 6, 5, 3, 2, 1])
        self.assertTrue(menu_order.follows_still(o))

    def test_rejects_bad_shapes(self):
        dup = FACTORY[:-1] + [1]
        cases = [
            [], "1,2", dict(still_slots=FACTORY, video_slots=list(menu_order.FACTORY_VIDEO)),
            dict(order(FACTORY[::-1]), schema=2), dict(order(FACTORY[::-1]), schema=True),
            dict(order(FACTORY[::-1]), extra=1), order(dup, menu_order.FACTORY_VIDEO),
            order(FACTORY[:-1], menu_order.FACTORY_VIDEO), order(FACTORY + [13], menu_order.FACTORY_VIDEO),
            order([True] + FACTORY[1:], menu_order.FACTORY_VIDEO), order([1.0] + FACTORY[1:], menu_order.FACTORY_VIDEO),
            order([13] + FACTORY[1:], menu_order.FACTORY_VIDEO),
            order(FACTORY[::-1], FACTORY[::-1]), order(FACTORY[::-1], [4] + list(menu_order.FACTORY_VIDEO[1:])),
        ]
        for case in cases:
            with self.assertRaises(menu_order.MenuOrderError, msg=repr(case)):
                menu_order.normalize(case)

    def test_video_policy_is_separate_from_shape(self):
        free = order(FACTORY[::-1], list(menu_order.FACTORY_VIDEO))
        self.assertEqual(menu_order.normalize(free), free)
        self.assertFalse(menu_order.follows_still(free))

    def test_edit_requests(self):
        edit = menu_order.edit_for(order(FACTORY[::-1]))
        self.assertEqual(menu_order.request_from_edit(edit), order(FACTORY[::-1]))
        with self.assertRaisesRegex(menu_order.MenuOrderError, "不算改动"):
            menu_order.request_from_edit(menu_order.edit_for(order(FACTORY)))
        for bad in (dict(edit, kind="name"), dict(edit, slot=1), {k: edit[k] for k in edit if k != "schema"}):
            with self.assertRaises(menu_order.MenuOrderError):
                menu_order.request_from_edit(bad)


class Models(unittest.TestCase):
    """The GR III 2.10 and GR IIIx 1.60 packages; ordinary and HDF bodies share the package of their series."""

    def test_supported_templates_and_capabilities(self):
        self.assertTrue(menu_order.supported(tpl.load()))
        # 2026-10-25: IIIx 1.60 holds the seven tables at the same addresses with the same bytes, so it writes too.
        self.assertTrue(menu_order.supported(tpl.load("gr3x_v160.json")))
        self.assertFalse(menu_order.supported(dict(tpl.load("gr3x_v160.json"), id="other-model", model="GR X", firmware="9.99")))
        self.assertFalse(menu_order.supported(None))
        self.assertTrue(menu_order.editable(tpl.load()))
        self.assertTrue(menu_order.editable(tpl.load("gr3x_v160.json")))
        self.assertFalse(menu_order.editable(None))
        self.assertTrue(models.capabilities(models.GR3)["menu_order"])
        self.assertTrue(models.capabilities(models.GR3)["menu_order_write"])
        self.assertTrue(models.capabilities(models.GR3X)["menu_order"])
        self.assertTrue(models.capabilities(models.GR3X)["menu_order_write"])

    def test_hdf_bodies_use_the_same_package(self):
        from darkslide import hdf_guard
        self.assertEqual(hdf_guard.BODIES["GR III HDF"][0], "GR III")
        self.assertEqual(hdf_guard.FAMILIES["GR III"]["payload_sha256"], menu_order_verify.PAYLOAD_SHA256)
        self.assertEqual(edit_core.PAYLOAD_SHA, menu_order_verify.PAYLOAD_SHA256)
        # The seven tables sit outside every protected HDF window (static windows; the
        # direct-call windows sit in code, far below 0x53B00000).
        tables = [(va, 12) for va in menu_order.STILL_VA] + [(va, 10) for va in menu_order.VIDEO_VA] + [(menu_order.RAW_VA, 12)]
        for name, a, b in hdf_guard.WINDOWS:
            for va, n in tables:
                self.assertFalse(a < va + n and b > va, name)

    def test_table_offsets_match_the_independent_readback(self):
        self.assertEqual(tuple(OFF(va) for va in menu_order.STILL_VA), menu_order_verify.STILL_OFFSETS)
        self.assertEqual(tuple(OFF(va) for va in menu_order.VIDEO_VA), menu_order_verify.VIDEO_OFFSETS)
        self.assertEqual(OFF(menu_order.RAW_VA), menu_order_verify.RAW_OFFSET)
        self.assertEqual(menu_order.FACTORY_STILL, menu_order_verify.DEFAULT_STILL)
        self.assertEqual(menu_order.FACTORY_VIDEO, menu_order_verify.DEFAULT_VIDEO)


class Writer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = synthetic_payload()
        cls.t = tpl.load()

    def test_every_sample_matches_the_independent_readback(self):
        for still in samples():
            request = order(still)
            changed, spans = menu_order.write_tables(self.base, request, self.t)
            self.assertEqual(len(spans), 7)
            self.assertEqual(sum(s["length"] for s in spans), 78)
            # Independent comparator: seven tables right, nothing else changed (incl. the checksum tail).
            got = menu_order_verify._compare_sorting_only(self.base, changed, request, False)
            self.assertEqual(got["still_slots"], tuple(still))

    def test_negative_first_writes_tone_22_and_raw_7(self):
        still = [12] + [s for s in FACTORY if s != 12]
        changed, _ = menu_order.write_tables(self.base, order(still), self.t)
        for va in menu_order.STILL_VA:
            self.assertEqual(changed[OFF(va)], 22)
        for va in menu_order.VIDEO_VA:
            self.assertEqual(changed[OFF(va)], 22)
        self.assertEqual(changed[OFF(menu_order.RAW_VA)], 7)
        self.assertEqual(changed[-4:], self.base[-4:])

    def test_spans_cover_whole_tables(self):
        _, spans = menu_order.write_tables(self.base, order(FACTORY[::-1]), self.t)
        want = sorted([(OFF(va), 12) for va in menu_order.STILL_VA] + [(OFF(va), 10) for va in menu_order.VIDEO_VA] +
                      [(OFF(menu_order.RAW_VA), 12)])
        self.assertEqual(sorted((s["offset"], s["length"]) for s in spans), want)

    def test_refuses_changed_or_unknown_tables(self):
        for va, delta in ((menu_order.STILL_VA[1], 0), (menu_order.VIDEO_VA[2], 3), (menu_order.RAW_VA, 5)):
            bad = bytearray(self.base)
            bad[OFF(va) + delta] ^= 0x40
            with self.assertRaises(menu_order.MenuOrderError):
                menu_order.write_tables(bytes(bad), order(FACTORY[::-1]), self.t)

    def test_refuses_factory_order_bad_template_and_other_models(self):
        with self.assertRaisesRegex(menu_order.MenuOrderError, "不算改动"):
            menu_order.write_tables(self.base, order(FACTORY), self.t)
        broken = json.loads(json.dumps(self.t))
        broken["slots"][11]["mode"] = 21
        with self.assertRaises(edit_core.BuildError):
            menu_order.write_tables(self.base, order(FACTORY[::-1]), broken)
        with self.assertRaisesRegex(menu_order.MenuOrderError, "暂不支持排序"):
            menu_order.write_tables(self.base, order(FACTORY[::-1]), dict(tpl.load("gr3x_v160.json"), id="other-model", model="GR X", firmware="9.99"))

    def test_pinned_entry_only_accepts_the_official_payload(self):
        with self.assertRaisesRegex(menu_order.MenuOrderError, "官方"):
            menu_order.apply(self.base, order(FACTORY[::-1]), self.t)

    def test_names_follow_the_order_and_show_renames(self):
        names = menu_order.names_in_order(order(FACTORY[::-1]), self.t, {"11": {"name": "Cross"}})
        self.assertEqual(names[:3], ["Cross（交叉冲洗）", "HDR 色调", "怀旧"])
        self.assertEqual(menu_order.names_in_order(None, self.t)[6], "负片")


class Composition(unittest.TestCase):
    """Compositor with the real menu writer on a synthetic payload; other writers are stubs."""

    @classmethod
    def setUpClass(cls):
        cls.base = synthetic_payload()
        cls.t = tpl.load()

    def build(self, edits, other=None, film=None, inplace=False, template=None):
        raw = b"synthetic input"
        with ExitStack() as stack:
            stack.enter_context(patch.object(edit_core, "INPUT_SIZE", len(raw)))
            stack.enter_context(patch.object(edit_core, "INPUT_SHA", edit_core._sha(raw)))
            stack.enter_context(patch.object(edit_core, "PAYLOAD_SIZE", len(self.base)))
            stack.enter_context(patch.object(edit_core, "PAYLOAD_SHA", edit_core._sha(self.base)))
            stack.enter_context(patch.object(edit_core, "_validate_edits"))
            stack.enter_context(patch.object(edit_core, "_decode", return_value=(self.base, [], 0, b"")))
            if other is not None:
                stack.enter_context(patch.object(edit_core, "apply_edits", return_value=other))
            if film is not None:
                stack.enter_context(patch.object(film_inplace_apply, "apply", return_value=film))
            pack = stack.enter_context(patch.object(
                edit_core, "_pack_payload",
                side_effect=lambda official, before, frames, stop, footer, merged, declared:
                dict(output=merged, declared=declared)))
            entry = compositor.build_inplace_composite if inplace else compositor.build_composite
            result = entry(raw, edits, template or self.t)
            self.assertEqual(pack.call_count, 1)
            return result

    def delta(self, at, data):
        changed = bytearray(self.base)
        changed[at:at + len(data)] = data
        return bytes(changed), [dict(offset=at, length=len(data))]

    def test_sorting_alone(self):
        request = order(FACTORY[::-1])
        got = self.build([menu_order.edit_for(request)])
        menu_order_verify._compare_sorting_only(self.base, got["output"], request, False)
        self.assertEqual(got["features"], ["menu_order"])
        self.assertEqual(sum(d["length"] for d in got["declared"]), 78)

    def test_with_name_icon_and_film_on_the_1_0_film_route(self):
        request = order([12] + [s for s in FACTORY if s != 12])
        name = self.delta(0x1000, b"Nega Warm")
        film = self.delta(0x20000, b"film-bytes")
        edits = [dict(kind="name", slot=12, string_id=273, text="Nega Warm"),
                 dict(kind="icon", slot=12, icon_id=23, pixels=b"\0" * 6400),
                 dict(kind="film", slot=8, film="portra400"), menu_order.edit_for(request)]
        got = self.build(edits, other=name, film=film, inplace=True)
        out = got["output"]
        self.assertEqual(out[0x1000:0x1009], b"Nega Warm")
        self.assertEqual(out[0x20000:0x2000A], b"film-bytes")
        menu_order_verify._compare_tables(self.base, out, request)
        self.assertEqual(got["features"], ["film", "icon", "menu_order", "name"])
        self.assertEqual(got["film_layout"], "inplace_history_cache_exit_v29")

    def test_overlap_with_another_feature_is_rejected(self):
        at = OFF(menu_order.RAW_VA) + 3
        with self.assertRaisesRegex(compositor.CompositeError, "overlapping"):
            self.build([dict(kind="name", slot=2, string_id=268, text="Vi"), menu_order.edit_for(order(FACTORY[::-1]))],
                       other=self.delta(at, b"\x55"))
        with self.assertRaisesRegex(compositor.CompositeError, "overlapping"):
            self.build([dict(kind="film", slot=8, film="portra400"), menu_order.edit_for(order(FACTORY[::-1]))],
                       film=self.delta(OFF(menu_order.STILL_VA[0]), b"\x55"), inplace=True)

    def test_rejected_requests(self):
        good = menu_order.edit_for(order(FACTORY[::-1]))
        for edits, message in (([good, good], "duplicate menu order"),
                               ([menu_order.edit_for(order(FACTORY))], "不算改动"),
                               ([dict(good, still_slots=FACTORY[:-1])], "拍照菜单顺序"),
                               ([dict(good, extra=1)], "字段不对")):
            with self.assertRaisesRegex(compositor.CompositeError, message):
                self.build(edits)

    def test_invalid_or_unsupported_request_fails_before_decoding(self):
        with patch.object(edit_core, "_decode", side_effect=AssertionError("must not decode")):
            with self.assertRaises(compositor.CompositeError):
                compositor.build_composite(b"", [dict(kind="menu_order", schema=1, still_slots=[1],
                                                      video_slots=[1])], self.t)
            with self.assertRaises(compositor.CompositeError):
                compositor.build_composite(b"", [menu_order.edit_for(order(FACTORY[::-1]))],
                                           dict(tpl.load("gr3x_v160.json"), id="other-model", model="GR X", firmware="9.99"))

    def test_every_edit_plus_the_order_fits(self):
        # 12 icons + 11 names + grain + 4 films are 28 edits; the menu order is one more.
        edits = [dict(kind="icon", slot=n, icon_id=0, pixels=b"") for n in range(1, 13)]
        edits += [dict(kind="name", slot=n, string_id=0, text="x") for n in range(2, 13)]
        edits += [dict(kind="grain_menu", slot=12, mode=22)]
        edits += [dict(kind="film", slot=s, film="portra400") for s in (8, 9, 10, 11)]
        stop = compositor.CompositeError("stop after counting")
        with patch.object(edit_core, "_validate_edits", side_effect=stop):
            with self.assertRaisesRegex(compositor.CompositeError, "stop after counting"):
                compositor.build_inplace_composite(b"", edits + [menu_order.edit_for(order(FACTORY[::-1]))], self.t)
            with self.assertRaises(compositor.CompositeError) as caught:
                compositor.build_inplace_composite(b"", edits + [dict(kind="name", slot=2, string_id=0, text="y")], self.t)
        self.assertNotIn("stop after counting", str(caught.exception))


class CombinedCheck(unittest.TestCase):
    """composite_verify's menu step with the independent module pinned to the synthetic payload."""

    @classmethod
    def setUpClass(cls):
        cls.base = synthetic_payload()
        cls.t = tpl.load()

    def windows(self, after, edits):
        with patch.object(menu_order_verify, "PAYLOAD_SIZE", len(self.base)), \
                patch.object(menu_order_verify, "PAYLOAD_SHA256", sha256(self.base)):
            return composite_verify._menu_order_expected(self.base, after, edits)

    def test_windows_are_the_seven_tables(self):
        request = order(FACTORY[::-1])
        after, spans = menu_order.write_tables(self.base, request, self.t)
        got = self.windows(after, [menu_order.edit_for(request)])
        self.assertEqual(sorted((a, len(d)) for a, d in got), sorted((s["offset"], s["length"]) for s in spans))
        for at, data in got:
            self.assertEqual(data, after[at:at + len(data)])

    def test_wrong_or_partial_tables_fail(self):
        request = order(FACTORY[::-1])
        after, _ = menu_order.write_tables(self.base, request, self.t)
        partial = bytearray(after)
        partial[OFF(menu_order.RAW_VA):OFF(menu_order.RAW_VA) + 12] = self.base[OFF(menu_order.RAW_VA):OFF(menu_order.RAW_VA) + 12]
        other = menu_order.edit_for(order(FACTORY[1:] + FACTORY[:1]))
        for candidate, edits in ((bytes(partial), [menu_order.edit_for(request)]), (after, [other]),
                                 (after, [menu_order.edit_for(request)] * 2),
                                 (after, [dict(menu_order.edit_for(request), slot=1)])):
            with self.assertRaises(ValueError):
                self.windows(candidate, edits)

    def test_unpinned_payload_fails_closed(self):
        request = order(FACTORY[::-1])
        after, _ = menu_order.write_tables(self.base, request, self.t)
        with self.assertRaises(ValueError):
            composite_verify._menu_order_expected(self.base, after, [menu_order.edit_for(request)])

    def test_iiix_template_with_a_bad_file_fails_closed(self):
        # IIIx 1.60 is accepted since 2026-10-25, but only with its pinned official payload.
        result = dict(output=b"x", output_sha256="0" * 64, payload_sha256_after="0" * 64)
        checks = composite_verify.verify(b"x", result, [menu_order.edit_for(order(FACTORY[::-1]))],
                                         tpl.load("gr3x_v160.json"))
        self.assertFalse(composite_verify.all_ok(checks))


class Generation(unittest.TestCase):
    def setUp(self):
        self.t = tpl.load()

    def test_edits_summary_and_note(self):
        o = order(FACTORY[::-1])
        edits = generate.edits_from_changes(None, "synthetic", {}, self.t, order=o)
        self.assertEqual(edits, [menu_order.edit_for(o)])
        self.assertEqual(generate.edits_from_changes(None, "synthetic", {}, self.t, order=order(FACTORY)), [])
        rows = generate.summarize({}, self.t, order=o)
        self.assertEqual(rows[0]["what"], "菜单顺序")
        self.assertTrue(rows[0]["value"].startswith("交叉冲洗、HDR 色调、怀旧"))
        note = generate.verification_note(edits, self.t)
        self.assertIn("菜单的新顺序在普通 GR III 上已实测，其他机身还没有", note)
        self.assertIn("刷机风险自负", note)
        hdf = generate.verification_note(edits, self.t, "GR III HDF")
        self.assertIn("GR III HDF", hdf)
        with self.assertRaises(generate.GenerateError):
            generate.edits_from_changes(None, "synthetic", {}, self.t, order=order(FACTORY[:-1]))
        self.assertEqual(generate.edits_from_changes(None, "synthetic", {}, tpl.load("gr3x_v160.json"), order=o),
                         [menu_order.edit_for(o)])
        with self.assertRaisesRegex(generate.GenerateError, "暂不支持排序"):
            generate.edits_from_changes(None, "synthetic", {}, dict(id="other", model="X", firmware="1"), order=o)

    def test_no_order_keeps_the_1_0_note_and_summary(self):
        edits = [dict(kind="icon", slot=n) for n in range(1, 13)]
        self.assertNotIn("菜单", generate.verification_note(edits, self.t))
        self.assertEqual(generate.summarize({}, self.t), [])

    def test_sorting_only_project_reaches_the_compositor(self):
        o = order(FACTORY[::-1])
        seen = {}

        def stop(official, edits, template, jpeg=None, **kw):
            seen["edits"] = edits
            raise compositor.CompositeError("synthetic stop")

        with patch.object(compositor, "build_composite", side_effect=stop):
            with self.assertRaisesRegex(generate.GenerateError, "synthetic stop"):
                generate.run(None, dict(id="synthetic", changes={}, menu_order=o), self.t, b"")
        self.assertEqual(seen["edits"], [menu_order.edit_for(o)])

    def test_film_route_carries_the_order(self):
        o = order(FACTORY[::-1])
        seen = {}

        def stop(official, edits, template, jpeg=None, **kw):
            seen["edits"] = edits
            raise compositor.CompositeError("synthetic stop")

        project = dict(id="synthetic", target_body="GR III HDF", changes={"8": {"film": "portra400"}}, menu_order=o)
        with patch.object(compositor, "build_inplace_composite", side_effect=stop):
            with self.assertRaisesRegex(generate.GenerateError, "synthetic stop"):
                generate._run(None, project, self.t, b"", inplace=True)
        self.assertEqual(seen["edits"], [dict(kind="film", slot=8, film="portra400"), menu_order.edit_for(o)])

    def test_empty_broken_and_unsupported_projects(self):
        with self.assertRaisesRegex(generate.GenerateError, "还没有改动"):
            generate.run(None, dict(id="synthetic", changes={}), self.t, b"")
        with self.assertRaisesRegex(generate.GenerateError, "还没有改动"):
            generate.run(None, dict(id="synthetic", changes={}, menu_order=order(FACTORY)), self.t, b"")
        with self.assertRaises(generate.GenerateError):
            generate.run(None, dict(id="synthetic", changes={}, menu_order=dict(order(FACTORY[::-1]), schema=9)), self.t, b"")
        with patch.object(compositor, "build_composite", side_effect=AssertionError("no build")):
            with self.assertRaisesRegex(generate.GenerateError, "暂不支持排序"):
                generate.run(None, dict(id="synthetic", changes={"2": {"name": "V"}}, menu_order=order(FACTORY[::-1])),
                             dict(tpl.load("gr3x_v160.json"), id="other-model", model="GR X", firmware="9.99"), b"")

    def test_iiix_candidate_refuses_a_broken_stored_order(self):
        store = unittest.mock.Mock()
        project = dict(id="p_000000000000", title="x", template_id=models.GR3X.id, input_sha256=models.GR3X.input_sha,
                       changes={"2": {"name": "V"}}, menu_order=dict(order(FACTORY[::-1]), schema=9))
        store.get_project.return_value = copy.deepcopy(project)
        with self.assertRaisesRegex(generate.GenerateError, "菜单顺序格式错误"):
            gr3x_generation.run(store, project, tpl.load("gr3x_v160.json"), b"")
        broken = dict(project, menu_order=None, menu_order_unreadable="bad")
        store.get_project.return_value = copy.deepcopy(broken)
        with self.assertRaisesRegex(generate.GenerateError, "无法读取"):
            gr3x_generation.run(store, broken, tpl.load("gr3x_v160.json"), b"")
        store.input_path.assert_not_called()

    def test_iiix_sorting_only_project_is_a_change(self):
        # An order alone counts as a change on IIIx too: the run goes on to the body check instead of
        # stopping at "还没有改动".
        store = unittest.mock.Mock()
        project = dict(id="p_000000000000", title="x", template_id=models.GR3X.id, input_sha256=models.GR3X.input_sha,
                       changes={}, menu_order=order(FACTORY[::-1]))
        store.get_project.return_value = copy.deepcopy(project)
        with self.assertRaisesRegex(generate.GenerateError, "请先选择具体机身版本"):
            gr3x_generation.run(store, project, tpl.load("gr3x_v160.json"), b"")
        factory = dict(project, menu_order=None)
        store.get_project.return_value = copy.deepcopy(factory)
        with self.assertRaisesRegex(generate.GenerateError, "还没有改动"):
            gr3x_generation.run(store, factory, tpl.load("gr3x_v160.json"), b"")


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.t = test_http.fake_template()
        sha = self.store.put_input(test_http.FAKE_OFFICIAL)
        self.pid = self.store.create_project("排序", sha, self.t)["id"]
        self.dir = self.store.root / "projects" / self.pid

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def write(self, name, value):
        (self.dir / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def files(self):
        return {name: (self.dir / name).read_bytes() for name in ("project.json", "draft.json")}

    def test_old_project_without_field_is_factory_and_clean(self):
        p = self.store.get_project(self.pid)
        self.assertNotIn("menu_order", self.read("draft.json"))
        self.assertIsNone(p["draft"]["menu_order"])          # the response says which order to show
        self.assertFalse(p["unsaved"])
        p = self.store.put_draft(self.pid, {}, self.t)      # an older page: nothing to carry over
        self.assertFalse(p["unsaved"])
        self.assertNotIn("menu_order", self.read("draft.json"))
        self.assertNotIn("menu_order_unreadable", p)
        self.assertNotIn("menu_order_unreadable", p["draft"])

    def test_draft_save_versions_reopen_and_reset(self):
        o = order([12] + [s for s in FACTORY if s != 12])
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=o)
        self.assertTrue(p["unsaved"])
        self.assertEqual(p["draft"]["menu_order"], o)
        # an older client that sends only changes keeps the draft order
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t)
        self.assertEqual(p["draft"]["menu_order"], o)
        p = self.store.save(self.pid, self.t)
        self.assertFalse(p["unsaved"])
        self.assertEqual(p["menu_order"], o)
        self.assertEqual(self.read("versions/1.json")["menu_order"], o)
        self.assertTrue(p["versions"][0]["menu_order"])
        reopened = Store(self.tmp.name).get_project(self.pid)
        self.assertEqual((reopened["menu_order"], reopened["draft"]["menu_order"], reopened["unsaved"]), (o, o, False))
        # only the order changes: unsaved
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=order(FACTORY[::-1]))
        self.assertTrue(p["unsaved"])
        # back to the factory order: an explicit null in the draft (a missing key would mean "the saved
        # order"), still unsaved until saved
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=None)
        self.assertIsNone(p["draft"]["menu_order"])
        self.assertIn("menu_order", self.read("draft.json"))
        self.assertIsNone(self.read("draft.json")["menu_order"])
        self.assertTrue(p["unsaved"])
        p = self.store.save(self.pid, self.t)
        self.assertNotIn("menu_order", p)
        self.assertNotIn("menu_order", self.read("versions/2.json"))
        self.assertEqual(self.read("versions/1.json")["menu_order"], o)
        self.assertEqual(self.store.list_projects()[0]["changes"], 1)

    def test_list_counts_the_order_as_one_change(self):
        self.store.put_draft(self.pid, {}, self.t, menu_order=order(FACTORY[::-1]))
        self.assertEqual(self.store.list_projects()[0]["changes"], 1)

    def test_factory_order_dict_is_no_change(self):
        p = self.store.put_draft(self.pid, {}, self.t, menu_order=order(FACTORY))
        self.assertIsNone(p["draft"]["menu_order"])
        self.assertIsNone(self.read("draft.json")["menu_order"])
        self.assertFalse(p["unsaved"])

    def test_bad_orders_are_refused_not_dropped(self):
        o = order(FACTORY[::-1])
        self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        for bad in (order(FACTORY[:-1] + [1], menu_order.FACTORY_VIDEO), dict(o, schema=2), "reverse",
                    order(FACTORY[::-1], list(menu_order.FACTORY_VIDEO))):
            with self.assertRaises(StoreError, msg=repr(bad)):
                self.store.put_draft(self.pid, {}, self.t, menu_order=bad)
        self.assertEqual(self.read("draft.json")["menu_order"], o)

    def test_explicit_order_rejected_with_a_clear_message(self):
        with self.assertRaisesRegex(StoreError, "菜单顺序没有存上，这次的其他改动也没有写入"):
            self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=order(FACTORY + [1], menu_order.FACTORY_VIDEO))
        self.assertEqual(self.read("draft.json")["changes"], {})

    def damage(self, value, where=("project.json", "draft.json")):
        for name in where:
            record = self.read(name)
            record["menu_order"] = value
            self.write(name, record)

    def test_damaged_stored_order_opens_with_a_notice_and_keeps_the_files(self):
        # Edited by hand, damaged, or written by a later version under other rules (the movie list not
        # following the photo list): the project still opens, the order shows as the factory one.
        for bad, reason in ((dict(order(FACTORY[::-1]), still_slots=[1, 1]), "拍照菜单顺序"),
                            (dict(order(FACTORY[::-1]), schema=2), "版本不认识"),
                            (order(FACTORY[::-1], list(menu_order.FACTORY_VIDEO)), "视频菜单顺序必须跟着拍照顺序"),
                            ("reverse", "字段不对")):
            with self.subTest(bad=bad):
                self.damage(bad)
                before = self.files()
                p = self.store.get_project(self.pid)
                self.assertIsNone(p["draft"]["menu_order"])
                self.assertIn(reason, p["draft"]["menu_order_unreadable"])
                self.assertIn(reason, p["menu_order_unreadable"])
                self.assertFalse(p["unsaved"])                 # what is on disk is consistent
                # generating never falls back to the factory order behind the user's back
                with self.assertRaisesRegex(generate.GenerateError, "已存的菜单顺序无法读取"):
                    generate.run(self.store, p, self.t, b"")
                self.assertEqual(self.store.list_projects()[0]["changes"], len(self.read("draft.json")["changes"]) + 1)
                # a rename (title) and an autosave that leaves the order alone both work; the value stays as stored
                self.assertEqual(self.store.rename_project(self.pid, "改名")["title"], "改名")
                p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t)
                self.assertEqual(p["draft"]["changes"], {"7": {"name": "Posi Warm"}})
                self.assertEqual(self.read("draft.json")["menu_order"], bad)
                self.assertEqual(self.read("project.json")["menu_order"], bad)
                self.assertIn("menu_order_unreadable", p["draft"])
                # saving without deciding about the order is refused and changes nothing
                before = self.files()
                with self.assertRaisesRegex(StoreError, "已存的菜单顺序无法读取，这次没有保存"):
                    self.store.save(self.pid, self.t)
                self.assertEqual(self.files(), before)
                # the user restores the factory order (or ⌘S on the page, which sends null): repaired
                p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=None)
                self.assertNotIn("menu_order_unreadable", p["draft"])
                self.assertIn("menu_order_unreadable", p)      # the saved one until the save
                self.assertTrue(p["unsaved"])
                p = self.store.save(self.pid, self.t)
                self.assertNotIn("menu_order", self.read("project.json"))
                self.assertNotIn("menu_order_unreadable", p)
                self.assertNotIn("menu_order_unreadable", p["draft"])
                self.assertFalse(p["unsaved"])
                self.assertEqual(p["changes"], {"7": {"name": "Posi Warm"}})

    def test_damaged_saved_order_with_a_good_draft(self):
        o = order(FACTORY[::-1])
        self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        self.damage(dict(o, still_slots=[1, 1]), where=("project.json",))
        p = self.store.get_project(self.pid)
        self.assertEqual(p["draft"]["menu_order"], o)
        self.assertNotIn("menu_order_unreadable", p["draft"])
        self.assertIn("拍照菜单顺序", p["menu_order_unreadable"])
        self.assertTrue(p["unsaved"])
        p = self.store.save(self.pid, self.t)
        self.assertEqual((p["menu_order"], p["unsaved"]), (o, False))
        self.assertNotIn("menu_order_unreadable", p)

    def test_damaged_draft_order_with_a_good_saved_order_shows_the_saved_one(self):
        # Only the draft's order is unreadable (edited by hand, or an unsaved draft of a later version):
        # the saved order is shown instead of the factory one, so the next save cannot throw it away.
        o = order(FACTORY[::-1])
        self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=o)
        self.store.save(self.pid, self.t)
        self.damage(dict(o, schema=2), where=("draft.json",))
        before = self.files()
        p = self.store.get_project(self.pid)
        self.assertEqual(self.files(), before)                  # opening changes nothing on disk
        self.assertEqual(p["draft"]["menu_order"], o)
        self.assertIn("版本不认识", p["draft"]["menu_order_fallback"])
        self.assertNotIn("menu_order_unreadable", p["draft"])
        self.assertNotIn("menu_order_unreadable", p)
        # the page's next draft write carries the order it shows: the draft is repaired, nothing is lost
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=p["draft"]["menu_order"])
        self.assertEqual(self.read("draft.json")["menu_order"], o)
        self.assertNotIn("menu_order_fallback", p["draft"])
        self.assertFalse(p["unsaved"])
        p = self.store.save(self.pid, self.t)
        self.assertEqual(self.read("project.json")["menu_order"], o)
        # with the saved order damaged too there is nothing good to fall back to: the old notice
        self.damage(dict(o, schema=2))
        p = self.store.get_project(self.pid)
        self.assertIsNone(p["draft"]["menu_order"])
        self.assertIn("menu_order_unreadable", p["draft"])
        self.assertNotIn("menu_order_fallback", p["draft"])

    def test_re_sorting_replaces_a_damaged_order(self):
        self.damage({"schema": 1})
        o = order(FACTORY[::-1])
        p = self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        self.assertEqual(p["draft"]["menu_order"], o)
        p = self.store.save(self.pid, self.t)
        self.assertEqual(self.read("project.json")["menu_order"], o)
        self.assertNotIn("menu_order_unreadable", p)

    def test_iiix_project_sorts_and_saves_its_order(self):
        # 2026-10-24: a GR IIIx project sorts and saves an order like GR III; only writing it waits.
        iiix = tpl.load("gr3x_v160.json")
        project = self.read("project.json")
        project.update(input_sha256=models.GR3X.input_sha, template_id=models.GR3X.id)
        self.write("project.json", project)
        o = order(FACTORY[::-1])
        p = self.store.put_draft(self.pid, {}, iiix, menu_order=o)
        self.assertEqual(p["draft"]["menu_order"], o)
        self.assertNotIn("menu_order_unreadable", p["draft"])
        p = self.store.save(self.pid, iiix)
        self.assertEqual(self.read("project.json")["menu_order"], o)
        self.assertNotIn("menu_order_unreadable", p)
        self.assertEqual(self.store.get_project(self.pid)["draft"]["menu_order"], o)   # reopened as saved
        self.store.put_draft(self.pid, {}, iiix, menu_order=None)   # 恢复原厂 then ⌘S clears it
        self.store.save(self.pid, iiix)
        self.assertNotIn("menu_order", self.read("project.json"))

    def test_hdf_body_keeps_its_order(self):
        o = order(FACTORY[::-1])
        self.store.set_target_body(self.pid, "GR III HDF", self.t)
        p = self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        self.assertEqual((p["target_body"], p["draft"]["menu_order"]), ("GR III HDF", o))
        p = self.store.save(self.pid, self.t)
        self.assertEqual(p["menu_order"], o)

    def test_other_models_refuse_an_order(self):
        other = dict(tpl.load("gr3x_v160.json"), id="other-model", model="GR X", firmware="9.99")
        with self.assertRaisesRegex(StoreError, "暂不支持排序"):
            self.store.put_draft(self.pid, {}, other, menu_order=order(FACTORY[::-1]))
        self.assertNotIn("menu_order", self.read("draft.json"))
        iiix = tpl.load("gr3x_v160.json")
        # the factory order (no order at all) is fine everywhere
        self.store.put_draft(self.pid, {}, iiix, menu_order=None)
        self.store.put_draft(self.pid, {}, iiix)


class DraftApi(test_http.Served):
    def test_status_publishes_the_factory_order(self):
        value = self.assertJson(self.call("GET", "/api/status"), 200)["menu_order"]
        self.assertEqual(value, dict(schema=1, factory_still=FACTORY, factory_video=list(menu_order.FACTORY_VIDEO),
                                     video_excluded=[4, 10], video_policy="follow_still"))

    def test_put_keep_reset_and_reject(self):
        pid = self.new_project()
        url = "/api/projects/%s/draft" % pid
        o = order(FACTORY[::-1])
        value = self.assertJson(self.call("PUT", url, {"changes": {}, "menu_order": o}), 200)
        self.assertEqual((value["draft"]["menu_order"], value["unsaved"]), (o, True))
        self.assertTrue(value["capabilities"]["menu_order"])
        value = self.assertJson(self.call("PUT", url, {"changes": {}}), 200)
        self.assertEqual(value["draft"]["menu_order"], o)
        res = self.call("PUT", url, {"changes": {}, "menu_order": dict(o, still_slots=[True] + o["still_slots"][1:])})
        self.assertEqual(res.status, 400)
        self.assertIn("菜单顺序", res.json()["error"])
        value = self.assertJson(self.call("POST", "/api/projects/%s/save" % pid), 200)
        self.assertEqual(value["menu_order"], o)
        value = self.assertJson(self.call("PUT", url, {"changes": {}, "menu_order": None}), 200)
        self.assertIsNone(value["draft"]["menu_order"])
        self.assertTrue(value["unsaved"])

    def test_damaged_order_does_not_lock_the_project(self):
        # Review 2026-10-18: a stored order that cannot be read used to make every route of the project
        # answer 400 (open, draft, save, rename, even {"menu_order": null}).
        pid = self.new_project()
        folder = self.store.root / "projects" / pid
        bad = dict(order(FACTORY[::-1]), still_slots=[1, 1])
        for name in ("project.json", "draft.json"):
            record = read_json(folder / name)
            record["menu_order"] = bad
            write_json(folder / name, record)
        url = "/api/projects/%s" % pid
        value = self.assertJson(self.call("GET", url), 200)
        self.assertIsNone(value["draft"]["menu_order"])
        self.assertIn("菜单顺序", value["draft"]["menu_order_unreadable"])
        self.assertTrue(value["capabilities"]["menu_order"])
        self.assertTrue(any(p["id"] == pid for p in self.assertJson(self.call("GET", "/api/status"), 200)["projects"]))
        value = self.assertJson(self.call("PUT", url + "/draft", {"changes": {"7": {"name": "Posi Warm"}}}), 200)
        self.assertEqual(value["draft"]["changes"], {"7": {"name": "Posi Warm"}})
        self.assertEqual(read_json(folder / "draft.json")["menu_order"], bad)
        self.assertEqual(self.assertJson(self.call("PATCH", url, {"title": "改名"}), 200)["title"], "改名")
        res = self.call("POST", url + "/save", {})
        self.assertEqual(res.status, 400)
        self.assertIn("已存的菜单顺序无法读取，这次没有保存", res.json()["error"])
        self.assertEqual(read_json(folder / "project.json")["menu_order"], bad)
        value = self.assertJson(self.call("PUT", url + "/draft", {"changes": {"7": {"name": "Posi Warm"}}, "menu_order": None}), 200)
        self.assertNotIn("menu_order_unreadable", value["draft"])
        value = self.assertJson(self.call("POST", url + "/save", {}), 200)
        self.assertNotIn("menu_order", read_json(folder / "project.json"))
        self.assertNotIn("menu_order_unreadable", value)
        self.assertEqual((value["changes"], value["unsaved"]), ({"7": {"name": "Posi Warm"}}, False))


if __name__ == "__main__":
    unittest.main()
