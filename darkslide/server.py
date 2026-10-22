"""Local HTTP server for Darkslide: static pages plus a small JSON API, bound to 127.0.0.1 only.

The HTTP layer below `App` is written from our own spec (2026-10-11).
"""
import copy
import hashlib
import json
import logging
import mimetypes
import os
import re
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import models, VERSION, base_curve, factory_envelope, film_fit, fitting_setup, film_luts, film_profiles, film_requests, fwread, fwzip, generate, names, photo_lut, pngcheck, shutdown_verify, template as tpl, xmp_lut
from .identify import identify
from .paths import WEB_ROOT, data_root
from .store import SHUTDOWN_BUDGET, Store, StoreError, read_json, write_json

MAX_BODY = 64 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, root=None, template_name="gr3_v210.json"):
        self.store = Store(root or data_root())
        fitting_setup.activate(self.store.root)
        try:
            self.store.ensure_film_icons_dir()
        except OSError:
            pass  # the film list falls back to its drawn name plates
        # One generation at a time, whether it comes in as a plain request or as a job.
        self.generation_lock = self.iiix_generation_lock = threading.Lock()
        self.long_jobs, self.long_jobs_lock = {}, threading.Lock()
        self.template = tpl.load(template_name)
        self.detail = tpl.load_detail(self.template)

    def status(self):
        from . import film_requests
        return dict(version=VERSION, template=self.template, detail=self.detail, projects=self.store.list_projects(),
                    builds=self.store.builds(), prefs=self.store.prefs(),
                    data_root=str(self.store.root),
                    shutdown=dict(budget=SHUTDOWN_BUDGET, width=720, height=480),
                    film_writer=film_requests.status(),
                    families=[models.capabilities(m) for m in models.ALL])

    def import_firmware(self, data, filename):
        if not data:
            raise ApiError(400, "文件是空的")
        digest = hashlib.sha256(data).hexdigest()
        try:
            model = models.from_sha(digest)
        except ValueError:
            result = identify(data, filename, self.template, self.store.builds())
            if result["kind"] == "official":
                raise ApiError(422, "原件身份不在内置机型表中")
            return result
        if len(data) != model.input_size:
            raise ApiError(422, "官方原件尺寸不匹配")
        template = models.template_for(model)
        result = identify(data, filename, template, self.store.builds())
        self.extract_factory(digest, data)
        self.store.put_input(data)
        return dict(result, template=template, detail=tpl.load_detail(template),
                    capabilities=models.capabilities(model))

    def extract_factory(self, digest, data):
        """Read factory assets with an immutable, content-bound model."""
        model = models.from_sha(digest)
        if len(data) != model.input_size or hashlib.sha256(data).hexdigest() != model.input_sha:
            raise ApiError(422, "官方原件身份不一致")
        template = models.template_for(model)
        folder = self.store.factory_dir(digest)
        index = folder / "factory.json"
        if read_json(index) and read_json(index).get("template_id") == template["id"]:
            return read_json(index)
        payload = fwread.Payload(fwread.unpack(data), layout=models.layout(model))
        slots = {}
        for slot in template["slots"]:
            desc, pixels = payload.icon_pixels(slot["icon_id"])
            png = pngcheck.write_png(desc["width"], desc["height"], fwread.icon_to_rgba(pixels))
            (folder / "icons").mkdir(parents=True, exist_ok=True)
            (folder / "icons" / ("%d.png" % slot["no"])).write_bytes(png)
            names_by_lang = {lang: payload.string(slot["string_id"], lang)["text"] for lang in ("en", "zh-CN", "ja")}
            slots[str(slot["no"])] = dict(names=names_by_lang,
                name_matches_template=names_by_lang["en"] == slot["factory_en"])
        value = dict(template_id=template["id"], payload_sha256=model.payload_sha, slots=slots)
        write_json(index, value)
        return value

    def factory(self, digest):
        folder = self.store.factory_dir(digest)
        value = read_json(folder / "factory.json")
        if value is None:
            if not self.store.has_input(digest):
                raise ApiError(404, "没有导入这个固件")
            value = self.extract_factory(digest, self.store.input_path(digest).read_bytes())
        return value

    def factory_shutdown(self, digest, request=2):
        """A factory shutdown image from the imported official input, read-only, cached. request is the
        shutdown route (2 normal GoodBye, 3 Street Collabo, 10 ING, 11 Urban, 12 Diary): each edition
        shows its own picture, found through the firmware's own request table."""
        if request not in shutdown_verify.SHARED_REQUESTS:
            raise ApiError(404, "没有这个关机画面")
        target = self.store.factory_dir(digest) / ("shutdown.jpg" if request == 2 else "shutdown_%d.jpg" % request)
        if not target.exists():
            if not self.store.has_input(digest):
                raise ApiError(404, "没有导入这个固件")
            payload = fwread.unpack(self.store.input_path(digest).read_bytes())
            try:
                p = fwread.Payload(bytes(payload), pinned=False)
                at = p.off(p.u32(p.u32(shutdown_verify._table_va(request)) + 4))
                path = bytes(payload[at:at + 64]).split(b"\0")[0]
            except (fwread.FirmwareReadError, ValueError, IndexError, struct.error):
                path = b""
            if not path.endswith(b".jpg"):
                raise ApiError(422, "没有在固件里找到原厂关机图")
            found = shutdown_verify.find_resource(payload, path)
            if len(found) != 1:
                raise ApiError(422, "没有在固件里找到原厂关机图")
            at, n = found[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload[at:at + n])
        return target.read_bytes()

    def check_name(self, body):
        template = (models.template_for(models.for_project(self.store.get_project(body["project_id"])))
                    if body.get("project_id") else self.template)
        slot = tpl.slot(template, int(body.get("slot")))
        name = body.get("name")
        if not isinstance(name, str):
            raise ApiError(400, "预设名必须是文字")
        limit = slot.get("name_max", len(slot["factory_en"]))
        return dict(limit=limit, factory_en=slot["factory_en"], checks=names.check_name(name, limit),
                    ok=names.name_ok(name, limit))


# ---------------------------------------------------------------------------
# HTTP layer, written from our own spec.
#
# Every request takes the same path through `RequestHandler.respond`:
#   Host check -> Origin check (non-GET only) -> /api/ route table, or a static
#   file for GET -> exactly one reply.
# A route's result is rendered to bytes before anything goes on the wire, so a
# failure while rendering is mapped like any other. Exceptions become JSON
# errors through ERROR_REPLIES; anything not listed there is left to
# socketserver, as the spec lists no other.
# ---------------------------------------------------------------------------

LOOPBACK = "127.0.0.1"
LOCAL_HOSTNAMES = (LOOPBACK, "localhost")
JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
BINARY_TYPE = "application/octet-stream"
# Sent right after the status line of every reply, the stdlib's own protocol
# errors included (see RequestHandler.send_response).
SAFETY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)
PREF_KEYS = ("preflight_seen", "guide_off", "guide_start_seen", "guide_work_seen",
             "guide_v1_news_seen", "guide_editor_seen")
# Seconds a single socket read or write may wait before the connection is dropped.
IDLE_TIMEOUT = 30
# A declared request body that the route never read (403, 404, 405, 413 ...) is
# read and thrown away after the reply, up to this many bytes, so the client can
# finish sending and then read the answer instead of a connection reset.
DISCARD_LIMIT = 1024 * 1024 * 1024

log = logging.getLogger("darkslide.http")


class Reply:
    """A reply that is more than "200 + JSON": another status, raw bytes, extra headers.

    `body` is a dict or list (sent as JSON) or bytes (sent as-is with `content_type`).
    """
    __slots__ = ("body", "status", "content_type", "headers")

    def __init__(self, body, status=200, content_type=None, headers=()):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.headers = tuple(headers)


def disposition(filename):
    """The extra header that makes a browser save the body as `filename`."""
    return ("Content-Disposition", 'attachment; filename="%s"' % filename)


def render(reply, errors="strict"):
    """(status, headers, body bytes) for a route result (dict or list) or a Reply.

    Headers come as the route's own first, then Content-Type and Content-Length.
    `errors` is the UTF-8 error handler used for JSON text.
    """
    if not isinstance(reply, Reply):
        reply = Reply(reply)
    if isinstance(reply.body, (bytes, bytearray)):
        data, kind = bytes(reply.body), reply.content_type or BINARY_TYPE
    else:
        data = json.dumps(reply.body, ensure_ascii=False).encode("utf-8", errors)
        kind = reply.content_type or JSON_TYPE
    return reply.status, reply.headers + (("Content-Type", kind), ("Content-Length", str(len(data)))), data


# Exception -> (status, message). Checked in order: StoreError and
# FirmwareReadError are ValueErrors, so they must come before the generic row.
ERROR_REPLIES = (
    (ApiError, lambda exc: (exc.status, str(exc))),
    (StoreError, lambda exc: (400, str(exc))),
    (fwread.FirmwareReadError, lambda exc: (422, "读取固件失败：" + str(exc))),
    (film_luts.FilmLutError, lambda exc: (422, str(exc))),
    (film_requests.FilmRequestError, lambda exc: (422, str(exc))),
    (film_fit.FilmFitError, lambda exc: (422, "拟合失败：" + str(exc))),
    (base_curve.BaseCurveError, lambda exc: (409, "等待相机数据：" + str(exc))),
    (fitting_setup.SetupError, lambda exc: (409, str(exc))),
    (factory_envelope.FactoryEnvelopeError, lambda exc: (409, "读不出原厂范围：" + str(exc))),
    (photo_lut.PhotoLutError, lambda exc: (422, str(exc))),
    (xmp_lut.XmpError, lambda exc: (422, str(exc))),
    ((KeyError, ValueError, TypeError), lambda exc: (400, "请求参数错误")),
)


def error_reply(exc):
    """The JSON error reply for `exc`, or None when the spec does not map it."""
    for kinds, describe in ERROR_REPLIES:
        if isinstance(exc, kinds):
            status, message = describe(exc)
            return Reply({"error": message}, status)
    return None


def split_target(target):
    """(path, query) of a request target.

    As in v0.2, ";params" at the end of the last path segment are not part of
    the path: "/api/status;x" is "/api/status" (urlparse rather than urlsplit).
    """
    parts = urlparse(target)
    return parts.path, parts.query


def printable(text):
    """`text` with each non-printable character (ESC, BEL, CR ...) spelled as an escape."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in text)


# --- static files -----------------------------------------------------------

def stays_inside(base, relative, flavour=os.path):
    """Text-only check, made before the filesystem is touched: does `relative`,
    joined onto the normalised absolute folder `base`, name something strictly
    below it? Refuses NUL bytes, absolute names, other drives and UNC shares
    (//host/share, \\\\host\\share) and ".." walks out of base. `flavour` is
    os.path, or posixpath / ntpath in tests.
    """
    if "\0" in relative:
        return False
    joined = flavour.normpath(flavour.join(base, relative))
    try:
        return joined != base and flavour.commonpath([base, joined]) == base
    except ValueError:  # another drive or share, or mixed absolute and relative
        return False


def static_target(root, url_path):
    """The regular file under `root` that `url_path` names, or None.

    "/" and "" mean index.html; otherwise one leading "/" is dropped and the rest
    is percent-decoded. The name must pass stays_inside first; then the resolved
    path (symlinks followed) must still be inside root and be a regular file.
    """
    if url_path in ("", "/"):
        relative = "index.html"
    else:
        relative = unquote(url_path[1:] if url_path.startswith("/") else url_path)
    base = Path(root).resolve()
    if not stays_inside(str(base), relative):
        return None
    try:
        target = (base / relative).resolve()
        if base in target.parents and target.is_file():
            return target
    except (OSError, ValueError, RuntimeError):  # over-long names, symlink loops
        pass
    return None


def static_type(path):
    # The whole path, not just the name: a bare "data:x.js" would be read as a data: URL.
    kind = mimetypes.guess_type(os.fspath(path))[0] or BINARY_TYPE
    if kind.startswith("text/") or kind == "application/javascript":
        kind += "; charset=utf-8"
    return kind


def static_reply(root, url_path):
    target = static_target(root, url_path)
    if target is not None:
        try:
            return Reply(target.read_bytes(), content_type=static_type(target))
        except OSError:  # vanished or unreadable since the check
            pass
    return Reply(b"not found", 404, TEXT_TYPE)


# --- API routes ---------------------------------------------------------------
# Each route is called as func(handler, **named_groups) and returns a dict/list
# (200 JSON) or a Reply.

def project_context(req, pid):
    project = req.store.get_project(pid)
    try:
        model = models.for_project(project)
        return project, model, models.template_for(model)
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc


def project_response(req, project):
    model = models.for_project(project)
    template = models.template_for(model)
    return dict(project, template=template, detail=tpl.load_detail(template),
                capabilities=models.capabilities(model))


def api_status(req):
    return req.app.status()


def api_import_firmware(req):
    filename = unquote(req.headers.get("X-Filename", ""))
    try:  # the official download is gr3_v210.zip; take fwdc239b.bin out of it
        data, filename = fwzip.unwrap(req.read_body(), filename)
    except fwzip.ZipError as e:
        raise ApiError(422, str(e))
    return req.app.import_firmware(data, filename)


def api_quit(req):
    req.after_reply = req.server.stop_soon
    return {"ok": True}


def api_check_name(req):
    return req.app.check_name(req.read_json())


def api_set_pref(req):
    body = req.read_json()
    key = body.get("key")
    if key not in PREF_KEYS:  # tuple, so an unhashable key is still just "unknown"
        raise ApiError(400, "未知设置")
    return req.store.set_pref(key, bool(body.get("value")))


def api_create_project(req):
    body = req.read_json()
    model = models.from_sha(body.get("input_sha256"))
    return project_response(req, req.store.create_project(body.get("title"), body.get("input_sha256"),
                            models.template_for(model), body.get("target_body"), body.get("camera_variant")))


def api_factory_shutdown(req, digest, n=None):
    return Reply(req.app.factory_shutdown(digest, 2 if n is None else int(n)), content_type="image/jpeg")


def api_factory(req, digest):
    return req.app.factory(digest)


def api_factory_icon(req, digest, n):
    req.app.factory(digest)
    icon = req.store.factory_dir(digest) / "icons" / ("%d.png" % int(n))
    if not icon.is_file():
        raise ApiError(404, "没有这个图标")
    return Reply(icon.read_bytes(), content_type="image/png")


def api_build_firmware(req, bid):
    from .legacy_import import checked_build
    path = req.store.build_path(bid,'manifest.json')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    model, output = checked_build(path.parent,manifest)
    data = output.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["output_sha256"]:
        raise ApiError(409, "下载期间历史构建已改变")
    return Reply(data,content_type=BINARY_TYPE,headers=[disposition(model.filename)])


def api_build_manifest(req, bid):
    data = req.store.build_path(bid, "manifest.json").read_bytes()
    return Reply(data, content_type=JSON_TYPE, headers=[disposition("darkslide-manifest-%s.json" % bid)])


def api_get_project(req, pid):
    return project_response(req, req.store.get_project(pid))


def api_set_menu_language(req, pid):
    body = req.read_json()
    if type(body) is not dict or set(body) != {"menu_language"}:
        raise ApiError(400, "请求格式不正确")
    return project_response(req, req.store.set_menu_language(pid, body["menu_language"]))


def api_rename_project(req, pid):
    return project_response(req, req.store.rename_project(pid, req.read_json().get("title")))


def api_set_target_body(req, pid):
    body = req.read_json()
    if type(body) is not dict or set(body) not in ({"target_body"}, {"camera_variant"}):
        raise ApiError(400, "请求格式不正确")
    return project_response(req, req.store.set_target_body(pid, body.get("target_body"), project_context(req, pid)[2], body.get("camera_variant")))


def api_delete_project(req, pid):
    """The user deletes a project from the home page (after confirming there)."""
    removed = req.store.delete_project(pid)
    return dict(deleted=pid, builds_removed=removed, projects=req.store.list_projects(), builds=req.store.builds())


def api_put_draft(req, pid):
    return project_response(req, req.store.put_draft(pid, req.read_json().get("changes"), project_context(req, pid)[2]))


def api_save_project(req, pid):
    return project_response(req, req.store.save(pid, project_context(req, pid)[2]))


def api_upload_asset(req, pid):
    data = req.read_body()
    if req.query == "kind=shutdown":
        return req.store.put_shutdown_jpeg(pid, data)
    return req.store.put_asset(pid, data, expect_icon=(req.query == "kind=icon"))


def api_get_asset(req, pid, digest):
    path = req.store.asset_path(pid, digest)
    kind = "image/jpeg" if path.suffix == ".jpg" else "image/png"
    return Reply(path.read_bytes(), content_type=kind)


def api_generate(req, pid):
    from . import inplace_generation
    app, store = req.app, req.store
    if (film_requests.needs_closed_output(store.get_project(pid).get("changes") or {}) and
            inplace_generation.production_available() is not True):
        raise ApiError(422, "胶片或四位置调色的完整输出尚未开放，请使用只读核对")
    project, model, template = project_context(req, pid)
    if not models.capabilities(model)['full_firmware']:
        raise ApiError(422, "IIIx 完整固件生成尚未接入此入口，请使用只读核对")
    if not template.get("reviewed"):
        raise ApiError(409, "模板不可用，不能生成")
    context_project = copy.deepcopy(project)
    project = copy.deepcopy(store.get_project(pid))
    if project != context_project:
        raise ApiError(409, "工程在准备生成期间改变，请重新操作")
    needs_inplace = film_requests.needs_closed_output(project.get("changes") or {})
    if needs_inplace and inplace_generation.production_available() is not True:
        raise ApiError(422, "胶片或四位置调色的完整输出尚未开放，请使用只读核对")
    source = store.input_path(project["input_sha256"])
    if not source.exists():
        raise ApiError(409, "找不到官方原件，请重新导入")
    if needs_inplace:
        try:
            # Pass the original saved project: run_inplace resolves and freezes
            # bake/LUT provenance itself. Never use the legacy composition path.
            output, manifest = generate.run_inplace(store, project, template, source.read_bytes())
        except generate.GenerateError as exc:
            return Reply({"error": str(exc), "checks": exc.checks}, 422)
        return store.put_build(output, manifest)
    if film_requests.available():
        # A 调色 on presets 8-11 is written as the LUT fitted from it (film + adjustments).
        project = dict(project, changes=store.film_changes_for_generate(pid, project.get("changes", {})))
    try:
        chosen = film_requests.from_changes(project.get("changes", {}))
    except film_requests.FilmRequestError as exc:
        raise ApiError(422, str(exc))
    users = sorted({e["film"] for e in chosen if film_luts.USER_ID.match(e["film"])})
    profiles = store.film_lut_profiles(pid, users) if users and film_requests.available() else None
    env = _factory_envelope(req, pid) if chosen and film_requests.available() else None
    if chosen and film_requests.available() and not env:
        raise ApiError(409, "读不出原厂范围，不能写入胶片")
    if env is not None:
        for ident in sorted({e["film"] for e in chosen}):
            profile = profiles[ident] if film_luts.USER_ID.match(ident) else film_profiles.load_profile(ident)
            bad = film_fit.envelope_violations(profile, env)
            if bad:
                raise ApiError(422, "「%s」超出了理光相机原厂的范围，不能写进相机：%s" % (ident, bad[0]))
    try:
        output, manifest = generate.run(store, project, template, source.read_bytes(), film_profiles=profiles)
    except generate.GenerateError as exc:
        return Reply({"error": str(exc), "checks": exc.checks}, 422)
    return store.put_build(output, manifest)


def _iiix_candidate_body(req):
    body = req.read_json()
    if (type(body) is not dict or set(body) != {'expected_changes', 'recipe_extension'} or
            type(body['expected_changes']) is not dict or type(body['recipe_extension']) is not bool):
        raise ApiError(400, '候选请求格式不正确')
    if body['recipe_extension'] and not models.IIIX_RECIPE_MODULE:
        raise ApiError(422, '1.0 不提供照片配方模块（它会让固件变大）；只能原位替换胶片')
    return body


def api_iiix_candidate(req, pid):
    """Explicit offline candidate; never changes the production output gate."""
    body = _iiix_candidate_body(req)
    if not req.app.iiix_generation_lock.acquire(blocking=False):
        raise ApiError(409, '已有 IIIx 候选正在生成，请稍后再试')
    try:
        return _iiix_candidate(req, pid, body)
    finally:
        req.app.iiix_generation_lock.release()


def _iiix_candidate(req, pid, body):
    """The candidate itself; the caller holds the generation lock."""
    from . import gr3x_generation
    project, model, template = project_context(req, pid)
    project = copy.deepcopy(project)
    if model != models.GR3X:
        raise ApiError(422, '此入口仅用于 GR IIIx 1.60')
    if template.get('reviewed') is not True:
        raise ApiError(409, '模板不可用')
    if project.get('unsaved') or project.get('changes', {}) != body['expected_changes']:
        raise ApiError(409, '当前选择尚未保存或已改变')
    source = req.store.input_path(model.input_sha)
    if not source.exists():
        raise ApiError(409, '找不到官方原件，请重新导入')
    try:
        output, manifest = gr3x_generation.run(req.store, project, template, source.read_bytes(),
            recipe_extension=body['recipe_extension'])
    except generate.GenerateError as exc:
        return Reply({'error': str(exc), 'checks': exc.checks}, 422)
    return req.store.put_build(output, manifest)


def api_film_catalog(req):
    return dict(film_luts.catalog(), data_revision=film_profiles.data_revision(), writer=film_requests.status(), icons=req.store.film_icon_versions(),
                icons_dir=str(req.store.film_icons_dir))


def api_film_icon(req, fid):
    data, mime = req.store.film_icon(fid)
    return Reply(data, content_type=mime)


def api_film_preview(req, fid):
    return Reply(film_luts.preview_path(fid).read_bytes(), content_type=TEXT_TYPE)


def api_import_film_lut(req, pid):
    filename = unquote(req.headers.get("X-Filename", ""))
    return req.store.put_film_lut(pid, req.read_body(), filename)


def api_list_film_luts(req, pid):
    return {"luts": req.store.list_film_luts(pid), "capabilities": models.capabilities(project_context(req, pid)[1])}


def api_delete_film_lut(req, pid, lid):
    return {"luts": req.store.delete_film_lut(pid, lid), "capabilities": models.capabilities(project_context(req, pid)[1])}


def api_get_film_lut(req, pid, lid):
    return Reply(req.store.film_lut_path(pid, lid).read_bytes(), content_type=TEXT_TYPE)


def _official(req, pid):
    source = req.store.input_path(req.store.get_project(pid)["input_sha256"])
    if not source.exists():
        raise ApiError(409, "找不到官方原件，请重新导入")
    return source


def _camera_model(req, pid):
    """Base gamma read from this project's official firmware by running the original code
    in Unicorn (user, 2026-10-14: Unicorn for reliability; the pure-Python reader
    base_curve_python stays as an unused backup). Ricoh data: memory only. The component is
    downloaded once with the user's consent; its first use on this computer runs the self check."""
    source = _official(req, pid)
    root = req.store.root
    setup = fitting_setup.status(root)
    if setup["state"] in ("unsupported", "failed"):
        raise fitting_setup.SetupError(setup["note"])
    if setup["state"] == "missing":
        raise ApiError(409, "需要先下载拟合组件：" + setup["note"])
    contract = base_curve.base_curve(source)
    if setup["state"] != "ready":
        fitting_setup.self_check(source, contract, root)
    if project_context(req,pid)[1] == models.GR3X:
        film_fit.contract_model(contract, models.GR3X.id)
    return contract


_LEGACY_IMPORT_LOCK = threading.Lock()


def api_legacy_import(req):
    from .legacy_import import import_store
    body = req.read_json()
    if (type(body) is not dict or set(body)-{'source','apply','source_digest'} or
            type(body.get('source')) is not str or type(body.get('apply',False)) is not bool):
        raise ApiError(400,'旧目录导入请求格式错误')
    with _LEGACY_IMPORT_LOCK:
        return import_store(req.store,body['source'],apply=body.get('apply',False),expected_digest=body.get('source_digest'))


def api_fitting_setup(req):
    return fitting_setup.status(req.store.root)


def api_install_fitting(req):
    return fitting_setup.install(req.store.root)


def api_photo_lut_learn(req):
    """Learn a look from a reference photo; pixels arrive decoded and downscaled by the page."""
    body = req.read_json()
    return photo_lut.learn(body.get("reference"), body.get("sources"))


def api_photo_lut_bake(req):
    body = req.read_json()
    name = body.get("name")
    size = body.get("size", photo_lut.SIZE)
    cube = photo_lut.bake(body.get("model"), body.get("params"),
                          name if type(name) is str and name else "Reference look", size)
    return {"cube": cube}


def api_xmp_lut(req):
    """Colour settings of a Lightroom / Camera Raw .xmp preset as an approximate .cube."""
    name = unquote(req.headers.get("X-Filename", ""))
    stem = re.sub(r"\.xmp$", "", name, flags=re.I).strip() or None
    raw = req.read_body()
    return xmp_lut.convert(raw, None if xmp_lut.parse(raw).get("Name") else stem)


def _factory_envelope(req, pid):
    """RICOH's own numeric ranges, read from this project's firmware (memory only).
    Raises when it cannot be read: writing is then refused, never let through."""
    if project_context(req, pid)[1] == models.GR3X:
        from .gr3x_factory_envelope import factory_envelope as read_iiix
        return read_iiix(_official(req, pid))
    return factory_envelope.factory_envelope(_official(req, pid))


GRADE_LIMITS = Path(__file__).with_name("grade_limits.json")


def _grade_limits_fingerprint(films):
    """What the precomputed slider ranges depend on: the page's grading code, the camera data
    and each film's preview LUT. Any change makes the table stale (never shown stale)."""
    from .grade_bake import page_bake_sha
    return dict(adjust_bake_sha256=page_bake_sha(),
                film_data_revision=film_profiles.data_revision(),
                previews={f: hashlib.sha256(film_luts.preview_path(f).read_bytes()).hexdigest() for f in films})


def api_grade_limits(req, pid):
    """Per built-in film: how far each camera-writable slider can go on its own and still fit
    into this camera (tools/build_grade_limits.py). Several sliders together are checked live."""
    model = project_context(req, pid)[1]
    try:
        table = json.loads(GRADE_LIMITS.read_text(encoding="utf-8"))
        films = sorted(table["fingerprint"]["previews"])
        valid = table.get("schema") == 1 and table["fingerprint"] == _grade_limits_fingerprint(films)
        limits = table["models"].get(model.id) if valid else None
    except (OSError, ValueError, KeyError, TypeError, film_luts.FilmLutError, film_requests.FilmRequestError):
        valid, limits = False, None
    try:  # the page's rough hint while dragging (curve start follows black, end follows white)
        env = _factory_envelope(req, pid)
        ends = {k: env.get(k) for k in ("curve_first_max", "curve_last_min")}
        ends = ends if all(type(v) is int for v in ends.values()) else None
    except (OSError, ValueError, ApiError):
        ends = None
    return dict(valid=bool(valid and limits), model_id=model.id, limits=limits or {}, curve_ends=ends,
                note="每个滑块单独拉时还能写进相机的范围；几个一起拉时，以调完后的自动检查为准。")


def api_film_grade_check(req, pid):
    """Would this graded film fit into the camera? Real fit on the page's own baked LUT;
    nothing is stored and no firmware is touched."""
    from .film_fit_check import check_cube
    body = req.read_json()
    if type(body) is not dict or type(body.get("cube")) is not str or not 0 < len(body["cube"]) <= 8 * 1024 * 1024:
        raise ApiError(400, "检查请求格式不正确")
    model = project_context(req, pid)[1]
    contract = _camera_model(req, pid)
    env = _factory_envelope(req, pid)
    return check_cube(body["cube"].encode("utf-8"), model, contract, env)


def _lut_job_key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _job_reply(job):
    if job is None:
        return dict(state="none")
    return {k: job[k] for k in ("state", "done", "total", "ahead", "result", "error", "partial") if k in job}


def _ranges_source(req, pid, lid):
    from . import lut_ranges
    entry = req.store.film_luts(pid).get(lid)
    if entry is None:
        raise ApiError(404, "找不到这个 LUT")
    if entry.get("bake") is not None:
        raise ApiError(422, "调色生成的 LUT 不单独计算滑块范围")
    data = req.store.film_lut_path(pid, lid).read_bytes()
    model = project_context(req, pid)[1]
    fp = lut_ranges.fingerprint(model.id, req.store.get_project(pid)["input_sha256"], hashlib.sha256(data).hexdigest())
    return data, model, fp, ("ranges", pid, lid, _lut_job_key(fp))


def _cached_ranges(store, pid, lid, fp):
    """The sliders of this LUT worked out so far (and whether the LUT itself fits), or {}."""
    try:
        record = store.film_lut_ranges(pid, lid)
    except (OSError, ValueError):
        return {}
    if type(record) is dict and record.get("fingerprint") == fp and type(record.get("limits")) is dict:
        return record["limits"]
    return {}


_RANGES_LOCK = threading.Lock()


def api_lut_ranges(req, pid, lid):
    """A user LUT's slider ranges worked out so far. Only sliders the user has touched are
    worked out (lut_ranges), each cached with what it depends on; the page shows a rough
    estimate for the rest. Several sliders together are still checked live."""
    from . import lut_ranges
    _, _, fp, key = _ranges_source(req, pid, lid)
    out = _job_reply(lut_ranges.JOBS.status(key))
    out.pop("result", None)
    if out["state"] in ("none", "done"):
        out["state"] = "idle"
    out["limits"] = dict(_cached_ranges(req.store, pid, lid, fp), **out.pop("partial", {}))
    return out


def api_start_lut_ranges(req, pid, lid):
    """Work out one slider of this LUT (the one being dragged): added to the LUT's running job,
    or a new job is started. Nothing happens when it is already known."""
    from . import lut_ranges
    body = req.read_json()
    if type(body) is not dict or body.get("slider") not in lut_ranges.SLIDERS:
        raise ApiError(400, "请求格式不正确")
    slider = body["slider"]
    data, model, fp, key = _ranges_source(req, pid, lid)
    cached = _cached_ranges(req.store, pid, lid, fp)
    if slider in cached or cached.get("_film_writable") is False or lut_ranges.JOBS.prioritize(key, slider):
        return api_lut_ranges(req, pid, lid)
    contract, env, store = _camera_model(req, pid), _factory_envelope(req, pid), req.store
    try:
        base = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ApiError(422, "这个 LUT 不是文本格式") from None

    def save(update):
        with _RANGES_LOCK:  # one file per LUT, read-merge-write
            limits = dict(_cached_ranges(store, pid, lid, fp), **update)
            store.set_film_lut_ranges(pid, lid, dict(fingerprint=fp, limits=limits))

    def run(api):
        return lut_ranges.compute_ranges(api, model.id, contract, env, base, store.root, save=save,
                                         writable=cached.get("_film_writable"))
    with _RANGES_LOCK:
        if not lut_ranges.JOBS.prioritize(key, slider):
            lut_ranges.JOBS.forget(key)
            lut_ranges.JOBS.submit(key, run, 2 * lut_ranges._left(0, None), priority=[slider])
    return api_lut_ranges(req, pid, lid)


def api_start_fit_into(req, pid):
    """「收进相机范围」: look for the smallest change that brings this look into the camera's
    range. Only a calculation: the result is offered to the page and nothing is stored."""
    from . import lut_ranges
    body = req.read_json()
    if type(body) is not dict or type(body.get("cube")) is not str or not 0 < len(body["cube"]) <= 8 * 1024 * 1024:
        raise ApiError(400, "请求格式不正确")
    film_luts.parse_cube(body["cube"].encode("utf-8"))
    model = project_context(req, pid)[1]
    contract, env = _camera_model(req, pid), _factory_envelope(req, pid)
    job = _lut_job_key("fit-into", model.id, req.store.get_project(pid)["input_sha256"], body["cube"])
    lut_ranges.JOBS.forget(("fit-into", pid, job))
    root = req.store.root
    lut_ranges.JOBS.submit(("fit-into", pid, job), lambda api: lut_ranges.fit_into(api, model.id, contract, env, body["cube"], root), 26, lane="now")
    return dict(_job_reply(lut_ranges.JOBS.status(("fit-into", pid, job))), job=job)


def api_fit_into_status(req, pid, job):
    from . import lut_ranges
    project_context(req, pid)
    return dict(_job_reply(lut_ranges.JOBS.status(("fit-into", pid, job))), job=job)


def api_film_check(req, pid):
    """Which built-in films stay inside RICOH's own numeric range (others: preview/export only)."""
    env = _factory_envelope(req, pid)
    model = project_context(req, pid)[1]
    films = {}
    for f in film_luts.catalog()["films"]:
        profile = film_profiles.load_profile(f["id"], model.id)
        bad = film_fit.envelope_violations(profile, env)
        if model == models.GR3X and not bad:
            from .gr3x_inplace_support import _numeric
            try:
                _numeric(profile, env)
            except ValueError as exc:
                bad = [str(exc)]
        films[f["id"]] = bad
    return dict(model_id=env.get("model_id"), films=films)


def api_film_prepare(req, pid):
    """Read an official original and return only a sparse-check summary."""
    from . import inplace_generation, film_requests, body_target
    body = req.read_json()
    if (type(body) is not dict or set(body) != {'expected_changes', 'request_id'} or
            type(body['expected_changes']) is not dict or
            type(body['request_id']) is not str or not 1 <= len(body['request_id']) <= 80):
        raise ApiError(400, '只读核对请求格式不正确')
    project = copy.deepcopy(req.store.get_project(pid))
    _, model, template = project_context(req, pid)
    if (template.get('reviewed') is not True or template.get('model') != model.name or
            template.get('firmware') != model.firmware or project.get('template_id') != template.get('id')):
        raise ApiError(409, '工程模板不适用于当前机型核对')
    if project.get('unsaved') or project.get('changes', {}) != body['expected_changes']:
        raise ApiError(409, '当前选择尚未保存或已改变，请保存后重新核对')
    try:
        target = body_target.require_family(body_target.of(project), model.name, model.firmware)
    except body_target.BodyTargetError as exc:
        raise ApiError(409, str(exc)) from exc
    if target is None:
        raise ApiError(409, '核对前请先选机身版本；同一份固件分不出普通机和 HDF')
    try:
        data_revision = film_profiles.data_revision()
        effective = copy.deepcopy(project)
        effective['changes'] = req.store.film_changes_for_generate(pid, project.get('changes', {}))
        requests = film_requests.from_changes(effective['changes'])
        source_index = copy.deepcopy(req.store.film_luts(pid))
        user_ids = [r['film'] for r in requests if r['film'].startswith('user_lut_')]
        profiles = req.store.film_lut_profiles(pid, user_ids) if user_ids else {}
        if project.get('input_sha256') != model.input_sha:
            raise ApiError(409, '工程必须使用对应机型的官方原件')
        source = req.store.input_path(project['input_sha256'])
        official = source.read_bytes()
        if len(official) != model.input_size or hashlib.sha256(official).hexdigest() != model.input_sha:
            raise ApiError(409, '官方原件校验失败，请重新导入')
        decoded = bytes(fwread.unpack(official))
        if model == models.GR3X:
            from .iiix_prepare import prepare
            result = prepare(decoded, effective, profiles)
        else:
            result = inplace_generation.prepare(decoded, effective, film_profiles=profiles)
        if (req.store.film_changes_for_generate(pid, project.get('changes', {})) != effective['changes'] or
                req.store.film_luts(pid) != source_index or
                (user_ids and req.store.film_lut_profiles(pid, user_ids) != profiles)):
            raise ApiError(409, '核对期间 LUT 拟合来源改变，请重新核对')
        # Detect a concurrent save/draft update before publishing the summary.
        current = req.store.get_project(pid)
        if (current.get('unsaved') or current.get('changes', {}) != project.get('changes', {}) or
                current.get('input_sha256') != project.get('input_sha256') or
                current.get('template_id') != project.get('template_id') or
                current.get('target_body') != project.get('target_body') or
                current.get('camera_variant') != project.get('camera_variant')):
            raise ApiError(409, '核对期间工程改变，请重新核对')
        if film_profiles.data_revision() != data_revision:
            raise ApiError(409, '核对期间胶片数据更新，请重新核对')
        body_check = body_target.sparse_report(decoded, target, result['windows'])
        if body_check.get('status') != 'protected_windows_unchanged':
            raise ApiError(422, body_target.summary(body_check))
    except (inplace_generation.InplaceGenerationError, film_requests.FilmRequestError, ValueError, OSError) as exc:
        raise ApiError(422, str(exc)) from exc
    checks = result['checks']
    return dict(project_id=pid, request_id=body['request_id'], layout=result['layout'], data_revision=data_revision,
                slots=[dict(slot=s['slot'], film=s['film'], factory=s['film'] is None)
                       for s in result['current_slots']],
                data_verified=checks['data_verified'],
                shared_reference_verified=checks['shared_bytes_match_reference'],
                coverage_verified=checks['complete_window_coverage_verified'],
                decoded_growth_bytes=0, production_approved=False, full_image_created=False,
                body=dict(target_body=target, status=body_check['status'],
                          changed_windows=body_check['changed_windows'],
                          protected_windows=len(body_check['protected_windows']),
                          summary=body_target.summary(body_check),
                          compatibility_approved=False, device_tested=False),
                note='仅离线数据和改动范围核对；生成时仍会重新检查，不能代替所选机身与具体配置的实测。')


_CURVE_ENDS = re.compile(r"胶片重建曲线起点或终点超出原厂数值边界：起点 (-?\d+)（原厂 (-?\d+)～(-?\d+)），终点 (-?\d+)（原厂 (-?\d+)～(-?\d+)）")


def _fit_refusal(exc):
    """(reason, violations) for a refused fit. The IIIx rebuild check names both curve ends; say it the
    way the GR III range check does, naming only the end that is outside, so both models read the same."""
    m = _CURVE_ENDS.search(str(exc))
    if m:
        first, flo, fhi, last, llo, lhi = map(int, m.groups())
        bad = ["曲线起点 %d 超出原厂范围（%d～%d）" % (first, flo, fhi)] if not flo <= first <= fhi else []
        bad += ["曲线终点 %d 超出原厂范围（%d～%d）" % (last, llo, lhi)] if not llo <= last <= lhi else []
        if bad:
            return "超出了理光相机原厂的范围：" + bad[0], bad
    return "这组颜色写不进相机：" + str(exc), []


def _fit_and_keep(req, pid, lid):
    model = project_context(req,pid)[1]
    entry = copy.deepcopy(req.store.film_luts(pid).get(lid))
    if entry is None:
        raise ApiError(404, '找不到这个 LUT')
    if model == models.GR3X and entry.get('bake') is not None:
        from .gr3x_recipe_lineage import metadata
        metadata(entry['bake'])
    contract = _camera_model(req, pid)
    data = req.store.film_lut_path(pid, lid).read_bytes()
    env = _factory_envelope(req, pid)

    def refuse(reason, violations):
        # Out of the camera's range: nothing is fitted or stored (user, 2026-10-17: check first,
        # never keep a fit that cannot be written). An imported LUT remembers that it was checked.
        if entry.get('bake') is None:
            req.store.set_film_lut_check(pid, lid, dict(writable=False, reason=reason, violations=violations, model_id=model.id))
        raise ApiError(422, reason + "。没有拟合进相机，也没有保存；可以点「收进相机范围」看看能写进相机的版本。")
    try:
        if model == models.GR3X:
            from .gr3x_camera_preview import preview, installed_fit_report
            profile, report = film_fit.fit_cube(data,lid,contract,env,model_id=model.id)
        else:
            profile, report = film_fit.fit_cube(data, lid, contract, env)
    except (film_fit.FilmFitError, film_requests.FilmRequestError) as exc:
        refuse(*_fit_refusal(exc))
    if model == models.GR3X:
        try:  # IIIx: the fit must also survive the 33-knot rebuild the camera will hold
            report = installed_fit_report(data,contract,profile,report,env)
        except ValueError as exc:
            refuse(*_fit_refusal(exc))
    violations = film_fit.envelope_violations(profile, env)
    if violations:
        refuse("超出了理光相机原厂的范围：" + violations[0], violations)
    report["envelope"] = dict(checked=True, model_id=env.get("model_id"), violations=violations)
    if model == models.GR3X and entry.get('bake') is not None:
        from .gr3x_recipe_lineage import attach
        profile = attach(profile, entry['bake'])
    camera = preview(contract['values'],profile,env) if model == models.GR3X else film_fit.camera_cube(contract['values'],profile)
    if model == models.GR3X and (req.store.film_luts(pid).get(lid) != entry or
            req.store.film_lut_path(pid, lid).read_bytes() != data):
        raise ApiError(409, '拟合期间 LUT 或调色来源改变，请重新拟合')
    return req.store.set_film_lut_fit(pid, lid, profile, report, camera)


def api_fit_film_lut(req, pid, lid):
    return _fit_and_keep(req, pid, lid)


def api_bake_grade(req, pid):
    """Save a preset's 调色 (film + adjustments, as a .cube made in the page) and fit it."""
    body = req.read_json()
    cube, film, grade = body.get("cube"), body.get("film"), body.get("grade")
    if not isinstance(cube, str) or not isinstance(body.get("name", ""), str):
        raise ApiError(400, "请求格式错误")
    if film is not None and not req.store._film_known(pid, film if isinstance(film, str) else ""):
        raise ApiError(422, "选的胶片没有可用数据")
    grade = req.store._clean_grade("调色", grade)
    bake = {"film": film, "grade": grade}
    if project_context(req,pid)[1] == models.GR3X:
        from .gr3x_recipe_lineage import capture
        bake['recipe_source'] = capture(req.store, pid, film)
    existed = film_luts.lut_id(cube.encode("utf-8")) in req.store.film_luts(pid)
    lut = req.store.put_film_lut(pid, cube.encode("utf-8"), body.get("name") or "调色", bake=bake)
    try:
        return _fit_and_keep(req, pid, lut["id"])
    except ApiError as exc:
        if exc.status == 422 and not existed:  # a refused 调色 leaves nothing behind
            req.store.delete_film_lut(pid, lut["id"])
        raise


# --- long requests as jobs ----------------------------------------------------
# 生成 and 拟合 can take longer than a browser is willing to wait for one reply (WebKit gives up
# on a request that has had no answer for 60 seconds, and the page shows "Load failed"). So the page
# hands them in as a job and asks how it is doing, the way 「收进相机范围」 does: POST .../jobs, then
# GET .../jobs/<job> until it is done. A job runs the very route function the plain endpoint runs
# (api_generate, api_fit_film_lut, ...) and keeps its status and JSON body, so the result, the
# errors and the checks are exactly what the plain request would have answered. The plain
# endpoints stay as they were.

LONG_TASKS = ("generate", "iiix-candidate", "film-fit", "film-bake")
GENERATING = ("generate", "iiix-candidate")
LONG_KEEP = 600  # seconds a finished job can still be read


class JobRequest:
    """What a route function sees of the request when it runs as a job."""
    query = ""
    after_reply = None

    def __init__(self, app, body):
        self.app, self.headers, self._body = app, {}, body

    @property
    def store(self):
        return self.app.store

    def read_json(self):
        return copy.deepcopy(self._body)

    def read_body(self):
        return json.dumps(self._body).encode("utf-8")


def _long_key(job):
    return ("long", job)


def _long_sweep(app):
    """Forget jobs that finished a while ago (the caller holds app.long_jobs_lock)."""
    from . import lut_ranges
    now = time.time()
    for job in list(app.long_jobs):
        state = lut_ranges.JOBS.status(_long_key(job))
        if state is None or (state["state"] in ("done", "failed") and now - state.get("finished", now) > LONG_KEEP):
            lut_ranges.JOBS.forget(_long_key(job))
            del app.long_jobs[job]


def _long_reply(app, pid, job):
    """The job's state for the page: queued / running (done, total, ahead), done (result) or
    failed (error, status and body, as the plain request would have answered)."""
    from . import lut_ranges
    row = app.long_jobs.get(job)
    state = lut_ranges.JOBS.status(_long_key(job)) if row and row["pid"] == pid else None
    if state is None:
        return dict(state="none", job=job)
    out = dict(_job_reply(state), job=job, task=row["task"])
    out.pop("result", None)
    if state["state"] == "done":
        answer = state["result"]
        if answer["status"] < 400:
            out["result"] = answer["body"]
        else:
            out.update(state="failed", status=answer["status"], error=answer["body"].get("error", ""), body=answer["body"])
    elif state["state"] == "failed":
        out.update(status=500, body=dict(error=out.get("error", "")))
    return out


def _long_call(task, req, pid, lid):
    if task == "generate":
        return api_generate(req, pid)
    if task == "iiix-candidate":
        return _iiix_candidate(req, pid, _iiix_candidate_body(req))
    if task == "film-fit":
        return api_fit_film_lut(req, pid, lid)
    return api_bake_grade(req, pid)


def api_start_job(req, pid):
    """Hand in a 生成 ("generate", "iiix-candidate") or a 拟合 ("film-fit" of "lut", "film-bake") as a
    job. `body` is what the plain endpoint would have been sent. Only one 生成 job runs at a time
    (the plain /generate, which the page no longer uses, stays as it was); the same 拟合 asked
    for again joins the one that is already running."""
    from . import lut_ranges
    body = req.read_json()
    task, inner, lid = body.get("task"), body.get("body", {}), body.get("lut")
    if (set(body) - {"task", "body", "lut"} or task not in LONG_TASKS or type(inner) is not dict or
            (task == "film-fit") != (lid is not None) or
            (lid is not None and not (type(lid) is str and film_luts.USER_ID.match(lid)))):
        raise ApiError(400, "请求格式不正确")
    req.store.get_project(pid)  # an unknown project: the same error as the plain endpoint
    app, generating = req.app, task in GENERATING
    with app.long_jobs_lock:
        _long_sweep(app)
        if generating:
            if not app.generation_lock.acquire(blocking=False):
                raise ApiError(409, "已有 IIIx 候选正在生成，请稍后再试" if task == "iiix-candidate" else "已有生成正在进行，请稍后再试")
            job = _lut_job_key("long", task, pid, time.time(), len(app.long_jobs))
        else:
            job = _lut_job_key("long", task, pid, lid, inner)
            if (lut_ranges.JOBS.status(_long_key(job)) or {}).get("state") in ("queued", "running"):
                return _long_reply(app, pid, job)

        def run(api):
            try:
                try:
                    answer = _long_call(task, JobRequest(app, inner), pid, lid)
                except Exception as exc:  # noqa: BLE001 - mapped like a plain request; anything else is a 500
                    answer = error_reply(exc)
                    if answer is None:
                        log.exception("job %s failed", task)
                        answer = Reply({"error": str(exc) or exc.__class__.__name__}, 500)
                status, _, data = render(answer, errors="backslashreplace")
                return dict(status=status, body=json.loads(data))
            finally:
                if generating:
                    app.generation_lock.release()
        try:
            app.long_jobs[job] = dict(pid=pid, task=task)
            lut_ranges.JOBS.forget(_long_key(job))
            lut_ranges.JOBS.submit(_long_key(job), run, 1, lane="generate" if generating else "fit")
        except BaseException:
            app.long_jobs.pop(job, None)
            if generating:
                app.generation_lock.release()
            raise
    return _long_reply(app, pid, job)


def api_job_status(req, pid, job):
    app = req.app
    with app.long_jobs_lock:
        return _long_reply(app, pid, job)


def api_list_jobs(req, pid):
    """This project's jobs that are still queued or running (a reloaded page picks them up again)."""
    app = req.app
    with app.long_jobs_lock:
        _long_sweep(app)
        replies = [_long_reply(app, pid, job) for job, row in app.long_jobs.items() if row["pid"] == pid]
    return dict(jobs=[r for r in replies if r["state"] in ("queued", "running")])


def api_film_lut_camera(req, pid, lid):
    if project_context(req, pid)[1] == models.GR3X:
        from .gr3x_camera_preview import preview
        profile = req.store.film_lut_profiles(pid,[lid])[lid]
        return Reply(preview(_camera_model(req,pid)['values'],profile,_factory_envelope(req,pid)),content_type=TEXT_TYPE)
    return Reply(req.store.film_lut_camera_path(pid, lid).read_bytes(), content_type=TEXT_TYPE)


_CAMERA_CUBES = {}


def api_film_camera(req, pid, fid):
    """Camera-model preview of a built-in film, on this project's firmware."""
    contract = _camera_model(req, pid)
    digest = hashlib.sha256(json.dumps(contract["values"], separators=(",", ":")).encode()).hexdigest()
    key = (contract["official_sha256"], contract["model_id"], digest,
           json.dumps(contract.get("settings"), sort_keys=True, default=str), film_profiles.data_revision(), fid)
    if key not in _CAMERA_CUBES:
        model = project_context(req,pid)[1]
        profile = film_profiles.load_profile(fid,model.id)
        if model == models.GR3X:
            from .gr3x_camera_preview import preview
            _CAMERA_CUBES[key] = preview(contract['values'],profile,_factory_envelope(req,pid))
        else:
            _CAMERA_CUBES[key] = film_fit.camera_cube(contract['values'],profile)
    return Reply(_CAMERA_CUBES[key], content_type=TEXT_TYPE)


def _recipe_project(req,pid):
    project,model,_ = project_context(req,pid)
    if model != models.GR3X:raise ApiError(409,'照片配方仅适用于 IIIx 1.60 工程')
    return project


def api_import_photo_recipe(req, pid):
    from . import photo_recipe_dng, photo_recipe_jpeg
    _recipe_project(req, pid)
    # Only the validated packet is retained. The uploaded original is not saved.
    try:
        data = req.read_body()
        reader = photo_recipe_jpeg if data.startswith(b"\xff\xd8") else photo_recipe_dng
        packet = reader.extract(data)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    return req.store.put_photo_recipe(pid, packet)


def api_list_photo_recipes(req, pid):
    _recipe_project(req, pid)
    return {"recipes": req.store.list_photo_recipes(pid)}


def api_photo_recipe_camera(req, pid, rid):
    _recipe_project(req, pid)
    profile = req.store.photo_recipe_profile(pid, rid)
    contract = _camera_model(req, pid)
    return Reply(film_fit.camera_cube(contract["values"], profile), content_type=TEXT_TYPE)


def _install_limits(req, pid):
    project = _recipe_project(req, pid)
    from .gr3x_factory_envelope import factory_envelope, FactoryEnvelopeError
    try:
        return factory_envelope(req.store.input_path(project['input_sha256']))
    except FactoryEnvelopeError as error:
        raise ApiError(409, '无法核对当前安装参数，请重新导入 IIIx 官方原件：' + str(error)) from error


def api_film_compatibility(req, pid):
    from .gr3x_camera_preview import readiness, LAYOUT
    limits = _install_limits(req, pid)
    profiles = [film_profiles.load_profile(fid, models.GR3X.id)
                for fid in film_profiles.ready_profile_ids()]
    profiles += req.store.list_photo_recipes(pid)
    result = {p['id']: readiness(p, limits) for p in profiles}
    for entry in req.store.list_film_luts(pid):
        ident = entry['id']
        if not entry['fitted']:
            result[ident] = dict(numeric_installable=False, reason='尚未拟合', needs_fit=True)
            continue
        try:
            result[ident] = readiness(req.store.film_lut_profiles(pid, [ident])[ident], limits)
        except ValueError as error:
            result[ident] = dict(numeric_installable=False, reason=str(error))
    return dict(layout=LAYOUT, profiles=result, camera_verified=False)


def api_photo_recipe_install_camera(req, pid, rid):
    from .gr3x_camera_preview import preview
    limits = _install_limits(req, pid)
    profile = req.store.photo_recipe_profile(pid, rid)
    return Reply(preview(_camera_model(req, pid)['values'], profile, limits), content_type=TEXT_TYPE)


def api_photo_recipe_export(req, pid, rid):
    from .photo_recipe_export import export_bundle
    _recipe_project(req, pid)
    packet = req.store.photo_recipe_packet(pid, rid)
    body = req.read_json()
    return Reply(export_bundle(packet, body.get("cube"), body.get("grade")),
                 content_type="application/zip", headers=[disposition("darkslide-photo-recipe.zip")])


# Placeholders usable in route paths; everything else in a path is literal.
PATH_PARTS = {
    "<rid>": r"(?P<rid>user_lut_photo_[0-9a-f]{32})",
    "<pid>": r"(?P<pid>p_[0-9a-f]{12})",
    "<bid>": r"(?P<bid>b_[0-9a-f]{12})",
    "<digest>": r"(?P<digest>[0-9a-f]{64})",
    "<n>": r"(?P<n>[0-9]{1,2})",
    "<fid>": r"(?P<fid>[a-z0-9][a-z0-9_-]{0,63})",
    "<lid>": r"(?P<lid>user_lut_[0-9a-f]{12})",
    "<job>": r"(?P<job>[0-9a-f]{16})",
}


def path_pattern(spec):
    """Compile "/api/projects/<pid>/save" into a regex (used with fullmatch)."""
    pieces = re.split(r"(<[a-z]+>)", spec)
    return re.compile("".join(PATH_PARTS[p] if p in PATH_PARTS else re.escape(p) for p in pieces))


ROUTES = tuple((method, path_pattern(spec), func) for method, spec, func in (
    ("POST", "/api/legacy-import", api_legacy_import),
    ("GET", "/api/status", api_status),
    ("POST", "/api/firmware", api_import_firmware),
    ("POST", "/api/quit", api_quit),
    ("POST", "/api/check-name", api_check_name),
    ("POST", "/api/prefs", api_set_pref),
    ("POST", "/api/projects", api_create_project),
    ("GET", "/api/factory/<digest>/shutdown.jpg", api_factory_shutdown),
    ("GET", "/api/factory/<digest>/shutdown/<n>.jpg", api_factory_shutdown),
    ("GET", "/api/factory/<digest>", api_factory),
    ("GET", "/api/factory/<digest>/icons/<n>.png", api_factory_icon),
    ("GET", "/api/builds/<bid>/firmware", api_build_firmware),
    ("GET", "/api/builds/<bid>/manifest", api_build_manifest),
    ("GET", "/api/projects/<pid>", api_get_project),
    ("PATCH", "/api/projects/<pid>", api_rename_project),
    ("PUT", "/api/projects/<pid>/target-body", api_set_target_body),
    ("PUT", "/api/projects/<pid>/menu-language", api_set_menu_language),
    ("PUT", "/api/projects/<pid>/draft", api_put_draft),
    ("POST", "/api/projects/<pid>/delete", api_delete_project),
    ("POST", "/api/projects/<pid>/save", api_save_project),
    ("POST", "/api/projects/<pid>/assets", api_upload_asset),
    ("GET", "/api/projects/<pid>/assets/<digest>", api_get_asset),
    ("POST", "/api/projects/<pid>/generate", api_generate),
    ("POST", "/api/projects/<pid>/iiix-candidate", api_iiix_candidate),
    ("POST", "/api/projects/<pid>/jobs", api_start_job),
    ("GET", "/api/projects/<pid>/jobs", api_list_jobs),
    ("GET", "/api/projects/<pid>/jobs/<job>", api_job_status),
    ("GET", "/api/film-catalog", api_film_catalog),
    ("GET", "/api/film-preview/<fid>.cube", api_film_preview),
    ("GET", "/api/film-icons/<fid>", api_film_icon),
    ("POST", "/api/projects/<pid>/photo-recipes", api_import_photo_recipe),
    ("GET", "/api/projects/<pid>/photo-recipes", api_list_photo_recipes),
    ("GET", "/api/projects/<pid>/photo-recipes/<rid>/camera.cube", api_photo_recipe_camera),
    ("GET", "/api/projects/<pid>/photo-recipes/<rid>/install.cube", api_photo_recipe_install_camera),
    ("GET", "/api/projects/<pid>/film-compatibility", api_film_compatibility),
    ("POST", "/api/projects/<pid>/photo-recipes/<rid>/export", api_photo_recipe_export),
    ("POST", "/api/projects/<pid>/film-luts", api_import_film_lut),
    ("GET", "/api/projects/<pid>/film-luts", api_list_film_luts),
    ("GET", "/api/projects/<pid>/film-luts/<lid>.cube", api_get_film_lut),
    ("POST", "/api/projects/<pid>/film-luts/<lid>/delete", api_delete_film_lut),
    ("POST", "/api/projects/<pid>/film-luts/<lid>/fit", api_fit_film_lut),
    ("GET", "/api/projects/<pid>/film-luts/<lid>/camera.cube", api_film_lut_camera),
    ("GET", "/api/projects/<pid>/film-luts/<lid>/ranges", api_lut_ranges),
    ("POST", "/api/projects/<pid>/film-luts/<lid>/ranges", api_start_lut_ranges),
    ("POST", "/api/projects/<pid>/film-fit-into", api_start_fit_into),
    ("GET", "/api/projects/<pid>/film-fit-into/<job>", api_fit_into_status),
    ("POST", "/api/projects/<pid>/film-bakes", api_bake_grade),
    ("POST", "/api/photo-lut/learn", api_photo_lut_learn),
    ("POST", "/api/photo-lut/bake", api_photo_lut_bake),
    ("POST", "/api/xmp-lut", api_xmp_lut),
    ("GET", "/api/projects/<pid>/film-camera/<fid>.cube", api_film_camera),
    ("GET", "/api/projects/<pid>/film-check", api_film_check),
    ("GET", "/api/projects/<pid>/grade-limits", api_grade_limits),
    ("POST", "/api/projects/<pid>/film-grade-check", api_film_grade_check),
    ("POST", "/api/projects/<pid>/film-prepare", api_film_prepare),
    ("GET", "/api/fitting-setup", api_fitting_setup),
    ("POST", "/api/fitting-setup", api_install_fitting),
))


def find_route(method, path):
    """(func, named groups) for the first ROUTES row whose method matches and whose
    pattern matches the whole path; ApiError 404 when none does."""
    for verb, pattern, func in ROUTES:
        if verb == method:
            match = pattern.fullmatch(path)
            if match:
                return func, match.groupdict()
    raise ApiError(404, "not found")


# --- handler and server -------------------------------------------------------

class RequestHandler(BaseHTTPRequestHandler):
    """Serves one connection of a LocalServer; the application is `self.server.app`."""

    server_version = "Darkslide/" + VERSION
    # A request line without a version (HTTP/0.9 style) is answered as HTTP/1.0,
    # so that reply also gets a status line and headers.
    default_request_version = "HTTP/1.0"
    timeout = IDLE_TIMEOUT

    # Request state, reset by respond() for every request.
    query = ""
    after_reply = None
    _body = None

    @property
    def app(self):
        return self.server.app

    @property
    def store(self):
        return self.server.app.store

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def do_PUT(self):
        self.respond()

    def do_PATCH(self):
        self.respond()

    def parse_request(self):
        accepted = super().parse_request()
        if self.request_version == "HTTP/0.9":  # an explicit "HTTP/0.9" token: still reply with headers
            self.request_version = "HTTP/1.0"
        return accepted

    # -- the fixed order of section 二 --
    def respond(self):
        self.query, self.after_reply, self._body = "", None, None
        try:
            status, headers, data = render(self.route())
        except Exception as exc:  # noqa: BLE001 - mapped by table, others re-raised
            # (a socket timeout is re-raised too; handle_one_request then drops the connection quietly)
            mapped = error_reply(exc)
            if mapped is None:
                raise
            self.after_reply = None
            # messages may quote client text (a slot key, say); escape what UTF-8 cannot carry
            status, headers, data = render(mapped, errors="backslashreplace")
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)
        if self.after_reply is not None:
            self.after_reply()
        self.discard_unread_body()

    def route(self):
        port = self.server.server_address[1]
        hosts = ["%s:%d" % (name, port) for name in LOCAL_HOSTNAMES]
        if self.single_header("Host") not in hosts:
            raise ApiError(403, "forbidden host")
        if self.command != "GET" and self.headers.get_all("Origin") is not None:
            if self.single_header("Origin") not in ["http://" + host for host in hosts]:
                raise ApiError(403, "forbidden origin")
        path, self.query = split_target(self.path)
        if path.startswith("/api/"):
            func, groups = find_route(self.command, path)
            return func(self, **groups)
        if self.command != "GET":
            raise ApiError(405, "method not allowed")
        return static_reply(WEB_ROOT, path)

    def single_header(self, name):
        """The header's value if it appears exactly once, else None."""
        values = self.headers.get_all(name) or []
        return values[0] if len(values) == 1 else None

    # -- request body --
    def read_body(self):
        """The body by Content-Length (b"" when absent or 0); 413 above MAX_BODY.

        A Content-Length that is not a whole number >= 0 raises ValueError."""
        if self._body is None:
            size = int(self.headers.get("Content-Length") or 0)
            if size < 0:
                raise ValueError("negative Content-Length")
            if size > MAX_BODY:
                raise ApiError(413, "文件太大")
            self._body = self.rfile.read(size) if size else b""
        return self._body

    def read_json(self):
        """The body as a JSON object; an empty body counts as {}."""
        try:
            raw = self.read_body()
        except ValueError:  # unusable Content-Length: on JSON routes v0.2 called that malformed
            raise ApiError(400, "请求格式错误") from None
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            value = None
        if not isinstance(value, dict):
            raise ApiError(400, "请求格式错误")
        return value

    def discard_unread_body(self):
        """Read and drop a declared body the route never read (at most DISCARD_LIMIT bytes)."""
        if self._body is not None:
            return
        try:
            left = min(int(self.headers.get("Content-Length") or 0), DISCARD_LIMIT)
        except ValueError:
            return
        try:
            while left > 0:
                chunk = self.rfile.read(min(left, 256 * 1024))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:  # timed out or reset; the connection is closed next anyway
            pass

    # -- reply --
    def send_response(self, code, message=None):
        super().send_response(code, message)
        for name, value in SAFETY_HEADERS:
            self.send_header(name, value)

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        if log.isEnabledFor(logging.DEBUG):
            log.debug("%s %s", self.address_string(), printable(fmt % args))


class LocalServer(ThreadingHTTPServer):
    """Threaded HTTP server for one App, listening on 127.0.0.1 only."""

    daemon_threads = True
    # The default backlog (5) is less than one page load (the browser opens 6 connections at once);
    # while a fit keeps the server busy, the extra connections were reset and the page half-loaded.
    request_queue_size = 64

    def __init__(self, address, app):
        if address[0] != LOOPBACK:
            raise ValueError("DARKSLIDE GR 只监听 %s，不能绑定 %r" % (LOOPBACK, address[0]))
        self.app = app
        super().__init__(address, RequestHandler)

    def stop_soon(self):
        """Stop serve_forever without blocking the caller (shutdown() waits for the loop)."""
        threading.Thread(target=self.shutdown, name="darkslide-stop", daemon=True).start()


def create_server(port, root=None):
    """Bind an App over `root` (default data folder) to 127.0.0.1:port; not yet serving."""
    return LocalServer((LOOPBACK, port), App(root))
