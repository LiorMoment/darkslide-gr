"""Offline v29 candidate: reclaim duplicate MultiAxial data, retain random code/curves.
Zero-table relocation follows the attributed MIT make_slots16.py implementation.
Matrix template reconstruction is local code; it uses the original per-object
0x870-byte workspace, no new allocation/global buffer; reuses the original object workspace. Not flashable.
"""
import hashlib,struct
from .film_precompiled import assemble_bundled
from .film_patch_plan import PlanImage,_branch
from .film_scheme_c import _getter_source,_post_source,_composer_source,MODE_CHANGE_WORDS
from .film_extension import _numeric,_envelope
from .film_requests import validate_batch,FilmRequestError
from .film_profiles import validate_profile
from .film_writer import _init_tail,SLOT_INFO,HDR_FIXES,hdr_fixes
from .film_standard_proxy import derive,ACTIVE,mask_slots
from . import film_own_checks
from .fwread import Payload
from .factory_envelope import factory_envelope_from_payload
from ._vendor.gr3_four_curve_hooks import point_source
from ._vendor.gr3_film_hooks import csp_entry
from .fwaddr import A as fa
from . import fwaddr

LAYOUT='inplace_history_cache_exit_v29'

CODE=((0x53dc3218,0x53dc3a40),)
DATA=((0x53dc2bf8,0x53dc3198),)
ZERO_SOURCE=0x53d36400
ZERO_OBJECT=0x55019848
MATRIX_TEMPLATE=0x53dc31d0

def need(v,m):
    if not v:raise ValueError(m)

def data_layout():
    base=fa(DATA)[0][0]
    slots=[]
    for i in range(4):
        slots.append(dict(slot=8+i,record=base+i*84,matrix=base+336+i*20,
            knots=[base+480+(i*3+j)*66 for j in range(3)],
            lookup=base+416+i*16,post=base+1272+i*24))
    return slots,base+416,base+1272

def _ma_copy_source():
    # Adapt only a source exactly matching the original repeated table.
    # Other memcpy requests keep the original function and ABI.
    return f'''
        movw ip, #{fa(MATRIX_TEMPLATE) & 65535}
        movt ip, #{fa(MATRIX_TEMPLATE) >> 16}
        cmp r1, ip
        bne abs({fa(0x5313fd80):#x})
        cmp r2, #0x870
        bne abs({fa(0x5313fd80):#x})
        push {{r4-r11}}
        mov ip, r0
        ldm r1!, {{r3-r11}}
        stm r0!, {{r3-r11}}
        ldm r1, {{r3-r11}}
        mov r2, #59
    M:  stm r0!, {{r3-r11}}
        subs r2, r2, #1
        bne M
        mov r0, ip
        pop {{r4-r11}}
        bx lr
    '''

def _ma_ctor_source(copy_at):
    # Replace the original zero-fill at the end of the constructor. Populate
    # the same private buffer and publish a valid source before ctor returns.
    return f'''
        movw ip, #{fa(MATRIX_TEMPLATE) & 65535}
        movt ip, #{fa(MATRIX_TEMPLATE) >> 16}
        cmp r5, ip
        bne abs({fa(0x531403a8):#x})
        push {{r4,lr}}
        movw r1, #{fa(MATRIX_TEMPLATE) & 65535}
        movt r1, #{fa(MATRIX_TEMPLATE) >> 16}
        bl abs({copy_at:#x})
        str r0, [r4, #8]
        pop {{r4,pc}}
    '''

def _ma_default_source(copy_at):
    # Original store did not change flags or r0-r2; preserve that contract.
    return f'''
        push {{r0-r2,r4,ip,lr}}
        mrs r4, cpsr
        movw ip, #{fa(MATRIX_TEMPLATE) & 65535}
        movt ip, #{fa(MATRIX_TEMPLATE) >> 16}
        cmp r3, ip
        bne D
        mov r1, r3
        mov r2, #0x870
        add r0, r0, #0x18
        bl abs({copy_at:#x})
        mov r3, r0
    D:  msr cpsr_f, r4
        pop {{r0-r2,r4,ip,lr}}
        str r3, [r0, #8]
        b abs({fa(0x536df4dc):#x})
    '''


def packing(sizes):
    order=sorted(sizes,key=lambda k:(-sizes[k],k))
    def search(i,cursors,chosen):
        if i==len(order):return chosen
        key=order[i];n=sizes[key]
        for j,(lo,hi) in enumerate(fa(CODE)):
            at=(cursors[j]+3)&~3
            if at+n<=hi:
                c=list(cursors);c[j]=at+n
                out=search(i+1,c,{**chosen,key:at})
                if out is not None:return out
        return None
    result=search(0,[a for a,b in fa(CODE)],{})
    need(result is not None,'fragmented code does not fit')
    return result


def _dirty_source(mask):
    # The factory already computes tone-change before overwriting its cached
    # tone. Leaving a film must also run Gamma once to replace its cached RGB.
    # No new persistent state: use the guarded FP-0xCC boolean for all changes.
    # Nonempty plans only; unchanged uninstalled tones follow the native bit.
    return f'''
        push {{r0,r2,r3}}
        mrs r3, APSR
        ldr r0, [r11, #-0xcc]
        cmp r0, #0
        bne S
        ldrb r2, [r4, #0x1a8]
        sub r2, r2, #18
        cmp r2, #4
        bhs D
        mov r0, #{mask}
        mov r0, r0, lsr r2
        tst r0, #1
        beq D
    S:  orr r1, r1, #1
    D:  msr APSR_nzcvq, r3
        pop {{r0,r2,r3}}
        strb r1, [r4, #0xe]
        b abs({fa(0x536dd028):#x})
    '''

def _curve_gate_source(table):
    # Keep the factory selector/history intact. Only this Gamma invocation
    # selects custom curves, by the installed private record identity.
    return f'''
        push {{r0-r2,ip}}
        mrs ip, APSR
        ldr r0, [r4, #8]
        movw r1, #{table & 65535}
        movt r1, #{table >> 16}
        mov r2, #4
    L:  ldr r3, [r1]
        cmp r3, #0
        beq N
        cmp r3, r0
        beq S
    N:  add r1, r1, #16
        subs r2, r2, #1
        bne L
        ldrb r3, [r4, #0x9b]
        b D
    S:  mov r3, #1
    D:  msr APSR_nzcvq, ip
        pop {{r0-r2,ip}}
        b abs({fa(0x536e8698):#x})
    '''

def common_source(mask):
    if type(mask) is not int or not 0<=mask<16:raise ValueError('mask')
    return f'''
        push {{r2,r3,r4,r5,lr}}
        mov r4, r12
        mov r5, lr
        ldr r3, [r0, #4]
        ldrb r2, [r3, #0x44]
        cmp r2, #0
        bne D
        ldrb r2, [r3, #0x1c0]
        cmp r2, r4
        bne D
        add r12, r3, r4, lsl #1
        ldrb r3, [r12, #0x1c2]
        sub r3, r3, #18
        cmp r3, #4
        bhs D
        mov r2, #{mask}
        mov r2, r2, lsr r3
        tst r2, #1
        beq D
        mov r2, r4
        mov r3, #11
        cmp r4, #0
        lsleq r3, r3, #16
        add r5, r5, #36
        subeq r5, r5, #4
        str r2, [sp]
        str r3, [sp, #4]
        str r5, [sp, #16]
        cmp r3, r3
        b R
    D:  ldr r3, [r0, #4]
        ldrb r2, [r3, #0x44]
        str r2, [sp]
        str r3, [sp, #4]
        cmp r2, #0
    R:  pop {{r2,r3,r4,r5,r12}}
        mov lr, r12
        pop {{r12}}
        push {{lr}}
        ldr lr, [sp, #4]
        ldr pc, [sp], #8
    '''

SHA='53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'

def code_sources(mask, point, copy_at):
    """Pure assembly specification; contains no original firmware bytes."""
    if type(mask) is not int or not 1 <= mask <= 15:
        raise ValueError('nonempty four-slot mask required')
    slots,lookup,post=data_layout()
    return dict(curve_gate=_curve_gate_source(lookup),dirty=_dirty_source(mask),point=point_source(),
        getter=_getter_source(lookup),post=_post_source(post),composer=_composer_source(lookup,point),common=common_source(mask),ma_copy=_ma_copy_source(),ma_default=_ma_default_source(copy_at),ma_ctor=_ma_ctor_source(copy_at))


def build_code(mask, assembler=None):
    """Both sizing and final placement use the same checked source specification."""
    if assembler is None:
        assembler=lambda source,base: assemble_bundled(source,base,fwaddr.family('GRIII-2.10-v29'))
    sizes={k:len(assembler(v,fa(CODE)[0][0])) for k,v in code_sources(mask,fa(CODE)[0][0],fa(CODE)[0][0]).items()}
    addresses=packing(sizes)
    code={k:assembler(v,addresses[k]) for k,v in code_sources(mask,addresses['point'],addresses['ma_copy']).items()}
    return addresses,code


def plan_offline(raw,edits,profiles):
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest()!=fwaddr.payload_sha(SHA):
        raise FilmRequestError('held official original required')
    requests=validate_batch(edits)
    if any(e['slot']==2 for e in requests):
        from .film_vivid_fifth import plan_fifth
        return plan_fifth(raw,list(requests),profiles,fwaddr.family('GRIII-2.10'))
    if type(profiles) is not dict:raise FilmRequestError('profiles')
    if not requests:return dict(windows=(),records=(),mask=0,code={},code_bytes=0,data_bytes=0,data_span_bytes=0,
        layout=LAYOUT,curve_kind='knots33',decoded_growth_bytes=0,data_capacity_bytes=1440,
        storage_approved=False,factory_random_path_preserved=True,
        full_image_created=False,production_integration_allowed=False,blockers=())
    p=Payload(raw,fwaddr.layout()) if fwaddr.is_220() else Payload(raw)
    def read(a,n):
        off=p.off(a)
        if p.off(a+n-1)!=off+n-1:raise FilmRequestError('mapping')
        return p.p[off:off+n]
    # MIT reference make_slots16.py patch_ma: zero-table equivalence and pointer relocation.
    # Unlike the upstream comment, ZERO_SOURCE is a known ArbiterService table.
    # It is shared read-only here; not one byte of it is changed.
    if read(fa(DATA)[0][0],1440)!=bytes(1440) or read(fa(ZERO_SOURCE),1440)!=bytes(1440):
        raise FilmRequestError('zero-table relocation identity')
    if read(fa(ZERO_OBJECT),32)!=struct.pack('<8I',*[fa(DATA)[0][0]]*8):
        raise FilmRequestError('zero-table pointer object')
    first=read(fa(MATRIX_TEMPLATE),72)
    full=read(fa(MATRIX_TEMPLATE),2160)
    if full!=first[:36]+first[36:72]*59:
        raise FilmRequestError('matrix compression is not lossless')
    limits=_envelope(factory_envelope_from_payload(raw))
    resolved={};knots={}
    for e in requests:
        if e['film'] not in profiles:raise FilmRequestError('所选胶片缺少参数')
        profile=validate_profile(profiles[e['film']],e['film'],fwaddr.model_id())
        resolved[e['slot']]=profile
        knots[e['slot']],_= _numeric(profile,limits)
    mask=mask_slots([e['slot'] for e in requests])
    for a,w in fa(MODE_CHANGE_WORDS):
        if read(a,4)!=struct.pack('<I',w):raise FilmRequestError('mode cache guard')
    slots,lookup,post=data_layout()
    addresses,code=build_code(mask)
    guards=[(fa(0x536e8694),0xe5d4309b,_branch(fa(0x536e8694),addresses['curve_gate'])),
            (fa(0x536dd024),0xe5c4100e,_branch(fa(0x536dd024),addresses['dirty'])),
            (fa(0x536e4774),_branch(fa(0x536e4774),fa(0x536e4720),link=True),_branch(fa(0x536e4774),addresses['post'],link=True)),
            (fa(0x536e87c4),_branch(fa(0x536e87c4),fa(0x536eedf0),link=True),_branch(fa(0x536e87c4),addresses['getter'],link=True)),
            (fa(0x536e87c8),0xe51b3044,_branch(fa(0x536e87c8),addresses['composer']))]
    if mask&4:guards.extend(hdr_fixes())
    # Reuse the held original switch/table guards; discard all old thunks.
    dummy=[fa(0x53f00000)]
    def ignore(role,source):
        a=dummy[0];dummy[0]+=256;return a
    old,_,_=derive(read,[e['slot'] for e in requests],ignore)
    guards.extend(row for row in old if row[0] not in fa(ACTIVE))
    # An installed tone's own still checks would push the original preset's
    # adjustments after Standard's (Bleach Bypass key/contrast/highlight +4);
    # only Standard's values may reach a film (film_own_checks).
    guards.extend(film_own_checks.changes(read,[e['slot'] for e in requests]))
    guards.extend([
        (fa(0x536df458),_branch(fa(0x536df458),fa(0x531403a8),link=True),_branch(fa(0x536df458),addresses['ma_ctor'],link=True)),
        (fa(0x536df4d8),0xe5803008,_branch(fa(0x536df4d8),addresses['ma_default'])),
        (fa(0x536df514),_branch(fa(0x536df514),fa(0x5313fd80),link=True),_branch(fa(0x536df514),addresses['ma_copy'],link=True)),
    ])
    guards.extend((fa(ZERO_OBJECT)+4*i,fa(DATA)[0][0],fa(ZERO_SOURCE)) for i in range(8))
    for i,a in enumerate(fa(ACTIVE)):
        bank=i//9
        guards.extend([(a,0xe5903004,0xe92d5000),(a+4,0xe5d32044,0xe3a0c000|bank),
                       (a+8,0xe3520000,_branch(a+8,addresses['common'],link=True))])
    aliases=[i for e in requests for i in SLOT_INFO[e['slot']][1]]
    allowed=list(fa(CODE))+list(fa(DATA))+[(a,a+4) for a,_,_ in guards]+[(fa(0x55018d94)+4*i,fa(0x55018d98)+4*i) for i in aliases]
    image=PlanImage(read,tuple(allowed),max_bytes=8192)
    for a,b,c in guards:image.guarded_word(a,b,c)
    for k,blob in code.items():image.write(addresses[k],blob)
    # Sparse lookup/post entries must be zero even when a slot is absent.
    lookup_blob=bytearray(64);post_blob=bytearray(96)
    stock=read(fa(0x5501900c),84);tail=_init_tail(read);records=[]
    for e in requests:
        s=slots[e['slot']-8];profile=resolved[e['slot']]
        record=bytearray(stock);record[4:8]=struct.pack('<I',s['matrix']);record[68:84]=tail
        image.write(s['record'],bytes(record))
        image.write(s['matrix'],struct.pack('<9h',*sum(profile['look_matrix_q13'],[])))
        for a,curve in zip(s['knots'],knots[e['slot']]):image.write(a,struct.pack('<33H',*curve))
        i=e['slot']-8
        lookup_blob[i*16:i*16+16]=struct.pack('<4I',s['record'],*s['knots'])
        post_blob[i*24:i*24+24]=csp_entry(s['record'],profile['post_matrix_q512'])
        for alias in SLOT_INFO[e['slot']][1]:image.write(fa(0x55018d94)+4*alias,struct.pack('<I',s['record']))
        records.append({**s,'film':e['film']})
    image.write(lookup,bytes(lookup_blob));image.write(post,bytes(post_blob))
    return dict(windows=image.seal(),records=tuple(records),mask=mask,code=addresses,
        code_bytes=sum(map(len,code.values())),data_bytes=160+300*len(records),data_span_bytes=1368,data_capacity_bytes=1440,
        storage_approved=False,factory_random_path_preserved=True,
        full_image_created=False,production_integration_allowed=False,
        layout=LAYOUT,curve_kind='knots33',decoded_growth_bytes=0,
        blockers=('new_multiaxial_reconstruction_requires_validation',),factory_curves_preserved=True)


def require_factory_preserving(candidate):
    """Release boundary, not a firmware export or permission prompt."""
    if (type(candidate) is not dict or candidate.get('layout')!=LAYOUT
            or type(candidate.get('mask')) is not int or not 0<=candidate['mask']<16
            or type(candidate.get('windows')) is not tuple):
        raise FilmRequestError('未知离线候选')
    if candidate.get('windows') or candidate.get('mask')!=0:
        raise FilmRequestError('新候选保留随机代码和曲线；MultiAxial重构及完整实机合同尚未放行')
    return ()
