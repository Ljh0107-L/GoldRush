#!/usr/bin/env python3
"""Ladder ledger — free, zero-quota sampling of the live construct's field win rate.

Why this exists
---------------
Two platform facts combine into a free measurement channel:

* ``docs/FAQ.md:366`` — games where OTHER teams challenge us do **not** consume
  our 500/day quota.
* ``docs/赛制介绍.md:27`` — a challenge is played by our **public model**, i.e.
  the single ``stage=4`` slot (``add_model_4`` is an upsert: 105 public models
  across 105 distinct ``user_id`` values, no team has two).

So once the public slot is re-published, every incoming challenge is a free
sample of whatever construct is currently sitting in that slot.

The trap this tool exists to avoid
----------------------------------
The platform's cumulative ``win_rate`` is a **mixture**. Of 817 external games,
only 36 were played by the frozen lineage — and those 36 were exclusively
against the two strongest teams. Every one of the 27 games against mid-table
Ausdroid was played by one of 25 different older builds. Quoting the cumulative
rate as the current construct's ability is the same error that produced a
falsified strategy hypothesis earlier in this project.

Therefore this tool always reports the post-publish window **separately** from
the cumulative figure, and prefers a per-opponent paired comparison, which
controls for who chose to challenge us rather than assuming the challenger mix
is stable.

Usage
-----
    ladder_ledger.py snapshot [--note TEXT]   # append current state to the ledger
    ladder_ledger.py report                   # before/after split + paired view + power
    ladder_ledger.py power [--baseline P]     # sample sizes needed to detect a change
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
LEDGER = ROOT / "sim" / "reports" / "ladder_ledger.json"

US_USER_ID = 220
US_TEAM = "0x8F"
# Builds belonging to the frozen deliverable lineage. Anything else is an older
# or experimental build and must never be pooled with these.
FROZEN_PREFIXES = ("frTu", "t1f", "mg", "gp", "chfix")


def api_rows() -> list[dict]:
    import arena
    rows: list[dict] = []
    page = 1
    while True:
        payload = arena.call("GET", "/api/user/get_game_list_1",
                             params={"page": page, "page_size": 200})
        batch = payload.get("list", [])
        if not batch:
            break
        rows += batch
        if len(rows) >= int(payload.get("total", 0)):
            break
        page += 1
    return rows


def api_rank() -> dict:
    import arena
    for row in arena.call("GET", "/api/user/get_rank_list_1").get("list", []):
        if int(row.get("user_id", -1)) == US_USER_ID:
            return row
    return {}


def api_public_model() -> dict:
    import arena
    for row in arena.call("GET", "/api/user/get_model_list_4").get("list", []):
        if int(row.get("user_id", -1)) == US_USER_ID:
            return row
    return {}


def our_side(row: dict) -> dict | None:
    mine = [p for p in row.get("players", []) if p.get("user_name_cn") == US_TEAM]
    return mine[0] if len(mine) == 1 else None


def their_side(row: dict) -> dict | None:
    theirs = [p for p in row.get("players", []) if p.get("user_name_cn") != US_TEAM]
    return theirs[0] if theirs else None


def classify(row: dict) -> str:
    """active = we initiated against another team; passive = they challenged us."""
    a, b = row.get("user_id"), row.get("user_id2")
    if a == US_USER_ID and b == US_USER_ID:
        return "self"
    if a == US_USER_ID:
        return "active"
    if b == US_USER_ID:
        return "passive"
    return "other"


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — correct at small n, unlike the normal approximation."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_n(baseline: float, target: float, alpha: float = 0.05,
               power: float = 0.80) -> int:
    """One-sided sample size to distinguish `target` from `baseline`.

    Uses the standard two-proportion normal approximation with the null variance
    under `baseline` and the alternative variance under `target`; adequate here
    because we only need an order-of-magnitude gate, and the Wilson interval is
    what actually adjudicates the observed data.
    """
    if target <= baseline:
        return 0
    z_a = 1.6449 if abs(alpha - 0.05) < 1e-9 else 1.2816
    z_b = 0.8416 if abs(power - 0.80) < 1e-9 else 1.2816
    num = z_a * math.sqrt(baseline * (1 - baseline)) + z_b * math.sqrt(target * (1 - target))
    return max(1, math.ceil((num / (target - baseline)) ** 2))


def collect() -> dict:
    rows = api_rows()
    rank = api_rank()
    model = api_public_model()
    buckets = collections.Counter(classify(r) for r in rows)
    ext = [r for r in rows if classify(r) in ("active", "passive") and our_side(r)]
    frozen = [r for r in ext
              if str(our_side(r).get("model_name", "")).startswith(FROZEN_PREFIXES)]
    frozen_opps = collections.Counter(
        (their_side(r) or {}).get("user_name_cn") for r in frozen)
    return {
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform_win_rate": rank.get("win_rate"),
        "platform_p90_ns": rank.get("user_cost1"),
        "public_model": {"id": model.get("id"), "updated_at": model.get("updated_at")},
        "game_counts": dict(buckets),
        "external_games": len(ext),
        "external_wins": sum(int(our_side(r).get("is_win") or 0) for r in ext),
        "frozen_lineage_games": len(frozen),
        "frozen_lineage_opponents": {k: v for k, v in frozen_opps.items() if k},
    }


def passive_split(rows: list[dict], cutover: str) -> tuple[list, list]:
    before, after = [], []
    for row in rows:
        if classify(row) != "passive" or not our_side(row):
            continue
        (after if str(row.get("created_at", "")) >= cutover else before).append(row)
    return before, after


def summarise(rows: list[dict]) -> tuple[int, int]:
    return sum(int(our_side(r).get("is_win") or 0) for r in rows), len(rows)


def cmd_snapshot(args: argparse.Namespace) -> int:
    entry = collect()
    if args.note:
        entry["note"] = args.note
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    ledger.append(entry)
    LEDGER.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("appended snapshot #%d to %s" % (len(ledger), LEDGER.relative_to(ROOT)))
    for key in ("captured_at_utc", "platform_win_rate", "external_games",
                "external_wins", "frozen_lineage_games"):
        print("  %-24s %s" % (key, entry[key]))
    print("  %-24s %s" % ("public_model.updated_at", entry["public_model"]["updated_at"]))
    print("  %-24s %s" % ("frozen_lineage_opponents", entry["frozen_lineage_opponents"]))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows = api_rows()
    model = api_public_model()
    cutover = args.cutover or str(model.get("updated_at") or "")
    if not cutover:
        print("cannot determine the publish cutover; pass --cutover ISO8601", file=sys.stderr)
        return 2
    print("cutover (public model updated_at) = %s" % cutover)
    before, after = passive_split(rows, cutover)
    bw, bn = summarise(before)
    aw, an = summarise(after)
    print()
    print("PASSIVE games (free samples; they challenge our public slot)")
    for label, w, n in (("before cutover", bw, bn), ("AFTER cutover", aw, an)):
        if n:
            lo, hi = wilson(w, n)
            print("  %-15s %3d games  %3d wins  %.3f  Wilson95 [%.3f, %.3f]"
                  % (label, n, w, w / n, lo, hi))
        else:
            print("  %-15s none yet" % label)

    # Per-opponent paired view: controls for WHO chose to challenge us.
    print()
    print("PER-OPPONENT paired view (only opponents present on both sides)")
    def by_opp(rs):
        out = collections.defaultdict(lambda: [0, 0])
        for r in rs:
            name = (their_side(r) or {}).get("user_name_cn")
            out[name][0] += 1
            out[name][1] += int(our_side(r).get("is_win") or 0)
        return out
    ob, oa = by_opp(before), by_opp(after)
    shared = sorted(set(ob) & set(oa))
    if not shared:
        print("  no opponent has challenged us both before and after yet —")
        print("  until then, the before/after gap confounds construct change with")
        print("  challenger-mix change and must NOT be read as construct improvement.")
    else:
        print("  %-24s %12s %12s" % ("opponent", "before", "after"))
        db = da = nb = na = 0
        for name in shared:
            print("  %-24s %5d/%-6d %5d/%-6d" % (name, ob[name][1], ob[name][0],
                                                 oa[name][1], oa[name][0]))
            db += ob[name][1]; nb += ob[name][0]
            da += oa[name][1]; na += oa[name][0]
        if nb and na:
            print("  %-24s %5d/%-6d %5d/%-6d   delta = %+.3f"
                  % ("POOLED (shared only)", db, nb, da, na, da / na - db / nb))

    baseline = bw / bn if bn else 0.2314
    print()
    print("READ-OUT GATE — samples needed to call an improvement (one-sided, alpha=0.05, power=0.80)")
    print("  baseline = %.4f (pre-cutover passive rate)" % baseline)
    for target in (0.30, 0.35, 0.40, 0.50, 0.69):
        if target > baseline:
            tag = "  <- top-16 cutoff" if abs(target - 0.69) < 1e-9 else ""
            print("    to demonstrate >= %.2f : %4d passive games%s"
                  % (target, required_n(baseline, target), tag))
    print("  Until the relevant n is reached, report the Wilson interval and say")
    print("  'not yet decidable'. Do not quote a point estimate as the answer.")
    return 0


def cmd_power(args: argparse.Namespace) -> int:
    baseline = args.baseline
    print("baseline = %.4f" % baseline)
    for target in (0.30, 0.35, 0.40, 0.50, 0.60, 0.69):
        if target > baseline:
            print("  detect >= %.2f : n = %d" % (target, required_n(baseline, target)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot"); snap.add_argument("--note", default="")
    snap.set_defaults(func=cmd_snapshot)
    rep = sub.add_parser("report"); rep.add_argument("--cutover", default="")
    rep.set_defaults(func=cmd_report)
    pw = sub.add_parser("power"); pw.add_argument("--baseline", type=float, default=0.2314)
    pw.set_defaults(func=cmd_power)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
