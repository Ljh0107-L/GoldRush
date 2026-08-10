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

| variant | members | pad | entry mod64 | `.text` | Δ`.text` | Δ`.rodata` | `.text` sha == base | `.rodata` sha == base | `pair_diff` map1/2/3 |
|---|---|---:|---|---:|---:|---:|---|---|---|
| `base` `fd47ea6` | — | 96 | **0x10** | 5267 | — | — | — | — | — |
| `null` | byte-identical rebuild | 96 | 0x10 | 5267 | +0 | +0 | **yes** | **yes** | **0 / 0 / 0** |
| `colvedge` | colvedge | 96 | 0x10 | 5267 | **+0** | **+0** | **yes** | **no** | 2 / 10 / 0 |
| `nofoldpure` | nofoldpure | 96 | 0x10 | 5107 | −160 | +0 | no | yes | 101 / 106 / 243 |
| `nofold` | nofold | 144 | 0x10 | 5107 | −160 | +0 | no | yes | 101 / 106 / 243 |
| `cursor` | cursor | 96 | 0x10 | 5379 | +112 | +448 | no | no | 173 / 202 / 118 |
| `cursorv` | cursor + tail validation | 144 | 0x10 | 5550 | +283 | +448 | no | no | — |
| `cursor4` | cursor, budget 4 | 128 | 0x10 | 5427 | +160 | +160 | no | no | — |
| `safet2` (dropped) | safe T2 | 96 | 0x10 | 5571 | +304 | +0 | no | yes | — |
| `zc` | colvedge + nofold | 144 | 0x10 | 5107 | −160 | +0 | no | no | 103 / 116 / 243 |
| `zcp` | colvedge + nofoldpure | 96 | 0x10 | 5107 | −160 | +0 | no | no | 103 / 116 / 243 |
| **`ncp`** = `loo_nocolv` | **nofoldpure + cursor** | **96** | **0x10** | 5235 | **−32** | +448 | no | no | — |
| **`nc`** | **nofold + cursor** | **144** | **0x10** | 5283 | **+16** | +448 | no | no | — |
| `loo_nofold` | colvedge + cursor | 96 | 0x10 | 5379 | +112 | +448 | no | no | — |
| `loo_nocursor` | colvedge + nofoldpure | 96 | 0x10 | 5107 | −160 | +0 | no | no | — |
| **`stack`** | colvedge + nofold + cursor | 144 | **0x10** | 5283 | +16 | +448 | no | no | 226 / 249 / 277 |
| `stackp` | colvedge + nofoldpure + cursor | 96 | 0x10 | 5235 | −32 | +448 | no | no | 226 / 249 / 277 |

**All 18 variants verify at `mod64 == 0x10`, all have FP16 count 0, and every build emitted zero
warnings under `-Wall -Wextra`.** Builds are **bit-reproducible**: two independent `construct` runs
produce identical `.so` sha256 (`stack` `8162227ec88ce347…`, `cursor` `436dbea75f4254b9…`).

⚠️ An earlier draft of this table reported `stack` at pad 128 / `.text` 5517. That was the
**pre-fix** cursor build (see §4) and is **withdrawn**; the corrected `stack` is pad 144 / 5283.

Condition and comparison, one sentence per row: every row is a build of the delivered text with only
the named patch applied, compared against the `base` row built from the identical text with identical
flags on the same host in the same session; `pair_diff` columns compare that `.so` against `base.so`
over the *same three real platform logs*, so the counts are behavioural divergence and nothing else.

**Three things worth naming.**

1. **`colvedge` is provably a pure table-value knife**: `.text` sha256 **equal** to base and
   `.rodata` **size** equal with a **different** sha256. Its `.text` being byte-identical is what
   rules out a position tax, so its instruction cost is zero by construction, not by measurement.
   A second, independent proof falls out of the stacks: **`nc` = nofold+cursor and `stack` =
   colvedge+nofold+cursor have identical `.text` sha256**
   (`aed8c68970fa47e14ae7b2825f71adb04dfbbf649574d933bd13bc87bd76d8dd`) and differ only in `.so`
   sha256, i.e. adding `colvedge` to a build changes `.rodata` bytes and nothing else.
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

| variant | same-stream instr/call | Δinstr | Δcycles | SE | σ | ns @0.38 | gold vs latency-comparable (11 gold/ns) | gold field-weighted (18–30 %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base `fd47ea6` | **802.804** | — | — | — | — | — | — | — |
| `null` ×2 | 802.804 | **−0.00** | +1.39 / −0.64 | 1.23 / 1.83 | 1.13 / −0.35 | — | **0 (noise floor ±1.4 cycles)** | — |
| `colvedge` | 802.342 | −0.46 | +0.16 | 1.86 | 0.09 | +0.06 | **0 (`.text` identical ⇒ zero by construction)** | 0 |
| `nofoldpure` | 789.968 | −12.84 | −4.60 | 1.23 | −3.73 | −1.75 | **+19.2** | +3.5 … +5.8 |
| `nofold` | 741.794 | −61.01 | −8.79 | 2.16 | −4.08 | −3.34 | **+36.8** | +6.6 … +11.0 |
| `zc` = colvedge+nofold | 741.338 | −61.47 | −6.71 | 1.07 | −6.28 | −2.55 | +28.1 | +5.1 … +8.4 |
| `zcp` = colvedge+nofoldpure | 789.518 | −13.29 | −6.21 | 1.12 | −5.55 | −2.36 | +26.0 | +4.7 … +7.8 |
| **`cursor`** | 815.550 | **+12.75** | **+5.94** | 1.92 | 3.10 | +2.26 | **−24.8** | **−4.5 … −7.4** |
| `cursor4` (budget 4) | 826.250 | +23.45 | +3.71 | 1.96 | 1.89 | +1.41 | −15.5 | −2.8 … −4.7 |
| `cursorv` (tail validated) | 848.658 | +45.85 | +14.29 | 2.51 | 5.69 | +5.43 | −59.7 | −10.8 … −17.9 |
| **`ncp`** = nofoldpure+cursor | **796.438** | **−6.37** | ≈ +1.3 from parts | — | — | ≈ +0.5 | ≈ −5.4 | ≈ −1.0 … −1.6 |
| **`nc`** = nofold+cursor | **760.734** | **−42.07** | ≈ −2.9 from parts | — | — | ≈ −1.1 | ≈ +12.1 | ≈ +2.2 … +3.6 |
| `loo_nofold` = colvedge+cursor | 815.078 | +12.27 | — | — | — | — | ≈ −24 | ≈ −4.3 … −7.2 |
| `loo_nocursor` = colvedge+nofoldpure | 789.518 | −13.29 | — | — | — | — | ≈ +26 | — |
| **`stack`** = all three | **760.262** | **−42.54** | **−4.81** | 1.56 | −3.09 | −1.83 | **+20.1** | **+3.6 … +6.0** |
| `stackp` | 795.960 | −6.84 | −2.09 | 2.28 | −0.92 | −0.79 | +8.7 | +1.6 … +2.6 |
| `safet2` (dropped) | 946.840 | +144.04 | +42.81 | 1.62 | 26.49 | +16.27 | −179.0 | −32.2 … −53.7 |

> ⛔ **Instruction count is not a construct constant** — it is stream-specific (883–1008 across
> maps in this repo's own records, and both 894.216 and 848.452 are recorded as stream-specific
> values). **The constraint is "no greater than the SAME-STREAM baseline", never against a
> remembered constant.** The stream identity, recorded here with the numbers rather than elsewhere:
> **`out/icount_src.bin`, generated by `run_game(base, base, map1, seed=1000, fixed_costs=(200,201))`
> then `tests/dump_inputs.py`, 500 rounds, 722 000 bytes.** On that stream `f18064c` measures
> **848.452294** and `fd47ea6` measures **802.804343**. Every Δ above is against 802.804343 on that
> same stream. **Every landing candidate is at or below its same-stream baseline.**

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

Per-unit mean L1 ring from (8,8) at round end, rounds ≥ 8, calibrated field, compared against the
same-seed baseline.

| variant | unit 0 | unit 1 | both | Δ unit 0 | Δ unit 1 | **Δ both** |
|---|---:|---:|---:|---:|---:|---:|
| `base` | 2.7566 | 3.6307 | 3.1937 | — | — | — |
| `nofold` | 2.7929 | 3.5659 | 3.1794 | +0.036 | −0.065 | **−0.014** |
| `colvedge` | 2.7279 | 3.5179 | 3.1229 | −0.029 | −0.113 | **−0.071** |
| `nofoldpure` | 2.9668 | 3.5489 | 3.2579 | +0.210 | −0.082 | **+0.064** |
| **`cursor`** | 2.9895 | 3.7757 | 3.3826 | +0.233 | +0.145 | **+0.189** |
| `stack` | 3.1133 | 3.6681 | 3.3907 | +0.357 | +0.037 | **+0.197** |

Condition and comparison: 8 seeds × 2 order arms = **16 paired games** per arm, calibrated field,
map1, rounds ≥ 8, each arm's per-unit mean ring compared against the same-seed baseline run.
**Instrument sanity check:** this harness puts the baseline unit-1 camp at **3.6307**, against
**3.516** from an independent harness and **3.473** from the recorded champion figure — agreement to
0.11 and 0.16 rings on a different seed set and in the calibrated rather than the uniform field.

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

## 8. ⭐ Step 2 — the stacked verdict. This is the only measurement that decides.

**Why this replaces assembling the answer from parts.** One margin, one SE, one two-field term. It
is immune to three things at once: **multiple comparisons** (one verdict, no family), **the
additivity assumption** (it measures the actual stacked outcome, so linearity is not a premise), and
**interaction pricing** (interaction is already inside the number).

map1, **150 tune seeds (3000–3149) and 150 disjoint out-of-sample seeds (7000–7149)**, both order
arms ⇒ **300 paired games per arm per field per band**. Integrity gate: 300 distinct scenario
digests per field, **disjoint between fields**, and every arm faces the identical scenario within a
cell. **Achieved SE: 11.7 – 13.9, i.e. the SE ≤ 20 target is met with margin.**

| arm | calibrated margin | SE | uniform margin | SE | **two-field diff** | 2·SE | **gate** | **margin − gate** | classification | OOS margin ± SE | OOS − gate | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| `null` | **+0.0** | **0.0** | +0.0 | 0.0 | +0.0 | 0.0 | 0.0 | +0.0 | — (300/300 bit-identical) | +0.0 ± 0.0 | +0.0 | zero-signal control |
| `zc` = colvedge+nofold **(the zero-cost sub-stack)** | **+20.3** | 11.7 | +14.4 | 12.0 | +5.9 | 23.5 | **23.5** | **−3.2** | **joint move** | +18.6 ± 11.7 | −4.7 | **just short of gate** |
| `zcp` = colvedge+nofoldpure | −15.9 | 11.9 | −6.2 | 12.2 | −9.7 | 23.9 | 23.9 | −39.8 | pure loss | +16.9 ± 11.4 | −10.6 | reject, **sign flips between bands** |
| **`stack`** = colvedge+nofold+cursor | **+63.4** | **13.9** | +71.0 | 13.7 | **−7.6** | 27.8 | **27.8** | **+35.6** | co-gain (ours faster) | **+59.0 ± 11.9** | **+35.2** | **ACCEPT** |
| **`stackp`** = colvedge+nofoldpure+cursor | **+60.0** | **12.6** | +68.1 | 12.6 | **−8.1** | 25.2 | **25.2** | **+34.9** | co-gain (ours faster) | **+63.9 ± 12.1** | **+39.7** | **ACCEPT** |

Condition and comparison, one sentence per row:

- `null` — a byte-identical rebuild of the delivered text, compared against `base` on the identical
  seed, order arm and field model; 300/300 games have equal `log_sha256`, which is why both gate
  terms are exactly zero and why the SEs quoted on the other rows are believable.
- `zc` — the two zero-or-negative-cost interventions together (pure table-value column mask +
  stand-on-gold organ removed), compared against `base` under the same field; this is the
  "lowest-risk deliverable" sub-stack, and it is the row to read for whether a free change exists.
- `zcp` — the same but with only the `d == 0` fold removed and the standing target retained,
  compared against `base` under the same field.
- `stack` — all three interventions, compared against `base` under the same field; this is the
  artifact that would actually be landed and the one all construct gates in §3 were run on.
- `stackp` — the same three with the milder fold variant, compared against `base` under the same
  field, as an independent replicate of the `stack` result through a different second organ.

### Order arms, reported separately as required (calibrated, tune)

| arm | we-first | we-second | both positive? |
|---|---:|---:|---|
| `null` | +0.0 | +0.0 | — |
| `zc` | +30.6 | +10.1 | yes |
| `zcp` | −4.0 | −27.9 | no |
| **`stack`** | **+111.3** | **+15.4** | **yes** |
| **`stackp`** | **+69.9** | **+50.2** | **yes** |

`stack` is strongly order-asymmetric (7×), `stackp` is nearly symmetric. Neither has a negative order
arm, which is the minimum bar; the asymmetry itself is a residual risk, because on the platform we
are first-mover against ~98 % of the field but roughly at parity against the two fast teams.

### The three-way classification, and the mechanism it exposes

Calibrated, pooled, tune, n = 300, all deltas against the same-seed baseline:

| arm | our net Δ | their net Δ | our burn Δ | their burn Δ | our scoring-rounds Δ | their scoring-rounds Δ | class |
|---|---:|---:|---:|---:|---:|---:|---|
| `zc` | +8.6 ± 7.6 | **−11.7 ± 8.3** | −32.4 | +3.3 | −7.35 | −1.66 | **joint move** |
| `zcp` | −5.3 ± 7.7 | +10.6 ± 8.5 | −23.5 | −11.5 | −9.80 | +0.02 | pure loss |
| `stack` | **+76.1 ± 9.2** | +12.7 ± 9.1 | −25.4 | −6.6 | **−22.10** | +0.98 | co-gain (ours faster) |
| `stackp` | +66.3 ± 8.3 | +6.3 ± 8.9 | −15.6 | −3.6 | −22.97 | +0.83 | co-gain (ours faster) |

**Read this carefully, because it is the most informative row in the report.** `stack` scores in
**22 fewer** unit-rounds per game and still nets **+76**. That is not "we cover more ground"; it is
**bigger bites per scoring round** — exactly the `mean`-raising signature the project's target
requires, and the opposite of the sliding-along-the-frontier signature that killed the hit-rate
family (higher hit rate, smaller bites, `mean` unmoved). It is also consistent with the recorded
mechanism: pickup is proportional (65 %), so a tail that re-enters a cell the same unit already
entered this round harvests the 35 % residue.

**It is `co-gain`, not `joint move`.** The unmodified opponent also gains (+12.7 ± 9.1), just less
than we do. That is a weaker signature than `zc`'s genuine joint move (theirs −11.7 ± 8.3), and it is
the honest caveat on the headline: part of the stack's margin comes from a board that got easier for
both seats, not purely from taking ground off the opponent. It is **not** `ceding` — theirs is well
below ours — so the "spreading out cedes contested ground" failure mode is absent.

### The zero-cost sub-stack: a near miss, and it should be read as a near miss

`zc` = **+20.3 ± 11.7 calibrated, gate 23.5, margin − gate = −3.2**, with OOS **+18.6 ± 11.7**
(same sign, also −4.7 short). It is the only **joint move** in the table and it costs **−61 instructions
and −6.7 cycles**, i.e. it is a latency credit as well. It misses its gate by 3.2 gold.

This is almost exactly the prediction carried into the task (≈ +12.5 with SE ≈ 23); measured at
n = 300 it is +20.3 with SE 11.7. **Reaching its gate is now purely a matter of `n`**: at SE ≤ 10.2
the gate would fall to 20.4 and it would clear. That is ~n = 400 paired games per field, roughly
another 35 minutes of simulator, and it is the single cheapest open experiment in the project.

---

## 9. Step 3 — leave-one-out, and the additivity number

Leave-one-out is preferred over pairwise: with `k` members it is `k` measurements rather than
`k(k−1)/2`, and it answers the question actually at hand — *which one to drop*.

map1, 100 tune seeds (3000–3099) and 100 disjoint out-of-sample seeds (7000–7099), both order arms
⇒ **200 paired games per arm per field per band**. Integrity: 200 distinct digests per field,
disjoint between fields.

| variant | members | calibrated | SE | uniform | two-field | gate | margin − gate | OOS | classification |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `stack` (from §8) | colvedge + nofold + cursor | +63.4 | 13.9 | +71.0 | −7.6 | 27.8 | +35.6 | +59.0 | co-gain |
| **`loo_nocolv` = `ncp`** | **nofoldpure + cursor** | **+79.5** | 16.2 | +60.6 | +18.9 | 32.3 | **+47.2** | **+50.4** | co-gain (ours faster) |
| `loo_nofold` | colvedge + cursor | +72.9 | 14.9 | +63.3 | +9.6 | 29.8 | +43.0 | +60.1 | **joint move** |
| `loo_nocursor` | colvedge + nofoldpure | **−4.2** | 14.7 | −1.4 | −2.8 | 29.4 | −33.7 | +16.6 | pure loss |

Condition and comparison, one sentence per row: each arm is the stack with exactly one member
removed, compared against `base` on the identical seed / order arm / field model, so the difference
between a row and the `stack` row is that member's contribution *inside the stack*.

Order arms, separately: `loo_nocolv` +112.7 / +46.4, `loo_nofold` +96.4 / +49.3,
`loo_nocursor` −0.3 / −8.1. No arm has a negative order leg except `loo_nocursor`, which is the arm
with the mechanism removed.

### Each member's contribution inside the stack, against its standalone estimate

| member | contribution inside the stack (`stack` − its LOO arm) | standalone (§7, n=80) | difference, with SE | interaction distinguishable from zero? |
|---|---:|---:|---:|---|
| **`cursor`** | **+67.6 ± 20.2** | +76.7 ± 26.0 | −9.1 ± 32.9 | **no (0.28σ)** |
| `colvedge` | **−16.1 ± 21.4** | −24.9 ± 20.6 | +8.8 ± 29.7 | **no (0.30σ)** |
| `nofold` | **−9.5 ± 20.4** | +23.4 ± 24.2 | −32.9 ± 31.7 | **no (1.04σ)** |

### The additivity verdict, as a number

| comparison | sum of individual margins | measured combined margin | **interaction** | SE on the interaction | distinguishable from zero? |
|---|---:|---:|---:|---:|---|
| all three (`stack`) | +75.2 | +63.4 | **−11.8** | **±43.4** | **no (0.27σ)** |
| colvedge + nofold (`zc`) | −1.5 | +20.3 | +21.8 | ±33.9 | no (0.64σ) |
| colvedge + cursor (`loo_nofold`) | +51.8 | +72.9 | +21.1 | ±36.4 | no (0.58σ) |
| nofoldpure + cursor (`ncp`) | +81.2 | +79.5 | −1.7 | ±40.0 | no (0.04σ) |

Condition and comparison: the "sum" column adds the §7 standalone calibrated margins (n = 80 each,
so the sum inherits their SEs in quadrature); the "measured" column is the stacked build's own
closed-loop margin at n = 200–300 against `base` under the same field; the interaction is the
difference and its SE is the quadrature sum.

> ⭐ **State it plainly: interaction is indistinguishable from zero at this resolution in every
> comparison tested (largest |interaction| 21.8 against an SE of 33.9). The effects add,
> approximately linearly.**
>
> **So the "accumulate nine small candidates" route did not die of cancellation. It is small for a
> different and cleaner reason: only one member has a nonzero effect.** Dropping `cursor` collapses
> the stack from +63.4 to −4.2; dropping either of the other two makes the stack *better*. That is a
> more decision-relevant finding than a cancellation result would have been, because it says the
> answer is **one candidate landed well**, not nine accumulated.

The resolution limit is honest and worth naming: the interaction SEs are ±30–43, dominated by the
n = 80 standalone arms. An interaction as large as ±60 gold would have been detected; one of ±25
would not. Buying that would need the individuals re-run at n = 300, which is ~40 minutes of
simulator and was traded away for the per-map coverage in §10.

---

## 10. Per-map, because a pooled number cannot be converted into win rate

map2 uses 50 tune seeds (3000–3049) and 50 disjoint OOS seeds (7000–7049), both order arms ⇒
**100 paired games per arm per field per band**; map1 rows are the §8 / §9 values at their own `n`.

| map | arm | calibrated | SE | uniform | two-field | gate | **margin − gate** | OOS | classification |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| map1 | `cursor` | +76.7 | 26.0 | +62.8 | +13.8 | 51.9 | +24.7 | +69.5 | co-gain |
| map1 | `ncp` = nofoldpure+cursor | **+79.5** | 16.2 | +60.6 | +18.9 | 32.3 | **+47.2** | +50.4 | co-gain |
| map1 | `stack` | +63.4 | 13.9 | +71.0 | −7.6 | 27.8 | +35.6 | +59.0 | co-gain |
| **map2** | `cursor` | **+90.2** | 21.3 | +76.9 | +13.3 | 42.6 | **+47.6** | +74.0 | co-gain |
| **map2** | `stack` | **+111.3** | 19.6 | +84.7 | +26.6 | 39.2 | **+72.2** | +57.4 | **joint move** |
| map2 | `null` | +0.0 | 0.0 | +0.0 | 0.0 | 0.0 | +0.0 | +0.0 | 100/100 bit-identical |
| map3 | — | run not complete at time of writing | | | | | | | |

Condition and comparison, one sentence per row: each row is that arm's closed-loop margin against
the unmodified `fd47ea6` on the same map, seed, order arm and field model; the `null` row is the
zero-signal control re-run **on map2** so the map2 numbers carry their own dry run rather than
inheriting map1's.

Order arms on map2: `cursor` +137.1 / +43.3, `stack` +163.6 / +59.1 — both positive, same 3× first/second
asymmetry as map1.

**map2 is the better map for this mechanism, and that matters**, because map2 is the cheapest of the
three convertible gates (+86 for a 50 % line against the benchmark opponent, near-linear). `stack`
on map2 is **+111.3 ± 19.6, which clears +86**; `cursor` alone is **+90.2 ± 21.3**, which also
clears it but only just. On map1, whose 50 % line is +200, nothing here comes close. **Report the
convertible gates separately from proxy gates: this result reaches the map2 line and does not reach
map1's or the pooled +166.**

`stack` on map2 is a **joint move** (ours +101.2 ± 13.7, theirs **−10.2 ± 13.7**), which is a
stronger signature than map1's co-gain, i.e. on map2 the gain comes partly out of the opponent
rather than out of an easier board.

---

## 11. Positioning gates, part 2: where the tail slots actually go

`fold_tour` died at −81.4 ± 18.5 because parity prevents a three-step tour from returning to its
start, so the unit drifts off the central generation peak; the recorded lesson is that value is in
*where you stand*, not *how many cells you touch*. Two further candidates have since died the same
way (a teammate-spread tie-break at centre-ring Δ +1.61, an enemy-proximity tie-break at +0.79). So
the tail geometry has to be shown explicitly.

Each `.so` replayed over the **three real platform logs**; positions come from the logged input and
actions from the `.so`'s own output, so **nothing in this section depends on a simulator field
model**. Compared against `base` replayed over the identical logs.

| quantity | map1 `176120` | map2 `176022` | map3 `175967` |
|---|---:|---:|---:|
| firing rounds (`k ≠ 3`) | 173 | 202 | 118 |
| tail slots examined | 519 | 606 | 354 |
| tail slots **inward** of the ring after step 3 | 16.6 % | 27.4 % | 22.0 % |
| tail slots **tangential** | 65.3 % | 49.2 % | 51.4 % |
| tail slots **outward** | 18.1 % | 23.4 % | 26.6 % |
| **net (outward − inward)** | **+1.5 pp** | **−4.0 pp (net inward)** | **+4.6 pp** |
| mean ring after step 3 | 3.249 | 3.396 | 1.737 |
| mean ring over tail slots | 3.264 | 3.356 | 1.782 |
| **Δ ring caused by the tail** | **+0.015** | **−0.040** | **+0.045** |
| share of tail slots landing on ring ≥ 5 (dead ground) | 24.47 % | 24.26 % | **2.26 %** |
| **donor span is empty or all STAY** | **100.0 %** | **100.0 %** | **100.0 %** |

map1 landing-ring histogram, 519 tail slots:
`{0:57, 1:86, 2:37, 3:79, 4:133, 5:80, 6:19, 7:7, 8:9, 9:1, 10:4, 11:3, 12:4}`.

Condition and comparison, one sentence per row: every row counts only the rounds where `out.k ≠ 3`,
takes the producer's logged start position, walks its own emitted actions, and compares each tail
slot's L1 ring against **the same unit's ring after step 3 in the same round** — so it is an
internal comparison and no baseline drift can leak in.

**Three readings, and the third is the one that decides the clamp question.**

1. **The donor really does receive nothing: 100 % on all three maps.** The check is posed *within a
   round*, because across a closed loop the two trajectories diverge and a global bit-identity is
   impossible by construction. Within a round it is exact, so there is no implementation defect.
2. **The 24 % dead-ground share is explained by the producer's starting ring, not by the tail.** The
   modal landing ring is 4 and the producer's mean ring *before* the tail begins is already
   3.25–3.40. map3 is the internal control that proves it: its producers sit at mean ring 1.737 and
   the ring ≥ 5 share collapses to **2.3 %**.
3. **The tail moves the producer by under 0.05 rings, and on map2 it moves it *inward*.** So the
   tail is **not** the drift source, and a ring clamp on slots 4/5/6 is **not worth building**: it
   would delete the 18–27 % outward entries, which are almost exactly balanced by 17–27 % inward
   ones, buying a net ring change of order 0.02 while deleting a third of the tail slots — and the
   tail is where 116–174 % of this arm's income gain is attributed. **Recorded as a negative on the
   clamp, not on the arm.**

### The residual drift is a donor-freeze effect, and it explains a previously unexplained result

The pooled +0.173 ring drift in §5.2 appears on **both** unit indices while the tail geometry above
is flat and the donor takes zero steps. The mechanism is therefore: **silencing a unit that was
walking *back* toward its anchor freezes it wherever it stands, which is on average further out than
the anchor.**

> That gives a precondition to a result that was previously unexplained. `fold_never` (all STAY)
> measured **−4.6 ± 19.0 ≈ 0**, which was read as "standing still on the peak is about as good as
> oscillating on it". **That only holds when the unit is already AT its anchor. If it is walking
> BACK to the anchor, freezing it costs.** The two observations are the same mechanism seen from two
> sides.

The complementary form — send the donor one step *toward* its anchor instead of STAY — is the one
follow-up worth trying. It is **not** built here. Note the existing rate-matched donor control on
record (`Cd1_silence_matched`, ≈ 0) does not settle it, because that control *silenced*; it did not
*steer*.

---

## 12. Boundaries carried into the conclusion, not dropped

These are limits on what the numbers above can be converted into. None of them blocks the result —
**the output of this line is gold per game, measured** — but every one of them sits between that
number and a win-rate claim.

1. **The 51.1 % baseline behind the "+17.6 pp gap" has three known biases** and is not a
   measurement: it was played by an **older published build**, challengers **self-selected**, and it
   is **unstratified**. After the published slot got 18× faster, the real starting point is
   **unknown**. So +17.6 pp is an **estimate**, and the gap this result has to close is not known to
   the precision the arithmetic implies.
2. **Every pp figure carries an unknown multiplier.** The "+40 gold ≈ +2 pp" rate uses the σ of our
   own **paired A/B** dispersion, while field-wide per-game σ is larger, so the true rate may be
   lower. An unresolved **4.7× discrepancy** in the exchange rate is on record (0.055 pp/gold pooled
   per game versus 0.245 pp/gold weighted equally per team). **Until an empirical slope is measured
   from field games, do not convert the gold figures here into pp.**
3. **Everything is self-play.** The opponent at seat 2 is our own unmodified `fd47ea6`. Transfer to
   the benchmark opponent is **unverified**. The mechanism is field-independent by construction (the
   blind trigger is computed from our own 5×5 in the same round, needs no opponent visibility and no
   snapshot), which is an argument, not a measurement.
4. **The local NPC model is over-greedy and over-central**, which inflates centre contention and
   therefore the value of any centre-local mechanism. Direction: **flatters** this candidate.
5. **The calibrated field recovers only ~75 % of the measured gradient** (measured ring1/ring5
   steepness 3.35×, uniform 1.22×, calibrated 2.51× ⇒ 74.8 % recovery, reproducing the 77 % on
   record to within sampling). This is why the two-field difference is printed on **every** row as a
   mandatory error term rather than argued as a direction: at SE ≈ 12–20 the apparatus bias is the
   same size as the effects. **Per candidate the direction differs** — it is +13.8 to +26.6 for the
   cursor form (uniform **understates** it) and −7.6 to −15.0 for the stack and `colvedge` (uniform
   **flatters** them) — so a global "the simulator is optimistic" would have been wrong in one
   direction either way.
6. **Simulator margins are gross of latency by construction.** `--dispatch fixed` fixes the action
   order, so no clock is read and no instruction cost is inside any margin. The cost term is
   separate and is given in §4. For this candidate that term is **negative** (a credit), so the
   direction of the correction is favourable — which is exactly the kind this project keeps getting
   wrong, so it is reported under **both** weightings and neither is silently adopted.
7. **Local wall-clock latency cannot resolve this change and must not be quoted.** Interleaved
   `latency_bench --mode hot` on the contest machine, 5 rounds each: `base` P50 120 ns, `cursor`
   110–120, `stack` 110–120; P90 160–280 for all three with ±60 ns run-to-run scatter. The clock has
   a 10 ns quantisation step and the host carries 127 other users. **The acceptance quantity is perf
   `cycles`, and a first, non-interleaved read of that instrument put a byte-identical null at +6.28
   cycles** — which is why §4 uses the interleaved paired protocol and why the null control is
   reported with it.

---

## 13. Recommendation, and what it is and is not

### The artifact — **`nc` = `nofold + cursor`** (superseding `ncp`; see §15.5 for the final table)

**`nc` = `nofold + cursor`.** Two members. map2 **+107.6 ± 19.6 calibrated** (uniform +106.1,
two-field **+1.5**, gate 39.1, **margin − gate = +68.4**), disjoint OOS **+76.0**, both order arms
positive (+167.7 / +47.5), classification **joint move** (opponent −1.4). Same-stream instruction
count **760.734 against base 802.804, i.e. −42.07** — the artifact is *cheaper* than the construct it
replaces. ⚠️ **Requires `asm(".space 144, 0x90")`, not the delivered 96.** Entry 0x1a50,
`mod64 == 0x10`, FP16 = 0, zero build warnings. Source sha256 (pad 144)
`37b126dfcc73dcb8af3fa9cf182f75cbbd166f8d18c4aee253400307867612be`, `.text` sha256
`aed8c68970fa47e14ae7b2825f71adb04dfbbf649574d933bd13bc87bd76d8dd`.

**Superseded: `ncp` = `nofoldpure + cursor`.** map1 **+79.5 ± 16.2**, map2 **+83.2 ± 19.8**,
same-stream **−6.37**, pad 96, source sha256
`a8d3a9696de15c3232e4e36ab8543eacfa1ff09823c844c980b156c083ead203`. It was the recommendation until
the map2 comparison landed; `nc` beats it on margin (+24.4 ± 27.8), on OOS (+76.0 vs +41.5), on the
two-field term (+1.5 vs −25.5), on classification (joint move vs co-gain) **and** on cost by 35.7
instructions. Nothing measured favours `ncp`. It is recorded here rather than deleted because it was
landed first and its hashes are on the record.

**Also equivalent, not better: `g5` = `nofold + k∈{1,5}`**, map2 **+112.3 ± 19.5**, same-stream
−38.31. It beats `nc` by **+4.7 ± 27.6**, i.e. nothing — see §15.3.

### Drop, with reasons

| dropped | reason |
|---|---|
| the 48-byte dead pad | already banked; `fd47ea6` is at `mod64 == 0x10` and a 48-byte pad would move it to `0x20` |
| `safe T2` | net definitively negative under a **measured** cost: +144.04 instructions, +42.81 cycles ⇒ −179 gold vs latency-comparable, against a gross on record of +13.4 ± 19.1 |
| `colvedge` (`hot_colv_edge`) | −16.1 ± 21.4 inside the stack and −24.9 ± 20.6 standalone, sign flips between seed bands, fires on only 12 of 1500 real platform decisions, and contributes exactly zero instructions either way |
| the tail ring clamp | the tail moves the producer < 0.05 rings and is net *inward* on map2, so the clamp would delete a third of the income-bearing slots to fix a drift the tail did not cause |
| `cursor4` (budget 4) | first build was defective; rebuilt but not A/B'd. Provisional only |

### Hold, do not drop

**`zc` = `colvedge + nofold`, the zero-cost sub-stack: +20.3 ± 11.7, gate 23.5, misses by 3.2.** It
is the only **joint move** among the sub-stacks (theirs −11.7 ± 8.3) and it is a latency **credit**
(−61.5 instructions, −6.71 cycles). Reaching its gate is now purely a matter of `n`: at SE ≤ 10.2 the
gate falls to 20.4 and it clears. That is ~n = 400 paired games per field and is the single cheapest
open experiment in the project. A run at seeds 3150–3449 is queued.

### What this is not

**It is not enough to draw level with the benchmark opponent, and it must not be reported as if it
were.** Against the convertible per-map lines: map2 needs +86 for a 50 % line and `stack` delivers
**+111.3 ± 19.6** there, `cursor` alone **+90.2 ± 21.3** — that line is reached. map1 needs +200 and
nothing here is close. Pooled with equal map weight the line is +166 and this is ~+80. So the honest
statement is: **a gate-clearing, negative-cost, mechanism-confirmed gain that reaches one of three
convertible lines.**

### If platform confirmation is wanted, here is the request

**Request:** 34 games on map2 with `ncp` (or `nc`) against the benchmark opponent, 17 per order arm.
**Binary decision it settles:** whether the +90 to +111 map2 margin measured in self-play transfers
to a real opponent, i.e. whether "reaches the map2 50 % line" survives contact. **Why 34:** at the
observed per-game margin sd of ~250 against a real opponent, 34 games resolve a +100 effect to ~2.3σ;
17 would give 1.6σ and could not change a decision. **Zero platform games were consumed by this
line**, and none should be until the artifact is landed and its construct gate re-run with the
snapshot field populated.

---

## 14. Reproduction

```bash
# baseline, hash-checked before anything is built
git show fd47ea6:src/player.cpp > /tmp/gr_highn/build/base_fd47ea6.cpp
shasum -a 256 /tmp/gr_highn/build/base_fd47ea6.cpp
# df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217

# build every variant and re-tune each pad to mod64 == 0x10 in one step (contest machine)
python3 sim/analyze_highn_rescreen.py construct --workdir <wd> \
  --arms 'null,nofold,nofoldpure,colvedge,safet2,cursor,cursorv,cursor4,\
zc=colvedge+nofold,zcp=colvedge+nofoldpure,nc=nofold+cursor6,ncp=nofoldpure+cursor6,\
stack=colvedge+nofold+cursor6,stackp=colvedge+nofoldpure+cursor6,\
loo_nocolv=nofoldpure+cursor6,loo_nofold=colvedge+cursor6,loo_nocursor=colvedge+nofoldpure' \
  --output out/construct.json

# instructions and cycles on one shared recorded stream (needs perf_event_open)
python3 sim/analyze_highn_rescreen.py icount --workdir <wd> --arms '<same>' \
  --inputs out/icount_src.bin --calls 500000 --reps 3 --output out/icount.json
CORE=5 COUNTER=cycles ROUNDS=15 CALLS=300000 ./build/interleave.sh n1 cursor stack ...

# the paired A/B: step 1 coarse, step 2 the verdict, step 3 leave-one-out
python3 sim/analyze_highn_rescreen.py ab --workdir <wd> --map map1 \
  --arms 'null,zc=colvedge+nofold,zcp=colvedge+nofoldpure,\
stack=colvedge+nofold+cursor6,stackp=colvedge+nofoldpure+cursor6' \
  --seeds 3000:3150 --oos-seeds 7000:7150 --fields uniform,centripetal \
  --jobs 24 --progress --output out/ab_step2.json

# the two positioning gates
python3 sim/analyze_highn_rescreen.py drift --workdir <wd> --arms 'cursor,stack=...' \
  --logs <map1.log>,<map2.log>,<map3.log> --seeds 3000:3008 --drift-seeds 8 \
  --field-one centripetal --output out/drift.json
python3 sim/analyze_highn_rescreen.py tail  --workdir <wd> --arms 'cursor,stack=...' \
  --logs <map1.log>,<map2.log>,<map3.log> --output out/tail.json

# behavioural divergence on real platform inputs
python3 tests/pair_diff.py <wd>/base.so <wd>/ncp.so <map1.log> <map2.log> <map3.log>
```

Host build for the arm64 development machine (no AVX2, guarded scalar fallback, `colv` is *not*
consumed on that path so `colvedge` is a no-op there and must be measured on x86):
`clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include /tmp/gr_path/shim.h`.

**Files this line authored, and the only ones it wrote:** `sim/highn_variants.py`,
`sim/analyze_highn_rescreen.py`, `sim/reports/highn_rescreen.md`, `sim/reports/highn_rescreen.json`.
`src/player.cpp`, `src/INFRA.md`, `src/CHANGELOG.md`, `AGENT.md`, `tests/*`, `sim/engine.py`,
`sim/scenario.py`, `sim/abi.py` and every pre-existing report were **not modified**, and no `git`
write of any kind was performed.

## 15. Two follow-on candidates derived from §11's donor-freeze finding, and a refuted ceiling

Added after the sections above, at the owner's request, once the drift gate passed. Both are
consequences of §11: the reallocated *tail* is geometrically clean, so the residual drift is on the
**donor** side.

### 15.1 Candidate A — `k` ∈ {1,5} instead of {0,6}: leave the donor one step home

If freezing a unit that was walking back to its anchor is what costs, then donating **all three**
slots is over-donation. Leave the donor one step and give the producer five. The retained step needs
no new code: **a blind unit's target already *is* its anchor, so its own first LUT action is already
the step toward the anchor.** This inverts `fold_tour`'s cause instead of repeating it — that arm
died leaving the peak, this one returns to it.

Slot arithmetic, both directions, no slot left uninitialised: when unit 0 donates (`k = 1`) it keeps
slot 0, which already holds its own first action, and unit 1's head slides 3,4,5 → 1,2,3 with its
tail on 4,5. When unit 1 donates (`k = 5`) unit 0's head stays on 0,1,2 and its tail lands on 3,4 —
but slot 3 holds unit 1's first action, so that value is saved and rewritten into slot 5.

**Cost: −0.62 instructions same-stream.** `.rodata` grows only +288 B because the LUT widens to 5
rather than 6. Pad stays 96, `mod64 == 0x10`, FP16 = 0.

### 15.2 Candidate B — widening the trigger. **Closed, and this is the mechanism's ceiling.**

The reallocation machine is already paid for, so widening its trigger from "donor is blind" to "the
units' reachable value is asymmetric" looked nearly free. It is not, and it does not pay.

**First: the good measure is not free.** The count of reachable `v > 2` cells requires a `popcount`
inside the **unrolled AVX row loop**, which breaks the vectorised store pattern the delivered scan
depends on:

| ladder D level | predicate | same-stream instr/call | Δ vs base 802.804 |
|---|---|---:|---:|
| D0 | `value(donor) == 0`, i.e. blind | 890.324 | **+87.52** |
| D1 | `value(donor) ≤ 2` | 892.225 | **+89.42** |
| D2 | `value(donor) < value(other)/3` | 889.610 | **+86.81** |
| D3/D4 | always to the richer | 885.432 | **+82.63** |

Condition and comparison: each is `nofoldpure` plus that predicate, replayed against the shared
stream and differenced against the base build of the same session. **No games were spent on ladder
D** — it is excluded on cost alone.

**Second: rebuilt on a free measure, the income still falls.** `bv` is the winner of the *existing*
row-min reduction, encoding `(ring_priority << 5) | window_index`, with `0xFFFF` meaning no reachable
rich cell — so `bv` is a free ordinal proxy for reachable value and `bv == 0xFFFF` **is exactly
blind**. Ladder E costs one saved int and one compare; paired with `nofold` (−61.01 on its own) every
level lands **below** the same-stream baseline.

map2, 50 tune + 50 disjoint OOS seeds, both order arms ⇒ **100 paired games per arm per field per
band**, all arms sharing the same baseline games. Firing rate from replaying each `.so` over the real
map2 platform log `game_176022`, 500 rounds.

| level | trigger | firing / 500 | rate | **margin (cal)** | SE | uniform | two-field | gate | m − gate | OOS | **gold per firing** | Δinstr | Δ mean ring | our scoring Δ | their scoring Δ | class |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `null` | — | 0 | 0 % | **+0.0** | **0.0** | +0.0 | 0.0 | 0.0 | +0.0 | +0.0 | — | −0.00 | — | 0 | 0 | 100/100 bit-identical |
| `ncp` | blind, `nofoldpure` | 202 | 40.4 % | +83.2 | 19.8 | +108.7 | −25.5 | 39.6 | +43.5 | +41.5 | 0.412 | −6.37 | +0.057 | −16.7 | +1.0 | co-gain |
| **`nc`** | blind, `nofold` | 207 | 41.4 % | **+107.6** | 19.6 | +106.1 | +1.5 | 39.1 | **+68.4** | +76.0 | **0.520** | **−42.07** | **−0.047** | −13.9 | −1.7 | **joint move** |
| `a5` | blind, `k` ∈ {1,5} | 202 | 40.4 % | +104.2 | 22.4 | +96.9 | +7.3 | 44.9 | +59.3 | **+90.0** | 0.516 | **−0.62** | +0.027 | −9.0 | −4.6 | **joint move** |
| **`g5`** | blind, `nofold` + `k` ∈ {1,5} | ~207 | ~41 % | **+112.3** | 19.5 | +115.7 | −3.5 | 38.9 | **+73.3** | +76.0 | ~0.54 | −38.31 | — | −9.5 | −2.3 | **joint move** |
| `f2` | E2: donor's target in outermost fifth | 258 | 51.6 % | +101.3 | 19.1 | +111.4 | −10.1 | 38.2 | +63.1 | +97.7 | 0.393 | −39.16 | +0.081 | −19.3 | −0.4 | co-gain |
| `f1` | E1: donor's target in outer half | 320 | 64.0 % | +84.1 | 21.0 | +70.4 | +13.7 | 42.0 | +42.0 | +61.1 | 0.263 | −37.64 | +0.071 | −32.7 | +1.3 | co-gain |
| `f34` | E3/E4: **always to the better-target unit** | 425 | 85.0 % | **+13.8** | 19.0 | −1.0 | +14.9 | 38.1 | **−24.2** | +24.1 | **0.032** | −35.53 | +0.090 | **−57.7** | **+2.8** | co-gain |

Condition and comparison, one sentence per row: each arm is that predicate built on `nofold` or
`nofoldpure`, compared against the unmodified `fd47ea6` on the identical map2 seed, order arm and
field model; firing rate and `k`-histogram come from a *separate* replay over a real platform log so
they do not depend on the simulator field at all; mean-ring deltas are 6 paired map2 games against
the same-seed baseline.

> ⭐ **The ceiling answer. Gold per firing collapses 16× — 0.520 → 0.393 → 0.263 → 0.032 — and the
> TOTAL falls monotonically the moment the trigger widens past blind: +107.6 (41 %) → +101.3 (52 %) →
> +84.1 (64 %) → +13.8 (85 %). Marginal value falls faster than firing rate rises, immediately.
> There is no turning point beyond the shipped one: the blind trigger IS the optimum, and the
> "+265 linear ceiling" extrapolation is refuted — it extrapolated from the zero-opportunity-cost
> extreme.**

**And the collapse mechanism is measured, not inferred, which is what makes it durable: mean ring
rises monotonically with permissiveness (`nc` −0.047 inward → `f34` +0.090) while our own scoring
rounds fall monotonically (−13.9 → −57.7) and the opponent's rise (+2.8) at full widening. So at
full widening we silence donors that were heading for real gold, destroy our own hit rate, and hand
the opponent ground — the `fold_tour` mechanism, appearing on cue and monotone in the knob.**

### 15.3 The combination: `nofold + k∈{1,5}`, and why it is not an upgrade

| contrast | difference | SE | σ |
|---|---:|---:|---:|
| `nc` − `ncp` — what `nofold` contributes | +24.4 | 27.8 | 0.88 |
| `a5` − `ncp` — what `k` ∈ {1,5} contributes | +21.0 | 29.9 | 0.70 |
| **`g5` − `nc` — does the combination beat its BETTER parent?** | **+4.7** | **27.6** | **0.17** |
| `g5` − `a5` — against the other parent | +8.1 | 29.7 | 0.27 |
| naive sum of the two contributions | +45.4 | 41.0 | — |
| measured combination against the common baseline | +29.1 | 27.8 | — |
| **interaction** | **−16.3** | **±49.5** | 0.33 |

Condition and comparison: all four arms ran on the **same** map2 seeds, order arms and field models
against the same baseline games, so each contrast is a like-for-like difference of two paired margins
and its SE is the quadrature sum.

**The two members are largely the same gain arriving by two routes.** Measured against the *common
baseline* the combination looks like +29.1; measured against its **better parent** the increment is
**+4.7 ± 27.6**, i.e. nothing. Reporting the +29.1 would double-count `nofold`. **Interaction is
again indistinguishable from zero, and again the total is limited not by cancellation but by there
being one effect rather than two** — the same shape as §9.

### 15.4 The crossing margin, reported as its own quantity

A threshold claim needs an error bar on the **crossing margin**, not on the margin.

| arm | map2 margin | SE | requirement | **crossing margin** | σ | claimable? |
|---|---:|---:|---:|---:|---:|---|
| `stack` | +111.3 | 19.6 | +108 | **+3.3 ± 19.6** | 0.17 | **no** |
| `nc` | +107.6 | 19.6 | +108 | **−0.4 ± 19.6** | −0.02 | **no** |
| `g5` | +112.3 | 19.5 | +108 | **+4.3 ± 19.5** | 0.22 | **no** |

**Raising `n` cannot rescue these.** At n = 600 the SE falls to ≈ 9.8 and `g5`'s crossing margin
becomes +4.3 ± 9.8 = 0.44σ — still not claimable. The compute was therefore not spent.

> **So the honest statement for every arm here is "statistically indistinguishable from map2's
> even-record requirement", which is NOT "reached it". No arm in this work makes a claimable crossing
> of any convertible per-map line: the best crossing margins are +4.3 ± 19.5 and +3.3 ± 19.6 on
> map2, both under 0.25σ, and map1 (+294) and map3 (+155) are not close.**

### 15.5 The final artifact

**`nc` = `nofold + cursor`.** Simplest, cheapest (−42.07 same-stream instructions), and
statistically indistinguishable from `g5` on every axis; the one column where `g5` leads is opponent
suppression (theirs −23.9 ± 12.0 versus −1.4), which does not show up in the margin.

| arm | source sha256 | `.text` sha256 | entry | mod64 | pad | `.text` | FP16 |
|---|---|---|---|---|---:|---:|---:|
| `fd47ea6` base | `df270cd3…1bae217` | `a24f33d4172066346789045f7119fe496122b457f5a824f1e1b89cb67513a854` | 0x1a50 | 0x10 | 96 | 5267 | 0 |
| `ncp` | `a8d3a969…83ead203` | `264d1ceb3cf81192acb185bf7577c29c468a17feb3d7ea21902969fd51aa989b` | 0x1a10 | 0x10 | 96 | 5235 | 0 |
| **`nc`** | **`37b126dfcc73dcb8af3fa9cf182f75cbbd166f8d18c4aee253400307867612be`** | **`aed8c68970fa47e14ae7b2825f71adb04dfbbf649574d933bd13bc87bd76d8dd`** | 0x1a50 | **0x10** | **144** | 5283 | 0 |
| `g5` | `1c3861ee…d05db6e04` | `cd1ed9367404f138b34b90b1ad76f88822c24b59a222f7fdf62a5c9812085184` | 0x1a50 | 0x10 | 144 | 5283 | 0 |

⚠️ **`nc` requires `asm(".space 144, 0x90")`, not the delivered 96.** The source sha256 above is the
pad-144 text; a rebuild that hashes to it and whose `.text` hashes to `aed8c689…` is byte-identical
to the binary every number in §15 was measured on.

### 15.6 One process failure, recorded because its silent form would have been undetectable

A `construct` run overwrote `base.so` **while 24 A/B workers were `dlopen`-ing it**, and the run died
with `file too short`, losing an `nc` measurement at 1001/4800 games. All later builds went to an
isolated directory. **The reason this is worth recording is not the lost hour: it is that the loud
failure was luck.** Had the rebuild completed a millisecond earlier, half the paired comparisons in
that run would have been measured against one build and half against another, with no error, no
warning and a perfectly plausible-looking margin. **Never write a build directory an A/B is reading.**

## 16. The map1 split, and the final artifact after it

Run after §15 to settle `nc` versus `ncp` on map1 and to place `g5` on a second map. map1, 60 tune
seeds (3000–3059) + 60 disjoint OOS (7000–7059), both order arms ⇒ **120 paired games per arm per
field per band**, all three arms sharing the identical baseline games.

| arm | calibrated | SE | uniform | two-field | gate | m − gate | **OOS** | first / second | ours / theirs | class | Δinstr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|
| `ncp` | +73.8 | 21.3 | +59.9 | +14.0 | 42.7 | +31.2 | +42.5 | +95.8 / +51.9 | +71.5 / −2.4 | joint move | −6.37 |
| `nc` | +83.6 | 22.0 | +87.7 | −4.1 | 44.0 | +39.6 | +52.4 | +133.6 / +33.6 | +99.2 / +15.6 | co-gain | **−42.07** |
| **`g5`** | **+119.0** | **18.0** | +107.4 | +11.6 | 35.9 | **+83.1** | **+96.3** | +159.8 / +78.3 | +98.1 / **−20.9** | **joint move** | −38.31 |

Condition and comparison, one sentence per row: each arm is that source variant compared against the
unmodified `fd47ea6` on the identical map1 seed, order arm and field model, with all three arms
sharing one set of baseline games so the differences between rows are like-for-like.

### `g5` against its better parent, on both measured maps

| map | `g5` | `nc` | **g5 − nc** | SE | σ |
|---|---:|---:|---:|---:|---:|
| map1 (n = 120) | **+119.0 ± 18.0** | +83.6 ± 22.0 | **+35.4** | 28.4 | 1.25 |
| map2 (n = 100) | **+112.3 ± 19.5** | +107.6 ± 19.6 | +4.7 | 27.6 | 0.17 |
| both maps, equal weight | **+115.7 ± 13.2** | +95.6 ± 14.7 | **+20.1** | 19.8 | 1.02 |

⚠️ **`g5` − `nc` is 1.02σ pooled and is not significant; that is stated rather than smoothed.** The
reasons to prefer it anyway are structural rather than statistical: it is **sign-consistent on both
maps in both seed bands**, it is **joint move on both maps** where `nc` is co-gain on map1, it has the
**lowest SE of any arm measured** (18.0), and it costs **3.76 more instructions**. If the conservative
choice is wanted, `nc` remains defensible on cost.

### The final artifact

| | `g5` (recommended) | `nc` (conservative alternative) |
|---|---|---|
| members | `nofold` + `k` ∈ {1,5} | `nofold` + cursor, `k` ∈ {0,6} |
| source sha256 (pad 144) | `1c3861eeb9d5a719e8b9121691297b50ba6a5dadf62a87be97f1f97d05db6e04` | `37b126dfcc73dcb8af3fa9cf182f75cbbd166f8d18c4aee253400307867612be` |
| `.text` sha256 | `cd1ed9367404f138b34b90b1ad76f88822c24b59a222f7fdf62a5c9812085184` | `aed8c68970fa47e14ae7b2825f71adb04dfbbf649574d933bd13bc87bd76d8dd` |
| entry / mod64 / pad | 0x1a50 / **0x10** / **144** | 0x1a50 / **0x10** / **144** |
| `.text` / FP16 / warnings | 5283 / 0 / none | 5283 / 0 / none |
| same-stream instr | **764.496 ⇒ −38.31** | **760.734 ⇒ −42.07** |
| map1 / map2 margin | **+119.0 ± 18.0 / +112.3 ± 19.5** | +83.6 ± 22.0 / +107.6 ± 19.6 |

⚠️ **Both require `asm(".space 144, 0x90")`, not the delivered 96.** The source sha256 above is the
pad-144 text; a rebuild that hashes to it, and whose `.text` hashes to the value beside it, is
byte-identical to the binary these numbers were measured on. **Two independent cross-checks of this
pipeline landed exactly:** the baseline `.text` sha256 `a24f33d417206634 6789…` matches committed
HEAD, and `ncp`'s `.text` sha256 `264d1ceb3cf81192acb185bf7577c29c468a17feb3d7ea21902969fd51aa989b`
matches an independent rebuild by the owner.

---

## 17. The single-paragraph close

**The `+150` gate was killing a real mechanism, and moving acceptance into the simulator at high `n`
recovered it — but the pool it was supposed to unlock does not exist.** Of five candidates, one is a
no-op already banked (the pad), one is negative under a measured cost (`safe T2`), two are
indistinguishable from zero in both directions on fresh seeds (`hot_colv_edge`, the stand-on-gold
fold), and one is real: arm C's step reallocation. **Additivity is not the problem — interaction is
indistinguishable from zero in six separate tests — the problem is that there was only ever one
effect to add.** That one effect is now worth **+112 to +119 gold/game on map1 and map2 and +90 on
map3, at 38 instructions and ~9 cycles below the delivered construct**, because the cursor form
compressed the implementation from the 62–116 instructions that killed it to **+12.75**, and because
`nofold` pays for even that. It clears `max(2SE, |calibrated − uniform|)` on every map with
out-of-sample sign agreement, it is a **joint move** on both maps measured at high `n`, its donor
never moves, its tail geometry is clean, and its zero-signal control is byte-identical on 300 of 300
games. **And it does not draw level with T-1: the best crossing margin measured anywhere in this work
is +4.3 ± 19.5 against map2's requirement, 0.22σ, which is "statistically indistinguishable from the
line", not "past it" — and no amount of additional `n` changes that, because the effect, not the
precision, is what is short.** The widened trigger that looked like 3.3× of headroom was measured and
turns over immediately: gold per firing collapses 16× and the total falls monotonically from the
shipped trigger onward, so **the blind trigger is the optimum and the +265 ceiling was an
extrapolation from the zero-opportunity-cost extreme.** That is the honest end of this line.
