from . import jpeg_profile
"""Offline shared shutdown slot editor. No camera, container export, or disk writes.

All known shutdown JPEG IDs (five on 2.10 / 1.60, seven on 2.20 / 1.70) resolve to the original Diary descriptor.
User JPEG is independently validated and padded to the original fixed capacity.
"""
import hashlib
import struct
from . import models

PIN = '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'
SIZE = 59718388
TABLE_LMA = 0x5407e0bc
DIARY_DESC = 0x53b5f508
TABLE_INDICES = (1, 2, 9, 10, 11)
REQUEST_IDS = (2, 3, 10, 11, 12)
EXPECTED_DESCRIPTORS = (0x53b5f520, 0x53b5f540, 0x53b5f550, 0x53b5f530,
                        0x53b5f518, 0x53b5f528, 0x53b5f568, 0x53b5f538,
                        0x53b5f560, 0x53b5f510, 0x53b5f558, DIARY_DESC)
PIXEL_OFFSET = 59298689
CAPACITY = 112509
DIARY_SHA = '0ad9a73876f58ef6d380a00e4f17f2584b83ec2a46c11a429a0d52002614e2ef'
DIARY_PATH_VA = 0x53b2298c
L210 = dict(size=SIZE, table_lma=TABLE_LMA, diary_desc=DIARY_DESC, table_indices=TABLE_INDICES,
            request_ids=REQUEST_IDS, expected=EXPECTED_DESCRIPTORS, pixel_offset=PIXEL_OFFSET,
            path_va=DIARY_PATH_VA, rtos_end=0x540b55c0)
# GR III 2.20 / GR IIIx 1.70 (checked 2026-10-26): the request table holds two more entries, 13 and 14
# (A:\Resource\Jpeg\GB_20th.jpg / GB_30th.jpg), and CollaboMode 5 / 6 select them. On the normal
# start-up branch of the boot entry (0x53820480 bl 0x53821A14) the firmware writes CollaboMode 6; another
# boot-mode branch bypasses it (checked 2026-10-26). So the 30th-anniversary route is the one a camera
# on 2.20 normally shows; all seven routes take the shared picture so whichever mode is set shows it.
L220 = dict(size=59798532, table_lma=0x5407e1bc, diary_desc=0x53b5ee38, table_indices=(1, 2, 9, 10, 11, 12, 13),
            request_ids=(2, 3, 10, 11, 12, 13, 14),
            expected=(0x53b5ee58, 0x53b5ee78, 0x53b5ee48, 0x53b5ee68, 0x53b5ee50, 0x53b5ee60, 0x53b5eea8,
                      0x53b5ee70, 0x53b5eea0, 0x53b5ee40, 0x53b5ee98, 0x53b5ee38, 0x53b5ee88, 0x53b5ee90),
            pixel_offset=59378832, path_va=0x53b222b0, rtos_end=0x540b5700)


def layout(model):
    return L220 if models.is_220(model) else L210

class SharedSlotError(ValueError):
    pass

def need(value, message):
    if not value:
        raise SharedSlotError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def off(va, size=4, rtos_end=0x540b55c0):
    need(type(va) is int and type(size) is int and size > 0 and
         0x53000000 <= va and va + size <= rtos_end, 'RTOS address out of bounds')
    return va - 0x53000000 + 0x10870

def jpeg_contract(data):
    try:
        return jpeg_profile.inspect(data)
    except jpeg_profile.ProfileError as exc:
        raise SharedSlotError(str(exc)) from exc

def pad_jpeg(jpeg, capacity=CAPACITY):
    need(type(capacity) is int and capacity == CAPACITY, 'Only the fixed image slot is supported')
    try:
        return jpeg_profile.fill_comments(jpeg, capacity)
    except jpeg_profile.ProfileError as exc:
        raise SharedSlotError(str(exc)) from exc

def apply_shared(decoded, jpeg=None, model_id=None):
    model = models.get(model_id)
    L = layout(model)
    need(type(decoded) is bytes and len(decoded) == L['size'] and sha(decoded) == model.payload_sha,
         'immutable official decoded GR III 2.10 / 2.20 required')
    entries = len(L['expected'])
    t = off(L['table_lma'], 4 * entries, L['rtos_end'])
    need(struct.unpack_from('<%dI' % entries, decoded, t) == L['expected'], 'original JPEG index table')
    diary = off(L['diary_desc'], 8, L['rtos_end'])
    need(decoded[diary:diary + 8] == struct.pack('<HHI', 720, 480, L['path_va']), 'original Diary descriptor')
    pixels = L['pixel_offset']
    need(sha(decoded[pixels:pixels + CAPACITY]) == DIARY_SHA and
         struct.unpack_from('<I', decoded, pixels - 4)[0] == CAPACITY,
         'original largest JPEG resource identity')
    updated = bytearray(decoded)
    edits = []
    for index in L['table_indices']:
        p = t + index * 4
        if L['expected'][index] != L['diary_desc']:
            before = decoded[p:p + 4]
            value = struct.pack('<I', L['diary_desc'])
            updated[p:p + 4] = value
            edits.append(dict(what='jpeg_index_alias', table_index=index,
                              request_id=index + 1, offset=p, length=4,
                              before=before.hex(), after=value.hex()))
    if jpeg is not None:
        padded = pad_jpeg(jpeg)
        if padded != decoded[pixels:pixels + CAPACITY]:
            updated[pixels:pixels + CAPACITY] = padded
            edits.append(dict(what='fixed_diary_jpeg', offset=pixels, length=CAPACITY,
                              input_jpeg_sha256=sha(jpeg), padded_sha256=sha(padded)))
    need(len(updated) == len(decoded), 'payload expansion')
    return bytes(updated), dict(schema='gr3-shared-shutdown-offline-v1',
             input_sha256=model.payload_sha, output_payload_sha256=sha(updated), edits=edits,
             table_indices=list(L['table_indices']), request_ids=list(L['request_ids']),
             shared_descriptor=L['diary_desc'],
             shared_path='B:\\Resource\\Jpeg\\GB_Diary.jpg', capacity=CAPACITY,
             decoded_size_unchanged=True, resource_count_unchanged=True,
             custom_jpeg_applied=jpeg is not None, checksum_updated=False,
             independent_entropy_decode_required=True, loader_verified=False,
             exportable=False, camera_verified=False)
