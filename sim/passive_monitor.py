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
#:
#: 2026-08-13: the above was only HALF structural, and it failed again. Making eras a list
#: made a missed publish *visible* (as a suspiciously long era) but not *impossible*, because
#: the list is still hand-maintained -- so it remained a reminder to update a date, which is
#: precisely what the comment above claims it is not. The 08-13T07:56:21Z publish was never
#: appended, so 144 decoy games and 45 games of the new construct pooled into one 26.2%
#: figure and the monitor emitted a confident BELOW verdict. Same bug, same shape, four days
#: apart. The genuinely structural fix is `live_boundary()`: read the boundary from the
#: platform instead of from this list, so forgetting to append cannot change the answer.
CONSTRUCT_ERAS = [
    ("fd47ea6-reflex", dt.datetime(2026, 8, 10, 8, 20, 18, tzinfo=dt.timezone.utc)),
    ("relaunch-2026-08-12", dt.datetime(2026, 8, 12, 1, 37, 41, tzinfo=dt.timezone.utc)),
    ("slot-2026-08-12-1527", dt.datetime(2026, 8, 12, 15, 27, 40, tzinfo=dt.timezone.utc)),
    ("idle-decoy-2026-08-12", dt.datetime(2026, 8, 12, 16, 31, 0, tzinfo=dt.timezone.utc)),
    ("slot-2026-08-13-0756", dt.datetime(2026, 8, 13, 7, 56, 21, tzinfo=dt.timezone.utc)),
]

#: An era whose MEDIAN own-score falls below this is treated as not-our-real-construct.
#: Measured separation is total at era granularity: the idle.so decoy era has median 0 while
#: every real-construct era sits at 1951-2015, and at game granularity 92% of decoy games score
#: under 100 against 0-1% of real ones. 300 sits in an empty gap, not on a boundary.
DECOY_MEDIAN_SCORE_BELOW = 300

#: Retained ONLY as an audit trail of decoy windows already observed. It is NOT the decoy test;
#: `era_looks_like_decoy` is. The owner published idle.so as an anti-leak decoy: "公测没有意义,
#: 我不想把目前最强的放在公开位上". A decoy's win rate is a true reading of a meaningless quantity,
#: and the prelim has its own submission channel, so the public slot carries no standings
#: information either way.
DECOY_ERAS = {"idle-decoy-2026-08-12"}


def live_boundary():
    """The newest era boundary AND the slot's artifact identity, read from the platform.

    Returns (label, datetime, model_id) or None if unreachable. This exists because the era
    list is hand-maintained and has now been stale twice; reading it live means a forgotten
    append cannot silently mix two constructs into one pooled figure.

    ⚠️ RESIDUAL, deliberately not fixed here: this returns only the NEWEST boundary. If TWO
    publishes are missed, the middle one is still absent and the two constructs either side of
    it still pool. The only backstop for that is the 08-12 visibility property -- a missing
    entry shows up as an implausibly long era -- which is why CONSTRUCT_ERAS must be kept as an
    append log even though the boundary is now read live. Do not delete old entries.
    """
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import arena  # noqa: PLC0415
        for page in range(1, 14):
            payload = arena.call("GET", "/api/user/get_model_list_4",
                                 params={"page": page, "page_size": 50})
            entries = payload.get("list") or []
            if not entries:
                return None
            for entry in entries:
                if int(entry.get("user_id") or 0) == 220:
                    raw = str(entry.get("updated_at") or "")
                    if not raw:
                        return None
                    stamp = _ts(raw)
                    return (f"live-{stamp:%Y-%m-%d-%H%M}", stamp, entry.get("id"))
    except Exception:
        return None
    return None


def sync_eras() -> dict[str, Any]:
    """Append the live boundary if this file does not already know about it.

    FAIL-CLOSED. Returns a status dict the caller MUST consult:
      {"ok": bool, "appended": str|None, "reason": str|None}

    If the platform cannot be read we do NOT proceed on the hard-coded list as though nothing
    happened -- that reproduces exactly the exposure this function exists to close (stale list
    -> confident verdict). The caller degrades the verdict to UNDECIDED and suppresses the
    headline instead. A stderr warning is not enough: under cron, stderr is where alerts go to
    die, which is why the caller prints the reason to stdout next to the VERDICT line.
    """
    live = live_boundary()
    if live is None:
        return {"ok": False, "appended": None,
                "reason": "could not read the public slot; era boundary may be stale, so any "
                          "era-split figure below could be pooling two constructs"}
    label, stamp, model_id = live
    newest = CONSTRUCT_ERAS[-1][1]
    if stamp > newest + dt.timedelta(seconds=60):
        CONSTRUCT_ERAS.append((label, stamp))
        print(f"  *** ERA LIST WAS STALE: appended {label} ({stamp:%Y-%m-%dT%H:%M:%SZ}) "
              f"from the live slot; the hard-coded newest was {newest:%Y-%m-%dT%H:%M:%SZ} ***")
        return {"ok": True, "appended": label, "reason": None, "model_id": model_id}
    return {"ok": True, "appended": None, "reason": None, "model_id": model_id}


def era_looks_like_decoy(rows: Sequence[Mapping[str, Any]]) -> bool:
    """Does this era's own scoring look like something that is NOT our real construct?

    A PREDICATE on observed behaviour, not a lookup in a name list. The first version of this
    guard kept a hand-maintained set of era LABELS, which was wrong twice over: it repeated the
    "list as truth" mistake this module warns about elsewhere, and it was inversely coupled to
    the live boundary read -- `sync_eras()` mints labels like `live-YYYY-MM-DD-HHMM`, which can
    never match a hand-written label, so the more reliable the live read got, the more certainly
    a NEW decoy publish would escape classification and be reported as a standings verdict.

    Artifact identity would be the ideal discriminator, but `get_model_list_4` exposes only the
    upsert slot id (278135, constant across publishes) and `updated_at`, so it cannot separate a
    decoy from a real construct. Behaviour can: idle.so does nothing and scores 0.
    """
    scores = [int(r.get("our_net") or 0) for r in rows if r.get("our_net") is not None]
    if len(scores) < 5:
        return False
    return st.median(scores) < DECOY_MEDIAN_SCORE_BELOW


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
#: A margin this small means the game could plausibly have gone the other way.
#: ⚠️ MUST track the chaos floor, not a round number. `src/CHANGELOG.md:5166` measures the
#: value-neutral per-game sd at ~245 gold with a symmetric sign, so the old 150 was NARROWER
#: THAN THE COIN: a watcher built for "this could have been the other result" had a bandwidth
#: below one sd, so a -299 game -- indistinguishable from noise -- was booked as a clean defeat
#: and never surfaced. That is exactly what hid the only loss in the current era.
#: If anyone re-measures the floor, this constant must move with it; the two are a pair.
CHAOS_FLOOR_SD = 245          # src/CHANGELOG.md:5166
#: ⭐ Set to 2 sd, not 1, because 1 sd DOES NOT CATCH THE CASE THAT MOTIVATED THE WIDENING:
#: the only loss in the current era is -299, which is 1.22 sd, so a 245 bandwidth still books it
#: as a clean defeat. Two-sided p for 1.22 sd is about 0.22 -- comfortably consistent with a coin
#: flip -- so the envelope that makes this watcher do its job is 2 sd. Checking that a proposed
#: fix catches the case that prompted it is the cheap step that would otherwise be skipped.
NEAR_TIE = 2 * CHAOS_FLOOR_SD
#: opponents that have ever beaten us in the passive condition
WATCH = ("1", "rikka", "君の仿瓷")
BOOTSTRAP = 20000
SEED = 5
#: n at which the CI is expected to narrow enough to decide; see the handoff note
TARGET_TEAMS = 25


#: Known broken slots, consolidated so a reader gets the whole picture at once. A negative
#: final net means the slot spent more on vision than it ever collected.
#:
#: ⚠️ BOTH consequences FLATTER US, which is why the filter is not optional:
#:   * win rate -- we "beat" an opponent that never competed, so our rate reads TOO HIGH,
#:     not too low. Excluding these moved one estimate from above the front-16 threshold to
#:     below it, i.e. the flattery was large enough to change a strategic conclusion.
#:   * latency -- the malfunction makes them SLOWER (P50 4400-5600ns, buying vision every
#:     round), so their apparent speed reads TOO SLOW and we look relatively faster than we
#:     are. A "this opponent is fast" reading can never be contaminated downward by a broken
#:     slot, but a "we are the fastest" reading can.
#: So the failure mode is always over-optimism about us, on both axes at once.
KNOWN_BROKEN_SLOTS = {
    "QuantLK":     "13 games, gid 190039-228126, net -22 to -815; broken for 2+ days running",
    "Tundra-wawa": "2 games, gid 191692/192807, net -1496/-1491, gold 4/9 vs vision 1500",
    "D12":         "1 game, gid 192902, net -721",
    "hhh":         "1 game, gid 192912, net -64",
    "DeepAlpha":   "2 games, gid 219141/219142 (08-11, map3), net -1500/-1498, full vision spend; "
                   "found by applying is_contest to a batch, NOT by matching this list",
}
#: ⚠️ Malfunctions are INTERMITTENT, so exclude GAMES and never whole TEAMS. Tundra-wawa is the
#: proof: broken on 08-10 (gid 191692) and 08-12 (gid 192807) but HEALTHY on 08-11, where it beat
#: us twice for real (gid 219176/219177, its net 1755/1786). Dropping the team would have thrown
#: away two genuine defeats and flattered us -- the same direction as counting the broken games.
#: Corollary: this table is a convenience, not the filter. Always run is_contest over the batch;
#: DeepAlpha was found that way and was absent from every list at the time.


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
                             "looks_like_decoy": era_looks_like_decoy(rows),
                             "median_own_score": (st.median([int(r.get("our_net") or 0) for r in rows])
                                                  if rows else None),
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
        "current_era_looks_like_decoy": bool(
            (era_summary.get(cur) or {}).get("looks_like_decoy")),
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
        "loss_margins": sorted(m for v in by.values() for m in v if m <= 0),
        "losses_within_1sd": sum(1 for v in by.values() for m in v if m <= 0
                                 and abs(m) <= CHAOS_FLOOR_SD),
        "losses_within_2sd": sum(1 for v in by.values() for m in v if m <= 0
                                 and abs(m) <= 2 * CHAOS_FLOOR_SD),
        "losses_total": sum(1 for v in by.values() for m in v if m <= 0),
        "near_tie_watch": {t: sorted(m for m in by.get(t, []) if abs(m) <= NEAR_TIE)
                           for t in WATCH if t in by},
    }
    if threshold is None:
        out["verdict"] = "UNDECIDED (threshold unavailable)"
        out["alert"] = False
        return out
    out["p_above_threshold"] = sum(1 for d in draws if d > threshold) / len(draws)
    if out["current_era_looks_like_decoy"]:
        # A decoy's win rate is a TRUE reading of a MEANINGLESS quantity. Emitting ABOVE/BELOW
        # here would invite a response to a deliberate configuration.
        out["verdict"] = ("SUPPRESSED: the current era does not look like our real construct "
                          "(median own score %s < %d). Its win rate measures whatever is on the "
                          "public slot, not us, and the prelim has a separate submission channel, "
                          "so it carries no standings information. Read the newest non-decoy era."
                          % ((era_summary.get(cur) or {}).get("median_own_score"),
                             DECOY_MEDIAN_SCORE_BELOW))
        out["alert"] = False
        return out
    if ci[0] > threshold:
        out["verdict"] = "ABOVE the front-16 threshold (CI lower bound clears it)"
        out["alert"] = True
    elif ci[1] < threshold:
        out["verdict"] = "BELOW the front-16 threshold (CI upper bound is under it)"
        out["alert"] = True
    else:
        need = teams_needed(rates, threshold)
        step = 100.0 / max(1, len(rates))
        out["teams_needed"] = need
        out["grid_step_pp"] = step
        out["verdict"] = (
            "UNDECIDED: threshold inside the CI. %s "
            "Grid step is %.2fpp (one team = 1/%d), and the CI lower bound is %.2fpp from the "
            "threshold -- so this can be a RESOLUTION statement rather than a standings one. "
            "Passive games cannot be initiated, so it is a time constraint, not a quota one."
            % (("Continuing at the observed win rate, the lower bound clears at about %d teams."
                % need) if need else "No attainable team count clears it at the observed rate.",
               step, len(rates), 100.0 * (threshold - ci[0])))
        out["alert"] = False
    return out


def teams_needed(rates: Sequence[float], threshold: float, cap: int = 60) -> int | None:
    """Smallest team count whose bootstrap lower bound clears the threshold, at the OBSERVED rate.

    Replaces a hard-coded TARGET_TEAMS = 25, which was a guess and overstated the wait by about
    2x: at the observed 11-of-12 the lower bound clears at 13 teams, not 25. Twenty-five is only
    required if the losing share doubles.

    ⚠️ This is a CONDITIONAL projection, not a forecast. It assumes the observed win/loss ratio
    continues AND that the threshold holds still -- and the threshold moved 75.21 -> 75.08 within
    eight minutes, so any threshold above the returned point's lower bound invalidates it.
    """
    if not rates:
        return None
    won = sum(1 for r in rates if r > 0.5)
    share = won / len(rates)
    rng = random.Random(SEED)
    for n in range(len(rates) + 1, cap + 1):
        k = round(share * n)
        synth = [1.0] * k + [0.0] * (n - k)
        draws = sorted(sum(rng.choice(synth) for _ in synth) / n for _ in range(BOOTSTRAP))
        if draws[int(0.025 * len(draws))] > threshold:
            return n
    return None


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


#: Append-only, de-timed, TRACKED summary. `sim/reports/` is gitignored, so the threshold
#: series -- which we have already ruled must always carry its read timestamp because it is a
#: moving target -- otherwise lives only in an uncommitted file, and `logs/` has been purged
#: twice already. Timing fields are excluded so that a dirty diff means a finding changed.
SERIES = ROOT / "sim" / "passive_series.jsonl"


def append_series(verdict: dict[str, Any], threshold: float | None, read_at: str) -> None:
    row = {
        "utc": read_at,
        "threshold": threshold,
        "era": verdict.get("current_era"),
        "n_teams": verdict.get("n_teams"),
        "n_games": verdict.get("n_games"),
        "per_opponent": verdict.get("per_opponent_rate"),
        "ci95": verdict.get("bootstrap_ci95_over_teams"),
        "verdict": (verdict.get("verdict") or "").split(":")[0],
        "looks_like_decoy": verdict.get("current_era_looks_like_decoy"),
    }
    prior = []
    if SERIES.exists():
        prior = [ln for ln in SERIES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # append only when a FINDING changed; identical consecutive rows would train the reader
    # to ignore the file, which is the noise-fatigue failure filed on 08-12.
    key = {k: v for k, v in row.items() if k != "utc"}
    if prior:
        last = json.loads(prior[-1])
        if {k: v for k, v in last.items() if k != "utc"} == key:
            return
    with open(SERIES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def cmd_poll(args: argparse.Namespace) -> int:
    # Read the era boundary from the platform BEFORE splitting anything. A publish that never
    # got appended to CONSTRUCT_ERAS silently pools two constructs, which produced a confident
    # and wrong BELOW verdict on 08-13.
    era_status = sync_eras()
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
    # Losses inside the chaos floor: a per-opponent rate is binary, so it books a -299 and a
    # -1400 as the same event and throws away the difference between "lost inside the noise"
    # and "was actually beaten". At n=12 the grid step is 8.33pp, so one coin-flip game can
    # move the verdict a whole notch.
    lt, lw = verdict.get("losses_total"), verdict.get("losses_within_1sd")
    if lt:
        l2 = verdict.get("losses_within_2sd")
        print("  losses %d: %d inside 1 sd (+-%d), %d inside 2 sd (+-%d) of the chaos floor: %s"
              % (lt, lw, CHAOS_FLOOR_SD, l2, 2 * CHAOS_FLOOR_SD, verdict.get("loss_margins")))
        if l2 == lt:
            print("     ⇒ EVERY loss in this era is indistinguishable from a coin flip, so the")
            print("       verdict is being held by noise, not by evidence of weakness.")
    append_series(verdict, threshold, verdict.get("threshold_read_at_utc") or "")
    print("  VERDICT: %s" % verdict["verdict"])
    # FAIL-CLOSED, printed to stdout next to the verdict: under cron, stderr is where alerts go
    # to die, and proceeding on a stale era list is the exposure this whole change exists to close.
    if not era_status.get("ok"):
        print("  ⛔ BOUNDARY UNVERIFIED -- treat the verdict as UNDECIDED regardless of the text")
        print("     above: %s" % era_status.get("reason"))
    if verdict.get("current_era_looks_like_decoy"):
        real = [(nm, es) for nm, es in (verdict.get("by_construct_era") or {}).items()
                if not es.get("looks_like_decoy") and es.get("per_opponent") is not None]
        if real:
            nm, es = real[-1]
            print("     newest era that DOES look like our real construct: %s -> per-opponent "
                  "%.1f%% (%d teams, %d games)"
                  % (nm, 100 * es["per_opponent"], es["teams"], es["games"]))
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
