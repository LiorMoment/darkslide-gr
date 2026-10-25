from . import package_inspect, verify_languages as VL
"""Independent combined readback. Imports the reader only, never a writer.

Expected bytes are derived from the immutable official file and requested edits;
the generator's declared spans are not used to authorize payload differences.
JPEG padding is accepted only as zero-filled COM segments inserted before SOS.
Entropy/pixel decoding remains the caller's separate image validation step.
"""
import hashlib
import struct
from . import fwread, menu_order_verify, models, soft_glow_verify

IDENTIFIERS = ((358, 4), (548, 268), (354, 269), (357, 270), (350, 271),
               (352, 272), (232, 274), (346, 275), (356, 276), (351, 277),
               (50, 278), (23, 273), (347, 279), (348, 280))
DIARY_PATH = b"B:\\Resource\\Jpeg\\GB_Diary.jpg"


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sum32(data):
    _need(len(data) % 4 == 0, "unaligned sum")
    return sum(x[0] for x in struct.iter_unpack('<I', data)) & 0xffffffff


def _sos(jpeg):
    _need(type(jpeg) is bytes and jpeg[:2] == b'\xff\xd8' and
          jpeg[-2:] == b'\xff\xd9', 'JPEG boundaries')
    pos = 2
    while pos + 4 <= len(jpeg):
        _need(jpeg[pos] == 255, 'JPEG marker')
        marker, length = jpeg[pos + 1], int.from_bytes(jpeg[pos + 2:pos + 4], 'big')
        _need(length >= 2 and pos + 2 + length <= len(jpeg), 'JPEG segment length')
        if marker == 0xda:
            return pos
        _need(marker not in (0xd8, 0xd9, 0x00), 'unexpected JPEG marker')
        pos += 2 + length
    raise ValueError('missing SOS')


def _padding_only(submitted, actual):
    a, b = _sos(submitted), _sos(actual)
    if actual[:a] != submitted[:a] or actual[b:] != submitted[a:]:
        return False
    pos = a
    while pos < b:
        if actual[pos:pos + 2] != b'\xff\xfe' or pos + 4 > b:
            return False
        length = int.from_bytes(actual[pos + 2:pos + 4], 'big')
        end = pos + 2 + length
        if length < 2 or end > b or any(actual[pos + 4:end]):
            return False
        pos = end
    return pos == b


def _resources(payload):
    head = 53123096
    _need(payload[head:head + 8] == b'RES\0\0\0\0\0' and
          struct.unpack_from('<I', payload, head + 12)[0] == 35, 'RES identity')
    rows, at = [], head + 16
    for _ in range(35):
        _need(at + 36 <= len(payload) - 4, 'RES header bound')
        length = struct.unpack_from('<I', payload, at + 32)[0]
        _need(length > 0 and at + 36 + length <= len(payload) - 4, 'RES body bound')
        rows.append((at, at + 36, length, payload[at:at + 32].split(b'\0', 1)[0]))
        at += 36 + length
    _need(at == len(payload) - 4, 'RES tail')
    return rows


def _container(raw, model_id=None):
    return package_inspect.inspect(raw, model_id)


def _longer_name_home(p, string, keep=VL.NEVER_LENT):
    """Where a name longer than the English original goes: among this string's
    records outside ja/en/zh-TW/zh-CN, the longest one-line text whose bytes no
    other record reaches (lowest language on a tie). (record offset, text offset,
    length byte, address) from the read-back module's own view of the tables."""
    L = p.L
    reach, mine = [], []
    for lang in range(21):
        base = p.u32(L['string_array_ram'] + 4 * lang)
        for ident in range(1, L['string_ids']):
            rec = p.u32(base + 4 * ident)
            if rec == 0:
                continue
            at = p.off(rec)
            size, rows = p.p[at], p.p[at + 1]
            text = p.u32(rec + 4)
            reach.append((text, text + 2 * size))
            if ident == string and lang not in keep and rows == 1 and p.p[at + 2:at + 4] == b'\0\0':
                mine.append((size, lang, at, text))
    mine.sort(key=lambda m: (-m[0], m[1]))
    for size, lang, at, text in mine:
        if size >= 2 and [x < text + 2 * size and y > text for x, y in reach].count(True) == 1:
            t = p.off(text)
            if p.p[t + 2 * size - 2:t + 2 * size] == b'\0\0':
                return at, t, size, text
    return None


def _menu_order_expected(before, after, menu_edits):
    """Menu-order tables, re-derived by the independent menu_order_verify module.
    Returns (offset, bytes) windows that the module has checked against the
    request; the caller still owns overlap checks and the whole-payload compare."""
    _need(len(menu_edits) <= 1, 'duplicate menu order request')
    windows = []
    for edit in menu_edits:
        _need(set(edit) == {'kind', 'schema', 'still_slots', 'video_slots'}, 'menu order request')
        request = {k: edit[k] for k in ('schema', 'still_slots', 'video_slots')}
        checked = menu_order_verify.verify_tables(before, after, request)
        _need(checked['scope'] == 'menu_tables_only' and checked['tables'] == 7, 'menu order scope')
        for start, end in checked['verified_spans']:
            windows.append((start, after[start:end]))
    return windows


def verify(official, result, edits, template, jpeg=None, *, film_profiles=None,
           film_layout='legacy_arena_v1'):
    """Return checks; failures are closed and do not authorize a download.

The caller selects the film layout explicitly; output metadata cannot select it.
The four-curve layout requires all four current selections. No BIN is read from
disk here, and passing checks does not change the generation availability gate.
"""
    checks = []

    def add(key, ok, detail=''):
        checks.append(dict(id=key, title=key, ok=bool(ok), detail=detail))

    try:
        model = models.for_template(template)
        _need(type(film_layout) is str and
              film_layout in ('legacy_arena_v1', 'four_curve_v1', 'inplace_history_cache_exit_v29'), 'film layout')
        _need(type(edits) is list and len(edits) <= 30 and (edits or jpeg is not None), 'request')
        _need(all(type(edit) is dict for edit in edits), 'edit object')
        film_edits = [edit for edit in edits if edit.get('kind') == 'film']
        menu_edits = [edit for edit in edits if edit.get('kind') == 'menu_order']
        glow_edits = [edit for edit in edits if edit.get('kind') == 'soft_glow']
        other_edits = [edit for edit in edits if edit.get('kind') not in ('film', 'menu_order', 'soft_glow')]
        _need(len(edits) - len(menu_edits) - len(glow_edits) <= 28, 'request')
        _need(len(glow_edits) <= 1 and all(e == {'kind': 'soft_glow', 'version': 'v22.6'} for e in glow_edits),
              'soft glow request')
        _need(model in (models.GR3, models.GR3X) or not glow_edits, 'soft glow: GR III 2.10 / GR IIIx 1.60 packages only')
        _need(model == models.GR3 or not film_edits, 'IIIx ordinary verifier does not accept film edits')
        # GR IIIx 1.60 holds the seven tables at the GR III 2.10 offsets; menu_order_verify pins both payloads.
        _need(model in (models.GR3, models.GR3X) or not menu_edits, 'menu order: GR III 2.10 / GR IIIx 1.60 packages only')
        _need(len(other_edits) <= 24, 'too many regular edits')
        if film_layout == 'inplace_history_cache_exit_v29':
            from . import film_inplace_complete as film_backend
            profiles = None
        elif film_layout == 'four_curve_v1':
            from . import film_four_verify as film_backend
            profiles = film_backend.resolve_profiles(film_edits, film_profiles)
        else:
            from . import film_verify as film_backend
            profiles = None
        _need(type(official) is bytes and _sha(official) == (fwread.V210_INPUT_SHA256 if model == models.GR3 else model.input_sha),
              'immutable official input')
        out = result['output']
        _need(type(out) is bytes, 'output bytes')
        add('input', True)
        add('output_sha', _sha(out) == result['output_sha256'])
        add('header_version', out[:128] == official[:128])
        add('file_sum', _sum32(out) == 0)
        oldfoot, oldmeta, oldend = _container(official) if model == models.GR3 else _container(official, model.id)
        newfoot, newmeta, newend = _container(out) if model == models.GR3 else _container(out, model.id)
        add('container_bounds_padding', True)
        before, after = fwread.unpack(official[:oldend]), fwread.unpack(out[:newend])
        _need(len(before) == len(after), 'payload size')
        add('independent_unpack', True)
        add('payload_sum', _sum32(after) == 0)
        add('footer_identity', oldfoot[:3] == newfoot[:3] and oldfoot[4] == newfoot[4])
        add('metadata', oldmeta == newmeta)
        p = fwread.Payload(before) if model == models.GR3 else fwread.Payload(before, models.layout(model))
        expected, spans = bytearray(before), []

        def write(at, data):
            end = at + len(data)
            _need(0 <= at < end <= len(before) - 4 and
                  not any(at < y and end > x for x, y in spans), 'expected edit overlap/bounds')
            expected[at:end] = data
            spans.append((at, end))

        _need(type(template) is dict and template.get('reviewed') is True and
              template.get('model') == model.name and template.get('firmware') == model.firmware and
              type(template.get('slots')) is list and len(template['slots']) == 14, 'template')
        seen = set()
        for edit in other_edits:
            _need(type(edit) is dict, 'edit object')
            no, kind = edit['slot'], edit['kind']
            _need(type(no) is int and 1 <= no <= 14 and type(kind) is str and
                  kind in ('icon', 'name', 'grain_menu') and (kind, no) not in seen, 'edit identity')
            seen.add((kind, no))
            icon, string = IDENTIFIERS[no - 1]
            slot = template['slots'][no - 1]
            _need(type(slot['no']) is int and slot['no'] == no and
                  type(slot['icon_id']) is int and slot['icon_id'] == icon and
                  type(slot['string_id']) is int and slot['string_id'] == string, 'template mapping')
            if kind == 'grain_menu':
                _need(set(edit) == {'kind', 'slot', 'mode'} and no == 12 and
                      type(edit['mode']) is int and edit['mode'] == 22, 'grain scope')
                row = p.off(0x53B3488C) + 6 * 8
                start = p.off(0x53B3500C)
                _need(before[row:row+8] == struct.pack('<II', 0x53B3500C, 9) and
                      before[start:start+18] == bytes.fromhex('0200020102020203020402050206020d000e') and
                      before[start+18:start+28] == b'\x00\x00OnStart\x00', 'grain stock layout')
                write(row+4, struct.pack('<I', 10))
                write(start+18, b'\x02\x0f')
            elif kind == 'icon':
                _need(set(edit) == {'kind', 'slot', 'icon_id', 'pixels'} and
                      type(edit['icon_id']) is int and edit['icon_id'] == icon and
                      1 <= no <= 12 and slot['icon'] in ('test', 'open'), 'icon scope')
                info, native = p.icon_pixels(icon)
                px = edit['pixels']
                _need(type(px) is bytes and len(px) == 6400 and len(native) == 6400, 'icon size')
                for y in range(40):
                    for x in range(40):
                        alpha = px[(y * 40 + x) * 4 + 3]
                        _need(alpha in (0, 255) and (alpha != 0) ==
                              (native[(y * 40 + x) * 4 + 3] != 0) ==
                              (1 <= x < 39 and 2 <= y < 38), 'icon alpha')
                    if 2 <= y < 38:
                        at = (y * 40 + 1) * 4
                        write(info['payload_offset'] + at, px[at:at + 152])
            else:
                _need(set(edit) - {'menu_language'} == {'kind', 'slot', 'string_id', 'text'} and
                      type(edit['string_id']) is int and edit['string_id'] == string and
                      2 <= no <= 12 and slot['rename'] in ('test', 'open'), 'name scope')
                en = p.string(string, 'en')
                others = [p.string(string, VL.INDEX[code]) for code in VL.chosen(edit)]
                text = edit['text']
                _need(type(text) is str and 0 < len(text) and
                      all(32 <= ord(c) <= 126 for c in text) and text == text.strip(' ') and
                      '  ' not in text and en['text'] == slot['factory_en'], 'name text')
                value = text.encode('utf-16-le') + b'\0\0'
                if len(text) <= len(en['text']):
                    capacity = en['length_byte'] * 2
                    write(en['text_offset'], value + bytes(capacity - len(value)))
                    write(en['record_offset'], bytes((len(text) + 1,)))
                    pointer = before[en['record_offset'] + 4:en['record_offset'] + 8]
                    for other in others:
                        write(other['record_offset'], bytes((len(text) + 1, 1, 0, 0)) + pointer)
                else:
                    home = _longer_name_home(p, string)
                    _need(home is not None and len(value) <= home[2] * 2 and
                          len(text) <= slot.get('name_max', 0), 'name length')
                    at, t, size, address = home
                    write(t, value + bytes(size * 2 - len(value)))
                    write(at, bytes((len(text) + 1,)))
                    record = bytes((len(text) + 1, 1, 0, 0)) + struct.pack('<I', address)
                    write(en['record_offset'], record)
                    for other in others:
                        if other['record_offset'] != at:
                            write(other['record_offset'], record)
        add('icon_name_expected', True)
        if film_edits and film_layout == 'inplace_history_cache_exit_v29':
            evidence, actual_windows = film_backend.readback(bytes(before), bytes(after), film_edits, film_profiles)
            for field in ('data_verified', 'shared_bytes_match_reference', 'complete_window_coverage_verified'):
                _need(evidence.get(field) is True, 'v29 ' + field)
                add('film_inplace_' + field, True)
            for at, value in actual_windows:
                write(at, value)
            add('film_layout', True, film_layout)
        elif film_edits:
            def read(payload, address, size):
                at = payload.off(address)
                _need(payload.off(address + size - 1) == at + size - 1,
                      'film read crosses mapped segments')
                return payload.p[at:at + size]

            q_film = fwread.Payload(after, pinned=False)
            old_read = lambda address, size: read(p, address, size)
            new_read = lambda address, size: read(q_film, address, size)
            if profiles is None:
                profiles = film_backend.resolve_profiles(film_edits, film_profiles)
            expectation = film_backend.derive_expected(old_read, film_edits, profiles)
            readback = film_backend.readback_windows(old_read, new_read, expectation)
            checks.extend(readback.checks)
            _need(readback.passed, 'film independent readback failed')
            add('film_layout', True, film_layout)
            for window in readback.expected_windows:
                write(window.offset, window.data)
        if menu_edits:
            for at, data in _menu_order_expected(before, after, menu_edits):
                write(at, data)
            add('menu_order_tables', True)
        if glow_edits:
            checked = soft_glow_verify.verify(bytes(before), bytes(after))
            _need(checked['scope'] == 'soft_glow_windows_only', 'soft glow scope')
            for at, data in checked['spans']:
                write(at, data)
            add('soft_glow_windows', True, '%d windows' % checked['windows'])
        row, start = p.off(0x53B3488C) + 6 * 8, p.off(0x53B3500C)
        add('grain_menu_expected', after[row:row+8] == expected[row:row+8] and
            after[start:start+28] == expected[start:start+28])
        q = fwread.Payload(after, pinned=False) if model == models.GR3 else fwread.Payload(after, models.layout(model), pinned=False)
        name_requests = {e['slot']: e for e in edits if e['kind'] == 'name'}
        semantic_ok = True
        for no, (icon, string) in enumerate(IDENTIFIERS, 1):
            info, raw = p.icon_pixels(icon)
            _, got = q.icon_pixels(icon)
            at = info['payload_offset']
            semantic_ok = semantic_ok and got == expected[at:at + len(raw)]
            for language in VL.checked(name_requests.get(no)):
                at = VL.INDEX[language]
                want = (name_requests[no]['text'] if no in name_requests and
                        VL.shows_new_name(name_requests[no], language) else p.string(string, at)['text'])
                semantic_ok = semantic_ok and q.string(string, at)['text'] == want
        add('readback_icons_names', semantic_ok)
        records = _resources(before)
        if jpeg is not None:
            diary = [row for row in records if row[3] == DIARY_PATH]
            _need(len(diary) == 1, 'Diary record')
            head, body, capacity, _ = diary[0]
            _need(capacity == 112509, 'fixed Diary capacity')
            desc = p.u32(0x55002b78 + 4 * 12)
            desc_at = p.off(desc)
            width, height, path_va = struct.unpack_from('<HHI', before, desc_at)
            _need((width, height) == (720, 480), 'Diary descriptor')
            at = p.off(path_va)
            _need(before[at:at + len(DIARY_PATH) + 1] == DIARY_PATH + b'\0', 'Diary path')
            actual = after[body:body + capacity]
            _need(_padding_only(jpeg, actual), 'JPEG altered beyond zero COM padding')
            write(body, actual)
            write(head, b'A')
            write(at, b'A')
            for request in (2, 3, 10, 11, 12):
                write(p.off(0x55002b78 + 4 * request), struct.pack('<I', desc))
            add('a_path_five_requests_fixed_slot_jpeg', True)
        check_value = (2 ** 32 - _sum32(expected[:-4])) % (2 ** 32)
        expected[-4:] = check_value.to_bytes(4, 'little')
        # This exact comparison also protects all other icons, languages, request
        # pointers, RES headers/bodies, RTOS code, and every unedited payload byte.
        add('entire_payload_matches_independent_request', after == expected)
        add('payload_sha', _sha(after) == result['payload_sha256_after'])
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error,
            fwread.FirmwareReadError) as exc:
        add('closed_failure', False, str(exc))
    return checks


def all_ok(checks):
    return type(checks) is list and bool(checks) and all(c.get('ok') is True for c in checks)
