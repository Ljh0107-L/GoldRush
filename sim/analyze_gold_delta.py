#!/usr/bin/env python3
"""Fog-free per-unit held-gold delta channel for platform logs.

Why this exists
---------------
Positions, actions and pickups in a platform log are fog-filtered: for an
opponent unit they are present in only about a third of unit-observations, so
any trajectory statistic drawn from them is a "near us" subset.  Per-unit
``gold`` is different: it is recorded in 100% of unit-observations whether or
not the unit is visible.  Round-over-round differences of that field therefore
give an *unbiased* per-unit income series for both players.

The channel is complete, not merely unbiased: summing a battlefield's mean
per-unit delta over 2 units x 500 rounds reproduces the head-to-head net score
difference to within a few gold on every battlefield tested.  That makes it the
reference against which fog-filtered trajectory statistics must be reconciled.

It also factors income into the two axes that matter for strategy work::

    mean gold per unit-round = P(unit scores) x mean gold given it scores

``hit`` is the first factor, ``yield_per_hit`` the second.

Traps respected
---------------
* ``start[r].cost == end[r-1].cost`` in 499/499 rounds, and ``start`` phase
  ``actions``/``pickup`` are likewise carry-over copies.  Only ``end`` phase
  values are differenced here.
* Forfeited games end in a row whose only keys are ``round``/``forfeit`` and run
  short of 500 rounds.  Such a row breaks the difference chain rather than being
  silently skipped, so no delta ever spans the gap.
* A single ordinary cell of value ``v <= 10`` yields ``(65*v+99)//100 <= 7``, so
  a unit-round gain of 8 or more cannot come from one ordinary cell.  ``ge8`` is
  reported as a structural floor on "multiple cells or one cell richer than 10";
  disentangling those two requires grid visibility and is not done here.

Build families
--------------
Our own side of an archived game is *not* one strategy: the logs span roughly a
hundred experimental builds, some deliberately crippled.  ``families`` resolves
a build family by stripping the trailing replicate letter from the player name,
and ``--validate`` checks a family's mean score delta against a known value so
an identification can be proven rather than assumed.
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

# Accounts of the two tracked opponents.
ACCOUNTS = {"T-1": "player163", "Tundra": "player57"}

# Build families of the frozen deliverable f18064c, proven by score-delta match
# against src/CHANGELOG.md.  Digit selects the map, trailing letter the replicate.
FROZEN_FAMILIES = {
    ("Tundra", "map1"): "frTu1",
    ("Tundra", "map2"): "frTu2",
    ("Tundra", "map3"): "frTu3",
    ("T-1", "map1"): "t1f1",
    ("T-1", "map2"): "t1f2",
    ("T-1", "map3"): "t1f3",
}

# Reference net-score deltas for those families (src/CHANGELOG.md, n=6 each).
FROZEN_REFERENCE = {
    "frTu1": -219.2, "frTu2": +50.2, "frTu3": +245.8,
    "t1f1": -274.3, "t1f2": -106.7, "t1f3": -104.0,
}

MAX_ORDINARY_PICKUP = (65 * 10 + 99) // 100  # 7


def header(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.loads(handle.readline())


def rounds(path: Path) -> Iterable[Mapping[str, Any]]:
    """Yield round records; forfeit/short rows are yielded as None sentinels."""
    with path.open(encoding="utf-8") as handle:
        handle.readline()
        handle.readline()
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            yield record if ("start" in record and "end" in record) else None


def game_files() -> list[Path]:
    return sorted(path for path in LOGS.glob("game_*.log") if path.is_file())


def family_of(name: str) -> str:
    """Strip the trailing replicate letter, keeping any embedded map digit."""
    return name[:-1] if len(name) > 1 else name


def our_side(head: Mapping[str, Any], account: str) -> tuple[str, int] | None:
    first, second = head.get("player1"), head.get("player2")
    if account == second:
        return str(first), 1
    if account == first:
        return str(second), 2
    return None


def families(account: str) -> Mapping[str, list[tuple[Path, int]]]:
    """Map build family -> [(log path, our player id)] for one opponent."""
    result: dict[str, list[tuple[Path, int]]] = collections.defaultdict(list)
    for path in game_files():
        try:
            head = header(path)
        except (OSError, json.JSONDecodeError):
            continue
        side = our_side(head, account)
        if side is None:
            continue
        name, pid = side
        result[family_of(name)].append((path, pid))
    return result


def unit_deltas(path: Path, our_pid: int) -> Mapping[str, list[int]]:
    """Per-unit end-phase held-gold deltas, split into our side and theirs."""
    previous: dict[int, list[int]] = {}
    series: dict[str, list[int]] = {"ours": [], "theirs": []}
    for record in rounds(path):
        if record is None:          # forfeit / malformed: break the chain
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


def net_delta(path: Path, our_pid: int) -> float | None:
    """Final net-score difference (ours minus theirs) for one game."""
    last = None
    for record in rounds(path):
        if record is not None:
            last = record
    if last is None:
        return None
    scores = {
        int(entry["id"]): int(entry["gold"]) - int(entry["vision_spent"])
        for entry in last["end"]["players"]
    }
    others = [value for pid, value in scores.items() if pid != our_pid]
    if our_pid not in scores or not others:
        return None
    return scores[our_pid] - others[0]


def describe(values: Sequence[int]) -> Mapping[str, float]:
    total = len(values)
    if not total:
        return {}
    gains = [value for value in values if value > 0]
    counts = collections.Counter(values)
    return {
        "n": total,
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if total > 1 else 0.0,
        "se": statistics.stdev(values) / math.sqrt(total) if total > 1 else 0.0,
        "hit": len(gains) / total,
        "yield_per_hit": statistics.fmean(gains) if gains else 0.0,
        "ge6": sum(count for value, count in counts.items() if value >= 6) / total,
        "ge8": sum(count for value, count in counts.items() if value > MAX_ORDINARY_PICKUP) / total,
        "negative": sum(count for value, count in counts.items() if value < 0) / total,
    }


def factor_split(ours: Mapping[str, float], theirs: Mapping[str, float]) -> Mapping[str, float]:
    """Attribute the mean-income ratio to the hit-rate and yield factors."""
    if not ours or not theirs or ours["mean"] <= 0 or theirs["mean"] <= 0:
        return {}
    if not ours["hit"] or not ours["yield_per_hit"]:
        return {}
    hit_ratio = theirs["hit"] / ours["hit"]
    yield_ratio = theirs["yield_per_hit"] / ours["yield_per_hit"]
    total = math.log(theirs["mean"] / ours["mean"])
    split = {"hit_ratio": hit_ratio, "yield_ratio": yield_ratio,
             "mean_ratio": theirs["mean"] / ours["mean"]}
    if abs(total) > 1e-9:
        split["hit_share_of_log_gap"] = math.log(hit_ratio) / total
        split["yield_share_of_log_gap"] = math.log(yield_ratio) / total
    return split


def analyze_frozen() -> Mapping[str, Any]:
    """Per-battlefield fog-free comparison for the frozen build f18064c."""
    battlefields: dict[str, Any] = {}
    pooled: dict[str, list[int]] = {"ours": [], "theirs": []}
    for (opponent, map_name), family in FROZEN_FAMILIES.items():
        account = ACCOUNTS[opponent]
        members = families(account).get(family, [])
        series: dict[str, list[int]] = {"ours": [], "theirs": []}
        score_deltas = []
        for path, pid in members:
            found = unit_deltas(path, pid)
            series["ours"] += found["ours"]
            series["theirs"] += found["theirs"]
            delta = net_delta(path, pid)
            if delta is not None:
                score_deltas.append(delta)
        if not series["ours"]:
            continue
        pooled["ours"] += series["ours"]
        pooled["theirs"] += series["theirs"]
        ours, theirs = describe(series["ours"]), describe(series["theirs"])
        # Reconciliation: the channel should account for the whole score gap.
        implied = (ours["mean"] - theirs["mean"]) * 2 * 500
        observed = statistics.fmean(score_deltas) if score_deltas else None
        battlefields["%s %s" % (opponent, map_name)] = {
            "family": family,
            "games": len(members),
            "ours": ours,
            "theirs": theirs,
            "factor_split": factor_split(ours, theirs),
            "implied_score_delta_from_channel": implied,
            "observed_mean_score_delta": observed,
            "reference_score_delta": FROZEN_REFERENCE.get(family),
            "reconciliation_residual": (implied - observed) if observed is not None else None,
        }
    ours, theirs = describe(pooled["ours"]), describe(pooled["theirs"])
    return {
        "battlefields": battlefields,
        "pooled": {"ours": ours, "theirs": theirs, "factor_split": factor_split(ours, theirs)},
        "channel": {
            "field": "end.players[].units[].gold, differenced round over round",
            "completeness": "per-unit gold is present in 100% of unit-observations, "
                            "including rounds where the unit is invisible",
            "multi_cell_floor": "a unit-round gain above %d cannot come from one "
                                "ordinary cell of value <= 10" % MAX_ORDINARY_PICKUP,
            "forfeit_handling": "rows lacking start/end break the difference chain",
        },
    }


def analyze_validate() -> Mapping[str, Any]:
    """Prove the frozen-build family identification against known score deltas."""
    output = {}
    for (opponent, map_name), family in FROZEN_FAMILIES.items():
        members = families(ACCOUNTS[opponent]).get(family, [])
        deltas = [net_delta(path, pid) for path, pid in members]
        deltas = [value for value in deltas if value is not None]
        expected = FROZEN_REFERENCE[family]
        measured = statistics.fmean(deltas) if deltas else None
        output[family] = {
            "opponent": opponent,
            "map": map_name,
            "games": len(deltas),
            "wins": sum(1 for value in deltas if value > 0),
            "measured_mean_score_delta": measured,
            "changelog_reference": expected,
            "matches": measured is not None and abs(measured - expected) < 0.5,
        }
    output["_verdict"] = (
        "all families match" if all(
            value.get("matches") for key, value in output.items() if not key.startswith("_")
        ) else "MISMATCH - identification not proven"
    )
    return output


def analyze_survey(minimum: int) -> Mapping[str, Any]:
    """List every build family with enough replicates, for future identification."""
    output: dict[str, Any] = {}
    for opponent, account in ACCOUNTS.items():
        rows = []
        for family, members in sorted(families(account).items()):
            if len(members) < minimum:
                continue
            deltas = [net_delta(path, pid) for path, pid in members]
            deltas = [value for value in deltas if value is not None]
            if not deltas:
                continue
            rows.append({
                "family": family,
                "games": len(deltas),
                "wins": sum(1 for value in deltas if value > 0),
                "mean_score_delta": statistics.fmean(deltas),
            })
        output[opponent] = sorted(rows, key=lambda row: row["mean_score_delta"])
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("frozen", help="fog-free per-battlefield comparison for f18064c")
    sub.add_parser("validate", help="prove the f18064c family identification")
    survey = sub.add_parser("survey", help="list build families by replicate count")
    survey.add_argument("--min-games", type=int, default=5)
    args = parser.parse_args(argv)

    if args.command == "frozen":
        result: Mapping[str, Any] = analyze_frozen()
    elif args.command == "validate":
        result = analyze_validate()
    else:
        result = analyze_survey(args.min_games)
    json.dump(result, sys.stdout, indent=2, sort_keys=True, default=float)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
