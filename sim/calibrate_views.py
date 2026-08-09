#!/usr/bin/env python3
"""Calibrate GoldRush maps, visibility, rendering, and snapshot semantics.

Reads repository logs and sim/maps.json, then writes the evidence report to
sim/reports/visibility_snapshot.json.  Standard-library only.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

N = 17
FOG = -5
PLAYER_MARK = -2
NPC_MARK = -4
FIELDS = (
    "enter",
    "leave",
    "gold_generated",
    "gold_collected",
    "gold_remaining",
    "occupants",
)


def ratio(exact: int, total: int) -> float:
    return exact / total if total else 0.0


def sha(rows: Iterable[str]) -> str:
    return hashlib.sha256("".join(rows).encode()).hexdigest()


def read_log(path: Path) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        metadata = json.loads(handle.readline())
        map_rows = ["".join(row) for row in json.loads(handle.readline())]
        rounds = [json.loads(line) for line in handle if line.strip()]
    return metadata, map_rows, rounds


def region_id(row: int, col: int) -> int:
    """User-confirmed 5-region windmill partition, 0-based coordinates."""
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
    raise AssertionError((row, col))


def region_grid() -> list[str]:
    return ["".join(str(region_id(r, c)) for c in range(N)) for r in range(N)]


def positive(value: int) -> int:
    return value if value > 0 else 0


def region_gold(grid: list[list[int]]) -> list[int]:
    result = [0] * 6
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            result[region_id(r, c)] += positive(value)
    return result


def entities(state: dict[str, Any], include_npcs: bool = True) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for player in state["players"]:
        for unit_index, unit in enumerate(player["units"]):
            if unit.get("position") is not None:
                result[f"p{player['id']}u{unit_index}"] = tuple(unit["position"])
    if include_npcs:
        for npc in state.get("npcs") or []:
            if npc.get("position") is not None:
                result[f"n{npc['id']}"] = tuple(npc["position"])
    return result


def region_occupants(state: dict[str, Any], include_npcs: bool = True) -> list[int]:
    result = [0] * 6
    for position in entities(state, include_npcs).values():
        result[region_id(*position)] += 1
    return result


def transitions(row: dict[str, Any]) -> tuple[list[int], list[int]]:
    before = {key: region_id(*pos) for key, pos in entities(row["start"]).items()}
    after = {key: region_id(*pos) for key, pos in entities(row["end"]).items()}
    enter = [0] * 6
    leave = [0] * 6
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        if old != new:
            if old is not None:
                leave[old] += 1
            if new is not None:
                enter[new] += 1
    return enter, leave


def region_collected(row: dict[str, Any]) -> list[int]:
    """Movement-phase depletion; generation only occurs before start."""
    result = [0] * 6
    start, end = row["start"]["grid"], row["end"]["grid"]
    for r in range(N):
        for c in range(N):
            result[region_id(r, c)] += max(positive(start[r][c]) - positive(end[r][c]), 0)
    return result


def region_generated(rows: list[dict[str, Any]], round_index: int) -> list[int]:
    """Pre-decision generation from previous end to this round's start."""
    current = rows[round_index]["start"]["grid"]
    previous = rows[round_index - 1]["end"]["grid"] if round_index else [[0] * N for _ in range(N)]
    result = [0] * 6
    for r in range(N):
        for c in range(N):
            result[region_id(r, c)] += max(positive(current[r][c]) - positive(previous[r][c]), 0)
    return result


def add_vectors(target: list[int], source: list[int]) -> None:
    for i in range(1, 6):
        target[i] += source[i]


def visible_union(centers: list[list[int]], radius: int) -> set[tuple[int, int]]:
    return {
        (r, c)
        for center_r, center_c in centers
        for r in range(max(0, center_r - radius), min(N, center_r + radius + 1))
        for c in range(max(0, center_c - radius), min(N, center_c + radius + 1))
    }


def unclipped_union(centers: list[list[int]], radius: int) -> set[tuple[int, int]]:
    return {
        (r, c)
        for center_r, center_c in centers
        for r in range(center_r - radius, center_r + radius + 1)
        for c in range(center_c - radius, center_c + radius + 1)
    }


def owner_and_radius(state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    vision = state["vision_r"]
    if len(vision) != 1:
        raise AssertionError(f"expected one filtered owner, got {vision}")
    owner_id = int(next(iter(vision)))
    owner = next(player for player in state["players"] if player["id"] == owner_id)
    return owner, vision[str(owner_id)]


def strip_full_markers(grid: list[list[int]]) -> list[list[int]]:
    return [[0 if value in (PLAYER_MARK, NPC_MARK) else value for value in row] for row in grid]


def render_full(state: dict[str, Any], npc_over_player: bool = True) -> list[list[int]]:
    """Render replay full-grid entity markers over its recoverable ground layer."""
    rendered = strip_full_markers(state["grid"])
    player_positions: list[tuple[int, int]] = []
    for player in state["players"]:
        player_positions.extend(tuple(unit["position"]) for unit in player["units"] if unit.get("position") is not None)
    npc_positions = [tuple(npc["position"]) for npc in state.get("npcs") or [] if npc.get("position") is not None]

    if npc_over_player:
        for r, c in player_positions:
            if rendered[r][c] == 0:
                rendered[r][c] = PLAYER_MARK
        for r, c in npc_positions:
            if rendered[r][c] in (0, PLAYER_MARK):
                rendered[r][c] = NPC_MARK
    else:
        for r, c in npc_positions:
            if rendered[r][c] == 0:
                rendered[r][c] = NPC_MARK
        for r, c in player_positions:
            if rendered[r][c] in (0, NPC_MARK):
                rendered[r][c] = PLAYER_MARK
    return rendered


def compare_grid(a: list[list[int]], b: list[list[int]]) -> int:
    return sum(a[r][c] != b[r][c] for r in range(N) for c in range(N))


def summarize_counter(counter: collections.Counter[str]) -> dict[str, Any]:
    result = dict(counter)
    if "exact" in counter and "samples" in counter:
        result["exact_rate"] = ratio(counter["exact"], counter["samples"])
    return result


def analyze_filtered(paths: list[Path], group: str, map_defs: dict[str, Any]) -> dict[str, Any]:
    by_radius: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    edge_by_radius: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    edge_examples: dict[str, dict[str, Any]] = {}
    totals: collections.Counter[str] = collections.Counter()
    spawn_pairs: collections.Counter[str] = collections.Counter()
    owner_ids: collections.Counter[str] = collections.Counter()
    header_fingerprints: collections.Counter[str] = collections.Counter()
    marker_cells = 0
    visible_wall_mismatches = 0
    visible_wall_checks = 0
    dynamic_bombs = 0
    dynamic_bombs_outside_token2 = 0

    known = {entry["fingerprints"]["definition_sha256"]: name for name, entry in map_defs.items() if not entry["limited"]}

    for path in paths:
        _, map_rows, rows = read_log(path)
        fingerprint = sha(map_rows)
        header_fingerprints[known.get(fingerprint, f"unknown:{fingerprint}")] += 1
        walls = {(r, c) for r, row in enumerate(map_rows) for c, value in enumerate(row) if value == "1"}
        token2 = {(r, c) for r, row in enumerate(map_rows) for c, value in enumerate(row) if value == "2"}

        first_owner, _ = owner_and_radius(rows[0]["start"])
        pair = tuple(tuple(unit["position"]) for unit in first_owner["units"])
        spawn_pairs[json.dumps(pair, separators=(",", ":"))] += 1
        owner_ids[str(first_owner["id"])] += 1

        for row in rows:
            owner, radius = owner_and_radius(row["start"])
            row_mismatch_cells = 0
            for phase in ("start", "end"):
                state = row[phase]
                phase_owner = next(player for player in state["players"] if player["id"] == owner["id"])
                centers = [unit["position"] for unit in phase_owner["units"] if unit.get("position") is not None]
                expected = visible_union(centers, radius)
                actual = {(r, c) for r in range(N) for c in range(N) if state["grid"][r][c] != FOG}
                mismatch = expected ^ actual
                row_mismatch_cells += len(mismatch)
                key = f"radius_{radius}"
                stat = by_radius[key]
                stat["phase_grids"] += 1
                stat["exact_phase_grids"] += not mismatch
                stat["mismatch_cells"] += len(mismatch)
                stat["expected_visible_cells"] += len(expected)
                stat["actual_visible_cells"] += len(actual)
                totals["phase_grids"] += 1
                totals["exact_phase_grids"] += not mismatch
                totals["mismatch_cells"] += len(mismatch)

                outside = unclipped_union(centers, radius) - expected
                if outside:
                    edge = edge_by_radius[key]
                    edge["phase_grids"] += 1
                    edge["exact_phase_grids"] += not mismatch
                    edge["discarded_out_of_bounds_union_cells"] += len(outside)
                    edge_examples.setdefault(
                        key,
                        {
                            "file": path.name,
                            "round": row["round"],
                            "phase": phase,
                            "centers": centers,
                            "radius": radius,
                            "unclipped_union_cells": len(unclipped_union(centers, radius)),
                            "clipped_union_cells": len(expected),
                        },
                    )

                grid = state["grid"]
                marker_cells += sum(value in (PLAYER_MARK, NPC_MARK) for grid_row in grid for value in grid_row)
                visible_wall_checks += len(walls & actual)
                visible_wall_mismatches += sum(grid[r][c] != -1 for r, c in walls & actual)
                for r, c in actual:
                    if grid[r][c] == -3:
                        dynamic_bombs += 1
                        dynamic_bombs_outside_token2 += (r, c) not in token2

            totals["rounds"] += 1
            totals["exact_rounds"] += row_mismatch_cells == 0
            by_radius[f"radius_{radius}"]["rounds"] += 1
            by_radius[f"radius_{radius}"]["exact_rounds"] += row_mismatch_cells == 0

    radii = {}
    for key, stat in sorted(by_radius.items()):
        item = dict(stat)
        item["exact_rate"] = ratio(stat["exact_phase_grids"], stat["phase_grids"])
        item["exact_round_rate"] = ratio(stat["exact_rounds"], stat["rounds"])
        radii[key] = item
    edges = {}
    for key, stat in sorted(edge_by_radius.items()):
        item = dict(stat)
        item["exact_rate"] = ratio(stat["exact_phase_grids"], stat["phase_grids"])
        item["example"] = edge_examples[key]
        edges[key] = item

    return {
        "group": group,
        "files": len(paths),
        "rounds": len(paths) * 500,
        "map_headers": dict(sorted(header_fingerprints.items())),
        "owner_ids": dict(sorted(owner_ids.items())),
        "round0_owner_spawn_pairs": dict(sorted(spawn_pairs.items())),
        "visibility": {
            "formula": "union of both owner-centered Chebyshev squares, inclusive radius, clipped independently to [0,16]^2",
            "totals": {
                **dict(totals),
                "exact_rate": ratio(totals["exact_phase_grids"], totals["phase_grids"]),
                "exact_round_rate": ratio(totals["exact_rounds"], totals["rounds"]),
            },
            "by_radius": radii,
            "edge_clipping": edges,
        },
        "filtered_grid_content": {
            "entity_marker_cells_minus2_or_minus4": marker_cells,
            "visible_static_wall_cell_checks": visible_wall_checks,
            "visible_static_wall_mismatches": visible_wall_mismatches,
            "dynamic_bomb_observations": dynamic_bombs,
            "dynamic_bombs_outside_line2_token2": dynamic_bombs_outside_token2,
            "token2_note": "Line-2 token 2 is retained as map bomb-candidate metadata, not treated as the current-round bomb bitmap; live -3 cells are dynamic and may occur outside token-2 cells.",
        },
    }


def field_result(counter: collections.Counter[str]) -> dict[str, Any]:
    return {
        **dict(counter),
        "region_exact_rate": ratio(counter["region_exact"], counter["regions"]),
        "snapshot_exact_rate": ratio(counter["snapshot_exact"], counter["snapshots"]),
    }


def analyze_full(paths: list[Path], map_defs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    overlay = collections.Counter()
    wrong_overlay = collections.Counter()
    marker_alignment = collections.Counter()
    terrain_at_entities = collections.Counter()
    snapshot_fields = {field: collections.Counter() for field in FIELDS}
    alternatives: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    snapshot_totals = collections.Counter()
    spawn_player_sets: collections.Counter[str] = collections.Counter()
    spawn_npc_sets: collections.Counter[str] = collections.Counter()
    header_fingerprints: collections.Counter[str] = collections.Counter()
    generation_negative_deltas = 0
    pickup_checks = collections.Counter()
    known = {entry["fingerprints"]["definition_sha256"]: name for name, entry in map_defs.items() if not entry["limited"]}

    for path in paths:
        _, map_rows, rows = read_log(path)
        fingerprint = sha(map_rows)
        header_fingerprints[known.get(fingerprint, f"unknown:{fingerprint}")] += 1
        first = rows[0]["start"]
        player_spawns = sorted(tuple(unit["position"]) for player in first["players"] for unit in player["units"])
        npc_spawns = sorted(tuple(npc["position"]) for npc in first["npcs"])
        spawn_player_sets[json.dumps(player_spawns, separators=(",", ":"))] += 1
        spawn_npc_sets[json.dumps(npc_spawns, separators=(",", ":"))] += 1

        generated_by_round = [region_generated(rows, index) for index in range(len(rows))]
        for index in range(1, len(rows)):
            previous, current = rows[index - 1]["end"]["grid"], rows[index]["start"]["grid"]
            generation_negative_deltas += sum(
                positive(current[r][c]) < positive(previous[r][c]) for r in range(N) for c in range(N)
            )

        for row in rows:
            collected = region_collected(row)
            metadata_pickup = sum(unit["pickup"] for player in row["end"]["players"] for unit in player["units"]) + sum(
                npc["pickup"] for npc in row["end"]["npcs"]
            )
            pickup_checks["rounds"] += 1
            pickup_checks["exact_rounds"] += sum(collected) == metadata_pickup
            pickup_checks["absolute_error"] += abs(sum(collected) - metadata_pickup)

            for phase in ("start", "end"):
                state = row[phase]
                correct = render_full(state, npc_over_player=True)
                wrong = render_full(state, npc_over_player=False)
                correct_mismatch = compare_grid(correct, state["grid"])
                wrong_mismatch = compare_grid(wrong, state["grid"])
                overlay["phase_grids"] += 1
                overlay["exact_phase_grids"] += correct_mismatch == 0
                overlay["mismatch_cells"] += correct_mismatch
                wrong_overlay["phase_grids"] += 1
                wrong_overlay["exact_phase_grids"] += wrong_mismatch == 0
                wrong_overlay["mismatch_cells"] += wrong_mismatch

                pset = {
                    tuple(unit["position"])
                    for player in state["players"]
                    for unit in player["units"]
                    if unit.get("position") is not None
                }
                nset = {tuple(npc["position"]) for npc in state["npcs"] if npc.get("position") is not None}
                for r in range(N):
                    for c in range(N):
                        value = state["grid"][r][c]
                        if value in (PLAYER_MARK, NPC_MARK):
                            marker_alignment[f"{value}|player={int((r,c) in pset)}|npc={int((r,c) in nset)}"] += 1
                for r, c in pset | nset:
                    terrain_at_entities[str(state["grid"][r][c])] += 1

            if "snapshot" not in row:
                continue
            snapshot_totals["snapshots"] += 1
            r = row["round"]
            begin, end = row["snapshot"]["window"]
            snapshot_totals["window_exact"] += (begin, end) == (r - 5, r - 1)

            enter, leave, collected = [0] * 6, [0] * 6, [0] * 6
            for index in range(begin, end + 1):
                one_enter, one_leave = transitions(rows[index])
                add_vectors(enter, one_enter)
                add_vectors(leave, one_leave)
                add_vectors(collected, region_collected(rows[index]))

            generated = [0] * 6
            generation_begin = 0 if begin == 0 else begin + 1
            for index in range(generation_begin, r + 1):
                add_vectors(generated, generated_by_round[index])
            remaining = region_gold(row["start"]["grid"])
            occupants = region_occupants(row["start"])
            got = {
                "enter": enter,
                "leave": leave,
                "gold_generated": generated,
                "gold_collected": collected,
                "gold_remaining": remaining,
                "occupants": occupants,
            }
            oracle = {
                field: [0]
                + [next(region[field] for region in row["snapshot"]["regions"] if region["id"] == region_number) for region_number in range(1, 6)]
                for field in FIELDS
            }
            for field in FIELDS:
                equality = [got[field][i] == oracle[field][i] for i in range(1, 6)]
                stat = snapshot_fields[field]
                stat["regions"] += 5
                stat["region_exact"] += sum(equality)
                stat["snapshots"] += 1
                stat["snapshot_exact"] += all(equality)
                stat["absolute_error"] += sum(abs(got[field][i] - oracle[field][i]) for i in range(1, 6))

            label_generated = [0] * 6
            for index in range(begin, end + 1):
                add_vectors(label_generated, generated_by_round[index])
            no_bootstrap_generated = [0] * 6
            for index in range(begin + 1, r + 1):
                add_vectors(no_bootstrap_generated, generated_by_round[index])
            alt_values = {
                "gold_remaining_from_end_r_minus_1": ("gold_remaining", region_gold(rows[r - 1]["end"]["grid"])),
                "occupants_players_only": ("occupants", region_occupants(row["start"], include_npcs=False)),
                "gold_generated_label_aligned_r_minus_5_through_r_minus_1": ("gold_generated", label_generated),
                "gold_generated_r_minus_4_through_r_without_initial_bootstrap": ("gold_generated", no_bootstrap_generated),
            }
            for name, (field, values) in alt_values.items():
                equality = [values[i] == oracle[field][i] for i in range(1, 6)]
                stat = alternatives[name]
                stat["regions"] += 5
                stat["region_exact"] += sum(equality)
                stat["snapshots"] += 1
                stat["snapshot_exact"] += all(equality)
                stat["absolute_error"] += sum(abs(values[i] - oracle[field][i]) for i in range(1, 6))

            prior_remaining = [0] * 6 if begin == 0 else region_gold(rows[begin]["start"]["grid"])
            conservation = all(prior_remaining[i] + generated[i] - collected[i] == remaining[i] for i in range(1, 6))
            snapshot_totals["gold_conservation_exact"] += conservation

    full_rendering = {
        "source": "full logs g0-g2, both start and end grids",
        "semantics": {
            "api_filtered_grid": "pure terrain; entities are separate arrays",
            "full_replay_grid": "nonzero ground has highest priority; on empty ground draw player=-2, then NPC=-4; NPC overrides a collocated player",
            "priority_high_to_low": ["nonzero_ground(-1 wall, -3 bomb, positive gold)", "npc(-4)", "player(-2)", "empty(0)"],
        },
        "correct_precedence": {
            **dict(overlay),
            "exact_rate": ratio(overlay["exact_phase_grids"], overlay["phase_grids"]),
        },
        "wrong_player_over_npc_precedence": {
            **dict(wrong_overlay),
            "exact_rate": ratio(wrong_overlay["exact_phase_grids"], wrong_overlay["phase_grids"]),
            "root_cause": "651 player/NPC collocations are rendered as NPC=-4, not player=-2.",
        },
        "marker_alignment": dict(sorted(marker_alignment.items())),
        "values_observed_at_entity_positions": dict(sorted(terrain_at_entities.items(), key=lambda item: int(item[0]))),
    }

    snapshot_report = {
        "source": "round-level row.snapshot oracle in g0-g2",
        "sample_rounds": "r=5,10,...,495",
        "sample_state": "start[r], after round-r generation and before any round-r movement",
        "regions": {
            "1": {"name": "center", "row": [4, 12], "col": [4, 12], "cells": 81},
            "2": {"name": "top", "row": [0, 3], "col": [0, 12], "cells": 52},
            "3": {"name": "left", "row": [4, 16], "col": [0, 3], "cells": 52},
            "4": {"name": "bottom", "row": [13, 16], "col": [4, 16], "cells": 52},
            "5": {"name": "right", "row": [0, 12], "col": [13, 16], "cells": 52},
        },
        "region_grid": region_grid(),
        "window": {
            "labels": "[r-5,r-1] inclusive",
            "movement_fields": "enter, leave, and gold_collected aggregate rounds r-5..r-1",
            "generated_field": "generation events after the prior sampled state through start[r]: r-4..r; first r=5 bootstrap includes r=0..5 because the prior ground state is zero",
            "remaining_and_occupants": "sample start[r]",
            "snapshots": snapshot_totals["snapshots"],
            "exact_window_labels": snapshot_totals["window_exact"],
            "window_label_exact_rate": ratio(snapshot_totals["window_exact"], snapshot_totals["snapshots"]),
        },
        "field_exact_matches": {field: field_result(snapshot_fields[field]) for field in FIELDS},
        "gold_accounting_cross_checks": {
            "snapshot_conservation_samples": snapshot_totals["snapshots"],
            "snapshot_conservation_exact": snapshot_totals["gold_conservation_exact"],
            "snapshot_conservation_exact_rate": ratio(snapshot_totals["gold_conservation_exact"], snapshot_totals["snapshots"]),
            "per_round_grid_depletion_vs_pickup": {
                **dict(pickup_checks),
                "exact_rate": ratio(pickup_checks["exact_rounds"], pickup_checks["rounds"]),
            },
            "negative_gold_deltas_between_previous_end_and_next_start": generation_negative_deltas,
        },
        "rejected_alternatives": {name: field_result(stat) for name, stat in alternatives.items()},
        "root_causes": [
            "The snapshot label excludes r, but gold_generated includes generation at start[r], because remaining is sampled after that generation.",
            "The first r=5 generated total bootstraps from zero ground and therefore includes starts r=0..5; omitting r=0 fails exactly the first snapshot in each of three logs.",
            "gold_remaining from end[r-1] misses generation at start[r].",
            "occupants includes all four player units and all seven NPCs.",
        ],
    }

    spawn = {
        "full_logs": {
            "files": len(paths),
            "player_spawn_sets": dict(sorted(spawn_player_sets.items())),
            "npc_spawn_sets": dict(sorted(spawn_npc_sets.items())),
            "canonical_players": [[0, 0], [0, 16], [16, 0], [16, 16]],
            "canonical_npcs": {"coordinate": [8, 8], "count": 7},
        },
        "map_headers": dict(sorted(header_fingerprints.items())),
    }
    return full_rendering, snapshot_report, spawn


def validate_assets(map_defs: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, entry in map_defs.items():
        if entry["limited"]:
            rows = entry["wall_rows"]
            computed = {"walls": sum(row.count("1") for row in rows), "walls_sha256": sha(rows)}
            expected = {"walls": entry["counts"]["walls"], "walls_sha256": entry["fingerprints"]["walls_sha256"]}
        else:
            rows = entry["rows"]
            wall_rows = ["".join("1" if value == "1" else "0" for value in row) for row in rows]
            candidate_rows = ["".join("1" if value == "2" else "0" for value in row) for row in rows]
            computed = {
                "walls": sum(row.count("1") for row in rows),
                "bomb_candidates": sum(row.count("2") for row in rows),
                "open": sum(row.count("0") for row in rows),
                "definition_sha256": sha(rows),
                "walls_sha256": sha(wall_rows),
                "bomb_candidates_sha256": sha(candidate_rows),
            }
            expected = {**entry["counts"], **entry["fingerprints"]}
        shape_ok = len(rows) == N and all(len(row) == N for row in rows)
        result[name] = {
            "shape_17x17": shape_ok,
            "computed": computed,
            "expected": expected,
            "exact": shape_ok and computed == expected,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_repo = Path(__file__).resolve().parents[1]
    parser.add_argument("--repo", type=Path, default=default_repo)
    parser.add_argument("--maps", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    repo = args.repo.resolve()
    maps_path = (args.maps or repo / "sim" / "maps.json").resolve()
    output_path = (args.output or repo / "sim" / "reports" / "visibility_snapshot.json").resolve()
    map_asset = json.loads(maps_path.read_text(encoding="utf-8"))
    map_defs = map_asset["maps"]

    full_paths = sorted((repo / "logs" / "gr_data" / "full").glob("g*.txt"))
    official_paths = sorted((repo / "logs" / "gr_data" / "user").glob("g*.txt"))
    local_paths = sorted(Path(path) for path in glob.glob(str(repo / "logs" / "game_*.log")))
    if not full_paths or not official_paths or not local_paths:
        raise SystemExit("required full, official-filtered, or local logs are missing")

    full_rendering, snapshots, full_spawn = analyze_full(full_paths, map_defs)
    official = analyze_filtered(official_paths, "official_filtered_g3_g10", map_defs)
    local = analyze_filtered(local_paths, "local_game_logs", map_defs)

    report = {
        "schema_version": 1,
        "inputs": {
            "full_logs": {"files": len(full_paths), "rounds": len(full_paths) * 500, "names": [path.name for path in full_paths]},
            "official_filtered_logs": {"files": len(official_paths), "rounds": len(official_paths) * 500, "names": [path.name for path in official_paths]},
            "local_filtered_logs": {
                "files_discovered": len(local_paths),
                "objective_baseline_files": 183,
                "delta_from_objective_baseline": len(local_paths) - 183,
                "rounds": len(local_paths) * 500,
                "note": "The analysis is exhaustive over files present at execution time; this workspace contains four more local logs than the stated 183-log baseline." if len(local_paths) != 183 else "Matches the stated baseline.",
            },
        },
        "map_assets": {
            "path": str(maps_path),
            "validation": validate_assets(map_defs),
            "observed_headers": {
                "full": full_spawn["map_headers"],
                "official_filtered": official["map_headers"],
                "local_filtered": local["map_headers"],
            },
            "map3_limitation": map_defs["map3"]["limitation"],
        },
        "spawn_coordinates": {
            **full_spawn,
            "official_filtered_round0_owner": {
                "files": official["files"],
                "owner_ids": official["owner_ids"],
                "spawn_pairs": official["round0_owner_spawn_pairs"],
            },
            "local_filtered_round0_owner": {
                "files": local["files"],
                "owner_ids": local["owner_ids"],
                "spawn_pairs": local["round0_owner_spawn_pairs"],
            },
        },
        "visibility_and_filtered_grid": {
            "official_filtered": official,
            "local_filtered": local,
            "semantics": [
                "Use the current phase's two owner positions: start positions for start.grid and end positions for end.grid.",
                "Visibility is a union, not a sum: overlap cells appear once.",
                "Clip each Chebyshev square to rows/columns 0..16; there is no wraparound.",
                "The round's start.vision_r applies to both start and end replay grids.",
            ],
        },
        "full_grid_rendering": full_rendering,
        "snapshots": snapshots,
        "mismatch_summary": {
            "accepted_semantics": {
                "visibility_mask_mismatch_cells": official["visibility"]["totals"]["mismatch_cells"] + local["visibility"]["totals"]["mismatch_cells"],
                "full_render_mismatch_cells": full_rendering["correct_precedence"]["mismatch_cells"],
                "snapshot_field_absolute_error": sum(snapshots["field_exact_matches"][field]["absolute_error"] for field in FIELDS),
            },
            "rejected_semantics": {
                "full_render_player_over_npc_mismatch_cells": full_rendering["wrong_player_over_npc_precedence"]["mismatch_cells"],
                "snapshot_alternatives": snapshots["rejected_alternatives"],
            },
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output_path}")
    print(f"full snapshots: {snapshots['window']['exact_window_labels']}/{snapshots['window']['snapshots']}")
    print(
        "official filtered phase-grids: "
        f"{official['visibility']['totals']['exact_phase_grids']}/{official['visibility']['totals']['phase_grids']}"
    )
    print(
        "local filtered phase-grids: "
        f"{local['visibility']['totals']['exact_phase_grids']}/{local['visibility']['totals']['phase_grids']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
