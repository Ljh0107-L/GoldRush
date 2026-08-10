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
complete fog removal produce **identical** results to the gold (+114.8 ± 52.6 in the second-mover
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
(−2.11σ)** against current and **−57.6 ± 26.7 (−2.16σ)** against never-folding. The mechanism is
parity: three cardinal steps cannot return to the origin, so *any* 3-distinct-cell tour necessarily
displaces the unit off the central generation peak, while oscillating in place keeps it there. Since
the change is free, this is not "the implementation was too expensive" — **the direction is wrong.**

**4. The whole upper bound is small anyway.** Perfect prophecy inside the window is worth
**+57.4 ± 29.4 pooled**, **+49.6** weighted at the two strong opponents' order frequency, and
**+0.34 gold/game** at the field's `f` ≈ 0.997 — against a pre-registered threshold of +150 and a
matched-order level deficit of **−411**. The oracle recovers **14%** of the deficit. Every cheap
approximation restricted to real information is negative: −453.2, −130.0, −49.7 gold/game pooled.

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
3. A demonstration that the parity argument in refutation 3 fails — e.g. a 3-step sequence that both
   touches 3 distinct cells and ends on the origin. **This is impossible on a 4-neighbour grid**
   (each step flips the parity of `row + col`, so three steps cannot return), so this route is closed
   by geometry rather than by measurement.

Anything else is a re-run of one of the rows above.
