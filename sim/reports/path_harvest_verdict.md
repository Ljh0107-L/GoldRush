# Path harvesting: upper bound, verdict, and why it is not the lever

> Round of 2026-08-09 late. **Zero platform games consumed.** `src/player.cpp` byte-identical to
> `f18064c` throughout (`shasum -a 256` = `0ecce6fc…84fdd` = `git show f18064c:src/player.cpp`).
> No behaviour was implemented; this is measurement and judgment only.

## 0. Answer first

**此路不通 — path harvesting is not the lever.** The hypothesis is *right about mechanism and wrong
about consequence*, and the implementation is *affordable but income-negative*.

| judgment | answer |
|---|---|
| 1. do the two routes agree | **yes**, on every overlapping quantity; one hypothesis-relevant nuance is refined, none contradicted |
| 2. gross upper bound | raw open-loop **1003.8 / 1071.2 / 1158.4** gold/game (map1/2/3); tripwire fired; honest ceiling after removing stock double-count **119.0 / 144.8 / 78.0** |
| 3. instruction budget | Master's 183-instruction figure is arithmetically right but rests on a wrong premise; the true budget is **49–91 instructions**, and the realizable budget is **≤ 0** |
| 4. decomposition → route | 55% short-target filler, 25% wrong start target, 20% chainable; cheap version ≈ 40–90 instr, full version ≈ 400–800 instr |
| 5. joint feasibility with IPC golf | **cost side feasible, income side negative → do not do it.** Bank the golf as pure latency instead (**120–200 gold**, not 144–256) |
| 6. honest conclusion | the bound does not survive contact with the closed loop; the real deficit is hit rate, and it is positional, not path-shaped |

## 1. What was measured, and how the two routes divide

Two independent routes were run against the same question, plus an orchestrator-side channel that
turned out to be the decisive one.

| route | artifact | what it can and cannot see |
|---|---|---|
| 1. opponent logs | `sim/analyze_opponent_paths.py`, `sim/reports/path_harvest_opponent.{json,md}` | 270 games / 133,093 rounds. Trajectories only where fog permits (~34–47% of opponent unit-rounds) |
| 2. our oracle | `sim/analyze_path_oracle.py`, `sim/reports/path_harvest_oracle.{json,md}` | simulator, 5×5-visible information only, open-loop counterfactual + closed-loop check |
| 0. fog-free channel | `sim/analyze_gold_delta.py`, `sim/reports/gold_delta_channel.json` | per-unit held gold is logged in **100%** of unit-observations; unbiased and complete |

### 1.1 The fog-free channel, and why it outranks the trajectory channel

Per-unit `gold` is recorded whether or not the unit is visible (2000/2000 unit-observations in a
sampled game), whereas `position` is present in 32.7%, a length-3 `actions` list in 34.0% and
`pickup` in 38.6%. Differencing end-phase gold therefore yields an unbiased per-unit income series
for both players.

It is not merely unbiased, it is **complete**: the mean per-unit delta gap, scaled by 2 units × 500
rounds, reproduces the observed head-to-head net score difference on all six battlefields.

| battlefield | ours | theirs | implied from channel | observed | residual |
|---|---:|---:|---:|---:|---:|
| T-1 map1 | +1.564 | +1.839 | −274.9 | −274.3 | −0.55 |
| T-1 map2 | +1.921 | +2.024 | −103.9 | −106.7 | +2.79 |
| T-1 map3 | +0.951 | +1.052 | −101.2 | −104.0 | +2.80 |
| Tundra map1 | +1.606 | +1.825 | −219.6 | −219.2 | −0.44 |
| Tundra map2 | +1.892 | +1.839 | +53.3 | +50.2 | +3.11 |
| Tundra map3 | +1.010 | +0.761 | +249.3 | +245.8 | +3.50 |

Max residual 3.5 gold on a ±250 gold quantity. **Any trajectory-derived claim that conflicts with
this channel loses.**

### 1.2 The frozen build's own platform games were located

Our side of an archived game is not one strategy: the 112 manifest games span **102 distinct
builds**, several deliberately crippled. Aggregating them produces a meaningless baseline — an
error that was live in the briefing.

`f18064c`'s own games were identified by build-name family and *proven*, not assumed:

| family | opponent | map | games | wins | measured mean Δ | reference | source of reference |
|---|---|---|---:|---:|---:|---:|---|
| `frTu1` | Tundra | map1 | 6 | 1 | −219.17 | −219.2 | `src/CHANGELOG.md` |
| `frTu2` | Tundra | map2 | 6 | 4 | +50.17 | +50.2 | `src/CHANGELOG.md` |
| `frTu3` | Tundra | map3 | 6 | 5 | +245.83 | +245.8 | `src/CHANGELOG.md` |
| `t1f1` | T-1 | map1 | 6 | 1 | −274.33 | −274.3 | Master session log `ac33eaaa-180` |
| `t1f2` | T-1 | map2 | 6 | 1 | −106.67 | −106.7 | Master session log `ac33eaaa-180` |

> **map2 sample correction (Master, 8.10).** The n=6 figure above is what I could *identify*; the
> better estimate is the n=12 top-up: **2 wins / 10 losses, −164.6 ± 61.6 SE (2.67σ, stable loss)**,
> also from session log `ac33eaaa-180` and likewise never recorded in the repo until now. map3's
> n=12 top-up is −102.9 ± 63.5 (1.62σ, undecidable), consistent with the identified −104.0.
> **Use −164.6 (n=12) as the primary figure and −106.67 (n=6, identified) as the secondary.** The
> consequence is material: at −165 rather than −107, map2 moves from "the golf budget flips it" to
> "marginal". Archiving lesson: platform games whose results never reach the repo are games not run.
| `t1f3` | T-1 | map3 | 6 | 1 | −104.00 | −104.0 | Master session log `ac33eaaa-180` |

Provenance caveat per 军规 27: the three `frTu` anchors are repo-verifiable; the three `t1f`
anchors exist only in the Master's session log, not in any repo file. Route 1 independently
refused to call `t1f` validated for exactly that reason. Three independent maps agreeing to
within 0.04 gold with matching win counts is nonetheless conclusive identification.

Also identified in passing: `t1x1/2/3` = predecessor `0c2e101` (−643.2 / −187.5 / −157.5),
`mxT13`/`mxTu1` = the reverted global-amount-priority candidate (−376.8 / −304.7).

Data hygiene: 7 of 112 archived games are **forfeits** ending in a `{"round","forfeit"}` row with
57–426 rounds; such a row must break the difference chain, not merely be skipped.

## 2. The falsification test — mechanism confirmed, consequence refuted

A single step onto a cell of value `v` pays `ceil(0.65v)`, capped at 7 for an ordinary `v ≤ 10`, so
a per-unit gain of ≥8 needs one of exactly three things. Reconstruction separates them
(clean subset = length-3 actions + all path cells non-fog + reconstructed pickup == logged pickup):

| entity | clean n | one fat cell (`v≥11`), one bite | **one cell, bitten twice** | chained 2 cells | chained 3 | chained total |
|---|---:|---:|---:|---:|---:|---:|
| T-1 | 1876 | 49.7% | **0.0%** | 44.9% | 5.4% | **50.3%** |
| Tundra | 1393 | 51.0% | **0.0%** | 44.4% | 4.6% | **49.0%** |
| **ours `f18064c`** | 2321 | 26.9% | **61.1%** | 11.7% | 0.2% | **11.9%** |

Independently reproduced by the orchestrator from raw logs: 61.1% / 26.9% / 11.7% / 0.2% on
n=2324 — an exact match. `one ordinary cell, one bite` is 0 for every entity, which is a free
consistency check on the `ceil(0.65v)` bound. Stratum agreement is within 3pp, and the *less*
fog-biased stratum shows slightly *more* chaining, so ~50% is a conservative estimate.

**Mechanism: confirmed.** They chain ~50% of their bursts; we chain ~12%. A 4× difference.

**Consequence: refuted.** On the same 36 games, on the unbiased channel:

- our ≥8 rate is **6.47%** vs T-1 6.20% and Tundra 5.12% — we reach big rounds *more* often;
- our gold per scoring round is **4.67** vs 4.19 — we extract *more* per trip;
- our yield-per-hit exceeds theirs in **6 of 6** battlefields, and our ≥8 rate in **5 of 6**;
- and we still lose.

The entire deficit is **hit rate: 34.8% vs 41.1%**. We win exactly the one battlefield
(Tundra map3) where our hit rate is the higher one — 24.7% vs 20.0%.

So the briefing's framing — "they collect paths, we collect points, therefore chain more" — is
backwards as a diagnosis. We already reach ≥8 more often than they do; we just do it by biting one
cell twice, which spends two of three steps to extract ≤90% of a single cell where a chain takes
65% of two. Chaining is the better *mechanism*, but the metric it improves (gold per scoring round)
is the one where we are **already ahead of both opponents**.

Corollary that matters more than the hypothesis: the published burst-rate comparison
(ours 15.2% vs 32.5%/34.4%, `sim/OPPONENTS.md:467-469`) is against the ~100-build archive mixture,
**not** against `f18064c`. Measured properly on the frozen build the burst gap largely disappears.
That comparison should not be used to price strategy work again.

## 3. The upper bound, and why the raw number is a mirage

Open-loop counterfactual, same-seed paired, oracle restricted to the 5×5 union, n=5 seeds/map:

| | map1 | map2 | map3 |
|---|---:|---:|---:|
| raw gross bound (net gold/game) | 1003.8 ± 29.9 | 1071.2 ± 52.9 | 1158.4 ± 69.3 |
| of which **novel** (cell the base never re-enters) | **119.0** | **144.8** | **78.0** |
| of which timing double-count | 836.6 | 859.8 | 865.2 |

The >800 tripwire fired on all three maps. It was **not** a modelling defect — the model
reproduces the engine's per-round pickup *and* burn in 500/500 rounds (1254/1254 and 132/132), and
the fast path matches exhaustive 15,625-pair enumeration on 195/195 sampled rounds with 0
mismatches. Route 2's fidelity check did find and kill a real inherited defect (stale bomb memory
inflating modelled burn, worth ~440 spurious gold/game), which is precisely why the check was worth
its runtime.

The resolution is conceptual: **gold is a stock, not a flow.** Crediting the oracle for a cell in
round *r* when the base's own trajectory harvests that same cell in round *r+k* double-counts. 85–92%
of the raw bound is exactly that. A cell we skip keeps 35% of its value *and* keeps receiving
additions, so under no contention the skipped gold is very largely collected later anyway.

This also answers the briefing's suspicion directly: the bound does **not** imply T-1 leaves ~500
gold unclaimed. It implies a per-round three-step path optimum is not a realizable income channel
for anyone.

### 3.1 Closed loop: none of it survives

| rung | meaning | map1 | map2 | map3 | pooled (n=9) |
|---|---|---:|---:|---:|---:|
| L1 | step 3 free only | +57.7 ± 35.1 | −156.3 ± 55.4 | −95.3 ± 210.8 | **−64.7** |
| L2 | steps 2–3 free | −690.7 ± 278.4 | −860.0 ± 143.4 | −818.3 ± 97.3 | **−789.7** |
| L3 | all three steps free | −864.7 ± 230.3 | −894.3 ± 126.5 | −738.3 ± 71.7 | **−832.4** |

0% of the open-loop bound survives trajectory drift. Even the cheapest rung is not established as
positive anywhere: +1.64σ on map1, a significant **−2.82σ on map2**, noise on map3, negative pooled.

The mechanism is now legible, and it is the same fact from three directions: **our income is
positional.** It comes from standing inside the central generation peak — which is why the anchor
scans kept `(6,8)/(11,8)` champion across V1–V5, W1–W3 and a 30-game pre-registered A/B. A myopic
path optimizer walks off the peak to collect a cell that would have kept 65% of its value anyway,
and pays for it on every subsequent round. The deeper the rung, the worse the loss, exactly as a
positional-income model predicts.

That also retro-explains the fold. The fold is not waste; it is a cheap way to stay on the peak
while extracting a little more. Consistent with the subsystem audit, where removing it moved net by
only +36/+5/+4 gold/game, all <2SE.

## 4. Decomposition and implementation pricing

Three-way split of the open-loop bound (residual exactly 0.0 on every map/seed; components are
Shapley-split per unit so they are additive by construction):

| cause | map1 | map2 | map3 | share |
|---|---:|---:|---:|---:|
| 1. short target, remaining steps used as filler | 552.2 | 585.1 | 637.3 | **55%** |
| 2. chainable multi-gold path existed, selector scored one point | 190.6 | 241.3 | 233.3 | 20% |
| 3. chosen start target itself not best | 261.0 | 244.8 | 287.8 | 25% |

Cause 1 dominating at 55% corroborates the trajectory channel from the opposite direction: both
opponents produce **zero** direction reversals and **zero** within-round revisits (0/44,318 and
0/33,438 clean unit-rounds; distinct cells always equal moved steps, path efficiency exactly 1.000),
while `f18064c` reverses in 56.5% of rounds and folds 76.1% of its 3-move rounds onto only two
distinct cells, so 16.6% of our moved steps land on a cell we already drained this round. Our side
of that comparison is essentially unbiased — own units are always visible, 35,386 of 36,000 clean.

Split by which factor the bound moves — the decision-relevant axis:

| | map1 | map2 | map3 |
|---|---:|---:|---:|
| new scoring round (raises **hit rate**) | 782.8 (78%) | 821.8 (77%) | 840.8 (73%) |
| richer scoring round (raises yield-per-hit) | 221.0 (22%) | 249.4 (23%) | 317.6 (27%) |

73–78% of the bound sits on the hit-rate axis, which is the axis where we are genuinely behind. In
principle 217–266 of our ~660–720 zero-yield unit-rounds/game are convertible, i.e. +21.7 to +26.6pp
of hit rate against the +6.3pp actually needed. **That is why the open-loop number is so seductive,
and why the closed-loop check was mandatory.**

Instruction cost estimates (orchestrator's, from `INFRA.md §2` unit prices; labelled estimate, not
measurement):

| version | what it does | estimated instructions | absorbable by IPC golf's 90–160? |
|---|---|---:|---|
| cheap (≈ L1) | when the target is near, re-choose only the trailing step(s) | **40–90** | **yes** |
| full (≈ L3) | true three-step path enumeration | **400–800** | no |

The cheap version is not free of the scan's shape: the selector's `goldm` is a threshold mask, not
per-cell values, so choosing a trailing step by *amount* needs value re-extraction — which is what
pushes the estimate toward the upper end.

## 5. Joint feasibility with IPC golf — the priority question

IPC golf can honestly free **90–160 instructions** (`INFRA.md §2.4`: scan 60–100, route+blk 20–40,
target 10–20 = 13.1–23.3 ns = 144–256 gold).

- **Cost side: feasible.** A 40–90 instruction cheap path-harvest fits inside 90–160, so net
  instruction change ≤ 0 is achievable and the first-mover crossover would not be crossed.
- **Income side: negative.** The organ that would occupy that budget is L1, measured at
  −64.7 ± 73.6 gold/game pooled and −156.3 ± 55.4 on map2.

Zero latency cost multiplied by negative income is still negative. **The combination is therefore
rejected, on income rather than on cost.** The correct use of the golf budget is to bank all of it
as pure latency. **But discount the top of the range** (Master, 8.10): 11 gold/ns holds only inside
the ±20ns crossover band, and 13.1–23.3ns pushes us from parity to the band edge, where the rate
falls — exactly the `INFRA §2.6` ChV lesson. Linear extrapolation gives 144–256 gold; the honest
figure is **120–200 gold/game**, and that is what should be quoted.

Against the frozen build's own battlefields that flips T-1 map3 (−102.9 ± 63.5, n=12) comfortably,
leaves T-1 map2 **marginal** (−164.6 ± 61.6, n=12 — not the −106.7 I first quoted), and flips
neither map1 (−274.3 T-1 / −219.2 Tundra). So golf alone secures at most one battlefield and
contests a second.

## 6. Corrections to the numbers used to commission this round (军规 27)

Verified correct at source: 894.216 instructions/call and 0.1454 ns/instruction (`INFRA.md §1`);
1 ns ≈ 11 gold in the crossover band (`§2.5`); 1.6 gold/instruction (0.1454 × 11 = 1.5994);
`f18064c` probeobs 2182.4 (`CHANGELOG` lines 11/35/177); IPC golf 90–160 with the scan/target/route
split (`§2.4`); T-1 ≈963 and Tundra ≈997–1376 I10-equivalent (`OPPONENTS.md:408-416`);
pickup `(65v+99)//100`, blocked-step continuation and pickup-before-trample (`engine.py:1050`,
`:1359`, `:1416-1430`). Opponent ceilings recomputed from `logs/opponents/manifest.json`:
T-1 **2476.4**, Tundra **2654.8**, n=5 each, all on one map (row fingerprint `4d6ac13d`) — so the
294.0 / 472.4 gaps are same-map and sound.

| # | claim as commissioned | measured | verdict |
|---|---|---|---|
| 1 | 54 T-1 / 40 Tundra opponent logs | 69 / 43 archived; 522 raw logs, of which 158 more vs the tracked opponents | **stale** |
| 2 | fold "只值约 1.5 金/轮" | 1.30 gold per *occurrence* unconditionally, 1.625 given `v≥3` | **right per occurrence, wrong if read per game-round** — that reading implies 750 gold/game vs the audited ±36 |
| 3 | "our 15.2% burst vs their 32.5/34.4% ⇒ they chain more" | those rates compare the ~100-build archive mixture to the opponents; on the frozen build our ≥8 rate 6.47% **exceeds** theirs 5.67% | **misleading baseline** |
| 4 | "294 gold ⇒ at most 183 instructions" | arithmetic correct, premise wrong: 294 is the *uncontested* gap, the novel-gold ceiling is 78–145 ⇒ 49–91 instructions, and realizable is ≤0 | **premise** |
| 5 | (orchestrator's own error) "union `start[r+1].actions` with `end[r].actions` to recover coverage" | recovers exactly **0** extra unit-observations; `end[r-1]` is never poorer than `start[r]` | **wrong, retracted** |

Two further schema facts worth keeping: logged `pickup` is fog-**truncated**, not merely sparse, so
`pickup >= delta_held` must be required before trusting it; and `start.grid`'s fog mask is exactly
the radius-2 union of our own start-of-round positions (0 mismatches in 46,240 cell-observations).

## 7. What is still open

- **Attribution of the hit-rate gap is NOT resolved.** Matched on the 18 f18064c-vs-Tundra games,
  conversion given visible supply is indistinguishable (supply=2: Tundra 58.8% vs ours 57.7%;
  supply≥3: 70.6% vs 71.9%), which points at supply outside the visible diamond, i.e. positioning.
  But the strata disagree: mirrored to the same observability condition Tundra converts ~7pp
  *better* while T-1 converts ~11pp *worse*, and the high-vision stratum is confounded because
  those are probe games where the opponent faced no contention. The trajectory channel is too
  fog-biased to settle this; the honest statement is that the gap is unbiasedly measured but its
  cause is not yet isolated.
- The one structural asymmetry that *is* unbiased and unexplained by anything we have priced:
  opponents never reverse or revisit, in 77,756 clean unit-rounds combined. Whatever produces their
  hit rate, it is compatible with strictly monotone three-step motion.
- `L1` on map1 alone (+57.7 ± 35.1) is the only non-negative cell in the closed-loop table. It is
  1.64σ on n=3 and significantly negative on map2, so it is not a candidate — but if the hit-rate
  question is ever reopened, map1 is where to look, because map1 is also the battlefield the golf
  budget cannot flip.

## 8. Reproduction

```sh
python3 sim/analyze_gold_delta.py validate      # prove the f18064c family identification
python3 sim/analyze_gold_delta.py frozen        # fog-free per-battlefield channel
python3 sim/analyze_opponent_paths.py run       # opponent trajectory channel (~16 s)
python3 sim/analyze_path_oracle.py --help       # oracle bound; see path_harvest_oracle.md
```

Zero platform games were consumed. `src/player.cpp` unchanged.
