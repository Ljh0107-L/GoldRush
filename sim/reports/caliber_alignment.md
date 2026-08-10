# Caliber alignment — where `f18064c`'s collection quality sits at matched action order

> Compiled 2026-08-10 by `caliber_alignment-18c4`. **Zero platform games consumed**; archived logs
> only (676 `logs/game_*.log`). New files only: `sim/analyze_caliber_alignment.py`,
> `sim/reports/caliber_passive_index.json`, this report and `sim/reports/caliber_alignment.json`.
> Nothing under `src/` touched; no file the parallel line created was modified; no `git` write.
> **军规 27**: every number below carries its build identity and sample size.

## 0. Verdict in four lines

1. **The two results were never measuring the same thing, and the mislabel is in the *provenance*,
   not the definition.** Result 2's "ours 36.0%" is **`f18064c` against T-1 and Tundra on 12 map1
   games**. It is a hard-coded constant in `sim/analyze_field_profile.py`, not a measurement of the
   133 passive games. The field's 42.3% is the *challengers'* side of 73 map1 passive games played
   against our **public defended slot**. The two numbers share **0 games, 0 builds, 0 counterparties**.
2. **Resolution (b) carries the conflict**, with (a) contributing in the direction that hurts us and
   (c) contributing exactly nothing.
3. **Result 1's identity survives; its gloss is refuted.** `A = +1.244` is exact (residual 3.4e-13,
   reproduced with the parallel line's own driver) but it compares *our first-mover rounds against
   their second-mover rounds*. At matched order `f18064c` is behind in **5 of 6** map x stratum cells.
4. **Unified statement**: at matched action order, `f18064c`'s map1 collection sits at roughly the
   **9th percentile of the field at our own order mix (f = 0.568) and the 27th percentile if we were
   given f = 1.0** — but **both figures are confounded by counterparty and neither should be quoted
   as a clean percentile.** Caveats in §7, statement in full at the end of §9.

### The three candidate resolutions, each with its share of the disputed 7.68 pp gap

The gap under one definition is `field median 42.32% − f18064c 34.64% = 7.68 pp` (D-CAL, map1).

| candidate | share | direction | evidence |
|---|---:|---|---|
| **(b) build mixture / corpus** | **>= +14.6 pp** — i.e. it **carries** the conflict and over-explains it | invalidates the comparison entirely | `f18064c` played **0 of 133** passive games (§2). The same statistic in the same 73 games puts our obsolete slot **14.6 pp above** `f18064c` (§5), so cross-corpus placement is not a quality ranking at all. |
| **(a) action order** | **−2.9 pp** | **against us** — the published comparison flattered us | field measured at f = 0.4005, ours at f = 0.5676. Standardised to the field's mix our hit falls 34.64% -> 31.71% (§4). |
| **(c) definition / denominator** | **0.00 pp** | none | D-CAL reproduces Result 2's q1/median/q3 and `gold_delta_channel.json`'s per-family hits **bit-identically** (§1, §3). |

## 1. The provenance correction (this leads, because it is the biggest finding)

`sim/analyze_field_profile.py` computes the field distribution from logs, but takes *our* number from
a literal:

```python
REFERENCE = {"OURS frozen (map1)": {"hit": 0.360, "yield": 4.68, "note": "mean of 36.2/35.8"}, ...}
```

| quantity | value | build | corpus | n |
|---|---:|---|---|---:|
| `gold_delta_channel.json` `frTu1` ours hit | 0.36222 | `f18064c` | vs **Tundra** map1 | 6 games |
| `gold_delta_channel.json` `t1f1` ours hit | 0.35822 | `f18064c` | vs **T-1** map1 | 6 games |
| mean, i.e. the published "36.0%" | **0.360220** | `f18064c` | 12 map1 games | 12 |
| my independent recomputation under D-CAL | **0.360220** | `f18064c` | same 12 | 12 |

Bit-identical. So the 36.0% is correctly *computed* and correctly *labelled as the frozen construct*
inside `field_playstyle_profile.md` §4.2 — the error is that it was then placed inside a distribution
built from a different corpus. **Our public slot in the 73 map1 passive games hits 49.22%**
(72,854 unit-rounds), not 36.0%. The 13.2 pp difference is **100% build + corpus, 0%
map-stratification** (both are map1-only), **0% definition, 0% denominator**.

## 2. (b) Build composition of the 133 passive games — `f18064c` played **none** of them

Reconstructed from log headers alone: a passive game is one whose header `player2 == "player220"`.
That yields **133 games / 73 on map1**, a set **bit-identical** to the parallel line's platform-built
index (`sim/reports/caliber_passive_index.json`). Our slot logs the *account* name in 133/133, so the
build must be fingerprinted. Per-round `end.players[].cost`, rounds >= 4:

| side | build | n games | P50 min | P50 median | P50 max |
|---|---|---:|---:|---:|---:|
| our public slot, 133 passive games | **unknown, not `f18064c`** | 133 | **3200 ns** | **3640 ns** | 9600 ns |
| our side, map1 `f18064c` families | `f18064c` (`frTu1/lnA0/a2A0/alA0/t1f1/adf1`) | 36 | 190 ns | **200 ns** | 290 ns |

**Zero overlap; 11x separated at the extremes; 0 of 133 games below 1000 ns.** The alternative
explanation — that the defender seat inflates `cost` — is dead: in exactly that seat T-1 records
**200 ns** (n=158), Tundra **225 ns** (n=112), Ausdroid **770 ns** (n=20), GoldMiner **930 ns**
(n=10); and in 227 self-play games our own p1 seat is 230 ns against 280 ns in the p2 seat. The
~3.6 us is the build. Corroborating: our slot bought vision in **1 of 133** games.
**Bias direction: none needed — this is a categorical exclusion, not an estimate.**

## 3. (c) Definition / denominator — contributes **0.00 pp**

Definition D-CAL: `end.players[].units[].gold` differenced round over round, round 0 dropped
(499 diffs/unit), forfeit rows break the chain, `hit = P(delta > 0)` per **unit-round**, action order
from `end.players[].cost` of the same round (lower first, exact tie to P1). Re-verified here:
opponent `gold` present in **20000/20000** unit-observations, `position` 5230, `actions` 7372,
`pickup` 7372 — the gold channel is the only unbiased one. The log field `order` is **not** the
action order (constant at 0 in 499/500 rounds of `game_179643` while the cost rule flips 271/228;
fogged for the opponent) and is ignored.

| field cut (29 teams, map1) | q1 | median | q3 |
|---|---:|---:|---:|
| **published Result 2** | 0.359 | **0.423** | 0.451 |
| **D-CAL primary (mine)** | **0.3590** | **0.4232** | **0.4514** |
| variant: rounds 4-499 (warm-up dropped) | 0.3609 | 0.4249 | 0.4526 |
| variant: player-round denominator | 0.6032 | 0.6630 | 0.7174 |

Exact reproduction. Warm-up removal moves the median +0.17 pp. The round-0 convention differs between
the two committed drivers — `analyze_gold_delta.py` drops it (499/unit) while `analyze_map1_lesion.py`
seeds `previous = 0` and keeps it (500/unit) — and it moves `f18064c`'s first-mover hit by
**0.05 pp** (lesion driver 0.4216 vs D-CAL 0.4221). My own `low=0` variant is a no-op by construction
(no predecessor exists for round 0), which is why that row is identical; the 0.05 pp figure is the
real measurement, taken from the lesion driver's own output. The player-round denominator widens the
gap (7.68 -> 9.97 pp) but flips no sign.

## 4. (a) Action order — the stratified table, and it cuts **against** us

Field side, map1 passive corpus, D-CAL. Teams with < 200 unit-rounds in a stratum are excluded from
that stratum (per-team detail for all 29 in the JSON).

| stratum | teams | q1 | median | q3 | `f18064c` vs T-1+Tundra (30 g) | `f18064c` vs Ausdroid (6 g) | our public slot (73 g) |
|---|---:|---:|---:|---:|---|---|---|
| **team moves first** | 19 | 0.4277 | **0.4509** | 0.5174 | **0.4221 → 21st pct** (16,994 ur) | 0.3767 → 11th pct (5,970 ur) | 0.5469 → 79th pct |
| **team moves second** | 21 | 0.3490 | **0.3775** | 0.4180 | **0.2469 → 10th pct** (12,946 ur) | 0.2222 → *18 ur, unusable* | 0.4102 → 71st pct |
| pooled (Result 2's cut) | 29 | 0.3590 | **0.4232** | 0.4514 | 0.3464 → 21st pct | 0.3763 → 34th pct | **0.4922 → 90th pct** |

The field's pooled number is computed at **f_field = 0.4005** and ours at **f = 0.5676** — a
composition mismatch. Standardising every entity to a common `f` over the 11 teams that have >= 200
unit-rounds in **both** strata:

| common f | field q1 / median / q3 | `f18064c` (30 g) | our public slot (73 g) |
|---|---|---|---|
| **0.4005** (the field's own mix) | 0.3474 / **0.3782** / 0.4335 | **0.3171 → 0th pct** | 0.4650 → 82nd |
| 0.5676 (our mix) | 0.3853 / **0.3905** / 0.4569 | **0.3464 → 9th pct** | 0.4878 → 82nd |
| 1.0000 (our Ausdroid condition) | 0.4208 / **0.4427** / 0.5174 | **0.4221 → 27th pct** | 0.5469 → 82nd |

**Stated plainly and kept prominent: the published comparison flattered us by 2.9 pp.** At the
field's own order mix `f18064c`'s map1 hit falls 34.64% -> 31.71%, below every one of the 11
standardisable teams. Resolution (a) therefore contributes **-2.9 pp**: it makes the gap *worse*.

## 5. Why the placement in §4 is not a quality ranking — proof by contradiction

Within the very same 73 games, our **obsolete, 18x-slower public slot out-collects its opponent on
hit by +8.73 pp** (paired per game, SE 0.0166, **5.25σ**, higher in **52/73** games) and lands at the
**90th percentile** of the field distribution those same games generate. If hit-rate placement
measured build quality, a three-generations-old defender would be top-decile. It is not.
**Cross-corpus hit placement is therefore invalid, and the artefact is >= 14.6 pp** (49.22 − 34.64),
about **1.9x the entire 7.68 pp gap** Result 2 was arguing about.

Mechanism, measured: an opponent's hit rate falls as *our* build improves, because both sides draw on
one generated pool. Regressing the opponent's hit on ours across our own build arms, map1:

| opponent | our arms | all arms | forfeit/degenerate arms removed |
|---|---:|---|---|
| Ausdroid (20 games) | 9 | slope **−0.297**, r −0.66 | −0.297, r −0.66 (n=9) |
| Tundra (90 games) | 41 | slope **−0.397**, r −0.63 | **−0.318**, r −0.39 (n=34) |
| T-1 (99 games) | 54 | +0.375 (**contaminated** by crashed arms) | **−0.116**, r −0.20 (n=43) |
| GoldMiner (10 games) | 4 | +0.187, r 0.11 | n < 3, **not computable** |

Slope **−0.12 to −0.32** once broken arms are excluded. **Bias direction: this makes the field's
42.3% an over-estimate of field skill and our 34.6% an under-estimate of ours, by an amount the
corpus cannot pin down because there is no overlap to calibrate on.**

## 6. Order-sensitivity ratio (income when first / income when second), D-CAL, map1

| entity | build identity | f | inc 1st | inc 2nd | **ratio** | Δhit pp | ur 1st / 2nd | usable |
|---|---|---:|---:|---:|---:|---:|---|---|
| **`f18064c` vs T-1+Tundra** | `f18064c`, 30 g | 0.568 | 4.079 | 1.713 | **2.382** | +17.5 | 16,994 / 12,946 | yes |
| T-1+Tundra vs `f18064c` | 30 g | 0.432 | 4.674 | 2.834 | **1.650** | +13.4 | 12,946 / 16,994 | yes |
| `f18064c` vs T-1 only | 6 g | 0.535 | 4.221 | 1.869 | 2.258 | +17.6 | 3,206 / 2,782 | yes |
| T-1 vs `f18064c` | 6 g | 0.465 | 4.384 | 3.066 | 1.430 | +10.4 | 2,782 / 3,206 | yes |
| `f18064c` vs Tundra only | 24 g | 0.576 | 4.046 | 1.670 | 2.423 | +17.6 | 13,788 / 10,164 | yes |
| Tundra vs `f18064c` | 24 g | 0.424 | 4.753 | 2.780 | 1.710 | +14.3 | 10,164 / 13,788 | yes |
| `f18064c` vs Ausdroid | `adf1`, 6 g | 0.997 | 3.496 | 2.444 | (1.430) | (+15.4) | 5,970 / **18** | **NO** |
| Ausdroid vs `f18064c` | 6 g | 0.003 | 4.556 | 3.317 | (1.374) | (−0.4) | **18** / 5,970 | **NO** |
| T-1+Tundra vs ALL our builds | **~95-build MIXTURE**, 189 g | 0.665 | 4.526 | 3.006 | 1.506 | +10.1 | 122,864 / 61,944 | yes, but mixture |
| our public slot | unknown build, 73 g | 0.600 | 4.364 | 2.965 | 1.472 | +13.7 | 43,678 / 29,176 | yes |
| field pooled | 29-team MIXTURE, 73 g | 0.400 | 4.029 | 2.935 | 1.373 | +9.1 | 29,176 / 43,678 | yes |
| per-field-team distribution | 11 of 29 usable | — | — | — | min 0.755 / q1 1.278 / **med 1.517** / q3 1.916 / max 5.667 | | | |
| `f18064c` map2 / map3 | 12 g each | 0.683 / 0.624 | — | — | **2.245 / 3.117** (theirs 1.616 / 1.610) | | | yes |

**Correction to a number now in load-bearing use: the "Ausdroid 1.37x" figure is not usable.** It
rests on **18 unit-rounds** (9 rounds of 2,994); Wilson95 on Ausdroid's first-mover hit is
**[0.203, 0.614]**, so the ratio is consistent with anything from 0.7x to 2.5x. It appears as
supporting evidence for pre-registered prediction **P5** (`fad0030`, "2.38x ... against 1.65x and
1.37x") and in `231b657`. **P5's conclusion is unaffected** — 2.38x versus 1.65x is solid, and 2.38x
versus the *field* median of **1.52x over 11 teams / 72,854 unit-rounds** is a stronger and properly
powered version of the same claim. Recommend P5 be re-anchored on the field distribution below and
the Ausdroid figure dropped.

**`f18064c` is the most order-sensitive collector in the corpus**: 2.38x against the field median of
1.52x, above the field q3 of 1.92x, with only 1 of 11 usable field teams higher. That single fact
generates both disputed results — we look strong relative to *ourselves* when first, and weak in
absolute level whenever we are not.

## 7. Matched-order placement — and where it genuinely cannot be computed

`A` is `(our income − their income)` over the rounds **we** move first, so its second term is *their
second-mover* income. It is order-mismatched by construction. The matched cells, from the same games
and the same channel:

| map | build | n | `A` (mismatched) | matched **both first** | matched **both second** | matched Δhit 1st / 2nd |
|---|---|---:|---:|---|---|---|
| **map1** | `f18064c` | 30 | **+1.244** | **−0.547 ± 0.145 (−3.77σ)** | **−1.144 ± 0.116 (−9.87σ)** | **−13.9 pp / −18.2 pp** |
| map2 | `f18064c` | 12 | +1.393 | −0.594 | −1.169 | −10.7 / −17.1 |
| map3 | `f18064c` | 12 | +1.158 | **+0.260** | −0.628 | **+0.6** / −4.4 |

Weighted at the observed f, the matched-order collection deficit and the order advantage that offsets
it (same identity, same games, `net_observed = matched_deficit + order_advantage`):

| map | f | matched-order collection deficit | order advantage | net observed |
|---|---:|---:|---:|---:|
| map1 | 0.5673 | **−409.3** | +123.2 | −286.1 |
| map2 | 0.6827 | **−388.0** | +359.8 | −28.2 |
| map3 | 0.6235 | **−37.0** | +107.9 | +70.9 |

(−409.3 is the pooled arithmetic, matching the parallel line's −411.1 to the round-0 convention; the
per-game paired form is −402.6, with the two strata at −0.547 ± 0.145 and −1.144 ± 0.116.)

**map3 is the only map where we are not behind at matched order in either stratum — and it is the only
map we win.** And the map1-versus-map2 question that Result 1 set out to answer decomposes as
**92% order advantage, 8% matched-order collection** (net difference −257.9 = −236.6 order +
−21.3 collection): map1 and map2 have essentially the same matched-order collection deficit while f
differs by 11.5 pp. So Result 1's *comparative* localization survives, while its *absolute* framing
does not — the map1 matched-order deficit of −409 gold/game is larger than the entire −286 net.

**What cannot be computed, so nobody tries again:**

1. **`f18064c`'s hit rate against any of the 29 passive-corpus teams.** It has never played one. **0
   shared games.** No stratification repairs a zero-overlap splice.
2. **Matched action order against Ausdroid.** Ausdroid moves first in **9 of 2,994** usable rounds
   (18 unit-rounds). The both-first paired contrast is −0.59 ± 1.48 (**−0.40σ**, n=5 games). "At
   f≈1 we win by +86" is supportable; "action order held fixed" is not.
3. **A counterparty-free percentile for `f18064c`.** That needs `f18064c` games against a *sample* of
   the field. The corpus has exactly one mid-field opponent (Ausdroid, 6 games) — a point, not a
   distribution. The percentiles in §4 are reported *with* the confound, not net of it.
4. **Order-sensitivity ratio for 18 of the 29 field teams** (< 200 unit-rounds in one stratum).
5. **The public slot's build name.** The log records `player220`, not a build. The latency
   fingerprint proves what it is *not*.
6. **`f18064c` versus the field on map2 or map3.** No `f18064c` game exists against any non-tracked
   opponent on those maps.
7. **Opponent trajectory quantities in the passive corpus** (position 26%, actions 37%, pickup 37%,
   all fog-truncated).

## 8. Gross collection versus burn — reconciling the two live conclusions

Same channel, decomposed into positive deltas (gross collection) and negative deltas (burn):

| matchup | build | n | gross/game ours − theirs | burn/game ours − theirs | net from channel | observed net |
|---|---|---:|---:|---:|---:|---:|
| vs Ausdroid | `adf1` (`f18064c`) | 6 | **−54.2 ± 58.5 (−0.93σ)** | **−140.0 ± 69.8 (−2.01σ)** | +85.8 ± 64.5 | **+85.8** |
| vs T-1+Tundra | `f18064c` | 30 | **−299.0 ± 47.5 (−6.29σ)** | −12.9 ± 18.7 (−0.69σ) | −286.1 ± 51.7 | **−286.1** |

Channel closes to **0.0 gold** on both — a fifth independent validation. Against the two strongest,
**104% of the net deficit is gross collection**. Against Ausdroid gross collection is **still
negative** and the win is carried entirely by burning 3x less. So the burn decomposition does cut
against the "harvest is fine" reading, and it does so while we hold a 99.7% first-mover rate — the
single most favourable order condition in the whole archive, worth 2.38x on our own income.

## 9. Survives / survives-with-scope / refuted

| claim | verdict | scope |
|---|---|---|
| **Result 1: the identity and `A = +1.244 > 0`** | **SURVIVES** | Arithmetically exact, residual 3.4e-13, replicates on all 7 disjoint splits. `f18064c`, 30 map1 games. |
| **Result 1: "`A > 0` means round-for-round `f18064c` out-collects T-1 and Tundra"** | **REFUTED as a collection-quality claim** | `A` compares our first-mover rounds to their second-mover rounds. At matched order we are behind in 5 of 6 map x stratum cells: −13.9 and −18.2 pp of hit on map1 (−3.77σ, −9.87σ). |
| **Result 1: "map1's deficit is the dispatch race, not collection"** | **SURVIVES-WITH-SCOPE** | Survives as a *comparative* claim: the map1-vs-map2 net gap is 92% order advantage, 8% matched-order collection. Refuted as an *absolute* claim: map1's matched-order collection deficit is −409 gold/game, larger than the −286 net. Consistent with the parallel line's withdrawal in `231b657`. |
| **Result 2: field q1 35.9 / median 42.3 / q3 45.1** | **SURVIVES** | Reproduced bit-identically. 29 teams, 73 map1 passive games, challenger side. |
| **Result 2: "ours 36.0% = 28th percentile, below-median debt"** | **REFUTED as stated** | The 36.0% is `f18064c` vs the two strongest (12 games); the distribution is the field vs our public slot (73 games); **0 shared games**. The same statistic puts our obsolete slot at the 90th percentile in those same games. |
| **Result 2: "collection quality is a general deficit, not a T-1-specific one"** | **SURVIVES-WITH-SCOPE** | Correct conclusion, invalid evidence. Supported instead by: gross collection −54.2/game vs Ausdroid at f≈1, and −299.0/game (−6.29σ) vs the two strongest at matched order. |
| **Parallel line: "harvest is not a hard deficit vs mid-table once action order is held fixed"** | **REFUTED as stated — and already self-retracted in `a89bcfb`; I confirm the retraction independently** | Action order **cannot** be held fixed against Ausdroid: 9 of 2,994 rounds, 18 unit-rounds, both-first paired contrast −0.59 ± 1.48 (**−0.40σ**, n=5 games). What is measured is "at f≈1 the net is +85.8, 4W/2L, n=6, 1.33σ". Gross collection there is **−54.2 ± 58.5** — negative point estimate, undecidable — not "not a deficit". The win is carried by burn (−140.0, −2.01σ). |

**The unified statement.** At matched action order, `f18064c`'s map1 collection quality is **below the
field, not competitive with it**: 21st percentile in the first-mover stratum and 10th in the
second-mover stratum as measured, 9th percentile once standardised to our own order mix and 0th at
the field's. **However, no clean percentile exists** — every one of those figures compares us against
the two strongest teams while comparing the field against our slowest defended build, and the corpus
contains no overlap with which to remove that. The percentile the data does support without a
counterparty confound is the *order-sensitivity* one: `f18064c` is at roughly the **91st percentile
of order sensitivity** (2.38x versus a field median of 1.52x, 10 of 11 usable teams below), which is
the same fact seen from the only angle that is internally comparable.

## 10. Recommendations (for files I do not own)

* `src/INFRA.md` / `src/CHANGELOG.md`: record that per-unit-hit-rate levels are **not** comparable
  across corpora — the counterparty slope is −0.12 to −0.32 — and that the exchangeable quantity is
  the *within-game paired* difference or the order-sensitivity ratio.
* `src/player.cpp`: the mainline target implied here is the **second-mover branch**, not the tail
  latency. Our order-sensitivity ratio 2.38x versus the field's 1.52x says our second-mover
  behaviour is unusually bad in relative terms; closing to the field's 1.52x at fixed f = 0.568 is
  worth roughly **+0.60 gold/round = +300 gold/game** on map1, comparable to the entire deficit.
* `AGENT.md` 军规 candidate: *a percentile is only a measurement if the two sides of it share a
  corpus. Reproduce the distribution AND the point in one driver, or report neither.*

## 11. Reproduce (all read-only, 0 platform games)

```sh
python3 sim/analyze_caliber_alignment.py census                                  # (b)
python3 sim/analyze_caliber_alignment.py definition --index sim/reports/caliber_passive_index.json
python3 sim/analyze_caliber_alignment.py stratify   --index sim/reports/caliber_passive_index.json
python3 sim/analyze_caliber_alignment.py coupling                                # §5
python3 sim/analyze_caliber_alignment.py identity                                # §7
python3 sim/analyze_caliber_alignment.py report     --index sim/reports/caliber_passive_index.json \
    > sim/reports/caliber_alignment.json
```

Read-only imports of `sim/analyze_gold_delta.py` and `sim/analyze_map1_lesion.py`, so Result 1 is
re-verified with its author's own code. `sim/OPPONENTS.md`'s pooled burst statistics are a ~100-build
mixture and are used nowhere in this report.
