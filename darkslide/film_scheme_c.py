"""Scheme C: fixed four-slot readonly ROM data, selected Standard controls.

Small plans only. No mutable global curve pointers, recycled factory curves,
Cross random code removal, device access or availability changes.
"""
import hashlib,struct
from .film_requests import FilmRequestError,validate_batch
from .film_profiles import validate_profile
from .film_patch_plan import PlanImage,from_official_payload,_branch
from .film_four_validation import fit_knots,ORIGINAL_BYTES,ORIGINAL_SHA
from .film_writer import _init_tail,SLOT_INFO,HDR_FIXES,hdr_fixes
from .film_standard_proxy import derive as proxy_derive,mask_slots
from .film_arm_assemble import assemble
from ._vendor.gr3_four_curve_hooks import point_source,composer_source
from ._vendor.gr3_film_hooks import csp_src,csp_entry
from .fwaddr import A as fa

LAYOUT='standard_sliders_v1'
ARENA,END=0x53D36400,0x53D37400
CODE_END=ARENA+0x800
SLOT_BASE=ARENA+0x800
STRIDE=0x180
LOOKUP=ARENA+0xE00
POST_TABLE=LOOKUP+0x40
PRIMARY,STANDARD=0x55018D94,0x5501900C
# Four direct A32 branch-shaped references independently observed in the
# official payload. Keep their incoming branches intact. Each landing skips
# the original all-zero tail without changing registers, LR or APSR. This
# protects these exact A32 paths only, not unknown indirect/Thumb users.
LANDINGS=(ARENA+0x21C,ARENA+0x528,ARENA+0x694,ARENA+0x7DC)
LANDING_REFS=((0x5390CE10,0x1A10A6C4),(0x53933A48,0xEA100AF3),
              (0x53A59330,0x0B0B75D7),(0x53B0A0B4,0x3B08B2C8))
# The official controller computes prior-tone != current-tone before updating
# its cached tone and keeps that boolean in the same frame at FP-0xCC.
MODE_CHANGE_WORDS=((0x536DCB64,0xE51B2034),(0x536DCB6C,0xE5D431A8),
                   (0x536DCB74,0xE0533002),(0x536DCB7C,0x13A03001),
                   (0x536DCB80,0xE50B30CC),(0x536DCDFC,0xE5C421A8))

def slot_layout(slot):
    if type(slot) is not int or slot not in SLOT_INFO:raise FilmRequestError('胶片位置无效')
    base=fa(SLOT_BASE)+(slot-8)*STRIDE
    return dict(slot=slot,record=base,matrix=base+84,knots=(base+104,base+172,base+240),
                post_entry=fa(POST_TABLE)+(slot-8)*24,lookup_entry=fa(LOOKUP)+(slot-8)*16)

def _fill_source(mask):
    return f'''
        push {{r0,r2,r12,lr}}
        mrs r12, APSR
        sub r2, r1, #18
        cmp r2, #4
        bhs D
        mov r0, #{mask}
        mov r0, r0, lsr r2
        tst r0, #1
        beq D
        ldr r3, [r6, #0x2d0]
        add r3, r3, #0x38000
        mov r2, #2
        strb r2, [r3, #0xa98]
        msr APSR_nzcvq, r12
        pop {{r0,r2,r12,lr}}
        .word 0xe89daff8
    D:  msr APSR_nzcvq, r12
        pop {{r0,r2,r12,lr}}
        .word 0x159632d0
        beq abs({fa(0x536dd4b8):#x})
        b abs({fa(0x536dd458):#x})
    '''

def _dirty_source(mask):
    return f'''
        push {{r0,r2,r3}}
        mrs r3, APSR
        ldrb r2, [r4, #0x1a8]
        sub r2, r2, #18
        cmp r2, #4
        bhs D
        mov r0, #{mask}
        mov r0, r0, lsr r2
        tst r0, #1
        bne S
        cmp r2, #3
        bne D
        ldr r0, [r11, #-0xcc]
        cmp r0, #0
        beq D
    S:  orr r1, r1, #1
    D:  msr APSR_nzcvq, r3
        pop {{r0,r2,r3}}
        strb r1, [r4, #0xe]
        b abs({fa(0x536dd028):#x})
    '''

def _getter_source(table):
    return f'''
        push {{r4,r5,r6,r7,r8,lr}}
        mov r4, r1
        mov r5, r2
        mov r6, r3
        ldr r12, [r11, #-0x48]
        ldr r12, [r12, #8]
        movw r2, #{table&65535}
        movt r2, #{table>>16}
        mov r7, #4
    L:  ldr r3, [r2]
        cmp r3, #0
        beq N
        cmp r3, r12
        addeq r2, r2, #4
        beq S
    N:  add r2, r2, #16
        subs r7, r7, #1
        bne L
        mov r1, r4
        mov r2, r5
        mov r3, r6
        bl abs({fa(0x536eedf0):#x})
        pop {{r4,r5,r6,r7,r8,pc}}
    S:  ldr r3, [r2]
        str r3, [r4]
        ldr r3, [r2, #4]
        str r3, [r5]
        ldr r3, [r2, #8]
        str r3, [r6]
        mov r3, #0
        strb r3, [r0]
        pop {{r4,r5,r6,r7,r8,pc}}
    '''

def _post_source(table):
    # Local adaptation of the already attributed MIT template: four fixed
    # sparse entries, not sentinel termination. Keep each slot's entry fixed.
    src=csp_src(table)
    start=src.index('    L:');finish=src.index('        add r4, r4, #4',start)
    return src[:start]+'''
        mov r5, #4
    L:  ldr r12, [r4]
        cmp r12, #0
        beq N
        cmp r12, r3
        beq S
    N:  add r4, r4, #24
        subs r5, r5, #1
        bne L
        b D
    S:  '''+src[finish:].lstrip()

def _composer_source(table,point):
    src=composer_source(table,point)
    start=src.index('    L:');finish=src.index('    D:',start)
    return src[:start]+'''
        mov r3, #4
    L:  ldr r2, [r1]
        cmp r2, #0
        beq N
        cmp r0, r2
        beq S
    N:  add r1, r1, #16
        subs r3, r3, #1
        bne L
'''+src[finish:]

class _Source:
    def __init__(self,read):
        if not callable(read):raise FilmRequestError('需要只读官方原件')
        self.callback=read;self.bytes={};self.regions={}
    def read(self,a,n):
        if type(a) is not int or type(n) is not int or not 0<n<=4096:raise FilmRequestError('胶片读取越界')
        out=[]
        for i in range(0,n,512):
            at=a+i;size=min(512,n-i);blob=self.callback(at,size)
            if type(blob) is not bytes or len(blob)!=size:raise FilmRequestError('原件读取不完整')
            for j,v in enumerate(blob):
                if at+j in self.bytes and self.bytes[at+j]!=v:raise FilmRequestError('原件读取期间变化')
                self.bytes[at+j]=v
            self.regions[at,size]=blob;out.append(blob)
        return b''.join(out)
    def stable(self):
        for (a,n),blob in tuple(self.regions.items()):
            if self.read(a,n)!=blob:raise FilmRequestError('原件读取期间变化')

def plan(read,edits,profiles):
    requests=validate_batch(edits)
    if any(e['slot']==2 for e in requests):raise FilmRequestError('Vivid requires the five-film layout')
    if not requests:return dict(windows=(),selected_slots=(),records=[],layout=LAYOUT,full_image_created=False)
    if type(profiles) is not dict:raise FilmRequestError('需要胶片拟合参数')
    resolved={}
    for e in requests:
        if e['film'] not in profiles:raise FilmRequestError('所选胶片缺少参数')
        resolved[e['slot']]=validate_profile(profiles[e['film']],e['film'])
    source=_Source(read);read=source.read
    if any(read(fa(ARENA),4096)):raise FilmRequestError('只读胶片区不匹配官方空白，拒绝覆盖')
    for at,word in fa(LANDING_REFS):
        if read(at,4)!=struct.pack('<I',word):raise FilmRequestError('只读区已知引用不匹配')
    for at,word in fa(MODE_CHANGE_WORDS):
        if read(at,4)!=struct.pack('<I',word):raise FilmRequestError('原厂模式切换缓存合同不匹配')
    stock=read(fa(STANDARD),84);tail=_init_tail(read)
    if int.from_bytes(read(fa(0x536DD464),4),'little')!=0xE89DAFF8:raise FilmRequestError('原厂fill返回不匹配')
    mask=mask_slots([e['slot'] for e in requests]);code=[];cursor=fa(ARENA)
    def allocate(role,src):
        nonlocal cursor
        at=cursor
        while True:
            blob=assemble(src,at)
            overlaps=[a for a in fa(LANDINGS) if at<a+4 and a<at+len(blob)]
            if not overlaps:break
            at=max(overlaps)+4
        cursor=at+len(blob)
        if cursor>fa(CODE_END):raise FilmRequestError('选择性胶片代码超出只读区')
        code.append(dict(role=role,address=at,code=blob));return at
    for name,src in (('fill',_fill_source(mask)),('dirty',_dirty_source(mask)),('point',point_source()),
                     ('getter',_getter_source(fa(LOOKUP))),('post',_post_source(fa(POST_TABLE)))):
        allocate(name,src)
    point=next(r['address'] for r in code if r['role']=='point')
    allocate('composer',_composer_source(fa(LOOKUP),point))
    addresses={r['role']:r['address'] for r in code}
    guards=[(fa(0x536DD450),0x159632D0,_branch(fa(0x536DD450),addresses['fill'])),
            (fa(0x536DD024),0xE5C4100E,_branch(fa(0x536DD024),addresses['dirty'])),
            (fa(0x536E4774),_branch(fa(0x536E4774),fa(0x536E4720),link=True),_branch(fa(0x536E4774),addresses['post'],link=True)),
            (fa(0x536E87C4),_branch(fa(0x536E87C4),fa(0x536EEDF0),link=True),_branch(fa(0x536E87C4),addresses['getter'],link=True)),
            (fa(0x536E87C8),0xE51B3044,_branch(fa(0x536E87C8),addresses['composer']))]
    # Outside the parameter pass-through, HDR still has a native multi-frame
    # trigger. The selected HDR fixes stay conditional. Retro pass-through is
    # already bypassed by the selected Standard case, so its patch is omitted.
    if mask&4:guards+=list(hdr_fixes())
    proxy_words,proxy_codes,proxy_meta=proxy_derive(read,[e['slot'] for e in requests],allocate)
    guards+=list(proxy_words)
    for at in fa(LANDINGS):
        code.append(dict(role='landing_guard',address=at,code=struct.pack('<I',_branch(at,fa(END)))))
    allowed=[(fa(ARENA),fa(END))]+[(a,a+4) for a,_,_ in guards]+[(a,a+len(b)) for a,b in proxy_codes]
    selected_aliases=[i for e in requests for i in SLOT_INFO[e['slot']][1]]
    allowed.extend((fa(PRIMARY)+4*i,fa(PRIMARY)+4*i+4) for i in selected_aliases)
    image=PlanImage(read,tuple(allowed),max_bytes=8192)
    for a,old,new in guards:image.guarded_word(a,old,new)
    for row in code:image.write(row['address'],row['code'])
    for a,blob in proxy_codes:image.write(a,blob)
    records=[];errors=[]
    for e in requests:
        r=slot_layout(e['slot']);p=resolved[e['slot']];clone=bytearray(stock)
        clone[4:8]=struct.pack('<I',r['matrix']);clone[68:84]=tail
        image.write(r['record'],bytes(clone));image.write(r['matrix'],struct.pack('<9h',*(v for row in p['look_matrix_q13'] for v in row)))
        channel_errors=[]
        for at,curve in zip(r['knots'],p['curves_u14']):
            knots,error=fit_knots(curve);image.write(at,struct.pack('<33H',*knots));channel_errors.append(error)
        image.write(r['lookup_entry'],struct.pack('<4I',r['record'],*r['knots']))
        image.write(r['post_entry'],csp_entry(r['record'],p['post_matrix_q512']))
        for i in SLOT_INFO[e['slot']][1]:image.write(fa(PRIMARY)+4*i,struct.pack('<I',r['record']))
        records.append(dict(r,film=e['film'],aliases=SLOT_INFO[e['slot']][1]));errors.append(dict(slot=e['slot'],channels=channel_errors))
    windows=image.seal();source.stable()
    return dict(layout=LAYOUT,windows=windows,selected_slots=tuple(e['slot'] for e in requests),installed_mask=mask,
                records=records,compression_errors=errors,code_regions=tuple(dict(role=r['role'],address=r['address'],size=len(r['code'])) for r in code),
                code_bytes=sum(len(r['code']) for r in code),code_span=cursor-fa(ARENA),
                landing_guards=fa(LANDINGS),arena_used=fa(POST_TABLE)+96-fa(ARENA),proxy=proxy_meta,
                factory_library_unchanged=True,factory_random_code_unchanged=True,expanded_scratch_bytes=0,
                source_regions=tuple((a,n) for a,n in source.regions),full_image_created=False,device_verified=False,
                storage_liveness_verified=False,runtime_mapping_verified=False,same_gamma_concurrent_calls_verified=False)

def plan_official_payload(raw,edits,profiles):
    if type(raw) is not bytes or len(raw)!=ORIGINAL_BYTES or hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA:
        raise FilmRequestError('方案C只接受官方GR III 2.10原件')
    requests=validate_batch(edits)
    if any(e['slot']==2 for e in requests):raise FilmRequestError('Vivid requires the five-film layout')
    if not requests:return plan(None,[],{})
    # One defensive snapshot serves the numeric gate and the actual plan.
    if type(profiles) is not dict:raise FilmRequestError('需要胶片拟合参数')
    copied={}
    for e in requests:
        if e['film'] not in profiles:raise FilmRequestError('所选胶片缺少参数')
        copied[e['film']]=validate_profile(profiles[e['film']],e['film'])
    from .factory_envelope import factory_envelope_from_payload
    from .film_four_validation import expand_knots
    envelope=factory_envelope_from_payload(raw)
    for p in copied.values():
        m=p['look_matrix_q13'];post=p['post_matrix_q512']
        if (any(not envelope['look_min']<=v<=envelope['look_max'] for row in m for v in row)
            or any(sum(abs(v) for v in row)>envelope['look_row_abs_max'] for row in m)
            or any(not envelope['post_min']<=v<=envelope['post_max'] for row in post for v in row)
            or any(sum(abs(post[i][j]) for i in range(3))>envelope['post_col_abs_max'] for j in range(3))):
            raise FilmRequestError('胶片矩阵超出原厂数值边界')
        for curve in p['curves_u14']:
            rebuilt=expand_knots(fit_knots(curve)[0])
            if any(not envelope['curve_step_min']<=b-a<=envelope['curve_step_max'] for a,b in zip(rebuilt,rebuilt[1:])):
                raise FilmRequestError('胶片重建曲线步差超出原厂数值边界')
            if not (envelope['curve_first_min']<=rebuilt[0]<=envelope['curve_first_max'] and envelope['curve_last_min']<=rebuilt[-1]<=envelope['curve_last_max']):
                raise FilmRequestError('胶片重建曲线起点或终点超出原厂数值边界：起点 %d（原厂 %d～%d），终点 %d（原厂 %d～%d）'%(rebuilt[0],envelope['curve_first_min'],envelope['curve_first_max'],rebuilt[-1],envelope['curve_last_min'],envelope['curve_last_max']))
    return plan(from_official_payload(raw,()).reader,list(requests),copied)
