# Hit rate is not a currency: the slope, and what it costs to buy a rank

> Orchestrator synthesis over the three measurement lines committed in `bf1186e`
> (`miss_taxonomy.md`, `map1_wall_repricing.md`, `archive_backfill.md`). Baseline under test is
> `f18064c` (`git show f18064c:src/player.cpp` = `0ecce6fc…84fdd`) throughout; HEAD has since moved
> to `fd47ea6`, which is bit-identical on the three known maps (`pair_diff` 0/500).
> **Zero platform games consumed by any of the three lines.**
>
> This file contains no new measurement. Everything numeric is either quoted from those reports or
> is arithmetic over their measured deltas, and the two estimates in §4 are labelled as estimates.

## 0. The one-line answer

The commissioning premise was **"the deficit is 100% hit rate: 34.8% vs 41.1%"**. That measurement
is exact — independently re-verified at source, n=35,928 unit-rounds/side — but the inference from
it is wrong. **Hit rate is a coordinate on a frontier, not a currency.** Closing the entire gap
costs zero instructions and *loses* score.

## 1. The slope Master asked for

Four closed-loop, same-seed-paired interventions were measured (`miss_taxonomy.md` §7). Scoring is
**relative** — our net change minus the unchanged opponent's, which is what a win-rate ranking pays
on:

| intervention | Δ hit rate | our Δ net | opponent Δ net | **relative Δ** | **slope** |
|---|---:|---:|---:|---:|---:|
| scan gate `v>2` → `v>0` (**0 instructions**) | **+8.51 ± 0.94 pp** | +26.3 ± 54.7 | +101.7 ± 31.6 | **−75.3** | **−8.8 gold/pp** |
| scan gate `v>2` → `v>1` | +3.43 pp | −21.6 ± 62.7 | +103.9 | −125.0 | −36.4 gold/pp |
| oracle, position-preserving, idle units only | +5.14 pp | +8.8 ± 56.1 | +24.9 | −16.1 | −3.1 gold/pp |
| **oracle, value-aware, idle units only** | +17.08 pp | **+414.6 ± 79.3** | +21.4 | **+393.1** | **+23.0 gold/pp** |

**The slope is not a constant — it is a property of the mechanism, and its sign flips.** Three of
four interventions buy hit rate at a *negative* price. Only the fourth pays, and inspecting *why*
gives the real conversion rule: the three losers move along the frontier (hit ↑, yield ↓, mean
flat: 1.353 → 1.379, +2.0%), while the winner raises the **mean** (1.353 → 1.768, +30.7%).

So the honest answer to "how many gold per pp" is: **quoting a single slope is a category error.**
The quantity that converts is the mean, and there it is exact and mechanism-independent:

> **Δ(mean held-gold per unit-round) × 2 units × 500 rounds = Δ gold/game.**
> **1.0 gold of mean ⇒ 1000 gold/game.**

Our platform mean is **1.4906**, the two strong opponents' is **1.5568** — only **4.4% apart**,
which is the whole six-battlefield pooled gap of **66 gold/game**. A 118% hit-rate ratio sits
against a 90% yield ratio and very nearly cancels.

## 2. Why this is a frontier and not one failed experiment

The frontier is bracketed from **both** directions, by two independent experiments run a day apart
with opposite intent:

| direction | intervention | result |
|---|---|---|
| hit ↑ , yield ↓ | scan gate `v>2`→`v>0` (this round, self-play paired, n=9) | +8.51pp, relative **−75.3** |
| yield ↑ , hit ↓ | global amount priority `23db121` (`CHANGELOG`: probeobs n=40, **all 500/500 first-mover, so latency-free**) | gold per scoring round **+7.63%**, net **+41.1 ± 51.7, z = 0.79 — null** |

Traversing the axis in either direction yields nothing. That is what a frontier means, and it
explains a class of prior verdicts rather than just one: the amount-priority organ was not killed
only by its +27.5 ns platform tax — **even at zero latency cost its income effect was null**,
because it bought the axis we already lead on by spending the axis we trail on.

This also retro-explains the fold (bite one cell twice), which the subsystem audit could only move
by +36/+5/+4 gold: it is a cheap way to hold position, and position is where our income comes from.

## 3. What the taxonomy says has to change

MECE over 10,333 misses, residual exactly 0 (`miss_taxonomy.md` §1):

| class | share of misses | pp if fully eliminated | pp **convertible** | novel gold/game |
|---|---:|---:|---:|---:|
| B supply — positioning | **59.86%** | 41.32 | **0.00** | 0.0 |
| C conversion — decision | 27.61% | 19.06 | **17.96** | 40.9 |
| D blocked — routing | 12.24% | 8.45 | 6.55 | 31.7 |
| A burn-cancelled | 0.29% | 0.20 | 0.16 | 0.2 |

The largest class is **structurally unfixable by any decision change** — B is defined by
`max_pickup == 0` within reach, and a god-view check found only 447 of 6185 had hidden reachable
gold, so it is not fog either. It is a positioning problem, and positioning is already what the
anchor optimises. The convertible pp all sit in C, whose cheapest fix is the zero-instruction gate
in §1 — which is precisely the intervention that loses 75 gold.

## 4. What a rank actually costs (estimate, not measurement)

The preliminary is a round-robin over ≥117 teams ranked by win rate (`docs/PRELIM_RULES.md`). We are
rank 92 at **23.68%**; the cutoff is **68.69%**.

Assuming per-game margins are approximately normal and that a strategy change shifts their mean
without changing their spread — **both assumptions unverified, and the second is the weaker one** —
the required shift is `Δz × σ`, with `Δz = Φ⁻¹(0.6869) − Φ⁻¹(0.2368) = 1.2037`:

| σ (per-game margin sd) | basis | required shift |
|---|---|---:|
| 222 | median within-cell sd, measured over the six f18064c families | **267 gold/game** |
| 333 | largest within-cell sd measured (`alA0`) | 401 gold/game |
| 500 | assumed field-wide, includes between-opponent variance | 602 gold/game |
| 800 | assumed field-wide, wider | 963 gold/game |

**Order of magnitude: a few hundred to ~1000 gold/game.** The within-cell figures are a *lower*
bound, because a field of 117 opponents of wildly differing strength has more margin variance than
one opponent on one map.

Set against that scale:

- IPC/latency golf: **120–200 gold** — an order of magnitude short, and latency does not enter the
  ranking directly. Corroborated independently: against Ausdroid (the rank-16 team) we are 1W-14L
  *with* a 2–800× latency advantage and reliable first mover.
- Closing the whole hit-rate gap: **−75 gold**. Wrong sign.
- The entire map1 wall pool, honestly priced: **34 gold novel**, ceiling +90.6 ± 58.9 (undecidable).
- Value-aware idle-only re-choice: **+393 gold** relative, 8/9 games — the **only** measured
  candidate of the right order. Heavily caveated (§5).

## 5. The one surviving lead, with its caveats stated first

`miss_taxonomy.md` §7: value-aware re-choice **restricted to idle units** (never redirect a unit
that is already scoring) → **+414.6 ± 79.3 gold/game for us, +21.4 for the opponent, relative
+393.1, 8/9 games**, raising the mean 1.353 → 1.768.

It does not contradict the path-harvest verdict's −832 gold/game: that intervention re-chose on
**every** round including scoring ones. **The whole difference is one guard.**

Caveats that must travel with the number, all upward-biasing: it is a **perfect-information oracle**,
measured in **self-play**, in a simulator whose **NPC model is over-greedy and over-central**, and
whose hit rate sits **3.81pp below** platform. The oracle costs thousands of instructions against a
budget of ~246 at 1.6 gold/instruction, so **a cheap approximation is entirely unproven** — that,
not the oracle, is the open question. Treat +393 as a ceiling on a direction, not a forecast.

## 6. Unresolved — flagged, not averaged

The wall-vs-bomb split of gate-blocked misses does **not** reconcile across three measurements:
published **82.6 / 17.4** (`CHANGELOG`), map1 line **67.9 / 32.1**, taxonomy line **58.0 / 42.0**.
The two new lines used different class boundaries and differ from each other by ~10pp. All three
agree only that walls are the majority. Nothing in the wall verdict turns on it — the verdict rests
on novel gold per block (0.193) and on the closed loop — but the number should not be quoted until
one definition wins.

Secondary, and actionable by whoever owns `player.cpp`: the richness gate's comment claims a poor
unit burns `10% × 0 = 0`, but the engine charges `(held+9)//10`, so a unit holding 10–99 gold burns
1–9 per bomb. Total burn is 219.4 gold/game with **99.4% falling on miss unit-rounds**. Comment and
engine disagree; the threshold of 100 was chosen from the comment's premise.

## 7. Verdict

**此路不通 on the axis we were sent to attack.** The hit-rate gap is real, exactly measured, and
worthless as a target: it is a coordinate, the frontier through it is flat-to-negative in both
directions, and the cheapest full fix costs zero instructions and loses 75 gold. The map1 wall pool
is a mirage by a factor of 27. Neither of the two paths commissioned this round survives.

What replaces them is a different objective function: **raise the mean, not the coordinate** — and
the only measured candidate of the right order of magnitude is idle-only value-aware re-choice,
whose cheap approximation is the next thing worth an experiment.

## 8. Reproduce

```sh
python3 -m sim.analyze_miss_taxonomy report      # taxonomy, frontier A/B, slope inputs
python3 -m sim.analyze_blocked_cost  report      # map1 wall repricing
python3 sim/analyze_gold_delta.py    frozen      # fog-free channel, hit/yield/mean
```

§1 and §4 arithmetic is over the deltas tabulated in those reports; §4's two normal-approximation
assumptions are stated in place and are not measured.
