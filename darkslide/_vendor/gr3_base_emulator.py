"""Read-only-original ARM model, adapted from gr3-film-mods emu/run_tuning (MIT).
Copyright (c) 2026 idontknownihil. See GR3_FILM_MODS_LICENSE.txt.
Modified by LiorMoment: caller-supplied pinned RTOS; no fw import/CLI/files;
bounded allocation/calls, four explicit slider bytes and diagnostic stubs. Guest RAM is a synthetic model,
not a camera state. No firmware patches are installed or exported.
Firmware addresses and the RTOS/data layout follow fwaddr for GR III 2.20 / IIIx 1.70 (LiorMoment, 2026-10-26).
"""
import struct
from ..fwaddr import A as fa
from .. import fwaddr

BASE=0x53000000
HEAP,STACK,STOP=0x60000000,0x5f000000,0x5e000000
DEFAULTS={**{o:4 for o in (0x77d,0x77e,0x77f,0x780,0x781,0x782,0x784,0x787,0x792,0x793,0x794)},0x783:1,0x786:1,0x798:1}
ZERO_STUBS={0x536b82d4,0x5313fbb8,0x536decb4,0x536e4634,0x536e4de4,
            0x536e5070,0x536e7348,0x536eaf80,0x536ec3e0,0x536eb0b0,0x536e9d1c}


class OriginalEmulator:
    def __init__(self,rtos):
        from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_INTR,UC_HOOK_MEM_WRITE,UC_PROT_READ,UC_PROT_EXEC
        from unicorn import arm_const
        L=fwaddr.layout()
        if type(rtos) is not bytes or len(rtos)!=L["rtos_size"]:raise ValueError("fixed original RTOS required")
        # Addresses relocated once here (2.10 / 1.60: unchanged), not in the per-instruction hook.
        self.size=L["rtos_size"]
        self.frame_functions=(fa(0x536e7b64),fa(0x536e82d0));self.allocators=(fa(0x536af2a8),fa(0x536af31c))
        self.frees=(fa(0x536af2e4),fa(0x536af358));self.assert_function=fa(0x536b93d0)
        self.zero_stubs=fa(frozenset(ZERO_STUBS));self.svc6_pc=fa(0x53050ecc)
        self.A=arm_const;self.u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        self.u.mem_map(BASE,(len(rtos)+65535)&~65535);self.u.mem_write(BASE,rtos)
        self.u.mem_protect(BASE,(len(rtos)+65535)&~65535,UC_PROT_READ|UC_PROT_EXEC)
        self.u.mem_map(0x55000000,0x1000000)
        lma=L["data_lma"]-BASE
        self.u.mem_write(0x55000000,rtos[lma:lma+L["data_size"]])
        self.u.mem_map(HEAP,0x4000000);self.u.mem_map(STACK-0x100000,0x100000);self.u.mem_map(STOP,0x1000)
        self.u.reg_write(arm_const.UC_ARM_REG_C1_C0_2,0xf00000)
        self.u.reg_write(arm_const.UC_ARM_REG_FPEXC,0x40000000)
        self.brk=HEAP;self.assertions=[];self.services=[];self.stub_calls={};self.allocations=[];self.frames=[];self.phase="init";self.writes=[]
        self.u.hook_add(UC_HOOK_CODE,self._code);self.u.hook_add(UC_HOOK_INTR,self._intr)
        self.u.hook_add(UC_HOOK_MEM_WRITE,self._write)

    def alloc(self,size):
        if type(size) is not int or not 0<size<=0x100000 or self.brk+size>HEAP+0x4000000:
            raise ValueError("synthetic allocation bounds")
        address=self.brk;self.brk=(address+size+63)&~63
        self.allocations.append((address,address+size));self.u.mem_write(address,bytes(size));return address

    def _return(self,value):
        self.u.reg_write(self.A.UC_ARM_REG_R0,value)
        self.u.reg_write(self.A.UC_ARM_REG_PC,self.u.reg_read(self.A.UC_ARM_REG_LR))

    def _code(self,u,address,size,_):
        if address in self.frame_functions:
            frame=u.reg_read(self.A.UC_ARM_REG_R2)
            words=struct.unpack("<7I",u.mem_read(frame,28))
            self.frames.append(dict(phase=self.phase,function=hex(address),frame_address=hex(frame),words=list(words),p64_at_frame24=words[6]))
        if address in self.allocators:self._return(self.alloc(u.reg_read(self.A.UC_ARM_REG_R0)))
        elif address in self.frees:self._return(0)
        elif address==self.assert_function:
            if not u.reg_read(self.A.UC_ARM_REG_R0):self.assertions.append((u.reg_read(self.A.UC_ARM_REG_LR),u.reg_read(self.A.UC_ARM_REG_R3)))
            self._return(0)
        elif address in self.zero_stubs:
            self.stub_calls[address]=self.stub_calls.get(address,0)+1;self._return(0)

    def _intr(self,u,number,_):
        pc=u.reg_read(self.A.UC_ARM_REG_PC)
        thumb=bool(u.reg_read(self.A.UC_ARM_REG_CPSR)&32)
        op=int.from_bytes(u.mem_read(pc-(2 if thumb else 4),2 if thumb else 4),"little")
        svc=(op&0xff00)==0xdf00 if thumb else (op&0x0f000000)==0x0f000000
        immediate=op&255 if thumb else op&0xffffff
        allowed=immediate in (0,8) or (immediate==6 and not thumb and pc==self.svc6_pc)
        if number!=2 or not svc or not allowed:raise ValueError("undeclared interrupt/service: number=%d pc=%#x thumb=%s instruction=%#x immediate=%#x"%(number,pc,thumb,op,immediate))
        self.services.append(dict(phase=self.phase,interrupt=number,svc=immediate));u.reg_write(self.A.UC_ARM_REG_R0,0)

    def _write(self,u,access,address,size,value,_):
        if self.phase=="make" and HEAP<=address<HEAP+0x4000000:self.writes.append((address,size))

    def call(self,address,*args):
        if not BASE<=address<BASE+self.size or len(args)>4:raise ValueError("original call boundary")
        for i in range(13):self.u.reg_write(getattr(self.A,"UC_ARM_REG_R"+str(i)),0)
        for i,value in enumerate(args):self.u.reg_write(getattr(self.A,"UC_ARM_REG_R"+str(i)),value)
        self.u.reg_write(self.A.UC_ARM_REG_SP,STACK-0x100);self.u.reg_write(self.A.UC_ARM_REG_LR,STOP)
        self.u.reg_write(self.A.UC_ARM_REG_CPSR,0x13)
        self.u.emu_start(address,STOP,count=5_000_000,timeout=15_000_000)
        if self.u.reg_read(self.A.UC_ARM_REG_PC)!=STOP:raise ValueError("original call did not return within bounds")

    def word(self,address):return struct.unpack("<I",self.u.mem_read(address,4))[0]
    def putword(self,address,value):self.u.mem_write(address,struct.pack("<I",value))
    def putbyte(self,address,value):self.u.mem_write(address,bytes((value,)))

    def standard_base(self,sliders=None):
        if sliders is not None and (type(sliders) is not dict or any(type(k) is not int for k in sliders) or set(sliders)!={0x77f,0x780,0x781,0x782}
                or any(type(v) is not int or not 0<=v<=8 for v in sliders.values())):
            raise ValueError("four encoded slider bytes required")
        obj=self.alloc(0x400);cfg=self.alloc(0x1000)
        self.putword(obj,fa(0x53dbd8a0))
        self.call(fa(0x536da1fc),obj,cfg);self.call(fa(0x536da62c),obj,cfg);self.putword(obj,fa(0x53dbd8a0))
        gamma=self.word(obj+0x17c)
        if not any(a<=gamma and gamma+512<=b for a,b in self.allocations):raise ValueError("synthetic gamma object extent")
        info=self.alloc(0x9000);si=self.alloc(0x80000)
        self.putbyte(info+0x82ac,1);self.putword(info+0x82d0,si)
        st=si+0x71248
        for offset,value in DEFAULTS.items():self.putbyte(st+offset,value)
        for offset,value in (sliders or {}).items():self.putbyte(st+offset,value)
        self.putbyte(st+0x77c,11)
        self.phase="make";self.frames=[];self.writes=[]
        self.call(fa(0x536dd8f0),obj,info)
        post_gamma=self.word(obj+0x17c)
        if not any(a<=post_gamma and post_gamma+512<=b for a,b in self.allocations):raise ValueError("post-call gamma extent")
        self.gamma_identity=dict(before=hex(gamma),after=hex(post_gamma),same=gamma==post_gamma)
        if self.assertions:raise ValueError("assertion in original model")
        if not self.frames:raise ValueError("base generation not reached; no cache validity proof")
        written=set()
        for address,size in self.writes:
            written.update(range(max(address,post_gamma),min(address+size,post_gamma+512)))
        if len(written)!=512:raise ValueError("Standard Make did not write all512 base bytes")
        self.base_write_coverage=512
        values=tuple(struct.unpack("<256H",self.u.mem_read(post_gamma,512)))
        if not any(values):raise ValueError("zero base curve has no validity proof")
        return values
