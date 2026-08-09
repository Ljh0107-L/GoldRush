"""Official-ABI game runner, byte-stable logger, and paired comparator."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import random
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Union

from .abi import (
    CallableStrategy,
    DecisionCall,
    SharedObjectStrategy,
    StayStrategy,
    StrategyError,
)
from .engine import (
    BOMB,
    DEFAULT_NPC_IDS,
    FinalScore,
    GameEngine,
    GameMap,
    NPCState,
    PlayerDecision,
    PlayerState,
    Position,
    RoundResult,
    Snapshot,
    UnitState,
    WorldState,
    winning_player,
)
from .npc import NPCModel
from .scenario import MapDefinition, ROUND_COUNT, ScenarioGenerator, SpawnState

DispatchMode = str
StrategyLike = Union[
    str,
    os.PathLike[str],
    Callable[[Any], Any],
    CallableStrategy,
    SharedObjectStrategy,
]

DEFAULT_DISPATCH = "fixed"
DEFAULT_FIXED_COSTS = (0, 1)
DISPATCH_MODES = ("measured", "p1", "p2", "fixed")
_JSON_KWARGS = {
    "ensure_ascii": True,
    "allow_nan": False,
    "separators": (",", ":"),
}


class RunnerError(RuntimeError):
    """Base class for game-level failures."""


class StrategyForfeit(RunnerError):
    """A seat failed to return a legal official ``GameOutput``."""

    def __init__(self, player_id: int, round_number: int, reason: str) -> None:
        self.player_id = player_id
        self.round_number = round_number
        self.reason = reason
        super().__init__(
            "player %d forfeits at round %d: %s"
            % (player_id, round_number, reason)
        )


@dataclass(frozen=True)
class GameResult:
    """A completed game and its deterministic machine-readable summary."""

    summary: Mapping[str, Any]
    log_bytes: bytes = field(repr=False)

    @property
    def scenario_digest(self) -> str:
        return str(self.summary["scenario_digest"])

    @property
    def log_digest(self) -> str:
        return str(self.summary["log_sha256"])

    def write_log(self, path: os.PathLike[str] | str) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.log_bytes)
        return destination


@dataclass(frozen=True)
class PairedResult:
    """Two seat-swapped games sharing one map, seed, and scenario intent stream."""

    a_as_p1: GameResult
    b_as_p1: GameResult

    @property
    def scenario_digest(self) -> str:
        first = self.a_as_p1.scenario_digest
        second = self.b_as_p1.scenario_digest
        if first != second:
            raise AssertionError("paired legs do not share a scenario digest")
        return first

    @property
    def summary(self) -> Mapping[str, Any]:
        return paired_summary(self)


class ScriptedStrategy(CallableStrategy):
    """Built-in deterministic policy that exercises every output field."""

    def __init__(self, *, name: str = "scripted") -> None:
        def scripted(value: Any) -> Tuple[int, ...]:
            action = int(value.round) % 5
            return (action,) * 6 + (3, int(value.round) % 2, int(value.round) % 3)

        super().__init__(scripted, name=name)


def _json_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    return json.dumps(value, sort_keys=sort_keys, **_JSON_KWARGS).encode("ascii")


def _stable_seed(*parts: Any) -> int:
    payload = _json_bytes(list(parts), sort_keys=True)
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], "big")


def load_map(source: Any = "map1") -> MapDefinition:
    """Load a built-in id, map JSON, decoded line 2, or complete official log."""
    if isinstance(source, MapDefinition):
        return source
    if isinstance(source, (str, os.PathLike)):
        text_source = os.fspath(source)
        if text_source in {"map1", "map2", "map3"}:
            return MapDefinition.load(text_source)
        path = Path(text_source).expanduser()
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
                second = handle.readline()
            try:
                first_value = json.loads(first)
            except json.JSONDecodeError as error:
                raise ValueError("map file is not valid JSON: %s" % path) from error
            if isinstance(first_value, list):
                return MapDefinition.from_log_line2(first_value, name=path.stem)
            if (
                isinstance(first_value, Mapping)
                and "player1" in first_value
                and "player2" in first_value
                and second.strip()
            ):
                return MapDefinition.from_log_line2(second, name=path.stem)
            return MapDefinition.load(path)
    return MapDefinition.load(source)


def strategy_name(strategy: StrategyLike, fallback: str = "strategy") -> str:
    if isinstance(strategy, (str, os.PathLike)):
        value = os.fspath(strategy)
        if value in {"stay", "scripted"}:
            return value
        return Path(value).stem or fallback
    return str(getattr(strategy, "name", getattr(strategy, "__name__", fallback)))


def _open_strategy(strategy: StrategyLike, *, fallback_name: str) -> Tuple[Any, bool]:
    """Return a fresh adapter and whether the runner owns its lifecycle."""
    if isinstance(strategy, SharedObjectStrategy):
        return SharedObjectStrategy(strategy.source_path, name=strategy.name), True
    if isinstance(strategy, (str, os.PathLike)):
        value = os.fspath(strategy)
        if value == "stay":
            return StayStrategy(), True
        if value == "scripted":
            return ScriptedStrategy(), True
        return SharedObjectStrategy(value), True
    if isinstance(strategy, CallableStrategy):
        return CallableStrategy(strategy.function, name=strategy.name), True
    if callable(strategy):
        return CallableStrategy(strategy, name=strategy_name(strategy, fallback_name)), True
    raise TypeError("strategy must be a .so path, built-in name, or callable")


def _spawn_state(state: WorldState) -> SpawnState:
    actor_cells = frozenset(
        [unit.position.cell for player in state.players for unit in player.units]
        + [npc.position.cell for npc in state.npcs]
    )
    gold_cells = frozenset(
        (row, col)
        for row in range(17)
        for col in range(17)
        if state.ground[row][col] > 0
    )
    bomb_cells = frozenset(
        (row, col)
        for row in range(17)
        for col in range(17)
        if state.ground[row][col] == BOMB
    )
    return SpawnState(
        actor_cells=actor_cells,
        gold_cells=gold_cells,
        bomb_cells=bomb_cells,
    )


def _npc_policy(
    scenario_digest: str,
    round_number: int,
) -> Callable[[int, Sequence[Sequence[int]], Position], Tuple[int, int, int]]:
    """Build a deterministic turn-time NPC callback.

    The engine invokes it in the published internal NPC order, after the faster
    player and earlier NPCs have changed the ground.  Each (round, NPC id) owns
    an independent seed, so strategy RNG consumption cannot perturb the model.
    """
    def decide(
        npc_id: int,
        current_ground: Sequence[Sequence[int]],
        current_position: Position,
    ) -> Tuple[int, int, int]:
        seed = _stable_seed("npc-policy", scenario_digest, round_number, npc_id)
        return NPCModel(seed=seed).actions(
            current_ground, current_position, npc_id=npc_id
        )

    return decide


def _npc_order(
    npc_ids: Sequence[int], scenario_digest: str, round_number: int
) -> Tuple[int, ...]:
    result = list(npc_ids)
    random.Random(
        _stable_seed("npc-dispatch", scenario_digest, round_number)
    ).shuffle(result)
    return tuple(result)


def _fixed_cost_pair(value: Sequence[int]) -> Tuple[int, int]:
    if len(value) != 2:
        raise ValueError("fixed_costs must contain exactly two integers")
    first, second = value
    if (
        not isinstance(first, int)
        or isinstance(first, bool)
        or not isinstance(second, int)
        or isinstance(second, bool)
        or first < 0
        or second < 0
    ):
        raise ValueError("fixed costs must be non-negative integers")
    return first, second


def _dispatch_costs(
    mode: DispatchMode,
    calls: Mapping[int, DecisionCall],
    fixed_costs: Tuple[int, int],
) -> Mapping[int, int]:
    if mode == "measured":
        return {1: calls[1].cost_ns, 2: calls[2].cost_ns}
    if mode == "p1":
        return {1: 0, 2: 1}
    if mode == "p2":
        return {1: 1, 2: 0}
    if mode == "fixed":
        return {1: fixed_costs[0], 2: fixed_costs[1]}
    raise ValueError("dispatch must be one of %s" % ", ".join(DISPATCH_MODES))


def _position(value: Position) -> list[int]:
    return [value.row, value.col]


def _unit_record(unit: UnitState) -> Mapping[str, Any]:
    return {
        "position": _position(unit.position),
        "gold": unit.gold,
        "actions": list(unit.actions),
        "pickup": unit.pickup,
    }


def _player_record(player: PlayerState) -> Mapping[str, Any]:
    return {
        "id": player.id,
        "cost": player.cost,
        "gold": player.gold,
        "vision_spent": player.vision_spent,
        "order": player.order,
        "units": [_unit_record(unit) for unit in player.units],
    }


def _npc_record(npc: NPCState) -> Mapping[str, Any]:
    return {
        "id": npc.id,
        "position": _position(npc.position),
        "actions": list(npc.actions),
        "pickup": npc.pickup,
        "cost": npc.cost,
    }


def _phase_record(state: WorldState, grid: Sequence[Sequence[int]]) -> Mapping[str, Any]:
    npcs = [_npc_record(npc) for npc in state.npcs]
    return {
        "grid": [list(row) for row in grid],
        "players": [_player_record(player) for player in state.players],
        "npcs": npcs,
        "overlap_events": [],
        "npc": dict(npcs[0]),
    }


def _snapshot_record(round_number: int, snapshot: Snapshot) -> Mapping[str, Any]:
    return {
        "round": round_number,
        "window": [snapshot.window_begin, snapshot.window_end],
        "regions": [
            {
                "id": region.id,
                "enter": region.enter,
                "leave": region.leave,
                "gold_generated": region.gold_generated,
                "gold_collected": region.gold_collected,
                "gold_remaining": region.gold_remaining,
                "occupants": region.occupants,
            }
            for region in snapshot.regions
        ],
    }


def round_log_record(result: RoundResult) -> Mapping[str, Any]:
    """Render one full official-compatible round object in stable field order."""
    start = _phase_record(result.start.state, result.start.full_grid)
    end = dict(_phase_record(result.state, result.full_grid))
    end["dispatch_order"] = list(result.dispatch_order)
    end["trample_events"] = [
        {
            "round": event.round,
            "pos": _position(event.pos),
            "unit_owner": event.unit_owner,
            "npc_count": event.npc_count,
            "penalty": event.penalty,
        }
        for event in result.trample_events
    ]
    end["burned"] = result.burned
    record = {"round": result.round, "start": start, "end": end}
    if result.start.snapshot is not None:
        record["snapshot"] = _snapshot_record(
            result.round, result.start.snapshot
        )
    return record


def _score_record(score: FinalScore) -> Mapping[str, int]:
    return {
        "gross_gold": score.gross_gold,
        "vision_spent": score.vision_spent,
        "net_gold": score.net_gold,
        "p90_cost": score.p90_cost,
    }


def _summary(
    *,
    scenario: ScenarioGenerator,
    player_names: Mapping[int, str],
    dispatch: str,
    fixed_costs: Tuple[int, int],
    engine: GameEngine,
    log_bytes: bytes,
) -> Mapping[str, Any]:
    scores = engine.final_scores()
    winner = winning_player(scores)
    return {
        "status": "ok",
        "rounds": ROUND_COUNT,
        "map": scenario.map.name,
        "map_fingerprint": scenario.map.fingerprint,
        "seed": ScenarioGenerator._seed_audit(scenario.seed),
        "scenario_digest": scenario.digest,
        "dispatch": dispatch,
        "fixed_costs": list(fixed_costs),
        "players": {
            "1": {"name": player_names[1], **_score_record(scores[1])},
            "2": {"name": player_names[2], **_score_record(scores[2])},
        },
        "winner": winner,
        "winner_name": None if winner is None else player_names[winner],
        "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
    }


def run_game(
    p1: StrategyLike,
    p2: StrategyLike,
    *,
    map_source: Any = "map1",
    seed: Any = 0,
    dispatch: DispatchMode = DEFAULT_DISPATCH,
    fixed_costs: Sequence[int] = DEFAULT_FIXED_COSTS,
    player1_name: Optional[str] = None,
    player2_name: Optional[str] = None,
    output_path: Optional[os.PathLike[str] | str] = None,
) -> GameResult:
    """Run exactly 500 rounds and return an official-compatible full log.

    Non-measured modes never read a clock. ``fixed`` is the default, making a
    same-seed run byte-identical when its strategies are deterministic. In
    ``measured`` mode ``perf_counter_ns`` costs determine dispatch; an exact tie
    goes to P1, matching the engine's lower-or-equal tie rule.
    """
    if dispatch not in DISPATCH_MODES:
        raise ValueError("dispatch must be one of %s" % ", ".join(DISPATCH_MODES))
    costs_pair = _fixed_cost_pair(fixed_costs)
    map_definition = load_map(map_source)
    scenario = ScenarioGenerator(map_definition, seed)
    engine = GameEngine(GameMap.from_definition(map_definition), npc_ids=DEFAULT_NPC_IDS)

    names = {
        1: player1_name or strategy_name(p1, "p1"),
        2: player2_name or strategy_name(p2, "p2"),
    }
    strategy1, own1 = _open_strategy(p1, fallback_name=names[1])
    try:
        strategy2, own2 = _open_strategy(p2, fallback_name=names[2])
    except Exception:
        if own1:
            strategy1.close()
        raise

    lines = [
        _json_bytes({"player1": names[1], "player2": names[2]}),
        _json_bytes([list(row) for row in map_definition.rows]),
    ]
    try:
        for round_number in range(ROUND_COUNT):
            events = scenario.resolve_round(round_number, _spawn_state(engine.state))
            start = engine.begin_round(events.gold_additions, events.bomb_refresh)
            calls = {}
            for player_id, strategy in ((1, strategy1), (2, strategy2)):
                try:
                    calls[player_id] = strategy.decide(
                        engine.player_input(player_id, start),
                        measured=(dispatch == "measured"),
                    )
                except StrategyError as error:
                    raise StrategyForfeit(player_id, round_number, str(error)) from error
                except Exception as error:
                    raise StrategyForfeit(
                        player_id,
                        round_number,
                        "%s: %s" % (type(error).__name__, error),
                    ) from error

            player_costs = _dispatch_costs(dispatch, calls, costs_pair)
            result = engine.execute_round(
                {
                    1: calls[1].decision,
                    2: calls[2].decision,
                },
                _npc_policy(scenario.digest, round_number),
                player_costs=player_costs,
                npc_order=_npc_order(engine.npc_ids, scenario.digest, round_number),
            )
            lines.append(_json_bytes(round_log_record(result)))
    finally:
        if own2:
            strategy2.close()
        if own1:
            strategy1.close()

    log_bytes = b"\n".join(lines) + b"\n"
    result = GameResult(
        summary=_summary(
            scenario=scenario,
            player_names=names,
            dispatch=dispatch,
            fixed_costs=costs_pair,
            engine=engine,
            log_bytes=log_bytes,
        ),
        log_bytes=log_bytes,
    )
    if output_path is not None:
        result.write_log(output_path)
    return result


def run_paired(
    strategy_a: StrategyLike,
    strategy_b: StrategyLike,
    *,
    map_source: Any = "map1",
    seed: Any = 0,
    dispatch: DispatchMode = DEFAULT_DISPATCH,
    fixed_costs: Sequence[int] = DEFAULT_FIXED_COSTS,
    name_a: Optional[str] = None,
    name_b: Optional[str] = None,
    output_paths: Optional[Sequence[os.PathLike[str] | str]] = None,
) -> PairedResult:
    """Run A-vs-B and B-vs-A under identical scenario intents."""
    if output_paths is not None and len(output_paths) != 2:
        raise ValueError("output_paths must contain the A/B and B/A log paths")
    a_name = name_a or strategy_name(strategy_a, "A")
    b_name = name_b or strategy_name(strategy_b, "B")
    first = run_game(
        strategy_a,
        strategy_b,
        map_source=map_source,
        seed=seed,
        dispatch=dispatch,
        fixed_costs=fixed_costs,
        player1_name=a_name,
        player2_name=b_name,
        output_path=None if output_paths is None else output_paths[0],
    )
    second = run_game(
        strategy_b,
        strategy_a,
        map_source=map_source,
        seed=seed,
        dispatch=dispatch,
        fixed_costs=fixed_costs,
        player1_name=b_name,
        player2_name=a_name,
        output_path=None if output_paths is None else output_paths[1],
    )
    paired = PairedResult(first, second)
    _ = paired.scenario_digest
    return paired


def paired_summary(result: PairedResult) -> Mapping[str, Any]:
    """Aggregate a paired result by strategy name, independent of seat."""
    legs = [result.a_as_p1.summary, result.b_as_p1.summary]
    totals: dict[str, dict[str, int]] = {}
    for leg in legs:
        for seat in ("1", "2"):
            player = leg["players"][seat]
            aggregate = totals.setdefault(
                player["name"],
                {"games": 0, "gross_gold": 0, "vision_spent": 0, "net_gold": 0, "wins": 0},
            )
            aggregate["games"] += 1
            aggregate["gross_gold"] += int(player["gross_gold"])
            aggregate["vision_spent"] += int(player["vision_spent"])
            aggregate["net_gold"] += int(player["net_gold"])
            if leg["winner_name"] == player["name"]:
                aggregate["wins"] += 1
    return {
        "status": "ok",
        "paired": True,
        "scenario_digest": result.scenario_digest,
        "strategies": {name: totals[name] for name in sorted(totals)},
        "legs": legs,
    }


# Common explicit aliases for callers that prefer object-oriented naming.
GameRunner = run_game
compare_paired = run_paired


__all__ = [
    "DEFAULT_DISPATCH",
    "DEFAULT_FIXED_COSTS",
    "DISPATCH_MODES",
    "GameResult",
    "PairedResult",
    "RunnerError",
    "StrategyForfeit",
    "ScriptedStrategy",
    "GameRunner",
    "compare_paired",
    "load_map",
    "paired_summary",
    "round_log_record",
    "run_game",
    "run_paired",
    "strategy_name",
]
