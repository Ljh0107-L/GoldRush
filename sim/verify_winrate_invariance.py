#!/usr/bin/env python3
"""Prove that dP(win)/d(margin) does not depend on how many games a pairing plays.

WHY THIS EXISTS
    While pricing marginal gold I asserted that if the qualifier played N games per
    pairing, per-game noise would average out by sqrt(N), the near-tie band would narrow,
    and the exchange rate would fall.  That is wrong, and the error is one of operator
    order -- the same shape as several other incidents in this project.

    Win rate is not "the sign of the mean margin", it is "the mean of the per-game signs":

        E[win rate vs opponent i] = P(margin_ij > 0) = Phi(mu_i / sigma_single)

    N does not appear.  ``sign()`` is applied per game, and ``mean(sign(x))`` is not
    ``sign(mean(x))``, so playing a pairing more often buys precision on our ESTIMATE of
    the win rate without changing the win rate itself.

    The mistaken version would have been right under a different scoring rule -- one where
    each pairing yields a single win decided by a majority of N games.  This module
    simulates both rules so the distinction is a measurement rather than an argument.
    The platform reports wins/games (the ladder figure 0.2320 was reproduced from game
    counts against a displayed 0.2314), so the first rule is the operative one.

CONSEQUENCE
    ``sigma_single`` is the ONLY rules-dependent quantity entering the slope, and it is set
    by within-game randomness: gold generation, NPC behaviour, and move order.  If the
    finals change maps or NPC counts, the slope moves as 1/sigma.  That is the thing to
    recalibrate -- not the number of games per pairing.

Usage
    python3 sim/verify_winrate_invariance.py
Deterministic: fixed seed.
"""

from __future__ import annotations

import math
import random

SEED = 20260810
SIGMA_SINGLE = 766.0        # measured pooled within-team single-game SD of margin
SHOCK = 100.0               # the candidate we are pricing, in gold of margin
TRIALS_PER_TEAM = 4000
TRUE_MARGINS = [-800, -700, -600, -500, -400, -300, -250, -200, -150, -100, -50, 0,
                25, 50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900,
                1000, 1100, 1200, 1300, 1400]


def _rate_by_game(rng: random.Random, n_per_pairing: int, shift: float) -> float:
    """Rule (a): win rate = won games / played games.  What the platform reports."""
    won = played = 0
    for mu in TRUE_MARGINS:
        for _ in range(TRIALS_PER_TEAM):
            for _ in range(n_per_pairing):
                played += 1
                won += rng.gauss(mu + shift, SIGMA_SINGLE) > 0
    return won / played


def _rate_by_pairing(rng: random.Random, n_per_pairing: int, shift: float) -> float:
    """Rule (b): each pairing is one win, decided by a majority of N games."""
    won = played = 0
    for mu in TRUE_MARGINS:
        for _ in range(TRIALS_PER_TEAM):
            k = sum(1 for _ in range(n_per_pairing)
                    if rng.gauss(mu + shift, SIGMA_SINGLE) > 0)
            played += 1
            won += 2 * k > n_per_pairing
    return won / played


def main() -> int:
    print(f"sigma_single = {SIGMA_SINGLE:.0f} gold, shock = +{SHOCK:.0f} gold of margin, "
          f"{len(TRUE_MARGINS)} teams x {TRIALS_PER_TEAM} trials")
    print(f"{'N':>3} {'(a) wins/games':>16} {'dP pp':>8} | {'(b) majority of N':>19} {'dP pp':>8}")
    slopes_a, slopes_b = [], []
    for n in (1, 3, 9, 25):
        rng = random.Random(SEED)
        a0 = _rate_by_game(rng, n, 0.0)
        rng = random.Random(SEED)          # common random numbers: same draws, shifted mean
        a1 = _rate_by_game(rng, n, SHOCK)
        rng = random.Random(SEED + 1)
        b0 = _rate_by_pairing(rng, n, 0.0)
        rng = random.Random(SEED + 1)
        b1 = _rate_by_pairing(rng, n, SHOCK)
        da, db = 100 * (a1 - a0), 100 * (b1 - b0)
        slopes_a.append(da)
        slopes_b.append(db)
        print(f"{n:>3} {a0:>16.4f} {da:>8.2f} | {b0:>19.4f} {db:>8.2f}")

    spread_a = max(slopes_a) - min(slopes_a)
    growth_b = slopes_b[-1] / slopes_b[0]
    print()
    print(f"  rule (a) slope spread over N=1..25 : {spread_a:.2f} pp  (no trend -> invariant)")
    print(f"  rule (b) slope growth  N=1 -> N=25 : {growth_b:.2f}x   (monotone -> N-dependent)")
    ok = spread_a < 0.5 and growth_b > 1.5
    print(f"  RESULT: {'PASS' if ok else 'FAIL'} -- the platform uses rule (a), so the slope "
          "carries no 1/sqrt(N) discount and the one-game-per-team design is "
          "unconditionally correct")

    f0 = 0.000511   # measured margin density at zero, per gold (band +/-200, n=333)
    implied = 0.3989 / f0
    print()
    print("  cross-check by an independent route:")
    print(f"    measured f(0) = {f0:.6f}/gold -> implied sigma = 0.3989/f(0) = {implied:.0f}")
    print(f"    independently measured pooled within-team SD  = {SIGMA_SINGLE:.0f}")
    print(f"    agreement {implied / SIGMA_SINGLE:.3f}x "
          f"({abs(implied / SIGMA_SINGLE - 1) * 100:.1f}% apart)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
