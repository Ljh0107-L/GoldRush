#!/usr/bin/env python3
"""analyze_tail_width.py — path attribution for the P90 - P50 decision-latency tail.

Consumes two artifact families produced on the contest build machine:

  * `masks_<game>.txt`  — per-round path label, from `tail_path_bench --mask-only` against
    the build derived by `sim/tail_trace_patch.py` (verified `pair_diff` 0/500 vs baseline
    on three maps, so the labels describe the baseline's paths).
  * `<counter>_<mode>_<game>.csv` — per-round value, min over reps, from
    `tail_path_bench --counter ... --evict ...` against the *unmodified* baseline .so.

and answers three separate questions that are easy to conflate:

  1. **Path selection** — what share of rounds is on each path, and how far apart are the
     path medians. This is the ceiling on what constant-shaping a branch chain can buy.
  2. **Within-path spread** — each path's own P90 - P50. If this is large, the tail is
     driven by variation *inside* a path and removing branches cannot reach it.
  3. **Slowest-decile composition** — which paths actually populate the slow decile.

It also reports the between/within variance split, and the run-to-run scatter of the
`P90 - P50` statistic itself, because a tail-width figure whose scatter exceeds the effect
is not quotable on a machine at load ~64.

usage: python3 sim/analyze_tail_width.py DATA_DIR [--json OUT.json] [--md OUT.md]
"""
import argparse
import csv
import json
import os
import statistics
import sys

BITS = {
    0: "blocked_u0", 1: "blocked_u1", 2: "escape", 3: "slowtick",
    4: "wave", 5: "reset", 6: "blind_u0", 7: "blind_u1",
    8: "slowmove", 9: "rich_u0", 10: "rich_u1",
}
MAPS = {"175847": "map1(40 walls)", "176396": "map2(24 walls)", "176389": "map3(78 walls)"}
STEADY_FROM = 20
PATH_ORDER = ["no-fallback", "fallback", "escape", "other"]


def classify(mask):
    """Single label per round. Cold layers win, because they are a different question."""
    if mask & (1 << 5):
        return "other", "reset"
    if mask & ((1 << 3) | (1 << 8)):
        return "other", "slow-start"
    if mask & (1 << 4):
        return "other", "waveTick(%20)"
    if mask & (1 << 2):
        return "escape", "escape"
    if mask & 0b11:
        return "fallback", "fallback"
    return "no-fallback", "no-fallback"


def route_only(mask):
    """Route label independent of the cold layers.

    `classify` folds every `waveTick` round into `other`, which hides the fact that a
    `waveTick` round *also* has a route, and that the ones which land in the slow decile
    are precisely the ones that are `waveTick` AND blocked. This second view keeps the two
    axes orthogonal so neither is credited with the other's cost.
    """
    if mask & ((1 << 5) | (1 << 3) | (1 << 8)):
        return "cold-layer"                      # reset / slow-start: 1-4 rounds per game
    if mask & (1 << 2):
        return "escape"
    if mask & 0b11:
        return "fallback"
    return "no-fallback"


def quant(values, q):
    """Same index convention as the C harness: v[floor(n*q)]."""
    if not values:
        return None
    s = sorted(values)
    i = min(len(s) - 1, int(len(s) * q))
    return s[i]


def read_masks(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[int(row["round"])] = int(row["mask"])
    return out


def read_values(path, so_index=0):
    """-> {run: {round: value}} for one .so in the file (index 0 = first on the cmdline).

    Multi-.so files exist because rep-level interleaving of baseline and a control (the
    trivial player, used as the instrument floor) is the drift-immune protocol; the
    control's rows must not be pooled into the subject's distribution.
    """
    runs = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if int(row["so_index"]) != so_index:
                continue
            runs.setdefault(int(row["run"]), {})[int(row["round"])] = int(row["value"])
    return runs


def per_round_consensus(runs):
    """Median across independent runs, per round: the round's own reproducible level."""
    rounds = sorted(next(iter(runs.values())).keys())
    return {r: int(statistics.median([runs[k][r] for k in runs])) for r in rounds}


def attribute(masks, values, steady_from=STEADY_FROM):
    rounds = [r for r in sorted(values) if r >= steady_from]
    groups, sub = {}, {}
    for r in rounds:
        label, detail = classify(masks[r])
        groups.setdefault(label, []).append(values[r])
        sub.setdefault(detail, []).append(values[r])
    allv = [values[r] for r in rounds]
    p90_all, p50_all = quant(allv, 0.90), quant(allv, 0.50)
    slow_cut = quant(allv, 0.90)
    slow_rounds = [r for r in rounds if values[r] >= slow_cut]
    slow_by_label = {}
    slow_by_route = {}
    for r in slow_rounds:
        lab = classify(masks[r])[0]
        slow_by_label[lab] = slow_by_label.get(lab, 0) + 1
        rt = route_only(masks[r])
        slow_by_route[rt] = slow_by_route.get(rt, 0) + 1

    # Orthogonal view: route x waveTick. The `%20` bomb-memory clear is 5% of rounds and
    # P90 is the top 10%, so every waveTick round is structurally inside the tail; it must
    # be priced separately from the route it happens to share a round with.
    rw = {}
    for r in rounds:
        rw.setdefault((route_only(masks[r]), bool(masks[r] & (1 << 4))), []).append(values[r])
    route_wave = {}
    for (rt, wv), v in rw.items():
        p50, p90 = quant(v, 0.50), quant(v, 0.90)
        route_wave["%s|wave=%d" % (rt, int(wv))] = {
            "n": len(v), "share_pct": round(100.0 * len(v) / len(rounds), 2),
            "p50": p50, "p90": p90, "within_width": p90 - p50}
    wave_delta = {}
    for rt in ("no-fallback", "fallback", "escape"):
        a = route_wave.get("%s|wave=0" % rt)
        b = route_wave.get("%s|wave=1" % rt)
        if a and b:
            wave_delta[rt] = {"n_wave": b["n"], "delta_p50": b["p50"] - a["p50"]}

    def table(d):
        out = {}
        for k, v in d.items():
            p50, p90 = quant(v, 0.50), quant(v, 0.90)
            out[k] = {
                "n": len(v), "share_pct": round(100.0 * len(v) / len(rounds), 2),
                "p50": p50, "p90": p90, "within_width": p90 - p50,
                "p99": quant(v, 0.99), "min": min(v), "max": max(v),
            }
        return out

    # Between-group vs within-group dispersion: how much of the round-to-round variance
    # is explained by which path was taken.
    grand = statistics.fmean(allv)
    between = sum(len(v) * (statistics.fmean(v) - grand) ** 2 for v in groups.values())
    within = sum(sum((x - statistics.fmean(v)) ** 2 for x in v) for v in groups.values())
    eta2 = between / (between + within) if (between + within) else 0.0

    return {
        "n_rounds": len(rounds), "steady_from": steady_from,
        "all": {"p50": p50_all, "p90": p90_all, "width": p90_all - p50_all,
                "p99": quant(allv, 0.99), "min": min(allv), "max": max(allv)},
        "paths": table(groups), "detail": table(sub),
        "route_x_wave": route_wave, "wave_delta": wave_delta,
        "slow_decile": {
            "cut": slow_cut, "n": len(slow_rounds),
            "composition_pct": {k: round(100.0 * v / len(slow_rounds), 2)
                                for k, v in sorted(slow_by_label.items())},
            "composition_n": dict(sorted(slow_by_label.items())),
            "route_composition_pct": {k: round(100.0 * v / len(slow_rounds), 2)
                                      for k, v in sorted(slow_by_route.items())},
        },
        "variance_split": {"eta2_path": round(eta2, 4),
                           "between": round(between, 1), "within": round(within, 1)},
    }


def run_scatter(masks, runs, steady_from=STEADY_FROM):
    """P50/P90/width computed independently per run — the honest error bar."""
    out = []
    for k in sorted(runs):
        v = [runs[k][r] for r in sorted(runs[k]) if r >= steady_from]
        p50, p90 = quant(v, 0.50), quant(v, 0.90)
        out.append({"run": k, "p50": p50, "p90": p90, "width": p90 - p50})
    widths = [x["width"] for x in out]
    p50s = [x["p50"] for x in out]
    return {
        "per_run": out,
        "width_mean": round(statistics.fmean(widths), 2),
        "width_sd": round(statistics.stdev(widths), 2) if len(widths) > 1 else None,
        "width_min": min(widths), "width_max": max(widths),
        "p50_min": min(p50s), "p50_max": max(p50s),
        "p50_spread": max(p50s) - min(p50s),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--json")
    ap.add_argument("--md")
    ap.add_argument("--streams", nargs="*", default=None,
                    help="counter_mode prefixes to analyse, e.g. tsc_code insn")
    ap.add_argument("--so-index", type=int, default=0,
                    help="which interleaved .so to attribute (0 = subject, 1 = control)")
    args = ap.parse_args()

    d = args.data_dir
    streams = args.streams
    if not streams:
        streams = sorted({f.rsplit("_", 1)[0] for f in os.listdir(d) if f.endswith(".csv")})
    result = {"data_dir": os.path.abspath(d), "streams": {}}
    lines = []
    for stream in streams:
        result["streams"][stream] = {}
        for game, mapname in MAPS.items():
            csv_path = os.path.join(d, "%s_%s.csv" % (stream, game))
            mask_path = os.path.join(d, "masks_%s.txt" % game)
            if not (os.path.exists(csv_path) and os.path.exists(mask_path)):
                continue
            masks = read_masks(mask_path)
            runs = read_values(csv_path, args.so_index)
            if not runs:
                continue
            cons = per_round_consensus(runs)
            entry = attribute(masks, cons)
            entry["scatter"] = run_scatter(masks, runs)
            entry["n_runs"] = len(runs)
            entry["map"] = mapname
            result["streams"][stream][game] = entry

            lines.append("")
            lines.append("### %s / %s (game %s, %d runs x min-over-reps)"
                         % (stream, mapname, game, len(runs)))
            a = entry["all"]
            lines.append("all steady rounds n=%d: P50=%s P90=%s **width=%s** P99=%s"
                         % (entry["n_rounds"], a["p50"], a["p90"], a["width"], a["p99"]))
            lines.append("")
            lines.append("| path | share | P50 | P90 | within-path width | share of slowest decile |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            comp = entry["slow_decile"]["composition_pct"]
            for k in PATH_ORDER:
                if k not in entry["paths"]:
                    continue
                p = entry["paths"][k]
                lines.append("| %s | %.1f%% | %s | %s | **%s** | %.1f%% |"
                             % (k, p["share_pct"], p["p50"], p["p90"], p["within_width"],
                                comp.get(k, 0.0)))
            lines.append("")
            lines.append("route x waveTick (orthogonal; waveTick is 5% of rounds and P90 is "
                         "the top 10%, so every waveTick round is structurally in the tail):")
            lines.append("| route | wave | share | P50 | P90 | within width |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for k, v in sorted(entry["route_x_wave"].items()):
                rt, wv = k.split("|")
                lines.append("| %s | %s | %.1f%% | %s | %s | %s |"
                             % (rt, wv[-1], v["share_pct"], v["p50"], v["p90"],
                                v["within_width"]))
            lines.append("waveTick P50 penalty by route: " + ", ".join(
                "%s +%s (n=%d)" % (k, v["delta_p50"], v["n_wave"])
                for k, v in sorted(entry["wave_delta"].items())))
            lines.append("slow-decile composition by route (cold layers not folded in): "
                         + ", ".join("%s %.1f%%" % (k, v) for k, v in
                                     sorted(entry["slow_decile"]["route_composition_pct"].items())))
            lines.append("")
            lines.append("path-selection variance share eta2=%.3f; run-to-run width sd=%s "
                         "(min %s / max %s); per-run P50 spread=%s"
                         % (entry["variance_split"]["eta2_path"],
                            entry["scatter"]["width_sd"], entry["scatter"]["width_min"],
                            entry["scatter"]["width_max"], entry["scatter"]["p50_spread"]))
            lines.append("")
            lines.append("sub-paths: " + ", ".join(
                "%s n=%d P50=%s P90=%s w=%s" % (k, v["n"], v["p50"], v["p90"], v["within_width"])
                for k, v in sorted(entry["detail"].items())))

    text = "\n".join(lines)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, ensure_ascii=False)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
