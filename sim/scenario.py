"""Deterministic, auditable GoldRush map and generation scenarios.

This module deliberately models only terrain and generation.  It does not
implement movement, strategy adapters, NPCs, a command line, or an engine.
Every pseudo-random choice is made by a private ``random.Random(seed)`` while
``ScenarioGenerator`` is constructed; all 500 round intents therefore exist
before a strategy can run, and resolving an intent never consumes randomness.

Calibration status
------------------
The following are descriptive fits, not published game constants:

* central attempts: Poisson mean ``CENTRAL_ATTEMPT_MEAN`` over the whole 9x9,
  positioned by a separable centripetal law, an attempt that lands on a wall
  produces nothing; central values: uniform integers 1..11;
* outer renewal wait: uniform integers 8..16; rich region: uniform 2..5;
* outer event shapes and values: empirical categorical distributions;
* bomb waves: rounds 0, 20, ..., 480, with independent eligible-cell
  Bernoulli probability 0.0789.

Outer coordinates marked ``2`` on official log line 2 are the rich arm's
hotspots: a rich event puts its high values on the picked arm's own token-2
cells and nowhere else, while the ordinary low values are spread uniformly over
the traversable cells of the other three arms, hotspot or not.  These markers do
not define or restrict bomb eligibility: bomb trials cover every traversable
non-wall cell.  All three official maps carry exactly 20 token-2 cells, exactly
five per windmill arm.  A map with no hotspot metadata falls back to uniform
placement inside the rich arm, which keeps such maps runnable but loses the
spatial concentration.

Two independent measurement channels disagree about the low-value law and the
disagreement is recorded rather than resolved; see ``GENERATION.md`` section 3.2.
The latent placement records of three full-information logs give uniform 1..10
(n=2611).  The grid-increment channel -- 18 full-vision platform probe games,
which is the channel a strategy and every simulator reading actually consume --
gives a flat 1..11 (n=6607 central events, chi-square 8.84 on 10 degrees of
freedom against uniform 1..11, and a cliff rather than a tail above 11, which
rules out same-round stacking as the explanation).  This module follows the grid
channel because the grid is what the engine shows and what the acceptance
measurement reads.

The opening round's latent decomposition is unknown.  This scenario applies
the fitted regular central law to every round and does not invent a separate
opening seeding rule.

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
# Historical constant, kept for provenance and for readers of the old reports:
# the latent-channel mean of *placed* central cells per round on map1 (n=1500).
# It is a post-rejection quantity and therefore map specific, which is exactly
# why it cannot be the sampler's input; see CENTRAL_ATTEMPT_MEAN.
CENTRAL_POISSON_MEAN = 1.744
# Sampler input: attempts per round over the whole central 9x9, walls included.
# One global constant; every per-map difference comes out of the geometry, so no
# map is special-cased anywhere.  Derivation, in two steps that are both stated
# because the second one is a calibration and not a measurement:
#   1. the platform's measured per-game central gold pooled over the three maps
#      (14043 gold, loose per-cell caliber) divided by 500 rounds, the 6.0 mean
#      value and the summed open-weight shares 0.8073+0.8873+0.4261, gives 2.2073
#      placements per round of *open* weight;
#   2. an attempt is also lost when the cell is occupied, and the simulator's own
#      grid puts that at about 11% of central cell-rounds (bombs plus npcs), so
#      the attempt rate is 2.2073 / 0.889.
# Residual after one pass: the simulator's measured central gold is 0.95 / 1.00 /
# 0.95 of the platform's on map1 / map2 / map3, i.e. 2.8% low pooled.  Left as is
# rather than rescaled again, because a second pass would only trade map1 and
# map3's -5% for map2's +3%.
CENTRAL_ATTEMPT_MEAN = 2.4829
# Grid-increment channel, 18 full-vision platform probe games, n=6607 central
# events pooled over the three maps: 611 602 557 587 597 638 601 602 596 580 636
# for values 1..11, chi-square 8.84 on df=10 against uniform, mean 6.0285.
CENTRAL_VALUE_MAX = 11
# Separable centripetal position law for the central 9x9, from GENERATION.md 3.3
# (row and column marginals of the per-cell placement frequency).  Indices are
# offsets from the top-left of the central square, i.e. rows and columns 4..12.
# A cell's weight is row weight times column weight; the population includes
# walls, and an attempt that lands on a wall is dropped rather than relocated.
CENTRAL_ROW_WEIGHTS = (22.3, 38.0, 41.9, 46.0, 65.2, 47.4, 43.9, 36.9, 25.6)
CENTRAL_COL_WEIGHTS = (20.0, 33.3, 41.6, 52.9, 56.3, 50.6, 40.0, 33.9, 22.8)
BOMB_PROBABILITY = 0.0789
OUTER_HOTSPOT_EMPIRICAL_SHARE = 618.0 / 1142.0
OUTER_HOTSPOT_RAW_CELL_RATE_RATIO = (618.0 / 20.0) / (524.0 / 164.0)
# Retired.  This was an expected-count fit for a weighted-without-replacement
# sampler that put a hotspot bonus on all four arms at once, which diluted rich
# values onto ordinary cells and ordinary values onto hotspots.  The structural
# rule replaced it: rich values go only to the rich arm's hotspots, ordinary
# values only to the other three arms.  Kept as a constant because published
# reports quote it; no sampler reads it any more.
OUTER_HOTSPOT_WEIGHT = 11.33648734667453
DEFAULT_MAPS_PATH = Path(__file__).with_name("maps.json")
NAMED_MAP_PATHS = (
    DEFAULT_MAPS_PATH,
    Path(__file__).with_name("maps_unknown.json"),
    Path(__file__).with_name("maps_final_photos.json"),
)

Cell = Tuple[int, int]
Seed = Union[int, str, bytes, bytearray]

# Exact empirical histograms in reports/generation.json.  Tuple form fixes
# iteration order and makes the fitted model visible in the audit payload.
OUTER_CELL_COUNT_HISTOGRAM = (
    (7, 3), (8, 25), (9, 42), (10, 27), (11, 16), (12, 7), (13, 1),
)
# No longer an input.  This was read as "how many hotspots does a rich event
# choose", and the sampler picked that many at random and lost the share of any
# it could not place.  The grid channel refutes that: on 46 fully-visible arm
# events the hit count equalled five minus the number of blocked hotspots in
# 46 of 46 cases, and the arm total stayed inside the [80, 112] support at every
# hit count (means 92.7 / 94.0 / 96.9 for 3 / 4 / 5 hits).  So a rich event
# splits its total over whichever hotspots are free, and this histogram is a
# free-hotspot-count observable, i.e. an emergent prediction to check rather
# than a knob to set.  See GENERATION.md 4.3.
RICH_CELL_COUNT_HISTOGRAM = ((3, 5), (4, 48), (5, 68))
# Direct marginal from GENERATION.md 4.4: ordinary cells touched per outer event
# (n=121, mean 4.909).  Replaces deriving the ordinary count as
# OUTER_CELL_COUNT_HISTOGRAM minus RICH_CELL_COUNT_HISTOGRAM, which needed a
# rejection loop and leaned on the retired rich-count draw.
ORDINARY_CELL_COUNT_HISTOGRAM = (
    (3, 9), (4, 41), (5, 36), (6, 25), (7, 8), (8, 1), (9, 1),
)
RICH_TOTAL_HISTOGRAM = (
    (80, 11), (81, 1), (82, 1), (84, 9), (85, 5), (86, 2),
    (87, 3), (88, 5), (89, 2), (90, 4), (91, 1), (92, 5),
    (93, 5), (94, 8), (95, 1), (96, 5), (97, 4), (98, 3),
    (99, 4), (100, 5), (101, 2), (102, 3), (103, 3), (104, 5),
    (105, 2), (107, 4), (108, 4), (109, 3), (110, 4), (111, 5),
    (112, 2),
)
ORDINARY_OUTER_VALUE_HISTOGRAM = (
    (1, 162), (2, 166), (3, 140), (4, 130), (5, 132), (6, 98),
    (7, 122), (8, 95), (9, 95), (10, 110), (11, 93), (12, 1),
    (16, 1), (19, 1),
)
# Superseded by the histogram above, kept so both channels stay visible.  It is
# the latent placement record of three full-information logs (n=595, mean 4.947);
# the active histogram is the grid-increment channel of 18 full-vision platform
# probe games (n=1346, mean 5.4391), measured on outer token-0 cells only, i.e.
# exactly the population the ordinary stream is allowed to land on.  The two
# agree on the declining shape and differ by one extra value bucket, the same
# 1..10 versus 1..11 disagreement seen in the central stream.  The three high
# outliers are kept because the platform shows the same 0.2 percent leak of a
# high value onto an ordinary cell (GENERATION.md 4.3 reports 547/548 = 99.82%
# of high placements on token-2 cells).
ORDINARY_OUTER_VALUE_HISTOGRAM_LATENT_CHANNEL = (
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
        """Load a map by name from the primary or auxiliary registries."""
        primary = Path(maps_path)
        registries = (primary,)
        if primary == DEFAULT_MAPS_PATH:
            registries = NAMED_MAP_PATHS
        for registry_path in registries:
            if not registry_path.exists():
                continue
            try:
                with registry_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            maps = payload.get("maps") if isinstance(payload, Mapping) else None
            if isinstance(maps, Mapping) and name in maps:
                return cls.from_json_file(registry_path, map_name=name)
        raise KeyError("unknown registered map %r" % name)

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
        if inferred_name is None:
            for registry_path in NAMED_MAP_PATHS:
                if not registry_path.exists():
                    continue
                try:
                    with registry_path.open("r", encoding="utf-8") as handle:
                        registry = json.load(handle)
                    for candidate, entry in registry.get("maps", {}).items():
                        if "rows" in entry and _normalise_rows(entry["rows"]) == rows:
                            inferred_name = str(candidate)
                            break
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
                if inferred_name is not None:
                    break

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
            if stripped.startswith("["):
                return cls.from_log_line2(stripped, name=map_name)
            path = Path(source).expanduser()
            if path.is_file():
                return cls.from_json_file(path, map_name=map_name)
            if map_name is None:
                try:
                    return cls.by_name(stripped, maps_path=maps_path)
                except KeyError:
                    pass
            raise ValueError("map source is neither a known name, JSON file, nor line 2")
        return cls.from_log_line2(source, name=map_name)


@dataclass(frozen=True)
class GoldPlacementIntent:
    """A value intended for one cell in a particular windmill region.

    ``cell`` is the coordinate the generator actually drew.  A bound placement
    is all-or-nothing: if that cell is unavailable at resolve time the value is
    not placed anywhere.  ``cell is None`` selects the legacy behaviour, where
    the value walks the region's ranked ``cell_orders`` until it finds a free
    coordinate.  The distinction is not cosmetic -- it is the difference between
    a wall or an actor destroying gold and a wall or an actor merely displacing
    it, and the platform's per-map central totals only match the first reading
    (GENERATION.md 3.4).
    """

    source: str
    region: int
    value: int
    cell: Optional[Cell] = None


@dataclass(frozen=True)
class GoldPoolIntent:
    """A total to be divided evenly among whichever of ``cells`` is free.

    This is the rich outer event.  The platform conserves its total: a blocked
    hotspot does not destroy its share, the same total is split over the free
    hotspots instead, which is why single-cell rich amounts reach 37 (a 112 total
    over three free cells) while the arm total never leaves [80, 112].  Evidence
    and the refutation of the destroy-the-share reading are in GENERATION.md 4.3.
    """

    source: str
    region: int
    total: int
    cells: Tuple[Cell, ...]


@dataclass(frozen=True)
class GoldIntent:
    """Pre-materialized values and ranked coordinates for one gold event."""

    source: str
    placements: Tuple[GoldPlacementIntent, ...]
    cell_orders: Tuple[Tuple[int, Tuple[Cell, ...]], ...]
    rich_region: Optional[int] = None
    rich_degraded: bool = False
    pools: Tuple[GoldPoolIntent, ...] = ()

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
                if placement.cell is not None:
                    # Bound placement: the coordinate was drawn at materialization
                    # time, so an unavailable cell destroys the value instead of
                    # displacing it onto a neighbour.
                    candidate = placement.cell
                    if candidate in gold_blocked or candidate in used:
                        unplaced.append(UnplacedGold(
                            source=placement.source, region=placement.region,
                            value=placement.value,
                        ))
                    else:
                        used.add(candidate)
                        additions.append(GoldAddition(
                            row=candidate[0], col=candidate[1],
                            value=placement.value, source=placement.source,
                            region=placement.region,
                        ))
                    continue
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

            for pool in gold_intent.pools:
                # Conserve the total: divide it over the free cells only.  The
                # remainder goes to the earliest free cells in the materialized
                # order, so nothing depends on runtime randomness.
                free = [
                    cell for cell in pool.cells
                    if cell not in gold_blocked and cell not in used
                ]
                if not free:
                    unplaced.append(UnplacedGold(
                        source=pool.source, region=pool.region, value=pool.total,
                    ))
                    continue
                quotient, remainder = divmod(pool.total, len(free))
                for index, cell in enumerate(free):
                    used.add(cell)
                    additions.append(GoldAddition(
                        row=cell[0], col=cell[1],
                        value=quotient + (1 if index < remainder else 0),
                        source=pool.source, region=pool.region,
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

    @staticmethod
    def _race_order(
        rng: random.Random, weighted_cells: Sequence[Tuple[Cell, float]]
    ) -> Tuple[Cell, ...]:
        """Weighted sampling without replacement, materialized as a full order.

        An exponential race: the cell with the smallest ``-log(U)/weight`` is the
        first draw, and truncating the order at *k* is a weighted draw of *k*
        distinct cells.  Every call consumes exactly one uniform per candidate,
        so the stream position does not depend on how many cells are kept.
        """
        keyed = []
        for cell, weight in weighted_cells:
            uniform = rng.random()
            while uniform == 0.0:  # Random.random currently excludes 0 rarely, not contractually.
                uniform = rng.random()
            keyed.append((-math.log(uniform) / weight, cell))
        keyed.sort()
        return tuple(cell for _, cell in keyed)

    def _central_candidate_order(self, rng: random.Random) -> Tuple[Cell, ...]:
        """Order all 81 central coordinates, walls included, by the position law.

        Walls stay in the population on purpose.  An attempt that draws a wall is
        dropped, which is what makes the per-map central yield fall with the
        walled-off share of the central square instead of being pushed onto the
        surviving cells.
        """
        weighted = [
            ((row, col), CENTRAL_ROW_WEIGHTS[row - 4] * CENTRAL_COL_WEIGHTS[col - 4])
            for row in range(4, 13)
            for col in range(4, 13)
        ]
        return self._race_order(rng, weighted)

    def _uniform_sample(
        self, rng: random.Random, pool: Sequence[Cell], count: int
    ) -> Tuple[Cell, ...]:
        cells = list(pool)
        rng.shuffle(cells)
        return tuple(cells[:count])

    def _region_hotspots(self, region: int) -> Tuple[Cell, ...]:
        hotspots = self.map.outer_hotspot_cells or frozenset()
        return tuple(sorted(cell for cell in hotspots if region_id(*cell) == region))

    def _region_traversable(self, region: int) -> Tuple[Cell, ...]:
        return tuple(sorted(
            cell for cell in self.map.traversable if region_id(*cell) == region
        ))

    def _make_central(self, rng: random.Random) -> GoldIntent:
        attempts = self._poisson(rng, CENTRAL_ATTEMPT_MEAN)
        order = self._central_candidate_order(rng)
        # Values are drawn for every attempt, including the ones that will be
        # dropped on a wall, so the value stream stays independent of terrain.
        values = [rng.randint(1, CENTRAL_VALUE_MAX) for _ in range(attempts)]
        placements = tuple(
            GoldPlacementIntent("central", 1, value, cell)
            for value, cell in zip(values, order[:attempts])
            if cell not in self.map.walls
        )
        return GoldIntent(source="central", placements=placements, cell_orders=())

    def _make_outer(self, rng: random.Random) -> GoldIntent:
        rich_region = rng.randint(2, 5)
        ordinary_count = self._weighted_choice(rng, ORDINARY_CELL_COUNT_HISTOGRAM)
        rich_total = self._weighted_choice(rng, RICH_TOTAL_HISTOGRAM)
        ordinary_values = [
            self._weighted_choice(rng, ORDINARY_OUTER_VALUE_HISTOGRAM)
            for _ in range(ordinary_count)
        ]

        # The rich arm spends its whole total on its own token-2 cells and
        # nowhere else; 547 of 548 observed high outer placements sat on a
        # token-2 cell (GENERATION.md 4.3).  The total is pooled rather than
        # pre-split because the platform divides it over the hotspots that are
        # free at generation time, which is also where the 16..37 single-cell
        # range comes from.  A map with no hotspot metadata has no such cells, so
        # it degrades to a same-sized pool of ordinary arm cells -- runnable, but
        # with the spatial concentration lost.
        rich_pool = self._region_hotspots(rich_region)
        rich_degraded = not rich_pool
        if rich_degraded:
            rich_pool = self._uniform_sample(
                rng, self._region_traversable(rich_region),
                len(RICH_CELL_COUNT_HISTOGRAM) + 2,
            )
        rich_cells = self._uniform_sample(rng, rich_pool, len(rich_pool))

        # Ordinary values never touch the rich arm (594 of 594 observed) and are
        # uniform over the other three arms including their hotspot cells: the
        # platform puts 71 of 594 = 12.0% of ordinary placements on a token-2
        # cell, against a 10.2-11.4% token-2 share of those arms' floor.
        ordinary_pool = [
            cell for cell in sorted(self.map.traversable)
            if region_id(*cell) not in (1, rich_region)
        ]
        ordinary_cells = self._uniform_sample(rng, ordinary_pool, ordinary_count)

        placements = tuple(
            GoldPlacementIntent("outer-ordinary", region_id(*cell), value, cell)
            for value, cell in zip(ordinary_values, ordinary_cells)
        )
        pools = (GoldPoolIntent("outer-rich", rich_region, rich_total, rich_cells),)
        return GoldIntent(
            source="outer", placements=placements, cell_orders=(),
            rich_region=rich_region, rich_degraded=rich_degraded, pools=pools,
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
            "rich_degraded": intent.rich_degraded,
            "placements": [
                {
                    "region": item.region, "source": item.source,
                    "value": item.value,
                    "cell": None if item.cell is None else list(item.cell),
                }
                for item in intent.placements
            ],
            "pools": [
                {
                    "region": item.region, "source": item.source,
                    "total": item.total, "cells": [list(cell) for cell in item.cells],
                }
                for item in intent.pools
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
                "central_attempt_mean": CENTRAL_ATTEMPT_MEAN,
                "central_attempt_population": "all 81 cells of the central 9x9, walls included",
                "central_position_law": "separable centripetal row x column marginals",
                "central_wall_attempt": "dropped, never relocated",
                "central_value_uniform": [1, CENTRAL_VALUE_MAX],
                "central_value_channel": "grid increments of 18 full-vision platform probe games",
                "central_placed_mean_latent_channel_map1": CENTRAL_POISSON_MEAN,
                "opening_rule": "unknown; regular central fit applied",
                "outer_wait_uniform": [8, 16],
                "outer_rich_region_uniform": [2, 5],
                "outer_rich_target": "the rich arm's own token-2 cells only",
                "outer_rich_total_rule": "pooled: the event total is divided over the hotspots free at generation time, never destroyed",
                "outer_ordinary_count_histogram": "direct marginal, mean 4.909",
                "outer_ordinary_target": "uniform over the other three arms' floor, hotspots included",
                "outer_hotspot_empirical_share": OUTER_HOTSPOT_EMPIRICAL_SHARE,
                "outer_hotspot_raw_cell_rate_ratio": OUTER_HOTSPOT_RAW_CELL_RATE_RATIO,
                "outer_hotspot_weight": None,
                "outer_hotspot_weight_retired": OUTER_HOTSPOT_WEIGHT,
                "outer_hotspot_role": "outer-gold coordinate target, never bomb eligibility",
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
    tolerance: float = 0.03,
) -> Mapping[str, Any]:
    """Check the hotspot share, which is now a prediction rather than a fit.

    Before 2026-08-11 this checked a tuned weight against the share it had been
    tuned on, which is circular.  The structural rule has no hotspot weight left,
    so the share is an output: 24 games per official map reproduce 0.5613 against
    the empirical 618/1142 = 0.5412.

    The default tolerance is not a judgement call, it is the truth's own error
    bar.  618/1142 is a proportion from 1142 placements, so its binomial standard
    error is sqrt(0.5412 * 0.4588 / 1142) = 0.01475, and 0.03 is two of those.
    Tightening it would assert a precision the *measurement* does not have, not
    merely one the model does not have.

    The same arithmetic says this statistic cannot settle the open question about
    the ordinary stream.  The rich stream is now known to conserve its total when
    a hotspot is blocked (GENERATION.md 4.3.1); the ordinary stream still destroys
    a blocked value because nothing has been measured about it either way.
    Destroying predicts 0.5613, which is 1.36 standard errors from the truth;
    conserving predicts roughly 0.539 (an estimate, not a measurement), which is
    0.15 standard errors away.  Both sit inside two standard errors, so the
    apparent tenfold difference in deviation carries no discriminating power.
    That is why the ordinary stream is left alone: go and measure it.

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
    "BOMB_PERIOD", "BOMB_PROBABILITY", "CENTRAL_ATTEMPT_MEAN",
    "CENTRAL_COL_WEIGHTS", "CENTRAL_POISSON_MEAN", "CENTRAL_ROW_WEIGHTS",
    "CENTRAL_VALUE_MAX",
    "DEFAULT_MAPS_PATH", "NAMED_MAP_PATHS", "GRID_SIZE", "OUTER_HOTSPOT_EMPIRICAL_SHARE",
    "OUTER_HOTSPOT_RAW_CELL_RATE_RATIO", "OUTER_HOTSPOT_WEIGHT",
    "ROUND_COUNT", "GoldAddition", "GoldIntent", "GoldPlacementIntent",
    "GoldPoolIntent", "MapDefinition", "ORDINARY_CELL_COUNT_HISTOGRAM",
    "RoundEvents", "RoundIntent", "ScenarioGenerator",
    "SpawnState", "UnplacedGold", "hotspot_fit_sanity", "region_id",
]
