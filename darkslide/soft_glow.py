"""柔光 (soft glow) v22.6: the fixed in-place patch, for GR III 2.10 and GR IIIx 1.60. No packing.

The patch is the reviewed v22.6 test firmware (glow effect byte-identical to the v22.4 the user
tested on a GR III; 40×40 ADJ icon), captured by tools/capture_soft_glow.py as decoded-payload
windows: the two glow modules, the image-pipeline hooks, the ADJ icon entry and its pixels. It
is a whole-camera switch with no settings of its own. Every original window holds the same bytes
in the GR IIIx 1.60 payload (checked 2026-10-25), so the same windows apply to both series; the
payload checksum word stays with packing. Nothing grows.

The independent readback lives in soft_glow_verify and does not import this.
"""
import json
from pathlib import Path

from . import edit_core as builder, models

VERSION = "v22.6"
DATA = Path(__file__).with_name("soft_glow_windows.json")


def edit():
    return dict(kind="soft_glow", version=VERSION)


def validate_request(request):
    builder._require(type(request) is dict and set(request) == {"kind", "version"} and
                     request["kind"] == "soft_glow" and request["version"] == VERSION, "柔光请求字段不对")


def _windows():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    builder._require(data.get("feature") == "soft_glow" and data.get("version") == VERSION and
                     data.get("official_payload_sha256") == models.GR3.payload_sha, "柔光数据版本不对")
    return [(at, bytes.fromhex(old), bytes.fromhex(new)) for at, old, new in data["windows"]]


def apply(before, model_id=None):
    """Write the windows into an official GR III 2.10 or GR IIIx 1.60 payload whose original bytes match."""
    model = models.get(model_id)
    builder._require(model in (models.GR3, models.GR3X), "柔光只支持 GR III 2.10 和 GR IIIx 1.60")
    builder._require(type(before) is bytes and len(before) == builder.PAYLOAD_SIZE and
                     builder._sha(before) == (builder.PAYLOAD_SHA if model == models.GR3 else model.payload_sha),
                     "immutable official payload required")
    changed, changes = bytearray(before), []
    for at, old, new in _windows():
        end = at + len(old)
        builder._require(len(new) == len(old) and 0 <= at and end <= len(before) - 4, "柔光窗口越界")
        builder._require(before[at:end] == old, "柔光原位字节和官方原件不同（%X）" % at)
        changed[at:end] = new
        changes.append(dict(what="soft_glow", offset=at, length=len(new)))
    return bytes(changed), changes
