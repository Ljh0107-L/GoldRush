# Comparison discipline: the six conditions, and the eight cases that forced them

> Written 2026-08-10 by the orchestrator at the Master's instruction, after the eighth
> mixed-basis incident in two days. This is a methodology file, not a measurement. It belongs in
> `sim/reports/` because it is evidence-derived; the Master is relaying the rule itself into
> `AGENT.md`, which this line does not own.

## The unified diagnosis

Every conclusion this project retracted in two days failed the same way, and it was **never**
arithmetic:

> **The number answered a different question from the one it was being used for.**

| number | what it was read as | what it actually answered |
|---|---|---|
| `A = +1.244 > 0` | our collection beats theirs round-for-round | the value of the **first-mover slot** (our first-mover income vs their *second*-mover income) |
| `36.0%` hit rate at the 28th percentile | our current build's standing in the field | a **different corpus** — our build played 0 of those 133 games |
| `2.38×` order-sensitivity | we are abnormally fragile when moving second | **local situation difficulty** — bad situation makes us slow *and* poor (RD collapses it to 1.13×) |
| oracle `+57.4` / `+0.34` | a priceable headroom | an **in-sample screening** value; out-of-sample it shrank 52% |
| `ORT_A` at `(6,6)/(10,10)` | evidence for a diagonal camping split | the **opening baked-route endpoint**, on a route that is rarely completed, in rounds where we are already ahead |

Five cases, five times "right quantity, wrong question". Classification rules cannot catch this,
because each number is individually correct. Only one action catches it, and it is mechanical:

## Delivery gate: write the condition sentence

> **Any report containing a decomposition, an accounting identity, or a cross-group comparison must,
> before delivery, carry one sentence per term naming the condition that term was measured under and
> what it is compared against. A report without those sentences is returned unread.**

This is a gate, not advice, and it is deliberately mechanical rather than a matter of judgement.
Applied to the case that cost the most, the sentence for `A` reads: *"our income on rounds where we
move first, minus their income on those same rounds, where they are therefore moving second."*
**Written out, the mismatch is visible in the sentence itself.** It was never written out, and the
error survived a review that verified closure, `f`, `A`, `B`, and seven holdout splits — all correct,
because the arithmetic was never the problem.

## Hypothesis generators are not evidence

> **A coincidence may generate a hypothesis. It may never be converted into a prior.**

Worked case: `(6,6)/(10,10)` is simultaneously the coverage-optimal static two-window placement
*and* a stale constant in the opening baked route whose "correction" measured negative. The
coincidence was **useful** — it is why that pair was examined at all. Its value stops there. The
reason to test the pair is the coverage arithmetic, which is independent of the coincidence; the
negative "fix" measures the opening route, which is a different quantity.

Conflating the two is the entry point for using observation to *support* a hypothesis rather than to
*test* it. Related and worth stating in the same breath: **"not identified" is different from
"identified weakly."** When the corpus contains **zero** variation in the variable of interest — as
it does for anchor column separation, since every game of the frozen build placed both units on
column 8 — no additional sample can help, and only an intervention can answer. Weak identification
tempts you to add data; non-identification must send you to an experiment.

## The rule

> **Any comparison that enters a decision must first declare whether all six of these hold:
> same build, same opponent, same map, same action order, same time window, same corpus. If any one
> fails, the comparison is mixed-basis and may not be used to price or to target.**

The six are not equally likely to be violated. Ranked by observed frequency of failure:
**build** (four cases), **action order** (two), **corpus** (one), **window** (one), **opponent**
(one), **map** (zero so far, because map fingerprinting from log row 2 is cheap and was adopted
early).

### Why "same corpus" is a separate condition and not a special case of the others

Case 8 below is the reason, and it is worth stating on its own because **no other check catches it**.
`sim/analyze_field_profile.py` held a reference literal `REFERENCE["OURS frozen (map1)"] = 0.360`.
That number is **correct** — it is `mean(0.36222, 0.35822)`, `f18064c`'s own map1 hit rate over 12
games against Tundra and T-1 — and it was **correctly labelled**. The defect is that it was placed
inside a distribution built from 133 passive games, and **`f18064c` played 0 of those 133 games**.

Note what fails to catch this. A unit test does not: the arithmetic is right. **Re-verifying the
number at source does not either** — re-sourcing confirms `0.360` is exactly what it claims to be.
The only check that catches it is:

> **A distribution and the point being placed inside it must be built from the same corpus.**

Measured cost of this one: our own obsolete public slot scores **49.22%** on the same statistic in
that corpus, which would place a three-generations-old build at the **90th percentile**. The artefact
is **≥14.6pp**, roughly twice the 7.7pp gap that was being argued about.

### Condition 4 is not enough on its own: action order is **endogenous**

Conditioning on action order removes the *arithmetic* mismatch that produced case 7, but it does
**not** make the comparison causal, because order is not assigned to us — **we cause it**. The engine
awards first move to whoever decided faster; our decision is slow on the `ok == 0` fallback branch
(+40 ns/unit, fired in 53.5% of map1 rounds); and that branch fires precisely when the LUT path is
**blocked**, which is a bad local situation. So a bad situation makes us slow **and** poor
simultaneously. Comparing "our second-mover rounds" against "their second-mover rounds" therefore
compares our *worst* situations against a roughly random sample of theirs.

Measured size of this confound, on the 30-game map1 corpus. Restricting to a near-tie window where
`|our_cost − their_cost| <= 10 ns`, so that which side moves first is quasi-random and our own branch
mix is matched across arms:

| stratum | our first/second ratio | theirs | ratio-of-ratios | absolute order gap, ours vs theirs |
|---|---:|---:|---:|---|
| observational | **2.380×** | 1.647× | **1.445×** | +2.366 vs +1.845 |
| **RD, ≤10 ns** | **1.759×** | 1.562× | **1.127×** | **+1.622 vs +1.660** |
| RD, ≤20 ns | 1.900× | 1.531× | 1.241× | +1.799 vs +1.579 |

The apparent 44.5% excess order-sensitivity collapses to 12.7%, and **in absolute gold per round we
lose marginally *less* from moving second than the opponents do.** The same collapse appears
per-account (player163 1.65× → 1.13×, player57 1.39× → 1.07×). So "we are abnormally fragile when
moving second" is **largely an artefact of reverse causation**; what survives is the *level* deficit,
which is visible in the same table and is what the map1 erratum already identified.

The general rule this yields:

> **When the conditioning variable is itself produced by the thing under study, stratifying on it is
> not identification. Find a stratum where it is quasi-random — here, a near-tie window — and report
> both, because the gap between them measures the confound.**

This is the third distinct correction of the same family in one session (case 7 mismatched the
condition, case 8 mismatched the corpus, this one mismatched *causality within a matched condition*),
which is why it is recorded as a rule rather than as a footnote.

## The eight cases

| # | claim as used | what it actually measured | cost of the error |
|---|---|---|---|
| 1 | "our burst rate 15.2% vs their 32.5/34.4% ⇒ they chain more" | a ~102-build archive mixture against two single builds | a whole night's hypothesis built on it; on the frozen build our ≥8 rate **exceeds** theirs |
| 2 | "collection ceiling 1765" | build `ff46275`, quoted as current | the prize pool was overstated 3.6×; the frozen build's ceiling is 2182.4 |
| 3 | "T-1 burst 5.67%" | the **pooled** two-opponent figure, labelled as T-1 | T-1 is 6.212%, Tundra 5.132%; conclusion survived, label did not |
| 4 | "1W-14L vs Ausdroid ⇒ mid-field already beats us" | 14 old builds incl. two crippled probes at 776/870 gold | `f18064c` is +85.8 (4W/2L); the swing is **+559.6 gold/game** |
| 5 | "Tundra map1 −35.4 ± 45.5" used as a control | a different window/version; **provably unreachable** in this corpus (best 20 of 90 games sum to −2234, needs −708) | a live verdict lost one of its two legs (same-window z = 0.20, not 3.74σ) |
| 6 | "map1 is the double-kill battlefield, −274 / −219" | n=6 each; Tundra is really n=24 at −289.04 (5.29σ), T-1 is 1.83σ **undecidable** | understated one, overstated the other's certainty |
| 7 | **"`A` > 0 ⇒ we out-collect them round-for-round"** | our **first**-mover income minus their **second**-mover income | inverted the map1 diagnosis; would have funded the wrong organ |
| 8 | "our hit 36.0% = 28th percentile of the field" | a **correct, correctly-labelled** figure for `f18064c` over 12 games vs the two strongest teams, placed inside a distribution built from 133 passive games that `f18064c` played **0** of; our actual slot there was a ~3600ns build, **11× slower**; field taken at f≈0.40 and ours at f≈0.57 | the same metric rates our obsolete slot **90th percentile** — the reductio. Artefact ≥14.6pp, ~2× the 7.7pp under debate |

Five of the eight were committed by the Master, three by this line. The shape is identical every
time: **an aggregate was substituted for a like-for-like pair.**

## The operational check that would have caught case 7

"Remember to verify semantics" is not executable. This is:

> **For every term in an accounting decomposition, write one sentence naming the condition it was
> measured under and the thing it is compared against — before delivery.**

Applied to the map1 identity, the sentence for `A` is: *"our income on rounds where we move first,
minus their income on those same rounds, where they are therefore moving second."* Written out, the
mismatch is visible in the sentence itself. It was not written out, and the error survived a review
that checked closure, `f`, `A`, `B`, and seven holdout splits — all of which were correct, because
the arithmetic was never the problem.

A corollary worth stating separately, since it is what makes accounting identities dangerous:
**an identity with zero residual is self-checking arithmetic, not evidence of mechanism.** Residual
0.00 proves only that the conditioning partitioned the total. Causal weight has to come from
somewhere else — a matched comparison, a marginal (discontinuity) estimate, or an intervention.

## A structural blind spot this exposed, worth its own note

Every local A/B in this project has run with **us as the first mover** (`sim/README.md` recommends
`--fixed-costs 200,201`, and `path_harvest_oracle.md` §1.2 chose it deliberately so that no NPC or
enemy has moved when seat 1 acts, which makes counterfactuals exact). That is the condition in which
we are *least* broken: at matched order we are −14.0pp on hit when both move first and −18.2pp when
both move second. **Our second-mover behaviour has therefore never been measured locally at all.**
Any harness intended to detect order-sensitivity must exercise both conditions and report them
separately.
