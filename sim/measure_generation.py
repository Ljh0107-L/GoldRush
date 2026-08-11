#!/usr/bin/env python3
"""Measure gold generation from full-information game logs, in ONE stated caliber,
and compare platform truth against the simulator.

Inputs are logs in the platform's full-information format, used for both sides:
line 0 is a JSON metadata object, line 1 is a JSON 17x17 array of token strings
('0' open, '1' wall, '2' outer hotspot) which is the map ground truth FOR THAT
LOG, and every later line is one JSON round object with ``round``, ``start`` and
``end``, each phase carrying a 17x17 integer ``grid``.  Grid codes: -5 fog,
-1 wall, -2 player unit, -3 bomb, -4 npc, 0 empty ground, >0 gold on the ground.

Caliber
=======
observation unit
    a (cell, round-transition) pair.  Transition ``r -> r+1`` compares
    ``rounds[r]['end']['grid']`` against ``rounds[r+1]['start']['grid']``.
generation event
    a positive increment of the gold lying on that cell across the transition,
    every negative code read as zero gold.
filter modes (both are computed in the same pass and reported side by side)
    ``loose``   drop the pair if either side is -5 (fog) or -1 (wall).  A cell
                holding a unit/bomb/npc marker still counts as observed with
                zero gold.  This is the original filter and it is the PRIMARY
                one for any platform-versus-simulator comparison, for a reason
                that is not obvious: the platform's own fog-filtered grid never
                marks actors at all (measured actor-marker share 0.00%, only
                bombs appear as -3), while a simulator grid marks units (-2) and
                npcs (-4).  A cell that cannot generate because somebody stands
                on it therefore stays inside the loose denominator on BOTH sides,
                which is what reality does, whereas the strict filter removes
                roughly 11% of simulator observations against roughly 4% of
                platform observations and silently inflates the simulator's
                rates.  The printed ``strict drops`` counters make the asymmetry
                visible.
    ``strict``  additionally require BOTH sides to be >= 0.  Useful as a
                within-side diagnostic -- it estimates the law conditional on the
                cell being free, and it removes the false events an actor or bomb
                marker can produce by hiding pre-existing gold and then moving
                away -- but it is NOT comparable across the two sides here.
buckets (built from the log's own line-1 token map; wall cells excluded everywhere)
    ``CTR``   Chebyshev distance from (8, 8) <= 4
    ``OUT0``  distance >= 5 and token '0'
    ``OUT2``  distance >= 5 and token '2'  (outer hotspot)
aggregations (both are reported)
    ``pooled``    1000 * sum(amount) / sum(obs) over the bucket, and its per-game
                  extrapolation ``rate * cells * 500``.  This is a
                  COVERAGE-WEIGHTED average: it is only comparable between two
                  sides whose coverage pattern is the same.  It is kept because
                  it is the original number.
    ``per_cell``  sum over the bucket's cells of (cell amount / cell obs) * 500.
                  Independent of the coverage pattern, hence the PRIMARY per-game
                  number.  Platform coverage is uneven (per-cell observations
                  span roughly 870..2830) while the simulator logs all 289 cells
                  of every round, so for a bucket with an internal rate gradient
                  (CTR carries a ~3.4x centripetal gradient) the two
                  aggregations disagree by construction, not by accident.
per-bucket single-event mean amount and the full integer amount histogram are
reported as well; the histogram is what exposes the two-component structure of
the generator (ordinary values 1..11 versus rich values 16..37).

Known biases (also printed in the report header)
================================================
* the four cells occupied by the two probe units and the two sparring units
  never generate, so every measured RATE, and therefore every per-game gold
  total, runs 2-3% low.  Single-event AMOUNTS are unbiased.
* ``per_cell`` skips cells with zero observations, i.e. it treats them as if
  they generated nothing; the number of such cells is printed in the coverage
  annex so the reader can size that hole.
* everything is reported per map.  Cross-map pooling is deliberately not
  offered: the maps differ in wall count and in hotspot placement, so a pooled
  cross-map number is uninterpretable in this project.
* each side's bucket cell count is taken from ITS OWN line-1 token map, so a sim
  map that lost its hotspots reports an empty (n/a) OUT2 bucket and carries
  those 20 cells inside OUT0, rather than being scored against the platform's
  bucket sizes.  The throwaway prototype scored the sim with the platform's cell
  counts, so its map3 sim numbers read "OUT0 4393 / OUT2 0" where this tool
  reads "OUT0 4929 / OUT2 n/a"; the map1 and map2 numbers are unaffected because
  there the two token maps are identical.

Usage
=====
    python3 sim/measure_generation.py --sim-logs /tmp/master/simlogs
    python3 sim/measure_generation.py --platform-logs '/tmp/master/plogs/*.log' \
        --sim-logs '/tmp/master/simlogs/map*/game_*.log' --strict --json /tmp/gen.json
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import glob
import json
import os
import pathlib
import re
import sys
import textwrap
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

GRID_SIZE = 17
CENTRE = (8, 8)
CENTRAL_RADIUS = 4
ROUNDS_PER_GAME = 500

FOG = -5
WALL = -1
PLAYER = -2
BOMB = -3
NPC = -4

WALL_TOKEN = "1"
HOTSPOT_TOKEN = "2"
OPEN_TOKEN = "0"

BUCKETS: Tuple[str, ...] = ("CTR", "OUT0", "OUT2")
FILTERS: Tuple[str, ...] = ("loose", "strict")
SPLIT_TOKENS: Tuple[str, ...] = (OPEN_TOKEN, HOTSPOT_TOKEN)

DEFAULT_PLATFORM_GLOB = "/tmp/master/plogs/*.log"
DEFAULT_TOKEN_MAPS = pathlib.Path(__file__).resolve().parent / "real_token_maps.json"

MAP_ID_IN_NAME = re.compile(r"_map(\d+)_")
MAP_ID_ANYWHERE = re.compile(r"map(\d+)")
MAP_KEY = re.compile(r"^[0-9]+$")

MEAN_BAND = (0.9, 1.1)
GOLD_TOLERANCE = 0.15

NA = "n/a"
NAN = float("nan")

BIAS_NOTE = (
    "the four cells occupied by the two probe units and the two sparring units never generate,\n"
    "                so every measured rate (and every per-game gold total) runs 2-3% low; "
    "single-event amounts are unbiased."
)


# --------------------------------------------------------------------------- #
# geometry and log loading
# --------------------------------------------------------------------------- #


def chebyshev(row: int, col: int) -> int:
    """Chebyshev (king-move) distance from the board centre (8, 8)."""
    return max(abs(row - CENTRE[0]), abs(col - CENTRE[1]))


def bucket_of(row: int, col: int, token: str) -> Optional[str]:
    """Bucket name for a cell, or None for wall cells (excluded everywhere)."""
    if token == WALL_TOKEN:
        return None
    if chebyshev(row, col) <= CENTRAL_RADIUS:
        return "CTR"
    return "OUT2" if token == HOTSPOT_TOKEN else "OUT0"


def bucket_members(token_map: Sequence[Sequence[str]]) -> Dict[str, List[Tuple[int, int]]]:
    """Cell list of every bucket for one token map."""
    members: Dict[str, List[Tuple[int, int]]] = {bucket: [] for bucket in BUCKETS}
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            bucket = bucket_of(row, col, token_map[row][col])
            if bucket is not None:
                members[bucket].append((row, col))
    return members


def token_members(token_map: Sequence[Sequence[str]]) -> Dict[str, List[Tuple[int, int]]]:
    """Cell list per token value, walls excluded, both rings pooled together."""
    members: Dict[str, List[Tuple[int, int]]] = {token: [] for token in SPLIT_TOKENS}
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            token = token_map[row][col]
            if token == WALL_TOKEN:
                continue
            members.setdefault(token, []).append((row, col))
    return members


def cells_with_token(token_map: Sequence[Sequence[str]], token: str) -> List[Tuple[int, int]]:
    """Sorted cell list carrying ``token``."""
    return [
        (row, col)
        for row in range(GRID_SIZE)
        for col in range(GRID_SIZE)
        if token_map[row][col] == token
    ]


def infer_map_id(path: str) -> int:
    """Map id of a log: ``_map<N>_`` in the file name, else the last ``map<N>`` in the path."""
    name = pathlib.Path(path).name
    hit = MAP_ID_IN_NAME.search(name)
    if hit:
        return int(hit.group(1))
    trail = MAP_ID_ANYWHERE.findall(str(path))
    if trail:
        return int(trail[-1])
    raise ValueError(
        "cannot infer a map id from %r: expected a '_map<N>_' fragment in the file "
        "name or a 'map<N>' directory in the path" % path
    )


def normalise_token_rows(rows: Any, where: str) -> List[List[str]]:
    """Normalise one 17x17 token map.

    Both shapes seen in this project are accepted: a row given as a sequence of
    17 token strings (the shape of a log's line 1), and a row given as one
    17-character string such as ``"00012200000021000"`` (the compact shape used
    by ``sim/real_token_maps.json``).  Anything else is a hard error.
    """
    if len(rows) != GRID_SIZE:
        raise ValueError("%s has %d rows, expected %d" % (where, len(rows), GRID_SIZE))
    normalised: List[List[str]] = []
    for index, row in enumerate(rows):
        tokens = [str(entry) for entry in row]
        if len(tokens) != GRID_SIZE:
            raise ValueError(
                "%s row %d has %d tokens, expected %d" % (where, index, len(tokens), GRID_SIZE)
            )
        normalised.append(tokens)
    return normalised


def load_log(path: str) -> Tuple[Mapping[str, Any], List[List[str]], List[Mapping[str, Any]]]:
    """Read one log exactly once: metadata, line-1 token map, rounds sorted by round."""
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("%s has fewer than 3 non-empty lines, not a full-information log" % path)
    metadata = json.loads(lines[0])
    tokens = normalise_token_rows(json.loads(lines[1]), "%s line 1" % path)
    rounds = [json.loads(line) for line in lines[2:]]
    rounds.sort(key=lambda entry: entry["round"])
    return metadata, tokens, rounds


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class SideScan:
    """Everything one side (platform or sim) contributes, from a single read pass."""

    label: str
    files: List[str] = dataclasses.field(default_factory=list)
    tokens: Dict[int, List[List[str]]] = dataclasses.field(default_factory=dict)
    cells: Dict[str, Dict[Tuple[int, int, int], List[int]]] = dataclasses.field(default_factory=dict)
    hist: Dict[str, Dict[Tuple[int, str], "collections.Counter[int]"]] = dataclasses.field(default_factory=dict)
    games: "collections.Counter[int]" = dataclasses.field(default_factory=collections.Counter)
    transitions: "collections.Counter[int]" = dataclasses.field(default_factory=collections.Counter)
    skipped_transitions: int = 0
    wall_token_observations: int = 0
    strict_dropped_obs: int = 0
    strict_dropped_events: int = 0
    token_conflicts: List[str] = dataclasses.field(default_factory=list)

    def counters(self, mode: str) -> Dict[Tuple[int, int, int], List[int]]:
        return self.cells[mode]

    def map_ids(self) -> List[int]:
        return sorted(self.tokens)


def _new_side(label: str, files: Sequence[str]) -> SideScan:
    side = SideScan(label=label, files=list(files))
    for mode in FILTERS:
        side.cells[mode] = collections.defaultdict(lambda: [0, 0, 0])
        side.hist[mode] = collections.defaultdict(collections.Counter)
    return side


def scan_side(label: str, paths: Sequence[str]) -> SideScan:
    """Accumulate both filter modes for one side, reading every log exactly once."""
    side = _new_side(label, paths)
    loose_cells = side.cells["loose"]
    strict_cells = side.cells["strict"]
    loose_hist = side.hist["loose"]
    strict_hist = side.hist["strict"]

    for path in paths:
        map_id = infer_map_id(path)
        _metadata, tokens, rounds = load_log(path)
        known = side.tokens.setdefault(map_id, tokens)
        if known != tokens:
            side.token_conflicts.append(path)
        side.games[map_id] += 1

        # Pre-resolve the counter objects so the hot loop does no hashing.
        plan: List[Tuple[int, List[Tuple[int, List[int], List[int], Any, Any]]]] = []
        wall_plan: List[Tuple[int, List[int]]] = []
        for row in range(GRID_SIZE):
            row_plan: List[Tuple[int, List[int], List[int], Any, Any]] = []
            wall_cols: List[int] = []
            for col in range(GRID_SIZE):
                bucket = bucket_of(row, col, tokens[row][col])
                if bucket is None:
                    wall_cols.append(col)
                    continue
                row_plan.append(
                    (
                        col,
                        loose_cells[(map_id, row, col)],
                        strict_cells[(map_id, row, col)],
                        loose_hist[(map_id, bucket)],
                        strict_hist[(map_id, bucket)],
                    )
                )
            if row_plan:
                plan.append((row, row_plan))
            if wall_cols:
                wall_plan.append((row, wall_cols))

        for index in range(len(rounds) - 1):
            before_round = rounds[index]
            after_round = rounds[index + 1]
            if after_round.get("round") != before_round.get("round", -2) + 1:
                side.skipped_transitions += 1
                continue
            before_phase = before_round.get("end")
            after_phase = after_round.get("start")
            if not before_phase or not after_phase:
                side.skipped_transitions += 1
                continue
            end_grid = before_phase.get("grid")
            start_grid = after_phase.get("grid")
            if end_grid is None or start_grid is None:
                side.skipped_transitions += 1
                continue
            side.transitions[map_id] += 1

            for row, row_plan in plan:
                end_row = end_grid[row]
                start_row = start_grid[row]
                for col, loose_cell, strict_cell, loose_bin, strict_bin in row_plan:
                    before = end_row[col]
                    after = start_row[col]
                    if before == FOG or after == FOG or before == WALL or after == WALL:
                        continue
                    clean = before >= 0 and after >= 0
                    loose_cell[0] += 1
                    if clean:
                        strict_cell[0] += 1
                    else:
                        side.strict_dropped_obs += 1
                    gain = (after if after > 0 else 0) - (before if before > 0 else 0)
                    if gain > 0:
                        loose_cell[1] += 1
                        loose_cell[2] += gain
                        loose_bin[gain] += 1
                        if clean:
                            strict_cell[1] += 1
                            strict_cell[2] += gain
                            strict_bin[gain] += 1
                        else:
                            side.strict_dropped_events += 1

            # Diagnostic only: a token-'1' cell must never pass the loose filter.
            for row, wall_cols in wall_plan:
                end_row = end_grid[row]
                start_row = start_grid[row]
                for col in wall_cols:
                    before = end_row[col]
                    after = start_row[col]
                    if before != FOG and before != WALL and after != FOG and after != WALL:
                        side.wall_token_observations += 1

    return side


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def cell_metrics(
    counters: Mapping[Tuple[int, int, int], Sequence[int]],
    map_id: int,
    members: Sequence[Tuple[int, int]],
) -> Dict[str, Any]:
    """Aggregate one cell set: both aggregations, the event rate and the mean amount."""
    obs = 0
    events = 0
    amount = 0
    per_cell = 0.0
    obs_min: Optional[int] = None
    obs_max: Optional[int] = None
    unobserved = 0
    for row, col in members:
        cell = counters.get((map_id, row, col))
        cell_obs, cell_events, cell_amount = (cell[0], cell[1], cell[2]) if cell else (0, 0, 0)
        obs += cell_obs
        events += cell_events
        amount += cell_amount
        if cell_obs:
            per_cell += cell_amount / cell_obs
        else:
            unobserved += 1
        obs_min = cell_obs if obs_min is None else min(obs_min, cell_obs)
        obs_max = cell_obs if obs_max is None else max(obs_max, cell_obs)
    return {
        "cells": len(members),
        "cells_unobserved": unobserved,
        "obs": obs,
        "events": events,
        "amount": amount,
        "obs_min": obs_min if obs_min is not None else 0,
        "obs_max": obs_max if obs_max is not None else 0,
        "event_rate_per_1000": (1000.0 * events / obs) if obs else NAN,
        "pooled_rate_per_1000": (1000.0 * amount / obs) if obs else NAN,
        "pooled_game_gold": (amount / obs) * len(members) * ROUNDS_PER_GAME if obs else NAN,
        "per_cell_game_gold": per_cell * ROUNDS_PER_GAME if obs else NAN,
        "mean_amount": (amount / events) if events else NAN,
    }


EMPTY_METRICS: Dict[str, Any] = {
    "cells": 0,
    "cells_unobserved": 0,
    "obs": 0,
    "events": 0,
    "amount": 0,
    "obs_min": 0,
    "obs_max": 0,
    "event_rate_per_1000": NAN,
    "pooled_rate_per_1000": NAN,
    "pooled_game_gold": NAN,
    "per_cell_game_gold": NAN,
    "mean_amount": NAN,
}


def ratio(sim_value: float, platform_value: float) -> float:
    """sim / platform, or nan when either side is missing or the base is zero."""
    if platform_value != platform_value or sim_value != sim_value:
        return NAN
    if platform_value == 0:
        return NAN
    return sim_value / platform_value


def side_metrics(
    side: Optional[SideScan],
    mode: str,
    map_id: int,
    members_of: str,
) -> Dict[str, Any]:
    """Metrics for one side/mode/map, keyed by bucket name or by token value."""
    if side is None or map_id not in side.tokens:
        return {}
    counters = side.counters(mode)
    token_map = side.tokens[map_id]
    groups = bucket_members(token_map) if members_of == "bucket" else token_members(token_map)
    return {name: cell_metrics(counters, map_id, cells) for name, cells in groups.items()}


# --------------------------------------------------------------------------- #
# token maps: loading and cross-check
# --------------------------------------------------------------------------- #


def load_token_maps(path: pathlib.Path) -> Dict[int, List[List[str]]]:
    """Load the reference token maps, reading only top-level keys matching ^[0-9]+$."""
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("%s must hold a JSON object keyed by the map id" % path)
    maps: Dict[int, List[List[str]]] = {}
    for key, value in raw.items():
        if not MAP_KEY.match(str(key)):
            continue  # self-documenting metadata such as schema_version / provenance
        maps[int(key)] = normalise_token_rows(value, "%s['%s']" % (path, key))
    if not maps:
        raise ValueError("%s holds no numeric map keys" % path)
    return maps


def crosscheck_token_maps(
    reference: Mapping[int, Sequence[Sequence[str]]],
    platform_tokens: Mapping[int, Sequence[Sequence[str]]],
    source: str,
) -> Dict[str, Any]:
    """Abort with a clear message unless the reference file matches the platform logs."""
    missing = sorted(set(platform_tokens) - set(reference))
    mismatches: List[Tuple[int, int, int, str, str]] = []
    for map_id in sorted(set(platform_tokens) & set(reference)):
        logged = platform_tokens[map_id]
        given = reference[map_id]
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if logged[row][col] != given[row][col]:
                    mismatches.append((map_id, row, col, given[row][col], logged[row][col]))
    if missing or mismatches:
        lines = [
            "measure_generation: token-map cross-check FAILED against %s" % source,
            "  the platform logs' line 1 is the ground truth; refusing to measure with a "
            "reference map that disagrees with it.",
        ]
        if missing:
            lines.append(
                "  maps present in the platform logs but absent from the reference file: %s"
                % ", ".join("map%d" % map_id for map_id in missing)
            )
        if mismatches:
            lines.append("  %d disagreeing cell(s); first 20 shown as map/row/col reference!=log:" % len(mismatches))
            for map_id, row, col, given, logged in mismatches[:20]:
                lines.append("    map%d (%2d,%2d) reference='%s' log='%s'" % (map_id, row, col, given, logged))
        raise SystemExit("\n".join(lines))
    return {
        "source": source,
        "maps_checked": sorted(set(platform_tokens) & set(reference)),
        "verdict": "match",
    }


def token2_comparison(
    platform: SideScan,
    sim: Optional[SideScan],
) -> Dict[int, Dict[str, Any]]:
    """token-2 cell count and exact cell list per map, platform truth vs sim."""
    map_ids = sorted(set(platform.tokens) | set(sim.tokens if sim else {}))
    table: Dict[int, Dict[str, Any]] = {}
    for map_id in map_ids:
        platform_map = platform.tokens.get(map_id)
        sim_map = sim.tokens.get(map_id) if sim else None
        platform_cells = cells_with_token(platform_map, HOTSPOT_TOKEN) if platform_map else None
        sim_cells = cells_with_token(sim_map, HOTSPOT_TOKEN) if sim_map else None
        entry: Dict[str, Any] = {
            "platform_count": len(platform_cells) if platform_cells is not None else None,
            "sim_count": len(sim_cells) if sim_cells is not None else None,
            "platform_cells": platform_cells,
            "sim_cells": sim_cells,
            "walls_platform": len(cells_with_token(platform_map, WALL_TOKEN)) if platform_map else None,
            "walls_sim": len(cells_with_token(sim_map, WALL_TOKEN)) if sim_map else None,
        }
        if platform_cells is None or sim_cells is None:
            entry["verdict"] = "one side missing"
            entry["missing_in_sim"] = None
            entry["extra_in_sim"] = None
            entry["token_map_identical"] = None
        else:
            entry["missing_in_sim"] = sorted(set(platform_cells) - set(sim_cells))
            entry["extra_in_sim"] = sorted(set(sim_cells) - set(platform_cells))
            entry["token_map_identical"] = platform_map == sim_map
            entry["verdict"] = "EQUAL" if platform_cells == sim_cells else "DIFFERENT"
        table[map_id] = entry
    return table


# --------------------------------------------------------------------------- #
# acceptance
# --------------------------------------------------------------------------- #


def undefined_reason(platform_value: float, sim_value: float) -> str:
    """Say plainly WHY a ratio is undefined instead of hiding it behind n/a."""
    if platform_value != platform_value and sim_value != sim_value:
        return "ratio undefined: neither side has an observation for this cut"
    if platform_value != platform_value:
        return "ratio undefined: no platform observation for this cut"
    if sim_value != sim_value:
        return "ratio undefined: the sim side has no such cell or no event there"
    if platform_value == 0:
        return "ratio undefined: the platform base is zero"
    return ""


def acceptance(
    map_id: int,
    bucket_platform: Mapping[str, Mapping[str, Any]],
    bucket_sim: Mapping[str, Mapping[str, Any]],
    split_platform: Mapping[str, Mapping[str, Any]],
    split_sim: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """The five acceptance checks for one map, evaluated on the given filter mode."""
    checks: List[Dict[str, Any]] = []
    for token in (HOTSPOT_TOKEN, OPEN_TOKEN):
        platform_mean = split_platform.get(token, EMPTY_METRICS)["mean_amount"]
        sim_mean = split_sim.get(token, EMPTY_METRICS)["mean_amount"]
        value = ratio(sim_mean, platform_mean)
        checks.append(
            {
                "check": "token-%s single-event mean" % token,
                "platform": platform_mean,
                "sim": sim_mean,
                "ratio": value,
                "band": list(MEAN_BAND),
                "passed": bool(value == value and MEAN_BAND[0] <= value <= MEAN_BAND[1]),
                "note": "" if value == value else undefined_reason(platform_mean, sim_mean),
            }
        )
    for bucket in BUCKETS:
        platform_gold = bucket_platform.get(bucket, EMPTY_METRICS)["per_cell_game_gold"]
        sim_gold = bucket_sim.get(bucket, EMPTY_METRICS)["per_cell_game_gold"]
        value = ratio(sim_gold, platform_gold)
        checks.append(
            {
                "check": "per-game gold %s" % bucket,
                "platform": platform_gold,
                "sim": sim_gold,
                "ratio": value,
                "band": [1.0 - GOLD_TOLERANCE, 1.0 + GOLD_TOLERANCE],
                "passed": bool(value == value and abs(value - 1.0) <= GOLD_TOLERANCE),
                "note": "" if value == value else undefined_reason(platform_gold, sim_gold),
            }
        )
    for check in checks:
        check["map"] = map_id
    return checks


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #


def fnum(value: float, width: int, digits: int) -> str:
    if value is None or value != value:
        return "%*s" % (width, NA)
    return "%*.*f" % (width, digits, value)


def rule(char: str = "=", width: int = 118) -> str:
    return char * width


def render_histogram(counter: Mapping[int, int]) -> str:
    return " ".join("%d:%d" % (amount, counter[amount]) for amount in sorted(counter))


def wrapped(text: str, indent: str, width: int = 112) -> List[str]:
    if not text:
        return [indent + "(empty)"]
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent + "  ") or [
        indent + "(empty)"
    ]


def cells_text(cells: Optional[Sequence[Tuple[int, int]]]) -> str:
    if cells is None:
        return NA
    return " ".join("(%d,%d)" % (row, col) for row, col in cells)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def print_header(
    platform: SideScan,
    sim: Optional[SideScan],
    platform_specs: Sequence[str],
    sim_specs: Sequence[str],
    token_note: str,
    modes: Sequence[str],
) -> None:
    print(rule())
    print("GoldRush gold generation: platform truth vs simulator (per map, never pooled across maps)")
    print(rule())
    print(
        "platform logs : %d file(s), maps %s, %s round-transitions   from %s"
        % (
            len(platform.files),
            ",".join(str(map_id) for map_id in platform.map_ids()) or "-",
            sum(platform.transitions.values()),
            "; ".join(platform_specs),
        )
    )
    if sim is None:
        print("sim logs      : none given (--sim-logs); the sim columns read %s" % NA)
    else:
        print(
            "sim logs      : %d file(s), maps %s, %s round-transitions   from %s"
            % (
                len(sim.files),
                ",".join(str(map_id) for map_id in sim.map_ids()) or "-",
                sum(sim.transitions.values()),
                "; ".join(sim_specs),
            )
        )
    print("token maps    : %s" % token_note)
    print("filter modes  : %s" % ", ".join(modes))
    print("aggregations  : pooled (coverage-weighted, original) and per_cell (coverage-free, primary)")
    print("known bias    : " + BIAS_NOTE)
    for side in (platform, sim):
        if side is None:
            continue
        if side.token_conflicts:
            print(
                "WARNING       : %s logs disagree on line 1 within a map (%d file(s), first: %s); "
                "the first map seen is used for bucket membership"
                % (side.label, len(side.token_conflicts), side.token_conflicts[0])
            )
        if side.skipped_transitions:
            print(
                "WARNING       : %s side skipped %d non-contiguous or grid-less round transition(s)"
                % (side.label, side.skipped_transitions)
            )
        if side.wall_token_observations:
            print(
                "WARNING       : %s side saw %d token-'1' cell-round(s) pass the loose filter; "
                "wall exclusion by token map and by grid code disagree"
                % (side.label, side.wall_token_observations)
            )
    print(
        "strict drops  : platform %d observation(s) / %d event(s)%s"
        % (
            platform.strict_dropped_obs,
            platform.strict_dropped_events,
            ""
            if sim is None
            else ";  sim %d / %d" % (sim.strict_dropped_obs, sim.strict_dropped_events),
        )
    )
    print()


def print_token2_table(table: Mapping[int, Mapping[str, Any]]) -> None:
    print(rule())
    print("[1] token-2 (outer hotspot) cells per map: platform truth (log line 1) vs sim")
    print(rule("-"))
    print("%-6s %10s %8s %8s %10s %s" % ("map", "platform", "sim", "walls_p", "walls_s", "verdict"))
    for map_id in sorted(table):
        entry = table[map_id]
        verdict = entry["verdict"]
        if verdict == "EQUAL":
            tail = "EQUAL (identical cell lists)"
        elif verdict == "DIFFERENT":
            tail = "DIFFERENT (missing in sim: %d, extra in sim: %d)" % (
                len(entry["missing_in_sim"]),
                len(entry["extra_in_sim"]),
            )
        else:
            tail = verdict
        print(
            "map%-3d %10s %8s %8s %10s %s"
            % (
                map_id,
                entry["platform_count"] if entry["platform_count"] is not None else NA,
                entry["sim_count"] if entry["sim_count"] is not None else NA,
                entry["walls_platform"] if entry["walls_platform"] is not None else NA,
                entry["walls_sim"] if entry["walls_sim"] is not None else NA,
                tail,
            )
        )
    for map_id in sorted(table):
        entry = table[map_id]
        print("  map%d platform cells:" % map_id)
        for line in wrapped(cells_text(entry["platform_cells"]), "    "):
            print(line)
        print("  map%d sim cells:" % map_id)
        for line in wrapped(cells_text(entry["sim_cells"]), "    "):
            print(line)
        if entry["verdict"] == "DIFFERENT":
            for label, key in (("missing in sim", "missing_in_sim"), ("extra in sim", "extra_in_sim")):
                if entry[key]:
                    print("  map%d %s:" % (map_id, label))
                    for line in wrapped(cells_text(entry[key]), "    "):
                        print(line)
        if entry["token_map_identical"] is not None:
            print(
                "  map%d full 17x17 token map identical: %s"
                % (map_id, "yes" if entry["token_map_identical"] else "no")
            )
    print()


def print_bucket_tables(
    mode: str,
    map_ids: Sequence[int],
    bucket_platform: Mapping[int, Mapping[str, Mapping[str, Any]]],
    bucket_sim: Mapping[int, Mapping[str, Mapping[str, Any]]],
) -> None:
    print(rule())
    print("[2a] per-bucket observations, rates and single-event amounts   (filter=%s)" % mode)
    print(rule("-"))
    print(
        "%-4s %-5s %6s %6s %10s %10s %7s %7s %9s %9s %9s %9s %7s %7s %7s %7s"
        % (
            "map",
            "bkt",
            "cellP",
            "cellS",
            "obsP",
            "obsS",
            "evP",
            "evS",
            "ev/kcrP",
            "ev/kcrS",
            "poolP",
            "poolS",
            "poolRat",
            "meanP",
            "meanS",
            "meanRat",
        )
    )
    for map_id in map_ids:
        for bucket in BUCKETS:
            plat = bucket_platform.get(map_id, {}).get(bucket, EMPTY_METRICS)
            simm = bucket_sim.get(map_id, {}).get(bucket, EMPTY_METRICS)
            print(
                "%-4s %-5s %6d %6d %10d %10d %7d %7d %9s %9s %9s %9s %7s %7s %7s %7s"
                % (
                    "map%d" % map_id,
                    bucket,
                    plat["cells"],
                    simm["cells"],
                    plat["obs"],
                    simm["obs"],
                    plat["events"],
                    simm["events"],
                    fnum(plat["event_rate_per_1000"], 9, 2),
                    fnum(simm["event_rate_per_1000"], 9, 2),
                    fnum(plat["pooled_rate_per_1000"], 9, 1),
                    fnum(simm["pooled_rate_per_1000"], 9, 1),
                    fnum(ratio(simm["pooled_rate_per_1000"], plat["pooled_rate_per_1000"]), 7, 2),
                    fnum(plat["mean_amount"], 7, 2),
                    fnum(simm["mean_amount"], 7, 2),
                    fnum(ratio(simm["mean_amount"], plat["mean_amount"]), 7, 2),
                )
            )
        print()
    print("  pool* = gold per 1000 observed cell-rounds (coverage-weighted); mean* = gold per single event")
    print()
    print(rule())
    print(
        "[2b] per-game gold by bucket over %d rounds, both aggregations   (filter=%s)"
        % (ROUNDS_PER_GAME, mode)
    )
    print(rule("-"))
    print(
        "%-4s %-5s | %10s %10s %7s | %10s %10s %7s"
        % ("map", "bkt", "poolPlat", "poolSim", "ratio", "cellPlat", "cellSim", "ratio")
    )
    for map_id in map_ids:
        for bucket in BUCKETS:
            plat = bucket_platform.get(map_id, {}).get(bucket, EMPTY_METRICS)
            simm = bucket_sim.get(map_id, {}).get(bucket, EMPTY_METRICS)
            print(
                "%-4s %-5s | %10s %10s %7s | %10s %10s %7s"
                % (
                    "map%d" % map_id,
                    bucket,
                    fnum(plat["pooled_game_gold"], 10, 0),
                    fnum(simm["pooled_game_gold"], 10, 0),
                    fnum(ratio(simm["pooled_game_gold"], plat["pooled_game_gold"]), 7, 2),
                    fnum(plat["per_cell_game_gold"], 10, 0),
                    fnum(simm["per_cell_game_gold"], 10, 0),
                    fnum(ratio(simm["per_cell_game_gold"], plat["per_cell_game_gold"]), 7, 2),
                )
            )
        totals: Dict[str, Dict[str, float]] = {}
        for key in ("pooled_game_gold", "per_cell_game_gold"):
            totals[key] = {}
            for label, table in (("platform", bucket_platform), ("sim", bucket_sim)):
                totals[key][label] = sum(
                    value
                    for bucket in BUCKETS
                    for value in [table.get(map_id, {}).get(bucket, EMPTY_METRICS)[key]]
                    if value == value
                )
        print(
            "%-4s %-5s | %10.0f %10.0f %7s | %10.0f %10.0f %7s"
            % (
                "map%d" % map_id,
                "TOTAL",
                totals["pooled_game_gold"]["platform"],
                totals["pooled_game_gold"]["sim"],
                fnum(ratio(totals["pooled_game_gold"]["sim"], totals["pooled_game_gold"]["platform"]), 7, 2),
                totals["per_cell_game_gold"]["platform"],
                totals["per_cell_game_gold"]["sim"],
                fnum(ratio(totals["per_cell_game_gold"]["sim"], totals["per_cell_game_gold"]["platform"]), 7, 2),
            )
        )
        print()
    print("  pool* = pooled rate x bucket cells x %d rounds (only comparable at equal coverage)" % ROUNDS_PER_GAME)
    print("  cell* = sum over the bucket's cells of (cell amount / cell obs) x %d (coverage-free, PRIMARY)"
          % ROUNDS_PER_GAME)
    print("  TOTAL sums each column over the buckets, skipping %s entries" % NA)
    print("  each side's bucket cell count comes from its OWN line-1 token map, so a sim map that lost its")
    print("  hotspots reads %s in OUT2 (empty bucket) and carries those cells in OUT0 instead" % NA)
    print()
    print(rule())
    print("[2c] coverage annex: observations per cell (min..max) and cells never observed   (filter=%s)" % mode)
    print(rule("-"))
    print(
        "%-4s %-5s | %20s %8s | %20s %8s"
        % ("map", "bkt", "platform obs/cell", "unobs", "sim obs/cell", "unobs")
    )
    for map_id in map_ids:
        for bucket in BUCKETS:
            plat = bucket_platform.get(map_id, {}).get(bucket, EMPTY_METRICS)
            simm = bucket_sim.get(map_id, {}).get(bucket, EMPTY_METRICS)
            print(
                "%-4s %-5s | %20s %8d | %20s %8d"
                % (
                    "map%d" % map_id,
                    bucket,
                    "%d..%d" % (plat["obs_min"], plat["obs_max"]) if plat["cells"] else NA,
                    plat["cells_unobserved"],
                    "%d..%d" % (simm["obs_min"], simm["obs_max"]) if simm["cells"] else NA,
                    simm["cells_unobserved"],
                )
            )
    print()


def print_split_table(
    mode: str,
    map_ids: Sequence[int],
    split_platform: Mapping[int, Mapping[str, Mapping[str, Any]]],
    split_sim: Mapping[int, Mapping[str, Mapping[str, Any]]],
) -> None:
    print(rule())
    print(
        "[3] frequency vs amount split per (map, token), both rings pooled   (filter=%s)" % mode
    )
    print("    a rate error and an amount error need different fixes, so they are never mixed here.")
    print(rule("-"))
    print(
        "%-4s %-6s | %9s %9s %7s | %8s %8s %7s | %8s %8s"
        % ("map", "token", "ev/kcrP", "ev/kcrS", "ratio", "meanP", "meanS", "ratio", "evP", "evS")
    )
    for map_id in map_ids:
        for token in SPLIT_TOKENS:
            plat = split_platform.get(map_id, {}).get(token, EMPTY_METRICS)
            simm = split_sim.get(map_id, {}).get(token, EMPTY_METRICS)
            print(
                "%-4s %-6s | %9s %9s %7s | %8s %8s %7s | %8d %8d"
                % (
                    "map%d" % map_id,
                    "tok" + token,
                    fnum(plat["event_rate_per_1000"], 9, 2),
                    fnum(simm["event_rate_per_1000"], 9, 2),
                    fnum(ratio(simm["event_rate_per_1000"], plat["event_rate_per_1000"]), 7, 2),
                    fnum(plat["mean_amount"], 8, 2),
                    fnum(simm["mean_amount"], 8, 2),
                    fnum(ratio(simm["mean_amount"], plat["mean_amount"]), 7, 2),
                    plat["events"],
                    simm["events"],
                )
            )
    print()


def print_histograms(
    mode: str,
    map_ids: Sequence[int],
    platform: SideScan,
    sim: Optional[SideScan],
) -> None:
    print(rule())
    print("[4] amount histograms per (map, bucket), integer amount:count   (filter=%s)" % mode)
    print("    the gap between the ordinary band (1..11) and the rich band (16..37) is the")
    print("    two-component structure of the generator; a sim that fills the gap is wrong.")
    print(rule("-"))
    for map_id in map_ids:
        for bucket in BUCKETS:
            for side in (platform, sim):
                if side is None:
                    continue
                counter = side.hist[mode].get((map_id, bucket), collections.Counter())
                total = sum(counter.values())
                mean = (sum(amount * count for amount, count in counter.items()) / total) if total else NAN
                head = "  map%d %-5s %-9s n=%6d mean=%s" % (
                    map_id,
                    bucket,
                    side.label,
                    total,
                    fnum(mean, 6, 2),
                )
                print(head)
                for line in wrapped(render_histogram(counter), "      "):
                    print(line)
        print()


def print_acceptance(
    mode: str,
    checks: Sequence[Mapping[str, Any]],
    primary: bool,
) -> bool:
    print(rule())
    print(
        "[5] acceptance vs platform truth   (filter=%s%s; per-game gold from the per_cell aggregation)"
        % (mode, ", PRIMARY" if primary else "")
    )
    print("    thresholds: single-event mean ratio in [%.2f, %.2f]; per-game gold within %d%% of platform"
          % (MEAN_BAND[0], MEAN_BAND[1], int(GOLD_TOLERANCE * 100)))
    print(rule("-"))
    print(
        "%-4s %-28s %10s %10s %7s %14s  %s"
        % ("map", "check", "platform", "sim", "ratio", "band", "verdict")
    )
    all_passed = True
    by_map: "collections.OrderedDict[int, List[Mapping[str, Any]]]" = collections.OrderedDict()
    for check in checks:
        by_map.setdefault(check["map"], []).append(check)
    for map_id, entries in by_map.items():
        failed = 0
        for check in entries:
            digits = 2 if "mean" in check["check"] else 0
            print(
                "%-4s %-28s %10s %10s %7s %14s  %s%s"
                % (
                    "map%d" % map_id,
                    check["check"],
                    fnum(check["platform"], 10, digits),
                    fnum(check["sim"], 10, digits),
                    fnum(check["ratio"], 7, 2),
                    "[%.2f, %.2f]" % (check["band"][0], check["band"][1]),
                    "PASS" if check["passed"] else "FAIL",
                    ("  <- " + check["note"]) if check["note"] else "",
                )
            )
            if not check["passed"]:
                failed += 1
        all_passed = all_passed and failed == 0
        print(
            "map%d VERDICT: %s (%d of %d checks failed)"
            % (map_id, "PASS" if failed == 0 else "FAIL", failed, len(entries))
        )
        print()
    print("OVERALL (%s): %s" % (mode, "PASS" if all_passed else "FAIL"))
    print()
    return all_passed


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #


def jnum(value: Any) -> Any:
    """JSON-safe number: nan becomes null so the payload stays valid JSON."""
    if isinstance(value, float):
        if value != value:
            return None
        return round(value, 6)
    return value


def jmetrics(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: jnum(value) for key, value in metrics.items()}


def build_payload(
    platform: SideScan,
    sim: Optional[SideScan],
    platform_specs: Sequence[str],
    sim_specs: Sequence[str],
    token_check: Mapping[str, Any],
    modes: Sequence[str],
    map_ids: Sequence[int],
    bucket_metrics: Mapping[str, Tuple[Mapping[int, Any], Mapping[int, Any]]],
    split_metrics: Mapping[str, Tuple[Mapping[int, Any], Mapping[int, Any]]],
    checks: Mapping[str, Sequence[Mapping[str, Any]]],
    token2: Mapping[int, Mapping[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "caliber": {
            "observation_unit": "(cell, round-transition) pair, end.grid[r] -> start.grid[r+1]",
            "event": "positive increment of ground gold, negative codes read as zero",
            "filters": {
                "loose": "drop the pair if either side is -5 (fog) or -1 (wall)",
                "strict": "loose, plus both sides must be >= 0 (no actor/bomb marker)",
            },
            "buckets": {
                "CTR": "chebyshev((8,8)) <= 4, walls excluded",
                "OUT0": "chebyshev >= 5 and token '0'",
                "OUT2": "chebyshev >= 5 and token '2'",
            },
            "aggregations": {
                "pooled": "1000 * sum(amount) / sum(obs); coverage-weighted, original number",
                "per_cell": "sum over cells of (cell amount / cell obs) * %d; coverage-free, primary"
                % ROUNDS_PER_GAME,
            },
            "rounds_per_game": ROUNDS_PER_GAME,
            "known_bias": "the 4 cells held by the two probe units and the two sparring units never "
            "generate, so rates run 2-3% low; single-event amounts are unbiased",
            "per_cell_bias": "cells with zero observations contribute zero (see cells_unobserved)",
            "cross_map_pooling": "not offered; the maps differ in walls and hotspot placement",
        },
        "inputs": {
            "platform": {
                "specs": list(platform_specs),
                "files": list(platform.files),
                "games_per_map": {str(k): v for k, v in sorted(platform.games.items())},
                "transitions_per_map": {str(k): v for k, v in sorted(platform.transitions.items())},
                "skipped_transitions": platform.skipped_transitions,
                "wall_token_observations": platform.wall_token_observations,
                "strict_dropped_obs": platform.strict_dropped_obs,
                "strict_dropped_events": platform.strict_dropped_events,
            },
            "token_maps": dict(token_check),
            "filter_modes": list(modes),
        },
        "token2_cells": {},
        "buckets": {},
        "token_split": {},
        "histograms": {},
        "acceptance": {},
    }
    if sim is not None:
        payload["inputs"]["sim"] = {
            "specs": list(sim_specs),
            "files": list(sim.files),
            "games_per_map": {str(k): v for k, v in sorted(sim.games.items())},
            "transitions_per_map": {str(k): v for k, v in sorted(sim.transitions.items())},
            "skipped_transitions": sim.skipped_transitions,
            "wall_token_observations": sim.wall_token_observations,
            "strict_dropped_obs": sim.strict_dropped_obs,
            "strict_dropped_events": sim.strict_dropped_events,
        }
    else:
        payload["inputs"]["sim"] = None

    for map_id, entry in token2.items():
        payload["token2_cells"][str(map_id)] = {
            "platform_count": entry["platform_count"],
            "sim_count": entry["sim_count"],
            "platform_cells": entry["platform_cells"],
            "sim_cells": entry["sim_cells"],
            "missing_in_sim": entry["missing_in_sim"],
            "extra_in_sim": entry["extra_in_sim"],
            "token_map_identical": entry["token_map_identical"],
            "verdict": entry["verdict"],
        }

    for mode in modes:
        plat_buckets, sim_buckets = bucket_metrics[mode]
        plat_split, sim_split = split_metrics[mode]
        payload["buckets"][mode] = {}
        payload["token_split"][mode] = {}
        payload["histograms"][mode] = {}
        for map_id in map_ids:
            payload["buckets"][mode][str(map_id)] = {}
            for bucket in BUCKETS:
                plat = plat_buckets.get(map_id, {}).get(bucket, EMPTY_METRICS)
                simm = sim_buckets.get(map_id, {}).get(bucket, EMPTY_METRICS)
                payload["buckets"][mode][str(map_id)][bucket] = {
                    "platform": jmetrics(plat),
                    "sim": jmetrics(simm),
                    "ratios": {
                        "pooled_rate": jnum(ratio(simm["pooled_rate_per_1000"], plat["pooled_rate_per_1000"])),
                        "pooled_game_gold": jnum(ratio(simm["pooled_game_gold"], plat["pooled_game_gold"])),
                        "per_cell_game_gold": jnum(
                            ratio(simm["per_cell_game_gold"], plat["per_cell_game_gold"])
                        ),
                        "mean_amount": jnum(ratio(simm["mean_amount"], plat["mean_amount"])),
                        "event_rate": jnum(ratio(simm["event_rate_per_1000"], plat["event_rate_per_1000"])),
                    },
                }
            payload["token_split"][mode][str(map_id)] = {}
            for token in SPLIT_TOKENS:
                plat = plat_split.get(map_id, {}).get(token, EMPTY_METRICS)
                simm = sim_split.get(map_id, {}).get(token, EMPTY_METRICS)
                payload["token_split"][mode][str(map_id)]["tok" + token] = {
                    "platform": jmetrics(plat),
                    "sim": jmetrics(simm),
                    "ratios": {
                        "event_rate": jnum(ratio(simm["event_rate_per_1000"], plat["event_rate_per_1000"])),
                        "mean_amount": jnum(ratio(simm["mean_amount"], plat["mean_amount"])),
                    },
                }
            payload["histograms"][mode][str(map_id)] = {}
            for bucket in BUCKETS:
                entry: Dict[str, Any] = {}
                for side in (platform, sim):
                    if side is None:
                        continue
                    counter = side.hist[mode].get((map_id, bucket), collections.Counter())
                    entry[side.label] = {str(amount): counter[amount] for amount in sorted(counter)}
                payload["histograms"][mode][str(map_id)][bucket] = entry
        payload["acceptance"][mode] = {
            "checks": [
                {
                    "map": check["map"],
                    "check": check["check"],
                    "platform": jnum(check["platform"]),
                    "sim": jnum(check["sim"]),
                    "ratio": jnum(check["ratio"]),
                    "band": [jnum(value) for value in check["band"]],
                    "passed": check["passed"],
                    "note": check["note"],
                }
                for check in checks[mode]
            ],
            "passed": all(check["passed"] for check in checks[mode]) if checks[mode] else None,
        }
    return payload


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def expand_spec(spec: str) -> List[str]:
    """Expand one CLI log spec: a glob, or a directory root holding map*/game_*.log."""
    path = pathlib.Path(os.path.expanduser(spec))
    if path.is_dir():
        for pattern in ("map*/game_*.log", "game_*.log", "*.log", "**/*.log"):
            hits = sorted(str(hit) for hit in path.glob(pattern))
            if hits:
                return hits
        return []
    return sorted(glob.glob(os.path.expanduser(spec)))


def collect_files(specs: Sequence[str], label: str, required: bool) -> List[str]:
    files: "collections.OrderedDict[str, None]" = collections.OrderedDict()
    for spec in specs:
        hits = expand_spec(spec)
        if not hits:
            message = "measure_generation: %s log spec %r matched no file" % (label, spec)
            if required:
                raise SystemExit(message)
            print(message, file=sys.stderr)
        for hit in hits:
            files[hit] = None
    if required and not files:
        raise SystemExit("measure_generation: no %s logs found in %s" % (label, list(specs)))
    return list(files)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="measure_generation.py",
        description="Measure gold generation from full-information logs and compare "
        "platform truth against the simulator, per map, in one stated caliber.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            caliber summary
              observation unit  (cell, round-transition); event = positive ground-gold increment
              filters           loose (skip fog/wall) and strict (also require both sides >= 0)
              buckets           CTR (chebyshev <= 4), OUT0 (>= 5, token 0), OUT2 (>= 5, token 2)
              aggregations      pooled (coverage-weighted, original) and per_cell (coverage-free, primary)
            """
        ),
    )
    parser.add_argument(
        "--platform-logs",
        action="append",
        metavar="GLOB",
        help="platform probe log glob or directory (repeatable; default %s)" % DEFAULT_PLATFORM_GLOB,
    )
    parser.add_argument(
        "--sim-logs",
        action="append",
        metavar="GLOB",
        help="simulator log glob or directory root holding map<N>/game_*.log (repeatable)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="report the strict filter (both sides >= 0)",
    )
    parser.add_argument(
        "--loose",
        action="store_true",
        help="report the loose filter (only fog/wall dropped); default is to print both",
    )
    parser.add_argument(
        "--token-maps",
        metavar="PATH",
        default=None,
        help="reference token maps to cross-check against the platform logs "
        "(default %s when it exists; only top-level keys matching ^[0-9]+$ are read)"
        % DEFAULT_TOKEN_MAPS,
    )
    parser.add_argument(
        "--no-token-maps",
        action="store_true",
        help="skip the reference file entirely and trust the platform logs' line 1",
    )
    parser.add_argument("--json", metavar="PATH", default=None, help="write the machine-readable payload here")
    parser.add_argument(
        "--fail-on-reject",
        action="store_true",
        help="exit 3 when the acceptance block fails (default: measurement success is exit 0)",
    )
    args = parser.parse_args(argv)
    if not args.platform_logs:
        args.platform_logs = [DEFAULT_PLATFORM_GLOB]
    if not args.sim_logs:
        args.sim_logs = []
    return args


def resolve_token_maps(args: argparse.Namespace) -> Tuple[Optional[Dict[int, List[List[str]]]], str, str]:
    """Return (reference maps or None, source label, human note)."""
    if args.no_token_maps:
        return None, "", "--no-token-maps given; the platform logs' line 1 is used as truth"
    if args.token_maps:
        path = pathlib.Path(os.path.expanduser(args.token_maps))
        if not path.exists():
            raise SystemExit("measure_generation: --token-maps %s does not exist" % path)
    else:
        path = DEFAULT_TOKEN_MAPS
        if not path.exists():
            return (
                None,
                "",
                "%s absent; falling back to the platform logs' line 1" % path,
            )
    maps = load_token_maps(path)
    note = "%s (%d map(s): %s) cross-checked against the platform logs' line 1" % (
        path,
        len(maps),
        ",".join(str(map_id) for map_id in sorted(maps)),
    )
    return maps, str(path), note


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    modes = [mode for mode in FILTERS if getattr(args, mode)] or list(FILTERS)

    try:
        reference, source, token_note = resolve_token_maps(args)
    except (ValueError, json.JSONDecodeError) as error:
        raise SystemExit("measure_generation: unusable --token-maps file: %s" % error)

    platform_files = collect_files(args.platform_logs, "platform", required=True)
    sim_files = collect_files(args.sim_logs, "sim", required=False) if args.sim_logs else []

    try:
        platform = scan_side("platform", platform_files)
        sim = scan_side("sim", sim_files) if sim_files else None
    except (ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
        raise SystemExit("measure_generation: unreadable log: %s" % error)

    token_check: Dict[str, Any] = {"source": None, "verdict": "not checked", "note": token_note}
    if reference is not None:
        token_check = dict(crosscheck_token_maps(reference, platform.tokens, source))
        token_check["note"] = token_note

    map_ids = sorted(set(platform.tokens) | set(sim.tokens if sim else {}))

    bucket_metrics: Dict[str, Tuple[Dict[int, Any], Dict[int, Any]]] = {}
    split_metrics: Dict[str, Tuple[Dict[int, Any], Dict[int, Any]]] = {}
    checks: Dict[str, List[Dict[str, Any]]] = {}
    for mode in modes:
        plat_buckets = {map_id: side_metrics(platform, mode, map_id, "bucket") for map_id in map_ids}
        sim_buckets = {map_id: side_metrics(sim, mode, map_id, "bucket") for map_id in map_ids}
        plat_split = {map_id: side_metrics(platform, mode, map_id, "token") for map_id in map_ids}
        sim_split = {map_id: side_metrics(sim, mode, map_id, "token") for map_id in map_ids}
        bucket_metrics[mode] = (plat_buckets, sim_buckets)
        split_metrics[mode] = (plat_split, sim_split)
        mode_checks: List[Dict[str, Any]] = []
        if sim is not None:
            for map_id in map_ids:
                mode_checks.extend(
                    acceptance(
                        map_id,
                        plat_buckets.get(map_id, {}),
                        sim_buckets.get(map_id, {}),
                        plat_split.get(map_id, {}),
                        sim_split.get(map_id, {}),
                    )
                )
        checks[mode] = mode_checks

    print_header(platform, sim, args.platform_logs, args.sim_logs, token_note, modes)
    print_token2_table(token2_comparison(platform, sim))
    for mode in modes:
        plat_buckets, sim_buckets = bucket_metrics[mode]
        plat_split, sim_split = split_metrics[mode]
        print_bucket_tables(mode, map_ids, plat_buckets, sim_buckets)
        print_split_table(mode, map_ids, plat_split, sim_split)
        print_histograms(mode, map_ids, platform, sim)

    accepted = True
    if sim is None:
        print(rule())
        print("[5] acceptance: skipped, no sim logs were given (--sim-logs)")
        print()
    else:
        for mode in modes:
            # loose is primary: the platform's grid does not mark actors, so only
            # the loose denominator means the same thing on both sides.  See the
            # module docstring and the "strict drops" counters in the header.
            accepted = print_acceptance(mode, checks[mode], primary=(mode == "loose")) and accepted

    payload = build_payload(
        platform,
        sim,
        args.platform_logs,
        args.sim_logs,
        token_check,
        modes,
        map_ids,
        bucket_metrics,
        split_metrics,
        checks,
        token2_comparison(platform, sim),
    )
    if args.json:
        target = pathlib.Path(os.path.expanduser(args.json))
        try:
            with target.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
        except OSError as error:
            raise SystemExit("measure_generation: cannot write --json %s: %s" % (target, error))
        print("payload written to %s" % target)

    if args.fail_on_reject and sim is not None and not accepted:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
