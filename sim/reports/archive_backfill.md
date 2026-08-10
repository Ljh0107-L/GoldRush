# Archive backfill, forfeit hygiene, and reconstruction of the missing `f18064c` samples

> Round of 2026-08-10. **Zero platform games consumed.** Analysis of existing logs only.
> No file under `src/` was touched; `sim/engine.py`, `sim/scenario.py`, `sim/abi.py`,
> `sim/probe/archive_logs.py` and `sim/analyze_gold_delta.py` were run **unmodified**.
> Artifacts written: `sim/reports/archive_backfill.md` (this file) and
> `sim/reports/archive_backfill.json` (machine-readable companion, every number below).
>
> Build identity for everything in this report: the frozen deliverable is `f18064c`
> (`perf(player): disable failed expedition scheduler`), `src/player.cpp` SHA256
> `0ecce6fc0d7141dd2ca4ddbb18dbee2aaff67a5a8f0a981df89bc9b9aba84fdd`. Repo HEAD at time of
> writing is `00c1712`, whose `src/player.cpp` is `d9be1e52…f455d22` — i.e. **HEAD is no longer
> f18064c** (the parallel Worker's `895a27e` landed a scan change). Every figure below is
> derived from platform logs produced by `f18064c` binaries, not from HEAD.

## 0. Answer first

| question | answer |
|---|---|
| A1. archive gap | **closed with zero code change.** Tracked games archived **112 → 270** (Tiuntled-1 69→158, Tundra-wawa 43→112). Root cause was a **stale manifest**, not an archiver defect |
| A2. does the enlarged corpus change any conclusion | **No — not one number moves.** `analyze_gold_delta.py` never reads the manifest; it globs `logs/game_*.log` directly, so all 270 games were already visible to the analyzer. `validate` still reports `all families match`; `frozen` is identical on **201/201** leaf values |
| A3. forfeits | **7** tracked-opponent games, all 7 already inside the pre-existing 112. All 7 are **our own** crippled latency-probe builds crashing; **none is an `f18064c` game**, so forfeit handling cannot touch any frozen-build figure |
| B1. n=12 T-1 map2 | **FOUND and reproduced exactly**: `t1f2` ∪ `t1n2`, 2W/10L, **−164.583 ± 61.629 SE (2.67σ)** vs quoted −164.6 ± 61.6. Match to 0.017 gold on the mean, 0.029 on the SE |
| B2. n=12 T-1 map3 | **FOUND and reproduced exactly**: `t1f3` ∪ `t1n3`, 3W/9L, **−102.917 ± 63.515 SE (1.62σ)** vs quoted −102.9 ± 63.5. Match to 0.017 / 0.015 |
| B3. map1 sample size | T-1 map1 **cannot** be tightened: `t1f1` n=6 only, and it is **1.83σ — undecidable, not a stable loss**. Tundra map1 **can**: **24** `f18064c` games exist (4 same-window arms), pooling to **−289.04 ± 54.65 SE, 5.29σ, 3W/21L** — a decisive stable loss |
| B4. the two Tundra map1 figures | **Not reconcilable.** −35.4 ± 45.5 is **unreproducible from the corpus by a hard bound**: the *20 best* Tundra-map1 games out of all 90 average **−111.70**, so no 20-game subset can average −35.4. It is a different window with no log representation at all |
| C. untracked opponents | **None worth tracking now.** Largest are `player167` (14) and `player132` (10), both **map1-only**, both fragmented across many builds, and **all of their games predate `f18064c`** (game ids 163k–164k vs 175967+). Recommendation only; tracked set unchanged |

## 1. Part A — archive backfill

### 1.1 Before and after

`python3 sim/probe/archive_logs.py build`, run as-is, twice (once mid-session, once at the end).

| tree | before | after |
|---|---:|---:|
| `logs/opponents/Tiuntled-1` (`player163`, model 87478) | 69 | **158** |
| `logs/opponents/Tundra-wawa` (`player57`, model 43116) | 43 | **112** |
| `logs/opponents/_selfplay-champ` (`champff4`) | 10 | 10 |
| **tracked-opponent total** | **112** | **270** |
| manifest `games[]` entries | 122 | **280** |
| manifest `schema_version` | 2 | 2 (unchanged) |
| `skipped_logs` | 1 | 1 (same log) |
| broken symlinks | — | **0** |

The gap of **89 + 69 = 158** closed exactly. The briefing's figures were correct at source.

**Schema and value stability.** All 122 pre-existing entries survive with an **identical key set and
identical values** (verified field-by-field against a pre-run copy of the manifest). Nothing was
rewritten, no entry was lost.

### 1.2 Root cause of the gap — staleness, not a defect

The previous manifest was generated `2026-08-09T15:13:49Z`; **258** of the raw logs post-dated it.
The archiver required **no change at all**: it already handles every log in the corpus. The only
skipped log is `logs/game_172111.log`, and it is legitimately unarchivable *and* irrelevant:

```
line 1: {"player1":"champ76e","player2":"probeobs"}
line 3: {"round":0,"forfeit":{"player_id":1,"reason":"runtime_error",...,"exit_code":-4}}
```

Zero completed rounds, and **no tracked opponent in the header**, so it never belonged to the 270.

> Two briefing figures were stale by the time I ran, worth noting for arithmetic hygiene: `logs/`
> held **526** raw `game_*.log` at my first scan and **530** at my last (not 522), and it is
> **783 MB**, not 132 MB. The 4–8 arrivals are a sibling agent's `gpA*`/`gpB*` self-play A/B
> (`sim/reports/ipc_golf_round1.md`); none involves a tracked opponent, so **the tracked total is
> stable at 270** and no figure in this report is affected. `logs/` is gitignored either way.

### 1.3 Forfeits — 7 games, chain broken, none of them `f18064c`

`sim/analyze_gold_delta.py` handles this correctly as written: `rounds()` yields `None` for any row
lacking both `start` and `end`, and `unit_deltas()` responds by clearing `previous`, so **the
difference chain is broken, not skipped** — no delta ever spans the gap. Verified by reading the
source, lines 87–97 and 138–141.

All 7 tracked-opponent forfeits, from primary logs:

| log | opponent | map | our build | completed rounds | forfeit row `round` | forfeiter | reason |
|---|---|---|---|---:|---:|---|---|
| `game_170565.log` | Tundra | map1 | `L4tun` | 63 | 63 | **us** (pid 1) | `runtime_error` (−11) |
| `game_170567.log` | T-1 | map1 | `L4t1` | 203 | 203 | **us** | `runtime_error` (−11) |
| `game_170860.log` | Tundra | map1 | `L8p` | 425 | 425 | **us** | `runtime_error` (−11) |
| `game_170986.log` | T-1 | map1 | `Lsp` | 424 | 424 | **us** | `runtime_error` (−11) |
| `game_171022.log` | T-1 | map1 | `Ltp` | 56 | 56 | **us** | `action_out_of_range` |
| `game_171223.log` | T-1 | map1 | `Lxp` | 80 | 80 | **us** | `action_out_of_range` |
| `game_171272.log` | T-1 | map1 | `Lzv1` | 342 | 342 | **us** | `action_out_of_range` |

Three facts worth recording:

1. **All 7 were already inside the pre-existing 112.** The backfill added **zero** new forfeits, so
   the "7 of 112" hygiene note now reads "**7 of 270**".
2. Round records are **0-indexed** (`complete_rounds = last_complete_round + 1`, 516/530 games at
   500/499). The briefing's "57–426 rounds" is the 1-based ordinal of the round that died
   (`forfeit.round + 1` = 57, 64, 81, 204, 343, 425, 426); my table reports the 0-based field
   verbatim. **Same games, same range, different indexing convention** — no discrepancy.
3. Every forfeiter is **us**, in the `L*` latency-probe family (deliberately crippled builds), and
   **no forfeit occurs in any `f18064c` family**. Independently confirmed by the channel:
   the frozen set's unit-round count is exactly `36 × 2 × 499 = 35,928`, i.e. no game ran short.

### 1.4 Does the enlarged corpus change any existing conclusion? **No.**

This is the deliverable that matters, and the answer is a clean no, for a structural reason:
**`sim/analyze_gold_delta.py` does not consult `logs/opponents/manifest.json`.** Its `game_files()`
globs `logs/game_*.log` directly (line 100–101). The archive gap was therefore a *bookkeeping* gap,
never a data-visibility gap — the analyzer was already seeing all 270 games while the manifest
listed 112.

| check | before backfill | after backfill | change |
|---|---|---|---|
| `analyze_gold_delta.py validate` verdict | `all families match` | `all families match` | none |
| `validate` per-family games | 6 / 6 / 6 / 6 / 6 / 6 | 6 / 6 / 6 / 6 / 6 / 6 | none |
| `frozen` output vs `sim/reports/gold_delta_channel.json` | — | **201 leaf values, 0 differences** | none |
| pooled ours `ge8` | 6.4685% | 6.4685% | none |
| pooled ours / theirs gold per scoring round | 4.6684 / 4.1894 | 4.6684 / 4.1894 | none |
| pooled ours / theirs hit rate | 34.786% / 41.146% | 34.786% / 41.146% | none |

Headline numbers re-verified at source (`sim/analyze_gold_delta.py frozen`, `pooled` block,
n = 35,928 unit-rounds per side, 36 `f18064c` games):

- `ge8` **6.4685%** ours vs **5.6725%** theirs — the briefing's 6.47% is exact.
- gold per scoring round **4.66843** ours vs **4.18941** theirs — 4.67 / 4.19 exact.
- hit rate **34.786%** vs **41.146%** — 34.8% / 41.1% exact.

**The archive backfill changes nothing. What *does* change conclusions is §2–§3 below, and that
comes from newly *identifying* `f18064c` games, not from archiving them.** The two must not be
conflated.

### 1.5 Untracked opponents — recommend not expanding the tracked set

Twelve non-tracked `player*` accounts appear in the corpus (the briefing named five of them; there
are seven more):

| account | games | maps | our build families faced (largest) | best-sampled family |
|---|---:|---|---|---|
| `player167` | 14 | map1 only | 8 families, largest `shadB` = 5 | `shadB` n=5, **−319.6 ± 96.0**, 1W |
| `player132` | 10 | map1 only | 4 families, largest `mG` = 6 | `mG` n=6, **+204.0 ± 70.7**, 5W |
| `player147` | 6 | map1 only | 4 families, largest 2 | `mM` n=2, +210.5 |
| `player47`, `player204` | 2 each | map1 | singleton families | — |
| `player83`, `player137`, `player186`, `player220`, `player224`, `player2`, `player3` | 1 each | map1 | singleton | — |

**Recommendation: do not expand the tracked set.** Reasons, in order of weight:

1. **Every one of these games predates `f18064c`.** Their game ids sit in 163068–164098; `f18064c`'s
   own platform games start at 175967. There is not a single `f18064c`-vs-untracked-opponent game in
   the corpus, so tracking them cannot price any current decision.
2. **All 12 are map1-only.** No map2/map3 coverage at all, so no three-map picture is possible.
3. Only two clear n≥6 same-build cells exist (`player132`/`mG` n=6 and `player167`/`shadB` n=5),
   both from the pre-`f18064c` era.

If a third opponent is ever wanted, the two candidates are `player132` (we beat it: +204 at n=6) and
`player167` (it beats us hardest of any opponent in the corpus: −319.6 at n=5) — but both would need
**fresh** `f18064c` games, which is a platform-budget decision for the orchestrator, not an
archiving decision. Adding them to `TEAMS` in `archive_logs.py` costs nothing and is
backward-compatible if the orchestrator wants indexing without new games.

## 2. Part B — the n=12 top-ups, located and reproduced from primary data

### 2.1 How they were found (no back-fitting)

Method, in order, before any target number was compared:

1. Enumerate **all** build families per opponent from log line 1 (114 families over 270 games:
   67 vs T-1, 47 vs Tundra; 260 distinct build names).
2. Classify each game's map by **row fingerprint of log line 2**, using `sim/maps.json`:
   `definition_sha256` of the joined rows identifies map1/map2, and `walls_sha256` of the
   wall-projection identifies map3 (map3 is `limited` — only walls are recoverable from `BAKED_W`).
   This is the same recipe `sim/calibrate_views.py:36-45,221` uses.
3. Filter for T-1 families of **n=6 on map2 / map3** that are *not* the already-proven `t1f*`.
   Exactly two survive: **`t1n2` (map2)** and **`t1n3` (map3)**. There is no `t1n1`.
4. *Only then* compare against the quoted figures.

### 2.2 T-1 map2, n=12 = `t1f2` ∪ `t1n2`

Per-game net score difference = (our `gold − vision_spent`) − (theirs), from the last complete
`end` row of each log. All 12 games ran the full 500 rounds; no forfeits.

| # | log | our build | game id | our net | opp net | Δ |
|---:|---|---|---:|---:|---:|---:|
| 1 | `logs/game_176022.log` | `t1f2a` | 176022 | 2008 | 2135 | −127 |
| 2 | `logs/game_176031.log` | `t1f2b` | 176031 | 1971 | 1989 | −18 |
| 3 | `logs/game_176055.log` | `t1f2c` | 176055 | 1799 | 2126 | −327 |
| 4 | `logs/game_176063.log` | `t1f2d` | 176063 | 1959 | 1966 | −7 |
| 5 | `logs/game_176068.log` | `t1f2e` | 176068 | 1753 | 2033 | −280 |
| 6 | `logs/game_176074.log` | `t1f2f` | 176074 | 1992 | 1873 | **+119** |
| 7 | `logs/game_176395.log` | `t1n2a` | 176395 | 1791 | 2398 | **−607** |
| 8 | `logs/game_176396.log` | `t1n2b` | 176396 | 1901 | 2349 | **−448** |
| 9 | `logs/game_176397.log` | `t1n2c` | 176397 | 2094 | 2056 | **+38** |
| 10 | `logs/game_176399.log` | `t1n2d` | 176399 | 1846 | 1908 | −62 |
| 11 | `logs/game_176400.log` | `t1n2e` | 176400 | 1936 | 2024 | −88 |
| 12 | `logs/game_176401.log` | `t1n2f` | 176401 | 1858 | 2026 | −168 |

| quantity | recomputed from logs | quoted (`ac33eaaa-180`, `path_harvest_verdict.md` §1.2, `src/CHANGELOG.md`) | verdict |
|---|---:|---:|---|
| wins / losses | **2 / 10** | 2 / 10 | **match** |
| mean Δ | **−164.5833** | −164.6 | **match** (0.017) |
| SE | **61.6287** | 61.6 | **match** (0.029) |
| σ | **2.671** | 2.67 | **match** |
| sd | 213.488 | — | — |

### 2.3 T-1 map3, n=12 = `t1f3` ∪ `t1n3`

| # | log | our build | game id | our net | opp net | Δ |
|---:|---|---|---:|---:|---:|---:|
| 1 | `logs/game_175967.log` | `t1f3a` | 175967 | 939 | 1234 | −295 |
| 2 | `logs/game_175969.log` | `t1f3b` | 175969 | 1129 | 860 | **+269** |
| 3 | `logs/game_175970.log` | `t1f3c` | 175970 | 951 | 1058 | −107 |
| 4 | `logs/game_175971.log` | `t1f3d` | 175971 | 874 | 1122 | −248 |
| 5 | `logs/game_175973.log` | `t1f3e` | 175973 | 849 | 978 | −129 |
| 6 | `logs/game_175974.log` | `t1f3f` | 175974 | 934 | 1048 | −114 |
| 7 | `logs/game_176387.log` | `t1n3a` | 176387 | 987 | 1283 | −296 |
| 8 | `logs/game_176389.log` | `t1n3b` | 176389 | 901 | 1082 | −181 |
| 9 | `logs/game_176390.log` | `t1n3c` | 176390 | 1064 | 780 | **+284** |
| 10 | `logs/game_176391.log` | `t1n3d` | 176391 | 821 | 1099 | −278 |
| 11 | `logs/game_176392.log` | `t1n3e` | 176392 | 911 | 1215 | −304 |
| 12 | `logs/game_176394.log` | `t1n3f` | 176394 | 1117 | 953 | **+164** |

| quantity | recomputed | quoted | verdict |
|---|---:|---:|---|
| wins / losses | **3 / 9** | 3 / 9 | **match** |
| mean Δ | **−102.9167** | −102.9 | **match** (0.017) |
| SE | **63.5147** | 63.5 | **match** (0.015) |
| σ | **1.620** | 1.62 | **match** |

### 2.4 Why the identification is proven, not assumed

Five independent pieces of evidence, none of which was used to *choose* the families:

1. **Structural uniqueness.** `t1n2` and `t1n3` are the *only* non-`t1f` T-1 families with n=6 on
   map2/map3 that are not already attributed elsewhere (`t1x*` = predecessor `0c2e101`,
   `mxT13` = reverted argmax candidate, both anchored below).
2. **Six quantities match simultaneously** — two means to 0.02 gold, two SEs to 0.03, two win
   counts, two σ values. A coincidence of that shape is not credible.
3. **The named tail values are unique fingerprints.** The briefing quotes "a long negative tail
   including −448 and −607". Across **all 270 tracked games**, Δ = −448 occurs **exactly once**
   (`game_176396`, `t1n2b`) and Δ = −607 occurs **exactly once** (`game_176395`, `t1n2a`). Both
   land in `t1n2`, and nowhere else in the corpus.
4. **Campaign block structure.** The five families form one contiguous campaign, and there is
   **not a single other T-1 game** between the end of `t1f1` and the start of `t1n3`:

   | order | family | map | game ids | wall-clock (log mtime) |
   |---:|---|---|---|---|
   | 1 | `t1f3` | map3 | 175967–175974 | 10:30 |
   | 2 | `t1f2` | map2 | 176022–176074 | 10:33 |
   | 3 | `t1f1` | map1 | 176120–176151 | 10:35 |
   | 4 | `t1n3` | map3 | 176387–176394 | 10:55 |
   | 5 | `t1n2` | map2 | 176395–176401 | 10:58 |

   n=6 on each of three maps, then a top-up on map3 and map2 **and only those two** — which is
   exactly why `src/CHANGELOG.md` marks map1 "未补样". The absence of a `t1n1` family is
   positive corroboration, not a hole.
5. **Fog-free income profile agrees.** Own-side per-unit gold statistics are properties of the
   decision rule, so the same binary must reproduce them:

   | family | our mean/unit-round | hit | yield/hit | ge8 |
   |---|---:|---:|---:|---:|
   | `t1f2` (map2, proven `f18064c`) | 1.9205 | 0.4452 | 4.7011 | 0.0832 |
   | **`t1n2`** (map2) | **1.9112** | **0.4395** | **4.6451** | **0.0807** |
   | `t1f3` (map3, proven `f18064c`) | 0.9509 | 0.2326 | 4.5650 | 0.0439 |
   | **`t1n3`** (map3) | **0.9718** | **0.2420** | **4.5756** | **0.0429** |

   Honest caveat: this check **confirms but does not discriminate strongly** — `t1x2`/`t1x3`
   (predecessor `0c2e101`) sit within a few percent too, because `f18064c` differs from it by
   removing dormant code. Items 2–4 carry the identification; item 5 only fails to contradict it.

**One correction to the record.** `path_harvest_verdict.md` §1.2 says the n=12 sets "never reached
the repo". As of this round the *numbers* are in `src/CHANGELOG.md` (the parallel Worker's 8.10
edit), and `−102.9 ± 63.5` is already load-bearing there as the T-1 map3 frozen baseline for the
argmax revert. What was missing was the **primary record**: which logs, which game ids, which
per-game deltas. That is now supplied above and in the JSON companion, and both figures are
independently reproducible from `logs/` by anyone.

## 3. Part B — map1 sample-size adjudication

### 3.1 T-1 map1: cannot be tightened, and is **not** a stable loss

`t1f1` is the only `f18064c` family on T-1 map1. Searched: all 67 T-1 families, all 99 T-1 map1
games. No sibling, no top-up, no A/B baseline arm.

| set | n | Δ per game | mean | SE | σ | W/L |
|---|---:|---|---:|---:|---:|---|
| `t1f1` (`f18064c`, T-1 map1) | 6 | `[-688, +314, -383, -90, -600, -199]` | **−274.33** | **149.98** | **1.83** | 1 / 5 |

`src/CHANGELOG.md` records −274.3 with no σ label, and it is right not to claim one: **1.83σ is
below the 2σ bar, i.e. undecidable at n=6, not a "stable loss"**. Anyone pricing a strategy against
"map1 = −274" is pricing against a point estimate whose 1SE band is ±150 gold. `未补样` is correct
and the honest label for this cell is *undecidable-negative*.

### 3.2 Tundra map1: **24** `f18064c` games exist, not 6

This is the one place the sample genuinely enlarges. Three A/B campaigns on Tundra map1 used
`f18064c` as the **baseline arm**, and their baseline arms are `f18064c`'s own games:

| family | role | n | mean Δ | SE | anchor in `src/CHANGELOG.md` | anchor matches? |
|---|---|---:|---:|---:|---|---|
| `frTu1` | current-window recheck | 6 | −219.17 | 107.69 | "map1 1-5, −219.2±107.7" + per-game list | **exact, incl. all 6 deltas** |
| `lnA0` | linker verdict, arm **A** = baseline | 6 | −412.67 | 67.52 | "净差 A −412.7±67.5" | **exact, mean and SE** |
| `alA0` | mod64 scan L1, arm **A** = incumbent `L0` | 6 | −400.83 | 135.94 | *(only P50 pairs quoted; no net anchor)* | design only |
| `a2A0` | mod64 scan L2, arm **A** = incumbent `L0` | 6 | −123.50 | 91.15 | "L2 … 净差配对 −262.7±97.3" → my `a2B0−a2A0` = **−262.67 ± 97.26** | **exact** |

Cross-checks that pin the arm assignment independently: `lnB0` = −387.17 ± 99.05 vs the CHANGELOG's
"B −387.2±99.0" (exact), and our first-dispatch rates recomputed from the manifest match the
CHANGELOG game-for-game — `frTu1` 53.93%, `frTu2` 72.67%, `frTu3` 70.07%, `mxTu1` 44.93%,
`mxT13` 48.83%.

| pooling | n | mean | SE | σ | W/L |
|---|---:|---:|---:|---:|---|
| `frTu1` alone (current published figure) | 6 | −219.17 | 107.69 | 2.04 | 1 / 5 |
| **all four arms** | **24** | **−289.04** | **54.65** | **5.29** | 3 / 21 |
| anchor-proven three arms only (drop `alA0`) | 18 | −251.78 | 57.01 | 4.42 | 3 / 15 |

**Pooling is legitimate here.** All four batches ran inside one 12-minute window (game ids
179643–179761, log mtimes 19:44–19:56 on 8-09), and the spread of the four batch means
(sd 141.5) is barely above what within-batch sampling noise alone predicts (109.3) — so the
−123.5 … −412.7 range is noise, not four different regimes. Either pooling (n=24 → 5.29σ or n=18 →
4.42σ) makes **Tundra map1 a decisive stable loss**, and *worse* than the currently published
−219.2. The n=6 figure was on the optimistic side of its own window by about 70 gold.

### 3.3 The −35.4 ± 45.5 baseline is unreproducible — a hard bound, not a failed search

| test | result |
|---|---|
| required sum of 20 deltas for a −35.4 mean | −708 |
| the **20 best** Tundra-map1 games out of all 90 in the corpus | sum **−2234**, mean **−111.70** |
| wins in the whole 90-game Tundra-map1 population | **3** |
| conclusion | **no 20-game subset of the corpus can average −35.4**, for any choice of builds |

That is a bound, not a search failure: even cherry-picking the twenty best games from every build
ever run against Tundra map1 lands 76 gold per game short of the claim. A second, independent
indicator agrees: the old window is documented at **80.04%** our-first on Tundra map1, and **no
family in the corpus reaches it** (maximum is `a2A0` at 66.5%; `frTu1` is 53.93%). Likewise the old
window's "9-1 / +242" Tundra map2 set cannot exist here — the corpus holds only **11** Tundra map2
games in total.

**Verdict: the two figures are separated by a window/version change and cannot be reconciled.**
−219.2 (n=6) and −289.0 (n=24) are the *same* window and agree within noise; −35.4 ± 45.5 is an
earlier window whose primary logs are **absent from `logs/` entirely**. Provenance: the figure
entered the repo exactly once, in commit `dc0949f`, with no supporting artifact — the same class of
number as the n=12 top-ups, except that this one is **not** recoverable.

### 3.4 Consequence: one leg of the argmax revert rests on that unverifiable baseline

`dc0949f` rejected the global-amount-priority candidate on two head-to-head legs. Re-derived from
primary data:

| leg | candidate | baseline used in `src/CHANGELOG.md` | claimed regression | **recomputed against primary-data baseline** |
|---|---|---|---|---|
| T-1 map3 | `mxT13` −376.83 ± 40.51 (n=6) | −102.9 ± 63.5 (n=12) | −273.9, 3.64σ | **−273.92, combined SE 75.33, 3.636σ — reproduces exactly** |
| Tundra map1 | `mxTu1` −304.67 ± 55.78 (n=6) | −35.4 ± 45.5 (n=20, **unreproducible**) | −269.3, 3.74σ | vs same-window n=24 pool −289.04 ± 54.65: **Δ = −15.63, combined SE 78.09, z = 0.20 — no regression at all** |

**The verdict stands, but for one reason, not two.** The T-1 map3 leg is now fully verifiable from
primary data and is decisive on its own at 3.64σ; the measured +27.5 ns platform latency tax is
independent evidence. But the Tundra map1 leg's "3.74σ deterioration" is an artefact of comparing a
current-window candidate against a *different window's* baseline. On same-window data the argmax
candidate and `f18064c` are indistinguishable on Tundra map1 (z = 0.20). This is exactly the failure
mode 军规 27 exists to catch, and `src/CHANGELOG.md` already carries the specific rule against it —
军规 21, "跨窗基线不可替代同窗控制" (cite by content, not line: the file is being edited
concurrently). The rule is stated later in the same file than the passage that breaks it.

### 3.5 Golf head-room, repriced against primary deficits

The 90–160-instruction position is worth 144–256 gold (`src/CHANGELOG.md` subsystem audit). Against
deficits recomputed here rather than the n=6 figures:

| battlefield | deficit (source) | flipped by 144? | flipped by 256? |
|---|---:|---|---|
| T-1 map3 | **−102.92** (n=12, reconstructed) | **yes** | yes |
| T-1 map2 | **−164.58** (n=12, reconstructed) | no | **yes** (marginal) |
| T-1 map1 | −274.33 (n=6, 1.83σ) | no | no |
| Tundra map1 | **−289.04** (n=24, 5.29σ) | no | no |

The CHANGELOG's "可望翻 map3（−103）、也许覆盖 map2（−165）、不足以单独翻 map1（−274）" survives
unchanged, with the map2 and map3 figures now backed by primary data. The one thing to add is that
Tundra map1 at −289 is **also** out of reach, and by more than T-1 map1 is.

## 4. Effect of the enlarged `f18064c` sample on the fog-free conclusions

The archive backfill moved nothing (§1.4). The *sample enlargement* from §2–§3 moves the fog-free
figures slightly. Channel is `end.players[].units[].gold` differenced round-over-round; forfeit rows
break the chain; `pickup` deliberately unused (it is fog-**truncated**, so `pickup >= delta_held`
must hold before it can be trusted, whereas per-unit `gold` is present in 100% of
unit-observations).

| pooled quantity | published set (36 games) | + n=12 top-ups (48 games) | + Tundra map1 arms (66 games) |
|---|---:|---:|---:|
| unit-rounds per side | 35,928 | 47,904 | 65,868 |
| ours `ge8` | **6.4685%** | 6.3961% | 6.4462% |
| theirs `ge8` | 5.6725% | 5.7448% | 5.8130% |
| ours gold per scoring round | **4.6684** | 4.6566 | 4.6662 |
| theirs gold per scoring round | 4.1894 | 4.2625 | 4.1353 |
| ours hit rate | **34.786%** | 34.609% | 34.364% |
| theirs hit rate | **41.146%** | 40.425% | 42.776% |
| hit-rate gap (pp) | 6.36 | 5.82 | **8.41** |
| mean net score Δ | −68.03 | −91.56 | −151.77 |
| channel-vs-observed residual | +1.87 | +2.07 | **+1.34** |

**Every load-bearing conclusion survives, and two strengthen:**

- "we reach ≥8 more often than they do" — **holds** (6.45% vs 5.81% on 66 games).
- "we extract more per scoring round" — **holds** (4.666 vs 4.135).
- "the entire deficit is hit rate" — **holds and strengthens**: the gap widens from 6.36 pp to
  8.41 pp.
- "yield-per-hit exceeds theirs in 6 of 6 battlefields" — **holds, 6 of 6**, on both sets.
- The channel stays **complete**: max reconciliation residual 2.80 gold on ±290-gold quantities.

**One published sub-claim weakens and must be corrected**, per 军规 27:

> `path_harvest_verdict.md` §2: "our ≥8 rate in **5 of 6** [battlefields]".

At the enlarged sample it is **4 of 6**. T-1 map2 flips when its top-up is included: ours 8.191% vs
theirs 8.450% at n=12, where at n=6 it was ours 8.317% vs theirs 8.233%. (T-1 map1 was already the
sixth exception at n=6: 6.814% vs 6.864%.) The pooled statement is unaffected.

Also for exactness: the same paragraph quotes "T-1 6.20% and Tundra 5.12%" for *their* ≥8 rates.
At source they are **6.212%** and **5.132%**. Rounding drift of 0.01 pp, no consequence, recorded
for completeness.

## 5. Numbers in this brief or in the repo that turn out to be wrong

| number | where | status |
|---|---|---|
| tracked-opponent games 158 + 112 = 270; archived 69 + 43 = 112; gap 158 | briefing | **correct**, verified at source |
| 7 forfeits, 57–426 rounds | briefing | **correct**; 0-based field reads 56–425, same games |
| ours `ge8` 6.47%, 4.67 vs 4.19, hit 34.8% vs 41.1% | briefing | **correct to the digit** |
| `logs/` holds 522 logs / 132 MB | briefing | **stale**: 530 logs / 783 MB, and growing from a sibling's self-play A/B. No tracked-opponent effect |
| n=12 sets have "no primary record in the repo" | briefing | **half right**: numbers are now in `src/CHANGELOG.md`; the primary log-level record was missing and is supplied here |
| "our ≥8 rate in 5 of 6 battlefields" | `sim/reports/path_harvest_verdict.md` §2 | **4 of 6** at the enlarged sample |
| "T-1 6.20% / Tundra 5.12%" ≥8 rates | `sim/reports/path_harvest_verdict.md` §2 | **6.212% / 5.132%** |
| Tundra map1 frozen baseline −35.4 ± 45.5 (n=20) | `src/CHANGELOG.md`, from `dc0949f` | **unreproducible from any corpus subset** (hard bound: best-20 = −111.70). Cross-window; must not be used as a same-window control |
| "T-1 map1 = −274.3" used as a stable deficit | `src/CHANGELOG.md`, subsystem audit | figure is right; it is **1.83σ, undecidable**, and should be labelled so |
| burst-rate comparison in `sim/OPPONENTS.md` (ours 15.2% vs 32.5%/34.4%, table currently ~lines 471–478, row explicitly marked ⛔混合体) | `sim/OPPONENTS.md` | **still poison** — a ~114-family archive mixture, not `f18064c`. Confirmed independently: the corpus now holds **114** distinct families / **260** distinct build names over 270 games |

## 6. Recommendations to relay for `src/CHANGELOG.md` (I did not edit it)

Anything below belongs to the parallel Worker's file. Ordered by importance.

1. **Add the primary-record provenance for the n=12 top-ups.** They are now reproducible from
   `logs/`, not only from session log `ac33eaaa-180`:
   - T-1 map2 n=12 = families `t1f2` ∪ `t1n2`, game ids 176022/176031/176055/176063/176068/176074 +
     176395/176396/176397/176399/176400/176401; Δ = `[-127,-18,-327,-7,-280,+119,-607,-448,+38,-62,-88,-168]`;
     **−164.583 ± 61.629 SE, 2.67σ, 2W/10L**.
   - T-1 map3 n=12 = `t1f3` ∪ `t1n3`, game ids 175967/175969/175970/175971/175973/175974 +
     176387/176389/176390/176391/176392/176394; Δ = `[-295,+269,-107,-248,-129,-114,-296,-181,+284,-278,-304,+164]`;
     **−102.917 ± 63.515 SE, 1.62σ, 3W/9L**.
   - Verification command: `python3 sim/analyze_gold_delta.py survey --min-games 5` shows both
     families; details in `sim/reports/archive_backfill.json` → `n12_reconstruction`.
2. **Replace the Tundra map1 head-to-head with the n=24 same-window pool.** `−289.04 ± 54.65 SE
   (5.29σ, 3W/21L)`, from four `f18064c` baseline arms `frTu1` + `lnA0` + `alA0` + `a2A0`, game ids
   179643–179761, one 12-minute window. Keep `−219.2 ± 107.7` (`frTu1`, n=6) as the secondary
   single-batch figure. Tundra map1 becomes a **decisive** stable loss, worse than published.
3. **Retire `−35.4 ± 45.5` as a control, and annotate the argmax verdict.** It is unreproducible
   from any 20-game subset of the corpus (best-20 = −111.70) and is cross-window. Recommended
   wording: the Tundra map1 leg of `23db121`'s rejection is **not** a −269.3 / 3.74σ regression; on
   same-window primary data it is **Δ = −15.6, z = 0.20, i.e. no regression**. The verdict stands on
   the T-1 map3 leg alone (−273.92, 3.636σ, reproduces exactly) plus the +27.5 ns latency tax. This
   is a live instance of the rule already at `src/CHANGELOG.md:394`.
4. **Label T-1 map1 as undecidable.** `−274.3 ± 149.98 SE = 1.83σ` at n=6 — below the 2σ bar. Either
   annotate the table cell or top it up. It is currently the only battlefield with no top-up and no
   σ label, and the golf/instruction head-room argument leans on it.
5. **Correct `sim/reports/path_harvest_verdict.md` §2** (also not mine to edit): "≥8 rate higher in
   5 of 6 battlefields" → **4 of 6** at the enlarged sample (T-1 map2 flips at n=12); their ≥8 rates
   are 6.212% / 5.132%, not 6.20% / 5.12%. The headline conclusion (deficit is hit rate) is
   unaffected and in fact strengthens to an 8.41 pp gap on 66 games.
6. **Archiving discipline, generalised.** The archive gap was pure manifest staleness, so the fix is
   a habit, not code: run `python3 sim/probe/archive_logs.py build` at the end of any round that
   pulls platform logs (≈11 s on 530 logs). Note also that a game's *result* is only recoverable
   later if the **build name encodes opponent + map + replicate**, as `t1f2a…f` does — that naming
   convention is what made this reconstruction possible at all, and it is worth making a rule.
7. **Do not expand the tracked opponent set now.** All 12 untracked accounts are map1-only and every
   one of their games predates `f18064c`. Candidates if ever needed: `player132` (n=10, `mG` n=6 at
   **+204.0 ± 70.7**) and `player167` (n=14, `shadB` n=5 at **−319.6 ± 96.0**, the hardest opponent
   in the corpus). Both need fresh games to be worth anything.

## 7. Reproduction

```bash
# archive (unmodified, ~11 s on 530 logs)
python3 sim/probe/archive_logs.py build
python3 sim/probe/archive_logs.py paths --team Tiuntled-1 | wc -l      # 158
python3 sim/probe/archive_logs.py paths --team Tundra-wawa | wc -l     # 112

# fog-free channel (unmodified)
python3 sim/analyze_gold_delta.py validate      # -> "all families match", 6 x n=6
python3 sim/analyze_gold_delta.py frozen        # -> identical to sim/reports/gold_delta_channel.json
python3 sim/analyze_gold_delta.py survey --min-games 5   # -> t1n2 -222.50, t1n3 -101.83 visible here
```

Every number in this report is also in `sim/reports/archive_backfill.json`, keyed under `archive`,
`forfeits`, `untracked_opponents`, `n12_reconstruction`, `map1_adjudication`, `fog_free_channel`,
`changelog_per_game_audit`, `argmax_revert_re_adjudication`, `golf_headroom_vs_primary_deficits` and
`family_census`, each with its source logs and build names.

**Audit pass on `src/CHANGELOG.md`'s own per-game lists.** Every quoted list and SE reproduces
byte-exactly from primary logs — `frTu1`, `frTu2`, `frTu3`, `mxTu1`, `mxT13`, `lnA0`, `lnB0`:
7 of 7 on the delta lists, 7 of 7 on means, 7 of 7 on SEs. The CHANGELOG's platform numbers are
sound; the only defect found is the one cross-window baseline in §3.3.

## 8. Boundaries respected

- **Zero platform games.** No game was run; the 8 logs that appeared mid-session are a sibling
  agent's, contain no tracked opponent, and are excluded from every figure.
- Nothing under `src/` was modified. The only files I created are
  `sim/reports/archive_backfill.md` and `sim/reports/archive_backfill.json`; the other untracked
  paths in `git status` (`sim/analyze_miss_taxonomy.py`, `sim/analyze_blocked_cost.py`,
  `sim/make_unknown_maps.py`, `sim/maps_unknown.json`) belong to sibling agents and were left
  alone. No `git add`/`commit`/`pull`/`push` was run.
- `sim/probe/archive_logs.py`, `sim/analyze_gold_delta.py`, `sim/engine.py`, `sim/scenario.py`,
  `sim/abi.py`, `sim/maps.json` were **read and executed, never edited**. The archiver needed no
  change, so the 112 pre-existing manifest entries keep their schema *and* their exact values.
- Helper scripts for this round live in `/tmp` (`scan_corpus.py`, `family_channel.py`,
  `pooled_extended.py`, `build_backfill_report.py`) and were deliberately not added to `sim/`;
  everything they compute is reproducible from the two unmodified tools above.
- `sim/analyze_miss_taxonomy.py`, `sim/analyze_blocked_cost.py` and their reports were not created,
  read-locked or touched. Scans were kept to single passes with results cached to `/tmp`.
