#!/usr/bin/env python3
"""Deterministic, standard-library analysis of the fixed 88-game opponent corpus.

The script intentionally pins the planner-established game IDs. Later manifest
additions are outside this corpus and do not change the rendered report.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence


TEAM_SPECS = {
    "Tiuntled-1": {
        "account": "player163",
        "ids": (
            158872, 158925, 158964, 159175, 162570, 162574, 162578, 162632,
            162660, 162699, 162704, 162707, 162737, 162745, 163406, 165557,
            166474, 166572, 166607, 166617, 166631, 166647, 166785, 167131,
            167490, 168446, 168951, 169276, 169366, 169367, 169404, 169405,
            169410, 169411, 169520, 169521, 169629, 169630, 169646, 169730,
            169732, 169760, 169761, 170147, 170493, 170567, 170986, 171022,
            171223, 171272,
        ),
        "valid_rounds": 23605,
        "complete": 45,
        "censored": 5,
        "maps": {"map1": 50},
    },
    "Tundra-wawa": {
        "account": "player57",
        "ids": (
            163068, 168016, 168033, 168117, 168185, 168189, 168210, 168233,
            168241, 168242, 168244, 168309, 168394, 168854, 168857, 168858,
            168918, 168920, 168950, 169275, 169368, 169406, 169412, 169522,
            169631, 169632, 169645, 169734, 169762, 169976, 169978, 170146,
            170492, 170494, 170496, 170565, 170705, 170860,
        ),
        "valid_rounds": 18488,
        "complete": 36,
        "censored": 2,
        "maps": {"map1": 34, "map2": 2, "map3": 2},
    },
}

PROBE_IDS = {
    "Tiuntled-1": (171719, 171747),
    "Tundra-wawa": (171687, 171708),
}

MAP_BY_WALLS = {40: "map1", 24: "map2", 78: "map3"}
CUT_PRIMARY = 20
CUT_SENSITIVITY = (10, 20, 30)
PCTS = (0, 10, 25, 50, 75, 90, 95, 99, 100)
REGION_NAMES = {1: "中心", 2: "上臂", 3: "左臂", 4: "下臂", 5: "右臂"}


@dataclass
class Game:
    team: str
    account: str
    game_id: int
    own_version: str
    path: Path
    map_name: str
    map_rows: list[list[str]]
    map_digest: str
    rows: list[dict[str, Any]]
    forfeit: Optional[dict[str, Any]]
    manifest_entry: dict[str, Any]

    @property
    def complete(self) -> bool:
        return len(self.rows) == 500 and self.forfeit is None


@dataclass
class BinaryEffect:
    label: str
    games: int
    n_true: int
    n_false: int
    median_delta: float
    mean_delta: float


def player(state: dict[str, Any], pid: int) -> dict[str, Any]:
    found = [p for p in state.get("players", []) if p.get("id") == pid]
    if len(found) != 1:
        raise AssertionError(f"expected exactly one player {pid}, got {len(found)}")
    return found[0]


def pct(values: Sequence[float], p: float) -> float:
    """Match tools/gamelog.py's deterministic empirical percentile rule."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * p / 100))
    return float(ordered[index])


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else float("nan")


def mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else float("nan")


def fmt_num(value: float, digits: int = 1) -> str:
    if math.isnan(value):
        return "NA"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.{digits}f}"


def fmt_signed(value: float, digits: int = 1) -> str:
    if math.isnan(value):
        return "NA"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):+d}"
    return f"{value:+.{digits}f}"


def fmt_rate(numerator: float, denominator: float, digits: int = 1) -> str:
    if not denominator:
        return "NA"
    return f"{100.0 * numerator / denominator:.{digits}f}%"


def cost(game: Game, row: dict[str, Any], pid: int) -> int:
    value = player(row["end"], pid).get("cost")
    if not isinstance(value, (int, float)):
        raise AssertionError((game.game_id, row.get("round"), pid, value))
    return int(value)


def held(row: dict[str, Any], pid: int) -> int:
    return int(player(row["end"], pid).get("gold", 0))


def vision(row: dict[str, Any], pid: int) -> int:
    return int(player(row["end"], pid).get("vision_spent", 0))


def vision_delta(game: Game, index: int, pid: int) -> int:
    current = vision(game.rows[index], pid)
    previous = vision(game.rows[index - 1], pid) if index else 0
    delta = current - previous
    if delta < 0:
        raise AssertionError((game.game_id, index, pid, previous, current))
    return delta


def unit_pickups(row: dict[str, Any], pid: int) -> tuple[list[int], bool]:
    units = player(row["end"], pid).get("units", [])
    observed = [int(u["pickup"]) for u in units if "pickup" in u]
    return observed, len(units) == 2 and len(observed) == 2


def unit_positions(row: dict[str, Any], pid: int) -> list[Optional[tuple[int, int]]]:
    units = player(row["end"], pid).get("units", [])
    result: list[Optional[tuple[int, int]]] = []
    for unit in units:
        pos = unit.get("position")
        result.append(tuple(pos) if pos is not None else None)
    return result


def region_id(row: int, col: int) -> int:
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if 0 <= row <= 3 and 0 <= col <= 12:
        return 2
    if 4 <= row <= 16 and 0 <= col <= 3:
        return 3
    if 13 <= row <= 16 and 4 <= col <= 16:
        return 4
    if 0 <= row <= 12 and 13 <= col <= 16:
        return 5
    raise AssertionError((row, col))


def load_games(repo: Path) -> tuple[dict[str, list[Game]], dict[str, list[Game]], dict[str, Any]]:
    manifest_path = repo / "logs" / "opponents" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("games", [])
    by_id: dict[int, dict[str, Any]] = {}
    for entry in entries:
        gid = int(entry["game_id"])
        if gid in by_id:
            raise AssertionError(f"duplicate manifest game_id {gid}")
        by_id[gid] = entry

    selected: dict[str, list[Game]] = {}
    selected_ids: set[int] = set()
    for team, spec in TEAM_SPECS.items():
        games: list[Game] = []
        for gid in spec["ids"]:
            if gid not in by_id:
                raise AssertionError(f"fixed corpus game {gid} missing from manifest")
            entry = by_id[gid]
            if entry.get("team") != team:
                raise AssertionError((gid, entry.get("team"), team))
            if entry.get("opponent", {}).get("account") != spec["account"]:
                raise AssertionError((gid, entry.get("opponent"), spec["account"]))
            if entry.get("opponent_player_id") != 2 or entry.get("our_player_id") != 1:
                raise AssertionError(f"game {gid}: target must be player2 and own side player1")
            rel = Path(entry["path"])
            path = repo / rel
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) < 3:
                raise AssertionError(f"short log {path}")
            names = json.loads(lines[0])
            if names.get("player1") != entry.get("our_version"):
                raise AssertionError((gid, names, entry.get("our_version")))
            if names.get("player2") != spec["account"]:
                raise AssertionError((gid, names, spec["account"]))
            map_rows = json.loads(lines[1])
            walls = sum(row.count("1") for row in map_rows)
            hotspots = sum(row.count("2") for row in map_rows)
            if walls not in MAP_BY_WALLS or hotspots != 20:
                raise AssertionError((gid, walls, hotspots))
            parsed_rows: list[dict[str, Any]] = []
            forfeit = None
            for line in lines[2:]:
                record = json.loads(line)
                if "start" in record and "end" in record:
                    parsed_rows.append(record)
                elif "forfeit" in record:
                    if forfeit is not None:
                        raise AssertionError(f"multiple forfeits in game {gid}")
                    forfeit = record
                else:
                    raise AssertionError(f"unknown record in game {gid}: {record}")
            if [r.get("round") for r in parsed_rows] != list(range(len(parsed_rows))):
                raise AssertionError(f"non-contiguous rounds in game {gid}")
            game = Game(
                team=team,
                account=spec["account"],
                game_id=gid,
                own_version=str(entry["our_version"]),
                path=path,
                map_name=MAP_BY_WALLS[walls],
                map_rows=map_rows,
                map_digest=hashlib.md5(lines[1].encode("utf-8")).hexdigest()[:8],
                rows=parsed_rows,
                forfeit=forfeit,
                manifest_entry=entry,
            )
            games.append(game)
            selected_ids.add(gid)
        selected[team] = games

    if sum(len(v) for v in selected.values()) != 88:
        raise AssertionError("fixed corpus must contain exactly 88 games")
    for team, games in selected.items():
        spec = TEAM_SPECS[team]
        valid = sum(len(g.rows) for g in games)
        complete = sum(g.complete for g in games)
        censored = len(games) - complete
        maps = collections.Counter(g.map_name for g in games)
        assert len(games) == len(spec["ids"]), (team, len(games))
        assert valid == spec["valid_rounds"], (team, valid, spec["valid_rounds"])
        assert complete == spec["complete"], (team, complete, spec["complete"])
        assert censored == spec["censored"], (team, censored, spec["censored"])
        assert dict(sorted(maps.items())) == spec["maps"], (team, maps, spec["maps"])

    probes: dict[str, list[Game]] = {}
    for team, ids in PROBE_IDS.items():
        probe_games: list[Game] = []
        for gid in ids:
            if gid not in by_id:
                raise AssertionError(f"fixed probe game {gid} missing from manifest")
            entry = by_id[gid]
            if entry.get("team") != team or entry.get("our_version") != "probeobs":
                raise AssertionError((gid, entry.get("team"), entry.get("our_version")))
            if entry.get("opponent", {}).get("account") != TEAM_SPECS[team]["account"]:
                raise AssertionError((gid, entry.get("opponent")))
            if entry.get("opponent_player_id") != 2 or entry.get("our_player_id") != 1:
                raise AssertionError(f"probe {gid}: target must be player2 and own side player1")
            path = repo / Path(entry["path"])
            lines = path.read_text(encoding="utf-8").splitlines()
            names = json.loads(lines[0])
            if names != {"player1": "probeobs", "player2": TEAM_SPECS[team]["account"]}:
                raise AssertionError((gid, names))
            map_rows = json.loads(lines[1])
            walls = sum(row.count("1") for row in map_rows)
            hotspots = sum(row.count("2") for row in map_rows)
            if walls not in MAP_BY_WALLS or hotspots != 20:
                raise AssertionError((gid, walls, hotspots))
            parsed_rows: list[dict[str, Any]] = []
            forfeit = None
            for line in lines[2:]:
                record = json.loads(line)
                if "start" in record and "end" in record:
                    parsed_rows.append(record)
                elif "forfeit" in record:
                    forfeit = record
                else:
                    raise AssertionError(f"unknown probe record in game {gid}: {record}")
            if len(parsed_rows) != 500 or forfeit is not None:
                raise AssertionError((gid, len(parsed_rows), forfeit))
            if [r.get("round") for r in parsed_rows] != list(range(500)):
                raise AssertionError(f"non-contiguous probe rounds in game {gid}")
            visibility = entry.get("opponent_visibility", {}).get("start", {})
            if visibility.get("rounds") != 500 or "opponent_uncontested_net" not in entry:
                raise AssertionError(f"probe metadata incomplete for game {gid}")
            probe_games.append(Game(
                team=team,
                account=TEAM_SPECS[team]["account"],
                game_id=gid,
                own_version="probeobs",
                path=path,
                map_name=MAP_BY_WALLS[walls],
                map_rows=map_rows,
                map_digest=hashlib.md5(lines[1].encode("utf-8")).hexdigest()[:8],
                rows=parsed_rows,
                forfeit=None,
                manifest_entry=entry,
            ))
        probes[team] = probe_games

    metadata = {
        "manifest_entries": len(entries),
        "fixed_ids": selected_ids,
        "probe_ids": {g.game_id for games in probes.values() for g in games},
    }
    return selected, probes, metadata


def rows_after(game: Game, cutoff: int) -> list[dict[str, Any]]:
    return [row for row in game.rows if int(row["round"]) >= cutoff]


def pooled_costs(games: Sequence[Game], pid: int, cutoff: int) -> list[int]:
    return [cost(g, row, pid) for g in games for row in rows_after(g, cutoff)]


def percentile_row(values: Sequence[float]) -> list[str]:
    return [fmt_num(pct(values, p)) for p in PCTS]


def per_game_p50(games: Sequence[Game], pid: int, cutoff: int) -> list[float]:
    return [pct([cost(g, row, pid) for row in rows_after(g, cutoff)], 50) for g in games]


def same_round_diffs(games: Sequence[Game], cutoff: int) -> list[int]:
    return [cost(g, row, 2) - cost(g, row, 1) for g in games for row in rows_after(g, cutoff)]


def target_first_count(games: Sequence[Game], cutoff: int) -> tuple[int, int]:
    first = total = 0
    for game in games:
        for row in rows_after(game, cutoff):
            order = row["end"].get("dispatch_order") or []
            if order:
                total += 1
                first += order[0] == 2
    return first, total


def binary_effect(
    games: Sequence[Game],
    label: str,
    predicate: Callable[[Game, int, dict[str, Any]], Optional[bool]],
    cutoff: int = CUT_PRIMARY,
) -> BinaryEffect:
    deltas: list[float] = []
    n_true = n_false = 0
    for game in games:
        yes: list[int] = []
        no: list[int] = []
        for index, row in enumerate(game.rows):
            if row["round"] < cutoff:
                continue
            flag = predicate(game, index, row)
            if flag is None:
                continue
            (yes if flag else no).append(cost(game, row, 2))
        n_true += len(yes)
        n_false += len(no)
        if yes and no:
            deltas.append(mean(yes) - mean(no))
    return BinaryEffect(label, len(deltas), n_true, n_false, median(deltas), mean(deltas))


def centered_correlation(
    games: Sequence[Game],
    x_fn: Callable[[Game, int, dict[str, Any]], Optional[float]],
    y_fn: Callable[[Game, int, dict[str, Any]], Optional[float]],
    cutoff: int = CUT_PRIMARY,
) -> tuple[float, int, int]:
    xs: list[float] = []
    ys: list[float] = []
    eligible_games = 0
    for game in games:
        pairs: list[tuple[float, float]] = []
        for index, row in enumerate(game.rows):
            if row["round"] < cutoff:
                continue
            x = x_fn(game, index, row)
            y = y_fn(game, index, row)
            if x is not None and y is not None:
                pairs.append((float(x), float(y)))
        if len(pairs) < 2:
            continue
        eligible_games += 1
        mx = mean([a for a, _ in pairs])
        my = mean([b for _, b in pairs])
        xs.extend(a - mx for a, _ in pairs)
        ys.extend(b - my for _, b in pairs)
    if not xs:
        return float("nan"), 0, eligible_games
    numerator = sum(x * y for x, y in zip(xs, ys))
    dx = math.sqrt(sum(x * x for x in xs))
    dy = math.sqrt(sum(y * y for y in ys))
    corr = numerator / (dx * dy) if dx and dy else float("nan")
    return corr, len(xs), eligible_games


def cost_bin_summary(games: Sequence[Game], pid: int) -> list[tuple[str, int, float, float]]:
    bins = (
        ("r=0", 0, 1),
        ("r=1", 1, 2),
        ("r=2-4", 2, 5),
        ("r=5-9", 5, 10),
        ("r=10-19", 10, 20),
        ("r=20-99", 20, 100),
        ("r=100-249", 100, 250),
        ("r=250-499", 250, 500),
    )
    result = []
    for label, lo, hi in bins:
        vals = [cost(g, r, pid) for g in games for r in g.rows if lo <= r["round"] < hi]
        result.append((label, len(vals), pct(vals, 50), pct(vals, 90)))
    return result


def chronological_cohorts(games: Sequence[Game]) -> list[tuple[str, list[Game]]]:
    ordered = sorted(games, key=lambda g: g.game_id)
    n = len(ordered)
    cuts = (0, n // 3, 2 * n // 3, n)
    labels = ("早期", "中期", "晚期")
    return [(labels[i], ordered[cuts[i]:cuts[i + 1]]) for i in range(3)]


def checkpoint_value(game: Game, round_count: int, pid: int, field: str) -> Optional[int]:
    if len(game.rows) < round_count:
        return None
    row = game.rows[round_count - 1]
    if field == "held":
        return held(row, pid)
    if field == "net":
        return held(row, pid) - vision(row, pid)
    raise AssertionError(field)


def held_curve(games: Sequence[Game], checkpoints: Sequence[int]) -> list[dict[str, float]]:
    result = []
    for checkpoint in checkpoints:
        triples = []
        for game in games:
            target = checkpoint_value(game, checkpoint, 2, "held")
            own = checkpoint_value(game, checkpoint, 1, "held")
            if target is not None and own is not None:
                triples.append((target, own, target - own))
        result.append({
            "round": checkpoint,
            "games": len(triples),
            "target": median([x[0] for x in triples]),
            "own": median([x[1] for x in triples]),
            "gap": median([x[2] for x in triples]),
        })
    return result


def held_window_rates(games: Sequence[Game]) -> list[dict[str, float]]:
    windows = ((0, 100), (100, 200), (200, 300), (300, 400), (400, 500))
    result = []
    for lo, hi in windows:
        target_rates: list[float] = []
        own_rates: list[float] = []
        gaps: list[float] = []
        for game in games:
            if len(game.rows) < hi:
                continue
            t0 = held(game.rows[lo - 1], 2) if lo else 0
            o0 = held(game.rows[lo - 1], 1) if lo else 0
            td = held(game.rows[hi - 1], 2) - t0
            od = held(game.rows[hi - 1], 1) - o0
            target_rates.append(td / (hi - lo))
            own_rates.append(od / (hi - lo))
            gaps.append((td - od) / (hi - lo))
        result.append({
            "label": f"{lo}-{hi - 1}",
            "games": len(target_rates),
            "target": median(target_rates),
            "own": median(own_rates),
            "gap": median(gaps),
        })
    return result


def final_score_summary(games: Sequence[Game]) -> dict[str, float]:
    complete = [g for g in games if g.complete]
    target_held = [held(g.rows[-1], 2) for g in complete]
    own_held = [held(g.rows[-1], 1) for g in complete]
    target_net = [held(g.rows[-1], 2) - vision(g.rows[-1], 2) for g in complete]
    own_net = [held(g.rows[-1], 1) - vision(g.rows[-1], 1) for g in complete]
    gaps = [a - b for a, b in zip(target_net, own_net)]
    return {
        "games": len(complete),
        "target_held": median(target_held),
        "own_held": median(own_held),
        "target_net": median(target_net),
        "own_net": median(own_net),
        "gap": median(gaps),
        "target_ahead": sum(x > 0 for x in gaps),
        "ties": sum(x == 0 for x in gaps),
    }


def vision_summary(games: Sequence[Game], pid: int) -> dict[str, Any]:
    increments = collections.Counter()
    purchase_rounds: list[int] = []
    final_spend: list[int] = []
    zero_games = 0
    by_mod5 = collections.Counter()
    by_mod20 = collections.Counter()
    for game in games:
        for i, row in enumerate(game.rows):
            delta = vision_delta(game, i, pid)
            if delta:
                increments[delta] += 1
                purchase_rounds.append(int(row["round"]))
                by_mod5[int(row["round"]) % 5] += 1
                by_mod20[int(row["round"]) % 20] += 1
        end_spend = vision(game.rows[-1], pid)
        final_spend.append(end_spend)
        zero_games += end_spend == 0
    return {
        "increments": increments,
        "events": len(purchase_rounds),
        "rounds": purchase_rounds,
        "final": final_spend,
        "zero_games": zero_games,
        "mod5": by_mod5,
        "mod20": by_mod20,
    }


def visibility_behavior(games: Sequence[Game], cutoff: int = CUT_PRIMARY) -> dict[str, Any]:
    rounds = any_visible = both_visible = 0
    endpoints = 0
    regions = collections.Counter()
    unit_regions = {0: collections.Counter(), 1: collections.Counter()}
    hotspots = 0
    center_d2 = center_d4 = 0
    observed_pickup_slots = positive_pickup_slots = pickup_sum = 0
    region_pickup_slots = collections.Counter()
    region_pickup_sum = collections.Counter()
    hotspot_pickup_slots = collections.Counter()
    action_codes = collections.Counter()
    action_lengths = collections.Counter()
    pair_lengths = collections.Counter()
    displacement = collections.Counter()
    region_transitions = 0
    consecutive_visible_pairs = 0
    complete_pickup_rounds = positive_complete_rounds = complete_pickup_sum = 0

    for game in games:
        hotspot_set = {
            (r, c) for r, row in enumerate(game.map_rows) for c, token in enumerate(row) if token == "2"
        }
        previous_positions: list[Optional[tuple[int, int]]] = [None, None]
        previous_round = -2
        for row in game.rows:
            rno = int(row["round"])
            positions = unit_positions(row, 2)
            if rno < cutoff:
                previous_positions = positions
                previous_round = rno
                continue
            rounds += 1
            visible_count = sum(pos is not None for pos in positions)
            any_visible += visible_count > 0
            both_visible += visible_count == 2
            pickups, pickup_complete = unit_pickups(row, 2)
            if pickup_complete:
                complete_pickup_rounds += 1
                amount = sum(pickups)
                complete_pickup_sum += amount
                positive_complete_rounds += amount > 0
            units = player(row["end"], 2).get("units", [])
            lengths: list[Optional[int]] = []
            for index, unit in enumerate(units):
                pos_raw = unit.get("position")
                pos = tuple(pos_raw) if pos_raw is not None else None
                if "actions" in unit:
                    actions = [int(x) for x in unit.get("actions", [])]
                    action_lengths[(index, len(actions))] += 1
                    action_codes.update(actions)
                    lengths.append(len(actions))
                else:
                    lengths.append(None)
                if pos is None:
                    continue
                endpoints += 1
                rid = region_id(*pos)
                regions[rid] += 1
                unit_regions[index][rid] += 1
                on_hotspot = pos in hotspot_set
                hotspots += on_hotspot
                d = max(abs(pos[0] - 8), abs(pos[1] - 8))
                center_d2 += d <= 2
                center_d4 += d <= 4
                if "pickup" in unit:
                    amount = int(unit["pickup"])
                    observed_pickup_slots += 1
                    positive_pickup_slots += amount > 0
                    pickup_sum += amount
                    region_pickup_slots[rid] += 1
                    region_pickup_sum[rid] += amount
                    hotspot_pickup_slots["hot" if on_hotspot else "other"] += amount
                if previous_round == rno - 1 and previous_positions[index] is not None:
                    old = previous_positions[index]
                    assert old is not None
                    manhattan = abs(pos[0] - old[0]) + abs(pos[1] - old[1])
                    displacement[manhattan] += 1
                    consecutive_visible_pairs += 1
                    region_transitions += region_id(*old) != rid
            if len(lengths) == 2 and lengths[0] is not None and lengths[1] is not None:
                pair_lengths[(lengths[0], lengths[1])] += 1
            previous_positions = positions
            previous_round = rno
    return {
        "rounds": rounds,
        "any_visible": any_visible,
        "both_visible": both_visible,
        "endpoints": endpoints,
        "regions": regions,
        "unit_regions": unit_regions,
        "hotspots": hotspots,
        "center_d2": center_d2,
        "center_d4": center_d4,
        "observed_pickup_slots": observed_pickup_slots,
        "positive_pickup_slots": positive_pickup_slots,
        "pickup_sum": pickup_sum,
        "region_pickup_slots": region_pickup_slots,
        "region_pickup_sum": region_pickup_sum,
        "hotspot_pickup_sum": hotspot_pickup_slots,
        "actions": action_codes,
        "action_lengths": action_lengths,
        "pair_lengths": pair_lengths,
        "displacement": displacement,
        "consecutive_visible_pairs": consecutive_visible_pairs,
        "region_transitions": region_transitions,
        "complete_pickup_rounds": complete_pickup_rounds,
        "positive_complete_rounds": positive_complete_rounds,
        "complete_pickup_sum": complete_pickup_sum,
    }


def phase_position_behavior(
    games: Sequence[Game], phase: str = "start", cutoff: int = 0,
) -> dict[str, Any]:
    rounds = any_visible = both_visible = endpoints = hotspots = center_d2 = center_d4 = 0
    regions = collections.Counter()
    for game in games:
        hotspot_set = {
            (r, c) for r, map_row in enumerate(game.map_rows)
            for c, token in enumerate(map_row) if token == "2"
        }
        for row in game.rows:
            if row["round"] < cutoff:
                continue
            units = player(row[phase], 2).get("units", [])
            positions = [tuple(u["position"]) if u.get("position") is not None else None for u in units]
            rounds += 1
            visible = sum(pos is not None for pos in positions)
            any_visible += visible > 0
            both_visible += visible == 2
            for pos in positions:
                if pos is None:
                    continue
                endpoints += 1
                regions[region_id(*pos)] += 1
                hotspots += pos in hotspot_set
                distance = max(abs(pos[0] - 8), abs(pos[1] - 8))
                center_d2 += distance <= 2
                center_d4 += distance <= 4
    return {
        "rounds": rounds,
        "any_visible": any_visible,
        "both_visible": both_visible,
        "endpoints": endpoints,
        "regions": regions,
        "hotspots": hotspots,
        "center_d2": center_d2,
        "center_d4": center_d4,
    }


def run_lengths(flags: Sequence[bool]) -> list[tuple[int, int, bool]]:
    result: list[tuple[int, int, bool]] = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        if start is not None and (not flag or index == len(flags) - 1):
            end = index if flag and index == len(flags) - 1 else index - 1
            closed = start > 0 and end + 1 < len(flags) and not flags[start - 1] and not flags[end + 1]
            result.append((start, end, closed))
            start = None
    return result


def excursion_summary(games: Sequence[Game], cutoff: int = CUT_PRIMARY) -> dict[str, Any]:
    hidden_lengths: list[int] = []
    closed_hidden_lengths: list[int] = []
    hidden_6_11_by_game: list[int] = []
    outer_lengths: list[int] = []
    closed_outer_lengths: list[int] = []
    outer_6_11_by_game: list[int] = []
    hidden_unit_deltas: list[float] = []
    visible_unit_deltas: list[float] = []
    per_game_hidden_visible_delta: list[float] = []

    for game in games:
        game_hidden_6_11 = 0
        game_outer_6_11 = 0
        game_hidden_changes: list[float] = []
        game_visible_changes: list[float] = []
        for unit_index in (0, 1):
            subset = [row for row in game.rows if row["round"] >= cutoff]
            positions = [unit_positions(row, 2)[unit_index] for row in subset]
            hidden_flags = [pos is None for pos in positions]
            outer_flags = [pos is not None and region_id(*pos) != 1 for pos in positions]
            for start, end, closed in run_lengths(hidden_flags):
                length = end - start + 1
                hidden_lengths.append(length)
                if closed:
                    closed_hidden_lengths.append(length)
                    game_hidden_6_11 += 6 <= length <= 11
            for start, end, closed in run_lengths(outer_flags):
                length = end - start + 1
                outer_lengths.append(length)
                if closed:
                    closed_outer_lengths.append(length)
                    game_outer_6_11 += 6 <= length <= 11
            previous_gold: Optional[int] = None
            for row, hidden_flag in zip(subset, hidden_flags):
                units = player(row["end"], 2).get("units", [])
                current_gold = int(units[unit_index].get("gold", 0))
                if previous_gold is not None:
                    delta = current_gold - previous_gold
                    if hidden_flag:
                        hidden_unit_deltas.append(delta)
                        game_hidden_changes.append(delta)
                    else:
                        visible_unit_deltas.append(delta)
                        game_visible_changes.append(delta)
                previous_gold = current_gold
        hidden_6_11_by_game.append(game_hidden_6_11)
        outer_6_11_by_game.append(game_outer_6_11)
        if game_hidden_changes and game_visible_changes:
            per_game_hidden_visible_delta.append(mean(game_hidden_changes) - mean(game_visible_changes))
    return {
        "hidden_lengths": hidden_lengths,
        "closed_hidden_lengths": closed_hidden_lengths,
        "hidden_6_11_by_game": hidden_6_11_by_game,
        "outer_lengths": outer_lengths,
        "closed_outer_lengths": closed_outer_lengths,
        "outer_6_11_by_game": outer_6_11_by_game,
        "hidden_unit_deltas": hidden_unit_deltas,
        "visible_unit_deltas": visible_unit_deltas,
        "per_game_hidden_visible_delta": per_game_hidden_visible_delta,
    }


def exact_version_rows(games: Sequence[Game]) -> list[dict[str, Any]]:
    groups: dict[str, list[Game]] = collections.defaultdict(list)
    for game in games:
        groups[game.own_version].append(game)
    output = []
    for version, group in sorted(groups.items(), key=lambda item: min(g.game_id for g in item[1])):
        tc = pooled_costs(group, 2, CUT_PRIMARY)
        oc = pooled_costs(group, 1, CUT_PRIMARY)
        complete = [g for g in group if g.complete]
        gaps = [
            (held(g.rows[-1], 2) - vision(g.rows[-1], 2))
            - (held(g.rows[-1], 1) - vision(g.rows[-1], 1))
            for g in complete
        ]
        output.append({
            "version": version,
            "ids": ",".join(str(g.game_id) for g in sorted(group, key=lambda g: g.game_id)),
            "games": len(group),
            "rounds": sum(len(rows_after(g, CUT_PRIMARY)) for g in group),
            "target_p50": pct(tc, 50),
            "target_p90": pct(tc, 90),
            "own_p50": pct(oc, 50),
            "cost_gap": median([a - b for a, b in zip(tc, oc)]),
            "net_gap": median(gaps),
        })
    return output


def render_counter(counter: collections.Counter[Any], limit: Optional[int] = None) -> str:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    if limit is not None:
        items = items[:limit]
    return ", ".join(f"{key}:{value}" for key, value in items) if items else "无"


def append_table(lines: list[str], headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.append("")


def team_short(team: str) -> str:
    return "T-1" if team == "Tiuntled-1" else "Tundra"


def render_report(corpus: dict[str, list[Game]], probes: dict[str, list[Game]], metadata: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.extend([
        "# Tiuntled-1 / Tundra-wawa 对手逆向报告",
        "",
        "> 本文由 `sim/analyze_opponents.py` 从固定的 88 份历史日志确定性生成，并把 4 份高可见率/不争金探针作为独立验证集；探针绝不混入历史主分布。没有联网、没有提交对局；所有百分位采用与 `tools/gamelog.py` 相同的 `floor(n*p/100)` 经验索引。",
        "",
        "## 0. 结论先行与证据等级",
        "",
        "- **测量事实**：直接来自日志字段或其确定性差分；表格默认主口径为稳态 `r>=20`。",
        "- **受条件测量**：对手位置、动作、`pickup` 只在我方视野过滤后出现，结论只代表“被我方看见”的子样本，不能外推全局路线或总拾取。",
        "- **机制推断**：由多项测量共同支持，但没有对手源码，均不是实现真值。特别是原始输出里的 `k`、`order`、被墙改写前动作和目标函数无法从过滤日志唯一恢复。",
        "- **核心画像**：历史主分布 T-1 约 200ns、Tundra 约 260ns；独立轻对手探针为 T-1 200/220ns、Tundra 270ns P50。两者稳态都有明显量化台阶且远比开局稳定。高可见探针表明历史中心占比确受近身视野选择偏倚；不争金净分则给出其策略上限锚点，而不是历史对撞收入的替代。",
        "",
        "## 1. 语料、断言与复现",
        "",
    ])
    corpus_rows = []
    for team, games in corpus.items():
        spec = TEAM_SPECS[team]
        maps = collections.Counter(g.map_name for g in games)
        corpus_rows.append((
            f"{team} / {spec['account']}", len(games), sum(len(g.rows) for g in games),
            sum(g.complete for g in games), len(games) - sum(g.complete for g in games),
            ", ".join(f"{k}:{v}" for k, v in sorted(maps.items())), "player2",
        ))
    append_table(lines, ["目标", "日志", "有效轮", "完整", "截尾", "地图", "目标座位"], corpus_rows)
    probe_rows = []
    for team, games in probes.items():
        for game in games:
            entry = game.manifest_entry
            start_vis = entry["opponent_visibility"]["start"]
            observed_net = held(game.rows[-1], 2) - vision(game.rows[-1], 2)
            if observed_net != entry["opponent_uncontested_net"]:
                raise AssertionError((game.game_id, observed_net, entry["opponent_uncontested_net"]))
            values = [cost(game, row, 2) for row in game.rows]
            probe_rows.append((
                team_short(team), game.game_id, game.map_name, len(game.rows),
                fmt_num(pct(values, 50)), fmt_num(pct(values, 90)), observed_net,
                fmt_rate(start_vis["visible_rounds"], start_vis["rounds"]),
                fmt_rate(start_vis["both_visible_rounds"], start_vis["rounds"]),
            ))
    lines.extend(["**独立 probeobs 探针（不混入上表或后文历史主分布）**", ""])
    append_table(lines, ["目标", "game_id", "地图", "轮", "目标P50", "P90", "不争金净分", "start至少一敌可见", "start双敌可见"], probe_rows)
    lines.extend([
        "脚本内固定 88 个历史 `game_id`，并逐项断言：首行名字、manifest 账号、我方/player1、目标/player2、轮号连续、有效轮数、完整/截尾数和地图混合。另固定 4 个 `probeobs` ID，逐项断言 500 轮、可见率元数据与 `opponent_uncontested_net`。manifest 其他后来新增日志不会悄悄改变任一口径。",
        "",
        "复现命令：",
        "",
        "```bash",
        "python3 sim/analyze_opponents.py --repo . --output sim/OPPONENTS.md",
        "python3 sim/analyze_opponents.py --repo . --check sim/OPPONENTS.md",
        "```",
        "",
        "输入只读 `logs/opponents/manifest.json` 与固定日志；输出只写指定 Markdown。报告不嵌入生成时间，因此同一语料逐字节稳定。",
        "",
        "## 2. 延迟分布、warmup 与 cutoff 敏感性",
        "",
    ])

    for team, games in corpus.items():
        label = team_short(team)
        lines.append(f"### 2.{1 if team == 'Tiuntled-1' else 2} {label}")
        lines.append("")
        values = pooled_costs(games, 2, CUT_PRIMARY)
        own_values = pooled_costs(games, 1, CUT_PRIMARY)
        append_table(
            lines,
            ["口径", "n", *[f"P{p}" for p in PCTS]],
            [
                [f"{label} r>=20", len(values), *percentile_row(values)],
                ["同局我方 r>=20", len(own_values), *percentile_row(own_values)],
            ],
        )
        append_table(
            lines,
            ["轮段", "n", f"{label} P50", f"{label} P90"],
            [[name, n, fmt_num(p50), fmt_num(p90)] for name, n, p50, p90 in cost_bin_summary(games, 2)],
        )
        sensitivity_rows = []
        for cutoff in CUT_SENSITIVITY:
            tv = pooled_costs(games, 2, cutoff)
            ov = pooled_costs(games, 1, cutoff)
            diffs = same_round_diffs(games, cutoff)
            first, dispatch_total = target_first_count(games, cutoff)
            sensitivity_rows.append((
                f"r>={cutoff}", len(tv), fmt_num(pct(tv, 50)), fmt_num(pct(tv, 90)),
                fmt_num(pct(tv, 99)), fmt_num(pct(ov, 50)), fmt_signed(median(diffs)),
                fmt_rate(sum(d < 0 for d in diffs), len(diffs)), fmt_rate(first, dispatch_total),
            ))
        append_table(
            lines,
            ["cutoff", "轮", "目标P50", "目标P90", "目标P99", "我方P50", "逐轮目标-我方中位", "目标更快", "dispatch目标先动"],
            sensitivity_rows,
        )
        pre = [cost(g, row, 2) for g in games for row in g.rows if row["round"] < 20]
        post = values
        game_p50s = per_game_p50(games, 2, CUT_PRIMARY)
        spreads = []
        for g in games:
            gv = [cost(g, row, 2) for row in rows_after(g, CUT_PRIMARY)]
            spreads.append(pct(gv, 90) - pct(gv, 50))
        modes = collections.Counter(post)
        lines.extend([
            f"**warmup 前后。** `r<20` 共 {len(pre)} 轮，P50/P90={fmt_num(pct(pre,50))}/{fmt_num(pct(pre,90))}ns；`r>=20` 共 {len(post)} 轮，为 {fmt_num(pct(post,50))}/{fmt_num(pct(post,90))}ns。开局高成本不能混入稳态实现预算。",
            f"**稳定性。** 每局稳态 P50 的跨局 P10/P50/P90={fmt_num(pct(game_p50s,10))}/{fmt_num(pct(game_p50s,50))}/{fmt_num(pct(game_p50s,90))}ns；每局 `(P90-P50)` 中位 {fmt_num(median(spreads))}ns。稳态最常见成本台阶：{render_counter(modes, 8)}。这描述量化分布，不等于 CPU 周期或源码分支。",
            "",
        ])

    lines.extend([
        "### 2.3 独立轻对手探针锚点",
        "",
    ])
    probe_timing_rows = []
    for team, games in probes.items():
        for game in games:
            all_costs = [cost(game, row, 2) for row in game.rows]
            steady_costs = [cost(game, row, 2) for row in rows_after(game, CUT_PRIMARY)]
            probe_timing_rows.append((team_short(team), game.game_id, fmt_num(pct(all_costs, 50)),
                                      fmt_num(pct(all_costs, 90)), fmt_num(pct(steady_costs, 50)),
                                      fmt_num(pct(steady_costs, 90)), game.manifest_entry["opponent_uncontested_net"]))
    append_table(lines, ["目标", "game_id", "全局P50", "全局P90", "稳态P50", "稳态P90", "不争金净分"], probe_timing_rows)
    lines.extend([
        "Tundra `171687` 是指定单局锚点：净分 2537、全局 P50/P90=270/350ns；第二局为 2864、270/360ns。它们使用极慢且不争金的 `probeobs`，应与 CHANGELOG 现役收入中位约 1515 对照理解为**对手不竞争时的上限锚点**，不是可直接相减的同版本 A/B。T-1 两局同类锚点净分 2567/2344。",
        "",
        "`src/INFRA.md §3` 明确记录“对手越重，我方读数越高约 10-30ns”的污染方向；轻 probeobs 减少的是目标受到的对手污染，因此历史重对手局的目标 cost **可能**偏高。但本探针 Tundra P50=270 并未低于历史池化 260，说明版本/窗口差异足以盖过该方向，不能拿 10-30ns 机械回扣。",
        "",
        "### 2.4 同窗比较与时间漂移",
        "",
        "同一回合的目标与我方 cost 共用评测窗口，故差值比跨局绝对数更抗窗口漂移；但我方版本不断变化，不能把差值全归因于目标版本。",
        "",
    ])
    cohort_rows = []
    for team, games in corpus.items():
        for name, group in chronological_cohorts(games):
            tv = pooled_costs(group, 2, CUT_PRIMARY)
            ov = pooled_costs(group, 1, CUT_PRIMARY)
            diffs = same_round_diffs(group, CUT_PRIMARY)
            cohort_rows.append((
                team_short(team), name, f"{min(g.game_id for g in group)}-{max(g.game_id for g in group)}",
                len(group), fmt_num(pct(tv, 50)), fmt_num(pct(tv, 90)), fmt_num(pct(ov, 50)),
                fmt_signed(median(diffs)), fmt_rate(sum(d < 0 for d in diffs), len(diffs)),
            ))
    append_table(lines, ["目标", "时序段", "game_id范围", "局", "目标P50", "目标P90", "我方P50", "目标-我方", "目标更快"], cohort_rows)

    lines.extend([
        "下面按 `game_id` 排序逐局展示漂移；`C`=完整、`X`=截尾。`净差`只在完整局给出，定义为 `(目标持金-目标视野费)-(我方持金-我方视野费)`。",
        "",
        "```text",
        "目标    game_id  状态/轮  map   我方精确版本       目标P50/P90  我方P50  逐轮cost差  净差",
    ])
    for team, games in corpus.items():
        for g in sorted(games, key=lambda item: item.game_id):
            tv = [cost(g, r, 2) for r in rows_after(g, CUT_PRIMARY)]
            ov = [cost(g, r, 1) for r in rows_after(g, CUT_PRIMARY)]
            diffs = [a - b for a, b in zip(tv, ov)]
            net_gap = "NA"
            if g.complete:
                ng = held(g.rows[-1], 2) - vision(g.rows[-1], 2) - held(g.rows[-1], 1) + vision(g.rows[-1], 1)
                net_gap = f"{ng:+d}"
            lines.append(
                f"{team_short(team):<8} {g.game_id:<7} {'C' if g.complete else 'X'}/{len(g.rows):<3} "
                f"{g.map_name:<5} {g.own_version:<18.18} {fmt_num(pct(tv,50)):>4}/{fmt_num(pct(tv,90)):<4} "
                f"{fmt_num(pct(ov,50)):>7} {fmt_signed(median(diffs)):>11} {net_gap:>6}"
            )
    lines.extend(["```", ""])

    lines.extend([
        "### 2.5 按我方精确 player1 版本分组",
        "",
        "不把相似名字合并；几乎所有组是单例，所以这些行是描述性同窗记录，不是可重复的版本因果估计。",
        "",
    ])
    for team, games in corpus.items():
        lines.append(f"**{team_short(team)}**")
        lines.append("")
        append_table(
            lines,
            ["我方精确版本", "game_id", "局", "稳态轮", "目标P50/P90", "我方P50", "逐轮cost差", "完整局净差中位"],
            [
                [r["version"], r["ids"], r["games"], r["rounds"],
                 f"{fmt_num(r['target_p50'])}/{fmt_num(r['target_p90'])}", fmt_num(r["own_p50"]),
                 fmt_signed(r["cost_gap"]), fmt_signed(r["net_gap"])]
                for r in exact_version_rows(games)
            ],
        )

    lines.extend([
        "## 3. 回合特征、相关性与指令预算",
        "",
        "### 3.1 within-game 特征效应",
        "",
        "每个二元特征先在**同一局内**计算 `mean(cost|特征=1)-mean(cost|特征=0)`，再跨局汇总；这样去除了每局窗口基线。`pickup` 只使用目标两单位在 `end.players` 都带 `pickup` 的完整可见轮；位置可见性本身由我方路线/视野决定。",
        "",
    ])
    effect_rows = []
    corr_rows = []
    for team, games in corpus.items():
        effects = [
            binary_effect(games, "快照轮(r%5=0,r>0)", lambda g, i, r: r["round"] > 0 and r["round"] % 5 == 0),
            binary_effect(games, "炸弹波轮(r%20=0)", lambda g, i, r: r["round"] % 20 == 0),
            binary_effect(games, "目标本轮买视野", lambda g, i, r: vision_delta(g, i, 2) > 0),
            binary_effect(games, "至少一单位位置可见", lambda g, i, r: any(p is not None for p in unit_positions(r, 2))),
            binary_effect(games, "两单位pickup完整且>0", lambda g, i, r: (sum(unit_pickups(r, 2)[0]) > 0) if unit_pickups(r, 2)[1] else None),
            binary_effect(games, "我方本轮有pickup", lambda g, i, r: sum(unit_pickups(r, 1)[0]) > 0),
        ]
        for effect in effects:
            effect_rows.append((team_short(team), effect.label, effect.games, effect.n_true, effect.n_false,
                                fmt_signed(effect.median_delta), fmt_signed(effect.mean_delta)))
        correlations = [
            ("目标cost ~ 我方cost", lambda g, i, r: cost(g, r, 2), lambda g, i, r: cost(g, r, 1)),
            ("目标cost ~ round", lambda g, i, r: cost(g, r, 2), lambda g, i, r: float(r["round"])),
            ("目标cost ~ 我方pickup", lambda g, i, r: cost(g, r, 2), lambda g, i, r: float(sum(unit_pickups(r, 1)[0]))),
            ("目标cost ~ 目标可见pickup", lambda g, i, r: cost(g, r, 2),
             lambda g, i, r: float(sum(unit_pickups(r, 2)[0])) if unit_pickups(r, 2)[1] else None),
        ]
        for name, x_fn, y_fn in correlations:
            corr, n, eligible = centered_correlation(games, x_fn, y_fn)
            corr_rows.append((team_short(team), name, n, eligible, f"{corr:.3f}" if not math.isnan(corr) else "NA"))
    append_table(lines, ["目标", "特征", "可配对局", "特征1轮", "特征0轮", "局内差中位(ns)", "局内差均值(ns)"], effect_rows)
    lines.extend([
        "相关系数同样先按局中心化再合并，只说明线性共变，不说明因果；`目标cost ~ 我方cost` 可视为同窗污染/量化共同变化的诊断。",
        "",
    ])
    append_table(lines, ["目标", "相关", "轮", "局", "局内中心化 Pearson r"], corr_rows)

    lines.extend([
        "### 3.2 成本模型与可装指令量",
        "",
        "严格采用 `src/INFRA.md` 的已标定公式：`cost ≈ 40ns 壳 + 2ns×输入行载荷条数 + 0.2ns×执行指令`，另有分支位点、`.text`、寄存器压力和对手污染等罚则。INFRA 明确给出现役完整双管线示例使用 **10 条输入行载荷**，并给出 659 指令对应约 192ns；这里不凭空假定对手一定也读取 10 条。",
        "",
        "因此给两种透明口径：`I0=(P50-40)/0.2` 是零载荷的宽松上界；`I10=(P50-40-20)/0.2` 是按文档 10 行载荷的等效指令数。它们都是**等效预算**，不是反汇编计数；未知载荷和罚则使原始指令数不可辨识。",
        "",
    ])
    budget_rows = []
    for team, games in corpus.items():
        p50 = pct(pooled_costs(games, 2, CUT_PRIMARY), 50)
        p90 = pct(pooled_costs(games, 2, CUT_PRIMARY), 90)
        budget_rows.append((
            team_short(team), fmt_num(p50), fmt_num(p90),
            fmt_num(max(0, (p50 - 40) / 0.2)), fmt_num(max(0, (p50 - 60) / 0.2)),
            "190ns阈值对应I10≈650" if team == "Tiuntled-1" else "历史260ns:I10≈1000；290ns:I10≈1150",
        ))
    append_table(lines, ["目标", "稳态P50", "P90", "I0上界", "I10等效", "INFRA预算表对照"], budget_rows)
    lines.extend([
        "**推断边界：** 以历史 P50 200→260 的实测差计，Tundra 比 T-1 约多 300 条 I10 等效空间；以轻探针 200→270 计约 350 条；INFRA 的 200→290 预算档才是 +450。日志不能判断预算用于扫描、路径、记忆还是恒形化。CHANGELOG 的“~750 指令”是历史工程推断，本报告只能给成本包络，不能从 cost 唯一恢复指令数。",
        "",
        "## 4. 持金曲线、同局差与 pickup 可见性",
        "",
        "`end.players[].gold` 是当前持金，可能已被炸弹扣减；下表称**持金**而不是“毛收入”。目标隐藏时 `pickup` 字段缺失，故绝不以可见 pickup 外推总收入。净分只用 `最终持金-累计 vision_spent`。",
        "",
    ])
    curve_rows = []
    rate_rows = []
    final_rows = []
    for team, games in corpus.items():
        for row in held_curve(games, (20, 50, 100, 200, 300, 400, 500)):
            curve_rows.append((team_short(team), row["round"], row["games"], fmt_num(row["target"]), fmt_num(row["own"]), fmt_signed(row["gap"])))
        for row in held_window_rates(games):
            rate_rows.append((team_short(team), row["label"], row["games"], f"{row['target']:.2f}", f"{row['own']:.2f}", fmt_signed(row["gap"], 2)))
        summary = final_score_summary(games)
        final_rows.append((team_short(team), int(summary["games"]), fmt_num(summary["target_held"]), fmt_num(summary["own_held"]),
                           fmt_num(summary["target_net"]), fmt_num(summary["own_net"]), fmt_signed(summary["gap"]),
                           f"{int(summary['target_ahead'])}/{int(summary['games'])}"))
    append_table(lines, ["目标", "轮末", "到达样本局", "目标持金中位", "我方持金中位", "同局持金差中位"], curve_rows)
    lines.extend([
        "完整 100 轮窗口的“持金净变化/轮”如下；负的单轮差可能包含烧损，不能解释成负 pickup。",
        "",
    ])
    append_table(lines, ["目标", "窗口", "完整窗口局", "目标持金变化/轮", "我方", "同局差"], rate_rows)
    append_table(lines, ["目标", "完整局", "目标终局持金", "我方持金", "目标净分", "我方净分", "同局净差中位", "目标净分领先"], final_rows)

    lines.extend([
        "## 5. 视野购买指纹",
        "",
        "购买量由相邻轮 `end.players[target].vision_spent` 的累计值差分，不能直接读某一轮的累计值当本轮购买。`+2/+3` 分别对应文档里的 vp=1/vp=2 费用；购买对下一轮生效。",
        "",
    ])
    vision_rows = []
    mod_rows = []
    for team, games in corpus.items():
        vs = vision_summary(games, 2)
        vision_rows.append((
            team_short(team), len(games), vs["events"], render_counter(vs["increments"]),
            vs["zero_games"], fmt_num(median(vs["final"])), fmt_num(pct(vs["final"], 90)),
            fmt_rate(vs["mod5"][0], vs["events"]), fmt_rate(vs["mod20"][0], vs["events"]),
        ))
        mod_rows.append((team_short(team), render_counter(vs["mod5"]), render_counter(vs["mod20"])))
    append_table(lines, ["目标", "局", "购买事件", "增量分布", "零购买局", "终局费中位", "P90", "事件落r%5=0", "落r%20=0"], vision_rows)
    append_table(lines, ["目标", "购买轮 mod5", "购买轮 mod20"], mod_rows)
    lines.extend([
        "历史 88 局两目标均为 **0 次购买、0 视野费**，这是清晰的负指纹：现有版本不依赖付费扩视野。它不排除对手使用免费默认窗或全局快照，也不保证未来版本不变。",
        "",
        "## 6. 可见性条件下的五区、中心与热点行为",
        "",
        "五区使用 `sim/calibrate_views.py` 已验证的风车划分：中心 `[4..12]×[4..12]`，其余为上/左/下/右臂。热点直接取每局日志第二行 token `2`，不把它误称炸弹位。端点位置来自目标 `end.players[].units[].position`；不可见为 `null`，不填补。",
        "",
    ])
    start_space_rows = []
    for team in corpus:
        for sample_name, games in (("历史88主样本", corpus[team]), ("高可见probe", probes[team])):
            pb = phase_position_behavior(games, "start", 0)
            start_space_rows.append((
                team_short(team), sample_name, len(games), pb["rounds"],
                fmt_rate(pb["any_visible"], pb["rounds"]),
                fmt_rate(pb["rounds"] - pb["any_visible"], pb["rounds"]),
                fmt_rate(pb["both_visible"], pb["rounds"]), pb["endpoints"],
                fmt_rate(pb["regions"][1], pb["endpoints"]),
                fmt_rate(pb["center_d2"], pb["endpoints"]),
                fmt_rate(pb["hotspots"], pb["endpoints"]),
            ))
    append_table(lines, ["目标", "样本", "局", "轮", "start至少一敌可见", "全不可见", "双敌可见", "位置端点", "中心R1", "d<=2", "热点"], start_space_rows)
    probe_region_rows = []
    for team, games in probes.items():
        pb = phase_position_behavior(games, "start", 0)
        for rid in range(1, 6):
            probe_region_rows.append((team_short(team), f"R{rid} {REGION_NAMES[rid]}",
                                      pb["regions"][rid], fmt_rate(pb["regions"][rid], pb["endpoints"])))
    append_table(lines, ["高可见probe目标", "start区域", "端点", "占比"], probe_region_rows)
    lines.extend([
        "历史样本每队约 56.7% 的轮至少看见一名敌人，等价于约 **43.3%（约44%）整轮完全看不见目标**，且单单位端点覆盖更低，空间统计有强近身选择偏倚。probe 的逐局至少一敌可见率为 Tundra 92.8%/94.4%、T-1 85.2%/87.2%，双敌为 49.8%/60.4% 与 31.4%/37.6%；它显著接近无偏，但仍非全信息。故区域/热点机制优先看 probe，历史表只用于对撞上下文。",
        "",
    ])
    visibility_rows = []
    region_rows = []
    pickup_region_rows = []
    behavior_by_team: dict[str, dict[str, Any]] = {}
    for team, games in corpus.items():
        b = visibility_behavior(games)
        behavior_by_team[team] = b
        visibility_rows.append((
            team_short(team), b["rounds"], fmt_rate(b["any_visible"], b["rounds"]),
            fmt_rate(b["both_visible"], b["rounds"]), b["endpoints"],
            fmt_rate(b["center_d2"], b["endpoints"]), fmt_rate(b["center_d4"], b["endpoints"]),
            fmt_rate(b["hotspots"], b["endpoints"]),
            f"{b['complete_pickup_rounds']}/{b['rounds']}",
            fmt_rate(b["positive_complete_rounds"], b["complete_pickup_rounds"]),
        ))
        for rid in range(1, 6):
            slots = b["region_pickup_slots"][rid]
            region_rows.append((team_short(team), f"R{rid} {REGION_NAMES[rid]}", b["regions"][rid],
                                fmt_rate(b["regions"][rid], b["endpoints"]),
                                fmt_rate(b["unit_regions"][0][rid], sum(b["unit_regions"][0].values())),
                                fmt_rate(b["unit_regions"][1][rid], sum(b["unit_regions"][1].values()))))
            pickup_region_rows.append((team_short(team), f"R{rid} {REGION_NAMES[rid]}", slots,
                                       fmt_rate(sum(1 for _ in []) , 1) if False else
                                       (f"{b['region_pickup_sum'][rid] / slots:.2f}" if slots else "NA"),
                                       b["region_pickup_sum"][rid]))
    append_table(lines, ["目标", "稳态轮", "至少一单位可见", "两单位可见", "可见端点", "端点d<=2", "端点d<=4", "端点在热点", "pickup完整轮", "完整轮pickup>0"], visibility_rows)
    append_table(lines, ["目标", "区域", "可见端点", "占比", "unit0占比", "unit1占比"], region_rows)
    lines.extend([
        "下表的 pickup 是“该单位终点可见时，日志附带的本轮 pickup”；它可能包含该单位本轮早先在视野外的动作，故只能按**可见终点条件**描述，不能解释为区域真实产率。",
        "",
    ])
    append_table(lines, ["目标", "可见终点区域", "单位轮", "pickup/单位轮", "pickup和"], pickup_region_rows)
    for team, b in behavior_by_team.items():
        hot = b["hotspot_pickup_sum"]["hot"]
        other = b["hotspot_pickup_sum"]["other"]
        lines.append(
            f"- **{team_short(team)}**：可见热点终点 {b['hotspots']}/{b['endpoints']}；热点终点可见 pickup 和 {hot}，其他终点 {other}。由于热点位于外臂且我方通常在中心，这个比例受可见选择偏差向下压，不能据此否定热点利用。"
        )
    lines.append("")

    lines.extend([
        "## 7. 动作痕迹、移动限制与 T-1 远征旧说复核",
        "",
        "### 7.1 可见动作与位移",
        "",
        "日志中的 `actions` 是引擎执行后记录；被阻挡动作会记成 4，而主动 STAY 也可能是 4，因此 code 4 不是纯碰墙率。仅在字段存在时计数。连续两轮都可见时，端点曼哈顿位移是净位移，不是路程。",
        "",
    ])
    movement_rows = []
    probe_behavior_by_team = {team: visibility_behavior(games, 0) for team, games in probes.items()}
    for sample_name, source in (("历史", behavior_by_team), ("高可见probe", probe_behavior_by_team)):
        for team, b in source.items():
            action_total = sum(b["actions"].values())
            displacement_total = sum(b["displacement"].values())
            movement_rows.append((
                team_short(team), sample_name, action_total, render_counter(b["actions"]),
                fmt_rate(b["actions"][4], action_total), render_counter(b["pair_lengths"], 8),
                b["consecutive_visible_pairs"], render_counter(b["displacement"], 8),
                fmt_rate(b["region_transitions"], displacement_total),
            ))
    append_table(lines, ["目标", "样本", "可见动作槽", "动作码分布", "code4", "双单位可见长度对", "连续可见端点对", "净位移分布", "跨区率"], movement_rows)
    lines.extend([
        "**可测限制。** 两单位共享 6 步、墙与碰撞会把实际动作改成 4；净位移显著小于动作槽数可由折返、阻挡或主动驻留造成，三者不可唯一分解。双单位同时可见时的动作长度对可给出局部切分痕迹，但过滤、执行改写和缺失轮使我们无法恢复全局原始 `k`；日志也没有玩家内部 `order`，只能看到整方的 `dispatch_order`，所以原始 `k/order` 策略不可能唯一逆向。",
        "",
        "### 7.2 “T-1 每局 5 次、6-11 轮间歇远征，隐身 4.81/轮”复核",
        "",
        "为避免把不可见直接等同外圈，使用两个分开的代理：",
        "",
        "1. **闭合不可见段**：同一目标单位在稳态连续 `position=null`，且段前段后都可见；这是相对我方视野的离镜，不是外圈真值。",
        "2. **闭合可见外区段**：连续可见且终点位于 R2-R5；这是外区真值子样本，但长途段容易因离镜被截断。",
        "",
        "单位持金逐轮始终可见，所以可比较 hidden/visible 的持金净变化；该变化含炸弹烧损，不等于 pickup 或毛收入。",
        "",
    ])
    excursion_by_team: dict[str, dict[str, Any]] = {}
    probe_excursion_by_team: dict[str, dict[str, Any]] = {}
    excursion_rows = []
    for sample_name, source, target_store in (
        ("历史", corpus, excursion_by_team), ("高可见probe", probes, probe_excursion_by_team),
    ):
        for team, games in source.items():
            ex = excursion_summary(games)
            target_store[team] = ex
            excursion_rows.append((
                team_short(team), sample_name, len(ex["closed_hidden_lengths"]),
                fmt_num(median(ex["closed_hidden_lengths"])), fmt_num(pct(ex["closed_hidden_lengths"], 90)),
                fmt_num(median(ex["hidden_6_11_by_game"])), fmt_num(pct(ex["hidden_6_11_by_game"], 90)),
                len(ex["closed_outer_lengths"]), fmt_num(median(ex["closed_outer_lengths"])),
                fmt_num(median(ex["outer_6_11_by_game"])),
                f"{mean(ex['hidden_unit_deltas']):.3f}", f"{mean(ex['visible_unit_deltas']):.3f}",
                fmt_signed(median(ex["per_game_hidden_visible_delta"]), 3),
            ))
    append_table(lines, ["目标", "样本", "闭合hidden段", "长度中位", "长度P90", "每局6-11 hidden段中位", "P90", "闭合可见外区段", "长度中位", "每局6-11外区段中位", "hidden持金Δ/单位轮", "visible持金Δ/单位轮", "局内hidden-visible中位"], excursion_rows)
    t1_ex = excursion_by_team["Tiuntled-1"]
    t1_probe_ex = probe_excursion_by_team["Tiuntled-1"]
    t1_hidden_count = median(t1_ex["hidden_6_11_by_game"])
    t1_outer_count = median(t1_ex["outer_6_11_by_game"])
    t1_probe_hidden = median(t1_probe_ex["hidden_6_11_by_game"])
    t1_probe_outer = median(t1_probe_ex["outer_6_11_by_game"])
    lines.extend([
        f"**判定：间歇外区机制方向相容，精确主张未确认。** 历史 T-1 每局 6-11 轮闭合 hidden 段中位 {fmt_num(t1_hidden_count)}、可见外区段 {fmt_num(t1_outer_count)}，说明历史近身视野代理失真；高可见 probe 分别为 {fmt_num(t1_probe_hidden)} 与 {fmt_num(t1_probe_outer)}。其中每局 {fmt_num(t1_probe_outer)} 个 6-11 轮可见外区段与旧称“约5次”同量级，支持间歇外出而非全职巡逻；但仅 2 局、同一次远征可能被短缺失切段，仍有 12.8-14.8% 全不可见轮。hidden/visible 持金变化含烧损，不能复现 4.81 的 pickup 口径，所以“恰5次”和“4.81/轮”均未严格验证。",
        "",
        "## 8. 机制假说：证据与推断分栏",
        "",
    ])
    hypothesis_rows = [
        ("双单位每轮都走完整轻管线", "稳态成本台阶窄；可见动作长度对与双单位均有动作", "与日志相容；看不见源码、隐藏轮和原始k，不能确认同构管线"),
        ("T-1 主要靠恒形/低分支保持约200ns", "成本与多数回合特征的局内效应、跨局P50范围", "若特征效应小则支持；仍可能是查表、布局或平台量化共同结果"),
        ("Tundra 用更多预算做复杂策略", "历史/探针 I10 等效差约+300/+350；290ns文档档位才是+450", "只确认成本空间，不知道预算用途；分支/.text罚则可能吞掉部分"),
        ("中心驻留+偶发外区", "可见端点五区、hidden段、热点端点和持金变化", "位置样本由我方视野选择；只能作为路线候选，不是全局占用率"),
        ("看到金才走/拾金轮更贵", "完整可见pickup轮的局内cost差", "pickup是动作结果，cost在决策时产生；相关不等于目标扫描导致，且有可见偏差"),
        ("按快照触发远征或买视野", "r%5、购买模分布、快照轮cost效应", "周期对齐可支持触发器，但不能识别内部阈值和目标区"),
        ("利用token-2热点", "可见热点终点及条件pickup", "长外圈段更易不可见；零/低占比也不能否定"),
    ]
    append_table(lines, ["假说", "直接证据", "推断边界"], hypothesis_rows)

    lines.extend([
        "## 9. 对手横向比较",
        "",
    ])
    compare_rows = []
    for team, games in corpus.items():
        b = behavior_by_team[team]
        vs = vision_summary(games, 2)
        final = final_score_summary(games)
        tv = pooled_costs(games, 2, CUT_PRIMARY)
        compare_rows.append((
            team_short(team), len(games), len(tv), fmt_num(pct(tv, 50)), fmt_num(pct(tv, 90)),
            fmt_num(median(per_game_p50(games, 2, CUT_PRIMARY))),
            fmt_rate(b["any_visible"], b["rounds"]), fmt_rate(b["regions"][1], b["endpoints"]),
            fmt_rate(b["hotspots"], b["endpoints"]), vs["events"], fmt_num(final["target_net"]),
            fmt_signed(final["gap"]), f"{int(final['target_ahead'])}/{int(final['games'])}",
        ))
    append_table(lines, ["目标", "局", "稳态轮", "P50", "P90", "逐局P50中位", "至少一单位可见", "可见中心端点", "可见热点端点", "视野购买", "完整局净分中位", "对我净差", "净分领先"], compare_rows)
    probe_compare_rows = []
    for team, games in probes.items():
        pb = phase_position_behavior(games, "start", 0)
        costs = pooled_costs(games, 2, CUT_PRIMARY)
        nets = [g.manifest_entry["opponent_uncontested_net"] for g in games]
        probe_compare_rows.append((
            team_short(team), len(games), fmt_num(pct(costs, 50)), fmt_num(pct(costs, 90)),
            fmt_rate(pb["any_visible"], pb["rounds"]), fmt_rate(pb["regions"][1], pb["endpoints"]),
            fmt_rate(pb["hotspots"], pb["endpoints"]), ",".join(str(x) for x in nets), fmt_num(median(nets)),
        ))
    lines.extend(["**独立高可见/不争金横比**", ""])
    append_table(lines, ["目标", "probe局", "稳态P50", "P90", "start可见", "中心端点", "热点端点", "各局不争金净分", "中位"], probe_compare_rows)
    lines.extend([
        "高可见 probe 将 Tundra 的中心端点确认在九成以上，而 T-1 中心约七成、外区与热点端点明显更多；这是目前最强的空间对手差异证据。T-1 的可见外区段也比 Tundra 多，和间歇远征方向一致。",
        "",
        "不同对手的历史日志对应不同我方版本和时间窗口，横比行为与净分是描述性的；延迟因有同局我方基准而更可靠。地图差异尤其影响 Tundra 的 2 局 map2 与 2 局 map3，不能把地图效应当对手策略漂移。",
        "",
        "## 10. 与 AGENT / CHANGELOG 的矛盾检查",
        "",
    ])
    t1_costs = pooled_costs(corpus["Tiuntled-1"], 2, CUT_PRIMARY)
    tu_costs = pooled_costs(corpus["Tundra-wawa"], 2, CUT_PRIMARY)
    t1_final = final_score_summary(corpus["Tiuntled-1"])
    contradiction_rows = [
        ("AGENT §0.2：T-1 190-240、Tundra 250-330，窗口稳定", "基本确认",
         f"本语料稳态P50/P90：T-1 {fmt_num(pct(t1_costs,50))}/{fmt_num(pct(t1_costs,90))}，Tundra {fmt_num(pct(tu_costs,50))}/{fmt_num(pct(tu_costs,90))}；另见时序段与同窗差。"),
        ("AGENT §1：T-1 收入1856-2424", "口径修正/部分相容",
         f"完整局目标净分中位 {fmt_num(t1_final['target_net'])}；范围受对手、地图、烧损和版本窗口影响。本报告不把隐藏pickup补成毛收入。"),
        ("AGENT/CHANGELOG：T-1 每局5次6-11轮远征，离镜4.81/轮", "机制相容、精确数未复现",
         f"高可见probe的6-11轮可见外区段/局中位 {fmt_num(t1_probe_outer)}，与约5次同量级；但仅2局且会被缺失切段。持金Δ不是pickup，4.81口径无法复现。"),
        ("CHANGELOG：T-1 全双管线~750指令", "相容但不可识别",
         "成本包络可容纳紧凑双管线；实际输入载荷和罚则未知，不能由纳秒唯一反推原始指令数。"),
        ("CHANGELOG 军规0：外圈池~2000-2900、中央~3250", "被仓库后续全信息证据推翻",
         "sim/GENERATION.md 用全信息日志给出中央≈4750、外圈≈4820。本过滤语料不能再次测总生成量，不复活旧估计。"),
        ("AGENT：token 2 是金币热点，不是炸弹位", "采用且无矛盾",
         "空间分析直接读取token 2为热点；没有从过滤日志声称其生成总量。"),
    ]
    append_table(lines, ["既有说法", "状态", "本报告判据"], contradiction_rows)

    lines.extend([
        "## 11. 可执行 counterplay",
        "",
        "1. **对 T-1 先保 190-200ns 档。** 按 INFRA 10行载荷，190/200ns 分别约 650/700 条 I10 等效指令；新增器官应低频门控或放入可被 P90 隐藏的稀疏轮，同时验轻轮伴生税。",
        "2. **对 Tundra 分档用预算。** 历史 P50=260 给约 +300 条、轻探针270给约 +350 条；只有接受 INFRA 290ns 档才是 +450。优先加可验证的堆记忆/短程拔点，而非全职外圈巡逻，并用同窗逐轮差确认仍保先手。",
        "3. **用在线快照而非地图常量识别外区机会。** token-2 可用于已知图快速层，但陌生图基线应依赖 `gold_remaining/generated/occupants`；这与 AGENT 的换图铁律一致。",
        "4. **热点只做触发式突袭。** 可见位置样本不足以证明对手长期蹲点；更稳妥的是在某臂快照存量上升且 occupants 低时，记忆热点或高额可见堆，短进短出。",
        "5. **针对中心竞争看同轮先后手，不看单局绝对收入。** 目标更快率和持金窗口差应共同评估；若加策略后先手率下降而持金无补偿，立即回滚。",
        "6. **利用零购买指纹。** 历史两目标从未付费扩视野，可把“默认窗外不知局部细节”作为对抗假设，但全局快照仍在；同时不能把“我看见它”误作“它买了视野”。",
        "7. **实验验收。** 新行为至少 3 对同窗，里程碑 6+6；按我方精确版本分组，保存 map 与 game_id，并同时报告 `r>=10/20/30`，防止 warmup 或截尾改变结论。",
        "",
        "## 12. 不确定性与样本量总表",
        "",
    ])
    uncertainty_rows = []
    for sample_name, source, behaviors, excursions in (
        ("历史", corpus, behavior_by_team, excursion_by_team),
        ("高可见probe", probes, probe_behavior_by_team, probe_excursion_by_team),
    ):
        for team, games in source.items():
            b = behaviors[team]
            ex = excursions[team]
            uncertainty_rows.append((
                team_short(team), sample_name, len(games), sum(len(g.rows) for g in games),
                len(pooled_costs(games, 2, CUT_PRIMARY)), sum(g.complete for g in games),
                len(games) - sum(g.complete for g in games), b["endpoints"], b["complete_pickup_rounds"],
                len(ex["closed_hidden_lengths"]), len(ex["closed_outer_lengths"]),
            ))
    append_table(lines, ["目标", "样本", "局", "有效轮", "稳态cost轮", "完整", "截尾", "可见端点", "双pickup完整轮", "闭合hidden段", "闭合可见外区段"], uncertainty_rows)
    lines.extend([
        "- **高可信**：目标 cost、dispatch 先后、累计持金、累计视野费、地图 token、完整/截尾状态。",
        "- **中可信**：同局持金/净分差、warmup、时序漂移、视野购买周期；仍受我方版本和评测窗口影响。",
        "- **低可信/条件性**：位置五区、热点驻留、动作码、pickup、远征段。它们受我方视野选择，且不同精确 player1 版本几乎无重复。",
        "- **不可辨识**：隐藏 pickup 总量、无烧损毛收入、对手看到的完整 grid、内部目标/记忆、源码指令数、原始被阻动作、每轮原始 `k/order`。",
        "- **因果限制**：回合特征表已用局内差消除局基线，但 feature 并非随机分配；尤其 pickup 是结果、可见性由双方位置共同决定。",
        "- **截尾限制**：截尾局参与其已观察轮的 cost/行为统计，不参与 500 轮终局或缺失窗口；所有曲线逐检查点给出到达样本数。",
        "",
        "---",
        "",
        "实现依据：`tools/gamelog.py`（字段/百分位）、`sim/README.md`（机械与可见性边界）、`sim/GENERATION.md`（热点与生成侧真值边界）、`src/INFRA.md`（成本模型）、`src/CHANGELOG.md` 与 `AGENT.md`（待复核历史主张）。",
        "",
    ])
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--output", type=Path, help="write report here; default: <repo>/sim/OPPONENTS.md")
    parser.add_argument("--check", type=Path, help="compare generated report with this file; do not write")
    parser.add_argument("--stdout", action="store_true", help="also print the generated report")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    corpus, probes, metadata = load_games(repo)
    report = render_report(corpus, probes, metadata)
    if args.check:
        check_path = args.check if args.check.is_absolute() else repo / args.check
        existing = check_path.read_text(encoding="utf-8")
        if existing != report:
            print(f"report differs: {check_path}", file=sys.stderr)
            return 1
        print(f"report matches: {check_path}")
    else:
        output = args.output or (repo / "sim" / "OPPONENTS.md")
        if not output.is_absolute():
            output = repo / output
        output.write_text(report, encoding="utf-8")
        print(f"wrote {output} ({len(report.encode('utf-8'))} bytes)")
    if args.stdout:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
