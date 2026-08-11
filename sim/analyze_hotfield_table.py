#!/usr/bin/env python3
"""Hot-field table-value rebake of ``f18064c``: enumeration, freeness proofs, income.

Why this is not the seventh transplant
======================================

Six "hot-field gait" transplants into the champion died on 8.9.  ``src/CHANGELOG.md:375-378``
prices that death: the hot-track transplant added ~30 instructions but measured +80-110 ns on
the platform.  At 0.1454 ns/instruction, 30 instructions is ~4.4 ns, so >95% of the cost was
**position tax** -- changing ``decide``'s size moved ``moveDecision``'s entry alignment, and
mod64 buckets ``0x20``/``0x30`` are each measured at +11.67 ns.  The tax crushed the first-mover
rate to 2-4% and income followed.

A **pure table-value rebake** changes no instruction and no byte of ``.text``, so the entry
alignment cannot move and the position tax is structurally zero.  The CHANGELOG names this the
highest-priority knife shape and records that **none of the six was pure table-value**.

This driver therefore does three things, in this order, and refuses to reorder them:

1. ``tables``    -- enumerate every constexpr value table in ``f18064c`` that participates in
                    movement, and *fix the attribution* (blind-round / visible-selection / both)
                    before anything is built or run.  The attribution decides which ceiling
                    applies: ``sim/reports/target_selection_closed.md`` caps same-round selector
                    miss-recovery at 26.5%/35.9% of misses, and that cap does not bind a
                    blind-round or a next-round-positioning mechanism.
2. ``freeness``  -- build every arm and *measure* the three properties that are this
                    candidate's entire claim to distinction: ``.text`` byte-identical,
                    ``moveDecision`` entry mod64 unchanged, dynamic instructions/call unchanged.
                    If any fails, the candidate has become a transplant and inherits the six
                    deaths.
3. ``ab``        -- same-seed paired A/B under ``--dispatch fixed`` (action order fixed by
                    construction, so the first-mover-rate channel is eliminated and what is left
                    is the pure income effect), both order arms separately, judged on
                    ``margin_delta`` and on the **change in the opponent's scoring-round
                    frequency**, with out-of-sample confirmation on disjoint seeds.

The coordinate problem, resolved rather than assumed
====================================================

The measured field is indexed by **L1 (Manhattan) ring from the board centre (8,8)** -- absolute.
``snakeh.cpp``'s ``ORB4`` computes exactly ``|r-8| + |c-8|`` and indexes ``HB[ring]`` with it, and
the ring range 7-12 quoted in ``CHANGELOG:337`` only exists under L1 (Chebyshev caps at 8).

The champion's target priority is indexed by **L1 distance from the unit** -- relative.  It is
*not* Chebyshev: the ``rm`` order is exactly the four L1=1 cells, then all eight L1=2, then all
eight L1=3, then the four L1=4 corners, with the unit's own cell ranked **last**; under Chebyshev
the class sizes would be 4/20 rather than 4/8/8/4.  ``mode tables`` prints the derivation.

Both quantities are L1, but about **different origins**, and that is the whole design problem:

    A table indexed relative to the unit cannot carry a ring-from-centre gradient, because the
    inward direction flips sign with the unit's position.  Our two anchors sit on opposite sides
    of the centre row -- (6,8) is above, (11,8) is below -- so one shared relative table cannot
    be inward for both.  The only ring content a relative ordering *can* carry is its L1
    magnitude, and the champion already prefers the smallest L1 strictly, which is already the
    most centre-preserving relative ordering that exists.

So the rebake target is the one **absolutely-indexed** pure value table on the movement path:
``SCT.colv[17]``, looked up by the unit's own absolute column ``sc``, whose five bits gate which
window columns may supply a target at all.  Re-baking it to clear the *outward* column implements
"do not chase gold into the colder column" with zero instructions.  ``rm`` arms are carried too,
as the empirical test of the structural claim above.

Modes
=====

``tables``    static enumeration + attribution of every constexpr table (no build, no run)
``field``     the simulator's generation field by ring vs the measured field -- the
              identification precondition, because a hot-field knife measured in a flat field
              measures nothing
``probe``     baseline runs: where the units actually stand, how often each candidate rule
              fires, and what it would substitute
``freeness``  build all arms; ``.text`` sha/size, entry mod64, static and dynamic instructions
``ab``        the paired A/B with margin and the opponent scoring-round column
``assemble``  merge every stage into the JSON companion
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as futures
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GRID = 17
STAY = 4
CENTRE = (8, 8)
ROUNDS = 500
STEADY_FROM = 8

# Engine rule (sim/engine.py:_dispatch): faster = P1 iff cost1 <= cost2, and the faster player is
# a single actor entry, so both of its units finish before any NPC moves.  Our strategy always
# sits at seat 1, so the cost pair alone selects the order arm and no clock is ever read.
COSTS_WE_FIRST = (200, 201)
COSTS_WE_SECOND = (201, 200)

# --- the measured field ------------------------------------------------------------------
# src/CHANGELOG.md:339-341, 13 games normalised by visible rounds, per cell-round generation
# rate by L1 ring from (8,8).  Ring 1-2 is quoted as a band 0.032-0.035; midpoint used.
MEASURED_L1_RATE = {0: 0.0335, 1: 0.0335, 2: 0.0335, 3: 0.025, 4: 0.019, 5: 0.010, 6: 0.004}
# snakeh.cpp ORB4 gradient that delivered 503->1318 on the snake skeleton (indexed by L1 ring).
SNAKE_HB = (2, 3, 3, 2, 1, 0, -1, -2, -2, -2, -2, -2, -2, -2, -2, -2, -2, -2, -2)

# sim/GENERATION.md Sec 3.3: separable row / column per-cell landing frequency inside the
# central 9x9, offsets -4..+4 from row/col 8, map1, 2611 landings.  chi2/df = 1.52 for the
# separable fit.  Used only by the ``centripetal`` field arm; the repo default is uniform.
ROW_RATE = {-4: 22.3, -3: 38.0, -2: 41.9, -1: 46.0, 0: 65.2, 1: 47.4, 2: 43.9, 3: 36.9, 4: 25.6}
COL_RATE = {-4: 20.0, -3: 33.3, -2: 41.6, -1: 52.9, 0: 56.3, 1: 50.6, 2: 40.0, 3: 33.9, 4: 22.8}

# f18064c's own ring-priority reorder (src/player.cpp:121-122 at that commit).
RM_BASE = (7, 11, 13, 17, 2, 6, 8, 10, 14, 16, 18, 22,
           1, 3, 5, 9, 15, 19, 21, 23, 0, 4, 20, 24, 12, 12)


def _prio_from_rm(rm: Sequence[int]) -> list[int]:
    prio = [0] * 25
    for rank, widx in enumerate(rm):
        prio[widx] = rank
    return prio


PRIO_BASE = _prio_from_rm(RM_BASE)


def _l1(widx: int) -> int:
    return abs(widx // 5 - 2) + abs(widx % 5 - 2)


def _cheb_w(widx: int) -> int:
    return max(abs(widx // 5 - 2), abs(widx % 5 - 2))


def ring_l1(row: int, col: int) -> int:
    return abs(row - CENTRE[0]) + abs(col - CENTRE[1])


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summary(values: Sequence[float]) -> Mapping[str, Any]:
    """Mean, SE and sigma of a paired-difference sample."""
    if not values:
        return {"n": 0, "mean": None, "se": None, "sigma": None}
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"n": 1, "mean": mean, "se": None, "sigma": None}
    se = statistics.stdev(values) / math.sqrt(len(values))
    return {
        "n": len(values), "mean": mean, "se": se,
        "sigma": (mean / se) if se else None,
        "median": statistics.median(values),
        "wins": sum(1 for v in values if v > 0), "losses": sum(1 for v in values if v < 0),
    }


# ==========================================================================================
# 1.  the table enumeration and its attribution
# ==========================================================================================

# ``affects`` is the movement channel each table can change.  ``pure_value`` means the table can
# be re-baked without changing instruction count or ``.text`` size: it is read at run time with a
# run-time index, so it lives in ``.rodata`` and its contents never reach the instruction stream.
TABLE_ENUMERATION: tuple[Mapping[str, Any], ...] = (
    {
        "name": "rm[26] -> TT.bestrow[5][32]",
        "where": "src/player.cpp:121-135 (constructor), consumed :432 (AVX) and :455 (scalar)",
        "bytes": "rm is compile-time only; bestrow is 5*32*2 = 320 B of .rodata",
        "index_space": "relative: window row i (0..4) x 5-bit column mask",
        "pure_value": True,
        "affects": "visible-target selection only",
        "blind_round_effect": "none directly; can *create* blind rounds if every v>2 cell in the "
                              "window is dropped from the mask, which routes the unit to its anchor",
        "ring_content": "none beyond L1 magnitude -- the inward direction is not expressible in "
                        "unit-relative coordinates, and the champion already prefers minimum L1",
        "ceiling": "the 26.5%/35.9% same-round selector ceiling binds the miss-recovery part",
        "rebaked_here": "yes: arms rm_far, rm_rowflip (structural tests, not candidates)",
    },
    {
        "name": "TT.remap[26]",
        "where": "src/player.cpp:116,123 -- written by the constructor, never read",
        "bytes": "26 B of .rodata, emitted because &TT is address-taken by _mm_prefetch",
        "index_space": "n/a (dead)",
        "pure_value": True,
        "affects": "nothing -- the pext cascade it fed was retired (comment at :463)",
        "blind_round_effect": "none",
        "ring_content": "none",
        "ceiling": "n/a",
        "rebaked_here": "no: a rebake here is provably a no-op, and it is listed so that a future "
                        "reader does not mistake it for the live priority table",
    },
    {
        "name": "SLut SL.fact/pdr/pdc[7][7][3]",
        "where": "src/player.cpp:188-220, consumed :501-508",
        "bytes": "3 * 147 = 441 B of .rodata",
        "index_space": "relative: (dr,dc) clamped to [-3,3]^2",
        "pure_value": True,
        "affects": "both -- it routes to a visible gold cell and to the anchor on blind rounds",
        "blind_round_effect": "yes, whenever a blind unit is 1..3 cells from its anchor",
        "ring_content": "none -- same relative-origin obstruction as rm; additionally pdr/pdc must "
                        "stay consistent with fact or the pass01 pre-verification desynchronises",
        "ceiling": "n/a, but the family is spent: fold_tour was exactly a zero-instruction table "
                   "rebake here and is the worst arm on record, -81.4 +- 18.5 out of sample",
        "rebaked_here": "no",
    },
    {
        "name": "ORT_A[2][4][3] / ORT_R[2][4] / ORT_C[2][4]",
        "where": "src/player.cpp:90-95, consumed in slowMove :302-311",
        "bytes": "24 + 8 + 8 = 40 B of .rodata",
        "index_space": "absolute in time (round 0..3) and gated on exact position match",
        "pure_value": True,
        "affects": "blind-round movement, map1 only, rounds 0-3 only",
        "blind_round_effect": "yes but tiny: slowMove is called only when blind, and the per-round "
                              "position gate self-aborts the route the moment any gold is seen",
        "ring_content": "yes in principle (the route endpoint is an absolute cell)",
        "ceiling": "n/a",
        "rebaked_here": "no: the one geometrically available correction was measured on 8.10 and "
                        "judged negative in both directions (-5.67+-25.91 and -18.67+-10.15) with "
                        "7 of 12 seeds bit-identical, i.e. the change was not even reached "
                        "(CHANGELOG 8.10 'ORT_A stale constant')",
    },
    {
        "name": "SCT.colv[17]",
        "where": "src/player.cpp:98-111, consumed :429-433 as rv = colv & rowok",
        "bytes": "17 B of .rodata inside the 51-byte SctT",
        "index_space": "ABSOLUTE: indexed by the unit's own column sc",
        "pure_value": True,
        "affects": "both -- it gates which window columns may supply a target, and dropping every "
                   "gold column turns a visible round into a blind round (target := anchor)",
        "blind_round_effect": "yes, by construction: suppressing the only visible gold makes "
                              "blind = 1, which sends the unit back to its anchor on the peak",
        "ring_content": "YES -- the only absolutely-indexed movement table, so it is the only place "
                        "where 'outward is colder' can be written down for free.  Restricted to the "
                        "column axis and to prohibition (it is a mask, not a priority).",
        "ceiling": "not bound by the same-round selector ceiling: the claimed channel is next-round "
                   "generation exposure, not recovering this round's miss",
        "rebaked_here": "yes -- this is the candidate",
    },
    {
        "name": "SCT.cb[17] / SCT.lsh[17]",
        "where": "src/player.cpp:98-108, consumed :417-421 and :432-433",
        "bytes": "34 B of .rodata",
        "index_space": "ABSOLUTE: indexed by sc",
        "pure_value": True,
        "affects": "nothing safely -- cb is the 32-byte load base and lsh is its matched bit "
                   "realignment; any change to one without the other misaligns the whole window",
        "blind_round_effect": "n/a",
        "ring_content": "n/a",
        "ceiling": "n/a",
        "rebaked_here": "no: inert as a policy knob, a rebake is a correctness bug not a bias",
    },
    {
        "name": "TT.rclv[21]",
        "where": "src/player.cpp:119, consumed :419",
        "bytes": "21 B of .rodata",
        "index_space": "ABSOLUTE: indexed by scan row + 2",
        "pure_value": True,
        "affects": "nothing safely -- for in-range rows it must be the identity or the scan loads "
                   "the wrong row and manufactures phantom gold; out-of-range rows are already "
                   "masked out by rowok, so their entries are behaviourally inert",
        "blind_round_effect": "n/a",
        "ring_content": "n/a",
        "ceiling": "n/a",
        "rebaked_here": "no",
    },
    {
        "name": "TT.d5[25] / TT.m5[25]",
        "where": "src/player.cpp:120, consumed :477-478",
        "bytes": "50 B of .rodata",
        "index_space": "relative: window index",
        "pure_value": True,
        "affects": "nothing safely -- they are div/mod 5 used to turn the chosen window index back "
                   "into a cell; a rebake aims the unit at a cell it never inspected",
        "blind_round_effect": "n/a",
        "ring_content": "n/a",
        "ceiling": "n/a",
        "rebaked_here": "no",
    },
    {
        "name": "BAKED_W[3][17]",
        "where": "src/player.cpp:74-84, consumed :272 (fingerprint) and :283 (wall flood)",
        "bytes": "3 * 17 * 4 = 204 B of .rodata",
        "index_space": "ABSOLUTE: map id x row, bit c+1",
        "pure_value": True,
        "affects": "both, via the blocked bitmap",
        "blind_round_effect": "yes",
        "ring_content": "yes in principle -- phantom walls in the cold rings would be an absolute "
                        "hot-field mask",
        "ceiling": "n/a",
        "rebaked_here": "no, and this is a deliberate refusal: the same table is the fingerprint "
                        "discriminator, so phantom entries break map identification, and the 8.10 "
                        "mis-lock lesion prices exactly one wrong wall cell at -689 gold",
    },
    {
        "name": "ORB4[17][17] (snakeh.cpp)",
        "where": "NOT PRESENT in f18064c -- it is the snake skeleton's per-cell per-direction bias",
        "bytes": "17*17*4 = 1156 B in snakeh",
        "index_space": "ABSOLUTE: cell x direction",
        "pure_value": True,
        "affects": "the snake's per-step greedy score",
        "blind_round_effect": "n/a for the champion",
        "ring_content": "yes -- this is why the +815 knife was free on the snake: the absolute-"
                        "indexed bias table already existed and only its numbers changed",
        "ceiling": "n/a",
        "rebaked_here": "not applicable; recorded because the brief names it and because its "
                        "absence from the champion is the structural reason the champion has only "
                        "one absolutely-indexed movement table (colv) to rebake",
    },
)


def mode_tables(args: argparse.Namespace) -> Mapping[str, Any]:
    """Static enumeration + the priority-metric derivation.  No build and no run."""
    classes_l1: dict[int, list[int]] = collections.defaultdict(list)
    classes_cheb: dict[int, list[int]] = collections.defaultdict(list)
    order = sorted(range(25), key=lambda w: PRIO_BASE[w])
    for widx in order:
        classes_l1[_l1(widx)].append(widx)
        classes_cheb[_cheb_w(widx)].append(widx)
    ranked = [{"rank": PRIO_BASE[w], "widx": w,
               "dr": w // 5 - 2, "dc": w % 5 - 2,
               "l1": _l1(w), "cheb": _cheb_w(w)} for w in order]
    l1_sequence = [_l1(w) for w in order]
    cheb_sequence = [_cheb_w(w) for w in order]
    monotone_l1 = all(a <= b for a, b in zip(l1_sequence[:-1], l1_sequence[1:-1]))
    payload = {
        "baseline": "f18064c src/player.cpp, sha256 0ecce6fc...84fdd",
        "priority_metric": {
            "ranked": ranked,
            "l1_sequence_by_rank": l1_sequence,
            "cheb_sequence_by_rank": cheb_sequence,
            "l1_class_sizes": {str(k): len(v) for k, v in sorted(classes_l1.items())},
            "cheb_class_sizes": {str(k): len(v) for k, v in sorted(classes_cheb.items())},
            "metric": "L1 (Manhattan) distance from the unit, NOT Chebyshev",
            "evidence": "ranks 0-3 are exactly the four L1=1 cells, 4-11 the eight L1=2 cells, "
                        "12-19 the eight L1=3 cells, 20-23 the four L1=4 corners; under Chebyshev "
                        "the classes would be 4 then 20.  The unit's own cell is ranked last (25).",
            "l1_monotone_ignoring_own_cell": monotone_l1,
            "tie_break": "within an L1 class, ascending window index = row-major = up-rows first, "
                         "then left-to-right; this is an upward/leftward drift bias",
        },
        "measured_field": {
            "metric": "L1 ring from (8,8) -- absolute",
            "rate_per_cell_round": MEASURED_L1_RATE,
            "source": "src/CHANGELOG.md:339-341, 13 games normalised by visible rounds",
            "why_l1_not_cheb": "CHANGELOG:337 quotes rings 7-12 for the amount profile; Chebyshev "
                               "caps at 8 on a 17x17 board, L1 caps at 16.  snakeh.cpp's ORB4 "
                               "computes |r-8|+|c-8| and indexes HB with it, confirming L1.",
            "snake_gradient": list(SNAKE_HB),
        },
        "coordinate_mapping_decision": {
            "problem": "the field is L1-from-centre (absolute); every champion priority table is "
                       "L1-from-unit (relative).  Same metric, different origin.",
            "resolution": "a unit-relative table cannot carry the gradient, because the inward "
                          "direction flips sign with the unit's position and the two anchors "
                          "(6,8) and (11,8) sit on opposite sides of row 8.  The only ring content "
                          "a relative ordering carries is its L1 magnitude, and minimum-L1-first "
                          "is already the most centre-preserving relative ordering, so rm is "
                          "already at the hot-field optimum of its own family.",
            "consequence": "the rebake must go into the one absolutely-indexed movement table, "
                           "SCT.colv[17], indexed by the unit's own column sc.  Because colv is a "
                           "5-bit mask it can express prohibition but not bias, and because only "
                           "the column axis has such a table the gradient is column-only.",
            "cost_of_that_choice": "colv also gates bomb recording (line :433 reuses rv), so a "
                                   "dropped column is also un-remembered for bombs.  This is "
                                   "self-limiting -- the unit no longer targets that column, so it "
                                   "no longer routes through it -- but burn is reported per arm.",
        },
        "tables": list(TABLE_ENUMERATION),
    }
    return payload


# ==========================================================================================
# 2.  the arms:  source rebakes
# ==========================================================================================

COLV_ANCHOR = "            colv[sc] = (uint8_t)(((31u >> hix) & (31u << lo)) & 31u);"
RM_ANCHOR = """        constexpr uint8_t rm[26] = {
            7,11,13,17, 2,6,8,10,14,16,18,22, 1,3,5,9,15,19,21,23, 0,4,20,24, 12, 12};"""

# window column j maps to absolute column sc-2+j, so bit 0 is the leftmost and bit 4 the
# rightmost cell of the 5-wide window.
BIT_LEFT, BIT_RIGHT = 1, 16


def _colv_rule(kind: str) -> str:
    """C++ constexpr body that computes the bits this arm drops from colv[sc]."""
    if kind == "edge":
        # Never target a column outside the central 9x9 column band (4..12): the leftmost
        # window column is <= 3 exactly when sc <= 5, the rightmost is >= 13 when sc >= 11.
        return ("            unsigned drop = 0;\n"
                "            if (sc <= 5)  drop |= 1u;\n"
                "            if (sc >= 11) drop |= 16u;\n")
    if kind == "band2":
        return ("            unsigned drop = 0;\n"
                "            if (sc <= 6)  drop |= 1u;\n"
                "            if (sc >= 10) drop |= 16u;\n")
    if kind == "all":
        return ("            unsigned drop = 0;\n"
                "            if (sc < 8)  drop |= 1u;\n"
                "            if (sc > 8)  drop |= 16u;\n")
    if kind == "anti_edge":
        return ("            unsigned drop = 0;\n"
                "            if (sc <= 5)  drop |= 16u;\n"
                "            if (sc >= 11) drop |= 1u;\n")
    if kind == "anti_all":
        return ("            unsigned drop = 0;\n"
                "            if (sc < 8)  drop |= 16u;\n"
                "            if (sc > 8)  drop |= 1u;\n")
    raise KeyError(kind)


def colv_table(kind: str | None) -> list[int]:
    """Python mirror of the constexpr colv[] each arm bakes, used by ``probe``."""
    out = []
    for sc in range(17):
        lo = -(sc - 2) if sc - 2 < 0 else 0
        hix = sc + 2 - 16 if sc + 2 > 16 else 0
        base = ((31 >> hix) & (31 << lo)) & 31
        drop = 0
        if kind == "edge":
            if sc <= 5:
                drop |= BIT_LEFT
            if sc >= 11:
                drop |= BIT_RIGHT
        elif kind == "band2":
            if sc <= 6:
                drop |= BIT_LEFT
            if sc >= 10:
                drop |= BIT_RIGHT
        elif kind == "all":
            if sc < 8:
                drop |= BIT_LEFT
            if sc > 8:
                drop |= BIT_RIGHT
        elif kind == "anti_edge":
            if sc <= 5:
                drop |= BIT_RIGHT
            if sc >= 11:
                drop |= BIT_LEFT
        elif kind == "anti_all":
            if sc < 8:
                drop |= BIT_RIGHT
            if sc > 8:
                drop |= BIT_LEFT
        out.append(base & ~drop)
    return out


def rm_table(kind: str) -> tuple[int, ...]:
    """The rebaked ring-priority reorder for the structural rm arms."""
    by_class: dict[int, list[int]] = collections.defaultdict(list)
    for widx in range(25):
        if widx == 12:
            continue
        by_class[_l1(widx)].append(widx)
    if kind == "far":                       # prefer the FARTHEST gold: anti-hot-field in L1
        order: list[int] = []
        for dist in (4, 3, 2, 1):
            order.extend(sorted(by_class[dist]))
    elif kind == "rowflip":                 # same L1 classes, tie-break down-rows first
        order = []
        for dist in (1, 2, 3, 4):
            order.extend(sorted(by_class[dist], key=lambda w: (-(w // 5), w % 5)))
    else:
        raise KeyError(kind)
    order.extend([12, 12])
    assert len(order) == 26 and sorted(set(order)) == list(range(25)), order
    return tuple(order)


ARMS: Mapping[str, Mapping[str, Any]] = {
    "base": {"kind": None, "role": "baseline f18064c, unmodified",
             "claim": "reference"},
    "hot_colv_edge": {
        "kind": "colv", "rule": "edge", "role": "candidate",
        "claim": "never take a target in a column outside the central 9x9 band (col 4..12); "
                 "fires only at sc<=5 or sc>=11"},
    "hot_colv_band2": {
        "kind": "colv", "rule": "band2", "role": "candidate",
        "claim": "as edge, plus one ring earlier: fires at |sc-8|>=2"},
    "hot_colv_all": {
        "kind": "colv", "rule": "all", "role": "candidate",
        "claim": "never take a target in the outward |dc|=2 column whenever the unit is off the "
                 "centre column at all"},
    "anti_colv_edge": {
        "kind": "colv", "rule": "anti_edge", "role": "rate-matched sign control",
        "claim": "identical firing set to hot_colv_edge, identical one dropped bit, opposite side: "
                 "drops the INWARD column.  Separates 'the field gradient' from 'losing targets'."},
    "anti_colv_all": {
        "kind": "colv", "rule": "anti_all", "role": "rate-matched sign control",
        "claim": "identical firing set to hot_colv_all, opposite side"},
    "rm_far": {
        "kind": "rm", "rule": "far", "role": "structural test",
        "claim": "reverse the L1 ordering: prefer the farthest gold.  Tests that the L1-magnitude "
                 "channel exists and is signed the way the structural argument says."},
    "rm_rowflip": {
        "kind": "rm", "rule": "rowflip", "role": "structural test",
        "claim": "identical L1 classes, tie-break flipped to down-rows first.  Predicted null, "
                 "because a relative row preference is inward for one anchor and outward for the "
                 "other; a measured null is the empirical form of the coordinate argument."},
}


def rebake_source(base_text: str, arm: str) -> str:
    spec = ARMS[arm]
    kind = spec["kind"]
    if kind is None:
        return base_text
    if kind == "colv":
        assert base_text.count(COLV_ANCHOR) == 1, "colv anchor not unique"
        body = _colv_rule(spec["rule"])
        replacement = (
            body +
            "            colv[sc] = (uint8_t)((((31u >> hix) & (31u << lo)) & 31u) & ~drop);"
        )
        return base_text.replace(COLV_ANCHOR, replacement)
    if kind == "rm":
        assert base_text.count(RM_ANCHOR) == 1, "rm anchor not unique"
        table = rm_table(spec["rule"])
        rows = ",".join(str(v) for v in table)
        replacement = ("        constexpr uint8_t rm[26] = {\n"
                       "            " + rows + "};")
        return base_text.replace(RM_ANCHOR, replacement)
    raise KeyError(kind)


# ==========================================================================================
# 3.  freeness proofs
# ==========================================================================================

BUILD_FLAGS = ["-std=c++17", "-O3", "-march=native", "-fPIC", "-Wall", "-Wextra", "-shared"]


def _run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: %s\n%s\n%s" % (" ".join(cmd), proc.stdout, proc.stderr))
    return proc.stdout


def build_arm(arm: str, base_src: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    text = rebake_source(base_src.read_text(), arm)
    src = workdir / ("%s.cpp" % arm)
    src.write_text(text)
    so = workdir / ("%s.so" % arm)
    _run(["g++", *BUILD_FLAGS, "-o", str(so), str(src), "-I", str(base_src.parent)])
    return so


BASELINE_COMMIT = "f18064c"
BASELINE_SHA256 = "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd"


def resolve_base_src(path: str, workdir: Path) -> Path:
    """Return the baseline source, extracting it from git if it is not already on disk.

    The worktree ``src/player.cpp`` belongs to whoever is editing it and must never be built here,
    so the baseline is always taken from ``git show f18064c:src/player.cpp`` and its sha256 checked.
    """
    candidate = Path(path) if path else Path()
    if path and candidate.exists():
        got = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if got != BASELINE_SHA256:
            raise SystemExit("%s has sha256 %s, expected the f18064c baseline %s"
                             % (candidate, got, BASELINE_SHA256))
        return candidate
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "base_player.cpp"
    text = _run(["git", "-C", str(ROOT), "show", "%s:src/player.cpp" % BASELINE_COMMIT])
    out.write_text(text)
    got = hashlib.sha256(out.read_bytes()).hexdigest()
    if got != BASELINE_SHA256:
        raise SystemExit("extracted %s:src/player.cpp has sha256 %s, expected %s"
                         % (BASELINE_COMMIT, got, BASELINE_SHA256))
    header = ROOT / "src" / "game_api.h"
    (workdir / "game_api.h").write_bytes(header.read_bytes())
    return out


def _section(so: Path, name: str) -> Mapping[str, Any]:
    out = _run(["readelf", "-S", "-W", str(so)])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[1] == name:
            return {"addr": int(parts[3], 16), "offset": int(parts[4], 16),
                    "size": int(parts[5], 16)}
    raise KeyError(name)


def _section_sha(so: Path, name: str, tmp: Path) -> str:
    out = tmp / ("%s%s.bin" % (so.stem, name.replace(".", "_")))
    _run(["objcopy", "-O", "binary", "--only-section=%s" % name, str(so), str(out)])
    return hashlib.sha256(out.read_bytes()).hexdigest()


def _entry_mod64(so: Path) -> Mapping[str, Any]:
    out = _run(["nm", "-D", "--defined-only", str(so)])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "moveDecision":
            addr = int(parts[0], 16)
            return {"addr": addr, "addr_hex": "0x%x" % addr, "mod64": addr % 64,
                    "mod64_hex": "0x%02x" % (addr % 64)}
    raise KeyError("moveDecision")


def _static_icount(so: Path) -> int:
    out = _run(["objdump", "-d", "--disassemble=moveDecision", str(so)])
    return sum(1 for line in out.splitlines() if re.match(r"^\s+[0-9a-f]+:\t", line))


def mode_freeness(args: argparse.Namespace) -> Mapping[str, Any]:
    workdir = Path(args.workdir)
    base_src = resolve_base_src(args.base_src, workdir)
    tmp = workdir / "sect"
    tmp.mkdir(parents=True, exist_ok=True)
    built: dict[str, Path] = {}
    for arm in ARMS:
        built[arm] = build_arm(arm, base_src, workdir)
    base_text = _section(built["base"], ".text")
    base_rodata = _section(built["base"], ".rodata")
    base_text_sha = _section_sha(built["base"], ".text", tmp)
    base_rodata_sha = _section_sha(built["base"], ".rodata", tmp)
    base_entry = _entry_mod64(built["base"])
    base_static = _static_icount(built["base"])

    rows: dict[str, Any] = {}
    for arm, so in built.items():
        text = _section(so, ".text")
        rodata = _section(so, ".rodata")
        text_sha = _section_sha(so, ".text", tmp)
        rodata_sha = _section_sha(so, ".rodata", tmp)
        entry = _entry_mod64(so)
        static = _static_icount(so)
        rows[arm] = {
            "so": str(so),
            "so_sha256": hashlib.sha256(so.read_bytes()).hexdigest(),
            "text_size": text["size"], "text_size_delta": text["size"] - base_text["size"],
            "text_sha256": text_sha, "text_identical_to_base": text_sha == base_text_sha,
            "rodata_size": rodata["size"],
            "rodata_size_delta": rodata["size"] - base_rodata["size"],
            "rodata_sha256": rodata_sha,
            "rodata_identical_to_base": rodata_sha == base_rodata_sha,
            "entry": entry,
            "entry_mod64_equals_base": entry["mod64"] == base_entry["mod64"],
            "static_instructions_moveDecision": static,
            "static_instruction_delta": static - base_static,
        }
    return {
        "build_flags": BUILD_FLAGS,
        "host": _run(["uname", "-srm"]).strip(),
        "compiler": _run(["g++", "--version"]).splitlines()[0],
        "base_src": str(base_src),
        "base_src_sha256": hashlib.sha256(base_src.read_bytes()).hexdigest(),
        "base_entry": base_entry,
        "base_text_size": base_text["size"],
        "base_rodata_size": base_rodata["size"],
        "arms": rows,
        "note": "the champion at f18064c carries no asm('.space') pad; its entry lands in the "
                "0x10 bucket unaided, so the gate is 'candidate mod64 == base mod64 == 0x10'.",
    }


def mode_icount(args: argparse.Namespace) -> Mapping[str, Any]:
    """Dynamic instructions and cycles per call on a fixed, shared input stream."""
    workdir = Path(args.workdir)
    inputs = Path(args.inputs)
    tool = workdir / "icount"
    if not tool.exists():
        _run(["g++", "-std=c++17", "-O2", "-o", str(tool),
              str(ROOT / "tests" / "icount.cpp"), "-ldl"])
    rows: dict[str, Any] = {}
    for counter in ("instructions", "cycles"):
        for arm in ARMS:
            so = workdir / ("%s.so" % arm)
            if not so.exists():
                continue
            out = _run([str(tool), str(so), str(inputs), str(args.calls), str(args.reps), counter])
            per_call = [float(m) for m in re.findall(r"raw_per_call=([0-9.]+)", out)]
            rows.setdefault(arm, {})[counter] = {
                "raw_per_call_reps": per_call,
                "raw_per_call_min": min(per_call) if per_call else None,
                "raw_per_call_mean": _mean(per_call),
            }
    base_i = rows.get("base", {}).get("instructions", {}).get("raw_per_call_min")
    base_c = rows.get("base", {}).get("cycles", {}).get("raw_per_call_min")
    for arm, cell in rows.items():
        if base_i is not None and "instructions" in cell:
            cell["instructions"]["delta_vs_base"] = cell["instructions"]["raw_per_call_min"] - base_i
        if base_c is not None and "cycles" in cell:
            cell["cycles"]["delta_vs_base"] = cell["cycles"]["raw_per_call_min"] - base_c
    return {
        "inputs": str(inputs), "calls": args.calls, "reps": args.reps,
        "protocol": "every .so is replayed against the SAME recorded input stream, so the "
                    "difference isolates code-path cost.  A behaviour knife can still shift the "
                    "dynamic count through path mix (fold vs LUT have different lengths); that is "
                    "a behaviour cost, not a position tax, and .text being byte-identical is what "
                    "rules the position tax out.",
        "arms": rows,
    }


# ==========================================================================================
# 4.  field models
# ==========================================================================================

def _centripetal_weight(row: int, col: int) -> float:
    dr, dc = row - CENTRE[0], col - CENTRE[1]
    if abs(dr) > 4 or abs(dc) > 4:
        return 1.0
    return ROW_RATE[dr] * COL_RATE[dc] / (ROW_RATE[0] * COL_RATE[0])


def install_field(model: str) -> None:
    """Monkeypatch the scenario generator *in this process only*.

    ``sim/scenario.py`` is shared with a concurrently running sibling worker and is never
    written.  ``uniform`` is the repo default and restores the stock class, which matters
    because a pool worker is reused across tasks with different field models.

    STALE AS OF 2026-08-11.  This experiment predates the central-law fix.  Two things
    broke it: the repo default central law is no longer spatially uniform (it is the
    separable centripetal law over the whole 9x9 with wall rejection), and
    ``_make_central`` no longer routes through ``_uniform_order``, so the override below
    is never called.  Both arms therefore now run the same law and this comparison is a
    null.  Its finding -- that a uniform central field biases the simulator against a
    hot-field knife -- is superseded by ``GENERATION.md`` 3.4; do not re-quote its
    numbers without re-running against the current generator.
    """
    import sim.runner as runner
    from sim.scenario import ScenarioGenerator, region_id

    if model == "uniform":
        runner.ScenarioGenerator = ScenarioGenerator
        return
    if model != "centripetal":
        raise KeyError(model)

    class CentripetalScenario(ScenarioGenerator):
        """Central 9x9 landings weighted by the measured separable row x column marginals.

        Implemented with the same exponential-race weighted-sampling-without-replacement trick
        the module already uses for outer hotspots, so the ordering semantics of
        ``resolve_round`` are untouched; only the permutation's law changes.
        """

        def _uniform_order(self, rng, region):            # type: ignore[override]
            cells = sorted(cell for cell in self.map.traversable if region_id(*cell) == region)
            if region != 1:
                rng.shuffle(cells)
                return tuple(cells)
            keyed = []
            for cell in cells:
                weight = _centripetal_weight(*cell)
                uniform = rng.random()
                while uniform == 0.0:
                    uniform = rng.random()
                keyed.append((-math.log(uniform) / weight, cell))
            keyed.sort()
            return tuple(cell for _, cell in keyed)

    runner.ScenarioGenerator = CentripetalScenario


def mode_field(args: argparse.Namespace) -> Mapping[str, Any]:
    from sim.runner import load_map
    from sim.scenario import ScenarioGenerator, region_id

    md = load_map(args.map)
    walls = {(r, c) for r, row in enumerate(md.rows)
             for c, ch in enumerate(row) if str(ch) == "1"}
    cells_by_ring: collections.Counter = collections.Counter()
    for r in range(GRID):
        for c in range(GRID):
            if (r, c) not in walls:
                cells_by_ring[ring_l1(r, c)] += 1

    def profile(model: str) -> Mapping[str, Any]:
        install_field(model)
        import sim.runner as runner
        generator_cls = runner.ScenarioGenerator if model == "centripetal" else ScenarioGenerator
        hits: collections.Counter = collections.Counter()
        hits_all: collections.Counter = collections.Counter()
        for seed in range(args.field_seeds):
            gen = generator_cls(md, seed)
            for rnd in range(ROUNDS):
                events = gen.resolve_round(rnd, None)
                for add in events.gold_additions:
                    ring = ring_l1(add.row, add.col)
                    hits_all[ring] += 1
                    if add.source == "central":
                        hits[ring] += 1
        denom = args.field_seeds * ROUNDS
        return {
            "central_only_rate": {str(k): hits[k] / (cells_by_ring[k] * denom)
                                  for k in sorted(cells_by_ring) if cells_by_ring[k]},
            "all_sources_rate": {str(k): hits_all[k] / (cells_by_ring[k] * denom)
                                 for k in sorted(cells_by_ring) if cells_by_ring[k]},
            "landings": sum(hits_all.values()),
        }

    uniform = profile("uniform")
    centripetal = profile("centripetal")
    ratio = {}
    for ring, measured in MEASURED_L1_RATE.items():
        key = str(ring)
        u = uniform["all_sources_rate"].get(key)
        c = centripetal["all_sources_rate"].get(key)
        ratio[key] = {"measured": measured,
                      "sim_uniform": u, "sim_uniform_over_measured": (u / measured) if u else None,
                      "sim_centripetal": c,
                      "sim_centripetal_over_measured": (c / measured) if c else None}
    return {
        "map": args.map, "games_sampled": args.field_seeds,
        "cells_per_l1_ring": {str(k): v for k, v in sorted(cells_by_ring.items())},
        "uniform": uniform, "centripetal": centripetal,
        "vs_measured": ratio,
        "note": "the repo default central law is spatially uniform over region 1 "
                "(sim/scenario.py:_make_central -> _uniform_order), so the simulator has NO "
                "gradient inside the central 9x9 where the measured gradient lives.  That biases "
                "the simulator AGAINST any hot-field knife: the knife's cost (forgone gold) is "
                "modelled correctly while its benefit (higher generation where it stays) is "
                "understated.  The centripetal arm restores the measured separable marginals.",
    }


# ==========================================================================================
# 4b.  geometry: the a-priori ceiling the measured field puts on any inward move
# ==========================================================================================

SIM_UNIFORM_L1_RATE = {0: 0.0238, 1: 0.0271, 2: 0.0286, 3: 0.0264, 4: 0.0260, 5: 0.0223,
                       6: 0.0122, 7: 0.0118, 8: 0.0035, 9: 0.0059, 10: 0.0023, 11: 0.0071,
                       12: 0.0069, 13: 0.0019, 14: 0.0021, 15: 0.0018, 16: 0.0023}


def _rate_measured(ring: int) -> float:
    if ring <= 2:
        return MEASURED_L1_RATE[2]
    return MEASURED_L1_RATE.get(ring, MEASURED_L1_RATE[6])


def mode_geometry(args: argparse.Namespace) -> Mapping[str, Any]:
    """How much window generation rate an inward move can possibly buy.

    The hit-rate law (CHANGELOG:328-330) says income is scoring-round frequency, and scoring-round
    frequency is bounded by how much gold appears inside the unit's own 5x5.  So the ceiling on any
    repositioning mechanism -- table-value or not -- is the ratio of window generation rates before
    and after the move.  Computed under the measured field and under the simulator's field, from
    the baseline's own measured position mix.
    """
    walls = _walls_of(args.map)

    def window_rate(srow: int, scol: int, rate) -> float:
        total = 0.0
        for i in range(5):
            for j in range(5):
                row, col = srow - 2 + i, scol - 2 + j
                if not (0 <= row < GRID and 0 <= col < GRID):
                    continue
                if (row, col) in walls:
                    continue
                total += rate(ring_l1(row, col))
        return total

    share = json.loads(Path(args.position_share).read_text()) if args.position_share else {
        "0": 0.0238, "1": 0.0570, "2": 0.1630, "3": 0.3041, "4": 0.2365,
        "5": 0.1165, "6": 0.0471, "7": 0.0269, "8": 0.0131, "9": 0.0057}
    share = {int(k): float(v) for k, v in share.items()}
    norm = sum(share.values())
    share = {k: v / norm for k, v in share.items()}
    mean_ring = sum(k * v for k, v in share.items())

    def by_ring(rate) -> Mapping[int, float]:
        buckets: dict[int, list[float]] = collections.defaultdict(list)
        for row in range(GRID):
            for col in range(GRID):
                if (row, col) in walls:
                    continue
                buckets[ring_l1(row, col)].append(window_rate(row, col, rate))
        return {k: statistics.fmean(v) for k, v in buckets.items()}

    def shifted(distribution: Mapping[int, float], amount: float) -> Mapping[int, float]:
        out: dict[int, float] = collections.defaultdict(float)
        for ring, weight in distribution.items():
            out[ring] += weight * (1.0 - amount)
            out[max(0, ring - 1)] += weight * amount
        return out

    out: dict[str, Any] = {"map": args.map, "mean_unit_l1_ring": mean_ring,
                           "position_share": {str(k): v for k, v in sorted(share.items())}}
    for tag, rate in (("measured", _rate_measured),
                      ("sim_uniform", lambda r: SIM_UNIFORM_L1_RATE.get(r, 0.002))):
        table = by_ring(rate)
        level = sum(share[k] * table[k] for k in share)
        cell = {"window_rate_by_unit_ring": {str(k): table[k] for k in sorted(table)},
                "baseline_window_rate": level,
                "window_rate_at_ring0": table[0],
                "headroom_to_ring0_pct": 100.0 * (table[0] / level - 1.0),
                "inward_shift_gain_pct": {}}
        for amount in (0.1, 0.2, 0.35, 0.5, 1.0):
            moved = shifted(share, amount)
            value = sum(moved[k] * table[k] for k in moved)
            cell["inward_shift_gain_pct"]["%.2f" % amount] = 100.0 * (value / level - 1.0)
        # inverse question: which starting ring has 1/2.6 of the champion's window rate?
        target = level / 2.6
        cell["ring_with_1_over_2p6_of_baseline"] = next(
            (k for k in sorted(table) if table[k] <= target), None)
        out[tag] = cell
    out["note"] = ("``ring_with_1_over_2p6_of_baseline`` answers 'where was the snake standing?': "
                   "the snake knife delivered x2.6 income, so under the hit-rate law its pre-knife "
                   "window rate was ~1/2.6 of the champion's current one.  The ring that produces "
                   "that rate is the snake's effective camp, and the distance from it to the "
                   "champion's current mean ring is the move the champion has ALREADY made.")
    return out


# ==========================================================================================
# 5.  probe: where the units stand and how often each rule fires
# ==========================================================================================

def _walls_of(map_name: str) -> frozenset:
    from sim.runner import load_map
    rows = load_map(map_name).rows
    return frozenset((r, c) for r, row in enumerate(rows)
                     for c, ch in enumerate(row) if str(ch) == "1")


def _select(grid, srow, scol, colv: Sequence[int], prio: Sequence[int]) -> Mapping[str, Any]:
    """Replicate f18064c's target pick under a given colv and priority table."""
    best = None
    for i in range(5):
        rrow = srow - 2 + i
        if not 0 <= rrow < GRID:
            continue
        mask = colv[scol]
        for j in range(5):
            if not (mask >> j) & 1:
                continue
            ccol = scol - 2 + j
            if not 0 <= ccol < GRID:
                continue
            if int(grid[rrow][ccol]) > 2:
                widx = i * 5 + j
                key = (prio[widx], widx)
                if best is None or key < best:
                    best = key
    has = best is not None
    standing = int(grid[srow][scol]) > 1
    if has:
        widx = best[1]
        target = (srow - 2 + widx // 5, scol - 2 + widx % 5)
    elif standing:
        target = (srow, scol)
    else:
        target = ((6, 8), (11, 8))
    return {"has": has, "standing": standing, "blind": (not has) and (not standing),
            "widx": best[1] if has else None, "target": target if has else None}


def mode_probe(args: argparse.Namespace) -> Mapping[str, Any]:
    from sim.runner import run_game
    install_field(args.field)
    colv_base = colv_table(None)
    arms = [name for name, spec in ARMS.items() if spec["kind"] == "colv"]
    colvs = {name: colv_table(ARMS[name]["rule"]) for name in arms}

    pos_col: collections.Counter = collections.Counter()
    pos_row: collections.Counter = collections.Counter()
    pos_ring: collections.Counter = collections.Counter()
    target_ring: collections.Counter = collections.Counter()
    kind_count: collections.Counter = collections.Counter()
    fire = {name: collections.Counter() for name in arms}
    delta_ring = {name: [] for name in arms}
    unit_rounds = 0
    our_score_rounds = 0
    opp_score_rounds = 0
    our_unit_rounds = 0
    opp_unit_rounds = 0

    for seed in args.seeds:
        for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
            result = run_game(args.base_so, args.base_so, map_source=args.map, seed=str(seed),
                              dispatch="fixed", fixed_costs=costs,
                              player1_name="base", player2_name="base")
            lines = result.log_bytes.decode().splitlines()
            for line in lines[2:]:
                if not line.strip():
                    continue
                record = json.loads(line)
                number = int(record["round"])
                grid = record["start"]["grid"]
                for pid, player in enumerate(record["end"]["players"], start=1):
                    for unit in player["units"]:
                        if pid == 1:
                            our_unit_rounds += 1
                            our_score_rounds += 1 if int(unit.get("pickup", 0)) > 0 else 0
                        else:
                            opp_unit_rounds += 1
                            opp_score_rounds += 1 if int(unit.get("pickup", 0)) > 0 else 0
                if number < STEADY_FROM:
                    continue
                for unit in record["start"]["players"][0]["units"]:
                    srow, scol = (int(v) for v in unit["position"])
                    unit_rounds += 1
                    pos_col[scol] += 1
                    pos_row[srow] += 1
                    pos_ring[ring_l1(srow, scol)] += 1
                    ref = _select(grid, srow, scol, colv_base, PRIO_BASE)
                    kind_count["has" if ref["has"] else
                                ("standing" if ref["standing"] else "blind")] += 1
                    if ref["has"]:
                        target_ring[ring_l1(*ref["target"])] += 1
                    for name in arms:
                        alt = _select(grid, srow, scol, colvs[name], PRIO_BASE)
                        if alt["widx"] != ref["widx"] or alt["has"] != ref["has"]:
                            fire[name]["changed"] += 1
                            if ref["has"] and not alt["has"]:
                                fire[name]["visible_to_blind"] += 1
                            elif ref["has"] and alt["has"]:
                                fire[name]["retargeted"] += 1
                                delta_ring[name].append(
                                    ring_l1(*alt["target"]) - ring_l1(*ref["target"]))
                        if ref["has"]:
                            widx = ref["widx"]
                            if not (colvs[name][scol] >> (widx % 5)) & 1:
                                fire[name]["chosen_column_suppressed"] += 1
    return {
        "map": args.map, "field": args.field, "seeds": list(args.seeds),
        "steady_from": STEADY_FROM,
        "our_unit_rounds": our_unit_rounds,
        "our_scoring_unit_rounds": our_score_rounds,
        "our_scoring_rate": our_score_rounds / max(1, our_unit_rounds),
        "opp_unit_rounds": opp_unit_rounds,
        "opp_scoring_unit_rounds": opp_score_rounds,
        "opp_scoring_rate": opp_score_rounds / max(1, opp_unit_rounds),
        "steady_unit_rounds": unit_rounds,
        "decision_kind_share": {k: v / max(1, unit_rounds) for k, v in kind_count.items()},
        "unit_column_share": {str(k): v / max(1, unit_rounds) for k, v in sorted(pos_col.items())},
        "unit_row_share": {str(k): v / max(1, unit_rounds) for k, v in sorted(pos_row.items())},
        "unit_l1_ring_share": {str(k): v / max(1, unit_rounds)
                               for k, v in sorted(pos_ring.items())},
        "chosen_target_l1_ring_share": {
            str(k): v / max(1, sum(target_ring.values())) for k, v in sorted(target_ring.items())},
        "rule_firing": {
            name: {
                "changed_share_of_unit_rounds": fire[name]["changed"] / max(1, unit_rounds),
                "chosen_column_suppressed_share": (
                    fire[name]["chosen_column_suppressed"] / max(1, unit_rounds)),
                "visible_to_blind_share": fire[name]["visible_to_blind"] / max(1, unit_rounds),
                "retargeted_share": fire[name]["retargeted"] / max(1, unit_rounds),
                "mean_target_ring_delta_when_retargeted": _mean(delta_ring[name]),
                "counts": dict(fire[name]),
            } for name in arms},
    }


# ==========================================================================================
# 6.  the A/B
# ==========================================================================================

def _game_stats(log_bytes: bytes) -> Mapping[str, Any]:
    """Per-seat scoring-round frequency, per-seat burn and drift, from one full log.

    Per-seat burn is exact and does not need an engine field: unit gold changes only by pickup
    (up) and by bomb / trample penalty (down), and there is no banking, so
    ``burn = previous + pickup - current`` for every unit-round.
    """
    per_seat = {1: collections.Counter(), 2: collections.Counter()}
    previous = {1: [0, 0], 2: [0, 0]}
    ring_hist: collections.Counter = collections.Counter()
    end_rings: list[int] = []
    burned_global = 0
    for line in log_bytes.decode().splitlines()[2:]:
        if not line.strip():
            continue
        record = json.loads(line)
        number = int(record["round"])
        burned_global += int(record["end"].get("burned", 0) or 0)
        for pid, player in enumerate(record["end"]["players"], start=1):
            for index, unit in enumerate(player["units"]):
                pickup = int(unit.get("pickup", 0))
                gold = int(unit.get("gold", 0))
                burn = previous[pid][index] + pickup - gold
                previous[pid][index] = gold
                cell = per_seat[pid]
                cell["unit_rounds"] += 1
                cell["burn"] += max(0, burn)
                if pickup > 0:
                    cell["scoring"] += 1
                    cell["pickup_total"] += pickup
                if number >= STEADY_FROM:
                    cell["steady_unit_rounds"] += 1
                    if pickup > 0:
                        cell["steady_scoring"] += 1
                if pid == 1 and number >= STEADY_FROM:
                    row, col = (int(v) for v in unit["position"])
                    end_rings.append(ring_l1(row, col))
                    ring_hist[ring_l1(row, col)] += 1
    out: dict[str, Any] = {"burned_global": burned_global,
                           "mean_end_l1_ring": _mean([float(v) for v in end_rings])}
    for pid, tag in ((1, "ours"), (2, "theirs")):
        cell = per_seat[pid]
        out["%s_unit_rounds" % tag] = cell["unit_rounds"]
        out["%s_scoring_rounds" % tag] = cell["scoring"]
        out["%s_scoring_rate" % tag] = cell["scoring"] / max(1, cell["unit_rounds"])
        out["%s_steady_scoring_rounds" % tag] = cell["steady_scoring"]
        out["%s_burn" % tag] = cell["burn"]
        out["%s_gold_per_scoring_round" % tag] = (
            cell["pickup_total"] / cell["scoring"] if cell["scoring"] else None)
    out["end_ring_share_ge6"] = (
        sum(v for k, v in ring_hist.items() if k >= 6) / max(1, sum(ring_hist.values())))
    return out


_TASK_FIELD = {"model": "uniform"}


def _play(task: Mapping[str, Any]) -> Mapping[str, Any]:
    from sim.runner import run_game
    install_field(task["field"])
    costs = COSTS_WE_FIRST if task["order"] == "we_first" else COSTS_WE_SECOND
    result = run_game(task["so"], task["opponent_so"], map_source=task["map"],
                      seed=str(task["seed"]), dispatch="fixed", fixed_costs=costs,
                      player1_name=task["arm"], player2_name="base")
    summary_ = result.summary
    stats = dict(_game_stats(result.log_bytes))
    stats.update({
        "arm": task["arm"], "seed": task["seed"], "order": task["order"],
        "field": task["field"], "band": task["band"],
        "net": int(summary_["players"]["1"]["net_gold"]),
        "opp_net": int(summary_["players"]["2"]["net_gold"]),
        "log_sha256": summary_["log_sha256"],
        "scenario_digest": summary_["scenario_digest"],
    })
    return stats


def mode_ab(args: argparse.Namespace) -> Mapping[str, Any]:
    workdir = Path(args.workdir)
    arms = args.arms or list(ARMS)
    sos = {arm: str(workdir / ("%s.so" % arm)) for arm in arms}
    base_so = str(workdir / "base.so")
    for arm, path in sos.items():
        if not Path(path).exists():
            raise SystemExit("missing %s -- run mode freeness first" % path)

    bands = {"in_sample": args.seeds, "out_of_sample": args.oos_seeds}
    tasks = []
    for band, seeds in bands.items():
        for seed in seeds:
            for order in ("we_first", "we_second"):
                for field in args.fields:
                    for arm in arms:
                        tasks.append({"arm": arm, "so": sos[arm], "opponent_so": base_so,
                                      "map": args.map, "seed": seed, "order": order,
                                      "field": field, "band": band})
    records: list[Mapping[str, Any]] = []
    with futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, row in enumerate(pool.map(_play, tasks, chunksize=1)):
            records.append(row)
            if args.progress and index % 25 == 0:
                print("  ... %d/%d" % (index + 1, len(tasks)), file=sys.stderr, flush=True)

    index_map: dict[tuple, Mapping[str, Any]] = {}
    for row in records:
        index_map[(row["band"], row["field"], row["order"], row["seed"], row["arm"])] = row

    # Integrity: within one (band, field, order, seed) every arm must face the SAME scenario, and
    # the two field models must face DIFFERENT ones -- otherwise the monkeypatch silently did not
    # take and the "centripetal" column would be a duplicate of "uniform".
    digest_groups: dict[tuple, set] = collections.defaultdict(set)
    for key, row in index_map.items():
        digest_groups[key[:4]].add(row["scenario_digest"])
    integrity = {
        "arms_share_scenario_within_cell": all(len(v) == 1 for v in digest_groups.values()),
        "field_models_differ": None,
    }
    if len(args.fields) > 1:
        by_field: dict[str, set] = collections.defaultdict(set)
        for key, row in index_map.items():
            by_field[key[1]].add(row["scenario_digest"])
        keys = list(by_field)
        integrity["field_models_differ"] = by_field[keys[0]].isdisjoint(by_field[keys[1]])
        integrity["distinct_digests_per_field"] = {k: len(v) for k, v in by_field.items()}

    def cell(band: str, field: str, orders: Sequence[str], arm: str) -> Mapping[str, Any]:
        margins, nets, opp_nets = [], [], []
        opp_score, our_score, burn, opp_burn, ring = [], [], [], [], []
        opp_score_rate, our_score_rate = [], []
        identical = 0
        total = 0
        for order in orders:
            for seed in bands[band]:
                base = index_map.get((band, field, order, seed, "base"))
                row = index_map.get((band, field, order, seed, arm))
                if base is None or row is None:
                    continue
                total += 1
                identical += 1 if row["log_sha256"] == base["log_sha256"] else 0
                margins.append((row["net"] - row["opp_net"]) - (base["net"] - base["opp_net"]))
                nets.append(row["net"] - base["net"])
                opp_nets.append(row["opp_net"] - base["opp_net"])
                opp_score.append(row["theirs_scoring_rounds"] - base["theirs_scoring_rounds"])
                our_score.append(row["ours_scoring_rounds"] - base["ours_scoring_rounds"])
                opp_score_rate.append(row["theirs_scoring_rate"] - base["theirs_scoring_rate"])
                our_score_rate.append(row["ours_scoring_rate"] - base["ours_scoring_rate"])
                burn.append(row["ours_burn"] - base["ours_burn"])
                opp_burn.append(row["theirs_burn"] - base["theirs_burn"])
                if row["mean_end_l1_ring"] is not None and base["mean_end_l1_ring"] is not None:
                    ring.append(row["mean_end_l1_ring"] - base["mean_end_l1_ring"])
        return {
            "games": total, "bit_identical_to_base": identical,
            "margin_delta": summary(margins), "net_delta": summary(nets),
            "opp_net_delta": summary(opp_nets),
            "opp_scoring_round_delta": summary(opp_score),
            "our_scoring_round_delta": summary(our_score),
            "opp_scoring_rate_delta": summary(opp_score_rate),
            "our_scoring_rate_delta": summary(our_score_rate),
            "our_burn_delta": summary(burn),
            "opp_burn_delta": summary(opp_burn),
            "mean_end_ring_delta": summary(ring),
        }

    aggregate: dict[str, Any] = {}
    for band in bands:
        for field in args.fields:
            for order_label, orders in (("we_first", ("we_first",)),
                                        ("we_second", ("we_second",)),
                                        ("pooled", ("we_first", "we_second"))):
                key = "%s|%s|%s" % (band, field, order_label)
                aggregate[key] = {arm: cell(band, field, orders, arm)
                                  for arm in arms if arm != "base"}
    base_levels = {}
    for band in bands:
        for field in args.fields:
            for order in ("we_first", "we_second"):
                rows = [index_map[k] for k in index_map
                        if k[0] == band and k[1] == field and k[2] == order and k[4] == "base"]
                if rows:
                    base_levels["%s|%s|%s" % (band, field, order)] = {
                        "net": summary([r["net"] for r in rows]),
                        "opp_net": summary([r["opp_net"] for r in rows]),
                        "margin": summary([r["net"] - r["opp_net"] for r in rows]),
                        "ours_scoring_rounds": summary([r["ours_scoring_rounds"] for r in rows]),
                        "theirs_scoring_rounds": summary([r["theirs_scoring_rounds"] for r in rows]),
                        "mean_end_l1_ring": _mean([r["mean_end_l1_ring"] for r in rows]),
                        "ours_burn": summary([r["ours_burn"] for r in rows]),
                        "theirs_burn": summary([r["theirs_burn"] for r in rows]),
                    }
    return {
        "map": args.map, "arms": arms, "fields": list(args.fields),
        "in_sample_seeds": list(args.seeds), "out_of_sample_seeds": list(args.oos_seeds),
        "dispatch": "fixed", "costs": {"we_first": list(COSTS_WE_FIRST),
                                       "we_second": list(COSTS_WE_SECOND)},
        "opponent": "unmodified f18064c baseline .so (self-play), so margin is well defined and "
                    "the opponent's scoring-round frequency is directly comparable",
        "base_levels": base_levels,
        "integrity": integrity,
        "aggregate": aggregate,
        "records": records if args.keep_records else [],
    }


# ==========================================================================================
# cli
# ==========================================================================================

def _seed_list(text: str) -> list[str]:
    out: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not part.startswith("-"):
            lo, hi = part.split("-", 1)
            out.extend(str(v) for v in range(int(lo), int(hi) + 1))
        else:
            out.append(part)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["tables", "field", "geometry", "probe", "freeness",
                                         "icount", "ab", "assemble"])
    parser.add_argument("--map", default="map1")
    parser.add_argument("--base-src", default="",
                        help="baseline source; empty = extract f18064c:src/player.cpp from git "
                             "into --workdir and verify its sha256")
    parser.add_argument("--base-so", default="")
    parser.add_argument("--workdir", default="/tmp/gr_tblknife/build")
    parser.add_argument("--inputs", default="")
    parser.add_argument("--calls", type=int, default=200000)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--seeds", type=_seed_list, default=_seed_list("1000-1011"))
    parser.add_argument("--oos-seeds", type=_seed_list, default=_seed_list("5000-5011"))
    parser.add_argument("--field", default="uniform", choices=["uniform", "centripetal"])
    parser.add_argument("--fields", default="uniform,centripetal")
    parser.add_argument("--field-seeds", type=int, default=8)
    parser.add_argument("--arms", default="")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--keep-records", action="store_true")
    parser.add_argument("--position-share", default="")
    parser.add_argument("--merge", action="append", default=[])
    parser.add_argument("--output", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.fields = [part for part in str(args.fields).split(",") if part]
    args.arms = [part for part in str(args.arms).split(",") if part]
    if not args.base_so:
        args.base_so = str(Path(args.workdir) / "base.so")

    if args.mode == "tables":
        payload: Mapping[str, Any] = mode_tables(args)
    elif args.mode == "field":
        payload = mode_field(args)
    elif args.mode == "geometry":
        payload = mode_geometry(args)
    elif args.mode == "probe":
        payload = mode_probe(args)
    elif args.mode == "freeness":
        payload = mode_freeness(args)
    elif args.mode == "icount":
        payload = mode_icount(args)
    elif args.mode == "ab":
        payload = mode_ab(args)
    else:
        merged: dict[str, Any] = {}
        for path in args.merge:
            merged[Path(path).stem] = json.loads(Path(path).read_text())
        payload = merged
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n")
        print("wrote %s (%d bytes)" % (args.output, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
