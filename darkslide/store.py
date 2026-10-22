"""Local data: imported inputs, projects (draft + saved versions), assets, builds.

Layout under the data root:
  inputs/<sha256>.bin
  projects/<id>/project.json      saved state (⌘S) and version list
  projects/<id>/draft.json        autosaved working copy
  projects/<id>/versions/<n>.json one file per ⌘S
  projects/<id>/assets/<sha256>.png|.jpg
  projects/<id>/luts/user_lut_<12 hex>.cube   user film LUTs, as uploaded
  projects/<id>/film_luts.json    their names and (once fitted) camera parameters
  builds.json, prefs.json
"""
import copy
import json
import os
import re
import secrets
import struct
import tempfile
import time
from pathlib import Path

from . import models, body_target, film_luts, film_requests, jpegcheck, names, pngcheck, shared_shutdown, template as tpl, menu_language
from .identify import digest_of

PROJECT_ID = re.compile(r"^p_[0-9a-f]{12}$")
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SOURCE_BYTES = 20 * 1024 * 1024
# Shutdown JPEG budget: the shared Diary slot (112,509 B) minus the 4-byte
# minimum COM padding, so any accepted file can be padded to the exact slot size.
SHUTDOWN_BUDGET = shared_shutdown.CAPACITY - 4


class StoreError(ValueError):
    pass


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _keep_blob(target, data, replace=False):
    """Store `data` at its content-addressed `target` once. An existing file with that
    name already holds the same bytes, so it is left alone; a new one is written under
    a temporary name and renamed into place, so a crash never leaves half a file.
    replace=True rewrites a derived file (not content-addressed) the same safe way."""
    if target.exists() and not replace:
        return
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    handle, scratch = tempfile.mkstemp(dir=str(folder), prefix=".incoming-", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(scratch, str(target))
    except OSError:
        if os.path.exists(scratch):
            os.remove(scratch)
        if not target.exists():  # a concurrent writer of the same content may have won the rename
            raise


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def png_size(data):
    if not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        raise StoreError("不是 PNG 文件")
    return struct.unpack(">II", data[16:24])


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # film-list icons ------------------------------------------------------
    # The user's own pictures for the built-in films' tiles: <data>/film_icons/<film id>.png|.jpg|.webp.
    # They live only on this computer; without one a tile shows the drawn name plate.
    ICON_TYPES = ((".png", "image/png", b"\x89PNG\r\n\x1a\n"), (".jpg", "image/jpeg", b"\xff\xd8\xff"),
                  (".jpeg", "image/jpeg", b"\xff\xd8\xff"), (".webp", "image/webp", b"RIFF"))
    MAX_FILM_ICON = 5 * 1024 * 1024

    @property
    def film_icons_dir(self):
        return self.root / "film_icons"

    def film_icon(self, ident):
        """(bytes, content type) of the user's icon for a built-in film, or StoreError."""
        if ident not in {f["id"] for f in film_luts.catalog()["films"]}:
            raise StoreError("无效的胶片编号")
        for ext, mime, magic in self.ICON_TYPES:
            path = self.film_icons_dir / (ident + ext)
            if path.is_file() and path.resolve().parent == self.film_icons_dir.resolve():
                if path.stat().st_size > self.MAX_FILM_ICON:
                    raise StoreError("%s 的图标超过 5 MB" % ident)
                data = path.read_bytes()
                if not data.startswith(magic) or (ext == ".webp" and data[8:12] != b"WEBP"):
                    raise StoreError("%s 的图标不是有效的图片" % ident)
                return data, mime
        raise StoreError("这卷胶片没有自定义图标")

    def film_icon_versions(self):
        """Film id -> a version stamp for every built-in film that has a usable icon file."""
        out = {}
        for film in film_luts.catalog()["films"]:
            try:
                data, _ = self.film_icon(film["id"])
            except StoreError:
                continue
            out[film["id"]] = digest_of(data)[:12]
        return out

    def ensure_film_icons_dir(self):
        """Create the folder with a short note (names to use) so people and AI assistants find it."""
        folder = self.film_icons_dir
        folder.mkdir(parents=True, exist_ok=True)
        note = folder / "README.txt"
        if not note.exists():
            ids = "\n".join("  %s.png    %s" % (f["id"], f["name"]) for f in film_luts.catalog()["films"])
            note.write_text("把胶片列表里的图标换成你自己的图片：\n"
                            "图片放在这个文件夹，按下面的编号命名（.png / .jpg / .webp 都可以），"
                            "建议正方形、160 像素以上、5 MB 以内。刷新 DARKSLIDE GR 页面就能看到。\n"
                            "这些图片只在你的电脑上，不会上传。\n\n" + ids + "\n", encoding="utf-8")
        return folder

    # inputs -------------------------------------------------------------
    def put_input(self, data):
        digest = digest_of(data)
        _keep_blob(self.root / "inputs" / (digest + ".bin"), data)
        return digest

    def input_path(self, digest):
        return self.root / "inputs" / (digest + ".bin")

    def factory_dir(self, digest):
        if not ASSET_ID.match(digest or ""):
            raise StoreError("固件编号无效")
        return self.root / "inputs" / digest

    def has_input(self, digest):
        return (self.root / "inputs" / (digest + ".bin")).exists()

    # projects -----------------------------------------------------------
    def _dir(self, project_id):
        if not PROJECT_ID.match(project_id or ""):
            raise StoreError("工程编号无效")
        path = self.root / "projects" / project_id
        if not (path / "project.json").exists():
            raise StoreError("找不到这个工程")
        return path

    def create_project(self, title, input_sha256, template, target_body=None, camera_variant=None):
        if not self.has_input(input_sha256):
            raise StoreError("请先导入官方固件")
        if camera_variant is not None:
            derived = body_target.variant_body(camera_variant)
            if target_body is not None and target_body != derived:
                raise StoreError("机身型号与目标机身不一致")
            target_body = derived
        target_body = self._target_body(target_body, template)
        project_id = "p_" + secrets.token_hex(6)
        stamp = now()
        project = dict(schema=1, id=project_id, title=(title or "未命名").strip()[:40] or "未命名",
                       input_sha256=input_sha256, template_id=template["id"],
                       created_at=stamp, saved_at=None, changes={}, versions=[])
        if target_body is not None:
            project["target_body"] = target_body
        if camera_variant is not None:
            project["camera_variant"] = camera_variant
        path = self.root / "projects" / project_id
        write_json(path / "project.json", project)
        write_json(path / "draft.json", dict(changes={}, updated_at=stamp))
        return self.get_project(project_id)

    def list_projects(self):
        out = []
        for item in sorted((self.root / "projects").glob("p_*/project.json")):
            value = read_json(item, {})
            draft = read_json(item.parent / "draft.json", {})
            out.append(dict(id=value.get("id"), title=value.get("title"), saved_at=value.get("saved_at"),
                            input_sha256=value.get("input_sha256"), target_body=value.get("target_body"),
                            updated_at=draft.get("updated_at"), changes=len(draft.get("changes", {}))))
        return sorted(out, key=lambda p: p.get("updated_at") or "", reverse=True)

    def delete_project(self, project_id):
        """Remove a project for good: drafts, versions, assets, LUTs, and the firmware files it
        generated (their records too, so nothing is left unreachable). Returns how many builds went."""
        import shutil
        path = self._dir(project_id)
        builds = self.builds()
        mine = [b for b in builds if b.get("project_id") == project_id]
        for b in mine:
            if re.match(r"^b_[0-9a-f]{12}$", b.get("build_id") or ""):
                shutil.rmtree(self.root / "builds" / b["build_id"], ignore_errors=True)
        write_json(self.root / "builds.json", [b for b in builds if b.get("project_id") != project_id])
        shutil.rmtree(path)
        return len(mine)

    def get_project(self, project_id):
        path = self._dir(project_id)
        project = read_json(path / "project.json")
        draft = read_json(path / "draft.json", dict(changes={}, updated_at=None))
        project["draft"] = draft
        project["unsaved"] = draft.get("changes", {}) != project.get("changes", {})
        project["target_body_label"] = body_target.label(project.get("target_body"))
        return project

    def rename_project(self, project_id, title):
        path = self._dir(project_id)
        project = read_json(path / "project.json")
        project["title"] = (title or "").strip()[:40] or project["title"]
        write_json(path / "project.json", project)
        return self.get_project(project_id)

    @staticmethod
    def _target_body(value, template):
        """Explicit ordinary/HDF body of the template's family; old projects keep none."""
        try:
            return body_target.require_family(body_target.validate(value), template.get("model"), template.get("firmware"))
        except body_target.BodyTargetError as exc:
            raise StoreError(str(exc))

    def set_target_body(self, project_id, value, template, camera_variant=None):
        path = self._dir(project_id)
        project = read_json(path / "project.json")
        if camera_variant is not None:
            derived = body_target.variant_body(camera_variant)
            if value is not None and value != derived:
                raise StoreError("机身型号与目标机身不一致")
            value = derived
        body = self._target_body(value, template)
        if camera_variant is not None:
            project["camera_variant"] = camera_variant
        else:
            project.pop("camera_variant", None)
        if body is None:
            project.pop("target_body", None)
        else:
            project["target_body"] = body
        write_json(path / "project.json", project)
        return self.get_project(project_id)

    def set_menu_language(self, project_id, value):
        """The camera menu language the user runs, chosen before generating."""
        try:
            value = menu_language.validate(value)
        except menu_language.MenuLanguageError as exc:
            raise StoreError(str(exc))
        path = self._dir(project_id)
        project = read_json(path / "project.json")
        project["menu_language"] = value
        write_json(path / "project.json", project)
        return self.get_project(project_id)

    def put_draft(self, project_id, changes, template):
        path = self._dir(project_id)
        clean = self.validate_changes(project_id, changes, template, strict=False)
        # The same draft again (the page pushes it when it closes or reloads) leaves the file alone:
        # a 生成 running meanwhile checks the project did not change and would otherwise give up.
        if read_json(path / "draft.json", {}).get("changes") != clean:
            write_json(path / "draft.json", dict(changes=clean, updated_at=now()))
        return self.get_project(project_id)

    def save(self, project_id, template):
        path = self._dir(project_id)
        project = read_json(path / "project.json")
        draft = read_json(path / "draft.json", dict(changes={}))
        clean = self.validate_changes(project_id, draft.get("changes", {}), template, strict=True)
        stamp = now()
        number = len(project.get("versions", [])) + 1
        record = dict(changes=clean, saved_at=stamp)
        if "menu_language" in project:  # a camera setting like target_body; noted with each saved version
            record["menu_language"] = project["menu_language"]
        write_json(path / "versions" / ("%d.json" % number), record)
        project.update(changes=clean, saved_at=stamp)
        project.setdefault("versions", []).append(dict(number=number, saved_at=stamp, changes=len(clean)))
        write_json(path / "project.json", project)
        return self.get_project(project_id)

    # assets -------------------------------------------------------------
    def put_asset(self, project_id, data, expect_icon=False):
        path = self._dir(project_id)
        if len(data) > MAX_SOURCE_BYTES:
            raise StoreError("图片太大（上限 20MB）")
        width, height = png_size(data)
        if expect_icon:
            try:
                pngcheck.check_icon(data)
            except pngcheck.PngError as exc:
                raise StoreError(str(exc))
        digest = digest_of(data)
        _keep_blob(path / "assets" / (digest + ".png"), data)
        return dict(asset=digest, width=width, height=height)

    def put_shutdown_jpeg(self, project_id, data):
        """Accept only a 720x480 baseline 4:2:0 JPEG that fits the shared slot."""
        path = self._dir(project_id)
        if len(data) > SHUTDOWN_BUDGET:
            raise StoreError("关机图超过 %d 字节，请降低质量" % SHUTDOWN_BUDGET)
        try:
            shared_shutdown.jpeg_contract(bytes(data))
            jpegcheck.check(bytes(data))           # every Huffman code, all 1350 blocks, ends at EOI
        except (shared_shutdown.SharedSlotError, jpegcheck.JpegCheckError) as exc:
            raise StoreError("关机图格式不符合相机要求：%s" % exc)
        digest = digest_of(data)
        _keep_blob(path / "assets" / (digest + ".jpg"), data)
        return dict(asset=digest, width=720, height=480, bytes=len(data))

    def asset_path(self, project_id, asset):
        if not ASSET_ID.match(asset or ""):
            raise StoreError("图片编号无效")
        folder = self._dir(project_id) / "assets"
        for ext in (".png", ".jpg"):
            path = folder / (asset + ext)
            if path.exists():
                return path
        raise StoreError("找不到图片")

    # validation ---------------------------------------------------------
    def validate_changes(self, project_id, changes, template, strict):
        """Keep only well-formed edits on open slots. strict=True (⌘S) also
        requires every preset name to pass the rules; drafts may hold
        work-in-progress names."""
        if not isinstance(changes, dict):
            raise StoreError("改动格式错误")
        clean = {}
        for key, edit in changes.items():
            if key == "shutdown":
                clean["shutdown"] = self._clean_shutdown(project_id, edit)
                continue
            try:
                no = int(key)
                slot = tpl.slot(template, no)
            except (ValueError, KeyError):
                raise StoreError("没有这个槽位：%s" % key)
            if not isinstance(edit, dict):
                raise StoreError("槽位 %s 的改动格式错误" % key)
            out = {}
            if "name" in edit:
                if not tpl.can_edit(template, no, "rename"):
                    raise StoreError("%s 的预设名暂不开放" % slot["name"])
                name = edit["name"]
                if not isinstance(name, str):
                    raise StoreError("预设名必须是文字")
                if strict and not names.name_ok(name, slot.get("name_max", len(slot["factory_en"]))):
                    raise StoreError("%s 的预设名没有通过检查" % slot["name"])
                out["name"] = name
            if "icon" in edit:
                if not tpl.can_edit(template, no, "icon"):
                    raise StoreError("%s 的图标暂不开放" % slot["name"])
                icon = edit["icon"]
                if not isinstance(icon, dict) or icon.get("style") not in ("image", "factory"):
                    raise StoreError("%s 的图标格式错误" % slot["name"])
                self.asset_path(project_id, icon.get("asset"))
                if icon.get("source"):
                    self.asset_path(project_id, icon["source"])
                if "film" in icon and (icon.get("style") != "image" or icon["film"] not in
                                       {f["id"] for f in film_luts.catalog()["films"]}):
                    raise StoreError("%s 的图标格式错误" % slot["name"])
                keep = ("asset", "source", "style", "crop", "text", "film")
                out["icon"] = {k: icon[k] for k in keep if k in icon}
            if "grain" in edit:
                if tpl.grain_stage(template, no) != "menu":
                    raise StoreError("%s 的颗粒项不能由本工具开放" % slot["name"])
                if edit["grain"] is not True:
                    raise StoreError("%s 的颗粒项改动格式错误" % slot["name"])
                out["grain"] = True
            if "film" in edit:
                film = edit["film"]
                try:
                    film_requests.validate_request({"kind": "film", "slot": no, "film": film})
                except film_requests.FilmRequestError as exc:
                    raise StoreError("%s：%s" % (slot["name"], exc)) from None
                if strict and not self._film_known(project_id, film):
                    raise StoreError("%s 选的胶片没有可用数据：%s" % (slot["name"], film))
                out["film"] = film
            if "grade" in edit:
                out["grade"] = self._clean_grade(slot["name"], edit["grade"])
            if out:
                clean[str(no)] = out
        return clean

    GRADE_SCALARS = ("temp", "tint", "exp", "con", "hi", "sh", "white", "black", "fade",
                     "sat", "splitHi", "splitSh", "vib")

    @classmethod
    def _clean_grade(cls, name, grade):
        """Adjustments from the 调整 column: sliders -100..100, HSL as eight values,
        curves as 2-16 rising points from x=0 to x=1. Only shapes previews and .cube."""
        bad = StoreError("%s 的调色格式错误" % name)
        if not isinstance(grade, dict) or len(json.dumps(grade)) > 4096:
            raise bad
        ok_num = lambda v: isinstance(v, int) and not isinstance(v, bool) and -100 <= v <= 100
        out = {}
        for key, value in grade.items():
            if key in cls.GRADE_SCALARS:
                if not ok_num(value):
                    raise bad
            elif key in ("hH", "hS", "hL"):
                if not (isinstance(value, list) and len(value) == 8 and all(map(ok_num, value))):
                    raise bad
            elif key == "curve":
                if not isinstance(value, dict) or not set(value) <= {"all", "r", "g", "b"}:
                    raise bad
                for pts in value.values():
                    if not (isinstance(pts, list) and 2 <= len(pts) <= 16):
                        raise bad
                    for p in pts:
                        if not (isinstance(p, list) and len(p) == 2 and all(
                                isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in p)):
                            raise bad
                    xs = [p[0] for p in pts]
                    if xs[0] != 0 or xs[-1] != 1 or any(b <= a for a, b in zip(xs, xs[1:])):
                        raise bad
            else:
                raise bad
            out[key] = value
        return out

    def _film_known(self, project_id, film):
        from .photo_recipe import RESTORED_ID
        if isinstance(film,str) and RESTORED_ID.fullmatch(film):
            self.photo_recipe_profile(project_id,film)
            return True
        if film_luts.USER_ID.match(film):
            return film in self.film_luts(project_id)
        return film in {f["id"] for f in film_luts.catalog()["films"]}

    def put_photo_recipe(self, project_id, packet):
        """Keep exact snapshot bytes; a photo restore never fabricates a LUT."""
        from . import models, photo_recipe
        project = self.get_project(project_id)
        if project['template_id'] != models.GR3X.id or project['input_sha256'] != models.GR3X.input_sha:
            raise StoreError('照片配方需要 GR IIIx 1.60 工程')
        try:
            profile = photo_recipe.restored_profile(packet)
        except ValueError as error:
            raise StoreError(str(error)) from error
        target = self._dir(project_id) / 'photo_recipes' / (profile['id']+'.dsfr')
        if target.exists() and target.read_bytes() != packet:
            raise StoreError('已存的照片配方身份冲突')
        _keep_blob(target, packet)
        return profile

    def photo_recipe_packet(self, project_id, ident):
        """Read and validate one immutable snapshot for both profile use and export."""
        from . import models, photo_recipe
        if type(ident) is not str or not photo_recipe.RESTORED_ID.fullmatch(ident):
            raise StoreError('照片配方编号无效')
        project = self.get_project(project_id)
        if project['template_id'] != models.GR3X.id or project['input_sha256'] != models.GR3X.input_sha:
            raise StoreError('照片配方机型不匹配')
        path = self._dir(project_id) / 'photo_recipes' / (ident+'.dsfr')
        try:
            if path.stat().st_size > photo_recipe.MAX_PACKET:
                raise ValueError('照片配方文件过大')
            packet = path.read_bytes()
            profile = photo_recipe.restored_profile(packet)
            if profile['id'] != ident:
                raise ValueError('照片配方内容与编号不一致')
            return packet
        except (ValueError, OSError) as error:
            raise StoreError('无法读取工程的照片配方：'+str(error)) from error

    def photo_recipe_profile(self, project_id, ident):
        from . import photo_recipe
        return photo_recipe.restored_profile(self.photo_recipe_packet(project_id, ident))

    def list_photo_recipes(self, project_id):
        # Revalidate content on every listing; do not trust a separate index.
        folder = self._dir(project_id) / 'photo_recipes'
        return [self.photo_recipe_profile(project_id, path.stem)
                for path in sorted(folder.glob('*.dsfr'))]

    # user film LUTs -----------------------------------------------------
    def film_luts(self, project_id):
        index = read_json(self._dir(project_id) / "film_luts.json", {})
        if type(index) is not dict:
            raise StoreError("LUT 索引格式损坏")
        for entry in index.values():
            if type(entry) is not dict or any(entry.get(k) is not None and type(entry[k]) is not dict
                                             for k in ('profile', 'fit', 'bake')):
                raise StoreError("LUT 来源记录格式损坏")
            profile, report = entry.get('profile') or {}, entry.get('fit') or {}
            if (type(profile.get('provenance', {})) is not dict or
                    type(report.get('envelope', {})) is not dict):
                raise StoreError("LUT 拟合来源格式损坏")
        return index

    def put_film_lut(self, project_id, data, filename, bake=None):
        """Keep an uploaded .cube with the project; same bytes give the same id.
        bake = {"film": id or None, "grade": {...}} marks a LUT made from a preset's
        调色 (film + adjustments) rather than a file the user imported."""
        try:
            title, size, _ = film_luts.parse_cube(data)
        except film_luts.FilmLutError as exc:
            raise StoreError(str(exc)) from None
        ident = film_luts.lut_id(data)
        path = self._dir(project_id)
        index = self.film_luts(project_id)
        if ident not in index:
            _keep_blob(path / "luts" / (ident + ".cube"), data)
            index[ident] = dict(name=film_luts.display_name(filename, title), size=size,
                                sha256=digest_of(data), imported_at=now(), profile=None, fit=None)
        if bake is not None and index[ident].get("bake") != bake:
            index[ident]["bake"] = bake
            if models.for_project(self.get_project(project_id)) == models.GR3X:
                # Identical cube bytes can come from different films/grades.
                # A failed re-fit must not label an old profile as the new source.
                index[ident].update(profile=None, fit=None)
        write_json(path / "film_luts.json", index)
        return self._lut_summary(ident, index[ident])

    def list_film_luts(self, project_id):
        return [self._lut_summary(k, v) for k, v in sorted(self.film_luts(project_id).items(),
                                                            key=lambda kv: kv[1].get("imported_at") or "")]

    @staticmethod
    def _lut_summary(ident, entry):
        fitted = entry.get('profile') is not None
        reason = None
        if fitted and entry['profile'].get('model') == models.GR3X.name and entry.get('bake') is not None:
            from .gr3x_recipe_lineage import verify
            try:verify(entry['profile'], entry['bake'])
            except ValueError as error:fitted, reason = False, str(error)
        return dict(id=ident, name=entry["name"], size=entry["size"], sha256=entry["sha256"],
                    imported_at=entry["imported_at"], fitted=fitted,
                    fit=entry.get("fit") if fitted else None, bake=entry.get("bake"), check=entry.get("check"),
                    needs_refit_reason=reason)

    def set_film_lut_fit(self, project_id, ident, profile, report, camera_cube):
        """Store the fitted camera parameters, the fit report and a camera-model preview."""
        self.film_lut_path(project_id, ident)
        path = self._dir(project_id)
        index = self.film_luts(project_id)
        _keep_blob(path / "luts" / (ident + ".camera.cube"), camera_cube, replace=True)
        index[ident].update(profile=profile, fit=report)
        index[ident].pop("check", None)
        write_json(path / "film_luts.json", index)
        return self._lut_summary(ident, index[ident])

    def set_film_lut_check(self, project_id, ident, check):
        """Remember that this LUT was checked and cannot go into the camera (nothing fitted is kept)."""
        self.film_lut_path(project_id, ident)
        index = self.film_luts(project_id)
        index[ident].update(check=check, profile=None, fit=None)
        write_json(self._dir(project_id) / "film_luts.json", index)
        (self._dir(project_id) / "luts" / (ident + ".camera.cube")).unlink(missing_ok=True)

    def delete_film_lut(self, project_id, ident):
        """Forget an imported LUT and its files. Refused while a preset (draft or
        saved project) still uses it; built-in films are not in this index at all."""
        self.film_lut_path(project_id, ident)
        path = self._dir(project_id)
        project = read_json(path / "project.json", {})
        draft = read_json(path / "draft.json", dict(changes={}))
        for changes in (draft.get("changes", {}), project.get("changes", {})):
            for key, edit in changes.items():
                if isinstance(edit, dict) and edit.get("film") == ident:
                    raise StoreError("第 %s 号预设正在用这个 LUT，先换成别的胶片再删" % key)
        index = self.film_luts(project_id)
        del index[ident]
        write_json(path / "film_luts.json", index)
        for suffix in (".cube", ".camera.cube", ".ranges.json"):
            (path / "luts" / (ident + suffix)).unlink(missing_ok=True)
        return self.list_film_luts(project_id)

    def film_lut_ranges(self, project_id, ident):
        """A user LUT's computed slider ranges (lut_ranges), or None."""
        return read_json(self.film_lut_path(project_id, ident).with_suffix(".ranges.json"), None)

    def set_film_lut_ranges(self, project_id, ident, record):
        write_json(self.film_lut_path(project_id, ident).with_suffix(".ranges.json"), record)

    def film_lut_camera_path(self, project_id, ident):
        path = self.film_lut_path(project_id, ident).with_suffix(".camera.cube")
        if not path.exists():
            raise StoreError("这个 LUT 还没有拟合")
        return path

    @staticmethod
    def grade_key(film, grade):
        return json.dumps({"film": film or None, "grade": grade or {}}, sort_keys=True, ensure_ascii=False)

    def bake_for(self, project_id, film, grade):
        """The fitted LUT made from exactly this film + grade, or None."""
        want = self.grade_key(film, grade)
        for ident, entry in self.film_luts(project_id).items():
            bake = entry.get("bake")
            if bake and self._lut_summary(ident, entry)['fitted'] and self.grade_key(bake.get("film"), bake.get("grade")) == want:
                return ident
        return None

    def film_changes_for_generate(self, project_id, changes):
        """Changes with each 8-11 preset that has a 调色 pointed at its fitted bake.
        Raises when a 调色 on those presets has not been fitted for its current values."""
        out = dict(changes)
        for key, edit in changes.items():
            if key == "shutdown" or not isinstance(edit, dict) or not edit.get("grade"):
                continue
            if key not in ("8", "9", "10", "11"):
                continue
            ident = self.bake_for(project_id, edit.get("film"), edit["grade"])
            if ident is None:
                raise StoreError("槽位 %s 的调色还没有拟合进相机（或拟合后又改过），请先点「拟合」" % key)
            out[key] = dict(edit, film=ident)
        return out

    def film_lut_path(self, project_id, ident):
        if not film_luts.USER_ID.match(ident or "") or ident not in self.film_luts(project_id):
            raise StoreError("找不到这个 LUT")
        return self._dir(project_id) / "luts" / (ident + ".cube")

    def film_lut_profiles(self, project_id, ids):
        """Camera parameters of the selected user LUTs, for generation."""
        from . import models
        project_sha = self.get_project(project_id).get("input_sha256")
        try:model = models.from_sha(project_sha) if ids else models.GR3
        except ValueError as exc:raise StoreError(str(exc)) from exc
        from .photo_recipe import RESTORED_ID
        index, out = self.film_luts(project_id), {}
        for ident in ids:
            if RESTORED_ID.fullmatch(ident):
                out[ident] = self.photo_recipe_profile(project_id,ident)
                continue
            entry = index.get(ident)
            if entry is None:
                raise StoreError("工程里没有这个 LUT：%s" % ident)
            if entry.get("profile") is None:
                raise StoreError("「%s」还没有拟合成相机数据，不能写入" % entry["name"])
            bad = ((entry.get("fit") or {}).get("envelope") or {}).get("violations")
            if bad:
                raise StoreError("「%s」超出了理光相机原厂的范围，不能写进相机：%s" % (entry["name"], bad[0]))
            # Re-read the original uploaded cube and bind the persisted fit to this project.
            from . import film_profiles, fwread
            try:
                cube = self.film_lut_path(project_id, ident).read_bytes()
                sha = digest_of(cube)
                profile = film_profiles.validate_profile(entry['profile'], ident, model.id)
                provenance = profile.get('provenance') or {}
                report = entry.get('fit') or {}
                project_sha = self.get_project(project_id)['input_sha256']
                if (film_luts.lut_id(cube) != ident or entry.get('sha256') != sha or
                        provenance.get('kind') != 'user_lut' or
                        provenance.get('source_sha256') != sha or
                        provenance.get('official_sha256') != project_sha or
                        project_sha != model.input_sha or
                        provenance.get('fitter') != ('darkslide-film-fit-v2-iiix-knots33' if model == models.GR3X else 'darkslide-film-fit-v2') or
                        report.get('fitter') != provenance.get('fitter') or
                        report.get('base_model_id') != provenance.get('base_model_id') or
                        provenance.get('base_model_id') not in (('gr3x-160-upstream-neutral-stubbed-v2',) if model == models.GR3X else (
                            'gr3-210-upstream-neutral-stubbed-v2', 'gr3-210-python-standard-p64-zero-v1')) or
                        report.get('layout') != 'four_curve_v1' or
                        provenance.get('target_input_encoding') != 'srgb' or
                        provenance.get('target_ev') != 0 or provenance.get('mid_index') != 7.8 or
                        not re.fullmatch(r'[0-9a-f]{64}', str(provenance.get('base_values_sha256', '')))):
                    raise ValueError('来源字段不匹配')
                film_luts.parse_cube(cube)
                if model == models.GR3X and entry.get('bake') is not None:
                    from .gr3x_recipe_lineage import verify
                    verify(profile, entry['bake'])
                json.dumps(entry, allow_nan=False)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise StoreError('LUT 来源或拟合记录核对失败：' + str(exc)) from exc
            out[ident] = copy.deepcopy(profile)
        return out

    def _clean_shutdown(self, project_id, edit):
        if not isinstance(edit, dict):
            raise StoreError("关机图改动格式错误")
        jpeg = self.asset_path(project_id, edit.get("asset"))
        if jpeg.suffix != ".jpg":
            raise StoreError("关机图必须是已压缩的 JPEG")
        if edit.get("source"):
            self.asset_path(project_id, edit["source"])
        keep = ("asset", "source", "quality", "bytes")
        return {k: edit[k] for k in keep if k in edit}

    # builds and prefs ----------------------------------------------------
    def builds(self):
        return read_json(self.root / "builds.json", [])

    def put_build(self, output, manifest):
        """Store a generated file that already passed every check."""
        model = models.from_sha(manifest.get('input_sha256'))
        filename = manifest.get('output_filename', model.filename)
        if filename != model.filename:
            raise StoreError('输出文件名与机型不一致')
        if (not manifest.get('checks') or not all(c.get('ok') is True for c in manifest['checks']) or
                digest_of(output) != manifest.get('output_sha256')):
            raise StoreError('输出与验证记录不一致')
        build_id = "b_" + secrets.token_hex(6)
        folder = self.root / "builds" / build_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / filename).write_bytes(output)
        manifest = dict(manifest, build_id=build_id)
        write_json(folder / "manifest.json", manifest)
        builds = self.builds()
        builds.append(dict(build_id=build_id, project_id=manifest["project_id"],
                           output_sha256=manifest["output_sha256"], created_at=manifest["created_at"]))
        write_json(self.root / "builds.json", builds)
        return manifest

    def build_path(self, build_id, name):
        if not re.match(r"^b_[0-9a-f]{12}$", build_id or "") or name not in ("fwdc239b.bin", "fwdc243b.bin", "manifest.json"):
            raise StoreError("生成记录无效")
        path = self.root / "builds" / build_id / name
        if not path.exists():
            raise StoreError("找不到生成记录")
        return path

    def prefs(self):
        return read_json(self.root / "prefs.json", {})

    def set_pref(self, key, value):
        prefs = self.prefs()
        prefs[key] = value
        write_json(self.root / "prefs.json", prefs)
        return prefs
