#!/usr/bin/env python3
"""Summarize the fixed-probe collection-control experiment from its manifest."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "logs" / "opponents" / "manifest.json"
PRIMARY_GROUPS = {
    "selfplay_champion": [172168, 172177, 172179, 172181, 172182],
    "Tiuntled-1": [171719, 171747, 172186, 172187, 172219],
    "Tundra-wawa": [171687, 171708, 172192, 172214, 172216],
}
EXTRA_SELFPLAY = [172183, 172185, 172188, 172215, 172218]
EXCLUDED_FAILED_GAMES = [172111]
SOLO_REFERENCE = 1802


def sample_summary(values):
    values = list(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "n": len(values),
        "values": values,
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "range": max(values) - min(values),
        "sample_stddev": deviation,
        "standard_error": deviation / math.sqrt(len(values)),
    }


def game_record(entry):
    target_name = entry["target"]["account"]
    target_score = entry["scores"][target_name]
    probe_score = entry["scores"][entry["probe_version"]]
    return {
        "game_id": entry["game_id"],
        "target": target_name,
        "target_gross_gold": target_score["gross_gold"],
        "target_vision_spent": target_score["vision_spent"],
        "target_net_score": target_score["net_score"],
        "target_cost_ns_p50": target_score["cost_ns_p50"],
        "target_cost_ns_p90": target_score["cost_ns_p90"],
        "probe_gross_gold": probe_score["gross_gold"],
        "probe_vision_spent": probe_score["vision_spent"],
        "probe_net_score": probe_score["net_score"],
        "probe_cost_ns_p50": probe_score["cost_ns_p50"],
        "probe_cost_ns_p90": probe_score["cost_ns_p90"],
        "target_first_rounds": entry["dispatch"]["target_first_rounds"],
        "rounds": entry["rounds"],
        "target_start_visible_rate": entry["target_visibility"]["start"]["visible_rate"],
        "target_end_visible_rate": entry["target_visibility"]["end"]["visible_rate"],
        "probe_start_visible_rate": entry["probe_visibility"]["start"]["visible_rate"],
        "probe_end_visible_rate": entry["probe_visibility"]["end"]["visible_rate"],
    }


def group_report(entries):
    games = [game_record(entry) for entry in entries]
    return {
        "games": games,
        "target_net_score": sample_summary(game["target_net_score"] for game in games),
        "target_gross_gold": sample_summary(game["target_gross_gold"] for game in games),
        "all_target_first_500_of_500": all(
            game["target_first_rounds"] == game["rounds"] == 500 for game in games
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    by_id = {entry["game_id"]: entry for entry in manifest["games"]}
    required = {game_id for ids in PRIMARY_GROUPS.values() for game_id in ids} | set(EXTRA_SELFPLAY)
    missing = sorted(required - set(by_id))
    if missing:
        raise SystemExit("manifest is missing games: %s" % missing)

    groups = {
        name: group_report([by_id[game_id] for game_id in ids])
        for name, ids in PRIMARY_GROUPS.items()
    }
    self_all = group_report([
        by_id[game_id]
        for game_id in PRIMARY_GROUPS["selfplay_champion"] + EXTRA_SELFPLAY
    ])
    t1 = groups["Tiuntled-1"]["target_net_score"]
    tundra = groups["Tundra-wawa"]["target_net_score"]
    mean_difference = t1["mean"] - tundra["mean"]
    difference_se = math.sqrt(t1["standard_error"] ** 2 + tundra["standard_error"] ** 2)
    champion = groups["selfplay_champion"]["target_net_score"]
    payload = {
        "primary_groups": groups,
        "extra_selfplay_game_ids": EXTRA_SELFPLAY,
        "selfplay_all_ten_sensitivity": self_all,
        "excluded_failed_game_ids": EXCLUDED_FAILED_GAMES,
        "comparisons": {
            "t1_minus_tundra_mean": mean_difference,
            "difference_standard_error": difference_se,
            "absolute_difference_over_standard_error": (
                abs(mean_difference) / difference_se if difference_se else None
            ),
            "t1_tundra_ranges_overlap": not (
                t1["max"] < tundra["min"] or tundra["max"] < t1["min"]
            ),
            "champion_minus_t1_mean": champion["mean"] - t1["mean"],
            "champion_minus_tundra_mean": champion["mean"] - tundra["mean"],
            "champion_first_move_minus_solo_1802": champion["mean"] - SOLO_REFERENCE,
            "solo_reference": SOLO_REFERENCE,
        },
        "all_primary_dispatch_valid": all(
            group["all_target_first_500_of_500"] for group in groups.values()
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
