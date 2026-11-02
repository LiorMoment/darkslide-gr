"""Negative Film detail menu: add the grain item (count 9 -> 10, append flag 2 + item 15). Returns the changed payload; no packing."""
import struct
from . import edit_core as builder, models


def validate_request(edit):
    builder._require(type(edit) is dict and set(edit) == {'kind', 'slot', 'mode'} and
                     type(edit['kind']) is str and edit['kind'] == 'grain_menu' and
                     type(edit['slot']) is int and edit['slot'] == 12 and
                     type(edit['mode']) is int and edit['mode'] == 22,
                     'grain requires built-in negative slot12/mode22; custom banks unsupported')


# (descriptor row, item list) of the Negative Film detail menu. 2.20 / IIIx 1.70 hold the same bytes
# at new addresses (checked 2026-10-25).
ADDRESSES = {False: (0x53B348BC, 0x53B3500C), True: (0x53B341E4, 0x53B34934)}


def apply(before, model_id=None):
    model = models.get(model_id)
    builder._require(type(before) is bytes and len(before) == (model.payload_size if models.is_220(model) else builder.PAYLOAD_SIZE) and
                     builder._sha(before) == model.payload_sha, 'immutable official payload required')
    row_va, list_va = ADDRESSES[models.is_220(model)]
    row = row_va - 0x53000000 + 0x10870
    start = list_va - 0x53000000 + 0x10870
    tail = start + 18
    builder._require(before[row:row+8] == struct.pack('<II', list_va, 9),
                     'negative descriptor mismatch')
    builder._require(before[start:tail] == bytes.fromhex('0200020102020203020402050206020d000e'),
                     'negative nine-item list mismatch')
    builder._require(before[tail:tail+10] == b'\x00\x00OnStart\x00',
                     'negative padding/OnStart boundary mismatch')
    changed = bytearray(before)
    changed[row+4:row+8] = struct.pack('<I', 10)
    changed[tail:tail+2] = b'\x02\x0f'
    changes = [dict(what='grain_menu_count', slot=12, offset=row+4, length=4),
               dict(what='grain_menu_item', slot=12, offset=tail, length=2)]
    return bytes(changed), changes
