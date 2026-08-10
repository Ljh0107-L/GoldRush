# Unfamiliar-map robustness: does the fingerprint re-check ever go silent?

> 2026-08-10. Audits the 8.10 mis-lock repair (`fd47ea6`) against terrain it has
> never seen. Judged on **zero illegal outputs** and **no silent lock-in**.
> Income on unfamiliar maps is deliberately **not** reported — there is no
> baseline there. Income appears only in the known-map premium, where one exists.
> Zero platform games were used.

## 0. Verdict

**The `round <= 24` window does not cover every constructible map class. The
"contradiction always fires in round 3-4" evidence was censored by the bound
itself.** With the bound removed, the first-contradiction round over 1 992
mis-locking games has **median 25, p90 237, p95 339, max 495**, and 18.9 % of
mis-locks never produce a visible contradiction at all. The shipped window
catches **49.7 %** of them; an unbounded window catches **81.1 %**.

**And yet the repair's +640 is still a bounded asset — for a different reason
than the one recorded.** Detection fires on the *earliest-observed* mismatching
cell, so the escape probability collapses as the number of mismatched cells
grows: 56.9 % at one mismatched cell, 8.3 % at three, **0 % at eight or more**
(0 of 24 mis-locks). mimic1/2/3 are wrong in 17/13/29 cells and are caught at
r3-4. Simultaneously, the maps that do escape are wrong by one bit in a region
the units never route through, and their blocked-step rate is indistinguishable
from a correct lock. The two effects point opposite ways and
their product is small at both measured ends.

**Recommendation: do not widen the window** (it costs a flat −46 gold/game on
known maps to detect errors with no measurable behavioural footprint) **and do
correct the recorded justification**, because "matched in round 3-4, 6× margin"
is false and a future decision leaning on it would be wrong. Specification in §9.

| Acceptance criterion | Result |
|---|---|
| Zero illegal outputs | **3 608/3 608 games** that the engine accepted completed all 500 rounds with a legal `GameOutput` every round, and zero forfeits; the other 42 never started — the *engine* rejects that terrain at setup (§10), which is a generator defect, not a construct fault |
| No silent lock-in | **FAILS as an absolute guarantee.** 1 002 of 1 992 mis-locking games ended still locked to a wrong table with `map_id >= 0`, never revised |
| Detector validated on a null | **Yes, three ways** (§2): null → silent 72/72, true mis-lock → reported 9/9, deliberately wrong lock → reported with the right table 18/18 |

---

## 1. What was measured, and with what

| Item | Value |
|---|---|
| Construct under test | `git show fd47ea6:src/player.cpp`, sha256 `df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217` (verified at extraction; `src/game_api.h` byte-identical to HEAD) |
| Build | `clang++ -O2 -std=c++17 -shared -fPIC`, host arm64 dylib, guarded scalar fallback, `_mm_prefetch`/`_MM_HINT_T0` stubbed by a one-line shim |
| Opponent / dispatch | passive `stay` seat, `dispatch=fixed`, `fixed_costs=(200, 100000)` so seat 1 always moves first; 500 rounds |
| Games played | **3 650** (3 567 adjudicated + 18 coverage + 9 premium + 56 equivalence; the coverage and equivalence sets were each run twice, before and after the observer was moved ahead of the gate) |
| Builds | `base` (unmodified), `probe` (= shipped, `VERIFY_ROUNDS=24`, observers added), `probe_inf` (`VERIFY_ROUNDS=100000`), `probe_force{0,1,2}` (lock forced to map1/2/3 at round 0, fingerprint bypassed) |

**The instrumentation is inert.** `base` and `probe` produce **identical log
SHA-256** on 14/14 map×seed pairs (map1/2/3, mimic1/2, dense, corridor × seeds
0,1) — measured on the same map and seed, compared byte-for-byte against the
uninstrumented build. Every behavioural number below is therefore a property of
`fd47ea6`, not of the probe.

Two observers were added, both write-only to probe state:

* **mechanism event** — the round `slowTick` itself raises `conflict` and drops
  to lazy. Gated exactly as shipped: `visited` plus `round <= VERIFY_ROUNDS`.
* **visible-conflict observer** — the same 5×5 wall test run *every* round with
  both the `visited` gate and the round bound removed. Called before and after
  the gate, so it also catches the round in which the mechanism fires and then
  clears the lock.

**The two agree exactly.** Across **243 paired games** (153 banded + 90
late-sample), `shipped.visible_conflict_round == unbounded.mechanism_round` in
**243/243** cases, measured on the same map and seed with `VERIFY_ROUNDS` as the
only difference. This is expected and now confirmed: a wall reads `-1` whether or
not it holds gold, so a contradiction is *static* — the first round it sits in a
window is the first round a unit standing on a new cell can see it. **The
`visited` gate costs nothing in detection latency. Only the round bound does.**

---

## 2. Mandatory zero-signal dry run (three-way)

### 2.1 Null arm — the detector must stay silent

| Population | Condition | Runs | Adjudicator says |
|---|---|---|---|
| map1 / map2 / map3 | the three known maps, terrain the baked tables were built from | 9 | **no mis-lock**, 9/9; locks map1@r0, map2@r1, map3@r1; conflict never raised |
| dense, sealed, anchorwall, asym, sparse, corridor | the six non-adversarial unknown maps already in `sim/maps_unknown.json` | 18 | **no mis-lock**, 18/18; no lock is ever taken, `map_id` ends at −2 (LAZY) |
| 12 corner-region single-cell flips of map1/2/3 | flips at (1,1)/(2,1)/(2,2)/(14,15) — inside the cells the round-0/1 fingerprint consults, so the lock must be *refused* | 36 | **no mis-lock**, 36/36; fingerprint rejects, `map_id` = −2 |
| mimic1_broken / mimic2_broken / mimic3_broken | the published one-cell controls rebuilt (mimic with (2,1)/(2,2) flipped so the parent candidate is eliminated) | 9 | **no mis-lock**, 9/9 |
| **total null** | | **72** | **72/72 silent** |

### 2.2 True-positive arm — the detector must fire

| Map | Condition | Runs | Adjudicator says |
|---|---|---|---|
| mimic1 | outer band identical to map1, interior rewritten | 3 | mis-lock to **map1**, locked r0, **detected r3** |
| mimic2 | outer band identical to map2 | 3 | mis-lock to **map2**, locked r1, **detected r3** |
| mimic3 | outer band identical to map3 | 3 | mis-lock to **map3**, locked r1, **detected r4** |

Reproduces the delivered claim (r3/r3/r4) exactly, on three seeds each.

### 2.3 Deliberately-wrong-lock arm — the detector must name the right table

Confusion matrix: rows = build with the lock **forced** at round 0 (fingerprint
bypassed), columns = terrain actually played, 3 seeds per cell. Compared against
the diagonal, where the forced lock happens to be correct.

| forced table \\ terrain | map1 | map2 | map3 |
|---|---|---|---|
| **map1** | silent (correct lock), 3/3 | mis-lock → map1, 44 wrong cells, **detected r0** | mis-lock → map1, 78 wrong cells, **detected r0** |
| **map2** | mis-lock → map2, 44 wrong cells, **detected r0** | silent (correct lock), 3/3 | mis-lock → map2, 74 wrong cells, **detected r1** |
| **map3** | mis-lock → map3, 78 wrong cells, **detected r0** | mis-lock → map3, 74 wrong cells, **detected r1** | silent (correct lock), 3/3 |

Diagonal 9/9 silent, off-diagonal 18/18 reported with the wrong table named and
the mismatch counted. **The detector is not vacuous and not trigger-happy.**

---

## 3. The contradiction-round distribution and its tail

Population: **every legal single-cell edit of every known map** — 838 maps
(274/286/278 for map1/2/3), 3 seeds each, played with the shipped
`VERIFY_ROUNDS=24` build. A single cell is the minimum possible perturbation and
therefore the maximally stealthy adversary. 6 maps × 3 seeds are excluded (§10).

| Outcome, over 2 505 valid games | n | share |
|---|---:|---:|
| fingerprint refused to lock (healthy) | 504 | 20.1 % |
| lock correct (the 3 known maps) | 9 | 0.4 % |
| **mis-locked, detected inside the shipped window** | **990** | **39.5 %** |
| **mis-locked, contradiction visible only after round 24** | **626** | **25.0 %** |
| **mis-locked, contradiction never visible in 500 rounds** | **376** | **15.0 %** |

Conditional on mis-locking (n = 1 992): detected 49.7 %, missed-but-visible-later
31.4 %, never visible 18.9 %.

### 3.1 First-contradiction round, bound removed

Measured on the shipped build's own trajectory (so the map is being played
*wrong* the whole time, which is the situation that matters), over the 1 992
mis-locking games; compared against the shipped window length of 24.

| statistic | over mis-locks where evidence ever appears (n=1 616) | over all mis-locks, "never" counted as ∞ (n=1 992) |
|---|---:|---:|
| median | 11 | **25** |
| p75 | 76 | 265 |
| **p90** | **237** | **never** |
| **p95** | **339** | **never** |
| p99 | 455 | never |
| **max** | **495** | **never** |
| mean | 65.1 | — |
| fraction > 24 | 38.7 % | 50.3 % |

**The shipped bound of 24 sits at the median of the uncensored distribution, not
at a 6× margin.** Example of how tight it is: `map1_x_05-11_wall_removed` is
caught at round 23 on seed 0 and missed on seeds 1 and 2, where the evidence
arrives at rounds 25 and 27.

### 3.2 Why the recorded "always round 3-4" was censored

Detection rounds *as reported by the shipped build* — the only rounds that build
can ever report, since it stops looking at 24:

| statistic | shipped build, n=990 detected |
|---|---:|
| min | 1 |
| median | 4 |
| p95 | 20 |
| **max** | **24** |

The maximum is exactly the bound. That is the signature of a censored sample: the
"round 3-4" observation was true of the mimic class and was then generalised into
a margin claim by a build that is structurally incapable of reporting a larger
number. The correct general statement is:

> detection round = min over mismatched cells of that cell's first-comparison
> round — and the distribution of *that* has p90 = never.

### 3.3 Is there a cheap bound just past 24?

Where the missed-but-eventually-visible evidence lands (n = 626), compared
against the shipped bound:

| evidence arrives in rounds | games | of missed-late | of all mis-locks |
|---|---:|---:|---:|
| 25–30 | 39 | 6.2 % | 2.0 % |
| 31–40 | 65 | 10.4 % | 3.3 % |
| 41–60 | 83 | 13.3 % | 4.2 % |
| 61–120 | 141 | 22.5 % | 7.1 % |
| 121–250 | 165 | 26.4 % | 8.3 % |
| 251–499 | 133 | 21.2 % | 6.7 % |

**No cliff.** The density is spread across the whole game, so there is no modest
widening that captures most of the tail: the choice is essentially 24 or no bound.

### 3.4 Widening does mechanically work — it is the price that kills it

30 maps drawn from the missed-late class to span the whole visible-round range,
3 seeds, played with both builds. Compared pairwise: same map, same seed,
`VERIFY_ROUNDS` the only difference.

| check | result |
|---|---|
| shipped `visible_conflict_round` == unbounded mechanism round | **90/90** exact |
| unbounded build dropped to LAZY and still finished all 500 rounds | **86/90** |
| the other 4 | the seed in which no contradiction was ever visible, so there was nothing to detect — expected, not a defect |
| shipped build never detected and ended still locked | 73/90 |

So the mechanism, the recovery and the `fixAnchor` re-anchoring all work at round
244 exactly as they do at round 3. The reason not to widen is §8, not a doubt
about whether the recovery functions.

---

## 4. Per-class lock outcomes

### 4.1 By construction class

51 curated maps (`sim/maps_unknown_late.json`), 3 seeds, shipped build. Cells
were selected from the coverage census (§5) by their predicted first-comparison
round on the *unperturbed* parent; the "visible rounds" column shows the actual
outcome, which is why the census is a screening tool and not a predictor — a
perturbation moves the trajectory that would have observed it.

| class | condition (all are single-cell edits of a known map) | runs | no lock | detected ≤24 | missed, visible later | missed, never visible | actual visible rounds |
|---|---|---:|---:|---:|---:|---|
| known | the three baked maps, as control | 9 | 0 | 0 (lock correct 9/9) | 0 | 0 | — |
| `B_corner` | flip inside the round-0/1 fingerprint region | 36 | **36** | 0 | 0 | 0 | — |
| `*_broken` | published one-cell controls | 9 | **9** | 0 | 0 | 0 | — |
| `A_r0004` | cell predicted first-compared in rounds 0–4 | 18 | 6 | **12** | 0 | 0 | min 1 med 2 max 4 |
| `A_r0524` | predicted rounds 5–24 | 18 | 0 | 14 | 4 | 0 | min 4 med 9 max 166 |
| `A_r2560` | predicted rounds 25–60 | 18 | 0 | 3 | **15** | 0 | min 10 med 101 max 394 |
| `A_r61200` | predicted rounds 61–200 | 18 | 6 | 1 | 8 | 3 | min 6 med 230 max 357 |
| `A_r201499` | predicted rounds 201–499 | 18 | 3 | 1 | 6 | 8 | min 15 med 217 max 378 |
| `A_never` | cell never compared on the parent map | 18 | 0 | 1 | 4 | **13** | min 16 med 205 max 365 |

### 4.2 By parent table

Same exhaustive population, split by which baked table the perturbation was built
from; compared against each other to locate the weakest table.

| parent | games | fingerprint refused | mis-locked | of mis-locks: detected | missed-late | never visible |
|---|---:|---:|---:|---:|---:|---:|
| map1 | 819 | 36 (4.4 %) | **780 (95.2 %)** | 56.8 % | 30.3 % | 12.9 % |
| map2 | 855 | 234 (27.4 %) | 618 (72.3 %) | 44.3 % | 35.8 % | 19.9 % |
| map3 | 831 | 234 (28.2 %) | 594 (71.5 %) | 46.0 % | 28.5 % | 25.6 % |

**map1 is the softest target**, and structurally so: it locks at round 0 from the
3×3 corner alone, so only **12 of its 272** legal single-cell edits break the
fingerprint (in all three seeds). map2/map3 need the round-1 9×9 (`vp=2`) to
separate, which consults far more cells, so **78 of 284** and **78 of 276** are
correctly refused — 6× more attack surface closed at the fingerprint stage.

Whether a given map escapes is also seed-dependent: **241 of the 832 legal
perturbations had their three seeds disagree** on the outcome state, because the seed sets the
gold layout and therefore the trajectory that would have observed the mismatch.
Any per-map claim below is therefore per (map, seed) unless stated otherwise.

### 4.3 State invariants (exhaustive population, 2 505 games)

| invariant | result |
|---|---|
| a lock, once taken, is always to the **parent's own table** | 2 001/2 001 (map1→0 @r0, map2→1 @r1, map3→2 @r1); never to a third table |
| a detected contradiction drops to LAZY irreversibly | 990/990 end at `map_id = −2` |
| a lock is never taken twice | `relock_round` fires 0 times in 2 505 games |
| a refused fingerprint reaches unknown mode | 504/504 end at `map_id = −2`, never stuck at −1 |
| **an undetected mis-lock is never revised** | 1 002/1 002 end still locked (`map_id` = 0/1/2) — this is the silent lock-in |

---

## 5. Where the blind region is

Coverage census: for each known map, the round at which each of the 289 cells
first enters the comparison set, derived from real trajectories with the round
bound removed. Condition: shipped build, passive opponent; compared against the
shipped bound of 24.

| map | seed | cells ever compared | compared by r24 | **first compared after r24** | never compared | max finite round |
|---|---|---:|---:|---:|---:|---:|
| map1 | 0 | 254 | 185 | **104 (36.0 %)** | 35 | 432 |
| map1 | 1 | 244 | 153 | **136 (47.1 %)** | 45 | 488 |
| map1 | 2 | 242 | 161 | **128 (44.3 %)** | 47 | 433 |
| map2 | 0 | 248 | 160 | **129 (44.6 %)** | 41 | 489 |
| map2 | 1 | 237 | 156 | **133 (46.0 %)** | 52 | 492 |
| map2 | 2 | 241 | 168 | **121 (41.9 %)** | 48 | 402 |
| map3 | 0 | 227 | 152 | **137 (47.4 %)** | 62 | 342 |
| map3 | 1 | 232 | 152 | **137 (47.4 %)** | 57 | 486 |
| map3 | 2 | 188 | 155 | **134 (46.4 %)** | 101 | 349 |

**36–47 % of the board is outside the shipped window.** The blind region is not
scattered — it is the two anti-diagonal corners. Per-cell outcome for parent
map1, pooled over 3 seeds (`D` detected ≤24, `L` mis-lock with evidence only
after 24, `n` mis-lock with no evidence ever, `.` fingerprint refused, `?` seeds
disagreed, `#` no legal perturbation):

```
      c0              c16
 r0   # . . D D D D L L ? L L ? ? ? ? #
 r1   # . # # D D D ? L ? L L ? # # ? #
 r2   . . . D D D D D D ? ? L L ? ? ? ?
 r3   D D D D D D D D D D D ? L L ? ? ?
 r4   ? ? D D D D D D D D D ? ? ? L ? ?
 r5   n ? L D D D D D D D D ? ? L ? L ?
 r6   n ? L D D D D D D D D D ? L ? ? ?
 r7   n L L L ? D D D D D D ? ? ? ? L ?
 r8   ? ? ? ? D D D D # D D D ? D ? ? ?
 r9   ? L ? L ? D D D D D D D ? ? ? ? ?
 r10  ? L L ? ? ? D D D D D D D D L ? n
 r11  n ? ? ? ? ? D D D D D D D D ? L ?
 r12  n n ? ? ? ? D D D D D D D D D L ?
 r13  n ? ? ? ? ? D D D D D D D D D D D
 r14  n n ? ? ? ? ? ? ? D D D D D . . .
 r15  # n # # ? ? ? ? L ? D D D # # . #
 r16  # ? ? ? ? ? L L L L D D D D . . #
```

The `D` band is the main diagonal — the corridor the two units actually walk in
the opening (unit 0 from (0,0) toward (6,6), unit 1 from (16,16) toward (10,10),
then both to the central anchors). The two anti-diagonal corners, which are the
*opponent's* spawns and which our units never approach, are the hole.

---

## 6. How large does an edit have to be before the window notices?

Population: `k` cells flipped uniformly at random in a known map, 8 samples per
`(parent, k)`, 3 seeds, shipped build (`sim/maps_unknown_randk.json`). Compared
across `k`; games whose terrain the engine rejects are excluded (§10).

| k | valid games | fingerprint refused | mis-lock detected ≤24 | missed, visible later | missed, never | **escape rate of mis-locks** |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 72 | 21 | 22 | 21 | 8 | **56.9 %** (n=51) |
| 2 | 69 | 27 | 30 | 8 | 4 | **28.6 %** (n=42) |
| 3 | 72 | 36 | 33 | 3 | 0 | **8.3 %** (n=36) |
| 5 | 72 | 39 | 31 | 2 | 0 | **6.1 %** (n=33) |
| 8 | 63 | 51 | 12 | 0 | 0 | **0.0 %** (n=12) |
| 16 | 60 | 48 | 12 | 0 | 0 | **0.0 %** (n=12) |

Two things happen together as `k` grows: the fingerprint increasingly refuses the
map outright (21 → 51 of 72), and any lock that is still taken is caught inside
the window. **At k ≥ 8, 0 of 24 mis-locks escaped.** This is the mechanism that
actually protects the delivered construct — not a round margin. A map the
organisers ship that is genuinely different from map1/2/3 differs in dozens of
cells; the mimic class differs in 17/13/29 cells and is caught at r3–4.

---

## 7. What the escaped mis-locks cost behaviourally

Income on unfamiliar maps is not reported. The behavioural proxy is the
stay-or-blocked step rate — a real wall the table does not know produces blocked
steps, a phantom wall produces detours. Compared **within the same single-cell
population**, so terrain difficulty is held constant to one cell.

| parent | state | games | blocked % median | blocked % p95 | all-stay rounds median | rounds min |
|---|---|---:|---:|---:|---:|---:|
| map1 | lock correct (control) | 3 | 24.63 | 24.63 | 0 | 500 |
| map1 | mis-lock detected ≤24 | 443 | 24.20 | **28.10** | 0 | 500 |
| map1 | **mis-lock missed, visible later** | 236 | **24.03** | 27.50 | 0 | 500 |
| map1 | **mis-lock missed, never visible** | 101 | **24.13** | 27.10 | 0 | 500 |
| map2 | lock correct (control) | 3 | 21.13 | 21.13 | 0 | 500 |
| map2 | mis-lock detected ≤24 | 274 | 22.10 | 25.97 | 0 | 500 |
| map2 | **mis-lock missed, visible later** | 221 | **21.57** | 23.93 | 0 | 500 |
| map2 | **mis-lock missed, never visible** | 123 | **21.57** | 23.80 | 0 | 500 |
| map3 | lock correct (control) | 3 | 30.47 | 30.47 | 4 | 500 |
| map3 | mis-lock detected ≤24 | 273 | 31.47 | **37.53** | 4 | 500 |
| map3 | **mis-lock missed, visible later** | 169 | **30.80** | 35.13 | 4 | 500 |
| map3 | **mis-lock missed, never visible** | 152 | **30.60** | 33.77 | 3 | 500 |

Running 500 rounds on a table wrong by one bit is behaviourally
indistinguishable from running on a correct table. The only class with an
elevated p95 is `mislock detected` — those are the maps whose wrong bit is on our
path, which is exactly why they get caught. Maximum all-stay rounds over all
2 505 games: **38** (`map3_x_06-12_wall_removed`, seed 1, a 78-wall parent), well
short of anything resembling passivity.

**This is a proxy, not a price.** §11 states the one measurement that would turn
it into one, and why this audit did not take it.

---

## 8. The cost of widening, measured

The cost driver is not the round number: the cold layer is entered only when a
unit stands on a cell it has never stood on, so the amortised cycle cost is
proportional to the number of such **verification scans inside the window**.
Measured on the three known maps, 3 seeds, with the unbounded probe so every
scan is counted; compared against the shipped bound of 24.

| window bound | verification scans per game (mean of 9 games) | min–max | **known-map premium** |
|---:|---:|---:|---:|
| 4 | 6.67 | 6–8 | −18.0 |
| 8 | 12.22 | 10–15 | −22.6 |
| 12 | 14.89 | 13–20 | −24.9 |
| **24 (shipped)** | **22.22** | 19–29 | **−31 (anchor)** |
| 40 | 29.00 | 22–38 | −36.7 |
| 60 | 34.78 | 23–45 | −41.5 |
| 90 | 41.89 | 26–54 | −47.5 |
| 120 | 46.78 | 27–65 | −51.6 |
| 250 | 65.33 | 38–89 | −67.1 |
| **499 (unbounded)** | **77.11** | 40–96 | **−77 (anchor)** |

The premium column is a linear interpolation in scan count anchored on the two
contest-machine measurements in `src/CHANGELOG.md` 8.10 (window 24 → 6–9
amortised cycles → −31 gold; unbounded → 21–24 cycles → −77 gold). The implied
slope is **−0.838 gold per verification scan**. This is a consistency check as
well as an interpolation: the two independently-measured cycle counts and the two
independently-measured scan counts agree on one slope, which is what "scans are
the cost driver" predicts.

### 8.1 The trade curve

Cost from §8, coverage from §3 (the exhaustive single-cell population, 1 992
mis-locking games), both as functions of the same bound.

| bound | known-map premium | mis-locks caught | marginal gold vs shipped | marginal coverage vs shipped |
|---:|---:|---:|---:|---:|
| **24 (shipped)** | **−31** | **49.7 %** | — | — |
| 40 | −36.7 | 54.9 % | −5.7 | +5.2 pt |
| 60 | −41.5 | 59.1 % | −10.5 | +9.4 pt |
| 90 | −47.5 | 62.5 % | −16.5 | +12.8 pt |
| 120 | −51.6 | 66.2 % | −20.6 | +16.5 pt |
| 250 | −67.1 | 74.4 % | −36.1 | +24.7 pt |
| **499 / unbounded** | **−77** | **81.1 %** | **−46** | **+31.4 pt** |

Widening is **certain cost against uncertain benefit**: the −46 gold is paid on
every game on the known maps, while the +31.4 points of coverage is only cashed
on maps that (a) mis-lock at all and (b) differ in few enough cells to have
escaped, and §7 shows those maps have no measurable behavioural footprint.

---

## 9. Specifications

### S1 — Correct the recorded justification (documentation, zero code, zero risk)

**Land this.** The comment in `src/player.cpp` at `VERIFY_ROUNDS`, the 8.10
CHANGELOG entry, and §1.3 of `sim/reports/prelim_readiness_audit.md` all state
that the bound leaves a "6× margin" because contradictions "are observed at round
3 or 4 across every seed and map tested". Replace with the measured facts:

* The round-3/4 observation is a property of the mimic class (17/13/29 mismatched
  cells for mimic1/2/3, all inside the 7x7 interior), not a law. With the bound
  removed, the first-contradiction round has median 25, p90 237, p95 339,
  max 495, and 18.9 % never.
* The bound of 24 is at the **median**, not at a 6× margin. The shipped build's
  reported detection rounds max out at exactly 24 because the bound censors them.
* What actually protects the construct is that detection fires on the
  *earliest-observed* mismatching cell. **Measured escape rate of a mis-lock:
  56.9 % at 1 mismatched cell, 28.6 % at 2, 8.3 % at 3, 0/24 at ≥8.**
* The blind region is not "rarely-entered periphery" in general — it is
  specifically the two anti-diagonal corners plus the far edges, 36–47 % of the
  board, and **31.4 % of single-cell mis-locks** (626 of 1 992 games) have their
  evidence inside the region we *do* eventually traverse, just past round 24.

The existing blind-spot sentence ("terrain that contradicts a baked table only in
regions we rarely enter, where a wrong wall costs almost nothing") survives §7
intact and should be kept — it is the load-bearing argument, and it is now
measured rather than asserted.

### S2 — Do **not** widen `VERIFY_ROUNDS` (recommendation, priced)

| option | known-map premium | change vs shipped | coverage of single-cell mis-locks | verdict |
|---|---:|---:|---:|---|
| keep `VERIFY_ROUNDS = 24` | −31 | — | 49.7 % | **recommended** |
| `VERIFY_ROUNDS = 60` | −41.5 | **−10.5** | 59.1 % | rejected: pays certainly, buys the flattest part of §3.3 |
| remove the bound | −77 | **−46** | 81.1 % | rejected: −46 gold to detect errors with no measurable footprint (§7) |

Because the two contest-machine anchors and the measured scan counts agree on one
slope (§8), any other bound `X` can be priced without a new platform run as
`gold(X) = −31 − 0.838 × (scans(X) − 22.2)`.

### S3 — A frontier point that may dominate, **unpriced, do not land yet**

The only wall bits that can cost anything are the ones the router reads, and the
router reads `bpw` along the 1–3 step path it is about to take — i.e. cells
orthogonally near the unit, not the whole 5×5 window. Measured on the known maps
(3 seeds), comparing two detector shapes over the same trajectories:

| detector shape | condition | cells covered of 289 (mean) | cell comparisons per game (mean) |
|---|---|---:|---:|
| 5×5 window, `round <= 24` (**shipped**) | as delivered | **160.2** | ≈ 555 (22.2 scans × 25) |
| self + 4 orthogonal neighbours, **all game** | same event stream, narrower window, no round bound | **157.8** | ≈ 385 (77.1 scans × 5) |

Equal coverage count for fewer cell comparisons, over a *different and more
relevant* region (adjacent to the whole-game path rather than near the opening
path). **This is not a recommendation** — the cost model has only two anchors and
cannot separate per-event overhead from per-cell cost, and 77 gate crossings
versus 22 may swamp the cell saving. The measurement that would settle it is
`tests/icount.cpp` plus the paired-seat latency protocol on the contest machine,
which this audit did not run.

---

## 10. Two defects found in the shared tooling (not in the construct)

1. **`sim/make_unknown_maps.py` protected only two of the four spawns.**
   `protect()` and the stated invariants covered (0,0), (16,16) and (8,8) but not
   the opponent spawns (0,16) and (16,0). The engine refuses a scenario in which
   *any* unit starts on a wall, so a generator that walls either cell yields
   terrain that dies at setup with `ValueError: player occupies a wall`. This bit
   this audit: 6 of 838 exhaustive maps and 8 of 144 random-k maps, 42 games,
   all excluded from every statistic above. **Fixed** in `protect()`, the
   validator loop and the docstring; regenerating `sim/maps_unknown.json`
   afterwards is **byte-identical**, so the change is backward compatible for the
   nine maps already in the registry.
2. **`sim/audit_unknown_maps.py` cannot answer the window question.** Its alert
   rule ("an unknown map that ends `locked mapN` is mis-fingerprinted") is sound —
   no unfamiliar map can legitimately match a baked table — but it reads the probe
   state only at the end of the game, so it cannot distinguish "never mis-locked"
   from "mis-locked and recovered", and it has no detection round. It is
   unmodified; `sim/audit_unknown_lock.py` adds the per-round timeline and the
   truth-versus-table diff. Its map classes **do** include the corner-isomorphic
   trigger condition (mimic1/2/3 preserve everything outside the 7×7 interior);
   what they lacked was a *late*-contradiction class, which is what was added.

---

## 11. Limits of this audit

* **Host arm64, scalar fallback.** Behaviour only. Every cycle, instruction and
  alignment number quoted is the operator's existing contest-machine measurement,
  used as an anchor and never re-derived here.
* **map3's ground truth is the construct's own table.** `sim/maps.json` carries
  map3 as `limited: true`, decoded from `BAKED_W[2]`. The map3 arm therefore
  cannot detect a disagreement between `BAKED_W[2]` and the real official map3;
  it can only test the mechanism.
* **The population is an adversarial family, not a model of the organisers'
  generator.** Single-cell and random-k edits of our own baked maps are the set of
  maps that *can* mis-lock; they are not a prior over what the preliminary will
  ship. The k-curve in §6 is the bridge: it says how much a candidate map must
  differ before the window is reliable, and any genuinely new map differs by far
  more than the k ≥ 8 threshold at which escape was 0/24.
* **The escaped mis-locks are priced only at the ends.** §7 shows one wrong bit
  costs nothing measurable behaviourally and the CHANGELOG shows ~15 wrong central
  bits cost −689 gold. The product (escape probability × cost) is small at both
  ends and **unmeasured in the middle (k = 2–5)**. Closing that gap requires an
  income number on unfamiliar maps, which this audit was instructed not to
  produce. If the operator wants it closed, the clean design is a paired
  within-map comparison — same map, same seed, `VERIFY_ROUNDS` the only variable —
  over the **8 escaped k = 2–5 maps × 3 seeds × 2 builds = 48 simulator games**,
  zero platform games. It would settle one binary question: **is the widening's
  benefit on escaped maps large enough to outweigh a certain −46 gold on known
  maps?** This audit's answer without it is no.

---

## 12. Reproduction

```bash
# 1. build the frozen construct and the instrumented variants (verifies the
#    extraction hash, then patches the extracted source textually)
python3 sim/probe/build_lock_probe.py                      # -> /tmp/umr/*.dylib

# 2. prove the instrumentation is inert (identical log SHA-256, 14 games)
#    -- the record is embedded in the JSON companion as `equivalence_record`

# 3. coverage census -> which cells are compared when
python3 sim/audit_unknown_lock.py --workers 9 --seeds 0 1 2 \
    --json /tmp/umr/census.json coverage

# 4. registries (both committed, so the exact populations are reproducible)
python3 sim/audit_unknown_lock.py build --census /tmp/umr/census.json \
    --mode bands --out sim/maps_unknown_late.json
python3 sim/audit_unknown_lock.py build --mode exhaustive \
    --parents map1 map2 map3 --out /tmp/umr/maps_exhaustive.json
python3 sim/audit_unknown_lock.py build --mode randk --k 1 2 3 5 8 16 \
    --samples 8 --out sim/maps_unknown_randk.json

# 5. dry run (null + true positive), then the populations
python3 sim/audit_unknown_lock.py --workers 12 --seeds 0 1 2 \
    --json /tmp/umr/dryrun_existing.json adjudicate --registry sim/maps_unknown.json
python3 sim/audit_unknown_lock.py --workers 12 --seeds 0 1 2 \
    --json /tmp/umr/bands_verdict.json adjudicate --registry sim/maps_unknown_late.json
python3 sim/audit_unknown_lock.py --workers 16 --seeds 0 1 2 --quiet \
    --json /tmp/umr/exhaustive_verdict.json adjudicate \
    --registry /tmp/umr/maps_exhaustive.json --builds shipped24
python3 sim/audit_unknown_lock.py --workers 16 --seeds 0 1 2 --quiet \
    --json /tmp/umr/randk_verdict.json adjudicate \
    --registry sim/maps_unknown_randk.json --builds shipped24

# 6. deliberately-wrong-lock arm (one run per forced table)
for m in 0 1 2; do python3 sim/audit_unknown_lock.py --seeds 0 1 2 \
    --json /tmp/umr/force$m.json adjudicate --registry --maps map1 map2 map3 \
    --force-build /tmp/umr/probe_force$m.dylib --force-map $m; done

# 7. premium: verification scans per window bound on the known maps
python3 sim/audit_unknown_lock.py --workers 9 --seeds 0 1 2 \
    --json /tmp/umr/premium.json premium

# 8. roll everything up
python3 sim/summarise_unknown_lock.py --dryrun ... --out \
    sim/reports/unknown_map_robustness.json
```

New files, none of them in the operator-owned set: `sim/audit_unknown_lock.py`,
`sim/summarise_unknown_lock.py`, `sim/probe/build_lock_probe.py`,
`sim/maps_unknown_late.json`, `sim/maps_unknown_randk.json`, this report and its
JSON companion. `sim/make_unknown_maps.py` received the four-corner spawn guard
described in §10 with byte-identical output. `src/player.cpp`, `src/INFRA.md`,
`src/CHANGELOG.md`, `AGENT.md`, `tests/**`, `sim/engine.py`, `sim/scenario.py`,
`sim/abi.py`, `sim/audit_unknown_maps.py` and every committed report are
unmodified.
