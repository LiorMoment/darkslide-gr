"""F31 menu order across a downgrade to the 1.0.0 release and back (review 2026-10-18, problem 1).

The 1.0.0 code (commit 9f3a921, VERSION 1.0.0) is taken out with `git archive` into a temporary
folder and run there in its own Python process, on the same data folder as this version. 1.0.0 writes
drafts as {changes, updated_at} only and saves with project.update(changes, saved_at): it opens, edits,
saves and renames a project this version saved with a menu order. Coming back, this version must still
have the saved order (a draft without the key stands for the saved order, not the factory one), saving
must keep it, and 恢复原厂顺序 must still work. With DARKSLIDE_OFFICIAL_BIN (GR III 2.10) 1.0.0 also
generates from such a project over its own HTTP API, and this version then generates it with its order.

Everything happens in temporary folders (the 1.0.0 copy, the data folder, the builds), removed
afterwards. Skipped where git or that commit is not available (e.g. a release copy without history)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_http
from darkslide import fwread, generate, menu_order, models, template as tpl
from darkslide.store import Store

RELEASE = "9f3a921"
APP = Path(__file__).resolve().parent.parent
OFFICIAL = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))
FACTORY = list(menu_order.FACTORY_STILL)
OFF = lambda va: va - 0x53000000 + 0x10870


def order(still):
    return dict(schema=1, still_slots=list(still), video_slots=menu_order.video_for(still))


def _git(*args, **kw):
    return subprocess.run(["git", "-C", str(APP), *args], capture_output=True, timeout=300, **kw)


def release_available():
    try:
        return _git("cat-file", "-e", RELEASE + "^{commit}").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Runs inside the 1.0.0 copy: argv = data folder, plan (JSON). Prints what it saw as JSON.
OLD_RUNNER = r"""
import http.client, json, sys, threading
root, plan = sys.argv[1], json.loads(sys.argv[2])
import darkslide
from darkslide import template as tpl
from darkslide.store import Store
out = dict(version=darkslide.VERSION, package=darkslide.__file__, steps=[])
store, t, pid = Store(root), tpl.load(), plan["pid"]
served = {}

def call(method, path, body=None):
    if "httpd" not in served:
        from darkslide.server import App, LocalServer
        httpd = LocalServer(("127.0.0.1", 0), App(root))
        threading.Thread(target=httpd.serve_forever, kwargs=dict(poll_interval=0.05), daemon=True).start()
        served["httpd"] = httpd
    port = served["httpd"].server_address[1]
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Host": "127.0.0.1:%d" % port}
    if data is not None:
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(data))})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1800)
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    return res.status, res.read()

for step in plan["steps"]:
    kind = step[0]
    if kind == "open":                      # what the 1.0.0 page reads when it opens the project
        p = store.get_project(pid)
        out["steps"].append(["open", p["unsaved"], p["draft"].get("changes", {}), p["title"]])
    elif kind == "push":                    # autosave / page close: the 1.0.0 page sends only the changes
        changes = step[1] if len(step) > 1 else store.get_project(pid)["draft"].get("changes", {})
        p = store.put_draft(pid, changes, t)
        out["steps"].append(["push", p["unsaved"]])
    elif kind == "save":
        p = store.save(pid, t)
        out["steps"].append(["save", p["unsaved"], len(p["versions"])])
    elif kind == "rename":
        out["steps"].append(["rename", store.rename_project(pid, step[1])["title"]])
    elif kind == "http":                    # the 1.0.0 HTTP API, as its page uses it
        status, body = call(step[1], step[2] % {"pid": pid}, step[3] if len(step) > 3 else None)
        value = json.loads(body.decode("utf-8")) if body[:1] in (b"{", b"[") else None
        out["steps"].append(["http", step[1], step[2], status, value])
    elif kind == "download":                # a build's firmware file, written where the plan says
        status, body = call("GET", "/api/builds/%s/firmware" % step[1])
        with open(step[2], "wb") as fh:
            fh.write(body)
        out["steps"].append(["download", status, len(body)])
print(json.dumps(out, ensure_ascii=False))
"""


class Release:
    """The 1.0.0 copy, once per test class."""

    @classmethod
    def extract_release(cls):
        cls.release_tmp = tempfile.TemporaryDirectory(prefix="darkslide-1.0.0-")
        # The commit is there (checked before the class runs), so a failure here is an error, not a skip.
        top = _git("rev-parse", "--show-toplevel").stdout.decode("utf-8").strip()
        archive = subprocess.run(["git", "-C", top, "archive", "--format=tar", RELEASE, "app"], capture_output=True, timeout=300)
        if archive.returncode != 0:
            cls.release_tmp.cleanup()
            raise RuntimeError("git archive %s 失败：%s" % (RELEASE, archive.stderr.decode("utf-8", "replace")))
        unpacked = subprocess.run(["tar", "-x", "-C", cls.release_tmp.name], input=archive.stdout, capture_output=True, timeout=300)
        if unpacked.returncode != 0:
            cls.release_tmp.cleanup()
            raise RuntimeError("tar 解不开 %s：%s" % (RELEASE, unpacked.stderr.decode("utf-8", "replace")))
        cls.old_app = Path(cls.release_tmp.name) / "app"

    @classmethod
    def drop_release(cls):
        cls.release_tmp.cleanup()

    def old(self, root, pid, *steps):
        """Run these steps with the 1.0.0 code on the data folder `root`."""
        env = dict(os.environ, PYTHONPATH=str(self.old_app), DARKSLIDE_DATA=str(root))
        for name in ("DARKSLIDE_OFFICIAL_BIN", "DARKSLIDE_IIIX_BIN", "DARKSLIDE_READBACK_ORIGINAL"):
            env.pop(name, None)
        run = subprocess.run([sys.executable, "-B", "-c", OLD_RUNNER, str(root), json.dumps(dict(pid=pid, steps=list(steps)))],
                             cwd=str(self.old_app), env=env, capture_output=True, text=True, timeout=3600)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(out["version"], "1.0.0")
        self.assertTrue(str(Path(out["package"]).resolve()).startswith(str(self.old_app.resolve()) + os.sep), out["package"])
        return out["steps"]


@unittest.skipUnless(release_available(), "需要 git 和 1.0.0 的提交 %s" % RELEASE)
class DowngradeRoundTrip(Release, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.extract_release()

    @classmethod
    def tearDownClass(cls):
        cls.drop_release()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.t = test_http.fake_template()
        sha = self.store.put_input(test_http.FAKE_OFFICIAL)
        self.pid = self.store.create_project("排序", sha, self.t)["id"]
        self.dir = self.root / "projects" / self.pid

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def new(self):
        """This version, opening the project afresh."""
        return Store(self.root).get_project(self.pid)

    def test_release_is_the_1_0_0_code(self):
        source = (self.old_app / "darkslide" / "store.py").read_text(encoding="utf-8")
        self.assertNotIn("menu_order", source)          # 1.0.0 knows nothing about menu orders
        self.assertIn('write_json(path / "draft.json", dict(changes=clean, updated_at=now()))', source)

    def test_saved_order_survives_opening_editing_saving_and_renaming_in_1_0_0(self):
        o = order([12] + [s for s in FACTORY if s != 12])
        self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=o)
        self.store.save(self.pid, self.t)
        self.assertEqual((self.read("project.json")["menu_order"], self.read("draft.json")["menu_order"]), (o, o))

        steps = self.old(self.root, self.pid, ["open"], ["push"],
                         ["push", {"7": {"name": "Posi Warm"}, "2": {"name": "Vivid+"}}], ["save"],
                         ["rename", "旧版改过"], ["push"])
        self.assertEqual(steps[0], ["open", False, {"7": {"name": "Posi Warm"}}, "排序"])   # 1.0.0 opens it as saved
        self.assertEqual(steps[3], ["save", False, 2])
        # what the review found: 1.0.0's draft has no menu_order at all; its save keeps the project's
        self.assertNotIn("menu_order", self.read("draft.json"))
        self.assertEqual(self.read("project.json")["menu_order"], o)

        p = self.new()
        self.assertEqual(p["menu_order"], o)
        self.assertEqual(p["draft"].get("menu_order"), o, "回到新版时显示已保存的顺序")
        self.assertFalse(p["unsaved"], "1.0.0 保存过的工程回来不是「未保存」")
        self.assertEqual((p["title"], p["draft"]["changes"]), ("旧版改过", {"7": {"name": "Posi Warm"}, "2": {"name": "Vivid+"}}))
        self.assertNotIn("menu_order_unreadable", p["draft"])
        self.assertEqual(self.store.list_projects()[0]["changes"], 3)

        # This version's page loads p.draft.menu_order and sends it with every draft write, then ⌘S.
        p = self.store.put_draft(self.pid, p["draft"]["changes"], self.t, menu_order=p["draft"].get("menu_order"))
        self.assertFalse(p["unsaved"])
        p = self.store.save(self.pid, self.t)
        self.assertEqual((self.read("project.json")["menu_order"], p["menu_order"]), (o, o))
        self.assertEqual(self.read("versions/3.json")["menu_order"], o)
        # an older page of this version (no menu_order key) keeps it too
        p = self.store.put_draft(self.pid, p["draft"]["changes"], self.t)
        self.assertEqual((p["draft"].get("menu_order"), p["unsaved"]), (o, False))

    def test_1_0_0_edits_without_saving_then_this_version_saves(self):
        o = order(FACTORY[::-1])
        self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        self.store.save(self.pid, self.t)
        self.old(self.root, self.pid, ["open"], ["push", {"2": {"name": "Vivid+"}}])    # edited in 1.0.0, not saved
        p = self.new()
        self.assertEqual(p["draft"].get("menu_order"), o)
        self.assertTrue(p["unsaved"])                     # because of the rename only
        self.assertEqual(p["draft"]["changes"], {"2": {"name": "Vivid+"}})
        self.store.put_draft(self.pid, p["draft"]["changes"], self.t, menu_order=p["draft"].get("menu_order"))
        p = self.store.save(self.pid, self.t)
        self.assertEqual((p["menu_order"], p["changes"], p["unsaved"]), (o, {"2": {"name": "Vivid+"}}, False))

    def test_restoring_the_factory_order_still_works_across_versions(self):
        o = order(FACTORY[::-1])
        self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=o)
        self.store.save(self.pid, self.t)
        self.old(self.root, self.pid, ["open"], ["push"])
        # back here: 恢复原厂顺序 writes an explicit null, which is not "the saved order"
        p = self.store.put_draft(self.pid, {"7": {"name": "Posi Warm"}}, self.t, menu_order=None)
        self.assertIsNone(self.read("draft.json")["menu_order"])
        self.assertIsNone(p["draft"].get("menu_order"))
        self.assertTrue(p["unsaved"])
        p = self.store.save(self.pid, self.t)
        self.assertNotIn("menu_order", self.read("project.json"))
        self.assertFalse(p["unsaved"])
        # 1.0.0 opens, edits and saves it; back here it is still the factory order
        steps = self.old(self.root, self.pid, ["open"], ["push", {"7": {"name": "Posi Warm"}, "3": {"name": "Mono+"}}], ["save"])
        self.assertEqual(steps[0][1], False)
        p = self.new()
        self.assertIsNone(p["draft"].get("menu_order"))
        self.assertNotIn("menu_order", p)
        self.assertFalse(p["unsaved"])
        self.assertEqual(self.store.list_projects()[0]["changes"], 2)

    def test_project_saved_by_1_0_0_alone_is_the_factory_order(self):
        # A project made and saved only by 1.0.0 (no menu_order anywhere) is the factory order here.
        self.old(self.root, self.pid, ["open"], ["push", {"2": {"name": "Vivid+"}}], ["save"])
        p = self.new()
        self.assertIsNone(p["draft"].get("menu_order"))
        self.assertFalse(p["unsaved"])
        self.assertNotIn("menu_order", self.read("draft.json"))

    def test_unsaved_order_and_1_0_0_draft_write(self):
        # Known limit, written down in the 回执: an order sorted here but NOT saved is not kept when 1.0.0
        # rewrites the draft (1.0.0 writes the draft file without it). The saved state is what comes back,
        # without an error and without touching the saved order.
        o, newer = order(FACTORY[::-1]), order([7] + [s for s in FACTORY if s != 7])
        self.store.put_draft(self.pid, {}, self.t, menu_order=o)
        self.store.save(self.pid, self.t)
        self.store.put_draft(self.pid, {}, self.t, menu_order=newer)          # sorted again, not saved
        self.old(self.root, self.pid, ["open"], ["push"])
        p = self.new()
        self.assertEqual((p["menu_order"], p["draft"].get("menu_order"), p["unsaved"]), (o, o, False))


@unittest.skipUnless(release_available() and OFFICIAL.exists(),
                     "需要 git、1.0.0 的提交 %s 和 DARKSLIDE_OFFICIAL_BIN（GR III 2.10 官方原件）" % RELEASE)
class RealDowngrade(Release, unittest.TestCase):
    """1.0.0 generates over its own HTTP API from a project this version saved with an order and a
    rename (the order is not written: 1.0.0 cannot sort); back here the order is still saved and
    this version's build carries it. Both builds stay in the temporary data folder."""

    @classmethod
    def setUpClass(cls):
        cls.extract_release()

    @classmethod
    def tearDownClass(cls):
        cls.drop_release()

    def test_1_0_0_generates_and_the_order_comes_back(self):
        raw = OFFICIAL.read_bytes()
        before = fwread.unpack(raw)
        tone = {s: s + 10 for s in FACTORY}
        still = [12] + [s for s in FACTORY if s != 12]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, t = Store(root), tpl.load()
            sha = store.put_input(raw)
            self.assertEqual(sha, models.GR3.input_sha)
            pid = store.create_project("降级", sha, t, target_body="GR III")["id"]
            store.put_draft(pid, {"2": {"name": "Vivid+"}}, t, menu_order=order(still))
            store.save(pid, t)

            out = root / "old_build.bin"
            steps = self.old(root, pid, ["http", "GET", "/api/projects/%(pid)s"], ["push"],
                             ["http", "POST", "/api/projects/%(pid)s/save", {}],
                             ["http", "POST", "/api/projects/%(pid)s/generate", {}])
            self.assertEqual(steps[0][3], 200)
            self.assertEqual(steps[2][3], 200, steps[2])
            self.assertEqual(steps[3][3], 200, steps[3])
            build = steps[3][4]
            self.assertTrue(all(c["ok"] is True for c in build["checks"]))
            self.assertNotIn("menu_order", build)
            steps = self.old(root, pid, ["download", build["build_id"], str(out)])
            self.assertEqual(steps[0][1], 200)
            after = fwread.unpack(out.read_bytes())
            self.assertEqual(len(after), len(before))
            for va in menu_order.STILL_VA:       # 1.0.0 leaves the menu as the factory made it
                self.assertEqual(list(after[OFF(va):OFF(va) + 12]), [tone[s] for s in FACTORY])
            vivid = next(s for s in t["slots"] if s["no"] == 2)
            self.assertEqual(fwread.Payload(after, pinned=False).string(vivid["string_id"], "en")["text"], "Vivid+")

            p = store.get_project(pid)
            self.assertEqual((p["menu_order"], p["draft"].get("menu_order"), p["unsaved"]), (order(still), order(still), False))
            output, manifest = generate.run(store, p, t, raw)
            self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
            self.assertEqual(manifest["menu_order"]["still_slots"], still)
            mine = fwread.unpack(output)
            for va in menu_order.STILL_VA:
                self.assertEqual(list(mine[OFF(va):OFF(va) + 12]), [tone[s] for s in still])


if __name__ == "__main__":
    unittest.main()
