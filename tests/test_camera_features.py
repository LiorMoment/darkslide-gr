"""柔光 / 65:24 wide ratio switches (whole-camera features, user 2026-10-24): format checks, storage in the
draft and the saved project, the capability gate (2026-10-25: 柔光 on for GR III / GR IIIx, 65:24 still off),
the draft route, generation writing 柔光 and refusing a project that has 65:24 on instead of leaving it out."""
import json
import tempfile
import unittest
from unittest.mock import patch

import test_http
from darkslide import camera_features, generate, models
from darkslide.store import Store, StoreError

ALL_ON = dict(soft_glow=True, wide_ratio=True)


def caps_with(**extra):
    real = models.capabilities
    return lambda model: dict(real(model), **extra)


class Clean(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(camera_features.clean(None), {})
        self.assertEqual(camera_features.clean({}), {})
        self.assertEqual(camera_features.clean(dict(soft_glow=False, wide_ratio=False)), {})
        self.assertEqual(camera_features.clean(ALL_ON), ALL_ON)
        self.assertEqual(camera_features.clean(dict(soft_glow=True, wide_ratio=False)), dict(soft_glow=True))
        for bad in ([], "on", 1, dict(soft_glow=1), dict(soft_glow="true"), dict(glow=True), dict(soft_glow=None)):
            with self.assertRaises(camera_features.FeatureError, msg=repr(bad)):
                camera_features.clean(bad)

    def test_capability_gate(self):
        with self.assertRaisesRegex(camera_features.FeatureError, "柔光 还不能装进这台相机"):
            camera_features.clean(dict(soft_glow=True), dict(soft_glow=False, wide_ratio=False))
        with self.assertRaisesRegex(camera_features.FeatureError, "65:24 宽幅 还不能装进这台相机"):
            camera_features.clean(dict(wide_ratio=True), dict(soft_glow=True))
        self.assertEqual(camera_features.clean(dict(soft_glow=False), {}), {})   # off is always fine
        self.assertEqual(camera_features.clean(ALL_ON, dict(soft_glow=True, wide_ratio=True)), ALL_ON)

    def test_both_series_take_soft_glow_but_not_the_wide_ratio(self):
        for model in (models.GR3, models.GR3X):
            caps = models.capabilities(model)
            self.assertIs(caps["soft_glow"], True, model.name)
            self.assertIs(caps["wide_ratio"], False, model.name)

    def test_refusal_names_what_cannot_be_written(self):
        self.assertIsNone(camera_features.refusal(None))
        self.assertIsNone(camera_features.refusal({}))
        self.assertIsNone(camera_features.refusal(dict(soft_glow=True)))
        self.assertEqual(camera_features.refusal(ALL_ON), "65:24 宽幅还没有接进固件生成，这次不能生成；请先把开关关掉")

    def test_edits(self):
        self.assertEqual(camera_features.edits(None), [])
        self.assertEqual(camera_features.edits(dict(wide_ratio=True)), [])
        self.assertEqual(camera_features.edits(dict(soft_glow=True)), [dict(kind="soft_glow", version="v22.6")])


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.t = test_http.fake_template()
        sha = self.store.put_input(test_http.FAKE_OFFICIAL)
        self.pid = self.store.create_project("开关", sha, self.t)["id"]
        self.dir = self.store.root / "projects" / self.pid

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def test_old_project_has_none_on(self):
        p = self.store.get_project(self.pid)
        self.assertEqual(p["draft"]["camera_features"], {})
        self.assertEqual(p["camera_features"], {})
        self.assertFalse(p["unsaved"])

    def test_cannot_turn_on_while_the_model_cannot_take_it(self):
        with self.assertRaisesRegex(StoreError, "65:24 宽幅 还不能装进这台相机"):
            self.store.put_draft(self.pid, {"2": {"name": "V"}}, self.t, camera_features=dict(wide_ratio=True))
        self.assertNotIn("camera_features", json.loads((self.dir / "draft.json").read_text(encoding="utf-8")) if (self.dir / "draft.json").exists() else {})
        p = self.store.put_draft(self.pid, {}, self.t, camera_features=dict(soft_glow=False))
        self.assertEqual(p["draft"]["camera_features"], {})
        self.assertFalse(p["unsaved"])

    def test_on_off_draft_save_and_reopen(self):
        with patch.object(models, "capabilities", caps_with(soft_glow=True, wide_ratio=True)):
            p = self.store.put_draft(self.pid, {}, self.t, camera_features=ALL_ON)
            self.assertEqual(p["draft"]["camera_features"], ALL_ON)
            self.assertTrue(p["unsaved"])
            p = self.store.put_draft(self.pid, {}, self.t)          # a page that does not send them keeps them
            self.assertEqual(p["draft"]["camera_features"], ALL_ON)
            p = self.store.save(self.pid, self.t)
            self.assertFalse(p["unsaved"])
            self.assertEqual(self.read("project.json")["camera_features"], ALL_ON)
            self.assertEqual(self.read("versions/1.json")["camera_features"], ALL_ON)
            self.assertEqual(self.store.get_project(self.pid)["draft"]["camera_features"], ALL_ON)
            p = self.store.put_draft(self.pid, {}, self.t, camera_features=dict(soft_glow=False, wide_ratio=True))
            self.assertTrue(p["unsaved"])
            p = self.store.save(self.pid, self.t)
            self.assertEqual(self.read("project.json")["camera_features"], dict(wide_ratio=True))
            self.store.put_draft(self.pid, {}, self.t, camera_features={})
            self.store.save(self.pid, self.t)
            self.assertNotIn("camera_features", self.read("project.json"))

    def test_saved_switch_the_model_can_no_longer_take_is_not_saved_again(self):
        with patch.object(models, "capabilities", caps_with(wide_ratio=True)):
            self.store.put_draft(self.pid, {}, self.t, camera_features=dict(wide_ratio=True))
        with self.assertRaisesRegex(StoreError, "这次没有保存"):
            self.store.save(self.pid, self.t)

    def test_bad_shape_is_refused_and_nothing_written(self):
        before = (self.dir / "draft.json").read_bytes() if (self.dir / "draft.json").exists() else None
        with self.assertRaisesRegex(StoreError, "这次的改动没有写入"):
            self.store.put_draft(self.pid, {"2": {"name": "V"}}, self.t, camera_features=dict(glow=True))
        after = (self.dir / "draft.json").read_bytes() if (self.dir / "draft.json").exists() else None
        self.assertEqual(before, after)


class Generation(unittest.TestCase):
    def test_a_project_with_one_on_is_refused(self):
        t = test_http.fake_template()
        project = dict(id="synthetic", changes={"2": {"name": "V"}}, camera_features=dict(wide_ratio=True))
        with patch.object(generate.compositor, "build_composite", side_effect=AssertionError("no build")):
            with self.assertRaisesRegex(generate.GenerateError, "65:24 宽幅还没有接进固件生成"):
                generate.run(None, project, t, b"")

    def test_soft_glow_alone_is_a_change_and_reaches_the_compositor(self):
        t = test_http.fake_template()
        seen = {}

        def stop(official, edits, template, jpeg=None, **kw):
            seen["edits"] = edits
            raise generate.compositor.CompositeError("synthetic stop")

        project = dict(id="synthetic", changes={}, camera_features=dict(soft_glow=True))
        with patch.object(generate.compositor, "build_composite", side_effect=stop):
            with self.assertRaisesRegex(generate.GenerateError, "synthetic stop"):
                generate.run(None, project, t, b"")
        self.assertEqual(seen["edits"], [dict(kind="soft_glow", version="v22.6")])
        rows = generate.summarize({}, t, features=dict(soft_glow=True))
        self.assertEqual(rows[0]["preset"], "柔光")
        self.assertIn("v22.6", generate.verification_note(seen["edits"], t))


class DraftApi(test_http.Served):
    def test_route_refuses_turning_on_and_keeps_when_absent(self):
        pid = self.new_project()
        url = "/api/projects/%s/draft" % pid
        value = self.assertJson(self.call("GET", "/api/projects/%s" % pid), 200)
        self.assertEqual(value["draft"]["camera_features"], {})
        self.assertIs(value["capabilities"]["soft_glow"], True)
        self.assertIs(value["capabilities"]["wide_ratio"], False)
        res = self.call("PUT", url, {"changes": {}, "camera_features": {"wide_ratio": True}})
        self.assertEqual(res.status, 400)
        self.assertIn("65:24 宽幅 还不能装进这台相机", res.json()["error"])
        value = self.assertJson(self.call("PUT", url, {"changes": {}, "camera_features": {"soft_glow": True}}), 200)
        self.assertEqual(value["draft"]["camera_features"], {"soft_glow": True})
        value = self.assertJson(self.call("PUT", url, {"changes": {}, "camera_features": {"soft_glow": False}}), 200)
        self.assertEqual(value["draft"]["camera_features"], {})
        value = self.assertJson(self.call("PUT", url, {"changes": {}}), 200)
        self.assertEqual(value["draft"]["camera_features"], {})


if __name__ == "__main__":
    unittest.main()
