#!/usr/bin/env python3
"""Own-player spatial null (Part A) and cross-round area coverage (Part B).

Purpose
=======

Two deliverables about **our own** frozen player (``src/player.cpp`` at
``fd47ea6``; every later commit touching that file is comment-only, verified by
``git diff fd47ea6 HEAD -- src/player.cpp`` containing zero non-comment lines).

**Part A -- real-strategy null.**  Our player provably has no periodic policy:
it is a greedy 5x5 scanner whose only fallback is a pair of *static* anchors
``(6,8)`` and ``(11,8)`` (``src/player.cpp:409``:
``g_s.anch_r[u] = 6 + 5*u; g_s.anch_c[u] = 8``).  Measuring the four metric
families (partition / periodicity / amplitude / phase) on this player therefore
calibrates how much apparent periodicity the *environment* injects, because the
world itself is periodic: bombs are completely resampled every 20 rounds
(``sim/scenario.py:52`` ``BOMB_PERIOD = 20``, used at ``sim/scenario.py:702``
``if round_number % BOMB_PERIOD == 0``) and outer-ring gold events fire on a
near-flat 8..16 round wait (``sim/scenario.py:691-694``
``next_outer += rng.randint(8, 16)``).

**Part B -- cross-round area coverage.**  How much of the board our two units
actually *stand on* over 500 rounds, how much is ever *targetable* (the target
selector only reads its own 5x5), and how much gold accumulates where they never
go -- with the stock/flow correction shown rather than asserted, and with the
contested / uncontested regimes separated.

Both parts are reported separately for the two action-order arms and are never
subtracted from one another.

Exactness
=========

Every quantity is derived from official-format logs, and two independent
reconstructions are validated to the last cell:

* **path exactness** -- the log stores *effective* actions
  (``sim/engine.py:1116`` builds ``UnitState`` from
  ``player_effective[...]``, and ``execute_action`` appends ``effective``, not
  ``requested``), so replaying them from the start position must land exactly on
  the logged end position.  Reported as ``path_replay_exact``.
* **ground exactness** -- replaying the whole round (all actors, in the logged
  ``dispatch_order``, ``ceil(65%)`` pickup, bomb removal) must reproduce the
  logged end-phase positive ground exactly.  ``render_full``
  (``sim/engine.py:850-863``) only overwrites cells whose ground is ``0``, so
  positive gold is never masked by the ``-2``/``-4`` actor markers and the log's
  ``grid`` is a lossless record of the gold stock.  Reported as
  ``ground_replay_exact``.

Subcommands
===========

``validate``  three-way dry run (zero-signal / injected-effect / reversed) on
              synthetic input, plus internal self-checks.  Exits 0 on success.
``run``       parse log batches, aggregate, write the report JSON.

Bias register (read before quoting any number)
==============================================

The simulator's NPC model reproduces real NPC actions only 38.7-39.2% of the
time and is both too greedy and too centre-biased (``sim/README.md`` section 7).
Absolute income is therefore not comparable to the platform; only same-seed
paired deltas are.  Part B is a **scarcity / dispersion** measurement, which the
repo's own guard rail says is systematically **over**-estimated by this
simulator (central-efficiency effects are under-estimated).  Direction of every
Part B number is annotated in the output under ``bias``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence

GRID = 17
CENTER = (8, 8)
ROUNDS = 500
STAY = 4
ACTION_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
WALL = -1
BOMB = -3
PLAYER_MARK = -2
NPC_MARK = -4
FOG = -5
SELECTOR_RADIUS = 2          # src/player.cpp scans a 5x5 window per unit
VP_PRICES = (0, 2, 3)
VP_RADII = (2, 3, 4)
ANCHORS = ((6, 8), (11, 8))  # src/player.cpp:409

ACF_MAX_LAG = 80
XCORR_MAX_LAG = 20
PERIOD_MIN = 3.0
PERIOD_MAX = 80.0
PERIOD_BAND = (4.0, 40.0)    # orchestrator-mandated reporting band
BOMB_PERIOD = 20             # sim/scenario.py:52
OUTER_WAIT_BAND = (8, 16)    # sim/scenario.py:691-694
PERMUTATIONS = 100
PERM_SALT = "goldrush-area-coverage-perm-v1"

STRATUM_CENTRAL = "central_d_le_4"
STRATUM_OUTER = "outer_d_ge_5"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #

def ring(cell: tuple[int, int]) -> int:
    return max(abs(cell[0] - CENTER[0]), abs(cell[1] - CENTER[1]))


RING_OF = [[ring((r, c)) for c in range(GRID)] for r in range(GRID)]
CENTRAL_CELLS = tuple(
    (r, c) for r in range(GRID) for c in range(GRID) if RING_OF[r][c] <= 4
)
OUTER_CELLS = tuple(
    (r, c) for r in range(GRID) for c in range(GRID) if RING_OF[r][c] >= 5
)
STRATA = {STRATUM_CENTRAL: CENTRAL_CELLS, STRATUM_OUTER: OUTER_CELLS}


def cheb_window(cell: tuple[int, int], radius: int) -> list[tuple[int, int]]:
    row, col = cell
    return [
        (r, c)
        for r in range(max(0, row - radius), min(GRID, row + radius + 1))
        for c in range(max(0, col - radius), min(GRID, col + radius + 1))
    ]


WINDOW_CACHE: dict[tuple[int, int, int], tuple[tuple[int, int], ...]] = {}


def window(cell: tuple[int, int], radius: int) -> tuple[tuple[int, int], ...]:
    key = (cell[0], cell[1], radius)
    got = WINDOW_CACHE.get(key)
    if got is None:
        got = tuple(cheb_window(cell, radius))
        WINDOW_CACHE[key] = got
    return got


def region_id(row: int, col: int) -> int:
    """Windmill region 1..5, byte-for-byte the rule at sim/engine.py:84-95."""
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    return 5


REGION_OF = [[region_id(r, c) for c in range(GRID)] for r in range(GRID)]
SNAPSHOT_OFFSETS = tuple(range(-8, 12))


def snapshot_metrics(
    snapshots: Sequence[tuple[int, Mapping[int, Mapping[str, int]]]],
    ground_start: Sequence[Sequence[Sequence[int]]],
    end_pos: Sequence[Sequence[tuple[int, int]]],
) -> dict[str, Any]:
    """B5 -- what the fog-free global sensor says, and whether we act on it.

    ``Snapshot`` is the only fog-independent field in ``GameInput``
    (``src/game_api.h:52-53``): every five rounds it reports, per windmill
    region, ``gold_generated / gold_collected / gold_remaining / occupants /
    enter / leave``.  ``gold_remaining`` samples ``start[r]``
    (``sim/README.md`` section 4), i.e. the stock the region holds at the
    moment the player receives the snapshot.

    Our delivered player reads exactly four ``GameInput`` fields -- ``round``,
    ``grid``, ``my_units``, ``my_units_gold`` -- and never touches ``snapshot``
    (grep count 0 in ``src/player.cpp``), so item (ii) below is expected to sit
    at or below the 4-arm chance level of 25%.
    """
    arm_gold: list[int] = []
    arm_gold_by_region: dict[int, list[int]] = {r: [] for r in (2, 3, 4, 5)}
    total_told: list[int] = []
    told_not_visible: list[int] = []
    told_not_visible_outer: list[int] = []
    visible_gold: list[int] = []
    snapshot_consistency_ok = 0
    hits = {offset: 0 for offset in SNAPSHOT_OFFSETS}
    outer_unit_rounds = {offset: 0 for offset in SNAPSHOT_OFFSETS}
    hits_rich = 0
    outer_rich = 0
    arm_hist: Counter = Counter()
    for round_number, regions in snapshots:
        remaining = {rid: int(regions[rid]["gold_remaining"]) for rid in (1, 2, 3, 4, 5)}
        best_arm = max((2, 3, 4, 5), key=lambda rid: (remaining[rid], -rid))
        arm_gold.append(remaining[best_arm])
        arm_gold_by_region[best_arm].append(remaining[best_arm])
        arm_hist[best_arm] += 1

        sg = ground_start[round_number]
        board_total = sum(v for row in sg for v in row if v > 0)
        told = sum(remaining.values())
        if told == board_total:
            snapshot_consistency_ok += 1
        seen = set()
        for j in (0, 1):
            # the snapshot arrives with round r's input, so the relevant window is
            # the one around the position the units hold at start[r] == end[r-1]
            reference = end_pos[round_number - 1][j] if round_number > 0 else end_pos[0][j]
            seen.update(window(reference, SELECTOR_RADIUS))
        vis = sum(sg[r][c] for r, c in seen if sg[r][c] > 0)
        visible_gold.append(vis)
        total_told.append(told)
        told_not_visible.append(told - vis)
        outer_told = sum(remaining[rid] for rid in (2, 3, 4, 5))
        outer_vis = sum(sg[r][c] for r, c in seen
                        if sg[r][c] > 0 and REGION_OF[r][c] != 1)
        told_not_visible_outer.append(outer_told - outer_vis)

        for offset in SNAPSHOT_OFFSETS:
            t = round_number + offset
            if not 0 <= t < ROUNDS:
                continue
            for j in (0, 1):
                cell = end_pos[t][j]
                if RING_OF[cell[0]][cell[1]] < 5:
                    continue
                outer_unit_rounds[offset] += 1
                if REGION_OF[cell[0]][cell[1]] == best_arm:
                    hits[offset] += 1
        if remaining[best_arm] >= 40:
            for offset in range(0, 5):
                t = round_number + offset
                if not 0 <= t < ROUNDS:
                    continue
                for j in (0, 1):
                    cell = end_pos[t][j]
                    if RING_OF[cell[0]][cell[1]] < 5:
                        continue
                    outer_rich += 1
                    if REGION_OF[cell[0]][cell[1]] == best_arm:
                        hits_rich += 1

    post = sum(hits[o] for o in range(0, 5))
    post_n = sum(outer_unit_rounds[o] for o in range(0, 5))
    pre = sum(hits[o] for o in range(-8, -3))
    pre_n = sum(outer_unit_rounds[o] for o in range(-8, -3))
    return {
        "snapshots": len(snapshots),
        "snapshot_totals_match_decoded_ground": snapshot_consistency_ok,
        "argmax_outer_arm_gold_remaining": mean_se(arm_gold),
        "argmax_outer_arm_histogram": {str(k): v for k, v in sorted(arm_hist.items())},
        "total_gold_reported_by_snapshot": mean_se(total_told),
        "gold_inside_our_two_5x5_windows": mean_se(visible_gold),
        "gold_told_but_not_visible": mean_se(told_not_visible),
        "gold_told_but_not_visible_outer_only": mean_se(told_not_visible_outer),
        "outer_unit_rounds_in_argmax_arm": {
            "post_offsets_0_to_4": {"hits": post, "outer_unit_rounds": post_n,
                                    "share": (post / post_n) if post_n else None},
            "pre_offsets_minus8_to_minus4": {"hits": pre, "outer_unit_rounds": pre_n,
                                             "share": (pre / pre_n) if pre_n else None},
            "rich_arm_ge_40_offsets_0_to_4": {
                "hits": hits_rich, "outer_unit_rounds": outer_rich,
                "share": (hits_rich / outer_rich) if outer_rich else None},
            "chance_level": 0.25,
            "by_offset": {
                str(o): {"hits": hits[o], "outer_unit_rounds": outer_unit_rounds[o],
                         "share": (hits[o] / outer_unit_rounds[o])
                                  if outer_unit_rounds[o] else None}
                for o in SNAPSHOT_OFFSETS
            },
        },
    }


# --------------------------------------------------------------------------- #
# statistics helpers -- every point estimate carries an SE
# --------------------------------------------------------------------------- #

def mean_se(values: Sequence[float]) -> dict[str, Any]:
    """Mean, standard error of the mean, n, and distribution landmarks."""
    data = [float(v) for v in values]
    n = len(data)
    if n == 0:
        return {"n": 0, "mean": None, "se": None, "sd": None,
                "median": None, "p10": None, "p90": None,
                "min": None, "max": None}
    mu = math.fsum(data) / n
    if n >= 2:
        sd = math.sqrt(math.fsum((v - mu) ** 2 for v in data) / (n - 1))
        se = sd / math.sqrt(n)
    else:
        sd = 0.0
        se = None
    return {
        "n": n, "mean": mu, "se": se, "sd": sd,
        "median": statistics.median(data),
        "p10": percentile(data, 0.10), "p90": percentile(data, 0.90),
        "min": min(data), "max": max(data),
    }


def percentile(values: Sequence[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return float(ordered[index])


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


class Rng:
    """Deterministic 64-bit SplitMix generator (no dependence on random module
    internals, so results are stable across interpreter versions)."""

    __slots__ = ("state",)

    def __init__(self, seed_material: Any) -> None:
        digest = hashlib.sha256(
            json.dumps(seed_material, sort_keys=True, separators=(",", ":")).encode()
        ).digest()
        self.state = int.from_bytes(digest[:8], "big")

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def bit(self) -> int:
        return self.next_u64() & 1

    def random(self) -> float:
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def gauss(self) -> float:
        u1 = max(self.random(), 1e-12)
        u2 = self.random()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def randint(self, low: int, high: int) -> int:
        return low + int(self.next_u64() % (high - low + 1))


# --------------------------------------------------------------------------- #
# series estimators
# --------------------------------------------------------------------------- #

def gap_aware_acf(
    values: Sequence[float | None], max_lag: int = ACF_MAX_LAG
) -> dict[str, Any]:
    """Autocorrelation using only lag pairs where both samples are present.

    ``None`` marks a gap.  Our own logs have no gaps, but the estimator is the
    same one applied to fog-truncated opponent data so the two are comparable.
    """
    present = [(t, float(v)) for t, v in enumerate(values) if v is not None]
    n = len(present)
    if n < 3:
        return {"n_present": n, "variance": None, "acf": {}, "degenerate": True}
    mu = math.fsum(v for _, v in present) / n
    var = math.fsum((v - mu) ** 2 for _, v in present) / n
    if var <= 0.0:
        return {"n_present": n, "variance": 0.0, "acf": {}, "degenerate": True,
                "reason": "zero variance"}
    lookup = dict(present)
    acf: dict[str, dict[str, float]] = {}
    for lag in range(1, max_lag + 1):
        pairs = 0
        total = 0.0
        for t, v in present:
            other = lookup.get(t + lag)
            if other is not None:
                pairs += 1
                total += (v - mu) * (other - mu)
        if pairs >= 8:
            r = total / (pairs * var)
            acf[str(lag)] = {"r": r, "pairs": pairs,
                             "se_white": 1.0 / math.sqrt(pairs)}
    return {"n_present": n, "variance": var, "acf": acf, "degenerate": False}


def period_grid() -> list[float]:
    """Deterministic period grid: uniform in frequency plus exact integers."""
    grid: set[float] = set()
    n_freq = 200
    f_lo, f_hi = 1.0 / PERIOD_MAX, 1.0 / PERIOD_MIN
    for i in range(n_freq + 1):
        f = f_lo + (f_hi - f_lo) * i / n_freq
        grid.add(round(1.0 / f, 6))
    for p in range(3, 81):
        grid.add(float(p))
    return sorted(grid)


PERIODS = period_grid()
_LS_BASIS: dict[tuple[int, int], list[tuple[float, list[float], list[float], float, float]]] = {}


def _ls_basis(n: int) -> list[tuple[float, list[float], list[float], float, float]]:
    """cos/sin basis at the Scargle time offset tau, cached per series length."""
    key = (n, len(PERIODS))
    got = _LS_BASIS.get(key)
    if got is not None:
        return got
    times = list(range(n))
    basis = []
    for p in PERIODS:
        w = 2.0 * math.pi / p
        s2 = math.fsum(math.sin(2.0 * w * t) for t in times)
        c2 = math.fsum(math.cos(2.0 * w * t) for t in times)
        tau = math.atan2(s2, c2) / (2.0 * w)
        cos_t = [math.cos(w * (t - tau)) for t in times]
        sin_t = [math.sin(w * (t - tau)) for t in times]
        cc = math.fsum(c * c for c in cos_t)
        ss = math.fsum(s * s for s in sin_t)
        basis.append((p, cos_t, sin_t, cc, ss))
    _LS_BASIS[key] = basis
    return basis


def lomb_scargle(values: Sequence[float | None]) -> dict[str, Any]:
    """Normalised Lomb-Scargle periodogram.

    Normalisation is Scargle's: ``P(w) = (1/2s^2)[...]`` with ``s^2`` the sample
    variance, for which independent Gaussian noise gives ``P`` approximately
    Exponential(mean 1).  The white-noise reference is *not* taken on faith --
    ``validate`` measures the null peak-power distribution empirically and the
    ``run`` output carries the environment-calibrated figure.
    """
    present = [(t, float(v)) for t, v in enumerate(values) if v is not None]
    n = len(present)
    if n < 8:
        return {"n_present": n, "degenerate": True, "power": {}, "peak": None}
    mu = math.fsum(v for _, v in present) / n
    var = math.fsum((v - mu) ** 2 for _, v in present) / (n - 1)
    if var <= 0.0:
        return {"n_present": n, "degenerate": True, "power": {}, "peak": None,
                "reason": "zero variance"}
    dense = len(present) == len(values)
    if dense:
        basis = _ls_basis(len(values))
        centred = [float(v) - mu for _, v in present]
        power = {}
        for p, cos_t, sin_t, cc, ss in basis:
            cs = math.fsum(x * c for x, c in zip(centred, cos_t))
            sn = math.fsum(x * s for x, s in zip(centred, sin_t))
            acc = 0.0
            if cc > 1e-12:
                acc += cs * cs / cc
            if ss > 1e-12:
                acc += sn * sn / ss
            power[p] = acc / (2.0 * var)
    else:
        times = [t for t, _ in present]
        centred = [v - mu for _, v in present]
        power = {}
        for p in PERIODS:
            w = 2.0 * math.pi / p
            s2 = math.fsum(math.sin(2.0 * w * t) for t in times)
            c2 = math.fsum(math.cos(2.0 * w * t) for t in times)
            tau = math.atan2(s2, c2) / (2.0 * w)
            cs = sn = cc = ss = 0.0
            for t, x in zip(times, centred):
                ct = math.cos(w * (t - tau))
                st = math.sin(w * (t - tau))
                cs += x * ct
                sn += x * st
                cc += ct * ct
                ss += st * st
            acc = 0.0
            if cc > 1e-12:
                acc += cs * cs / cc
            if ss > 1e-12:
                acc += sn * sn / ss
            power[p] = acc / (2.0 * var)

    band = [(p, v) for p, v in power.items() if PERIOD_BAND[0] <= p <= PERIOD_BAND[1]]
    band_peak_period, band_peak_power = max(band, key=lambda kv: (kv[1], -kv[0]))
    all_peak_period, all_peak_power = max(power.items(), key=lambda kv: (kv[1], -kv[0]))
    m_eff = len(power)
    return {
        "n_present": n,
        "degenerate": False,
        "peak": {"period": all_peak_period, "power": all_peak_power,
                 "fap": false_alarm(all_peak_power, m_eff)},
        "band_peak": {"period": band_peak_period, "power": band_peak_power,
                      "band": list(PERIOD_BAND),
                      "fap": false_alarm(band_peak_power, sum(1 for _ in band))},
        "power_at_bomb_period": power.get(float(BOMB_PERIOD)),
        "power_in_outer_band": {
            str(p): power[float(p)]
            for p in range(OUTER_WAIT_BAND[0], OUTER_WAIT_BAND[1] + 1)
            if float(p) in power
        },
        "power": {("%.6g" % p): v for p, v in sorted(power.items())},
    }


def false_alarm(power: float, m_independent: int) -> float:
    """Conservative FAP for the maximum of m independent Exp(1) draws."""
    if power <= 0.0:
        return 1.0
    single = math.exp(-power)
    if single <= 0.0:
        return 0.0
    return 1.0 - (1.0 - single) ** max(1, m_independent)


def turning_point_excursions(values: Sequence[float]) -> dict[str, Any]:
    """Peak-trough decomposition with plateaus compressed.

    An *excursion peak* is a strict local maximum of the plateau-compressed
    series (boundaries count).  Excursion duration is the number of original
    samples strictly between the two bracketing minima.  This definition is
    policy-agnostic: it presumes no centre-to-periphery direction.
    """
    if not values:
        return {"peaks": [], "durations": [], "n": 0}
    compressed: list[tuple[float, int, int]] = []   # value, first index, last index
    for i, v in enumerate(values):
        if compressed and compressed[-1][0] == v:
            compressed[-1] = (v, compressed[-1][1], i)
        else:
            compressed.append((v, i, i))
    m = len(compressed)
    peaks: list[float] = []
    durations: list[int] = []
    for i in range(m):
        v, first, last = compressed[i]
        # A series that never turns has no excursion at all, so m == 1 yields
        # nothing.  At the two boundaries only the one existing neighbour has to
        # be strictly lower, because a unit that starts far out and walks in has
        # genuinely peaked at round 0.
        is_max = m >= 2 and \
                 (i == 0 or compressed[i - 1][0] < v) and \
                 (i == m - 1 or compressed[i + 1][0] < v)
        if not is_max:
            continue
        peaks.append(v)
        left = compressed[i - 1][2] if i > 0 else first
        right = compressed[i + 1][1] if i < m - 1 else last
        durations.append(right - left + 1)
    return {"peaks": peaks, "durations": durations, "n": len(peaks)}


def cross_correlation(
    xs: Sequence[float], ys: Sequence[float], max_lag: int = XCORR_MAX_LAG
) -> dict[str, float]:
    """Normalised cross-correlation of xs[t] against ys[t + lag].

    A peak at lag ``L`` means ``ys`` reproduces ``xs`` ``L`` rounds later, i.e.
    **xs leads ys by L**.  Negative ``L`` means ys leads.
    """
    n = len(xs)
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sx = math.sqrt(math.fsum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(math.fsum((y - my) ** 2 for y in ys))
    out: dict[str, float] = {}
    if sx <= 0.0 or sy <= 0.0:
        return out
    for lag in range(-max_lag, max_lag + 1):
        total = 0.0
        count = 0
        for t in range(n):
            u = t + lag
            if 0 <= u < n:
                total += (xs[t] - mx) * (ys[u] - my)
                count += 1
        if count >= 8:
            out[str(lag)] = total / (sx * sy)
    return out


# --------------------------------------------------------------------------- #
# partition estimators + within-round unit-label permutation null
# --------------------------------------------------------------------------- #

AXES = ("row", "col", "diag", "anti")


def axis_score(cell: tuple[int, int], axis: str) -> int:
    r, c = cell
    if axis == "row":
        return r
    if axis == "col":
        return c
    if axis == "diag":
        return r + c
    return r - c


def _flat(cell: tuple[int, int]) -> int:
    return cell[0] * GRID + cell[1]


def occupancy_overlap(
    pos0: Sequence[tuple[int, int]], pos1: Sequence[tuple[int, int]],
    swaps: Sequence[int] | None = None,
) -> float:
    n = len(pos0)
    h0 = [0] * (GRID * GRID)
    h1 = [0] * (GRID * GRID)
    for t in range(n):
        a, b = pos0[t], pos1[t]
        if swaps is not None and swaps[t]:
            a, b = b, a
        h0[_flat(a)] += 1
        h1[_flat(b)] += 1
    return math.fsum(min(x, y) for x, y in zip(h0, h1)) / n


def centroid_separation(
    pos0: Sequence[tuple[int, int]], pos1: Sequence[tuple[int, int]],
    swaps: Sequence[int] | None = None,
) -> float:
    n = len(pos0)
    s0r = s0c = s1r = s1c = 0
    for t in range(n):
        a, b = pos0[t], pos1[t]
        if swaps is not None and swaps[t]:
            a, b = b, a
        s0r += a[0]; s0c += a[1]
        s1r += b[0]; s1c += b[1]
    return math.hypot((s0r - s1r) / n, (s0c - s1c) / n)


def best_separating_axis(
    pos0: Sequence[tuple[int, int]], pos1: Sequence[tuple[int, int]],
    swaps: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Best fixed axis + threshold, and the fraction of rounds it classifies the
    two units correctly.  Orientation (which unit is on the low side) is fixed
    across the whole game, so this cannot be gamed round by round."""
    n = len(pos0)
    best = {"axis": None, "threshold": None, "accuracy": 0.0, "orientation": None}
    for axis in AXES:
        lo_off = -GRID
        span = 3 * GRID          # covers row/col in [0,16] and diag in [-16,32]
        plus = [0] * (span + 2)
        minus = [0] * (span + 2)
        for t in range(n):
            a, b = pos0[t], pos1[t]
            if swaps is not None and swaps[t]:
                a, b = b, a
            sa = axis_score(a, axis)
            sb = axis_score(b, axis)
            if sa == sb:
                continue
            lo, hi = (sa, sb) if sa < sb else (sb, sa)
            target = plus if sa < sb else minus
            # thresholds theta with lo < theta <= hi
            start = lo + 1 - lo_off
            stop = hi - lo_off
            target[start] += 1
            target[stop + 1] -= 1
        run_p = run_m = 0
        for idx in range(span + 1):
            run_p += plus[idx]
            run_m += minus[idx]
            theta = idx + lo_off
            for orientation, count in (("unit0_low", run_p), ("unit1_low", run_m)):
                acc = count / n
                if acc > best["accuracy"]:
                    best = {"axis": axis, "threshold": theta,
                            "accuracy": acc, "orientation": orientation}
    return best


def partition_metrics(
    pos0: Sequence[tuple[int, int]], pos1: Sequence[tuple[int, int]],
    seed_material: Any, permutations: int = PERMUTATIONS,
) -> dict[str, Any]:
    observed = {
        "overlap": occupancy_overlap(pos0, pos1),
        "centroid_separation": centroid_separation(pos0, pos1),
        "axis": best_separating_axis(pos0, pos1),
    }
    rng = Rng([PERM_SALT, seed_material])
    n = len(pos0)
    null_ovl: list[float] = []
    null_sep: list[float] = []
    null_acc: list[float] = []
    for _ in range(permutations):
        swaps = [rng.bit() for _ in range(n)]
        null_ovl.append(occupancy_overlap(pos0, pos1, swaps))
        null_sep.append(centroid_separation(pos0, pos1, swaps))
        null_acc.append(best_separating_axis(pos0, pos1, swaps)["accuracy"])
    return {
        "observed": observed,
        "null_unit_label_permutation": {
            "permutations": permutations,
            "overlap": mean_se(null_ovl),
            "centroid_separation": mean_se(null_sep),
            "axis_accuracy": mean_se(null_acc),
            "p_overlap_le_observed": sum(
                1 for v in null_ovl if v <= observed["overlap"]
            ) / permutations,
            "p_separation_ge_observed": sum(
                1 for v in null_sep if v >= observed["centroid_separation"]
            ) / permutations,
            "p_accuracy_ge_observed": sum(
                1 for v in null_acc if v >= observed["axis"]["accuracy"]
            ) / permutations,
        },
    }


# --------------------------------------------------------------------------- #
# log parsing and exact replay
# --------------------------------------------------------------------------- #

def decode_ground(grid: Sequence[Sequence[int]]) -> list[list[int]]:
    """Latent ground from a full-log grid.

    ``render_full`` writes ``-2``/``-4`` only where ground is exactly ``0``,
    so positive gold and ``-1``/``-3`` survive verbatim.
    """
    out = []
    for row in grid:
        out.append([
            v if (v > 0 or v == WALL or v == BOMB) else 0
            for v in row
        ])
    return out


def walls_from_map_line(line: str) -> set[tuple[int, int]]:
    rows = json.loads(line)
    return {
        (r, c)
        for r, row in enumerate(rows)
        for c, token in enumerate(row)
        if str(token) == "1"
    }


def hotspots_from_map_line(line: str) -> set[tuple[int, int]]:
    rows = json.loads(line)
    return {
        (r, c)
        for r, row in enumerate(rows)
        for c, token in enumerate(row)
        if str(token) == "2"
    }


class ReplayError(RuntimeError):
    pass


def replay_round(
    start_ground: list[list[int]],
    dispatch: Sequence[int],
    player_plans: Mapping[int, tuple[int, Sequence[Sequence[int]]]],
    npc_plans: Mapping[int, Sequence[int]],
    start_positions: Mapping[tuple[str, int, int], tuple[int, int]],
) -> dict[str, Any]:
    """Re-execute a logged round from effective actions.

    Mirrors ``sim/engine.py:1014-1104``: bounds, wall and *player* blocking
    (NPCs never block), ``ceil(65%)`` pickup on entry only, bomb removal on
    entry.  Returns the resulting ground plus a full pickup attribution.
    """
    board = [row[:] for row in start_ground]
    positions = dict(start_positions)
    entered: dict[tuple[str, int, int], list[tuple[int, int]]] = {
        key: [] for key in positions
    }
    pickups: list[tuple[tuple[int, int], str, int, int]] = []  # cell, cls, amount, actor

    def player_cells_except(owner: int, unit: int) -> set[tuple[int, int]]:
        return {
            v for k, v in positions.items()
            if k[0] == "p" and not (k[1] == owner and k[2] == unit)
        }

    def execute(key: tuple[str, int, int], action: int, cls: str, actor: int) -> None:
        origin = positions[key]
        if action == STAY:
            return
        delta = ACTION_DELTAS[action]
        cand = (origin[0] + delta[0], origin[1] + delta[1])
        if not (0 <= cand[0] < GRID and 0 <= cand[1] < GRID):
            raise ReplayError("effective action leaves the board")
        if board[cand[0]][cand[1]] == WALL:
            raise ReplayError("effective action enters a wall")
        if key[0] == "p" and cand in player_cells_except(key[1], key[2]):
            raise ReplayError("effective action enters an occupied player cell")
        positions[key] = cand
        entered[key].append(cand)
        value = board[cand[0]][cand[1]]
        if value > 0:
            amount = (65 * value + 99) // 100
            board[cand[0]][cand[1]] = value - amount
            pickups.append((cand, cls, amount, actor))
        if board[cand[0]][cand[1]] == BOMB:
            board[cand[0]][cand[1]] = 0

    for actor_id in dispatch:
        if actor_id > 0:
            order, unit_actions = player_plans[actor_id]
            cls = "p%d" % actor_id
            for unit_index in (order, 1 - order):
                for action in unit_actions[unit_index]:
                    execute(("p", actor_id, unit_index), action, cls, actor_id)
        else:
            for action in npc_plans[actor_id]:
                execute(("n", actor_id, 0), action, "npc", actor_id)
    return {"ground": board, "positions": positions,
            "entered": entered, "pickups": pickups}


def parse_game(path: Path) -> dict[str, Any]:
    lines = path.read_bytes().splitlines()
    if len(lines) < 2 + ROUNDS:
        raise ReplayError("log %s has %d lines" % (path, len(lines)))
    names = json.loads(lines[0])
    walls = walls_from_map_line(lines[1].decode())
    hotspots = hotspots_from_map_line(lines[1].decode())
    rounds = [json.loads(line) for line in lines[2:2 + ROUNDS]]
    return {"names": names, "walls": walls, "hotspots": hotspots, "rounds": rounds}


# --------------------------------------------------------------------------- #
# per-game feature extraction
# --------------------------------------------------------------------------- #

def extract_game(
    log_path: str, batch: str, game_index: int, fixed_costs: Sequence[int],
    permutations: int, scenario_digest: str,
) -> dict[str, Any]:
    path = Path(log_path)
    parsed = parse_game(path)
    rounds = parsed["rounds"]
    walls = parsed["walls"]
    hotspots = parsed["hotspots"]

    faster_seat = 1 if fixed_costs[0] <= fixed_costs[1] else 2

    # ---- pass 1: replay every round exactly, collect grounds and attribution
    ground_start: list[list[list[int]]] = []
    ground_end: list[list[list[int]]] = []
    path_replay_exact = 0
    path_replay_total = 0
    ground_replay_exact = 0
    pickup_by_cell_class: dict[tuple[int, int], Counter] = {}
    entered_by_seat: dict[int, list[list[list[tuple[int, int]]]]] = {1: [], 2: []}
    occupied_by_seat: dict[int, list[list[list[tuple[int, int]]]]] = {1: [], 2: []}
    start_pos_by_seat: dict[int, list[list[tuple[int, int]]]] = {1: [], 2: []}
    end_pos_by_seat: dict[int, list[list[tuple[int, int]]]] = {1: [], 2: []}
    income_by_seat: dict[int, list[list[int]]] = {1: [], 2: []}
    vision_spent_by_seat: dict[int, list[int]] = {1: [], 2: []}
    real_moves_by_seat: dict[int, list[list[int]]] = {1: [], 2: []}
    action_count_by_seat: dict[int, list[list[int]]] = {1: [], 2: []}
    npc_entered: list[list[tuple[int, int]]] = []
    snapshots: list[tuple[int, dict[int, dict[str, int]]]] = []

    for rec in rounds:
        if "snapshot" in rec:
            snapshots.append((
                int(rec["snapshot"]["round"]),
                {int(reg["id"]): dict(reg) for reg in rec["snapshot"]["regions"]},
            ))
        start = rec["start"]
        end = rec["end"]
        sg = decode_ground(start["grid"])
        eg = decode_ground(end["grid"])
        ground_start.append(sg)
        ground_end.append(eg)

        start_players = {int(p["id"]): p for p in start["players"]}
        end_players = {int(p["id"]): p for p in end["players"]}
        start_npcs = {int(x["id"]): x for x in start["npcs"]}
        end_npcs = {int(x["id"]): x for x in end["npcs"]}

        start_positions: dict[tuple[str, int, int], tuple[int, int]] = {}
        player_plans: dict[int, tuple[int, list[list[int]]]] = {}
        for pid in (1, 2):
            sp = start_players[pid]
            ep = end_players[pid]
            acts = [list(u["actions"]) for u in ep["units"]]
            player_plans[pid] = (int(ep.get("order", 0) or 0), acts)
            for j in (0, 1):
                start_positions[("p", pid, j)] = tuple(sp["units"][j]["position"])
        npc_plans: dict[int, list[int]] = {}
        for nid, rec_npc in end_npcs.items():
            npc_plans[nid] = list(rec_npc["actions"])
            start_positions[("n", nid, 0)] = tuple(start_npcs[nid]["position"])

        out = replay_round(sg, list(end["dispatch_order"]), player_plans,
                           npc_plans, start_positions)

        # path exactness against the logged end positions
        for pid in (1, 2):
            for j in (0, 1):
                path_replay_total += 1
                if out["positions"][("p", pid, j)] == \
                        tuple(end_players[pid]["units"][j]["position"]):
                    path_replay_exact += 1
        for nid in npc_plans:
            path_replay_total += 1
            if out["positions"][("n", nid, 0)] == tuple(end_npcs[nid]["position"]):
                path_replay_exact += 1

        # ground exactness (positive gold only; -2/-4 mask empty cells)
        rep = out["ground"]
        ok = True
        for r in range(GRID):
            for c in range(GRID):
                a = rep[r][c]
                b = eg[r][c]
                if (a if a > 0 else 0) != (b if b > 0 else 0):
                    ok = False
                    break
            if not ok:
                break
        ground_replay_exact += 1 if ok else 0

        for cell, cls, amount, _actor in out["pickups"]:
            pickup_by_cell_class.setdefault(cell, Counter())[cls] += amount

        for pid in (1, 2):
            ent = [out["entered"][("p", pid, j)] for j in (0, 1)]
            entered_by_seat[pid].append(ent)
            occ = []
            for j in (0, 1):
                cells = [start_positions[("p", pid, j)]] + ent[j]
                occ.append(cells)
            occupied_by_seat[pid].append(occ)
            start_pos_by_seat[pid].append(
                [start_positions[("p", pid, j)] for j in (0, 1)])
            end_pos_by_seat[pid].append(
                [tuple(end_players[pid]["units"][j]["position"]) for j in (0, 1)])
            income_by_seat[pid].append([
                int(end_players[pid]["units"][j]["gold"])
                - int(start_players[pid]["units"][j]["gold"])
                for j in (0, 1)
            ])
            vision_spent_by_seat[pid].append(int(end_players[pid]["vision_spent"]))
            acts = player_plans[pid][1]
            action_count_by_seat[pid].append([len(acts[0]), len(acts[1])])
            real_moves_by_seat[pid].append(
                [sum(1 for a in acts[j] if a != STAY) for j in (0, 1)])
        npc_entered.append([
            cell for nid in npc_plans for cell in out["entered"][("n", nid, 0)]
        ])

    # ---- generation flow per cell (stock/flow separation input)
    generated = [[0] * GRID for _ in range(GRID)]
    generation_events: list[tuple[int, tuple[int, int], int]] = []
    for r in range(GRID):
        for c in range(GRID):
            v = ground_start[0][r][c]
            if v > 0:
                generated[r][c] += v
                generation_events.append((0, (r, c), v))
    monotonicity_violations = 0
    for t in range(ROUNDS - 1):
        eg = ground_end[t]
        sg = ground_start[t + 1]
        for r in range(GRID):
            row_e = eg[r]
            row_s = sg[r]
            for c in range(GRID):
                a = row_e[c]
                b = row_s[c]
                a = a if a > 0 else 0
                b = b if b > 0 else 0
                if b > a:
                    generated[r][c] += b - a
                    generation_events.append((t + 1, (r, c), b - a))
                elif b < a:
                    monotonicity_violations += 1

    per_seat: dict[str, Any] = {}
    for pid in (1, 2):
        per_seat[str(pid)] = seat_features(
            pid=pid,
            faster_seat=faster_seat,
            names=parsed["names"],
            walls=walls,
            hotspots=hotspots,
            ground_start=ground_start,
            ground_end=ground_end,
            generated=generated,
            generation_events=generation_events,
            pickup_by_cell_class=pickup_by_cell_class,
            entered=entered_by_seat[pid],
            occupied=occupied_by_seat[pid],
            start_pos=start_pos_by_seat[pid],
            end_pos=end_pos_by_seat[pid],
            income=income_by_seat[pid],
            vision_spent=vision_spent_by_seat[pid],
            real_moves=real_moves_by_seat[pid],
            action_count=action_count_by_seat[pid],
            snapshots=snapshots,
            seed_material=[batch, game_index, pid],
            permutations=permutations,
        )

    return {
        "batch": batch,
        "game_index": game_index,
        "log_file": path.name,
        "scenario_digest": scenario_digest,
        "fixed_costs": list(fixed_costs),
        "faster_seat": faster_seat,
        "names": parsed["names"],
        "validation": {
            "path_replay_exact": path_replay_exact,
            "path_replay_total": path_replay_total,
            "ground_replay_exact": ground_replay_exact,
            "ground_replay_total": ROUNDS,
            "ground_monotonicity_violations": monotonicity_violations,
        },
        "environment": environment_features(
            walls, hotspots, ground_start, ground_end, generation_events),
        "seats": per_seat,
    }


def seat_features(
    *, pid: int, faster_seat: int, names: Mapping[str, Any],
    walls: set[tuple[int, int]], hotspots: set[tuple[int, int]],
    ground_start: list[list[list[int]]], ground_end: list[list[list[int]]],
    generated: list[list[int]], generation_events: Sequence[tuple[int, tuple[int, int], int]],
    pickup_by_cell_class: Mapping[tuple[int, int], Counter],
    entered: list[list[list[tuple[int, int]]]],
    occupied: list[list[list[tuple[int, int]]]],
    start_pos: list[list[tuple[int, int]]],
    end_pos: list[list[tuple[int, int]]],
    income: list[list[int]],
    vision_spent: list[int],
    real_moves: list[list[int]],
    action_count: list[list[int]],
    snapshots: Sequence[tuple[int, Mapping[int, Mapping[str, int]]]],
    seed_material: Any, permutations: int,
) -> dict[str, Any]:
    strategy = names.get("player%d" % pid, "player%d" % pid)
    is_faster = (pid == faster_seat)

    # ---------------- Part A -------------------------------------------------
    d_series = [[ring(end_pos[t][j]) for t in range(ROUNDS)] for j in (0, 1)]
    inc_series = [[income[t][j] for t in range(ROUNDS)] for j in (0, 1)]

    part_a: dict[str, Any] = {}
    part_a["partition"] = partition_metrics(
        [end_pos[t][0] for t in range(ROUNDS)],
        [end_pos[t][1] for t in range(ROUNDS)],
        seed_material, permutations,
    )
    part_a["periodicity"] = {
        "ring_distance": [
            {"acf": gap_aware_acf(d_series[j]), "ls": lomb_scargle(d_series[j])}
            for j in (0, 1)
        ],
        "unit_income": [
            {"acf": gap_aware_acf(inc_series[j]), "ls": lomb_scargle(inc_series[j])}
            for j in (0, 1)
        ],
        "player_income_sum": {
            "acf": gap_aware_acf([inc_series[0][t] + inc_series[1][t]
                                  for t in range(ROUNDS)]),
            "ls": lomb_scargle([inc_series[0][t] + inc_series[1][t]
                                for t in range(ROUNDS)]),
        },
    }
    ring_share = []
    for j in (0, 1):
        counts = Counter(d_series[j])
        ring_share.append([counts.get(d, 0) / ROUNDS for d in range(9)])
    pooled = Counter(d for j in (0, 1) for d in d_series[j])
    part_a["amplitude"] = {
        "ring_share_by_unit": ring_share,
        "ring_share_pooled": [pooled.get(d, 0) / (2 * ROUNDS) for d in range(9)],
        "mean_ring_pooled": math.fsum(d * pooled.get(d, 0) for d in range(9))
                            / (2 * ROUNDS),
        "modal_ring_pooled": max(range(9), key=lambda d: (pooled.get(d, 0), -d)),
        "outer_share_pooled": sum(pooled.get(d, 0) for d in range(5, 9))
                              / (2 * ROUNDS),
        "excursions": [turning_point_excursions(d_series[j]) for j in (0, 1)],
    }
    part_a["phase"] = {
        "pearson_d0_d1": pearson(d_series[0], d_series[1]),
        "cross_correlation_d0_d1": cross_correlation(d_series[0], d_series[1]),
    }

    # ---------------- Part B -------------------------------------------------
    entered_counts: Counter = Counter()
    occupied_counts: Counter = Counter()
    entered_rounds: dict[tuple[int, int], list[int]] = {}
    cell_round_entered = {name: 0 for name in STRATA}
    distinct_per_unit_round: list[int] = []
    distinct_per_player_round: list[int] = []
    three_action_hist: Counter = Counter()
    three_real_move_hist: Counter = Counter()
    for t in range(ROUNDS):
        round_cells_by_stratum = {name: set() for name in STRATA}
        player_round_cells: set[tuple[int, int]] = set()
        for j in (0, 1):
            cells = entered[t][j]
            uniq = set(cells)
            distinct_per_unit_round.append(len(uniq))
            if action_count[t][j] == 3:
                three_action_hist[len(uniq)] += 1
            if real_moves[t][j] == 3:
                three_real_move_hist[len(uniq)] += 1
            player_round_cells |= uniq
            for cell in cells:
                entered_counts[cell] += 1
                entered_rounds.setdefault(cell, []).append(t)
            for cell in occupied[t][j]:
                occupied_counts[cell] += 1
            for cell in uniq:
                name = STRATUM_CENTRAL if RING_OF[cell[0]][cell[1]] <= 4 else STRATUM_OUTER
                round_cells_by_stratum[name].add(cell)
        distinct_per_player_round.append(len(player_round_cells))
        for name, cells_set in round_cells_by_stratum.items():
            cell_round_entered[name] += len(cells_set)

    # targetable (selector 5x5) and actual-vision unions
    radius_series = vision_radius_series(vision_spent)
    targetable_count_per_round = {name: [] for name in STRATA}
    visible_count_per_round = {name: [] for name in STRATA}
    never_targetable_tracker = [[True] * GRID for _ in range(GRID)]
    nontargetable_run = [[0] * GRID for _ in range(GRID)]
    nontargetable_run_max = [[0] * GRID for _ in range(GRID)]
    nontargetable_total = [[0] * GRID for _ in range(GRID)]
    for t in range(ROUNDS):
        seen = set()
        for j in (0, 1):
            seen.update(window(end_pos[t][j], SELECTOR_RADIUS))
        vis = set()
        for j in (0, 1):
            vis.update(window(end_pos[t][j], radius_series[t]))
        counts = {name: 0 for name in STRATA}
        vcounts = {name: 0 for name in STRATA}
        for cell in seen:
            name = STRATUM_CENTRAL if RING_OF[cell[0]][cell[1]] <= 4 else STRATUM_OUTER
            counts[name] += 1
            never_targetable_tracker[cell[0]][cell[1]] = False
        for cell in vis:
            name = STRATUM_CENTRAL if RING_OF[cell[0]][cell[1]] <= 4 else STRATUM_OUTER
            vcounts[name] += 1
        for name in STRATA:
            targetable_count_per_round[name].append(counts[name])
            visible_count_per_round[name].append(vcounts[name])
        for r in range(GRID):
            for c in range(GRID):
                if (r, c) in seen:
                    nontargetable_run[r][c] = 0
                else:
                    nontargetable_run[r][c] += 1
                    nontargetable_total[r][c] += 1
                    if nontargetable_run[r][c] > nontargetable_run_max[r][c]:
                        nontargetable_run_max[r][c] = nontargetable_run[r][c]

    ours = "p%d" % pid
    theirs = "p%d" % (3 - pid)
    coverage: dict[str, Any] = {}
    for name, cells in STRATA.items():
        traversable = [cell for cell in cells if cell not in walls]
        touched = [cell for cell in traversable if entered_counts.get(cell, 0) > 0]
        untouched = [cell for cell in traversable if entered_counts.get(cell, 0) == 0]
        occupied_only = [cell for cell in traversable
                         if occupied_counts.get(cell, 0) > 0]
        naive = 0
        for t in range(ROUNDS):
            sg = ground_start[t]
            for cell in untouched:
                v = sg[cell[0]][cell[1]]
                if v > 0:
                    naive += v
        flow = sum(generated[cell[0]][cell[1]] for cell in untouched)
        eaten_npc = 0
        eaten_opp = 0
        eaten_ours = 0
        for cell in untouched:
            counter = pickup_by_cell_class.get(cell)
            if counter:
                eaten_npc += counter.get("npc", 0)
                eaten_opp += counter.get(theirs, 0)
                eaten_ours += counter.get(ours, 0)
        remaining = flow - eaten_npc - eaten_opp - eaten_ours
        total_flow_stratum = sum(generated[cell[0]][cell[1]] for cell in traversable)
        naive_all = 0
        for t in range(ROUNDS):
            sg = ground_start[t]
            for cell in traversable:
                v = sg[cell[0]][cell[1]]
                if v > 0:
                    naive_all += v
        hist = Counter(entered_counts.get(cell, 0) for cell in cells)
        never_target = [cell for cell in traversable
                        if never_targetable_tracker[cell[0]][cell[1]]]
        coverage[name] = {
            "cells_total": len(cells),
            "cells_traversable": len(traversable),
            "walls": len(cells) - len(traversable),
            "distinct_entered": len(touched),
            "distinct_entered_share_of_traversable": len(touched) / len(traversable),
            "distinct_occupied": len(occupied_only),
            "untouched_traversable": len(untouched),
            "cell_round_coverage_fraction_all_cells":
                cell_round_entered[name] / (len(cells) * ROUNDS),
            "cell_round_coverage_fraction_traversable":
                cell_round_entered[name] / (len(traversable) * ROUNDS),
            "visit_count_histogram": {str(k): v for k, v in sorted(hist.items())},
            "never_targetable_cells": len(never_target),
            "never_targetable_share_of_traversable": len(never_target) / len(traversable),
            "targetable_per_round": mean_se(targetable_count_per_round[name]),
            "targetable_share_per_round": mean_se(
                [v / len(cells) for v in targetable_count_per_round[name]]),
            "visible_per_round": mean_se(visible_count_per_round[name]),
            "nontargetable_rounds_per_cell": mean_se(
                [nontargetable_total[cell[0]][cell[1]] for cell in traversable]),
            "longest_nontargetable_run_per_cell": mean_se(
                [nontargetable_run_max[cell[0]][cell[1]] for cell in traversable]),
            "gold": {
                "naive_per_round_stock_sum_on_untouched": naive,
                "naive_per_round_stock_sum_all_traversable": naive_all,
                "flow_generated_on_untouched": flow,
                "flow_generated_all_traversable": total_flow_stratum,
                "stock_flow_discount_share": (1.0 - flow / naive) if naive > 0 else None,
                "contested_eaten_by_npc_on_untouched": eaten_npc,
                "contested_eaten_by_opponent_on_untouched": eaten_opp,
                "eaten_by_us_on_untouched_should_be_zero": eaten_ours,
                "uncontested_remaining_on_untouched": remaining,
            },
        }

    # regeneration timing after our own departures
    regen = regeneration_timing(entered_rounds, ground_start, walls)

    return {
        "strategy": strategy,
        "seat": pid,
        "arm": "first_mover" if is_faster else "second_mover",
        "vision_spent_final": vision_spent[-1],
        "gross_gold_units": [sum(income[t][j] for t in range(ROUNDS)) for j in (0, 1)],
        "part_a": part_a,
        "part_b": {
            "coverage": coverage,
            "distinct_cells_per_unit_round": mean_se(distinct_per_unit_round),
            "distinct_cells_per_player_round": mean_se(distinct_per_player_round),
            "distinct_entered_hist_when_3_actions":
                {str(k): v for k, v in sorted(three_action_hist.items())},
            "distinct_entered_hist_when_3_real_moves":
                {str(k): v for k, v in sorted(three_real_move_hist.items())},
            "regeneration": regen,
            "snapshot": snapshot_metrics(snapshots, ground_start, end_pos),
        },
        "series": {
            "d0": "".join(str(v) for v in d_series[0]),
            "d1": "".join(str(v) for v in d_series[1]),
            "income0": ",".join(str(v) for v in inc_series[0]),
            "income1": ",".join(str(v) for v in inc_series[1]),
        },
    }


def vision_radius_series(vision_spent: Sequence[int]) -> list[int]:
    """Effective vision radius per round, decoded from cumulative spend.

    A purchase in round r-1 costs ``VP_PRICES[vp]`` and takes effect in round r
    for exactly one round (``sim/README.md`` section 4).
    """
    radii = [VP_RADII[0]] * len(vision_spent)
    previous = 0
    for t, total in enumerate(vision_spent):
        delta = total - previous
        previous = total
        if t + 1 < len(radii):
            if delta == VP_PRICES[1]:
                radii[t + 1] = VP_RADII[1]
            elif delta == VP_PRICES[2]:
                radii[t + 1] = VP_RADII[2]
    return radii


def regeneration_timing(
    entered_rounds: Mapping[tuple[int, int], Sequence[int]],
    ground_start: Sequence[Sequence[Sequence[int]]],
    walls: set[tuple[int, int]],
) -> dict[str, Any]:
    """For each departure from a cell, how long until the cell is worth returning
    for -- i.e. the first later round whose *start* stock reaches a threshold.

    Right-censored departures (threshold never reached before round 500) are
    reported separately; medians use the observed-only subset and are therefore
    optimistic, which is stated in the output.
    """
    thresholds = (1, 3, 5, 7, 10)
    hits: dict[int, dict[str, list[int]]] = {
        v: {STRATUM_CENTRAL: [], STRATUM_OUTER: []} for v in thresholds
    }
    censored: dict[int, dict[str, int]] = {
        v: {STRATUM_CENTRAL: 0, STRATUM_OUTER: 0} for v in thresholds
    }
    by_ring: dict[int, dict[int, list[int]]] = {v: {} for v in thresholds}
    departures = {STRATUM_CENTRAL: 0, STRATUM_OUTER: 0}
    for cell, rounds_list in entered_rounds.items():
        if cell in walls:
            continue
        name = STRATUM_CENTRAL if RING_OF[cell[0]][cell[1]] <= 4 else STRATUM_OUTER
        d = RING_OF[cell[0]][cell[1]]
        last = max(rounds_list)
        departures[name] += 1
        for v in thresholds:
            wait = None
            for t in range(last + 1, ROUNDS):
                if ground_start[t][cell[0]][cell[1]] >= v:
                    wait = t - last
                    break
            if wait is None:
                censored[v][name] += 1
            else:
                hits[v][name].append(wait)
                by_ring[v].setdefault(d, []).append(wait)
    return {
        "definition": ("wait, in rounds, from the last round our unit entered the "
                       "cell to the first later round whose start-phase stock is "
                       ">= threshold; right-censored at round 499"),
        "departure_cells": departures,
        "wait_by_threshold": {
            str(v): {
                name: {**mean_se(hits[v][name]),
                       "censored": censored[v][name],
                       "censored_share": censored[v][name] /
                                         max(1, censored[v][name] + len(hits[v][name]))}
                for name in (STRATUM_CENTRAL, STRATUM_OUTER)
            }
            for v in thresholds
        },
        "wait_by_ring_threshold_3": {
            str(d): mean_se(vals) for d, vals in sorted(by_ring[3].items())
        },
    }


def environment_features(
    walls: set[tuple[int, int]], hotspots: set[tuple[int, int]],
    ground_start: Sequence[Sequence[Sequence[int]]],
    ground_end: Sequence[Sequence[Sequence[int]]],
    generation_events: Sequence[tuple[int, tuple[int, int], int]],
) -> dict[str, Any]:
    """Generator-side facts measured from this game, for cross-checking the
    repo's calibration numbers rather than trusting them."""
    central_cells_per_round: Counter = Counter()
    central_value = 0
    outer_rounds: set[int] = set()
    outer_event_cells: Counter = Counter()
    outer_event_value: Counter = Counter()
    outer_hotspot_cells = 0
    outer_cells_total = 0
    per_cell_events = [[0] * GRID for _ in range(GRID)]
    per_cell_value = [[0] * GRID for _ in range(GRID)]
    for t, cell, value in generation_events:
        per_cell_events[cell[0]][cell[1]] += 1
        per_cell_value[cell[0]][cell[1]] += value
        if RING_OF[cell[0]][cell[1]] <= 4:
            if t > 0:
                central_cells_per_round[t] += 1
                central_value += value
        else:
            if t > 0:
                outer_rounds.add(t)
                outer_event_cells[t] += 1
                outer_event_value[t] += value
                outer_cells_total += 1
                if cell in hotspots:
                    outer_hotspot_cells += 1
    counts = [central_cells_per_round.get(t, 0) for t in range(1, ROUNDS)]
    intervals = []
    ordered = sorted(outer_rounds)
    for a, b in zip(ordered, ordered[1:]):
        intervals.append(b - a)
    ring_rate: dict[str, dict[str, float]] = {}
    for d in range(9):
        cells = [(r, c) for r in range(GRID) for c in range(GRID)
                 if RING_OF[r][c] == d and (r, c) not in walls]
        if not cells:
            continue
        ev = sum(per_cell_events[r][c] for r, c in cells)
        va = sum(per_cell_value[r][c] for r, c in cells)
        ring_rate[str(d)] = {
            "traversable_cells": len(cells),
            "events_per_cell_per_game": ev / len(cells),
            "gold_per_cell_per_game": va / len(cells),
        }
    lifetimes = gold_block_lifetimes(ground_start, ground_end, generation_events)
    return {
        "central_generation_cells_per_round": mean_se(counts),
        "central_generation_gold_per_game": central_value,
        "central_cells_per_round_histogram":
            {str(k): v for k, v in sorted(Counter(counts).items())},
        "outer_event_rounds": len(outer_rounds),
        "outer_event_interval": mean_se(intervals),
        "outer_event_interval_histogram":
            {str(k): v for k, v in sorted(Counter(intervals).items())},
        "outer_event_cells": mean_se(list(outer_event_cells.values())),
        "outer_event_gold": mean_se(list(outer_event_value.values())),
        "outer_hotspot_share_of_outer_landings":
            outer_hotspot_cells / outer_cells_total if outer_cells_total else None,
        "generation_rate_by_ring": ring_rate,
        "gold_block_lifetime": lifetimes,
        "bomb_wave_rounds_detected": detect_bomb_waves(ground_start, ground_end),
    }


def gold_block_lifetimes(
    ground_start: Sequence[Sequence[Sequence[int]]],
    ground_end: Sequence[Sequence[Sequence[int]]],
    generation_events: Sequence[tuple[int, tuple[int, int], int]],
) -> dict[str, Any]:
    """Rounds a freshly created block survives before the cell returns to zero.

    Only *clean* births are used: the cell's stock was zero immediately before
    the deposit, so the lifetime belongs to one block.  Right-censored blocks
    (still alive at round 499) are reported separately.
    """
    births: dict[tuple[int, int], list[int]] = {}
    for t, cell, _value in generation_events:
        if t == 0:
            continue
        prior = ground_end[t - 1][cell[0]][cell[1]]
        if (prior if prior > 0 else 0) == 0:
            births.setdefault(cell, []).append(t)
    out: dict[str, list[int]] = {STRATUM_CENTRAL: [], STRATUM_OUTER: []}
    censored = {STRATUM_CENTRAL: 0, STRATUM_OUTER: 0}
    for cell, times in births.items():
        name = STRATUM_CENTRAL if RING_OF[cell[0]][cell[1]] <= 4 else STRATUM_OUTER
        for t in times:
            death = None
            for u in range(t, ROUNDS):
                v = ground_end[u][cell[0]][cell[1]]
                if (v if v > 0 else 0) == 0:
                    death = u
                    break
            if death is None:
                censored[name] += 1
            else:
                out[name].append(death - t + 1)
    return {
        "definition": ("rounds from a clean birth (stock 0 immediately before the "
                       "deposit) to the first round whose end-phase stock is 0, "
                       "inclusive; right-censored at 499"),
        **{name: {**mean_se(out[name]), "censored": censored[name]}
           for name in (STRATUM_CENTRAL, STRATUM_OUTER)},
    }


def detect_bomb_waves(
    ground_start: Sequence[Sequence[Sequence[int]]],
    ground_end: Sequence[Sequence[Sequence[int]]],
) -> dict[str, Any]:
    """Rounds where the bomb set changes by more than actor consumption."""
    waves = []
    for t in range(1, ROUNDS):
        prev = {(r, c) for r in range(GRID) for c in range(GRID)
                if ground_end[t - 1][r][c] == BOMB}
        cur = {(r, c) for r in range(GRID) for c in range(GRID)
               if ground_start[t][r][c] == BOMB}
        if cur - prev:
            waves.append(t)
    mods = Counter(t % BOMB_PERIOD for t in waves)
    return {"rounds": waves, "count": len(waves),
            "modulo_bomb_period_histogram": {str(k): v for k, v in sorted(mods.items())},
            "all_on_period": all(t % BOMB_PERIOD == 0 for t in waves)}


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #

def aggregate_group(seats: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fold per-game seat records into per-arm means with SEs."""
    n = len(seats)
    out: dict[str, Any] = {"games": n}

    # ---- Part A: partition
    out["partition"] = {
        "observed_overlap": mean_se([s["part_a"]["partition"]["observed"]["overlap"]
                                     for s in seats]),
        "null_overlap": mean_se([
            s["part_a"]["partition"]["null_unit_label_permutation"]["overlap"]["mean"]
            for s in seats]),
        "observed_centroid_separation": mean_se([
            s["part_a"]["partition"]["observed"]["centroid_separation"] for s in seats]),
        "null_centroid_separation": mean_se([
            s["part_a"]["partition"]["null_unit_label_permutation"]
             ["centroid_separation"]["mean"] for s in seats]),
        "observed_axis_accuracy": mean_se([
            s["part_a"]["partition"]["observed"]["axis"]["accuracy"] for s in seats]),
        "null_axis_accuracy": mean_se([
            s["part_a"]["partition"]["null_unit_label_permutation"]
             ["axis_accuracy"]["mean"] for s in seats]),
        "best_axis_histogram": dict(Counter(
            "%s@%s/%s" % (s["part_a"]["partition"]["observed"]["axis"]["axis"],
                          s["part_a"]["partition"]["observed"]["axis"]["threshold"],
                          s["part_a"]["partition"]["observed"]["axis"]["orientation"])
            for s in seats)),
        "permutation_p_values": {
            "overlap_le_observed": mean_se([
                s["part_a"]["partition"]["null_unit_label_permutation"]
                 ["p_overlap_le_observed"] for s in seats]),
            "separation_ge_observed": mean_se([
                s["part_a"]["partition"]["null_unit_label_permutation"]
                 ["p_separation_ge_observed"] for s in seats]),
            "accuracy_ge_observed": mean_se([
                s["part_a"]["partition"]["null_unit_label_permutation"]
                 ["p_accuracy_ge_observed"] for s in seats]),
        },
    }

    # ---- Part A: periodicity
    def fold_series(kind: str, index: int | None) -> dict[str, Any]:
        acfs: dict[int, list[float]] = {}
        peaks: list[float] = []
        peak_periods: list[float] = []
        band_peaks: list[float] = []
        band_periods: list[float] = []
        bomb_power: list[float] = []
        outer_power: dict[int, list[float]] = {}
        degenerate = 0
        for s in seats:
            node = s["part_a"]["periodicity"][kind]
            node = node[index] if index is not None else node
            acf = node["acf"]
            if acf.get("degenerate"):
                degenerate += 1
            else:
                for lag, item in acf["acf"].items():
                    acfs.setdefault(int(lag), []).append(item["r"])
            ls = node["ls"]
            if not ls.get("degenerate"):
                peaks.append(ls["peak"]["power"])
                peak_periods.append(ls["peak"]["period"])
                band_peaks.append(ls["band_peak"]["power"])
                band_periods.append(ls["band_peak"]["period"])
                if ls.get("power_at_bomb_period") is not None:
                    bomb_power.append(ls["power_at_bomb_period"])
                for p, v in ls.get("power_in_outer_band", {}).items():
                    outer_power.setdefault(int(p), []).append(v)
        significant = [
            lag for lag, vals in sorted(acfs.items())
            if abs(mean_se(vals)["mean"]) > 3.0 * (mean_se(vals)["se"] or 1e9)
        ]
        return {
            "degenerate_series": degenerate,
            "acf_by_lag": {str(lag): mean_se(vals) for lag, vals in sorted(acfs.items())},
            "acf_lags_beyond_3se": significant,
            "acf_max_abs_lag_3_to_60": max(
                ((abs(mean_se(vals)["mean"]), lag) for lag, vals in acfs.items()
                 if 3 <= lag <= 60), default=(None, None)),
            "ls_peak_power_full_range": mean_se(peaks),
            "ls_peak_period_full_range": mean_se(peak_periods),
            "ls_peak_period_histogram": {
                str(k): v for k, v in sorted(Counter(
                    int(round(p)) for p in peak_periods).items())},
            "ls_band_peak_power_4_40": mean_se(band_peaks),
            "ls_band_peak_period_4_40": mean_se(band_periods),
            "ls_band_peak_period_histogram": {
                str(k): v for k, v in sorted(Counter(
                    int(round(p)) for p in band_periods).items())},
            "ls_power_at_period_20": mean_se(bomb_power),
            "ls_power_in_outer_band_8_16": {
                str(p): mean_se(vals) for p, vals in sorted(outer_power.items())},
        }

    out["periodicity"] = {
        "ring_distance_unit0": fold_series("ring_distance", 0),
        "ring_distance_unit1": fold_series("ring_distance", 1),
        "unit_income_unit0": fold_series("unit_income", 0),
        "unit_income_unit1": fold_series("unit_income", 1),
        "player_income_sum": fold_series("player_income_sum", None),
    }

    # ---- Part A: amplitude
    ring_pooled = [
        mean_se([s["part_a"]["amplitude"]["ring_share_pooled"][d] for s in seats])
        for d in range(9)
    ]
    out["amplitude"] = {
        "ring_share_pooled_by_d": {str(d): ring_pooled[d] for d in range(9)},
        "ring_share_unit0_by_d": {
            str(d): mean_se([s["part_a"]["amplitude"]["ring_share_by_unit"][0][d]
                             for s in seats]) for d in range(9)},
        "ring_share_unit1_by_d": {
            str(d): mean_se([s["part_a"]["amplitude"]["ring_share_by_unit"][1][d]
                             for s in seats]) for d in range(9)},
        "mean_ring_pooled": mean_se([s["part_a"]["amplitude"]["mean_ring_pooled"]
                                     for s in seats]),
        "outer_share_pooled": mean_se([s["part_a"]["amplitude"]["outer_share_pooled"]
                                       for s in seats]),
        "modal_ring_histogram": dict(Counter(
            s["part_a"]["amplitude"]["modal_ring_pooled"] for s in seats)),
        "excursion_peak_histogram": dict(Counter(
            int(p) for s in seats
            for j in (0, 1)
            for p in s["part_a"]["amplitude"]["excursions"][j]["peaks"])),
        "excursion_peak_mean": mean_se([
            p for s in seats for j in (0, 1)
            for p in s["part_a"]["amplitude"]["excursions"][j]["peaks"]]),
        "excursion_duration_mean": mean_se([
            p for s in seats for j in (0, 1)
            for p in s["part_a"]["amplitude"]["excursions"][j]["durations"]]),
        "excursions_per_game": mean_se([
            sum(s["part_a"]["amplitude"]["excursions"][j]["n"] for j in (0, 1))
            for s in seats]),
    }

    # ---- Part A: phase
    xcorr: dict[int, list[float]] = {}
    for s in seats:
        for lag, v in s["part_a"]["phase"]["cross_correlation_d0_d1"].items():
            xcorr.setdefault(int(lag), []).append(v)
    out["phase"] = {
        "pearson_d0_d1": mean_se([s["part_a"]["phase"]["pearson_d0_d1"]
                                  for s in seats
                                  if s["part_a"]["phase"]["pearson_d0_d1"] is not None]),
        "cross_correlation_by_lag": {
            str(lag): mean_se(vals) for lag, vals in sorted(xcorr.items())},
    }

    # ---- Part B
    coverage: dict[str, Any] = {}
    for name in STRATA:
        node = [s["part_b"]["coverage"][name] for s in seats]
        hist_total: Counter = Counter()
        for item in node:
            for k, v in item["visit_count_histogram"].items():
                hist_total[int(k)] += v
        coverage[name] = {
            "cells_total": node[0]["cells_total"],
            "cells_traversable": node[0]["cells_traversable"],
            "walls": node[0]["walls"],
            "distinct_entered": mean_se([x["distinct_entered"] for x in node]),
            "distinct_entered_share_of_traversable": mean_se(
                [x["distinct_entered_share_of_traversable"] for x in node]),
            "distinct_occupied": mean_se([x["distinct_occupied"] for x in node]),
            "untouched_traversable": mean_se([x["untouched_traversable"] for x in node]),
            "cell_round_coverage_fraction_all_cells": mean_se(
                [x["cell_round_coverage_fraction_all_cells"] for x in node]),
            "cell_round_coverage_fraction_traversable": mean_se(
                [x["cell_round_coverage_fraction_traversable"] for x in node]),
            "visit_count_histogram_summed_over_games":
                {str(k): v for k, v in sorted(hist_total.items())},
            "visit_count_histogram_mean_per_game":
                {str(k): v / n for k, v in sorted(hist_total.items())},
            "never_targetable_cells": mean_se([x["never_targetable_cells"] for x in node]),
            "never_targetable_share_of_traversable": mean_se(
                [x["never_targetable_share_of_traversable"] for x in node]),
            "targetable_per_round": mean_se([x["targetable_per_round"]["mean"]
                                             for x in node]),
            "targetable_share_per_round": mean_se([x["targetable_share_per_round"]["mean"]
                                                   for x in node]),
            "visible_per_round": mean_se([x["visible_per_round"]["mean"] for x in node]),
            "nontargetable_rounds_per_cell": mean_se(
                [x["nontargetable_rounds_per_cell"]["mean"] for x in node]),
            "nontargetable_rounds_per_cell_p90_of_cells": mean_se(
                [x["nontargetable_rounds_per_cell"]["p90"] for x in node]),
            "longest_nontargetable_run_per_cell": mean_se(
                [x["longest_nontargetable_run_per_cell"]["mean"] for x in node]),
            "longest_nontargetable_run_max_over_cells": mean_se(
                [x["longest_nontargetable_run_per_cell"]["max"] for x in node]),
            "gold": {
                key: mean_se([x["gold"][key] for x in node
                              if x["gold"][key] is not None])
                for key in node[0]["gold"]
            },
        }
    out["coverage"] = coverage
    out["distinct_cells_per_unit_round"] = mean_se(
        [s["part_b"]["distinct_cells_per_unit_round"]["mean"] for s in seats])
    out["distinct_cells_per_player_round"] = mean_se(
        [s["part_b"]["distinct_cells_per_player_round"]["mean"] for s in seats])

    for key in ("distinct_entered_hist_when_3_actions",
                "distinct_entered_hist_when_3_real_moves"):
        total: Counter = Counter()
        for s in seats:
            for k, v in s["part_b"][key].items():
                total[int(k)] += v
        denom = sum(total.values()) or 1
        out[key] = {
            "counts": {str(k): v for k, v in sorted(total.items())},
            "shares": {str(k): v / denom for k, v in sorted(total.items())},
            "unit_rounds": denom,
        }

    regen: dict[str, Any] = {}
    thresholds = sorted(seats[0]["part_b"]["regeneration"]["wait_by_threshold"].keys(),
                        key=int)
    for v in thresholds:
        regen[v] = {}
        for name in (STRATUM_CENTRAL, STRATUM_OUTER):
            nodes = [s["part_b"]["regeneration"]["wait_by_threshold"][v][name]
                     for s in seats]
            regen[v][name] = {
                "median_of_game_medians": mean_se([x["median"] for x in nodes
                                                   if x["median"] is not None]),
                "mean_of_game_means": mean_se([x["mean"] for x in nodes
                                               if x["mean"] is not None]),
                "p90_of_game_p90s": mean_se([x["p90"] for x in nodes
                                             if x["p90"] is not None]),
                "censored_share": mean_se([x["censored_share"] for x in nodes]),
                "departure_events_per_game": mean_se([x["n"] + x["censored"]
                                                      for x in nodes]),
            }
    ring_regen: dict[str, Any] = {}
    for s in seats:
        for d, item in s["part_b"]["regeneration"]["wait_by_ring_threshold_3"].items():
            ring_regen.setdefault(d, []).append(item)
    out["regeneration"] = {
        "by_threshold": regen,
        "by_ring_threshold_3": {
            d: {"median_of_game_medians": mean_se([x["median"] for x in items
                                                   if x["median"] is not None]),
                "mean_of_game_means": mean_se([x["mean"] for x in items
                                               if x["mean"] is not None])}
            for d, items in sorted(ring_regen.items(), key=lambda kv: int(kv[0]))
        },
    }
    out["gross_gold_per_game"] = mean_se([sum(s["gross_gold_units"]) for s in seats])
    out["vision_spent_final"] = mean_se([s["vision_spent_final"] for s in seats])

    # ---- B5: snapshot informativeness (pooled over games)
    snap = [s["part_b"]["snapshot"] for s in seats]
    offsets = sorted(snap[0]["outer_unit_rounds_in_argmax_arm"]["by_offset"].keys(),
                     key=int)

    def pool(getter) -> dict[str, Any]:
        hits = sum(getter(x)["hits"] for x in snap)
        total = sum(getter(x)["outer_unit_rounds"] for x in snap)
        share = (hits / total) if total else None
        se = (math.sqrt(share * (1 - share) / total)
              if share is not None and total > 0 else None)
        return {"hits": hits, "outer_unit_rounds": total, "share": share,
                "se_binomial": se,
                "z_vs_chance_0.25": ((share - 0.25) / se)
                                    if se and se > 0 else None}

    out["snapshot"] = {
        "snapshots_per_game": mean_se([x["snapshots"] for x in snap]),
        "snapshot_totals_match_decoded_ground": {
            "matched": sum(x["snapshot_totals_match_decoded_ground"] for x in snap),
            "total": sum(x["snapshots"] for x in snap),
        },
        "argmax_outer_arm_gold_remaining": mean_se(
            [x["argmax_outer_arm_gold_remaining"]["mean"] for x in snap]),
        "argmax_outer_arm_gold_remaining_median_of_games": mean_se(
            [x["argmax_outer_arm_gold_remaining"]["median"] for x in snap]),
        "total_gold_reported_by_snapshot": mean_se(
            [x["total_gold_reported_by_snapshot"]["mean"] for x in snap]),
        "gold_inside_our_two_5x5_windows": mean_se(
            [x["gold_inside_our_two_5x5_windows"]["mean"] for x in snap]),
        "gold_told_but_not_visible": mean_se(
            [x["gold_told_but_not_visible"]["mean"] for x in snap]),
        "gold_told_but_not_visible_outer_only": mean_se(
            [x["gold_told_but_not_visible_outer_only"]["mean"] for x in snap]),
        "argmax_arm_histogram": {
            str(k): sum(x["argmax_outer_arm_histogram"].get(str(k), 0) for x in snap)
            for k in (2, 3, 4, 5)},
        "outer_unit_rounds_in_argmax_arm": {
            "post_offsets_0_to_4": pool(
                lambda x: x["outer_unit_rounds_in_argmax_arm"]["post_offsets_0_to_4"]),
            "pre_offsets_minus8_to_minus4": pool(
                lambda x: x["outer_unit_rounds_in_argmax_arm"]
                           ["pre_offsets_minus8_to_minus4"]),
            "rich_arm_ge_40_offsets_0_to_4": pool(
                lambda x: x["outer_unit_rounds_in_argmax_arm"]
                           ["rich_arm_ge_40_offsets_0_to_4"]),
            "chance_level": 0.25,
            "by_offset": {
                o: pool(lambda x, o=o: x["outer_unit_rounds_in_argmax_arm"]
                                        ["by_offset"][o])
                for o in offsets
            },
        },
    }
    return out


def aggregate_environment(games: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    env = [g["environment"] for g in games]
    interval_hist: Counter = Counter()
    central_hist: Counter = Counter()
    bomb_mod: Counter = Counter()
    for e in env:
        for k, v in e["outer_event_interval_histogram"].items():
            interval_hist[int(k)] += v
        for k, v in e["central_cells_per_round_histogram"].items():
            central_hist[int(k)] += v
        for k, v in e["bomb_wave_rounds_detected"]["modulo_bomb_period_histogram"].items():
            bomb_mod[int(k)] += v
    ring: dict[str, Any] = {}
    for d in range(9):
        vals_e = [e["generation_rate_by_ring"][str(d)]["events_per_cell_per_game"]
                  for e in env if str(d) in e["generation_rate_by_ring"]]
        vals_g = [e["generation_rate_by_ring"][str(d)]["gold_per_cell_per_game"]
                  for e in env if str(d) in e["generation_rate_by_ring"]]
        if vals_e:
            ring[str(d)] = {"events_per_cell_per_game": mean_se(vals_e),
                            "gold_per_cell_per_game": mean_se(vals_g)}
    return {
        "games": len(env),
        "central_generation_cells_per_round": mean_se(
            [e["central_generation_cells_per_round"]["mean"] for e in env]),
        "central_cells_per_round_histogram_summed":
            {str(k): v for k, v in sorted(central_hist.items())},
        "central_generation_gold_per_game": mean_se(
            [e["central_generation_gold_per_game"] for e in env]),
        "outer_event_rounds_per_game": mean_se([e["outer_event_rounds"] for e in env]),
        "outer_event_interval": mean_se([e["outer_event_interval"]["mean"] for e in env]),
        "outer_event_interval_histogram_summed":
            {str(k): v for k, v in sorted(interval_hist.items())},
        "outer_event_interval_min_max": [
            min(interval_hist), max(interval_hist)] if interval_hist else None,
        "outer_event_cells": mean_se([e["outer_event_cells"]["mean"] for e in env]),
        "outer_event_gold": mean_se([e["outer_event_gold"]["mean"] for e in env]),
        "outer_hotspot_share_of_outer_landings": mean_se(
            [e["outer_hotspot_share_of_outer_landings"] for e in env
             if e["outer_hotspot_share_of_outer_landings"] is not None]),
        "generation_rate_by_ring": ring,
        "gold_block_lifetime": {
            name: {
                "median_of_game_medians": mean_se(
                    [e["gold_block_lifetime"][name]["median"] for e in env
                     if e["gold_block_lifetime"][name]["median"] is not None]),
                "mean_of_game_means": mean_se(
                    [e["gold_block_lifetime"][name]["mean"] for e in env
                     if e["gold_block_lifetime"][name]["mean"] is not None]),
                "p90_of_game_p90s": mean_se(
                    [e["gold_block_lifetime"][name]["p90"] for e in env
                     if e["gold_block_lifetime"][name]["p90"] is not None]),
                "blocks_per_game": mean_se([e["gold_block_lifetime"][name]["n"]
                                            for e in env]),
                "censored_per_game": mean_se([e["gold_block_lifetime"][name]["censored"]
                                              for e in env]),
            }
            for name in (STRATUM_CENTRAL, STRATUM_OUTER)
        },
        "bomb_waves_per_game": mean_se(
            [e["bomb_wave_rounds_detected"]["count"] for e in env]),
        "bomb_wave_modulo_20_histogram_summed":
            {str(k): v for k, v in sorted(bomb_mod.items())},
        "bomb_waves_all_on_period_20": all(
            e["bomb_wave_rounds_detected"]["all_on_period"] for e in env),
    }


def generator_intent_baseline(
    map_name: str, seeds: Sequence[int], maps_path: str | None = None
) -> dict[str, Any]:
    """Radial gradient and event structure straight out of the generator.

    ``ScenarioGenerator.resolve_all()`` called with no ``SpawnState`` resolves
    every round against an *empty* board, so nothing is rejected for occupancy.
    That separates two quantities the repo's prose conflates:

    * the **intent** gradient -- what the generator wants to place, and
    * the **realized** gradient -- what actually lands once seven NPCs, four
      player units, existing gold and bombs have occupied cells.

    ``sim/GENERATION.md`` section 3.3 already warns that its own observed
    gradient (single-cell frequency 78.2 at d=1 versus 22.8 at d=4) is an
    *under*-statement of the intent because all seven NPCs spawn on (8,8), and
    generation excludes occupied cells.  This function measures both ends of
    that inequality rather than quoting either.
    """
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from sim.scenario import (            # noqa: PLC0415
            BOMB_PERIOD as SC_BOMB_PERIOD,
            CENTRAL_POISSON_MEAN,
            ScenarioGenerator,
        )
    except Exception as exc:                  # pragma: no cover
        return {"available": False, "error": repr(exc)}

    per_cell_events = [[0] * GRID for _ in range(GRID)]
    per_cell_value = [[0] * GRID for _ in range(GRID)]
    central_counts: list[int] = []
    outer_intervals: list[int] = []
    outer_rounds_per_game: list[int] = []
    outer_cells_per_event: list[int] = []
    outer_gold_per_event: list[int] = []
    bomb_rounds_all_on_period = True
    bomb_rounds_per_game: list[int] = []
    walls: set[tuple[int, int]] = set()
    for seed in seeds:
        gen = ScenarioGenerator(map_name, seed)
        walls = set(gen.map.walls)
        events = gen.resolve_all()
        outer_rounds: list[int] = []
        bomb_rounds: list[int] = []
        for rec in events:
            central = 0
            outer_cells = 0
            outer_gold = 0
            for add in rec.gold_additions:
                cell = (add.row, add.col)
                per_cell_events[cell[0]][cell[1]] += 1
                per_cell_value[cell[0]][cell[1]] += add.value
                if RING_OF[cell[0]][cell[1]] <= 4:
                    central += 1
                else:
                    outer_cells += 1
                    outer_gold += add.value
            if rec.round > 0:
                central_counts.append(central)
            if outer_cells:
                outer_rounds.append(rec.round)
                outer_cells_per_event.append(outer_cells)
                outer_gold_per_event.append(outer_gold)
            if rec.bomb_refresh is not None:
                bomb_rounds.append(rec.round)
        outer_rounds_per_game.append(len(outer_rounds))
        bomb_rounds_per_game.append(len(bomb_rounds))
        for a, b in zip(outer_rounds, outer_rounds[1:]):
            outer_intervals.append(b - a)
        if any(r % SC_BOMB_PERIOD for r in bomb_rounds):
            bomb_rounds_all_on_period = False

    ring: dict[str, Any] = {}
    games = max(1, len(seeds))
    for d in range(9):
        cells = [(r, c) for r in range(GRID) for c in range(GRID)
                 if RING_OF[r][c] == d and (r, c) not in walls]
        if not cells:
            continue
        ev = sum(per_cell_events[r][c] for r, c in cells)
        va = sum(per_cell_value[r][c] for r, c in cells)
        ring[str(d)] = {
            "traversable_cells": len(cells),
            "events_per_cell_per_game": ev / len(cells) / games,
            "gold_per_cell_per_game": va / len(cells) / games,
        }
    ratio = None
    if "1" in ring and "4" in ring and ring["4"]["events_per_cell_per_game"] > 0:
        ratio = (ring["1"]["events_per_cell_per_game"]
                 / ring["4"]["events_per_cell_per_game"])
    return {
        "available": True,
        "source": ("sim/scenario.py ScenarioGenerator.resolve_all() with no "
                   "SpawnState, i.e. intent against an empty board"),
        "map": map_name,
        "seeds": len(seeds),
        "module_constants": {
            "BOMB_PERIOD": SC_BOMB_PERIOD,
            "CENTRAL_POISSON_MEAN": CENTRAL_POISSON_MEAN,
            "outer_wait_uniform": list(OUTER_WAIT_BAND),
        },
        "central_intent_cells_per_round": mean_se(central_counts),
        "outer_event_rounds_per_game": mean_se(outer_rounds_per_game),
        "outer_event_interval": mean_se(outer_intervals),
        "outer_event_interval_histogram":
            {str(k): v for k, v in sorted(Counter(outer_intervals).items())},
        "outer_event_cells": mean_se(outer_cells_per_event),
        "outer_event_gold": mean_se(outer_gold_per_event),
        "bomb_refresh_rounds_per_game": mean_se(bomb_rounds_per_game),
        "bomb_refresh_all_on_period_20": bomb_rounds_all_on_period,
        "intent_generation_rate_by_ring": ring,
        "intent_d1_over_d4_event_ratio": ratio,
    }


def source_verification() -> dict[str, Any]:
    """Code-level facts re-derived for this report, with the version tag.

    Rule: re-derive from code or logs anything that enters a conclusion.  Two of
    the four generator claims this report depends on turned out to be wrong in
    the repo's own prose, so they are recorded here as contradictions rather than
    quietly used.
    """
    return {
        "source_commit_note": (
            "src/player.cpp at HEAD is behaviourally identical to fd47ea6: every "
            "later commit touching that file (53f22c8, 515df3b, 5ac0ebb, a89bcfb) "
            "adds only '//' comment lines, and "
            "`git diff fd47ea6 HEAD -- src/player.cpp` contains zero non-comment "
            "added or removed lines."
        ),
        "confirmed": [
            {"claim": "bombs are completely resampled every 20 rounds",
             "source": "sim/scenario.py:52 BOMB_PERIOD = 20; used at "
                       "sim/scenario.py:702 `if round_number % BOMB_PERIOD == 0`",
             "status": "confirmed in code and re-measured from the logs "
                       "(bomb_wave_modulo_20_histogram_summed)"},
            {"claim": "outer-ring gold events fire on a near-flat 8..16 wait",
             "source": "sim/scenario.py:691-694 "
                       "`next_outer = rng.randint(8,16)` / `+= rng.randint(8,16)`",
             "status": "confirmed in code and re-measured "
                       "(outer_event_interval_histogram_summed)"},
            {"claim": "central regular-round rate is 1.744 cells/round",
             "source": "sim/scenario.py:53 CENTRAL_POISSON_MEAN = 1.744, drawn at "
                       "sim/scenario.py:630",
             "status": "confirmed as the implemented parameter"},
            {"claim": "central gold value is Uniform{1..10}",
             "source": "sim/scenario.py:631-633 `rng.randint(1, 10)`",
             "status": "confirmed in code"},
            {"claim": "gold generation never lands on an occupied cell, on an "
                      "existing bomb, or on a wall, but does stack on a cell that "
                      "already holds gold",
             "source": "sim/scenario.py:517-520 gold_blocked = actor_cells | "
                       "bomb_cells | blocked_cells | gold_exclusions | walls; "
                       "sim/runner.py:194-215 _spawn_state never sets "
                       "gold_exclusions, and sim/scenario.py:344-348 documents "
                       "that existing gold alone does not block an additive "
                       "placement",
             "status": "confirmed in code; the flow measurement in this report "
                       "relies on it (per-cell stock is non-decreasing between "
                       "end[t] and start[t+1])"},
            {"claim": "the delivered player is blind to the only fog-free global "
                      "sensor in the API",
             "source": "src/game_api.h:41-54 lists nine GameInput fields.  "
                       "`grep -o 'in->[a-z_]*' src/player.cpp | sort | uniq -c` "
                       "returns exactly four: my_units (12), round (11), grid (8), "
                       "my_units_gold (3).  Case-insensitive grep counts for "
                       "'snapshot', 'gold_opp', 'visible_npc' and 'visible_enem' "
                       "in src/player.cpp are all 0.  State field rem_prev[4], "
                       "commented '上次快照外区(id 2-5)存量', is dead: nothing "
                       "reads or writes it.",
             "status": "confirmed; this is what B5 prices"},
            {"claim": "our player does contain one round-modulo-20 hook, so it is "
                      "not perfectly aperiodic",
             "source": "src/player.cpp:418-420 `if (in->round % 20 == 0) "
                       "waveTick(in);` and src/player.cpp:268-271 waveTick is "
                       "`memset(g_s.bombbit, 0, sizeof(g_s.bombbit))` -- it "
                       "ignores its argument and only clears the remembered bomb "
                       "bitmap.  It never sets a target, an anchor or a move.",
             "status": "confirmed; disclosed as a caveat on the period-20 "
                       "baseline, because the hook exists precisely because the "
                       "world resamples bombs on that period, so the two are "
                       "confounded by construction and cannot be separated in "
                       "this design"},
            {"claim": "the anchors are static",
             "source": "src/player.cpp:409 "
                       "`g_s.anch_r[u] = 6 + 5*u; g_s.anch_c[u] = 8` -> (6,8) and "
                       "(11,8), both on the central column, d = 2 and d = 3",
             "status": "confirmed in code; there is no periodic term anywhere in "
                       "the target selector"},
        ],
        "contradictions_found": [
            {
                "repo_claim": "sim/GENERATION.md:70 -- '模拟器直接用经验直方图, "
                              "不用参数化泊松' (the simulator uses the empirical "
                              "histogram, not a parametric Poisson)",
                "code_reality": "sim/scenario.py:630 `count = self._poisson(rng, "
                                "CENTRAL_POISSON_MEAN)`, and _poisson at "
                                "sim/scenario.py:582-590 is a textbook "
                                "product-of-uniforms Poisson sampler.  The "
                                "empirical per-round-count histogram in "
                                "GENERATION.md section 3.1 is NOT used.",
                "consequence": "The simulator's central count distribution is a "
                               "true Poisson(1.744); the measured truth is "
                               "under-dispersed with a thin tail, so simulated "
                               "high-count rounds are more frequent than real "
                               "ones.  Mean is right, shape is not.",
            },
            {
                "repo_claim": "sim/GENERATION.md:112 -- '模拟器直接采样经验边际' "
                              "(the simulator samples the empirical marginals "
                              "directly), stated about the centripetal central "
                              "gradient of GENERATION.md section 3.3 (single-cell "
                              "frequency 78.2 at d=1 versus 22.8 at d=4, a 3.4x "
                              "centre-to-edge ratio)",
                "code_reality": "sim/scenario.py:629-638 _make_central builds its "
                                "cell order with _uniform_order "
                                "(sim/scenario.py:603-606), which is a plain "
                                "`rng.shuffle` over the traversable region-1 "
                                "cells.  There is no row/column marginal, no "
                                "radial weight and no weighted race -- unlike "
                                "_outer_weighted_order (sim/scenario.py:608-627), "
                                "which does implement the token-2 hotspot weight.  "
                                "Central gold is therefore placed UNIFORMLY over "
                                "the 65 traversable central cells.",
                "consequence": "The simulator has no central generation peak.  "
                               "Measured here as intent_d1_over_d4_event_ratio, "
                               "which is 1.0 within noise instead of 3.4.  This "
                               "matters for both parts of this report: (a) the "
                               "player's fallback anchors are justified in "
                               "src/player.cpp:18 as '中央双驻守' on the central "
                               "generation peak, and this simulator cannot price "
                               "that peak at all, so it systematically "
                               "UNDER-values central anchoring; and (b) Part B's "
                               "untouched-cell gold is spread uniformly across the "
                               "central 9x9 in the simulator, whereas in reality "
                               "the d=3..4 shell we skip would hold roughly a "
                               "third as much per cell, so Part B OVER-states the "
                               "dispersion opportunity inside the central 9x9.  "
                               "Both directions agree with the repo's standing "
                               "guard rail, and this is the mechanism behind it.",
                "verified_how": "read sim/scenario.py:603-638 directly, then "
                                "measured the intent by calling "
                                "ScenarioGenerator.resolve_all() with no "
                                "SpawnState on the same seeds as the log batches",
            },
        ],
        "numbers_taken_from_the_repo_not_measured_here": [
            {"value": "collected/generated = 0.988 board-wide, 0.993 central",
             "source": "src/INFRA.md:5"},
            {"value": "NPCs eat 65.6% of map1's gold; a lost speed race does not "
                      "converge within 5 rounds, so contested gold is 100% novel "
                      "and must not be discounted",
             "source": "src/INFRA.md:5-9"},
            {"value": "naive 500-round open-loop bound 1004/1071/1158 gold across "
                      "three maps, of which 85-92% is temporal double counting; "
                      "novel remainder 119/145/78; closed-loop realisation "
                      "-832.4 +- 90.8",
             "source": "src/CHANGELOG.md:497-505 and "
                       "sim/reports/path_harvest_oracle.md:10-14,190,304,348"},
            {"value": "gold-block lifetime from 3421 death samples: central median "
                      "3 / P90 8; outer ordinary median 9 / mean 17.9; outer "
                      "hotspot median 8 / mean 12.9",
             "source": "src/INFRA.md:208"},
            {"value": "NPC action-level fidelity 38.70% historical / 39.18% fresh; "
                      "simulator clean lifetime median 3 / P90 10 versus truth "
                      "3 / 13",
             "source": "sim/README.md sections 7 and 9"},
            {"value": "outer-hotspot expedition measured at about -477 gold/game "
                      "paired over 30 same-seed swapped-seat pairs",
             "source": "sim/GENERATION.md:287 and src/CHANGELOG.md:147"},
            {"value": "our first-mover income is about 2.4x our second-mover "
                      "income (local 2.67x / 2.25x, platform 2.385x)",
             "source": "sim/README.md section 10.0"},
        ],
    }


# --------------------------------------------------------------------------- #
# three-way dry run
# --------------------------------------------------------------------------- #

def _synthetic_positions(kind: str, seed: str) -> tuple[list, list]:
    rng = Rng(["synthetic-positions", kind, seed])
    pos0: list[tuple[int, int]] = []
    pos1: list[tuple[int, int]] = []
    for _ in range(ROUNDS):
        if kind == "zero":
            a = (rng.randint(2, 14), rng.randint(2, 14))
            b = (rng.randint(2, 14), rng.randint(2, 14))
        elif kind == "injected":
            a = (rng.randint(1, 6), rng.randint(2, 14))
            b = (rng.randint(10, 15), rng.randint(2, 14))
        elif kind == "reversed":
            b = (rng.randint(1, 6), rng.randint(2, 14))
            a = (rng.randint(10, 15), rng.randint(2, 14))
        else:
            raise ValueError(kind)
        pos0.append(a)
        pos1.append(b)
    return pos0, pos1


def _synthetic_series(kind: str, seed: str) -> list[float]:
    rng = Rng(["synthetic-series", kind, seed])
    if kind == "zero":
        return [rng.gauss() for _ in range(ROUNDS)]
    if kind == "injected":
        return [3.0 * math.sin(2.0 * math.pi * t / 24.0) + 0.5 * rng.gauss()
                for t in range(ROUNDS)]
    if kind == "reversed":
        base = [3.0 * math.sin(2.0 * math.pi * t / 24.0) + 0.5 * rng.gauss()
                for t in range(ROUNDS)]
        return list(reversed(base))
    if kind == "injected_period_20":
        return [3.0 * math.sin(2.0 * math.pi * t / 20.0) + 0.5 * rng.gauss()
                for t in range(ROUNDS)]
    raise ValueError(kind)


def _synthetic_gold(kind: str) -> dict[str, Any]:
    """Ground-truth stock/flow arithmetic on a hand-built two-cell world.

    Cell A receives 10 gold on round 0 and is never touched; the stock therefore
    sits for 500 rounds, so the naive per-round sum is 5000 while the flow is 10.
    Cell B receives 10 gold and is eaten by an NPC on round 250.
    """
    generated = {"A": 10, "B": 10}
    if kind == "zero":
        untouched: list[str] = []
        eaten: dict[str, int] = {}
    elif kind == "injected":
        untouched = ["A", "B"]
        eaten = {"B": 7}
    elif kind == "reversed":
        untouched = ["B"]
        eaten = {"B": 7}
    else:
        raise ValueError(kind)
    naive = 0
    for cell in untouched:
        stock = generated[cell]
        for t in range(ROUNDS):
            if cell in eaten and t >= 250:
                stock = generated[cell] - eaten[cell]
            naive += stock
    flow = sum(generated[cell] for cell in untouched)
    contested = sum(eaten.get(cell, 0) for cell in untouched)
    return {
        "naive_per_round_stock_sum": naive,
        "flow_generated": flow,
        "stock_flow_discount_share": (1.0 - flow / naive) if naive else None,
        "contested": contested,
        "uncontested_remaining": flow - contested,
    }


def dry_run() -> dict[str, Any]:
    """Zero-signal, injected-effect and reversed-input adjudication.

    Every estimator that feeds a conclusion is exercised on all three inputs.
    A PASS requires the zero-signal case to report *absence*, the injected case
    to recover the injected magnitude and location, and the reversed case to
    report the reversal rather than the original.
    """
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "pass": bool(passed), "detail": detail})

    # ---------- periodicity ----------
    # A correctly calibrated estimator must NOT report zero false positives on
    # white noise -- it must report them at approximately the nominal rate.  The
    # zero-signal criterion is therefore "the false-positive rate is at or below
    # the nominal level", not "no draw is ever flagged".
    ls_null_peaks: list[float] = []
    ls_null_band_peaks: list[float] = []
    acf_null_max: list[float] = []
    flagged = 0
    draws = 200
    for i in range(draws):
        series = _synthetic_series("zero", str(i))
        ls = lomb_scargle(series)
        ls_null_peaks.append(ls["peak"]["power"])
        ls_null_band_peaks.append(ls["band_peak"]["power"])
        if ls["peak"]["fap"] < 0.05:
            flagged += 1
        acf = gap_aware_acf(series)
        acf_null_max.append(max(abs(v["r"]) for v in acf["acf"].values()))
    null_peak = mean_se(ls_null_peaks)
    null_band = mean_se(ls_null_band_peaks)
    rate = flagged / draws
    record("periodicity_zero_signal_reports_none", rate <= 0.10,
           {"white_noise_ls_peak_power": null_peak,
            "white_noise_ls_band_peak_power_4_40": null_band,
            "draws": draws,
            "flagged_at_fap_0.05": flagged,
            "false_positive_rate": rate,
            "nominal_rate": 0.05,
            "white_noise_acf_max_abs": mean_se(acf_null_max),
            "note": ("zero signal must produce false positives at approximately "
                     "the nominal 5% rate; a rate of 0 would mean the threshold "
                     "is miscalibrated in the other direction")})

    recovered: list[float] = []
    for i in range(20):
        ls = lomb_scargle(_synthetic_series("injected", str(i)))
        recovered.append(ls["peak"]["period"])
    record("periodicity_injected_effect_recovered",
           all(abs(p - 24.0) <= 1.0 for p in recovered),
           {"target_period": 24.0, "recovered": mean_se(recovered),
            "worst_abs_error": max(abs(p - 24.0) for p in recovered)})

    rec20: list[float] = []
    for i in range(20):
        ls = lomb_scargle(_synthetic_series("injected_period_20", str(i)))
        rec20.append(ls["peak"]["period"])
    record("periodicity_recovers_bomb_period_20_when_present",
           all(abs(p - 20.0) <= 1.0 for p in rec20),
           {"recovered": mean_se(rec20)})

    rev_ok = True
    rev_detail = []
    for i in range(10):
        forward = _synthetic_series("injected", str(i))
        backward = list(reversed(forward))
        pf = lomb_scargle(forward)["peak"]
        pb = lomb_scargle(backward)["peak"]
        same_period = abs(pf["period"] - pb["period"]) <= 1.0
        shuffled = list(forward)
        rng = Rng(["dry-shuffle", i])
        for k in range(len(shuffled) - 1, 0, -1):
            j = int(rng.next_u64() % (k + 1))
            shuffled[k], shuffled[j] = shuffled[j], shuffled[k]
        ps = lomb_scargle(shuffled)["peak"]
        destroyed = ps["power"] < pf["power"] / 3.0
        rev_ok = rev_ok and same_period and destroyed
        rev_detail.append({"forward": pf, "time_reversed": pb, "phase_shuffled": ps})
    record("periodicity_reversed_input_behaves_correctly", rev_ok,
           {"expectation": ("time reversal preserves the period (the estimator is "
                            "not reading a trend), phase shuffling destroys the "
                            "power (the estimator is reading order, not marginals)"),
            "cases": rev_detail[:3], "cases_total": len(rev_detail)})

    # ---------- partition ----------
    # Two units drawn independently from the same distribution cannot reach
    # OVL == 1 at 500 samples over 169 cells: multinomial sampling noise alone
    # costs roughly 0.3.  The correct zero-signal criterion is therefore
    # agreement with the within-round label-permutation null, which carries the
    # identical sampling noise, not an absolute overlap threshold.
    p0, p1 = _synthetic_positions("zero", "0")
    zero_part = partition_metrics(p0, p1, "dry-zero", permutations=40)
    zero_null = zero_part["null_unit_label_permutation"]
    zero_ok = (
        zero_part["observed"]["centroid_separation"] < 1.5
        and zero_null["p_accuracy_ge_observed"] > 0.05
        and zero_null["p_overlap_le_observed"] > 0.05
        and zero_null["p_separation_ge_observed"] > 0.05
        and abs(zero_part["observed"]["overlap"] - zero_null["overlap"]["mean"]) < 0.05
    )
    record("partition_zero_signal_reports_none", zero_ok,
           dict(zero_part["observed"]) |
           {"perm_p_accuracy": zero_null["p_accuracy_ge_observed"],
            "perm_p_overlap": zero_null["p_overlap_le_observed"],
            "perm_p_separation": zero_null["p_separation_ge_observed"],
            "null_overlap": zero_null["overlap"],
            "null_axis_accuracy": zero_null["axis_accuracy"],
            "note": ("observed overlap must sit on top of the permutation null; "
                     "the absolute value is well below 1 purely from sampling "
                     "noise, which is exactly why the null is required")})

    p0, p1 = _synthetic_positions("injected", "0")
    inj_part = partition_metrics(p0, p1, "dry-inj", permutations=40)
    inj_ok = (
        inj_part["observed"]["overlap"] < 0.05
        and inj_part["observed"]["axis"]["axis"] == "row"
        and inj_part["observed"]["axis"]["accuracy"] > 0.99
        and inj_part["observed"]["axis"]["orientation"] == "unit0_low"
        and inj_part["null_unit_label_permutation"]["p_accuracy_ge_observed"] <= 0.05
    )
    record("partition_injected_effect_recovered", inj_ok, inj_part["observed"])

    p0, p1 = _synthetic_positions("reversed", "0")
    rev_part = partition_metrics(p0, p1, "dry-rev", permutations=40)
    rev_ok2 = (
        rev_part["observed"]["axis"]["axis"] == "row"
        and rev_part["observed"]["axis"]["accuracy"] > 0.99
        and rev_part["observed"]["axis"]["orientation"] == "unit1_low"
        and abs(rev_part["observed"]["overlap"] - inj_part["observed"]["overlap"]) < 0.05
    )
    record("partition_reversed_input_flips_orientation_only", rev_ok2,
           {"reversed": rev_part["observed"], "forward": inj_part["observed"]})

    # ---------- amplitude / excursions ----------
    flat = turning_point_excursions([3] * 100)
    saw = turning_point_excursions([1, 2, 3, 4, 3, 2, 1] * 10)
    rev_saw = turning_point_excursions(list(reversed([1, 2, 3, 4, 3, 2, 1] * 10)))
    record("excursion_zero_signal_reports_none", flat["n"] == 0,
           {"flat_series_peaks": flat["n"]})
    record("excursion_injected_effect_recovered",
           saw["n"] == 10 and set(saw["peaks"]) == {4},
           {"peaks": saw["n"], "distinct_peak_values": sorted(set(saw["peaks"])),
            "durations": sorted(set(saw["durations"]))})
    record("excursion_reversed_input_matches",
           rev_saw["n"] == saw["n"] and sorted(rev_saw["peaks"]) == sorted(saw["peaks"]),
           {"forward": saw["n"], "reversed": rev_saw["n"]})

    # ---------- phase ----------
    a = [math.sin(2 * math.pi * t / 24.0) for t in range(ROUNDS)]
    b = [math.sin(2 * math.pi * (t - 6) / 24.0) for t in range(ROUNDS)]
    rng = Rng("dry-phase")
    noise0 = [rng.gauss() for _ in range(ROUNDS)]
    noise1 = [rng.gauss() for _ in range(ROUNDS)]
    xc_zero = cross_correlation(noise0, noise1)
    xc_lag = cross_correlation(a, b)
    peak_lag = max(xc_lag.items(), key=lambda kv: kv[1])[0]
    xc_rev = cross_correlation(b, a)
    peak_lag_rev = max(xc_rev.items(), key=lambda kv: kv[1])[0]
    record("phase_zero_signal_reports_none",
           max(abs(v) for v in xc_zero.values()) < 0.20,
           {"max_abs_xcorr_white_noise": max(abs(v) for v in xc_zero.values())})
    record("phase_injected_lag_recovered", int(peak_lag) == 6,
           {"expected_lag": 6, "recovered_lag": int(peak_lag),
            "construction": "b[t] = a[t-6], so b reproduces a six rounds later",
            "convention": ("xcorr(L) correlates xs[t] with ys[t+L]; a peak at "
                           "L > 0 means xs leads ys by L")})
    record("phase_reversed_input_flips_lag_sign", int(peak_lag_rev) == -6,
           {"recovered_lag": int(peak_lag_rev)})

    # ---------- stock / flow arithmetic ----------
    g_zero = _synthetic_gold("zero")
    g_inj = _synthetic_gold("injected")
    g_rev = _synthetic_gold("reversed")
    record("stock_flow_zero_signal_reports_none",
           g_zero["naive_per_round_stock_sum"] == 0 and g_zero["flow_generated"] == 0,
           g_zero)
    expected_naive = 10 * 500 + 10 * 250 + 3 * 250     # = 8250 gold-rounds
    record("stock_flow_injected_effect_recovered",
           g_inj["flow_generated"] == 20
           and g_inj["naive_per_round_stock_sum"] == expected_naive
           and abs(g_inj["stock_flow_discount_share"]
                   - (1 - 20 / expected_naive)) < 1e-12
           and g_inj["contested"] == 7,
           g_inj | {"expected_naive": expected_naive,
                    "expected_flow": 20,
                    "note": ("naive counts the same 10-gold stock once per round; "
                             "the flow counts it once, so the discount is 99.76% "
                             "on this construction -- larger than the 85-92% the "
                             "repo measured because the synthetic block never "
                             "dies")})
    record("stock_flow_reversed_input_attaches_to_other_set",
           g_rev["flow_generated"] == 10 and g_rev["contested"] == 7
           and g_rev["uncontested_remaining"] == 3,
           g_rev)

    # ---------- replay kernel ----------
    board = [[0] * GRID for _ in range(GRID)]
    board[8][9] = 10
    board[8][10] = BOMB
    out = replay_round(
        board, [1, -1],
        {1: (0, [[3, 3, STAY], []])},
        {-1: [STAY, STAY, STAY]},
        {("p", 1, 0): (8, 8), ("p", 1, 1): (0, 0), ("p", 2, 0): (16, 16),
         ("p", 2, 1): (0, 16), ("n", -1, 0): (16, 0)},
    )
    pick = out["pickups"]
    replay_ok = (
        out["positions"][("p", 1, 0)] == (8, 10)
        and len(pick) == 1 and pick[0][0] == (8, 9) and pick[0][2] == 7
        and out["ground"][8][9] == 3 and out["ground"][8][10] == 0
    )
    record("replay_kernel_pickup_and_bomb", replay_ok,
           {"end_position": list(out["positions"][("p", 1, 0)]),
            "pickups": [[list(c), cls, amt] for c, cls, amt, _ in pick],
            "residual_on_gold_cell": out["ground"][8][9],
            "bomb_cell_after": out["ground"][8][10],
            "expectation": "ceil(0.65*10)=7 collected, 3 residual, bomb consumed"})

    board2 = [[0] * GRID for _ in range(GRID)]
    board2[0][1] = WALL
    blocked = False
    try:
        replay_round(board2, [1], {1: (0, [[3], []])}, {},
                     {("p", 1, 0): (0, 0), ("p", 1, 1): (16, 16),
                      ("p", 2, 0): (0, 16), ("p", 2, 1): (16, 0)})
    except ReplayError:
        blocked = True
    record("replay_kernel_rejects_impossible_effective_action", blocked,
           {"expectation": ("an effective action can never enter a wall; if the log "
                            "says it does, the parse is wrong and we must fail loudly")})

    vr = vision_radius_series([0, 0, 2, 2, 5, 5])
    record("vision_radius_decoding",
           vr == [2, 2, 2, 3, 2, 4],
           {"input_cumulative_spend": [0, 0, 2, 2, 5, 5], "decoded_radii": vr,
            "expectation": "a purchase in r-1 raises the radius in r for one round"})

    passed = all(item["pass"] for item in checks)
    return {
        "pass": passed,
        "checks": checks,
        "white_noise_reference": {
            "ls_peak_power_full_range": null_peak,
            "ls_band_peak_power_4_40": null_band,
            "acf_max_abs_over_lags_1_80": mean_se(acf_null_max),
            "periods_in_grid": len(PERIODS),
            "note": ("empirical white-noise reference for a length-500 series; the "
                     "environment-calibrated figure in the run output is the number "
                     "to compare an opponent against, because the environment is "
                     "not white noise"),
        },
    }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def _worker(args: tuple) -> dict[str, Any]:
    return extract_game(*args)


def load_batch(label: str, directory: Path) -> list[tuple]:
    summary = json.loads((directory / "summary.json").read_text())
    if summary.get("status") != "ok" or summary.get("games_failed"):
        raise RuntimeError("batch %s is not clean: %s" % (label, summary.get("status")))
    tasks = []
    for record in summary["records"]:
        result = record["result"]
        log_file = record.get("log_file")
        if log_file is None:
            for key in ("a_as_p1", "b_as_p1"):
                if key in record:
                    raise RuntimeError("paired batches are not supported; "
                                       "run one --fixed-costs per arm")
            raise RuntimeError("batch %s record has no log_file" % label)
        tasks.append((
            str(directory / log_file), label, int(record["game_index"]),
            tuple(result["fixed_costs"]), PERMUTATIONS, result["scenario_digest"],
        ))
    return tasks


def run(argv: argparse.Namespace) -> int:
    batches: list[tuple[str, Path]] = []
    for spec in argv.batch:
        if "=" not in spec:
            raise SystemExit("--batch expects LABEL=DIR, got %r" % spec)
        label, directory = spec.split("=", 1)
        batches.append((label, Path(directory)))

    tasks: list[tuple] = []
    batch_meta: dict[str, Any] = {}
    for label, directory in batches:
        summary = json.loads((directory / "summary.json").read_text())
        tasks.extend(load_batch(label, directory))
        batch_meta[label] = {
            "directory": str(directory),
            "map": summary["map"],
            "map_fingerprint": summary["map_fingerprint"],
            "dispatch": summary["dispatch"],
            "fixed_costs": summary["fixed_costs"],
            "strategies": summary["strategies"],
            "games": len(summary["records"]),
            "seeds": [r["seed"] for r in summary["records"]],
            "scenario_digests": [r["result"]["scenario_digest"]
                                 for r in summary["records"]],
            "log_sha256": [r["result"]["log_sha256"] for r in summary["records"]],
            "player_names": {
                "1": summary["records"][0]["result"]["players"]["1"]["name"],
                "2": summary["records"][0]["result"]["players"]["2"]["name"],
            },
            "gross_gold_by_seat": {
                seat: mean_se([r["result"]["players"][seat]["gross_gold"]
                               for r in summary["records"]])
                for seat in ("1", "2")
            },
        }
    tasks.sort(key=lambda item: (item[1], item[2]))

    jobs = argv.jobs or max(1, (os.cpu_count() or 2) // 2)
    games: list[dict[str, Any]] = []
    if jobs <= 1:
        for task in tasks:
            games.append(_worker(task))
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
            for result in pool.map(_worker, tasks, chunksize=1):
                games.append(result)
    games.sort(key=lambda g: (g["batch"], g["game_index"]))

    validation = {
        "path_replay_exact": sum(g["validation"]["path_replay_exact"] for g in games),
        "path_replay_total": sum(g["validation"]["path_replay_total"] for g in games),
        "ground_replay_exact": sum(g["validation"]["ground_replay_exact"] for g in games),
        "ground_replay_total": sum(g["validation"]["ground_replay_total"] for g in games),
        "ground_monotonicity_violations": sum(
            g["validation"]["ground_monotonicity_violations"] for g in games),
    }
    validation["path_replay_exact_all"] = (
        validation["path_replay_exact"] == validation["path_replay_total"])
    validation["ground_replay_exact_all"] = (
        validation["ground_replay_exact"] == validation["ground_replay_total"])

    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for game in games:
        for seat in ("1", "2"):
            node = game["seats"][seat]
            key = (node["strategy"], node["arm"])
            groups.setdefault(key, []).append(node)

    arms: dict[str, Any] = {}
    for (strategy, arm), seats in sorted(groups.items()):
        arms["%s::%s" % (strategy, arm)] = {
            "strategy": strategy,
            "arm": arm,
            "source_batches": sorted({
                g["batch"] for g in games
                for s in ("1", "2")
                if g["seats"][s] is not None
                and g["seats"][s]["strategy"] == strategy
                and g["seats"][s]["arm"] == arm
            }),
            **aggregate_group(seats),
        }

    series: dict[str, Any] = {}
    for game in games:
        for seat in ("1", "2"):
            node = game["seats"][seat]
            series["%s/%04d/seat%s" % (game["batch"], game["game_index"], seat)] = {
                "strategy": node["strategy"], "arm": node["arm"],
                "scenario_digest": game["scenario_digest"],
                **node["series"],
            }

    provenance: dict[str, Any] = {}
    if argv.provenance:
        provenance = json.loads(Path(argv.provenance).read_text())

    dry = dry_run()
    if not dry["pass"]:
        raise SystemExit("dry run failed; refusing to emit a report")

    env_games = [g for g in games if g["batch"] in argv.environment_batch] or games
    intent_seeds: list[int] = []
    intent_map = "map1"
    for label, meta in sorted(batch_meta.items()):
        if argv.environment_batch and label not in argv.environment_batch:
            continue
        intent_map = meta["map"]
        for seed in meta["seeds"]:
            if seed.get("type") == "int":
                value = int(seed["value"])
                if value not in intent_seeds:
                    intent_seeds.append(value)
    report = {
        "schema": "goldrush.area_coverage.v1",
        "generated_by": "sim/analyze_area_coverage.py",
        "question": (
            "Part A: does our provably non-periodic player look periodic under a "
            "gap-aware ACF plus Lomb-Scargle estimator in an environment whose "
            "bombs resample every 20 rounds and whose outer-ring gold events fire "
            "every 8-16 rounds?  Part B: how much of the board do our two units "
            "touch over 500 rounds, and how much gold accumulates where they do "
            "not go?"
        ),
        "definitions": definitions(),
        "source_verification": source_verification(),
        "bias": bias_register(),
        "provenance": provenance,
        "batches": batch_meta,
        "validation": validation,
        "dry_run": dry,
        "environment_baseline": aggregate_environment(env_games),
        "generator_intent_baseline": generator_intent_baseline(
            intent_map, intent_seeds[:argv.intent_seeds]),
        "arms": arms,
        "raw_series": series,
        "per_game": [
            {
                "batch": g["batch"], "game_index": g["game_index"],
                "scenario_digest": g["scenario_digest"],
                "fixed_costs": g["fixed_costs"], "faster_seat": g["faster_seat"],
                "names": g["names"], "validation": g["validation"],
                "seats": {
                    seat: {
                        "strategy": g["seats"][seat]["strategy"],
                        "arm": g["seats"][seat]["arm"],
                        "gross_gold_units": g["seats"][seat]["gross_gold_units"],
                        "ring_share_pooled":
                            g["seats"][seat]["part_a"]["amplitude"]["ring_share_pooled"],
                        "partition_observed":
                            g["seats"][seat]["part_a"]["partition"]["observed"],
                        "ls_peak_ring0":
                            g["seats"][seat]["part_a"]["periodicity"]
                                          ["ring_distance"][0]["ls"]["peak"],
                        "ls_peak_ring1":
                            g["seats"][seat]["part_a"]["periodicity"]
                                          ["ring_distance"][1]["ls"]["peak"],
                        "ls_peak_income_sum":
                            g["seats"][seat]["part_a"]["periodicity"]
                                          ["player_income_sum"]["ls"]["peak"],
                        "coverage": {
                            name: {
                                k: v for k, v in
                                g["seats"][seat]["part_b"]["coverage"][name].items()
                                if k != "visit_count_histogram"
                            }
                            for name in STRATA
                        },
                    }
                    for seat in ("1", "2")
                },
            }
            for g in games
        ],
    }

    output = Path(argv.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=1, sort_keys=True,
                                 allow_nan=False) + "\n")
    print("wrote %s (%d games, %d arms)" % (output, len(games), len(arms)))
    print("path replay exact: %d/%d   ground replay exact: %d/%d" % (
        validation["path_replay_exact"], validation["path_replay_total"],
        validation["ground_replay_exact"], validation["ground_replay_total"]))
    for key in sorted(arms):
        node = arms[key]
        print("  %-40s games=%-4d gross=%.1f" % (
            key, node["games"], node["gross_gold_per_game"]["mean"] or 0.0))
    return 0


def definitions() -> dict[str, Any]:
    return {
        "board": "17x17, indices 0..16, centre (8,8)",
        "ring_distance": "d = chebyshev(position, (8,8))",
        "strata": {
            STRATUM_CENTRAL: "d <= 4, the windmill region 1 9x9 block (81 cells)",
            STRATUM_OUTER: "d >= 5 (208 cells)",
        },
        "phase_used_for_positions": (
            "end phase.  Positions change only during a round, so "
            "start[t+1] == end[t]; the two series are the same up to a one-round "
            "shift and end[t] is the position the round's decision produced."
        ),
        "entered_vs_occupied": (
            "'entered' = cells a unit moved into with a non-STAY effective action; "
            "this is the pickup-relevant set because standing still collects "
            "nothing (sim/engine.py execute_action returns before pickup when the "
            "move did not happen).  'occupied' additionally counts the cell the "
            "unit started the round on."
        ),
        "income": "per unit, end.gold - start.gold; includes bomb and trample burn",
        "overlap": (
            "OVL = sum over 289 cells of min(p0(cell), p1(cell)) where pj is unit "
            "j's normalised end-phase visit histogram over the 500 rounds; 1.0 "
            "means the two units are spatially indistinguishable"
        ),
        "separating_axis": (
            "four fixed axis families (row, col, diag = r+c, anti = r-c); for each, "
            "the integer threshold and the game-constant orientation that maximise "
            "the fraction of rounds with score(low unit) < theta <= score(high "
            "unit).  Rounds with equal scores are never counted correct."
        ),
        "partition_null": (
            "within-round unit-label permutation: independently swap the two unit "
            "labels in each round with probability 1/2, recompute all three "
            "statistics, %d draws per game" % PERMUTATIONS
        ),
        "acf": (
            "gap-aware: r(k) = sum over t with both t and t+k present of "
            "(x_t - mu)(x_{t+k} - mu) / (pairs * var), mu and var over present "
            "samples; lags 1..%d; white-noise SE = 1/sqrt(pairs)" % ACF_MAX_LAG
        ),
        "lomb_scargle": (
            "Scargle normalisation P(w) = (1/2s^2)[Cx^2/Ccc + Sx^2/Sss] with s^2 "
            "the sample variance, so independent Gaussian noise gives "
            "approximately Exponential(1); %d periods spanning %.0f..%.0f, "
            "uniform in frequency plus every integer period 3..80; the reported "
            "band peak is restricted to periods %.0f..%.0f"
            % (len(PERIODS), PERIOD_MIN, PERIOD_MAX, PERIOD_BAND[0], PERIOD_BAND[1])
        ),
        "false_alarm_probability": (
            "1 - (1 - exp(-power))^M with M the number of grid periods searched; "
            "conservative because grid periods are correlated"
        ),
        "excursion": (
            "peak-trough decomposition of d(t) with plateaus compressed: an "
            "excursion peak is a strict local maximum of the compressed series "
            "(series boundaries count as one-sided).  Duration is the number of "
            "original rounds between the two bracketing minima, inclusive.  The "
            "definition presumes no centre-to-periphery direction."
        ),
        "cross_correlation_sign": (
            "xcorr(L) correlates d0 at t with d1 at t+L, so a peak at L > 0 means "
            "unit 1 reproduces unit 0's ring distance L rounds later, i.e. unit 0 "
            "leads unit 1 by L rounds"
        ),
        "targetable": (
            "union over both units of the chebyshev radius-%d square around the "
            "end-phase position -- the window src/player.cpp actually scans.  "
            "'visible' uses the vision radius in effect that round instead, "
            "decoded from the cumulative vision_spent field."
            % SELECTOR_RADIUS
        ),
        "cell_round_coverage_fraction": (
            "sum over rounds of the number of distinct stratum cells entered that "
            "round, divided by (stratum cells * 500)"
        ),
        "gold_naive_vs_flow": (
            "naive = sum over all 500 rounds of the start-phase stock sitting on "
            "cells our units never enter, which counts a persistent stock once per "
            "round and therefore has units of gold-rounds, not gold.  flow = the "
            "gold actually deposited on those cells over the game, measured as "
            "start[t+1] minus end[t] per cell plus the round-0 initial stock.  "
            "The flow is then split into a contested part (picked up from those "
            "cells by NPCs or by the opponent -- a speed race, 100% novel, not "
            "discounted) and an uncontested remainder still on the board at the "
            "end."
        ),
        "regeneration_wait": (
            "for each cell our unit ever entered, the wait from the last such "
            "round to the first later round whose start-phase stock reaches a "
            "threshold; right-censored at round 499 and the censored share is "
            "reported, so the medians are optimistic"
        ),
        "arms": (
            "first_mover = our seat is the faster player under "
            "--dispatch fixed (lower fixed cost; ties favour P1); second_mover is "
            "the other seat.  Arms are reported separately and never subtracted."
        ),
    }


def bias_register() -> dict[str, Any]:
    return {
        "npc_model": (
            "The simulator's NPC reproduces real NPC actions 38.7% (historical) / "
            "39.2% (fresh replay) of the time and is too greedy and too "
            "centre-biased (sim/README.md section 7).  Absolute income is not "
            "comparable to the platform; only same-seed paired deltas are."
        ),
        "part_b_direction": (
            "Part B is a scarcity/dispersion measurement.  The repo's guard rail "
            "states scarcity/dispersion effects are systematically OVER-estimated "
            "by this simulator and central-efficiency effects UNDER-estimated.  So "
            "the untouched-cell gold reported here is an upper bound in the "
            "central stratum in two compounding ways: the model's NPCs eat central "
            "gold too fast, which shortens block lifetimes and inflates the "
            "apparent contested share, and the same over-greed makes the outer "
            "ring look relatively more attractive than it is."
        ),
        "coverage_direction": (
            "Coverage counts (distinct cells entered, cell-round fraction, "
            "never-targetable share) are properties of OUR policy replayed "
            "exactly from effective actions, so they carry no NPC-model bias "
            "except through the positions our policy reaches, which do depend on "
            "where gold is left standing."
        ),
        "generation_side": (
            "sim/GENERATION.md section 7 note 7: generation is fitted from three "
            "map1 games and should not depend on opponent strength, so the "
            "environment baseline (lambda, interval band, radial gradient) is the "
            "least biased block in this report.  It is still a fit, not the "
            "official RNG."
        ),
        "order_arms": (
            "Never subtract the two arms.  Our first-mover income is roughly "
            "2.4x our second-mover income and action order is endogenous on the "
            "platform; here it is manipulated exogenously by --fixed-costs, but "
            "the two arms remain different populations of game states."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="three-way dry run on synthetic input")
    v.add_argument("--output", default=None,
                   help="optional path for the dry-run record")

    r = sub.add_parser("run", help="parse log batches and write the report")
    r.add_argument("--batch", action="append", required=True,
                   metavar="LABEL=DIR", help="a cli.py --output-dir, repeatable")
    r.add_argument("--environment-batch", action="append", default=[],
                   help="batch labels to use for the generator-side baseline "
                        "(default: all)")
    r.add_argument("--output", required=True)
    r.add_argument("--provenance", default=None,
                   help="JSON file with hostname / lscpu / sha256 / commit")
    r.add_argument("--jobs", type=int, default=0)
    r.add_argument("--intent-seeds", type=int, default=40,
                   help="how many batch seeds to re-materialise through the "
                        "generator for the intent-side baseline")

    args = parser.parse_args(argv)
    if args.command == "validate":
        result = dry_run()
        text = json.dumps(result, indent=1, sort_keys=True, allow_nan=False)
        if args.output:
            Path(args.output).write_text(text + "\n")
        for item in result["checks"]:
            print("%-52s %s" % (item["check"], "PASS" if item["pass"] else "FAIL"))
        print("white-noise LS peak power (len 500, %d periods): %.3f +- %.3f" % (
            len(PERIODS),
            result["white_noise_reference"]["ls_peak_power_full_range"]["mean"],
            result["white_noise_reference"]["ls_peak_power_full_range"]["se"]))
        print("dry run: %s" % ("PASS" if result["pass"] else "FAIL"))
        return 0 if result["pass"] else 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
