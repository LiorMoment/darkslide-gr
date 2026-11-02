"""GR IIIx 1.60 sparse curve adaptation of the pinned Darkslide GR III v29 work.
No firmware export, installation, or runtime approval. See gr3x_inplace_sources.json.
"""
import math
from fractions import Fraction
from .film_requests import FilmRequestError
from ._vendor.gr3_film_hooks import csp_src
from ._vendor.gr3_four_curve_hooks import composer_source
from .fwaddr import A as fa
MODE_CHANGE_WORDS=((0x536DCB64,0xE51B2034),(0x536DCB6C,0xE5D431A8),(0x536DCB74,0xE0533002),(0x536DCB7C,0x13A03001),(0x536DCB80,0xE50B30CC),(0x536DCDFC,0xE5C421A8))
def expand_knots(knots):
    if type(knots) not in (tuple, list) or len(knots) != 33 or any(type(v) is not int or not 0 <= v <= 16383 for v in knots):
        raise FilmRequestError('曲线压缩节点格式错误')
    return tuple(knots[i // 8] + ((knots[i // 8 + 1] - knots[i // 8]) * (i % 8) >> 3) for i in range(256))


def fit_knots(curve):
    """Local tridiagonal least-squares fit, using the published step-8 format.

    Clipping and quantization are reported through reconstruction errors. This
    does not imply lossless compression or a measured camera colour error.
    """
    if type(curve) is not list or len(curve) != 256 or any(type(v) is not int or not 0 <= v <= 16383 for v in curve):
        raise FilmRequestError('需要完整的256点u14曲线')
    diagonal, upper, rhs = [0] * 33, [0] * 32, [0] * 33
    for i, value in enumerate(curve):
        k, fraction = divmod(i, 8)
        b, a = fraction, 8 - fraction
        diagonal[k] += a * a
        diagonal[k + 1] += b * b
        upper[k] += a * b
        rhs[k] += a * value * 8
        rhs[k + 1] += b * value * 8
    for k in range(1, 33):
        ratio = Fraction(upper[k - 1]) / diagonal[k - 1]
        diagonal[k] -= ratio * upper[k - 1]
        rhs[k] -= ratio * rhs[k - 1]
    solution = [Fraction(0)] * 33
    solution[-1] = rhs[-1] / diagonal[-1]
    for k in range(31, -1, -1):
        solution[k] = (rhs[k] - upper[k] * solution[k + 1]) / diagonal[k]
    knots = tuple(min(16383, max(0, math.floor(v + Fraction(1, 2)))) for v in solution)
    rebuilt = expand_knots(knots)
    errors = [a - b for a, b in zip(rebuilt, curve)]
    return knots, dict(max_abs_u14=max(abs(v) for v in errors),
                       rms_u14=math.sqrt(sum(v * v for v in errors) / 256),
                       lossless=all(v == 0 for v in errors))

def _numeric(profile, envelope):
    look, post = profile['look_matrix_q13'], profile['post_matrix_q512']
    if (any(not envelope['look_min'] <= v <= envelope['look_max']
            for row in look for v in row)
            or any(sum(abs(v) for v in row) > envelope['look_row_abs_max']
                   for row in look)
            or any(not envelope['post_min'] <= v <= envelope['post_max']
                   for row in post for v in row)
            or any(sum(abs(post[i][j]) for i in range(3))
                   > envelope['post_col_abs_max'] for j in range(3))):
        raise FilmRequestError('胶片矩阵超出原厂数值边界')
    packed, errors = [], []
    for curve in profile['curves_u14']:
        knots, error = fit_knots(curve)
        rebuilt = expand_knots(knots)
        if any(not envelope['curve_step_min'] <= b - a <= envelope['curve_step_max']
               for a, b in zip(rebuilt, rebuilt[1:])):
            raise FilmRequestError('胶片重建曲线步差超出原厂数值边界')
        if not (envelope['curve_first_min'] <= rebuilt[0] <= envelope['curve_first_max']
                and envelope['curve_last_min'] <= rebuilt[-1] <= envelope['curve_last_max']):
            raise FilmRequestError('胶片重建曲线起点或终点超出原厂数值边界：起点 %d（原厂 %d～%d），终点 %d（原厂 %d～%d）' % (
                rebuilt[0], envelope['curve_first_min'], envelope['curve_first_max'],
                rebuilt[-1], envelope['curve_last_min'], envelope['curve_last_max']))
        packed.append(knots)
        errors.append(error)
    return tuple(packed), tuple(errors)

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

