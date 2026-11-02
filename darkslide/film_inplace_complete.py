"""Independent complete-payload v29 reader; never imports a producer.

Frozen coverage authorizes the address set; actual image bytes must match the
separately pinned shared-code hash and independently derived profile fields.
This is byte integrity, not a proof of runtime behavior or release approval.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from . import film_inplace_integrity as integrity, film_inplace_readback as data
from . import film_profiles
from .fwaddr import A as fa
from . import fwaddr

LAYOUT = data.LAYOUT
RANGES = Path(__file__).with_name('film_inplace_ranges.json')
RANGES_220 = Path(__file__).with_name('film_inplace_ranges_220.json')

@dataclass(frozen=True)
class ActualWindow:
    address: int
    expected: bytes
    replacement: bytes


def resolve_profiles(edits, supplied=None):
    data.need(type(edits) is list and len(edits) <= 4, 'film request list')
    selected = {}
    for e in edits:
        data.need(type(e) is dict and set(e) == {'kind', 'slot', 'film'} and
                  e['kind'] == 'film' and type(e['slot']) is int and
                  e['slot'] in data.ALIASES and e['slot'] not in selected and
                  type(e['film']) is str, 'film request identity')
        selected[e['slot']] = e['film']
    supplied = {} if supplied is None else supplied
    data.need(type(supplied) is dict and all(k in selected.values() and k.startswith('user_lut_')
                                           for k in supplied), 'user profile scope')
    resolved = {}
    for ident in selected.values():
        if ident.startswith('user_lut_'):
            data.need(ident in supplied, 'missing user profile')
            resolved[ident] = film_profiles.validate_profile(supplied[ident], ident, fwaddr.model_id())
        else:
            resolved[ident] = film_profiles.load_profile(ident, fwaddr.model_id())
    return selected, resolved


def offset(address, size, length):
    data.need(type(address) is int and type(size) is int and 0 < size <= 512,
              'range integer/size')
    end = address + size
    if fa(0x55000000) <= address < end <= fa(0x5503a080):
        at = address - fa(0x55000000) + fa(0x5407b540) - fa(0x53000000) + 0x10870
    else:
        data.need(fa(0x53000000) <= address < end <= fa(0x5407b540), 'canonical RTOS mapping')
        at = address - fa(0x53000000) + 0x10870
    data.need(0 <= at < at + size <= length - 4, 'range/checksum bounds')
    return at


def readback(before, after, edits, profiles=None, *, model_id=None, layout=None):
    data.need(type(before) is bytes and type(after) is bytes and len(before) == len(after),
              'immutable equal-sized payloads')
    return readback_reader(before, lambda at, n: after[at:at+n], edits, profiles, model_id=model_id, layout=layout)


def readback_reader(before, read_after, edits, profiles=None, *, model_id=None, layout=None):
    try:
        if type(edits) is list and any(type(e) is dict and e.get('slot') == 2 for e in edits):
            from . import film_vivid_fifth_readback
            return film_vivid_fifth_readback.readback_reader(before,read_after,edits,profiles,layout=layout,model_id=model_id)
        data.need(layout is None or layout == LAYOUT, 'four-slot complete layout mismatch')
        if model_id is not None:
            from . import models
            with fwaddr.firmware(models.get(model_id)):
                return _readback_reader(before, read_after, edits, profiles)
        return _readback_reader(before, read_after, edits, profiles)
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise data.ReadbackError('v29 complete readback: %s' % exc) from exc


def _readback_reader(before, read_after, edits, profiles=None):
    """Same bounded reader core, also usable against a sparse offline overlay."""
    data.need(type(before) is bytes and callable(read_after), 'original/reader')
    selection, resolved = resolve_profiles(edits, profiles)
    schema = json.loads((RANGES_220 if fwaddr.is_220() else RANGES).read_text(encoding='utf-8'))
    data.need(type(schema) is dict, 'range object')
    data.need(type(schema.get('schema')) is int and schema['schema'] == 1 and
              schema.get('layout') == LAYOUT, 'range schema')
    mask = sum(1 << (s-8) for s in selection)
    data.need(type(schema.get('masks')) is dict, 'range masks')
    ranges = schema['masks'][str(mask)]
    data.need(type(ranges) is list and len(ranges) <= 8192, 'range list')
    total = 0
    windows, spans = [], []
    for entry in ranges:
        data.need(type(entry) is dict and set(entry) == {'address', 'length'}, 'range entry')
        address, n = entry['address'], entry['length']
        data.need(type(n) is int and 0 < n <= 512, 'range size')
        total += n
        data.need(total <= 8192, 'range budget')
        a = offset(address, n, len(before)); b = a+n
        data.need(not any(a < y and b > x for x,y in spans), 'range overlap')
        spans.append((a,b))
        actual = read_after(a, n)
        data.need(type(actual) is bytes and len(actual) == n, 'bounded read length')
        windows.append(ActualWindow(address, before[a:b], actual))
    checks = integrity.verify(before, tuple(windows), selection, resolved, layout=LAYOUT)
    # integrity checks the complete address coverage against the pinned reference,
    # not the schema file itself or the producer's declarations.
    return checks, tuple((a, w.replacement) for (a,b),w in zip(spans, windows))
