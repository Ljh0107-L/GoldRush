#!/usr/bin/env python3
"""Seat-swapped simulator prior for disabling u1 parasitic hunt hijacking."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.runner import run_paired  # noqa: E402

BASELINE_NAME = "anchor-with-hunt"
NOHUNT_NAME = "anchor-no-hunt"


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(values: Iterable[float]) -> dict[str, float | int | None]:
    sequence = list(values)
    return {
        "n": len(sequence),
        "median": statistics.median(sequence) if sequence else None,
        "mean": statistics.fmean(sequence) if sequence else None,
        "p10": percentile(sequence, 0.10),
        "p90": percentile(sequence, 0.90),
        "min": min(sequence) if sequence else None,
        "max": max(sequence) if sequence else None,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_r1(position: tuple[int, int]) -> bool:
    return 4 <= position[0] <= 12 and 4 <= position[1] <= 12


def behavior(result: Any, strategy_name: str) -> dict[str, Any]:
    lines = result.log_bytes.splitlines()
    metadata = json.loads(lines[0])
    player_id = 1 if metadata["player1"] == strategy_name else 2
    unit_rounds = [0, 0]
    r1 = [0, 0]
    d2 = [0, 0]
    zero_pickup = [0, 0]
    pickup = [0, 0]
    action4 = [0, 0]
    actions = [0, 0]
    for line in lines[2:]:
        row = json.loads(line)
        owner = next(player for player in row["end"]["players"] if int(player["id"]) == player_id)
        for unit_index, unit in enumerate(owner["units"]):
            position = tuple(unit["position"])
            amount = int(unit["pickup"])
            unit_rounds[unit_index] += 1
            r1[unit_index] += is_r1(position)
            d2[unit_index] += max(abs(position[0] - 8), abs(position[1] - 8)) <= 2
            zero_pickup[unit_index] += amount == 0
            pickup[unit_index] += amount
            effective = [int(action) for action in unit["actions"]]
            action4[unit_index] += sum(action == 4 for action in effective)
            actions[unit_index] += len(effective)
    return {
        "strategy": strategy_name,
        "unit_rounds": unit_rounds,
        "r1": r1,
        "d2": d2,
        "zero_pickup": zero_pickup,
        "pickup": pickup,
        "action4": action4,
        "actions": actions,
    }


def one_pair(baseline: str, no_hunt: str, seed: int) -> dict[str, Any]:
    paired = run_paired(
        baseline,
        no_hunt,
        map_source="map1",
        seed=seed,
        dispatch="fixed",
        fixed_costs=(0, 1),
        name_a=BASELINE_NAME,
        name_b=NOHUNT_NAME,
    )
    legs = [paired.a_as_p1, paired.b_as_p1]
    scores = []
    behaviors = []
    for leg in legs:
        by_name = {player["name"]: player for player in leg.summary["players"].values()}
        base_score = int(by_name[BASELINE_NAME]["net_gold"])
        no_hunt_score = int(by_name[NOHUNT_NAME]["net_gold"])
        scores.append({"baseline": base_score, "no_hunt": no_hunt_score, "delta": no_hunt_score - base_score})
        behaviors.append(behavior(leg, BASELINE_NAME))
        behaviors.append(behavior(leg, NOHUNT_NAME))
    return {
        "seed": seed,
        "scenario_digest": paired.scenario_digest,
        "seat_averaged_delta": statistics.fmean(item["delta"] for item in scores),
        "legs": scores,
        "behaviors": behaviors,
    }


def aggregate_behavior(items: list[dict[str, Any]]) -> dict[str, Any]:
    unit_rounds = [sum(item["unit_rounds"][unit] for item in items) for unit in range(2)]
    r1 = [sum(item["r1"][unit] for item in items) for unit in range(2)]
    d2 = [sum(item["d2"][unit] for item in items) for unit in range(2)]
    zero = [sum(item["zero_pickup"][unit] for item in items) for unit in range(2)]
    pickup = [sum(item["pickup"][unit] for item in items) for unit in range(2)]
    action4 = [sum(item["action4"][unit] for item in items) for unit in range(2)]
    actions = [sum(item["actions"][unit] for item in items) for unit in range(2)]
    total_rounds = sum(unit_rounds)
    return {
        "games": len(items),
        "unit_rounds": sum(unit_rounds),
        "r1_rate": sum(r1) / total_rounds,
        "d_le_2_rate": sum(d2) / total_rounds,
        "zero_pickup_unit_round_rate": sum(zero) / total_rounds,
        "pickup_per_unit_round": sum(pickup) / total_rounds,
        "effective_action4_rate": sum(action4) / sum(actions),
        "by_unit": [
            {
                "unit": unit,
                "unit_rounds": unit_rounds[unit],
                "r1_rate": r1[unit] / unit_rounds[unit],
                "d_le_2_rate": d2[unit] / unit_rounds[unit],
                "zero_pickup_unit_round_rate": zero[unit] / unit_rounds[unit],
                "pickup_per_unit_round": pickup[unit] / unit_rounds[unit],
                "effective_action4_rate": action4[unit] / actions[unit],
            }
            for unit in range(2)
        ],
    }


def run(baseline: Path, no_hunt: Path, pairs: int, workers: int, seed_base: int) -> dict[str, Any]:
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(one_pair, str(baseline), str(no_hunt), seed_base + index): index
            for index in range(pairs)
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["seed"])
    behavior_items = [item for result in results for item in result["behaviors"]]
    compact_pairs = [
        {key: result[key] for key in ("seed", "scenario_digest", "seat_averaged_delta", "legs")}
        for result in results
    ]
    return {
        "schema_version": 1,
        "status": "complete",
        "design": {
            "source_commit": "da210b8",
            "map": "map1",
            "pairs": pairs,
            "games": pairs * 2,
            "dispatch": "fixed; P1 faster in each leg; algorithms swap seats",
            "seed_base": seed_base,
            "patch": "analysis-only /tmp source copy replaces hunt live expression with int live = 0; repository src/player.cpp untouched",
            "bias": "simulator NPCs over-consume central gold (remaining 27 vs real 39), so returning u1 to center has a conservative local payoff",
        },
        "artifacts": {
            "baseline": {"path": str(baseline), "sha256": sha256(baseline)},
            "no_hunt": {"path": str(no_hunt), "sha256": sha256(no_hunt)},
        },
        "seat_averaged_income_delta_no_hunt_minus_baseline": summarize(result["seat_averaged_delta"] for result in results),
        "leg_income_delta": summarize(leg["delta"] for result in results for leg in result["legs"]),
        "behavior": {
            name: aggregate_behavior([item for item in behavior_items if item["strategy"] == name])
            for name in (BASELINE_NAME, NOHUNT_NAME)
        },
        "pairs": compact_pairs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--no-hunt", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=2026083000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.baseline, args.no_hunt, args.pairs, args.workers, args.seed_base)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("seat_averaged_income_delta_no_hunt_minus_baseline", "behavior")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
