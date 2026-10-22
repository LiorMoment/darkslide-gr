import json
import os
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from firmware_registry_fixture import http_identity
from darkslide import names, template as tpl
from darkslide.identify import digest_of as sha256, identify
from darkslide.server import App, LocalServer
from darkslide.store import Store, StoreError


def png(width, height, alpha=None):
    """RGBA PNG; default alpha follows the factory icon contract."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    alpha = alpha or (lambda x, y: 255 if 1 <= x < 39 and 2 <= y < 38 else 0)
    raw = b"".join(b"\x00" + b"".join(bytes((40, 40, 40, alpha(x, y))) for x in range(width)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FAKE_OFFICIAL = b"official-firmware-bytes"


def test_template():
    t = tpl.load()
    t = json.loads(json.dumps(t))
    t["official_inputs"] = [dict(filename="fwdc239b.bin", size=len(FAKE_OFFICIAL), sha256=sha256(FAKE_OFFICIAL))]
    return t


class NameRules(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(names.name_ok("Posi Warm", 13))

    def test_each_rule(self):
        cases = {"": "not_empty", "Positive Film X": "length", "正片": "charset", "a\nb": "single_line",
                 " Posi": "no_edge_space", "Posi  Warm": "no_double_space"}
        for value, rule in cases.items():
            failed = {c["rule"] for c in names.check_name(value, 13) if not c["ok"]}
            self.assertIn(rule, failed, value)

    def test_limit_is_inclusive(self):
        self.assertTrue(names.name_ok("Positive Film", 13))
        self.assertFalse(names.name_ok("Positive Films", 13))


class Template(unittest.TestCase):
    def test_real_template(self):
        t = tpl.load()
        self.assertEqual(len(t["slots"]), 14)
        self.assertTrue(t["reviewed"])
        self.assertEqual(tpl.slot(t, 11)["factory_en"], "Cross Processing")
        self.assertFalse(tpl.can_edit(t, 13, "icon"))
        self.assertFalse(tpl.can_edit(t, 1, "rename"))
        self.assertTrue(tpl.can_edit(t, 7, "rename"))

    def test_rejects_duplicate_slots(self):
        t = tpl.load()
        t["slots"].append(dict(t["slots"][0]))
        with self.assertRaises(tpl.TemplateError):
            tpl.validate(t)


class Identify(unittest.TestCase):
    def setUp(self):
        self.t = test_template()

    def test_official(self):
        self.assertEqual(identify(FAKE_OFFICIAL, "fwdc239b.bin", self.t, [])["kind"], "official")

    def test_generated(self):
        data = b"generated"
        r = identify(data, "fwdc239b.bin", self.t, [dict(output_sha256=sha256(data), project_id="p_000000000000")])
        self.assertEqual((r["kind"], r["project_id"]), ("generated", "p_000000000000"))

    def test_modified_same_size(self):
        data = b"x" * len(FAKE_OFFICIAL)
        r = identify(data, "fwdc239b.bin", self.t, [])
        self.assertEqual(r["kind"], "rejected")
        self.assertIn("可能被改过", r["reason"])

    def test_other_file(self):
        self.assertEqual(identify(b"abc", "photo.jpg", self.t, [])["kind"], "rejected")


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.t = test_template()
        self.sha = self.store.put_input(FAKE_OFFICIAL)
        self.p = self.store.create_project("测试", self.sha, self.t)

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_imported_input(self):
        with self.assertRaises(StoreError):
            self.store.create_project("x", "0" * 64, self.t)

    def test_the_same_draft_again_changes_nothing(self):
        # the page pushes its draft when it reloads; a 生成 running meanwhile must not see a change
        pid = self.p["id"]
        self.store.put_draft(pid, {"7": {"name": "Posi Warm"}}, self.t)
        self.store.save(pid, self.t)
        before = self.store.get_project(pid)
        self.assertEqual(self.store.put_draft(pid, {"7": {"name": "Posi Warm"}}, self.t), before)
        self.assertNotEqual(self.store.put_draft(pid, {"7": {"name": "Posi"}}, self.t), before)

    def test_draft_then_save(self):
        pid = self.p["id"]
        p = self.store.put_draft(pid, {"7": {"name": "Posi Warm"}}, self.t)
        self.assertTrue(p["unsaved"])
        p = self.store.save(pid, self.t)
        self.assertFalse(p["unsaved"])
        self.assertEqual(p["changes"], {"7": {"name": "Posi Warm"}})
        self.assertEqual(len(p["versions"]), 1)
        self.assertTrue((Path(self.tmp.name) / "projects" / pid / "versions" / "1.json").exists())

    def test_draft_keeps_bad_name_but_save_refuses(self):
        pid = self.p["id"]
        self.store.put_draft(pid, {"7": {"name": "Positive Film Warmer"}}, self.t)  # 20 > 18
        with self.assertRaises(StoreError):
            self.store.save(pid, self.t)

    def test_closed_slots_refused(self):
        pid = self.p["id"]
        with self.assertRaises(StoreError):
            self.store.put_draft(pid, {"1": {"name": "Std"}}, self.t)
        icon = self.store.put_asset(pid, png(40, 40), expect_icon=True)["asset"]
        with self.assertRaises(StoreError):
            self.store.put_draft(pid, {"13": {"icon": {"asset": icon, "style": "image"}}}, self.t)

    def test_icon_asset(self):
        pid = self.p["id"]
        with self.assertRaises(StoreError):
            self.store.put_asset(pid, png(38, 36), expect_icon=True)
        icon = self.store.put_asset(pid, png(40, 40), expect_icon=True)["asset"]
        p = self.store.put_draft(pid, {"11": {"icon": {"asset": icon, "style": "factory", "text": "X", "junk": 1}}}, self.t)
        self.assertEqual(p["draft"]["changes"]["11"]["icon"], {"asset": icon, "style": "factory", "text": "X"})
        with self.assertRaises(StoreError):
            self.store.put_draft(pid, {"11": {"icon": {"asset": "0" * 64, "style": "image"}}}, self.t)

    def test_icon_contract(self):
        pid = self.p["id"]
        with self.assertRaises(StoreError):
            self.store.put_asset(pid, png(40, 40, alpha=lambda x, y: 255), expect_icon=True)
        with self.assertRaises(StoreError):
            self.store.put_asset(pid, png(40, 40, alpha=lambda x, y: 128 if (x, y) == (20, 20) else (255 if 1 <= x < 39 and 2 <= y < 38 else 0)), expect_icon=True)
        self.store.put_asset(pid, png(300, 200, alpha=lambda x, y: 128), expect_icon=False)

    def test_bad_project_id(self):
        with self.assertRaises(StoreError):
            self.store.get_project("../../etc")


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = App(cls.tmp.name)
        cls.app.template = test_template()
        identity = http_identity(sha256(FAKE_OFFICIAL), len(FAKE_OFFICIAL), lambda: cls.app.template)
        identity.__enter__(); cls.addClassCleanup(identity.__exit__, None, None, None)
        cls.app.extract_factory = lambda digest, data: None
        cls.httpd = LocalServer(("127.0.0.1", 0), cls.app)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method,
                                   headers=headers or {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        status, body = self.req("POST", "/api/firmware", FAKE_OFFICIAL, {"X-Filename": "fwdc239b.bin"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["kind"], "official")
        status, body = self.req("POST", "/api/projects", {"title": "t", "input_sha256": sha256(FAKE_OFFICIAL)})
        pid = json.loads(body)["id"]
        status, body = self.req("POST", "/api/projects/%s/assets?kind=icon" % pid, png(40, 40))
        asset = json.loads(body)["asset"]
        status, _ = self.req("PUT", "/api/projects/%s/draft" % pid, {"changes": {"12": {"icon": {"asset": asset, "style": "image"}}}})
        self.assertEqual(status, 200)
        status, body = self.req("POST", "/api/projects/%s/save" % pid)
        self.assertEqual(json.loads(body)["changes"]["12"]["icon"]["asset"], asset)
        status, body = self.req("GET", "/api/projects/%s/assets/%s" % (pid, asset))
        self.assertTrue(body.startswith(b"\x89PNG"))
        status, body = self.req("POST", "/api/projects/%s/generate" % pid)
        # the fake input is not the official file, so the builder refuses and nothing is stored
        self.assertEqual(status, 422)
        self.assertIn("无法生成", json.loads(body)["error"])
        self.assertEqual(self.app.store.builds(), [])

    def test_check_name(self):
        status, body = self.req("POST", "/api/check-name", {"slot": 11, "name": "Day 200"})
        value = json.loads(body)
        self.assertEqual((value["ok"], value["limit"]), (True, 16))

    def test_rejects_foreign_origin(self):
        status, _ = self.req("POST", "/api/prefs", {"key": "preflight_seen", "value": True}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_rejects_foreign_host(self):
        status, _ = self.req("GET", "/api/status", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_static_traversal(self):
        status, _ = self.req("GET", "/../darkslide/server.py")
        self.assertEqual(status, 404)
        status, body = self.req("GET", "/")
        self.assertIn(b"DARKSLIDE", body)


class Quit(unittest.TestCase):
    def test_quit_stops_server(self):
        tmp = tempfile.TemporaryDirectory()
        app = App(tmp.name)
        httpd = LocalServer(("127.0.0.1", 0), app)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        r = urllib.request.Request("http://127.0.0.1:%d/api/quit" % port, data=b"{}", method="POST")
        with urllib.request.urlopen(r) as resp:
            self.assertEqual(json.loads(resp.read()), {"ok": True})
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        httpd.server_close()
        tmp.cleanup()

    def test_quit_refuses_foreign_origin(self):
        tmp = tempfile.TemporaryDirectory()
        app = App(tmp.name)
        httpd = LocalServer(("127.0.0.1", 0), app)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        r = urllib.request.Request("http://127.0.0.1:%d/api/quit" % port, data=b"{}", method="POST",
                                   headers={"Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 403)
        self.assertTrue(t.is_alive())
        httpd.shutdown()
        httpd.server_close()
        tmp.cleanup()


# Set DARKSLIDE_OFFICIAL_BIN to your own GR III 2.10 fwdc239b.bin to run these.
OFFICIAL = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))


@unittest.skipUnless(OFFICIAL.exists(), "official firmware not available on this machine")
class RealFirmware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from darkslide import fwread
        cls.fwread = fwread
        cls.payload = fwread.Payload(fwread.unpack(OFFICIAL.read_bytes()))
        cls.t = tpl.load()

    def test_every_slot_matches_template(self):
        for slot in self.t["slots"]:
            desc, pixels = self.payload.icon_pixels(slot["icon_id"])
            self.assertEqual((desc["width"], desc["height"]), (40, 40))
            self.assertEqual(pixels[3::4].count(0), 232)
            self.assertEqual(self.payload.string(slot["string_id"])["text"], slot["factory_en"])

    def test_factory_icon_round_trips_through_png(self):
        _, pixels = self.payload.icon_pixels(358)
        png_bytes = pngcheck_write(self.fwread.icon_to_rgba(pixels))
        from darkslide import pngcheck
        self.assertTrue(pngcheck.check_icon(png_bytes))
        _, _, rows = pngcheck.rgba_rows(png_bytes)
        rgba = b"".join(rows)
        self.assertEqual(self.fwread.rgba_to_icon(rgba), self.fwread.icon_to_rgba(pixels))

    def test_rejects_other_payload(self):
        with self.assertRaises(self.fwread.FirmwareReadError):
            self.fwread.Payload(b"x" * 100)


def pngcheck_write(rgba):
    from darkslide import pngcheck
    return pngcheck.write_png(40, 40, rgba)


if __name__ == "__main__":
    unittest.main()
