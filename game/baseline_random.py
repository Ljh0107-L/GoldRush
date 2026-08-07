# baseline_random.py - GoldRush 2.0 sparring partner
# Random-but-legal strategy. Used as a fixed opponent to measure whether a new
# version actually improves. Deliberately ASCII-only and dependency-free.
import random

SAFE = [4, 4, 4, 4, 4, 4, 3, 0, 0]


class Player:
    def MoveDecision(self, game_input):
        try:
            return [random.randint(0, 4) for _ in range(6)] + [3, 0, 0]
        except Exception:
            return list(SAFE)
