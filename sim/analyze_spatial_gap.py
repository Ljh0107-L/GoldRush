#!/usr/bin/env python3
"""Compare real champion probe logs with exact-strategy simulator logs."""

from __future__ import annotations

import argparse
from collections import Counter
import glob
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


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summary(values: Iterable[float]) -> dict[str, float | int | None]:
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


def is_r1(position: tuple[int, int]) -> bool:
    return 4 <= position[0] <= 12 and 4 <= position[1] <= 12


def distance(position: tuple[int, int]) -> int:
    return max(abs(position[0] - 8), abs(position[1] - 8))


def player(phase: dict[str, Any], player_id: int = 1) -> dict[str, Any]:
    return next(item for item in phase["players"] if int(item["id"]) == player_id)


def parse_log_bytes(data: bytes, source: str) -> dict[str, Any]:
    lines = data.splitlines()
    metadata = json.loads(lines[0])
    rows = [json.loads(line) for line in lines[2:]]
    return {"source": source, "metadata": metadata, "rows": rows}


def parse_path(path: Path) -> dict[str, Any]:
    return parse_log_bytes(path.read_bytes(), str(path))


def cutoff_profile(logs: list[dict[str, Any]], phase: str, begin: int, end: int) -> dict[str, Any]:
    unit_count = r1 = d2 = d4 = 0
    by_unit = [{"n": 0, "r1": 0, "d2": 0} for _ in range(2)]
    any_r1 = both_r1 = rounds = 0
    exact_anchor = within_anchor2 = 0
    anchors = ((6, 8), (11, 8))
    for log in logs:
        for row in log["rows"]:
            round_number = int(row["round"])
            if not begin <= round_number < end:
                continue
            positions = [tuple(unit["position"]) for unit in player(row[phase])["units"]]
            flags = []
            for unit_index, position in enumerate(positions):
                unit_count += 1
                flag = is_r1(position)
                r1 += flag
                d2 += distance(position) <= 2
                d4 += distance(position) <= 4
                exact_anchor += position == anchors[unit_index]
                within_anchor2 += max(abs(position[0] - anchors[unit_index][0]), abs(position[1] - anchors[unit_index][1])) <= 2
                by_unit[unit_index]["n"] += 1
                by_unit[unit_index]["r1"] += flag
                by_unit[unit_index]["d2"] += distance(position) <= 2
                flags.append(flag)
            rounds += 1
            any_r1 += any(flags)
            both_r1 += all(flags)
    return {
        "rounds": rounds,
        "unit_rounds": unit_count,
        "r1_rate": r1 / unit_count,
        "d_le_2_rate": d2 / unit_count,
        "d_le_4_rate": d4 / unit_count,
        "any_unit_r1_player_round_rate": any_r1 / rounds,
        "both_units_r1_player_round_rate": both_r1 / rounds,
        "exact_anchor_rate": exact_anchor / unit_count,
        "within_anchor_chebyshev2_rate": within_anchor2 / unit_count,
        "by_unit": [
            {
                "unit": index,
                "unit_rounds": item["n"],
                "r1_rate": item["r1"] / item["n"],
                "d_le_2_rate": item["d2"] / item["n"],
            }
            for index, item in enumerate(by_unit)
        ],
    }


def run_lengths(flags: list[bool]) -> list[int]:
    lengths = []
    current = 0
    for flag in flags + [False]:
        if flag:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    return lengths


def behavior_profile(logs: list[dict[str, Any]]) -> dict[str, Any]:
    action_count = stay_actions = unit_rounds = less_three = positive_pickup = pickup_sum = 0
    exits = enters = 0
    outside_runs: list[int] = []
    per_game_r1 = []
    for log in logs:
        game_r1 = game_n = 0
        unit_outside_flags = [[], []]
        for row in log["rows"]:
            start_player = player(row["start"])
            end_player = player(row["end"])
            for unit_index, unit in enumerate(end_player["units"]):
                position = tuple(unit["position"])
                game_n += 1
                game_r1 += is_r1(position)
                unit_outside_flags[unit_index].append(not is_r1(position))
                actions = [int(action) for action in unit.get("actions", [])]
                if actions:
                    action_count += len(actions)
                    stay_actions += sum(action == 4 for action in actions)
                    less_three += sum(action != 4 for action in actions) < 3
                amount = int(unit.get("pickup", 0))
                unit_rounds += 1
                positive_pickup += amount > 0
                pickup_sum += amount
                before = tuple(start_player["units"][unit_index]["position"])
                exits += is_r1(before) and not is_r1(position)
                enters += not is_r1(before) and is_r1(position)
        per_game_r1.append(game_r1 / game_n)
        for flags in unit_outside_flags:
            outside_runs.extend(run_lengths(flags))
    return {
        "unit_rounds": unit_rounds,
        "effective_action4_rate": stay_actions / action_count,
        "less_than_three_move_unit_round_rate": less_three / unit_rounds,
        "positive_pickup_unit_round_rate": positive_pickup / unit_rounds,
        "pickup_per_positive_unit_round": pickup_sum / positive_pickup,
        "center_exit_transitions": exits,
        "center_enter_transitions": enters,
        "outside_run_lengths": summary(outside_runs),
        "per_game_r1_rate": summary(per_game_r1),
    }


def snapshot_profile(logs: list[dict[str, Any]]) -> dict[str, Any]:
    center_generated = []
    outer_generated = []
    center_remaining = []
    center_occupants = []
    own_center = []
    other_center = []
    for log in logs:
        for row in log["rows"]:
            snapshot = row.get("snapshot")
            if snapshot is None:
                continue
            regions = {int(item["id"]): item for item in snapshot["regions"]}
            center = regions[1]
            own = sum(is_r1(tuple(unit["position"])) for unit in player(row["start"])["units"])
            center_generated.append(int(center["gold_generated"]))
            outer_generated.append(sum(int(regions[index]["gold_generated"]) for index in range(2, 6)))
            center_remaining.append(int(center["gold_remaining"]))
            center_occupants.append(int(center["occupants"]))
            own_center.append(own)
            other_center.append(int(center["occupants"]) - own)
    total_generated = sum(center_generated) + sum(outer_generated)
    return {
        "snapshots": len(center_generated),
        "center_generated": summary(center_generated),
        "outer_generated": summary(outer_generated),
        "center_generation_share": sum(center_generated) / total_generated,
        "center_remaining": summary(center_remaining),
        "center_occupants": summary(center_occupants),
        "own_center_occupants": summary(own_center),
        "other_center_occupants": summary(other_center),
    }


def corpus_profile(logs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "games": len(logs),
        "sources": [log["source"] for log in logs],
        "names": [log["metadata"] for log in logs],
        "position_semantics": "player1 own-unit endpoints; one observation per unit per included round",
        "start": {
            "all": cutoff_profile(logs, "start", 0, 500),
            "opening_r_lt_20": cutoff_profile(logs, "start", 0, 20),
            "steady_r_ge_20": cutoff_profile(logs, "start", 20, 500),
            "steady_r_ge_30": cutoff_profile(logs, "start", 30, 500),
        },
        "end": {
            "all": cutoff_profile(logs, "end", 0, 500),
            "opening_r_lt_20": cutoff_profile(logs, "end", 0, 20),
            "steady_r_ge_20": cutoff_profile(logs, "end", 20, 500),
            "steady_r_ge_30": cutoff_profile(logs, "end", 30, 500),
        },
        "behavior": behavior_profile(logs),
        "snapshot": snapshot_profile(logs),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_sim_logs(strategy: Path, games: int, seed_base: int) -> list[dict[str, Any]]:
    from sim.probe.player import Player
    from sim.runner import run_game

    result = []
    for index in range(games):
        game = run_game(
            strategy,
            Player().MoveDecision,
            map_source="map1",
            seed=seed_base + index,
            dispatch="fixed",
            fixed_costs=(0, 1),
            player1_name="champff4",
            player2_name="probeobs",
        )
        result.append(parse_log_bytes(game.log_bytes, "generated:seed=%d" % (seed_base + index)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-glob", default=str(Path(__file__).resolve().parents[1] / "logs" / "opponents" / "_selfplay-champ" / "*.log"))
    parser.add_argument("--sim-glob")
    parser.add_argument("--sim-strategy", type=Path)
    parser.add_argument("--sim-games", type=int, default=10)
    parser.add_argument("--sim-seed-base", type=int, default=2026082900)
    parser.add_argument("--strategy-source-commit", default="ff462756")
    parser.add_argument("--strategy-source-sha256", default="d4526b22f4371e33e17dd3508ef74235e77ffc84ff81966ff0def25e0be013b0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    real_paths = sorted(Path(path) for path in glob.glob(args.real_glob))
    if len(real_paths) != 10:
        raise SystemExit("expected 10 real logs, got %d" % len(real_paths))
    real_logs = [parse_path(path) for path in real_paths]
    if args.sim_strategy:
        sim_logs = generate_sim_logs(args.sim_strategy, args.sim_games, args.sim_seed_base)
        sim_asset = {"path": str(args.sim_strategy), "sha256": sha256(args.sim_strategy)}
    elif args.sim_glob:
        sim_paths = sorted(Path(path) for path in glob.glob(args.sim_glob))
        sim_logs = [parse_path(path) for path in sim_paths]
        sim_asset = {"paths": [str(path) for path in sim_paths]}
    else:
        raise SystemExit("provide --sim-strategy or --sim-glob")

    real = corpus_profile(real_logs)
    simulated = corpus_profile(sim_logs)
    payload = {
        "schema_version": 1,
        "status": "complete",
        "definition": {
            "R1": "central Region 1: row and col both in [4,12] inclusive",
            "d_le_2": "Chebyshev distance from (8,8) <= 2",
            "denominator": "unit-rounds, both player1 units, no visibility filtering",
            "phase": "start and end reported separately",
        },
        "strategy_identity": {
            "real_log_player1": "champff4",
            "source_commit": args.strategy_source_commit,
            "source_sha256": args.strategy_source_sha256,
            "sim_asset": sim_asset,
            "note": "Simulator library is the frozen source compiled for host ABI; prefetch-only calls may be shimmed to no-op without changing decisions.",
        },
        "real": real,
        "simulated_exact_strategy": simulated,
        "key_deltas_sim_minus_real": {
            "end_all_r1_pp": 100 * (simulated["end"]["all"]["r1_rate"] - real["end"]["all"]["r1_rate"]),
            "end_all_d2_pp": 100 * (simulated["end"]["all"]["d_le_2_rate"] - real["end"]["all"]["d_le_2_rate"]),
            "end_steady_r1_pp": 100 * (simulated["end"]["steady_r_ge_20"]["r1_rate"] - real["end"]["steady_r_ge_20"]["r1_rate"]),
            "snapshot_other_center_occupants_mean": simulated["snapshot"]["other_center_occupants"]["mean"] - real["snapshot"]["other_center_occupants"]["mean"],
            "snapshot_center_generation_share_pp": 100 * (simulated["snapshot"]["center_generation_share"] - real["snapshot"]["center_generation_share"]),
        },
        "interpretation_guardrail": {
            "prototype_97_percent_is_same_strategy": False,
            "reason": "97% came from sim/idle_sweep.py's simplified Python anchor policy, not frozen champff4; exact champff4 simulator occupancy is reported separately.",
            "go_no_go": "Do not infer simulator spatial saturation or platform transfer from the simplified policy's absolute occupancy. Use exact strategy binaries and identical metric semantics.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"key_deltas": payload["key_deltas_sim_minus_real"], "real_end": real["end"], "sim_end": simulated["end"], "real_snapshot": real["snapshot"], "sim_snapshot": simulated["snapshot"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
