"""Independent wide ratio readback; no firmware writer imports.

Pins the window file by SHA-256 and what it means: the GR III 2.10 official payload with the windows of a
mode written is exactly the composed payload of the pinned wide16 resources. On a candidate it checks every
window holds those bytes and returns the spans for the combined verifier, which still owns overlap, all
other bytes, the checksum and the container.
"""
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).with_name('wide_ratio_windows.json')
DATA_SHA256 = '152ac74bd63d59e8701a1a925c376bb0ce89ab82529978920870997e500ae6c1'
GR3_PAYLOAD_SHA256 = '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'
IIIX_PAYLOAD_SHA256 = 'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'
# GR IIIx 1.60 holds every window's original bytes at the GR III 2.10 offsets (checked 2026-10-30).
COMPOSED_SHA256 = {GR3_PAYLOAD_SHA256: {'10': 'bf3301ee7350af1a02c7ad7e8b8691ad4add3f972693ea831f6c6bbfb082b485',
                                       '11': 'f0bc2c0ef1e942c54e9d4e4d73805eac206bbbdf8e2d8d8dc9ee44461c70bbe4'},
                   IIIX_PAYLOAD_SHA256: {'10': 'c27ccba747c4cee231e40719050681d2c4383f506da3a1f1f1cf2b85739506c4',
                                        '11': '5f3e045ead16b68a0f48af7d06d73b053046676059669d0dbfcca779486bd56d'}}
PAYLOAD_SIZE = 59718388
# 日期印记 (date07): mode 11 with the date module, one window set.
DATE_DATA = Path(__file__).with_name('date_stamp_windows.json')
DATE_DATA_SHA256 = '50c700906683773dfaa177c71d93e14401a8756531664f24f09214abf2315e70'
DATE_COMPOSED_SHA256 = {GR3_PAYLOAD_SHA256: 'd4925668439bdd2ac4aaa5623debaaf22a686e6da77919bb262b3711f3056a8f',
                        IIIX_PAYLOAD_SHA256: '3f9960482ca51c39f1965f3b2ff5a4880ad0694fad136b6b84e1e78d8ec32913'}


class WideRatioError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise WideRatioError(message)


def windows(glow, date=False):
    raw = (DATE_DATA if date else DATA).read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == (DATE_DATA_SHA256 if date else DATA_SHA256), 'Wide ratio window file changed')
    data = json.loads(raw)
    _need(not date or glow, 'Date stamp needs glow')
    out, cursor = [], 0
    for at, old, new in (data['windows'] if date else data['modes']['11' if glow else '10']):
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        _need(type(at) is int and at >= cursor and len(old) == len(new) > 0 and
              at + len(old) <= PAYLOAD_SIZE - 4, 'Wide ratio window bounds')
        out.append((at, old, new))
        cursor = at + len(old)
    return out


def verify(original, candidate, glow, date=False):
    """Check the windows of a combined payload; returns {'spans': [(offset, bytes)], ...}."""
    _need(type(original) is bytes and len(original) == PAYLOAD_SIZE, 'Original payload size')
    digest = hashlib.sha256(original).hexdigest()
    _need(digest in COMPOSED_SHA256, 'Expected the original GR III 2.10 or GR IIIx 1.60 decoded payload')
    _need(type(candidate) is bytes and len(candidate) == len(original), 'Candidate payload size')
    _need(type(glow) is bool, 'glow flag')
    _need(type(date) is bool, 'date flag')
    rows = windows(glow, date)
    rebuilt = bytearray(original)
    spans = []
    for at, old, new in rows:
        _need(original[at:at + len(old)] == old, 'Wide ratio original bytes')
        _need(candidate[at:at + len(new)] == new, 'Wide ratio window %X' % at)
        rebuilt[at:at + len(new)] = new
        spans.append((at, new))
    _need(hashlib.sha256(rebuilt).hexdigest() == (DATE_COMPOSED_SHA256[digest] if date else COMPOSED_SHA256[digest]['11' if glow else '10']), 'Windows do not rebuild wide16')
    return dict(scope='wide_ratio_windows_only', windows=len(rows), spans=spans)
