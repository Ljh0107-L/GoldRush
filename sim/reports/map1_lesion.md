# map1's real lesion: collection quality and order-sensitivity (title corrected — see erratum)

> ## 🔴 ERRATUM (orchestrator, same day, after 6 confirmatory platform games)
>
> **The headline causal claim of this report is WITHDRAWN. Every measurement in it is correct and
> reproduces exactly; the *interpretation* of `A` is not.**
>
> `A` is defined as `(ours − theirs)` on rounds **we** move first — but on those rounds **they are
> moving second**. So `A > 0` compares our first-mover income against their *second*-mover income:
> it is largely the value of the first-mover slot itself, **not** a measure of collection skill. The
> claim "`A > 0` … we do not collect badly, we just don't move first enough" **does not follow**.
>
> At *matched* action order, over the same 30 games / 14,970 rounds, they beat us in **both**
> conditions:
>
> | condition | ours | theirs | gap |
> |---|---:|---:|---:|
> | both moving **first** | 4.0793/round | **4.6740** | theirs **+14.6%** |
> | both moving **second** | 1.7128/round | **2.8336** | theirs **+65.4%** |
>
> The correct decomposition of the −286.1 is therefore:
>
> | term | gold/game |
> |---|---:|
> | pure **collection** deficit, action order held fixed | **−411.1** |
> | our **action-order advantage** (`f` = 0.568 > 0.5) | **+124.4** |
> | sum | −286.7 (observed −286.1) |
>
> **The conclusion inverts: our first-mover rate 56.8% is higher than the opponents' 43.2%, so the
> race is a net *advantage* to us, and we lose anyway. The lesion is collection quality, partially
> masked by that advantage.** `f* = 0.7036` remains arithmetically exact but frames a collection
> problem as a race problem; its true meaning is "at this collection level we would need to win 70%
> of races to break even".
>
> The sharper diagnosis the same data supports — **we are abnormally order-sensitive**:
>
> | entity | first-mover income | second-mover income | ratio | loss when second |
> |---|---:|---:|---:|---:|
> | **ours** | 4.0793 | **1.7128** | **2.38×** | **−58.0%** |
> | T-1 / Tundra | 4.6740 | 2.8336 | 1.65× | −39.4% |
> | Ausdroid | 4.5556 (n=9, unreliable) | 3.3166 | 1.37× | −27.2% |
>
> We are ~1.44× more order-sensitive than the two strong opponents and the most fragile second mover
> of the three. This is consistent with positional income: we camp the central generation peak, so an
> opponent moving first strips the peak cells before we arrive, whereas their chained motion depends
> less on holding one spot. **The implied repair is target selection — avoid cells an opponent moving
> first can take — not latency.**
>
> **Survives unchanged:** the localization (deficit accrues after round ~120), Lead A discarded, Lead
> B discarded as supply, the stock/flow scope correction, the +40 ns fallback dose-response, the
> 9.4–12.0 gold/ns dispatch transfer function, and the C1/C2 *gold values* (the measured marginal
> value of flipping one round, 3.289 gold, stands). **Withdrawn:** "the lesion is the dispatch race",
> and with it C1/C2's status — they are **compensation, not cure**, and buy ≈0 against the field,
> where our `f` is already 0.997.
>
> Confirmed on 6 fresh platform games vs Ausdroid (`adf1a`..`adf1f`, ids 185257/185259/185262/
> 185264/185266/185267, `f18064c` SHA256 `e88e5e80…395695dbad`, FP16=0): `f` = 0.9970, net
> **+85.8 ± 64.5, 4W/2L** (1.33σ, undecidable). Also established there: the "1W-14L vs Ausdroid"
> record is a **build-mixture artifact** — 14 old builds average −473.8 (1/13) while `f18064c`
> averages +85.8 (4/2), a swing of **+559.6 gold/game**.
>
> Process note: the identity's arithmetic was verified (closure, `f`, `A`, `B`, all 7 holdout splits)
> but its **semantics** were not — the exact failure this report itself warns about when it says an
> accounting identity is algebra, not causality. The JSON already contained `our_income_when_first`
> and `their_income_when_they_first`; subtracting them exposes it.
>
> ---

> Round of 2026-08-10. **Zero platform games consumed** — this reads archived logs only.
> Baseline pinned to **`f18064c`** (`git show f18064c:src/player.cpp`, `shasum -a 256` =
> `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`, verified this round).
> HEAD has since moved to **`fd47ea6`**; that fix is bit-identical on the three known maps
> (`pair_diff` 0/500), so everything here carries, but nothing here was measured on HEAD.
> Nothing under `src/` was modified. Driver `sim/analyze_map1_lesion.py`; machine-readable
> companion `sim/reports/map1_lesion.json`.
>
> Follow-on to `sim/reports/map1_wall_repricing.md` (committed as `bf1186e`), which falsified the
> routing hypothesis. This report finds what map1's deficit actually is.

---

## 0. Answer first

**The map1 deficit is not in the opening, is not uniform, and is not supply: it accrues entirely
after round ~120 and it localizes, exactly and with zero residual, to the dispatch race — map1's
break-even first-mover rate is 0.704 and we achieve 0.567, while map2 is 0.694 vs 0.683 (tie) and
map3 is 0.568 vs 0.624 (win). One number orders all three maps. The reason our map1 rate is low is
that map1's 40 walls put us on the `ok==0` cold fallback branch in 53.5% of rounds at a measured
+40 ns per unit, which fattens our cost tail (P75 250 ns vs the opponents' 230) and loses us the
race. Lead A (the opening) is discarded; Lead B is discarded as a *supply* mechanism and vindicated
as a *latency* mechanism.**

| question | answer |
|---|---|
| where does the deficit accrue | **after round ~120.** Cumulative deficit at round 24 is **−19.1** and at round 99 **−4.6** — negative, i.e. we are *ahead*. It crosses zero at ~round 120 and then accrues at a near-constant +30 gold per 50 rounds to +286 |
| u0 vs u1 | both units, 39/61 split (charge u0 112.9 ± 27.4, u1 173.2 ± 29.2). The **largest** u0/u1 asymmetry is on map3, the map we win. Does not localize |
| spatial | region-1 occupancy 90-98% and mean anchor distance 1.7-2.8 on **all three maps**. No map1 anomaly |
| what does localize | the move-order accounting identity `net = 500·[f·A + (1−f)·B]`, which closes with residual **0.00** on map1 |
| the lesion, in one line | **f = 0.567 against a break-even f\* = 0.704, a −13.6 pp margin worth 20.98 gold/game per pp** |
| why f is low | our cost P50 is 10 ns *faster* than theirs but our P75 is 20 ns and P90 20 ns *slower*. The fat upper half is a clean dose-response on the fallback branch: 0/1/2 fallback units → **180/220/260 ns** P50, i.e. **+40 ns per unit**, on 53.5% of rounds. Plus **+70 ns** every 20th round from `waveTick` |
| stock/flow | race-lost gold is **100% novel**: 5 rounds after losing a race the cumulative gap is 135% of the round-0 gap, i.e. **nothing is recovered**. The board is 98.8% harvested, so a cell we lose is taken by someone else, not by us later |
| priced candidate | make the fallback branchless/warm: +16.16 pp → **+266 gold/game (RD)**; with a realistic +10 ns uniform penalty **+164 gold/game**; `waveTick` amortisation **+32 gold/game** at ~zero cost |
| generality | **the latency lever is worth ~0 against ~95% of the field.** Against 10 of the 12 archived mid-field opponents our first-mover rate is already 0.97-1.00 because they run at 450-46,930 ns. It matters against T-1, Tundra and a handful of others |
| Lead A | **discarded** |
| Lead B | **discarded as supply, vindicated as latency** |

---

## 1. Method and corpus, before any hypothesis

### 1.1 Why this channel

Per-unit `gold` is logged in 100% of unit-observations regardless of fog, verified again here:
in the map1 corpus the opponent's `position` is `null` while the opponent's `gold` is always
present. Three further fields are fog-free and are used:

| field | availability | use |
|---|---|---|
| `end.players[].units[].gold` | both sides, 100% | per-unit income by differencing |
| `end.players[].cost` | both sides, 100% | exact per-round first-mover, by the engine's own rule |
| `end.players[].units[].position` | **our** units, 100% | fog-free spatial series for our side |
| `snapshot.regions[]` | global, every 5th round | fog-free per-region generation / collection / remaining |

The engine's dispatch rule is sourced, not assumed: lower cost moves first and P1 wins an exact tie
(`docs/PRELIM_RULES.md` §2.4, quoting `赛制:91` and `FAQ:308`).

### 1.2 Corpus

| map | games | families | walls | net delta (mean ± SE) |
|---|---:|---|---:|---:|
| **map1** | **30** | `frTu1`, `lnA0`, `a2A0`, `alA0` (Tundra) + `t1f1` (T-1) | 40 | **−286.10 ± 51.69 (−5.53σ)** |
| map2 | 12 | `frTu2`, `t1f2` | 24 | −28.25 ± 70.58 (−0.40σ) |
| map3 | 12 | `frTu3`, `t1f3` | 78 | +70.92 ± 83.47 (+0.85σ) |

The four Tundra map1 arms come from the sibling backfill (`sim/reports/archive_backfill.json`); the
map identity of every game is re-verified here from log row 2 (`read_game` raises if a family's wall
count does not match its expected map), and all 54 games run the full 500 rounds with no forfeits.

---

## 2. Localization — produced before any cause was proposed

### 2.1 The cumulative deficit curve

`deficit = theirs − ours`, so a negative cumulative value means we are ahead.

| round | 24 | 49 | 99 | 149 | 199 | 299 | 399 | 499 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **map1 cumulative deficit** | **−19.1** | **−19.8** | **−4.6** | +26.1 | +77.9 | +146.8 | +213.8 | **+286.1** |
| share of final | −6.7% | −6.9% | −1.6% | 9.1% | 27.2% | 51.3% | 74.7% | 100% |

**We win the first ~120 rounds of map1 and lose the remaining 380.** The deficit is therefore
neither front-loaded nor uniform: 0% of it exists at round 100, and from there it accrues at a
near-constant rate.

### 2.2 The regime change, and what moves

50-round blocks (map1). `our_hit` / `their_hit` are per-unit-round scoring rates.

| block | deficit ± SE | our hit | their hit | our yield/hit | their yield/hit | our first-mover rate |
|---|---:|---:|---:|---:|---:|---:|
| 0-50 | **−19.80 ± 10.52** | **0.3830** | **0.3887** | 4.421 | 3.666 | **0.651** |
| 50-100 | +15.17 ± 8.65 | 0.3557 | 0.4770 | 4.883 | 3.813 | 0.577 |
| 100-150 | +30.70 ± 9.97 | 0.3430 | 0.4997 | 4.744 | 3.880 | 0.536 |
| 150-200 | +51.87 ± 9.24 | 0.3620 | 0.5350 | 4.774 | 4.142 | 0.535 |
| 200-500 (mean) | +34.7 | 0.3355 | 0.4924 | 4.678 | 3.991 | 0.562 |

Three facts fall out with no interpretation required:

1. **In the first 50 rounds our hit rate and theirs are the same** (0.3830 vs 0.3887) and our
   yield per hit is much better (4.421 vs 3.666). That is why we are ahead.
2. From round 50 their hit rate **rises by 10.8 pp and stays** (0.3887 → 0.4962 mean over rounds 50-500)
   while ours **falls by 4.1 pp** (0.3830 → 0.3415). Our yield per hit stays better throughout, on every block.
3. **Our first-mover rate falls with it**, 0.651 → 0.536-0.577, and tracks the deficit block by
   block.

The same table on the maps we do not lose: on map2 their hit rises 6.4 pp and on **map3 it does not
rise at all** (−0.5 pp). The magnitude of the opponents' mid-game hit-rate gain is
**+10.8 / +6.4 / −0.5 pp** on map1/map2/map3 — the same ordering as the deficit.

### 2.3 The per-unit split does not localize

Exact additive attribution (charging our unit `u` with `theirs_total/2 − ours_u`; residual 0.000):

| map | deficit | our u0 | our u1 | their u0 | their u1 | charge u0 | charge u1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| map1 | 286.1 ± 51.7 | 792.6 | 732.4 | 934.8 | 876.2 | **112.9 ± 27.4** | **173.2 ± 29.2** |
| map2 | 25.2 ± 70.6 | 1003.2 | 899.5 | 967.2 | 960.7 | −39.2 | 64.5 |
| map3 | −73.9 ± 83.5 | 596.0 | **382.6** | 435.5 | 469.2 | −143.7 | 69.8 |

Both of our units are deficient on map1 (39/61 split, neither dominant). The `u1` shortfall is a
*global* property of the build — u1 earns less than u0 on all three maps — and it is **largest on
map3 (−36%), the map we win**. The two anchors are indeed asymmetric, but that asymmetry is not
map1's lesion.

### 2.4 The spatial series does not localize either

Our own fog-free occupancy, identical on all three maps across all 20 blocks:

| map | region-1 share | u0 anchor distance | u1 anchor distance | both units in region 1 |
|---|---:|---:|---:|---:|
| map1 | 0.94-0.98 | 1.72-2.78 | 1.73-2.63 | 0.88-0.96 |
| map2 | 0.88-0.99 | 1.85-2.33 | 2.04-3.69 | 0.76-0.97 |
| map3 | 0.88-1.00 | 1.08-2.21 | 1.56-2.51 | 0.88-1.00 |

The build parks where it is designed to park, on every map. There is no positional drift, no
map1-specific displacement, and no decay over the game.

### 2.5 What does localize: the move-order identity

Exactly one side moves first in each round, so with `f` = our first-mover rate, `A` = (our income −
their income) averaged over the rounds **we** move first, and `B` = the same over the rounds **they**
move first:

```
net_per_round  =  f · A  +  (1 − f) · B          (an identity, not a model)
break-even f*  =  −B / (A − B)
```

Both `A` and `B` are within-round differences, so no matching is needed and no causal claim is made.

| map | f | A | B | **f\*** | **margin** | won on our-first rounds | lost on their-first rounds | net (identity) | net (observed) | residual |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **map1** | 0.5673 | +1.244 | −2.953 | **0.7036** | **−13.6 pp** | +352.8 | **−638.9** | −286.1 | −286.1 | **0.00** |
| map2 | 0.6827 | +1.393 | −3.155 | 0.6938 | −1.1 pp | +475.3 | −500.6 | −25.3 | −28.2 | +3.00 |
| map3 | 0.6235 | +1.158 | −1.525 | 0.5684 | **+5.5 pp** | +361.1 | −287.2 | +73.9 | +70.9 | +3.00 |

The residual on map2/map3 is **exactly +3.00**, which is our vision spend — the identity is on gross
gold and the observed net subtracts it. That is a complete, self-checking accounting closure.

**Read the map1 row plainly: we earn +352.8 gold on the 56.7% of rounds where we move first, and we
lose 638.9 on the 43.3% where they do.** And `A > 0` means that *round for round, when we get to
move first, `f18064c` out-collects T-1 and Tundra*. We do not lose map1 because we collect badly. We
lose it because we do not move first often enough.

**Value of the channel: 20.98 gold/game per pp of first-mover rate on map1** (`0.01 · 500 · (A − B)`),
22.74 on map2, 13.42 on map3.

### 2.6 Out-of-sample: the result replicates on every disjoint split

There are no seeds here — these are platform games — so the equivalent of the disjoint-seed rule is
a disjoint-**game** rule. Three independent partitions:

| split | n | f | A | B | f\* | margin | gold/pp | fallback ns/unit | fallback share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 30 | 0.5673 | +1.244 | −2.953 | 0.7036 | −13.6 | 20.98 | 40 | 0.535 |
| **opponent Tundra** | 24 | 0.5750 | +1.266 | −3.073 | 0.7083 | −13.3 | 21.69 | 40 | 0.531 |
| **opponent T-1** | 6 | 0.5363 | +1.151 | −2.515 | 0.6860 | −15.0 | 18.33 | 40 | 0.551 |
| campaign `frTu1`+`lnA0` | 12 | 0.5490 | +1.357 | −3.053 | 0.6923 | −14.3 | 22.05 | 40 | 0.522 |
| campaign `a2A0`+`alA0` | 12 | 0.6010 | +1.182 | −3.094 | 0.7236 | −12.3 | 21.38 | 35 | 0.540 |
| even game ids | 16 | 0.5641 | +1.199 | −2.760 | 0.6970 | −13.3 | 19.80 | 45 | 0.538 |
| odd game ids | 14 | 0.5709 | +1.294 | −3.177 | 0.7106 | −14.0 | 22.36 | 35 | 0.532 |

`A` is positive on **every** split (+1.15 to +1.36). `f*` is 0.686-0.724 on every split. The margin
is −12.3 to −15.0 pp on every split, **across two different opponents**. This is not a one-window
artefact, and it is exactly the check my own previous round failed to do before quoting a 7.30σ
in-sample number that collapsed to 0.12σ out of sample.

---

## 3. Why our first-mover rate is low on map1

### 3.1 We win the median and lose the tail

Per-round decision cost, rounds ≥ 2 (rounds 0-1 are the ~2 μs cold start):

| map | side | P5 | P25 | **P50** | **P75** | **P90** | P95 | race losses |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **map1** | ours | 150 | 180 | **200** | **250** | **310** | 400 | **0.432** |
| | theirs | 170 | 190 | **210** | **230** | **290** | 350 | |
| map2 | ours | 140 | 170 | 190 | 220 | 290 | 380 | 0.315 |
| | theirs | 160 | 190 | 210 | 250 | 290 | 330 | |
| map3 | ours | 150 | 170 | 190 | 220 | 270 | 320 | 0.374 |
| | theirs | 160 | 190 | 210 | 230 | 250 | 280 | |

**On map1 our median is 10 ns faster than theirs and our P75 is 20 ns slower.** On map2 we are
faster at every quantile up to P90. And **48.2% of the rounds we lose the race have our cost above
our own P75**. The race is decided in our upper quartile, not at our median — which is exactly the
part of the distribution that the current cold/hot annotation strategy optimises *against*.

### 3.2 The tail is the fallback branch, with a clean dose-response

Our own effective action shapes are logged in full (own units are never fogged). `(a,4,4)` and
`(4,4,4)` can only come from the `ok==0` fallback at `player.cpp:509-514`, because the LUT
(`SL.fact`) always emits three moves for `d ≥ 1`. Conditioning our cost on how many of the two units
took that branch, excluding `round%20==0`:

| map | 0 fallback units | 1 unit | 2 units | **marginal ns/unit (P50)** | rounds with any fallback |
|---|---|---|---|---:|---:|
| **map1** | P50 **180** (n=6580) | P50 **220** (n=5966) | P50 **260** (n=1674) | **+40** | **0.535** |
| map2 | 180 (n=3483) | 210 (n=1895) | 250 (n=310) | +35 | 0.386 |
| map3 | 180 (n=2834) | 210 (n=2222) | 230 (n=632) | +25 | 0.502 |

**180 → 220 → 260 is exactly linear at +40 ns per unit.** A monotone dose-response with a named
source-level mechanism is much more than a correlation: in steady state the *only* variable-cost part
of `decide` is this branch. The scan is a fixed five-row AVX load, the LUT lookup is fixed, the `blk`
composition is fixed; what varies is `if (ok) { three stores } else { steerStep(...) }`, where
`steerStep` is an out-of-line call that may call `escapeStep`, and `escapeStep` is
`__attribute__((noinline, cold))` (`player.cpp:156`). The `ok` branch is taken ~70% of the time, so
the fallback is also the *mispredicted* side.

### 3.3 And a second, cheaper tail: `waveTick`

Our cost on map1 by `round % 20`:

| residue | 0 | 1-19 (range) |
|---|---:|---:|
| P50 | **270** | 200-210 |
| P90 | **490** | 290-330 |
| mean | **316.9** | 221.7-258.5 |

`waveTick` (`player.cpp:240-243`) is `noinline, cold`, fires on `round % 20 == 0` (`:376`) and
`memset`s the 92-byte `bombbit`. It costs **+70 ns at the median and +190 ns at P90**, on 25 rounds
per game. map2 +50 ns, map3 +60 ns.

### 3.4 The latency → gold transfer function, measured not modelled

Both costs are logged every round, so the engine's dispatch rule can be re-run exactly under a
hypothetical shift of **our** cost only. This is a recomputation, not a model.

| shift of our cost | −40 ns | −30 | −20 | −10 | 0 | +10 | +20 |
|---|---:|---:|---:|---:|---:|---:|---:|
| map1 first-mover rate | 0.2969 | 0.3644 | 0.4346 | 0.5005 | **0.5673** | 0.6246 | 0.6778 |

**+5.73 pp of first-mover rate per 10 ns.** Combined with the RD flip value below, that is
**9.4 gold/ns**; combined with the identity's average `gold/pp` it is **12.0 gold/ns**. Both bracket
`src/INFRA.md §2.5`'s **11 gold/ns**, which this report therefore confirms from primary logs by a
completely independent route. Cost is logged at 10 ns granularity, so sub-10 ns counterfactuals
cannot be resolved.

**Regression-discontinuity price of one flipped round.** Restricting to rounds where
`|our cost − their cost| ≤ 10 ns` matches our own cost — and therefore our own branch mix and local
state — across the two arms, while which side moves first is decided by a few nanoseconds:

| stratum | our order gap | their order gap | **net swing per flipped round** |
|---|---:|---:|---:|
| observational | +2.366 | +1.831 | 4.197 |
| **RD, ≤10 ns** | **+1.631** | **+1.658** | **3.289** |

Every "RD" gold figure below uses 3.289 gold per flipped round, which is 22% below the observational
figure — i.e. the selection bias is real and has been removed rather than argued away.

---

## 4. Stock/flow: race-lost gold is novel, and the prior round's 87%-timing figure does not apply here

The five-region snapshot is a global, fog-free accounting of the whole board:

| map | generated/game | collected/game | collected/generated | region-1 share of generation | region-1 collected/generated |
|---|---:|---:|---:|---:|---:|
| map1 | 9815.2 | 9697.8 | **0.988** | 0.496 | **0.993** |
| map2 | 10383.2 | 10297.3 | 0.992 | 0.533 | 0.996 |
| map3 | 7366.6 | 7308.4 | 0.992 | 0.348 | 0.995 |

**The board is essentially fully harvested, region by region.** Of map1's 9698 collected gold we take
1525 (15.7%) and they take 1811 (18.7%); the remaining **6362 (65.6%) goes to the seven NPCs**. Per
unit that is 7.86% for us against 9.34% for them and a 1/11 par of 9.09% — map1 is the one map where
our per-unit share falls below par while theirs sits on it.

On such a board, "we will collect it later" is not available. Tested directly, conditioning on losing
the race in round `r` and following our income forward:

| offset | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---:|---:|---:|---:|---:|---:|
| map1 income after **winning** the race | 4.067 | 3.153 | 3.152 | 3.158 | 3.101 | 3.103 |
| map1 income after **losing** it | 1.698 | 2.914 | 2.935 | 2.941 | 3.025 | 3.013 |
| gap this round | **+2.369** | +0.239 | +0.216 | +0.217 | +0.076 | +0.090 |
| cumulative gap | +2.369 | +2.608 | +2.825 | +3.041 | +3.117 | **+3.207** |

**The share of the round-0 loss still open after five rounds is 1.354 on map1** (1.329 map2, 1.427
map3) — greater than one, i.e. **nothing is recovered and the gap slightly widens.** All gold in this
channel is novel.

This is a methodological correction to `sim/reports/path_harvest_oracle.md` §6, which found 87-92% of
a per-round counterfactual to be *timing* gold. That measurement was made in **uncontested self-play**,
where a skipped cell keeps 35%, keeps growing, and is re-entered by our own trajectory. Here, with
seven NPCs and two enemy units in the same central region, the cell is depleted immediately by
somebody else. **Contention converts timing gold into novel gold**, and the conversion is complete.

---

## 5. Verdict on Lead A — the opening: DISCARDED

The localization refutes it before any mechanism is examined: the cumulative deficit at round 24 is
**−19.1** and at round 99 **−4.6**. We are *ahead* for the first ~120 rounds of map1. There is no
deficit in the opening to recover.

One real defect was nevertheless found and is worth a line in `CHANGELOG` even though it is small.
map1's opening is a baked BFS route whose declared targets are **`(6,6)` and `(10,10)`**
(`player.cpp:89-95`, `ORT_A` comments), while the anchors have since been moved to **`(6,8)` and
`(11,8)`** (`player.cpp:372`, the 8.10 central-anchor change). `slowMove`'s runtime BFS on map2/map3
targets the anchor directly (`player.cpp:316`, `goal = anch_r[u]*N + anch_c[u]`). Measured
consequence, fog-free:

| map | u0 first reaches `(6,8)` | u1 first reaches `(11,8)` | our gold, rounds 0-24 |
|---|---:|---:|---:|
| **map1 (baked route)** | median round **8.0** | median round **8.0** | 81.6 |
| map2 (runtime BFS) | 6.5 | 6.5 | 92.4 |
| map3 (runtime BFS) | 5.0 | 7.5 | 38.2 |

So map1's baked route delivers the units to their anchors **1.5-3 rounds later** than the runtime BFS
does on the other maps, because it aims at a stale pair of cells. At ~3 gold/round that is worth
**~5-11 gold/game**. It is a free correctness fix, not a lesion.

**The honest reading of my own previous-round datum.** Substituting behaviour in 16 opening
unit-rounds collapsed a game from 1369 to 79 net. That proves the opening is **fragile to damage**,
which is a one-sided property: breaking it costs >1200 gold, and fixing it — measured here — is worth
about 5-11. High leverage is not headroom. Lead A is discarded.

---

## 6. Verdict on Lead B — the walls: DISCARDED as supply, VINDICATED as latency

### 6.1 The supply mechanism is discarded, with evidence

Static geometry from log row 2 (primary source; `sim/maps.json` matches all three layouts exactly):

| map | walls | region-1 walls | **region-1 generation capacity** | region-1 share of generation | region-1 collected/generated |
|---|---:|---:|---:|---:|---:|
| **map1** | 40 | 16/81 (19.8%) | **65 cells** | 0.496 | 0.993 |
| map2 | 24 | 12/81 (14.8%) | 69 cells | 0.533 | 0.996 |
| **map3** | 78 | **54/81 (66.7%)** | **27 cells** | 0.348 | 0.995 |

Four independent reasons the supply story fails:

1. **map1's centre is not starved.** Its generation capacity (65 cells) is within 6% of map2's (69),
   and its share of total generation (49.6%) is within 7% of map2's (53.3%). map1 and map2 are the
   same map on this axis, and their outcomes differ by 258 gold.
2. **The map with the most-deleted centre is the map we win.** map3's central region is 66.7% walls
   with only 27 generation cells — 2.4× more concentrated than map1 — and Tundra map3 is +245.8
   (2.26σ), our only stable win.
3. **There is no unclaimed central gold for a better anchor to find.** Region 1 on map1 is
   **99.3% harvested**. A map-specific anchor cannot collect gold that is already gone; it can only
   change *who* collects it, which is the contention channel, not the supply channel.
4. The existing anchor A/B evidence agrees: `(6,8)/(10,8)` vs `(6,8)/(11,8)` returned +37.6 at 0.96σ
   over a 30-game pre-registered probeobs A/B. Neighbouring cells do not pay, and a geometry-derived
   placement has nothing to aim at given (3).

### 6.2 The same walls are vindicated through latency

The chain is fully sourced and every link is measured:

```
map1 has 16 central walls (19.8% of region 1, vs map2's 12)
  -> the `ok` waypoint check at player.cpp:504-506 fails more often
     (fallback present in 53.5% of map1 rounds vs 38.6% on map2)
  -> the fallback runs steerStep -> possibly escapeStep, which is noinline+cold
     (+40 ns per fallback unit, dose-response 180/220/260 ns)
  -> our cost P75 becomes 250 ns against their 230 (median is still 10 ns ahead)
  -> we lose the dispatch race in 43.2% of rounds against 31.5% on map2
  -> at 20.98 gold/pp, the 13.6 pp shortfall against break-even is the 286-gold deficit
```

This is precisely the mechanism my own previous round could not see, because it priced the walls on
the **income** channel (worth ~0, correctly) and the walls act on the **latency** channel. Both
conclusions stand together: the wall detour is worth nothing, and the wall *branch* is worth
hundreds.

---

## 7. Priced candidates

Exact race recomputation under each shift, valued at 3.289 gold per flipped round (RD) and
20.98 gold/pp (identity). map1, 30 games, 14,940 rounds.

| # | candidate | shift applied | first-mover rate | Δpp | **gold/game (RD)** | gold/game (identity) |
|---|---|---|---:|---:|---:|---:|
| — | baseline | none | 0.5675 | — | — | — |
| **C1** | **branchless / warm fallback** | −40 ns per fallback unit | 0.7292 | +16.16 | **+265.8** | +339.2 |
| C1′ | …with a realistic +10 ns uniform cost | −40 ns/unit, +10 ns always | 0.6675 | +9.99 | **+164.3** | +209.7 |
| C1″ | …with a +20 ns uniform cost | −40 ns/unit, +20 ns always | 0.5957 | +2.82 | +46.3 | +59.1 |
| C1½ | half the fallback cost removed | −20 ns per fallback unit | 0.6517 | +8.42 | +138.5 | +176.7 |
| **C2** | **amortise `waveTick`** | −70 ns on `round%20==0` | 0.5870 | +1.95 | **+32.0** | +40.9 |
| C1+C2 | both | both | 0.7499 | +18.24 | **+299.9** | +382.8 |

**Break-even is f\* = 0.7036.** C1 alone reaches 0.7292 and therefore flips map1 from −286 to
positive. C1′ reaches 0.6675 and closes 57% of the gap.

**Stock/flow:** all of it is novel (§4), so no discount applies. **Bias:** the RD column is the
conservative one; the identity column is 28% higher and inherits the observational selection.
The 10 ns cost granularity means the +5 ns and +10 ns uniform-penalty scenarios are
indistinguishable in the log and are reported as one row.

### 7.1 Instruction budget

At `1.6 gold/instruction` (0.1454 ns/instr × 11 gold/ns, `src/INFRA.md` §1 line 20 and §2.5 line 111,
re-verified) and with the frozen header's own marginal caveat (84 instructions returned only
5.6 cycles, ~6× below the average price):

| candidate | what it costs | budget implied by the measured gain |
|---|---|---|
| C1 | making the fallback branchless means computing the steer step unconditionally: ~25-40 instructions on 100% of rounds. At the *average* price that is 40-64 gold; at this codebase's *marginal* price ~7-11 gold | gain +266 (RD). **The acceptance gate is: the uniform cost must stay ≤ ~10 ns (≈70 instructions), or the gain collapses from +164 to +46** |
| C2 | spreading the 92-byte `bombbit` clear over the wave (e.g. clear two rows per round, or gate rows by a wave counter) — a handful of instructions, no new text | gain +32 at ~zero cost. **Highest value density in this report** |

**The efficiency argument for targeting the tail.** The mean saving of C1 is 0.655 fallback
units/round × 40 ns = 26.2 ns, which as a *uniform* shift would buy ~+13.9 pp; targeted at the
fallback it buys **+16.16 pp**. Removing latency where we were actually losing the race is ~17% more
gold per average nanosecond than removing it uniformly.

### 7.2 In gold/game per pp of hit rate

Per criterion 5, converting to the opponent-independent currency. Our map1 yield per hit is 4.7312
(`sim/reports/gold_delta_channel.json`, Tundra map1), so over 2 units × 500 rounds:

* **1 pp of hit rate = 47.3 gold/game.**
* Closing map1's 286-gold deficit therefore needs **+6.05 pp of hit rate**.
* C1 (+266 gold) is worth **+5.6 pp of hit rate**; C1′ (+164) is **+3.5 pp**; C2 (+32) is **+0.7 pp**.
* 1 pp of first-mover rate = 20.98 gold = **0.44 pp of hit rate**. 10 ns = 5.73 pp of first-mover
  rate = **+2.5 pp of hit rate**.

---

## 8. Generality — and the honest limit of this finding

This is where the strategic recalibration bites, and it cuts against my own candidate.

**The dispatch race can only be contested by an opponent whose decision cost is within ~±150 ns of
ours.** Run the same identity against every opponent account with archived map1 games:

| opponent | games | our P50 | **their P50** | **our f** | A | our income at f≈1 | their income at their f≈0 |
|---|---:|---:|---:|---:|---:|---:|---:|
| T-1 `t1f1` (f18064c) | 6 | 208 | **215** | **0.536** | +1.151 | — | — |
| Tundra (f18064c ×4) | 24 | 204 | **211** | **0.575** | +1.266 | **2037** | 1415 |
| player167 Ausdroid | 14 | 300 | 785 | **0.969** | −0.865 | 1346 | 1779 |
| player167, our fast subset | 7 | 240 | 780 | **0.985** | −1.155 | 1247 | 1824 |
| player147 神秘高手 | 6 | 235 | 1040 | **0.999** | −0.115 | 1408 | 1465 |
| player132 GoldMiner | 10 | 240 | 930 | **0.998** | +0.276 | 1518 | 1379 |
| player83 | 1 | 220 | 450 | 0.980 | −0.400 | 1391 | 1591 |
| player186 若叶睦 | 1 | 250 | 380 | 0.852 | +0.444 | 1737 | 1515 |
| player47 rikka | 2 | 235 | 365 | 0.815 | −0.059 | 1472 | 1501 |

**Build-mixture warning, mandatory:** only the two `f18064c` rows are one strategy. Every mid-field
row was faced by a mixture of older and partly crippled experimental builds. Use these rows for the
*shape* of the identity, never for our ability.

Three conclusions:

1. **Against ~everyone except the few fast teams, `f` is already 0.97-1.00 and the latency lever is
   worth zero.** Ten of the twelve archived mid-field opponents run at 450-46,930 ns. Independently,
   `sim/reports/field_position.md` finds only 18 of 100 ranked teams have a board P90 ≤ 1 μs against
   our 300 ns. **So C1 and C2, however large on map1 against T-1 and Tundra, buy nothing against the
   bulk of a 117-team round robin.** They are worth banking because they are cheap and because the
   fast teams are also the strong teams, not because they move the win rate much.
2. **Against those opponents the whole net score is `500·A` — our collection quality at
   first-mover parity — and that is the general lesion.** The mid-field rows show `A < 0` for five of
   eight opponents, i.e. builds of that era were out-collected even while moving first.
3. **We have no `f18064c` measurement of `A` against any mid-field opponent, and that is the single
   most valuable missing number in the project.** The two `f18064c` rows say our income when moving
   first on map1 is **2037 gold/game** — consistent with the uncontested `probeobs` 2182.4 — whereas
   the builds that went 1W-14L against Ausdroid managed 1247-1737 at the same move order. If 2037
   transfers, `f18064c` beats Ausdroid's 1824 by roughly **+213**; if it does not, the general lesion
   is collection quality and nothing in the latency line will fix it.

### 8.1 Platform quota request (not run — stated as required)

* **Target:** measure `A` for `f18064c` at `f ≈ 1` against a mid-field opponent on map1, i.e. test
  whether our first-mover collection level (2037 gold/game) transfers off the two tracked opponents.
* **Opponent:** `player167` (Ausdroid, ~rank 18, the closest observable proxy for the rank-16
  cutoff), on map1.
* **Count:** **6 games** (the project's established milestone unit; enough to separate +213 from 0 at
  the observed per-game SD of ~200-350 if the effect is real, and enough to see the sign).
* **Pre-registered read:** `f` should be ≥ 0.97 (their P50 is 780 ns). If `A > 0` the general lesion
  is *not* collection quality and the latency line is the right one to fund. If `A < 0` at `f ≈ 1`,
  the latency line is a dead end for the win rate and the next round must attack collection quality
  directly. Either result is decisive; there is no ambiguous outcome.
* **Why this and not a candidate A/B:** C1/C2 are latency changes whose *behaviour* is unchanged, so
  they can be gated locally with `pair_diff` and on the race machine with cycle counts. Quota is
  better spent on the one thing no local measurement can produce.

---

## 9. Bias register

| number | direction / status | reason |
|---|---|---|
| the channel itself | **unbiased and complete** | per-unit gold in 100% of unit-observations; the identity reproduces the observed net with residual 0.00 (map1) and +3.00 (map2/3 = our vision spend) |
| `A`, `B`, `f`, `f*`, gold/pp | **exact accounting**, no model | within-round differences; replicate on all seven disjoint splits |
| observational order gap (+2.366) | **biased UP** | cost is endogenous to the branch taken |
| RD order gap (+1.631), all "gold (RD)" figures | **the conservative estimate** | `\|cost gap\| ≤ 10 ns` matches our own cost across arms; 22% below observational |
| +40 ns/unit fallback cost | **observational with a monotone dose-response and a named source mechanism** | 180/220/260 ns at 0/1/2 units; in steady state this is the only variable-cost branch. Not an isolated causal measurement |
| C1's gold value | **highly sensitive to its own uniform cost** | +266 at 0 ns, +164 at 10 ns, +46 at 20 ns. Must be gated on the race machine |
| cost counterfactuals below 10 ns | **unresolvable** | platform cost is logged at 10 ns granularity |
| field probe absolute levels | **not our ability** | mixed and partly crippled builds; shape only |
| stock/flow | **novel, measured not assumed** | 1.354 of the round-0 gap still open after 5 rounds; board 98.8% harvested |
| local NPC model | **not used anywhere in this report** | every comparison is between the two seats of the same platform game, so the over-central NPC bias cannot enter |
| absolute income | **platform-to-platform only** | no simulator number is compared to a platform number |

---

## 10. Reproduce

```sh
mkdir -p /tmp/gr_lesion
# baseline provenance (no build needed: this report reads logs only)
git show f18064c:src/player.cpp | shasum -a 256      # 0ecce6fc...84fdd

python3 -m sim.analyze_map1_lesion geometry    --out /tmp/gr_lesion/geometry.json     # <1 s
python3 -m sim.analyze_map1_lesion localize    --out /tmp/gr_lesion/localize.json     # ~3 s
python3 -m sim.analyze_map1_lesion identity    --out /tmp/gr_lesion/identity.json
python3 -m sim.analyze_map1_lesion contention  --out /tmp/gr_lesion/contention.json
python3 -m sim.analyze_map1_lesion branch-cost --out /tmp/gr_lesion/branch_cost.json  # ~20 s
python3 -m sim.analyze_map1_lesion stock-flow  --out /tmp/gr_lesion/stock_flow.json
python3 -m sim.analyze_map1_lesion holdout     --out /tmp/gr_lesion/holdout.json
python3 -m sim.analyze_map1_lesion supply      --out /tmp/gr_lesion/supply.json
python3 -m sim.analyze_map1_lesion field       --out /tmp/gr_lesion/field.json
python3 -m sim.analyze_map1_lesion assemble    --out sim/reports/map1_lesion.json

# cross-checks against already-committed tooling (read-only)
python3 sim/analyze_gold_delta.py validate     # proves the f18064c family identification
```

Whole suite is well under a minute; nothing compiles, nothing simulates, no platform games.
**Sample sizes:** map1 **30** games / 14,940 rounds / 29,880 unit-rounds; map2 and map3 **12** games
each. Reported rather than maximised — this is the entire `f18064c` map1 corpus that exists.

---

## 11. Corrections to the commissioning brief (军规 27)

Verified correct and used as given: the round-robin/win-rate qualification structure and the
delatency-is-not-a-ranking-term reading (`docs/PRELIM_RULES.md` §2.1-2.5); ≥117 teams and the
45.6 pp gap to the rank-16 cutoff (`sim/reports/field_position.md` §1-2); the four-arm Tundra map1
backfill and its pooled −289.04 ± 54.65 (`sim/reports/archive_backfill.json`); `f18064c`'s source
hash; `fd47ea6` being bit-identical on known maps; 0.1454 ns/instruction and 11 gold/ns
(`src/INFRA.md`); the `(6,8)/(10,8)` anchor A/B at +37.6 / 0.96σ.

| # | claim as commissioned | found | verdict |
|---|---|---|---|
| 1 | "is the deficit front-loaded in the opening, uniform, or late?" — with Lead A framed as the primary suspect | **late.** Cumulative deficit is −19.1 at round 24 and −4.6 at round 99; we are ahead for ~120 rounds | **Lead A refuted by the localization** |
| 2 | "the two anchors are not symmetric" (per-unit axis as the second suspect) | true but not map1's lesion: the u1 shortfall exists on all three maps and is **largest on map3, which we win** | **does not localize** |
| 3 | "map1 has 40 walls including the six central interior ones … those walls may cost us by deleting generation cells from the central peak" | map1 region-1 capacity 65 cells vs map2's 69 (−6%) and generation share 49.6% vs 53.3%; region 1 is **99.3% harvested**; **map3's centre is 66.7% walled with 27 cells and map3 is the map we win** | **supply mechanism discarded**; the walls act through latency instead |
| 4 | "the anchor `(6,8)/(11,8)` is shared across all three maps despite their geometry differing, so a map-specific anchor would cost ~0 instructions" | the premise is true, but there is nothing for it to aim at: the central region is already 99.3% harvested, so a relocated anchor changes who collects, not how much exists | **premise true, inference discarded** |
| 5 | "map1's opening is structurally unique — a baked BFS route, 4 rounds out of the corner" | true, and it contains a real stale-constant defect: `ORT_A` aims at `(6,6)`/`(10,10)` while the anchors are `(6,8)`/`(11,8)`, so map1 reaches its anchors at median round **8.0** vs 6.5 on map2. Worth ~5-11 gold/game | **new finding, small; recommend fixing as correctness** |
| 6 | "16 opening unit-rounds out of 1000 are worth >1200 gold" (my own prior datum, offered as evidence of headroom) | correct as stated but **one-sided**: it measures fragility to damage. Measured headroom in the opening is ~5-11 gold | **corrected: leverage ≠ headroom** |
| 7 | "Tundra map1 pooled −289.04 ± 54.65" | reproduced independently here at **−286.10 ± 51.69** over the same 30 games including T-1 (Tundra-only subset: **−289.04**, exact) | **confirmed** |
| 8 | "do not treat their 41.1% hit rate as the target; frame as our own absolute level" | adopted, and the framing turns out to be load-bearing: at equal move order our collection is **better** than T-1's and Tundra's (`A > 0` on all seven splits). The absolute level that matters is 2037 gold/game at `f ≈ 1` | **adopted; changes the conclusion** |
| 9 | "the deficit is not latency" (from Ausdroid: 1W-14L despite a 2-800× latency advantage) | **true for the field, false for map1 against the two fast opponents.** Against Ausdroid `f = 0.969` so latency was already saturated; against T-1/Tundra `f = 0.536-0.575` and the race is the whole deficit. Both statements coexist | **refined, not contradicted** |
| 10 | (implicit in the prior round) 87-92% of counterfactual gold is *timing* | that holds in uncontested self-play only. On contested platform boards, 98.8% harvested, **0% is timing** (1.354 of the round-0 gap still open at +5 rounds) | **scope of the prior result corrected** |

---

## 12. Recommendations for `src/*` (I must not edit them)

Ordered by gold per instruction, with the gate each must pass.

1. **C2 — amortise `waveTick` (highest value density).** `+32 gold/game` on map1 (`+25` map2,
   `+24` map3) for a handful of instructions and no new text. Clear `bombbit` incrementally instead
   of in one cold `memset` on `round%20==0`: e.g. clear two rows per round on a rolling index, or
   carry a 5-bit wave tag per row and treat a stale tag as zero. **Gate:** `pair_diff` 0/500 on all
   three maps (behaviour must be identical), and the `round%20==0` cost quantile must lose its spike
   on the race machine.
2. **C1 — remove the cold-fallback tail.** `+164 to +266 gold/game` on map1, `+125 to +189` on map2,
   `+99 to +125` on map3. Compute the `steerStep` result unconditionally and select between it and
   the LUT triple with a mask, so the branch is never mispredicted and `escapeStep` is never a cold
   out-of-line call. **Hard gate, stated numerically because the value collapses fast:** the uniform
   cost added to the non-fallback path must be **≤ 10 ns (~70 instructions)**; at 20 ns the gain
   falls from +164 to +46 and the change is not worth its risk. Also re-check `moveDecision`'s
   mod-64 alignment, which `CHANGELOG` flags as coupled to `decide`'s size.
3. **Fix the stale baked-route target.** `ORT_A`/`ORT_R`/`ORT_C` aim at `(6,6)`/`(10,10)`; the
   anchors are `(6,8)`/`(11,8)`. Re-bake the 4-round route to the current anchors, or delete the
   baked route and let `slowMove`'s runtime BFS handle map1 as it does map2/map3. Worth ~5-11
   gold/game; the real argument is that a constant contradicting another constant will bite again.
4. **Record the pricing constants in `INFRA.md`, now that they are measured rather than modelled.**
   map1 first-mover transfer function **+5.73 pp per 10 ns**; **20.98 gold/game per pp** of
   first-mover rate on map1 (22.74 map2, 13.42 map3); RD flip value **3.289 gold per flipped round**;
   break-even first-mover rates **0.704 / 0.694 / 0.568**. The independently derived **9.4-12.0
   gold/ns** brackets the existing 11 gold/ns and confirms it from primary logs.
5. **Record the scope correction on stock/flow.** The 87-92%-timing discount is valid for
   uncontested self-play and **invalid for contested platform play**, where the board is 98.8%
   harvested and lost gold is 100% novel. Any future bound must state which regime it is in.
6. **Do not fund latency work on the strength of the win rate alone.** Against ≥90% of a 117-team
   field our first-mover rate is already ~1.0 and C1/C2 are worth zero there. Fund them because they
   are cheap and because they flip map1 against the strong teams — and fund the §8.1 quota request
   first, because it decides whether the next round should attack latency or collection quality.
