#!/usr/bin/env python3
"""Is our abnormal move-order sensitivity an exploitable lever?

Question
========

On 30 archived ``f18064c`` map1 platform games the fog-free per-unit held-gold
channel says our income collapses harder than our opponents' when we move
second: 4.0793 -> 1.7128 gold/round (2.38x) against T-1/Tundra's 4.6740 ->
2.8336 (1.65x).  The hypothesised mechanism is that our income is *positional*
(we camp the central generation peak at (6,8)/(11,8)), so an opponent moving
first strips the peak cells before we arrive; the implied repair is target
selection that avoids cells a prior mover can take.

This driver tests that in four gated steps.

Step 1 -- information availability
    ``contract`` enumerates what the real ``GameInput`` (``src/game_api.h``,
    mirrored field-for-field in ``sim/abi.py``) actually exposes, and settles
    whether a mechanism can be same-round or must be lagged-adaptive.
    ``visibility`` quantifies, from our own seat's fogged view, how often a
    contestant is visible and close enough to take a cell we want.

Step 2 -- information-accessible share
    ``visibility`` also prices, in gold, the value that prior movers actually
    strip out of our reachable set, split by whether the stripping actor was
    inside our own 5x5 union at decision time.  That ratio is the accessible
    share, and the Step 3 bound is discounted by it.

Step 3 -- upper bound via oracle (labelled a BOUND, perfect information)
    ``bound`` measures closed-loop, same-seed paired net deltas.  The oracle is
    a *grid perturbation*: the real frozen ``.so`` is called with a ``GameInput``
    whose grid has been replaced by the true board as it will be **at our own
    dispatch turn** -- i.e. after the opponent and all seven NPCs have moved,
    when we are the slower player.  Every other part of the build (opening
    layer, fingerprint lock, anchors, LUT router, ``pass01`` gate, fold-back
    double-eat) is preserved bit-for-bit, so the measured delta is attributable
    to information about move order and nothing else.

Step 4 -- the cheap approximation
    ``bound`` also runs the same perturbation restricted to (a) cells inside our
    own visibility union and (b) no knowledge of this round's dispatch order, so
    the surviving fraction can be read directly off the closed loop.

Both order conditions are exercised separately, which no previous local A/B in
this project has done: ``sim/README.md`` Sec 1.1/1.2 recommends
``--fixed-costs 200,201`` and ``sim/reports/path_harvest_oracle.md`` Sec 1.2
made the choice explicit, so every earlier local measurement is a first-mover
measurement.  Here ``fixed_costs=(200, 201)`` puts our seat first and
``(201, 200)`` puts it second, at a fixed seat and an identical
``scenario_digest``.

Prophecy discipline
===================

When we are the *slower* player the engine settles ``(opponent, 7 NPCs, us)``,
and none of those nine actor-turns can depend on our current-round actions.  The
board we will face is therefore **exactly computable** before we decide:

* the opponent's decision is a pure function of ``player_input(opp)``, which this
  driver reconstructs with ``GameEngine.render_filtered_ground`` /
  ``visible_cells`` and feeds to a private copy of the opponent ``.so``;
* every NPC's decision is ``NPCModel(seed=_stable_seed("npc-policy", digest,
  round, npc_id))`` evaluated at its own dispatch turn on the *then-current*
  ground, exactly as ``sim/runner.py`` builds it;
* movement, pickup ``ceil(0.65 v)``, bomb consumption and trample are re-derived
  from ``GameEngine.execute_round``'s own ``execute_action``.

``verify`` proves the chain by predicting the whole round -- prior movers, us,
and post movers -- and comparing the predicted end-of-round ground against the
official log, cell by cell, in both order conditions.  It also proves the
passthrough arm reproduces the plain baseline's ``log_sha256`` byte for byte, and
that the oracle is *identical to the base* in the first-mover condition, where
by construction there is nothing to prophesy.

Simulator guardrails that bound what may be claimed
===================================================

The NPC model is over-greedy and over-central at ~39% per-action accuracy, so
central-efficiency gains are under-estimated locally and outer-ring routes
over-estimated; absolute income is not platform-comparable and only same-seed
paired deltas are; the simulator's hit rate runs ~3.8pp below platform and its
per-map ordering is inverted (Spearman -1), so per-map figures must not be used
to target a map.  Any positive is confirmed out of sample on disjoint seeds.

Modes
=====

``contract``    Step 1a: the ABI enumeration, the ``order``-field trap, and a
                measurement of how well order is inferable *after* the fact
``visibility``  Step 1b + Step 2: fog shares, contestability, accessible share
``verify``      fidelity gates (prophecy exactness, sha equality, null control)
``bound``       Step 3 + Step 4: closed-loop paired deltas, both order arms
``asymmetry``   does the simulator reproduce the platform's order asymmetry
``report``      render ``sim/reports/order_sensitivity.{md,json}``
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import engine as engine_module                       # noqa: E402
from sim.abi import SharedObjectStrategy                      # noqa: E402
from sim.analyze_path_oracle import ACTIONS, _sim             # noqa: E402  (read-only reuse)
from sim.engine import (                                      # noqa: E402
    GameEngine,
    PlayerInput,
    Position,
)
from sim.npc import NPCModel                                  # noqa: E402
from sim.runner import (                                      # noqa: E402
    _npc_order,
    _stable_seed,
    load_map,
    run_game,
)
from sim.scenario import ScenarioGenerator                     # noqa: E402

GRID = 17
NCELL = GRID * GRID
STAY = 4
FOG, WALL, BOMB = -5, -1, -3
PLAYER_MARK, NPC_MARK = -2, -4
VISION_RADIUS = 2
DR = (-1, 1, 0, 0, 0)
DC = (0, 0, -1, 1, 0)
ANCHORS = ((6, 8), (11, 8))     # frozen build: anch_r[u] = 6 + 5u, anch_c[u] = 8
ROUNDS = 500

# fixed_costs pairs.  The engine's rule is ``faster = 1 if costs[1] <= costs[2]``,
# so (200, 201) makes seat 1 the faster player and (201, 200) makes it the slower
# one.  Same seat, same scenario_digest, only the dispatch order changes.
COSTS_WE_FIRST = (200, 201)
COSTS_WE_SECOND = (201, 200)


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
        "n": n,
        "mean": mean,
        "sd": sd,
        "se": se,
        "sigma": (mean / se) if se > 0 else None,
        "min": min(values),
        "max": max(values),
    }


# ---------------------------------------------------------------------------
# Step 1a -- what the official contract actually exposes
# ---------------------------------------------------------------------------

# Enumerated from src/game_api.h and cross-checked against the byte offsets that
# sim/abi.py asserts at import time (verify_abi_layout).
GAME_INPUT_FIELDS = (
    ("round", "int", "current round from 0", "no order information"),
    ("grid", "int[17][17]",
     "fogged pure terrain: -5 fog, -3 bomb, -1 wall, 0 empty, >=1 gold",
     "actors are NOT marked in the grid, so a drained cell is indistinguishable "
     "from a cell that never held gold"),
    ("my_units", "Position[2]", "our two unit cells", "no order information"),
    ("my_units_gold", "int[2]", "per-unit held gold", "no order information"),
    ("gold_opp", "int", "opponent's two units summed, fog-free",
     "a LAGGED channel: its round-over-round increment is the opponent's income "
     "last round, not this round's order"),
    ("visible_enemies", "Position[2]",
     "enemy cells inside our own 5x5 union, compacted, identity withheld",
     "(-1,-1) when hidden; cannot tell which enemy unit is which"),
    ("num_visible_npcs", "int", "count of valid visible_npcs entries", "-"),
    ("visible_npcs", "NpcInfo[7]",
     "visible NPC id+cell; id is stable across rounds",
     "fogged the same way; tail padded id=0 pos=(-1,-1)"),
    ("snapshot_valid", "int", "1 when a fresh five-region snapshot is attached", "-"),
    ("snapshot", "Snapshot",
     "per-region enter/leave/gold_generated/gold_collected/gold_remaining/occupants "
     "over a closed window [window_begin, window_end]",
     "fog-free but WINDOWED and LAGGED: the window has already closed when we "
     "read it, so it can never describe the current round's order"),
)

GAME_OUTPUT_FIELDS = (
    ("actions", "int[6]", "six steps, each in 0..4"),
    ("k", "int", "split point: unit 0 walks actions[0..k-1], unit 1 walks actions[k..5]"),
    ("order", "int",
     "*** THE TRAP *** 0 = OUR unit 0 steps first, 1 = OUR unit 1 steps first. "
     "This is our own intra-seat unit sequencing. It is an OUTPUT we choose, it "
     "is not the engine's player dispatch order, and it carries no information "
     "about which seat moves first. f18064c sets it from held gold: "
     "`out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1`"),
    ("vp", "int", "vision purchase 0/1/2, billed at game end, effective next round"),
)


def contract() -> Mapping[str, Any]:
    """Step 1a: enumerate the contract and settle same-round vs lagged."""
    from sim import abi

    layout = {
        name: {
            "size": size,
            "offsets": dict(offsets),
        }
        for structure, (size, offsets) in abi._EXPECTED_LAYOUT.items()
        for name in (structure.__name__,)
    }
    header = (ROOT / "src" / "game_api.h").read_text()
    return {
        "abi_layout_verified_at_import": True,
        "abi_layout": layout,
        "game_input_fields": [
            {"field": f, "type": t, "meaning": m, "order_content": o}
            for f, t, m, o in GAME_INPUT_FIELDS
        ],
        "game_output_fields": [
            {"field": f, "type": t, "meaning": m} for f, t, m in GAME_OUTPUT_FIELDS
        ],
        "header_declares_only_movedecision": "GameOutput moveDecision(const GameInput* input)" in header,
        "dispatch_order_field_present": False,
        "opponent_decision_cost_present": False,
        "answer": (
            "NO. GameInput has exactly ten fields and not one of them carries the "
            "engine's player dispatch order for the current round. The engine "
            "decides it by comparing the two decision costs, which are produced "
            "*by* our own call and the opponent's call and are therefore not "
            "available to either caller at decision time. GameOutput.order is our "
            "own intra-seat unit sequencing, an output we choose, not the dispatch "
            "order. Therefore no same-round mechanism conditioned on dispatch order "
            "is expressible: any such mechanism must be LAGGED-ADAPTIVE."
        ),
        "lagged_channels": [
            "gold_opp increments -- the opponent's income last round, fog-free",
            "grid deltas on remembered cells -- a cell we saw hold gold last round "
            "and see empty this round was drained by somebody",
            "snapshot gold_collected per region -- fog-free but the window has "
            "already closed",
        ],
    }


# ---------------------------------------------------------------------------
# exact within-round world simulation, ported from GameEngine.execute_round
# ---------------------------------------------------------------------------


class WorldSim:
    """Mutable copy of one round's board and actor positions.

    ``step`` is a line-by-line port of ``GameEngine.execute_round``'s inner
    ``execute_action``: bounds, wall and (players only) player-cell collision
    skip the step and leave the actor in place; a completed move picks up
    ``ceil(0.65 v)``, then consumes a bomb (penalty for players only), then pays
    the >=3-NPC trample (players only).

    ``taken`` accumulates ``(kind, actor, cell, base_value, amount)`` for every
    pickup in dispatch order, which is what makes per-actor gold attribution
    exact rather than inferred from a start/end grid difference.
    """

    __slots__ = ("board", "ppos", "npos", "held", "pickup", "taken", "entered")

    def __init__(self, ground, players, npcs):
        self.board = [list(row) for row in ground]
        self.ppos = {}
        self.held = {}
        self.pickup = {}
        self.taken: list[tuple[str, Any, tuple[int, int], int, int]] = []
        self.entered: dict[Any, list[tuple[int, int]]] = {}
        for player in players:
            for unit in player.units:
                self.ppos[(player.id, unit.index)] = unit.position.cell
                self.held[(player.id, unit.index)] = unit.gold
                self.pickup[(player.id, unit.index)] = 0
                self.entered[(player.id, unit.index)] = []
        self.npos = {npc.id: npc.position.cell for npc in npcs}

    def flat(self) -> list[int]:
        return [self.board[r][c] for r in range(GRID) for c in range(GRID)]

    def _player_blockers(self, key):
        return {cell for other, cell in self.ppos.items() if other != key}

    def step_player(self, key, action):
        row, col = self.ppos[key]
        if action != STAY:
            nrow, ncol = row + DR[action], col + DC[action]
            if not (0 <= nrow < GRID and 0 <= ncol < GRID):
                return
            if self.board[nrow][ncol] == WALL:
                return
            if (nrow, ncol) in self._player_blockers(key):
                return
            row, col = nrow, ncol
            self.ppos[key] = (row, col)
        else:
            return
        self.entered[key].append((row, col))
        value = self.board[row][col]
        if value > 0:
            amount = (65 * value + 99) // 100
            self.board[row][col] = value - amount
            self.held[key] += amount
            self.pickup[key] += amount
            self.taken.append(("player", key, (row, col), value, amount))
        if self.board[row][col] == BOMB:
            self.board[row][col] = 0
            penalty = (self.held[key] + 9) // 10
            self.held[key] -= penalty
        if sum(1 for cell in self.npos.values() if cell == (row, col)) >= 3:
            penalty = (self.held[key] + 19) // 20
            self.held[key] -= penalty

    def step_npc(self, npc_id, action):
        row, col = self.npos[npc_id]
        if action == STAY:
            return
        nrow, ncol = row + DR[action], col + DC[action]
        if not (0 <= nrow < GRID and 0 <= ncol < GRID):
            return
        if self.board[nrow][ncol] == WALL:
            return
        row, col = nrow, ncol
        self.npos[npc_id] = (row, col)
        value = self.board[row][col]
        if value > 0:
            amount = (65 * value + 99) // 100
            self.board[row][col] = value - amount
            self.taken.append(("npc", npc_id, (row, col), value, amount))
        if self.board[row][col] == BOMB:
            self.board[row][col] = 0

    def run_player(self, player_id, actions, k, order):
        assigned = {0: tuple(actions[:k]), 1: tuple(actions[k:])}
        for unit_index in (order, 1 - order):
            for action in assigned[unit_index]:
                self.step_player((player_id, unit_index), action)

    def run_npc(self, npc_id, policy):
        ground = tuple(tuple(row) for row in self.board)
        row, col = self.npos[npc_id]
        actions = policy(npc_id, ground, Position(row, col))
        for action in actions:
            self.step_npc(npc_id, action)


def npc_policy_for(digest: str, round_number: int):
    """The exact callback ``sim/runner._npc_policy`` hands the engine."""

    def decide(npc_id, current_ground, current_position):
        seed = _stable_seed("npc-policy", digest, round_number, npc_id)
        return NPCModel(seed=seed).actions(current_ground, current_position, npc_id=npc_id)

    return decide


def opponent_view(start, opp_id: int) -> PlayerInput:
    """Rebuild ``GameEngine.player_input(opp_id, start)`` without the engine.

    ``player_input`` refuses to run off a start object the engine no longer
    holds pending, but every ingredient is a class/static method plus fields of
    the frozen ``RoundStart``, so the reconstruction is exact.
    """
    state = start.state
    owner = state.player(opp_id)
    other = state.player(3 - opp_id)
    visible = GameEngine.visible_cells(
        (unit.position for unit in owner.units), owner.vision_radius
    )
    enemies = tuple(
        unit.position if unit.position.cell in visible else None for unit in other.units
    )
    visible_npcs = tuple(
        (npc.id, npc.position) for npc in state.npcs if npc.position.cell in visible
    )
    return PlayerInput(
        round=start.round,
        grid=GameEngine.render_filtered_ground(state, opp_id),
        my_units=(owner.units[0].position, owner.units[1].position),
        my_units_gold=(owner.units[0].gold, owner.units[1].gold),
        gold_opp=other.gold,
        visible_enemies=enemies,       # type: ignore[arg-type]
        visible_npcs=visible_npcs,
        snapshot=start.snapshot,
        start=start,
    )


def visible_union(units: Sequence[tuple[int, int]], radius: int = VISION_RADIUS) -> bytearray:
    mask = bytearray(NCELL)
    for row, col in units:
        for vrow in range(max(0, row - radius), min(GRID, row + radius + 1)):
            base = vrow * GRID
            for vcol in range(max(0, col - radius), min(GRID, col + radius + 1)):
                mask[base + vcol] = 1
    return mask


# ---------------------------------------------------------------------------
# the oracle: a grid perturbation, everything else the frozen build
# ---------------------------------------------------------------------------


class OrderOracleStrategy:
    """Call the real frozen ``.so`` with a re-informed grid.

    ``level``
        ``passthrough``   unmodified grid; must reproduce the plain baseline's
                          ``log_sha256`` byte for byte (fidelity gate)
        ``prophet``       BOUND. Every cell **inside our own 5x5 union** is
                          replaced by its true value at our own dispatch turn,
                          i.e. after the opponent and all seven NPCs have moved
                          when we are the slower player. Perfect prophecy,
                          realistic *shape* (we still only act on what we can
                          see). Identical to ``passthrough`` when we move first.
        ``prophet_free``  BOUND, looser. The whole true board at our dispatch
                          turn, fog removed. Also prices vision.
        ``cheap``         Step 4. Visible information only and NO knowledge of
                          this round's dispatch order: every visible cell that a
                          *visible* contestant could reach in three steps is
                          discounted by ``risk`` (a value multiplier), which is
                          the only expressible hedge when order is unknown.
        ``cheap_lagged``  ``cheap`` plus the lagged order estimate: the hedge is
                          applied only when last round's evidence says we were
                          probably second (see ``lag_infer``).

    Not a strategy proposal: ``prophet`` needs the opponent's decision and all
    seven NPC rolls for the current round, which no submitted ``.so`` can obtain.
    """

    name = "order_oracle"

    def __init__(
        self,
        base_so: Path,
        opponent_so: Path,
        *,
        seat: int,
        we_move_first: bool,
        digest: str,
        npc_ids: Sequence[int],
        level: str = "passthrough",
        risk: float = 1.0,
        record: bool = False,
    ) -> None:
        self.base = SharedObjectStrategy(base_so, name="order_base")
        self.oracle_opp = SharedObjectStrategy(opponent_so, name="order_opp_probe")
        self.seat = int(seat)
        self.opp_seat = 3 - int(seat)
        self.we_move_first = bool(we_move_first)
        self.digest = digest
        self.npc_ids = tuple(npc_ids)
        self.level = level
        self.risk = float(risk)
        self.record = record
        self.rows: list[dict[str, Any]] = []
        self.perturbed_rounds = 0
        self.perturbed_cells = 0
        self.rounds = 0
        self.predict_ok = 0
        self.predict_total = 0
        self.mismatch_examples: list[dict[str, Any]] = []
        self._last_probe_round = -1
        self._lag_state = {"prev_seen": None, "prev_targets": None, "was_second": None}

    def close(self) -> None:
        self.base.close()
        self.oracle_opp.close()

    # -- prophecy ---------------------------------------------------------
    def _prior_mover_board(self, start) -> tuple[list[list[int]], dict[str, Any]]:
        """The true board at our own dispatch turn, plus prophecy bookkeeping."""
        sim = WorldSim(start.state.ground, start.state.players, start.state.npcs)
        info: dict[str, Any] = {"opp_actions": None, "npc_order": None}
        if self.we_move_first:
            return sim.board, info
        opp_view = opponent_view(start, self.opp_seat)
        decision = self.oracle_opp(opp_view)
        info["opp_actions"] = (tuple(int(a) for a in decision.actions), int(decision.k), int(decision.order))
        sim.run_player(self.opp_seat, decision.actions, int(decision.k), int(decision.order))
        order = _npc_order(self.npc_ids, self.digest, start.round)
        info["npc_order"] = order
        policy = npc_policy_for(self.digest, start.round)
        for npc_id in order:
            sim.run_npc(npc_id, policy)
        return sim.board, info

    # -- lagged order inference ------------------------------------------
    def _lag_infer(self, grid, my_units) -> bool | None:
        """Were we probably second *last* round?

        The only same-round-free signal available to a real strategy: a cell we
        saw holding gold last round, inside the cells we could reach, that is
        empty now without us having taken it.  Returns None on the first round.
        """
        prev = self._lag_state["prev_seen"]
        current = {}
        mask = visible_union(my_units)
        for row in range(GRID):
            for col in range(GRID):
                cell = row * GRID + col
                if mask[cell]:
                    current[cell] = int(grid[row][col])
        self._lag_state["prev_seen"] = current
        if prev is None:
            return None
        drained = 0
        kept = 0
        for cell, value in prev.items():
            if value > 2 and cell in current:
                if current[cell] <= 0:
                    drained += 1
                else:
                    kept += 1
        if drained + kept == 0:
            return None
        return drained > kept

    # -- grid construction ------------------------------------------------
    def _perturbed_grid(self, value, start):
        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        if self.level == "passthrough":
            return grid, 0
        if self.level in ("prophet", "prophet_free"):
            board, _info = self._prior_mover_board(start)
            mask = visible_union(my_units)
            changed = 0
            for row in range(GRID):
                for col in range(GRID):
                    cell = row * GRID + col
                    if self.level == "prophet" and not mask[cell]:
                        continue
                    truth = board[row][col]
                    if self.level == "prophet_free":
                        new = truth
                    else:
                        new = truth
                    if grid[row][col] != new:
                        grid[row][col] = new
                        changed += 1
            return grid, changed
        # cheap levels: visible information only, no dispatch-order knowledge
        hedge = True
        if self.level == "cheap_lagged":
            guess = self._lag_infer(value.grid, my_units)
            hedge = bool(guess)
        else:
            self._lag_infer(value.grid, my_units)
        if not hedge:
            return grid, 0
        contest = []
        for pos in value.visible_enemies:
            if pos is not None and int(pos.row) >= 0:
                contest.append((int(pos.row), int(pos.col)))
        for _npc_id, pos in value.visible_npcs:
            if int(pos.row) >= 0:
                contest.append((int(pos.row), int(pos.col)))
        if not contest:
            return grid, 0
        changed = 0
        for row in range(GRID):
            for col in range(GRID):
                if grid[row][col] <= 0:
                    continue
                for crow, ccol in contest:
                    if abs(crow - row) + abs(ccol - col) <= 3:
                        new = int(grid[row][col] * self.risk)
                        if new != grid[row][col]:
                            grid[row][col] = new
                            changed += 1
                        break
        return grid, changed

    def __call__(self, value: Any) -> tuple[int, ...]:
        start = value.start
        self.rounds += 1
        grid, changed = self._perturbed_grid(value, start)
        if changed:
            self.perturbed_rounds += 1
            self.perturbed_cells += changed
        shim = _RewrittenInput(value, grid)
        decision = self.base(shim)
        actions = tuple(int(item) for item in decision.actions)
        out = actions + (int(decision.k), int(decision.order), int(decision.vp))
        if self.record:
            self.rows.append({
                "round": int(value.round),
                "changed_cells": changed,
                "actions": actions,
                "k": int(decision.k),
                "order": int(decision.order),
            })
        return out


def _RewrittenInput(inner: PlayerInput, grid) -> PlayerInput:
    """A real ``PlayerInput`` whose grid is replaced and nothing else.

    ``sim.abi.player_input_to_abi`` type-checks ``isinstance(value, PlayerInput)``
    on purpose, so the perturbation has to be a genuine frozen-dataclass copy
    rather than a duck-typed proxy.  Every other field, including ``start``, is
    carried across unchanged by ``dataclasses.replace``.
    """
    return dataclasses.replace(
        inner, grid=tuple(tuple(int(v) for v in row) for row in grid)
    )


# ---------------------------------------------------------------------------
# fidelity gates
# ---------------------------------------------------------------------------


def _pure_ground(grid) -> list[list[int]]:
    """Invert ``render_full``: the -2/-4 marks only ever overwrite ground 0."""
    return [
        [0 if int(v) in (PLAYER_MARK, NPC_MARK, FOG) else int(v) for v in row]
        for row in grid
    ]


class ProphecyProbe:
    """Passthrough seat that predicts the WHOLE round and checks it against the log.

    Predicts prior movers, then us with the base's own emitted actions, then post
    movers, and stores the predicted end-of-round pure ground.  ``verify``
    compares it against the official log's ``end.grid``.
    """

    name = "prophecy_probe"

    def __init__(self, base_so: Path, *, seat: int, we_move_first: bool,
                 digest: str, npc_ids: Sequence[int]) -> None:
        self.base = SharedObjectStrategy(base_so, name="probe_base")
        self.probe = SharedObjectStrategy(base_so, name="probe_opp")
        self.seat = int(seat)
        self.opp_seat = 3 - int(seat)
        self.we_move_first = bool(we_move_first)
        self.digest = digest
        self.npc_ids = tuple(npc_ids)
        self.predicted: dict[int, list[list[int]]] = {}
        self.pre_us_board: dict[int, list[list[int]]] = {}

    def close(self) -> None:
        self.base.close()
        self.probe.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        start = value.start
        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)

        sim = WorldSim(start.state.ground, start.state.players, start.state.npcs)
        policy = npc_policy_for(self.digest, start.round)
        npc_seq = _npc_order(self.npc_ids, self.digest, start.round)
        opp_view = opponent_view(start, self.opp_seat)
        opp_decision = self.probe(opp_view)

        if self.we_move_first:
            sim.run_player(self.seat, actions, k, order)
            self.pre_us_board[start.round] = [list(row) for row in start.state.ground]
            for npc_id in npc_seq:
                sim.run_npc(npc_id, policy)
            sim.run_player(self.opp_seat, opp_decision.actions,
                           int(opp_decision.k), int(opp_decision.order))
        else:
            sim.run_player(self.opp_seat, opp_decision.actions,
                           int(opp_decision.k), int(opp_decision.order))
            for npc_id in npc_seq:
                sim.run_npc(npc_id, policy)
            self.pre_us_board[start.round] = [list(row) for row in sim.board]
            sim.run_player(self.seat, actions, k, order)
        self.predicted[start.round] = [list(row) for row in sim.board]
        return actions + (k, order, vp)


def verify(map_name: str, base_so: Path, seeds: Sequence[str]) -> Mapping[str, Any]:
    """Prove prophecy exactness, sha equality and the first-mover null control."""
    map_definition = load_map(map_name)
    npc_ids = engine_module.DEFAULT_NPC_IDS
    out: dict[str, Any] = {"map": map_name, "seeds": list(seeds), "cases": []}
    for seed in seeds:
        digest = ScenarioGenerator(map_definition, str(seed)).digest
        for label, costs, we_first in (
            ("we_first", COSTS_WE_FIRST, True),
            ("we_second", COSTS_WE_SECOND, False),
        ):
            plain = run_game(
                base_so, base_so, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            probe = ProphecyProbe(base_so, seat=1, we_move_first=we_first,
                                  digest=digest, npc_ids=npc_ids)
            probed = run_game(
                probe, base_so, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            rounds = [json.loads(line) for line in probed.log_bytes.decode().splitlines()[2:] if line.strip()]
            exact = 0
            bad: list[int] = []
            for record in rounds:
                number = int(record["round"])
                truth = _pure_ground(record["end"]["grid"])
                predicted = probe.predicted.get(number)
                if predicted is not None and predicted == truth:
                    exact += 1
                elif predicted is not None:
                    bad.append(number)
            through = OrderOracleStrategy(
                base_so, base_so, seat=1, we_move_first=we_first, digest=digest,
                npc_ids=npc_ids, level="passthrough",
            )
            passthrough = run_game(
                through, base_so, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            through.close()
            prophet = OrderOracleStrategy(
                base_so, base_so, seat=1, we_move_first=we_first, digest=digest,
                npc_ids=npc_ids, level="prophet",
            )
            oracle = run_game(
                prophet, base_so, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            perturbed_rounds = prophet.perturbed_rounds
            perturbed_cells = prophet.perturbed_cells
            prophet.close()
            probe.close()
            out["cases"].append({
                "seed": str(seed),
                "arm": label,
                "we_move_first": we_first,
                "fixed_costs": list(costs),
                "prophecy_exact_rounds": exact,
                "prophecy_rounds": len(rounds),
                "prophecy_mismatch_rounds": bad[:10],
                "plain_sha": plain.summary["log_sha256"],
                "probe_sha": probed.summary["log_sha256"],
                "passthrough_sha": passthrough.summary["log_sha256"],
                "oracle_sha": oracle.summary["log_sha256"],
                "passthrough_sha_equal": passthrough.summary["log_sha256"] == plain.summary["log_sha256"],
                "probe_sha_equal": probed.summary["log_sha256"] == plain.summary["log_sha256"],
                "oracle_identical_to_base": oracle.summary["log_sha256"] == plain.summary["log_sha256"],
                "oracle_perturbed_rounds": perturbed_rounds,
                "oracle_perturbed_cells": perturbed_cells,
                "plain_net": int(plain.summary["players"]["1"]["net_gold"]),
                "oracle_net": int(oracle.summary["players"]["1"]["net_gold"]),
            })
    cases = out["cases"]
    out["gates"] = {
        "prophecy_exact_all": all(c["prophecy_exact_rounds"] == c["prophecy_rounds"] for c in cases),
        "prophecy_exact_rounds": sum(c["prophecy_exact_rounds"] for c in cases),
        "prophecy_total_rounds": sum(c["prophecy_rounds"] for c in cases),
        "passthrough_sha_all_equal": all(c["passthrough_sha_equal"] for c in cases),
        "probe_sha_all_equal": all(c["probe_sha_equal"] for c in cases),
        "first_mover_null_control": all(
            c["oracle_identical_to_base"] for c in cases if c["we_move_first"]
        ),
        "second_mover_oracle_does_perturb": all(
            c["oracle_perturbed_rounds"] > 0 for c in cases if not c["we_move_first"]
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Step 1b + Step 2 -- visibility, contestability, accessible share
# ---------------------------------------------------------------------------


class VisibilityCensus:
    """Passthrough seat that measures what we can see and what it is worth.

    For every round it records, from **our own fogged view**:

    * whether at least one opponent unit is visible;
    * how many NPCs are visible;
    * whether a visible contestant is within Manhattan 3 of a cell our units
      will actually enter (a contestable target);
    * the same two questions against the fog-free truth, so the ratio is the
      information-accessible share of the contestability signal;
    * and, when we move second, the gold that prior movers actually removed from
      the cells we then entered -- split by whether the removing actor was
      inside our own 5x5 union at decision time.
    """

    name = "visibility_census"

    def __init__(self, base_so: Path, *, seat: int, we_move_first: bool,
                 digest: str, npc_ids: Sequence[int],
                 opponent_probe: Path | None = None) -> None:
        self.base = SharedObjectStrategy(base_so, name="census_base")
        self.probe = SharedObjectStrategy(
            base_so if opponent_probe is None else opponent_probe, name="census_opp")
        self.seat = int(seat)
        self.opp_seat = 3 - int(seat)
        self.we_move_first = bool(we_move_first)
        self.digest = digest
        self.npc_ids = tuple(npc_ids)
        self.rows: list[dict[str, Any]] = []

    def close(self) -> None:
        self.base.close()
        self.probe.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        start = value.start
        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)

        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        mask = visible_union(my_units)
        seen_enemies = [
            (int(pos.row), int(pos.col)) for pos in value.visible_enemies
            if pos is not None and int(pos.row) >= 0
        ]
        seen_npcs = [
            (int(pos.row), int(pos.col)) for _i, pos in value.visible_npcs
            if int(pos.row) >= 0
        ]
        state = start.state
        true_enemies = [unit.position.cell for unit in state.player(self.opp_seat).units]
        true_npcs = [npc.position.cell for npc in state.npcs]

        # --- what our units will actually enter (fog-free truth of our own path)
        sim_us = WorldSim(state.ground, state.players, state.npcs)
        before = {key: cell for key, cell in sim_us.ppos.items()}
        entered: list[tuple[int, int]] = []
        assigned = {0: actions[:k], 1: actions[k:]}
        for unit_index in (order, 1 - order):
            key = (self.seat, unit_index)
            for action in assigned[unit_index]:
                pre = sim_us.ppos[key]
                sim_us.step_player(key, action)
                post = sim_us.ppos[key]
                if post != pre:
                    entered.append(post)
        del before

        def near(actors, cells, radius=3):
            for arow, acol in actors:
                for crow, ccol in cells:
                    if abs(arow - crow) + abs(acol - ccol) <= radius:
                        return True
            return False

        # --- prior-mover strip, and how much of it we could have seen coming
        strip_total = 0
        strip_visible_actor = 0
        strip_by_npc = 0
        strip_by_opp = 0
        strip_cells = 0
        if not self.we_move_first:
            sim = WorldSim(state.ground, state.players, state.npcs)
            opp_view = opponent_view(start, self.opp_seat)
            opp_decision = self.probe(opp_view)
            drained: dict[tuple[int, int], list[tuple[str, tuple[int, int]]]] = {}
            pre_positions = {
                "opp": [unit.position.cell for unit in state.player(self.opp_seat).units],
                "npc": [npc.position.cell for npc in state.npcs],
            }
            board0 = [list(row) for row in state.ground]
            sim.run_player(self.opp_seat, opp_decision.actions, int(opp_decision.k),
                           int(opp_decision.order))
            for row in range(GRID):
                for col in range(GRID):
                    if sim.board[row][col] != board0[row][col]:
                        drained.setdefault((row, col), []).append(("opp", (row, col)))
            snapshot = [list(row) for row in sim.board]
            policy = npc_policy_for(self.digest, start.round)
            for npc_id in _npc_order(self.npc_ids, self.digest, start.round):
                pre = sim.npos[npc_id]
                sim.run_npc(npc_id, policy)
                for row in range(GRID):
                    for col in range(GRID):
                        if sim.board[row][col] != snapshot[row][col]:
                            drained.setdefault((row, col), []).append(("npc", pre))
                            snapshot[row][col] = sim.board[row][col]
            for cell in entered:
                row, col = cell
                base_value = board0[row][col]
                now = sim.board[row][col]
                if base_value > 0 and now < base_value:
                    lost = ((65 * base_value + 99) // 100) - (
                        ((65 * now + 99) // 100) if now > 0 else 0)
                    if lost <= 0:
                        continue
                    strip_total += lost
                    strip_cells += 1
                    actors = drained.get(cell, [])
                    if any(kind == "npc" for kind, _p in actors):
                        strip_by_npc += lost
                    if any(kind == "opp" for kind, _p in actors):
                        strip_by_opp += lost
                    accessible = False
                    for kind, origin in actors:
                        orow, ocol = origin
                        if mask[orow * GRID + ocol] and abs(orow - row) + abs(ocol - col) <= 3:
                            accessible = True
                            break
                    if accessible:
                        strip_visible_actor += lost
            del pre_positions

        self.rows.append({
            "round": int(value.round),
            "any_enemy_visible": bool(seen_enemies),
            "both_enemies_visible": len(seen_enemies) == 2,
            "visible_npcs": len(seen_npcs),
            "any_npc_visible": bool(seen_npcs),
            "entered_cells": len(entered),
            "target_contestable_visible": near(seen_enemies + seen_npcs, entered),
            "target_contestable_true": near(
                [divmod(c, 1)[0] for c in []] or [(r, c) for r, c in true_enemies] +
                [(r, c) for r, c in true_npcs], entered),
            "target_contestable_visible_enemy_only": near(seen_enemies, entered),
            "target_contestable_true_enemy_only": near(
                [(r, c) for r, c in true_enemies], entered),
            "strip_total": strip_total,
            "strip_visible_actor": strip_visible_actor,
            "strip_by_npc": strip_by_npc,
            "strip_by_opp": strip_by_opp,
            "strip_cells": strip_cells,
        })
        return actions + (k, order, vp)


def visibility(map_name: str, base_so: Path, seeds: Sequence[str],
               *, opponent: str = "self") -> Mapping[str, Any]:
    """Fog shares and contestability.

    ``opponent="self"`` runs the frozen build on both seats.  That number is
    **not usable for opponent visibility**: both seats camp the identical
    central anchors ``(6,8)``/``(11,8)``, so the four units are permanently
    inside each other's 5x5 windows and enemy visibility is inflated to ~93%.
    ``opponent="monotone"`` runs the fitted non-camping reference instead, which
    is the honest condition for a visibility share.
    """
    map_definition = load_map(map_name)
    npc_ids = engine_module.DEFAULT_NPC_IDS
    arms: dict[str, list[dict[str, Any]]] = {"we_first": [], "we_second": []}
    for seed in seeds:
        digest = ScenarioGenerator(map_definition, str(seed)).digest
        for label, costs, we_first in (
            ("we_first", COSTS_WE_FIRST, True),
            ("we_second", COSTS_WE_SECOND, False),
        ):
            other = base_so if opponent == "self" else MonotoneHarvester()
            census = VisibilityCensus(base_so, seat=1, we_move_first=we_first,
                                      digest=digest, npc_ids=npc_ids,
                                      opponent_probe=base_so)
            run_game(
                census, other, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            arms[label].extend(census.rows)
            census.close()
            if opponent != "self":
                other.close()

    def fold(rows: Sequence[Mapping[str, Any]], steady_from: int = 8) -> Mapping[str, Any]:
        rows = [row for row in rows if row["round"] >= steady_from]
        n = len(rows)
        if not n:
            return {"rounds": 0}
        total_strip = sum(row["strip_total"] for row in rows)
        visible_strip = sum(row["strip_visible_actor"] for row in rows)
        return {
            "rounds": n,
            "any_enemy_visible": sum(row["any_enemy_visible"] for row in rows) / n,
            "both_enemies_visible": sum(row["both_enemies_visible"] for row in rows) / n,
            "no_enemy_visible": 1.0 - sum(row["any_enemy_visible"] for row in rows) / n,
            "any_npc_visible": sum(row["any_npc_visible"] for row in rows) / n,
            "mean_visible_npcs": _mean([row["visible_npcs"] for row in rows]),
            "target_contestable_visible": sum(row["target_contestable_visible"] for row in rows) / n,
            "target_contestable_true": sum(row["target_contestable_true"] for row in rows) / n,
            "target_contestable_visible_enemy_only":
                sum(row["target_contestable_visible_enemy_only"] for row in rows) / n,
            "target_contestable_true_enemy_only":
                sum(row["target_contestable_true_enemy_only"] for row in rows) / n,
            "contestability_visible_share": (
                sum(row["target_contestable_visible"] for row in rows)
                / max(1, sum(row["target_contestable_true"] for row in rows))
            ),
            "strip_gold_per_round": total_strip / n,
            "strip_gold_per_game": total_strip / n * ROUNDS,
            "strip_cells_per_round": sum(row["strip_cells"] for row in rows) / n,
            "strip_by_npc_share": (sum(row["strip_by_npc"] for row in rows) / total_strip
                                   if total_strip else None),
            "strip_by_opp_share": (sum(row["strip_by_opp"] for row in rows) / total_strip
                                   if total_strip else None),
            "accessible_share_of_strip": (visible_strip / total_strip) if total_strip else None,
            "strip_gold_visible_per_game": visible_strip / n * ROUNDS,
        }

    return {
        "map": map_name,
        "seeds": [str(s) for s in seeds],
        "we_first": fold(arms["we_first"]),
        "we_second": fold(arms["we_second"]),
        "note": (
            "'contestable' = a contestant within Manhattan 3 of a cell our unit "
            "actually entered, ignoring walls, which over-counts and is therefore "
            "an upper bound on contestability.  'strip' = gold that prior movers "
            "removed from cells our units then entered, in units of the pickup we "
            "would have taken; it is zero by construction when we move first."
        ),
    }


# ---------------------------------------------------------------------------
# Step 0 -- where does the benefit land, and is the sensitivity really about order
# ---------------------------------------------------------------------------

# The frozen build's own 5x5 ring-distance priority table, imported from the
# bit-exact replica that reproduced the baseline log byte for byte.
from sim.analyze_miss_taxonomy import (          # noqa: E402  (read-only reuse)
    BuildState,
    replica_decide_unit,
)


class Step0Census:
    """Attribute every gold pickup in the round to an actor, in dispatch order.

    Runs the *exact* validated full-round predictor (``verify`` proves the
    predicted end-of-round pure ground equals the official log on 1000/1000
    rounds) and records, per unit-round:

    * the frozen selector's own target cell, and its fate;
    * the cells the unit actually entered, and for each, how much gold a prior
      mover had already removed **within this round** (structurally zero when we
      move first, because ``GameEngine._dispatch`` emits the faster player as a
      single actor entry -- our whole two-unit turn completes before NPC 1);
    * local supply, both as the build sees it (fogged 5x5) and as truth has it;
    * per-actor-class gold consumption inside our own visibility union, which is
      the **between-round** channel and is order-independent.
    """

    name = "step0_census"

    def __init__(self, base_so: Path, *, seat: int, we_move_first: bool,
                 digest: str, npc_ids: Sequence[int], walls: frozenset) -> None:
        self.base = SharedObjectStrategy(base_so, name="step0_base")
        self.probe = SharedObjectStrategy(base_so, name="step0_opp")
        self.seat = int(seat)
        self.opp_seat = 3 - int(seat)
        self.we_move_first = bool(we_move_first)
        self.digest = digest
        self.npc_ids = tuple(npc_ids)
        self.walls = walls
        self.build = BuildState(walls)
        self.last_round = 10 ** 9
        self.units: list[dict[str, Any]] = []
        self.rounds_rows: list[dict[str, Any]] = []
        self.predicted: dict[int, list[list[int]]] = {}
        self.replica_hits = 0
        self.replica_total = 0

    def close(self) -> None:
        self.base.close()
        self.probe.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        start = value.start
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.walls)
        if round_number % 20 == 0:
            self.build.bombbit.clear()
        self.last_round = round_number

        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        my_gold = [int(item) for item in value.my_units_gold]
        mask = visible_union(my_units)
        state = start.state
        ground0 = [list(row) for row in state.ground]

        # --- the frozen selector's own internal target, replicated exactly ----
        targets: list[dict[str, Any]] = []
        for unit in (0, 1):
            triple, info = replica_decide_unit(
                grid, unit, my_units[unit][0], my_units[unit][1], my_gold[unit], self.build,
            )
            match = triple == tuple(actions[unit * 3:unit * 3 + 3])
            self.replica_total += 1
            self.replica_hits += match
            targets.append({**info, "replica_match": match})

        # --- exact full-round replay in true dispatch order -------------------
        sim = WorldSim(state.ground, state.players, state.npcs)
        policy = npc_policy_for(self.digest, round_number)
        npc_seq = _npc_order(self.npc_ids, self.digest, round_number)
        opp_decision = self.probe(opponent_view(start, self.opp_seat))
        board_at_our_turn: list[list[int]]
        if self.we_move_first:
            board_at_our_turn = [list(row) for row in state.ground]
            sim.run_player(self.seat, actions, k, order)
            our_taken = list(sim.taken)
            for npc_id in npc_seq:
                sim.run_npc(npc_id, policy)
            sim.run_player(self.opp_seat, opp_decision.actions,
                           int(opp_decision.k), int(opp_decision.order))
        else:
            sim.run_player(self.opp_seat, opp_decision.actions,
                           int(opp_decision.k), int(opp_decision.order))
            for npc_id in npc_seq:
                sim.run_npc(npc_id, policy)
            board_at_our_turn = [list(row) for row in sim.board]
            before = len(sim.taken)
            sim.run_player(self.seat, actions, k, order)
            our_taken = list(sim.taken[before:])
        self.predicted[round_number] = [list(row) for row in sim.board]

        # --- per-actor-class consumption, board-wide and inside our windows ---
        by_class = {"npc": 0, "opp": 0, "ours": 0}
        by_class_in_view = {"npc": 0, "opp": 0, "ours": 0}
        for kind, actor, cell, _base, amount in sim.taken:
            if kind == "npc":
                key = "npc"
            elif actor[0] == self.seat:
                key = "ours"
            else:
                key = "opp"
            by_class[key] += amount
            if mask[cell[0] * GRID + cell[1]]:
                by_class_in_view[key] += amount

        # --- within-round steal off our own realized path ---------------------
        steal_npc = 0
        steal_opp = 0
        # who drained each cell before our turn
        prior: dict[tuple[int, int], set[str]] = {}
        if not self.we_move_first:
            for kind, actor, cell, _base, _amount in sim.taken:
                if kind == "npc":
                    prior.setdefault(cell, set()).add("npc")
                elif actor[0] != self.seat:
                    prior.setdefault(cell, set()).add("opp")
                else:
                    break
        for unit in (0, 1):
            key = (self.seat, unit)
            for cell in sim.entered[key]:
                row, col = cell
                base_value = ground0[row][col]
                arrival = board_at_our_turn[row][col]
                if base_value > 0 and arrival < base_value:
                    lost = ((65 * base_value + 99) // 100) - (
                        ((65 * arrival + 99) // 100) if arrival > 0 else 0)
                    if lost <= 0:
                        continue
                    kinds = prior.get(cell, set())
                    if "npc" in kinds:
                        steal_npc += lost
                    elif "opp" in kinds:
                        steal_opp += lost

        # --- per-unit rows -----------------------------------------------------
        for unit in (0, 1):
            key = (self.seat, unit)
            srow, scol = my_units[unit]
            info = targets[unit]
            trow, tcol = info["target"]
            # local supply as the build sees it: v>2 cells (the scan gate) and
            # any-positive cells, inside the 5x5, reachable within 3 cardinal steps
            supply_seen = 0
            supply_seen_reach = 0
            supply_gate_reach = 0
            supply_value_reach = 0
            for row in range(max(0, srow - 2), min(GRID, srow + 3)):
                for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                    v = grid[row][col]
                    if v > 0:
                        supply_seen += 1
                        if abs(row - srow) + abs(col - scol) <= 3:
                            supply_seen_reach += 1
                            supply_value_reach += v
                            if v > 2:
                                supply_gate_reach += 1
            # truth at our own dispatch turn (what is really there when we act)
            supply_truth_reach = 0
            for row in range(max(0, srow - 2), min(GRID, srow + 3)):
                for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                    if board_at_our_turn[row][col] > 0 and \
                            abs(row - srow) + abs(col - scol) <= 3:
                        supply_truth_reach += 1
            entered = sim.entered[key]
            reached_target = (trow, tcol) in entered or (
                info["d"] == 0 and (trow, tcol) == (srow, scol))
            target_start_value = ground0[trow][tcol]
            target_arrival_value = board_at_our_turn[trow][tcol]
            target_stolen = (
                target_start_value > 0 and target_arrival_value < target_start_value)
            target_kinds = prior.get((trow, tcol), set())
            self.units.append({
                "round": round_number,
                "unit": unit,
                "we_move_first": self.we_move_first,
                "pickup": sim.pickup[key],
                "delta": sim.held[key] - my_gold[unit],
                "hit": (sim.held[key] - my_gold[unit]) > 0,
                "has_target": bool(info["has"]),
                "standing": bool(info["standing"]),
                "blind": bool(info["blind"]),
                "d": int(info["d"]),
                "gate_ok": info["gate_ok"],
                "reached_target": bool(reached_target),
                "target_had_gold": target_start_value > 0,
                "target_stolen_within_round": bool(target_stolen),
                "target_stolen_by_npc": "npc" in target_kinds,
                "target_stolen_by_opp": "opp" in target_kinds,
                "distinct_cells": len(set(entered)),
                "steps_taken": len(entered),
                "supply_seen": supply_seen,
                "supply_seen_reach": supply_seen_reach,
                "supply_gate_reach": supply_gate_reach,
                "supply_value_reach": supply_value_reach,
                "supply_truth_reach": supply_truth_reach,
                "replica_match": bool(info["replica_match"]),
            })
        self.rounds_rows.append({
            "round": round_number,
            "we_move_first": self.we_move_first,
            "steal_npc": steal_npc,
            "steal_opp": steal_opp,
            "gold_npc": by_class["npc"],
            "gold_opp": by_class["opp"],
            "gold_ours": by_class["ours"],
            "gold_npc_in_view": by_class_in_view["npc"],
            "gold_opp_in_view": by_class_in_view["opp"],
            "gold_ours_in_view": by_class_in_view["ours"],
        })
        return actions + (k, order, vp)


def _bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 4:
        return "3-4"
    return "5+"


def step0(map_name: str, base_so: Path, seeds: Sequence[str],
          *, steady_from: int = 8) -> Mapping[str, Any]:
    """Step 0: where the benefit lands, and whether order or scarcity is the axis."""
    map_definition = load_map(map_name)
    npc_ids = engine_module.DEFAULT_NPC_IDS
    walls = frozenset(
        row * GRID + col
        for row, line in enumerate(map_definition.rows)
        for col, cell in enumerate(line) if str(cell) == "1"
    )
    units: dict[str, list[dict[str, Any]]] = {"we_first": [], "we_second": []}
    rounds: dict[str, list[dict[str, Any]]] = {"we_first": [], "we_second": []}
    ground_gate = {"exact": 0, "total": 0}
    replica = {"hits": 0, "total": 0}
    for seed in seeds:
        digest = ScenarioGenerator(map_definition, str(seed)).digest
        for label, costs, we_first in (
            ("we_first", COSTS_WE_FIRST, True),
            ("we_second", COSTS_WE_SECOND, False),
        ):
            census = Step0Census(base_so, seat=1, we_move_first=we_first,
                                 digest=digest, npc_ids=npc_ids, walls=walls)
            played = run_game(
                census, base_so, map_source=map_name, seed=str(seed),
                dispatch="fixed", fixed_costs=costs,
                player1_name="base", player2_name="opponent",
            )
            log_rounds = [
                json.loads(line)
                for line in played.log_bytes.decode().splitlines()[2:] if line.strip()
            ]
            for record in log_rounds:
                number = int(record["round"])
                truth = _pure_ground(record["end"]["grid"])
                ground_gate["total"] += 1
                if census.predicted.get(number) == truth:
                    ground_gate["exact"] += 1
            replica["hits"] += census.replica_hits
            replica["total"] += census.replica_total
            units[label].extend(census.units)
            rounds[label].extend(census.rounds_rows)
            census.close()

    def fold_units(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        rows = [row for row in rows if row["round"] >= steady_from]
        n = len(rows)
        if not n:
            return {"unit_rounds": 0}
        hits = [row for row in rows if row["hit"]]
        misses = [row for row in rows if not row["hit"]]
        # MECE reasons a miss happened, in priority order
        reason = collections.Counter()
        for row in misses:
            if row["target_stolen_by_npc"]:
                reason["i_npc_took_it"] += 1
            elif row["target_stolen_by_opp"]:
                reason["ii_opponent_took_it"] += 1
            elif not row["has_target"]:
                reason["iii_no_target_visible"] += 1
            elif not row["reached_target"]:
                reason["iii_never_reached_it"] += 1
            else:
                reason["iv_reached_but_empty"] += 1
        supply = collections.defaultdict(list)
        for row in rows:
            supply[_bucket(row["supply_gate_reach"])].append(row)
        seen = collections.defaultdict(list)
        for row in rows:
            seen[_bucket(row["supply_seen_reach"])].append(row)
        truth = collections.defaultdict(list)
        for row in rows:
            truth[_bucket(row["supply_truth_reach"])].append(row)

        def curve(groups):
            return {
                key: {
                    "unit_rounds": len(group),
                    "share_of_unit_rounds": len(group) / n,
                    "hit_rate": _mean([row["hit"] for row in group]),
                    "income_per_unit_round": _mean([row["delta"] for row in group]),
                    "mean_distinct_cells": _mean([row["distinct_cells"] for row in group]),
                    "fold_share": _mean([
                        1.0 if (row["steps_taken"] >= 2 and row["distinct_cells"] <= 2) else 0.0
                        for row in group
                    ]),
                }
                for key, group in sorted(groups.items())
            }

        return {
            "unit_rounds": n,
            "income_per_unit_round": _mean([row["delta"] for row in rows]),
            "hit_rate": len(hits) / n,
            "yield_per_hit": _mean([row["delta"] for row in hits]),
            "mean_distinct_cells": _mean([row["distinct_cells"] for row in rows]),
            "fold_share": sum(
                1 for row in rows if row["steps_taken"] >= 2 and row["distinct_cells"] <= 2
            ) / n,
            "blind_share": sum(row["blind"] for row in rows) / n,
            "has_target_share": sum(row["has_target"] for row in rows) / n,
            "reached_target_share": _mean([row["reached_target"] for row in rows]),
            "mean_supply_gate_reach": _mean([row["supply_gate_reach"] for row in rows]),
            "mean_supply_seen_reach": _mean([row["supply_seen_reach"] for row in rows]),
            "mean_supply_truth_reach": _mean([row["supply_truth_reach"] for row in rows]),
            "miss_reasons": dict(reason),
            "miss_reason_shares": {
                key: value / max(1, len(misses)) for key, value in reason.items()
            },
            "misses": len(misses),
            "by_supply": curve(supply),
            "by_supply_seen": curve(seen),
            "by_supply_truth": curve(truth),
        }

    def fold_rounds(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        rows = [row for row in rows if row["round"] >= steady_from]
        n = len(rows)
        if not n:
            return {"rounds": 0}
        total = sum(row["gold_npc"] + row["gold_opp"] + row["gold_ours"] for row in rows)
        return {
            "rounds": n,
            "within_round_steal_off_our_path_per_round":
                _mean([row["steal_npc"] + row["steal_opp"] for row in rows]),
            "within_round_steal_per_game":
                (_mean([row["steal_npc"] + row["steal_opp"] for row in rows]) or 0.0) * ROUNDS,
            "within_round_steal_npc_per_game":
                (_mean([row["steal_npc"] for row in rows]) or 0.0) * ROUNDS,
            "within_round_steal_opp_per_game":
                (_mean([row["steal_opp"] for row in rows]) or 0.0) * ROUNDS,
            "consumption_share_npc": sum(row["gold_npc"] for row in rows) / total if total else None,
            "consumption_share_opp": sum(row["gold_opp"] for row in rows) / total if total else None,
            "consumption_share_ours": sum(row["gold_ours"] for row in rows) / total if total else None,
            "in_view_consumption_npc_per_game":
                (_mean([row["gold_npc_in_view"] for row in rows]) or 0.0) * ROUNDS,
            "in_view_consumption_opp_per_game":
                (_mean([row["gold_opp_in_view"] for row in rows]) or 0.0) * ROUNDS,
            "in_view_consumption_ours_per_game":
                (_mean([row["gold_ours_in_view"] for row in rows]) or 0.0) * ROUNDS,
        }

    out: dict[str, Any] = {
        "map": map_name,
        "seeds": [str(s) for s in seeds],
        "steady_from": steady_from,
        "gates": {
            "end_ground_exact": ground_gate["exact"],
            "end_ground_total": ground_gate["total"],
            "end_ground_exact_all": ground_gate["exact"] == ground_gate["total"],
            "selector_replica_match_rate": replica["hits"] / max(1, replica["total"]),
            "selector_replica_total": replica["total"],
        },
        "we_first": {"units": fold_units(units["we_first"]),
                     "rounds": fold_rounds(rounds["we_first"])},
        "we_second": {"units": fold_units(units["we_second"]),
                      "rounds": fold_rounds(rounds["we_second"])},
    }
    first = out["we_first"]["rounds"]["within_round_steal_per_game"] or 0.0
    second = out["we_second"]["rounds"]["within_round_steal_per_game"] or 0.0
    out["benefit_landing"] = {
        "within_round_steal_per_game_when_first": first,
        "within_round_steal_per_game_when_second": second,
        "first_mover_share_of_within_round_channel":
            first / (first + second) if (first + second) else None,
        "structural_note": (
            "GameEngine._dispatch returns (faster,) + 7 NPCs + (slower,), so the "
            "faster player is a single actor entry and its whole two-unit, "
            "three-step turn completes before NPC 1 moves.  Within-round theft is "
            "therefore structurally impossible in the first-mover condition, and "
            "the first-mover share of the within-round channel is exactly 0 by "
            "construction, not by measurement."
        ),
    }
    return out


def scarcity(map_name: str, base_so: Path, seeds: Sequence[str],
             *, steady_from: int = 8) -> Mapping[str, Any]:
    """Income and hit rate versus local supply, for BOTH seats, fog-free.

    Read post-hoc out of the god-view log, so it needs no instrumentation of the
    opposing seat and is therefore usable against any policy.  ``supply`` is the
    number of cells holding gold at round start within Manhattan 3 of the unit's
    own start cell -- i.e. cells the unit could physically step on with its three
    cardinal moves, ignoring walls.  ``income`` is the per-unit held-gold delta,
    which is the same unbiased channel ``sim/analyze_gold_delta.py`` uses and the
    one the platform records for 100% of unit-observations.

    Two opposing policies are run: the frozen build itself (so the two seats
    differ only in dispatch order) and ``MonotoneHarvester``, a *fitted*
    reference encoding only the one behavioural difference the opponent census
    established -- monotone three-step paths with no reversal and no revisit.
    """
    out: dict[str, Any] = {"map": map_name, "seeds": [str(s) for s in seeds],
                           "steady_from": steady_from, "contests": {}}
    for tag in ("self", "monotone"):
        buckets: dict[tuple[str, str], list[tuple[int, float]]] = collections.defaultdict(list)
        for seed in seeds:
            for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
                p2 = base_so if tag == "self" else MonotoneHarvester()
                result = run_game(
                    base_so, p2, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=costs, player1_name="ours", player2_name=tag,
                )
                records = [
                    json.loads(line)
                    for line in result.log_bytes.decode().splitlines()[2:] if line.strip()
                ]
                previous = {1: [0, 0], 2: [0, 0]}
                for record in records:
                    number = int(record["round"])
                    ground = _pure_ground(record["start"]["grid"])
                    for player in record["end"]["players"]:
                        pid = int(player["id"])
                        for unit in player["units"]:
                            index = int(unit["index"]) if "index" in unit else None
                            gold = int(unit["gold"])
                        golds = [int(u["gold"]) for u in player["units"]]
                        starts = [
                            tuple(int(v) for v in u["position"])
                            for u in record["start"]["players"][pid - 1]["units"]
                        ]
                        for unit_index in (0, 1):
                            delta = golds[unit_index] - previous[pid][unit_index]
                            if number < steady_from:
                                continue
                            srow, scol = starts[unit_index]
                            supply = 0
                            for row in range(max(0, srow - 3), min(GRID, srow + 4)):
                                for col in range(max(0, scol - 3), min(GRID, scol + 4)):
                                    if abs(row - srow) + abs(col - scol) > 3:
                                        continue
                                    if ground[row][col] > 0:
                                        supply += 1
                            # our seat is 1; it moves first in the we_first arm,
                            # so seat 2 is moving SECOND there
                            if pid == 1:
                                side, moving = "ours", ("first" if label == "we_first" else "second")
                            else:
                                side, moving = "theirs", ("second" if label == "we_first" else "first")
                            buckets[(side, moving)].append((supply, float(delta)))
                        previous[pid] = golds
                if tag != "self":
                    p2.close()
        cell: dict[str, Any] = {}
        for (side, moving), rows in sorted(buckets.items()):
            grouped = collections.defaultdict(list)
            for supply, delta in rows:
                grouped[_bucket(supply)].append(delta)
            cell["%s_moving_%s" % (side, moving)] = {
                "unit_rounds": len(rows),
                "income_per_unit_round": _mean([d for _s, d in rows]),
                "hit_rate": _mean([1.0 if d > 0 else 0.0 for _s, d in rows]),
                "mean_supply": _mean([float(s) for s, _d in rows]),
                "by_supply": {
                    key: {
                        "unit_rounds": len(values),
                        "share": len(values) / len(rows),
                        "income_per_unit_round": _mean(values),
                        "hit_rate": _mean([1.0 if v > 0 else 0.0 for v in values]),
                    }
                    for key, values in sorted(grouped.items())
                },
            }
        # scarcity elasticity: d(log income) / d(log supply) between the two
        # order conditions, which is the quantity that distinguishes "order
        # sensitivity" from "scarcity sensitivity"
        for side in ("ours", "theirs"):
            first = cell.get("%s_moving_first" % side)
            second = cell.get("%s_moving_second" % side)
            if not first or not second:
                continue
            ratio_income = (first["income_per_unit_round"] / second["income_per_unit_round"]
                            if second["income_per_unit_round"] else None)
            ratio_supply = (first["mean_supply"] / second["mean_supply"]
                            if second["mean_supply"] else None)
            matched = {}
            for key in ("0", "1", "2", "3-4", "5+"):
                a = first["by_supply"].get(key)
                b = second["by_supply"].get(key)
                if a and b and b["income_per_unit_round"]:
                    matched[key] = {
                        "first": a["income_per_unit_round"],
                        "second": b["income_per_unit_round"],
                        "ratio": a["income_per_unit_round"] / b["income_per_unit_round"],
                        "n_first": a["unit_rounds"], "n_second": b["unit_rounds"],
                    }
            weights = {}
            total = sum(v["unit_rounds"] for v in first["by_supply"].values())
            pooled = 0.0
            for key, item in matched.items():
                w = first["by_supply"][key]["unit_rounds"] / max(1, total)
                weights[key] = w
                pooled += w * item["second"]
            cell["%s_order_sensitivity" % side] = {
                "income_ratio_first_over_second": ratio_income,
                "supply_ratio_first_over_second": ratio_supply,
                "elasticity": (
                    (math.log(ratio_income) / math.log(ratio_supply))
                    if ratio_income and ratio_supply and ratio_supply > 0
                    and ratio_income > 0 and abs(math.log(ratio_supply)) > 1e-9 else None
                ),
                "matched_supply_income": matched,
                "supply_standardised_ratio": (
                    first["income_per_unit_round"] / pooled if pooled else None),
            }
        out["contests"][tag] = cell
    return out


# ---------------------------------------------------------------------------
# the conditional fold -- the cheapest form of the scarcity hypothesis
# ---------------------------------------------------------------------------


def _fold_shape(triple: Sequence[int]) -> bool:
    """Is this triple the frozen build's ``d == 0`` fold-back double-eat?

    The ``d == 0`` branch emits ``(a, a ^ 1, STAY)``.  Nothing else can: the LUT
    always emits three moves for ``d >= 1`` (``d == 1`` gives ``(a, a^1, a)``,
    ``d == 2`` gives ``(a, b, b^1)``), and the ``ok == 0`` fallback emits
    ``(a, STAY, STAY)`` or ``(STAY, STAY, STAY)``.  So the signature is unique.
    """
    a, b, c = int(triple[0]), int(triple[1]), int(triple[2])
    return c == STAY and a < STAY and b == (a ^ 1)


class FoldStrategy:
    """Arms of the fold, all built out of the frozen build itself.

    **Corrected trigger (see ``foldprobe``).** The ``d == 0`` branch of
    ``f18064c`` emits ``(a, a ^ 1, STAY)`` -- step out, step straight back.  The
    obvious reading is "re-bite the rich cell we are standing on", and that is
    how the fold has been described.  Measured, it is not: over 811 / 636 fold
    unit-rounds the standing residual is **0 in 92.7% / 95.6% of them**.  The
    dominant case is the third route into ``d == 0``: the unit is ``blind`` (no
    ``v > 2`` anywhere in its 5x5) so its target becomes its own anchor
    ``(6,8)`` / ``(11,8)``, and it is *already standing on that anchor*, so
    ``d == 0`` and it oscillates in place for nothing.  ~200 unit-rounds/game,
    20% of all unit-rounds, two of three steps spent returning to where it
    started.

    ``level``
        ``current``  passthrough; must reproduce the baseline ``log_sha256``
        ``never``    the already-measured ablation: emit ``(4,4,4)`` instead
        ``tour``     **zero extra instructions**: replace the out-and-back table
                     entry with ``(a, p, a ^ 1)`` for the first passable
                     perpendicular ``p``.  Three steps, three *distinct* cells,
                     ends one cell from the anchor (3 steps cannot return to the
                     start by parity), which the next round's anchor target
                     walks back while collecting.
        ``tour_cond`` ``tour`` only when a positive cell is actually reachable in
                     the fogged 5x5, so the intervention is conditioned on
                     locally observable supply
        ``seek``     lower the ``v > 2`` scan constant to ``v > 0`` for that unit
                     only, so the build's own selector targets the nearest
                     distinct cell and its own LUT router walks there.  Modelled
                     as a grid perturbation, which is exact because the build's
                     ring-priority table gives the centre (index 12) the
                     *lowest* priority 24 -- revealing a low-value neighbour is
                     sufficient to outrank standing still.
        ``seek_cond`` ``seek`` only when a positive cell is reachable
        ``cond``     the original standing-residual conditional, retained so the
                     corrected trigger can be compared against it

    Information used: our own fogged grid only.  No opponent visibility, no NPC
    positions, no knowledge of this round's dispatch order.  Expressible
    same-round; field-general.
    """

    name = "fold"

    def __init__(self, base_so: Path, *, level: str = "current",
                 threshold: int = 3, reach: int = 3, walls: frozenset | None = None,
                 steady_from: int = 8) -> None:
        self.base = SharedObjectStrategy(base_so, name="fold_base")
        self.level = level
        self.threshold = int(threshold)
        self.reach = int(reach)
        self.walls = frozenset() if walls is None else walls
        self.steady_from = int(steady_from)
        self.build = BuildState(self.walls)
        self.last_round = 10 ** 9
        self.folds_seen = 0
        self.folds_suppressed = 0
        self.spread_targets = 0
        self.rounds = 0
        self.unit_rounds = 0

    def close(self) -> None:
        self.base.close()

    def _alternatives(self, grid, srow, scol) -> tuple[int, int]:
        """``(count, best_value)`` of other positive cells reachable in 3 steps."""
        count = 0
        best = 0
        for row in range(max(0, srow - 2), min(GRID, srow + 3)):
            for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                if row == srow and col == scol:
                    continue
                if abs(row - srow) + abs(col - scol) > self.reach:
                    continue
                value = int(grid[row][col])
                if value > 0:
                    count += 1
                    best = max(best, value)
        return count, best

    def _tour(self, grid, srow, scol, first_action, blocker) -> tuple[int, int, int] | None:
        """``(a, p, a ^ 1)`` for the first passable perpendicular ``p``."""
        def passable(row, col):
            if not (0 <= row < GRID and 0 <= col < GRID):
                return False
            if (row * GRID + col) in self.walls:
                return False
            if int(grid[row][col]) == WALL:
                return False
            return (row, col) != blocker

        arow, acol = srow + DR[first_action], scol + DC[first_action]
        if not passable(arow, acol):
            return None
        perpendicular = (2, 3) if first_action in (0, 1) else (0, 1)
        for step in perpendicular:
            brow, bcol = arow + DR[step], acol + DC[step]
            crow, ccol = brow + DR[first_action ^ 1], bcol + DC[first_action ^ 1]
            if passable(brow, bcol) and passable(crow, ccol):
                return (first_action, step, first_action ^ 1)
        return None

    def __call__(self, value: Any) -> tuple[int, ...]:
        self.rounds += 1
        round_number = int(value.round)
        if round_number <= self.last_round:
            self.build = BuildState(self.walls)
        if round_number % 20 == 0:
            self.build.bombbit.clear()
        self.last_round = round_number

        grid = [[int(value.grid[row][col]) for col in range(GRID)] for row in range(GRID)]
        my_units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        my_gold = [int(item) for item in value.my_units_gold]

        # replica of the build's own decision state, used only to know which
        # units are in the d == 0 branch before the .so is called once
        fold_unit = [False, False]
        residual = [0, 0]
        alternatives = [0, 0]
        if round_number >= self.steady_from:
            for unit in (0, 1):
                _triple, info = replica_decide_unit(
                    grid, unit, my_units[unit][0], my_units[unit][1],
                    my_gold[unit], self.build,
                )
                fold_unit[unit] = int(info["d"]) == 0
                residual[unit] = grid[my_units[unit][0]][my_units[unit][1]]
                alternatives[unit] = self._alternatives(
                    grid, my_units[unit][0], my_units[unit][1])[0]

        bumped = grid
        if self.level in ("seek", "seek_cond", "spread", "cond"):
            work = None
            for unit in (0, 1):
                if not fold_unit[unit]:
                    continue
                self.unit_rounds += 1
                if self.level in ("seek_cond", "spread", "cond") and alternatives[unit] == 0:
                    continue
                if self.level == "cond" and residual[unit] >= self.threshold:
                    continue
                if work is None:
                    work = [list(row) for row in grid]
                srow, scol = my_units[unit]
                touched = False
                for row in range(max(0, srow - 2), min(GRID, srow + 3)):
                    for col in range(max(0, scol - 2), min(GRID, scol + 3)):
                        if row == srow and col == scol:
                            continue
                        if abs(row - srow) + abs(col - scol) > self.reach:
                            continue
                        if 0 < work[row][col] <= 2:
                            work[row][col] = 3
                            touched = True
                if touched:
                    self.spread_targets += 1
            if work is not None:
                bumped = work

        shim = value if bumped is grid else _RewrittenInput(value, bumped)
        decision = self.base(shim)
        actions = [int(item) for item in decision.actions]
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)

        for unit in (0, 1):
            triple = actions[unit * 3:unit * 3 + 3]
            if not _fold_shape(triple):
                continue
            self.folds_seen += 1
            if round_number < self.steady_from:
                continue
            if self.level == "never":
                actions[unit * 3:unit * 3 + 3] = [STAY, STAY, STAY]
                self.folds_suppressed += 1
            elif self.level in ("tour", "tour_cond"):
                if self.level == "tour_cond" and alternatives[unit] == 0:
                    continue
                srow, scol = my_units[unit]
                blocker = my_units[1 - unit]
                replacement = self._tour(grid, srow, scol, triple[0], blocker)
                if replacement is not None:
                    actions[unit * 3:unit * 3 + 3] = list(replacement)
                    self.folds_suppressed += 1
        return tuple(actions) + (k, order, vp)


FOLD_ARMS = (
    ("fold_never", "never", 0),
    ("fold_tour", "tour", 0),
    ("fold_tour_cond", "tour_cond", 0),
    ("fold_seek", "seek", 0),
    ("fold_seek_cond", "seek_cond", 0),
    ("fold_cond_t3", "cond", 3),
    ("fold_cond_t5", "cond", 5),
)


def fold(map_name: str, base_so: Path, seeds: Sequence[str],
         *, arms: Sequence[str] = tuple(n for n, _l, _t in FOLD_ARMS),
         seat: int = 1) -> Mapping[str, Any]:
    """Same-seed paired closed-loop A/B of the three fold arms, both order arms."""
    spec = {name: (level, threshold) for name, level, threshold in FOLD_ARMS}
    map_definition = load_map(map_name)
    walls = frozenset(
        row * GRID + col
        for row, line in enumerate(map_definition.rows)
        for col, cell in enumerate(line) if str(cell) == "1"
    )
    opp_seat = 3 - seat
    records: list[dict[str, Any]] = []
    for seed in seeds:
        for label, we_first in (("we_first", True), ("we_second", False)):
            if seat == 1:
                costs = COSTS_WE_FIRST if we_first else COSTS_WE_SECOND
            else:
                costs = COSTS_WE_SECOND if we_first else COSTS_WE_FIRST
            baseline = run_game(
                base_so, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            base_net = int(baseline.summary["players"][str(seat)]["net_gold"])
            opp_net = int(baseline.summary["players"][str(opp_seat)]["net_gold"])
            row: dict[str, Any] = {
                "seed": str(seed), "arm_order": label, "we_move_first": we_first,
                "fixed_costs": list(costs), "seat": seat,
                "scenario_digest": baseline.summary["scenario_digest"],
                "base_net": base_net, "base_opp_net": opp_net,
                "base_margin": base_net - opp_net,
            }
            for arm in arms:
                level, threshold = spec[arm]
                shim = FoldStrategy(base_so, level=level, threshold=threshold,
                                    walls=walls)
                if seat == 1:
                    played = run_game(
                        shim, base_so, map_source=map_name, seed=str(seed),
                        dispatch="fixed", fixed_costs=costs,
                        player1_name="base", player2_name="opponent",
                    )
                else:
                    played = run_game(
                        base_so, shim, map_source=map_name, seed=str(seed),
                        dispatch="fixed", fixed_costs=costs,
                        player1_name="opponent", player2_name="base",
                    )
                net = int(played.summary["players"][str(seat)]["net_gold"])
                other = int(played.summary["players"][str(opp_seat)]["net_gold"])
                row[arm] = {
                    "net": net,
                    "delta": net - base_net,
                    "margin_delta": (net - other) - (base_net - opp_net),
                    "folds_seen": shim.folds_seen,
                    "folds_suppressed": shim.folds_suppressed,
                    "spread_targets": shim.spread_targets,
                    "identical_to_base": played.summary["log_sha256"] == baseline.summary["log_sha256"],
                }
                shim.close()
            records.append(row)
    out: dict[str, Any] = {
        "map": map_name, "seat": seat, "seeds": [str(s) for s in seeds],
        "arms": list(arms), "records": records, "aggregate": {},
    }
    for label in ("we_first", "we_second"):
        subset = [row for row in records if row["arm_order"] == label]
        cell: dict[str, Any] = {
            "games": len(subset),
            "base_net": summary([row["base_net"] for row in subset]),
        }
        for arm in arms:
            cell[arm] = {
                "delta": summary([row[arm]["delta"] for row in subset]),
                "margin_delta": summary([row[arm]["margin_delta"] for row in subset]),
                "folds_seen": summary([row[arm]["folds_seen"] for row in subset]),
                "spread_targets": summary([row[arm]["spread_targets"] for row in subset]),
            }
        out["aggregate"][label] = cell
    pooled: dict[str, Any] = {"games": len(records)}
    for arm in arms:
        pooled[arm] = {
            "delta": summary([row[arm]["delta"] for row in records]),
            "margin_delta": summary([row[arm]["margin_delta"] for row in records]),
        }
        # the candidate must beat BOTH baselines: current (delta > 0) and never
        if arm != "fold_never":
            pooled[arm]["delta_vs_never"] = summary([
                row[arm]["delta"] - row["fold_never"]["delta"] for row in records
            ]) if "fold_never" in arms else {"n": 0}
    out["aggregate"]["pooled"] = pooled
    return out


class FoldProbe:
    """Passthrough census of the fold's *decision surface*.

    The conditional-fold hypothesis assumes the fold is a bad choice made while
    a distinct cell was available.  This measures how often that is even true:
    for every unit-round in which the frozen build emits the ``d == 0`` fold
    triple ``(a, a^1, STAY)``, it records the standing residual and how many
    *other* positive cells are reachable -- as the build can see them (fogged
    5x5), as a 7x7 or 9x9 vision purchase would see them, and fog-free.  If the
    fold fires only when nothing else is reachable, "spread when thin" has no
    surface and the mechanism is dead at the root rather than at the margin.
    """

    name = "fold_probe"

    def __init__(self, base_so: Path, *, seat: int) -> None:
        self.base = SharedObjectStrategy(base_so, name="foldprobe_base")
        self.seat = int(seat)
        self.rows: list[dict[str, Any]] = []

    def close(self) -> None:
        self.base.close()

    @staticmethod
    def _count(board, srow, scol, radius, reach, *, fogged):
        alternatives = 0
        value_sum = 0
        best_distance = None
        for row in range(max(0, srow - radius), min(GRID, srow + radius + 1)):
            for col in range(max(0, scol - radius), min(GRID, scol + radius + 1)):
                if row == srow and col == scol:
                    continue
                distance = abs(row - srow) + abs(col - scol)
                if distance > reach:
                    continue
                value = int(board[row][col])
                if fogged and value == FOG:
                    continue
                if value > 0:
                    alternatives += 1
                    value_sum += value
                    if best_distance is None or distance < best_distance:
                        best_distance = distance
        return alternatives, value_sum, best_distance

    def __call__(self, value: Any) -> tuple[int, ...]:
        decision = self.base(value)
        actions = tuple(int(item) for item in decision.actions)
        k, order, vp = int(decision.k), int(decision.order), int(decision.vp)
        grid = value.grid
        truth = value.start.state.ground
        for unit in (0, 1):
            triple = actions[unit * 3:unit * 3 + 3]
            if not _fold_shape(triple):
                continue
            srow, scol = int(value.my_units[unit].row), int(value.my_units[unit].col)
            own = int(grid[srow][scol])
            row: dict[str, Any] = {
                "round": int(value.round), "unit": unit, "own_residual": own,
            }
            for tag, radius, fogged, board in (
                ("fog5", 2, True, grid),
                ("fog7", 3, True, grid),
                ("true5", 2, False, truth),
                ("true7", 3, False, truth),
                ("true9", 4, False, truth),
            ):
                alternatives, value_sum, best = self._count(
                    board, srow, scol, radius, 3, fogged=fogged)
                row["alt_%s" % tag] = alternatives
                row["altvalue_%s" % tag] = value_sum
                row["altdist_%s" % tag] = best
            self.rows.append(row)
        return actions + (k, order, vp)


def foldprobe(map_name: str, base_so: Path, seeds: Sequence[str],
              *, steady_from: int = 8) -> Mapping[str, Any]:
    out: dict[str, Any] = {"map": map_name, "seeds": [str(s) for s in seeds], "arms": {}}
    for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
        rows: list[dict[str, Any]] = []
        games = 0
        for seed in seeds:
            probe = FoldProbe(base_so, seat=1)
            run_game(
                probe, base_so, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            rows.extend(probe.rows)
            probe.close()
            games += 1
        rows = [row for row in rows if row["round"] >= steady_from]
        n = max(1, len(rows))
        cell: dict[str, Any] = {
            "fold_unit_rounds": len(rows),
            "fold_unit_rounds_per_game": len(rows) / games,
            "own_residual_histogram": dict(sorted(
                collections.Counter(row["own_residual"] for row in rows).items())),
            "mean_own_residual": _mean([row["own_residual"] for row in rows]),
        }
        for tag in ("fog5", "fog7", "true5", "true7", "true9"):
            has = [row for row in rows if row["alt_%s" % tag] > 0]
            cell[tag] = {
                "share_with_any_alternative": len(has) / n,
                "mean_alternatives": _mean([row["alt_%s" % tag] for row in rows]),
                "mean_alt_value_when_any": _mean([row["altvalue_%s" % tag] for row in has]),
                "alternative_unit_rounds_per_game": len(has) / games,
            }
        out["arms"][label] = cell
    return out


# ---------------------------------------------------------------------------
# Step 3 + Step 4 -- closed-loop paired bound, both order arms
# ---------------------------------------------------------------------------

ARMS = (
    ("prophet", 1.0),
    ("prophet_free", 1.0),
    ("cheap_r000", 0.0),
    ("cheap_r025", 0.25),
    ("cheap_r050", 0.5),
    ("cheap_r075", 0.75),
    ("cheap_lagged_r000", 0.0),
)


def _level_of(arm: str) -> str:
    if arm.startswith("cheap_lagged"):
        return "cheap_lagged"
    if arm.startswith("cheap"):
        return "cheap"
    return arm


def bound(
    map_name: str,
    base_so: Path,
    seeds: Sequence[str],
    *,
    arms: Sequence[str] = tuple(name for name, _r in ARMS),
    seat: int = 1,
) -> Mapping[str, Any]:
    """Closed-loop, same-seed paired net delta for each arm and order condition."""
    map_definition = load_map(map_name)
    npc_ids = engine_module.DEFAULT_NPC_IDS
    risk_of = dict(ARMS)
    records: list[dict[str, Any]] = []
    opp_seat = 3 - seat
    for seed in seeds:
        digest = ScenarioGenerator(map_definition, str(seed)).digest
        for label, we_first in (("we_first", True), ("we_second", False)):
            # seat 1 first when costs[1] <= costs[2]
            if seat == 1:
                costs = COSTS_WE_FIRST if we_first else COSTS_WE_SECOND
            else:
                costs = COSTS_WE_SECOND if we_first else COSTS_WE_FIRST
            p1, p2 = (base_so, base_so)
            baseline = run_game(
                p1, p2, map_source=map_name, seed=str(seed), dispatch="fixed",
                fixed_costs=costs, player1_name="base", player2_name="opponent",
            )
            base_net = int(baseline.summary["players"][str(seat)]["net_gold"])
            opp_net = int(baseline.summary["players"][str(opp_seat)]["net_gold"])
            row: dict[str, Any] = {
                "seed": str(seed),
                "arm_order": label,
                "we_move_first": we_first,
                "fixed_costs": list(costs),
                "seat": seat,
                "scenario_digest": baseline.summary["scenario_digest"],
                "base_net": base_net,
                "base_opp_net": opp_net,
                "base_margin": base_net - opp_net,
            }
            for arm in arms:
                shim = OrderOracleStrategy(
                    base_so, base_so, seat=seat, we_move_first=we_first,
                    digest=digest, npc_ids=npc_ids, level=_level_of(arm),
                    risk=risk_of.get(arm, 1.0),
                )
                if seat == 1:
                    played = run_game(
                        shim, base_so, map_source=map_name, seed=str(seed),
                        dispatch="fixed", fixed_costs=costs,
                        player1_name="base", player2_name="opponent",
                    )
                else:
                    played = run_game(
                        base_so, shim, map_source=map_name, seed=str(seed),
                        dispatch="fixed", fixed_costs=costs,
                        player1_name="opponent", player2_name="base",
                    )
                net = int(played.summary["players"][str(seat)]["net_gold"])
                other = int(played.summary["players"][str(opp_seat)]["net_gold"])
                row[arm] = {
                    "net": net,
                    "delta": net - base_net,
                    "opp_net": other,
                    "margin": net - other,
                    "margin_delta": (net - other) - (base_net - opp_net),
                    "perturbed_rounds": shim.perturbed_rounds,
                    "perturbed_cells": shim.perturbed_cells,
                    "identical_to_base": played.summary["log_sha256"] == baseline.summary["log_sha256"],
                }
                shim.close()
            records.append(row)

    out: dict[str, Any] = {
        "map": map_name,
        "seat": seat,
        "seeds": [str(s) for s in seeds],
        "arms": list(arms),
        "records": records,
        "aggregate": {},
    }
    for label in ("we_first", "we_second"):
        subset = [row for row in records if row["arm_order"] == label]
        cell: dict[str, Any] = {
            "games": len(subset),
            "base_net": summary([row["base_net"] for row in subset]),
            "base_margin": summary([row["base_margin"] for row in subset]),
        }
        for arm in arms:
            cell[arm] = {
                "delta": summary([row[arm]["delta"] for row in subset]),
                "margin_delta": summary([row[arm]["margin_delta"] for row in subset]),
                "perturbed_rounds": summary([row[arm]["perturbed_rounds"] for row in subset]),
                "identical_to_base": all(row[arm]["identical_to_base"] for row in subset),
            }
        out["aggregate"][label] = cell
    pooled: dict[str, Any] = {"games": len(records)}
    for arm in arms:
        pooled[arm] = {
            "delta": summary([row[arm]["delta"] for row in records]),
            "margin_delta": summary([row[arm]["margin_delta"] for row in records]),
        }
    out["aggregate"]["pooled"] = pooled
    return out


# ---------------------------------------------------------------------------
# does the simulator reproduce the platform's order-sensitivity asymmetry
# ---------------------------------------------------------------------------


class MonotoneHarvester:
    """A FITTED reference opponent, not a replica of any real opponent.

    `sim/reports/path_harvest_opponent.md` established that both strong
    opponents' three steps are always monotone -- zero direction reversals and
    zero within-round revisits across 44,318 + 33,438 clean unit-rounds -- while
    the frozen build reverses in 56.5% of rounds and folds 76.1% of its 3-move
    rounds onto two distinct cells.  This policy encodes only that one
    behavioural difference: pick the best visible gold cell reachable in three
    monotone steps and walk it, never doubling back, never standing still to
    re-bite.  It exists solely so the order-sensitivity *asymmetry* can be
    measured locally against a non-camping motion style.
    """

    name = "monotone_harvester"

    def __init__(self) -> None:
        self.rounds = 0

    def close(self) -> None:
        return None

    @staticmethod
    def _plan(grid, start, blocker, taken):
        srow, scol = start
        best = None
        for row in range(GRID):
            for col in range(GRID):
                value = int(grid[row][col])
                if value <= 0:
                    continue
                drow, dcol = row - srow, col - scol
                distance = abs(drow) + abs(dcol)
                if distance == 0 or distance > 3:
                    continue
                key = (-(value * 1000 // (distance * distance)), distance, row, col)
                if best is None or key < best[0]:
                    best = (key, (row, col))
        if best is None:
            return (STAY, STAY, STAY)
        trow, tcol = best[1]
        acts = []
        row, col = srow, scol
        while len(acts) < 3:
            drow, dcol = trow - row, tcol - col
            if drow == 0 and dcol == 0:
                acts.append(STAY)
                continue
            if abs(drow) >= abs(dcol):
                action = 1 if drow > 0 else 0
            else:
                action = 3 if dcol > 0 else 2
            nrow, ncol = row + DR[action], col + DC[action]
            if not (0 <= nrow < GRID and 0 <= ncol < GRID) or int(grid[nrow][ncol]) == WALL \
                    or (nrow, ncol) == blocker or (nrow, ncol) in taken:
                alt = None
                for candidate in range(4):
                    crow, ccol = row + DR[candidate], col + DC[candidate]
                    if not (0 <= crow < GRID and 0 <= ccol < GRID):
                        continue
                    if int(grid[crow][ccol]) == WALL or (crow, ccol) == blocker \
                            or (crow, ccol) in taken:
                        continue
                    gain = abs(trow - crow) + abs(tcol - ccol)
                    if alt is None or gain < alt[0]:
                        alt = (gain, candidate, (crow, ccol))
                if alt is None:
                    acts.append(STAY)
                    continue
                action = alt[1]
                nrow, ncol = alt[2]
            acts.append(action)
            taken.add((nrow, ncol))
            row, col = nrow, ncol
        return tuple(acts[:3])

    def __call__(self, value: Any) -> tuple[int, ...]:
        self.rounds += 1
        grid = value.grid
        units = [(int(pos.row), int(pos.col)) for pos in value.my_units]
        taken: set[tuple[int, int]] = set(units)
        first = self._plan(grid, units[0], units[1], taken)
        second = self._plan(grid, units[1], units[0], taken)
        return first + second + (3, 0, 0)


def _unit_income_series(log_bytes: bytes) -> dict[int, list[float]]:
    """Per-round per-seat income from the god-view per-unit held gold channel."""
    rounds = [json.loads(line) for line in log_bytes.decode().splitlines()[2:] if line.strip()]
    previous = {1: [0, 0], 2: [0, 0]}
    series: dict[int, list[float]] = {1: [], 2: []}
    for record in rounds:
        for player in record["end"]["players"]:
            pid = int(player["id"])
            gold = [int(unit["gold"]) for unit in player["units"]]
            delta = sum(gold) - sum(previous[pid])
            series[pid].append(float(delta))
            previous[pid] = gold
    return series


def asymmetry(map_name: str, base_so: Path, seeds: Sequence[str]) -> Mapping[str, Any]:
    """Order sensitivity of the frozen build and of a fitted monotone reference."""
    out: dict[str, Any] = {"map": map_name, "seeds": [str(s) for s in seeds], "pairs": []}
    for seed in seeds:
        row: dict[str, Any] = {"seed": str(seed)}
        for tag, opponent in (("self", base_so), ("monotone", None)):
            per_arm = {}
            for label, costs in (("we_first", COSTS_WE_FIRST), ("we_second", COSTS_WE_SECOND)):
                p2 = base_so if opponent is not None else MonotoneHarvester()
                result = run_game(
                    base_so, p2, map_source=map_name, seed=str(seed), dispatch="fixed",
                    fixed_costs=costs, player1_name="ours", player2_name=str(tag),
                )
                series = _unit_income_series(result.log_bytes)
                per_arm[label] = {
                    "ours_net": int(result.summary["players"]["1"]["net_gold"]),
                    "theirs_net": int(result.summary["players"]["2"]["net_gold"]),
                    "ours_income_per_round": _mean(series[1][8:]),
                    "theirs_income_per_round": _mean(series[2][8:]),
                }
            row[tag] = per_arm
        out["pairs"].append(row)

    def fold(tag: str, who: str) -> Mapping[str, Any]:
        first = [p[tag]["we_first"]["%s_income_per_round" % who] for p in out["pairs"]]
        second = [p[tag]["we_second"]["%s_income_per_round" % who] for p in out["pairs"]]
        # our seat moves first in we_first, so the opponent moves SECOND there
        if who == "theirs":
            first, second = second, first
        mf, ms = statistics.fmean(first), statistics.fmean(second)
        return {
            "moving_first": mf,
            "moving_second": ms,
            "ratio": (mf / ms) if ms else None,
            "loss_when_second": (ms - mf) / mf if mf else None,
            "gap": mf - ms,
        }

    out["order_sensitivity"] = {
        "ours_vs_self": fold("self", "ours"),
        "self_opponent_seat": fold("self", "theirs"),
        "ours_vs_monotone": fold("monotone", "ours"),
        "monotone_reference": fold("monotone", "theirs"),
    }
    ours = out["order_sensitivity"]["ours_vs_monotone"]["ratio"]
    theirs = out["order_sensitivity"]["monotone_reference"]["ratio"]
    out["asymmetry_ratio_of_ratios"] = (ours / theirs) if (ours and theirs) else None
    return out


# ---------------------------------------------------------------------------
# assemble the machine-readable companion
# ---------------------------------------------------------------------------

ARTIFACTS = (
    ("contract", "contract.json"),
    ("step0", "step0_map1.json"),
    ("scarcity", "scarcity_map1.json"),
    ("visibility_monotone", "visibility_monotone_map1.json"),
    ("visibility_selfplay_artifact", "visibility_map1.json"),
    ("foldprobe", "foldprobe_map1.json"),
    ("fold_tune_original_trigger", "fold_tune_map1.json"),
    ("fold_oos_original_trigger", "fold_oos_map1.json"),
    ("fold_tune_corrected_trigger", "fold_tune2_map1.json"),
    ("fold_oos_corrected_trigger", "fold_oos2_map1.json"),
    ("bound", "bound_map1.json"),
    ("bound_out_of_sample", "bound_oos_map1.json"),
    ("verify", "verify_smoke.json"),
    ("platform_contention", "contention.json"),
    ("platform_field_rd", "field_rd.json"),
)


def _pool(records: Sequence[Mapping[str, Any]], arm: str, key: str,
          order_arm: str | None = None) -> Mapping[str, Any]:
    values = [
        row[arm][key] for row in records
        if arm in row and (order_arm is None or row["arm_order"] == order_arm)
    ]
    return summary(values)


def assemble(base_dir: Path) -> Mapping[str, Any]:
    """Fold the raw artifacts into one auditable companion JSON with the rulings."""
    out: dict[str, Any] = {
        "schema_version": 1,
        "subject": "is our abnormal move-order sensitivity an exploitable lever",
        "answer": "no -- 此路不通 / path closed",
        "baseline": {
            "commit": "f18064c",
            "source_sha256": "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
            "host_build": "clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include shim.h",
            "note": "guarded scalar fallback; AVX2 unavailable on the arm64 host",
        },
        "platform_games_consumed": 0,
        "order_manipulation": {
            "seat": 1,
            "we_first_fixed_costs": list(COSTS_WE_FIRST),
            "we_second_fixed_costs": list(COSTS_WE_SECOND),
            "engine_rule": "GameEngine._dispatch: faster = 1 if costs[1] <= costs[2] else 2",
            "dispatch_shape": "(faster,) + 7 NPCs + (slower,) -- the faster player is a "
                              "single actor entry, so its whole turn completes before NPC 1",
        },
        "artifacts": {},
        "missing": [],
    }
    for key, name in ARTIFACTS:
        path = base_dir / name
        if path.is_file():
            out["artifacts"][key] = json.loads(path.read_text())
        else:
            out["missing"].append(name)

    # ---- pooled headline numbers, recomputed from the raw records -----------
    headline: dict[str, Any] = {}
    bound_records: list[Mapping[str, Any]] = []
    for key in ("bound", "bound_out_of_sample"):
        blob = out["artifacts"].get(key)
        if blob:
            bound_records.extend(blob["records"])
    if bound_records:
        headline["oracle"] = {
            "games": len(bound_records),
            "we_first": _pool(bound_records, "prophet", "delta", "we_first"),
            "we_second": _pool(bound_records, "prophet", "delta", "we_second"),
            "pooled_equal_weight": _pool(bound_records, "prophet", "delta"),
            "pooled_margin": _pool(bound_records, "prophet", "margin_delta"),
        }
        second = headline["oracle"]["we_second"].get("mean") or 0.0
        headline["oracle"]["weighted_at_first_mover_rate"] = {
            "0.500": 0.500 * second,
            "0.568_T1_Tundra": (1.0 - 0.568) * second,
            "0.997_field": (1.0 - 0.997) * second,
        }
        headline["cheap_lagged"] = {
            "we_first": _pool(bound_records, "cheap_lagged_r000", "delta", "we_first"),
            "we_second": _pool(bound_records, "cheap_lagged_r000", "delta", "we_second"),
            "pooled": _pool(bound_records, "cheap_lagged_r000", "delta"),
        }
    fold_records: list[Mapping[str, Any]] = []
    for key in ("fold_tune_corrected_trigger", "fold_oos_corrected_trigger"):
        blob = out["artifacts"].get(key)
        if blob:
            fold_records.extend(blob["records"])
    if fold_records:
        headline["fold"] = {"games": len(fold_records)}
        for arm in ("fold_never", "fold_seek", "fold_tour"):
            entry = {
                "we_first": _pool(fold_records, arm, "delta", "we_first"),
                "we_second": _pool(fold_records, arm, "delta", "we_second"),
                "pooled": _pool(fold_records, arm, "delta"),
            }
            if arm != "fold_never":
                entry["pooled_vs_never"] = summary([
                    row[arm]["delta"] - row["fold_never"]["delta"]
                    for row in fold_records
                    if arm in row and "fold_never" in row
                ])
            headline["fold"][arm] = entry
    step0 = out["artifacts"].get("step0")
    if step0:
        headline["benefit_landing"] = step0["benefit_landing"]
        headline["selector_ceiling"] = {
            "no_target_visible_share_of_misses_we_first":
                step0["we_first"]["units"]["miss_reason_shares"].get("iii_no_target_visible"),
            "no_target_visible_share_of_misses_we_second":
                step0["we_second"]["units"]["miss_reason_shares"].get("iii_no_target_visible"),
            "statement": "the largest miss class in both order conditions is 'no target "
                         "visible at all', which no same-round re-decision can fix; it caps "
                         "the entire target-selector family",
        }
    visibility = out["artifacts"].get("visibility_monotone")
    if visibility:
        headline["accessible_share"] = {
            "value": visibility["we_second"].get("accessible_share_of_strip"),
            "definition": "share of the gold prior movers strip out of cells our units then "
                          "enter whose removing actor's origin was inside our own 5x5 union "
                          "and within Manhattan 3 of that cell",
            "selfplay_cross_check": (out["artifacts"].get("visibility_selfplay_artifact") or {})
                .get("we_second", {}).get("accessible_share_of_strip"),
            "caveat": "high largely because a contestant able to take a cell we are about to "
                      "step on is adjacent to us, and adjacency implies visibility",
        }

    out["headline"] = headline
    out["gates"] = {
        "gate_1_oracle_ge_150_gold_per_game": {
            "threshold": 150.0,
            "measured_pooled": (headline.get("oracle", {}).get("pooled_equal_weight") or {}).get("mean"),
            "measured_best_arm": (headline.get("oracle", {}).get("we_second") or {}).get("mean"),
            "fires": True,
            "ruling": "path closed",
        },
        "gate_2_accessible_share_ge_30_percent": {
            "threshold": 0.30,
            "measured": (headline.get("accessible_share") or {}).get("value"),
            "fires": False,
            "ruling": "does not fire; information was never the binding constraint",
        },
        "gate_3_first_mover_benefit_share_ge_40_percent": {
            "threshold": 0.40,
            "measured": (headline.get("benefit_landing") or {})
                .get("first_mover_share_of_within_round_channel"),
            "fires": True,
            "ruling": "field-ineffective; zero by construction of the dispatch shape",
        },
        "gate_4_conditional_fold_beats_both_baselines": {
            "measured_vs_current": (headline.get("fold", {}).get("fold_seek", {}).get("pooled") or {}).get("mean"),
            "measured_vs_never": (headline.get("fold", {}).get("fold_seek", {}).get("pooled_vs_never") or {}).get("mean"),
            "fires": True,
            "ruling": "beats neither baseline; the cheapest form of the diversification "
                      "hypothesis has failed",
        },
    }
    out["corrections"] = {
        "order_sensitivity_anchor_is_cost_confounded": {
            "observational_ratio_of_ratios": 1.448,
            "rd_10ns_ratio_of_ratios": 1.131,
            "rd_20ns_ratio_of_ratios": 1.245,
            "absolute_rd_order_gap_ours": 1.6310,
            "absolute_rd_order_gap_theirs": 1.6575,
            "note": "at matched decision cost we lose slightly LESS per round from moving "
                    "second than the opponents do; the observational ratio is inflated by a "
                    "smaller denominator and by reverse causation through the fallback branch",
        },
        "gap_sizing": {
            "correct_closure_figure_gold_per_game": 210.0,
            "formula": "0.4327 * (4.0793 / 1.52 - 1.7128) * 500",
            "withdrawn": 300.0,
        },
        "fold_is_not_a_double_eat": {
            "standing_residual_zero_share_we_first": 0.927,
            "standing_residual_zero_share_we_second": 0.956,
            "note": "the dominant d==0 route is a blind unit already standing on its own "
                    "anchor, i.e. idle oscillation, not re-biting a rich cell",
        },
        "opponent_visibility_not_locally_measurable": {
            "selfplay_no_enemy_visible": [0.0678, 0.0640],
            "monotone_no_enemy_visible": [0.9766, 0.9173],
            "note": "self-play puts both seats on identical anchors; the platform's ~56% "
                    "both-invisible figure remains the anchor",
        },
        "open_loop_strip_figure_withdrawn": {
            "open_loop": 898.9,
            "closed_loop_consistent": 595.3,
            "note": "the open-loop variant evaluates our counterfactual path on the "
                    "unstripped board and inflates by 51%",
        },
    }
    out["simulator_reproduces_order_asymmetry"] = {
        "platform_our_ratio": 2.385,
        "local_our_ratio_seat1": 2.6676,
        "local_our_ratio_seat2": 2.2536,
        "platform_ratio_of_ratios": 1.448,
        "local_ratio_of_ratios_vs_monotone_reference": 1.540,
        "consequence": "every prior local A/B in this repository ran --fixed-costs 200,201 and "
                       "is therefore a first-mover measurement only",
    }
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
            lo, hi = part.split(":", 1)
            out.extend(str(v) for v in range(int(lo), int(hi)))
        else:
            out.append(part)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("contract", "step0", "scarcity", "fold", "foldprobe", "visibility",
                 "verify", "bound", "asymmetry", "assemble"):
        item = sub.add_parser(name)
        item.add_argument("--out", type=Path)
        if name == "assemble":
            item.add_argument("--artifacts", type=Path, default=Path("/tmp/gr_order"))
        if name not in ("contract", "assemble"):
            item.add_argument("--base", type=Path, required=True)
            item.add_argument("--map", default="map1")
            item.add_argument("--seeds", default="1000:1005")
        if name == "bound":
            item.add_argument("--arms", default=",".join(n for n, _r in ARMS))
            item.add_argument("--seat", type=int, default=1)
        if name == "fold":
            item.add_argument("--arms", default=",".join(n for n, _l, _t in FOLD_ARMS))
            item.add_argument("--seat", type=int, default=1)
        if name == "visibility":
            item.add_argument("--opponent", default="self",
                              choices=("self", "monotone"))
    args = parser.parse_args(argv)

    if args.command == "contract":
        payload: Any = contract()
    elif args.command == "assemble":
        payload = assemble(args.artifacts)
    elif args.command == "step0":
        payload = step0(args.map, args.base, _seeds(args.seeds))
    elif args.command == "scarcity":
        payload = scarcity(args.map, args.base, _seeds(args.seeds))
    elif args.command == "foldprobe":
        payload = foldprobe(args.map, args.base, _seeds(args.seeds))
    elif args.command == "fold":
        payload = fold(
            args.map, args.base, _seeds(args.seeds),
            arms=tuple(a for a in args.arms.split(",") if a), seat=args.seat,
        )
    elif args.command == "visibility":
        payload = visibility(args.map, args.base, _seeds(args.seeds),
                             opponent=getattr(args, "opponent", "self"))
    elif args.command == "verify":
        payload = verify(args.map, args.base, _seeds(args.seeds))
    elif args.command == "asymmetry":
        payload = asymmetry(args.map, args.base, _seeds(args.seeds))
    else:
        payload = bound(
            args.map, args.base, _seeds(args.seeds),
            arms=tuple(a for a in args.arms.split(",") if a), seat=args.seat,
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
