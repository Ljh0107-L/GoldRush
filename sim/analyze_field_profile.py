#!/usr/bin/env python3
"""Field playstyle profile from opponent-side statistics in games we initiated.

Purpose
-------
Two nights of analysis covered only the two strongest teams, because 91% of our
external games were against them by our own choice. This profiles the REST of
the field so we can answer the question that decides the remaining week: is our
hit rate of about 35% bottom-of-field, ordinary, or near-elite?

Why the opponent side is usable while our side is not
----------------------------------------------------
These games were played by older builds of ours, so OUR numbers here are not the
frozen construct's. But an opponent cannot see our source — only the board — so
opponent behaviour is essentially independent of which build we fielded. Hence
opponent-side statistics are usable and our side is reported only as context,
clearly labelled.

Channel
-------
Per-unit ``gold`` is recorded in 100% of unit-observations even when the unit is
invisible, so round-over-round differences give an unbiased per-unit income
series. That channel reproduces head-to-head score differences to within 3.5
gold and is the same one used in ``sim/analyze_gold_delta.py``.

    mean gold per unit-round = P(unit scores) x mean gold given it scores

Trajectory-derived quantities (reversals, revisits, central residency) require
positions and actions, which ARE fog-filtered for opponents, so those are marked
fog-limited and reported qualitatively only.

Map stratification is mandatory: the same opponent's hit rate varies enormously
by map (Tiuntled-1 measured 45.5% / 50.0% / 26.4% on map1/2/3), so pooling
across maps and comparing to a different map mix would be meaningless.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
US_TEAM = "0x8F"
MAX_ORDINARY_PICKUP = (65 * 10 + 99) // 100          # 7

# Reference values already established on the frozen construct and the top two,
# per battlefield, from sim/reports/gold_delta_channel.json.
REFERENCE = {
    "OURS frozen (map1)": {"hit": 0.360, "yield": 4.68, "note": "mean of 36.2/35.8"},
    "OURS frozen (all 3 maps)": {"hit": 0.348, "yield": 4.668, "note": "pooled 36 games"},
    "Tiuntled-1 (map1)": {"hit": 0.455, "yield": 4.41, "note": ""},
    "Tundra-wawa (map1)": {"hit": 0.506, "yield": 3.83, "note": ""},
}


def rounds(path: Path):
    with path.open(encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        handle.readline()
        recs = []
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            recs.append(rec if ("start" in rec and "end" in rec) else None)
    return header, recs


def per_unit_deltas(recs, pid: int) -> list[int]:
    prev, out = None, []
    for rec in recs:
        if rec is None:
            prev = None
            continue
        entry = next((p for p in rec["end"]["players"] if int(p["id"]) == pid), None)
        if entry is None:
            continue
        cur = [int(u["gold"]) for u in entry["units"]]
        if prev is not None and len(prev) == len(cur):
            out.extend(now - was for now, was in zip(cur, prev))
        prev = cur
    return out


def describe(values: list[int]) -> dict | None:
    if not values:
        return None
    gains = [v for v in values if v > 0]
    counts = collections.Counter(values)
    return {
        "unit_rounds": len(values),
        "mean": statistics.fmean(values),
        "hit": len(gains) / len(values),
        "yield_per_hit": statistics.fmean(gains) if gains else 0.0,
        "ge8": sum(c for v, c in counts.items() if v > MAX_ORDINARY_PICKUP) / len(values),
    }


def vision_spent(recs, pid: int) -> int | None:
    for rec in reversed(recs):
        if rec is None:
            continue
        entry = next((p for p in rec["end"]["players"] if int(p["id"]) == pid), None)
        if entry is not None:
            return int(entry.get("vision_spent") or 0)
    return None


def quartiles(vals: list[float]) -> tuple[float, float, float]:
    s = sorted(vals)
    if len(s) == 1:
        return (s[0], s[0], s[0])
    return (s[len(s) // 4], statistics.median(s), s[(3 * len(s)) // 4])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default="/tmp/gr_fix/mid_usable.json",
                    help="JSON mapping opponent name -> [game_id]")
    ap.add_argument("--map", type=int, default=None, help="restrict to one map id")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    per_opp: dict[str, dict] = {}
    for team, ids in sorted(index.items()):
        if "测试用户" in team:                       # organiser test accounts
            continue
        deltas, games, maps, vis = [], 0, collections.Counter(), []
        for gid in ids:
            path = LOGS / ("game_%s.log" % gid)
            if not path.exists():
                continue
            header, recs = rounds(path)
            live = [r for r in recs if r is not None]
            if len(live) < 400:
                continue                              # forfeit / truncated
            # identify which player id is the opponent
            names = {1: header.get("player1"), 2: header.get("player2")}
            opp_pid = None
            for pid, nm in names.items():
                ent = next((p for p in live[-1]["end"]["players"] if int(p["id"]) == pid), None)
                if ent is None:
                    continue
                opp_pid = pid if str(nm) and not str(nm).startswith(("player220",)) else opp_pid
            # Which player id is the opponent depends on who initiated. In games we
            # initiated we are player1; in games they initiated (passive, i.e. a challenge
            # against our public slot) we are player2. Decide from the header names: our
            # side is whichever slot carries a build name we recognise as ours.
            p1, p2 = str(names.get(1) or ""), str(names.get(2) or "")
            opp_pid = 1 if p2.startswith("player220") or p2 == "0x8F" else 2
            fp = 0
            try:
                with path.open(encoding="utf-8") as fh:
                    fh.readline()
                    fp = sum(row.count("1") for row in json.loads(fh.readline()))
            except Exception:                         # noqa: BLE001
                fp = 0
            map_id = {40: 1, 24: 2, 78: 3}.get(fp, 0)
            if args.map and map_id != args.map:
                continue
            d = per_unit_deltas(live, opp_pid)
            if not d:
                continue
            deltas += d
            games += 1
            maps[map_id] += 1
            v = vision_spent(live, opp_pid)
            if v is not None:
                vis.append(v)
        stats = describe(deltas)
        if stats and games:
            per_opp[team] = {**stats, "games": games, "map_mix": dict(maps),
                             "vision_spent_total": sum(vis) if vis else None}

    if not per_opp:
        print("no usable games after filtering", file=sys.stderr)
        return 2

    label = "map%d only" % args.map if args.map else "all maps pooled (see map_mix)"
    print("=== FIELD PLAYSTYLE PROFILE — opponent side, %s ===" % label)
    print("%-20s %5s %8s %8s %9s %8s %7s  %s"
          % ("team", "games", "u-rounds", "hit%", "yld/hit", ">=8%", "vision", "map mix"))
    for team, s in sorted(per_opp.items(), key=lambda kv: -kv[1]["hit"]):
        print("%-20s %5d %8d %8.1f %9.2f %8.2f %7s  %s"
              % (team, s["games"], s["unit_rounds"], 100 * s["hit"], s["yield_per_hit"],
                 100 * s["ge8"], s["vision_spent_total"], s["map_mix"]))

    print()
    print("=== FIELD DISTRIBUTION across %d opponents (q1 / median / q3) ===" % len(per_opp))
    for key, fmt, scale in (("hit", "%.1f%%", 100), ("yield_per_hit", "%.2f", 1),
                            ("ge8", "%.2f%%", 100)):
        q1, med, q3 = quartiles([s[key] * scale for s in per_opp.values()])
        print("  %-14s " % key + " / ".join(fmt % v for v in (q1, med, q3)))

    print()
    print("=== REFERENCE (established separately, frozen construct + top two) ===")
    for name, ref in REFERENCE.items():
        print("  %-26s hit %.1f%%   yld/hit %.2f   %s"
              % (name, 100 * ref["hit"], ref["yield"], ref["note"]))

    hits = sorted(s["hit"] for s in per_opp.values())
    ours = REFERENCE["OURS frozen (map1)"]["hit"] if args.map == 1 else \
        REFERENCE["OURS frozen (all 3 maps)"]["hit"]
    below = sum(1 for h in hits if h < ours)
    print()
    print("=== ANSWER TO THE HEADLINE QUESTION ===")
    print("  our frozen construct hit rate = %.1f%%" % (100 * ours))
    print("  opponents with a LOWER hit rate: %d / %d" % (below, len(hits)))
    print("  => our hit rate sits at roughly the %.0fth percentile of this sample"
          % (100 * below / len(hits)))
    print()
    print("BIASES (all of them apply; do not quote a number without them)")
    print("  1. challenger/opponent set is NOT uniform: these are teams WE chose to")
    print("     challenge, so the sample is self-selected and skews to teams we studied.")
    print("  2. our own side in these games is an OLD build, so only opponent-side")
    print("     numbers are used; opponents cannot see our code, only the board.")
    print("  3. per-opponent game counts are small (1-14); unit-rounds are plentiful but")
    print("     correlated within a game, so treat games as the replication unit.")
    print("  4. trajectory quantities (reversal, revisit, central residency) are NOT")
    print("     computed here: opponent positions/actions are fog-filtered (~1/3 visible).")
    print("     Any such comparison would be fog-limited and is marked undecidable.")
    if args.json:
        args.json.write_text(json.dumps(
            {"per_opponent": per_opp, "reference": REFERENCE, "map_filter": args.map},
            indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
