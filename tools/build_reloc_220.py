"""Build darkslide/data/reloc_220.json: GR III 2.10 / IIIx 1.60 addresses -> 2.20 / 1.70.

    python3 tools/build_reloc_220.py <address map .json> <2.10 payload> <2.20 payload> \
        <IIIx 1.60 payload> <IIIx 1.70 payload>

Payloads are the decoded official files (fwread.unpack). The address map lists every
firmware address DARKSLIDE uses with its checked 2.20 address. This tool keeps the
uniquely checked points and adds segments: two neighbouring points with the same shift
whose words in between agree in both pairs (GR III and IIIx) once branch offsets,
PC-relative loads, movw/movt immediates and pointers are masked (pointer words must also
point at matching code, and a pointer to a string - also into the middle of one - must point
to the identical string, whether or not the pointer word itself changed).
Points are checked the same way (data identity: a point whose words name a string must name
the same string; otherwise it is re-located by identity nearby or dropped). Any address inside
such a segment relocates by that shift;
everything else stays unmapped and fwaddr refuses it. Output holds addresses only.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

RTOS = 0x53000000
RAM = 0x55000000
BODY = 0x10870
OLD = dict(size=0x10B55C0, lma=0x5407B540, dsize=0x3A080)
NEW = dict(size=0x10B5700, lma=0x5407B640, dsize=0x3A0C0)
MAX_SEGMENT = 0x40000


def offset(va, L):
    if RAM <= va < RAM + L['dsize']:
        va = va - RAM + L['lma']
    if not RTOS <= va < RTOS + L['size']:
        return None
    return va - RTOS + BODY


def is_ptr(w):
    return 0x53000000 <= w < 0x540C0000 or 0x55000000 <= w < 0x56000000


def mask(w):
    if is_ptr(w):
        return 0xD0D00000 | (w >> 24)
    if (w >> 25) & 7 == 5:
        return w & 0xFF000000
    if w & 0x0FF00000 in (0x03000000, 0x03400000):
        return w & 0xFFF0F000
    rn_pc = (w >> 16) & 0xF == 0xF
    if rn_pc and ((w >> 26) & 3 == 1 or (w >> 25) & 7 == 1):
        return w & 0xFFFFF000
    if rn_pc and w & 0x0E000090 == 0x90:
        return w & 0xFFFFF0F0
    if w & 0x0F1F0E00 == 0x0D1F0A00:
        return w & 0xFFFFFF00
    return w


def words(p, va, n, L):
    a = offset(va, L)
    if a is None or offset(va + 4 * n - 4, L) != a + 4 * n - 4 or a + 4 * n > len(p):
        return None
    return struct.unpack_from('<%dI' % n, p, a)


def cstring(p, va, L):
    """The NUL-terminated printable ASCII string (>= 4 chars) read from va, else None. A pointer
    into the middle of a longer string (a shared suffix, a substring) counts as well: C reads from
    the pointer to the NUL, so the byte before does not matter."""
    at = offset(va, L)
    if at is None:
        return None
    end = p.find(b'\0', at, at + 256)
    text = p[at:end] if end != -1 else b''
    return text if len(text) >= 4 and all(0x20 <= c < 0x7F for c in text) else None


def code_table_like(p, va, L):
    """Aligned, and the first two words both fall in the code range: the target may be a table of
    code pointers (a C++ vtable) whose bytes happen to be printable ("S" is 0x53), or ordinary text."""
    w = words(p, va, 2, L) if va % 4 == 0 else None
    return w is not None and all(RTOS <= x < 0x53B00000 for x in w)


AMBIGUOUS = []   # targets accepted as code-pointer tables after their text differed (reported)


def string_ok(a, b, pa, pb):
    """A pointer to text must point to the identical text (shape alone cannot tell two paths apart).
    A target that reads as text but may be a code-pointer table is first compared as text; only when
    the text differs is it compared as a table (each word's code target of the same shape), and such
    acceptances are listed in AMBIGUOUS instead of passing silently."""
    sa = cstring(pa, a, OLD)
    if sa is None or cstring(pb, b, NEW) == sa:
        return True
    if code_table_like(pa, a, OLD) and code_table_like(pb, b, NEW):
        A, B = words(pa, a, 2, OLD), words(pb, b, 2, NEW)
        if all(target_ok(x, y, pa, pb) for x, y in zip(A, B)):
            AMBIGUOUS.append((a, b))
            return True
    return False


def target_ok(a, b, pa, pb):
    wa, wb = words(pa, a & ~3, 8, OLD), words(pb, b & ~3, 8, NEW)
    if wa is None or wb is None:
        return a == b
    return [mask(x) for x in wa] == [mask(x) for x in wb]


def segment_ok(pairs, start, end, delta):
    n = (end - start) // 4 + 1
    for pa, pb in pairs:
        A, B = words(pa, start, n, OLD), words(pb, start + delta, n, NEW)
        if A is None or B is None:
            return False
        for x, y in zip(A, B):
            if x == y:
                continue
            if mask(x) != mask(y):
                return False
            if is_ptr(x) and not (target_ok(x, y, pa, pb) and string_ok(x, y, pa, pb)):
                return False
        # equal words that point to strings: the string must still be the same one
        for x, y in zip(A, B):
            if x == y and is_ptr(x) and not string_ok(x, y, pa, pb):
                return False
    return True


DATA_START = 0x53B00000   # read-only data and initialised RAM from here on; code below


def point_identity_ok(pairs, old, new, n=2):
    """The first n words at a point, in both pairs: every string pointer names the same string;
    every other word is equal - by value for a data point (DATA_START and above, RAM), and once
    branch offsets and other relocated instruction fields are masked for a code point. A pointer
    in data must also keep a target of the same shape. (n=2 covers an 8-byte descriptor without
    reading into its neighbour.)"""
    data = old >= DATA_START
    for pa, pb in pairs:
        A, B = words(pa, old & ~3, n, OLD), words(pb, new & ~3, n, NEW)
        if A is None or B is None:
            continue
        for x, y in zip(A, B):
            if is_ptr(x) and cstring(pa, x, OLD) is not None:
                if not (is_ptr(y) and string_ok(x, y, pa, pb)):
                    return False
            elif data:
                if x != y and not (is_ptr(x) and is_ptr(y) and target_ok(x, y, pa, pb)):
                    return False
            elif mask(x) != mask(y):
                return False
    return True


def relocate_by_identity(pairs, old, near, n=2):
    """Search +-8 KB around `near` for the one place where non-pointer words are equal and
    every string pointer names the same string, in both pairs. None when not unique."""
    found = None
    pa0, pb0 = pairs[0]
    A = words(pa0, old & ~3, n, OLD)
    for d in range(-0x2000, 0x2004, 4):
        cand = (near & ~3) + d
        ok = True
        for pa, pb in pairs:
            X, Y = words(pa, old & ~3, n, OLD), words(pb, cand, n, NEW)
            if X is None or Y is None:
                ok = False; break
            for x, y in zip(X, Y):
                if is_ptr(x):
                    if not is_ptr(y) or not string_ok(x, y, pa, pb) or (cstring(pa, x, OLD) is None and not target_ok(x, y, pa, pb)):
                        ok = False; break
                elif x != y:
                    ok = False; break
            if not ok:
                break
        if ok and any(is_ptr(x) and cstring(pa0, x, OLD) for x in A):
            if found is not None:
                return None
            found = cand + (old & 3)
    return found


# Addresses DARKSLIDE uses that the address map did not list (its inventory skipped _vendor/):
# located here by a unique masked signature, identical in both firmware pairs.
EXTRA = (0x53050ECC, 0x5313FBB8, 0x536AF2A8, 0x536AF2E4, 0x536AF31C, 0x536AF358, 0x536B82D4,
         0x536B93D0, 0x53DBD8A0,
         # 柔光 v22.6 sources (C modules and build scripts)
         0x531A0048, 0x531C4D20, 0x531C4D68, 0x531C4DB0, 0x531C4DF8, 0x531C4E40, 0x536ABD28,
         0x53B5E278, 0x53B5E360, 0x53E030A4, 0x536A26F0, 0x5383C8A8, 0x5383C870, 0x536A248C,
         0x5339E8B0, 0x533A6AD0, 0x53394758, 0x533A1808, 0x536AE71C, 0x536AE870, 0x5339E7A0,
         0x5339DC70, 0x5339DE6C, 0x5362E294, 0x5362D478)


def locate(pairs, old):
    """New address of `old`: one masked match within 128 KB, the same in both pairs, else None."""
    found = set()
    for pa, pb in pairs:
        hit = None
        for k in (8, 16, 32, 64):
            win = words(pa, old - 4 * k, 2 * k, OLD)
            if win is None:
                return None
            want = [mask(w) for w in win]
            if len(set(want)) < k // 2:
                continue
            hits = []
            for d in range(-0x20000, 0x20004, 4):
                cand = words(pb, old - 4 * k + d, 2 * k, NEW)
                if cand is not None and mask(cand[k]) == want[k] and [mask(w) for w in cand] == want:
                    hits.append(old + d)
                    if len(hits) > 1:
                        break
            if len(hits) == 1:
                hit = hits[0]
                break
            if not hits:
                return None
        if hit is None:
            return None
        found.add(hit)
    return found.pop() if len(found) == 1 else None


# Near-identical functions a masked signature cannot tell apart, located through the original
# pointer table that names them (table entry -> function), then checked by masked body.
VIA_POINTER = {0x531C4D20 + 0x48 * k: (0x53B3FED0, 8 * k) for k in range(5)}


def locate_via_pointer(pairs, points, old):
    table, step = VIA_POINTER[old]
    if table not in points:
        return None
    found = set()
    for pa, pb in pairs:
        if words(pa, table + step, 1, OLD) != (old,):
            return None
        new = words(pb, points[table] + step, 1, NEW)[0]
        a, b = words(pa, old, 16, OLD), words(pb, new, 16, NEW)
        if a is None or b is None or [mask(w) for w in a] != [mask(w) for w in b]:
            return None
        found.add(new)
    return found.pop() if len(found) == 1 else None


def main(argv):
    if len(argv) != 6:
        raise SystemExit(__doc__)
    rows = json.loads(Path(argv[1]).read_text(encoding='utf-8'))
    p210, p220, p160, p170 = (Path(x).read_bytes() for x in argv[2:6])
    shas = [hashlib.sha256(p).hexdigest() for p in (p210, p220, p160, p170)]
    for p, L in ((p210, OLD), (p220, NEW), (p160, OLD), (p170, NEW)):
        if struct.unpack_from('<I', p, BODY - 4)[0] != L['size']:
            raise SystemExit('payload layout mismatch')
    points = {}
    for r in rows:
        if not r['status'].startswith('唯一') or r.get('new') is None:
            continue
        old, new = r['old'], r['new']
        if points.get(old, new) != new:
            raise SystemExit('conflicting map rows for %#x' % old)
        points[old] = new
    pairs = ((p210, p220), (p160, p170))
    missing = []
    for old in EXTRA:
        if old in points:
            continue
        new = locate_via_pointer(pairs, points, old) if old in VIA_POINTER else locate(pairs, old)
        if new is None:
            print('extra address %#x: no unique match in both pairs' % old)
            missing.append(old)
            continue
        print('extra %#x -> %#x (%+#x)' % (old, new, new - old))
        points[old] = new
    if missing:
        raise SystemExit('%d extra addresses not located' % len(missing))
    # Data identity: a point whose words name a string must name the same string after relocation.
    fixed, dropped = [], []
    for old in sorted(points):
        new = points[old]
        if point_identity_ok(pairs, old, new):
            continue
        better = relocate_by_identity(pairs, old, new)
        if better is not None and point_identity_ok(pairs, old, better):
            fixed.append((old, new, better)); points[old] = better
        else:
            dropped.append((old, new)); del points[old]
    for old, new, better in fixed:
        print('identity fix %#x: %#x -> %#x' % (old, new, better))
    for old, new in dropped:
        print('identity drop %#x (was %#x)' % (old, new))
    ordered = sorted(points.items())
    segments = []
    for (a, na), (b, nb) in zip(ordered, ordered[1:]):
        d = na - a
        if nb - b != d or b - a > MAX_SEGMENT or (a < RAM) != (b < RAM):
            continue
        if segment_ok(pairs, a & ~3, b & ~3, d):
            segments.append([a, b, d])
    merged = []
    for a, b, d in segments:
        if merged and merged[-1][1] == a and merged[-1][2] == d:
            merged[-1][1] = b
        else:
            merged.append([a, b, d])
    out = dict(schema=1, note='GR III 2.10 / IIIx 1.60 address -> 2.20 / 1.70; addresses only',
               identity_fixes=[[a, b, c] for a, b, c in fixed], identity_drops=[[a, b] for a, b in dropped],
               text_or_code_table=sorted({tuple(x) for x in AMBIGUOUS}),
               payload_sha256=dict(gr3_210=shas[0], gr3_220=shas[1], gr3x_160=shas[2], gr3x_170=shas[3]),
               points=[[a, b] for a, b in ordered], segments=merged)
    target = Path(__file__).resolve().parent.parent / 'darkslide' / 'data' / 'reloc_220.json'
    target.write_text(json.dumps(out, separators=(',', ':')) + '\n', encoding='utf-8')
    covered = sum(b - a + 4 for a, b, _ in merged)
    print('%d points, %d segments covering %d bytes -> %s' % (len(ordered), len(merged), covered, target))
    print('targets accepted as code-pointer tables after their text differed: %d' % len(set(AMBIGUOUS)))


if __name__ == '__main__':
    main(sys.argv)
