#!/usr/bin/env python3
"""Unfamiliar-map lock adjudicator: does the fingerprint re-check ever go silent?

The construct at ``fd47ea6`` fingerprints the terrain in the opening rounds and,
once it decides which of map1/map2/map3 it is on, replaces its wall bitmap with
that map's baked table.  A mis-lock is silent -- no crash, no forfeit -- so the
8.10 repair added a re-check that drops the lock when an observation contradicts
the table.  The re-check is gated on ``round <= VERIFY_ROUNDS`` with
``VERIFY_ROUNDS == 24``, chosen because on the current three maps the
contradiction always fires in round 3-4.

That bound is a property of those three maps, not a law.  This module measures
the round at which a contradiction first becomes available, as a distribution
over constructible map classes, and looks for the tail beyond 24.

Subcommands
-----------
``coverage``    census of *when* each of the 289 cells first enters the
                comparison set, derived from real trajectories.  A cell first
                compared after round 24 is a cell where a one-bit terrain
                difference would mis-lock undetected under the shipped bound.
``build``       emit a perturbation registry: single-cell edits of map1/2/3 at
                cells chosen from the census tail, plus null controls.
``adjudicate``  play each map with the instrumented builds and report, per map:
                illegal output, lock taken, lock target, lock round, whether the
                lock was revised, and -- when the lock is wrong -- whether the
                error was detected at all, under the shipped bound and under an
                unbounded window.
``premium``     count verification scans on the known maps as a function of the
                window bound.  The shipped premium is proportional to this count
                and is anchored on two contest-machine measurements.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing as mp
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

N = 17
RAD = 2                       # the construct's own comparison window radius
SPAWNS = ((0, 0), (16, 16))
# The engine seats the opponent at the other two corners and refuses a scenario
# where any unit spawns on a wall.  sim/make_unknown_maps.py's protect() lists
# only our own two spawns plus the NPC cell, so a generator that walls (0,16) or
# (16,0) produces terrain the engine rejects at setup; guard all four here.
OPP_SPAWNS = ((0, 16), (16, 0))
NPC_SPAWN = (8, 8)
KNOWN = ("map1", "map2", "map3")
LATE_REGISTRY = ROOT / "sim" / "maps_unknown_late.json"
PROBE_DIR = Path("/tmp/umr")

MAP_ID_MEANING = {-1: "undetermined", -2: "unknown(lazy)", 0: "locked map1",
                  1: "locked map2", 2: "locked map3"}

_INT_PROBES = (
    "probe_map_id", "probe_mode", "probe_lock_round", "probe_lock_to",
    "probe_unknown_round", "probe_mech_conflict_round",
    "probe_mech_conflict_count", "probe_visible_conflict_round",
    "probe_relock_round", "probe_scan_calls", "probe_verify_scans",
    "probe_cells_compared", "probe_cells_learned", "probe_rounds_seen",
    "probe_slowtick_calls", "probe_verify_rounds", "probe_force_lock",
)


# --------------------------------------------------------------------------
# baked tables, decoded from the same source the construct compiles from
# --------------------------------------------------------------------------
def baked_tables(source: Path | None = None) -> dict[str, list[str]]:
    """Decode ``BAKED_W`` out of the extracted construct source.

    Reading the table from the source under test (rather than from
    ``sim/maps.json``) means a divergence between the two cannot silently
    invalidate the audit.
    """
    text = (source or (PROBE_DIR / "player_base.cpp")).read_text(encoding="utf-8")
    start = text.index("constexpr uint32_t BAKED_W[3][N] = {")
    end = text.index("};", start)
    body = text[start:end]
    words = [int(tok, 16) for tok in
             __import__("re").findall(r"0x([0-9a-fA-F]{8})u", body)]
    if len(words) != 3 * N:
        raise SystemExit("decoded %d BAKED_W words, expected %d" % (len(words), 3 * N))
    out = {}
    for m in range(3):
        rows = []
        for r in range(N):
            word = words[m * N + r]
            rows.append("".join("1" if (word >> (c + 1)) & 1 else "0" for c in range(N)))
        out["map%d" % (m + 1)] = rows
    return out


def wall_rows_of(map_def) -> list[str]:
    return ["".join("1" if (r, c) in map_def.walls else "0" for c in range(N))
            for r in range(N)]


# --------------------------------------------------------------------------
# one game, with the probe image kept live
# --------------------------------------------------------------------------
def read_probe(library) -> dict:
    out = {}
    for name in _INT_PROBES:
        fn = getattr(library, name)
        fn.restype = ctypes.c_int
        out[name.replace("probe_", "")] = int(fn())
    for name, ctype in (("probe_map_id_by_round", ctypes.c_int8),
                        ("probe_mode_by_round", ctypes.c_uint8),
                        ("probe_scans_by_round", ctypes.c_uint8),
                        ("probe_verify_by_round", ctypes.c_uint8)):
        fn = getattr(library, name)
        fn.restype = ctypes.POINTER(ctype)
        buf = fn()
        out[name.replace("probe_", "")] = [int(buf[i]) for i in range(500)]
    return out


def play(so: Path, map_def, seed: str, *, want_probe: bool = True) -> dict:
    """Play 500 rounds against the passive seat and return log facts + probe state."""
    from sim.abi import SharedObjectStrategy
    from sim.runner import run_game

    record: dict = {"seed": seed, "forfeit": None}
    with SharedObjectStrategy(so, name="cand") as strategy:
        def call(value, _s=strategy):
            return _s(value)
        call.name = "cand"
        try:
            result = run_game(call, "stay", map_source=map_def, seed=seed,
                              dispatch="fixed", fixed_costs=(200, 100000),
                              player1_name="cand", player2_name="passive")
        except Exception as exc:                      # noqa: BLE001
            record["forfeit"] = "%s: %s" % (type(exc).__name__, exc)
            return record
        if want_probe:
            try:
                record["probe"] = read_probe(strategy._library)
            except AttributeError:
                record["probe"] = None
        lines = result.log_bytes.decode("utf-8").splitlines()
        traj: list[tuple[tuple[int, int], tuple[int, int]]] = []
        rounds = 0
        stay_rounds = 0
        blocked = 0
        steps = 0
        vp_spend = 0
        for line in lines[2:]:
            if not line.strip():
                continue
            rec = json.loads(line)
            me_start = next(p for p in rec["start"]["players"] if int(p["id"]) == 1)
            traj.append(tuple(tuple(int(x) for x in u["position"])
                              for u in me_start["units"]))
            if "end" not in rec:
                continue
            rounds += 1
            me = next(p for p in rec["end"]["players"] if int(p["id"]) == 1)
            acts = [int(a) for unit in me["units"] for a in (unit.get("actions") or ())]
            if acts and all(a == 4 for a in acts):
                stay_rounds += 1
            steps += len(acts)
            blocked += sum(1 for a in acts if a == 4)
            vp_spend = int(me.get("vision_spent") or 0)
        record.update({
            "rounds": rounds, "all_stay_rounds": stay_rounds,
            "stay_or_blocked_steps": blocked, "steps": steps,
            "vision_spent": vp_spend, "trajectory": traj,
            "net_gold": int(result.summary["players"]["1"]["net_gold"]),
            "log_sha256": result.summary["log_sha256"],
        })
    return record


# --------------------------------------------------------------------------
# coverage census
# --------------------------------------------------------------------------
def comparison_rounds(traj, *, shape: str = "cheb2") -> dict[tuple[int, int], int]:
    """First round each cell enters the construct's comparison set.

    ``shape='cheb2'`` mirrors the shipped gate exactly with the round bound
    removed: a unit standing on a cell it has never stood on triggers one clipped
    5x5 window comparison, and ``visited`` is shared between the two units.
    Every cell inside that window is compared in that round.  Fog cannot intrude:
    the engine's base vision radius is 2, so the scanned window is fully visible.

    ``shape='manh1'`` is the same event stream with the window narrowed to the
    cell itself plus its four orthogonal neighbours -- the only cells the router
    ever reads a wall bit for -- which is the cheaper detector priced in the
    report.  ``shape='cell'`` narrows it further to the stood-on cell alone.
    """
    offsets: list[tuple[int, int]]
    if shape == "cheb2":
        offsets = [(dr, dc) for dr in range(-2, 3) for dc in range(-2, 3)]
    elif shape == "manh1":
        offsets = [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]
    elif shape == "cell":
        offsets = [(0, 0)]
    else:
        raise ValueError("unknown shape %r" % shape)
    visited: set[tuple[int, int]] = set()
    first: dict[tuple[int, int], int] = {}
    for rnd, positions in enumerate(traj):
        for (sr, sc) in positions:
            if (sr, sc) in visited:
                continue
            visited.add((sr, sc))
            for dr, dc in offsets:
                r, c = sr + dr, sc + dc
                if 0 <= r < N and 0 <= c < N:
                    first.setdefault((r, c), rnd)
    return first


def cmd_coverage(args) -> int:
    from sim.scenario import MapDefinition
    jobs = [(name, seed) for name in KNOWN for seed in args.seeds]
    with mp.Pool(args.workers) as pool:
        played = pool.starmap(_coverage_job, [(str(args.so), n, s) for n, s in jobs])
    per_map: dict[str, list[dict]] = {}
    for (name, seed), rec in zip(jobs, played):
        per_map.setdefault(name, []).append(rec)

    census = {}
    print("%-6s %-5s %8s %8s %8s %8s %8s %8s" % (
        "map", "seed", "by r4", "by r8", "by r24", "by r60", "by r500", "never"))
    for name in KNOWN:
        rows = []
        for rec in per_map[name]:
            first = rec["first"]
            counts = {}
            for bound in (4, 8, 24, 60, 120, 250, 499):
                counts[bound] = sum(1 for v in first.values() if v <= bound)
            never = N * N - len(first)
            rows.append({"seed": rec["seed"], "counts": counts, "never": never,
                         "max_round": max(first.values()) if first else -1,
                         "first": {"%d,%d" % k: v for k, v in sorted(first.items())},
                         "first_manh1": {"%d,%d" % k: v for k, v
                                         in sorted(rec["first_manh1"].items())},
                         "distinct_cells_stood_on": len({p for step in
                                                         rec["trajectory"]
                                                         for p in step})})
            print("%-6s %-5s %8d %8d %8d %8d %8d %8d" % (
                name, rec["seed"], counts[4], counts[8], counts[24], counts[60],
                counts[499], never))
        census[name] = rows
    if args.json:
        args.json.write_text(json.dumps(census, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 0


def _coverage_job(so: str, name: str, seed: str) -> dict:
    from sim.scenario import MapDefinition
    rec = play(Path(so), MapDefinition.by_name(name), seed, want_probe=False)
    return {"seed": seed, "first": comparison_rounds(rec["trajectory"]),
            "first_manh1": comparison_rounds(rec["trajectory"], shape="manh1"),
            "trajectory": rec["trajectory"]}


# --------------------------------------------------------------------------
# registry construction
# --------------------------------------------------------------------------
def connected(rows: list[str]) -> bool:
    from sim.make_unknown_maps import connected as _c
    return _c(rows)


def flip(rows: list[str], cell: tuple[int, int]) -> list[str]:
    r, c = cell
    grid = [list(row) for row in rows]
    grid[r][c] = "0" if grid[r][c] == "1" else "1"
    return ["".join(row) for row in grid]


def legal_perturbation(rows: list[str]) -> bool:
    for r, c in (*SPAWNS, *OPP_SPAWNS, NPC_SPAWN):
        if rows[r][c] == "1":
            return False
    return connected(rows)


def cmd_build(args) -> int:
    from sim.make_unknown_maps import wall_count
    from sim.scenario import MapDefinition

    known = {n: wall_rows_of(MapDefinition.by_name(n)) for n in KNOWN}
    baked = baked_tables()
    for name in KNOWN:
        if baked[name] != known[name]:
            # map3 in sim/maps.json is derived from BAKED_W, map1/2 from logs.
            raise SystemExit("%s walls disagree between maps.json and BAKED_W" % name)

    maps: dict[str, dict] = {}

    def add(name: str, rows: list[str], note: str, parent: str, cell=None) -> None:
        if not legal_perturbation(rows):
            raise SystemExit("illegal generated map %s" % name)
        maps[name] = {
            "limited": False, "rows": rows,
            "counts": {"wall": wall_count(rows)},
            "source": {"kind": "synthetic", "generator": "sim/audit_unknown_lock.py",
                       "note": note, "parent": parent,
                       "cell": list(cell) if cell else None},
        }

    if args.mode == "exhaustive":
        # Every legal single-cell edit of every parent.  A single cell is the
        # minimum possible perturbation, hence the maximally stealthy adversary:
        # if the re-check survives this population it survives any larger edit
        # that also touches one of these cells.
        for parent in args.parents:
            rows = known[parent]
            for r in range(N):
                for c in range(N):
                    cand = flip(rows, (r, c))
                    if not legal_perturbation(cand):
                        continue
                    kind = "wall_added" if rows[r][c] == "0" else "wall_removed"
                    add("%s_x_%02d-%02d_%s" % (parent, r, c, kind), cand,
                        "single-cell %s at (%d,%d)" % (kind, r, c), parent, (r, c))
    elif args.mode == "randk":
        # k cells flipped uniformly at random.  This is the population that
        # answers "how big does an edit have to be before the shipped window
        # reliably notices"; the escape probability should fall off sharply in k
        # because detection fires on the EARLIEST-observed mismatching cell.
        import random
        rng = random.Random(args.rng_seed)
        cells = [(r, c) for r in range(N) for c in range(N)]
        for parent in args.parents:
            rows = known[parent]
            for k in args.k:
                made = 0
                attempts = 0
                while made < args.samples and attempts < 400:
                    attempts += 1
                    picked = rng.sample(cells, k)
                    cand = rows
                    for cell in picked:
                        cand = flip(cand, cell)
                    if not legal_perturbation(cand):
                        continue
                    add("%s_k%02d_s%02d" % (parent, k, made), cand,
                        "%d random cell flips: %s" % (
                            k, " ".join("%d-%d" % c for c in sorted(picked))),
                        parent, None)
                    made += 1
    else:
        census = json.loads(args.census.read_text(encoding="utf-8"))
        # Class A: single-cell perturbations, one per census band, both directions.
        bands = ((0, 4, "A_r0004"), (5, 24, "A_r0524"), (25, 60, "A_r2560"),
                 (61, 200, "A_r61200"), (201, 499, "A_r201499"),
                 (500, 10 ** 9, "A_never"))
        for parent in KNOWN:
            first = {tuple(int(x) for x in k.split(",")): v
                     for k, v in census[parent][0]["first"].items()}
            rows = known[parent]
            for lo, hi, tag in bands:
                picks = []
                for r in range(N):
                    for c in range(N):
                        when = first.get((r, c), 10 ** 9)
                        if not (lo <= when <= hi):
                            continue
                        if not legal_perturbation(flip(rows, (r, c))):
                            continue
                        picks.append((when, rows[r][c], (r, c)))
                for direction in ("0", "1"):
                    same = [p for p in picks if p[1] == direction]
                    if not same:
                        continue
                    same.sort(key=lambda p: (p[0], p[2]))
                    when, _orig, cell = same[len(same) // 2]
                    kind = "wall_added" if direction == "0" else "wall_removed"
                    add("%s_%s_%s_%s" % (parent, tag, kind, "%d-%d" % cell),
                        flip(rows, cell),
                        "single-cell %s at (%d,%d); census first-comparison round %s"
                        % (kind, cell[0], cell[1],
                           "never" if when >= 10 ** 9 else str(when)),
                        parent, cell)

        # Class B: null controls -- perturbations inside the round-0/1 fingerprint
        # region, which must break the lock instead of mis-locking.
        for parent in KNOWN:
            rows = known[parent]
            for cell in ((2, 1), (2, 2), (14, 15), (1, 1)):
                cand = flip(rows, cell)
                if legal_perturbation(cand):
                    add("%s_B_corner_%d-%d" % (parent, *cell), cand,
                        "corner-region flip: must fail the fingerprint, not mis-lock",
                        parent, cell)

        # Class C: the published one-cell control, rebuilt.  mimic1 with (2,1)
        # opened is the map the 8.10 report used to separate the mis-lock cost
        # from terrain difficulty; it must NOT mis-lock.
        payload = json.loads((ROOT / "sim" / "maps_unknown.json").read_text(
            encoding="utf-8"))
        for src, cell in (("mimic1", (2, 1)), ("mimic2", (2, 2)), ("mimic3", (2, 2))):
            rows = list(payload["maps"][src]["rows"])
            cand = flip(rows, cell)
            if legal_perturbation(cand):
                add("%s_broken" % src, cand,
                    "published control: %s with (%d,%d) flipped so the "
                    "fingerprint rejects the parent table" % (src, *cell),
                    src, cell)

    payload = {
        "schema_version": 1, "grid_size": N,
        "cell_codes": {"0": "open", "1": "wall", "2": "bomb_candidate"},
        "purpose": ("late-contradiction probes for the fingerprint re-check window; "
                    "NOT official terrain"),
        "maps": maps,
    }
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print("wrote %s with %d maps" % (args.out, len(maps)))
    for name in sorted(maps):
        print("  %-40s %s" % (name, maps[name]["source"]["note"]))
    return 0


# --------------------------------------------------------------------------
# adjudication
# --------------------------------------------------------------------------
def load_registry(paths: list[Path]):
    from sim.scenario import MapDefinition
    out = []
    for name in KNOWN:
        out.append((name, MapDefinition.by_name(name), {"parent": name,
                                                        "note": "known map"}))
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for name in sorted(payload["maps"]):
            entry = payload["maps"][name]
            out.append((name, MapDefinition.from_log_line2(entry["rows"], name=name),
                        entry.get("source", {})))
    return out


def truth_vs_table(map_def, table_index: int, baked) -> dict:
    """How wrong a lock is, and where, independent of any detector."""
    rows = wall_rows_of(map_def)
    tab = baked["map%d" % (table_index + 1)]
    phantom = [(r, c) for r in range(N) for c in range(N)
               if tab[r][c] == "1" and rows[r][c] != "1"]
    missing = [(r, c) for r in range(N) for c in range(N)
               if tab[r][c] != "1" and rows[r][c] == "1"]
    return {"phantom_walls": phantom, "missing_walls": missing,
            "mismatch_cells": len(phantom) + len(missing)}


def _adjudicate_job(spec) -> dict:
    so, name, rows, seed, tag = spec
    from sim.scenario import MapDefinition
    map_def = MapDefinition.from_log_line2(rows, name=name)
    rec = play(Path(so), map_def, seed)
    rec.pop("trajectory", None)
    rec["map"] = name
    rec["build"] = tag
    return rec


def cmd_adjudicate(args) -> int:
    baked = baked_tables()
    entries = load_registry(args.registry)
    if args.maps:
        keep = set(args.maps)
        entries = [e for e in entries if e[0] in keep]
    builds = {"shipped24": args.probe, "unbounded": args.probe_inf}
    if args.builds:
        builds = {k: v for k, v in builds.items() if k in set(args.builds)}
    if args.force_build is not None:
        builds = {"force%d" % args.force_map: args.force_build}

    specs = []
    for name, map_def, meta in entries:
        rows = list(map_def.rows)
        for tag, so in builds.items():
            for seed in args.seeds:
                specs.append((str(so), name, rows, seed, tag))
    with mp.Pool(args.workers) as pool:
        played = pool.map(_adjudicate_job, specs)

    grouped: dict[tuple[str, str], list[dict]] = {}
    for rec in played:
        grouped.setdefault((rec["map"], rec["build"]), []).append(rec)

    results = {}
    if not args.quiet:
        print("%-38s %-10s %-5s %-14s %-6s %-7s %-7s %-6s %s" % (
            "map", "build", "seed", "lock", "lockR", "mechR", "visR", "revis",
            "verdict"))
    for name, map_def, meta in entries:
        parent = meta.get("parent")
        entry = {"note": meta.get("note"), "parent": parent,
                 "cell": meta.get("cell"), "walls": len(map_def.walls),
                 "runs": []}
        for tag in builds:
            for rec in sorted(grouped.get((name, tag), []), key=lambda r: r["seed"]):
                probe = rec.get("probe") or {}
                lock_to = probe.get("lock_to", -1)
                lock_round = probe.get("lock_round", -1)
                mech = probe.get("mech_conflict_round", -1)
                vis = probe.get("visible_conflict_round", -1)
                final = probe.get("map_id", None)
                wrongness = (truth_vs_table(map_def, lock_to, baked)
                             if lock_to >= 0 else None)
                mislock = bool(wrongness and wrongness["mismatch_cells"] > 0)
                if not mislock:
                    verdict = "no mis-lock"
                elif mech >= 0:
                    verdict = "mis-lock DETECTED r%d" % mech
                else:
                    verdict = "MIS-LOCK UNDETECTED"
                row = {
                    "build": tag, "seed": rec["seed"], "forfeit": rec.get("forfeit"),
                    "rounds": rec.get("rounds"), "net_gold": rec.get("net_gold"),
                    "all_stay_rounds": rec.get("all_stay_rounds"),
                    "stay_or_blocked_steps": rec.get("stay_or_blocked_steps"),
                    "steps": rec.get("steps"),
                    "lock_to": lock_to, "lock_round": lock_round,
                    "final_map_id": final,
                    "mech_conflict_round": mech, "visible_conflict_round": vis,
                    "relock_round": probe.get("relock_round", -1),
                    "unknown_round": probe.get("unknown_round", -1),
                    "verify_scans": probe.get("verify_scans"),
                    "cells_compared": probe.get("cells_compared"),
                    "scan_calls": probe.get("scan_calls"),
                    "slowtick_calls": probe.get("slowtick_calls"),
                    "verify_rounds_const": probe.get("verify_rounds"),
                    "table_mismatch_cells": (wrongness or {}).get("mismatch_cells", 0),
                    "phantom_walls": len((wrongness or {}).get("phantom_walls", [])),
                    "missing_walls": len((wrongness or {}).get("missing_walls", [])),
                    "mislock": mislock, "verdict": verdict,
                    "log_sha256": rec.get("log_sha256"),
                }
                entry["runs"].append(row)
                if not args.quiet:
                    print("%-38s %-10s %-5s %-14s %-6s %-7s %-7s %-6s %s" % (
                        name, tag, rec["seed"],
                        MAP_ID_MEANING.get(lock_to, str(lock_to)) if lock_to >= 0
                        else "no lock",
                        lock_round, mech, vis, row["relock_round"], verdict))
        results[name] = entry

    if args.json:
        args.json.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 0


# --------------------------------------------------------------------------
# premium: verification scans as a function of the window bound
# --------------------------------------------------------------------------
def _premium_job(spec) -> dict:
    so, name, seed = spec
    from sim.scenario import MapDefinition
    rec = play(Path(so), MapDefinition.by_name(name), seed)
    probe = rec["probe"]
    scans = probe["scans_by_round"]
    verify = probe["verify_by_round"]
    return {"map": name, "seed": seed, "scans_by_round": scans,
            "verify_by_round": verify,
            "verify_scans": probe["verify_scans"],
            "cells_compared": probe["cells_compared"]}


def cmd_premium(args) -> int:
    specs = [(str(args.probe_inf), name, seed)
             for name in KNOWN for seed in args.seeds]
    with mp.Pool(args.workers) as pool:
        played = pool.map(_premium_job, specs)
    bounds = (4, 8, 12, 24, 40, 60, 90, 120, 250, 499)
    table = {}
    print("%-6s %-5s %s" % ("map", "seed",
                            "  ".join("<=%d" % b for b in bounds)))
    for rec in played:
        cum = []
        for b in bounds:
            cum.append(sum(rec["verify_by_round"][:b + 1]))
        table.setdefault(rec["map"], []).append(
            {"seed": rec["seed"], "cum_verify_scans": dict(zip(map(str, bounds), cum)),
             "total_verify_scans": rec["verify_scans"]})
        print("%-6s %-5s %s" % (rec["map"], rec["seed"],
                                "  ".join("%5d" % v for v in cum)))
    if args.json:
        args.json.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 0


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--seeds", nargs="+", default=["0", "1", "2"])
    parser.add_argument("--json", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("coverage")
    p.add_argument("--so", type=Path, default=PROBE_DIR / "base.dylib")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("build")
    p.add_argument("--census", type=Path, default=None)
    p.add_argument("--mode", choices=("bands", "exhaustive", "randk"),
                   default="bands")
    p.add_argument("--parents", nargs="+", default=list(KNOWN))
    p.add_argument("--k", nargs="+", type=int, default=[1, 2, 3, 5, 8, 16])
    p.add_argument("--samples", type=int, default=8)
    p.add_argument("--rng-seed", type=int, default=20260810)
    p.add_argument("--out", type=Path, default=LATE_REGISTRY)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("adjudicate")
    p.add_argument("--probe", type=Path, default=PROBE_DIR / "probe.dylib")
    p.add_argument("--probe-inf", type=Path, default=PROBE_DIR / "probe_inf.dylib")
    p.add_argument("--force-build", type=Path, default=None)
    p.add_argument("--force-map", type=int, default=0)
    p.add_argument("--registry", nargs="*", type=Path,
                   default=[ROOT / "sim" / "maps_unknown.json"])
    p.add_argument("--maps", nargs="*", default=None)
    p.add_argument("--builds", nargs="*", default=None,
                   help="subset of {shipped24,unbounded}")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_adjudicate)

    p = sub.add_parser("premium")
    p.add_argument("--probe-inf", type=Path, default=PROBE_DIR / "probe_inf.dylib")
    p.set_defaults(func=cmd_premium)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
