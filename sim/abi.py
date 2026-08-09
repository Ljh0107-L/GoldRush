"""Exact ctypes bridge for the official GoldRush strategy ABI.

The structure declarations in this module intentionally mirror ``src/game_api.h``.
Shared libraries are loaded from unique temporary copies so two seats using the
same source file receive independent loader images and independent C++ globals.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import tempfile
from time import perf_counter_ns
from typing import Any, Callable, Optional

from .engine import PlayerDecision, PlayerInput

GRID_SIZE = 17
MAX_NPCS = 7
S = 6
REGION_COUNT = 5


class Position(ctypes.Structure):
    _fields_ = [("row", ctypes.c_int), ("col", ctypes.c_int)]


class NpcInfo(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int), ("pos", Position)]


class RegionStat(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int)
        for name in (
            "id",
            "enter",
            "leave",
            "gold_generated",
            "gold_collected",
            "gold_remaining",
            "occupants",
        )
    ]


class Snapshot(ctypes.Structure):
    _fields_ = [
        ("window_begin", ctypes.c_int),
        ("window_end", ctypes.c_int),
        ("regions", RegionStat * REGION_COUNT),
    ]


class GameInput(ctypes.Structure):
    _fields_ = [
        ("round", ctypes.c_int),
        ("grid", (ctypes.c_int * GRID_SIZE) * GRID_SIZE),
        ("my_units", Position * 2),
        ("my_units_gold", ctypes.c_int * 2),
        ("gold_opp", ctypes.c_int),
        ("visible_enemies", Position * 2),
        ("num_visible_npcs", ctypes.c_int),
        ("visible_npcs", NpcInfo * MAX_NPCS),
        ("snapshot_valid", ctypes.c_int),
        ("snapshot", Snapshot),
    ]


class GameOutput(ctypes.Structure):
    _fields_ = [
        ("actions", ctypes.c_int * S),
        ("k", ctypes.c_int),
        ("order", ctypes.c_int),
        ("vp", ctypes.c_int),
    ]


# Sizes and offsets are part of the ABI, not merely implementation details.
_EXPECTED_LAYOUT = {
    Position: (8, {"row": 0, "col": 4}),
    NpcInfo: (12, {"id": 0, "pos": 4}),
    RegionStat: (
        28,
        {
            "id": 0,
            "enter": 4,
            "leave": 8,
            "gold_generated": 12,
            "gold_collected": 16,
            "gold_remaining": 20,
            "occupants": 24,
        },
    ),
    Snapshot: (148, {"window_begin": 0, "window_end": 4, "regions": 8}),
    GameInput: (
        1444,
        {
            "round": 0,
            "grid": 4,
            "my_units": 1160,
            "my_units_gold": 1176,
            "gold_opp": 1184,
            "visible_enemies": 1188,
            "num_visible_npcs": 1204,
            "visible_npcs": 1208,
            "snapshot_valid": 1292,
            "snapshot": 1296,
        },
    ),
    GameOutput: (36, {"actions": 0, "k": 24, "order": 28, "vp": 32}),
}


def verify_abi_layout() -> None:
    """Raise if this interpreter cannot represent the official 4-byte ABI."""
    if ctypes.sizeof(ctypes.c_int) != 4:
        raise RuntimeError("official ABI requires a 4-byte C int")
    for structure, (expected_size, expected_offsets) in _EXPECTED_LAYOUT.items():
        actual_size = ctypes.sizeof(structure)
        if actual_size != expected_size:
            raise RuntimeError(
                "%s ABI size is %d, expected %d"
                % (structure.__name__, actual_size, expected_size)
            )
        for field, expected_offset in expected_offsets.items():
            actual_offset = getattr(structure, field).offset
            if actual_offset != expected_offset:
                raise RuntimeError(
                    "%s.%s ABI offset is %d, expected %d"
                    % (structure.__name__, field, actual_offset, expected_offset)
                )


verify_abi_layout()


def _copy_position(destination: Position, source: Any) -> None:
    destination.row = int(source.row)
    destination.col = int(source.col)


def player_input_to_abi(value: PlayerInput) -> GameInput:
    """Convert an engine ``PlayerInput`` to the exact official ``GameInput``.

    The engine has already applied the radius purchased by the previous round's
    ``vp`` to its fog grid. Enemy positions are compacted without identity, and
    every unused NPC slot is explicitly padded with ``id=0, pos=(-1,-1)``.
    """
    if not isinstance(value, PlayerInput):
        raise TypeError("value must be sim.engine.PlayerInput")
    result = GameInput()
    result.round = int(value.round)
    if len(value.grid) != GRID_SIZE or any(len(row) != GRID_SIZE for row in value.grid):
        raise ValueError("PlayerInput grid must be exactly 17x17")
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            result.grid[row][col] = int(value.grid[row][col])

    for index in range(2):
        _copy_position(result.my_units[index], value.my_units[index])
        result.my_units_gold[index] = int(value.my_units_gold[index])
    result.gold_opp = int(value.gold_opp)

    enemies = [enemy for enemy in value.visible_enemies if enemy is not None]
    if len(enemies) > 2:
        raise ValueError("PlayerInput has more than two visible enemies")
    for index in range(2):
        if index < len(enemies):
            _copy_position(result.visible_enemies[index], enemies[index])
        else:
            result.visible_enemies[index] = Position(-1, -1)

    visible_npcs = tuple(value.visible_npcs)
    if len(visible_npcs) > MAX_NPCS:
        raise ValueError("PlayerInput has more than seven visible NPCs")
    result.num_visible_npcs = len(visible_npcs)
    for index in range(MAX_NPCS):
        if index < len(visible_npcs):
            npc_id, npc_position = visible_npcs[index]
            result.visible_npcs[index].id = int(npc_id)
            _copy_position(result.visible_npcs[index].pos, npc_position)
        else:
            result.visible_npcs[index].id = 0
            result.visible_npcs[index].pos = Position(-1, -1)

    snapshot = value.snapshot
    result.snapshot_valid = int(snapshot is not None)
    if snapshot is None:
        # game_api.h defines window_begin=-1 as the no-snapshot sentinel;
        # zero-initialization leaves every otherwise undefined payload field 0.
        result.snapshot.window_begin = -1
    else:
        result.snapshot.window_begin = int(snapshot.window_begin)
        result.snapshot.window_end = int(snapshot.window_end)
        if len(snapshot.regions) != REGION_COUNT:
            raise ValueError("snapshot must contain exactly five regions")
        for index, source in enumerate(snapshot.regions):
            destination = result.snapshot.regions[index]
            for field, _ctype in RegionStat._fields_:
                setattr(destination, field, int(getattr(source, field)))
    return result


# Friendly aliases used by external callers.
to_game_input = player_input_to_abi
convert_player_input = player_input_to_abi
AbiPosition = Position
AbiNpcInfo = NpcInfo
AbiRegionStat = RegionStat
AbiSnapshot = Snapshot
AbiGameInput = GameInput
AbiGameOutput = GameOutput


class StrategyError(RuntimeError):
    """Base class for strategy loading, execution, and contract failures."""


class StrategyLoadError(StrategyError):
    """The strategy shared object could not be loaded or bound."""


class StrategyOutputError(StrategyError):
    """The strategy returned a value outside the official output contract."""


class StrategyExecutionError(StrategyError):
    """A Python strategy callable raised while making a decision."""


@dataclass(frozen=True)
class DecisionCall:
    decision: PlayerDecision
    cost_ns: int


def validate_output(value: Any) -> PlayerDecision:
    """Return a validated engine decision or raise a clear ABI contract error."""
    try:
        return PlayerDecision.from_like(value)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise StrategyOutputError("illegal GameOutput: %s" % error) from error


class SharedObjectStrategy:
    """One isolated ``moveDecision`` image loaded from a unique temporary copy."""

    def __init__(self, path: os.PathLike[str] | str, *, name: Optional[str] = None) -> None:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise StrategyLoadError("strategy shared object does not exist: %s" % source)
        self.source_path = source
        self.name = name or source.stem
        self._temporary = tempfile.TemporaryDirectory(prefix="goldrush-abi-")
        copied = Path(self._temporary.name) / ("strategy" + (source.suffix or ".so"))
        try:
            shutil.copy2(source, copied)
            self._library = ctypes.CDLL(str(copied))
            function = self._library.moveDecision
            function.argtypes = [ctypes.POINTER(GameInput)]
            function.restype = GameOutput
            self._move_decision = function
        except (AttributeError, OSError) as error:
            self._temporary.cleanup()
            system = "%s %s" % (platform.system(), platform.machine())
            detail = str(error)
            raise StrategyLoadError(
                "cannot load %s on %s: %s. Official Linux x86_64 .so files "
                "must be executed in a compatible Linux x86_64 environment; "
                "they cannot be loaded natively on macOS ARM."
                % (source, system, detail)
            ) from error

    def decide(self, value: PlayerInput, *, measured: bool = False) -> DecisionCall:
        game_input = player_input_to_abi(value)
        if measured:
            started = perf_counter_ns()
            raw = self._move_decision(ctypes.byref(game_input))
            elapsed = perf_counter_ns() - started
        else:
            raw = self._move_decision(ctypes.byref(game_input))
            elapsed = 0
        return DecisionCall(validate_output(raw), elapsed)

    def __call__(self, value: PlayerInput) -> PlayerDecision:
        return self.decide(value).decision

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
            self._temporary = None  # type: ignore[assignment]

    def __enter__(self) -> "SharedObjectStrategy":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()


class CallableStrategy:
    """Runner-level adapter for local Python smoke tests and scripted policies."""

    def __init__(self, function: Callable[[PlayerInput], Any], *, name: Optional[str] = None) -> None:
        if not callable(function):
            raise TypeError("function must be callable")
        self.function = function
        self.name = name or getattr(function, "__name__", function.__class__.__name__)

    def decide(self, value: PlayerInput, *, measured: bool = False) -> DecisionCall:
        try:
            if measured:
                started = perf_counter_ns()
                raw = self.function(value)
                elapsed = perf_counter_ns() - started
            else:
                raw = self.function(value)
                elapsed = 0
        except Exception as error:
            raise StrategyExecutionError(
                "%s raised %s: %s" % (self.name, type(error).__name__, error)
            ) from error
        return DecisionCall(validate_output(raw), elapsed)

    def __call__(self, value: PlayerInput) -> PlayerDecision:
        return self.decide(value).decision

    def close(self) -> None:
        return None


class StayStrategy(CallableStrategy):
    """Built-in legal deterministic policy used for local smoke tests."""

    def __init__(self, *, name: str = "stay") -> None:
        super().__init__(lambda _value: ((4, 4, 4, 4, 4, 4, 6, 0, 0)), name=name)


__all__ = [
    "GRID_SIZE",
    "MAX_NPCS",
    "S",
    "REGION_COUNT",
    "Position",
    "NpcInfo",
    "RegionStat",
    "Snapshot",
    "GameInput",
    "GameOutput",
    "AbiPosition",
    "AbiNpcInfo",
    "AbiRegionStat",
    "AbiSnapshot",
    "AbiGameInput",
    "AbiGameOutput",
    "DecisionCall",
    "StrategyError",
    "StrategyLoadError",
    "StrategyOutputError",
    "StrategyExecutionError",
    "SharedObjectStrategy",
    "CallableStrategy",
    "StayStrategy",
    "player_input_to_abi",
    "to_game_input",
    "convert_player_input",
    "validate_output",
    "verify_abi_layout",
]
