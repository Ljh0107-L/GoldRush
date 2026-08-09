#!/usr/bin/env python3
"""Calibrate GoldRush gold and bomb generation from full-information logs.

Only Python's standard library is used.  The extractor compares each round's
start grid with the preceding end grid.  This is safe for these logs because
terrain/gold has render precedence: an actor on gold is rendered as that gold,
while -2/-4 therefore denotes an actor on empty floor.  Snapshot totals and
intra-round ground-flow conservation are used as exhaustive cross-checks.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

GRID_SIZE = 17
BOMB_PERIOD = 20


def region_id(row: int, col: int) -> int:
    """Return the user-specified windmill region id (1 is central)."""
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if 0 <= row <= 3 and 0 <= col <= 12:
        return 2
    if 4 <= row <= 16 and 0 <= col <= 3:
        return 3
    if 13 <= row <= 16 and 4 <= col <= 16:
        return 4
    if 0 <= row <= 12 and 13 <= col <= 16:
        return 5
    raise ValueError("coordinate outside 17x17 grid: %r" % ((row, col),))


def load_log(path: pathlib.Path) -> Tuple[dict, list, List[dict]]:
    with path.open("r", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    if len(lines) < 3:
        raise ValueError("%s is not a full-information log" % path)
    metadata = json.loads(lines[0])
    header = json.loads(lines[1])
    rounds = [json.loads(line) for line in lines[2:]]
    if len(header) != GRID_SIZE or any(len(row) != GRID_SIZE for row in header):
        raise ValueError("%s has a non-17x17 header" % path)
    if [entry.get("round") for entry in rounds] != list(range(len(rounds))):
        raise ValueError("%s does not contain contiguous zero-based rounds" % path)
    return metadata, header, rounds


def positive(value: int) -> int:
    return value if value > 0 else 0


def histogram(values: Iterable[int]) -> collections.Counter:
    return collections.Counter(values)


def json_hist(counter: Mapping[Any, Any]) -> Dict[str, Any]:
    def key_order(item: Tuple[Any, Any]) -> Tuple[int, Any]:
        key = item[0]
        try:
            return (0, int(key))
        except (TypeError, ValueError):
            return (1, str(key))

    return {str(key): value for key, value in sorted(counter.items(), key=key_order)}


def rounded(value: float, digits: int = 4) -> float:
    return round(value, digits)


def expected_uniform(total: int, low: int, high: int) -> Dict[str, float]:
    expectation = total / float(high - low + 1)
    return {str(value): round(expectation, 2) for value in range(low, high + 1)}


def expected_poisson(total: int, lam: float, maximum: int) -> Dict[str, float]:
    probability = math.exp(-lam)
    probabilities = [probability]
    for value in range(1, maximum + 1):
        probability *= lam / value
        probabilities.append(probability)
    result = {str(value): round(total * probabilities[value], 2) for value in range(maximum + 1)}
    result["%d+" % (maximum + 1)] = round(total * max(0.0, 1.0 - sum(probabilities)), 2)
    return result


def expected_binomial_mixture(sample_sizes: Sequence[int], probability: float, maximum: int) -> Dict[str, float]:
    expected = [0.0] * (maximum + 1)
    overflow = 0.0
    for sample_size in sample_sizes:
        for successes in range(sample_size + 1):
            term = (
                math.comb(sample_size, successes)
                * probability ** successes
                * (1.0 - probability) ** (sample_size - successes)
            )
            if successes <= maximum:
                expected[successes] += term
            else:
                overflow += term
    result = {str(value): round(count, 2) for value, count in enumerate(expected)}
    result["%d+" % (maximum + 1)] = round(overflow, 2)
    return result


def pearson_chi_square(observed: Mapping[int, int], expected: Mapping[int, float]) -> float:
    score = 0.0
    for key in set(observed) | set(expected):
        exp = float(expected.get(key, 0.0))
        if exp > 0.0:
            score += (float(observed.get(key, 0)) - exp) ** 2 / exp
    return rounded(score)


def actor_positions(phase: dict) -> Tuple[set, set]:
    players = {
        tuple(unit["position"])
        for player in phase["players"]
        for unit in player["units"]
    }
    npcs = {tuple(npc["position"]) for npc in phase["npcs"]}
    return players, npcs


def make_event(round_number: int, previous_grid: List[List[int]], start_grid: List[List[int]]) -> dict:
    by_region = {region: [] for region in range(1, 6)}
    cells = []
    negative_deltas = 0
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            delta = positive(start_grid[row][col]) - positive(previous_grid[row][col])
            if delta < 0:
                negative_deltas += 1
            elif delta > 0:
                region = region_id(row, col)
                by_region[region].append(delta)
                cells.append((row, col, region, delta))
    return {
        "round": round_number,
        "cells": cells,
        "by_region": by_region,
        "negative_deltas": negative_deltas,
    }


def snapshot_covered_rounds(snapshot_round: int, first_snapshot: bool) -> range:
    # Empirically, the snapshot delivered at s includes the current start's
    # generation.  The first snapshot also includes round-0 initial seeding.
    return range(0, snapshot_round + 1) if first_snapshot else range(snapshot_round - 4, snapshot_round + 1)


def calibrate(paths: Sequence[pathlib.Path]) -> dict:
    games = []
    central_regular_counts: List[int] = []
    central_regular_values: List[int] = []
    central_regular_totals: List[int] = []
    central_opening_counts: List[int] = []
    central_opening_values: List[int] = []
    central_opening_totals: List[int] = []
    central_all_counts: List[int] = []
    central_all_values: List[int] = []
    outer_events: List[dict] = []
    outer_hotspot_by_input: List[dict] = []
    outer_waits: List[int] = []
    outer_intervals: List[int] = []
    outer_first_rounds: List[int] = []
    outer_right_censored: List[int] = []
    bomb_waves: List[dict] = []
    bomb_eligible_sizes: List[int] = []
    bomb_exclusions = collections.Counter()
    header2_exclusions = collections.Counter()
    snapshot_generation_residuals: List[int] = []
    snapshot_remaining_residuals: List[int] = []
    snapshot_covered = 0
    tail_uncovered = []
    ground_flow_failures = 0
    actor_render = collections.Counter()
    non_wave_bomb_set_changes = 0
    bombs_on_walls = 0
    wave_resampling = collections.Counter()
    fixed_header_exposure = collections.Counter()
    total_rounds = 0
    total_snapshots = 0
    total_generation_negative_cells = 0

    for path in paths:
        metadata, header, rounds = load_log(path)
        if not rounds:
            raise ValueError("%s contains no rounds" % path)
        total_rounds += len(rounds)
        walls = {(row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE) if header[row][col] == "1"}
        header2 = {(row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE) if header[row][col] == "2"}
        floor = {(row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE)} - walls
        if set(value for row in header for value in row) - {"0", "1", "2"}:
            raise ValueError("%s has unexpected header values" % path)

        zero_grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        previous_grid = zero_grid
        events = []
        game_outer = []
        game_ground_failures = 0
        game_nonwave_changes = 0

        for index, entry in enumerate(rounds):
            start = entry["start"]
            end = entry["end"]
            event = make_event(entry["round"], previous_grid, start["grid"])
            events.append(event)
            total_generation_negative_cells += event["negative_deltas"]

            central_values = event["by_region"][1]
            central_all_counts.append(len(central_values))
            central_all_values.extend(central_values)
            if index == 0:
                central_opening_counts.append(len(central_values))
                central_opening_values.extend(central_values)
                central_opening_totals.append(sum(central_values))
            else:
                central_regular_counts.append(len(central_values))
                central_regular_values.extend(central_values)
                central_regular_totals.append(sum(central_values))

            outer_values = [
                value
                for region in range(2, 6)
                for value in event["by_region"][region]
            ]
            if outer_values:
                region_totals = {region: sum(event["by_region"][region]) for region in range(2, 6)}
                maximum = max(region_totals.values())
                rich_regions = [region for region, amount in region_totals.items() if amount == maximum]
                rich_region = rich_regions[0] if len(rich_regions) == 1 else None
                outer_event = {
                    "file": str(path.resolve()),
                    "round": entry["round"],
                    "values": outer_values,
                    "cells": [cell for cell in event["cells"] if cell[2] > 1],
                    "token2_cells": [
                        cell for cell in event["cells"] if cell[2] > 1 and (cell[0], cell[1]) in header2
                    ],
                    "by_region": {region: list(event["by_region"][region]) for region in range(2, 6)},
                    "region_totals": region_totals,
                    "rich_region": rich_region,
                }
                outer_events.append(outer_event)
                game_outer.append(outer_event)

            start_ground = sum(positive(value) for row in start["grid"] for value in row)
            end_ground = sum(positive(value) for row in end["grid"] for value in row)
            pickups = sum(
                unit["pickup"]
                for player in end["players"]
                for unit in player["units"]
            ) + sum(npc["pickup"] for npc in end["npcs"])
            if start_ground - end_ground != pickups:
                ground_flow_failures += 1
                game_ground_failures += 1

            for phase_name in ("start", "end"):
                phase = entry[phase_name]
                players, npcs = actor_positions(phase)
                for position in players | npcs:
                    value = phase["grid"][position[0]][position[1]]
                    if value > 0:
                        actor_render["actor_position_rendered_as_gold"] += 1
                    elif position in npcs and value == -4:
                        actor_render["empty_actor_position_rendered_as_npc"] += 1
                    elif position in players and value == -2:
                        actor_render["empty_actor_position_rendered_as_player"] += 1
                    else:
                        actor_render["unexpected_actor_render"] += 1

            start_bombs = {
                (row, col)
                for row in range(GRID_SIZE)
                for col in range(GRID_SIZE)
                if start["grid"][row][col] == -3
            }
            bombs_on_walls += len(start_bombs & walls)
            if index > 0:
                prior_end_bombs = {
                    (row, col)
                    for row in range(GRID_SIZE)
                    for col in range(GRID_SIZE)
                    if rounds[index - 1]["end"]["grid"][row][col] == -3
                }
                if entry["round"] % BOMB_PERIOD:
                    changes = len(start_bombs ^ prior_end_bombs)
                    non_wave_bomb_set_changes += changes
                    game_nonwave_changes += changes
                else:
                    wave_resampling["old_bombs_before_wave"] += len(prior_end_bombs)
                    wave_resampling["spawned_after_wave"] += len(start_bombs)
                    wave_resampling["same_coordinate_after_resample"] += len(start_bombs & prior_end_bombs)
                    wave_resampling["old_coordinates_removed"] += len(prior_end_bombs - start_bombs)
                    wave_resampling["new_coordinates_added"] += len(start_bombs - prior_end_bombs)

            if entry["round"] % BOMB_PERIOD == 0:
                players, npcs = actor_positions(start)
                actors = players | npcs
                gold = {position for position in floor if start["grid"][position[0]][position[1]] > 0}
                eligible = floor - gold - actors
                if not start_bombs <= eligible:
                    raise ValueError("bomb rendered on an ineligible cell in %s round %d" % (path, entry["round"]))

                categories = collections.Counter()
                header_categories = collections.Counter()
                for population, counter in ((floor, categories), (header2, header_categories)):
                    for position in population:
                        has_gold = position in gold
                        has_player = position in players
                        has_npc = position in npcs
                        if has_gold and (has_player or has_npc):
                            counter["gold_and_actor"] += 1
                        elif has_gold:
                            counter["gold"] += 1
                        elif has_player and has_npc:
                            counter["player_and_npc"] += 1
                        elif has_player:
                            counter["player"] += 1
                        elif has_npc:
                            counter["npc"] += 1
                        else:
                            counter["eligible"] += 1
                bomb_exclusions.update(categories)
                header2_exclusions.update(header_categories)
                bomb_eligible_sizes.append(len(eligible))
                fixed_header_exposure["eligible"] += len(eligible & header2)
                fixed_header_exposure["spawned"] += len(start_bombs & header2)
                fixed_header_exposure["off_header2_eligible"] += len(eligible - header2)
                fixed_header_exposure["off_header2_spawned"] += len(start_bombs - header2)
                bomb_waves.append(
                    {
                        "file": str(path.resolve()),
                        "round": entry["round"],
                        "floor_candidates": len(floor),
                        "header2_hotspot_cells": len(header2),
                        "header2_eligible": len(eligible & header2),
                        "header2_spawned": len(start_bombs & header2),
                        "eligible": len(eligible),
                        "spawned": len(start_bombs),
                        "excluded": {key: categories[key] for key in sorted(categories) if key != "eligible"},
                    }
                )

            previous_grid = end["grid"]

        snapshot_rounds = []
        covered_rounds = set()
        first_snapshot = True
        for entry in rounds:
            snapshot = entry.get("snapshot")
            if snapshot is None:
                continue
            total_snapshots += 1
            snapshot_rounds.append(entry["round"])
            covered = snapshot_covered_rounds(entry["round"], first_snapshot)
            first_snapshot = False
            covered_rounds.update(covered)
            by_id = {region["id"]: region for region in snapshot["regions"]}
            for region in range(1, 6):
                extracted = sum(
                    sum(events[round_number]["by_region"][region])
                    for round_number in covered
                )
                snapshot_generation_residuals.append(by_id[region]["gold_generated"] - extracted)
                remaining = sum(
                    positive(entry["start"]["grid"][row][col])
                    for row in range(GRID_SIZE)
                    for col in range(GRID_SIZE)
                    if region_id(row, col) == region
                )
                snapshot_remaining_residuals.append(by_id[region]["gold_remaining"] - remaining)
        snapshot_covered += len(covered_rounds)
        uncovered = sorted(set(range(len(rounds))) - covered_rounds)
        tail_uncovered.extend({"file": str(path.resolve()), "round": value} for value in uncovered)

        event_rounds = [event["round"] for event in game_outer]
        if event_rounds:
            outer_first_rounds.append(event_rounds[0])
            outer_waits.append(event_rounds[0])
            for previous, current in zip(event_rounds, event_rounds[1:]):
                outer_intervals.append(current - previous)
                outer_waits.append(current - previous)
            outer_right_censored.append(len(rounds) - event_rounds[-1])

        outer_floor = {position for position in floor if region_id(*position) > 1}
        non_hotspot_outer = outer_floor - header2
        location_counts = collections.Counter(
            (row, col)
            for event in game_outer
            for row, col, _region, _value in event["cells"]
        )
        token2_placements = sum(location_counts[position] for position in header2)
        non_token2_placements = sum(location_counts[position] for position in non_hotspot_outer)
        token2_value = sum(
            value
            for event in game_outer
            for row, col, _region, value in event["cells"]
            if (row, col) in header2
        )
        non_token2_value = sum(sum(event["values"]) for event in game_outer) - token2_value
        ratio_numerator = token2_placements * len(non_hotspot_outer)
        ratio_denominator = non_token2_placements * len(header2)
        ratio_gcd = math.gcd(ratio_numerator, ratio_denominator)
        hotspot_summary = {
            "path": str(path.resolve()),
            "outer_spawned_cell_placements": token2_placements + non_token2_placements,
            "token2_placements": token2_placements,
            "non_token2_placements": non_token2_placements,
            "token2_placement_share": {
                "numerator": token2_placements,
                "denominator": token2_placements + non_token2_placements,
                "rounded": rounded(token2_placements / float(token2_placements + non_token2_placements), 4),
            },
            "token2_outer_cells": len(header2),
            "non_token2_outer_traversable_cells": len(non_hotspot_outer),
            "token2_cells_used": sum(location_counts[position] > 0 for position in header2),
            "token2_cell_placement_count_histogram": json_hist(histogram(location_counts[position] for position in header2)),
            "token2_generated_value": token2_value,
            "non_token2_generated_value": non_token2_value,
            "empirical_per_cell_placement_ratio_token2_over_non_token2": {
                "exact_fraction": "%d/%d" % (ratio_numerator // ratio_gcd, ratio_denominator // ratio_gcd),
                "rounded": rounded(ratio_numerator / float(ratio_denominator), 4),
            },
            "token2_cells_overlapping_central_region": sum(region_id(*position) == 1 for position in header2),
        }
        outer_hotspot_by_input.append(hotspot_summary)

        games.append(
            {
                "path": str(path.resolve()),
                "metadata": metadata,
                "rounds": len(rounds),
                "snapshots": len(snapshot_rounds),
                "snapshot_rounds": snapshot_rounds,
                "header_histogram": json_hist(histogram(value for row in header for value in row)),
                "central_generated_value": sum(sum(event["by_region"][1]) for event in events),
                "outer_event_count": len(game_outer),
                "outer_event_rounds": event_rounds,
                "bomb_wave_count": sum(1 for entry in rounds if entry["round"] % BOMB_PERIOD == 0),
                "ground_flow_failures": game_ground_failures,
                "non_wave_bomb_set_changes": game_nonwave_changes,
                "snapshot_uncovered_rounds": uncovered,
            }
        )

    # Mark whether every outer event was constrained by a snapshot.  Coverage is
    # per file, so use the per-game uncovered lists rather than round number alone.
    uncovered_keys = {(entry["file"], entry["round"]) for entry in tail_uncovered}
    outer_uncovered = sum((event["file"], event["round"]) in uncovered_keys for event in outer_events)

    hotspot_token2_placements = sum(item["token2_placements"] for item in outer_hotspot_by_input)
    hotspot_non_placements = sum(item["non_token2_placements"] for item in outer_hotspot_by_input)
    hotspot_token2_cells = sum(item["token2_outer_cells"] for item in outer_hotspot_by_input)
    hotspot_non_cells = sum(item["non_token2_outer_traversable_cells"] for item in outer_hotspot_by_input)
    hotspot_ratio_numerator = hotspot_token2_placements * hotspot_non_cells
    hotspot_ratio_denominator = hotspot_non_placements * hotspot_token2_cells
    hotspot_ratio_gcd = math.gcd(hotspot_ratio_numerator, hotspot_ratio_denominator)
    hotspot_aggregate = {
        "outer_spawned_cell_placements": hotspot_token2_placements + hotspot_non_placements,
        "token2_placements": hotspot_token2_placements,
        "non_token2_placements": hotspot_non_placements,
        "token2_placement_share": {
            "numerator": hotspot_token2_placements,
            "denominator": hotspot_token2_placements + hotspot_non_placements,
            "rounded": rounded(
                hotspot_token2_placements / float(hotspot_token2_placements + hotspot_non_placements), 4
            ),
        },
        "token2_cell_game_exposures": hotspot_token2_cells,
        "non_token2_outer_traversable_cell_game_exposures": hotspot_non_cells,
        "token2_cells_used": sum(item["token2_cells_used"] for item in outer_hotspot_by_input),
        "token2_generated_value": sum(item["token2_generated_value"] for item in outer_hotspot_by_input),
        "non_token2_generated_value": sum(item["non_token2_generated_value"] for item in outer_hotspot_by_input),
        "empirical_per_cell_placement_ratio_token2_over_non_token2": {
            "exact_fraction": "%d/%d"
            % (hotspot_ratio_numerator // hotspot_ratio_gcd, hotspot_ratio_denominator // hotspot_ratio_gcd),
            "rounded": rounded(hotspot_ratio_numerator / float(hotspot_ratio_denominator), 4),
        },
        "token2_cells_overlapping_central_region": sum(
            item["token2_cells_overlapping_central_region"] for item in outer_hotspot_by_input
        ),
    }

    central_count_hist = histogram(central_regular_counts)
    central_value_hist = histogram(central_regular_values)
    central_lambda = sum(central_regular_counts) / float(len(central_regular_counts))
    poisson_expected_numeric = {}
    probability = math.exp(-central_lambda)
    for value in range(max(central_count_hist) + 1):
        if value:
            probability *= central_lambda / value
        poisson_expected_numeric[value] = len(central_regular_counts) * probability

    central_uniform_expected_numeric = {
        value: len(central_regular_values) / 10.0 for value in range(1, 11)
    }

    rich_events = [event for event in outer_events if event["rich_region"] is not None]
    rich_region_values = [event["rich_region"] for event in rich_events]
    rich_totals = [event["region_totals"][event["rich_region"]] for event in rich_events]
    rich_counts = [len(event["by_region"][event["rich_region"]]) for event in rich_events]
    rich_cell_values = [
        value
        for event in rich_events
        for value in event["by_region"][event["rich_region"]]
    ]
    ordinary_counts = [
        sum(len(values) for region, values in event["by_region"].items() if region != event["rich_region"])
        for event in rich_events
    ]
    ordinary_values = [
        value
        for event in rich_events
        for region, values in event["by_region"].items()
        if region != event["rich_region"]
        for value in values
    ]
    rich_near_even = sum(
        max(event["by_region"][event["rich_region"]])
        - min(event["by_region"][event["rich_region"]])
        <= 1
        for event in rich_events
    )
    hotspot_aggregate["dominant_rich_region_placements"] = sum(rich_counts)
    hotspot_aggregate["dominant_rich_region_placements_on_token2"] = sum(
        cell[2] == event["rich_region"]
        for event in rich_events
        for cell in event["token2_cells"]
    )

    interval_expected_numeric = {value: len(outer_waits) / 9.0 for value in range(8, 17)}
    interval_observed = histogram(outer_waits)
    region_expected_numeric = {value: len(rich_events) / 4.0 for value in range(2, 6)}
    region_observed = histogram(rich_region_values)

    bomb_spawn_counts = [wave["spawned"] for wave in bomb_waves]
    bomb_spawn_total = sum(bomb_spawn_counts)
    bomb_eligible_total = sum(bomb_eligible_sizes)
    bomb_probability = bomb_spawn_total / float(bomb_eligible_total)
    bomb_expected = expected_binomial_mixture(bomb_eligible_sizes, bomb_probability, 30)

    report = {
        "schema_version": 1,
        "status_legend": {
            "confirmed": "directly observed and/or exhaustively checked against conservation and snapshots",
            "fitted": "descriptive statistical form fitted to these three logs; not an official rule",
            "unknown": "not identifiable from 1500 rounds without assuming a latent generator",
        },
        "inputs": {
            "files": [str(path.resolve()) for path in paths],
            "game_count": len(games),
            "round_count": total_rounds,
            "games": games,
        },
        "region_definition": {
            "status": "confirmed",
            "coordinate_base": 0,
            "regions": {
                "1_central": {"rows": [4, 12], "cols": [4, 12], "cells": 81},
                "2_top": {"rows": [0, 3], "cols": [0, 12], "cells": 52},
                "3_left": {"rows": [4, 16], "cols": [0, 3], "cells": 52},
                "4_bottom": {"rows": [13, 16], "cols": [4, 16], "cells": 52},
                "5_right": {"rows": [0, 12], "cols": [13, 16], "cells": 52},
            },
            "partition_cell_count": 289,
        },
        "extraction": {
            "status": "confirmed",
            "method": "positive(start[r]) - positive(end[r-1]) per cell; round 0 uses an all-zero pre-grid",
            "render_precedence": "positive gold/bomb/wall is rendered over actors; -2/-4 therefore means empty floor under an actor",
            "actor_render_observations": json_hist(actor_render),
            "ground_flow_equation": "sum(start positive)-sum(end positive)=player pickups+npc pickups",
            "ground_flow_rounds_checked": total_rounds,
            "ground_flow_failures": ground_flow_failures,
            "negative_generation_cell_deltas": total_generation_negative_cells,
            "snapshot_timing": {
                "status": "confirmed",
                "rule": "snapshot delivered at round s includes generation visible at start(s), despite window label ending at s-1",
                "first_snapshot_exception": "round-5 snapshot also includes round-0 initial seeding, so it constrains rounds 0..5",
                "snapshots_checked": total_snapshots,
                "region_generation_constraints_checked": len(snapshot_generation_residuals),
                "generation_residual_histogram": json_hist(histogram(snapshot_generation_residuals)),
                "region_remaining_constraints_checked": len(snapshot_remaining_residuals),
                "remaining_residual_histogram": json_hist(histogram(snapshot_remaining_residuals)),
            },
        },
        "sample_sizes": {
            "games": len(games),
            "rounds": total_rounds,
            "snapshot_covered_rounds": snapshot_covered,
            "snapshot_uncovered_tail_rounds": len(tail_uncovered),
            "central_opening_round_events": len(central_opening_counts),
            "central_regular_round_events": len(central_regular_counts),
            "central_regular_spawned_cells": len(central_regular_values),
            "outer_events": len(outer_events),
            "outer_spawned_cells": sum(len(event["values"]) for event in outer_events),
            "bomb_waves": len(bomb_waves),
            "bomb_floor_candidate_exposures": sum(wave["floor_candidates"] for wave in bomb_waves),
            "bomb_eligible_exposures": bomb_eligible_total,
        },
        "central_generation": {
            "confirmed": {
                "all_1500_rounds": {
                    "round_count": len(central_all_counts),
                    "spawned_cell_count_histogram": json_hist(histogram(central_all_counts)),
                    "spawned_cell_value_histogram": json_hist(histogram(central_all_values)),
                },
                "opening_round_0_seeding": {
                    "round_count": len(central_opening_counts),
                    "spawned_cell_count_histogram": json_hist(histogram(central_opening_counts)),
                    "spawned_cell_value_histogram": json_hist(histogram(central_opening_values)),
                    "per_game_generated_value": central_opening_totals,
                    "note": "raw round-0 cell deltas are confirmed; decomposition into base seeding versus a regular spawn is not identifiable",
                },
                "regular_rounds_1_plus": {
                    "round_count": len(central_regular_counts),
                    "spawned_cell_count": sum(central_regular_counts),
                    "spawned_cell_count_histogram": json_hist(central_count_hist),
                    "spawned_cell_value_histogram": json_hist(central_value_hist),
                    "per_round_generated_total_histogram": json_hist(histogram(central_regular_totals)),
                    "generated_value_total": sum(central_regular_values),
                },
            },
            "fitted": {
                "regular_count": {
                    "status": "fitted",
                    "form": "Poisson(lambda)",
                    "lambda_numerator_spawned_cells": sum(central_regular_counts),
                    "lambda_denominator_rounds": len(central_regular_counts),
                    "lambda_mle_rounded": rounded(central_lambda, 3),
                    "empirical_histogram": json_hist(central_count_hist),
                    "expected_histogram_under_fit": expected_poisson(
                        len(central_regular_counts), central_lambda, max(central_count_hist)
                    ),
                    "pearson_chi_square_on_observed_support": pearson_chi_square(
                        central_count_hist, poisson_expected_numeric
                    ),
                },
                "regular_cell_value": {
                    "status": "fitted",
                    "form": "DiscreteUniform{1,...,10}",
                    "empirical_histogram": json_hist(central_value_hist),
                    "expected_histogram_under_fit": expected_uniform(len(central_regular_values), 1, 10),
                    "pearson_chi_square": pearson_chi_square(
                        central_value_hist, central_uniform_expected_numeric
                    ),
                },
            },
            "unknown": {
                "opening_latent_decomposition_events": len(central_opening_counts),
                "snapshot_unconstrained_tail_rounds": tail_uncovered,
                "warning": "the fitted regular law is descriptive and the 12 tail rounds have no later snapshot total",
            },
        },
        "outer_generation": {
            "confirmed": {
                "event_count": len(outer_events),
                "event_wait_interval_histogram_including_first_from_round_0": json_hist(interval_observed),
                "inter_event_interval_histogram": json_hist(histogram(outer_intervals)),
                "first_event_round_histogram": json_hist(histogram(outer_first_rounds)),
                "right_censored_rounds_after_last_event_histogram": json_hist(histogram(outer_right_censored)),
                "spawned_cell_count_histogram": json_hist(histogram(len(event["values"]) for event in outer_events)),
                "spawned_cell_value_histogram": json_hist(histogram(value for event in outer_events for value in event["values"])),
                "event_generated_value_histogram": json_hist(histogram(sum(event["values"]) for event in outer_events)),
                "event_rounds_by_input": {
                    game["path"]: game["outer_event_rounds"] for game in games
                },
                "header_token2_outer_gold_hotspot": {
                    "status": "confirmed",
                    "semantics": "header token 2 is an outer-gold hotspot; it is traversable and remains bomb-eligible",
                    "aggregate": hotspot_aggregate,
                    "per_input": outer_hotspot_by_input,
                    "all_20_token2_cells_used_in_every_input": all(
                        item["token2_outer_cells"] == 20 and item["token2_cells_used"] == 20
                        for item in outer_hotspot_by_input
                    ),
                    "note": "the exact fractions are empirical placement-rate ratios, not fabricated engine weights",
                },
                "dominant_rich_region": {
                    "uniquely_identified_events": len(rich_events),
                    "region_histogram": json_hist(region_observed),
                    "generated_total_histogram": json_hist(histogram(rich_totals)),
                    "distinct_spawned_cell_count_histogram": json_hist(histogram(rich_counts)),
                    "spawned_cell_value_histogram": json_hist(histogram(rich_cell_values)),
                    "near_even_split_events_max_minus_min_le_1": rich_near_even,
                },
                "other_three_regions_combined": {
                    "observed_spawned_cell_count_histogram": json_hist(histogram(ordinary_counts)),
                    "observed_spawned_cell_value_histogram": json_hist(histogram(ordinary_values)),
                },
            },
            "fitted": {
                "renewal_wait": {
                    "status": "fitted",
                    "form": "DiscreteUniform{8,...,16}",
                    "sample_count": len(outer_waits),
                    "empirical_histogram": json_hist(interval_observed),
                    "expected_histogram_under_fit": expected_uniform(len(outer_waits), 8, 16),
                    "pearson_chi_square": pearson_chi_square(interval_observed, interval_expected_numeric),
                },
                "rich_region_choice": {
                    "status": "fitted",
                    "form": "DiscreteUniform{2,3,4,5}",
                    "empirical_histogram": json_hist(region_observed),
                    "expected_histogram_under_fit": {
                        str(region): round(len(rich_events) / 4.0, 2) for region in range(2, 6)
                    },
                    "pearson_chi_square": pearson_chi_square(region_observed, region_expected_numeric),
                },
                "recommended_nonparametric_parts": {
                    "event_cell_count": "sample the confirmed empirical categorical histogram",
                    "rich_total_and_cell_count": "sample their joint empirical distribution, then split the total near-evenly",
                    "ordinary_cell_values": "sample the empirical categorical histogram; a simple uniform 1..10 is not claimed",
                },
            },
            "unknown": {
                "latent_draw_count": "multiple latent placements can merge into one observed cell delta",
                "token2_official_engine_weight": "unknown; report gives exact empirical per-cell placement ratios instead",
                "ordinary_values_above_10": sum(value > 10 for value in ordinary_values),
                "events_without_snapshot_constraint": outer_uncovered,
            },
        },
        "bomb_generation": {
            "confirmed": {
                "period_rounds": BOMB_PERIOD,
                "wave_rounds": list(range(0, max(game["rounds"] for game in games), BOMB_PERIOD)),
                "waves_checked": len(bomb_waves),
                "non_wave_bomb_set_changes_between_end_and_next_start": non_wave_bomb_set_changes,
                "bombs_rendered_on_header_walls": bombs_on_walls,
                "header_map": {
                    "meaning": {
                        "0": "ordinary traversable cell",
                        "1": "wall",
                        "2": "outer-gold hotspot; traversable and bomb-eligible",
                    },
                    "histogram_per_input": [game["header_histogram"] for game in games],
                    "header2_cells_per_input": [int(game["header_histogram"].get("2", 0)) for game in games],
                },
                "wave_floor_candidates": {
                    "count_histogram": json_hist(histogram(wave["floor_candidates"] for wave in bomb_waves)),
                    "eligible_count_histogram": json_hist(histogram(wave["eligible"] for wave in bomb_waves)),
                    "spawn_count_histogram": json_hist(histogram(bomb_spawn_counts)),
                    "candidate_exposures": sum(wave["floor_candidates"] for wave in bomb_waves),
                    "eligible_exposures": bomb_eligible_total,
                    "spawned": bomb_spawn_total,
                    "explained_exclusions": {
                        key: bomb_exclusions[key]
                        for key in ("gold", "gold_and_actor", "player", "npc", "player_and_npc")
                    },
                },
                "header2_outer_gold_hotspot_bomb_stratum": {
                    "hotspot_cell_exposures": sum(wave["header2_hotspot_cells"] for wave in bomb_waves),
                    "eligible_exposures": fixed_header_exposure["eligible"],
                    "spawned": fixed_header_exposure["spawned"],
                    "explained_exclusions": {
                        key: header2_exclusions[key]
                        for key in ("gold", "gold_and_actor", "player", "npc", "player_and_npc")
                    },
                    "gold_related_exclusions": header2_exclusions["gold"] + header2_exclusions["gold_and_actor"],
                    "off_header2_cell_exposures": sum(
                        wave["floor_candidates"] - wave["header2_hotspot_cells"] for wave in bomb_waves
                    ),
                    "off_header2_eligible_exposures": fixed_header_exposure["off_header2_eligible"],
                    "off_header2_spawned": fixed_header_exposure["off_header2_spawned"],
                    "hotspot_spawn_rate_per_all_cell_exposures": rounded(
                        fixed_header_exposure["spawned"]
                        / float(sum(wave["header2_hotspot_cells"] for wave in bomb_waves)),
                        4,
                    ),
                    "off_hotspot_spawn_rate_per_all_cell_exposures": rounded(
                        fixed_header_exposure["off_header2_spawned"]
                        / float(
                            sum(wave["floor_candidates"] - wave["header2_hotspot_cells"] for wave in bomb_waves)
                        ),
                        4,
                    ),
                    "hotspot_spawn_rate_when_eligible": rounded(
                        fixed_header_exposure["spawned"] / float(fixed_header_exposure["eligible"]), 4
                    ),
                    "off_hotspot_spawn_rate_when_eligible": rounded(
                        fixed_header_exposure["off_header2_spawned"]
                        / float(fixed_header_exposure["off_header2_eligible"]),
                        4,
                    ),
                    "note": "token-2 cells remain bomb-eligible; their raw bomb deficit is explained by lower eligibility from persistent outer gold, not bomb-candidate semantics",
                },
                "resampling_transition_totals_excluding_round_0": json_hist(wave_resampling),
                "per_wave": bomb_waves,
            },
            "fitted": {
                "status": "fitted",
                "form": "independent Bernoulli spawn on each currently eligible traversable cell",
                "spawn_numerator": bomb_spawn_total,
                "eligible_denominator": bomb_eligible_total,
                "p_mle_rounded": rounded(bomb_probability, 4),
                "simple_rounded_candidate_p": 0.08,
                "empirical_spawn_count_histogram": json_hist(histogram(bomb_spawn_counts)),
                "expected_spawn_count_histogram_under_varying_eligible_n": bomb_expected,
            },
            "unknown": {
                "official_probability": "not published; 0.08 is a fitted rounded candidate, not a confirmed constant",
            },
        },
        "residuals_and_ambiguities": {
            "confirmed_zero_residuals": {
                "snapshot_generation_region_constraints_nonzero": sum(value != 0 for value in snapshot_generation_residuals),
                "snapshot_remaining_region_constraints_nonzero": sum(value != 0 for value in snapshot_remaining_residuals),
                "ground_flow_rounds_failed": ground_flow_failures,
                "negative_generation_cell_deltas": total_generation_negative_cells,
                "outer_events_without_snapshot_constraint": outer_uncovered,
                "non_wave_bomb_set_changes": non_wave_bomb_set_changes,
            },
            "ambiguous_or_unknown_event_counts": {
                "central_tail_rounds_without_later_snapshot": len(tail_uncovered),
                "opening_round_events_with_unidentified_latent_decomposition": len(central_opening_counts),
                "ordinary_outer_observed_cells_above_10_possible_merged_placements": sum(value > 10 for value in ordinary_values),
            },
            "no_fabricated_precision": "all raw histograms are exact; fitted parameters retain numerator/denominator and are explicitly non-official",
        },
    }

    return report


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=pathlib.Path, help="full-information g*.txt logs")
    parser.add_argument("--output", required=True, type=pathlib.Path, help="JSON report path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = calibrate(args.logs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        "wrote %s (%d games, %d rounds)"
        % (args.output, report["inputs"]["game_count"], report["inputs"]["round_count"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
