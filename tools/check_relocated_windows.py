"""Check that 2.20 / 1.70 feature windows are the 2.10 / 1.60 windows, relocated.

    python3 tools/check_relocated_windows.py film-gr3  <2.10 payload> <2.20 payload>
    python3 tools/check_relocated_windows.py film-gr3x <IIIx 1.60 payload> <IIIx 1.70 payload>

For every slot mask the same edits are planned on both originals. The windows must
pair up one to one at relocated addresses with equal lengths, and in both the
original bytes and the replacement bytes every word must be equal, except:
- a pointer whose relocation is the new word;
- a B/BL with the same condition whose relocated target is the new target;
- a movw/movt pair on one register whose relocated value is the new value.
For targets outside the relocation table (e.g. switch-table cases read from the
original by the writer) both targets must hold the same 16 words once branch offsets,
PC-relative loads, movw/movt immediates and pointers are masked.
Anything else stops the check. Equal pointer-like words whose relocation differs are
listed as warnings for review (they can be plain data). Uses fwaddr's table and the
assembler from the source checkout (clang), never the precompiled bundle under test.
"""
import json
import struct
import sys
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from darkslide import fwaddr, models  # noqa: E402
from darkslide.film_arm_assemble import assemble  # noqa: E402

FILMS = {8: 'portra400', 9: 'pro400h', 10: 'gold200', 11: 'ektar100'}


def is_ptr(w):
    return 0x53000000 <= w < 0x540C0000 or 0x55000000 <= w < 0x56000000


def is_branch(w):
    return (w >> 25) & 7 == 5 and w >> 28 != 0xF


def target(w, at):
    d = w & 0xFFFFFF
    if d & 0x800000:
        d -= 1 << 24
    return at + 8 + 4 * d


def mov_kind(w):
    if w >> 28 == 0xF:
        return None
    if w & 0x0FF00000 == 0x03000000:
        return 'w'
    if w & 0x0FF00000 == 0x03400000:
        return 't'
    return None


def imm16(w):
    return ((w >> 16) & 0xF) << 12 | (w & 0xFFF)


def relocate(x, model):
    with fwaddr.firmware(model):
        return fwaddr.A(x)


READERS = {}


def masked(word):
    if is_ptr(word):
        return 0xD0D00000 | (word >> 24)
    if is_branch(word):
        return word & 0xFF000000
    if mov_kind(word):
        return word & 0xFFF0F000
    rn_pc = (word >> 16) & 0xF == 0xF
    if rn_pc and ((word >> 26) & 3 == 1 or (word >> 25) & 7 == 1):
        return word & 0xFFFFF000
    if mov_kind(word):
        return word & 0xFFF0F000
    return word


def same_target(o, n, model):
    """Relocation of o is n, or (outside the table) both targets hold the same masked 16 words."""
    try:
        return relocate(o, model) == n
    except ValueError:
        pass
    if abs(n - o) > 0x100000:
        return False
    try:
        a, b = READERS['old'](o & ~3, 64), READERS['new'](n & ~3, 64)
    except ValueError:
        return False
    return ([masked(w) for w in struct.unpack('<16I', a)] == [masked(w) for w in struct.unpack('<16I', b)])


def compare(old_at, old, new_at, new, model, where, warnings):
    """Word-level comparison of one byte string pair; returns list of problems."""
    problems = []
    if len(old) != len(new):
        return ['%s: length %d vs %d' % (where, len(old), len(new))]
    if old == new:
        lead = (-old_at) % 4
        for i in range(lead, len(old) - 3, 4):
            w = struct.unpack_from('<I', old, i)[0]
            if is_ptr(w):
                try:
                    moved = relocate(w, model)
                except ValueError:
                    moved = None
                if moved != w:
                    warnings.append('%s+%#x: equal pointer-like word %#010x (relocates to %s)'
                                    % (where, i, w, '%#x' % moved if moved is not None else 'none'))
        return problems
    if (old_at - new_at) % 4:
        return ['%s: word alignment differs' % where]
    lead = (-old_at) % 4
    if old[:lead] != new[:lead] or (len(old) - lead) % 4 and old[len(old) - (len(old) - lead) % 4:] != new[len(new) - (len(new) - lead) % 4:]:
        problems.append('%s: unaligned edge bytes differ' % where)
    n = (len(old) - lead) // 4
    A = struct.unpack_from('<%dI' % n, old, lead)
    B = struct.unpack_from('<%dI' % n, new, lead)
    done = set()
    # movw/movt pairs on one register (the movt within the next 8 words)
    for i, (a, b) in enumerate(zip(A, B)):
        if mov_kind(a) == 'w' and mov_kind(b) == 'w' and (a >> 12) & 15 == (b >> 12) & 15:
            rd = (a >> 12) & 15
            for j in range(i + 1, min(n, i + 9)):
                if mov_kind(A[j]) == 't' and mov_kind(B[j]) == 't' and (A[j] >> 12) & 15 == rd == (B[j] >> 12) & 15:
                    vo, vn = imm16(a) | imm16(A[j]) << 16, imm16(b) | imm16(B[j]) << 16
                    if (a, A[j]) != (b, B[j]):
                        if not same_target(vo, vn, model):
                            problems.append('%s+%#x: movw/movt %#x -> %#x is not its relocation' % (where, lead + 4 * i, vo, vn))
                    done |= {i, j}
                    break
    for i, (a, b) in enumerate(zip(A, B)):
        if a == b or i in done:
            continue
        at_o, at_n = old_at + lead + 4 * i, new_at + lead + 4 * i
        if is_ptr(a) and is_ptr(b) and same_target(a, b, model):
            continue
        if is_branch(a) and is_branch(b) and a >> 24 == b >> 24 and same_target(target(a, at_o), target(b, at_n), model):
            continue
        problems.append('%s+%#x: %#010x -> %#010x not explained by relocation' % (where, lead + 4 * i, a, b))
    return problems


def plans(module, raw, model):
    from darkslide import film_writer
    profile_model = model.id   # film data labelled with this firmware, as the generator does
    out = {}
    with mock.patch.object(module, 'assemble_bundled', lambda s, b, f: assemble(s, b)), fwaddr.firmware(model):
        for mask in range(1, 16):
            slots = [8 + i for i in range(4) if mask >> i & 1]
            edits = [dict(kind='film', slot=s, film=FILMS[s]) for s in slots]
            profiles = film_writer.resolve_profiles(edits, None, profile_model)
            out[mask] = module.plan_offline(raw, edits, profiles)['windows']
    return out


def main(argv):
    if len(argv) != 4 or argv[1] not in ('film-gr3', 'film-gr3x'):
        raise SystemExit(__doc__)
    if argv[1] == 'film-gr3':
        from darkslide import film_inplace_cache as module
        old_model, new_model = models.GR3, models.GR3_220
    else:
        from darkslide import gr3x_inplace as module
        old_model, new_model = models.GR3X, models.GR3X_170
    old_raw, new_raw = Path(argv[2]).read_bytes(), Path(argv[3]).read_bytes()
    from darkslide import fwread
    for key, raw, L in (('old', old_raw, fwread.V210), ('new', new_raw, fwread.V220)):
        payload = fwread.Payload(raw, L, pinned=False)
        READERS[key] = (lambda q: lambda va, n: q.p[q.off(va):q.off(va) + n])(payload)
    old, new = plans(module, old_raw, old_model), plans(module, new_raw, new_model)
    problems, warnings, summary = [], [], {}
    for mask in range(1, 16):
        a, b = sorted(old[mask], key=lambda w: w.address), sorted(new[mask], key=lambda w: w.address)
        if len(a) != len(b):
            problems.append('mask %d: %d vs %d windows' % (mask, len(a), len(b)))
            continue
        moved = sorted(b, key=lambda w: w.address)
        pairs = []
        index = {w.address: w for w in moved}
        for w in a:
            try:
                at = relocate(w.address, new_model)
            except ValueError:
                problems.append('mask %d: window %#x does not relocate' % (mask, w.address)); continue
            if at not in index:
                problems.append('mask %d: no 2.20 window at %#x (from %#x)' % (mask, at, w.address)); continue
            pairs.append((w, index.pop(at)))
        if index:
            problems.append('mask %d: unpaired new windows %s' % (mask, [hex(x) for x in index]))
        for w, v in pairs:
            where = 'mask %d %#x' % (mask, w.address)
            problems += compare(w.address, w.expected, v.address, v.expected, new_model, where + ' original', warnings)
            problems += compare(w.address, w.replacement, v.address, v.replacement, new_model, where + ' replacement', warnings)
        summary[mask] = dict(windows=len(b), bytes=sum(len(w.replacement) for w in b))
    print(json.dumps(dict(check=argv[1], problems=problems[:50], problem_count=len(problems),
                          warnings=sorted(set(warnings))[:80], warning_count=len(set(warnings)),
                          masks=summary), ensure_ascii=False, indent=1))
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main(sys.argv)
