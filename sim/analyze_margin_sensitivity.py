#!/usr/bin/env python3
"""Empirical win-rate sensitivity to net-score margin, from archived platform logs.

WHY THIS EXISTS
    The repo has been screening candidates against a fixed ``+150 gold/game`` admission
    gate.  That gate was reverse-engineered from "close the -294 deficit against
    Tiuntled-1", but ``docs/赛制介绍.md:35`` says the qualifier is a **full-field round
    robin ranked by win rate**.  For an opponent we are nearly level with, the sign of the
    per-game margin decides the game, so the value of marginal gold is concentrated in a
    "near-tie band".  A fixed +150 floor implicitly asserts that band is empty.  This
    module measures whether it is.

WHAT IT MEASURES (three quantities, all on the SAME-GAME DIFFERENCE)
    1. the per-game margin histogram,
    2. how many opponents sit inside the near-tie band, and
    3. ``dP(win)/d(margin)`` by **counterfactual counting**: shift every game's margin by
       ``s`` and count how many win/loss signs flip.  No distributional assumption.

MARGIN IS A DIFFERENCE, NEVER OUR OWN NET
    ``margin = our_net - their_net`` within one game.  The most expensive lesson in this
    project is the gap between ``net`` and ``margin``: the B2 candidate scored net +111
    while its margin was only +14.5, because the mechanism was ceding ground rather than
    earning.  Any admission gate must be stated in margin.

LOAD-BEARING CAVEATS (these bound what the output may be used for)
    * CONFIGURATION MIXING.  The archive spans ~300 different builds of our own player,
      not the in-service one.  So this is NOT the current build's margin distribution, and
      by the six-condition rule it must not be subtracted from anything measured on a
      single build.  It answers an EXISTENCE question ("is the band populated at all"),
      which is far more robust to build identity than any level is.
    * OPPONENT SELECTION.  We chose whom to challenge, and mostly challenged the two
      strongest, so the opponent mix is not the field.  Per-game pooled numbers are
      therefore reported alongside a variant that drops those two.
    * WITHIN-TEAM NOISE VS BAND WIDTH.  ``run`` prints the pooled within-team single-game
      SD.  If that SD is comparable to the band half-width, then a design with one game
      per team CANNOT resolve per-team band membership -- a team truly at the centre of
      the band is detected only Phi(w/sigma)*2-1 of the time.  Quantity 3 is immune to
      this, because win/loss is decided per game and never needs a team mean.

DRY RUNS (rule 33; the ``validate`` subcommand)
    Both the adjudicator AND the corpus filter are dry-run tested.  The filter test is not
    decoration: the first version of this analysis silently admitted self-play games
    because ``bool(n) and regex.match(n) and ...`` yields ``None`` for a non-match, and
    ``None == False`` is ``False`` in Python, so the "both sides are ours" guard never
    fired.  That inflated the opponent count from 12 to 106.  A dry run that covers only
    the statistic and not the sample selection would not have caught it.

Usage
    python3 sim/analyze_margin_sensitivity.py validate
    python3 sim/analyze_margin_sensitivity.py run [--logs DIR] [--output JSON]
Deterministic: files are iterated in sorted order; there is no randomness.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS = ROOT / "logs"
DEFAULT_JSON = ROOT / "sim" / "reports" / "margin_sensitivity.json"

#: Organiser-run test accounts.  They hold public models and appear on the ladder but do
#: not enter the qualifier, so every field estimate must drop them.
TEST_ACCOUNTS = frozenset({"player2", "player3", "player4"})
#: Ladder opponents are named ``player<digits>``; our own builds carry free-form names.
OPPONENT_NAME = re.compile(r"^player\d+$")
#: Our own account.  Games of "one of our builds" vs "our published slot" are self-play.
OUR_ACCOUNT = "player220"

MIN_ROUNDS = 450
BAND_THRESHOLDS = (200, 400)
SHIFTS = (40, 100, 150, 200, 400)
HIST_EDGES = (-400, -200, -100, 0, 100, 200, 400)


def is_opponent(name: Any) -> bool:
    """True iff ``name`` is a real ladder opponent.

    Returns a strict ``bool``.  Do not simplify to an ``and`` chain: a non-matching name
    would then yield ``None``, and ``None == False`` is ``False``, which silently defeats
    the "both sides are ours" guard in :func:`keep_pairing`.
    """
    if not isinstance(name, str) or not name:
        return False
    return OPPONENT_NAME.match(name) is not None and name != OUR_ACCOUNT


def keep_pairing(p1: Any, p2: Any) -> bool:
    """True iff exactly one side is a real opponent and neither is a test account.

    Both names must be non-empty strings first: a malformed header carrying ``None`` would
    otherwise pass the ``is_opponent(p1) != is_opponent(p2)`` guard, since ``None`` is not
    an opponent and the other side is.  The dry run pins this case.
    """
    if not isinstance(p1, str) or not isinstance(p2, str) or not p1 or not p2:
        return False
    if p1 in TEST_ACCOUNTS or p2 in TEST_ACCOUNTS:
        return False
    return is_opponent(p1) != is_opponent(p2)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval.  Used because the band counts are small-sample."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def flips(margins: Sequence[float], shift: float) -> int:
    """Games whose win/loss sign flips when every margin moves by ``shift``.

    A margin of exactly 0 counts as a loss, so shifting -100 up by +100 lands on 0 and
    must NOT be counted as a flip.  That boundary is the off-by-one this function is most
    likely to get wrong, and ``validate`` pins it.
    """
    return sum(1 for m in margins if (m > 0) != ((m + shift) > 0))


def _summary(values: Sequence[float]) -> Mapping[str, float]:
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = sum(values) / n
    if n > 1:
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
        se = sd / math.sqrt(n)
    else:
        sd = se = float("nan")
    ordered = sorted(values)
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "se": se,
        "median": ordered[n // 2],
        "min": ordered[0],
        "max": ordered[-1],
    }


def final_margin(path: Path) -> tuple[list[int], int, bool] | None:
    """Return ([net_p1, net_p2], rounds, forfeit) from the last ``end`` phase.

    Score is gross gold minus vision spend.  Reads streaming so a 1.6MB log costs one
    pass and no full parse of intermediate rounds.
    """
    last: Mapping[str, Any] | None = None
    forfeit = False
    rounds = 0
    with path.open() as handle:
        handle.readline()  # line 1: player names
        handle.readline()  # line 2: map tokens
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if "forfeit" in record:
                forfeit = True
                continue
            end = record.get("end")
            if end is not None:
                last = end
                rounds += 1
    if last is None:
        return None
    nets = [p.get("gold", 0) - p.get("vision_spent", 0) for p in last["players"]]
    if len(nets) != 2:
        return None
    return nets, rounds, forfeit


def collect(logs_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(logs_dir.glob("game_*.log")):
        try:
            with path.open() as handle:
                header = json.loads(handle.readline())
        except (OSError, ValueError):
            continue
        p1, p2 = header.get("player1"), header.get("player2")
        if not keep_pairing(p1, p2):
            continue
        parsed = final_margin(path)
        if parsed is None:
            continue
        nets, rounds, forfeit = parsed
        if rounds < MIN_ROUNDS or forfeit:
            continue
        if is_opponent(p2):
            ours, theirs, opponent, our_build = nets[0], nets[1], p2, p1
        else:
            ours, theirs, opponent, our_build = nets[1], nets[0], p1, p2
        rows.append(
            {
                "game": path.stem,
                "opponent": opponent,
                "our_build": our_build,
                "margin": ours - theirs,
            }
        )
    return rows


# --------------------------------------------------------------------------- dry runs
FILTER_CASES = (
    (("unified", "player220"), False, "self-play: our build vs our own published slot"),
    (("player220", "unified"), False, "self-play, seats swapped"),
    (("cpp20", "player47"), True, "our build vs a real opponent"),
    (("player47", "cpp20"), True, "real opponent in seat 1"),
    (("player163", "player57"), False, "two opponents, neither of them us"),
    (("terra143", "terra188"), False, "two of our own builds"),
    (("probeobs", "player2"), False, "organiser test account must be dropped"),
    ((None, "player47"), False, "missing name"),
)

FLIP_CASES = (
    ([0.0] * 50, 100, 50, "zero signal: 0 is a loss, +100 makes every one a win"),
    ([0.0] * 50, 0, 0, "zero signal, zero shift: nothing may flip"),
    ([5000.0] * 25 + [-5000.0] * 25, 40, 0, "margins far from 0: small shift flips none"),
    ([5000.0] * 25 + [-5000.0] * 25, 400, 0, "still none at +400"),
    ([50.0] * 20 + [-50.0] * 20, 100, 20, "forward: only the losers flip"),
    ([50.0] * 20 + [-50.0] * 20, -100, 20, "reversed: only the winners flip"),
    ([-100.0], 100, 0, "boundary: -100 shifted +100 lands on 0, still a loss"),
    ([-101.0], 100, 0, "boundary: -101 shifted +100 is -1, still a loss"),
    ([-99.0], 100, 1, "boundary: -99 shifted +100 is +1, now a win"),
)


def validate() -> int:
    failures = 0
    print("== dry run: corpus filter ==")
    print("   (the first version of this analysis failed exactly here, not in the maths)")
    for (p1, p2), want, why in FILTER_CASES:
        got = keep_pairing(p1, p2)
        ok = got == want
        failures += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {str(p1):12s} vs {str(p2):12s} "
              f"keep={got!s:5s} want={want!s:5s}  {why}")
    print("== dry run: counterfactual flip counter (zero / true / reversed / boundary) ==")
    for margins, shift, want, why in FLIP_CASES:
        got = flips(margins, shift)
        ok = got == want
        failures += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] n={len(margins):3d} shift={shift:+5d} "
              f"flips={got:3d} want={want:3d}  {why}")
    print("== dry run: Wilson interval ==")
    lo, hi = wilson(0, 10)
    ok = lo == 0.0 and 0.0 < hi < 0.4
    failures += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] wilson(0,10) = [{lo:.3f}, {hi:.3f}] "
          "must pin the lower end at 0 and stay well below 0.4")
    lo, hi = wilson(5, 10)
    ok = lo < 0.5 < hi
    failures += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] wilson(5,10) = [{lo:.3f}, {hi:.3f}] must straddle 0.5")
    print(f"RESULT: {'PASS' if failures == 0 else f'FAIL ({failures})'}")
    return 1 if failures else 0


# ------------------------------------------------------------------------------- run
def analyse(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    margins = [float(r["margin"]) for r in rows]
    by_opponent: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_opponent[row["opponent"]].append(float(row["margin"]))

    histogram: Counter[str] = Counter()
    labels: list[str] = []
    bounds = (-math.inf,) + HIST_EDGES + (math.inf,)
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        label = (f"<{hi:.0f}" if lo == -math.inf else
                 f">{lo:.0f}" if hi == math.inf else f"{lo:.0f}..{hi:.0f}")
        labels.append(label)
        histogram[label] = sum(1 for m in margins if lo <= m < hi)

    band_games: dict[str, Any] = {}
    for thr in BAND_THRESHOLDS:
        k = sum(1 for m in margins if abs(m) <= thr)
        lo, hi = wilson(k, len(margins))
        band_games[str(thr)] = {"count": k, "n": len(margins),
                                "share": k / len(margins) if margins else float("nan"),
                                "wilson95": [lo, hi]}

    teams = {name: _summary(vals) | {"win_rate": sum(1 for v in vals if v > 0) / len(vals)}
             for name, vals in sorted(by_opponent.items(), key=lambda kv: -len(kv[1]))}
    band_teams: dict[str, Any] = {}
    for thr in BAND_THRESHOLDS:
        k = sum(1 for s in teams.values() if abs(s["mean"]) <= thr)
        lo, hi = wilson(k, len(teams))
        band_teams[str(thr)] = {
            "count": k, "n_teams": len(teams), "wilson95": [lo, hi],
            "caveat": "small sample; per the reporting discipline this is UNDECIDABLE and "
                      "must not be used to recalibrate the gate",
        }

    # Pooled within-team single-game SD.  This is what decides whether a design with a
    # handful of games per team can resolve per-team band membership at all.
    num = den = 0.0
    per_team_sd = {}
    for name, vals in by_opponent.items():
        if len(vals) < 5:
            continue
        s = _summary(vals)
        per_team_sd[name] = {"n": s["n"], "sd": s["sd"]}
        num += s["sd"] * (s["n"] - 1)
        den += s["n"] - 1
    sigma_single = num / den if den else float("nan")

    slope: dict[str, Any] = {}
    heavy = {"player163", "player57"}
    light = [float(r["margin"]) for r in rows if r["opponent"] not in heavy]
    for shift in SHIFTS:
        k = flips(margins, shift)
        lo, hi = wilson(k, len(margins))
        entry = {
            "shift": shift,
            "pooled_flips": k,
            "pooled_pp": 100.0 * k / len(margins) if margins else float("nan"),
            "pooled_wilson95_pp": [100 * lo, 100 * hi],
            "pp_per_gold": (100.0 * k / len(margins) / shift) if margins else float("nan"),
        }
        if light:
            kl = flips(light, shift)
            llo, lhi = wilson(kl, len(light))
            entry["excl_two_strongest_pp"] = 100.0 * kl / len(light)
            entry["excl_two_strongest_wilson95_pp"] = [100 * llo, 100 * lhi]
            entry["excl_two_strongest_n"] = len(light)
        per = [flips(v, shift) / len(v) for v in by_opponent.values()]
        s = _summary([100.0 * p for p in per])
        entry["equal_weight_per_team_pp"] = s["mean"]
        entry["equal_weight_per_team_se_pp"] = s["se"]
        entry["equal_weight_n_teams"] = s["n"]
        slope[str(shift)] = entry

    return {
        "purpose": "measure dP(win)/d(margin) empirically so the admission gate is a "
                   "recalibratable quantity rather than a hard-coded constant",
        "conditions": {
            "margin_definition": "our_net - their_net within the SAME game "
                                 "(never our own net; see the B2 net +111 / margin +14.5 case)",
            "score_definition": "gross gold - vision spend, from the final end phase",
            "corpus": "all archived platform logs; exactly one side a real ladder opponent",
            "our_build": "MIXED (~hundreds of builds) -- NOT the in-service build; usable "
                         "for existence, not as any single build's distribution",
            "opponent_selection": "we chose whom to challenge and mostly challenged the two "
                                  "strongest, so the opponent mix is not the field",
            "excluded": sorted(TEST_ACCOUNTS) + [f"self-play vs {OUR_ACCOUNT}",
                                                 f"games shorter than {MIN_ROUNDS} rounds",
                                                 "forfeited games"],
        },
        "games": len(rows),
        "n_opponents": len(by_opponent),
        "histogram": {"labels": labels, "counts": {k: histogram[k] for k in labels}},
        "near_tie_band_by_game": band_games,
        "near_tie_band_by_team": band_teams,
        "per_team": teams,
        "within_team_single_game_sd": {
            "per_team": per_team_sd,
            "pooled": sigma_single,
            "note": "inflated by build heterogeneity; opponents we faced with few builds "
                    "give the cleaner read. Compare against the band half-width: if the SD "
                    "is not well below it, one game per team cannot resolve band membership.",
        },
        "slope": slope,
    }


def render(report: Mapping[str, Any]) -> None:
    print(f"games={report['games']}  real opponents={report['n_opponents']}")
    print("\n-- per-game margin histogram --")
    hist = report["histogram"]
    total = report["games"]
    for label in hist["labels"]:
        c = hist["counts"][label]
        print(f"  {label:>12}: {c:4d} ({100.0 * c / total:5.1f}%)")
    print("\n-- near-tie band, BY GAME (this is what decides win/loss) --")
    for thr, v in report["near_tie_band_by_game"].items():
        lo, hi = v["wilson95"]
        print(f"  |margin| <= {thr:>3}: {v['count']:4d}/{v['n']} = {100 * v['share']:5.1f}% "
              f"Wilson95 [{100 * lo:.1f}, {100 * hi:.1f}]%")
    print("\n-- near-tie band, BY TEAM (small sample: UNDECIDABLE) --")
    for thr, v in report["near_tie_band_by_team"].items():
        lo, hi = v["wilson95"]
        print(f"  |mean margin| <= {thr:>3}: {v['count']}/{v['n_teams']} "
              f"Wilson95 [{100 * lo:.0f}, {100 * hi:.0f}]%  -- UNDECIDABLE, do not recalibrate on this")
    sd = report["within_team_single_game_sd"]
    print(f"\n-- within-team single-game SD (pooled) = {sd['pooled']:.0f} gold --")
    for thr in BAND_THRESHOLDS:
        print(f"  the +/-{thr} band is {thr / sd['pooled']:.2f} pooled-sigma wide")
    print("\n-- empirical dP(win)/d(margin) by counterfactual counting --")
    print(f"  {'shift':>6} {'pooled':>18} {'excl 2 strongest':>20} {'per-team equal wt':>20} {'pp/gold':>9}")
    for shift, v in report["slope"].items():
        lo, hi = v["pooled_wilson95_pp"]
        excl = v.get("excl_two_strongest_pp")
        print(f"  {shift:>6} {v['pooled_pp']:6.2f}pp [{lo:4.2f},{hi:5.2f}] "
              f"{(f'{excl:6.2f}pp' if excl is not None else 'n/a'):>20} "
              f"{v['equal_weight_per_team_pp']:8.2f}+-{v['equal_weight_per_team_se_pp']:.2f}pp "
              f"{v['pp_per_gold']:9.4f}")
    print("\n  READ: if pp/gold is roughly constant across shifts, marginal gold is priced "
          "LINEARLY and no hard threshold gate is justified.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="dry-run the corpus filter and the adjudicator")
    run = sub.add_parser("run", help="parse the archive and write the JSON artifact")
    run.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    run.add_argument("--output", type=Path, default=DEFAULT_JSON)
    run.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "validate":
        return validate()

    rows = collect(args.logs)
    if not rows:
        print(f"no usable games under {args.logs}", file=sys.stderr)
        return 2
    report = analyse(rows)
    render(report)
    if not args.no_write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
