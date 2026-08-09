"""Deterministic layered mechanics for the GoldRush 17x17 game.

The engine is intentionally policy-free and random-free.  A runner supplies all
round-start generation, both player ``GameOutput``-like decisions, all seven
three-action NPC decisions, measured player costs, and (optionally) the exact
nine-entry dispatch order.  The engine only validates and applies mechanics.

Typical runner integration::

    engine = GameEngine(GameMap.from_definition(map_definition))
    start = engine.begin_round(
        gold_additions=scenario_events.gold_additions,
        bomb_refresh=scenario_events.bomb_refresh,
    )
    input1 = engine.player_input(1)  # both views refer to the same ``start``
    input2 = engine.player_input(2)
    result = engine.execute_round(
        {1: output1, 2: output2},
        npc_actions,                 # {-1: [a, a, a], ..., -7: [...]}
        player_costs={1: ns1, 2: ns2},
        dispatch_order=optional_exact_order,
    )

``begin_round`` returns one frozen :class:`RoundStart`; ``current_start`` and
both :class:`PlayerInput` objects retain that exact object.  Decisions therefore
cannot observe mutations caused by the other caller.  Full-log rendering and
public filtered rendering are separate helpers: full rendering overlays actors,
whereas filtered grids are pure ground with fog outside the owner's visibility.

Replay integrations can use :meth:`GameEngine.from_trace_start` or
:meth:`GameEngine.inject_start_state` to install an official pre-action state.
Full-log actor markers -2 and -4 are decoded as empty latent ground.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple


GRID_SIZE = 17
PLAYER_IDS = (1, 2)
DEFAULT_NPC_IDS = (-1, -2, -3, -4, -5, -6, -7)
ACTION_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
ACTIONS_PER_PLAYER = 6
ACTIONS_PER_NPC = 3
BASE_VISION_RADIUS = 2
VP_PRICES = (0, 2, 3)
VP_RADII = (2, 3, 4)
FOG = -5
WALL = -1
PLAYER_MARK = -2
BOMB = -3
NPC_MARK = -4

Cell = Tuple[int, int]
Grid = Tuple[Tuple[int, ...], ...]
RegionVector = Tuple[int, int, int, int, int]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int(value: Any, name: str) -> int:
    if not _is_int(value):
        raise TypeError("%s must be an integer" % name)
    return value


def _cell(value: Any, name: str = "cell") -> Cell:
    if isinstance(value, Position):
        result = value.cell
    else:
        try:
            result = (_require_int(value[0], name + " row"), _require_int(value[1], name + " col"))
        except (IndexError, KeyError, TypeError):
            raise TypeError("%s must be a two-integer coordinate" % name) from None
    if not (0 <= result[0] < GRID_SIZE and 0 <= result[1] < GRID_SIZE):
        raise ValueError("%s is outside the 17x17 grid: %r" % (name, result))
    return result


def region_id(row: int, col: int) -> int:
    """Return the fixed windmill region id in 1..5."""
    row, col = _cell((row, col))
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    return 5


def _grid(value: Sequence[Sequence[Any]], *, decode_full_markers: bool = False) -> Grid:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("grid must be a sequence of rows")
    rows = []
    try:
        source_rows = list(value)
    except TypeError:
        raise TypeError("grid must be a sequence of rows") from None
    if len(source_rows) != GRID_SIZE:
        raise ValueError("grid must have exactly 17 rows")
    for row_number, raw_row in enumerate(source_rows):
        if isinstance(raw_row, str):
            raw_values: Sequence[Any] = tuple(raw_row)
        else:
            raw_values = raw_row
        try:
            values = list(raw_values)
        except TypeError:
            raise TypeError("grid row %d is not a sequence" % row_number) from None
        if len(values) != GRID_SIZE:
            raise ValueError("grid row %d must have exactly 17 cells" % row_number)
        row = []
        for raw in values:
            item = _require_int(raw, "grid value")
            if decode_full_markers and item in (PLAYER_MARK, NPC_MARK):
                item = 0
            if item == FOG or item < FOG:
                raise ValueError("latent ground cannot contain fog or unknown negative values")
            if item in (PLAYER_MARK, NPC_MARK):
                raise ValueError("latent ground cannot contain actor markers")
            if item == -5 or item == -4 or item == -2 or item < -5:
                raise ValueError("invalid latent ground value %d" % item)
            row.append(item)
        rows.append(tuple(row))
    return tuple(rows)


def _actions(value: Any, count: int, name: str) -> Tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("%s must be a sequence" % name)
    try:
        result = tuple(value)
    except TypeError:
        raise TypeError("%s must be a sequence" % name) from None
    if len(result) != count:
        raise ValueError("%s must contain exactly %d actions" % (name, count))
    for action in result:
        if not _is_int(action) or not 0 <= action <= 4:
            raise ValueError("%s actions must be integer codes 0..4" % name)
    return result  # type: ignore[return-value]


def _region_vector(value: Any, name: str) -> RegionVector:
    if isinstance(value, Mapping):
        result = tuple(_require_int(value.get(index, value.get(str(index), 0)), name) for index in range(1, 6))
    else:
        result = tuple(value)
        if len(result) == 6:
            result = result[1:]
        if len(result) != 5:
            raise ValueError("%s must have five region values" % name)
        result = tuple(_require_int(item, name) for item in result)
    if any(item < 0 for item in result):
        raise ValueError("%s cannot contain negative values" % name)
    return result  # type: ignore[return-value]


def _zero_regions() -> RegionVector:
    return (0, 0, 0, 0, 0)


def _add_regions(left: RegionVector, right: RegionVector) -> RegionVector:
    return tuple(left[index] + right[index] for index in range(5))  # type: ignore[return-value]


@dataclass(frozen=True, order=True)
class Position:
    """Immutable zero-based board position."""

    row: int
    col: int

    def __post_init__(self) -> None:
        _cell((self.row, self.col), "position")

    @property
    def cell(self) -> Cell:
        return (self.row, self.col)

    @classmethod
    def from_like(cls, value: Any) -> "Position":
        row, col = _cell(value, "position")
        return cls(row, col)


@dataclass(frozen=True)
class GameMap:
    """Static 17x17 terrain; only walls are mechanically significant."""

    name: str
    walls: FrozenSet[Cell]
    size: int = GRID_SIZE

    def __post_init__(self) -> None:
        if self.size != GRID_SIZE:
            raise ValueError("only the official 17x17 map is supported")
        checked = frozenset(_cell(item, "wall") for item in self.walls)
        object.__setattr__(self, "walls", checked)

    @property
    def traversable(self) -> FrozenSet[Cell]:
        return frozenset(
            (row, col)
            for row in range(GRID_SIZE)
            for col in range(GRID_SIZE)
            if (row, col) not in self.walls
        )

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Any]], name: str = "map") -> "GameMap":
        """Load official line-2 style rows (token 1 means wall)."""
        if len(rows) != GRID_SIZE:
            raise ValueError("map must have exactly 17 rows")
        walls = set()
        for row_number, raw_row in enumerate(rows):
            values = list(raw_row)
            if len(values) != GRID_SIZE:
                raise ValueError("map row %d must have exactly 17 cells" % row_number)
            for col, value in enumerate(values):
                token = str(value)
                if token not in ("0", "1", "2"):
                    raise ValueError("map tokens must be 0, 1, or 2")
                if token == "1":
                    walls.add((row_number, col))
        return cls(name=name, walls=frozenset(walls))

    @classmethod
    def from_definition(cls, definition: Any) -> "GameMap":
        """Adapt :class:`sim.scenario.MapDefinition` without importing it."""
        if isinstance(definition, cls):
            return definition
        if hasattr(definition, "walls"):
            return cls(
                name=str(getattr(definition, "name", "map")),
                walls=frozenset(getattr(definition, "walls")),
            )
        return cls.from_rows(definition)


@dataclass(frozen=True)
class UnitState:
    """One player unit in an immutable phase state."""

    owner: int
    index: int
    position: Position
    gold: int = 0
    actions: Tuple[int, ...] = ()
    pickup: int = 0

    def __post_init__(self) -> None:
        if self.owner not in PLAYER_IDS or self.index not in (0, 1):
            raise ValueError("invalid player unit identity")
        if not isinstance(self.position, Position):
            object.__setattr__(self, "position", Position.from_like(self.position))
        _require_int(self.gold, "unit gold")
        _require_int(self.pickup, "unit pickup")
        if self.gold < 0 or self.pickup < 0:
            raise ValueError("unit gold and pickup must be non-negative")
        if self.actions:
            object.__setattr__(self, "actions", _actions(self.actions, len(self.actions), "unit actions"))


@dataclass(frozen=True)
class PlayerState:
    """One player's two units and cumulative public accounting."""

    id: int
    units: Tuple[UnitState, UnitState]
    cost: int = 0
    order: int = 0
    vision_spent: int = 0
    vision_radius: int = BASE_VISION_RADIUS

    def __post_init__(self) -> None:
        if self.id not in PLAYER_IDS:
            raise ValueError("player id must be 1 or 2")
        if len(self.units) != 2:
            raise ValueError("a player must have exactly two units")
        if tuple((unit.owner, unit.index) for unit in self.units) != ((self.id, 0), (self.id, 1)):
            raise ValueError("player units must be ordered indices 0, 1 with matching owner")
        _require_int(self.cost, "player cost")
        _require_int(self.vision_spent, "vision_spent")
        if self.cost < 0 or self.vision_spent < 0:
            raise ValueError("cost and vision_spent must be non-negative")
        if self.order not in (0, 1):
            raise ValueError("player order must be 0 or 1")
        if self.vision_radius not in VP_RADII:
            raise ValueError("vision radius must be 2, 3, or 4")

    @property
    def gold(self) -> int:
        return self.units[0].gold + self.units[1].gold


@dataclass(frozen=True)
class NPCState:
    """One NPC; NPCs have no player-scoring purse."""

    id: int
    position: Position
    actions: Tuple[int, ...] = ()
    pickup: int = 0
    cost: int = 0

    def __post_init__(self) -> None:
        if not _is_int(self.id) or self.id >= 0:
            raise ValueError("NPC id must be a negative integer")
        if not isinstance(self.position, Position):
            object.__setattr__(self, "position", Position.from_like(self.position))
        _require_int(self.pickup, "NPC pickup")
        _require_int(self.cost, "NPC cost")
        if self.pickup < 0 or self.cost < 0:
            raise ValueError("NPC pickup and cost must be non-negative")
        if self.actions:
            object.__setattr__(self, "actions", _actions(self.actions, len(self.actions), "NPC actions"))


@dataclass(frozen=True)
class WorldState:
    """Immutable latent phase state.  ``ground`` never contains actor markers."""

    round: int
    ground: Grid
    players: Tuple[PlayerState, PlayerState]
    npcs: Tuple[NPCState, ...]

    def __post_init__(self) -> None:
        _require_int(self.round, "round")
        object.__setattr__(self, "ground", _grid(self.ground))
        if tuple(player.id for player in self.players) != PLAYER_IDS:
            raise ValueError("players must be ordered P1, P2")
        ids = tuple(npc.id for npc in self.npcs)
        if len(ids) != 7 or len(set(ids)) != 7 or any(item >= 0 for item in ids):
            raise ValueError("state must contain seven distinct negative NPC ids")

    def player(self, player_id: int) -> PlayerState:
        if player_id not in PLAYER_IDS:
            raise KeyError("player id must be 1 or 2")
        return self.players[player_id - 1]

    def npc(self, npc_id: int) -> NPCState:
        for npc in self.npcs:
            if npc.id == npc_id:
                return npc
        raise KeyError("unknown NPC id %r" % npc_id)


@dataclass(frozen=True)
class PlayerDecision:
    """Validated GameOutput-like decision plus optional externally measured cost."""

    actions: Tuple[int, ...]
    k: int
    order: int
    vp: int
    cost: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", _actions(self.actions, ACTIONS_PER_PLAYER, "player actions"))
        for name in ("k", "order", "vp", "cost"):
            _require_int(getattr(self, name), name)
        if not 0 <= self.k <= ACTIONS_PER_PLAYER:
            raise ValueError("k must be in 0..6")
        if self.order not in (0, 1):
            raise ValueError("order must be 0 or 1")
        if self.vp not in (0, 1, 2):
            raise ValueError("vp must be 0, 1, or 2")
        if self.cost < 0:
            raise ValueError("cost must be non-negative")

    @classmethod
    def from_like(cls, value: Any) -> "PlayerDecision":
        """Accept this class, a mapping/object, or Python's length-nine output."""
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                actions=tuple(value["actions"]), k=value["k"], order=value["order"],
                vp=value["vp"], cost=value.get("cost", 0),
            )
        if hasattr(value, "actions") and hasattr(value, "k"):
            return cls(
                actions=tuple(value.actions), k=value.k, order=value.order,
                vp=value.vp, cost=getattr(value, "cost", 0),
            )
        try:
            values = tuple(value)
        except TypeError:
            raise TypeError("player output must be GameOutput-like or a length-nine sequence") from None
        if len(values) != 9:
            raise ValueError("Python player output must contain six actions, k, order, vp")
        return cls(tuple(values[:6]), values[6], values[7], values[8])


@dataclass(frozen=True)
class GoldAddition:
    """One explicit additive round-start gold placement."""

    position: Position
    amount: int
    source: str = "external"

    def __post_init__(self) -> None:
        if not isinstance(self.position, Position):
            object.__setattr__(self, "position", Position.from_like(self.position))
        _require_int(self.amount, "gold addition")
        if self.amount <= 0:
            raise ValueError("gold addition must be positive")

    @classmethod
    def from_like(cls, value: Any) -> "GoldAddition":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            position = value.get("position", value.get("cell"))
            amount = value.get("amount", value.get("value"))
            return cls(Position.from_like(position), amount, str(value.get("source", "external")))
        if hasattr(value, "row") and hasattr(value, "col") and hasattr(value, "value"):
            return cls(Position(value.row, value.col), value.value, str(getattr(value, "source", "external")))
        values = tuple(value)
        if len(values) == 3:
            return cls(Position(values[0], values[1]), values[2])
        if len(values) == 2:
            return cls(Position.from_like(values[0]), values[1])
        raise ValueError("gold addition must be (row,col,amount) or (cell,amount)")


@dataclass(frozen=True)
class EnvironmentUpdate:
    """Auditable result of applying explicit generation and exclusions."""

    accepted_gold: Tuple[GoldAddition, ...]
    rejected_gold: Tuple[GoldAddition, ...]
    bomb_refresh: Optional[FrozenSet[Cell]]
    rejected_bombs: FrozenSet[Cell]
    generated_by_region: RegionVector


@dataclass(frozen=True)
class RegionStat:
    id: int
    enter: int
    leave: int
    gold_generated: int
    gold_collected: int
    gold_remaining: int
    occupants: int


@dataclass(frozen=True)
class Snapshot:
    """Five-region global snapshot sampled at round-start multiples of five."""

    window_begin: int
    window_end: int
    regions: Tuple[RegionStat, ...]

    def __post_init__(self) -> None:
        if tuple(item.id for item in self.regions) != (1, 2, 3, 4, 5):
            raise ValueError("snapshot regions must be ordered 1..5")


@dataclass(frozen=True)
class RoundStart:
    """Common immutable pre-decision state for both player callers."""

    round: int
    state: WorldState
    full_grid: Grid
    snapshot: Optional[Snapshot]
    environment: EnvironmentUpdate


@dataclass(frozen=True)
class PlayerInput:
    """Pure-standard-library equivalent of the public GameInput."""

    round: int
    grid: Grid
    my_units: Tuple[Position, Position]
    my_units_gold: Tuple[int, int]
    gold_opp: int
    visible_enemies: Tuple[Optional[Position], Optional[Position]]
    visible_npcs: Tuple[Tuple[int, Position], ...]
    snapshot: Optional[Snapshot]
    start: RoundStart = field(compare=False, repr=False)

    @property
    def snapshot_valid(self) -> int:
        return int(self.snapshot is not None)

    @property
    def num_visible_npcs(self) -> int:
        return len(self.visible_npcs)


@dataclass(frozen=True)
class MovementEvent:
    actor_id: int
    unit_index: Optional[int]
    requested_action: int
    effective_action: int
    origin: Position
    destination: Position
    moved: bool
    blocked_by: Optional[str] = None


@dataclass(frozen=True)
class PickupEvent:
    actor_id: int
    unit_index: Optional[int]
    position: Position
    before: int
    amount: int
    remaining: int


@dataclass(frozen=True)
class BombEvent:
    actor_id: int
    unit_index: Optional[int]
    position: Position
    penalty: int


@dataclass(frozen=True)
class TrampleEvent:
    round: int
    pos: Position
    unit_owner: int
    npc_count: int
    penalty: int


@dataclass(frozen=True)
class RoundAccounting:
    enter: RegionVector
    leave: RegionVector
    gold_collected: RegionVector
    gold_generated: RegionVector


@dataclass(frozen=True)
class RoundResult:
    """Immutable end state, effective actions, and all mechanical events."""

    round: int
    start: RoundStart
    state: WorldState
    full_grid: Grid
    dispatch_order: Tuple[int, ...]
    movements: Tuple[MovementEvent, ...]
    pickups: Tuple[PickupEvent, ...]
    bombs: Tuple[BombEvent, ...]
    trample_events: Tuple[TrampleEvent, ...]
    burned: int
    accounting: RoundAccounting

    @property
    def end(self) -> WorldState:
        return self.state


@dataclass(frozen=True)
class FinalScore:
    player_id: int
    gross_gold: int
    vision_spent: int
    net_gold: int
    p90_cost: int


def p90_cost(costs: Iterable[int]) -> int:
    """Return the repository's observed discrete P90 (sorted index int(n*.90))."""
    values = sorted(_require_int(value, "cost") for value in costs)
    if any(value < 0 for value in values):
        raise ValueError("costs must be non-negative")
    if not values:
        return 0
    return values[min(len(values) - 1, int(len(values) * 90 / 100))]


def winning_player(scores: Mapping[int, FinalScore]) -> Optional[int]:
    """Choose higher net score, then lower P90; return None on an exact tie."""
    first, second = scores[1], scores[2]
    if first.net_gold != second.net_gold:
        return 1 if first.net_gold > second.net_gold else 2
    if first.p90_cost != second.p90_cost:
        return 1 if first.p90_cost < second.p90_cost else 2
    return None


class GameEngine:
    """Stateful deterministic mechanics engine with a strict two-phase round API."""

    def __init__(self, game_map: Any, *, npc_ids: Sequence[int] = DEFAULT_NPC_IDS) -> None:
        self.map = GameMap.from_definition(game_map)
        ids = tuple(npc_ids)
        if len(ids) != 7 or len(set(ids)) != 7 or any(not _is_int(item) or item >= 0 for item in ids):
            raise ValueError("npc_ids must contain seven distinct negative integers")
        self.npc_ids = ids
        self.round_number = 0
        self._cost_history: Dict[int, list[int]] = {1: [], 2: []}
        self._generated_history: Dict[int, RegionVector] = {}
        self._movement_history: Dict[int, Tuple[RegionVector, RegionVector, RegionVector]] = {}
        self._vision_radii: Dict[int, int] = {1: BASE_VISION_RADIUS, 2: BASE_VISION_RADIUS}
        self._pending_start: Optional[RoundStart] = None
        self._last_result: Optional[RoundResult] = None
        self._state = self._canonical_state()
        self._assert_state(self._state)

    def _canonical_state(self) -> WorldState:
        board = tuple(
            tuple(WALL if (row, col) in self.map.walls else 0 for col in range(GRID_SIZE))
            for row in range(GRID_SIZE)
        )
        players = (
            PlayerState(1, (
                UnitState(1, 0, Position(0, 0)),
                UnitState(1, 1, Position(16, 16)),
            )),
            PlayerState(2, (
                UnitState(2, 0, Position(0, 16)),
                UnitState(2, 1, Position(16, 0)),
            )),
        )
        npcs = tuple(NPCState(npc_id, Position(8, 8)) for npc_id in self.npc_ids)
        return WorldState(-1, board, players, npcs)

    @property
    def state(self) -> WorldState:
        """Latest committed end state (or canonical births before round 0)."""
        return self._state

    @property
    def current_start(self) -> RoundStart:
        """Return the exact frozen object created by :meth:`begin_round`."""
        if self._pending_start is None:
            raise RuntimeError("no round has been started")
        return self._pending_start

    @property
    def last_result(self) -> Optional[RoundResult]:
        return self._last_result

    @property
    def cost_history(self) -> Mapping[int, Tuple[int, ...]]:
        return {player_id: tuple(values) for player_id, values in self._cost_history.items()}

    def _assert_state(self, state: WorldState) -> None:
        if tuple(npc.id for npc in state.npcs) != self.npc_ids:
            raise ValueError("state NPC ids/order must match the engine's configured npc_ids")
        player_cells = []
        for player in state.players:
            for unit in player.units:
                cell = unit.position.cell
                if cell in self.map.walls:
                    raise ValueError("player occupies a wall: %r" % (cell,))
                player_cells.append(cell)
        if len(set(player_cells)) != 4:
            raise ValueError("the four player units may not overlap")
        for npc in state.npcs:
            if npc.position.cell in self.map.walls:
                raise ValueError("NPC occupies a wall: %r" % (npc.position.cell,))
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                value = state.ground[row][col]
                if (row, col) in self.map.walls:
                    if value != WALL:
                        raise ValueError("static wall missing from latent ground")
                elif value == WALL:
                    raise ValueError("latent ground adds a wall absent from GameMap")
                elif value < 0 and value != BOMB:
                    raise ValueError("invalid non-wall latent value %d" % value)
        actor_cells = set(player_cells) | {npc.position.cell for npc in state.npcs}
        bombs = {
            (row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE)
            if state.ground[row][col] == BOMB
        }
        if bombs & actor_cells:
            raise ValueError("bombs may not be refreshed under actors")

    @staticmethod
    def _actor_cells(state: WorldState) -> FrozenSet[Cell]:
        return frozenset(
            [unit.position.cell for player in state.players for unit in player.units]
            + [npc.position.cell for npc in state.npcs]
        )

    def begin_round(
        self,
        gold_additions: Iterable[Any] = (),
        bomb_refresh: Optional[Iterable[Any]] = None,
        *,
        gold_exclusions: Iterable[Any] = (),
        bomb_exclusions: Iterable[Any] = (),
    ) -> RoundStart:
        """Apply pre-decision environment input and freeze the common start.

        ``bomb_refresh=None`` preserves surviving bombs.  Any iterable, including
        an empty one, is a complete replacement set.  Explicit exclusion cells
        are filtered and reported.  Malformed/out-of-bounds placements raise;
        walls and surviving bombs cannot receive gold.  On a complete refresh,
        old bombs are removed first.  New bombs are filtered from walls, actors,
        positive gold, and caller exclusions.
        """
        if self._pending_start is not None:
            raise RuntimeError("finish the pending round before beginning another")
        additions = tuple(GoldAddition.from_like(item) for item in gold_additions)
        excluded_gold = frozenset(_cell(item, "gold exclusion") for item in gold_exclusions)
        excluded_bombs = frozenset(_cell(item, "bomb exclusion") for item in bomb_exclusions)
        proposed_refresh = None if bomb_refresh is None else frozenset(
            _cell(item, "bomb refresh cell") for item in bomb_refresh
        )
        board = [list(row) for row in self._state.ground]
        # A supplied set is a complete wave, so old bombs cease to exist before
        # this round's explicit additions are installed.  This permits gold on a
        # cell whose old bomb was discarded while still forbidding gold+bomb in
        # the resulting start state.
        if proposed_refresh is not None:
            for row in range(GRID_SIZE):
                for col in range(GRID_SIZE):
                    if board[row][col] == BOMB:
                        board[row][col] = 0
        accepted = []
        rejected_gold = []
        generated = [0, 0, 0, 0, 0]
        for addition in additions:
            cell = addition.position.cell
            if cell in excluded_gold:
                rejected_gold.append(addition)
                continue
            value = board[cell[0]][cell[1]]
            if value == WALL:
                raise ValueError("gold addition targets a wall: %r" % (cell,))
            if value == BOMB:
                raise ValueError("gold addition targets a surviving bomb: %r" % (cell,))
            board[cell[0]][cell[1]] += addition.amount
            generated[region_id(*cell) - 1] += addition.amount
            accepted.append(addition)

        refresh: Optional[FrozenSet[Cell]] = None
        rejected_bomb_set: FrozenSet[Cell] = frozenset()
        if proposed_refresh is not None:
            proposed = proposed_refresh
            actors = self._actor_cells(self._state)
            positive = {
                (row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE)
                if board[row][col] > 0
            }
            blocked = self.map.walls | actors | positive | excluded_bombs
            rejected_bomb_set = frozenset(proposed & blocked)
            refresh = frozenset(proposed - blocked)
            for row, col in refresh:
                board[row][col] = BOMB

        environment = EnvironmentUpdate(
            accepted_gold=tuple(accepted), rejected_gold=tuple(rejected_gold),
            bomb_refresh=refresh, rejected_bombs=rejected_bomb_set,
            generated_by_region=tuple(generated),  # type: ignore[arg-type]
        )
        self._generated_history[self.round_number] = environment.generated_by_region
        players = tuple(
            PlayerState(
                player.id, player.units, player.cost, player.order, player.vision_spent,
                self._vision_radii[player.id],
            )
            for player in self._state.players
        )
        state = WorldState(self.round_number, tuple(tuple(row) for row in board), players, self._state.npcs)
        self._assert_state(state)
        snapshot = self._make_snapshot(state)
        start = RoundStart(
            self.round_number, state, self.render_full(state), snapshot, environment,
        )
        self._pending_start = start
        return start

    start_round = begin_round

    def _make_snapshot(self, state: WorldState) -> Optional[Snapshot]:
        round_number = state.round
        if round_number <= 0 or round_number % 5:
            return None
        begin, end = round_number - 5, round_number - 1
        enter = _zero_regions()
        leave = _zero_regions()
        collected = _zero_regions()
        for index in range(begin, end + 1):
            values = self._movement_history.get(index)
            if values is not None:
                enter = _add_regions(enter, values[0])
                leave = _add_regions(leave, values[1])
                collected = _add_regions(collected, values[2])
        generated = _zero_regions()
        generation_begin = 0 if begin == 0 else begin + 1
        for index in range(generation_begin, round_number + 1):
            generated = _add_regions(generated, self._generated_history.get(index, _zero_regions()))
        remaining = [0, 0, 0, 0, 0]
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if state.ground[row][col] > 0:
                    remaining[region_id(row, col) - 1] += state.ground[row][col]
        occupants = [0, 0, 0, 0, 0]
        for player in state.players:
            for unit in player.units:
                occupants[region_id(*unit.position.cell) - 1] += 1
        for npc in state.npcs:
            occupants[region_id(*npc.position.cell) - 1] += 1
        return Snapshot(
            begin, end,
            tuple(
                RegionStat(
                    index + 1, enter[index], leave[index], generated[index],
                    collected[index], remaining[index], occupants[index],
                )
                for index in range(5)
            ),
        )

    @staticmethod
    def visible_cells(centers: Iterable[Any], radius: int) -> FrozenSet[Cell]:
        """Chebyshev-square union clipped to the board."""
        _require_int(radius, "radius")
        if radius < 0:
            raise ValueError("radius must be non-negative")
        result = set()
        for value in centers:
            row, col = _cell(value, "visibility center")
            for visible_row in range(max(0, row - radius), min(GRID_SIZE, row + radius + 1)):
                for visible_col in range(max(0, col - radius), min(GRID_SIZE, col + radius + 1)):
                    result.add((visible_row, visible_col))
        return frozenset(result)

    @staticmethod
    def render_ground(state: WorldState) -> Grid:
        """Return the immutable pure latent ground grid."""
        return state.ground

    @classmethod
    def render_full(cls, state: WorldState) -> Grid:
        """Render nonzero ground > NPC(-4) > player(-2) > empty."""
        board = [list(row) for row in state.ground]
        player_cells = {unit.position.cell for player in state.players for unit in player.units}
        npc_cells = {npc.position.cell for npc in state.npcs}
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if board[row][col] != 0:
                    continue
                if (row, col) in npc_cells:
                    board[row][col] = NPC_MARK
                elif (row, col) in player_cells:
                    board[row][col] = PLAYER_MARK
        return tuple(tuple(row) for row in board)

    @classmethod
    def render_filtered_ground(
        cls, state: WorldState, player_id: int, radius: Optional[int] = None
    ) -> Grid:
        """Render public pure ground with -5 fog and no actor overlays."""
        player = state.player(player_id)
        selected_radius = player.vision_radius if radius is None else radius
        visible = cls.visible_cells((unit.position for unit in player.units), selected_radius)
        return tuple(
            tuple(state.ground[row][col] if (row, col) in visible else FOG for col in range(GRID_SIZE))
            for row in range(GRID_SIZE)
        )

    def player_input(self, player_id: int, start: Optional[RoundStart] = None) -> PlayerInput:
        """Build a perspective view while retaining the common RoundStart object."""
        if player_id not in PLAYER_IDS:
            raise KeyError("player id must be 1 or 2")
        common = self.current_start if start is None else start
        if self._pending_start is not common:
            raise ValueError("player input must be built from the engine's current start")
        state = common.state
        owner = state.player(player_id)
        opponent = state.player(3 - player_id)
        visible = self.visible_cells((unit.position for unit in owner.units), owner.vision_radius)
        enemies = tuple(
            unit.position if unit.position.cell in visible else None for unit in opponent.units
        )
        visible_npcs = tuple(
            (npc.id, npc.position) for npc in state.npcs if npc.position.cell in visible
        )
        return PlayerInput(
            round=common.round,
            grid=self.render_filtered_ground(state, player_id),
            my_units=(owner.units[0].position, owner.units[1].position),
            my_units_gold=(owner.units[0].gold, owner.units[1].gold),
            gold_opp=opponent.gold,
            visible_enemies=enemies,  # type: ignore[arg-type]
            visible_npcs=visible_npcs,
            snapshot=common.snapshot,
            start=common,
        )

    @staticmethod
    def _normalize_npc_decisions(value: Mapping[int, Any], ids: Tuple[int, ...]) -> Dict[int, Tuple[int, ...]]:
        if not isinstance(value, Mapping):
            raise TypeError("NPC decisions must be a dictionary keyed by NPC id")
        if set(value) != set(ids):
            missing = set(ids) - set(value)
            extra = set(value) - set(ids)
            raise ValueError("NPC decision ids mismatch; missing=%r extra=%r" % (sorted(missing), sorted(extra)))
        result = {}
        for npc_id in ids:
            raw = value[npc_id]
            if isinstance(raw, Mapping):
                raw = raw.get("actions")
            elif hasattr(raw, "actions"):
                raw = raw.actions
            result[npc_id] = _actions(raw, ACTIONS_PER_NPC, "NPC %d actions" % npc_id)
        return result

    def _dispatch(
        self,
        costs: Mapping[int, int],
        dispatch_order: Optional[Sequence[int]],
        npc_order: Optional[Sequence[int]],
    ) -> Tuple[int, ...]:
        faster = 1 if costs[1] <= costs[2] else 2
        slower = 3 - faster
        order = self.npc_ids if npc_order is None else tuple(npc_order)
        if len(order) != 7 or set(order) != set(self.npc_ids):
            raise ValueError("npc_order must be a permutation of all seven NPC ids")
        expected = (faster,) + tuple(order) + (slower,)
        if dispatch_order is None:
            return expected
        supplied = tuple(dispatch_order)
        if len(supplied) != 9:
            raise ValueError("dispatch_order must contain nine actor ids")
        if supplied[0] != faster or supplied[-1] != slower:
            raise ValueError("dispatch player endpoints disagree with measured cost order")
        if set(supplied[1:8]) != set(self.npc_ids) or len(set(supplied[1:8])) != 7:
            raise ValueError("dispatch middle must be a permutation of all seven NPC ids")
        return supplied

    def execute_round(
        self,
        player_decisions: Mapping[int, Any],
        npc_decisions: Any,
        *,
        player_costs: Optional[Mapping[int, int]] = None,
        dispatch_order: Optional[Sequence[int]] = None,
        npc_order: Optional[Sequence[int]] = None,
    ) -> RoundResult:
        """Validate decisions and execute players/NPCs in exact dispatch order.

        ``npc_decisions`` may be either the replay mapping ``{id: actions}`` or
        a callback ``(npc_id, current_ground, current_position) -> actions``.
        The callback form is evaluated at each NPC's actual dispatch turn, after
        the faster player and earlier NPCs have changed the board.  This matches
        the state used to calibrate the fitted NPC policy while retaining exact
        recorded-action replay support.
        """
        start = self.current_start
        if not isinstance(player_decisions, Mapping) or set(player_decisions) != {1, 2}:
            raise ValueError("player_decisions must contain exactly ids 1 and 2")
        decisions = {item: PlayerDecision.from_like(player_decisions[item]) for item in PLAYER_IDS}
        if player_costs is None:
            costs = {item: decisions[item].cost for item in PLAYER_IDS}
        else:
            if set(player_costs) != {1, 2}:
                raise ValueError("player_costs must contain exactly ids 1 and 2")
            costs = {item: _require_int(player_costs[item], "player cost") for item in PLAYER_IDS}
            if any(value < 0 for value in costs.values()):
                raise ValueError("player costs must be non-negative")
        npc_policy = npc_decisions if callable(npc_decisions) else None
        npc_actions = (
            {} if npc_policy is not None
            else self._normalize_npc_decisions(npc_decisions, self.npc_ids)
        )
        dispatch = self._dispatch(costs, dispatch_order, npc_order)

        board = [list(row) for row in start.state.ground]
        positions: Dict[Tuple[str, int, int], Cell] = {}
        held: Dict[Tuple[int, int], int] = {}
        for player in start.state.players:
            for unit in player.units:
                positions[("p", player.id, unit.index)] = unit.position.cell
                held[(player.id, unit.index)] = unit.gold
        for npc in start.state.npcs:
            positions[("n", npc.id, 0)] = npc.position.cell
        player_effective: Dict[Tuple[int, int], list[int]] = {
            (player_id, unit): [] for player_id in PLAYER_IDS for unit in (0, 1)
        }
        npc_effective: Dict[int, list[int]] = {npc_id: [] for npc_id in self.npc_ids}
        player_pickups: Dict[Tuple[int, int], int] = {
            (player_id, unit): 0 for player_id in PLAYER_IDS for unit in (0, 1)
        }
        npc_pickups: Dict[int, int] = {npc_id: 0 for npc_id in self.npc_ids}
        movements: list[MovementEvent] = []
        pickups: list[PickupEvent] = []
        bombs: list[BombEvent] = []
        tramples: list[TrampleEvent] = []
        burned = 0

        def player_cells_except(owner: int, unit_index: int) -> set[Cell]:
            return {
                value for key, value in positions.items()
                if key[0] == "p" and not (key[1] == owner and key[2] == unit_index)
            }

        def execute_action(actor_id: int, unit_index: Optional[int], requested: int) -> None:
            nonlocal burned
            is_player = actor_id > 0
            key = ("p", actor_id, unit_index if unit_index is not None else 0) if is_player else ("n", actor_id, 0)
            origin = positions[key]
            effective = requested
            destination = origin
            blocked_by: Optional[str] = None
            moved = False
            if requested != 4:
                delta = ACTION_DELTAS[requested]
                candidate = (origin[0] + delta[0], origin[1] + delta[1])
                if not (0 <= candidate[0] < GRID_SIZE and 0 <= candidate[1] < GRID_SIZE):
                    effective, blocked_by = 4, "bounds"
                elif board[candidate[0]][candidate[1]] == WALL:
                    effective, blocked_by = 4, "wall"
                elif is_player and candidate in player_cells_except(actor_id, int(unit_index)):
                    effective, blocked_by = 4, "player"
                else:
                    destination = candidate
                    positions[key] = candidate
                    moved = True
            if is_player:
                player_effective[(actor_id, int(unit_index))].append(effective)
            else:
                npc_effective[actor_id].append(effective)
            movements.append(MovementEvent(
                actor_id, unit_index, requested, effective,
                Position(*origin), Position(*destination), moved, blocked_by,
            ))
            if not moved:
                return

            row, col = destination
            value = board[row][col]
            if value > 0:
                amount = (65 * value + 99) // 100
                board[row][col] = value - amount
                if is_player:
                    purse_key = (actor_id, int(unit_index))
                    held[purse_key] += amount
                    player_pickups[purse_key] += amount
                else:
                    npc_pickups[actor_id] += amount
                pickups.append(PickupEvent(
                    actor_id, unit_index, Position(row, col), value, amount, value - amount,
                ))

            if board[row][col] == BOMB:
                board[row][col] = 0
                penalty = 0
                if is_player:
                    purse_key = (actor_id, int(unit_index))
                    penalty = (held[purse_key] + 9) // 10
                    held[purse_key] -= penalty
                    burned += penalty
                bombs.append(BombEvent(actor_id, unit_index, Position(row, col), penalty))

            if is_player:
                npc_count = sum(
                    value == destination for actor_key, value in positions.items() if actor_key[0] == "n"
                )
                if npc_count >= 3:
                    purse_key = (actor_id, int(unit_index))
                    penalty = (held[purse_key] + 19) // 20
                    held[purse_key] -= penalty
                    burned += penalty
                    tramples.append(TrampleEvent(
                        start.round + 1, Position(row, col), actor_id, npc_count, penalty,
                    ))

        for actor_id in dispatch:
            if actor_id > 0:
                decision = decisions[actor_id]
                assigned = {
                    0: decision.actions[:decision.k],
                    1: decision.actions[decision.k:],
                }
                for unit_index in (decision.order, 1 - decision.order):
                    for action in assigned[unit_index]:
                        execute_action(actor_id, unit_index, action)
            else:
                actions = npc_actions.get(actor_id)
                if npc_policy is not None:
                    current_ground = tuple(tuple(row) for row in board)
                    current_position = Position(*positions[("n", actor_id, 0)])
                    raw_actions = npc_policy(actor_id, current_ground, current_position)
                    actions = _actions(
                        raw_actions, ACTIONS_PER_NPC,
                        "NPC %d callback actions" % actor_id,
                    )
                for action in actions:
                    execute_action(actor_id, None, action)

        players = []
        for player_id in PLAYER_IDS:
            decision = decisions[player_id]
            units = tuple(
                UnitState(
                    player_id, unit_index,
                    Position(*positions[("p", player_id, unit_index)]),
                    held[(player_id, unit_index)],
                    tuple(player_effective[(player_id, unit_index)]),
                    player_pickups[(player_id, unit_index)],
                )
                for unit_index in (0, 1)
            )
            prior = start.state.player(player_id)
            players.append(PlayerState(
                player_id, units, costs[player_id], decision.order,
                prior.vision_spent + VP_PRICES[decision.vp], prior.vision_radius,
            ))
        npcs = tuple(
            NPCState(
                npc.id, Position(*positions[("n", npc.id, 0)]),
                tuple(npc_effective[npc.id]), npc_pickups[npc.id], 0,
            )
            for npc in start.state.npcs
        )
        end_state = WorldState(start.round, tuple(tuple(row) for row in board), tuple(players), npcs)  # type: ignore[arg-type]
        self._assert_state(end_state)

        enter = [0, 0, 0, 0, 0]
        leave = [0, 0, 0, 0, 0]
        start_positions = {
            ("p", player.id, unit.index): unit.position.cell
            for player in start.state.players for unit in player.units
        }
        start_positions.update({("n", npc.id, 0): npc.position.cell for npc in start.state.npcs})
        for key, before in start_positions.items():
            after = positions[key]
            old_region, new_region = region_id(*before), region_id(*after)
            if old_region != new_region:
                leave[old_region - 1] += 1
                enter[new_region - 1] += 1
        collected = [0, 0, 0, 0, 0]
        for event in pickups:
            collected[region_id(*event.position.cell) - 1] += event.amount
        accounting = RoundAccounting(
            tuple(enter), tuple(leave), tuple(collected), start.environment.generated_by_region,  # type: ignore[arg-type]
        )

        start_ground = sum(max(value, 0) for row in start.state.ground for value in row)
        end_ground = sum(max(value, 0) for row in end_state.ground for value in row)
        if start_ground - end_ground != sum(event.amount for event in pickups):
            raise AssertionError("ground-gold conservation failed")
        start_held = sum(player.gold for player in start.state.players)
        end_held = sum(player.gold for player in end_state.players)
        player_collected = sum(
            event.amount for event in pickups if event.actor_id in PLAYER_IDS
        )
        if start_held + player_collected - burned != end_held:
            raise AssertionError("held-gold conservation failed")

        result = RoundResult(
            start.round, start, end_state, self.render_full(end_state), dispatch,
            tuple(movements), tuple(pickups), tuple(bombs), tuple(tramples), burned,
            accounting,
        )
        self._movement_history[start.round] = (accounting.enter, accounting.leave, accounting.gold_collected)
        for player_id in PLAYER_IDS:
            self._cost_history[player_id].append(costs[player_id])
            self._vision_radii[player_id] = VP_RADII[decisions[player_id].vp]
        self._state = end_state
        self._last_result = result
        self._pending_start = None
        self.round_number += 1
        return result

    finish_round = execute_round

    def final_scores(self) -> Mapping[int, FinalScore]:
        """Return gross, cumulative VP expense, net score, and P90 for both players."""
        return {
            player.id: FinalScore(
                player.id, player.gold, player.vision_spent,
                player.gold - player.vision_spent,
                p90_cost(self._cost_history[player.id]),
            )
            for player in self._state.players
        }

    def winner(self) -> Optional[int]:
        return winning_player(self.final_scores())

    def reset_explicit_state(
        self,
        round_number: int,
        ground: Sequence[Sequence[Any]],
        players: Sequence[PlayerState],
        npcs: Sequence[NPCState],
        *,
        vision_radii: Optional[Mapping[int, int]] = None,
        cost_history: Optional[Mapping[int, Iterable[int]]] = None,
        generated_history: Optional[Mapping[int, Any]] = None,
        movement_history: Optional[Mapping[int, Sequence[Any]]] = None,
        decode_full_markers: bool = True,
    ) -> WorldState:
        """Reset before ``round_number`` for deterministic replay/state injection.

        The next operation is normally :meth:`begin_round`.  To inject an
        already-generated official ``start`` phase, use :meth:`inject_start_state`.
        Optional history maps make future snapshots exact after a mid-game reset.
        """
        _require_int(round_number, "round_number")
        if round_number < 0:
            raise ValueError("round_number must be non-negative")
        radii = {1: BASE_VISION_RADIUS, 2: BASE_VISION_RADIUS}
        if vision_radii is not None:
            if set(vision_radii) != {1, 2}:
                raise ValueError("vision_radii must contain players 1 and 2")
            radii = {item: _require_int(vision_radii[item], "vision radius") for item in PLAYER_IDS}
            if any(value not in VP_RADII for value in radii.values()):
                raise ValueError("vision radius must be 2, 3, or 4")
        normalized_players = tuple(
            PlayerState(
                player.id, player.units, player.cost, player.order,
                player.vision_spent, radii[player.id],
            )
            for player in players
        )
        state = WorldState(
            round_number - 1,
            _grid(ground, decode_full_markers=decode_full_markers),
            normalized_players,  # type: ignore[arg-type]
            tuple(npcs),
        )
        self._assert_state(state)
        self.round_number = round_number
        self._state = state
        self._vision_radii = radii
        self._pending_start = None
        self._last_result = None
        self._cost_history = {1: [], 2: []}
        if cost_history is not None:
            if set(cost_history) != {1, 2}:
                raise ValueError("cost_history must contain players 1 and 2")
            self._cost_history = {
                item: [_require_int(value, "cost") for value in cost_history[item]]
                for item in PLAYER_IDS
            }
            if any(value < 0 for values in self._cost_history.values() for value in values):
                raise ValueError("cost history cannot be negative")
        self._generated_history = {}
        if generated_history is not None:
            self._generated_history = {
                _require_int(index, "history round"): _region_vector(values, "generated history")
                for index, values in generated_history.items()
            }
        self._movement_history = {}
        if movement_history is not None:
            for index, values in movement_history.items():
                if len(values) != 3:
                    raise ValueError("movement history values must be (enter,leave,collected)")
                self._movement_history[_require_int(index, "history round")] = (
                    _region_vector(values[0], "enter history"),
                    _region_vector(values[1], "leave history"),
                    _region_vector(values[2], "collected history"),
                )
        return state

    def inject_start_state(
        self,
        round_number: int,
        ground: Sequence[Sequence[Any]],
        players: Sequence[PlayerState],
        npcs: Sequence[NPCState],
        *,
        vision_radii: Optional[Mapping[int, int]] = None,
        snapshot: Optional[Snapshot] = None,
        generated_this_round: Any = (0, 0, 0, 0, 0),
        decode_full_markers: bool = True,
    ) -> RoundStart:
        """Install an already-generated official pre-action state as current start."""
        self.reset_explicit_state(
            round_number, ground, players, npcs,
            vision_radii=vision_radii, decode_full_markers=decode_full_markers,
        )
        generated = _region_vector(generated_this_round, "generated_this_round")
        self._generated_history[round_number] = generated
        state = WorldState(round_number, self._state.ground, self._state.players, self._state.npcs)
        environment = EnvironmentUpdate((), (), None, frozenset(), generated)
        if snapshot is None:
            snapshot = self._make_snapshot(state)
        start = RoundStart(round_number, state, self.render_full(state), snapshot, environment)
        self._pending_start = start
        return start

    @classmethod
    def from_trace_start(
        cls,
        game_map: Any,
        round_number: int,
        phase: Mapping[str, Any],
        *,
        vision_radii: Optional[Mapping[int, int]] = None,
        snapshot: Optional[Snapshot] = None,
    ) -> "GameEngine":
        """Construct from an official full-log ``start`` phase dictionary."""
        raw_npcs = phase.get("npcs", ())
        npc_ids = tuple(item["id"] for item in raw_npcs)
        engine = cls(game_map, npc_ids=npc_ids)
        players = []
        for raw_player in phase["players"]:
            player_id = raw_player["id"]
            units = tuple(
                UnitState(
                    player_id, index, Position.from_like(raw_unit["position"]),
                    raw_unit.get("gold", 0), tuple(raw_unit.get("actions") or ()),
                    raw_unit.get("pickup", 0),
                )
                for index, raw_unit in enumerate(raw_player["units"])
            )
            players.append(PlayerState(
                player_id, units, raw_player.get("cost", 0), raw_player.get("order", 0),
                raw_player.get("vision_spent", 0),
                (vision_radii or {}).get(player_id, BASE_VISION_RADIUS),
            ))
        players.sort(key=lambda item: item.id)
        npcs = tuple(
            NPCState(
                raw["id"], Position.from_like(raw["position"]),
                tuple(raw.get("actions") or ()), raw.get("pickup", 0), raw.get("cost", 0),
            )
            for raw in raw_npcs
        )
        engine.inject_start_state(
            round_number, phase["grid"], players, npcs,
            vision_radii=vision_radii, snapshot=snapshot, decode_full_markers=True,
        )
        return engine


def _empty_map() -> GameMap:
    return GameMap("smoke", frozenset())


def _stays() -> Dict[int, Tuple[int, int, int]]:
    return {npc_id: (4, 4, 4) for npc_id in DEFAULT_NPC_IDS}


def smoke_check() -> Mapping[str, bool]:
    """Run focused deterministic checks for the contract's ordering edge cases."""
    checks: Dict[str, bool] = {}

    # Blocked attempt is effective 4 and the following step still executes.
    engine = GameEngine(_empty_map())
    engine.begin_round()
    result = engine.execute_round(
        {1: PlayerDecision((2, 1, 4, 4, 4, 4), 2, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    checks["blocked_step_continuation"] = (
        result.state.player(1).units[0].position.cell == (1, 0)
        and result.state.player(1).units[0].actions == (4, 1)
    )

    # A whole-unit dispatch vacates a cell that the second unit may then enter.
    engine = GameEngine(_empty_map())
    base_players = (
        PlayerState(1, (UnitState(1, 0, Position(1, 1)), UnitState(1, 1, Position(1, 2)))),
        PlayerState(2, (UnitState(2, 0, Position(0, 16)), UnitState(2, 1, Position(16, 0)))),
    )
    base_npcs = tuple(NPCState(item, Position(8, 8)) for item in DEFAULT_NPC_IDS)
    empty = tuple(tuple(0 for _ in range(GRID_SIZE)) for _ in range(GRID_SIZE))
    engine.inject_start_state(0, empty, base_players, base_npcs, decode_full_markers=False)
    result = engine.execute_round(
        {1: PlayerDecision((1, 2, 4, 4, 4, 4), 1, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    checks["vacated_cell"] = result.state.player(1).units[1].position.cell == (1, 1)

    # Re-entry repeats ceil(65%) pickup on the residual.
    pickup_players = (
        PlayerState(1, (UnitState(1, 0, Position(1, 1)), UnitState(1, 1, Position(16, 16)))),
        base_players[1],
    )
    engine = GameEngine(_empty_map())
    engine.reset_explicit_state(0, empty, pickup_players, base_npcs, decode_full_markers=False)
    engine.begin_round([((1, 2), 10)])
    result = engine.execute_round(
        {1: PlayerDecision((3, 2, 3, 4, 4, 4), 3, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    checks["repeated_pickup"] = (
        result.state.player(1).units[0].pickup == 9 and result.state.ground[1][2] == 1
    )

    # Player bomb entry removes the bomb and burns ceil(10%).
    engine = GameEngine(_empty_map())
    rich_players = (
        PlayerState(1, (UnitState(1, 0, Position(1, 1), 11), UnitState(1, 1, Position(16, 16)))),
        base_players[1],
    )
    engine.reset_explicit_state(0, empty, rich_players, base_npcs, decode_full_markers=False)
    engine.begin_round(bomb_refresh={(1, 2)})
    result = engine.execute_round(
        {1: PlayerDecision((3, 4, 4, 4, 4, 4), 1, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    checks["bomb_removal"] = result.state.ground[1][2] == 0 and result.burned == 2

    # Pickup occurs before the 5% trample computation.
    crowded_npcs = tuple(
        NPCState(item, Position(1, 2) if index < 3 else Position(8, 8))
        for index, item in enumerate(DEFAULT_NPC_IDS)
    )
    engine = GameEngine(_empty_map())
    engine.reset_explicit_state(0, empty, pickup_players, crowded_npcs, decode_full_markers=False)
    engine.begin_round([((1, 2), 10)])
    result = engine.execute_round(
        {1: PlayerDecision((3, 4, 4, 4, 4, 4), 1, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    checks["pickup_before_trample"] = (
        result.state.player(1).units[0].gold == 6
        and result.trample_events[0].penalty == 1
    )

    # NPC entry consumes a bomb without an NPC purse requirement.
    one_near = tuple(
        NPCState(item, Position(2, 1) if index == 0 else Position(8, 8))
        for index, item in enumerate(DEFAULT_NPC_IDS)
    )
    engine = GameEngine(_empty_map())
    engine.reset_explicit_state(0, empty, base_players, one_near, decode_full_markers=False)
    engine.begin_round(bomb_refresh={(2, 2)})
    npc_moves = _stays()
    npc_moves[-1] = (3, 4, 4)
    result = engine.execute_round(
        {1: PlayerDecision((4,) * 6, 6, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        npc_moves, player_costs={1: 0, 2: 1},
    )
    checks["npc_bomb_consumption"] = result.state.ground[2][2] == 0 and result.bombs[0].penalty == 0

    # A VP purchase is charged now but changes visibility only next round.
    engine = GameEngine(_empty_map())
    start = engine.begin_round()
    before = start.state.player(1).vision_radius
    engine.execute_round(
        {1: PlayerDecision((4,) * 6, 6, 0, 2), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
        _stays(), player_costs={1: 0, 2: 1},
    )
    next_start = engine.begin_round()
    checks["vp_next_round"] = (
        before == 2 and next_start.state.player(1).vision_radius == 4
        and next_start.state.player(1).vision_spent == 3
    )

    # r=5 labels movement 0..4 but includes generation starts 0..5.
    engine = GameEngine(_empty_map())
    sampled: Optional[Snapshot] = None
    for round_number in range(6):
        start = engine.begin_round([((8, 8), 1)])
        if round_number == 5:
            sampled = start.snapshot
        engine.execute_round(
            {1: PlayerDecision((4,) * 6, 6, 0, 0), 2: PlayerDecision((4,) * 6, 6, 0, 0)},
            _stays(), player_costs={1: 0, 2: 1},
        )
    center = sampled.regions[0] if sampled is not None else None
    checks["snapshot_timing"] = bool(
        sampled is not None and sampled.window_begin == 0 and sampled.window_end == 4
        and center is not None and center.gold_generated == 6
        and center.gold_remaining == 6 and center.occupants == 7
    )

    if not all(checks.values()):
        raise AssertionError("engine smoke check failed: %r" % checks)
    return checks


__all__ = [
    "ACTIONS_PER_NPC", "ACTIONS_PER_PLAYER", "ACTION_DELTAS", "BASE_VISION_RADIUS",
    "BOMB", "BombEvent", "Cell", "DEFAULT_NPC_IDS", "EnvironmentUpdate", "FOG",
    "FinalScore", "GRID_SIZE", "GameEngine", "GameMap", "GoldAddition", "Grid",
    "MovementEvent", "NPCState", "NPC_MARK", "PLAYER_IDS", "PLAYER_MARK",
    "PickupEvent", "PlayerDecision", "PlayerInput", "PlayerState", "Position",
    "RegionStat", "RoundAccounting", "RoundResult", "RoundStart", "Snapshot",
    "TrampleEvent", "UnitState", "VP_PRICES", "VP_RADII", "WALL", "WorldState",
    "p90_cost", "region_id", "smoke_check", "winning_player",
]


if __name__ == "__main__":
    print(smoke_check())
