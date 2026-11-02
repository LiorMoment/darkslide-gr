from . import package, models, menu_language
"""Apply requested icon/name edits and package the result once.

Uses the fixed image layout and package contract. Returns bytes,
never writes firmware files. Independent readback is still required by the caller.
"""
import struct
from . import build as codec
BuildError = codec.BuildError
INPUT_SHA, INPUT_SIZE = codec.INPUT_SHA, codec.INPUT_SIZE
PAYLOAD_SHA, PAYLOAD_SIZE = codec.PAYLOAD_SHA, codec.PAYLOAD_SIZE
_template, _require, _sha = codec._template, codec._require, codec._sha
_icon, _string = codec._icon, codec._string
_sum, _literal, _decode = codec._sum, codec._literal, codec._decode
SOURCE_SHA256 = "b4cde6959ed2bd294ae5eb003e784985938cb23105c746130f48a9fcd8dab5db"


def _validate_edits(edits, template):
    slots = _template(template)
    _require(type(edits) is list and 0 < len(edits) <= 23, "1 to 23 edits required")
    seen = set()
    for edit in edits:
        _require(type(edit) is dict, "改动必须为对象")
        kind, no = edit.get("kind"), edit.get("slot")
        _require(type(kind) is str and kind in ("icon", "name") and type(no) is int and 1 <= no <= 14, "改动槽位或类型错误")
        fields = {"kind", "slot", "icon_id", "pixels"} if kind == "icon" else {"kind", "slot", "string_id", "text"}
        _require(set(edit) - ({"menu_language"} if kind == "name" else set()) == fields, "改动存在缺失或未知字段")
        if "menu_language" in edit:
            _require(type(edit["menu_language"]) is str and edit["menu_language"] in menu_language.CHOICES, "相机菜单语言不正确")
        key = "icon_id" if kind == "icon" else "string_id"
        stage = "icon" if kind == "icon" else "rename"
        _require(type(edit[key]) is int and edit[key] == slots[no - 1][key] and
                 slots[no - 1][stage] in ("test", "open"), "该槽位或编号不允许修改")
        _require((kind, no) not in seen, "同一槽位重复改动")
        seen.add((kind, no))
        if kind == "icon":
            _require(type(edit["pixels"]) is bytes and len(edit["pixels"]) == 6400,
                     "图标必须是40×40 RGBA字节")
            _require(all(a in (0, 255) for a in edit["pixels"][3::4]), "图标alpha必须为0或255")
        else:
            text = edit["text"]
            limit = slots[no - 1].get("name_max", len(slots[no - 1]["factory_en"]))
            _require(type(text) is str and 0 < len(text) <= limit and
                     all(32 <= ord(c) <= 126 for c in text) and
                     text == text.strip(" ") and "  " not in text, "预设名须为限长可打印ASCII且无多余空格")
    return slots


# Languages whose own text never lends its storage to a longer preset name.
KEEP_LANGUAGES = menu_language.KEEP  # ja, en, zh-TW, zh-CN


def _lendable(payload, identifier, keep=KEEP_LANGUAGES):
    """Longest one-line text of this string in another language that no other
    record (any language, any string) points into. Ties go to the lower language.
    Returns (record offset, text offset, length byte, address) or None."""
    spans, own = [], []
    layout = codec._layout(payload)
    for language in range(21):
        table = codec._u32(payload, layout[4] + language * 4)
        for other in range(1, 785):
            record = codec._u32(payload, table + other * 4)
            if not record:
                continue
            r = codec._off(record, 8, layout)
            count, lines, zeros = struct.unpack_from("<BBH", payload, r)
            va = struct.unpack_from("<I", payload, r + 4)[0]
            spans.append((va, va + count * 2))
            if other == identifier and language not in keep and lines == 1 and zeros == 0 and count >= 2:
                own.append((count, -language, r, va))
    best = None
    for count, neg_language, r, va in sorted(own, reverse=True):
        if sum(1 for a, b in spans if a < va + count * 2 and b > va) == 1:
            t = codec._off(va, count * 2, layout)
            if payload[t + (count - 1) * 2:t + count * 2] == b"\0\0":
                best = (r, t, count, va)
                break
    return best


def apply_edits(before, edits, template):
    """Apply validated name/icon edits to the official payload, without packing."""
    slots = _validate_edits(edits, template)
    model = models.for_template(template)
    _require(type(before) is bytes and len(before) == (model.payload_size if models.is_220(model) else PAYLOAD_SIZE) and
             _sha(before) == model.payload_sha, "immutable official payload required")
    after, changes, ranges = bytearray(before), [], []

    def write(offset, value, what, edit=None):
        _require(0 <= offset <= len(after) - len(value) - 4, "目标范围越界")
        end = offset + len(value)
        _require(not any(offset < b and end > a for a, b in ranges), "改动目标重叠")
        if before[offset:end] == value:
            return
        after[offset:end] = value
        ranges.append((offset, end))
        entry = {"what": what, "offset": offset, "length": len(value)}
        if edit:
            entry.update(slot=edit["slot"])
            key = "icon_id" if edit["kind"] == "icon" else "string_id"
            entry[key] = edit[key]
        changes.append(entry)

    for edit in sorted(edits, key=lambda e: (e["slot"], e["kind"])):
        if edit["kind"] == "icon":
            pixels = edit["pixels"]
            _require(type(pixels) is bytes and len(pixels) == 6400, "图标必须是40×40 RGBA字节")
            start, size, w, h = _icon(before, edit["icon_id"])
            _require((size, w, h) == (6400, 40, 40), "原厂图标尺寸不匹配")
            for other in range(1, 671):
                if other == edit["icon_id"]:
                    continue
                a, length, _, _ = _icon(before, other)
                _require(not (a < start + size and a + length > start), "图标像素与其他编号共享，拒绝修改")
            for y in range(40):
                for x in range(40):
                    alpha = pixels[(y * 40 + x) * 4 + 3]
                    original = before[start + (y * 40 + x) * 4 + 3]
                    visible = 1 <= x < 39 and 2 <= y < 38
                    _require(alpha in (0, 255) and (alpha != 0) == (original != 0) and
                             (alpha != 0) == visible, "图标透明边或alpha不符合原厂规则")
                if 2 <= y < 38:
                    a = (y * 40 + 1) * 4
                    write(start + a, pixels[a:a + 152], "icon", edit)
        else:
            text = edit["text"]
            _require(type(text) is str, "预设名必须为字符串")
            en = _string(before, edit["string_id"], 1)
            others = [(code, _string(before, edit["string_id"], menu_language.index(code)))
                      for code in menu_language.repointed(edit)]
            r, t, count, va, old = en
            _require(old == slots[edit["slot"] - 1]["factory_en"], "原厂英文名与模板不符")
            _require(0 < len(text) and all(32 <= ord(c) <= 126 for c in text) and
                     text == text.strip(" ") and "  " not in text, "预设名须为可打印ASCII且无多余空格")
            new = text.encode("utf-16-le") + b"\0\0"
            if len(text) <= len(old):  # fits the English text's own storage
                write(t, new + bytes(count * 2 - len(new)), "name_en_text", edit)
                write(r, bytes((len(text) + 1,)), "name_en_length", edit)
                for code, rec in others:
                    write(rec[0], bytes((len(text) + 1, 1, 0, 0)) + struct.pack("<I", va),
                          "name_%s_record" % code.replace("-", "_"), edit)
            else:  # longer: use the longest storage only this string uses, in another language
                lent = _lendable(before, edit["string_id"])
                _require(lent is not None, "没有可借用的预设名存储")
                lr, lt, lcount, lva = lent
                _require(len(text) <= lcount - 1, "预设名超过可用长度")
                record = bytes((len(text) + 1, 1, 0, 0)) + struct.pack("<I", lva)
                write(lt, new + bytes(lcount * 2 - len(new)), "name_lent_text", edit)
                write(lr, bytes((len(text) + 1,)), "name_lent_length", edit)
                write(r, record, "name_en_record", edit)
                for code, rec in others:
                    if rec[0] != lr:  # the lending language already shows its own (new) text
                        write(rec[0], record, "name_%s_record" % code.replace("-", "_"), edit)
    _require(changes, "改动未改变任何字节")
    return bytes(after), changes


def _pack_payload(official_bin, before, frames, stop, footer, changed, declared, model_id=None):
    model = models.get(model_id)
    _require(type(changed) is bytes and len(changed) == len(before), "Replacement size differs")
    try:
        output, finished, changes, rewritten = package.pack_change(
            official_bin, before, frames, stop, footer, changed, declared)
        readback, _, _, _ = _decode(output, model.id)
        _require(readback == finished, "Result does not match the requested image")
    except package.PackageError as exc:
        raise BuildError(str(exc)) from exc
    return dict(output=output, input_sha256=model.input_sha, output_sha256=_sha(output),
                payload_sha256_before=model.payload_sha, payload_sha256_after=_sha(finished),
                version_word=model.version.hex(" ").upper(), payload_changes=changes,
                container=dict(frames_replaced=rewritten, header_changed=False,
                               footer_fields_changed=["encoded_size", "checksum"]),
                builder=dict(name="Darkslide firmware builder", version="1.0",
                             source_sha256=SOURCE_SHA256,
                             source_hash_scope="edit_core.py with SOURCE_SHA256 value replaced by 64 zeros"))


def build(official_bin, edits, template):
    """Compatibility entry: unchanged icon/name result bytes."""
    _validate_edits(edits, template)
    model = models.for_template(template)
    _require(type(official_bin) is bytes and len(official_bin) == model.input_size and
             _sha(official_bin) == model.input_sha, "fixed official GR III 2.10 input required")
    before, frames, stop, footer = _decode(official_bin, model.id)
    changed, changes = apply_edits(before, edits, template)
    return _pack_payload(official_bin, before, frames, stop, footer, changed, changes, model.id)
