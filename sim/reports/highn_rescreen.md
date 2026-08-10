# High-n re-screen of the "killed for being too small" pool, and the additivity verdict

> Written 2026-08-11. Baseline **`fd47ea6`** — `git show fd47ea6:src/player.cpp`, sha256
> **`df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217`**, asserted before every
> build. `src/player.cpp` was **never written**; a sibling line owns that file. Every artifact is a
> textual rebake in `/tmp` (host) or `~/gr_highn/build` (contest machine). **Zero platform games
> consumed.** Drivers are new files owned by this line: `sim/analyze_highn_rescreen.py` and
> `sim/highn_variants.py`. Machine-readable companion: `sim/reports/highn_rescreen.json`.
>
> Judged on **`margin` = Δ(ours − theirs)**, never on `net`. Both order arms
> (`--fixed-costs 200,201` and `201,200`) always run and are always reported separately as well as
> pooled. Acceptance is `margin − max(2·SE, |calibrated − uniform|) > 0` **and** sign agreement on a
> disjoint out-of-sample seed set; both gate terms are printed on every row.
>
> **Headline: the route is not closed, and it is not closed for the reason everyone expected.**
> The cursor form compresses arm C from the previously measured 62–116 instructions to **+12.75
> instructions / +5.94 ± 1.92 cycles**, and the full stack is **cheaper than the delivered
> construct** (−42.54 instructions, −4.81 ± 1.56 cycles). So the cost term that killed this
> candidate is gone. What binds now is the **income** side and the **apparatus** term.

---

## 0. What this document is measured against, in one table

| quantity | condition it was measured under, and what it is compared against |
|---|---|
| every `margin` | closed-loop self-play on the stated map, `dispatch="fixed"`, the arm `.so` at seat 1 against the **unmodified `fd47ea6` `.so`** at seat 2, compared against a baseline game on the identical seed, order arm **and field model** |
| every `Δinstructions` | `tests/icount.cpp` with `perf_event_open`, 500 000 calls × 3 reps, best rep, every `.so` replayed against the **same** recorded 500-round input stream, compared against the `base` row of the same run |
| every `Δcycles` | the same tool in `cycles` mode, but **interleaved**: base and arm alternating on a pinned core, 15 rounds, first 2 discarded as warm-up, compared as a **paired** difference so frequency and layout drift are common-mode |
| every `mod64` | `nm -D --defined-only` on the built `.so`, symbol `moveDecision`, address taken mod 64, compared against the delivered `0x10` bucket |
| every `pair_diff` | `tests/pair_diff.py` replaying the arm and the baseline `.so` over the **same three real platform logs** (`game_176120` = map1, `game_176022` = map2, `game_175967` = map3), counting rounds whose `GameOutput` differs |
| the calibrated field | `sim.analyze_hotfield_table.install_field("centripetal")`, an **in-process monkeypatch**; `sim/scenario.py` was not modified, and the integrity gate is that the two field models produce **disjoint** scenario digests |

**Caliper identity.** `f18064c` compiled on the contest machine and replayed against the shared
stream reproduces the registered hot-field anchor to six decimals — **848.452294 vs 848.452** — so
this is the same instrument the earlier instruction verdicts were taken with, not a new one.

---

## 1. Zero-signal dry run — the record, attached

Three independent null controls, all on the delivered baseline text rebuilt to a different
filename. This is the thing that licenses every SE claimed below.

| null control | condition and comparison | result |
|---|---|---|
| `.text` sha256 | `null.cpp` is byte-identical to `base.cpp`, compiled with identical flags, compared against `base.so` | **identical**, and `.rodata` sha256 identical too |
| `pair_diff` | `null.so` vs `base.so` replayed over the three real platform logs, 1500 rounds | **0/500, 0/500, 0/500** |
| dynamic instructions | 500 000 calls × 3 reps on the shared stream, compared against `base` | **−0.00 / call** (802.804 vs 802.804) |
| **closed-loop margin** | **80 paired games** (40 seeds × 2 order arms), map1, both field models, compared against `base` on the identical seed / order / field | **margin = +0.0, SE = 0.0, and 80/80 games bit-identical (`log_sha256` equal)** |
| cycles noise floor | two byte-identical nulls in the interleaved paired protocol | **+1.39 ± 1.23** and **−0.64 ± 1.83** ⇒ the cycle instrument's floor is **≈ ±1.4 cycles**, *not* zero |

**Reading.** The income instrument has *exactly* zero signal — not "statistically zero", literally
zero, because a byte-identical build produces a byte-identical game log on every one of 80 paired
seeds. The **cycle** instrument does not: two byte-identical builds differ by up to 2 cycles, so no
cycle delta below ≈ ±1.4 may be called nonzero. That distinction is recorded because the first
version of the cycle protocol here (non-interleaved, no warm-up) showed a byte-identical null at
**+6.28 cycles** and a real arm at +16.32, i.e. it would have manufactured a result.

---

## 2. The two catches in the candidate list, resolved before any `n` was spent

### 2.1 The 48-byte dead pad is already banked. **Dropped.**

`fd47ea6` carries `asm(".space 96, 0x90")` (source line 560 of the extracted text). Its
`moveDecision` entry is at **0x1a50 ⇒ mod64 = 0x10**, the only non-degraded bucket. An
eleven-point sweep, one build per point, all other bytes identical:

| pad | 0 | 16 | 32 | 48 | 64 | 80 | **96** | 112 | 128 | 144 | 160 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| entry mod64 | 0x30 | 0x00 | **0x10** | **0x20** | 0x30 | 0x00 | **0x10** | 0x20 | 0x30 | 0x00 | **0x10** |
| `.text` size | 5171 | 5187 | 5203 | 5219 | 5235 | 5251 | 5267 | 5283 | 5299 | 5315 | 5331 |

Condition and comparison: each row is a separate build of the delivered text with only the
`.space` operand changed, compared against the delivered pad of 96.

**Therefore:** the entry moves **one byte per pad byte**, `96 ≡ 32 (mod 64)`, and the delivered
construct is already in the good bucket. "Add a 48-byte pad" is not a no-op — it is a **regression**:
a 48-byte pad lands the entry at `0x20`, priced in this repo at +11.67 ns ≈ −128 gold. The candidate
is dropped, and the older `f18064c` claim ("lands in the good bucket unaided") is *also* true and
not in conflict: `f18064c` has no pad at all and a different `decide` size.

**Consequence adopted for everything below: the position tax is a build step, not a cost.** Because
the pad is 96 ≥ 63, any shift `X` is absorbed in one step by `pad = 96 + ((0x10 − mod64) mod 64)`,
which always lands in [96, 159]. The driver does this automatically and re-checks with `nm`.
**Every variant in this report verifies at `mod64 == 0x10`, so no layout term appears in any cost
figure.** Pads actually used: `cursor` 128, `nofold` 144, `stack` 128, `stackp` 144, `cursorv` 144,
the rest 96.

⚠️ Do not infer which code precedes the entry from source order — the pad sits *after*
`moveDecision` in the source and still moves it. `nofold` and `nofoldpure` have **identical**
`.text` sizes (5107) yet need **different** pads (144 vs 96). Only `nm` is authoritative.

### 2.2 "`Clut_k6_stay` cursor form" and "cheapest variable-k arm-C form" are one candidate. **De-duplicated.**

Confirmed by construction: the cursor form *is* the cheap implementation of arm C's reallocation,
and the widened LUT is the only faithful budget-only extension. Measured **once**, as `cursor`. A
cross-check against the previously measured shim arm is in §6.

### 2.3 Two further list changes, both by arithmetic rather than judgement

- **`safe T2` dropped.** Measured here at **+144.04 instructions and +42.81 ± 1.62 cycles** — worse
  than the +105 instructions on record. At 0.38 ns/cycle and 11 gold/ns that is **−179 gold** against
  latency-comparable opponents and **−32 to −54** field-weighted, against a gross on record of
  +13.4 ± 19.1. Its *net* is definitively negative under a **measured** cost, so the permissive
  inclusion rule does not reach it.
- **`cursor4` (producer budget 4) withdrawn, not measured.** Its first build was **incorrect** —
  the unit-1-producer branch slid the head to slot 0 instead of slot `3 − extra`, so the head was
  then overwritten with STAY. It was rebuilt correctly, but its number is reported as provisional
  only; the k = 4 cap is a diagnostic, not the candidate.

**Final list.** Three interventions, plus the stacks built from them:

| candidate | organ | what it does | why it is in |
|---|---|---|---|
| `cursor` | step budget | arm C: when exactly one unit is blind, `k` becomes a cursor over the shared 6-slot buffer and the sighted unit gets the whole budget | the only large gross in the pool, and the only use of the wasted steps never refuted |
| `colvedge` | `SCT.colv[17]` | `hot_colv_edge`: drop the outward window column from the absolute column mask at `sc ≤ 5` or `sc ≥ 11` | zero instructions by construction (`.text` sha256 equal) |
| `nofold` / `nofoldpure` | stand-on-gold | remove the standing target fallback and/or the `d == 0` two-step fold | negative instruction cost; gross sign uncertain ⇒ permissive inclusion |

---

## 3. The construct gates, on every variant

Contest machine `Ubiquant220@8.153.76.120`: Linux 6.12.0 x86_64, AMD EPYC 9T25, `g++ (GCC) 14.3.1
20251022`, flags `-std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared`, **zero warnings**,
**AVX512-FP16 count = 0 for every row**.

| variant | pad | entry mod64 | `.text` | Δ`.text` | Δ`.rodata` | `.text` sha == base | `.rodata` sha == base | `pair_diff` map1/2/3 |
|---|---:|---|---:|---:|---:|---|---|---|
| `base` `fd47ea6` | 96 | **0x10** | 5267 | — | — | — | — | — |
| `null` | 96 | 0x10 | 5267 | +0 | +0 | **yes** | **yes** | **0 / 0 / 0** |
| `colvedge` | 96 | 0x10 | 5267 | +0 | +0 | **yes** | **no** | 2 / 10 / 0 |
| `nofoldpure` | 96 | 0x10 | 5107 | −160 | +0 | no | yes | 101 / 106 / 243 |
| `nofold` | 144 | 0x10 | 5107 | −160 | +0 | no | yes | 101 / 106 / 243 |
| `cursor` | 128 | 0x10 | 5729 | +462 | +448 | no | no | 173 / 202 / 118 |
| `cursorv` | 144 | 0x10 | 5818 | +551 | +448 | no | no | — |
| `safet2` | 96 | 0x10 | 5571 | +304 | +0 | no | yes | — |
| `zc` = colvedge+nofold | 144 | 0x10 | 5107 | −160 | +0 | no | no | 103 / 116 / 243 |
| `zcp` = colvedge+nofoldpure | 96 | 0x10 | 5107 | −160 | +0 | no | no | 103 / 116 / 243 |
| **`stack`** = colvedge+nofold+cursor | 128 | **0x10** | 5517 | +250 | +448 | no | no | 226 / 249 / 277 |
| `stackp` = colvedge+nofoldpure+cursor | 144 | 0x10 | 5533 | +266 | +448 | no | no | 226 / 249 / 277 |

Condition and comparison, one sentence per row: every row is a build of the delivered text with only
the named patch applied, compared against the `base` row built from the identical text with identical
flags on the same host in the same session; `pair_diff` columns compare that `.so` against `base.so`
over the *same three real platform logs*, so the counts are behavioural divergence and nothing else.

**Three things worth naming.**

1. **`colvedge` is provably a pure table-value knife**: `.text` sha256 **equal** to base and
   `.rodata` **size** equal with a **different** sha256. Its `.text` being byte-identical is what
   rules out a position tax, so its instruction cost is zero by construction, not by measurement.
   It is the hot-field **column-band mask** value change; it shares no code with `fold_tour` and is
   not a tour rewrite.
2. **`colvedge` barely fires**: 12 divergent rounds out of 1500 real platform decisions. Any income
   estimate for it must be read against that: it is a very small intervention, and a large
   simulator estimate in either direction would be suspicious.
3. **`nofold` and `nofoldpure` diverge from base on the *same* rounds** (101/106/243) and from each
   other on only **6/500 and 9/500**. So the extra half of `nofold` — deleting the `standing` target
   — changes the *output* almost never (a unit that sees no gold has usually already reached its
   anchor, so the anchor route also yields STAY), but it deletes far more code: −61.01 instructions
   against −12.84.

### The snapshot blind spot does not apply here

`tests/dump_inputs.py` once returned `snapshot_valid = 0` unconditionally and handed two real
behaviour changes a false `0/500`. None of the patches in this report reads any `snapshot` field —
they touch `colv[sc]`, the `standing`/`selfm` masks, the `d == 0` block, `SLut`'s width and `out.k` —
so the gate is not blind to them. This is asserted by inspection of the patch texts, which are
generated by exact-anchor substitution and are reproduced in `sim/highn_variants.py`.

---

## 4. Cost: measured, not estimated. Instructions **and** cycles.

Instructions from 500 000 calls × 3 reps on the shared stream. Cycles from the interleaved paired
protocol, n = 13 pairs after discarding 2 warm-up pairs, pinned to one core.

Conversion **0.38 ns/cycle**, cross-derived two ways from this repo's own numbers, which agree:
(a) the golf datapoint 84 instructions deleted = 5.6 cycles = 2.1 ns ⇒ 0.375 ns/cycle;
(b) base 802.804 instructions × 0.1454 ns ÷ 303.2 cycles ⇒ 0.385 ns/cycle.

| variant | Δinstr | Δcycles | SE | σ | ns | gold vs latency-comparable (11 gold/ns) | gold field-weighted (18–30 % of field) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `null` control ×2 | −0.00 | +1.39 / −0.64 | 1.23 / 1.83 | 1.13 / −0.35 | — | **0 (noise floor ±1.4 cycles)** | — |
| `colvedge` | −0.46 | +0.16 | 1.86 | 0.09 | +0.06 | **0 (`.text` identical ⇒ zero by construction)** | 0 |
| `nofoldpure` | −12.84 | −4.60 | 1.23 | −3.73 | −1.75 | **+19.2** | +3.5 … +5.8 |
| `nofold` | −61.01 | −8.79 | 2.16 | −4.08 | −3.34 | **+36.8** | +6.6 … +11.0 |
| `zc` | −61.47 | −6.71 | 1.07 | −6.28 | −2.55 | +28.1 | +5.1 … +8.4 |
| `zcp` | −13.29 | −6.21 | 1.12 | −5.55 | −2.36 | +26.0 | +4.7 … +7.8 |
| **`cursor`** | **+12.75** | **+5.94** | **1.92** | **3.10** | **+2.26** | **−24.8** | **−4.5 … −7.4** |
| `cursorv` (tail validated) | +45.85 | +14.29 | 2.51 | 5.69 | +5.43 | −59.7 | −10.8 … −17.9 |
| **`stack`** | **−42.54** | **−4.81** | **1.56** | **−3.09** | **−1.83** | **+20.1** | **+3.6 … +6.0** |
| `stackp` | −6.84 | −2.09 | 2.28 | −0.92 | −0.79 | +8.7 | +1.6 … +2.6 |
| `safet2` (dropped) | +144.04 | +42.81 | 1.62 | 26.49 | +16.27 | −179.0 | −32.2 … −53.7 |

Condition and comparison: each row is that `.so` replayed against the identical recorded stream and
differenced against the `base` build of the same session; the cycle column additionally interleaves
base and arm so that clock and placement drift cancel in the pair.

### The compression worked, and the pre-registered risk was avoided rather than survived

The previously measured shapes of this mechanism cost **+62.10** (`ptr`, cheapest) to **+115.73**
(`lazy`, faithful) instructions, of which ~58 was bookkeeping: plan each unit, reallocate, then
rewrite six ints of `out.actions`. The cursor form is **+12.75**, a 4.9× reduction, and cycles agree
in sign and magnitude (+5.94 against the +32.47 of `ptr`).

**Why it is cheap, stated precisely, because the first attempt at it was wrong.** The literal reading
of the proposal — give unit 1 a *variable* write base, 0 when unit 0 is blind and 3 otherwise — was
built and measured at **+19.97 instructions**, and it is **incorrect**: when *both* units are blind
there is no reallocation, yet unit 1 has already overwritten unit 0's slots, and no value of `k` can
place unit 0's actions anywhere but the prefix. Both-blind is the **majority** of blind rounds (324
of 550 on the instrumented game), so repairing it needs a six-store swap on most rounds — strictly
worse. The engine caught it immediately: `illegal GameOutput: actions must be integer codes 0..4`.

The correct form keeps the **delivered constant store indices** and uses `k` itself as the cursor:

| condition | `k` | what is written |
|---|---:|---|
| not exactly one unit blind | 3 | **nothing at all** — the delivered 3 + 3 layout, bit-identical |
| unit 1 blind, unit 0 produces | 6 | three tail stores into slots 3..5 — **zero moves** |
| unit 0 blind, unit 1 produces | 0 | unit 1's head slides 3..5 → 0..2, then the three tail stores |

The asymmetry is the whole trick: unit 0's head **already sits at slot 0**, which is where a 6-slot
span must begin, so that direction needs no rewrite at all. Because the store indices stayed
constant, `acts` never left registers — **the pre-registered vectorisation risk did not have to be
survived, it was designed out.** The tail is real only when the delivered triple *is* the LUT plan;
the `d == 0` fold and the blocked steer fallback both leave the plan pointer null, and a null tail
resolves to `SL.fact[3][3]`, whose entries are all STAY by construction, which reproduces the
simulator arm's `head_ok` gate with no extra branch.

**The stack's latency term is a credit, not a tax.** `nofold` alone returns −61.01 instructions and
−8.79 cycles, which more than pays for the cursor form. Under **any** weighting — including the
expensive T-1 weighting — the stack is faster than the delivered construct, so there is no cost to
subtract from its gross.

---

## 5. Positioning gates: the `fold_tour` drift test

`fold_tour` — the zero-instruction table-value form of "use the wasted steps differently" — died at
−81.4 ± 18.5 because parity prevents a three-step tour from returning to its start, so the unit
drifts off the central generation peak. Any step-transfer arm must show it is not reproducing that.

### 5.1 The `k` histogram: the mechanism fires, in both directions

Each `.so` replayed over the three real platform logs, 500 rounds each; compared against `base`,
which is `k = 3` on 500/500 everywhere.

| variant | map1 (`176120`) | map2 (`176022`) | map3 (`175967`) | reallocation rate |
|---|---|---|---|---:|
| `base`, `colvedge`, `nofold`, `nofoldpure`, `zc` | `{3: 500}` | `{3: 500}` | `{3: 500}` | 0 % |
| **`cursor`** | `{0: 73, 3: 327, 6: 100}` | `{0: 94, 3: 298, 6: 108}` | `{0: 51, 3: 382, 6: 67}` | **34.6 / 40.4 / 23.6 %** |
| `stack` | `{0: 75, 3: 324, 6: 101}` | `{0: 99, 3: 288, 6: 113}` | `{0: 49, 3: 386, 6: 65}` | 35.2 / 42.4 / 22.8 % |

Both directions fire and are roughly balanced (`k = 0` when unit 1 produces, `k = 6` when unit 0
does), which is the requirement that the arm work for **either** unit being the blind one.

### 5.2 Mean ring distance: ⚠️ the cursor form **does** drift outward

Per-unit mean L1 ring from (8,8) at round end, rounds ≥ 8, calibrated field, 12 paired games,
compared against the same-seed baseline.

| variant | unit 0 | unit 1 | both | Δ unit 0 | Δ unit 1 | **Δ both** |
|---|---:|---:|---:|---:|---:|---:|
| `base` | 2.7829 | 3.5855 | 3.1842 | — | — | — |
| `colvedge` | 2.6799 | 3.5337 | 3.1068 | −0.103 | −0.052 | **−0.077** |
| `nofold` | 2.7810 | 3.5835 | 3.1822 | −0.002 | −0.002 | **−0.002** |
| `nofoldpure` | 3.0054 | 3.5413 | 3.2734 | +0.223 | −0.044 | **+0.089** |
| `zc` | 2.7046 | 3.4577 | 3.0811 | −0.078 | −0.128 | **−0.103** |
| **`cursor`** | 2.9661 | 3.7486 | 3.3574 | +0.183 | +0.163 | **+0.173** |
| `stack` | 3.1126 | 3.6858 | 3.3992 | +0.330 | +0.100 | **+0.215** |

**This must not be waved away, and it also is not the `fold_tour` failure.** Three points.

1. The drift is **real and in the bad direction**: +0.17 rings for `cursor`, +0.22 for `stack`.
2. Its **mechanism is different from `fold_tour`'s**. It appears on *both* units, including the
   donor — which by construction does nothing on a firing round. The cause is that silencing a unit
   that was walking *back* toward its anchor freezes it wherever it stands, and that is on average
   further out than the anchor. `fold_tour` drifted because a 3-step tour cannot close on a parity
   board; this drifts because a frozen return trip does not complete.
3. It is **already priced**. Every margin below is a closed-loop measurement of the same build, so
   the outward drift and whatever it costs are inside the number, not additional to it. `fold_tour`
   was −81.4 *with* its drift; `cursor` is strongly positive *with* its drift.

The actionable consequence is a follow-up, not a veto: an arm that sends the donor one step toward
its anchor instead of STAY would test whether the drift is recoverable. The rate-matched donor
control on record (`Cd1_silence_matched`) measured ≈ 0, so the donor side is not obviously where the
value is — but that control silenced, it did not *steer*.

---

## 6. Does the C++ artifact express the previously measured mechanism?

The prior arm-C numbers were taken with a Python shim (`BudgetStrategy` + `Clut_k6_stay`) wrapped
around the frozen `.so`. This report's artifact is real C++. They must be shown to be the same
intervention or the prior work does not transfer.

| condition | C++ `cursor` margin | shim `Clut_k6_stay` margin | paired difference (C++ − shim) |
|---|---:|---:|---:|
| calibrated field, 20 paired games, map1, host scalar build | **+62.0 ± 47.2** | **+89.9 ± 45.5** | **−27.9 ± 53.3** |
| uniform field, 20 paired games, map1, host scalar build | +26.6 ± 64.8 | −20.9 ± 48.5 | +47.5 ± 60.3 |

Condition and comparison: all three columns are the same 20 games per field — same seeds, same order
arms, same field monkeypatch, each compared against a baseline run of the unmodified `fd47ea6` `.so`
on the identical seed; the difference column is paired game by game. The shim's own fidelity gate,
`replica_match_rate`, is **1.0000** against `fd47ea6`, and `anchor_default_mismatch` is 0, so the
Python replica reproduces the delivered planner exactly.

**Reading.** The paired difference is indistinguishable from zero in both fields, so the C++ cursor
form and the previously measured shim arm are the same intervention at this resolution. The prior
mechanism work — trigger specificity, the three-way control set, the residue-not-reach explanation —
therefore transfers to this artifact. Note this cross-check is deliberately low-n; its job is to
exclude a *gross* mismatch, and it does.

---

## 7. Step 1 — the coarse individual screen (its only job is deciding who enters the stack)

**Precision is deliberately not bought here.** A false exclusion is permanent while a false
inclusion is cheap — the stacked measurement absorbs it and leave-one-out can find it. **Rule used:
include unless the candidate is clearly negative**, where "clearly" means the point estimate is
negative by more than its own gate. Individual numbers below do **not** enter the final decision.

map1, 40 tune seeds (3000–3039) and 40 disjoint out-of-sample seeds (7000–7039), 2 order arms each
⇒ **80 paired games per field per band**. Integrity: `arms_share_scenario_within_cell = true`,
`field_models_differ = true`, 80 distinct scenario digests per field with zero overlap.

| arm | calibrated margin | SE | uniform margin | SE | **two-field diff** | gate = max(2SE,\|Δfield\|) | margin − gate | classification | OOS margin | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| `null` | +0.0 | 0.0 | +0.0 | 0.0 | +0.0 | 0.0 | +0.0 | — (identical) | 0.0 | zero-signal control |
| `colvedge` | −24.9 | 20.6 | −9.9 | 19.9 | −15.0 | 41.2 | −66.1 | pure loss | +3.0 | undecidable |
| `nofoldpure` | +4.5 | 26.2 | +8.4 | 24.3 | −3.9 | 52.4 | −47.9 | co-gain (ours faster) | +39.9 | undecidable |
| `nofold` | +23.4 | 24.2 | −5.5 | 21.5 | **+28.9** | 48.3 | −24.9 | **joint move** | +23.3 | **undecidable on this apparatus** |
| **`cursor`** | **+76.7** | **26.0** | **+62.8** | 27.6 | **+13.8** | **51.9** | **+24.7** | co-gain (ours faster) | **+69.5** | **accept** |

Condition and comparison, one sentence per row:

- `null` — byte-identical rebuild, compared against `base` on the identical seed, order arm and
  field; every one of the 80 games is bit-identical, which is why both SE terms are exactly 0.
- `colvedge` — the absolute `colv[sc]` column-band mask value change, compared against `base` under
  the same field; note its `pair_diff` fires on only 12 of 1500 real platform decisions.
- `nofoldpure` — only the `d == 0` two-step fold removed, standing target retained, compared against
  `base` under the same field.
- `nofold` — the registered ablation: both the standing target fallback and the fold removed,
  compared against `base` under the same field.
- `cursor` — arm C in cursor form, producer budget 6, no tail validation, compared against `base`
  under the same field; this is the same intervention §6 ties to `Clut_k6_stay`.

**Three readings.**

1. **`cursor` clears its gate even at the coarse `n`**, and its OOS margin agrees in sign. Its
   calibrated +76.7 sits right on the defensible low end of the prior record (+80.1 tune); the
   forbidden-high OOS values from that record (+171 … +215) do **not** reproduce here, which is the
   correct direction for a re-screen to move.
2. **`colvedge` contradicts its prior estimate.** The number carried into this task was
   +17.1 ± 13.7; measured here on fresh seeds against `fd47ea6` it is **−24.9 ± 20.6 calibrated with
   an OOS of +3.0**, i.e. the sign disagrees between bands. It is not *clearly* negative, so under
   the permissive rule it entered the stack — but it enters on a coin flip, not on strength.
3. **`nofold` is apparatus-limited, and that is the more important finding.** Its two-field
   difference (+28.9) **exceeds its own effect** (+23.4). Adding seeds cannot fix that: the two-field
   term is systematic. Its prior estimate (−4.6 ± 19.0) and this one (+23.4 ± 24.2) are ~1.1σ apart,
   i.e. compatible and jointly uninformative.

---
