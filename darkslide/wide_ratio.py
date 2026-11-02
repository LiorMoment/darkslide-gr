"""Wide aspect ratios (16:9 and 65:24 JPEG, RAW stays full 3:2) for GR III 2.10 and GR IIIx 1.60. No packing.

The fixed in-place patch (wide16: the wide2 fixes with the v22.28 glow): menu and ADJ choices, JPEG crop, viewfinder
mask, post-capture review, 65:24 AF range. Every window holds the same original bytes in the GR IIIx 1.60
payload (checked 2026-10-30), so the same windows apply; IIIx is untested on a camera. No package a user tested matches these windows yet. It is one patch with 柔光 when both are on, because they share
hooks, so a project never carries a separate soft_glow edit next to this one. Nothing grows.

The independent readback lives in wide_ratio_verify and does not import this.
"""
import json
from pathlib import Path

from . import edit_core as builder, models

VERSION = "wide16"
DATA = Path(__file__).with_name("wide_ratio_windows.json")
# 日期印记 (date07) is built on mode 11, so it replaces that mode's windows as one set.
DATE_DATA = Path(__file__).with_name("date_stamp_windows.json")
DATE_VERSION = "date07"


def edit(glow=False, date=False):
    if date:
        return dict(kind="wide_ratio", version=VERSION, glow=True, date=True)
    return dict(kind="wide_ratio", version=VERSION, glow=bool(glow))


def validate_request(request):
    builder._require(type(request) is dict and set(request) - {"date"} == {"kind", "version", "glow"} and
                     request["kind"] == "wide_ratio" and request["version"] == VERSION and
                     type(request["glow"]) is bool, "画幅请求字段不对")
    builder._require("date" not in request or (request["date"] is True and request["glow"] is True), "日期印记要和柔光、画幅一起装")


def _windows(glow, date=False):
    if date:
        data = json.loads(DATE_DATA.read_text(encoding="utf-8"))
        builder._require(glow and data.get("feature") == "date_stamp" and data.get("version") == DATE_VERSION and
                         data.get("official_payload_sha256") == models.GR3.payload_sha, "日期数据版本不对")
        return [(at, bytes.fromhex(old), bytes.fromhex(new)) for at, old, new in data["windows"]]
    data = json.loads(DATA.read_text(encoding="utf-8"))
    builder._require(data.get("feature") == "wide_ratio" and data.get("version") == VERSION and
                     data.get("official_payload_sha256") == models.GR3.payload_sha, "画幅数据版本不对")
    return [(at, bytes.fromhex(old), bytes.fromhex(new)) for at, old, new in data["modes"]["11" if glow else "10"]]


def apply(before, model_id=None, glow=False, date=False):
    """Write the windows into an official GR III 2.10 or GR IIIx 1.60 payload whose original bytes match."""
    model = models.get(model_id)
    builder._require(model in (models.GR3, models.GR3X), "画幅只支持 GR III 2.10 和 GR IIIx 1.60")
    builder._require(type(before) is bytes and len(before) == builder.PAYLOAD_SIZE and
                     builder._sha(before) == (builder.PAYLOAD_SHA if model == models.GR3 else model.payload_sha),
                     "immutable official payload required")
    changed, changes = bytearray(before), []
    for at, old, new in _windows(glow, date):
        end = at + len(old)
        builder._require(len(new) == len(old) and 0 <= at and end <= len(before) - 4, "画幅窗口越界")
        builder._require(before[at:end] == old, "画幅原位字节和官方原件不同（%X）" % at)
        changed[at:end] = new
        changes.append(dict(what="wide_ratio", offset=at, length=len(new)))
    return bytes(changed), changes
