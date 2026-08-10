# Reverse-engineering `rikka`, and the verdict on the step-budget axis

Scope: read-only reverse engineering of team `rikka` from archived platform logs, plus a
feasibility judgement on porting anything it does to a nanosecond-latency implementation.
**Zero platform games were played.** 19 archived logs were downloaded (log retrieval does not
consume quota); the other 14 were already on disk.

Apparatus: `sim/analyze_rikka.py` (subcommands `roster`, `income`, `budget`, `styles`, `reach`,
`report`, `selftest`), machine-readable output in `sim/reports/rikka_strategy.json`.
Corpus-wide `k` survey and the instruction-cost estimate were produced by two delegated lines;
their artefacts are `/tmp/rikka/kcorpus.{json,md}` and `/tmp/rikka/kcost.md`.

---

## 0. Verdict in one page

**The premise that motivated this investigation is false, and the axis it opened is judged
negative. The net product of this line is that two candidate directions are closed.**

1. **`rikka` fields two different constructs, and the one on the ladder is architecturally
   identical to ours.** Its public defence slot `player47` runs at P50 350–400 ns with a *fixed*
   3+3 step split — not a high-latency strategy-only winner. The 1.07 µs on the leaderboard is a
   cumulative P90 inflated by its own bespoke builds.
2. **Our current build is not shown to be behind `rikka`'s ladder model, but parity is not
   established either.** The comparison cannot be made cleanly: we have never played their public
   slot with a current build. Unconditionally over n=20 map1 games our construct family collects
   `1.559 ± 0.061` per unit-round against their `1.776 ± 0.022` (`-0.217`, `3.36σ`) — but that is a
   **mixture artifact**, because the two corpora have different action-order mixes. Conditioning on
   order in the identified (near-tie) stratum leaves both arms non-significant (`0.65σ` first,
   `0.74σ` second). See §4.2; this correction supersedes an earlier single-game reading of `1.798`.
3. **The only `rikka` construct that beats us runs 8–11 ms per round** — a bespoke anti-us search,
   `-465 ± 145` gold/game (`3.2σ`) against our brand-new fast defence while we moved first in
   1500/1500 rounds. **Not portable to nanosecond latency**, for reasons given in §4.
4. **The step-budget split `k` is a real, legal, previously-untested axis — and it is judged
   negative.** The engine does split a fixed six-step budget by `k` (our `CHANGELOG`'s claim that it
   hardcodes 3+3 is false; see §3.1). But the two strongest teams in the field provably do *not*
   use it, the field-wide association with rank is negative, the prize ceiling is +83…106 gold/game,
   and unlocking anything beyond `k=4` costs 314 gold/game because of the 5×5 scanner (§5).
5. **Copying `rikka`'s playstyle would cost us about 85 gold/game** (§4.3). Its selectivity declines
   exactly the unit-rounds in which we currently earn a small but positive amount.
6. **One measurement caveat escapes this report and may affect the `-411` water-level standard**:
   opponent income is strongly crowded by ours against `Tundra` (`-0.781 ± 0.067`) but not at all
   against `T-1` (`-0.034 ± 0.101`). See §6.6 — flagged for the standard's owner, not acted on here.

---

## 1. Identity, latency tier, and the first-mover rate

`rikka` = `user_id 47`, team name `rikka`, 清华大学. Public defence slot `model_id 51256`, name
`player47`, `lang 2` (C++), `created_at 2026-07-25T03:02:07Z`, `updated_at 2026-08-06T15:37:19Z`
(`get_model_list_4`). Ladder row at time of measurement: `win_rate 0.6744` (24 h rolling window),
`user_cost1 1070` ns (cumulative P90 since launch). An earlier observation of ~82.09 % is consistent
with the same team under a different 24 h window; the two are not comparable.

### 1.1 Two constructs, never pooled

| construct | how submitted | games | P50 cost (steady state) | P90 | step split `k` |
|---|---|---|---:|---:|---|
| `player47` | public defence slot | 18 | **350–400 ns** | 550–620 ns | **fixed 3** |
| `g47v220m1/m2/m3` | bespoke challenge builds | 15 | **263 µs → 11.0 ms** | up to 13.1 ms | **variable 0…6** |

The bespoke names decode as "g(ame) 47 v(ersus) 220 m(ap) N" — they are written specifically against
our account. They are submitted through the challenge path, so they never occupy `rikka`'s public
slot and would not appear in a round-robin over public slots.

All latency figures read `end.players[i].cost` only, with rounds 0–3 dropped as warm-up.
`start[r].cost` is a stale copy of `end[r-1].cost` and is never used.

### 1.2 Our first-mover rate `f` is a function of *our* build, and it is endogenous

Against `player47`'s stable ~360 ns, `f` is determined entirely by which of our builds was in the
game. `f` is *not* an instrument: our faster builds are also our newer and better ones, so `f`
correlates with construct quality and cannot be used to attribute anything.

| our build | our P50 | `f` | net (gold) | result |
|---|---:|---:|---:|---|
| `cpp20/21/22` | 3210–3910 ns | 0.000 | −464 / +44 / −513 | 1W 2L |
| `ns322`, `ns326*` | 1070–1090 ns | 0.002–0.006 | −715 … −845 | 0W 5L |
| `v39`, `v40` | 550 ns | 0.163–0.173 | −1331, −1552 | 0W 2L |
| `v42` | 760 ns | 0.048 | −107 | 0W 1L |
| `v5b` | 410–420 ns | 0.341–0.405 | −190, −213 | 0W 2L |
| `v5h`, `v5f` | 260–290 ns | 0.681–0.756 | +185, +50 | 2W 0L |
| `vsrikka`, `mR1` | 230–240 ns | 0.776–0.792 | +275, −225 | 1W 1L |

At our current 200 ns, extrapolated `f` against `player47` is ≈0.80–0.85, **not 1.0**: their P90 of
~600 ns overlaps our P90 of ~400 ns. So `rikka`'s ladder model is *not* a constant second mover.

Our modern-fast subset (`v5h`, `v5f`, `vsrikka`, `mR1`) is 3W 1L at mean net `+71` over n=4 — **not
adjudicable**, and four different constructs, so it cannot be attributed to any of them.

### 1.3 Record, stratified at the publish boundary

Our public defence slot carried the 8/7 build (P50 ≈3600 ns) until `2026-08-10T08:20:18Z`, when
`fd47ea6` (P50 ≈200 ns) went up. Passive games either side of that instant involve **different
constructs of ours** and are never pooled.

| stratum | games | record | mean net ± SE | our P50 | their P50 | `f` |
|---|---:|---|---:|---:|---:|---:|
| `player47` vs our 18 assorted actives | 18 | 4W 14L | — (mixture, not attributable) | 230–3910 ns | ~360 ns | 0.000–0.792 |
| `g47v220m*` **variable k** vs our 8/7 slow defence | 9 | 1W 8L | −85 (see note) | 3600–4960 ns | 263 µs–10.9 ms | 1.000 |
| `g47v220m*` **fixed k=3** vs our 8/7 slow defence | 3 | 0W 3L | −706 | 3280–3640 ns | 360–430 ns | 0.000 |
| `g47v220m*` **variable k** vs our `fd47ea6` | 3 | **0W 3L** | **−392.7** | **200 ns** | 7.5–11.0 ms | **1.000** |

Overall 6W 27L across 33 games.

**The bottom row is the hardest single datum on this line.** Games `185615` (map1, −541),
`185688` (map2, −315), `185763` (map3, −322): our newest, fastest build moved first in every one of
1500 scored rounds and still lost by 300–540 gold on all three maps. Recommend adopting these three
games as a fixed benchmark anchor — any future candidate can be replayed against the same three
bespoke opponents to see whether the −393 narrows. (One game per map; the ±SE across three maps is
±74 but that is a between-map spread, not a repeated measure.)

---

## 2. Income structure through the unbiased channel

Only per-unit `end` `gold` is differenced. That field is present in 100 % of unit-observations
whether or not the unit is visible. `pickup` and `actions` are **not** safe for opponents — see §3.2.

`mean` is the only directly convertible quantity: 1.0 gold per unit-round is 1000 gold per game.
**`mean ≠ hit × yield_per_hit`**; the residual is burn.

### 2.1 The step budget is six per round for everyone, so gold-per-step is directly comparable

| construct | games | gold/unit-round ± SE | gold/player-round | gold/allocated step | gross/game |
|---|---:|---:|---:|---:|---:|
| ours `fd47ea6` (f=1.000, we first) | 3 | 1.689 ± 0.067 | 3.378 | 0.563 | 1689 |
| `rikka` variable-k, **same 3 games**, they second | 3 | **2.154 ± 0.129** | 4.307 | **0.718** | 2154 |
| `rikka` public `player47` (18 g, map1) | 18 | 1.776 ± 0.022 | 3.552 | 0.592 | 1776 |
| `rikka` bespoke **fixed k=3** (3 g, fast, they first) | 3 | 1.887 ± 0.053 | 3.775 | 0.629 | 1887 |
| `rikka` bespoke variable-k (all 12) | 12 | 2.129 ± 0.069 | 4.258 | 0.710 | 2129 |

The cleanest row-pair is the first two: same games, same maps, same window, our order advantage.
`-465 ± 145` gold/game, `3.2σ`. Because the step budget is identical, that deficit is by identity a
difference in gold *per step* — but note that "gold per step" is itself improvable by reallocating
steps, so this identity does not by itself exclude the `k` axis. §5 excludes it on other grounds.

A directional note that survives its confound: `rikka`'s own fixed-k build earned 1.887 while
**moving first**, and its variable-k builds earned 2.129 while **moving second**. The order
confound therefore works *against* variable-k, so `+242` gold/game is a lower bound on the combined
(k + 8–11 ms search) effect. It does **not** separate the two, and no data in the corpus can.

### 2.2 Factor decomposition

| construct | mean | hit | yield/hit | burn floor | ge8 |
|---|---:|---:|---:|---:|---:|
| ours `fd47ea6` (3 g) | 1.689 | 36.7 % | 4.90 | 0.113 | — |
| `rikka` public k=3 (18 g) | 1.776 | 48.6 % | 3.71 | 0.028 | — |
| `rikka` bespoke k=3 (3 g) | 1.887 | 49.2 % | 3.88 | 0.018 | — |
| `rikka` variable-k (12 g) | 2.129 | 36.3 % | 6.64 | 0.288 | — |

The two `k=3` `rikka` builds share one profile (hit ≈49 %, yield ≈3.8, near-zero burn); the
variable-k builds show the mechanical signature of concentrating the budget — **hit falls to 36 %
because parked units are guaranteed zeros, while yield rises to 6.6 because the moving unit sweeps
further**. That signature is consistent with `k` doing real work, but it is inseparable from the
search depth that accompanies it.

### 2.3 Vision

`rikka`'s public slot spent **0** on vision in all 18 games. Only its bespoke heavy builds buy:
425/477/450 on 08-08, then 128/69/22 on 08-10. `T-1` and `Tundra` also spend 0.

**Conclusion: no nanosecond-feasible construct in this corpus buys vision. Vision purchase
co-occurs only with the non-portable heavy search. Our "never buy" policy remains correctly priced;
no re-pricing is warranted on this evidence.**

---

## 3. Method: what the log will and will not support

### 3.1 The step-budget mechanic, confirmed from three independent sources

`k` is a player output in `0…6`. The engine assigns `actions[:k]` to unit 0 and `actions[k:]` to
unit 1 (`sim/engine.py:1088-1092`); `order` only controls which unit executes first, not the slice
mapping. The total is always exactly six (`ACTIONS_PER_PLAYER = 6`, `sim/engine.py:45`).

Because `sim/engine.py` is our own reimplementation, it cannot settle what the *platform* does.
Two independent confirmations:

* **Platform logs.** In rounds where both of a player's units have a non-null `end` position, both
  action lists are complete, so their lengths must sum to six. Measured: **18 131 / 18 131 rounds,
  zero violations**, across 33 games and three distinct constructs (ours, `rikka` public, `rikka`
  bespoke). Corpus-wide the delegated survey extends this to **391 193 / 391 193, zero violations**.
* **Official documentation.** `docs/官方示例代码说明.md:19` states `k ∈ [0,6]`, and line 4 says the
  shipped official sample itself uses "角色0走 4 步、角色1走 2 步(k=4)" — **a 4+2 split**.

> **Erratum.** `src/CHANGELOG.md` records that "引擎硬编码 u0/u1 各消费 3 步". That half is **false**.
> The true half is "`SLut` 仅生成 3 步", which is a limitation of *our own* implementation. The
> experiment on this axis was recorded as *cancelled on cost*, not judged, so its income side was
> never measured. This report supplies the missing judgement (§5). Filing the erratum belongs to the
> line that owns `src/`.

### 3.2 The opponent `actions` and `pickup` fields are fog-truncated

An opponent unit's `actions` array holds only the prefix of the round taken while the unit stayed
inside our vision, and `pickup` is truncated to match. Decisive instance: `logs/game_163075.log`
round 69, unit 0 shows `position: null, actions: [1], pickup: 0` while its `gold` rose by **+7**.
Any statistic drawn from opponent `pickup` is therefore not merely low-coverage but **silently
wrong**. It is used nowhere in this report.

The corresponding positive result: a unit-observation whose `end` position is **non-null** has a
*complete* action list. Verified by replaying the action deltas from the previous round's end
position — **40 249 / 40 249 exact** in this report's games, and **841 662 / 841 662** corpus-wide.

### 3.3 Adjudicator and its dry runs

Two readings of `k`, one exact and one one-sided:

* **exact** — from both-units-visible rounds, where the two lengths pin `k` and must sum to six.
* **one-sided** — `len(actions) >= 4` proves an allocation of at least four steps *even when the
  position is null*, because truncation only ever removes a suffix.

`sim/analyze_rikka.py selftest` runs four checks, all passing:

| check | kind | result |
|---|---|---|
| our own `k`, known `= 3` from `src/player.cpp:566` | **zero-signal** | 33/33 games clean; 0 / 33 000 at `len≥4`; exact-k `{3: 16500}` |
| `rikka` public slot, independent expected-negative | zero-signal | 0 / 8 292 at `len≥4`; 0 sum-violations |
| `rikka` bespoke builds, known-positive | positive control | 1 878 / 5 430 = 34.6 % at `len≥4` |
| action-delta replay on visible pairs | negative control for the walk-back | 40 249 / 40 249 exact |

The zero-signal run is the one that matters: an adjudicator that has never been shown an input with
no signal is a suggestion, not a test. It reports "no" on both sides where the answer is known to be
"no", and reports signal only where a construct demonstrably differs.

### 3.4 Selection-bias audit, and one retraction

Anything cell- or step-related can only be read from visible unit-observations, which is a
*selection*: a visible opponent unit is by construction close to one of our units. The audit
compares the visible subset's mean income against the complete gold channel over the same games.

| construct | n (complete) | mean, complete | mean, visible subset | ratio | licensed? |
|---|---:|---:|---:|---:|---|
| ours `fd47ea6` | 2 994 | 1.689 | 1.689 | **1.00** | yes (100 % visible) |
| `rikka` public `player47` | 17 964 | 1.776 | 1.781 | **1.00** | yes |
| `rikka` bespoke k=3 | 2 994 | 1.887 | 2.396 | 1.27 | with caution |
| `rikka` bespoke variable-k | 11 976 | 2.129 | 0.790 | **0.37** | **no** |
| `T-1` `player163` | — | 2.037 | 1.424 | 0.70 | with caution |
| `Tundra` `player57` | — | 1.977 | 1.603 | 0.81 | with caution |

> **Retraction, recorded before use.** An earlier draft of this work derived a marginal price of
> `0.38` gold per allocated step by stratifying the variable-k builds by step count. That figure is
> **withdrawn**: at ratio 0.37 the subset understates income by a factor of 2.7, because "a six-step
> unit that nonetheless ended inside our vision" is precisely the atypical round. The only
> defensible prices are the whole-channel figures in §2.1, `0.563–0.718` gold per allocated step.

---

## 4. Playstyle: what `rikka` actually does, and what it would cost us to copy

### 4.1 Style differs across the field, and more than one style wins

map1 only, action-delta walk reconstruction, identical definition for every side:

| side | ladder win | distinct cells / unit-round | effective steps / unit-round | wasted steps | gross / effective step | audit ratio |
|---|---:|---:|---:|---:|---:|---:|
| `T-1` `player163` | 95.63 % | **3.035** | 2.03 | 32.2 % | 0.882 | 0.70 |
| `Tundra` `player57` | 78.21 % | **3.616** | 2.62 | 12.8 % | 0.672 | 0.81 |
| `rikka` `player47` | 67.44 % | **2.051** | 1.28 | 57.5 % | 1.428 | **1.00** |
| ours `fd47ea6` map1 | — | 2.328 | 2.08 | ~30 % | 0.873 | **1.00** |

This independently reproduces the ≈3.0 distinct-cell figure previously measured for `T-1` by a
different apparatus, so that number is not an artefact of either method. But `rikka`'s 2.05 is also
real, and it is the *more* trustworthy of the two (audit ratio 1.00 against 0.70).

**Therefore "the opponent always steps on 3.0 distinct cells" is `T-1`/`Tundra`'s style, not a
property of winning.** Styles at 2.05 and at 3.62 distinct cells both sit above 67 % ladder win rate.
Any hypothesis built on dispersion being *the* correct playstyle loses its external support here.

### 4.2 The two fully-licensed rows: same output, opposite operating points

Restricting to the two constructs whose audit ratio is 1.00, with one definition
("move round" = at least one effective move):

| side | unit-rounds | move rounds | income \| move | overall |
|---|---:|---:|---:|---:|
| `rikka` `player47` | 6 637 | **56.8 %** | **3.136** | 1.781 |
| ours `fd47ea6`, game `185615` only | 998 | **92.1 %** | 1.952 | 1.798 |

Closure: `0.5680 × 3.1355 = 1.7811`; `0.9208 × 1.9521 = 1.7976`.

`rikka` declines to move in 43.2 % of unit-rounds and earns 3.14 when it does. We move in 92.1 % and
earn 1.95. **Two very different operating points landing on nearly the same product.** That is
external, exogenous evidence that the "move frequency × income per move" contour is flat: we cannot
manufacture it by choosing our own experiments, unlike an internal ablation.

The *contour* result stands. The *level* comparison in that table does not, and is corrected below.

### 4.2b Correction: the level comparison at n=20, and why the naive gap is a mixture artifact

The `1.798` above is one game. Extending to every map1 game of our current construct family —
8 games of `fd47ea6` (post-boundary passive defence) plus 12 of `f18064c` (`frTu1` ×6, `t1f1` ×6);
the mislock fix is behaviour-neutral on known maps, so they are reported separately and pooled:

| our construct | games | mean per unit-round ± SE |
|---|---:|---:|
| `fd47ea6` | 8 | 1.521 ± 0.144 |
| `f18064c` | 12 | 1.585 ± 0.041 |
| **pooled** | **20** | **1.559 ± 0.061** |
| *(reference)* `rikka` `player47` | 18 | 1.776 ± 0.022 |

Unconditionally that is `-0.217 ± 0.065`, **`3.36σ` against us** — the opposite sign to the
single-game reading. But the two corpora differ in action-order mix, so the difference must be
conditioned. Splitting every round by who actually moved first:

| stratum | our income, we move first | our income, they move first | their income, they move first | their income, we move first |
|---|---:|---:|---:|---:|
| all rounds | **1.944 ± 0.034** (n=11 596) | **0.913 ± 0.041** (n=7 268) | **1.814 ± 0.025** (n=13 526) | **1.705 ± 0.053** (n=4 124) |
| near-tie `|Δcost| ≤ 10 ns` | 2.034 ± 0.104 (n=908) | 1.128 ± 0.110 (n=866) | 1.869 ± 0.232 (n=160) | 1.286 ± 0.182 (n=196) |

Like-for-like, each side measured **while moving first**: `+0.130 ± 0.042` (`3.09σ`, in our favour)
on all rounds, `+0.165 ± 0.254` (`0.65σ`) in the near-tie window.
Each side **while moving second**: `-0.792 ± 0.066` (`11.91σ`, against us) on all rounds, but
`-0.158 ± 0.213` (`0.74σ`) in the near-tie window.

**Reading.** The naive order split says we are far better first and catastrophically worse second.
Almost all of that vanishes in the identified stratum, so it is predominantly **reverse causation**,
not order sensitivity: our high-cost rounds are the rounds where the step lookup was blocked and the
fallback fired, and a blocked position is *both* slow *and* poor. This reproduces the already-recorded
collapse of the "we are the field's most fragile second mover" anchor (ratio-of-ratios shrank from
1.445 to 1.127 under the same window); my figures give `1.80×` versus their `1.45×` in the near-tie
window, consistent with that prior correction and adding nothing new.

**Consequence for the headline.** The unconditional `-0.217` is a mix artifact and must not be quoted
as a water-level gap; the identified stratum returns **not adjudicable in both order arms**. Note
also that even the near-tie comparison is confounded by opponent identity — our near-tie rounds come
from games against `T-1`/`Tundra`/`Capoo`/others, theirs from games against our legacy builds, and
§6.6 shows opponent income is crowded by ours against a camper. So:

> **The data do not support a claim that `rikka`'s ladder model out-collects our current construct,
> and do not establish parity either. "Nothing to learn from it" is downgraded from *established* to
> *not contradicted*.** Settling it needs games between a current build and `player47`, of which we
> have zero.

### 4.3 Copying `rikka`'s selectivity is quantifiably negative for us

Our own side, 100 % visible, zero selection, `fd47ea6` on all three maps (2 994 unit-rounds):

| effective moves | share | mean income | hit | ring drift |
|---:|---:|---:|---:|---:|
| 0 | 4.9 % | **0.000** (148 obs, no exception) | 0.0 % | ±0.000 |
| 1 | 23.9 % | 0.029 | 5.3 % | **−0.102** |
| 2 | 23.5 % | 0.159 | 13.8 % | −0.020 |
| 3 | 47.7 % | **3.451** | 67.6 % | +0.037 |

Allocated steps 8 982, effective moves 6 403 ⇒ **2 579 wasted = 28.7 %, about 860 steps per game.**

`rikka`'s style is to convert those low-productivity rounds into deliberate stationarity. For us
that trade is **negative**: the 52.3 % of unit-rounds that move ≤2 steps currently earn `0.085`
each, and declining them earns `0.000`. Adopting the policy would forfeit
`0.523 × 0.085 × 1000 ≈ 85` **gold per game**. It is not a transferable improvement.

> **Negative result, own hypothesis falsified.** I proposed that the low-move fallback pushes a unit
> off the central generation peak, destroying positional income. The ring-drift column refutes it:
> the fallback drifts **toward** the centre (−0.102 at one effective move, −0.020 at two), and only
> the fully productive rounds drift outward (+0.037). The positional-loss story is dead; it should
> not be revisited.

---

## 5. Can any of it be done in nanoseconds? Component verdicts

### 5.1 The 8–11 ms search: **not portable, structurally**

`rikka`'s bespoke builds spend 263 µs to 11.0 ms per round — between 1 300× and 55 000× our 200 ns.
Legal under the 300 ms per-round and 60 s total budget, and it wins. But there is no version of it
at 200 ns: the deficit it produces (`-465`) is the output of search depth, and depth is exactly what
cannot be bought at our latency. **Judged not portable. No further work warranted.**

Note also that it is not on the ladder in the sense that matters: it is a challenge-path build, not
`rikka`'s public slot, so a round-robin over public slots would not face it.

### 5.2 Variable `k`: **judged negative**, four independent reasons

**(a) The prize has a hard ceiling of +83…106 gold/game.** The only steps that are free to
reallocate are those in fully-dead unit-rounds, which earn exactly `0.000` with no exception across
148 observations: **148 steps per game**. At the defensible price range of `0.563–0.718` gold per
allocated step that is `+83…106` gold/game — and that assumes perfect foreknowledge of which rounds
will be dead. The remaining ~710 wasted steps per game are not free; they carry `0.029–0.159`
gold/unit-round of opportunity cost.

**(b) The 5×5 scanner caps the useful split at `k=4`.** `src/player.cpp` scans five rows and masks
five columns (`rowsel[5]`, loops at `:460/:481/:489`), so every gold target satisfies
`|dr|,|dc| ≤ 2`, Manhattan `d ≤ 4`; `dr0/dc0` are then clamped to `[-3,3]` at `:529-530`.
**Steps 5 and 6 cannot be aimed at any gold the player has seen.** They can only oscillate or walk
into unscanned cells. Making them gold-directed requires a 7×7 scan: **+196 instructions/call
(≈314 gold/game at the conservative 1.6 gold/instruction) plus 1 472 B of `.rodata`** — on its own
larger than the entire prize in (a).

**(c) The cheap variants can only oscillate, and oscillation is a suspected negative asset.**
The step-lookup table's early-arrival fold oscillates between already-visited cells
(`src/player.cpp:230-239`). The organ-level measurement of that behaviour is `−36 / −5 / −4` gold
(SE 22 / 17 / 19) across three maps — indistinguishable from zero and signed negative. The cheapest
tier costed (`+9…12` instructions, 14–19 gold) hands over steps that can do nothing else. Cheap and
probably empty.

**(d) The field evidence inverts the argument that opened this axis.** A corpus-wide survey of
40 opponent teams (696 logs; controls: ours 0 / 773 236 at `len≥4`, `player47` 0 / 8 292,
positive control 191/527 reproduced exactly):

| team | ladder win | verdict on `k` | evidence |
|---|---:|---|---|
| `Tiuntled-1` (#1) | **95.63 %** | **fixed 3+3** | 0 / 72 006 obs; 10 171 exact-k rounds all `k=3`; 95 % UB ≤ 0.03 % |
| `Tundra-wawa` | 78.21 % | **fixed 3+3** | 0 / 66 435 obs; 10 649 exact-k rounds; UB ≤ 0.03 % |
| `ZZK` | 84.65 % | mixed (A/B tests it) | `player204` 0/852 fixed; `B007` 217/720 variable |
| `rocket dogs` | 82.37 % | **not adjudicable** | 0/955 but only 57 exact-k rounds |
| `Ausdroid` | 69.65 % | **variable, in its public slot** | 2 283 / 10 374 = 22.0 %; 53.4 % of exact-k rounds ≠ 3 |
| `GoldMiner` | 53.78 % | variable, heavily | mean `k` 3.200; `k∈{0,6}` in 306/839 rounds |

Field-wide: **28 of 40 teams proven `k ≠ 3`** (5.53 % of 220 413 observations), so variable `k` is a
*common* practice. Fixed-3 teams (n=4) have median ladder win **78.88 %**; proven-variable teams
(n=23) median **53.28 %**. Fixed-3 outranks variable in 83 of 92 pairs, one-sided permutation
`p = 0.0040`; the sample-size confound is absent and points the other way
(`Spearman(n_games, is-proven) = −0.071`).

**Honest limits on (d).** This is observational. `k ≠ 3` may be a marker of "team currently
experimenting with bespoke builds", and experiments lose. `ZZK` at 84.65 % shows variable `k` is not
disqualifying, `Ausdroid` at the qualification line uses it in production, and `rocket dogs` at
82.37 % is genuinely not adjudicable. The defensible claim is therefore **"no evidence that variable
`k` is advantageous, and the two strongest teams demonstrably achieve their results without it"** —
not "variable `k` is harmful".

**(e) An unsigned term larger than the whole prize.** A bomb charges `(held + 9) // 10` to the
*hitting* unit (`sim/engine.py execute_action`), linear in that unit's purse, and the measured mean
purse at detonation is ≈198 gold — far above the 100-gold richness gate, so the gate does not cap
per-hit burn. Concentrating movement in the rich unit roughly doubles our measured 219.4 gold/game
burn (`+219`); concentrating it in the poor unit roughly removes it (`−218`). **The sign is set by a
`k` rule that does not exist yet, and its magnitude exceeds every instruction cost in the table.**

#### Instruction cost, for the record

Measured from `objdump` of the shipped x86-64 artefact, not from source reading: the 3-step blocked
check is 25 instructions / 112 bytes ⇒ **8 instructions per step per unit**, so 3→6 steps is
`+48` instructions/call directly. `out.k = 3` is literally one instruction (`movl $0x3, …`). The
offset domain does **not** need to grow — `dr0/dc0` already admit `d` up to 6; today's table runs out
of *depth*, not domain, so 6 steps over the existing 7×7 domain is 882 B (+441 B), whereas the naive
13×13 table (3 042 B) buys no scanner-visible reach at all.

| tier | extra instr/call | Δ`.rodata` | gold @1.6 | gold @0.275 | `.text` tax | layout risk |
|---|---:|---:|---:|---:|---:|---|
| T1 full `k∈0..6` | +65…108 | +448…889 B | 104–173 | 18–30 | 26–35 | high |
| T1 + 8-candidate split compare | +129…192 | +448…889 B | 206–307 | 35–53 | 58–77 | high |
| **T2 cap-4 (`k∈{2,3,4}`)** | **+27…46** | **+147 B** | **43–74** | **7–13** | 13–17 | medium |
| T2 cap-5 | +46…75 | +294 B | 74–120 | 13–21 | 19–26 | medium |
| T3a conditional branch form | ~+15 avg | 0 | 24 | 4 | 25–34 | **highest** |
| T3b masked constant-shape | +55…61 | ≤16 B | 88–98 | 15–17 | 25–33 | lower |
| T3c minimal donation, oscillation only | +9…12 | 0 | 14–19 | 2–3 | 5–6 | lowest |
| *any variant re-running `scan`* | +390/pass | — | +624 | +107 | — | **forbid** |
| *7×7 scan to make `k=5/6` gold-directed* | +196 | +1 472 B | 314 | 54 | — | — |

The cheapest non-vacuous tier is **T2 cap-4**: deepen the lookup table from 3 to 4 slots on the
existing offset domain, `+27…46` instructions, total roughly 20–91 gold/game including `.text` tax.
Since (b) shows `k=4` is also the largest scanner-aimable split, T2 cap-4 is simultaneously the
cheapest and the *most* that can be done without paying 314 gold for a 7×7 scan.

**Arithmetic of the verdict:** cheapest credible cost 20–91 gold/game, prize ceiling +83…106
gold/game, an unsigned ±219 gold bomb term on top, and the only steps the cheap tier can supply are
ones the audit prices at `−36 / −5 / −4`. **The axis does not clear its own cost.**

Two further obstacles worth recording: the branch-not-taken cost of the conditional tier is *not*
near zero — a new 20/80 branch whose direction depends on scanned gold values is the pattern that
measured `+40…50 ns` previously, and because `decide` is fully inlined, a rarely-taken body is
exactly what the compiler hoists into `.text.unlikely`, **before** the entry, making T3a the tier
*most* likely to shift the `mod 64 == 0x10` bucket (each wrong bucket is `+11.67 ± 1.67 ns ≈ −128`
gold). And "the parked unit costs nothing" is false at the instruction level: its scan (195), target
(~50) and `blk` (~20) are still paid; only its route emit (~47) can be skipped.

### 5.3 Selectivity (`rikka`'s 43 % stationarity): **portable and cheap, but negative**

Mechanically trivial — emit stays instead of a route. But §4.3 prices it at **−85 gold/game** for
us, because the rounds it would decline currently earn a small positive amount. Judged negative on
income, not on cost.

---

## 6. The 2500 target

Target: map1 uncontested (versus the `probeobs` slow probe, ours 500/500 first) **≥ 2500**.
Current `fd47ea6`: **2182.4**. Gap **+318**.

### 6.1 Verdict: not reachable from anything on this line

| candidate | best-case contribution | verdict |
|---|---:|---|
| copy `rikka`'s ladder-model collection | **0** | nothing identified to copy; level comparison not adjudicable (§4.2b) |
| copy `rikka`'s selectivity | **−85** | negative, §4.3 |
| variable `k`, cheap tier (T3c) | ≈0, cost 14–19 | vacuous: supplies only oscillation (`−36/−5/−4`) |
| variable `k`, T2 cap-4 | **≤ +106**, cost 20–91, ±219 unsigned | does not clear its cost, §5.2 |
| variable `k`, `k=5/6` gold-directed | ≤ +106, cost 314+ | net negative by construction |
| port the 8–11 ms search | large but unavailable | not portable, §5.1 |
| buy vision | no evidence of any | §2.3 |
| low-move fallback drifts us off-centre | **0** | hypothesis falsified, §4.3 |

**My upper bound for this line is +0 to +106 gold/game, most plausibly ≈0, against a requirement of
+318.** I do not believe 2500 is reachable via `rikka`, and I do not think the step-budget axis
should be opened.

### 6.2 What is *not* excluded, with magnitude ceilings

The question "if not here, then where" deserves an explicit answer rather than a list of closures.
Ranked by ceiling, from my own measurements unless marked otherwise:

| # | residual candidate | measured pool | ceiling | status |
|---|---|---|---:|---|
| 1 | **The low-productivity unit-round population.** 52.3 % of our unit-rounds make ≤2 effective moves and earn `0.085`; the 47.7 % that make three earn `3.451`. | 1 566 of 2 994 unit-rounds | `0.523 × (3.451 − 0.085) × 1000` ≈ **+1760** nominal | **open, but attacked once and failed.** The wall-detour prototypes cut fallback 37.3 %→20.7 % and recovered 427/960 blocked decisions, yet measured `−373` (unsafe, more bomb burn) and `−51.5 ± 94` (safe, not adjudicable, pickup *down*). The pool is real and is the only one of the right order of magnitude; the two known attacks on it both failed on burn and on cycles, not on the size of the pool. |
| 2 | **Bomb burn.** 219.4 gold/game, 99.4 % of it on miss unit-rounds, and the richness gate never binds — mean purse at detonation ≈198 against a 100-gold gate. | 219.4 gold/game | **+219** if eliminated | **open, but needs a new mechanism.** Threshold tuning is already judged dead (always-true vs current: `−8±23`, `+6±13`, `+38±26`, all consistent with zero), while always-false is `+344/+374/+212`, so the organ is valuable and the *knob* is not the lever. |
| 3 | Order-conditioned second-mover deficit | 38.5 % of unit-rounds at `0.913` vs `1.944` | ≤ +400 nominal | **mostly illusory.** §4.2b shows it collapses to `0.74σ` in the identified stratum; the residual causal part is small and it is the same anchor already retired once. |
| 4 | Variable `k` | 148 free steps/game | +83…106 | **closed here** (§5.2) |

Items 1 and 2 together have a nominal ceiling far above +318; every other measured axis is at or
below ±106. **So the honest answer is: yes, there is still enough nominal headroom for +318, but all
of it sits in the two pools that have already resisted one attack each, and neither failed because
the pool was too small — they failed on burn and on cycles.** That is a materially different
situation from "no candidates remain".

### 6.3 ⚠️ The 2500 objective may itself be misspecified

An uncontested collection ceiling does not order the ladder. Recomputed here directly from the
archived `probeobs` logs rather than quoted (the probe moved first in 100 % of rounds in every game,
so the uncontested condition holds):

| team | uncontested map1 net ± SE | n | ladder win rate |
|---|---:|---:|---:|
| `Tundra-wawa` `player57` | **2654.8 ± 72.7** | 5 | 78.21 % |
| `Tiuntled-1` `player163` | **2476.4 ± 52.7** | 5 | **95.63 %** |
| ours `f18064c` | 2182.4 (from `src/CHANGELOG.md:11`, n=5) | 5 | — |
| ours `ff46275` (`champff4`, superseded) | 1683.6 ± 63.5 | 10 | — |

`Tundra` is nominally 178.4 ± 89.6 above `T-1` — **`1.99σ`, not adjudicable at the 2σ gate**, so the
ordering is not reversed on this evidence. But it is certainly not *positive*: the ladder's #1 team
does not have the higher ceiling, and the two are indistinguishable while their win rates differ by
17 pp. **So "raise the uncontested ceiling to 2500" is not demonstrably the objective that maximises
rank**, and reaching it would put us level with `T-1`'s ceiling while saying nothing about whether we
would win like `T-1`. Recommend the owner re-examine the north star before more effort is spent on
it; a contested, order-balanced objective is better aligned with the actual ranking rule.

### 6.4 A reusable discipline this line produced

**The licensing ratio.** Any statistic drawn from the fog-filtered trajectory channel must first be
audited as

    bias_ratio = mean income over the visible subset / mean income over the complete gold channel,
                 computed on the same games

Ratio ≈1.00 licenses the trajectory statistics for that construct; anything far from 1.00 does not.
In this report the ratio was 1.00 for our own side and for `rikka`'s public slot, 0.70/0.81 for
`T-1`/`Tundra` (use with caution), and **0.37 for `rikka`'s variable-k builds — which retired a
number I had already computed** (§3.4). Computing the ratio *before* using the channel, rather than
after being surprised, is the cheap habit that caught it.

### 6.5 What this line is worth anyway

It closes two candidate directions with quantified reasons rather than leaving them as hopes:
the step-budget axis (previously *cancelled, never judged*) and "learn collection from a
high-ladder, high-P90 opponent". It also corrects an engine-level factual error in `CHANGELOG`,
supplies a hard benchmark anchor (§1.3), and establishes that no nanosecond-feasible construct in
the corpus buys vision.

### 6.6 Open caveat handed onward: the water-level standard may be partly crowded

On a shared board an opponent's measured income is depressed by ours. Measured
`corr(our mean, their mean)` across games, map1:

| opponent | n | corr ± SE | OLS `d(theirs)/d(ours)` | reading |
|---|---:|---:|---:|---|
| `T-1` `player163` | 99 | `−0.034 ± 0.101` (0.3σ) | −0.029 | **no crowding**; its 2.048 water level is clean |
| `Tundra` `player57` | 90 | `−0.781 ± 0.067` (11.7σ) | **−0.519** | strong crowding |
| `rikka` `player47` | 18 | `−0.223 ± 0.244` (0.9σ) | −0.168 | not adjudicable |

Mechanically coherent: `Tundra` is an extreme centre-camper competing for the same cells, while
`T-1` roams. Implication: the `-411` same-order water-level standard is clean on its `T-1` half but
its `Tundra` half embeds roughly 50 % give-back, so the true level to make up is smaller than 411
and closing the gap against `Tundra` is easier than it looks.

**This correlation is itself confounded** — our construct varies across those games, and a
gold-rich seed lifts both sides (positive) while crowding pushes down (negative), so the observed
coefficient is a sum of opposing effects. I report direction and magnitude only and recommend the
owner of the standard re-derive it within a single construct before changing any number.

---

## 7. Residual unknowns

1. **Our current build has never played `rikka`'s public slot.** The nearest proxies (`vsrikka`,
   `mR1`, 08-09) are n=2 at net `+25`. The parity claim in §4.2 rests on comparing our 1 map1 game
   against their 18, with different opponents on each side.
2. **`rocket dogs` (82.37 %) is not adjudicable on `k`** — 0/955 observations but only 57 exact-k
   rounds, so a rate as high as 5.1 % would be invisible. Same for `若叶睦的狗2.0`, `counterfactual`,
   `君の仿瓷`.
3. **The bomb-concentration sign is undetermined** (§5.2e) and would need a concrete `k` rule.
4. **The general-population target-distance histogram is unmeasured**; only the wall-blocked subset
   exists, so the true duty cycle of a fourth step is an assumption.
5. Every instruction figure here is a **budget, not an acceptance criterion**. The gate is perf
   cycles per call, same-window paired and seat-swapped, at ≥2σ; a prior candidate was −42
   instructions and +15…18 cycles. If this axis were ever reopened, the table above cannot substitute
   for that gate.
6. Any local A/B on this axis must report **both order arms**; every historical local A/B in this
   repository ran only the "we move first" condition, and our weakest condition is second.
