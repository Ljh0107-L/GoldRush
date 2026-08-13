#!/usr/bin/env python3
"""Generate symmetric 17x17 maps for the unfamiliar-map robustness audit.

Official terrain is guaranteed symmetric under at least one of: up-down mirror,
left-right mirror, or 180-degree central rotation (owner-confirmed 2026-08-13).
Central-only and 90-degree-rotation-only maps carry NO axis mirror yet are
legal -- and the two reconstructed finals photos (``sim/maps_final_photos.json``:
photo_1 = central-only, photo_2 = rot90+central) prove the organisers ship them.
An earlier revision of this generator rejected exactly those classes.

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
``ud_gate``     up-down mirror ONLY: a full row-8 wall with off-centre gates --
                the mirror axis passes BETWEEN the construct's two anchors.
``cen_zigzag``  central 180-degree rotation ONLY (chiral Z/S blocks, no mirror);
                the class finals photo_1 belongs to.  Carries a token-2 set.
``pinwheel``    90-degree rotation ONLY (chiral arms, no mirror); the class
                finals photo_2 belongs to.  Token-2 set is rot90-symmetric.
``diag_stair``  both diagonal mirrors (+ central, no axis mirror): staircase
                walls -- legal via central symmetry, previously zero coverage.

Invariants every generated map must satisfy, or generation fails loudly:
  * both player spawns (0,0) and (16,16) are traversable
  * both OPPONENT spawns (0,16) and (16,0) are traversable -- the engine refuses
    a scenario in which any unit starts on a wall, so walling these two cells
    yields terrain that dies at setup with ``player occupies a wall`` rather
    than a usable audit case.  (Added 8.10; output-identical for the nine maps
    already in ``sim/maps_unknown.json``, none of which walls either cell.)
  * the NPC spawn (8,8) is traversable
  * all traversable cells form a single connected component (4-neighbour)
  * the wall mask satisfies >=1 of: up-down mirror, left-right mirror,
    central 180-degree rotation (the official guarantee)
  * each family lands in exactly the symmetry class it claims (fail loudly)
  * token-2 cells, when present, sit on open cells only
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
    """Full symmetry classification of the wall mask (not just axis mirrors)."""
    walls = wall_rows(rows)
    S = {(r, c) for r in range(N) for c in range(N) if walls[r][c] == "1"}
    axes = []
    if all((N - 1 - r, c) in S for r, c in S):
        axes.append("up_down")
    if all((r, N - 1 - c) in S for r, c in S):
        axes.append("left_right")
    if all((N - 1 - r, N - 1 - c) in S for r, c in S):
        axes.append("central")
    if all((c, N - 1 - r) in S for r, c in S):
        axes.append("rot90")
    if all((c, r) in S for r, c in S):
        axes.append("diag")
    if all((N - 1 - c, N - 1 - r) in S for r, c in S):
        axes.append("antidiag")
    return axes


GUARANTEE = ("up_down", "left_right", "central")


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


def _closure(grid: list[list[str]], seeds: set[tuple[int, int]],
             transforms) -> None:
    """Union the seed set with its images under the given transforms."""
    cells = set(seeds)
    frontier = set(seeds)
    while frontier:
        nxt = set()
        for r, c in frontier:
            for t in transforms:
                cell = t(r, c)
                if cell not in cells:
                    cells.add(cell)
                    nxt.add(cell)
        frontier = nxt
    for r, c in cells:
        grid[r][c] = "1"


def _place_tokens(rows: list[str], cells: list[tuple[int, int]]) -> list[str]:
    grid = [list(r) for r in rows]
    for r, c in cells:
        if grid[r][c] == "1":
            raise SystemExit("token-2 cell (%d,%d) collides with a wall" % (r, c))
        if (r, c) in (*SPAWNS, *OPP_SPAWNS, NPC_SPAWN):
            raise SystemExit("token-2 cell (%d,%d) is a protected spawn" % (r, c))
        grid[r][c] = "2"
    return as_rows(grid)


def make_ud_gate() -> list[str]:
    """Up-down mirror ONLY. Mirror axis = row 8, between the two anchors.

    Row 8 is a full wall with gates at columns 3 and 10 (an off-centre pair so
    left-right symmetry is broken; ``protect`` re-opens (8,8) for the NPC spawn,
    adding a centre gate).  Mirrored band clutter above/below keeps the halves
    structured without sealing them.
    """
    grid = blank()
    for c in range(N):
        grid[8][c] = "1"
    grid[8][3] = grid[8][10] = "0"
    for c in (1, 2, 5, 6, 11, 14):        # LR-asymmetric clutter, mirrored UD
        grid[4][c] = grid[12][c] = "1"
    for r in (2, 3):
        grid[r][7] = grid[N - 1 - r][7] = "1"
        grid[r][12] = grid[N - 1 - r][12] = "1"
    protect(grid)
    return as_rows(grid)


def make_cen_zigzag() -> list[str]:
    """Central 180-degree rotation ONLY (chiral, no mirror) -- photo_1's class."""
    grid = blank()
    seeds = {(3, 3), (3, 4), (4, 4), (4, 5),          # Z block
             (2, 10), (3, 10), (4, 10), (4, 11),      # L block
             (7, 2), (7, 3), (6, 3), (6, 4),          # S block
             (5, 7), (6, 7), (7, 7), (7, 8),          # bar with foot
             (11, 1), (11, 2), (12, 2)}               # lower hook
    _closure(grid, seeds, [lambda r, c: (N - 1 - r, N - 1 - c)])
    protect(grid)
    rows = as_rows(grid)
    tokens = [(1, 4), (2, 7), (4, 13), (5, 11), (6, 1), (8, 5), (9, 12),
              (10, 3), (12, 8), (13, 10), (14, 2), (15, 9), (1, 12), (3, 15),
              (6, 15), (10, 11), (12, 4), (14, 15), (15, 1), (8, 2)]
    return _place_tokens(rows, tokens)                 # 弹位不对称(官方 map1/2 同款)


def make_pinwheel() -> list[str]:
    """90-degree rotation ONLY (chiral arms, no mirror) -- photo_2's class."""
    grid = blank()
    arm = {(2, 6), (3, 6), (4, 6), (5, 6), (5, 7), (2, 7)}   # hooked arm, chiral
    rot = lambda r, c: (c, N - 1 - r)
    _closure(grid, arm, [rot])
    protect(grid)
    rows = as_rows(grid)
    seed_tokens = [(1, 3), (4, 4), (6, 9), (3, 11), (7, 6)]
    tokens = []
    for r, c in seed_tokens:                           # 弹位跟随 rot90(官方 map3 同款)
        for _ in range(4):
            tokens.append((r, c))
            r, c = c, N - 1 - r
    return _place_tokens(rows, sorted(set(tokens)))


def make_diag_stair() -> list[str]:
    """Both diagonal mirrors (+ central); NO axis mirror. Staircase walls.

    Legal via the central-rotation clause of the guarantee; the class had zero
    coverage anywhere in the suite.
    """
    grid = blank()
    seeds = {(2, 5), (3, 6), (4, 7), (1, 9), (1, 12), (6, 11), (7, 12)}
    d1 = lambda r, c: (c, r)
    d2 = lambda r, c: (N - 1 - c, N - 1 - r)
    _closure(grid, seeds, [d1, d2])
    protect(grid)
    rows = as_rows(grid)
    tokens = [(1, 1), (2, 8), (5, 13), (6, 2), (8, 6), (9, 15), (11, 4),
              (13, 9), (14, 14), (15, 6), (1, 14), (4, 12), (7, 1), (10, 8),
              (12, 12), (13, 3), (15, 11), (3, 13), (5, 5), (10, 1)]
    return _place_tokens(rows, tokens)                 # 弹位不对称



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
        "ud_gate": make_ud_gate(),
        "cen_zigzag": make_cen_zigzag(),
        "pinwheel": make_pinwheel(),
        "diag_stair": make_diag_stair(),
    }
    # 每个新家族必须精确落在它声称的对称类(多一个少一个都算生成失败)
    expected_class = {
        "ud_gate": ["up_down"],
        "cen_zigzag": ["central"],
        "pinwheel": ["central", "rot90"],
        "diag_stair": ["antidiag", "central", "diag"],
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
        if not any(a in axes for a in GUARANTEE):
            failures.append("%s: violates the symmetry guarantee (has %s)" % (name, axes))
        if name in expected_class and sorted(axes) != expected_class[name]:
            failures.append("%s: class %s != declared %s" % (name, sorted(axes), expected_class[name]))
        for kname, krows in known_rows.items():
            if wall_rows(rows) == wall_rows(krows):
                failures.append("%s: identical to %s" % (name, kname))
        payload["maps"][name] = {
            "limited": False,
            "source": {"kind": "synthetic", "generator": "sim/make_unknown_maps.py"},
            "rows": rows,
            "counts": {"wall": wall_count(rows),
                       "bomb_candidate": sum(r.count("2") for r in rows)},
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
