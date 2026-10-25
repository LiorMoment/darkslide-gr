"""Independent 柔光 (soft glow) readback; no firmware writer imports.

Pins the window file by SHA-256, and pins what it means: the GR III 2.10 official payload with
these windows written is exactly the decoded payload of the reviewed v22.6 test firmware. On a
candidate it checks every window holds the v22.6 bytes and returns the spans for the combined
verifier, which still owns overlap, all other bytes, the checksum and the container.
"""
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).with_name('soft_glow_windows.json')
DATA_SHA256 = '9e1b9b7a7a32a3e6144a85f9f05c03c5f4903dfa1c7b5526eff1c013d0f2aa90'
GR3_PAYLOAD_SHA256 = '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'
IIIX_PAYLOAD_SHA256 = 'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'
# Decoded payload of the delivered v22.6 GR III test firmware (BIN ece36e0f…).
V226_PAYLOAD_SHA256 = '97e00581d1b9fb9d58b28c283d9730ae9c477163418e74767877cef170b3e9e6'
PAYLOAD_SIZE = 59718388


class SoftGlowError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise SoftGlowError(message)


def windows():
    raw = DATA.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == DATA_SHA256, 'Soft glow window file changed')
    data = json.loads(raw)
    out, cursor = [], 0
    for at, old, new in data['windows']:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        _need(type(at) is int and at >= cursor and len(old) == len(new) > 0 and
              at + len(old) <= PAYLOAD_SIZE - 4, 'Soft glow window bounds')
        out.append((at, old, new))
        cursor = at + len(old)
    return out


def verify(original, candidate):
    """Check the soft glow windows of a combined payload; returns {'spans': [(offset, bytes)], ...}."""
    _need(type(original) is bytes and len(original) == PAYLOAD_SIZE, 'Original payload size')
    digest = hashlib.sha256(original).hexdigest()
    _need(digest in (GR3_PAYLOAD_SHA256, IIIX_PAYLOAD_SHA256),
          'Expected the original GR III 2.10 or GR IIIx 1.60 decoded payload')
    _need(type(candidate) is bytes and len(candidate) == len(original), 'Candidate payload size')
    rows = windows()
    if digest == GR3_PAYLOAD_SHA256:
        rebuilt = bytearray(original)
        for at, _, new in rows:
            rebuilt[at:at + len(new)] = new
        # The checksum word is the only other difference from the delivered payload.
        total = sum(int.from_bytes(rebuilt[i:i + 4], 'little') for i in range(0, PAYLOAD_SIZE - 4, 4))
        rebuilt[-4:] = ((-total) & 0xFFFFFFFF).to_bytes(4, 'little')
        _need(hashlib.sha256(rebuilt).hexdigest() == V226_PAYLOAD_SHA256, 'Windows do not rebuild v22.6')
    spans = []
    for at, old, new in rows:
        end = at + len(old)
        _need(original[at:end] == old, 'Original bytes differ at %X' % at)
        _need(candidate[at:end] == new, 'Soft glow bytes differ at %X' % at)
        spans.append((at, new))
    return dict(scope='soft_glow_windows_only', version='v22.6', windows=len(rows),
                window_bytes=sum(len(n) for _, n in spans), spans=spans)
