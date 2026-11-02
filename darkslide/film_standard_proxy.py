"""Selected GR III 2.10 tones borrow existing Standard menu/value cases.

Own table-routing implementation. Reference facts: MIT gr3-film-mods
04d5d5dd6e85d80089c6200bd0f0de5fff2d658f make_slots.py/FINDINGS.
No new tone IDs, storage-bank changes or whole-firmware creation here.
"""
import struct
from .film_requests import FilmRequestError
from .film_arm_assemble import assemble
from .film_patch_plan import _branch
from .fwaddr import A as fa

FUNCTIONS=(0x532BD784,0x532BDCD8,0x532BE22C,0x532BE8A0,0x532BEF14,0x532BF588,
           0x532BFBFC,0x532C0DA8,0x532C141C,0x532C34E8,0x532C3B54,0x532C41C0,
           0x532C494C,0x532C50D8,0x532C5864,0x532C5FF0,0x532C8BD4,0x532C9360)
ACTIVE=(0x5325B9E0,0x5325BC68,0x5325BEF0,0x5325C2BC,0x5325C688,0x5325CA54,0x5325CE20,0x5325D4E0,0x5325D8AC,
        0x5325DE28,0x5325E0C8,0x5325E368,0x5325E758,0x5325EB48,0x5325EF38,0x5325F328,0x5325FA28,0x5325FE18,
        0x532603C8,0x53260668,0x53260908,0x53260CF8,0x532610E8,0x532614D8,0x532618C8,0x53261FC8,0x532623B8)
PIPELINE=((0x536DB838,0x536DBD00),(0x536DB840,0x536DBDCC),(0x536DB848,0x536DBEA4),(0x536DB850,0x536DBF50))
MENU_TABLES=(0x5318DEC0,0x531ACB28)

def mask_slots(slots):
    if type(slots) not in (tuple,list) or any(type(s) is not int or s not in (8,9,10,11) for s in slots) or len(slots)!=len(set(slots)):
        raise FilmRequestError('需要不重复的四胶片位置选择')
    return sum(1<<(s-8) for s in slots)

def dispatch_source(bank,mask):
    """Alias only selected tones; replay the original first load otherwise.

    The thunk saved incoming IP/LR and IP names original entry+4. Both
    success and fallback restore the original LR/IP/SP. Successful R2/R3 and
    NZCV match the original Standard predicate before its existing tail B.
    """
    mode=11<<16 if bank==0 else 11
    offset=40 if bank==0 else 44
    return f'''
        push {{r2,r3,r4,lr}}
        mov r4, r12
        mrs r3, APSR
        push {{r3,r12}}
        ldr r3, [r0, #4]
        ldrb r2, [r3, #0x44]
        cmp r2, #0
        bne D
        ldrb r2, [r3, #0x1c0]
        cmp r2, #{bank}
        bne D
        ldrb r3, [r3, #{0x1c2+2*bank}]
        sub r3, r3, #18
        cmp r3, #4
        bhs D
        mov r2, #{mask}
        mov r2, r2, lsr r3
        tst r2, #1
        beq D
        mov r2, #{bank}
        mov r3, #{mode}
        str r2, [sp, #8]
        str r3, [sp, #12]
        add r4, r4, #{offset}
        str r4, [sp, #28]
        cmp r3, #{mode}
        b R
    D:  ldr r3, [r0, #4]
        str r3, [sp, #12]
        str r4, [sp, #28]
        ldr r3, [sp]
        msr APSR_nzcvq, r3
    R:  add sp, sp, #8
        pop {{r2,r3,r4,lr}}
        pop {{r12,pc}}
    '''

def thunk_source(resume,handler):
    return f'''
        push {{r12,lr}}
        movw r12, #{resume&65535}
        movt r12, #{resume>>16}
        b abs({handler:#x})
    '''


def _tail(word,pc):
    if word>>24!=0xEA:raise FilmRequestError('标准滑杆尾分支不匹配')
    delta=word&0xFFFFFF
    if delta&0x800000:delta-=1<<24
    at=pc+8+delta*4
    if at%4 or not fa(0x53000000)<=at<fa(0x540B55C0):raise FilmRequestError('标准滑杆尾目标越界')
    return at

def derive(read,slots,allocate):
    """Return bounded guarded words and original-size code replacements."""
    mask=mask_slots(slots)
    if not mask:return (),(),dict(mask=0,switches=0,predicates=0)
    tables=[]
    for start in fa(FUNCTIONS):
        words=struct.unpack('<512I',read(start,2048))
        end=next((i for i,w in enumerate(words[1:],1) if w==0xE1A0C00D),None)
        if end is None:raise FilmRequestError('标准滑杆函数边界不匹配')
        for i in range(end-2):
            w=words[i];r=(w>>12)&15
            if w&0xFFF00FFF==0xE240000B and (w>>16)&15==r and words[i+1]==0xE350000B|(r<<16) and words[i+2]&0xFFFFF000==0x979FF000:
                if r not in (4,5):raise FilmRequestError('标准滑杆switch寄存器不匹配')
                tables.append(start+4*i+16)
    if len(tables)!=102 or len(set(tables))!=102:raise FilmRequestError('标准滑杆102处切换点不匹配')
    changes=[]
    for table in tables+list(fa(MENU_TABLES)):
        target=int.from_bytes(read(table,4),'little')
        if target%4 or not fa(0x53000000)<=target<fa(0x540B55C0):raise FilmRequestError('标准菜单目标不匹配')
        for i in range(4):
            if mask&(1<<i):
                at=table+4*(7+i);changes.append((at,int.from_bytes(read(at,4),'little'),target))
    for i,(at,target) in enumerate(fa(PIPELINE)):
        if mask&(1<<i):changes.append((at,_branch(at,target,condition=0),_branch(at,fa(0x536DBAA4),condition=0)))
    handlers={b:allocate("proxy_dispatch_"+str(b),dispatch_source(b,mask)) for b in range(3)}
    for i,at in enumerate(fa(ACTIVE)):
        bank=i//9;n=12 if bank==0 else 13;words=struct.unpack('<'+str(n)+'I',read(at,n*4))
        if bank==0:
            fixed={0:0xE5903004,1:0xE5D32044,2:0xE3520000,4:0xE59331C0,5:0xE3C334FF,6:0xE3C33CFF,7:0xE353080B,9:0xE3A00000,10:0xE12FFF1E,
                   3:_branch(at+12,at+36,condition=1),8:_branch(at+32,at+44,condition=0)}
        else:
            fixed={0:0xE5903004,1:0xE5D32044,2:0xE3520000,4:0xE5D321C0,5:0xE3520000|bank,7:0xE3A00000,8:0xE12FFF1E,9:0xE5D33000|(0x1C2+2*bank),10:0xE353000B,
                   3:_branch(at+12,at+28,condition=1),6:_branch(at+24,at+36,condition=0),11:_branch(at+44,at+28,condition=1)}
        if any(words[j]!=v for j,v in fixed.items()):raise FilmRequestError('标准滑杆原厂激活合同不匹配')
        _tail(words[-1],at+4*(n-1))
        thunk=allocate('proxy_thunk_'+str(i),thunk_source(at+4,handlers[bank]))
        changes.append((at,words[0],_branch(at,thunk)))
    return tuple(changes),(),dict(mask=mask,switches=102,predicates=27,dispatchers=handlers)


def vivid_changes(read):
    """Guarded Vivid-only aliases; keep original records and all other tones intact.

    Table discovery uses the already validated Standard routing grammar. Vivid
    is table entry 1 (tone 12 minus 11). Its dedicated pipeline branch is before
    the Positive branch; they share a target but only Vivid's branch is changed.
    """
    tables=[]
    for start in fa(FUNCTIONS):
        words=struct.unpack('<512I',read(start,2048))
        end=next((i for i,w in enumerate(words[1:],1) if w==0xE1A0C00D),None)
        if end is None:raise FilmRequestError('Vivid standard switch boundary')
        for i in range(end-2):
            w=words[i];r=(w>>12)&15
            if w&0xFFF00FFF==0xE240000B and (w>>16)&15==r and words[i+1]==0xE350000B|(r<<16) and words[i+2]&0xFFFFF000==0x979FF000:
                if r not in (4,5):raise FilmRequestError('Vivid standard switch register')
                tables.append(start+4*i+16)
    if len(tables)!=102 or len(set(tables))!=102:raise FilmRequestError('Vivid standard switch count')
    changes=[]
    for table in tables+list(fa(MENU_TABLES)):
        target=int.from_bytes(read(table,4),'little')
        if target%4 or not fa(0x53000000)<=target<fa(0x540B55C0):raise FilmRequestError('Vivid standard target')
        at=table+4;changes.append((at,int.from_bytes(read(at,4),'little'),target))
    changes.append((fa(0x536DB808),_branch(fa(0x536DB808),fa(0x536DB8FC),condition=0),
                    _branch(fa(0x536DB808),fa(0x536DBAA4),condition=0)))
    return tuple(changes)
