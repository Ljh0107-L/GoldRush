# Repricing blocked routing on map1: is the 920-gold wall pool real?

> Measurement and judgment only. **Zero platform games consumed.** No strategy was implemented.
> `src/`, `sim/engine.py`, `sim/scenario.py`, `sim/abi.py` and `sim/analyze_path_oracle.py` were
> read only. Artifacts: this file, `sim/reports/map1_wall_repricing.json`, driver
> `sim/analyze_blocked_cost.py`.
>
> Base under test: `git show f18064c:src/player.cpp`,
> `shasum -a 256` = `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd` (verified).
> The worktree `src/player.cpp` is **not** this file (it is `d9be1e52…f455d22`, a parallel Worker's
> `895a27e` line) and was never used. Host arm64 dylib
> `/tmp/gr_wall/base.so` = `28d4fda788c893df835c2c9b495a626d99db44afd2c87383ebd6f961994b5cee`,
> built `clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include /tmp/gr_wall/shim.h`. The guarded
> scalar fallback is the path exercised (no AVX2 on this host). It is **equivalent to the AVX2 path
> for target selection and bomb recording**: `player.cpp:447-458` computes
> `rowsel[i] = min_j TT.bestrow[i][1<<j]` over in-bounds window cells with `v > 2`, and
> `:432` computes `TT.bestrow[i][mask]`, which by the table's construction at `:126-135` is the same
> minimum over the same set; the bomb writes at `:457` and `:442` target the same `bombbit[rr+3]`
> bit `cc+1`. Behaviour is the target here, not latency. Separately, §1.2 shows my Python
> reconstruction reproduces this binary's emitted triple on 4920/4920 steady unit-rounds.

---

## 0. Verdict

**The 920 gold/game map1 wall pool is a mirage and the map1 central-wall lesion is a 伪命题 / false
premise: the figure applies a routed-decision rate to an all-unit-round base and then multiplies by
a conditional-mean gap whose two arms share exactly zero common support (0 of 829 covariate strata
overlap, 3525 unit-rounds), the wall-detour mechanism it motivated is reproducibly negative at
−124.4 ± 56.1 gold/game over 12 paired games — matching the platform's −51.5 ± 94 — and even an
unimplementable *perfect* per-round repair of the entire blocked class is only +90.6 ± 58.9
gold/game — 1.54σ, not established, and comparable to the 55-77 gold/game an implementation of it
would cost in latency; this path is closed.**

| question | answer |
|---|---|
| is the 920 arithmetic reproducible | **yes, exactly**: 0.373 × 0.826 × 1000 × (3.048 − 0.064) = **919.36**, error −0.64 gold. The base is *wall-blocked unit-rounds*, built by applying a **routed-decision** rate to an **all-unit-round** denominator |
| is the clear-vs-blocked gap causal | **no.** Wall blocking is a deterministic function of `(start cell, clamped target offset)` once the wall table is locked, so positivity fails completely: **overlap share 0.000** on 829 strata. Coarse matching *raises* the gap to 2.67, which is the trap |
| mechanical cost of a block | **2 voluntarily forfeited steps, not a lost round.** The `ok` check fails inside `player.cpp:504-506`, *before* the engine sees anything; the engine never refuses a walled step from us. Median blocked episode is 1-2 rounds and `steerStep` self-heals |
| stock/flow-separated true cost, map1 | open-loop achievable repair **283.9 ± 26.4** gold/game, of which **novel 33.7 ± 7.8** and **timing 237.2 ± 27.9** (13% novel). Per wall block the novel cost is **0.193 gold**, not 2.984 — a factor of 15.5 |
| what the closed loop realizes | wall-detour (arrival) **−124.4 ± 56.1** (n=12, −2.22σ); position-preserving **−142.3 ± 36.8** (n=6, −3.87σ); unrestricted perfect repair **+90.6 ± 58.9** (n=12, **1.54σ, undecidable**) |
| safe-detour paradox | **resolved and quantified.** Only 30.1% of wall blocks admit *arrival*; conditional on arrival the best possible detour gains 3.79 gold of pickup and pays **6.69 gold of burn**, net **−2.90**. My oracle-quality version of that mechanism reproduces the platform's −51.5 to within 0.67σ |
| does any mechanism fit the budget | **no.** The unestablished +90.6 ± 58.9 bound is the *upper* bound for a perfect oracle; the cheapest known implementation shape of a reachability-aware selector (CHANGELOG gate C-2: +224 B text, ~+105 instructions, +27 cycles P50) already costs **55-77 gold/game**, and the `23db121` episode turned a local +10 ns into a platform +27.5 ns ≈ −300 gold |
| is the lesion map1-specific | **no.** Perfect-repair upper bounds are +90.6 / +113.0 / +94.5 gold/game on map1/map2/map3, all undecidable; novel gold 34 / 39 / 21. The battlefield we *win* (Tundra map3, +245.8, 2.26σ) has the **most** wall blocks (242/game vs map1's 175) |

## 1. What was built, and the gates it passed

`sim/analyze_blocked_cost.py` reuses `sim/analyze_path_oracle`'s fidelity-checked substrate
verbatim (`extract_state`, `_sim`, `joint_best`, `harvest_map`, `classify`, `fog_discipline`) and
adds one thing the substrate did not have: a **source-exact reconstruction of the frozen build's own
routing decision**, so that "blocked" is defined by the code rather than inferred.

### 1.1 What "blocked" means at source

Read at `git show f18064c:src/player.cpp`:

* target ladder (`:463-479`): the 5×5 window is scanned for cells with `v > 2`; the winner is the
  one with the lowest rank in the `pext` remap `rm[26] = {7,11,13,17, 2,6,8,10,14,16,18,22,
  1,3,5,9,15,19,21,23, 0,4,20,24, 12, 12}` (`:121-125`; the priority loop runs `k < 25`, so the
  trailing duplicate is unused and slot 12 — the unit's own cell — takes the last rank, 24). Ranks
  0-3 are the four orthogonal neighbours, 4-11 ring-2, 12-19 ring-3, 20-23 ring-4. If no
  `v ≥ 3` exists, the own cell is taken when it still holds `v ≥ 2` (`standing`), otherwise the
  anchor `(6,8)`/`(11,8)` (`:372`, `blind`).
* `blk` (`:481-483`) = `bpw` (walls + boundary sentinels) `| (rich & bombbit)`, where
  `rich = held ≥ 100` and `bombbit` accumulates every `-3` seen in either unit's 5×5 window and is
  cleared **only** every 20 rounds by `waveTick` (`:240-243`, `:376`).
* the route (`:499-514`): three LUT waypoints from `SL.pdr/pdc` are tested against `blk`; if all
  pass, the pre-folded triple `SL.fact` is emitted; **if any fails, the unit emits
  `(steerStep, STAY, STAY)`** — one cautious checked step and two voluntary stays.

So the "37.3% blocked" population is the `ok == 0` branch. Its emitted shape is exactly
`stall = (a,4,4)` — or `stay3` when `steerStep` returns −1 — and no other branch can produce that
shape (`d ≥ 1` always yields three moves after the fold pre-fold at `:207-216`). Measured on map1
across 5 seeds: 1264 blocked unit-rounds = **1170 `stall` + 94 `stay3`**, 0 of any other shape, with
a further 4 `stay3` coming from the `d == 0` fold branch when no neighbour is passable.

### 1.2 Fidelity gates

| gate | result |
|---|---|
| reconstruction vs the real `moveDecision`, steady rounds (≥8) | **0 / 4920 mismatches** on map1 (5 seeds); 0 / 2952 on map2; 0 / 2952 on map3 |
| all mismatches | 27 unit-rounds on map1, all at rounds **0, 1, 3** only — the `mode==1` opening `slowMove` march, which is outside the steady window by construction |
| harvest model vs engine per-unit pickup | **4997 / 5000 unit-rounds exact** on map1; game sums 1254/1254, 1424/1424, 1542/1552, 1412/1412, 1318/1324 — residual 16 of 6956 gold (0.23%), the documented "bomb remembered inside fog that another actor consumed" case (`path_harvest_oracle.md` §1.5) |
| open-loop trajectory drift | **`log_sha256` identical to a plain baseline run on all 11 traced games** (`trajectory_identical_all: true` on all three maps) |
| information discipline | `fog_discipline()` ran every 50th round of every traced game and never fired |
| dispatch | `dispatch="fixed"`, `fixed_costs=(200,201)` throughout, so seat 1 moves before all NPCs and the enemy; no clock is read; same-seed runs are byte-reproducible |

---

## 2. Where 920 comes from — the arithmetic, and the denominator error

`python3 -m sim.analyze_blocked_cost derive` enumerates candidate denominators against the
published constants (`src/CHANGELOG.md`, paragraph **受阻路由诊断（最终构型 trace…**, lines **106-110** as of commit
`cb092bc`; the brief cites lines 68-90 and a parallel Worker's commits have since shifted the file,
so the anchor text is quoted rather than the line number).

| candidate | count/game | × gap | value | error vs 920 |
|---|---:|---:|---:|---:|
| A: **all** blocked unit-rounds × (clear − wall) | 373.0 | 2.984 | 1113.03 | +193.03 |
| **B: wall-blocked unit-rounds × (clear − wall)** | **308.10** | **2.984** | **919.36** | **−0.64** |
| C: wall and bomb arms, each with its own gap | 373.0 | — | 1115.24 | +195.24 |
| D: all blocked × clear pickup (no counterfactual credit) | 373.0 | 3.048 | 1136.90 | +216.90 |
| E: central-wall blocks only × (clear − wall) | 234.62 | 2.984 | 700.10 | −219.90 |

**Candidate B reproduces the published figure to within 0.64 gold.** So 920 =
`0.373 × 0.826 × (2 units × 500 rounds) × (3.048 − 0.064)`. The brief's own arithmetic check
(1112) is candidate A and is correct as arithmetic; it simply omitted the 82.6% wall share.

Two independent errors are baked into that count of 308.1:

1. **Denominator mismatch.** 37.3% is a rate over *normal-route* decisions, i.e. the `d != 0`
   branch — the CHANGELOG's own phrase is 正常路由受阻率. Multiplying it by all 1000 unit-rounds
   silently includes the `d == 0` fold population, which is never routed and can never be blocked.
   Measured fold share on map1: **19.0–22.4%** (per seed 0.1911, 0.2022, 0.2012, 0.2236, 0.1900) of unit-rounds (`fold_share`, 5 seeds). Applying the
   published rates to the correct base gives 0.373 × 0.826 × ~800 = **246**, not 308 — the
   denominator alone inflates the pool by **+25%**.
   Confirmation that 37.3% is the routed rate: my measured routed blocked rate is
   map1 **32.1% ± 1.7**, map2 **23.7% ± 1.8**, map3 **35.8% ± 2.6**, against the published
   37.3 / 24.5 / 36.7 — map2 and map3 land within 0.9 pp on the *routed* denominator, whereas the
   all-unit-round rates (25.7 / 19.1 / 30.4) are 5-13 pp off. The published rates are routed rates.
2. **The gap is not a loss.** 2.984 gold is `E[pickup | clear] − E[pickup | wall-blocked]`, a
   difference of conditional means across two populations that, as §3 proves, have no common
   support. It is not the causal cost of a block and it is not stock/flow separated.

Reproducing the *published arithmetic* on my own measured local inputs (175.0 ± 13.6 wall blocks,
gap 2.448) gives **428.3 ± 35.5 gold/game** — already less than half of 920 before any
identification or stock/flow work is done.

---

## 3. The confound test: the two arms share zero common support

The brief asked for a matched or counterfactual design rather than a raw conditional mean. I ran
both, and the matched design is the one that fails informatively.

### 3.1 The propensity is degenerate by construction

For wall blocking, `ok` is a function of exactly `(start cell, clamped target offset (dr0,dc0))`
and the locked `BAKED_W` table. It contains no random component. So for every covariate stratum
`X = (cell, dr0, dc0)`, `P(wall-blocked | X) ∈ {0, 1}` — a matched pair cannot exist.

Measured (map1, 5 seeds, 3525 clear + wall-blocked unit-rounds):

| audit | strata | mixed strata | unit-rounds in mixed strata | overlap share |
|---|---:|---:|---:|---:|
| **wall-blocked vs clear, `X = (cell, dr0, dc0)`** | **829** | **0** | **0** | **0.000** |
| all blocked vs clear, `X = (cell, dr0, dc0, rich)` | 1072 | 113 | 1507 | 0.384 |
| all blocked vs clear, `X = (cell, dr0, dc0)` | 864 | 117 | 1733 | 0.441 |

The only overlap in the whole design comes from **bomb** blocking, whose treatment varies because
`bombbit` is time-varying and resampled every 20 rounds (and, pooled across seeds, because
different games remember different bombs). For the wall arm — the arm that carries the 920 claim —
overlap is exactly zero. **No covariate adjustment, matching, weighting or regression can identify
"the cost of being wall-blocked", because the counterfactual "this same situation, not blocked"
does not exist in the data-generating process.**

### 3.2 Coarse matching makes it worse, not better

Stratifying on `(target_mode, d, target-value bucket, visible ≥3 supply bucket)` — 26 strata, all
861 wall blocks matched — gives a weighted gap of **2.669**, *larger* than the raw 2.448. On
`(target_mode, d)` alone it is 2.735. This is the trap: a competent-looking matched analysis
"confirms" ~2.7 gold/block and would have produced a pool of ~470 gold. The reason matching fails
is that the confounder is not target value or supply — wall-blocked rounds are in fact **richer**
situations (mean target value **5.03 vs 3.55**, mean visible ≥3 supply **1.02 vs 0.76**) — the
confounder is **reachability**, and reachability *is* the treatment.

### 3.3 What is identified instead

The identified estimand holds the state fixed and varies only the action: *given the wall is where
it is, what is the best achievable three-step outcome from this cell, and how much better is it than
what we did?* That is `V_best − V_base` on the blocked unit-round, computed by the same
`joint_best` search whose fast path matched exhaustive 15,625-pair enumeration on 195/195 sampled
rounds (`path_harvest_oracle.md` §1.6).

map1, pooled 5 seeds, 4920 steady unit-rounds:

| | clear | wall-blocked | bomb-blocked | fold |
|---|---:|---:|---:|---:|
| unit-rounds | 2664 | 861 | 403 | 992 |
| realized pickup / unit-round (engine truth) | 2.506 | **0.058** | 0.040 | 0.135 |
| effective moves / unit-round | 2.731 | **0.832** | 0.762 | 1.969 |
| **best achievable net / unit-round** | 3.218 | **1.598** | 1.571 | 0.566 |
| achievable gain (joint) / unit-round | 0.891 | **1.623** | 1.531 | 0.657 |
| share with **zero** achievable gain | 68.9% | **54.1%** | 56.6% | 74.5% |
| mean target value | 3.549 | 5.034 | 4.074 | 0.149 |
| mean `d` | 1.925 | 2.938 | 2.610 | 0 |

Two facts kill the 2.984 gap outright:

* **The ceiling of a wall-blocked unit-round is 1.598 gold** — 64% of what a clear unit-round
  actually delivers. A "loss" of 2.984 gold is arithmetically impossible when the maximum
  attainable value of the state is 1.6.
* **54.1% of wall-blocked unit-rounds have literally nothing better available.** For those, the
  `stall` is not a lesion, it is a correct read of a genuinely poor local geometry.

The published pickup pair itself reproduces well on the blocked side (mine 0.058 vs published
0.064; bomb 0.040 vs 0.030) and lower on the clear side (2.506 vs 3.048), consistent with local
absolute income being lower than the platform's (see §10 bias register).

---

## 4. What a block costs mechanically

The brief asked whether continuation semantics make "blocked" cheaper than it looks. Verified at
source and then measured; the answer is more specific than the question.

1. **The blocked population is not an engine event at all.** `blk` already contains every wall and
   boundary, so the frozen build never *requests* a walled step; it replaces the plan with one
   checked `steerStep`. Continuation semantics (`sim/engine.py:1050` sets
   `effective, blocked_by = 4, "wall"` and the loop continues; the `blocked_step_continuation`
   smoke check at `:1359` asserts `actions == (4, 1)` with the unit at `(1,0)`; pickup before
   trample at `:1416-1430`) therefore do **not** apply to this class.
2. **The cost is two voluntarily forfeited steps.** 0.832 effective moves on a wall-blocked
   unit-round vs 2.731 on a clear one — 1.90 moves given up. The round is not lost; the unit still
   advances one cell, generally toward the target.
3. **The block self-heals fast.** Blocked episodes per unit (map1, 5 seeds): mean run length
   **2.60 rounds**, per-seed medians `2,1,2,1,2`, p90 5.4, max 18.8, and **50.0% of episodes are
   exactly one round**.
   The lesion is a *delay*, which is why §5 finds 87% of its value is timing rather than novel gold.
4. **A separate, genuine engine-refusal population exists and is unrelated.** 15.8% (per-seed
   11.8-18.3%) of seat-1 unit-rounds have at least one step refused by the engine (179.8 refused
   steps per 1000
   unit-rounds, concentrated on step 0: 112-140, step 1: 38-59, step 2: 8-22). Since walls and
   (when rich) bombs are pre-filtered by `blk`, these are player-collision refusals — and in local
   self-play **both seats anchor on the same `(6,8)/(11,8)` cells**, so this rate is a local
   artefact and is **not platform-transferable**. It is reported only to close the continuation
   question: even here, a refused step does not abort the remaining steps.

So "blocked" never meant "lost the round", and the 0.064 figure is not measuring a lost round — it
is measuring a one-step crawl through a cell that, by the selector's own logic, has no `v ≥ 3` on
it.

---

## 5. Stock/flow separation, and the price of the pool

Same diagnostic as `path_harvest_oracle.md` §6, restricted to the wall-blocked class: for every
extra cell the oracle would take, ask whether the base's **own realized trajectory** re-enters that
cell at any later round of the same game. Because the trace is open-loop, that trajectory is the
real one.

map1 (5 seeds, gold/game, ± SE across seeds):

| pricing rung | map1 | reading |
|---|---:|---|
| published claim | 920 | conditional-mean gap × wall blocks, unidentified |
| published arithmetic on my measured inputs | 428.3 ± 35.5 | same method, honest inputs |
| unphysical wall removal (run the plan we wanted, wall passable) | 316.3 ± 43.9 | walls do not move; includes bomb hits on the "opened" route |
| **identified achievable repair, open-loop** | **283.9 ± 26.4** | best 3 steps with the wall in place |
| of which **novel** (our trajectory never re-enters the cell) | **33.7 ± 7.8** | **13% novel share** |
| of which timing | 237.2 ± 27.9 | 87% — we collect it later anyway |
| target-reaching detour (the side-step's mechanism, oracle quality) | **−152.9 ± 76.9** | worse than standing still |
| position-preserving repair (end on the start cell) | +42.5 ± 13.6 | small, and see §6 |

**Per wall block, the novel cost is 33.74 / 175.0 = 0.193 gold**, against the published 2.984 — a
factor of **15.5**. As a pool, 920 → 33.7 is a factor of **27.3**.

The >800 gold/game tripwire from the previous round fires on nothing here: the largest raw number
this line produces is 428, and after identification and stock/flow it is 34. The tripwire's job was
done upstream, in the arithmetic.

---

## 6. Closed loop: what actually survives

Same-seed paired, `dispatch=fixed`. Only unit-rounds the reconstruction marks as blocked get a
substituted triple; every other unit-round is byte-identical passthrough and the teammate always
keeps its base triple. **Substitution is gated to the steady window (round ≥ 8)**, so the
fingerprint-lock and BFS-march opening is untouched — see §6.2, where ignoring that gate produced a
completely different and completely wrong answer.

Because a positive in-sample result on n=6 is exactly the failure mode this project has been burned
by, every arm that came out positive was re-run on a **disjoint out-of-sample seed batch**.

| arm | freedom on blocked unit-rounds | seeds 0-5 | seeds 6-11 (OOS) | **pooled n=12** | σ | W-L |
|---|---|---:|---:|---:|---:|---:|
| `oracle3` | any of the 125 triples (**upper bound**) | +167.3 ± 22.9 (6-0, 7.30σ) | **+13.8 ± 111.4** (2-4, 0.12σ) | **+90.6 ± 58.9** | **1.54** | 8-4 |
| `oracle3_ge3` | same, but may not enter a 1-2 gold cell | +120.8 ± 41.0 (5-0, 2.95σ) | not run | +120.8 ± 41.0 (n=6) | 2.95 | 5-0 |
| `detour` | must end on the intended target (arrival) | −125.8 ± 76.1 (2-4) | **−123.0 ± 89.8** (2-4) | **−124.4 ± 56.1** | **−2.22** | 4-8 |
| `posfix` | must end on the start cell (positional guard) | −142.3 ± 36.8 (0-6) | not run | −142.3 ± 36.8 (n=6) | −3.87 | 0-6 |

Cross-map, `oracle3`, seeds 0-5: **map2 +113.0 ± 80.6 (1.40σ, 5-1)**, **map3 +94.5 ± 91.2 (1.04σ,
3-3)**. Same magnitude as map1's pooled figure, and equally undecidable.

### 6.1 Readings

* **The wall-detour mechanism is dead, and now reproducibly so.** `detour` is −124.4 ± 56.1 over 12
  paired games (−2.22σ), and the two disjoint seed batches agree to within 3 gold (−125.8 vs
  −123.0). Its material balance is the classic trap: pickup/unit-round **up** 0.28-0.33, zero-yield
  **down** 6-7 pp, effective moves **up** 0.17 — every mechanism side-indicator green — and the net
  is negative because burn rises more (§7.2).
* **The positional guard is dead too.** `posfix` −142.3 ± 36.8, 0-6, −3.87σ. Forcing the unit to end
  where it started turns every blocked round into a fold, pickup/unit-round falls 0.174 and
  zero-yield rises. This closes the caveat `path_harvest_oracle.md` §5 left open ("a hand-built
  organ with a positional guard … is not what was measured and could behave differently"): it was
  measured here, and it behaves worse.
* **The perfect-repair upper bound is real but not established.** +90.6 ± 58.9 over 12 games is
  1.54σ. The in-sample 7.30σ did **not** replicate: the out-of-sample batch returned +13.8 ± 111.4
  with per-game deltas `[−357, −89, −78, −67, +335, +339]`. Anyone who had stopped at seeds 0-5
  would have taken a 7σ result to the platform. **The honest statement is: an unimplementable oracle
  can extract at most about +90 gold/game from this population, and even that is not statistically
  separated from zero.**
* **What the oracle is actually doing is not a wall repair.** Measured over 763 blocked unit-rounds
  (3 seeds): the free action makes **2.99 moves** (base: 0.83), ends on the **selected target only
  11.3%** of the time, ends on the **start cell 0.0%** of the time, and its extra gold splits
  **75% from other visible `v ≥ 3` cells / 25% from sub-threshold `v ∈ {1,2}` cells**. Forbidding
  sub-threshold cells (`oracle3_ge3`) removes only 46.5 of the 167.3 in-sample gold. So the
  mechanism is *reachability-aware target re-selection* — "the ring-nearest `v ≥ 3` cell is behind a
  wall, take a reachable one instead" — not a detour to the blocked target and not the picky-threshold
  line the CHANGELOG already rejected at gate B. That is the direction the CHANGELOG's own
  「可达选靶」 phrasing pointed at; its *pricing* was wrong by a factor of ten, and its
  *causal story* (walls cost 3 gold a block) is wrong outright.

### 6.2 The opening-gate artefact, disclosed

Before the steady gate existed, the same three arms measured `oracle3` **−494.0 ± 194.0**, `detour`
−69.3 ± 47.4 and `posfix` −913.3 ± 85.3. The only difference was that substitution was allowed in
rounds 0-7, i.e. in at most **16 unit-rounds per game** — the `mode == 1` fingerprint-lock and
4-5-round BFS march to the anchors. Hijacking those 16 unit-rounds dropped seed 2 from 1369 to 79
net gold and raised the blocked-unit-round count from ~250 to 441-978 in a feedback loop.

That is worth recording as evidence in its own right: **16 opening unit-rounds out of 1000 can swing
a game by more than 1200 gold.** It is the strongest single demonstration in this report that this
build's income is positional, and it is why every number in §6 is quoted with the gate on.

## 7. The safe-detour paradox, resolved

The paradox: a mechanism that demonstrably recovered 44.5% (427/960) of originally blocked
decisions and drove fallback 37.3% → 20.7% produced `Δ = −51.5 ± 94` (z = −0.55) on the platform,
in `probeobs` games where we are first mover 500/500 and uncontested — so that number is **pure
behaviour**, not latency and not contention.

Three measurements settle it, and they agree.

### 7.1 The side-step buys motion, not arrival

**Only 30.1% of wall-blocked unit-rounds admit *any* three-step sequence that ends on the selected
target cell.** The reason is geometric and exact: mean `d` on wall blocks is 2.94, with the
distribution `{d=2: 274, d=3: 403, d=4: 161, d=5: 9, d=6: 14}`. A `d = 1` target can never be
wall-blocked (its only waypoint is the gold cell itself, and gold and walls do not coexist), and at
`d ≥ 3` a wall detour needs ≥5 steps, which does not fit in a round. The `[side, a0, a0]` dogleg
ends at `side + 2·a0`; for the 70% of blocks where arrival is impossible, all it can do is move the
unit.

### 7.2 Conditional on arrival, the detour is negative — and the loss is burn

Among the reachable 30.1%, the *best possible* target-reaching sequence (chosen by net, so it
already avoids bombs whenever a bomb-free arriving route exists):

| quantity, per reachable wall-blocked unit-round | value |
|---|---:|
| pickup gained | **+3.788** |
| burn paid | **+6.691** |
| **net** | **−2.904** |

So the "≈3 gold per block" intuition is roughly right as a *gross pickup* figure — 3.79 gold, even
larger than the published 2.984 — and is more than fully cancelled by the burn on the only routes
that arrive. Insisting on arrival means crossing cells the frozen build refuses on purpose. For the
bomb-blocked class the same measurement is `+25.1` burn against a net of `−21.8`, and the
unphysical "ignore the bomb bit" counterfactual is **−27.5 gold per occurrence** — the `rich` bomb
gate is not a nuisance, it is one of the build's most valuable organs.

This is the unsafe side-step's failure derived from first principles rather than observed after the
fact: the CHANGELOG records burn 52 → 476 (+424) and Δ = −373, z = −4.56. My `detour` arm's own
material balance (seeds 0-5, steady-gated) is extra pickup **+327.5** against a net of **−125.8**,
i.e. **≈453 gold of extra burn and loss per game** — the same mechanism and the same order of
magnitude as the CHANGELOG's +424, from an independent route.

### 7.3 The oracle-quality safe detour lands where the platform landed

| | Δ net gold/game | SE | setting |
|---|---:|---:|---|
| platform safe side-step (`f18064c` A/B, n=10/arm) | **−51.5** | 94 | probeobs, uncontested, first mover 500/500 → pure behaviour |
| this report, `detour` arm (n=12 paired, two disjoint seed batches) | **−124.4** | 56.1 | local sim, `dispatch=fixed`, pure behaviour by construction |

Difference −72.9 on a combined SE of 109.4 = **0.67σ** — statistically the same number, and my
estimate is the more negative of the two. The local measurement is twice as tight, replicates across
disjoint seeds (−125.8 and −123.0), and is **biased in the mechanism's favour** (`sim/README.md` §7:
the over-central NPC model over-estimates central competition and relatively over-values outer-ring
routes, so a repair that walks a unit off the peak is flattered locally). So −51.5 was not noise
around zero; it was a correct measurement of a mechanism worth roughly −50 to −125 gold.

### 7.4 Which of the four candidate explanations is true

The brief offered four. The evidence assigns them shares:

| candidate explanation | verdict |
|---|---|
| the pool is genuinely near zero | **true, and it is the main effect.** Novel gold 33.7 ± 7.8 gold/game; 54.1% of wall blocks have zero achievable gain; the state's ceiling is 1.598 gold; and the *entire* perfect-repair bound is +90.6 ± 58.9, i.e. undecidable |
| the recovered targets are low-value | **false.** Wall-blocked targets are *richer* (5.03 vs 3.55) with more visible supply (1.02 vs 0.76). What is recovered is not the target — 69.9% of the time it cannot be reached at all |
| the detour walks the unit off the central generation peak | **true, and it is why the arrival-forcing arm is negative while the free arm is not.** `detour` −124.4 ± 56.1 (it must leave the peak to arrive) versus `oracle3` +90.6 ± 58.9 (it may stay near); `posfix` −142.3 shows the reverse extreme is worse still. And 16 hijacked opening unit-rounds swing a game by >1200 gold (§6.2) — the same positional-income mechanism as `path_harvest_verdict.md` §3.1 |
| the block is not costly because of engine continuation semantics | **wrong mechanism, right conclusion.** The blocked class never reaches the engine (§4). What makes the block cheap is that it forfeits 2 of 3 steps for a median of 1-2 rounds while `steerStep` self-heals, so the cost is timing (87%), not gold |

One term the brief did not list turned out to matter as much as any of them: **the arrival route's
bomb burn (+6.69 gold per reachable block)**. That is what converts "recovered 44.5% of decisions"
into "recovered approximately nothing", and it is why the *unsafe* variant was far worse than the
safe one rather than better.

---

## 8. Is this a map1 lesion at all?

No. Both the open-loop pool and the closed-loop upper bound are essentially map-invariant, and the
battlefield we win has the most wall blocks.

| | map1 | map2 | map3 |
|---|---:|---:|---:|
| routed blocked rate, measured (published) | 32.1% ± 1.7 (37.3%) | 23.7% ± 1.8 (24.5%) | 35.8% ± 2.6 (36.7%) |
| wall blocks / game | 175.0 ± 13.6 | 107.4 ± 2.9 | **241.5 ± 4.7** |
| primary cause wall / bomb | 67.9% / 32.1% | 56.8% / 43.2% | 80.5% / 19.5% |
| central-six share of wall blocks (published map1 76.2%) | **70.3%** | 8.1% | 20.9% |
| achievable repair, open-loop | 283.9 ± 26.4 | 286.3 ± 14.1 | 293.4 ± 11.2 |
| **novel gold, open-loop** | **33.7 ± 7.8** | **38.6 ± 4.2** | **21.0 ± 10.1** |
| novel share | 13% | 14% | 8% |
| target-reaching detour, open-loop | −152.9 ± 76.9 | −199.5 ± 173.5 | −169.0 ± 72.6 |
| **`oracle3` closed loop** | **+90.6 ± 58.9** (n=12, 1.54σ) | **+113.0 ± 80.6** (n=6, 1.40σ) | **+94.5 ± 91.2** (n=6, 1.04σ) |

Three structural points:

* The published rates themselves already say map1 (37.3%) and map3 (36.7%) are the same battlefield
  on this axis — 0.6 pp apart. map3 is where we **win** (Tundra map3 +245.8 ± 108.8, 2.26σ). A
  variable that is equal on our best and worst battlefields cannot explain the difference between
  them.
* Locally the ordering even reverses: map3 has 38% more wall blocks per game than map1, and map2 —
  where only 8.1% of wall blocks involve any central wall — has the *largest* closed-loop bound.
* The closed-loop upper bound is statistically indistinguishable across all three maps (+91 / +113 /
  +95) and undecidable on all three. Whatever value lives in this population, it is a property of
  the selector, not of map1's geometry.

The one map1-specific element of the published diagnosis survives and is confirmed: the six central
interior walls really do dominate map1's wall blocks — **70.3% of wall blocks** name one of
`(9,7) (7,9) (9,9) (8,10) (7,7) (8,6)` as the first blocked waypoint, against the published 76.2%.
(For all blocks the published 62.9% reads 47.7% locally, tracking my higher bomb-block share.) **The
geometry claim is right; the pricing and the causal conclusion drawn from it are not.**

---

## 9. Sizing against the map1 deficit, and the instruction budget

Deficit target, adopted from the sibling's primary-log adjudication
(`sim/reports/archive_backfill.json` → `map1_adjudication`, orchestrator-verified; I independently
re-derived the two figures the repo can check and they reproduce exactly):

| battlefield | n | mean Δ | SE | σ | W-L |
|---|---:|---:|---:|---:|---:|
| Tundra map1, pooled four `f18064c` baseline arms | 24 | **−289.04** | 54.65 | **5.29** | 3-21 |
| Tundra map1, anchor-proven three arms | 18 | −251.78 | 57.01 | 4.42 | 3-15 |
| T-1 map1, family `t1f1` (the only `f18064c` family) | 6 | **−274.33** | 149.98 | **1.83** | 1-5 |

So map1 is a **decisive** stable loss on Tundra and a **point estimate only** on T-1. That makes the
mirage verdict more consequential, not less: the battlefield that most needs a lever is the one where
this candidate lever is emptiest.

### 9.1 The availability trap, stated numerically

On map1, wall-blocked unit-rounds that scored nothing and *could* have scored under perfect
three-step play number **75.0 ± 6.4 per game = +7.62 pp ± 0.65 of hit rate**; counting all blocked
unit-rounds it is **+11.08 pp ± 0.38**. Closing Tundra map1 through hit rate alone needs about
**+6.1 pp** (289.04 / 4.7312 / 1000) and T-1 map1 about **+5.9 pp**.

**The open-loop availability exceeds the requirement by 25%, and the closed loop realizes +90.6 ±
58.9 — 31% of the deficit, at 1.54σ, with a perfect oracle and zero implementation cost.** The
conversion rate from "available hit-rate opportunity" to "realized gold" is roughly one third, and
it comes with a standard error as large as its mean. That is the same shape as
`path_harvest_oracle.md` §4 (+21.7 to +26.6 pp available against +6.3 pp needed, closed loop −832),
one rung less bad.

### 9.2 Budget

Prices verified at source: 0.1454 ns/instruction (`src/INFRA.md` §1 line 20), 1 ns ≈ 11 gold inside
the ±20 ns crossover band (§2.5 line 111), so **1.6 gold/instruction** (0.1454 × 11 = 1.5994).

| item | value |
|---|---|
| **income ceiling** of any repair of this population | **+90.6 ± 58.9 gold/game** (perfect oracle, zero cost, n=12, 1.54σ — *not established*) |
| what that buys at the average price, if it were established | 57 instructions |
| open-loop novel gold (the stock/flow-honest per-round figure) | 33.7 ± 7.8 gold/game → 21 instructions |
| cheapest known implementation shape: gate C-2 double-layer mask (`src/CHANGELOG.md`) | +224 B `.text`, ~+105 instructions/call, **+27 cycles P50, +5-7 ns** → **55-77 gold/game** |
| measured cost of the wall-detour side-step actually built | +672 B `.text`, +27 cycles P50 ≈ 7 ns ≈ **77 gold/game** |
| same at the frozen header's *marginal* rate (84 instructions ↔ 5.6 cycles, 6× below average) | ~60 gold/game for a ~315-instruction organ |
| platform latency risk premium | `23db121`: local +10 ns became platform **+27.5 ns** once `.text` and access shape grew — that is ≈ **−300 gold** |

A reachability-aware selector is *not* a zero-instruction constant change: it needs a second row
mask or per-cell value extraction plus a passability test per candidate, which is exactly the shape
`path_harvest_verdict.md` §4 priced at 400-800 instructions for the general case and the CHANGELOG
measured at +105 instructions / +27 cycles for the narrowest useful case. **Expected value:
+90.6 ± 58.9 income minus 55-77 gold of certain cost = +14 to +36 gold/game with a ±59 standard
error and a live −300 gold tail.** That does not clear a 50-gold resolution gate, let alone a
289-gold deficit. Even at the most flattering reading — taking the in-sample +167.3 and ignoring the
out-of-sample collapse — the net after cost would be ~+90 to +112 gold/game, i.e. 31-39% of Tundra
map1's deficit, still not enough to flip the battlefield.

One bias note in the mechanism's favour, stated for completeness: the surviving mechanism keeps the
unit *near* its current cell (2.99 moves, never returning to start, harvesting nearby reachable
cells), so its value is central efficiency, which `sim/README.md` §7 says is **under**-estimated
locally. The true income could be higher than +90.6. But it is still measured at 1.54σ on n=12 in a
simulator whose NPC model is its weakest link, and `sim/README.md` §10 requires platform
confirmation, which would cost quota this line has not earned.

---

## 10. Sample-size adjudication

The brief asked me to adjudicate this; the orchestrator has since settled it from primary logs. I
report the settled position, plus the two independent checks I ran before the update, because they
corroborate it.

| claim | status |
|---|---|
| T-1 map1 −274.3 is n=6 and ~1.83σ | **confirmed independently.** `sim/analyze_gold_delta.py` `net_delta` over family `t1f1`: deltas `[-688,-600,-383,-199,-90,+314]`, mean −274.333, sd 367.36, SE 149.975, **σ = 1.829**. Undecidable at 2σ. No sibling family exists for T-1 map1 (`survey --min 8` is empty for both opponents), so it cannot be tightened from the repo |
| Tundra map1 is n=6 at −219.2 ± 107.7 (~2.04σ) | **superseded.** My own check reproduces `frTu1` exactly (mean −219.167, SE 107.694, **σ = 2.035**), but the sibling identified three further `f18064c` baseline arms; pooled **n=24: −289.04 ± 54.65, σ 5.29, 3W/21L**. map1 on Tundra is a decisive loss |
| the n=20 Tundra map1 frozen baseline of −35.4 ± 45.5 is "statistically a tie from an earlier window" and should be weighed against −219.2 | **retired, do not pool.** The figure appears exactly once in the repo (`src/CHANGELOG.md` **line 167**, not ~129 — the file has grown), with no n, no per-game deltas and no artifact; no archived build family has more than 6 replicates; and the sibling's impossibility proof shows the 20 *best* Tundra map1 games in the whole 90-game corpus sum to −2234 (mean −111.70) against the −708 that figure requires. As an arithmetic aside, SE 45.5 with `frTu1`'s per-game sd of 263.8 would need n ≈ 34, not 20 |
| the fog-free gold-delta channel adds independent samples | **no, and the brief is right to say so.** It reproduces −274.9 / −219.6 on the *same* games (`sim/reports/gold_delta_channel.json`), so it validates the accounting and does not tighten any interval |

Poolability, stated honestly even though the answer is now moot: had the −35.4 figure been real, it
would have been statistically compatible with −219.2 (difference −183.8 on a combined SE of 116.9 =
1.57σ, no detectable window shift), and inverse-variance pooling would have given −63 ± 42 =
1.51σ, i.e. undecidable. The impossibility proof is a stronger instrument than that compatibility
test and it overrides it. One caveat I will flag rather than hide: the four pooled Tundra arms have
a between-arm sd of means of **141.5** (−123.5, −219.2, −400.8, −412.7) against a pooled SE of
54.65, so the pooled interval is narrower than a random-effects treatment would give; the sign and
the 4.4σ anchor-proven subset are not in doubt, but "−289.04 ± 54.65" should be read as a
fixed-effect summary of one 12-minute window.

None of this changes the pricing in §2-§7 by a single gold: the pool is empty regardless of how
large the hole it was supposed to fill turns out to be.

---

## 11. Bias register

| number | direction | reason |
|---|---|---|
| open-loop achievable repair (284 gold) | **biased UP, dominant** | per-round counterfactual, stock/flow double-count (87% timing); the oracle inherits the base's position for free |
| novel gold (33.7) | biased UP | still ignores that any repair costs position, which §6 prices at hundreds of gold; slightly biased down by crediting a later return at 100% rather than 65% |
| closed-loop deltas (+90.6 / −124.4 / −142.3) | **biased UP for arms that leave the peak; biased DOWN for `oracle3`** | `sim/README.md` §7: the over-greedy, over-central NPC model over-estimates central competition and relatively over-estimates outer-ring routes. `detour`/`posfix` move the unit outward, so their negatives are if anything flattered; `oracle3`'s value is central efficiency, so its +90.6 may be an under-estimate. Both readings are given in §9.2 |
| in-sample vs out-of-sample | **in-sample n=6 is not trustworthy on this quantity** | `oracle3` map1: seeds 0-5 gave +167.3 ± 22.9 (7.30σ), seeds 6-11 gave +13.8 ± 111.4 (0.12σ). Pooled n=12 is 1.54σ. Any n=6 positive in this family must be re-run on disjoint seeds |
| local absolute income (seat-1 net 1122-1552) | **not a ceiling, not platform-comparable** | self-play against a full-strength copy, not the passive `probeobs` probe (2182.4) |
| clear-class pickup 2.506 vs published 3.048 | local is lower | same lower-income cause; the blocked arms (0.058 vs 0.064) reproduce, which is the arm that matters |
| bomb-block share 32.1% vs published 17.4% | local is higher | `rich` gates the bomb bitmap at held ≥ 100; local income timing differs, and the published trace was a real-log replay. **Flagged as a contradiction with the published trace, not averaged away** — the sibling owns frequency (`sim/reports/miss_taxonomy.md`) |
| engine-refusal rate 15.8% | **local artefact, inflated** | both seats anchor on the same `(6,8)/(11,8)` in self-play, so enemy-collision refusals are over-represented |
| all deltas | same-seed paired, `dispatch=fixed`, no clock read | byte-reproducible; the two arms are the same game |
| every number above | derived from `f18064c` only | no cross-build aggregate is used anywhere; `sim/OPPONENTS.md`'s burst-rate comparison (the ~102-build mixture) is not used |

---

### 11.1 Cross-validation with the sibling miss taxonomy

`sim/reports/miss_taxonomy.md` (same frozen source hash, independently reconstructed selector, its
own sample) owns the *frequency* of the blocked miss class; I own its *pricing* on map1. Overlap:

| quantity | sibling | this report | reading |
|---|---|---|---|
| does the engine ever refuse a walled or out-of-bounds step from us | **0** in 14,970 unit-rounds | 0 by construction; the 15.8% refusal population is actor collisions | **independently confirmed on a second sample** |
| what the execution-level refusals are | 155 visible enemy + 28 own teammate | labelled actor collision, inflated by both seats sharing `(6,8)/(11,8)` in self-play | **agree** |
| wall vs bomb-gate share of gate blocks | **58.0% / 42.0%** (628 / 454 misses) | **67.9% / 32.1%** (all blocked unit-rounds, map1, 5 seeds) | **both far from the published 82.6% / 17.4%; we differ from each other by ~10 pp** on different class boundaries (theirs conditions on a payable reachable target). **Flagged, not averaged.** The published wall share is the outlier |
| novel gold in the blocked class on map1 | 26.0 gold/game (`D_BLOCKED`) | 33.7 ± 7.8 gold/game (wall-blocked only) | **same order, consistent** |
| is the lesion map1-specific | no | no | **agree** |
| closed-loop repair of the blocked class | cites my *earlier draft* ("−69 to −913"); their own position-preserving probe is +8.8 ± 56.1 | **superseded: +90.6 ± 58.9 (`oracle3`, n=12), −124.4 ± 56.1 (`detour`, n=12), −142.3 ± 36.8 (`posfix`, n=6)** | **stale citation, flagged.** The earlier figures were the opening-gate artefact of §6.2. Their +8.8 ± 56.1 and my −142.3 ± 36.8 for position-preserving repair differ by 151 gold on a combined SE of 67.4 = **2.25σ**, i.e. marginally inconsistent. I flag it rather than reconcile it: the constructions differ (their probe re-chooses within a round under a different admissibility rule; mine *forces* the unit to finish on its start cell on every blocked round, which is the stricter and more damaging constraint). This is the one live disagreement between the two reports |

Their independent contribution that I do not price and that strengthens the recommendation in §14.6:
**burn is almost entirely a miss-round phenomenon** — 219.4 gold/game of seat-1 burn, 99.4% of it on
miss unit-rounds — which is consistent with, and much larger than, the +6.69 gold/reachable-block
burn tax I measure on arrival-forcing detours.

---

## 12. Reproduce

```sh
# 1. build the frozen base (never the worktree file)
mkdir -p /tmp/gr_wall
git show f18064c:src/player.cpp > /tmp/gr_wall/base_f18064c.cpp
shasum -a 256 /tmp/gr_wall/base_f18064c.cpp        # 0ecce6fc…84fdd
cp /tmp/gr_path/shim.h /tmp/gr_wall/shim.h          # x86 prefetch tokens on non-AVX2 hosts
clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include /tmp/gr_wall/shim.h \
        -o /tmp/gr_wall/base.so /tmp/gr_wall/base_f18064c.cpp

# 2. the 920 arithmetic (no simulation, instant)
python3 -m sim.analyze_blocked_cost derive

# 3. open-loop trace: frequency, causes, confound audit, pricing, stock/flow  (~4 min map1)
python3 -m sim.analyze_blocked_cost trace --map map1 --base-so /tmp/gr_wall/base.so \
        --seeds 0 1 2 3 4 --out /tmp/gr_wall/final_map1.json
python3 -m sim.analyze_blocked_cost trace --map map2 --base-so /tmp/gr_wall/base.so \
        --seeds 0 1 2 --out /tmp/gr_wall/final_map2.json
python3 -m sim.analyze_blocked_cost trace --map map3 --base-so /tmp/gr_wall/base.so \
        --seeds 0 1 2 --out /tmp/gr_wall/final_map3.json

# 4. closed-loop repairs, same-seed paired.  Substitution is gated to round >= 8.
#    Every positive arm is re-run on a DISJOINT seed batch (~3 min per arm).
for r in oracle3 oracle3_ge3 detour posfix; do
  python3 -m sim.analyze_blocked_cost realized --map map1 --base-so /tmp/gr_wall/base.so \
          --seeds 0 1 2 3 4 5 --repair $r --out /tmp/gr_wall/steady_$r.json
done
for r in oracle3 detour; do
  python3 -m sim.analyze_blocked_cost realized --map map1 --base-so /tmp/gr_wall/base.so \
          --seeds 6 7 8 9 10 11 --repair $r --out /tmp/gr_wall/oos_map1_$r.json
done
for m in map2 map3; do
  python3 -m sim.analyze_blocked_cost realized --map $m --base-so /tmp/gr_wall/base.so \
          --seeds 0 1 2 3 4 5 --repair oracle3 --out /tmp/gr_wall/steady_${m}_oracle3.json
done

# 6. platform-side sample sizes (read-only, no games consumed)
python3 sim/analyze_gold_delta.py validate
python3 sim/analyze_gold_delta.py survey --min 8      # empty for both opponents: no family > 6
```

Wall clock on this host (≤4 concurrent jobs, a sibling competing for CPU): a traced game costs
~5.5 s of engine plus ~5 s of analysis, so 11 traced games + 11 baselines ≈ 4 min; each closed-loop
arm of 6 paired games ≈ 3-4 min.

**Sample sizes, reported rather than maximised (per instruction):** open-loop trace map1 5 seeds
(4920 steady unit-rounds), map2/map3 3 seeds (2952 each). Closed loop: `oracle3` map1 **12** paired
games (two disjoint batches), `detour` map1 **12**, `oracle3_ge3` map1 6, `posfix` map1 6,
`oracle3` map2 6, `oracle3` map3 6 — **48 paired games plus 48 baselines** in total. Zero platform
games.

---

## 13. Corrections to numbers in the commissioning brief (军规 27)

Verified correct at source and used as given: 0.1454 ns/instruction and the 894.216 instruction
baseline (`src/INFRA.md` §1); 1 ns ≈ 11 gold in the ±20 ns band (§2.5); 1.6 gold/instruction
(0.1454 × 11 = 1.5994); the published trace constants 37.3/24.5/36.7%, 82.6/17.4%,
3.048/0.064/0.030, 62.9/76.2% and the six wall coordinates (`src/CHANGELOG.md` lines **106-110** at
`cb092bc`); the side-step verdicts 427/960 = 44.5%, fallback 37.3% → 20.7%, unsafe Δ = −373
z = −4.56 with burn 52 → 476, safe Δ = −51.5 SE 94 z = −0.55 with pickup 2.208 → 2.168 and
zero-yield 56.83% → 56.84% (lines **112-119**); continuation semantics and pickup-before-trample (`sim/engine.py:1050`, `:1359`,
`:1416-1430`); `f18064c` source SHA256.

| # | claim as commissioned | measured / found | verdict |
|---|---|---|---|
| 1 | "37.3% of 2 × 500 × ~2.98 gives ~1112, so 920 rests on some other base" | correct: candidate A = 1113.03. The base is **wall-blocked unit-rounds**: 0.373 × 0.826 × 1000 × 2.984 = **919.36** | **confirmed, denominator identified** |
| 2 | 37.3% is a "normal-route blocked rate" | correct, and materially so: it is conditional on `d != 0`. Applying it to all 1000 unit-rounds over-counts by ~25% (fold share 19-22%) | **confirmed; the published pool double-counts the fold population** |
| 3 | "of map1's blocks 82.6% are walls and 17.4% the bomb richness gate" | locally 67.9% / 32.1% (map1, 5 seeds; 69.2% / 33.8% under an any-wall-waypoint attribution). Bounds/self/enemy = 0 exactly, as published | **contradiction flagged**, not averaged: different setting (local self-play vs real-log replay) changes `rich` timing. Frequency belongs to the sibling report |
| 4 | six central walls carry 62.9% of all blocks / 76.2% of wall blocks | locally 47.7% / **70.3%**. The wall-conditional claim cross-validates; the all-block claim tracks my higher bomb share | **substantially confirmed** |
| 5 | "pickup on clear / wall / bomb = 3.048 / 0.064 / 0.030" | locally 2.506 / **0.058** / 0.040 | **blocked arms confirmed**; `clear` lower because local income is lower (bias-labelled) |
| 6 | "a blocked step does not abort the unit's remaining steps … so 'blocked' may not mean 'lost the round'" | true of the engine, but **the blocked class never reaches the engine**: `blk` pre-filters walls, so the build emits one checked step plus two STAYs. Continuation semantics govern a *different*, collision-driven population (15.8% of unit-rounds locally, a self-play artefact) | **right conclusion, wrong mechanism** |
| 7 | "T-1 map1 is ~1.83σ (undecidable)" | exactly: −274.333, SE 149.975, σ 1.829 | **confirmed** |
| 8 | "Tundra map1 is −219.2 ± 107.7 SE (~2.04σ)" | reproduces exactly (σ 2.035) for family `frTu1`, but is **superseded** by the pooled n=24 figure −289.04 ± 54.65, σ 5.29 | **superseded upward** |
| 9 | "`src/CHANGELOG.md` line ~129 uses a larger n=20 Tundra map1 frozen baseline of −35.4 ± 45.5" | the figure is at **line 167** at commit `cb092bc`, and the repo states **no n at all** for it; no archived family exceeds 6 replicates. It is retired by the sibling's impossibility proof | **line number stale; the n=20 attribution is not repo-verifiable and the figure is retired** |
| 10 | "map1 is our worst battlefield on both opponents by point estimate" | true, and now decisive on Tundra; on T-1 it remains a point estimate at 1.83σ | **confirmed, with the σ stated** |
| 11 | "map1's lesion is its central walls" (the received diagnosis) | the *geometry* is right (70.3% of map1 wall blocks are the central six) but the *pricing* is not (novel gold 33.7 ± 7.8 gold/game; closed-loop ceiling +90.6 ± 58.9 at 1.54σ), the *causal story* is not (the wall-detour mechanism is −124.4 ± 56.1 over 12 games), and the lesion is not map1-specific (map3, which we win, has 38% more wall blocks and an indistinguishable +94.5 ± 91.2 ceiling) | **false premise** |
| 12 | "a raw figure above ~800 gold/game is a tripwire" | never fired: the largest number this line produces is 428 (published method, honest inputs) and 316 (unphysical wall removal). The 920 exceeded the tripwire because of its denominator, not because of a per-round counterfactual | noted |
| 13 | (my own error, corrected mid-round) an earlier draft of this report had `oracle3` at −494 and `detour` at −69 | those runs allowed substitution in rounds 0-7, hijacking the opening BFS march in ≤16 unit-rounds and destroying the game (§6.2). With substitution gated to round ≥ 8 the arms are +90.6 and −124.4. **The wall-detour verdict is unchanged in sign and is now more negative; the perfect-repair figure changed sign.** | **retracted and replaced** |

---

## 14. Recommendations (for the owners of `src/*`, since I must not edit them)

1. **Retire the 920-gold pool.** Replace the `src/CHANGELOG.md` sentence at line 110
   (「这是明日『可达选靶/地图通行性锚点』的首要开题，不应再扫 RICH_T」) with the priced verdict:
   published arithmetic reproduces at 919.36 but rests on a routed rate applied to an all-unit-round
   base and an unidentified conditional-mean gap; open-loop achievable repair 283.9 ± 26.4 of which
   novel 33.7 ± 7.8; closed-loop wall detour −124.4 ± 56.1 (n=12); closed-loop perfect repair
   +90.6 ± 58.9 (1.54σ, undecidable) against an implementation cost of 55-77 gold. Keep the trace
   numbers — they are good measurements; only the inference was wrong.
2. **Record the denominator rule.** Any future 受阻率 must state whether the base is unit-rounds or
   routed decisions. A one-line note next to line 106 prevents the exact 25% inflation that happened
   here.
3. **Record the identification rule.** Conditional-mean gaps between a strategy's own branches are
   not costs when the branch is a deterministic function of the state — the normal case in this
   codebase. The cheap test is the overlap audit in
   `sim/analyze_blocked_cost.py::_overlap_audit`: if `overlap_share == 0`, the gap is not a lever.
4. **Record the opening-gate rule (new, and it nearly fooled me).** Any closed-loop A/B that
   substitutes behaviour must leave the `mode == 1` opening untouched, or state that it does not.
   16 hijacked opening unit-rounds out of 1000 moved a map1 game by more than 1200 gold and flipped
   the sign of the headline result (§6.2).
5. **Record the out-of-sample rule.** `oracle3` measured +167.3 ± 22.9 at 7.30σ on seeds 0-5 and
   +13.8 ± 111.4 at 0.12σ on seeds 6-11. On this quantity an n=6 same-seed positive is not evidence.
   Milestone gates should require two disjoint seed batches before any platform quota is spent.
6. **Bank the `rich` bomb gate as a first-class asset, with a number.** Ignoring the bomb bitmap on a
   blocked route costs **−27.5 gold per bomb-blocked occurrence**, and insisting on arrival at a
   wall-blocked target costs **+6.69 gold of burn per reachable block against +3.79 of pickup**.
   That is a larger and better-established figure than the pool it was competing against, and it
   independently corroborates `src/INFRA.md` §2.4's audit of 避弹 at 35.4 gold/ns.
7. **If map1 is reopened, reopen it on hit rate and position, not on routing.** The unbiased channel
   says map1 carries the largest hit-ratio deficit of all six battlefields (theirs/ours 1.397 on
   Tundra, 1.269 on T-1, versus 0.810 on the one battlefield we win). The +6.1 pp of hit rate that
   Tundra map1 needs is available in the blocked class on paper (+7.62 pp) and worth at most
   +90.6 ± 58.9 gold in practice before paying 55-77 gold of latency. It will have to come from
   somewhere else. The single most striking positional datum this round produced is that 16 opening
   unit-rounds are worth >1200 gold — if any part of map1 deserves the next look, that is where the
   leverage density is.
