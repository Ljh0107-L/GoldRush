"""Deterministic, auditable GoldRush map and generation scenarios.

This module deliberately models only terrain and generation.  It does not
implement movement, strategy adapters, NPCs, a command line, or an engine.
Every pseudo-random choice is made by a private ``random.Random(seed)`` while
``ScenarioGenerator`` is constructed; all 500 round intents therefore exist
before a strategy can run, and resolving an intent never consumes randomness.

Calibration status
------------------
The following are descriptive fits to the three 500-round logs summarized by
``reports/generation.json``; they are not published game constants:

* central count: Poisson mean 1.744; central values: uniform integers 1..10;
* outer renewal wait: uniform integers 8..16; rich region: uniform 2..5;
* outer event shapes and values: empirical categorical distributions;
* bomb waves: rounds 0, 20, ..., 480, with independent eligible-cell
  Bernoulli probability 0.0789.

The opening round's latent decomposition is unknown.  This scenario applies
the fitted regular central law to every round and does not invent a separate
opening seeding rule.  Outer coordinates marked ``2`` on official log line 2
are treated as an empirically fitted outer-gold hotspot.  The observed share is
618/1142 (54.1%): its raw per-cell rate ratio is 9.67, while the sampler-specific
weighted-without-replacement fit used here is approximately 11.336.  These
markers do not define or restrict bomb eligibility: bomb trials cover every
traversable non-wall cell.  Map 3 has recoverable walls but
no line-2 hotspot metadata, so its outer coordinate model necessarily falls
back to uniform weights; central and bomb generation remain available.

Generation locations can depend on actor and board state that cannot be known
at materialization time.  A ``RoundIntent`` consequently stores deterministic
rankings and bomb trials.  ``resolve_round`` filters those choices through a
caller-supplied ``SpawnState`` without touching an RNG.  The intent stream has
canonical JSON bytes and a SHA-256 digest for reproducibility and audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import random
from typing import Any, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple, Union


GRID_SIZE = 17
ROUND_COUNT = 500
BOMB_PERIOD = 20
CENTRAL_POISSON_MEAN = 1.744
BOMB_PROBABILITY = 0.0789
OUTER_HOTSPOT_EMPIRICAL_SHARE = 618.0 / 1142.0
OUTER_HOTSPOT_RAW_CELL_RATE_RATIO = (618.0 / 20.0) / (524.0 / 164.0)
# Expected-count fit for this module's exponential-race weighted-without-
# replacement sampler, using each observed event/region population and count.
OUTER_HOTSPOT_WEIGHT = 11.33648734667453
DEFAULT_MAPS_PATH = Path(__file__).with_name("maps.json")

Cell = Tuple[int, int]
Seed = Union[int, str, bytes, bytearray]

# Exact empirical histograms in reports/generation.json.  Tuple form fixes
# iteration order and makes the fitted model visible in the audit payload.
OUTER_CELL_COUNT_HISTOGRAM = (
    (7, 3), (8, 25), (9, 42), (10, 27), (11, 16), (12, 7), (13, 1),
)
RICH_CELL_COUNT_HISTOGRAM = ((3, 5), (4, 48), (5, 68))
RICH_TOTAL_HISTOGRAM = (
    (80, 11), (81, 1), (82, 1), (84, 9), (85, 5), (86, 2),
    (87, 3), (88, 5), (89, 2), (90, 4), (91, 1), (92, 5),
    (93, 5), (94, 8), (95, 1), (96, 5), (97, 4), (98, 3),
    (99, 4), (100, 5), (101, 2), (102, 3), (103, 3), (104, 5),
    (105, 2), (107, 4), (108, 4), (109, 3), (110, 4), (111, 5),
    (112, 2),
)
ORDINARY_OUTER_VALUE_HISTOGRAM = (
    (1, 81), (2, 75), (3, 58), (4, 68), (5, 60), (6, 64),
    (7, 53), (8, 57), (9, 40), (10, 38), (15, 1),
)


def region_id(row: int, col: int) -> int:
    """Return the zero-based-grid windmill region id (1 is central)."""
    if not (0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE):
        raise ValueError("cell outside 17x17 grid: %r" % ((row, col),))
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    return 5


def _normalise_rows(value: Any, size: int = GRID_SIZE) -> Tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("map rows must be a sequence of rows")
    rows = []
    for raw_row in value:
        if isinstance(raw_row, str):
            row = raw_row
        elif isinstance(raw_row, Sequence):
            row = "".join(str(cell) for cell in raw_row)
        else:
            raise ValueError("each map row must be a string or sequence")
        if len(row) != size:
            raise ValueError("map row has length %d, expected %d" % (len(row), size))
        if set(row) - {"0", "1", "2"}:
            raise ValueError("map rows may contain only official tokens 0, 1, and 2")
        rows.append(row)
    if len(rows) != size:
        raise ValueError("map has %d rows, expected %d" % (len(rows), size))
    return tuple(rows)


def _cells_with(rows: Sequence[str], token: str) -> FrozenSet[Cell]:
    return frozenset(
        (row, col)
        for row, values in enumerate(rows)
        for col, value in enumerate(values)
        if value == token
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class MapDefinition:
    """Immutable terrain plus optional line-2 outer-hotspot metadata.

    Token ``1`` is a wall.  Tokens ``0`` and ``2`` are traversable.  Token
    ``2`` is retained as ``outer_hotspot_cells`` because generation logs show
    strong outer-gold enrichment there; it never limits the bomb population.
    ``outer_hotspot_cells is None`` means the metadata is unavailable (map 3),
    while an empty set means a supplied official line explicitly had no 2s.
    """

    name: str
    rows: Tuple[str, ...]
    walls: FrozenSet[Cell]
    traversable: FrozenSet[Cell]
    outer_hotspot_cells: Optional[FrozenSet[Cell]]
    limited: bool = False
    source: str = ""

    def __post_init__(self) -> None:
        rows = _normalise_rows(self.rows)
        walls = _cells_with(rows, "1")
        floor = frozenset(
            (row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE)
        ) - walls
        hotspots = self.outer_hotspot_cells
        if hotspots is not None:
            hotspots = frozenset(hotspots)
            if not hotspots <= floor:
                raise ValueError("line-2 hotspot metadata includes a wall")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "walls", walls)
        object.__setattr__(self, "traversable", floor)
        object.__setattr__(self, "outer_hotspot_cells", hotspots)

    @property
    def fingerprint(self) -> str:
        """Stable SHA-256 of the terrain and available line-2 metadata."""
        return hashlib.sha256(_canonical_json_bytes({"rows": self.rows})).hexdigest()

    @classmethod
    def by_name(
        cls, name: str, maps_path: Union[str, os.PathLike[str]] = DEFAULT_MAPS_PATH
    ) -> "MapDefinition":
        """Load ``map1``, ``map2``, or ``map3`` from a maps registry."""
        return cls.from_json_file(maps_path, map_name=name)

    @classmethod
    def from_json_file(
        cls,
        path: Union[str, os.PathLike[str]],
        map_name: Optional[str] = None,
    ) -> "MapDefinition":
        """Load a registry entry, a single map object, or a line-2 JSON file."""
        file_path = Path(path)
        with file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, list):
            return cls.from_log_line2(payload, name=map_name or file_path.stem)

        if not isinstance(payload, Mapping):
            raise ValueError("map JSON root must be an object or official line-2 array")

        if "maps" in payload:
            maps = payload["maps"]
            if not isinstance(maps, Mapping):
                raise ValueError("maps registry has a non-object 'maps' member")
            if map_name is None:
                if len(maps) != 1:
                    raise ValueError("map_name is required for a multi-map registry")
                map_name = next(iter(maps))
            if map_name not in maps:
                raise KeyError("unknown map %r in %s" % (map_name, file_path))
            entry = maps[map_name]
            size = int(payload.get("grid_size", GRID_SIZE))
        else:
            entry = payload
            size = int(payload.get("grid_size", GRID_SIZE))
            map_name = map_name or str(payload.get("name", file_path.stem))

        if size != GRID_SIZE:
            raise ValueError("only the official 17x17 grid is supported")
        if not isinstance(entry, Mapping):
            raise ValueError("map entry must be an object")

        limited = bool(entry.get("limited", False))
        if "rows" in entry:
            rows = _normalise_rows(entry["rows"], size)
            hotspots: Optional[FrozenSet[Cell]] = _cells_with(rows, "2")
        elif "wall_rows" in entry:
            wall_rows = _normalise_rows(entry["wall_rows"], size)
            rows = tuple(row.replace("2", "0") for row in wall_rows)
            hotspots = None
            limited = True
        else:
            raise ValueError("map entry has neither 'rows' nor 'wall_rows'")

        source = "json:%s" % file_path.resolve()
        return cls(
            name=str(map_name), rows=rows, walls=_cells_with(rows, "1"),
            traversable=frozenset(), outer_hotspot_cells=hotspots,
            limited=limited, source=source,
        )

    @classmethod
    def from_log_line2(
        cls,
        line: Union[str, bytes, bytearray, Sequence[Sequence[Any]]],
        name: Optional[str] = None,
    ) -> "MapDefinition":
        """Load any official log line 2, supplied as JSON text or decoded rows."""
        decoded: Any = line
        if isinstance(line, (bytes, bytearray)):
            decoded = json.loads(bytes(line).decode("utf-8"))
        elif isinstance(line, str):
            decoded = json.loads(line)
        rows = _normalise_rows(decoded)

        inferred_name = name
        if inferred_name is None and DEFAULT_MAPS_PATH.exists():
            try:
                with DEFAULT_MAPS_PATH.open("r", encoding="utf-8") as handle:
                    registry = json.load(handle)
                for candidate, entry in registry.get("maps", {}).items():
                    if "rows" in entry and _normalise_rows(entry["rows"]) == rows:
                        inferred_name = str(candidate)
                        break
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

        return cls(
            name=inferred_name or "official-log-line-2",
            rows=rows,
            walls=_cells_with(rows, "1"),
            traversable=frozenset(),
            outer_hotspot_cells=_cells_with(rows, "2"),
            limited=False,
            source="official-log-line-2",
        )

    @classmethod
    def load(
        cls,
        source: Union["MapDefinition", str, bytes, bytearray, os.PathLike[str], Sequence[Sequence[Any]]],
        map_name: Optional[str] = None,
        maps_path: Union[str, os.PathLike[str]] = DEFAULT_MAPS_PATH,
    ) -> "MapDefinition":
        """Load by built-in name, JSON path, decoded rows, or official line 2."""
        if isinstance(source, cls):
            return source
        if isinstance(source, (bytes, bytearray)):
            return cls.from_log_line2(source, name=map_name)
        if isinstance(source, os.PathLike):
            return cls.from_json_file(source, map_name=map_name)
        if isinstance(source, str):
            stripped = source.strip()
            if stripped in {"map1", "map2", "map3"} and map_name is None:
                return cls.by_name(stripped, maps_path=maps_path)
            if stripped.startswith("["):
                return cls.from_log_line2(stripped, name=map_name)
            path = Path(source)
            if path.is_file():
                return cls.from_json_file(path, map_name=map_name)
            raise ValueError("map source is neither a known name, JSON file, nor line 2")
        return cls.from_log_line2(source, name=map_name)


@dataclass(frozen=True)
class GoldPlacementIntent:
    """A value intended for one cell in a particular windmill region."""

    source: str
    region: int
    value: int


@dataclass(frozen=True)
class GoldIntent:
    """Pre-materialized values and ranked coordinates for one gold event."""

    source: str
    placements: Tuple[GoldPlacementIntent, ...]
    cell_orders: Tuple[Tuple[int, Tuple[Cell, ...]], ...]
    rich_region: Optional[int] = None

    def order_for(self, region: int) -> Tuple[Cell, ...]:
        for candidate_region, cells in self.cell_orders:
            if candidate_region == region:
                return cells
        return ()


@dataclass(frozen=True)
class RoundIntent:
    """All random choices for a round, independent of runtime spawn state."""

    round: int
    central: GoldIntent
    outer: Optional[GoldIntent]
    bomb_trials: Optional[FrozenSet[Cell]]


@dataclass(frozen=True)
class SpawnState:
    """Runtime occupancy used to resolve a pre-materialized round intent.

    Actor and ``blocked_cells`` exclude both gold and bombs.  Existing bombs
    exclude gold.  Existing gold excludes bombs but does not, by itself,
    prevent an additive gold placement; callers can put such cells in
    ``gold_exclusions`` when their engine requires empty-cell-only spawning.
    ``bomb_exclusions`` and ``gold_exclusions`` are explicit rule hooks.
    """

    actor_cells: FrozenSet[Cell] = field(default_factory=frozenset)
    gold_cells: FrozenSet[Cell] = field(default_factory=frozenset)
    bomb_cells: FrozenSet[Cell] = field(default_factory=frozenset)
    blocked_cells: FrozenSet[Cell] = field(default_factory=frozenset)
    gold_exclusions: FrozenSet[Cell] = field(default_factory=frozenset)
    bomb_exclusions: FrozenSet[Cell] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for attribute in (
            "actor_cells", "gold_cells", "bomb_cells", "blocked_cells",
            "gold_exclusions", "bomb_exclusions",
        ):
            cells = frozenset(getattr(self, attribute))
            for cell in cells:
                if (
                    not isinstance(cell, tuple) or len(cell) != 2
                    or not all(isinstance(part, int) for part in cell)
                    or not (0 <= cell[0] < GRID_SIZE and 0 <= cell[1] < GRID_SIZE)
                ):
                    raise ValueError("invalid spawn-state cell: %r" % (cell,))
            object.__setattr__(self, attribute, cells)


@dataclass(frozen=True, order=True)
class GoldAddition:
    """Resolved additive gold amount at one traversable cell."""

    row: int
    col: int
    value: int
    source: str
    region: int

    @property
    def cell(self) -> Cell:
        return (self.row, self.col)


@dataclass(frozen=True)
class UnplacedGold:
    """An intent that could not find a non-excluded cell in its region."""

    source: str
    region: int
    value: int


@dataclass(frozen=True)
class RoundEvents:
    """Resolved per-round gold additions and optional complete bomb refresh."""

    round: int
    gold_additions: Tuple[GoldAddition, ...]
    bomb_refresh: Optional[FrozenSet[Cell]]
    unplaced_gold: Tuple[UnplacedGold, ...] = ()
    rejected_bomb_trials: FrozenSet[Cell] = field(default_factory=frozenset)

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "round": self.round,
            "gold_additions": [
                {
                    "cell": [item.row, item.col], "region": item.region,
                    "source": item.source, "value": item.value,
                }
                for item in self.gold_additions
            ],
            "bomb_refresh": None if self.bomb_refresh is None else [
                list(cell) for cell in sorted(self.bomb_refresh)
            ],
            "unplaced_gold": [
                {"region": item.region, "source": item.source, "value": item.value}
                for item in self.unplaced_gold
            ],
            "rejected_bomb_trials": [
                list(cell) for cell in sorted(self.rejected_bomb_trials)
            ],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.as_dict())


class ScenarioGenerator:
    """Pre-materialize and resolve a deterministic 500-round scenario."""

    round_count = ROUND_COUNT

    def __init__(
        self,
        map_definition: Union[
            MapDefinition, str, bytes, bytearray, os.PathLike[str], Sequence[Sequence[Any]]
        ],
        seed: Seed,
        *,
        map_name: Optional[str] = None,
        maps_path: Union[str, os.PathLike[str]] = DEFAULT_MAPS_PATH,
    ) -> None:
        self.map = MapDefinition.load(
            map_definition, map_name=map_name, maps_path=maps_path
        )
        self.seed = self._validate_seed(seed)

        # This RNG is local and dies after construction.  No public operation,
        # including resolution, can advance or perturb the scenario stream.
        rng = random.Random(self.seed)
        self._intents = self._materialize(rng)
        self._canonical_events = _canonical_json_bytes(self._canonical_payload())
        self._digest = hashlib.sha256(self._canonical_events).hexdigest()

    @staticmethod
    def _validate_seed(seed: Seed) -> Seed:
        if isinstance(seed, bytearray):
            return bytes(seed)
        if not isinstance(seed, (int, str, bytes)):
            raise TypeError("seed must be int, str, bytes, or bytearray")
        return seed

    @staticmethod
    def _seed_audit(seed: Seed) -> Mapping[str, Any]:
        if isinstance(seed, bytes):
            return {"type": "bytes", "hex": seed.hex()}
        if isinstance(seed, int):
            return {"type": "int", "value": str(seed)}
        return {"type": "str", "value": seed}

    @property
    def intents(self) -> Tuple[RoundIntent, ...]:
        return self._intents

    @property
    def rounds(self) -> Tuple[RoundIntent, ...]:
        """Alias exposing the 500 pre-materialized round intents."""
        return self._intents

    @property
    def canonical_events(self) -> bytes:
        """Byte-stable canonical serialization of map, model, seed, and intents."""
        return self._canonical_events

    @property
    def digest(self) -> str:
        """SHA-256 hex digest of ``canonical_events``."""
        return self._digest

    def to_canonical_bytes(self) -> bytes:
        return self._canonical_events

    def canonical_digest(self) -> str:
        return self._digest

    def intent_for_round(self, round_number: int) -> RoundIntent:
        if not 0 <= round_number < ROUND_COUNT:
            raise IndexError("round must be in 0..499")
        return self._intents[round_number]

    def resolve_round(
        self, round_number: int, state: Optional[SpawnState] = None
    ) -> RoundEvents:
        """Resolve one intent against state without consuming randomness."""
        intent = self.intent_for_round(round_number)
        state = SpawnState() if state is None else state
        if not isinstance(state, SpawnState):
            raise TypeError("state must be SpawnState or None")

        gold_blocked = (
            state.actor_cells | state.bomb_cells | state.blocked_cells
            | state.gold_exclusions | self.map.walls
        )
        used: set[Cell] = set()
        additions = []
        unplaced = []
        for gold_intent in (intent.central, intent.outer):
            if gold_intent is None:
                continue
            cursors = {region: 0 for region, _ in gold_intent.cell_orders}
            orders = dict(gold_intent.cell_orders)
            for placement in gold_intent.placements:
                order = orders.get(placement.region, ())
                cursor = cursors.get(placement.region, 0)
                selected: Optional[Cell] = None
                while cursor < len(order):
                    candidate = order[cursor]
                    cursor += 1
                    if candidate not in gold_blocked and candidate not in used:
                        selected = candidate
                        break
                cursors[placement.region] = cursor
                if selected is None:
                    unplaced.append(UnplacedGold(
                        source=placement.source, region=placement.region,
                        value=placement.value,
                    ))
                else:
                    used.add(selected)
                    additions.append(GoldAddition(
                        row=selected[0], col=selected[1], value=placement.value,
                        source=placement.source, region=placement.region,
                    ))

        refresh: Optional[FrozenSet[Cell]] = None
        rejected: FrozenSet[Cell] = frozenset()
        if intent.bomb_trials is not None:
            bomb_blocked = (
                state.actor_cells | state.gold_cells | state.blocked_cells
                | state.bomb_exclusions | self.map.walls | frozenset(used)
            )
            refresh = frozenset(intent.bomb_trials - bomb_blocked)
            rejected = frozenset(intent.bomb_trials & bomb_blocked)

        return RoundEvents(
            round=round_number, gold_additions=tuple(additions),
            bomb_refresh=refresh, unplaced_gold=tuple(unplaced),
            rejected_bomb_trials=rejected,
        )

    def resolve_all(
        self, states: Optional[Iterable[SpawnState]] = None
    ) -> Tuple[RoundEvents, ...]:
        """Resolve all rounds; states must provide exactly 500 snapshots."""
        if states is None:
            return tuple(self.resolve_round(round_number) for round_number in range(ROUND_COUNT))
        state_tuple = tuple(states)
        if len(state_tuple) != ROUND_COUNT:
            raise ValueError("states must contain exactly 500 SpawnState objects")
        return tuple(
            self.resolve_round(round_number, state_tuple[round_number])
            for round_number in range(ROUND_COUNT)
        )

    @staticmethod
    def _poisson(rng: random.Random, mean: float) -> int:
        threshold = math.exp(-mean)
        product = 1.0
        count = 0
        while product > threshold:
            count += 1
            product *= rng.random()
        return count - 1

    @staticmethod
    def _weighted_choice(
        rng: random.Random, histogram: Sequence[Tuple[int, int]]
    ) -> int:
        total = sum(weight for _, weight in histogram)
        target = rng.randrange(total)
        for value, weight in histogram:
            if target < weight:
                return value
            target -= weight
        raise AssertionError("unreachable weighted-choice tail")

    def _uniform_order(self, rng: random.Random, region: int) -> Tuple[Cell, ...]:
        cells = sorted(cell for cell in self.map.traversable if region_id(*cell) == region)
        rng.shuffle(cells)
        return tuple(cells)

    def _outer_weighted_order(
        self, rng: random.Random, region: int
    ) -> Tuple[Cell, ...]:
        cells = sorted(cell for cell in self.map.traversable if region_id(*cell) == region)
        hotspots = self.map.outer_hotspot_cells
        if hotspots is None or not hotspots:
            rng.shuffle(cells)
            return tuple(cells)

        # Exponential-race ordering is weighted sampling without replacement.
        # All keys are materialized now; runtime filtering merely skips cells.
        keyed = []
        for cell in cells:
            weight = OUTER_HOTSPOT_WEIGHT if cell in hotspots else 1.0
            uniform = rng.random()
            while uniform == 0.0:  # Random.random currently excludes 0 rarely, not contractually.
                uniform = rng.random()
            keyed.append((-math.log(uniform) / weight, cell))
        keyed.sort()
        return tuple(cell for _, cell in keyed)

    def _make_central(self, rng: random.Random) -> GoldIntent:
        count = self._poisson(rng, CENTRAL_POISSON_MEAN)
        placements = tuple(
            GoldPlacementIntent("central", 1, rng.randint(1, 10))
            for _ in range(count)
        )
        return GoldIntent(
            source="central", placements=placements,
            cell_orders=((1, self._uniform_order(rng, 1)),),
        )

    def _make_outer(self, rng: random.Random) -> GoldIntent:
        rich_region = rng.randint(2, 5)

        # The aggregate marginals are reported, but their exact joint law is
        # not.  Draw and condition only on confirmed support constraints.
        while True:
            total_count = self._weighted_choice(rng, OUTER_CELL_COUNT_HISTOGRAM)
            rich_count = self._weighted_choice(rng, RICH_CELL_COUNT_HISTOGRAM)
            ordinary_count = total_count - rich_count
            if 3 <= ordinary_count <= 9:
                break
        while True:
            rich_total = self._weighted_choice(rng, RICH_TOTAL_HISTOGRAM)
            quotient, remainder = divmod(rich_total, rich_count)
            if 16 <= quotient and quotient + (1 if remainder else 0) <= 37:
                break

        rich_values = [quotient + (index < remainder) for index in range(rich_count)]
        rng.shuffle(rich_values)
        ordinary_values = [
            self._weighted_choice(rng, ORDINARY_OUTER_VALUE_HISTOGRAM)
            for _ in range(ordinary_count)
        ]

        # Assign ordinary values to the other regions by least accumulated
        # value.  This preserves the empirically identified rich region while
        # retaining every sampled ordinary value.
        other_regions = [region for region in range(2, 6) if region != rich_region]
        rng.shuffle(other_regions)
        totals = {region: 0 for region in other_regions}
        ordinary_placements = []
        for value in ordinary_values:
            region = min(other_regions, key=lambda item: (totals[item], other_regions.index(item)))
            totals[region] += value
            ordinary_placements.append(GoldPlacementIntent("outer-ordinary", region, value))

        placements = tuple(
            GoldPlacementIntent("outer-rich", rich_region, value)
            for value in rich_values
        ) + tuple(ordinary_placements)
        orders = tuple(
            (region, self._outer_weighted_order(rng, region))
            for region in range(2, 6)
        )
        return GoldIntent(
            source="outer", placements=placements, cell_orders=orders,
            rich_region=rich_region,
        )

    def _materialize(self, rng: random.Random) -> Tuple[RoundIntent, ...]:
        outer_rounds = set()
        next_outer = rng.randint(8, 16)
        while next_outer < ROUND_COUNT:
            outer_rounds.add(next_outer)
            next_outer += rng.randint(8, 16)

        traversable = tuple(sorted(self.map.traversable))
        intents = []
        for round_number in range(ROUND_COUNT):
            central = self._make_central(rng)
            outer = self._make_outer(rng) if round_number in outer_rounds else None
            bomb_trials: Optional[FrozenSet[Cell]] = None
            if round_number % BOMB_PERIOD == 0:
                bomb_trials = frozenset(
                    cell for cell in traversable
                    if rng.random() < BOMB_PROBABILITY
                )
            intents.append(RoundIntent(
                round=round_number, central=central, outer=outer,
                bomb_trials=bomb_trials,
            ))
        return tuple(intents)

    @staticmethod
    def _gold_intent_audit(intent: Optional[GoldIntent]) -> Any:
        if intent is None:
            return None
        return {
            "source": intent.source,
            "rich_region": intent.rich_region,
            "placements": [
                {"region": item.region, "source": item.source, "value": item.value}
                for item in intent.placements
            ],
            "cell_orders": {
                str(region): [list(cell) for cell in cells]
                for region, cells in intent.cell_orders
            },
        }

    def _canonical_payload(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "map": {
                "rows": list(self.map.rows),
                "limited": self.map.limited,
                "outer_hotspot_metadata_available": self.map.outer_hotspot_cells is not None,
            },
            "seed": self._seed_audit(self.seed),
            "round_count": ROUND_COUNT,
            "model": {
                "status": "descriptive fits, not official constants",
                "central_poisson_mean": CENTRAL_POISSON_MEAN,
                "central_value_uniform": [1, 10],
                "opening_rule": "unknown; regular central fit applied",
                "outer_wait_uniform": [8, 16],
                "outer_rich_region_uniform": [2, 5],
                "outer_hotspot_empirical_share": OUTER_HOTSPOT_EMPIRICAL_SHARE,
                "outer_hotspot_raw_cell_rate_ratio": OUTER_HOTSPOT_RAW_CELL_RATE_RATIO,
                "outer_hotspot_weight": OUTER_HOTSPOT_WEIGHT,
                "outer_hotspot_weight_fit": "approximate, sampler-specific expected-count fit",
                "outer_hotspot_role": "outer-gold coordinate weight, never bomb eligibility",
                "bomb_period": BOMB_PERIOD,
                "bomb_probability": BOMB_PROBABILITY,
                "bomb_population": "all traversable cells before runtime exclusions",
            },
            "rounds": [
                {
                    "round": intent.round,
                    "central": self._gold_intent_audit(intent.central),
                    "outer": self._gold_intent_audit(intent.outer),
                    "bomb_trials": None if intent.bomb_trials is None else [
                        list(cell) for cell in sorted(intent.bomb_trials)
                    ],
                }
                for intent in self._intents
            ],
        }


def hotspot_fit_sanity(
    map_definition: Union[
        MapDefinition, str, bytes, bytearray, os.PathLike[str], Sequence[Sequence[Any]]
    ] = "map1",
    seeds: Iterable[Seed] = tuple(range(32)),
    tolerance: float = 0.02,
) -> Mapping[str, Any]:
    """Deterministically check the sampler-specific hotspot calibration.

    With map1 and seeds 0..31, the current implementation produces per-seed
    shares in [0.5064935064935064, 0.58311345646438], arithmetic mean
    0.5466220426503278, and pooled share 6871/12571 = 0.5465754514358444.
    The assertion compares the pooled result with the empirical 618/1142.
    This helper is opt-in and never runs during scenario construction.
    """
    if tolerance < 0.0:
        raise ValueError("tolerance must be non-negative")
    map_object = MapDefinition.load(map_definition)
    if not map_object.outer_hotspot_cells:
        raise ValueError("hotspot sanity check requires line-2 hotspot metadata")
    seed_values = tuple(seeds)
    if not seed_values:
        raise ValueError("at least one seed is required")

    shares = []
    hotspot_count = 0
    outer_count = 0
    for seed in seed_values:
        generator = ScenarioGenerator(map_object, seed)
        seed_hotspots = 0
        seed_outer = 0
        for round_number in range(ROUND_COUNT):
            for addition in generator.resolve_round(round_number).gold_additions:
                if addition.source.startswith("outer"):
                    seed_outer += 1
                    if addition.cell in map_object.outer_hotspot_cells:
                        seed_hotspots += 1
        if not seed_outer:
            raise AssertionError("materialized seed had no outer additions")
        shares.append(seed_hotspots / float(seed_outer))
        hotspot_count += seed_hotspots
        outer_count += seed_outer

    pooled_share = hotspot_count / float(outer_count)
    if abs(pooled_share - OUTER_HOTSPOT_EMPIRICAL_SHARE) > tolerance:
        raise AssertionError(
            "hotspot fit %.12f differs from empirical %.12f by more than %.12f"
            % (pooled_share, OUTER_HOTSPOT_EMPIRICAL_SHARE, tolerance)
        )
    return {
        "seed_count": len(seed_values),
        "minimum_seed_share": min(shares),
        "maximum_seed_share": max(shares),
        "mean_seed_share": sum(shares) / len(shares),
        "pooled_hotspot_cells": hotspot_count,
        "pooled_outer_cells": outer_count,
        "pooled_share": pooled_share,
        "empirical_hotspot_cells": 618,
        "empirical_outer_cells": 1142,
        "empirical_share": OUTER_HOTSPOT_EMPIRICAL_SHARE,
        "absolute_error": abs(pooled_share - OUTER_HOTSPOT_EMPIRICAL_SHARE),
        "tolerance": tolerance,
    }


__all__ = [
    "BOMB_PERIOD", "BOMB_PROBABILITY", "CENTRAL_POISSON_MEAN",
    "DEFAULT_MAPS_PATH", "GRID_SIZE", "OUTER_HOTSPOT_EMPIRICAL_SHARE",
    "OUTER_HOTSPOT_RAW_CELL_RATE_RATIO", "OUTER_HOTSPOT_WEIGHT",
    "ROUND_COUNT", "GoldAddition", "GoldIntent", "GoldPlacementIntent",
    "MapDefinition", "RoundEvents", "RoundIntent", "ScenarioGenerator",
    "SpawnState", "UnplacedGold", "hotspot_fit_sanity", "region_id",
]
