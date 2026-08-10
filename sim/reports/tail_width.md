# Tail width: `P90 − P50` of decision latency — attribution and five knives

> Line `tail_width-aab8`. **Verdict: NEGATIVE on the operative target.** No construct lowered
> `P90` by the required two quanta on any map, and none lowered it on all three. A real,
> reproducible narrowing of `P90 − P50` was found (−66 to −76 core cycles on all three maps
> against a null floor of 0 to +7), but it is bought **entirely by raising `P50`**, which does
> not lower `P90` and therefore does not serve a prize judged on tail latency.
> Nothing was committed to `src/player.cpp`.

## 0. Conditions — these belong to every number in this document

| item | value |
|---|---|
| baseline construct | `git show HEAD:src/player.cpp` at `b5de9ef`, source sha256 `3168589d4cb1…ca38b` |
| baseline `.text` sha256 | `264d1ceb3cf81192acb185bf7577c29c468a17feb3d7ea21902969fd51aa989b` — **matches the given baseline exactly** |
| baseline `.text` / entry / mod64 / FP16 | 5235 B / `0x1a10` / `0x10` / 0 |
| build | quant-compiler, GCC 14.3.1, `-std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared` |
| build directory | isolated `~/goldrush/tw_aab8`; no other line writes there, so no library was ever overwritten under a running comparison |
| machine load | 116–119 users, load average **63** at Step 1 and **88–90** at the final A/B |
| pinned CPU | 3 |
| streams | map1 `game_175847.bin` `d773e06d…` (40 walls) · map2 `game_176396.bin` `04da8666…` (24 walls) · map3 `game_176389.bin` `31f32360…` (78 walls); 500 × 1444 B, built by `tests/dump_inputs.py` at `b5de9ef` with the snapshot field populated |
| eviction condition | **600 × 64 B-aligned branchy victim functions spanning 38,400 B**, walked in full before every timed call |
| instrument | per-round **min over 31 reps**, reps interleaved across all `.so` inside one process, **15 independent runs** for the final A/B, steady rounds `r >= 20` |
| primary unit | **TSC ticks**, quantum **27 ticks = 10.00 ns** |
| cross-check unit | `rdpmc` core cycles, `PERF_COUNT_HW_CPU_CYCLES`, `exclude_kernel`, read through the perf mmap page |

### 0.1 Two corrections to the shared record

1. **The "68 KB I-cache/BTB eviction" is not a 68 KB victim set.** It is the size of the
   `bench_thrash` **binary**: `size -A` gives Total = **69,694 B**, while `nm` places
   `thrashOne<0>`…`<599>` **38,400 B** apart. The condition is stated in bytes here so it
   stops mutating.
2. **The precedent's "−27 cycles" was exactly one TSC quantum.** This VM advances the guest
   TSC only in 27-tick steps, and 27 ticks / 2.699993 GHz = 10.00 ns, which is also why
   `LATENCY_VALIDATION` found that figure and why the platform shows 10 ns quantisation. The
   `escapeStep` knife was therefore a **one-quantum** result — the instrument's minimum
   resolvable step.

### 0.2 Instrument choice, and the condition that had to be rejected

`tests/latency_bench.cpp` reports wall nanoseconds, and at this load its P90 scatter is
±60 ns — three times the effect. Two counters were characterised against a 6-instruction
trivial player interleaved rep-by-rep in the same invocation:

| counter | trivial-player width | verdict |
|---|---:|---|
| **TSC ticks** | **0** on all three maps (P50 = P90 = P99 = 54) | primary: contributes **zero** width |
| `rdpmc` core cycles | 13 | cross-check: finer resolution, small own width |

`rdpmc` carries ~660–725 cycles of constant overhead on this KVM guest. That is irrelevant
to `P90 − P50`, which is a **difference of two quantiles of one distribution**, so any
constant cancels exactly — which is also why the width statistic has run-to-run sd 0.0 while
the `P50` itself drifts.

**The faithful `-cold2` condition (16 MiB data write-through plus the code victim set) had to
be rejected**: it destroys the signal. Path-selection variance share `eta²` collapses from
0.62–0.68 to **0.10–0.21**, within-path widths balloon from 27–54 to **216–243** ticks, and
no-fallback rounds then supply **17–46%** of the slowest decile. **Code-only eviction is the
discriminating condition.** Both are recorded so nobody re-derives the wrong one.

## 1. Baseline tail width — the number the knives had to move

| condition | map1 | map2 | map3 | run-to-run sd |
|---|---:|---:|---:|---|
| **eviction, TSC ticks** | **108** | **108** | **81** | **0.0 / 0.0 / 0.0** |
| eviction, `rdpmc` core cycles | 132 | 153 | 116 | 2.9 / 8.2 / 5.1 |
| **hot, no eviction, TSC** | **27** | **27** | **27** | 0.0 |

**The entire hot-replay width is one quantum.** That independently re-derives why the
`escapeStep` precedent showed nothing in ordinary hot replay: there was nothing to show.

### 1.1 🔴 The ceiling, stated before any candidate

Local width under the precedent's own condition is **30–40 ns**; the platform width being
attacked is **100 ns**. This bench reproduces **at most about one third** of the platform
tail. **`P90 − P50 <= 70 ns` is not verifiable on this machine in any unit**, and no
platform-width claim appears anywhere in this document.

## 2. Path attribution — the answer is path *selection*, not within-path spread

Labels come from a build derived from HEAD by `sim/tail_trace_patch.py`, which is
**`pair_diff` 0/500 on three maps against the baseline** and whose label stream is
re-derived twice per map with **0 mismatches**. The instrumented build is never timed.

**map1 (40 walls) — overall P50 324 / P90 432 / width 108**

| path | share | P50 | P90 | within-path width | share of slowest decile |
|---|---:|---:|---:|---:|---:|
| no-fallback | 45.0% | 270 | 324 | 54 | **0.0%** |
| fallback | 31.7% | 351 | 405 | 54 | 23.6% |
| escape | 18.1% | 405 | 459 | 54 | 61.8% |
| other (cold layers) | 5.2% | 405 | 459 | 54 | 14.6% |

**map2 (24 walls) — overall P50 297 / P90 405 / width 108**

| path | share | P50 | P90 | within-path width | share of slowest decile |
|---|---:|---:|---:|---:|---:|
| no-fallback | 61.2% | 270 | 297 | 27 | **0.0%** |
| fallback | 25.2% | 351 | 405 | 54 | 36.4% |
| escape | 8.1% | 405 | 459 | 54 | 47.3% |
| other | 5.4% | 351 | 567 | 216¹ | 16.4% |

**map3 (78 walls) — overall P50 297 / P90 378 / width 81**

| path | share | P50 | P90 | within-path width | share of slowest decile |
|---|---:|---:|---:|---:|---:|
| no-fallback | 56.7% | 270 | 297 | 27 | **0.0%** |
| fallback | 35.6% | 351 | 378 | 27 | 73.9% |
| escape | 2.7% | 432 | 459 | 27 | 15.9% |
| other | 5.0% | 324 | 405 | 81 | 10.1% |

¹ inflated by three lazy-learning slow-start rounds at 567–594; not a route effect.

- **The slowest decile contains 0.0% no-fallback rounds on all three maps**, on both counters.
- **Within-path width is 27–54 ticks (1–2 quanta)** against an overall width of 81–108: the
  paths are individually narrow and simply *offset from each other*.
- **`eta²` = 0.62–0.68** of round-to-round variance is explained by path selection
  (0.57–0.64 on the `rdpmc` cross-check).
- The archived **hot** medians are reproduced (243 / 243 / 270, span one quantum) — but under
  eviction the span is **135–162 ticks**, three times larger. Hot replay prices only the
  work; eviction also prices the cold code a path drags in. A ceiling computed from hot
  medians understates path selection by 3×.

### 2.1 `waveTick`, and why it is worth one eighth rather than one half

| | map1 | map2 | map3 |
|---|---:|---:|---:|
| wave rounds globally | 5.0% | 5.0% | 5.0% |
| **share OF the slowest decile** | **12.7%** | **12.7%** | **10.1%** |
| enrichment vs base rate | ×2.5 | ×2.5 | ×2.0 |
| of 24 wave rounds, in the decile | 7 (29%) | 7 (29%) | 7 (29%) |

`rdpmc` cross-check: 14.6% / 14.6% / 12.5%. **71% of wave rounds never enter the tail**,
because +54…81 ticks on top of a *no-fallback* round lands at 324–351, still below the decile
cut of 378–432. **Only wave rounds that are also blocked cross it** — so the wave knife's
value is conditional on the blocked knife rather than additive to it.

### 2.2 Exact work decomposition — where the excess actually is

Instruction counts are deterministic (zero run-to-run variation), so this table is exact.

| event | Δ instructions | Δ TSC ticks | ticks per instruction |
|---|---:|---:|---:|
| baseline no-fallback round | 710 (abs) | 270 (abs) | 0.38 |
| one unit blocked | +76…79 | +81 | 1.05 |
| both units blocked | +144…159 | +108…162 | 0.9 |
| escape, one unit | +177…180 | +108…162 | 0.8 |
| both blocked + escape | +251…273 | +162…189 | 0.7 |
| **`waveTick` (`%20`)** | **+8** | **+54…81** | **6.8–10** |

`rdpmc` confirms the mechanism: the no-fallback path runs at **IPC 1.9**, the escape
increment at **IPC 1.0**. **The blocked band's excess is the work itself, not a branch
artefact** — which is why cutting branches could not reach it. `waveTick` is the sole
exception: 8 instructions, ~70 ticks, i.e. almost pure far-call plus certain misprediction of
a branch taken 5% of the time.

## 3. The five knives

All five are **`pair_diff` 0/500 on all three maps**, entry `mod64 == 0x10` after re-tuning
the pad in one step, FP16 = 0.

| knife | what it does | `.text` | pad | `.text` sha256 (first 16) |
|---|---|---:|---:|---|
| `wave` | `%20` clear becomes an inline **masked** clear on every round | 5363 B | 112 | `f542083ad4f3f147` |
| `blk` | `steerStep`/`escapeStep` read the already-materialised `blk[]`; the two merge into one out-of-line cold function | 5052 B | 112 | `526e8e0db9d90c1e` |
| `both` | `wave` + `blk` | 5158 B | 144 | `55ee7d372d09abed` |
| `cst` | `blk` + the steering branch itself constant-shaped (unconditional call, mask select) | 5196 B | 128 | `6afc1897425c7a0a` |
| `all` | `wave` + `cst` | 5304 B | 144 | `f317029208b3d4a3` |

### 3.1 Two design corrections that were load-bearing

**`waveTick` must be masked, not unconditional.** An unconditional
`memset(bombbit, 0, 92)` destroys the cross-round bomb memory the rich-gate depends on — a
large behaviour change. The masked form `m = -(uint32_t)(round % 20 != 0); bombbit[i] &= m`
reproduces the clear exactly when `m == 0` and is the identity when `m == ~0`, at the same
insertion point so ordering against the scan's `bombbit |=` writes is unchanged.

**The `ok0` reuse is not provable and was dropped.** It looks as though `steerStep`'s `ok0`
is the LUT `ok`'s first term. It is not: the LUT index is **clamped** to `[-3,3]` while
`steerStep` receives the target **unclamped**, and the row-vs-column preference turns on
`adr >= adc`, so clamping can flip it — true `(dr,dc) = (4,5)` clamps to `(3,3)` giving
`adr == adc` and row-first, while `steerStep` sees `4 < 5` and goes column-first. Blind-anchor
targets at `(6,8)`/`(11,8)` are routinely further than 3 from a corner unit, so those rounds
are reachable. **`pair_diff` would have caught this only if a log happened to contain that
geometry** — so `pair_diff` 0/500 is evidence over the logged input distribution, not a
proof of equivalence.

**What does survive is provable, because the index domain was checked**:
`pass01(r,c,rich)` is `~((bpw[r+1] | (rich & bombbit[r+3])) >> (c+1)) & 1` and
`blk[i] = bpw[i] | (rich & bombbit[i+2])`, so `blk[r+1]` is the same word; the reachable
domain is `r ∈ [-1,17] → blk[0..18]` and `blk` has 19 entries.

## 4. Results — paired within-run deltas

The byte-identical null control reproduces the baseline's run-to-run fluctuation **run for
run**, which is what licenses paired within-run differencing: the shared window component
cancels. `rdpmc` figures below are **net of the null control's own median**.

| construct | ΔP50 ticks | **ΔP90 ticks** | **ΔP90 cycles, net of null** | ΔWidth ticks | **ΔWidth cycles, net of null** |
|---|---|---|---|---|---|
| **null (byte-identical)** | 0 / 0 / 0 | **0 / 0 / 0** | **0 / 0 / 0** | 0 / 0 / 0 | **0 / 0 / 0** |
| `wave` | 0 / 0 / 0 | −27 / 0 / 0 | −5 / +1 / −6 | −27 / 0 / 0 | +1 / −2 / +6 |
| `blk` | 0 / 0 / 0 | 0 / 0 / 0 | −10 / **+14** / −1 | 0 / 0 / 0 | −8 / −23 / +5 |
| `both` | 0 / 0 / 0 | 0 / 0 / 0 | −5 / −1 / +4 | 0 / 0 / 0 | −8 / +4 / +5 |
| `cst` | +54 / +54 / +54 | −27 / **+27** / 0 | **+31 / +57 / +36** | −54 / −54 / −54 | −59 / −63 / −58 |
| `all` | +27 / +54 / +27 | −27 / 0 / 0 | −18 / +1 / −20 | **−54 / −54 / −27** | **−66 / −76 / −63** |

Order is map1 / map2 / map3. Raw null floors for scale: `rdpmc` ΔP90 median −4 / +4 / −1 with
per-run range up to ±33; TSC ΔP90 median 0 / 0 / 0 with per-run range `[−27,+27]` / `[0,0]` /
`[−27,+27]`.

### 4.1 Verdicts

- **`wave` — negative on the global statistic, positive on its own rounds.** The −27 tick
  median on map1 is one quantum and is **not** supported by the cross-check (−5 net of null);
  maps 2 and 3 are flat. But on the 24 rounds it targets it removes **−54 TSC ticks** and
  **−87 / −72.5 / −68.5 core cycles**, against a null of −4 / −5 / −4 on the same rounds —
  **15–20× the floor**, at **zero `P50` cost**. The knife does exactly what it was designed
  to do; the design's leverage on `P90` is simply one eighth of the tail.
- **`blk` — negative.** A real instruction reduction (−9 on every round, −49 / −36 / −21 on
  escape rounds, instruction P90 −43 / −27 / −10) that does not convert: the sign is
  inconsistent across maps on the fine counter and the TSC statistic does not move at all.
  −49 instructions on escape rounds buys about −15 core cycles, which is the floor.
- **`both` — negative.** The two knives do not add.
- **`cst` — rejected.** `P90` is **worse on all three maps** on the fine counter
  (+31 / +57 / +36) and `P50` rises +85…120 core cycles, over the +54 tick budget. It narrows
  the width only by lifting the floor.
- **`all` — a real width effect that does not qualify.** −66 / −76 / −63 core cycles of
  width on all three maps (≈ −50 ticks ≈ −18.5 ns), cross-counter consistent with the TSC
  −54 / −54 / −27, against a null of 0 / +7 / −5. **But `P90` itself falls only −18 / +1 /
  −20 core cycles** — under one quantum on two maps, zero on the third — and it costs
  +45…73 core cycles of `P50`. **It does not meet the −2 quantum `P90` bar and is not
  recommended for landing.**

### 4.2 The 90th percentile relocated rather than the tail coming down

The composition check that was asked for. Slowest-decile share by route:

| construct | map1 | map3 |
|---|---|---|
| baseline | escape 74.1% / fallback 22.2% / no-fallback 0% | fallback 77.6% / escape 20.4% / no-fallback 2.0% |
| `both` | escape 64.8% / fallback 31.5% / no-fallback 0% | fallback 79.6% / escape 18.5% / no-fallback 1.9% |
| **`cst`** | escape 33.6% / fallback 34.4% / **no-fallback 30.5%** | fallback 45.6% / escape 7.2% / **no-fallback 47.2%** |
| **`all`** | escape 38.3% / fallback 33.3% / **no-fallback 26.7%** | fallback 50.0% / escape 9.8% / **no-fallback 40.2%** |

`both` **cheapens**: the escape band's share of the decile falls and the fallback band's
rises, i.e. escape came down toward fallback with no new occupants. `cst` and `all`
**equalise**: the no-fallback band goes from 0–2% of the decile to 27–47%, meaning the 90th
percentile now sits on the **raised floor**. That is precisely the failure mode predicted
before building, now measured.

## 5. Zero-signal control record

`null.so` is rebuilt from a byte-for-byte copy of the baseline source into a separate file
and carried through the **whole** pipeline: build, `.text` extraction, alignment check,
`pair_diff`, and every A/B invocation interleaved rep-by-rep with the candidates.

| check | result |
|---|---|
| `.text` sha256 | `264d1ceb…989b` — **byte-identical** to the baseline (`cmp` clean) |
| entry `mod64` / FP16 | `0x10` / 0 |
| `pair_diff` | 0/500 × 3 maps |
| TSC ΔP50 / ΔP90 / ΔWidth | **0 / 0 / 0** on all three maps |
| TSC per-round Δ | **exactly 0** on every path subset on every map |
| instruction Δ | **0** on every round on every map |
| `rdpmc` ΔP50 / ΔP90 / ΔWidth | −11 / −4 / 0 · −1 / +4 / +7 · 0 / −1 / −5 |
| `rdpmc` ΔP90 per-run range | `[−32,+9]` / `[−15,+18]` / `[−18,+13]` |

The control reports no change **and** reproduces the baseline's fluctuation run for run. Its
`rdpmc` ΔP90 floor of −4…+4 with a per-run range up to ±33 is the threshold every candidate
figure above is judged against.

## 6. Why the objective is not reachable by this route

- **Band arithmetic.** `P50` sits on the no-fallback band, `P90` on the blocked band. The
  entire blocked-band excess is **+81 ticks = 3 quanta**. Cheapening it therefore has a hard
  ceiling of 3 quanta, and the target needed 2 of them.
- **The excess is work, not branches.** +76 instructions executing at IPC 1.0. The
  *provable* redundancy inside it is 9 instructions on all rounds plus 40 more on escape
  rounds ≈ −15 core cycles, at the null floor. There is no 50-instruction saving available
  without changing behaviour.
- **Constant-shaping cannot substitute**, because it moves the 90th percentile onto the
  raised floor instead of bringing the tail down (§4.2, measured).
- **Reducing blocked *frequency*** would need 40% → under 10%, which is a behaviour change
  and outside this line.
- **The one pure-footprint source is `waveTick`**, worth one eighth of the tail.
- **60–70 ns of the platform's 100 ns width is not reproduced by any local cache condition
  tested**, so it can neither be attributed nor attacked from this machine.

## 7. Platform verification will not be visible tonight

A tail change cannot be confirmed on the board tonight. The cumulative cost statistic pools
on the order of **443,000 samples from 886 games**, so a single publish moves it by about
0.06% of its weight. Confirmation needs **several hundred further games**. Nobody should
re-read the board in an hour and conclude a knife failed.

## 8. Reproduction

```bash
# on quant-compiler, in an isolated directory
git show HEAD:src/player.cpp > src/player.cpp
g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o base.so src/player.cpp -Isrc
objcopy -O binary --only-section=.text base.so t.bin && sha256sum t.bin   # 264d1ceb…989b
python3 tests/dump_inputs.py logs/game_175847.log logs/game_176396.log logs/game_176389.log

python3 sim/tail_trace_patch.py src/player.cpp trace_player.cpp        # labels
python3 sim/tail_candidates.py  src/player.cpp cands                   # knives
# re-tune the pad ONCE per candidate: delta = (0x10 - entry_mod64 + 64) % 64; pad = 96 + delta
g++ -std=c++17 -O2 -o tail_path_bench tests/tail_path_bench.cpp tests/tail_icache_thrash.cpp -ldl

./tail_path_bench --mask-only --csv masks_175847.txt logs/game_175847.bin ./trace.so
./tail_path_bench --counter tsc    --evict code --reps 31 --runs 15 --cpu 3 \
    --csv f_tsc_175847.csv  logs/game_175847.bin ./base.so ./null.so ./wave.so ./blk.so ./both.so ./cst.so ./all.so
./tail_path_bench --counter cycles --evict code --reps 31 --runs 15 --cpu 3 \
    --csv f_pmc_175847.csv  logs/game_175847.bin ./base.so ./null.so ./wave.so ./blk.so ./both.so ./cst.so ./all.so

python3 sim/analyze_tail_width.py DATA_DIR --streams tsc_code
python3 sim/analyze_tail_ab.py    DATA_DIR --stream f_tsc
python3 sim/analyze_tail_ab.py    DATA_DIR --stream f_pmc
```

Machine-readable companion: `sim/reports/tail_width.json`.
Diffs against `git show HEAD:src/player.cpp`: `sim/reports/tail_width_wave.diff`,
`sim/reports/tail_width_all.diff`. Both were **re-verified end to end**: applied to a fresh
export of `HEAD:src/player.cpp` in a clean directory and rebuilt, they reproduce exactly the
`.text` sha256 quoted in §3 (`f542083a…69e1` and `f317029…f514c`) with `mod64 == 0x10`.
**Neither is recommended for landing on the tail objective**; `wave` is the only one that is
free (`P50` flat, `pair_diff` 0/500, −87…−68 core cycles on the 5% of rounds it targets) if
it is ever wanted for its own sake.
