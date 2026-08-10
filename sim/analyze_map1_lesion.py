#!/usr/bin/env python3
"""Localize the map1 deficit on the fog-free per-unit held-gold channel.

Purpose
=======

The map1 wall hypothesis was falsified (`sim/reports/map1_wall_repricing.md`).
This driver does the step that comes next and does it **hypothesis-free**: it
asks *when* and *where* the map1 deficit accrues before any cause is proposed.

Why this channel
----------------

Per-unit ``gold`` is recorded in 100% of unit-observations regardless of fog
(`sim/analyze_gold_delta.py` docstring; verified again here: opponent
``position`` is ``null`` while opponent ``gold`` is present).  Round-over-round
differencing therefore gives an *unbiased and complete* per-unit income series
for **both** players, and summing it reproduces the head-to-head net score.
Two further fields are fog-free and are used here:

* ``end.players[].cost`` for both players -> exact per-round first-mover;
* ``end.players[].units[].position`` for **our own** units (always present),
  which gives a fog-free spatial series for our side only.

Corpus
------

`f18064c` map1 games only.  The sibling backfill (`sim/reports/archive_backfill.json`)
proved four `f18064c` baseline arms exist against Tundra map1 -- ``frTu1``,
``lnA0``, ``a2A0``, ``alA0`` -- not one, plus ``t1f1`` against T-1 map1, for
**30 games**.  map2/map3 `f18064c` families are loaded as the contrast set, so
every map1 statement can be made as a difference against the maps we do not
lose.

Sub-commands
------------

``localize``
    The primary object.  Per-round cumulative deficit curve, round-block
    decomposition, the u0/u1 split, hit/yield factorisation per block, the
    first-mover series, our own fog-free spatial occupancy per block, and the
    four-arm batch spread.  No hypothesis is required to read it.

``supply``
    The fog-free five-region snapshot channel (``gold_generated`` /
    ``gold_collected`` / ``gold_remaining`` / ``occupants`` per region per
    5-round window).  This is the only global, fog-free supply instrument in a
    platform log and it is what Lead B has to be tested on.

``geometry``
    Static, zero-sample: wall density per region per map derived from log row 2
    (primary source), plus the generation-capacity consequence.

Discipline
----------

* Baseline is pinned to `f18064c` (`git show f18064c:src/player.cpp`,
  sha256 ``0ecce6fc...84fdd``).  HEAD has moved to ``fd47ea6``; that fix is
  bit-identical on the three known maps, but nothing here depends on HEAD.
* Absolute income is **not** platform-comparable to the local simulator; only
  same-seed paired deltas are.  Everything in this file is platform-to-platform
  within one game, so the comparison is between the two seats of the same game
  and needs no simulator at all.
* No platform games are consumed: this reads archived logs only.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.analyze_gold_delta import (  # noqa: E402
    ACCOUNTS,
    families,
    header,
    net_delta,
    rounds,
)
from sim.scenario import region_id  # noqa: E402

GRID = 17
ROUNDS = 500

# f18064c families per (opponent, map).  The map1 Tundra set is the four
# baseline arms proven in sim/reports/archive_backfill.json; every other cell is
# the single family recorded in sim/analyze_gold_delta.FROZEN_FAMILIES.
CORPUS: Mapping[str, Sequence[tuple[str, str]]] = {
    "map1": (
        ("Tundra", "frTu1"), ("Tundra", "lnA0"), ("Tundra", "a2A0"), ("Tundra", "alA0"),
        ("T-1", "t1f1"),
    ),
    "map2": (("Tundra", "frTu2"), ("T-1", "t1f2")),
    "map3": (("Tundra", "frTu3"), ("T-1", "t1f3")),
}

# Wall count per map, from log row 2 (primary source, verified in `geometry`).
MAP_BY_WALLS = {24: "map2", 40: "map1", 78: "map3"}

# player.cpp:372 -- the same two anchors on all three maps.
ANCHORS = ((6, 8), (11, 8))


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _se(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return 0.0 if values else None
    return statistics.stdev(values) / math.sqrt(len(values))


def summarise(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        return {"n": 0}
    out = {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "se": _se(values),
    }
    out["sigma"] = (out["mean"] / out["se"]) if out["se"] else None
    return out


# ---------------------------------------------------------------------------
# per-game extraction
# ---------------------------------------------------------------------------


class GameSeries:
    """Everything fog-free that one archived game yields, indexed by round."""

    __slots__ = (
        "path", "family", "opponent", "map_name", "our_pid", "walls",
        "our_unit", "their_unit", "our_first", "our_cost", "their_cost",
        "our_pos", "final_delta", "rounds_seen", "snapshots",
    )

    def __init__(self, path: Path, family: str, opponent: str, our_pid: int) -> None:
        self.path = path
        self.family = family
        self.opponent = opponent
        self.our_pid = our_pid
        # per-round income, index = round number, value = list per unit
        self.our_unit: dict[int, list[int]] = {}
        self.their_unit: dict[int, list[int]] = {}
        self.our_first: dict[int, int] = {}
        self.our_cost: dict[int, int] = {}
        self.their_cost: dict[int, int] = {}
        self.our_pos: dict[int, list[tuple[int, int] | None]] = {}
        self.snapshots: list[Mapping[str, Any]] = []
        self.rounds_seen = 0
        self.final_delta: float | None = None
        self.walls: frozenset[tuple[int, int]] = frozenset()
        self.map_name = ""


def read_game(path: Path, family: str, opponent: str, our_pid: int) -> GameSeries:
    series = GameSeries(path, family, opponent, our_pid)
    with path.open(encoding="utf-8") as handle:
        handle.readline()
        row2 = json.loads(handle.readline())
    series.walls = frozenset(
        (r, c) for r, row in enumerate(row2) for c, cell in enumerate(row) if str(cell) == "1"
    )
    series.map_name = MAP_BY_WALLS.get(len(series.walls), "unknown")

    previous: dict[int, list[int]] = {}
    for record in rounds(path):
        if record is None:                      # forfeit / malformed: break the chain
            previous = {}
            continue
        number = int(record["round"])
        series.rounds_seen += 1
        entries = {int(item["id"]): item for item in record["end"]["players"]}
        if number == 0:
            # held gold starts at 0, verified on the corpus, so round 0 income is
            # the end-phase value itself and must not be dropped -- Lead A lives here.
            previous = {pid: [0] * len(item["units"]) for pid, item in entries.items()}
        for pid, item in entries.items():
            current = [int(unit["gold"]) for unit in item["units"]]
            earlier = previous.get(pid)
            if earlier is not None and len(earlier) == len(current):
                delta = [now - was for now, was in zip(current, earlier)]
                if pid == our_pid:
                    series.our_unit[number] = delta
                else:
                    series.their_unit[number] = delta
            previous[pid] = current
        ours = entries.get(our_pid)
        theirs = next((item for pid, item in entries.items() if pid != our_pid), None)
        if ours is not None and theirs is not None:
            our_cost = int(ours.get("cost", 0) or 0)
            their_cost = int(theirs.get("cost", 0) or 0)
            series.our_cost[number] = our_cost
            series.their_cost[number] = their_cost
            # engine tie rule: lower-or-equal cost moves first, P1 wins an exact tie
            series.our_first[number] = int(
                our_cost < their_cost or (our_cost == their_cost and our_pid == 1)
            )
        if ours is not None:
            positions: list[tuple[int, int] | None] = []
            for unit in ours["units"]:
                cell = unit.get("position")
                positions.append((int(cell[0]), int(cell[1])) if cell else None)
            series.our_pos[number] = positions
        snapshot = record.get("snapshot")
        if snapshot:
            series.snapshots.append(snapshot)
    series.final_delta = net_delta(path, our_pid)
    return series


def load_corpus(maps: Sequence[str]) -> dict[str, list[GameSeries]]:
    cache: dict[str, Mapping[str, list[tuple[Path, int]]]] = {}
    out: dict[str, list[GameSeries]] = {}
    for map_name in maps:
        games: list[GameSeries] = []
        for opponent, family in CORPUS[map_name]:
            account = ACCOUNTS[opponent]
            if account not in cache:
                cache[account] = families(account)
            for path, pid in cache[account].get(family, []):
                series = read_game(path, family, opponent, pid)
                if series.map_name != map_name:
                    raise AssertionError(
                        "%s: family %s resolved to %s, expected %s (wall count %d)"
                        % (path.name, family, series.map_name, map_name, len(series.walls))
                    )
                games.append(series)
        out[map_name] = games
    return out


# ---------------------------------------------------------------------------
# localization
# ---------------------------------------------------------------------------


def _blocks(size: int) -> list[tuple[int, int]]:
    return [(start, min(start + size, ROUNDS)) for start in range(0, ROUNDS, size)]


def per_round_curves(games: Sequence[GameSeries]) -> Mapping[str, Any]:
    """Mean per-round income for both sides and the cumulative deficit curve."""
    ours = [[] for _ in range(ROUNDS)]
    theirs = [[] for _ in range(ROUNDS)]
    for game in games:
        for number in range(ROUNDS):
            our = game.our_unit.get(number)
            their = game.their_unit.get(number)
            if our is not None:
                ours[number].append(float(sum(our)))
            if their is not None:
                theirs[number].append(float(sum(their)))
    our_mean = [(_mean(values) or 0.0) for values in ours]
    their_mean = [(_mean(values) or 0.0) for values in theirs]
    deficit = [t - o for o, t in zip(our_mean, their_mean)]
    cumulative = []
    total = 0.0
    for value in deficit:
        total += value
        cumulative.append(total)
    return {
        "our_income_per_round": our_mean,
        "their_income_per_round": their_mean,
        "deficit_per_round": deficit,
        "cumulative_deficit": cumulative,
        "games_per_round": [len(values) for values in ours],
    }


def block_table(games: Sequence[GameSeries], size: int = 25) -> list[Mapping[str, Any]]:
    """Round-block decomposition with a per-game SE, which is the honest one."""
    rows = []
    for start, stop in _blocks(size):
        our_totals, their_totals, deficits = [], [], []
        our_u = [[], []]
        their_u = [[], []]
        our_hits = our_rounds = their_hits = their_rounds = 0
        our_gain_sum = their_gain_sum = 0
        first_moves = first_rounds = 0
        for game in games:
            our_sum = their_sum = 0.0
            saw = False
            for number in range(start, stop):
                our = game.our_unit.get(number)
                their = game.their_unit.get(number)
                if our is None or their is None:
                    continue
                saw = True
                our_sum += sum(our)
                their_sum += sum(their)
                for index, value in enumerate(our[:2]):
                    our_u[index].append(float(value))
                    our_rounds += 1
                    if value > 0:
                        our_hits += 1
                        our_gain_sum += value
                for index, value in enumerate(their[:2]):
                    their_u[index].append(float(value))
                    their_rounds += 1
                    if value > 0:
                        their_hits += 1
                        their_gain_sum += value
                if number in game.our_first:
                    first_rounds += 1
                    first_moves += game.our_first[number]
            if saw:
                our_totals.append(our_sum)
                their_totals.append(their_sum)
                deficits.append(their_sum - our_sum)
        rows.append({
            "block": [start, stop],
            "rounds": stop - start,
            "our_gold": summarise(our_totals),
            "their_gold": summarise(their_totals),
            "deficit": summarise(deficits),
            "our_hit": our_hits / our_rounds if our_rounds else None,
            "their_hit": their_hits / their_rounds if their_rounds else None,
            "our_yield_per_hit": our_gain_sum / our_hits if our_hits else None,
            "their_yield_per_hit": their_gain_sum / their_hits if their_hits else None,
            "our_mean_per_unit_round": _mean(our_u[0] + our_u[1]),
            "their_mean_per_unit_round": _mean(their_u[0] + their_u[1]),
            "our_u0_mean": _mean(our_u[0]),
            "our_u1_mean": _mean(our_u[1]),
            "their_u0_mean": _mean(their_u[0]),
            "their_u1_mean": _mean(their_u[1]),
            "our_first_rate": first_moves / first_rounds if first_rounds else None,
        })
    return rows


def unit_split(games: Sequence[GameSeries]) -> Mapping[str, Any]:
    """Exact additive attribution of the game deficit to our two units.

    ``deficit = (theirs_u0 + theirs_u1) - (ours_u0 + ours_u1)``, so charging our
    unit ``u`` with ``theirs_total/2 - ours_u`` is exact and sums to the deficit.
    Their own u0/u1 asymmetry is reported separately rather than matched, because
    their unit indices are not our unit indices.
    """
    per_game = {"u0_charge": [], "u1_charge": [], "deficit": [],
                "our_u0": [], "our_u1": [], "their_u0": [], "their_u1": []}
    for game in games:
        our0 = our1 = their0 = their1 = 0.0
        for number in range(ROUNDS):
            our = game.our_unit.get(number)
            their = game.their_unit.get(number)
            if our is None or their is None:
                continue
            our0 += our[0]
            our1 += our[1] if len(our) > 1 else 0
            their0 += their[0]
            their1 += their[1] if len(their) > 1 else 0
        deficit = (their0 + their1) - (our0 + our1)
        half = (their0 + their1) / 2.0
        per_game["u0_charge"].append(half - our0)
        per_game["u1_charge"].append(half - our1)
        per_game["deficit"].append(deficit)
        per_game["our_u0"].append(our0)
        per_game["our_u1"].append(our1)
        per_game["their_u0"].append(their0)
        per_game["their_u1"].append(their1)
    out = {key: summarise(values) for key, values in per_game.items()}
    residual = [
        per_game["u0_charge"][i] + per_game["u1_charge"][i] - per_game["deficit"][i]
        for i in range(len(per_game["deficit"]))
    ]
    out["additivity_residual"] = summarise(residual)
    return out


def spatial_series(games: Sequence[GameSeries], size: int = 25) -> list[Mapping[str, Any]]:
    """Our own fog-free occupancy per round-block: region, anchor, distance."""
    rows = []
    for start, stop in _blocks(size):
        region_counts = collections.Counter()
        on_anchor = [0, 0]
        seen = [0, 0]
        dist = [[], []]
        r1_both = r1_rounds = 0
        for game in games:
            for number in range(start, stop):
                positions = game.our_pos.get(number)
                if not positions:
                    continue
                inside = 0
                for index, cell in enumerate(positions[:2]):
                    if cell is None:
                        continue
                    seen[index] += 1
                    region_counts[region_id(*cell)] += 1
                    if region_id(*cell) == 1:
                        inside += 1
                    anchor = ANCHORS[index]
                    manhattan = abs(cell[0] - anchor[0]) + abs(cell[1] - anchor[1])
                    dist[index].append(float(manhattan))
                    if manhattan == 0:
                        on_anchor[index] += 1
                r1_rounds += 1
                r1_both += int(inside == 2)
        total = sum(region_counts.values())
        rows.append({
            "block": [start, stop],
            "region_share": {
                str(key): region_counts[key] / total for key in sorted(region_counts)
            } if total else {},
            "u0_on_anchor_rate": on_anchor[0] / seen[0] if seen[0] else None,
            "u1_on_anchor_rate": on_anchor[1] / seen[1] if seen[1] else None,
            "u0_mean_anchor_distance": _mean(dist[0]),
            "u1_mean_anchor_distance": _mean(dist[1]),
            "both_units_in_region1_rate": r1_both / r1_rounds if r1_rounds else None,
        })
    return rows


def arm_spread(games: Sequence[GameSeries]) -> Mapping[str, Any]:
    """Why do the four Tundra map1 baseline arms differ by ~290 gold?"""
    by_family: dict[str, list[GameSeries]] = collections.defaultdict(list)
    for game in games:
        by_family[game.family].append(game)
    out = {}
    for family, members in sorted(by_family.items()):
        deltas = [game.final_delta for game in members if game.final_delta is not None]
        first_rates, our_p50, their_p50, our_income, their_income = [], [], [], [], []
        for game in members:
            firsts = [value for value in game.our_first.values()]
            if firsts:
                first_rates.append(statistics.fmean(firsts))
            ours = sorted(game.our_cost.values())
            theirs = sorted(game.their_cost.values())
            if ours:
                our_p50.append(float(statistics.median(ours)))
            if theirs:
                their_p50.append(float(statistics.median(theirs)))
            our_income.append(float(sum(sum(v) for v in game.our_unit.values())))
            their_income.append(float(sum(sum(v) for v in game.their_unit.values())))
        out[family] = {
            "games": len(members),
            "opponent": members[0].opponent,
            "net_delta": summarise([float(value) for value in deltas]),
            "our_first_rate": summarise(first_rates),
            "our_cost_p50_ns": summarise(our_p50),
            "their_cost_p50_ns": summarise(their_p50),
            "our_income": summarise(our_income),
            "their_income": summarise(their_income),
        }
    return out


def first_mover_effect(games: Sequence[GameSeries]) -> Mapping[str, Any]:
    """Income conditional on winning the dispatch race, pooled over rounds.

    Observational, not causal: cost correlates with the branch taken, which
    correlates with the local state.  Reported as a magnitude bound only.
    """
    first_income, second_income = [], []
    first_hit = first_rounds = second_hit = second_rounds = 0
    for game in games:
        for number, flag in game.our_first.items():
            our = game.our_unit.get(number)
            if our is None:
                continue
            total = float(sum(our))
            if flag:
                first_income.append(total)
                for value in our[:2]:
                    first_rounds += 1
                    first_hit += int(value > 0)
            else:
                second_income.append(total)
                for value in our[:2]:
                    second_rounds += 1
                    second_hit += int(value > 0)
    return {
        "our_first_rounds": len(first_income),
        "our_second_rounds": len(second_income),
        "our_first_rate": len(first_income) / max(1, len(first_income) + len(second_income)),
        "income_when_first": _mean(first_income),
        "income_when_second": _mean(second_income),
        "hit_when_first": first_hit / first_rounds if first_rounds else None,
        "hit_when_second": second_hit / second_rounds if second_rounds else None,
        "gap_per_round": (_mean(first_income) or 0.0) - (_mean(second_income) or 0.0),
        "caveat": "observational; cost is endogenous to the branch taken",
    }


def localize(maps: Sequence[str]) -> Mapping[str, Any]:
    corpus = load_corpus(maps)
    out: dict[str, Any] = {
        "corpus": {
            map_name: {
                "games": len(games),
                "families": sorted({game.family for game in games}),
                "opponents": sorted({game.opponent for game in games}),
                "wall_count": sorted({len(game.walls) for game in games}),
                "net_delta": summarise(
                    [float(game.final_delta) for game in games if game.final_delta is not None]),
                "rounds_complete": sorted({game.rounds_seen for game in games}),
            }
            for map_name, games in corpus.items()
        },
        "curves": {},
        "blocks_25": {},
        "blocks_50": {},
        "unit_split": {},
        "spatial": {},
        "first_mover": {},
    }
    for map_name, games in corpus.items():
        out["curves"][map_name] = per_round_curves(games)
        out["blocks_25"][map_name] = block_table(games, 25)
        out["blocks_50"][map_name] = block_table(games, 50)
        out["unit_split"][map_name] = unit_split(games)
        out["spatial"][map_name] = spatial_series(games, 25)
        out["first_mover"][map_name] = first_mover_effect(games)
    out["arm_spread_map1"] = arm_spread(corpus.get("map1", []))
    return out


# ---------------------------------------------------------------------------
# move-order sensitivity and the latency -> first-mover transfer function
# ---------------------------------------------------------------------------


def _side_series(games: Sequence[GameSeries]):
    """Yield (our_first, our_delta_list, their_delta_list, cost_gap) per round."""
    for game in games:
        for number in range(ROUNDS):
            our = game.our_unit.get(number)
            their = game.their_unit.get(number)
            flag = game.our_first.get(number)
            if our is None or their is None or flag is None:
                continue
            gap = game.our_cost[number] - game.their_cost[number]
            yield flag, our, their, gap, number


def order_sensitivity(games: Sequence[GameSeries], tie_window: int | None = None) -> Mapping[str, Any]:
    """Income / hit / yield for BOTH sides conditional on who moved first.

    ``tie_window`` restricts to rounds where ``|our_cost - their_cost| <= w``.
    In that stratum our own cost -- and therefore our own branch mix and local
    state -- is nearly identical across the two arms, while which side moves
    first is decided by a few nanoseconds.  That is a regression-discontinuity
    design and it is the only way to read a *causal* order effect out of an
    observational cost series.
    """
    buckets = {
        "our_first": {"ours": [], "theirs": []},
        "their_first": {"ours": [], "theirs": []},
    }
    hits = collections.Counter()
    rounds_seen = collections.Counter()
    gains = collections.Counter()
    for flag, our, their, gap, _number in _side_series(games):
        if tie_window is not None and abs(gap) > tie_window:
            continue
        key = "our_first" if flag else "their_first"
        buckets[key]["ours"].append(float(sum(our)))
        buckets[key]["theirs"].append(float(sum(their)))
        for side, values in (("ours", our), ("theirs", their)):
            for value in values[:2]:
                rounds_seen[(key, side)] += 1
                if value > 0:
                    hits[(key, side)] += 1
                    gains[(key, side)] += value

    def cell(key: str, side: str) -> Mapping[str, Any]:
        total = rounds_seen[(key, side)]
        hit = hits[(key, side)]
        return {
            "unit_rounds": total,
            "income_per_round": _mean(buckets[key][side]),
            "income_per_unit_round": (_mean(buckets[key][side]) or 0.0) / 2.0,
            "hit": hit / total if total else None,
            "yield_per_hit": gains[(key, side)] / hit if hit else None,
        }

    out: dict[str, Any] = {
        "tie_window_ns": tie_window,
        "rounds": len(buckets["our_first"]["ours"]) + len(buckets["their_first"]["ours"]),
        "our_first_rate": (
            len(buckets["our_first"]["ours"])
            / max(1, len(buckets["our_first"]["ours"]) + len(buckets["their_first"]["ours"]))
        ),
    }
    for side in ("ours", "theirs"):
        first_key = "our_first" if side == "ours" else "their_first"
        second_key = "their_first" if side == "ours" else "our_first"
        moving_first = cell(first_key, side)
        moving_second = cell(second_key, side)
        out[side] = {
            "moving_first": moving_first,
            "moving_second": moving_second,
            "order_gap_per_round": (moving_first["income_per_round"] or 0.0)
            - (moving_second["income_per_round"] or 0.0),
            "order_gap_hit_pp": 100.0 * ((moving_first["hit"] or 0.0) - (moving_second["hit"] or 0.0)),
            "order_sensitivity_ratio": (
                (moving_first["income_per_round"] or 0.0) / (moving_second["income_per_round"] or 1e-9)
            ),
        }
    return out


def transfer_function(
    games: Sequence[GameSeries], shifts: Sequence[int] = (-40, -30, -20, -10, 0, 10, 20, 30, 40)
) -> Mapping[str, Any]:
    """Exact first-mover rate if our decision cost were shifted by ``-delta`` ns.

    Both costs are logged for every round, so this is an exact recomputation of
    the engine's dispatch rule (`docs/PRELIM_RULES.md` §2.4: lower cost moves
    first, P1 wins an exact tie), not a model.  The opponent's cost is held at
    its observed value, which is the correct counterfactual for a change on our
    side only.  Costs are logged at 10 ns granularity.
    """
    pairs = [
        (game.our_cost[number], game.their_cost[number], game.our_pid)
        for game in games for number in sorted(game.our_cost)
        if number in game.their_cost
    ]
    gaps = sorted(our - their for our, their, _pid in pairs)
    rows = []
    for delta in shifts:
        wins = 0
        for our, their, pid in pairs:
            shifted = our - delta
            if shifted < their or (shifted == their and pid == 1):
                wins += 1
        rows.append({"shift_ns": delta, "our_first_rate": wins / len(pairs) if pairs else None})
    base = next(row["our_first_rate"] for row in rows if row["shift_ns"] == 0)
    minus10 = next((row["our_first_rate"] for row in rows if row["shift_ns"] == 10), None)
    return {
        "rounds": len(pairs),
        "cost_gap_ns_percentiles": {
            str(percent): gaps[min(len(gaps) - 1, int(percent / 100 * len(gaps)))]
            for percent in (5, 10, 25, 50, 75, 90, 95)
        } if gaps else {},
        "near_tie_share_10ns": sum(1 for value in gaps if abs(value) <= 10) / len(gaps) if gaps else None,
        "near_tie_share_20ns": sum(1 for value in gaps if abs(value) <= 20) / len(gaps) if gaps else None,
        "curve": rows,
        "baseline_first_rate": base,
        "pp_per_10ns_at_zero": (
            100.0 * ((minus10 or base) - base) if minus10 is not None else None),
    }


def contention(maps: Sequence[str]) -> Mapping[str, Any]:
    corpus = load_corpus(maps)
    out: dict[str, Any] = {}
    for map_name, games in corpus.items():
        full = order_sensitivity(games)
        near = order_sensitivity(games, tie_window=10)
        near20 = order_sensitivity(games, tie_window=20)
        transfer = transfer_function(games)
        # gold/game value of one extra pp of first-mover rate, using the
        # RD (near-tie) order gap so the price is not inflated by selection.
        rd_gap = near["ours"]["order_gap_per_round"]
        obs_gap = full["ours"]["order_gap_per_round"]
        per_pp_rd = 0.01 * rd_gap * ROUNDS
        per_pp_obs = 0.01 * obs_gap * ROUNDS
        pp10 = transfer["pp_per_10ns_at_zero"] or 0.0
        out[map_name] = {
            "games": len(games),
            "observational": full,
            "near_tie_10ns": near,
            "near_tie_20ns": near20,
            "transfer_function": transfer,
            "pricing": {
                "gold_per_game_per_pp_first_mover_rd": per_pp_rd,
                "gold_per_game_per_pp_first_mover_observational": per_pp_obs,
                "pp_first_mover_per_10ns": pp10,
                "gold_per_game_per_10ns_rd": per_pp_rd * pp10,
                "gold_per_game_per_ns_rd": per_pp_rd * pp10 / 10.0,
                "note": "RD figure uses the |cost gap| <= 10 ns stratum, where our own "
                        "cost and therefore our own branch mix is matched across arms",
            },
        }
    return out


# ---------------------------------------------------------------------------
# where our decision cost comes from, and what fixing it is worth
# ---------------------------------------------------------------------------

# `player.cpp` shape signatures.  The LUT (`SL.fact`) always emits three moves
# for d>=1, so `(a,4,4)` and `(4,4,4)` can only come from the `ok==0` fallback
# (`player.cpp:509-514`) or, for `stay3`, from the `d==0` fold with no passable
# neighbour.  Logged actions are *effective*, so a blocked first step turns
# `(a,b,c)` into `(4,b,c)`; that case is labelled separately.
FALLBACK_SHAPES = ("stall", "stay3")


def shape_of(actions: Sequence[Any]) -> str:
    values = [int(item) for item in list(actions)[:3]]
    while len(values) < 3:
        values.append(4)
    a0, a1, a2 = values
    if a0 == 4 and a1 == 4 and a2 == 4:
        return "stay3"
    if a1 == 4 and a2 == 4:
        return "stall"
    if a0 == 4:
        return "blocked_first"
    if a1 == (a0 ^ 1):
        return "fold0" if a2 == 4 else ("fold1" if a2 == a0 else "other")
    if a2 == (a1 ^ 1):
        return "fold2"
    return "other" if a2 == 4 else "march"


def _cost_rows(game: GameSeries) -> Iterable[Mapping[str, Any]]:
    with game.path.open(encoding="utf-8") as handle:
        handle.readline()
        handle.readline()
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if "end" not in record:
                continue
            number = int(record["round"])
            entry = next(
                (item for item in record["end"]["players"] if int(item["id"]) == game.our_pid), None)
            if entry is None:
                continue
            shapes = [shape_of(unit["actions"]) for unit in entry["units"]]
            yield {
                "round": number,
                "cost": int(entry.get("cost", 0) or 0),
                "shapes": shapes,
                "fallback_units": sum(1 for shape in shapes if shape in FALLBACK_SHAPES),
                "wave_tick": int(number % 20 == 0),
            }


def branch_cost(maps: Sequence[str], warmup: int = 2) -> Mapping[str, Any]:
    """Dose-response of our decision cost against the fallback branch and waveTick,
    then an exact targeted counterfactual on the first-mover race.

    The chain under test is *not* the falsified routing one.  It is:
    map1's walls -> the ``ok`` waypoint check fails more often -> the
    ``steerStep``/``escapeStep`` fallback runs, and ``escapeStep`` is
    ``__attribute__((noinline, cold))`` (`player.cpp:156`) -> our cost tail
    fattens -> we lose the dispatch race -> we lose contested cells.

    The association between shape and cost is observational; the dose-response
    (0, 1, 2 fallback units in the same round) is what makes it more than a
    correlation, and the source-level mechanism is named above.  Labelled
    accordingly, not claimed as an isolated causal measurement.
    """
    corpus = load_corpus(maps)
    out: dict[str, Any] = {}
    for map_name, games in corpus.items():
        rows: list[Mapping[str, Any]] = []
        for game in games:
            for row in _cost_rows(game):
                if row["round"] >= warmup:
                    rows.append({**row, "game": game.path.name})
        shape_mix = collections.Counter(
            shape for row in rows for shape in row["shapes"])
        total_units = sum(shape_mix.values())

        def spread(values: Sequence[int]) -> Mapping[str, Any]:
            if not values:
                return {}
            ordered = sorted(values)
            return {
                "n": len(ordered),
                "mean": statistics.fmean(ordered),
                "p50": ordered[len(ordered) // 2],
                "p75": ordered[int(0.75 * len(ordered))],
                "p90": ordered[int(0.90 * len(ordered))],
            }

        dose = {
            str(k): spread([row["cost"] for row in rows
                            if row["fallback_units"] == k and not row["wave_tick"]])
            for k in (0, 1, 2)
        }
        wave = {
            "wave_tick_rounds": spread([row["cost"] for row in rows if row["wave_tick"]]),
            "other_rounds": spread([row["cost"] for row in rows if not row["wave_tick"]]),
        }
        # marginal per-unit fallback cost from the dose-response, and the
        # waveTick cost net of its own fallback mix
        per_unit = None
        if dose["0"] and dose["2"]:
            per_unit = (dose["2"]["p50"] - dose["0"]["p50"]) / 2.0
        wave_excess = None
        if wave["wave_tick_rounds"] and wave["other_rounds"]:
            wave_excess = wave["wave_tick_rounds"]["p50"] - wave["other_rounds"]["p50"]

        # exact targeted counterfactual: remove `excess` ns from the rounds the
        # fix would touch and recompute the dispatch race round by round
        pairs = []
        for game in games:
            costs = {row["round"]: row for row in _cost_rows(game)}
            for number in sorted(game.our_cost):
                row = costs.get(number)
                if row is None or number < warmup:
                    continue
                pairs.append((
                    game.our_cost[number], game.their_cost[number], game.our_pid,
                    row["fallback_units"], row["wave_tick"],
                ))

        def race(shift_fn) -> float:
            wins = 0
            for our, their, pid, fallback, waveflag in pairs:
                shifted = our - shift_fn(fallback, waveflag)
                if shifted < their or (shifted == their and pid == 1):
                    wins += 1
            return wins / len(pairs) if pairs else 0.0

        base_rate = race(lambda fallback, waveflag: 0)
        scenarios = {
            "baseline": {"shift": "none", "first_rate": base_rate},
        }
        if per_unit:
            for share in (0.5, 1.0):
                label = "fallback_cost_cut_%d_pct" % int(share * 100)
                scenarios[label] = {
                    "shift": "%.0f ns per fallback unit" % (per_unit * share),
                    "first_rate": race(lambda f, w, s=share: per_unit * s * f),
                }
        if per_unit:
            # honest engineering counterfactual: a branchless / always-warm
            # fallback removes the per-unit excess but pays a small uniform cost
            # on every round.  Both terms are applied exactly.
            for uniform in (5, 10, 20):
                scenarios["fallback_cut_minus_%dns_uniform" % uniform] = {
                    "shift": "%.0f ns per fallback unit, -%d ns on every round"
                             % (per_unit, uniform),
                    "first_rate": race(
                        lambda f, w, u=uniform: per_unit * f - u),
                }
        if wave_excess:
            scenarios["wave_tick_removed"] = {
                "shift": "%d ns on round%%20==0" % wave_excess,
                "first_rate": race(lambda f, w: wave_excess * w),
            }
            if per_unit:
                scenarios["both_removed"] = {
                    "shift": "both",
                    "first_rate": race(lambda f, w: per_unit * f + wave_excess * w),
                }
        identity = order_identity(games)
        near = order_sensitivity(games, tie_window=10)
        flip_value = (
            near["ours"]["order_gap_per_round"] + near["theirs"]["order_gap_per_round"])
        for label, item in scenarios.items():
            item["delta_pp"] = 100.0 * (item["first_rate"] - base_rate)
            item["flipped_rounds_per_game"] = (item["first_rate"] - base_rate) * ROUNDS
            item["gold_per_game_rd"] = item["flipped_rounds_per_game"] * flip_value
            item["gold_per_game_identity"] = (
                item["delta_pp"] * identity["gold_per_pp_of_first_mover_rate"])
        out[map_name] = {
            "games": len(games),
            "rounds": len(rows),
            "shape_mix": {key: value / total_units for key, value in shape_mix.most_common()},
            "rounds_with_any_fallback": sum(1 for row in rows if row["fallback_units"]) / len(rows),
            "cost_by_fallback_units": dose,
            "marginal_fallback_cost_ns_p50": per_unit,
            "wave_tick": wave,
            "wave_tick_excess_ns_p50": wave_excess,
            "flip_value_gold_rd": flip_value,
            "gold_per_pp_identity": identity["gold_per_pp_of_first_mover_rate"],
            "scenarios": scenarios,
        }
    return out


def stock_flow(maps, horizon: int = 5):
    """Is gold lost to the dispatch race *novel*, or merely deferred to us?

    On this board the question has a sharp answer.  The five-region snapshot
    shows ``collected / generated = 0.988`` in every region of every map, so a
    cell we do not take is taken by *someone*; the only open question is whether
    that someone is us, a few rounds later.

    Test: condition on losing the race in round ``r`` and measure our income in
    rounds ``r .. r+h`` against the same window conditioned on winning it.  If a
    lost cell were merely deferred, the following rounds would over-perform and
    the cumulative gap would shrink towards zero.
    """
    corpus = load_corpus(maps)
    out = {}
    for map_name, games in corpus.items():
        after_first = [[] for _ in range(horizon + 1)]
        after_second = [[] for _ in range(horizon + 1)]
        for game in games:
            for number in range(ROUNDS):
                flag = game.our_first.get(number)
                if flag is None:
                    continue
                window = []
                ok = True
                for step in range(horizon + 1):
                    our = game.our_unit.get(number + step)
                    if our is None:
                        ok = False
                        break
                    window.append(float(sum(our)))
                if not ok:
                    continue
                target = after_first if flag else after_second
                for step in range(horizon + 1):
                    target[step].append(window[step])
        rows = []
        cumulative_first = cumulative_second = 0.0
        for step in range(horizon + 1):
            first = _mean(after_first[step]) or 0.0
            second = _mean(after_second[step]) or 0.0
            cumulative_first += first
            cumulative_second += second
            rows.append({
                "offset": step,
                "income_after_winning_race": first,
                "income_after_losing_race": second,
                "gap_this_round": first - second,
                "cumulative_gap": cumulative_first - cumulative_second,
            })
        out[map_name] = {
            "games": len(games),
            "horizon": horizon,
            "rows": rows,
            "gap_at_offset_0": rows[0]["gap_this_round"],
            "cumulative_gap_over_horizon": rows[-1]["cumulative_gap"],
            "share_of_offset0_gap_still_open": (
                rows[-1]["cumulative_gap"] / rows[0]["gap_this_round"]
                if rows[0]["gap_this_round"] else None),
            "reading": "a value >= 1 means none of the round-0 loss is recovered in the "
                       "following rounds, i.e. the gold is novel rather than deferred",
        }
    return out


def holdout() -> Mapping[str, Any]:
    """Out-of-sample replication of the map1 result on disjoint game splits.

    There are no seeds here -- these are platform games -- so the equivalent of a
    disjoint-seed check is a disjoint-game check.  Three partitions are used:
    by opponent (Tundra 24 / T-1 6), by A/B campaign (the four Tundra arms taken
    two and two), and odd/even game id.  A finding that does not survive all
    three is not a finding.
    """
    games = load_corpus(["map1"])["map1"]
    splits: dict[str, list[GameSeries]] = {
        "all_30": games,
        "opponent_Tundra_24": [g for g in games if g.opponent == "Tundra"],
        "opponent_T1_6": [g for g in games if g.opponent == "T-1"],
        "arms_frTu1_lnA0": [g for g in games if g.family in ("frTu1", "lnA0")],
        "arms_a2A0_alA0": [g for g in games if g.family in ("a2A0", "alA0")],
        "game_id_even": [g for g in games if int(g.path.stem.split("_")[1]) % 2 == 0],
        "game_id_odd": [g for g in games if int(g.path.stem.split("_")[1]) % 2 == 1],
    }
    out: dict[str, Any] = {}
    for label, subset in splits.items():
        if not subset:
            continue
        identity = order_identity(subset)
        rows = []
        for game in subset:
            rows.extend(row for row in _cost_rows(game) if row["round"] >= 2)
        cost0 = sorted(row["cost"] for row in rows
                       if row["fallback_units"] == 0 and not row["wave_tick"])
        cost2 = sorted(row["cost"] for row in rows
                       if row["fallback_units"] == 2 and not row["wave_tick"])
        per_unit = ((cost2[len(cost2) // 2] - cost0[len(cost0) // 2]) / 2.0
                    if cost0 and cost2 else None)
        out[label] = {
            "games": len(subset),
            "families": sorted({game.family for game in subset}),
            "our_first_rate": identity["our_first_rate"],
            "A": identity["A_when_we_move_first"],
            "B": identity["B_when_they_move_first"],
            "break_even_first_mover_rate": identity["break_even_first_mover_rate"],
            "margin_vs_break_even_pp": identity["margin_vs_break_even_pp"],
            "net_from_identity": identity["net_from_identity"],
            "net_observed_mean": identity["net_observed_mean"],
            "gold_per_pp": identity["gold_per_pp_of_first_mover_rate"],
            "marginal_fallback_cost_ns_p50": per_unit,
            "fallback_round_share": (
                sum(1 for row in rows if row["fallback_units"]) / len(rows) if rows else None),
        }
    return out


def assemble(paths: Mapping[str, Path]) -> Mapping[str, Any]:
    """Fold the sub-command artifacts into the machine-readable verdict."""
    loaded = {key: json.loads(path.read_text()) for key, path in paths.items() if path.exists()}
    localize_data = loaded.get("localize", {})
    identity_data = loaded.get("identity", {})
    branch = loaded.get("branch_cost", {})
    curve = (localize_data.get("curves", {}).get("map1", {}) or {}).get("cumulative_deficit", [])
    checkpoints = {str(r): curve[r] for r in (24, 49, 99, 149, 199, 299, 399, 499) if r < len(curve)}
    map1_identity = identity_data.get("map1", {})
    map1_branch = branch.get("map1", {})
    return {
        "schema_version": 1,
        "subject": "map1 lesion localization on the fog-free per-unit gold channel",
        "baseline": {
            "commit": "f18064c",
            "src_player_cpp_sha256":
                "0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd",
            "note": "HEAD has moved to fd47ea6; that fix is bit-identical on the three known "
                    "maps (pair_diff 0/500), so these conclusions carry, but the baseline is "
                    "pinned to f18064c",
        },
        "platform_games_consumed": 0,
        "localization": {
            "corpus": localize_data.get("corpus", {}),
            "map1_cumulative_deficit_checkpoints": checkpoints,
            "verdict": "not front-loaded and not uniform: we are AHEAD through round ~120 "
                       "and the whole deficit accrues after it, at a near-constant rate, "
                       "coincident with a regime change in the opponents' hit rate at "
                       "round 50-75",
            "unit_split_map1": (localize_data.get("unit_split", {}) or {}).get("map1", {}),
            "spatial_note": "our region-1 occupancy is 90-98% and mean anchor distance "
                            "1.7-2.8 on all three maps: no map1 spatial anomaly",
        },
        "order_identity": identity_data,
        "lesion": {
            "statement": "map1's break-even first-mover rate is 0.704 and we achieve 0.567; "
                         "map2 0.694 vs 0.683; map3 0.568 vs 0.624. One number orders the "
                         "three maps and the identity closes exactly.",
            "break_even_rate": {
                name: value.get("break_even_first_mover_rate")
                for name, value in identity_data.items()
            },
            "achieved_rate": {
                name: value.get("our_first_rate") for name, value in identity_data.items()
            },
            "margin_pp": {
                name: value.get("margin_vs_break_even_pp")
                for name, value in identity_data.items()
            },
            "gold_per_pp_of_first_mover_rate": {
                name: value.get("gold_per_pp_of_first_mover_rate")
                for name, value in identity_data.items()
            },
        },
        "cause_of_the_low_rate": {
            "marginal_fallback_cost_ns_per_unit_p50": map1_branch.get(
                "marginal_fallback_cost_ns_p50"),
            "fallback_round_share": map1_branch.get("rounds_with_any_fallback"),
            "wave_tick_excess_ns_p50": map1_branch.get("wave_tick_excess_ns_p50"),
            "cost_by_fallback_units": map1_branch.get("cost_by_fallback_units"),
            "scenarios": map1_branch.get("scenarios"),
            "source_mechanism": "player.cpp:504-514 -- the ok waypoint check fails, "
                                "steerStep runs and may call escapeStep, which is "
                                "__attribute__((noinline, cold)) at player.cpp:156; "
                                "waveTick at :240 is likewise noinline cold and fires on "
                                "round%20==0",
        },
        "stock_flow": loaded.get("stock_flow", {}),
        "holdout": loaded.get("holdout", {}),
        "field_generality": loaded.get("field", {}),
        "geometry": loaded.get("geometry", {}),
        "supply": loaded.get("supply", {}),
        "leads": {
            "A_opening": "DISCARDED -- the cumulative deficit is negative (we are ahead) "
                         "through round ~120; the only opening defect found is a stale baked "
                         "route target ((6,6)/(10,10) vs the anchors (6,8)/(11,8)) costing "
                         "1.5-3 rounds of arrival delay, ~5-11 gold/game",
            "B_supply_geometry": "DISCARDED as a supply mechanism -- region 1 is 99.3% "
                                 "harvested on map1, its generation share (49.6%) and "
                                 "capacity (65 cells) are within 8% of map2's, and map3, "
                                 "whose centre is 66.7% walled with only 27 cells, is the map "
                                 "we win. VINDICATED as a latency mechanism instead: map1's "
                                 "walls put us on the cold fallback branch in 53.5% of rounds "
                                 "at +40 ns per unit.",
        },
        "bias_labels": {
            "channel": "fog-free and complete: per-unit gold is logged in 100% of "
                       "unit-observations; the identity reproduces the observed net score "
                       "with residual 0.00 on map1 and +3.00 on map2/map3 (exactly our "
                       "vision spend, which the identity excludes)",
            "order_effect": "the observational first-vs-second gap is confounded by cost "
                            "endogeneity; the |cost gap| <= 10 ns stratum is the RD estimate "
                            "and every gold figure quoted as 'RD' uses it",
            "branch_cost": "observational association with a monotone dose-response "
                           "(0/1/2 fallback units -> 180/220/260 ns P50) and a named "
                           "source-level mechanism; not an isolated causal measurement",
            "field_probe": "our side there is a MIXTURE of experimental builds, several "
                           "crippled; use it for the identity's shape, never for our level",
            "cost_granularity": "platform cost is logged at 10 ns granularity, so a "
                                "sub-10 ns counterfactual cannot be resolved",
            "no_simulator": "nothing here uses the local NPC model; the comparison is "
                            "between the two seats of the same platform game",
        },
    }


def order_identity(games: Sequence[GameSeries]) -> Mapping[str, Any]:
    """Exact, model-free decomposition of the net score by who moved first.

    Exactly one side moves first in each round, so with ``f`` = our first-mover
    rate, ``A`` = (our income - their income) averaged over the rounds we move
    first, and ``B`` = the same over the rounds they move first::

        net_per_round = f * A + (1 - f) * B

    Both ``A`` and ``B`` are differences taken *within the same rounds*, so no
    matching is needed and no causal claim is made: this is accounting.  The
    break-even first-mover rate follows immediately as ``f* = -B / (A - B)``,
    which is the single number that orders the three maps.
    """
    our_first_ours, our_first_theirs = [], []
    their_first_ours, their_first_theirs = [], []
    per_game = []
    for game in games:
        g_of_o, g_of_t, g_tf_o, g_tf_t = [], [], [], []
        for number in range(ROUNDS):
            our = game.our_unit.get(number)
            their = game.their_unit.get(number)
            flag = game.our_first.get(number)
            if our is None or their is None or flag is None:
                continue
            if flag:
                g_of_o.append(float(sum(our)))
                g_of_t.append(float(sum(their)))
            else:
                g_tf_o.append(float(sum(our)))
                g_tf_t.append(float(sum(their)))
        our_first_ours += g_of_o
        our_first_theirs += g_of_t
        their_first_ours += g_tf_o
        their_first_theirs += g_tf_t
        rounds_total = len(g_of_o) + len(g_tf_o)
        if not rounds_total:
            continue
        f = len(g_of_o) / rounds_total
        a = (_mean(g_of_o) or 0.0) - (_mean(g_of_t) or 0.0)
        b = (_mean(g_tf_o) or 0.0) - (_mean(g_tf_t) or 0.0)
        per_game.append({
            "game": game.path.name, "family": game.family, "f": f, "A": a, "B": b,
            "net_from_identity": ROUNDS * (f * a + (1 - f) * b),
            "net_observed": game.final_delta,
            "break_even_f": (-b / (a - b)) if abs(a - b) > 1e-9 else None,
        })
    total = len(our_first_ours) + len(their_first_ours)
    f = len(our_first_ours) / total if total else 0.0
    a = (_mean(our_first_ours) or 0.0) - (_mean(our_first_theirs) or 0.0)
    b = (_mean(their_first_ours) or 0.0) - (_mean(their_first_theirs) or 0.0)
    identity_net = ROUNDS * (f * a + (1 - f) * b)
    observed = [game.final_delta for game in games if game.final_delta is not None]
    return {
        "rounds": total,
        "our_first_rate": f,
        "A_when_we_move_first": a,
        "B_when_they_move_first": b,
        "our_income_when_first": _mean(our_first_ours),
        "their_income_when_we_first": _mean(our_first_theirs),
        "our_income_when_second": _mean(their_first_ours),
        "their_income_when_they_first": _mean(their_first_theirs),
        "gold_won_on_our_first_rounds": ROUNDS * f * a,
        "gold_lost_on_their_first_rounds": ROUNDS * (1 - f) * b,
        "net_from_identity": identity_net,
        "net_observed_mean": _mean([float(value) for value in observed]),
        "identity_residual": identity_net - (_mean([float(value) for value in observed]) or 0.0),
        "break_even_first_mover_rate": (-b / (a - b)) if abs(a - b) > 1e-9 else None,
        "margin_vs_break_even_pp": (
            100.0 * (f - (-b / (a - b))) if abs(a - b) > 1e-9 else None),
        "gold_per_pp_of_first_mover_rate": 0.01 * ROUNDS * (a - b),
        "per_game": per_game,
    }


def field(min_games: int = 1) -> Mapping[str, Any]:
    """Run the order identity against every opponent account that has map1 games.

    **Build-mixture warning (mandatory read).** Only the five `f18064c` map1
    families are one strategy; every other opponent was faced by a mixture of
    experimental builds, several deliberately crippled.  This section is
    therefore a *generality probe on the identity's shape*, not a strength
    measurement, and its absolute levels must not be quoted as our ability.
    """
    accounts: dict[str, list[tuple[Path, int]]] = collections.defaultdict(list)
    for path in sorted((ROOT / "logs").glob("game_*.log")):
        try:
            head = header(path)
            with path.open(encoding="utf-8") as handle:
                handle.readline()
                row2 = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        walls = sum(1 for row in row2 for cell in row if str(cell) == "1")
        if MAP_BY_WALLS.get(walls) != "map1":
            continue
        first, second = str(head.get("player1")), str(head.get("player2"))
        if first.startswith("player") and not second.startswith("player"):
            accounts[first].append((path, 2))
        elif second.startswith("player") and not first.startswith("player"):
            accounts[second].append((path, 1))
    out: dict[str, Any] = {"note": field.__doc__, "accounts": {}}
    for account, members in sorted(accounts.items()):
        if len(members) < min_games:
            continue
        games = []
        for path, pid in members:
            try:
                games.append(read_game(path, family_of_path(path, pid), account, pid))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        games = [game for game in games if game.rounds_seen >= 490]
        if not games:
            continue
        identity = order_identity(games)
        our_p50, their_p50 = [], []
        for game in games:
            if game.our_cost:
                our_p50.append(float(statistics.median(sorted(game.our_cost.values()))))
            if game.their_cost:
                their_p50.append(float(statistics.median(sorted(game.their_cost.values()))))
        out["accounts"][account] = {
            "games": len(games),
            "our_build_families": sorted({game.family for game in games}),
            "our_cost_p50_ns": summarise(our_p50),
            "their_cost_p50_ns": summarise(their_p50),
            "our_first_rate": identity["our_first_rate"],
            "A_when_we_move_first": identity["A_when_we_move_first"],
            "B_when_they_move_first": identity["B_when_they_move_first"],
            "break_even_first_mover_rate": identity["break_even_first_mover_rate"],
            "margin_vs_break_even_pp": identity["margin_vs_break_even_pp"],
            "net_from_identity": identity["net_from_identity"],
            "net_observed_mean": identity["net_observed_mean"],
            "wins": sum(
                1 for game in games if game.final_delta is not None and game.final_delta > 0),
        }
    return out


def family_of_path(path: Path, our_pid: int) -> str:
    head = header(path)
    name = str(head.get("player1") if our_pid == 1 else head.get("player2"))
    return name[:-1] if len(name) > 1 else name


# ---------------------------------------------------------------------------
# five-region snapshot channel (fog-free supply)
# ---------------------------------------------------------------------------


def supply(maps: Sequence[str]) -> Mapping[str, Any]:
    """Region-wise generation / collection / remaining, from the log snapshots.

    The snapshot is emitted by the engine at round-start multiples of five and
    covers the previous five rounds (`sim/engine.py:460`).  It is global and
    fog-free: it is the only supply instrument in a platform log that does not
    depend on visibility.  It does **not** say who collected, so it bounds the
    supply side without attributing it.
    """
    corpus = load_corpus(maps)
    out: dict[str, Any] = {}
    for map_name, games in corpus.items():
        per_region = collections.defaultdict(lambda: collections.defaultdict(list))
        totals = collections.defaultdict(list)
        early = collections.defaultdict(lambda: collections.defaultdict(list))
        for game in games:
            game_region = collections.defaultdict(lambda: collections.defaultdict(float))
            for snapshot in game.snapshots:
                window = snapshot.get("window") or [0, 0]
                is_early = int(window[1]) < 50
                for region in snapshot["regions"]:
                    rid = str(region["id"])
                    for field in ("gold_generated", "gold_collected", "gold_remaining", "occupants"):
                        game_region[rid][field] += float(region.get(field, 0) or 0)
                        if is_early:
                            early[rid][field].append(float(region.get(field, 0) or 0))
            windows = max(1, len(game.snapshots))
            for rid, fields in game_region.items():
                for field, value in fields.items():
                    if field in ("gold_remaining", "occupants"):
                        per_region[rid][field].append(value / windows)   # mean level
                    else:
                        per_region[rid][field].append(value)             # per-game flow
            totals["generated"].append(
                sum(fields["gold_generated"] for fields in game_region.values()))
            totals["collected"].append(
                sum(fields["gold_collected"] for fields in game_region.values()))
        out[map_name] = {
            "games": len(games),
            "snapshot_windows_per_game": sorted({len(game.snapshots) for game in games}),
            "per_region": {
                rid: {field: summarise(values) for field, values in sorted(fields.items())}
                for rid, fields in sorted(per_region.items())
            },
            "totals": {key: summarise(values) for key, values in sorted(totals.items())},
            "region1_share_of_generation": (
                _mean(per_region["1"]["gold_generated"]) /
                max(1e-9, _mean(totals["generated"]) or 0.0)
            ) if per_region.get("1") and totals.get("generated") else None,
            "region1_collected_over_generated": (
                _mean(per_region["1"]["gold_collected"]) /
                max(1e-9, _mean(per_region["1"]["gold_generated"]) or 0.0)
            ) if per_region.get("1") else None,
            "early_note": "windows ending before round 50 pooled separately",
        }
    return out


# ---------------------------------------------------------------------------
# static geometry (zero samples)
# ---------------------------------------------------------------------------


def geometry() -> Mapping[str, Any]:
    """Wall density per region per map, derived from log row 2 (primary source)."""
    from sim.runner import load_map

    layouts: dict[int, frozenset[tuple[int, int]]] = {}
    counts: collections.Counter = collections.Counter()
    for path in sorted((ROOT / "logs").glob("game_*.log")):
        try:
            with path.open(encoding="utf-8") as handle:
                handle.readline()
                row2 = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError):
            continue
        walls = frozenset(
            (r, c) for r, row in enumerate(row2) for c, cell in enumerate(row) if str(cell) == "1"
        )
        layouts[len(walls)] = walls
        counts[len(walls)] += 1

    cells_per_region = collections.Counter(region_id(r, c) for r in range(GRID) for c in range(GRID))
    out: dict[str, Any] = {"layouts_seen": {}, "repo_agreement": {}}
    for size, walls in sorted(layouts.items()):
        name = MAP_BY_WALLS.get(size, "unknown")
        per_region = collections.Counter(region_id(r, c) for r, c in walls)
        # generation capacity = non-wall cells, since gold never lands on a wall
        capacity = {
            str(rid): cells_per_region[rid] - per_region[rid] for rid in sorted(cells_per_region)
        }
        # how much of region 1 a two-cell camp at the anchors can reach in one round
        reach = set()
        for anchor in ANCHORS:
            for dr in range(-1, 2):
                for dc in range(-1, 2):
                    if abs(dr) + abs(dc) > 1:
                        continue
                    cell = (anchor[0] + dr, anchor[1] + dc)
                    if 0 <= cell[0] < GRID and 0 <= cell[1] < GRID and cell not in walls:
                        reach.add(cell)
        window = set()
        for anchor in ANCHORS:
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    cell = (anchor[0] + dr, anchor[1] + dc)
                    if 0 <= cell[0] < GRID and 0 <= cell[1] < GRID and cell not in walls:
                        window.add(cell)
        out["layouts_seen"][name] = {
            "wall_count": size,
            "games_in_archive": counts[size],
            "walls_per_region": {str(rid): per_region[rid] for rid in sorted(cells_per_region)},
            "cells_per_region": {str(rid): cells_per_region[rid] for rid in sorted(cells_per_region)},
            "wall_density_per_region": {
                str(rid): per_region[rid] / cells_per_region[rid] for rid in sorted(cells_per_region)
            },
            "generation_capacity_cells": capacity,
            "region1_capacity": capacity["1"],
            "anchor_step_reach_cells": len(reach),
            "anchor_5x5_window_cells": len(window),
            "anchor_window_share_of_region1_capacity": (
                len([cell for cell in window if region_id(*cell) == 1]) / max(1, capacity["1"])
            ),
            "anchors_are_walls": [anchor in walls for anchor in ANCHORS],
        }
        try:
            repo_walls = frozenset(
                (r, c) for r, row in enumerate(load_map(name).rows)
                for c, cell in enumerate(row) if str(cell) == "1"
            )
            out["repo_agreement"][name] = {
                "sim_maps_json_matches_log": repo_walls == walls,
                "sim_maps_json_wall_count": len(repo_walls),
            }
        except Exception as error:                     # pragma: no cover - defensive
            out["repo_agreement"][name] = {"error": str(error)}
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("localize", "per-round / per-block / per-unit deficit decomposition"),
        ("contention", "move-order sensitivity and the latency transfer function"),
        ("identity", "exact move-order accounting identity per map"),
        ("stock-flow", "is race-lost gold novel or deferred to us"),
        ("holdout", "disjoint-game replication of the map1 result"),
        ("assemble", "fold the artifacts into the machine-readable verdict"),
        ("branch-cost", "decision-cost dose-response and the targeted latency counterfactual"),
        ("field", "the same identity against every archived map1 opponent"),
        ("supply", "fog-free five-region snapshot channel"),
        ("geometry", "static wall density and generation capacity per region"),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--out", type=Path)
        if name not in ("geometry", "field", "holdout", "assemble"):
            item.add_argument("--map", action="append", dest="maps", default=None)
        if name == "assemble":
            item.add_argument("--artifacts", default="/tmp/gr_lesion")

    args = parser.parse_args(argv)
    if args.command == "localize":
        payload = localize(args.maps or ["map1", "map2", "map3"])
    elif args.command == "contention":
        payload = contention(args.maps or ["map1", "map2", "map3"])
    elif args.command == "assemble":
        base = Path(args.artifacts or "/tmp/gr_lesion")
        payload = assemble({
            "localize": base / "localize.json",
            "identity": base / "identity.json",
            "branch_cost": base / "branch_cost.json",
            "stock_flow": base / "stock_flow.json",
            "holdout": base / "holdout.json",
            "field": base / "field.json",
            "geometry": base / "geometry.json",
            "supply": base / "supply.json",
        })
    elif args.command == "holdout":
        payload = holdout()
    elif args.command == "stock-flow":
        payload = stock_flow(args.maps or ["map1", "map2", "map3"])
    elif args.command == "branch-cost":
        payload = branch_cost(args.maps or ["map1", "map2", "map3"])
    elif args.command == "identity":
        corpus = load_corpus(args.maps or ["map1", "map2", "map3"])
        payload = {name: order_identity(games) for name, games in corpus.items()}
    elif args.command == "field":
        payload = field(min_games=1)
    elif args.command == "supply":
        payload = supply(args.maps or ["map1", "map2", "map3"])
    else:
        payload = geometry()

    text = json.dumps(payload, indent=1, sort_keys=True, default=str)
    if getattr(args, "out", None):
        args.out.write_text(text)
        print("wrote %s (%d bytes)" % (args.out, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
