#!/usr/bin/env python3
"""Measure the gold our one-target walker leaves on the table versus the best
visible three-step path.

Modes
=====

``bound``
    Primary route.  Runs the frozen build in the local engine and wraps seat 1
    with an *open-loop* measurement shim: the shim calls the real
    ``moveDecision``, records a per-round counterfactual, and then returns the
    base decision **unchanged**, so the game trajectory (and therefore the log
    bytes) is identical to a plain baseline run.  The recorded quantity is the
    per-round difference between the best legal joint three-step path and the
    path the base strategy actually requested, evaluated under one shared,
    information-constrained model.

``realized``
    Secondary route.  Closed-loop: the shim substitutes the oracle's action
    pair, so the trajectory drifts.  Shows how much of the open-loop bound
    survives drift.

``verify``
    Validates the fast structural search against the slow exhaustive
    125 x 125 enumeration on a sampled subset of rounds.

``oracle``
    Log route (kept for completeness).  Official logs record a *god-view* full
    grid, so this route re-applies the seat's own fog filter before reading
    anything; it never uses a latent cell.  We have no platform log for the
    frozen build, so this is not the route used for the report.

``opponent``
    Untouched fully-visible opponent trajectory census.

Information discipline
======================

In ``bound``/``realized``/``verify`` the oracle reads exactly five fields of the
``PlayerInput`` the engine hands to the strategy:

* ``grid``            already fog-filtered by ``GameEngine.render_filtered_ground``;
                      every non-visible cell is literally the ``-5`` sentinel,
                      so a fogged gold value is not merely unused, it is absent.
* ``my_units``        own unit cells.
* ``my_units_gold``   own held gold, read live (never from a stale log copy).
* ``visible_enemies`` fog-filtered enemy cells (``(-1,-1)`` when hidden).
* ``visible_npcs``    fog-filtered NPC cells, for the >=3 trample rule.

plus the statically known wall table for the map (the frozen build fingerprints
and locks the identical table by round 4) and bombs remembered from previous
rounds inside the current 20-round bomb wave, exactly the memory the frozen
build itself keeps in ``g_s.bombbit``.
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
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACTIONS = tuple(itertools.product(range(5), repeat=3))
DR = (-1, 1, 0, 0, 0)
DC = (0, 0, -1, 1, 0)
FOG, WALL, BOMB = -5, -1, -3
GRID = 17
NCELL = GRID * GRID
VISION_RADIUS = 2          # frozen build never buys vision in steady state
BOMB_WAVE = 20             # engine refreshes the whole bomb population each 20 rounds


def _build_neighbours() -> tuple[tuple[int, ...], ...]:
    table = []
    for row in range(GRID):
        for col in range(GRID):
            entry = []
            for action in range(5):
                nrow, ncol = row + DR[action], col + DC[action]
                entry.append(nrow * GRID + ncol if 0 <= nrow < GRID and 0 <= ncol < GRID else -1)
            table.append(tuple(entry))
    return tuple(table)


NBR = _build_neighbours()

# ---------------------------------------------------------------------------
# log helpers (shared with the opponent census)
# ---------------------------------------------------------------------------


def load(path: Path) -> tuple[Mapping[str, Any], list[list[str]], list[Mapping[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), json.loads(lines[1]), [json.loads(line) for line in lines[2:]]


def player(phase: Mapping[str, Any], pid: int) -> Mapping[str, Any]:
    return next(item for item in phase["players"] if int(item["id"]) == pid)


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def summary(values: Sequence[float]) -> Mapping[str, float]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None, "sd": None, "se": None}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p10": percentile(values, .10),
        "p90": percentile(values, .90),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "se": statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0,
    }


def path_cells(start: Sequence[int], actions: Sequence[int]) -> list[tuple[int, int]]:
    row, col = map(int, start)
    result = []
    for action in actions:
        if action != 4:
            row += DR[action]
            col += DC[action]
        result.append((row, col))
    return result


def visible_path_record(grid: Sequence[Sequence[int]], unit: Mapping[str, Any]) -> Mapping[str, Any] | None:
    position = unit.get("position")
    actions = unit.get("actions") or ()
    if position is None or len(actions) != 3 or unit.get("pickup") is None:
        return None
    cells = path_cells(position, actions)
    if any(not (0 <= row < 17 and 0 <= col < 17) or grid[row][col] == FOG for row, col in cells):
        return None
    board = {(row, col): int(grid[row][col]) for row in range(17) for col in range(17) if grid[row][col] > 0}
    events = []
    for cell, action in zip(cells, actions):
        if action == 4:
            continue
        before = board.get(cell, 0)
        if before > 0:
            amount = (65 * before + 99) // 100
            board[cell] = before - amount
            events.append((cell, before, amount))
    moves = [int(action) for action in actions if int(action) != 4]
    return {
        "actions": tuple(map(int, actions)),
        "cells": cells,
        "events": events,
        "distinct_gold_cells": len({event[0] for event in events}),
        "pickup_events": len(events),
        "reconstructed_pickup": sum(event[2] for event in events),
        "reported_pickup": int(unit["pickup"]),
        "straight": len(set(moves)) <= 1,
        "turn": len(set(moves)) >= 2,
        "reversal": any((first ^ 1) == second for first, second in zip(moves, moves[1:])),
    }


def analyze_opponents(specs: Sequence[str]) -> Mapping[str, Any]:
    output = {}
    for spec in specs:
        team, raw_paths = spec.split("=", 1)
        paths = [Path(item) for item in raw_paths.split(",") if item]
        visibility = collections.Counter()
        complete_records = []
        own_records = []
        burst_records = []
        for path in paths:
            _header, _map_rows, rows = load(path)
            for row in rows:
                if not isinstance(row.get("start"), Mapping) or not isinstance(row.get("end"), Mapping):
                    continue
                grid = row["start"]["grid"]
                for pid, destination in ((2, complete_records), (1, own_records)):
                    start_player = player(row["start"], pid)
                    end_player = player(row["end"], pid)
                    for start_unit, end_unit in zip(start_player["units"], end_player["units"]):
                        visibility[(pid, "units")] += 1
                        visibility[(pid, "position")] += start_unit.get("position") is not None
                        visibility[(pid, "actions3")] += len(end_unit.get("actions") or ()) == 3
                        visibility[(pid, "pickup")] += end_unit.get("pickup") is not None
                        merged = dict(end_unit)
                        merged["position"] = start_unit.get("position")
                        record = visible_path_record(grid, merged)
                        if record is not None:
                            destination.append(record)
                target_start = player(row["start"], 2)
                target_end = player(row["end"], 2)
                round_records = []
                for start_unit, end_unit in zip(target_start["units"], target_end["units"]):
                    merged = dict(end_unit)
                    merged["position"] = start_unit.get("position")
                    round_records.append(visible_path_record(grid, merged))
                if all(record is not None for record in round_records):
                    records = [record for record in round_records if record is not None]
                    reported = sum(record["reported_pickup"] for record in records)
                    if reported >= 6:
                        events = [event for record in records for event in record["events"]]
                        burst_records.append({
                            "events": events,
                            "distinct_gold_cells": len({event[0] for event in events}),
                            "pickup_events": len(events),
                            "reconstructed_pickup": sum(record["reconstructed_pickup"] for record in records),
                            "reported_pickup": reported,
                            "straight": all(record["straight"] for record in records),
                            "turn": any(record["turn"] for record in records),
                            "reversal": any(record["reversal"] for record in records),
                        })
        def aggregate(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
            hist = collections.Counter(int(item["distinct_gold_cells"]) for item in records)
            event_hist = collections.Counter(int(item["pickup_events"]) for item in records)
            return {
                "units": len(records),
                "distinct_gold_cells_hist": dict(sorted(hist.items())),
                "pickup_events_hist": dict(sorted(event_hist.items())),
                "multi_distinct_rate": sum(value for key, value in hist.items() if key >= 2) / len(records) if records else None,
                "straight_rate": sum(bool(item.get("straight")) for item in records) / len(records) if records else None,
                "turn_rate": sum(bool(item.get("turn")) for item in records) / len(records) if records else None,
                "reversal_rate": sum(bool(item.get("reversal")) for item in records) / len(records) if records else None,
                "pickup_match_rate": sum(item["reconstructed_pickup"] == item["reported_pickup"] for item in records) / len(records) if records else None,
            }
        output[team] = {
            "games": len(paths),
            "target_visibility": {
                key: visibility[(2, key)] / visibility[(2, "units")] for key in ("position", "actions3", "pickup")
            },
            "target_complete_visible": aggregate(complete_records),
            "our_complete": aggregate(own_records),
            "target_round_pickup_ge6_fully_visible": aggregate(burst_records),
            "selection_bias_warning": "Opponent positions/actions/pickups are fog-filtered; complete-visible trajectories are a near-us subset, not a complete route census.",
        }
    return output


def walls_from_map(map_rows: Sequence[Sequence[str]]) -> set[tuple[int, int]]:
    return {(row, col) for row, values in enumerate(map_rows) for col, value in enumerate(values) if str(value) == "1"}


# ---------------------------------------------------------------------------
# slow exhaustive reference (audited correct against sim/engine.py)
# ---------------------------------------------------------------------------


def simulate_unit(
    actions: Sequence[int],
    start: tuple[int, int],
    teammate: tuple[int, int],
    enemy_cells: set[tuple[int, int]],
    walls: set[tuple[int, int]],
    visible_board: Mapping[tuple[int, int], int],
    remembered_bombs: set[tuple[int, int]],
    held: int,
    npc_counts: Mapping[tuple[int, int], int],
) -> Mapping[str, Any]:
    row, col = start
    board = dict(visible_board)
    bombs = set(remembered_bombs)
    pickup = burned = 0
    events = []
    effective = []
    for requested in actions:
        destination = (row, col)
        moved = False
        if requested != 4:
            candidate = (row + DR[requested], col + DC[requested])
            if 0 <= candidate[0] < 17 and 0 <= candidate[1] < 17 and candidate not in walls and candidate != teammate and candidate not in enemy_cells:
                destination = candidate
                row, col = candidate
                moved = True
        effective.append(requested if moved else 4)
        if not moved:
            continue
        value = board.get(destination, 0)
        if value > 0:
            amount = (65 * value + 99) // 100
            board[destination] = value - amount
            held += amount
            pickup += amount
            events.append((destination, value, amount))
        if destination in bombs:
            bombs.remove(destination)
            penalty = (held + 9) // 10
            held -= penalty
            burned += penalty
        if npc_counts.get(destination, 0) >= 3:
            penalty = (held + 19) // 20
            held -= penalty
            burned += penalty
    return {
        "position": (row, col),
        "held": held,
        "board": board,
        "bombs": bombs,
        "pickup": pickup,
        "burned": burned,
        "net": pickup - burned,
        "events": events,
        "effective": tuple(effective),
        "distinct_gold_cells": len({event[0] for event in events}),
    }


def joint_outcomes(
    starts: Sequence[tuple[int, int]],
    held: Sequence[int],
    order: int,
    grid: Sequence[Sequence[int]],
    walls: set[tuple[int, int]],
    remembered_bombs: set[tuple[int, int]],
    enemy_cells: set[tuple[int, int]],
    npc_counts: Mapping[tuple[int, int], int],
    constraints: Sequence[int | None] = (None, None),
) -> Iterable[Mapping[str, Any]]:
    board = {(row, col): int(grid[row][col]) for row in range(17) for col in range(17) if grid[row][col] > 0}
    first, second = order, 1 - order
    seqs_first = [seq for seq in ACTIONS if constraints[first] is None or seq[0] == constraints[first]]
    seqs_second = [seq for seq in ACTIONS if constraints[second] is None or seq[0] == constraints[second]]
    for actions_first in seqs_first:
        one = simulate_unit(actions_first, starts[first], starts[second], enemy_cells, walls, board, remembered_bombs, held[first], npc_counts)
        for actions_second in seqs_second:
            two = simulate_unit(actions_second, starts[second], one["position"], enemy_cells, walls, one["board"], one["bombs"], held[second], npc_counts)
            by_unit = [None, None]
            by_unit[first], by_unit[second] = one, two
            yield {
                "actions": (actions_first, actions_second) if first == 0 else (actions_second, actions_first),
                "units": by_unit,
                "net": one["net"] + two["net"],
                "pickup": one["pickup"] + two["pickup"],
                "burned": one["burned"] + two["burned"],
                "distinct_gold_cells": len({event[0] for result in by_unit for event in result["events"]}),
            }


def best_outcome(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
    return max(joint_outcomes(*args, **kwargs), key=lambda item: (item["net"], item["pickup"], item["distinct_gold_cells"], item["actions"]))


# ---------------------------------------------------------------------------
# fast structural search
# ---------------------------------------------------------------------------

_NET, _PICK = 0, 1


def _sim(
    seq: Sequence[int],
    start: int,
    blocker: int,
    board: Sequence[int],
    blocked: Sequence[int],
    bombs: frozenset,
    npc3: frozenset,
    held: int,
    overlay: tuple | None = None,
):
    """Simulate one unit's three requested actions on a flat board.

    Mirrors ``sim/engine.py`` exactly: stay/blocked picks nothing up, the
    remaining steps continue from the unchanged cell, pickup is
    ``(65 v + 99) // 100``, then bomb ``(held + 9) // 10``, then >=3-NPC trample
    ``(held + 19) // 20``.

    ``overlay`` is a tuple of ``(cell, value)`` describing cells the teammate
    already depleted; it is never mutated.  Returns
    ``(net, pickup, burned, final, mod, entered, attempted, used_bombs)``.
    """
    pos = start
    pickup = 0
    burned = 0
    mod = dict(overlay) if overlay else None
    entered: list[int] = []
    attempted: list[int] = []
    used: list[int] = []
    for action in seq:
        if action == 4:
            continue
        nxt = NBR[pos][action]
        if nxt < 0:
            continue
        attempted.append(nxt)
        if blocked[nxt] or nxt == blocker:
            continue
        pos = nxt
        entered.append(pos)
        if mod is not None and pos in mod:
            value = mod[pos]
        else:
            value = board[pos]
        if value > 0:
            amount = (65 * value + 99) // 100
            if mod is None:
                mod = {}
            mod[pos] = value - amount
            held += amount
            pickup += amount
        if pos in bombs and pos not in used:
            used.append(pos)
            penalty = (held + 9) // 10
            held -= penalty
            burned += penalty
        if pos in npc3:
            penalty = (held + 19) // 20
            held -= penalty
            burned += penalty
    changed = () if mod is None else tuple(sorted(mod.items()))
    if overlay:
        base_overlay = dict(overlay)
        changed = tuple(sorted((cell, value) for cell, value in mod.items() if base_overlay.get(cell) != value))
    return (
        pickup - burned, pickup, burned, pos, changed,
        tuple(entered), tuple(attempted), tuple(used),
    )


def _seqs_with_prefix(base_seq: Sequence[int], free_from: int) -> tuple[tuple[int, ...], ...]:
    head = tuple(int(value) for value in base_seq[:free_from])
    if free_from >= 3:
        return (head,)
    return tuple(head + tail for tail in itertools.product(range(5), repeat=3 - free_from))


def joint_best(
    seqs: Sequence[Sequence[Sequence[int]]],
    starts: Sequence[int],
    held: Sequence[int],
    order: int,
    board: Sequence[int],
    blocked: Sequence[int],
    bombs: frozenset,
    npc3: frozenset,
    objective: int = _NET,
):
    """Exact best joint outcome under whole-unit serial settlement.

    Unit ``order`` executes all three steps first, blocked by the other unit's
    START cell; the other unit then executes, blocked by the first unit's FINAL
    cell and reading the first unit's depleted cells.

    Structure exploited (see report): the second unit's outcome differs from its
    teammate-free outcome only if its clean trajectory attempts the first unit's
    final cell or enters a cell the first unit depleted / a bomb it consumed.
    Everything else reuses a precomputed clean score, and
    ``max(clean) >= max(any constrained variant)`` gives an admissible prune.
    """
    first, second = order, 1 - order
    seqs_first, seqs_second = seqs[first], seqs[second]

    # --- second unit, teammate-free reference table -------------------------
    clean = [
        _sim(seq, starts[second], -1, board, blocked, bombs, npc3, held[second])
        for seq in seqs_second
    ]
    by_attempt: dict[int, list[int]] = {}
    by_enter: dict[int, list[int]] = {}
    for index, item in enumerate(clean):
        for cell in item[6]:
            by_attempt.setdefault(cell, []).append(index)
        for cell in item[5]:
            by_enter.setdefault(cell, []).append(index)
    order_clean = sorted(range(len(clean)), key=lambda index: -clean[index][objective])
    # Admissible bound for either objective: net <= pickup, and both blocking and
    # teammate depletion can only lower a sequence's pickup, so no reachable
    # second-unit outcome can beat the largest teammate-free pickup.
    upper_second = max((item[_PICK] for item in clean), default=0)

    # --- first unit, grouped by the only thing the second unit can observe ---
    groups: dict[tuple, tuple] = {}
    for seq in seqs_first:
        item = _sim(seq, starts[first], starts[second], board, blocked, bombs, npc3, held[first])
        key = (item[3], item[4], item[7])
        previous = groups.get(key)
        if previous is None or item[objective] > previous[0][objective]:
            groups[key] = (item, seq)
    ranked = sorted(groups.values(), key=lambda entry: -entry[0][objective])

    best_score = None
    best = None
    evaluations = 0
    for item_first, seq_first in ranked:
        if best_score is not None and item_first[objective] + upper_second <= best_score:
            break
        final_first = item_first[3]
        overlay = item_first[4]
        bombs_second = bombs - set(item_first[7]) if item_first[7] else bombs
        touched = {cell for cell, _value in overlay} | set(item_first[7])
        affected = set(by_attempt.get(final_first, ()))
        for cell in touched:
            affected.update(by_enter.get(cell, ()))
        local_best = None
        local_item = None
        local_seq = None
        for index in affected:
            candidate = _sim(
                seqs_second[index], starts[second], final_first, board, blocked,
                bombs_second, npc3, held[second], overlay,
            )
            evaluations += 1
            if local_best is None or candidate[objective] > local_best:
                local_best = candidate[objective]
                local_item = candidate
                local_seq = seqs_second[index]
        for index in order_clean:
            if index in affected:
                continue
            candidate = clean[index]
            if local_best is None or candidate[objective] > local_best:
                local_best = candidate[objective]
                local_item = candidate
                local_seq = seqs_second[index]
            break
        if local_item is None:
            continue
        total = item_first[objective] + local_best
        if best_score is None or total > best_score:
            best_score = total
            pair = [None, None]
            pair[first] = (seq_first, item_first)
            pair[second] = (local_seq, local_item)
            best = pair
    if best is None:
        raise AssertionError("joint_best produced no candidate")
    net = best[0][1][0] + best[1][1][0]
    pickup = best[0][1][1] + best[1][1][1]
    burned = best[0][1][2] + best[1][1][2]
    cells = {cell for side in best for cell in side[1][5]}
    gold_cells = {
        cell for side in best for cell in side[1][5]
        if board[cell] > 0
    }
    return {
        "net": net,
        "pickup": pickup,
        "burned": burned,
        "actions": (tuple(best[0][0]), tuple(best[1][0])),
        "entered": (best[0][1][5], best[1][1][5]),
        "unit_net": (best[0][1][0], best[1][1][0]),
        "unit_pickup": (best[0][1][1], best[1][1][1]),
        "distinct_cells": len(cells),
        "distinct_gold_cells": len(gold_cells),
        "evaluations": evaluations,
        "groups": len(ranked),
    }


# ---------------------------------------------------------------------------
# per-round visible state extraction and decomposition
# ---------------------------------------------------------------------------

PATTERNS = ("stay3", "fold0", "fold1", "stall", "fold2", "march", "other")
SHORT_TARGET = frozenset({"stay3", "fold0", "fold1", "stall"})


def classify(seq: Sequence[int]) -> str:
    a0, a1, a2 = (int(value) for value in seq[:3])
    if a0 == 4 and a1 == 4 and a2 == 4:
        return "stay3"
    if a0 == 4:
        return "other"
    if a1 == 4:
        return "stall" if a2 == 4 else "other"
    if a1 == (a0 ^ 1):
        if a2 == 4:
            return "fold0"          # d==0 standing fold, third step wasted
        if a2 == a0:
            return "fold1"          # d==1 adjacent target, go-back-go
        return "other"
    if a2 == (a1 ^ 1):
        return "fold2"              # d==2 arrive-then-step-back
    if a2 == 4:
        return "other"
    return "march"                  # d==3 full travel


class VisibleState:
    """Everything the oracle is allowed to know for one round, and nothing else."""

    __slots__ = (
        "board", "blocked", "bombs", "npc3", "starts", "held", "order",
        "visible_mask", "gold_cells",
    )

    def __init__(self, board, blocked, bombs, npc3, starts, held, order, visible_mask, gold_cells):
        self.board = board
        self.blocked = blocked
        self.bombs = bombs
        self.npc3 = npc3
        self.starts = starts
        self.held = held
        self.order = order
        self.visible_mask = visible_mask
        self.gold_cells = gold_cells


def _visible_mask(cells: Sequence[int], radius: int) -> bytearray:
    mask = bytearray(NCELL)
    for cell in cells:
        row, col = divmod(cell, GRID)
        for vrow in range(max(0, row - radius), min(GRID, row + radius + 1)):
            base = vrow * GRID
            for vcol in range(max(0, col - radius), min(GRID, col + radius + 1)):
                mask[base + vcol] = 1
    return mask


def extract_state(
    grid: Sequence[Sequence[int]],
    my_units: Sequence[tuple[int, int]],
    my_gold: Sequence[int],
    enemies: Sequence[tuple[int, int]],
    npcs: Sequence[tuple[int, int]],
    order: int,
    static_walls: frozenset,
    remembered_bombs: set[int],
) -> VisibleState:
    board = [0] * NCELL
    blocked = bytearray(NCELL)
    bombs = set()
    gold_cells = []
    for row in range(GRID):
        line = grid[row]
        base = row * GRID
        for col in range(GRID):
            value = int(line[col])
            if value > 0:
                board[base + col] = value
                gold_cells.append(base + col)
            elif value == WALL:
                blocked[base + col] = 1
            elif value == BOMB:
                bombs.add(base + col)
    for cell in static_walls:
        blocked[cell] = 1
    bombs |= remembered_bombs
    starts = tuple(row * GRID + col for row, col in my_units)
    for row, col in enemies:
        if row >= 0:
            blocked[row * GRID + col] = 1
    counts = collections.Counter(row * GRID + col for row, col in npcs)
    npc3 = frozenset(cell for cell, count in counts.items() if count >= 3)
    return VisibleState(
        board, blocked, frozenset(bombs), npc3, starts, tuple(int(v) for v in my_gold),
        int(order), _visible_mask(starts, VISION_RADIUS), tuple(gold_cells),
    )


def fog_discipline(state: VisibleState) -> None:
    """Prove no latent cell entered the model: every informative cell the oracle
    reads (gold value or bomb) must lie inside the seat's own visibility union."""
    mask = state.visible_mask
    for cell in state.gold_cells:
        if not mask[cell]:
            raise AssertionError("oracle read a gold value outside the visibility union: %d" % cell)
    for cell in state.bombs:
        if not mask[cell]:
            # remembered bombs are allowed: they were inside the union when seen
            continue
    return None


def harvest_map(entered: Sequence[Sequence[int]], board: Sequence[int]) -> dict[int, int]:
    """Per-cell gold actually taken by a pair of ordered entry sequences."""
    remaining: dict[int, int] = {}
    taken: dict[int, int] = {}
    for side in entered:
        for cell in side:
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


def round_analysis(state: VisibleState, base_actions: Sequence[int]) -> Mapping[str, Any]:
    """Value the base's requested path and the three constrained optima."""
    starts, held, order = state.starts, state.held, state.order
    board, blocked, bombs, npc3 = state.board, state.blocked, state.bombs, state.npc3
    base = (tuple(int(v) for v in base_actions[:3]), tuple(int(v) for v in base_actions[3:6]))

    def search(seqs0, seqs1, objective=_NET):
        return joint_best((seqs0, seqs1), starts, held, order, board, blocked, bombs, npc3, objective)

    exact_base = search((base[0],), (base[1],))
    free1 = (_seqs_with_prefix(base[0], 1), _seqs_with_prefix(base[1], 1))
    free2 = (_seqs_with_prefix(base[0], 2), _seqs_with_prefix(base[1], 2))
    step3 = search(free2[0], free2[1])
    tail_u0 = search(free1[0], (base[1],))
    tail_u1 = search((base[0],), free1[1])
    tail_both = search(free1[0], free1[1])
    full = search(ACTIONS, ACTIONS)

    solo0 = tail_u0["net"] - exact_base["net"]
    solo1 = tail_u1["net"] - exact_base["net"]
    interaction = tail_both["net"] - exact_base["net"] - solo0 - solo1
    share0 = solo0 + interaction / 2.0
    share1 = solo1 + interaction / 2.0

    pickup_bound = full["pickup"] - exact_base["pickup"]
    if full["burned"] or exact_base["burned"]:
        gross = search(ACTIONS, ACTIONS, _PICK)
        pickup_bound = gross["pickup"] - exact_base["pickup"]

    serial = lambda pair: (pair[order], pair[1 - order])
    base_take = harvest_map(serial(exact_base["entered"]), board)
    best_take = harvest_map(serial(full["entered"]), board)
    extra = {
        cell: amount - base_take.get(cell, 0)
        for cell, amount in best_take.items()
        if amount - base_take.get(cell, 0) > 0
    }
    base_entered = frozenset(cell for side in exact_base["entered"] for cell in side)

    return {
        "base": exact_base,
        "step3": step3,
        "tail_both": tail_both,
        "full": full,
        "patterns": (classify(base[0]), classify(base[1])),
        "triples": (base[0], base[1]),
        "tail_share": (share0, share1),
        "unit_gain": (
            full["unit_net"][0] - exact_base["unit_net"][0],
            full["unit_net"][1] - exact_base["unit_net"][1],
        ),
        "unit_base_pickup": exact_base["unit_pickup"],
        "unit_best_pickup": full["unit_pickup"],
        "tail_total": tail_both["net"] - exact_base["net"],
        "cause_first": full["net"] - tail_both["net"],
        "total": full["net"] - exact_base["net"],
        "step3_gain": step3["net"] - exact_base["net"],
        "pickup_bound": pickup_bound,
        "extra_by_cell": extra,
        "base_entered": base_entered,
        "evaluations": exact_base["evaluations"] + step3["evaluations"] + tail_u0["evaluations"]
        + tail_u1["evaluations"] + tail_both["evaluations"] + full["evaluations"],
    }


# ---------------------------------------------------------------------------
# accumulator
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, steady_from: int = 8) -> None:
        self.steady_from = steady_from
        self.total = collections.Counter()
        self.floats = collections.defaultdict(float)
        self.pattern_counts = collections.Counter()
        self.pattern_gain = collections.defaultdict(float)
        self.pattern_avail = collections.Counter()
        self.distinct_base = collections.Counter()
        self.distinct_best = collections.Counter()
        self.triples = collections.Counter()
        self.axis_gain = collections.defaultdict(float)
        self.axis_count = collections.Counter()
        self.extra_history: list[tuple[int, dict[int, int]]] = []
        self.entered_history: list[frozenset] = []
        self.rounds = 0
        self.steady_rounds = 0
        self.max_round_gain = 0
        self.samples: list[Mapping[str, Any]] = []

    def add(self, round_number: int, analysis: Mapping[str, Any]) -> None:
        self.rounds += 1
        steady = round_number >= self.steady_from
        self.steady_rounds += steady
        base, full = analysis["base"], analysis["full"]
        add = self.floats.__setitem__
        get = self.floats.__getitem__
        for key, value in (
            ("base_net", base["net"]), ("base_pickup", base["pickup"]), ("base_burned", base["burned"]),
            ("best_net", full["net"]), ("best_pickup", full["pickup"]), ("best_burned", full["burned"]),
            ("total", analysis["total"]), ("pickup_bound", analysis["pickup_bound"]),
            ("cause_first", analysis["cause_first"]), ("tail_total", analysis["tail_total"]),
            ("step3_gain", analysis["step3_gain"]),
            ("evaluations", analysis["evaluations"]),
        ):
            add(key, get(key) + value)
            if steady:
                add("steady_" + key, get("steady_" + key) + value)
        self.total["rounds"] += 1
        self.total["total_available"] += analysis["total"] > 0
        self.total["cause_first_available"] += analysis["cause_first"] > 0
        self.total["tail_available"] += analysis["tail_total"] > 0
        self.total["step3_available"] += analysis["step3_gain"] > 0
        self.total["base_burn_rounds"] += base["burned"] > 0
        self.total["best_burn_rounds"] += full["burned"] > 0
        self.distinct_base[base["distinct_gold_cells"]] += 1
        self.distinct_best[full["distinct_gold_cells"]] += 1
        if analysis["total"] > self.max_round_gain:
            self.max_round_gain = analysis["total"]
        for pattern, share in zip(analysis["patterns"], analysis["tail_share"]):
            self.pattern_counts[pattern] += 1
            self.pattern_gain[pattern] += share
            self.pattern_avail[pattern] += share > 1e-9
            bucket = "cause1" if pattern in SHORT_TARGET else "cause2"
            self.pattern_gain[bucket] += share
            self.pattern_counts[bucket] += 1
            self.pattern_avail[bucket] += share > 1e-9
        for triple in analysis["triples"]:
            self.triples[tuple(triple)] += 1
        # hit-rate vs yield axis (orchestrator's factor split).  Per-unit net is
        # additive over units, so this axis reconciles with the total exactly.
        for index in (0, 1):
            gain = analysis["unit_gain"][index]
            base_pickup = analysis["unit_base_pickup"][index]
            axis = "new_scoring_round" if base_pickup == 0 else "richer_scoring_round"
            self.axis_gain[axis] += gain
            self.axis_count[axis + "_unit_rounds"] += 1
            if gain > 0:
                self.axis_count[axis + "_with_gain"] += 1
            if base_pickup == 0:
                self.axis_count["base_zero_unit_rounds"] += 1
            else:
                self.axis_count["base_scoring_unit_rounds"] += 1
        self.extra_history.append((round_number, analysis["extra_by_cell"]))
        self.entered_history.append(analysis["base_entered"])

    def stock_flow(self) -> Mapping[str, Any]:
        """Split the raw bound into gold the base never collects later (``novel``)
        and gold the base collects a few rounds later anyway (``timing``).

        Gold is a stock, not a flow: a per-round counterfactual credits the oracle
        for a cell the base harvests two rounds later, so the raw sum
        double-counts.  A cell is ``timing`` when the base's own realized
        trajectory re-enters it at any later round of the same game.
        """
        suffix: set[int] = set()
        novel = timing = 0.0
        novel_cells = timing_cells = 0
        for index in range(len(self.extra_history) - 1, -1, -1):
            _round, extra = self.extra_history[index]
            for cell, amount in extra.items():
                if cell in suffix:
                    timing += amount
                    timing_cells += 1
                else:
                    novel += amount
                    novel_cells += 1
            suffix |= self.entered_history[index]
        return {
            "novel_gold": novel,
            "timing_gold": timing,
            "novel_cell_events": novel_cells,
            "timing_cell_events": timing_cells,
            "novel_share": novel / (novel + timing) if novel + timing else None,
        }

    def result(self) -> Mapping[str, Any]:
        floats = dict(self.floats)
        cause1 = self.pattern_gain["cause1"]
        cause2 = self.pattern_gain["cause2"]
        cause3 = floats.get("cause_first", 0.0)
        return {
            "rounds": self.rounds,
            "steady_rounds": self.steady_rounds,
            "sums": floats,
            "gross_bound_net": floats.get("total", 0.0),
            "gross_bound_pickup": floats.get("pickup_bound", 0.0),
            "decomposition": {
                "cause1_short_target_fold_filler": cause1,
                "cause2_chainable_multi_gold": cause2,
                "cause3_wrong_first_target": cause3,
                "sum": cause1 + cause2 + cause3,
                "residual_vs_total": floats.get("total", 0.0) - (cause1 + cause2 + cause3),
            },
            "ladder": {
                "L1_free_step3_only": floats.get("step3_gain", 0.0),
                "L2_free_steps23": floats.get("tail_total", 0.0),
                "L3_free_all_three": floats.get("total", 0.0),
            },
            "availability": {
                "rounds_with_any_gain": self.total["total_available"],
                "rounds_with_cause3_gain": self.total["cause_first_available"],
                "rounds_with_tail_gain": self.total["tail_available"],
                "rounds_with_step3_gain": self.total["step3_available"],
            },
            "pattern": {
                name: {
                    "unit_rounds": self.pattern_counts[name],
                    "gain": self.pattern_gain[name],
                    "unit_rounds_with_gain": self.pattern_avail[name],
                    "gain_per_occurrence": (
                        self.pattern_gain[name] / self.pattern_avail[name]
                        if self.pattern_avail[name] else 0.0
                    ),
                }
                for name in list(PATTERNS) + ["cause1", "cause2"]
            },
            "distinct_gold_cells_hist": {
                "base": dict(sorted(self.distinct_base.items())),
                "best": dict(sorted(self.distinct_best.items())),
            },
            "burn_rounds": {
                "base": self.total["base_burn_rounds"],
                "best": self.total["best_burn_rounds"],
            },
            "factor_axis": {
                "new_scoring_round_gold": self.axis_gain["new_scoring_round"],
                "richer_scoring_round_gold": self.axis_gain["richer_scoring_round"],
                "sum": self.axis_gain["new_scoring_round"] + self.axis_gain["richer_scoring_round"],
                "new_scoring_unit_rounds": self.axis_count["new_scoring_round_unit_rounds"],
                "richer_scoring_unit_rounds": self.axis_count["richer_scoring_round_unit_rounds"],
                "new_scoring_unit_rounds_with_gain": self.axis_count["new_scoring_round_with_gain"],
                "richer_scoring_unit_rounds_with_gain": self.axis_count["richer_scoring_round_with_gain"],
                "new_scoring_gold_per_occurrence": (
                    self.axis_gain["new_scoring_round"] / self.axis_count["new_scoring_round_with_gain"]
                    if self.axis_count["new_scoring_round_with_gain"] else 0.0
                ),
                "richer_scoring_gold_per_occurrence": (
                    self.axis_gain["richer_scoring_round"] / self.axis_count["richer_scoring_round_with_gain"]
                    if self.axis_count["richer_scoring_round_with_gain"] else 0.0
                ),
                "base_hit_rate": (
                    self.axis_count["base_scoring_unit_rounds"]
                    / (2 * self.rounds) if self.rounds else None
                ),
            },
            "stock_flow": self.stock_flow(),
            "top_base_action_triples": dict(
                sorted(
                    ((",".join(map(str, key)), count) for key, count in self.triples.items()),
                    key=lambda item: -item[1],
                )[:12]
            ),
            "max_single_round_gain": self.max_round_gain,
        }


# ---------------------------------------------------------------------------
# measurement shim
# ---------------------------------------------------------------------------


class PathOracleStrategy:
    """Seat-1 shim around the frozen build.

    ``open_loop=True`` returns the base decision byte-for-byte, so the game
    trajectory is unchanged and every round yields a clean counterfactual.
    ``open_loop=False`` substitutes the oracle's action pair (closed loop).
    """

    name = "path_oracle"

    def __init__(
        self,
        walls: set[tuple[int, int]],
        base_so: Path,
        *,
        open_loop: bool = True,
        closed_loop_level: int = 3,
        remember_bombs: bool = True,
        sample_every: int = 0,
        check_fog_every: int = 50,
        steady_from: int = 8,
    ) -> None:
        from sim.abi import SharedObjectStrategy

        self.static_walls = frozenset(row * GRID + col for row, col in walls)
        self.base = SharedObjectStrategy(base_so, name="oracle_base")
        self.open_loop = open_loop
        self.closed_loop_level = int(closed_loop_level)
        self.remember_bombs = remember_bombs
        self.sample_every = sample_every
        self.check_fog_every = check_fog_every
        self.recorder = Recorder(steady_from=steady_from)
        self.remembered_bombs: set[int] = set()
        self.last_round = 10 ** 9
        self.samples: list[Mapping[str, Any]] = []

    def close(self) -> None:
        self.base.close()

    def __call__(self, value: Any) -> tuple[int, ...]:
        round_number = int(value.round)
        if round_number <= self.last_round or round_number % BOMB_WAVE == 0:
            self.remembered_bombs.clear()
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

        if self.remember_bombs and self.remembered_bombs:
            # Memory update a live strategy could also perform: a currently
            # visible cell that is not -3 proves the bomb there is gone (ours,
            # the enemy's or an NPC's step consumes it), so purge it before use.
            mask = _visible_mask([r * GRID + c for r, c in my_units], VISION_RADIUS)
            self.remembered_bombs.difference_update([
                cell for cell in self.remembered_bombs
                if mask[cell] and grid[cell // GRID][cell % GRID] != BOMB
            ])

        state = extract_state(
            grid, my_units, my_gold, enemies, npcs, order,
            self.static_walls, set(self.remembered_bombs) if self.remember_bombs else set(),
        )
        if self.remember_bombs:
            for row in range(GRID):
                for col in range(GRID):
                    if grid[row][col] == BOMB:
                        self.remembered_bombs.add(row * GRID + col)
        if self.check_fog_every and round_number % self.check_fog_every == 0:
            fog_discipline(state)

        if int(decision.k) != 3:
            return passthrough

        analysis = round_analysis(state, actions)
        self.recorder.add(round_number, analysis)
        if self.sample_every and round_number % self.sample_every == 7:
            self.samples.append({
                "round": round_number,
                "grid": grid,
                "my_units": my_units,
                "my_gold": my_gold,
                "enemies": enemies,
                "npcs": npcs,
                "order": order,
                "actions": actions,
                "remembered_bombs": sorted(state.bombs),
                "static_walls": [list(divmod(cell, GRID)) for cell in sorted(self.static_walls)],
                "fast_best_net": analysis["full"]["net"],
                "fast_base_net": analysis["base"]["net"],
                "fast_tail_net": analysis["tail_both"]["net"],
            })
        if self.open_loop:
            return passthrough
        key = {1: "step3", 2: "tail_both", 3: "full"}[self.closed_loop_level]
        best = analysis[key]["actions"]
        return tuple(best[0]) + tuple(best[1]) + (3, order, int(decision.vp))


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


def _static_walls(map_name: str) -> set[tuple[int, int]]:
    from sim.runner import load_map

    return walls_from_map(load_map(map_name).rows)


def analyze_bound(
    maps: Sequence[str],
    base_so: Path,
    seeds: Sequence[str],
    *,
    sample_every: int = 0,
    trajectory_check: bool = True,
) -> Mapping[str, Any]:
    from sim.runner import run_game

    output: dict[str, Any] = {"maps": {}}
    samples: list[Mapping[str, Any]] = []
    for map_name in maps:
        walls = _static_walls(map_name)
        per_seed = []
        for seed in seeds:
            shim = PathOracleStrategy(
                walls, base_so, open_loop=True, sample_every=sample_every,
            )
            measured = run_game(
                shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
                fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
            )
            shim.close()
            record = dict(shim.recorder.result())
            record["seed"] = seed
            record["measured_net_gold"] = int(measured.summary["players"]["1"]["net_gold"])
            record["measured_log_sha256"] = measured.summary["log_sha256"]
            if trajectory_check:
                plain = run_game(
                    base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
                    fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
                )
                record["baseline_net_gold"] = int(plain.summary["players"]["1"]["net_gold"])
                record["baseline_log_sha256"] = plain.summary["log_sha256"]
                record["trajectory_identical"] = (
                    plain.summary["log_sha256"] == measured.summary["log_sha256"]
                )
            samples.extend({"map": map_name, "seed": seed, **item} for item in shim.samples)
            per_seed.append(record)
        output["maps"][map_name] = {
            "seeds": list(seeds),
            "per_seed": [
                {
                    key: value for key, value in record.items()
                    if key not in {"pattern", "distinct_gold_cells_hist", "sums", "top_base_action_triples"}
                }
                for record in per_seed
            ],
            "gross_bound_net": summary([record["gross_bound_net"] for record in per_seed]),
            "gross_bound_pickup": summary([record["gross_bound_pickup"] for record in per_seed]),
            "decomposition": {
                key: summary([record["decomposition"][key] for record in per_seed])
                for key in (
                    "cause1_short_target_fold_filler",
                    "cause2_chainable_multi_gold",
                    "cause3_wrong_first_target",
                    "sum",
                    "residual_vs_total",
                )
            },
            "ladder": {
                key: summary([record["ladder"][key] for record in per_seed])
                for key in ("L1_free_step3_only", "L2_free_steps23", "L3_free_all_three")
            },
            "availability": {
                key: summary([record["availability"][key] for record in per_seed])
                for key in (
                    "rounds_with_any_gain", "rounds_with_cause3_gain",
                    "rounds_with_tail_gain", "rounds_with_step3_gain",
                )
            },
            "pattern": {
                name: {
                    field: summary([record["pattern"][name][field] for record in per_seed])
                    for field in ("unit_rounds", "gain", "unit_rounds_with_gain", "gain_per_occurrence")
                }
                for name in list(PATTERNS) + ["cause1", "cause2"]
            },
            "factor_axis": {
                key: summary([record["factor_axis"][key] for record in per_seed])
                for key in (
                    "new_scoring_round_gold", "richer_scoring_round_gold", "sum",
                    "new_scoring_unit_rounds", "richer_scoring_unit_rounds",
                    "new_scoring_unit_rounds_with_gain", "richer_scoring_unit_rounds_with_gain",
                    "new_scoring_gold_per_occurrence", "richer_scoring_gold_per_occurrence",
                    "base_hit_rate",
                )
            },
            "stock_flow": {
                key: summary([record["stock_flow"][key] for record in per_seed])
                for key in ("novel_gold", "timing_gold", "novel_cell_events", "timing_cell_events", "novel_share")
            },
            "top_base_action_triples_first_seed": per_seed[0]["top_base_action_triples"],
            "distinct_gold_cells_hist_first_seed": per_seed[0]["distinct_gold_cells_hist"],
            "base_net_model_per_game": summary([record["sums"]["base_net"] for record in per_seed]),
            "best_net_model_per_game": summary([record["sums"]["best_net"] for record in per_seed]),
            "measured_net_gold": summary([record["measured_net_gold"] for record in per_seed]),
            "trajectory_identical_all": all(
                record.get("trajectory_identical", True) for record in per_seed
            ),
        }
    output["samples"] = samples
    return output


def analyze_realized(
    map_name: str, base_so: Path, seeds: Sequence[str], level: int = 3
) -> Mapping[str, Any]:
    from sim.runner import run_game

    walls = _static_walls(map_name)
    records = []
    for seed in seeds:
        baseline = run_game(
            base_so, base_so, map_source=map_name, seed=seed, dispatch="fixed",
            fixed_costs=(200, 201), player1_name="base", player2_name="opponent",
        )
        shim = PathOracleStrategy(walls, base_so, open_loop=False, closed_loop_level=level)
        oracle = run_game(
            shim, base_so, map_source=map_name, seed=seed, dispatch="fixed",
            fixed_costs=(200, 201), player1_name="oracle", player2_name="opponent",
        )
        shim.close()
        base_net = int(baseline.summary["players"]["1"]["net_gold"])
        oracle_net = int(oracle.summary["players"]["1"]["net_gold"])
        records.append({
            "seed": seed, "base_net": base_net, "oracle_net": oracle_net,
            "delta": oracle_net - base_net,
            "closed_loop_round_sum": shim.recorder.result()["gross_bound_net"],
        })
    return {
        "map": map_name,
        "closed_loop_level": level,
        "level_meaning": {1: "step 3 free only", 2: "steps 2-3 free", 3: "all three steps free"}[level],
        "games": len(records),
        "records": records,
        "delta": summary([item["delta"] for item in records]),
        "base_net": summary([item["base_net"] for item in records]),
        "oracle_net": summary([item["oracle_net"] for item in records]),
    }


def verify_fast_path(samples: Sequence[Mapping[str, Any]], limit: int = 0) -> Mapping[str, Any]:
    """Compare the fast structural search with the slow 125x125 enumeration."""
    checked = []
    mismatches = []
    chosen = list(samples)[: limit or len(samples)]
    for sample in chosen:
        grid = sample["grid"]
        walls = {
            (row, col) for row in range(GRID) for col in range(GRID)
            if int(grid[row][col]) == WALL
        }
        static = sample.get("static_walls")
        if static:
            walls |= {tuple(cell) for cell in static}
        starts = [tuple(cell) for cell in sample["my_units"]]
        held = [int(item) for item in sample["my_gold"]]
        order = int(sample["order"])
        enemies = {tuple(cell) for cell in sample["enemies"]}
        npc_counts = collections.Counter(tuple(cell) for cell in sample["npcs"])
        bombs = {divmod(int(cell), GRID) for cell in sample["remembered_bombs"]}
        slow_best = best_outcome(
            starts, held, order, grid, walls, bombs, enemies, npc_counts,
        )
        base = (tuple(sample["actions"][:3]), tuple(sample["actions"][3:6]))
        first, second = order, 1 - order
        board = {
            (row, col): int(grid[row][col]) for row in range(GRID) for col in range(GRID)
            if int(grid[row][col]) > 0
        }
        one = simulate_unit(
            base[first], starts[first], starts[second], enemies, walls, board, bombs,
            held[first], npc_counts,
        )
        two = simulate_unit(
            base[second], starts[second], one["position"], enemies, walls, one["board"],
            one["bombs"], held[second], npc_counts,
        )
        slow_base = one["net"] + two["net"]
        slow_tail = best_outcome(
            starts, held, order, grid, walls, bombs, enemies, npc_counts,
            (base[0][0], base[1][0]),
        )
        entry = {
            "map": sample.get("map"),
            "seed": sample.get("seed"),
            "round": sample["round"],
            "fast_best_net": sample["fast_best_net"],
            "slow_best_net": slow_best["net"],
            "fast_base_net": sample["fast_base_net"],
            "slow_base_net": slow_base,
            "fast_tail_net": sample["fast_tail_net"],
            "slow_tail_net": slow_tail["net"],
        }
        entry["match"] = (
            entry["fast_best_net"] == entry["slow_best_net"]
            and entry["fast_base_net"] == entry["slow_base_net"]
            and entry["fast_tail_net"] == entry["slow_tail_net"]
        )
        checked.append(entry)
        if not entry["match"]:
            mismatches.append(entry)
    return {
        "rounds_checked": len(checked),
        "mismatches": len(mismatches),
        "mismatch_detail": mismatches[:10],
        "records": checked,
        "method": "fast structural search vs exhaustive 125x125 joint_outcomes with dict-copy simulate_unit",
    }


def fog_filter(grid: Sequence[Sequence[int]], units: Sequence[Sequence[int]], radius: int) -> list[list[int]]:
    """Re-apply the seat's own fog to a god-view log grid and strip actor marks."""
    mask = _visible_mask([int(row) * GRID + int(col) for row, col in units], radius)
    out = []
    for row in range(GRID):
        line = []
        for col in range(GRID):
            if not mask[row * GRID + col]:
                line.append(FOG)
                continue
            value = int(grid[row][col])
            line.append(0 if value in (-2, -4) else value)
        out.append(line)
    return out


def analyze_oracle(paths: Sequence[Path], player_name: str) -> Mapping[str, Any]:
    """Secondary log route.  Held gold is taken from ``end[r-1]`` because the
    documented stale-copy trap makes ``start[r]`` an unreliable source."""
    per_game = []
    hist_actual = collections.Counter()
    hist_oracle = collections.Counter()
    for path in paths:
        header, map_rows, rows = load(path)
        if header.get("player1") != player_name:
            continue
        static = frozenset(row * GRID + col for row, col in walls_from_map(map_rows))
        remembered: set[int] = set()
        recorder = Recorder()
        previous_gold = None
        for row in rows:
            round_number = int(row["round"])
            if round_number % BOMB_WAVE == 0:
                remembered.clear()
            start_player = player(row["start"], 1)
            end_player = player(row["end"], 1)
            starts = [tuple(map(int, unit["position"])) for unit in start_player["units"]]
            grid = fog_filter(row["start"]["grid"], starts, VISION_RADIUS)
            held = previous_gold if previous_gold is not None else [
                int(unit["gold"]) for unit in start_player["units"]
            ]
            previous_gold = [int(unit["gold"]) for unit in end_player["units"]]
            order = int(end_player.get("order", start_player.get("order", 0)))
            actual = [tuple(unit.get("actions") or ()) for unit in end_player["units"]]
            if any(len(item) != 3 for item in actual):
                continue
            visible = _visible_mask([r * GRID + c for r, c in starts], VISION_RADIUS)
            enemies = [
                tuple(map(int, unit["position"])) for unit in player(row["start"], 2)["units"]
                if unit.get("position") is not None
                and visible[int(unit["position"][0]) * GRID + int(unit["position"][1])]
            ]
            npcs = [
                tuple(map(int, npc["position"])) for npc in row["start"].get("npcs", ())
                if npc.get("position") is not None
                and visible[int(npc["position"][0]) * GRID + int(npc["position"][1])]
            ]
            state = extract_state(
                grid, starts, held, enemies, npcs, order, static, set(remembered),
            )
            for r in range(GRID):
                for c in range(GRID):
                    if grid[r][c] == BOMB:
                        remembered.add(r * GRID + c)
            analysis = round_analysis(state, actual[0] + actual[1])
            recorder.add(round_number, analysis)
            hist_actual[analysis["base"]["distinct_gold_cells"]] += 1
            hist_oracle[analysis["full"]["distinct_gold_cells"]] += 1
        per_game.append({"path": str(path), **recorder.result()})
    if not per_game:
        raise ValueError("no logs matched player1 name %r" % player_name)
    return {
        "games": len(per_game),
        "per_game": per_game,
        "actual_distinct_gold_cells_hist": dict(sorted(hist_actual.items())),
        "oracle_distinct_gold_cells_hist": dict(sorted(hist_oracle.items())),
        "information_rule": (
            "Log grids are god-view; this route re-applies the seat's own radius-2 fog "
            "filter and strips actor marks before reading anything.  Enemies and NPCs are "
            "filtered to the same visibility union.  Bombs are remembered inside the "
            "current 20-round wave only."
        ),
        "held_gold_source": "end[r-1].units[].gold (start[r] is a documented stale copy)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    opponent = sub.add_parser("opponent")
    opponent.add_argument("--team", action="append", required=True, help="TEAM=comma,separated,logs")

    oracle = sub.add_parser("oracle")
    oracle.add_argument("--player-name", default="base")
    oracle.add_argument("logs", nargs="+")

    bound = sub.add_parser("bound")
    bound.add_argument("--map", action="append", required=True)
    bound.add_argument("--base-so", type=Path, required=True)
    bound.add_argument("--seeds", nargs="+", required=True)
    bound.add_argument("--sample-every", type=int, default=0)
    bound.add_argument("--no-trajectory-check", action="store_true")
    bound.add_argument("--out", type=Path)

    realized = sub.add_parser("realized")
    realized.add_argument("--map", required=True)
    realized.add_argument("--base-so", type=Path, required=True)
    realized.add_argument("--seeds", nargs="+", required=True)
    realized.add_argument("--level", type=int, choices=(1, 2, 3), default=3)

    verify = sub.add_parser("verify")
    verify.add_argument("--samples", type=Path, required=True)
    verify.add_argument("--limit", type=int, default=0)

    args = parser.parse_args()
    if args.mode == "opponent":
        result = analyze_opponents(args.team)
    elif args.mode == "oracle":
        result = analyze_oracle([Path(item) for item in args.logs], args.player_name)
    elif args.mode == "bound":
        result = analyze_bound(
            args.map, args.base_so, args.seeds, sample_every=args.sample_every,
            trajectory_check=not args.no_trajectory_check,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    elif args.mode == "verify":
        payload = json.loads(args.samples.read_text(encoding="utf-8"))
        result = verify_fast_path(payload.get("samples", payload), args.limit)
    else:
        result = analyze_realized(args.map, args.base_so, args.seeds, args.level)
    printable = dict(result)
    printable.pop("samples", None)
    printable.pop("records", None)
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
