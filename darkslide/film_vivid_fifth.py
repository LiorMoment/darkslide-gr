"""Five-film sparse planner with Vivid isolated from the legacy four-film layout.

Uses existing attributed GPL/MIT Darkslide templates. The four-film code path and
its resource stay unchanged. Only selections containing Vivid use this layout.
No complete image, device access, release approval, or compiler fallback here.
"""
import hashlib
import struct
from .film_precompiled import assemble_bundled
from .film_patch_plan import PlanImage, _branch
from .film_requests import validate_batch, FilmRequestError
from .film_profiles import validate_profile
from .film_writer import _init_tail, SLOT_INFO, hdr_fixes
from .film_standard_proxy import vivid_changes
from .film_slots import mask_slots
from . import film_own_checks, models, fwaddr
from .fwaddr import A as fa
from .fwread import Payload
from ._vendor.gr3_film_hooks import csp_entry

LAYOUTS = {'GRIII-2.10': 'inplace_history_cache_exit_vivid5_v1',
           'GRIIIx-1.60': 'gr3x160_inplace_cache_exit_vivid5_v1',
           'GRIII-2.20': 'inplace_history_cache_exit_vivid5_220_v1',
           'GRIIIx-1.70': 'gr3x170_inplace_cache_exit_vivid5_v1'}
FAMILIES = {key:key+'-vivid5-v1' for key in LAYOUTS}
CODE = ((0x53dc3218, 0x53dc3848),)
DATA = ((0x53dc2bf8, 0x53dc3198), (0x53dc3848, 0x53dc3a40))
LOOKUP, POST = 0x53dc3978, 0x53dc39c8
VIVID = dict(slot=2, record=0x53dc3848, matrix=0x53dc389c,
             knots=(0x53dc38b0,0x53dc38f2,0x53dc3934), lookup=LOOKUP+64, post=POST+96)
ORDER = (8,9,10,11,2)
ALIASES = {**{s:v[1] for s,v in SLOT_INFO.items()}, 2:(12,)}


def need(ok, reason):
    if not ok: raise FilmRequestError(reason)


def _family(family):
    if family in ('GRIII-2.10','GRIII-2.20'):
        from . import film_inplace_cache as module
        base, new = models.GR3, models.GR3_220
    elif family in ('GRIIIx-1.60','GRIIIx-1.70'):
        from . import gr3x_inplace as module
        base, new = models.GR3X, models.GR3X_170
    else:
        raise FilmRequestError('unsupported five-film family')
    current=fwaddr.current()
    need(current is None or current==new,'five-film model/context mismatch')
    need(family not in ('GRIII-2.20','GRIIIx-1.70') or current==new,
         'new five-film family requires its firmware context')
    return module,current or base


def family_name(family):
    """Resolve a base planner name through the explicit version context."""
    _,model=_family(family)
    return {models.GR3:'GRIII-2.10',models.GR3X:'GRIIIx-1.60',
            models.GR3_220:'GRIII-2.20',models.GR3X_170:'GRIIIx-1.70'}[model]


def data_layout():
    slots = [];base=fa(0x53dc2bf8);lookup=fa(LOOKUP);post=fa(POST)
    for i,s in enumerate(ORDER[:4]):
        slots.append(dict(slot=s,record=base+i*84,matrix=base+336+i*20,
                          knots=tuple(base+480+(i*3+j)*66 for j in range(3)),
                          lookup=lookup+i*16,post=post+i*24))
    slots.append({k:fa(v) for k,v in VIVID.items()})
    return slots,lookup,post


def _replace(source, old, new):
    need(source.count(old)==1, 'five-film template identity mismatch')
    return source.replace(old,new,1)


def _tone_index(source, reg):
    return _replace(source,f'''        sub {reg}, {reg}, #18
        cmp {reg}, #4
        bhs D''',f'''        cmp {reg}, #12
        beq V
        sub {reg}, {reg}, #18
        cmp {reg}, #4
        bhs D
        b M
    V:  mov {reg}, #4
    M:  mov {reg}, {reg}''')


def code_sources(mask, point, copy_at, family):
    need(type(mask) is int and 16<=mask<=31, 'Vivid must be installed for five-film code')
    module,_ = _family(family)
    common = _replace(module.common_source(0),
        '        mov r2, #0\n        mov r2, r2, lsr r3',
        f'        mov r2, #{mask}\n        mov r2, r2, lsr r3')
    return dict(curve_gate=_replace(module._curve_gate_source(fa(LOOKUP)),'        mov r2, #4','        mov r2, #5'),
        dirty=_tone_index(module._dirty_source(mask),'r2'),point=module.point_source(),
        getter=_replace(module._getter_source(fa(LOOKUP)),'        mov r7, #4','        mov r7, #5'),
        post=_replace(module._post_source(fa(POST)),'        mov r5, #4','        mov r5, #5'),
        composer=_replace(module._composer_source(fa(LOOKUP),point),'        mov r3, #4','        mov r3, #5'),
        common=_tone_index(common,'r3'),ma_copy=module._ma_copy_source(),
        ma_default=module._ma_default_source(copy_at),ma_ctor=module._ma_ctor_source(copy_at))


def build_code(mask, family, assembler=None):
    resolved_family=family_name(family)
    if assembler is None:
        assembler=lambda source,base: assemble_bundled(source,base,FAMILIES[resolved_family])
    start,end=fa(CODE)[0]
    sizes={k:len(assembler(v,start)) for k,v in code_sources(mask,start,start,family).items()}
    addresses={};cursor=start
    for key in sorted(sizes,key=lambda k:(-sizes[k],k)):
        cursor=(cursor+3)&~3;addresses[key]=cursor;cursor+=sizes[key]
        need(cursor<=end,'five-film code capacity exceeded')
    code={k:assembler(v,addresses[k]) for k,v in code_sources(mask,addresses['point'],addresses['ma_copy'],family).items()}
    need({k:len(v) for k,v in code.items()}==sizes,'five-film second-pass size mismatch')
    return addresses,code


def plan_fifth(raw, edits, profiles, family):
    module,model=_family(family)
    need(type(raw) is bytes and hashlib.sha256(raw).hexdigest()==model.payload_sha,'held official original required')
    requests=validate_batch(edits)
    need(type(profiles) is dict and any(e['slot']==2 for e in requests),'five-film plan requires Vivid')
    p=Payload(raw,models.layout(model))
    def read(a,n):
        off=p.off(a)
        need(p.off(a+n-1)==off+n-1,'mapping')
        return p.p[off:off+n]
    need(read(fa(module.DATA)[0][0],1440)==bytes(1440) and read(fa(module.ZERO_SOURCE),1440)==bytes(1440),'zero-table identity')
    need(read(fa(module.ZERO_OBJECT),32)==struct.pack('<8I',*[fa(module.DATA)[0][0]]*8),'zero-table pointer object')
    first=read(fa(module.MATRIX_TEMPLATE),72)
    need(read(fa(module.MATRIX_TEMPLATE),2160)==first[:36]+first[36:72]*59,'matrix compression is not lossless')
    # Vivid's original record/matrix remain available to metadata/secondary consumers.
    need(int.from_bytes(read(fa(0x55018d94)+12*4,4),'little')==fa(0x550196d8),'Vivid primary binding')
    need(int.from_bytes(read(fa(0x550196dc),4),'little')==fa(0x53dc26dc),'Vivid original matrix identity')
    limits=module.factory_envelope_from_payload(raw)
    if models.film_base(model)==models.GR3:limits=module._envelope(limits)
    resolved={};knots={};errors={}
    for e in requests:
        need(e['film'] in profiles,'所选胶片缺少参数')
        profile=validate_profile(profiles[e['film']],e['film'],model.id)
        resolved[e['slot']]=profile
        knots[e['slot']],errors[e['slot']]=module._numeric(profile,limits)
    mask=mask_slots([e['slot'] for e in requests])
    for a,w in fa(module.MODE_CHANGE_WORDS):need(read(a,4)==struct.pack('<I',w),'mode cache guard')
    slots,lookup,post=data_layout();slots={s['slot']:s for s in slots}
    addresses,code=build_code(mask,family)
    guards=[(fa(0x536e8694),0xe5d4309b,_branch(fa(0x536e8694),addresses['curve_gate'])),
            (fa(0x536dd024),0xe5c4100e,_branch(fa(0x536dd024),addresses['dirty'])),
            (fa(0x536e4774),_branch(fa(0x536e4774),fa(0x536e4720),link=True),_branch(fa(0x536e4774),addresses['post'],link=True)),
            (fa(0x536e87c4),_branch(fa(0x536e87c4),fa(0x536eedf0),link=True),_branch(fa(0x536e87c4),addresses['getter'],link=True)),
            (fa(0x536e87c8),0xe51b3044,_branch(fa(0x536e87c8),addresses['composer']))]
    if mask&4:guards.extend(hdr_fixes())
    dummy=[fa(0x53f00000)]
    def ignore(role,source):
        a=dummy[0];dummy[0]+=256;return a
    legacy=[e['slot'] for e in requests if e['slot']!=2]
    old,_,_=module.derive(read,legacy,ignore)
    guards.extend(row for row in old if row[0] not in fa(module.ACTIVE))
    guards.extend(vivid_changes(read))
    guards.extend(film_own_checks.changes(read,[e['slot'] for e in requests]))
    guards.extend([
        (fa(0x536df458),_branch(fa(0x536df458),fa(0x531403a8),link=True),_branch(fa(0x536df458),addresses['ma_ctor'],link=True)),
        (fa(0x536df4d8),0xe5803008,_branch(fa(0x536df4d8),addresses['ma_default'])),
        (fa(0x536df514),_branch(fa(0x536df514),fa(0x5313fd80),link=True),_branch(fa(0x536df514),addresses['ma_copy'],link=True))])
    guards.extend((fa(module.ZERO_OBJECT)+4*i,fa(module.DATA)[0][0],fa(module.ZERO_SOURCE)) for i in range(8))
    for i,a in enumerate(fa(module.ACTIVE)):
        bank=i//9
        guards.extend([(a,0xe5903004,0xe92d5000),(a+4,0xe5d32044,0xe3a0c000|bank),
                       (a+8,0xe3520000,_branch(a+8,addresses['common'],link=True))])
    aliases=[i for e in requests for i in ALIASES[e['slot']]]
    allowed=list(fa(CODE))+list(fa(DATA))+[(a,a+4) for a,_,_ in guards]+[(fa(0x55018d94)+4*i,fa(0x55018d98)+4*i) for i in aliases]
    image=PlanImage(read,tuple(allowed),max_bytes=8192)
    for a,b,c in guards:image.guarded_word(a,b,c)
    for key,blob in code.items():image.write(addresses[key],blob)
    lookup_blob=bytearray(80);post_blob=bytearray(120)
    stock=read(fa(0x5501900c),84);tail=_init_tail(read);records=[]
    for e in requests:
        s=slots[e['slot']];profile=resolved[e['slot']]
        record=bytearray(stock);record[4:8]=struct.pack('<I',s['matrix']);record[68:84]=tail
        image.write(s['record'],bytes(record));image.write(s['matrix'],struct.pack('<9h',*sum(profile['look_matrix_q13'],[])))
        for a,curve in zip(s['knots'],knots[e['slot']]):image.write(a,struct.pack('<33H',*curve))
        i=ORDER.index(e['slot'])
        lookup_blob[i*16:i*16+16]=struct.pack('<4I',s['record'],*s['knots'])
        post_blob[i*24:i*24+24]=csp_entry(s['record'],profile['post_matrix_q512'])
        for alias in ALIASES[e['slot']]:image.write(fa(0x55018d94)+4*alias,struct.pack('<I',s['record']))
        records.append({**s,'film':e['film']})
    image.write(lookup,bytes(lookup_blob));image.write(post,bytes(post_blob))
    return dict(windows=image.seal(),records=tuple(records),mask=mask,code=addresses,compression_errors=errors,
        code_bytes=sum(map(len,code.values())),data_bytes=200+300*len(records),data_span_bytes=1944,data_capacity_bytes=1944,
        storage_approved=False,factory_random_path_preserved=True,full_image_created=False,
        production_integration_allowed=False,layout=LAYOUTS[family_name(family)],curve_kind='knots33',decoded_growth_bytes=0,
        blockers=('five_slot_runtime_and_device_validation_pending',),factory_curves_preserved=True)
