# Miss taxonomy: what our zero-yield unit-rounds are made of, and whether closing them pays

Measurement and judgement only.  No strategy change, no platform submission, nothing under
`src/` touched.  Build under test is the frozen **`f18064c`** source extracted with
`git show f18064c:src/player.cpp`; verified `shasum -a 256` =
`0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`.  The worktree copy is a
*different file* (`d9be1e52...`, commit `895a27e` cut 84 instructions from it) and was not used.
Artifacts: `sim/reports/miss_taxonomy.json`, driver `sim/analyze_miss_taxonomy.py`.

## 0. Verdict up front

**The class that must shrink to close the hit-rate gap is `C_CONVERSION`, sub-class
`C1_no_gold_target` -- the `v > 2` scan gate marching an idle unit to its anchor instead of
at collectable sub-threshold gold.  A zero-instruction mechanism for it exists, it closes the
whole gap, and it is worth nothing.  This path is closed.**

| step | result |
|---|---|
| target, briefed sample (n=36 games) | ours **34.786%** vs theirs **41.146%**, gap **6.36 pp** |
| target, largest identifiable sample (n=66) | ours 34.364% vs theirs 42.776%, gap **8.41 pp** |
| simulator hit rate, same channel definition | **30.98%** (biased DOWN vs platform, see Sec 6) |
| miss unit-rounds classified | **10333** of 14970 graded, MECE residual **0** |
| pp available by re-choosing one unit's 3 actions | **24.66 pp** (open loop) |
| pp delivered by the zero-instruction gate change, **closed loop** | **+8.51 +- 0.94 pp** |
| net gold that gate change delivers, closed loop, same-seed paired | **+26.3 +- 54.7 gold/game** |
| ... while the *unchanged* opponent gains | **+101.7 gold/game**, so **relative score -75.3** |
| yield per scoring round while doing it | **5.077 -> 4.037** (-20.5%) |
| mean held-gold delta per unit-round | 1.353 -> 1.379, i.e. **+2.0%, nil** |

Hit rate and yield-per-hit lie on a **steep frontier**.  Ours is 34.8% x 4.67 gold, theirs is
41.1% x 4.19: a **118% hit-rate ratio against a 90% yield ratio**, whose realised mean
held-gold delta per unit-round differs by only **4.4%** (1.4906 vs 1.5568).  Sliding our build
along that frontier with a one-constant change reproduces their hit rate and buys no score.
The 6.36 pp is a *coordinate*, not a loss.

What is **not** closed, and is the one positive finding here: a *value-aware* re-choice for
**idle units only** (leave a scoring unit alone) returns **+414.6 +- 79.3 gold/game** for us
against +21.4 for the opponent (relative **+393.1**, 8/9 games).  That is a different mechanism
against the same classes -- it raises the mean 1.353 -> 1.768 rather than trading along the
frontier.  Sec 7 prices it, and Sec 9 labels the (upward) biases it carries.

## 1. Miss classification table: 3 maps x 4 classes

(a) share of all misses / (b1) hit-rate pp if the class were fully eliminated /
(b2) hit-rate pp actually reachable by re-choosing that unit's own 3 actions this round /
(b3) same, restricted to *position-preserving* alternatives (unit ends where it started) /
(c) gold/game after stock/flow separation, `novel + timing (+ burn delta)`.

### A. burn-cancelled (collected, then lost it)

| quantity | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| (a) share of all misses | 0.19% | 0.20% | 0.48% | 0.29% |
| (b1) pp if fully eliminated | 0.14 | 0.14 | 0.32 | 0.20 |
| (b2) pp convertible same-round | 0.12 | 0.12 | 0.24 | 0.16 |
| (b3) pp convertible position-preserving | 0.06 | 0.02 | 0.04 | 0.04 |
| (c) **novel** gold/game | 0.2 | 0.0 | 0.4 | 0.2 |
| (c) *timing* gold/game | 1.8 | 0.0 | 1.4 | 1.1 |
| (c) novel share of cellwise gold | 10.0% | n/a | 22.2% | 15.8% |
| (c) burn delta gold/game | +13.8 | +27.0 | +62.4 | +34.4 |
| (c) raw gain gold/game (tripwire >800) | 15.8 | 27.0 | 64.2 | 35.7 |
| gold accounting residual | +0.00 | +0.00 | +0.00 | +0.00 |
| contested share (cross-round) | 14.3% | 57.1% | 43.8% | 40.0% |
| miss unit-rounds (15 games) | 7 | 7 | 16 | 30 |

Sub-classes (pooled, 15 games):

| sub-class | misses | share of all misses | pp b1 | pp b2 | pp b3 | novel gold/game | timing gold/game |
|---|---|---|---|---|---|---|---|
| A1 bomb entry burned the pickup | 12 | 0.12% | 0.08 | 0.07 | 0.03 | 0.0 | 0.7 |
| A2 >=3-NPC trample burned the pickup | 18 | 0.17% | 0.12 | 0.09 | 0.01 | 0.2 | 0.4 |

### B. supply-side (positioning: nothing collectable reachable)

| quantity | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| (a) share of all misses | 57.67% | 58.61% | 63.52% | 59.86% |
| (b1) pp if fully eliminated | 41.52 | 40.24 | 42.18 | 41.32 |
| (b2) pp convertible same-round | 0.00 | 0.00 | 0.00 | 0.00 |
| (b3) pp convertible position-preserving | 0.00 | 0.00 | 0.00 | 0.00 |
| (c) **novel** gold/game | 0.0 | 0.0 | 0.0 | 0.0 |
| (c) *timing* gold/game | 0.0 | 0.0 | 0.0 | 0.0 |
| (c) novel share of cellwise gold | n/a | n/a | n/a | n/a |
| (c) burn delta gold/game | +63.2 | +84.4 | +140.8 | +96.1 |
| (c) raw gain gold/game (tripwire >800) | 63.2 | 84.4 | 140.8 | 96.1 |
| gold accounting residual | +0.00 | +0.00 | +0.00 | +0.00 |
| contested share (cross-round) | 24.6% | 29.5% | 24.9% | 26.3% |
| miss unit-rounds (15 games) | 2072 | 2008 | 2105 | 6185 |

Sub-classes (pooled, 15 games):

| sub-class | misses | share of all misses | pp b1 | pp b2 | pp b3 | novel gold/game | timing gold/game |
|---|---|---|---|---|---|---|---|
| B1 no visible gold in the 5x5 window at all | 4494 | 43.49% | 30.02 | 0.00 | 0.00 | 0.0 | 0.0 |
| B2 visible gold, all of it at Manhattan >= 4 | 680 | 6.58% | 4.54 | 0.00 | 0.00 | 0.0 | 0.0 |
| B3 gold at Manhattan <= 3, no 3-step route | 1011 | 9.78% | 6.75 | 0.00 | 0.00 | 0.0 | 0.0 |

### C. conversion (decision: aimed at something that could not pay)

| quantity | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| (a) share of all misses | 30.48% | 27.82% | 24.29% | 27.61% |
| (b1) pp if fully eliminated | 21.94 | 19.10 | 16.13 | 19.06 |
| (b2) pp convertible same-round | 20.22 | 18.40 | 15.25 | 17.96 |
| (b3) pp convertible position-preserving | 8.34 | 5.89 | 6.15 | 6.79 |
| (c) **novel** gold/game | 53.4 | 44.2 | 25.0 | 40.9 |
| (c) *timing* gold/game | 293.4 | 265.8 | 270.8 | 276.7 |
| (c) novel share of cellwise gold | 15.4% | 14.3% | 8.5% | 12.9% |
| (c) burn delta gold/game | +34.8 | +26.8 | +61.0 | +40.9 |
| (c) raw gain gold/game (tripwire >800) | 381.6 | 336.8 | 356.8 | 358.4 |
| gold accounting residual | +0.00 | +0.00 | +0.00 | +0.00 |
| contested share (cross-round) | 29.4% | 35.7% | 32.7% | 32.4% |
| miss unit-rounds (15 games) | 1095 | 953 | 805 | 2853 |

Sub-classes (pooled, 15 games):

| sub-class | misses | share of all misses | pp b1 | pp b2 | pp b3 | novel gold/game | timing gold/game |
|---|---|---|---|---|---|---|---|
| C1 scan found no `v>2` cell -> marched to the anchor | 2208 | 21.37% | 14.75 | 13.93 | 5.06 | 32.2 | 187.5 |
| C2 chosen gold target at Manhattan 4 | 233 | 2.25% | 1.56 | 1.48 | 0.75 | 1.8 | 22.3 |
| C3 chosen gold target sealed off | 412 | 3.99% | 2.75 | 2.54 | 0.98 | 6.9 | 66.9 |

### D. blocked (routing: payable reachable target, failed to arrive)

| quantity | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| (a) share of all misses | 11.66% | 13.37% | 11.71% | 12.24% |
| (b1) pp if fully eliminated | 8.40 | 9.18 | 7.78 | 8.45 |
| (b2) pp convertible same-round | 6.31 | 7.82 | 5.51 | 6.55 |
| (b3) pp convertible position-preserving | 1.04 | 1.48 | 1.14 | 1.22 |
| (c) **novel** gold/game | 26.0 | 51.8 | 17.2 | 31.7 |
| (c) *timing* gold/game | 287.4 | 330.0 | 274.6 | 297.3 |
| (c) novel share of cellwise gold | 8.3% | 13.6% | 5.9% | 9.6% |
| (c) burn delta gold/game | +35.0 | +43.8 | +49.4 | +42.7 |
| (c) raw gain gold/game (tripwire >800) | 348.4 | 425.6 | 341.2 | 371.7 |
| gold accounting residual | +0.00 | +0.00 | +0.00 | +0.00 |
| contested share (cross-round) | 31.5% | 32.1% | 29.9% | 31.2% |
| miss unit-rounds (15 games) | 419 | 458 | 388 | 1265 |

Sub-classes (pooled, 15 games):

| sub-class | misses | share of all misses | pp b1 | pp b2 | pp b3 | novel gold/game | timing gold/game |
|---|---|---|---|---|---|---|---|
| D1 `pass01` waypoint gate refused the LUT path | 1082 | 10.47% | 7.23 | 5.36 | 1.08 | 29.3 | 243.7 |
| D2 a requested step blocked at execution | 183 | 1.77% | 1.22 | 1.19 | 0.15 | 2.3 | 53.6 |
| D3 route ran, target paid nothing | 0 | 0.00% | 0.00 | 0.00 | 0.00 | 0.0 | 0.0 |

### Column totals (pooled)

| class | misses | share | pp b1 | pp b2 | pp b3 | novel g/game | timing g/game | raw g/game |
|---|---|---|---|---|---|---|---|---|
| A. burn-cancelled (collected, then lost it) | 30 | 0.29% | 0.20 | 0.16 | 0.04 | 0.2 | 1.1 | 35.7 |
| B. supply-side (positioning: nothing collectable reachable) | 6185 | 59.86% | 41.32 | 0.00 | 0.00 | 0.0 | 0.0 | 96.1 |
| C. conversion (decision: aimed at something that could not pay) | 2853 | 27.61% | 19.06 | 17.96 | 6.79 | 40.9 | 276.7 | 358.4 |
| D. blocked (routing: payable reachable target, failed to arrive) | 1265 | 12.24% | 8.45 | 6.55 | 1.22 | 31.7 | 297.3 | 371.7 |
| **total** | **10333** | **100.00%** | **69.02** | **24.66** | **8.06** | **72.7** | **575.1** | **861.9** |
| MECE residual | **0** | -- | -- | -- | -- | -- | -- | -- |

Every raw-gain figure is **below** the 800 gold/game tripwire, and after stock/flow separation
the novel component is **72.7 gold/game pooled across all four classes** -- 11.2% of the raw sum.

## 2. Which class must shrink, and the arithmetic

We need **+6.36 pp** (briefed n=36 sample) to **+8.41 pp** (largest identifiable n=66 sample).
Per 998 graded unit-rounds per game that is **+63 to +84** converted unit-rounds.

| class | pp available same-round | pp available position-preserving | can it close 6.36 pp? |
|---|---|---|---|
| A. burn-cancelled (collected, then lost it) | 0.16 | 0.04 | only partly |
| B. supply-side (positioning: nothing collectable reachable) | 0.00 | 0.00 | no -- structurally 0 |
| C. conversion (decision: aimed at something that could not pay) | 17.96 | 6.79 | yes on its own |
| D. blocked (routing: payable reachable target, failed to arrive) | 6.55 | 1.22 | yes on its own |

* **`B_SUPPLY` (59.86% of misses, 41.32 pp of the denominator) contributes exactly 0 pp.**
  By definition `max_pickup == 0`: no re-choice of that unit's three actions collects anything.
  It is the *largest* class and the *least* actionable.  A god-view check confirms this is not a
  fog artefact: only **447 of 6185** supply misses (7.2%) had any real gold inside the reachable
  set at all; in the rest the neighbourhood is genuinely empty.  Shrinking `B_SUPPLY` is a
  *multi-round positioning* problem, not a per-round decision problem, and is out of scope here.
* **`C_CONVERSION` is the class that must shrink.**  17.96 pp same-round, 6.79 pp
  position-preserving; both exceed +6.36 pp on their own.  Inside it, `C1_no_gold_target`
  alone carries 13.93 pp same-round and 5.06 pp position-preserving.
* `D_BLOCKED` carries 6.55 pp same-round but only 1.22 pp position-preserving.  Its internal
  attribution matters for the parallel map1 wall repricing and is worth stating precisely:

  | mechanism | misses | note |
  |---|---|---|
  | `D1_gate`, wall-attributable | 628 | the `pass01` waypoint gate found a wall on a LUT waypoint |
  | `D1_gate`, **bomb-richness-gate**-attributable | 454 | the gate would have passed if the unit were not `rich` (held < 100) |
  | `D2_exec`, engine refused: static wall | **0** | |
  | `D2_exec`, engine refused: out of bounds | **0** | |
  | `D2_exec`, engine refused: **visible enemy unit** | 155 | `pass01` cannot see enemies |
  | `D2_exec`, engine refused: **our own teammate** | 28 | the teammate check is retired in f18064c (`player.cpp:151`) |

  Two readings.  First, **58.0%** of gate-blocked misses are wall-caused and **42.0%** are
  caused by the *bomb richness gate*, so wall repricing owns at most the former slice.
  Second, and this **independently confirms the sibling repricing report's claim that "the
  engine never refuses a walled step from us"**: across 14,970 unit-rounds the engine refused
  a step for a wall or a boundary **exactly 0 times**.  The build's own `pass01` gate is
  airtight once the wall table is locked; every single execution-level block is an *actor
  collision* with an enemy or with our own teammate, which is what the gate structurally
  cannot see.  `D2_exec` is therefore an actor-collision class, not a wall class, and no
  contradiction with the sibling report exists on any figure I can compare.
* `A_BURN` is negligible (30 unit-rounds, 0.20 pp): collecting and then losing it to a bomb or
  trample explains almost none of the gap between the pickup-based and held-delta hit rates
  (31.18% vs 30.98%).
* **Secondary finding worth recording: burn is almost entirely a miss-round phenomenon.**
  Total seat-1 burn is **219.4 gold/game** pooled, of which **99.4%** lands on miss unit-rounds
  and **82.5%** on unit-rounds that collected literally nothing (152.2 / 182.6 / 323.4 gold/game
  on map1/map2/map3).  The burn-delta column of Sec 1 sums to **214.1 gold/game**, i.e. a
  same-round re-choice would avoid 98% of all our burn.  Unlike the cellwise figures this
  one is **novel by construction** -- a burn is a purse loss, not a cell, so there is no later
  round in which we collect it anyway and no stock/flow discount applies.  The mechanism is
  visible in the source: `player.cpp:401` merges bombs into the blocked bitmap only when
  `held >= 100`, and the comment justifies it as "a poor unit burns 10% x 0 = 0, bombs are
  transparent" -- but the engine charges `(held + 9) // 10`, which is 0 only at `held == 0`.
  A unit holding 50 burns 5.  **Important caveat: this is not separable evidence.**  The
  closed-loop runs in Sec 3 already avoid burn (their objective is net delta, not pickup), and
  they returned +8.8 and +414.6 gold, so the 214 gold must not be added on top of them.
* `D3_arrived_empty` is **exactly 0** in 14,970 unit-rounds.  That is a mechanics proof, not a
  coincidence: if the build aims at a `v>2` cell at Manhattan <= 3 that is genuinely reachable
  and nothing obstructs the emitted route, `(65v+99)//100 >= 2` is collected with certainty.

## 3. Does closing it pay?  Closed-loop, same-seed paired

This is the part that decides the round.  Three closed-loop variants, seat 1 only, opponent
left as the unmodified frozen build, `dispatch=fixed`, 3 maps x seeds 0,1,2 = 9 paired games each.

| variant | our net gold | opponent net gold | **relative** | hit rate | yield/hit | mean delta/unit-round | Delta>=8 rate |
|---|---|---|---|---|---|---|---|
| **zero-instruction gate change** (`v>2` -> `v>0`) | +26.3 +- 54.7 | +101.7 +- 31.6 | **-75.3** (3/9) | 0.3081 -> **0.3931** (+8.51 pp) | 5.077 -> 4.037 | 1.353 -> 1.379 | 0.0523 -> 0.0488 |
| perfect free oracle, position-preserving, idle units only | +8.8 +- 56.1 | +24.9 +- 33.6 | **-16.1** (6/9) | 0.3081 -> **0.3595** (+5.14 pp) | 5.077 -> 4.452 | 1.353 -> 1.362 | 0.0523 -> 0.0483 |
| perfect free oracle, value-aware, idle units only | +414.6 +- 79.3 | +21.4 +- 36.8 | **+393.1** (8/9) | 0.3081 -> **0.4788** (+17.08 pp) | 5.077 -> 4.178 | 1.353 -> 1.768 | 0.0523 -> 0.0538 |

Readings:

1. **The gap is closable and closing it is worthless.**  The gate change delivers
   **+8.51 pp** of hit rate -- more than the briefed 6.36 pp and more than the n=66 sensitivity
   bound of 8.41 pp -- for **+26.3 +- 54.7** gold, i.e. nil.  It also hands the *unchanged*
   opponent +101.7 gold, so the **relative** score moves -75.3 and only 3 of 9 games improve.
2. **The mechanism is the frontier.**  Yield per scoring round falls 5.077 -> 4.037 (-20.5%) as
   hit rate rises, and the product -- the mean held-gold delta, which *is* income -- moves
   1.353 -> 1.379.  Sub-threshold cells pay 1-2 gold; the build's own hits pay 5.1.
   The platform pair sits on the same frontier: their hit rate is 118% of ours and their
   yield per hit is 90% of ours, and the realised mean held-gold delta per unit-round -- the
   quantity that actually becomes score -- differs by only **4.4%** (1.4906 vs 1.5568).
   (`hit_ratio x yield_ratio = 1.0615` overstates `mean_ratio = 1.0444` because the channel has a
   small negative tail: 0.687% of our unit-rounds and 0.482% of theirs lose held gold outright.)
   So a 6.36 pp hit-rate gap corresponds to a 4.4% income gap, and the taxonomy's job is to
   say whether the pp or the income is the thing you can actually move.  It is the income.
3. **Position preservation is not the missing ingredient either.**  The `hold` oracle -- which
   *is* the positional guard `path_harvest_oracle.md` Sec 5 said was untested -- returns
   +8.8 +- 56.1 gold for +5.14 pp.  Also nil.
4. **The one thing that does pay is value-aware re-choice for idle units:** +414.6 +- 79.3 gold,
   relative +393.1, 8/9 games, and it raises the *mean* 1.353 -> 1.768 (+30.7%) rather than
   trading along the frontier.  Note this **contradicts nothing** in the path-harvest oracle:
   its L1/L2/L3 rungs re-chose actions on *every* round including scoring ones, which walks a
   producing unit off its cell.  The difference between -832 and +415 gold/game is the single
   guard *"never touch a unit that is already scoring"*.  That is a genuinely new result and
   it is the only live lead this round produced.

Per-map closed-loop detail for the gate change:

| map | our net gold | opponent | hit-rate pp | rounds where the replica diverges |
|---|---|---|---|---|
| map1 | +18.7 +- 90.3 | +103.0 | +8.32 +- 1.75 | 181 of 492 |
| map2 | +33.3 +- 90.0 | +108.7 | +10.65 +- 1.47 | 198 of 492 |
| map3 | +27.0 +- 140.0 | +93.3 | +6.55 +- 1.09 | 154 of 492 |

`threshold=1` (accept `v>=2`, the variant the frozen source's own line 411 records as
platform-judged-negative) is also flat locally: **-21.6 +- 62.7 gold** for +3.43 pp.
Local and platform agree in sign, which is the strongest cross-validation available here.

## 4. Contention: measured across the round boundary, and it is not zero

Because `fixed_costs=(200, 201)` makes seat 1 the faster mover, the engine settles
`(seat 1, all seven NPCs, seat 2)` -- confirmed from the log's own `end.dispatch_order`,
`[1, -5, -7, -4, -3, -2, -6, -1, 2]`.  At the moment seat 1 acts, `positions` still holds every NPC and enemy start cell and
`board` is untouched by them, so **within-round theft is structurally impossible**.  Reporting
it as zero would be vacuous, so it is measured across the boundary instead: a cell inside this
round's reachable set that a third party drained during the *previous* round, after we acted.

That attribution is closed against the engine's own accounting.  For every round the
reconstructed third-party removal total must equal `sum(end.npcs[].pickup) +
sum(end.players[2].units[].pickup)`; it does on **7500 of 7500** rounds.

| class | contested share of its misses | narrow variant (previous round's chosen target drained) |
|---|---|---|
| A. burn-cancelled (collected, then lost it) | 40.0% | 4 unit-rounds |
| B. supply-side (positioning: nothing collectable reachable) | 26.3% | 135 unit-rounds |
| C. conversion (decision: aimed at something that could not pay) | 32.4% | 74 unit-rounds |
| D. blocked (routing: payable reachable target, failed to arrive) | 31.2% | 64 unit-rounds |

Contention is a *distal* cause spread fairly evenly across the substantive classes
(26.3% / 32.4% / 31.2% for B / C / D; `A_BURN`'s 40% is noise at n=30), which is
why it is an annotation and not a class: it changes *why* a cell was empty, not *what our
decision could have done about it this round*.  It is also the one number here that the local
NPC model distorts most (Sec 6), so it should be read as a rough magnitude only.

## 5. Per-map cross-check against the platform deficit ranking

| map | platform theirs/ours hit ratio | platform ours hit | **simulator** ours hit | C_CONVERSION share of misses | C pp same-round | B_SUPPLY share | B3 walled-off share | map walls |
|---|---|---|---|---|---|---|---|---|
| map1 | **1.334** | 36.02% | 28.00% | 30.48% | 20.22 | 57.67% | 9.96% | 40 |
| map2 | **1.177** | 44.34% | 31.34% | 27.82% | 18.40 | 58.61% | 4.09% | 24 |
| map3 | **0.967** | 24.00% | 33.59% | 24.29% | 15.25 | 63.52% | 15.48% | 78 |

* `C_CONVERSION`'s share **and** its convertible pp rank map1 > map2 > map3, which is exactly
  the platform deficit ranking (1.334 > 1.177 > 0.967).  `B_SUPPLY` ranks inversely.
  **But with only three maps a perfect rank agreement has p >= 1/6 = 0.167 under the null**, so
  this cannot be called significant.  It is a consistency check that passed, nothing more.
* The `B3_walled_off` share tracks wall count monotonically (map3 78 walls -> 15.48%, map1 40 ->
  9.96%, map2 24 -> 4.09%).  That is an independent validity check on the class definition.
* **The simulator's per-map hit-rate ordering is perfectly inverted relative to the platform.**
  Platform: map2 44.3% > map1 36.0% > map3 24.0%.  Simulator: map3 33.6% > map2 31.3% >
  map1 28.0%.  Spearman rho = -1.  **Per-map pp figures in this report must therefore not be
  used to target a specific map**; only the pooled figures and the closed-loop paired deltas
  carry weight.  The most likely cause is the documented NPC over-greed, which bites hardest
  where gold is densest and walls fewest.

## 6. Denominator reconciliation and simulator fidelity

| quantity | value | note |
|---|---|---|
| platform channel definition | `end.players[].units[].gold` differenced round over round | verified in `gold_delta_channel.json.channel.field` |
| platform n | 35928 unit-observations / side, 36 games | = 36 x 2 x 499, so round 0 has no predecessor |
| simulator, same definition | **30.98%** over 14970 graded unit-rounds | round 0 dropped identically; locally `start[r] == end[r-1]` exactly |
| simulator, pickup-based variant | 31.18% | differs by only 0.20 pp, i.e. burn almost never cancels a pickup |
| **sim-vs-platform gap** | **3.81 pp low** | direction: simulator hit rate is biased **DOWN** |

The simulator reproduces our hit rate to within 3.81 pp but **the per-map pattern is inverted**
(Sec 5).  That is a real fidelity limit and it caps how far any pp figure here can be trusted:
the pooled magnitude is credible to roughly +-4 pp, the per-map decomposition is not credible
at all.  What *is* trustworthy is the same-seed paired closed-loop delta in Sec 3, because both
legs run in the same simulator against the same scenario digest and the bias cancels in sign.

## 7. Pricing at 1.6 gold/instruction

| candidate | instruction cost | budget it would need | measured closed-loop return | verdict |
|---|---|---|---|---|
| `C1` gate change `v>2` -> `v>0` | **0** (one constant in `_mm256_set1_epi32`) | none | +26.3 +- 54.7 gold, relative -75.3 | **closed: free and still not worth it** |
| `C1` gate change `v>2` -> `v>1` | **0** | none | -21.6 +- 62.7 gold | closed; platform already judged it negative |
| position-preserving idle re-choice | >= hundreds of instructions (125-sequence search) | headroom buys **~5 instructions** | +8.8 +- 56.1 gold | closed |
| widen the bomb richness gate below `held == 100` | small: the gate is already computed at `player.cpp:401` | headroom buys **~134 instructions**, but see the caveat | not separably measured; already inside the Sec 3 runs | **open, not separable** |
| **value-aware idle re-choice** | oracle is thousands; a cheap approximation is the open question | headroom buys **~246 instructions** | +414.6 +- 79.3 gold, relative +393.1 | **open, biased UP** |

Carry both pricing caveats.  The 11 gold/ns rate holds only inside the +-20 ns crossover band
and decays outside it, and 1.6 gold/instruction is an **average**: the frozen source's own
header records that deleting 84 instructions returned only 5.6 cycles, about six times below
average.  So ~246 instructions is a conservative *ceiling* on the budget the one live lead
could justify, not a promise, and it must additionally be discounted for:

* **self-play**: the opponent here is a copy of ourselves, and it gained +21.4 gold under the
  `free` variant.  Against T-1, which contests the same central cells far more effectively, the
  same cells would not be free.  Direction: **biased UP**.
* **NPC over-greed**: `sim/README.md` Sec 7 records 39.18% per-action accuracy and NPCs
  over-eating by +24%..+71%, which "over-estimates central competition, under-estimates central
  residency and relatively over-estimates outer-ring routes".  The `free` variant sends idle
  units off the anchor onto outer cells, so its value is **relatively over-estimated**.
* **absolute income is not comparable**: local net gold is 1358/game against a full-strength
  copy of ourselves; the platform's uncontested figure is 2182.4.  Only paired deltas transfer.

## 8. Substrate proofs (shown, not asserted)

| claim | evidence |
|---|---|
| frozen source is the one under test | `git show f18064c:src/player.cpp` -> `shasum -a 256` = `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd` (expected, matched); worktree copy is `d9be1e523ca523f1a2d7cecd4faa52971511670d57e3316ef25a32758f455d22` |
| **verbatim passthrough** | measurement run `log_sha256` = `f0388bbe7c197b2ae542c8647fad34f65362eb7a61891291606910d8ec8cc512` **equals** the plain baseline run's `f0388bbe7c197b2ae542c8647fad34f65362eb7a61891291606910d8ec8cc512` for map1 seed 0; `trajectory_identical_all` true on all 15 games |
| seat 1 moves first | `end.dispatch_order` = `[1, -5, -7, -4, -3, -2, -6, -1, 2]` |
| harvest model is exact | model pickup 1254 = engine 1254, model burn 132 = engine 132, exact on 1000/1000 unit-rounds (map1 seed 0) |
| **selector replica is bit-exact** | predicted triple equals the `.so`'s emitted triple on **984/984 = 100%** steady-state unit-rounds; and driving the game with the replica at `threshold=2` from round 8 on reproduces the baseline **log byte for byte** (`log_sha256_equal` true on 9/9 games) |
| fog discipline | `fog_discipline()` ran on **every** round of the selfcheck game (500 rounds) and never fired |
| contention attribution is exact | third-party removal total = NPC + seat-2 pickups on **7500/7500** rounds |
| MECE | class residual **0** on every map and pooled; `D3_arrived_empty` empirically 0 |
| value model vs ground truth | 17 of 14970 graded unit-rounds disagree (0.11%), **all** in the direction of the model under-stating pickup (16 under / 0 over) -- a step-3 cell at Chebyshev 3 lies outside the radius-2 window, so fog hides its value.  Miss/hit is always taken from the log, never the model |

One host-build caveat, stated for completeness: the arm64 host takes the guarded scalar
fallback rather than the AVX2 path.  The two are behaviourally identical by inspection -- both
mark `v > 2` gold and `v == -3` bombs over the same clipped 5x5 window and reduce through the
same `TT.bestrow` table -- so target choice and bomb memory are unaffected; only latency is,
and latency is not the diagnostic target.  The `.so` is not byte-reproducible (Mach-O UUID),
but the *source* hash is pinned and verified.

## 9. Bias register

| number | direction | reason |
|---|---|---|
| simulator hit rate 30.98% | **biased DOWN** ~3.8 pp | over-greedy local NPCs strip gold before we arrive; self-play against a full-strength clone |
| per-map hit rates | **ordering inverted**, not merely shifted | see Sec 5; do not target a map from this report |
| class shares and pp b1/b2/b3 | unbiased *given* the trajectory (exact algebraic split, residual 0) but inherit the hit-rate bias in the denominator | |
| `B_SUPPLY` share | biased slightly **UP** | fog scores an invisible cell as 0, so 447/6185 supply misses actually had reachable gold |
| raw per-round gain sums | **biased UP, dominant** | stock/flow double-count; 88.8% of the cellwise gold is *timing* |
| novel gold 72.7/game | still biased **UP** | it ignores the positional cost of the detour, which Sec 3 prices |
| burn-delta component | signed and exact | `gain == cellwise pickup gain + burn delta` by construction, residual 0 |
| contested share | **biased UP** | local NPCs over-eat by +24%..+71%, so third-party drain is over-counted |
| closed-loop paired deltas | trustworthy in **sign**; magnitude biased **UP** for the `free` variant | self-play redistribution + NPC bias over-values outer-ring routes |
| `thr0` negative verdict | **robust** | it agrees in sign with the platform note on `player.cpp:411`, and the free-and-still-worthless conclusion does not depend on magnitude |

## 10. Reproduce

```bash
git show f18064c:src/player.cpp > /tmp/gr_miss/player_f18064c.cpp
shasum -a 256 /tmp/gr_miss/player_f18064c.cpp   # 0ecce6fc...84fdd
cp /tmp/gr_path/shim.h /tmp/gr_miss/shim.h      # stubs the x86 prefetch tokens
clang++ -O2 -std=c++17 -shared -fPIC -I$PWD/src -include /tmp/gr_miss/shim.h \
        -o /tmp/gr_miss/base.so /tmp/gr_miss/player_f18064c.cpp

# substrate proofs (per map)
for m in map1 map2 map3; do
  python3 -m sim.analyze_miss_taxonomy selfcheck --map $m \
          --base-so /tmp/gr_miss/base.so --seed 0 --out /tmp/gr_miss/selfcheck_$m.json
done

# primary taxonomy: 3 maps x 5 seeds
python3 -m sim.analyze_miss_taxonomy taxonomy --map map1 --map map2 --map map3 \
        --base-so /tmp/gr_miss/base.so --seeds 0 1 2 3 4 --jobs 4 \
        --out /tmp/gr_miss/raw.json

# closed loop: perfect free oracle, idle units only
for L in hold free; do
  python3 -m sim.analyze_miss_taxonomy realized --map map1 --map map2 --map map3 \
          --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --level $L \
          --out /tmp/gr_miss/realized_$L.json
done

# closed loop: the zero-instruction gate change, with threshold=2 as the exactness control
for m in map1 map2 map3; do
  python3 -m sim.analyze_miss_taxonomy threshold --map $m \
          --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --thresholds 2 1 0 \
          --out /tmp/gr_miss/thr_$m.json
done

# frontier table (hit vs yield vs opponent redistribution)
python3 -m sim.analyze_miss_taxonomy frontier --map map1 --map map2 --map map3 \
        --base-so /tmp/gr_miss/base.so --seeds 0 1 2 --out /tmp/gr_miss/frontier.json

# final artifacts
python3 -m sim.analyze_miss_taxonomy finalize --raw /tmp/gr_miss/raw.json \
        --realized /tmp/gr_miss/realized_hold.json --realized /tmp/gr_miss/realized_free.json \
        --threshold /tmp/gr_miss/thr_map1.json --threshold /tmp/gr_miss/thr_map2.json \
        --threshold /tmp/gr_miss/thr_map3.json --frontier /tmp/gr_miss/frontier.json \
        --selfcheck /tmp/gr_miss/selfcheck_map1.json \
        --out-json sim/reports/miss_taxonomy.json --out-md sim/reports/miss_taxonomy.md
```

Wall clock on one arm64 host with a sibling agent competing for CPU: selfcheck ~10 s/map,
taxonomy 45 s at `--jobs 4`, each `realized` level ~60 s, threshold sweep ~90 s/map in parallel,
frontier 3 min.  Total under 8 minutes.

## 11. Sample sizes

| measurement | maps | seeds | games | unit-rounds |
|---|---|---|---|---|
| primary taxonomy | 3 | 5 (0-4) | 15 measured + 15 baseline | 14970 graded |
| closed loop, each of 2 oracle levels | 3 | 3 (0-2) | 9 variant + 9 baseline | -- |
| closed loop, each of 3 thresholds | 3 | 3 (0-2) | 9 variant + 9 baseline | -- |
| frontier table | 3 | 3 (0-2) | 27 variant + 9 baseline | -- |
| substrate selfcheck | 3 | 1 | 3 measured + 3 baseline | 3,000 |

## 12. Corrections to the brief

* **Nothing in the brief was found to be wrong.**  Every number I was handed re-verified at its source: `gold_delta_channel.json.pooled` gives ours `0.3478624` / theirs `0.4114618`, gap **6.360 pp**, n=35,928 per side; the mid-task update's n=48 (5.816 pp) and n=66 (8.412 pp) figures verify in `archive_backfill.json.fog_free_channel`; the frozen source hash is exactly `0ecce6fc...84fdd` and the worktree copy is indeed a different file.
* Two brief statements are **more precise than stated** rather than wrong.  (i) The gap is 6.360 pp, not 6.3 pp, and the enlarged-sample range is 5.82-8.41 pp; this report carries both. (ii) The channel is `end[r] - end[r-1]`, so the platform denominator is 499 differences per unit (36 x 2 x 499 = 35,928), not 500.  I dropped round 0 to match, giving 998 graded unit-rounds per game rather than 1000.
* One brief expectation was **not borne out, in our favour**: contention is *not* zero and is not merely a boundary artefact.  26-32% of misses in each substantive class (and 40% of the 30 `A_BURN` misses, which is noise at that n) had a reachable cell drained by a third party in the previous round.  Within-round theft is impossible as briefed, and I state that explicitly (Sec 4) rather than reporting a vacuous zero.
* One published number I could not reproduce as stated, flagged rather than averaged: `path_harvest_oracle.md` Sec 5 reports the closed-loop myopic path optimum at **-832 +- 91 gold/game** and concludes "greedy three-step path value is the wrong objective". That is correct for *its* intervention (re-choose on every round).  Restricting the identical search to **idle units only** flips the sign to **+415 +- 79 gold/game**, 8/9 games.  The two results are compatible -- the oracle report itself flagged the positional guard as untested -- but the headline sentence should not be read as closing the whole line.
* The frozen source's own comment at `player.cpp:411` records the `>=2` pickiness variant as platform-judged-negative (`1184` vs `2388`).  Read carefully that is a **loss margin against an opponent in one game**, not a paired A/B against our own `>=3` build, so on its own it is suggestive rather than decisive.  This round supplies the missing paired evidence: locally the `>=2` variant is -21.6 +- 62.7 gold and the `>=1` variant is +26.3 +- 54.7 gold, both nil, which agrees with the note in sign.
* **Cross-validation with the parallel map1 wall repricing** (`sim/reports/map1_wall_repricing.md`, same frozen source hash, independently reconstructed selector): **no contradiction found on any comparable figure.**  We agree that the `ok` gate fails inside `player.cpp:504-506` before the engine sees anything (I measure exactly 0 engine-level wall or bounds refusals in 14,970 unit-rounds, which is their claim proved on a second sample); that the blocked class's novel gold is small (their 33.7 +- 7.8 gold/game on map1 vs my `D_BLOCKED` novel 26.0 gold/game on map1, different class boundaries, same order); that closed-loop repair of the blocked class is flat-to-negative (their -69 to -913, my position-preserving +8.8 +- 56.1); and that the lesion is not map1-specific.  I add two things they do not price: 42.0% of gate-blocked misses are caused by the **bomb richness gate** rather than by walls, and every execution-level block is an actor collision (155 enemy / 28 teammate).  Wall repricing therefore owns at most the 58.0% gate-wall slice, which is smaller than a wall-count-based split would suggest.
* `sim/OPPONENTS.md` line 477's "our side" rows are correctly marked unusable (102-build mixture, 军规 28) and were not used for any figure here.  The burst-rate comparison in that file is against that mixture and is likewise unused.

