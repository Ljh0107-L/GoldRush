#!/usr/bin/env python3
"""Generate axis-symmetric 17x17 maps for the unfamiliar-map robustness audit.

Preliminary terrain is expected to be symmetric across the horizontal axis,
the vertical axis, or both.  Every generated wall mask therefore satisfies at
least one of those symmetries while still exercising a distinct geometry.

Each generated map targets one specific assumption in the construct:

``dense``       far more walls than map3 (the densest known map, 78) -- stresses
                routing, the blocked-step self-heal and the escape path.
``sealed``      a wall ring around the central 9x9 with only four narrow gates,
                attacking the central-generation-peak income model directly.
``anchorwall``  walls placed exactly on the two hard-coded anchors (6,8)/(11,8)
                to force ``fixAnchor`` to relocate both of them.
``lr_only``     left-right symmetry without top-bottom symmetry.
``sparse``      almost no walls -- the opposite extreme from ``dense``.
``corridor``    long horizontal bands forming narrow corridors, maximising the
                blocked-move rate that map1's wall pockets already cause.
``mimic1/2/3``  identical to map1/2/3 across the outer band but with a different,
                doubly symmetric centre.

Invariants every generated map must satisfy, or generation fails loudly:
  * both player spawns (0,0) and (16,16) are traversable
  * both OPPONENT spawns (0,16) and (16,0) are traversable -- the engine refuses
    a scenario in which any unit starts on a wall, so walling these two cells
    yields terrain that dies at setup with ``player occupies a wall`` rather
    than a usable audit case.  (Added 8.10; output-identical for the nine maps
    already in ``sim/maps_unknown.json``, none of which walls either cell.)
  * the NPC spawn (8,8) is traversable
  * all traversable cells form a single connected component (4-neighbour)
  * the wall mask is top-bottom symmetric, left-right symmetric, or both
  * the terrain differs from map1/map2/map3
"""
from __future__ import annotations

import collections
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

N = 17
SPAWNS = ((0, 0), (16, 16))
OPP_SPAWNS = ((0, 16), (16, 0))
NPC_SPAWN = (8, 8)
ANCHORS = ((6, 8), (11, 8))


def blank() -> list[list[str]]:
    return [["0"] * N for _ in range(N)]


def as_rows(grid: list[list[str]]) -> list[str]:
    return ["".join(row) for row in grid]


def wall_count(rows: list[str]) -> int:
    return sum(row.count("1") for row in rows)


def wall_rows(rows: list[str]) -> list[str]:
    """Project terrain tokens to a binary wall mask."""
    return ["".join("1" if cell == "1" else "0" for cell in row) for row in rows]


def symmetry_axes(rows: list[str]) -> list[str]:
    walls = wall_rows(rows)
    axes = []
    if walls == walls[::-1]:
        axes.append("up_down")
    if all(row == row[::-1] for row in walls):
        axes.append("left_right")
    return axes


def mirror_left_right(grid: list[list[str]]) -> None:
    for r in range(N):
        for c in range(N // 2):
            if grid[r][c] == "1" or grid[r][N - 1 - c] == "1":
                grid[r][c] = grid[r][N - 1 - c] = "1"


def mirror_up_down(grid: list[list[str]]) -> None:
    for r in range(N // 2):
        for c in range(N):
            if grid[r][c] == "1" or grid[N - 1 - r][c] == "1":
                grid[r][c] = grid[N - 1 - r][c] = "1"


def connected(rows: list[str]) -> bool:
    floor = {(r, c) for r in range(N) for c in range(N) if rows[r][c] != "1"}
    if not floor:
        return False
    start = next(iter(sorted(floor)))
    seen = {start}
    queue = collections.deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            cell = (r + dr, c + dc)
            if cell in floor and cell not in seen:
                seen.add(cell)
                queue.append(cell)
    return seen == floor


def protect(grid: list[list[str]]) -> None:
    """Keep the cells the engine requires traversable open.

    All four corners matter, not just ours: the engine seats the opponent at
    (0,16)/(16,0) and rejects the scenario if any unit spawns on a wall.
    """
    for r, c in (*SPAWNS, *OPP_SPAWNS, NPC_SPAWN):
        grid[r][c] = "0"


def make_dense() -> list[str]:
    grid = blank()
    # Six mirrored wall bands, each with three aligned gates: 84 walls while
    # every horizontal chamber remains connected through columns 4/8/12.
    for r in (2, 4, 6, 10, 12, 14):
        for c in range(N):
            if c not in (4, 8, 12):
                grid[r][c] = "1"
    protect(grid)
    return as_rows(grid)


def make_sealed() -> list[str]:
    grid = blank()
    lo, hi = 4, 12
    for i in range(lo, hi + 1):
        grid[lo][i] = grid[hi][i] = "1"
        grid[i][lo] = grid[i][hi] = "1"
    for gate in (8,):           # four narrow gates, one per side
        grid[lo][gate] = grid[hi][gate] = "0"
        grid[gate][lo] = grid[gate][hi] = "0"
    protect(grid)
    return as_rows(grid)


def make_anchorwall() -> list[str]:
    grid = blank()
    for r, c in ANCHORS:        # both hard-coded anchors become walls
        grid[r][c] = "1"
    for r, c in ANCHORS:        # ring them so the nearest free cell is 2 away
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            grid[r + dr][c + dc] = "1"
    for r in range(3, 14):      # some extra structure so it is not near-empty
        grid[r][3] = grid[r][13] = "1"
    for r in range(3, 14, 4):
        grid[r][3] = grid[r][13] = "0"
    protect(grid)
    return as_rows(grid)


def make_lr_only() -> list[str]:
    grid = blank()
    for r in range(1, 16):      # irregular half-map, reflected left-right
        grid[r][(r * 3) % 7 + 1] = "1"
        if r % 3 == 0:
            grid[r][(r * 5) % 6 + 1] = "1"
    for c in range(2, 7):
        grid[12][c] = "1"
    mirror_left_right(grid)
    protect(grid)
    return as_rows(grid)


def make_sparse() -> list[str]:
    grid = blank()
    for r, c in (
        (2, 2), (2, 14), (14, 2), (14, 14),
        (5, 7), (5, 9), (11, 7), (11, 9),
    ):
        grid[r][c] = "1"
    protect(grid)
    return as_rows(grid)


def make_corridor() -> list[str]:
    grid = blank()
    for band, r in enumerate(range(2, 15, 3)):
        for c in range(N):
            grid[r][c] = "1"
        if band % 2 == 0:
            grid[r][8] = "0"
        else:
            grid[r][3] = grid[r][13] = "0"
    protect(grid)
    return as_rows(grid)


def make_mimic(known_rows: list[str]) -> list[str]:
    """Match a known map on the outer band and replace its centre symmetrically."""
    grid = [list(row) for row in known_rows]
    for r in range(5, 12):                  # rewrite only the interior
        for c in range(5, 12):
            grid[r][c] = "0"
    for r, c in (
        (6, 6), (6, 10), (10, 6), (10, 10),
        (7, 7), (7, 9), (9, 7), (9, 9),
    ):
        grid[r][c] = "1"
    protect(grid)
    return as_rows(grid)


def main() -> int:
    from sim.scenario import MapDefinition

    known = {name: MapDefinition.by_name(name) for name in ("map1", "map2", "map3")}
    known_rows = {name: list(d.rows) for name, d in known.items()}

    built = {
        "dense": make_dense(),
        "sealed": make_sealed(),
        "anchorwall": make_anchorwall(),
        "lr_only": make_lr_only(),
        "sparse": make_sparse(),
        "corridor": make_corridor(),
        "mimic1": make_mimic(known_rows["map1"]),
        "mimic2": make_mimic(known_rows["map2"]),
        "mimic3": make_mimic(known_rows["map3"]),
    }

    payload = {
        "schema_version": 1,
        "grid_size": N,
        "cell_codes": {"0": "open", "1": "wall", "2": "bomb_candidate"},
        "purpose": "unfamiliar-map robustness audit; NOT official terrain",
        "maps": {},
    }
    failures = []
    for name, rows in built.items():
        if len(rows) != N or any(len(r) != N for r in rows):
            failures.append("%s: not 17x17" % name)
            continue
        for r, c in (*SPAWNS, *OPP_SPAWNS, NPC_SPAWN):
            if rows[r][c] == "1":
                failures.append("%s: required cell (%d,%d) is a wall" % (name, r, c))
        if not connected(rows):
            failures.append("%s: traversable cells are not connected" % name)
        axes = symmetry_axes(rows)
        if not axes:
            failures.append("%s: wall mask has no axis symmetry" % name)
        for kname, krows in known_rows.items():
            if wall_rows(rows) == wall_rows(krows):
                failures.append("%s: identical to %s" % (name, kname))
        payload["maps"][name] = {
            "limited": False,
            "source": {"kind": "synthetic", "generator": "sim/make_unknown_maps.py"},
            "rows": rows,
            "counts": {"wall": wall_count(rows)},
            "wall_symmetry": axes,
        }

    if failures:
        for line in failures:
            print("INVALID  " + line, file=sys.stderr)
        return 1

    out = ROOT / "sim" / "maps_unknown.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote %s" % out.relative_to(ROOT))
    print("%-12s %5s  %-24s  %s" % ("map", "walls", "wall symmetry", "note"))
    for name in ("map1", "map2", "map3"):
        print("%-12s %5d  %-24s  (known reference)" % (
            name, wall_count(known_rows[name]), ",".join(symmetry_axes(known_rows[name]))))
    for name, rows in built.items():
        same_outer = ("outer band == " + name.replace("mimic", "map")
                      if name.startswith("mimic") else "")
        print("%-12s %5d  %-24s  %s" % (
            name, wall_count(rows), ",".join(symmetry_axes(rows)), same_outer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
