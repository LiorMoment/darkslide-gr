from firmware_registry_fixture import http_identity
"""Local HTTP shell: construction, request order,
every API route, every error class, reply headers and static files."""
import contextlib
import http.client
import inspect
import io
import json
import os
import re
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from darkslide import VERSION, fwread, generate, pngcheck, server, template as tpl
from darkslide.identify import digest_of as sha256
from darkslide.paths import WEB_ROOT
from darkslide.server import MAX_BODY, ROUTES, ApiError, App, LocalServer, create_server, error_reply, find_route
from darkslide.store import StoreError, read_json, write_json

FAKE_OFFICIAL = b"official-firmware-bytes"
OTHER_INPUT = b"another-imported-input"   # imported, but no cached factory data
JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
NOT_FOUND_JSON = {"error": "not found"}


def fake_template():
    t = json.loads(json.dumps(tpl.load()))
    t["official_inputs"] = [dict(filename="fwdc239b.bin", size=len(FAKE_OFFICIAL), sha256=sha256(FAKE_OFFICIAL))]
    return t


def icon_png():
    """40x40 RGBA that meets the factory icon alpha contract."""
    vx, vy, vw, vh = pngcheck.VISIBLE
    rgba = b"".join(bytes((40, 40, 40, 255 if vx <= x < vx + vw and vy <= y < vy + vh else 0))
                    for y in range(40) for x in range(40))
    return pngcheck.write_png(40, 40, rgba)


def photo_png(width=300, height=200):
    return pngcheck.write_png(width, height, bytes((10, 20, 30, 128)) * (width * height))


class Res:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class Served(unittest.TestCase):
    """One LocalServer per test class, on a background thread, over a fresh data folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = App(cls.tmp.name)
        cls.app.template = fake_template()
        cls.store = cls.app.store
        cls.sha = cls.store.put_input(FAKE_OFFICIAL)
        if getattr(cls, "synthetic_identity", True):
            cls.identity = http_identity(cls.sha,len(FAKE_OFFICIAL),lambda:cls.app.template)
            cls.identity.__enter__();cls.addClassCleanup(cls.identity.__exit__,None,None,None)
        cls.httpd = LocalServer(("127.0.0.1", 0), cls.app)
        cls.port = cls.httpd.server_address[1]
        cls.loop = threading.Thread(target=cls.httpd.serve_forever, kwargs=dict(poll_interval=0.05), daemon=True)
        cls.loop.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        """Send exactly this request line; Host defaults to 127.0.0.1:<port>, a None value omits a header."""
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        merged = {"Host": "127.0.0.1:%d" % self.port}
        if data is not None:
            merged["Content-Length"] = str(len(data))
        merged.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in merged.items():
                for one in (value if isinstance(value, list) else [value]):
                    if one is not None:
                        conn.putheader(name, one)
            conn.endheaders(data)
            resp = conn.getresponse()
            return Res(resp.status, resp.headers, resp.read())
        finally:
            conn.close()

    def raw(self, data):
        """Send these bytes as-is; everything the server sends back before it closes."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)

    def assertJson(self, res, status, value=None):
        self.assertEqual(res.status, status, res.body)
        self.assertEqual(res.headers["Content-Type"], JSON_TYPE)
        if value is not None:
            self.assertEqual(res.json(), value)
        return res.json()

    def assertError(self, res, status, message):
        self.assertJson(res, status, {"error": message})

    def new_project(self, title="t"):
        return self.store.create_project(title, self.sha, self.app.template)["id"]


# --------------------------------------------------------------- section 一

class Construction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(self.tmp.name)

    def test_local_server_keeps_app_and_daemon_threads(self):
        httpd = LocalServer(("127.0.0.1", 0), self.app)
        self.addCleanup(httpd.server_close)
        self.assertIs(httpd.app, self.app)
        self.assertTrue(httpd.daemon_threads)
        self.assertEqual(httpd.server_address[0], "127.0.0.1")
        self.assertIs(httpd.RequestHandlerClass, server.RequestHandler)

    def test_local_server_refuses_any_other_host(self):
        for host in ("0.0.0.0", "localhost", "", "::1", "192.168.1.2", "127.0.0.2"):
            with self.assertRaises(ValueError, msg=host):
                LocalServer((host, 0), self.app)

    def test_handler_is_a_module_level_class(self):
        handler = server.RequestHandler
        self.assertTrue(issubclass(handler, BaseHTTPRequestHandler))
        self.assertEqual(handler.__qualname__, "RequestHandler")   # not defined inside a factory
        self.assertIs(getattr(server, handler.__name__), handler)

    def test_create_server_binds_loopback_and_does_not_serve(self):
        httpd = create_server(0, root=self.tmp.name)
        self.addCleanup(httpd.server_close)
        self.assertIsInstance(httpd, LocalServer)
        self.assertEqual(httpd.server_address[0], "127.0.0.1")
        self.assertEqual(httpd.app.store.root, Path(self.tmp.name))
        port = httpd.server_address[1]
        self.assertNotEqual(port, 0)

    def test_create_server_default_root_is_the_data_folder(self):
        with mock.patch.dict(os.environ, {"DARKSLIDE_DATA": self.tmp.name}):
            httpd = create_server(0)
        self.addCleanup(httpd.server_close)
        self.assertEqual(httpd.app.store.root, Path(self.tmp.name))

    def test_create_server_has_no_default_port(self):
        self.assertIs(inspect.signature(create_server).parameters["port"].default, inspect.Parameter.empty)

    def test_routes_are_an_ordered_table(self):
        self.assertIsInstance(ROUTES, (list, tuple))
        for method, pattern, func in ROUTES:
            self.assertIn(method, ("GET", "POST", "PUT", "PATCH"))
            self.assertIsInstance(pattern, re.Pattern)
            self.assertTrue(callable(func))


class Launcher(unittest.TestCase):
    def test_free_port_still_starts_at_8787(self):
        import launcher
        self.assertEqual(inspect.signature(launcher.free_port).parameters["start"].default, 8787)

    def test_main_builds_the_server_with_create_server(self):
        import launcher
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.server_address = ("127.0.0.1", 8799)
        with mock.patch.object(launcher, "create_server", return_value=fake) as made, \
                mock.patch.object(launcher.webbrowser, "open") as browse, \
                mock.patch.dict(os.environ, {"PORT": "8799"}), \
                mock.patch.object(launcher.sys, "argv", ["launcher.py", "--no-browser"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            launcher.main()
        made.assert_called_once_with(8799)
        fake.serve_forever.assert_called_once_with()
        fake.__exit__.assert_called_once()
        browse.assert_not_called()
        self.assertIn("http://127.0.0.1:8799/", out.getvalue())

    def test_runs_until_quit_and_opens_the_browser_on_a_live_server(self):
        import launcher
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen, ready = [], threading.Event()

        def request(address, method, path):
            port = int(address.rstrip("/").rsplit(":", 1)[1])
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request(method, path, body=b"{}" if method == "POST" else None)
                resp = conn.getresponse()
                resp.read()
                return resp.status
            finally:
                conn.close()

        def browse(address):  # the page must already be served when the browser is pointed at it
            seen.append((address, request(address, "GET", "/api/status")))
            ready.set()

        with mock.patch.dict(os.environ, {"PORT": "0", "DARKSLIDE_DATA": tmp.name}), \
                mock.patch.object(launcher.webbrowser, "open", side_effect=browse), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            runner = threading.Thread(target=launcher.main, args=([],), daemon=True)
            runner.start()
            self.assertTrue(ready.wait(10))
            address, status = seen[0]
            self.assertEqual(status, 200)
            self.assertEqual(request(address, "POST", "/api/quit"), 200)
            runner.join(10)
        self.assertFalse(runner.is_alive())
        self.assertIn(address, out.getvalue())
        self.assertIn("已停止", out.getvalue())
        with self.assertRaises(OSError):  # the listening socket is closed
            request(address, "GET", "/api/status")

    def test_ctrl_c_stops_and_closes_the_server(self):
        import launcher
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made, real = [], launcher.create_server

        def spy(port):
            made.append(real(port))
            return made[-1]

        with mock.patch.dict(os.environ, {"PORT": "0", "DARKSLIDE_DATA": tmp.name}), \
                mock.patch.object(launcher, "create_server", side_effect=spy), \
                mock.patch.object(launcher, "wait_for", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            launcher.main(["--no-browser"])
        self.assertEqual(made[0].socket.fileno(), -1)
        self.assertIn("已停止", out.getvalue())


class RouteTable(unittest.TestCase):
    PID, BID, DIGEST = "p_0123456789ab", "b_0123456789ab", "0123456789abcdef" * 4
    EXPECTED = [
        ("GET", "/api/status", "api_status", {}),
        ("POST", "/api/firmware", "api_import_firmware", {}),
        ("POST", "/api/quit", "api_quit", {}),
        ("POST", "/api/check-name", "api_check_name", {}),
        ("POST", "/api/prefs", "api_set_pref", {}),
        ("POST", "/api/projects", "api_create_project", {}),
        ("GET", "/api/factory/%s/shutdown.jpg" % DIGEST, "api_factory_shutdown", dict(digest=DIGEST)),
        ("GET", "/api/factory/%s" % DIGEST, "api_factory", dict(digest=DIGEST)),
        ("GET", "/api/factory/%s/icons/7.png" % DIGEST, "api_factory_icon", dict(digest=DIGEST, n="7")),
        ("GET", "/api/factory/%s/icons/14.png" % DIGEST, "api_factory_icon", dict(digest=DIGEST, n="14")),
        ("GET", "/api/builds/%s/firmware" % BID, "api_build_firmware", dict(bid=BID)),
        ("GET", "/api/builds/%s/manifest" % BID, "api_build_manifest", dict(bid=BID)),
        ("GET", "/api/projects/%s" % PID, "api_get_project", dict(pid=PID)),
        ("PATCH", "/api/projects/%s" % PID, "api_rename_project", dict(pid=PID)),
        ("PUT", "/api/projects/%s/target-body" % PID, "api_set_target_body", dict(pid=PID)),
        ("PUT", "/api/projects/%s/draft" % PID, "api_put_draft", dict(pid=PID)),
        ("POST", "/api/projects/%s/save" % PID, "api_save_project", dict(pid=PID)),
        ("POST", "/api/projects/%s/assets" % PID, "api_upload_asset", dict(pid=PID)),
        ("GET", "/api/projects/%s/assets/%s" % (PID, DIGEST), "api_get_asset", dict(pid=PID, digest=DIGEST)),
        ("POST", "/api/projects/%s/generate" % PID, "api_generate", dict(pid=PID)),
        ("GET", "/api/projects/%s/film-check" % PID, "api_film_check", dict(pid=PID)),
        ("POST", "/api/projects/%s/film-prepare" % PID, "api_film_prepare", dict(pid=PID)),
    ]

    def test_every_spec_row_resolves(self):
        for method, path, name, groups in self.EXPECTED:
            func, found = find_route(method, path)
            self.assertEqual((func.__name__, found), (name, groups), path)

    def test_whole_path_must_match(self):
        for method, path, _, _ in self.EXPECTED:
            for near in (path + "/", path + "x", "/x" + path, path.upper(), path + "\n"):
                if near == path:
                    continue
                with self.assertRaises(ApiError, msg=near) as cm:
                    find_route(method, near)
                self.assertEqual((cm.exception.status, str(cm.exception)), (404, "not found"))

    def test_wrong_method_is_not_found(self):
        for method, path, _, _ in self.EXPECTED:
            for other in ("GET", "POST", "PUT", "PATCH"):
                if any(m == other and p == path for m, p, _, _ in self.EXPECTED):
                    continue
                with self.assertRaises(ApiError, msg=(other, path)):
                    find_route(other, path)

    def test_id_shapes(self):
        bad = ["/api/projects/p_0123456789a", "/api/projects/p_0123456789abc", "/api/projects/p_0123456789AB",
               "/api/projects/q_0123456789ab", "/api/builds/b_0123456789a/firmware",
               "/api/factory/%s" % ("0" * 63), "/api/factory/%s" % ("A" * 64),
               "/api/factory/%s/icons/123.png" % self.DIGEST, "/api/factory/%s/icons/.png" % self.DIGEST,
               "/api/factory/%s/icons/a.png" % self.DIGEST]
        for path in bad:
            with self.assertRaises(ApiError, msg=path):
                find_route("GET", path)


class ErrorMapping(unittest.TestCase):
    def check(self, exc, status, message):
        reply = error_reply(exc)
        self.assertEqual((reply.status, reply.body), (status, {"error": message}))

    def test_table(self):
        self.check(ApiError(409, "冲突"), 409, "冲突")
        self.check(ApiError(413, "文件太大"), 413, "文件太大")
        self.check(StoreError("找不到这个工程"), 400, "找不到这个工程")
        self.check(fwread.FirmwareReadError("frame header beyond end of file"), 422,
                   "读取固件失败：frame header beyond end of file")
        self.check(KeyError(99), 400, "请求参数错误")
        self.check(ValueError("x"), 400, "请求参数错误")
        self.check(TypeError("x"), 400, "请求参数错误")
        # other ValueErrors outside the generate route fall into the generic row
        self.check(generate.GenerateError("x"), 400, "请求参数错误")
        self.check(tpl.TemplateError("x"), 400, "请求参数错误")

    def test_unlisted_exceptions_are_not_mapped(self):
        for exc in (RuntimeError("x"), OSError("x"), ZeroDivisionError()):
            self.assertIsNone(error_reply(exc))


# --------------------------------------------------------------- section 二

class Guards(Served):
    def test_host_must_be_loopback_with_the_listening_port(self):
        p = self.port
        for host in ("evil.example", "127.0.0.1", "localhost", "127.0.0.1:%d" % (p + 1 if p < 65535 else p - 1),
                     "LOCALHOST:%d" % p, "[::1]:%d" % p, "127.0.0.1:%d.evil.example" % p, "0.0.0.0:%d" % p,
                     "evil.example:%d" % p, "127.0.0.1:0%d" % p):
            self.assertError(self.call("GET", "/api/status", headers={"Host": host}), 403, "forbidden host")
        for host in ("127.0.0.1:%d" % p, "localhost:%d" % p):
            self.assertEqual(self.call("GET", "/api/status", headers={"Host": host}).status, 200, host)

    def test_missing_or_repeated_host_is_forbidden(self):
        self.assertError(self.call("GET", "/api/status", headers={"Host": None}), 403, "forbidden host")
        ok = "127.0.0.1:%d" % self.port
        self.assertError(self.call("GET", "/api/status", headers={"Host": [ok, ok]}), 403, "forbidden host")

    def test_host_is_checked_for_static_files_too(self):
        self.assertError(self.call("GET", "/", headers={"Host": "evil.example"}), 403, "forbidden host")
        self.assertError(self.call("GET", "/nope.js", headers={"Host": "evil.example"}), 403, "forbidden host")

    def test_origin_must_be_our_own_address(self):
        p = self.port
        body = {"key": "guide_off", "value": True}
        for origin in ("http://evil.example", "null", "http://127.0.0.1:%d/" % p, "https://127.0.0.1:%d" % p,
                       "http://127.0.0.1", "http://localhost", "http://localhost:%d.evil.example" % p,
                       "http://127.0.0.1:%d" % (p + 1 if p < 65535 else p - 1), "HTTP://127.0.0.1:%d" % p, ""):
            self.assertError(self.call("POST", "/api/prefs", body, {"Origin": origin}), 403, "forbidden origin")
        for origin in ("http://127.0.0.1:%d" % p, "http://localhost:%d" % p, None):
            self.assertJson(self.call("POST", "/api/prefs", body, {"Origin": origin}), 200)

    def test_repeated_origin_is_forbidden(self):
        mine = "http://127.0.0.1:%d" % self.port
        res = self.call("POST", "/api/prefs", {"key": "guide_off", "value": True}, {"Origin": [mine, mine]})
        self.assertError(res, 403, "forbidden origin")

    def test_origin_applies_to_every_non_get_method(self):
        pid = self.new_project()
        evil = {"Origin": "http://evil.example"}
        for method, path in (("POST", "/api/quit"), ("PUT", "/api/projects/%s/draft" % pid),
                             ("PATCH", "/api/projects/%s" % pid), ("POST", "/api/projects/p_000000000000/save")):
            self.assertError(self.call(method, path, {}, evil), 403, "forbidden origin")
        self.assertEqual(self.store.get_project(pid)["draft"]["changes"], {})

    def test_origin_is_ignored_for_get(self):
        self.assertJson(self.call("GET", "/api/status", headers={"Origin": "http://evil.example"}), 200)
        self.assertEqual(self.call("GET", "/", headers={"Origin": "http://evil.example"}).status, 200)

    def test_order_host_then_origin_then_method(self):
        evil_both = {"Host": "evil.example", "Origin": "http://evil.example"}
        self.assertError(self.call("POST", "/", b"", evil_both), 403, "forbidden host")
        self.assertError(self.call("POST", "/", b"", {"Origin": "http://evil.example"}), 403, "forbidden origin")
        self.assertError(self.call("POST", "/", b""), 405, "method not allowed")

    def test_non_get_outside_api_is_405(self):
        for method, path in (("POST", "/"), ("PUT", "/index.html"), ("PATCH", "/nope"), ("POST", "/api"),
                             ("POST", "/apix/status")):
            self.assertError(self.call(method, path, b""), 405, "method not allowed")

    def test_other_methods_are_left_to_the_standard_library(self):
        for method in ("DELETE", "HEAD", "OPTIONS"):
            res = self.call(method, "/api/status")
            self.assertEqual(res.status, 501, method)
            self.assertEqual(res.headers["Cache-Control"], "no-store")

    def test_query_string_is_split_off(self):
        self.assertJson(self.call("GET", "/api/status?x=1&y=/api/"), 200)
        self.assertEqual(self.call("GET", "/?v=2").body, (WEB_ROOT / "index.html").read_bytes())

    def test_params_on_the_last_segment_are_not_part_of_the_path(self):
        # as in v0.2 (urlparse): ";x" at the end of the last segment is dropped before routing
        self.assertJson(self.call("GET", "/api/status;x"), 200)
        self.assertEqual(self.call("GET", "/index.html;v=1").body, (WEB_ROOT / "index.html").read_bytes())
        pid = self.new_project()
        value = self.assertJson(self.call("POST", "/api/projects/%s/assets;x?kind=icon" % pid, icon_png()), 200)
        self.assertEqual((value["width"], value["height"]), (40, 40))
        self.assertJson(self.call("PATCH", "/api/projects/%s;x" % pid, {"title": "semi"}), 200)
        self.assertEqual(self.store.get_project(pid)["title"], "semi")
        # only the last segment carries params
        self.assertJson(self.call("GET", "/api/projects;x/%s" % pid), 404, NOT_FOUND_JSON)


# --------------------------------------------------------------- section 三

class Body(Served):
    def test_over_64_mib_is_413_without_reading(self):
        size = str(MAX_BODY + 1)
        pid = self.new_project()
        for path in ("/api/firmware", "/api/check-name", "/api/projects/%s/assets?kind=icon" % pid):
            # the header promises more than the limit and nothing follows: only an early 413 can answer
            res = self.call("POST", path, headers={"Content-Length": size})
            self.assertError(res, 413, "文件太大")

    def test_missing_or_zero_length_is_empty(self):
        self.assertError(self.call("POST", "/api/firmware"), 400, "文件是空的")
        self.assertError(self.call("POST", "/api/firmware", b""), 400, "文件是空的")

    def test_empty_json_body_is_an_empty_object(self):
        self.assertError(self.call("POST", "/api/prefs", b""), 400, "未知设置")
        # {} -> create_project(None, None, ...) -> TypeError in the store: a parameter error, not a format error
        self.assertError(self.call("POST", "/api/projects", b""), 400, "请求参数错误")
        self.assertError(self.call("POST", "/api/check-name", b""), 400, "请求参数错误")

    def test_unparseable_or_non_object_json_is_400(self):
        for raw in (b"{", b"[]", b"[1, 2]", b"1", b'"text"', b"null", b"true", b"\xff\xfe\xfa",
                    b"{'a': 1}", b"[" * 100000):
            self.assertError(self.call("POST", "/api/check-name", raw), 400, "请求格式错误")

    def test_bad_content_length_is_a_parameter_error(self):
        for value in ("abc", "-1"):
            res = self.call("POST", "/api/firmware", headers={"Content-Length": value})
            self.assertError(res, 400, "请求参数错误")

    def test_bad_content_length_on_json_routes_is_a_format_error(self):
        # v0.2 read JSON bodies inside its "malformed request" guard
        pid = self.new_project()
        for method, path in (("POST", "/api/prefs"), ("POST", "/api/check-name"), ("POST", "/api/projects"),
                             ("PATCH", "/api/projects/%s" % pid), ("PUT", "/api/projects/%s/draft" % pid)):
            for value in ("abc", "5.0", "0x10", "-3"):
                res = self.call(method, path, headers={"Content-Length": value})
                self.assertError(res, 400, "请求格式错误")

    def test_over_64_mib_sent_in_full_still_reads_the_413(self):
        # the unread body is drained after the reply, so the sender is not reset mid-upload
        self.assertError(self.call("POST", "/api/firmware", bytes(MAX_BODY + 1)), 413, "文件太大")
        self.assertError(self.call("POST", "/api/quit", b"{}", {"Origin": "http://evil.example"}),
                         403, "forbidden origin")
        self.assertTrue(self.loop.is_alive())


class Timeouts(Served):
    def test_handler_has_an_idle_timeout(self):
        self.assertGreater(server.RequestHandler.timeout, 0)

    def dropped(self, data):
        """Send a request that stops half way; the server closes without a reply or a traceback."""
        with mock.patch.object(server.RequestHandler, "timeout", 0.3), \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(data)
            self.assertEqual(sock.recv(100), b"")
        self.assertEqual(err.getvalue(), "")

    def test_half_sent_headers_are_dropped(self):
        self.dropped(b"GET /api/status HTTP/1.0\r\nHost: 127.0.0.1")

    def test_a_stalled_body_is_dropped(self):
        self.dropped(b"POST /api/prefs HTTP/1.0\r\nHost: 127.0.0.1:%d\r\nContent-Length: 10\r\n\r\n{}" % self.port)


# --------------------------------------------------------------- section 四

class Replies(Served):
    def requests(self):
        pid = self.new_project()
        return [("GET", "/api/status", None, {}), ("GET", "/", None, {}), ("GET", "/app.js", None, {}),
                ("GET", "/missing.html", None, {}), ("GET", "/api/status", None, {"Host": "evil.example"}),
                ("POST", "/", b"", {}), ("GET", "/api/nope", None, {}),
                ("POST", "/api/firmware", None, {"Content-Length": str(MAX_BODY + 1)}),
                ("GET", "/api/projects/p_000000000000", None, {}),
                ("POST", "/api/projects/%s/assets" % pid, photo_png(), {}),
                ("DELETE", "/api/status", None, {})]

    def test_every_reply_has_the_fixed_headers(self):
        for method, path, body, headers in self.requests():
            res = self.call(method, path, body, headers)
            with self.subTest(method=method, path=path, status=res.status):
                self.assertTrue(res.headers["Content-Type"])
                self.assertEqual(int(res.headers["Content-Length"]), len(res.body))
                self.assertEqual(res.headers["Cache-Control"], "no-store")
                self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(res.headers["Referrer-Policy"], "no-referrer")
                self.assertEqual(res.headers["Server"], "Darkslide/" + VERSION)
                self.assertEqual(len(res.headers.get_all("Cache-Control")), 1)

    def test_json_is_utf8_without_ascii_escapes(self):
        res = self.call("GET", "/api/projects/p_000000000000")
        self.assertEqual(res.body, json.dumps({"error": "找不到这个工程"}, ensure_ascii=False).encode("utf-8"))
        self.assertNotIn(b"\\u", res.body)

    def test_a_versionless_request_line_still_gets_status_and_headers(self):
        reply = self.raw(b"GET /api/status\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port)
        head, _, body = reply.partition(b"\r\n\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.0 200 "), head)
        self.assertIn(b"\r\nX-Content-Type-Options: nosniff", head)
        self.assertEqual(json.loads(body.decode("utf-8"))["version"], VERSION)
        for line in (b"GET / HTTP/0.9", b"GARBAGE", b"GET / HTTP/9.9"):
            head = self.raw(line + b"\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port).partition(b"\r\n\r\n")[0]
            self.assertTrue(head.startswith(b"HTTP/1.0 "), (line, head))
            self.assertIn(b"\r\nCache-Control: no-store", head)

    def test_access_log_escapes_control_characters(self):
        self.assertEqual(server.printable("a\rb\x1b[31m\x07\u2028中 文"), "a\\rb\\x1b[31m\\x07\\u2028中 文")
        with self.assertLogs("darkslide.http", "DEBUG") as logs:
            self.raw(b"GET /x\x1b[31mred\x07\r HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port)
        lines = [record.getMessage() for record in logs.records if "red" in record.getMessage()]
        self.assertTrue(lines, logs.output)
        for line in lines:
            self.assertIn("\\x1b[31mred\\x07", line)
            self.assertFalse(any(ch in line for ch in "\x1b\x07\r\n"), line)

    def test_access_log_goes_to_logging_not_the_terminal(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertLogs("darkslide.http", "DEBUG") as logs:
            self.call("GET", "/api/status?probe=log")
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(any("GET /api/status?probe=log" in line for line in logs.output), logs.output)
        self.assertTrue(all(record.levelname == "DEBUG" for record in logs.records))


# --------------------------------------------------------------- section 五

class Routes(Served):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other = cls.store.put_input(OTHER_INPUT)

    def test_status(self):
        value = self.assertJson(self.call("GET", "/api/status"), 200)
        self.assertEqual(value["version"], VERSION)
        self.assertEqual(value["data_root"], str(self.store.root))
        self.assertEqual(value, json.loads(json.dumps(self.app.status(), ensure_ascii=False)))

    def test_import_official_firmware(self):
        with mock.patch.object(self.app, "extract_factory", return_value=None) as extract:
            res = self.call("POST", "/api/firmware", FAKE_OFFICIAL, {"X-Filename": "fwdc239b.bin"})
        value = self.assertJson(res, 200)
        self.assertEqual((value["kind"], value["filename"], value["sha256"]), ("official", "fwdc239b.bin", self.sha))
        extract.assert_called_once_with(self.sha, FAKE_OFFICIAL)

    def test_import_filename_is_url_decoded_and_defaults_to_empty(self):
        res = self.call("POST", "/api/firmware", b"abc", {"X-Filename": "%E5%9B%BA%E4%BB%B6%20v2.bin"})
        self.assertEqual(self.assertJson(res, 200)["filename"], "固件 v2.bin")
        res = self.call("POST", "/api/firmware", b"abc")
        value = self.assertJson(res, 200)
        self.assertEqual((value["filename"], value["kind"]), ("", "rejected"))

    def test_import_firmware_read_error_is_422(self):
        # the real extract_factory cannot unpack the fake bytes
        res = self.call("POST", "/api/firmware", FAKE_OFFICIAL, {"X-Filename": "fwdc239b.bin"})
        self.assertEqual(res.status, 422)
        self.assertTrue(res.json()["error"].startswith("读取固件失败："), res.json())

    def test_check_name(self):
        value = self.assertJson(self.call("POST", "/api/check-name", {"slot": 11, "name": "Day 200"}), 200)
        self.assertEqual((value["ok"], value["limit"], value["factory_en"]), (True, 16, "Cross Processing"))
        value = self.assertJson(self.call("POST", "/api/check-name", {"slot": 11, "name": "x" * 17}), 200)
        self.assertFalse(value["ok"])
        self.assertError(self.call("POST", "/api/check-name", {"slot": 11, "name": 5}), 400, "预设名必须是文字")
        for bad in ({"slot": 99, "name": "x"}, {"slot": "eleven", "name": "x"}, {"name": "x"}, {"slot": [11]}):
            self.assertError(self.call("POST", "/api/check-name", bad), 400, "请求参数错误")

    def test_text_utf8_cannot_carry(self):
        # the name is echoed back, so rendering the reply fails: mapped like any ValueError, as in v0.2
        res = self.call("POST", "/api/check-name", b'{"slot": 11, "name": "\\ud800"}')
        self.assertError(res, 400, "请求参数错误")
        # an error message that quotes such text still goes out, as a JSON escape
        pid = self.new_project()
        res = self.call("PUT", "/api/projects/%s/draft" % pid, b'{"changes": {"\\ud800": {}}}')
        self.assertError(res, 400, "没有这个槽位：\ud800")

    def test_prefs(self):
        for key in ("preflight_seen", "guide_off", "guide_start_seen", "guide_work_seen"):
            value = self.assertJson(self.call("POST", "/api/prefs", {"key": key, "value": 1}), 200)
            self.assertIs(value[key], True)
        value = self.assertJson(self.call("POST", "/api/prefs", {"key": "guide_off", "value": ""}), 200)
        self.assertIs(value["guide_off"], False)
        self.assertIs(self.store.prefs()["guide_off"], False)
        for key in ("theme", "", None, ["guide_off"], {"k": 1}):
            self.assertError(self.call("POST", "/api/prefs", {"key": key, "value": True}), 400, "未知设置")
        self.assertNotIn("theme", self.store.prefs())

    def test_create_project(self):
        value = self.assertJson(self.call("POST", "/api/projects", {"title": " 夜景 ", "input_sha256": self.sha}), 200)
        self.assertRegex(value["id"], r"^p_[0-9a-f]{12}$")
        self.assertEqual((value["title"], value["input_sha256"]), ("夜景", self.sha))
        value = self.assertJson(self.call("POST", "/api/projects", {"input_sha256": self.sha}), 200)
        self.assertEqual(value["title"], "未命名")
        self.assertError(self.call("POST", "/api/projects", {"title": "t", "input_sha256": "0" * 64}),
                         400, "请求参数错误")

    def test_factory(self):
        # a cache whose template_id does not match: factory() serves it, while a real
        # import (test_import_firmware_read_error_is_422) still has to re-extract
        cached = dict(template_id="test-cache", slots={"7": {"names": {"en": "Positive Film"}}})
        write_json(self.store.factory_dir(self.sha) / "factory.json", cached)
        self.assertJson(self.call("GET", "/api/factory/%s" % self.sha), 200, cached)
        self.assertError(self.call("GET", "/api/factory/%s" % ("0" * 64)), 404, "没有导入这个固件")
        res = self.call("GET", "/api/factory/%s" % self.other)
        self.assertEqual(res.status, 400)
        self.assertEqual(res.json()["error"], "请求参数错误")
        self.assertJson(self.call("GET", "/api/factory/%s" % self.sha.upper()), 404, NOT_FOUND_JSON)

    def test_factory_icon(self):
        write_json(self.store.factory_dir(self.sha) / "factory.json", dict(template_id="test-cache"))
        icons = self.store.factory_dir(self.sha) / "icons"
        icons.mkdir(parents=True, exist_ok=True)
        (icons / "3.png").write_bytes(icon_png())
        for n in ("3", "03"):
            res = self.call("GET", "/api/factory/%s/icons/%s.png" % (self.sha, n))
            self.assertEqual((res.status, res.headers["Content-Type"], res.body), (200, "image/png", icon_png()))
        self.assertError(self.call("GET", "/api/factory/%s/icons/4.png" % self.sha), 404, "没有这个图标")
        self.assertError(self.call("GET", "/api/factory/%s/icons/3.png" % ("0" * 64)), 404, "没有导入这个固件")
        self.assertJson(self.call("GET", "/api/factory/%s/icons/123.png" % self.sha), 404, NOT_FOUND_JSON)

    def test_factory_shutdown(self):
        jpeg = b"\xff\xd8 cached factory shutdown \xff\xd9"
        target = self.store.factory_dir(self.sha) / "shutdown.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jpeg)
        res = self.call("GET", "/api/factory/%s/shutdown.jpg" % self.sha)
        self.assertEqual((res.status, res.headers["Content-Type"], res.body), (200, "image/jpeg", jpeg))
        self.assertError(self.call("GET", "/api/factory/%s/shutdown.jpg" % ("0" * 64)), 404, "没有导入这个固件")
        res = self.call("GET", "/api/factory/%s/shutdown.jpg" % self.other)
        self.assertEqual(res.status, 422)
        self.assertTrue(res.json()["error"].startswith("读取固件失败："))

    def put_build(self, output=b"generated-output"):
        manifest = dict(project_id="p_000000000000", input_sha256=self.sha, checks=[dict(ok=True)], features=["name"], output_sha256=sha256(output), created_at="2026-10-11T00:00:00")
        return self.store.put_build(output, manifest)["build_id"], output

    def test_build_firmware_download(self):
        bid, output = self.put_build()
        res = self.call("GET", "/api/builds/%s/firmware" % bid)
        self.assertEqual((res.status, res.headers["Content-Type"], res.body), (200, "application/octet-stream", output))
        self.assertEqual(res.headers["Content-Disposition"], 'attachment; filename="fwdc239b.bin"')
        self.assertError(self.call("GET", "/api/builds/b_000000000000/firmware"), 400, "找不到生成记录")
        self.assertJson(self.call("GET", "/api/builds/b_00000000000/firmware"), 404, NOT_FOUND_JSON)

    def test_build_manifest_download(self):
        bid, _ = self.put_build()
        res = self.call("GET", "/api/builds/%s/manifest" % bid)
        self.assertEqual((res.status, res.headers["Content-Type"]), (200, JSON_TYPE))
        self.assertEqual(res.headers["Content-Disposition"], 'attachment; filename="darkslide-manifest-%s.json"' % bid)
        self.assertEqual(res.body, (self.store.root / "builds" / bid / "manifest.json").read_bytes())
        self.assertError(self.call("GET", "/api/builds/b_000000000000/manifest"), 400, "找不到生成记录")
        self.assertIsNone(self.call("GET", "/api/projects/p_000000000000").headers["Content-Disposition"])

    def test_get_and_rename_project(self):
        pid = self.new_project("旧名")
        self.assertEqual(self.assertJson(self.call("GET", "/api/projects/%s" % pid), 200)["title"], "旧名")
        value = self.assertJson(self.call("PATCH", "/api/projects/%s" % pid, {"title": "新名"}), 200)
        self.assertEqual(value["title"], "新名")
        self.assertEqual(self.store.get_project(pid)["title"], "新名")
        self.assertError(self.call("GET", "/api/projects/p_000000000000"), 400, "找不到这个工程")
        self.assertError(self.call("PATCH", "/api/projects/p_000000000000", {"title": "x"}), 400, "找不到这个工程")
        self.assertJson(self.call("GET", "/api/projects/../../etc"), 404, NOT_FOUND_JSON)

    def test_draft_and_save(self):
        pid = self.new_project()
        value = self.assertJson(self.call("PUT", "/api/projects/%s/draft" % pid,
                                          {"changes": {"7": {"name": "Posi Warm"}}}), 200)
        self.assertTrue(value["unsaved"])
        value = self.assertJson(self.call("POST", "/api/projects/%s/save" % pid), 200)
        self.assertFalse(value["unsaved"])
        self.assertEqual((value["changes"], len(value["versions"])), ({"7": {"name": "Posi Warm"}}, 1))
        self.assertError(self.call("PUT", "/api/projects/%s/draft" % pid, {"changes": []}), 400, "改动格式错误")
        self.call("PUT", "/api/projects/%s/draft" % pid, {"changes": {"7": {"name": "Positive Film Warmer"}}})
        self.assertError(self.call("POST", "/api/projects/%s/save" % pid), 400, "正片 的预设名没有通过检查")

    def test_upload_assets_by_exact_query(self):
        pid = self.new_project()
        base = "/api/projects/%s/assets" % pid
        value = self.assertJson(self.call("POST", base + "?kind=icon", icon_png()), 200)
        self.assertEqual((value["asset"], value["width"], value["height"]), (sha256(icon_png()), 40, 40))
        self.assertError(self.call("POST", base + "?kind=icon", photo_png()), 400, "图标必须是 40×40")
        for query in ("", "?kind=photo", "?kind=icon&x=1", "?kind=shutdown&x=1", "?KIND=icon"):
            value = self.assertJson(self.call("POST", base + query, photo_png()), 200)
            self.assertEqual((value["width"], value["height"]), (300, 200), query)
        res = self.call("POST", base + "?kind=shutdown", photo_png())
        self.assertEqual(res.status, 400)
        self.assertTrue(res.json()["error"].startswith("关机图格式不符合相机要求"), res.json())
        self.assertError(self.call("POST", base, b"not a png"), 400, "不是 PNG 文件")

    def test_get_asset_type_follows_suffix(self):
        pid = self.new_project()
        asset = self.store.put_asset(pid, icon_png(), expect_icon=True)["asset"]
        res = self.call("GET", "/api/projects/%s/assets/%s" % (pid, asset))
        self.assertEqual((res.status, res.headers["Content-Type"], res.body), (200, "image/png", icon_png()))
        jpeg = b"\xff\xd8 placeholder jpeg \xff\xd9"
        (self.store.root / "projects" / pid / "assets" / (sha256(jpeg) + ".jpg")).write_bytes(jpeg)
        res = self.call("GET", "/api/projects/%s/assets/%s" % (pid, sha256(jpeg)))
        self.assertEqual((res.status, res.headers["Content-Type"], res.body), (200, "image/jpeg", jpeg))
        self.assertError(self.call("GET", "/api/projects/%s/assets/%s" % (pid, "0" * 64)), 400, "找不到图片")
        self.assertJson(self.call("GET", "/api/projects/%s/assets/%s" % (pid, asset.upper())), 404, NOT_FOUND_JSON)

    def test_generate_refuses_unreviewed_template(self):
        pid = self.new_project()
        with mock.patch.dict(self.app.template, {"reviewed": False}):
            self.assertError(self.call("POST", "/api/projects/%s/generate" % pid), 409, "模板不可用，不能生成")

    def test_generate_needs_the_official_input(self):
        pid = self.new_project()
        path = self.store.root / "projects" / pid / "project.json"
        write_json(path, dict(read_json(path), input_sha256="f" * 64))
        self.assertError(self.call("POST", "/api/projects/%s/generate" % pid), 409, "需要已支持机型的官方原件")

    def test_generate_error_is_422_with_checks(self):
        pid = self.new_project()
        self.assertJson(self.call("POST", "/api/projects/%s/generate" % pid), 422, {"error": "还没有改动", "checks": []})
        checks = [dict(id="file_sum", ok=False, title="整文件 32 位加和为 0")]
        with mock.patch.object(generate, "run", side_effect=generate.GenerateError("核对没过", checks)):
            self.assertJson(self.call("POST", "/api/projects/%s/generate" % pid), 422,
                            {"error": "核对没过", "checks": checks})
        self.assertError(self.call("POST", "/api/projects/p_000000000000/generate"), 400, "找不到这个工程")

    def test_generate_success_stores_the_build(self):
        pid = self.new_project()
        output = b"pretend-generated-firmware"
        manifest = dict(project_id=pid, input_sha256=self.sha, checks=[dict(ok=True)], features=["name"], output_sha256=sha256(output), created_at="2026-10-11T00:00:00")
        with mock.patch.object(generate, "run", return_value=(output, manifest)) as run:
            value = self.assertJson(self.call("POST", "/api/projects/%s/generate" % pid), 200)
        store, project, template, official = run.call_args.args
        self.assertIs(store, self.store)
        self.assertEqual((project["id"], template, official), (pid, self.app.template, FAKE_OFFICIAL))
        self.assertRegex(value["build_id"], r"^b_[0-9a-f]{12}$")
        self.assertIn(value["build_id"], [b["build_id"] for b in self.store.builds()])
        res = self.call("GET", "/api/builds/%s/firmware" % value["build_id"])
        self.assertEqual(res.body, output)

    def test_other_api_paths_are_404(self):
        pid = self.new_project()
        for method, path in (("GET", "/api/"), ("GET", "/api/nope"), ("GET", "/api/status/"), ("GET", "/api/statusx"),
                             ("GET", "/api/STATUS"), ("POST", "/api/status"), ("GET", "/api/quit"),
                             ("PUT", "/api/projects/%s" % pid), ("GET", "/api/projects/%s/save" % pid),
                             ("GET", "/api/projects/%s/draft" % pid), ("PATCH", "/api/prefs"),
                             ("GET", "/api/projects/%s/./" % pid), ("GET", "/api//status"),
                             ("GET", "/api/%73tatus")):
            self.assertJson(self.call(method, path, b"" if method != "GET" else None), 404, NOT_FOUND_JSON)


class DeleteProject(Served):
    """User, 2026-10-17: the × on the home list removes a project and the builds it made, nothing else."""

    def test_delete_removes_project_and_its_builds_only(self):
        from darkslide.store import write_json
        mine, other = self.new_project("mine"), self.new_project("other")
        records = []
        for build_id, pid in (("b_aaaaaaaaaaaa", mine), ("b_bbbbbbbbbbbb", mine), ("b_cccccccccccc", other)):
            folder = self.store.root / "builds" / build_id
            folder.mkdir(parents=True)
            (folder / "fwdc239b.bin").write_bytes(b"x")
            records.append(dict(build_id=build_id, project_id=pid, output_sha256="0" * 64, created_at="t"))
        write_json(self.store.root / "builds.json", records)
        origin = {"Origin": "http://127.0.0.1:%d" % self.port}
        out = self.assertJson(self.call("POST", "/api/projects/%s/delete" % mine, b"", origin), 200)
        self.assertEqual(out["deleted"], mine)
        self.assertEqual(out["builds_removed"], 2)
        self.assertEqual([b["build_id"] for b in out["builds"]], ["b_cccccccccccc"])
        self.assertNotIn(mine, [p["id"] for p in out["projects"]])
        self.assertIn(other, [p["id"] for p in out["projects"]])
        self.assertFalse((self.store.root / "projects" / mine).exists())
        self.assertFalse((self.store.root / "builds" / "b_aaaaaaaaaaaa").exists())
        self.assertTrue((self.store.root / "builds" / "b_cccccccccccc" / "fwdc239b.bin").exists())
        self.assertEqual(self.call("POST", "/api/projects/%s/delete" % mine, b"", origin).status, 400)
        self.assertIn(self.call("POST", "/api/projects/..%2Fbuilds/delete", b"", origin).status, (400, 404))
        self.assertTrue((self.store.root / "builds" / "b_cccccccccccc" / "fwdc239b.bin").exists())
        self.assertTrue((self.store.root / "projects" / other / "project.json").exists())


class Quit(Served):
    def test_quit_replies_then_stops_from_another_thread(self):
        self.assertJson(self.call("GET", "/api/quit"), 404, NOT_FOUND_JSON)
        self.assertTrue(self.loop.is_alive())
        res = self.call("POST", "/api/quit")
        self.assertJson(res, 200, {"ok": True})
        self.assertEqual(res.headers["Referrer-Policy"], "no-referrer")
        self.loop.join(timeout=5)
        self.assertFalse(self.loop.is_alive())


# --------------------------------------------------------------- section 六

class Static(Served):
    def test_index(self):
        index = (WEB_ROOT / "index.html").read_bytes()
        for path in ("/", "/index.html", "/%69ndex.html"):
            res = self.call("GET", path)
            self.assertEqual((res.status, res.headers["Content-Type"], res.body),
                             (200, "text/html; charset=utf-8", index), path)
        self.assertEqual(server.static_target(WEB_ROOT, ""), (WEB_ROOT / "index.html").resolve())

    def test_scripts_and_styles_carry_utf8(self):
        res = self.call("GET", "/app.js")
        self.assertIn(res.headers["Content-Type"], ("text/javascript; charset=utf-8",
                                                    "application/javascript; charset=utf-8"))
        self.assertEqual(res.body, (WEB_ROOT / "app.js").read_bytes())
        self.assertEqual(self.call("GET", "/app.css").headers["Content-Type"], "text/css; charset=utf-8")

    def test_traversal_and_missing_files_are_plain_404(self):
        for path in ("/../darkslide/server.py", "/%2e%2e/darkslide/server.py", "/%2E%2E%2Fdarkslide%2Fserver.py",
                     "/..%2f..%2fREADME.md", "/%2Fetc%2Fpasswd", "/index.html%00", "/./../launcher.py",
                     "/" + "%2e%2e%2f" * 12 + "etc/passwd", "/missing.html", "/.", "/%2e", "/%2e%2e",
                     "/..\\launcher.py", "/..%5Claunch.py", "/api"):
            res = self.call("GET", path)
            self.assertEqual((res.status, res.headers["Content-Type"], res.body), (404, TEXT_TYPE, b"not found"), path)


class StaticFiles(unittest.TestCase):
    """static_target / static_type against a scratch web root."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "web"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "index.html").write_text("<p>DARKSLIDE</p>", encoding="utf-8")
        (self.root / "sub" / "notes.txt").write_text("hi", encoding="utf-8")
        (self.root / "blob").write_bytes(b"\x00\x01")
        (self.root / "data.json").write_text("{}", encoding="utf-8")
        (self.base / "secret.txt").write_text("outside", encoding="utf-8")

    def test_inside_files_only(self):
        self.assertEqual(server.static_target(self.root, "/sub/notes.txt"), (self.root / "sub" / "notes.txt").resolve())
        self.assertEqual(server.static_target(self.root, "/sub%2Fnotes.txt"), (self.root / "sub" / "notes.txt").resolve())
        for path in ("/sub", "/sub/", "/../secret.txt", "/%2e%2e/secret.txt", "/sub/../../secret.txt",
                     "/" + str(self.base / "secret.txt"), "/%00", "/nope"):
            self.assertIsNone(server.static_target(self.root, path), path)

    def test_over_long_names_are_just_missing(self):
        for path in ("/" + "a" * 300, "/sub/" + "b" * 1100, "/a" * 600 + "/x"):
            self.assertIsNone(server.static_target(self.root, path), len(path))

    def test_names_are_checked_as_text_before_the_filesystem(self):
        import ntpath
        import posixpath
        win = "C:\\app\\web"
        for rel in ("//attacker.example/share/x.png", "\\\\attacker.example\\share\\x.png", "..\\launcher.py",
                    "C:/Windows/win.ini", "D:x", "/x", "sub/../../x", ".", "", "a\0b"):
            self.assertFalse(server.stays_inside(win, rel, ntpath), rel)
        for rel in ("index.html", "sub/notes.txt", "sub\\notes.txt", "sub/../index.html", "sub//notes.txt"):
            self.assertTrue(server.stays_inside(win, rel, ntpath), rel)
        posix = "/srv/web"
        for rel in ("//attacker.example/share", "/etc/passwd", "../x", "sub/../../x", ".", "", "a\0b"):
            self.assertFalse(server.stays_inside(posix, rel, posixpath), rel)
        for rel in ("index.html", "..\\x", "sub/./notes.txt", "data:foo.js"):
            self.assertTrue(server.stays_inside(posix, rel, posixpath), rel)
        # a refused name never reaches resolve(): only the root itself is resolved
        resolved, real = [], Path.resolve

        def spy(path, *args, **kwargs):
            resolved.append(path)
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", spy):
            for path in ("/%2F%2Fattacker.example%2Fshare%2Fx.png", "/%2e%2e/secret.txt", "/%00"):
                self.assertIsNone(server.static_target(self.root, path), path)
        self.assertEqual(resolved, [self.root] * 3)

    def test_symlinks_must_stay_inside(self):
        try:
            (self.root / "out.txt").symlink_to(self.base / "secret.txt")
            (self.root / "in.txt").symlink_to(self.root / "sub" / "notes.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertIsNone(server.static_target(self.root, "/out.txt"))
        self.assertEqual(server.static_target(self.root, "/in.txt"), (self.root / "sub" / "notes.txt").resolve())

    def test_types(self):
        self.assertEqual(server.static_type(Path("a.txt")), "text/plain; charset=utf-8")
        self.assertEqual(server.static_type(Path("a.html")), "text/html; charset=utf-8")
        self.assertEqual(server.static_type(Path("a.png")), "image/png")
        self.assertEqual(server.static_type(Path("a.json")), "application/json")
        self.assertEqual(server.static_type(Path("blob")), "application/octet-stream")
        self.assertEqual(server.static_type(Path("a.darkslide-unknown")), "application/octet-stream")
        with mock.patch("mimetypes.guess_type", return_value=("application/javascript", None)):
            self.assertEqual(server.static_type(Path("a.js")), "application/javascript; charset=utf-8")

    def test_served_from_the_module_web_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(tmp.name)
        httpd = LocalServer(("127.0.0.1", 0), app)
        loop = threading.Thread(target=httpd.serve_forever, kwargs=dict(poll_interval=0.05), daemon=True)
        loop.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"Host": "localhost:%d" % port})
                resp = conn.getresponse()
                return resp.status, resp.headers["Content-Type"], resp.read()
            finally:
                conn.close()

        colon_names = os.name != "nt"   # ":" cannot be in a Windows file name
        if colon_names:
            (self.root / "data:foo.js").write_text("x", encoding="utf-8")
            (self.root / "semi;colon.txt").write_text("x", encoding="utf-8")

        with mock.patch.object(server, "WEB_ROOT", self.root):
            self.assertEqual(get("/blob"), (200, "application/octet-stream", b"\x00\x01"))
            self.assertEqual(get("/sub/notes.txt"), (200, "text/plain; charset=utf-8", b"hi"))
            self.assertEqual(get("/sub/"), (404, TEXT_TYPE, b"not found"))
            self.assertEqual(get("/../secret.txt"), (404, TEXT_TYPE, b"not found"))
            self.assertEqual(get("/" + "a" * 300), (404, TEXT_TYPE, b"not found"))
            if colon_names:
                # typed by its suffix, not read as a "data:" URL
                for path in ("/data:foo.js", "/data%3Afoo.js"):
                    status, kind, _ = get(path)
                    self.assertEqual(status, 200, path)
                    self.assertIn(kind, ("text/javascript; charset=utf-8", "application/javascript; charset=utf-8"))
                # ";colon.txt" is params on the last segment, as in v0.2
                self.assertEqual(get("/semi;colon.txt"), (404, TEXT_TYPE, b"not found"))


if __name__ == "__main__":
    unittest.main()
