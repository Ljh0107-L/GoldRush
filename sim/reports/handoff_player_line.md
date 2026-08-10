# Handoff to the `player.cpp` line: C2 spec, C1 gate, and an honest resizing of both

> Written 2026-08-10 by the orchestrator. This line does not own `src/*`; everything here is a
> **specification for the parallel Worker to implement**, not an implementation. Nothing in this
> file has been built or measured as code.
>
> Read the resizing in §0 before spending any effort. It changed after the map1 erratum
> (`231b657`), and it is not favourable.

## 0. Honest resizing — read first

Both candidates buy **first-mover rate** `f`. The map1 erratum withdrew the diagnosis that made
that valuable:

- Our first-mover rate on map1 is **0.568** against the opponents' 0.432. The race is already a
  **net advantage to us** worth +124.4 gold/game; we lose map1 anyway, on a −411.1 gold/game
  collection deficit at matched action order.
- Against the field, `f` is **already 0.997** (10 of 12 archived mid-field opponents run at
  450–46,930 ns against our ~204 ns; only 18 of 100 ranked teams have board P90 ≤ 1 μs). So buying
  `f` buys **≈0** against ~98% of a 117-team round robin.

**Therefore: C1 is withdrawn as a recommendation** (it was the larger candidate, at +265.8 gold, and
this line is retracting it). **C2 is recommended only on the grounds that it is nearly free**, not on
its headline number. Field-averaged, C2's +32 gold against two teams is worth roughly **+0.5
gold/game**. The defensible reasons to take it anyway are that it is cheap, that it shrinks a P90
tail which is the thing that decides `f` against the *fast* subset of the field, and that the fast
teams are also the strong teams.

If the parallel line's hot-path budget is contested, **C2 should lose to anything that raises mean
income per unit-round**, which is the quantity that actually converts (Δmean × 2 units × 500 rounds
= Δgold/game; 1.0 of mean = 1000 gold/game).

## 1. C2 — amortise `waveTick`

**Target.** `waveTick`, `src/player.cpp:240` in the `f18064c` numbering, marked
`__attribute__((noinline, cold))`, entered when `round % 20 == 0`.

**Measured cost (platform logs, 30 `f18064c` map1 games, our own `end.players[].cost`):**
**+70 ns P50 and +190 ns P90** on the 25 rounds per game where it runs. In steady state it is one of
only two variable-cost branches; the other is the `ok == 0` fallback.

**Measured value:** +1.95pp of first-mover rate → **+32.0 gold/game** (conservative
regression-discontinuity valuation at 3.289 gold per flipped round) or +40.9 on the identity
average. **Against the two fast opponents only.** See §0.

**Intent of the change:** spread the bomb-wave bookkeeping so no single round pays the whole
cost — the mechanism is a scheduling change, not a semantic one. Behaviour must be identical.

**Acceptance gates, all of which must pass:**

1. **`pair_diff` 0/500 on all three maps.** This is an equivalence refactor; any behavioural
   divergence means the change is wrong, not that the baseline was wrong.
2. ⚠️ **Re-check `moveDecision`'s mod64 alignment and re-tune the dead pad.** This is the gate that
   can invert the whole candidate. Entry alignment is a discrete cliff, not a gradient: `0x10` is
   the only non-degraded slot, and `0x20`/`0x30` each cost **+11.67 ns**. At 11 gold/ns that is
   **≈128 gold/game — four times C2's +32 gold gain.** Pad length is coupled to `decide`'s size, so
   *any* change in that translation unit can silently move it. `tests/verify_construct.sh` asserts
   `mod64 == 0x10` and suggests a pad length on failure; run it.
3. **Verify on cycles, not instruction count.** Instruction count has already passed a −67-gold
   construct: `golf1` was −42 instructions and +15–18 cycles. Marginal instruction price in this
   function is ~0.025 ns (IPC ≈ 4.14, dependency-bound), six times below the 0.1454 ns average, so
   deleted scalar instructions are often free and removing them buys nothing.
4. **Latency A/B must swap seats.** Slot effect is ±20 ns at P90, the same order as real effects; a
   fixed-seat A/B previously manufactured a +20 ns / 2.83σ regression that reversed on swap.
5. **FP16 count must be 0** and the artifact must be built on `quant-compiler`. Building elsewhere
   emitted AVX512-FP16 and the platform returned SIGILL, forfeiting a game.

**Expected net if gate 2 holds:** −70 ns × 25 rounds amortised, +1.95pp `f`, +32 gold/game against
T-1 and Tundra, ≈0 against the field. **If gate 2 fails and cannot be re-tuned, abandon C2** — the
layout tax alone is 4× the gain.

## 2. C1 — withdrawn, recorded with its gate so nobody re-proposes it blind

C1 was "make the `ok == 0` fallback branch branchless or warm", removing the measured **+40 ns per
fallback unit** on the **53.5%** of map1 rounds that take it (map2 38.6% at +35 ns, map3 50.2% at
+25 ns). Valued at +16.16pp of `f` → **+265.8 gold/game** against the two fast opponents.

It is withdrawn on the §0 grounds. If it is ever revived, its gate is a **value-collapse curve**,
which matters more than the headline:

| uniform cost added to the non-fallback path | net |
|---|---:|
| 0 ns | +265.8 |
| **10 ns (~70 instructions)** | **+164.3** |
| 20 ns | +46.3 |

**The non-fallback path may not get more than ~10 ns / ~70 instructions more expensive.** Since the
fallback path is currently `noinline, cold` (`steerStep` → `escapeStep` at `:156`), warming it means
moving work onto the hot path, which is exactly what the budget forbids. That tension is why the
candidate is unproven, not merely unfunded.

## 3. Two defects for this line to fix, both already independently confirmed negative-or-small

Reported for completeness; the parallel line has already measured both and recorded them.

1. **`ORT_A` stale constant.** The baked map1 opening route aims at `(6,6)/(10,10)` while the anchors
   have been `(6,8)/(11,8)` since 8.10. map1 reaches its anchors at median round **8.0** against
   map2's **6.5** (map2 uses a runtime BFS that targets the anchor directly). Sized at ~5–11
   gold/game. **The parallel line measured the fix as negative** (`44707ee`) — recorded so it is not
   retried.
2. **Richness-gate comment contradicts the engine.** The comment claims a poor unit burns
   `10% × 0 = 0`, but the engine charges `(held + 9) // 10`, so a unit holding 10–99 gold burns 1–9
   per bomb. Total burn is 219.4 gold/game with **99.4% falling on miss unit-rounds**. The threshold
   of 100 was chosen from the comment's premise. **The parallel line measured the threshold as a
   non-lever** (`515df3b`) — but the **comment is still wrong** and should be corrected so the next
   reader does not re-derive a mechanism from it.

## 4. One number worth recording in `INFRA.md`

The dispatch transfer function, recomputed by replaying the engine's order rule on logged costs,
gives **+5.73pp of first-mover rate per 10 ns**, which combined with the flip valuation yields
**9.4–12.0 gold/ns**. This is the **first independent confirmation** of `INFRA.md` §2.5's 11 gold/ns,
derived from primary platform logs by a completely different route than the original head-to-head
calibration. Worth recording as a cross-validation success, with the same caveat as the original:
it holds near the crossover band and decays away from it.


---

## 5. `C_k5_stay` — candidate, measured, sub-gate, with a cost range and one fatal gate

> Added after the step-budget round. **Not delivered, not landed.** Ownership of `src/player.cpp`,
> `src/INFRA.md` and `src/CHANGELOG.md` is currently **unassigned**, so nothing here may be applied
> until an owner is designated.

**What it is.** `k` is a split point over a fixed 6-action budget (`src/game_api.h:58-60`, engine at
`sim/engine.py:1089-1090`): unit 0 executes `actions[0..k-1]`, unit 1 executes `actions[k..5]`.
Currently `k` is always 3. The candidate sets the split so that **a unit which is `blind` — no `v>2`
anywhere in its own 5×5, about 20% of unit-rounds — receives 1 action (a stay) while the other unit
receives 5.** Must work in **both** directions, since either unit can be the blind one; supporting
one direction halves the benefit. Trigger is computed from our own 5×5 in the same round, so it needs
no dispatch-order knowledge, no opponent visibility and no snapshot.

**Measured (self-play, map1, both order arms, same-seed paired, judged on `margin` = change in
`ours − theirs`):** tune seeds 1000-1011 **+161.0 ± 45.4 (3.55σ)**, out-of-sample seeds 2000-2011
**+120.7 ± 38.9 (3.11σ)**, positive in **6 of 6** shards.

**Mechanism confirmed by a three-way control set**: silencing a *random* unit instead of the blind one
gives −85.5 / −118.7 (so the benefit requires the blind trigger specifically, and is **not** about
asymmetry); silencing the *productive* unit gives **−383 to −426 at −6.5 to −7.7σ**; a matched
silence control gives ≈0.

**Why it does not clear its gate.** The pre-registered gate was margin ≥ +150 on both seed sets. k5
clears on tune and misses out-of-sample; k6 does the reverse and is seed-unstable. Independently, the
genuinely free part of the pool is capped at **+83 to +106 gold/game** (fully idle unit-rounds are
~148 steps/game with income identically 0.000), and the measured +111 to +121 sits on that cap. So
this candidate **captures approximately all of the free waste and nothing beyond it** — which is why
the earlier "monotone rise to +194" red flag dissolved.

**Cost, and the gate that can invert it.** This is a **behaviour** change, not a table-value change:
the delivered `SLut` emits only 3 steps and the PACK ordering assumes 3+3, so action generation,
legality checking and PACK need extending. At a conservative average of 1.6 gold/instruction, 50
instructions ≈ 80 gold → **net ≈ +40**; at the measured marginal price of ~0.025 ns/instruction ≈
0.28 gold/instruction, 50 instructions ≈ 14 gold → **net ≈ +106**. So the honest range is
**+40 to +106**.

⚠️ **And it carries the alignment risk: `moveDecision` entry `mod64` must stay at `0x10`, because
`0x20`/`0x30` each cost +11.67 ns ≈ 128 gold — more than the whole candidate.** Pad length is coupled
to `decide`'s size, so any change in that translation unit can move it silently. **Run
`tests/verify_construct.sh` and re-tune the pad; without that step the +40 end of the range goes
negative.**

**Validation route.** Do not spend 8 platform games on it: at a per-game margin sd of ~250, 8 games
resolve +120 to only ~1.0σ — undecidable, and therefore a batch that cannot change a decision.
Reaching 2σ needs **n ≈ 17 per arm**. Preferred route is to **ride along** with the next behaviour
change that goes to the platform, provided attribution can be separated (different organs, locatable
by `pair_diff`); if it cannot be separated, schedule the 17 games rather than shipping an
unattributable batch. Transfer to T-1 is **unverified** — all measurements above are self-play.
