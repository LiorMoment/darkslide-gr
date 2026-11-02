"""Firmware address relocation for GR III 2.20 / GR IIIx 1.70.

Feature code is written with GR III 2.10 / IIIx 1.60 addresses. Inside
`with firmware(model)` for a 2.20 / 1.70 model, A() turns each one into the 2.20
address from data/reloc_220.json (built by tools/build_reloc_220.py): an exact
checked point, or an address inside a segment whose code agrees in both firmware
pairs. Anything else is refused, never guessed. Outside that block (the default,
and every new thread) A() returns its argument unchanged, so 2.10 / 1.60 output
cannot change.
"""
import bisect
import contextlib
import contextvars
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import fwread, models

_STATE = contextvars.ContextVar('darkslide_firmware', default=None)
DATA = Path(__file__).with_name('data') / 'reloc_220.json'
DATA_SHA256 = '1f16152991f27277f3d811e46ca5db3fe78642272c038aeb9a97e2d1490f177e'


class RelocationError(ValueError):
    pass


@lru_cache(maxsize=1)
def _table():
    raw = DATA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA256:
        raise RelocationError('2.20 地址换算表指纹不符，请使用完整匹配的程序包')
    data = json.loads(raw)
    points = {a: b for a, b in data['points']}
    segments = sorted(tuple(s) for s in data['segments'])
    return points, segments, [s[0] for s in segments]


def _relocate(x):
    points, segments, starts = _table()
    if x in points:
        return points[x]
    i = bisect.bisect_right(starts, x) - 1
    if i >= 0:
        a, b, d = segments[i]
        if a <= x <= b:
            return x + d
    raise RelocationError('地址 %#x 没有核对过的 2.20 对应位置' % x)


def A(x):
    """2.10 / 1.60 address -> address of the current firmware.

    Containers (tuple, list, frozenset, set) are converted element by element. Only
    ints in the firmware address range 0x53000000-0x55FFFFFF are addresses; other
    values (instruction words, sizes, slot numbers) pass through unchanged.
    """
    if _STATE.get() is None:
        return x
    if type(x) in (tuple, list, frozenset, set):
        return type(x)(A(v) for v in x)
    if type(x) is int and 0x53000000 <= x < 0x56000000:
        return _relocate(x)
    return x


def is_220():
    return _STATE.get() is not None


def current():
    """The 2.20 / 1.70 model in force, or None for 2.10 / 1.60."""
    return _STATE.get()


def layout():
    return fwread.V220 if is_220() else fwread.V210


def payload_sha(default):
    """Decoded-original SHA-256 the feature code must see: `default` (2.10 / 1.60) or the 2.20 / 1.70 model's."""
    model = _STATE.get()
    return default if model is None else model.payload_sha


def model_id():
    """Profile identity for film data: the 2.20 / 1.70 model's id, or None (the caller's 2.10 / 1.60 default)."""
    model = _STATE.get()
    return None if model is None else model.id


def firmware_name(default):
    """'2.10' / '1.60' as given, or the 2.20 / 1.70 model's firmware string."""
    model = _STATE.get()
    return default if model is None else model.firmware


def family(base):
    """Precompiled ARM family name: 'GRIII-2.10-v29' -> 'GRIII-2.20-v29' under 2.20."""
    if not is_220():
        return base
    return base.replace('GRIII-2.10', 'GRIII-2.20').replace('GRIIIx-1.60', 'GRIIIx-1.70')


@contextlib.contextmanager
def firmware(model):
    """Run feature code for this model; only 2.20 / 1.70 switch addresses."""
    token = _STATE.set(model if models.is_220(model) else None)
    try:
        yield
    finally:
        _STATE.reset(token)
