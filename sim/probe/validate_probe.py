#!/usr/bin/env python3
"""Run the observation probe for 500 rounds on map1 and map2."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.abi import validate_output  # noqa: E402
from sim.probe.player import Player  # noqa: E402
from sim.runner import run_game  # noqa: E402


DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))


def cell(value):
    return (int(value.row), int(value.col))


def chebyshev(left, right):
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


class MobilePatrol:
    """Deterministic moving target used only by this local smoke test."""

    def __init__(self):
        self.waypoints = (
            ((4, 5), (12, 11)),
            ((5, 12), (11, 4)),
            ((11, 12), (5, 4)),
            ((12, 5), (4, 11)),
        )

    @staticmethod
    def route(start, target, grid, other):
        position = start
        actions = []
        for _ in range(3):
            candidates = []
            for action, delta in enumerate(DELTAS):
                destination = (position[0] + delta[0], position[1] + delta[1])
                if not (0 <= destination[0] < 17 and 0 <= destination[1] < 17):
                    continue
                value = int(grid[destination[0]][destination[1]])
                if value in (-5, -3, -1) or destination == other:
                    continue
                candidates.append((chebyshev(destination, target), action, destination))
            if not candidates:
                actions.append(4)
                continue
            _distance, action, position = min(candidates)
            actions.append(action)
        return tuple(actions), position

    def __call__(self, game_input):
        phase = (int(game_input.round) // 30) % len(self.waypoints)
        targets = self.waypoints[phase]
        positions = [cell(game_input.my_units[index]) for index in range(2)]
        route0, endpoint0 = self.route(positions[0], targets[0], game_input.grid, positions[1])
        route1, _endpoint1 = self.route(positions[1], targets[1], game_input.grid, endpoint0)
        return route0 + route1 + (3, 0, 0)


class MeasuredProbe:
    def __init__(self):
        self.player = Player()
        self.times_ns = []
        self.illegal_outputs = 0
        self.vp2_outputs = 0
        self.visible_distances = []

    def __call__(self, game_input):
        own = [cell(game_input.my_units[index]) for index in range(2)]
        visible = []
        for enemy in game_input.visible_enemies:
            if enemy is not None:
                visible.append(cell(enemy))
        if visible:
            self.visible_distances.append(
                min(chebyshev(friend, enemy) for friend in own for enemy in visible)
            )
        started = perf_counter_ns()
        output = self.player.MoveDecision(game_input)
        self.times_ns.append(perf_counter_ns() - started)
        try:
            decision = validate_output(output)
        except Exception:
            self.illegal_outputs += 1
            raise
        self.vp2_outputs += int(decision.vp == 2)
        return output


def validate_map(map_name, seed):
    probe = MeasuredProbe()
    result = run_game(
        probe,
        MobilePatrol(),
        map_source=map_name,
        seed=seed,
        dispatch="fixed",
        fixed_costs=(1_000_000, 1),
        player1_name="probeobs",
        player2_name="mobile-patrol",
    )
    rounds = int(result.summary["rounds"])
    probe_score = result.summary["players"]["1"]
    assert rounds == 500
    assert len(probe.times_ns) == rounds
    assert probe.illegal_outputs == 0
    assert probe.player._fallbacks == 0
    assert probe.vp2_outputs == rounds
    assert probe_score["vision_spent"] == rounds * 3
    return {
        "map": map_name,
        "seed": seed,
        "rounds": rounds,
        "illegal_outputs": probe.illegal_outputs,
        "fallbacks": probe.player._fallbacks,
        "vp2_outputs": probe.vp2_outputs,
        "vision_spent": probe_score["vision_spent"],
        "visible_rounds": probe.player._visible_rounds,
        "opponent_visible_rate": probe.player._visible_rounds / rounds,
        "visible_enemy_unit_observations": probe.player._visible_units,
        "nearest_visible_enemy_distance_p50": (
            nearest_rank(probe.visible_distances, 0.50) if probe.visible_distances else None
        ),
        "decision_time_ns": {
            "p50": nearest_rank(probe.times_ns, 0.50),
            "p90": nearest_rank(probe.times_ns, 0.90),
            "p99": nearest_rank(probe.times_ns, 0.99),
            "max": max(probe.times_ns),
        },
    }


def main():
    reports = [validate_map("map1", 90210), validate_map("map2", 90211)]
    aggregate_times = []
    for report in reports:
        aggregate_times.append(report["decision_time_ns"]["max"])
    payload = {
        "status": "pass",
        "maps": reports,
        "total_rounds": sum(report["rounds"] for report in reports),
        "total_illegal_outputs": sum(report["illegal_outputs"] for report in reports),
        "total_fallbacks": sum(report["fallbacks"] for report in reports),
        "all_vp2": all(report["vp2_outputs"] == report["rounds"] for report in reports),
        "all_vision_fees_3_per_round": all(
            report["vision_spent"] == report["rounds"] * 3 for report in reports
        ),
        "worst_decision_time_ns": max(aggregate_times),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
