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


def games_used_this_window(rows: list[dict]) -> int:
    start = window_start()
    def ts(row):
        return datetime.datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    return sum(1 for r in rows if ts(r) >= start)


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


def cmd_submit(args: argparse.Namespace) -> int:
    import arena
    data = load_roster()
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
    used = games_used_this_window(all_games())
    remaining = DAILY_QUOTA - used
    print("quota window: used %d / %d  -> remaining %d" % (used, DAILY_QUOTA, remaining))
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


def cmd_estimate(_args: argparse.Namespace) -> int:
    roster = load_roster()
    if not RESULTS.exists():
        print("no results yet", file=sys.stderr)
        return 2
    done = [d for d in json.loads(RESULTS.read_text(encoding="utf-8")) if "is_win" in d]
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
    s = sub.add_parser("submit")
    s.add_argument("--so", default="/tmp/gr_fix/plat_fix.so")
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
