"""Internal v29 complete-payload adapter. Does not grant export permission."""
from . import inplace_generation, fwread, fwaddr
from .film_requests import FilmRequestError, validate_batch

LAYOUT = inplace_generation.LAYOUT


def apply(raw, edits, *, profiles=None):
    requests = validate_batch(edits)
    changes = {str(e['slot']): {'film': e['film']} for e in requests}
    try:
        prepared = inplace_generation.prepare(raw, {'changes': changes}, film_profiles=profiles)
        p = fwread.Payload(raw, fwaddr.layout()) if fwaddr.is_220() else fwread.Payload(raw)
        pending = []
        for w in prepared['windows']:
            if (type(w.address) is not int or type(w.expected) is not bytes or
                    type(w.replacement) is not bytes or
                    not 0 < len(w.expected) == len(w.replacement) <= 512):
                raise FilmRequestError('v29 window shape')
            a, n = p.off(w.address), len(w.replacement)
            b = a + n
            if (p.off(w.address + n - 1) != b - 1 or not 0 <= a < b <= len(raw) - 4 or
                    raw[a:b] != w.expected or any(a < y and b > x for x, y, _ in pending)):
                raise FilmRequestError('v29 window mapping/original/overlap')
            pending.append((a, b, w.replacement))
        out = bytearray(raw)
        for a, b, value in pending:
            out[a:b] = value
        return bytes(out), [dict(offset=a, length=b-a, kind='film') for a, b, _ in pending]
    except (ValueError, KeyError, TypeError) as exc:
        raise FilmRequestError('不扩容胶片完整适配失败：%s' % exc) from exc
