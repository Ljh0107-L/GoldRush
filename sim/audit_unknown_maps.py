#!/usr/bin/env python3
"""Unfamiliar-map robustness audit for the delivered construct.

The construct treats every map as unknown (no baked wall tables, no fingerprint
lock -- that mechanism was removed in ``dbdcbdb``): it learns walls online and
retires learning once both anchors are confirmed.  The organisers declined to
guarantee the preliminary reuses the public terrain, so two failure modes are
audited on every registered synthetic/unknown map:

1. **Hard failure** -- an exception or an illegal action list.  ``moveDecision``
   is wrapped in try/catch returning an all-stay ``SAFE_OUT``, so a latent bug
   surfaces as silent total passivity rather than a crash.  This audit therefore
   counts all-stay rounds explicitly instead of trusting "it did not throw".
2. **Behavioural collapse** -- net gold under half the known-map mean, games not
   reaching 500 rounds, or a blocked-step rate blow-up on dense-wall geometry.

Usage:
    python3 sim/audit_unknown_maps.py --so /path/to/player.so [--seeds 0 1 2]
                                      [--maps map3 dense ...] [--json out.json]

Run on i5 (latency-free income machine); the .so must be a Gold-built artifact.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KNOWN = ("map1", "map2", "map3")
UNKNOWN_REGISTRY = ROOT / "sim" / "maps_unknown.json"


def load_maps(names: list[str] | None) -> list[tuple[str, object]]:
    from sim.scenario import MapDefinition
    out: list[tuple[str, object]] = []
    for name in KNOWN:
        out.append((name, MapDefinition.by_name(name)))
    payload = json.loads(UNKNOWN_REGISTRY.read_text(encoding="utf-8"))
    for name in sorted(payload["maps"]):
        out.append((name, MapDefinition.from_json_file(UNKNOWN_REGISTRY, map_name=name)))
    if names:
        keep = set(names)
        out = [item for item in out if item[0] in keep]
    return out


def run_one(strategy, map_def, seed: str) -> dict:
    """Play one game and measure passivity from the official-format log.

    ``GameResult`` exposes only ``summary`` and ``log_bytes``, so the per-round
    detail is read back out of the log with the same parser shape used for
    platform logs.  ``effective`` actions are what the engine actually executed,
    so a 4 there means "stayed or was blocked" -- exactly the signal that
    distinguishes a working strategy from one silently returning SAFE_OUT.
    """
    from sim.runner import run_game
    result = run_game(
        strategy, "stay", map_source=map_def, seed=seed, dispatch="fixed",
        fixed_costs=(200, 100000), player1_name="cand", player2_name="passive",
    )
    summary = result.summary
    lines = result.log_bytes.decode("utf-8").splitlines()
    rounds = 0
    stay_rounds = 0
    blocked = 0
    steps = 0
    pickups = 0
    for line in lines[2:]:
        if not line.strip():
            continue
        record = json.loads(line)
        if "end" not in record:
            continue
        rounds += 1
        me = next(p for p in record["end"]["players"] if int(p["id"]) == 1)
        acts = [int(a) for unit in me["units"] for a in (unit.get("actions") or ())]
        if acts and all(a == 4 for a in acts):
            stay_rounds += 1
        steps += len(acts)
        blocked += sum(1 for a in acts if a == 4)
        pickups += sum(int(unit.get("pickup") or 0) for unit in me["units"])
    return {
        "seed": seed,
        "rounds": rounds,
        "net_gold": int(summary["players"]["1"]["net_gold"]),
        "all_stay_rounds": stay_rounds,
        "stay_or_blocked_steps": blocked,
        "steps": steps,
        "pickup": pickups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--so", required=True, type=Path, help="frozen construct .so")
    parser.add_argument("--seeds", nargs="+", default=["0", "1"])
    parser.add_argument("--maps", nargs="+", default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    from sim.abi import SharedObjectStrategy

    results: dict[str, dict] = {}
    hard_failures: list[str] = []
    print("%-12s %6s %8s %9s %10s %11s" % (
        "map", "walls", "net", "rounds", "all-stay", "blocked%"))
    for name, map_def in load_maps(args.maps):
        walls = len(map_def.walls)
        per_seed = []
        for seed in args.seeds:
            # A fresh library image per game so cross-round state cannot leak
            # between maps and mask a first-game-only bug.
            with SharedObjectStrategy(args.so, name="cand") as strategy:
                try:
                    per_seed.append(run_one(strategy, map_def, seed))
                except Exception as exc:              # noqa: BLE001 - audit must not stop
                    hard_failures.append("%s seed=%s: %s: %s"
                                         % (name, seed, type(exc).__name__, exc))
                    continue
        if not per_seed:
            print("%-12s %6d  %s" % (name, walls, "ALL SEEDS FAILED"))
            continue
        net = statistics.fmean(r["net_gold"] for r in per_seed)
        rounds = min(r["rounds"] for r in per_seed)
        stay = statistics.fmean(r["all_stay_rounds"] for r in per_seed)
        blk = 100.0 * sum(r["stay_or_blocked_steps"] for r in per_seed) / max(
            1, sum(r["steps"] for r in per_seed))
        results[name] = {"walls": walls, "net_mean": net, "rounds_min": rounds,
                         "all_stay_rounds_mean": stay, "stay_or_blocked_pct": blk,
                         "per_seed": per_seed}
        print("%-12s %6d %8.0f %9d %10.1f %11.1f" % (
            name, walls, net, rounds, stay, blk))

    print()
    known_net = [results[n]["net_mean"] for n in KNOWN if n in results]
    baseline = statistics.fmean(known_net) if known_net else None
    alerts: list[str] = []
    for name, data in results.items():
        if name in KNOWN:
            continue
        if data["rounds_min"] < 500:
            alerts.append("%s: only %d rounds completed" % (name, data["rounds_min"]))
        if data["all_stay_rounds_mean"] > 25:
            alerts.append("%s: %.0f all-stay rounds (SAFE_OUT / total passivity suspected)"
                          % (name, data["all_stay_rounds_mean"]))
        if baseline and data["net_mean"] < 0.5 * baseline:
            alerts.append("%s: net %.0f is under half the known-map mean %.0f"
                          % (name, data["net_mean"], baseline))
    if hard_failures:
        print("HARD FAILURES (exception escaped the simulator):")
        for line in hard_failures:
            print("   " + line)
    print("ALERTS:" if alerts else "ALERTS: none")
    for line in alerts:
        print("   ! " + line)

    if args.json:
        args.json.write_text(json.dumps(
            {"results": results, "hard_failures": hard_failures, "alerts": alerts,
             "known_map_net_mean": baseline, "seeds": args.seeds},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 1 if (hard_failures or alerts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
