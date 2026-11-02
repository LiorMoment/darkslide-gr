"""A film slot's own still-image adjustment checks must stay quiet.

Background (FINDINGS §23 and the camera test of 2026-10-19 on a GR III HDF): every
Image Control adjustment has one stored value per tone and per set, and each value
is pushed to the camera only through a small "is this tone the active one" check.
The Standard proxy (film_standard_proxy / film_inplace_cache.common_source) makes
Standard's checks also answer yes for an installed film tone, so the film uses
Standard's values. The film tone's own checks were left as they were, so for an
installed film tone both the Standard check and the tone's own check answer yes and
the factory values of the original preset are pushed as well (Bleach Bypass: key
+4, contrast +4, highlight contrast +4). On the camera the original preset's values
won: Bleach Bypass came out about one stop brighter than the fitted film.

This module finds, for each installed film tone, its own still checks (sets No0,
No1, No2) whose adjustment Standard also has, and changes the tone the check
compares against to 0xFF, which no Image Control ever uses. One word per check,
the function's flow is unchanged; the check then always answers no. Adjustments
Standard does not have (toning, HDR strength, Cross Process preset) keep their
original checks: the film pipeline does not read them.

The adjustment a check belongs to is identified by the property calls in the code
it continues into ("tail"): checks of the same adjustment call the same pair.
"""
import struct

from .film_requests import FilmRequestError
from .fwaddr import A as fa

TONES = {2: 12, 8: 18, 9: 19, 10: 20, 11: 21}   # film slot -> Image Control tone
STANDARD = 11
NEVER = 0xFF
RANGE = (0x5325B000, 0x5326B800)        # still-image checks of tones 11..22
# Adjustments shared with Standard, per installed tone and set (checked on 2.10).
EXPECTED = {12: 9, 18: 9, 19: 9, 20: 2, 21: 9}


def _rotate_imm(imm12):
    rot, value = (imm12 >> 8) * 2, imm12 & 0xFF
    return ((value >> rot) | (value << (32 - rot))) & 0xFFFFFFFF if rot else value


def _branch_target(word, at):
    delta = word & 0xFFFFFF
    if delta & 0x800000:
        delta -= 1 << 24
    return at + 8 + delta * 4


def _cond_branch(word, cond):
    return word >> 28 == cond and (word >> 24) & 0xF == 0xA


def _check_at(words, at):
    """(set, tone, index of the compare word, tail) for an exact stock check, else None."""
    w = words
    if w[0] != 0xE5903004 or w[1] != 0xE5D32044 or w[2] != 0xE3520000:
        return None
    # set No0, 12 words: ldr r3,[r3,#0x1c0]; clear the top bytes; cmp r3,#tone<<16
    if (w[3] == 0x1A000004 and w[4] == 0xE59331C0 and w[5] == 0xE3C334FF and w[6] == 0xE3C33CFF
            and w[7] & 0xFFFFF000 == 0xE3530000 and w[8] == 0x0A000001 and w[9] == 0xE3A00000
            and w[10] == 0xE12FFF1E and w[11] >> 24 == 0xEA):
        value = _rotate_imm(w[7] & 0xFFF)
        if value & 0xFFFF:
            return None
        return 0, value >> 16, 7, _branch_target(w[11], at + 44)
    # sets No1/No2, 13 words: ldrb [0x1c0] == set; ldrb r3,[r3,#0x1c2+2*set]; cmp r3,#tone
    if (_cond_branch(w[3], 0x1) and w[4] == 0xE5D321C0 and w[5] in (0xE3520001, 0xE3520002)
            and _cond_branch(w[6], 0x0) and w[7] == 0xE3A00000 and w[8] == 0xE12FFF1E
            and w[9] == 0xE5D33000 | (0x1C2 + 2 * (w[5] & 0xF)) and w[10] & 0xFFFFFF00 == 0xE3530000
            and _cond_branch(w[11], 0x1) and w[12] >> 24 == 0xEA):
        return w[5] & 0xF, w[10] & 0xFF, 10, _branch_target(w[12], at + 48)
    return None


def _signature(read, tail):
    """The property calls of the tail: the same pair means the same adjustment."""
    words = struct.unpack('<24I', read(tail, 96))
    calls = tuple(_branch_target(x, tail + 4 * i) for i, x in enumerate(words) if x >> 24 == 0xEB)
    if len(calls) < 3:
        raise FilmRequestError('调节检查尾部结构不匹配')
    return calls[1:]


def scan(read):
    """Every stock still-image check in RANGE: dicts with at, set, tone, word, tail, signature."""
    start, end = fa(RANGE)
    words = struct.unpack('<%dI' % ((end - start) // 4), read(start, end - start))
    found = []
    for i in range(len(words) - 13):
        hit = _check_at(words[i:i + 13], start + 4 * i)
        if hit:
            bank, tone, index, tail = hit
            at = start + 4 * i + 4 * index
            found.append(dict(at=at, set=bank, tone=tone, word=words[i + index], tail=tail,
                              signature=_signature(read, tail)))
    return found


def _never(row):
    return (row['word'] & 0xFFFFF000) | (0x8FF if row['set'] == 0 else NEVER)


def changes(read, slots):
    """(address, original word, replacement word) for the installed film slots."""
    if type(slots) not in (list, tuple) or any(s not in TONES for s in slots) or len(set(slots)) != len(slots):
        raise FilmRequestError('需要不重复的胶片位置')
    rows = scan(read)
    shared = {}
    for r in rows:
        if r['tone'] == STANDARD:
            shared.setdefault(r['set'], set()).add(r['signature'])
    if sorted(len(v) for v in shared.values()) != [9, 9, 9]:
        raise FilmRequestError('标准调节检查数量不匹配')
    out = []
    for slot in sorted(slots):
        tone = TONES[slot]
        for bank in (0, 1, 2):
            mine = [r for r in rows if r['tone'] == tone and r['set'] == bank and r['signature'] in shared[bank]]
            if len(mine) != EXPECTED[tone] or len({r['signature'] for r in mine}) != len(mine):
                raise FilmRequestError('胶片位置的调节检查数量不匹配')
            out.extend((r['at'], r['word'], _never(r)) for r in mine)
    return tuple(out)
