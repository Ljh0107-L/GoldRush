#!/usr/bin/env python3
"""Paired A/B/C experiment for productive motion in empty central windows."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.raid_bake import ANCHORS  # noqa: E402
from sim.raid_proto import (  # noqa: E402
    ACTION_DELTAS,
    ROUTES3,
    STAY,
    CentralPolicy,
    cell,
    chebyshev,
    local_harvest,
    replay_endpoint,
    shortest_route,
    visible_gold_near,
)
from sim.runner import run_paired  # noqa: E402

DEFAULT_OUTPUT = ROOT / "sim" / "reports" / "idle_sweep.json"
VARIANTS = {
    "stay": ("idle-stay", "stay", "none"),
    "two": ("idle-two-point", "two", "none"),
    "loop": ("idle-four-loop", "loop", "none"),
    "hard": ("center-hard-mask", "stay", "hard"),
    "soft": ("center-first-soft-mask", "stay", "soft"),
    "two-hard": ("two-point-center-mask", "two", "hard"),
    "loop-hard": ("four-loop-center-mask", "loop", "hard"),
}
TWO_POINTS = (
    ((6, 8), (7, 8)),
    ((11, 8), (10, 8)),
)
FOUR_LOOPS = (
    ((6, 8), (7, 8), (7, 9), (6, 9)),
    ((11, 8), (10, 8), (10, 7), (11, 7)),
)


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


def valid_sweep(actions: tuple[int, ...], value: Any, start: tuple[int, int], forbidden: set[tuple[int, int]]) -> bool:
    current = start
    for action in actions:
        delta = ACTION_DELTAS[action]
        current = (current[0] + delta[0], current[1] + delta[1])
        if current in forbidden or int(value.grid[current[0]][current[1]]) == -3:
            return False
    return True


def in_center(position: tuple[int, int]) -> bool:
    return 4 <= position[0] <= 12 and 4 <= position[1] <= 12


def visible_center_gold(value: Any, origin: tuple[int, int]) -> int:
    total = 0
    for row in range(max(4, origin[0] - 2), min(13, origin[0] + 3)):
        for col in range(max(4, origin[1] - 2), min(13, origin[1] + 3)):
            amount = int(value.grid[row][col])
            if amount > 0:
                total += amount
    return total


def center_harvest(
    value: Any,
    rows: tuple[str, ...],
    start: tuple[int, int],
    home: tuple[int, int],
    forbidden: set[tuple[int, int]],
) -> tuple[int, ...]:
    """Three-step local plan scored only by central 9x9 gold."""
    best: tuple[int, tuple[int, ...]] | None = None
    for actions in ROUTES3:
        current = start
        collected: dict[tuple[int, int], int] = {}
        legal = True
        stays = 0
        for action in actions:
            if action == STAY:
                candidate = current
                stays += 1
            else:
                delta = ACTION_DELTAS[action]
                candidate = (current[0] + delta[0], current[1] + delta[1])
            if not (0 <= candidate[0] < 17 and 0 <= candidate[1] < 17):
                legal = False
                break
            observed = int(value.grid[candidate[0]][candidate[1]])
            if rows[candidate[0]][candidate[1]] == "1" or observed == -3 or candidate in forbidden:
                legal = False
                break
            if observed > 0 and in_center(candidate):
                collected[candidate] = observed
            current = candidate
        if not legal:
            continue
        score = sum(collected.values()) * 1000 - chebyshev(current, home) * 7 - stays
        candidate_score = (score, tuple(-action for action in actions))
        if best is None or candidate_score > (best[0], tuple(-action for action in best[1])):
            best = (score, actions)
    return (STAY, STAY, STAY) if best is None else best[1]


class IdlePolicy(CentralPolicy):
    """Same local harvester for all variants; only empty-window motion differs."""

    def __init__(self, map_name: str, rows: tuple[str, ...], variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(variant)
        super().__init__(map_name, rows)
        self.variant = variant
        self.name, self.motion, self.clamp = VARIANTS[variant]
        self.game_requests: list[list[tuple[int, ...]]] = []
        self.requests: list[tuple[int, ...]] = []
        self.idle_sweep_unit_rounds = 0
        self.game_idle_counts: list[int] = []

    def reset(self) -> None:
        super().reset()
        if getattr(self, "requests", None):
            self.game_requests.append(self.requests)
            self.game_idle_counts.append(self.idle_sweep_unit_rounds)
        self.requests = []
        self.idle_sweep_unit_rounds = 0

    def finish(self) -> tuple[list[list[tuple[int, ...]]], list[int]]:
        games = list(self.game_requests)
        counts = list(self.game_idle_counts)
        if self.requests:
            games.append(list(self.requests))
            counts.append(self.idle_sweep_unit_rounds)
        return games, counts

    @staticmethod
    def two_actions(unit_index: int, position: tuple[int, int]) -> tuple[int, int, int] | None:
        first, second = TWO_POINTS[unit_index]
        if position == first:
            action = 1 if unit_index == 0 else 0
        elif position == second:
            action = 0 if unit_index == 0 else 1
        else:
            return None
        return (action, 1 - action, action)

    @staticmethod
    def loop_actions(unit_index: int, position: tuple[int, int]) -> tuple[int, int, int] | None:
        loop = FOUR_LOOPS[unit_index]
        try:
            index = loop.index(position)
        except ValueError:
            return None
        actions = (1, 3, 0, 2) if unit_index == 0 else (0, 2, 1, 3)
        return tuple(actions[(index + offset) & 3] for offset in range(3))  # type: ignore[return-value]

    def idle_actions(
        self,
        value: Any,
        unit_index: int,
        position: tuple[int, int],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, ...]:
        anchor = ANCHORS[unit_index]
        if self.motion == "stay":
            route = shortest_route(self.rows, position, anchor, forbidden)
            return (route + (STAY, STAY, STAY))[:3]
        actions = (
            self.two_actions(unit_index, position)
            if self.motion == "two"
            else self.loop_actions(unit_index, position)
        )
        if actions is not None and valid_sweep(actions, value, position, forbidden):
            self.idle_sweep_unit_rounds += 1
            return actions
        route = shortest_route(self.rows, position, anchor, forbidden)
        return (route + (STAY, STAY, STAY))[:3]

    def unit_actions(
        self,
        value: Any,
        unit_index: int,
        position: tuple[int, int],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, ...]:
        anchor = ANCHORS[unit_index]
        center_gold = visible_center_gold(value, position)
        all_gold = visible_gold_near(value, position)
        if self.clamp in {"hard", "soft"} and center_gold > 0:
            return center_harvest(value, self.rows, position, anchor, forbidden)
        if self.clamp == "soft" and all_gold > 0:
            return local_harvest(value, self.rows, position, anchor, forbidden)
        if self.clamp == "none" and all_gold > 0:
            return local_harvest(value, self.rows, position, anchor, forbidden)
        return self.idle_actions(value, unit_index, position, forbidden)

    def __call__(self, value: Any) -> tuple[int, ...]:
        if int(value.round) == 0:
            self.reset()
        positions = [cell(item) for item in value.my_units]
        blocks = self.actor_blocks(value)
        actions0 = self.unit_actions(value, 0, positions[0], blocks | {positions[1]})
        endpoint0 = replay_endpoint(positions[0], actions0)
        actions1 = self.unit_actions(value, 1, positions[1], blocks | {endpoint0})
        endpoint1 = replay_endpoint(positions[1], actions1)
        self.previous_expected = [endpoint0, endpoint1]
        requested = actions0 + actions1
        self.requests.append(requested)
        return requested + (3, 0, 0)


def load_rows(map_name: str) -> tuple[str, ...]:
    maps = json.loads((ROOT / "sim" / "maps.json").read_text(encoding="utf-8"))["maps"]
    return tuple(maps[map_name]["rows"])


def player_from_phase(phase: dict[str, Any], player_id: int) -> dict[str, Any]:
    return next(player for player in phase["players"] if int(player["id"]) == player_id)


def visible_window(rows: tuple[str, ...], positions: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    result = set()
    for center in positions:
        for row in range(max(0, center[0] - 2), min(17, center[0] + 3)):
            for col in range(max(0, center[1] - 2), min(17, center[1] + 3)):
                if rows[row][col] != "1":
                    result.add((row, col))
    return result


def behavior_metrics(
    result: Any,
    strategy_name: str,
    requests: list[tuple[int, ...]],
    rows: tuple[str, ...],
    idle_sweep_unit_rounds: int,
) -> dict[str, Any]:
    metadata = json.loads(result.log_bytes.splitlines()[0])
    player_id = 1 if metadata["player1"] == strategy_name else 2
    records = [json.loads(line) for line in result.log_bytes.splitlines()[2:]]
    if len(records) != 500 or len(requests) != 500:
        raise AssertionError((len(records), len(requests)))

    zero_pickup = 0
    unit_rounds = 0
    unit_positions: list[list[tuple[int, int]]] = [[], []]
    mutual_blocks = 0
    requested_move_blocked = 0
    effective_stay_actions = 0
    less_than_three_move_unit_rounds = 0
    region1_unit_rounds = 0
    center_d2_unit_rounds = 0

    for round_index, record in enumerate(records):
        start_player = player_from_phase(record["start"], player_id)
        end_player = player_from_phase(record["end"], player_id)
        requested = requests[round_index]
        start_positions = [tuple(unit["position"]) for unit in start_player["units"]]
        end_positions = [tuple(unit["position"]) for unit in end_player["units"]]
        for unit_index, unit in enumerate(end_player["units"]):
            zero_pickup += int(int(unit["pickup"]) == 0)
            unit_rounds += 1
            position = end_positions[unit_index]
            unit_positions[unit_index].append(position)
            region1_unit_rounds += int(in_center(position))
            center_d2_unit_rounds += int(chebyshev(position, (8, 8)) <= 2)
            effective = tuple(int(action) for action in unit["actions"])
            effective_stay_actions += sum(action == STAY for action in effective)
            less_than_three_move_unit_rounds += int(sum(action != STAY for action in effective) < 3)

        # Player order is fixed to 0 in all three policies.  Reconstruct effective
        # paths and identify a blocked requested move whose destination is exactly
        # the other friendly unit's then-current cell.
        first_current = start_positions[0]
        second_current = start_positions[1]
        first_effective = tuple(int(action) for action in end_player["units"][0]["actions"])
        for requested_action, effective_action in zip(requested[:3], first_effective):
            destination = (
                first_current[0] + ACTION_DELTAS[requested_action][0],
                first_current[1] + ACTION_DELTAS[requested_action][1],
            )
            if requested_action != STAY and effective_action == STAY:
                requested_move_blocked += 1
                mutual_blocks += int(destination == second_current)
            first_current = (
                first_current[0] + ACTION_DELTAS[effective_action][0],
                first_current[1] + ACTION_DELTAS[effective_action][1],
            )
        second_effective = tuple(int(action) for action in end_player["units"][1]["actions"])
        for requested_action, effective_action in zip(requested[3:], second_effective):
            destination = (
                second_current[0] + ACTION_DELTAS[requested_action][0],
                second_current[1] + ACTION_DELTAS[requested_action][1],
            )
            if requested_action != STAY and effective_action == STAY:
                requested_move_blocked += 1
                mutual_blocks += int(destination == first_current)
            second_current = (
                second_current[0] + ACTION_DELTAS[effective_action][0],
                second_current[1] + ACTION_DELTAS[effective_action][1],
            )

    rolling_coverage: list[int] = []
    for unit_index in range(2):
        positions = unit_positions[unit_index]
        for end in range(4, len(positions)):
            rolling_coverage.append(len(visible_window(rows, positions[end - 4 : end + 1])))

    return {
        "strategy": strategy_name,
        "zero_pickup_unit_rounds": zero_pickup,
        "unit_rounds": unit_rounds,
        "rolling_5_round_window_coverage": rolling_coverage,
        "mutual_blocked_steps": mutual_blocks,
        "all_requested_move_blocked_steps": requested_move_blocked,
        "effective_stay_actions": effective_stay_actions,
        "less_than_three_move_unit_rounds": less_than_three_move_unit_rounds,
        "idle_sweep_unit_rounds": idle_sweep_unit_rounds,
        "region1_unit_rounds": region1_unit_rounds,
        "center_d2_unit_rounds": center_d2_unit_rounds,
    }


def baseline_pickup_origin(result: Any, strategy_name: str) -> dict[str, int]:
    """Exact pickup-cell accounting for the P1 leg, before NPCs or P2 move."""
    metadata = json.loads(result.log_bytes.splitlines()[0])
    if metadata["player1"] != strategy_name:
        raise AssertionError("pickup-origin diagnostic requires baseline as P1")
    outside_amount = outside_cells = total_amount = total_cells = 0
    for line in result.log_bytes.splitlines()[2:]:
        record = json.loads(line)
        start_player = player_from_phase(record["start"], 1)
        end_player = player_from_phase(record["end"], 1)
        board = [list(row) for row in record["start"]["grid"]]
        reconstructed = 0
        for unit_index in (0, 1):
            current = tuple(start_player["units"][unit_index]["position"])
            for action in end_player["units"][unit_index]["actions"]:
                action = int(action)
                if action == STAY:
                    continue
                current = (
                    current[0] + ACTION_DELTAS[action][0],
                    current[1] + ACTION_DELTAS[action][1],
                )
                value = int(board[current[0]][current[1]])
                if value > 0:
                    amount = (65 * value + 99) // 100
                    board[current[0]][current[1]] = value - amount
                    reconstructed += amount
                    total_amount += amount
                    total_cells += 1
                    if not in_center(current):
                        outside_amount += amount
                        outside_cells += 1
        metadata_pickup = sum(int(unit["pickup"]) for unit in end_player["units"])
        if reconstructed != metadata_pickup:
            raise AssertionError((record["round"], reconstructed, metadata_pickup))
    return {
        "total_amount": total_amount,
        "total_pickup_cells": total_cells,
        "outside_amount": outside_amount,
        "outside_pickup_cells": outside_cells,
    }


def one_comparison(variant_mode: str, map_name: str, seed: int) -> dict[str, Any]:
    rows = load_rows(map_name)
    baseline = IdlePolicy(map_name, rows, "stay")
    variant = IdlePolicy(map_name, rows, variant_mode)
    paired = run_paired(
        baseline,
        variant,
        map_source=map_name,
        seed=seed,
        dispatch="fixed",
        fixed_costs=(0, 1),
        name_a=baseline.name,
        name_b=variant.name,
    )
    baseline_requests, baseline_idle = baseline.finish()
    variant_requests, variant_idle = variant.finish()
    if len(baseline_requests) != 2 or len(variant_requests) != 2:
        raise AssertionError((len(baseline_requests), len(variant_requests)))

    legs = [paired.a_as_p1, paired.b_as_p1]
    scores = []
    behaviors = []
    for leg_index, leg in enumerate(legs):
        by_name = {value["name"]: value for value in leg.summary["players"].values()}
        baseline_score = int(by_name[baseline.name]["net_gold"])
        variant_score = int(by_name[variant.name]["net_gold"])
        scores.append({"baseline": baseline_score, "variant": variant_score, "delta": variant_score - baseline_score})
        behaviors.append(behavior_metrics(leg, baseline.name, baseline_requests[leg_index], rows, baseline_idle[leg_index]))
        behaviors.append(behavior_metrics(leg, variant.name, variant_requests[leg_index], rows, variant_idle[leg_index]))
    return {
        "comparison": "%s-minus-stay" % variant_mode,
        "variant": variant.name,
        "map": map_name,
        "seed": seed,
        "scenario_digest": paired.scenario_digest,
        "seat_averaged_delta": statistics.fmean(item["delta"] for item in scores),
        "legs": scores,
        "behaviors": behaviors,
        "baseline_p1_pickup_origin": baseline_pickup_origin(paired.a_as_p1, baseline.name),
    }


def aggregate_behavior(items: list[dict[str, Any]]) -> dict[str, Any]:
    zero = sum(item["zero_pickup_unit_rounds"] for item in items)
    unit_rounds = sum(item["unit_rounds"] for item in items)
    coverage = [value for item in items for value in item["rolling_5_round_window_coverage"]]
    less_three = sum(item["less_than_three_move_unit_rounds"] for item in items)
    return {
        "games": len(items),
        "unit_rounds": unit_rounds,
        "zero_pickup_unit_rounds": zero,
        "zero_pickup_unit_round_rate": zero / unit_rounds,
        "rolling_5_round_window_coverage": summarize(coverage),
        "mutual_blocked_steps": sum(item["mutual_blocked_steps"] for item in items),
        "all_requested_move_blocked_steps": sum(item["all_requested_move_blocked_steps"] for item in items),
        "effective_stay_action_rate": sum(item["effective_stay_actions"] for item in items) / (unit_rounds * 3),
        "less_than_three_move_unit_rounds": less_three,
        "less_than_three_move_unit_round_rate": less_three / unit_rounds,
        "idle_sweep_unit_rounds": sum(item["idle_sweep_unit_rounds"] for item in items),
        "region1_occupancy_rate": sum(item["region1_unit_rounds"] for item in items) / unit_rounds,
        "center_d2_occupancy_rate": sum(item["center_d2_unit_rounds"] for item in items) / unit_rounds,
    }


def aggregate_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    behavior_items = [item for result in results for item in result["behaviors"]]
    strategies = sorted({item["strategy"] for item in behavior_items})
    origins = [result["baseline_p1_pickup_origin"] for result in results]
    total_amount = sum(item["total_amount"] for item in origins)
    total_cells = sum(item["total_pickup_cells"] for item in origins)
    outside_amount = sum(item["outside_amount"] for item in origins)
    outside_cells = sum(item["outside_pickup_cells"] for item in origins)
    return {
        "pair_count": len(results),
        "game_count": len(results) * 2,
        "seat_averaged_income_delta": summarize(result["seat_averaged_delta"] for result in results),
        "leg_income_delta": summarize(leg["delta"] for result in results for leg in result["legs"]),
        "behavior": {
            strategy: aggregate_behavior([item for item in behavior_items if item["strategy"] == strategy])
            for strategy in strategies
        },
        "baseline_p1_pickup_origin": {
            "games": len(origins),
            "total_amount": total_amount,
            "total_pickup_cells": total_cells,
            "outside_amount": outside_amount,
            "outside_amount_share": outside_amount / total_amount if total_amount else None,
            "outside_pickup_cells": outside_cells,
            "outside_pickup_cell_share": outside_cells / total_cells if total_cells else None,
            "outside_average_amount": outside_amount / outside_cells if outside_cells else None,
        },
    }


def run_experiment(pairs_per_map: int, workers: int, variants: tuple[str, ...]) -> dict[str, Any]:
    if not variants or any(variant == "stay" or variant not in VARIANTS for variant in variants):
        raise ValueError("variants must be non-stay keys from %s" % sorted(VARIANTS))
    jobs = [
        (variant, map_name, 2026081900 + map_index * 1000 + pair_index)
        for variant in variants
        for map_index, map_name in enumerate(("map1", "map2"))
        for pair_index in range(pairs_per_map)
    ]
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one_comparison, *job): job for job in jobs}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: (item["comparison"], item["map"], item["seed"]))
    comparisons = {}
    for variant in variants:
        selected = [result for result in results if result["comparison"] == "%s-minus-stay" % variant]
        comparisons["%s-minus-stay" % variant] = {
            "aggregate": aggregate_comparison(selected),
            "by_map": {
                map_name: aggregate_comparison([result for result in selected if result["map"] == map_name])
                for map_name in ("map1", "map2")
            },
        }
    compact_pairs = [
        {key: result[key] for key in ("comparison", "variant", "map", "seed", "scenario_digest", "seat_averaged_delta", "legs")}
        for result in results
    ]
    return {
        "schema_version": 1,
        "status": "complete",
        "design": {
            "anchors": [list(anchor) for anchor in ANCHORS],
            "A": "same local 5x5 harvester; empty window returns to anchor and STAYs",
            "B": "same harvester; empty window alternates anchor and one inward neighbor with three LUT moves",
            "C": "same harvester; empty window cycles a four-cell 2x2 loop with three LUT moves",
            "D": "hard center mask: score only 9x9 center gold; if none, return/idle",
            "D_prime": "soft center-first mask: prefer center gold; allow outer gold only when no center gold is visible",
            "variants_run": list(variants),
            "rolling_coverage": "for each unit and each 5-round end-position window, union of radius-2 non-wall cells",
            "mutual_block": "requested non-STAY step became effective STAY and requested destination equaled friendly unit's then-current cell",
            "dispatch": "fixed; two seat-swapped legs per seed",
            "pairs_per_map_per_comparison": pairs_per_map,
            "pairs_per_comparison": pairs_per_map * 2,
            "total_pairs": len(results),
        },
        "bias": {
            "source": "NPC model is too greedy and too central",
            "direction": "conservative for B/C central sweeps: it suppresses all central strategies, so a local uplift should understate platform upside",
        },
        "comparisons": comparisons,
        "pairs": compact_pairs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs-per-map", type=int, default=15)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--variants", nargs="+", default=["hard", "soft", "two", "loop"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_experiment(args.pairs_per_map, args.workers, tuple(args.variants))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        name: value["aggregate"]
        for name, value in report["comparisons"].items()
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
