# Suppression as a tie-breaker: zero-quota open-loop envelope and verdict

> **Verdict: negative. Pre-registered gate "after-discount < 100 gold => do not build" fires.**
> After-discount margin **−3.46 gold/game** for the only form with a usable firing rate, **+0.06
> gold/game** for the only form that is genuinely free, and **+13.4 gold/game** for a deliberately
> over-generous ceiling. Nothing was built.
>
> Baseline `f18064c`, `src/player.cpp` sha256
> `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`, read with
> `git show f18064c:src/player.cpp` and verified. Repo HEAD `fd47ea6`. **Nothing under `src/` was
> written.** **Zero platform games consumed** — every behavioural number comes from the local engine,
> every opponent number from archived logs.
>
> Driver: `sim/suppression_envelope.py`. Machine-readable companion:
> `sim/reports/suppression_envelope.json`.

---

## 0. Answer first

| question | answer |
|---|---|
| 1. tie frequency | **26.08% of rounds** tie in the selector's value dimension; **7.79% of rounds** tie at the minimal ring; only **0.559% of unit-rounds** tie in *both* value and ring, and that is the only free form |
| 2. discordance | **30.5–34.2%** (value ties), **32.7–37.2%** (ring ties), **26.3–41.1%** (ring+amount ties) |
| 3. measured suppression slope | **−0.690 ± 0.178 gold/unit-round (−3.89σ)** from the exogenous `probeobs` control; **decidable**, and it survives a reverse-causality control. The endogenous read is −1.694 ± 0.213 and on frozen-latency games is *unidentified* (the far bucket is empty) |
| 4. sensor cost | **+25 x86-64 instructions** for both units, marginal inline, against a codegen noise floor of 7. Priced at **0 gold** under the 42-instruction credit; **−69.1 gold** if that credit is not available |
| raw envelope | **−0.02 to +1.56 gold/game** across enemy fields for the usable forms; **+44.6 (89.3 with two-round persistence)** for the over-generous ceiling |
| after 85% discount | **−8.37 to +0.06 gold/game**; **+13.4** for the over-generous ceiling |
| gate | **< 100 => do not build, judge negative** |

**One-line mechanism.** The `rm` within-ring tie-break order is *arbitrary*, yet re-ordering it
purposefully toward enemies **loses collection**. An arbitrary order beating a purposeful one is only
possible because "nearer the enemy" is correlated with "further from the central generation peak", so
**the arbitrary order is accidentally peak-preserving.**

---

## 1. A structural fact about our own build that was not written down: the selector compares no values

`f18064c`'s target selector (`src/player.cpp:453-528` at that commit) is **value-blind above a
threshold.** The scan marks every window cell with `grid > 2`
(`_mm256_cmpgt_epi32(vrow, v2s)` with `v2s = 2`), and `TT.bestrow` then returns the survivor with the
smallest `(prio[widx], widx)` where `prio` is the inverse of the L1-ring reorder table
`rm = {7,11,13,17, 2,6,8,10,14,16,18,22, 1,3,5,9,15,19,21,23, 0,4,20,24, 12}`. Decoding `rm`:

| rank | window offsets `(dr,dc)` | L1 ring |
|---|---|---:|
| 0–3 | (−1,0) (0,−1) (0,+1) (+1,0) | 1 |
| 4–11 | (−2,0) (−1,−1) (−1,+1) (0,−2) (0,+2) (+1,−1) (+1,+1) (+2,0) | 2 |
| 12–19 | (−2,±1) (−1,±2) (+1,±2) (+2,±1) | 3 |
| 20–23 | (±2,±2) | 4 |
| 24 | (0,0) — the unit's own cell, deliberately last | 0 |

So the ordering is **criterion 1 = L1 ring distance, criterion 2 = a fixed arbitrary position order
inside the ring.** There is no comparison of gold amounts anywhere. **This invalidates the phrase
"tie for maximum value"** as a description of this selector and forces three nested definitions,
which are **not interchangeable** — a reader given only one of them would misprice this candidate by
up to 30×:

| definition | meaning | free in value? | free in travel time? |
|---|---|---|---|
| **value** | `>= 2` candidates anywhere in the window; every candidate is equal in the selector's own value dimension | no (mean amount delta **−0.457**) | no (mean target ring delta **+0.79**) |
| **ring** | `>= 2` candidates at the minimal ring; the tie survives to the arbitrary `rm` order | no (mean amount delta **−0.283**) | **yes** (ring delta exactly 0.00) |
| **ring + amount** | `>= 2` candidates at the minimal ring *and* with equal gold | **yes** (0.00) | **yes** (0.00) |

Only the third form matches the brief's premise "by construction this costs zero collection".

---

## 2. Instrument and its validation

`sim/suppression_envelope.py` runs the frozen construct at seat 1 behind a **pass-through shim**: the
shim returns the base decision verbatim, so the game it observes is exactly the game `f18064c` plays,
and it reads the seat's **own fog-filtered `PlayerInput`** rather than the log's full grid. That
distinction matters twice: the log's `full_grid` overlays actors as `-2`, hiding gold under a body,
while the real `GameInput` is pure ground; and fog is a **no-op for this selector**, because the 5x5
window is always inside vision radius 2.

**Replica validation: 8133 / 8133 = 100.0% agreement.** On every unit-round with a wall-free and
bomb-free 3-step LUT path and `k == 3`, the emitted action triple is fully determined by the pick, so
the live `.so`'s own output confirms the Python replica. Bomb memory is replicated including the
20-round `waveTick` clear, so rich units are inside the validated subset too. The first draft
disagreed on 64/223 and the cause was real: the replica had missed `SLut`'s **pre-folded
early-arrival** step (`fact[d] = fact[d-1] ^ 1` for `0 < d < 3`, plus `fact[2] = fact[1] ^ 1` when
`d == 1`). Fixing that took agreement to 1.000.

**Build note.** The frozen source calls `_mm_prefetch` unconditionally (`f18064c` line 351) while
`<immintrin.h>` is only included under `__AVX2__`. On this aarch64 host the hint is stubbed out with
`-D'_mm_prefetch(a,b)=((void)0)'`; a prefetch hint cannot change behaviour, and the scan then
compiles to the source's own documented scalar reference path. `SctT::colv` was verified analytically
to be equivalent to the scalar path's bounds checks for the only interesting columns
(`sc = 0, 1, 15, 16`, where the AVX load base is clamped and lanes spill into the next row) — the
spilled lanes are always masked out.

**Conditions on every behavioural number below**: construct `f18064c`; 24 games = `map1,map2,map3`
x seeds `1001..1004` x both order conditions (`fixed` dispatch at costs `(200,201)` = we-first and
`(201,200)` = we-second); opponent = self-play, second seat the same frozen construct; steady window
`r >= 8` of 500; **23,616 unit-rounds / 11,808 rounds**; `.so` sha256 `3980ce30c20aa30c…`.

---

## 3. Quantity 1 — tie frequency

| | per round | per unit-round |
|---|---:|---:|
| a target exists at all | — | **48.17%** [47.53, 48.80] |
| **value tie** | **26.08%** [25.30, 26.88] | 15.66% [15.20, 16.12] |
| **ring tie** | **7.79%** [7.32, 8.29] | 4.11% [3.87, 4.37] |
| **ring + amount tie** | — | **0.559%** [0.472, 0.662] |

Ties as a share of unit-rounds *that have a target*: value **32.50%**, ring **8.54%**, ring+amount
**1.16%**. Candidate-count histogram over all unit-rounds: 0 -> 12241, 1 -> 7678, 2 -> 2798,
3 -> 744, 4 -> 126, 5 -> 26, 6 -> 3. Chosen-target ring histogram: ring1 2900, ring2 4024, ring3
3078, ring4 1173, ring0 200.

Stable across conditions, so the cap is not an artefact of one cell:

| cell | value tie / round | ring tie / round | ring+amount / unit-round |
|---|---:|---:|---:|
| we-first | 25.78% | 7.40% | — |
| we-second | 26.39% | 8.18% | — |
| map1 | 24.57% | 7.34% | 0.534% |
| map2 | 22.76% | 6.43% | 0.521% |
| map3 | 30.92% | 9.60% | 0.622% |

**Reading.** Against a 5%-tie-frequency cap, the value and ring definitions pass comfortably and the
free definition fails by an order of magnitude: **0.559% of unit-rounds tie freely, and after
requiring a visible enemy and discordance it fires 0.9–2.1 times per game.** A rule that fires twice
per game cannot produce a measurable suppression credit.

---

## 4. Quantity 2 — discordance, and the firing rate it implies

"Nearer a visible enemy" is applied as a **strict refinement**: minimise Chebyshev distance from the
candidate cell to the nearest visible enemy, keeping the live `rm` order as the residual tie-break so
no new arbitrariness is introduced. Two enemy fields are reported, because self-play's enemy is a
copy of ourselves and therefore camps the same two anchors:

* **self-play field** — the real opponent in the replay. Visible-enemy rate **93.65%** of unit-rounds.
* **T-1-calibrated field** — two T-1 unit positions drawn from the 5-game `probeobs` occupancy sample
  (2998 observations, mean centre-ring 5.31, visible-subset centre `d<=4` share 0.481 against the
  strict bound `[0.517, 0.726]` from `sim/reports/t1_spatial_policy.json`), then passed through our
  real Chebyshev-2 vision rule. Visible-enemy rate falls to **65.85%**, close to the ~57% historical
  rate. **Bias direction: the visible subset over-represents the centre (detection probability falls
  monotonically from 0.828 at `d=0` to 0.150 at `d=8`), so this field over-states co-location and
  therefore over-states the firing rate — anti-conservative, i.e. it flatters the candidate.**

| definition | discordance (self-play) | discordance (T-1 field) | firing rate / unit-round (self-play) | (T-1 field) |
|---|---:|---:|---:|---:|
| value | **30.55%** [29.03, 32.11] | **34.19%** [32.32, 36.11] | 4.429% | 3.485% |
| ring | **37.17%** [34.06, 40.38] | **32.70%** [29.15, 36.46] | 1.410% | 0.872% |
| ring + amount | **41.13%** [32.86, 49.93] | **26.25%** [17.86, 36.82] | 0.216% | 0.089% |

Where the two criteria agree the change is a no-op, so roughly two thirds of ties contribute nothing
even before pricing.

What a firing buys and costs, per firing (self-play / T-1 field), ring form:

| quantity | self-play | T-1 field |
|---|---:|---:|
| mean Chebyshev gain toward the nearest enemy | 1.495 | 1.714 |
| share of firings that newly reach `d <= 1` | 167/333 = 50.2% | 88/206 = 42.7% |
| mean target **ring** delta | 0.000 | 0.000 |
| mean target **gold amount** delta | **−0.283** | **−0.461** |
| mean target **centre-ring** delta | **−0.99** | **−0.86** |

The last two rows are the candidate's real cost and are the reason it fails: the enemy-nearer cell
carries less gold, and the negative centre-ring delta shows the mechanism — enemies are on average
found *outward* of us, so "toward the enemy" partially means "off the peak" even when the ring
distance is held fixed.

---

## 5. Quantity 3 — the measured suppression slope, exogenous and **decidable**

**Corpus.** The 5 T-1 `probeobs` games `172219 / 171747 / 172186 / 171719 / 172187` (map1, T-1 =
`player163`, T-1 at seat 2). `sim/OPPONENTS.md §1` fixes *four* probe games, but two of those are
Tundra; the T-1-specific probe set is five, per the same file's ERRATA and
`sim/reports/t1_spatial_policy.md`. Both sets are measured and both are reported.

**Why this is the exogenous control.** `probeobs` **does not use gold as a movement objective** — it
moves only to maintain observation. Its proximity to T-1 is therefore driven by tracking, not by gold
richness, which is exactly the confound that makes the naive read useless. It also barely collects
(115–152 gold over 500 rounds against T-1's ~1400), so the measured effect is **pure body-blocking,
not depletion** — and that matters, because depletion is already counted as *our* income and
crediting it again would double-count.

**Channel.** Per-unit `gold` differencing, recorded in 100% of unit-observations; negative deltas
clipped to zero because a drop is a bomb burn, not income. Window `r >= 20`.
**Position coverage 62.46%**, and the mandated bias check:
**`bias_ratio` = visible-subset mean / full-channel mean = 0.9755** (2.697 against 2.765), i.e. near
unbiased — against 0.7708 on the ordinary corpus, which is why the ordinary corpus cannot carry this.

| Chebyshev distance from T-1's unit to our nearest unit | n | gold/unit-round | scoring-round rate |
|---:|---:|---:|---:|
| 1 | 514 | 2.300 | 0.5486 |
| 2 | 873 | 2.392 | 0.5498 |
| 3 | 756 | 2.611 | 0.5437 |
| 4 | 855 | 3.324 | 0.6561 |

| estimate | value |
|---|---|
| **gold slope, `d<=1` minus `d>=3`** | **−0.690 ± 0.178 (−3.89σ)** |
| **count-caliber slope (scoring-round rate)** | **−5.47pp ± 2.51pp (−2.18σ)** |
| own-displacement-stratified slope (reverse-causality control) | **−0.907** pooled; dominant stratum (displacement 2, n = 454 / 1103) **−0.850 ± 0.200** |
| the 2-game `OPPONENTS.md` probe subset alone | −0.436 ± 0.297 (**−1.47σ**, not significant) |
| endogenous, ordinary corpus, our P50 > 260 ns | −1.694 ± 0.213 |
| endogenous, ordinary corpus, our P50 <= 260 ns | **unidentified — the `d>=3` bucket is empty (n = 0)** |

Three things about this table are worth carrying.

1. **The obvious confound is controlled, and it was pushing the wrong way.** "T-1 idles when there is
   nothing to collect, and a tracking probe catches up while it idles" would manufacture a spurious
   negative slope. Conditioning on T-1's own net displacement makes the slope **larger** in
   magnitude, not smaller, so that channel is not driving the result.
2. **The endogenous read is not merely inflated, it is unidentified.** On the T-1 games where our own
   latency was <= 260 ns there is **no** `d>=3` observation at all: when we camp the centre, a T-1 that
   is visible to us is always within Chebyshev 2. Any "slope" from that corpus would be an artefact
   of the vision geometry. Had we substituted −1.694, we would have priced the candidate at 2.5x the
   truth and might have built it.
3. **Distances beyond 4 are structurally unobservable** in the probe corpus, because the probe buys
   the 9x9 view (radius 4). The closing account says the unobserved rounds are *richer*
   (implied ~2.878 against 2.697 observed), so the true far-field income is higher and the slope
   magnitude reported here is, if anything, **conservative in the candidate's favour**.

**Order condition, stated because it is not ours.** `probeobs` runs at 0.80–0.86 ms, so in the probe
corpus **T-1 moves first in essentially every round**, whereas against our 204 ns construct T-1 moves
second in 85–96% of rounds. Blocking is possible in both conditions — the blocker's body occupies
cells either way — but in the probe condition our positions are one round stale when T-1 moves.
Direction: this makes the probe slope an **under**-estimate of what a first-moving blocker achieves,
again conservative in the candidate's favour. It does not rescue the arithmetic.

---

## 6. Quantity 4 — cost of reading the sensor

Our build reads **none** of `visible_enemies`, `snapshot`, `visible_npcs`, `gold_opp`. Two
independent measurements, both x86-64 (`-march=x86-64-v3`, the platform ISA; arm64 reported only as a
host cross-check):

**(a) marginal inline cost in the real function.** The read is spliced into the frozen construct's own
per-unit loop behind a `volatile` sink and the static instruction count of `_moveDecision` is
differenced against a sink-only arm. Three no-sensor control arms bound the codegen noise floor at
**7 instructions**.

| arm | `_moveDecision` instructions | marginal for **both** units |
|---|---:|---:|
| `base` (unmodified frozen construct) | 647 | — |
| sink only (`sr`, `sc`) | 650 | 0 (reference) |
| sink control (`my_units_gold[u]`) | 650 | 0 |
| sink control (`round`) | 643 | −7 |
| **slot-0 displacement read** | 644 | **−6 => free to within noise** |
| **nearest of both `visible_enemies` slots** | 675 | **+25** |

**(b) standalone leaf** (upper bound; pays argument loads the inline form does not): raw coordinate
pair 2, slot-0 displacement 8, best-effort nearest-of-two 39, first-draft branch-free nearest 53.

**Pricing.** At 0.1454 ns/instruction and −19 gold/ns, +25 instructions is 3.64 ns = **−69.1 gold**.
An independent line has since measured the accepted stack to be **instruction-negative by −42.54
instructions / −4.81 cycles** against the shipped construct, so a +25 addition still leaves total
instructions below what we already ship and the correct charge is **0 gold**; the envelope below is
computed at 0. That credit is reported here as received from the orchestrator, not re-measured by this
line — **which is why the verdict is stated so that it does not depend on it**: at the full −69.1 gold
charge the candidate is merely more negative. No layout tax is priced: `.rodata` growth was proven not
to move the entry (448 dummy bytes), and entry alignment is separately controllable via the 96-byte
`asm(".space 96, 0x90")` pad.

**Re-derived flip exposure for the frozen construct alone.** T-1's per-round cost is *their* property
and is recorded in 100% of rounds, so it can be held while substituting our current 204 ns. Over
**74,445** steady T-1 cost-rounds (their P50 = 200 ns): λ(+6..+15 ns) = **10.217%**, λ(+20) = 18.34%,
λ(+30) = 24.51%, and **λ(+1..+5 ns) = 0 exactly** because their cost is quantised to 10 ns. The 10.2%
is close to the brief's 9.274%. The quantisation is *not* used to argue the cost away — the
pre-registered −19 gold/ns average price is what prices here.

---

## 7. The envelope

**Construction.** For every firing the driver records the pair (Chebyshev distance from the live
target cell to the nearest visible enemy -> the same for the alternative cell) and prices it with the
**measured income-versus-distance curve** from section 5, rather than with a single near-versus-far
slope. Our own collection change is the realised amount difference on the cell we walk to. The
mandated 85% discount is applied **to the suppression credit only**; our collection loss is a
realised amount difference on the cell we actually enter, not a re-harvest of our own stock, so
discounting it would understate the cost. Per `sim/reports/path_harvest_verdict.md`'s own scope
warning, the "raw" column is also the reading you get if you take the contention argument that gold
lost to a race is 100% novel.

Gold per game (1000 unit-rounds; 984 steady):

| form | enemy field | firings/game | opponent gold removed | our gold Δ | **margin raw** | **margin after 85%** |
|---|---|---:|---:|---:|---:|---:|
| ring | T-1-calibrated | 8.58 | +3.30 | −3.96 | **−0.65** | **−3.46** |
| ring | self-play | 13.88 | +3.89 | −3.92 | −0.02 | −3.33 |
| ring + amount (**the only free form**) | self-play | 2.12 | +0.60 | 0.00 | **+0.60** | **+0.09** |
| ring + amount | T-1-calibrated | 0.88 | +0.40 | 0.00 | +0.40 | **+0.06** |
| value | T-1-calibrated | 34.29 | +11.69 | −10.12 | +1.56 | −8.37 |
| value | self-play | 43.58 | +12.34 | −19.92 | −7.58 | −18.07 |

**Break-even, stated per firing.** Credit per firing is **0.280** gold (self-play) and **0.385**
(T-1 field); cost per firing is **0.283** and **0.461**. The suppression credit per firing is
**statistically indistinguishable from the collection cost per firing**, which is why every row above
sits within a few gold of zero.

**Most generous construction that is still arithmetic rather than wishing.** Take the largest firing
rate measured (43.58/game, value definition), give **every** firing the largest transition the curve
allows (`income(4) − income(1)` = 1.024 gold), ignore our collection loss entirely and ignore
latency:

| | raw | after 85% |
|---|---:|---:|
| one-round credit | **44.6** | 6.7 |
| two-round adjacency persistence | **89.3** | 13.4 |
| using the stratified slope 0.907 instead | 39.5 | 5.9 |

**Even this does not reach the 100-gold gate.** That is what makes the negative robust rather than
marginal: there is no plausible re-parameterisation inside the measured envelope that reaches +100,
let alone the +128/+162 needed to draw with T-1 or the +166 equal-map-weighted requirement.

### 7.1 Opponent scoring-round **count**, the comparable caliber

Baseline from the same 24 games: opponent **228.0** scoring unit-rounds/game, ours **234.8**.

| arm | opponent scoring rounds removed | share | judgment |
|---|---:|---:|---|
| `snakeu` (recorded anchor, `src/CHANGELOG.md:1177`) | −130 (660 -> 530) | **−20%** | sufficient (~−611 gold) |
| hot-field table knife (`sim/reports/hotfield_table_knife.md:520`) | −6.45 ± 1.68 (−3.84σ) | **−2.5%** | insufficient (margin −4.3 ± 16.1) |
| **this candidate, ring form** | **−0.26 to −0.31** | **−0.11% to −0.14%** | **~20x smaller than the already-insufficient arm** |
| this candidate, value form | −0.87 to −1.06 | −0.38% to −0.46% | ~6x smaller |

**Our own scoring-round Δ is exactly 0 for the ring form** — both cells are `grid > 2` candidates at
the same ring, so a pickup still happens on the same step budget. That is a trap worth recording:
**for this candidate the count caliber alone reads "free", and it is not.** The entire cost lands in
the gold caliber. (For the *value* form the count caliber is not even safe in principle: the
alternative sits +0.79 rings further out, and ring-4 targets — 10.3% of all targets — are
unreachable inside a 3-step budget, so that form can also reduce our own scoring-round count. That
un-priced extra cost makes the value row worse than tabulated, not better.)

### 7.2 Per map, T-1-calibrated field, ring form

| map | opponent gold removed | our gold Δ | margin raw | after 85% | gold needed for 50% vs T-1 | fraction reached |
|---|---:|---:|---:|---:|---:|---:|
| map1 | +2.68 | −8.12 | −5.44 | −7.72 | +200 | negative |
| **map2** | +2.74 | **+5.75** | **+8.49** | **+6.16** | +86 | **7.2%** |
| map3 | +4.48 | −9.50 | −5.02 | −8.83 | +127 | negative |

map2 is the only positive cell, and its sign comes from the amount delta being a coin flip across
maps (−8.12 / +5.75 / −9.50), not from suppression, whose sign is stable and small. Since the
win-rate curve is convex, +6.16 gold on map2 buys essentially nothing.

**Three-way classification: pie-shrinking.** Both sides' collection falls (ours −3.96, theirs −3.30),
total gold collected goes down, and the margin is ~0. It is neither a joint move nor ceding; it is
mutual value destruction at a scale too small to matter.

---

## 8. The third candidate killed by one mechanism

| candidate | proposed because | mechanism of death | result |
|---|---|---|---|
| `fold_tour` | the fold "wastes" two of three steps | a 3-distinct-cell tour cannot end on its origin (odd parity), so it drifts off the central generation peak | **−81.4 ± 18.5 (−4.39σ)** |
| teammate-spread tie-break (this round) | zero new input cost; the field's strongest team differentiates its units | target **centre-ring delta +1.61**, amount delta +0.32, fires 1.74% of unit-rounds | predicted dead, **confirmed by construction** — do not pursue |
| enemy-proximity tie-break (this round) | suppression is worth as much as earning, and ties make it free | value form pushes the target **+0.79 rings** outward; ring form pays **−0.283 to −0.461 gold** per firing with centre-ring delta −0.99 to −0.86 | **margin −3.46 after discount** |

**Three independent candidates, proposed for three different reasons, all die by the same mechanism:
any rule that moves a unit away from the central generation peak loses more than the rule buys.** The
generation gradient is why: the measured field is **0.0335 per cell-round at rings 0–2, 0.0250 at ring
3, 0.0190 at 4, 0.0100 at 5 and 0.0040 at ring 6** — an **eightfold** fall
(`sim/reports/hotfield_table_knife.md:184`, from `src/CHANGELOG.md:339-341`).

**Independent corroboration that the instrument is measuring the right construct.** This replay puts
the frozen construct's mean unit L1 ring at **3.516 on map1** (3.045 pooled over three maps), against
the independently recorded **3.473** for the champion's camp
(`sim/reports/hotfield_table_knife.md`). Two different harnesses, agreement to 0.04 rings.

**And the same report already recorded the pie-shrinking signature from the other direction**: the
hot-field table knife suppressed the opponent by −6.45 ± 1.68 scoring rounds (−3.84σ) but **our own
fell too**, −4.87 ± 1.55 — whereas `snakeu`, which bought its suppression by leaving the centre,
raised its own by ×3.3 while paying −40% of its income. This candidate reproduces the knife's
signature at one twentieth of the dose.

And the sharpest form of it, visible only in this round's data: **the `rm` within-ring tie-break order
is arbitrary, yet re-ordering it purposefully toward enemies loses collection. An arbitrary order can
only beat a purposeful one because "nearer the enemy" is correlated with "further from the peak" — so
the arbitrary order is accidentally peak-preserving.**

This also does *not* contradict the brief's premise that suppression is worth as much as earning.
Suppression **is** worth as much as earning: the exogenous slope is a real −0.690 gold/unit-round at
Chebyshev 1, significant at −3.89σ. The candidate fails on **frequency and on price**, not on
mechanism. The forms that fire often enough are not free, and the form that is free fires twice a
game.

---

## 9. Zero-signal dry run (mandatory, no exemptions)

`python3 sim/suppression_envelope.py dryrun`, RNG seed 20260810, deterministic.

| test | input with known-zero signal | expected | measured | verdict |
|---|---|---|---|---|
| **A** tie detector | gold on a 5-spaced lattice, so no 5x5 window can hold two candidates; all 289 unit positions | 0 ties | **0 value ties / 289 positions** | **PASS — reports nothing** |
| **B** tie detector, positive control | every cell gold | 225 ties, pick always `rm[0]` = widx 7 | **225 / 225, pick histogram = {7: 225}** | **PASS — detects the planted tie and picks the documented cell** |
| **C** discordance, enemy independent of gold | random 25%-density gold, enemy placed uniformly at random | rule still relabels (it refines an arbitrary order) but must not out-perform a random relabel *in value* | rule mean Chebyshev gain **+0.726**, random relabel **−0.037** | **records the geometric truth: an enemy is approached even when it is placed at random, which is why the envelope must be priced by a measured slope and never by the firing rate alone** |
| **D** slope estimator | 20,000 synthetic unit-rounds, income drawn independently of distance | slope ≈ 0 | slope **+0.0248 ± 0.0805, +0.31σ** | **PASS — reports nothing (|σ| < 2)** |
| **E** slope estimator, planted effect | same, with −1.0 gold planted at `d <= 1` | recovers −1.0 | **−0.9968 ± 0.0800, −12.46σ** | **PASS — recovers the planted effect** |

Test C is the informative one: it establishes that firing frequency is **not** evidence of value,
because a tie-break toward a randomly placed enemy still "succeeds" geometrically. That is precisely
why sections 5 and 7 price the envelope with the exogenous slope rather than with the firing rate.

---

## 10. Bias direction of every estimate

| estimate | bias | direction relative to the verdict |
|---|---|---|
| tie frequency, discordance, amount delta | none — exact replay of the frozen construct, replica validated 8133/8133 | neutral |
| self-play enemy field | opponent is a copy of us and camps the same anchors; visible-enemy rate 93.6% against ~57% historical | **over-states** firing => flatters the candidate |
| T-1-calibrated enemy field | drawn from a fog-truncated visible subset that over-represents the centre (detection 0.828 at `d=0` -> 0.150 at `d=8`) | **over-states** co-location => flatters the candidate |
| probe slope, position coverage 62.46% | `bias_ratio` = **0.9755**, near unbiased; unobserved rounds are richer, so the far-field income is under-measured | **under-states** the slope magnitude => flatters the candidate |
| probe slope, order condition | probe is 4000x slower, so T-1 moves first; our positions are stale when it moves | **under-states** what a first-moving blocker achieves => flatters the candidate |
| probe slope, reverse causality | controlled by stratifying on T-1's own displacement; stratified slope is *larger* | neutral-to-flattering |
| our collection loss | measured exactly from the realised amount delta; not discounted | neutral |
| value-form reachability | ring-4 targets are unreachable in 3 steps, so the value form can also cost us scoring rounds; not priced | **under-states** the value form's cost => flatters the candidate |
| sensor cost | priced at 0 under the 42-instruction credit | flatters the candidate |

**Every material bias in this measurement flatters the candidate, and it still fails the gate by an
order of magnitude.**

---

## 11. Verdict against the pre-registered gates

| gate | threshold | measured | fires? |
|---|---|---:|---|
| after-discount < 100 gold | do not build, judge negative | **−3.46** (ring, T-1 field) / **+0.06** (free form) / **+13.4** (over-generous ceiling) | **YES** |
| after-discount >= 300 gold | worth building | — | no |
| in between | report with composition | — | no |
| tie frequency < 5% | cap too small | 26.08%/round value, 7.79%/round ring — **pass**; but **0.559%** of unit-rounds for the only free form | the free form fails |
| sensor read > 25 instructions | cost eats most of it | **+25**, priced at 0 under the 42-instruction credit — **pass** | no |

**DO NOT BUILD.** The candidate is not capped by physics — the suppression channel is real and
`snakeu` demonstrated −20% opponent scoring rounds — and it is no longer capped by cost. It is capped
by the joint requirement that made it attractive in the first place: **the only form that is
genuinely free fires twice per game, and every form that fires often enough pays for itself in
collection at almost exactly the rate it earns in suppression.**

## 12. What would legitimately reopen this

Not a new enemy field, not a cheaper sensor, and not more seeds — the envelope is bounded above by
44.6 raw / 13.4 discounted under assumptions already too generous. Only one of these:

1. **A tie definition that is free and frequent.** If a future selector compares gold amounts (it
   currently does not), "equal value" would become a genuine equivalence class and the free tie rate
   could rise well above 0.559%. That is a consequence of a *different* selector, not of this one.
2. **A suppression mechanism that does not move our unit.** The whole cost here is that the
   enemy-nearer cell is a worse cell. A mechanism that suppresses without relocating us — for
   instance exploiting the dispatch order rather than geometry — is not bounded by this measurement.
3. **A rule change** that flattens the central generation gradient, which is what makes every
   off-peak movement lose.

## Reproduction

```bash
git show f18064c:src/player.cpp | shasum -a 256      # 0ecce6fc…84fdd
python3 sim/suppression_envelope.py dryrun --out /tmp/gr_suppr/dryrun.json
python3 sim/suppression_envelope.py ties   --maps map1,map2,map3 --seeds 1001,1002,1003,1004 \
                                           --out /tmp/gr_suppr/ties_full.json
python3 sim/suppression_envelope.py slope  --out /tmp/gr_suppr/slope.json
python3 sim/suppression_envelope.py icount --out /tmp/gr_suppr/icount.json
python3 sim/suppression_envelope.py report --out sim/reports/suppression_envelope.json
```

Inputs are read-only: `git show f18064c:src/player.cpp`, `sim/engine.py` unmodified, and the archived
logs. Outputs are `sim/reports/suppression_envelope.{md,json}` only.
