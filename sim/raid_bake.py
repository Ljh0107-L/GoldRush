#!/usr/bin/env python3
"""Bake outer-arm hotspot observation stations and wall-safe routes.

This is a design-time tool.  It deliberately emits no fallback for map3 because
that map has wall data only: its token-2 hotspot metadata is unavailable.
"""

from __future__ import annotations

import argparse
from collections import deque
from itertools import combinations
import json
from pathlib import Path
from typing import Iterable

GRID_SIZE = 17
ANCHORS = ((6, 8), (11, 8))
ACTION_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1))
ACTION_NAMES = "UDLR"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPS = ROOT / "sim" / "maps.json"


def region_id(row: int, col: int) -> int:
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    if row <= 12 and col >= 13:
        return 5
    raise AssertionError((row, col))


def chebyshev(left: tuple[int, int], right: tuple[int, int]) -> int:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def legal_cells(rows: list[str]) -> list[tuple[int, int]]:
    return [
        (row, col)
        for row in range(GRID_SIZE)
        for col in range(GRID_SIZE)
        if rows[row][col] != "1"
    ]


def covered(station: tuple[int, int], hotspots: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(cell for cell in hotspots if chebyshev(station, cell) <= 2))


def bfs_route(
    rows: list[str], start: tuple[int, int], target: tuple[int, int]
) -> tuple[int, ...]:
    queue = deque([start])
    previous: dict[tuple[int, int], tuple[tuple[int, int], int] | None] = {start: None}
    while queue:
        current = queue.popleft()
        if current == target:
            break
        for action, delta in enumerate(ACTION_DELTAS):
            nxt = (current[0] + delta[0], current[1] + delta[1])
            if not (0 <= nxt[0] < GRID_SIZE and 0 <= nxt[1] < GRID_SIZE):
                continue
            if rows[nxt[0]][nxt[1]] == "1" or nxt in previous:
                continue
            previous[nxt] = (current, action)
            queue.append(nxt)
    if target not in previous:
        raise ValueError("unreachable route %r -> %r" % (start, target))
    reverse: list[int] = []
    current = target
    while previous[current] is not None:
        parent, action = previous[current]  # type: ignore[misc]
        reverse.append(action)
        current = parent
    return tuple(reversed(reverse))


def route_record(rows: list[str], start: tuple[int, int], target: tuple[int, int]) -> dict[str, object]:
    actions = bfs_route(rows, start, target)
    return {
        "from": list(start),
        "to": list(target),
        "steps": len(actions),
        "rounds_at_3_steps": (len(actions) + 2) // 3,
        "actions": list(actions),
        "directions": "".join(ACTION_NAMES[action] for action in actions),
    }


def choose_stations(
    rows: list[str], hotspots: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], tuple[tuple[int, int], tuple[int, int]], int]:
    candidates = legal_cells(rows)
    cover = {cell: frozenset(covered(cell, hotspots)) for cell in candidates}

    # Token-2 cells have the same fitted generation weight.  Therefore the
    # maximum-weight one-station solution is exactly maximum hotspot coverage.
    single = min(
        candidates,
        key=lambda cell: (
            -len(cover[cell]),
            sum(chebyshev(cell, hotspot) for hotspot in cover[cell]),
            min(len(bfs_route(rows, anchor, cell)) for anchor in ANCHORS),
            cell,
        ),
    )

    all_hotspots = frozenset(hotspots)
    minimum_count = 0
    for count in range(1, len(hotspots) + 1):
        if any(
            frozenset().union(*(cover[cell] for cell in group)) == all_hotspots
            for group in combinations(candidates, count)
        ):
            minimum_count = count
            break
    if minimum_count > 2:
        raise AssertionError("arm requires %d stations, not a two-station plan" % minimum_count)

    pairs = [
        pair
        for pair in combinations(candidates, 2)
        if cover[pair[0]] | cover[pair[1]] == all_hotspots
    ]
    pair = min(
        pairs,
        key=lambda cells: (
            sum(min(len(bfs_route(rows, anchor, cell)) for anchor in ANCHORS) for cell in cells),
            len(bfs_route(rows, cells[0], cells[1])),
            cells,
        ),
    )
    return single, pair, minimum_count


def bake_map(map_name: str, definition: dict[str, object]) -> dict[str, object]:
    if bool(definition.get("limited")):
        return {
            "status": "unsupported_no_hotspot_metadata",
            "policy": "central_only_no_raid",
            "reason": "map3 has walls but no token-2 cells; map1/map2 hotspot layouts are not symmetry-equivalent",
        }
    rows = [str(row) for row in definition["rows"]]  # type: ignore[index]
    hotspots_by_arm = {
        arm: tuple(
            (row, col)
            for row in range(GRID_SIZE)
            for col in range(GRID_SIZE)
            if rows[row][col] == "2" and region_id(row, col) == arm
        )
        for arm in range(2, 6)
    }
    arms: dict[str, object] = {}
    for arm, hotspots in hotspots_by_arm.items():
        if len(hotspots) != 5:
            raise AssertionError("%s arm %d has %d hotspots" % (map_name, arm, len(hotspots)))
        single, pair, minimum_count = choose_stations(rows, hotspots)
        stations = (single,) + pair
        arms[str(arm)] = {
            "hotspots": [list(cell) for cell in hotspots],
            "minimum_station_count": minimum_count,
            "single": {
                "station": list(single),
                "covered_hotspots": [list(cell) for cell in covered(single, hotspots)],
                "covered_weight": len(covered(single, hotspots)),
            },
            "double": {
                "stations": [list(cell) for cell in pair],
                "covered_hotspots": [
                    [list(cell) for cell in covered(station, hotspots)] for station in pair
                ],
                "between": route_record(rows, pair[0], pair[1]),
            },
            "routes": {
                "anchor_6_8": [route_record(rows, ANCHORS[0], station) for station in stations],
                "anchor_11_8": [route_record(rows, ANCHORS[1], station) for station in stations],
            },
        }
    return {"status": "baked", "arms": arms}


def bake(maps_path: Path = DEFAULT_MAPS) -> dict[str, object]:
    payload = json.loads(maps_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "action_codes": {"0": "up", "1": "down", "2": "left", "3": "right"},
        "vision_radius": 2,
        "actions_per_round": 3,
        "anchors": [list(anchor) for anchor in ANCHORS],
        "weight_model": "all token-2 hotspots have equal fitted weight",
        "maps": {
            name: bake_map(name, definition)
            for name, definition in payload["maps"].items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--maps", type=Path, default=DEFAULT_MAPS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = bake(args.maps)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
