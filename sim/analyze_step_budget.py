#!/usr/bin/env python3
"""Positioning and step-budget reallocation: four arms in one apparatus.

Question
========

``sim/reports/order_sensitivity.md`` established that ~200 unit-rounds/game --
20% of all unit-rounds -- are ``blind``: no ``v > 2`` cell anywhere in the unit's
5x5, so the target degenerates to that unit's own anchor, the unit is *already
standing on that anchor*, ``d == 0`` fires and it emits ``(a, a^1, STAY)`` --
step out, step back, for nothing.  That is ~600 wasted steps per game.

Two ways to stop wasting them, measured in one apparatus because they share a
target function and may substitute for or compound each other:

======  ==================================================================
arm A   current: 3+3, fixed anchors (6,8)/(11,8)
arm B   positioning: anchors that are blind less often, including d<=1
arm C   conditional ``k``: accept the blind round, hand that unit's step
        budget to the other unit
arm D   B and C combined
======  ==================================================================

``k`` semantics, re-verified at source (third independent reading)
=================================================================

``src/game_api.h:58-60`` -- ``actions[6]``; ``k`` is a **split point in [0,6]``
with unit 0 executing ``actions[0..k-1]`` and unit 1 executing ``actions[k..5]``.
``sim/engine.py:1089-1090`` implements exactly ``actions[:decision.k]`` /
``actions[decision.k:]``, with ``ACTIONS_PER_PLAYER = 6`` and validation
``0 <= k <= 6`` at ``sim/engine.py:370-371``.  **The total budget is always 6.**
``k = 3`` is the delivered 3+3; ``k = 6`` gives unit 1 zero actions; ``k = 0``
gives unit 0 zero actions.  ``order`` is a separate orthogonal output selecting
which of *our* units steps first, and is passed through untouched everywhere in
this driver.

Because the split is positional, the same sweep covers both directions: if the
blind unit is unit 1 the producer's budget is ``k``; if the blind unit is unit 0
the producer's budget is ``6 - k``.  Both are exercised.

Why arm C is a legal same-round mechanism
=========================================

The order-sensitivity round established that dispatch order is **not** observable
within a round, so order-conditioned mechanisms must be lagged, and the best
lagged variant measured -88.2 +/- 23.0 gold/game.  Arm C is not subject to that:
its trigger is "this unit is blind", computed from our own fogged 5x5 in the same
round, exactly as the delivered selector already does.  No prophecy, no order
knowledge, no opponent visibility.

Isolating "more steps" from "better planning"
=============================================

The delivered LUT emits three steps.  When a unit is given 4-6, this driver does
**not** give it a smarter planner: it takes the real ``.so``'s own three-step
triple verbatim, simulates those three steps on the unit's own *fogged* view
(walls from the locked table, ``ceil(0.65 v)`` pickup, bomb burn), and then
re-applies the **same** target-selection + LUT + ``pass01`` policy from the new
position, taking as many of the second triple's steps as the budget allows.  The
only new logic is "re-enter the existing planner once".  Everything the arms do
is therefore expressible with the delivered algorithm; §"implementability" in the
report prices what re-entering it costs.

Parity, stated correctly
========================

``stay`` does not flip ``(row + col)`` parity; only a real move does.  So "three
actions cannot return to the origin" is false -- ``(out, back, stay)`` returns.
The true statement is that **visiting three distinct cells needs three real moves
= three parity flips, so it cannot end where it began**, which is what sank
``fold_tour`` at -81.4 +/- 18.5.  With an **even** move budget a unit can both
visit extra cells and return to its start, so that argument does not carry to a
4- or 6-move budget.  This asymmetry is the structural reason arm C is not a
re-run of ``fold_tour``, and it is why the primary failure mode to watch is
outward drift of the *extended* unit, reported at every budget.

What is NOT the mechanism
=========================

Vacate-and-re-harvest does not exist.  Generation never lands on an occupied
cell, but the round order is ``generate -> both decide -> faster player -> NPCs
-> slower player``, so occupancy at generation time is the **end-of-previous-round**
position, and the delivered fold, never-fold and ``k = 6`` all end on the same
cell.  All three block that cell identically.  Nothing here is designed around it.

Modes
=====

``ksemantics``  print the third independent re-verification of ``k`` from source
``geometry``    static anchor geometry: per-window generation weight from the
                measured centripetal ring table, vision-union size, wall count
``blind``       the blind decomposition -- (1) supply vs (2) threshold -- plus the
                baseline drift distribution and realised pickup by centre ring
``verify``      fidelity gates: arm A reproduces the baseline ``log_sha256``
                byte for byte through the full apparatus; replica agreement
``ab``          the four-arm same-seed paired A/B, both order conditions, with the
                uncontested ``probeobs`` north star
``assemble``    the machine-readable companion
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.abi import SharedObjectStrategy                        # noqa: E402
from sim.analyze_miss_taxonomy import (                         # noqa: E402  read-only reuse
    PRIO,
    SL_FACT,
    SL_PDC,
    SL_PDR,
    BuildState,
    _clamp3,
    _steer_step,
    replica_decide_unit,
)
from sim.engine import PlayerInput                              # noqa: E402
from sim.runner import load_map, run_game                       # noqa: E402

GRID = 17
NCELL = GRID * GRID
STAY = 4
FOG, WALL, BOMB = -5, -1, -3
PLAYER_MARK, NPC_MARK = -2, -4
DR = (-1, 1, 0, 0, 0)
DC = (0, 0, -1, 1, 0)
CENTRE = (8, 8)
ROUNDS = 500
STEADY_FROM = 8
BOMB_WAVE = 20

DEFAULT_ANCHORS = ((6, 8), (11, 8))     # f18064c: anch_r[u] = 6 + 5u, anch_c[u] = 8

# Engine rule: faster = 1 if costs[1] <= costs[2] else 2.  Our strategy sits at
# seat 1 in every run below, so the cost pair alone selects the order arm.
COSTS_WE_FIRST = (200, 201)
COSTS_WE_SECOND = (201, 200)

# Measured single-cell generation frequency by Chebyshev distance from (8,8),
# sim/GENERATION.md Sec 3.3 (1497 regular rounds, 2611 landings, map1).  The
# gradient is *under*-stated because occupancy suppresses generation and the
# seven NPCs all spawn at (8,8), which is exactly why this driver reports
# realised pickup by ring alongside it.
RING_FREQ = {0: 66.0, 1: 78.2, 2: 55.4, 3: 43.2, 4: 22.8}
RING_FREQ_OUTER = 22.8 / 2.0      # d >= 5 is outer-ring pulse territory, not central


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summary(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        return {"n": 0}
    n = len(values)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    return {
        "n": n, "mean": mean, "sd": sd, "se": se,
        "sigma": (mean / se) if se > 0 else None,
        "min": min(values), "max": max(values),
    }


def cheb(cell: Sequence[int], other: Sequence[int] = CENTRE) -> int:
    return max(abs(cell[0] - other[0]), abs(cell[1] - other[1]))


# ---------------------------------------------------------------------------
# k semantics, re-verified from source
# ---------------------------------------------------------------------------


def ksemantics() -> Mapping[str, Any]:
    header = (ROOT / "src" / "game_api.h").read_text().splitlines()
    engine = (ROOT / "sim" / "engine.py").read_text().splitlines()

    def find(lines, needle, limit=4):
        return [
            {"line": index + 1, "text": text.strip()}
            for index, text in enumerate(lines) if needle in text
        ][:limit]

    return {
        "header_actions_decl": find(header, "int actions[S]"),
        "header_k_decl": find(header, "int      k;"),
        "header_order_decl": find(header, "int      order;"),
        "header_S_const": find(header, "constexpr int S"),
        "engine_split": find(engine, "decision.actions[:decision.k]")
        + find(engine, "decision.actions[decision.k:]"),
        "engine_actions_per_player": find(engine, "ACTIONS_PER_PLAYER = 6"),
        "engine_k_validation": find(engine, "k must be in 0..6"),
        "engine_exec_order": find(engine, "for unit_index in (decision.order, 1 - decision.order)"),
        "conclusion": (
            "k is a split point over a SIX-action array: unit 0 executes "
            "actions[:k] and unit 1 executes actions[k:], so the total budget is "
            "always 6 and k redistributes it.  k=3 is the delivered 3+3, k=6 "
            "gives unit 1 zero actions, k=0 gives unit 0 zero actions.  order is "
            "orthogonal: it only selects which of our two units steps first."
        ),
        "both_directions_covered_by_one_sweep": (
            "the blind unit's budget is 6-k when it is unit 1 and k when it is "
            "unit 0, so sweeping the producer budget over {3,4,5,6} exercises "
            "k in {3,4,5,6} for a blind unit 1 and k in {3,2,1,0} for a blind unit 0"
        ),
    }


# ---------------------------------------------------------------------------
# parameterised replica of the frozen selector (anchors are the only knob)
# ---------------------------------------------------------------------------


def plan_unit(
    grid: Sequence[Sequence[int]],
    unit: int,
    srow: int,
    scol: int,
    held: int,
    state: BuildState,
    anchors: Sequence[Sequence[int]],
    *,
    threshold: int = 2,
) -> tuple[tuple[int, int, int], dict[str, Any]]:
    """``sim.analyze_miss_taxonomy.replica_decide_unit`` with the anchor exposed.

    Line-for-line the same selector, LUT router, ``pass01`` waypoint gate and
    escape mask; the *only* difference is that the blind branch targets
    ``anchors[unit]`` instead of the module constant ``ANCHORS[unit]``.  The
    ``verify`` mode proves that with ``anchors = DEFAULT_ANCHORS`` this function
    reproduces ``replica_decide_unit`` exactly, and that the whole apparatus then
    reproduces the baseline log byte for byte.
    """
    rich = held >= 100
    best = None
    for i in range(5):
        rrow = srow - 2 + i
        if not 0 <= rrow < GRID:
            continue
        for j in range(5):
            ccol = scol - 2 + j
            if not 0 <= ccol < GRID:
                continue
            value = int(grid[rrow][ccol])
            if value > threshold:
                widx = i * 5 + j
                key = (PRIO[widx], widx)
                if best is None or key < best:
                    best = key
            elif value == BOMB:
                state.bombbit.add(rrow * GRID + ccol)

    has = best is not None
    standing = int(grid[srow][scol]) > 1
    blind = (not has) and (not standing)
    if has:
        widx = best[1]
        target = (srow - 2 + widx // 5, scol - 2 + widx % 5)
    elif standing:
        target = (srow, scol)
    else:
        target = (int(anchors[unit][0]), int(anchors[unit][1]))

    dr0 = _clamp3(target[0] - srow)
    dc0 = _clamp3(target[1] - scol)
    distance = abs(dr0) + abs(dc0)
    acts = [STAY, STAY, STAY]
    gate_ok: bool | None = None
    if distance == 0:
        mask = 0
        for action in range(4):
            if state.passable(srow + DR[action], scol + DC[action], rich):
                mask |= 1 << action
        gate_ok = mask != 0
        if mask:
            action = (mask & -mask).bit_length() - 1
            acts[0] = action
            acts[1] = action ^ 1
    else:
        plan = SL_FACT[dr0 + 3][dc0 + 3]
        xrow = SL_PDR[dr0 + 3][dc0 + 3]
        xcol = SL_PDC[dr0 + 3][dc0 + 3]
        gate_ok = all(
            state.passable(srow + xrow[t], scol + xcol[t], rich) for t in range(3)
        )
        if gate_ok:
            acts = list(plan)
        else:
            action = _steer_step(
                state, srow, scol, target[0], target[1],
                state.last_r[unit], state.last_c[unit], rich,
            )
            if action >= 0:
                acts[0] = action

    state.last_r[unit] = srow
    state.last_c[unit] = scol
    return (
        (acts[0], acts[1], acts[2]),
        {
            "rich": rich, "has": has, "standing": standing, "blind": blind,
            "target": target, "d": distance, "gate_ok": gate_ok,
        },
    )


def walk_forward(
    grid: list[list[int]],
    srow: int,
    scol: int,
    held: int,
    triple: Sequence[int],
    walls: frozenset,
) -> tuple[int, int, int, list[tuple[int, int]]]:
    """Advance one unit through its own triple on its own fogged view.

    Mirrors the engine's rules that the unit itself can predict: bounds and wall
    skip the step, a completed move picks up ``ceil(0.65 v)``, a bomb burns
    ``ceil(held/10)``.  Player-vs-player collision is deliberately **not**
    modelled, matching the delivered build, whose ``pass01`` teammate check was
    retired as provably redundant (measured 0 collision rounds).  Fog cells are
    treated as holding nothing, because that is what the unit knows.
    """
    row, col = srow, scol
    entered: list[tuple[int, int]] = []
    for action in triple:
        if action == STAY:
            continue
        nrow, ncol = row + DR[action], col + DC[action]
        if not (0 <= nrow < GRID and 0 <= ncol < GRID):
            continue
        if (nrow * GRID + ncol) in walls or grid[nrow][ncol] == WALL:
            continue
        row, col = nrow, ncol
        entered.append((row, col))
        value = grid[row][col]
        if value > 0:
            amount = (65 * value + 99) // 100
            grid[row][col] = value - amount
            held += amount
        elif value == BOMB:
            grid[row][col] = 0
            held -= (held + 9) // 10
    return row, col, held, entered


# ---------------------------------------------------------------------------
# the four arms, in one strategy
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ArmSpec:
    """One point in the (positioning x step-budget) design."""

    name: str
    family: str                                  # "A" | "B" | "B2" | "C" | "D"
    anchors: tuple[tuple[int, int], tuple[int, int]] = DEFAULT_ANCHORS
    producer_budget: int = 3                     # 3 = no reallocation
    blind_tail: str = "truncate"                 # "truncate" | "stay"
    trigger: str = "blind"
    extend: str = "replan"                       # "replan" | "lut"
    # extend vocabulary
    #   "replan"  the mainline apparatus: forward-simulate the producer's own
    #             triple on its fogged view, then RE-ENTER plan_unit from the
    #             mid position and take the first (budget - 3) actions of the
    #             second plan.  This is a second scan + second target selection:
    #             an ALGORITHM change, not a budget change.
    #   "lut"     the only faithful budget-only extension: read columns 3..
    #             (budget-1) of the SAME plan for the SAME target out of a wider
    #             constexpr LUT.  Provably a pure suffix of the delivered triple.
    # trigger vocabulary
    #   "blind"   fire on every blind unit; the blind unit is the donor  (mainline)
    #   "idle"    fire only when the blind unit planned ZERO real moves
    #   "one"     fire only when EXACTLY ONE unit is blind -- with budget 3 +
    #             "stay" this is the *rate-matched* silence-only control that
    #             ``Cd_silence_only`` is not (that one also fires on both-blind
    #             rounds, ~3x the volume)
    #   "flip"    same trigger rounds as "blind", but the SIGHTED unit is the
    #             donor: pure-asymmetry control, blindness no longer chooses
    #   "random"  same trigger rounds as "blind", donor chosen by a deterministic
    #             per-(seed, round) coin: the pre-registered discriminator

    @property
    def reallocates(self) -> bool:
        return self.producer_budget != 3


def _coin(salt: int, round_number: int) -> int:
    """A deterministic, reproducible per-(seed, round) fair coin."""
    x = (int(salt) ^ (int(round_number) * 0x9E3779B97F4A7C15)) & 0xFFFFFFFFFFFFFFFF
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    x = (x ^ (x >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    return (x ^ (x >> 31)) & 1


def _salt(seed: Any) -> int:
    return int.from_bytes(
        hashlib.blake2b(str(seed).encode(), digest_size=8).digest(), "big")


# ---------------------------------------------------------------------------
# the ONE faithful budget-only extension: widen the delivered LUT
# ---------------------------------------------------------------------------
#
# ``SLut`` in ``src/player.cpp:188`` is ``uint8_t fact[7][7][3]`` -- the width is
# a compile-time 3 and ``out.k = 3`` is a constant at ``:524``.  Re-entering the
# planner (what arm C's ``producer_budget`` actually does) is a *second scan and
# second target selection*, i.e. an algorithm change.  The only extension that is
# purely a budget change is to widen the same constexpr table and read one more
# column of the SAME plan for the SAME target.  Widening is provably a pure
# suffix: ``fact_w[dr][dc][:3] == fact_3[dr][dc]`` for all 49 entries (asserted
# below), so the delivered three actions are preserved byte for byte.


def _build_slut_wide(width: int):
    """``_build_slut`` at an arbitrary width, same row-major rule, same fold."""
    fact = [[None] * 7 for _ in range(7)]
    pdr = [[None] * 7 for _ in range(7)]
    pdc = [[None] * 7 for _ in range(7)]
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            row, col = 0, 0
            acts, prow, pcol = [], [], []
            for _step in range(width):
                rr, cc = dr - row, dc - col
                adr, adc = abs(rr), abs(cc)
                action = STAY
                if adr | adc:
                    if adr >= adc:
                        action = 1 if rr > 0 else 0
                        row += 1 if rr > 0 else -1
                    else:
                        action = 3 if cc > 0 else 2
                        col += 1 if cc > 0 else -1
                acts.append(action)
                prow.append(row)
                pcol.append(col)
            distance = abs(dr) + abs(dc)
            if 0 < distance < width:
                for t in range(distance, width):
                    acts[t] = acts[t - 1] ^ 1
            fact[dr + 3][dc + 3] = tuple(acts)
            pdr[dr + 3][dc + 3] = tuple(prow)
            pdc[dr + 3][dc + 3] = tuple(pcol)
    return (tuple(tuple(r) for r in fact), tuple(tuple(r) for r in pdr),
            tuple(tuple(r) for r in pdc))


SLUT_WIDE = {w: _build_slut_wide(w) for w in (3, 4, 5, 6)}
for _w in (4, 5, 6):                                   # the pure-suffix property
    for _i in range(7):
        for _j in range(7):
            if SLUT_WIDE[_w][0][_i][_j][:3] != SL_FACT[_i][_j]:
                raise AssertionError(
                    "widening is not a pure suffix at width %d, (%d,%d)" % (_w, _i, _j))
del _w, _i, _j


def _rewrite(inner: PlayerInput, grid) -> PlayerInput:
    return dataclasses.replace(
        inner, grid=tuple(tuple(int(v) for v in row) for row in grid))


class BudgetStrategy:
    """Arms A-D on top of the real frozen ``.so``.

    Discipline that makes the A/B exact:

    * rounds below ``steady_from`` are passed through verbatim, because the
      build's slow-open layer (fingerprint lock, baked BFS march, ``vp``) is not
      replicated and must not be perturbed;
    * a unit the replica calls **non-blind** always uses the ``.so``'s own triple
      verbatim -- the arms only ever touch blind units and the step budget;
    * a unit the replica calls **blind** uses ``plan_unit`` with this arm's
      anchors, which for ``DEFAULT_ANCHORS`` is provably the same triple, so
      arm A reproduces the baseline log byte for byte (``verify``);
    * the producer's extension re-enters the *same* planner from the position
      reached after its first three steps; ``last_r``/``last_c`` are restored to
      the round-start position afterwards so the next round sees exactly what the
      delivered build would see (they feed only the rare escape fallback).
    """

    name = "budget"

    def __init__(self, base_so: Path, spec: ArmSpec, *, walls: frozenset,
                 steady_from: int = STEADY_FROM, record: bool = False,
                 salt: int = 0) -> None:
        self.base = SharedObjectStrategy(base_so, name="budget_base")
        self.spec = spec
        self.walls = walls
        self.steady_from = int(steady_from)
        self.record = record
        self.salt = int(salt)
        self.build = BuildState(walls)
        self.ref = BuildState(walls)
        self.last_round = 10 ** 9
        # counters
        self.rounds = 0
        self.unit_rounds = 0
        self.blind_unit_rounds = 0
        self.both_blind_rounds = 0
        self.one_blind_rounds = 0
        self.reallocated_rounds = 0
        self.reallocated_to_u0 = 0
        self.reallocated_to_u1 = 0
        self.extension_steps_used = 0
        self.extension_steps_effective = 0
        self.donor_was_blind = 0
        self.replan_targets = 0
        self.replan_target_outside_start_window = 0
        self.replan_blind = 0
        self.lut_widen_used = 0
        self.lut_widen_refused = 0
        self.lut_tail_gate_tested = 0
        self.lut_tail_gate_rejected = 0
        self.blind_override_rounds = 0
        self.replica_match = 0
        self.replica_total = 0
        self.anchor_default_mismatch = 0
        self.k_histogram: collections.Counter = collections.Counter()
        self.planned_moves_when_blind: collections.Counter = collections.Counter()
        self.donor_planned_moves: collections.Counter = collections.Counter()
        self.rows: list[dict[str, Any]] = []

    def close(self) -> None:
        self.base.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.walls)
            self.ref = BuildState(self.walls)
        if round_number % BOMB_WAVE == 0:
            self.build.bombbit.clear()
            self.ref.bombbit.clear()
        self.last_round = round_number
        self.rounds += 1

        decision = self.base(value)
        actions = [int(item) for item in decision.actions]
        base_k, order, vp = int(decision.k), int(decision.order), int(decision.vp)
        if round_number < self.steady_from or base_k != 3:
            self.k_histogram[base_k] += 1
            return tuple(actions) + (base_k, order, vp)

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        gold = [int(item) for item in value.my_units_gold]

        # --- pass 1: this arm's planner, in the build's own unit order ---------
        plans: list[tuple[int, int, int]] = []
        infos: list[dict[str, Any]] = []
        for unit in (0, 1):
            triple, info = plan_unit(
                grid, unit, units[unit][0], units[unit][1], gold[unit],
                self.build, self.spec.anchors,
            )
            plans.append(triple)
            infos.append(info)
            emitted = tuple(actions[unit * 3:unit * 3 + 3])
            self.replica_total += 1
            self.replica_match += int(triple == emitted)
            self.unit_rounds += 1
            if info["blind"]:
                self.blind_unit_rounds += 1

        # a default-anchor arm must never disagree with the .so on a blind unit
        if self.spec.anchors == DEFAULT_ANCHORS:
            for unit in (0, 1):
                if infos[unit]["blind"] and plans[unit] != tuple(actions[unit * 3:unit * 3 + 3]):
                    self.anchor_default_mismatch += 1

        blind = [bool(infos[unit]["blind"]) for unit in (0, 1)]
        if blind[0] and blind[1]:
            self.both_blind_rounds += 1
        elif blind[0] or blind[1]:
            self.one_blind_rounds += 1

        # --- arm B: a blind unit walks to THIS arm's anchor --------------------
        triples = [tuple(actions[0:3]), tuple(actions[3:6])]
        for unit in (0, 1):
            if blind[unit] and self.spec.anchors != DEFAULT_ANCHORS:
                triples[unit] = plans[unit]
                self.blind_override_rounds += 1

        # --- arm C: hand the blind unit's budget to the producer ---------------
        k = 3
        out = list(triples[0]) + list(triples[1])
        eligible = [False, False]
        for unit in (0, 1):
            if not blind[unit]:
                continue
            planned = sum(1 for a in triples[unit] if a != STAY)
            self.planned_moves_when_blind[planned] += 1
            if self.spec.trigger == "idle":
                eligible[unit] = planned == 0
            else:
                eligible[unit] = True
        if (not self.spec.reallocates) and self.spec.blind_tail == "stay":
            # donor-side control: silence the blind unit but give the producer
            # nothing extra, so k stays 3.  Isolates "stop the blind unit
            # walking" from "hand its budget to the producer".
            gate = (blind[0] != blind[1]) if self.spec.trigger == "one" else True
            for unit in (0, 1):
                if eligible[unit] and gate:
                    triples[unit] = (STAY, STAY, STAY)
                    self.reallocated_rounds += 1
                    self.donor_planned_moves[
                        sum(1 for a in (plans[unit] if self.spec.anchors != DEFAULT_ANCHORS
                                        else tuple(actions[unit * 3:unit * 3 + 3]))
                            if a != STAY)] += 1
            out = list(triples[0]) + list(triples[1])
        if self.spec.reallocates and (eligible[0] != eligible[1]) and (blind[0] != blind[1]):
            blind_unit = 0 if eligible[0] else 1
            # who actually gives up its budget.  For the mainline "blind" trigger
            # that is the blind unit; the two controls below break that link
            # without changing which rounds fire or how many steps move.
            donor = blind_unit
            if self.spec.trigger == "flip":
                donor = 1 - blind_unit
            elif self.spec.trigger == "random":
                if _coin(self.salt, round_number):
                    donor = 1 - blind_unit
            self.donor_was_blind += int(donor == blind_unit)
            producer = 1 - donor
            self.donor_planned_moves[
                sum(1 for a in triples[donor] if a != STAY)] += 1
            budget = int(self.spec.producer_budget)
            extra = budget - 3
            head = triples[producer]
            prow, pcol = units[producer]
            if self.spec.extend == "lut":
                # ---- faithful budget-only widening -------------------------
                # same target, same plan, one more column of the same table.
                # Only legal when the delivered triple really IS the LUT plan
                # (the steer/escape fallback path has no wider form), so the
                # gate is: the widened prefix must equal what the .so emitted.
                info = infos[producer]
                srow, scol = units[producer]
                dr0 = _clamp3(int(info["target"][0]) - srow)
                dc0 = _clamp3(int(info["target"][1]) - scol)
                wide = SLUT_WIDE[budget][0][dr0 + 3][dc0 + 3]
                xrow = SLUT_WIDE[budget][1][dr0 + 3][dc0 + 3]
                xcol = SLUT_WIDE[budget][2][dr0 + 3][dc0 + 3]
                rich = gold[producer] >= 100
                head_ok = (info["d"] != 0 and bool(info["gate_ok"])
                           and tuple(wide[:3]) == tuple(head))
                tail_ok = all(
                    self.build.passable(srow + xrow[t], scol + xcol[t], rich)
                    for t in range(3, budget))
                if head_ok:
                    self.lut_tail_gate_tested += 1
                    if not tail_ok:
                        self.lut_tail_gate_rejected += 1
                usable = head_ok and tail_ok
                if usable:
                    tail = tuple(wide[3:budget])
                    self.lut_widen_used += 1
                else:
                    tail = tuple([STAY] * extra)
                    self.lut_widen_refused += 1
                info2 = {"has": False, "target": info["target"], "blind": False}
            else:
                work = [list(row) for row in grid]
                prow, pcol, pheld, _entered = walk_forward(
                    work, units[producer][0], units[producer][1], gold[producer],
                    head, self.walls,
                )
                keep_r, keep_c = self.build.last_r[producer], self.build.last_c[producer]
                triple2, info2 = plan_unit(
                    work, producer, prow, pcol, pheld, self.build, self.spec.anchors,
                )
                self.build.last_r[producer], self.build.last_c[producer] = keep_r, keep_c
                tail = tuple(triple2[:extra])
            producer_actions = tuple(head) + tail
            self.extension_steps_used += len(tail)
            self.extension_steps_effective += sum(1 for a in tail if a != STAY)
            # is the second leg aimed at a cell the producer could NOT see from
            # its round-start square?  ``work`` is the round-start fogged grid,
            # so no fog is broken -- but a target outside the round-start 5x5 is
            # reach the delivered 3-step LUT structurally does not have.
            if info2["has"]:
                self.replan_targets += 1
                if max(abs(info2["target"][0] - units[producer][0]),
                       abs(info2["target"][1] - units[producer][1])) > 2:
                    self.replan_target_outside_start_window += 1
            self.replan_blind += int(bool(info2["blind"]))

            if self.spec.blind_tail == "stay":
                blind_actions = tuple([STAY] * (6 - budget))
            else:
                blind_actions = tuple(triples[donor][:6 - budget])

            if producer == 0:
                out = list(producer_actions) + list(blind_actions)
                k = budget
            else:
                out = list(blind_actions) + list(producer_actions)
                k = 6 - budget
            self.reallocated_rounds += 1
            if producer == 0:
                self.reallocated_to_u0 += 1
            else:
                self.reallocated_to_u1 += 1
            if self.record:
                self.rows.append({
                    "round": round_number, "producer": producer,
                    "blind_unit": blind_unit, "donor": donor,
                    "donor_was_blind": donor == blind_unit,
                    "k": k, "budget": budget,
                    "producer_start": units[producer],
                    "producer_mid": (prow, pcol),
                    "donor_planned_moves": sum(1 for a in triples[donor] if a != STAY),
                    "replan_target": list(info2["target"]),
                    "replan_has": bool(info2["has"]),
                    "tail": list(tail),
                })
        elif self.record:
            self.rows.append({
                "round": round_number, "producer": None,
                "blind": list(blind), "k": k,
            })

        if len(out) != 6:
            raise AssertionError("assembled action array must be length 6, got %d" % len(out))
        if not 0 <= k <= 6:
            raise AssertionError("assembled k out of range: %d" % k)
        self.k_histogram[k] += 1
        return tuple(int(a) for a in out) + (k, order, vp)


# ---------------------------------------------------------------------------
# static anchor geometry
# ---------------------------------------------------------------------------


def _window(cell: Sequence[int], radius: int = 2) -> list[tuple[int, int]]:
    return [
        (row, col)
        for row in range(max(0, cell[0] - radius), min(GRID, cell[0] + radius + 1))
        for col in range(max(0, cell[1] - radius), min(GRID, cell[1] + radius + 1))
    ]


def _ring_weight(cell: Sequence[int]) -> float:
    return RING_FREQ.get(cheb(cell), RING_FREQ_OUTER)


ANCHOR_VARIANTS: tuple[tuple[str, tuple[tuple[int, int], tuple[int, int]]], ...] = (
    ("A_current_6_8__11_8", ((6, 8), (11, 8))),
    ("V1_6_8__10_8", ((6, 8), (10, 8))),
    ("V2_7_8__9_8", ((7, 8), (9, 8))),
    ("V3_8_7__8_9", ((8, 7), (8, 9))),
    ("V4_7_8__10_8", ((7, 8), (10, 8))),
    ("V5_6_8__9_8", ((6, 8), (9, 8))),
    ("V6_7_8__8_9", ((7, 8), (8, 9))),
    ("V7_8_8__10_8", ((8, 8), (10, 8))),
    ("D1_6_6__10_10", ((6, 6), (10, 10))),
    ("D2_6_10__10_6", ((6, 10), (10, 6))),
)


def _neighbours(cell: Sequence[int]) -> list[tuple[int, int]]:
    out = []
    for action in range(4):
        row, col = cell[0] + DR[action], cell[1] + DC[action]
        if 0 <= row < GRID and 0 <= col < GRID:
            out.append((row, col))
    return out


def sacrifice_trade(map_name: str = "map1") -> Mapping[str, Any]:
    """The B2 sacrifice trade, for every free cell within Chebyshev 4 of centre.

    Generation never lands on an occupied cell and occupancy is evaluated at the
    **end-of-previous-round** position, so a unit parked on a cell permanently
    sterilises it.  The anchor cell's own generation frequency is therefore a
    **cost**, not a benefit, and the quantity B2 wants to maximise is the
    generation weight of the cells the unit can still reach and return from --
    its free orthogonal neighbours, reachable by an even-parity out-and-back.
    """
    rows = load_map(map_name).rows
    walls = {
        (r, c) for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    }
    table = []
    for row in range(GRID):
        for col in range(GRID):
            cell = (row, col)
            if cell in walls or cheb(cell) > 4:
                continue
            free = [n for n in _neighbours(cell) if n not in walls]
            own = _ring_weight(cell)
            neighbour_weight = sum(_ring_weight(n) for n in free)
            table.append({
                "cell": [row, col],
                "cheb_from_centre": cheb(cell),
                "own_weight_sterilised": own,
                "free_neighbours": len(free),
                "free_neighbour_weight": neighbour_weight,
                "sacrifice_ratio": neighbour_weight / own if own else None,
                "net_weight": neighbour_weight - own,
            })
    table.sort(key=lambda item: -item["sacrifice_ratio"])
    return {
        "map": map_name,
        "rule": "generation excludes occupied cells; occupancy is the "
                "end-of-previous-round position (round order: generate -> both "
                "decide -> faster -> NPCs -> slower), so a parked unit "
                "permanently sterilises its own cell",
        "best_by_sacrifice_ratio": table[:12],
        "current_anchors": [
            item for item in table if item["cell"] in ([6, 8], [11, 8])
        ],
        "d1_peak_cells": [item for item in table if item["cheb_from_centre"] == 1],
        "finding": (
            "the four free d=1 cells (7,8) (9,8) (8,7) (8,9) are POCKETS: the "
            "pinwheel walls (7,7) (7,9) (9,7) (9,9) and (8,6) (8,10) leave them "
            "only one or two free orthogonal neighbours, so camping ON the peak "
            "sterilises a 78.2 cell and can reach at most 121.4 of neighbour "
            "weight, whereas (6,8) and (10,8) at d=2 sterilise only 55.4 and "
            "reach 232.2.  The sacrifice trade therefore points AWAY from the "
            "peak, and (6,8) -- the delivered u0 anchor -- is already close to "
            "optimal."
        ),
    }


def geometry(map_name: str = "map1") -> Mapping[str, Any]:
    """Per-anchor-pair generation weight, vision union, wall exposure, sacrifice."""
    rows = load_map(map_name).rows
    walls = {
        (r, c) for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    }
    out: dict[str, Any] = {
        "map": map_name,
        "ring_freq_source": "sim/GENERATION.md Sec 3.3, 1497 regular rounds / 2611 landings",
        "ring_freq": RING_FREQ,
        "centre": list(CENTRE),
        "free_cells_at_d1": sorted(
            [list(c) for c in _window(CENTRE, 1) if c not in walls and cheb(c) == 1]),
        "walls_at_d1": sorted(
            [list(c) for c in _window(CENTRE, 1) if c in walls and cheb(c) == 1]),
        "sacrifice_trade": sacrifice_trade(map_name),
        "variants": {},
    }
    for name, anchors in ANCHOR_VARIANTS:
        cells = [_window(a) for a in anchors]
        union = set(cells[0]) | set(cells[1])
        overlap = set(cells[0]) & set(cells[1])
        free_union = [c for c in union if c not in walls]
        per_anchor = []
        for index, anchor in enumerate(anchors):
            free = [c for c in cells[index] if c not in walls]
            neighbours = [n for n in _neighbours(anchor) if n not in walls]
            per_anchor.append({
                "anchor": list(anchor),
                "cheb_from_centre": cheb(anchor),
                "anchor_is_wall": tuple(anchor) in walls,
                "window_cells": len(cells[index]),
                "window_free_cells": len(free),
                "window_generation_weight": sum(_ring_weight(c) for c in free),
                "own_weight_sterilised": _ring_weight(anchor),
                "free_neighbours": len(neighbours),
                "free_neighbour_weight": sum(_ring_weight(n) for n in neighbours),
            })
        sterilised = sum(item["own_weight_sterilised"] for item in per_anchor)
        central = {(r, c) for r in range(4, 13) for c in range(4, 13)}
        out["variants"][name] = {
            "anchors": [list(a) for a in anchors],
            "per_anchor": per_anchor,
            "central_9x9_coverage": len(union & central) / len(central),
            "central_9x9_cells_covered": len(union & central),
            "central_columns_covered": sorted({c for _r, c in (union & central)}),
            "union_cells": len(union),
            "union_free_cells": len(free_union),
            "overlap_cells": len(overlap),
            "union_generation_weight": sum(_ring_weight(c) for c in free_union),
            "sterilised_weight": sterilised,
            "net_generation_weight": sum(_ring_weight(c) for c in free_union) - sterilised,
            "reachable_neighbour_weight": sum(
                item["free_neighbour_weight"] for item in per_anchor),
            "anchor_separation_manhattan": abs(anchors[0][0] - anchors[1][0])
            + abs(anchors[0][1] - anchors[1][1]),
        }
    base = out["variants"]["A_current_6_8__11_8"]
    for name, cell in out["variants"].items():
        cell["union_weight_vs_current"] = (
            cell["union_generation_weight"] / base["union_generation_weight"])
        cell["net_weight_vs_current"] = (
            cell["net_generation_weight"] / base["net_generation_weight"])
        cell["neighbour_weight_vs_current"] = (
            cell["reachable_neighbour_weight"] / base["reachable_neighbour_weight"])
    out["trade_off_note"] = (
        "moving the anchors together raises the single-cell frequency AT the "
        "anchor but (a) that cell is the one we sterilise, so its own frequency "
        "is a cost not a benefit, and (b) the two 5x5 windows begin to overlap, "
        "shrinking the vision union.  Both halves are reported.  The blind rate "
        "and the realised pickup in `blind` / `anchorcells` net them out."
    )
    return out


# ---------------------------------------------------------------------------
# the blind decomposition, drift, and realised pickup by ring
# ---------------------------------------------------------------------------


def _pure_ground(grid) -> list[list[int]]:
    return [
        [0 if int(v) in (PLAYER_MARK, NPC_MARK, FOG) else int(v) for v in row]
        for row in grid
    ]


class BlindCensus:
    """Passthrough census of why a unit is blind, and where it ends up.

    Classification of each blind unit-round, MECE:

    * ``supply``     -- no positive cell anywhere in the unit's 5x5.  Genuinely
                        nothing near this position; a different anchor could help.
    * ``threshold``  -- a positive cell IS in the 5x5 but every one has
                        ``v <= 2``, so the ``v > 2`` scan gate filtered it.  This
                        class is already judged negative: ``v>2`` -> ``v>0`` is a
                        zero-instruction change that gains +8.51pp of hit rate and
                        loses 75 gold relatively, so if it dominates, arm B hits
                        the same wall.

    ``threshold`` is further split by whether the filtered cell is reachable in
    three cardinal steps (Manhattan <= 3), since an unreachable one is not
    actionable at any threshold.
    """

    name = "blind_census"

    def __init__(self, base_so: Path, *, walls: frozenset,
                 anchors=DEFAULT_ANCHORS, steady_from: int = STEADY_FROM) -> None:
        self.base = SharedObjectStrategy(base_so, name="blind_base")
        self.walls = walls
        self.anchors = anchors
        self.steady_from = int(steady_from)
        self.build = BuildState(walls)
        self.last_round = 10 ** 9
        self.units: list[dict[str, Any]] = []

    def close(self) -> None:
        self.base.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.walls)
        if round_number % BOMB_WAVE == 0:
            self.build.bombbit.clear()
        self.last_round = round_number

        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)
        if round_number < self.steady_from:
            return actions + (k, order, vp)

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        gold = [int(item) for item in value.my_units_gold]
        truth = value.start.state.ground

        for unit in (0, 1):
            triple, info = plan_unit(
                grid, unit, units[unit][0], units[unit][1], gold[unit],
                self.build, self.anchors,
            )
            srow, scol = units[unit]
            low_any = 0
            low_reach = 0
            low_value = 0
            high_any = 0
            for row in range(max(0, srow - 2), min(GRID, srow + 3)):
                for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                    v = grid[row][col]
                    if v <= 0:
                        continue
                    if v > 2:
                        high_any += 1
                        continue
                    low_any += 1
                    low_value += v
                    if abs(row - srow) + abs(col - scol) <= 3:
                        low_reach += 1
            # fog-free truth in the same window, to price a wider view
            true_any = 0
            for row in range(max(0, srow - 2), min(GRID, srow + 3)):
                for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                    if truth[row][col] > 0:
                        true_any += 1
            self.units.append({
                "round": round_number, "unit": unit,
                "cell": [srow, scol], "cheb_from_centre": cheb((srow, scol)),
                "held": gold[unit],
                "blind": bool(info["blind"]),
                "has": bool(info["has"]), "standing": bool(info["standing"]),
                "d": int(info["d"]),
                "low_any": low_any, "low_reach": low_reach, "low_value": low_value,
                "high_any": high_any, "true_positive_cells": true_any,
                "triple": list(triple),
                "replica_match": triple == tuple(actions[unit * 3:unit * 3 + 3]),
            })
        return actions + (k, order, vp)


def _unit_series(log_bytes: bytes):
    """Per-round per-unit (position, gold-delta) for both seats, from the log."""
    lines = log_bytes.decode().splitlines()
    records = [json.loads(line) for line in lines[2:] if line.strip()]
    previous = {1: [0, 0], 2: [0, 0]}
    for record in records:
        number = int(record["round"])
        out = {}
        for player in record["end"]["players"]:
            pid = int(player["id"])
            golds = [int(u["gold"]) for u in player["units"]]
            ends = [tuple(int(v) for v in u["position"]) for u in player["units"]]
            starts = [
                tuple(int(v) for v in u["position"])
                for u in record["start"]["players"][pid - 1]["units"]
            ]
            out[pid] = {
                "delta": [golds[i] - previous[pid][i] for i in (0, 1)],
                "pickup": [int(u.get("pickup", 0)) for u in player["units"]],
                "end": ends, "start": starts,
                "effective": [
                    [int(a) for a in u.get("actions", [])] for u in player["units"]
                ],
            }
            previous[pid] = golds
        yield number, out


def blind(map_name: str, base_so: Path, seeds: Sequence[str],
          *, steady_from: int = STEADY_FROM) -> Mapping[str, Any]:
    """Blind decomposition, baseline drift distribution, realised pickup by ring."""
    rows = load_map(map_name).rows
    walls = frozenset(
        r * GRID + c for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    )
    arms: dict[str, list[dict[str, Any]]] = {"we_first": [], "we_second": []}
    drift: dict[str, list[dict[str, Any]]] = {"we_first": [], "we_second": []}
    for seed in seeds:
        for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
            census = BlindCensus(base_so, walls=walls, steady_from=steady_from)
            result = run_game(
                census, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            arms[label].extend(census.units)
            census.close()
            for number, payload in _unit_series(result.log_bytes):
                if number < steady_from:
                    continue
                mine = payload[1]
                for unit in (0, 1):
                    effective = mine["effective"][unit]
                    drift[label].append({
                        "round": number, "unit": unit,
                        "end_cheb": cheb(mine["end"][unit]),
                        "start_cheb": cheb(mine["start"][unit]),
                        "pickup": mine["pickup"][unit],
                        "delta": mine["delta"][unit],
                        "effective_moves": sum(1 for a in effective if a != STAY),
                        "planned_len": len(effective),
                    })

    def fold_blind(rowset: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        total = len(rowset)
        blinds = [row for row in rowset if row["blind"]]
        n = max(1, len(blinds))
        supply = [row for row in blinds if row["low_any"] == 0]
        threshold = [row for row in blinds if row["low_any"] > 0]
        threshold_reach = [row for row in threshold if row["low_reach"] > 0]
        by_unit = collections.Counter(row["unit"] for row in blinds)
        return {
            "unit_rounds": total,
            "blind_unit_rounds": len(blinds),
            "blind_share_of_unit_rounds": len(blinds) / max(1, total),
            "blind_unit_rounds_per_game": len(blinds) / max(1, total) * 2 * ROUNDS,
            "blind_by_unit": {str(k): v for k, v in sorted(by_unit.items())},
            "blind_share_u0": by_unit[0] / max(1, total / 2),
            "blind_share_u1": by_unit[1] / max(1, total / 2),
            "class_1_supply": len(supply) / n,
            "class_2_threshold": len(threshold) / n,
            "class_2_threshold_reachable": len(threshold_reach) / n,
            "class_2_threshold_unreachable": (len(threshold) - len(threshold_reach)) / n,
            "mean_low_value_when_threshold": _mean([row["low_value"] for row in threshold]),
            "supply_but_truth_has_gold": (
                sum(1 for row in supply if row["true_positive_cells"] > 0) / max(1, len(supply))),
            "replica_match_rate": _mean([1.0 if row["replica_match"] else 0.0 for row in rowset]),
            "planned_real_moves_when_blind": {
                str(k): v / n for k, v in sorted(collections.Counter(
                    sum(1 for a in row["triple"] if a != STAY) for row in blinds).items())},
            "wasted_steps_per_game_fully_idle": (
                sum(3 for row in blinds if all(a == STAY for a in row["triple"]))
                / max(1, total) * 2 * ROUNDS),
            "wasted_steps_per_game_fold_shape": (
                sum(1 for row in blinds
                    if sum(1 for a in row["triple"] if a != STAY) == 2)
                / max(1, total) * 2 * ROUNDS),
            "blind_by_ring": {
                str(ring): {
                    "unit_rounds": len(group),
                    "blind_share": _mean([1.0 if row["blind"] else 0.0 for row in group]),
                }
                for ring, group in sorted(
                    _group(rowset, lambda row: row["cheb_from_centre"]).items())
            },
        }

    def fold_drift(rowset: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        n = max(1, len(rowset))
        histogram = collections.Counter(row["end_cheb"] for row in rowset)
        return {
            "unit_rounds": len(rowset),
            "mean_end_cheb": _mean([row["end_cheb"] for row in rowset]),
            "end_cheb_histogram": {str(k): v / n for k, v in sorted(histogram.items())},
            "share_end_d_le_1": sum(1 for row in rowset if row["end_cheb"] <= 1) / n,
            "share_end_d_le_2": sum(1 for row in rowset if row["end_cheb"] <= 2) / n,
            "share_end_d_ge_4": sum(1 for row in rowset if row["end_cheb"] >= 4) / n,
            "pickup_by_end_ring": {
                str(ring): {
                    "unit_rounds": len(group),
                    "share": len(group) / n,
                    "mean_pickup": _mean([row["pickup"] for row in group]),
                    "mean_delta": _mean([row["delta"] for row in group]),
                    "hit_rate": _mean([1.0 if row["delta"] > 0 else 0.0 for row in group]),
                }
                for ring, group in sorted(
                    _group(rowset, lambda row: row["end_cheb"]).items())
            },
            "by_effective_moves": {
                str(count): {
                    "unit_rounds": len(group),
                    "share": len(group) / n,
                    "unit_rounds_per_game": len(group) / n * 2 * ROUNDS,
                    "steps_per_game": len(group) / n * 2 * ROUNDS * 3,
                    "mean_pickup": _mean([row["pickup"] for row in group]),
                    "mean_delta": _mean([row["delta"] for row in group]),
                    "hit_rate": _mean([1.0 if row["delta"] > 0 else 0.0 for row in group]),
                    "mean_end_cheb": _mean([row["end_cheb"] for row in group]),
                    "mean_ring_drift": _mean([
                        row["end_cheb"] - row["start_cheb"] for row in group]),
                }
                for count, group in sorted(
                    _group(rowset, lambda row: row["effective_moves"]).items())
            },
            "pickup_by_start_ring": {
                str(ring): {
                    "unit_rounds": len(group),
                    "share": len(group) / n,
                    "mean_pickup": _mean([row["pickup"] for row in group]),
                    "mean_delta": _mean([row["delta"] for row in group]),
                    "hit_rate": _mean([1.0 if row["delta"] > 0 else 0.0 for row in group]),
                }
                for ring, group in sorted(
                    _group(rowset, lambda row: row["start_cheb"]).items())
            },
        }

    return {
        "map": map_name,
        "seeds": [str(s) for s in seeds],
        "steady_from": steady_from,
        "we_first": {"blind": fold_blind(arms["we_first"]), "drift": fold_drift(drift["we_first"])},
        "we_second": {"blind": fold_blind(arms["we_second"]), "drift": fold_drift(drift["we_second"])},
        "gate_b_precondition_note": (
            "class_2_threshold is already judged negative: the v>2 -> v>0 change "
            "is zero-instruction, gains +8.51pp of hit rate and loses 75 gold "
            "relatively.  If class 2 dominates, arm B hits the same wall.  This "
            "decomposition gates arm B only; arm C reallocates budget regardless "
            "of why the unit is blind."
        ),
    }


def _group(rowset, key):
    out: dict[Any, list] = collections.defaultdict(list)
    for row in rowset:
        out[key(row)].append(row)
    return out


# ---------------------------------------------------------------------------
# B2's mandatory measurement: what we actually COLLECT from the cells we vacate
# ---------------------------------------------------------------------------


def anchorcells(map_name: str, base_so: Path, seeds: Sequence[str], *,
                arm: str = "A_current", steady_from: int = STEADY_FROM) -> Mapping[str, Any]:
    """Per-cell generation, sterilisation and *realised* collection near centre.

    B2's premise is that a parked unit sterilises its own cell while its rich
    neighbours stay fertile, so it can step out, collect, and return.  The
    Master's stated risk is that all seven NPCs spawn at ``(8,8)`` and NPCs
    consume 65.6% of map1's gold, so a vacated cell may simply be harvested by an
    NPC.  This mode answers that directly by replaying every round in exact
    dispatch order and attributing every pickup to us / the opponent / an NPC.

    The replay is the one validated in ``sim/reports/order_sensitivity.md``
    (predicted end-of-round pure ground == official log on 9000/9000 rounds).
    Generation per cell is read straight out of the logs as
    ``start_ground[t] - end_ground[t-1]`` clipped at zero, which is exact because
    generation is the only process that adds ground gold.
    """
    map_definition = load_map(map_name)
    rows = map_definition.rows
    walls = frozenset(
        r * GRID + c for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    )
    spec = _extra_arm(arm)
    out: dict[str, Any] = {
        "map": map_name, "arm": arm, "anchors": [list(a) for a in spec.anchors],
        "seeds": [str(s) for s in seeds], "steady_from": steady_from, "arms": {},
    }
    for label, costs, we_first in (
        ("we_first", COSTS_WE_FIRST, True),
        ("we_second", COSTS_WE_SECOND, False),
    ):
        generated: collections.Counter = collections.Counter()
        taken: dict[str, collections.Counter] = {
            "ours": collections.Counter(), "opp": collections.Counter(),
            "npc": collections.Counter(),
        }
        occupied: collections.Counter = collections.Counter()
        rounds_counted = 0
        games = 0
        exact = 0
        total = 0
        for seed in seeds:
            shim = BudgetStrategy(base_so, spec, walls=walls, steady_from=steady_from)
            result = run_game(
                shim, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name=arm, player2_name="opponent",
            )
            shim.close()
            games += 1
            replay = _replay_game(result.log_bytes)
            for record in replay:
                if record["round"] < steady_from:
                    continue
                rounds_counted += 1
                total += 1
                exact += int(record["ground_exact"])
                for cell, amount in record["generated"].items():
                    generated[cell] += amount
                for side in ("ours", "opp", "npc"):
                    for cell, amount in record["taken"][side].items():
                        taken[side][cell] += amount
                for cell in record["our_end_cells"]:
                    occupied[cell] += 1
        per_cell = []
        for row in range(GRID):
            for col in range(GRID):
                cell = (row, col)
                if (row * GRID + col) in walls or cheb(cell) > 4:
                    continue
                total_taken = sum(taken[side][cell] for side in ("ours", "opp", "npc"))
                per_cell.append({
                    "cell": [row, col],
                    "cheb_from_centre": cheb(cell),
                    "generated_per_game": generated[cell] / games,
                    "ours_per_game": taken["ours"][cell] / games,
                    "opp_per_game": taken["opp"][cell] / games,
                    "npc_per_game": taken["npc"][cell] / games,
                    "our_share_of_taken": (
                        taken["ours"][cell] / total_taken if total_taken else None),
                    "npc_share_of_taken": (
                        taken["npc"][cell] / total_taken if total_taken else None),
                    "our_occupancy_rate": occupied[cell] / max(1, rounds_counted / 2),
                })
        anchors = [tuple(a) for a in spec.anchors]
        lookup = {tuple(item["cell"]): item for item in per_cell}
        anchor_rows = [lookup[a] for a in anchors if a in lookup]
        neighbour_rows = []
        for anchor in anchors:
            for n in _neighbours(anchor):
                if n in lookup:
                    neighbour_rows.append({**lookup[n], "neighbour_of": list(anchor)})
        by_ring = {}
        for ring, group in sorted(_group(per_cell, lambda item: item["cheb_from_centre"]).items()):
            by_ring[str(ring)] = {
                "cells": len(group),
                "generated_per_game": sum(item["generated_per_game"] for item in group),
                "ours_per_game": sum(item["ours_per_game"] for item in group),
                "npc_per_game": sum(item["npc_per_game"] for item in group),
                "opp_per_game": sum(item["opp_per_game"] for item in group),
                "our_capture_rate": (
                    sum(item["ours_per_game"] for item in group)
                    / max(1e-9, sum(item["generated_per_game"] for item in group))),
                "npc_capture_rate": (
                    sum(item["npc_per_game"] for item in group)
                    / max(1e-9, sum(item["generated_per_game"] for item in group))),
            }
        out["arms"][label] = {
            "games": games,
            "replay_ground_exact": exact,
            "replay_rounds": total,
            "replay_ground_exact_all": exact == total,
            "anchor_cells": anchor_rows,
            "anchor_neighbours": neighbour_rows,
            "by_ring": by_ring,
            "centre_cell_8_8": lookup.get((8, 8)),
        }
    return out


def _replay_game(log_bytes: bytes):
    """Exact per-round replay with per-cell, per-actor-class attribution.

    Purely log-driven, and therefore exact with no model at all: the official log
    records ``end.dispatch_order`` (the nine actor turns in the order the engine
    ran them), every player unit's **effective** actions, and every NPC's
    effective actions.  Replaying effective actions needs no blocking logic --
    a step the engine refused is already ``STAY`` -- so the only thing being
    re-derived is *which cell each pickup came from*, which the log does not
    record.  The gate is that the replayed end-of-round pure ground must equal
    the logged one, cell for cell.
    """
    lines = log_bytes.decode().splitlines()
    records = [json.loads(line) for line in lines[2:] if line.strip()]
    previous_end: list[list[int]] | None = None
    out = []
    for record in records:
        number = int(record["round"])
        start_ground = _pure_ground(record["start"]["grid"])
        end_ground = _pure_ground(record["end"]["grid"])
        generated: dict[tuple[int, int], int] = {}
        if previous_end is not None:
            for row in range(GRID):
                for col in range(GRID):
                    delta = start_ground[row][col] - previous_end[row][col]
                    if delta > 0:
                        generated[(row, col)] = delta
        previous_end = end_ground

        sim = _LogSim(start_ground, record["start"])
        dispatch = [int(v) for v in record["end"]["dispatch_order"]]
        for actor in dispatch:
            if actor > 0:
                payload = record["end"]["players"][actor - 1]
                unit_order = int(payload.get("order", 0))
                for unit_index in (unit_order, 1 - unit_order):
                    for action in payload["units"][unit_index]["actions"]:
                        sim.step_player((actor, unit_index), int(action))
            else:
                npc = next(item for item in record["end"]["npcs"]
                           if int(item["id"]) == actor)
                for action in npc["actions"]:
                    sim.step_npc(actor, int(action))

        taken = {"ours": collections.Counter(), "opp": collections.Counter(),
                 "npc": collections.Counter()}
        for kind, actor, cell, amount in sim.taken:
            if kind == "npc":
                taken["npc"][cell] += amount
            elif actor[0] == 1:
                taken["ours"][cell] += amount
            else:
                taken["opp"][cell] += amount
        our_end = [
            tuple(int(v) for v in unit["position"])
            for unit in record["end"]["players"][0]["units"]
        ]
        out.append({
            "round": number,
            "generated": generated,
            "taken": taken,
            "our_end_cells": our_end,
            "ground_exact": sim.board == end_ground,
        })
    return out


class _LogSim:
    """Minimal exact board replay driven by logged *effective* actions."""

    __slots__ = ("board", "ppos", "npos", "taken")

    def __init__(self, ground: list[list[int]], start_phase: Mapping[str, Any]) -> None:
        self.board = [list(row) for row in ground]
        self.ppos: dict[tuple[int, int], tuple[int, int]] = {}
        for pid in (1, 2):
            for index, unit in enumerate(start_phase["players"][pid - 1]["units"]):
                self.ppos[(pid, index)] = tuple(int(v) for v in unit["position"])
        self.npos: dict[int, tuple[int, int]] = {
            int(item["id"]): tuple(int(v) for v in item["position"])
            for item in start_phase["npcs"]
        }
        self.taken: list[tuple[str, Any, tuple[int, int], int]] = []

    def _land(self, kind, actor, row, col):
        value = self.board[row][col]
        if value > 0:
            amount = (65 * value + 99) // 100
            self.board[row][col] = value - amount
            self.taken.append((kind, actor, (row, col), amount))
        if self.board[row][col] == BOMB:
            self.board[row][col] = 0

    def step_player(self, key, action):
        if action == STAY:
            return
        row, col = self.ppos[key]
        nrow, ncol = row + DR[action], col + DC[action]
        if not (0 <= nrow < GRID and 0 <= ncol < GRID):
            return
        self.ppos[key] = (nrow, ncol)
        self._land("player", key, nrow, ncol)

    def step_npc(self, npc_id, action):
        if action == STAY:
            return
        row, col = self.npos[npc_id]
        nrow, ncol = row + DR[action], col + DC[action]
        if not (0 <= nrow < GRID and 0 <= ncol < GRID):
            return
        self.npos[npc_id] = (nrow, ncol)
        self._land("npc", npc_id, nrow, ncol)


# ---------------------------------------------------------------------------
# the uncontested probeobs north star
# ---------------------------------------------------------------------------


class ProbeObs:
    """The real uploaded observation probe, wrapped as a local strategy.

    ``sim/probe/player.py`` is the file the arena received: it buys the 9x9 view
    every round and moves only to maintain observation, never using gold as a
    movement objective, which is why the platform calls it a deliberately-losing
    probe.  Running our arms against it locally reproduces the *shape* of the
    platform's uncontested measurement (the number whose ``f18064c`` value is
    2182.4 and whose owner target is >= 2500).  Absolute local income is not
    platform-comparable; only the paired delta is.
    """

    name = "probeobs"

    def __init__(self) -> None:
        from sim.probe.player import Player

        self.player = Player()

    def close(self) -> None:
        return None

    def __call__(self, value: Any) -> Any:
        return self.player.MoveDecision(value)


# ---------------------------------------------------------------------------
# the paired A/B over arms
# ---------------------------------------------------------------------------

ARM_TABLE: tuple[ArmSpec, ...] = (
    ArmSpec("A_current", "A"),
    # --- arm B: positioning ------------------------------------------------
    ArmSpec("B_V1_6_8__10_8", "B", anchors=((6, 8), (10, 8))),
    ArmSpec("B_V2_7_8__9_8", "B", anchors=((7, 8), (9, 8))),
    ArmSpec("B_V3_8_7__8_9", "B", anchors=((8, 7), (8, 9))),
    ArmSpec("B_V4_7_8__10_8", "B", anchors=((7, 8), (10, 8))),
    ArmSpec("B_V5_6_8__9_8", "B", anchors=((6, 8), (9, 8))),
    ArmSpec("B_V6_7_8__8_9", "B", anchors=((7, 8), (8, 9))),
    ArmSpec("B_V7_8_8__10_8", "B", anchors=((8, 8), (10, 8))),
    # --- arm B2: sacrifice a cheap cell whose neighbours are rich ------------
    # the sacrifice-optimal pairs by neighbour weight / own weight; note that
    # (6,8)/(10,8) is simultaneously V1 and the B2 optimum, and that the four
    # free d=1 cells are pockets with only one or two free neighbours
    ArmSpec("B2_bracket_6_8__10_8", "B2", anchors=((6, 8), (10, 8))),
    ArmSpec("B2_5_8__11_8", "B2", anchors=((5, 8), (11, 8))),
    ArmSpec("B2_6_7__10_9", "B2", anchors=((6, 7), (10, 9))),
    # split-board / diagonal pairs: the delivered anchors are stacked on column 8
    # and every previous sweep varied only u1's row, so the "each unit covers
    # half the board" degree of freedom has never been exercised.  (6,6)+(10,10)
    # is the coverage-optimal static two-window placement (49/81 = 60.5% of the
    # central 9x9 against the delivered 45/81 = 55.6%).
    ArmSpec("B2_diag_6_6__10_10", "B2", anchors=((6, 6), (10, 10))),
    ArmSpec("B2_diag_6_10__10_6", "B2", anchors=((6, 10), (10, 6))),
    # --- arm C: conditional k, producer budget 4/5/6, both tails -----------
    ArmSpec("C_k4_truncate", "C", producer_budget=4),
    ArmSpec("C_k5_truncate", "C", producer_budget=5),
    ArmSpec("C_k6_truncate", "C", producer_budget=6),
    ArmSpec("C_k4_stay", "C", producer_budget=4, blind_tail="stay"),
    ArmSpec("C_k5_stay", "C", producer_budget=5, blind_tail="stay"),
    ArmSpec("C_k6_stay", "C", producer_budget=6, blind_tail="stay"),
    # --- arm C, idle-only trigger: donor has ZERO planned real moves, so the
    #     opportunity cost of taking its budget is zero by construction.  The
    #     difference (blind trigger - idle trigger) is the part of arm C's gain
    #     that had to be bought from low-efficiency 1-2 move unit-rounds.
    ArmSpec("Cidle_k4", "C", producer_budget=4, trigger="idle"),
    ArmSpec("Cidle_k5", "C", producer_budget=5, trigger="idle"),
    ArmSpec("Cidle_k6", "C", producer_budget=6, trigger="idle"),
    # donor-side-only control: blind unit stays, producer keeps 3 steps (k = 3)
    ArmSpec("Cd_silence_only", "C", producer_budget=3, blind_tail="stay"),
    # RATE-MATCHED donor-side control: silence the blind unit ONLY on the rounds
    # arm C actually fires on (exactly one unit blind).  ``Cd_silence_only``
    # above also silences both-blind rounds, ~3x the unit-rounds, so it is not a
    # valid decomposition term for arm C.
    ArmSpec("Cd1_silence_matched", "C", producer_budget=3, blind_tail="stay",
            trigger="one"),
    # --- the pre-registered red-flag controls -------------------------------
    #     identical trigger rounds, identical total step budget, identical
    #     number of silenced steps; only WHICH unit is silenced changes.
    ArmSpec("Crand_k6", "C", producer_budget=6, trigger="random"),
    ArmSpec("Cflip_k6", "C", producer_budget=6, trigger="flip"),
    ArmSpec("Crand_k5_stay", "C", producer_budget=5, blind_tail="stay",
            trigger="random"),
    ArmSpec("Cflip_k5_stay", "C", producer_budget=5, blind_tail="stay",
            trigger="flip"),
    ArmSpec("Crand_k4_stay", "C", producer_budget=4, blind_tail="stay",
            trigger="random"),
    ArmSpec("Cflip_k4_stay", "C", producer_budget=4, blind_tail="stay",
            trigger="flip"),
    # --- the faithful budget-only variants: LUT widening, no re-plan --------
    #     these are the ONLY arms in the table that a shippable build can
    #     express without a second scan; the delivered three actions are a
    #     byte-for-byte prefix of what they emit.
    ArmSpec("Clut_k4_stay", "C", producer_budget=4, blind_tail="stay",
            extend="lut"),
    ArmSpec("Clut_k5_stay", "C", producer_budget=5, blind_tail="stay",
            extend="lut"),
    ArmSpec("Clut_k6_stay", "C", producer_budget=6, blind_tail="stay",
            extend="lut"),
    # and the same two red-flag controls applied to the faithful variant
    ArmSpec("Clutrand_k6", "C", producer_budget=6, blind_tail="stay",
            extend="lut", trigger="random"),
    ArmSpec("Clutflip_k6", "C", producer_budget=6, blind_tail="stay",
            extend="lut", trigger="flip"),
    # --- arm D: the best positioning candidate x the best budget candidate.
    #     Gate D: must beat the better of B and C individually, else "not additive".
    ArmSpec("D_5_8__11_8_k5_stay", "D", anchors=((5, 8), (11, 8)),
            producer_budget=5, blind_tail="stay"),
    ArmSpec("D_6_7__10_9_k5_stay", "D", anchors=((6, 7), (10, 9)),
            producer_budget=5, blind_tail="stay"),
    ArmSpec("D_diag_6_6__10_10_k5_stay", "D", anchors=((6, 6), (10, 10)),
            producer_budget=5, blind_tail="stay"),
    # k=4 is the architectural maximum: the 5x5 scan cannot supply targets for a
    # longer leg, and reaching further needs a 7x7 scan at ~+196 instructions
    # (~314 gold), more than 3x this line's whole prize pool.  These are the
    # SHIPPABLE variants; k=5 and k=6 above are diagnostics for the saturation
    # prediction only and must not be proposed.
    ArmSpec("D_diag_6_6__10_10_k4_stay", "D", anchors=((6, 6), (10, 10)),
            producer_budget=4, blind_tail="stay"),
    ArmSpec("D_diag_6_6__10_10_k4_trunc", "D", anchors=((6, 6), (10, 10)),
            producer_budget=4),
)

ARMS_BY_NAME = {spec.name: spec for spec in ARM_TABLE}


def _extra_arm(name: str) -> ArmSpec:
    """Parse an on-the-fly arm D spec: ``D_<anchorvariant>_k<budget>[_stay]``."""
    if name in ARMS_BY_NAME:
        return ARMS_BY_NAME[name]
    if not name.startswith("D_"):
        raise KeyError("unknown arm %r" % name)
    body = name[2:]
    tail = "truncate"
    if body.endswith("_stay"):
        tail = "stay"
        body = body[: -len("_stay")]
    variant, _, budget = body.rpartition("_k")
    lookup = {vname[3:] if vname[1].isdigit() else vname: anchors
              for vname, anchors in ANCHOR_VARIANTS}
    anchors = None
    for vname, value in ANCHOR_VARIANTS:
        if vname == variant or vname.startswith(variant) or variant in vname:
            anchors = value
            break
    if anchors is None:
        raise KeyError("unknown anchor variant %r in %r" % (variant, name))
    del lookup
    trigger = "idle" if "_idle" in name else "blind"
    budget = budget.replace("_idle", "")
    return ArmSpec(name, "D", anchors=anchors, producer_budget=int(budget),
                   blind_tail=tail, trigger=trigger)


def _install_field(model: str) -> None:
    """Reuse the hot-field line's in-process central-field calibration.

    ``sim/scenario.py::_make_central`` places central gold **uniformly** over region 1,
    so the stock board has no gradient inside the central 9x9 -- exactly where the
    measured gradient lives (ring1->ring5 steepness 3.35x measured, 1.22x uniform,
    2.51x under the calibrated separable law).  ``sim/analyze_hotfield_table.install_field``
    monkeypatches only the permutation's law, in this process only, and never writes
    ``sim/scenario.py``.  A candidate whose whole premise is *where in the centre to
    stand* must be judged on a board that has a centre.
    """
    from sim.analyze_hotfield_table import install_field   # noqa: PLC0415
    install_field(model)


def ab(map_name: str, base_so: Path, seeds: Sequence[str], *,
       arms: Sequence[str], opponent: str = "self",
       steady_from: int = STEADY_FROM, field: str = "uniform") -> Mapping[str, Any]:
    """Same-seed paired closed-loop A/B of every arm, both order conditions."""
    _install_field(field)
    rows = load_map(map_name).rows
    walls = frozenset(
        r * GRID + c for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    )
    specs = [_extra_arm(name) for name in arms]
    records: list[dict[str, Any]] = []

    def make_opponent():
        return base_so if opponent == "self" else ProbeObs()

    for seed in seeds:
        for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
            other = make_opponent()
            baseline = run_game(
                base_so, other, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name=opponent,
            )
            if opponent != "self":
                other.close()
            base_net = int(baseline.summary["players"]["1"]["net_gold"])
            base_opp = int(baseline.summary["players"]["2"]["net_gold"])
            row: dict[str, Any] = {
                "seed": str(seed), "arm_order": label, "we_move_first": label == "we_first",
                "fixed_costs": list(costs), "opponent": opponent,
                "scenario_digest": baseline.summary["scenario_digest"],
                "base_net": base_net, "base_opp_net": base_opp,
                "base_margin": base_net - base_opp,
                "base_sha": baseline.summary["log_sha256"],
            }
            base_drift = _drift_stats(baseline.log_bytes, steady_from)
            row["base_drift"] = base_drift
            for spec in specs:
                shim = BudgetStrategy(base_so, spec, walls=walls, steady_from=steady_from,
                                      salt=_salt(seed))
                other = make_opponent()
                played = run_game(
                    shim, other, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=costs, player1_name=spec.name, player2_name=opponent,
                )
                if opponent != "self":
                    other.close()
                net = int(played.summary["players"]["1"]["net_gold"])
                opp = int(played.summary["players"]["2"]["net_gold"])
                row[spec.name] = {
                    "net": net,
                    "delta": net - base_net,
                    "opp_net": opp,
                    "margin_delta": (net - opp) - (base_net - base_opp),
                    "identical_to_base": played.summary["log_sha256"] == row["base_sha"],
                    "blind_unit_rounds": shim.blind_unit_rounds,
                    "one_blind_rounds": shim.one_blind_rounds,
                    "both_blind_rounds": shim.both_blind_rounds,
                    "reallocated_rounds": shim.reallocated_rounds,
                    "reallocated_to_u0": shim.reallocated_to_u0,
                    "reallocated_to_u1": shim.reallocated_to_u1,
                    "extension_steps_used": shim.extension_steps_used,
                    "extension_steps_effective": shim.extension_steps_effective,
                    "donor_was_blind": shim.donor_was_blind,
                    "replan_targets": shim.replan_targets,
                    "replan_target_outside_start_window":
                        shim.replan_target_outside_start_window,
                    "replan_blind": shim.replan_blind,
                    "lut_widen_used": shim.lut_widen_used,
                    "lut_widen_refused": shim.lut_widen_refused,
                    "blind_override_rounds": shim.blind_override_rounds,
                    "replica_match_rate": shim.replica_match / max(1, shim.replica_total),
                    "anchor_default_mismatch": shim.anchor_default_mismatch,
                    "k_histogram": {str(k): v for k, v in sorted(shim.k_histogram.items())},
                    "donor_planned_moves": {
                        str(k): v for k, v in sorted(shim.donor_planned_moves.items())},
                    "planned_moves_when_blind": {
                        str(k): v for k, v in sorted(shim.planned_moves_when_blind.items())},
                    "drift": _drift_stats(played.log_bytes, steady_from),
                }
                shim.close()
            records.append(row)

    out: dict[str, Any] = {
        "map": map_name, "opponent": opponent, "field": field,
        "seeds": [str(s) for s in seeds],
        "base_log_sha": [row["base_sha"] for row in records],
        "arms": [spec.name for spec in specs],
        "arm_specs": {
            spec.name: {
                "family": spec.family, "anchors": [list(a) for a in spec.anchors],
                "producer_budget": spec.producer_budget, "blind_tail": spec.blind_tail,
                "k_when_producer_is_u0": spec.producer_budget,
                "k_when_producer_is_u1": 6 - spec.producer_budget,
            }
            for spec in specs
        },
        "records": records, "aggregate": {},
    }
    for label in ("we_first", "we_second"):
        subset = [row for row in records if row["arm_order"] == label]
        cell: dict[str, Any] = {
            "games": len(subset),
            "base_net": summary([row["base_net"] for row in subset]),
            "base_mean_end_cheb": _mean([row["base_drift"]["mean_end_cheb"] for row in subset]),
        }
        for spec in specs:
            name = spec.name
            cell[name] = {
                "delta": summary([row[name]["delta"] for row in subset]),
                "margin_delta": summary([row[name]["margin_delta"] for row in subset]),
                "net": summary([row[name]["net"] for row in subset]),
                "reallocated_rounds": summary([row[name]["reallocated_rounds"] for row in subset]),
                "reallocated_to_u0": summary([row[name]["reallocated_to_u0"] for row in subset]),
                "reallocated_to_u1": summary([row[name]["reallocated_to_u1"] for row in subset]),
                "extension_steps_effective": summary(
                    [row[name]["extension_steps_effective"] for row in subset]),
                "donor_was_blind": summary(
                    [row[name]["donor_was_blind"] for row in subset]),
                "replan_target_outside_start_window": summary(
                    [row[name]["replan_target_outside_start_window"] for row in subset]),
                "replan_targets": summary([row[name]["replan_targets"] for row in subset]),
                "blind_unit_rounds": summary([row[name]["blind_unit_rounds"] for row in subset]),
                "mean_end_cheb": _mean([row[name]["drift"]["mean_end_cheb"] for row in subset]),
                "mean_end_cheb_delta": _mean([
                    row[name]["drift"]["mean_end_cheb"] - row["base_drift"]["mean_end_cheb"]
                    for row in subset]),
                "drift_delta": summary([
                    row[name]["drift"]["mean_end_cheb"] - row["base_drift"]["mean_end_cheb"]
                    for row in subset]),
                "share_end_d_ge_4_delta": _mean([
                    row[name]["drift"]["share_end_d_ge_4"] - row["base_drift"]["share_end_d_ge_4"]
                    for row in subset]),
                "identical_to_base": all(row[name]["identical_to_base"] for row in subset),
                "replica_match_rate": _mean([row[name]["replica_match_rate"] for row in subset]),
                "anchor_default_mismatch": sum(
                    row[name]["anchor_default_mismatch"] for row in subset),
                "k_histogram": _merge_hist([row[name]["k_histogram"] for row in subset]),
                "donor_planned_moves": _merge_hist(
                    [row[name]["donor_planned_moves"] for row in subset]),
                "donor_rounds_per_game": _mean([
                    sum(row[name]["donor_planned_moves"].values()) for row in subset]),
                "donor_idle_rounds_per_game": _mean([
                    row[name]["donor_planned_moves"].get("0", 0) for row in subset]),
            }
        out["aggregate"][label] = cell
    pooled: dict[str, Any] = {"games": len(records)}
    for spec in specs:
        name = spec.name
        pooled[name] = {
            "delta": summary([row[name]["delta"] for row in records]),
            "margin_delta": summary([row[name]["margin_delta"] for row in records]),
            "drift_delta": summary([
                row[name]["drift"]["mean_end_cheb"] - row["base_drift"]["mean_end_cheb"]
                for row in records]),
            "same_sign_across_order_arms": _same_sign(records, name),
        }
    out["aggregate"]["pooled"] = pooled
    return out


def _merge_hist(items: Sequence[Mapping[str, Any]]) -> Mapping[str, float]:
    total: collections.Counter = collections.Counter()
    for item in items:
        for key, value in item.items():
            total[key] += value
    grand = sum(total.values()) or 1
    return {key: total[key] / grand for key in sorted(total)}


def _same_sign(records: Sequence[Mapping[str, Any]], name: str) -> bool | None:
    first = [row[name]["delta"] for row in records if row["arm_order"] == "we_first"]
    second = [row[name]["delta"] for row in records if row["arm_order"] == "we_second"]
    if not first or not second:
        return None
    a, b = statistics.fmean(first), statistics.fmean(second)
    if a == 0 or b == 0:
        return None
    return (a > 0) == (b > 0)


def _drift_stats(log_bytes: bytes, steady_from: int) -> Mapping[str, Any]:
    ends: list[int] = []
    per_unit: dict[int, list[int]] = {0: [], 1: []}
    for number, payload in _unit_series(log_bytes):
        if number < steady_from:
            continue
        for unit in (0, 1):
            distance = cheb(payload[1]["end"][unit])
            ends.append(distance)
            per_unit[unit].append(distance)
    n = max(1, len(ends))
    histogram = collections.Counter(ends)
    return {
        "unit_rounds": len(ends),
        "mean_end_cheb": _mean([float(v) for v in ends]),
        "mean_end_cheb_u0": _mean([float(v) for v in per_unit[0]]),
        "mean_end_cheb_u1": _mean([float(v) for v in per_unit[1]]),
        "share_end_d_le_1": sum(1 for v in ends if v <= 1) / n,
        "share_end_d_le_2": sum(1 for v in ends if v <= 2) / n,
        "share_end_d_ge_4": sum(1 for v in ends if v >= 4) / n,
        "end_cheb_histogram": {str(k): v / n for k, v in sorted(histogram.items())},
    }


# ---------------------------------------------------------------------------
# fidelity gates
# ---------------------------------------------------------------------------


def verify(map_name: str, base_so: Path, seeds: Sequence[str]) -> Mapping[str, Any]:
    """Arm A through the full apparatus must be byte-identical to the baseline."""
    rows = load_map(map_name).rows
    walls = frozenset(
        r * GRID + c for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    )
    cases: list[dict[str, Any]] = []
    parity = _parity_check()
    for seed in seeds:
        for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
            plain = run_game(
                base_so, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            shim = BudgetStrategy(base_so, ARMS_BY_NAME["A_current"], walls=walls)
            through = run_game(
                shim, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            cases.append({
                "seed": str(seed), "arm": label,
                "plain_sha": plain.summary["log_sha256"],
                "arm_a_sha": through.summary["log_sha256"],
                "sha_equal": plain.summary["log_sha256"] == through.summary["log_sha256"],
                "plain_net": int(plain.summary["players"]["1"]["net_gold"]),
                "arm_a_net": int(through.summary["players"]["1"]["net_gold"]),
                "replica_match_rate": shim.replica_match / max(1, shim.replica_total),
                "anchor_default_mismatch": shim.anchor_default_mismatch,
                "k_histogram": {str(k): v for k, v in sorted(shim.k_histogram.items())},
                "blind_unit_rounds": shim.blind_unit_rounds,
                "one_blind_rounds": shim.one_blind_rounds,
                "both_blind_rounds": shim.both_blind_rounds,
            })
            shim.close()
    # a k != 3 arm must actually change the split, and stay legal
    legality: list[dict[str, Any]] = []
    for name in ("C_k4_truncate", "C_k6_truncate"):
        shim = BudgetStrategy(base_so, ARMS_BY_NAME[name], walls=walls)
        played = run_game(
            shim, base_so, map_source=map_name, seed=str(seeds[0]), dispatch="fixed",
            fixed_costs=COSTS_WE_FIRST, player1_name=name, player2_name="opponent",
        )
        legality.append({
            "arm": name,
            "status": played.summary["status"],
            "k_histogram": {str(k): v for k, v in sorted(shim.k_histogram.items())},
            "reallocated_rounds": shim.reallocated_rounds,
            "reallocated_to_u0": shim.reallocated_to_u0,
            "reallocated_to_u1": shim.reallocated_to_u1,
            "extension_steps_effective": shim.extension_steps_effective,
        })
        shim.close()
    return {
        "map": map_name, "seeds": [str(s) for s in seeds],
        "cases": cases,
        "k_legality": legality,
        "parity": parity,
        "gates": {
            "arm_a_sha_equals_baseline_all": all(c["sha_equal"] for c in cases),
            "no_default_anchor_mismatch": all(c["anchor_default_mismatch"] == 0 for c in cases),
            "replica_match_rate_min": min(c["replica_match_rate"] for c in cases),
            "k_arms_change_the_split": all(
                len(item["k_histogram"]) > 1 for item in legality),
            "k_arms_exercise_both_directions": all(
                item["reallocated_to_u0"] > 0 and item["reallocated_to_u1"] > 0
                for item in legality),
            "all_games_ok": all(item["status"] == "ok" for item in legality),
        },
    }


def _parity_check() -> Mapping[str, Any]:
    """Prove the parity statement the design rests on, by enumeration."""
    import itertools

    def endpoint(actions):
        row = col = 0
        for action in actions:
            row += DR[action]
            col += DC[action]
        return row, col

    out: dict[str, Any] = {}
    for budget in (3, 4, 5, 6):
        returns_with_moves = collections.Counter()
        for actions in itertools.product(range(5), repeat=budget):
            moves = sum(1 for a in actions if a != STAY)
            if endpoint(actions) == (0, 0):
                returns_with_moves[moves] += 1
        distinct_and_returns = 0
        for actions in itertools.product(range(4), repeat=budget):   # all real moves
            row = col = 0
            seen = set()
            for action in actions:
                row += DR[action]
                col += DC[action]
                seen.add((row, col))
            if (row, col) == (0, 0) and len(seen) >= 3:
                distinct_and_returns += 1
        out[str(budget)] = {
            "returning_sequences_by_real_move_count":
                {str(k): v for k, v in sorted(returns_with_moves.items())},
            "all_real_move_sequences_that_visit_3plus_distinct_and_return":
                distinct_and_returns,
        }
    out["statement"] = (
        "stay does not flip (row+col) parity; only a real move does.  So an ODD "
        "number of real moves can never return to the origin, which is why "
        "visiting three distinct cells in three real moves cannot end where it "
        "began -- that is what sank fold_tour.  With an EVEN real-move budget a "
        "unit can visit extra cells AND return, so the argument does not carry "
        "to a 4- or 6-move budget."
    )
    return out


# ---------------------------------------------------------------------------
# step-index pickup attribution -- the decisive red-flag test
# ---------------------------------------------------------------------------
#
# The pre-registered red flag is "value must saturate at k = 4-5 because the
# 5x5 scan makes Manhattan 4 the farthest targetable cell".  The decisive test
# is to ask the engine, not the replica, *which action slot* the gold arrived
# on.  ``run_attributed`` is ``sim.runner.run_game`` re-driven step by step so
# that ``RoundResult.movements`` and ``RoundResult.pickups`` -- both ordered
# event lists the engine already emits -- can be zipped back onto the six
# action slots.  ``stepattr`` gates itself by rebuilding the official log bytes
# and comparing ``log_sha256`` with the plain ``run_game`` of the same setup, so
# the attribution provably describes the same game the A/B measured.


def _align_pickups(result: Any) -> list[int]:
    """Amount picked up by each ``MovementEvent``, in engine execution order.

    ``pickups`` is a strict subsequence of the moved ``movements`` in the same
    append order (both are appended inside ``execute_action``), so a single
    forward pointer is exact.  A cell that yielded nothing on entry cannot yield
    anything later in the same round -- generation happens only at round start --
    so no false match is possible.  The caller cross-checks every per-unit total
    against the engine's own ``UnitState.pickup``.
    """
    amounts: list[tuple[int, int, int]] = []
    pointer = 0
    picks = result.pickups
    for event in result.movements:
        amount = before = remaining = 0
        if event.moved and pointer < len(picks):
            candidate = picks[pointer]
            if (candidate.actor_id == event.actor_id
                    and candidate.unit_index == event.unit_index
                    and candidate.position.cell == event.destination.cell):
                amount = int(candidate.amount)
                before = int(candidate.before)
                remaining = int(candidate.remaining)
                pointer += 1
        amounts.append((amount, before, remaining))
    if pointer != len(picks):
        raise AssertionError(
            "pickup alignment failed: consumed %d of %d" % (pointer, len(picks)))
    return amounts


def run_attributed(p1: Any, p2: Any, *, map_name: str, seed: str,
                   costs: Sequence[int], player1_name: str | None = None,
                   player2_name: str | None = None) -> Mapping[str, Any]:
    """``run_game`` with per-action-slot pickup attribution for seat 1."""
    import sim.runner as _runner                                     # noqa: PLC0415
    from sim.engine import GameEngine, GameMap                        # noqa: PLC0415
    ScenarioGenerator = _runner.ScenarioGenerator                     # honours install_field
    from sim.runner import (                                          # noqa: PLC0415
        ROUND_COUNT, _dispatch_costs, _fixed_cost_pair, _json_bytes, _npc_order,
        _npc_policy, _open_strategy, _spawn_state, _summary, round_log_record,
        strategy_name,
    )

    costs_pair = _fixed_cost_pair(tuple(int(c) for c in costs))
    definition = load_map(map_name)
    scenario = ScenarioGenerator(definition, seed)
    engine = GameEngine(GameMap.from_definition(definition))
    names = {
        1: player1_name or strategy_name(p1, "p1"),
        2: player2_name or strategy_name(p2, "p2"),
    }
    strategy1, own1 = _open_strategy(p1, fallback_name=names[1])
    strategy2, own2 = _open_strategy(p2, fallback_name=names[2])
    lines = [
        _json_bytes({"player1": names[1], "player2": names[2]}),
        _json_bytes([list(row) for row in definition.rows]),
    ]
    rounds: list[dict[str, Any]] = []
    try:
        for number in range(ROUND_COUNT):
            events = scenario.resolve_round(number, _spawn_state(engine.state))
            start = engine.begin_round(events.gold_additions, events.bomb_refresh)
            calls = {
                pid: strat.decide(engine.player_input(pid, start), measured=False)
                for pid, strat in ((1, strategy1), (2, strategy2))
            }
            decision = calls[1].decision
            result = engine.execute_round(
                {1: calls[1].decision, 2: calls[2].decision},
                _npc_policy(scenario.digest, number),
                player_costs=_dispatch_costs("fixed", calls, costs_pair),
                npc_order=_npc_order(engine.npc_ids, scenario.digest, number),
            )
            lines.append(_json_bytes(round_log_record(result)))

            amounts = _align_pickups(result)
            k, order = int(decision.k), int(decision.order)
            assigned = {0: 6 - (6 - k), 1: 6 - k}
            assigned[0] = k
            # seat 1's movements, in the engine's own (order, 1 - order) sequence
            mine = [
                (event, trio) for event, trio in zip(result.movements, amounts)
                if event.actor_id == 1
            ]
            if len(mine) != 6:
                raise AssertionError("seat 1 must emit exactly 6 actions")
            slots: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
            cursor = 0
            for unit in (order, 1 - order):
                for index in range(assigned[unit]):
                    event, (amount, before, remaining) = mine[cursor]
                    if int(event.unit_index) != unit:
                        raise AssertionError("slot/unit mismatch in attribution")
                    slots[unit].append({
                        "index": index,
                        "requested": int(event.requested_action),
                        "moved": bool(event.moved),
                        "blocked_by": event.blocked_by,
                        "cell": list(event.destination.cell),
                        "from": list(event.origin.cell),
                        "amount": amount,
                        "before": before,
                        "remaining": remaining,
                    })
                    cursor += 1
            engine_pickup = {
                int(u.index): int(u.pickup)
                for u in result.state.player(1).units
            }
            for unit in (0, 1):
                mine_total = sum(s["amount"] for s in slots[unit])
                if mine_total != engine_pickup[unit]:
                    raise AssertionError(
                        "attribution total %d != engine pickup %d (round %d unit %d)"
                        % (mine_total, engine_pickup[unit], number, unit))
            rounds.append({
                "round": number, "k": k, "order": order,
                "budget": [assigned[0], assigned[1]],
                "slots": slots,
            })
    finally:
        if own2:
            strategy2.close()
        if own1:
            strategy1.close()
    log_bytes = b"\n".join(lines) + b"\n"
    return {
        "summary": _summary(
            scenario=scenario, player_names=names, dispatch="fixed",
            fixed_costs=costs_pair, engine=engine, log_bytes=log_bytes,
        ),
        "rounds": rounds,
    }


def _fold_attribution(rounds: Sequence[Mapping[str, Any]], *,
                      steady_from: int) -> Mapping[str, Any]:
    """Pickup by action-slot index, split by whether the round reallocated."""
    by_index: collections.Counter = collections.Counter()
    moved_by_index: collections.Counter = collections.Counter()
    slots_by_index: collections.Counter = collections.Counter()
    baseline_by_index: collections.Counter = collections.Counter()
    baseline_slots: collections.Counter = collections.Counter()
    donor_pickup = 0
    donor_slots = 0
    producer_head = 0
    producer_tail = 0
    tail_slots = 0
    tail_moved = 0
    realloc_rounds = 0
    blocked_tail = 0
    # re-milking census: the engine leaves floor(0.35 v) behind, so a cell the
    # unit already entered this round is still worth 65% of its residue.  This
    # separates "the extra steps reach NEW ground" from "the extra steps
    # re-harvest the pile the first three steps already stood on".
    # ---- residue census (check 1: the mechanism variable, not the outcome) ----
    # ``before`` is the cell value on entry, ``remaining`` = floor(0.35 before) is
    # what the 65% rule leaves behind, i.e. the milkable pool this round.
    residue = {
        "head_entries": 0, "head_before": 0, "head_left": 0,
        "tail_entries": 0, "tail_before": 0, "tail_left": 0,
        "base_entries": 0, "base_before": 0, "base_left": 0,
    }
    revisit = {
        "head_pickup_new": 0, "head_pickup_revisit": 0,
        "tail_pickup_new": 0, "tail_pickup_revisit": 0,
        "tail_moves_new": 0, "tail_moves_revisit": 0,
        "base_pickup_new": 0, "base_pickup_revisit": 0,
        "base_moves_new": 0, "base_moves_revisit": 0,
    }
    for row in rounds:
        if int(row["round"]) < steady_from:
            continue
        k = int(row["k"])
        budgets = [len(row["slots"]["0"] if "0" in row["slots"] else row["slots"][0]),
                   len(row["slots"]["1"] if "1" in row["slots"] else row["slots"][1])]
        get = (lambda u: row["slots"][str(u)]) if "0" in row["slots"] else (
            lambda u: row["slots"][u])
        if k == 3:
            for unit in (0, 1):
                cells = get(unit)
                if not cells:
                    continue
                seen: set[tuple[int, int]] = {tuple(cells[0]["from"])}
                for slot in cells:
                    baseline_by_index[slot["index"]] += slot["amount"]
                    baseline_slots[slot["index"]] += 1
                    if not slot["moved"]:
                        continue
                    cell = tuple(slot["cell"])
                    tag = "revisit" if cell in seen else "new"
                    revisit["base_pickup_%s" % tag] += slot["amount"]
                    revisit["base_moves_%s" % tag] += 1
                    residue["base_entries"] += 1
                    residue["base_before"] += int(slot.get("before", 0))
                    residue["base_left"] += int(slot.get("remaining", 0))
                    seen.add(cell)
            continue
        realloc_rounds += 1
        producer = 0 if budgets[0] > budgets[1] else 1
        donor = 1 - producer
        for slot in get(donor):
            donor_pickup += slot["amount"]
            donor_slots += 1
        cells = get(producer)
        seen = {tuple(cells[0]["from"])}
        for slot in cells:
            index = int(slot["index"])
            by_index[index] += slot["amount"]
            slots_by_index[index] += 1
            moved_by_index[index] += int(slot["moved"])
            phase = "head" if index < 3 else "tail"
            if index < 3:
                producer_head += slot["amount"]
            else:
                producer_tail += slot["amount"]
                tail_slots += 1
                tail_moved += int(slot["moved"])
                if not slot["moved"]:
                    blocked_tail += 1
            if not slot["moved"]:
                continue
            cell = tuple(slot["cell"])
            tag = "revisit" if cell in seen else "new"
            revisit["%s_pickup_%s" % (phase, tag)] += slot["amount"]
            residue["%s_entries" % phase] += 1
            residue["%s_before" % phase] += int(slot.get("before", 0))
            residue["%s_left" % phase] += int(slot.get("remaining", 0))
            if phase == "tail":
                revisit["tail_moves_%s" % tag] += 1
            seen.add(cell)
    return {
        "realloc_rounds": realloc_rounds,
        "producer_pickup_by_slot_index": {str(i): by_index[i] for i in range(6)},
        "producer_slots_by_index": {str(i): slots_by_index[i] for i in range(6)},
        "producer_moved_by_index": {str(i): moved_by_index[i] for i in range(6)},
        "producer_head_pickup_steps_1_3": producer_head,
        "producer_tail_pickup_steps_4_6": producer_tail,
        "tail_slots": tail_slots,
        "tail_moved": tail_moved,
        "tail_blocked": blocked_tail,
        "donor_pickup": donor_pickup,
        "donor_slots": donor_slots,
        "revisit": revisit,
        "residue": residue,
        "nonrealloc_pickup_by_slot_index": {
            str(i): baseline_by_index[i] for i in range(3)},
        "nonrealloc_slots_by_index": {str(i): baseline_slots[i] for i in range(3)},
        "nonrealloc_pickup_total": sum(baseline_by_index.values()),
        "total_pickup": (sum(baseline_by_index.values()) + producer_head
                         + producer_tail + donor_pickup),
    }


def _per_round_unit_pickup(rounds: Sequence[Mapping[str, Any]]) -> dict[int, dict[int, int]]:
    """``{round: {unit: gold picked up that round}}`` from an attributed run."""
    out: dict[int, dict[int, int]] = {}
    for row in rounds:
        slots = row["slots"]
        get = (lambda u: slots[str(u)]) if "0" in slots else (lambda u: slots[u])
        out[int(row["round"])] = {
            unit: sum(int(s["amount"]) for s in get(unit)) for unit in (0, 1)
        }
    return out


def _per_round_tail(rounds: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, int]]:
    """``{round: {tail, head, donor, producer}}`` for the reallocated rounds."""
    out: dict[int, dict[str, int]] = {}
    for row in rounds:
        k = int(row["k"])
        if k == 3:
            continue
        slots = row["slots"]
        get = (lambda u: slots[str(u)]) if "0" in slots else (lambda u: slots[u])
        producer = 0 if len(get(0)) > len(get(1)) else 1
        out[int(row["round"])] = {
            "producer": producer,
            "head": sum(int(s["amount"]) for s in get(producer) if int(s["index"]) < 3),
            "tail": sum(int(s["amount"]) for s in get(producer) if int(s["index"]) >= 3),
            "donor": sum(int(s["amount"]) for s in get(1 - producer)),
        }
    return out


def _free_paid_split(base_rounds: Sequence[Mapping[str, Any]],
                     played_rounds: Sequence[Mapping[str, Any]],
                     shim_rows: Sequence[Mapping[str, Any]], *,
                     steady_from: int) -> Mapping[str, Any]:
    """Split the reallocation's *direct* effect into free and paid origins.

    ``free`` means the donor unit earned nothing in the paired baseline run on
    that round, so taking its budget cost nothing; ``paid`` means it did earn.
    Two independent classifiers are reported:

    ``realised``  the paired baseline's realised donor income on that round --
                  the true opportunity cost, but only an estimate, because the
                  two closed loops diverge after the first reallocation;
    ``planned``   the donor's own planned real-move count, which is the ONLY
                  classifier a shippable strategy could actually condition on,
                  because effective moves are not knowable before dispatch.
    """
    base_pickup = _per_round_unit_pickup(base_rounds)
    tails = _per_round_tail(played_rounds)
    realised = {"free": collections.Counter(), "paid": collections.Counter()}
    planned: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for record in shim_rows:
        if record.get("producer") is None:
            continue
        number = int(record["round"])
        if number < steady_from or number not in tails:
            continue
        donor = int(record["donor"])
        counterfactual = int(base_pickup.get(number, {}).get(donor, 0))
        row = tails[number]
        bucket = "free" if counterfactual == 0 else "paid"
        realised[bucket]["rounds"] += 1
        realised[bucket]["tail"] += row["tail"]
        realised[bucket]["donor_kept"] += row["donor"]
        realised[bucket]["counterfactual"] += counterfactual
        realised[bucket]["direct"] += row["tail"] + row["donor"] - counterfactual
        key = int(record["donor_planned_moves"])
        planned[key]["rounds"] += 1
        planned[key]["tail"] += row["tail"]
        planned[key]["donor_kept"] += row["donor"]
        planned[key]["counterfactual"] += counterfactual
        planned[key]["direct"] += row["tail"] + row["donor"] - counterfactual
    return {
        "realised": {name: dict(counter) for name, counter in realised.items()},
        "by_donor_planned_moves": {
            str(key): dict(planned[key]) for key in sorted(planned)},
    }


def stepattr(map_name: str, base_so: Path, seeds: Sequence[str], *,
             arms: Sequence[str], opponent: str = "self",
             steady_from: int = STEADY_FROM) -> Mapping[str, Any]:
    """Attribute every gold pickup to the action slot that collected it.

    For each seed and order arm this runs the plain baseline and every named arm
    through ``run_attributed``, checks the reconstructed ``log_sha256`` against
    ``run_game``, and folds pickup by slot index.  The headline quantity is
    ``producer_tail_pickup_steps_4_6``: the gold the *extension* steps actually
    collected, to be set beside the arm's own ``margin_delta``.
    """
    rows = load_map(map_name).rows
    walls = frozenset(
        r * GRID + c for r, line in enumerate(rows)
        for c, cell in enumerate(line) if str(cell) == "1"
    )
    specs = [_extra_arm(name) for name in arms]
    records: list[dict[str, Any]] = []
    for seed in seeds:
        for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
            reference = run_game(
                base_so, base_so if opponent == "self" else ProbeObs(),
                map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name=opponent,
            )
            base = run_attributed(
                base_so, base_so if opponent == "self" else ProbeObs(),
                map_name=map_name, seed=str(seed), costs=costs,
                player1_name="base", player2_name=opponent,
            )
            base_net = int(base["summary"]["players"]["1"]["net_gold"])
            base_opp = int(base["summary"]["players"]["2"]["net_gold"])
            row: dict[str, Any] = {
                "seed": str(seed), "arm_order": label,
                "base_net": base_net, "base_opp_net": base_opp,
                "base_margin": base_net - base_opp,
                "attribution_gate_sha_equal":
                    base["summary"]["log_sha256"] == reference.summary["log_sha256"],
                "base_attribution": _fold_attribution(
                    base["rounds"], steady_from=steady_from),
            }
            for spec in specs:
                shim = BudgetStrategy(
                    base_so, spec, walls=walls, steady_from=steady_from,
                    salt=_salt(seed), record=True,
                )
                played = run_attributed(
                    shim, base_so if opponent == "self" else ProbeObs(),
                    map_name=map_name, seed=str(seed), costs=costs,
                    player1_name=spec.name, player2_name=opponent,
                )
                net = int(played["summary"]["players"]["1"]["net_gold"])
                opp = int(played["summary"]["players"]["2"]["net_gold"])
                fold = _fold_attribution(played["rounds"], steady_from=steady_from)
                split = _free_paid_split(
                    base["rounds"], played["rounds"], shim.rows,
                    steady_from=steady_from)
                donor_planned = collections.Counter()
                donor_blind = 0
                fired = 0
                for record in shim.rows:
                    if record.get("producer") is None:
                        continue
                    fired += 1
                    donor_planned[int(record["donor_planned_moves"])] += 1
                    donor_blind += int(bool(record["donor_was_blind"]))
                row[spec.name] = {
                    "net": net, "opp_net": opp,
                    "delta": net - base_net,
                    "margin_delta": (net - opp) - (base_net - base_opp),
                    "attribution": fold,
                    "split": split,
                    "fired_rounds": fired,
                    "donor_was_blind": donor_blind,
                    "donor_planned_moves": {
                        str(key): donor_planned[key] for key in sorted(donor_planned)},
                    "replan_targets": shim.replan_targets,
                    "replan_target_outside_start_window":
                        shim.replan_target_outside_start_window,
                    "replan_blind": shim.replan_blind,
                    "lut_widen_used": shim.lut_widen_used,
                    "lut_widen_refused": shim.lut_widen_refused,
                    "extension_steps_effective": shim.extension_steps_effective,
                }
                shim.close()
            records.append(row)

    out: dict[str, Any] = {
        "map": map_name, "opponent": opponent, "steady_from": steady_from,
        "seeds": [str(s) for s in seeds], "arms": [s.name for s in specs],
        "records": records, "aggregate": {},
    }
    out["gates"] = {
        "attribution_reproduces_baseline_log_sha_all":
            all(row["attribution_gate_sha_equal"] for row in records),
    }
    for label in ("we_first", "we_second", "pooled"):
        subset = records if label == "pooled" else [
            row for row in records if row["arm_order"] == label]
        cell: dict[str, Any] = {"games": len(subset)}
        for spec in specs:
            name = spec.name
            def pull(path, rows=subset, name=name):
                return [_dig(row[name], path) for row in rows]
            cell[name] = {
                "margin_delta": summary(pull("margin_delta")),
                "delta": summary(pull("delta")),
                "opp_delta": summary([
                    row[name]["opp_net"] - row["base_opp_net"] for row in subset]),
                "fired_rounds": summary(pull("fired_rounds")),
                "donor_was_blind": summary(pull("donor_was_blind")),
                "tail_pickup_steps_4_6": summary(
                    pull("attribution.producer_tail_pickup_steps_4_6")),
                "head_pickup_steps_1_3": summary(
                    pull("attribution.producer_head_pickup_steps_1_3")),
                "donor_pickup": summary(pull("attribution.donor_pickup")),
                "tail_slots": summary(pull("attribution.tail_slots")),
                "tail_moved": summary(pull("attribution.tail_moved")),
                "tail_blocked": summary(pull("attribution.tail_blocked")),
                "gold_per_moved_tail_step": _mean([
                    _dig(row[name], "attribution.producer_tail_pickup_steps_4_6")
                    / max(1, _dig(row[name], "attribution.tail_moved"))
                    for row in subset]),
                "pickup_by_slot_index": {
                    str(i): _mean([
                        _dig(row[name], "attribution.producer_pickup_by_slot_index")[str(i)]
                        for row in subset]) for i in range(6)},
                "moved_by_slot_index": {
                    str(i): _mean([
                        _dig(row[name], "attribution.producer_moved_by_index")[str(i)]
                        for row in subset]) for i in range(6)},
                "replan_targets": summary(pull("replan_targets")),
                "replan_target_outside_start_window": summary(
                    pull("replan_target_outside_start_window")),
                "replan_blind": summary(pull("replan_blind")),
                "extension_steps_effective": summary(pull("extension_steps_effective")),
                "donor_planned_moves": _merge_hist([
                    row[name]["donor_planned_moves"] for row in subset]),
                "total_pickup": summary(pull("attribution.total_pickup")),
                "residue": {
                    field: summary(pull("attribution.residue.%s" % field))
                    for field in ("head_entries", "head_before", "head_left",
                                  "tail_entries", "tail_before", "tail_left",
                                  "base_entries", "base_before", "base_left")
                },
                "revisit": {
                    field: summary(pull("attribution.revisit.%s" % field))
                    for field in ("head_pickup_new", "head_pickup_revisit",
                                  "tail_pickup_new", "tail_pickup_revisit",
                                  "tail_moves_new", "tail_moves_revisit")
                },
                "total_pickup_delta": summary([
                    _dig(row[name], "attribution.total_pickup")
                    - row["base_attribution"]["total_pickup"] for row in subset]),
                "free_paid": {
                    bucket: {
                        field: summary([
                            _dig(row[name], "split.realised")[bucket].get(field, 0)
                            for row in subset])
                        for field in ("rounds", "tail", "donor_kept",
                                      "counterfactual", "direct")
                    }
                    for bucket in ("free", "paid")
                },
                "by_donor_planned_moves": {
                    str(moves): {
                        field: _mean([
                            _dig(row[name], "split.by_donor_planned_moves")
                            .get(str(moves), {}).get(field, 0)
                            for row in subset])
                        for field in ("rounds", "tail", "counterfactual", "direct")
                    }
                    for moves in (0, 1, 2, 3)
                },
            }
        cell["base_pickup_by_slot_index"] = {
            str(i): _mean([
                row["base_attribution"]["nonrealloc_pickup_by_slot_index"][str(i)]
                for row in subset]) for i in range(3)}
        cell["base_total_pickup"] = summary(
            [row["base_attribution"]["total_pickup"] for row in subset])
        cell["base_residue"] = {
            field: summary([row["base_attribution"]["residue"][field] for row in subset])
            for field in ("base_entries", "base_before", "base_left")
        }
        cell["base_revisit"] = {
            field: summary([row["base_attribution"]["revisit"][field] for row in subset])
            for field in ("base_pickup_new", "base_pickup_revisit",
                          "base_moves_new", "base_moves_revisit")
        }
        out["aggregate"][label] = cell
    return out


def _dig(payload: Mapping[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# companion
# ---------------------------------------------------------------------------

ARTIFACTS = (
    ("ksemantics", "ksemantics.json"),
    ("geometry", "geometry.json"),
    ("blind", "blind_map1.json"),
    ("verify", "verify.json"),
    ("ab_selfplay_tune", "ab_self_tune.json"),
    ("ab_selfplay_oos", "ab_self_oos.json"),
    ("ab_probeobs_tune", "ab_probe_tune.json"),
    ("ab_probeobs_oos", "ab_probe_oos.json"),
    ("ab_armd", "ab_armd.json"),
)


def _pool(records, arm, key, order_arm=None):
    values = [
        row[arm][key] for row in records
        if arm in row and (order_arm is None or row["arm_order"] == order_arm)
    ]
    return summary(values)


def assemble(base_dir: Path) -> Mapping[str, Any]:
    out: dict[str, Any] = {
        "schema_version": 1,
        "subject": "positioning and step-budget reallocation, four arms, one apparatus",
        "baseline": {
            "commit": "f18064c",
            "source_sha256": "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
            "host_build": "clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include shim.h",
            "note": "guarded scalar fallback; AVX2 unavailable on the arm64 host",
        },
        "platform_games_consumed": 0,
        "artifacts": {},
        "missing": [],
    }
    for key, name in ARTIFACTS:
        path = base_dir / name
        if path.is_file():
            out["artifacts"][key] = json.loads(path.read_text())
        else:
            out["missing"].append(name)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seeds(text: str) -> list[str]:
    out: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            low, high = part.split(":", 1)
            out.extend(str(v) for v in range(int(low), int(high)))
        else:
            out.append(part)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ksemantics", "geometry", "blind", "anchorcells", "verify", "ab",
                 "stepattr", "assemble"):
        item = sub.add_parser(name)
        item.add_argument("--out", type=Path)
        if name in ("blind", "verify", "ab", "anchorcells", "stepattr"):
            item.add_argument("--base", type=Path, required=True)
            item.add_argument("--seeds", default="1000:1010")
        if name in ("geometry", "blind", "verify", "ab", "anchorcells", "stepattr"):
            item.add_argument("--map", default="map1")
        if name == "anchorcells":
            item.add_argument("--arm", default="A_current")
        if name in ("ab", "stepattr"):
            item.add_argument("--arms", default=",".join(s.name for s in ARM_TABLE))
            item.add_argument("--opponent", default="self", choices=("self", "probeobs"))
            item.add_argument("--field", default="uniform",
                              choices=("uniform", "centripetal"))
        if name == "assemble":
            item.add_argument("--artifacts", type=Path, default=Path("/tmp/gr_step"))
    args = parser.parse_args(argv)

    if args.command == "ksemantics":
        payload: Any = ksemantics()
    elif args.command == "geometry":
        payload = geometry(args.map)
    elif args.command == "blind":
        payload = blind(args.map, args.base, _seeds(args.seeds))
    elif args.command == "anchorcells":
        payload = anchorcells(args.map, args.base, _seeds(args.seeds), arm=args.arm)
    elif args.command == "verify":
        payload = verify(args.map, args.base, _seeds(args.seeds))
    elif args.command == "assemble":
        payload = assemble(args.artifacts)
    elif args.command == "stepattr":
        payload = stepattr(
            args.map, args.base, _seeds(args.seeds),
            arms=tuple(a for a in args.arms.split(",") if a),
            opponent=args.opponent,
        )
    else:
        payload = ab(
            args.map, args.base, _seeds(args.seeds),
            arms=tuple(a for a in args.arms.split(",") if a),
            opponent=args.opponent, field=args.field,
        )

    text = json.dumps(payload, indent=1, sort_keys=True, default=str)
    if getattr(args, "out", None):
        args.out.write_text(text)
        print("wrote %s (%d bytes)" % (args.out, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
