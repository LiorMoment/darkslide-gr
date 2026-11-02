"""Independent 柔光 (soft glow) readback; no firmware writer imports.

Pins the window file by SHA-256, and pins what it means: the GR III 2.10 official payload with
these windows written is exactly the decoded payload of the v22.28 build. On a
candidate it checks every window holds the v22.28 bytes and returns the spans for the combined
verifier, which still owns overlap, all other bytes, the checksum and the container.
"""
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).with_name('soft_glow_windows.json')
DATA_SHA256 = '309f13527c79b71e7a80d91dd99ce6f82f58a7a40942aaec0070695b54f0a523'
GR3_PAYLOAD_SHA256 = '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'
IIIX_PAYLOAD_SHA256 = 'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'
# Decoded payload of the v22.28 build (windows on the official GR III 2.10 payload, checksum set).
BUILD_PAYLOAD_SHA256 = '2e700b0defeb838e4d38e74cb3cdd416cb069cdb23473956b1744b0c263b8ba9'
PAYLOAD_SIZE = 59718388
# GR III 2.20 / GR IIIx 1.70 (the optimized glow core at relocated addresses, settings read from the Normal and Playback
# start-up branches instead of the settings constructor, which is on the MENU update path); pinned the same way: the 2.20
# official payload with these windows is the rebuilt candidate payload.
DATA_220 = Path(__file__).with_name('soft_glow_windows_220.json')
DATA_220_SHA256 = 'c3e7afa6844d767707239f5a26098f6a577c05e48bbd4a34cf877ab6b01c6294'
GR3_220_PAYLOAD_SHA256 = '5b475fcc434dec0f12e7956b02b5b38959d599b7a4ff90486263c46816a21b6d'
IIIX_170_PAYLOAD_SHA256 = '1129b9289b3cc68ab6f407a87d60db750483cc5ae41a6609596f6fe676b14521'
BUILD_220_PAYLOAD_SHA256 = 'ce54ddcbb6f1baca7814a549fd9eddb12b1f3e3b3c1347a76e0c06d40df97644'
PAYLOAD_SIZE_220 = 59798532


class SoftGlowError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise SoftGlowError(message)


def windows(new_firmware=False):
    path, digest, size = (DATA_220, DATA_220_SHA256, PAYLOAD_SIZE_220) if new_firmware else (DATA, DATA_SHA256, PAYLOAD_SIZE)
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == digest, 'Soft glow window file changed')
    data = json.loads(raw)
    out, cursor = [], 0
    for at, old, new in data['windows']:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        _need(type(at) is int and at >= cursor and len(old) == len(new) > 0 and
              at + len(old) <= size - 4, 'Soft glow window bounds')
        out.append((at, old, new))
        cursor = at + len(old)
    return out


def verify(original, candidate):
    """Check the soft glow windows of a combined payload; returns {'spans': [(offset, bytes)], ...}."""
    _need(type(original) is bytes and len(original) in (PAYLOAD_SIZE, PAYLOAD_SIZE_220), 'Original payload size')
    digest = hashlib.sha256(original).hexdigest()
    _need(digest in (GR3_PAYLOAD_SHA256, IIIX_PAYLOAD_SHA256, GR3_220_PAYLOAD_SHA256, IIIX_170_PAYLOAD_SHA256),
          'Expected the original GR III 2.10 / 2.20 or GR IIIx 1.60 / 1.70 decoded payload')
    _need(type(candidate) is bytes and len(candidate) == len(original), 'Candidate payload size')
    new_firmware = digest in (GR3_220_PAYLOAD_SHA256, IIIX_170_PAYLOAD_SHA256)
    rows = windows(new_firmware)
    if digest in (GR3_PAYLOAD_SHA256, GR3_220_PAYLOAD_SHA256):
        rebuilt = bytearray(original)
        for at, _, new in rows:
            rebuilt[at:at + len(new)] = new
        # The checksum word is the only other difference from the delivered / rebuilt payload.
        total = sum(int.from_bytes(rebuilt[i:i + 4], 'little') for i in range(0, len(rebuilt) - 4, 4))
        rebuilt[-4:] = ((-total) & 0xFFFFFFFF).to_bytes(4, 'little')
        _need(hashlib.sha256(rebuilt).hexdigest() == (BUILD_220_PAYLOAD_SHA256 if new_firmware else BUILD_PAYLOAD_SHA256),
              'Windows do not rebuild v22.28')
    spans = []
    for at, old, new in rows:
        end = at + len(old)
        _need(original[at:end] == old, 'Original bytes differ at %X' % at)
        _need(candidate[at:end] == new, 'Soft glow bytes differ at %X' % at)
        spans.append((at, new))
    return dict(scope='soft_glow_windows_only', version='v22.28', windows=len(rows),
                window_bytes=sum(len(n) for _, n in spans), spans=spans)
