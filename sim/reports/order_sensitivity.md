# Order sensitivity: is it an exploitable lever?

> Round of 2026-08-10. **Zero platform games consumed** — every number below is either read out of
> archived logs or produced by the local simulator. Baseline pinned to **`f18064c`**
> (`git show f18064c:src/player.cpp`, `shasum -a 256` =
> `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`). Nothing under `src/` was
> touched; no behaviour was implemented. Measurement and judgement only.
>
> Driver: `sim/analyze_order_sensitivity.py`. Companion: `sim/reports/order_sensitivity.json`.

## 0. Answer first

**此路不通 / this path is closed.** All four pre-registered gates were tested and three of the four
fire against the hypothesis. Stated as the one unmistakable sentence the brief asks for:

> **The oracle's closed-loop, same-seed paired net delta is +42.3 ± 18.5 gold/game pooled over both
> order conditions (+36.6 when weighted at the opponents' first-mover rate `f` = 0.568, +0.25 at the
> field's `f` = 0.997, and +84.6 ± 34.2 in the single most favourable arm), which is below the
> pre-registered +150 gold/game threshold on every reading — so the path is closed; the
> information-accessible share is 92.6% and therefore does *not* fire its 30% gate, but the
> first-mover share of the mechanism's benefit is exactly 0.0% against a 40% gate, and the
> conditional fold beats neither of its two baselines, so the mainline fails on three gates out of
> four.**

| # | pre-registered gate | measured | ruling |
|---|---|---:|---|
| 1 | oracle closed-loop ≥ +150 gold/game | **+42.3 ± 18.5** pooled; +36.6 at platform `f`; +84.6 best arm | **FIRES — path closed** |
| 2 | information-accessible share ≥ 30% | **92.6%** | does not fire |
| 3 | ≥ 40% of benefit under our-first | **0.0%**, structurally | **FIRES — field-ineffective** |
| 4 | conditional fold beats **both** baselines | beats **neither** (`fold_seek` −5.9 ± 17.8 vs current, −1.3 ± 16.4 vs never-fold) | **FIRES — cheapest form failed** |

Three things came out of this that are worth more than the negative verdict:

1. **The simulator reproduces the platform's order-sensitivity asymmetry**, and therefore **every
   prior local A/B in this repository is a first-mover measurement only.** §8 quantifies it and
   gives guardrail text for `sim/README.md`.
2. **The headline anchor is confounded.** Our 2.38× order-sensitivity ratio falls to **1.767×** in
   the cost-matched (near-tie) stratum while the opponents' falls only 1.647× → 1.562×, so the
   *causal* excess is ~1.13×, not ~1.44×. §9.1.
3. **The fold is not what it has been described as.** In 92.7% / 95.6% of the unit-rounds where the
   build emits its "fold-back double-eat" triple, the standing residual is **zero** — it is idle
   oscillation on the anchor, not re-biting a rich cell. §7.1.

---

## 1. Substrate and fidelity gates

Host build: `clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include shim.h` on arm64, so the guarded
scalar fallback compiles and the AVX2 path is not exercised. The fallback is behaviourally
equivalent for target selection, which the gates below independently confirm.

Order is manipulated at a **fixed seat** by flipping the deterministic cost pair. `GameEngine._dispatch`
(`sim/engine.py:925`) computes `faster = 1 if costs[1] <= costs[2] else 2`, so with our strategy at
seat 1:

* `--fixed-costs 200,201` → **we move first**
* `--fixed-costs 201,200` → **we move second**

Both legs share the same seed and therefore the same `scenario_digest`, and the NPC seeds are derived
from that digest and the round number only, so this is a clean order manipulation with no other
difference. This is the design no local A/B in this repo has run before.

### 1.1 An exact within-round prophecy engine (new, reusable)

To build an oracle that "knows the dispatch order and where the opponent will move", the driver
reconstructs the opponent's own `PlayerInput` from the frozen `RoundStart`
(`GameEngine.render_filtered_ground` + `visible_cells`, both class/static methods), queries a
**private copy of the opponent `.so`** for its current-round decision, replays all seven NPC rolls
with `NPCModel(seed=_stable_seed("npc-policy", digest, round, npc_id))` evaluated at each NPC's true
dispatch turn on the then-current ground — exactly as `sim/runner._npc_policy` builds them — and
re-derives movement, `ceil(0.65 v)` pickup, bomb consumption and the ≥3-NPC trample from
`execute_round`'s own `execute_action`.

This is legitimate and exact precisely **because** of the dispatch shape: when we are the slower
player the engine settles `(opponent, NPC×7, us)`, and none of those nine actor-turns can depend on
our current-round actions. The board we will face is therefore computable before we decide.

| gate | result |
|---|---|
| predicted end-of-round pure ground == official log, cell by cell | **9000/9000 rounds** (8000 in `step0` at 8 seeds × 2 order arms, 1000 in `verify`), both order conditions |
| passthrough arm's `log_sha256` == plain baseline's | 2/2 arms, byte-identical |
| oracle **identical to base** in the first-mover condition (null control) | 16/16 games, 0/500 rounds perturbed |
| oracle does perturb in the second-mover condition | 347/500 rounds, 16/16 games |
| bit-exact selector replica reproduces the emitted triple | 99.5% of 16,000 unit-rounds (the 0.5% is the round<8 opening layer) |

The first-mover null control is the most useful of these: it is not a check that happened to pass,
it is a *structural* prediction of the dispatch shape, and it is what makes §4's ruling airtight.

---

## 2. Step 1 — information availability, answered from the actual contract

### 2.1 Can our code know, during a round, whether it is moving first or second?

**No.** `GameInput` (`src/game_api.h`; byte layout asserted at import by `sim/abi.py`'s
`verify_abi_layout`, size 1444, ten fields) exposes exactly:

| field | what it carries about move order |
|---|---|
| `round` | nothing |
| `grid[17][17]` | fogged pure terrain; **actors are not marked**, so a drained cell is indistinguishable from a cell that never held gold |
| `my_units[2]`, `my_units_gold[2]` | nothing |
| `gold_opp` | **lagged**: its round-over-round increment is the opponent's income *last* round |
| `visible_enemies[2]` | positions inside our own 5×5 union only, compacted, identity withheld |
| `num_visible_npcs`, `visible_npcs[7]` | same fog; `id` is stable across rounds |
| `snapshot_valid`, `snapshot` | fog-free but **windowed and lagged** — `[window_begin, window_end]` has already closed |

The engine decides order by comparing the two decision costs. Those costs are produced *by* the two
`moveDecision` calls, so neither caller can read them at decision time; and no field mirrors them.

### 2.2 The `order`-field trap, addressed explicitly

`GameOutput.order` (offset 28) is **not** the dispatch order. The header says
`0=角色0先执行 1=角色1先执行` — it selects which of **our own two units** steps first, and it is an
**output we choose**, not an input we read. The frozen build sets it from held gold:

```cpp
// f18064c src/player.cpp:525
out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
```

with the design note at line 28 reading `order=持金多者先走` ("whoever holds more gold goes first").
Anyone reading `order` as a dispatch signal is reading their own output back.

### 2.3 Consequence, stated plainly

**No same-round mechanism conditioned on dispatch order is expressible. Any such mechanism must be
lagged-adaptive.** The available lagged channels are the `gold_opp` increment, grid deltas on
remembered cells ("I saw gold here last round, it is empty now and I did not take it"), and the
snapshot's per-region `gold_collected`.

§6 prices the best lagged mechanism I could build (`cheap_lagged`): **−88.2 ± 23.0 gold/game**
pooled. Lagged inference is not merely weaker than same-round knowledge — applying it costs money,
because it fires in the first-mover condition where the hedge is pure loss.

---

## 3. Step 1b + Step 2 — visibility, contestability, accessible share

### 3.1 A withdrawn artifact first

Measuring opponent visibility with `base` on both seats is **invalid**: both seats camp the identical
central anchors `(6,8)`/`(11,8)`, so all four units sit permanently inside each other's windows and
enemy visibility inflates to 93.2%. That number is withdrawn. The honest condition is a non-camping
opponent; the table below uses `MonotoneHarvester`, a **fitted** reference encoding only the one
behavioural difference the opponent census established (monotone three-step paths, zero reversals,
zero within-round revisits over 77,756 clean opponent unit-rounds).

### 3.2 Measured shares, map1, 2,952 rounds per arm, steady rounds ≥ 8

| quantity | our-first | our-second |
|---|---:|---:|
| **no** opponent unit visible | **97.66%** | **91.73%** |
| at least one opponent unit visible | 2.34% | 8.27% |
| both opponent units visible | 0.00% | 0.88% |
| at least one NPC visible | 83.50% | 89.50% |
| mean visible NPCs | 2.20 | 2.46 |
| a **visible opponent** within 3 steps of a cell we actually enter | **2.03%** | **7.15%** |
| the same against fog-free truth | 3.62% | 9.65% |
| a visible **contestant of any kind** within 3 steps of a cell we enter | 79.74% | 87.09% |
| the same against fog-free truth | 86.96% | 92.58% |
| **visible / true contestability** | **0.917** | **0.941** |

Two readings, both load-bearing:

* **The opponent channel is tiny.** Even with perfect information, an opponent unit is in contest
  range of a cell we step on in only 3.6% / 9.7% of rounds. The two extreme local estimates of
  opponent visibility (6.4% invisible under self-play, 91.7–97.7% invisible against a non-camping
  reference) bracket the platform's ~56%-both-invisible figure, which remains the number to use;
  local self-play cannot estimate it at all.
* **Fog is not the bottleneck.** 92–94% of true contestability is already visible, because the
  contestants that can take a cell we are about to step on are contestants *adjacent* to us, and
  adjacency implies visibility.

### 3.3 The information-accessible share, as a number

Definition: of the gold that prior movers actually strip out of the cells our units then enter,
what fraction was removed by an actor whose origin was inside our own 5×5 union **and** within 3
steps of that cell — i.e. an actor we could see and could have known was a threat to that cell.

**Accessible share = 92.6%** (0.9258 against the monotone reference; 0.9401 under self-play, i.e.
robust to the contest). **Gate 2 (30%) does not fire.**

The correct interpretation is the opposite of the brief's expectation, and it matters:

> The bound is not unreachable because we cannot see the theft coming. It is unreachable because we
> **can** see it coming and knowing does not help — there is nothing else to go to. §7.2 measures
> that directly: in only 31–35% of the rounds where the build gives up and oscillates is *any* other
> positive cell reachable at all, and widening to a fog-free 7×7 raises that only to 39–45%.

Applying the discount as the brief requires: 0.926 × the Step 3 bound of +42.3 = **+39.2 gold/game**.
The discount is not what closes this path; the raw bound already is.

Operational cross-check, from §6: the *realized* share of the oracle that survives restriction to
real information is **17.5% in the second-mover arm and ≤ 0% pooled** — an order of magnitude below
the information-availability share, which is itself the finding that information was never the
binding constraint.

---

## 4. Step 0 — where the benefit lands (inserted gate; it decides the shape)

### 4.1 The dispatch shape makes the first-mover share exactly zero

`GameEngine._dispatch` returns `(faster,) + (7 NPCs) + (slower,)`. The faster player is a **single
actor entry**, so its entire two-unit, three-step turn completes before NPC 1 moves. Log-verified
1500/1500 per `sim/README.md`'s fidelity matrix, and re-confirmed here by the 9000/9000 ground gate.

**Nothing can steal from a first mover's three-step path within that round.** Within-round
survival-aware targeting has structurally zero value in the first-mover condition.

| within-round gold stripped off our realized path | per game |
|---|---:|
| when we move **first** | **0.0** |
| when we move **second** | **595.3** — of which NPCs 548.5 (92.1%) and the opponent 46.7 (7.9%) |
| **first-mover share of the channel** | **0.000** |

**Gate 3 fires: 0% < 40% ⇒ field-ineffective.** Against a field where 82 of 100 ranked teams have
P90 > 1 μs and our first-mover rate is 0.997, a second-mover-only mechanism buys ≈ 0 in ~99.7% of
rounds. And the Master's stated mechanism — "an opponent moving first strips the peak cells" — is
7.9% of an already order-gated channel; against a non-camping opponent it is **2.2%** (§3, monotone
contest: NPC 98.3% / opponent 2.2%).

Bias label, as required: the local NPC model is over-greedy and over-central at ~39% per-action
accuracy, so the NPC share of the strip is **over**-stated locally. The platform anchor (NPC 65.6%
of map1's gold against theirs 18.7%) says the direction is right and the split milder.

### 4.2 MECE reasons a miss happened, separately by order

7,872 unit-rounds per arm, 8 seeds, steady rounds ≥ 8. A "miss" is a unit-round with non-positive
held-gold delta.

| reason | our-first | our-second |
|---|---:|---:|
| (i) an NPC took our target | **0.0%** (structural) | 15.4% |
| (ii) the opponent took our target | **0.0%** (structural) | 9.9% |
| (iii) no target visible at all | **73.5%** | **64.1%** |
| (iii) never reached the target | 26.1% | 10.4% |
| (iv) reached it, it was empty | 0.4% | 0.2% |
| misses | 5,417 | 6,442 |

### 4.3 First-class finding: a ceiling on the whole target-selector family

**The largest miss class in both order conditions is "no target visible at all" — 73.5% our-first and
64.1% our-second — and no same-round re-decision can fix it.** Stated as a ceiling: **at most 26.5%
(first) / 35.9% (second) of our misses are addressable by any change to the target selector.** The
prior-mover-theft subset of that is 0.0% / 25.3%; weighted at the opponents' `f` = 0.568/0.432,
prior-mover theft is **10.9% of all our misses**, and at the field's `f` = 0.997 it is **0.08%**.

Any future proposal of the form "choose targets better" must first say how it escapes this ceiling.

### 4.4 The supply-binned test: scarcity is real but a minority of the effect

Binned by **true reachable supply at our own dispatch turn** (cells holding gold within Manhattan 3):

| true reachable supply | our-first income/unit-round | our-second | ratio |
|---|---:|---:|---:|
| 0 | −0.268 | −0.163 | — |
| 1 | +1.583 | +0.940 | 1.68× |
| 2 | +2.579 | +1.698 | 1.52× |
| 3–4 | +3.721 | +2.375 | 1.57× |
| share of unit-rounds at supply 0 | 35.3% | **52.8%** | |

Exact decomposition of the −0.811 gold/unit-round order penalty by standardising the second-mover
income on the first-mover supply weights: **40.3% is supply thinning** (the board really is emptier)
and **59.7% survives at matched true supply**.

### 4.5 Ours versus theirs as supply thins — the direct comparison

Income per unit-round binned by pre-round reachable supply, fog-free from the god-view log so it
works against any policy, with the order ratio standardised on matched supply:

| entity | raw first/second ratio | supply-standardised | matched-supply ratios (supply 1 / 2 / 3–4) |
|---|---:|---:|---|
| **ours (`f18064c`)** | **1.950×** | **1.832×** | 2.10 / 1.75 / 1.77 |
| monotone reference | 1.266× | **1.048×** | 1.37 / 0.89 / 0.91 |

**Answer to "does our income fall off faster than theirs as supply thins": in level terms no — we
out-earn the reference at every supply level. In order terms yes, decisively — at matched local
supply our second-mover penalty is ~1.8× while the reference's is ~1.0×.** Only 4–6% of our order
penalty is composition (being placed in thinner windows); 94–96% is within-bucket.

So the sharpest available reading is neither "order sensitivity" nor "scarcity sensitivity" but
**commitment concentration**: a monotone three-distinct-cell path gets three independent chances to
land on surviving gold; ours gets essentially one. Supporting numbers: our `mean_distinct_cells` is
1.86 (first) / 1.88 (second) against the opponents' measured 3.000; our fold share *rises* 65.3% →
67.9% when the board is thinner; our yield-per-hit falls only 5.06 → 3.80 (−25%) while our hit rate
falls 0.312 → 0.182 (−42%) — we still extract well when we land, we stop landing.

**§7 tests that reading directly, and it fails too** — which is the most informative result in this
report, because it is the reading's own prediction that is refuted.

### 4.6 The cross-round NPC channel, separated

Gold consumed **inside our own 5×5 union**, per game:

| | NPC | opponent | us |
|---|---:|---:|---:|
| when we move first | **1021.5** | 255.5 | 1581.3 |
| when we move second | 1398.8 | 810.0 | 699.7 |

NPCs eat ~1021 gold/game out of our own windows **even when we always move first**. Board-wide
consumption shares are NPC 0.764 / 0.773, opponent 0.074 / 0.156, ours 0.162 / 0.072 — the order
effect is a near-perfect *transfer between the two players* with the NPC share invariant. The
cross-round NPC channel is large and order-independent; the within-round channel is entirely
order-gated. Conflating them is what produced the original mechanical error.

---

## 5. Step 3 — the upper bound (labelled a BOUND; perfect information)

The oracle is a **grid perturbation**: the real frozen `.so` is called with a `GameInput` whose grid
is replaced by the **true board at our own dispatch turn**. Everything else in the build — opening
layer, fingerprint lock, anchors, LUT router, `pass01` gate, escape mask, fold — is bit-identical, so
the measured delta is attributable to move-order information and nothing else. It is not a strategy
proposal: it requires the opponent's current-round decision and all seven NPC rolls, which no
submitted `.so` can obtain.

Closed-loop, same-seed paired, map1, 16 seeds (1000–1007 plus disjoint 2000–2007) × 2 order arms:

| arm | our-first | our-second | pooled (equal weight) |
|---|---:|---:|---:|
| `prophet` — perfect prophecy inside our own 5×5 | **+0.00 exactly** (bit-identical, 0/500 rounds perturbed) | **+84.62 ± 34.17 (2.48σ)** | **+42.31 ± 18.45 (2.29σ)** |
| margin (ours − theirs) | +0.00 | +108.69 ± 46.66 | +54.34 ± 24.94 |
| `prophet_free` — same, fog also removed | +0.00 | **+114.8 ± 52.6 — identical to `prophet` to the gold** | — |

Out-of-sample behaviour, exactly the check the brief demands: the second-mover arm measures
**+114.8 ± 52.6 (2.18σ)** on the tuning seeds and **+54.5 ± 44.5 (1.22σ)** on the disjoint seeds — a
**52% shrinkage**, and the out-of-sample arm alone is not significant. The pooled figure above is the
honest one.

Weighted by real first-mover rates:

| weighting | oracle value |
|---|---:|
| equal weight over both arms | **+42.3 gold/game** |
| the opponents' `f` = 0.568 (T-1 / Tundra) | **+36.6 gold/game** |
| the field's `f` = 0.997 | **+0.25 gold/game** |
| after the 92.6% accessibility discount (equal weight) | **+39.2 gold/game** |

**No stock/flow discount is applied anywhere.** Under contention the premise of that discount fails
(NPCs take 65.6% of map1; after losing a race the gap does not close within 5 rounds), so gold lost
to a race is treated as 100% novel, exactly as the scope warning requires. The >800 gold/game
tripwire never fired: the largest closed-loop figure in this report is +114.8.

**`prophet_free == prophet` exactly** is worth recording on its own: removing fog from the prophecy
adds **zero**, because the selector only ever reads its own 5×5 window. Buying vision cannot help
this mechanism.

### 5.1 Ruling against the +150 gate and against the ruler

**+42.3 pooled, +36.6 at platform order frequency, +84.6 in the single most favourable arm — below
+150 on every reading. Gate 1 fires.**

Against the sizing: the oracle recovers **20%** of the arithmetically correct closure figure
(+210.1 gold/game for closing 2.38× → 1.52× at `f` = 0.568; the +300 figure was arithmetically
wrong) and **10%** of the matched-order level deficit of −411 gold/game. The 80–90% shortfall is the
part positional income makes unreachable, and that shortfall is the finding: **a perfect, free,
same-round oracle over move order recovers a tenth of the deficit, and only in the arm that barely
occurs against the field.**

---

## 6. Step 4 — the cheap approximation

Restricted to (a) cells inside our own visibility union and (b) no knowledge of this round's dispatch
order. The only hedge expressible without order knowledge is to discount cells a *visible* contestant
could reach in three steps by a value multiplier; `cheap_lagged` additionally gates the hedge on the
lagged order estimate (a cell we saw holding gold last round is empty now and we did not take it).

| arm | our-first | our-second | pooled |
|---|---:|---:|---:|
| `cheap_r000` — avoid contested cells outright | −771.0 ± 22.1 | −135.5 ± 38.2 | **−453.2 ± 84.8** |
| `cheap_r050` — halve their value | −255.5 ± 37.1 | −4.5 ± 27.6 | −130.0 ± 39.3 |
| `cheap_lagged_r000` — hedge only when lagged evidence says we were second (n=32) | **−151.6 ± 34.2 (−4.44σ)** | −24.8 ± 21.8 (−1.14σ) | **−88.2 ± 23.0 (−3.84σ)** |

**Surviving fraction of the oracle: ≤ 0% pooled for every arm.** The best arm reaches
+20.1 / 114.8 = **17.5%** of the oracle *in the second-mover arm on the tuning seeds only*, and pays
−151.6 for it in the first-mover arm, where the hedge is pure loss because nothing can steal from a
first mover's path — the structural fact from §4.1 turned into a bill.

Instruction pricing, per the brief's 1.6 gold/instruction conservative average ceiling: the gap
between oracle and cheap approximation is not a matter of instructions at all. The oracle's extra
information is the opponent's current-round decision plus seven NPC rolls; no instruction budget buys
it. The *lagged* approximation is cheap (a 289-bit remembered-gold bitmap plus a compare, ~20–40
instructions ⇒ 32–64 gold at 1.6 gold/instruction) and it measures **−88.2 gold/game**, so it is
negative before it is charged for. The brief's own caution applies: the 11 gold/ns rate holds only
inside the ±20 ns crossover band, and the frozen source's own header records deleting 84 instructions
returning only 5.6 cycles, six times below average.

---

## 7. The conditional fold (the Master's candidate, promoted ahead of the oracle)

### 7.1 A premise correction: the fold is idle oscillation, not double-eating

The `d == 0` branch emits `(a, a^1, STAY)` — step out, step straight back. The signature is unique:
the LUT emits three moves for `d ≥ 1` (`d == 1` → `(a, a^1, a)`, `d == 2` → `(a, b, b^1)`), and the
`ok == 0` fallback emits `(a, STAY, STAY)` or `(STAY, STAY, STAY)`.

`foldprobe` measures the standing residual on every such unit-round:

| | our-first | our-second |
|---|---:|---:|
| fold unit-rounds per game | 202.8 (20% of all unit-rounds) | 159.0 |
| standing residual **= 0** | **92.7%** | **95.6%** |
| mean standing residual | 0.186 | 0.124 |

**The fold is overwhelmingly not a re-bite.** The dominant route into `d == 0` is the third one: the
unit is `blind` (no `v > 2` anywhere in its 5×5), so its target becomes its own anchor `(6,8)` /
`(11,8)`, and it is *already standing on that anchor* — so `d == 0` and it oscillates in place for
nothing, spending two of three steps returning to where it started. The published framing
("61.1% of our ≥8 bursts are one cell bitten twice") describes a real but different and much smaller
phenomenon.

### 7.2 The decision surface is small, and vision does not enlarge it

Share of fold unit-rounds in which **any** other positive cell is reachable within three steps:

| window | our-first | our-second | mean value of the alternatives when any |
|---|---:|---:|---:|
| fogged 5×5 (what the build has) | **31.3%** | **34.6%** | 1.70 / 1.78 |
| fogged 7×7 (a `vp=1` purchase) | 33.1% | 37.3% | 1.90 / 1.97 |
| fog-free 7×7 | 38.8% | 44.8% | 3.31 / 3.41 |
| fog-free 9×9 | 38.8% | 44.8% | 3.31 / 3.41 |

In ~2/3 of the rounds where the build gives up, **there is nothing to give up in favour of**, and
buying vision does not create the surface. That is the same wall as §3.3.

### 7.3 Three-plus arms, closed-loop, both order conditions separate

Tuning on seeds 1000–1007, confirmation on disjoint seeds 2000–2019, pooled n=56 games (28 seeds × 2
order arms). All arms are built out of the frozen build itself; the candidate must beat **both**
`current` (delta > 0) and `never` (delta − never > 0).

| arm | our-first | our-second | pooled | vs `fold_never` |
|---|---:|---:|---:|---:|
| `fold_never` — the already-measured ablation (published +36/+5/+4, all < 2 SE) | −3.9 ± 31.1 | −5.2 ± 22.4 | **−4.6 ± 19.0 (−0.24σ)** | — |
| `fold_seek` — lower the `v > 2` scan constant to `v > 0` for that unit, so the build's own selector and LUT walk to a distinct cell | −8.1 ± 27.9 | −3.6 ± 22.5 | **−5.9 ± 17.8 (−0.33σ)** | **−1.3 ± 16.4 (−0.08σ)** |
| `fold_tour` — **zero extra instructions**: replace the table entry `(a, a^1, 4)` with `(a, p, a^1)`, three steps, three distinct cells | −104.1 ± 30.0 | −58.6 ± 21.4 | **−81.4 ± 18.5 (−4.39σ)** | **−76.8 ± 18.8 (−4.09σ)** |
| `fold_tour_cond` — `tour` only when a positive cell is reachable (tuning only, n=16) | −69.6 ± 44.9 | −5.5 ± 45.8 | −37.6 ± 32.1 | −24.4 ± 44.3 |
| `fold_cond_t3` — the original standing-residual conditional (OOS n=40) | +14.8 ± 27.2 | −35.1 ± 19.6 | −10.2 ± 17.0 | −12.0 ± 20.4 |

**RULING ON GATE 4: the conditional fold beats NEITHER baseline. `fold_seek` loses to `current`
(−5.9 ± 17.8) and ties `never` (−1.3 ± 16.4); `fold_tour` loses to both at −4.39σ / −4.09σ. The
cheapest form of the diversification hypothesis has FAILED.** This is not "undecidable between one
and two baselines" — it beats zero of the two.

Two by-products worth keeping:

* **`fold_never` is now measured at −4.6 ± 19.0 over 56 games.** The published +36/+5/+4 is confirmed
  as noise and the pooled sign has gone mildly negative. That closes it as an asset question.
* **`fold_tour` is the zero-instruction arm and it is the worst arm, significantly so.** Touching
  three distinct cells instead of oscillating costs ~81 gold/game, because three steps cannot return
  to the start by parity, so the tour drifts the unit **off the central generation peak** while the
  oscillation keeps it on. That is a direct, closed-loop confirmation of positional income: the value
  is in *where you stand*, not *how many cells you touch*. It also explains the `fold_never` null
  cleanly — the real alternative to folding is standing still, and standing still on the peak is
  nearly as good as oscillating on it.

### 7.4 Simulator-bias discount interval, as a number

Two families with **opposite** bias directions:

* **Avoidance / contention family** (the oracle, the `cheap` arms). Value scales with how much prior
  movers strip. Locally NPCs over-eat by 1.235×–1.715× (`[8943, 8958, 8543]` against a truth of
  `[5216, 7051, 6916]`) and the local NPC consumption share is 0.768 against the platform's 0.656.
  ⇒ **local × [0.58, 0.85]** on platform.
* **Diversification family** (`tour`, `seek`). Value scales with *surviving* local density, which is
  **lower** locally (P90 clean gold lifetime 10 against a truth of 13) ⇒ value multiplier 1.30–1.72;
  but the trigger (blind rounds) is correspondingly *more* frequent locally. Under a Poisson window
  model, local blind share 0.524 ⇒ λ = 0.646; platform λ = 0.84–1.11 ⇒ blind share 0.33–0.43 ⇒
  frequency × 0.63–0.83. Net **1.07–1.08**, honest envelope **[0.8, 1.4]**.

**Explicit limit, since a false point estimate would be worse than none: the calibration cannot
resolve better than a factor of ≈1.7** (the map spread of the NPC over-eating ratio), and because the
simulator's per-map ordering is inverted (Spearman −1) the map1 figure **cannot** be transferred to
platform map1 at all — only the pooled figure can. With local effects of |≤ 81| gold and SEs of
±18–34, a ±40% multiplier is second-order: **no multiplier inside either interval turns any arm
positive**, so the bias interval is not the reason anything here failed.

---

## 8. Does the simulator reproduce the platform's order-sensitivity asymmetry?

**Yes — both the magnitude and the asymmetry.** This is the most consequential by-product of the
round, because it converts "no local A/B has run the second-mover arm" from an oversight into a
measured bias in every prior local conclusion.

| quantity | platform (30 games, 14,970 rounds) | local |
|---|---:|---:|
| our first/second income ratio | **2.385×** | **2.668× (seat 1), 2.254× (seat 2)** — base vs base, same seed, same `scenario_digest`, only `fixed_costs` flipped |
| ratio-of-ratios, ours vs the counterparty | **1.448×** (2.385 / 1.647) | **1.540×** (1.950 / 1.266, against the fitted monotone reference) |

Caveat on the local asymmetry figure: the monotone reference is weak in absolute terms (0.34
gold/unit-round against real opponents' ~1.8), and low-income policies have compressed ratios, so
1.540 should be read as "the direction and rough magnitude reproduce", not as a calibrated estimate.
The *magnitude* row needs no such caveat — it is our own build against itself.

### 8.1 Recommended guardrail text for `sim/README.md` (§4.1, new)

> ### 4.1 强制：本地 A/B 必须包含后手臂
>
> `--dispatch fixed --fixed-costs 200,201` 让 P1 恒为快方。**一个 `--fixed-costs` 只覆盖一种行动顺序。**
> 我方在先手臂的收入约为后手臂的 2.4 倍（本地 base-vs-base 实测 2.67×/2.25×，平台观测 2.385×），
> 且 `GameEngine._dispatch` 返回 `(faster,) + 7 NPC + (slower,)` —— 快方是**单个 actor 条目**，
> 其两单位三步全部走完后 NPC 才动，所以**先手臂内"回合内被抢"是结构性的 0**。
> 只跑先手臂等于只测最有利、且最不可能暴露目标选择缺陷的那一半。
>
> 因此：任何 A/B 都必须同时跑 `--fixed-costs 200,201`（我方先手）与 `201,200`（我方后手），
> **分开汇报**，再按对手的真实先手率加权（T-1/Tundra `f`≈0.568；场地整体 `f`≈0.997）。
> 两腿共享同一 `scenario_digest`，是干净的顺序操纵。
>
> **本仓库此前所有本地 A/B 结论只在"我方先手"条件下成立。** 需要复检旧判决的后手臂时，
> `sim/analyze_order_sensitivity.py` 提供逐回合精确的回合内预言机（重构对手 `player_input` +
> 7 个 NPC 按真实 dispatch 时点重放；验收：预测的回合末纯地面 == 官方日志 **9000/9000** 回合，
> 两种顺序条件均通过），可直接复用。

---

## 9. Corrections to the brief and to the record

### 9.1 The 2.38× vs field-median-1.52× anchor is largely a decision-cost confound

Re-running `sim/analyze_map1_lesion.order_sensitivity` with the near-tie regression-discontinuity
window, the stratum in which our own cost — hence our own branch mix and local state — is matched
across arms:

| stratum | our ratio | opponents' ratio | ratio-of-ratios | our absolute order gap | theirs |
|---|---:|---:|---:|---:|---:|
| observational (the anchor) | **2.385×** | 1.647× | **1.448×** | 2.3655 gold/round | 1.8315 |
| RD, \|cost gap\| ≤ 10 ns | **1.767×** | 1.562× | **1.131×** | **1.6310** | **1.6575** |
| RD, ≤ 20 ns | 1.905× | 1.530× | 1.245× | 1.8067 | 1.5773 |

Per-account, on every field account with ≥ 200 unit-rounds in both strata, the same collapse:
`player163` 1.653× → **1.131×**, `player57` 1.393× → **1.068×**.

In *absolute* gold the RD order gap is **1.631/round for us against 1.658 for them** — once cost is
matched we lose very slightly **less** per round from moving second than they do. The observational
ratio looks worse only because our *denominator* (second-mover income) is smaller, which is the
level deficit the `map1_lesion.md` erratum already identified.

Mechanism of the confound: rounds where we move second are rounds our decision cost was high; our
cost is high on the fallback branch (+40 ns per fallback unit, firing on 53.5% of rounds); the
fallback fires precisely when the LUT path is **blocked** — a bad local situation. Bad situation →
slow → second **and** low income. Reverse causation, not order causation.

**Consequence:** the 91st-percentile framing is an observational percentile. The causally
attributable excess is ~1.07–1.13×, not ~1.44×, and the headroom that "fixing order sensitivity"
could recover is correspondingly smaller than any of the sizings on the table.

### 9.2 The gap sizing arithmetic

Closing 2.38× → 1.52× at fixed `f` = 0.568 is worth
`0.4327 × (4.0793 / 1.52 − 1.7128) × 500` = **+210.0 gold/game**, not +300. Both the +300 and, after
§9.1, the +210 are upper bounds on a quantity whose causal component is much smaller.

### 9.3 Ausdroid

Concur with the orchestrator: the 1.37× row rests on 18 unit-rounds and is unusable. It is not cited
anywhere above.

### 9.4 Opponent-visibility share

The brief's "roughly 56% of rounds have both opponent units invisible" cannot be reproduced or
refuted locally: self-play gives 6.4% (both seats camp the same anchors) and a non-camping reference
gives 91.7–97.7%. The platform figure stands as the anchor; local self-play must not be used for it.
Withdrawn from my own earlier draft: the 93.2% enemy-visibility figure.

### 9.5 The fold's description

"61.1% of our ≥8 bursts are one cell bitten twice" is correct but has been generalised into "the
fold is a double-eat". Measured over all fold unit-rounds the standing residual is zero in 92.7% /
95.6% of them: the fold is dominated by idle anchor oscillation. Any future proposal about the fold
should start from §7.1.

### 9.6 Open-loop figures that must not be quoted

An earlier variant of the strip measurement evaluated our counterfactual path on the *unstripped*
board and returned 898.9 gold/game against the closed-loop-consistent 595.3 — a 51% inflation from
exactly the open-loop double-counting the project forbids. Only 595.3 appears above.

### 9.7 My own interim numbers are superseded by the out-of-sample pooling

The interim figures I reported mid-flight — oracle **+57.4** pooled and **+0.34** at the field's
first-mover rate — were **in-sample only** (seeds 1000–1007, n=16 games). They are quoted in
`sim/reports/target_selection_closed.md` and in commit `b0e9786`. After the disjoint-seed
confirmation (seeds 2000–2007) the correct pooled figures are **+42.3 ± 18.5** and **+0.25**, because
the second-mover arm shrank from +114.8 ± 52.6 (2.18σ) to +54.5 ± 44.5 (1.22σ). The direction and
every ruling are unchanged; only the magnitudes move, and they move **down**. Anyone re-quoting the
bound should use +42.3 / +0.25.


---

## 10. Reproduction

```bash
# substrate
git show f18064c:src/player.cpp > /tmp/gr_order/player_f18064c.cpp
shasum -a 256 /tmp/gr_order/player_f18064c.cpp
# 0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd
clang++ -O2 -std=c++17 -shared -fPIC -Isrc -include /tmp/gr_path/shim.h \
  -o /tmp/gr_order/base.dylib /tmp/gr_order/player_f18064c.cpp

B=/tmp/gr_order/base.dylib
O=/tmp/gr_order

# Step 1a -- the contract, the order-field trap, the lagged channels
python3 sim/analyze_order_sensitivity.py contract --out $O/contract.json

# fidelity gates: prophecy exactness, sha equality, first-mover null control
python3 sim/analyze_order_sensitivity.py verify   --base $B --map map1 --seeds 1000 \
  --out $O/verify_smoke.json

# Step 0 -- benefit landing, MECE miss reasons, supply curves, consumption split
python3 sim/analyze_order_sensitivity.py step0    --base $B --map map1 --seeds 1000:1008 \
  --out $O/step0_map1.json

# Step 0 -- ours vs a non-camping reference, income/hit vs supply, both seats, fog-free
python3 sim/analyze_order_sensitivity.py scarcity --base $B --map map1 --seeds 1000:1006 \
  --out $O/scarcity_map1.json

# Step 1b + Step 2 -- visibility, contestability, accessible share (use --opponent monotone)
python3 sim/analyze_order_sensitivity.py visibility --base $B --map map1 --seeds 1000:1006 \
  --opponent monotone --out $O/visibility_monotone_map1.json
python3 sim/analyze_order_sensitivity.py visibility --base $B --map map1 --seeds 1000:1006 \
  --opponent self     --out $O/visibility_map1.json   # artifact, see Sec 3.1

# the fold: decision surface, then the arms (tuning then disjoint confirmation)
python3 sim/analyze_order_sensitivity.py foldprobe --base $B --map map1 --seeds 1000:1004 \
  --out $O/foldprobe_map1.json
python3 sim/analyze_order_sensitivity.py fold --base $B --map map1 --seeds 1000:1008 \
  --arms fold_never,fold_tour,fold_tour_cond,fold_seek,fold_seek_cond \
  --out $O/fold_tune2_map1.json
python3 sim/analyze_order_sensitivity.py fold --base $B --map map1 --seeds 2000:2020 \
  --arms fold_never,fold_seek,fold_tour --out $O/fold_oos2_map1.json

# Step 3 + Step 4 -- the bound and the cheap approximation, both order arms
python3 sim/analyze_order_sensitivity.py bound --base $B --map map1 --seeds 1000:1008 \
  --arms prophet,prophet_free,cheap_r000,cheap_r050,cheap_lagged_r000 \
  --out $O/bound_map1.json
python3 sim/analyze_order_sensitivity.py bound --base $B --map map1 --seeds 2000:2008 \
  --arms prophet,cheap_lagged_r000 --out $O/bound_oos_map1.json

# the platform-side anchors and the RD correction
python3 sim/analyze_map1_lesion.py contention --map map1 --out $O/contention.json

# companion JSON
python3 sim/analyze_order_sensitivity.py assemble --artifacts $O \
  --out sim/reports/order_sensitivity.json
```

Runtime on an 18-core arm64 host: ~5 s per baseline game, ~7 s per prophecy game; the full set above
is ~50 minutes wall clock with the four long modes run concurrently.

---

## 11. What may and may not be claimed from this report

**May be claimed.**

* No same-round mechanism conditioned on dispatch order is expressible; any such mechanism is
  lagged-adaptive. This follows from the contract, not from a measurement.
* Within-round theft is structurally zero in the first-mover condition, so the benefit share of
  within-round survival-aware targeting under our-first is exactly 0%.
* A perfect, free oracle over move order is worth +42.3 ± 18.5 gold/game pooled and +0.25 at the
  field's first-mover rate, closed-loop, same-seed paired, out-of-sample confirmed.
* No cheap approximation of it survives; the best is −88.2 ± 23.0.
* The conditional fold beats neither of its two baselines, and its zero-instruction form is
  significantly negative because it drifts the unit off the central peak.
* The simulator reproduces the platform's order-sensitivity magnitude and asymmetry, so every prior
  local A/B in this repository is a first-mover measurement.
* At most 26.5% / 35.9% of our misses are addressable by any change to the target selector.

**May not be claimed.**

* No absolute local income figure is platform-comparable; only same-seed paired deltas are.
* No per-map claim: the simulator's per-map ordering is inverted (Spearman −1).
* The NPC/opponent split of the strip is biased toward the NPC side locally (~39% per-action NPC
  accuracy); the platform's 65.6% / 18.7% consumption split is the anchor for magnitude.
* `MonotoneHarvester` is a fitted reference, not a replica of T-1, Tundra or anyone else. It is used
  only for the asymmetry direction and for the supply-conditioned contrast.
* Nothing here says the *level* deficit is closed or explained. It says move order is not the lever;
  the matched-order deficit of −411 gold/game remains open.

**Platform requests, stated rather than run.** None required for this verdict — every gate resolved
locally, and the one number I could not resolve locally (the true opponent-visibility share) does not
change any ruling, since gate 2 does not fire at either bracket. If the order-sensitivity anchor is
ever re-used to price work, the request that *would* be worth games is a cost-matched confirmation of
§9.1 — but it needs no new games either, because the archived 30-game corpus already contains the
near-tie stratum and `analyze_map1_lesion.py contention` reads it.
