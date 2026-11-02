"""柔光 (soft glow) v22.28: the fixed in-place patch, one source for every firmware. No packing.

Optimized scalar core; the saved setting is read at the Normal and Playback start-up branches, off the MENU
update path, and only records the user chose are written. The state sits in the high half of the settings singleton guard
word, so nothing is allocated and the default-template path stays original. Captured as decoded-payload windows: the two glow
modules, the image-pipeline hooks, the ADJ icon entry and its pixels. It is a whole-camera switch with no settings
of its own. Every original window holds the same bytes in the GR IIIx 1.60 payload, so one file serves GR III 2.10
and GR IIIx 1.60, and one serves GR III 2.20 and GR IIIx 1.70; the payload checksum word stays with packing. Nothing
grows. With wide ratios it is part of the wide ratio windows, built from the same glow.

The independent readback lives in soft_glow_verify and does not import this.
"""
import json
from pathlib import Path

from . import edit_core as builder, models

VERSION = "v22.28"
DATA = Path(__file__).with_name("soft_glow_windows.json")
# GR III 2.20 / GR IIIx 1.70: the same sources at the relocated addresses, checked word by word against the 2.10
# build. Shared by both series, like the 2.10 windows.
DATA_220 = Path(__file__).with_name("soft_glow_windows_220.json")


def edit():
    return dict(kind="soft_glow", version=VERSION)


def validate_request(request):
    builder._require(type(request) is dict and set(request) == {"kind", "version"} and
                     request["kind"] == "soft_glow" and request["version"] == VERSION, "柔光请求字段不对")


def _windows(model=models.GR3):
    new = models.is_220(model)
    data = json.loads((DATA_220 if new else DATA).read_text(encoding="utf-8"))
    builder._require(data.get("feature") == "soft_glow" and data.get("version") == VERSION and
                     data.get("official_payload_sha256") == (models.GR3_220 if new else models.GR3).payload_sha,
                     "柔光数据版本不对")
    return [(at, bytes.fromhex(old), bytes.fromhex(new)) for at, old, new in data["windows"]]


def apply(before, model_id=None):
    """柔光 (soft glow) v22.28: the fixed in-place patch, one source for every firmware. No packing.

Optimized scalar core; the saved setting is read at the Normal and Playback start-up branches, off the MENU
update path, and only records the user chose are written. The state sits in the high half of the settings singleton guard
word, so nothing is allocated and the default-template path stays original. Captured as decoded-payload windows: the two glow
modules, the image-pipeline hooks, the ADJ icon entry and its pixels. It is a whole-camera switch with no settings
of its own. Every original window holds the same bytes in the GR IIIx 1.60 payload, so one file serves GR III 2.10
and GR IIIx 1.60, and one serves GR III 2.20 and GR IIIx 1.70; the payload checksum word stays with packing. Nothing
grows. With wide ratios it is part of the wide ratio windows, built from the same glow.

The independent readback lives in soft_glow_verify and does not import this.
"""
    model = models.get(model_id)
    builder._require(model in models.ALL, "柔光只支持 GR III 2.10 / 2.20 和 GR IIIx 1.60 / 1.70")
    builder._require(type(before) is bytes and len(before) == model.payload_size and
                     builder._sha(before) == model.payload_sha, "immutable official payload required")
    changed, changes = bytearray(before), []
    for at, old, new in _windows(model):
        end = at + len(old)
        builder._require(len(new) == len(old) and 0 <= at and end <= len(before) - 4, "柔光窗口越界")
        builder._require(before[at:end] == old, "柔光原位字节和官方原件不同（%X）" % at)
        changed[at:end] = new
        changes.append(dict(what="soft_glow", offset=at, length=len(new)))
    return bytes(changed), changes
