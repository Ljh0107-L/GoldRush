#!/usr/bin/env python3
"""analyze_tail_ab.py — paired A/B for the tail-width knives.

WHY PAIRED PER-ROUND: the primary summary `P90 - P50` in TSC ticks is quantised to 27
ticks on this VM, and the machine (load 63-90) makes the 90th-percentile *round* flip
across that boundary between runs. Comparing independent runs therefore cannot resolve an
effect of one quantum. The byte-identical null control proves the flips are a shared window
effect rather than construct noise: it reproduces the baseline's fluctuation run for run.
That is exactly the condition under which **paired within-run differencing** works, because
the shared component cancels.

So three views are produced, in increasing sensitivity:

  1. `per_run_quantiles` — the headline statistic (P50/P90/width) computed independently per
     run for every .so, including the null control. Quantised, but it is the number the
     acceptance criterion is written in.
  2. `paired_pXX` — P50/P90/width differences taken *within* each run, then summarised
     across runs. The shared window component cancels.
  3. `paired_by_path` — the median per-round difference restricted to the rounds a knife can
     possibly affect. A knife aimed at 5% of rounds moves the global P90 by at most a
     fraction of its per-round effect, so measuring it on its own rounds is the only way to
     see whether it did what it was designed to do, and with what sign.

usage: python3 sim/analyze_tail_ab.py DATA_DIR --stream ab_tsc [--json OUT.json]
"""
import argparse
import csv
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_tail_width import MAPS, STEADY_FROM, classify, quant, read_masks, route_only


def read_multi(path):
    """-> {so_name: {run: {round: value}}}, preserving command-line order."""
    out, order = {}, []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            so = row["so"]
            if so not in out:
                out[so] = {}
                order.append(so)
            out[so].setdefault(int(row["run"]), {})[int(row["round"])] = int(row["value"])
    return out, order


def quantiles(vals):
    p50, p90 = quant(vals, 0.50), quant(vals, 0.90)
    return {"p50": p50, "p90": p90, "width": p90 - p50, "p99": quant(vals, 0.99)}


def summarise(xs):
    return {"median": statistics.median(xs), "min": min(xs), "max": max(xs),
            "mean": round(statistics.fmean(xs), 2),
            "sd": round(statistics.stdev(xs), 2) if len(xs) > 1 else None,
            "n_runs": len(xs)}


def wave_of(mask):
    return bool(mask & (1 << 4))


def path_sets(masks, rounds):
    """Round subsets a knife can affect, as disjoint-where-it-matters groups."""
    sets = {
        "all": rounds,
        "no-fallback": [r for r in rounds if route_only(masks[r]) == "no-fallback"],
        "fallback": [r for r in rounds if route_only(masks[r]) == "fallback"],
        "escape": [r for r in rounds if route_only(masks[r]) == "escape"],
        "wave": [r for r in rounds if wave_of(masks[r])],
        "wave&blocked": [r for r in rounds if wave_of(masks[r])
                         and route_only(masks[r]) in ("fallback", "escape")],
        "wave&no-fallback": [r for r in rounds if wave_of(masks[r])
                             and route_only(masks[r]) == "no-fallback"],
        "blocked(any)": [r for r in rounds if route_only(masks[r]) in ("fallback", "escape")],
    }
    return sets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--stream", required=True, help="e.g. ab_tsc / ab_pmc / ab_insn")
    ap.add_argument("--base", default="./base.so")
    ap.add_argument("--json")
    args = ap.parse_args()

    result = {"stream": args.stream, "base": args.base, "maps": {}}
    for game, mapname in MAPS.items():
        csv_path = os.path.join(args.data_dir, "%s_%s.csv" % (args.stream, game))
        mask_path = os.path.join(args.data_dir, "masks_%s.txt" % game)
        if not (os.path.exists(csv_path) and os.path.exists(mask_path)):
            continue
        masks = read_masks(mask_path)
        data, order = read_multi(csv_path)
        runs = sorted(data[args.base])
        rounds = [r for r in sorted(data[args.base][runs[0]]) if r >= STEADY_FROM]
        sets = path_sets(masks, rounds)

        entry = {"map": mapname, "n_runs": len(runs), "n_rounds": len(rounds),
                 "per_run_quantiles": {}, "paired": {}, "paired_by_path": {},
                 "decile": {}}
        for so in order:
            entry["per_run_quantiles"][so] = [
                quantiles([data[so][run][r] for r in rounds]) for run in runs]

        for so in order:
            if so == args.base:
                continue
            dp50, dp90, dw = [], [], []
            for run in runs:
                a = quantiles([data[args.base][run][r] for r in rounds])
                b = quantiles([data[so][run][r] for r in rounds])
                dp50.append(b["p50"] - a["p50"])
                dp90.append(b["p90"] - a["p90"])
                dw.append(b["width"] - a["width"])
            entry["paired"][so] = {"d_p50": summarise(dp50), "d_p90": summarise(dp90),
                                   "d_width": summarise(dw)}
            byp = {}
            for name, rs in sets.items():
                if not rs:
                    continue
                # per-round difference, median over runs first (kills run noise), then the
                # median over the rounds in the subset.
                per_round = []
                for r in rs:
                    d = [data[so][run][r] - data[args.base][run][r] for run in runs]
                    per_round.append(statistics.median(d))
                byp[name] = {"n": len(rs),
                             "median_d": statistics.median(per_round),
                             "mean_d": round(statistics.fmean(per_round), 2),
                             "share_improved": round(
                                 100.0 * sum(1 for x in per_round if x < 0) / len(per_round), 1),
                             "share_worse": round(
                                 100.0 * sum(1 for x in per_round if x > 0) / len(per_round), 1)}
            entry["paired_by_path"][so] = byp

        # Decile band composition per construct: did the 90th percentile relocate?
        for so in order:
            cons = {r: statistics.median([data[so][run][r] for run in runs]) for r in rounds}
            cut = quant([cons[r] for r in rounds], 0.90)
            slow = [r for r in rounds if cons[r] >= cut]
            comp, joint = {}, {}
            for r in slow:
                rt = route_only(masks[r])
                comp[rt] = comp.get(rt, 0) + 1
                joint[(rt, int(wave_of(masks[r])))] = joint.get((rt, int(wave_of(masks[r]))), 0) + 1
            entry["decile"][so] = {
                "cut": cut, "n": len(slow),
                "by_route_pct": {k: round(100.0 * v / len(slow), 1)
                                 for k, v in sorted(comp.items())},
                "by_route_n": dict(sorted(comp.items())),
                "route_x_wave_n": {"%s|wave=%d" % k: v for k, v in sorted(joint.items())},
            }
        result["maps"][game] = entry

    # ------------------------------------------------------------------- printing
    print("stream=%s   base=%s" % (args.stream, args.base))
    for game, e in result["maps"].items():
        print("\n### %s (game %s) n_rounds=%d n_runs=%d" % (e["map"], game,
                                                            e["n_rounds"], e["n_runs"]))
        print("per-run width, one column per run (quantised to 27 for tsc):")
        for so, qs in e["per_run_quantiles"].items():
            print("  %-12s P50 %s | P90 %s | width %s"
                  % (so, [q["p50"] for q in qs], [q["p90"] for q in qs],
                     [q["width"] for q in qs]))
        print("paired within-run deltas (median [min,max] over runs):")
        for so, p in e["paired"].items():
            print("  %-12s dP50 %+d [%+d,%+d]  dP90 %+d [%+d,%+d]  dWIDTH %+d [%+d,%+d]"
                  % (so, p["d_p50"]["median"], p["d_p50"]["min"], p["d_p50"]["max"],
                     p["d_p90"]["median"], p["d_p90"]["min"], p["d_p90"]["max"],
                     p["d_width"]["median"], p["d_width"]["min"], p["d_width"]["max"]))
        print("paired per-round delta by path (median over runs then over rounds):")
        for so, byp in e["paired_by_path"].items():
            bits = ["%s n=%d %+g" % (k, v["n"], v["median_d"]) for k, v in byp.items()]
            print("  %-12s %s" % (so, "  ".join(bits)))
        print("slowest-decile composition by route:")
        for so, dv in e["decile"].items():
            print("  %-12s cut=%s n=%d  %s" % (so, dv["cut"], dv["n"], dv["by_route_pct"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
