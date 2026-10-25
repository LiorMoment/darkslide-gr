"""Independent GR III 2.10 / GR IIIx 1.60 menu-order readback; no firmware writer imports.

Requests use stable editor slot IDs, never display rows. verify_tables checks
only the seven tables and is a component of combined verification. The stricter
verify_sorting_only also rejects every unrelated payload change. Firmware
container/header checks remain the responsibility of the combined verifier.
"""
import hashlib
import struct

PAYLOAD_SHA256 = '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'
# GR IIIx 1.60 decoded payload: same size, and the seven tables below sit at the same
# offsets with the same factory bytes (each original table is still compared below).
IIIX_PAYLOAD_SHA256 = 'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'
PAYLOAD_SIZE = 59718388
DEFAULT_STILL = (1, 2, 3, 4, 5, 6, 12, 7, 8, 9, 10, 11)
DEFAULT_VIDEO = (1, 2, 3, 5, 6, 12, 7, 8, 9, 11)
# These are file offsets in the decoded 2.10 / IIIx 1.60 payload, not virtual addresses.
STILL_OFFSETS = (0xB69A94, 0xB6983C, 0xB69A0C)
VIDEO_OFFSETS = (0xB69AA4, 0xB69A38, 0xB69BDC)
RAW_OFFSET = 0xB6B1F8
_RAW_ENUM = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
             12: 7, 7: 8, 8: 9, 9: 10, 10: 11, 11: 12}


class MenuOrderError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise MenuOrderError(message)


def _order(values, identities):
    _need(type(values) is list and len(values) == len(identities), 'Invalid order length')
    _need(all(type(v) is int for v in values), 'Slot IDs must be integers')
    _need(set(values) == set(identities), 'Order must contain every allowed slot exactly once')
    return tuple(values)


def validate_request(request):
    """Return immutable (still_slots, video_slots); no implicit ordering policy."""
    _need(type(request) is dict and set(request) == {'schema', 'still_slots', 'video_slots'},
          'Invalid menu-order request fields')
    _need(type(request['schema']) is int and request['schema'] == 1, 'Invalid order schema')
    return (_order(request['still_slots'], DEFAULT_STILL),
            _order(request['video_slots'], DEFAULT_VIDEO))


def _reference(original, candidate):
    _need(type(original) is bytes and len(original) == PAYLOAD_SIZE and
          hashlib.sha256(original).hexdigest() in (PAYLOAD_SHA256, IIIX_PAYLOAD_SHA256),
          'Expected the immutable original GR III 2.10 or GR IIIx 1.60 decoded payload')
    _need(type(candidate) is bytes and len(candidate) == len(original),
          'Candidate payload size or type differs')


def _compare_tables(original, candidate, request):
    """Pure comparator for tests; public entry points additionally pin the source."""
    still, video = validate_request(request)
    _need(type(original) is bytes and type(candidate) is bytes and
          len(original) == len(candidate), 'Payload size or type differs')
    specs = [(o, bytes(s+10 for s in DEFAULT_STILL), bytes(s+10 for s in still))
             for o in STILL_OFFSETS]
    specs += [(o, bytes(s+10 for s in DEFAULT_VIDEO), bytes(s+10 for s in video))
              for o in VIDEO_OFFSETS]
    specs.append((RAW_OFFSET, bytes(range(1,13)), bytes(_RAW_ENUM[s] for s in still)))
    spans, changed = [], 0
    for start, old, wanted in sorted(specs):
        end = start+len(old)
        _need(0 <= start < end <= len(original)-4, 'Menu table bounds')
        _need(original[start:end] == old, 'Original menu table differs')
        _need(candidate[start:end] == wanted, 'Menu table does not match requested slot order')
        changed += sum(a != b for a,b in zip(old,wanted))
        spans.append((start,end))
    return {'scope': 'menu_tables_only', 'tables': 7, 'table_bytes': 78,
            'changed_table_bytes': changed, 'verified_spans': tuple(spans),
            'still_slots': still, 'video_slots': video}


def verify_tables(original, candidate, request):
    """Check all seven tables in a combined payload, not unrelated edit safety.

verified_spans are fixed and returned only after their actual bytes pass. The
combined verifier must independently authorize/check all other modified spans,
the checksum, the container, and overlap with other features.
"""
    _reference(original, candidate)
    return _compare_tables(original, candidate, request)


def _sum32(data):
    _need(len(data) % 4 == 0, 'Unaligned payload')
    return sum(word[0] for word in struct.iter_unpack('<I', data)) & 0xFFFFFFFF


def _compare_sorting_only(original, candidate, request, finalized):
    _need(type(finalized) is bool, 'Finalized must be a boolean')
    result = _compare_tables(original, candidate, request)
    cursor = 0
    for start,end in result['verified_spans']:
        _need(original[cursor:start] == candidate[cursor:start],
              'Unexpected change outside menu tables')
        cursor = end
    tail = len(original)-4 if finalized else len(original)
    _need(original[cursor:tail] == candidate[cursor:tail],
          'Unexpected change outside menu tables')
    if finalized:
        _need(_sum32(original) == 0, 'Original payload checksum')
        _need(_sum32(candidate) == 0, 'Candidate payload checksum')
    result.update(scope='sorting_only_decoded_payload', finalized=finalized,
                  checksum_verified=finalized, outside_authorized_spans_unchanged=True)
    return result


def verify_sorting_only(original, candidate, request, *, finalized=False):
    """Check a sorting-only payload stage or finalized payload.

finalized=False requires even the checksum tail to stay unchanged (table stage).
finalized=True permits only the final four-byte checksum to change, requires
zero 32-bit sums for both payloads, and still rejects other modifications.
No output is created, encoded, downloaded, or approved for camera installation.
"""
    _reference(original, candidate)
    return _compare_sorting_only(original, candidate, request, finalized)
