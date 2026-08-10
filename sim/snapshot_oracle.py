#!/usr/bin/env python3
"""snapshot_oracle.py — closed-loop same-seed paired A/B for the snapshot rudder.

The mechanism under test reads ``GameInput.snapshot`` (delivered every 5 rounds) and
redirects **only** unit 1's fallback anchor to the geometric centre of the region with
extremal ``gold_remaining``.  Three compile-time variants are priced:

  OFF   mechanism compiled out (must be byte-identical to the live baseline artifact)
  RICH  anchor -> centroid of argmax gold_remaining      (the candidate)
  POOR  anchor -> centroid of argmin gold_remaining      (the reversed-signal control)

Design (six conditions held fixed inside every subtraction):
  configuration  one .so per variant, same compiler/flags/host, mod64 entry bucket 0x10
  opponent       the live baseline artifact in the other seat (documented local protocol)
  map            map1 (mode-1 opening exits at round 4, so the snapshot can only ever
                 touch the *fallback anchor*, never the opening BFS march)
  action order   two arms run and reported separately; never subtracted from each other
  time window    every game for one seed is generated from one scenario digest, asserted
  corpus         a pre-declared seed list with a pre-declared even/odd holdout split

For seed ``s`` and cost setting ``c`` the worker runs, all on the same scenario:
    ref     baseline vs baseline
    v@P1    variant   vs baseline
    v@P2    baseline  vs variant
and forms  delta(v, seat) = net_gold(v @ seat) - net_gold(baseline @ seat).
``--fixed-costs 200,201`` makes seat P1 the first mover; ``201,200`` makes seat P2 the
first mover, so the two cost settings cross spawn seat with action order.

Usage
  python3 sim/snapshot_oracle.py plan     --out sim/reports/snapshot_oracle.json
  python3 sim/snapshot_oracle.py run      --seedset pilot --results /tmp/snapres
  python3 sim/snapshot_oracle.py analyze  --results /tmp/snapres --out sim/reports/...
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.runner import run_game  # noqa: E402

# --------------------------------------------------------------------------- plan
# Pre-declared before any main-batch result was inspected.  The split rule is a pure
# function of the seed index, so it cannot be re-cut after the fact.
MAP = "map1"
PILOT_BASE = 5_100_000
PILOT_N = 24
MAIN_BASE = 5_200_000
COST_ARMS = {"o1": (200, 201), "o2": (201, 200)}
VARIANTS = ("off", "rich", "poor")

# Region -> anchor table baked into the RICH/POOR builds.  Rotation-closed under
# (r,c) -> (c, 16-r) and passable on all three maps; each is 0.5 cells from the true
# centroid of its region.
SNAP_ANCHOR = {1: (8, 8), 2: (1, 6), 3: (10, 1), 4: (15, 10), 5: (6, 15)}
BASE_ANCHOR = {0: (6, 8), 1: (11, 8)}
GRID = 17


def region_id(row: int, col: int) -> int:
    """The fixed windmill region id in 1..5 (mirrors sim/engine.py:84)."""
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    return 5


def main_seeds(pairs: int) -> list[int]:
    return [MAIN_BASE + index for index in range(2 * pairs)]


def split_of(seed: int) -> str:
    """Pre-declared, index-parity holdout rule.  Even index screens, odd holds out."""
    return "in_sample" if (seed - MAIN_BASE) % 2 == 0 else "holdout"


def seedset(name: str, pairs: int) -> list[int]:
    if name == "pilot":
        return [PILOT_BASE + index for index in range(PILOT_N)]
    if name == "main":
        return main_seeds(pairs)
    raise ValueError("unknown seedset %r" % name)


# ------------------------------------------------------------------- diagnostics
def _anchor_track(rows: Sequence[Mapping[str, Any]], mode: str) -> list[tuple[int, int]]:
    """Reconstruct unit 1's fallback anchor per round, exactly as the .so computes it.

    ``gold_remaining`` is recomputed from the log's round-start full grid (positive
    ground cells), which is bit-exact with engine.py's ``_make_snapshot`` remaining.
    On map1 the anchor is never rewritten by ``fixAnchor`` (FAST from round 4, all five
    table cells passable), so this reconstruction is the effective anchor.
    """
    anchor = BASE_ANCHOR[1]
    track: list[tuple[int, int]] = []
    for row in rows:
        rnd = int(row["round"])
        if mode in ("rich", "poor") and rnd > 0 and rnd % 5 == 0:
            grid = row["start"]["grid"]
            remaining = [0] * 6
            for r in range(GRID):
                gr = grid[r]
                for c in range(GRID):
                    v = gr[c]
                    if v > 0:
                        remaining[region_id(r, c)] += v
            if mode == "rich":
                best = 1
                for i in range(2, 6):
                    if remaining[i] > remaining[best]:
                        best = i
            else:
                best = 1
                for i in range(2, 6):
                    if remaining[i] < remaining[best]:
                        best = i
            anchor = SNAP_ANCHOR[best]
        track.append(anchor)
    return track


def _blind(grid: Sequence[Sequence[int]], row: int, col: int) -> bool:
    """Reconstruct the player's ``blind`` gate for a unit at (row, col).

    src/player.cpp only steers a unit at its fallback anchor when the 5x5 scan finds no
    whole-gold cell (value >= 3) and the unit is not standing on residual gold (> 1).
    Inside a radius-2 window there is never fog, and the log's full grid only overwrites
    *empty* ground with entity markers (-2/-4), so the two gates agree bit for bit.
    """
    if grid[row][col] > 1:
        return False
    for r in range(max(0, row - 2), min(GRID, row + 3)):
        line = grid[r]
        for c in range(max(0, col - 2), min(GRID, col + 3)):
            if line[c] > 2:
                return False
    return True


def diagnose(rows: Sequence[Mapping[str, Any]], player_id: int, mode: str) -> dict[str, Any]:
    """Mechanism diagnostics for one seat of one game, all from the log."""
    track = _anchor_track(rows, mode)
    unit_rounds = 0
    transit = 0            # u1 round-start Manhattan distance to its anchor > 2
    blind_rounds = 0       # rounds where u1 actually consults the fallback anchor
    blind_transit = 0      # blind AND far from the anchor == genuinely walking
    outer = 0              # u1 round-start position outside region 1
    zero_pick = 0
    pickup_u1 = 0
    pickup_u0 = 0
    outer_u0 = 0
    burned = 0
    excursions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    prev_anchor: tuple[int, int] | None = None
    for index, row in enumerate(rows):
        players = {int(p["id"]): p for p in row["start"]["players"]}
        end_players = {int(p["id"]): p for p in row["end"]["players"]}
        units = players[player_id]["units"]
        end_units = end_players[player_id]["units"]
        pos1 = units[1]["position"]
        pos0 = units[0]["position"]
        anchor = track[index]
        grid = row["start"]["grid"]
        blind1 = False
        if pos1:
            unit_rounds += 1
            dist = abs(pos1[0] - anchor[0]) + abs(pos1[1] - anchor[1])
            transit += dist > 2
            outer += region_id(pos1[0], pos1[1]) != 1
            pick = int(end_units[1]["pickup"])
            zero_pick += pick == 0
            pickup_u1 += pick
            blind1 = _blind(grid, pos1[0], pos1[1])
            blind_rounds += blind1
            blind_transit += blind1 and dist > 2
        if pos0:
            pickup_u0 += int(end_units[0]["pickup"])
            outer_u0 += region_id(pos0[0], pos0[1]) != 1
        # ``end.burned`` is a single round total for the whole board; the log does not
        # attribute it per player, so this is informational only, never subtracted.
        burned += int(row["end"].get("burned") or 0)
        # excursion bookkeeping: a maximal run of rounds under one unchanged anchor
        if anchor != prev_anchor:
            if current is not None:
                excursions.append(current)
            start_dist = (abs(pos1[0] - anchor[0]) + abs(pos1[1] - anchor[1])) if pos1 else None
            current = {
                "start_round": int(row["round"]),
                "anchor": list(anchor),
                "region": region_id(*anchor),
                "start_distance": start_dist,
                "rounds": 0,
                "blind_rounds": 0,
                "arrive_round": None,
                "arrive_window_gold": None,
                "arrive_cell_gold": None,
                "pickup": 0,
                "transit_rounds": 0,
            }
            prev_anchor = anchor
        if current is not None and pos1:
            current["rounds"] += 1
            current["blind_rounds"] += blind1
            current["pickup"] += int(end_units[1]["pickup"])
            dist = abs(pos1[0] - anchor[0]) + abs(pos1[1] - anchor[1])
            current["transit_rounds"] += dist > 2
            if current["arrive_round"] is None and dist <= 2:
                window = 0
                for r in range(max(0, anchor[0] - 2), min(GRID, anchor[0] + 3)):
                    for c in range(max(0, anchor[1] - 2), min(GRID, anchor[1] + 3)):
                        if grid[r][c] > 0:
                            window += grid[r][c]
                current["arrive_round"] = int(row["round"])
                current["arrive_window_gold"] = window
                cell = grid[anchor[0]][anchor[1]]
                current["arrive_cell_gold"] = cell if cell > 0 else 0
    if current is not None:
        excursions.append(current)
    return {
        "unit_rounds": unit_rounds,
        "transit_rounds": transit,
        "blind_rounds_u1": blind_rounds,
        "blind_transit_rounds_u1": blind_transit,
        "outer_rounds_u1": outer,
        "outer_rounds_u0": outer_u0,
        "zero_pickup_rounds_u1": zero_pick,
        "pickup_u1": pickup_u1,
        "pickup_u0": pickup_u0,
        "burned": burned,
        "excursions": excursions,
    }


# ----------------------------------------------------------------------- one task
def _income(result: Any, player_id: int) -> int:
    return int(result.summary["players"][str(player_id)]["net_gold"])


def one_task(task: Mapping[str, Any]) -> dict[str, Any]:
    seed = int(task["seed"])
    costs = tuple(task["costs"])
    base = task["baseline"]
    started = time.time()
    ref = run_game(base, base, map_source=MAP, seed=seed, dispatch="fixed", fixed_costs=costs)
    digest = ref.summary["scenario_digest"]
    out: dict[str, Any] = {
        "seed": seed,
        "costs": list(costs),
        "arm": task["arm"],
        "scenario_digest": digest,
        "ref": {"p1": _income(ref, 1), "p2": _income(ref, 2)},
        "variants": {},
        "diag": {},
    }
    ref_rows = [json.loads(line) for line in ref.log_bytes.splitlines()[2:]]
    if task["diagnostics"]:
        out["diag"]["ref@p1"] = diagnose(ref_rows, 1, "off")
        out["diag"]["ref@p2"] = diagnose(ref_rows, 2, "off")
    for name, path in task["variants"].items():
        as_p1 = run_game(path, base, map_source=MAP, seed=seed, dispatch="fixed", fixed_costs=costs)
        as_p2 = run_game(base, path, map_source=MAP, seed=seed, dispatch="fixed", fixed_costs=costs)
        for leg in (as_p1, as_p2):
            if leg.summary["scenario_digest"] != digest:
                raise RuntimeError("scenario digest drift at seed %d" % seed)
        out["variants"][name] = {
            "p1": _income(as_p1, 1),
            "p2": _income(as_p2, 2),
            "delta_p1": _income(as_p1, 1) - out["ref"]["p1"],
            "delta_p2": _income(as_p2, 2) - out["ref"]["p2"],
            "opp_p2_when_we_are_p1": _income(as_p1, 2),
            "opp_p1_when_we_are_p2": _income(as_p2, 1),
        }
        if task["diagnostics"] and name != "off":
            rows1 = [json.loads(line) for line in as_p1.log_bytes.splitlines()[2:]]
            rows2 = [json.loads(line) for line in as_p2.log_bytes.splitlines()[2:]]
            out["diag"]["%s@p1" % name] = diagnose(rows1, 1, name)
            out["diag"]["%s@p2" % name] = diagnose(rows2, 2, name)
    out["wall_seconds"] = round(time.time() - started, 2)
    return out


def cmd_run(args: argparse.Namespace) -> int:
    variants = {}
    for name in args.variants.split(","):
        path = Path(args.so_dir) / ("%s.so" % name)
        if not path.exists():
            raise SystemExit("missing variant .so: %s" % path)
        variants[name] = str(path)
    seeds = seedset(args.seedset, args.pairs)
    arms = {key: COST_ARMS[key] for key in args.arms.split(",")}
    results_dir = Path(args.results)
    results_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "seed": seed,
            "costs": costs,
            "arm": arm,
            "baseline": args.baseline,
            "variants": variants,
            "diagnostics": bool(args.diagnostics),
        }
        for arm, costs in sorted(arms.items())
        for seed in seeds
    ]
    out_path = results_dir / ("%s_%s.jsonl" % (args.seedset, args.tag))
    done = set()
    if out_path.exists() and not args.overwrite:
        for line in out_path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                done.add((record["seed"], record["arm"]))
    tasks = [t for t in tasks if (t["seed"], t["arm"]) not in done]
    print("tasks=%d (already done %d) workers=%d -> %s"
          % (len(tasks), len(done), args.workers, out_path), flush=True)
    failures = 0
    with out_path.open("a") as sink:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(one_task, task): task for task in tasks}
            for count, future in enumerate(as_completed(futures), 1):
                task = futures[future]
                try:
                    record = future.result()
                except Exception as error:  # keep the batch alive, record the hole
                    failures += 1
                    record = {"seed": task["seed"], "arm": task["arm"],
                              "status": "error", "error": repr(error)}
                sink.write(json.dumps(record, sort_keys=True) + "\n")
                sink.flush()
                if count % 20 == 0 or count == len(tasks):
                    print("  %d/%d done (failures %d)" % (count, len(tasks), failures), flush=True)
    return 2 if failures else 0


# ------------------------------------------------------------------------ stats
def stats(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    wins = sum(1 for v in values if v > 0)
    losses = sum(1 for v in values if v < 0)
    return {
        "n": n,
        "mean": round(mean, 3),
        "se": round(se, 3),
        "sd": round(sd, 3),
        "t": round(mean / se, 3) if se > 0 else None,
        "median": round(statistics.median(values), 3),
        "wins": wins,
        "losses": losses,
        "ties": n - wins - losses,
        "min": min(values),
        "max": max(values),
    }


def paired_contrast(a: Mapping[int, float], b: Mapping[int, float]) -> dict[str, Any]:
    """Per-seed paired difference of two same-seed per-seed statistics."""
    keys = sorted(set(a) & set(b))
    return stats([a[k] - b[k] for k in keys])


def load_records(results: Path, pattern: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(results.glob(pattern)):
        for line in path.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return [r for r in records if r.get("status") != "error"]


def _agg_excursions(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [e for e in entries]
    if not rows:
        return {"n": 0}
    arrived = [e for e in rows if e["arrive_round"] is not None]
    def q(vals: Sequence[float], f: float) -> float | None:
        if not vals:
            return None
        ordered = sorted(vals)
        return ordered[max(0, math.ceil(len(ordered) * f) - 1)]
    window = [e["arrive_window_gold"] for e in arrived]
    cell = [e["arrive_cell_gold"] for e in arrived]
    rounds = [e["rounds"] for e in rows]
    delay = [e["arrive_round"] - e["start_round"] for e in arrived]
    pickup = [e["pickup"] for e in rows]
    transit = [e["transit_rounds"] for e in rows]
    blind = [e["blind_rounds"] for e in rows]
    start_dist = [e["start_distance"] for e in rows if e["start_distance"] is not None]
    return {
        "n": len(rows),
        "arrived": len(arrived),
        "arrival_rate": round(len(arrived) / len(rows), 4),
        "start_distance": {"mean": round(statistics.fmean(start_dist), 3) if start_dist else None,
                           "median": statistics.median(start_dist) if start_dist else None},
        "rounds_per_excursion": {"mean": round(statistics.fmean(rounds), 3),
                                 "median": statistics.median(rounds),
                                 "p90": q(rounds, 0.9)},
        "blind_rounds_per_excursion": {"mean": round(statistics.fmean(blind), 3),
                                       "median": statistics.median(blind)},
        "rounds_to_arrive": {"mean": round(statistics.fmean(delay), 3) if delay else None,
                             "median": statistics.median(delay) if delay else None,
                             "p90": q(delay, 0.9)},
        "arrival_window_gold_5x5": {"mean": round(statistics.fmean(window), 3) if window else None,
                                    "median": statistics.median(window) if window else None,
                                    "p10": q(window, 0.10), "p90": q(window, 0.90),
                                    "zero_rate": round(sum(1 for v in window if v == 0) / len(window), 4)
                                    if window else None},
        "arrival_cell_gold": {"mean": round(statistics.fmean(cell), 3) if cell else None,
                              "median": statistics.median(cell) if cell else None,
                              "zero_rate": round(sum(1 for v in cell if v == 0) / len(cell), 4)
                              if cell else None},
        "pickup_per_excursion": {"mean": round(statistics.fmean(pickup), 3),
                                 "median": statistics.median(pickup)},
        "transit_share_within_excursion": round(sum(transit) / sum(rounds), 4) if sum(rounds) else None,
    }


def _agg_diag(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(entries)
    if not rows:
        return {"games": 0}
    total = sum(r["unit_rounds"] for r in rows)
    blind = sum(r["blind_rounds_u1"] for r in rows)
    outer_exc = [e for r in rows for e in r["excursions"] if e["region"] != 1]
    return {
        "games": len(rows),
        "u1_unit_rounds": total,
        "transit_share_u1": round(sum(r["transit_rounds"] for r in rows) / total, 4),
        "blind_share_u1": round(blind / total, 4),
        "blind_transit_share_u1": round(sum(r["blind_transit_rounds_u1"] for r in rows) / total, 4),
        "transit_share_within_blind_rounds": round(
            sum(r["blind_transit_rounds_u1"] for r in rows) / blind, 4) if blind else None,
        "outer_ring_share_u1": round(sum(r["outer_rounds_u1"] for r in rows) / total, 4),
        "outer_ring_share_u0": round(sum(r["outer_rounds_u0"] for r in rows) / total, 4),
        "zero_pickup_share_u1": round(sum(r["zero_pickup_rounds_u1"] for r in rows) / total, 4),
        "pickup_per_round_u1": round(sum(r["pickup_u1"] for r in rows) / total, 4),
        "pickup_per_round_u0": round(sum(r["pickup_u0"] for r in rows) / total, 4),
        "pickup_per_game_u1": round(sum(r["pickup_u1"] for r in rows) / len(rows), 2),
        "pickup_per_game_u0": round(sum(r["pickup_u0"] for r in rows) / len(rows), 2),
        "burned_per_game_both_players": round(sum(r["burned"] for r in rows) / len(rows), 2),
        "excursions_all": _agg_excursions(e for r in rows for e in r["excursions"]),
        "excursions_outer_only": _agg_excursions(outer_exc),
        "excursions_central_only": _agg_excursions(
            e for r in rows for e in r["excursions"] if e["region"] == 1),
    }


def analyse(records: Sequence[Mapping[str, Any]], splits: bool) -> dict[str, Any]:
    """Per-seed deltas, decomposed by cost arm x seat, then folded into order arms."""
    cells: dict[tuple[str, str, str], dict[int, float]] = {}
    for rec in records:
        arm = rec["arm"]
        for name, payload in rec["variants"].items():
            for seat in ("p1", "p2"):
                cells.setdefault((arm, seat, name), {})[rec["seed"]] = float(payload["delta_%s" % seat])
    # Which (cost arm, seat) cells are the first mover?
    first_cells = {("o1", "p1"), ("o2", "p2")}
    report: dict[str, Any] = {"cells": {}, "order_arms": {}, "contrast": {}}
    for (arm, seat, name), series in sorted(cells.items()):
        order = "first" if (arm, seat) in first_cells else "second"
        key = "%s|%s|seat_%s|%s" % (name, arm, seat, order)
        if splits:
            report["cells"][key] = {
                split: stats([v for s, v in sorted(series.items()) if split_of(s) == split])
                for split in ("in_sample", "holdout")
            }
            report["cells"][key]["all"] = stats([v for _, v in sorted(series.items())])
        else:
            report["cells"][key] = {"all": stats([v for _, v in sorted(series.items())])}
    # Order arms: seat-balanced per-seed mean of the two cells sharing an action order.
    for order, members in (("first", [("o1", "p1"), ("o2", "p2")]),
                           ("second", [("o1", "p2"), ("o2", "p1")])):
        for name in VARIANTS:
            series: dict[int, float] = {}
            parts = [cells.get((arm, seat, name), {}) for arm, seat in members]
            if not all(parts):
                continue
            for seed in sorted(set(parts[0]) & set(parts[1])):
                series[seed] = statistics.fmean(part[seed] for part in parts)
            key = "%s|order_%s" % (name, order)
            if splits:
                report["order_arms"][key] = {
                    split: stats([v for s, v in sorted(series.items()) if split_of(s) == split])
                    for split in ("in_sample", "holdout")
                }
                report["order_arms"][key]["all"] = stats([v for _, v in sorted(series.items())])
            else:
                report["order_arms"][key] = {"all": stats([v for _, v in sorted(series.items())])}
            report.setdefault("_series", {})[key] = series
    # RICH - POOR contrast, paired per seed inside each order arm.
    for order in ("first", "second"):
        rich = report.get("_series", {}).get("rich|order_%s" % order, {})
        poor = report.get("_series", {}).get("poor|order_%s" % order, {})
        if not rich or not poor:
            continue
        if splits:
            entry = {}
            for split in ("in_sample", "holdout"):
                a = {s: v for s, v in rich.items() if split_of(s) == split}
                b = {s: v for s, v in poor.items() if split_of(s) == split}
                entry[split] = paired_contrast(a, b)
            entry["all"] = paired_contrast(rich, poor)
        else:
            entry = {"all": paired_contrast(rich, poor)}
        report["contrast"]["rich_minus_poor|order_%s" % order] = entry
    report.pop("_series", None)
    return report


# Opponent first-mover rates measured live on the platform (AGENT.md section 1, 8.10):
# against Tiuntled-1 we now move first only 19-26% of games (the old "85-96%" is void);
# against Tundra it is 53.93 / 72.67 / 70.07 % on map1 / map2 / map3.
FIRST_MOVER_WEIGHTS = {
    "t1_live_0.225": 0.225,
    "tundra_map1_0.539": 0.539,
    "neutral_0.500": 0.500,
    "tundra_map3_0.701": 0.701,
}


def weighted(records: Sequence[Mapping[str, Any]], splits: bool) -> dict[str, Any]:
    """Per-seed arm-weighted deltas: w * first-mover arm + (1-w) * second-mover arm.

    The two arms share the same seeds, so the weighted quantity is formed per seed and
    only then averaged; combining the two arms' standard errors would ignore their
    within-seed correlation.
    """
    cells: dict[tuple[str, str, str], dict[int, float]] = {}
    for rec in records:
        for name, payload in rec["variants"].items():
            for seat in ("p1", "p2"):
                cells.setdefault((rec["arm"], seat, name), {})[rec["seed"]] = float(
                    payload["delta_%s" % seat])
    first_cells = {("o1", "p1"), ("o2", "p2")}
    arms: dict[tuple[str, str], dict[int, float]] = {}
    for name in VARIANTS:
        for order, members in (("first", [c for c in first_cells]),
                               ("second", [("o1", "p2"), ("o2", "p1")])):
            parts = [cells.get((arm, seat, name), {}) for arm, seat in members]
            if not all(parts):
                continue
            arms[(name, order)] = {
                seed: statistics.fmean(part[seed] for part in parts)
                for seed in sorted(set(parts[0]) & set(parts[1]))
            }
    out: dict[str, Any] = {}
    for label, w in sorted(FIRST_MOVER_WEIGHTS.items()):
        entry: dict[str, Any] = {}
        for name in VARIANTS:
            if (name, "first") not in arms:
                continue
            first, second = arms[(name, "first")], arms[(name, "second")]
            series = {s: w * first[s] + (1 - w) * second[s] for s in sorted(set(first) & set(second))}
            if splits:
                entry[name] = {split: stats([v for s, v in sorted(series.items())
                                             if split_of(s) == split])
                               for split in ("in_sample", "holdout")}
                entry[name]["all"] = stats([v for _, v in sorted(series.items())])
            else:
                entry[name] = {"all": stats([v for _, v in sorted(series.items())])}
        if ("rich", "first") in arms and ("poor", "first") in arms:
            series = {}
            for s in sorted(set(arms[("rich", "first")]) & set(arms[("poor", "first")])):
                rich = w * arms[("rich", "first")][s] + (1 - w) * arms[("rich", "second")][s]
                poor = w * arms[("poor", "first")][s] + (1 - w) * arms[("poor", "second")][s]
                series[s] = rich - poor
            if splits:
                entry["rich_minus_poor"] = {
                    split: stats([v for s, v in sorted(series.items()) if split_of(s) == split])
                    for split in ("in_sample", "holdout")}
                entry["rich_minus_poor"]["all"] = stats([v for _, v in sorted(series.items())])
            else:
                entry["rich_minus_poor"] = {"all": stats([v for _, v in sorted(series.items())])}
        out[label] = {"first_mover_weight": w, "deltas": entry}
    return out


def reference_income(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Absolute reference income by arm and seat.  Only a labelling sanity check: the
    simulator's absolute income is NOT comparable to the platform (NPC model 38.7%)."""
    out: dict[str, Any] = {}
    for arm in sorted({r["arm"] for r in records}):
        rows = [r for r in records if r["arm"] == arm]
        p1 = [r["ref"]["p1"] for r in rows]
        p2 = [r["ref"]["p2"] for r in rows]
        out[arm] = {
            "games": len(rows),
            "seat_p1_mean": round(statistics.fmean(p1), 2),
            "seat_p2_mean": round(statistics.fmean(p2), 2),
            "first_mover_seat": "p1" if arm == "o1" else "p2",
            "first_over_second_ratio": round(
                (statistics.fmean(p1) / statistics.fmean(p2)) if arm == "o1"
                else (statistics.fmean(p2) / statistics.fmean(p1)), 3),
        }
    return out


def cmd_analyze(args: argparse.Namespace) -> int:
    results = Path(args.results)
    payload: dict[str, Any] = {}
    for label, pattern, splits in (("pilot", "pilot_*.jsonl", False),
                                   ("main", "main_*.jsonl", True)):
        records = load_records(results, pattern)
        if not records:
            continue
        payload[label] = {
            "games": len(records) * (1 + 2 * len(records[0]["variants"])),
            "tasks": len(records),
            "seeds": sorted({r["seed"] for r in records}),
            "reference_income": reference_income(records),
            "income": analyse(records, splits),
            "arm_weighted": weighted(records, splits),
        }
        diag_keys = sorted({k for r in records for k in r.get("diag", {})})
        if diag_keys:
            payload[label]["diagnostics"] = {}
            for arm in sorted({r["arm"] for r in records}):
                for key in diag_keys:
                    entries = [r["diag"][key] for r in records
                               if r["arm"] == arm and key in r.get("diag", {})]
                    if entries:
                        payload[label]["diagnostics"]["%s|%s" % (arm, key)] = _agg_diag(entries)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(out.read_text()) if out.exists() else {}
        existing.update({"results": payload})
        out.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote %s" % out)
    print(json.dumps(payload.get("main", payload.get("pilot", {})).get("income", {}),
                     indent=2, sort_keys=True)[:12000])
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan = {
        "declared_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "map": MAP,
        "rounds_per_game": 500,
        "opponent": "the live baseline artifact in the other seat (self-play protocol)",
        "dispatch": "fixed",
        "cost_arms": {k: list(v) for k, v in COST_ARMS.items()},
        "arm_semantics": {
            "o1": "fixed-costs 200,201 -> seat P1 is the first mover",
            "o2": "fixed-costs 201,200 -> seat P2 is the first mover",
        },
        "pilot_seeds": [PILOT_BASE + i for i in range(PILOT_N)],
        "pilot_role": "SD estimation and batch sizing only; excluded from screening and holdout",
        "main_seed_base": MAIN_BASE,
        "main_pairs": args.pairs,
        "main_seeds": main_seeds(args.pairs),
        "split_rule": "seed index (seed - %d): even -> in_sample (screening), odd -> holdout"
                      % MAIN_BASE,
        "holdout_policy": "the mechanism is priced on holdout only; in-sample screens",
        "variants": {
            "off": "SNAP_MODE=0, mechanism compiled out, must be byte-identical to baseline",
            "rich": "SNAP_MODE=1, u1 fallback anchor -> centroid of argmax gold_remaining",
            "poor": "SNAP_MODE=2, u1 fallback anchor -> centroid of argmin gold_remaining",
        },
        "region_anchor_table": {str(k): list(v) for k, v in SNAP_ANCHOR.items()},
        "baseline_anchor": {str(k): list(v) for k, v in BASE_ANCHOR.items()},
    }
    plan["declaration_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text()) if out.exists() else {}
    existing["plan"] = plan
    existing.setdefault("schema_version", 1)
    out.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--out", default=str(ROOT / "sim/reports/snapshot_oracle.json"))
    p.add_argument("--pairs", type=int, default=120)
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("run")
    r.add_argument("--baseline", required=True)
    r.add_argument("--so-dir", required=True)
    r.add_argument("--variants", default="off,rich,poor")
    r.add_argument("--seedset", default="pilot", choices=("pilot", "main"))
    r.add_argument("--pairs", type=int, default=120)
    r.add_argument("--arms", default="o1,o2")
    r.add_argument("--results", default="/tmp/snapres")
    r.add_argument("--tag", default="a")
    r.add_argument("--workers", type=int, default=24)
    r.add_argument("--diagnostics", type=int, default=0)
    r.add_argument("--overwrite", action="store_true")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("analyze")
    a.add_argument("--results", default="/tmp/snapres")
    a.add_argument("--out", default="")
    a.set_defaults(func=cmd_analyze)
    return parser


if __name__ == "__main__":
    ns = build_parser().parse_args()
    raise SystemExit(ns.func(ns))
