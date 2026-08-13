#!/usr/bin/env python3
"""Multiprocessing command line interface for GoldRush simulation batches."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import inspect
import json
import multiprocessing
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

# Support both ``python3 sim/cli.py`` and ``python3 -m sim.cli``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sim.runner import (  # type: ignore
        DEFAULT_DISPATCH,
        DEFAULT_FIXED_COSTS,
        DISPATCH_MODES,
        GameResult,
        PairedResult,
        load_map,
        run_game,
        run_paired,
    )
else:
    from .runner import (
        DEFAULT_DISPATCH,
        DEFAULT_FIXED_COSTS,
        DISPATCH_MODES,
        GameResult,
        PairedResult,
        load_map,
        run_game,
        run_paired,
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _parse_seed(value: str) -> Any:
    try:
        return int(value, 0)
    except ValueError:
        return value


def _seed_audit(seed: Any) -> Mapping[str, str]:
    if isinstance(seed, int):
        return {"type": "int", "value": str(seed)}
    if isinstance(seed, bytes):
        return {"type": "bytes", "hex": seed.hex()}
    return {"type": "str", "value": str(seed)}


def _batch_seed(base: Any, index: int) -> Any:
    if index == 0:
        return base
    digest = hashlib.sha256(
        _canonical(["goldrush-batch-seed", _seed_audit(base), index])
    ).digest()
    return int.from_bytes(digest[:16], "big")


def _fixed_costs(values: Sequence[str]) -> tuple[int, int]:
    tokens = []
    for value in values:
        tokens.extend(part for part in value.split(",") if part != "")
    if len(tokens) != 2:
        raise argparse.ArgumentTypeError("--fixed-costs requires two integers")
    try:
        result = (int(tokens[0]), int(tokens[1]))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--fixed-costs requires two integers") from error
    if result[0] < 0 or result[1] < 0:
        raise argparse.ArgumentTypeError("--fixed-costs values must be non-negative")
    return result


def _worker(task: Mapping[str, Any]) -> Any:
    common = {
        "map_source": task["map_source"],
        "seed": task["seed"],
        "dispatch": task["dispatch"],
        "fixed_costs": task["fixed_costs"],
    }
    if task["paired"]:
        return run_paired(task["p1"], task["p2"], **common)
    return run_game(task["p1"], task["p2"], **common)


def _executor(jobs: int) -> ProcessPoolExecutor:
    kwargs: dict[str, Any] = {
        "max_workers": jobs,
        "mp_context": multiprocessing.get_context("spawn"),
    }
    if "max_tasks_per_child" in inspect.signature(ProcessPoolExecutor).parameters:
        # One task is one complete game or paired comparison. Recycling after
        # every task guarantees a fresh strategy/library process lifecycle.
        kwargs["max_tasks_per_child"] = 1
    return ProcessPoolExecutor(**kwargs)


def _record_single(index: int, seed: Any, result: GameResult, output_dir: Path) -> Mapping[str, Any]:
    filename = "game_%04d.log" % index
    result.write_log(output_dir / filename)
    return {
        "game_index": index,
        "seed": _seed_audit(seed),
        "log_file": filename,
        "result": result.summary,
    }


def _record_pair(index: int, seed: Any, result: PairedResult, output_dir: Path) -> Mapping[str, Any]:
    first_name = "pair_%04d_ab.log" % index
    second_name = "pair_%04d_ba.log" % index
    result.a_as_p1.write_log(output_dir / first_name)
    result.b_as_p1.write_log(output_dir / second_name)
    return {
        "game_index": index,
        "seed": _seed_audit(seed),
        "scenario_digest": result.scenario_digest,
        "log_files": [first_name, second_name],
        "result": result.summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic GoldRush official-ABI games"
    )
    parser.add_argument(
        "--p1",
        required=True,
        help="P1 strategy .so path, or built-in 'stay'/'scripted'",
    )
    parser.add_argument(
        "--p2",
        required=True,
        help="P2 strategy .so path, or built-in 'stay'/'scripted'",
    )
    parser.add_argument(
        "--map",
        default="map1",
        dest="map_source",
        help="registered map name, map JSON, line-2 JSON, or official log",
    )
    parser.add_argument("--seed", default="0", help="base integer or string seed")
    parser.add_argument("--games", type=int, default=1, help="games or paired samples")
    parser.add_argument(
        "--dispatch",
        choices=DISPATCH_MODES,
        default=DEFAULT_DISPATCH,
        help="measured, forced p1/p2, or deterministic fixed costs",
    )
    parser.add_argument(
        "--fixed-costs",
        nargs="+",
        default=[str(DEFAULT_FIXED_COSTS[0]), str(DEFAULT_FIXED_COSTS[1])],
        metavar="N",
        help="P1 P2 costs (also accepts P1,P2); used by fixed mode",
    )
    parser.add_argument("--output-dir", default="sim-output")
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, min(4, os.cpu_count() or 1)),
        help="maximum fresh worker processes",
    )
    parser.add_argument(
        "--paired",
        action="store_true",
        help="run A/B and seat-swapped B/A legs for every seed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.games <= 0:
        parser.error("--games must be positive")
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    try:
        fixed_costs = _fixed_costs(args.fixed_costs)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))

    # Validate the map in the parent so malformed input fails before workers
    # start. Workers reload it independently to keep each task self-contained.
    try:
        map_definition = load_map(args.map_source)
    except Exception as error:
        parser.error("cannot load --map: %s" % error)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base_seed = _parse_seed(args.seed)
    seeds = [_batch_seed(base_seed, index) for index in range(args.games)]
    tasks = [
        {
            "index": index,
            "p1": args.p1,
            "p2": args.p2,
            "map_source": args.map_source,
            "seed": seeds[index],
            "dispatch": args.dispatch,
            "fixed_costs": fixed_costs,
            "paired": args.paired,
        }
        for index in range(args.games)
    ]

    records = []
    failures = 0
    with _executor(args.jobs) as executor:
        futures = [executor.submit(_worker, task) for task in tasks]
        # Submission order, rather than completion order, fixes summary ordering.
        for task, future in zip(tasks, futures):
            index = int(task["index"])
            try:
                result = future.result()
                if args.paired:
                    records.append(_record_pair(index, seeds[index], result, output_dir))
                else:
                    records.append(_record_single(index, seeds[index], result, output_dir))
            except Exception as error:
                failures += 1
                records.append(
                    {
                        "game_index": index,
                        "seed": _seed_audit(seeds[index]),
                        "status": "error",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )

    summary = {
        "status": "error" if failures else "ok",
        "paired": bool(args.paired),
        "games_requested": args.games,
        "games_failed": failures,
        "map": map_definition.name,
        "map_fingerprint": map_definition.fingerprint,
        "dispatch": args.dispatch,
        "fixed_costs": list(fixed_costs),
        "strategies": {"A": args.p1, "B": args.p2},
        "records": records,
    }
    summary_bytes = json.dumps(
        summary,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ).encode("ascii") + b"\n"
    (output_dir / "summary.json").write_bytes(summary_bytes)
    sys.stdout.buffer.write(summary_bytes)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
