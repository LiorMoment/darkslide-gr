"""Selected MIT curve-switch and post-matrix hooks from gr3-film-mods.
Copyright (c) 2026 idontknownihil. See GR3_FILM_MODS_LICENSE.txt.
Local selection and runtime-check modification by LiorMoment, 2026-10-13.
Firmware addresses relocate through fwaddr.A for GR III 2.20 / IIIx 1.70 (LiorMoment, 2026-10-26).
"""
import struct
from ..fwaddr import A as fa
CURVE_LIB = 0x55037604
CSP_CALC = 0x536E4720

def fill_src(sets, selectors, ret):
    """sets: SETS address; selectors: [(tone, sel)]; ret: the function's epilogue word (stock 0x536DD464).
    r1 = tone, r3 = ShootingInfo base (ldr at 0x536DD450)."""
    lib6 = fa(CURVE_LIB) + 24
    sel = '\n'.join(f'cmp r1, #{t}\nmoveq r2, #{s}' for t, s in selectors)
    return f'''
        movw r0, #{sets & 0xFFFF:#x}
        movt r0, #{sets >> 16:#x}
        cmp r1, #20
        addeq r0, r0, #24          @ HDR: curves 0-2 in slots 6-8
        cmp r1, #18
        addeq r0, r0, #48          @ Bleach: full 256-point curves in slots 9-11 (local adaptation)
        ldm r0, {{r4, r5, r7, r8, r9, r10}}
        movw r0, #{lib6 & 0xFFFF:#x}
        movt r0, #{lib6 >> 16:#x}
        stm r0, {{r4, r5, r7, r8, r9, r10}}
        add r3, r3, #0x38000
        mov r2, #0
        {sel}
        strb r2, [r3, #0xa98]
        .word {ret:#x}             @ ldm sp, {{r3-r10, fp, sp, pc}}
    '''

def csp_src(table):
    return f'''
        push {{r0, r1, r4, r5, r6, r7, r8, lr}}
        bl abs({fa(CSP_CALC):#x})
        ldr r0, [sp]               @ YCbCrMatrix object: 9 coefficients
        ldr r1, [sp, #4]
        ldr r3, [r1, #8]           @ look record
        movw r4, #{table & 0xFFFF:#x}
        movt r4, #{table >> 16:#x}
    L:  ldr r12, [r4]
        cmp r12, #0
        beq D
        cmp r12, r3
        addne r4, r4, #24
        bne L
        add r4, r4, #4             @ P, int16 row-major, 512 = 1.0
        sub sp, sp, #36
        mov r12, #0
    C:  ldr lr, [r0, r12]
        lsl lr, lr, #13
        asr lr, lr, #13            @ sign-extend 19 bits
        str lr, [sp, r12]
        add r12, r12, #4
        cmp r12, #36
        bne C
        mov r5, #0                 @ row i * 12
    O:  mov r6, #0                 @ column j * 2
    I:  ldr r7, [sp, r5]
        ldrsh r8, [r4, r6]
        mul lr, r7, r8
        add r2, r5, #4
        ldr r7, [sp, r2]
        add r2, r6, #6
        ldrsh r8, [r4, r2]
        mla lr, r7, r8, lr
        add r2, r5, #8
        ldr r7, [sp, r2]
        add r2, r6, #12
        ldrsh r8, [r4, r2]
        mla lr, r7, r8, lr
        add lr, lr, #256
        asr lr, lr, #9
        lsl lr, lr, #13
        lsr lr, lr, #13
        str lr, [r0], #4
        add r6, r6, #2
        cmp r6, #6
        bne I
        add r5, r5, #12
        cmp r5, #36
        bne O
        add sp, sp, #36
    D:  pop {{r0, r1, r4, r5, r6, r7, r8, pc}}
    '''

def csp_entry(key, P):
    flat = [int(v) for row in P for v in row]
    if not (len(flat) == 9 and all(-32768 <= v < 32768 for v in flat)):
        raise ValueError('post matrix outside signed16 format')
    return struct.pack('<I9hH', key, *flat, 0)
