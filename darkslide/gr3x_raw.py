"""IIIx 1.60 original metadata/RAW-path preservation, independently read back.

Evidence: GRIIIx_160元数据与RAW核对_2026-10-13_v06. This does not write
photo metadata, extend RAW selectors, or assert a complete RAW pixel pipeline.
"""
import hashlib
import struct
from . import models

# Actual code windows and data consumed by the offline-executed stock paths.
WINDOWS=((0x53599df0,0x53599e68),(0x535c780c,0x535c796c),
 (0x535bf7a8,0x535bf7ec),(0x535d02b4,0x535d034c),
 (0x535d0400,0x535d0518),(0x535f4d44,0x535f4d6c),
 (0x53606738,0x53606768),(0x53608074,0x5360808c),
 (0x53607bcc,0x53607be0),(0x53604cb0,0x53604d14),
 (0x536efa28,0x536efbb4),(0x535ee924,0x535ef474),
 (0x53289058,0x53289118),(0x53d2b6f4,0x53d2b724),
 (0x53d2d920,0x53d2d92b),(0x53efd184,0x53efd1c6),
 (0x53efd1c8,0x53efd1e9),(0x55037634,0x550376b8),
 (0x536068a4,0x536068a8),(0x53606fd4,0x53606fd8))
# Vivid uses the stock mode/tag/RAW selector. The separate calibration-table
# index 22 is not a menu index or RAW selector, and remains untouched.
SLOTS={2:(12,0x101,2),8:(18,0x107,9),9:(19,0x108,10),10:(20,0x109,11),11:(21,0x10a,12)}
NOTE=('RAW 已核对原厂槽位读回与分段参数路径；完整机内显影待实机。'
      '照片不保存自定义胶片配方；更换同一槽位的胶片后，旧 RAW 不能据此恢复旧配方。')

def check_preserved(before,after):
    """Both arguments are independent fwread.Payload readers, never writer plans."""
    if type(before.p) is not bytes or hashlib.sha256(before.p).hexdigest()!=models.GR3X.payload_sha:
        raise ValueError('RAW 核对需要官方 GR IIIx 1.60 原件')
    if type(after.p) is not bytes or len(after.p)!=len(before.p):
        raise ValueError('RAW 核对的输出长度错误')
    def read(p,address,size):
        start=p.off(address)
        if size<=0 or p.off(address+size-1)!=start+size-1:
            raise ValueError('RAW 核对地址越界')
        out=p.p[start:start+size]
        if len(out)!=size:raise ValueError('RAW 核对数据不完整')
        return out
    seen=set()
    def preserve(address,size):
        if (address,size) in seen:return
        old=read(before,address,size)
        if old!=read(after,address,size):
            raise ValueError('原厂元数据/RAW 路径被改动：%08X'%address)
        seen.add((address,size))
    for lo,hi in WINDOWS:preserve(lo,hi-lo)
    # The 33-matrix MakerNote payload is separate from the live film records.
    # Preserve its records AND every matrix actually selected by the consumer.
    for index in range(33):
        record=struct.unpack('<I',read(before,0x55037634+4*index,4))[0]
        if not 0x55018e00<=record<=0x55019900-84:raise ValueError('元数据记录范围变化')
        preserve(record,84)
        primary,secondary,variants=struct.unpack('<III',read(before,record,12))
        preserve(primary,18)
        if read(before,0x53efd1c8+index,1)[0]:
            variant=struct.unpack('<H',read(before,0x53efd184+2*index,2))[0]
            if variant>255:raise ValueError('元数据矩阵索引范围变化')
            preserve(variants+4*variant,4)
            secondary=struct.unpack('<I',read(before,variants+4*variant,4))[0]
        preserve(secondary,18)
    return {'protected_ranges':len(seen),'calibration_entries':33,
            'file_formatter_verified':False,'camera_verified':False}

def contract(edits):
    rows=[]
    for edit in edits:
        if edit.get('kind')!='film':continue
        slot=edit['slot']
        if type(slot) is not int or slot not in SLOTS:raise ValueError('不支持的 RAW 胶片槽位')
        tone,code,selector=SLOTS[slot]
        rows.append(dict(slot=slot,film=edit['film'],imaging_mode=tone,
                         maker_note_004f=code,raw_selector=selector))
    return dict(schema=1,model_id=models.GR3X.id,evidence='gr3x-160-metadata-raw-offline-v06',
                slots=sorted(rows,key=lambda row:row['slot']),
                offline_scope='stock-mode-tag-readback-and-segmented-parameter-paths',
                raw_as_shot_selector=0,photo_identity='factory-slot-only',
                custom_recipe_stored_in_photo=False,calibration_021b='original-preserved',
                complete_raw_development_verified=False,external_raw_color_match_verified=False,
                camera_verified=False,note=NOTE)
