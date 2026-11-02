"""Independent five-slot exact-window reader; never imports a planner/bundle.

Numeric fields, stock guarded routing and coverage are reconstructed from the
held original. Only private ARM fragments use a separately frozen reviewed
reference. This is byte integrity, not a proof of camera runtime behavior.
"""
import hashlib
import json
import struct
from pathlib import Path
from .film_inplace_readback import ReadbackError, need, nodes

LAYOUTS = {'inplace_history_cache_exit_vivid5_v1': ('GR III', '2.10', 'GRIII-2.10',
    '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'),
    'gr3x160_inplace_cache_exit_vivid5_v1': ('GR IIIx', '1.60', 'GRIIIx-1.60',
    'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630')}
LAYOUTS.update({
    'inplace_history_cache_exit_vivid5_220_v1': ('GR III', '2.20', 'GRIII-2.20',
        '5b475fcc434dec0f12e7956b02b5b38959d599b7a4ff90486263c46816a21b6d'),
    'gr3x170_inplace_cache_exit_vivid5_v1': ('GR IIIx', '1.70', 'GRIIIx-1.70',
        '1129b9289b3cc68ab6f407a87d60db750483cc5ae41a6609596f6fe676b14521')})
MODEL_IDS = {'GRIII-2.10':'gr3-2.10-reviewed-1','GRIIIx-1.60':'gr3x-1.60-reviewed-1',
    'GRIII-2.20':'gr3-2.20-reviewed-1','GRIIIx-1.70':'gr3x-1.70-reviewed-1'}
# These independent exact points are supported by independent_original_probe.
# No production relocation table/context is consulted by the five-slot reader.
NEW_POINTS = {
    0x5501900c:0x55019014, 0x55018d94:0x55018d9c, 0x55019848:0x55019850,
    0x53dc27a4:0x53dc28dc, 0x53dc2bf8:0x53dc2d30, 0x53dc2d48:0x53dc2e80,
    0x53dc2dd8:0x53dc2f10, 0x53d36400:0x53d35de0,
    0x53dc3218:0x53dc3350, 0x53dc3848:0x53dc3980, 0x53dc389c:0x53dc39d4,
    0x53dc38b0:0x53dc39e8, 0x53dc3978:0x53dc3ab0, 0x53dc39c8:0x53dc3b00,
    0x5318dec0:0x5318df80, 0x531acb28:0x531acc00,
    0x536db808:0x536db7e0, 0x536db8fc:0x536db8d4, 0x536dbaa4:0x536dba7c,
    0x536db838:0x536db810, 0x536dbd00:0x536dbcd8,
    0x536db840:0x536db818, 0x536dbdcc:0x536dbda4,
    0x536db848:0x536db820, 0x536dbea4:0x536dbe7c,
    0x536db850:0x536db828, 0x536dbf50:0x536dbf28,
    0x536e8694:0x536e866c, 0x536dd024:0x536dcffc,
    0x536e4774:0x536e474c, 0x536e4720:0x536e46f8,
    0x536e87c4:0x536e879c, 0x536eedf0:0x536eedc8, 0x536e87c8:0x536e87a0,
    0x536df458:0x536df430, 0x531403a8:0x53140468, 0x536df4d8:0x536df4b0,
    0x536df514:0x536df4ec, 0x5313fd80:0x5313fe40,
    0x536dbeb4:0x536dbe8c, 0x536dc27c:0x536dc254, 0x536dc818:0x536dc7f0,
    0x536dbebc:0x536dbe94, 0x535131fc:0x535132ec, 0x53513274:0x53513364,
    0x53513218:0x53513308,
}
ALIASES = {8:(18,22,23),9:(19,),10:(20,24),11:(21,),2:(12,)}
ORDER = (8,9,10,11,2)
REFERENCE = Path(__file__).with_name('film_vivid_fifth_reference.json')
REFERENCE_SHA256 = 'f15aec9a69fe64a472f8d8ebaf83d63f3ad39b199022325fd98523b740a9a16b'
REFERENCE_220 = Path(__file__).with_name('film_vivid_fifth_reference_220.json')
REFERENCE_220_SHA256 = '6236d4cc630c391aaf87242ef145e3fa29700d0caeeef4f0974fab5ddce021cb'
FUNCTIONS = (0x532BD784,0x532BDCD8,0x532BE22C,0x532BE8A0,0x532BEF14,0x532BF588,
    0x532BFBFC,0x532C0DA8,0x532C141C,0x532C34E8,0x532C3B54,0x532C41C0,
    0x532C494C,0x532C50D8,0x532C5864,0x532C5FF0,0x532C8BD4,0x532C9360)
ACTIVE = (0x5325B9E0,0x5325BC68,0x5325BEF0,0x5325C2BC,0x5325C688,0x5325CA54,0x5325CE20,0x5325D4E0,0x5325D8AC,
    0x5325DE28,0x5325E0C8,0x5325E368,0x5325E758,0x5325EB48,0x5325EF38,0x5325F328,0x5325FA28,0x5325FE18,
    0x532603C8,0x53260668,0x53260908,0x53260CF8,0x532610E8,0x532614D8,0x532618C8,0x53261FC8,0x532623B8)


def offset(at,n,length):
    need(type(at) is int and type(n) is int and n>0,'typed range')
    new=length==59798532
    data_end=0x5503a0c0 if new else 0x5503a080
    lma=0x5407b640 if new else 0x5407b540
    if 0x55000000<=at<at+n<=data_end:
        off=at-0x55000000+lma-0x53000000+0x10870
    else:
        need(0x53000000<=at<at+n<=lma,'canonical address')
        off=at-0x53000000+0x10870
    need(0<=off<off+n<=length-4,'payload range')
    return off


def branch(at,target,condition=14,link=False):
    displacement=target-at-8
    need(displacement%4==0 and -(1<<25)<=displacement<(1<<25),'branch range')
    return (condition<<28)|(0xB000000 if link else 0xA000000)|((displacement//4)&0xffffff)


def target(at,word):
    displacement=word&0xffffff
    if displacement&0x800000:displacement-=0x1000000
    return at+8+displacement*4


def own_checks(read, selected, *, newer=False):
    """Parse original exact check contracts and match shared property call tails."""
    start,end=(0x5325b0d8,0x5326b8d8) if newer else (0x5325b000,0x5326b800)
    w=struct.unpack('<%dI'%((end-start)//4),read(start,end-start));rows=[]
    for i in range(len(w)-13):
        q=w[i:i+13];pc=start+i*4
        if q[:3]!=(0xe5903004,0xe5d32044,0xe3520000):continue
        bank=tone=index=tail=None
        if (q[3:7]==(0x1a000004,0xe59331c0,0xe3c334ff,0xe3c33cff) and
            q[7]&0xfffff000==0xe3530000 and q[8:11]==(0x0a000001,0xe3a00000,0xe12fff1e) and q[11]>>24==0xea):
            imm=q[7]&255;rot=((q[7]>>8)&15)*2
            value=((imm>>rot)|(imm<<(32-rot)))&0xffffffff if rot else imm
            if value&65535:continue
            bank,tone,index,tail=0,value>>16,7,target(pc+44,q[11])
        elif (q[3]>>24==0x1a and q[4]==0xe5d321c0 and q[5] in (0xe3520001,0xe3520002)
              and q[6]>>24==0x0a and q[7:9]==(0xe3a00000,0xe12fff1e)
              and q[9]==0xe5d33000|(0x1c2+2*(q[5]&15)) and q[10]&0xffffff00==0xe3530000
              and q[11]>>24==0x1a and q[12]>>24==0xea):
            bank,tone,index,tail=q[5]&15,q[10]&255,10,target(pc+48,q[12])
        if bank is None:continue
        tailwords=struct.unpack('<24I',read(tail,96))
        calls=tuple(target(tail+j*4,v) for j,v in enumerate(tailwords) if v>>24==0xeb)
        need(len(calls)>=3,'check tail calls')
        rows.append((bank,tone,pc+index*4,q[index],calls[1:]))
    standard={b:{r[4] for r in rows if r[:2]==(b,11)} for b in range(3)}
    need(all(len(s)==9 for s in standard.values()),'Standard check signatures')
    out=[]
    for s in selected:
        tone=12 if s==2 else s+10
        for bank in range(3):
            hits=[r for r in rows if r[:2]==(bank,tone) and r[4] in standard[bank]]
            need(len(hits)==(2 if s==10 else 9) and len({r[4] for r in hits})==len(hits),'own check count')
            out.extend((r[2],r[3],(r[3]&0xfffff000)|(0x8ff if bank==0 else 0xff)) for r in hits)
    return out


def expected_bytes(raw, selection, profiles, layout, *, model_id=None):
    """Independently reconstruct the exact allowlist, guards and values."""
    need(layout in LAYOUTS,'five-slot layout')
    name,version,family,sha=LAYOUTS[layout]
    need(model_id is None or model_id==MODEL_IDS[family],'explicit model/layout mismatch')
    newer=version in ('2.20','1.70')
    def A(old):
        if not newer:return old
        need(old in NEW_POINTS,'independent exact relocation point '+hex(old))
        return NEW_POINTS[old]
    need(type(raw) is bytes and hashlib.sha256(raw).hexdigest()==sha,'official identity')
    need(type(selection) is dict and 2 in selection and all(type(s) is int and s in ORDER and type(v) is str and v for s,v in selection.items()),'five-slot selection')
    need(type(profiles) is dict and all(v in profiles for v in selection.values()),'profiles')
    def read(a,n):
        off=offset(a,n,len(raw));return raw[off:off+n]
    def word(a):return int.from_bytes(read(a,4),'little')
    expected={}
    def put(a,blob):
        need(type(blob) is bytes and blob,'expected blob')
        offset(a,len(blob),len(raw))
        for i,b in enumerate(blob):
            need(a+i not in expected,'expected overlap');expected[a+i]=b
    def guard(a,old,new):
        need(word(a)==old,'original word guard %08x'%a)
        put(a,struct.pack('<I',new))
    refbytes=(REFERENCE_220 if newer else REFERENCE).read_bytes()
    need(hashlib.sha256(refbytes).hexdigest()==(REFERENCE_220_SHA256 if newer else REFERENCE_SHA256),'independent ARM reference digest')
    reference=json.loads(refbytes)
    mask=sum(1<<ORDER.index(s) for s in selection)
    need(reference.get('schema')==1,'reference schema')
    roles=reference['families'][family][str(mask)]
    need(set(roles)=={'curve_gate','dirty','point','getter','post','composer','common','ma_copy','ma_default','ma_ctor'},'role set')
    addr={}
    for role,row in roles.items():
        a=row['address'];blob=bytes.fromhex(row['hex'])
        need(A(0x53dc3218)<=a<a+len(blob)<=A(0x53dc3848) and a%4==0,'private code range')
        addr[role]=a;put(a,blob)
    # Stock switch tables; neither the declared windows nor producer scans are trusted.
    tables=[]
    for old_start in FUNCTIONS:
        start=old_start+0xf0 if newer else old_start
        words=struct.unpack('<512I',read(start,2048))
        ends=[i for i in range(1,512) if words[i]==0xe1a0c00d]
        need(ends,'stock function boundary')
        for i in range(ends[0]-2):
            inst=words[i];reg=(inst>>12)&15
            if (inst&0xfff00fff==0xe240000b and (inst>>16)&15==reg and
                words[i+1]==0xe350000b|(reg<<16) and words[i+2]&0xfffff000==0x979ff000):
                need(reg in (4,5),'switch register');tables.append(start+4*i+16)
    need(len(tables)==len(set(tables))==102,'102 switch tables')
    for table in tables+[A(0x5318dec0),A(0x531acb28)]:
        std=word(table);need(std%4==0 and 0x53000000<=std<(0x5407b640 if newer else 0x5407b540),'switch target')
        for s in selection:
            a=table+4*((12 if s==2 else s+10)-11);guard(a,word(a),std)
    pipelines={2:(0x536db808,0x536db8fc),8:(0x536db838,0x536dbd00),9:(0x536db840,0x536dbdcc),10:(0x536db848,0x536dbea4),11:(0x536db850,0x536dbf50)}
    for s in selection:
        a,old=pipelines[s];a,old=A(a),A(old);guard(a,branch(a,old,0),branch(a,A(0x536dbaa4),0))
    for a,old,new in own_checks(read,selection,newer=newer):guard(a,old,new)
    for i,old_a in enumerate(ACTIVE):
        a=old_a+0xd8 if newer else old_a
        guard(a,0xe5903004,0xe92d5000);guard(a+4,0xe5d32044,0xe3a0c000|i//9)
        guard(a+8,0xe3520000,branch(a+8,addr['common'],link=True))
    hooks=((0x536e8694,0xe5d4309b,'curve_gate',False),(0x536dd024,0xe5c4100e,'dirty',False),
        (0x536e4774,branch(A(0x536e4774),A(0x536e4720),link=True),'post',True),
        (0x536e87c4,branch(A(0x536e87c4),A(0x536eedf0),link=True),'getter',True),(0x536e87c8,0xe51b3044,'composer',False),
        (0x536df458,branch(A(0x536df458),A(0x531403a8),link=True),'ma_ctor',True),(0x536df4d8,0xe5803008,'ma_default',False),
        (0x536df514,branch(A(0x536df514),A(0x5313fd80),link=True),'ma_copy',True))
    for a,old,role,link in hooks:guard(A(a),old,branch(A(a),addr[role],link=link))
    for i in range(8):guard(A(0x55019848)+4*i,A(0x53dc2bf8),A(0x53d36400))
    if 10 in selection:
        for a,old,new in ((0x536dbeb4,0xe5d39788,0xe3a09000),(0x536dc27c,branch(A(0x536dc27c),A(0x536dc818),0),0xe1a00000),
            (0x536dbebc,0xe5d32796,0xe3a02000),(0x535131fc,branch(A(0x535131fc),A(0x53513274),0),branch(A(0x535131fc),A(0x53513218),0))):guard(A(a),old,new)
    lookup=bytearray(80);post=bytearray(120)
    for i,s in enumerate(ORDER):
        if s not in selection:continue
        profile=profiles[selection[s]]
        need(profile.get('id')==selection[s] and profile.get('model')==name and profile.get('firmware')==version,'profile identity')
        if s==2:record,matrix,knots=A(0x53dc3848),A(0x53dc389c),A(0x53dc38b0)
        else:record,matrix,knots=A(0x53dc2bf8)+84*i,A(0x53dc2d48)+20*i,A(0x53dc2dd8)+198*i
        stock=bytearray(read(A(0x5501900c),84));stock[4:8]=struct.pack('<I',matrix);stock[68:84]=read(A(0x53dc27a4),16)
        put(record,bytes(stock));put(matrix,struct.pack('<9h',*sum(profile['look_matrix_q13'],[])))
        need(len(profile['curves_u14'])==3,'three RGB curves')
        for c,curve in enumerate(profile['curves_u14']):put(knots+66*c,struct.pack('<33H',*nodes(tuple(curve))))
        lookup[16*i:16*i+16]=struct.pack('<4I',record,knots,knots+66,knots+132)
        post[24*i:24*i+24]=struct.pack('<I9hH',record,*sum(profile['post_matrix_q512'],[]),0)
        for alias in ALIASES[s]:put(A(0x55018d94)+4*alias,struct.pack('<I',record))
    put(A(0x53dc3978),bytes(lookup));put(A(0x53dc39c8),bytes(post))
    need(len(expected)<=8192,'expected byte budget')
    return expected


def verify(raw,windows,selection,profiles,*,layout,model_id=None):
    try:
        expected=expected_bytes(raw,selection,profiles,layout,model_id=model_id)
        need(type(windows) is tuple,'window sequence')
        actual={}
        for w in windows:
            need(type(w.address) is int and type(w.expected) is bytes and type(w.replacement) is bytes and 0<len(w.expected)==len(w.replacement)<=512,'window type/size')
            off=offset(w.address,len(w.expected),len(raw))
            need(raw[off:off+len(w.expected)]==w.expected,'window original bytes')
            for i,b in enumerate(w.replacement):
                a=w.address+i;need(a not in actual,'overlapping window');actual[a]=b
        need(actual==expected,'complete independent byte/coverage mismatch')
        return dict(data_verified=True,layout=layout,selected_slots=sorted(selection),shared_bytes_match_reference=True,
            complete_window_coverage_verified=True,original_routing_verified=True,shared_code_verified=False,
            production_approved=False,reference_kind='independent stock routing and numeric reconstruction; frozen reviewed private ARM')
    except (AttributeError,KeyError,TypeError,ValueError,OSError,OverflowError,struct.error) as exc:
        raise ReadbackError('five-slot readback: %s'%exc) from exc


def verify_project(raw,windows,changes,profiles,*,planned_layout,checked_layout,model_id=None):
    need(planned_layout==checked_layout and checked_layout in LAYOUTS,'App layout mismatch')
    need(type(changes) is dict,'project changes');selection={}
    for key,value in changes.items():
        if type(value) is dict and 'film' in value:
            need(type(key) is str and key in ('2','8','9','10','11') and type(value['film']) is str and value['film'],'project film slot')
            selection[int(key)]=value['film']
    return verify(raw,windows,selection,profiles,layout=checked_layout,model_id=model_id)


def _readback_reader(before,read_after,edits,profiles=None,*,layout=None,model_id=None):
    from . import film_profiles
    need(type(edits) is list and 1<=len(edits)<=5 and callable(read_after),'edits/reader')
    selection={}
    for e in edits:
        need(type(e) is dict and set(e)=={'kind','slot','film'} and e['kind']=='film' and type(e['slot']) is int and e['slot'] in ORDER and e['slot'] not in selection and type(e['film']) is str and e['film'],'film edit')
        selection[e['slot']]=e['film']
    if layout is None:
        sha=hashlib.sha256(before).hexdigest();candidates=[k for k,v in LAYOUTS.items() if v[3]==sha]
        need(len(candidates)==1,'original family');layout=candidates[0]
    need(layout in LAYOUTS,'layout')
    supplied={} if profiles is None else profiles
    need(type(supplied) is dict and all(k in selection.values() and k.startswith('user_lut_') for k in supplied),'user profile scope')
    requested_model=MODEL_IDS[LAYOUTS[layout][2]]
    need(model_id is None or model_id==requested_model,'explicit model/layout mismatch')
    model_id=requested_model;resolved={}
    for ident in selection.values():
        resolved[ident]=film_profiles.validate_profile(supplied[ident],ident,model_id) if ident.startswith('user_lut_') else film_profiles.load_profile(ident,model_id)
    expected=expected_bytes(before,selection,resolved,layout,model_id=model_id)
    # Coalesce independently derived coverage, not ranges supplied by a generator.
    addresses=sorted(expected);groups=[]
    for a in addresses:
        if not groups or a!=groups[-1][-1]+1 or len(groups[-1])==512:groups.append([])
        groups[-1].append(a)
    spans=[]
    for group in groups:
        a=group[0];n=len(group);off=offset(a,n,len(before));blob=read_after(off,n)
        need(type(blob) is bytes and blob==bytes(expected[x] for x in group),'generated bytes differ from independent expected')
        spans.append((off,blob))
    return dict(data_verified=True,layout=layout,selected_slots=sorted(selection),shared_bytes_match_reference=True,
        complete_window_coverage_verified=True,original_routing_verified=True,shared_code_verified=False,production_approved=False),tuple(spans)


def readback_reader(before,read_after,edits,profiles=None,*,layout=None,model_id=None):
    try:
        return _readback_reader(before,read_after,edits,profiles,layout=layout,model_id=model_id)
    except (AttributeError,KeyError,TypeError,ValueError,OSError,OverflowError,struct.error) as exc:
        raise ReadbackError('five-slot complete readback: %s'%exc) from exc


def readback(before,after,edits,profiles=None,*,layout=None,model_id=None):
    need(type(before) is bytes and type(after) is bytes and len(before)==len(after),'immutable equal-sized payloads')
    return readback_reader(before,lambda a,n:after[a:a+n],edits,profiles,layout=layout,model_id=model_id)
