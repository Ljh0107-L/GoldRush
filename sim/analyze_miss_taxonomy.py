#!/usr/bin/env python3
"""Classify our own *miss* unit-rounds (zero held-gold yield) into a MECE taxonomy.

Why
===

On the unbiased fog-free channel the frozen build ``f18064c`` reaches a 34.79%
hit rate (share of unit-rounds with a positive held-gold delta) against the
opponents' 41.15%; every other income factor favours us, so the whole score
deficit is that 6.36 pp.  We are full-information about *ourselves* in the local
simulator, so unlike the opponent side this is settleable: we can enumerate
every zero-yield unit-round and ask what, mechanically, produced the zero.

Method
======

The measurement reuses the fidelity-checked substrate of
``sim.analyze_path_oracle``:

* ``MissTaxonomyStrategy`` wraps seat 1, calls the real ``moveDecision`` from a
  host build of the frozen source, and returns its six actions, ``k``, ``order``
  and ``vp`` **verbatim** -- so the trajectory does not drift and the
  measurement run's ``log_sha256`` equals a plain baseline run's;
* ``dispatch="fixed"`` with ``fixed_costs=(200, 201)`` makes seat 1 the faster
  mover, so the engine settles ``(seat 1, all seven NPCs, seat 2)``.  When seat 1
  acts no NPC and no enemy unit has moved yet, which makes the per-round
  counterfactual *exact* rather than approximate;
* the per-round harvest model is ``sim.analyze_path_oracle._sim``, proven to
  reproduce the engine's pickup and burn in 500/500 rounds;
* the *ground truth* denominator is read back out of the resulting full log
  (god-view ``end.players[].units[].gold`` differenced round over round, exactly
  the platform channel definition), never out of the model.

Two independent replicas run side by side and are cross-checked:

1. ``replica_decide_unit`` -- a line-by-line Python port of the frozen build's
   own target selector, LUT router and ``pass01`` waypoint gate.  It exposes the
   build's *internal* state (``has`` / ``standing`` / ``blind`` / target / ``d`` /
   ``ok``) which the ``.so`` does not export.  Validated by requiring that its
   predicted action triple equals the triple the ``.so`` actually emitted.
2. the value model, which answers "could this unit have scored this round by
   re-choosing only its own three actions, with everything else held at what
   really happened".

Taxonomy (MECE, priority-ordered, zero residual by construction)
================================================================

Let ``pickup`` be the unit's realized pickup and ``mp`` the largest pickup any
of its 125 legal action triples could have produced this round.

* ``A_BURN``       -- ``pickup > 0`` but the bomb / trample burn cancelled it, so
                      the held-gold delta is not positive.  We collected and lost.
* ``B_SUPPLY``     -- ``pickup == 0`` and ``mp == 0``: nothing collectable is
                      reachable at all.  A positioning failure; no same-round
                      decision can help.
    - ``B1_empty_window``  no visible gold anywhere in the unit's 5x5 window
    - ``B2_out_of_range``  visible gold, but every gold cell is at Manhattan >= 4
                           (the window is Chebyshev radius 2, movement is
                           4-directional with 3 steps, so the window corners are
                           structurally unreachable this round)
    - ``B3_walled_off``    gold at Manhattan <= 3 with no 3-step route to it
* ``C_BLOCKED``    -- ``pickup == 0``, ``mp > 0``, and the route was obstructed
    - ``C1_gate``  the build's own ``pass01`` waypoint gate refused the LUT path
                   (emitted triple is ``(a,4,4)`` or ``(4,4,4)``), split into
                   wall-attributable and bomb-richness-gate-attributable
    - ``C2_exec``  a requested step was blocked at execution (wall / bounds /
                   teammate / enemy)
* ``D_CONVERSION`` -- ``pickup == 0``, ``mp > 0``, route ran exactly as planned,
                      still zero.  A decision failure
    - ``D1_saw_and_reachable``  a reachable ``v>2`` cell sat inside the unit's own
                                5x5 window and we still scored nothing
    - ``D2_threshold_gate``     not D1, but reachable gold inside the own window,
                                all of it ``v<=2``: the ``v>2`` scan gate hid it
    - ``D3_scan_width``         every reachable gold cell lies outside the unit's
                                own 5x5 window (visible only via the teammate's
                                window, i.e. information the seat already has)

``CONTENTION`` is reported as a cross-cutting annotation, not a class.  Because
seat 1 moves first, within-round theft is structurally impossible (at the moment
seat 1 acts the engine has not yet advanced any NPC or enemy unit), so it is
measured across the round boundary: a cell inside this round's reachable set
that a third party drained during the *previous* round, after we had acted.
That attribution is closed against the engine's own accounting -- the theft total
must equal the NPC plus seat-2 pickups of that round.

Stock/flow separation
=====================

Gold is a stock, not a flow.  Every gold figure is split into ``novel`` (our own
realized trajectory never re-enters that cell later in the same game) and
``timing`` (it does, so the base collects it a few rounds later anyway).  A raw
per-round counterfactual sum above ~800 gold/game is a tripwire, not a finding.

Modes
=====

``taxonomy``   primary measurement over maps x seeds; writes the raw JSON
``selfcheck``  proves the replica, the harvest model, the log_sha256 equality,
               the contention identity and the within-round-theft impossibility
``report``     renders the markdown + machine-readable report from a raw JSON
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.analyze_path_oracle import (  # noqa: E402  (read-only reuse)
    ACTIONS,
    BOMB,
    BOMB_WAVE,
    DC,
    DR,
    GRID,
    NBR,
    NCELL,
    VISION_RADIUS,
    WALL,
    _sim,
    _visible_mask,
    classify,
    extract_state,
    fog_discipline,
    harvest_map,
    summary,
    walls_from_map,
)

STAY = 4
PLAYER_MARK, NPC_MARK, FOG_MARK = -2, -4, -5
ANCHORS = ((6, 8), (11, 8))          # frozen build: anch_r[u] = 6 + 5u, anch_c[u] = 8

# ---------------------------------------------------------------------------
# frozen-build tables, ported verbatim from f18064c src/player.cpp
# ---------------------------------------------------------------------------

# TabsT::remap -- 5x5 window indices in ring-distance priority order.  Note that
# the centre (index 12, the unit's own cell) has the *lowest* priority 24: the
# four Manhattan-4 corners outrank standing gold.
_RM = (7, 11, 13, 17,
       2, 6, 8, 10, 14, 16, 18, 22,
       1, 3, 5, 9, 15, 19, 21, 23,
       0, 4, 20, 24,
       12)
PRIO = [0] * 25
for _k in range(25):
    PRIO[_RM[_k]] = _k


def _build_slut() -> tuple[tuple[tuple[tuple[int, ...], ...], ...], ...]:
    """Port of ``SLut``: fact / pdr / pdc for (dr, dc) in [-3, 3]^2.

    Row-major unobstructed simulation, then the "early arrival" fold is
    pre-folded into ``fact`` (but deliberately *not* into pdr/pdc, which the
    waypoint gate reads).
    """
    fact = [[None] * 7 for _ in range(7)]
    pdr = [[None] * 7 for _ in range(7)]
    pdc = [[None] * 7 for _ in range(7)]
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            row, col = 0, 0
            acts, prow, pcol = [], [], []
            for _step in range(3):
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
            if 0 < distance < 3:
                acts[distance] = acts[distance - 1] ^ 1
                if distance == 1:
                    acts[2] = acts[1] ^ 1
            fact[dr + 3][dc + 3] = tuple(acts)
            pdr[dr + 3][dc + 3] = tuple(prow)
            pdc[dr + 3][dc + 3] = tuple(pcol)
    return (
        tuple(tuple(row) for row in fact),
        tuple(tuple(row) for row in pdr),
        tuple(tuple(row) for row in pdc),
    )


SL_FACT, SL_PDR, SL_PDC = _build_slut()


def _clamp3(value: int) -> int:
    return -3 if value < -3 else (3 if value > 3 else value)


class BuildState:
    """The frozen build's own persistent per-seat state, replicated.

    ``bombbit`` is deliberately *sticky*: the build only ORs into it and clears
    the whole thing on ``round % 20 == 0`` (``waveTick``).  It never purges a
    remembered bomb that a currently visible cell proves is gone.  The value
    model wants the purged version; the *gate* replica must use this one.
    """

    __slots__ = ("walls", "bombbit", "last_r", "last_c", "last_round")

    def __init__(self, walls: frozenset) -> None:
        self.walls = walls                       # frozenset of flat wall cells
        self.bombbit: set[int] = set()
        self.last_r = [0, 0]
        self.last_c = [0, 0]
        self.last_round = 10 ** 9

    def passable(self, row: int, col: int, rich: bool) -> bool:
        if not (0 <= row < GRID and 0 <= col < GRID):
            return False                         # bpw boundary sentinel
        cell = row * GRID + col
        if cell in self.walls:
            return False
        return not (rich and cell in self.bombbit)


def _escape_step(state: BuildState, row: int, col: int, prow: int, pcol: int, rich: bool) -> int:
    mask = 0
    for action in range(4):
        if state.passable(row + DR[action], col + DC[action], rich):
            mask |= 1 << action
    back = 0
    for action in range(4):
        if row + DR[action] == prow and col + DC[action] == pcol:
            back |= 1 << action
    candidate = (mask & ~back) | 16
    action = (candidate & -candidate).bit_length() - 1
    return -1 if action == 4 else action


def _steer_step(
    state: BuildState, row: int, col: int, goal_row: int, goal_col: int,
    prow: int, pcol: int, rich: bool,
) -> int:
    drr, dcc = goal_row - row, goal_col - col
    ar = 1 if drr > 0 else 0
    ac = 2 + (1 if dcc > 0 else 0)
    adr, adc = abs(drr), abs(dcc)
    row_first = adr >= adc
    p0 = ar if row_first else ac
    p1 = ac if row_first else ar
    ok0 = state.passable(row + DR[p0], col + DC[p0], rich)
    ok1 = state.passable(row + DR[p1], col + DC[p1], rich) and (adr != 0 and adc != 0)
    if ok0 or ok1:
        return p0 if ok0 else p1
    if adr or adc:
        return _escape_step(state, row, col, prow, pcol, rich)
    return -1


def replica_decide_unit(
    grid: Sequence[Sequence[int]],
    unit: int,
    srow: int,
    scol: int,
    held: int,
    state: BuildState,
    *,
    threshold: int = 2,
) -> tuple[tuple[int, int, int], dict[str, Any]]:
    """Replicate one unit's three actions exactly as f18064c computes them.

    Mutates ``state.bombbit`` the way the build's scan does (sticky OR, both
    units in index order), so unit 1 sees bombs unit 0's window revealed.
    Returns the triple plus the build's internal decision state.

    ``threshold`` is the frozen build's ``v > 2`` pickiness gate, exposed only so
    the report can price a change to it; the default reproduces f18064c exactly.
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
        target = ANCHORS[unit]

    dr0 = _clamp3(target[0] - srow)
    dc0 = _clamp3(target[1] - scol)
    distance = abs(dr0) + abs(dc0)
    acts = [STAY, STAY, STAY]
    gate_ok: bool | None = None
    gate_ok_poor: bool | None = None
    if distance == 0:
        mask = 0
        for action in range(4):
            if state.passable(srow + DR[action], scol + DC[action], rich):
                mask |= 1 << action
        gate_ok = mask != 0
        gate_ok_poor = any(
            state.passable(srow + DR[action], scol + DC[action], False) for action in range(4)
        )
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
        gate_ok_poor = all(
            state.passable(srow + xrow[t], scol + xcol[t], False) for t in range(3)
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
            "rich": rich,
            "has": has,
            "standing": standing,
            "blind": blind,
            "target": target,
            "d": distance,
            "gate_ok": gate_ok,
            "gate_ok_if_poor": gate_ok_poor,
        },
    )


# ---------------------------------------------------------------------------
# per-cell take helper (harvest_map with a teammate overlay)
# ---------------------------------------------------------------------------


def take_map(entered: Sequence[int], board: Sequence[int], overlay: Sequence[tuple[int, int]] | None) -> dict[int, int]:
    """Per-cell gold one unit takes, given cells the teammate already drained."""
    remaining: dict[int, int] = dict(overlay) if overlay else {}
    taken: dict[int, int] = {}
    for cell in entered:
        value = remaining.get(cell)
        if value is None:
            value = board[cell]
        if value > 0:
            amount = (65 * value + 99) // 100
            remaining[cell] = value - amount
            taken[cell] = taken.get(cell, 0) + amount
        else:
            remaining[cell] = value
    return taken


# ---------------------------------------------------------------------------
# per-round measurement
# ---------------------------------------------------------------------------

CLASSES = (
    "A_BURN",
    "B_SUPPLY",
    "C_CONVERSION",
    "D_BLOCKED",
)
SUBCLASSES = {
    "A_BURN": ("A1_bomb", "A2_trample"),
    "B_SUPPLY": ("B1_empty_window", "B2_out_of_range", "B3_walled_off"),
    "C_CONVERSION": (
        "C1_no_gold_target", "C2_target_out_of_range", "C3_target_unreachable",
    ),
    "D_BLOCKED": ("D1_gate", "D2_exec", "D3_arrived_empty"),
}


def round_measure(
    state: Any,
    base_actions: Sequence[int],
    build_info: Sequence[Mapping[str, Any]],
    replica_match: Sequence[bool],
    static_walls: frozenset = frozenset(),
) -> list[dict[str, Any]]:
    """Value the base pair and, per unit, the best same-round alternative.

    Serial settlement mirrors the engine exactly: unit ``order`` executes all
    three steps blocked by the teammate's START cell, then the other unit
    executes blocked by the first unit's FINAL cell and reading its depletions.
    Each unit's alternative is evaluated with *everything else held at what
    really happened*, which is the exact marginal counterfactual for hit rate.
    """
    starts, held, order = state.starts, state.held, state.order
    board, blocked, bombs, npc3 = state.board, state.blocked, state.bombs, state.npc3
    seqs = (tuple(int(v) for v in base_actions[:3]), tuple(int(v) for v in base_actions[3:6]))
    first, second = order, 1 - order

    item_first = _sim(seqs[first], starts[first], starts[second], board, blocked, bombs, npc3, held[first])
    overlay = item_first[4]
    bombs_second = bombs - set(item_first[7]) if item_first[7] else bombs
    item_second = _sim(
        seqs[second], starts[second], item_first[3], board, blocked, bombs_second,
        npc3, held[second], overlay,
    )
    base_items = [None, None]
    base_items[first] = item_first
    base_items[second] = item_second

    rows: list[dict[str, Any]] = []
    for unit in (0, 1):
        if unit == first:
            blocker, unit_bombs, unit_overlay = starts[second], bombs, None
        else:
            blocker, unit_bombs, unit_overlay = item_first[3], bombs_second, overlay
        start_cell = starts[unit]
        base_item = base_items[unit]
        base_take = take_map(base_item[5], board, unit_overlay)

        best_delta, best_item, best_seq = None, None, None
        best_hold, best_hold_item = None, None
        max_pickup = 0
        reach: set[int] = set()
        for seq in ACTIONS:
            item = _sim(
                seq, start_cell, blocker, board, blocked, unit_bombs, npc3,
                held[unit], unit_overlay,
            )
            reach.update(item[5])
            if item[1] > max_pickup:
                max_pickup = item[1]
            if best_delta is None or item[0] > best_delta or (
                item[0] == best_delta and item[1] > best_item[1]
            ):
                best_delta, best_item, best_seq = item[0], item, seq
            if item[3] == start_cell:
                if best_hold is None or item[0] > best_hold:
                    best_hold, best_hold_item = item[0], item

        best_take = take_map(best_item[5], board, unit_overlay)
        extra = {
            cell: amount - base_take.get(cell, 0)
            for cell, amount in best_take.items()
            if amount - base_take.get(cell, 0) > 0
        }
        hold_extra = {}
        if best_hold_item is not None:
            hold_take = take_map(best_hold_item[5], board, unit_overlay)
            hold_extra = {
                cell: amount - base_take.get(cell, 0)
                for cell, amount in hold_take.items()
                if amount - base_take.get(cell, 0) > 0
            }

        # engine-level block: a requested non-STAY step whose destination the
        # engine refused.  Attributed exactly, because the distinction matters:
        # the build's own pass01 gate already pre-screens walls, so a *wall*
        # refusal from the engine should be near-impossible, while enemy and
        # teammate cells are invisible to that gate (the teammate check is
        # retired in f18064c and a fogged enemy cannot be seen at all).
        blocked_steps = 0
        blocked_by_bounds = blocked_by_wall = blocked_by_enemy = blocked_by_teammate = 0
        position = start_cell
        for action in seqs[unit]:
            if action == STAY:
                continue
            nxt = NBR[position][action]
            if nxt < 0:
                blocked_steps += 1
                blocked_by_bounds += 1
                continue
            if blocked[nxt] or nxt == blocker:
                blocked_steps += 1
                if nxt in static_walls:
                    blocked_by_wall += 1
                elif nxt == blocker:
                    blocked_by_teammate += 1
                else:
                    blocked_by_enemy += 1
                continue
            position = nxt

        # visible gold in this unit's own 5x5 scan window, and reachability
        window: list[tuple[int, int, int]] = []      # (cell, value, manhattan)
        srow, scol = divmod(start_cell, GRID)
        for rrow in range(max(0, srow - 2), min(GRID, srow + 3)):
            for ccol in range(max(0, scol - 2), min(GRID, scol + 3)):
                cell = rrow * GRID + ccol
                value = board[cell]
                if value > 0:
                    window.append((cell, value, abs(rrow - srow) + abs(ccol - scol)))
        reach_gold = [(cell, board[cell]) for cell in reach if board[cell] > 0]
        own_cell_gold = board[start_cell]
        if own_cell_gold > 0 and start_cell in reach:
            pass                                     # already counted via reach
        window_cells = {item[0] for item in window}

        rows.append({
            "unit": unit,
            "is_first_mover": unit == first,
            "base_pickup": base_item[1],
            "base_burn": base_item[2],
            "base_delta": base_item[0],
            "base_entered": tuple(base_item[5]),
            "base_take": base_take,
            "max_pickup": max_pickup,
            "best_delta": best_delta,
            "best_seq": best_seq,
            "extra_by_cell": extra,
            "hold_delta": best_hold,
            "hold_extra_by_cell": hold_extra,
            "reach": frozenset(reach),
            "reach_gold": reach_gold,
            "board_value": {cell: board[cell] for cell in reach},
            "reach_gold_in_window_above_gate": [
                cell for cell, value in reach_gold if cell in window_cells and value > 2
            ],
            "reach_gold_in_window": [cell for cell, value in reach_gold if cell in window_cells],
            "window_gold": window,
            "blocked_steps": blocked_steps,
            "blocked_by_bounds": blocked_by_bounds,
            "blocked_by_wall": blocked_by_wall,
            "blocked_by_enemy": blocked_by_enemy,
            "blocked_by_teammate": blocked_by_teammate,
            "pattern": classify(seqs[unit]),
            "triple": seqs[unit],
            "build": dict(build_info[unit]),
            "replica_match": bool(replica_match[unit]),
            "start_cell": start_cell,
            "held": held[unit],
        })
    return rows


class MissTaxonomyStrategy:
    """Seat-1 shim: measure, then return the base decision byte-for-byte."""

    name = "miss_taxonomy"

    def __init__(
        self,
        walls: set[tuple[int, int]],
        base_so: Path,
        *,
        check_fog_every: int = 50,
    ) -> None:
        from sim.abi import SharedObjectStrategy

        self.static_walls = frozenset(row * GRID + col for row, col in walls)
        self.base = SharedObjectStrategy(base_so, name="taxonomy_base")
        self.check_fog_every = check_fog_every
        self.build = BuildState(self.static_walls)
        self.model_bombs: set[int] = set()
        self.last_round = 10 ** 9
        self.rows: list[dict[str, Any]] = []
        self.k_not_three = 0
        self.replica_hits = 0
        self.replica_total = 0
        self.replica_hits_steady = 0
        self.replica_total_steady = 0

    def close(self) -> None:
        self.base.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.static_walls)
            self.model_bombs.clear()
        if round_number % BOMB_WAVE == 0:
            self.build.bombbit.clear()
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

        # --- build replica: sticky bomb memory, exactly as g_s.bombbit ---------
        build_info = []
        replica_match = []
        for unit in (0, 1):
            triple, info = replica_decide_unit(
                grid, unit, my_units[unit][0], my_units[unit][1], my_gold[unit], self.build,
            )
            build_info.append(info)
            emitted = tuple(actions[unit * 3:unit * 3 + 3])
            match = triple == emitted
            replica_match.append(match)
            self.replica_total += 1
            self.replica_hits += match
            if round_number >= 8:
                self.replica_total_steady += 1
                self.replica_hits_steady += match

        # --- value model: purged bomb memory (a strategy could do this too) ----
        if self.model_bombs:
            mask = _visible_mask([r * GRID + c for r, c in my_units], VISION_RADIUS)
            self.model_bombs.difference_update([
                cell for cell in self.model_bombs
                if mask[cell] and grid[cell // GRID][cell % GRID] != BOMB
            ])
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
            self.k_not_three += 1
            return passthrough

        for row in round_measure(state, actions, build_info, replica_match, self.static_walls):
            row["round"] = round_number
            self.rows.append(row)
        return passthrough


# ---------------------------------------------------------------------------
# ground truth from the full log
# ---------------------------------------------------------------------------


def _ground(grid: Sequence[Sequence[int]]) -> list[int]:
    """God-view log grid -> pure ground.  ``render_full`` only paints the -2/-4
    actor marks onto cells whose ground is exactly 0, so the inverse is exact."""
    flat = [0] * NCELL
    for row in range(GRID):
        line = grid[row]
        base = row * GRID
        for col in range(GRID):
            value = int(line[col])
            flat[base + col] = 0 if value in (PLAYER_MARK, NPC_MARK, FOG_MARK) else value
    return flat


def parse_log(log_bytes: bytes, seat: int = 1) -> dict[str, Any]:
    """Extract the platform-definition ground truth from one full log."""
    lines = log_bytes.decode().splitlines()
    rounds = [json.loads(line) for line in lines[2:] if line.strip()]
    per_unit: list[dict[str, Any]] = []
    theft: list[dict[int, int]] = []
    entered_history: list[frozenset] = []
    start_grounds: list[list[int]] = []
    theft_identity_ok = 0
    theft_identity_total = 0
    for record in rounds:
        start, end = record["start"], record["end"]
        players = {item["id"]: item for item in start["players"]}
        end_players = {item["id"]: item for item in end["players"]}
        ours_start, ours_end = players[seat], end_players[seat]
        order = int(ours_end["order"])
        start_ground = _ground(start["grid"])
        end_ground = _ground(end["grid"])
        entered: list[list[int]] = [[], []]
        for unit in (0, 1):
            cell = tuple(ours_start["units"][unit]["position"])
            position = cell[0] * GRID + cell[1]
            for action in ours_end["units"][unit]["actions"]:
                if int(action) == STAY:
                    continue
                nxt = NBR[position][int(action)]
                if nxt < 0:
                    continue
                position = nxt
                entered[unit].append(position)
        our_take = harvest_map((entered[order], entered[1 - order]), start_ground)
        round_theft: dict[int, int] = {}
        for cell in range(NCELL):
            after_us = start_ground[cell] - our_take.get(cell, 0)
            if after_us > 0 and end_ground[cell] < after_us:
                round_theft[cell] = after_us - end_ground[cell]
        third_party = (
            sum(int(item["pickup"]) for item in end["npcs"])
            + sum(int(item["pickup"]) for item in end_players[3 - seat]["units"])
        )
        theft_identity_total += 1
        theft_identity_ok += int(sum(round_theft.values()) == third_party)
        theft.append(round_theft)
        start_grounds.append(start_ground)
        entered_history.append(frozenset(entered[0]) | frozenset(entered[1]))
        for unit in (0, 1):
            gold_start = int(ours_start["units"][unit]["gold"])
            gold_end = int(ours_end["units"][unit]["gold"])
            pickup = int(ours_end["units"][unit]["pickup"])
            per_unit.append({
                "round": int(record["round"]),
                "unit": unit,
                "gold_start": gold_start,
                "gold_end": gold_end,
                "delta": gold_end - gold_start,
                "pickup": pickup,
                "burn": pickup - (gold_end - gold_start),
                "effective": tuple(int(a) for a in ours_end["units"][unit]["actions"]),
                "entered": tuple(entered[unit]),
            })
    trample_cells = [
        {tuple(event["pos"]) for event in record["end"]["trample_events"]
         if int(event["unit_owner"]) == seat}
        for record in rounds
    ]
    return {
        "per_unit": per_unit,
        "theft": theft,
        "entered_history": entered_history,
        "start_ground": start_grounds,
        "trample_cells": trample_cells,
        "rounds": len(rounds),
        "theft_identity_ok": theft_identity_ok,
        "theft_identity_total": theft_identity_total,
    }


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def classify_miss(row: Mapping[str, Any], truth: Mapping[str, Any]) -> tuple[str, str]:
    """Priority-ordered MECE label for one miss unit-round.

    The order is causal, not convenient:

    1. ``A_BURN``       is the only class with ``pickup > 0``, so it separates first.
    2. ``B_SUPPLY``     is ``max_pickup == 0``: no same-round decision can help,
                        so nothing else can be the proximate cause.
    3. ``C_CONVERSION`` the selector aimed at something that *could not pay*
                        (the anchor, a target out of 3-step range, or a sealed
                        cell).  Fixing routing would not help; the target choice
                        is the binding constraint.  A decision failure.
    4. ``D_BLOCKED``    the selector aimed at a ``v>2`` cell that was inside its
                        own 5x5 window, at Manhattan <= 3, and genuinely
                        reachable -- arriving there was *guaranteed* to pay -- and
                        we still got nothing.  A routing failure.

    Exhaustive and disjoint by construction, so the residual is identically zero.
    """
    if truth["pickup"] > 0:
        return "A_BURN", "A2_trample" if row["trampled"] else "A1_bomb"
    if row["max_pickup"] == 0:
        if not row["window_gold"]:
            return "B_SUPPLY", "B1_empty_window"
        if all(item[2] >= 4 for item in row["window_gold"]):
            return "B_SUPPLY", "B2_out_of_range"
        return "B_SUPPLY", "B3_walled_off"
    build = row["build"]
    if not build["has"]:
        # ``blind`` (anchor march) or the ``standing`` self-target; either way the
        # scan found no v>2 cell, so the v>2 gate is the binding constraint.
        return "C_CONVERSION", "C1_no_gold_target"
    if build["d"] >= 4:
        return "C_CONVERSION", "C2_target_out_of_range"
    if (build["target"][0] * GRID + build["target"][1]) not in row["reach"]:
        return "C_CONVERSION", "C3_target_unreachable"
    if row["pattern"] in ("stall", "stay3"):
        return "D_BLOCKED", "D1_gate"
    if row["blocked_steps"] > 0:
        return "D_BLOCKED", "D2_exec"
    return "D_BLOCKED", "D3_arrived_empty"


def stock_flow_split(
    events: Sequence[tuple[int, Mapping[int, int]]], entered_history: Sequence[frozenset]
) -> tuple[float, float, int, int]:
    """Split per-cell counterfactual gold into novel and timing.

    A cell is *timing* when our own realized trajectory re-enters it at any
    later round of the same game; the base already collects that gold, so a
    per-round counterfactual that credits it is double-counting a stock.
    """
    by_round: dict[int, dict[int, int]] = {}
    for round_number, extra in events:
        bucket = by_round.setdefault(round_number, {})
        for cell, amount in extra.items():
            bucket[cell] = bucket.get(cell, 0) + amount
    novel = timing = 0.0
    novel_events = timing_events = 0
    suffix: set[int] = set()
    for index in range(len(entered_history) - 1, -1, -1):
        for cell, amount in by_round.get(index, {}).items():
            if cell in suffix:
                timing += amount
                timing_events += 1
            else:
                novel += amount
                novel_events += 1
        suffix |= entered_history[index]
    return novel, timing, novel_events, timing_events


def analyze_game(
    map_name: str, seed: str, base_so: Path, *, trajectory_check: bool = True,
) -> dict[str, Any]:
    from sim.runner import load_map, run_game

    walls = walls_from_map(load_map(map_name).rows)
    shim = MissTaxonomyStrategy(walls, base_so)
    measured = run_game(
        shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
        fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
    )
    shim.close()
    truth = parse_log(measured.log_bytes, seat=1)

    baseline_sha = None
    if trajectory_check:
        plain = run_game(
            base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
            fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
        )
        baseline_sha = plain.summary["log_sha256"]

    by_key = {(row["round"], row["unit"]): row for row in shim.rows}
    truth_by_key = {(item["round"], item["unit"]): item for item in truth["per_unit"]}
    entered_history = truth["entered_history"]
    theft = truth["theft"]
    trample_cells = truth["trample_cells"]

    counts: collections.Counter = collections.Counter()
    sub_counts: collections.Counter = collections.Counter()
    convertible: collections.Counter = collections.Counter()
    convertible_hold: collections.Counter = collections.Counter()
    gain_events: dict[str, list[tuple[int, Mapping[int, int]]]] = collections.defaultdict(list)
    hold_events: dict[str, list[tuple[int, Mapping[int, int]]]] = collections.defaultdict(list)
    burn_gain: collections.Counter = collections.Counter()
    contested: collections.Counter = collections.Counter()
    contested_target: collections.Counter = collections.Counter()
    fogged_supply = 0
    fogged_supply_gold = 0
    model_truth_mismatch = 0
    model_under_truth = 0
    model_over_truth = 0
    gain_total: collections.Counter = collections.Counter()
    gate_wall = gate_bomb = 0
    exec_bounds = exec_wall = exec_enemy = exec_teammate = 0
    far_target = teammate_drain = 0
    threshold_binding = scan_width_binding = also_obstructed = 0
    hits = misses = 0
    hits_pickup = 0
    graded = 0
    burn_total = 0
    burn_on_misses = 0
    burn_on_zero_pickup_misses = 0
    d_hist: collections.Counter = collections.Counter()

    for round_number in range(truth["rounds"]):
        for unit in (0, 1):
            key = (round_number, unit)
            truth_row = truth_by_key.get(key)
            row = by_key.get(key)
            if truth_row is None or row is None:
                continue
            # platform channel definition: end-phase held gold differenced round
            # over round.  Locally start[r] == end[r-1] exactly, so the r>=1
            # restriction below reproduces the platform's 499-difference chain.
            if round_number == 0:
                continue
            graded += 1
            burn_total += truth_row["burn"]
            hit = truth_row["delta"] > 0
            hits += hit
            hits_pickup += truth_row["pickup"] > 0
            if row["base_delta"] != truth_row["delta"] or row["base_pickup"] != truth_row["pickup"]:
                model_truth_mismatch += 1
                if row["base_pickup"] < truth_row["pickup"]:
                    model_under_truth += 1
                elif row["base_pickup"] > truth_row["pickup"]:
                    model_over_truth += 1
            if hit:
                continue
            misses += 1
            burn_on_misses += truth_row["burn"]
            if truth_row["pickup"] == 0:
                burn_on_zero_pickup_misses += truth_row["burn"]
            row = dict(row)
            trampled_here = {r * GRID + c for r, c in trample_cells[round_number]}
            # the engine's trample check sits inside the "moved" branch, so only
            # cells the unit actually entered can trample it.
            row["trampled"] = any(cell in trampled_here for cell in truth_row["entered"])
            row["bombed"] = truth_row["burn"] > 0 and not row["trampled"]
            start_ground = truth["start_ground"][round_number]
            label, sub = classify_miss(row, truth_row)
            counts[label] += 1
            sub_counts[(label, sub)] += 1

            gain = max(0, row["best_delta"] - row["base_delta"])
            pickup_part = sum(row["extra_by_cell"].values())
            if row["best_delta"] > 0:
                convertible[label] += 1
                convertible[(label, sub)] += 1
            if row["hold_delta"] is not None and row["hold_delta"] > 0:
                convertible_hold[label] += 1
                convertible_hold[(label, sub)] += 1
            if row["extra_by_cell"]:
                gain_events[label].append((round_number, row["extra_by_cell"]))
                gain_events["%s|%s" % (label, sub)].append((round_number, row["extra_by_cell"]))
            if row["hold_extra_by_cell"]:
                hold_events[label].append((round_number, row["hold_extra_by_cell"]))
                hold_events["%s|%s" % (label, sub)].append((round_number, row["hold_extra_by_cell"]))
            # exact split: gain == cellwise pickup gain + signed burn delta, so
            # the class gold accounting closes with a zero residual.
            burn_gain[label] += gain - pickup_part
            burn_gain[(label, sub)] += gain - pickup_part
            gain_total[label] += gain
            gain_total[(label, sub)] += gain

            previous = theft[round_number - 1] if round_number >= 1 else {}
            if previous:
                if any(cell in previous for cell in row["reach"]):
                    contested[label] += 1
                    contested[(label, sub)] += 1
                target = row["build"]["target"]
                if target[0] * GRID + target[1] in previous:
                    contested_target[label] += 1
                    contested_target[(label, sub)] += 1

            if label == "B_SUPPLY":
                # Price the information constraint: god-view gold sitting inside
                # the reachable set that our own fog hid from us this round.
                hidden = sum(
                    start_ground[cell] for cell in row["reach"]
                    if start_ground[cell] > 0 and row["board_value"].get(cell, 0) == 0
                )
                if hidden > 0:
                    fogged_supply += 1
                    fogged_supply_gold += hidden
            if sub == "D1_gate":
                if row["build"]["gate_ok_if_poor"] and not row["build"]["gate_ok"]:
                    gate_bomb += 1
                else:
                    gate_wall += 1
            if sub == "D2_exec":
                exec_bounds += row["blocked_by_bounds"] > 0
                exec_wall += row["blocked_by_wall"] > 0
                exec_enemy += row["blocked_by_enemy"] > 0
                exec_teammate += row["blocked_by_teammate"] > 0
            if label == "C_CONVERSION":
                d_hist[row["build"]["d"]] += 1
                if row["build"]["has"] and row["build"]["d"] >= 4:
                    far_target += 1
                if row["reach_gold_in_window"] and not row["reach_gold_in_window_above_gate"]:
                    threshold_binding += 1
                elif not row["reach_gold_in_window"]:
                    scan_width_binding += 1
            # sensitivity cross-tab: how many C_CONVERSION misses ALSO carried an
            # obstructed route, i.e. how much of the class would move to D_BLOCKED
            # under a routing-first priority instead of a targeting-first one.
            if label == "C_CONVERSION" and (
                row["pattern"] in ("stall", "stay3") or row["blocked_steps"] > 0
            ):
                also_obstructed += 1
            if sub == "D3_arrived_empty" and not row["is_first_mover"]:
                    teammate_drain += 1

    residual = graded - hits - misses
    class_stock_flow: dict[str, dict[str, float]] = {}
    for label, events in gain_events.items():
        novel, timing, nevents, tevents = stock_flow_split(events, entered_history)
        class_stock_flow[label] = {
            "novel_gold": novel, "timing_gold": timing,
            "novel_cell_events": nevents, "timing_cell_events": tevents,
        }
    hold_stock_flow: dict[str, dict[str, float]] = {}
    for label, events in hold_events.items():
        novel, timing, nevents, tevents = stock_flow_split(events, entered_history)
        hold_stock_flow[label] = {
            "novel_gold": novel, "timing_gold": timing,
            "novel_cell_events": nevents, "timing_cell_events": tevents,
        }

    return {
        "map": map_name,
        "seed": seed,
        "graded_unit_rounds": graded,
        "hits": hits,
        "misses": misses,
        "residual_unit_rounds": residual,
        "hit_rate_held_delta": hits / graded if graded else None,
        "hit_rate_pickup": hits_pickup / graded if graded else None,
        "class_counts": {name: counts[name] for name in CLASSES},
        "sub_counts": {
            "%s|%s" % (label, sub): sub_counts[(label, sub)]
            for label in CLASSES for sub in SUBCLASSES[label]
        },
        "class_sum": sum(counts[name] for name in CLASSES),
        "class_residual": misses - sum(counts[name] for name in CLASSES),
        "convertible": {name: convertible[name] for name in CLASSES},
        "convertible_sub": {
            "%s|%s" % (label, sub): convertible[(label, sub)]
            for label in CLASSES for sub in SUBCLASSES[label]
        },
        "convertible_position_preserving": {name: convertible_hold[name] for name in CLASSES},
        "convertible_position_preserving_sub": {
            "%s|%s" % (label, sub): convertible_hold[(label, sub)]
            for label in CLASSES for sub in SUBCLASSES[label]
        },
        "burn_avoidance_gold": {str(key if isinstance(key, str) else "%s|%s" % key): value
                                for key, value in burn_gain.items()},
        "gain_total_gold": {str(key if isinstance(key, str) else "%s|%s" % key): value
                            for key, value in gain_total.items()},
        "stock_flow": class_stock_flow,
        "stock_flow_position_preserving": hold_stock_flow,
        "contested": {str(key if isinstance(key, str) else "%s|%s" % key): value
                      for key, value in contested.items()},
        "contested_target": {str(key if isinstance(key, str) else "%s|%s" % key): value
                             for key, value in contested_target.items()},
        "gate_attribution": {"wall": gate_wall, "bomb_richness_gate": gate_bomb},
        "exec_attribution": {
            "bounds": exec_bounds, "static_wall": exec_wall,
            "visible_enemy": exec_enemy, "own_teammate": exec_teammate,
        },
        "burn_total": burn_total,
        "burn_on_misses": burn_on_misses,
        "burn_on_zero_pickup_misses": burn_on_zero_pickup_misses,
        "fogged_supply_unit_rounds": fogged_supply,
        "fogged_supply_gold_on_ground": fogged_supply_gold,
        "conversion_far_target": far_target,
        "conversion_threshold_gate_binding": threshold_binding,
        "conversion_scan_width_binding": scan_width_binding,
        "conversion_also_obstructed": also_obstructed,
        "conversion_teammate_drain_suspect": teammate_drain,
        "conversion_d_hist": dict(sorted(d_hist.items())),
        "model_truth_mismatch": model_truth_mismatch,
        "model_under_truth": model_under_truth,
        "model_over_truth": model_over_truth,
        "replica_match_rate": shim.replica_hits / shim.replica_total if shim.replica_total else None,
        "replica_match_rate_steady": (
            shim.replica_hits_steady / shim.replica_total_steady
            if shim.replica_total_steady else None
        ),
        "k_not_three": shim.k_not_three,
        "theft_identity_ok": truth["theft_identity_ok"],
        "theft_identity_total": truth["theft_identity_total"],
        "measured_log_sha256": measured.summary["log_sha256"],
        "baseline_log_sha256": baseline_sha,
        "trajectory_identical": (baseline_sha == measured.summary["log_sha256"]) if baseline_sha else None,
        "measured_net_gold": int(measured.summary["players"]["1"]["net_gold"]),
        "scenario_digest": measured.summary["scenario_digest"],
    }


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


def _job(payload: tuple[str, str, str, bool]) -> dict[str, Any]:
    map_name, seed, base_so, check = payload
    return analyze_game(map_name, seed, Path(base_so), trajectory_check=check)


def analyze_taxonomy(
    maps: Sequence[str], base_so: Path, seeds: Sequence[str], *, jobs: int = 1,
    trajectory_check: bool = True,
) -> dict[str, Any]:
    payloads = [(m, str(s), str(base_so), trajectory_check) for m in maps for s in seeds]
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as pool:
            records = list(pool.map(_job, payloads))
    else:
        records = [_job(item) for item in payloads]

    out: dict[str, Any] = {
        "build": {
            "commit": "f18064c",
            "source_sha256": "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
            "base_so": str(base_so),
        },
        "sample": {"maps": list(maps), "seeds": [str(s) for s in seeds], "games": len(records)},
        "per_game": records,
        "maps": {},
    }
    for map_name in maps:
        group = [item for item in records if item["map"] == map_name]
        out["maps"][map_name] = _aggregate(group)
    out["pooled"] = _aggregate(records)
    return out


def _aggregate(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not group:
        return {}
    total_unit_rounds = sum(item["graded_unit_rounds"] for item in group)
    total_misses = sum(item["misses"] for item in group)
    keys = ["%s|%s" % (label, sub) for label in CLASSES for sub in SUBCLASSES[label]]

    def _sum(field: str, key: str) -> float:
        return sum(item[field].get(key, 0) for item in group)

    def _sf(field: str, key: str, part: str) -> float:
        return sum(item[field].get(key, {}).get(part, 0.0) for item in group)

    games = len(group)
    classes: dict[str, Any] = {}
    for label in list(CLASSES) + keys:
        if label in CLASSES:
            count = sum(item["class_counts"][label] for item in group)
            conv = sum(item["convertible"][label] for item in group)
            hold = sum(item["convertible_position_preserving"][label] for item in group)
        else:
            count = sum(item["sub_counts"].get(label, 0) for item in group)
            conv = sum(item["convertible_sub"].get(label, 0) for item in group)
            hold = sum(item["convertible_position_preserving_sub"].get(label, 0) for item in group)
        novel = _sf("stock_flow", label, "novel_gold")
        timing = _sf("stock_flow", label, "timing_gold")
        novel_hold = _sf("stock_flow_position_preserving", label, "novel_gold")
        timing_hold = _sf("stock_flow_position_preserving", label, "timing_gold")
        burn = _sum("burn_avoidance_gold", label)
        gain = _sum("gain_total_gold", label)
        classes[label] = {
            "miss_unit_rounds": count,
            "share_of_misses": count / total_misses if total_misses else None,
            "pp_if_fully_eliminated": 100.0 * count / total_unit_rounds if total_unit_rounds else None,
            "convertible_same_round": conv,
            "pp_convertible_same_round": 100.0 * conv / total_unit_rounds if total_unit_rounds else None,
            "convertible_position_preserving": hold,
            "pp_convertible_position_preserving": (
                100.0 * hold / total_unit_rounds if total_unit_rounds else None
            ),
            "gold_per_game_raw_gain": gain / games,
            "gold_per_game_novel": novel / games,
            "gold_per_game_timing": timing / games,
            "gold_per_game_cellwise_raw": (novel + timing) / games,
            "novel_share": novel / (novel + timing) if (novel + timing) else None,
            "gold_per_game_burn_avoided": burn / games,
            "gold_per_game_novel_position_preserving": novel_hold / games,
            "gold_per_game_timing_position_preserving": timing_hold / games,
            "gold_accounting_residual": (gain - (novel + timing) - burn) / games,
            "contested_unit_rounds": _sum("contested", label),
            "contested_share": (
                _sum("contested", label) / count if count else None
            ),
            "contested_target_unit_rounds": _sum("contested_target", label),
        }
    return {
        "games": games,
        "graded_unit_rounds": total_unit_rounds,
        "misses": total_misses,
        "hits": sum(item["hits"] for item in group),
        "class_residual": total_misses - sum(
            sum(item["class_counts"][label] for label in CLASSES) for item in group
        ),
        "hit_rate_held_delta": summary([item["hit_rate_held_delta"] for item in group]),
        "hit_rate_pickup": summary([item["hit_rate_pickup"] for item in group]),
        "hit_rate_held_delta_pooled": sum(item["hits"] for item in group) / total_unit_rounds,
        "classes": classes,
        "gate_attribution": {
            "wall": sum(item["gate_attribution"]["wall"] for item in group),
            "bomb_richness_gate": sum(item["gate_attribution"]["bomb_richness_gate"] for item in group),
        },
        "exec_attribution": {
            key: sum(item["exec_attribution"][key] for item in group)
            for key in ("bounds", "static_wall", "visible_enemy", "own_teammate")
        },
        "conversion_far_target": sum(item["conversion_far_target"] for item in group),
        "conversion_threshold_gate_binding": sum(
            item["conversion_threshold_gate_binding"] for item in group
        ),
        "conversion_scan_width_binding": sum(
            item["conversion_scan_width_binding"] for item in group
        ),
        "conversion_also_obstructed": sum(item["conversion_also_obstructed"] for item in group),
        "conversion_teammate_drain_suspect": sum(
            item["conversion_teammate_drain_suspect"] for item in group
        ),
        "burn_total_per_game": sum(item["burn_total"] for item in group) / games,
        "burn_on_misses_per_game": sum(item["burn_on_misses"] for item in group) / games,
        "burn_on_zero_pickup_misses_per_game": sum(
            item["burn_on_zero_pickup_misses"] for item in group
        ) / games,
        "fogged_supply_unit_rounds": sum(item["fogged_supply_unit_rounds"] for item in group),
        "fogged_supply_gold_on_ground_per_game": sum(
            item["fogged_supply_gold_on_ground"] for item in group
        ) / games,
        "conversion_d_hist": dict(sorted(
            collections.Counter(
                {int(k): v for item in group for k, v in item["conversion_d_hist"].items()}
            ).items()
        )) if group else {},
        "fidelity": {
            "model_truth_mismatch_unit_rounds": sum(item["model_truth_mismatch"] for item in group),
            "model_under_truth_unit_rounds": sum(item["model_under_truth"] for item in group),
            "model_over_truth_unit_rounds": sum(item["model_over_truth"] for item in group),
            "replica_match_rate": summary([item["replica_match_rate"] for item in group]),
            "replica_match_rate_steady": summary([item["replica_match_rate_steady"] for item in group]),
            "k_not_three": sum(item["k_not_three"] for item in group),
            "theft_identity_ok": sum(item["theft_identity_ok"] for item in group),
            "theft_identity_total": sum(item["theft_identity_total"] for item in group),
            "trajectory_identical_all": all(
                item["trajectory_identical"] in (True, None) for item in group
            ),
        },
        "measured_net_gold": summary([item["measured_net_gold"] for item in group]),
    }


def selfcheck(map_name: str, base_so: Path, seed: str) -> dict[str, Any]:
    """Prove the substrate rather than assert it."""
    from sim.runner import load_map, run_game

    walls = walls_from_map(load_map(map_name).rows)
    shim = MissTaxonomyStrategy(walls, base_so, check_fog_every=1)
    measured = run_game(
        shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
        fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
    )
    shim.close()
    plain = run_game(
        base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
        fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
    )
    truth = parse_log(measured.log_bytes, seat=1)
    truth_by_key = {(item["round"], item["unit"]): item for item in truth["per_unit"]}
    model_pickup = engine_pickup = model_burn = engine_burn = 0
    exact = total = 0
    for row in shim.rows:
        item = truth_by_key[(row["round"], row["unit"])]
        model_pickup += row["base_pickup"]
        model_burn += row["base_burn"]
        engine_pickup += item["pickup"]
        engine_burn += item["burn"]
        total += 1
        exact += int(row["base_pickup"] == item["pickup"] and row["base_burn"] == item["burn"])
    # within-round theft impossibility: with dispatch (seat1, NPCs, seat2), the
    # cells seat 1 enters carry their start-of-round value, so the model's
    # pickup can only match the engine's if nobody moved before us.
    return {
        "map": map_name,
        "seed": seed,
        "measured_log_sha256": measured.summary["log_sha256"],
        "baseline_log_sha256": plain.summary["log_sha256"],
        "log_sha256_equal": measured.summary["log_sha256"] == plain.summary["log_sha256"],
        "dispatch_order_first_round": json.loads(
            measured.log_bytes.decode().splitlines()[2]
        )["end"]["dispatch_order"],
        "replica_match": shim.replica_hits,
        "replica_total": shim.replica_total,
        "replica_match_rate": shim.replica_hits / shim.replica_total,
        "replica_match_steady": shim.replica_hits_steady,
        "replica_total_steady": shim.replica_total_steady,
        "replica_match_rate_steady": shim.replica_hits_steady / shim.replica_total_steady,
        "harvest_model_exact_unit_rounds": exact,
        "harvest_model_unit_rounds": total,
        "model_pickup_sum": model_pickup,
        "engine_pickup_sum": engine_pickup,
        "model_burn_sum": model_burn,
        "engine_burn_sum": engine_burn,
        "theft_identity_ok": truth["theft_identity_ok"],
        "theft_identity_total": truth["theft_identity_total"],
        "fog_discipline_rounds_checked": truth["rounds"],
        "k_not_three": shim.k_not_three,
    }


class MissFixOracleStrategy:
    """Closed-loop *upper bound* on the taxonomy's headline pp claim.

    Not a strategy proposal and not affordable: it enumerates all 125 action
    triples per unit under the exact validated value model, which is orders of
    magnitude more work than 1.6 gold/instruction could ever buy.  Its only job
    is to answer "if a perfect, free organ converted every convertible miss into
    a hit, would the game score go up".  If even this loses, no cheap
    approximation of it can win.

    ``level="hold"`` keeps the substituted triple *position preserving* (the unit
    ends the round on the cell it started on), which is exactly the positional
    guard `path_harvest_oracle.md` Sec 5 identified as the missing ingredient.
    ``level="free"`` drops the guard for contrast.
    """

    name = "miss_fix_oracle"

    def __init__(self, walls: set[tuple[int, int]], base_so: Path, *, level: str = "hold") -> None:
        from sim.abi import SharedObjectStrategy

        self.static_walls = frozenset(row * GRID + col for row, col in walls)
        self.base = SharedObjectStrategy(base_so, name="fix_base")
        self.level = level
        self.model_bombs: set[int] = set()
        self.last_round = 10 ** 9
        self.substituted = 0
        self.unit_rounds = 0
        self.model_delta_gain = 0

    def close(self) -> None:
        self.base.close()

    def _best(self, start_cell, blocker, state, held, overlay, bombs):
        best = None
        for seq in ACTIONS:
            item = _sim(
                seq, start_cell, blocker, state.board, state.blocked, bombs,
                state.npc3, held, overlay,
            )
            if self.level == "hold" and item[3] != start_cell:
                continue
            if best is None or item[0] > best[0][0] or (
                item[0] == best[0][0] and item[0 + 1] > best[0][1]
            ):
                best = (item, seq)
        return best

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round or round_number % BOMB_WAVE == 0:
            self.model_bombs.clear()
        self.last_round = round_number

        decision = self.base(value)
        actions = [int(item) for item in decision.actions]
        order = int(decision.order)
        if int(decision.k) != 3:
            return tuple(actions) + (int(decision.k), order, int(decision.vp))

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        my_gold = [int(item) for item in value.my_units_gold]
        enemies = [
            (int(pos.row), int(pos.col)) for pos in value.visible_enemies
            if pos is not None and int(pos.row) >= 0
        ]
        npcs = [(int(pos.row), int(pos.col)) for _npc_id, pos in value.visible_npcs]
        if self.model_bombs:
            mask = _visible_mask([r * GRID + c for r, c in my_units], VISION_RADIUS)
            self.model_bombs.difference_update([
                cell for cell in self.model_bombs
                if mask[cell] and grid[cell // GRID][cell % GRID] != BOMB
            ])
        state = extract_state(
            grid, my_units, my_gold, enemies, npcs, order,
            self.static_walls, set(self.model_bombs),
        )
        for row in range(GRID):
            for col in range(GRID):
                if grid[row][col] == BOMB:
                    self.model_bombs.add(row * GRID + col)

        first, second = order, 1 - order
        seqs = [tuple(actions[:3]), tuple(actions[3:6])]
        held, starts, bombs = state.held, state.starts, state.bombs

        base_first = _sim(
            seqs[first], starts[first], starts[second], state.board, state.blocked,
            bombs, state.npc3, held[first],
        )
        self.unit_rounds += 1
        if base_first[0] <= 0:
            candidate = self._best(starts[first], starts[second], state, held[first], None, bombs)
            if candidate is not None and candidate[0][0] > 0:
                seqs[first] = candidate[1]
                self.substituted += 1
                self.model_delta_gain += candidate[0][0] - base_first[0]
        item_first = _sim(
            seqs[first], starts[first], starts[second], state.board, state.blocked,
            bombs, state.npc3, held[first],
        )
        overlay = item_first[4]
        bombs_second = bombs - set(item_first[7]) if item_first[7] else bombs
        base_second = _sim(
            seqs[second], starts[second], item_first[3], state.board, state.blocked,
            bombs_second, state.npc3, held[second], overlay,
        )
        self.unit_rounds += 1
        if base_second[0] <= 0:
            candidate = self._best(
                starts[second], item_first[3], state, held[second], overlay, bombs_second,
            )
            if candidate is not None and candidate[0][0] > 0:
                seqs[second] = candidate[1]
                self.substituted += 1
                self.model_delta_gain += candidate[0][0] - base_second[0]
        return tuple(seqs[0]) + tuple(seqs[1]) + (3, order, int(decision.vp))


def analyze_realized(
    maps: Sequence[str], base_so: Path, seeds: Sequence[str], *, level: str = "hold",
) -> dict[str, Any]:
    """Same-seed paired closed-loop delta for the perfect-free-organ upper bound."""
    from sim.runner import load_map, run_game

    out: dict[str, Any] = {"level": level, "maps": {}}
    for map_name in maps:
        walls = walls_from_map(load_map(map_name).rows)
        records = []
        for seed in seeds:
            baseline = run_game(
                base_so, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
            )
            shim = MissFixOracleStrategy(walls, base_so, level=level)
            fixed = run_game(
                shim, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=(200, 201), player1_name="fix", player2_name="opponent",
            )
            shim.close()
            truth_base = parse_log(baseline.log_bytes, seat=1)
            truth_fix = parse_log(fixed.log_bytes, seat=1)

            def hit_rate(truth):
                rows = [item for item in truth["per_unit"] if item["round"] > 0]
                return sum(item["delta"] > 0 for item in rows) / len(rows)

            records.append({
                "seed": str(seed),
                "base_net": int(baseline.summary["players"]["1"]["net_gold"]),
                "fix_net": int(fixed.summary["players"]["1"]["net_gold"]),
                "delta": int(fixed.summary["players"]["1"]["net_gold"])
                - int(baseline.summary["players"]["1"]["net_gold"]),
                "base_hit_rate": hit_rate(truth_base),
                "fix_hit_rate": hit_rate(truth_fix),
                "hit_rate_pp": 100.0 * (hit_rate(truth_fix) - hit_rate(truth_base)),
                "substituted_unit_rounds": shim.substituted,
                "unit_rounds": shim.unit_rounds,
                "open_loop_model_gain": shim.model_delta_gain,
            })
        out["maps"][map_name] = {
            "records": records,
            "delta": summary([item["delta"] for item in records]),
            "hit_rate_pp": summary([item["hit_rate_pp"] for item in records]),
            "base_hit_rate": summary([item["base_hit_rate"] for item in records]),
            "fix_hit_rate": summary([item["fix_hit_rate"] for item in records]),
            "substituted_unit_rounds": summary([item["substituted_unit_rounds"] for item in records]),
            "open_loop_model_gain": summary([item["open_loop_model_gain"] for item in records]),
        }
    everything = [item for group in out["maps"].values() for item in group["records"]]
    out["pooled"] = {
        "games": len(everything),
        "delta": summary([item["delta"] for item in everything]),
        "hit_rate_pp": summary([item["hit_rate_pp"] for item in everything]),
        "substituted_unit_rounds": summary([item["substituted_unit_rounds"] for item in everything]),
        "open_loop_model_gain": summary([item["open_loop_model_gain"] for item in everything]),
    }
    return out


class ReplicaThresholdStrategy:
    """The validated selector replica with the ``v > 2`` scan gate as a knob.

    Purpose: price the *cheapest conceivable* mechanism for the dominant
    convertible class.  Lowering the frozen build's pickiness threshold is a
    change to one constant (``_mm256_set1_epi32(2)``), i.e. **zero extra
    instructions**, so it is the natural floor of the cost ladder.

    Discipline that makes the A/B exact:

    * rounds ``< steady_from`` defer to the real ``.so`` verbatim, because the
      slow-start fingerprint/BFS layer is not replicated;
    * from ``steady_from`` on, the replica drives, and it is proven to reproduce
      the ``.so``'s emitted triple on 100% of steady-state unit-rounds;
    * ``threshold=2`` is therefore a **control** that must reproduce the plain
      baseline byte for byte.  Any other value isolates exactly the gate change.
    """

    name = "replica_threshold"

    def __init__(
        self, walls: set[tuple[int, int]], base_so: Path, *,
        threshold: int = 2, steady_from: int = 8,
    ) -> None:
        from sim.abi import SharedObjectStrategy

        self.static_walls = frozenset(row * GRID + col for row, col in walls)
        self.base = SharedObjectStrategy(base_so, name="replica_base")
        self.threshold = int(threshold)
        self.steady_from = int(steady_from)
        self.build = BuildState(self.static_walls)
        self.last_round = 10 ** 9
        self.overrides = 0
        self.differs = 0

    def close(self) -> None:
        self.base.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.static_walls)
        if round_number % BOMB_WAVE == 0:
            self.build.bombbit.clear()
        self.last_round = round_number

        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        order = int(decision.order)
        passthrough = actions + (int(decision.k), order, int(decision.vp))
        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        my_gold = [int(item) for item in value.my_units_gold]

        triples = []
        for unit in (0, 1):
            triple, _info = replica_decide_unit(
                grid, unit, my_units[unit][0], my_units[unit][1], my_gold[unit],
                self.build, threshold=self.threshold,
            )
            triples.append(triple)
        if round_number < self.steady_from or int(decision.k) != 3:
            return passthrough
        self.overrides += 1
        replica = tuple(triples[0]) + tuple(triples[1])
        if replica != actions:
            self.differs += 1
        # ``order`` and ``vp`` are unchanged: the gate only touches target choice.
        return replica + (3, order, int(decision.vp))


def analyze_threshold(
    maps: Sequence[str], base_so: Path, seeds: Sequence[str], thresholds: Sequence[int],
) -> dict[str, Any]:
    """Same-seed paired A/B of the zero-instruction scan-gate change."""
    from sim.runner import load_map, run_game

    out: dict[str, Any] = {"thresholds": list(thresholds), "maps": {}}
    for map_name in maps:
        walls = walls_from_map(load_map(map_name).rows)
        per_threshold: dict[str, Any] = {}
        for threshold in thresholds:
            records = []
            for seed in seeds:
                baseline = run_game(
                    base_so, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
                )
                shim = ReplicaThresholdStrategy(walls, base_so, threshold=threshold)
                variant = run_game(
                    shim, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
                )
                shim.close()

                def hit_rate(blob):
                    truth = parse_log(blob, seat=1)
                    rows = [item for item in truth["per_unit"] if item["round"] > 0]
                    return sum(item["delta"] > 0 for item in rows) / len(rows)

                records.append({
                    "seed": str(seed),
                    "base_net": int(baseline.summary["players"]["1"]["net_gold"]),
                    "variant_net": int(variant.summary["players"]["1"]["net_gold"]),
                    "delta": int(variant.summary["players"]["1"]["net_gold"])
                    - int(baseline.summary["players"]["1"]["net_gold"]),
                    "opponent_delta": int(variant.summary["players"]["2"]["net_gold"])
                    - int(baseline.summary["players"]["2"]["net_gold"]),
                    "base_hit_rate": hit_rate(baseline.log_bytes),
                    "variant_hit_rate": hit_rate(variant.log_bytes),
                    "hit_rate_pp": 100.0 * (hit_rate(variant.log_bytes) - hit_rate(baseline.log_bytes)),
                    "log_sha256_equal": baseline.summary["log_sha256"] == variant.summary["log_sha256"],
                    "replica_driven_rounds": shim.overrides,
                    "replica_differs_rounds": shim.differs,
                })
            per_threshold[str(threshold)] = {
                "records": records,
                "delta": summary([item["delta"] for item in records]),
                "opponent_delta": summary([item["opponent_delta"] for item in records]),
                "hit_rate_pp": summary([item["hit_rate_pp"] for item in records]),
                "log_sha256_equal_all": all(item["log_sha256_equal"] for item in records),
                "replica_differs_rounds": summary([item["replica_differs_rounds"] for item in records]),
            }
        out["maps"][map_name] = per_threshold
    out["pooled"] = {
        str(threshold): {
            "delta": summary([
                item["delta"] for group in out["maps"].values()
                for item in group[str(threshold)]["records"]
            ]),
            "hit_rate_pp": summary([
                item["hit_rate_pp"] for group in out["maps"].values()
                for item in group[str(threshold)]["records"]
            ]),
            "log_sha256_equal_all": all(
                group[str(threshold)]["log_sha256_equal_all"] for group in out["maps"].values()
            ),
        }
        for threshold in thresholds
    }
    return out


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------

def analyze_frontier(
    maps: Sequence[str], base_so: Path, seeds: Sequence[str],
    tags: Sequence[str] = ("thr0", "hold", "free"),
) -> list[dict[str, Any]]:
    """Hit rate vs yield-per-hit vs opponent redistribution, same-seed paired.

    This is the table that decides the round: it shows that the cheap mechanism
    moves *along* the hit/yield frontier (hit up, yield down, product flat) while
    the value-aware one moves the product.  It also records the **opponent's**
    net-gold change, which the plain paired delta hides -- in self-play a change
    that frees central gold hands some of it straight back to our own clone.
    """
    from sim.runner import load_map, run_game

    def channel(blob: bytes, seat: int = 1) -> dict[str, float]:
        truth = parse_log(blob, seat=seat)
        rows = [item for item in truth["per_unit"] if item["round"] > 0]
        hits = [item["delta"] for item in rows if item["delta"] > 0]
        return {
            "n": len(rows),
            "hit": len(hits) / len(rows),
            "mean": sum(item["delta"] for item in rows) / len(rows),
            "yield_per_hit": (sum(hits) / len(hits)) if hits else 0.0,
            "ge8": sum(1 for item in rows if item["delta"] >= 8) / len(rows),
        }

    rows: list[dict[str, Any]] = []
    for map_name in maps:
        walls = walls_from_map(load_map(map_name).rows)
        for seed in seeds:
            baseline = run_game(
                base_so, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
            )
            for tag in tags:
                if tag == "thr0":
                    shim: Any = ReplicaThresholdStrategy(walls, base_so, threshold=0)
                else:
                    shim = MissFixOracleStrategy(walls, base_so, level=tag)
                variant = run_game(
                    shim, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=(200, 201), player1_name="v", player2_name="opponent",
                )
                shim.close()
                base_channel = channel(baseline.log_bytes)
                variant_channel = channel(variant.log_bytes)
                rows.append({
                    "map": map_name, "seed": str(seed), "tag": tag,
                    "us_delta": int(variant.summary["players"]["1"]["net_gold"])
                    - int(baseline.summary["players"]["1"]["net_gold"]),
                    "opp_delta": int(variant.summary["players"]["2"]["net_gold"])
                    - int(baseline.summary["players"]["2"]["net_gold"]),
                    "hit_pp": 100.0 * (variant_channel["hit"] - base_channel["hit"]),
                    "hit_base": base_channel["hit"], "hit_var": variant_channel["hit"],
                    "ypf_base": base_channel["yield_per_hit"],
                    "ypf_var": variant_channel["yield_per_hit"],
                    "mean_base": base_channel["mean"], "mean_var": variant_channel["mean"],
                    "ge8_base": base_channel["ge8"], "ge8_var": variant_channel["ge8"],
                })
    return rows


PLATFORM_REFERENCE = {
    "source": "sim/reports/gold_delta_channel.json (n=36) and sim/reports/archive_backfill.json "
              "(enlarged n=48 / n=66); build f18064c; fog-free held-gold channel",
    "channel_field": "end.players[].units[].gold, differenced round over round",
    "n36": {"ours_hit": 0.3478623914495658, "theirs_hit": 0.4114618125139167,
            "gap": 0.4114618125139167 - 0.3478623914495658, "n": 35928, "games": 36,
            "note": "primary; the sample the channel was validated against"},
    "n48": {"ours_hit": 0.34608800935203743, "theirs_hit": 0.40424599198396793,
            "gap": 0.40424599198396793 - 0.34608800935203743, "n": 47904, "games": 48},
    "n66": {"ours_hit": 0.34364182911277097, "theirs_hit": 0.4277646201493897,
            "gap": 0.4277646201493897 - 0.34364182911277097, "n": 65868, "games": 66,
            "note": "sensitivity bound; largest identifiable f18064c sample"},
    "per_map": {
        "map1": {"ours_hit": 0.36021941, "theirs_hit": 0.48042084, "ratio": 1.33370},
        "map2": {"ours_hit": 0.44338009, "theirs_hit": 0.52187708, "ratio": 1.17704},
        "map3": {"ours_hit": 0.23997996, "theirs_hit": 0.23212959, "ratio": 0.96729},
    },
    "map_walls": {"map1": 40, "map2": 24, "map3": 78},
    "yield_per_hit": {"ours": 4.66842694831173, "theirs": 4.189406750997768},
    "mean_delta_per_unit_round": {"ours": 1.4906201291471832, "theirs": 1.5567802271209086},
    "factor_split": {"hit_ratio": 1.1828292526804287, "yield_ratio": 0.8973915191096237,
                     "mean_ratio": 1.044384277845206,
                     "note": "hit_ratio x yield_ratio = 1.0615 != mean_ratio 1.0444 because the "
                             "channel has a small negative tail (ours 0.687%, theirs 0.482% of "
                             "unit-rounds), so mean != hit x yield_per_hit exactly"},
    "ge8": {"ours": 0.06468492540636829, "theirs": 0.05672456023157426},
}

CORRECTIONS = [
    "**Nothing in the brief was found to be wrong.**  Every number I was handed re-verified at its "
    "source: `gold_delta_channel.json.pooled` gives ours `0.3478624` / theirs `0.4114618`, gap "
    "**6.360 pp**, n=35,928 per side; the mid-task update's n=48 (5.816 pp) and n=66 (8.412 pp) "
    "figures verify in `archive_backfill.json.fog_free_channel`; the frozen source hash is exactly "
    "`0ecce6fc...84fdd` and the worktree copy is indeed a different file.",
    "Two brief statements are **more precise than stated** rather than wrong.  (i) The gap is "
    "6.360 pp, not 6.3 pp, and the enlarged-sample range is 5.82-8.41 pp; this report carries both. "
    "(ii) The channel is `end[r] - end[r-1]`, so the platform denominator is 499 differences per "
    "unit (36 x 2 x 499 = 35,928), not 500.  I dropped round 0 to match, giving 998 graded "
    "unit-rounds per game rather than 1000.",
    "One brief expectation was **not borne out, in our favour**: contention is *not* zero and is not "
    "merely a boundary artefact.  26-32% of misses in each substantive class (and 40% of the 30 "
    "`A_BURN` misses, which is noise at that n) had a reachable cell drained by a "
    "third party in the previous round.  Within-round theft is impossible as briefed, and I state "
    "that explicitly (Sec 4) rather than reporting a vacuous zero.",
    "One published number I could not reproduce as stated, flagged rather than averaged: "
    "`path_harvest_oracle.md` Sec 5 reports the closed-loop myopic path optimum at "
    "**-832 +- 91 gold/game** and concludes \"greedy three-step path value is the wrong objective\". "
    "That is correct for *its* intervention (re-choose on every round).  Restricting the identical "
    "search to **idle units only** flips the sign to **+415 +- 79 gold/game**, 8/9 games.  The two "
    "results are compatible -- the oracle report itself flagged the positional guard as untested -- "
    "but the headline sentence should not be read as closing the whole line.",
    "The frozen source's own comment at `player.cpp:411` records the `>=2` pickiness variant as "
    "platform-judged-negative (`1184` vs `2388`).  Read carefully that is a **loss margin against an "
    "opponent in one game**, not a paired A/B against our own `>=3` build, so on its own it is "
    "suggestive rather than decisive.  This round supplies the missing paired evidence: locally the "
    "`>=2` variant is -21.6 +- 62.7 gold and the `>=1` variant is +26.3 +- 54.7 gold, both nil, "
    "which agrees with the note in sign.",
    "**Cross-validation with the parallel map1 wall repricing** (`sim/reports/map1_wall_repricing.md`, "
    "same frozen source hash, independently reconstructed selector): **no contradiction found on any "
    "comparable figure.**  We agree that the `ok` gate fails inside `player.cpp:504-506` before the "
    "engine sees anything (I measure exactly 0 engine-level wall or bounds refusals in 14,970 "
    "unit-rounds, which is their claim proved on a second sample); that the blocked class's novel "
    "gold is small (their 33.7 +- 7.8 gold/game on map1 vs my `D_BLOCKED` novel 26.0 gold/game on "
    "map1, different class boundaries, same order); that closed-loop repair of the blocked class is "
    "flat-to-negative (their -69 to -913, my position-preserving +8.8 +- 56.1); and that the lesion "
    "is not map1-specific.  I add two things they do not price: 42.0% of gate-blocked misses are "
    "caused by the **bomb richness gate** rather than by walls, and every execution-level block is "
    "an actor collision (155 enemy / 28 teammate).  Wall repricing therefore owns at most the 58.0% "
    "gate-wall slice, which is smaller than a wall-count-based split would suggest.",
    "`sim/OPPONENTS.md` line 477's \"our side\" rows are correctly marked unusable (102-build "
    "mixture, 军规 28) and were not used for any figure here.  The burst-rate comparison in that "
    "file is against that mixture and is likewise unused.",
]


def finalize(
    raw_path: Path, realized_paths: Sequence[Path], threshold_paths: Sequence[Path],
    frontier_path: Path | None, selfcheck_path: Path | None,
) -> dict[str, Any]:
    """Merge every artifact into the machine-readable report blob."""
    import statistics as _stats

    raw = json.loads(raw_path.read_text())
    realized = {}
    for path in realized_paths:
        blob = json.loads(path.read_text())
        realized[blob["level"]] = blob
    threshold: dict[str, Any] = {"maps": {}, "pooled": {}}
    thresholds: set[str] = set()
    for path in threshold_paths:
        blob = json.loads(path.read_text())
        for map_name, group in blob["maps"].items():
            threshold["maps"][map_name] = group
            thresholds |= set(group)
    for key in sorted(thresholds, key=lambda item: -int(item)):
        records = [
            item for group in threshold["maps"].values() for item in group[key]["records"]
        ]
        threshold["pooled"][key] = {
            "delta": summary([item["delta"] for item in records]),
            "opponent_delta": summary([item["opponent_delta"] for item in records]),
            "hit_rate_pp": summary([item["hit_rate_pp"] for item in records]),
            "log_sha256_equal_all": all(item["log_sha256_equal"] for item in records),
            "games": len(records),
        }

    frontier: dict[str, Any] = {}
    if frontier_path is not None:
        rows = json.loads(frontier_path.read_text())
        for tag in sorted({item["tag"] for item in rows}):
            group = [item for item in rows if item["tag"] == tag]
            mean = lambda field: _stats.fmean([item[field] for item in group])  # noqa: E731
            sem = lambda field: (  # noqa: E731
                _stats.stdev([item[field] for item in group]) / math.sqrt(len(group))
                if len(group) > 1 else 0.0
            )
            frontier[tag] = {
                "n": len(group),
                "us_delta_mean": mean("us_delta"), "us_delta_se": sem("us_delta"),
                "opp_delta_mean": mean("opp_delta"), "opp_delta_se": sem("opp_delta"),
                "relative_mean": mean("us_delta") - mean("opp_delta"),
                "relative_wins": sum(1 for item in group if item["us_delta"] - item["opp_delta"] > 0),
                "hit_pp": mean("hit_pp"),
                "hit_base": mean("hit_base"), "hit_var": mean("hit_var"),
                "ypf_base": mean("ypf_base"), "ypf_var": mean("ypf_var"),
                "mean_base": mean("mean_base"), "mean_var": mean("mean_var"),
                "ge8_base": mean("ge8_base"), "ge8_var": mean("ge8_var"),
                "per_game": group,
            }

    substrate: dict[str, Any] = {
        "source_sha256": "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
        "worktree_sha256": "d9be1e523ca523f1a2d7cecd4faa52971511670d57e3316ef25a32758f455d22",
        "worktree_note": "worktree src/player.cpp is NOT f18064c; commit 895a27e changed it",
    }
    if selfcheck_path is not None:
        substrate.update(json.loads(selfcheck_path.read_text()))
    return {
        "generated_by": "sim/analyze_miss_taxonomy.py finalize",
        "build": raw["build"],
        "platform_reference": PLATFORM_REFERENCE,
        "taxonomy": raw,
        "realized": realized,
        "threshold": threshold,
        "frontier": frontier,
        "substrate": substrate,
        "corrections": CORRECTIONS,
        "class_definitions": {
            "denominator": "miss unit-round := end-phase held gold differenced round over round "
                           "is <= 0, exactly the platform channel; round 0 dropped",
            "priority": "A_BURN -> B_SUPPLY -> C_CONVERSION -> D_BLOCKED, exhaustive and disjoint",
            "A_BURN": "pickup > 0 but bomb/trample burn cancelled it",
            "B_SUPPLY": "pickup == 0 and max_pickup == 0 over all 125 own action triples",
            "C_CONVERSION": "max_pickup > 0 and the build's own target was not a v>2 cell at "
                            "Manhattan <= 3 that is actually reachable",
            "D_BLOCKED": "max_pickup > 0 and the target was payable and reachable, yet the emitted "
                         "route did not arrive",
            "contention": "cross-cutting annotation, not a class: a cell in this round's reachable "
                          "set that an NPC or the enemy drained during the previous round, after "
                          "seat 1 had acted.  Within-round theft is structurally impossible.",
        },
    }


CLASS_LABEL = {
    "A_BURN": "A. burn-cancelled (collected, then lost it)",
    "B_SUPPLY": "B. supply-side (positioning: nothing collectable reachable)",
    "C_CONVERSION": "C. conversion (decision: aimed at something that could not pay)",
    "D_BLOCKED": "D. blocked (routing: payable reachable target, failed to arrive)",
}
SUB_LABEL = {
    "A_BURN|A1_bomb": "A1 bomb entry burned the pickup",
    "A_BURN|A2_trample": "A2 >=3-NPC trample burned the pickup",
    "B_SUPPLY|B1_empty_window": "B1 no visible gold in the 5x5 window at all",
    "B_SUPPLY|B2_out_of_range": "B2 visible gold, all of it at Manhattan >= 4",
    "B_SUPPLY|B3_walled_off": "B3 gold at Manhattan <= 3, no 3-step route",
    "C_CONVERSION|C1_no_gold_target": "C1 scan found no `v>2` cell -> marched to the anchor",
    "C_CONVERSION|C2_target_out_of_range": "C2 chosen gold target at Manhattan 4",
    "C_CONVERSION|C3_target_unreachable": "C3 chosen gold target sealed off",
    "D_BLOCKED|D1_gate": "D1 `pass01` waypoint gate refused the LUT path",
    "D_BLOCKED|D2_exec": "D2 a requested step blocked at execution",
    "D_BLOCKED|D3_arrived_empty": "D3 route ran, target paid nothing",
}


def _fmt(value: Any, spec: str = "%.2f") -> str:
    return "n/a" if value is None else spec % value


def _row(label: str, cells: Sequence[str]) -> str:
    return "| %s | %s |" % (label, " | ".join(cells))


def render_markdown(blob: Mapping[str, Any]) -> str:
    raw = blob["taxonomy"]
    maps = raw["sample"]["maps"]
    pooled = raw["pooled"]
    ref = blob["platform_reference"]
    out: list[str] = []
    w = out.append

    w("# Miss taxonomy: what our zero-yield unit-rounds are made of, and whether closing them pays")
    w("")
    w("Measurement and judgement only.  No strategy change, no platform submission, nothing under")
    w("`src/` touched.  Build under test is the frozen **`f18064c`** source extracted with")
    w("`git show f18064c:src/player.cpp`; verified `shasum -a 256` =")
    w("`0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`.  The worktree copy is a")
    w("*different file* (`d9be1e52...`, commit `895a27e` cut 84 instructions from it) and was not used.")
    w("Artifacts: `sim/reports/miss_taxonomy.json`, driver `sim/analyze_miss_taxonomy.py`.")
    w("")
    w("## 0. Verdict up front")
    w("")
    w("**The class that must shrink to close the hit-rate gap is `C_CONVERSION`, sub-class")
    w("`C1_no_gold_target` -- the `v > 2` scan gate marching an idle unit to its anchor instead of")
    w("at collectable sub-threshold gold.  A zero-instruction mechanism for it exists, it closes the")
    w("whole gap, and it is worth nothing.  This path is closed.**")
    w("")
    w("| step | result |")
    w("|---|---|")
    w("| target, briefed sample (n=36 games) | ours **%.3f%%** vs theirs **%.3f%%**, gap **%.2f pp** |"
      % (100 * ref["n36"]["ours_hit"], 100 * ref["n36"]["theirs_hit"], 100 * ref["n36"]["gap"]))
    w("| target, largest identifiable sample (n=66) | ours %.3f%% vs theirs %.3f%%, gap **%.2f pp** |"
      % (100 * ref["n66"]["ours_hit"], 100 * ref["n66"]["theirs_hit"], 100 * ref["n66"]["gap"]))
    w("| simulator hit rate, same channel definition | **%.2f%%** (biased DOWN vs platform, see Sec 6) |"
      % (100 * pooled["hit_rate_held_delta_pooled"]))
    w("| miss unit-rounds classified | **%d** of %d graded, MECE residual **%d** |"
      % (pooled["misses"], pooled["graded_unit_rounds"], pooled["class_residual"]))
    w("| pp available by re-choosing one unit's 3 actions | **%.2f pp** (open loop) |"
      % sum(pooled["classes"][c]["pp_convertible_same_round"] for c in CLASSES))
    w("| pp delivered by the zero-instruction gate change, **closed loop** | **+%.2f +- %.2f pp** |"
      % (blob["threshold"]["pooled"]["0"]["hit_rate_pp"]["mean"],
         blob["threshold"]["pooled"]["0"]["hit_rate_pp"]["se"]))
    w("| net gold that gate change delivers, closed loop, same-seed paired | **%+.1f +- %.1f gold/game** |"
      % (blob["threshold"]["pooled"]["0"]["delta"]["mean"],
         blob["threshold"]["pooled"]["0"]["delta"]["se"]))
    w("| ... while the *unchanged* opponent gains | **%+.1f gold/game**, so **relative score %+.1f** |"
      % (blob["frontier"]["thr0"]["opp_delta_mean"], blob["frontier"]["thr0"]["relative_mean"]))
    w("| yield per scoring round while doing it | **%.3f -> %.3f** (-%.1f%%) |"
      % (blob["frontier"]["thr0"]["ypf_base"], blob["frontier"]["thr0"]["ypf_var"],
         100 * (1 - blob["frontier"]["thr0"]["ypf_var"] / blob["frontier"]["thr0"]["ypf_base"])))
    w("| mean held-gold delta per unit-round | %.3f -> %.3f, i.e. **%+.1f%%, nil** |"
      % (blob["frontier"]["thr0"]["mean_base"], blob["frontier"]["thr0"]["mean_var"],
         100 * (blob["frontier"]["thr0"]["mean_var"] / blob["frontier"]["thr0"]["mean_base"] - 1)))
    w("")
    w("Hit rate and yield-per-hit lie on a **steep frontier**.  Ours is %.1f%% x %.2f gold, theirs is"
      % (100 * ref["n36"]["ours_hit"], ref["yield_per_hit"]["ours"]))
    w("%.1f%% x %.2f: a **%.0f%% hit-rate ratio against a %.0f%% yield ratio**, whose realised mean"
      % (100 * ref["n36"]["theirs_hit"], ref["yield_per_hit"]["theirs"],
         100 * ref["factor_split"]["hit_ratio"], 100 * ref["factor_split"]["yield_ratio"]))
    w("held-gold delta per unit-round differs by only **%.1f%%** (%.4f vs %.4f).  Sliding our build"
      % (100 * (ref["factor_split"]["mean_ratio"] - 1),
         ref["mean_delta_per_unit_round"]["ours"], ref["mean_delta_per_unit_round"]["theirs"]))
    w("along that frontier with a one-constant change reproduces their hit rate and buys no score.")
    w("The %.2f pp is a *coordinate*, not a loss." % (100 * ref["n36"]["gap"]))
    w("")
    free = blob["frontier"]["free"]
    w("What is **not** closed, and is the one positive finding here: a *value-aware* re-choice for")
    w("**idle units only** (leave a scoring unit alone) returns **%+.1f +- %.1f gold/game** for us"
      % (free["us_delta_mean"], free["us_delta_se"]))
    w("against %+.1f for the opponent (relative **%+.1f**, %d/%d games).  That is a different mechanism"
      % (free["opp_delta_mean"], free["relative_mean"], free["relative_wins"], free["n"]))
    w("against the same classes -- it raises the mean %.3f -> %.3f rather than trading along the"
      % (free["mean_base"], free["mean_var"]))
    w("frontier.  Sec 7 prices it, and Sec 9 labels the (upward) biases it carries.")
    w("")
    w("## 1. Miss classification table: 3 maps x 4 classes")
    w("")
    w("(a) share of all misses / (b1) hit-rate pp if the class were fully eliminated /")
    w("(b2) hit-rate pp actually reachable by re-choosing that unit's own 3 actions this round /")
    w("(b3) same, restricted to *position-preserving* alternatives (unit ends where it started) /")
    w("(c) gold/game after stock/flow separation, `novel + timing (+ burn delta)`.")
    w("")
    for label in CLASSES:
        w("### %s" % CLASS_LABEL[label])
        w("")
        w(_row("quantity", list(maps) + ["pooled"]))
        w("|---" * (len(maps) + 2) + "|")
        rows = [
            ("(a) share of all misses", "share_of_misses", 100.0, "%.2f%%"),
            ("(b1) pp if fully eliminated", "pp_if_fully_eliminated", 1.0, "%.2f"),
            ("(b2) pp convertible same-round", "pp_convertible_same_round", 1.0, "%.2f"),
            ("(b3) pp convertible position-preserving", "pp_convertible_position_preserving", 1.0, "%.2f"),
            ("(c) **novel** gold/game", "gold_per_game_novel", 1.0, "%.1f"),
            ("(c) *timing* gold/game", "gold_per_game_timing", 1.0, "%.1f"),
            ("(c) novel share of cellwise gold", "novel_share", 100.0, "%.1f%%"),
            ("(c) burn delta gold/game", "gold_per_game_burn_avoided", 1.0, "%+.1f"),
            ("(c) raw gain gold/game (tripwire >800)", "gold_per_game_raw_gain", 1.0, "%.1f"),
            ("gold accounting residual", "gold_accounting_residual", 1.0, "%+.2f"),
            ("contested share (cross-round)", "contested_share", 100.0, "%.1f%%"),
            ("miss unit-rounds (15 games)", "miss_unit_rounds", 1.0, "%d"),
        ]
        for title, field, scale, spec in rows:
            cells = []
            for name in list(maps) + ["pooled"]:
                bucket = pooled if name == "pooled" else raw["maps"][name]
                value = bucket["classes"][label][field]
                cells.append("n/a" if value is None else spec % (value * scale))
            w(_row(title, cells))
        w("")
        w("Sub-classes (pooled, 15 games):")
        w("")
        w(_row("sub-class", ["misses", "share of all misses", "pp b1", "pp b2", "pp b3", "novel gold/game", "timing gold/game"]))
        w("|---" * 8 + "|")
        for sub in SUBCLASSES[label]:
            key = "%s|%s" % (label, sub)
            item = pooled["classes"][key]
            w(_row(SUB_LABEL.get(key, key), [
                "%d" % item["miss_unit_rounds"],
                _fmt(None if item["share_of_misses"] is None else 100 * item["share_of_misses"], "%.2f%%"),
                _fmt(item["pp_if_fully_eliminated"]),
                _fmt(item["pp_convertible_same_round"]),
                _fmt(item["pp_convertible_position_preserving"]),
                _fmt(item["gold_per_game_novel"], "%.1f"),
                _fmt(item["gold_per_game_timing"], "%.1f"),
            ]))
        w("")
    w("### Column totals (pooled)")
    w("")
    w(_row("class", ["misses", "share", "pp b1", "pp b2", "pp b3", "novel g/game", "timing g/game", "raw g/game"]))
    w("|---" * 9 + "|")
    tot = collections.Counter()
    for label in CLASSES:
        item = pooled["classes"][label]
        for field in ("miss_unit_rounds", "pp_if_fully_eliminated", "pp_convertible_same_round",
                      "pp_convertible_position_preserving", "gold_per_game_novel",
                      "gold_per_game_timing", "gold_per_game_raw_gain"):
            tot[field] += item[field] or 0
        w(_row(CLASS_LABEL[label], [
            "%d" % item["miss_unit_rounds"],
            "%.2f%%" % (100 * item["share_of_misses"]),
            "%.2f" % item["pp_if_fully_eliminated"],
            "%.2f" % item["pp_convertible_same_round"],
            "%.2f" % item["pp_convertible_position_preserving"],
            "%.1f" % item["gold_per_game_novel"],
            "%.1f" % item["gold_per_game_timing"],
            "%.1f" % item["gold_per_game_raw_gain"],
        ]))
    w(_row("**total**", [
        "**%d**" % tot["miss_unit_rounds"], "**100.00%**",
        "**%.2f**" % tot["pp_if_fully_eliminated"],
        "**%.2f**" % tot["pp_convertible_same_round"],
        "**%.2f**" % tot["pp_convertible_position_preserving"],
        "**%.1f**" % tot["gold_per_game_novel"],
        "**%.1f**" % tot["gold_per_game_timing"],
        "**%.1f**" % tot["gold_per_game_raw_gain"],
    ]))
    w(_row("MECE residual", ["**%d**" % pooled["class_residual"]] + ["--"] * 7))
    w("")
    w("Every raw-gain figure is **below** the 800 gold/game tripwire, and after stock/flow separation")
    w("the novel component is **%.1f gold/game pooled across all four classes** -- %.1f%% of the raw sum."
      % (tot["gold_per_game_novel"],
         100 * tot["gold_per_game_novel"] / max(1e-9, tot["gold_per_game_novel"] + tot["gold_per_game_timing"])))
    w("")

    fid = pooled["fidelity"]
    w("## 2. Which class must shrink, and the arithmetic")
    w("")
    need36 = 100 * ref["n36"]["gap"]
    need66 = 100 * ref["n66"]["gap"]
    w("We need **+%.2f pp** (briefed n=36 sample) to **+%.2f pp** (largest identifiable n=66 sample)."
      % (need36, need66))
    w("Per 998 graded unit-rounds per game that is **+%.0f to +%.0f** converted unit-rounds."
      % (round(need36 / 100 * 998), round(need66 / 100 * 998)))
    w("")
    w("| class | pp available same-round | pp available position-preserving | can it close %.2f pp? |" % need36)
    w("|---|---|---|---|")
    for label in CLASSES:
        item = pooled["classes"][label]
        verdict = "yes on its own" if item["pp_convertible_same_round"] >= need36 else (
            "no -- structurally 0" if item["pp_convertible_same_round"] == 0 else "only partly")
        w("| %s | %.2f | %.2f | %s |" % (
            CLASS_LABEL[label], item["pp_convertible_same_round"],
            item["pp_convertible_position_preserving"], verdict))
    w("")
    w("* **`B_SUPPLY` (%.2f%% of misses, %.2f pp of the denominator) contributes exactly 0 pp.**"
      % (100 * pooled["classes"]["B_SUPPLY"]["share_of_misses"],
         pooled["classes"]["B_SUPPLY"]["pp_if_fully_eliminated"]))
    w("  By definition `max_pickup == 0`: no re-choice of that unit's three actions collects anything.")
    w("  It is the *largest* class and the *least* actionable.  A god-view check confirms this is not a")
    w("  fog artefact: only **%d of %d** supply misses (%.1f%%) had any real gold inside the reachable"
      % (pooled["fogged_supply_unit_rounds"], pooled["classes"]["B_SUPPLY"]["miss_unit_rounds"],
         100 * pooled["fogged_supply_unit_rounds"] / pooled["classes"]["B_SUPPLY"]["miss_unit_rounds"]))
    w("  set at all; in the rest the neighbourhood is genuinely empty.  Shrinking `B_SUPPLY` is a")
    w("  *multi-round positioning* problem, not a per-round decision problem, and is out of scope here.")
    w("* **`C_CONVERSION` is the class that must shrink.**  %.2f pp same-round, %.2f pp"
      % (pooled["classes"]["C_CONVERSION"]["pp_convertible_same_round"],
         pooled["classes"]["C_CONVERSION"]["pp_convertible_position_preserving"]))
    w("  position-preserving; both exceed +%.2f pp on their own.  Inside it, `C1_no_gold_target`" % need36)
    w("  alone carries %.2f pp same-round and %.2f pp position-preserving."
      % (pooled["classes"]["C_CONVERSION|C1_no_gold_target"]["pp_convertible_same_round"],
         pooled["classes"]["C_CONVERSION|C1_no_gold_target"]["pp_convertible_position_preserving"]))
    w("* `D_BLOCKED` carries %.2f pp same-round but only %.2f pp position-preserving.  Its internal"
      % (pooled["classes"]["D_BLOCKED"]["pp_convertible_same_round"],
         pooled["classes"]["D_BLOCKED"]["pp_convertible_position_preserving"]))
    w("  attribution matters for the parallel map1 wall repricing and is worth stating precisely:")
    w("")
    w("  | mechanism | misses | note |")
    w("  |---|---|---|")
    w("  | `D1_gate`, wall-attributable | %d | the `pass01` waypoint gate found a wall on a LUT waypoint |"
      % pooled["gate_attribution"]["wall"])
    w("  | `D1_gate`, **bomb-richness-gate**-attributable | %d | the gate would have passed if the unit were not `rich` (held < 100) |"
      % pooled["gate_attribution"]["bomb_richness_gate"])
    w("  | `D2_exec`, engine refused: static wall | **%d** | |" % pooled["exec_attribution"]["static_wall"])
    w("  | `D2_exec`, engine refused: out of bounds | **%d** | |" % pooled["exec_attribution"]["bounds"])
    w("  | `D2_exec`, engine refused: **visible enemy unit** | %d | `pass01` cannot see enemies |"
      % pooled["exec_attribution"]["visible_enemy"])
    w("  | `D2_exec`, engine refused: **our own teammate** | %d | the teammate check is retired in f18064c (`player.cpp:151`) |"
      % pooled["exec_attribution"]["own_teammate"])
    w("")
    w("  Two readings.  First, **%.1f%%** of gate-blocked misses are wall-caused and **%.1f%%** are"
      % (100 * pooled["gate_attribution"]["wall"] / max(1, pooled["gate_attribution"]["wall"] + pooled["gate_attribution"]["bomb_richness_gate"]),
         100 * pooled["gate_attribution"]["bomb_richness_gate"] / max(1, pooled["gate_attribution"]["wall"] + pooled["gate_attribution"]["bomb_richness_gate"])))
    w("  caused by the *bomb richness gate*, so wall repricing owns at most the former slice.")
    w("  Second, and this **independently confirms the sibling repricing report's claim that \"the")
    w("  engine never refuses a walled step from us\"**: across 14,970 unit-rounds the engine refused")
    w("  a step for a wall or a boundary **exactly %d times**.  The build's own `pass01` gate is"
      % (pooled["exec_attribution"]["static_wall"] + pooled["exec_attribution"]["bounds"]))
    w("  airtight once the wall table is locked; every single execution-level block is an *actor")
    w("  collision* with an enemy or with our own teammate, which is what the gate structurally")
    w("  cannot see.  `D2_exec` is therefore an actor-collision class, not a wall class, and no")
    w("  contradiction with the sibling report exists on any figure I can compare.")
    w("* `A_BURN` is negligible (%d unit-rounds, %.2f pp): collecting and then losing it to a bomb or"
      % (pooled["classes"]["A_BURN"]["miss_unit_rounds"], pooled["classes"]["A_BURN"]["pp_if_fully_eliminated"]))
    w("  trample explains almost none of the gap between the pickup-based and held-delta hit rates")
    w("  (%.2f%% vs %.2f%%)."
      % (100 * pooled["hit_rate_pickup"]["mean"], 100 * pooled["hit_rate_held_delta_pooled"]))
    w("* **Secondary finding worth recording: burn is almost entirely a miss-round phenomenon.**")
    w("  Total seat-1 burn is **%.1f gold/game** pooled, of which **%.1f%%** lands on miss unit-rounds"
      % (pooled["burn_total_per_game"],
         100 * pooled["burn_on_misses_per_game"] / pooled["burn_total_per_game"]))
    w("  and **%.1f%%** on unit-rounds that collected literally nothing (%.1f / %.1f / %.1f gold/game"
      % (100 * pooled["burn_on_zero_pickup_misses_per_game"] / pooled["burn_total_per_game"],
         raw["maps"]["map1"]["burn_total_per_game"], raw["maps"]["map2"]["burn_total_per_game"],
         raw["maps"]["map3"]["burn_total_per_game"]))
    w("  on map1/map2/map3).  The burn-delta column of Sec 1 sums to **%.1f gold/game**, i.e. a"
      % sum(pooled["classes"][c]["gold_per_game_burn_avoided"] for c in CLASSES))
    w("  same-round re-choice would avoid %.0f%% of all our burn.  Unlike the cellwise figures this"
      % (100 * sum(pooled["classes"][c]["gold_per_game_burn_avoided"] for c in CLASSES)
         / pooled["burn_total_per_game"]))
    w("  one is **novel by construction** -- a burn is a purse loss, not a cell, so there is no later")
    w("  round in which we collect it anyway and no stock/flow discount applies.  The mechanism is")
    w("  visible in the source: `player.cpp:401` merges bombs into the blocked bitmap only when")
    w("  `held >= 100`, and the comment justifies it as \"a poor unit burns 10% x 0 = 0, bombs are")
    w("  transparent\" -- but the engine charges `(held + 9) // 10`, which is 0 only at `held == 0`.")
    w("  A unit holding 50 burns 5.  **Important caveat: this is not separable evidence.**  The")
    w("  closed-loop runs in Sec 3 already avoid burn (their objective is net delta, not pickup), and")
    w("  they returned %+.1f and %+.1f gold, so the %.0f gold must not be added on top of them."
      % (blob["frontier"]["hold"]["us_delta_mean"], blob["frontier"]["free"]["us_delta_mean"],
         sum(pooled["classes"][c]["gold_per_game_burn_avoided"] for c in CLASSES)))
    w("* `D3_arrived_empty` is **exactly 0** in 14,970 unit-rounds.  That is a mechanics proof, not a")
    w("  coincidence: if the build aims at a `v>2` cell at Manhattan <= 3 that is genuinely reachable")
    w("  and nothing obstructs the emitted route, `(65v+99)//100 >= 2` is collected with certainty.")
    w("")
    w("## 3. Does closing it pay?  Closed-loop, same-seed paired")
    w("")
    w("This is the part that decides the round.  Three closed-loop variants, seat 1 only, opponent")
    w("left as the unmodified frozen build, `dispatch=fixed`, 3 maps x seeds 0,1,2 = 9 paired games each.")
    w("")
    w("| variant | our net gold | opponent net gold | **relative** | hit rate | yield/hit | mean delta/unit-round | Delta>=8 rate |")
    w("|---|---|---|---|---|---|---|---|")
    for tag, title in (
        ("thr0", "**zero-instruction gate change** (`v>2` -> `v>0`)"),
        ("hold", "perfect free oracle, position-preserving, idle units only"),
        ("free", "perfect free oracle, value-aware, idle units only"),
    ):
        f = blob["frontier"][tag]
        w("| %s | %+.1f +- %.1f | %+.1f +- %.1f | **%+.1f** (%d/%d) | %.4f -> **%.4f** (%+.2f pp) | %.3f -> %.3f | %.3f -> %.3f | %.4f -> %.4f |"
          % (title, f["us_delta_mean"], f["us_delta_se"], f["opp_delta_mean"], f["opp_delta_se"],
             f["relative_mean"], f["relative_wins"], f["n"],
             f["hit_base"], f["hit_var"], f["hit_pp"], f["ypf_base"], f["ypf_var"],
             f["mean_base"], f["mean_var"], f["ge8_base"], f["ge8_var"]))
    w("")
    w("Readings:")
    w("")
    w("1. **The gap is closable and closing it is worthless.**  The gate change delivers")
    w("   **+%.2f pp** of hit rate -- more than the briefed %.2f pp and more than the n=66 sensitivity"
      % (blob["frontier"]["thr0"]["hit_pp"], need36))
    w("   bound of %.2f pp -- for **%+.1f +- %.1f** gold, i.e. nil.  It also hands the *unchanged*"
      % (need66, blob["frontier"]["thr0"]["us_delta_mean"], blob["frontier"]["thr0"]["us_delta_se"]))
    w("   opponent %+.1f gold, so the **relative** score moves %+.1f and only %d of %d games improve."
      % (blob["frontier"]["thr0"]["opp_delta_mean"], blob["frontier"]["thr0"]["relative_mean"],
         blob["frontier"]["thr0"]["relative_wins"], blob["frontier"]["thr0"]["n"]))
    w("2. **The mechanism is the frontier.**  Yield per scoring round falls %.3f -> %.3f (-%.1f%%) as"
      % (blob["frontier"]["thr0"]["ypf_base"], blob["frontier"]["thr0"]["ypf_var"],
         100 * (1 - blob["frontier"]["thr0"]["ypf_var"] / blob["frontier"]["thr0"]["ypf_base"])))
    w("   hit rate rises, and the product -- the mean held-gold delta, which *is* income -- moves")
    w("   %.3f -> %.3f.  Sub-threshold cells pay 1-2 gold; the build's own hits pay %.1f."
      % (blob["frontier"]["thr0"]["mean_base"], blob["frontier"]["thr0"]["mean_var"],
         blob["frontier"]["thr0"]["ypf_base"]))
    w("   The platform pair sits on the same frontier: their hit rate is %.0f%% of ours and their"
      % (100 * ref["factor_split"]["hit_ratio"]))
    w("   yield per hit is %.0f%% of ours, and the realised mean held-gold delta per unit-round -- the"
      % (100 * ref["factor_split"]["yield_ratio"]))
    w("   quantity that actually becomes score -- differs by only **%.1f%%** (%.4f vs %.4f)."
      % (100 * (ref["factor_split"]["mean_ratio"] - 1),
         ref["mean_delta_per_unit_round"]["ours"], ref["mean_delta_per_unit_round"]["theirs"]))
    w("   (`hit_ratio x yield_ratio = %.4f` overstates `mean_ratio = %.4f` because the channel has a"
      % (ref["factor_split"]["hit_ratio"] * ref["factor_split"]["yield_ratio"],
         ref["factor_split"]["mean_ratio"]))
    w("   small negative tail: %.3f%% of our unit-rounds and %.3f%% of theirs lose held gold outright.)"
      % (100 * 0.0068748608327766645, 100 * 0.004815185927410376))
    w("   So a %.2f pp hit-rate gap corresponds to a %.1f%% income gap, and the taxonomy's job is to"
      % (need36, 100 * (ref["factor_split"]["mean_ratio"] - 1)))
    w("   say whether the pp or the income is the thing you can actually move.  It is the income.")
    w("3. **Position preservation is not the missing ingredient either.**  The `hold` oracle -- which")
    w("   *is* the positional guard `path_harvest_oracle.md` Sec 5 said was untested -- returns")
    w("   %+.1f +- %.1f gold for %+.2f pp.  Also nil."
      % (blob["frontier"]["hold"]["us_delta_mean"], blob["frontier"]["hold"]["us_delta_se"],
         blob["frontier"]["hold"]["hit_pp"]))
    w("4. **The one thing that does pay is value-aware re-choice for idle units:** %+.1f +- %.1f gold,"
      % (blob["frontier"]["free"]["us_delta_mean"], blob["frontier"]["free"]["us_delta_se"]))
    w("   relative %+.1f, %d/%d games, and it raises the *mean* %.3f -> %.3f (%+.1f%%) rather than"
      % (blob["frontier"]["free"]["relative_mean"], blob["frontier"]["free"]["relative_wins"],
         blob["frontier"]["free"]["n"], blob["frontier"]["free"]["mean_base"],
         blob["frontier"]["free"]["mean_var"],
         100 * (blob["frontier"]["free"]["mean_var"] / blob["frontier"]["free"]["mean_base"] - 1)))
    w("   trading along the frontier.  Note this **contradicts nothing** in the path-harvest oracle:")
    w("   its L1/L2/L3 rungs re-chose actions on *every* round including scoring ones, which walks a")
    w("   producing unit off its cell.  The difference between -832 and %+.0f gold/game is the single"
      % blob["frontier"]["free"]["us_delta_mean"])
    w("   guard *\"never touch a unit that is already scoring\"*.  That is a genuinely new result and")
    w("   it is the only live lead this round produced.")
    w("")
    w("Per-map closed-loop detail for the gate change:")
    w("")
    w("| map | our net gold | opponent | hit-rate pp | rounds where the replica diverges |")
    w("|---|---|---|---|---|")
    for name in maps:
        g = blob["threshold"]["maps"][name]["0"]
        w("| %s | %+.1f +- %.1f | %+.1f | %+.2f +- %.2f | %.0f of 492 |" % (
            name, g["delta"]["mean"], g["delta"]["se"], g["opponent_delta"]["mean"],
            g["hit_rate_pp"]["mean"], g["hit_rate_pp"]["se"], g["replica_differs_rounds"]["mean"]))
    w("")
    w("`threshold=1` (accept `v>=2`, the variant the frozen source's own line 411 records as")
    w("platform-judged-negative) is also flat locally: **%+.1f +- %.1f gold** for %+.2f pp."
      % (blob["threshold"]["pooled"]["1"]["delta"]["mean"], blob["threshold"]["pooled"]["1"]["delta"]["se"],
         blob["threshold"]["pooled"]["1"]["hit_rate_pp"]["mean"]))
    w("Local and platform agree in sign, which is the strongest cross-validation available here.")
    w("")
    w("## 4. Contention: measured across the round boundary, and it is not zero")
    w("")
    w("Because `fixed_costs=(200, 201)` makes seat 1 the faster mover, the engine settles")
    w("`(seat 1, all seven NPCs, seat 2)` -- confirmed from the log's own `end.dispatch_order`,")
    w("`%s`.  At the moment seat 1 acts, `positions` still holds every NPC and enemy start cell and"
      % blob["substrate"]["dispatch_order_first_round"])
    w("`board` is untouched by them, so **within-round theft is structurally impossible**.  Reporting")
    w("it as zero would be vacuous, so it is measured across the boundary instead: a cell inside this")
    w("round's reachable set that a third party drained during the *previous* round, after we acted.")
    w("")
    w("That attribution is closed against the engine's own accounting.  For every round the")
    w("reconstructed third-party removal total must equal `sum(end.npcs[].pickup) +")
    w("sum(end.players[2].units[].pickup)`; it does on **%d of %d** rounds."
      % (fid["theft_identity_ok"], fid["theft_identity_total"]))
    w("")
    w("| class | contested share of its misses | narrow variant (previous round's chosen target drained) |")
    w("|---|---|---|")
    for label in CLASSES:
        item = pooled["classes"][label]
        w("| %s | %.1f%% | %d unit-rounds |" % (
            CLASS_LABEL[label], 100 * (item["contested_share"] or 0),
            item["contested_target_unit_rounds"]))
    w("")
    w("Contention is a *distal* cause spread fairly evenly across the substantive classes"
      % ())
    w("(%.1f%% / %.1f%% / %.1f%% for B / C / D; `A_BURN`'s %.0f%% is noise at n=%d), which is"
      % (100 * pooled["classes"]["B_SUPPLY"]["contested_share"],
         100 * pooled["classes"]["C_CONVERSION"]["contested_share"],
         100 * pooled["classes"]["D_BLOCKED"]["contested_share"],
         100 * pooled["classes"]["A_BURN"]["contested_share"],
         pooled["classes"]["A_BURN"]["miss_unit_rounds"]))
    w("why it is an annotation and not a class: it changes *why* a cell was empty, not *what our")
    w("decision could have done about it this round*.  It is also the one number here that the local")
    w("NPC model distorts most (Sec 6), so it should be read as a rough magnitude only.")
    w("")
    w("## 5. Per-map cross-check against the platform deficit ranking")
    w("")
    w("| map | platform theirs/ours hit ratio | platform ours hit | **simulator** ours hit | C_CONVERSION share of misses | C pp same-round | B_SUPPLY share | B3 walled-off share | map walls |")
    w("|---|---|---|---|---|---|---|---|---|")
    for name in maps:
        pm = ref["per_map"][name]
        mm = raw["maps"][name]
        w("| %s | **%.3f** | %.2f%% | %.2f%% | %.2f%% | %.2f | %.2f%% | %.2f%% | %d |" % (
            name, pm["ratio"], 100 * pm["ours_hit"], 100 * mm["hit_rate_held_delta_pooled"],
            100 * mm["classes"]["C_CONVERSION"]["share_of_misses"],
            mm["classes"]["C_CONVERSION"]["pp_convertible_same_round"],
            100 * mm["classes"]["B_SUPPLY"]["share_of_misses"],
            100 * mm["classes"]["B_SUPPLY|B3_walled_off"]["share_of_misses"],
            ref["map_walls"][name]))
    w("")
    w("* `C_CONVERSION`'s share **and** its convertible pp rank map1 > map2 > map3, which is exactly")
    w("  the platform deficit ranking (1.334 > 1.177 > 0.967).  `B_SUPPLY` ranks inversely.")
    w("  **But with only three maps a perfect rank agreement has p >= 1/6 = 0.167 under the null**, so")
    w("  this cannot be called significant.  It is a consistency check that passed, nothing more.")
    w("* The `B3_walled_off` share tracks wall count monotonically (map3 78 walls -> %.2f%%, map1 40 ->"
      % (100 * raw["maps"]["map3"]["classes"]["B_SUPPLY|B3_walled_off"]["share_of_misses"]))
    w("  %.2f%%, map2 24 -> %.2f%%).  That is an independent validity check on the class definition."
      % (100 * raw["maps"]["map1"]["classes"]["B_SUPPLY|B3_walled_off"]["share_of_misses"],
         100 * raw["maps"]["map2"]["classes"]["B_SUPPLY|B3_walled_off"]["share_of_misses"]))
    w("* **The simulator's per-map hit-rate ordering is perfectly inverted relative to the platform.**")
    w("  Platform: map2 %.1f%% > map1 %.1f%% > map3 %.1f%%.  Simulator: map3 %.1f%% > map2 %.1f%% >"
      % (100 * ref["per_map"]["map2"]["ours_hit"], 100 * ref["per_map"]["map1"]["ours_hit"],
         100 * ref["per_map"]["map3"]["ours_hit"],
         100 * raw["maps"]["map3"]["hit_rate_held_delta_pooled"],
         100 * raw["maps"]["map2"]["hit_rate_held_delta_pooled"]))
    w("  map1 %.1f%%.  Spearman rho = -1.  **Per-map pp figures in this report must therefore not be"
      % (100 * raw["maps"]["map1"]["hit_rate_held_delta_pooled"]))
    w("  used to target a specific map**; only the pooled figures and the closed-loop paired deltas")
    w("  carry weight.  The most likely cause is the documented NPC over-greed, which bites hardest")
    w("  where gold is densest and walls fewest.")
    w("")
    w("## 6. Denominator reconciliation and simulator fidelity")
    w("")
    w("| quantity | value | note |")
    w("|---|---|---|")
    w("| platform channel definition | `end.players[].units[].gold` differenced round over round | verified in `gold_delta_channel.json.channel.field` |")
    w("| platform n | %d unit-observations / side, 36 games | = 36 x 2 x 499, so round 0 has no predecessor |" % ref["n36"]["n"])
    w("| simulator, same definition | **%.2f%%** over %d graded unit-rounds | round 0 dropped identically; locally `start[r] == end[r-1]` exactly |"
      % (100 * pooled["hit_rate_held_delta_pooled"], pooled["graded_unit_rounds"]))
    w("| simulator, pickup-based variant | %.2f%% | differs by only %.2f pp, i.e. burn almost never cancels a pickup |"
      % (100 * pooled["hit_rate_pickup"]["mean"],
         100 * (pooled["hit_rate_pickup"]["mean"] - pooled["hit_rate_held_delta_pooled"])))
    w("| **sim-vs-platform gap** | **%.2f pp low** | direction: simulator hit rate is biased **DOWN** |"
      % (100 * (ref["n36"]["ours_hit"] - pooled["hit_rate_held_delta_pooled"])))
    w("")
    w("The simulator reproduces our hit rate to within %.2f pp but **the per-map pattern is inverted**"
      % abs(100 * (ref["n36"]["ours_hit"] - pooled["hit_rate_held_delta_pooled"])))
    w("(Sec 5).  That is a real fidelity limit and it caps how far any pp figure here can be trusted:")
    w("the pooled magnitude is credible to roughly +-4 pp, the per-map decomposition is not credible")
    w("at all.  What *is* trustworthy is the same-seed paired closed-loop delta in Sec 3, because both")
    w("legs run in the same simulator against the same scenario digest and the bias cancels in sign.")
    w("")
    w("## 7. Pricing at 1.6 gold/instruction")
    w("")
    w("| candidate | instruction cost | budget it would need | measured closed-loop return | verdict |")
    w("|---|---|---|---|---|")
    w("| `C1` gate change `v>2` -> `v>0` | **0** (one constant in `_mm256_set1_epi32`) | none | %+.1f +- %.1f gold, relative %+.1f | **closed: free and still not worth it** |"
      % (blob["frontier"]["thr0"]["us_delta_mean"], blob["frontier"]["thr0"]["us_delta_se"],
         blob["frontier"]["thr0"]["relative_mean"]))
    w("| `C1` gate change `v>2` -> `v>1` | **0** | none | %+.1f +- %.1f gold | closed; platform already judged it negative |"
      % (blob["threshold"]["pooled"]["1"]["delta"]["mean"], blob["threshold"]["pooled"]["1"]["delta"]["se"]))
    w("| position-preserving idle re-choice | >= hundreds of instructions (125-sequence search) | headroom buys **~%.0f instructions** | %+.1f +- %.1f gold | closed |"
      % (max(0.0, blob["frontier"]["hold"]["us_delta_mean"]) / 1.6,
         blob["frontier"]["hold"]["us_delta_mean"], blob["frontier"]["hold"]["us_delta_se"]))
    w("| widen the bomb richness gate below `held == 100` | small: the gate is already computed at `player.cpp:401` | headroom buys **~%.0f instructions**, but see the caveat | not separably measured; already inside the Sec 3 runs | **open, not separable** |"
      % (sum(pooled["classes"][c]["gold_per_game_burn_avoided"] for c in CLASSES) / 1.6))
    w("| **value-aware idle re-choice** | oracle is thousands; a cheap approximation is the open question | headroom buys **~%.0f instructions** | %+.1f +- %.1f gold, relative %+.1f | **open, biased UP** |"
      % (blob["frontier"]["free"]["relative_mean"] / 1.6, blob["frontier"]["free"]["us_delta_mean"],
         blob["frontier"]["free"]["us_delta_se"], blob["frontier"]["free"]["relative_mean"]))
    w("")
    w("Carry both pricing caveats.  The 11 gold/ns rate holds only inside the +-20 ns crossover band")
    w("and decays outside it, and 1.6 gold/instruction is an **average**: the frozen source's own")
    w("header records that deleting 84 instructions returned only 5.6 cycles, about six times below")
    w("average.  So ~%.0f instructions is a conservative *ceiling* on the budget the one live lead"
      % (blob["frontier"]["free"]["relative_mean"] / 1.6))
    w("could justify, not a promise, and it must additionally be discounted for:")
    w("")
    w("* **self-play**: the opponent here is a copy of ourselves, and it gained %+.1f gold under the"
      % blob["frontier"]["free"]["opp_delta_mean"])
    w("  `free` variant.  Against T-1, which contests the same central cells far more effectively, the")
    w("  same cells would not be free.  Direction: **biased UP**.")
    w("* **NPC over-greed**: `sim/README.md` Sec 7 records 39.18% per-action accuracy and NPCs")
    w("  over-eating by +24%..+71%, which \"over-estimates central competition, under-estimates central")
    w("  residency and relatively over-estimates outer-ring routes\".  The `free` variant sends idle")
    w("  units off the anchor onto outer cells, so its value is **relatively over-estimated**.")
    w("* **absolute income is not comparable**: local net gold is %.0f/game against a full-strength"
      % pooled["measured_net_gold"]["mean"])
    w("  copy of ourselves; the platform's uncontested figure is 2182.4.  Only paired deltas transfer.")
    w("")
    w("## 8. Substrate proofs (shown, not asserted)")
    w("")
    sub = blob["substrate"]
    w("| claim | evidence |")
    w("|---|---|")
    w("| frozen source is the one under test | `git show f18064c:src/player.cpp` -> `shasum -a 256` = `%s` (expected, matched); worktree copy is `%s` |"
      % (sub["source_sha256"], sub["worktree_sha256"]))
    w("| **verbatim passthrough** | measurement run `log_sha256` = `%s` **equals** the plain baseline run's `%s` for map1 seed 0; `trajectory_identical_all` true on all %d games |"
      % (sub["measured_log_sha256"], sub["baseline_log_sha256"], raw["sample"]["games"]))
    w("| seat 1 moves first | `end.dispatch_order` = `%s` |" % sub["dispatch_order_first_round"])
    w("| harvest model is exact | model pickup %d = engine %d, model burn %d = engine %d, exact on %d/%d unit-rounds (map1 seed 0) |"
      % (sub["model_pickup_sum"], sub["engine_pickup_sum"], sub["model_burn_sum"],
         sub["engine_burn_sum"], sub["harvest_model_exact_unit_rounds"], sub["harvest_model_unit_rounds"]))
    w("| **selector replica is bit-exact** | predicted triple equals the `.so`'s emitted triple on **%d/%d = 100%%** steady-state unit-rounds; and driving the game with the replica at `threshold=2` from round 8 on reproduces the baseline **log byte for byte** (`log_sha256_equal` true on %d/%d games) |"
      % (sub["replica_match_steady"], sub["replica_total_steady"],
         sum(1 for g in blob["threshold"]["maps"].values() for r in g["2"]["records"] if r["log_sha256_equal"]),
         sum(1 for g in blob["threshold"]["maps"].values() for r in g["2"]["records"])))
    w("| fog discipline | `fog_discipline()` ran on **every** round of the selfcheck game (%d rounds) and never fired |" % sub["fog_discipline_rounds_checked"])
    w("| contention attribution is exact | third-party removal total = NPC + seat-2 pickups on **%d/%d** rounds |"
      % (fid["theft_identity_ok"], fid["theft_identity_total"]))
    w("| MECE | class residual **%d** on every map and pooled; `D3_arrived_empty` empirically 0 |" % pooled["class_residual"])
    w("| value model vs ground truth | %d of %d graded unit-rounds disagree (%.2f%%), **all** in the direction of the model under-stating pickup (%d under / %d over) -- a step-3 cell at Chebyshev 3 lies outside the radius-2 window, so fog hides its value.  Miss/hit is always taken from the log, never the model |"
      % (fid["model_truth_mismatch_unit_rounds"], pooled["graded_unit_rounds"],
         100 * fid["model_truth_mismatch_unit_rounds"] / pooled["graded_unit_rounds"],
         fid["model_under_truth_unit_rounds"], fid["model_over_truth_unit_rounds"]))
    w("")
    w("One host-build caveat, stated for completeness: the arm64 host takes the guarded scalar")
    w("fallback rather than the AVX2 path.  The two are behaviourally identical by inspection -- both")
    w("mark `v > 2` gold and `v == -3` bombs over the same clipped 5x5 window and reduce through the")
    w("same `TT.bestrow` table -- so target choice and bomb memory are unaffected; only latency is,")
    w("and latency is not the diagnostic target.  The `.so` is not byte-reproducible (Mach-O UUID),")
    w("but the *source* hash is pinned and verified.")
    w("")
    w("## 9. Bias register")
    w("")
    w("| number | direction | reason |")
    w("|---|---|---|")
    w("| simulator hit rate %.2f%% | **biased DOWN** ~%.1f pp | over-greedy local NPCs strip gold before we arrive; self-play against a full-strength clone |"
      % (100 * pooled["hit_rate_held_delta_pooled"], 100 * (ref["n36"]["ours_hit"] - pooled["hit_rate_held_delta_pooled"])))
    w("| per-map hit rates | **ordering inverted**, not merely shifted | see Sec 5; do not target a map from this report |")
    w("| class shares and pp b1/b2/b3 | unbiased *given* the trajectory (exact algebraic split, residual 0) but inherit the hit-rate bias in the denominator | |")
    w("| `B_SUPPLY` share | biased slightly **UP** | fog scores an invisible cell as 0, so %d/%d supply misses actually had reachable gold |"
      % (pooled["fogged_supply_unit_rounds"], pooled["classes"]["B_SUPPLY"]["miss_unit_rounds"]))
    w("| raw per-round gain sums | **biased UP, dominant** | stock/flow double-count; %.1f%% of the cellwise gold is *timing* |"
      % (100 * tot["gold_per_game_timing"] / max(1e-9, tot["gold_per_game_novel"] + tot["gold_per_game_timing"])))
    w("| novel gold %.1f/game | still biased **UP** | it ignores the positional cost of the detour, which Sec 3 prices |" % tot["gold_per_game_novel"])
    w("| burn-delta component | signed and exact | `gain == cellwise pickup gain + burn delta` by construction, residual 0 |")
    w("| contested share | **biased UP** | local NPCs over-eat by +24%..+71%, so third-party drain is over-counted |")
    w("| closed-loop paired deltas | trustworthy in **sign**; magnitude biased **UP** for the `free` variant | self-play redistribution + NPC bias over-values outer-ring routes |")
    w("| `thr0` negative verdict | **robust** | it agrees in sign with the platform note on `player.cpp:411`, and the free-and-still-worthless conclusion does not depend on magnitude |")
    w("")
    w("## 10. Reproduce")
    w("")
    w("```bash")
    w("git show f18064c:src/player.cpp > /tmp/gr_miss/player_f18064c.cpp")
    w("shasum -a 256 /tmp/gr_miss/player_f18064c.cpp   # 0ecce6fc...84fdd")
    w("cp /tmp/gr_path/shim.h /tmp/gr_miss/shim.h      # stubs the x86 prefetch tokens")
    w("clang++ -O2 -std=c++17 -shared -fPIC -I$PWD/src -include /tmp/gr_miss/shim.h \\")
    w("        -o /tmp/gr_miss/base.so /tmp/gr_miss/player_f18064c.cpp")
    w("")
    w("# substrate proofs (per map)")
    w("for m in map1 map2 map3; do")
    w("  python3 -m sim.analyze_miss_taxonomy selfcheck --map $m \\")
    w("          --base-so /tmp/gr_miss/base.so --seed 0 --out /tmp/gr_miss/selfcheck_$m.json")
    w("done")
    w("")
    w("# primary taxonomy: 3 maps x 5 seeds")
    w("python3 -m sim.analyze_miss_taxonomy taxonomy --map map1 --map map2 --map map3 \\")
    w("        --base-so /tmp/gr_miss/base.so --seeds 0 1 2 3 4 --jobs 4 \\")
    w("        --out /tmp/gr_miss/raw.json")
    w("")
    w("# closed loop: perfect free oracle, idle units only")
    w("for L in hold free; do")
    w("  python3 -m sim.analyze_miss_taxonomy realized --map map1 --map map2 --map map3 \\")
    w("          --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --level $L \\")
    w("          --out /tmp/gr_miss/realized_$L.json")
    w("done")
    w("")
    w("# closed loop: the zero-instruction gate change, with threshold=2 as the exactness control")
    w("for m in map1 map2 map3; do")
    w("  python3 -m sim.analyze_miss_taxonomy threshold --map $m \\")
    w("          --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --thresholds 2 1 0 \\")
    w("          --out /tmp/gr_miss/thr_$m.json")
    w("done")
    w("")
    w("# frontier table (hit vs yield vs opponent redistribution)")
    w("python3 -m sim.analyze_miss_taxonomy frontier --map map1 --map map2 --map map3 \\")
    w("        --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --out /tmp/gr_miss/frontier.json")
    w("")
    w("# final artifacts")
    w("python3 -m sim.analyze_miss_taxonomy finalize --raw /tmp/gr_miss/raw.json \\")
    w("        --realized /tmp/gr_miss/realized_hold.json --realized /tmp/gr_miss/realized_free.json \\")
    w("        --threshold /tmp/gr_miss/thr_map1.json --threshold /tmp/gr_miss/thr_map2.json \\")
    w("        --threshold /tmp/gr_miss/thr_map3.json --frontier /tmp/gr_miss/frontier.json \\")
    w("        --selfcheck /tmp/gr_miss/selfcheck_map1.json \\")
    w("        --out-json sim/reports/miss_taxonomy.json --out-md sim/reports/miss_taxonomy.md")
    w("```")
    w("")
    w("Wall clock on one arm64 host with a sibling agent competing for CPU: selfcheck ~10 s/map,")
    w("taxonomy 45 s at `--jobs 4`, each `realized` level ~60 s, threshold sweep ~90 s/map in parallel,")
    w("frontier 3 min.  Total under 8 minutes.")
    w("")
    w("## 11. Sample sizes")
    w("")
    w("| measurement | maps | seeds | games | unit-rounds |")
    w("|---|---|---|---|---|")
    w("| primary taxonomy | 3 | 5 (0-4) | %d measured + %d baseline | %d graded |"
      % (raw["sample"]["games"], raw["sample"]["games"], pooled["graded_unit_rounds"]))
    w("| closed loop, each of 2 oracle levels | 3 | 3 (0-2) | 9 variant + 9 baseline | -- |")
    w("| closed loop, each of 3 thresholds | 3 | 3 (0-2) | 9 variant + 9 baseline | -- |")
    w("| frontier table | 3 | 3 (0-2) | 27 variant + 9 baseline | -- |")
    w("| substrate selfcheck | 3 | 1 | 3 measured + 3 baseline | 3,000 |")
    w("")
    w("## 12. Corrections to the brief")
    w("")
    for item in blob["corrections"]:
        w("* %s" % item)
    w("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    run = sub.add_parser("taxonomy")
    run.add_argument("--map", action="append", dest="maps", default=None)
    run.add_argument("--base-so", required=True)
    run.add_argument("--seeds", nargs="+", default=["0"])
    run.add_argument("--jobs", type=int, default=1)
    run.add_argument("--no-trajectory-check", action="store_true")
    run.add_argument("--out", default=None)

    check = sub.add_parser("selfcheck")
    check.add_argument("--map", default="map1")
    check.add_argument("--base-so", required=True)
    check.add_argument("--seed", default="0")
    check.add_argument("--out", default=None)

    rep = sub.add_parser("report")
    rep.add_argument("--raw", required=True)
    rep.add_argument("--out", required=True)

    real = sub.add_parser("realized")
    real.add_argument("--map", action="append", dest="maps", default=None)
    real.add_argument("--base-so", required=True)
    real.add_argument("--seeds", nargs="+", default=["0"])
    real.add_argument("--level", choices=("hold", "free"), default="hold")
    real.add_argument("--out", default=None)

    thr = sub.add_parser("threshold")
    thr.add_argument("--map", action="append", dest="maps", default=None)
    thr.add_argument("--base-so", required=True)
    thr.add_argument("--seeds", nargs="+", default=["0"])
    thr.add_argument("--thresholds", nargs="+", type=int, default=[2, 0])
    thr.add_argument("--out", default=None)

    fro = sub.add_parser("frontier")
    fro.add_argument("--map", action="append", dest="maps", default=None)
    fro.add_argument("--base-so", required=True)
    fro.add_argument("--seeds", nargs="+", default=["0"])
    fro.add_argument("--out", default=None)

    fin = sub.add_parser("finalize")
    fin.add_argument("--raw", required=True)
    fin.add_argument("--realized", action="append", default=[])
    fin.add_argument("--threshold", action="append", default=[])
    fin.add_argument("--frontier", default=None)
    fin.add_argument("--selfcheck", default=None)
    fin.add_argument("--out-json", required=True)
    fin.add_argument("--out-md", required=True)

    args = parser.parse_args()
    if args.mode == "finalize":
        blob = finalize(
            Path(args.raw), [Path(item) for item in args.realized],
            [Path(item) for item in args.threshold],
            Path(args.frontier) if args.frontier else None,
            Path(args.selfcheck) if args.selfcheck else None,
        )
        Path(args.out_json).write_text(
            json.dumps(blob, indent=1, sort_keys=True, default=str) + "\n"
        )
        Path(args.out_md).write_text(render_markdown(blob))
        print("wrote", args.out_json, "and", args.out_md)
        return 0
    if args.mode == "taxonomy":
        result = analyze_taxonomy(
            args.maps or ["map1"], Path(args.base_so), args.seeds,
            jobs=max(1, args.jobs), trajectory_check=not args.no_trajectory_check,
        )
    elif args.mode == "realized":
        result = analyze_realized(
            args.maps or ["map1"], Path(args.base_so), args.seeds, level=args.level,
        )
    elif args.mode == "threshold":
        result = analyze_threshold(
            args.maps or ["map1"], Path(args.base_so), args.seeds, args.thresholds,
        )
    elif args.mode == "frontier":
        result = analyze_frontier(args.maps or ["map1"], Path(args.base_so), args.seeds)
    elif args.mode == "selfcheck":
        result = selfcheck(args.map, Path(args.base_so), args.seed)
    else:
        # ``report`` re-renders the markdown from an already-finalized blob, so
        # the prose can be regenerated without re-running any game.
        blob = json.loads(Path(args.raw).read_text())
        if "taxonomy" not in blob:
            raise SystemExit(
                "report expects a finalized blob (sim/reports/miss_taxonomy.json); "
                "run `finalize` first to merge the raw taxonomy with the closed-loop artifacts"
            )
        Path(args.out).write_text(render_markdown(blob))
        print("wrote", args.out)
        return 0

    text = json.dumps(result, indent=1, sort_keys=True, default=str)
    if getattr(args, "out", None):
        Path(args.out).write_text(text + "\n")
        print("wrote", args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
