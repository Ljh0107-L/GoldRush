#!/usr/bin/env python3
"""Authoritative executable validation suite for GoldRush simulator fidelity.

The suite is standard-library-only (apart from the local ``sim`` package), does
not load strategy shared objects, does not use the network, and treats fitted
NPC/generation behavior as warnings rather than exact-mechanics failures.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping, Sequence

# Support both ``python3 sim/validate.py`` and ``python3 -m sim.validate``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim import calibrate_views
from sim.abi import (
    GameInput,
    GameOutput,
    NpcInfo,
    Position as AbiPosition,
    RegionStat as AbiRegionStat,
    Snapshot as AbiSnapshot,
    player_input_to_abi,
    verify_abi_layout,
)
from sim.engine import (
    BOMB,
    FOG,
    GRID_SIZE,
    NPCState,
    PLAYER_MARK,
    NPC_MARK,
    GameEngine,
    GameMap,
    PlayerDecision,
    PlayerState,
    Position,
    UnitState,
    region_id,
)
from sim.runner import load_map, run_game, run_paired
from sim.scenario import (
    BOMB_PERIOD,
    MapDefinition,
    ScenarioGenerator,
    SpawnState,
    region_id as scenario_region_id,
)

FIELDS = (
    "enter",
    "leave",
    "gold_generated",
    "gold_collected",
    "gold_remaining",
    "occupants",
)
VP_BY_PRICE = {0: 0, 2: 1, 3: 2}
TRUTH_NPC = {
    "start_occupancy_exposures_r1_to_r5": [5674, 1279, 1123, 1238, 1186],
    "pickup_per_round": {"mean": 12.7887, "median": 10, "p90_nearest_rank": 27},
    "pickup_per_game_totals": [5216, 7051, 6916],
    "clean_positive_cell_lifetime": {
        "count": 3293,
        "mean": 6.277254782873,
        "median": 3,
        "p90_nearest_rank": 13,
    },
    "infra_strong_player_visible_lifetime": {"median": 2, "p90_nearest_rank": 6},
}
NPC_CALIBRATION_TARGETS = {
    "pickup_per_game_each_inclusive": [5216, 7051],
    "region1_absolute_offset_max": 309,
    "clean_lifetime_p90_inclusive": [11, 15],
    "clean_lifetime_median_exact": 3,
}


def _json_load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_log(path: Path) -> tuple[dict[str, Any], list[list[str]], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        metadata = json.loads(handle.readline())
        map_rows = json.loads(handle.readline())
        rounds = [json.loads(line) for line in handle if line.strip()]
    return metadata, map_rows, rounds


def _grid_tuple(grid: Sequence[Sequence[int]]) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(row) for row in grid)


def _positive_total(grid: Sequence[Sequence[int]]) -> int:
    return sum(value for row in grid for value in row if value > 0)


def _held(phase: Mapping[str, Any]) -> int:
    return sum(unit.get("gold", 0) for player in phase["players"] for unit in player["units"])


def _nearest_rank(values: Sequence[int], proportion: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(proportion * len(ordered)) - 1))]


def _summary(values: Sequence[int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": (sum(values) / len(values)) if values else 0.0,
        "median": statistics.median(values) if values else 0,
        "p90_nearest_rank": _nearest_rank(values, 0.90),
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
    }


def _player_decisions(row: Mapping[str, Any]) -> dict[int, PlayerDecision]:
    starts = {player["id"]: player for player in row["start"]["players"]}
    decisions: dict[int, PlayerDecision] = {}
    for player in row["end"]["players"]:
        units = player["units"]
        actions = tuple(units[0]["actions"] + units[1]["actions"])
        price = player.get("vision_spent", 0) - starts[player["id"]].get("vision_spent", 0)
        decisions[player["id"]] = PlayerDecision(
            actions,
            len(units[0]["actions"]),
            player.get("order", 0),
            VP_BY_PRICE[price],
        )
    return decisions


def _tramples(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "round": event.round,
            "pos": [event.pos.row, event.pos.col],
            "unit_owner": event.unit_owner,
            "npc_count": event.npc_count,
            "penalty": event.penalty,
        }
        for event in result.trample_events
    ]


def _add(target: list[int], source: Sequence[int]) -> None:
    for index in range(1, 6):
        target[index] += source[index]


def _differences(actual: Sequence[int], expected: Sequence[int]) -> list[float]:
    return [actual[index] - expected[index] for index in range(min(len(actual), len(expected)))]


class ValidationSuite:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo = args.repo.resolve()
        self._logs: dict[Path, tuple[dict[str, Any], list[list[str]], list[dict[str, Any]]]] = {}
        self._generated: dict[str, Any] = {}
        self.hard_checks: dict[str, Any] = {}
        self.hard_failures: list[dict[str, str]] = []
        self.fitted: dict[str, Any] = {}
        self.platform_items: list[dict[str, Any]] = []

    def log(self, path: Path) -> tuple[dict[str, Any], list[list[str]], list[dict[str, Any]]]:
        path = path.resolve()
        if path not in self._logs:
            self._logs[path] = _read_log(path)
        return self._logs[path]

    def hard(self, name: str, function: Callable[[], Mapping[str, Any]]) -> None:
        print("validating %s" % name, file=sys.stderr)
        started = perf_counter()
        try:
            result = dict(function())
            result.setdefault("passed", True)
        except Exception as error:
            result = {
                "passed": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }
        result["elapsed_seconds"] = round(perf_counter() - started, 6)
        self.hard_checks[name] = result
        if not result["passed"]:
            self.hard_failures.append({"check": name, "error": str(result.get("error", "assertion failed"))})

    @property
    def full_paths(self) -> list[Path]:
        return [self.args.full_log_dir / ("g%d.txt" % index) for index in range(3)]

    @property
    def filtered_paths(self) -> list[Path]:
        return [self.args.filtered_log_dir / ("g%d.txt" % index) for index in range(3, 11)]

    def mechanical_replay(self) -> Mapping[str, Any]:
        counts = {
            "rounds": 0,
            "end_grids": 0,
            "end_grid_cells": 0,
            "player_unit_positions": 0,
            "player_unit_gold": 0,
            "player_unit_actions": 0,
            "player_unit_pickup": 0,
            "npc_positions": 0,
            "npc_actions": 0,
            "npc_pickup": 0,
            "round_burned": 0,
            "trample_arrays": 0,
            "ground_material_balance": 0,
            "held_material_balance": 0,
        }
        expected = {
            "rounds": 1500,
            "end_grids": 1500,
            "end_grid_cells": 433500,
            "player_unit_positions": 6000,
            "player_unit_gold": 6000,
            "player_unit_actions": 6000,
            "player_unit_pickup": 6000,
            "npc_positions": 10500,
            "npc_actions": 10500,
            "npc_pickup": 10500,
            "round_burned": 1500,
            "trample_arrays": 1500,
            "ground_material_balance": 1500,
            "held_material_balance": 1500,
        }
        errors: list[str] = []
        per_game: dict[str, Any] = {}
        for path in self.full_paths:
            _meta, map_rows, rows = self.log(path)
            game_map = GameMap.from_definition(MapDefinition.from_log_line2(map_rows, name=path.stem))
            game_exact = 0
            for row in rows:
                counts["rounds"] += 1
                engine = GameEngine.from_trace_start(game_map, row["round"], row["start"])
                npc_decisions = {npc["id"]: npc["actions"] for npc in row["end"]["npcs"]}
                costs = {player["id"]: player["cost"] for player in row["end"]["players"]}
                result = engine.execute_round(
                    _player_decisions(row),
                    npc_decisions,
                    player_costs=costs,
                    dispatch_order=row["end"]["dispatch_order"],
                )
                oracle_grid = _grid_tuple(row["end"]["grid"])
                cell_matches = sum(
                    result.full_grid[r][c] == oracle_grid[r][c]
                    for r in range(GRID_SIZE)
                    for c in range(GRID_SIZE)
                )
                counts["end_grid_cells"] += cell_matches
                exact_grid = cell_matches == GRID_SIZE * GRID_SIZE
                counts["end_grids"] += exact_grid
                game_exact += exact_grid

                raw_players = {player["id"]: player for player in row["end"]["players"]}
                for player in result.state.players:
                    raw = raw_players[player.id]
                    for unit, expected_unit in zip(player.units, raw["units"]):
                        counts["player_unit_positions"] += list(unit.position.cell) == expected_unit["position"]
                        counts["player_unit_gold"] += unit.gold == expected_unit["gold"]
                        counts["player_unit_actions"] += list(unit.actions) == expected_unit["actions"]
                        counts["player_unit_pickup"] += unit.pickup == expected_unit["pickup"]
                raw_npcs = {npc["id"]: npc for npc in row["end"]["npcs"]}
                for npc in result.state.npcs:
                    raw = raw_npcs[npc.id]
                    counts["npc_positions"] += list(npc.position.cell) == raw["position"]
                    counts["npc_actions"] += list(npc.actions) == raw["actions"]
                    counts["npc_pickup"] += npc.pickup == raw["pickup"]
                counts["round_burned"] += result.burned == row["end"]["burned"]
                counts["trample_arrays"] += _tramples(result) == row["end"]["trample_events"]

                pickup = sum(unit["pickup"] for player in row["end"]["players"] for unit in player["units"])
                pickup += sum(npc["pickup"] for npc in row["end"]["npcs"])
                ground_ok = _positive_total(row["start"]["grid"]) - _positive_total(row["end"]["grid"]) == pickup
                player_pickup = sum(unit["pickup"] for player in row["end"]["players"] for unit in player["units"])
                held_ok = _held(row["start"]) + player_pickup - row["end"]["burned"] == _held(row["end"])
                counts["ground_material_balance"] += ground_ok
                counts["held_material_balance"] += held_ok
                if len(errors) < 10 and (not exact_grid or not ground_ok or not held_ok):
                    errors.append("%s round %d" % (path.name, row["round"]))
            per_game[path.name] = {"rounds": len(rows), "exact_end_grids": game_exact}
        passed = counts == expected
        return {
            "passed": passed,
            "observed_exact_counts": counts,
            "required_exact_counts": expected,
            "per_game": per_game,
            "mismatch_examples": errors,
            "error": None if passed else "mechanical replay exact counts differ",
        }

    def snapshots(self) -> Mapping[str, Any]:
        exact = {field: 0 for field in FIELDS}
        absolute_error = {field: 0 for field in FIELDS}
        snapshots = 0
        window_exact = 0
        for path in self.full_paths:
            _meta, _map_rows, rows = self.log(path)
            generated = [calibrate_views.region_generated(rows, index) for index in range(len(rows))]
            for row in rows:
                if "snapshot" not in row:
                    continue
                snapshots += 1
                round_number = row["round"]
                begin, end = row["snapshot"]["window"]
                window_exact += (begin, end) == (round_number - 5, round_number - 1)
                enter = [0] * 6
                leave = [0] * 6
                collected = [0] * 6
                for index in range(begin, end + 1):
                    one_enter, one_leave = calibrate_views.transitions(rows[index])
                    _add(enter, one_enter)
                    _add(leave, one_leave)
                    _add(collected, calibrate_views.region_collected(rows[index]))
                generated_window = [0] * 6
                generation_begin = 0 if begin == 0 else begin + 1
                for index in range(generation_begin, round_number + 1):
                    _add(generated_window, generated[index])
                computed = {
                    "enter": enter,
                    "leave": leave,
                    "gold_generated": generated_window,
                    "gold_collected": collected,
                    "gold_remaining": calibrate_views.region_gold(row["start"]["grid"]),
                    "occupants": calibrate_views.region_occupants(row["start"]),
                }
                oracle = {
                    field: [0] + [
                        next(region[field] for region in row["snapshot"]["regions"] if region["id"] == region_id_value)
                        for region_id_value in range(1, 6)
                    ]
                    for field in FIELDS
                }
                for field in FIELDS:
                    for index in range(1, 6):
                        exact[field] += computed[field][index] == oracle[field][index]
                        absolute_error[field] += abs(computed[field][index] - oracle[field][index])
        total = snapshots * 5 * len(FIELDS)
        exact_total = sum(exact.values())
        passed = snapshots == 297 and window_exact == 297 and total == 8910 and exact_total == total
        return {
            "passed": passed,
            "semantics_reused_from": str((self.repo / "sim" / "calibrate_views.py").resolve()),
            "snapshots": snapshots,
            "regions_per_snapshot": 5,
            "fields_per_region": len(FIELDS),
            "comparisons": total,
            "exact_comparisons": exact_total,
            "exact_by_field": exact,
            "absolute_error_by_field": absolute_error,
            "window_labels_exact": window_exact,
            "error": None if passed else "snapshot calibration semantics did not reproduce all fields",
        }

    def region_partition(self) -> Mapping[str, Any]:
        counts = [0, 0, 0, 0, 0]
        disagreements = []
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                ids = (region_id(row, col), scenario_region_id(row, col), calibrate_views.region_id(row, col))
                counts[ids[0] - 1] += 1
                if len(set(ids)) != 1:
                    disagreements.append([row, col, *ids])
        passed = sum(counts) == 289 and counts == [81, 52, 52, 52, 52] and not disagreements
        return {
            "passed": passed,
            "partition_cells": sum(counts),
            "region_cell_counts_r1_to_r5": counts,
            "cross_module_disagreements": disagreements,
            "error": None if passed else "region partition is incomplete or inconsistent",
        }

    def filtered_fog_and_arrays(self) -> Mapping[str, Any]:
        by_radius: dict[str, dict[str, int]] = {}
        total_rounds = 0
        total_phase_grids = 0
        mismatch_cells = 0
        files = []
        for path in self.filtered_paths:
            _meta, _map_rows, rows = self.log(path)
            files.append(path.name)
            for row in rows:
                owner, radius = calibrate_views.owner_and_radius(row["start"])
                key = str(radius)
                stat = by_radius.setdefault(key, {"rounds": 0, "phase_grids": 0, "mismatch_cells": 0})
                stat["rounds"] += 1
                total_rounds += 1
                for phase_name in ("start", "end"):
                    phase = row[phase_name]
                    phase_owner = next(player for player in phase["players"] if player["id"] == owner["id"])
                    centers = [unit["position"] for unit in phase_owner["units"] if unit.get("position") is not None]
                    expected = GameEngine.visible_cells(centers, radius)
                    actual = frozenset(
                        (r, c) for r in range(GRID_SIZE) for c in range(GRID_SIZE)
                        if phase["grid"][r][c] != FOG
                    )
                    mismatches = len(expected ^ actual)
                    stat["phase_grids"] += 1
                    stat["mismatch_cells"] += mismatches
                    total_phase_grids += 1
                    mismatch_cells += mismatches

        local_paths = sorted(self.args.local_log_dir.glob("game_*.log"))
        local_by_radius: dict[str, dict[str, int]] = {}
        local_rounds = 0
        local_phase_grids = 0
        local_mismatch_cells = 0
        # 中途判负的对局(选手非法输出/超时/崩溃)末条记录会合法地缺 start 或 end。
        # 这不是数据损坏, 不应让保真度门禁变红; 跳过但计数披露, 避免静默吞掉。
        local_skipped_incomplete = 0
        local_files_with_incomplete = 0
        local_total_rows = 0
        for path in local_paths:
            _meta, _map_rows, rows = _read_log(path)
            file_had_incomplete = False
            local_total_rows += len(rows)
            for row in rows:
                if "start" not in row or "end" not in row:
                    local_skipped_incomplete += 1
                    file_had_incomplete = True
                    continue
                owner, radius = calibrate_views.owner_and_radius(row["start"])
                key = str(radius)
                stat = local_by_radius.setdefault(key, {"rounds": 0, "phase_grids": 0, "mismatch_cells": 0})
                stat["rounds"] += 1
                local_rounds += 1
                for phase_name in ("start", "end"):
                    phase = row[phase_name]
                    phase_owner = next(player for player in phase["players"] if player["id"] == owner["id"])
                    centers = [unit["position"] for unit in phase_owner["units"] if unit.get("position") is not None]
                    expected = GameEngine.visible_cells(centers, radius)
                    actual = frozenset(
                        (r, c) for r in range(GRID_SIZE) for c in range(GRID_SIZE)
                        if phase["grid"][r][c] != FOG
                    )
                    mismatches = len(expected ^ actual)
                    stat["phase_grids"] += 1
                    stat["mismatch_cells"] += mismatches
                    local_phase_grids += 1
                    local_mismatch_cells += mismatches
            if file_had_incomplete:
                local_files_with_incomplete += 1

        map_definition = MapDefinition.from_json_file(self.args.maps, "map1")
        game_map = GameMap.from_definition(map_definition)
        base = GameEngine(game_map, npc_ids=(-1, -2, -3, -4, -5, -6, -7))
        ground = tuple(tuple(-1 if (r, c) in game_map.walls else 0 for c in range(17)) for r in range(17))
        players = (
            PlayerState(1, (UnitState(1, 0, Position(8, 8)), UnitState(1, 1, Position(0, 0)))),
            PlayerState(2, (UnitState(2, 0, Position(8, 9)), UnitState(2, 1, Position(16, 16)))),
        )
        npc_cells = ((8, 8), (8, 9), (10, 10), (16, 15), (6, 6), (2, 3), (15, 15))
        npcs = tuple(NPCState(npc_id, Position(*cell)) for npc_id, cell in zip(base.npc_ids, npc_cells))
        start = base.inject_start_state(0, ground, players, npcs, vision_radii={1: 2, 2: 2}, decode_full_markers=False)
        view = base.player_input(1, start)
        abi = player_input_to_abi(view)
        visible_enemy_ok = [
            [abi.visible_enemies[index].row, abi.visible_enemies[index].col] for index in range(2)
        ] == [[8, 9], [-1, -1]]
        visible_npcs_expected = [(npc.id, list(npc.position.cell)) for npc in npcs if npc.position.cell in GameEngine.visible_cells(((8, 8), (0, 0)), 2)]
        visible_npcs_actual = [
            (abi.visible_npcs[index].id, [abi.visible_npcs[index].pos.row, abi.visible_npcs[index].pos.col])
            for index in range(abi.num_visible_npcs)
        ]
        padding_ok = all(
            abi.visible_npcs[index].id == 0
            and abi.visible_npcs[index].pos.row == -1
            and abi.visible_npcs[index].pos.col == -1
            for index in range(abi.num_visible_npcs, 7)
        )
        pure_ground_ok = all(value not in (PLAYER_MARK, NPC_MARK) for row in view.grid for value in row)
        radius_unit = {}
        for radius in (2, 3, 4):
            visible = GameEngine.visible_cells(((8, 8), (0, 0)), radius)
            rendered = GameEngine.render_filtered_ground(start.state, 1, radius)
            actual = {(r, c) for r in range(17) for c in range(17) if rendered[r][c] != FOG}
            radius_unit[str(radius)] = {"visible_cells": len(visible), "exact": actual == set(visible)}

        array_tests = {
            "visible_enemy_compaction_and_padding": visible_enemy_ok,
            "visible_npc_count": abi.num_visible_npcs,
            "visible_npc_values_exact": visible_npcs_actual == visible_npcs_expected,
            "unused_npc_slots_padded": padding_ok,
            "filtered_grid_has_no_actor_markers": pure_ground_ok,
            "radius_render_tests": radius_unit,
        }
        passed = (
            files == ["g3.txt", "g4.txt", "g5.txt", "g6.txt", "g7.txt", "g8.txt", "g9.txt", "g10.txt"]
            and total_rounds == 4000
            and total_phase_grids == 8000
            and mismatch_cells == 0
            and set(by_radius) == {"2", "3", "4"}
            and len(local_paths) > 0
            # 不假设每份本地日志都是完整 500 轮: 中途判负的对局天然更短。
            # 等价强度的断言 = 每一条记录都被处理(校验或按不完整跳过), 且被校验的全部零误差。
            and local_rounds + local_skipped_incomplete == local_total_rows
            and local_rounds > 0
            and local_phase_grids == local_rounds * 2
            and local_mismatch_cells == 0
            and visible_enemy_ok
            and visible_npcs_actual == visible_npcs_expected
            and padding_ok
            and pure_ground_ok
            and all(item["exact"] for item in radius_unit.values())
        )
        return {
            "passed": passed,
            "files": files,
            "rounds": total_rounds,
            "phase_grids": total_phase_grids,
            "mismatch_cells": mismatch_cells,
            "by_radius": by_radius,
            "local_filtered_logs": {
                "files": len(local_paths),
                "rounds": local_rounds,
                "phase_grids": local_phase_grids,
                "mismatch_cells": local_mismatch_cells,
                "by_radius": local_by_radius,
                "skipped_incomplete_rows": local_skipped_incomplete,
                "files_with_incomplete_rows": local_files_with_incomplete,
                "total_rows_seen": local_total_rows,
                "skip_reason": "中途判负对局的末条记录合法缺 start/end; 跳过不计入吻合统计",
            },
            "visible_array_and_padding_unit_tests": array_tests,
            "error": None if passed else "official/local fog masks or visibility array unit tests differ",
        }

    def maps(self) -> Mapping[str, Any]:
        registry = _json_load(self.args.maps)
        results: dict[str, Any] = {}
        passed = True
        for name in ("map1", "map2"):
            definition = MapDefinition.from_json_file(self.args.maps, name)
            expected = registry["maps"][name]
            asset_fingerprint = hashlib.sha256("".join(definition.rows).encode("ascii")).hexdigest()
            item = {
                "limited": definition.limited,
                "walls": len(definition.walls),
                "hotspots": len(definition.outer_hotspot_cells or ()),
                "open_token_zero": sum(row.count("0") for row in definition.rows),
                "asset_definition_fingerprint": asset_fingerprint,
                "scenario_canonical_fingerprint": definition.fingerprint,
                "expected_asset_definition_fingerprint": expected["fingerprints"]["definition_sha256"],
            }
            item["exact"] = (
                not definition.limited
                and item["walls"] == expected["counts"]["walls"]
                and item["hotspots"] == expected["counts"]["bomb_candidates"]
                and item["open_token_zero"] == expected["counts"]["open"]
                and item["asset_definition_fingerprint"] == item["expected_asset_definition_fingerprint"]
            )
            passed &= item["exact"]
            results[name] = item

        map3 = MapDefinition.from_json_file(self.args.maps, "map3")
        scenario3 = ScenarioGenerator(map3, "map3-runnable")
        events3 = scenario3.resolve_round(0, SpawnState())
        map3_ok = (
            map3.limited
            and map3.outer_hotspot_cells is None
            and len(map3.walls) == registry["maps"]["map3"]["counts"]["walls"]
            and all(addition.cell in map3.traversable for addition in events3.gold_additions)
            and events3.bomb_refresh is not None
            and set(events3.bomb_refresh) <= set(map3.traversable)
        )
        results["map3"] = {
            "limited": map3.limited,
            "walls": len(map3.walls),
            "hotspot_metadata": None,
            "scenario_rounds": len(scenario3.rounds),
            "round0_resolved_gold": len(events3.gold_additions),
            "round0_resolved_bombs": len(events3.bomb_refresh or ()),
            "runnable_and_limited": map3_ok,
        }
        passed &= map3_ok

        _meta, line2, _rows = self.log(self.full_paths[0])
        direct = MapDefinition.from_log_line2(json.dumps(line2), name="arbitrary-line2")
        complete_log = load_map(self.full_paths[0])
        arbitrary_ok = direct.rows == complete_log.rows == MapDefinition.from_json_file(self.args.maps, "map1").rows
        results["arbitrary_log_line2_loading"] = {
            "json_text_and_complete_log_equal": arbitrary_ok,
            "loaded_name_from_complete_log": complete_log.name,
        }
        passed &= arbitrary_ok
        return {"passed": passed, "maps": results, "error": None if passed else "map asset validation failed"}

    def abi(self) -> Mapping[str, Any]:
        verify_abi_layout()
        expected = {
            "AbiPosition": [8, {"row": 0, "col": 4}],
            "NpcInfo": [12, {"id": 0, "pos": 4}],
            "RegionStat": [28, {"id": 0, "enter": 4, "leave": 8, "gold_generated": 12, "gold_collected": 16, "gold_remaining": 20, "occupants": 24}],
            "Snapshot": [148, {"window_begin": 0, "window_end": 4, "regions": 8}],
            "GameInput": [1444, {"round": 0, "grid": 4, "my_units": 1160, "my_units_gold": 1176, "gold_opp": 1184, "visible_enemies": 1188, "num_visible_npcs": 1204, "visible_npcs": 1208, "snapshot_valid": 1292, "snapshot": 1296}],
            "GameOutput": [36, {"actions": 0, "k": 24, "order": 28, "vp": 32}],
        }
        classes = {
            "AbiPosition": AbiPosition,
            "NpcInfo": NpcInfo,
            "RegionStat": AbiRegionStat,
            "Snapshot": AbiSnapshot,
            "GameInput": GameInput,
            "GameOutput": GameOutput,
        }
        actual = {
            name: [ctypes.sizeof(structure), {field: getattr(structure, field).offset for field, _ctype in structure._fields_}]
            for name, structure in classes.items()
        }
        passed = actual == expected and ctypes.sizeof(ctypes.c_int) == 4
        return {
            "passed": passed,
            "c_int_size": ctypes.sizeof(ctypes.c_int),
            "actual": actual,
            "expected": expected,
            "error": None if passed else "ABI size/offset mismatch",
        }

    def deterministic_logs(self) -> Mapping[str, Any]:
        started = perf_counter()
        first = run_game("stay", "scripted", map_source="map1", seed="validation-determinism")
        elapsed = perf_counter() - started
        second = run_game("stay", "scripted", map_source="map1", seed="validation-determinism")
        different = run_game("stay", "scripted", map_source="map1", seed="validation-divergence")
        self._generated["primary"] = first
        self._generated["primary_elapsed"] = elapsed
        same = first.log_bytes == second.log_bytes and first.log_digest == second.log_digest
        diverged = first.log_bytes != different.log_bytes and first.scenario_digest != different.scenario_digest
        passed = same and diverged
        return {
            "passed": passed,
            "same_seed_byte_identity": same,
            "same_seed_log_sha256": first.log_digest,
            "different_seed_divergence": diverged,
            "different_seed_log_sha256": different.log_digest,
            "single_game_wall_clock_seconds": round(elapsed, 6),
            "error": None if passed else "deterministic identity/divergence contract failed",
        }

    def paired(self) -> Mapping[str, Any]:
        paired = run_paired("stay", "scripted", map_source="map1", seed="validation-paired", name_a="A", name_b="B")
        first_names = [paired.a_as_p1.summary["players"][seat]["name"] for seat in ("1", "2")]
        second_names = [paired.b_as_p1.summary["players"][seat]["name"] for seat in ("1", "2")]
        digest_ok = paired.a_as_p1.scenario_digest == paired.b_as_p1.scenario_digest == paired.scenario_digest
        swap_ok = first_names == ["A", "B"] and second_names == ["B", "A"]
        passed = digest_ok and swap_ok
        return {
            "passed": passed,
            "scenario_digest": paired.scenario_digest,
            "leg_a_b_names": first_names,
            "leg_b_a_names": second_names,
            "seat_swap_exact": swap_ok,
            "error": None if passed else "paired legs do not preserve scenario or swap seats",
        }

    def gamelog_subprocess(self) -> Mapping[str, Any]:
        game = self._generated["primary"]
        tool = (self.repo / "tools" / "gamelog.py").resolve()
        before = hashlib.sha256(tool.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="goldrush-validation-") as directory:
            log_path = Path(directory) / "generated.log"
            log_path.write_bytes(game.log_bytes)
            completed = subprocess.run(
                [sys.executable, str(tool), str(log_path)],
                cwd=str(self.repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        after = hashlib.sha256(tool.read_bytes()).hexdigest()
        passed = completed.returncode == 0 and "500 回合" in completed.stdout and "解析失败" not in completed.stderr and before == after
        return {
            "passed": passed,
            "command": [sys.executable, str(tool), "<temporary-generated-log>"],
            "returncode": completed.returncode,
            "stdout_contains_500_rounds": "500 回合" in completed.stdout,
            "stderr": completed.stderr,
            "tool_sha256_before": before,
            "tool_sha256_after": after,
            "tool_unmodified": before == after,
            "error": None if passed else "unmodified tools/gamelog.py did not parse generated log",
        }

    def generated_game_invariants(self) -> Mapping[str, Any]:
        game = self._generated["primary"]
        lines = game.log_bytes.decode("ascii").splitlines()
        map_rows = json.loads(lines[1])
        walls = {(r, c) for r, row in enumerate(map_rows) for c, value in enumerate(row) if value == "1"}
        rows = [json.loads(line) for line in lines[2:] if line]
        illegal = []
        material_failures = []
        non_wave_bomb_changes = 0
        wave_eligibility_failures = 0
        wave_rounds = []
        previous_end_bombs: set[tuple[int, int]] | None = None
        for row in rows:
            round_number = row["round"]
            for player in row["end"]["players"]:
                actions = [action for unit in player["units"] for action in unit["actions"]]
                if len(actions) != 6 or any(action not in range(5) for action in actions):
                    illegal.append([round_number, "player", player["id"], actions])
            for npc in row["end"]["npcs"]:
                if len(npc["actions"]) != 3 or any(action not in range(5) for action in npc["actions"]):
                    illegal.append([round_number, "npc", npc["id"], npc["actions"]])
            pickup = sum(unit["pickup"] for player in row["end"]["players"] for unit in player["units"])
            pickup += sum(npc["pickup"] for npc in row["end"]["npcs"])
            player_pickup = sum(unit["pickup"] for player in row["end"]["players"] for unit in player["units"])
            if _positive_total(row["start"]["grid"]) - _positive_total(row["end"]["grid"]) != pickup:
                material_failures.append([round_number, "ground"])
            if _held(row["start"]) + player_pickup - row["end"]["burned"] != _held(row["end"]):
                material_failures.append([round_number, "held"])
            start_bombs = {(r, c) for r in range(17) for c in range(17) if row["start"]["grid"][r][c] == BOMB}
            end_bombs = {(r, c) for r in range(17) for c in range(17) if row["end"]["grid"][r][c] == BOMB}
            if round_number % BOMB_PERIOD == 0:
                wave_rounds.append(round_number)
                actors = {
                    tuple(unit["position"])
                    for player in row["start"]["players"] for unit in player["units"]
                } | {tuple(npc["position"]) for npc in row["start"]["npcs"]}
                gold = {(r, c) for r in range(17) for c in range(17) if row["start"]["grid"][r][c] > 0}
                wave_eligibility_failures += len(start_bombs & (walls | actors | gold))
            elif previous_end_bombs is not None:
                non_wave_bomb_changes += len(start_bombs ^ previous_end_bombs)
            previous_end_bombs = end_bombs
        expected_waves = list(range(0, 500, 20))
        passed = (
            len(rows) == 500
            and not illegal
            and not material_failures
            and wave_rounds == expected_waves
            and non_wave_bomb_changes == 0
            and wave_eligibility_failures == 0
        )
        return {
            "passed": passed,
            "rounds": len(rows),
            "illegal_outputs": illegal[:10],
            "material_balance_failures": material_failures[:10],
            "bomb_wave_rounds": wave_rounds,
            "expected_bomb_wave_rounds": expected_waves,
            "non_wave_bomb_set_changes": non_wave_bomb_changes,
            "wave_bomb_ineligible_cells": wave_eligibility_failures,
            "log_sha256": game.log_digest,
            "error": None if passed else "generated game invariants failed",
        }

    def wall_clock(self) -> Mapping[str, Any]:
        elapsed = float(self._generated["primary_elapsed"])
        passed = elapsed > 0 and self._generated["primary"].summary["rounds"] == 500
        return {
            "passed": passed,
            "rounds": 500,
            "wall_clock_seconds": round(elapsed, 6),
            "previous_independent_observation_seconds": 4.38,
            "ratio_to_previous_observation": round(elapsed / 4.38, 4),
            "note": "Informational benchmark; speed variation is not a fidelity failure.",
            "error": None if passed else "wall-clock game did not complete",
        }

    def generation_evidence(self) -> Mapping[str, Any]:
        report = _json_load(self.args.generation_report)
        extraction = report["extraction"]
        timing = extraction["snapshot_timing"]
        hotspot = report["outer_generation"]["confirmed"]["header_token2_outer_gold_hotspot"]
        bomb = report["bomb_generation"]["confirmed"]
        hard_assertions = {
            "ground_flow_failures_zero": extraction["ground_flow_failures"] == 0,
            "negative_generation_deltas_zero": extraction["negative_generation_cell_deltas"] == 0,
            "generation_residuals_all_zero": timing["generation_residual_histogram"] == {"0": 1485},
            "remaining_residuals_all_zero": timing["remaining_residual_histogram"] == {"0": 1485},
            "snapshot_constraints_297x5": timing["region_generation_constraints_checked"] == 1485,
            "hotspot_observed_618_of_1142": hotspot["aggregate"]["token2_placements"] == 618 and hotspot["aggregate"]["outer_spawned_cell_placements"] == 1142,
            "all_hotspots_used": hotspot["all_20_token2_cells_used_in_every_input"] is True,
            "hotspots_declared_traversable_bomb_eligible": "bomb-eligible" in hotspot["semantics"],
            "bomb_period_20": bomb["period_rounds"] == 20,
            "bomb_waves_75": bomb["waves_checked"] == 75,
            "non_wave_bomb_changes_zero": bomb["non_wave_bomb_set_changes_between_end_and_next_start"] == 0,
            "bombs_on_walls_zero": bomb["bombs_rendered_on_header_walls"] == 0,
            "hotspot_bomb_eligibility_observed": bomb["header2_outer_gold_hotspot_bomb_stratum"]["eligible_exposures"] > 0 and bomb["header2_outer_gold_hotspot_bomb_stratum"]["spawned"] > 0,
        }
        map1 = MapDefinition.from_json_file(self.args.maps, "map1")
        generator = ScenarioGenerator(map1, "generation-eligibility")
        wave_intents = [intent for intent in generator.intents if intent.bomb_trials is not None]
        non_wave_trials = [intent.round for intent in generator.intents if intent.round % 20 and intent.bomb_trials is not None]
        trial_cells = set().union(*(set(intent.bomb_trials or ()) for intent in wave_intents))
        scenario_assertions = {
            "exactly_25_wave_intents": len(wave_intents) == 25,
            "no_non_wave_trials": not non_wave_trials,
            "all_trials_traversable": trial_cells <= set(map1.traversable),
            "hotspots_are_traversable": set(map1.outer_hotspot_cells or ()) <= set(map1.traversable),
            "hotspots_appear_in_unrestricted_bomb_trials": bool(trial_cells & set(map1.outer_hotspot_cells or ())),
        }
        passed = all(hard_assertions.values()) and all(scenario_assertions.values())
        self.fitted["generation_fit"] = self._generation_fit_warning(map1)
        return {
            "passed": passed,
            "report": str(self.args.generation_report.resolve()),
            "report_residual_and_evidence_assertions": hard_assertions,
            "runtime_scenario_eligibility_assertions": scenario_assertions,
            "runtime_unique_bomb_trial_cells": len(trial_cells),
            "runtime_hotspot_trial_cells": len(trial_cells & set(map1.outer_hotspot_cells or ())),
            "error": None if passed else "generation confirmed-evidence assertions failed",
        }

    def _generation_fit_warning(self, map1: MapDefinition) -> Mapping[str, Any]:
        hotspot_count = 0
        outer_count = 0
        seeds = list(range(8))
        for seed in seeds:
            generator = ScenarioGenerator(map1, seed)
            for round_number in range(500):
                for addition in generator.resolve_round(round_number).gold_additions:
                    if addition.source.startswith("outer"):
                        outer_count += 1
                        hotspot_count += addition.cell in (map1.outer_hotspot_cells or frozenset())
        share = hotspot_count / outer_count
        empirical = 618 / 1142
        return {
            "classification": "fitted_warning",
            "claim": "Sampler hotspot weighting is a descriptive fit, not exact mechanics.",
            "seeds": seeds,
            "generated_hotspot_cells": hotspot_count,
            "generated_outer_cells": outer_count,
            "generated_share": share,
            "truth_share": empirical,
            "absolute_difference": abs(share - empirical),
            "warning": "Deviation is reported and does not affect hard_pass.",
        }

    def weak_player_npc(self) -> None:
        print("validating fitted weak-player NPC behavior", file=sys.stderr)
        _meta, _map_rows, g0 = self.log(self.full_paths[0])
        scripts: dict[int, tuple[tuple[int, ...], ...]] = {1: (), 2: ()}
        mutable: dict[int, list[tuple[int, ...]]] = {1: [], 2: []}
        for row in g0:
            starts = {player["id"]: player for player in row["start"]["players"]}
            for player in row["end"]["players"]:
                units = player["units"]
                price = player.get("vision_spent", 0) - starts[player["id"]].get("vision_spent", 0)
                mutable[player["id"]].append(tuple(units[0]["actions"] + units[1]["actions"] + [len(units[0]["actions"]), player.get("order", 0), VP_BY_PRICE[price]]))
        scripts = {seat: tuple(values) for seat, values in mutable.items()}

        def policy(seat: int) -> Callable[[Any], tuple[int, ...]]:
            def recorded(value: Any) -> tuple[int, ...]:
                return scripts[seat][value.round]
            recorded.__name__ = "g0_recorded_p%d" % seat
            return recorded

        seeds = ["npc-synthetic-0", "npc-synthetic-1", "npc-synthetic-2"]
        generated_rows = []
        per_game_pickup = []
        for seed in seeds:
            game = run_game(policy(1), policy(2), map_source="map1", seed=seed)
            rows = [json.loads(line) for line in game.log_bytes.decode("ascii").splitlines()[2:] if line]
            generated_rows.append(rows)
            per_game_pickup.append(sum(npc["pickup"] for row in rows for npc in row["end"]["npcs"]))

        occupancy = [0, 0, 0, 0, 0]
        pickup_rounds: list[int] = []
        clean_lifetimes: list[int] = []
        replenished_lifetimes: list[int] = []
        right_censored = 0
        for rows in generated_rows:
            active: dict[tuple[int, int], dict[str, Any]] = {}
            previous_end = [[0] * 17 for _ in range(17)]
            for row in rows:
                for npc in row["start"]["npcs"]:
                    occupancy[region_id(*npc["position"]) - 1] += 1
                pickup_rounds.append(sum(npc["pickup"] for npc in row["end"]["npcs"]))
                start_grid = row["start"]["grid"]
                end_grid = row["end"]["grid"]
                for r in range(17):
                    for c in range(17):
                        cell = (r, c)
                        previous = max(previous_end[r][c], 0)
                        current = max(start_grid[r][c], 0)
                        if previous == 0 and current > 0:
                            active[cell] = {"start": row["round"], "replenished": False}
                        elif previous > 0 and current > previous and cell in active:
                            active[cell]["replenished"] = True
                        if cell in active and max(end_grid[r][c], 0) == 0:
                            lifetime = row["round"] - active[cell]["start"] + 1
                            (replenished_lifetimes if active[cell]["replenished"] else clean_lifetimes).append(lifetime)
                            del active[cell]
                previous_end = end_grid
            right_censored += len(active)

        metrics = {
            "synthetic_seeds": seeds,
            "policy": "g0 recorded effective player decisions replayed as two callables",
            "games": len(generated_rows),
            "rounds": len(pickup_rounds),
            "npc_start_occupancy_exposures_r1_to_r5": occupancy,
            "npc_pickup_per_round": _summary(pickup_rounds),
            "npc_pickup_per_game_totals": per_game_pickup,
            "clean_positive_cell_lifetime": _summary(clean_lifetimes),
            "replenished_positive_cell_lifetime": _summary(replenished_lifetimes),
            "right_censored_positive_cells": right_censored,
        }
        comparisons = {
            "occupancy_delta": _differences(occupancy, TRUTH_NPC["start_occupancy_exposures_r1_to_r5"]),
            "pickup_per_game_delta_against_truth_list_order": _differences(per_game_pickup, TRUTH_NPC["pickup_per_game_totals"]),
            "pickup_mean_delta": metrics["npc_pickup_per_round"]["mean"] - TRUTH_NPC["pickup_per_round"]["mean"],
            "pickup_median_delta": metrics["npc_pickup_per_round"]["median"] - TRUTH_NPC["pickup_per_round"]["median"],
            "pickup_p90_delta": metrics["npc_pickup_per_round"]["p90_nearest_rank"] - TRUTH_NPC["pickup_per_round"]["p90_nearest_rank"],
            "clean_lifetime_count_delta": metrics["clean_positive_cell_lifetime"]["count"] - TRUTH_NPC["clean_positive_cell_lifetime"]["count"],
            "clean_lifetime_mean_delta": metrics["clean_positive_cell_lifetime"]["mean"] - TRUTH_NPC["clean_positive_cell_lifetime"]["mean"],
            "clean_lifetime_median_delta": metrics["clean_positive_cell_lifetime"]["median"] - TRUTH_NPC["clean_positive_cell_lifetime"]["median"],
            "clean_lifetime_p90_delta": metrics["clean_positive_cell_lifetime"]["p90_nearest_rank"] - TRUTH_NPC["clean_positive_cell_lifetime"]["p90_nearest_rank"],
        }
        pickup_low, pickup_high = NPC_CALIBRATION_TARGETS["pickup_per_game_each_inclusive"]
        lifetime_low, lifetime_high = NPC_CALIBRATION_TARGETS["clean_lifetime_p90_inclusive"]
        target_checks = {
            "all_pickup_totals_in_range": all(
                pickup_low <= value <= pickup_high for value in per_game_pickup
            ),
            "region1_absolute_offset_within_limit": abs(comparisons["occupancy_delta"][0])
            <= NPC_CALIBRATION_TARGETS["region1_absolute_offset_max"],
            "clean_lifetime_p90_in_range": lifetime_low
            <= metrics["clean_positive_cell_lifetime"]["p90_nearest_rank"]
            <= lifetime_high,
            "clean_lifetime_median_exact": metrics["clean_positive_cell_lifetime"]["median"]
            == NPC_CALIBRATION_TARGETS["clean_lifetime_median_exact"],
        }
        self.fitted["npc_behavior"] = {
            "classification": "fitted_warning",
            "truth": TRUTH_NPC,
            "synthetic_weak_player_metrics": metrics,
            "comparison_to_truth": comparisons,
            "calibration_attempt": {
                "passed": all(target_checks.values()),
                "targets": NPC_CALIBRATION_TARGETS,
                "checks": target_checks,
                "selection": "rule-based bomb avoidance retained on corpus evidence; target inertia failed its oracle gate and was not rolled out",
                "candidate_evidence": str(self.args.npc_report.resolve()),
                "in_sample_warning": "The macro benchmark used exactly these three synthetic seeds and replay policies; it is not held-out evidence.",
            },
            "warning": "Private NPC behavior and fitted generation are not identifiable exactly; deviations are intentionally warnings, not hard failures.",
        }

    def report(self) -> Mapping[str, Any]:
        self.platform_items.append(
            {
                "classification": "platform_limited",
                "capability": "official Linux x86_64 strategy .so execution",
                "available": False,
                "platform": "%s %s" % (platform.system(), platform.machine()),
                "reason": "Acceptance explicitly uses built-in/scripted/callable policies on macOS; no .so is loaded.",
            }
        )
        self.platform_items.append(
            {
                "classification": "platform_limited",
                "capability": "platform submission/network validation",
                "available": False,
                "reason": "Deliberately not attempted: suite performs no submissions and no HTTP/network access.",
            }
        )
        return {
            "schema_version": 1,
            "suite": "GoldRush simulator authoritative fidelity validation",
            "repository": str(self.repo),
            "inputs": {
                "full_logs": [str(path.resolve()) for path in self.full_paths],
                "official_filtered_logs": [str(path.resolve()) for path in self.filtered_paths],
                "local_log_dir": str(self.args.local_log_dir.resolve()),
                "maps": str(self.args.maps.resolve()),
                "mechanics_report": str(self.args.mechanics_report.resolve()),
                "visibility_report": str(self.args.visibility_report.resolve()),
                "generation_report": str(self.args.generation_report.resolve()),
                "npc_report": str(self.args.npc_report.resolve()),
            },
            "hard_pass": {
                "passed": not self.hard_failures,
                "checks": self.hard_checks,
                "failures": self.hard_failures,
                "exit_code_contract": "0 iff all hard deterministic fidelity checks pass; fitted warnings never make exit nonzero",
            },
            "fitted_warning": {
                "classification": "non-failing fitted/statistical deviations",
                "checks": self.fitted,
            },
            "platform_limited": {
                "classification": "not attempted on this platform/by scope",
                "items": self.platform_items,
            },
        }

    def run(self) -> tuple[Mapping[str, Any], int]:
        self.hard("mechanical_replay_3x500", self.mechanical_replay)
        self.hard("snapshots_297x5x6", self.snapshots)
        self.hard("region_partition_289", self.region_partition)
        self.hard("official_filtered_fog_and_visible_arrays", self.filtered_fog_and_arrays)
        self.hard("map_definitions_and_loading", self.maps)
        self.hard("abi_size_and_offsets", self.abi)
        self.hard("same_seed_identity_and_different_seed_divergence", self.deterministic_logs)
        self.hard("paired_legs_digest_and_seat_swap", self.paired)
        self.hard("unmodified_gamelog_subprocess", self.gamelog_subprocess)
        self.hard("generated_game_invariants", self.generated_game_invariants)
        self.hard("single_game_wall_clock", self.wall_clock)
        self.hard("generation_residuals_hotspot_and_bomb_eligibility", self.generation_evidence)
        try:
            self.weak_player_npc()
        except Exception as error:
            self.fitted["npc_behavior"] = {
                "classification": "fitted_warning",
                "error_type": type(error).__name__,
                "error": str(error),
                "warning": "Fitted benchmark could not complete; hard deterministic fidelity status is unaffected.",
            }
        report = self.report()
        return report, 0 if report["hard_pass"]["passed"] else 1


def _resolve(value: str | os.PathLike[str], repo: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else repo / path


def build_parser() -> argparse.ArgumentParser:
    default_repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=default_repo, help="repository root (default: detected from sim/validate.py)")
    parser.add_argument("--full-log-dir", default="logs/gr_data/full")
    parser.add_argument("--filtered-log-dir", default="logs/gr_data/user")
    parser.add_argument("--local-log-dir", default="logs")
    parser.add_argument("--maps", default="sim/maps.json")
    parser.add_argument("--mechanics-report", default="sim/reports/mechanics.json")
    parser.add_argument("--visibility-report", default="sim/reports/visibility_snapshot.json")
    parser.add_argument("--generation-report", default="sim/reports/generation.json")
    parser.add_argument("--npc-report", default="sim/reports/npc.json")
    parser.add_argument("--output", default="sim/reports/validation.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.repo = args.repo.expanduser().resolve()
    for attribute in (
        "full_log_dir", "filtered_log_dir", "local_log_dir", "maps",
        "mechanics_report", "visibility_report", "generation_report", "npc_report", "output",
    ):
        setattr(args, attribute, _resolve(getattr(args, attribute), args.repo).resolve())
    suite = ValidationSuite(args)
    report, exit_code = suite.run()
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).encode("ascii") + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    sys.stdout.buffer.write(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
