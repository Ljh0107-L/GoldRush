#!/usr/bin/env python3
"""Index target-opponent logs with symlinks and a machine-readable manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGS_ROOT = ROOT / "logs"
FROZEN_CHAMPION = {
    "model_name": "champ76e",
    "git_commit": "76eef470cb28d1bd52c71afe57b09108c32b8f6d",
    "source_sha256": "d4526b22f4371e33e17dd3508ef74235e77ffc84ff81966ff0def25e0be013b0",
    "so_sha256": "a784ae7c524725d1a2920d8eb90f93ea93a2160794cc086105a2d3361d1a9e5c",
    "build_command": "g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o champ.so src/player.cpp -Isrc",
}
PROBE_ARTIFACT = {
    "model_name": "probeobs",
    "git_commit": "6213661",
    "source_sha256": "c3d02a2b5a2ddfad1f884c98318d376511270cb34cddb08d4949b9c61720f2fd",
}
TEAMS = {
    "Tiuntled-1": {"account": "player163", "model_id": 87478, "role": "opponent"},
    "Tundra-wawa": {"account": "player57", "model_id": 43116, "role": "opponent"},
    "_selfplay-champ": {"account": "champ76e", "model_id": None, "role": "selfplay_champion"},
}


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def score_record(player, costs):
    gross = int(player.get("gold", 0))
    vision = int(player.get("vision_spent", 0))
    return {
        "gross_gold": gross,
        "vision_spent": vision,
        "net_score": gross - vision,
        "cost_ns_p50": percentile(costs, 0.50),
        "cost_ns_p90": percentile(costs, 0.90),
    }


def _phase_visibility(records, target_player_id, phase):
    visible_rounds = 0
    both_visible_rounds = 0
    visible_unit_observations = 0
    phase_rounds = 0
    for row in records:
        state = row.get(phase)
        if not isinstance(state, dict):
            continue
        target = next(
            (player for player in state.get("players", []) if int(player["id"]) == target_player_id),
            None,
        )
        if target is None:
            continue
        count = sum(unit.get("position") is not None for unit in target.get("units", []))
        visible_rounds += int(count > 0)
        both_visible_rounds += int(count == 2)
        visible_unit_observations += count
        phase_rounds += 1
    return {
        "rounds": phase_rounds,
        "visible_rounds": visible_rounds,
        "visible_rate": visible_rounds / phase_rounds if phase_rounds else None,
        "both_visible_rounds": both_visible_rounds,
        "both_visible_rate": both_visible_rounds / phase_rounds if phase_rounds else None,
        "visible_unit_observations": visible_unit_observations,
    }


def parse_log(path):
    with path.open("r", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        _map_rows = json.loads(handle.readline())
        records = [json.loads(line) for line in handle if line.strip()]
    completed = [row for row in records if isinstance(row.get("end"), dict)]
    if not completed:
        raise ValueError("log has no completed rounds")
    names = {1: header["player1"], 2: header["player2"]}
    last_players = {int(player["id"]): player for player in completed[-1]["end"]["players"]}
    costs = {1: [], 2: []}
    dispatch_first = {1: 0, 2: 0}
    for row in completed:
        end = row["end"]
        for player in end.get("players", []):
            player_id = int(player["id"])
            if player_id in costs and "cost" in player:
                costs[player_id].append(int(player["cost"]))
        order = end.get("dispatch_order") or []
        if order and int(order[0]) in dispatch_first:
            dispatch_first[int(order[0])] += 1
    return {
        "header": header,
        "names": names,
        "rounds": len(completed),
        "scores": {
            names[player_id]: score_record(last_players[player_id], costs[player_id])
            for player_id in (1, 2)
        },
        "dispatch_first_by_player_id": dispatch_first,
        "visibility_by_target": {
            player_id: {
                phase: _phase_visibility(records, player_id, phase)
                for phase in ("start", "end")
            }
            for player_id in (1, 2)
        },
    }


def relative_to_root(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def target_team(names):
    for team, metadata in TEAMS.items():
        if metadata["account"] in names.values():
            return team
    return None


def build_manifest(logs_root):
    logs_root = logs_root.resolve()
    opponents_root = logs_root / "opponents"
    opponents_root.mkdir(parents=True, exist_ok=True)
    entries = []
    expected_links = {team: set() for team in TEAMS}

    for source in sorted(logs_root.glob("game_*.log")):
        parsed = parse_log(source)
        team = target_team(parsed["names"])
        if team is None:
            continue
        metadata = TEAMS[team]
        target_id = 1 if parsed["names"][1] == metadata["account"] else 2
        probe_id = 3 - target_id
        game_id = source.stem.removeprefix("game_")
        team_dir = opponents_root / team
        team_dir.mkdir(parents=True, exist_ok=True)
        archive_path = team_dir / source.name
        expected_links[team].add(archive_path.name)
        relative_target = os.path.relpath(source, team_dir)
        if archive_path.is_symlink():
            if os.readlink(archive_path) != relative_target:
                archive_path.unlink()
                archive_path.symlink_to(relative_target)
        elif archive_path.exists():
            raise RuntimeError("refusing to replace non-symlink archive file: %s" % archive_path)
        else:
            archive_path.symlink_to(relative_target)

        target_first_rounds = parsed["dispatch_first_by_player_id"][target_id]
        entries.append({
            "game_id": int(game_id) if game_id.isdigit() else game_id,
            "team": team,
            "target": {
                "account": metadata["account"],
                "model_id": metadata["model_id"],
                "role": metadata["role"],
            },
            "probe_version": parsed["names"][probe_id],
            "probe_player_id": probe_id,
            "target_player_id": target_id,
            "rounds": parsed["rounds"],
            "scores": parsed["scores"],
            "target_net_vs_probe": parsed["scores"][metadata["account"]]["net_score"],
            "target_visibility": parsed["visibility_by_target"][target_id],
            "dispatch": {
                "first_by_player_id": parsed["dispatch_first_by_player_id"],
                "target_first_rounds": target_first_rounds,
                "target_first_rate": target_first_rounds / parsed["rounds"],
            },
            "path": relative_to_root(archive_path),
            "source_path": relative_to_root(source),
        })

    for team in TEAMS:
        team_dir = opponents_root / team
        team_dir.mkdir(parents=True, exist_ok=True)
        for path in team_dir.glob("game_*.log"):
            if path.name not in expected_links[team] and path.is_symlink():
                path.unlink()

    entries.sort(key=lambda entry: (entry["team"], int(entry["game_id"])))
    manifest = {
        "schema_version": 2,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "logs_root": relative_to_root(logs_root),
        "percentile_method": "nearest_rank",
        "artifacts": {
            "frozen_champion": FROZEN_CHAMPION,
            "observation_probe": PROBE_ARTIFACT,
        },
        "teams": {
            team: {
                **metadata,
                "game_count": sum(entry["team"] == team for entry in entries),
                "paths": [entry["path"] for entry in entries if entry["team"] == team],
            }
            for team, metadata in TEAMS.items()
        },
        "games": entries,
    }
    manifest_path = opponents_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path, manifest


def list_paths(logs_root, team):
    manifest_path = logs_root.resolve() / "opponents" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if team not in manifest["teams"]:
        raise SystemExit("unknown team %r; choose from %s" % (team, ", ".join(manifest["teams"])))
    for path in manifest["teams"][team]["paths"]:
        print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-root", type=Path, default=DEFAULT_LOGS_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="rebuild symlinks and manifest")
    paths_parser = subparsers.add_parser("paths", help="print one team's indexed log paths")
    paths_parser.add_argument("--team", required=True, choices=tuple(TEAMS))
    args = parser.parse_args()
    if args.command == "build":
        manifest_path, manifest = build_manifest(args.logs_root)
        print(json.dumps({
            "manifest": relative_to_root(manifest_path),
            "counts": {team: data["game_count"] for team, data in manifest["teams"].items()},
        }, ensure_ascii=False, sort_keys=True))
    else:
        list_paths(args.logs_root, args.team)


if __name__ == "__main__":
    main()
