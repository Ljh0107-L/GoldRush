"""Maximum-vision, non-competitive opponent observation probe.

This file is deliberately standalone because the arena uploads only this file.
The policy buys the 9x9 view every round and moves only to maintain observation;
it never uses gold as a movement objective.
"""

from itertools import product


_ACTION_DELTA = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
_ROUTES = tuple(product(range(5), repeat=3))
_FALLBACK = [4, 4, 4, 4, 4, 4, 3, 0, 2]


def _cell(value):
    if value is None:
        return None
    try:
        row, col = int(value.row), int(value.col)
    except AttributeError:
        row, col = int(value[0]), int(value[1])
    if 0 <= row < 17 and 0 <= col < 17:
        return (row, col)
    return None


def _chebyshev(left, right):
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def _clamp(cell):
    return (max(0, min(16, cell[0])), max(0, min(16, cell[1])))


class Player:
    """Stateful observer with two lightweight enemy tracks."""

    def __init__(self):
        self._tracks = [self._new_track(), self._new_track()]
        self._fallbacks = 0
        self._decisions = 0
        self._visible_rounds = 0
        self._visible_units = 0

    @staticmethod
    def _new_track():
        return {"pos": None, "velocity": (0, 0), "missed": 999}

    def _reset(self):
        self._tracks = [self._new_track(), self._new_track()]
        self._fallbacks = 0
        self._decisions = 0
        self._visible_rounds = 0
        self._visible_units = 0

    @staticmethod
    def _predicted(track):
        position = track["pos"]
        if position is None:
            return None
        velocity = track["velocity"]
        horizon = min(track["missed"] + 1, 3)
        return _clamp((position[0] + velocity[0] * horizon,
                       position[1] + velocity[1] * horizon))

    @staticmethod
    def _observe(track, position):
        previous = track["pos"]
        if previous is None or track["missed"] > 1:
            velocity = (0, 0)
        else:
            velocity = (position[0] - previous[0], position[1] - previous[1])
            velocity = (max(-3, min(3, velocity[0])),
                        max(-3, min(3, velocity[1])))
        track["pos"] = position
        track["velocity"] = velocity
        track["missed"] = 0

    def _update_tracks(self, observations, own_units):
        for track in self._tracks:
            track["missed"] += 1

        if len(observations) == 2:
            predictions = [self._predicted(track) for track in self._tracks]
            references = [predictions[index] or own_units[index] for index in range(2)]
            straight = (_chebyshev(references[0], observations[0]) +
                        _chebyshev(references[1], observations[1]))
            crossed = (_chebyshev(references[0], observations[1]) +
                       _chebyshev(references[1], observations[0]))
            assigned = observations if straight <= crossed else observations[::-1]
            self._observe(self._tracks[0], assigned[0])
            self._observe(self._tracks[1], assigned[1])
            return [assigned[0], assigned[1]], [True, True]

        if len(observations) == 1:
            observation = observations[0]
            predictions = [self._predicted(track) for track in self._tracks]
            references = [predictions[index] or own_units[index] for index in range(2)]
            index = min(range(2), key=lambda item: _chebyshev(references[item], observation))
            self._observe(self._tracks[index], observation)
            # Both observers converge on the sighting, but route scoring keeps a
            # standoff ring and reserves distinct endpoints.
            return [observation, observation], [True, True]

        targets = []
        tracked = []
        for track in self._tracks:
            prediction = self._predicted(track)
            if prediction is not None and track["missed"] <= 16:
                targets.append(prediction)
                tracked.append(True)
            else:
                targets.append(None)
                tracked.append(False)
        return targets, tracked

    @staticmethod
    def _search_target(round_number, unit_index):
        waypoints = ((5, 6), (6, 11), (11, 10), (10, 5))
        phase = (round_number // 10 + unit_index * 2) % len(waypoints)
        return waypoints[phase]

    @staticmethod
    def _route_score(route, start, target, mode, grid, hard_blocked, visible_enemies, reserved):
        position = start
        path = []
        gold_steps = 0
        stays = 0
        for action in route:
            delta = _ACTION_DELTA[action]
            candidate = (position[0] + delta[0], position[1] + delta[1])
            if action == 4:
                candidate = position
                stays += 1
            if not (0 <= candidate[0] < 17 and 0 <= candidate[1] < 17):
                return None
            if candidate in hard_blocked:
                return None
            cell_value = int(grid[candidate[0]][candidate[1]])
            # Unknown cells are not used: with the purchased radius four, all
            # three planned steps are known after round zero. This prevents a
            # speculative third step from wasting itself against an unseen wall.
            if cell_value in (-5, -3, -1):
                return None
            if cell_value > 0:
                gold_steps += 1
            position = candidate
            path.append(position)

        distance = _chebyshev(position, target)
        if mode == "visible":
            score = abs(distance - 3) * 140
            if distance <= 1:
                score += 20000
            if distance > 4:
                score += (distance - 4) * 450
        elif mode == "tracked":
            score = abs(distance - 2) * 90
            if distance > 4:
                score += (distance - 4) * 180
        else:
            score = distance * 100

        # Gold is never an objective. A modest penalty chooses an equally good
        # empty route without preventing observation when no empty route exists.
        score += gold_steps * 35 + stays * 3
        for step in path:
            for enemy in visible_enemies:
                proximity = _chebyshev(step, enemy)
                if proximity == 0:
                    score += 30000
                elif proximity == 1:
                    score += 6000
            for occupied in reserved:
                proximity = _chebyshev(step, occupied)
                if proximity == 0:
                    score += 12000
                elif proximity == 1:
                    score += 120
        return (score, route, position)

    def _best_route(self, start, target, mode, grid, hard_blocked, visible_enemies, reserved):
        best = None
        for route in _ROUTES:
            candidate = self._route_score(
                route, start, target, mode, grid, hard_blocked,
                visible_enemies, reserved,
            )
            if candidate is not None and (best is None or candidate[:2] < best[:2]):
                best = candidate
        if best is None:
            return (4, 4, 4), start
        return best[1], best[2]

    def MoveDecision(self, game_input):
        """Return six legal actions, k=3, order=0, and unconditional vp=2."""
        try:
            round_number = int(game_input.round)
            if round_number == 0 and self._decisions:
                self._reset()
            grid = game_input.grid
            own_units = [_cell(game_input.my_units[index]) for index in range(2)]
            if any(position is None for position in own_units):
                raise ValueError("missing own-unit position")
            observations = []
            for raw in game_input.visible_enemies:
                position = _cell(raw)
                if position is not None and position not in observations:
                    observations.append(position)

            self._decisions += 1
            self._visible_units += len(observations)
            if observations:
                self._visible_rounds += 1

            targets, tracked = self._update_tracks(observations, own_units)
            modes = []
            for index in range(2):
                if observations:
                    modes.append("visible")
                elif tracked[index]:
                    modes.append("tracked")
                else:
                    targets[index] = self._search_target(round_number, index)
                    modes.append("search")

            visible_enemies = tuple(observations)
            route0, endpoint0 = self._best_route(
                own_units[0], targets[0], modes[0], grid,
                {own_units[1]}, visible_enemies, (),
            )
            route1, _endpoint1 = self._best_route(
                own_units[1], targets[1], modes[1], grid,
                {endpoint0}, visible_enemies, (endpoint0,),
            )
            return list(route0 + route1 + (3, 0, 2))
        except Exception:
            # The arena forfeits the whole game on an uncaught exception. Keep
            # maximum vision even in the conservative all-stay fallback.
            self._fallbacks += 1
            return list(_FALLBACK)
