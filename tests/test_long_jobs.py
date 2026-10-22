"""生成 and 拟合 as jobs: POST .../jobs, then GET .../jobs/<job> (darkslide/server.py, "long requests as jobs").

A job runs the same route function the plain endpoint runs, so these tests stand in for the slow part
(the firmware build, the fit) and check what the job layer promises: the answer is the plain answer,
one 生成 at a time, the same fit joins instead of running twice, a reloaded page can find the job."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from darkslide import film_luts, fwread, lut_ranges, models, server
from darkslide import template as tpl

GOLD = film_luts.preview_path("gold200").read_text(encoding="utf-8")
FAIL_CHECKS = [dict(ok=True, title="a"), dict(ok=False, title="b", detail="why")]


def ask(app, pid, body):
    request = SimpleNamespace(app=app, store=app.store, read_json=lambda: json.loads(json.dumps(body)))
    return server.api_start_job(request, pid)


def status(app, pid, job):
    return server.api_job_status(SimpleNamespace(app=app, store=app.store), pid, job)


def active(app, pid):
    return server.api_list_jobs(SimpleNamespace(app=app, store=app.store), pid)["jobs"]


def wait_for(app, pid, job, states=("done", "failed")):
    for _ in range(500):
        out = status(app, pid, job)
        if out["state"] in states:
            return out
        time.sleep(0.01)
    raise AssertionError("job did not finish: %r" % status(app, pid, job))


class Setup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = server.App(tmp.name)
        with patch.object(self.app.store, "has_input", return_value=True):
            self.pid = self.app.store.create_project("jobs", fwread.V210_INPUT_SHA256, tpl.load())["id"]
            self.other = self.app.store.create_project("other", fwread.V210_INPUT_SHA256, tpl.load())["id"]
        self.lid = self.app.store.put_film_lut(self.pid, GOLD.encode(), "mine.cube")["id"]
        # its own runner, as in test_lut_ranges: other tests may stop the shared one
        patcher = patch.object(lut_ranges, "JOBS", lut_ranges.Jobs())
        patcher.start()
        self.addCleanup(patcher.stop)


class Answers(Setup):
    def test_a_generation_job_answers_what_the_plain_request_answers(self):
        manifest = dict(build_id="b_000000000001", checks=[dict(ok=True)], summary=[])
        with patch.object(server, "api_generate", return_value=manifest):
            out = ask(self.app, self.pid, dict(task="generate"))
            self.assertEqual((out["task"], out["state"] in ("queued", "running", "done")), ("generate", True))
            done = wait_for(self.app, self.pid, out["job"])
        self.assertEqual((done["state"], done["result"]), ("done", manifest))

    def test_a_refused_generation_keeps_its_status_error_and_checks(self):
        refused = server.Reply({"error": "还不能生成", "checks": FAIL_CHECKS}, 422)
        with patch.object(server, "api_generate", return_value=refused):
            failed = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="generate"))["job"])
        self.assertEqual((failed["state"], failed["status"], failed["error"]), ("failed", 422, "还不能生成"))
        self.assertEqual(failed["body"], {"error": "还不能生成", "checks": FAIL_CHECKS})
        self.assertNotIn("result", failed)

    def test_route_errors_are_mapped_like_a_plain_request(self):
        for raised, status_code, message in ((server.ApiError(409, "找不到官方原件，请重新导入"), 409, "找不到官方原件，请重新导入"),
                                             (film_luts.FilmLutError("坏的 LUT"), 422, "坏的 LUT"),
                                             (KeyError("x"), 400, "请求参数错误")):
            with self.subTest(raised=raised), patch.object(server, "api_generate", side_effect=raised):
                failed = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="generate"))["job"])
                self.assertEqual((failed["state"], failed["status"], failed["error"]), ("failed", status_code, message))
        with patch.object(server, "api_generate", side_effect=RuntimeError("boom")), self.assertLogs("darkslide.http", "ERROR"):
            failed = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="generate"))["job"])
        self.assertEqual((failed["state"], failed["status"], failed["error"]), ("failed", 500, "boom"))

    def test_the_task_gets_the_body_the_plain_endpoint_would_get(self):
        seen = []
        def fit(req, pid, lid):
            seen.append(("fit", pid, lid))
            return {"id": lid}
        def bake(req):
            seen.append(("bake", req.read_json()))
            return {"id": "baked"}
        body = dict(cube="c", name="n", film=None, grade={"exp": 5})
        with patch.object(server, "api_fit_film_lut", side_effect=fit), patch.object(server, "api_bake_grade", side_effect=lambda req, pid: bake(req)):
            fitted = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="film-fit", lut=self.lid))["job"])
            baked = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="film-bake", body=body))["job"])
        self.assertEqual((fitted["result"], baked["result"]), ({"id": self.lid}, {"id": "baked"}))
        self.assertEqual(seen, [("fit", self.pid, self.lid), ("bake", body)])

    def test_iiix_candidate_checks_its_body_like_the_plain_endpoint(self):
        failed = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="iiix-candidate", body={}))["job"])
        self.assertEqual((failed["state"], failed["status"], failed["error"]), ("failed", 400, "候选请求格式不正确"))
        self.assertTrue(self.app.generation_lock.acquire(blocking=False))  # and the lock was given back
        self.app.generation_lock.release()


class OneGeneration(Setup):
    def hold(self):
        entered, release = threading.Event(), threading.Event()
        def slow(req, pid):
            entered.set()
            release.wait(10)
            return {"build_id": "b_000000000001"}
        return entered, release, patch.object(server, "api_generate", side_effect=slow)

    def test_a_second_generation_is_refused_while_one_runs_then_allowed(self):
        entered, release, slow = self.hold()
        with slow:
            first = ask(self.app, self.pid, dict(task="generate"))
            self.assertTrue(entered.wait(5))
            for pid in (self.pid, self.other):  # the same project and another one
                with self.assertRaises(server.ApiError) as caught:
                    ask(self.app, pid, dict(task="generate"))
                self.assertEqual((caught.exception.status, str(caught.exception)), (409, "已有生成正在进行，请稍后再试"))
            with self.assertRaises(server.ApiError) as caught:  # nor the IIIx candidate
                ask(self.app, self.pid, dict(task="iiix-candidate", body={}))
            self.assertEqual(caught.exception.status, 409)
            release.set()
            wait_for(self.app, self.pid, first["job"])
        with patch.object(server, "api_generate", return_value={"build_id": "b_000000000002"}):
            second = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="generate"))["job"])
        self.assertEqual(second["result"], {"build_id": "b_000000000002"})

    def test_a_failing_generation_gives_the_lock_back(self):
        with patch.object(server, "api_generate", side_effect=RuntimeError("boom")), self.assertLogs("darkslide.http", "ERROR"):
            wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="generate"))["job"])
        self.assertTrue(self.app.generation_lock.acquire(blocking=False))
        self.app.generation_lock.release()

    def test_a_refused_hand_in_leaves_nothing_behind(self):
        for bad in ({}, {"task": "delete"}, {"task": "generate", "body": []}, {"task": "generate", "x": 1}, {"task": "film-fit"},
                    {"task": "film-fit", "lut": "../x"}, {"task": "generate", "lut": self.lid}):
            with self.subTest(bad=bad), self.assertRaises(server.ApiError) as caught:
                ask(self.app, self.pid, bad)
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.app.long_jobs, {})
        self.assertTrue(self.app.generation_lock.acquire(blocking=False))
        self.app.generation_lock.release()


class Fits(Setup):
    def test_the_same_fit_asked_again_joins_the_running_one(self):
        entered, release, calls = threading.Event(), threading.Event(), []
        def slow(req, pid, lid):
            calls.append(lid)
            entered.set()
            release.wait(10)
            return {"id": lid}
        with patch.object(server, "api_fit_film_lut", side_effect=slow):
            first = ask(self.app, self.pid, dict(task="film-fit", lut=self.lid))
            self.assertTrue(entered.wait(5))
            again = ask(self.app, self.pid, dict(task="film-fit", lut=self.lid))
            self.assertEqual(again["job"], first["job"])
            release.set()
            self.assertEqual(wait_for(self.app, self.pid, first["job"])["result"], {"id": self.lid})
        self.assertEqual(calls, [self.lid])

    def test_fits_wait_in_line_and_do_not_block_a_generation(self):
        gate = threading.Event()
        started = []
        def slow(req, pid, lid):
            started.append(lid)
            gate.wait(10)
            return {"id": lid}
        second_lid = self.app.store.put_film_lut(self.pid, (GOLD + "# two\n").encode(), "two.cube")["id"]
        with patch.object(server, "api_fit_film_lut", side_effect=slow), patch.object(server, "api_generate", return_value={"build_id": "b_000000000001"}):
            one = ask(self.app, self.pid, dict(task="film-fit", lut=self.lid))
            two = ask(self.app, self.pid, dict(task="film-fit", lut=second_lid))
            build = ask(self.app, self.pid, dict(task="generate"))
            self.assertEqual(wait_for(self.app, self.pid, build["job"])["state"], "done")
            for _ in range(200):
                if started:
                    break
                time.sleep(0.01)
            self.assertEqual(status(self.app, self.pid, two["job"])["state"], "queued")
            self.assertEqual(status(self.app, self.pid, two["job"])["ahead"], 0)
            gate.set()
            wait_for(self.app, self.pid, one["job"])
            wait_for(self.app, self.pid, two["job"])
        self.assertEqual(started, [self.lid, second_lid])

    def test_a_fit_refused_by_the_camera_range_keeps_its_message(self):
        message = "超出了理光相机原厂的范围：曲线起点 1 超出原厂范围（2～3）。没有拟合进相机，也没有保存；可以点「收进相机范围」看看能写进相机的版本。"
        with patch.object(server, "api_fit_film_lut", side_effect=server.ApiError(422, message)):
            failed = wait_for(self.app, self.pid, ask(self.app, self.pid, dict(task="film-fit", lut=self.lid))["job"])
        self.assertEqual((failed["state"], failed["status"], failed["error"]), ("failed", 422, message))


class Finding(Setup):
    def test_a_reloaded_page_finds_the_running_generation_only(self):
        entered, release = threading.Event(), threading.Event()
        def slow(req, pid):
            entered.set()
            release.wait(10)
            return {"build_id": "b_000000000001"}
        with patch.object(server, "api_generate", side_effect=slow):
            job = ask(self.app, self.pid, dict(task="generate"))["job"]
            self.assertTrue(entered.wait(5))
            self.assertEqual([(j["job"], j["task"], j["state"]) for j in active(self.app, self.pid)], [(job, "generate", "running")])
            self.assertEqual(active(self.app, self.other), [])  # another project's page sees nothing
            self.assertEqual(status(self.app, self.other, job), {"state": "none", "job": job})
            release.set()
            wait_for(self.app, self.pid, job)
        self.assertEqual(active(self.app, self.pid), [])
        self.assertEqual(status(self.app, self.pid, job)["state"], "done")  # still readable for a while

    def test_an_unknown_job_is_none_and_old_finished_jobs_are_forgotten(self):
        self.assertEqual(status(self.app, self.pid, "0123456789abcdef"), {"state": "none", "job": "0123456789abcdef"})
        with patch.object(server, "api_generate", return_value={"build_id": "b_000000000001"}):
            job = ask(self.app, self.pid, dict(task="generate"))["job"]
            wait_for(self.app, self.pid, job)
        with patch.object(server, "LONG_KEEP", -1):
            active(self.app, self.pid)  # asking sweeps
        self.assertEqual(status(self.app, self.pid, job)["state"], "none")
        self.assertEqual(self.app.long_jobs, {})
        self.assertEqual(lut_ranges.JOBS.status(server._long_key(job)), None)

    def test_the_project_has_to_exist(self):
        with self.assertRaises(Exception):
            ask(self.app, "p_ffffffffffff", dict(task="generate"))
        self.assertEqual(self.app.long_jobs, {})
        self.assertTrue(self.app.generation_lock.acquire(blocking=False))
        self.app.generation_lock.release()


class OverHttp(Setup):
    def test_routes_and_json_over_a_real_server(self):
        paths = [(m, p.pattern) for m, p, _ in server.ROUTES]
        for method, url in (("POST", "/api/projects/p_000000000000/jobs"), ("GET", "/api/projects/p_000000000000/jobs"),
                            ("GET", "/api/projects/p_000000000000/jobs/0123456789abcdef")):
            self.assertTrue(any(m == method and p.fullmatch(url) for m, p, _ in server.ROUTES), url)
        httpd = server.LocalServer(("127.0.0.1", 0), self.app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def call(method, url, data=None):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            try:
                conn.request(method, url, body=None if data is None else json.dumps(data).encode())
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()
        base = "/api/projects/%s/jobs" % self.pid
        refused = server.Reply({"error": "还不能生成", "checks": FAIL_CHECKS}, 422)
        with patch.object(server, "api_generate", return_value=refused):
            code, out = call("POST", base, {"task": "generate"})
            self.assertEqual(code, 200)
            for _ in range(200):
                code, out = call("GET", base + "/" + out["job"])
                if out["state"] not in ("queued", "running"):
                    break
                time.sleep(0.01)
        self.assertEqual((code, out["state"], out["status"], out["body"]["checks"]), (200, "failed", 422, FAIL_CHECKS))
        self.assertEqual(call("POST", base, {"task": "nope"})[0], 400)
        self.assertEqual(call("GET", base), (200, {"jobs": []}))


if __name__ == "__main__":
    unittest.main()
