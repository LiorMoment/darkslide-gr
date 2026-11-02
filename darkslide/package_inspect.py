"""Read-only format validation, independent of the serialization modules."""


def check(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def inspect(raw, model_id=None):
    from . import models
    model = models.get(model_id)
    check(type(raw) is bytes and not len(raw) % 4 and len(raw) >= 152,
          'Invalid update-file extent')
    trailing = len(raw) - 24
    footer = tuple(int.from_bytes(raw[i:i + 4], 'little') for i in range(trailing, len(raw), 4))
    check(footer[:3] == (model.project, 0xa55a5aa5, 2) and footer[4] == model.payload_size,
          'Unexpected update-file identity')
    boundary = 128 + footer[3]
    padding_count = (4 - boundary % 4) % 4
    check(130 <= boundary and boundary + padding_count <= trailing, '页脚编码长度不合法')
    check(not any(raw[boundary:boundary + padding_count]), '对齐字节不是零')
    cursor, produced, ended = 128, 0, False

    def number(count, limit):
        nonlocal cursor
        check(cursor + count <= limit, '编码记号不完整')
        value = int.from_bytes(raw[cursor:cursor + count], 'big')
        cursor += count
        return value

    while cursor < boundary:
        label = number(2, boundary)
        if not label:
            ended = True
            break
        size = label & 32767
        check(size and cursor + size <= boundary, '帧长度不合法')
        limit = cursor + size
        if label >= 32768:
            produced += size
            cursor = limit
        else:
            bits, count, terminated = 0, 0, False
            while cursor < limit:
                if count == 0:
                    bits, count = number(2, limit), 16
                count -= 1
                reference = (bits >> count) & 1
                if reference:
                    code = number(2, limit)
                    distance = (code & 0xf800) >> 3 | code & 255
                    length = (code >> 8) & 7
                    if length == 7:
                        length += number(1, limit)
                    if not distance:
                        check(cursor == limit, '结束记号不在帧尾')
                        terminated = True
                        break
                    if length == 262:
                        more = 255
                        while more == 255:
                            more = number(1, limit)
                            length += more
                    check(distance <= produced, '回溯距离超出已解数据')
                    produced += length + 3
                else:
                    number(1, limit)
                    produced += 1
                check(produced <= footer[4], '解码总量超过页脚声明')
            check(terminated, '压缩帧缺少结束记号')
        check(produced <= footer[4], '解码总量超过页脚声明')
    check(ended and cursor == boundary and produced == footer[4], '编码流结尾或解码总量与页脚不符')
    return footer, raw[boundary + padding_count:trailing], boundary
