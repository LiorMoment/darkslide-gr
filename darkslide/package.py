"""GR III update-file framing, decoding, and deterministic serialization."""
from dataclasses import dataclass
import struct
from . import models

HEADER_BYTES = 128
TRAILER_BYTES = 24
WORD_MODULUS = 1 << 32
RAW_CHUNK = 24576
EXPECTED_LENGTH = 59718388
VERSION = bytes.fromhex('02 0a 15 1b')


class PackageError(ValueError):
    pass


def demand(condition, explanation):
    if not condition:
        raise PackageError(explanation)


def word_total(block):
    demand(len(block) % 4 == 0, '数据长度不是 4 字节的倍数')
    return sum(value for (value,) in struct.iter_unpack('<I', block)) % WORD_MODULUS


def close_words(prefix):
    """Complete a word-aligned prefix with its additive check word."""
    inverse = (WORD_MODULUS - word_total(prefix)) % WORD_MODULUS
    return prefix + inverse.to_bytes(4, 'little')


@dataclass(frozen=True)
class Trailer:
    project: int
    sentinel: int
    operation: int
    stream_length: int
    image_length: int
    check_word: int

    @classmethod
    def read(cls, block):
        demand(len(block) == TRAILER_BYTES, '页脚长度不合法')
        return cls(*(int.from_bytes(block[i:i + 4], 'little') for i in range(0, 24, 4)))

    def values(self):
        return (self.project, self.sentinel, self.operation, self.stream_length,
                self.image_length, self.check_word)

    def prefix(self, encoded_length):
        values = (self.project, self.sentinel, self.operation, encoded_length, self.image_length)
        return b''.join(value.to_bytes(4, 'little') for value in values)


@dataclass(frozen=True)
class Frame:
    stored: bool      # 原样存放的帧（不经压缩）
    source: tuple     # 在编码流中的区间 (起, 止)
    decoded: tuple    # 在解码数据中的区间 (起, 止)


class Window:
    """A bounded cursor; every read has one checked owner."""
    def __init__(self, block, start, limit):
        self.block, self.position, self.limit = block, start, limit

    def take(self, count):
        following = self.position + count
        demand(0 <= count and following <= self.limit, '帧数据不完整')
        value = self.block[self.position:following]
        self.position = following
        return value

    def byte(self):
        return self.take(1)[0]

    def big_word(self):
        return int.from_bytes(self.take(2), 'big')

    @property
    def available(self):
        return self.limit - self.position


def _expand_compressed(source, output, budget):
    complete = False
    while source.available and not complete:
        control = source.big_word()
        for mask in (1 << bit for bit in range(15, -1, -1)):
            if not source.available:
                break
            if control & mask:
                token = source.big_word()
                distance = token >> 8 >> 3 << 8 | (token & 255)
                length = (token >> 8) & 7
                if length == 7:
                    length += source.byte()
                if distance == 0:
                    demand(source.available == 0, '结束记号之后帧里还有数据')
                    complete = True
                    break
                if length == 262:
                    while True:
                        more = source.byte()
                        length += more
                        if more < 255:
                            break
                count = length + 3
                demand(distance <= len(output), '回溯距离超出已解数据')
                demand(len(output) + count <= budget, '解码总量超过页脚声明')
                origin = len(output) - distance
                if count <= distance:
                    output.extend(output[origin:origin + count])
                else:
                    for index in range(count):
                        output.append(output[origin + index])
            else:
                demand(len(output) < budget, '解码总量超过页脚声明')
                output.append(source.byte())
    demand(complete, '压缩帧缺少结束记号')


def raw_frames(block):
    chunks = []
    view = memoryview(block)
    for start in range(0, len(view), RAW_CHUNK):
        part = view[start:start + RAW_CHUNK]
        chunks.extend(((32768 + len(part)).to_bytes(2, 'big'), part.tobytes()))
    return b''.join(chunks)


@dataclass
class Package:
    wire: bytes
    trailer: Trailer
    header: bytes
    auxiliary: bytes
    stream_end: int
    frames: list
    image: bytes

    @classmethod
    def read(cls, wire, model_id=None):
        model = models.get(model_id)
        demand(type(wire) is bytes, '输入必须是不可变的 bytes')
        demand(len(wire) % 4 == 0 and len(wire) - HEADER_BYTES >= TRAILER_BYTES,
               'File extent is invalid')
        trailer_start = len(wire) - TRAILER_BYTES
        trailer = Trailer.read(wire[trailer_start:])
        demand((trailer.project, trailer.sentinel, trailer.operation) ==
               (model.project, 0xa55a5aa5, 2), 'Update identity is unsupported')
        demand(trailer.image_length == (model.payload_size if models.is_220(model) else EXPECTED_LENGTH), '解码数据长度不受支持')
        demand(all(wire[index:index + 4] == model.version for index in (56, 80)), 'Version fields differ')
        demand(word_total(wire) == 0, '整文件校验和不为零')
        end = HEADER_BYTES + trailer.stream_length
        padding = (-end) % 4
        demand(HEADER_BYTES + 2 <= end and end + padding <= trailer_start, '编码流范围不合法')
        demand(wire[end:end + padding] == bytes(padding), '编码流后的对齐区不是零')
        stream = Window(wire, HEADER_BYTES, end)
        output, pieces = bytearray(), []
        while stream.available:
            begin = stream.position
            word = stream.big_word()
            if word == 0:
                demand(stream.available == 0, '编码流结束位置与页脚长度不符')
                demand(len(output) == trailer.image_length, '解码总量与页脚声明不符')
                image = bytes(output)
                demand(word_total(image) == 0, '解码数据校验和不为零')
                return cls(wire, trailer, wire[:HEADER_BYTES], wire[end + padding:trailer_start],
                           end, pieces, image)
            count, plain = word % 32768, word >= 32768
            demand(count > 0 and count <= stream.available, '帧长度不合法')
            part = Window(wire, stream.position, stream.position + count)
            image_begin = len(output)
            if plain:
                demand(len(output) + count <= trailer.image_length, '原样帧超出解码数据范围')
                output.extend(part.take(count))
            else:
                _expand_compressed(part, output, trailer.image_length)
            stream.position = part.limit
            pieces.append(Frame(plain, (begin, stream.position), (image_begin, len(output))))
        raise PackageError('File stream has no end token')

    def replace_image(self, changed, intervals):
        demand(type(changed) is bytes and len(changed) == len(self.image), '修改后数据长度与原件不同')
        demand(word_total(changed) == 0, '修改后数据校验和不为零')
        fragments, rewritten = [], []
        for number, frame in enumerate(self.frames):
            left = frame.decoded[0] if frame.stored else max(frame.decoded[0] - 8191, 0)
            affected = any(begin < frame.decoded[1] and finish > left for begin, finish in intervals)
            if affected:
                fragments.append(raw_frames(changed[frame.decoded[0]:frame.decoded[1]]))
                rewritten.append(number)
            else:
                fragments.append(self.wire[frame.source[0]:frame.source[1]])
        encoded = b''.join(fragments) + bytes(2)
        aligned_stream = encoded + bytes((-len(encoded)) % 4)
        fields = self.trailer.prefix(len(encoded))
        result = close_words(b''.join((self.header, aligned_stream, self.auxiliary, fields)))
        return result, rewritten


def pack_change(official, original, frames, end, footer, changed, declarations):
    """Compatibility adapter sharing one checksum and serialization owner."""
    finished = close_words(changed[:-4])
    rows = [dict(row) for row in declarations]
    if finished[-4:] != original[-4:]:
        rows.append(dict(what='payload_checksum', offset=len(original) - 4, length=4))
    archive = Package(official, Trailer(*footer), official[:HEADER_BYTES],
                      official[end + (-end) % 4:len(official) - TRAILER_BYTES], end, frames, original)
    spans = [(row['offset'], row['offset'] + row['length']) for row in rows]
    output, rewritten = archive.replace_image(finished, spans)
    return output, finished, rows, rewritten
