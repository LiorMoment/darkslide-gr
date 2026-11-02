"""Read-only access to a GR III 2.10 firmware file: unpack the container,
then read factory preset icons and menu strings.

Independent implementation of the firmware container format.
Nothing here writes or modifies firmware.
"""
import hashlib
import struct

MAX_OUTPUT = 128 * 1024 * 1024

# Layout of the decoded GR III 2.10 payload.
V210 = dict(
    payload_sha256="53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703",
    rtos_va=0x53000000, rtos_body=0x10870, rtos_size=0x10B55C0,
    data_lma=0x5407B540, data_ram=0x55000000, data_size=0x3A080,
    icon_index_ram=0x550020FC, icon_body=0x21284D0, icon_size=18354964, icon_max_id=670,
    string_array_ram=0x55002BAC, languages=21, string_ids=785,
)
V210_INPUT_SHA256 = "d6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4"
# GR III 2.20 / GR IIIx 1.70 (rebuilt main system, 2026-10-21). RTOS +0x140 bytes, initialized data
# +0x40; the icon table and ICONBIN are unchanged, ICONBIN only moved with the longer RTOS record; the
# string array moved +8 because the shutdown-image request table before it gained two entries.
# Checked 2026-10-25: all 670 icons and 21 x 784 strings read back identical to 2.10.
V220 = dict(
    payload_sha256="5b475fcc434dec0f12e7956b02b5b38959d599b7a4ff90486263c46816a21b6d",
    rtos_va=0x53000000, rtos_body=0x10870, rtos_size=0x10B5700,
    data_lma=0x5407B640, data_ram=0x55000000, data_size=0x3A0C0,
    icon_index_ram=0x550020FC, icon_body=0x2128610, icon_size=18354964, icon_max_id=670,
    string_array_ram=0x55002BB4, languages=21, string_ids=785,
)
LANG = {"ja": 0, "en": 1, "zh-TW": 18, "zh-CN": 19}


class FirmwareReadError(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise FirmwareReadError(message)


_HEAD = 0x80            # reserved header before the encoded stream
_LITERAL = 0x8000       # tag bit: set = stored frame, clear = compressed frame


def _inflate(src, start, stop, out, limit):
    """Decode one compressed frame body src[start:stop] onto out (references may reach earlier frames)."""
    cur, flags, left = start, 0, 0
    while True:
        if left == 0:
            need(cur + 2 <= stop, "压缩帧在控制字处截断")
            flags = (src[cur] << 8) | src[cur + 1]
            cur, left = cur + 2, 16
        is_ref = flags & 0x8000
        flags, left = (flags << 1) & 0xFFFF, left - 1
        if not is_ref:
            need(cur < stop, "压缩帧在原样字节处截断")
            need(len(out) < limit, "解码数据超过上限")
            out.append(src[cur])
            cur += 1
            continue
        need(cur + 2 <= stop, "压缩帧在回溯记号处截断")
        hi, lo = src[cur], src[cur + 1]
        cur += 2
        back = ((hi >> 3) << 8) | lo
        run = hi & 7
        if run == 7:
            need(cur < stop, "压缩帧在长度扩展处截断")
            run += src[cur]
            cur += 1
        if back == 0:
            need(cur == stop, "压缩帧的结束记号不在帧尾")
            return
        if run == 262:
            while True:
                need(cur < stop, "压缩帧在长度扩展处截断")
                step = src[cur]
                cur += 1
                run += step
                if step != 255:
                    break
        run += 3
        need(back <= len(out), "回溯距离超出已解出的数据")
        need(len(out) + run <= limit, "解码数据超过上限")
        origin = len(out) - back
        if back >= run:
            out += out[origin:origin + run]
        else:
            piece = bytes(out[origin:])
            out += (piece * (run // back + 1))[:run]


def unpack(raw):
    """Decode the encoded stream of a GR III update package and return the payload.

    Written from the project's own format spec. The stream starts after the
    reserved header and is a run of big-endian tagged frames ended by a zero
    tag. Only the stream is decoded here: callers may pass a whole package or
    just header + stream, and check footer, padding and checksums themselves.
    """
    need(isinstance(raw, (bytes, bytearray)), "需要二进制数据")
    size = len(raw)
    need(size >= _HEAD + 2, "数据太短，没有编码流")
    out = bytearray()
    at = _HEAD
    while True:
        need(at + 2 <= size, "编码流在帧标记处截断，没有结束标记")
        tag = (raw[at] << 8) | raw[at + 1]
        at += 2
        if tag == 0:
            break
        body_end = at + (tag & 0x7FFF)
        need(body_end <= size, "帧正文越过数据结尾")
        if tag & _LITERAL:
            need(len(out) + (body_end - at) <= MAX_OUTPUT, "解码数据超过上限")
            out += raw[at:body_end]
        else:
            _inflate(raw, at, body_end, out, MAX_OUTPUT)
        at = body_end
    need(out, "编码流里没有数据")
    return bytes(out)

class Payload:
    def __init__(self, payload, layout=V210, pinned=True):
        # pinned=False reads a modified payload with the same 2.10 layout
        # (used for read-back checks of generated files).
        if pinned:
            need(hashlib.sha256(payload).hexdigest() == layout["payload_sha256"],
                 "解码结果和已知的 GR III 2.10 不一致")
        self.p, self.L = payload, layout
        self._check_section(layout["rtos_body"], b"RTOS\0\0\0\0", layout["rtos_size"])
        self._check_section(layout["icon_body"], b"ICONBIN\0", layout["icon_size"])

    def _check_section(self, body, magic, size):
        head = self.p[body - 16:body]
        need(head[:8] == magic and struct.unpack_from("<I", head, 12)[0] == size, "section header mismatch")

    def off(self, va):
        L = self.L
        if L["data_ram"] <= va < L["data_ram"] + L["data_size"]:
            va = va - L["data_ram"] + L["data_lma"]
        need(L["rtos_va"] <= va < L["rtos_va"] + L["rtos_size"], "address outside RTOS image")
        return va - L["rtos_va"] + L["rtos_body"]

    def u32(self, va):
        return struct.unpack_from("<I", self.p, self.off(va))[0]

    # icons --------------------------------------------------------------
    def icon_descriptor(self, icon_id):
        need(0 < icon_id <= self.L["icon_max_id"], "icon id out of range")
        desc_va = self.u32(self.L["icon_index_ram"] + 4 * icon_id)
        width, height, pixel_index = struct.unpack_from("<HHI", self.p, self.off(desc_va))
        start = self.L["icon_body"] + 4 * pixel_index
        size = 4 * width * height
        need(4 * pixel_index + size <= self.L["icon_size"], "icon pixels outside ICONBIN")
        return dict(icon_id=icon_id, width=width, height=height, payload_offset=start, size=size)

    def icon_pixels(self, icon_id):
        d = self.icon_descriptor(icon_id)
        return d, self.p[d["payload_offset"]:d["payload_offset"] + d["size"]]

    # strings ------------------------------------------------------------
    def string(self, string_id, lang="en"):
        L = self.L
        li = lang if type(lang) is int else LANG[lang]  # a code above, or a menu language index
        need(0 <= li < L["languages"], "language out of range")
        need(0 < string_id < L["string_ids"], "string id out of range")
        table = self.u32(L["string_array_ram"] + 4 * li)
        record = self.u32(table + 4 * string_id)
        need(record != 0, "empty string record")
        o = self.off(record)
        n, lines = self.p[o], self.p[o + 1]
        text_va = self.u32(record + 4)
        t = self.off(text_va)
        text = self.p[t:t + 2 * (n - 1)].decode("utf-16-le")
        need(self.p[t + 2 * (n - 1):t + 2 * n] == b"\0\0", "string not NUL-terminated")
        return dict(text=text, length_byte=n, lines=lines, record_offset=o, text_offset=t)


def icon_to_rgba(raw):
    """Machine icon words (R, G, B, 4th byte per pixel) -> RGBA bytes.
    4th byte 0 is the factory transparent key; everything else shows opaque."""
    out = bytearray(raw)
    for i in range(3, len(out), 4):
        out[i] = 0 if raw[i] == 0 else 255
    return bytes(out)


def rgba_to_icon(rgba):
    """RGBA bytes from a contract-checked 40x40 PNG -> machine icon words.
    Byte order is unchanged (R, G, B, A); alpha must already be 0 or 255."""
    need(len(rgba) == 6400, "icon must be 40x40 RGBA")
    for i in range(3, 6400, 4):
        need(rgba[i] in (0, 255), "icon alpha must be 0 or 255")
    return bytes(rgba)
