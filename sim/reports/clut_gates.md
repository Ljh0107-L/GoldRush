# Three gates on `Clut_k6_stay`, and one reopened door that stayed shut

> Baseline `f18064c` (`git show` → `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`).
> The worktree `src/player.cpp` (delivered `fd47ea6`) was **never built** — every artifact here is a
> patch of `git show f18064c:src/player.cpp`, verified byte-identical to the pin before patching.
> **Zero platform games consumed.** Machine-readable companion: `sim/reports/clut_gates.json`.
> Judged on **margin** = Δ(ours − theirs), never on net. Both order arms always separate.
>
> **Bias labels for every number**: (i) all closed-loop A/B are **self-play** against our own frozen
> `.so` — not `T-1`, so nothing here is a transfer claim; (ii) the local NPC model is over-greedy and
> over-central, which inflates the value of any centre-local mechanism; (iii) `fixed_costs` force the
> dispatch order, so simulator margins are **gross of** the instruction cost measured in check 2;
> (iv) the calibrated field of check 3 recovers 2.58× of the measured 3.35× ring1→ring5 steepness
> (77 %), so it under-states the real gradient and its corrections are therefore floors, not ceilings.
>
> **Verdict: `Clut_k6_stay` does not survive. Check 2 kills it. B2 stays closed. The search space on
> this line is exhausted.**

## Check 1 — the 2.3σ tune/OOS divergence is unexplained, so the low end stands

Two disjoint seed sets, 24 games each (12 seeds × 2 order arms), self-play, map1, same-seed paired
against the unmodified baseline. Supply-side rows come straight off `ScenarioGenerator` with no
strategy in the loop; realised rows come from the per-action-slot attribution, whose fidelity gate
(reconstructed `log_sha256` == plain `run_game`) passes on all 48 games.

| mechanism variable | TUNE 1000–1011 | OOS 2000–2011 | OOS − TUNE |
|---|---:|---:|---:|
| **margin_delta (the outcome)** | **+80.1 ± 38.9** | **+215.5 ± 41.3** | **+135.4 (2.39σ)** |
| trigger rate: fired rounds/game | 229.4 ± 3.2 | 226.2 ± 2.4 | −3.2 (−0.80σ) |
| gold generated/game (supply) | 9758.0 ± 64.2 | 9630.5 ± 72.5 | −127.5 (−1.32σ) |
| `floor(0.35v)` residue pool generated | 2867.9 ± 18.9 | 2834.3 ± 23.6 | −33.6 (−1.11σ) |
| central-9×9 gold generated | 4859.8 ± 46.7 | 4705.4 ± 51.5 | **−154.3 (−2.22σ)** |
| mean generated pile value | 7.675 | 7.710 | identical shape |
| tail entries/game | 448.2 ± 8.4 | 436.5 ± 6.4 | −11.7 (−1.11σ) |
| **value on the cells the tail enters** | 194.5 ± 16.6 | 209.1 ± 18.2 | **+14.6 (0.59σ)** |
| residue the tail leaves behind | 32.5 ± 3.3 | 37.1 ± 3.9 | +4.7 (0.91σ) |
| **tail gold collected (mechanism output)** | 162.0 ± 13.5 | 172.0 ± 14.4 | **+10.0 (0.50σ)** |
| baseline residue left on entered cells | 398.6 ± 35.1 | 374.5 ± 32.3 | −24.1 (−0.51σ) |

Condition and comparison, one sentence per row:

- **margin_delta** — closed-loop self-play on map1, `dispatch="fixed"`, each arm game compared against the unmodified `f18064c` on the identical seed and order arm; the two columns are two disjoint seed sets of the same size measured by the same driver.
- **trigger rate** — count of rounds in which exactly one unit was blind and the split actually changed, counted inside the strategy shim, compared between seed sets on the same arm.
- **gold generated, residue pool, central-9×9, mean pile** — enumerated directly from `ScenarioGenerator.resolve_round` for 500 rounds per seed with **no strategy and no engine**, so they are properties of the seed set alone; compared between seed sets.
- **tail entries / value entered / residue left / tail gold** — taken from the engine's own ordered `movements`/`pickups` events attributed to action slots 4–6 of the producer, compared between seed sets on the same arm.
- **baseline residue left** — same attribution applied to the *baseline* run of each pair, compared between seed sets, i.e. how much milkable residue the unmodified build leaves for anyone to take.

**Reading.** Every mechanism variable is indistinguishable, and the supply side is if anything
*smaller* out of sample (less gold, less residue pool, and significantly less central gold at
−2.22σ). The mechanism's own output — tail gold — differs by **+10.0 ± 20**, which accounts for
**7.4 % of the +135.4 margin divergence**; the other 92.6 % is closed-loop propagation of a small
perturbation, not a bigger pool. Per-seed values are heavily right-skewed in both sets (OOS median
+180 vs mean +215.5; TUNE median +34 vs mean +80.1), which is the shape that produces exactly this
kind of set-to-set swing.

**Therefore: no mechanism explanation exists; pool-weighting cannot lift the estimate** (the OOS pool
is the *smaller* one, so weighting by pool size pushes the point estimate *below* the tune value,
never above); the divergence is **unexplained**, the direction is the seed-sensitivity signature
rather than confirmation, and the point estimate to carry forward is the low end,
**+80.1 ± 38.9 gross (2.06σ)**. The naive 48-game pool (+147.8 ± 28.4) must not be used.

## Check 2 — the measured cost of six steps. This is what kills the candidate.

`tests/icount.cpp` with `perf_event_open`, 500 000 calls × 3 reps, best rep, every `.so` replayed
against the **same** recorded 500-round input stream (baseline self-play, map1 seed 1000), built on
quant-compiler (AMD EPYC 9T25, x86_64, gcc 14.3.1, `-std=c++17 -O3 -march=native -fPIC -shared`,
zero warnings, **AVX512-FP16 count = 0** for every arm). **Base reproduces the registered hot-field
anchor to six decimals — 848.452298 vs 848.452 — so this is the same caliper, not a new one.**

| shape | instr/call | Δ | cost @1.6 | cycles/call | Δ | static instr | `.text` | `.rodata` | mod64 | net vs +80.1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| base `f18064c` | 848.452 | — | — | 295.339 | — | 704 | 5363 | 1352 | **0x10** | — |
| `tableonly` — widen table only, k stays 3 | 852.792 | **+4.34** | −7 | 308.297 | +12.96 | 702 | **5363** | 1800 | 0x20 | n/a (no-op) |
| `ptr` — cheapest working shape, no tail validation | 910.548 | **+62.10** | **−99** | 327.805 | +32.47 | 764 | 5587 | 1800 | 0x20 | **−19** |
| `novalid` — int-array stash, no tail validation | 926.850 | +78.40 | −125 | 331.120 | +35.78 | 774 | 5635 | 1800 | 0x20 | −45 |
| `lazy` — validate 3 extra waypoints, producer only | 964.184 | **+115.73** | **−185** | 351.096 | +55.76 | 849 | 6035 | 1800 | 0x20 | **−105** |
| `eager` — validate 6 waypoints, both units | 969.550 | +121.10 | −194 | 355.882 | +60.54 | 802 | 5763 | 1800 | 0x20 | −114 |

Condition and comparison, one sentence per row:

- **base** — `git show f18064c:src/player.cpp` compiled unmodified on the contest machine, measured on the shared stream; every other row is compared against this row on that identical stream, and "raw" includes the constant harness loop.
- **tableonly** — `fact/pdr/pdc` widened from `[3]` to `[6]` with nothing else changed, compared against base; its k-histogram is 500/500 `k=3` and its `.text` byte-count is *identical* to base, so it is a provable behavioural no-op and its +4.34 is the pure table cost.
- **ptr** — widened table plus the conditional split, stashing one `const uint8_t*` per unit and validating nothing beyond the delivered three waypoints, compared against base; the cheapest shape I could find that still fires.
- **novalid** — same as `ptr` but stashing three `int`s per unit instead of a pointer, compared against base, to price the stash shape itself.
- **lazy** — widened table plus the split, with the three extra waypoints validated *after* the unit loop for the producer only, compared against base; the cheapest *faithful* shape, i.e. the one whose behaviour matches the simulator margin.
- **eager** — same validation done inside the loop for both units unconditionally, compared against base, as the branchless upper bound.

**The decomposition, which is the answer to the question you asked.**
- Widening the table is nearly free: **+4.34 instructions**, `.text` unchanged. My ≈24 estimate was
  right about the table.
- **The conditional-`k` mechanism itself costs +57.8** (`ptr` − `tableonly`): the blind-mask
  accumulation, the per-unit stash, the post-loop branch and the six-int rewrite of `out.actions`.
  I priced this at ~13. **This is where my estimate failed — not the validation.**
- **Validating the extra waypoints costs a further +37.3 … +42.7.** Even *lazy* validation, paid on
  only the ~46 % of rounds that reallocate, costs +37, because keeping `ir/ic/sr/sc` alive past the
  unit loop forces spills.
- **Cycles agree in sign and are, if anything, worse.** Base IPC is 848.452/295.339 = 2.873, but the
  *added* work runs at 115.73/55.76 = **2.08 instructions per cycle** — below average — so the added
  instructions are more expensive than the mean instruction and the instruction-based cost above is
  the **conservative** one. The repo's own note that cycles, not instructions, is the economically
  meaningful quantity therefore points the same way, only harder.

**Against the death line you set — 60+ instructions kills it — every working shape is ≥ 62.1 and the
faithful one is 115.7.** Your `+42` (gross +80.1 minus an *estimated* 38) becomes **−19 at the
absolute cheapest and −105 at the faithful shape** once the cost is measured. Even at the forbidden
OOS gross of +215.5 the faithful shape is +30.5, i.e. inside one SE (41.3) of zero.

**A second, independent tax sits on top.** Every variant — *including the free table-only one* — moves
`moveDecision`'s entry from base's `0x10` to **`0x20`**, priced in this repo at +11.67 ns ≈ **−128
gold**. The cause is `.rodata` growing 1352 → 1800 B (+448), which shifts the entry regardless of
`.text`. A 48-byte `asm(".space")` pad restores `0x10`, but the implication is that **`f18064c` sits
in the good bucket unaided and any version of this candidate makes it pad-dependent** — a coupling
the repo records as invisible to every normal signal.

**Two secondary consequences, both measured rather than argued.**
- **Share of 6-step paths fully unobstructed, versus 3-step paths: 100 %.** `novalid`, `lazy` and
  `eager` produce *identical* k-histograms on the shared stream (`k=0` on 69 rounds, `k=6` on 81,
  150 reallocations, 450 tail slots, **zero** stays), i.e. the three extra waypoints rejected **0 of
  150** firings; the simulator agrees at **2 rejections in 1216 firings across 8 closed-loop games
  (0.16 %)**. The widened tail is the early-arrival fold, which oscillates back over cells the first
  three waypoints already validated. So validation buys nothing measurable — which is why `ptr` is
  the honest best case, and why the measured margin applies to it, and it still loses.
- **Blocked / fallback rate: unchanged by construction.** The patch never touches the 3-step gate or
  the steer fallback, the head is still emitted by the untouched `acts[0..2]` stores, and
  `tableonly`'s k-histogram is byte-for-byte base.

## Check 3 — B2 under a calibrated central field: the door stayed shut, and it shut harder

Both field models run in the same driver on the same 12 OOS seeds × 2 order arms (24 games each),
self-play, same-seed paired against the unmodified baseline. The calibrated field is the hot-field
line's in-process monkeypatch (`sim/analyze_hotfield_table.install_field`); `sim/scenario.py` was
**not** modified. **Gate that the patch took: 24 distinct baseline `log_sha256` per field with zero
overlap between the two fields**, and the measured per-cell central landing rate reproduces the
report — ring1/ring5 steepness **1.26× uniform, 2.58× calibrated** against 3.35× measured.

| arm | field | margin (pooled) | first / second | our net | their net | class |
|---|---|---:|---:|---:|---:|---|
| B2 `(6,10)/(10,6)` | uniform | +14.5 ± 55.3 (0.26σ) | +81.0 / −51.9 | +111.1 ± 39.7 | +96.6 ± 30.4 | **ceding** |
| B2 `(6,10)/(10,6)` | **calibrated** | **−58.5 ± 32.6 (−1.80σ)** | −28.0 / −88.9 | +28.8 ± 30.7 | +87.2 ± 27.0 | **ceding** |
| B2 `(6,6)/(10,10)` | uniform | −20.3 ± 39.5 (−0.52σ) | +24.2 / −64.9 | +93.3 ± 33.0 | +113.6 ± 31.3 | **ceding** |
| B2 `(6,6)/(10,10)` | **calibrated** | **−135.9 ± 39.7 (−3.42σ)** | −95.8 / −175.9 | +4.4 ± 25.1 | +140.2 ± 27.4 | **ceding** |
| `Clut_k6_stay` | uniform | +215.5 ± 41.3 (5.22σ) | +271.2 / +159.8 | +159.9 ± 29.6 | −55.6 ± 24.6 | **joint** |
| `Clut_k6_stay` | **calibrated** | +171.1 ± 39.0 (4.38σ) | +235.5 / +106.7 | +148.8 ± 33.2 | −22.3 ± 25.5 | **joint** |

Condition and comparison, one sentence per row:

- **B2 `(6,10)/(10,6)`, uniform** — the stock simulator, i.e. the condition the original negative verdict was measured under, compared against the unmodified baseline on the same seed and order arm; reproduces the committed +14.5 exactly.
- **B2 `(6,10)/(10,6)`, calibrated** — the identical driver and seeds with only the central-9×9 landing law changed, compared against a baseline re-run **under the same calibrated field**, so the field is common-mode and cannot leak into the delta.
- **B2 `(6,6)/(10,10)`, uniform / calibrated** — same two conditions applied to the coverage-optimal diagonal, each compared against its own same-field baseline.
- **`Clut_k6_stay`, uniform / calibrated** — the surviving candidate carried through both field models on the same seeds as a companion, each compared against its own same-field baseline, to see whether the defect was flattering it too.

Field-model contrast: `(6,10)/(10,6)` **−73.0 ± 64.1 (−1.14σ)**, `(6,6)/(10,10)` **−115.5 ± 56.0
(−2.06σ)**, `Clut_k6_stay` −44.4 ± 56.8 (−0.78σ).

**Reading, and the bias direction was the opposite of the one feared.** The concern was that a flat
central field makes "move to a different central cell" a no-op by construction and therefore
*manufactures* B2's negative verdict. The measurement says the reverse: restoring the gradient makes
B2 **worse** by 73–116 gold, one of the two significantly so, and both order arms flip from mixed to
uniformly negative. The reason is visible in the three-way classification: B2 is **ceding in both
fields** — our net rises (+111.1 → +28.8, +93.3 → +4.4) while the unmodified opponent's rises more
(+96.6, +140.2) — and a real gradient makes the vacated central anchor *more* valuable, so there is
*more* to cede. **The uniform field was flattering B2 by 73–116 gold, not framing it.** Your prior
holds; the door does not reopen; B2 is closed harder than before.

**The general point this makes concrete.** A device defect's bias direction must be judged **per
candidate**, because the same defect points opposite ways for different candidates: the flat central
field *flatters* B2 (less gradient ⇒ less to cede ⇒ margin looks better than it is) and
*understates the cost* for arm S (leaving the centre), whose negative verdict is therefore already
conservative and needs no re-run. Deciding "the simulator is optimistic" or "the simulator is
pessimistic" once, globally, would have been wrong in one of the two directions either way.

## Reproduction

```
# check 1 (mechanism variables, both seed sets)
python3 sim/analyze_step_budget.py stepattr --base <f18064c.so> --seeds 2000:2012 \
        --arms Clut_k6_stay --out /tmp/res_oos.json          # and --seeds 1000:1012

# check 2 (contest machine only: needs perf_event_open)
python3 /tmp/gr_gates/make_variants.py                        # emits the three shapes from the pin
g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o out/X.so player_lut6_X.cpp -Isrc
./out/icount out/X.so icount_src.bin 500000 3 instructions    # and ... cycles
./out/khist  out/X.so icount_src.bin                          # k histogram / firing rate

# check 3 (both field models)
python3 sim/analyze_step_budget.py ab --base <f18064c.so> --seeds 2000:2012 \
        --field {uniform,centripetal} --arms B2_diag_6_10__10_6,B2_diag_6_6__10_10,Clut_k6_stay
```

The input stream is regenerated by `run_game(base, base, map1, seed=1000, fixed_costs=(200,201))`
with `output_path=` set, then `python3 tests/dump_inputs.py <log>`; `tests/icount.cpp` is unmodified.

## Where this leaves the line

| candidate | gross margin (defensible) | measured implementation cost | net | status |
|---|---:|---:|---:|---|
| `Clut_k6_stay`, faithful (`lazy`) | +80.1 ± 38.9 | −185 (+115.7 instr) | **−105** | **dead** |
| `Clut_k6_stay`, cheapest (`ptr`, unvalidated) | +80.1 ± 38.9 | −99 (+62.1 instr) | **−19** | **dead** |
| B2 `(6,10)/(10,6)` under a real gradient | −58.5 ± 32.6 | — | — | **closed harder** |
| B2 `(6,6)/(10,10)` under a real gradient | −135.9 ± 39.7 | — | — | **closed harder** |

`Clut_k6_stay` was the only surviving candidate in the project. It does not survive: the mechanism is
real and reproduces under a calibrated field (+171.1 ± 39.0, joint move, opponent down), but the
cheapest implementation that expresses it costs 62 instructions and the faithful one 116, against a
defensible gross of +80.1, plus a fresh 48-byte pad dependency. **The search space on this line is
exhausted.** The +4.34-instruction table widening is the only free thing found here, and on its own
it is a behavioural no-op worth exactly zero.
