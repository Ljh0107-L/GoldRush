#!/usr/bin/env python3
"""Field-wide win-rate sampling: pre-registered roster, submission, archiving.

The quantity being estimated is our win rate against the WHOLE field, because
the preliminary is an engine-run full round-robin ranked by win rate
(``docs/赛制介绍.md:33-39``). We have never sampled it: the frozen lineage's 36
external games were played only against the two strongest teams, and the
leaderboard figure is a 24-hour rolling window in which 91% of our external
games were against those same two opponents.

Why stratified, and why plus-or-minus five points is unreachable
---------------------------------------------------------------
The estimand is a mean over TEAMS, not a single proportion, so its error is
dominated by between-team spread rather than by game count. The challengeable
teams' own win rates have sd about 0.189, which gives:

    +-5pp  -> 56 teams of the 88 available   (nearly the whole field)
    +-7pp  -> 29 teams
    +-10pp -> 14 teams

So more games per opponent cannot buy precision on the field mean; only more
opponents can. Stratifying by board win rate collapses the within-stratum
spread and is therefore mandatory, not a refinement. It also names the error we
made historically: sampling only the strongest teams picked the highest-variance
and least representative stratum.

Integrity rules enforced by this tool
-------------------------------------
* The roster is drawn ONCE and written to disk before any game is submitted.
  ``submit`` refuses to run unless the roster file already exists, and never
  re-draws. This removes "pick opponents after seeing results" as a possibility.
* Selection within a stratum is systematic on sorted ``user_id`` -- deterministic
  and independent of strength within the stratum, so it cannot be steered.
* The purposive Ausdroid games are tagged ``purposive`` and are EXCLUDED from the
  field estimate; they are a decision benchmark, not a sample.
* No self-play. A previous window spent 231 of 488 games on self-play, some of
  it deliberately-losing probes, which is what corrupted the ladder reading.

Usage
-----
    field_sample.py roster                 # draw and freeze the roster (once)
    field_sample.py show                    # print the frozen roster
    field_sample.py reduce                  # cut to one game per team (roster untouched)
    field_sample.py submit --confirm        # submit pending games (quota-aware)
    field_sample.py collect                 # fetch logs + build the results table
    field_sample.py estimate                # stratified estimate + the pre-registered question
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

ROSTER = ROOT / "sim" / "reports" / "field_sample_roster.json"
RESULTS = ROOT / "sim" / "reports" / "field_sample.json"

US_USER_ID = 220
US_TEAM = "0x8F"
# Organiser test accounts (user_id 2/3/4, named 测试用户N). They hold public models and
# appear on the board, but they are not competitors and will not be in the preliminary
# round-robin, so including them would bias the low strata. Excluded from the population.
EXCLUDED_USER_IDS = frozenset({2, 3, 4})
MAPS = (1, 2, 3)
# Stratum edges on board win_rate, chosen at natural breaks in the distribution
# (min .189 / q1 .333 / median .436 / q3 .611 / max .967).
STRATA = (("S1", 0.00, 0.45, 12), ("S2", 0.45, 0.65, 8),
          ("S3", 0.65, 0.80, 5), ("S4", 0.80, 1.01, 5))
PURPOSIVE_MATCH = "usdroid"          # rank-16 qualification-line benchmark
PURPOSIVE_PER_MAP = 2
DAILY_QUOTA = 500


def api(path: str, **params):
    import arena
    return arena.call("GET", path, params=params or None)


def board() -> list[dict]:
    return api("/api/user/get_rank_list_1").get("list", [])


def public_models() -> list[dict]:
    return api("/api/user/get_model_list_4").get("list", [])


def all_games() -> list[dict]:
    import arena
    rows, page = [], 1
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


def window_start() -> datetime.datetime:
    """Beijing-midnight boundary = UTC 16:00 of the previous day."""
    now = datetime.datetime.now(datetime.timezone.utc)
    boundary = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < boundary:
        boundary -= datetime.timedelta(days=1)
    return boundary


def quota_state() -> tuple[int, int]:
    """Authoritative quota from the platform, not inferred from the game list.

    ``get_user_info`` reports ``today_initiated`` and ``daily_initiate_limit``
    directly. Counting rows in the game list OVERCOUNTS, because games where other
    teams challenge us do not consume quota (``docs/FAQ.md:366``) yet still appear
    in the list. Measured on 2026-08-10: 498 rows in the window against 470
    initiated, the difference being exactly the passive challenges.
    """
    info = api("/api/user/get_user_info")
    return (int(info.get("today_initiated") or 0),
            int(info.get("daily_initiate_limit") or DAILY_QUOTA))


def cmd_roster(args: argparse.Namespace) -> int:
    if ROSTER.exists() and not args.force:
        print("roster already frozen at %s — refusing to re-draw.\n"
              "Re-drawing after seeing results would invalidate the design; use --force only\n"
              "if no game has been submitted yet." % ROSTER.relative_to(ROOT), file=sys.stderr)
        return 2
    by_user = {int(m["user_id"]): m for m in public_models()}
    pool = []
    for row in board():
        uid = int(row.get("user_id", -1))
        if uid == US_USER_ID or uid in EXCLUDED_USER_IDS or uid not in by_user:
            continue
        pool.append({"user_id": uid, "team": row.get("user_name_cn"),
                     "board_win_rate": float(row["win_rate"]),
                     "model_id": int(by_user[uid]["id"])})
    entries, summary = [], []
    for name, lo, hi, pick in STRATA:
        members = sorted((p for p in pool if lo <= p["board_win_rate"] < hi),
                         key=lambda p: p["user_id"])          # deterministic order
        take = min(pick, len(members))
        # Systematic sampling: even spacing over the sorted list, so selection is
        # independent of strength within the stratum and cannot be steered.
        step = len(members) / take if take else 0
        chosen = [members[min(len(members) - 1, int(i * step))] for i in range(take)]
        seen, dedup = set(), []
        for c in chosen:                                       # guard against collisions
            if c["user_id"] in seen:
                for m in members:
                    if m["user_id"] not in seen:
                        c = m
                        break
            seen.add(c["user_id"])
            dedup.append(c)
        for opponent in dedup:
            for map_id in MAPS:
                entries.append({"kind": "stratified", "stratum": name,
                                "map_id": map_id, **opponent})
        summary.append({"stratum": name, "range": [lo, hi], "population": len(members),
                        "sampled": len(dedup),
                        "weight": len(members) / max(1, len(pool))})
    # Purposive benchmark: the team sitting on the qualification line.
    target = [p for p in pool if PURPOSIVE_MATCH in str(p["team"]).lower()]
    for opponent in target[:1]:
        for map_id in MAPS:
            for _ in range(PURPOSIVE_PER_MAP):
                entries.append({"kind": "purposive", "stratum": "BENCH",
                                "map_id": map_id, **opponent})
    payload = {
        "frozen_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "construct": "fd47ea6",
        "estimand": "our win rate against the whole field (preliminary is a full round-robin)",
        "population_size": len(pool),
        "strata": summary,
        "purposive_note": "EXCLUDED from the field estimate; decision benchmark only",
        "games_planned": len(entries),
        "entries": entries,
    }
    ROSTER.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("froze roster: %d games over %d opponents -> %s"
          % (len(entries), len({e["user_id"] for e in entries}), ROSTER.relative_to(ROOT)))
    for s in summary:
        print("  %-4s range[%.2f,%.2f) population %2d  sampled %2d  weight %.2f"
              % (s["stratum"], s["range"][0], s["range"][1], s["population"],
                 s["sampled"], s["weight"]))
    bench = [e for e in entries if e["kind"] == "purposive"]
    print("  BENCH  %s x%d games (not pooled into the estimate)"
          % (bench[0]["team"] if bench else "-", len(bench)))
    return 0


def load_roster() -> dict:
    if not ROSTER.exists():
        print("no frozen roster; run `field_sample.py roster` first", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(ROSTER.read_text(encoding="utf-8"))


def cmd_show(_args: argparse.Namespace) -> int:
    data = load_roster()
    print("frozen_at %s   construct %s   planned %d games"
          % (data["frozen_at_utc"], data["construct"], data["games_planned"]))
    per = collections.Counter((e["stratum"], e["team"]) for e in data["entries"])
    for (stratum, team), n in sorted(per.items()):
        print("  %-6s %-26s %d games" % (stratum, team, n))
    return 0


PLAN30 = ROOT / "sim" / "reports" / "field_sample_plan30.json"


def cmd_reduce(args: argparse.Namespace) -> int:
    """Cut the plan to one game per team without touching the frozen roster.

    Why: precision here is dominated by BETWEEN-team spread, so a second and third
    game against the same team buys almost nothing, while a second team buys a lot.
    The frozen roster is 30 teams x 3 maps; this keeps all 30 teams and one map each.
    Binomial SE at n=30 is about 9pp, and the batch only has to answer one binary
    question -- are we above or below roughly 55% against the field -- so the extra
    4pp that 90 games would buy changes no action.

    The map is assigned by ROTATION over the roster's own team order, not chosen:
    team index i takes map (i mod 3) + 1. That keeps all three maps represented
    10/10/10, avoids binding any team to any map as a systematic bias, and is fully
    determined by the frozen file, so no outcome can influence the selection. Nothing
    has been played yet, so this cannot be cherry-picking either way.
    """
    data = load_roster()
    strat = [e for e in data["entries"] if e["kind"] == "stratified"]
    bench = [e for e in data["entries"] if e["kind"] == "purposive"]
    teams: dict[int, list[dict]] = collections.OrderedDict()
    for e in sorted(strat, key=lambda x: (x["stratum"], int(x["user_id"]), int(x["map_id"]))):
        teams.setdefault(int(e["user_id"]), []).append(e)
    chosen = []
    for i, (_uid, rows) in enumerate(teams.items()):
        want = (i % 3) + 1
        pick = next((r for r in rows if int(r["map_id"]) == want), rows[0])
        chosen.append(pick)
    kept_bench = sorted(bench, key=lambda x: int(x["map_id"]))[:args.bench]
    plan = {
        "derived_from_roster_frozen_at": data["frozen_at_utc"],
        "construct": data["construct"],
        "reduced_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "rule": ("keep all teams, one game each; map = (team_index mod 3) + 1 over the "
                 "roster's own ordering; bench truncated to the first N by map_id"),
        "rationale": ("precision is set by between-team spread, and the decision is the "
                      "binary 'are we above or below ~55% against the field', which +-9pp "
                      "at n=30 already answers"),
        "strata": data["strata"],
        "entries": chosen + kept_bench,
    }
    PLAN30.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bymap = collections.Counter(int(e["map_id"]) for e in chosen)
    bystrat = collections.Counter(e["stratum"] for e in chosen)
    print("wrote %s" % PLAN30.relative_to(ROOT))
    print("  stratified: %d games over %d teams (one each)" % (len(chosen), len(teams)))
    print("  map split : %s" % dict(sorted(bymap.items())))
    print("  strata    : %s" % dict(sorted(bystrat.items())))
    print("  bench     : %d (purposive, not pooled)" % len(kept_bench))
    print("  roster file itself is UNCHANGED: %s" % ROSTER.relative_to(ROOT))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    import arena
    if PLAN30.exists():
        data = json.loads(PLAN30.read_text(encoding="utf-8"))
        print("PLAN: reduced one-game-per-team plan (%d entries) from %s"
              % (len(data["entries"]), PLAN30.name))
    else:
        data = load_roster()
        print("PLAN: full frozen roster (%d entries)" % len(data["entries"]))
    done = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else []
    submitted = collections.Counter((d["user_id"], d["map_id"], d["kind"]) for d in done)
    pending = []
    for entry in data["entries"]:
        key = (entry["user_id"], entry["map_id"], entry["kind"])
        want = sum(1 for e in data["entries"]
                   if (e["user_id"], e["map_id"], e["kind"]) == key)
        if submitted[key] < want:
            submitted[key] += 1
            pending.append(entry)
    if not pending:
        print("nothing pending; roster fully submitted")
        return 0
    used, limit = quota_state()
    remaining = limit - used
    print("quota (platform-reported today_initiated): used %d / %d -> remaining %d"
          % (used, limit, remaining))
    print("pending in roster: %d" % len(pending))
    batch = pending[:min(len(pending), max(0, remaining - args.reserve))]
    if not batch:
        print("no headroom after reserving %d; wait for the window reset (UTC 16:00)"
              % args.reserve)
        return 1
    if not args.confirm:
        print("DRY RUN — would submit %d games. Re-run with --confirm." % len(batch))
        for e in batch[:5]:
            print("   %-6s %-24s map%d" % (e["stratum"], e["team"], e["map_id"]))
        return 0
    so = Path(args.so)
    if not so.is_file():
        print("missing --so artifact %s" % so, file=sys.stderr)
        return 2
    for i, entry in enumerate(batch):
        name = "fs%s%02d%d" % (entry["stratum"][-1] if entry["kind"] == "stratified" else "B",
                               i, entry["map_id"])
        try:
            arena.cmd_submit(argparse.Namespace(
                map=str(entry["map_id"]), vs=str(entry["model_id"]),
                code=["%s:%s" % (so, name)], dry_run=False))
        except Exception as exc:                                # noqa: BLE001
            print("  submit failed for %s map%d: %s" % (entry["team"], entry["map_id"], exc))
            continue
        done.append({**entry, "model_name": name,
                     "submitted_at_utc": datetime.datetime.now(
                         datetime.timezone.utc).isoformat()})
        RESULTS.write_text(json.dumps(done, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("submitted %d; ledger -> %s" % (len(batch), RESULTS.relative_to(ROOT)))
    return 0


def per_game_mechanics(game_id: int, our_model: str) -> dict:
    """Download the log and derive f, burn and the gross/burn/mean split for both sides.

    Net score alone is misleading here: the other line showed an action-order
    advantage can mask the mechanism. So every game records the first-mover share
    and both sides' burn, and the report gives the three-way split
    ``mean = hit x yield - burn`` under a stated order condition.
    """
    import arena
    path = ROOT / "logs" / ("game_%d.log" % game_id)
    if not path.exists():
        try:
            text = arena.call("GET", "/api/user/get_game_log",
                              params={"id": game_id}, raw=True)   # NOTE: 'id', not 'game_id'
            if not text or len(text) < 1000:
                return {}
            path.write_text(text, encoding="utf-8")
        except Exception:                                          # noqa: BLE001
            return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3:
        return {}
    header = json.loads(lines[0])
    our_pid = 1 if str(header.get("player1")) == our_model else 2
    prev: dict[int, list[int]] = {}
    delta = {our_pid: [], 3 - our_pid: []}
    firsts = []
    cost = {our_pid: [], 3 - our_pid: []}   # per-round latency, for P5 (opponent speed)
    for line in lines[2:]:
        if not line.strip():
            continue
        rec = json.loads(line)
        if "end" not in rec:
            prev = {}
            continue
        order = rec["end"].get("dispatch_order")
        if order and int(rec["round"]) >= 4:
            firsts.append(1 if int(order[0]) == our_pid else 0)
        for entry in rec["end"]["players"]:
            pid = int(entry["id"])
            if int(rec["round"]) >= 4 and pid in cost:
                cost[pid].append(int(entry.get("cost") or 0))   # end-phase only: start is stale
            cur = [int(u["gold"]) for u in entry["units"]]
            if pid in prev and len(prev[pid]) == len(cur):
                delta[pid].extend(n - w for n, w in zip(cur, prev[pid]))
            prev[pid] = cur
    def split(values):
        if not values:
            return {}
        gains = [v for v in values if v > 0]
        hit = len(gains) / len(values)
        yld = statistics.fmean(gains) if gains else 0.0
        mean = statistics.fmean(values)
        return {"unit_rounds": len(values), "hit": hit, "yield_per_hit": yld,
                "mean": mean, "gross": hit * yld, "burn": hit * yld - mean}
    def p50(v):
        return sorted(v)[len(v) // 2] if v else None
    return {"f": statistics.fmean(firsts) if firsts else None,
            "our_p50_ns": p50(cost[our_pid]), "their_p50_ns": p50(cost[3 - our_pid]),
            "ours": split(delta[our_pid]), "theirs": split(delta[3 - our_pid])}


def cmd_collect(_args: argparse.Namespace) -> int:
    """Match submitted model names back to platform games and record outcomes."""
    if not RESULTS.exists():
        print("no submissions recorded yet", file=sys.stderr)
        return 2
    done = json.loads(RESULTS.read_text(encoding="utf-8"))
    wanted = {d.get("model_name"): d for d in done if d.get("model_name")}
    for row in all_games():
        mine = [p for p in row.get("players", []) if p.get("model_name") in wanted]
        if not mine:
            continue
        rec = wanted[mine[0]["model_name"]]
        theirs = [p for p in row.get("players", []) if p is not mine[0]]
        rec.update({"game_id": row.get("id"), "is_win": int(mine[0].get("is_win") or 0),
                    "our_net": int(mine[0].get("coin_num") or 0),
                    "their_net": int(theirs[0].get("coin_num") or 0) if theirs else None,
                    "error_msg": row.get("error_msg") or ""})
        mech = per_game_mechanics(int(row["id"]), str(rec["model_name"]))
        if mech:
            rec["mechanics"] = mech
    RESULTS.write_text(json.dumps(done, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    resolved = sum(1 for d in done if "is_win" in d)
    print("resolved %d / %d submitted games" % (resolved, len(done)))
    return 0


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def crit_r(n: int) -> float:
    """Smallest |r| that is significant at two-tailed p<0.05 for n points.

    Needed because these predictions are adjudicated over STRATA, so n is 4 or 5,
    not 96. At n=4 even r=0.94 is not significant. Without this gate a test of the
    form "r > 0" holds half the time under the null, which manufactures
    confirmations: a synthetic run with burn deliberately held CONSTANT across
    strata still reported P2 as HOLDS at r=0.003.
    """
    t = {3: 12.706, 4: 4.303, 5: 3.182, 6: 2.776, 7: 2.571, 8: 2.447}.get(n, 2.0)
    df = max(1, n - 2)
    return t / (t * t + df) ** 0.5


def is_contest(rec: dict) -> bool:
    """False when the opponent's slot FAILED rather than lost.

    A negative final net score means the opponent spent more on vision than it ever
    collected: a broken configuration, not a defeat. Counting those as wins inflates every
    win-rate estimate built on them. This is not a rare event -- two separate opponents have
    produced it (``QuantLK`` in four passive games at -742/-339/-210/-255, and
    ``Tundra-wawa`` at -1496 while collecting 4 gold across 998 unit-rounds) -- so the
    filter is standing, not ad hoc. Excluding them moved the post-changeover passive win
    rate from 21/31 to 17/27, i.e. from above the front-16 threshold to below it, which is
    exactly the kind of swing that must not ride on an opponent's malfunction.

    Running total for 2026-08-10: SEVEN games across FOUR opponents were removed by this
    filter -- QuantLK x4 (-742/-339/-210/-255), Tundra-wawa x2 (-1496/-1491), D12 (-721)
    and hhh (-64) -- about 7% of contested games. That is a rate, not an anomaly, so the
    filter is required rather than defensive. Note the malfunction is SLOW, not fast: those
    games show the opponent at P50 4400-5600ns because it buys vision every round, so they
    INFLATE an opponent's apparent latency rather than flattering it.

    Callers must print how many were excluded; a silent filter is how a corpus quietly
    stops meaning what its label says.
    """
    theirs = rec.get("their_net")
    return theirs is None or int(theirs) >= 0


def cmd_estimate(_args: argparse.Namespace) -> int:
    roster = load_roster()
    if not RESULTS.exists():
        print("no results yet", file=sys.stderr)
        return 2
    resolved = [d for d in json.loads(RESULTS.read_text(encoding="utf-8")) if "is_win" in d]
    done = [d for d in resolved if is_contest(d)]
    dropped = [d for d in resolved if not is_contest(d)]
    if dropped:
        print("excluded %d game(s) where the opponent finished on a NEGATIVE net score"
              " (broken slot, not a contest):" % len(dropped))
        for d in dropped:
            print("  %s  their_net=%s  game %s"
                  % (d.get("team"), d.get("their_net"), d.get("game_id")))
        print()
    weights = {s["stratum"]: s["weight"] for s in roster["strata"]}
    print("%-6s %6s %6s %8s %-18s %s" % ("strat", "n", "W", "rate", "Wilson95", "weight"))
    total, wsum = 0.0, 0.0
    for name in [s["stratum"] for s in roster["strata"]]:
        sub = [d for d in done if d["stratum"] == name]
        if not sub:
            print("%-6s %6s %6s %8s" % (name, 0, 0, "-"))
            continue
        w = sum(d["is_win"] for d in sub)
        lo, hi = wilson(w, len(sub))
        print("%-6s %6d %6d %8.3f [%.3f, %.3f]   %.2f"
              % (name, len(sub), w, w / len(sub), lo, hi, weights.get(name, 0)))
        total += weights.get(name, 0) * (w / len(sub))
        wsum += weights.get(name, 0)
    if wsum:
        print()
        print("STRATIFIED FIELD ESTIMATE = %.3f  (weights sum %.2f)" % (total / wsum, wsum))
        print("  compare: platform 24h rolling win_rate is NOT comparable (91%% top-two mix);")
        print("  the standing to-be-verified baseline is the all-time passive rate")
        print("  68/133 = 0.511, biased by: old Aug-7 build, self-selected challengers, unstratified.")
    # ---- pre-registered predictions P1/P2/P3 (docs/FIELD_SAMPLING_PLAN.md 5.5) ----
    withmech = [d for d in done if d.get("mechanics", {}).get("ours")]
    if withmech:
        print()
        print("=== PRE-REGISTERED CHECKS (written before the run; do not revise) ===")
        n_strat = sum(1 for d in withmech if d["kind"] == "stratified")
        underpowered = n_strat < 60
        UP = "UNDERPOWERED (descriptive only at this n)"
        if underpowered:
            print("!! CORRELATION / MONOTONICITY PARTS ARE NOT ADJUDICABLE AT n=%d." % n_strat)
            print("   The plan was cut to one game per team deliberately: precision is set by")
            print("   between-team spread, and the decision this batch serves is the binary")
            print("   'are we above or below roughly 55%% against the field', which +-9pp answers.")
            print("   That trade costs the correlation tests their power -- the game-level floor")
            print("   becomes |r| > %.3f and the per-stratum cells are about 12/8/5/5."
                  % crit_r(max(3, n_strat)))
            print("   P1/P2/P5 are therefore DESCRIPTIVE ONLY below. Reporting them as")
            print("   HOLDS/FALSIFIED at this n would be the exact false confirmation that")
            print("   rule 33 exists to prevent. The main criteria -- weighted win rate and")
            print("   which stratum we start losing at -- remain usable.")
        order = [s["stratum"] for s in roster["strata"]]
        rates, burns = {}, {}
        for name in order:
            sub = [d for d in withmech if d["stratum"] == name]
            if not sub:
                continue
            rates[name] = statistics.fmean(d["is_win"] for d in sub)
            burns[name] = statistics.fmean(d["mechanics"]["theirs"].get("burn", 0)
                                           for d in sub if d["mechanics"].get("theirs"))
        seq = [rates[n] for n in order if n in rates]
        print("P1 low strata win more, monotone decreasing: rates %s"
              % [round(x, 3) for x in seq])
        print("   -> %s" % (UP if underpowered
                            else "HOLDS (monotone non-increasing)"
                            if all(a >= b for a, b in zip(seq, seq[1:]))
                            else "FALSIFIED (not monotone)"))
        if len(rates) >= 3:
            xs = [burns[n] for n in order if n in rates]
            ys = [rates[n] for n in order if n in rates]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            vx = sum((a - mx) ** 2 for a in xs) ** 0.5
            vy = sum((b - my) ** 2 for b in ys) ** 0.5
            r = cov / (vx * vy) if vx and vy else float("nan")
            rc = crit_r(len(xs))
            spread = (max(xs) - min(xs)) if xs else 0.0
            print("P2 stratum win rate correlates POSITIVELY with opponent burn: r = %.3f"
                  " (n=%d strata, |r| must exceed %.3f for p<0.05)" % (r, len(xs), rc))
            if spread < 1e-9:
                verdict = ("INCONCLUSIVE (opponent burn is constant across strata, so there is"
                           " no variation to correlate against)")
            elif r > rc:
                verdict = "HOLDS (positive and significant)"
            elif r < -rc:
                verdict = "FALSIFIED (significant in the WRONG direction)"
            else:
                verdict = "INCONCLUSIVE (|r| below the n=%d significance floor)" % len(xs)
            print("   -> %s" % (UP if underpowered else verdict))
        wins = [d for d in withmech if d["is_win"]]
        if wins:
            dg = statistics.fmean(d["mechanics"]["ours"]["gross"]
                                  - d["mechanics"]["theirs"]["gross"] for d in wins)
            db = statistics.fmean(d["mechanics"]["theirs"]["burn"]
                                  - d["mechanics"]["ours"]["burn"] for d in wins)
            # SEs are required, not decorative: at ~15 winning games two bare mean
            # differences will look decisive from noise alone. Same defect class as the
            # old "r > 0" test and the un-barred confound gradient.
            def sem(vals):
                v = list(vals)
                return (statistics.stdev(v) / len(v) ** 0.5) if len(v) > 1 else float("nan")
            g_se = sem(d["mechanics"]["ours"]["gross"] - d["mechanics"]["theirs"]["gross"]
                       for d in wins)
            b_se = sem(d["mechanics"]["theirs"]["burn"] - d["mechanics"]["ours"]["burn"]
                       for d in wins)
            print("P3 in games we WIN (n=%d), burn edge beats gross edge: "
                  "d_gross=%+.4f+-%.4f (%+.0f gold)  d_burn=%+.4f+-%.4f (%+.0f gold)"
                  % (len(wins), dg, g_se, dg * 1000, db, b_se, db * 1000))
            if db <= 0 and dg <= 0:
                p3 = ("FALSIFIED (neither edge is in our favour in games we win; comparing two"
                      " negative edges cannot show burn 'dominating')")
            elif db > dg and db > 0:
                p3 = "HOLDS (burn edge positive and larger than the gross edge)"
            elif dg > 0 and dg >= db:
                p3 = "FALSIFIED (gross edge dominates)"
            else:
                p3 = "INCONCLUSIVE (mixed signs; state the split rather than a winner)"
            # a verdict needs the winner to be distinguishable from the loser, not just
            # numerically larger; require the gap to clear the combined error.
            gap_se = (g_se ** 2 + b_se ** 2) ** 0.5
            if underpowered or len(wins) < 8:
                p3 = UP + (" -- only %d winning games" % len(wins))
            elif gap_se == gap_se and abs(db - dg) <= 2 * gap_se:
                p3 = ("INCONCLUSIVE (the two edges differ by %+.4f+-%.4f, within 2 SE, so which"
                      " one dominates is not resolved)" % (db - dg, gap_se))
            print("   -> %s" % p3)
        # P5: stratum win rate should track opponent SLOWNESS (their P50) and our f
        def corr(xs, ys):
            if len(xs) < 3:
                return float("nan")
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            dx = sum((a - mx) ** 2 for a in xs) ** 0.5
            dy = sum((b - my) ** 2 for b in ys) ** 0.5
            return num / (dx * dy) if dx and dy else float("nan")
        sp, fv, wr = [], [], []
        for name in order:
            sub = [d for d in withmech if d["stratum"] == name
                   and d["mechanics"].get("their_p50_ns")]
            if not sub:
                continue
            sp.append(statistics.fmean(d["mechanics"]["their_p50_ns"] for d in sub))
            fv.append(statistics.fmean(d["mechanics"]["f"] or 0 for d in sub))
            wr.append(statistics.fmean(d["is_win"] for d in sub))
        if len(wr) >= 3:
            r_speed, r_f = corr(sp, wr), corr(fv, wr)
            rc5 = crit_r(len(wr))
            print("P5 win rate tracks opponent slowness / our f: r(their_P50)=%.3f  r(f)=%.3f"
                  " (n=%d strata, floor %.3f)" % (r_speed, r_f, len(wr), rc5))
            if r_speed > rc5 or r_f > rc5:
                p5 = "HOLDS (at least one relation positive and significant)"
            elif r_speed < -rc5 or r_f < -rc5:
                p5 = "FALSIFIED (significant in the WRONG direction)"
            else:
                p5 = "INCONCLUSIVE (both below the n=%d significance floor)" % len(wr)
            print("   -> %s" % (UP if underpowered else p5))
            print("   P2 vs P4 vs P5 are competing; compare r(opponent burn) above with these.")
        fs = [d["mechanics"]["f"] for d in withmech if d["mechanics"].get("f") is not None]
        if fs:
            print("observed f: median %.3f  min %.3f  max %.3f  (f<0.95 in %d/%d games)"
                  % (statistics.median(fs), min(fs), max(fs),
                     sum(1 for x in fs if x < 0.95), len(fs)))
            print("  reminder: first mover does NOT auto-convert (AGENT.md:57); f~1 is")
            print("  necessary, not sufficient -- several older 230-260ns builds still lost.")

        # ---- game-level adjudication of P2/P5 (the statistically sound version) ----
        # Added BEFORE any real game existed, for a reason the synthetic dry run made
        # unmissable: adjudicated over 4 strata, r=0.943 is still "not significant"
        # (floor 0.950), so the stratum-level forms of P2 and P5 are close to
        # unfalsifiable no matter what the world does. Correlating per GAME uses
        # n=90 instead of n=4 and drops the floor from 0.950 to about 0.21. The
        # stratum-level lines above are kept for continuity with the pre-registration;
        # where the two disagree, the game-level verdict is the one with the power.
        strat_games = [d for d in withmech if d["kind"] == "stratified"
                       and d["mechanics"].get("their_p50_ns")]
        if len(strat_games) >= 10:
            wins = [float(d["is_win"]) for d in strat_games]
            speeds = [float(d["mechanics"]["their_p50_ns"]) for d in strat_games]
            fvals = [float(d["mechanics"]["f"]) for d in strat_games
                     if d["mechanics"].get("f") is not None]
            burns = [float(d["mechanics"]["theirs"].get("burn", 0.0)) for d in strat_games
                     if d["mechanics"].get("theirs")]
            floor = crit_r(len(strat_games))
            print()
            print("--- P2/P5 at the GAME level (n=%d, significance floor %.3f) ---"
                  % (len(strat_games), floor))
            for label, xs in (("opponent burn  (P2)", burns),
                              ("opponent P50   (P5)", speeds),
                              ("our f          (P5)", fvals)):
                if len(xs) != len(wins) or (max(xs) - min(xs)) < 1e-9:
                    print("  %s: no usable variation" % label)
                    continue
                rr = corr(xs, wins)
                tag = ("positive, significant" if rr > floor else
                       "NEGATIVE, significant" if rr < -floor else
                       "not significant")
                print("  %s vs win: r = %+.3f  (%s)" % (label, rr, tag))

        # ---- adjudicate claim B: do we actually burn less than the field? ----
        # Claim A ("the bomb-avoidance organ is worth keeping") rests on a clean
        # same-seed paired ablation and is settled. Claim B ("our surplus against the
        # FIELD comes from burning less") only ever had cross-team comparisons, and the
        # two that exist point in OPPOSITE directions: against Ausdroid theirs was 3.0x
        # ours, while in game 185976 ours was 55% higher than theirs. This block decides
        # B on 90 games. Note B being false does not weaken A.
        bb = [d for d in withmech if d["kind"] == "stratified"
              and d["mechanics"].get("ours") and d["mechanics"].get("theirs")]
        if len(bb) >= 10:
            def quart(vals):
                v = sorted(vals)
                return (v[len(v) // 4], statistics.median(v), v[(3 * len(v)) // 4])
            ours_b = [d["mechanics"]["ours"].get("burn", 0.0) for d in bb]
            thrs_b = [d["mechanics"]["theirs"].get("burn", 0.0) for d in bb]
            lower = sum(1 for d in bb
                        if d["mechanics"]["ours"].get("burn", 0.0)
                        < d["mechanics"]["theirs"].get("burn", 0.0))
            lo, hi = wilson(lower, len(bb))
            print()
            print("--- claim B: is our burn lower than the opponent's? (n=%d) ---" % len(bb))
            print("  our burn    Q1/med/Q3 = %.4f / %.4f / %.4f" % quart(ours_b))
            print("  their burn  Q1/med/Q3 = %.4f / %.4f / %.4f" % quart(thrs_b))
            print("  games where OUR burn is lower: %d/%d = %.3f  Wilson95 [%.3f, %.3f]"
                  % (lower, len(bb), lower / len(bb), lo, hi))
            if lo > 0.5:
                verdict = "HOLDS (significantly more than half)"
            elif hi < 0.5:
                verdict = "FALSIFIED IN REVERSE (we burn MORE than the field)"
            else:
                verdict = ("FALSIFIED as stated (not significantly more than half; keep only"
                           " claim A -- the organ is worth keeping -- and drop the claim that"
                           " avoidance is our surplus source)")
            print("  -> %s" % verdict)
            print("  per stratum (B may hold only where opponents are slow):")
            for name in order:
                sub = [d for d in bb if d["stratum"] == name]
                if not sub:
                    continue
                sl = sum(1 for d in sub if d["mechanics"]["ours"].get("burn", 0.0)
                         < d["mechanics"]["theirs"].get("burn", 0.0))
                print("    %-5s %2d games, our burn lower in %2d (%.2f), their median burn %.4f"
                      % (name, len(sub), sl, sl / len(sub),
                         statistics.median([d["mechanics"]["theirs"].get("burn", 0.0)
                                            for d in sub])))
            print("  if B holds only in some strata it MUST be stated per stratum; S1+S2 are")
            print("  71 of 88 teams, so a stratum-limited B still matters but must not be")
            print("  generalised to the whole field.")

        # ---- middle link of the scarcity causal chain (no extra quota, one more column) ----
        # The other line's hypothesis is that our income is scarcity-sensitive and moving
        # second is merely one way the board gets thin. That chain needs three links:
        #   faster opponent -> we move second more -> board is thinner for us -> we lose.
        # This block supplies the MIDDLE link, binned by opponent speed, so the chain has
        # data at both ends and in between rather than being inferred across two reports.
        binned = [d for d in withmech
                  if d["mechanics"].get("their_p50_ns") and d["mechanics"].get("f") is not None]
        if len(binned) >= 8:
            binned.sort(key=lambda d: d["mechanics"]["their_p50_ns"])
            q = max(1, len(binned) // 4)
            groups = [binned[i:i + q] for i in range(0, len(binned), q)][:4]
            print()
            print("--- our second-mover share by opponent speed (chain middle link) ---")
            print("%-16s %4s %10s %14s %9s %10s" % (
                "their P50 range", "n", "their P50", "our 2nd-mover", "win rate", "our mean"))
            for g in groups:
                lo = g[0]["mechanics"]["their_p50_ns"]
                hi = g[-1]["mechanics"]["their_p50_ns"]
                second = statistics.fmean(1.0 - d["mechanics"]["f"] for d in g)
                wrate = statistics.fmean(d["is_win"] for d in g)
                omean = statistics.fmean(d["mechanics"]["ours"].get("mean", 0) for d in g
                                         if d["mechanics"].get("ours"))
                print("%-16s %4d %10.0f %14.3f %9.3f %10.4f"
                      % ("%.0f-%.0f" % (lo, hi), len(g),
                         statistics.median([d["mechanics"]["their_p50_ns"] for d in g]),
                         second, wrate, omean))
            print("  read: if the fastest-opponent bin shows BOTH a higher second-mover share")
            print("  AND a lower win rate, the middle link holds. If our second-mover share is")
            print("  flat across bins, then opponent speed is NOT reaching us through action")
            print("  order, and the scarcity chain must be carried by something else.")
            print("  CAUTION -- this table is observational and action order is ENDOGENOUS: we")
            print("  cause it. Our slow fallback branch fires exactly when our local situation is")
            print("  bad, so a bad board makes us slow AND poor at once, and binning on speed")
            print("  compares our worst situations against a random sample of theirs. The other")
            print("  line measured this confound directly (sim/reports/comparison_discipline.md):")
            print("  restricted to a near-tie window our order sensitivity collapses from 2.380x")
            print("  to 1.759x, ratio-of-ratios 1.445x -> 1.127x, and in absolute gold per round")
            print("  we lose marginally LESS from moving second than they do. So do NOT read the")
            print("  bins causally; read the near-tie block below, and treat the gap between the")
            print("  two as the size of the confound.")

            # quasi-random subset: games where the two sides' P50 are within a hair, so
            # which side moves first is close to a coin flip and our own branch mix is
            # not driving the split. This is the game-level analogue of the near-tie
            # window; the difference from the full sample measures the confound.
            for window in (10, 20):
                tie = [d for d in binned
                       if abs(d["mechanics"]["their_p50_ns"] - d["mechanics"]["our_p50_ns"]) <= window]
                if len(tie) >= 6:
                    tw = statistics.fmean(d["is_win"] for d in tie)
                    tsecond = statistics.fmean(1.0 - d["mechanics"]["f"] for d in tie)
                    lo_t, hi_t = wilson(sum(int(d["is_win"]) for d in tie), len(tie))
                    print("  near-tie |dP50|<=%2dns: n=%2d  our 2nd-mover %.3f  win rate %.3f"
                          " Wilson95 [%.3f, %.3f]"
                          % (window, len(tie), tsecond, tw, lo_t, hi_t))
                else:
                    print("  near-tie |dP50|<=%2dns: n=%d, too few to report" % (window, len(tie)))
            print("  if the near-tie win rate is ALSO low, the deficit is a LEVEL deficit, not an")
            print("  order-sensitivity deficit, and no amount of winning the order race fixes it.")

            # The endogeneity gate, stated as a comparison rather than a warning.
            # Opponent P50 is the only clean EXOGENOUS stratifier here: we cannot
            # influence how fast they are. f is ENDOGENOUS -- it is a function of our own
            # cost, which is a function of which branch our code took, which is worse
            # exactly when our local board is worse. Reporting both and differencing them
            # is what turns "beware the confound" into a measured quantity.
            fb = sorted(binned, key=lambda d: d["mechanics"]["f"])
            qf = max(1, len(fb) // 4)
            fgroups = [fb[i:i + qf] for i in range(0, len(fb), qf)][:4]
            print()
            print("--- the same win rates binned by f (ENDOGENOUS) vs by opponent P50 (EXOGENOUS) ---")
            print("%-18s %4s %9s   |  %-18s %4s %9s" % (
                "f range (endog)", "n", "win rate", "P50 range (exog)", "n", "win rate"))
            for fg, sg in zip(fgroups, groups):
                print("%-18s %4d %9.3f   |  %-18s %4d %9.3f" % (
                    "%.3f-%.3f" % (fg[0]["mechanics"]["f"], fg[-1]["mechanics"]["f"]),
                    len(fg), statistics.fmean(d["is_win"] for d in fg),
                    "%.0f-%.0f" % (sg[0]["mechanics"]["their_p50_ns"],
                                   sg[-1]["mechanics"]["their_p50_ns"]),
                    len(sg), statistics.fmean(d["is_win"] for d in sg)))
            def spread_se(a, b):
                """Top-minus-bottom win-rate gap and its standard error."""
                pa = statistics.fmean(d["is_win"] for d in a)
                pb = statistics.fmean(d["is_win"] for d in b)
                se = (pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b)) ** 0.5
                return pb - pa, se
            f_spread, f_se = spread_se(fgroups[0], fgroups[-1])
            p_spread, p_se = spread_se(groups[0], groups[-1])
            d_se = (f_se ** 2 + p_se ** 2) ** 0.5
            # Printed WITH standard errors, because a quartile gap over n~22 per bin has
            # an SE near 0.14: a zero-signal dry run where the winner was a pure coin flip
            # still produced an exogenous spread of -0.250, which without an error bar
            # reads as a real gradient. Same failure mode as the old "r > 0" test.
            print("  top-minus-bottom spread: by f %+.3f+-%.3f   by opponent P50 %+.3f+-%.3f"
                  % (f_spread, f_se, p_spread, p_se))
            print("  difference (the confound) %+.3f+-%.3f  -> %s"
                  % (f_spread - p_spread, d_se,
                     "exceeds 2 SE, confound is real"
                     if abs(f_spread - p_spread) > 2 * d_se
                     else "within 2 SE, no measurable confound at this n"))
            for label, sp, se in (("by f", f_spread, f_se),
                                  ("by opponent P50", p_spread, p_se)):
                if abs(sp) <= 2 * se:
                    print("    note: the %s gradient itself is within 2 SE -- do not read a trend"
                          % label)
            print("  THAT DIFFERENCE IS THE CONFOUND, not an effect: f is produced by our own")
            print("  cost, so binning on it sorts our own good boards from our bad ones. Only the")
            print("  exogenous column supports a causal reading, and even it is observational.")
            print("  Precedent: the observational 2.380x order sensitivity became 1.759x in a")
            print("  near-tie window (ratio-of-ratios 1.445x -> 1.127x), so the anchor that P4/P5")
            print("  were originally built on shrank by roughly an order of magnitude.")

    bench = [d for d in done if d["kind"] == "purposive"]
    if bench:
        w = sum(d["is_win"] for d in bench)
        lo, hi = wilson(w, len(bench))
        print()
        print("BENCH (purposive, NOT pooled): %s %d/%d = %.3f Wilson95 [%.3f, %.3f]"
              % (bench[0]["team"], w, len(bench), w / len(bench), lo, hi))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roster"); r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_roster)
    sub.add_parser("show").set_defaults(func=cmd_show)
    rd = sub.add_parser("reduce")
    rd.add_argument("--bench", type=int, default=2,
                    help="purposive benchmark games to keep (default 2)")
    rd.set_defaults(func=cmd_reduce)
    s = sub.add_parser("submit")
    # Durable default. The five-gate-verified artifact was originally only in
    # /tmp, which macOS clears on reboot, so a batch scheduled hours later could
    # have found it missing. ./player_current.so is inside the repo but matched by
    # the *.so gitignore rule, so it survives reboots without entering git.
    # sha256 f66471636a528d33c2cfa16e1187a8fc91023ddb7eceed3061df156b0db1c7bd
    # ROOT-anchored, because arena._spec() opens the path relative to the current
    # working directory; a bare relative default would break under cron or from a
    # subdirectory.
    s.add_argument("--so", default=str(ROOT / "player_current.so"))
    s.add_argument("--confirm", action="store_true")
    s.add_argument("--reserve", type=int, default=20,
                   help="games to leave unspent in the current window. Defaults to 20 so an "
                        "accidental --confirm near a window edge refuses instead of dribbling "
                        "out a partial batch; a fresh 500-game window still fits all 96.")
    s.set_defaults(func=cmd_submit)
    sub.add_parser("collect").set_defaults(func=cmd_collect)
    sub.add_parser("estimate").set_defaults(func=cmd_estimate)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
