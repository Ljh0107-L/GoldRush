# Comparison discipline: the six conditions, and the eight cases that forced them

> Written 2026-08-10 by the orchestrator at the Master's instruction, after the eighth
> mixed-basis incident in two days. This is a methodology file, not a measurement. It belongs in
> `sim/reports/` because it is evidence-derived; the Master is relaying the rule itself into
> `AGENT.md`, which this line does not own.

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
