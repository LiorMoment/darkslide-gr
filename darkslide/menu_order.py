"""Image Control menu order (GR III 2.10, GR IIIx 1.60): the request shape and the table writer.

The order is a list of fixed editor slot numbers (template `no`), never menu rows.
The camera keeps three photo lists (sets No0/No1/No2), three movie lists and the
RAW development option list; the first six hold tones, the RAW list holds its
own option numbers. Names, icons and films stay bound to the slot, so moving a
slot moves them with it.

The GR III 2.10 and GR IIIx 1.60 packages are supported. Ordinary and HDF bodies run
the same package of their series. The IIIx 1.60 payload holds the seven tables at the
same addresses with the same contents as GR III 2.10 (checked byte by byte on 2026-10-25),
so one writer serves both.

Writing returns the changed payload and the seven table spans (78 bytes). It
never touches the payload checksum (packing owns it) and never packs.
The independent readback lives in menu_order_verify and does not import this.
"""
from . import edit_core as builder, models

SCHEMA = 1
# Photo order as the camera ships it, in editor slots (Negative Film = slot 12 is row 7).
FACTORY_STILL = (1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11)
# The movie lists have no Soft Monotone (slot 4) and no HDR Tone (slot 10).
VIDEO_EXCLUDED = (4, 10)
FACTORY_VIDEO = tuple(s for s in FACTORY_STILL if s not in VIDEO_EXCLUDED)
# First version: the movie order follows the photo order (the store enforces it).
VIDEO_POLICY = "follow_still"
UNSUPPORTED = "这个型号暂不支持排序"

STILL_VA = (0x53B59224, 0x53B58FCC, 0x53B5919C)
VIDEO_VA = (0x53B59234, 0x53B591C8, 0x53B5936C)
RAW_VA = 0x53B5A988


class MenuOrderError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise MenuOrderError(message)


def _offset(va):
    return va - 0x53000000 + 0x10870


# Reviewed template -> official payload identity the writer accepts.
_TEMPLATES = {(models.GR3.id, "GR III", "2.10"): models.GR3, (models.GR3X.id, "GR IIIx", "1.60"): models.GR3X}


def _model_of(template):
    if type(template) is not dict:
        return None
    return _TEMPLATES.get((template.get("id"), template.get("model"), template.get("firmware")))


def supported(template):
    """The reviewed GR III 2.10 and GR IIIx 1.60 templates (ordinary and HDF bodies share each)."""
    return _model_of(template) is not None


def model_supported(model):
    return getattr(model, "id", None) in (models.GR3.id, models.GR3X.id)


def editable(template):
    """Whether the page may sort and save an order; every sortable model can also write it now."""
    return supported(template)


def model_editable(model):
    return model_supported(model)


def refusal(template):
    """Why an order cannot be written for this template."""
    return UNSUPPORTED


def video_for(still):
    return [s for s in still if s not in VIDEO_EXCLUDED]


def _slots(values, allowed, what):
    _need(type(values) is list and len(values) == len(allowed), "%s 需要 %d 项" % (what, len(allowed)))
    _need(all(type(v) is int for v in values), "%s 只能是整数槽位" % what)
    _need(sorted(values) == sorted(allowed), "%s 必须恰好包含每个风格一次" % what)
    return list(values)


def normalize(value):
    """Validate a stored/requested order. None means the factory order; an order
    equal to the factory one is returned as None, so it never counts as a change."""
    if value is None:
        return None
    _need(type(value) is dict and set(value) == {"schema", "still_slots", "video_slots"},
          "菜单顺序字段不对")
    _need(type(value["schema"]) is int and value["schema"] == SCHEMA, "菜单顺序版本不认识")
    still = _slots(value["still_slots"], FACTORY_STILL, "拍照菜单顺序")
    video = _slots(value["video_slots"], FACTORY_VIDEO, "视频菜单顺序")
    if tuple(still) == FACTORY_STILL and tuple(video) == FACTORY_VIDEO:
        return None
    return dict(schema=SCHEMA, still_slots=still, video_slots=video)


def follows_still(order):
    return order is None or order["video_slots"] == video_for(order["still_slots"])


def request_from_edit(edit):
    """Compositor request -> normalized order; the factory order is not an edit."""
    _need(type(edit) is dict and set(edit) == {"kind", "schema", "still_slots", "video_slots"} and
          edit["kind"] == "menu_order", "菜单顺序请求字段不对")
    order = normalize({k: edit[k] for k in ("schema", "still_slots", "video_slots")})
    _need(order is not None, "菜单顺序和原厂相同，不算改动")
    return order


def edit_for(order):
    return dict(kind="menu_order", schema=SCHEMA, still_slots=list(order["still_slots"]),
                video_slots=list(order["video_slots"]))


def _tones(template):
    slots = builder._template(template)
    tone = {}
    for slot in slots:
        if slot["no"] in FACTORY_STILL:
            _need(type(slot.get("mode")) is int and 0 < slot["mode"] < 256, "模板缺少风格编号")
            tone[slot["no"]] = slot["mode"]
    _need(sorted(tone) == sorted(FACTORY_STILL), "模板缺少内置风格")
    return tone


def write_tables(before, order, template):
    """Rewrite the seven tables of a payload whose tables still hold the factory
    order. Reads the factory contents from the payload itself and refuses if they
    differ, so the template and the firmware have to agree on every identity."""
    order = normalize(order)
    _need(order is not None, "菜单顺序和原厂相同，不算改动")
    _need(supported(template), UNSUPPORTED)
    tone = _tones(template)
    still_old = bytes(tone[s] for s in FACTORY_STILL)
    video_old = bytes(tone[s] for s in FACTORY_VIDEO)
    raw_at = _offset(RAW_VA)
    raw_old = before[raw_at:raw_at + len(FACTORY_STILL)]
    _need(sorted(raw_old) == list(range(1, 13)), "RAW 显影选项表和原厂不同")
    # RAW option of each tone, read from the factory tables (row r of the photo list <-> RAW entry r).
    raw_of = {still_old[r]: raw_old[r] for r in range(len(FACTORY_STILL))}
    plan = [(va, still_old, bytes(tone[s] for s in order["still_slots"])) for va in STILL_VA]
    plan += [(va, video_old, bytes(tone[s] for s in order["video_slots"])) for va in VIDEO_VA]
    plan.append((RAW_VA, raw_old, bytes(raw_of[tone[s]] for s in order["still_slots"])))
    changed, spans = bytearray(before), []
    for va, old, new in plan:
        at = _offset(va)
        _need(0 <= at and at + len(old) <= len(before) - 4, "菜单表越界")
        _need(before[at:at + len(old)] == old, "菜单表和原厂不同（%08X）" % va)
        _need(len(new) == len(old), "菜单表长度不对")
        changed[at:at + len(new)] = new
        spans.append(dict(what="menu_order", table="%08X" % va, offset=at, length=len(new)))
    return bytes(changed), spans


def apply(before, order, template):
    """Pinned entry for the compositor: only the official payload of the template's model."""
    model = _model_of(template)
    _need(model is not None, UNSUPPORTED)
    _need(type(before) is bytes and len(before) == builder.PAYLOAD_SIZE and
          builder._sha(before) == (builder.PAYLOAD_SHA if model == models.GR3 else model.payload_sha),
          "需要官方 %s %s 原件" % (model.name, model.firmware))
    return write_tables(before, order, template)


def names_in_order(order, template, changes=None):
    """Display names in camera order; a renamed preset reads "new name（preset name）"."""
    still = FACTORY_STILL if order is None else order["still_slots"]
    slots = builder._template(template)
    out = []
    for no in still:
        label = slots[no - 1]["name"]
        name = ((changes or {}).get(str(no)) or {}).get("name")
        out.append(label if name is None else "%s（%s）" % (name, label))
    return out
