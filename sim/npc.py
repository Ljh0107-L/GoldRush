"""Deterministic, fitted approximation of the private GoldRush NPC policy.

Public API
==========

``NPCModel(rng=None, seed=0)``
    Build a model.  ``rng`` may be any object implementing ``choice(sequence)``
    (for example ``random.Random``).  If it is omitted, the model owns a
    ``random.Random(seed)`` instance.  No module-global RNG is used.

``NPCModel.actions(grid, position, npc_id=None) -> tuple[int, int, int]``
    Return exactly three actions, using the official action codes
    0=up, 1=down, 2=left, 3=right, 4=stay.  ``grid`` is a rectangular sequence
    of rows; -1 is an obstacle and positive values are gold.  Other values,
    including full-log occupancy markers -2/-4 and bombs -3, are traversable.
    ``position`` may be a ``(row, col)`` sequence or an object with ``row`` and
    ``col`` attributes.  ``npc_id`` is accepted for engine integration but the
    fitted approximation does not use identity.  Returned moves never cross a
    boundary or obstacle; a trapped or malformed state returns three stays.

``NPCModel.__call__`` is an alias for ``actions``.  ``choose_actions`` is a
convenience one-shot wrapper.

Model
=====

The official policy is private and was not identifiable exactly from g0-g2.
This approximation is therefore explicitly *fitted*.  At each of the three
slots it selects reachable gold maximizing ``gold / distance**4``, takes a
random shortest-path step, applies the public 65% pickup rule to its private
planning copy, and replans.  With no reachable gold it samples a legal
cardinal move uniformly.  RNG is used only for exact ties and fallback roaming.
Calibration and limitations are recorded in ``sim/reports/npc.json``.
"""

from __future__ import annotations

from collections import deque
import random
from typing import Any, Optional, Sequence, Tuple

UP, DOWN, LEFT, RIGHT, STAY = range(5)
ACTION_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
ACTIONS_PER_TURN = 3
DISTANCE_POWER = 4
OBSTACLE = -1

Position = Tuple[int, int]
Actions = Tuple[int, int, int]


def _position(value: Any) -> Optional[Position]:
    """Convert supported position representations without raising."""
    try:
        if hasattr(value, "row") and hasattr(value, "col"):
            return int(value.row), int(value.col)
        return int(value[0]), int(value[1])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _board(grid: Sequence[Sequence[int]]) -> Optional[list[list[int]]]:
    """Return a mutable rectangular integer board, or None when malformed."""
    try:
        rows = [list(map(int, row)) for row in grid]
    except (TypeError, ValueError):
        return None
    if not rows or not rows[0]:
        return None
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        return None
    return rows


def _inside(board: Sequence[Sequence[int]], pos: Position) -> bool:
    return 0 <= pos[0] < len(board) and 0 <= pos[1] < len(board[0])


def _step(pos: Position, action: int) -> Position:
    dr, dc = ACTION_DELTAS[action]
    return pos[0] + dr, pos[1] + dc


def legal_actions(
    grid: Sequence[Sequence[int]],
    position: Any,
    include_stay: bool = True,
) -> Tuple[int, ...]:
    """Return legal action codes in stable official-code order.

    This helper is defensive: malformed grids or positions yield ``(STAY,)``.
    """
    board = _board(grid)
    pos = _position(position)
    if board is None or pos is None or not _inside(board, pos):
        return (STAY,)
    actions = []
    for action in range(4):
        nxt = _step(pos, action)
        if _inside(board, nxt) and board[nxt[0]][nxt[1]] != OBSTACLE:
            actions.append(action)
    if include_stay:
        actions.append(STAY)
    return tuple(actions) or (STAY,)


class NPCModel:
    """Fitted gold-seeking NPC approximation with injected RNG determinism."""

    def __init__(self, rng: Optional[Any] = None, seed: int = 0) -> None:
        self.rng = random.Random(seed) if rng is None else rng
        if not callable(getattr(self.rng, "choice", None)):
            raise TypeError("rng must provide choice(sequence)")

    def _paths(
        self, board: Sequence[Sequence[int]], pos: Position
    ) -> tuple[dict[Position, int], dict[Position, Tuple[int, ...]]]:
        """BFS distances and all first actions on shortest paths from pos."""
        distances = {pos: 0}
        first_actions: dict[Position, Tuple[int, ...]] = {pos: ()}
        queue = deque([pos])
        while queue:
            current = queue.popleft()
            next_distance = distances[current] + 1
            for action in range(4):
                nxt = _step(current, action)
                if not _inside(board, nxt) or board[nxt[0]][nxt[1]] == OBSTACLE:
                    continue
                first = (action,) if current == pos else first_actions[current]
                if nxt not in distances:
                    distances[nxt] = next_distance
                    first_actions[nxt] = first
                    queue.append(nxt)
                elif distances[nxt] == next_distance:
                    # Preserve stable order and avoid duplicate first actions.
                    merged = first_actions[nxt] + tuple(
                        action_code
                        for action_code in first
                        if action_code not in first_actions[nxt]
                    )
                    first_actions[nxt] = merged
        return distances, first_actions

    def _action(self, board: list[list[int]], pos: Position) -> int:
        distances, first_actions = self._paths(board, pos)
        best_numerator = 0
        best_denominator = 1
        targets: list[Position] = []

        for row, values in enumerate(board):
            for col, value in enumerate(values):
                target = (row, col)
                distance = distances.get(target, 0)
                if value <= 0 or distance <= 0:
                    continue
                denominator = distance**DISTANCE_POWER
                comparison = value * best_denominator - best_numerator * denominator
                if comparison > 0:
                    best_numerator = value
                    best_denominator = denominator
                    targets = [target]
                elif comparison == 0:
                    targets.append(target)

        if targets:
            target = self.rng.choice(targets)
            return self.rng.choice(first_actions[target])

        cardinal = legal_actions(board, pos, include_stay=False)
        if cardinal == (STAY,):
            return STAY
        return self.rng.choice(cardinal)

    def actions(
        self,
        grid: Sequence[Sequence[int]],
        position: Any,
        npc_id: Optional[int] = None,
    ) -> Actions:
        """Return three sequential, physically legal actions.

        ``npc_id`` is intentionally ignored: g0-g2 did not justify an
        identity-specific policy.  A private board copy prevents caller state
        mutation.  Planning removes ``ceil(65% * gold)`` after each entry,
        leaving exactly ``floor(35% * gold)`` for non-negative integer gold.
        """
        del npc_id
        board = _board(grid)
        pos = _position(position)
        if board is None or pos is None or not _inside(board, pos):
            return (STAY, STAY, STAY)
        if board[pos[0]][pos[1]] == OBSTACLE:
            return (STAY, STAY, STAY)

        result = []
        for _ in range(ACTIONS_PER_TURN):
            action = self._action(board, pos)
            nxt = _step(pos, action)
            # _action is legal by construction; retain a defensive fallback.
            if not _inside(board, nxt) or board[nxt[0]][nxt[1]] == OBSTACLE:
                action = STAY
                nxt = pos
            result.append(action)
            pos = nxt
            value = board[pos[0]][pos[1]]
            if value > 0:
                board[pos[0]][pos[1]] = value * 35 // 100

        return tuple(result)  # type: ignore[return-value]

    __call__ = actions


def choose_actions(
    grid: Sequence[Sequence[int]],
    position: Any,
    rng: Optional[Any] = None,
    seed: int = 0,
    npc_id: Optional[int] = None,
) -> Actions:
    """One-shot convenience wrapper around :class:`NPCModel`."""
    return NPCModel(rng=rng, seed=seed).actions(grid, position, npc_id=npc_id)


__all__ = [
    "NPCModel",
    "choose_actions",
    "legal_actions",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "STAY",
]
