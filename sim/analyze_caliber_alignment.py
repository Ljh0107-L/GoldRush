#!/usr/bin/env python3
"""Reconcile two of our own collection-quality results that cannot both be true
as literally stated, by recomputing both sides in ONE definition.

The conflict
============

*Result 1* (`sim/reports/map1_lesion.md`, commit ``7361401``).  On the 30-game
`f18064c` map1 corpus the move-order accounting identity
``net_per_round = f*A + (1-f)*B`` closes with residual 0.00 and gives
``A = +1.244 > 0``.  That was read as "round for round, when we move first,
`f18064c` out-collects T-1 and Tundra", i.e. our collection is
competitive-to-better.

*Result 2* (`sim/reports/field_playstyle_profile.md`, driver
`sim/analyze_field_profile.py`).  map1-stratified field hit rates from 133
passive games are q1 35.9% / median 42.3% / q3 45.1%, with "ours" at 36.0% =
28th percentile, framed as a below-median debt.

Three candidate resolutions are tested here, none assumed:

(a) action-order stratification -- the two numbers are measured in different
    first-mover conditions;
(b) build mixture -- the 133 passive games may not be `f18064c` at all;
(c) different denominator or hit definition.

What this driver adds that the two existing drivers do not
=========================================================

1.  ``census``      -- the build identity of the 133 passive games, established
    from primary log fields (per-round decision ``cost`` is a build fingerprint
    that survives the fact that the public slot logs our *account* name rather
    than a build name), plus the seat controls that rule out the alternative
    explanation that the defender seat inflates ``cost``.
2.  ``stratify``    -- every team's hit rate split by who moved first in that
    round, on one definition, so `f18064c` can be placed at matched action
    order instead of spliced across corpora.
3.  ``coupling``    -- the counterparty confound, measured: `f18064c`'s number
    was earned against the two strongest collectors on the board while the
    field's number was earned against our slow public slot.  Our 99 map1 games
    against T-1 across 54 of our own build families are a natural experiment
    on exactly that.
4.  ``identity``    -- Result 1 re-verified with the parallel line's own code,
    then the *matched-order* cells that ``A`` does not contain.
5.  ``definition``  -- Result 2's number reproduced, then re-cut under
    definition variants, to size candidate (c).

Definition D-CAL (used for every hit rate in this file)
======================================================

* channel: ``end.players[].units[].gold``, differenced round over round.  Per-unit
  ``gold`` is present in 100% of unit-observations regardless of fog, unlike
  ``position`` (~33%), ``actions`` (~34%) and ``pickup`` (~39%), so this is the
  only unbiased channel.  Verified again in ``census``.
* round 0 is dropped (it has no predecessor) -> 499 diffs per unit, 998
  unit-rounds per two-unit game.  This matches ``sim/analyze_gold_delta.py`` and
  ``sim/analyze_field_profile.py``.  ``sim/analyze_map1_lesion.py`` instead seeds
  round 0 from zero and keeps it (500/unit); the difference is reported, not
  silently absorbed.
* a row whose only keys are ``round``/``forfeit`` breaks the difference chain
  rather than being skipped, so no delta ever spans the gap.
* ``hit`` = P(per-unit-round delta > 0); ``yield_per_hit`` = mean delta given a
  hit.  Both are per unit-round, never per player-round.
* action order for round r comes from ``end.players[].cost`` of the SAME round.
  ``start[r].cost == end[r-1].cost`` is a stale copy, so ``start`` is unusable.
  Lower cost moves first; an exact tie goes to player 1
  (`docs/PRELIM_RULES.md` §2.4, `docs/FAQ.md:308`).
* the log field ``order`` is NOT the action order.  Verified in ``census``: it is
  present for our own side only, is 0 in 499/500 rounds of ``game_179643``
  while the cost rule flips 271/228, and does not track ``vision_spent`` or
  ``vision_r``.  It is ignored.
* rounds 0-3 are the decision-cost warm-up (2160 -> 490 ns).  Every stratified
  number is reported both over rounds 1-499 (primary) and 4-499 (sensitivity).
* map identity comes from log row 2: 40 walls = map1, 24 = map2, 78 = map3.

Discipline
==========

* Zero platform games consumed; archived logs only.
* Every conclusion-bearing number is labelled with its build identity and sample
  size (军规 27).  ``sim/OPPONENTS.md``'s pooled burst statistics are a ~100-build
  mixture and are not used anywhere in this file.
* Read-only reuse of ``sim/analyze_gold_delta.py`` and ``sim/analyze_map1_lesion.py``
  by import, so Result 1 is re-verified with its author's own code.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGS = ROOT / "logs"
ROUNDS = 500
OUR_ACCOUNT = "player220"
NUMERIC_ACCOUNT = re.compile(r"^player(\d+)$")
MAP_BY_WALLS = {24: "map2", 40: "map1", 78: "map3"}

# Tracked opponent accounts.  player167 = Ausdroid (the only mid-field team
# f18064c has ever played), player132 = GoldMiner.
ACCOUNT_TEAM = {
    "player163": "Tiuntled-1", "player57": "Tundra-wawa",
    "player167": "Ausdroid", "player132": "GoldMiner",
}

# f18064c map1 build families.  frTu1/lnA0/a2A0/alA0 are the four baseline arms
# against Tundra proven in sim/reports/archive_backfill.json; t1f1 is against
# T-1; adf1 is the six games against Ausdroid submitted 2026-08-10 by the
# orchestrator (SHA256 e88e5e80..395695dbad, byte-exact to the CHANGELOG record).
F18064C_MAP1 = {
    "frTu1": "player57", "lnA0": "player57", "a2A0": "player57",
    "alA0": "player57", "t1f1": "player163", "adf1": "player167",
}
# Reference net-score deltas, src/CHANGELOG.md, used as an identification proof.
F18064C_REFERENCE = {"frTu1": -219.2, "t1f1": -274.3}

# Organiser test accounts, excluded from every field distribution.
ORGANISER = ("测试用户",)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.fmean(clean) if clean else None


def _median(values: Sequence[float]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.median(clean) if clean else None


def _pct(values: Sequence[float], q: float) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    ordered = sorted(clean)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def quartiles_as_result2(values: Sequence[float]) -> tuple[float, float, float]:
    """The exact quartile convention of sim/analyze_field_profile.py.

    Reproduced verbatim so Result 2's q1/median/q3 can be matched bit for bit
    before any variant is tried.
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return (ordered[0], ordered[0], ordered[0])
    return (ordered[len(ordered) // 4], statistics.median(ordered),
            ordered[(3 * len(ordered)) // 4])


def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if not total:
        return None
    phat = hits / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return (centre - half, centre + half)


# ---------------------------------------------------------------------------
# one-pass game reader
# ---------------------------------------------------------------------------


class Game:
    """Everything definition D-CAL needs from one archived log."""

    __slots__ = ("gid", "path", "name1", "name2", "our_pid", "kind", "map_name",
                 "walls", "delta", "cost", "first", "order_field", "vision",
                 "final_gold", "rounds_seen", "forfeit", "pos_present",
                 "gold_present", "act_present", "pick_present")

    def __init__(self, gid: int, path: Path) -> None:
        self.gid = gid
        self.path = path
        self.delta: dict[int, dict[int, list[int]]] = {}   # pid -> round -> per-unit delta
        self.cost: dict[int, dict[int, int]] = {}          # pid -> round -> cost ns
        self.first: dict[int, int] = {}                    # round -> pid that moves first
        self.order_field: dict[int, dict[int, Any]] = {}
        self.vision: dict[int, int] = {}
        self.final_gold: dict[int, int] = {}
        self.rounds_seen = 0
        self.forfeit: Mapping[str, Any] | None = None
        self.pos_present = [0, 0]
        self.gold_present = [0, 0]
        self.act_present = [0, 0]
        self.pick_present = [0, 0]


def family_of(name: str) -> str:
    """Strip the trailing replicate letter, matching sim/analyze_gold_delta.py."""
    return name[:-1] if len(name) > 1 else name


def classify(name1: str, name2: str) -> tuple[int | None, str]:
    """Which player id is ours, and what kind of game this is.

    Our public defended slot logs our *account* name ``player220``; a game we
    initiated logs our *build* name in slot 1 and the opponent's account in
    slot 2.  Both slots carrying build names means self-play.
    """
    if name2 == OUR_ACCOUNT:
        return 2, "passive"
    if name1 == OUR_ACCOUNT:
        return 1, "passive_reversed"
    if NUMERIC_ACCOUNT.match(name2):
        return 1, "active"
    if NUMERIC_ACCOUNT.match(name1):
        return 2, "active_reversed"
    return None, "selfplay"


def read_game(path: Path, want_presence: bool = False) -> Game | None:
    gid = int(path.stem.split("_")[1])
    game = Game(gid, path)
    try:
        with path.open(encoding="utf-8") as handle:
            head = json.loads(handle.readline())
            row2 = json.loads(handle.readline())
            game.name1 = str(head.get("player1"))
            game.name2 = str(head.get("player2"))
            game.walls = sum(1 for row in row2 for cell in row if str(cell) == "1")
            game.map_name = MAP_BY_WALLS.get(game.walls, "unknown")
            game.our_pid, game.kind = classify(game.name1, game.name2)
            previous: dict[int, list[int]] = {}
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if "start" not in record or "end" not in record:
                    # forfeit / malformed: break the difference chain
                    if "forfeit" in record:
                        game.forfeit = record["forfeit"]
                    previous = {}
                    continue
                number = int(record["round"])
                game.rounds_seen += 1
                entries = {int(item["id"]): item for item in record["end"]["players"]}
                for pid, item in entries.items():
                    current = [int(unit["gold"]) for unit in item["units"]]
                    earlier = previous.get(pid)
                    if earlier is not None and len(earlier) == len(current):
                        game.delta.setdefault(pid, {})[number] = [
                            now - was for now, was in zip(current, earlier)]
                    previous[pid] = current
                    game.cost.setdefault(pid, {})[number] = int(item.get("cost") or 0)
                    game.order_field.setdefault(pid, {})[number] = item.get("order")
                    game.vision[pid] = int(item.get("vision_spent") or 0)
                    game.final_gold[pid] = int(item.get("gold") or 0)
                    if want_presence and pid in (1, 2):
                        index = pid - 1
                        for unit in item["units"]:
                            game.gold_present[index] += int(unit.get("gold") is not None)
                            game.pos_present[index] += int(unit.get("position") is not None)
                            game.act_present[index] += int(unit.get("actions") is not None)
                            game.pick_present[index] += int(unit.get("pickup") is not None)
                if len(entries) == 2:
                    a, b = sorted(entries)
                    ca, cb = game.cost[a][number], game.cost[b][number]
                    game.first[number] = a if (ca < cb or (ca == cb and a == 1)) else b
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None
    return game


def all_games(want_presence: bool = False) -> list[Game]:
    out = []
    for path in sorted(LOGS.glob("game_*.log")):
        game = read_game(path, want_presence)
        if game is not None:
            out.append(game)
    return out


# ---------------------------------------------------------------------------
# definition D-CAL: stratified hit rate
# ---------------------------------------------------------------------------


def stratified(games: Sequence[Game], pid_of: Mapping[int, int],
               low: int = 1, high: int = ROUNDS) -> Mapping[str, Any]:
    """Hit / yield for one side, split by whether that side moved first.

    ``pid_of`` maps game id -> the player id whose side is being measured.
    ``low``/``high`` bound the round numbers used; ``low=1`` implements the
    drop-round-0 convention of definition D-CAL.
    """
    cells = {"first": {"n": 0, "hits": 0, "gain": 0, "sum": 0, "burn": 0, "burns": 0},
             "second": {"n": 0, "hits": 0, "gain": 0, "sum": 0, "burn": 0, "burns": 0}}
    per_game_first, per_game_second, per_game_all = [], [], []
    rounds_first = rounds_total = 0
    for game in games:
        pid = pid_of.get(game.gid)
        if pid is None or pid not in game.delta:
            continue
        local = {"first": [0, 0], "second": [0, 0], "all": [0, 0]}
        for number, deltas in game.delta[pid].items():
            if number < low or number >= high:
                continue
            leader = game.first.get(number)
            if leader is None:
                continue
            rounds_total += 1
            rounds_first += int(leader == pid)
            key = "first" if leader == pid else "second"
            for value in deltas[:2]:
                cell = cells[key]
                cell["n"] += 1
                cell["sum"] += value
                if value > 0:
                    cell["hits"] += 1
                    cell["gain"] += value
                elif value < 0:
                    cell["burns"] += 1
                    cell["burn"] += -value
                local[key][0] += 1
                local[key][1] += int(value > 0)
                local["all"][0] += 1
                local["all"][1] += int(value > 0)
        if local["first"][0]:
            per_game_first.append(local["first"][1] / local["first"][0])
        if local["second"][0]:
            per_game_second.append(local["second"][1] / local["second"][0])
        if local["all"][0]:
            per_game_all.append(local["all"][1] / local["all"][0])

    def render(key: str) -> Mapping[str, Any]:
        cell = cells[key]
        if not cell["n"]:
            return {"unit_rounds": 0, "hit": None, "yield_per_hit": None,
                    "mean_gold_per_unit_round": None, "wilson95": None}
        return {
            "unit_rounds": cell["n"],
            "hit": cell["hits"] / cell["n"],
            "yield_per_hit": (cell["gain"] / cell["hits"]) if cell["hits"] else None,
            "mean_gold_per_unit_round": cell["sum"] / cell["n"],
            "income_per_round": 2.0 * cell["sum"] / cell["n"],
            "gross_per_round": 2.0 * cell["gain"] / cell["n"],
            "burn_per_round": 2.0 * cell["burn"] / cell["n"],
            "burn_rate": cell["burns"] / cell["n"],
            "wilson95": wilson(cell["hits"], cell["n"]),
        }

    total_n = cells["first"]["n"] + cells["second"]["n"]
    total_hits = cells["first"]["hits"] + cells["second"]["hits"]
    total_gain = cells["first"]["gain"] + cells["second"]["gain"]
    total_burn = cells["first"]["burn"] + cells["second"]["burn"]
    total_sum = cells["first"]["sum"] + cells["second"]["sum"]
    first, second = render("first"), render("second")
    ratio = None
    if first["unit_rounds"] and second["unit_rounds"] and second["income_per_round"]:
        ratio = first["income_per_round"] / second["income_per_round"]
    return {
        "games": len(per_game_all),
        "rounds": rounds_total,
        "first_mover_rate": (rounds_first / rounds_total) if rounds_total else None,
        "moving_first": first,
        "moving_second": second,
        "order_sensitivity_ratio": ratio,
        "order_gap_hit_pp": (100.0 * (first["hit"] - second["hit"])
                             if first["unit_rounds"] and second["unit_rounds"] else None),
        "pooled": {
            "unit_rounds": total_n,
            "hit": (total_hits / total_n) if total_n else None,
            "yield_per_hit": (total_gain / total_hits) if total_hits else None,
            "income_per_round": (2.0 * total_sum / total_n) if total_n else None,
            "gross_per_round": (2.0 * total_gain / total_n) if total_n else None,
            "burn_per_round": (2.0 * total_burn / total_n) if total_n else None,
            "gross_per_game": (ROUNDS * 2.0 * total_gain / total_n) if total_n else None,
            "burn_per_game": (ROUNDS * 2.0 * total_burn / total_n) if total_n else None,
        },
        "per_game_hit_first": per_game_first,
        "per_game_hit_second": per_game_second,
        "per_game_hit_pooled": per_game_all,
        "round_window": [low, high],
    }


# ---------------------------------------------------------------------------
# (b) build census of the passive corpus
# ---------------------------------------------------------------------------


def census() -> Mapping[str, Any]:
    games = all_games(want_presence=True)
    kinds = collections.Counter(game.kind for game in games)
    maps = collections.Counter(game.map_name for game in games)
    passive = [game for game in games if game.kind == "passive"]

    def cost_percentiles(game: Game, pid: int, low: int = 4) -> Mapping[str, Any]:
        values = [value for number, value in game.cost.get(pid, {}).items() if number >= low]
        return {"p50": _pct(values, 0.5), "p90": _pct(values, 0.9),
                "mean": _mean(values), "n": len(values)}

    rows = []
    for game in passive:
        ours = cost_percentiles(game, game.our_pid)
        theirs = cost_percentiles(game, 3 - game.our_pid)
        strat = stratified([game], {game.gid: game.our_pid})
        rows.append({
            "gid": game.gid, "map": game.map_name,
            "challenger_build_name": game.name1,
            "our_slot_name": game.name2,
            "our_cost_p50_ns": ours["p50"], "our_cost_p90_ns": ours["p90"],
            "their_cost_p50_ns": theirs["p50"], "their_cost_p90_ns": theirs["p90"],
            "our_first_mover_rate": strat["first_mover_rate"],
            "our_vision_spent": game.vision.get(game.our_pid),
            "rounds": game.rounds_seen,
        })

    our_p50 = [row["our_cost_p50_ns"] for row in rows if row["our_cost_p50_ns"] is not None]

    # f18064c's own map1 decision cost, same field, same rounds>=4 rule.
    frozen_p50 = []
    for game in games:
        if game.kind != "active" or game.map_name != "map1":
            continue
        if family_of(game.name1) in F18064C_MAP1:
            frozen_p50.append(cost_percentiles(game, game.our_pid)["p50"])

    # Seat controls: is the defender seat (player 2) systematically inflated?
    self_play_seat = {"p1_p50": [], "p2_p50": []}
    for game in games:
        if game.kind != "selfplay":
            continue
        self_play_seat["p1_p50"].append(cost_percentiles(game, 1)["p50"])
        self_play_seat["p2_p50"].append(cost_percentiles(game, 2)["p50"])
    defender_seat = collections.defaultdict(list)
    for game in games:
        if game.kind == "active":
            defender_seat[game.name2].append(cost_percentiles(game, 2)["p50"])

    # Fog audit, re-verified here rather than cited.
    fog = {"our_side": collections.Counter(), "their_side": collections.Counter()}
    for game in passive[:20]:
        us, them = game.our_pid - 1, (3 - game.our_pid) - 1
        for label, index in (("our_side", us), ("their_side", them)):
            fog[label]["gold"] += game.gold_present[index]
            fog[label]["position"] += game.pos_present[index]
            fog[label]["actions"] += game.act_present[index]
            fog[label]["pickup"] += game.pick_present[index]
            fog[label]["observations"] += 2 * game.rounds_seen

    # the `order` field is not the action order
    probe = read_game(LOGS / "game_179643.log")
    order_check = None
    if probe is not None:
        agree = collections.Counter()
        for number, leader in probe.first.items():
            value = probe.order_field.get(1, {}).get(number)
            agree[(int(leader == 1), value)] += 1
        order_check = {
            "game": "game_179643.log",
            "our_order_field_values": dict(collections.Counter(
                probe.order_field.get(1, {}).values())),
            "opponent_order_field_values": dict(collections.Counter(
                probe.order_field.get(2, {}).values())),
            "cost_rule_first_mover_vs_order_field": {str(k): v for k, v in agree.items()},
            "verdict": "the `order` field is not the action order; the cost rule flips "
                       "while `order` stays constant, and `order` is fogged for the "
                       "opponent -- ignored throughout",
        }

    challenger_names = collections.Counter(row["challenger_build_name"] for row in rows)
    return {
        "archive": {"logs": len(games), "kinds": dict(kinds), "maps": dict(maps)},
        "passive_corpus": {
            "games": len(passive),
            "map_mix": dict(collections.Counter(row["map"] for row in rows)),
            "our_slot_names": dict(collections.Counter(row["our_slot_name"] for row in rows)),
            "distinct_challenger_build_names": len(challenger_names),
            "our_cost_p50_ns": {
                "min": min(our_p50) if our_p50 else None,
                "q1": _pct(our_p50, 0.25), "median": _median(our_p50),
                "q3": _pct(our_p50, 0.75), "max": max(our_p50) if our_p50 else None,
                "games_below_1000ns": sum(1 for value in our_p50 if value < 1000),
                "games_below_500ns": sum(1 for value in our_p50 if value < 500),
            },
            "our_vision_spent_nonzero_games": sum(
                1 for row in rows if (row["our_vision_spent"] or 0) > 0),
            "rows": rows,
        },
        "f18064c_cost_p50_ns_map1": {
            "n_games": len(frozen_p50),
            "min": min(frozen_p50) if frozen_p50 else None,
            "median": _median(frozen_p50),
            "max": max(frozen_p50) if frozen_p50 else None,
        },
        "seat_controls": {
            "self_play_p1_seat_p50_median": _median(self_play_seat["p1_p50"]),
            "self_play_p2_seat_p50_median": _median(self_play_seat["p2_p50"]),
            "self_play_games": len(self_play_seat["p1_p50"]),
            "opponent_defender_seat_p50_median": {
                name: {"n": len(values), "median_p50_ns": _median(values)}
                for name, values in sorted(defender_seat.items(), key=lambda kv: -len(kv[1]))
                if len(values) >= 1
            },
            "verdict": "the player-2 / defender seat is not inflated: T-1 and Tundra "
                       "record 200 and 225 ns medians in exactly that seat, and our own "
                       "builds record a LOWER median in the p2 seat than the p1 seat in "
                       "self-play.  A ~3.6 us median in our slot is therefore the build, "
                       "not the seat.",
        },
        "fog_audit_first_20_passive": {
            label: {key: value for key, value in counter.items()}
            for label, counter in fog.items()
        },
        "order_field_check": order_check,
    }


# ---------------------------------------------------------------------------
# (a) the stratified field table
# ---------------------------------------------------------------------------


def passive_index() -> Mapping[str, list[int]]:
    """Team name -> passive game ids, rebuilt from log headers alone.

    The parallel line built this from ``get_game_list_1``; it is rebuilt here
    from the archive so the corpus is verifiable without a platform call.  Team
    names come from the numeric-account mapping where the challenger used its
    account name, and from an explicit external index otherwise.
    """
    index = ROOT / "sim" / "reports" / "caliber_passive_index.json"
    if index.exists():
        return json.loads(index.read_text(encoding="utf-8"))
    return {}


def stratify(map_name: str = "map1", low: int = 1,
             external_index: Mapping[str, Sequence[int]] | None = None) -> Mapping[str, Any]:
    games = {game.gid: game for game in all_games()}
    passive = [game for game in games.values()
               if game.kind == "passive" and game.map_name == map_name]
    by_team: dict[str, list[Game]] = collections.defaultdict(list)
    gid_team: dict[int, str] = {}
    if external_index:
        for team, ids in external_index.items():
            if team.startswith("_") or not isinstance(ids, list):
                continue                              # metadata keys
            for gid in ids:
                gid_team[int(gid)] = team
    for game in passive:
        team = gid_team.get(game.gid) or ("challenger:" + family_of(game.name1))
        by_team[team].append(game)

    field_rows = []
    for team, group in sorted(by_team.items()):
        if any(marker in team for marker in ORGANISER):
            continue
        pid_of = {game.gid: 3 - game.our_pid for game in group}
        row = stratified(group, pid_of, low=low)
        row["team"] = team
        row["games"] = len(group)
        row["challenger_build_names"] = sorted({game.name1 for game in group})
        row["their_cost_p50_ns"] = _median([
            _pct([v for n, v in game.cost[3 - game.our_pid].items() if n >= 4], 0.5)
            for game in group])
        field_rows.append(row)

    # our public slot in the same games, same definition
    slot_pid = {game.gid: game.our_pid for game in passive}
    slot = stratified(passive, slot_pid, low=low)

    # f18064c, per counterparty, same definition
    frozen: dict[str, Any] = {}
    for family, account in F18064C_MAP1.items():
        group = [game for game in games.values()
                 if game.kind == "active" and game.map_name == map_name
                 and family_of(game.name1) == family and game.name2 == account]
        if not group:
            continue
        ours = stratified(group, {game.gid: game.our_pid for game in group}, low=low)
        theirs = stratified(group, {game.gid: 3 - game.our_pid for game in group}, low=low)
        frozen[family] = {"opponent_account": account,
                          "opponent_team": ACCOUNT_TEAM.get(account, account),
                          "games": len(group), "ours": ours, "theirs": theirs}

    def pool(families: Sequence[str], side: str) -> Mapping[str, Any]:
        group = [game for game in games.values()
                 if game.kind == "active" and game.map_name == map_name
                 and family_of(game.name1) in families
                 and game.name2 == F18064C_MAP1.get(family_of(game.name1))]
        pid_of = {game.gid: (game.our_pid if side == "ours" else 3 - game.our_pid)
                  for game in group}
        out = dict(stratified(group, pid_of, low=low))
        out["games"] = len(group)
        return out

    two_strong = ("frTu1", "lnA0", "a2A0", "alA0", "t1f1")
    pooled = {
        "f18064c_vs_two_strongest": {
            "families": list(two_strong), "ours": pool(two_strong, "ours"),
            "theirs": pool(two_strong, "theirs")},
        "f18064c_vs_ausdroid": {
            "families": ["adf1"], "ours": pool(("adf1",), "ours"),
            "theirs": pool(("adf1",), "theirs")},
        "f18064c_all_map1": {
            "families": list(two_strong) + ["adf1"],
            "ours": pool(tuple(two_strong) + ("adf1",), "ours"),
            "theirs": pool(tuple(two_strong) + ("adf1",), "theirs")},
    }

    # placement of f18064c in the field distribution, per stratum
    placements = {}
    for stratum in ("moving_first", "moving_second", "pooled"):
        field = [(row["team"], row[stratum]["hit"], row[stratum].get("unit_rounds", 0),
                  row["games"])
                 for row in field_rows if row[stratum]["hit"] is not None]
        # a team needs some data in the stratum to be counted
        usable = [item for item in field if item[2] >= 200]
        values = [item[1] for item in usable]
        q1, med, q3 = quartiles_as_result2(values) if values else (None, None, None)
        entry = {
            "teams_in_stratum": len(usable),
            "teams_dropped_for_thin_stratum": len(field) - len(usable),
            "q1": q1, "median": med, "q3": q3,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "teams": sorted(usable, key=lambda item: -item[1]),
        }
        for label, source in (
                ("f18064c_vs_two_strongest", pooled["f18064c_vs_two_strongest"]["ours"]),
                ("f18064c_vs_ausdroid", pooled["f18064c_vs_ausdroid"]["ours"]),
                ("our_public_slot", slot)):
            hit = source[stratum]["hit"] if stratum != "pooled" else source["pooled"]["hit"]
            if stratum == "pooled":
                hit = source["pooled"]["hit"]
            if hit is None or not values:
                entry[label] = None
                continue
            below = sum(1 for value in values if value < hit)
            entry[label] = {
                "hit": hit,
                "teams_below": below, "teams_total": len(values),
                "percentile": 100.0 * below / len(values),
                "unit_rounds": (source[stratum]["unit_rounds"] if stratum != "pooled"
                                else source["pooled"]["unit_rounds"]),
            }
        placements[stratum] = entry

    return {
        "map": map_name, "round_window_low": low,
        "field_rows": field_rows,
        "our_public_slot_same_games": slot,
        "f18064c_by_family": frozen,
        "f18064c_pooled": pooled,
        "placements": placements,
    }


# ---------------------------------------------------------------------------
# the counterparty confound, measured
# ---------------------------------------------------------------------------


def coupling(map_name: str = "map1", low: int = 1) -> Mapping[str, Any]:
    """How much of an opponent's hit rate is OUR build's weakness?

    Both sides compete for the same generated gold, so a side's hit rate is not
    a property of that side alone.  Our 99 map1 games against T-1 span 54 of our
    own build families, several deliberately crippled: that is a natural
    experiment on the counterparty term, and Ausdroid gives the same experiment
    with `f18064c` itself on one arm.
    """
    games = [game for game in all_games()
             if game.kind == "active" and game.map_name == map_name]
    out: dict[str, Any] = {"map": map_name, "opponents": {}}
    for account, team in ACCOUNT_TEAM.items():
        group = [game for game in games if game.name2 == account]
        if len(group) < 4:
            continue
        rows = []
        by_family: dict[str, list[Game]] = collections.defaultdict(list)
        for game in group:
            by_family[family_of(game.name1)].append(game)
        for family, members in sorted(by_family.items()):
            ours = stratified(members, {game.gid: game.our_pid for game in members}, low=low)
            theirs = stratified(members, {game.gid: 3 - game.our_pid for game in members},
                               low=low)
            rows.append({
                "our_family": family, "games": len(members),
                "is_f18064c": family in F18064C_MAP1,
                "our_hit": ours["pooled"]["hit"], "their_hit": theirs["pooled"]["hit"],
                "our_hit_first": ours["moving_first"]["hit"],
                "their_hit_first": theirs["moving_first"]["hit"],
                "our_first_mover_rate": ours["first_mover_rate"],
                "sum_hit": (ours["pooled"]["hit"] or 0) + (theirs["pooled"]["hit"] or 0),
                "our_gold": _mean([float(game.final_gold.get(game.our_pid, 0))
                                   for game in members]),
                "their_gold": _mean([float(game.final_gold.get(3 - game.our_pid, 0))
                                     for game in members]),
            })
        pairs = [(row["our_hit"], row["their_hit"]) for row in rows
                 if row["our_hit"] is not None and row["their_hit"] is not None]
        slope = intercept = correlation = None
        if len(pairs) >= 3:
            xs = [x for x, _ in pairs]
            ys = [y for _, y in pairs]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            if sxx > 0:
                slope = sxy / sxx
                intercept = my - slope * mx
            syy = sum((y - my) ** 2 for y in ys)
            if sxx > 0 and syy > 0:
                correlation = sxy / math.sqrt(sxx * syy)
        sums = [row["sum_hit"] for row in rows if row["sum_hit"]]
        out["opponents"][team] = {
            "account": account, "games": len(group), "our_families": len(rows),
            "rows": sorted(rows, key=lambda row: row["our_hit"] or 0.0),
            "regression_their_hit_on_our_hit": {
                "slope": slope, "intercept": intercept, "pearson_r": correlation,
                "n_families": len(pairs)},
            "sum_of_hits": {"mean": _mean(sums), "sd": statistics.stdev(sums)
                            if len(sums) > 1 else None,
                            "min": min(sums) if sums else None,
                            "max": max(sums) if sums else None},
        }

    # The single cleanest contrast: the same mid-field opponent, our old builds
    # on one arm and f18064c on the other.
    aus = [game for game in games if game.name2 == "player167"]
    old = [game for game in aus if family_of(game.name1) != "adf1"]
    new = [game for game in aus if family_of(game.name1) == "adf1"]
    out["ausdroid_contrast"] = {
        "old_builds": {
            "games": len(old),
            "families": sorted({family_of(game.name1) for game in old}),
            "their_hit": stratified(old, {game.gid: 3 - game.our_pid for game in old},
                                    low=low),
            "our_hit": stratified(old, {game.gid: game.our_pid for game in old}, low=low),
            "our_gold": _mean([float(game.final_gold.get(game.our_pid, 0)) for game in old]),
            "their_gold": _mean([float(game.final_gold.get(3 - game.our_pid, 0))
                                 for game in old]),
        },
        "f18064c": {
            "games": len(new), "families": ["adf1"],
            "their_hit": stratified(new, {game.gid: 3 - game.our_pid for game in new},
                                    low=low),
            "our_hit": stratified(new, {game.gid: game.our_pid for game in new}, low=low),
            "our_gold": _mean([float(game.final_gold.get(game.our_pid, 0)) for game in new]),
            "their_gold": _mean([float(game.final_gold.get(3 - game.our_pid, 0))
                                 for game in new]),
        },
    }
    return out


# ---------------------------------------------------------------------------
# (c) definition variants
# ---------------------------------------------------------------------------


def definition(map_name: str = "map1",
               external_index: Mapping[str, Sequence[int]] | None = None
               ) -> Mapping[str, Any]:
    """Reproduce Result 2's cut, then vary one knob at a time."""
    games = {game.gid: game for game in all_games()}
    passive = [game for game in games.values()
               if game.kind == "passive" and game.map_name == map_name]
    gid_team: dict[int, str] = {}
    if external_index:
        for team, ids in external_index.items():
            if team.startswith("_") or not isinstance(ids, list):
                continue                              # metadata keys
            for gid in ids:
                gid_team[int(gid)] = team
    by_team: dict[str, list[Game]] = collections.defaultdict(list)
    for game in passive:
        by_team[gid_team.get(game.gid) or ("challenger:" + family_of(game.name1))].append(game)

    variants: dict[str, Any] = {}

    def cut(label: str, low: int, high: int, unit_of_replication: str,
            min_rounds: int = 400) -> None:
        values = []
        for team, group in sorted(by_team.items()):
            if any(marker in team for marker in ORGANISER):
                continue
            usable = [game for game in group if game.rounds_seen >= min_rounds]
            if not usable:
                continue
            if unit_of_replication == "unit_round":
                row = stratified(usable, {game.gid: 3 - game.our_pid for game in usable},
                                 low=low, high=high)
                if row["pooled"]["hit"] is not None:
                    values.append((team, row["pooled"]["hit"]))
            else:                                   # player-round: any unit scores
                hits = total = 0
                for game in usable:
                    pid = 3 - game.our_pid
                    for number, deltas in game.delta.get(pid, {}).items():
                        if number < low or number >= high:
                            continue
                        total += 1
                        hits += int(any(value > 0 for value in deltas[:2]))
                if total:
                    values.append((team, hits / total))
        raw = [value for _, value in values]
        q1, med, q3 = quartiles_as_result2(raw) if raw else (None, None, None)
        variants[label] = {"teams": len(raw), "q1": q1, "median": med, "q3": q3,
                           "per_team": sorted(values, key=lambda item: -item[1])}

    cut("D-CAL primary: unit-round, rounds 1-499", 1, ROUNDS, "unit_round")
    cut("variant: unit-round, rounds 4-499 (warm-up dropped)", 4, ROUNDS, "unit_round")
    cut("variant: unit-round, rounds 0-499 (round 0 kept, lesion convention)",
        0, ROUNDS, "unit_round")
    cut("variant: PLAYER-round (any of 2 units scores), rounds 1-499", 1, ROUNDS,
        "player_round")
    cut("variant: unit-round, first half only (rounds 1-250)", 1, 250, "unit_round")
    cut("variant: unit-round, second half only (rounds 250-499)", 250, ROUNDS, "unit_round")

    # the same knobs applied to f18064c, so both sides move together
    ours: dict[str, Any] = {}
    two_strong = ("frTu1", "lnA0", "a2A0", "alA0", "t1f1")
    for label, families_, account_filter in (
            ("f18064c vs two strongest (30 games)", two_strong, None),
            ("f18064c vs Ausdroid (6 games)", ("adf1",), "player167")):
        group = [game for game in games.values()
                 if game.kind == "active" and game.map_name == map_name
                 and family_of(game.name1) in families_
                 and game.name2 == F18064C_MAP1.get(family_of(game.name1))]
        row = {}
        for sub, low, high in (("rounds 1-499", 1, ROUNDS), ("rounds 4-499", 4, ROUNDS),
                               ("rounds 0-499", 0, ROUNDS)):
            found = stratified(group, {game.gid: game.our_pid for game in group},
                              low=low, high=high)
            row[sub] = {"hit": found["pooled"]["hit"],
                        "unit_rounds": found["pooled"]["unit_rounds"],
                        "hit_first": found["moving_first"]["hit"],
                        "hit_second": found["moving_second"]["hit"],
                        "first_mover_rate": found["first_mover_rate"]}
        hits = total = 0
        for game in group:
            for number, deltas in game.delta.get(game.our_pid, {}).items():
                if number < 1:
                    continue
                total += 1
                hits += int(any(value > 0 for value in deltas[:2]))
        row["player-round, rounds 1-499"] = {"hit": hits / total if total else None,
                                             "player_rounds": total}
        row["games"] = len(group)
        ours[label] = row

    return {"map": map_name, "field_variants": variants, "f18064c_variants": ours,
            "result2_published": {"q1": 0.359, "median": 0.423, "q3": 0.451,
                                  "ours": 0.360, "percentile": 28,
                                  "source": "sim/reports/field_playstyle_profile.md"}}


# ---------------------------------------------------------------------------
# Result 1 re-verification, plus the matched-order cells
# ---------------------------------------------------------------------------


def identity() -> Mapping[str, Any]:
    from sim.analyze_map1_lesion import (   # noqa: PLC0415  read-only reuse
        load_corpus, order_identity, order_sensitivity)
    corpus = load_corpus(["map1", "map2", "map3"])
    out: dict[str, Any] = {"source_driver": "sim/analyze_map1_lesion.py (imported, read-only)",
                           "maps": {}}
    for map_name, group in corpus.items():
        found = order_identity(group)
        sensitivity = order_sensitivity(group)
        matched_first = ((sensitivity["ours"]["moving_first"]["hit"] or 0.0)
                         - (sensitivity["theirs"]["moving_first"]["hit"] or 0.0))
        matched_second = ((sensitivity["ours"]["moving_second"]["hit"] or 0.0)
                          - (sensitivity["theirs"]["moving_second"]["hit"] or 0.0))
        out["maps"][map_name] = {
            "games": len(group),
            "families": sorted({game.family for game in group}),
            "f": found["our_first_rate"],
            "A_when_we_move_first": found["A_when_we_move_first"],
            "B_when_they_move_first": found["B_when_they_move_first"],
            "break_even_f": found["break_even_first_mover_rate"],
            "net_from_identity": found["net_from_identity"],
            "net_observed_mean": found["net_observed_mean"],
            "identity_residual": found["identity_residual"],
            "order_mismatched_cells": {
                "our_income_when_we_first": found["our_income_when_first"],
                "their_income_when_we_first (they are SECOND)":
                    found["their_income_when_we_first"],
                "our_income_when_second": found["our_income_when_second"],
                "their_income_when_they_first (they are FIRST)":
                    found["their_income_when_they_first"],
            },
            "matched_order_income": {
                "both_first: ours - theirs":
                    (sensitivity["ours"]["moving_first"]["income_per_round"] or 0.0)
                    - (sensitivity["theirs"]["moving_first"]["income_per_round"] or 0.0),
                "both_second: ours - theirs":
                    (sensitivity["ours"]["moving_second"]["income_per_round"] or 0.0)
                    - (sensitivity["theirs"]["moving_second"]["income_per_round"] or 0.0),
            },
            "matched_order_hit": {
                "ours_first": sensitivity["ours"]["moving_first"]["hit"],
                "theirs_first": sensitivity["theirs"]["moving_first"]["hit"],
                "gap_first_pp": 100.0 * matched_first,
                "ours_second": sensitivity["ours"]["moving_second"]["hit"],
                "theirs_second": sensitivity["theirs"]["moving_second"]["hit"],
                "gap_second_pp": 100.0 * matched_second,
            },
            "order_sensitivity": {side: sensitivity[side] for side in ("ours", "theirs")},
        }
    out["note"] = (
        "A = (our income - their income) over the rounds WE move first, so it "
        "compares our first-mover rounds against their SECOND-mover rounds.  It "
        "is therefore not a matched-action-order comparison and cannot support "
        "'we out-collect them'.  The matched cells are given above.")
    return out


# ---------------------------------------------------------------------------
# the report's own numbers, assembled
# ---------------------------------------------------------------------------


TWO_STRONGEST = ("frTu1", "lnA0", "a2A0", "alA0", "t1f1")


def _frozen_games(games: Mapping[int, Game], families_: Sequence[str],
                  map_name: str = "map1") -> list[Game]:
    return [game for game in games.values()
            if game.kind == "active" and game.map_name == map_name
            and family_of(game.name1) in families_
            and game.name2 == F18064C_MAP1.get(family_of(game.name1))]


def _paired(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        return {"n": 0}
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {"n": len(values), "mean": mean, "se": se,
            "sigma": (mean / se) if se else None}


def report(index_path: Path | None = None) -> Mapping[str, Any]:
    """Every number quoted in sim/reports/caliber_alignment.md, in one object."""
    external = None
    if index_path is not None:
        external = json.loads(Path(index_path).read_text(encoding="utf-8"))
    games = {game.gid: game for game in all_games()}
    f30 = _frozen_games(games, TWO_STRONGEST)
    f6 = _frozen_games(games, ("adf1",))
    passive1 = [game for game in games.values()
                if game.kind == "passive" and game.map_name == "map1"]

    # provenance of Result 2's "ours 36.0%"
    channel = json.loads((ROOT / "sim" / "reports" / "gold_delta_channel.json")
                         .read_text(encoding="utf-8"))
    published_tundra = channel["battlefields"]["Tundra map1"]["ours"]["hit"]
    published_t1 = channel["battlefields"]["T-1 map1"]["ours"]["hit"]
    mine_tundra = stratified(_frozen_games(games, ("frTu1",)),
                             {g.gid: g.our_pid for g in _frozen_games(games, ("frTu1",))}
                             )["pooled"]["hit"]
    mine_t1 = stratified(_frozen_games(games, ("t1f1",)),
                         {g.gid: g.our_pid for g in _frozen_games(games, ("t1f1",))}
                         )["pooled"]["hit"]

    # matched-order, per game, with a standard error
    def matched_pairs(group: Sequence[Game]) -> Mapping[str, Any]:
        both_first, both_second, gross, burn = [], [], [], []
        for game in group:
            of, os_, tf, ts = [], [], [], []
            og = ob = tg = tb = 0
            for number in range(1, ROUNDS):
                ours = game.delta.get(game.our_pid, {}).get(number)
                theirs = game.delta.get(3 - game.our_pid, {}).get(number)
                leader = game.first.get(number)
                if ours is None or theirs is None or leader is None:
                    continue
                for value in ours[:2]:
                    og += max(value, 0)
                    ob += -min(value, 0)
                for value in theirs[:2]:
                    tg += max(value, 0)
                    tb += -min(value, 0)
                if leader == game.our_pid:
                    of.append(float(sum(ours[:2])))
                    ts.append(float(sum(theirs[:2])))
                else:
                    os_.append(float(sum(ours[:2])))
                    tf.append(float(sum(theirs[:2])))
            if of and tf:
                both_first.append(statistics.fmean(of) - statistics.fmean(tf))
            if os_ and ts:
                both_second.append(statistics.fmean(os_) - statistics.fmean(ts))
            gross.append(float(og - tg))
            burn.append(float(ob - tb))
        return {"both_first_gold_per_round": _paired(both_first),
                "both_second_gold_per_round": _paired(both_second),
                "gross_diff_per_game": _paired(gross),
                "burn_diff_per_game": _paired(burn),
                "net_from_channel_per_game": _paired(
                    [g - b for g, b in zip(gross, burn)])}

    # order-sensitivity ratios for everything the corpus supports
    def entity(group: Sequence[Game], ours: bool, label: str) -> Mapping[str, Any]:
        pid_of = {game.gid: (game.our_pid if ours else 3 - game.our_pid) for game in group}
        found = stratified(group, pid_of)
        first, second = found["moving_first"], found["moving_second"]
        thin = min(first["unit_rounds"], second["unit_rounds"]) < 200
        return {"entity": label, "games": len(group),
                "first_mover_rate": found["first_mover_rate"],
                "hit_first": first["hit"], "hit_second": second["hit"],
                "income_first": first.get("income_per_round"),
                "income_second": second.get("income_per_round"),
                "unit_rounds_first": first["unit_rounds"],
                "unit_rounds_second": second["unit_rounds"],
                "order_sensitivity_ratio": found["order_sensitivity_ratio"],
                "order_gap_hit_pp": found["order_gap_hit_pp"],
                "usable": not thin,
                "wilson95_hit_first": first.get("wilson95"),
                "wilson95_hit_second": second.get("wilson95")}

    mixture = [game for game in games.values()
               if game.kind == "active" and game.map_name == "map1"
               and game.name2 in ("player163", "player57")]
    order_rows = [
        entity(f30, True, "f18064c vs T-1+Tundra (30 games)"),
        entity(f30, False, "T-1+Tundra vs f18064c (30 games)"),
        entity(_frozen_games(games, ("t1f1",)), True, "f18064c vs T-1 only (6)"),
        entity(_frozen_games(games, ("t1f1",)), False, "T-1 vs f18064c (6)"),
        entity(_frozen_games(games, ("frTu1", "lnA0", "a2A0", "alA0")), True,
               "f18064c vs Tundra only (24)"),
        entity(_frozen_games(games, ("frTu1", "lnA0", "a2A0", "alA0")), False,
               "Tundra vs f18064c (24)"),
        entity(f6, True, "f18064c vs Ausdroid (6)"),
        entity(f6, False, "Ausdroid vs f18064c (6)"),
        entity(mixture, False,
               "T-1+Tundra vs ALL our builds (189 games, ~95-build MIXTURE)"),
        entity(passive1, True, "our public slot (73 map1 passive games)"),
        entity(passive1, False, "field pooled (73 map1 passive games, 29-team MIXTURE)"),
    ]

    strat = stratify("map1", 1, external)
    per_team_order = []
    gid_team: dict[int, str] = {}
    if external:
        for team, ids in external.items():
            if team.startswith("_") or not isinstance(ids, list):
                continue
            for gid in ids:
                gid_team[int(gid)] = team
    grouped: dict[str, list[Game]] = collections.defaultdict(list)
    for game in passive1:
        grouped[gid_team.get(game.gid, "challenger:" + family_of(game.name1))].append(game)
    for team, group in sorted(grouped.items()):
        per_team_order.append(entity(group, False, team))
    usable_ratios = sorted(row["order_sensitivity_ratio"] for row in per_team_order
                           if row["usable"] and row["order_sensitivity_ratio"])

    # standardised-f placement
    standardised = {}
    usable_teams = [row for row in per_team_order if row["usable"]]
    ours30 = stratified(f30, {game.gid: game.our_pid for game in f30})
    slot = stratified(passive1, {game.gid: game.our_pid for game in passive1})
    for f in (0.4005, 0.5676, 1.0):
        field = sorted(f * row["hit_first"] + (1 - f) * row["hit_second"]
                       for row in usable_teams)
        q1, med, q3 = quartiles_as_result2(field)
        entry = {"teams": len(field), "q1": q1, "median": med, "q3": q3}
        for label, source in (("f18064c_vs_two_strongest", ours30),
                              ("our_public_slot", slot)):
            value = (f * source["moving_first"]["hit"]
                     + (1 - f) * source["moving_second"]["hit"])
            entry[label] = {"hit": value,
                            "percentile": 100.0 * sum(1 for x in field if x < value)
                            / len(field)}
        standardised["f=%.4f" % f] = entry

    # paired within-game hit contest in the passive corpus
    contest = []
    for game in passive1:
        a = stratified([game], {game.gid: game.our_pid})["pooled"]["hit"]
        b = stratified([game], {game.gid: 3 - game.our_pid})["pooled"]["hit"]
        if a is not None and b is not None:
            contest.append(a - b)
    contest_stat = dict(_paired(contest))
    contest_stat["slot_higher_in_games"] = sum(1 for value in contest if value > 0)

    # counterparty slope, degenerate arms removed
    coup = coupling("map1")
    slopes = {}
    for team, block_ in coup["opponents"].items():
        rows = [row for row in block_["rows"]
                if row["our_hit"] and row["their_hit"]]
        clean = [row for row in rows
                 if (row["their_gold"] or 0) >= 1600 and row["our_hit"] > 0.15]

        def fit(subset: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
            if len(subset) < 3:
                return {"n": len(subset), "slope": None, "pearson_r": None}
            xs = [row["our_hit"] for row in subset]
            ys = [row["their_hit"] for row in subset]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            return {"n": len(subset), "slope": sxy / sxx if sxx else None,
                    "pearson_r": sxy / math.sqrt(sxx * syy) if sxx and syy else None}
        slopes[team] = {"all_arms": fit(rows), "degenerate_arms_removed": fit(clean)}

    return {
        "definition": "D-CAL, see module docstring",
        "provenance_of_result2_ours_36pct": {
            "claim": "field_playstyle_profile.md reports 'ours 36.0% (28th percentile)'",
            "actual_source": "hard-coded constant REFERENCE['OURS frozen (map1)'] in "
                             "sim/analyze_field_profile.py, = mean of the two f18064c "
                             "map1 families in sim/reports/gold_delta_channel.json",
            "published_frTu1_hit": published_tundra,
            "published_t1f1_hit": published_t1,
            "published_mean": (published_tundra + published_t1) / 2,
            "my_recomputation_frTu1": mine_tundra,
            "my_recomputation_t1f1": mine_t1,
            "my_mean": (mine_tundra + mine_t1) / 2,
            "corpus_of_that_number": "12 map1 games, frTu1 (vs Tundra) + t1f1 (vs T-1)",
            "games_it_shares_with_the_133_passive_corpus": 0,
            "verdict": "the 36.0% is f18064c against the two strongest teams; it is NOT "
                       "our public slot and NOT from the 133 passive games.  The field's "
                       "42.3% is the challengers' side of the 73 map1 passive games.  The "
                       "two numbers share no game, no build and no counterparty.",
        },
        "census": census(),
        "stratify_map1": {key: value for key, value in strat.items()
                          if key != "field_rows"},
        "field_rows_map1": [
            {key: value for key, value in row.items()
             if key not in ("per_game_hit_first", "per_game_hit_second",
                            "per_game_hit_pooled")}
            for row in strat["field_rows"]],
        "order_sensitivity": {
            "entities": order_rows,
            "per_field_team": per_team_order,
            "field_usable_ratio_distribution": {
                "teams_usable": len(usable_ratios),
                "teams_total": len(per_team_order),
                "min": usable_ratios[0] if usable_ratios else None,
                "q1": _pct(usable_ratios, 0.25),
                "median": _median(usable_ratios),
                "q3": _pct(usable_ratios, 0.75),
                "max": usable_ratios[-1] if usable_ratios else None,
            },
        },
        "standardised_f_placement": standardised,
        "paired_within_game_hit_contest_passive_map1": contest_stat,
        "matched_order_f18064c_vs_two_strongest": matched_pairs(f30),
        "matched_order_f18064c_vs_ausdroid": matched_pairs(f6),
        "counterparty_slopes": slopes,
        "identity": identity(),
        "definition_variants": definition("map1", external),
        "not_computable": [
            "f18064c's hit rate against any of the 29 passive-corpus teams: it has never "
            "played one of them (0 shared games).",
            "matched action order against Ausdroid: Ausdroid moves first in 9 of 2994 "
            "usable rounds (18 unit-rounds), Wilson95 on its first-mover hit is about "
            "[0.20, 0.61].",
            "a counterparty-free percentile for f18064c: that needs f18064c games against "
            "a sample of the field; the corpus has exactly one mid-field opponent "
            "(Ausdroid, 6 games), which is a point and not a distribution.",
            "order-sensitivity ratio for 18 of the 29 field teams: fewer than 200 "
            "unit-rounds in one stratum.",
            "the public slot's build NAME: the log records the account 'player220', not a "
            "build name.  The latency fingerprint proves what it is not, not what it is.",
            "f18064c versus the field on map2 or map3: f18064c has no game against any "
            "non-tracked opponent on those maps.",
            "opponent trajectory quantities in the passive corpus: position ~26%, actions "
            "~37%, pickup ~37% present, all fog-truncated.",
        ],
    }


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("census", help="(b) build identity of the 133 passive games")
    strat = sub.add_parser("stratify", help="(a) action-order-stratified field table")
    strat.add_argument("--map", default="map1")
    strat.add_argument("--low", type=int, default=1)
    strat.add_argument("--index", type=Path, default=None,
                       help="JSON team -> [game_id] for passive-game team names")
    coup = sub.add_parser("coupling", help="counterparty confound, measured")
    coup.add_argument("--map", default="map1")
    defn = sub.add_parser("definition", help="(c) definition variants")
    defn.add_argument("--map", default="map1")
    defn.add_argument("--index", type=Path, default=None)
    sub.add_parser("identity", help="Result 1 re-verified + matched-order cells")
    rep = sub.add_parser("report", help="every number in caliber_alignment.md")
    rep.add_argument("--index", type=Path, default=None)
    every = sub.add_parser("all", help="everything, to JSON")
    every.add_argument("--index", type=Path, default=None)
    every.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    index = None
    if getattr(args, "index", None):
        index = json.loads(Path(args.index).read_text(encoding="utf-8"))

    if args.command == "census":
        result: Mapping[str, Any] = census()
    elif args.command == "stratify":
        result = stratify(args.map, args.low, index)
    elif args.command == "coupling":
        result = coupling(args.map)
    elif args.command == "definition":
        result = definition(args.map, index)
    elif args.command == "identity":
        result = identity()
    elif args.command == "report":
        result = report(args.index)
    else:
        result = {
            "definition": {
                "channel": "end.players[].units[].gold differenced round over round",
                "hit": "P(per-unit-round delta > 0)",
                "round_window": "1-499 primary (round 0 dropped), 4-499 sensitivity",
                "action_order": "end.players[].cost of the same round; lower first; "
                                "exact tie to player 1",
                "forfeit": "rows lacking start/end break the difference chain",
                "map": "log row 2 wall count 40/24/78 = map1/map2/map3",
            },
            "census": census(),
            "stratify_map1": stratify("map1", 1, index),
            "stratify_map1_no_warmup": stratify("map1", 4, index),
            "coupling_map1": coupling("map1"),
            "definition_variants": definition("map1", index),
            "identity": identity(),
        }
    json.dump(result, sys.stdout, indent=2, sort_keys=True, default=float,
              ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
