#!/usr/bin/env python3
"""Run the pre-registered batch of games against one opponent's public slot.

The batch this exists for is registered in ``sim/reports/rikka_strategy.md`` §8:
20 games of our live artefact against ``rikka``'s public defence slot
(``model_id 51256``, model name ``player47``) on map1.  It answers the one
question that report could not, because we have never played their public slot
with a current build.

The pre-registered parameters live in this file rather than in a shell history so
that the batch is reproducible and so the conditions travel with the data, in the
same spirit as the licensing rule in that report: a quantity that holds only
under preconditions must carry those preconditions with it.

Quota discipline
----------------
Remaining quota is read from ``get_user_info``'s ``today_initiated`` and
``daily_initiate_limit`` and from nowhere else.  Counting rows of
``get_game_list_1`` is wrong because that list also contains passive defence
games, which do not consume quota; the two disagreed by 28 on one occasion.
The guard is fail-closed: if the authoritative figures cannot be read, nothing
is submitted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import string
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

LOGS = ROOT / "logs"

# --- pre-registered batch parameters (sim/reports/rikka_strategy.md, section 8) ---
OPPONENT_MODEL_ID = 51256          # rikka public defence slot, model name player47
OPPONENT_UID = 47
MAP_ID = 1                         # single map on purpose: between-map spread is
                                   # comparable to the effect being measured
GAMES = 20
NAME_PREFIX = "pl47"               # replicates pl47a .. pl47t, folded by --fold-prefix
ARTEFACT = ROOT / "player_current.so"
# src/CHANGELOG.md:88 records this as the live fd47ea6 artefact, built on the
# contest machine.  It is also the artefact currently occupying our public slot,
# so it has already completed platform games without an illegal-instruction fault.
ARTEFACT_SHA256 = "f66471636a528d33c2cfa16e1187a8fc91023ddb7eceed3061df156b0db1c7bd"
RESERVE = 20                       # quota left unspent after the batch


def sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quota(arena) -> tuple[int, int]:
    """Authoritative remaining quota.  Raises rather than guessing."""
    info = arena.call("GET", "/api/user/get_user_info")
    used, limit = info.get("today_initiated"), info.get("daily_initiate_limit")
    if not isinstance(used, int) or not isinstance(limit, int):
        raise SystemExit("cannot read authoritative quota fields; refusing to submit")
    return used, limit


def preflight(arena, games: int, reserve: int) -> None:
    if not ARTEFACT.is_file():
        raise SystemExit("artefact missing: %s" % ARTEFACT)
    found = sha256(ARTEFACT)
    if found != ARTEFACT_SHA256:
        raise SystemExit(
            "artefact SHA256 mismatch\n  expected %s\n  found    %s\n"
            "This is the check that would have caught the SIGILL game 172111."
            % (ARTEFACT_SHA256, found))
    head = ARTEFACT.read_bytes()[:20]
    if head[:4] != b"\x7fELF" or head[4] != 2 or head[18] != 0x3E:
        raise SystemExit("artefact is not an ELF64 x86-64 object; wrong build host")
    used, limit = quota(arena)
    remaining = limit - used
    print("artefact   : %s" % ARTEFACT.name)
    print("  sha256   : %s (matches src/CHANGELOG.md:88, live fd47ea6)" % found)
    print("  format   : ELF64 x86-64")
    print("quota      : %d used of %d, %d remaining" % (used, limit, remaining))
    print("plan       : %d games on map%d vs model_id %d, reserve %d"
          % (games, MAP_ID, OPPONENT_MODEL_ID, reserve))
    if remaining < games + reserve:
        raise SystemExit(
            "REFUSING: %d remaining < %d games + %d reserve. The window resets at "
            "16:00 UTC; wait for it rather than eating the reserve."
            % (remaining, games, reserve))
    # Confirm the opponent slot still exists and still has the expected identity.
    models = arena.call("GET", "/api/user/get_model_list_4").get("list", [])
    match = [m for m in models if int(m.get("id", -1)) == OPPONENT_MODEL_ID]
    if not match:
        raise SystemExit("opponent model_id %d is no longer published" % OPPONENT_MODEL_ID)
    entry = match[0]
    if int(entry.get("user_id", -1)) != OPPONENT_UID:
        raise SystemExit("opponent model_id %d now belongs to user_id %s, not %d"
                         % (OPPONENT_MODEL_ID, entry.get("user_id"), OPPONENT_UID))
    print("opponent   : model %d '%s' (team %s, updated %s)"
          % (OPPONENT_MODEL_ID, entry.get("name"), entry.get("user_name_cn"),
             entry.get("updated_at")))


def replicate_names(count: int) -> list[str]:
    if count > len(string.ascii_lowercase):
        raise SystemExit("more replicates than single-letter suffixes")
    return ["%s%s" % (NAME_PREFIX, letter) for letter in string.ascii_lowercase[:count]]


def submit_all(arena, names: list[str], dry_run: bool) -> list[str]:
    import os
    blob = ARTEFACT.read_bytes()
    submitted = []
    for name in names:
        fields = [("map_id", str(MAP_ID)), ("model_id", str(OPPONENT_MODEL_ID)),
                  ("model_langs", "2"), ("model_names", name)]
        files = [("model_files", os.path.basename(str(ARTEFACT)), blob)]
        if dry_run:
            print("  [dry-run] would submit %s vs %d on map%d"
                  % (name, OPPONENT_MODEL_ID, MAP_ID))
            submitted.append(name)
            continue
        arena.call("POST", "/api/user/add_model_1", multipart=(fields, files))
        print("  submitted %s" % name)
        submitted.append(name)
        time.sleep(2)
    return submitted


def collect(arena, names: set[str], expected: int, timeout_s: int) -> list[int]:
    """Poll our game list for the batch's games and download each log once ready."""
    deadline = time.time() + timeout_s
    done: dict[int, str] = {}
    while time.time() < deadline and len(done) < expected:
        payload = arena.call("GET", "/api/user/get_game_list_1",
                             params={"page": 1, "page_size": 100})
        for row in payload.get("list") or []:
            gid = int(row["id"])
            ours = [p for p in row.get("players", [])
                    if str(p.get("model_name")) in names]
            if not ours or gid in done:
                continue
            if not row.get("is_parse_log"):
                continue
            path = LOGS / ("game_%d.log" % gid)
            if not path.is_file():
                text = arena.call("GET", "/api/user/get_game_log",
                                  params={"id": gid}, raw=True)
                if len(text) < 10000:
                    print("  game %d log not usable yet" % gid)
                    continue
                path.write_text(text)
            done[gid] = str(ours[0]["model_name"])
            print("  ready %d (%s)" % (gid, done[gid]))
        if len(done) < expected:
            time.sleep(15)
    if len(done) < expected:
        print("WARNING: only %d of %d games completed within the timeout; the "
              "analysis must report the realised n, not the planned n"
              % (len(done), expected))
    return sorted(done)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=GAMES)
    parser.add_argument("--reserve", type=int, default=RESERVE)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--collect-only", action="store_true",
                        help="skip submission, just gather logs for the batch names")
    args = parser.parse_args(argv)
    import arena

    names = replicate_names(args.games)
    if not args.collect_only:
        preflight(arena, args.games, args.reserve)
        if args.dry_run:
            print("\n--dry-run: nothing submitted")
        submit_all(arena, names, args.dry_run)
        if args.dry_run:
            return 0
    print("\ncollecting logs into %s" % LOGS)
    ids = collect(arena, set(names), args.games, args.timeout)
    print("\n%d games collected: %s" % (len(ids), ids))
    used, limit = quota(arena)
    print("quota after batch: %d used of %d, %d remaining" % (used, limit, limit - used))
    print("\nnow run:")
    print("  python3 sim/analyze_rikka.py --uid 47 --map map1 --fold-prefix %s roster"
          % NAME_PREFIX)
    print("  python3 sim/analyze_rikka.py --uid 47 --map map1 --fold-prefix %s styles"
          % NAME_PREFIX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
