#!/usr/bin/env python3
"""Cross-day passive-game monitor: is our win rate above the front-16 threshold yet?

WHY THIS EXISTS
    Our position relative to the qualifying threshold is UNRESOLVED, and it cannot be
    resolved by spending quota. Three facts force that conclusion:

    * The round-robin estimand is the mean over OPPONENTS of P(win), not the mean over
      games. Our passive corpus is badly unbalanced -- one opponent challenged us six times
      and won all six -- so per-game weighting (63.0%) is simply the wrong functional.
      Per-opponent gives 76.7%.
    * But per-opponent has only n=10 teams, and seven of them sit at exactly 1.000, so the
      between-team sd is 0.417 and the bootstrap CI over teams is [50.0, 100.0]. The
      threshold sits inside it. P(above threshold) is 75.3%.
    * Widening that interval needs more OPPONENTS, and opponents only enter through passive
      games, which we cannot initiate -- other teams choose to challenge us. So the binding
      constraint is elapsed time, not quota.

    Buying active games does not substitute. Active games attack the opponent's PUBLIC
    slot, which may be generations stale, while passive games face whatever they currently
    run. Measuring the active condition more precisely just measures the wrong condition
    more precisely; the binding uncertainty is the bias between the two, and that bias is
    only identifiable on opponents appearing in BOTH corpora -- which again needs passive
    games. Hence: a free monitor, not a purchase.

WHAT IT REPORTS, AND WHEN IT STAYS QUIET
    Per-opponent estimate, bootstrap CI over teams, P(above threshold), and the threshold's
    value AT THE TIME OF READING -- the threshold is rank 16's 24-hour rolling win rate and
    is NOT a constant (observed moving from 0.6756 to 0.6731 within about twenty minutes).
    It alerts only when the CI clears the threshold on one side or the other. A monitor that
    prints "still undecidable" every day trains its reader to ignore it.

    It also tracks near-tie games (|margin| <= 150) against the three opponents that have
    ever beaten us passively, because those are the only convertible places in the standings.

TWO FILTERS THAT ARE NOT OPTIONAL
    * ``is_contest``: an opponent finishing on a NEGATIVE net score spent more on vision
      than it collected -- a broken configuration, not a defeat. Two opponents have done
      this. Including them moved the estimate from below the threshold to above it.
    * post-changeover only: our public slot was replaced at 2026-08-10T08:20:18Z and the
      previous occupant was about 18x slower, so games before that cutoff measure a
      different player.

Usage
    python3 sim/passive_monitor.py validate     # dry run: filters AND statistics
    python3 sim/passive_monitor.py poll         # fetch, append, judge, alert or stay quiet
    python3 sim/passive_monitor.py bias         # paired staleness identification
Deterministic apart from the platform fetch; bootstrap uses a fixed seed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "sim" / "reports" / "passive_monitor.json"
FIELD_SAMPLE = ROOT / "sim" / "reports" / "field_sample.json"

OUR_USER_ID = 220
OUR_SLOT_NAME = "player220"
#: Every time we publish, the passive corpus starts measuring a DIFFERENT player. A single
#: hard-coded cutoff silently rots the moment the next publish happens, which is exactly what
#: went wrong: this constant was pinned to the 08-10 publish, never learned about the
#: 08-12T01:37:41Z relaunch, and so pooled two constructs into one 80.7% figure that
#: understated the current one by 16pp (77.9% before vs 93.5% after). The fix is structural,
#: not a reminder to update a date: eras are a LIST, the newest entry defines "current", and
#: the monitor reports every era separately so a missing entry shows up as a suspiciously long
#: era rather than as a silently mixed pool.
#: APPEND a new entry on every publish. Do not edit the old ones.
CONSTRUCT_ERAS = [
    ("fd47ea6-reflex", dt.datetime(2026, 8, 10, 8, 20, 18, tzinfo=dt.timezone.utc)),
    ("relaunch-2026-08-12", dt.datetime(2026, 8, 12, 1, 37, 41, tzinfo=dt.timezone.utc)),
]
#: earliest era boundary: games before this are a different player entirely (about 18x slower)
CHANGEOVER = CONSTRUCT_ERAS[0][1]


def era_of(created_at) -> str:
    """Which construct era a game belongs to. Newest matching era wins.

    A record without a timestamp is attributed to the CURRENT era: synthetic dry-run inputs
    have no ``created_at``, and defaulting them to "current" keeps the dry run exercising the
    headline path rather than a legacy branch nobody reads.
    """
    if not created_at:
        return CONSTRUCT_ERAS[-1][0]
    t = _ts(created_at)
    label = "pre-" + CONSTRUCT_ERAS[0][0]
    for name, start in CONSTRUCT_ERAS:
        if t > start:
            label = name
    return label


def current_era() -> str:
    return CONSTRUCT_ERAS[-1][0]
TEST_USER_IDS = frozenset({2, 3, 4})
NEAR_TIE = 150
#: opponents that have ever beaten us in the passive condition
WATCH = ("1", "rikka", "君の仿瓷")
BOOTSTRAP = 20000
SEED = 5
#: n at which the CI is expected to narrow enough to decide; see the handoff note
TARGET_TEAMS = 25


#: Known broken slots, consolidated so a reader gets the whole picture at once. A negative
#: final net means the slot spent more on vision than it ever collected. Note the malfunction
#: makes them SLOWER (P50 4400-5600ns, buying vision every round), so these games inflate an
#: opponent's apparent latency rather than flattering it.
KNOWN_BROKEN_SLOTS = {
    "QuantLK":     "13 games, gid 190039-228126, net -22 to -815; broken for 2+ days running",
    "Tundra-wawa": "2 games, gid 191692/192807, net -1496/-1491, gold 4/9 vs vision 1500",
    "D12":         "1 game, gid 192902, net -721",
    "hhh":         "1 game, gid 192912, net -64",
}


def is_contest(their_net: Any) -> bool:
    """False when the opponent's slot failed rather than lost (negative final net).

    See :data:`KNOWN_BROKEN_SLOTS`. This is a rate, not an anomaly: about 7% of contested
    games are opponent malfunctions, so the filter is required rather than defensive.
    """
    if their_net is None:
        return True
    return int(their_net) >= 0


def keep_game(row: Mapping[str, Any]) -> bool:
    """Passive, post-changeover, not self-play, not a test account, not an error."""
    if int(row.get("user_id2") or 0) != OUR_USER_ID:
        return False                                    # not a game we were challenged in
    if int(row.get("user_id") or 0) == OUR_USER_ID:
        return False                                    # self-play
    if int(row.get("user_id") or 0) in TEST_USER_IDS:
        return False
    if row.get("error_msg"):
        return False
    players = row.get("players") or []
    mine = [p for p in players if p.get("model_name") == OUR_SLOT_NAME]
    if len(mine) != 1 or len(players) != 2:
        return False
    created = row.get("created_at")
    if not created:
        return False
    return _ts(created) > CHANGEOVER


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def to_record(row: Mapping[str, Any]) -> dict[str, Any]:
    players = row["players"]
    mine = [p for p in players if p.get("model_name") == OUR_SLOT_NAME][0]
    theirs = [p for p in players if p is not mine][0]
    return {
        "game_id": row["id"],
        "created_at": row["created_at"],
        "map_id": row.get("map_id"),
        "opponent": theirs.get("user_name_cn") or theirs.get("model_name"),
        "opponent_model": theirs.get("model_name"),
        "our_net": int(mine.get("coin_num") or 0),
        "their_net": int(theirs.get("coin_num") or 0),
        "is_win": int(mine.get("is_win") or 0),
    }


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n <= 0:
        return [float("nan"), float("nan")]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def judge(records: Sequence[Mapping[str, Any]], threshold: float | None) -> dict[str, Any]:
    """Per-opponent estimate with a bootstrap CI over TEAMS, plus the decision gate."""
    usable = [r for r in records if is_contest(r["their_net"])]
    dropped = [r for r in records if not is_contest(r["their_net"])]
    # Pooling across publishes mixes constructs. Headline the CURRENT era; keep the rest visible.
    by_era: dict[str, list] = defaultdict(list)
    for r in usable:
        by_era[era_of(r.get("created_at"))].append(r)
    cur = current_era()
    era_summary = {}
    for name, rows in sorted(by_era.items()):
        b: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            b[str(r["opponent"])].append(int(r["our_net"]) - int(r["their_net"]))
        rr = [sum(1 for m in v if m > 0) / len(v) for v in b.values()]
        era_summary[name] = {"games": len(rows), "teams": len(b),
                             "per_opponent": (sum(rr) / len(rr)) if rr else None,
                             "losses": {t: sorted(v) for t, v in b.items() if any(m <= 0 for m in v)}}
    usable = by_era.get(cur, [])
    by: dict[str, list[int]] = defaultdict(list)
    for r in usable:
        by[str(r["opponent"])].append(int(r["our_net"]) - int(r["their_net"]))
    teams = sorted(by)
    if not teams:
        return {"n_teams": 0, "verdict": "no data"}
    rates = [sum(1 for m in by[t] if m > 0) / len(by[t]) for t in teams]
    per_opponent = sum(rates) / len(rates)
    n_games = sum(len(by[t]) for t in teams)
    wins = sum(sum(1 for m in by[t] if m > 0) for t in teams)
    rng = random.Random(SEED)
    draws = []
    for _ in range(BOOTSTRAP):
        pick = [rng.choice(teams) for _ in teams]
        draws.append(sum(sum(1 for m in by[t] if m > 0) / len(by[t]) for t in pick) / len(pick))
    draws.sort()
    ci = [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]]
    out: dict[str, Any] = {
        "current_era": cur, "by_construct_era": era_summary,
        "n_teams": len(teams), "n_games": n_games,
        "excluded_broken": len(dropped),
        "excluded_broken_detail": [{"opponent": r["opponent"], "their_net": r["their_net"],
                                    "game_id": r["game_id"]} for r in dropped],
        "per_game_rate": wins / n_games,
        "per_opponent_rate": per_opponent,
        "between_team_sd": st.stdev(rates) if len(rates) > 1 else float("nan"),
        "bootstrap_ci95_over_teams": ci,
        "threshold": threshold,
        "threshold_read_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "per_opponent_detail": {t: {"n": len(by[t]),
                                    "wins": sum(1 for m in by[t] if m > 0),
                                    "margins": sorted(by[t])} for t in teams},
        "near_tie_watch": {t: sorted(m for m in by.get(t, []) if abs(m) <= NEAR_TIE)
                           for t in WATCH if t in by},
    }
    if threshold is None:
        out["verdict"] = "UNDECIDED (threshold unavailable)"
        out["alert"] = False
        return out
    out["p_above_threshold"] = sum(1 for d in draws if d > threshold) / len(draws)
    if ci[0] > threshold:
        out["verdict"] = "ABOVE the front-16 threshold (CI lower bound clears it)"
        out["alert"] = True
    elif ci[1] < threshold:
        out["verdict"] = "BELOW the front-16 threshold (CI upper bound is under it)"
        out["alert"] = True
    else:
        out["verdict"] = ("UNDECIDED: threshold inside the CI. Needs roughly %d teams; "
                          "passive games cannot be initiated, so this is a time constraint."
                          % TARGET_TEAMS)
        out["alert"] = False
    return out


def paired_bias() -> dict[str, Any]:
    """Identify the staleness bias ONLY on opponents present in both corpora.

    Active games attack the opponent's public slot; passive games face their current code.
    The unpaired difference between the two corpora confounds that with opponent
    composition, so only a paired comparison identifies it. If the overlap is empty the
    honest output is "not identifiable", never the unpaired difference.
    """
    if not (LEDGER.exists() and FIELD_SAMPLE.exists()):
        return {"identifiable": False, "reason": "need both corpora on disk"}
    passive = [r for r in json.loads(LEDGER.read_text(encoding="utf-8"))["records"]
               if is_contest(r["their_net"])]
    active = [r for r in json.loads(FIELD_SAMPLE.read_text(encoding="utf-8"))
              if r.get("kind") == "stratified" and "is_win" in r
              and is_contest(r.get("their_net"))]
    pa: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    ac: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for r in passive:
        pa[str(r["opponent"])].append((r.get("map_id"), r["our_net"] - r["their_net"], r["is_win"]))
    for r in active:
        ac[str(r["team"])].append((r.get("map_id"), r["our_net"] - r["their_net"], r["is_win"]))
    both = sorted(set(pa) & set(ac))
    if len(both) < 2:
        return {"identifiable": False, "overlap": both,
                "reason": "fewer than 2 opponents in both corpora; the unpaired difference "
                          "must NOT be substituted, it confounds opponent composition"}
    win_diff, margin_diff, detail = [], [], {}
    for t in both:
        aw = st.fmean([x[2] for x in ac[t]]); pw = st.fmean([x[2] for x in pa[t]])
        am = st.fmean([x[1] for x in ac[t]]); pm = st.fmean([x[1] for x in pa[t]])
        win_diff.append(aw - pw); margin_diff.append(am - pm)
        detail[t] = {"active_maps": sorted({x[0] for x in ac[t]}),
                     "passive_maps": sorted({x[0] for x in pa[t]}),
                     "active_win": aw, "passive_win": pw,
                     "active_margin": am, "passive_margin": pm,
                     "map_aligned": {x[0] for x in ac[t]} == {x[0] for x in pa[t]}}
    def mse(v):
        m = sum(v) / len(v)
        return m, (st.stdev(v) / math.sqrt(len(v))) if len(v) > 1 else float("nan")
    wm, ws = mse(win_diff); mm, ms = mse(margin_diff)
    return {"identifiable": True, "n_pairs": len(both), "overlap": both,
            "win_rate_bias_pp": 100 * wm, "win_rate_bias_se_pp": 100 * ws,
            "win_rate_sigma": (wm / ws) if ws and ws == ws else float("nan"),
            "margin_bias_gold": mm, "margin_bias_se_gold": ms,
            "per_team": detail,
            "note": "a bias driven by one or two teams is not a corpus property; check "
                    "per_team before correcting anything with it"}


def read_threshold() -> tuple[float | None, list[dict[str, Any]]]:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import arena  # type: ignore
        payload = arena.call("GET", "/api/user/get_rank_list_1",
                             params={"page": 1, "page_size": 20})
        rows = payload.get("list") or []
        if len(rows) < 16:
            return None, rows
        return float(rows[15].get("win_rate")), rows
    except Exception:                                     # noqa: BLE001
        return None, []


def cmd_poll(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "sim"))
    import field_sample as fsmod                          # reuses the paged fetch
    rows = fsmod.all_games()
    fresh = [to_record(r) for r in rows if keep_game(r)]
    prior = {}
    if LEDGER.exists():
        prior = {r["game_id"]: r for r in json.loads(LEDGER.read_text(encoding="utf-8"))["records"]}
    merged = dict(prior)
    added = 0
    for rec in fresh:
        if rec["game_id"] not in merged:
            merged[rec["game_id"]] = rec
            added += 1
    records = [merged[k] for k in sorted(merged)]
    threshold, _ = read_threshold()
    verdict = judge(records, threshold)
    hist = []
    if LEDGER.exists():
        try: hist = json.loads(LEDGER.read_text(encoding="utf-8")).get("threshold_history", [])
        except Exception: hist = []
    if threshold is not None:
        hist.append({"utc": dt.datetime.now(dt.timezone.utc).isoformat(), "threshold": threshold})
    payload = {"records": records, "judgement": verdict, "threshold_history": hist,
               "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "conditions": {
                   "corpus": "passive games only (user_id2 == 220), post-changeover",
                   "changeover": CHANGEOVER.isoformat(),
                   "estimand": "mean over OPPONENTS of P(win) -- the round-robin functional",
                   "threshold_note": "rank 16's 24h rolling win rate; NOT a constant",
               }}
    if not args.no_write:
        LEDGER.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("passive monitor: %d games (%d new), %d teams, %d broken excluded"
          % (verdict.get("n_games", 0), added, verdict.get("n_teams", 0),
             verdict.get("excluded_broken", 0)))
    print("  per-opponent %.1f%%  bootstrap CI over teams [%.1f, %.1f]"
          % (100 * verdict["per_opponent_rate"], 100 * verdict["bootstrap_ci95_over_teams"][0],
             100 * verdict["bootstrap_ci95_over_teams"][1]))
    if threshold is not None:
        print("  threshold %.2f%% (read %s)  P(above) = %.1f%%"
              % (100 * threshold, verdict["threshold_read_at_utc"],
                 100 * verdict["p_above_threshold"]))
    print("  era: %s (headline is the CURRENT construct only)" % verdict.get("current_era"))
    for nm, es in (verdict.get("by_construct_era") or {}).items():
        po = es["per_opponent"]
        print("    %-22s games %3d teams %2d per-opponent %s"
              % (nm, es["games"], es["teams"], ("%.1f%%" % (100 * po)) if po is not None else "n/a"))
    if len(hist) > 1:
        print("  threshold history: %s -> %s (%+.2fpp)"
              % (", ".join("%.4f" % h["threshold"] for h in hist[-4:]),
                 hist[-1]["utc"][:16], 100 * (hist[-1]["threshold"] - hist[0]["threshold"])))
    print("  VERDICT: %s" % verdict["verdict"])
    for team, near in (verdict.get("near_tie_watch") or {}).items():
        if near:
            print("  near-tie vs %s: %s" % (team, near))
    if verdict.get("alert"):
        print("  *** ALERT: the threshold comparison has become decidable ***")
    return 0


def cmd_bias(_args: argparse.Namespace) -> int:
    out = paired_bias()
    print(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


def validate() -> int:
    failures = 0
    print("== dry run: game filter ==")
    base = {"user_id": 47, "user_id2": OUR_USER_ID, "error_msg": "",
            "created_at": "2026-08-10T12:00:00.000Z",
            "players": [{"model_name": "x"}, {"model_name": OUR_SLOT_NAME}]}
    cases = [
        (dict(base), True, "passive, post-changeover, valid"),
        (dict(base, user_id2=999), False, "we were not the challenged party"),
        (dict(base, user_id=OUR_USER_ID), False, "self-play"),
        (dict(base, user_id=3), False, "organiser test account"),
        (dict(base, error_msg="boom"), False, "errored game"),
        (dict(base, created_at="2026-08-10T07:00:00.000Z"), False, "PRE-changeover: different player"),
        (dict(base, players=[{"model_name": "x"}, {"model_name": "y"}]), False, "our slot absent"),
    ]
    for row, want, why in cases:
        got = keep_game(row)
        ok = got == want
        failures += not ok
        print("  [%s] keep=%-5s want=%-5s  %s" % ("PASS" if ok else "FAIL", got, want, why))
    print("== dry run: broken-slot filter ==")
    for net, want, why in ((100, True, "normal"), (0, True, "zero is a legitimate score"),
                           (-1, False, "negative net = broken slot"), (None, True, "unknown")):
        got = is_contest(net)
        ok = got == want
        failures += not ok
        print("  [%s] their_net=%-5s contest=%-5s want=%-5s  %s"
              % ("PASS" if ok else "FAIL", net, got, want, why))
    print("== dry run: the judgement, three ways ==")
    def rec(op, margin, gid):
        return {"opponent": op, "our_net": 1000 + margin, "their_net": 1000,
                "is_win": 1 if margin > 0 else 0, "game_id": gid, "map_id": 1}
    scen = [
        ("all teams beaten -> ABOVE",
         [rec("t%d" % i, +500, i) for i in range(12)], 0.6756, "ABOVE"),
        ("all teams lost -> BELOW",
         [rec("t%d" % i, -500, i) for i in range(12)], 0.6756, "BELOW"),
        ("half and half at n=10 -> UNDECIDED",
         [rec("t%d" % i, +500 if i % 2 else -500, i) for i in range(10)], 0.6756, "UNDECIDED"),
    ]
    for label, recs, thr, expect in scen:
        v = judge(recs, thr)
        got = v["verdict"].split()[0].rstrip(":")
        ok = got == expect
        failures += not ok
        print("  [%s] %-38s -> %s (CI [%.2f, %.2f])"
              % ("PASS" if ok else "FAIL", label, got,
                 v["bootstrap_ci95_over_teams"][0], v["bootstrap_ci95_over_teams"][1]))
    print("== dry run: per-opponent vs per-game weighting must differ when unbalanced ==")
    unbal = [rec("heavy", -100, i) for i in range(6)] + [rec("t%d" % i, +100, 100 + i) for i in range(3)]
    v = judge(unbal, 0.6756)
    ok = abs(v["per_game_rate"] - 3 / 9) < 1e-9 and abs(v["per_opponent_rate"] - 3 / 4) < 1e-9
    failures += not ok
    print("  [%s] per-game %.3f (expect 0.333) vs per-opponent %.3f (expect 0.750)"
          % ("PASS" if ok else "FAIL", v["per_game_rate"], v["per_opponent_rate"]))
    print("RESULT: %s" % ("PASS" if failures == 0 else "FAIL (%d)" % failures))
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    p = sub.add_parser("poll")
    p.add_argument("--no-write", action="store_true")
    sub.add_parser("bias")
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        return validate()
    if args.cmd == "bias":
        return cmd_bias(args)
    return cmd_poll(args)


if __name__ == "__main__":
    raise SystemExit(main())
