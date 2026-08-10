"""Source rebakes of the delivered construct ``fd47ea6``, for the high-n re-screen.

Every variant is produced by an *exact-anchor* textual substitution on
``git show fd47ea6:src/player.cpp``.  The anchors are asserted unique, so a variant
either applies verbatim or raises -- there is no fuzzy patching and no possibility of a
silently-partial rebake.  ``src/player.cpp`` is never written; the baseline text is read
out of git and every artifact lands in the caller's ``/tmp`` workdir.

Why a separate module: ``sim/analyze_hotfield_table.py`` owns the ``colv`` rebake against
the *older* ``f18064c`` pin and is a committed driver this line does not own.  The
``_colv_rule("edge")`` body below is a verbatim copy of that module's rule so the two
lines measure the same intervention, but the anchor and the pin are this line's.

Vocabulary
----------
``base``        unmodified ``fd47ea6``.
``null``        the same text, built to a different filename.  The zero-signal control:
                its margin against ``base`` must be exactly 0 on every seed.
``nofold``      the registered ablation of the stand-on-gold organ: drop BOTH the
                ``standing`` target fallback and the ``d == 0`` two-step fold.
``nofoldpure``  drop only the ``d == 0`` fold; the ``standing`` target is retained, so a
                unit standing on residue holds position instead of oscillating.
``colvedge``    ``hot_colv_edge``: never take a target in a column outside the central
                9x9 column band, fires at ``sc <= 5`` or ``sc >= 11``.
``safet2``      the safe two-tier T2: a second ``== 2`` row mask, strictly below the
                >= 3 tier and below standing residue.
``cursor``      arm C in its cursor form: widen ``SLut`` to 6, and let the sighted unit
                own the whole 6-slot buffer when exactly one unit is blind, using the
                shared cursor rather than a post-loop rewrite of ``out.actions``.
``cursorv``     ``cursor`` plus validation of the three extra waypoints against ``blk``.
``cursor4``     ``cursor`` capped at a 4-step producer budget (k in {2, 3, 4}).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "fd47ea6"
BASELINE_SHA256 = "df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217"

# ---------------------------------------------------------------------------
# anchors, all asserted unique against the baseline text
# ---------------------------------------------------------------------------

A_TARGET3 = """            int has = -(int)(bv != 0xFFFF);
            int standing = -(int)(in->grid[sr][sc] > 1);   // 1金残渣不折返: 回哨位张网
            int selfm = ~has & standing;
            blind = ~has & ~standing;
            tgr = ((sr - 2 + TT.d5[w]) & has) | (sr & selfm) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & has) | (sc & selfm) | (g_s.anch_c[u] & blind);"""

A_TARGET3_NOSTAND = """            int has = -(int)(bv != 0xFFFF);
            blind = ~has;
            tgr = ((sr - 2 + TT.d5[w]) & has) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & has) | (g_s.anch_c[u] & blind);"""

A_FOLD = """        int d = (dr0 < 0 ? -dr0 : dr0) + (dc0 < 0 ? -dc0 : dc0);
        if (d == 0) {                            // 站金: 折返双吃
            unsigned pm = (~(blk[sr] >> (sc + 1)) & 1u) |
                          ((~(blk[sr + 2] >> (sc + 1)) & 1u) << 1) |
                          ((~(blk[sr + 1] >> (sc)) & 1u) << 2) |
                          ((~(blk[sr + 1] >> (sc + 2)) & 1u) << 3);
            if (pm) {
                int a = __builtin_ctz(pm);
                acts[0] = a; acts[1] = a ^ 1;
            }
        } else {"""

A_FOLD_OFF = """        {                                        // 折返消融: d==0 由 SL[3][3] 保持 STAY"""

A_COLV = "            colv[sc] = (uint8_t)(((31u >> hix) & (31u << lo)) & 31u);"

A_ROWSEL_DECL = "        uint16_t rowsel[5];"
A_V2S = ("            const __m256i v2s = _mm256_set1_epi32(2);"
         "   // 挑食: 只标 ≥3 整格(≥2 版判负: 1184视2388)")
A_AVX512_CMP = "                uint32_t b8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, vm3);"
A_AVX2_CMP = """                uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm3)));"""
A_ROWSEL_STORE = "                rowsel[i] = TT.bestrow[i][(((g8 << 2) >> lsh) & 31u) & rv];"
A_ROWSEL_INIT = "        rowsel[0] = rowsel[1] = rowsel[2] = rowsel[3] = rowsel[4] = 0xFFFF;"
A_SCALAR_PICK = """                if (v > 2) {
                    uint16_t e = TT.bestrow[i][1u << j];
                    if (e < rowsel[i]) rowsel[i] = e;
                } else if (v == -3) g_s.bombbit[rr + 3] |= 1u << (cc + 1);"""
A_MINRED = """            uint16_t b01 = rowsel[0] < rowsel[1] ? rowsel[0] : rowsel[1];
            uint16_t b23 = rowsel[2] < rowsel[3] ? rowsel[2] : rowsel[3];
            uint16_t b0123 = b01 < b23 ? b01 : b23;
            uint16_t bv = b0123 < rowsel[4] ? b0123 : rowsel[4];
            int w = bv & 31;"""

# --- SLut, the 3-wide table and its early-arrival pre-fold -------------------
A_SLUT_DECL = """int8_t  pdr[7][7][3], pdc[7][7][3];"""
A_SLUT_FACT = "    uint8_t fact[7][7][3];"
A_SLUT_BODY = """                int r = 0, c = 0;
                for (int i = 0; i < 3; ++i) {
                    int rr = dr - r, cc = dc - c;
                    int adr = rr < 0 ? -rr : rr, adc = cc < 0 ? -cc : cc;
                    uint8_t a = STAY;
                    if (adr | adc) {
                        if (adr >= adc) { a = rr > 0 ? 1 : 0; r += rr > 0 ? 1 : -1; }
                        else            { a = cc > 0 ? 3 : 2; c += cc > 0 ? 1 : -1; }
                    }
                    fact[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                }
                {   // 早到折返预折叠(语义与旧运行时 fold 块逐位一致)
                    int d = (dr < 0 ? -dr : dr) + (dc < 0 ? -dc : dc);
                    if (d > 0 && d < 3) {
                        fact[dr + 3][dc + 3][d] =
                            (uint8_t)(fact[dr + 3][dc + 3][d - 1] ^ 1);
                        if (d == 1)
                            fact[dr + 3][dc + 3][2] =
                                (uint8_t)(fact[dr + 3][dc + 3][1] ^ 1);
                    }
                }"""

# --- the unit loop: head, route, tail --------------------------------------
A_LOOP_HEAD = """    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;"""

A_ROUTE_OK = """            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
            } else {"""

A_LOOP_TAIL = """        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
    }

    out.k = 3;"""

A_PAD = 'asm(".space 96, 0x90");'

# the four-way selection block ``safet2`` installs, and its stand-on-gold-free form
A_TARGET3_T2 = """            int has = -(int)(bv != 0xFFFF);
            int standing = -(int)(in->grid[sr][sc] > 1);   // 保留现役脚下残值优先级
            int selfm = ~has & standing;
            int lowm = ~has & ~standing & low;
            int targetm = has | lowm;
            blind = ~has & ~standing & ~low;
            int w = ((bv & 31) & has) | ((lv & 31) & lowm);
            tgr = ((sr - 2 + TT.d5[w]) & targetm) | (sr & selfm) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & targetm) | (sc & selfm) | (g_s.anch_c[u] & blind);"""

A_TARGET3_T2_NOSTAND = """            int has = -(int)(bv != 0xFFFF);
            int lowm = ~has & low;
            int targetm = has | lowm;
            blind = ~has & ~low;
            int w = ((bv & 31) & has) | ((lv & 31) & lowm);
            tgr = ((sr - 2 + TT.d5[w]) & targetm) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & targetm) | (g_s.anch_c[u] & blind);"""


def _colv_rule_edge() -> str:
    """Verbatim copy of ``analyze_hotfield_table._colv_rule('edge')``."""
    return ("            unsigned drop = 0;\n"
            "            if (sc <= 5)  drop |= 1u;\n"
            "            if (sc >= 11) drop |= 16u;\n")


# ---------------------------------------------------------------------------
# individual rebakes
# ---------------------------------------------------------------------------


def _sub(text: str, anchor: str, replacement: str, tag: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise AssertionError("anchor %r occurs %d times, expected 1" % (tag, count))
    return text.replace(anchor, replacement)


def patch_nofold(text: str) -> str:
    """Drop both halves of the stand-on-gold organ.

    Tolerant of ``safet2`` having already rewritten the selection block: in that case the
    ``standing`` tier is removed but the 2-gold tier is kept, so ``blind`` becomes
    ``~has & ~low`` rather than ``~has``.  That is the only composition of the two that
    preserves each one's stated mechanism.
    """
    if A_TARGET3 in text:
        text = _sub(text, A_TARGET3, A_TARGET3_NOSTAND, "target3")
    else:
        text = _sub(text, A_TARGET3_T2, A_TARGET3_T2_NOSTAND, "target3_t2_nostand")
    return _sub(text, A_FOLD, A_FOLD_OFF, "fold")


def patch_nofoldpure(text: str) -> str:
    return _sub(text, A_FOLD, A_FOLD_OFF, "fold")


def patch_colvedge(text: str) -> str:
    body = _colv_rule_edge()
    replacement = (
        body +
        "            colv[sc] = (uint8_t)((((31u >> hix) & (31u << lo)) & 31u) & ~drop);")
    return _sub(text, A_COLV, replacement, "colv")


def patch_safet2(text: str) -> str:
    """The safe two-tier T2, rebased from ``/tmp/player_safe_t2.cpp`` (sha256 0ac0d24b..).

    Priority becomes  >=3 whole cell  >  standing residue  >  reachable 2-gold  >  anchor,
    all four selected by mask, with the low tier reusing the delivered LUT and the same
    three-step ``blk`` check.
    """
    text = _sub(text, A_ROWSEL_DECL, "        uint16_t rowsel[5], rowsel2[5];", "rowsel_decl")
    text = _sub(text, A_AVX512_CMP,
                A_AVX512_CMP + "\n"
                "                uint32_t e8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, v2s);",
                "avx512_cmp")
    text = _sub(text, A_AVX2_CMP,
                A_AVX2_CMP + "\n"
                "                uint32_t e8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(\n"
                "                    _mm256_cmpeq_epi32(vrow, v2s)));",
                "avx2_cmp")
    text = _sub(text, A_ROWSEL_STORE,
                A_ROWSEL_STORE + "\n"
                "                rowsel2[i] = TT.bestrow[i][(((e8 << 2) >> lsh) & 31u) & rv];",
                "rowsel_store")
    text = _sub(text, A_ROWSEL_INIT,
                A_ROWSEL_INIT + "\n"
                "        rowsel2[0] = rowsel2[1] = rowsel2[2] = rowsel2[3] = rowsel2[4] = 0xFFFF;",
                "rowsel_init")
    text = _sub(text, A_SCALAR_PICK,
                """                if (v > 2) {
                    uint16_t e = TT.bestrow[i][1u << j];
                    if (e < rowsel[i]) rowsel[i] = e;
                } else if (v == 2) {
                    uint16_t e = TT.bestrow[i][1u << j];
                    if (e < rowsel2[i]) rowsel2[i] = e;
                } else if (v == -3) g_s.bombbit[rr + 3] |= 1u << (cc + 1);""",
                "scalar_pick")
    text = _sub(text, A_MINRED,
                """            uint16_t b01 = rowsel[0] < rowsel[1] ? rowsel[0] : rowsel[1];
            uint16_t b23 = rowsel[2] < rowsel[3] ? rowsel[2] : rowsel[3];
            uint16_t b0123 = b01 < b23 ? b01 : b23;
            uint16_t bv = b0123 < rowsel[4] ? b0123 : rowsel[4];
            uint16_t l01 = rowsel2[0] < rowsel2[1] ? rowsel2[0] : rowsel2[1];
            uint16_t l23 = rowsel2[2] < rowsel2[3] ? rowsel2[2] : rowsel2[3];
            uint16_t l0123 = l01 < l23 ? l01 : l23;
            uint16_t lv = l0123 < rowsel2[4] ? l0123 : rowsel2[4];
            int low = -(int)(lv != 0xFFFF);""",
                "minred")
    # the four-way selection replaces the three-way one; keep ``standing`` above the low tier
    text = _sub(text, A_TARGET3,
                """            int has = -(int)(bv != 0xFFFF);
            int standing = -(int)(in->grid[sr][sc] > 1);   // 保留现役脚下残值优先级
            int selfm = ~has & standing;
            int lowm = ~has & ~standing & low;
            int targetm = has | lowm;
            blind = ~has & ~standing & ~low;
            int w = ((bv & 31) & has) | ((lv & 31) & lowm);
            tgr = ((sr - 2 + TT.d5[w]) & targetm) | (sr & selfm) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & targetm) | (sc & selfm) | (g_s.anch_c[u] & blind);""",
                "target3_t2")
    return text


# --- the cursor form -------------------------------------------------------

_SLUT_WIDE_BODY = """                int r = 0, c = 0;
                for (int i = 0; i < SLW; ++i) {
                    int rr = dr - r, cc = dc - c;
                    int adr = rr < 0 ? -rr : rr, adc = cc < 0 ? -cc : cc;
                    uint8_t a = STAY;
                    if (adr | adc) {
                        if (adr >= adc) { a = rr > 0 ? 1 : 0; r += rr > 0 ? 1 : -1; }
                        else            { a = cc > 0 ? 3 : 2; c += cc > 0 ? 1 : -1; }
                    }
                    fact[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                }
                {   // 早到折返预折叠, 推广到 SLW 宽(SLW==3 时与交付表逐位一致)
                    int d = (dr < 0 ? -dr : dr) + (dc < 0 ? -dc : dc);
                    if (d > 0 && d < SLW)
                        for (int t = d; t < SLW; ++t)
                            fact[dr + 3][dc + 3][t] =
                                (uint8_t)(fact[dr + 3][dc + 3][t - 1] ^ 1);
                }"""


def patch_cursor(text: str, *, budget: int = 6, validate: bool = False) -> str:
    """Arm C, cursor form.

    The engine only requires unit 0 to occupy ``actions[:k]`` and unit 1 the suffix
    ``actions[k:]`` (``src/game_api.h:58-59``), so ``k`` can be used as a *cursor* over one
    shared six-slot buffer instead of the constant 3 it is today.  ``k`` takes three values:

    ==============================  ====  ================================================
    condition                       k     what is written
    ==============================  ====  ================================================
    not exactly one unit blind      3     nothing at all -- the delivered 3 + 3 layout
    unit 1 blind, unit 0 producer   6     three tail stores into slots 3..5
    unit 0 blind, unit 1 producer   0     unit 1's head shifted 3..5 -> 0..2, then the tail
    ==============================  ====  ================================================

    Two properties matter and both are load-bearing.

    * **The producer-is-unit-0 direction needs zero moves**, because unit 0's head already
      sits at slot 0 where a 6-slot span has to begin.  That is the half of the measured
      shapes' six-int rewrite that the cursor deletes outright.
    * **The delivered store indices stay constant.**  An earlier draft gave unit 1 a
      *variable* write base (0 when unit 0 was blind, else 3), which is the literal reading
      of "unit 0 writes from slot 0 and advances".  That draft is **incorrect**: when both
      units are blind there is no reallocation, yet unit 1 has already overwritten unit 0's
      slots, and no value of ``k`` can put unit 0's actions anywhere but the prefix.
      Repairing it costs a six-store swap on the both-blind rounds, which are the *majority*
      of blind rounds (324 of 550 on the instrumented game), so it is strictly worse.  The
      constant-index form below also sidesteps the pre-registered risk that a variable store
      index pushes ``acts`` out of registers and inhibits vectorisation of the store block.

    The tail is only real when the delivered triple *is* the LUT plan: the ``d == 0``
    standing fold and the blocked steer fallback both leave ``pext`` null, and a null tail
    resolves to ``SL.fact[3][3]``, whose entries are all STAY by construction.  That
    reproduces the simulator arm's ``head_ok`` gate with no extra branch.
    """
    if budget not in (4, 5, 6):
        raise ValueError(budget)
    extra = budget - 3
    text = _sub(text, A_SLUT_FACT,
                "    static constexpr int SLW = %d;\n"
                "    uint8_t fact[7][7][SLW];" % budget, "slut_fact")
    text = _sub(text, A_SLUT_DECL, "int8_t  pdr[7][7][SLW], pdc[7][7][SLW];", "slut_decl")
    text = _sub(text, A_SLUT_BODY, _SLUT_WIDE_BODY, "slut_body")
    text = _sub(text, A_LOOP_HEAD,
                """    int cur = 3;                                 // k 当游标: 3 = 交付布局, 0/6 = 已重分配
    int blind0 = 0;                              // u0 是否盲(u1 的尾块要读)
    const uint8_t* ext0 = nullptr;               // u0 的 LUT 计划; 非空才可续写尾段
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        const uint8_t* pext = nullptr;""", "loop_head")
    if validate:
        route = ("""            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
                unsigned ok2 = 1u;               // 续写前额外校验 3 个航点
                for (int t = 3; t < %d; ++t)
                    ok2 &= (~(blk[sr + xr[t] + 1] >> (sc + xc[t] + 1)) & 1u);
                if (ok2) pext = pa;
            } else {""" % budget)
    else:
        route = """            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
                pext = pa;                       // 可续写: 尾段与本段同表同目标
            } else {"""
    text = _sub(text, A_ROUTE_OK, route, "route_ok")
    if budget == 6:
        realloc = """            const uint8_t* pe = blind0 ? pext : ext0;
            if (!pe) pe = SL.fact[3][3];          // 非 LUT 路径: 六格恒 STAY 的表项
            if (blind0) {                         // u1 是生产者: 头段下移到跨度起点
                out.actions[0] = out.actions[3];
                out.actions[1] = out.actions[4];
                out.actions[2] = out.actions[5];
                cur = 0;
            } else cur = 6;                       // u0 是生产者: 头段已在槽 0, 零搬移
            out.actions[3] = pe[3];
            out.actions[4] = pe[4];
            out.actions[5] = pe[5];"""
    else:
        # producer span is [0, b) for unit 0 and [3 - extra, 6) for unit 1, so unit 1's head
        # must slide DOWN by ``extra`` slots, not to slot 0.  Forward iteration is safe
        # because every destination index is below its source.
        shift = "\n".join(
            "                out.actions[%d] = out.actions[%d];" % (3 - extra + i, 3 + i)
            for i in range(3))
        prod0 = "\n".join(
            "                out.actions[%d] = pe[%d];" % (3 + t, 3 + t) for t in range(extra))
        prod0 += "\n" + "\n".join(
            "                out.actions[%d] = STAY;" % s for s in range(budget, 6))
        prod1 = "\n".join(
            "                out.actions[%d] = pe[%d];" % (6 - extra + t, 3 + t)
            for t in range(extra))
        prod1 += "\n" + "\n".join(
            "                out.actions[%d] = STAY;" % s for s in range(3 - extra))
        realloc = """            const uint8_t* pe = blind0 ? pext : ext0;
            if (!pe) pe = SL.fact[3][3];
            if (blind0) {                         // u1 是生产者, 跨度 [3-extra, 6)
%s
%s
                cur = %d;
            } else {                              // u0 是生产者, 跨度 [0, b)
%s
                cur = %d;
            }""" % (shift, prod1, 3 - extra, prod0, budget)
    tail = ("""        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
        int bd = blind != 0;
        if (u == 0) {
            blind0 = bd; ext0 = pext;
        } else if (bd != blind0) {                // 恰好一个盲 ⇒ 把预算给能用的那个
%s
        }
    }

    out.k = cur;""" % realloc)
    return _sub(text, A_LOOP_TAIL, tail, "loop_tail")


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

VARIANTS: Mapping[str, Mapping[str, object]] = {
    "base": {
        "patches": (),
        "role": "baseline",
        "claim": "unmodified fd47ea6; every margin below is measured against this .so on the "
                 "identical seed, order arm and field model",
    },
    "null": {
        "patches": (),
        "role": "zero-signal control",
        "claim": "byte-identical source rebuilt to a different filename; margin must be exactly "
                 "0 on every paired seed, which is what licenses the SE claimed elsewhere",
    },
    "nofold": {
        "patches": ("nofold",),
        "role": "candidate",
        "claim": "remove the stand-on-gold organ as registered in subsystem_value_audit.md: both "
                 "the standing target fallback and the d==0 two-step fold",
    },
    "nofoldpure": {
        "patches": ("nofoldpure",),
        "role": "candidate",
        "claim": "remove only the d==0 two-step fold; a unit standing on residue keeps the cell "
                 "as its target and therefore holds position",
    },
    "colvedge": {
        "patches": ("colvedge",),
        "role": "candidate",
        "claim": "hot_colv_edge: drop the outward window column from the absolute colv mask when "
                 "the unit is at sc<=5 or sc>=11, so no target is taken outside column band 4..12",
    },
    "safet2": {
        "patches": ("safet2",),
        "role": "candidate",
        "claim": "safe two-tier T2: a second ==2 row mask strictly below >=3 and below standing "
                 "residue, reusing the delivered LUT and the full three-step blk check",
    },
    "cursor": {
        "patches": ("cursor6",),
        "role": "candidate",
        "claim": "arm C cursor form, producer budget 6, no tail validation: the cheapest shape "
                 "that expresses the reallocation, with the six-int rewrite deleted",
    },
    "cursorv": {
        "patches": ("cursor6v",),
        "role": "cost control",
        "claim": "as cursor, plus validating the three extra waypoints against blk; prices the "
                 "validation that clut_gates measured as rejecting 0 of 150 firings",
    },
    "cursor4": {
        "patches": ("cursor4",),
        "role": "candidate",
        "claim": "arm C cursor form capped at a 4-step producer budget, the repo's registered "
                 "architectural cap on k",
    },
}

_PATCHERS = {
    "nofold": patch_nofold,
    "nofoldpure": patch_nofoldpure,
    "colvedge": patch_colvedge,
    "safet2": patch_safet2,
    "cursor6": lambda t: patch_cursor(t, budget=6, validate=False),
    "cursor6v": lambda t: patch_cursor(t, budget=6, validate=True),
    "cursor4": lambda t: patch_cursor(t, budget=4, validate=False),
}

# order matters: nofold/safet2 both rewrite the target-selection block, so safet2 must be
# applied first (it consumes the three-way anchor) and nofold's fold removal after.
PATCH_ORDER = ("safet2", "nofold", "nofoldpure", "colvedge", "cursor6", "cursor6v", "cursor4")


def compose(name: str, patches: Sequence[str]) -> Mapping[str, object]:
    return {"patches": tuple(patches), "role": "stack", "claim": name}


def apply_patches(text: str, patches: Sequence[str]) -> str:
    ordered = [p for p in PATCH_ORDER if p in patches]
    unknown = set(patches) - set(ordered)
    if unknown:
        raise KeyError(sorted(unknown))
    for patch in ordered:
        text = _PATCHERS[patch](text)
    return text


def baseline_text(workdir: Path) -> Path:
    """Extract ``fd47ea6:src/player.cpp`` and verify its sha256 before anything is built."""
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "base_fd47ea6.cpp"
    if not out.exists():
        proc = subprocess.run(["git", "-C", str(ROOT), "show",
                               "%s:src/player.cpp" % BASELINE_COMMIT],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        out.write_text(proc.stdout)
    got = hashlib.sha256(out.read_bytes()).hexdigest()
    if got != BASELINE_SHA256:
        raise SystemExit("baseline sha256 %s != expected %s" % (got, BASELINE_SHA256))
    header = ROOT / "src" / "game_api.h"
    if header.exists():
        (workdir / "game_api.h").write_bytes(header.read_bytes())
    return out


BUILD_FLAGS = ["-std=c++17", "-O3", "-march=native", "-fPIC", "-Wall", "-Wextra", "-shared"]
FP16_RE = re.compile(
    r"\b(vmovw|vmovsh|vcvtph|vcvtsh|vadd[sp]h|vsub[sp]h|vmul[sp]h|vdiv[sp]h|"
    r"vfmadd[0-9]*[sp]h|vcmp[sp]h|vmax[sp]h|vmin[sp]h|vsqrt[sp]h|vrcpph|vrsqrtph)\b")


def build(name: str, patches: Sequence[str], base_src: Path, workdir: Path,
          *, compiler: str = "g++", extra_flags: Sequence[str] = ()) -> tuple[Path, Path, str]:
    """Emit ``<name>.cpp`` and ``<name>.so`` in ``workdir``; return (src, so, warnings)."""
    workdir.mkdir(parents=True, exist_ok=True)
    text = apply_patches(base_src.read_text(), patches)
    src = workdir / ("%s.cpp" % name)
    src.write_text(text)
    so = workdir / ("%s.so" % name)
    cmd = [compiler, *BUILD_FLAGS, *extra_flags, "-o", str(so), str(src),
           "-I", str(base_src.parent)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("build %s failed:\n%s\n%s" % (name, proc.stdout, proc.stderr))
    return src, so, proc.stderr
