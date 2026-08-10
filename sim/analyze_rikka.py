#!/usr/bin/env python3
"""Reverse-engineer team ``rikka`` (user_id 47) from archived platform logs.

Why this file exists
--------------------
``rikka`` sits high on the public ladder while carrying a large reported P90, so
it looked like a sample of "wins on strategy alone while always moving second".
That framing turns out to be wrong in a way that matters, and the wrongness is
the point of this analyser: ``rikka`` fields *two different constructs*.

* ``player47`` -- the public defence slot (model_id 51256).  P50 about 360 ns and
  a step allocation of exactly 3+3, i.e. architecturally the same shape as ours.
* ``g47v220m1/m2/m3`` -- bespoke challenge builds whose names literally encode
  "47 versus 220 on map N".  These run 263 us to 11 ms per round and vary the
  step split ``k`` across the whole 0..6 range.

Merging those two would violate the same-construct rule, so every statistic here
is reported per construct family and never pooled across them.

Measurement rules respected
---------------------------
Latency: only ``end.players[i].cost`` is read.  ``start[r].cost`` is a stale copy
of ``end[r-1].cost``, and rounds 0..3 are warm-up, so they are dropped.

Income: only per-unit ``end`` ``gold`` is differenced.  That field is recorded in
100% of unit-observations whether or not the unit is visible; ``pickup`` is not.

Trajectory: an opponent's ``actions`` array is *fog-truncated* -- it holds only
the prefix taken while the unit stayed visible.  ``logs/game_163075.log`` round
69 shows ``position: null, actions: [1], pickup: 0`` while that unit's ``gold``
rose by 7, so both ``actions`` and ``pickup`` are silently short there.  Two
consequences are used throughout:

* a unit-observation with a non-null ``end`` position has a *complete* action
  list (``verify`` checks this by replaying the deltas), so a round in which both
  units are visible pins ``k`` exactly and the two lengths must sum to six;
* ``len(actions) >= 4`` proves an allocation of at least four steps even when the
  position is null, because truncation only ever removes a suffix.

Our own side is the zero-signal control for the ``k`` adjudicator: ``player.cpp``
emits ``out.k = 3`` unconditionally, so it must report 0% at ``len >= 4``.
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
LOGS = ROOT / "logs"
REPORTS = ROOT / "sim" / "reports"

OUR_ACCOUNT = "player220"          # our public defence slot; team 0x8F, user_id 220
RIKKA_PUBLIC = "player47"          # rikka's public defence slot, model_id 51256


def public_slot(uid: int) -> str:
    """A team's public defence slot is named ``player<user_id>``."""
    return "player%d" % uid

# Our public slot carried the 8/7 build until this instant, when fd47ea6 went up.
# Before it our defence ran at P50 about 3600 ns; after it, about 200 ns.  Passive
# games either side of the line are different constructs and are never pooled.
PUBLISH_BOUNDARY = "2026-08-10T08:20:18Z"

# Engine mechanics, mirrored from sim/engine.py for self-containment.
ACTION_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
ACTIONS_PER_PLAYER = 6
WARMUP_ROUNDS = 4

# A single ordinary cell of value v <= 10 yields (65*v+99)//100 <= 7, so a
# unit-round gain above 7 needs several cells or one cell richer than 10.
MAX_ORDINARY_PICKUP = (65 * 10 + 99) // 100


# ----------------------------------------------------------------- log plumbing

def header(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.loads(handle.readline())


def rounds(path: Path) -> Iterable[Mapping[str, Any]| None]:
    """Yield round records; a forfeit/short row is yielded as a None sentinel."""
    with path.open(encoding="utf-8") as handle:
        handle.readline()               # header
        handle.readline()               # map token grid
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            yield record if ("start" in record and "end" in record) else None


def player_of(state: Mapping[str, Any], pid: int) -> Mapping[str, Any] | None:
    for entry in state.get("players", []):
        if int(entry["id"]) == pid:
            return entry
    return None


# --------------------------------------------------------------- small numerics

def percentile(values: Sequence[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def mean_se(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1:
        return float(values[0]), None
    return statistics.fmean(values), statistics.stdev(values) / math.sqrt(len(values))


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """95% Wilson interval; small-n honest, unlike the normal approximation."""
    if total == 0:
        return None
    z = 1.959963984540054
    phat = successes / total
    denom = 1.0 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# ------------------------------------------------------------------- game index

def load_roster(path: Path) -> list[Mapping[str, Any]]:
    """Load the platform game index that identifies opponents by team."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def rikka_games(index: Sequence[Mapping[str, Any]], uid: int = 47) -> list[Mapping[str, Any]]:
    return [row for row in index if uid in (row.get("user_id"), row.get("user_id2"))]


def classify(row: Mapping[str, Any], uid: int = 47,
             fold_prefix: str | None = None) -> Mapping[str, Any]:
    """Resolve construct families and the publish-boundary stratum for one game.

    ``fold_prefix`` folds our own replicate names into a single stratum, e.g. the
    pre-registered batch ``pl47a``..``pl47t`` collapses to ``active_pl47``.  It is
    an explicit opt-in rather than a general "strip the trailing letter" rule,
    because that rule would silently merge genuinely different constructs -- it
    would turn ``cpp20``/``cpp21``/``cpp22`` into one bucket.
    """
    theirs = ours = None
    for entry in row["players"]:
        name = str(entry["model_name"])
        if name == OUR_ACCOUNT or entry.get("user_name_cn") == "0x8F":
            ours = entry
        else:
            theirs = entry
    if ours is None or theirs is None:
        return {}
    initiated_by_them = row.get("user_id") == uid
    created = str(row.get("created_at", ""))
    our_name = str(ours["model_name"])
    folded = (fold_prefix if (fold_prefix and our_name.startswith(fold_prefix))
              else our_name)
    return {
        "game_id": row["id"],
        "map": "map%s" % row.get("map_id"),
        "created_at": created,
        # They initiated => we defended with the public slot => our construct is
        # whatever was published at the time, which the boundary resolves.
        "passive_for_us": initiated_by_them,
        "their_model": str(theirs["model_name"]),
        "our_model": our_name,
        "their_family": "public" if str(theirs["model_name"]) == public_slot(uid) else "targeted",
        "our_stratum": (
            ("defence_fd47ea6" if created >= PUBLISH_BOUNDARY else "defence_0807_slow")
            if initiated_by_them else "active_%s" % folded
        ),
        "our_coins": int(ours["coin_num"]),
        "their_coins": int(theirs["coin_num"]),
        "we_won": bool(ours["is_win"]),
    }


# ----------------------------------------------------------------- measurements

def latency(path: Path) -> Mapping[int, Mapping[str, Any]]:
    """Steady-state end-phase cost per player id, warm-up rounds dropped."""
    series: dict[int, list[int]] = collections.defaultdict(list)
    for record in rounds(path):
        if record is None or int(record["round"]) < WARMUP_ROUNDS:
            continue
        for entry in record["end"]["players"]:
            series[int(entry["id"])].append(int(entry["cost"]))
    return {
        pid: {
            "n": len(values),
            "p50": percentile(values, 0.50),
            "p90": percentile(values, 0.90),
            "max": max(values),
        }
        for pid, values in series.items()
    }


def order_share(path: Path, our_pid: int) -> Mapping[str, Any]:
    """Rounds in which our measured cost is strictly lower, i.e. we move first.

    ``f`` is *endogenous*: it is produced by both sides' own branch behaviour, so
    it identifies nothing on its own.  ``near_tie`` restricts to rounds where the
    cost gap is at most 10 ns -- one platform quantisation step -- which is the
    closest available approximation to a coin flip for who moves first.
    """
    first = ties = total = 0
    near_first = near_total = 0
    for record in rounds(path):
        if record is None or int(record["round"]) < WARMUP_ROUNDS:
            continue
        costs = {int(e["id"]): int(e["cost"]) for e in record["end"]["players"]}
        if our_pid not in costs or len(costs) != 2:
            continue
        ours = costs[our_pid]
        theirs = next(value for pid, value in costs.items() if pid != our_pid)
        total += 1
        first += ours < theirs
        ties += ours == theirs
        if abs(ours - theirs) <= 10:
            near_total += 1
            near_first += ours < theirs
    return {
        "rounds": total,
        "first_mover_rounds": first,
        "ties": ties,
        "f": first / total if total else None,
        "near_tie_rounds": near_total,
        "near_tie_f": near_first / near_total if near_total else None,
    }


def gold_deltas(path: Path, our_pid: int) -> Mapping[str, list[int]]:
    """Per-unit end-phase held-gold deltas: the complete, fog-free income series."""
    previous: dict[int, list[int]] = {}
    series: dict[str, list[int]] = {"ours": [], "theirs": []}
    for record in rounds(path):
        if record is None:                      # forfeit: break the chain
            previous = {}
            continue
        for entry in record["end"]["players"]:
            pid = int(entry["id"])
            current = [int(unit["gold"]) for unit in entry["units"]]
            earlier = previous.get(pid)
            if earlier is not None and len(earlier) == len(current):
                key = "ours" if pid == our_pid else "theirs"
                series[key].extend(now - was for now, was in zip(current, earlier))
            previous[pid] = current
    return series


def describe(values: Sequence[int]) -> Mapping[str, float]:
    """Factor a per-unit-round income series.

    ``mean`` is the only directly convertible quantity: a mean of 1.0 gold per
    unit-round is 1000 gold per game.  It is *not* ``hit * yield_per_hit``; the
    residual is the burn floor below.
    """
    total = len(values)
    if not total:
        return {}
    gains = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    mean, se = mean_se(values)
    return {
        "n": total,
        "mean": mean,
        "se": se,
        "hit": len(gains) / total,
        "yield_per_hit": statistics.fmean(gains) if gains else 0.0,
        "gross": sum(gains) / total,
        "burn_floor": -sum(losses) / total,
        "loss_rate": len(losses) / total,
        "ge8": sum(1 for v in values if v > MAX_ORDINARY_PICKUP) / total,
        "zero": sum(1 for v in values if v == 0) / total,
    }


def net_delta(path: Path, our_pid: int) -> float | None:
    """Final net-score difference, ours minus theirs, from the last full round."""
    last = None
    for record in rounds(path):
        if record is not None:
            last = record
    if last is None:
        return None
    scores = {
        int(e["id"]): int(e["gold"]) - int(e["vision_spent"])
        for e in last["end"]["players"]
    }
    others = [value for pid, value in scores.items() if pid != our_pid]
    if our_pid not in scores or not others:
        return None
    return scores[our_pid] - others[0]


def vision(path: Path) -> Mapping[int, int]:
    last = None
    for record in rounds(path):
        if record is not None:
            last = record
    if last is None:
        return {}
    return {int(e["id"]): int(e["vision_spent"]) for e in last["end"]["players"]}


def step_budget(path: Path, pid: int) -> Mapping[str, Any]:
    """Adjudicate the step split ``k`` for one player.

    Two independent readings are produced:

    ``proven_over_three``  observations with ``len(actions) >= 4``.  Valid even
        under fog truncation, because truncation only removes a suffix.  Our own
        side must score exactly zero here.
    ``exact_k``            distribution over rounds in which *both* units have a
        non-null end position, where the two lengths are complete and must sum
        to ``ACTIONS_PER_PLAYER``.  ``sum_violations`` is an extraction check
        that has to stay at zero.
    """
    observations = over_three = 0
    both_visible = sum_violations = 0
    exact: collections.Counter[int] = collections.Counter()
    visible_lengths: dict[int, list[int]] = {0: [], 1: []}
    for record in rounds(path):
        if record is None:
            continue
        entry = player_of(record["end"], pid)
        if entry is None:
            continue
        units = entry["units"]
        for index, unit in enumerate(units):
            actions = unit.get("actions")
            if actions is None:
                continue
            observations += 1
            over_three += len(actions) >= 4
            if unit.get("position") is not None:
                visible_lengths[index].append(len(actions))
        if len(units) == 2 and all(
            unit.get("position") is not None and unit.get("actions") is not None
            for unit in units
        ):
            both_visible += 1
            lengths = [len(unit["actions"]) for unit in units]
            if sum(lengths) != ACTIONS_PER_PLAYER:
                sum_violations += 1
            else:
                exact[lengths[0]] += 1
    mean_k = (
        sum(k * c for k, c in exact.items()) / sum(exact.values()) if exact else None
    )
    return {
        "observations": observations,
        "proven_over_three": over_three,
        "proven_over_three_rate": over_three / observations if observations else None,
        "both_visible_rounds": both_visible,
        "sum_violations": sum_violations,
        "exact_k": {k: exact.get(k, 0) for k in range(ACTIONS_PER_PLAYER + 1)},
        "mean_k": mean_k,
        "mean_visible_len": {
            index: (statistics.fmean(values) if values else None)
            for index, values in visible_lengths.items()
        },
    }


def reach(path: Path, pid: int) -> Mapping[str, Any]:
    """Distinct cells stepped per unit-round, from complete action lists only.

    Restricted to unit-observations whose ``end`` position is non-null, because
    only those carry a complete action list.  That restriction is a *selection*:
    a visible unit is by construction near one of our units.  The bias direction
    is stated in the report rather than corrected here.
    """
    per_unit: list[int] = []
    per_unit_steps: list[int] = []
    per_round: list[int] = []
    covered = total = 0
    for record in rounds(path):
        if record is None:
            continue
        entry = player_of(record["end"], pid)
        if entry is None:
            continue
        total += 1
        cells_this_round: set[tuple[int, int]] = set()
        complete = 0
        for unit in entry["units"]:
            position, actions = unit.get("position"), unit.get("actions")
            if position is None or actions is None:
                continue
            complete += 1
            # Walk backwards from the known end position through the deltas.
            row, col = int(position[0]), int(position[1])
            visited = [(row, col)]
            for action in reversed(actions):
                delta = ACTION_DELTAS[int(action)]
                row, col = row - delta[0], col - delta[1]
                visited.append((row, col))
            distinct = set(visited)
            per_unit.append(len(distinct))
            per_unit_steps.append(sum(1 for a in actions if int(a) != 4))
            cells_this_round |= distinct
        if complete == 2:
            covered += 1
            per_round.append(len(cells_this_round))
    unit_mean, unit_se = mean_se(per_unit)
    round_mean, round_se = mean_se(per_round)
    steps_mean, _ = mean_se(per_unit_steps)
    return {
        "unit_observations": len(per_unit),
        "distinct_cells_per_unit_round": unit_mean,
        "distinct_cells_per_unit_round_se": unit_se,
        "effective_steps_per_unit_round": steps_mean,
        "both_complete_rounds": covered,
        "rounds": total,
        "coverage": covered / total if total else None,
        "distinct_cells_per_player_round": round_mean,
        "distinct_cells_per_player_round_se": round_se,
    }


def verify_action_completeness(path: Path, pid: int) -> Mapping[str, Any]:
    """Replay deltas between consecutive visible positions; must match exactly.

    This is the proof that a non-null end position implies a complete action
    list.  It is also the negative control for the ``reach`` walk-back: if the
    direction table or the effective-action semantics were wrong, this fails.
    """
    previous: dict[int, Any] = {}
    pairs = exact = 0
    for record in rounds(path):
        if record is None:
            previous = {}
            continue
        entry = player_of(record["end"], pid)
        if entry is None:
            continue
        for index, unit in enumerate(entry["units"]):
            position, actions = unit.get("position"), unit.get("actions")
            earlier = previous.get(index)
            if earlier is not None and position is not None and actions is not None:
                row, col = int(earlier[0]), int(earlier[1])
                for action in actions:
                    delta = ACTION_DELTAS[int(action)]
                    row, col = row + delta[0], col + delta[1]
                pairs += 1
                exact += (row == int(position[0]) and col == int(position[1]))
            previous[index] = position
    return {"pairs": pairs, "exact": exact, "rate": exact / pairs if pairs else None}


# ------------------------------------------------------------------ aggregation

def measure_game(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    path = LOGS / ("game_%s.log" % entry["game_id"])
    if not path.is_file():
        return None
    head = header(path)
    first, second = str(head.get("player1")), str(head.get("player2"))
    if entry["our_model"] == second:
        our_pid, their_pid = 2, 1
    elif entry["our_model"] == first:
        our_pid, their_pid = 1, 2
    else:
        return None
    costs = latency(path)
    result = dict(entry)
    result.update({
        "our_pid": our_pid,
        "their_pid": their_pid,
        "our_cost": costs.get(our_pid, {}),
        "their_cost": costs.get(their_pid, {}),
        "order": order_share(path, our_pid),
        "net_delta": net_delta(path, our_pid),
        "vision": vision(path),
        "their_k": step_budget(path, their_pid),
        "our_k": step_budget(path, our_pid),
    })
    return result


def stratify(games: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Group by (their construct family, our construct stratum, map)."""
    buckets: dict[tuple[str, str, str], list[Mapping[str, Any]]] = collections.defaultdict(list)
    for game in games:
        buckets[(game["their_family"], game["our_stratum"], game["map"])].append(game)
    out: dict[str, Any] = {}
    for (family, stratum, map_name), rows in sorted(buckets.items()):
        deltas = [row["net_delta"] for row in rows if row["net_delta"] is not None]
        mean, se = mean_se(deltas)
        wins = sum(row["we_won"] for row in rows)
        our_p50 = [row["our_cost"].get("p50") for row in rows if row["our_cost"].get("p50")]
        their_p50 = [row["their_cost"].get("p50") for row in rows if row["their_cost"].get("p50")]
        f_values = [row["order"]["f"] for row in rows if row["order"]["f"] is not None]
        out["%s | %s | %s" % (family, stratum, map_name)] = {
            "games": len(rows),
            "game_ids": [row["game_id"] for row in rows],
            "wins": wins,
            "win_rate": wins / len(rows),
            "win_rate_wilson95": wilson(wins, len(rows)),
            "mean_net_delta": mean,
            "se_net_delta": se,
            "sigma": (abs(mean) / se) if (mean is not None and se) else None,
            "our_cost_p50_median": percentile(our_p50, 0.50),
            "their_cost_p50_median": percentile(their_p50, 0.50),
            "our_first_mover_rate_median": percentile(f_values, 0.50),
        }
    return out


def income_by_family(games: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Fog-free income decomposition per (their family, our stratum, map)."""
    buckets: dict[tuple[str, str, str], dict[str, list[int]]] = collections.defaultdict(
        lambda: {"ours": [], "theirs": []}
    )
    score_deltas: dict[tuple[str, str, str], list[float]] = collections.defaultdict(list)
    for game in games:
        path = LOGS / ("game_%s.log" % game["game_id"])
        if not path.is_file():
            continue
        key = (game["their_family"], game["our_stratum"], game["map"])
        series = gold_deltas(path, game["our_pid"])
        buckets[key]["ours"] += series["ours"]
        buckets[key]["theirs"] += series["theirs"]
        if game["net_delta"] is not None:
            score_deltas[key].append(game["net_delta"])
    out: dict[str, Any] = {}
    for key, series in sorted(buckets.items()):
        ours, theirs = describe(series["ours"]), describe(series["theirs"])
        if not ours or not theirs:
            continue
        implied = (ours["mean"] - theirs["mean"]) * 2 * 500
        observed = statistics.fmean(score_deltas[key]) if score_deltas[key] else None
        out["%s | %s | %s" % key] = {
            "ours": ours,
            "theirs": theirs,
            "implied_score_delta": implied,
            "observed_score_delta": observed,
            "residual": (implied - observed) if observed is not None else None,
        }
    return out


# ------------------------------------------------------------------ subcommands

def _games(args: argparse.Namespace) -> list[Mapping[str, Any]]:
    index = load_roster(Path(args.index))
    rows = [classify(row, args.uid, getattr(args, 'fold_prefix', None))
            for row in rikka_games(index, args.uid)]
    measured = [measure_game(row) for row in rows if row]
    games = [row for row in measured if row]
    keep = getattr(args, "map", None)
    if keep:
        games = [row for row in games if row["map"] == keep]
    return games


def playstyle(path: Path, pid: int) -> Mapping[str, Any]:
    """Effective-step and distinct-cell profile plus the bias audit for one side.

    Everything cell- or step-related can only be read from unit-observations whose
    ``end`` position is visible, which is a *selection*: a visible opponent unit
    is by construction close to one of our units.  ``bias_ratio`` is the audit
    that decides whether the selected subset may be used at all -- it compares
    the subset's mean income against the complete, fog-free gold channel over the
    same games.  A ratio near 1.00 licenses the profile; 0.37 does not.
    """
    previous: dict[int, int] = {}
    complete: list[Mapping[str, Any]] = []
    for record in rounds(path):
        if record is None:
            previous = {}
            continue
        entry = player_of(record["end"], pid)
        if entry is None:
            continue
        for index, unit in enumerate(entry["units"]):
            gold = int(unit["gold"])
            earlier = previous.get(index)
            position, actions = unit.get("position"), unit.get("actions")
            if earlier is not None and position is not None and actions is not None:
                row, col = int(position[0]), int(position[1])
                visited = [(row, col)]
                for action in reversed(actions):
                    delta = ACTION_DELTAS[int(action)]
                    row, col = row - delta[0], col - delta[1]
                    visited.append((row, col))
                complete.append({
                    "delta": gold - earlier,
                    "allocated": len(actions),
                    "effective": sum(1 for a in actions if int(a) != 4),
                    "distinct": len(set(visited)),
                })
            previous[index] = gold
    return {"observations": complete}


def playstyle_summary(rows: Sequence[Mapping[str, Any]],
                      unbiased_mean: float | None) -> Mapping[str, Any]:
    if not rows:
        return {}
    deltas = [row["delta"] for row in rows]
    allocated = sum(row["allocated"] for row in rows)
    effective = sum(row["effective"] for row in rows)
    gross = sum(value for value in deltas if value > 0)
    subset_mean = statistics.fmean(deltas)
    distinct_mean, distinct_se = mean_se([row["distinct"] for row in rows])
    return {
        "unit_rounds": len(rows),
        "bias_ratio": (subset_mean / unbiased_mean) if unbiased_mean else None,
        "subset_mean": subset_mean,
        "unbiased_mean": unbiased_mean,
        "effective_steps_per_unit_round": effective / len(rows),
        "wasted_step_share": (allocated - effective) / allocated if allocated else None,
        "distinct_cells_per_unit_round": distinct_mean,
        "distinct_cells_se": distinct_se,
        "stationary_share": sum(1 for row in rows if row["distinct"] == 1) / len(rows),
        # Two different prices for a step.  The allocated price is what a fixed
        # 6-per-round budget actually buys; the effective price is what a step
        # earns once it has managed to move at all.
        "gross_per_effective_step": gross / effective if effective else None,
        "gross_per_allocated_step": gross / allocated if allocated else None,
    }


def cmd_styles(args: argparse.Namespace) -> int:
    """Compare our playstyle against one opponent's, audit attached.

    Answers a specific question: is a rival's income advantage bought by wasting
    fewer of its six steps, or by earning more per step that does move?  Those
    two point at different repairs, and the fixed 6-step budget makes them
    separable without any counterfactual.
    """
    games = _games(args)
    if not games:
        print("no games for uid=%s map=%s" % (args.uid, getattr(args, "map", None)))
        return 0
    groups: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for game in games:
        label = "%s %s" % (game["their_model"], game["map"])
        groups[label].append(game)
    print("uid=%s  games=%d  (their side, then ours, per model x map)" % (args.uid, len(games)))
    print("%-24s %5s %7s %6s %8s %8s %7s %7s %8s %8s" % (
        "side", "games", "unit-r", "bias", "unbias", "effstep", "waste", "distinct",
        "g/effstep", "g/allocstep"))
    for label, rows in sorted(groups.items()):
        for who in ("theirs", "ours"):
            pooled: list[Mapping[str, Any]] = []
            unbiased: list[int] = []
            for game in rows:
                path = LOGS / ("game_%s.log" % game["game_id"])
                pid = game["their_pid"] if who == "theirs" else game["our_pid"]
                pooled += playstyle(path, pid)["observations"]
                unbiased += gold_deltas(path, game["our_pid"])[
                    "theirs" if who == "theirs" else "ours"]
            summary = playstyle_summary(
                pooled, statistics.fmean(unbiased) if unbiased else None)
            if not summary:
                continue
            print("%-24s %5d %7d %6.2f %8.3f %8.2f %6.1f%% %7.3f %8.3f %8.3f" % (
                ("%s | %s" % (label, who))[:24], len(rows), summary["unit_rounds"],
                summary["bias_ratio"] or 0.0, summary["unbiased_mean"] or 0.0,
                summary["effective_steps_per_unit_round"],
                100 * (summary["wasted_step_share"] or 0.0),
                summary["distinct_cells_per_unit_round"],
                summary["gross_per_effective_step"] or 0.0,
                summary["gross_per_allocated_step"] or 0.0))
    print()
    print("bias column is subset_mean/unbiased_mean; only rows near 1.00 license the")
    print("distinct/effstep columns.  unbias column is the complete gold channel and")
    print("is always safe.  waste = share of the six allocated steps that did not move.")
    return 0


def burn_partition(path: Path, pid: int) -> Mapping[str, Any]:
    """Partition one side's destroyed gold into bomb-on-a-seen-cell, fog, and trample.

    Why the partition matters: a burn caused by a bomb the player *could see* is a
    deliberate trade made by the richness gate, whereas a burn on an unseen bomb
    would be a knowledge deficit that a wider scan could repair.  The two call for
    completely different work, and only the second would justify a scan upgrade.

    The classifier walks each unit forward from its round-start position through
    its effective actions and reads the round-start grid, which is what the
    decision actually saw.  ``-3`` is a bomb, ``-5`` is fog.  Chebyshev distance
    from the unit's own start cell decides whether the cell was inside that unit's
    5x5 scan window at all; anything at distance 3 was invisible to it even if
    another unit could see it.

    Categories can overlap when one unit-round carries both a bomb and a trample,
    so ``residual`` is reported rather than forced to zero.
    """
    previous: dict[int, int] = {}
    lost = bomb_seen = fog_walk = other = 0
    trample_ours = trample_theirs = 0
    distances: collections.Counter[int] = collections.Counter()
    held_at_hit: list[int] = []
    for record in rounds(path):
        if record is None:
            previous = {}
            continue
        start, end = record.get("start"), record["end"]
        entry, start_entry = player_of(end, pid), player_of(start, pid) if start else None
        for event in end.get("trample_events") or []:
            penalty = int(event.get("penalty") or 0)
            if int(event.get("unit_owner", -1)) == pid:
                trample_ours += penalty
            else:
                trample_theirs += penalty
        if entry is None or start_entry is None:
            continue
        grid = start.get("grid")
        for index, unit in enumerate(entry["units"]):
            gold = int(unit["gold"])
            earlier = previous.get(index)
            actions = unit.get("actions")
            start_pos = start_entry["units"][index].get("position")
            if earlier is None or actions is None or start_pos is None or grid is None:
                previous[index] = gold
                continue
            delta = gold - earlier
            if delta < 0:
                row, col = int(start_pos[0]), int(start_pos[1])
                seen_bomb = walked_fog = False
                for action in actions:
                    step = ACTION_DELTAS[int(action)]
                    row, col = row + step[0], col + step[1]
                    if not (0 <= row < 17 and 0 <= col < 17):
                        continue
                    value = grid[row][col]
                    if value == -3:
                        seen_bomb = True
                        distances[max(abs(row - int(start_pos[0])),
                                      abs(col - int(start_pos[1])))] += 1
                    elif value == -5:
                        walked_fog = True
                lost += -delta
                if seen_bomb:
                    bomb_seen += -delta
                    held_at_hit.append(earlier)
                elif walked_fog:
                    fog_walk += -delta
                else:
                    other += -delta
            previous[index] = gold
    return {
        "gold_lost": lost,
        "on_a_seen_bomb": bomb_seen,
        "walked_into_fog": fog_walk,
        "neither": other,
        "trample_ours": trample_ours,
        "trample_theirs": trample_theirs,
        "bomb_only": lost - trample_ours,
        "residual_after_trample": other - trample_ours,
        "seen_bomb_cheb_distance": dict(sorted(distances.items())),
        "held_before_hit_median": statistics.median(held_at_hit) if held_at_hit else None,
        "share_held_below_gate": (
            sum(1 for value in held_at_hit if value < 100) / len(held_at_hit)
            if held_at_hit else None
        ),
    }


def cmd_burn(args: argparse.Namespace) -> int:
    """Reprice and partition our own destroyed gold over the current construct family.

    Runs over every archived game of the live artefact plus the frozen build it
    descends from, on all maps, because the question is mechanical rather than
    map-specific.  Our own side is 100% visible, so this measurement carries no
    selection bias and needs no licensing ratio.
    """
    index = load_roster(Path(args.index))
    chosen: list[tuple[Path, int]] = []
    for row in index:
        for entry in row["players"]:
            name = str(entry["model_name"])
            current = (
                (name == OUR_ACCOUNT and str(row.get("created_at", "")) >= PUBLISH_BOUNDARY)
                or name.startswith("frTu") or name.startswith("t1f")
            )
            if not current:
                continue
            path = LOGS / ("game_%s.log" % row["id"])
            if not path.is_file():
                continue
            head = header(path)
            pid = (1 if str(head.get("player1")) == name
                   else 2 if str(head.get("player2")) == name else None)
            if pid:
                chosen.append((path, pid))
    if not chosen:
        print("no games of the current construct family are archived")
        return 0
    totals: collections.Counter[str] = collections.Counter()
    distances: collections.Counter[int] = collections.Counter()
    held: list[int] = []
    for path, pid in chosen:
        found = burn_partition(path, pid)
        for key in ("gold_lost", "on_a_seen_bomb", "walked_into_fog", "neither",
                    "trample_ours", "trample_theirs"):
            totals[key] += found[key]
        for distance, count in found["seen_bomb_cheb_distance"].items():
            distances[int(distance)] += count
        if found["held_before_hit_median"] is not None:
            held.append(found["held_before_hit_median"])
    games = len(chosen)
    print("current construct family: %d archived games, all maps" % games)
    print("%-46s %10s %12s" % ("component", "total", "gold/game"))
    for label, key in (
        ("gold destroyed on our side (all causes)", "gold_lost"),
        ("  on a bomb VISIBLE in the round-start grid", "on_a_seen_bomb"),
        ("  walked into fog", "walked_into_fog"),
        ("  neither (includes trample)", "neither"),
        ("trample penalty, our units", "trample_ours"),
        ("trample penalty, their units", "trample_theirs"),
    ):
        print("%-46s %10d %12.1f" % (label, totals[key], totals[key] / games))
    bomb_only = totals["gold_lost"] - totals["trample_ours"]
    print("%-46s %10d %12.1f" % ("=> bomb burn (total minus own trample)", bomb_only, bomb_only / games))
    print()
    print("Chebyshev distance of seen-bomb cells from the unit's round-start cell")
    print("(<=2 is inside that unit's own 5x5 scan; 3 would be invisible to it):")
    for distance in sorted(distances):
        print("   d=%d : %d cells" % (distance, distances[distance]))
    if not any(d >= 3 for d in distances):
        print("   -> no detonation beyond the scan window: a wider scan recovers nothing here")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    """Realised central-9x9 occupancy of our own units, against the exogenous gradient.

    Two distinct things get called "coverage" and they differ a lot:

    * the *static* figure, i.e. which cells lie inside the two anchors' vision
      windows if both units never move;
    * the *realised* figure measured here, i.e. which cells a unit actually stood
      on over a season of games.

    Our own units are visible in 100% of unit-observations, so this needs no
    licensing ratio.  The occupancy profile is then compared against the central
    generation gradient from ``sim/GENERATION.md``, which was measured from
    full-information logs and is therefore exogenous to our behaviour.

    Deliberately NOT computed here: mean observed gold on rarely- versus
    often-occupied cells.  That comparison is endogenous -- a cell becomes
    often-occupied *because* gold was seen on it -- so it mostly measures reverse
    causation and would overstate the case for staying put.
    """
    # sim/GENERATION.md:106, per-cell generation rate by column offset from 8.
    COLUMN_RATE = {4: 20.0, 5: 33.3, 6: 41.6, 7: 52.9, 8: 56.3,
                   9: 50.6, 10: 40.0, 11: 33.9, 12: 22.8}
    index = load_roster(Path(args.index))
    chosen: list[tuple[Path, int]] = []
    for row in index:
        if args.map and ("map%s" % row.get("map_id")) != args.map:
            continue
        for entry in row["players"]:
            name = str(entry["model_name"])
            current = (
                (name == OUR_ACCOUNT and str(row.get("created_at", "")) >= PUBLISH_BOUNDARY)
                or name.startswith("frTu") or name.startswith("t1f")
            )
            if not current:
                continue
            path = LOGS / ("game_%s.log" % row["id"])
            if not path.is_file():
                continue
            head = header(path)
            pid = (1 if str(head.get("player1")) == name
                   else 2 if str(head.get("player2")) == name else None)
            if pid:
                chosen.append((path, pid))
    if not chosen:
        print("no archived games match")
        return 0
    occupancy: collections.Counter[tuple[int, int]] = collections.Counter()
    unit_rounds = low_productivity = inside = 0
    for path, pid in chosen:
        for record in rounds(path):
            if record is None:
                continue
            entry = player_of(record["end"], pid)
            if entry is None:
                continue
            for unit in entry["units"]:
                position, actions = unit.get("position"), unit.get("actions")
                if position is None:
                    continue
                row_i, col_i = int(position[0]), int(position[1])
                unit_rounds += 1
                occupancy[(row_i, col_i)] += 1
                if 4 <= row_i <= 12 and 4 <= col_i <= 12:
                    inside += 1
                if actions is not None and sum(1 for a in actions if int(a) != 4) <= 2:
                    low_productivity += 1
    central = [(r, c) for r in range(4, 13) for c in range(4, 13)]
    touched = [cell for cell in central if occupancy[cell]]
    print("current construct family, %s: %d games, %d unit-rounds"
          % (args.map or "all maps", len(chosen), unit_rounds))
    print("  REALISED central 9x9 coverage : %d/81 = %.1f%% of cells ever occupied"
          % (len(touched), 100 * len(touched) / 81))
    print("  unit-rounds spent inside the central 9x9 : %.1f%%" % (100 * inside / unit_rounds))
    print("  low-productivity unit-rounds (<=2 effective moves) : %.1f%%"
          % (100 * low_productivity / unit_rounds))
    print()
    print("central column occupancy against the exogenous generation gradient:")
    print("  %-6s %12s %14s %12s" % ("column", "occupancy", "generation", "over/under"))
    columns = {c: sum(occupancy[(r, c)] for r in range(4, 13)) for c in range(4, 13)}
    total = sum(columns.values()) or 1
    rate_total = sum(COLUMN_RATE.values())
    for col in range(4, 13):
        share = columns[col] / total
        expected = COLUMN_RATE[col] / rate_total
        print("  %-6d %11.1f%% %13.1f%% %11.2fx"
              % (col, 100 * share, 100 * expected, share / expected))
    edge = columns[4] / total
    peak = columns[8] / total
    gradient = COLUMN_RATE[8] / COLUMN_RATE[4]
    print()
    print("  occupancy ratio col8/col4 = %.1fx ; generation ratio = %.2fx"
          % (peak / edge if edge else float("inf"), gradient))
    if edge:
        print("  => we are %.1fx more concentrated than the gold gradient alone justifies"
              % ((peak / edge) / gradient))
    print("  (gold is a stock, so the optimum is more concentrated than proportional;")
    print("   this ratio bounds the over-concentration, it is not the amount to remove)")
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    games = _games(args)
    print("%-8s %-6s %-20s %-14s %-16s %8s %8s %6s %7s %s" % (
        "game", "map", "created_at", "their_model", "our_stratum",
        "ourP50", "theirP50", "f", "net", "W"))
    for game in sorted(games, key=lambda row: row["created_at"]):
        print("%-8s %-6s %-20s %-14s %-16s %8s %8s %6.3f %7s %s" % (
            game["game_id"], game["map"], game["created_at"][:19],
            game["their_model"], game["our_stratum"],
            game["our_cost"].get("p50"), game["their_cost"].get("p50"),
            game["order"]["f"] or 0.0, game["net_delta"],
            "W" if game["we_won"] else "L"))
    print()
    print(json.dumps(stratify(games), ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_income(args: argparse.Namespace) -> int:
    print(json.dumps(income_by_family(_games(args)), ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    games = _games(args)
    print("%-8s %-14s %-16s %7s %9s %7s %6s %s" % (
        "game", "their_model", "our_stratum", "obs", "len>=4", "bothvis", "meank", "violations"))
    for game in sorted(games, key=lambda row: row["created_at"]):
        theirs = game["their_k"]
        print("%-8s %-14s %-16s %7d %9s %7d %6s %d" % (
            game["game_id"], game["their_model"], game["our_stratum"],
            theirs["observations"],
            "%d (%.1f%%)" % (theirs["proven_over_three"],
                             100 * (theirs["proven_over_three_rate"] or 0)),
            theirs["both_visible_rounds"],
            "%.2f" % theirs["mean_k"] if theirs["mean_k"] is not None else "-",
            theirs["sum_violations"]))
    print()
    print("zero-signal control -- our own side, player.cpp emits k=3 always:")
    for game in sorted(games, key=lambda row: row["created_at"])[:6]:
        ours = game["our_k"]
        print("  game %s  obs=%d  len>=4=%d  exact_k=%s  violations=%d" % (
            game["game_id"], ours["observations"], ours["proven_over_three"],
            {k: v for k, v in ours["exact_k"].items() if v}, ours["sum_violations"]))
    return 0


def cmd_reach(args: argparse.Namespace) -> int:
    games = _games(args)
    out: dict[str, Any] = {}
    for game in games:
        path = LOGS / ("game_%s.log" % game["game_id"])
        out[str(game["game_id"])] = {
            "map": game["map"],
            "their_model": game["their_model"],
            "our_stratum": game["our_stratum"],
            "verify_theirs": verify_action_completeness(path, game["their_pid"]),
            "verify_ours": verify_action_completeness(path, game["our_pid"]),
            "theirs": reach(path, game["their_pid"]),
            "ours": reach(path, game["our_pid"]),
            "vision": game["vision"],
        }
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    games = _games(args)
    payload = {
        "schema_version": 1,
        "our_account": OUR_ACCOUNT,
        "publish_boundary_utc": PUBLISH_BOUNDARY,
        "rikka_public_model": RIKKA_PUBLIC,
        "games": games,
        "strata": stratify(games),
        "income": income_by_family(games),
        "reach": {
            str(game["game_id"]): {
                "map": game["map"],
                "their_model": game["their_model"],
                "our_stratum": game["our_stratum"],
                "verify_theirs": verify_action_completeness(
                    LOGS / ("game_%s.log" % game["game_id"]), game["their_pid"]),
                "theirs": reach(LOGS / ("game_%s.log" % game["game_id"]), game["their_pid"]),
                "ours": reach(LOGS / ("game_%s.log" % game["game_id"]), game["our_pid"]),
                "vision": game["vision"],
            }
            for game in games
        },
    }
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)
        handle.write("\n")
    print("wrote %s (%d games)" % (destination, len(games)))
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Zero-signal and known-signal dry runs for both adjudicators.

    An adjudicator that has never been shown an input with no signal is a
    suggestion, not a test.  Three checks run here:

    1. k adjudicator on our own side, where the answer is known to be "always 3"
       from ``src/player.cpp`` line 566.  It must report 0 at ``len >= 4``.
    2. k adjudicator on rikka's public slot, an independent side whose answer is
       also expected to be 3+3.  Reported, not asserted.
    3. the action-completeness replay, which is the negative control for the
       walk-back used by ``reach``: a wrong direction table fails it loudly.
    """
    games = _games(args)
    failures = 0
    print("check 1 -- zero-signal: our own k, expected 0 at len>=4 in every game")
    for game in games:
        ours = game["our_k"]
        bad = ours["proven_over_three"] or ours["sum_violations"]
        nonthree = {k: v for k, v in ours["exact_k"].items() if v and k != 3}
        if bad or nonthree:
            failures += 1
            print("  FAIL game %s: len>=4=%d violations=%d non-three=%s" % (
                game["game_id"], ours["proven_over_three"], ours["sum_violations"], nonthree))
    print("  %d/%d games clean" % (len(games) - failures, len(games)))

    print("check 2 -- independent expected-negative: rikka's public slot player47")
    public = [game for game in games if game["their_family"] == "public"]
    over = sum(game["their_k"]["proven_over_three"] for game in public)
    obs = sum(game["their_k"]["observations"] for game in public)
    viol = sum(game["their_k"]["sum_violations"] for game in public)
    print("  games=%d observations=%d len>=4=%d sum_violations=%d" % (
        len(public), obs, over, viol))
    if viol:
        failures += 1
        print("  FAIL: sum-violations must be zero")

    print("check 3 -- known-positive: rikka's targeted builds must show len>=4")
    targeted = [game for game in games if game["their_family"] == "targeted"]
    over_t = sum(game["their_k"]["proven_over_three"] for game in targeted)
    obs_t = sum(game["their_k"]["observations"] for game in targeted)
    print("  games=%d observations=%d len>=4=%d (%.1f%%)" % (
        len(targeted), obs_t, over_t, 100 * over_t / obs_t if obs_t else 0.0))
    if targeted and not over_t:
        failures += 1
        print("  FAIL: known-positive family reported no signal")

    print("check 4 -- action-completeness replay on visible pairs (both sides)")
    pairs = exact = 0
    for game in games:
        path = LOGS / ("game_%s.log" % game["game_id"])
        for pid in (game["their_pid"], game["our_pid"]):
            found = verify_action_completeness(path, pid)
            pairs += found["pairs"]
            exact += found["exact"]
    print("  %d/%d exact (%.2f%%)" % (exact, pairs, 100 * exact / pairs if pairs else 0.0))
    if pairs and exact != pairs:
        failures += 1
        print("  FAIL: walk-back disagrees with recorded positions")

    print()
    print("selftest failures: %d" % failures)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", default="/tmp/rikka/games_all.json",
                        help="platform game index JSON (get_game_list_1 dump)")
    parser.add_argument("--uid", type=int, default=47, help="opponent user_id")
    parser.add_argument("--map", default=None, help="restrict to one map, e.g. map1")
    parser.add_argument("--fold-prefix", default=None, dest="fold_prefix",
                        help="fold our replicate model names sharing this prefix into one stratum")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("roster", cmd_roster), ("income", cmd_income), ("budget", cmd_budget),
        ("reach", cmd_reach), ("styles", cmd_styles), ("burn", cmd_burn),
        ("coverage", cmd_coverage),
        ("selftest", cmd_selftest),
    ):
        sub = subparsers.add_parser(name)
        sub.set_defaults(handler=handler)
    report = subparsers.add_parser("report")
    report.add_argument("--out", default=str(REPORTS / "rikka_strategy.json"))
    report.set_defaults(handler=cmd_report)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
