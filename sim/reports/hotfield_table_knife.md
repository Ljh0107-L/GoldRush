# Hot-field table-value rebake of `f18064c` — RULED NEGATIVE, mechanism identified

> Driver `sim/analyze_hotfield_table.py`; machine-readable companion `sim/reports/hotfield_table_knife.json`.
> Baseline `f18064c`, `src/player.cpp` sha256 `0ecce6fc…84fdd`, read with `git show f18064c:src/player.cpp`
> (the worktree `src/player.cpp` belongs to a parallel worker and was never built).
> All builds and all 1024 games ran on `quant-compiler` (`ssh Ubiquant220@8.153.76.120`,
> EPYC Zen4, GCC 14.3.1, `-std=c++17 -O3 -march=native -fPIC -shared`), in an isolated tree
> `~/gr_tblknife`. Zero platform quota spent. No `git` writes. `sim/reports/step_budget_positioning.md`
> and `/tmp/gr_step` belong to the sibling worker `redflag-0d82` and were not touched;
> `sim/scenario.py` was never modified — the alternative field model is a per-process monkeypatch.

---

## 0. Why this was not the seventh failed transplant — and it still lost

**The two distinctions held up, and both were measured rather than argued.**

1. **The position tax that killed the six is structurally absent, and now proven absent.** `.text` is
   **byte-identical** (sha256 equal, 5363 bytes) for every one of the seven arms; `moveDecision`
   sits at exactly `0x1990`, `mod64 = 0x10`, in every arm; the static instruction count of
   `moveDecision` is 704 in every arm. Only `.rodata` differs, by 8–16 bytes for the `colv` arms and
   230–258 bytes for the `rm` arms, with `.rodata`'s **size** unchanged at 1352 bytes. There is no
   mechanism by which any of these arms can pay the +80–110 ns the hot-track transplant paid.
2. **The intervention was not bound by the selector ceiling, and it did move the unit.** The
   rate-matched hot-minus-anti contrast on the mean end-of-round L1 ring is
   **−0.2601 ± 0.0157 (−16.6σ)**: the hot arm genuinely pulls the units 0.26 rings closer to the
   generation peak than its own control does. The gait change happened.

**And the income did not follow.** The same paired contrast on margin is **−4.3 ± 16.1 (−0.27σ)**.
Moving the champion 0.26 rings inward is worth zero.

**Ruling: NEGATIVE — do not land any arm.** Nothing in the pool reaches the repo's +150 gate; the
only arm with a positive pooled sign (`hot_colv_edge`, +17.1 ± 13.7) is capped by its own firing rate
at roughly +30 gold/game and fails out-of-sample in the field model that matters. The negative is
*clean* in the way the 8.9 platform experiments could not be: latency, alignment and first-mover rate
are all held fixed by construction, so what was measured is the mechanism itself.

**What the negative buys, stated as a positive result.** The standing explanation — "the champion
already sits at the hot-field optimum" — is now quantified rather than asserted. The champion's mean
unit L1 ring is **3.473**. Under the measured field, the window generation rate at 1/2.6 of the
champion's own is the rate at **ring 7**. The snake knife delivered ×2.6 income, so under the
hit-rate law the snake was camped at ring ≈7 and the knife moved it to ring ≈3.5. **That is exactly
where the champion already stands. The champion has already made the entire move the snake knife
made**, which is why six transplants added nothing, and why this one, which is genuinely free, also
adds nothing.

---

## 1. The table enumeration and the attribution — fixed before anything was built or run

Reproduce with `python3 sim/analyze_hotfield_table.py tables`.

### 1.1 First, a correction to the brief's premise

**The champion's target priority is by L1 (Manhattan) distance from the unit, not Chebyshev.** From
`rm` at `src/player.cpp:121-122`, ranks 0–3 are exactly the four L1=1 cells, ranks 4–11 the eight
L1=2 cells, 12–19 the eight L1=3 cells, 20–23 the four L1=4 corners, and the unit's own cell is
ranked **last**. Under Chebyshev the class sizes would be 4 then 20, not 4/8/8/4. Within an L1 class
the tie breaks to the lower window index, i.e. row-major, i.e. **up-rows first then left-to-right** —
a systematic up/left drift bias.

The measured field is also L1, about a different origin: `snakeh.cpp`'s `ORB4` computes
`|r-8| + |c-8|` and indexes `HB[ring]` with it, and `CHANGELOG:337` quotes rings 7–12 for the amount
profile, which cannot exist under Chebyshev (max 8 on a 17×17 board). So both quantities are L1;
the difference is only the origin.

### 1.2 The enumeration

Every constexpr table in `f18064c` that can reach movement. "Pure value" means it is read at run time
with a run-time index, so it lives in `.rodata` and its contents never enter the instruction stream —
verified by the freeness measurements in §4, not assumed.

| table (source line) | bytes | index space | pure value? | affects blind rounds? | affects visible selection? | carries ring-from-centre content? | rebaked here? |
|---|---:|---|---|---|---|---|---|
| `rm[26]` → `TT.bestrow[5][32]` (`:121-135`, read `:432`/`:455`) | 320 B (`bestrow`) | **relative**: window row × 5-bit column mask | yes | only indirectly — dropping all visible gold makes `blind=1` | **yes, this is the selector** | **no**, beyond L1 magnitude | yes, as a structural test |
| `TT.remap[26]` (`:116`,`:123`) | 26 B | n/a — **dead** | yes | no | no | no | no: **written by the constructor and never read**; the `pext` cascade it fed was retired (`:463`). A rebake here is provably a no-op. Listed so nobody mistakes it for the live priority table. |
| `SLut SL.fact/pdr/pdc[7][7][3]` (`:188-220`, read `:501-508`) | 441 B | **relative**: `(dr,dc)` clamped to `[-3,3]²` | yes | **yes** — it routes a blind unit to its anchor | yes | no; also `pdr/pdc` must track `fact` or the `pass01` pre-check desynchronises | no — `fold_tour` was exactly a zero-instruction rebake here and is the worst arm on record (−81.4 ± 18.5 OOS) |
| `ORT_A/ORT_R/ORT_C` (`:90-95`, read `:302-311`) | 40 B | absolute in time (rounds 0–3), position-gated | yes | **yes**, but map1 only, rounds 0–3 only, self-aborting on first sight of gold | no | yes in principle (route endpoint is an absolute cell) | no — the one geometrically available correction was measured 8.10 and judged negative both ways (−5.67 ± 25.91 and −18.67 ± 10.15) with 7/12 seeds bit-identical |
| **`SCT.colv[17]`** (`:98-111`, read `:429-433` as `rv = colv & rowok`) | 17 B | **ABSOLUTE: the unit's own column `sc`** | yes | **yes, by construction** — suppressing the only visible gold sets `blind=1` and sends the unit to its anchor on the peak | **yes** | **YES — the only absolutely-indexed movement table** | **yes — this is the candidate** |
| `SCT.cb[17]` / `SCT.lsh[17]` (`:98-108`, read `:417-433`) | 34 B | absolute (`sc`) | yes | n/a | n/a | n/a | no — `cb` is the 32-byte load base and `lsh` its matched bit realignment; changing one without the other misaligns the whole window. Inert as a policy knob. |
| `TT.rclv[21]` (`:119`, read `:419`) | 21 B | absolute (scan row + 2) | yes | n/a | n/a | n/a | no — must be the identity for in-range rows or the scan loads the wrong row and manufactures phantom gold; out-of-range entries are already masked by `rowok`, hence inert |
| `TT.d5[25]` / `TT.m5[25]` (`:120`, read `:477-478`) | 50 B | relative | yes | n/a | n/a | n/a | no — div/mod 5 turning the chosen window index back into a cell; a rebake aims the unit at a cell it never inspected |
| `BAKED_W[3][17]` (`:74-84`, read `:272`,`:283`) | 204 B | **absolute** (map × row) | yes | yes | yes | yes in principle — phantom walls in cold rings would be an absolute hot-field mask | no, deliberately: the same table is the fingerprint discriminator, so phantom entries break map identification, and the 8.10 mis-lock lesion prices **one** wrong wall cell at **−689 gold** |
| `ORB4[17][17]` (`snakeh.cpp`) | 1156 B | absolute (cell × direction) | yes | n/a for the champion | n/a | **yes** | **not present in `f18064c`** — recorded because its absence is the structural reason the champion has only one absolutely-indexed movement table to rebake |

### 1.3 The coordinate mapping decision, stated explicitly

The field is L1-from-centre (absolute); every champion priority table is L1-from-unit (relative). The
mapping decision is forced, and it is a *negative* result about the chassis:

> **A unit-relative table cannot carry a ring-from-centre gradient, because the inward direction
> flips sign with the unit's position.** The two anchors sit on opposite sides of the centre row —
> `(6,8)` is above it, `(11,8)` below — so one shared relative table cannot be inward for both. The
> only ring content a relative ordering *can* carry is its **L1 magnitude**, and minimum-L1-first is
> already the most centre-preserving relative ordering that exists. **`rm` is therefore already at
> the hot-field optimum of its own family, by construction rather than by luck.**

Hence the rebake had to go into `SCT.colv[17]`. Two consequences follow, and both are limitations of
the chassis rather than of the design:

- `colv` is a **5-bit mask**, so it can express **prohibition** but not bias. The snake's `ORB4`
  could hold a graded `{+3 … −2}` gradient; the champion's only absolute table cannot.
- Only the **column** axis has such a table. The row dimension is gated arithmetically by `rowok`,
  and its only absolute table (`TT.rclv`) is a correctness clamp. So the champion's free hot-field
  gradient is column-only, i.e. at most half of a radial field.
- **Contamination, named up front:** `colv` also gates bomb recording (`:433` reuses `rv`), so a
  suppressed column is also un-remembered for bombs. This is self-limiting — the unit no longer
  targets that column and therefore no longer routes through it — and per-seat burn is reported per
  arm in §5 to check it.

### 1.4 Which ceiling applies

`sim/reports/target_selection_closed.md` caps *same-round miss recovery* by any selector change at
26.5% (our-first) / 35.9% (our-second) of our misses, because 73.5%/64.1% of misses have no visible
target to re-decide toward. **That ceiling binds the part of this knife that recovers this round's
miss, and it does not bind the claimed channel**, which is next-round generation exposure: the knife
gives up *this* round's cold target to be standing somewhere hotter *next* round. The brief is right
that the ceiling was derived for blind-round-blind interventions and same-round re-decisions; the
ceiling was not imported. The relevant bound turned out to be a different one, computed in §2.

---

## 2. The ceiling the field geometry itself imposes — computed before the A/B

Reproduce with `python3 sim/analyze_hotfield_table.py geometry --map map1`.

Under the hit-rate law (income = scoring-round frequency, `CHANGELOG:328-330`), the ceiling on *any*
repositioning mechanism is the ratio of expected new-gold cells inside the unit's own 5×5 before and
after the move. Computed on map1 walls, from the baseline's own measured position mix (§3), under the
measured field of `CHANGELOG:339-341`.

| unit's own L1 ring | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| expected new-gold cells per round inside its 5×5 | 0.511 | 0.489 | 0.436 | 0.394 | 0.323 | 0.245 | 0.175 | 0.132 | 0.097 |

*Condition: map1 walls, the measured `CHANGELOG` field extended flat at 0.004 for rings ≥6, averaged
over every legal cell at that ring; compared against nothing — this is the absolute level.*

| quantity | measured field | simulator's default (uniform) field |
|---|---:|---:|
| champion's mean unit L1 ring (steady rounds, baseline) | 3.473 | 3.473 |
| its window generation rate | 0.3516 | 0.4479 |
| headroom if it sat permanently on `(8,8)` | **+45.2%** | +12.7% |
| gain from moving inward **0.35** ring | **+5.35%** | +2.38% |
| gain from moving inward **1.00** ring | +15.28% | +6.79% |
| ring whose window rate is 1/2.6 of the champion's | **7** | 8 |

*Condition: same position mix and same walls under two generation laws; each field's numbers are
compared against that same field's own baseline level, so the two columns are ratios within a field,
not across fields.*

Three things fall straight out:

1. **The realised 0.27-ring inward move is worth at most ≈ +4.1% window rate under the measured
   field**, i.e. ≈ **+53 gold** at a 1300-gold base *if* income tracked window rate one-for-one,
   which it cannot. That is below the repo's +150 gate and inside the apparatus's own noise
   (per-cell paired SE in the A/B below is 24–47 gold, so an effect of this size is not resolvable).
   **The mechanism was too small to matter before a single game was played.**
2. **The snake's ×2.6 is fully explained by geometry.** 1/2.6 of the champion's window rate is the
   rate at ring 7; the snake knife moved the snake from ring ≈7 to ≈ring 3.5, which is the
   champion's current camp. The champion is not "near" the snake knife's destination, it *is* the
   destination.
3. The family is not a priori worthless — +45.2% of headroom exists — but it is only reachable by
   *sitting on the centre*, which is not a strategy for two units on a board whose seven NPCs all
   spawn at `(8,8)`.

---

## 3. The identification precondition: the simulator's field is flatter than the measured one

Reproduce with `python3 sim/analyze_hotfield_table.py field --map map1 --field-seeds 8`.

`sim/scenario.py::_make_central` places central gold through `_uniform_order(rng, 1)` — a **uniform
shuffle** of region 1's traversable cells. **The simulator therefore has no gradient at all inside
the central 9×9, which is exactly where the measured gradient lives.** A hot-field knife measured in
a flat field measures only its own cost. That had to be fixed before the A/B could mean anything, so
the driver installs a second field law in-process (a `ScenarioGenerator` subclass whose region-1
ordering is weighted by the measured separable row × column marginals of `sim/GENERATION.md` §3.3,
using the exponential-race sampler the module already uses for outer hotspots). **`sim/scenario.py`
itself was never written to**, because the sibling worker is running against it concurrently.

| L1 ring | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| measured (`CHANGELOG:339-341`) | 0.0335 | 0.0335 | 0.0335 | 0.0250 | 0.0190 | 0.0100 | 0.0040 |
| simulator, uniform (repo default) | 0.0238 | 0.0271 | 0.0286 | 0.0264 | 0.0260 | 0.0223 | 0.0122 |
| simulator, centripetal (this driver) | 0.0617 | 0.0456 | 0.0399 | 0.0361 | 0.0283 | 0.0182 | 0.0078 |
| uniform ÷ measured | 0.71 | 0.81 | 0.85 | 1.05 | **1.37** | **2.23** | **3.04** |

*Condition: 8 scenarios × 500 rounds per field law on map1, per-cell-round rate over non-wall cells,
all generation sources; each simulator row is compared against the measured row above it.*

Gradient steepness, ring 1 → ring 5: **measured 3.35×, simulator-uniform 1.22×, simulator-centripetal
2.51×**. In log terms the default simulator reproduces **16%** of the measured gradient and the
centripetal law **76%**. **Direction of the bias: the default simulator is biased against this
knife** — it models the cost (forgone gold) correctly and understates the benefit by ~2.2× at ring 5.
Both fields are therefore run and reported separately, and the verdict is checked against a linear
extrapolation to the true gradient in §6.

---

## 4. The three freeness proofs — measured, not assumed

Reproduce with `python3 sim/analyze_hotfield_table.py freeness --base-src src/base_player.cpp --workdir build`
then `… icount --inputs out/icount_src.bin --calls 500000 --reps 3`.

### 4.1 Static: `.text` byte-identical, entry alignment identical, static instruction count identical

| arm | `.text` size | `.text` sha256 == base | `moveDecision` addr | `mod64` | static instrs in `moveDecision` | `.rodata` size | `.rodata` bytes differing | AVX512-FP16 count |
|---|---:|---|---|---|---:|---:|---:|---:|
| `base` | 5363 | — | `0x1990` | `0x10` | 704 | 1352 | 0 | 0 |
| `hot_colv_edge` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 8 | 0 |
| `hot_colv_band2` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 10 | 0 |
| `hot_colv_all` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 12 | 0 |
| `anti_colv_edge` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 12 | 0 |
| `anti_colv_all` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 16 | 0 |
| `rm_far` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 258 | 0 |
| `rm_rowflip` | 5363 | **yes** | `0x1990` | `0x10` | 704 | 1352 | 230 | 0 |

*Condition: all eight `.so` built on `quant-compiler` from the same `game_api.h` with identical flags
in the same session; every row is compared against the `base` row, and the `.text` sha256 column is
the strongest available form of the claim — not merely equal size, but the same bytes. The FP16 column
uses the full `tests/verify_construct.sh` offender regex and is trivially zero for every arm because
`.text` is byte-identical to `base`, which is the shipped champion.*

The differing `.rodata` byte counts are exactly the table cells each rule rewrites: `hot_colv_edge`
touches `colv[2..5]` and `colv[11..14]` = 8 bytes; `hot_colv_all` touches `colv[2..7]` and
`colv[9..14]` = 12; `anti_colv_all` touches `colv[0..7]` and `colv[9..16]` = 16; the `rm` arms rewrite
most of `bestrow` (320 B) plus the dead `remap` (26 B). **Nothing else in the artifact moved.**

A clean rebuild in a fresh directory reproduced all eight artifact sha256 values bit for bit
(`out/freeness_reproduce.json`), so the byte-identity of `.text` is a property of the change and
not of one lucky build.

`f18064c` carries **no** `asm(".space …)` pad — its entry lands in the `0x10` bucket unaided — so the
gate here is "candidate `mod64` == base `mod64` == `0x10`", which is what `tests/verify_construct.sh`
asserts, and it is satisfied trivially because the entry address is *literally the same address*.

### 4.2 Dynamic: instructions and cycles per call on a shared input stream

| arm | instructions/call (raw) | Δ vs base | cycles/call (raw) | Δ vs base |
|---|---:|---:|---:|---:|
| `base` | 848.452 | — | 298.982 | — |
| `hot_colv_edge` | 847.986 | −0.47 | 296.436 | −2.55 |
| `hot_colv_band2` | 845.956 | −2.50 | 303.156 | +4.17 |
| `hot_colv_all` | 846.452 | −2.00 | 296.300 | −2.68 |
| `anti_colv_edge` | 847.700 | −0.75 | 297.299 | −1.68 |
| `anti_colv_all` | 846.332 | −2.12 | 294.265 | −4.72 |
| `rm_far` | 852.402 | +3.95 | 296.312 | −2.67 |
| `rm_rowflip` | 849.816 | +1.36 | 297.674 | −1.31 |

*Condition: `tests/icount.cpp` with `perf_event_open`, 500 000 calls × 3 reps, best rep, every `.so`
replayed against the **same** recorded 500-round input stream (`out/icount_src.bin`, from a baseline
self-play game on map1 seed 1000); each row is compared against the `base` row on that identical
stream, and "raw" includes the harness loop, which is constant across rows.*

Honest reading: the dynamic count is **not** structurally guaranteed to be identical for a behaviour
knife, and it is not — it moves by −2.5 … +4.0 instructions per call because the path mix shifts
between the `d==0` fold and the LUT branch. That is a **behaviour** cost of ≤ 0.6 ns at
0.1454 ns/instruction, not a position tax. **The position tax is excluded by the `.text` sha256 being
equal, which is a stronger statement than the brief asked for.** Cycle deltas are −4.7 … +4.2, i.e.
±1.4 ns at 3.4 GHz, and their signs disagree with the instruction deltas, which is what one expects
from noise rather than from a real cost.

**Verdict on §4: all three freeness properties hold. This candidate is not a transplant and does not
inherit the six deaths.**

---

## 5. Where the units actually stand, and how often each rule fires

Reproduce with `python3 sim/analyze_hotfield_table.py probe --seeds 1000-1003 --field {uniform,centripetal}`.

| quantity | uniform field | centripetal field |
|---|---:|---:|
| blind share of steady unit-rounds | **52.7%** | 48.4% |
| visible-target (`has`) share | 46.7% | 50.9% |
| standing-residual share | 0.6% | 0.7% |
| our scoring unit-rounds per 1000 | 254 | 248 |
| opponent's scoring unit-rounds per 1000 | 230 | 250 |
| share of unit-rounds at column 8 (the anchors' column) | 44.8% | 46.0% |
| share of unit-rounds at Chebyshev column offset `abs(sc-8) >= 3` | 9.5% | 8.4% |
| mean unit L1 ring | 3.47 | 3.19 |
| share of **chosen targets** at L1 ring ≥ 5 | **41.2%** | 28.6% |

*Condition: 4 seeds × both order arms of baseline self-play on map1, steady rounds ≥ 8, target picks
recomputed in Python from each round's own `start.grid` with the build's exact `rm`/`prio`; the two
columns are compared against each other to expose how much of the answer is the field law's doing.*

The blind share reproduces the brief's ~53%, and 41% of the champion's chosen targets are already in
the measured-cold ring ≥5 band — so there was a real pool to work on.

| arm | changed unit-rounds | visible → blind | retargeted | mean target-ring change when retargeted |
|---|---:|---:|---:|---:|
| `hot_colv_edge` | 0.70% | 0.61% | 0.09% | **−1.71** |
| `hot_colv_band2` | 2.63% | 2.17% | 0.46% | **−2.86** |
| `hot_colv_all` | 5.01% | 4.18% | 0.83% | **−2.65** |
| `anti_colv_edge` | 0.76% | 0.60% | 0.17% | **+2.77** |
| `anti_colv_all` | 3.60% | 3.02% | 0.57% | **+1.91** |

*Condition: same probe games and same baseline trajectory, each rule evaluated counterfactually
against the baseline's own pick on the identical window, uniform field; each row is compared against
the baseline pick, and the `anti_*` rows are the rate-matched mirrors of the `hot_*` rows above them.*

This is the design check that the rules do what they claim: the hot rules pull the chosen target
**1.7–2.9 rings inward**, the anti rules push it **1.9–2.8 rings outward**, and the firing sets are
matched to within 0.06–1.4 pp. `hot_colv_edge` fires on only 0.70% of unit-rounds — 7 unit-rounds per
game — because 44.8% of unit-rounds sit exactly on column 8. The baseline's mean pickup per scoring
round is **4.423 gold** (128 baseline games, all cells), and a substitution can at most turn a
zero-gold unit-round into a scoring one or the reverse, so the arm's entire two-sided envelope is
**7 × 4.42 ≈ ±31 gold/game — 5× below the +150 gate**. That arm is closed by arithmetic before any
measurement, and its measured +17.1 ± 13.7 sits near the top of its own envelope, which is why more
seeds cannot rescue it.

---

## 6. The A/B — 1024 games, `--dispatch fixed`, both order arms, two field laws, disjoint OOS seeds

Reproduce with
`python3 sim/analyze_hotfield_table.py ab --workdir build --seeds 1000-1015 --oos-seeds 5000-5015 --fields uniform,centripetal --jobs 14`.

Protocol: same-seed paired self-play against the unmodified `f18064c` `.so`; `--dispatch fixed` with
`fixed_costs = (200,201)` for `we_first` and `(201,200)` for `we_second`, so **action order is fixed
by construction and the first-mover-rate channel is eliminated entirely** — the residual is the pure
income effect. Integrity gates passed: all arms within a cell share one `scenario_digest`
(`arms_share_scenario_within_cell = true`), the two field laws share none
(`field_models_differ = true`), and no arm was ever bit-identical to base (0/128 each), i.e. every
arm really is a behaviour change.

### 6.1 Baseline levels this is all measured against

| cell | our net | opponent net | margin | our scoring rounds /1000 | their scoring rounds /1000 | our mean end L1 ring |
|---|---:|---:|---:|---:|---:|---:|
| in-sample, uniform, we_first | 1322.4 | 562.3 | +760.1 | 308.7 | 177.4 | 3.367 |
| in-sample, uniform, we_second | 544.2 | 1294.7 | −750.4 | 185.2 | 303.8 | 3.542 |
| in-sample, centripetal, we_first | 1293.9 | 513.0 | +780.9 | 311.3 | 185.4 | 3.126 |
| in-sample, centripetal, we_second | 487.9 | 1314.3 | −826.4 | 178.8 | 309.6 | 3.285 |
| OOS, uniform, we_first | 1276.4 | 492.4 | +783.9 | 300.1 | 179.8 | 3.410 |
| OOS, uniform, we_second | 514.4 | 1303.9 | −789.4 | 182.4 | 301.3 | 3.554 |
| OOS, centripetal, we_first | 1310.7 | 537.4 | +773.3 | 307.7 | 184.8 | 3.131 |
| OOS, centripetal, we_second | 529.6 | 1286.2 | −756.6 | 182.3 | 310.1 | 3.286 |

*Condition: 16 seeds per band of `f18064c` playing itself under fixed dispatch on map1; each row is
an absolute level, compared against nothing, and exists so that every delta below has a denominator.
**Bias label:** the local NPC/opponent model is over-greedy and over-central, and the opponent here is
a copy of us, so its 177–310 scoring rounds per 1000 are far below the 525–660 of the real strong
opponents — the opponent-scoring column below is measured against a copy of ourselves, not against a
53%-rate opponent.*

### 6.2 All-cells pooled margin (n = 128 per arm: 32 seeds × 2 order arms × 2 field laws)

| arm | margin Δ | SE | σ | wins/losses | our scoring-round Δ | **opponent scoring-round Δ** | mean end L1 ring Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `hot_colv_edge` | **+17.1** | 13.7 | +1.25 | 59/54 | +0.9 | **−2.0** | −0.053 |
| `hot_colv_band2` | −1.8 | 18.2 | −0.10 | 58/69 | −2.7 | −2.2 | −0.156 |
| `hot_colv_all` | **−32.4** | 16.6 | −1.95 | 56/72 | −9.6 | **−5.9** | **−0.277** |
| `anti_colv_edge` | +3.4 | 17.5 | +0.20 | 65/63 | +2.1 | +0.2 | +0.021 |
| `anti_colv_all` | −28.1 | 19.2 | −1.47 | 59/69 | −4.7 | +0.6 | −0.017 |
| `rm_far` | **−116.4** | 17.7 | **−6.57** | 37/91 | −20.6 | **+3.3** | +0.083 |
| `rm_rowflip` | +14.8 | 18.5 | +0.80 | 63/65 | −1.3 | +1.8 | +0.026 |

*Condition: paired against the same-seed, same-order, same-field baseline game; margin is
`(ours − theirs)` change, never net; each row is compared against `base` and the `anti_*` rows are
the rate-matched mirrors of the `hot_*` rows.*

### 6.3 Both order arms separately, and out-of-sample confirmation

| arm | field | in-sample we_first | in-sample we_second | in-sample pooled | OOS pooled | confirmed OOS? |
|---|---|---:|---:|---:|---:|---|
| `hot_colv_edge` | uniform | +15.9 (+0.43σ) | +9.1 (+0.19σ) | +12.5 ± 29.6 | +21.7 ± 28.6 | yes, but both < 1σ |
| `hot_colv_edge` | centripetal | +36.8 (+1.43σ) | +59.7 (+1.24σ) | **+48.2 ± 26.9 (+1.79σ)** | **−13.8 ± 24.1** | **NO — sign flips** |
| `hot_colv_band2` | uniform | −60.9 | −19.7 | −40.3 ± 31.1 | +10.0 ± 36.2 | no |
| `hot_colv_band2` | centripetal | +22.8 | +43.3 | +33.0 ± 39.4 | −9.9 ± 38.5 | **NO — sign flips** |
| `hot_colv_all` | uniform | −69.2 | −19.2 | −44.2 ± 33.8 | −48.8 ± 37.0 | negative in both |
| `hot_colv_all` | centripetal | +3.4 | +18.1 | +10.7 ± 33.5 | −47.3 ± 28.5 | **NO — sign flips** |
| `anti_colv_edge` | uniform | −30.9 | +33.9 | +1.5 ± 38.9 | −6.2 ± 34.5 | no |
| `anti_colv_edge` | centripetal | +13.1 | +4.7 | +8.9 ± 31.7 | +9.6 ± 36.2 | yes, but both < 0.3σ |
| `anti_colv_all` | uniform | −84.5 | +29.6 | −27.5 ± 36.3 | −15.0 ± 47.4 | negative in both |
| `anti_colv_all` | centripetal | −77.4 | +15.5 | −31.0 ± 37.0 | −39.0 ± 32.9 | negative in both |
| `rm_far` | uniform | −145.4 (−3.70σ) | −36.9 | **−91.2 ± 33.7** | **−118.0 ± 35.9** | negative in both |
| `rm_far` | centripetal | −125.9 | −112.1 | **−119.0 ± 34.6** | **−137.4 ± 38.6** | negative in both |
| `rm_rowflip` | uniform | +34.5 | +56.6 | +45.6 ± 34.9 | +11.9 ± 39.4 | shrank 74% |
| `rm_rowflip` | centripetal | −27.3 | +66.0 | +19.3 ± 41.2 | −17.5 ± 32.6 | **NO — sign flips** |

*Condition: same-seed paired margin deltas against base within each order arm; in-sample seeds
1000–1015, out-of-sample seeds 5000–5015 (disjoint); each cell is compared against its own base cell,
and the last column asks only whether the in-sample sign survived on the disjoint seeds.*

**Every arm with a positive in-sample point estimate fails to confirm out of sample in at least one
field law, and no arm reaches even a third of the +150 gate in any cell.**

### 6.4 The decisive test: the rate-matched hot-minus-anti contrast

Because the two arms are run on the *same* games, the hot-minus-anti difference can be taken paired,
which removes the scenario variance that dominates the columns above.

| contrast | field | margin | opponent scoring rounds | our scoring rounds | mean end L1 ring |
|---|---|---:|---:|---:|---:|
| `hot_colv_all` − `anti_colv_all` | uniform | −25.3 ± 22.2 (−1.14σ) | — | — | — |
| `hot_colv_all` − `anti_colv_all` | centripetal | +16.7 ± 23.4 (+0.71σ) | — | — | — |
| `hot_colv_all` − `anti_colv_all` | **both** | **−4.3 ± 16.1 (−0.27σ)** | **−6.45 ± 1.68 (−3.84σ)** | **−4.87 ± 1.55 (−3.14σ)** | **−0.2601 ± 0.0157 (−16.6σ)** |
| `hot_colv_edge` − `anti_colv_edge` | **both** | +13.7 ± 17.8 (+0.77σ) | −2.22 ± 1.43 (−1.55σ) | −1.15 ± 1.51 (−0.76σ) | −0.0741 ± 0.0184 (−4.03σ) |

*Condition: paired within each game (same scenario, same order arm, same field law), n = 64 per field
and 128 pooled; each contrast is hot-arm-minus-its-own-rate-matched-anti-arm, so the firing set, the
number of dropped mask bits and the loss of candidate targets are all held constant and only the
**direction** of the suppression varies.*

Read this table row by row, because it is the whole result:

- **The gait change is real and enormous in effect size**: −0.2601 ± 0.0157 L1 rings at −16.6σ. The
  hot rule does pull the units inward relative to its control. There is no question of the
  intervention having failed to fire.
- **The opponent's scoring-round frequency falls, significantly, in the snake's direction**:
  −6.45 ± 1.68 (−3.84σ). **So the mechanism is the same one the snake knife used** — this is exactly
  the discriminator the brief asked for, and it says "same mechanism", not "ceding". But the
  magnitude is −6.45 against a base of 177–310, i.e. **−2.5%**, where the snake achieved
  660 → 530 = **−20%**. Same mechanism, one eighth of the strength.
- **And margin is zero**: −4.3 ± 16.1, because **our own** scoring rounds fall almost as much:
  −4.87 ± 1.55 (−3.14σ) in the same paired contrast. Our net falls −22.5 ± 10.9 and theirs falls
  ≈ −18.2, so the hot direction **shrinks the harvested pool on both sides**, slightly in our
  disfavour. **This refines the discriminator the brief proposed.** The opponent column on its own
  says "same mechanism as the snake knife" and it is right about the sign; what distinguishes the
  snake knife is the *joint* move — it raised our scoring rounds 119 → 390 (×3.3) **while** lowering
  theirs 660 → 530. A one-sided suppression with our own side falling too is a different animal, and
  the opponent column alone cannot tell them apart. Report both columns, always.
- `anti_colv_all` loses −28.1 while moving the unit only −0.017 rings, which **decomposes the cost**:
  the pure price of losing that many candidate targets is ≈ −28 to −32 gold, and the positional
  benefit of the hot direction is ≈ +4 ± 16 gold. The knife is a bad trade at every dose.

### 6.5 Dose-response

| arm | firing rate (unit-rounds) | all-cells pooled margin | mean end L1 ring Δ |
|---|---:|---:|---:|
| `hot_colv_edge` | 0.70% | +17.1 ± 13.7 | −0.053 |
| `anti_colv_edge` | 0.76% | +3.4 ± 17.5 | +0.021 |
| `hot_colv_band2` | 2.63% | −1.8 ± 18.2 | −0.156 |
| `anti_colv_all` | 3.60% | −28.1 ± 19.2 | −0.017 |
| `hot_colv_all` | 5.01% | −32.4 ± 16.6 | −0.277 |

*Condition: firing rate from the §5 probe under the uniform field; margin pooled over all 128 games
of that arm, each paired against its own base game. Rows are ordered by dose and compared against
each other to expose the slope.*

Margin is **monotonically decreasing in dose** across the hot arms (+17.1 → −1.8 → −32.4) while the
inward ring movement rises monotonically (−0.053 → −0.156 → −0.277). **The cost scales with dose and
the benefit does not pay for it** — which is the same statement as §6.4 seen from a different angle,
and it forecloses "try a bigger dose".

### 6.6 Does extrapolating to the true field rescue anything? No.

The centripetal law captures 76% of the measured log-gradient and the uniform law 16%, so a linear
extrapolation in log-gradient to the true field (1.21) is available:

| arm | uniform (log-grad 0.20) | centripetal (0.92) | linear extrapolation to measured (1.21) | +150 gate |
|---|---:|---:|---:|---|
| `hot_colv_all` vs base | −46.5 ± 24.8 | −18.3 ± 22.1 | ≈ −7 | fails |
| `hot_colv_edge` vs base | +17.1 ± 20.4 | +17.2 ± 18.4 | ≈ +17 | fails (and dose-capped at ≈ +30) |
| `hot_colv_all` − `anti_colv_all` | −25.3 ± 22.2 | +16.7 ± 23.4 | ≈ +34 ± ~30 | n/a |
| `rm_far` vs base | −104.6 ± 24.5 | −128.2 ± 25.7 | ≈ −138 | fails, and worsens with the field |

*Condition: each field column is the all-band, both-order pooled margin under that generation law
against that law's own baseline; the extrapolation column is a two-point linear fit in
log(ring1/ring5 rate ratio) and is an estimate, not a measurement. Compared against the repo's
pre-registered +150 adoption gate.*

**No arm crosses the gate even at the true gradient.** The hot-minus-anti contrast does trend the
right way with field steepness — a **+42.0** swing from uniform to centripetal, which against the
quadrature SE of the two independent contrasts (√(22.2² + 23.4²) = 32.3) is **+1.30σ**: directionally
consistent with a real field channel, nowhere near establishing one. That is the same conclusion §2
reached from geometry alone, and the same one §7 reaches from the opposite direction.

---

## 7. The structural tests, which came out positive and matter more than the candidate

`rm_far` reverses the L1 ordering so the selector prefers the **farthest** visible gold. It is a pure
`rm` rebake, hence just as free as the candidate.

| quantity | value | what it establishes |
|---|---:|---|
| all-cells pooled margin | **−116.4 ± 17.7 (−6.57σ)**, 37 wins / 91 losses | the L1-magnitude channel exists and is large |
| uniform field | −104.6 ± 24.5 | — |
| centripetal field | **−128.2 ± 25.7** | it gets **worse** as the field steepens, so the channel is genuinely the *field*, not travel time |
| our scoring rounds | −20.6 /1000 | the loss is a hit-rate loss, per the hit-rate law |
| opponent scoring rounds | **+3.3** /1000 | **the ceding signature**: we lose scoring rounds and the opponent gains them — the exact opposite of the snake knife's 660 → 530 |
| mean end L1 ring | +0.083 | it pushes us outward, as designed |

*Condition: 128 paired games per row against the same-seed base; the field rows are compared against
each other to isolate the field's contribution, and the opponent column is compared against the
snake knife's −130 signature quoted in `CHANGELOG:341`.*

**This is the positive result that makes the candidate's negative interpretable.** The hot-field
channel is real, worth well over 100 gold in the direction that matters, and the champion's
minimum-L1-first ordering is already sitting on the correct side of it. The champion is not ignoring
the field; it is already exploiting the only part of the field a unit-relative table can see.

`rm_rowflip` flips the within-class tie-break from up-rows-first to down-rows-first — predicted
**null**, because a relative row preference is inward for `(11,8)` and outward for `(6,8)`
simultaneously. Measured: +45.6 in-sample uniform (+1.31σ) → +11.9 OOS; +19.3 in-sample centripetal →
**−17.5** OOS. All four cells within 1.4σ of zero, sign unstable across bands. **Null confirmed**,
which is the empirical form of the §1.3 coordinate argument.

*Condition: 32 paired games per band-field cell against the same-seed base; the in-sample and OOS
columns are compared against each other to test stability, which is the whole point of a predicted
null.*

---

## 8. Ruling

**NEGATIVE. Do not land any arm. Do not preserve the candidate.**

| claim | status |
|---|---|
| `.text` byte-identical, so zero position tax | **PROVEN** (sha256 equal, 5363 B, all 7 arms) |
| entry `mod64` unchanged at `0x10` | **PROVEN** (identical address `0x1990`, all 7 arms) |
| dynamic instructions/call unchanged | **substantially yes**: −2.5 … +4.0 instr = ≤0.6 ns, pure path mix; the position tax is excluded by the stronger `.text` proof |
| the intervention actually changed the gait | **PROVEN**: −0.2601 ± 0.0157 L1 rings at −16.6σ vs its rate-matched control |
| it uses the snake knife's mechanism | **PARTLY**: the opponent's scoring rounds fall, −6.45 ± 1.68 (−3.84σ), same sign as the snake's 660 → 530 — but **ours fall too** (−4.87 ± 1.55), whereas the snake knife raised ours ×3.3. Same sign on the opponent, opposite sign on us. |
| the mechanism is big enough to pay | **NO**: −2.5% opponent suppression vs the snake's −20%; margin −4.3 ± 16.1 |
| any arm reaches the +150 gate | **NO**: best pooled point estimate +17.1 ± 13.7, dose-capped at ≈ +30 |
| any positive confirms out of sample | **NO**: every positive in-sample cell flips or collapses on disjoint seeds |

**The standing explanation is confirmed, with numbers.** The champion camps at mean L1 ring 3.473;
the snake's pre-knife camp was ring ≈7; the snake knife's ×2.6 was the 7 → 3.5 move, which the
champion has already made. What remains inside the champion is a +45.2% window-rate headroom
reachable only by sitting permanently on `(8,8)`, and the ≈0.27-ring move a free column-axis
prohibition can actually deliver is worth ≈ +4% window rate ≈ +53 gold at perfect conversion — below
the gate and below this apparatus's resolution. **The hot-field direction is closed for the champion
chassis on grounds of size, not of sign.**

### What is now closed, and what is not

**Closed — do not re-run:**

1. **Pure table-value hot-field rebakes of `f18064c` are structurally limited to a column-axis
   prohibition, and that prohibition is measured negative at three doses with a rate-matched sign
   control.** `f18064c` has exactly **one** absolutely-indexed table on the movement path
   (`SCT.colv[17]`); it is a 5-bit mask, so it can express prohibition but not a graded bias, and it
   exists only on the column axis. There is no second place to put a free hot-field gradient.
   Anything with a graded bias or a row axis costs instructions and therefore becomes transplant
   number seven.
2. **Unit-relative reorderings carry no ring content beyond L1 magnitude**, argued in §1.3 and
   confirmed by `rm_rowflip`'s null and `rm_far`'s −116.4. `rm` is already optimal in its family.
3. `TT.remap[26]` is dead. Any future audit that counts it as a live selector table is wrong.

**Not closed by this work:**

1. The **absolute** hot-field gradient in a chassis that has an absolutely-indexed bias table —
   which is what `snakeh`/`snakeu` are. `CHANGELOG:379-381`'s conclusion ("the champion cannot evolve
   incrementally, only be reborn") is strengthened, not weakened: the organ is free only if the
   chassis is born with the absolute table, and this measurement shows the champion's substitute for
   that table (a 17-byte column mask) is too coarse to carry the gradient.
2. **Positioning proper.** The knife *did* move the units 0.26 rings for free; it just was not worth
   anything at that size. If some other mechanism can move them 1.0+ ring inward (+15.3% window rate
   under the measured field) it is worth re-pricing — but note the sign of the transfer here: in the
   paired hot-minus-anti contrast our net falls −22.5 ± 10.9 while the opponent's falls ≈ −18.2, so
   concentrating shrank the pool and took slightly more from us than from them. That is the mirror
   image of B2 (spreading out ceded ground) and it says the contested centre is not a free lunch in
   either direction at this size.
3. The bomb-memory contamination in `colv` was never a live problem, and it resolved in the
   *favourable* direction. All-cells pooled per-seat burn deltas (exact, from the unit gold series,
   `burn = previous + pickup − current`): `hot_colv_edge` **−8.9 ± 7.6**, `hot_colv_band2`
   **−11.6 ± 8.5**, `hot_colv_all` **−6.7 ± 7.9**, `anti_colv_edge` +4.1 ± 8.3, `anti_colv_all`
   −0.0 ± 8.5 — every arm within 1.4σ of zero, and the three hot arms **burn less**, not more,
   because a unit that does not chase the outward column also does not walk into it.
   *Condition: 128 paired games per arm against the same-seed base, compared against `base`'s own
   burn in the same game.* So the negative verdict is not an artefact of bomb blindness.

### Delivered artefacts

- `sim/analyze_hotfield_table.py` — modes `tables`, `field`, `geometry`, `probe`, `freeness`,
  `icount`, `ab`, `assemble`.
- `sim/reports/hotfield_table_knife.md` — this file.
- `sim/reports/hotfield_table_knife.json` — the companion, carrying all six stages including the
  full enumeration, the geometry ceiling, both field profiles, both probes, the freeness and icount
  tables, and the A/B aggregate with its integrity gates.
