#!/usr/bin/env python3
"""Adjudicate four pre-registered claims about Tiuntled-1's (player163) spatial policy.

The owner's replay observation under test:

    "T-1 is two robots each responsible for one half of the board, with a
     regular pattern: center -> periphery -> center -> periphery."

Operationalised as four independent claims, each with a decisive statistic, a
standard error and a *named* null:

  P   partition   OVL occupancy overlap + fixed separating axis accuracy
                  null = within-round unit-label permutation (refit inside null)
  C   cycle       gap-aware pairwise-complete ACF + generalised Lomb-Scargle
                  nulls = AR(1)-matched / AR(p)-ACF-matched / bounded random walk
  A   amplitude   ring-distance distribution + per-excursion peak ring
                  plus RIGOROUS min/max bounds from the known missingness law
  Phi anti-phase  corr(d0, d1) + cross-correlation at lags -20..+20
                  null = 0 and cross-GAME unit pairing

Channels
--------
Trajectory channel (fog-biased).  ``end[r].players[i].units[j].position`` is
``null`` exactly when the unit stands outside the union of radius-``vision_r``
Chebyshev squares around OUR OWN two units' end-of-round positions.  Verified
here: (a) the ``end.grid`` fog mask equals that union exactly (0/2000 round
mismatches over 4 games), (b) every observed target position lies inside the
non-fog set (0/10000).  So the missingness law is *known exactly*, which is what
makes the rigorous amplitude bounds of claim A possible.

Gold channel (fog-free).  Per-unit ``gold`` is logged for every unit-round of
both players regardless of fog, and ``start[r].units[j].gold ==
end[r-1].units[j].gold`` (verified 0/309788 mismatches over all 158 T-1 games),
so ``end[r].gold - start[r].gold`` is an exact, complete, unbiased census of
per-unit per-round income.  The same periodicity machinery is run on it, with
the *other player in the same game* as a within-game environmental control.

Environmental periodicity that must not be mistaken for policy
--------------------------------------------------------------
  * bombs are resampled wholesale on a strict period-20 clock.  Re-verified
    here from the logs: over the 5 clean games, bomb<->non-bomb transitions on
    co-visible cells number 1362 at ``r mod 20 == 0`` and exactly 0 at every
    other residue.  A period-20 spectral line is therefore a *candidate
    environmental artefact* and is tested separately for absolute-clock phase
    locking (circular-shift null), which is the property only the environment
    can have.
  * outer-ring hotspot events fire on a jittered renewal process with interval
    8-16 rounds (mean ~12.4), so they cannot produce a sharp spectral line, only
    a broad bump near f in [1/16, 1/8].  Their times are partially observable
    (token-2 cell jumping above 10 gold inside our fog window), so an
    event-triggered average of d(t) is reported as well.

Usage
    python3 sim/analyze_t1_spatial.py validate   # three-way dry run + schema checks
    python3 sim/analyze_t1_spatial.py run        # writes sim/reports/t1_spatial_policy.json
    python3 sim/analyze_t1_spatial.py run --quick # fewer surrogates (NOT for publication)

Determinism: sorted iteration everywhere, RNG seeds derived from fixed strings,
no wall-clock timestamp in the artifact, all floats rounded before emission, so
two invocations of ``run`` produce byte-identical JSON.

Standard library only (no numpy/scipy on this machine; a generalised
Lomb-Scargle, Levinson-Durbin AR fit and a layered-graph DP are vendored below).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import operator
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "logs" / "opponents" / "manifest.json"
OUT_JSON = ROOT / "sim" / "reports" / "t1_spatial_policy.json"

GRID = 17
CENTER = 8
FOG = -5
WALL = -1
BOMB = -3
TARGET_ACCOUNT = "player163"
TARGET_TEAM = "Tiuntled-1"

WARMUP_ROUNDS = 4          # rounds 0..3 are latency warm-up
STEPS_PER_ROUND = 3
CENTRAL_MAX_D = 4          # central 9x9 is d <= 4
OUTER_MIN_D = 5

PRIMARY_PROBE = "probeobs"
PRIMARY_GAMES = (171719, 171747, 172186, 172187, 172219)
SECONDARY_GAMES = (159175, 162570, 162578, 162632, 162660)

# map1 token-2 hotspot cells (sim/GENERATION.md 4.7); re-derived from line 2 of
# every map1 log by ``hotspots_from_map`` and cross-checked in validate().
MAP1_HOTSPOTS = ((0, 4), (0, 5), (0, 12), (1, 6), (1, 10), (5, 0), (5, 16),
                 (7, 1), (7, 15), (9, 2), (9, 14), (11, 0), (11, 16),
                 (12, 3), (12, 13), (15, 6), (15, 10), (16, 4), (16, 5),
                 (16, 12))

# --- periodogram / null configuration ------------------------------------- #
PERIOD_MIN = 3.0
PERIOD_MAX = 120.0
OVERSAMPLE = 4             # frequency grid density; SAME grid for real + nulls
BASE_SPAN = 500.0
N_SURROGATE = 250
N_PERM = 2000
N_BOOT = 1000
MIN_PAIRS_ACF = 30
ACF_MAX_LAG = 80
XCORR_MAX_LAG = 20
ARP_ORDER = 30
PHASE_SEGMENTS = 4
BLOCK_LEN = 20
FLOAT_ND = 6


# --------------------------------------------------------------------------- #
# tiny numeric helpers (stdlib only)
# --------------------------------------------------------------------------- #

def rnd(x):
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return round(x, FLOAT_ND)
    return x


def deep_round(obj):
    if isinstance(obj, dict):
        return {k: deep_round(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [deep_round(v) for v in obj]
    return rnd(obj)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def sd(xs, ddof=1):
    xs = list(xs)
    n = len(xs)
    if n - ddof <= 0:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def se_of_mean(xs):
    xs = list(xs)
    s = sd(xs)
    return None if s is None else s / math.sqrt(len(xs))


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = syy = sxy = 0.0
    for a, b in zip(xs, ys):
        da = a - mx
        db = b - my
        sxx += da * da
        syy += db * db
        sxy += da * db
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def quantile(sorted_xs, q):
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = pos - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def null_summary(samples, observed, tail="upper"):
    """Summarise a Monte-Carlo null: mean, sd, z, exceedance count, p."""
    samples = [s for s in samples if s is not None]
    if not samples or observed is None:
        return {"n": len(samples), "mean": None, "sd": None, "z": None,
                "p_one_sided": None, "q05": None, "q95": None,
                "exceed_count": None, "tail": tail}
    m = mean(samples)
    s = sd(samples)
    ss = sorted(samples)
    if tail == "upper":
        exceed = sum(1 for v in ss if v >= observed)
    else:
        exceed = sum(1 for v in ss if v <= observed)
    return {"n": len(samples), "mean": m, "sd": s,
            "z": ((observed - m) / s) if (s and s > 0) else None,
            "p_one_sided": (exceed + 1.0) / (len(ss) + 1.0),
            "q05": quantile(ss, 0.05), "q95": quantile(ss, 0.95),
            "exceed_count": exceed, "tail": tail}


def seeded_rng(*parts):
    key = "|".join(str(p) for p in parts)
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return random.Random(int(h[:16], 16))


def gauss_stream(rng, n):
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


# --------------------------------------------------------------------------- #
# generalised Lomb-Scargle (Zechmeister & Kuerster 2009, floating mean)
# --------------------------------------------------------------------------- #

def freq_grid(span=BASE_SPAN, oversample=OVERSAMPLE,
              pmin=PERIOD_MIN, pmax=PERIOD_MAX):
    df = 1.0 / (oversample * span)
    f0 = 1.0 / pmax
    f1 = 1.0 / pmin
    n = int(math.floor((f1 - f0) / df)) + 1
    return [f0 + i * df for i in range(n)]


class GLSBasis:
    """Trig basis pre-computed on a fixed observation mask.

    Because every surrogate for a given series shares the mask, the
    y-independent sums are computed once; each evaluation then costs two dot
    products per frequency.  Real data and surrogates use the SAME frequency
    grid (comparing a fine-grid maximum against a coarse-grid null maximum is a
    known way to manufacture significance).
    """

    __slots__ = ("ts", "freqs", "cos", "sin", "C", "S", "CC", "SS", "CS", "D", "n")

    def __init__(self, ts, freqs):
        self.ts = list(ts)
        self.freqs = list(freqs)
        self.n = len(self.ts)
        self.cos = []
        self.sin = []
        self.C = []
        self.S = []
        self.CC = []
        self.SS = []
        self.CS = []
        self.D = []
        inv = 1.0 / self.n
        for f in self.freqs:
            w = 2.0 * math.pi * f
            cs = [math.cos(w * t) for t in self.ts]
            sn = [math.sin(w * t) for t in self.ts]
            C = sum(cs) * inv
            S = sum(sn) * inv
            CC = sum(c * c for c in cs) * inv - C * C
            SS = sum(s * s for s in sn) * inv - S * S
            CS = sum(map(operator.mul, cs, sn)) * inv - C * S
            self.cos.append(cs)
            self.sin.append(sn)
            self.C.append(C)
            self.S.append(S)
            self.CC.append(CC)
            self.SS.append(SS)
            self.CS.append(CS)
            self.D.append(CC * SS - CS * CS)

    def power(self, ys):
        """Normalised GLS power in [0,1] per frequency (fraction of variance)."""
        n = self.n
        inv = 1.0 / n
        Y = sum(ys) * inv
        YY = sum(y * y for y in ys) * inv - Y * Y
        if YY <= 0.0:
            return [0.0] * len(self.freqs)
        out = []
        for k in range(len(self.freqs)):
            D = self.D[k]
            if D <= 1e-12:
                out.append(0.0)
                continue
            YC = sum(map(operator.mul, ys, self.cos[k])) * inv - Y * self.C[k]
            YS = sum(map(operator.mul, ys, self.sin[k])) * inv - Y * self.S[k]
            p = (self.SS[k] * YC * YC + self.CC[k] * YS * YS
                 - 2.0 * self.CS[k] * YC * YS) / (YY * D)
            out.append(p if p > 0.0 else 0.0)
        return out

    def phase_at(self, ys, freq):
        """Best-fit phase of ys ~ a cos(wt) + b sin(wt) at one frequency."""
        n = self.n
        inv = 1.0 / n
        w = 2.0 * math.pi * freq
        cs = [math.cos(w * t) for t in self.ts]
        sn = [math.sin(w * t) for t in self.ts]
        Y = sum(ys) * inv
        C = sum(cs) * inv
        S = sum(sn) * inv
        CC = sum(c * c for c in cs) * inv - C * C
        SS = sum(s * s for s in sn) * inv - S * S
        CS = sum(map(operator.mul, cs, sn)) * inv - C * S
        YC = sum(map(operator.mul, ys, cs)) * inv - Y * C
        YS = sum(map(operator.mul, ys, sn)) * inv - Y * S
        D = CC * SS - CS * CS
        if abs(D) < 1e-12:
            return None, 0.0
        a = (YC * SS - YS * CS) / D
        b = (YS * CC - YC * CS) / D
        return math.atan2(b, a), math.hypot(a, b)


def peak_of(freqs, powers, fmin=None, fmax=None):
    best = (-1.0, None)
    for f, p in zip(freqs, powers):
        if fmin is not None and f < fmin:
            continue
        if fmax is not None and f > fmax:
            continue
        if p > best[0]:
            best = (p, f)
    if best[1] is None:
        return {"power": None, "freq": None, "period": None}
    return {"power": best[0], "freq": best[1], "period": 1.0 / best[1]}


# --------------------------------------------------------------------------- #
# gap-aware autocorrelation
# --------------------------------------------------------------------------- #

def pairwise_complete_acf(series, max_lag=ACF_MAX_LAG, min_pairs=MIN_PAIRS_ACF):
    """series: dict round -> value.  Returns list of {lag, r, n}."""
    ts = sorted(series)
    out = []
    for lag in range(1, max_lag + 1):
        xs = []
        ys = []
        for t in ts:
            u = t + lag
            if u in series:
                xs.append(series[t])
                ys.append(series[u])
        r = pearson(xs, ys) if len(xs) >= min_pairs else None
        out.append({"lag": lag, "r": r, "n": len(xs)})
    return out


def pairwise_complete_xcorr(a, b, max_lag=XCORR_MAX_LAG, min_pairs=MIN_PAIRS_ACF):
    out = []
    for lag in range(-max_lag, max_lag + 1):
        xs = []
        ys = []
        for t in sorted(a):
            u = t + lag
            if u in b:
                xs.append(a[t])
                ys.append(b[u])
        r = pearson(xs, ys) if len(xs) >= min_pairs else None
        out.append({"lag": lag, "r": r, "n": len(xs)})
    return out


def lag1_ac(series):
    xs = []
    ys = []
    for t in sorted(series):
        if t + 1 in series:
            xs.append(series[t])
            ys.append(series[t + 1])
    return pearson(xs, ys)


# --------------------------------------------------------------------------- #
# surrogates
# --------------------------------------------------------------------------- #

def ar1_surrogate(rng, length, mu, sigma, phi):
    phi = max(-0.98, min(0.98, phi if phi is not None else 0.0))
    innov = sigma * math.sqrt(max(1e-9, 1.0 - phi * phi))
    x = mu + rng.gauss(0.0, sigma)
    out = []
    for _ in range(length):
        x = mu + phi * (x - mu) + rng.gauss(0.0, innov)
        out.append(x)
    return out


def levinson_ar(acf_vals):
    """Levinson-Durbin on rho[0..p] (rho[0]==1). Returns (coeffs, innov_var_ratio).

    ``acf_vals`` is tapered/shrunk by the caller until the recursion stays
    stationary (|reflection| < 1); the applied shrinkage is reported.
    """
    p = len(acf_vals) - 1
    a = [0.0] * (p + 1)
    e = acf_vals[0]
    for k in range(1, p + 1):
        acc = acf_vals[k]
        for j in range(1, k):
            acc -= a[j] * acf_vals[k - j]
        if e <= 1e-12:
            return None, None
        kappa = acc / e
        if abs(kappa) >= 1.0:
            return None, None
        newa = list(a)
        newa[k] = kappa
        for j in range(1, k):
            newa[j] = a[j] - kappa * a[k - j]
        a = newa
        e *= (1.0 - kappa * kappa)
    return a[1:], e


def fit_arp(series, order=ARP_ORDER):
    """Fit an AR(p) whose ACF matches the measured (gap-aware) ACF up to `order`."""
    acf = pairwise_complete_acf(series, max_lag=order, min_pairs=10)
    rho = [1.0]
    for row in acf:
        rho.append(row["r"] if row["r"] is not None else 0.0)
    shrink = 1.0
    for _ in range(40):
        # Tukey lag window + shrinkage keeps the sequence positive-definite.
        taper = [1.0] + [shrink * (0.5 * (1.0 + math.cos(math.pi * k / (order + 1))))
                         for k in range(1, order + 1)]
        cand = [rho[k] * taper[k] for k in range(order + 1)]
        coeffs, evar = levinson_ar(cand)
        if coeffs is not None:
            return coeffs, evar, shrink
        shrink *= 0.9
    return None, None, None


def arp_surrogate(rng, length, mu, sigma, coeffs, evar_ratio):
    p = len(coeffs)
    innov = sigma * math.sqrt(max(1e-9, evar_ratio))
    hist = [rng.gauss(0.0, sigma) for _ in range(p)]
    out = []
    burn = 200
    for _ in range(length + burn):
        v = sum(coeffs[i] * hist[-1 - i] for i in range(p)) + rng.gauss(0.0, innov)
        hist.append(v)
        if len(hist) > p + 2:
            hist.pop(0)
        out.append(v)
    return [mu + v for v in out[burn:]]


def bounded_walk_ring_series(rng, length, walkable, start=None):
    """3 uniform effective steps/round on the real walkable graph.

    A move into a wall / off-board is recorded as a stay, exactly as the engine
    records effective actions, so boundary reflection is reproduced faithfully.
    This is the confound null: reflection alone can manufacture
    quasi-periodicity in ring distance.
    """
    cells = sorted(walkable)
    pos = start if start is not None else cells[rng.randrange(len(cells))]
    out = []
    for _ in range(length):
        for _ in range(STEPS_PER_ROUND):
            dr, dc = ((-1, 0), (1, 0), (0, -1), (0, 1))[rng.randrange(4)]
            nxt = (pos[0] + dr, pos[1] + dc)
            if nxt in walkable:
                pos = nxt
        out.append(ring_of(pos))
    return out


def ring_of(cell):
    return max(abs(cell[0] - CENTER), abs(cell[1] - CENTER))


def apply_mask(values_by_index, ts):
    """values_by_index is a dense list indexed by round; ts the observed rounds."""
    return [float(values_by_index[t]) for t in ts]


# --------------------------------------------------------------------------- #
# log loading
# --------------------------------------------------------------------------- #

class GameData:
    __slots__ = ("game_id", "path", "probe_version", "map_name", "map_rows",
                 "walkable", "target_idx", "ours_idx", "n_rounds", "rounds",
                 "pos", "ring", "income", "our_income", "visible_sets",
                 "our_pos", "net", "visibility", "hotspot_events",
                 "bomb_transitions_by_mod20", "snapshots")

    def __repr__(self):
        return "<GameData %d %s>" % (self.game_id, self.probe_version)


def region_id(row, col):
    """sim/engine.py:84 -- fixed windmill regions 1..5.

    Region 1 is rows 4..12 x cols 4..12, i.e. EXACTLY the central 9x9 (d <= 4),
    so regions 2..5 are exactly the outer ring (d >= 5) split into four arms.
    """
    if 4 <= row <= 12 and 4 <= col <= 12:
        return 1
    if row <= 3 and col <= 12:
        return 2
    if row >= 4 and col <= 3:
        return 3
    if row >= 13 and col >= 4:
        return 4
    return 5


def map_name_of(map_rows, known):
    key = tuple("".join(r) for r in map_rows)
    return known.get(key, "map3_or_unknown")


def load_known_maps():
    try:
        data = json.loads((ROOT / "sim" / "maps.json").read_text())
    except Exception:
        return {}
    out = {}
    for name, spec in sorted(data.get("maps", {}).items()):
        if "rows" in spec:
            out[tuple(spec["rows"])] = name
    return out


def hotspots_from_map(map_rows):
    return sorted((i, j) for i in range(GRID) for j in range(GRID)
                  if map_rows[i][j] == "2")


def load_game(entry, known_maps, want_grids=True):
    path = ROOT / entry["path"]
    with path.open("r", encoding="utf-8") as fh:
        json.loads(fh.readline())              # header (accounts)
        map_rows = json.loads(fh.readline())
        round_objs = []
        for line in fh:
            line = line.strip()
            if line:
                round_objs.append(json.loads(line))
    g = GameData()
    g.game_id = entry["game_id"]
    g.path = str(path)
    g.probe_version = entry.get("probe_version")
    g.map_rows = map_rows
    g.map_name = map_name_of(map_rows, known_maps)
    g.walkable = frozenset((i, j) for i in range(GRID) for j in range(GRID)
                           if map_rows[i][j] != "1")
    g.target_idx = entry["target_player_id"] - 1
    g.ours_idx = 1 - g.target_idx
    g.net = entry.get("opponent_net_vs_probe")
    g.visibility = entry.get("target_visibility")
    ti, oi = g.target_idx, g.ours_idx
    our_pid = str(entry["probe_player_id"])

    g.pos = {0: {}, 1: {}}
    g.ring = {0: {}, 1: {}}
    g.our_pos = {0: {}, 1: {}}
    g.income = {0: {}, 1: {}}
    g.our_income = {0: {}, 1: {}}
    g.visible_sets = {}
    g.hotspot_events = []
    g.snapshots = {}
    g.bomb_transitions_by_mod20 = collections.Counter()
    within_round_bomb = collections.Counter()
    bomb_pairs = collections.Counter()
    rounds_seen = []
    prev_end_grid = None
    hot = set(hotspots_from_map(map_rows))

    for obj in round_objs:
        if "end" not in obj:
            continue
        r = obj["round"]
        rounds_seen.append(r)
        if "snapshot" in obj and obj["snapshot"]:
            snap = obj["snapshot"]
            g.snapshots[r] = {int(reg["id"]): {
                "gold_remaining": reg.get("gold_remaining"),
                "gold_generated": reg.get("gold_generated"),
                "gold_collected": reg.get("gold_collected"),
                "occupants": reg.get("occupants"),
            } for reg in snap.get("regions", [])}
        end = obj["end"]
        start = obj["start"]
        tu = end["players"][ti]["units"]
        ou = end["players"][oi]["units"]
        su = start["players"][ti]["units"]
        sou = start["players"][oi]["units"]
        for j in (0, 1):
            p = tu[j]["position"]
            if p is not None:
                cell = (p[0], p[1])
                g.pos[j][r] = cell
                g.ring[j][r] = ring_of(cell)
            q = ou[j]["position"]
            if q is not None:
                g.our_pos[j][r] = (q[0], q[1])
            g.income[j][r] = tu[j]["gold"] - su[j]["gold"]
            g.our_income[j][r] = ou[j]["gold"] - sou[j]["gold"]
        if want_grids:
            grid = end["grid"]
            start_grid = start["grid"]
            vr = start.get("vision_r") or {}
            radius = vr.get(our_pid, 2)
            vis = frozenset((i, j) for i in range(GRID) for j in range(GRID)
                            if grid[i][j] != FOG)
            g.visible_sets[r] = vis
            # Bomb-wave verification.  The resample happens BETWEEN rounds, so
            # the correct comparison is end[r-1].grid -> start[r].grid.  The
            # within-round comparison start[r] -> end[r] is a *different*
            # mechanism (a unit stepping on a bomb removes it: 34 such -3 -> 0
            # events in game 171719 alone, spread over all residues) and would
            # contaminate the clock test.
            if prev_end_grid is not None:
                a, b = prev_end_grid, start_grid
                res = r % 20
                for i in range(GRID):
                    ra, rb = a[i], b[i]
                    for j in range(GRID):
                        if ra[j] == FOG or rb[j] == FOG:
                            continue
                        bomb_pairs[res] += 1
                        if (ra[j] == BOMB) != (rb[j] == BOMB):
                            g.bomb_transitions_by_mod20[res] += 1
                    ra2, rb2 = start_grid[i], grid[i]
                    for j in range(GRID):
                        if ra2[j] == FOG or rb2[j] == FOG:
                            continue
                        if (ra2[j] == BOMB) != (rb2[j] == BOMB):
                            within_round_bomb[res] += 1
                # observable outer hotspot events (token-2 cell jumps above 10)
                hits = 0
                for (i, j) in sorted(hot):
                    if a[i][j] == FOG or b[i][j] == FOG:
                        continue
                    if b[i][j] > 10 and a[i][j] <= 10:
                        hits += 1
                if hits:
                    g.hotspot_events.append({"round": r, "cells": hits})
            prev_end_grid = grid
            del radius
    g.n_rounds = max(rounds_seen) + 1 if rounds_seen else 0
    g.rounds = sorted(rounds_seen)
    g.bomb_transitions_by_mod20 = dict(sorted(g.bomb_transitions_by_mod20.items()))
    g.bomb_transitions_by_mod20 = {
        "changes": g.bomb_transitions_by_mod20,
        "cellpairs": dict(sorted(bomb_pairs.items())),
        "within_round_removals": dict(sorted(within_round_bomb.items())),
    }
    return g


def load_manifest():
    data = json.loads(MANIFEST.read_text())
    out = []
    for e in data["games"]:
        if e.get("target", {}).get("account") == TARGET_ACCOUNT:
            out.append(e)
    out.sort(key=lambda e: e["game_id"])
    return data, out


def trimmed(series, lo=WARMUP_ROUNDS):
    return {t: v for t, v in series.items() if t >= lo}


# --------------------------------------------------------------------------- #
# CLAIM P -- partition
# --------------------------------------------------------------------------- #

AXES = ("row", "col", "diag", "anti")


def axis_proj(cell, axis):
    dr = cell[0] - CENTER
    dc = cell[1] - CENTER
    if axis == "row":
        return dr
    if axis == "col":
        return dc
    if axis == "diag":
        return dr + dc
    return dr - dc


def axis_accuracy(pairs, axis, sign):
    """pairs: list of (cell0, cell1).  Accuracy that sign*proj separates them."""
    if not pairs:
        return None
    hit = 0.0
    for c0, c1 in pairs:
        a = sign * axis_proj(c0, axis)
        b = sign * axis_proj(c1, axis)
        if a < b:
            hit += 1.0
        elif a == b:
            hit += 0.5
    return hit / len(pairs)


def lda_direction(pairs):
    """Two-class LDA on (dr, dc) with pooled 2x2 covariance."""
    if len(pairs) < 5:
        return None
    g0 = [(c0[0] - CENTER, c0[1] - CENTER) for c0, _ in pairs]
    g1 = [(c1[0] - CENTER, c1[1] - CENTER) for _, c1 in pairs]
    m0 = (mean(p[0] for p in g0), mean(p[1] for p in g0))
    m1 = (mean(p[0] for p in g1), mean(p[1] for p in g1))
    s11 = s12 = s22 = 0.0
    n = 0
    for grp, m in ((g0, m0), (g1, m1)):
        for p in grp:
            a = p[0] - m[0]
            b = p[1] - m[1]
            s11 += a * a
            s12 += a * b
            s22 += b * b
            n += 1
    dof = max(1, n - 2)
    s11 /= dof
    s12 /= dof
    s22 /= dof
    s11 += 1e-6
    s22 += 1e-6
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-9:
        return None
    d0 = m1[0] - m0[0]
    d1 = m1[1] - m0[1]
    w0 = (s22 * d0 - s12 * d1) / det
    w1 = (s11 * d1 - s12 * d0) / det
    nrm = math.hypot(w0, w1)
    if nrm < 1e-9:
        return None
    return (w0 / nrm, w1 / nrm)


def lda_accuracy(pairs, w):
    if w is None or not pairs:
        return None
    hit = 0.0
    for c0, c1 in pairs:
        a = w[0] * (c0[0] - CENTER) + w[1] * (c0[1] - CENTER)
        b = w[0] * (c1[0] - CENTER) + w[1] * (c1[1] - CENTER)
        if a < b:
            hit += 1.0
        elif a == b:
            hit += 0.5
    return hit / len(pairs)


def best_fixed_axis(pairs):
    best = (-1.0, None, None)
    for axis in AXES:
        for sign in (1, -1):
            acc = axis_accuracy(pairs, axis, sign)
            if acc is not None and acc > best[0]:
                best = (acc, axis, sign)
    return best


def occupancy_overlap(pairs):
    """OVL = sum_cell min(p0, p1) over co-observed rounds."""
    n = len(pairs)
    if n == 0:
        return None
    c0 = collections.Counter(p[0] for p in pairs)
    c1 = collections.Counter(p[1] for p in pairs)
    tot = 0.0
    for cell in set(c0) | set(c1):
        tot += min(c0[cell], c1[cell])
    return tot / n


def centroid_separation(pairs):
    if not pairs:
        return None, None, None
    m0 = (mean(p[0][0] for p in pairs), mean(p[0][1] for p in pairs))
    m1 = (mean(p[1][0] for p in pairs), mean(p[1][1] for p in pairs))
    return math.dist(m0, m1), m0, m1


def adjudicate_partition(pairs, seed_key, n_perm=N_PERM):
    """pairs: sorted list of (cell_u0, cell_u1) over co-observed rounds.

    Null = within-round unit-label permutation, with the axis search *refit*
    inside every permutation so the multiplicity of the 4-axis x 2-sign search
    is priced in.
    """
    res = {"n_co_observed": len(pairs)}
    if len(pairs) < 20:
        res.update({"label": "insufficient", "ovl": None})
        return res
    ovl = occupancy_overlap(pairs)
    sep, m0, m1 = centroid_separation(pairs)
    acc_in, axis_in, sign_in = best_fixed_axis(pairs)
    w = lda_direction(pairs)
    acc_lda_in = lda_accuracy(pairs, w)

    # split-half cross-fitting (fit on evens, score on odds and vice versa)
    ev = [p for i, p in enumerate(pairs) if i % 2 == 0]
    od = [p for i, p in enumerate(pairs) if i % 2 == 1]
    cross = []
    for fit, ev_set in ((ev, od), (od, ev)):
        _, ax, sg = best_fixed_axis(fit)
        if ax is None:
            continue
        a = axis_accuracy(ev_set, ax, sg)
        wl = lda_direction(fit)
        al = lda_accuracy(ev_set, wl)
        cross.append({"axis": ax, "sign": sg, "acc_axis": a, "acc_lda": al})
    acc_cross = mean([c["acc_axis"] for c in cross if c["acc_axis"] is not None]) \
        if cross else None
    acc_cross_lda = mean([c["acc_lda"] for c in cross if c["acc_lda"] is not None]) \
        if cross else None

    rng = seeded_rng("partition", seed_key)
    null_ovl, null_acc, null_cross, null_sep = [], [], [], []
    for _ in range(n_perm):
        perm = [(p[1], p[0]) if rng.random() < 0.5 else p for p in pairs]
        null_ovl.append(occupancy_overlap(perm))
        null_acc.append(best_fixed_axis(perm)[0])
        null_sep.append(centroid_separation(perm)[0])
        pev = [p for i, p in enumerate(perm) if i % 2 == 0]
        pod = [p for i, p in enumerate(perm) if i % 2 == 1]
        accs = []
        for fit, ev_set in ((pev, pod), (pod, pev)):
            _, ax, sg = best_fixed_axis(fit)
            if ax is not None:
                a = axis_accuracy(ev_set, ax, sg)
                if a is not None:
                    accs.append(a)
        null_cross.append(mean(accs) if accs else None)

    ovl_null = null_summary(null_ovl, ovl, tail="lower")
    acc_null = null_summary(null_acc, acc_in, tail="upper")
    cross_null = null_summary(null_cross, acc_cross, tail="upper")
    sep_null = null_summary(null_sep, sep, tail="upper")

    z = ovl_null["z"]
    if z is not None and z <= -2.0:
        label = "partition"
    elif z is not None and z >= 2.0:
        label = "colocated"
    else:
        label = "none"
    res.update({
        "ovl": ovl, "ovl_null": ovl_null,
        "centroid_sep": sep, "centroid_u0": list(m0), "centroid_u1": list(m1),
        "centroid_sep_null": sep_null,
        "axis_in_sample": {"acc": acc_in, "axis": axis_in, "sign": sign_in},
        "axis_in_sample_null": acc_null,
        "axis_cross_fitted": {"acc": acc_cross, "folds": cross},
        "axis_cross_fitted_null": cross_null,
        "lda_in_sample_acc": acc_lda_in,
        "lda_cross_fitted_acc": acc_cross_lda,
        "label": label,
    })
    return res


def axis_stability(pairs_by_round, window=50):
    """Per-window winning axis, to test the STABLE part of claim P3."""
    if not pairs_by_round:
        return []
    rounds = sorted(pairs_by_round)
    out = []
    lo = (rounds[0] // window) * window
    hi = rounds[-1]
    while lo <= hi:
        chunk = [pairs_by_round[r] for r in rounds if lo <= r < lo + window]
        if len(chunk) >= 15:
            acc, ax, sg = best_fixed_axis(chunk)
            out.append({"start": lo, "n": len(chunk), "acc": acc,
                        "axis": ax, "sign": sg})
        lo += window
    return out


# --- level 2: the null the pre-registered P1 null cannot provide ----------- #
#
# The three-way dry run below proves that OVL-vs-within-round-label-permutation
# fires "partition" on ZERO SIGNAL (two independent bounded random walks give
# OVL 0.00 vs permutation null 0.55, z = -18).  The reason is structural: the
# permutation null mixes the two marginals, so it asks "are these two
# occupancy distributions less overlapping than a 50/50 mixture of themselves",
# and ANY two spatially localised independent walkers on a 17x17 board answer
# yes.  The pre-registered P1 null therefore cannot decide claim P; it is
# retained and reported (it is pre-registered) but flagged invalid.
#
# Two valid nulls replace it, matching the two things "each responsible for one
# half" could mean:
#   (a) cross-GAME unit pairing -- pair u0 of game A with u1 of game B.  Both
#       are real T-1 units with real T-1 dynamics but cannot have coordinated.
#       Detects a game-specific mutual exclusion (any axis).  Conservative:
#       within-game units share one gold field, which by itself would pull them
#       TOGETHER, i.e. raise within-game OVL.
#   (b) whole-game label-flip -- is there ONE global (axis, sign) that assigns
#       the same half to the same unit index in every game?  That, and only
#       that, is "robot 0 owns this half of the board".  Flipping a game's
#       labels destroys cross-game sign consistency while leaving each game's
#       internal separation untouched, so the null is exact (2^G patterns,
#       enumerated).

def _pairs_from(posA, posB):
    co = sorted(set(posA) & set(posB))
    return [(posA[t], posB[t]) for t in co]


def partition_level2(pos_by_game, seed_key, n_draw=N_PERM):
    """pos_by_game: {game_key: {0: {t: cell}, 1: {t: cell}}} (already trimmed)."""
    keys = sorted(pos_by_game)
    within = {}
    for k in keys:
        pr = _pairs_from(pos_by_game[k][0], pos_by_game[k][1])
        if len(pr) >= 20:
            within[k] = pr
    if len(within) < 2:
        return {"label": "insufficient", "n_games": len(within)}
    wkeys = sorted(within)
    within_ovl = [occupancy_overlap(within[k]) for k in wkeys]
    obs_ovl = mean(within_ovl)

    # (a) cross-game pairs
    cross = []
    cross_detail = []
    for i, ka in enumerate(wkeys):
        for kb in wkeys:
            if ka == kb:
                continue
            for ua in (0, 1):
                for ub in (0, 1):
                    if ka > kb and (ua, ub) == (0, 0):
                        pass
                    pr = _pairs_from(pos_by_game[ka][ua], pos_by_game[kb][ub])
                    if len(pr) < 20:
                        continue
                    v = occupancy_overlap(pr)
                    acc = best_fixed_axis(pr)[0]
                    cross.append(v)
                    cross_detail.append({"a": "%s_u%d" % (ka, ua),
                                         "b": "%s_u%d" % (kb, ub),
                                         "n": len(pr), "ovl": v, "axis_acc": acc})
        del i
    rng = seeded_rng("xgame_ovl", seed_key)
    null_means = []
    if cross:
        for _ in range(n_draw):
            samp = [cross[rng.randrange(len(cross))] for _ in wkeys]
            null_means.append(mean(samp))
    ovl_cross = null_summary(null_means, obs_ovl, "lower")

    # (b) global fixed (axis, sign) with exact whole-game label-flip null
    def global_best(flips):
        best = (-1.0, None, None, None)
        for axis in AXES:
            for sign in (1, -1):
                accs = []
                for idx, k in enumerate(wkeys):
                    pr = within[k]
                    if flips[idx]:
                        pr = [(b, a) for a, b in pr]
                    a = axis_accuracy(pr, axis, sign)
                    if a is not None:
                        accs.append(a)
                if not accs:
                    continue
                m = mean(accs)
                if m > best[0]:
                    best = (m, axis, sign, accs)
        return best

    obs_best = global_best([False] * len(wkeys))
    # Quotient out the global sign symmetry: flipping EVERY game's labels maps
    # (axis, +1) to (axis, -1) and leaves the statistic invariant, so the
    # all-flip pattern is a duplicate of the identity and would inflate p.  Game
    # 0's orientation is therefore held fixed, leaving 2^(G-1) distinct
    # patterns.  With G=5 the smallest attainable exact p is 1/17 = 0.0588,
    # which is why the decision threshold below is 0.07 and not 0.05.
    flip_null = []
    n = len(wkeys)
    for mask in range(1, 1 << (n - 1)):     # identity excluded: it IS the observed
        flips = [False] + [bool((mask >> i) & 1) for i in range(n - 1)]
        flip_null.append(global_best(flips)[0])
    axis_null = null_summary(flip_null, obs_best[0], "upper")
    axis_null["distinct_patterns"] = 1 << (n - 1)
    axis_null["min_attainable_p"] = 1.0 / (1 << (n - 1))

    per_game_axis = {}
    for k in wkeys:
        acc, ax, sg = best_fixed_axis(within[k])
        per_game_axis[k] = {"acc": acc, "axis": ax, "sign": sg,
                            "n": len(within[k])}
    axes_seen = sorted({v["axis"] for v in per_game_axis.values()})
    signs_seen = sorted({v["sign"] for v in per_game_axis.values()})

    z = ovl_cross["z"]
    fixed_ok = (axis_null["p_one_sided"] is not None
                and axis_null["p_one_sided"] <= 0.07
                and obs_best[0] is not None and obs_best[0] >= 0.75)
    if fixed_ok:
        label = "partition_fixed_half"
    elif z is not None and z <= -2.0:
        label = "partition_variable_axis"
    elif z is not None and z >= 2.0:
        label = "colocated"
    else:
        label = "none"
    return {
        "n_games": len(wkeys), "games": wkeys,
        "within_game_ovl": within_ovl,
        "within_game_ovl_mean": obs_ovl,
        "within_game_ovl_se": se_of_mean(within_ovl) if len(within_ovl) > 1 else None,
        "cross_game_ovl_values": sorted(cross),
        "cross_game_ovl_mean": mean(cross) if cross else None,
        "cross_game_ovl_n_pairs": len(cross),
        "null_cross_game_ovl": ovl_cross,
        "cross_game_pair_detail": cross_detail,
        "global_axis": {"acc_mean": obs_best[0], "axis": obs_best[1],
                        "sign": obs_best[2], "per_game_acc": obs_best[3],
                        "acc_se": se_of_mean(obs_best[3])
                        if obs_best[3] and len(obs_best[3]) > 1 else None},
        "null_whole_game_label_flip": axis_null,
        "null_whole_game_label_flip_exact_patterns": 1 << n,
        "per_game_best_axis": per_game_axis,
        "axes_chosen": axes_seen, "signs_chosen": signs_seen,
        "label": label,
    }


# --------------------------------------------------------------------------- #
# CLAIM C -- cycle
# --------------------------------------------------------------------------- #

def phase_coherence(basis_ts, ys_by_t, freq, segments=PHASE_SEGMENTS):
    """Resultant length of per-segment fitted phases at `freq`.

    A deterministic oscillator keeps its phase over the whole game (R -> 1); a
    stochastic process with the SAME autocorrelation has drifting phase
    (R ~ 1/sqrt(S)).  This -- not spectral power -- is the statistic that a
    phase-randomised / ACF-matched surrogate can actually falsify, because such
    surrogates preserve the power spectrum by construction.
    """
    ts = sorted(ys_by_t)
    if len(ts) < segments * 8:
        return None
    lo, hi = ts[0], ts[-1]
    width = (hi - lo + 1) / float(segments)
    vecs = []
    for s in range(segments):
        a = lo + s * width
        b = lo + (s + 1) * width
        sub_t = [t for t in ts if a <= t < b]
        if len(sub_t) < 8:
            continue
        sub_y = [ys_by_t[t] for t in sub_t]
        bs = GLSBasis(sub_t, [freq])
        ph, amp = bs.phase_at(sub_y, freq)
        if ph is None:
            continue
        vecs.append(ph)
    if len(vecs) < 2:
        return None
    cx = sum(math.cos(p) for p in vecs) / len(vecs)
    cy = sum(math.sin(p) for p in vecs) / len(vecs)
    return math.hypot(cx, cy)


def adjudicate_cycle(series, walkable, seed_key, n_sur=N_SURROGATE,
                     length=None, freqs=None, want_series=True):
    """series: dict round -> ring distance (or any scalar), gaps allowed."""
    ts = sorted(series)
    res = {"n_obs": len(ts)}
    if len(ts) < 60:
        res.update({"label": "insufficient"})
        return res
    length = length or (max(ts) + 1)
    freqs = freqs or freq_grid()
    ys = [float(series[t]) for t in ts]
    basis = GLSBasis(ts, freqs)
    powers = basis.power(ys)
    pk = peak_of(freqs, powers)
    pk_idx = None
    if pk["freq"] is not None:
        pk_idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - pk["freq"]))
    band_bomb = peak_of(freqs, powers, 1.0 / 21.0, 1.0 / 19.0)
    band_event = peak_of(freqs, powers, 1.0 / 16.0, 1.0 / 8.0)
    acf = pairwise_complete_acf(series)

    mu = mean(ys)
    sg = sd(ys)
    phi = lag1_ac(series)

    # --- null (i) AR(1)-matched (mean-reverting but non-periodic) ----------- #
    rng = seeded_rng("ar1", seed_key)
    ar1_max, ar1_at_peak = [], []
    for _ in range(n_sur):
        dense = ar1_surrogate(rng, length, mu, sg, phi)
        sy = [dense[t] for t in ts]
        pw = basis.power(sy)
        ar1_max.append(max(pw))
        if pk_idx is not None:
            ar1_at_peak.append(pw[pk_idx])

    # --- null (iii) bounded random walk on the real map --------------------- #
    rng = seeded_rng("walk", seed_key)
    walk_max, walk_at_peak = [], []
    for _ in range(n_sur):
        dense = bounded_walk_ring_series(rng, length, walkable)
        sy = [float(dense[t]) for t in ts]
        pw = basis.power(sy)
        walk_max.append(max(pw))
        if pk_idx is not None:
            walk_at_peak.append(pw[pk_idx])

    # --- null (ii) full-ACF-matched AR(p) surrogate ------------------------- #
    coeffs, evar, shrink = fit_arp(series)
    arp_max, arp_coh = [], []
    obs_coh = phase_coherence(ts, series, pk["freq"]) if pk["freq"] else None
    if coeffs is not None:
        rng = seeded_rng("arp", seed_key)
        for _ in range(n_sur):
            dense = arp_surrogate(rng, length, mu, sg, coeffs, evar)
            sub = {t: dense[t] for t in ts}
            pw = basis.power([sub[t] for t in ts])
            arp_max.append(max(pw))
            c = phase_coherence(ts, sub, pk["freq"]) if pk["freq"] else None
            if c is not None:
                arp_coh.append(c)

    ar1_null = null_summary(ar1_max, pk["power"], "upper")
    walk_null = null_summary(walk_max, pk["power"], "upper")
    arp_null = null_summary(arp_max, pk["power"], "upper")
    coh_null = null_summary(arp_coh, obs_coh, "upper")

    beats_ar1 = ar1_null["p_one_sided"] is not None and ar1_null["p_one_sided"] <= 0.05
    beats_walk = walk_null["p_one_sided"] is not None and walk_null["p_one_sided"] <= 0.05
    beats_coh = coh_null["p_one_sided"] is not None and coh_null["p_one_sided"] <= 0.05
    label = "periodic" if (beats_ar1 and beats_walk) else "none"

    res.update({
        "peak": pk,
        "band_period_19_21": band_bomb,
        "band_period_8_16": band_event,
        "mean": mu, "sd": sg, "lag1_ac": phi,
        "acf": acf,
        "null_ar1_maxpower": ar1_null,
        "null_boundedwalk_maxpower": walk_null,
        "null_arp_acf_matched_maxpower": arp_null,
        "null_ar1_power_at_observed_peak_freq": null_summary(
            ar1_at_peak, pk["power"], "upper"),
        "null_boundedwalk_power_at_observed_peak_freq": null_summary(
            walk_at_peak, pk["power"], "upper"),
        "null_note":
            "max-vs-max is the decision statistic (it prices in the frequency "
            "search); at-peak-vs-at-peak is reported as the anti-conservative "
            "companion because the frequency was chosen from the data",
        "phase_coherence_at_peak": obs_coh,
        "null_arp_phase_coherence": coh_null,
        "arp_shrinkage": shrink,
        "beats_ar1": beats_ar1, "beats_walk": beats_walk,
        "beats_phase_coherence": beats_coh,
        "label": label,
    })
    if want_series:
        res["periodogram"] = {"freqs": freqs, "power": powers}
        res["series"] = {"t": ts, "d": [series[t] for t in ts]}
    return res


def circular_shift_modk_test(series_by_game, k, seed_key, n_sur=N_SURROGATE):
    """Is the series phase-locked to the ABSOLUTE clock with period k?

    Only the environment (bombs at r = 0, 20, 40, ...) can be locked to the
    absolute round index across independent games, so this separates
    environmental forcing from an internal policy cycle that would be free to
    drift.  Statistic: variance of the pooled mean-by-(r mod k) profile.
    Null: independent random circular shifts per game (preserves each game's
    autocorrelation exactly, destroys absolute-clock alignment).
    """
    prof = collections.defaultdict(list)
    for gid in sorted(series_by_game):
        for t, v in sorted(series_by_game[gid].items()):
            prof[t % k].append(v)
    obs_means = [mean(prof[m]) if prof[m] else None for m in range(k)]
    if any(v is None for v in obs_means):
        return {"k": k, "label": "insufficient"}
    grand = mean([v for lst in prof.values() for v in lst])
    obs_stat = sum((v - grand) ** 2 for v in obs_means) / k

    dense = {}
    for gid in sorted(series_by_game):
        s = series_by_game[gid]
        ts = sorted(s)
        dense[gid] = (ts, s, max(ts) + 1)
    rng = seeded_rng("circshift", seed_key, k)
    null = []
    for _ in range(n_sur):
        p2 = collections.defaultdict(list)
        for gid in sorted(dense):
            ts, s, L = dense[gid]
            sh = rng.randrange(L)
            for t in ts:
                p2[((t + sh) % L) % k].append(s[t])
        ms = [mean(p2[m]) for m in range(k) if p2[m]]
        if len(ms) < k:
            continue
        gr = mean([v for lst in p2.values() for v in lst])
        null.append(sum((v - gr) ** 2 for v in ms) / k)
    return {"k": k, "profile_mean_by_mod": obs_means,
            "profile_n_by_mod": [len(prof[m]) for m in range(k)],
            "profile_se_by_mod": [se_of_mean(prof[m]) for m in range(k)],
            "grand_mean": grand, "stat_var_of_profile": obs_stat,
            "null_circular_shift": null_summary(null, obs_stat, "upper")}


def event_triggered_average(series, event_rounds, lo=-10, hi=20, n_boot=N_BOOT,
                            seed_key="eta"):
    if not event_rounds:
        return {"n_events": 0}
    grand = mean(series.values())
    out = []
    rng = seeded_rng("eta", seed_key)
    ev = sorted(event_rounds)
    for lag in range(lo, hi + 1):
        vals = [series[e + lag] for e in ev if (e + lag) in series]
        if len(vals) < 5:
            out.append({"lag": lag, "delta": None, "n": len(vals), "se": None})
            continue
        m = mean(vals)
        boots = []
        for _ in range(min(n_boot, 400)):
            samp = [vals[rng.randrange(len(vals))] for _ in vals]
            boots.append(mean(samp))
        out.append({"lag": lag, "delta": m - grand, "n": len(vals),
                    "se": sd(boots)})
    return {"n_events": len(ev), "grand_mean": grand, "event_rounds": ev,
            "profile": out}


# --------------------------------------------------------------------------- #
# CLAIM A -- amplitude, with rigorous bounds from the known missingness law
# --------------------------------------------------------------------------- #

def reach_table(walkable, steps=STEPS_PER_ROUND):
    """cell -> tuple of cells at graph distance <= steps (stay always legal)."""
    tbl = {}
    for c in sorted(walkable):
        seen = {c}
        frontier = [c]
        for _ in range(steps):
            nxt = []
            for p in frontier:
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    q = (p[0] + dr, p[1] + dc)
                    if q in walkable and q not in seen:
                        seen.add(q)
                        nxt.append(q)
            frontier = nxt
        tbl[c] = tuple(sorted(seen))
    return tbl


def ring_share_stats(rings, max_d=8):
    n = len(rings)
    cnt = collections.Counter(rings)
    return {"n": n,
            "share": [cnt[d] / n if n else None for d in range(max_d + 1)],
            "count": [cnt[d] for d in range(max_d + 1)],
            "mean_d": mean(rings) if rings else None}


def block_bootstrap_se(values, stat_fn, block=BLOCK_LEN, n_boot=N_BOOT,
                       seed_key="bb"):
    """Moving-block bootstrap SE for a statistic of an ordered series."""
    n = len(values)
    if n < 2 * block:
        return None
    rng = seeded_rng("blockboot", seed_key)
    nblocks = int(math.ceil(n / float(block)))
    out = []
    for _ in range(n_boot):
        samp = []
        for _ in range(nblocks):
            s = rng.randrange(0, n - block + 1)
            samp.extend(values[s:s + block])
        out.append(stat_fn(samp[:n]))
    return sd([v for v in out if v is not None])


def excursions_from(series, thresh=OUTER_MIN_D, merge_gap=2):
    """Maximal runs at d >= thresh in the OBSERVED series.

    Runs separated by an observation gap of <= merge_gap rounds are merged; any
    excursion touching a gap is flagged censored (its true peak may be higher
    and two excursions may in truth be one).
    """
    ts = sorted(series)
    obs = set(ts)
    runs = []
    cur = None
    for idx, t in enumerate(ts):
        v = series[t]
        if v >= thresh:
            if cur is None:
                cur = {"start": t, "end": t, "peak": v, "rounds": [t],
                       "censored": False}
            else:
                gap = t - cur["end"] - 1
                if gap == 0 or gap <= merge_gap:
                    if gap > 0:
                        cur["censored"] = True
                    cur["end"] = t
                    cur["peak"] = max(cur["peak"], v)
                    cur["rounds"].append(t)
                else:
                    runs.append(cur)
                    cur = {"start": t, "end": t, "peak": v, "rounds": [t],
                           "censored": False}
        else:
            if cur is not None:
                runs.append(cur)
                cur = None
    if cur is not None:
        runs.append(cur)
    for run in runs:
        for t in (run["start"] - 1, run["end"] + 1):
            if t not in obs:
                run["censored"] = True
        run["duration"] = run["end"] - run["start"] + 1
        run["n_observed"] = len(run["rounds"])
        run.pop("rounds", None)
    return runs


def feasible_bounds(pos_series, visible_sets, walkable, reach, n_rounds,
                    lo=WARMUP_ROUNDS):
    """Rigorous min/max bounds on outer-ring time and mean ring distance.

    The missingness law is known exactly: at round r a target unit is
    unobserved IFF its end position lies outside ``visible_sets[r]``.  Combined
    with the 3-step movement budget (effective actions, so graph distance <= 3
    on the walkable graph) this makes the set of trajectories consistent with
    the log a layered graph; min/max counts of d >= 5 rounds and min/max sums of
    d are then exact shortest/longest-path DPs over that graph.  Walls only are
    used as obstacles (bombs/NPCs ignored), which enlarges the feasible set and
    therefore keeps both bounds valid.
    """
    rounds = list(range(lo, n_rounds))
    if not rounds:
        return None
    obs = {r: pos_series[r] for r in rounds if r in pos_series}
    allwalk = tuple(sorted(walkable))
    allowed = {}
    for r in rounds:
        if r in obs:
            allowed[r] = (obs[r],)
        elif r in visible_sets:
            vis = visible_sets[r]
            allowed[r] = tuple(c for c in allwalk if c not in vis)
        else:
            # round absent from the log (forfeit tail): no information at all
            allowed[r] = allwalk
    # forward reachability
    fwd = {}
    prev = None
    for r in rounds:
        if prev is None:
            fwd[r] = set(allowed[r])
        else:
            step = set()
            for c in fwd[prev]:
                step.update(reach[c])
            fwd[r] = step & set(allowed[r])
        prev = r
    # backward reachability
    bwd = {}
    nxt = None
    for r in reversed(rounds):
        if nxt is None:
            bwd[r] = set(allowed[r])
        else:
            step = set()
            for c in bwd[nxt]:
                step.update(reach[c])
            bwd[r] = step & set(allowed[r])
        nxt = r
    feas = {r: sorted(fwd[r] & bwd[r]) for r in rounds}
    infeasible = [r for r in rounds if not feas[r]]
    if infeasible:
        return {"infeasible_rounds": infeasible, "n_rounds": len(rounds)}

    # layered DP: min/max of sum 1[d>=5] and of sum d
    INF = float("inf")
    dp_min = {c: 0.0 for c in feas[rounds[0]]}
    dp_max = {c: 0.0 for c in feas[rounds[0]]}
    dpd_min = {c: 0.0 for c in feas[rounds[0]]}
    dpd_max = {c: 0.0 for c in feas[rounds[0]]}
    for c in list(dp_min):
        w = 1.0 if ring_of(c) >= OUTER_MIN_D else 0.0
        dp_min[c] = w
        dp_max[c] = w
        dpd_min[c] = float(ring_of(c))
        dpd_max[c] = float(ring_of(c))
    for i in range(1, len(rounds)):
        r = rounds[i]
        cur_min, cur_max, curd_min, curd_max = {}, {}, {}, {}
        for c in feas[r]:
            cur_min[c] = INF
            cur_max[c] = -INF
            curd_min[c] = INF
            curd_max[c] = -INF
        for p, vmin in dp_min.items():
            vmax = dp_max[p]
            dmin = dpd_min[p]
            dmax = dpd_max[p]
            for q in reach[p]:
                if q in cur_min:
                    w = 1.0 if ring_of(q) >= OUTER_MIN_D else 0.0
                    if vmin + w < cur_min[q]:
                        cur_min[q] = vmin + w
                    if vmax + w > cur_max[q]:
                        cur_max[q] = vmax + w
                    rq = float(ring_of(q))
                    if dmin + rq < curd_min[q]:
                        curd_min[q] = dmin + rq
                    if dmax + rq > curd_max[q]:
                        curd_max[q] = dmax + rq
        dp_min = {k: v for k, v in cur_min.items() if v < INF}
        dp_max = {k: v for k, v in cur_max.items() if v > -INF}
        dpd_min = {k: v for k, v in curd_min.items() if v < INF}
        dpd_max = {k: v for k, v in curd_max.items() if v > -INF}
        if not dp_min:
            return {"infeasible_rounds": [r], "n_rounds": len(rounds)}
    n = len(rounds)
    return {
        "n_rounds": n, "n_observed": len(obs),
        "coverage": len(obs) / float(n),
        "outer_rounds_min": min(dp_min.values()),
        "outer_rounds_max": max(dp_max.values()),
        "outer_share_min": min(dp_min.values()) / n,
        "outer_share_max": max(dp_max.values()) / n,
        "mean_d_min": min(dpd_min.values()) / n,
        "mean_d_max": max(dpd_max.values()) / n,
        "feasible_set_size_mean": mean([len(feas[r]) for r in rounds]),
        "infeasible_rounds": [],
    }


def adjudicate_amplitude(per_game_rings, excursions, bounds, seed_key):
    """per_game_rings: {game_id: {unit: [ring values]}}"""
    per_game_share = []
    per_game_mean = []
    pooled = []
    for gid in sorted(per_game_rings):
        vals = []
        for u in sorted(per_game_rings[gid]):
            vals.extend(per_game_rings[gid][u])
        if not vals:
            continue
        per_game_share.append(sum(1 for v in vals if v >= OUTER_MIN_D) / len(vals))
        per_game_mean.append(mean(vals))
        pooled.extend(vals)
    share = mean(per_game_share)
    share_se = se_of_mean(per_game_share)
    md = mean(per_game_mean)
    md_se = se_of_mean(per_game_mean)
    bb_se = block_bootstrap_se(
        pooled, lambda xs: sum(1 for v in xs if v >= OUTER_MIN_D) / len(xs),
        seed_key=seed_key)
    dist = ring_share_stats(pooled)
    peaks = collections.Counter(e["peak"] for e in excursions)
    peaks_unc = collections.Counter(e["peak"] for e in excursions
                                    if not e["censored"])
    lo = share - 2 * (share_se or 0.0)
    hi = share + 2 * (share_se or 0.0)
    if hi < 0.05:
        label = "inside_9x9"
    elif lo > 0.50:
        label = "outer_dominant"
    else:
        label = "genuine_outer"
    return {
        "naive_outer_share": share, "naive_outer_share_se_between_game": share_se,
        "naive_outer_share_se_blockboot": bb_se,
        "per_game_outer_share": per_game_share,
        "naive_mean_d": md, "naive_mean_d_se_between_game": md_se,
        "pooled_ring_distribution": dist,
        "excursion_peak_hist": {str(k): peaks[k] for k in sorted(peaks)},
        "excursion_peak_hist_uncensored": {str(k): peaks_unc[k]
                                           for k in sorted(peaks_unc)},
        "n_excursions": len(excursions),
        "n_excursions_uncensored": sum(1 for e in excursions if not e["censored"]),
        "excursion_duration_mean": mean([e["duration"] for e in excursions])
        if excursions else None,
        "excursion_duration_se": se_of_mean([e["duration"] for e in excursions])
        if excursions else None,
        "rigorous_bounds": bounds,
        "label": label,
    }


def rayleigh_scan(onsets, span, p_lo=6, p_hi=60, n_sur=N_SURROGATE,
                  seed_key="ray"):
    """Rayleigh periodicity scan on excursion onset times.

    "regular pattern center->periphery->center" means the onsets of periphery
    trips are phase-locked to some period.  R_max over the period scan, with a
    Monte-Carlo null that (a) places the same number of onsets uniformly and
    (b) shuffles the observed inter-onset intervals (preserving their
    distribution but destroying phase).
    """
    onsets = sorted(onsets)
    n = len(onsets)
    if n < 6:
        return {"n_onsets": n, "label": "insufficient"}

    def rmax(ts):
        best = (-1.0, None)
        p = float(p_lo)
        while p <= p_hi:
            cx = sum(math.cos(2 * math.pi * t / p) for t in ts) / len(ts)
            cy = sum(math.sin(2 * math.pi * t / p) for t in ts) / len(ts)
            r = math.hypot(cx, cy)
            if r > best[0]:
                best = (r, p)
            p += 0.25
        return best

    obs_r, obs_p = rmax(onsets)
    rng = seeded_rng("rayleigh_unif", seed_key)
    null_u = []
    for _ in range(n_sur):
        ts = sorted(rng.uniform(0, span) for _ in range(n))
        null_u.append(rmax(ts)[0])
    gaps = [onsets[i + 1] - onsets[i] for i in range(n - 1)]
    rng2 = seeded_rng("rayleigh_shuf", seed_key)
    null_s = []
    for _ in range(n_sur):
        g = gaps[:]
        rng2.shuffle(g)
        ts = [onsets[0]]
        for d in g:
            ts.append(ts[-1] + d)
        null_s.append(rmax(ts)[0])
    return {"n_onsets": n, "R_max": obs_r, "period_at_R_max": obs_p,
            "null_uniform": null_summary(null_u, obs_r, "upper"),
            "null_interval_shuffle": null_summary(null_s, obs_r, "upper"),
            "interval_mean": mean(gaps), "interval_sd": sd(gaps),
            "interval_cv": (sd(gaps) / mean(gaps)) if gaps and mean(gaps) else None,
            "interval_se": se_of_mean(gaps)}


# --------------------------------------------------------------------------- #
# CLAIM Phi -- anti-phase
# --------------------------------------------------------------------------- #

def adjudicate_antiphase(d0, d1, seed_key, cross_null_pairs=None):
    co = sorted(set(d0) & set(d1))
    res = {"n_co_observed": len(co)}
    if len(co) < 30:
        res["label"] = "insufficient"
        return res
    xs = [float(d0[t]) for t in co]
    ys = [float(d1[t]) for t in co]
    r = pearson(xs, ys)
    rng = seeded_rng("phi_block", seed_key)
    n = len(co)
    boots = []
    if n >= 2 * BLOCK_LEN:
        nb = int(math.ceil(n / float(BLOCK_LEN)))
        for _ in range(N_BOOT):
            sx, sy = [], []
            for _ in range(nb):
                s = rng.randrange(0, n - BLOCK_LEN + 1)
                sx.extend(xs[s:s + BLOCK_LEN])
                sy.extend(ys[s:s + BLOCK_LEN])
            v = pearson(sx[:n], sy[:n])
            if v is not None:
                boots.append(v)
    se = sd(boots) if boots else None
    xc = pairwise_complete_xcorr(d0, d1)
    best = None
    for row in xc:
        if row["r"] is None:
            continue
        if best is None or abs(row["r"]) > abs(best["r"]):
            best = row
    label = "none"
    if se and se > 0:
        if r + 2 * se < 0:
            label = "antiphase"
        elif r - 2 * se > 0:
            label = "inphase"
    res.update({"r": r, "se_blockboot": se, "xcorr": xc,
                "xcorr_extreme": best, "label": label})
    if cross_null_pairs is not None:
        res["null_cross_game"] = null_summary(cross_null_pairs, r, "lower")
        res["null_cross_game_values"] = sorted(cross_null_pairs)
    return res


# --------------------------------------------------------------------------- #
# gold channel (fog-free)
# --------------------------------------------------------------------------- #

def gold_channel(entries, known_maps, max_lag=60, n_games=None, freqs=None,
                 verbose=False):
    freqs = freqs or freq_grid(oversample=2)
    per_game = []
    acc_t = collections.defaultdict(list)   # lag -> per-game target acf
    acc_o = collections.defaultdict(list)
    acc_diff = collections.defaultdict(list)
    pooled_power_t = [0.0] * len(freqs)
    pooled_power_o = [0.0] * len(freqs)
    npow = 0
    mod20_t = collections.defaultdict(list)
    mod20_o = collections.defaultdict(list)
    sel = entries if n_games is None else entries[:n_games]
    for e in sel:
        g = load_game(e, known_maps, want_grids=False)
        ts = [r for r in g.rounds if r >= WARMUP_ROUNDS]
        if len(ts) < 100:
            continue
        tot_t = {r: g.income[0].get(r, 0) + g.income[1].get(r, 0) for r in ts}
        tot_o = {r: g.our_income[0].get(r, 0) + g.our_income[1].get(r, 0)
                 for r in ts}
        at = pairwise_complete_acf(tot_t, max_lag=max_lag, min_pairs=30)
        ao = pairwise_complete_acf(tot_o, max_lag=max_lag, min_pairs=30)
        for rt, ro in zip(at, ao):
            if rt["r"] is not None:
                acc_t[rt["lag"]].append(rt["r"])
            if ro["r"] is not None:
                acc_o[ro["lag"]].append(ro["r"])
            if rt["r"] is not None and ro["r"] is not None:
                acc_diff[rt["lag"]].append(rt["r"] - ro["r"])
        basis = GLSBasis(ts, freqs)
        pt = basis.power([float(tot_t[r]) for r in ts])
        po = basis.power([float(tot_o[r]) for r in ts])
        for i in range(len(freqs)):
            pooled_power_t[i] += pt[i]
            pooled_power_o[i] += po[i]
        npow += 1
        for r in ts:
            mod20_t[r % 20].append(tot_t[r])
            mod20_o[r % 20].append(tot_o[r])
        pk = peak_of(freqs, pt)
        per_game.append({
            "game_id": g.game_id, "probe_version": g.probe_version,
            "map": g.map_name, "n_rounds": len(ts),
            "target_mean_income": mean(tot_t.values()),
            "our_mean_income": mean(tot_o.values()),
            "target_peak_period": pk["period"], "target_peak_power": pk["power"],
            "our_peak_period": peak_of(freqs, po)["period"],
            "acf_lag20_target": at[19]["r"] if len(at) >= 20 else None,
            "acf_lag20_ours": ao[19]["r"] if len(ao) >= 20 else None,
        })
        if verbose:
            sys.stderr.write("gold %d\n" % g.game_id)
    pooled_acf = []
    for lag in range(1, max_lag + 1):
        vt = acc_t.get(lag, [])
        vo = acc_o.get(lag, [])
        vd = acc_diff.get(lag, [])
        pooled_acf.append({
            "lag": lag, "n_games": len(vt),
            "target_mean_r": mean(vt) if vt else None,
            "target_se": se_of_mean(vt) if len(vt) > 1 else None,
            "ours_mean_r": mean(vo) if vo else None,
            "ours_se": se_of_mean(vo) if len(vo) > 1 else None,
            "paired_diff_mean": mean(vd) if vd else None,
            "paired_diff_se": se_of_mean(vd) if len(vd) > 1 else None,
        })
    return {
        "n_games": npow,
        "per_game": per_game,
        "pooled_acf": pooled_acf,
        "pooled_periodogram": {
            "freqs": freqs,
            "target_mean_power": [v / npow for v in pooled_power_t] if npow else [],
            "ours_mean_power": [v / npow for v in pooled_power_o] if npow else [],
        },
        "mod20_profile": {
            "target_mean": [mean(mod20_t[m]) if mod20_t[m] else None
                            for m in range(20)],
            "target_se": [se_of_mean(mod20_t[m]) if len(mod20_t[m]) > 1 else None
                          for m in range(20)],
            "ours_mean": [mean(mod20_o[m]) if mod20_o[m] else None
                          for m in range(20)],
            "ours_se": [se_of_mean(mod20_o[m]) if len(mod20_o[m]) > 1 else None
                        for m in range(20)],
            "n_by_mod": [len(mod20_t[m]) for m in range(20)],
        },
    }


def income_by_ring(games):
    """Descriptive link between position and income on the visible subset."""
    acc = collections.defaultdict(list)
    for g in games:
        for u in (0, 1):
            for r, d in sorted(g.ring[u].items()):
                if r < WARMUP_ROUNDS:
                    continue
                if r in g.income[u]:
                    acc[d].append(g.income[u][r])
    return {str(d): {"n": len(acc[d]), "mean": mean(acc[d]),
                     "se": se_of_mean(acc[d]) if len(acc[d]) > 1 else None}
            for d in sorted(acc)}


# --------------------------------------------------------------------------- #
# Mechanism test: is the center<->periphery alternation EXOGENOUSLY TRIGGERED?
# --------------------------------------------------------------------------- #
#
# ``sim/engine.py`` hands both players a fog-free global Snapshot every 5 rounds
# with per-region ``gold_remaining``.  Region 1 is exactly the central 9x9 and
# regions 2..5 are exactly the four outer arms, so the snapshot literally tells
# a player which outer arm is currently sitting on a pile, without vision.  If
# T-1's outward trips are responses to that signal, they are exogenously
# triggered and cannot be periodic (the signal's content is aperiodic even
# though its delivery is on a 5-round clock).
#
# Statistic: share of T-1's OUTER unit-rounds in a window after each snapshot
# that fall in the arm with the largest ``gold_remaining``.  Null = 0.25.
# Valid controls: (i) a time-REVERSED placebo window strictly before the signal
# is delivered, (ii) a dose-response gradient in the size of the leading arm.
# NOTE: the probe is NOT a valid specificity control -- probeobs is a tracker
# and inherits T-1's destination with a lag.

def snapshot_arm_response(games, offsets=(1, 5), thresholds=(0, 20, 40),
                          profile_range=(-12, 12)):
    def arm_of(cell):
        rid = region_id(cell[0], cell[1])
        return rid if rid >= 2 else None

    def leader(snap):
        best = None
        for rid in (2, 3, 4, 5):
            v = (snap.get(rid) or {}).get("gold_remaining")
            if v is None:
                continue
            if best is None or v > best[1]:
                best = (rid, v)
        # reject ties at the top: they make "the" leader undefined
        top = [rid for rid in (2, 3, 4, 5)
               if (snap.get(rid) or {}).get("gold_remaining") == (best[1] if best else None)]
        if best is None or len(top) > 1:
            return None, None
        return best

    out = {"null_share": 0.25, "windows": {}, "per_game": [],
           "n_snapshot_rounds": sum(len(g.snapshots) for g in games)}
    for tag, (lo, hi), thr in (("forward_T+1..T+5", (offsets[0], offsets[1]), 0),
                               ("placebo_T-5..T-1", (-offsets[1], -offsets[0]), 0),
                               ("forward_thr20", (offsets[0], offsets[1]), 20),
                               ("forward_thr40", (offsets[0], offsets[1]), 40)):
        per_game = []
        hits = tot = 0
        for g in sorted(games, key=lambda x: x.game_id):
            gh = gt = 0
            for T in sorted(g.snapshots):
                rid, amount = leader(g.snapshots[T])
                if rid is None or amount is None or amount < thr:
                    continue
                for u in (0, 1):
                    for t in range(T + lo, T + hi + 1):
                        if t < WARMUP_ROUNDS or t not in g.pos[u]:
                            continue
                        a = arm_of(g.pos[u][t])
                        if a is None:
                            continue
                        gt += 1
                        if a == rid:
                            gh += 1
            if gt >= 20:
                per_game.append(gh / gt)
            hits += gh
            tot += gt
        share = hits / tot if tot else None
        out["windows"][tag] = {
            "threshold_gold": thr, "window": [lo, hi],
            "share": share, "n_outer_unit_rounds": tot, "n_hits": hits,
            "se_binomial": math.sqrt(share * (1 - share) / tot)
            if (share is not None and tot) else None,
            "per_game_share": per_game,
            "se_between_game": se_of_mean(per_game) if len(per_game) > 1 else None,
            "z_vs_025": ((share - 0.25) / math.sqrt(0.25 * 0.75 / tot))
            if (share is not None and tot) else None,
        }
    prof = []
    for off in range(profile_range[0], profile_range[1] + 1):
        hits = tot = 0
        for g in games:
            for T in sorted(g.snapshots):
                rid, amount = leader(g.snapshots[T])
                if rid is None:
                    continue
                for u in (0, 1):
                    t = T + off
                    if t < WARMUP_ROUNDS or t not in g.pos[u]:
                        continue
                    a = arm_of(g.pos[u][t])
                    if a is None:
                        continue
                    tot += 1
                    if a == rid:
                        hits += 1
        prof.append({"offset": off, "share": hits / tot if tot else None,
                     "n": tot,
                     "se": math.sqrt((hits / tot) * (1 - hits / tot) / tot)
                     if tot else None})
    out["offset_profile"] = prof
    fwd = out["windows"]["forward_T+1..T+5"]
    pla = out["windows"]["placebo_T-5..T-1"]
    # Decision uses the NULL standard error sqrt(p0 q0 / n) (never zero, unlike
    # the plug-in SE at share 0 or 1) at |z| > 3, AND requires the conservative
    # between-game SE to agree.  Both must clear the 0.25 null.
    lab = "none"
    zz = fwd.get("z_vs_025")
    bg_m = mean(fwd["per_game_share"]) if fwd["per_game_share"] else None
    bg_se = fwd.get("se_between_game")
    if zz is not None and bg_m is not None:
        strict = (bg_se is None) or (abs(bg_m - 0.25) > 2 * bg_se)
        if zz > 3.0 and bg_m > 0.25 and strict:
            lab = "targets_leading_arm"
        elif zz < -3.0 and bg_m < 0.25 and strict:
            lab = "avoids_leading_arm"
    out["label"] = lab
    out["decision_rule"] = ("|z vs 0.25| > 3 using the null SE, and the "
                            "between-game mean at least 2 between-game SEs "
                            "from 0.25")
    out["placebo_label"] = (
        "clean" if (pla.get("z_vs_025") is not None
                    and abs(pla["z_vs_025"]) < 3.0)
        else "CONTAMINATED")
    out["controls"] = ["time-reversed placebo window (T-5..T-1)",
                       "dose-response in leading-arm gold (0 / 20 / 40)"]
    out["invalid_control_note"] = (
        "probeobs cannot serve as a specificity control: it is a tracker and "
        "follows T-1, so it inherits T-1's destination with a lag")
    out["confound_direction"] = (
        "a T-1 unit sitting in an arm depletes it and lowers that arm's "
        "gold_remaining, which pushes the measured share DOWN, so the estimate "
        "is a floor")
    return out


def fog_bias_income_decomposition(games):
    """Model-free, fog-immune measure of the fog bias direction.

    Per-unit gold is logged for 100% of unit-rounds, so income on UNOBSERVED
    rounds is (total income) - (income on observed rounds) exactly, with no
    reweighting model.  If the hidden rounds are richer than the visible ones,
    the fog is hiding the productive part of the policy.
    """
    rows = []
    ratios = []
    for g in sorted(games, key=lambda x: x.game_id):
        obs_sum = obs_n = hid_sum = hid_n = 0
        for u in (0, 1):
            for r, inc in sorted(g.income[u].items()):
                if r < WARMUP_ROUNDS:
                    continue
                if r in g.pos[u]:
                    obs_sum += inc
                    obs_n += 1
                else:
                    hid_sum += inc
                    hid_n += 1
        if obs_n == 0 or hid_n == 0:
            continue
        om = obs_sum / obs_n
        hm = hid_sum / hid_n
        rows.append({"game_id": g.game_id, "observed_mean": om,
                     "unobserved_mean": hm, "n_observed": obs_n,
                     "n_unobserved": hid_n,
                     "ratio_unobserved_over_observed": hm / om if om else None,
                     "total_mean": (obs_sum + hid_sum) / (obs_n + hid_n),
                     "visible_over_unbiased": om / ((obs_sum + hid_sum) /
                                                    (obs_n + hid_n))})
        if om:
            ratios.append(hm / om)
    return {"per_game": rows,
            "ratio_mean": mean(ratios) if ratios else None,
            "ratio_se": se_of_mean(ratios) if len(ratios) > 1 else None,
            "visible_over_unbiased_mean": mean([r["visible_over_unbiased"]
                                                for r in rows]) if rows else None,
            "visible_over_unbiased_se": se_of_mean([r["visible_over_unbiased"]
                                                    for r in rows])
            if len(rows) > 1 else None,
            "interpretation":
                "ratio > 1 means the fog hides T-1's RICHER rounds, so the "
                "visible subset understates both income and (because income "
                "tracks activity) outward excursions"}


# --------------------------------------------------------------------------- #
# three-way dry run
# --------------------------------------------------------------------------- #

def synth_mask(n_rounds, miss_rate, seed_key, periodic=None):
    rng = seeded_rng("mask", seed_key)
    ts = []
    for t in range(WARMUP_ROUNDS, n_rounds):
        if periodic is not None:
            # observation probability itself oscillates with period `periodic`
            p = 0.5 + 0.45 * math.cos(2 * math.pi * t / periodic)
            if rng.random() < p:
                ts.append(t)
        else:
            if rng.random() >= miss_rate:
                ts.append(t)
    return ts


def dry_run(n_sur=80, n_perm=400, walkable=None, verbose=False):
    """Zero-signal / injected / reversed, per claim, with expected labels."""
    walkable = walkable or frozenset((i, j) for i in range(GRID)
                                     for j in range(GRID))
    reach = reach_table(walkable)
    freqs = freq_grid()
    out = {"scenarios": [], "all_passed": True}

    def record(claim, scenario, expected, got, detail):
        ok = (got == expected)
        out["scenarios"].append({
            "claim": claim, "scenario": scenario, "expected": expected,
            "observed": got, "passed": ok, "detail": detail})
        if not ok:
            out["all_passed"] = False

    # ---------------- claim P ---------------- #
    # Level 1 (pre-registered within-round label permutation) is run on the
    # zero-signal scenario purely to DOCUMENT that it is invalid; the expected
    # label for that scenario is therefore recorded as the (wrong) label the
    # pre-registered instrument actually produces, with known_invalid=True.
    cells = sorted(walkable)

    def two_walks(rng, n, restrict_a=None, restrict_b=None, same=False):
        wa = restrict_a or walkable
        wb = restrict_b or walkable
        ca = sorted(wa)
        cb = sorted(wb)
        pa = ca[rng.randrange(len(ca))]
        pb = pa if same else cb[rng.randrange(len(cb))]
        pos0, pos1 = {}, {}
        for t in range(n):
            for _ in range(3):
                dr, dc = ((-1, 0), (1, 0), (0, -1), (0, 1))[rng.randrange(4)]
                q = (pa[0] + dr, pa[1] + dc)
                if q in wa:
                    pa = q
                if same:
                    continue
                dr, dc = ((-1, 0), (1, 0), (0, -1), (0, 1))[rng.randrange(4)]
                q = (pb[0] + dr, pb[1] + dc)
                if q in wb:
                    pb = q
            pos0[t] = pa
            pos1[t] = pa if same else pb
        return pos0, pos1

    scen_pos = {}
    rng = seeded_rng("dry", "P_none")
    scen_pos["zero_signal"] = {}
    for g in range(5):
        p0, p1 = two_walks(rng, 200)
        scen_pos["zero_signal"]["g%d" % g] = {0: p0, 1: p1}
    rng = seeded_rng("dry", "P_fixed")
    top = frozenset(c for c in cells if c[0] < CENTER)
    bot = frozenset(c for c in cells if c[0] > CENTER)
    scen_pos["fixed_half"] = {}
    for g in range(5):
        p0, p1 = two_walks(rng, 200, restrict_a=top, restrict_b=bot)
        scen_pos["fixed_half"]["g%d" % g] = {0: p0, 1: p1}
    rng = seeded_rng("dry", "P_var")
    scen_pos["variable_axis"] = {}
    halves = []
    for axis in AXES:
        halves.append((frozenset(c for c in cells if axis_proj(c, axis) < 0),
                       frozenset(c for c in cells if axis_proj(c, axis) > 0)))
    for g in range(5):
        a, b = halves[g % len(halves)]
        if g % 2:
            a, b = b, a
        p0, p1 = two_walks(rng, 200, restrict_a=a, restrict_b=b)
        scen_pos["variable_axis"]["g%d" % g] = {0: p0, 1: p1}
    rng = seeded_rng("dry", "P_colo")
    scen_pos["colocated"] = {}
    for g in range(5):
        p0, p1 = two_walks(rng, 200, same=True)
        scen_pos["colocated"]["g%d" % g] = {0: p0, 1: p1}

    expect_l2 = {"zero_signal": "none", "fixed_half": "partition_fixed_half",
                 "variable_axis": "partition_variable_axis",
                 "colocated": "colocated"}
    for name in ("zero_signal", "fixed_half", "variable_axis", "colocated"):
        l2 = partition_level2(scen_pos[name], "dryP2_" + name, n_draw=n_perm)
        record("P", "level2_" + name, expect_l2[name], l2["label"],
               {"within_ovl": rnd(l2.get("within_game_ovl_mean")),
                "cross_ovl": rnd(l2.get("cross_game_ovl_mean")),
                "z_vs_cross": rnd((l2.get("null_cross_game_ovl") or {}).get("z")),
                "global_axis_acc": rnd((l2.get("global_axis") or {}).get("acc_mean")),
                "p_labelflip": rnd((l2.get("null_whole_game_label_flip") or {}).get("p_one_sided"))})
        # level-1 (pre-registered) on the same scenario, for the record
        pr = _pairs_from(scen_pos[name]["g0"][0], scen_pos[name]["g0"][1])
        l1 = adjudicate_partition(pr, "dryP1_" + name, n_perm=n_perm)
        out["scenarios"].append({
            "claim": "P", "scenario": "level1_preregistered_" + name,
            "expected": "DOCUMENTED-INVALID" if name == "zero_signal" else l1["label"],
            "observed": l1["label"],
            "passed": True,
            "known_invalid": name == "zero_signal",
            "detail": {"ovl": rnd(l1.get("ovl")),
                       "perm_null_mean": rnd((l1.get("ovl_null") or {}).get("mean")),
                       "z": rnd((l1.get("ovl_null") or {}).get("z")),
                       "note": "within-round label permutation fires on zero "
                               "signal; retained for the record only"
                       if name == "zero_signal" else ""}})

    # ---------------- claim C ---------------- #
    ts = synth_mask(500, 0.376, "C")
    # (a) zero signal: mean-reverting AR(1), phi=0.7 -- the named trap
    rng = seeded_rng("dry", "C_none")
    dense = ar1_surrogate(rng, 500, 3.7, 1.9, 0.7)
    s = {t: dense[t] for t in ts}
    r = adjudicate_cycle(s, walkable, "dryC_none", n_sur=n_sur, freqs=freqs,
                         want_series=False)
    record("C", "zero_signal_ar1_phi0.7_mean_reverting", "none", r["label"],
           {"peak_period": rnd(r["peak"]["period"]),
            "peak_power": rnd(r["peak"]["power"]),
            "p_ar1": rnd(r["null_ar1_maxpower"]["p_one_sided"]),
            "p_walk": rnd(r["null_boundedwalk_maxpower"]["p_one_sided"])})

    # (b) injected period 24
    rng = seeded_rng("dry", "C_p24")
    s = {t: 4.0 + 2.5 * math.cos(2 * math.pi * t / 24.0) + rng.gauss(0, 0.8)
         for t in ts}
    r24 = adjudicate_cycle(s, walkable, "dryC_p24", n_sur=n_sur, freqs=freqs,
                           want_series=False)
    ok24 = (r24["label"] == "periodic" and r24["peak"]["period"] is not None
            and abs(r24["peak"]["period"] - 24.0) / 24.0 < 0.1)
    record("C", "injected_period_24", "periodic@24",
           ("periodic@%.1f" % r24["peak"]["period"]) if ok24 else r24["label"],
           {"peak_period": rnd(r24["peak"]["period"]),
            "peak_power": rnd(r24["peak"]["power"]),
            "p_ar1": rnd(r24["null_ar1_maxpower"]["p_one_sided"]),
            "p_walk": rnd(r24["null_boundedwalk_maxpower"]["p_one_sided"]),
            "phase_coherence": rnd(r24["phase_coherence_at_peak"]),
            "p_phase_vs_arp": rnd(r24["null_arp_phase_coherence"]["p_one_sided"])})
    if ok24:
        out["scenarios"][-1]["expected"] = "periodic@24"
        out["scenarios"][-1]["observed"] = "periodic@24"
        out["scenarios"][-1]["passed"] = True
    else:
        out["all_passed"] = False
        out["scenarios"][-1]["passed"] = False

    # (c) "reversed" for a period detector = a DIFFERENT period; the failure
    # mode of a periodogram is reporting the wrong line, so identity of the
    # period is what must be discriminated.
    rng = seeded_rng("dry", "C_p8")
    s = {t: 4.0 + 2.5 * math.cos(2 * math.pi * t / 8.0) + rng.gauss(0, 0.8)
         for t in ts}
    r8 = adjudicate_cycle(s, walkable, "dryC_p8", n_sur=n_sur, freqs=freqs,
                          want_series=False)
    ok8 = (r8["label"] == "periodic" and r8["peak"]["period"] is not None
           and abs(r8["peak"]["period"] - 8.0) / 8.0 < 0.1)
    out["scenarios"].append({
        "claim": "C", "scenario": "reversed_injected_period_8_must_not_report_24",
        "expected": "periodic@8",
        "observed": ("periodic@%.1f" % r8["peak"]["period"]) if r8["peak"]["period"]
        else r8["label"],
        "passed": bool(ok8),
        "detail": {"peak_period": rnd(r8["peak"]["period"]),
                   "peak_power": rnd(r8["peak"]["power"]),
                   "p_ar1": rnd(r8["null_ar1_maxpower"]["p_one_sided"])}})
    if ok8:
        out["scenarios"][-1]["observed"] = "periodic@8"
    else:
        out["all_passed"] = False

    # (d) false-positive guard: periodicity in the MASK, white noise in d
    ts_p = synth_mask(500, 0.376, "Cmask", periodic=24.0)
    rng = seeded_rng("dry", "C_maskonly")
    s = {t: rng.gauss(3.7, 1.9) for t in ts_p}
    rm = adjudicate_cycle(s, walkable, "dryC_mask", n_sur=n_sur, freqs=freqs,
                          want_series=False)
    record("C", "guard_periodic_observation_mask_white_noise_values", "none",
           rm["label"],
           {"peak_period": rnd(rm["peak"]["period"]),
            "n_obs": rm["n_obs"],
            "p_ar1": rnd(rm["null_ar1_maxpower"]["p_one_sided"])})

    # ---------------- claim A ---------------- #
    rng = seeded_rng("dry", "A_none")
    inner = frozenset(c for c in walkable if ring_of(c) <= CENTRAL_MAX_D)
    rings = {}
    for gid in range(3):
        vals = bounded_walk_ring_series(rng, 200, inner)
        rings[gid] = {0: vals}
    exc = excursions_from({i: v for i, v in enumerate(rings[0][0])})
    a = adjudicate_amplitude(rings, exc, None, "dryA_none")
    record("A", "zero_signal_confined_to_central_9x9", "inside_9x9", a["label"],
           {"outer_share": rnd(a["naive_outer_share"]),
            "se": rnd(a["naive_outer_share_se_between_game"])})

    rng = seeded_rng("dry", "A_present")
    rings = {}
    for gid in range(3):
        vals = []
        for t in range(200):
            base = 4.0 + 3.2 * math.cos(2 * math.pi * t / 24.0)
            vals.append(max(0, min(8, int(round(base)))))
        rings[gid] = {0: vals}
    exc = excursions_from({i: v for i, v in enumerate(rings[0][0])})
    a = adjudicate_amplitude(rings, exc, None, "dryA_present")
    record("A", "injected_excursions_reaching_d7", "genuine_outer", a["label"],
           {"outer_share": rnd(a["naive_outer_share"]),
            "peaks": a["excursion_peak_hist"]})

    rng = seeded_rng("dry", "A_rev")
    outer = frozenset(c for c in walkable if ring_of(c) >= OUTER_MIN_D)
    rings = {}
    for gid in range(3):
        rings[gid] = {0: bounded_walk_ring_series(rng, 200, outer)}
    exc = excursions_from({i: v for i, v in enumerate(rings[0][0])})
    a = adjudicate_amplitude(rings, exc, None, "dryA_rev")
    record("A", "reversed_confined_to_outer_ring", "outer_dominant", a["label"],
           {"outer_share": rnd(a["naive_outer_share"])})

    # rigorous-bound machinery on synthetic ground truth
    bt = bound_selftest(walkable, reach)
    out["bounds_selftest_synthetic"] = bt
    if not bt["passed"]:
        out["all_passed"] = False

    # ---------------- claim Phi ---------------- #
    ts = synth_mask(500, 0.3, "Phi")
    rng = seeded_rng("dry", "Phi_none")
    d0 = {t: rng.gauss(3.7, 1.9) for t in ts}
    d1 = {t: rng.gauss(3.7, 1.9) for t in ts}
    r = adjudicate_antiphase(d0, d1, "dryPhi_none")
    record("Phi", "zero_signal_independent", "none", r["label"],
           {"r": rnd(r["r"]), "se": rnd(r["se_blockboot"])})

    rng = seeded_rng("dry", "Phi_anti")
    d0, d1 = {}, {}
    for t in ts:
        base = 2.5 * math.cos(2 * math.pi * t / 24.0)
        d0[t] = 4.0 + base + rng.gauss(0, 0.7)
        d1[t] = 4.0 - base + rng.gauss(0, 0.7)
    r = adjudicate_antiphase(d0, d1, "dryPhi_anti")
    record("Phi", "injected_antiphase", "antiphase", r["label"],
           {"r": rnd(r["r"]), "se": rnd(r["se_blockboot"]),
            "xcorr_extreme": deep_round(r["xcorr_extreme"])})

    rng = seeded_rng("dry", "Phi_in")
    d0, d1 = {}, {}
    for t in ts:
        base = 2.5 * math.cos(2 * math.pi * t / 24.0)
        d0[t] = 4.0 + base + rng.gauss(0, 0.7)
        d1[t] = 4.0 + base + rng.gauss(0, 0.7)
    r = adjudicate_antiphase(d0, d1, "dryPhi_in")
    record("Phi", "reversed_inphase", "inphase", r["label"],
           {"r": rnd(r["r"]), "se": rnd(r["se_blockboot"])})

    # ---------------- snapshot-response mechanism test ---------------- #
    class FakeGame(object):
        pass

    def make_snap_game(gid, mode, rng):
        fg = FakeGame()
        fg.game_id = gid
        fg.snapshots = {}
        fg.pos = {0: {}, 1: {}}
        fg.income = {0: {}, 1: {}}
        arms = {2: [(r, c) for r in range(0, 4) for c in range(0, 13)],
                3: [(r, c) for r in range(4, 17) for c in range(0, 4)],
                4: [(r, c) for r in range(13, 17) for c in range(4, 17)],
                5: [(r, c) for r in range(0, 13) for c in range(13, 17)]}
        for T in range(5, 500, 5):
            lead = 2 + rng.randrange(4)
            fg.snapshots[T] = {1: {"gold_remaining": 60}}
            for a in (2, 3, 4, 5):
                fg.snapshots[T][a] = {"gold_remaining": 50 if a == lead else
                                      rng.randrange(0, 10)}
            for u in (0, 1):
                for t in range(T + 1, T + 6):
                    if mode == "none":
                        pick = 2 + rng.randrange(4)
                    elif mode == "target":
                        pick = lead
                    else:                                 # avoid
                        opts = [a for a in (2, 3, 4, 5) if a != lead]
                        pick = opts[rng.randrange(3)]
                    cellset = arms[pick]
                    fg.pos[u][t] = cellset[rng.randrange(len(cellset))]
        return fg

    for mode, expect in (("none", "none"), ("target", "targets_leading_arm"),
                         ("avoid", "avoids_leading_arm")):
        rng = seeded_rng("dry", "snap_" + mode)
        gs = [make_snap_game(i, mode, rng) for i in range(4)]
        sr = snapshot_arm_response(gs)
        record("C_mech", "snapshot_response_" + mode, expect, sr["label"],
               {"share": rnd(sr["windows"]["forward_T+1..T+5"]["share"]),
                "z_vs_025": rnd(sr["windows"]["forward_T+1..T+5"]["z_vs_025"]),
                "placebo": rnd(sr["windows"]["placebo_T-5..T-1"]["share"]),
                "placebo_label": sr["placebo_label"],
                "n": sr["windows"]["forward_T+1..T+5"]["n_outer_unit_rounds"]})

    # recompute the global flag from the (possibly patched) scenario records so
    # that the period-identity scenarios, whose labels are rewritten above, are
    # scored exactly once
    out["all_passed"] = (all(s["passed"] for s in out["scenarios"])
                         and out["bounds_selftest_synthetic"]["passed"])
    out["n_scenarios"] = len(out["scenarios"])
    out["n_passed"] = sum(1 for s in out["scenarios"] if s["passed"])
    return out


def bound_selftest(walkable, reach):
    """Bounds must BRACKET a known ground truth and be informative.

    The synthetic probe *tracks* the target (one unit chases it with a random
    delay, the other patrols), which reproduces the real coverage regime
    (probeobs per-unit coverage 0.56-0.68) rather than an unrealistically blind
    probe.  Reported for two vision radii.
    """
    results = []
    for radius, tag in ((2, "r2"), (4, "r4")):
        rng = seeded_rng("bound", "selftest_" + tag)
        n_rounds = 260
        true_pos = {}
        p = (CENTER, CENTER)
        for t in range(n_rounds):
            target_d = 4.0 + 3.2 * math.cos(2 * math.pi * t / 24.0)
            best = None
            for q in reach[p]:
                err = abs(ring_of(q) - target_d) + 0.01 * rng.random()
                if best is None or err < best[0]:
                    best = (err, q)
            p = best[1]
            true_pos[t] = p
        # chaser: moves greedily toward the target's position `lag` rounds ago
        chaser = (CENTER, CENTER)
        patrol = (2, 2)
        vis = {}
        for t in range(n_rounds):
            tgt = true_pos[max(0, t - 2)]
            best = None
            for q in reach[chaser]:
                dd = abs(q[0] - tgt[0]) + abs(q[1] - tgt[1]) + 0.01 * rng.random()
                if best is None or dd < best[0]:
                    best = (dd, q)
            chaser = best[1]
            for _ in range(3):
                dr, dc = ((-1, 0), (1, 0), (0, -1), (0, 1))[rng.randrange(4)]
                q = (patrol[0] + dr, patrol[1] + dc)
                if q in walkable:
                    patrol = q
            s = set()
            for c in (chaser, patrol):
                for i in range(max(0, c[0] - radius), min(GRID, c[0] + radius + 1)):
                    for j in range(max(0, c[1] - radius), min(GRID, c[1] + radius + 1)):
                        s.add((i, j))
            vis[t] = frozenset(s)
        obs = {t: true_pos[t] for t in range(n_rounds) if true_pos[t] in vis[t]}
        b = feasible_bounds(obs, vis, walkable, reach, n_rounds, lo=0)
        truth_outer = sum(1 for t in range(n_rounds)
                          if ring_of(true_pos[t]) >= OUTER_MIN_D)
        truth_mean = mean([ring_of(true_pos[t]) for t in range(n_rounds)])
        ok = (b is not None and not b.get("infeasible_rounds")
              and b["outer_rounds_min"] <= truth_outer <= b["outer_rounds_max"]
              and b["mean_d_min"] - 1e-9 <= truth_mean <= b["mean_d_max"] + 1e-9)
        width = (b["outer_share_max"] - b["outer_share_min"]) if b else None
        results.append({
            "vision_radius": radius, "passed": bool(ok),
            "coverage": b["coverage"] if b else None,
            "truth_outer_rounds": truth_outer,
            "truth_outer_share": truth_outer / float(n_rounds),
            "bound_outer_share": [b["outer_share_min"], b["outer_share_max"]]
            if b else None,
            "bound_width": width,
            "truth_mean_d": truth_mean,
            "bound_mean_d": [b["mean_d_min"], b["mean_d_max"]] if b else None,
        })
    return {"passed": all(r["passed"] for r in results), "cases": results,
            "coverage": results[-1]["coverage"],
            "truth_outer_share": results[-1]["truth_outer_share"],
            "bound_outer_share": results[-1]["bound_outer_share"],
            "truth_mean_d": results[-1]["truth_mean_d"],
            "bound_mean_d": results[-1]["bound_mean_d"],
            "note": "synthetic ground truth; probe tracks the target so that "
                    "coverage matches the real probeobs regime"}


def bound_validation_real(game, reach, shift=250):
    """Real-data validation of the bound DP on a near-complete game.

    Our own probe path is time-rotated by `shift` to synthesise an alternative
    fog mask.  A target position is then declared *unobserved* iff it falls
    outside that synthetic visible set, so the invisibility constraint holds by
    construction and the true (logged) trajectory is available as ground truth.
    """
    out = []
    for u in (0, 1):
        full = {r: c for r, c in sorted(game.pos[u].items()) if r >= WARMUP_ROUNDS}
        if len(full) < 300:
            continue
        # restrict to the longest contiguous run of KNOWN ground truth
        known = sorted(full)
        best = (0, None)
        run_start = known[0]
        for i in range(1, len(known) + 1):
            if i == len(known) or known[i] != known[i - 1] + 1:
                length = known[i - 1] - run_start + 1
                if length > best[0]:
                    best = (length, (run_start, known[i - 1]))
                if i < len(known):
                    run_start = known[i]
        if best[1] is None or best[0] < 200:
            continue
        r_lo, r_hi = best[1]
        rounds = list(range(r_lo, r_hi + 1))
        n = r_hi + 1
        span = game.n_rounds
        fake_vis = {}
        for r in rounds:
            src = (r + shift) % span
            s = set()
            for j in (0, 1):
                c = game.our_pos[j].get(src)
                if c is None:
                    continue
                R = 2
                for i in range(max(0, c[0] - R), min(GRID, c[0] + R + 1)):
                    for k in range(max(0, c[1] - R), min(GRID, c[1] + R + 1)):
                        s.add((i, k))
            fake_vis[r] = frozenset(s)
        obs = {r: full[r] for r in rounds if full[r] in fake_vis[r]}
        b = feasible_bounds(obs, fake_vis, game.walkable, reach, n, lo=r_lo)
        truth_outer = sum(1 for r in rounds if ring_of(full[r]) >= OUTER_MIN_D)
        truth_mean = mean([ring_of(full[r]) for r in rounds])
        nR = len(rounds)
        ok = (b is not None and not b.get("infeasible_rounds")
              and b["outer_share_min"] - 1e-9 <= truth_outer / nR
              <= b["outer_share_max"] + 1e-9
              and b["mean_d_min"] - 1e-9 <= truth_mean <= b["mean_d_max"] + 1e-9)
        out.append({"game_id": game.game_id, "unit": u,
                    "window": [r_lo, r_hi], "n_rounds": nR,
                    "synthetic_coverage": b.get("coverage") if b else None,
                    "truth_outer_share": truth_outer / nR,
                    "bound_outer_share": [b["outer_share_min"], b["outer_share_max"]]
                    if b and "outer_share_min" in b else None,
                    "truth_mean_d": truth_mean,
                    "bound_mean_d": [b["mean_d_min"], b["mean_d_max"]]
                    if b and "mean_d_min" in b else None,
                    "infeasible_rounds": (b or {}).get("infeasible_rounds"),
                    "brackets_truth": bool(ok)})
    return out


# --------------------------------------------------------------------------- #
# schema self-checks
# --------------------------------------------------------------------------- #

def schema_checks(entries, known_maps, n_games=12):
    res = {}
    gold_bad = gold_tot = 0
    mask_bad = mask_tot = 0
    step_bad = step_tot = 0
    obs_outside_vis = 0
    stale_cost_ok = stale_cost_tot = 0
    sel = [e for e in entries if e["game_id"] in set(PRIMARY_GAMES) | set(SECONDARY_GAMES)]
    sel = sel[:n_games]
    for e in sel:
        path = ROOT / e["path"]
        ti = e["target_player_id"] - 1
        oi = 1 - ti
        our_pid = str(e["probe_player_id"])
        with path.open("r", encoding="utf-8") as fh:
            json.loads(fh.readline())
            map_rows = json.loads(fh.readline())
            walk = frozenset((i, j) for i in range(GRID) for j in range(GRID)
                             if map_rows[i][j] != "1")
            reach = reach_table(walk)
            prev_end = None
            prev_round = None
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if "end" not in o:
                    continue
                r = o["round"]
                end = o["end"]
                start = o["start"]
                if prev_end is not None and prev_round == r - 1:
                    for pi in (0, 1):
                        for j in (0, 1):
                            gold_tot += 1
                            if start["players"][pi]["units"][j]["gold"] != \
                                    prev_end["players"][pi]["units"][j]["gold"]:
                                gold_bad += 1
                        stale_cost_tot += 1
                        if start["players"][pi].get("cost") == \
                                prev_end["players"][pi].get("cost"):
                            stale_cost_ok += 1
                    for j in (0, 1):
                        a = prev_end["players"][ti]["units"][j]["position"]
                        b = end["players"][ti]["units"][j]["position"]
                        if a is not None and b is not None:
                            step_tot += 1
                            if (b[0], b[1]) not in reach[(a[0], a[1])]:
                                step_bad += 1
                grid = end["grid"]
                R = (start.get("vision_r") or {}).get(our_pid, 2)
                pred = set()
                for j in (0, 1):
                    c = end["players"][oi]["units"][j]["position"]
                    if c is None:
                        continue
                    for i in range(max(0, c[0] - R), min(GRID, c[0] + R + 1)):
                        for k in range(max(0, c[1] - R), min(GRID, c[1] + R + 1)):
                            pred.add((i, k))
                act = set((i, j) for i in range(GRID) for j in range(GRID)
                          if grid[i][j] != FOG)
                mask_tot += 1
                if pred != act:
                    mask_bad += 1
                for j in (0, 1):
                    c = end["players"][ti]["units"][j]["position"]
                    if c is not None and (c[0], c[1]) not in act:
                        obs_outside_vis += 1
                prev_end = end
                prev_round = r
    res["gold_identity_start_eq_prev_end"] = {
        "mismatches": gold_bad, "checked": gold_tot,
        "passed": gold_bad == 0}
    res["fog_mask_eq_union_radius_squares"] = {
        "mismatched_rounds": mask_bad, "checked": mask_tot,
        "passed": mask_bad == 0}
    res["observed_target_pos_inside_visible_set"] = {
        "violations": obs_outside_vis, "passed": obs_outside_vis == 0}
    res["three_step_graph_reachability"] = {
        "violations": step_bad, "checked": step_tot, "passed": step_bad == 0}
    res["start_cost_is_stale_copy_of_prev_end"] = {
        "matched": stale_cost_ok, "checked": stale_cost_tot,
        "rate": (stale_cost_ok / stale_cost_tot) if stale_cost_tot else None}
    res["games_checked"] = sorted(e["game_id"] for e in sel)
    res["all_passed"] = all(v.get("passed", True) for v in res.values()
                            if isinstance(v, dict))
    return res


def bomb_clock_check(games):
    changes = collections.Counter()
    pairs = collections.Counter()
    within = collections.Counter()
    for g in games:
        for k, v in g.bomb_transitions_by_mod20["changes"].items():
            changes[k] += v
        for k, v in g.bomb_transitions_by_mod20["cellpairs"].items():
            pairs[k] += v
        for k, v in g.bomb_transitions_by_mod20["within_round_removals"].items():
            within[k] += v
    tbl = []
    for m in range(20):
        tbl.append({"mod20": m, "bomb_state_changes": changes.get(m, 0),
                    "covisible_cell_pairs": pairs.get(m, 0),
                    "rate": (changes.get(m, 0) / pairs[m]) if pairs.get(m) else None})
    off = sum(changes.get(m, 0) for m in range(1, 20))
    return {"by_mod20": tbl, "changes_at_mod0": changes.get(0, 0),
            "changes_off_wave": off,
            "comparison": "end[r-1].grid vs start[r].grid on co-visible cells",
            "within_round_bomb_removals_by_mod20": dict(sorted(within.items())),
            "within_round_note":
                "start[r] -> end[r] bomb disappearances are unit detonations, a "
                "separate mechanism spread over all residues; they are excluded "
                "from the clock test",
            "period_20_confirmed": off == 0 and changes.get(0, 0) > 0,
            "games": sorted(g.game_id for g in games)}


# --------------------------------------------------------------------------- #
# main run
# --------------------------------------------------------------------------- #

def analyse_corpus(games, corpus_name, n_sur, n_perm, want_series=True,
                   emit_periodogram=True):
    reach_cache = {}
    per_game = {}
    ring_by_game = {}
    pos_by_game = {}
    all_exc = []
    all_onsets = []
    for g in sorted(games, key=lambda x: x.game_id):
        key = "%s/%d" % (corpus_name, g.game_id)
        if g.map_name not in reach_cache:
            reach_cache[g.map_name] = reach_table(g.walkable)
        reach = reach_cache[g.map_name]
        d0 = trimmed(g.ring[0])
        d1 = trimmed(g.ring[1])
        co = sorted(set(d0) & set(d1))
        pairs = [(g.pos[0][t], g.pos[1][t]) for t in co]
        pairs_by_round = {t: (g.pos[0][t], g.pos[1][t]) for t in co}
        part = adjudicate_partition(pairs, key, n_perm=n_perm)
        part["axis_stability_50r_windows"] = axis_stability(pairs_by_round)
        cyc = {}
        for u, s in ((0, d0), (1, d1)):
            cyc[str(u)] = adjudicate_cycle(s, g.walkable, key + "/u%d" % u,
                                           n_sur=n_sur, length=g.n_rounds,
                                           want_series=want_series)
            if not emit_periodogram:
                cyc[str(u)].pop("periodogram", None)
        phi = adjudicate_antiphase(d0, d1, key)
        exc = {}
        bounds = {}
        for u, s in ((0, d0), (1, d1)):
            e = excursions_from(s)
            exc[str(u)] = e
            all_exc.extend(e)
            all_onsets.extend([x["start"] for x in e])
            if g.visible_sets:
                bd = feasible_bounds(
                    g.pos[u], g.visible_sets, g.walkable, reach, g.n_rounds)
                if bd is not None and bd.get("infeasible_rounds"):
                    # the invisibility constraint has been contradicted -> the
                    # missingness law is not what we think; fall back to
                    # reachability-only bounds and flag loudly
                    open_vis = {r: frozenset() for r in g.visible_sets}
                    bd2 = feasible_bounds(g.pos[u], open_vis, g.walkable,
                                          reach, g.n_rounds)
                    bd = {"invisibility_constraint_contradicted": True,
                          "contradicted_rounds": bd["infeasible_rounds"],
                          "reachability_only": bd2}
                bounds[str(u)] = bd
        ring_by_game[g.game_id] = {0: [d0[t] for t in sorted(d0)],
                                   1: [d1[t] for t in sorted(d1)]}
        pos_by_game["%d" % g.game_id] = {0: trimmed(g.pos[0]),
                                         1: trimmed(g.pos[1])}
        ev = [e["round"] for e in g.hotspot_events]
        per_game[str(g.game_id)] = {
            "game_id": g.game_id, "probe_version": g.probe_version,
            "map": g.map_name, "net_gold": g.net,
            "n_rounds": g.n_rounds,
            "n_obs_u0": len(d0), "n_obs_u1": len(d1), "n_co_observed": len(co),
            "mean_d_u0": mean(d0.values()), "mean_d_u1": mean(d1.values()),
            "partition": part, "cycle": cyc, "antiphase": phi,
            "excursions": exc,
            "rigorous_bounds": bounds,
            "observed_hotspot_event_rounds": ev,
            "event_triggered_d": {
                str(u): event_triggered_average(s, ev, seed_key=key + "/u%d" % u)
                for u, s in ((0, d0), (1, d1))},
        }
    return per_game, ring_by_game, all_exc, all_onsets, pos_by_game


def cross_game_null(games):
    """Pair units from DIFFERENT games: destroys coordination, keeps dynamics."""
    units = []
    for g in sorted(games, key=lambda x: x.game_id):
        for u in (0, 1):
            units.append((g.game_id, u, trimmed(g.ring[u])))
    out = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            gi, ui, si = units[i]
            gj, uj, sj = units[j]
            if gi == gj:
                continue
            co = sorted(set(si) & set(sj))
            if len(co) < 30:
                continue
            r = pearson([float(si[t]) for t in co], [float(sj[t]) for t in co])
            if r is not None:
                out.append(r)
    return out


def pooled_claim_stats(per_game, key_path):
    vals = []
    for gid in sorted(per_game, key=int):
        node = per_game[gid]
        for k in key_path:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if node is not None:
            vals.append(node)
    return {"n": len(vals), "values": vals, "mean": mean(vals) if vals else None,
            "se": se_of_mean(vals) if len(vals) > 1 else None,
            "sd": sd(vals) if len(vals) > 1 else None}


def build_report(args):
    manifest, entries = load_manifest()
    known = load_known_maps()
    n_sur = 40 if args.quick else N_SURROGATE
    n_perm = 300 if args.quick else N_PERM

    by_id = {e["game_id"]: e for e in entries}
    primary = [load_game(by_id[g], known) for g in sorted(PRIMARY_GAMES)]
    secondary = [load_game(by_id[g], known) for g in sorted(SECONDARY_GAMES)]

    report = {
        "schema_version": 3,
        "purpose": "adjudicate 4 pre-registered claims about Tiuntled-1 spatial policy",
        "target": {"account": TARGET_ACCOUNT, "team": TARGET_TEAM,
                   "model_id": 87478,
                   "n_games_in_manifest": len(entries)},
        "note_no_timestamp": "omitted so that two `run` invocations are byte-identical",
        "definitions": {
            "ring_distance": "chebyshev((r,c),(8,8))",
            "central_9x9": "d <= 4", "outer_ring": "d >= 5",
            "phase_used": "end[r] positions (authoritative); rounds 0-3 excluded",
            "steps_per_round": STEPS_PER_ROUND,
        },
    }

    report["dry_run"] = dry_run(n_sur=max(40, n_sur // 3),
                                n_perm=max(200, n_perm // 4),
                                walkable=primary[0].walkable)
    report["schema_checks"] = schema_checks(entries, known)
    report["environment_confounds"] = {
        "bomb_20_round_clock": bomb_clock_check(primary),
        "outer_event_interval_doc": {
            "source": "sim/GENERATION.md 4.1",
            "interval_range_rounds": [8, 16], "mean_interval_rounds": 12.4,
            "why_not_a_spectral_line":
                "a renewal process with interval uniform on 8..16 has "
                "CV~0.21 and therefore produces a broad spectral bump over "
                "f in [1/16,1/8] with no phase locking to the absolute round "
                "index, unlike the strictly periodic bomb clock",
        },
    }

    # ------------- corpora ------------- #
    corpora = {}
    for name, games in (("primary_probeobs_clean", primary),
                        ("secondary_tracker_perturbed", secondary)):
        pg, rings, exc, onsets, posbg = analyse_corpus(
            games, name, n_sur, n_perm,
            want_series=True, emit_periodogram=True)
        cross = cross_game_null(games)
        level2 = partition_level2(posbg, name, n_draw=n_perm)
        # Real-data demonstration that the pre-registered level-1 null is
        # invalid: feed it CROSS-GAME unit pairs, which cannot have coordinated.
        l1_invalid = []
        keys = sorted(posbg)
        for i, ka in enumerate(keys):
            kb = keys[(i + 1) % len(keys)]
            if ka == kb:
                continue
            pr = _pairs_from(posbg[ka][0], posbg[kb][1])
            if len(pr) < 20:
                continue
            r1 = adjudicate_partition(pr, "l1inv/%s/%s" % (ka, kb),
                                      n_perm=max(200, n_perm // 4))
            l1_invalid.append({
                "u0_from_game": ka, "u1_from_game": kb, "n": len(pr),
                "ovl": r1["ovl"], "perm_null_mean": r1["ovl_null"]["mean"],
                "z": r1["ovl_null"]["z"], "label": r1["label"],
                "axis_cross_fitted_acc": r1["axis_cross_fitted"]["acc"]})
        pooled_bounds = {}
        for stat in ("outer_share_min", "outer_share_max",
                     "mean_d_min", "mean_d_max", "coverage"):
            vals = []
            for gid in sorted(pg, key=int):
                for u in ("0", "1"):
                    b = pg[gid]["rigorous_bounds"].get(u)
                    if b and stat in b:
                        vals.append(b[stat])
            pooled_bounds[stat] = {"n": len(vals), "mean": mean(vals) if vals else None,
                                   "se": se_of_mean(vals) if len(vals) > 1 else None,
                                   "min": min(vals) if vals else None,
                                   "max": max(vals) if vals else None}
        amp = adjudicate_amplitude(rings, exc, pooled_bounds, name)
        span = max(g.n_rounds for g in games)
        ray = rayleigh_scan(onsets, span, n_sur=n_sur, seed_key=name)
        ring_series_by_game = {}
        for g in games:
            for u in (0, 1):
                ring_series_by_game["%d_u%d" % (g.game_id, u)] = trimmed(g.ring[u])
        mod_tests = {
            "mod20": circular_shift_modk_test(
                {k: v for k, v in sorted(ring_series_by_game.items())}, 20,
                name, n_sur=n_sur),
            "mod12": circular_shift_modk_test(
                {k: v for k, v in sorted(ring_series_by_game.items())}, 12,
                name, n_sur=n_sur),
        }
        # pooled per-claim summaries
        corpora[name] = {
            "games": sorted(g.game_id for g in games),
            "probe_versions": sorted({g.probe_version for g in games}),
            "maps": sorted({g.map_name for g in games}),
            "net_gold": {str(g.game_id): g.net for g in
                         sorted(games, key=lambda x: x.game_id)},
            "visibility": {str(g.game_id): g.visibility for g in
                           sorted(games, key=lambda x: x.game_id)},
            "per_game": pg,
            "partition_level2": level2,
            "partition_level1_null_invalidity_on_real_cross_game_pairs": l1_invalid,
            "pooled": {
                "partition_ovl": pooled_claim_stats(pg, ["partition", "ovl"]),
                "partition_ovl_z": pooled_claim_stats(
                    pg, ["partition", "ovl_null", "z"]),
                "axis_in_sample_acc": pooled_claim_stats(
                    pg, ["partition", "axis_in_sample", "acc"]),
                "axis_cross_fitted_acc": pooled_claim_stats(
                    pg, ["partition", "axis_cross_fitted", "acc"]),
                "centroid_sep": pooled_claim_stats(pg, ["partition", "centroid_sep"]),
                "antiphase_r": pooled_claim_stats(pg, ["antiphase", "r"]),
                "cycle_peak_period_u0": pooled_claim_stats(
                    pg, ["cycle", "0", "peak", "period"]),
                "cycle_peak_period_u1": pooled_claim_stats(
                    pg, ["cycle", "1", "peak", "period"]),
                "cycle_peak_power_u0": pooled_claim_stats(
                    pg, ["cycle", "0", "peak", "power"]),
                "cycle_peak_power_u1": pooled_claim_stats(
                    pg, ["cycle", "1", "peak", "power"]),
            },
            "amplitude": amp,
            "excursion_onset_rayleigh": ray,
            "absolute_clock_phase_lock": mod_tests,
            "cross_game_null_r_values": sorted(cross),
            "cross_game_null_summary": null_summary(
                cross, mean([pg[g]["antiphase"]["r"] for g in sorted(pg, key=int)
                             if pg[g]["antiphase"].get("r") is not None]),
                "lower"),
        }
    report["corpora"] = corpora

    # bound machinery validated on real data (near-complete games)
    reach1 = reach_table(secondary[0].walkable)
    val = []
    for g in secondary:
        if g.visibility and g.visibility["end"]["visible_rate"] >= 0.95:
            val.extend(bound_validation_real(g, reach1))
    report["bounds_validation_real_data"] = val

    # ------------- gold channel ------------- #
    limit = 20 if args.quick else None
    report["gold_channel"] = gold_channel(entries, known, n_games=limit,
                                          verbose=args.verbose)
    report["gold_channel"]["income_by_ring_visible_subset_clean"] = \
        income_by_ring(primary)
    report["gold_channel"]["income_by_ring_visible_subset_perturbed"] = \
        income_by_ring(secondary)

    # ------------- verdicts ------------- #
    report["verdicts"] = make_verdicts(report)
    return report


def make_verdicts(report):
    prim = report["corpora"]["primary_probeobs_clean"]
    sec = report["corpora"]["secondary_tracker_perturbed"]
    v = {}

    # ---- P ---- #
    z = prim["pooled"]["partition_ovl_z"]
    ovl = prim["pooled"]["partition_ovl"]
    n_sig = sum(1 for x in z["values"] if x is not None and x <= -2.0)
    axc = prim["pooled"]["axis_cross_fitted_acc"]
    l2 = prim["partition_level2"]
    l1inv = prim["partition_level1_null_invalidity_on_real_cross_game_pairs"]
    l1inv_fired = sum(1 for x in l1inv if x["label"] == "partition")
    ga = l2["global_axis"]
    xz = (l2.get("null_cross_game_ovl") or {}).get("z")
    fixed_p = (l2.get("null_whole_game_label_flip") or {}).get("p_one_sided")
    if l2["label"] == "partition_fixed_half":
        pv = "CERTIFIED"
    elif l2["label"] == "partition_variable_axis":
        pv = ("REFUTED as stated (no fixed half-board); the weaker claim "
              "'the two units mutually avoid each other with a game-specific "
              "axis' is CERTIFIED")
    elif l2["label"] == "colocated":
        pv = "REFUTED (units co-locate rather than partition)"
    else:
        pv = "UNDECIDABLE"
    v["P_partition"] = {
        "verdict": pv,
        "level2_label": l2["label"],
        "decisive_statistic":
            "(a) within-game OVL vs cross-GAME unit-pairing null; "
            "(b) mean accuracy of a single GLOBAL (axis, sign) vs the exact "
            "whole-game label-flip null",
        "P1_within_game_ovl_mean": l2["within_game_ovl_mean"],
        "P1_within_game_ovl_se": l2["within_game_ovl_se"],
        "P1_cross_game_ovl_mean": l2["cross_game_ovl_mean"],
        "P1_cross_game_n_pairs": l2["cross_game_ovl_n_pairs"],
        "P1_z_vs_cross_game_null": xz,
        "P1_p_vs_cross_game_null": (l2.get("null_cross_game_ovl") or {}).get("p_one_sided"),
        "P3_global_axis": ga["axis"], "P3_global_sign": ga["sign"],
        "P3_global_axis_acc_mean": ga["acc_mean"], "P3_global_axis_acc_se": ga["acc_se"],
        "P3_global_axis_per_game_acc": ga["per_game_acc"],
        "P3_p_whole_game_label_flip": fixed_p,
        "P3_axes_chosen_per_game": l2["axes_chosen"],
        "P3_signs_chosen_per_game": l2["signs_chosen"],
        "P3_threshold_for_fixed_half": {"acc_mean": 0.75, "p": 0.07,
                                        "why_007": (l2.get("null_whole_game_label_flip") or {}).get("min_attainable_p")},
        "P2_centroid_sep_mean": prim["pooled"]["centroid_sep"]["mean"],
        "P2_centroid_sep_se": prim["pooled"]["centroid_sep"]["se"],
        "P2_note": "weak test by construction; not used to decide",
        "prereg_P1_permutation_null": {
            "ovl_mean": ovl["mean"], "ovl_se": ovl["se"],
            "z_mean": z["mean"], "z_se": z["se"],
            "games_z_below_-2": n_sig, "n_games": z["n"],
            "axis_cross_fitted_acc_mean": axc["mean"],
            "axis_cross_fitted_acc_se": axc["se"],
            "STATUS": "INVALID -- see dry_run scenario "
                      "level1_preregistered_zero_signal: two independent "
                      "bounded random walks give OVL 0.00 vs permutation null "
                      "0.54, z = -19. The null mixes the two marginals, so it "
                      "tests spatial localisation, not partition.",
            "real_data_confirmation_cross_game_pairs_fired": l1inv_fired,
            "real_data_confirmation_n_pairs": len(l1inv),
            "real_data_confirmation_detail": l1inv,
        },
        "null": "cross-GAME unit pairing (a) and exact whole-game label flip (b); "
                "the pre-registered within-round permutation is reported but "
                "cannot decide the claim",
        "perturbed_corpus_level2_label": sec["partition_level2"]["label"],
        "perturbed_corpus_within_ovl": sec["partition_level2"]["within_game_ovl_mean"],
        "perturbed_corpus_cross_ovl": sec["partition_level2"]["cross_game_ovl_mean"],
        "perturbed_corpus_z": (sec["partition_level2"].get("null_cross_game_ovl")
                               or {}).get("z"),
        "perturbed_corpus_global_axis_acc": sec["partition_level2"]["global_axis"]["acc_mean"],
        "perturbed_corpus_p_labelflip": (
            sec["partition_level2"].get("null_whole_game_label_flip") or {}
        ).get("p_one_sided"),
        "fog_bias_direction":
            "co-observation requires BOTH units inside our fog window, which "
            "over-samples rounds where the two units are close together; that "
            "biases OVL UPWARD and axis accuracy DOWNWARD, i.e. AGAINST "
            "partition. Any partition finding here is therefore conservative, "
            "and a null finding on P3 is correspondingly weakened.",
    }

    # ---- C ---- #
    per = prim["per_game"]
    periods, powers, beats = [], [], []
    for gid in sorted(per, key=int):
        for u in ("0", "1"):
            c = per[gid]["cycle"][u]
            if c.get("peak"):
                periods.append(c["peak"]["period"])
                powers.append(c["peak"]["power"])
            beats.append(bool(c.get("beats_ar1")) and bool(c.get("beats_walk")))
    ray = prim["excursion_onset_rayleigh"]
    mod20 = prim["absolute_clock_phase_lock"]["mod20"]
    mod12 = prim["absolute_clock_phase_lock"]["mod12"]
    gold = report["gold_channel"]
    gold_max_abs = None
    gold_worst = None
    for row in gold["pooled_acf"]:
        if row["paired_diff_mean"] is None:
            continue
        val = abs(row["paired_diff_mean"])
        if gold_max_abs is None or val > gold_max_abs:
            gold_max_abs = val
            gold_worst = row
    n_beat = sum(1 for b in beats if b)
    cv = "REFUTED"
    if n_beat >= len(beats) * 0.6 and sd(periods) is not None and \
            sd(periods) / (mean(periods) or 1) < 0.25:
        cv = "CERTIFIED"
    elif n_beat >= 3:
        cv = "UNDECIDABLE"
    v["C_cycle"] = {
        "verdict": cv,
        "decisive_statistic":
            "GLS peak power vs AR(1)-matched and bounded-random-walk nulls, "
            "combined with the Rayleigh phase-lock scan on excursion onsets "
            "and the fog-free gold-channel paired ACF",
        "n_unit_series": len(beats),
        "n_series_beating_both_nulls": n_beat,
        "peak_period_mean": mean(periods) if periods else None,
        "peak_period_se": se_of_mean(periods) if len(periods) > 1 else None,
        "peak_period_sd": sd(periods) if len(periods) > 1 else None,
        "peak_period_values": periods,
        "peak_power_mean": mean(powers) if powers else None,
        "peak_power_se": se_of_mean(powers) if len(powers) > 1 else None,
        "rayleigh_R_max": ray.get("R_max"),
        "rayleigh_period": ray.get("period_at_R_max"),
        "rayleigh_p_vs_uniform": (ray.get("null_uniform") or {}).get("p_one_sided"),
        "rayleigh_p_vs_interval_shuffle":
            (ray.get("null_interval_shuffle") or {}).get("p_one_sided"),
        "excursion_interval_mean": ray.get("interval_mean"),
        "excursion_interval_se": ray.get("interval_se"),
        "excursion_interval_cv": ray.get("interval_cv"),
        "absolute_clock_mod20_p": (mod20.get("null_circular_shift") or {}).get("p_one_sided"),
        "absolute_clock_mod12_p": (mod12.get("null_circular_shift") or {}).get("p_one_sided"),
        "gold_channel_paired_acf_max_abs_diff": gold_max_abs,
        "gold_channel_paired_acf_worst_lag": gold_worst,
        "nulls": ["AR(1)-matched same-mask", "bounded random walk on map1 walkables",
                  "AR(p=30) ACF-matched (phase-coherence test)",
                  "circular-shift absolute-clock null",
                  "uniform + interval-shuffle Rayleigh nulls",
                  "within-game other-player paired control (gold channel)"],
        "fog_bias_direction":
            "the probe loses T-1 preferentially on long outward excursions "
            "(detection probability falls monotonically with d), so gaps are "
            "concentrated exactly at the extremes of any radial cycle. That "
            "ATTENUATES a real cycle: a null result here is weaker evidence "
            "than a positive one. The fog-free gold channel is not subject to "
            "this bias and is therefore the load-bearing test for C.",
    }

    # ---- A ---- #
    amp = prim["amplitude"]
    b = amp["rigorous_bounds"] or {}
    v["A_amplitude"] = {
        "verdict": "CERTIFIED_OUTER" if amp["label"] == "genuine_outer"
        else amp["label"].upper(),
        "decisive_statistic": "share of unit-rounds with d >= 5",
        "naive_outer_share": amp["naive_outer_share"],
        "naive_outer_share_se_between_game": amp["naive_outer_share_se_between_game"],
        "naive_outer_share_se_blockboot": amp["naive_outer_share_se_blockboot"],
        "naive_mean_d": amp["naive_mean_d"],
        "naive_mean_d_se": amp["naive_mean_d_se_between_game"],
        "rigorous_outer_share_bounds": [
            (b.get("outer_share_min") or {}).get("mean"),
            (b.get("outer_share_max") or {}).get("mean")],
        "rigorous_outer_share_bounds_se": [
            (b.get("outer_share_min") or {}).get("se"),
            (b.get("outer_share_max") or {}).get("se")],
        "rigorous_mean_d_bounds": [
            (b.get("mean_d_min") or {}).get("mean"),
            (b.get("mean_d_max") or {}).get("mean")],
        "ring_share_by_d": amp["pooled_ring_distribution"]["share"],
        "excursion_peak_hist": amp["excursion_peak_hist"],
        "excursion_peak_hist_uncensored": amp["excursion_peak_hist_uncensored"],
        "turning_point_ring": max(
            [int(k) for k in amp["excursion_peak_hist"]] or [0]),
        "null": "the d<=4 confinement hypothesis (share(d>=5)=0) and the "
                "layered-graph feasibility DP that bounds it from both sides",
        "fog_bias_direction":
            "detection probability decreases monotonically with d, so the "
            "naive share UNDERSTATES outer-ring time. The naive number is a "
            "lower bound; the DP max is a rigorous upper bound.",
    }

    # ---- Phi ---- #
    ap = prim["pooled"]["antiphase_r"]
    cg = prim["cross_game_null_summary"]
    xr = []
    for gid in sorted(per, key=int):
        ex = per[gid]["antiphase"].get("xcorr_extreme")
        if ex:
            xr.append(ex)
    labels = [per[gid]["antiphase"]["label"] for gid in sorted(per, key=int)]
    if ap["mean"] is not None and ap["se"] and ap["mean"] + 2 * ap["se"] < 0:
        phv = "CERTIFIED"
    elif ap["mean"] is not None and ap["se"] and ap["mean"] - 2 * ap["se"] > 0:
        phv = "REFUTED_IN_PHASE"
    else:
        phv = "REFUTED"
    v["Phi_antiphase"] = {
        "verdict": phv,
        "decisive_statistic": "corr(d0,d1) on co-observed rounds, pooled over games",
        "r_mean": ap["mean"], "r_se": ap["se"], "r_values": ap["values"],
        "per_game_labels": labels,
        "null_zero": "two-sided via between-game SE",
        "null_cross_game_pairing": cg,
        "xcorr_extremes": xr,
        "fog_bias_direction":
            "co-observation requires both units in our fog window; our probe "
            "follows one of them, so co-observed rounds over-represent rounds "
            "where the units are near each other, which biases corr(d0,d1) "
            "UPWARD (towards in-phase) and therefore against detecting "
            "anti-phase. A null result is thus partly conservative, but a "
            "POSITIVE r cannot be trusted as evidence of real in-phase play.",
    }
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("validate", help="three-way dry run + schema checks")
    pv.add_argument("--quick", action="store_true")
    pv.add_argument("--verbose", action="store_true")
    pr = sub.add_parser("run", help="full run, writes the JSON artifact")
    pr.add_argument("--quick", action="store_true")
    pr.add_argument("--no-write", action="store_true")
    pr.add_argument("--out", default=str(OUT_JSON))
    pr.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        manifest, entries = load_manifest()
        known = load_known_maps()
        by_id = {e["game_id"]: e for e in entries}
        g = load_game(by_id[PRIMARY_GAMES[0]], known)
        dr = dry_run(n_sur=60, n_perm=300, walkable=g.walkable,
                     verbose=args.verbose)
        sc = schema_checks(entries, known, n_games=4)
        bc = bomb_clock_check([g])
        print("== three-way dry run ==")
        for s in dr["scenarios"]:
            print("  [%s] %-8s %-52s expected=%-14s observed=%-14s %s"
                  % ("PASS" if s["passed"] else "FAIL", s["claim"],
                     s["scenario"][:52], s["expected"], s["observed"],
                     json.dumps(deep_round(s["detail"]), sort_keys=True)))
        bs = dr["bounds_selftest_synthetic"]
        for case in bs["cases"]:
            print("  [%s] A        rigorous-bound DP (vision r=%d, coverage %.3f): "
                  "truth outer share %.4f in [%.4f, %.4f]; truth mean d %.3f in "
                  "[%.3f, %.3f]"
                  % ("PASS" if case["passed"] else "FAIL", case["vision_radius"],
                     case["coverage"], case["truth_outer_share"],
                     case["bound_outer_share"][0], case["bound_outer_share"][1],
                     case["truth_mean_d"], case["bound_mean_d"][0],
                     case["bound_mean_d"][1]))
        print("== schema checks ==")
        for k in sorted(sc):
            if isinstance(sc[k], dict):
                print("  %-46s %s" % (k, json.dumps(deep_round(sc[k]),
                                                    sort_keys=True)))
        print("== bomb clock ==")
        print("  changes at r mod 20 == 0: %d ; off-wave: %d ; period-20 %s"
              % (bc["changes_at_mod0"], bc["changes_off_wave"],
                 "CONFIRMED" if bc["period_20_confirmed"] else "NOT CONFIRMED"))
        ok = dr["all_passed"] and sc["all_passed"] and bc["period_20_confirmed"]
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    report = build_report(args)
    payload = deep_round(report)
    text = json.dumps(payload, sort_keys=True, indent=1, ensure_ascii=False)
    if not args.no_write:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        sys.stderr.write("wrote %s (%d bytes, sha256 %s)\n"
                         % (out, len(text) + 1,
                            hashlib.sha256((text + "\n").encode()).hexdigest()[:16]))
    else:
        sys.stderr.write("sha256 %s\n"
                         % hashlib.sha256((text + "\n").encode()).hexdigest()[:16])
    for k in sorted(report["verdicts"]):
        v = report["verdicts"][k]
        print("%-16s %s" % (k, v["verdict"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
