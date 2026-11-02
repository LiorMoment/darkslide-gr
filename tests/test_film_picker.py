import json
import tempfile
import unittest
from unittest import mock

from darkslide import base_curve, factory_envelope, film_fit, film_luts, film_profiles, fitting_setup, template as tpl
from darkslide._vendor import gr3_lut_fit as fitlib
from darkslide.store import Store, StoreError

try:
    from tests.test_http import Served, icon_png
except ImportError:  # run through "discover -s tests"
    from test_http import Served, icon_png


def cube(size=2, title="Mine", rows=None):
    n = size - 1
    rows = rows or [(r / n, g / n, b / n) for b in range(size) for g in range(size) for r in range(size)]
    text = ['TITLE "%s"' % title, "LUT_3D_SIZE %d" % size] + ["%.4f %.4f %.4f" % row for row in rows]
    return ("\n".join(text) + "\n").encode()


class ParseCube(unittest.TestCase):
    def test_reads_a_plain_cube(self):
        title, size, rows = film_luts.parse_cube(cube(3, "Warm"))
        self.assertEqual((title, size, len(rows)), ("Warm", 3, 27))
        self.assertEqual(rows[1], (0.5, 0.0, 0.0), "red runs fastest")

    def test_rejects_what_we_cannot_use(self):
        bad = [b"", b"x" * (film_luts.MAX_CUBE + 1), "文".encode("utf-16"),
               b"LUT_1D_SIZE 4\n0 0 0\n", cube(2)[:-12], b"LUT_3D_SIZE 1\n0 0 0\n",
               b"DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n" + cube(2),
               cube(2, rows=[(0, 0, 0)] * 7 + [(3, 3, 3)]), b"LUT_3D_SIZE 2\nhello\n",
               cube(2, rows=[(float("nan"), 0, 0)] + [(0, 0, 0)] * 7)]
        for data in bad:
            with self.assertRaises(film_luts.FilmLutError):
                film_luts.parse_cube(data)


class Catalog(unittest.TestCase):
    def test_every_built_in_film_has_camera_data_and_a_preview(self):
        cat = film_luts.catalog()
        ids = [f["id"] for f in cat["films"]]
        self.assertEqual(sorted(ids), sorted(film_profiles.ready_profile_ids()))
        self.assertIn("5207", ids)
        self.assertIn("5219", ids)
        for film in cat["films"]:
            self.assertTrue(film["ready"])
            title, size, rows = film_luts.parse_cube(film_luts.preview_path(film["id"]).read_bytes())
            self.assertEqual(size, 17)
            self.assertTrue(all(0.0 <= v <= 1.0 for row in rows for v in row))
        self.assertIn("spektrafilm", cat["credit"])
        self.assertIn("近似", cat["note"])

    def test_darkslide_fitted_films_are_recorded_and_licensed(self):
        root = film_luts.preview_path("gold200").parent.parent
        own = json.loads((root / "camera_profiles_darkslide_fits.json").read_text(encoding="utf-8"))
        self.assertEqual((own["license"], own["authors"], own["modified_by"]), ("CC-BY-SA-4.0", ["Andrea Volpato"], "LiorMoment"))
        self.assertGreaterEqual(len(own["profiles"]), 8)
        for entry in own["profiles"]:
            profile = film_profiles.load_profile(entry["id"])
            prov = profile["provenance"]
            self.assertEqual((prov["kind"], prov["license"], prov["source_sha256"]),
                             ("darkslide_fit", "CC-BY-SA-4.0", entry["source_sha256"]))
            self.assertIs(profile["native_effect_verified"], False)
            self.assertIn(entry["level"], ("高", "中", "低"))
            self.assertFalse(any(isinstance(v, list) and len(v) == 256 and v == profile["curves_u14"][0]
                                 for v in prov.values()), "no base gamma or curve copy in provenance")

    def test_preview_path_refuses_other_files(self):
        for ident in ("../catalog", "nothere", "Portra400"):
            with self.assertRaises(film_luts.FilmLutError):
                film_luts.preview_path(ident)


class StoreFilm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.template = tpl.load()
        sha = self.store.put_input(b"official-stand-in")
        self.pid = self.store.create_project("t", sha, self.template)["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, changes, strict=False):
        return self.store.validate_changes(self.pid, changes, self.template, strict)

    def test_film_choice_is_kept_on_the_replaceable_presets_only(self):
        self.assertEqual(self.check({"9": {"film": "gold200"}}, strict=True), {"9": {"film": "gold200"}})
        for key, film in (("7", "gold200"), ("12", "portra400"), ("9", "Gold 200"), ("9", 5)):
            with self.assertRaises(StoreError):
                self.check({key: {"film": film}})
        with self.assertRaises(StoreError):
            self.check({"9": {"film": "velvia50"}}, strict=True)
        self.assertEqual(self.check({"9": {"film": "velvia50"}}), {"9": {"film": "velvia50"}}, "drafts may hold it")

    def test_grade_keeps_well_formed_adjustments_on_any_slot(self):
        grade = {"temp": 20, "con": -15, "hH": [0, 0, 0, 0, 0, 10, 0, 0],
                 "curve": {"all": [[0, 0], [0.5, 0.56], [1, 1]], "b": [[0, 0.04], [1, 0.96]]}}
        self.assertEqual(self.check({"7": {"grade": grade}}, strict=True), {"7": {"grade": grade}})
        self.assertEqual(self.check({"9": {"film": "gold200", "grade": {"sat": 5}}}),
                         {"9": {"film": "gold200", "grade": {"sat": 5}}})
        bad = [[], {"temp": 101}, {"temp": 1.5}, {"temp": True}, {"nope": 1}, {"hS": [0] * 7},
               {"curve": {"x": [[0, 0], [1, 1]]}}, {"curve": {"all": [[0, 0]]}},
               {"curve": {"all": [[0.1, 0], [1, 1]]}}, {"curve": {"all": [[0, 0], [0.5, 2], [1, 1]]}},
               {"curve": {"all": [[0, 0], [0.6, 0.5], [0.4, 0.5], [1, 1]]}},
               {"curve": {"all": [[0, 0]] + [[i / 20, 0] for i in range(1, 20)] + [[1, 1]]}}]
        for g in bad:
            with self.assertRaises(StoreError):
                self.check({"7": {"grade": g}})
        with self.assertRaises(StoreError):
            self.check({"9": {"grade": {"sat": 1, "ref": {"film": "gold200"}}}})

    def test_own_icons_for_built_in_films(self):
        folder = self.store.ensure_film_icons_dir()
        note = (folder / "README.txt").read_text(encoding="utf-8")
        self.assertIn("gold200.png", note)
        self.assertEqual(self.store.film_icon_versions(), {})
        png = b"\x89PNG\r\n\x1a\n" + b"rest"
        (folder / "gold200.png").write_bytes(png)
        (folder / "portra400.jpg").write_bytes(b"not a jpeg")
        (folder / "nothere.png").write_bytes(png)
        self.assertEqual(self.store.film_icon("gold200"), (png, "image/png"))
        self.assertEqual(list(self.store.film_icon_versions()), ["gold200"], "bad and unknown files are skipped")
        for bad in ("portra400", "nothere", "../film_icons/gold200", "c200"):
            with self.assertRaises(StoreError):
                self.store.film_icon(bad)
        (folder / "gold200.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (Store.MAX_FILM_ICON))
        with self.assertRaises(StoreError):
            self.store.film_icon("gold200")

    def test_an_icon_may_say_which_film_it_came_from(self):
        asset = self.store.put_asset(self.pid, icon_png(), expect_icon=True)["asset"]
        ok = {"9": {"icon": {"asset": asset, "style": "image", "film": "gold200"}}}
        self.assertEqual(self.check(ok, strict=True), ok)
        for bad in ({"asset": asset, "style": "image", "film": "velvia50"},
                    {"asset": asset, "style": "factory", "text": "G", "film": "gold200"},
                    {"asset": asset, "style": "image", "film": 5}):
            with self.assertRaises(StoreError):
                self.check({"9": {"icon": bad}})

    def test_a_film_icon_may_be_a_stand_in_for_the_users_icon(self):
        # the plate used while the user's icon could not be read keeps its mark, so the page can put the
        # user's icon back later; only a film icon may carry it, and only as true
        asset = self.store.put_asset(self.pid, icon_png(), expect_icon=True)["asset"]
        ok = {"9": {"icon": {"asset": asset, "style": "image", "film": "gold200", "fallback": True}}}
        self.assertEqual(self.check(ok, strict=True), ok)
        for bad in ({"asset": asset, "style": "image", "fallback": True},
                    {"asset": asset, "style": "image", "film": "gold200", "fallback": False},
                    {"asset": asset, "style": "image", "film": "gold200", "fallback": 1}):
            with self.assertRaises(StoreError):
                self.check({"9": {"icon": bad}})

    def test_deleting_an_own_lut(self):
        a = self.store.put_film_lut(self.pid, cube(3, "Teal"), "teal.cube")
        b = self.store.put_film_lut(self.pid, cube(2, "Warm"), "warm.cube")
        files = self.store.film_lut_path(self.pid, a["id"]).parent
        (files / (a["id"] + ".camera.cube")).write_bytes(b"fitted")
        self.store.put_draft(self.pid, {"9": {"film": a["id"]}}, self.template)
        with self.assertRaises(StoreError) as ctx:
            self.store.delete_film_lut(self.pid, a["id"])
        self.assertIn("第 9 号预设正在用", str(ctx.exception))
        self.store.put_draft(self.pid, {"9": {"film": "gold200"}}, self.template)
        left = self.store.delete_film_lut(self.pid, a["id"])
        self.assertEqual([x["id"] for x in left], [b["id"]])
        self.assertFalse((files / (a["id"] + ".cube")).exists())
        self.assertFalse((files / (a["id"] + ".camera.cube")).exists())
        for gone in (a["id"], "gold200", "user_lut_000000000000", "../x"):
            with self.assertRaises(StoreError):
                self.store.delete_film_lut(self.pid, gone)
        again = self.store.put_film_lut(self.pid, cube(3, "Teal"), "teal.cube")
        self.assertEqual(again["id"], a["id"], "importing the same file again works after a delete")

    def test_user_lut_import_list_and_profiles(self):
        a = self.store.put_film_lut(self.pid, cube(3, "Teal"), "teal.cube")
        self.assertTrue(film_luts.USER_ID.match(a["id"]))
        self.assertEqual((a["name"], a["size"], a["fitted"]), ("Teal", 3, False))
        self.assertEqual(self.store.put_film_lut(self.pid, cube(3, "Teal"), "again.cube")["id"], a["id"])
        self.assertEqual([x["id"] for x in self.store.list_film_luts(self.pid)], [a["id"]])
        self.assertEqual(self.store.film_lut_path(self.pid, a["id"]).read_bytes(), cube(3, "Teal"))
        self.assertEqual(self.check({"11": {"film": a["id"]}}, strict=True), {"11": {"film": a["id"]}})
        with self.assertRaises(StoreError):
            self.store.film_lut_profiles(self.pid, [a["id"]])  # not fitted yet
        index_path = self.store._dir(self.pid) / "film_luts.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index[a["id"]]["profile"] = {"schema": 1, "id": a["id"]}
        index_path.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(StoreError):  # incomplete profile is not a bound fitted source
            self.store.film_lut_profiles(self.pid, [a["id"]])
        for ident in ("user_lut_000000000000", "../x", ""):
            with self.assertRaises(StoreError):
                self.store.film_lut_path(self.pid, ident)
        with self.assertRaises(StoreError):
            self.store.put_film_lut(self.pid, b"not a cube", "x.cube")


def fake_contract():
    """A made-up sRGB-like base gamma; the real one is read from the user's firmware."""
    values = [int(round(16384 * fitlib.srgb_encode(i / fitlib.K))) for i in range(256)]
    return dict(values=values, output_normalization_divisor=16384, model_id="test-model",
                official_sha256="0" * 64)


def identity_cube(size=9):
    n = size - 1
    return cube(size, "Same", [(r / n, g / n, b / n) for b in range(size) for g in range(size) for r in range(size)])


class Fit(unittest.TestCase):
    def test_identity_lut_fits_to_neutral_camera_parameters(self):
        profile, report = film_fit.fit_cube(identity_cube(), "user_lut_0123456789ab", fake_contract())
        self.assertEqual(profile["look_matrix_q13"], [[8192, 0, 0], [0, 8192, 0], [0, 0, 8192]])
        self.assertEqual(profile["post_matrix_q512"], [[512, 0, 0], [0, 512, 0], [0, 0, 512]])
        self.assertEqual(report["level"], "高")
        self.assertLess(report["de_mean"], 0.5)
        self.assertEqual(profile["provenance"]["base_model_id"], "test-model")
        self.assertFalse(any(isinstance(v, list) for v in profile["provenance"].values()), "base gamma itself is never stored")
        film_profiles.validate_profile(profile, "user_lut_0123456789ab")

    def test_warm_lut_moves_red_up_and_blue_down(self):
        n = 8
        rows = [(min(1, r / n * 1.1), g / n, b / n * 0.85) for b in range(n + 1) for g in range(n + 1) for r in range(n + 1)]
        profile, report = film_fit.fit_cube(cube(n + 1, "Warm", rows), "user_lut_0123456789ab", fake_contract())
        mid = [c[96] for c in profile["curves_u14"]]
        self.assertGreater(mid[0], mid[2])
        self.assertIn(report["level"], ("高", "中"))
        title, size, cam = film_luts.parse_cube(film_fit.camera_cube(fake_contract()["values"], profile, 5))
        self.assertEqual(size, 5)
        grey = cam[62]  # centre of a 5-cube
        self.assertGreater(grey[0], grey[2])

    def test_factory_envelope_refuses_and_never_clamps(self):
        profile = film_profiles.load_profile("gold200")
        self.assertEqual(film_fit.envelope_violations(profile, None), [], "no envelope yet: nothing refused")
        wide = dict(look_min=-32768, look_max=32767, look_row_abs_max=10 ** 6, post_min=-4096, post_max=4096,
                    post_col_abs_max=8191, curve_step_max=16384, curve_step_min=-16384)
        self.assertEqual(film_fit.envelope_violations(profile, wide), [])
        tight = dict(wide, look_max=8000, curve_step_max=10)
        bad = film_fit.envelope_violations(profile, tight)
        self.assertTrue(any("前矩阵系数" in b for b in bad) and any("曲线最大陡度" in b for b in bad), bad)
        self.assertEqual(profile, film_profiles.load_profile("gold200"), "the check never changes the numbers")
        self.assertEqual(film_fit.envelope_violations(profile, dict(post_max=100)),
                         film_fit.envelope_violations(profile, dict(post_max=100)))

    def test_de2000_reference_pair(self):
        # Sharma et al. test data pair 1 (Lab inputs converted through sRGB are not exact,
        # so only check the function is symmetric, zero on equal colours and positive otherwise).
        self.assertEqual(fitlib.de2000((0.5, 0.4, 0.3), (0.5, 0.4, 0.3)), 0)
        a, b = (0.5, 0.4, 0.3), (0.45, 0.42, 0.35)
        self.assertAlmostEqual(fitlib.de2000(a, b), fitlib.de2000(b, a), places=9)
        self.assertGreater(fitlib.de2000(a, b), 1)


class Http(Served):
    def setUp(self):
        # This small input is an explicitly registered test-only model.
        from dataclasses import replace
        from darkslide import models
        fixture = replace(models.GR3, input_sha=self.sha,
                          input_size=len(self.store.input_path(self.sha).read_bytes()))
        registered = mock.patch.object(models, "ALL", models.ALL + (fixture,))
        registered.start()
        self.addCleanup(registered.stop)

    def test_catalog_preview_and_user_luts(self):
        cat = self.assertJson(self.call("GET", "/api/film-catalog"), 200)
        self.assertEqual(cat["writer"]["available"], True)
        first = cat["films"][0]["id"]
        res = self.call("GET", "/api/film-preview/%s.cube" % first)
        self.assertEqual(res.status, 200)
        self.assertIn(b"LUT_3D_SIZE 17", res.body)
        self.assertEqual(self.call("GET", "/api/film-preview/nothere.cube").status, 422)
        pid = self.new_project()
        res = self.call("POST", "/api/projects/%s/film-luts" % pid, cube(2, "Mine"), {"X-Filename": "mine.cube"})
        lut = self.assertJson(res, 200)
        listed = self.assertJson(self.call("GET", "/api/projects/%s/film-luts" % pid), 200)
        self.assertEqual([x["id"] for x in listed["luts"]], [lut["id"]])
        res = self.call("GET", "/api/projects/%s/film-luts/%s.cube" % (pid, lut["id"]))
        self.assertEqual(res.body, cube(2, "Mine"))
        res = self.call("POST", "/api/projects/%s/film-luts" % pid, b"nope", {"X-Filename": "x.cube"})
        self.assertEqual(res.status, 400)
        self.assertEqual(cat["icons"], {})
        self.assertEqual(self.call("GET", "/api/film-icons/%s" % first).status, 400)
        res = self.call("POST", "/api/projects/%s/film-luts/%s/delete" % (pid, lut["id"]))
        self.assertEqual(self.assertJson(res, 200)["luts"], [])
        self.assertEqual(self.call("POST", "/api/projects/%s/film-luts/%s/delete" % (pid, lut["id"])).status, 400)

    def test_fit_needs_camera_data_and_factory_range(self):
        ready = mock.patch.object(fitting_setup, "status", return_value={"state": "ready", "note": ""})
        ready.start()
        self.addCleanup(ready.stop)
        pid = self.new_project()
        lut = self.assertJson(self.call("POST", "/api/projects/%s/film-luts" % pid, identity_cube(5), {"X-Filename": "same.cube"}), 200)
        with mock.patch.object(base_curve, "base_curve", side_effect=base_curve.BaseCurveError("读不到")):
            res = self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"]))
            self.assertIn("等待相机数据", self.assertJson(res, 409)["error"])
        with mock.patch.object(fitting_setup, "status", return_value={"state": "missing", "note": "要下载"}):
            res = self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"]))
            self.assertIn("需要先下载拟合组件", self.assertJson(res, 409)["error"])
        with mock.patch.object(base_curve, "base_curve", return_value=fake_contract()), \
                mock.patch.object(factory_envelope, "factory_envelope", side_effect=factory_envelope.FactoryEnvelopeError("坏")):
            res = self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"]))
            self.assertIn("读不出原厂范围", self.assertJson(res, 409)["error"], "no range: never let through")
        self.assertEqual(self.call("GET", "/api/fitting-setup").status, 200)

    def test_builtin_films_are_checked_against_the_factory_range(self):
        pid = self.new_project()
        # the range read from the official 2.10 firmware by factory_envelope: every built-in film is inside it
        env = dict(model_id="m", look_min=-5792, look_max=9578, look_row_abs_max=19776, post_min=-8191,
                   post_max=8191, post_col_abs_max=8191, curve_step_max=194, curve_step_min=-58)
        with mock.patch.object(factory_envelope, "factory_envelope", return_value=env):
            got = self.assertJson(self.call("GET", "/api/projects/%s/film-check" % pid), 200)["films"]
        self.assertEqual(set(got), {f["id"] for f in film_luts.catalog()["films"]})
        self.assertEqual({k: v for k, v in got.items() if v}, {}, "all 16 are fitted inside the factory range")
        with mock.patch.object(factory_envelope, "factory_envelope", return_value=dict(env, look_max=8500)):
            tight = self.assertJson(self.call("GET", "/api/projects/%s/film-check" % pid), 200)["films"]
        self.assertTrue(any(tight.values()), "a tighter range is reported, film by film")

    def test_out_of_range_is_refused_before_anything_is_kept(self):
        """User, 2026-10-17: check first; a fit that cannot go into the camera is never kept."""
        wide = dict(model_id="m", look_min=-32768, look_max=32767, look_row_abs_max=10 ** 6, post_min=-8191,
                    post_max=8191, post_col_abs_max=8191, curve_step_max=16384, curve_step_min=-16384)
        for p in (mock.patch.object(factory_envelope, "factory_envelope", return_value=wide),
                  mock.patch.object(fitting_setup, "status", return_value={"state": "ready", "note": ""}),
                  mock.patch.object(base_curve, "base_curve", return_value=fake_contract())):
            p.start()
            self.addCleanup(p.stop)
        pid = self.new_project()
        lut = self.assertJson(self.call("POST", "/api/projects/%s/film-luts" % pid, identity_cube(5), {"X-Filename": "same.cube"}), 200)
        bad = ["曲线起点 2000 超出原厂范围（0～1920）"]
        with mock.patch.object(film_fit, "envelope_violations", return_value=bad):
            res = self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"]))
            self.assertIn("曲线起点 2000", self.assertJson(res, 422)["error"])
            listed = self.assertJson(self.call("GET", "/api/projects/%s/film-luts" % pid), 200)["luts"][0]
            self.assertFalse(listed["fitted"]); self.assertIsNone(listed["fit"])
            self.assertEqual(listed["check"]["writable"], False); self.assertEqual(listed["check"]["violations"], bad)
            self.assertEqual(self.call("GET", "/api/projects/%s/film-luts/%s/camera.cube" % (pid, lut["id"])).status, 400)
            with self.assertRaises(Exception):
                self.store.film_lut_profiles(pid, [lut["id"]])  # generation refuses it too
            body = {"cube": identity_cube(9).decode(), "name": "调色", "film": "gold200", "grade": {"exp": 90}}
            self.assertJson(self.call("POST", "/api/projects/%s/film-bakes" % pid, body), 422)
            ids = [l["id"] for l in self.assertJson(self.call("GET", "/api/projects/%s/film-luts" % pid), 200)["luts"]]
            self.assertEqual(ids, [lut["id"]], "a refused 调色 leaves nothing behind")
        with mock.patch.object(film_fit, "fit_cube", side_effect=film_fit.FilmFitError("颜色变化太大")):
            other = self.assertJson(self.call("POST", "/api/projects/%s/film-luts" % pid, identity_cube(7), {"X-Filename": "b.cube"}), 200)
            res = self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, other["id"]))
            self.assertIn("颜色变化太大", self.assertJson(res, 422)["error"])
        fitted = self.assertJson(self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"])), 200)
        self.assertTrue(fitted["fitted"]); self.assertIsNone(fitted["check"], "a passing fit clears the refusal")

    def test_fit_bake_and_camera_preview(self):
        wide = dict(model_id="m", look_min=-32768, look_max=32767, look_row_abs_max=10 ** 6, post_min=-8191,
                    post_max=8191, post_col_abs_max=8191, curve_step_max=16384, curve_step_min=-16384)
        env = mock.patch.object(factory_envelope, "factory_envelope", return_value=wide)
        env.start()
        self.addCleanup(env.stop)
        ready = mock.patch.object(fitting_setup, "status", return_value={"state": "ready", "note": ""})
        ready.start()
        self.addCleanup(ready.stop)
        pid = self.new_project()
        lut = self.assertJson(self.call("POST", "/api/projects/%s/film-luts" % pid, identity_cube(5), {"X-Filename": "same.cube"}), 200)
        self.assertEqual(self.call("GET", "/api/projects/%s/film-luts/%s/camera.cube" % (pid, lut["id"])).status, 400)
        with mock.patch.object(base_curve, "base_curve", return_value=fake_contract()):
            fitted = self.assertJson(self.call("POST", "/api/projects/%s/film-luts/%s/fit" % (pid, lut["id"])), 200)
            self.assertTrue(fitted["fitted"])
            self.assertEqual(fitted["fit"]["level"], "高")
            res = self.call("GET", "/api/projects/%s/film-luts/%s/camera.cube" % (pid, lut["id"]))
            self.assertEqual(res.status, 200)
            self.assertIn(b"LUT_3D_SIZE 17", res.body)
            res = self.call("GET", "/api/projects/%s/film-camera/gold200.cube" % pid)
            self.assertEqual(res.status, 200)
            grade = {"temp": 20}
            body = {"cube": identity_cube(5).decode(), "name": "怀旧 调色", "film": "gold200", "grade": grade}
            bake = self.assertJson(self.call("POST", "/api/projects/%s/film-bakes" % pid, body), 200)
            self.assertEqual(bake["bake"], {"film": "gold200", "grade": grade})
            self.assertTrue(bake["fitted"])
            self.assertEqual(self.store.bake_for(pid, "gold200", {"temp": 20}), bake["id"])
            self.assertIsNone(self.store.bake_for(pid, "gold200", {"temp": 21}))
            changes = {"9": {"film": "gold200", "grade": grade}, "8": {"film": "5219"}, "7": {"grade": {"sat": 4}}}
            out = self.store.film_changes_for_generate(pid, changes)
            self.assertEqual(out["9"]["film"], bake["id"])
            self.assertEqual(out["8"], {"film": "5219"})
            self.assertEqual(out["7"], {"grade": {"sat": 4}})
            with self.assertRaises(StoreError):
                self.store.film_changes_for_generate(pid, {"10": {"grade": {"con": 5}}})
            # a fitted LUT outside RICOH's own range is kept but cannot go to generation
            index_path = self.store._dir(pid) / "film_luts.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index[fitted["id"]]["fit"]["envelope"] = {"checked": True, "violations": ["曲线最大陡度 900 超出原厂范围（—～500）"]}
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaises(StoreError) as ctx:
                self.store.film_lut_profiles(pid, [fitted["id"]])
            self.assertIn("超出了理光相机原厂的范围", str(ctx.exception))
            bad = dict(body, grade={"temp": 500})
            self.assertEqual(self.call("POST", "/api/projects/%s/film-bakes" % pid, bad).status, 400)


if __name__ == "__main__":
    unittest.main()
