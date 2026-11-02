"""Step-8 knot sampling adapted from gr3-film-mods film_cave.py (MIT).
Copyright (c) 2026 idontknownihil. See GR3_FILM_MODS_LICENSE.txt.
Modified by LiorMoment: current-record selection, fixed full quartet, no global
curve-pointer publication. Selected consumers now sample readonly knots directly.
Firmware addresses relocate through fwaddr.A for GR III 2.20 / IIIx 1.70 (LiorMoment, 2026-10-26).
"""
from ..fwaddr import A as fa


def expand_source():
    return '''
        ldr r1, [r0]
        str r7, [r0]
        mov r2, #0
    E:  lsr r3, r2, #3
        add r3, r1, r3, lsl #1
        ldrh r12, [r3]
        ldrh r3, [r3, #2]
        sub r3, r3, r12
        and r8, r2, #7
        mul r3, r8, r3
        add r12, r12, r3, asr #3
        strh r12, [r7], #2
        add r2, r2, #1
        cmp r2, #256
        bne E
        bx lr
    '''


def getter_source(table, fallback_table, remap):
    # The existing composite stores incoming P at fp-48 and Gamma at fp-44.
    # r4-r8 and LR survive readonly-knot selection and the stock fallback.
    return f'''
        push {{r4, r5, r6, r7, r8, lr}}
        mov r4, r1
        mov r5, r2
        mov r6, r3
        ldr r12, [r11, #-0x48]
        ldr r12, [r12, #8]
        movw r2, #{table & 65535:#x}
        movt r2, #{table >> 16:#x}
    L:  ldr r3, [r2]
        cmp r3, #0
        beq D
        cmp r3, r12
        addeq r2, r2, #4
        beq S
        add r2, r2, #16
        b L
    S:  ldr r3, [r2]
        str r3, [r4]
        ldr r3, [r2, #4]
        str r3, [r5]
        ldr r3, [r2, #8]
        str r3, [r6]
        mov r3, #0
        strb r3, [r0]
        pop {{r4, r5, r6, r7, r8, pc}}
    D:  mov r1, r4
        mov r2, r5
        mov r3, r6
        bl abs({fa(0x536eedf0):#x})
        movw r7, #{fallback_table & 65535:#x}
        movt r7, #{fallback_table >> 16:#x}
        mov r0, r4
        bl abs({remap:#x})
        mov r0, r5
        bl abs({remap:#x})
        mov r0, r6
        bl abs({remap:#x})
        pop {{r4, r5, r6, r7, r8, pc}}
    '''


def point_source():
    """Exactly compose the virtual 256-point curve; no expanded memory.

    Input R0 is unsigned16 Base, R1 points to 33 readonly u14 knots. R0
    returns the value. R4..R11 survive; only LR uses four temporary stack bytes.
    Step-8 expansion floors negative differences; Q6 composition truncates
    negative differences toward zero, matching the native two-stage rounding.
    """
    return '''
        push {lr}
        lsr r2, r0, #6
        cmp r2, #255
        movhs r2, #0xff
        and r12, r0, #63
        movhs r12, #0
        lsr r3, r2, #3
        add lr, r1, r3, lsl #1
        and r2, r2, #7
        ldrh r0, [lr]
        ldrh r3, [lr, #2]
        sub r1, r3, r0
        mul r1, r2, r1
        add r0, r0, r1, asr #3
        ldrh r1, [lr]
        sub r3, r3, r1
        add r2, r2, #1
        mul r3, r2, r3
        add r1, r1, r3, asr #3
        sub r1, r1, r0
        mul r1, r12, r1
        cmp r1, #0
        addlt r1, r1, #63
        add r0, r0, r1, asr #6
        pop {pc}
    '''


def composer_source(table, point):
    """Selected RGB/Y composition; unknown records resume the native consumer.

    The native outer function owns the saved registers, canary and final flags.
    No new object, global state or large stack buffer is introduced.
    """
    return f'''
        ldr r0, [r11, #-0x48]
        ldr r0, [r0, #8]
        movw r1, #{table & 65535:#x}
        movt r1, #{table >> 16:#x}
    L:  ldr r2, [r1]
        cmp r2, #0
        beq D
        cmp r0, r2
        beq S
        add r1, r1, #16
        b L
    D:  ldr r3, [r11, #-0x44]
        b abs({fa(0x536e87cc):#x})
    S:  ldr r4, [r11, #-0x44]
        ldr r7, [r11, #-0x3c]
        ldr r8, [r11, #-0x38]
        ldr r9, [r11, #-0x34]
        mov r10, #0
    P:  ldrh r0, [r4, r10]
        mov r1, r7
        bl abs({point:#x})
        add r1, r4, #0x2a00
        add r1, r1, #8
        strh r0, [r1, r10]
        mov r1, #77
        mul r5, r0, r1
        ldrh r0, [r4, r10]
        mov r1, r8
        bl abs({point:#x})
        add r1, r4, #0x2c00
        add r1, r1, #8
        strh r0, [r1, r10]
        mov r1, #150
        mla r5, r0, r1, r5
        ldrh r0, [r4, r10]
        mov r1, r9
        bl abs({point:#x})
        add r1, r4, #0x2e00
        add r1, r1, #8
        strh r0, [r1, r10]
        mov r1, #29
        mla r5, r0, r1, r5
        asr r0, r5, #8
        add r1, r4, #0x3000
        add r1, r1, #8
        strh r0, [r1, r10]
        add r10, r10, #2
        cmp r10, #512
        bne P
        b abs({fa(0x536e89cc):#x})
    '''


def remap_source():
    """Only per-call outputs are guarded; the original LIB is never written."""
    return '''
        ldr r1, [r0]
        mov r2, r7
        mov r3, #3
    M:  ldr r8, [r2], #8
        cmp r1, r8
        ldreq r1, [r2, #-4]
        streq r1, [r0]
        bxeq lr
        subs r3, r3, #1
        bne M
        bx lr
    '''
