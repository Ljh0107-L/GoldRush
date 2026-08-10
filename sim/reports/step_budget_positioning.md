# Step budget and positioning: four arms, one apparatus

> Baseline `f18064c` (`git show` → `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`),
> host build `clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include shim.h`, guarded scalar fallback
> (no AVX2 on the arm64 host). **Zero platform games consumed.** Driver `sim/analyze_step_budget.py`;
> artifacts in `/tmp/gr_step/`. All A/B are same-seed paired, `dispatch="fixed"`, both order arms run
> and reported separately. **Judged on `margin_delta` = Δ(ours − theirs)** (`analyze_step_budget.py:1455`),
> not on our net alone.
>
> **Bias labels that apply to every number below**: (i) the opponent is either our own frozen `.so`
> (self-play) or `probeobs`, a deliberately-losing observation probe — neither is `T-1`, so nothing here
> is a transfer claim; (ii) the local NPC model is over-greedy and over-central, which inflates centre
> contention and therefore inflates the value of any centre-local mechanism; (iii) `fixed_costs` force
> the dispatch order, so every margin is **gross of the instruction cost** the change would pay.

## 1. Results

`margin_delta` in gold/game, mean ± SE (σ). "first/second" are the two order arms, always separate.

| # | arm | tune margin | OOS margin | first / second (OOS) | ruling |
|---|---|---:|---:|---:|---|
| 1 | B2 bracket `(6,8)/(10,8)` | **−80.7 ± 37.0** (−2.18σ) | — | — | fails |
| 2 | B2 diagonal `(6,6)/(10,10)` | **−9.1 ± 49.6** (−0.18σ) | **−20.3 ± 39.5** (−0.52σ) | +24.2 / −64.9 | fails on margin (net +93.3, 2.83σ) |
| 3 | B2 diagonal `(6,10)/(10,6)` | −50.8 ± 49.6 (−1.02σ) | **+14.5 ± 55.3** (+0.26σ) | +81.0 / −51.9 | fails, margin ≈ 0 (net +111.1, 2.80σ) |
| 4 | C `k5_stay` | **+147.0 ± 44.4** (3.31σ) | **+120.7 ± 37.4** (3.23σ) | +138.3 / +103.1 | passes gates |
| 5 | C `k6_truncate` | **+203.2 ± 45.4** (4.48σ) | **+194.4 ± 49.8** (3.91σ) | +312.8 / +76.1 | passes gates, **red flag fired** |
| 6 | D (B2 diag × C `k5_stay`) | +86.4 ± 50.5 (1.71σ) | +70.3 ± 52.9 (1.33σ) | +148.8 / −8.2 | **not additive** — below C alone |
| 7 | Cd silence-only, **unmatched** | **−374.5 ± 49.0** (−7.64σ) | — | −541.6 / −207.4 | strongly negative — **but see §2.4** |
| 8 | Cd1 silence-only, **rate-matched** | −11.8 ± 47.5 (−0.25σ) | +23.1 ± 37.7 (+0.61σ) | +1.4 / +44.8 | **zero**: the donor side is free |
| 9 | Cidle (fully-idle trigger only) | −0.2 ± 10.8 (−0.02σ) | — | — | **+0.0**: the pre-registered free pool is worth nothing |
| 10 | **Crand `k6`** (donor picked at random) | −85.5 ± 43.9 (−1.95σ) | **−118.7 ± 40.4** (−2.94σ) | −167.5 / −69.9 | **control: the gain inverts** |
| 11 | **Cflip `k6`** (the *sighted* unit silenced) | −425.8 ± 53.3 (−7.99σ) | **−382.7 ± 56.6** (−6.76σ) | −588.4 / −177.0 | **control: the gain collapses** |
| 12 | **Clut `k6_stay`** (faithful, LUT-widened) | **+80.1 ± 38.9** (2.06σ) | **+215.5 ± 41.3** (5.22σ) | +271.2 / +159.8 | reproduces row 5 **without** re-planning |
| 13 | Clutrand `k6` / Clutflip `k6` | −105.7 / −474.6 | **−95.7 / −419.1** | −184.6·−537.7 / −6.8·−300.5 | same controls, same collapse |

**Condition and comparison, one sentence per row** (delivery gate; a row without this line is not deliverable):

1. Measured against `probeobs` on tune seeds 1000–1007 (8 seeds × 2 order arms = 16 games), map1, compared against the unmodified `f18064c` run on the identical seed and order arm.
2. Tune column is vs `probeobs` on seeds 1000–1007 (16 games); OOS column is **self-play** on disjoint seeds 2000–2011 (24 games) — *the two columns are different opponents and are not a tune-vs-OOS pair*; both compare against the unmodified baseline on the same seed and order arm. (Self-play on the tune seeds gives −26.4 ± 40.1, same sign.)
3. Tune column is self-play on seeds 1000–1009 (20 games), OOS column is self-play on seeds 2000–2011 (24 games), both against the baseline on the same seed and order arm.
4. Tune column is vs `probeobs` on seeds 1000–1007 (16 games), OOS column is self-play on seeds 2000–2011 (24 games); both against the baseline on the same seed and order arm — again **different opponents in the two columns**.
5. Same as row 4 (tune = `probeobs`/16 games, OOS = self-play/24 games); a self-play re-measurement of the *same arm* on the tune seeds gives only **+76.1 ± 45.8 (1.66σ)**, so the "tune +203" and "OOS +194" are not a stable pair — they are two opponents.
6. Both columns self-play (tune = seeds 1000–1009, 20 games; OOS = seeds 2000–2011, 24 games), compared against the baseline **and** against arm C alone on the same seeds — it loses to C alone in both.
7. Self-play, tune seeds 1000–1011 (24 games), compared against the baseline; **fires on every blind unit-round — 796 silenced unit-rounds on the single game instrumented (seed 2000, we-first), including 324 rounds in which *both* units are blind and we therefore do nothing at all — against the 226 unit-rounds arm C touches on the same game**, so it is not a decomposition term for arm C.
8. Self-play, tune seeds 1000–1011 and OOS seeds 2000–2011 (24 games each), compared against the baseline; **identical trigger set to arm C** (exactly one unit blind, 226–245 silenced unit-rounds/game), donor pinned to `STAY`, `k` left at 3, no extension.
9. Self-play, tune seeds 1000–1009 (20 games), compared against the baseline; trigger restricted to donors that planned **zero** real moves, which fires **0.7–0.9 rounds/game** — the pool behind the pre-registered "+83…106" ceiling is measured in this apparatus at **5.34 (first) / 2.67 (second) steps/game** of fully-idle blind planning, and at 114.3 (first) / 65.2 (second) steps/game of *realised* zero-effective-move stepping, not 148.
10. Self-play, tune and OOS (24 games each), compared against the baseline; **identical trigger rounds, identical total budget of 6, identical count of silenced steps** to row 5 — only *which* unit is silenced changes, by a deterministic per-(seed, round) coin.
11. Same construction as row 10 except the donor is always the **sighted** unit, so the blind unit receives all six steps; the units are exactly as asymmetric as in row 5.
12. Self-play, tune and OOS (24 games each), compared against the baseline; producer's extra slots come from a **widened constexpr LUT** (`fact[7][7][6]`) reading the same plan for the same target, no second scan and no re-plan; the widening is a **proven pure suffix** — `fact_w[dr][dc][:3] == fact_3[dr][dc]` for all 49 entries, asserted at import.
13. Same construction as rows 10–11 applied to row 12's mechanism, self-play, tune and OOS (24 games each), compared against the baseline.

## 2. The `k > 4` red flag: verdict

**Pre-registered flag**: value should saturate at k=4–5 because the 5×5 scan makes Manhattan 4 the
farthest targetable cell. **Observed**: margin rises monotonically to k=6. Five measurements resolve it.

**2.1 Steps 4–6 do collect, and they collect the whole gain.** Per-action-slot attribution taken from
the engine's own ordered `movements`/`pickups` event lists, gated by rebuilding the official log and
matching `log_sha256` against plain `run_game` (**gate passes on all 96 attributed games**); every
per-unit total cross-checked against the engine's own `UnitState.pickup`. Self-play OOS seeds
2000-2011, 24 games, gold/game on the producer's slots, compared against the same baseline runs whose
three slots yield 369.0 / 361.2 / 320.4 gold/game summed over both units:

| arm | slot 1 | 2 | 3 | **4** | **5** | **6** | tail (4–6) | total income Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C `k4_stay` | 203.5 | 185.6 | 177.0 | **122.7** | — | — | +122.7 | +87.2 |
| C `k5_stay` | 186.5 | 179.9 | 161.4 | **125.8** | **79.4** | — | +205.2 | +118.0 |
| C `k6_truncate` | 176.9 | 174.4 | 172.1 | **131.7** | **83.0** | **73.8** | +288.5 | +196.7 |
| Clut `k6_stay` | 174.7 | 185.7 | 173.6 | **99.8** | **48.8** | **23.4** | +172.0 | +148.7 |

The tail collects **116–174 % of the arm's whole income gain**. So the mechanism is what it claims to
be — *more steps collect more gold* — and the gain does **not** come from somewhere else.

**2.2 Why it does not saturate: the residue, not the window.** Reach *does* saturate exactly as
predicted — the re-plan's target lay outside the producer's own round-start 5×5 in **0 of ~3 280
re-plans** (0.0/game across 24 games), and the faithful variant has no re-plan at all. What keeps
paying is that pickup is **proportional (65 %), not total**: entering a cell takes `ceil(0.65 v)` and
leaves `floor(0.35 v)`, and the LUT's early-arrival fold oscillates the unit back onto its own target.
Measured share of tail gold collected on a cell the same unit **already entered that round**: 25.8 %
(C k4) → 36.7 % (C k6), and **62.3 % → 77.9 %** for the faithful LUT variant, against 13.7 % in the
baseline. Marginal yield per extra slot decays geometrically at ≈0.48× (99.8 → 48.8 → 23.4), so it
never reaches zero and there is no saturation point. **The prediction assumed the value came from
reach; it comes from residue.** The pre-registered architectural ceiling on `k` was right about reach
and wrong about value.

**2.3 The free-versus-paid split: the +106 ceiling was computed on the wrong pool.** The pool behind
it — unit-rounds with **zero effective moves** — is 114.3 (first) / 65.2 (second) steps/game here with
income identically 0.000, and the arm that targets the *predictable* part of it (`Cidle`, row 9, donors
that planned zero real moves) fires 0.7–0.9 rounds/game and measures **+0.0 ± 11 (n=20)**. The gain
therefore comes **entirely from the paid-looking pool**: in arm C's own donor pool of 231.7 rounds/game,
the planned-real-move histogram is 0/1/2/3 = **0.2 % / 14.9 % / 24.6 % / 60.3 %**, so 99.8 % of donors
planned at least one real move. It "materially exceeds +106", and the excess is explained: a blind
unit's planned moves realise **zero income 83 % of the time** — 191 of the 232 donor rounds/game had the
donor earning 0 in the paired baseline run — so the blind trigger taps **191 zero-income donor
rounds/game where the idle trigger taps 0.5**. Blindness is a far better free-step detector than
idleness, and the pre-registered ceiling priced the wrong pool. The opportunity cost is then measured
experimentally rather than modelled — see 2.4. *(The paired-baseline counterfactual is an estimate
only: the two closed loops diverge after the first reallocation, which is exactly why 2.4 matters.)*

**2.4 The donor side is free, and the −374.5 was a volume artefact.** `Cd_silence_only` (row 7)
silenced **796 unit-rounds** on the instrumented game, including 324 rounds in which *both* units are
blind and we therefore do nothing at all; arm C touches 226 on the same game. Rate-matched to arm C's
own trigger set, silencing costs **+23.1 ± 37.7 (OOS) / −11.8 ± 47.5 (tune)** — zero. The clean
experimental decomposition is therefore `margin(C k6) = margin(matched silencing) + margin(extension)`
≈ `+23 + +192`.

**2.5 Role specialisation is refuted.** Rows 10–11 and 13 hold the trigger rounds, the total budget of
6, the number of silenced steps and the degree of asymmetry all fixed, and change only *which* unit is
silenced. Picking the donor at random does not shrink the gain, it **inverts** it (+215.5 → −95.7);
silencing the sighted unit **destroys** it (→ −419.1), and the blind unit given six steps collects
4.6 gold/game from 500 effective extension steps. Random sits within 1σ of the midpoint of the two
(−101.8 predicted, −95.7 measured), as a 50/50 mixture should. **`k` is not an asymmetry knob whose
value comes from asymmetry; it is a reallocation knob whose value comes entirely from correctly
identifying the unit that cannot use its steps.**

## 3. Three weaknesses, stated

1. **Everything is self-play or `probeobs`; transfer to `T-1` is unverified.** The winning mechanism is
   re-milking the 35 % residue of cells we have already harvested, which is precisely the mechanism
   most exposed to an opponent that sweeps rather than mirrors us, and the local NPC model is
   over-greedy and over-central, which favours centre-local re-milking. No unknown-map run was done.
2. **`C_k4_stay` is seed-unstable and moved the wrong way.** On our net it is **tune +7.2 ± 24.7
   (0.29σ) versus OOS +80.2 ± 29.7 (2.70σ)** — an *increase* out of sample, the opposite of the usual
   in-sample shrinkage, which means the tune estimate was not a ceiling and neither number can be
   used for pricing. `Clut_k5_stay` shows the same instability with the opposite sign (tune −16.5,
   OOS +94.1).
3. **The red flag fired and the arm as measured is an apparatus artefact.** `truncate`'s 6-step
   producer obtains planning capability the delivered build does not have (§4), so the measured arm is
   not transferable as measured; the faithful replacement is measured here but has not been built in
   `src/`, and its cost is estimated from the instruction audit, not measured on a real build.

## 4. `truncate` semantics, and what the apparatus actually changed

**What `truncate` is.** `blind_tail="truncate"` gives the donor the **first `6 − budget` actions of its
own plan** (`sim/analyze_step_budget.py:699`); `"stay"` gives it `STAY`s instead. At `k=6` both are the
empty tuple, so **`C_k6_truncate` and `C_k6_stay` are the same arm** — verified identical net and
opponent net on all 20 paired games in `/tmp/gr_step/ab_self_tune_C.json`. The label only carries
information at k=4 and k=5 (where truncate leaves the donor stepping out without returning, which is
why `k5_truncate` and `k5_stay` differ: OOS +157.5 vs +120.7).

**The 6-step producer does obtain planning capability beyond the delivered 3-step LUT.** `SLut` is
`uint8_t fact[7][7][3]` (`src/player.cpp:188`) and `out.k = 3` is a constant (`:524`) — there is no
4th, 5th or 6th action anywhere in the delivered algorithm. The apparatus manufactures one by
forward-simulating the producer's own triple on its fogged view (`walk_forward`) and then
**re-entering `plan_unit` from the mid position** — a second 5×5 scan plus a second target selection.
No fog is broken (the re-plan reads the round-start filtered grid, and its target was inside the
producer's own round-start 5×5 in 100 % of cases), but it **is an algorithm change, not a budget
change**, and it violates the separation constraint. Priced from `sim/reports/instruction_phase_audit.md`
(scan 389.8 for two units, target 101.2, `blk` 39.4, steer/checks/self-heal 231.5, on 894.216
instr/call): a re-plan is ≈381 instructions plus ≈60 for the forward walk, ≈441 when it fires, ≈200
instr/call at the measured 45 % firing rate ⇒ **≈320 gold/game at 1.6 gold/instruction**, which
consumes the entire +194 gain and independently reproduces the ≈314 gold already recorded for the 7×7
alternative in `sim/reports/rikka_strategy.md` §5.2(b).

**A faithful budget-only version is possible, and it is the one that should be priced.** Widening the
same constexpr table to `fact[7][7][w]` and reading column 3.. of the **same plan for the same
target** is a proven pure suffix of the delivered triple, needs no second scan, and measures
**+215.5 ± 41.3 (5.22σ) OOS / +80.1 ± 38.9 (2.06σ) tune, both order arms positive in both seed sets**
(row 12); its k=4 and k=5 forms are weaker (OOS +98.8 ± 49.4 and +94.1 ± 45.5), so width 6 is the pick. Cost: +441 B `.rodata` (+147 B if the waypoint gate is not widened) and ≈22 instructions per
call — 3 extra table reads, 3 extra `blk` bit-tests, a 4–6-instruction blind test the selector already
computes, and one conditional `out.k` ⇒ **≈35 gold/game**. Ten times cheaper than the re-plan and no
worse on margin.

## 5. Shippability

**Arm C is shippable-only-in-a-smaller-form, and the form measured is not it.**

- `C_k6_truncate` / `C_k5_stay` as measured: **withdrawn**. They are an algorithm change wearing a
  budget change's label, and at ≈320 gold/game of implementation cost they are net negative even at
  face value.
- `Clut_k6_stay` (LUT widening + `k∈{0,6}` when exactly one unit is blind): **the only candidate that
  survives**, at +215.5 ± 41.3 OOS gross, ≈35 gold/game of cost, both order arms positive on both seed
  sets. It must not be shipped on this evidence alone: it needs an unknown-map run and a run against a
  non-mirror opponent, because its mechanism is residue re-milking and weakness 1 bites hardest there.
- Arm B is closed. Arm D is not additive and is closed. `Cd_silence_only`'s −374.5 should not be cited
  again: it is a trigger-volume artefact, and the rate-matched control is zero.
