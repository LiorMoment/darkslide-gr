"""Partial film hook plans using selected MIT upstream code; no firmware export."""
from dataclasses import dataclass
import struct
from .film_arm_assemble import assemble
from ._vendor.gr3_film_hooks import fill_src,csp_src,csp_entry
from .fwaddr import A as fa

@dataclass(frozen=True)
class Window:
    address:int
    expected:bytes
    replacement:bytes

class PlanImage:
    def __init__(self,reader,allowed,max_bytes=8192):
        if not callable(reader) or type(allowed) is not tuple or type(max_bytes) is not int or not 0<max_bytes<=16384:
            raise ValueError("bounded reader and explicit window contract required")
        for a,b in allowed:
            if type(a) is not int or type(b) is not int or not 0<=a<b<=2**32:
                raise ValueError("invalid permitted window")
        self.reader,self.allowed,self.limit=reader,allowed,max_bytes
        self.windows=[];self.closed=False
    def read(self,address,n):
        if type(address) is not int or type(n) is not int or not 0<n<=512 or not 0<=address<address+n<=2**32:
            raise ValueError("invalid bounded read")
        value=self.reader(address,n)
        if type(value) is not bytes or len(value)!=n:raise ValueError("short or mutable source read")
        result=bytearray(value)
        for w in self.windows:
            a=max(address,w.address);b=min(address+n,w.address+len(w.replacement))
            if a<b:result[a-address:b-address]=w.replacement[a-w.address:b-w.address]
        return bytes(result)
    def word(self,address):return struct.unpack("<I",self.read(address,4))[0]
    def write(self,address,data):
        if self.closed:raise ValueError("plan sealed")
        if type(address) is not int or type(data) is not bytes or not data or len(data)>512:
            raise ValueError("typed bounded write required")
        end=address+len(data)
        if not any(a<=address<end<=b for a,b in self.allowed):raise ValueError("outside declared feature windows")
        if any(address<w.address+len(w.replacement) and end>w.address for w in self.windows):
            raise ValueError("overlapping planned writes")
        if sum(len(w.replacement) for w in self.windows)+len(data)>self.limit:
            raise ValueError("partial-plan size limit")
        expected=self.read(address,len(data))
        self.windows.append(Window(address,expected,data))
    def guarded_word(self,address,expected,replacement):
        if type(expected) is not int or type(replacement) is not int or not 0<=expected<2**32 or not 0<=replacement<2**32:
            raise ValueError("typed word guard required")
        if self.word(address)!=expected:raise ValueError("original word mismatch")
        self.write(address,struct.pack("<I",replacement))
    def seal(self):
        for w in self.windows:
            if self.reader(w.address,len(w.expected))!=w.expected:raise ValueError("source changed")
        self.closed=True
        return tuple(self.windows)

def from_official_payload(raw,allowed):
    from .fwread import Payload
    if type(raw) is not bytes:raise ValueError("immutable original payload required")
    from . import fwaddr
    payload=Payload(raw,fwaddr.layout()) if fwaddr.is_220() else Payload(raw)
    def read(address,n):
        begin=payload.off(address);last=payload.off(address+n-1)
        if last-begin!=n-1:raise ValueError("source crosses mapped segments")
        return payload.p[begin:begin+n]
    return PlanImage(read,allowed)

A,A_END=0x536ef61c,0x536ef6dc
B,B_END=0x536ef6fc,0x536ef998
SETS,SETS_END=0x536dd4b8,0x536dd510
LIB=0x55037604
SELECTORS=((18,4),(19,3),(20,3),(21,2))
TARGET_RECORDS=frozenset((0x55019060,0x5501939c,0x55019108,0x55018f94))
HOOK_RANGES=((A,A_END),(B,B_END),(SETS,SETS_END),
             (0x536ef618,0x536ef61c),(0x536dd450,0x536dd458),(0x536e4774,0x536e4778))

def _branch(at,target,link=False,condition=14):
    delta=target-at-8
    if delta%4 or not -(1<<25)<=delta<(1<<25):raise ValueError("branch range")
    return (0xeb000000 if link else (condition<<28)|0x0a000000)|((delta>>2)&0xffffff)

def plan_four_mode_hooks(image,post_by_record):
    if type(image) is not PlanImage or image.closed or image.windows:
        raise ValueError("fresh local plan required")
    if type(post_by_record) is not dict or not 1<=len(post_by_record)<=4:
        raise ValueError("one to four record post matrices required")
    entries=[]
    for key,matrix in post_by_record.items():
        if type(key) is not int or key not in fa(TARGET_RECORDS) or type(matrix) is not list or len(matrix)!=3:
            raise ValueError("aligned record and matrix required")
        for row in matrix:
            if type(row) is not list or len(row)!=3 or any(type(x) is not int or not -32768<=x<=32767 for x in row) or sum(row)!=512:
                raise ValueError("white-preserving signed16 Q512 post matrix required")
        entries.append(csp_entry(key,matrix))
    # Four full 256-point groups: existing library0..2 HDR,3..5 Cross,6..8 Retro,9..11 Bleach.
    # No 33-knot conversion or getter replacement is used.
    library=[image.word(fa(LIB)+4*i) for i in range(12)]
    if any(p>>16!=0x53ef or p%2 for p in library):raise ValueError("unexpected original curve pointers")
    ret=image.word(fa(0x536dd464))
    if ret!=0xe89daff8:raise ValueError("fill return mismatch")
    stub=assemble(csp_src(0),fa(B));table_at=fa(B)+len(stub)
    table=b"".join(entries)+csp_entry(0,[[512,0,0],[0,512,0],[0,0,512]])
    if table_at+len(table)>fa(B_END):raise ValueError("post code/table capacity")
    post=assemble(csp_src(table_at),fa(B))
    fill=assemble(fill_src(fa(SETS),SELECTORS,ret),fa(A))
    if len(fill)>fa(A_END)-fa(A):raise ValueError("curve-switch capacity")
    sets=[library[6:12],library[0:3]+library[9:12],library[6:12]]
    guards=[
        (fa(0x536ef618),0xeddf0be0,_branch(fa(0x536ef618),fa(0x536ef6dc))),
        (fa(0x536dd450),0x159632d0,0xe59632d0),
        (fa(0x536dd454),_branch(fa(0x536dd454),fa(SETS),condition=0),_branch(fa(0x536dd454),fa(A))),
        (fa(0x536e4774),_branch(fa(0x536e4774),fa(0x536e4720),link=True),_branch(fa(0x536e4774),fa(B),link=True))]
    for address,expected,replacement in guards:
        if image.word(address)!=expected:raise ValueError("original word mismatch")
    writes=[(fa(B),post),(table_at,table),(fa(A),fill),
            (fa(SETS),struct.pack("<18I",*[p for group in sets for p in group]))]
    writes += [(address,struct.pack("<I",replacement)) for address,expected,replacement in guards]
    # Preflight every write and source read before publishing any partial result.
    if sum(len(data) for address,data in writes)>image.limit:
        raise ValueError("partial-plan size limit")
    spans=[]
    for address,data in writes:
        end=address+len(data)
        if len(data)>512 or not any(a<=address<end<=b for a,b in image.allowed):
            raise ValueError("outside declared feature windows")
        if any(address<b and end>a for a,b in spans):raise ValueError("overlapping planned writes")
        spans.append((address,end));image.read(address,len(data))
    try:
        for address,data in writes:image.write(address,data)
        windows=image.seal()
    except Exception:
        image.windows.clear();image.closed=True
        raise
    return {"windows":windows,"curve_points":256,"modes":("bleach","retro","hdr","cross"),
            "area_a_used":len(fill),"area_b_used":len(post)+len(table),
            "getter_replaced":False,"complete_image_created":False,
            "records_and_curves_written":False,"native_effect_verified":False}
