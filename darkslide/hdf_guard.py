"""Read-only HDF adaptation guard. Does not identify connected hardware or write firmware.
The body is an explicit user declaration; package identity identifies only its family.
Protected windows are a bounded regression set, NOT an exhaustive compatibility proof.
"""
import argparse, hashlib, json
from pathlib import Path

BASE, OFFSET, SIZE=0x53000000,0x10870,0x10b55c0
FAMILIES={
 'GR III':{'version':'2.10','raw_sha256':'d6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4','payload_sha256':'53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703'},
 'GR IIIx':{'version':'1.60','raw_sha256':'708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7','payload_sha256':'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630'},
}
BODIES={
 'GR III':('GR III',1,0x1320e,0,0x53d62194,'GR_III'),
 'GR III HDF':('GR III',1,0x13216,1,0x53d621a8,'GR3_HDF'),
 'GR IIIx':('GR IIIx',0,0x1329a,0,0x53d621fc,'GR_IIIX'),
 'GR IIIx HDF':('GR IIIx',0,0x1329c,1,0x53d62124,'GR3x_HDF'),
}
WINDOWS=[
 ('project_and_filter_initialization',0x536b40f0,0x536b448c),
 ('project_filter_getters',0x536b448c,0x536b468c),
 ('body_descriptors',0x53d62124,0x53d62210),
 ('body_names',0x53d5fde8,0x53d5fe10),
 ('folder_names',0x53d21a84,0x53d21ab0),
 ('folder_selector',0x53588b70,0x53588be4),
 ('folder_pointer_table',0x5409397c,0x540939bc),
 ('hdf_exposure_adjust',0x5346d860,0x5346d950),
 ('hdf_preset_processing',0x5350cd44,0x5350d2e4),
 ('shutdown_selection',0x5319f180,0x5319f2bc),
 ('collabo_command_setter',0x53290b2c,0x53290c50),
 ('collabo_service_setter',0x536bf2e4,0x536bf358),
 ('collabo_service_wrappers',0x536bf670,0x536bf6c0),
 ('hdf_setting_getter',0x53551980,0x5355198c),
 ('hdf_setting_accessor',0x5354b520,0x5354b564),
 ('white_balance_factory',0x53709628,0x53709b00),
 ('still_white_balance_region',0x5370074c,0x5370263c),
 ('movie_white_balance_region',0x5370263c,0x53702900),
 ('wave_detect_correct_region',0x536c2a30,0x536c3898),
 ('aperture_correction_region',0x53492234,0x53492720),
]
# GR III 2.20 / GR IIIx 1.70 (rebuilt main system, 2026-10-21). Same body table values, same 20
# windows re-located; collabo_service_wrappers was redrawn because 2.20 rewrote that code (five value
# branches became one). Located by the author's own function-by-function comparison of the two
# official packages (2026-10-25).
SIZE_220=0x10b5700
FAMILIES_220={
 'GR III':{'version':'2.20','raw_sha256':'5c53760b3b564c87239dbf7f5c94d0f6fd91accd156ec4ba672ebfd6b861793e','payload_sha256':'5b475fcc434dec0f12e7956b02b5b38959d599b7a4ff90486263c46816a21b6d'},
 'GR IIIx':{'version':'1.70','raw_sha256':'aa8e3ae1349d94e83be381c20530e41d5e57b75c27ef24ba81c7b017d6c11592','payload_sha256':'1129b9289b3cc68ab6f407a87d60db750483cc5ae41a6609596f6fe676b14521'},
}
DESCRIPTORS_220={'GR III':0x53d61b74,'GR III HDF':0x53d61b88,'GR IIIx':0x53d61bdc,'GR IIIx HDF':0x53d61b04}
FILTER_CALL_TARGET,FILTER_CALL_TARGET_220=0x536b454c,0x536b463c
WINDOWS_220=[
 ('project_and_filter_initialization',0x536b41e0,0x536b457c),
 ('project_filter_getters',0x536b457c,0x536b477c),
 ('body_descriptors',0x53d61b04,0x53d61bf0),
 ('body_names',0x53d5f7c8,0x53d5f7f0),
 ('folder_names',0x53d21464,0x53d21490),
 ('folder_selector',0x53588c60,0x53588cd4),
 ('folder_pointer_table',0x54093a84,0x54093ac4),
 ('hdf_exposure_adjust',0x5346d950,0x5346da40),
 ('hdf_preset_processing',0x5350ce34,0x5350d3d4),
 ('shutdown_selection',0x5319f240,0x5319f390),
 ('collabo_command_setter',0x53290c04,0x53290d34),
 ('collabo_service_setter',0x536bf320,0x536bf394),
 ('collabo_service_wrappers',0x536bf67c,0x536bf698),
 ('hdf_setting_getter',0x53551a70,0x53551a7c),
 ('hdf_setting_accessor',0x5354b610,0x5354b654),
 ('white_balance_factory',0x53709600,0x53709ad8),
 ('still_white_balance_region',0x53700724,0x53702614),
 ('movie_white_balance_region',0x53702614,0x537028d8),
 ('wave_detect_correct_region',0x536c2a08,0x536c3870),
 ('aperture_correction_region',0x53492324,0x53492810),
]

def need(ok,message):
 if not ok:raise ValueError(message)

def sha(b):return hashlib.sha256(b).hexdigest()
def window(p,a,b,size=SIZE):
 need(BASE<=a<b<=BASE+size,'invalid window')
 return p[a-BASE+OFFSET:b-BASE+OFFSET]

def generation(original,family):
 """(info, windows, rtos size, filter-call target, is 2.20) for the official payload of this family."""
 digest=sha(original)
 if digest==FAMILIES[family]['payload_sha256']:return FAMILIES[family],WINDOWS,SIZE,FILTER_CALL_TARGET,False
 if digest==FAMILIES_220[family]['payload_sha256']:return FAMILIES_220[family],WINDOWS_220,SIZE_220,FILTER_CALL_TARGET_220,True
 raise ValueError('official decoded identity does not match body family')

def direct_calls(p,target=0x536b454c):
 """Decode aligned A32 BL immediate words; static candidates, not a complete call graph."""
 out=[]
 for off in range(0,0x8f0000,4):
  word=int.from_bytes(p[OFFSET+off:OFFSET+off+4],'little')
  if word&0x0f000000!=0x0b000000 or word>>28==15:continue
  imm=word&0xffffff
  if imm&0x800000:imm-=0x1000000
  if BASE+off+8+4*imm==target:out.append(BASE+off)
 return out

def inspect(original,body,candidate=None):
 need(body in BODIES,'unknown body; explicit supported body selection required')
 family,project,pid,kind,descriptor,folder=BODIES[body]
 info,table,size,target,new=generation(original,family)
 if new:descriptor=DESCRIPTORS_220[body]
 windows=list(table)
 # Protect each observed direct branch instruction and its local context as well.
 calls=direct_calls(original,target)
 windows.extend(('filter_call_'+hex(a),a-16,a+32) for a in calls)
 if candidate is not None:need(len(candidate)==len(original),'payload length changed: review required')
 rows=[]
 for name,a,b in windows:
  src=window(original,a,b,size)
  unchanged=None if candidate is None else src==window(candidate,a,b,size)
  rows.append({'name':name,'start':hex(a),'end_exclusive':hex(b),'sha256':sha(src),'unchanged':unchanged})
 changed=[x['name'] for x in rows if x['unchanged'] is False]
 return {'body_declared':body,'family':family,'firmware':info['version'],
  'body_detected':False,'project_type':project,'stored_product_id':hex(pid),
  'filter_type':kind,'descriptor':hex(descriptor),'folder':folder,
  'official_payload_sha256':sha(original),
  'candidate_payload_sha256':None if candidate is None else sha(candidate),
  'status':'baseline_only' if candidate is None else ('review_required' if changed else 'protected_windows_unchanged'),
  'compatibility_approved':False,'device_tested':False,
  'changed_windows':changed,'direct_call_candidates':[hex(a) for a in calls],'protected_windows':rows,
  'limitations':['package does not identify the connected body','bounded windows do not prove all runtime paths','no device validation','shutdown setting value on HDF hardware remains unverified']}

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--official-payload',type=Path,required=True)
 ap.add_argument('--body',choices=list(BODIES),required=True);ap.add_argument('--candidate-payload',type=Path)
 args=ap.parse_args()
 result=inspect(args.official_payload.read_bytes(),args.body,None if args.candidate_payload is None else args.candidate_payload.read_bytes())
 print(json.dumps(result,ensure_ascii=False,indent=2))
 if result['changed_windows']:raise SystemExit(2)
if __name__=='__main__':main()


def inspect_sparse(original, body, windows):
    """Validate canonical sparse edits without constructing a complete candidate.

    windows: tuple of dict(address: VA, expected: bytes, replacement: bytes).
    RAM initial-data addresses are canonicalized to their flash source. Aliased
    overlaps, wrong originals, noninteger addresses and incomplete windows fail.
    """
    result=inspect(original,body)
    need(result['firmware'] in ('2.10','1.60','2.20','1.70'),'sparse plans are only checked on GR III 2.10 / 2.20, GR IIIx 1.60 / 1.70')
    new_firmware=result['firmware'] in ('2.20','1.70')
    size=SIZE_220 if new_firmware else SIZE
    ram,ram_end,lma=(0x55000000,0x5503a0c0,0x5407b640) if new_firmware else (0x55000000,0x5503a080,0x5407b540)
    need(type(windows) is tuple,'windows must be a tuple')
    overlay={}
    for w in windows:
        need(type(w) is dict and set(w)=={'address','expected','replacement'},'window schema')
        a=w['address'];old=w['expected'];new=w['replacement']
        need(type(a) is int and type(old) is bytes and type(new) is bytes,'window types')
        need(0<len(old)==len(new)<=8192,'window length')
        if ram<=a and a+len(old)<=ram_end:
            a=a-ram+lma
        need(BASE<=a<a+len(old)<=BASE+size,'window outside RTOS or initialized data')
        need(window(original,a,a+len(old),size)==old,'expected original mismatch')
        for i,b in enumerate(new):
            need(a+i not in overlay,'overlapping canonical window')
            overlay[a+i]=b
        need(len(overlay)<=65536,'sparse limit')
    changed=[]
    for row in result['protected_windows']:
        a,b=int(row['start'],16),int(row['end_exclusive'],16)
        src=window(original,a,b,size)
        same=all(overlay.get(a+i,v)==v for i,v in enumerate(src))
        row['unchanged']=same
        if not same:changed.append(row['name'])
    result.update(status='review_required' if changed else 'protected_windows_unchanged',
                  changed_windows=changed,sparse_bytes=len(overlay),complete_candidate_created=False)
    return result


def shutdown_routes(original, body):
    """Read all factory collaboration routes without guessing a body setting."""
    need(inspect(original,body)['firmware'] in ('2.10','1.60'),'shutdown routes are only read on GR III 2.10 / GR IIIx 1.60')
    import struct
    result=[]
    for mode,request,label in [(0,2,'ordinary'),(1,3,'Street'),(2,11,'Urban'),(3,10,'ING'),(4,12,'Diary')]:
        # Table starts at request 1, RAM 0x55002b78 uses index=request.
        ptr=struct.unpack('<I',window(original,0x5407e0b8+request*4,0x5407e0bc+request*4))[0]
        width,height,path=struct.unpack('<HHI',window(original,ptr,ptr+8))
        text=window(original,path,path+96).split(b'\0')[0].decode('ascii')
        result.append(dict(collabo_mode=mode,label=label,request=request,descriptor=hex(ptr),path=text,width=width,height=height))
    return {'body_declared':body,'actual_collabo_mode':None,'routes':result,'hardware_setting_inferred':False}
