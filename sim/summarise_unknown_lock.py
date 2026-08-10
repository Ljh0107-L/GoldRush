#!/usr/bin/env python3
"""Summarise the lock-audit runs into the report tables and the JSON companion.

Reads the raw per-run JSON produced by ``sim/audit_unknown_lock.py`` and emits
``sim/reports/unknown_map_robustness.json``.

Every number here is a count or a quantile over games that were actually played.
The single modelled quantity is the known-map premium in gold: it is a linear
interpolation in verification-scan count anchored on the two contest-machine
measurements recorded in ``src/CHANGELOG.md`` 8.10 (window 24 = -31 gold,
unbounded = -77 gold).  Income on unfamiliar maps is deliberately absent -- there
is no baseline there, so the behavioural proxies (blocked-step rate, all-stay
rounds) are reported instead.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
KNOWN = ("map1", "map2", "map3")
BOUNDS = (4, 8, 12, 24, 40, 60, 90, 120, 250, 499)
# Contest-machine anchors for the known-map premium (src/CHANGELOG.md 8.10,
# real AVX build): the shipped 24-round window costs 6-9 amortised cycles per
# moveDecision call ~ -31 gold/game, and removing the bound costs 21-24 cycles
# ~ -77 gold/game.
ANCHORS = {"bound24": {"cycles": [6, 9], "gold": -31},
           "unbounded": {"cycles": [21, 24], "gold": -77}}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def state_of(run: dict) -> str:
    if run.get("forfeit"):
        return "invalid_scenario" if "occupies a wall" in run["forfeit"] else "forfeit"
    if run["lock_to"] < 0:
        return "no_lock"
    if run["table_mismatch_cells"] == 0:
        return "correct_lock"
    if run["mech_conflict_round"] >= 0:
        return "mislock_detected"
    if run["visible_conflict_round"] >= 0:
        return "mislock_missed_late_evidence"
    return "mislock_missed_no_evidence"


def flatten(data: dict, build: str) -> list[dict]:
    out = []
    for name, entry in sorted(data.items()):
        for run in entry["runs"]:
            if run["build"] != build:
                continue
            row = {"map": name, "parent": entry.get("parent"),
                   "cell": entry.get("cell"), "note": entry.get("note"),
                   "state": state_of(run)}
            row.update({k: run.get(k) for k in (
                "seed", "build", "forfeit", "rounds", "lock_to", "lock_round",
                "final_map_id", "mech_conflict_round", "visible_conflict_round",
                "relock_round", "unknown_round", "table_mismatch_cells",
                "phantom_walls", "missing_walls", "all_stay_rounds",
                "stay_or_blocked_steps", "steps", "verify_scans",
                "cells_compared", "verify_rounds_const")})
            out.append(row)
    return out


def quantile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[k]


def population(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["state"] not in ("invalid_scenario", "forfeit")]
    mis = [r for r in valid if r["state"].startswith("mislock")]
    with_ev = [r["visible_conflict_round"] for r in mis
               if r["visible_conflict_round"] >= 0]
    shipped_det = [r["mech_conflict_round"] for r in mis
                   if r["mech_conflict_round"] >= 0]
    never = sum(1 for r in mis if r["visible_conflict_round"] < 0)
    padded = sorted(with_ev) + [10 ** 9] * never
    return {
        "runs_total": len(rows),
        "runs_valid": len(valid),
        "excluded_invalid_scenario": sorted({r["map"] for r in rows
                                             if r["state"] == "invalid_scenario"}),
        "forfeits": [r for r in rows if r["state"] == "forfeit"],
        "games_short_of_500": [
            "%s seed=%s rounds=%s" % (r["map"], r["seed"], r["rounds"])
            for r in valid if r["rounds"] != 500],
        "states": dict(collections.Counter(r["state"] for r in valid)),
        "mislocks": len(mis),
        "mislock_detected_by_shipped_window": len(shipped_det),
        "mislock_missed": len(mis) - len(shipped_det),
        "first_contradiction_round": {
            "n_with_evidence": len(with_ev),
            "n_never": never,
            "median_with_evidence": quantile(with_ev, 0.5),
            "p75_with_evidence": quantile(with_ev, 0.75),
            "p90_with_evidence": quantile(with_ev, 0.90),
            "p95_with_evidence": quantile(with_ev, 0.95),
            "p99_with_evidence": quantile(with_ev, 0.99),
            "max_with_evidence": max(with_ev) if with_ev else None,
            "median_all_mislocks": ("never" if (quantile(padded, 0.5) or 0) >= 10 ** 9
                                    else quantile(padded, 0.5)),
            "p75_all_mislocks": ("never" if (quantile(padded, 0.75) or 0) >= 10 ** 9
                                 else quantile(padded, 0.75)),
            "p90_all_mislocks": ("never" if (quantile(padded, 0.90) or 0) >= 10 ** 9
                                 else quantile(padded, 0.90)),
            "p95_all_mislocks": ("never" if (quantile(padded, 0.95) or 0) >= 10 ** 9
                                 else quantile(padded, 0.95)),
        },
        "shipped_window_reported_rounds": {
            "n": len(shipped_det),
            "min": min(shipped_det) if shipped_det else None,
            "median": quantile(shipped_det, 0.5),
            "p95": quantile(shipped_det, 0.95),
            "max": max(shipped_det) if shipped_det else None,
        },
        "silent_lock_in_runs": sum(1 for r in mis if r["final_map_id"] >= 0),
        "lock_ever_revised_twice": sum(1 for r in valid if r["relock_round"] >= 0),
        "all_stay_rounds_max": max((r["all_stay_rounds"] for r in valid), default=None),
    }


def coverage_curve(rows: list[dict]) -> list[dict]:
    mis = [r for r in rows if r["state"].startswith("mislock")]
    out = []
    for bound in BOUNDS:
        caught = sum(1 for r in mis if 0 <= r["visible_conflict_round"] <= bound)
        out.append({"bound": bound, "caught": caught, "of_mislocks": len(mis),
                    "fraction": caught / len(mis) if mis else None})
    return out


def premium_curve(premium: dict) -> dict:
    per_bound: dict[int, list[int]] = {}
    for name in KNOWN:
        for row in premium[name]:
            for key, value in row["cum_verify_scans"].items():
                per_bound.setdefault(int(key), []).append(value)
    scans24 = statistics.fmean(per_bound[24])
    top = max(per_bound)
    scans_all = statistics.fmean(per_bound[top])
    slope = (-77 - -31) / (scans_all - scans24)
    rows = []
    for bound in sorted(per_bound):
        mean = statistics.fmean(per_bound[bound])
        rows.append({"bound": bound, "scans_mean": round(mean, 2),
                     "scans_min": min(per_bound[bound]),
                     "scans_max": max(per_bound[bound]),
                     "gold_premium": round(-31 + slope * (mean - scans24), 1)})
    return {"rows": rows, "anchor_bound_24_scans": round(scans24, 2),
            "anchor_unbounded_scans": round(scans_all, 2),
            "gold_per_scan": round(slope, 3),
            "anchors": {"bound24_gold": -31, "unbounded_gold": -77,
                        "source": "src/CHANGELOG.md 8.10, contest machine, real AVX build"}}


def behaviour_by_state(rows: list[dict]) -> dict:
    out = {}
    for parent in KNOWN:
        for state in ("correct_lock", "no_lock", "mislock_detected",
                      "mislock_missed_late_evidence", "mislock_missed_no_evidence"):
            group = [r for r in rows if r["parent"] == parent and r["state"] == state]
            if not group:
                continue
            blocked = sorted(100.0 * r["stay_or_blocked_steps"] / max(1, r["steps"])
                             for r in group)
            out.setdefault(parent, {})[state] = {
                "n": len(group),
                "blocked_pct_median": round(statistics.median(blocked), 2),
                "blocked_pct_p95": round(quantile(blocked, 0.95), 2),
                "all_stay_rounds_median": statistics.median(
                    sorted(r["all_stay_rounds"] for r in group)),
                "all_stay_rounds_max": max(r["all_stay_rounds"] for r in group),
                "rounds_min": min(r["rounds"] for r in group),
            }
    return out


def randk_table(rows: list[dict]) -> list[dict]:
    by_k: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        if row["map"] in KNOWN:
            continue
        k = int(row["map"].split("_k")[1].split("_")[0])
        by_k[k][row["state"]] += 1
    out = []
    for k in sorted(by_k):
        counter = by_k[k]
        mis = (counter["mislock_detected"] + counter["mislock_missed_late_evidence"]
               + counter["mislock_missed_no_evidence"])
        out.append({"k": k, "runs": sum(counter.values()),
                    "no_lock": counter["no_lock"],
                    "mislock_detected": counter["mislock_detected"],
                    "mislock_missed_late": counter["mislock_missed_late_evidence"],
                    "mislock_missed_none": counter["mislock_missed_no_evidence"],
                    "mislocks": mis,
                    "escape_rate_of_mislocks": round(
                        (counter["mislock_missed_late_evidence"]
                         + counter["mislock_missed_no_evidence"]) / mis, 4)
                    if mis else None})
    return out


def near_miss_bands(rows: list[dict]) -> list[dict]:
    """Where the missed-but-eventually-visible evidence actually lands.

    Answers "is there a cheap bound just past 24 that would pick up most of the
    tail" -- it shows there is not, the density is spread over the whole game.
    """
    mis = [r for r in rows if r["state"].startswith("mislock")]
    late = [r for r in mis if r["state"] == "mislock_missed_late_evidence"]
    out = []
    for lo, hi in ((25, 30), (31, 40), (41, 60), (61, 120), (121, 250), (251, 499)):
        n = sum(1 for r in late if lo <= r["visible_conflict_round"] <= hi)
        out.append({"round_lo": lo, "round_hi": hi, "runs": n,
                    "pct_of_missed_late": round(100.0 * n / len(late), 1) if late else None,
                    "pct_of_all_mislocks": round(100.0 * n / len(mis), 1) if mis else None})
    return out


def cell_grid(rows: list[dict]) -> dict:
    """Per-cell outcome map for the exhaustive single-cell population.

    Symbols: ``D`` detected inside the shipped window, ``L`` mis-locked with the
    contradiction only becoming visible after round 24, ``n`` mis-locked with no
    contradiction ever visible, ``.`` the fingerprint rejected the map (the
    healthy outcome), ``=`` the lock was correct, ``?`` the three seeds disagreed,
    ``#`` no legal perturbation at that cell.
    """
    symbol = {"mislock_detected": "D", "mislock_missed_late_evidence": "L",
              "mislock_missed_no_evidence": "n", "no_lock": ".",
              "correct_lock": "=", "invalid_scenario": "#", "forfeit": "!"}
    out = {}
    for parent in KNOWN:
        per_cell: dict[tuple[int, int], list[dict]] = {}
        for row in rows:
            if row["parent"] != parent or not row["cell"]:
                continue
            per_cell.setdefault(tuple(row["cell"]), []).append(row)
        grid = []
        visible = {}
        for r in range(17):
            line = []
            for c in range(17):
                group = per_cell.get((r, c))
                if not group:
                    line.append("#")
                    continue
                states = {row["state"] for row in group}
                line.append(symbol[group[0]["state"]] if len(states) == 1 else "?")
                visible["%d,%d" % (r, c)] = [row["visible_conflict_round"]
                                             for row in group]
            grid.append("".join(line))
        out[parent] = {"grid": grid, "visible_conflict_round_by_cell": visible}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dryrun", type=Path, required=True)
    parser.add_argument("--bands", type=Path, required=True)
    parser.add_argument("--exhaustive", type=Path, required=True)
    parser.add_argument("--randk", type=Path, required=True)
    parser.add_argument("--late", type=Path, required=True)
    parser.add_argument("--force", nargs="+", type=Path, required=True)
    parser.add_argument("--premium", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--equivalence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    dry = flatten(load(args.dryrun), "shipped24")
    dry_inf = flatten(load(args.dryrun), "unbounded")
    bands = flatten(load(args.bands), "shipped24")
    bands_inf = flatten(load(args.bands), "unbounded")
    exhaustive = flatten(load(args.exhaustive), "shipped24")
    randk = flatten(load(args.randk), "shipped24")
    late_ship = flatten(load(args.late), "shipped24")
    late_inf = flatten(load(args.late), "unbounded")

    payload = {
        "provenance": {
            "construct_commit": "fd47ea6",
            "player_cpp_sha256":
                "df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217",
            "build": "clang++ -O2 -std=c++17 -shared -fPIC, host arm64 dylib, "
                     "guarded scalar fallback, x86 prefetch tokens stubbed",
            "verify_rounds_under_test": 24,
            "opponent": "passive stay seat",
            "dispatch": "fixed, costs (200, 100000) so seat 1 always moves first",
            "rounds_per_game": 500,
            "probe_inert_check": "log SHA-256 identical, base vs instrumented, "
                                 "14/14 games across map1/2/3, mimic1/2, dense, corridor",
        },
        "dry_run": {"shipped24": population(dry), "unbounded": population(dry_inf),
                    "per_map": dry},
        "bands": {"shipped24": population(bands), "unbounded": population(bands_inf),
                  "per_map": bands},
        "exhaustive": population(exhaustive),
        "exhaustive_per_parent": {},
        "randk": {"population": population(randk), "by_k": randk_table(randk)},
        "late_sample_pairing": {},
        "force_lock": {},
        "coverage_curve_exhaustive": coverage_curve(exhaustive),
        "coverage_curve_bands": coverage_curve(bands),
        "premium_curve": premium_curve(load(args.premium)),
        "behaviour_by_state_exhaustive": behaviour_by_state(
            [r for r in exhaustive if r["state"] not in ("invalid_scenario",)]),
        "exhaustive_cell_grid": cell_grid(exhaustive),
        "exhaustive_near_miss_bands": near_miss_bands(exhaustive),
        "census": {},
        "equivalence_record": args.equivalence.read_text(
            encoding="utf-8").splitlines(),
    }

    for parent in KNOWN:
        payload["exhaustive_per_parent"][parent] = population(
            [r for r in exhaustive if r["parent"] == parent])

    pairs = {}
    for row in late_ship:
        pairs[(row["map"], row["seed"])] = {"shipped_visible": row["visible_conflict_round"],
                                            "shipped_mech": row["mech_conflict_round"],
                                            "shipped_final": row["final_map_id"]}
    equal = 0
    recovered = 0
    total = 0
    for row in late_inf:
        key = (row["map"], row["seed"])
        if key not in pairs:
            continue
        total += 1
        pairs[key]["unbounded_mech"] = row["mech_conflict_round"]
        pairs[key]["unbounded_final"] = row["final_map_id"]
        pairs[key]["unbounded_rounds"] = row["rounds"]
        if pairs[key]["shipped_visible"] == row["mech_conflict_round"]:
            equal += 1
        if row["final_map_id"] == -2 and row["rounds"] == 500:
            recovered += 1
    payload["late_sample_pairing"] = {
        "paired_runs": total,
        "shipped_visible_equals_unbounded_mech": equal,
        "unbounded_recovered_to_lazy_and_finished": recovered,
        "not_recovered_because_no_evidence_in_that_seed": [
            k for k, v in pairs.items()
            if v.get("unbounded_final", -2) >= 0 and v["shipped_visible"] < 0],
        "detail": {"%s|seed%s" % k: v for k, v in sorted(pairs.items())},
    }

    for path in args.force:
        data = load(path)
        builds = sorted({r["build"] for e in data.values() for r in e["runs"]})
        payload["force_lock"][path.stem] = {
            build: flatten(data, build) for build in builds}

    census = load(args.census)
    # S3 frontier point: the same new-cell event stream, two window shapes.
    shapes = {"cheb2_bound24": [], "cheb2_all_game": [],
              "manh1_all_game": [], "manh1_bound24": [], "cells_stood_on": []}
    for name in KNOWN:
        for row in census[name]:
            cheb = row["first"]
            manh = row.get("first_manh1") or {}
            shapes["cheb2_bound24"].append(sum(1 for v in cheb.values() if v <= 24))
            shapes["cheb2_all_game"].append(len(cheb))
            shapes["manh1_all_game"].append(len(manh))
            shapes["manh1_bound24"].append(sum(1 for v in manh.values() if v <= 24))
            shapes["cells_stood_on"].append(row.get("distinct_cells_stood_on"))
    payload["detector_shapes"] = {
        "note": "cells of 289 covered by each detector shape, mean over the three "
                "known maps x three seeds; same trajectories, same new-cell event "
                "stream, only the inspected window differs",
        "means": {k: round(statistics.fmean([x for x in v if x is not None]), 1)
                  for k, v in shapes.items()},
        "per_game": shapes,
    }
    for name in KNOWN:
        rows = []
        for row in census[name]:
            values = sorted(row["first"].values())
            never = 289 - len(values)
            padded = values + [10 ** 9] * never
            rows.append({
                "seed": row["seed"], "cells_ever_compared": len(values),
                "never_compared": never,
                "median": quantile(padded, 0.50),
                "p75": ("never" if quantile(padded, 0.75) >= 10 ** 9
                        else quantile(padded, 0.75)),
                "p90": ("never" if quantile(padded, 0.90) >= 10 ** 9
                        else quantile(padded, 0.90)),
                "max_finite": values[-1] if values else None,
                "compared_by_24": sum(1 for v in values if v <= 24),
                "late_after_24": sum(1 for v in values if v > 24) + never,
                "late_after_24_pct": round(
                    100.0 * (sum(1 for v in values if v > 24) + never) / 289, 1),
            })
        payload["census"][name] = rows

    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print("wrote %s (%d bytes)" % (args.out, args.out.stat().st_size))
    print("exhaustive states:", payload["exhaustive"]["states"])
    print("first-contradiction round:",
          payload["exhaustive"]["first_contradiction_round"])
    print("shipped reported rounds:",
          payload["exhaustive"]["shipped_window_reported_rounds"])
    for row in payload["premium_curve"]["rows"]:
        print("  bound %-4s scans %6.2f gold %6.1f" % (
            row["bound"], row["scans_mean"], row["gold_premium"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
