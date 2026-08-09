#!/usr/bin/env python3
"""Paired economic prototype for snapshot-triggered outer hotspot raids.

The policies are intentionally Python callables and do not modify simulator
mechanics.  Baseline and raid share the same local 5x5 harvest controller; the
only treatment is unit1's cold snapshot-triggered expedition state machine.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import product
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.raid_bake import ANCHORS, ACTION_DELTAS as CARDINAL_DELTAS, bake  # noqa: E402
from sim.runner import run_paired  # noqa: E402

ACTION_DELTAS = CARDINAL_DELTAS + ((0, 0),)
STAY = 4
THRESHOLD = 40
RAID_MAX_ROUNDS = 12
ROUTES3 = tuple(product(range(5), repeat=3))
DEFAULT_OUTPUT = ROOT / "sim" / "reports" / "raid.json"


def cell(value: Any) -> tuple[int, int]:
    return (int(value.row), int(value.col))


def chebyshev(left: tuple[int, int], right: tuple[int, int]) -> int:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


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


def shortest_route(
    rows: tuple[str, ...],
    start: tuple[int, int],
    target: tuple[int, int],
    forbidden: set[tuple[int, int]],
) -> tuple[int, ...]:
    if start == target:
        return ()
    queue = [start]
    previous: dict[tuple[int, int], tuple[tuple[int, int], int] | None] = {start: None}
    for current in queue:
        for action, delta in enumerate(ACTION_DELTAS[:4]):
            nxt = (current[0] + delta[0], current[1] + delta[1])
            if not (0 <= nxt[0] < 17 and 0 <= nxt[1] < 17):
                continue
            if rows[nxt[0]][nxt[1]] == "1" or nxt in forbidden or nxt in previous:
                continue
            previous[nxt] = (current, action)
            if nxt == target:
                queue.clear()
                break
            queue.append(nxt)
        else:
            continue
        break
    if target not in previous:
        return (STAY, STAY, STAY)
    reverse: list[int] = []
    current = target
    while previous[current] is not None:
        parent, action = previous[current]  # type: ignore[misc]
        reverse.append(action)
        current = parent
    return tuple(reversed(reverse))


def replay_endpoint(start: tuple[int, int], actions: Iterable[int]) -> tuple[int, int]:
    current = start
    for action in actions:
        delta = ACTION_DELTAS[action]
        current = (current[0] + delta[0], current[1] + delta[1])
    return current


def visible_gold_near(value: Any, origin: tuple[int, int]) -> int:
    total = 0
    for row in range(max(0, origin[0] - 2), min(17, origin[0] + 3)):
        for col in range(max(0, origin[1] - 2), min(17, origin[1] + 3)):
            amount = int(value.grid[row][col])
            if amount > 0:
                total += amount
    return total


def local_harvest(
    value: Any,
    rows: tuple[str, ...],
    start: tuple[int, int],
    home: tuple[int, int],
    forbidden: set[tuple[int, int]],
) -> tuple[int, ...]:
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
            if observed > 0:
                collected[candidate] = observed
            current = candidate
        if not legal:
            continue
        score = sum(collected.values()) * 1000 - chebyshev(current, home) * 7 - stays
        candidate_score = (score, tuple(-action for action in actions))
        if best is None or candidate_score > (best[0], tuple(-action for action in best[1])):
            best = (score, actions)
    return (STAY, STAY, STAY) if best is None else best[1]


@dataclass
class Expedition:
    trigger_round: int
    arm: int
    generated: int
    occupants: int
    target: tuple[int, int]
    status: str = "travel"
    first_station_round: int | None = None
    end_round: int | None = None
    end_reason: str | None = None
    travel_rounds: int = 0
    harvest_rounds: int = 0
    return_rounds: int = 0
    pickup_travel: int = 0
    pickup_station: int = 0
    pickup_return: int = 0
    losses: int = 0
    endpoint_deviation_rounds: int = 0
    visible_interference_rounds: int = 0
    stations_reached_mask: int = 0

    def record(self) -> dict[str, Any]:
        return {
            "trigger_round": self.trigger_round,
            "arm": self.arm,
            "snapshot_gold_generated": self.generated,
            "snapshot_occupants": self.occupants,
            "status_at_end": self.status,
            "first_station_round": self.first_station_round,
            "arrival_rounds": None if self.first_station_round is None else self.first_station_round - self.trigger_round,
            "end_round": self.end_round,
            "end_reason": self.end_reason,
            "travel_rounds": self.travel_rounds,
            "harvest_rounds": self.harvest_rounds,
            "return_rounds": self.return_rounds,
            "pickup_travel": self.pickup_travel,
            "pickup_station": self.pickup_station,
            "pickup_return": self.pickup_return,
            "pickup_total": self.pickup_travel + self.pickup_station + self.pickup_return,
            "losses": self.losses,
            "endpoint_deviation_rounds": self.endpoint_deviation_rounds,
            "visible_interference_rounds": self.visible_interference_rounds,
            "stations_reached": bin(self.stations_reached_mask).count("1"),
        }


class CentralPolicy:
    """Two anchored 5x5 local harvesters; shared control for A and B."""

    name = "central-baseline"

    def __init__(self, map_name: str, rows: tuple[str, ...]) -> None:
        self.map_name = map_name
        self.rows = rows
        self.previous_expected: list[tuple[int, int] | None] = [None, None]

    def reset(self) -> None:
        self.previous_expected = [None, None]

    @staticmethod
    def actor_blocks(value: Any) -> set[tuple[int, int]]:
        result = {cell(position) for position in value.visible_enemies if position is not None}
        return result

    def central_actions(
        self,
        value: Any,
        unit_index: int,
        position: tuple[int, int],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, ...]:
        anchor = ANCHORS[unit_index]
        if visible_gold_near(value, position) > 0 and chebyshev(position, anchor) <= 3:
            return local_harvest(value, self.rows, position, anchor, forbidden)
        route = shortest_route(self.rows, position, anchor, forbidden)
        return (route + (STAY, STAY, STAY))[:3]

    def __call__(self, value: Any) -> tuple[int, ...]:
        if int(value.round) == 0:
            self.reset()
        positions = [cell(item) for item in value.my_units]
        blocks = self.actor_blocks(value)
        actions0 = self.central_actions(value, 0, positions[0], blocks | {positions[1]})
        endpoint0 = replay_endpoint(positions[0], actions0)
        actions1 = self.central_actions(value, 1, positions[1], blocks | {endpoint0})
        self.previous_expected = [endpoint0, replay_endpoint(positions[1], actions1)]
        return actions0 + actions1 + (3, 0, 0)


class RaidPolicy(CentralPolicy):
    """Central unit0 plus snapshot-triggered, two-station unit1 raids."""

    name = "snapshot-raid"

    def __init__(
        self,
        map_name: str,
        rows: tuple[str, ...],
        arm_stations: dict[int, tuple[tuple[int, int], tuple[int, int]]],
    ) -> None:
        super().__init__(map_name, rows)
        self.arm_stations = arm_stations
        self.expeditions: list[Expedition] = []
        self.active: Expedition | None = None
        self.previous_gold = 0
        self.previous_mode: str | None = None
        self.game_records: list[list[dict[str, Any]]] = []

    def reset(self) -> None:
        super().reset()
        if getattr(self, "active", None) is not None:
            self._close(499, "game_end")
        if hasattr(self, "expeditions") and self.expeditions:
            self.game_records.append([item.record() for item in self.expeditions])
        self.expeditions = []
        self.active = None
        self.previous_gold = 0
        self.previous_mode = None

    def finalize(self) -> list[dict[str, Any]]:
        if self.active is not None:
            self._close(499, "game_end")
        current = [item.record() for item in self.expeditions]
        return current

    def _close(self, round_number: int, reason: str) -> None:
        if self.active is None:
            return
        self.active.end_round = round_number
        self.active.end_reason = reason
        self.active = None

    def _start(self, round_number: int, arm: int, generated: int, occupants: int, position: tuple[int, int]) -> None:
        if self.active is not None:
            self._close(round_number, "retargeted_by_new_pulse")
        pair = self.arm_stations[arm]
        target = min(pair, key=lambda station: (chebyshev(position, station), station))
        self.active = Expedition(round_number, arm, generated, occupants, target)
        self.expeditions.append(self.active)

    def _account_previous(self, value: Any, position: tuple[int, int]) -> None:
        current_gold = int(value.my_units_gold[1])
        delta = current_gold - self.previous_gold
        if self.active is not None and self.previous_mode is not None:
            if delta >= 0:
                if self.previous_mode == "travel":
                    self.active.pickup_travel += delta
                elif self.previous_mode == "harvest":
                    self.active.pickup_station += delta
                else:
                    self.active.pickup_return += delta
            else:
                self.active.losses += -delta
            expected = self.previous_expected[1]
            if expected is not None and expected != position:
                self.active.endpoint_deviation_rounds += 1
        self.previous_gold = current_gold

    def _pulse(self, value: Any) -> tuple[int, int, int] | None:
        if value.snapshot is None:
            return None
        candidates = [
            (int(stat.gold_generated), -int(stat.occupants), int(stat.id))
            for stat in value.snapshot.regions
            if 2 <= int(stat.id) <= 5 and int(stat.gold_generated) >= THRESHOLD
        ]
        if not candidates:
            return None
        generated, negative_occupants, arm = max(candidates)
        return arm, generated, -negative_occupants

    def raid_actions(
        self,
        value: Any,
        position: tuple[int, int],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, ...]:
        active = self.active
        if active is None:
            return self.central_actions(value, 1, position, forbidden)

        pair = self.arm_stations[active.arm]
        for index, station in enumerate(pair):
            if position == station:
                active.stations_reached_mask |= 1 << index
        if active.status == "travel" and position == active.target:
            active.status = "harvest"
            active.first_station_round = int(value.round)

        age = int(value.round) - active.trigger_round
        if active.status in {"travel", "harvest"} and age >= RAID_MAX_ROUNDS:
            active.status = "return"
        if active.status == "return" and position == ANCHORS[1]:
            self._close(int(value.round), "returned_after_timeout")
            return self.central_actions(value, 1, position, forbidden)

        if active.status == "travel":
            active.travel_rounds += 1
            route = shortest_route(self.rows, position, active.target, forbidden)
            return (route + (STAY, STAY, STAY))[:3]

        if active.status == "return":
            active.return_rounds += 1
            route = shortest_route(self.rows, position, ANCHORS[1], forbidden)
            return (route + (STAY, STAY, STAY))[:3]

        active.harvest_rounds += 1
        local_gold = visible_gold_near(value, position)
        if local_gold > 0:
            return local_harvest(value, self.rows, position, active.target, forbidden)

        missing = [station for index, station in enumerate(pair) if not (active.stations_reached_mask & (1 << index))]
        if missing:
            active.target = min(missing, key=lambda station: (chebyshev(position, station), station))
            active.status = "travel"
            route = shortest_route(self.rows, position, active.target, forbidden)
            return (route + (STAY, STAY, STAY))[:3]
        active.status = "return"
        route = shortest_route(self.rows, position, ANCHORS[1], forbidden)
        return (route + (STAY, STAY, STAY))[:3]

    def __call__(self, value: Any) -> tuple[int, ...]:
        if int(value.round) == 0:
            self.reset()
            self.previous_gold = int(value.my_units_gold[1])
        positions = [cell(item) for item in value.my_units]
        self._account_previous(value, positions[1])

        pulse = self._pulse(value)
        if pulse is not None:
            self._start(int(value.round), pulse[0], pulse[1], pulse[2], positions[1])

        blocks = self.actor_blocks(value)
        if self.active is not None:
            nearby_enemy = any(chebyshev(positions[1], other) <= 2 for other in blocks)
            nearby_npc = any(chebyshev(positions[1], npc_position.cell) <= 2 for _npc_id, npc_position in value.visible_npcs)
            self.active.visible_interference_rounds += int(nearby_enemy or nearby_npc)

        actions0 = self.central_actions(value, 0, positions[0], blocks | {positions[1]})
        endpoint0 = replay_endpoint(positions[0], actions0)
        self.previous_mode = None if self.active is None else self.active.status
        actions1 = self.raid_actions(value, positions[1], blocks | {endpoint0})
        self.previous_expected = [endpoint0, replay_endpoint(positions[1], actions1)]
        return actions0 + actions1 + (3, 0, 0)


def load_policy_data(map_name: str) -> tuple[tuple[str, ...], dict[int, tuple[tuple[int, int], tuple[int, int]]]]:
    maps = json.loads((ROOT / "sim" / "maps.json").read_text(encoding="utf-8"))["maps"]
    definition = maps[map_name]
    if definition["limited"]:
        raise ValueError("%s has no hotspot metadata" % map_name)
    rows = tuple(definition["rows"])
    baked = bake()["maps"][map_name]["arms"]
    arm_stations = {
        int(arm): tuple(tuple(cell_) for cell_ in value["double"]["stations"])
        for arm, value in baked.items()
    }
    return rows, arm_stations  # type: ignore[return-value]


def one_pair(map_name: str, seed: int) -> dict[str, Any]:
    rows, arm_stations = load_policy_data(map_name)
    baseline = CentralPolicy(map_name, rows)
    raid = RaidPolicy(map_name, rows, arm_stations)
    paired = run_paired(
        baseline,
        raid,
        map_source=map_name,
        seed=seed,
        dispatch="fixed",
        fixed_costs=(0, 1),
        name_a=CentralPolicy.name,
        name_b=RaidPolicy.name,
    )
    legs = paired.summary["legs"]
    deltas = []
    scores = []
    for leg in legs:
        by_name = {value["name"]: value for value in leg["players"].values()}
        baseline_score = int(by_name[CentralPolicy.name]["net_gold"])
        raid_score = int(by_name[RaidPolicy.name]["net_gold"])
        deltas.append(raid_score - baseline_score)
        scores.append({"baseline": baseline_score, "raid": raid_score, "delta": raid_score - baseline_score})
    current = raid.finalize()
    games = list(raid.game_records) + [current]
    return {
        "map": map_name,
        "seed": seed,
        "scenario_digest": paired.scenario_digest,
        "seat_averaged_delta": statistics.fmean(deltas),
        "legs": scores,
        "expeditions": games,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    flat_expeditions = [expedition for result in results for game in result["expeditions"] for expedition in game]
    arrivals = [item["arrival_rounds"] for item in flat_expeditions if item["arrival_rounds"] is not None]
    active_rounds = sum(
        item["travel_rounds"] + item["harvest_rounds"] + item["return_rounds"]
        for item in flat_expeditions
    )
    game_count = len(results) * 2
    return {
        "pair_count": len(results),
        "game_count": game_count,
        "active_rounds": active_rounds,
        "active_round_fraction": active_rounds / (game_count * 500) if game_count else None,
        "seat_averaged_income_delta": summarize(result["seat_averaged_delta"] for result in results),
        "leg_income_delta": summarize(leg["delta"] for result in results for leg in result["legs"]),
        "expeditions": {
            "count": len(flat_expeditions),
            "arrived_count": len(arrivals),
            "arrival_rounds": summarize(arrivals),
            "pickup_total": summarize(item["pickup_total"] for item in flat_expeditions),
            "pickup_at_station": summarize(item["pickup_station"] for item in flat_expeditions),
            "pickup_in_travel": summarize(item["pickup_travel"] for item in flat_expeditions),
            "pickup_on_return": summarize(item["pickup_return"] for item in flat_expeditions),
            "losses": summarize(item["losses"] for item in flat_expeditions),
            "travel_rounds": summarize(item["travel_rounds"] for item in flat_expeditions),
            "harvest_rounds": summarize(item["harvest_rounds"] for item in flat_expeditions),
            "return_rounds": summarize(item["return_rounds"] for item in flat_expeditions),
            "snapshot_occupants": summarize(item["snapshot_occupants"] for item in flat_expeditions),
            "endpoint_deviation_rounds": summarize(item["endpoint_deviation_rounds"] for item in flat_expeditions),
            "visible_interference_rounds": summarize(item["visible_interference_rounds"] for item in flat_expeditions),
            "station_coverage": {
                "zero": sum(item["stations_reached"] == 0 for item in flat_expeditions),
                "one": sum(item["stations_reached"] == 1 for item in flat_expeditions),
                "two": sum(item["stations_reached"] == 2 for item in flat_expeditions),
            },
            "end_reasons": {
                reason: sum(item["end_reason"] == reason for item in flat_expeditions)
                for reason in sorted({item["end_reason"] for item in flat_expeditions})
            },
        },
    }


def run_experiment(pairs_per_map: int, workers: int) -> dict[str, Any]:
    jobs = [
        (map_name, 2026080900 + map_index * 1000 + pair_index)
        for map_index, map_name in enumerate(("map1", "map2"))
        for pair_index in range(pairs_per_map)
    ]
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one_pair, *job): job for job in jobs}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: (item["map"], item["seed"]))
    by_map = {
        map_name: aggregate([result for result in results if result["map"] == map_name])
        for map_name in ("map1", "map2")
    }
    compact_results = [
        {
            "map": result["map"],
            "seed": result["seed"],
            "scenario_digest": result["scenario_digest"],
            "seat_averaged_delta": result["seat_averaged_delta"],
            "legs": result["legs"],
        }
        for result in results
    ]
    return {
        "schema_version": 1,
        "status": "complete",
        "baked_tables": bake(),
        "design": {
            "baseline": "two anchor-biased 5x5 local harvesters at (6,8) and (11,8)",
            "treatment": "unit0 baseline; unit1 triggers on outer RegionStat.gold_generated>=40, visits baked two-station cover, then returns",
            "threshold": THRESHOLD,
            "raid_max_rounds": RAID_MAX_ROUNDS,
            "dispatch": "fixed; two seat-swapped legs per seed",
            "pairs_per_map": pairs_per_map,
            "total_pairs": len(results),
            "map3": "not run: no token-2 metadata, central-only policy",
        },
        "biases": [
            {
                "source": "NPC model is too greedy and too central",
                "direction": "makes central residence worse, so raid-minus-baseline delta is optimistically biased upward",
            },
            {
                "source": "fitted outer hotspot share 0.553 versus observed truth 0.541",
                "direction": "small additional optimistic bias toward raids; fit is close but not exact",
            },
        ],
        "aggregate": aggregate(results),
        "by_map": by_map,
        "pairs": compact_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs-per-map", type=int, default=15)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.pairs_per_map < 1:
        raise SystemExit("--pairs-per-map must be positive")
    report = run_experiment(args.pairs_per_map, args.workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"aggregate": report["aggregate"], "by_map": report["by_map"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
