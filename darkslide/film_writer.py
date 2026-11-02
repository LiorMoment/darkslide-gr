"""Selected film writes and hook plans. Full export remains behind the availability gate."""
import copy
import struct
from .film_requests import validate_batch,available,FilmRequestError
from .film_profiles import load_profile,validate_profile
from .film_patch_plan import PlanImage,from_official_payload,_branch
from .film_arm_assemble import assemble
from ._vendor.gr3_film_hooks import csp_src,csp_entry
from .fwaddr import A as fa
from . import fwaddr

RECORDS=0x55018d94
STD_RECORD=0x5501900c
CURVE_LIB=0x55037604
ARENA,ARENA_END=0x53d36400,0x53d37400
FILL,CSP=0x536ef61c,0x536ef6fc
FILL_END,CSP_END=0x536ef6dc,0x536ef998
SLOT_INFO={8:(18,(18,22,23)),9:(19,(19,)),10:(20,(20,24)),11:(21,(21,))}
CODE_RANGES=((FILL,FILL_END),(CSP,CSP_END),(0x536ef618,0x536ef61c),
             (0x536dd450,0x536dd458),(0x536e4774,0x536e4778))
MAX_LOCAL_BYTES=16384

def selective_fill_source(stock,table,ret):
    # Adapted from gr3-film-mods make_slots.py stock-pointer restoration and
    # original-Cross fallback. Local tone lookup replaces added-mode enum lookup.
    return f"""
        stmdb sp!, {{r7}}
        movw r0, #{(fa(CURVE_LIB)+36)&65535:#x}
        movt r0, #{(fa(CURVE_LIB)+36)>>16:#x}
        movw r2, #{stock&65535:#x}
        movt r2, #{stock>>16:#x}
        ldm r2, {{r4,r5,r7}}
        stm r0, {{r4,r5,r7}}
        movw r2, #{table&65535:#x}
        movt r2, #{table>>16:#x}
    L:  ldr r4, [r2]
        cmp r4, #0
        beq D
        cmp r4, r1
        addeq r4, r2, #4
        beq S
        add r2, r2, #16
        b L
    S:  ldm r4, {{r5,r8,r9}}
        stm r0, {{r5,r8,r9}}
        add r3, r3, #0x38000
        mov r2, #4
        strb r2, [r3, #0xa98]
        ldmia sp!, {{r7}}
        .word {ret:#x}
    D:  ldmia sp!, {{r7}}
        cmp r1, #21
        beq abs({fa(0x536dd4b8):#x})
        b abs({fa(0x536dd458):#x})
    """

def _movw(rd,value):
    return 0xe3000000|((value>>12)&15)<<16|rd<<12|(value&0xfff)

def _movt(rd,value):
    return _movw(rd,value>>16)|0x400000

def _init_tail(reader):
    # movw/movt ip, #<stock tail 0x53dc27a4>: the immediate follows the relocated address.
    guards={fa(0x536eb0dc):_movw(12,fa(0x53dc27a4)&0xffff),fa(0x536eb0e0):_movt(12,fa(0x53dc27a4)),
            fa(0x536eb160):0xe89c000f,fa(0x536eb164):0xe59f6098,
            fa(0x536eb168):0xe59f5098,fa(0x536eb16c):0xe59f4098,
            fa(0x536eb170):0xe59fe098,fa(0x536eb174):0xe59f7098,
            fa(0x536eb178):0xe887000f,fa(0x536eb214):fa(STD_RECORD)+68}
    for address,expected in guards.items():
        if int.from_bytes(reader(address,4),"little")!=expected:
            raise FilmRequestError("标准记录初始化结构不匹配")
    return reader(fa(0x53dc27a4),16)

HDR_FIXES=((0x536dbeb4,0xe5d39788,0xe3a09000),
           (0x536dc27c,_branch(0x536dc27c,0x536dc818,condition=0),0xe1a00000),
           (0x536dbebc,0xe5d32796,0xe3a02000),
           (0x535131fc,_branch(0x535131fc,0x53513274,condition=0),_branch(0x535131fc,0x53513218,condition=0)))
RETRO_FIXES=((0x536dbe98,0xe1ce40b0,0xe1ce10b0),)

def hdr_fixes():
    """HDR_FIXES for the current firmware: branch words are recomputed at the relocated addresses."""
    return ((fa(0x536dbeb4),0xe5d39788,0xe3a09000),
            (fa(0x536dc27c),_branch(fa(0x536dc27c),fa(0x536dc818),condition=0),0xe1a00000),
            (fa(0x536dbebc),0xe5d32796,0xe3a02000),
            (fa(0x535131fc),_branch(fa(0x535131fc),fa(0x53513274),condition=0),_branch(fa(0x535131fc),fa(0x53513218),condition=0)))

def plan(reader,edits,profiles):
    requests=validate_batch(edits)
    if any(e['slot'] not in SLOT_INFO for e in requests):
        raise FilmRequestError('此旧写入布局不支持所选胶片位置')
    if not requests:return {"windows":(),"selected_slots":(),"records":[],"complete_image_created":False}
    if not callable(reader) or type(profiles) is not dict:
        raise FilmRequestError("需要只读原件和胶片数据")
    source=reader
    def reader(address,n):
        if type(address) is not int or type(n) is not int or not 0<n<=512 or not 0<=address<address+n<=2**32:
            raise FilmRequestError("胶片原件读取越界")
        value=source(address,n)
        if type(value) is not bytes or len(value)!=n:
            raise FilmRequestError("胶片原件读取不完整")
        return value
    try:
        resolved={edit["slot"]:validate_profile(profiles[edit["film"]],edit["film"]) for edit in requests}
    except KeyError as error:
        raise FilmRequestError("所选胶片的相机数据尚未就绪") from error
    library=[int.from_bytes(reader(fa(CURVE_LIB)+4*i,4),"little") for i in range(12)]
    ordered=sorted(library)
    if any(not fa(0x53ef0000)<=pointer<=fa(0x53f00000)-512 or pointer%2 for pointer in library) or any(b-a<512 for a,b in zip(ordered,ordered[1:])):
        raise FilmRequestError("原厂曲线库布局不匹配")
    template=reader(fa(STD_RECORD),84)
    if type(template) is not bytes or len(template)!=84:raise FilmRequestError("标准记录读取失败")
    tail=_init_tail(reader)
    ret=int.from_bytes(reader(fa(0x536dd464),4),"little")
    if ret!=0xe89daff8:raise FilmRequestError("曲线填充函数返回结构不匹配")
    selected={edit["slot"] for edit in requests}
    fixes=list(hdr_fixes() if 10 in selected else ())+list(fa(RETRO_FIXES) if 9 in selected else ())
    allowed=list(fa(CODE_RANGES))+[(fa(ARENA),fa(ARENA_END)),(fa(0x536dd454),fa(0x536dd458))]
    allowed.extend((at,at+4) for at,old,new in fixes)
    for edit in requests:
        for index in SLOT_INFO[edit["slot"]][1]:allowed.append((fa(RECORDS)+4*index,fa(RECORDS)+4*index+4))
        if edit["slot"] in (10,11):
            first=0 if edit["slot"]==10 else 3
            allowed.extend((library[i],library[i]+512) for i in range(first,first+3))
    image=PlanImage(reader,tuple(allowed),max_bytes=MAX_LOCAL_BYTES)
    cursor=fa(ARENA)
    def allocate(size):
        nonlocal cursor
        a=cursor;cursor+=(size+3)&~3
        if cursor>fa(ARENA_END):raise FilmRequestError("胶片私有数据空间不足")
        return a
    def write_zero(address,blob):
        if any(reader(address,len(blob))):raise FilmRequestError("胶片私有存放区域不是原厂空白")
        image.write(address,blob)
    pointers=[];posts=[];records=[]
    for edit in requests:
        slot=edit["slot"];tone,indices=SLOT_INFO[slot];profile=resolved[slot]
        record=allocate(84);matrix=allocate(18)
        clone=bytearray(template);clone[4:8]=matrix.to_bytes(4,"little");clone[68:84]=tail
        write_zero(record,bytes(clone))
        write_zero(matrix,struct.pack("<9h",*[v for row in profile["look_matrix_q13"] for v in row]))
        curve_ptr=[]
        for channel,curve in enumerate(profile["curves_u14"]):
            if slot==10:at=library[channel]
            elif slot==11:at=library[channel+3]
            else:at=allocate(512)
            blob=struct.pack("<256H",*curve)
            if slot in (8,9):write_zero(at,blob)
            else:image.write(at,blob)
            curve_ptr.append(at)
        for index in indices:image.write(fa(RECORDS)+4*index,record.to_bytes(4,"little"))
        pointers.append((tone,curve_ptr));posts.append(csp_entry(record,profile["post_matrix_q512"]))
        records.append({"slot":slot,"film":edit["film"],"record":record,"matrix":matrix,"curves":tuple(curve_ptr)})
    stock_at=allocate(12);write_zero(stock_at,struct.pack("<3I",*library[9:12]))
    table=struct.pack("<"+"I"*(4*(len(pointers)+1)),*[v for tone,ptrs in pointers for v in [tone,*ptrs]],0,0,0,0)
    table_at=allocate(len(table));write_zero(table_at,table)
    post_blob=b"".join(posts)+csp_entry(0,[[512,0,0],[0,512,0],[0,0,512]])
    post_at=allocate(len(post_blob));write_zero(post_at,post_blob)
    fill_code=assemble(selective_fill_source(stock_at,table_at,ret),fa(FILL))
    post_code=assemble(csp_src(post_at),fa(CSP))
    if len(fill_code)>fa(FILL_END)-fa(FILL) or len(post_code)>fa(CSP_END)-fa(CSP):
        raise FilmRequestError("胶片代码超出指定区域")
    for at,old,new in [(fa(0x536ef618),0xeddf0be0,_branch(fa(0x536ef618),fa(0x536ef6dc))),
                       (fa(0x536dd450),0x159632d0,0xe59632d0),
                       (fa(0x536dd454),_branch(fa(0x536dd454),fa(0x536dd4b8),condition=0),_branch(fa(0x536dd454),fa(FILL))),
                       (fa(0x536e4774),_branch(fa(0x536e4774),fa(0x536e4720),link=True),_branch(fa(0x536e4774),fa(CSP),link=True))]:
        image.guarded_word(at,old,new)
    for at,old,new in fixes:image.guarded_word(at,old,new)
    image.write(fa(FILL),fill_code);image.write(fa(CSP),post_code)
    return {"windows":image.seal(),"selected_slots":tuple(edit["slot"] for edit in requests),
            "records":records,"arena_used":cursor-fa(ARENA),"curve_points":256,
            "original_cross_chain_preserved":True,"complete_image_created":False,
            "native_effect_verified":False}

def resolve_profiles(edits, supplied=None, model_id=None):
    """Firmware boundary: receive Cloud's fitted data, never read a user LUT.

    User profiles use user_lut_ IDs. They cannot replace bundled film IDs or
    authorize additional writes. No requests retain the existing no-read path.
    """
    requests=validate_batch(edits)
    if supplied is None:supplied={}
    if type(supplied) is not dict:raise FilmRequestError("拟合参数必须是编号到参数的映射")
    requested={e["film"] for e in requests}
    if any(type(key) is not str or key not in requested or not key.startswith("user_lut_") for key in supplied):
        raise FilmRequestError("自定义拟合参数不能覆盖内置胶片或未选位置")
    result={}
    for edit in requests:
        ident=edit["film"]
        if ident.startswith("user_lut_"):
            if ident not in supplied:raise FilmRequestError("缺少用户 LUT 的拟合参数")
            result[ident]=validate_profile(supplied[ident],ident,model_id)
        else:result[ident]=load_profile(ident,model_id)
    return result

def plan_official_payload(raw,edits,*,profiles=None):
    requests=validate_batch(edits)
    if not requests:return {"windows":(),"selected_slots":(),"records":[],"complete_image_created":False}
    return _plan_resolved(raw,requests,resolve_profiles(list(requests),profiles))

def _plan_resolved(raw,requests,profiles):
    """Plan from profiles that were already resolved (and, in apply, already range-checked)."""
    try:
        source=from_official_payload(raw,((fa(ARENA),fa(ARENA_END)),))
        return plan(source.reader,list(requests),profiles)
    except (ValueError,OSError) as error:
        raise FilmRequestError("胶片写入计划未通过原件核对："+str(error)) from error

def apply(raw,edits,*,profiles=None):
    requests=validate_batch(edits)
    if not requests:return raw,[]
    if not available():raise FilmRequestError("胶片写入尚未开启")
    from .film_gate import require_inside
    # one defensive snapshot: the profiles that pass the factory-range gate are the ones planned
    snapshot=copy.deepcopy(resolve_profiles(list(requests),profiles))
    require_inside(raw,snapshot)
    result=_plan_resolved(raw,list(requests),snapshot)
    from .fwread import Payload
    p=Payload(raw,fwaddr.layout()) if fwaddr.is_220() else Payload(raw);pending=[];spans=[]
    for window in result["windows"]:
        a=p.off(window.address);b=a+len(window.replacement)
        if p.off(window.address+len(window.replacement)-1)!=b-1 or b>len(raw)-4:
            raise FilmRequestError("胶片写入越界")
        if any(a<hi and b>lo for lo,hi in spans):raise FilmRequestError("胶片写入地址别名冲突")
        if raw[a:b]!=window.expected:raise FilmRequestError("胶片原字变化")
        spans.append((a,b));pending.append((a,b,window.replacement))
    changed=bytearray(raw)
    for a,b,value in pending:changed[a:b]=value
    changes=[{"offset":a,"length":b-a,"kind":"film"} for a,b,value in pending]
    return bytes(changed),changes
