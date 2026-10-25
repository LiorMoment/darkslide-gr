"""Glue between a saved project and the builder + independent check.

Order: saved project → edits → build (generation core, in memory) → verify
(independent read-back) → only if every check passes, the caller stores the file.
"""
import time
import copy
import hashlib
import json

from . import body_target, camera_features, composite_verify, compositor, fwread, menu_order, names, pngcheck, rescue_gate, film_requests, template as tpl, menu_language


# Display titles for the composite checker's ids (presentation only).
CHECK_TITLES = {
    "input": "输入是官方 GR III 2.10 原件",
    "output_sha": "输出文件 SHA 与生成记录一致",
    "header_version": "文件头 128 字节与原件相同（版本保持 2.10）",
    "metadata": "元数据与原件相同",
    "footer_identity": "文件尾固定字段与原件相同",
    "container_bounds_padding": "压缩流边界、结束码与对齐正确",
    "file_sum": "整文件 32 位加和为 0",
    "independent_unpack": "独立解包生成的文件",
    "payload_sum": "解码数据加和为 0",
    "icon_name_expected": "按请求独立推导图标和预设名的预期内容",
    "a_path_five_requests_fixed_slot_jpeg": "关机图：五个入口指向同一位置、A 盘路径、图片只多出填充段",
    "entire_payload_matches_independent_request": "整份解码数据与独立推导的预期逐字节一致（没有任何越界改动）",
    "payload_sha": "解码数据 SHA 与生成记录一致",
    "readback_icons_names": "读回 14 个预设的图标和预设名（英文、简中、繁中、日文）",
    "grain_menu_expected": "负片颗粒菜单项、原九项和相邻字符串独立核对",
    "menu_order_tables": "影像控制菜单顺序：三组拍照表、三组视频表和 RAW 显影选项表与所选顺序一致（独立核对）",
    "soft_glow_windows": "柔光 v22.6：两个柔光模块、图像管线挂钩和 ADJ 图标与交付的测试固件逐字节一致（独立核对）",
    "film_requested_data": "胶片记录、矩阵和完整曲线与所选数据一致",
    "film_code_fields_and_targets": "胶片跳转、选择和后矩阵指令独立核对",
    "film_unselected_and_original_protected": "未选胶片槽位和受保护原厂数据保持不变",
    "body_protected_windows": "所选机身的 HDF 保护窗原字保持（有界核对，不代表兼容）",
    "film_closed_failure": "胶片独立读回出错",
    "closed_failure": "核对过程出错",
}


def titled(checks):
    return [dict(c, title=CHECK_TITLES.get(c["id"], c.get("title", c["id"]))) for c in checks]


class GenerateError(ValueError):
    def __init__(self, message, checks=None):
        super().__init__(message)
        self.checks = checks or []


def slot_keys(changes):
    return sorted((k for k in changes if k != "shutdown"), key=int)


def edits_from_changes(store, project_id, changes, template, *, include_films=False, asset_snapshot=None,
                       menu_lang=None, order=None, features=None):
    edits = []
    for key in slot_keys(changes):
        slot = tpl.slot(template, int(key))
        change = changes[key]
        if "icon" in change:
            asset = change["icon"]["asset"]
            png = asset_snapshot[asset] if asset_snapshot is not None else store.asset_path(project_id, asset).read_bytes()
            pngcheck.check_icon(png)
            _, _, rows = pngcheck.rgba_rows(png)
            edits.append(dict(kind="icon", slot=slot["no"], icon_id=slot["icon_id"], pixels=b"".join(rows)))
        if "name" in change:
            if not names.name_ok(change["name"], slot.get("name_max", len(slot["factory_en"]))):
                raise GenerateError("%s 的预设名没有通过检查" % slot["name"])
            edit = dict(kind="name", slot=slot["no"], string_id=slot["string_id"], text=change["name"])
            if menu_lang is not None:
                edit["menu_language"] = menu_lang
            edits.append(edit)
        if change.get("grain") is True:
            if tpl.grain_stage(template, slot["no"]) != "menu":
                raise GenerateError("%s 的颗粒项不能由本工具开放" % slot["name"])
            edits.append(dict(kind="grain_menu", slot=slot["no"], mode=slot["mode"]))
    try:
        films=film_requests.from_changes(changes)
    except film_requests.FilmRequestError as exc:
        raise GenerateError(str(exc)) from exc
    if include_films or film_requests.available():edits.extend(films)
    try:
        order = menu_order.normalize(order)
    except menu_order.MenuOrderError as exc:
        raise GenerateError("菜单顺序格式错误：%s" % exc) from exc
    if order is not None:
        if not menu_order.supported(template):
            raise GenerateError(menu_order.refusal(template))
        edits.append(menu_order.edit_for(order))
    edits.extend(camera_features.edits(features))
    return edits


def summarize(changes, template, *, include_films=False, order=None, features=None):
    out = []
    if (features or {}).get("soft_glow"):
        out.append(dict(slot=None, preset="柔光", what="整台相机", value="打开（v22.6）：在 ADJ 里开关柔光"))
    if order is not None:
        out.append(dict(slot=None, preset="影像控制菜单", what="菜单顺序",
                        value="%s，自定义 1、2 在最后；视频列表跟着照片顺序（原本就没有柔和单色和 HDR 色调）" %
                        "、".join(menu_order.names_in_order(order, template, changes))))
    if "shutdown" in changes:
        size = changes["shutdown"].get("bytes")
        out.append(dict(slot=None, preset="关机画面", what="关机图",
                        value="自定义图片" + ("（%s 字节）" % size if size else "")))
    for key in slot_keys(changes):
        slot = tpl.slot(template, int(key))
        icon = changes[key].get("icon")
        if icon:
            value = "原厂风格 " + icon.get("text", "") if icon.get("style") == "factory" else "自定义图片"
            out.append(dict(slot=slot["no"], preset=slot["name"], what="图标", value=value))
        if "name" in changes[key]:
            out.append(dict(slot=slot["no"], preset=slot["name"], what="预设名", value=changes[key]["name"]))
        if changes[key].get("grain") is True:
            out.append(dict(slot=slot["no"], preset=slot["name"], what="颗粒项", value="开放（档位在相机详细设置里调）"))
        if "film" in changes[key] and (include_films or film_requests.available()):
            out.append(dict(slot=slot["no"], preset=slot["name"], what="胶片", value=changes[key]["film"]))
    return out


# 柔光 v22.6: what has and has not been on a camera (both generation routes add it).
SOFT_GLOW_NOTE = ("柔光用的是 v22.6：柔光效果和用户在普通 GR III 上实测没问题的 v22.4 逐字节相同；"
                  "v22.6 新画的 ADJ 柔光图标、柔光和其他改动一起装、GR IIIx 和 HDF 机身上的柔光都还没实测。")


# Presets whose icon / name changes have been flashed and checked on a GR III so far.
DEVICE_TESTED = {"icon": set(range(1, 13)), "name": set(range(2, 13))}


def body_check(official_bin, output, body, template, has_shutdown):
    """Fourth layer, only for a declared body: protected HDF windows keep their
    original words, plus which CollaboMode shutdown routes the image covers."""
    if body is None:
        return [], dict(target_body=None, label=body_target.UNSPECIFIED)
    try:
        report = body_target.build_report(bytes(fwread.unpack(official_bin)), body, bytes(fwread.unpack(output)))
        shutdown = body_target.shutdown_report(body) if has_shutdown else None
    except (body_target.BodyTargetError, ValueError) as exc:
        raise GenerateError("机身保护窗核对出错：%s" % exc) from exc
    ok = report["status"] == "protected_windows_unchanged"
    checks = titled([dict(id="body_protected_windows", ok=ok, detail=body_target.summary(report))])
    info = dict(target_body=body, label=body, family=report["family"], firmware=report["firmware"],
                filter_type=report["filter_type"], status=report["status"],
                changed_windows=report["changed_windows"], protected_windows=len(report["protected_windows"]),
                body_detected=False, compatibility_approved=False, device_tested=False,
                limitations=report["limitations"])
    if shutdown is not None:
        info["shutdown_routes"] = dict(status=shutdown["status"], missing_requests=shutdown["missing_requests"],
                                       collabo_mode_observed=None, camera_verified=False)
    return checks, info


def verification_note(edits, template, body=None, has_shutdown=False):
    """Say which parts of this build were tested on a camera, without stretching it to newly opened presets."""
    untested = sorted({e["slot"] for e in edits if e["kind"] in DEVICE_TESTED and e["slot"] not in DEVICE_TESTED[e["kind"]]})
    note = "离线核对通过。开放的 12 个预设的图标和预设名（含加长到上限的预设名）已在一台 GR III 上一起刷入实测；关机画面、负片颗粒单独改和与图标、预设名一起改也已实测；你填的具体内容未经实测。"
    if untested:
        names = "、".join(tpl.slot(template, no)["name"] for no in untested)
        note += "这次改到的 " + names + " 是新开放的预设，还没在相机上实测：刷后请看影像控制菜单、拍摄画面、回放详情，以及其他菜单里有没有意外出现你改的名称或图标。"
    if any(e["kind"] == "soft_glow" for e in edits):
        note += SOFT_GLOW_NOTE
    if any(e["kind"] == "menu_order" for e in edits):
        note += "影像控制菜单的新顺序在普通 GR III 上已实测，其他机身还没有：刷后请看拍照、视频、RAW 显影里的列表顺序，选中后拍出的风格和名称是否一致，关机重开和 U1–U3 里存的风格有没有变。"
    if any(e["kind"] == "film" for e in edits):
        note += "用户已反馈普通 GR III 的 Gold 200、Portra 400、Ektar 100、5219 四卷候选实测正常，测试项目未逐项记录；不代表本次配置或其他机型均已实测，离线读回不能证明实际成像效果。"
    if body_target.is_hdf(body):
        note += "以上实测都在普通版 GR III 上做的，%s 机身还没有实测；离线只核对了已知的 HDF 路径没被改到。" % body
    if body is not None and has_shutdown:
        note += "关机图已覆盖普通、Street、Urban、ING、Diary 五个已知入口，不改机身模式、不扩容。新增 Street 路线尚未实机验证；机身当前模式仍未读取。"
    return note + "刷机风险自负，刷机前请备好官方 2.10 救机卡。"


def run(store, project, template, official_bin, *, film_profiles=None):
    """Icons, preset names and the shutdown image in one build: the composite
    core applies everything to one official payload and packs once; the
    composite checker re-derives the expected payload and reads it all back."""
    if film_requests.needs_closed_output(project.get("changes") or {}):
        raise GenerateError("胶片或四位置调色的完整输出尚未开放，请使用只读核对")
    return _run(store, project, template, official_bin, film_profiles=film_profiles)


def _run(store, project, template, official_bin, *, film_profiles=None, inplace=False, asset_snapshot=None):
    if project.get("unsaved"):
        raise GenerateError("有未保存的改动，请先保存（⌘S）")
    changes = project.get("changes") or {}
    # A saved order this version cannot use is never replaced by the factory order behind the user's back.
    unreadable = project.get("menu_order_unreadable") or (project.get("draft") or {}).get("menu_order_unreadable")
    if unreadable:
        raise GenerateError("已存的菜单顺序无法读取（%s），没有生成：请在「菜单顺序」里重新排好或恢复原厂顺序并保存" % unreadable)
    try:
        order = menu_order.normalize(project.get("menu_order"))
    except menu_order.MenuOrderError as exc:
        raise GenerateError("菜单顺序格式错误：%s" % exc) from exc
    if order is not None and not menu_order.supported(template):
        raise GenerateError(menu_order.refusal(template))
    # 柔光 / 65:24 are not in the firmware generation yet: a project with one on is refused, never left out.
    pending = camera_features.refusal(project.get("camera_features"))
    if pending:
        raise GenerateError(pending)
    camera_on = project.get("camera_features") or {}
    if not changes and order is None and not camera_features.edits(camera_on):
        raise GenerateError("还没有改动")
    # The page only enables 生成 once a menu language is chosen; a project without one
    # (older projects, direct API callers) keeps the 1.0.0 behaviour (Simplified Chinese).
    menu_lang = project.get("menu_language")
    if menu_lang is not None:
        try:
            menu_language.validate(menu_lang)
        except menu_language.MenuLanguageError as exc:
            raise GenerateError(str(exc))
    try:
        body = body_target.require_family(body_target.of(project), template.get("model"), template.get("firmware"))
    except body_target.BodyTargetError as exc:
        raise GenerateError(str(exc)) from exc
    # Cloud supplies already-fitted camera data. Freeze the handoff once so
    # writing and independent readback observe the same request inputs.
    profiles_snapshot=copy.deepcopy(film_profiles)
    edits = edits_from_changes(store, project["id"], changes, template, include_films=inplace, asset_snapshot=asset_snapshot,
                               menu_lang=menu_lang, order=order, features=camera_on)
    custom_hashes={}
    if profiles_snapshot is not None and (inplace or film_requests.available()):
        try:
            for edit in edits:
                if edit["kind"]=="film" and edit["film"].startswith("user_lut_"):
                    raw=json.dumps(profiles_snapshot[edit["film"]],sort_keys=True,ensure_ascii=False,
                                   allow_nan=False,separators=(",",":")).encode("utf-8")
                    custom_hashes[edit["film"]]=hashlib.sha256(raw).hexdigest()
        except (TypeError,ValueError,KeyError) as error:
            raise GenerateError("用户 LUT 拟合参数无法冻结或记录来源") from error
    pending_films=film_requests.from_changes(changes)
    skipped=[] if (inplace or film_requests.available()) else [
        dict(edit,reason="胶片写入已接通，等实机测试后开启，暂不参与生成") for edit in pending_films]
    jpeg = None
    if "shutdown" in changes:
        asset = changes["shutdown"]["asset"]
        jpeg = asset_snapshot[asset] if asset_snapshot is not None else store.asset_path(project["id"], asset).read_bytes()
    if not edits and jpeg is None and skipped:
        raise GenerateError("胶片写入尚未开启，当前选择仅用于近似预览")
    # The writer and the independent check both use the layout named here, never one
    # read back from the result.
    layout = ('inplace_history_cache_exit_v29' if inplace else film_requests.FILM_LAYOUT) if any(e["kind"] == "film" for e in edits) else "legacy_arena_v1"
    try:
        if inplace:
            result = compositor.build_inplace_composite(official_bin, edits, template, jpeg,
                                                        film_profiles=profiles_snapshot)
        else:
            result = compositor.build_composite(official_bin, edits, template, jpeg,
                                                film_profiles=profiles_snapshot, film_layout=layout)
    except compositor.CompositeError as exc:
        raise GenerateError("无法生成：%s" % exc)
    checks = titled(composite_verify.verify(official_bin, result, edits, template, jpeg,
                                            film_profiles=profiles_snapshot, film_layout=layout))
    if not composite_verify.all_ok(checks):
        raise GenerateError("独立核对没有全部通过，不输出文件", checks)
    # Third layer, blind to the request: can the official 2.10 package still rescue the camera?
    rescue = rescue_gate.gate(official_bin, result["output"])
    checks += rescue
    if not rescue_gate.all_ok(rescue):
        raise GenerateError("救机门禁没有通过，不输出文件", checks)
    body_checks, body_info = body_check(official_bin, result["output"], body, template, jpeg is not None)
    checks += body_checks
    if not all(c["ok"] for c in body_checks):
        raise GenerateError("改动碰到了 %s 受保护的路径，需要专项复核，不输出文件" % body, checks)
    features = list(result.get("features") or [])
    manifest = dict(
        schema=1, film_layout=layout, decoded_growth_bytes=0, production_approved=False,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        project_id=project["id"], project_title=project["title"],
        project_version=len(project.get("versions", [])),
        template_id=template["id"], model=template["model"], firmware=template["firmware"],
        input_sha256=result["input_sha256"], output_sha256=result["output_sha256"],
        output_filename="fwdc239b.bin",
        payload_sha256_before=result["payload_sha256_before"],
        payload_sha256_after=result["payload_sha256_after"],
        version_word=result["version_word"], summary=summarize(changes, template, include_films=inplace, order=order, features=camera_on),
        menu_language=menu_lang,
        features=features, payload_changes=result["payload_changes"],
        container=result["container"], builder=result["builder"], checks=checks,
        skipped=skipped,
        rescue=dict(rescue_gate.RESCUE_NOTE),
        body=body_info,
        verification=verification_note(edits, template, body, jpeg is not None),
    )
    if jpeg is not None:
        manifest["feature"] = "shutdown" if features == ["shutdown"] else "composite"
        manifest["shutdown"] = dict(jpeg_bytes=len(jpeg), slot_bytes=result["shared_capacity"],
                                    requests=result["request_ids"], path=result["shared_path"])
    if order is not None:
        manifest["menu_order"] = dict(order, video_policy=menu_order.VIDEO_POLICY)
    if custom_hashes:
        manifest["custom_film_profiles_sha256"]=custom_hashes
    return result["output"], manifest


def run_inplace(store, project, template, official_bin):
    """Internal saved-project v29 path, behind the dedicated HTTP release gate.

    Resolves persisted bakes and user-LUT provenance through the existing Store
    contract. It returns only after generation, independent readback, rescue and
    body checks, followed by a second source/saved-state check. Does not store a
    build or open the output gate. Calling it on real inputs needs authorization.
    """
    from . import film_profiles as profile_data, models
    saved = copy.deepcopy(store.get_project(project['id']))
    template = copy.deepcopy(template)
    if saved != project or saved.get('unsaved'):
        raise GenerateError('工程未保存或已经改变')
    if (template.get('reviewed') is not True or template.get('model') != 'GR III' or
            template.get('firmware') != '2.10' or saved.get('template_id') != template.get('id') or
            saved.get('input_sha256') != models.GR3.input_sha):
        raise GenerateError('不扩容完整接线仅适用于 GR III 2.10 已核对模板')
    try:
        body = body_target.require_family(body_target.of(saved), 'GR III', '2.10')
        if body is None:
            raise GenerateError('请先选择机身版本')
        source = store.input_path(saved['input_sha256'])
        if (type(official_bin) is not bytes or len(official_bin) != models.GR3.input_size or
                hashlib.sha256(official_bin).hexdigest() != models.GR3.input_sha or
                source.read_bytes() != official_bin):
            raise GenerateError('官方原件身份不一致')
        revision = profile_data.data_revision()
        effective = copy.deepcopy(saved)
        effective['changes'] = store.film_changes_for_generate(saved['id'], saved.get('changes', {}))
        requests = film_requests.from_changes(effective['changes'])
        ids = sorted({e['film'] for e in requests if e['film'].startswith('user_lut_')})
        index = copy.deepcopy(store.film_luts(saved['id']))
        profiles = copy.deepcopy(store.film_lut_profiles(saved['id'], ids)) if ids else {}
        assets = {e[k]['asset'] for key,e in effective['changes'].items()
                  if key != 'shutdown' for k in ('icon',) if k in e}
        if 'shutdown' in effective['changes']:
            assets.add(effective['changes']['shutdown']['asset'])
        asset_bytes = {name: store.asset_path(saved['id'], name).read_bytes() for name in assets}
        result = _run(store, effective, template, official_bin, film_profiles=profiles, inplace=True, asset_snapshot=asset_bytes)
        if (store.get_project(saved['id']) != saved or source.read_bytes() != official_bin or
                store.film_changes_for_generate(saved['id'], saved.get('changes', {})) != effective['changes'] or
                store.film_luts(saved['id']) != index or
                (ids and store.film_lut_profiles(saved['id'], ids) != profiles) or
                profile_data.data_revision() != revision or
                any(store.asset_path(saved['id'], name).read_bytes() != value for name,value in asset_bytes.items())):
            raise GenerateError('生成核对期间工程或来源改变，不输出文件')
        result[1]['film_data_revision'] = revision
        return result
    except GenerateError:
        raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise GenerateError('不扩容完整接线失败：%s' % exc) from exc
