#!/usr/bin/env python3
"""Audit the central position law against the platform, beyond bucket totals.

Two questions that a bucket total cannot answer, both raised as acceptance gates
for the centripetal-plus-wall-rejection central law in ``scenario.py``.

Gate A -- is the wall rule "drop" or "displace"?
    Aggregate central yield can be matched by more than one mechanism, so match
    the shape instead.  Regress each open central cell's measured event rate on
    the separable centripetal weight (one free scale per map, which is exactly
    the one free parameter of the law) and then ask whether the residual knows
    about walls.  A dropped attempt is destroyed, so residuals must be
    independent of wall adjacency.  An attempt that slides to a nearby free cell
    would deposit the wall's share next door, so wall-adjacent cells would carry
    a systematic positive residual.  Because wall adjacency correlates with the
    distance from the centre, the comparison is stratified by Chebyshev ring and
    the per-ring differences are then averaged.

Gate B -- is the shape right, not just the total?
    One parameter is being asked to reproduce a five-point curve.  Print the
    per-ring rates for d = 0..4 plus the outer ring, platform against simulator,
    in both the gold and the event normalisation.

Caliber (identical on both sides, and identical to sim/measure_generation.py):
    an event is a positive increment from ``end.grid[r]`` to ``start.grid[r+1]``
    where neither side is fog (-5) or wall (-1); negative codes read as zero
    gold; the observation unit is a (cell, round-transition) pair.  Rates are
    per-cell and therefore independent of the coverage pattern, which matters
    because the platform side is covered by probe anchors while simulator logs
    are fully visible.

Known bias carried into every number here: generation never lands under an
actor, and the platform's own grid does not mark actors at all, so measured
rates are a few percent below the underlying law on both sides.  Ratios between
the two sides are affected only to the extent that the two occupancy patterns
differ.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sim.scenario import CENTRAL_COL_WEIGHTS, CENTRAL_ROW_WEIGHTS  # type: ignore
else:
    from .scenario import CENTRAL_COL_WEIGHTS, CENTRAL_ROW_WEIGHTS

FOG = -5
WALL = -1
Cell = Tuple[int, int]


def cheb(row: int, col: int) -> int:
    return max(abs(row - 8), abs(col - 8))


def central_weight(row: int, col: int) -> float:
    return CENTRAL_ROW_WEIGHTS[row - 4] * CENTRAL_COL_WEIGHTS[col - 4]


def scan(paths: Sequence[str], map_of) -> Tuple[Dict[Any, Dict[str, int]], Dict[int, Any]]:
    """Return per-(map, cell) obs/event/amount counters and each map's token grid."""
    per_cell: Dict[Any, Dict[str, int]] = collections.defaultdict(
        lambda: {"obs": 0, "ev": 0, "amt": 0}
    )
    tokens: Dict[int, Any] = {}
    for path in paths:
        map_id = map_of(path)
        lines = Path(path).read_text().splitlines()
        token = json.loads(lines[1])
        tokens.setdefault(map_id, token)
        rounds = sorted(
            (json.loads(line) for line in lines[2:] if line.strip()),
            key=lambda item: item["round"],
        )
        for index in range(len(rounds) - 1):
            end = rounds[index]["end"]["grid"]
            start = rounds[index + 1]["start"]["grid"]
            for row in range(17):
                for col in range(17):
                    before, after = end[row][col], start[row][col]
                    if before in (FOG, WALL) or after in (FOG, WALL):
                        continue
                    bucket = per_cell[(map_id, row, col)]
                    bucket["obs"] += 1
                    have = before if before > 0 else 0
                    want = after if after > 0 else 0
                    if want > have:
                        bucket["ev"] += 1
                        bucket["amt"] += want - have
    return per_cell, tokens


def wall_neighbours(token: Sequence[Sequence[str]], row: int, col: int) -> int:
    count = 0
    for d_row, d_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        n_row, n_col = row + d_row, col + d_col
        if not (0 <= n_row < 17 and 0 <= n_col < 17):
            continue
        if str(token[n_row][n_col]) == "1":
            count += 1
    return count


def gate_a(per_cell, tokens, label: str) -> Mapping[str, Any]:
    print()
    print("=" * 96)
    print("GATE A  central per-cell residual against the centripetal weight, by wall adjacency  [%s]" % label)
    print("  a dropped attempt predicts no dependence; an attempt that slides next door predicts")
    print("  a positive residual on wall-adjacent cells")
    payload: Dict[str, Any] = {}
    for map_id in sorted(tokens):
        token = tokens[map_id]
        cells = [
            (row, col)
            for row in range(4, 13)
            for col in range(4, 13)
            if str(token[row][col]) != "1" and per_cell[(map_id, row, col)]["obs"] > 0
        ]
        if not cells:
            continue
        observed = {cell: per_cell[(map_id, cell[0], cell[1])] for cell in cells}
        # one free scale per map: total predicted events == total observed events
        weight_exposure = sum(central_weight(*cell) * observed[cell]["obs"] for cell in cells)
        total_events = sum(observed[cell]["ev"] for cell in cells)
        if not weight_exposure or not total_events:
            continue
        scale = total_events / weight_exposure
        rows = []
        for cell in cells:
            expected = scale * central_weight(*cell) * observed[cell]["obs"]
            seen = observed[cell]["ev"]
            rows.append({
                "cell": cell,
                "ring": cheb(*cell),
                "walls": wall_neighbours(token, *cell),
                "expected": expected,
                "observed": seen,
                # Poisson-standardised residual; expected counts are 20+ per cell
                "z": (seen - expected) / math.sqrt(expected) if expected > 0 else 0.0,
                "ratio": seen / expected if expected > 0 else float("nan"),
            })
        print()
        print("  map%d   %d open central cells, %d observed events, one fitted scale" % (map_id, len(cells), total_events))
        print("  %-5s %-7s %6s %9s %9s %9s %8s" % ("ring", "walladj", "cells", "obs ev", "exp ev", "obs/exp", "mean z"))
        strata = []
        for ring in range(5):
            for adjacent in (False, True):
                group = [item for item in rows if item["ring"] == ring and (item["walls"] > 0) == adjacent]
                if not group:
                    continue
                obs = sum(item["observed"] for item in group)
                exp = sum(item["expected"] for item in group)
                mean_z = statistics.fmean(item["z"] for item in group)
                print("  %-5d %-7s %6d %9d %9.1f %9.3f %8.3f"
                      % (ring, "yes" if adjacent else "no", len(group), obs, exp, obs / exp, mean_z))
            pair = {
                adjacent: [item for item in rows if item["ring"] == ring and (item["walls"] > 0) == adjacent]
                for adjacent in (False, True)
            }
            if pair[True] and pair[False]:
                strata.append((
                    ring,
                    statistics.fmean(item["z"] for item in pair[True]),
                    statistics.fmean(item["z"] for item in pair[False]),
                    len(pair[True]), len(pair[False]),
                ))
        if strata:
            diffs = [adj - non for _, adj, non, _, _ in strata]
            # each stratum difference is a difference of means of standardised
            # residuals, so its own standard error is sqrt(1/n_adj + 1/n_non)
            errs = [math.sqrt(1.0 / n_adj + 1.0 / n_non) for _, _, _, n_adj, n_non in strata]
            weights = [1.0 / (err * err) for err in errs]
            combined = sum(d * w for d, w in zip(diffs, weights)) / sum(weights)
            combined_se = math.sqrt(1.0 / sum(weights))
            print("  stratified over rings with both groups present (%d rings): mean z difference"
                  " wall-adjacent minus not = %+.3f +/- %.3f  (%+.2f sigma)"
                  % (len(strata), combined, combined_se, combined / combined_se))
            payload["map%d" % map_id] = {
                "strata": [
                    {"ring": ring, "z_wall_adjacent": adj, "z_not_adjacent": non,
                     "cells_adjacent": n_adj, "cells_not_adjacent": n_non}
                    for ring, adj, non, n_adj, n_non in strata
                ],
                "z_difference": combined,
                "z_difference_se": combined_se,
                "sigma": combined / combined_se,
                "open_central_cells": len(cells),
                "observed_events": total_events,
            }
        else:
            print("  no ring on this map has both wall-adjacent and non-adjacent open cells:"
                  " this map carries no information for gate A")
            payload["map%d" % map_id] = {"informative": False, "open_central_cells": len(cells)}
    return payload


def gate_a_contrast(platform_payload, sim_payload) -> Mapping[str, Any]:
    """Difference the platform's wall-adjacency residual against the simulator's.

    The raw platform residual is not by itself evidence of sliding: the weight
    model is approximate and occupancy is not uniform, so a wall-adjacency
    difference can appear for reasons that have nothing to do with the wall rule.
    The simulator is the null control -- it drops attempts and never slides -- so
    whatever residual it shows under the identical measurement is the size of the
    artifact.  Only the platform-minus-simulator difference is a sliding signal.
    """
    print()
    print("=" * 96)
    print("GATE A verdict  platform residual minus the drop-only simulator's own residual")
    print("  the simulator never slides, so its residual is the artifact floor;")
    print("  a sliding platform would have to exceed it")
    print("  %-6s %12s %12s %14s %8s" % ("map", "platform z", "sim z", "difference", "sigma"))
    diffs: List[Tuple[float, float]] = []
    payload: Dict[str, Any] = {}
    for key in sorted(set(platform_payload) & set(sim_payload)):
        left, right = platform_payload[key], sim_payload[key]
        if "z_difference" not in left or "z_difference" not in right:
            continue
        difference = left["z_difference"] - right["z_difference"]
        error = math.sqrt(left["z_difference_se"] ** 2 + right["z_difference_se"] ** 2)
        print("  %-6s %+12.3f %+12.3f %+9.3f+-%.3f %+8.2f"
              % (key, left["z_difference"], right["z_difference"], difference, error, difference / error))
        diffs.append((difference, error))
        payload[key] = {"platform_z": left["z_difference"], "sim_z": right["z_difference"],
                        "difference": difference, "se": error, "sigma": difference / error}
    if diffs:
        weights = [1.0 / (error * error) for _, error in diffs]
        combined = sum(d * w for (d, _), w in zip(diffs, weights)) / sum(weights)
        combined_se = math.sqrt(1.0 / sum(weights))
        print("  pooled over the informative maps: %+.3f +/- %.3f (%+.2f sigma)"
              % (combined, combined_se, combined / combined_se))
        print("  reading: a displacing wall rule would show a positive difference;"
              " %s" % ("no such signal" if abs(combined / combined_se) < 2 else "SIGNAL PRESENT"))
        payload["pooled"] = {"difference": combined, "se": combined_se, "sigma": combined / combined_se}
    return payload


def gate_b(platform, platform_tokens, sim, sim_tokens) -> Mapping[str, Any]:
    print()
    print("=" * 96)
    print("GATE B  per-ring rates, platform against simulator, per-cell caliber")
    payload: Dict[str, Any] = {}
    for map_id in sorted(platform_tokens):
        token = platform_tokens[map_id]
        print()
        print("  map%d   %-32s %-32s" % (map_id, "gold per 1000 cell-rounds", "events per 1000 cell-rounds"))
        print("  %-9s %6s %10s %10s %7s %10s %10s %7s"
              % ("ring", "cells", "platform", "sim", "ratio", "platform", "sim", "ratio"))
        rows = {}
        for ring in list(range(5)) + ["5+", "5+tok0", "5+tok2"]:
            def keep(row: int, col: int) -> bool:
                if str(token[row][col]) == "1":
                    return False
                distance = cheb(row, col)
                if ring == "5+":
                    return distance >= 5
                if ring == "5+tok0":
                    return distance >= 5 and str(token[row][col]) == "0"
                if ring == "5+tok2":
                    return distance >= 5 and str(token[row][col]) == "2"
                return distance == ring
            cells = [(row, col) for row in range(17) for col in range(17) if keep(row, col)]
            if not cells:
                continue
            def rate(source, key: str) -> float:
                total_obs = sum(source[(map_id, r, c)]["obs"] for r, c in cells)
                total = sum(source[(map_id, r, c)][key] for r, c in cells)
                return 1000.0 * total / total_obs if total_obs else float("nan")
            p_gold, s_gold = rate(platform, "amt"), rate(sim, "amt")
            p_ev, s_ev = rate(platform, "ev"), rate(sim, "ev")
            def ratio(numerator: float, denominator: float) -> str:
                if denominator and denominator == denominator and numerator == numerator:
                    return "%.3f" % (numerator / denominator)
                return "n/a"
            print("  %-9s %6d %10.1f %10.1f %7s %10.2f %10.2f %7s"
                  % ("d=%s" % ring, len(cells), p_gold, s_gold, ratio(s_gold, p_gold),
                     p_ev, s_ev, ratio(s_ev, p_ev)))
            rows[str(ring)] = {
                "cells": len(cells),
                "platform_gold_per_kcr": p_gold, "sim_gold_per_kcr": s_gold,
                "platform_events_per_kcr": p_ev, "sim_events_per_kcr": s_ev,
            }
        payload["map%d" % map_id] = rows
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform-logs", default="/tmp/master/plogs/*.log")
    parser.add_argument("--sim-logs", default="/tmp/master/simfix/map*/game_*.log")
    parser.add_argument("--json", default=None, help="write the machine-readable payload here")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform_paths = sorted(glob.glob(args.platform_logs))
    sim_paths = sorted(glob.glob(args.sim_logs))
    if not platform_paths:
        raise SystemExit("no platform logs matched %r" % args.platform_logs)
    if not sim_paths:
        raise SystemExit("no simulator logs matched %r" % args.sim_logs)
    print("platform logs: %d   simulator logs: %d" % (len(platform_paths), len(sim_paths)))
    platform, platform_tokens = scan(platform_paths, lambda p: int(p.split("_map")[1][0]))
    sim, sim_tokens = scan(sim_paths, lambda p: int(p.split("map")[-1].split("/")[0]))
    gate_a_platform = gate_a(platform, platform_tokens, "platform truth")
    gate_a_sim = gate_a(sim, sim_tokens, "simulator, the drop-only null control")
    payload = {
        "platform_logs": platform_paths,
        "sim_logs": sim_paths,
        "gate_a_platform": gate_a_platform,
        "gate_a_sim": gate_a_sim,
        "gate_a_contrast": gate_a_contrast(gate_a_platform, gate_a_sim),
        "gate_b": gate_b(platform, platform_tokens, sim, sim_tokens),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        print()
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
