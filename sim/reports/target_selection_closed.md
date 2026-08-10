# CLOSED: the target-selection family

> Written 2026-08-10 by the orchestrator at the Master's instruction. **Read this before proposing
> any change to how `f18064c` chooses which cell to walk to.** Sixteen-plus candidates have been
> spent in this family and every one was judged negative. This file exists to stop the seventeenth.
>
> Evidence: `sim/reports/order_sensitivity.md`, `sim/reports/miss_taxonomy.md`,
> `sim/reports/path_harvest_verdict.md`, `sim/reports/map1_wall_repricing.md`. Baseline `f18064c`
> (`0ecce6fc…84fdd`). Zero platform games were spent on any of the four closing arguments below.

## The ceiling

**In both action-order conditions, the largest single class of our zero-yield unit-rounds is
"there was no target visible at all": 73.5% when we move first, 64.1% when we move second.**

No same-round re-decision can address those, because there is nothing in the window to re-decide
toward. Therefore:

| quantity | value |
|---|---:|
| share of our misses addressable by **any** selector change | **26.5% (our-first) / 35.9% (our-second)** |
| of which "a prior mover took it" | 0.0% / 25.3% |
| prior-mover theft weighted at the two strong opponents' `f` = 0.568/0.432 | **10.9% of all misses** |
| prior-mover theft weighted at the **field's** `f` ≈ 0.997 | **0.08% of all misses** |

The second row is structural, not sampled: `GameEngine._dispatch` (`sim/engine.py:925`) returns
`(faster,) + 7 NPCs + (slower,)`, and the faster player is a **single actor entry**, so its two
units and six steps all complete before any NPC moves. **Within a first-mover round nothing can
steal from our path.** Confirmed empirically: a perfect-prophecy oracle perturbs **0 of 500** rounds
and returns **exactly +0.0 gold** in the first-mover arm.

## The four independent refutations

**1. It is not fog.** An oracle given perfect within-window prophecy and an oracle given that *plus*
complete fog removal produce **identical** results to the gold (+84.6 ± 34.2 in the second-mover
arm, both). Removing fog contributes **exactly zero**, because the selector only ever reads its own
5×5 window. Buying vision does not help either: widening to a fog-free 7×7 raises the share of
blind-fold rounds that have any reachable positive cell only from 31–35% to 39–45%, at a mean total
value of 3.3–3.4 raw ≈ 2 gold of pickup. **The surface the selector would choose from does not
exist, so no amount of seeing more creates it.**

**2. It is not information.** The share of prior-mover theft that is in principle *visible* to us is
**94.0%** — but that figure is near-tautological, since a contestant able to take a cell we are about
to step onto must be adjacent, and adjacency implies visibility. The honest reading is: **we can see
it coming, and knowing does not help, because there is nowhere else to go.**

**3. It is not path shape, and this is the decisive one because the test was free.**
`fold_tour` — replacing the fold `(a, a^1, stay)` with a 3-distinct-cell tour `(a, p, a^1)`, a **pure
table change costing zero instructions** — is the **worst** arm measured: **−70.8 ± 33.6 gold/game
(−2.11σ)** against current and **−57.6 ± 26.7 (−2.16σ)** against never-folding; confirmed
out-of-sample at n=56 as **−81.4 ± 18.5 (−4.39σ)**, negative in *both* order arms.

The mechanism is parity, stated precisely because a loose version of it is false. Action `4` is
*stay* and does **not** change `(row + col)`; only an actual move flips that parity. So "three
actions cannot return to the origin" is **wrong** — `out, back, stay` uses two moves and returns.
The true statement is:

> **Visiting 3 distinct cells requires 3 actual moves, which is an odd number of parity flips, so a
> 3-distinct-cell tour can never end on its starting cell.**

That is what makes the tour lose: it necessarily displaces the unit off the central generation peak,
while the fold's two-move oscillation returns to it. Since the change is free, this is not "the
implementation was too expensive" — **the direction is wrong.** Note the corollary, which matters for
any future step-budget work: with an **even** number of moves available a unit *can* both visit extra
distinct cells and end where it started, so this argument does **not** generalise to 4- or 6-move
budgets.

**4. The whole upper bound is small anyway.** Perfect prophecy inside the window is worth
**+42.3 ± 18.5 pooled** (n=32 games, out-of-sample), **+36.6** weighted at the two strong opponents'
order frequency, and **+0.25 gold/game** at the field's `f` ≈ 0.997 — against a pre-registered
threshold of +150 and a matched-order level deficit of **−411**. The oracle recovers **10%** of the
deficit. Every cheap approximation restricted to real information is negative: −453.2, −130.0,
−49.7 gold/game pooled, and the best *implementable* lagged-inference variant is **−88.2 ± 23.0**.

> **Provenance note.** An earlier revision of this file quoted +57.4 pooled / +0.34 field. Those were
> **in-sample only** (n=16). Out of sample the second-mover arm shrank from +114.8 ± 52.6 (2.18σ) to
> +54.5 ± 44.5 (1.22σ), a 52% shrinkage, giving the pooled +42.3 above. **Every ruling is unchanged
> and every magnitude moved down**, so the closure is stronger, not weaker. This is also a live
> instance of why positives must be confirmed on disjoint seeds.

**5. And the order it would need is not observable.** `GameInput` carries no dispatch-order field —
the engine derives order by comparing the two decision costs, which are produced *by* the two
`moveDecision` calls. `GameOutput.order` is **our own output** selecting which of our two units steps
first (`src/player.cpp:525`, `order = my_units_gold[0] >= my_units_gold[1] ? 0 : 1`); reading it as a
dispatch signal is reading your own output back. So any order-conditioned mechanism must be
**lagged-adaptive**, and the best lagged variant measured costs **−88.2 ± 23.0 gold/game**, because
it fires in the first-mover arm where hedging is pure loss.

## A premise this family was built on, now corrected

The fold was described throughout as "standing on gold and double-eating the residual". **It is
mostly not that.** On 811 / 636 fold unit-rounds the residual on the re-bitten cell is **zero in
92.7% / 95.6%** of them. The dominant path into `d == 0` is: the unit is **blind** (no `v>2` anywhere
in its 5×5), so its target degenerates to its own anchor, and it is **already standing on that
anchor** — so it oscillates in place for nothing, about **200 unit-rounds per game = 20% of all
unit-rounds**, two of three steps spent returning to the start.

The "double-eat" reading holds only on the minority of fold rounds that do have residual — which is
where the ≥8 bursts come from — but as a description of the *class* it is wrong. **The two readings
imply opposite fixes: one says the selector is not greedy enough, the other says the unit is standing
somewhere that can see nothing.** The second is a positioning problem and is where the line moved.

## Candidates already spent in this family — check here before proposing

Threshold moves: `v>2`→`v>0` (**+8.51pp hit rate, relative −75.3 gold** — it closes the entire
hit-rate gap and loses money), `v>2`→`v>1`, `≥4`, `≥5`. Amount-priority ordering (`23db121`,
reverted: +7.63% per bite, net +41.1 ± 51.7 = null at zero latency cost, then −304.7/−376.8
head-to-head). Three-step path greedy (−64.7 / −789.7 / −832.4 at three rungs). Wall side-step,
unsafe (−373) and safe (−51.5). Blocked-residual fallback (37.3 gold, below the 50-gold resolution
gate). Safe two-layer low-value mask (+13.43 ± 19.08, failed mechanism gate). Outer-ring hotspot
raiding (median −477). Idle sweeps: two-point oscillation (−11.25), small ring (−7.75). NPC-style
target inertia. This round: `fold_never`, `fold_tour`, `fold_tour_cond`, `fold_seek`, and three
contested-cell-avoidance arms.

## What would legitimately reopen this family

Not a new scoring rule, not a new threshold, and not more vision. Only one of these:

1. Evidence that the **"no target visible" share falls materially below 73.5% / 64.1%** — which is a
   *positioning* result, not a selector result, and would have to come from the positioning line.
2. A rule change in the actual contest that alters window size, step count, or the dispatch order
   (`(faster) + NPCs + (slower)`), any of which invalidates the structural-zero argument.
3. A demonstration that the parity argument in refutation 3 fails — i.e. a 3-action sequence that
   both touches 3 distinct cells and ends on the origin. **This is impossible on a 4-neighbour
   grid**: 3 distinct cells require 3 actual moves, each flipping the parity of `row + col`, and an
   odd number of flips cannot return. Closed by geometry rather than by measurement. **This applies
   only to a 3-move budget**; it says nothing about 4- or 6-move budgets, where closure is possible.

Anything else is a re-run of one of the rows above.
