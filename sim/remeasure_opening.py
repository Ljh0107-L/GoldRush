#!/usr/bin/env python3
"""ARMED, NOT FIRED: re-measure the opening bands after the fix is published.

Run this only AFTER a new construct reaches the public slot. It answers one
question: how much of the 20ns that band r8-24 contributes to our P90 did the
scan-skip fix actually take?

    python3 sim/remeasure_opening.py            # measure and compare
    python3 sim/remeasure_opening.py --min-games 25   # demand a larger sample

WHY THIS EXISTS AS A SCRIPT
This will be run under time pressure, and the failure mode under time pressure
is reading a premature number. So the sample-size gate, the era boundary, the
concurrency filter and the baseline are all decided HERE, in advance, rather
than in the moment. Nothing below requires a judgement call at run time.

THE PENDING VERIFICATION THIS DISCHARGES
The fix's value is currently known only as a bound: ΔP90 is at most 20ns,
because that is what removing the whole band is worth, and at least 0. The
point estimate cannot be derived from the fix's 73.2% row-skip rate --
multiplying 20 by 0.732 is proportion arithmetic applied to a quantile, which
is exactly the error this project has filed a rule against. A quantile only
moves as much as removal-and-recompute says it does, so the true figure
requires per-round costs from the patched build. That is what this measures.

PREREQUISITE: a publish, which needs the owner's explicit approval. Until then
this script has nothing to read and will refuse to report.

METHOD, fixed in advance so it matches arm A exactly:
  * era boundary read LIVE from the public slot's `updated_at`, so the script
    cannot go stale the way a hard-coded changeover date did. Epochs are a
    list, never a constant.
  * serial games only (one submission per timestamp minute). Concurrency
    doubles OUR OWN measured cost -- 220 serial against 430 in a six-game
    burst on one construct -- while leaving opponents' cost alone, because the
    contention is among our own processes.
  * `is_contest()` predicate applied per game, never a list of known-broken
    teams. The list is a cache; the predicate is the test.
  * warm-up rounds are KEPT. r0-7 is the object of measurement here, so the
    usual r<4 exclusion would delete the thing being measured.
  * random control on every band: excise the same NUMBER of rounds at random
    and recompute, to separate a real shift from sample shrinkage.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "tools"))

import arena  # noqa: E402
import field_sample as fs  # noqa: E402

LOGS = ROOT / "logs"
OUR_MODELS = {"fA", "fSA", "fSB", "fB", "player220"}
OUR_USER_ID = 220

# Arm A, measured 2026-08-12 on the pre-fix construct. 37 serial post-changeover
# games, 18,500 rounds, all rounds including r0. These are the numbers the new
# build gets compared against; they are frozen deliberately.
ARM_A = {
    "games": 37,
    "rounds": 18500,
    "p90": 230,
    "mean": 194.9,
    "bands": {"r0-7": {"dp90": 10, "dmean": 24.0},
              "r8-24": {"dp90": 20, "dmean": 5.4},
              "r0-24": {"dp90": 30, "dmean": 30.4}},
    "steady_p50": 140,
    "rivals": {"T-1": {"p90": 200}, "Tundra": {"p90": 230}},
}

BANDS = [("r0-7", 0, 7), ("r8-24", 8, 24), ("r0-24", 0, 24), ("r25-49", 25, 49)]
CONTROL_DRAWS = 400


def quantile(values: list[int], pct: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def public_slot_updated_at() -> str | None:
    """Live era boundary. A hard-coded date silently mixed two constructs once
    already; reading it means this script cannot repeat that."""
    for page in range(1, 14):
        try:
            payload = arena.call("GET", "/api/user/get_model_list_4",
                                 params={"page": page, "page_size": 50})
        except Exception:
            return None
        entries = payload.get("list") or []
        if not entries:
            return None
        for entry in entries:
            if int(entry.get("user_id") or 0) == OUR_USER_ID:
                stamp = str(entry.get("updated_at") or "")
                return stamp.replace("Z", "").replace("T", "T")[:19] or None
    return None


def fetch(gid: int) -> bool:
    path = LOGS / f"game_{gid}.log"
    if path.exists() and path.stat().st_size > 1000:
        return True
    try:
        text = arena.call("GET", "/api/user/get_game_log", params={"id": gid}, raw=True)
    except Exception:
        return False
    if text and len(text) > 1000:
        path.write_text(text)
        return True
    return False


def our_rounds(after: str, min_games: int) -> tuple[list[tuple[int, int]], int, dict[str, int]]:
    games = fs.all_games()
    minute = Counter(g["created_at"][:16] for g in games)
    dropped = {"pre_era": 0, "concurrent": 0, "broken_slot": 0, "no_log": 0, "short": 0}
    pairs: list[tuple[int, int]] = []
    kept = 0
    for record in games:
        players = record.get("players") or []
        if len(players) != 2:
            continue
        if not any(str(p.get("model_name") or "") in OUR_MODELS for p in players):
            continue
        if record["created_at"][:19] < after:
            dropped["pre_era"] += 1
            continue
        if minute[record["created_at"][:16]] != 1:
            dropped["concurrent"] += 1
            continue
        gid = int(record["id"])
        if not fetch(gid):
            dropped["no_log"] += 1
            continue
        with open(LOGS / f"game_{gid}.log", encoding="utf-8") as handle:
            handle.readline()
            handle.readline()
            rows = [json.loads(line) for line in handle if line.strip().startswith("{")]
        header_path = LOGS / f"game_{gid}.log"
        with open(header_path, encoding="utf-8") as handle:
            header = json.loads(handle.readline())
        ends = [r for r in rows if "end" in r]
        if len(ends) < 400:
            dropped["short"] += 1
            continue
        us = 1 if header.get("player1") in OUR_MODELS else 2
        them = 2 if us == 1 else 1
        final = {int(p["id"]): p for p in ends[-1]["end"]["players"]}
        opponent = final.get(them, {})
        if (int(opponent.get("gold") or 0) - int(opponent.get("vision_spent") or 0)) < 0:
            dropped["broken_slot"] += 1
            continue
        kept += 1
        for row in ends:
            by_id = {int(p["id"]): p for p in row["end"]["players"]}
            if us in by_id:
                pairs.append((int(row.get("round", 0)), int(by_id[us].get("cost") or 0)))
    return pairs, kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-games", type=int, default=15,
                        help="refuse to report below this many clean games")
    parser.add_argument("--after", default=None,
                        help="era boundary override; default reads the public slot live")
    args = parser.parse_args()

    after = args.after or public_slot_updated_at()
    if not after:
        print("could not read the public slot's updated_at; pass --after explicitly",
              file=sys.stderr)
        return 2
    print(f"era boundary (public slot updated_at, read live): {after}")

    pairs, games, dropped = our_rounds(after, args.min_games)
    print(f"clean games: {games}   rounds: {len(pairs)}")
    print("dropped: " + "  ".join(f"{k}={v}" for k, v in dropped.items()))

    if games < args.min_games:
        print(f"\nREFUSING TO REPORT: {games} clean games < --min-games {args.min_games}.")
        print("Arm A used 37. Reading a premature number here is the failure mode this")
        print("gate exists to prevent; wait for defence games to accumulate and re-run.")
        return 3

    costs = [c for _, c in pairs]
    p90 = quantile(costs, 90)
    mean = st.mean(costs)
    print(f"\n{'':>10} {'arm A (pre-fix)':>17} {'arm B (now)':>13} {'change':>9}")
    print(f"{'P90':>10} {ARM_A['p90']:>17} {p90:>13} {p90 - ARM_A['p90']:>+9}")
    print(f"{'mean':>10} {ARM_A['mean']:>17.1f} {mean:>13.1f} {mean - ARM_A['mean']:>+9.1f}")

    random.seed(0)
    print(f"\nper-band, priced by removal-and-recompute (never by proportion):")
    print(f"{'band':>8} {'n':>6} {'median':>7} {'dP90':>6} {'ctrl':>7} {'dmean':>7} "
          f"{'arm A dP90':>11} {'verdict':>24}")
    for label, lo, hi in BANDS:
        segment = [c for r, c in pairs if lo <= r <= hi]
        kept = [c for r, c in pairs if not (lo <= r <= hi)]
        if not segment or not kept:
            continue
        band_p90 = quantile(kept, 90)
        d90 = p90 - band_p90
        control = []
        for _ in range(CONTROL_DRAWS):
            idx = set(random.sample(range(len(costs)), len(segment)))
            control.append(quantile([c for i, c in enumerate(costs) if i not in idx], 90))
        cd = p90 - st.mean(control)
        dmean = mean - st.mean(kept)
        prior = ARM_A["bands"].get(label, {}).get("dp90")
        if prior is None:
            verdict = ""
        elif abs(d90) <= abs(cd) + 2:
            verdict = "gone (indistinguishable)"
        elif d90 < prior:
            verdict = f"reduced from {prior}"
        else:
            verdict = f"NOT reduced (was {prior})"
        print(f"{label:>8} {len(segment):>6} {st.median(segment):>7.0f} {d90:>+6} "
              f"{cd:>+7.2f} {dmean:>+7.1f} {str(prior):>11} {verdict:>24}")

    took = ARM_A["bands"]["r8-24"]["dp90"] - (
        p90 - quantile([c for r, c in pairs if not (8 <= r <= 24)], 90))
    print(f"\nwhat the fix took from band r8-24: {took:+} ns of the 20ns bound [0, 20]")
    for name, ref in ARM_A["rivals"].items():
        print(f"   versus {name} at {ref['p90']}: "
              f"{'AHEAD' if p90 < ref['p90'] else 'behind'} by {abs(p90 - ref['p90'])}ns")
    print("\nCaliber: platform per-round cost, serial games only, is_contest applied per")
    print("game. Official three maps only -- this says nothing about unfamiliar maps,")
    print("where the generalisation line measures a materially different profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
