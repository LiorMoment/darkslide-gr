"""Independent v29 sparse data reader. No writer imports or release approval."""
from fractions import Fraction
from functools import lru_cache
import hashlib
import struct
from .fwaddr import A as fa
from . import fwaddr

LAYOUT = 'gr3x160_inplace_cache_exit_v1'
ORIGINAL_SHA = 'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'
BASE = 0x53dc2bf8
ALIASES = {8:(18,22,23),9:(19,),10:(20,24),11:(21,)}

class ReadbackError(ValueError):
    pass

def need(ok, reason):
    if not ok:
        raise ReadbackError(reason)

@lru_cache(maxsize=96)
def nodes(curve):
    """Solve the 33-variable least-squares system by dense Gauss-Jordan."""
    need(len(curve)==256 and all(type(v) is int and 0<=v<=16383 for v in curve), 'curve')
    a=[[Fraction(0) for _ in range(34)] for _ in range(33)]
    for x,y in enumerate(curve):
        k,t=divmod(x,8)
        weights=((k,8-t),(k+1,t))
        for i,u in weights:
            a[i][33]+=u*y*8
            for j,v in weights:a[i][j]+=u*v
    for k in range(33):
        pivot=a[k][k];need(pivot!=0,'singular fit')
        a[k]=[v/pivot for v in a[k]]
        for i in range(33):
            if i!=k and a[i][k]:
                scale=a[i][k]
                a[i]=[x-scale*y for x,y in zip(a[i],a[k])]
    return tuple(min(16383,max(0,(v[33]+Fraction(1,2)).numerator//(v[33]+Fraction(1,2)).denominator)) for v in a)

def _verify(raw, windows, selection, profiles, *, layout):
    """selection is the complete saved slot->profile-id snapshot, not a delta.

    Returns data_verified only. Shared instruction semantics are NOT verified.
    Never use this result alone to authorize export or installation.
    """
    need(layout==LAYOUT,'layout mismatch')
    need(type(raw) is bytes and hashlib.sha256(raw).hexdigest()==fwaddr.payload_sha(ORIGINAL_SHA),'original identity')
    need(type(selection) is dict and all(type(s) is int and s in ALIASES and type(n) is str for s,n in selection.items()),'selection')
    need(type(profiles) is dict and all(n in profiles for n in selection.values()),'profiles')
    need(type(windows) is tuple,'windows')
    def original(at,n):
        if fa(0x55000000)<=at and at+n<=fa(0x5503a080):at=at-fa(0x55000000)+fa(0x5407b540)
        need(type(at) is int and type(n) is int and n>0 and fa(0x53000000)<=at and at+n<=fa(0x540b55c0),'address mapping')
        off=at-fa(0x53000000)+0x10870
        return raw[off:off+n]
    overlay={}
    for w in windows:
        need(type(w.address) is int and type(w.expected) is bytes and type(w.replacement) is bytes,'window types')
        need(0<len(w.expected)==len(w.replacement)<=512,'window size')
        need(not (w.address < fa(0x540b55c0) and w.address+len(w.expected) > fa(0x5407b540)),'noncanonical data alias')
        need(original(w.address,len(w.expected))==w.expected,'original bytes')
        for i,b in enumerate(w.replacement):
            at=w.address+i;need(at not in overlay,'overlap');overlay[at]=b
    need(len(overlay)<=8192,'sparse limit')
    if not selection:need(not windows,'empty selection changed bytes')
    def read(at,n):
        b=original(at,n)
        return bytes(overlay.get(at+i,v) for i,v in enumerate(b))
    for s in ALIASES:
        i=s-8;record=fa(BASE)+84*i;matrix=fa(BASE)+336+20*i;knots=fa(BASE)+480+198*i
        lookup=fa(BASE)+416+16*i;post=fa(BASE)+1272+24*i
        if s in selection:
            p=profiles[selection[s]]
            need(p.get('id')==selection[s] and p.get('model')=='GR IIIx' and p.get('firmware')==fwaddr.firmware_name('1.60'),'profile identity')
            need(read(lookup,16)==struct.pack('<4I',record,knots,knots+66,knots+132),'lookup')
            expected_record=bytearray(original(fa(0x5501900c),84))
            expected_record[4:8]=struct.pack('<I',matrix)
            expected_record[68:84]=original(fa(0x53dc27a4),16)
            need(read(record,84)==bytes(expected_record),'slot record')
            need(read(matrix,18)==struct.pack('<9h',*sum(p['look_matrix_q13'],[])),'look matrix')
            need(read(post,24)==struct.pack('<I9hH',record,*sum(p['post_matrix_q512'],[]),0),'post matrix')
            for channel,curve in enumerate(p['curves_u14']):
                need(channel<3,'curve count')
                need(read(knots+66*channel,66)==struct.pack('<33H',*nodes(tuple(curve))),'curve nodes')
            need(len(p['curves_u14'])==3,'curve count')
        elif selection:
            need(read(lookup,16)==bytes(16) and read(post,24)==bytes(24),'uninstalled lookup')
            for at,n in ((record,84),(matrix,18),(knots,198)):
                need(read(at,n)==original(at,n),'uninstalled data')
        for alias in ALIASES[s]:
            at=fa(0x55018d94)+alias*4
            need(read(at,4)==(struct.pack('<I',record) if s in selection else original(at,4)),'slot alias')
    return dict(data_verified=True,layout=layout,selected_slots=sorted(selection),
                shared_code_verified=False,production_approved=False)


def verify(raw, windows, selection, profiles, *, layout):
    """Read selected data fields only; malformed input is always rejected."""
    try:
        return _verify(raw,windows,selection,profiles,layout=layout)
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError,struct.error) as exc:
        raise ReadbackError(str(exc)) from exc
