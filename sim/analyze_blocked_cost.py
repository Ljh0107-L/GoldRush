#!/usr/bin/env python3
"""Reprice the "blocked routing" lesion on map1 and adjudicate the 920 gold pool.

Context
=======

``src/CHANGELOG.md`` (lines 68-72) reports, for the frozen build ``f18064c``:

* normal-route blocked rate map1/map2/map3 = 37.3% / 24.5% / 36.7%;
* of map1's blocks, 82.6% wall and 17.4% the ``rich``-gated bomb bitmap;
* pickup on clear / wall-blocked / bomb-blocked unit-rounds = 3.048 / 0.064 / 0.030
  gold per unit-round;
* six central interior walls ``(7,7) (7,9) (9,7) (9,9) (8,6) (8,10)`` carry 62.9%
  of all map1 blocks (76.2% of wall blocks).

From those numbers a pool of "~3 gold lost per block, therefore ~920 gold/game"
was inferred.  This module tests that inference on four axes:

``derive``
    Pure arithmetic.  Reproduces the 920 figure from the published constants and
    names the denominator that makes it come out, then re-prices it with the
    denominator the source code actually implies.

``trace``
    Open-loop instrumented run of the frozen build.  For every unit-round it
    *reconstructs the decision from source semantics* -- the 5x5 ">=3 gold, ring
    priority" target rule, the three-way target ladder, the ``SL`` LUT triple,
    and the ``ok`` waypoint check against ``bpw | (rich & bombbit)`` -- and
    validates the reconstruction by requiring the reconstructed action triple to
    equal the triple the real ``moveDecision`` emitted (a per-round ``pair_diff``).
    It then prices the blocked class four ways:

    1. the raw conditional means (what the CHANGELOG measured);
    2. an overlap/positivity audit of the clear-vs-blocked contrast, which is the
       confound test: blocking is a *deterministic function* of
       ``(start cell, clamped target offset)`` once the wall table is locked, so
       the two arms share no common support and the raw gap is not a causal
       effect under any adjustment;
    3. the identified estimand instead: the best achievable three-step outcome
       from the same cell *with the wall in place*, minus what we actually did;
    4. the unphysical "magic wall removal" counterfactual, i.e. the number the
       920 claim implicitly wants, for completeness.

    Every gold figure is then stock/flow separated: extra gold is ``novel`` only
    if our own realized trajectory never re-enters that cell later in the game.

``realized``
    Closed-loop, same-seed paired.  Only unit-rounds the reconstruction marks as
    blocked get a substituted action; everything else is byte-identical
    passthrough.  Three repair variants bracket any conceivable mechanism:
    ``oracle3`` (perfect three-step freedom = upper bound), ``detour`` (reach the
    intended target by a detour = the safe side-step's mechanism at oracle
    quality), ``posfix`` (best three steps that end on the start cell = the
    position-preserving guard the path-harvest report asked for).

Information discipline is inherited unchanged from ``sim/analyze_path_oracle``:
the shim reads the seat's own already-fog-filtered ``PlayerInput``, so a fogged
gold value is the ``-5`` sentinel rather than a latent read, and
``fog_discipline`` asserts it.

Bias labels (``sim/README.md`` §7-8, §10)
    The local NPC model is over-greedy and over-central (39.18% per-action
    accuracy).  Its residual *over-estimates central competition* and
    *relatively over-estimates outer-ring routes*, so a repair that walks a unit
    off the central generation peak is measured **favourably** here: a local
    reading of ~0 is an upper bound, not a lower bound.  Absolute income is not
    platform-comparable; only same-seed paired deltas are.

Nothing here implements a strategy and nothing is submitted to the platform.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.analyze_path_oracle import (  # noqa: E402  (path bootstrap above)
    ACTIONS,
    BOMB,
    BOMB_WAVE,
    DC,
    DR,
    GRID,
    NCELL,
    VISION_RADIUS,
    WALL,
    _sim,
    _visible_mask,
    classify,
    extract_state,
    fog_discipline,
    harvest_map,
    joint_best,
    summary,
    walls_from_map,
)

STAY = 4

# ---------------------------------------------------------------------------
# published constants under test (src/CHANGELOG.md lines 68-72), re-read here so
# every number that enters a conclusion is sourced (军规 27)
# ---------------------------------------------------------------------------

PUBLISHED = {
    "source": "src/CHANGELOG.md lines 68-72 (frozen build f18064c trace)",
    "blocked_rate": {"map1": 0.373, "map2": 0.245, "map3": 0.367},
    "map1_cause_share": {"wall": 0.826, "bomb": 0.174},
    "pickup_gold_per_unit_round": {"clear": 3.048, "wall_blocked": 0.064, "bomb_blocked": 0.030},
    "central_wall_share_of_all_blocks": 0.629,
    "central_wall_share_of_wall_blocks": 0.762,
    "central_walls": [(7, 7), (7, 9), (9, 7), (9, 9), (8, 6), (8, 10)],
    "side_step_recovery": {"blocked_decisions": 960, "recovered": 427, "fallback_after": 0.207},
    "claimed_pool_gold_per_game": 920.0,
}

# 1 instruction = 0.1454 ns (src/INFRA.md §1) x 11 gold/ns inside the +-20ns
# crossover band (src/INFRA.md §2.5) = 1.5994 gold/instruction, an *average*.
GOLD_PER_INSTRUCTION = 0.1454 * 11.0

ANCHORS = ((6, 8), (11, 8))          # player.cpp:372  anch_r[u] = 6 + 5u, anch_c = 8

# ---------------------------------------------------------------------------
# exact mirrors of the frozen build's constexpr tables
# ---------------------------------------------------------------------------

# player.cpp:121-125  pext ring-distance remap -> priority rank per 5x5 window slot
_RM = (7, 11, 13, 17, 2, 6, 8, 10, 14, 16, 18, 22, 1, 3, 5, 9, 15, 19, 21, 23, 0, 4, 20, 24, 12)
PRIO = [255] * 25
for _rank, _slot in enumerate(_RM):
    PRIO[_slot] = _rank


def _build_slut() -> tuple[dict, dict, dict]:
    """Mirror of ``struct SLut`` (player.cpp:188-220).

    ``fact`` = the emitted action triple (early-arrival fold pre-folded);
    ``pdr``/``pdc`` = cumulative displacement after each step, which is what the
    ``ok`` waypoint check reads.
    """
    fact: dict[tuple[int, int], tuple[int, int, int]] = {}
    pdr: dict[tuple[int, int], tuple[int, int, int]] = {}
    pdc: dict[tuple[int, int], tuple[int, int, int]] = {}
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            row = col = 0
            acts: list[int] = []
            crow: list[int] = []
            ccol: list[int] = []
            for _step in range(3):
                rest_r, rest_c = dr - row, dc - col
                adr, adc = abs(rest_r), abs(rest_c)
                action = STAY
                if adr or adc:
                    if adr >= adc:
                        action = 1 if rest_r > 0 else 0
                        row += 1 if rest_r > 0 else -1
                    else:
                        action = 3 if rest_c > 0 else 2
                        col += 1 if rest_c > 0 else -1
                acts.append(action)
                crow.append(row)
                ccol.append(col)
            dist = abs(dr) + abs(dc)
            if 0 < dist < 3:
                acts[dist] = acts[dist - 1] ^ 1
                if dist == 1:
                    acts[2] = acts[1] ^ 1
            fact[(dr, dc)] = tuple(acts)
            pdr[(dr, dc)] = tuple(crow)
            pdc[(dr, dc)] = tuple(ccol)
    return fact, pdr, pdc


SL_FACT, SL_PDR, SL_PDC = _build_slut()


def _clamp3(value: int) -> int:
    return -3 if value < -3 else (3 if value > 3 else value)


# ---------------------------------------------------------------------------
# source-faithful decision reconstruction
# ---------------------------------------------------------------------------


class PlayerBlockView:
    """The frozen build's own ``blk`` bitmap: walls/boundary plus, when the unit
    holds >=100 gold, every bomb remembered inside the current 20-round wave.

    ``bombbit`` is *not* per-round: ``waveTick`` (player.cpp:240-243) memsets it
    only on ``round % 20 == 0``, and each unit's scan ORs its own 5x5 window into
    it *before* that unit's ``blk`` is composed, so unit 1 sees unit 0's finds.
    """

    def __init__(self, walls: frozenset[tuple[int, int]]) -> None:
        self.walls = walls
        self.bombs: set[tuple[int, int]] = set()

    def wave_tick(self) -> None:
        self.bombs.clear()

    def scan(self, grid: Sequence[Sequence[int]], srow: int, scol: int) -> None:
        for row in range(srow - 2, srow + 3):
            if not 0 <= row < GRID:
                continue
            line = grid[row]
            for col in range(scol - 2, scol + 3):
                if 0 <= col < GRID and line[col] == BOMB:
                    self.bombs.add((row, col))

    def cause(self, row: int, col: int, rich: bool) -> str | None:
        """None when passable; else the reason the frozen build sees."""
        if not (0 <= row < GRID and 0 <= col < GRID):
            return "bounds"
        if (row, col) in self.walls:
            return "wall"
        if rich and (row, col) in self.bombs:
            return "bomb"
        return None

    def passable(self, row: int, col: int, rich: bool) -> bool:
        return self.cause(row, col, rich) is None


def escape_step(view: PlayerBlockView, row: int, col: int, prow: int, pcol: int, rich: bool) -> int:
    """Mirror of ``escapeStep`` (player.cpp:156-169)."""
    mask = 0
    for action in range(4):
        if view.passable(row + DR[action], col + DC[action], rich):
            mask |= 1 << action
    back = 0
    for action in range(4):
        if (row + DR[action], col + DC[action]) == (prow, pcol):
            back |= 1 << action
    candidates = (mask & ~back) | 16
    action = (candidates & -candidates).bit_length() - 1
    return -1 if action == 4 else action


def steer_step(
    view: PlayerBlockView, row: int, col: int, grow: int, gcol: int,
    prow: int, pcol: int, rich: bool,
) -> int:
    """Mirror of ``steerStep`` (player.cpp:171-184)."""
    drr, dcc = grow - row, gcol - col
    axis_row = 1 if drr > 0 else 0
    axis_col = 2 + (1 if dcc > 0 else 0)
    adr, adc = abs(drr), abs(dcc)
    row_first = adr >= adc
    primary = axis_row if row_first else axis_col
    secondary = axis_col if row_first else axis_row
    ok0 = view.passable(row + DR[primary], col + DC[primary], rich)
    ok1 = view.passable(row + DR[secondary], col + DC[secondary], rich) and adr != 0 and adc != 0
    if ok0 or ok1:
        return primary if ok0 else secondary
    if adr or adc:
        return escape_step(view, row, col, prow, pcol, rich)
    return -1


class UnitDecision:
    """Everything the reconstruction knows about one unit-round."""

    __slots__ = (
        "unit", "cell", "rich", "target_mode", "target", "d", "branch", "triple",
        "waypoints", "waypoint_causes", "primary_cause", "blocked_cells", "supply3",
        "target_value", "standing_value",
    )

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def blocked(self) -> bool:
        return self.branch in ("fallback_step", "fallback_stay")

    def as_json(self) -> Mapping[str, Any]:
        return {
            "unit": self.unit, "cell": list(self.cell), "rich": self.rich,
            "target_mode": self.target_mode, "target": list(self.target), "d": self.d,
            "branch": self.branch, "triple": list(self.triple),
            "primary_cause": self.primary_cause,
            "blocked_cells": [list(cell) for cell in self.blocked_cells],
            "supply3": self.supply3, "target_value": self.target_value,
            "standing_value": self.standing_value,
        }


def reconstruct_unit(
    grid: Sequence[Sequence[int]],
    unit: int,
    cell: tuple[int, int],
    held: int,
    view: PlayerBlockView,
    previous: tuple[int, int],
) -> UnitDecision:
    """Reproduce ``decide``'s per-unit branch (player.cpp:397-521) exactly.

    Order of operations matters and is preserved: scan (which feeds ``bombbit``)
    -> three-way target ladder -> ``blk`` composition -> LUT or fallback.
    """
    srow, scol = cell
    rich = held >= 100
    view.scan(grid, srow, scol)

    best_rank = 255
    target: tuple[int, int] | None = None
    supply3 = 0
    for i in range(5):
        row = srow - 2 + i
        if not 0 <= row < GRID:
            continue
        line = grid[row]
        for j in range(5):
            col = scol - 2 + j
            if not 0 <= col < GRID:
                continue
            if line[col] > 2:
                supply3 += 1
                rank = PRIO[i * 5 + j]
                if rank < best_rank:
                    best_rank, target = rank, (row, col)

    standing_value = int(grid[srow][scol]) if grid[srow][scol] > 0 else 0
    if target is not None:
        target_mode = "has"
    elif standing_value > 1:
        target_mode, target = "standing", (srow, scol)
    else:
        target_mode, target = "blind", ANCHORS[unit]
    target_value = int(grid[target[0]][target[1]]) if grid[target[0]][target[1]] > 0 else 0

    dr0 = _clamp3(target[0] - srow)
    dc0 = _clamp3(target[1] - scol)
    dist = abs(dr0) + abs(dc0)

    waypoints: list[tuple[int, int]] = []
    causes: list[str | None] = []
    if dist == 0:
        mask = 0
        for action in range(4):
            if view.passable(srow + DR[action], scol + DC[action], rich):
                mask |= 1 << action
        if mask:
            action = (mask & -mask).bit_length() - 1
            triple = (action, action ^ 1, STAY)
            branch = "fold"
        else:
            triple = (STAY, STAY, STAY)
            branch = "fold_stay"
    else:
        offsets = SL_PDR[(dr0, dc0)], SL_PDC[(dr0, dc0)]
        waypoints = [(srow + offsets[0][i], scol + offsets[1][i]) for i in range(3)]
        causes = [view.cause(row, col, rich) for row, col in waypoints]
        if not any(causes):
            triple = SL_FACT[(dr0, dc0)]
            branch = "lut"
        else:
            action = steer_step(view, srow, scol, target[0], target[1], previous[0], previous[1], rich)
            if action >= 0:
                triple = (action, STAY, STAY)
                branch = "fallback_step"
            else:
                triple = (STAY, STAY, STAY)
                branch = "fallback_stay"

    primary = next((cause for cause in causes if cause), None)
    blocked_cells = [waypoints[i] for i, cause in enumerate(causes) if cause]
    return UnitDecision(
        unit=unit, cell=cell, rich=rich, target_mode=target_mode, target=target,
        d=dist, branch=branch, triple=triple, waypoints=tuple(waypoints),
        waypoint_causes=tuple(causes), primary_cause=primary,
        blocked_cells=tuple(dict.fromkeys(blocked_cells)), supply3=supply3,
        target_value=target_value, standing_value=standing_value,
    )


# ---------------------------------------------------------------------------
# per-round oracle searches (reuse of the fidelity-checked substrate)
# ---------------------------------------------------------------------------


def _teammate_blocker(state, base: Sequence[Sequence[int]], unit: int) -> int:
    """Cell that blocks ``unit`` under whole-unit serial settlement."""
    other = 1 - unit
    if unit == state.order:
        return state.starts[other]
    result = _sim(
        base[other], state.starts[other], state.starts[unit], state.board,
        state.blocked, state.bombs, state.npc3, state.held[other],
    )
    return result[3]


def _pinned_search(state, base, unit: int, seqs: Sequence[Sequence[int]]):
    pair: list[Any] = [None, None]
    pair[unit] = tuple(seqs)
    pair[1 - unit] = (tuple(base[1 - unit]),)
    if not pair[unit]:
        return None
    return joint_best(
        pair, state.starts, state.held, state.order, state.board,
        state.blocked, state.bombs, state.npc3,
    )


def _final_cells(state, base, unit: int) -> dict[tuple[int, ...], int]:
    """Final cell of every three-step sequence for ``unit`` under the exact blocker."""
    blocker = _teammate_blocker(state, base, unit)
    out = {}
    for seq in ACTIONS:
        out[seq] = _sim(
            seq, state.starts[unit], blocker, state.board, state.blocked,
            state.bombs, state.npc3, state.held[unit],
        )[3]
    return out


# ---------------------------------------------------------------------------
# measurement shim
# ---------------------------------------------------------------------------

REPAIRS = ("none", "oracle3", "oracle3_ge3", "detour", "posfix")


class BlockedCostShim:
    """Seat-1 wrapper: reconstruct the branch, price the blocked class, and by
    default return the base decision verbatim so the trajectory cannot drift."""

    name = "blocked_cost"

    def __init__(
        self,
        map_name: str,
        walls: set[tuple[int, int]],
        base_so: Path,
        *,
        repair: str = "none",
        steady_from: int = 8,
        check_fog_every: int = 50,
        price_clear: bool = True,
    ) -> None:
        from sim.abi import SharedObjectStrategy

        if repair not in REPAIRS:
            raise ValueError("repair must be one of %s" % ", ".join(REPAIRS))
        self.map_name = map_name
        self.wall_cells = frozenset(walls)
        self.static_walls = frozenset(row * GRID + col for row, col in walls)
        self.base = SharedObjectStrategy(base_so, name="blocked_base")
        self.repair = repair
        self.steady_from = steady_from
        self.check_fog_every = check_fog_every
        self.price_clear = price_clear

        self.view = PlayerBlockView(self.wall_cells)
        self.model_bombs: set[int] = set()
        self.previous: list[tuple[int, int]] = [(-1, -1), (-1, -1)]
        self.last_round = 10 ** 9

        self.rows: list[dict[str, Any]] = []
        self.mismatch: list[dict[str, Any]] = []
        self.rounds = 0
        self.substitutions = 0
        # stock/flow bookkeeping: per-round extra-by-cell keyed by class, plus the
        # base's own realized entries so a later re-entry can be detected.
        self.extra_history: list[tuple[int, dict[str, dict[int, int]]]] = []
        self.entered_history: list[frozenset[int]] = []
        self.target_history: list[list[tuple[str, tuple[int, int], int]]] = []

    def close(self) -> None:
        self.base.close()

    # -- helpers ---------------------------------------------------------
    def _repair_seqs(self, state, base_pair, unit: int, decision: UnitDecision, finals):
        if self.repair == "oracle3":
            return ACTIONS
        if self.repair == "oracle3_ge3":
            # Same freedom, but the unit may not ENTER a cell holding 1-2 gold.
            # The selector's `v > 2` threshold (player.cpp:408/454) means those
            # cells are invisible to the live build; forbidding them isolates how
            # much of `oracle3`'s gain is really sub-threshold pickup rather than
            # anything to do with the wall.
            low = frozenset(
                cell for cell in range(NCELL) if 0 < state.board[cell] <= 2
            )
            if not low:
                return ACTIONS
            blocker = _teammate_blocker(state, base_pair, unit)
            keep = []
            for seq in ACTIONS:
                entered = _sim(
                    seq, state.starts[unit], blocker, state.board, state.blocked,
                    state.bombs, state.npc3, state.held[unit],
                )[5]
                if not any(cell in low for cell in entered):
                    keep.append(seq)
            return tuple(keep)
        if self.repair == "detour":
            goal = decision.target[0] * GRID + decision.target[1]
        else:                                     # posfix: end where you started
            goal = state.starts[unit]
        return tuple(seq for seq, final in finals.items() if final == goal)

    # -- strategy call ---------------------------------------------------
    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.view = PlayerBlockView(self.wall_cells)
            self.model_bombs.clear()
            self.previous = [(-1, -1), (-1, -1)]
        if round_number % BOMB_WAVE == 0:         # player.cpp:376 waveTick
            self.view.wave_tick()
            self.model_bombs.clear()
        self.last_round = round_number

        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        order = int(decision.order)
        passthrough = actions + (int(decision.k), order, int(decision.vp))

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        my_gold = [int(item) for item in value.my_units_gold]
        enemies = [
            (int(pos.row), int(pos.col)) for pos in value.visible_enemies
            if pos is not None and int(pos.row) >= 0
        ]
        npcs = [(int(pos.row), int(pos.col)) for _npc_id, pos in value.visible_npcs]

        # physical bomb memory for the harvest model (same discipline as
        # analyze_path_oracle: purge a remembered bomb the moment a visible cell
        # proves it gone).
        if self.model_bombs:
            mask = _visible_mask([r * GRID + c for r, c in my_units], VISION_RADIUS)
            self.model_bombs.difference_update([
                cell for cell in self.model_bombs
                if mask[cell] and grid[cell // GRID][cell % GRID] != BOMB
            ])

        decisions = [
            reconstruct_unit(grid, unit, my_units[unit], my_gold[unit], self.view, self.previous[unit])
            for unit in (0, 1)
        ]
        self.previous = list(my_units)

        state = extract_state(
            grid, my_units, my_gold, enemies, npcs, order,
            self.static_walls, set(self.model_bombs),
        )
        for row in range(GRID):
            for col in range(GRID):
                if grid[row][col] == BOMB:
                    self.model_bombs.add(row * GRID + col)
        if self.check_fog_every and round_number % self.check_fog_every == 0:
            fog_discipline(state)

        if int(decision.k) != 3:
            return passthrough

        base_pair = (actions[:3], actions[3:6])
        for unit in (0, 1):
            if tuple(decisions[unit].triple) != tuple(base_pair[unit]):
                self.mismatch.append({
                    "round": round_number, "unit": unit,
                    "emitted": list(base_pair[unit]),
                    "reconstructed": list(decisions[unit].triple),
                    "branch": decisions[unit].branch,
                    "target_mode": decisions[unit].target_mode,
                })

        self.rounds += 1
        steady = round_number >= self.steady_from
        exact = _pinned_search(state, base_pair, 0, (base_pair[0],))
        extra: dict[str, dict[int, int]] = {}
        targets: list[tuple[str, tuple[int, int], int]] = []
        serial = (base_pair[order], base_pair[1 - order])
        base_take = harvest_map(
            (exact["entered"][order], exact["entered"][1 - order]), state.board,
        )
        substitute: dict[int, tuple[int, int, int]] = {}

        for unit in (0, 1):
            decided = decisions[unit]
            klass = self._class_of(decided)
            row: dict[str, Any] = {
                "round": round_number, "steady": steady, "unit": unit,
                "cell": list(decided.cell), "rich": decided.rich,
                "target_mode": decided.target_mode, "target": list(decided.target),
                "target_value": decided.target_value, "standing_value": decided.standing_value,
                "supply3": decided.supply3, "d": decided.d, "branch": decided.branch,
                "class": klass, "primary_cause": decided.primary_cause,
                "blocked_cells": [list(cell) for cell in decided.blocked_cells],
                "pattern": classify(base_pair[unit]),
                "requested": list(base_pair[unit]),
                "base_pickup": exact["unit_pickup"][unit],
                "base_net": exact["unit_net"][unit],
                "base_moves": len(exact["entered"][unit]),
                "base_final": exact["entered"][unit][-1] if exact["entered"][unit] else state.starts[unit],
            }
            price = decided.blocked or self.price_clear
            if price:
                free = _pinned_search(state, base_pair, unit, ACTIONS)
                row["free_net"] = free["unit_net"][unit]
                row["free_pickup"] = free["unit_pickup"][unit]
                row["free_gain"] = free["unit_net"][unit] - exact["unit_net"][unit]
                # the search maximises the JOINT net, so the per-unit figure can
                # dip when a unit yields a cell to its teammate.  The joint gain
                # is the well-defined non-negative value of freeing this unit.
                row["free_joint_gain"] = free["net"] - exact["net"]
                row["free_actions"] = list(free["actions"][unit])
                take = harvest_map(
                    (free["entered"][order], free["entered"][1 - order]), state.board,
                )
                gained = {
                    cell: amount - base_take.get(cell, 0)
                    for cell, amount in take.items()
                    if amount - base_take.get(cell, 0) > 0
                }
                # Attribution: how much of the oracle's extra gold comes from cells
                # the live selector cannot even see?  `v > 2` is the threshold at
                # player.cpp:408 (AVX) / :454 (scalar), so cells holding 1-2 gold are
                # invisible to target selection.  If the extra is mostly
                # sub-threshold, the gain belongs to the picky-threshold line, not
                # to wall routing.
                row["free_extra_lowval"] = sum(
                    amount for cell, amount in gained.items() if 0 < state.board[cell] <= 2
                )
                row["free_extra_ge3"] = sum(
                    amount for cell, amount in gained.items() if state.board[cell] > 2
                )
                if gained:
                    extra.setdefault(klass, {})
                    for cell, amount in gained.items():
                        extra[klass][cell] = extra[klass].get(cell, 0) + amount
            if decided.blocked:
                row["magic_gain"] = self._magic_gain(state, base_pair, unit, decided, exact)
                row["wall_in_blocked_set"] = "wall" in decided.waypoint_causes
                row["bomb_in_blocked_set"] = "bomb" in decided.waypoint_causes
                # Arrival test: can *any* legal three-step sequence put this unit
                # on the cell it selected?  71% of wall blocks sit at d>=3, where
                # a wall detour needs >=5 steps, so the answer is structurally no
                # and the safe side-step could only buy motion, never arrival.
                finals = _final_cells(state, base_pair, unit)
                goal = decided.target[0] * GRID + decided.target[1]
                reaching = tuple(seq for seq, final in finals.items() if final == goal)
                row["target_reachable_in_round"] = bool(reaching)
                if reaching:
                    reach = _pinned_search(state, base_pair, unit, reaching)
                    row["reach_joint_gain"] = reach["net"] - exact["net"]
                    row["reach_pickup_delta"] = reach["pickup"] - exact["pickup"]
                    row["reach_burn_delta"] = reach["burned"] - exact["burned"]
                else:
                    row["reach_joint_gain"] = 0.0
                    row["reach_pickup_delta"] = 0.0
                    row["reach_burn_delta"] = 0.0
                back = tuple(
                    seq for seq, final in finals.items() if final == state.starts[unit]
                )
                if back:
                    hold = _pinned_search(state, base_pair, unit, back)
                    row["posfix_joint_gain"] = hold["net"] - exact["net"]
                else:
                    row["posfix_joint_gain"] = 0.0
                if decided.target_mode == "has":
                    targets.append((klass, decided.target, decided.target_value))
                if self.repair != "none" and steady:
                    # Substitute only inside the steady window.  Rounds < steady_from
                    # are the mode==1 opening (fingerprint lock + BFS march, which
                    # `slowMove` can overwrite), where the reconstruction is not
                    # authoritative and hijacking the march would confound the
                    # closed-loop delta with a bad opening.
                    seqs = self._repair_seqs(state, base_pair, unit, decided, finals)
                    picked = _pinned_search(state, base_pair, unit, seqs)
                    if picked is not None:
                        chosen = tuple(picked["actions"][unit])
                        if chosen != tuple(base_pair[unit]):
                            substitute[unit] = chosen
            self.rows.append(row)

        self.extra_history.append((round_number, extra))
        self.entered_history.append(frozenset(
            cell for side in exact["entered"] for cell in side
        ))
        self.target_history.append(targets)

        if not substitute:
            return passthrough
        self.substitutions += len(substitute)
        pair = [list(base_pair[0]), list(base_pair[1])]
        for unit, triple in substitute.items():
            pair[unit] = list(triple)
        return tuple(pair[0]) + tuple(pair[1]) + (3, order, int(decision.vp))

    # -- classification --------------------------------------------------
    @staticmethod
    def _class_of(decided: UnitDecision) -> str:
        if not decided.blocked:
            return "fold" if decided.d == 0 else "clear"
        if decided.primary_cause == "wall":
            return "wall_blocked"
        if decided.primary_cause == "bomb":
            return "bomb_blocked"
        return "bounds_blocked"

    def _magic_gain(self, state, base_pair, unit: int, decided: UnitDecision, exact) -> float:
        """Unphysical counterfactual: run the LUT triple the unit *wanted*, with
        the blocking cells treated as passable.  This is the quantity the
        "3 gold per block" claim implicitly prices; walls do not actually move."""
        magic = bytearray(state.blocked)
        for row, col in decided.blocked_cells:
            if 0 <= row < GRID and 0 <= col < GRID:
                magic[row * GRID + col] = 0
        dr0 = _clamp3(decided.target[0] - decided.cell[0])
        dc0 = _clamp3(decided.target[1] - decided.cell[1])
        wanted = SL_FACT[(dr0, dc0)]
        blocker = _teammate_blocker(state, base_pair, unit)
        result = _sim(
            wanted, state.starts[unit], blocker, state.board, magic,
            state.bombs, state.npc3, state.held[unit],
        )
        return result[0] - exact["unit_net"][unit]

    # -- aggregation -----------------------------------------------------
    def stock_flow(self) -> Mapping[str, Any]:
        """Split each class's extra gold into ``novel`` (our own realized
        trajectory never re-enters that cell later) and ``timing``."""
        suffix: set[int] = set()
        out: dict[str, dict[str, float]] = {}
        for index in range(len(self.extra_history) - 1, -1, -1):
            _round, per_class = self.extra_history[index]
            for klass, cells in per_class.items():
                bucket = out.setdefault(klass, {"novel": 0.0, "timing": 0.0, "novel_events": 0, "timing_events": 0})
                for cell, amount in cells.items():
                    if cell in suffix:
                        bucket["timing"] += amount
                        bucket["timing_events"] += 1
                    else:
                        bucket["novel"] += amount
                        bucket["novel_events"] += 1
            suffix |= self.entered_history[index]
        for bucket in out.values():
            total = bucket["novel"] + bucket["timing"]
            bucket["novel_share"] = bucket["novel"] / total if total else None
        return out

    def block_runs(self, *, steady_from: int = 8) -> Mapping[str, Any]:
        """Self-heal latency: how many rounds in a row does a unit stay blocked?

        The fallback still takes one ``steerStep`` toward the target, so a block
        is a 1-step-per-round crawl rather than a stop.  Run lengths therefore
        price the block as *delay*, which is timing gold by construction.
        """
        runs: list[int] = []
        for unit in (0, 1):
            series = [
                (row["round"], row["class"].endswith("_blocked"))
                for row in self.rows if row["unit"] == unit and row["round"] >= steady_from
            ]
            series.sort()
            current = 0
            for _round, blocked in series:
                if blocked:
                    current += 1
                elif current:
                    runs.append(current)
                    current = 0
            if current:
                runs.append(current)
        hist = collections.Counter(runs)
        return {
            "runs": len(runs),
            "blocked_unit_rounds": sum(runs),
            "mean_run_length": statistics.fmean(runs) if runs else None,
            "median_run_length": statistics.median(runs) if runs else None,
            "p90_run_length": (
                sorted(runs)[min(len(runs) - 1, int(0.9 * len(runs)))] if runs else None),
            "max_run_length": max(runs) if runs else None,
            "run_length_hist": {str(key): value for key, value in sorted(hist.items())},
            "share_of_runs_length_1": hist[1] / len(runs) if runs else None,
        }

    def target_fate(self, horizons: Sequence[int] = (1, 2, 3, 5, 10, 500)) -> Mapping[str, Any]:
        """For every blocked unit-round with a real gold target, does our own
        realized trajectory enter that target cell within ``h`` later rounds?"""
        entered = self.entered_history
        out: dict[str, dict[str, Any]] = {}
        for index, targets in enumerate(self.target_history):
            for klass, target, value in targets:
                cell = target[0] * GRID + target[1]
                bucket = out.setdefault(klass, {
                    "occurrences": 0, "target_value_sum": 0,
                    **{"within_%d" % h: 0 for h in horizons},
                })
                bucket["occurrences"] += 1
                bucket["target_value_sum"] += value
                for horizon in horizons:
                    stop = min(len(entered), index + 1 + horizon)
                    if any(cell in entered[step] for step in range(index + 1, stop)):
                        bucket["within_%d" % horizon] += 1
        for bucket in out.values():
            for key in list(bucket):
                if key.startswith("within_"):
                    bucket[key + "_rate"] = bucket[key] / bucket["occurrences"] if bucket["occurrences"] else None
        return out


# ---------------------------------------------------------------------------
# aggregation of a traced game
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _class_table(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        groups[row["class"]].append(row)
    out = {}
    for klass, items in sorted(groups.items()):
        out[klass] = {
            "unit_rounds": len(items),
            "pickup_per_unit_round": _mean([item["base_pickup"] for item in items]),
            "net_per_unit_round": _mean([item["base_net"] for item in items]),
            "zero_pickup_rate": _mean([1.0 if item["base_pickup"] == 0 else 0.0 for item in items]),
            "moves_per_unit_round": _mean([item["base_moves"] for item in items]),
            "free_gain_per_unit_round": _mean([item["free_gain"] for item in items if "free_gain" in item]),
            "free_joint_gain_per_unit_round": _mean(
                [item["free_joint_gain"] for item in items if "free_joint_gain" in item]),
            "free_joint_gain_zero_share": _mean(
                [1.0 if item["free_joint_gain"] <= 0 else 0.0
                 for item in items if "free_joint_gain" in item]),
            "free_net_per_unit_round": _mean([item["free_net"] for item in items if "free_net" in item]),
            "free_extra_lowval_per_unit_round": _mean(
                [item["free_extra_lowval"] for item in items if "free_extra_lowval" in item]),
            "free_extra_ge3_per_unit_round": _mean(
                [item["free_extra_ge3"] for item in items if "free_extra_ge3" in item]),
            "free_extra_lowval_share": (
                sum(item.get("free_extra_lowval", 0) for item in items)
                / max(1e-9, sum(item.get("free_extra_lowval", 0) + item.get("free_extra_ge3", 0)
                                for item in items))
            ),
            "magic_gain_per_unit_round": _mean(
                [item["magic_gain"] for item in items if "magic_gain" in item]),
            "target_reachable_rate": _mean(
                [1.0 if item["target_reachable_in_round"] else 0.0
                 for item in items if "target_reachable_in_round" in item]),
            "reach_joint_gain_per_unit_round": _mean(
                [item["reach_joint_gain"] for item in items if "reach_joint_gain" in item]),
            "reach_pickup_delta_per_unit_round": _mean(
                [item["reach_pickup_delta"] for item in items if "reach_pickup_delta" in item]),
            "reach_burn_delta_per_unit_round": _mean(
                [item["reach_burn_delta"] for item in items if "reach_burn_delta" in item]),
            "reach_joint_gain_given_reachable": _mean(
                [item["reach_joint_gain"] for item in items
                 if item.get("target_reachable_in_round")]),
            "reach_burn_delta_given_reachable": _mean(
                [item["reach_burn_delta"] for item in items
                 if item.get("target_reachable_in_round")]),
            "reach_pickup_delta_given_reachable": _mean(
                [item["reach_pickup_delta"] for item in items
                 if item.get("target_reachable_in_round")]),
            "posfix_joint_gain_per_unit_round": _mean(
                [item["posfix_joint_gain"] for item in items if "posfix_joint_gain" in item]),
            "target_mode_share": {
                mode: sum(1 for item in items if item["target_mode"] == mode) / len(items)
                for mode in ("has", "standing", "blind")
            },
            "d_hist": {
                str(key): value for key, value in
                sorted(collections.Counter(item["d"] for item in items).items())
            },
            "mean_target_value": _mean([item["target_value"] for item in items]),
            "mean_supply3": _mean([item["supply3"] for item in items]),
            "mean_d": _mean([item["d"] for item in items]),
        }
    return out


def _overlap_audit(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Positivity / common-support audit of the clear-vs-blocked contrast.

    Covariate ``X = (start cell, clamped target offset, rich)`` fully determines
    the frozen build's ``ok`` check once the wall table is locked.  If
    ``P(blocked | X)`` is 0 or 1 for (almost) every stratum, the two arms share no
    common support and no adjustment can identify a "cost of being blocked".
    """
    routed = [row for row in rows if row["class"] in ("clear", "wall_blocked", "bomb_blocked", "bounds_blocked")]
    strata: dict[tuple, list[int]] = collections.defaultdict(list)
    strata_norich: dict[tuple, list[int]] = collections.defaultdict(list)
    for row in routed:
        cell = tuple(row["cell"])
        offset = (
            _clamp3(row["target"][0] - cell[0]),
            _clamp3(row["target"][1] - cell[1]),
        )
        blocked = 0 if row["class"] == "clear" else 1
        strata[(cell, offset, row["rich"])].append(blocked)
        strata_norich[(cell, offset)].append(blocked)

    def audit(table: Mapping[tuple, Sequence[int]]) -> Mapping[str, Any]:
        deterministic = mixed = 0
        det_rounds = mixed_rounds = 0
        for values in table.values():
            total = sum(values)
            if total == 0 or total == len(values):
                deterministic += 1
                det_rounds += len(values)
            else:
                mixed += 1
                mixed_rounds += len(values)
        return {
            "strata": len(table),
            "deterministic_strata": deterministic,
            "mixed_strata": mixed,
            "unit_rounds": det_rounds + mixed_rounds,
            "unit_rounds_in_deterministic_strata": det_rounds,
            "unit_rounds_in_mixed_strata": mixed_rounds,
            "overlap_share": mixed_rounds / (det_rounds + mixed_rounds) if det_rounds + mixed_rounds else None,
        }

    wall_only = [row for row in routed if row["class"] in ("clear", "wall_blocked")]
    wall_strata: dict[tuple, list[int]] = collections.defaultdict(list)
    for row in wall_only:
        cell = tuple(row["cell"])
        offset = (
            _clamp3(row["target"][0] - cell[0]),
            _clamp3(row["target"][1] - cell[1]),
        )
        wall_strata[(cell, offset)].append(0 if row["class"] == "clear" else 1)
    return {
        "covariate": "(start cell, clamped target offset, rich)",
        "with_rich": audit(strata),
        "without_rich": audit(strata_norich),
        "wall_vs_clear_only": audit(wall_strata),
    }


def _matched_contrast(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> Mapping[str, Any]:
    """Stratified clear-vs-wall-blocked pickup contrast on coarse covariates.

    This is the design the raw 3.048-vs-0.064 comparison should have used.  It is
    reported *and* labelled: it removes the confound it can see, while the
    overlap audit shows the residual confound it cannot.
    """
    def key_of(row: Mapping[str, Any]) -> tuple:
        out = []
        for key in keys:
            if key == "target_value_bucket":
                value = row["target_value"]
                out.append(0 if value == 0 else (1 if value <= 4 else (2 if value <= 9 else 3)))
            elif key == "supply_bucket":
                out.append(min(int(row["supply3"]), 3))
            else:
                out.append(row[key])
        return tuple(out)

    clear: dict[tuple, list[float]] = collections.defaultdict(list)
    blocked: dict[tuple, list[float]] = collections.defaultdict(list)
    for row in rows:
        if row["class"] == "clear":
            clear[key_of(row)].append(row["base_pickup"])
        elif row["class"] == "wall_blocked":
            blocked[key_of(row)].append(row["base_pickup"])
    common = sorted(set(clear) & set(blocked))
    weight_total = 0
    weighted = 0.0
    per_stratum = []
    for key in common:
        weight = len(blocked[key])
        gap = _mean(clear[key]) - _mean(blocked[key])
        weighted += weight * gap
        weight_total += weight
        per_stratum.append({
            "stratum": list(key), "clear_n": len(clear[key]), "blocked_n": weight,
            "clear_pickup": _mean(clear[key]), "blocked_pickup": _mean(blocked[key]),
            "gap": gap,
        })
    matched_blocked = sum(len(blocked[key]) for key in common)
    all_blocked = sum(len(values) for values in blocked.values())
    return {
        "keys": list(keys),
        "strata_clear": len(clear),
        "strata_blocked": len(blocked),
        "strata_common": len(common),
        "blocked_unit_rounds": all_blocked,
        "blocked_unit_rounds_matched": matched_blocked,
        "matched_share": matched_blocked / all_blocked if all_blocked else None,
        "weighted_gap": weighted / weight_total if weight_total else None,
        "raw_gap": (
            _mean([value for values in clear.values() for value in values])
            - _mean([value for values in blocked.values() for value in values])
        ) if clear and blocked else None,
        "per_stratum_top": sorted(per_stratum, key=lambda item: -item["blocked_n"])[:12],
    }


def summarize_game(shim: BlockedCostShim, *, steady_only: bool = True) -> Mapping[str, Any]:
    rows = [row for row in shim.rows if row["steady"]] if steady_only else list(shim.rows)
    routed = [row for row in rows if row["class"] != "fold"]
    blocked = [row for row in rows if row["class"].endswith("_blocked")]
    wall_blocked = [row for row in rows if row["class"] == "wall_blocked"]
    per_cell = collections.Counter()
    for row in blocked:
        for cell in row["blocked_cells"]:
            per_cell[tuple(cell)] += 1
    primary_cell = collections.Counter()
    for row in blocked:
        if row["blocked_cells"]:
            primary_cell[tuple(row["blocked_cells"][0])] += 1
    central = {tuple(cell) for cell in PUBLISHED["central_walls"]}
    central_primary = sum(count for cell, count in primary_cell.items() if cell in central)

    unit_rounds = len(rows)
    scale = (2 * 500) / unit_rounds if unit_rounds else 0.0
    clear_pickup = _mean([row["base_pickup"] for row in rows if row["class"] == "clear"]) or 0.0
    wall_pickup = _mean([row["base_pickup"] for row in rows if row["class"] == "wall_blocked"]) or 0.0
    stock = shim.stock_flow()
    wall_stock = stock.get("wall_blocked", {})
    # hit-rate axis: a wall-blocked unit-round is "convertible" when the base
    # scored nothing and perfect three-step play would have scored something.
    convertible = [
        row for row in wall_blocked
        if row["base_pickup"] == 0 and row.get("free_joint_gain", 0) > 0
    ]
    convertible_all = [
        row for row in blocked
        if row["base_pickup"] == 0 and row.get("free_joint_gain", 0) > 0
    ]
    hit_axis = {
        "base_hit_rate": _mean([1.0 if row["base_pickup"] > 0 else 0.0 for row in rows]),
        "wall_blocked_convertible_unit_rounds": len(convertible),
        "wall_blocked_convertible_pp_of_hit_rate": 100.0 * len(convertible) / unit_rounds
        if unit_rounds else None,
        "all_blocked_convertible_pp_of_hit_rate": 100.0 * len(convertible_all) / unit_rounds
        if unit_rounds else None,
    }
    pool = {
        "steady_unit_rounds_measured": unit_rounds,
        "scale_to_1000_unit_rounds": scale,
        "wall_blocked_per_game": len(wall_blocked) * scale,
        "blocked_per_game": len(blocked) * scale,
        # (i) the published arithmetic, reproduced on local numbers
        "published_style_pool_gold_per_game": len(wall_blocked) * scale * (clear_pickup - wall_pickup),
        # (ii) unphysical wall-removal counterfactual: run the plan we wanted
        "magic_wall_removal_gold_per_game":
            sum(row.get("magic_gain", 0.0) for row in wall_blocked) * scale,
        # (iii) the identified estimand: best achievable three steps WITH the wall
        "achievable_repair_gold_per_game":
            sum(row.get("free_joint_gain", 0.0) for row in wall_blocked) * scale,
        "achievable_repair_all_blocked_gold_per_game":
            sum(row.get("free_joint_gain", 0.0) for row in blocked) * scale,
        # (iii-b) restricted mechanisms
        "target_detour_gold_per_game":
            sum(row.get("reach_joint_gain", 0.0) for row in wall_blocked) * scale,
        "position_preserving_gold_per_game":
            sum(row.get("posfix_joint_gain", 0.0) for row in wall_blocked) * scale,
        # (iv) stock/flow split of the achievable pool
        "novel_gold_per_game": wall_stock.get("novel", 0.0) * scale,
        "timing_gold_per_game": wall_stock.get("timing", 0.0) * scale,
        "novel_share": wall_stock.get("novel_share"),
    }
    return {
        "unit_rounds": unit_rounds,
        "pool": pool,
        "hit_rate_axis": hit_axis,
        "rounds": shim.rounds,
        "reconstruction_mismatches": len(shim.mismatch),
        "reconstruction_mismatch_rounds": sorted({item["round"] for item in shim.mismatch})[:20],
        "substitutions": shim.substitutions,
        "branch_counts": dict(collections.Counter(row["branch"] for row in rows)),
        "pattern_counts": dict(collections.Counter(row["pattern"] for row in rows)),
        "class_counts": dict(collections.Counter(row["class"] for row in rows)),
        "blocked_rate_all_unit_rounds": len(blocked) / unit_rounds if unit_rounds else None,
        "blocked_rate_routed_unit_rounds": len(blocked) / len(routed) if routed else None,
        "fold_share": 1.0 - (len(routed) / unit_rounds) if unit_rounds else None,
        "cause_share_of_blocks": {
            cause: sum(1 for row in blocked if row["primary_cause"] == cause) / len(blocked)
            for cause in ("wall", "bomb", "bounds")
        } if blocked else None,
        "cause_share_wall_dominant": {
            "any_wall_waypoint": sum(1 for row in blocked if row.get("wall_in_blocked_set")) / len(blocked),
            "any_bomb_waypoint": sum(1 for row in blocked if row.get("bomb_in_blocked_set")) / len(blocked),
        } if blocked else None,
        "class_table": _class_table(rows),
        "central_wall_share_of_all_blocks": central_primary / len(blocked) if blocked else None,
        "central_wall_share_of_wall_blocks": (
            sum(count for cell, count in primary_cell.items() if cell in central) / len(wall_blocked)
            if wall_blocked else None
        ),
        "top_blocking_cells": [
            {"cell": list(cell), "primary_blocks": count} for cell, count in primary_cell.most_common(12)
        ],
        "overlap_audit": _overlap_audit(rows),
        "matched_contrast": _matched_contrast(
            rows, ("target_mode", "d", "target_value_bucket", "supply_bucket"),
        ),
        "matched_contrast_position": _matched_contrast(rows, ("target_mode", "d")),
        "magic_gain_per_wall_block": _mean([row["magic_gain"] for row in wall_blocked if "magic_gain" in row]),
        "magic_gain_total_wall": sum(row.get("magic_gain", 0.0) for row in wall_blocked),
        "free_gain_total_by_class": {
            klass: sum(row["free_gain"] for row in rows if row["class"] == klass and "free_gain" in row)
            for klass in sorted({row["class"] for row in rows})
        },
        "stock_flow_by_class": stock,
        "block_runs": shim.block_runs(steady_from=shim.steady_from),
        "target_fate": shim.target_fate(),
        "engine_blocks": None,          # filled by the caller from the log
    }


# ---------------------------------------------------------------------------
# engine-truth cross-check from the log
# ---------------------------------------------------------------------------


def engine_truth(log_bytes: bytes, seat: int = 1) -> Mapping[str, Any]:
    """Per-round engine truth for ``seat``: effective actions and pickup.

    ``end.units[].actions`` are the *effective* actions the engine settled, so a
    request the engine refused shows up as a shorter/altered list.  This is the
    only place a genuine engine-level block can be counted.
    """
    lines = log_bytes.decode().splitlines()
    rounds = []
    for line in lines[2:]:
        record = json.loads(line)
        if "end" not in record:
            continue
        players = {int(item["id"]): item for item in record["end"]["players"]}
        entry = players.get(seat)
        if entry is None:
            continue
        rounds.append({
            "round": int(record["round"]),
            "units": [
                {
                    "position": list(unit["position"]),
                    "actions": list(unit["actions"]),
                    "pickup": int(unit["pickup"]),
                    "gold": int(unit["gold"]),
                }
                for unit in entry["units"]
            ],
            "start": [
                list(unit["position"])
                for unit in {int(item["id"]): item for item in record["start"]["players"]}[seat]["units"]
            ],
        })
    return {"rounds": rounds}


def engine_block_census(
    truth: Mapping[str, Any], requested: Mapping[tuple[int, int], Sequence[int]]
) -> Mapping[str, Any]:
    """Count unit-rounds where the engine refused a step we actually requested.

    ``end.units[].actions`` is the engine's *effective* action list, positionally
    aligned with the request, so a refusal is exactly
    ``requested[i] != 4 and effective[i] == 4``.  The frozen build checks walls
    and (when rich) bombs itself, so a refusal here can only be a bounds, own
    teammate or *enemy* collision -- and in local self-play both seats anchor on
    the same ``(6,8)/(11,8)`` cells, which inflates the enemy-collision arm.
    """
    total = refused = refused_steps = 0
    per_step = collections.Counter()
    examples = []
    for entry in truth["rounds"]:
        for unit, observed in enumerate(entry["units"]):
            key = (entry["round"], unit)
            want = requested.get(key)
            if want is None:
                continue
            total += 1
            effective = list(observed["actions"])
            hits = [
                index for index in range(min(len(effective), len(want)))
                if want[index] != STAY and effective[index] == STAY
            ]
            if hits:
                refused += 1
                refused_steps += len(hits)
                for index in hits:
                    per_step[index] += 1
                if len(examples) < 8:
                    examples.append({"round": entry["round"], "unit": unit,
                                     "requested": list(want), "effective": effective})
    return {
        "unit_rounds_compared": total,
        "unit_rounds_with_engine_refusal": refused,
        "engine_refusal_rate": refused / total if total else None,
        "refused_steps": refused_steps,
        "refused_steps_by_index": {str(key): value for key, value in sorted(per_step.items())},
        "note": "continuation semantics: a refused step is effective 4 and the "
                "remaining steps still execute (sim/engine.py:1050 and the "
                "blocked_step_continuation smoke check at :1359)",
        "examples": examples,
    }


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


def _static_walls(map_name: str) -> set[tuple[int, int]]:
    from sim.runner import load_map

    return walls_from_map(load_map(map_name).rows)


def run_trace(
    maps: Sequence[str], base_so: Path, seeds: Sequence[str], *, steady_from: int = 8,
) -> Mapping[str, Any]:
    from sim.runner import run_game

    out: dict[str, Any] = {"maps": {}}
    for map_name in maps:
        walls = _static_walls(map_name)
        per_seed = []
        pooled_rows: list[Mapping[str, Any]] = []
        for seed in seeds:
            shim = BlockedCostShim(map_name, walls, base_so, repair="none", steady_from=steady_from)
            measured = run_game(
                shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
                fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
            )
            plain = run_game(
                base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
                fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
            )
            record = dict(summarize_game(shim))
            record["seed"] = seed
            record["measured_net_gold"] = int(measured.summary["players"]["1"]["net_gold"])
            record["baseline_net_gold"] = int(plain.summary["players"]["1"]["net_gold"])
            record["trajectory_identical"] = (
                measured.summary["log_sha256"] == plain.summary["log_sha256"]
            )
            truth = engine_truth(measured.log_bytes, seat=1)
            record["engine_blocks"] = _engine_blocks_from_rows(shim, truth)
            model_pickup = sum(row["base_pickup"] for row in shim.rows)
            engine_pickup = sum(
                unit["pickup"] for entry in truth["rounds"] for unit in entry["units"]
            )
            record["model_vs_engine_pickup"] = {
                "model_sum": model_pickup, "engine_sum": engine_pickup,
                "exact_rounds": _pickup_agreement(shim, truth),
                "rounds": len(truth["rounds"]),
            }
            shim.close()
            per_seed.append(record)
            pooled_rows.extend(shim.rows)
        out["maps"][map_name] = {
            "seeds": list(seeds),
            "per_seed": [
                {key: value for key, value in record.items()
                 if key not in ("overlap_audit", "matched_contrast", "matched_contrast_position")}
                for record in per_seed
            ],
            "blocked_rate_all_unit_rounds": summary(
                [record["blocked_rate_all_unit_rounds"] for record in per_seed]),
            "blocked_rate_routed_unit_rounds": summary(
                [record["blocked_rate_routed_unit_rounds"] for record in per_seed]),
            "pool": {
                key: summary([record["pool"][key] for record in per_seed])
                for key in per_seed[0]["pool"]
                if all(record["pool"].get(key) is not None for record in per_seed)
            },
            "hit_rate_axis": {
                key: summary([record["hit_rate_axis"][key] for record in per_seed])
                for key in per_seed[0]["hit_rate_axis"]
                if all(record["hit_rate_axis"].get(key) is not None for record in per_seed)
            },
            "block_runs": {
                key: summary([record["block_runs"][key] for record in per_seed])
                for key in ("mean_run_length", "median_run_length", "p90_run_length",
                            "max_run_length", "runs", "share_of_runs_length_1")
                if all(record["block_runs"].get(key) is not None for record in per_seed)
            },
            "central_wall_share_of_wall_blocks": summary(
                [record["central_wall_share_of_wall_blocks"] for record in per_seed]),
            "central_wall_share_of_all_blocks": summary(
                [record["central_wall_share_of_all_blocks"] for record in per_seed]),
            "cause_share_of_blocks": {
                cause: summary([record["cause_share_of_blocks"][cause] for record in per_seed])
                for cause in ("wall", "bomb", "bounds")
            },
            "pooled": _pooled_summary(pooled_rows),
            "first_seed_overlap_audit": per_seed[0]["overlap_audit"],
            "first_seed_matched_contrast": per_seed[0]["matched_contrast"],
            "first_seed_matched_contrast_position": per_seed[0]["matched_contrast_position"],
            "reconstruction_mismatches_total": sum(
                record["reconstruction_mismatches"] for record in per_seed),
            "trajectory_identical_all": all(record["trajectory_identical"] for record in per_seed),
        }
    return out


def _engine_blocks_from_rows(shim: BlockedCostShim, truth: Mapping[str, Any]) -> Mapping[str, Any]:
    requested = {
        (row["round"], row["unit"]): tuple(row["requested"]) for row in shim.rows
        if "requested" in row
    }
    if not requested:
        return {"note": "requested triples not recorded"}
    return engine_block_census(truth, requested)


def _pickup_agreement(shim: BlockedCostShim, truth: Mapping[str, Any]) -> int:
    engine = {
        (entry["round"], unit): observed["pickup"]
        for entry in truth["rounds"] for unit, observed in enumerate(entry["units"])
    }
    agree = 0
    total = 0
    for row in shim.rows:
        key = (row["round"], row["unit"])
        if key in engine:
            total += 1
            agree += engine[key] == row["base_pickup"]
    return agree


def _pooled_summary(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    steady = [row for row in rows if row["steady"]]
    return {
        "unit_rounds": len(steady),
        "class_counts": dict(collections.Counter(row["class"] for row in steady)),
        "class_table": _class_table(steady),
        "overlap_audit": _overlap_audit(steady),
        "matched_contrast": _matched_contrast(
            steady, ("target_mode", "d", "target_value_bucket", "supply_bucket")),
        "matched_contrast_position": _matched_contrast(steady, ("target_mode", "d")),
        "magic_gain_per_wall_block": _mean(
            [row["magic_gain"] for row in steady
             if row["class"] == "wall_blocked" and "magic_gain" in row]),
        "free_gain_totals_by_class": {
            klass: sum(row["free_gain"] for row in steady
                       if row["class"] == klass and "free_gain" in row)
            for klass in sorted({row["class"] for row in steady})
        },
    }


def mechanism_signature(log_bytes: bytes, seat: int = 1, steady_from: int = 8) -> Mapping[str, Any]:
    """Pickup / zero-yield / burn signature of one arm, engine truth from the log.

    These are exactly the side indicators the platform A/B quoted for the safe
    side-step (pickup/unit-round 2.208 -> 2.168, zero-pickup 56.83% -> 56.84%),
    so a local repair arm can be compared against them mechanism-for-mechanism.
    """
    lines = log_bytes.decode().splitlines()
    pickup = burned = unit_rounds = zero = moves = 0
    for line in lines[2:]:
        record = json.loads(line)
        if "end" not in record or int(record["round"]) < steady_from:
            continue
        entry = {int(item["id"]): item for item in record["end"]["players"]}.get(seat)
        if entry is None:
            continue
        burned += int(record["end"].get("burned", 0) or 0)
        for unit in entry["units"]:
            unit_rounds += 1
            value = int(unit["pickup"])
            pickup += value
            zero += value == 0
            moves += sum(1 for action in unit["actions"] if action != STAY)
    return {
        "unit_rounds": unit_rounds,
        "pickup_total": pickup,
        "pickup_per_unit_round": pickup / unit_rounds if unit_rounds else None,
        "zero_pickup_rate": zero / unit_rounds if unit_rounds else None,
        "effective_moves_per_unit_round": moves / unit_rounds if unit_rounds else None,
        "burned_both_players": burned,
    }


def run_realized(
    map_name: str, base_so: Path, seeds: Sequence[str], repair: str, *, steady_from: int = 8,
) -> Mapping[str, Any]:
    from sim.runner import run_game

    walls = _static_walls(map_name)
    records = []
    for seed in seeds:
        plain = run_game(
            base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
            fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
        )
        shim = BlockedCostShim(
            map_name, walls, base_so, repair=repair, steady_from=steady_from, price_clear=False,
        )
        repaired = run_game(
            shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
            fixed_costs=(200, 201), player1_name="repair", player2_name="opponent",
        )
        base_net = int(plain.summary["players"]["1"]["net_gold"])
        repair_net = int(repaired.summary["players"]["1"]["net_gold"])
        rows = [row for row in shim.rows if row["steady"]]
        blocked = [row for row in rows if row["class"].endswith("_blocked")]
        base_sig = mechanism_signature(plain.log_bytes, seat=1, steady_from=steady_from)
        repair_sig = mechanism_signature(repaired.log_bytes, seat=1, steady_from=steady_from)
        records.append({
            "seed": seed,
            "base_net": base_net,
            "repair_net": repair_net,
            "delta": repair_net - base_net,
            "substitutions": shim.substitutions,
            "blocked_unit_rounds": len(blocked),
            "blocked_rate_all_unit_rounds": len(blocked) / len(rows) if rows else None,
            "identical_trajectory": plain.summary["log_sha256"] == repaired.summary["log_sha256"],
            "base_signature": base_sig,
            "repair_signature": repair_sig,
        })
        shim.close()
    deltas = [item["delta"] for item in records]
    return {
        "map": map_name,
        "repair": repair,
        "games": len(records),
        "records": records,
        "delta": summary(deltas),
        "wins": sum(1 for value in deltas if value > 0),
        "losses": sum(1 for value in deltas if value < 0),
        "signature_delta": {
            key: summary([
                item["repair_signature"][key] - item["base_signature"][key]
                for item in records
            ])
            for key in ("pickup_per_unit_round", "zero_pickup_rate",
                        "effective_moves_per_unit_round", "pickup_total", "burned_both_players")
        },
        "base_signature_mean": {
            key: summary([item["base_signature"][key] for item in records])
            for key in ("pickup_per_unit_round", "zero_pickup_rate",
                        "effective_moves_per_unit_round")
        },
        "repair_signature_mean": {
            key: summary([item["repair_signature"][key] for item in records])
            for key in ("pickup_per_unit_round", "zero_pickup_rate",
                        "effective_moves_per_unit_round")
        },
        "sigma": (
            statistics.fmean(deltas) / (statistics.stdev(deltas) / math.sqrt(len(deltas)))
            if len(deltas) > 1 and statistics.stdev(deltas) > 0 else None
        ),
    }


# ---------------------------------------------------------------------------
# the 920 arithmetic
# ---------------------------------------------------------------------------


def derive_pool(measured: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Reproduce the published ~920 gold/game pool and find its denominator."""
    rate = PUBLISHED["blocked_rate"]["map1"]
    wall = PUBLISHED["map1_cause_share"]["wall"]
    bomb = PUBLISHED["map1_cause_share"]["bomb"]
    pickup = PUBLISHED["pickup_gold_per_unit_round"]
    gap_wall = pickup["clear"] - pickup["wall_blocked"]
    gap_bomb = pickup["clear"] - pickup["bomb_blocked"]
    unit_rounds = 2 * 500

    candidates = [
        {
            "name": "A: all blocked unit-rounds x (clear - wall gap)",
            "count": rate * unit_rounds,
            "gap": gap_wall,
            "value": rate * unit_rounds * gap_wall,
        },
        {
            "name": "B: wall-blocked unit-rounds x (clear - wall gap)",
            "count": rate * wall * unit_rounds,
            "gap": gap_wall,
            "value": rate * wall * unit_rounds * gap_wall,
        },
        {
            "name": "C: wall+bomb split, each with its own gap",
            "count": rate * unit_rounds,
            "gap": None,
            "value": rate * unit_rounds * (wall * gap_wall + bomb * gap_bomb),
        },
        {
            "name": "D: all blocked unit-rounds x clear pickup (no counterfactual credit)",
            "count": rate * unit_rounds,
            "gap": pickup["clear"],
            "value": rate * unit_rounds * pickup["clear"],
        },
        {
            "name": "E: central-wall blocks only x (clear - wall gap)",
            "count": rate * PUBLISHED["central_wall_share_of_all_blocks"] * unit_rounds,
            "gap": gap_wall,
            "value": rate * PUBLISHED["central_wall_share_of_all_blocks"] * unit_rounds * gap_wall,
        },
    ]
    target = PUBLISHED["claimed_pool_gold_per_game"]
    for item in candidates:
        item["error_vs_920"] = item["value"] - target
        item["reproduces_920"] = abs(item["value"] - target) <= 5.0
    out: dict[str, Any] = {
        "published_pool": target,
        "gap_clear_minus_wall_blocked": gap_wall,
        "gap_clear_minus_bomb_blocked": gap_bomb,
        "candidates": candidates,
        "identified_denominator": next(
            (item["name"] for item in candidates if item["reproduces_920"]), None),
    }
    if measured:
        out["measured_correction"] = measured
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _pool_arms(arms: Mapping[str, Any]) -> Mapping[str, Any]:
    """Pool same-(map, repair) arms across disjoint seed batches.

    Both batches are same-seed paired against the same baseline, so pooling is a
    plain mean over independent games.  This is where the seeds 0-5 / 6-11 split
    matters: an in-sample 7.3 sigma can pool down to 1.5 sigma.
    """
    groups: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for payload in arms.values():
        key = (payload["map"], payload["repair"])
        groups[key].extend(float(record["delta"]) for record in payload["records"])
    out = {}
    for (map_name, repair), deltas in sorted(groups.items()):
        n = len(deltas)
        mean = statistics.fmean(deltas)
        sd = statistics.stdev(deltas) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n > 1 else 0.0
        out["%s/%s" % (map_name, repair)] = {
            "games": n, "mean": mean, "sd": sd, "se": se,
            "sigma": (mean / se) if se else None,
            "wins": sum(1 for value in deltas if value > 0),
            "losses": sum(1 for value in deltas if value < 0),
            "deltas": sorted(deltas),
        }
    return out


def assemble_report(
    trace_paths: Sequence[Path], realized_paths: Sequence[Path], *, primary: str = "map1",
) -> Mapping[str, Any]:
    """Fold the trace and closed-loop artifacts into the machine-readable verdict."""
    traces: dict[str, Any] = {}
    for path in trace_paths:
        payload = json.loads(path.read_text())
        for map_name, block in payload["maps"].items():
            traces[map_name] = block
    arms: dict[str, Any] = {}
    for path in realized_paths:
        payload = json.loads(path.read_text())
        seeds = [record["seed"] for record in payload["records"]]
        key = "%s/%s/seeds%s-%s" % (payload["map"], payload["repair"], seeds[0], seeds[-1])
        arms[key] = payload

    main_map = traces.get(primary, {})
    pool = main_map.get("pool", {})

    def value(key: str) -> Mapping[str, Any] | None:
        item = pool.get(key)
        if not item:
            return None
        return {"mean": item["mean"], "se": item.get("se")}

    derivation = derive_pool()
    out: dict[str, Any] = {
        "schema_version": 1,
        "subject": "map1 blocked-routing repricing and the 920 gold/game pool verdict",
        "build": {
            "source": "git show f18064c:src/player.cpp",
            "source_sha256": "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
        },
        "platform_games_consumed": 0,
        "the_920_figure": derivation,
        "verdict": {
            "pool_920_is": "mirage",
            "map1_central_wall_lesion_is": "false premise (伪命题)",
            "one_sentence":
                "The 920 gold/game map1 wall pool is a mirage -- its arithmetic applies a "
                "routed-decision rate to an all-unit-round base and then multiplies by a "
                "conditional-mean gap whose two arms share exactly zero common support, the "
                "wall-detour mechanism it motivated is reproducibly negative (-124.4 +- 56.1 "
                "gold/game over 12 paired games, matching the platform's -51.5 +- 94), and even "
                "an unimplementable perfect per-round repair of the whole blocked class is only "
                "+90.6 +- 58.9 (1.54 sigma, undecidable) -- so the map1 central-wall lesion is a "
                "false premise and this path is closed.",
            "residual_real_cost":
                "Upper bound +90.6 +- 58.9 gold/game on map1 (perfect per-round three-step play "
                "on blocked unit-rounds only, n=12 paired, 1.54 sigma = NOT established; map2 "
                "+113.0 +- 80.6, map3 +94.5 +- 91.2, both undecidable). It is not a wall repair: "
                "the oracle arrives at the blocked target in only 11.3% of substitutions and "
                "never returns to its start cell; 75% of its extra gold comes from OTHER visible "
                ">=3 cells. Cost of the cheapest known implementation shape (CHANGELOG gate C-2: "
                "+224B text, ~+105 instructions, +27 cycles P50) is 55-77 gold/game, comparable "
                "to the whole unestablished bound, with a live risk of the 23db121 platform "
                "latency surprise (local +10ns -> platform +27.5ns).",
        },
        "primary_map": primary,
        "pricing_ladder_gold_per_game": {
            "published_claim": PUBLISHED["claimed_pool_gold_per_game"],
            "published_arithmetic_on_measured_inputs": value("published_style_pool_gold_per_game"),
            "unphysical_wall_removal": value("magic_wall_removal_gold_per_game"),
            "identified_achievable_repair_open_loop": value("achievable_repair_gold_per_game"),
            "novel_gold": value("novel_gold_per_game"),
            "timing_gold": value("timing_gold_per_game"),
            "target_reaching_detour_open_loop": value("target_detour_gold_per_game"),
            "position_preserving_open_loop": value("position_preserving_gold_per_game"),
        },
        "closed_loop_arms": {
            name: {
                "map": payload["map"],
                "repair": payload["repair"],
                "seeds": [record["seed"] for record in payload["records"]],
                "games": payload["games"],
                "delta_mean": payload["delta"]["mean"],
                "delta_se": payload["delta"]["se"],
                "sigma": payload["sigma"],
                "wins": payload["wins"],
                "losses": payload["losses"],
                "deltas": sorted(record["delta"] for record in payload["records"]),
                "substitutions_per_game": [record["substitutions"] for record in payload["records"]],
                "signature_delta": {
                    key: item["mean"] for key, item in payload["signature_delta"].items()
                },
            }
            for name, payload in sorted(arms.items())
        },
        "closed_loop_pooled": _pool_arms(arms),
        "frequency_cross_validation": {
            map_name: {
                "blocked_rate_routed_unit_rounds": block["blocked_rate_routed_unit_rounds"],
                "blocked_rate_all_unit_rounds": block["blocked_rate_all_unit_rounds"],
                "published_blocked_rate": PUBLISHED["blocked_rate"].get(map_name),
                "cause_share_of_blocks": block["cause_share_of_blocks"],
                "central_wall_share_of_wall_blocks": block["central_wall_share_of_wall_blocks"],
                "central_wall_share_of_all_blocks": block["central_wall_share_of_all_blocks"],
                "pool": block["pool"],
                "hit_rate_axis": block["hit_rate_axis"],
                "block_runs": block["block_runs"],
                "class_table": block["pooled"]["class_table"],
                "overlap_audit": block["first_seed_overlap_audit"],
                "matched_contrast": block["first_seed_matched_contrast"],
                "reconstruction_mismatches_total": block["reconstruction_mismatches_total"],
                "trajectory_identical_all": block["trajectory_identical_all"],
            }
            for map_name, block in sorted(traces.items())
        },
        "bias_labels": {
            "local_npc_model": "39.18% per-action accuracy; over-greedy and over-central. "
                               "sim/README.md §7 states the residual over-estimates central "
                               "competition and relatively over-estimates outer-ring routes, so a "
                               "repair that walks a unit off the central peak is measured "
                               "FAVOURABLY here. Local ~0 is an upper bound.",
            "absolute_income": "not platform-comparable (local seat-1 net 1122-1552 vs probeobs "
                               "2182.4); only same-seed paired deltas are.",
            "open_loop_pool": "biased UP: per-round stock/flow double-count, and the oracle "
                              "inherits the base's positioning for free.",
            "closed_loop": "same-seed paired, dispatch=fixed, no clock read; trustworthy in sign.",
        },
        "instruction_price": {
            "gold_per_instruction_average": GOLD_PER_INSTRUCTION,
            "caveats": [
                "11 gold/ns holds only inside the +-20ns crossover band (src/INFRA.md §2.5)",
                "1.6 gold/instruction is an average; the frozen header records 84 instructions "
                "deleted returning only 5.6 cycles, six times below average",
            ],
        },
        "map1_deficit_reference": {
            "source": "sim/reports/archive_backfill.json -> map1_adjudication "
                      "(sibling agent, primary platform logs, orchestrator-adopted)",
            "Tundra_map1_pooled_n24": {
                "mean": -289.0416666666667, "se": 54.65191164319908,
                "sigma": 5.2887750487797485, "wins": 3, "losses": 21,
                "note": "four f18064c baseline arms inside one 12-minute window; "
                        "between-batch sd of arm means 141.5 (frTu1 -219.2, a2A0 -123.5, "
                        "alA0 -400.8, lnA0 -412.7) -- heterogeneous but pooled per "
                        "orchestrator instruction",
            },
            "Tundra_map1_anchor_proven_n18": {
                "mean": -251.7777777777778, "se": 57.0052, "sigma": 4.42,
            },
            "T1_map1_n6": {
                "mean": -274.3333333333333, "se": 149.97525721857517,
                "sigma": 1.829190617313079, "wins": 1, "losses": 5,
                "note": "independently reproduced here from sim/analyze_gold_delta.py "
                        "net_delta over family t1f1: deltas "
                        "[-688,-600,-383,-199,-90,+314]; undecidable at 2 sigma",
            },
            "retired": {
                "value": "-35.4 +- 45.5 (src/CHANGELOG.md line 167, 'frozen baseline')",
                "reason": "no n stated in any repo file, no archived build family exceeds 6 "
                          "replicates (sim/analyze_gold_delta.py survey --min 8 returns empty "
                          "for both opponents), and the sibling's impossibility proof shows the "
                          "20 best Tundra map1 games in the corpus sum to -2234 (mean -111.70) "
                          "against the -708 the figure requires",
            },
            "hit_rate_frame": {
                "source": "sim/reports/gold_delta_channel.json",
                "theirs_over_ours_hit_ratio": {
                    "Tundra map1": 1.397, "T-1 map1": 1.269, "Tundra map3": 0.810,
                },
                "hit_rate_pp_needed": {"Tundra map1": 6.1, "T-1 map1": 5.9},
            },
        },
    }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_derive = sub.add_parser("derive", help="re-derive the published 920 gold pool")
    p_derive.add_argument("--out", type=Path)

    p_trace = sub.add_parser("trace", help="open-loop blocked-class trace and pricing")
    p_trace.add_argument("--map", action="append", dest="maps", default=None)
    p_trace.add_argument("--base-so", type=Path, required=True)
    p_trace.add_argument("--seeds", nargs="+", default=["0"])
    p_trace.add_argument("--steady-from", type=int, default=8)
    p_trace.add_argument("--out", type=Path)

    p_real = sub.add_parser("realized", help="closed-loop paired repair experiment")
    p_real.add_argument("--map", default="map1")
    p_real.add_argument("--base-so", type=Path, required=True)
    p_real.add_argument("--seeds", nargs="+", default=["0", "1", "2"])
    p_real.add_argument("--repair", choices=[item for item in REPAIRS if item != "none"],
                        default="oracle3")
    p_real.add_argument("--steady-from", type=int, default=8)
    p_real.add_argument("--out", type=Path)

    p_report = sub.add_parser("report", help="assemble the machine-readable verdict JSON")
    p_report.add_argument("--trace", action="append", type=Path, required=True)
    p_report.add_argument("--realized", action="append", type=Path, default=[])
    p_report.add_argument("--primary", default="map1")
    p_report.add_argument("--out", type=Path)

    args = parser.parse_args(argv)

    if args.command == "derive":
        payload = derive_pool()
    elif args.command == "trace":
        payload = run_trace(
            args.maps or ["map1"], args.base_so, args.seeds, steady_from=args.steady_from,
        )
    elif args.command == "report":
        payload = assemble_report(args.trace, args.realized, primary=args.primary)
    else:
        payload = run_realized(
            args.map, args.base_so, args.seeds, args.repair, steady_from=args.steady_from,
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
