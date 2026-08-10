#!/usr/bin/env python3
"""Unfamiliar-map robustness audit for the delivered construct.

The construct carries baked wall tables for map1/map2/map3 and a fingerprint
elimination round.  The organisers declined to guarantee the preliminary reuses
the current terrain, so three failure modes are untested and each would cost
ranking directly:

1. **Hard failure** -- an exception or an illegal action list.  ``moveDecision``
   is wrapped in try/catch returning an all-stay ``SAFE_OUT``, so a latent bug
   surfaces as silent total passivity rather than a crash.  This audit therefore
   counts all-stay rounds explicitly instead of trusting "it did not throw".
2. **Mis-fingerprinting** -- elimination only consults cells actually observed.
   If an unseen map agrees with a baked table everywhere the units have looked,
   the construct locks that table and overwrites its wall bitmap with terrain
   that is not real.  Detected here by reading ``map_id`` out of an instrumented
   copy, and cross-checked against the blocked-move rate.
3. **Latency blow-up** -- on an unlocked or unknown map the mode never returns
   to FAST, so the ``cold``-attributed ``slowTick`` is called every round for
   500 rounds.  Cost is reported as blocked-move rate and all-stay rate here;
   the cycle measurement is done separately with ``tests/icount.cpp``.

Usage:
    python3 sim/audit_unknown_maps.py --so /tmp/base.so [--probe-so /tmp/probe.so]
                                      [--seeds 0 1 2] [--json out.json]

``--probe-so`` is an instrumented build of the same source exporting
``probe_map_id``/``probe_mode``; when supplied, the settled fingerprint verdict
becomes direct evidence rather than inference.
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KNOWN = ("map1", "map2", "map3")
UNKNOWN_REGISTRY = ROOT / "sim" / "maps_unknown.json"
MAP_ID_MEANING = {-1: "undetermined", -2: "unknown(lazy)", 0: "locked map1",
                  1: "locked map2", 2: "locked map3"}
MODE_MEANING = {0: "FAST", 1: "OPENING", 2: "LAZY"}


def load_maps(names: list[str] | None) -> list[tuple[str, object]]:
    from sim.scenario import MapDefinition
    out: list[tuple[str, object]] = []
    for name in KNOWN:
        out.append((name, MapDefinition.by_name(name)))
    payload = json.loads(UNKNOWN_REGISTRY.read_text(encoding="utf-8"))
    for name in sorted(payload["maps"]):
        out.append((name, MapDefinition.from_log_line2(
            payload["maps"][name]["rows"], name=name)))
    if names:
        keep = set(names)
        out = [item for item in out if item[0] in keep]
    return out


def probe_state(strategy) -> tuple[int, int] | None:
    """Read map_id/mode out of the image that actually played the game.

    This MUST use the strategy's own loaded library.  ``SharedObjectStrategy``
    copies the .so to a private temporary path and loads that copy, so opening
    the original file again yields a fresh, never-executed image whose ``g_s``
    is still zero-initialised -- which decodes as "locked map1 / FAST" and would
    fake a mis-fingerprinting alarm on every map.  (The real post-reset initial
    state is map_id=-1 / mode=1, set on new-game detection.)
    """
    library = getattr(strategy, "_library", None)
    if library is None:
        return None
    try:
        library.probe_map_id.restype = ctypes.c_int
        library.probe_mode.restype = ctypes.c_int
        return int(library.probe_map_id()), int(library.probe_mode())
    except AttributeError:
        return None


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
    parser.add_argument("--probe-so", type=Path, default=None,
                        help="instrumented build exporting probe_map_id/probe_mode")
    parser.add_argument("--seeds", nargs="+", default=["0", "1"])
    parser.add_argument("--maps", nargs="+", default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    from sim.abi import SharedObjectStrategy

    results: dict[str, dict] = {}
    hard_failures: list[str] = []
    print("%-12s %6s %8s %9s %10s %11s  %s" % (
        "map", "walls", "net", "rounds", "all-stay", "blocked%", "fingerprint"))
    for name, map_def in load_maps(args.maps):
        walls = len(map_def.walls)
        per_seed = []
        verdicts = set()
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
            if args.probe_so is not None:
                with SharedObjectStrategy(args.probe_so, name="probe") as probe:
                    # Pass a BARE callable: runner._open_strategy re-loads a fresh
                    # image when handed a SharedObjectStrategy, which would leave
                    # our handle unexecuted and its g_s zero-initialised. A plain
                    # callable is wrapped and invoked directly, so `probe` is the
                    # image that actually plays and its state is observable.
                    def play(value, _p=probe):
                        return _p(value)
                    play.name = "probe"
                    try:
                        run_one(play, map_def, seed)
                    except Exception:                 # noqa: BLE001
                        pass
                    state = probe_state(probe)        # read the live image, not the file
                    if state is not None:
                        verdicts.add("%s / %s" % (MAP_ID_MEANING.get(state[0], state[0]),
                                                  MODE_MEANING.get(state[1], state[1])))
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
                         "per_seed": per_seed,
                         "fingerprint_verdict": sorted(verdicts) or None}
        print("%-12s %6d %8.0f %9d %10.1f %11.1f  %s" % (
            name, walls, net, rounds, stay, blk,
            ",".join(sorted(verdicts)) if verdicts else "-"))

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
        verdict = data["fingerprint_verdict"]
        if verdict and any(v.startswith("locked") for v in verdict):
            alerts.append("%s: MIS-FINGERPRINTED as %s -- wall bitmap overwritten "
                          "with terrain that is not real" % (name, verdict))
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
