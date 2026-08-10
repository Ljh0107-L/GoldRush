#!/usr/bin/env python3
"""Compare archived platform end.cost with latency_bench CSV samples.

Example:
  python3 tests/analyze_latency.py --samples-dir tests/latency_work \
      --logs-dir logs --games 175847 176396 176389

The local comparison uses the median of repeated measurements for each round,
so the 480 steady-state platform rounds are compared with 480 local points
rather than treating repeated calls of the same input as independent games.
"""
import argparse
import csv
import json
import math
import pathlib
import statistics


def percentile(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))]


def summary(values):
    return {
        "n": len(values),
        "min": min(values),
        "p10": percentile(values, 10),
        "p25": percentile(values, 25),
        "p50": percentile(values, 50),
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def wasserstein_equal(a, b):
    a, b = sorted(a), sorted(b)
    assert len(a) == len(b)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def ks_distance(a, b):
    a, b = sorted(a), sorted(b)
    ia = ib = 0
    distance = 0.0
    while ia < len(a) or ib < len(b):
        if ib == len(b) or (ia < len(a) and a[ia] <= b[ib]):
            value = a[ia]
        else:
            value = b[ib]
        while ia < len(a) and a[ia] <= value:
            ia += 1
        while ib < len(b) and b[ib] <= value:
            ib += 1
        distance = max(distance, abs(ia / len(a) - ib / len(b)))
    return distance


def correlation(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    return numerator / math.sqrt(da * db) if da and db else 0.0


def read_platform(path, player_id, cutoff):
    with path.open() as handle:
        header = json.loads(next(handle))
        next(handle)  # initial/map record
        rows = [json.loads(line) for line in handle]
    costs = {}
    for row in rows:
        if row["round"] < cutoff:
            continue
        player = next(p for p in row["end"]["players"] if p["id"] == player_id)
        costs[row["round"]] = player["cost"]
    return header, costs


def read_local(path, cutoff):
    by_round = {}
    by_rep = {}
    residues = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            round_id = int(row["round"])
            if round_id < cutoff:
                continue
            elapsed = int(row["elapsed_ns"])
            rep = int(row["rep"])
            by_round.setdefault(round_id, []).append(elapsed)
            by_rep.setdefault(rep, []).append(elapsed)
            residues[elapsed % 10] = residues.get(elapsed % 10, 0) + 1
    medians = {round_id: int(statistics.median(values))
               for round_id, values in by_round.items()}
    pooled = [value for values in by_round.values() for value in values]
    return medians, pooled, by_rep, residues


def compare_game(logs_dir, samples_dir, game, modes, player_id, cutoff):
    header, platform = read_platform(logs_dir / f"game_{game}.log", player_id, cutoff)
    result = {
        "game": game,
        "player1": header.get("player1"),
        "player2": header.get("player2"),
        "platform": summary(list(platform.values())),
        "platform_residues_mod10": {
            str(r): sum(value % 10 == r for value in platform.values()) for r in range(10)
        },
        "modes": {},
    }
    for mode in modes:
        path = samples_dir / f"samples_{game}_{mode}.csv"
        local, pooled, by_rep, residues = read_local(path, cutoff)
        rounds = sorted(set(platform) & set(local))
        pvalues = [platform[r] for r in rounds]
        lvalues = [local[r] for r in rounds]
        offsets = [p - l for p, l in zip(pvalues, lvalues)]
        median_offset = percentile(offsets, 50)
        shifted = [value + median_offset for value in lvalues]
        rep_p50 = [percentile(values, 50) for _, values in sorted(by_rep.items())]
        rep_p90 = [percentile(values, 90) for _, values in sorted(by_rep.items())]
        result["modes"][mode] = {
            "local_pooled": summary(pooled),
            "local_round_medians": summary(lvalues),
            "local_residues_mod10": {str(k): v for k, v in sorted(residues.items())},
            "rep_p50_range": [min(rep_p50), max(rep_p50)],
            "rep_p90_range": [min(rep_p90), max(rep_p90)],
            "platform_minus_local_round": summary(offsets),
            "pearson_same_round": round(correlation(pvalues, lvalues), 6),
            "wasserstein_raw_ns": round(wasserstein_equal(pvalues, lvalues), 3),
            "ks_raw": round(ks_distance(pvalues, lvalues), 6),
            "median_shift_ns": median_offset,
            "wasserstein_after_median_shift_ns": round(
                wasserstein_equal(pvalues, shifted), 3),
            "ks_after_median_shift": round(ks_distance(pvalues, shifted), 6),
        }
    return result


def aggregate(results, modes):
    # Aggregate already-computed per-game quantile summaries are not valid.
    # Re-read raw arrays in main if a pooled distribution is needed; this block
    # intentionally reports robust ranges across games instead.
    out = {}
    for mode in modes:
        out[mode] = {
            "local_round_median_p50_range": [
                min(r["modes"][mode]["local_round_medians"]["p50"] for r in results),
                max(r["modes"][mode]["local_round_medians"]["p50"] for r in results),
            ],
            "local_round_median_p90_range": [
                min(r["modes"][mode]["local_round_medians"]["p90"] for r in results),
                max(r["modes"][mode]["local_round_medians"]["p90"] for r in results),
            ],
            "median_offset_range": [
                min(r["modes"][mode]["median_shift_ns"] for r in results),
                max(r["modes"][mode]["median_shift_ns"] for r in results),
            ],
            "shifted_wasserstein_range": [
                min(r["modes"][mode]["wasserstein_after_median_shift_ns"] for r in results),
                max(r["modes"][mode]["wasserstein_after_median_shift_ns"] for r in results),
            ],
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples-dir", type=pathlib.Path, required=True)
    parser.add_argument("--logs-dir", type=pathlib.Path, required=True)
    parser.add_argument("--games", type=int, nargs="+", required=True)
    parser.add_argument("--modes", nargs="+", default=["hot", "cold", "cold2"])
    parser.add_argument("--player-id", type=int, default=1)
    parser.add_argument("--cutoff", type=int, default=20)
    args = parser.parse_args()
    games = [compare_game(args.logs_dir, args.samples_dir, game, args.modes,
                          args.player_id, args.cutoff) for game in args.games]
    print(json.dumps({"cutoff": args.cutoff, "games": games,
                      "cross_game_ranges": aggregate(games, args.modes)},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
