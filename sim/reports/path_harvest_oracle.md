# Path-harvest oracle: what "one target, walk three steps" costs us

Measurement only. No strategy change, no platform submission, `src/player.cpp` byte-identical to
`f18064c`. Artifacts: `sim/reports/path_harvest_oracle.json`, driver `sim/analyze_path_oracle.py`.

## 0. Verdict up front

| quantity | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| **gross upper bound, net gold/game** (mean ± SE, n=5 seeds) | **1003.8 ± 29.9** | **1071.2 ± 52.9** | **1158.4 ± 69.3** | 1077.8 ± 30.7 |
| gross upper bound, pickup-only gold/game | 861.8 ± 24.5 | 893.0 ± 39.5 | 858.6 ± 42.8 | 871.1 ± 21.4 |
| of which *novel* gold (base never re-enters the cell) | 119.0 ± 18.6 | 144.8 ± 16.7 | 78.0 ± 8.5 | 113.9 ± 8.8 |
| of which *timing* gold (base harvests it a few rounds later anyway) | 836.6 ± 43.8 | 859.8 ± 50.1 | 865.2 ± 56.3 | 853.9 ± 29.0 |
| **closed-loop realized delta, all three steps free** | -864.7 ± 230.3 | -894.3 ± 126.5 | -738.3 ± 71.7 | -832.4 ± 90.8 |
| **closed-loop realized delta, third step free only** | +57.7 ± 35.1 | -156.3 ± 55.4 | -95.3 ± 210.8 | -64.7 ± 73.6 |

**The >800 gold/game tripwire FIRED** on all three maps. It is not a bug — the model is provably
exact — it is the expected looseness of a per-round counterfactual summed over 500 rounds. See §6.
The operationally meaningful figure is not 1078 but **114 ± 9 gold/game of novel gold**, and even
that is not realizable: playing the oracle *loses* 832 gold/game, and the cheapest rung of the
ladder is -65 ± 74.

Bias labels: the pickup/net bounds are **biased UP** (stock/flow double-count, dominant) and
mildly **biased DOWN** by fogged gold scoring as zero. Closed-loop deltas are same-seed paired
and trustworthy in *sign and rough magnitude*, but the local NPC model is over-greedy and
over-centralised, so a change whose value comes from central efficiency is **under**-estimated
locally — i.e. the true closed-loop numbers could be somewhat less negative than shown, but the
sign is robust across 9 games and 3 maps.

## 1. Method

### 1.1 Open-loop measurement (primary)

`PathOracleStrategy(open_loop=True)` wraps seat 1:

1. call the real `moveDecision` from `/tmp/gr_path/base.so`;
2. rebuild the visible state from the `PlayerInput` the engine just handed the strategy;
3. score the base's requested action pair and five constrained optima;
4. **return the base's six actions, `k`, `order` and `vp` verbatim.**

Because step 4 is a verbatim passthrough, the trajectory does not drift. Demonstration: for all
15 games the measurement run's `log_sha256` equals the plain baseline run's `log_sha256` for the
same map and seed (`trajectory_identical_all_seeds: true` on every map, `dispatch="fixed"` so no
clock is read). Same-seed paired by construction: the two runs are the *same* game.

### 1.2 Seat choice makes the counterfactual exact, not approximate

`fixed_costs=(200, 201)` makes player 1 the faster mover. `GameEngine._dispatch` then settles
`(faster player, all seven NPCs, slower player)`, so when seat 1 acts **no NPC and no enemy unit
has moved yet**. Therefore:

* every gold value the oracle plans against is the value seat 1 would actually collect;
* the enemy cells that block are exactly the enemy start cells;
* the `>=3 NPC` trample count at a destination is exactly the start-of-round count.

The only residual error is fog, which the information constraint forces on us anyway.

### 1.3 Exact fields read, and the fog guarantee

Only these enter the model:

| field | note |
|---|---|
| `PlayerInput.grid` | already fog-filtered by `GameEngine.render_filtered_ground`; every non-visible cell **is** the `-5` sentinel |
| `PlayerInput.my_units` | own unit cells |
| `PlayerInput.my_units_gold` | own held gold, read **live** |
| `PlayerInput.visible_enemies` | `(-1,-1)` when fogged |
| `PlayerInput.visible_npcs` | for the trample rule |
| static wall table of the map | the frozen build fingerprints and locks the identical `BAKED_W` table by round 4 |
| bombs remembered within the current 20-round wave | purged as soon as a currently visible cell proves the bomb is gone |

**Mechanism that guarantees no fogged read:** the oracle does not filter a god-view grid, it reads
the seat's own already-filtered array. A fogged gold amount is *absent*, not merely unused — the
value at that index is `-5`, and `extract_state` maps anything that is not `>0 / -1 / -3` to
"empty and passable". As a belt-and-braces check, `fog_discipline()` asserts that every gold value
that entered the model lies inside the radius-2 Chebyshev visibility union of the seat's own two
units; it ran on every 50th round of all 15 games and never fired.

The `oracle` (log) sub-command was also fixed: official logs record a **god-view** `start.grid`
(`render_full`), which the inherited code read directly. It now re-applies the seat's own fog
filter and strips the `-2 / -4` actor marks first, and filters enemies/NPCs to the same union.

### 1.4 Held gold and the stale-copy trap

The audited defect (3) is moot in the primary route: held gold is read live from
`PlayerInput.my_units_gold`, never from a log. The secondary log route now takes held gold from
`end[r-1].units[].gold` rather than `start[r]`, precisely because of the documented
`start[r].cost == end[r-1].cost` stale-copy trap. Held gold only scales bomb/trample penalties.

### 1.5 Model fidelity — the strongest available validation

The information-constrained model reproduces the engine **exactly**, round by round, for the
base's own action (map1, seed 0, 500 rounds):

```
model pickup sum 1254   engine pickup sum 1254   exact-match rounds 500/500
model burn   sum  132   engine burn   sum  132   exact-match rounds 500/500
```

At game level across all 15 games the model's summed base net differs from the engine's final
`net_gold` by -2.4 (map1), -1.6 (map2), -1.2 (map3) gold, i.e. ~0.15%. The residual is a bomb
remembered inside fog that some other actor consumed out of sight — unavoidable under the
information constraint, and it applies identically to base and oracle so it cancels in the delta.

This validation is what licenses the whole exercise: a wrong mechanics model would show up here.
(It did: the inherited stale-bomb memory inflated modelled burn to 649 vs the true 132, which by
itself inflated the raw bound by ~440 gold/game. Fixed by purging a remembered bomb the moment a
visible cell proves it is gone.)

### 1.6 Fast path and its equivalence check

The inherited `joint_outcomes` enumerates 125 x 125 = 15,625 dict-copying pairs per round; the
decomposition needs six such searches per round, i.e. ~8 M evaluations per game. Measured
reference cost: **23.0 ms/round**, so the exhaustive route alone would be ~11.5 s/game for one
search and roughly a minute per game for all six — before the engine.

Structure exploited instead:

* a unit's three-step path touches at most three cells, so a flat 289-int board plus a ≤3-entry
  overlay dict replaces the whole-board copy;
* the second unit's outcome differs from its **teammate-free** outcome *only if* its clean
  trajectory attempts the first unit's final cell, or enters a cell the first unit depleted or a
  bomb it consumed. Inverted indices `by_attempt` / `by_enter` name exactly those sequences; all
  others reuse a precomputed clean score;
* first-unit sequences are grouped by `(final cell, depletion delta, bombs consumed)` — the only
  thing the second unit can observe — keeping the best per group;
* admissible prune: `net <= pickup`, and both blocking and teammate depletion can only *lower* a
  sequence's pickup (blocking a step is equivalent to staying, which picks up nothing; a smaller
  purse stays smaller through the monotone penalty maps). So `max(teammate-free pickup)` bounds
  any reachable second-unit outcome and the group scan can stop.

Result: **~4.4 s per 500-round game including the engine**, for all six searches.

**Equivalence check.** 195 sampled rounds (every 41st round of all 15 games, 65 per map) were
re-solved with the exhaustive reference. The reference was confirmed to really enumerate 15,625
joint pairs per round. Three quantities were compared per round — the base action pair's value,
the first-step-locked optimum, and the unconstrained optimum. **195/195 rounds agreed on all
three; 0 mismatches.**

## 2. The three-way decomposition

### 2.1 Operational definitions

Let `V(S)` be the joint net value of the best action pair drawn from set `S`, under the shared
model, with the base's own `order`.

* `V_base` = value of the base's exact requested pair.
* `V_tail` = `V(each unit's first requested action pinned, steps 2-3 free)`.
* `V_best` = `V(all 5^3 x 5^3 pairs)`.

Monotone by construction: `V_base <= V_tail <= V_best`.

* **cause (3) — the chosen starting target was itself not the best** := `V_best - V_tail`.
  This is exactly the gain that *requires changing the first step*, i.e. heading for a different
  target. It is the price of the selector's "nearest gold cell with value >= 3, distance-only
  ranking" rule (`player.cpp` lines 463-479: ring-distance priority via `TT.bestrow`, no value
  term, `v > 2` threshold).
* **tail waste** := `V_tail - V_base`, split *per unit* and then bucketed. Per-unit split is
  symmetric and exactly additive: `solo_u = V(unit u's tail free, other pinned) - V_base`;
  `interaction = V_tail - V_base - solo_0 - solo_1`; unit `u` is charged
  `solo_u + interaction/2`.
* Each unit-round is labelled by the base's own requested triple:

  | label | triple shape | source in `player.cpp` |
  |---|---|---|
  | `fold0` | `(a, a^1, 4)` | `d==0` standing fold — **third step is literally STAY** (line 497: `acts[0]`,`acts[1]` written, `acts[2]` left at `STAY`) |
  | `fold1` | `(a, a^1, a)` | `d==1` pre-folded LUT: target adjacent, go-back-go |
  | `stall` | `(a, 4, 4)` | LUT waypoint check `ok` failed → single cautious `steerStep`, **two steps wasted** (lines 507-514) |
  | `stay3` | `(4,4,4)` | no passable direction at all |
  | `fold2` | `(a0, a1, a1^1)` | `d==2` arrive-then-step-back |
  | `march` | three moves, no reversal | `d==3` full travel |

* **cause (1) — target only one step away, remaining steps fall back to the fold** := tail waste
  summed over unit-rounds labelled `fold0 / fold1 / stall / stay3`. These are exactly the states
  where the base's plan committed at most one net-new cell and filled the rest with filler.
* **cause (2) — a chainable multi-gold path existed but the selector scored a single point** :=
  tail waste summed over unit-rounds labelled `fold2 / march`, where the base was genuinely
  travelling and the extra gold requires chaining an additional gold cell.

`cause1 + cause2 + cause3 = total`, exactly, by construction. Measured
`residual_vs_total = 0.0 ± 0.0` on every map and seed.

### 2.2 Results (net gold/game, mean ± SE, n=5 seeds per map)

| component | map1 | map2 | map3 |
|---|---|---|---|
| (1) short target / fold filler | **552.2 ± 28.5** (55%) | **585.1 ± 49.0** (55%) | **637.3 ± 36.1** (55%) |
| (2) chainable multi-gold path | 190.6 ± 15.6 (19%) | 241.3 ± 26.4 (23%) | 233.3 ± 31.2 (20%) |
| (3) wrong starting target | 261.0 ± 13.7 (26%) | 244.8 ± 20.5 (23%) | 287.8 ± 24.6 (25%) |
| **total** | **1003.8 ± 29.9** | **1071.2 ± 52.9** | **1158.4 ± 69.3** |
| residual | 0.0 | 0.0 | 0.0 |

The split is strikingly stable across maps: **55 / 20 / 25**.

### 2.3 Implementation ladder — what each amount of freedom is worth

| rung | freedom | map1 | map2 | map3 |
|---|---|---|---|---|
| L1 | only step 3 re-chosen | 285.0 ± 10.4 | 324.6 ± 33.4 | 316.4 ± 14.2 |
| L2 | steps 2-3 re-chosen | 742.8 ± 23.5 | 826.4 ± 59.0 | 870.6 ± 64.2 |
| L3 | all three re-chosen | 1003.8 ± 29.9 | 1071.2 ± 52.9 | 1158.4 ± 69.3 |

L1 is the cheapest conceivable organ (the `fold0` branch already leaves `acts[2]` at `STAY`), and
open-loop it looks like ~300 gold/game. §5 shows it does not survive.

## 3. Frequency and yield per occurrence

Per-unit-round, out of 1000 unit-rounds per game (500 rounds x 2 units).

### map1 (n=5 seeds)

| base pattern | unit-rounds | unit-rounds with a gain | gold/game (± SE) | gold per occurrence |
|---|---|---|---|---|
| `stall` (a,4,4) | 235.2 | 96.6 | 320.8 ± 8.7 | 3.33 |
| `fold1` (a,a^1,a) | 229.4 | 56.4 | 153.6 ± 3.6 | 2.74 |
| `fold0` (a,a^1,4) | 199.2 | 31.8 | 75.8 ± 20.5 | 2.24 |
| `fold2` | 174.8 | 40.8 | 116.3 ± 15.4 | 2.92 |
| `march` | 141.8 | 29.0 | 74.3 ± 10.2 | 2.61 |
| `stay3` | 19.6 | 1.8 | 2.0 ± 0.8 | 0.70 |
| **cause1** total | 683.4 | 186.6 | 552.2 ± 28.5 | 2.97 |
| **cause2** total | 316.6 | 69.8 | 190.6 ± 15.6 | 2.78 |

### map2 / map3 headlines

| bucket | map2 unit-rounds / gold / per-occ | map3 unit-rounds / gold / per-occ |
|---|---|---|
| `stall` | 173.6 / 328.1 ± 31.5 / 3.91 | 299.4 / 305.6 ± 24.8 / 3.06 |
| `fold1` | 251.2 / 206.1 ± 27.8 / 3.59 | 244.0 / 266.3 ± 16.5 / 4.58 |
| `fold0` | 197.4 / 50.1 ± 12.0 / 1.75 | 137.4 / 59.0 ± 10.5 / 3.13 |
| `fold2` | 167.0 / 126.0 ± 20.5 / 3.23 | 186.4 / 139.2 ± 8.0 / 3.74 |
| `march` | 209.2 / 115.3 ± 18.0 / 2.83 | 117.0 / 93.8 ± 24.8 / 4.08 |
| cause1 | 622.8 / 585.1 ± 49.0 / 3.44 | 696.4 / 637.3 ± 36.1 / 3.53 |
| cause2 | 377.2 / 241.3 ± 26.4 / 3.04 | 303.6 / 233.3 ± 31.2 / 3.86 |

Round-level availability (out of 500 rounds/game, map1 / map2 / map3):

| condition | map1 | map2 | map3 |
|---|---|---|---|
| any gain available | 272.2 ± 8.2 | 269.6 ± 4.7 | 243.4 ± 4.7 |
| tail gain available (L2) | 218.0 ± 8.4 | 213.0 ± 7.0 | 199.8 ± 4.5 |
| step-3 gain available (L1) | 96.0 ± 3.3 | 97.2 ± 3.5 | 92.8 ± 3.4 |
| cause-3 gain available | 98.0 ± 6.1 | 99.0 ± 3.6 | 81.0 ± 2.7 |

Distinct gold cells harvested per round, map1 seed 0:

```
base : {0:273, 1:187, 2:36, 3:4}
best : {0:123, 1:197, 2:124, 3:51, 4:3, 5:2}
```

So the base reaches >=2 distinct gold cells in 8% of rounds; the best visible path could in 36%.
This is the raw shape of cause (2).

**The single largest cheap-conditional candidate found:** `stall`, the `(a,4,4)` triple the LUT
waypoint check produces when any of the three pre-computed waypoints is a wall or (when rich) a
remembered bomb. It occurs on 17-30% of unit-rounds and carries 306-328 gold/game of the bound
at 3.1-3.9 gold per occurrence. It is a *narrow, frequent, high-yield* condition — exactly the
shape asked for — and it is cheap to detect because `ok` is already computed. But see §5.

## 4. The orchestrator's factor axis: hit rate vs yield

Per-unit net is additive over units, so the total bound splits **exactly** by whether the base
unit already scored that round.

* **new scoring round**: base unit pickup == 0 and the oracle path yields something → raises
  *hit rate*.
* **richer scoring round**: base unit pickup > 0 and the oracle path yields more → raises
  *yield per hit*.

| | map1 | map2 | map3 |
|---|---|---|---|
| base hit rate (unit-rounds with pickup>0) | 28.02% | 31.28% | 33.74% |
| **new scoring round, gold/game** | **782.8 ± 33.3 (78%)** | **821.8 ± 50.9 (77%)** | **840.8 ± 59.9 (73%)** |
| richer scoring round, gold/game | 221.0 ± 12.9 (22%) | 249.4 ± 23.8 (23%) | 317.6 ± 40.7 (27%) |
| sum (= total) | 1003.8 | 1071.2 | 1158.4 |
| new-scoring unit-rounds available | 719.8 ± 8.8 | 687.2 ± 10.4 | 662.6 ± 10.4 |
| of those, with a gain | 265.8 ± 10.8 | 257.4 ± 8.9 | 216.8 ± 6.1 |
| gold per occurrence (new) | 2.99 ± 0.16 | 3.19 ± 0.18 | 3.87 ± 0.23 |
| richer-scoring unit-rounds available | 280.2 ± 8.8 | 312.8 ± 10.4 | 337.4 ± 10.4 |
| of those, with a gain | 62.8 ± 3.4 | 71.4 ± 3.9 | 76.0 ± 4.9 |
| gold per occurrence (richer) | 3.51 ± 0.13 | 3.49 ± 0.32 | 4.24 ± 0.51 |

**73-78% of the bound sits on the "new scoring round" axis** — the axis the orchestrator's
platform measurement identifies as the binding constraint (our 34.8% hit rate vs opponents'
41.1%). Availability is large: 217-266 new-scoring opportunities per game out of ~660-720
zero-yield unit-rounds, i.e. the oracle can convert 31-37% of our currently-empty unit-rounds
into scoring ones. That is +21.7 to +26.6 pp of hit rate available in principle, far more than
the +6.3 pp needed.

Local hit rate is 28.0-33.7% versus the platform's 34.8%, consistent with the NPC-model bias
(over-greedy local NPCs strip more gold before we reach it) and with self-play contention.
Direction: local hit rate is **biased DOWN**, so the availability figures above are, if anything,
conservative.

## 5. Closed-loop cross-check: how much survives trajectory drift

`realized` mode substitutes the oracle's actions, so the trajectory drifts. Same map, same seed,
paired against a plain baseline run. Seeds 0,1,2 per map (n=3 games per cell, 9 per rung).

| rung | map1 | map2 | map3 | pooled (n=9) |
|---|---|---|---|---|
| L1 — step 3 free only | **+57.7 ± 35.1** | **-156.3 ± 55.4** | **-95.3 ± 210.8** | **-64.7 ± 73.6** |
| L2 — steps 2-3 free | -690.7 ± 278.4 | -860.0 ± 143.4 | -818.3 ± 97.3 | -789.7 ± 109.3 |
| L3 — all three free | -864.7 ± 230.3 | -894.3 ± 126.5 | -738.3 ± 71.7 | -832.4 ± 90.8 |

**Zero percent of the open-loop bound survives; the realized effect is strongly negative.** L3
drops seat-1 income from ~1257-1415 to ~392-635. Reason: the frozen build's income is not
generated by any single round's harvest, it is generated by *where the units stand*. The
distance-only nearest-gold rule plus the central anchors `(6,8)/(11,8)` keeps both units parked
in the gold-generation peak, where fresh gold appears next to them every round. A per-round
myopic path optimum walks off that peak to collect 35%-residual scraps and never comes back. The
open-loop bound measures a *free lunch that is only free because the base paid for the position*.

L1 is the honest test of a genuinely cheap organ, and it is `-64.7 ± 73.6` pooled — indistinguishable
from zero at best, negative on two of three maps. Even re-choosing only the third step is enough
to walk a unit off its gold cell and forfeit next round's fold.

Caveat, stated in the base's favour: L1/L2/L3 are all driven by a *purely myopic* objective. A
hand-built organ with a positional guard (e.g. "only extend the fold if the extra cell is
adjacent to the current cell, so the unit ends where it started") is not what was measured and
could behave differently. What §5 does establish is that greedy three-step path value is the
wrong objective function, so any organ built from this bound must carry a positional constraint
that this measurement did not model.

## 6. Why the tripwire fired, and the resolution

The bound exceeds 800 gold/game on all three maps. Checks performed:

1. **Is the mechanics model wrong?** No. It reproduces the engine's per-round pickup *and* burn
   exactly in 500/500 rounds (§1.5). One real defect was found and fixed by this check (stale
   bomb memory, worth ~440 gold/game of spurious bound).
2. **Is the fast search wrong?** No. 195/195 sampled rounds match the exhaustive 15,625-pair
   enumeration on all three quantities (§1.6).
3. **Is the oracle cheating on information?** No. It reads the engine's own fog-filtered array;
   fogged values are the `-5` sentinel, and an assertion confirms every gold value used lies
   inside the seat's radius-2 visibility union (§1.3).
4. **Is the quantity itself loose?** Yes, and this is the answer. **Gold is a stock, not a flow.**
   A per-round counterfactual credits the oracle for a cell in round *r* while the base's own
   trajectory harvests that same cell in round *r+k*; the base's realized income already contains
   it. Summing 500 such rounds double-counts massively.

The `stock_flow` diagnostic quantifies (4) directly. For every round it computes the per-cell
extra gold the oracle would take, then asks whether the base's *own realized trajectory* re-enters
that cell at any later round of the same game:

| | map1 | map2 | map3 | pooled |
|---|---|---|---|---|
| novel gold (base never returns) | 119.0 ± 18.6 | 144.8 ± 16.7 | 78.0 ± 8.5 | 113.9 ± 8.8 |
| timing gold (base returns later) | 836.6 ± 43.8 | 859.8 ± 50.1 | 865.2 ± 56.3 | 853.9 ± 29.0 |
| novel share | 12.7% | 14.6% | 8.3% | 11.8 ± 1.0% |

**88% of the raw bound is timing, not gold.** Uncontested — which is the setting in which the
294-gold T-1 gap was measured — timing is worth close to nothing: a cell we skip keeps 35% of its
value *and* keeps receiving the scenario generator's additions, so the gold is not lost, it can
grow. So the tripwire's implied contradiction dissolves: the bound does **not** say T-1 leaves
~500 gold unclaimed. It says a per-round three-step path optimum is not a realizable income
channel for anybody, us or T-1.

`novel_gold` is itself still an over-estimate of realizable value (it ignores that the oracle's
detour costs position, which §5 prices at hundreds of gold) and a slight under-estimate in one
respect (a returning base takes only 65% of the then-current value, not all of it). The honest
range for "realizable by any three-step path organ" is therefore **between the closed-loop L1
figure of -65 ± 74 and the novel-gold figure of 114 ± 9 gold/game.**

## 7. Pricing consequence

Against the cost model in `src/INFRA.md` (combined unit price 1.6 gold per instruction, so the
294-gold T-1 gap buys at most ~183 instructions at 100% realization):

* the raw 1078-gold bound would nominally buy ~670 instructions, but at the measured realization
  it buys **nothing**;
* the tightest defensible realizable ceiling, 114 gold/game, buys **~71 instructions** — and only
  if an organ could capture 100% of novel gold with zero positional damage, which §5 shows is the
  hard part, not the search;
* the closed-loop L1 result (-65 ± 74) says the cheapest myopic version is already net-negative,
  so **any** implementation must add a positional guard, i.e. it cannot be a pure "score the path"
  organ.

The most promising narrow condition, if one is pursued anyway, is the `stall` triple `(a,4,4)`:
17-30% of unit-rounds, 306-328 gold/game of the bound, 3.1-3.9 gold per occurrence, and it is
already detectable for free because `player.cpp` line 506 computes the `ok` flag that produces it.
The second is `fold0`'s literally-wasted third step (137-199 unit-rounds/game). Both sit on the
**new scoring round** axis, which is the axis where the platform data says we are behind. But both
must be implemented as *position-preserving* extensions (end the round on the same cell), because
the unconstrained versions measured here destroy hundreds of gold.

## 8. Bias register

| number | direction | reason |
|---|---|---|
| gross bound (net and pickup) | **biased UP, dominant** | per-round stock/flow double-count, 88% of the total |
| gross bound | biased UP | oracle inherits the base's positioning for free |
| gross bound | biased DOWN, small | fogged gold scores as zero; an invisible enemy could block a planned step |
| decomposition shares (55/20/25) | unbiased *given* the bound | exact algebraic split, residual 0.0 |
| factor axis (new vs richer) | unbiased *given* the bound | exact per-unit additive split |
| availability / frequency counts | biased DOWN | local hit rate 28-34% vs platform 34.8%, over-greedy NPCs strip gold first |
| closed-loop deltas | trustworthy in sign; magnitude biased toward **more negative** | central-efficiency income is systematically under-estimated locally |
| local absolute income (1122-1725 net) | **not a ceiling** | self-play against a full-strength copy of ourselves, not the passive `probeobs` probe; the platform's uncontested figure is 2182.4 |
| all deltas | paired, same seed, same scenario digest | `dispatch="fixed"`, no clock read, deterministic |

## 9. Reproduce

```bash
clang++ -O2 -std=c++17 -shared -fPIC -include /tmp/gr_path/shim.h \
        -o /tmp/gr_path/base.so src/player.cpp

python3 -m sim.analyze_path_oracle bound \
        --map map1 --map map2 --map map3 \
        --base-so /tmp/gr_path/base.so --seeds 0 1 2 3 4 \
        --sample-every 41 --out /tmp/gr_path/bound_raw.json

python3 -m sim.analyze_path_oracle verify --samples /tmp/gr_path/bound_raw.json

for m in map1 map2 map3; do for L in 1 2 3; do
  python3 -m sim.analyze_path_oracle realized --map $m \
          --base-so /tmp/gr_path/base.so --seeds 0 1 2 --level $L
done; done
```

Wall clock: 15 measurement games + 15 baseline games at ~4.4 s and ~4.7 s respectively (~2.5 min),
195-round equivalence check 4.5 s, 27 closed-loop games ~2 min.

## 10. Sample sizes

| measurement | maps | seeds/map | games | unit-rounds |
|---|---|---|---|---|
| open-loop bound | 3 | 5 (0,1,2,3,4) | 15 measured + 15 baseline | 15,000 |
| equivalence check | 3 | 5 | 195 sampled rounds | — |
| closed loop, each of 3 rungs | 3 | 3 (0,1,2) | 9 oracle + 9 baseline per rung | — |
| round-level fidelity | map1 | 0 | 1 | 500 rounds |
