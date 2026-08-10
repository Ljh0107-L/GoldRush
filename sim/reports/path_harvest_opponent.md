# Path harvesting vs point harvesting: opponent log measurement

Generated 2026-08-10T06:15:24.848214+00:00 by `sim/analyze_opponent_paths.py`
(scope=`all`, 270 games, 133093 rounds parsed, 18.2s runtime).

**Question.** Do Tiuntled-1 (T-1) and Tundra-wawa collect gold from MULTIPLE cells along their three-step path each round (path harvesting), or do they walk to ONE high-value cell (point harvesting)?

## 0. Sample and data channels

| team | games | rounds | high-vision (our r>=3) games | maps |
|---|---|---|---|---|
| Tiuntled-1 | 158 | 77605 | 9 | 4d6ac13d, 80c59521, df58e370 |
| Tundra-wawa | 112 | 55488 | 5 | 4d6ac13d, 80c59521, df58e370 |

Forfeit rows skipped: 7, in games ['game_170565', 'game_170567', 'game_170860', 'game_170986', 'game_171022', 'game_171223', 'game_171272']. Forfeit rows carry no `start`/`end` and are dropped; because the fog-free delta is differenced WITHIN a row (`end[r] - start[r]`, and `start[r].gold == end[r-1].gold` is verified), no delta ever spans a gap.

Machinery validation (`validate` sub-command, 4 games):
- `effective_action_replay`: PASS (unit_rounds=4000, mismatches=0)
- `npc_reverse_replay`: PASS (npc_rounds=3411, mismatches=0)
- `start_grid_fog_mask_is_radius2_union_of_our_start_positions`: PASS (cell_observations=46240, mismatches=0)
- `pickup` completeness by logged action-list length: {"0|delta==pickup": 43, "0|delta>pickup(truncated)": 62, "1|delta==pickup": 42, "1|delta>pickup(truncated)": 50, "2|delta<pickup(burn)": 1, "2|delta==pickup": 34, "2|delta>pickup(truncated)": 67, "3|delta<pickup(burn)": 9, "3|delta==pickup": 1999, "3|delta>pickup(truncated)": 33, "absent|no_pickup_field": 1660}
  A `delta>pickup` row proves the logged `pickup` is fog-truncated. It happens for every short action list, and for a small residue of length-3 lists, so all cell-level work below is restricted to length-3 records with `pickup >= delta`.
- our own pickup reconstruction (end-to-end machinery check on the fully visible side): {"match": 3955, "over": 10, "path_partly_fogged": 35}

`start[r].actions` vs `end[r-1].actions`: {"both_absent": 1658, "identical": 1962, "only_end_prev": 372} -- `end[r]` is a strict superset (`only_start_next` = 0), so the 'take the union of both phase views' trick recovers nothing here and `end[r].actions` is used alone.

Our-side entity identification (the archive's 'ours' side is ~100 different builds, so no archive-wide 'ours' average is used):
- `frTu1*` (frTu1a, frTu1b, frTu1c, frTu1d, frTu1e, frTu1f): observed net diffs [-530, 248, -344, -123, -290, -276] vs `src/CHANGELOG.md` [-530, 248, -344, -123, -290, -276] -> **PASS(exact match)**
- `frTu2*` (frTu2a, frTu2b, frTu2c, frTu2d, frTu2e, frTu2f): observed net diffs [54, 192, -63, 122, 441, -445] vs `src/CHANGELOG.md` [54, 192, -63, 122, 441, -445] -> **PASS(exact match)**
- `frTu3*` (frTu3a, frTu3b, frTu3c, frTu3d, frTu3e, frTu3f): observed net diffs [157, 514, -97, 577, 31, 293] vs `src/CHANGELOG.md` [157, 514, -97, 577, 31, 293] -> **PASS(exact match)**
- `t1f1/2/3*`: NAME-CONVENTION ONLY: no per-game CHANGELOG anchor exists for the T-1 three-map frozen replay, so these 18 games are reported separately (entity ours.f18064c_named) and never merged into the validated set without being labelled.

## 1. Channel A -- fog-free per-unit held-gold delta (headline, unbiased)

Per-unit `gold` is logged for 100% of unit-observations in both phases for both players, so this table is a complete census of the listed unit-rounds. No fog selection whatsoever.

| entity | unit-rounds | mean delta | delta>0 (hit%) | yield per hit | delta<0 | >=6 | >=8 | >=10 | >=12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 155210 | +1.844 | 43.6% | 4.59 | 0.45% | 13.69% | 7.12% | 4.03% | 3.09% |
| T-1 (manifest archive only) | 66210 | +2.061 | 47.8% | 4.65 | 0.40% | 15.57% | 8.18% | 4.64% | 3.44% |
| T-1 (high-vision probe games) | 9000 | +1.771 | 40.0% | 4.82 | 0.33% | 13.66% | 7.20% | 4.21% | 3.10% |
| T-1 (ordinary games) | 146210 | +1.848 | 43.8% | 4.58 | 0.45% | 13.69% | 7.12% | 4.02% | 3.09% |
| Tundra (all games) | 110976 | +1.870 | 49.8% | 3.96 | 0.32% | 13.72% | 6.39% | 2.77% | 1.84% |
| Tundra (manifest archive only) | 41976 | +2.182 | 55.5% | 4.09 | 0.28% | 16.22% | 7.57% | 3.23% | 2.10% |
| Tundra (high-vision probe games) | 5000 | +2.655 | 63.6% | 4.34 | 0.36% | 20.28% | 9.28% | 4.12% | 2.68% |
| Tundra (ordinary games) | 105976 | +1.833 | 49.2% | 3.94 | 0.32% | 13.41% | 6.25% | 2.70% | 1.80% |
| T-1 (only the 18 f18064c games) | 18000 | +1.635 | 40.5% | 4.49 | 0.53% | 12.13% | 6.20% | 3.59% | 2.85% |
| Tundra (only the 18 f18064c games) | 18000 | +1.472 | 41.6% | 3.90 | 0.43% | 11.04% | 5.12% | 2.35% | 1.58% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 18000 | +1.500 | 35.0% | 4.69 | 0.71% | 13.32% | 6.42% | 1.76% | 0.53% |
| OURS f18064c (t1f*, name-convention) | 18000 | +1.476 | 34.5% | 4.65 | 0.67% | 12.63% | 6.49% | 1.84% | 0.51% |
| OURS f18064c (both families, 36 games) | 36000 | +1.488 | 34.7% | 4.67 | 0.69% | 12.97% | 6.46% | 1.80% | 0.52% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 16057 | +1.244 | 32.4% | 4.47 | 0.90% | 11.52% | 5.70% | 1.46% | 0.24% |
| OURS archive mixture (~100 builds; NOT a baseline) | 108186 | +0.890 | 23.8% | 4.22 | 0.99% | 7.05% | 3.70% | 1.43% | 0.75% |

### 1a. Per-battlefield reconciliation against the orchestrator's independent run

Same 36 f18064c games (`frTu1/2/3*` vs Tundra, `t1f1/2/3*` vs T-1), 6 games per battlefield. `hit%` = share of unit-rounds with delta>0, `yield/hit` = mean delta among those. The orchestrator differenced consecutive END phases (n=5,988 per battlefield, round 1 unavailable); this script differences WITHIN a row (`end[r]-start[r]`, n=6,000), which additionally includes round 1. That is the entire methodological difference and it is worth <=0.05 gold/unit-round.

| battlefield | side | unit-rounds | mean delta | hit% | yield/hit | >=8% |
|---|---|---:|---:|---:|---:|---:|
| Tundra-wawa 4d6ac13d | ours f18064c | 6000 | +1.602 | 36.1% | 4.73 | 6.87% |
| Tundra-wawa 4d6ac13d | Tundra-wawa | 6000 | +1.821 | 50.5% | 3.83 | 5.98% |
| Tundra-wawa 80c59521 | ours f18064c | 6000 | +1.889 | 44.1% | 4.71 | 8.18% |
| Tundra-wawa 80c59521 | Tundra-wawa | 6000 | +1.835 | 54.3% | 3.93 | 7.10% |
| Tundra-wawa df58e370 | ours f18064c | 6000 | +1.008 | 24.7% | 4.60 | 4.20% |
| Tundra-wawa df58e370 | Tundra-wawa | 6000 | +0.759 | 20.0% | 3.98 | 2.28% |
| Tiuntled-1 4d6ac13d | ours f18064c | 6000 | +1.561 | 35.8% | 4.63 | 6.80% |
| Tiuntled-1 4d6ac13d | Tiuntled-1 | 6000 | +1.835 | 45.4% | 4.41 | 6.85% |
| Tiuntled-1 80c59521 | ours f18064c | 6000 | +1.917 | 44.4% | 4.70 | 8.30% |
| Tiuntled-1 80c59521 | Tiuntled-1 | 6000 | +2.020 | 49.9% | 4.59 | 8.22% |
| Tiuntled-1 df58e370 | ours f18064c | 6000 | +0.949 | 23.2% | 4.56 | 4.38% |
| Tiuntled-1 df58e370 | Tiuntled-1 | 6000 | +1.050 | 26.4% | 4.43 | 3.53% |

- T-1 (all games): mean +1.844/unit-round -> **+3.688 gold/round at player level** (x2 units); delta>=8 = 7.12% (95% CI 7.00%-7.25%, n=155210).
- T-1 (manifest archive only): mean +2.061/unit-round -> **+4.122 gold/round at player level** (x2 units); delta>=8 = 8.18% (95% CI 7.97%-8.39%, n=66210).
- Tundra (all games): mean +1.870/unit-round -> **+3.740 gold/round at player level** (x2 units); delta>=8 = 6.39% (95% CI 6.25%-6.54%, n=110976).
- Tundra (manifest archive only): mean +2.182/unit-round -> **+4.364 gold/round at player level** (x2 units); delta>=8 = 7.57% (95% CI 7.32%-7.83%, n=41976).

Losses are rare and small: T-1 delta<0 in 0.45% (mean -35.69 when negative); Tundra delta<0 in 0.32% (mean -31.96 when negative)

### 1b. Player-level (per-round) alignment with the published burst-round rates

`sim/OPPONENTS.md` publishes PER-ROUND (both units summed) figures: burst-round rate (delta-held >= 6) of 32.5% for T-1, 34.4% for Tundra, 15.2% for us, and mean delta-held/round of 4.038 / 4.302 / 1.868. Those are a different口径 from the per-unit table above, so both are given here. Note the earlier `>=6` figures were framed as PICKUP >= 6 in some inherited code; pickup and delta-held differ by burn (bombs and NPC trample), and the divergence is measured below.

| entity | rounds | mean delta/round | delta>0 | >=6 (burst-round rate) | >=8 | >=12 |
|---|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 77605 | +3.688 | 66.2% | 29.00% | 18.54% | 8.51% |
| T-1 (manifest archive only) | 33105 | +4.122 | 71.3% | 33.00% | 21.57% | 9.71% |
| T-1 (high-vision probe games) | 4500 | +3.541 | 58.9% | 28.56% | 19.27% | 8.51% |
| T-1 (ordinary games) | 73105 | +3.697 | 66.7% | 29.02% | 18.50% | 8.52% |
| Tundra (all games) | 55488 | +3.740 | 72.4% | 29.69% | 18.66% | 6.71% |
| Tundra (manifest archive only) | 20988 | +4.364 | 78.4% | 34.90% | 22.44% | 8.20% |
| Tundra (high-vision probe games) | 2500 | +5.310 | 85.4% | 42.60% | 28.72% | 11.36% |
| Tundra (ordinary games) | 52988 | +3.666 | 71.8% | 29.08% | 18.19% | 6.49% |
| T-1 (only the 18 f18064c games) | 9000 | +3.270 | 62.4% | 25.54% | 15.92% | 7.70% |
| Tundra (only the 18 f18064c games) | 9000 | +2.944 | 62.6% | 23.93% | 14.70% | 5.42% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 9000 | +2.999 | 55.6% | 27.14% | 16.84% | 4.42% |
| OURS f18064c (t1f*, name-convention) | 9000 | +2.951 | 55.2% | 25.98% | 16.41% | 4.56% |
| OURS f18064c (both families, 36 games) | 18000 | +2.975 | 55.4% | 26.56% | 16.63% | 4.49% |
| OURS archive mixture (~100 builds; NOT a baseline) | 54093 | +1.780 | 39.7% | 14.67% | 8.85% | 2.67% |

The manifest-archive rows are the ones directly comparable with the published numbers, and they land on them: T-1 burst-round rate 33.0% against the published 32.5% and mean +4.122 against 4.038; Tundra 34.9% against 34.4% and +4.364 against 4.302. The small residue is the forfeit-game handling (this script keeps the completed rounds of the 7 aborted games and includes round 1).

Pickup vs delta-held divergence (visible subset, trustworthy length-3 records only): held-gold delta equals logged pickup in the overwhelming majority of rounds; the two diverge only where a bomb or a 3-NPC trample burned part of the purse. Per-unit delta<0 rates of 0.45% (T-1) and 0.32% (Tundra) bound the total size of that channel.

### 1c. The >=8 structural bound and its high-value-cell confound

A single step onto a cell of value `v` pays `ceil(0.65v)`; ordinary cells cap at `v=10` -> 7 gold. So a per-unit delta of >=8 in one round needs either two or more paying cells, or one cell with `v>=11` (spawn stacking, or one of the 20 outer-ring token-2 hotspots). Visible-grid census of cell values:

| stratum | visible cell-obs | gold cell-obs | share of gold cells with v>=11 | of those, on token-2 hotspot |
|---|---:|---:|---:|---:|
| Tiuntled-1|all | 3610184 | 258840 | 7.93% | 5110 |
| Tundra-wawa|all | 2552763 | 178159 | 7.59% | 3202 |

This census is observation-weighted, so it OVER-states `v>=11` prevalence (an uneaten fat cell is re-counted every round it stays visible). It is an upper bound on the single-high-value-cell explanation, not an estimate. Section 3 settles the question directly instead.

## 2. Channel B -- trajectory channel (visible subset; bias measured)

| entity | unit-rounds | len-3 action list | reconstructable | path cells all known | clean (recon==logged pickup) | recon match rate |
|---|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 155210 | 39.0% | 39.0% | 45779 | 44318 | 90.7% (n=59166) |
| T-1 (manifest archive only) | 66210 | 40.2% | 40.2% | 19872 | 19251 | 89.9% (n=26012) |
| T-1 (high-vision probe games) | 9000 | 77.5% | 77.5% | 6208 | 6092 | 95.3% (n=6857) |
| T-1 (ordinary games) | 146210 | 36.6% | 36.6% | 39571 | 38226 | 90.1% (n=52309) |
| Tundra (all games) | 110976 | 46.9% | 46.9% | 35075 | 33438 | 87.8% (n=50640) |
| Tundra (manifest archive only) | 41976 | 42.1% | 42.1% | 12316 | 11718 | 86.5% (n=17124) |
| Tundra (high-vision probe games) | 5000 | 76.8% | 76.8% | 3378 | 3247 | 92.7% (n=3709) |
| Tundra (ordinary games) | 105976 | 45.5% | 45.5% | 31697 | 30191 | 87.4% (n=46931) |
| T-1 (only the 18 f18064c games) | 18000 | 39.2% | 39.2% | 5493 | 5300 | 91.9% (n=6891) |
| Tundra (only the 18 f18064c games) | 18000 | 48.3% | 48.3% | 5983 | 5708 | 89.3% (n=8477) |
| OURS f18064c (frTu*, CHANGELOG-validated) | 18000 | 100.0% | 100.0% | 17698 | 17671 | 99.7% (n=18000) |
| OURS f18064c (t1f*, name-convention) | 18000 | 100.0% | 100.0% | 17733 | 17715 | 99.7% (n=18000) |
| OURS f18064c (both families, 36 games) | 36000 | 100.0% | 100.0% | 35431 | 35386 | 99.7% (n=36000) |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 16057 | 100.0% | 100.0% | 15822 | 15809 | 99.8% (n=16057) |
| OURS archive mixture (~100 builds; NOT a baseline) | 108186 | 100.0% | 100.0% | 105636 | 105425 | 99.7% (n=108186) |

### 2a. Selection bias, quantified on the fog-free channel

Because the fog-free delta exists for EVERY unit-round, the bias of the visible subset can be measured directly rather than merely acknowledged: compare the delta distribution of reconstructable vs non-reconstructable unit-rounds of the same games.

| entity | subset | unit-rounds | mean delta | delta>=6 | delta>=8 |
|---|---|---:|---:|---:|---:|
| T-1 (all games) | reconstructable | 60466 | +1.260 | 12.08% | 5.53% |
| T-1 (all games) | fogged-out | 94744 | +2.217 | 14.72% | 8.14% |
| T-1 (manifest archive only) | reconstructable | 26602 | +1.460 | 13.50% | 6.42% |
| T-1 (manifest archive only) | fogged-out | 39608 | +2.465 | 16.97% | 9.36% |
| T-1 (high-vision probe games) | reconstructable | 6971 | +1.485 | 11.99% | 5.98% |
| T-1 (high-vision probe games) | fogged-out | 2029 | +2.753 | 19.37% | 11.38% |
| T-1 (ordinary games) | reconstructable | 53495 | +1.230 | 12.09% | 5.47% |
| T-1 (ordinary games) | fogged-out | 92715 | +2.205 | 14.62% | 8.07% |
| Tundra (all games) | reconstructable | 52016 | +1.507 | 11.88% | 5.15% |
| Tundra (all games) | fogged-out | 58960 | +2.191 | 15.34% | 7.48% |
| Tundra (manifest archive only) | reconstructable | 17655 | +1.854 | 14.61% | 6.45% |
| Tundra (manifest archive only) | fogged-out | 24321 | +2.420 | 17.38% | 8.38% |
| Tundra (high-vision probe games) | reconstructable | 3839 | +2.514 | 19.43% | 8.21% |
| Tundra (high-vision probe games) | fogged-out | 1161 | +3.120 | 23.08% | 12.83% |
| Tundra (ordinary games) | reconstructable | 48177 | +1.426 | 11.28% | 4.91% |
| Tundra (ordinary games) | fogged-out | 57799 | +2.172 | 15.18% | 7.37% |
| T-1 (only the 18 f18064c games) | reconstructable | 7050 | +0.985 | 10.26% | 4.44% |
| T-1 (only the 18 f18064c games) | fogged-out | 10950 | +2.054 | 13.33% | 7.33% |
| Tundra (only the 18 f18064c games) | reconstructable | 8691 | +1.211 | 10.08% | 4.46% |
| Tundra (only the 18 f18064c games) | fogged-out | 9309 | +1.716 | 11.95% | 5.74% |
| OURS f18064c (frTu*, CHANGELOG-validated) | reconstructable | 18000 | +1.500 | 13.32% | 6.42% |
| OURS f18064c (t1f*, name-convention) | reconstructable | 18000 | +1.476 | 12.63% | 6.49% |
| OURS f18064c (both families, 36 games) | reconstructable | 36000 | +1.488 | 12.97% | 6.46% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | reconstructable | 16057 | +1.244 | 11.52% | 5.70% |
| OURS archive mixture (~100 builds; NOT a baseline) | reconstructable | 108186 | +0.890 | 7.05% | 3.70% |

### 2b. Path shape and paying-cell histogram (clean subset only)

| entity | clean n | moved steps 0/1/2/3 | distinct cells 0/1/2/3 | PAYING cells 0/1/2/3 | mean paying cells | mean pickup | top-cell share |
|---|---:|---|---|---|---:|---:|---:|
| T-1 (all games) | 44318 | 11298/6351/10130/16539 | 11298/6351/10130/16539 | 68.9%/25.9%/4.9%/0.4% | 0.368 | 1.276 | 94.3% |
| T-1 (manifest archive only) | 19251 | 5391/2461/3887/7512 | 5391/2461/3887/7512 | 66.4%/27.5%/5.6%/0.5% | 0.402 | 1.436 | 93.8% |
| T-1 (high-vision probe games) | 6092 | 2212/714/794/2372 | 2212/714/794/2372 | 68.7%/25.0%/5.7%/0.6% | 0.382 | 1.434 | 93.2% |
| T-1 (ordinary games) | 38226 | 9086/5637/9336/14167 | 9086/5637/9336/14167 | 68.9%/26.0%/4.8%/0.3% | 0.366 | 1.251 | 94.4% |
| Tundra (all games) | 33438 | 2860/1829/5607/23142 | 2860/1829/5607/23142 | 61.4%/33.0%/5.3%/0.3% | 0.446 | 1.400 | 95.0% |
| Tundra (manifest archive only) | 11718 | 1182/434/1318/8784 | 1182/434/1318/8784 | 56.3%/36.8%/6.5%/0.5% | 0.512 | 1.670 | 94.6% |
| Tundra (high-vision probe games) | 3247 | 43/24/217/2963 | 43/24/217/2963 | 40.2%/48.4%/10.5%/0.8% | 0.719 | 2.392 | 93.6% |
| Tundra (ordinary games) | 30191 | 2817/1805/5390/20179 | 2817/1805/5390/20179 | 63.6%/31.4%/4.7%/0.3% | 0.417 | 1.294 | 95.3% |
| T-1 (only the 18 f18064c games) | 5300 | 1216/807/1372/1905 | 1216/807/1372/1905 | 70.3%/24.6%/4.8%/0.3% | 0.351 | 1.134 | 94.0% |
| Tundra (only the 18 f18064c games) | 5708 | 335/457/1223/3693 | 335/457/1223/3693 | 65.4%/29.8%/4.5%/0.2% | 0.395 | 1.201 | 95.3% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 17671 | 736/4463/4169/8303 | 736/4463/10528/1944 | 64.6%/33.4%/2.0%/0.0% | 0.374 | 1.669 | 98.7% |
| OURS f18064c (t1f*, name-convention) | 17715 | 951/4503/4213/8048 | 951/4503/10290/1971 | 65.2%/32.3%/2.5%/0.0% | 0.373 | 1.625 | 98.3% |
| OURS f18064c (both families, 36 games) | 35386 | 1687/8966/8382/16351 | 1687/8966/20818/3915 | 64.9%/32.8%/2.2%/0.0% | 0.374 | 1.647 | 98.5% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 15809 | 1189/3939/3803/6878 | 1189/3939/9163/1518 | 67.2%/30.7%/2.1%/0.0% | 0.349 | 1.474 | 98.5% |
| OURS archive mixture (~100 builds; NOT a baseline) | 105425 | 10765/25654/12414/56592 | 10765/25654/37817/31189 | 75.8%/22.2%/1.9%/0.1% | 0.262 | 1.027 | 97.5% |

| entity | clean n | straight | turn | reversal | 3 effective-stays | revisit rate | 3-move rounds | of those, folded to <3 distinct cells |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 44318 | 29.7% | 44.8% | 0.0% | 25.5% | 0.0% | 16539 | 0.0% |
| T-1 (manifest archive only) | 19251 | 26.3% | 45.7% | 0.0% | 28.0% | 0.0% | 7512 | 0.0% |
| T-1 (high-vision probe games) | 6092 | 21.2% | 42.5% | 0.0% | 36.3% | 0.0% | 2372 | 0.0% |
| T-1 (ordinary games) | 38226 | 31.0% | 45.2% | 0.0% | 23.8% | 0.0% | 14167 | 0.0% |
| Tundra (all games) | 33438 | 13.5% | 78.0% | 0.0% | 8.6% | 0.0% | 23142 | 0.0% |
| Tundra (manifest archive only) | 11718 | 8.4% | 81.5% | 0.0% | 10.1% | 0.0% | 8784 | 0.0% |
| Tundra (high-vision probe games) | 3247 | 4.0% | 94.6% | 0.0% | 1.3% | 0.0% | 2963 | 0.0% |
| Tundra (ordinary games) | 30191 | 14.5% | 76.2% | 0.0% | 9.3% | 0.0% | 20179 | 0.0% |
| T-1 (only the 18 f18064c games) | 5300 | 32.2% | 44.9% | 0.0% | 22.9% | 0.0% | 1905 | 0.0% |
| Tundra (only the 18 f18064c games) | 5708 | 21.2% | 72.9% | 0.0% | 5.9% | 0.0% | 3693 | 0.0% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 17671 | 26.4% | 12.4% | 57.0% | 4.2% | 57.0% | 8303 | 76.6% |
| OURS f18064c (t1f*, name-convention) | 17715 | 26.4% | 12.2% | 56.0% | 5.4% | 56.0% | 8048 | 75.5% |
| OURS f18064c (both families, 36 games) | 35386 | 26.4% | 12.3% | 56.5% | 4.8% | 56.5% | 16351 | 76.1% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 15809 | 26.2% | 11.6% | 54.6% | 7.5% | 54.6% | 6878 | 77.9% |
| OURS archive mixture (~100 builds; NOT a baseline) | 105425 | 27.0% | 22.9% | 39.9% | 10.2% | 35.7% | 56592 | 44.9% |

`3 effective-stays` conflates a deliberate stay with a move blocked by a unit, a wall or the board edge -- the log only records EFFECTIVE actions, so the two are indistinguishable. It is inflated for both sides in this subset precisely because the subset requires the two players to be close together.

**The single hardest structural difference in the whole study is in the two columns above:** both opponents produce ZERO direction reversals and ZERO within-round revisits, over every clean unit-round measured; our frozen build reverses in more than half of them and folds three quarters of its 3-move rounds onto only two distinct cells. Our side's trajectory sample is essentially complete (our own units are always visible to us), so that half of the comparison is not fog-limited.

| entity | clean n | mean moved steps | mean distinct cells | distinct per move | wasted-step rate | gold per moved step | gold per paying cell |
|---|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 44318 | 1.720 | 1.720 | 1.000 | 0.0% | 0.742 | 3.47 |
| T-1 (manifest archive only) | 19251 | 1.702 | 1.702 | 1.000 | 0.0% | 0.844 | 3.57 |
| T-1 (high-vision probe games) | 6092 | 1.546 | 1.546 | 1.000 | 0.0% | 0.927 | 3.75 |
| T-1 (ordinary games) | 38226 | 1.748 | 1.748 | 1.000 | 0.0% | 0.716 | 3.42 |
| Tundra (all games) | 33438 | 2.466 | 2.466 | 1.000 | 0.0% | 0.568 | 3.14 |
| Tundra (manifest archive only) | 11718 | 2.511 | 2.511 | 1.000 | 0.0% | 0.665 | 3.26 |
| Tundra (high-vision probe games) | 3247 | 2.879 | 2.879 | 1.000 | 0.0% | 0.831 | 3.33 |
| Tundra (ordinary games) | 30191 | 2.422 | 2.422 | 1.000 | 0.0% | 0.534 | 3.11 |
| T-1 (only the 18 f18064c games) | 5300 | 1.748 | 1.748 | 1.000 | 0.0% | 0.649 | 3.23 |
| Tundra (only the 18 f18064c games) | 5708 | 2.450 | 2.450 | 1.000 | 0.0% | 0.490 | 3.04 |
| OURS f18064c (frTu*, CHANGELOG-validated) | 17671 | 2.134 | 1.774 | 0.831 | 16.9% | 0.782 | 4.47 |
| OURS f18064c (t1f*, name-convention) | 17715 | 2.093 | 1.750 | 0.836 | 16.4% | 0.776 | 4.35 |
| OURS f18064c (both families, 36 games) | 35386 | 2.113 | 1.762 | 0.834 | 16.6% | 0.779 | 4.41 |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 15809 | 2.035 | 1.696 | 0.833 | 16.7% | 0.724 | 4.22 |
| OURS archive mixture (~100 builds; NOT a baseline) | 105425 | 2.089 | 1.848 | 0.885 | 11.5% | 0.492 | 3.92 |

### 2c. Availability vs conversion: why does the opponent score more often?

Measured identically for both sides. A unit-round enters only if the entire Manhattan<=2 diamond around the pre-round position is non-FOG in `start.grid` (so availability is never partially blind) and the logged pickup is trustworthy. `supply` = number of start-of-round gold cells at Manhattan 1..2, i.e. gold that is comfortably inside the 3-step budget. `hit` = logged pickup > 0.

| entity | n | mean supply within 2 | mean adjacent gold | overall hit rate | hit rate given supply=0 | =1 | =2 | >=3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 10813 | 1.001 | 0.231 | 24.4% | 9.2% | 35.9% | 36.5% | 30.6% |
| T-1 (manifest archive only) | 6510 | 1.114 | 0.201 | 24.6% | 10.5% | 37.7% | 33.9% | 23.7% |
| T-1 (high-vision probe games) | 4616 | 0.988 | 0.196 | 24.9% | 9.9% | 38.3% | 35.8% | 30.4% |
| T-1 (ordinary games) | 6197 | 1.010 | 0.257 | 24.0% | 8.6% | 34.1% | 37.1% | 30.7% |
| Tundra (all games) | 4907 | 1.010 | 0.251 | 41.4% | 20.2% | 50.7% | 56.9% | 62.2% |
| Tundra (manifest archive only) | 2788 | 0.987 | 0.244 | 46.9% | 23.5% | 62.4% | 63.0% | 63.1% |
| Tundra (high-vision probe games) | 1832 | 0.896 | 0.281 | 58.6% | 28.5% | 80.5% | 83.4% | 90.8% |
| Tundra (ordinary games) | 3075 | 1.078 | 0.233 | 31.2% | 13.7% | 35.9% | 43.9% | 47.7% |
| T-1 (only the 18 f18064c games) | 837 | 0.843 | 0.323 | 23.7% | 8.4% | 31.6% | 40.0% | 47.8% |
| Tundra (only the 18 f18064c games) | 661 | 0.991 | 0.331 | 39.0% | 19.3% | 41.9% | 58.8% | 70.6% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 18000 | 0.867 | 0.337 | 35.1% | 12.6% | 45.8% | 58.1% | 71.9% |
| OURS f18064c (t1f*, name-convention) | 18000 | 0.939 | 0.378 | 34.6% | 12.0% | 42.8% | 57.3% | 65.6% |
| OURS f18064c (both families, 36 games) | 36000 | 0.903 | 0.357 | 34.9% | 12.3% | 44.3% | 57.7% | 68.5% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 16057 | 0.962 | 0.375 | 32.6% | 11.7% | 39.3% | 51.2% | 62.6% |
| OURS archive mixture (~100 builds; NOT a baseline) | 108186 | 0.892 | 0.326 | 24.0% | 7.7% | 32.0% | 42.5% | 44.6% |

Supply distribution (share of the same unit-rounds by number of gold cells within 2 steps):

| entity | n | supply=0 | 1 | 2 | 3 | >=4 |
|---|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 10813 | 41.5% | 30.7% | 18.0% | 6.8% | 3.1% |
| T-1 (manifest archive only) | 6510 | 38.9% | 29.0% | 19.4% | 8.3% | 4.3% |
| T-1 (high-vision probe games) | 4616 | 42.9% | 29.6% | 18.0% | 5.9% | 3.5% |
| T-1 (ordinary games) | 6197 | 40.5% | 31.4% | 17.9% | 7.4% | 2.7% |
| Tundra (all games) | 4907 | 37.7% | 34.3% | 19.4% | 6.9% | 1.7% |
| Tundra (manifest archive only) | 2788 | 40.3% | 31.9% | 18.9% | 7.2% | 1.7% |
| Tundra (high-vision probe games) | 1832 | 44.7% | 30.5% | 17.1% | 6.4% | 1.4% |
| Tundra (ordinary games) | 3075 | 33.5% | 36.6% | 20.7% | 7.3% | 1.9% |
| T-1 (only the 18 f18064c games) | 837 | 44.0% | 34.4% | 16.1% | 4.4% | 1.1% |
| Tundra (only the 18 f18064c games) | 661 | 36.0% | 38.3% | 18.0% | 6.4% | 1.4% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 18000 | 42.9% | 35.2% | 15.7% | 4.9% | 1.3% |
| OURS f18064c (t1f*, name-convention) | 18000 | 40.0% | 35.5% | 17.0% | 5.8% | 1.7% |
| OURS f18064c (both families, 36 games) | 36000 | 41.5% | 35.4% | 16.3% | 5.3% | 1.5% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 16057 | 38.4% | 36.5% | 17.7% | 5.8% | 1.7% |
| OURS archive mixture (~100 builds; NOT a baseline) | 108186 | 43.8% | 32.9% | 15.7% | 5.8% | 1.8% |

## 3. Channel C -- the key judgment: one fat cell, or a chain?

For every clean burst unit-round the reconstruction says exactly which cells paid and how much. `single` = one distinct paying cell; `chained` = 2 or 3.

### burst definition `delta_ge6`

| entity | clean burst n | single cell | of which v>=11 | of which token-2 hotspot | chained (2 cells) | chained (3 cells) | chained total | top-cell share of burst gold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 4181 | 68.3% | 934 | 131 | 1198 | 127 | 31.7% | 87.9% |
| T-1 (manifest archive only) | 2028 | 67.3% | 462 | 89 | 589 | 74 | 32.7% | 87.3% |
| T-1 (high-vision probe games) | 627 | 64.8% | 145 | 64 | 192 | 29 | 35.2% | 86.4% |
| T-1 (ordinary games) | 3554 | 68.9% | 789 | 67 | 1006 | 98 | 31.1% | 88.2% |
| Tundra (all games) | 3291 | 69.5% | 711 | 27 | 916 | 87 | 30.5% | 88.8% |
| Tundra (manifest archive only) | 1409 | 69.4% | 303 | 20 | 386 | 45 | 30.6% | 88.9% |
| Tundra (high-vision probe games) | 579 | 66.8% | 116 | 12 | 172 | 20 | 33.2% | 88.5% |
| Tundra (ordinary games) | 2712 | 70.1% | 595 | 15 | 744 | 67 | 29.9% | 88.9% |
| T-1 (only the 18 f18064c games) | 430 | 67.7% | 93 | 10 | 125 | 14 | 32.3% | 87.4% |
| Tundra (only the 18 f18064c games) | 483 | 68.5% | 102 | 2 | 141 | 11 | 31.5% | 88.3% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 2393 | 91.3% | 532 | 107 | 207 | 2 | 8.7% | 98.4% |
| OURS f18064c (t1f*, name-convention) | 2267 | 88.1% | 508 | 81 | 266 | 4 | 11.9% | 97.7% |
| OURS f18064c (both families, 36 games) | 4660 | 89.7% | 1040 | 188 | 473 | 6 | 10.3% | 98.1% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 1846 | 89.2% | 395 | 33 | 198 | 2 | 10.8% | 98.0% |
| OURS archive mixture (~100 builds; NOT a baseline) | 7602 | 85.5% | 1935 | 860 | 1059 | 41 | 14.5% | 95.7% |

### burst definition `delta_ge8`

| entity | clean burst n | single cell | of which v>=11 | of which token-2 hotspot | chained (2 cells) | chained (3 cells) | chained total | top-cell share of burst gold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 1876 | 49.7% | 933 | 92 | 842 | 101 | 50.3% | 82.1% |
| T-1 (manifest archive only) | 944 | 48.8% | 461 | 60 | 418 | 65 | 51.2% | 81.4% |
| T-1 (high-vision probe games) | 304 | 47.4% | 144 | 45 | 132 | 28 | 52.6% | 81.2% |
| T-1 (ordinary games) | 1572 | 50.2% | 789 | 47 | 710 | 73 | 49.8% | 82.3% |
| Tundra (all games) | 1393 | 51.0% | 711 | 22 | 618 | 64 | 49.0% | 83.0% |
| Tundra (manifest archive only) | 593 | 51.1% | 303 | 16 | 254 | 36 | 48.9% | 83.0% |
| Tundra (high-vision probe games) | 239 | 48.5% | 116 | 9 | 108 | 15 | 51.5% | 82.6% |
| Tundra (ordinary games) | 1154 | 51.6% | 595 | 13 | 510 | 49 | 48.4% | 83.1% |
| T-1 (only the 18 f18064c games) | 186 | 50.0% | 93 | 7 | 83 | 10 | 50.0% | 81.2% |
| Tundra (only the 18 f18064c games) | 208 | 49.0% | 102 | 2 | 100 | 6 | 51.0% | 82.4% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 1154 | 89.7% | 532 | 75 | 117 | 2 | 10.3% | 98.4% |
| OURS f18064c (t1f*, name-convention) | 1167 | 86.5% | 508 | 58 | 155 | 3 | 13.5% | 97.7% |
| OURS f18064c (both families, 36 games) | 2321 | 88.1% | 1040 | 133 | 272 | 5 | 11.9% | 98.0% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 913 | 87.3% | 395 | 24 | 115 | 1 | 12.7% | 97.9% |
| OURS archive mixture (~100 builds; NOT a baseline) | 3986 | 81.3% | 1932 | 636 | 713 | 32 | 18.7% | 94.7% |

### burst definition `pickup_ge6`

| entity | clean burst n | single cell | of which v>=11 | of which token-2 hotspot | chained (2 cells) | chained (3 cells) | chained total | top-cell share of burst gold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 4205 | 68.4% | 948 | 132 | 1202 | 127 | 31.6% | 88.0% |
| T-1 (manifest archive only) | 2039 | 67.3% | 468 | 90 | 592 | 74 | 32.7% | 87.3% |
| T-1 (high-vision probe games) | 629 | 64.7% | 146 | 65 | 193 | 29 | 35.3% | 86.5% |
| T-1 (ordinary games) | 3576 | 69.0% | 802 | 67 | 1009 | 98 | 31.0% | 88.2% |
| Tundra (all games) | 3299 | 69.6% | 713 | 27 | 917 | 87 | 30.4% | 88.9% |
| Tundra (manifest archive only) | 1410 | 69.4% | 303 | 20 | 386 | 45 | 30.6% | 88.9% |
| Tundra (high-vision probe games) | 579 | 66.8% | 116 | 12 | 172 | 20 | 33.2% | 88.5% |
| Tundra (ordinary games) | 2720 | 70.1% | 597 | 15 | 745 | 67 | 29.9% | 88.9% |
| T-1 (only the 18 f18064c games) | 433 | 67.9% | 95 | 10 | 125 | 14 | 32.1% | 87.5% |
| Tundra (only the 18 f18064c games) | 484 | 68.6% | 102 | 2 | 141 | 11 | 31.4% | 88.3% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 2406 | 91.3% | 537 | 108 | 207 | 2 | 8.7% | 98.4% |
| OURS f18064c (t1f*, name-convention) | 2275 | 88.1% | 510 | 81 | 266 | 4 | 11.9% | 97.7% |
| OURS f18064c (both families, 36 games) | 4681 | 89.8% | 1047 | 189 | 473 | 6 | 10.2% | 98.1% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 1858 | 89.2% | 398 | 33 | 198 | 2 | 10.8% | 98.0% |
| OURS archive mixture (~100 builds; NOT a baseline) | 7649 | 85.6% | 1945 | 867 | 1063 | 41 | 14.4% | 95.7% |

### burst definition `pickup_ge8`

| entity | clean burst n | single cell | of which v>=11 | of which token-2 hotspot | chained (2 cells) | chained (3 cells) | chained total | top-cell share of burst gold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 1895 | 50.0% | 948 | 93 | 846 | 101 | 50.0% | 82.2% |
| T-1 (manifest archive only) | 954 | 49.1% | 468 | 61 | 421 | 65 | 50.9% | 81.5% |
| T-1 (high-vision probe games) | 307 | 47.6% | 146 | 46 | 133 | 28 | 52.4% | 81.4% |
| T-1 (ordinary games) | 1588 | 50.5% | 802 | 47 | 713 | 73 | 49.5% | 82.3% |
| Tundra (all games) | 1396 | 51.1% | 713 | 22 | 619 | 64 | 48.9% | 83.0% |
| Tundra (manifest archive only) | 593 | 51.1% | 303 | 16 | 254 | 36 | 48.9% | 83.0% |
| Tundra (high-vision probe games) | 239 | 48.5% | 116 | 9 | 108 | 15 | 51.5% | 82.6% |
| Tundra (ordinary games) | 1157 | 51.6% | 597 | 13 | 511 | 49 | 48.4% | 83.1% |
| T-1 (only the 18 f18064c games) | 188 | 50.5% | 95 | 7 | 83 | 10 | 49.5% | 81.4% |
| Tundra (only the 18 f18064c games) | 208 | 49.0% | 102 | 2 | 100 | 6 | 51.0% | 82.4% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 1159 | 89.7% | 537 | 76 | 117 | 2 | 10.3% | 98.4% |
| OURS f18064c (t1f*, name-convention) | 1169 | 86.5% | 510 | 58 | 155 | 3 | 13.5% | 97.7% |
| OURS f18064c (both families, 36 games) | 2328 | 88.1% | 1047 | 134 | 272 | 5 | 11.9% | 98.0% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 916 | 87.3% | 398 | 24 | 115 | 1 | 12.7% | 98.0% |
| OURS archive mixture (~100 builds; NOT a baseline) | 4003 | 81.3% | 1945 | 642 | 717 | 32 | 18.7% | 94.7% |

- T-1, `delta>=8` clean bursts (n=1876): chained 50.3% (95% CI 48.0%-52.5%), one fat cell in one bite 49.7%, one cell bitten twice 0.0%. Single-cell base-value histogram: {"11": 653, "12": 58, "13": 38, "14": 27, "15": 22, "16": 17, "17": 16, "18": 21, "19": 5, "20": 16, "21": 18, "22": 12, "23": 3, "24": 5, "25": 4, "26": 3, "27": 7, "28": 4, "31": 1, "33": 1, "36": 1, "38": 1}
- Tundra, `delta>=8` clean bursts (n=1393): chained 49.0% (95% CI 46.3%-51.6%), one fat cell in one bite 51.0%, one cell bitten twice 0.0%. Single-cell base-value histogram: {"11": 550, "12": 53, "13": 24, "14": 16, "15": 6, "16": 12, "17": 6, "18": 9, "19": 8, "20": 4, "21": 4, "22": 7, "23": 1, "24": 3, "25": 1, "26": 3, "27": 3, "28": 1}

**Stratum agreement (probe / high-vision games vs ordinary games).** The high-vision stratum is the set of games in which OUR build spent the majority of rounds at vision radius >= 3 (the `probeobs` observation probes and a few others). Those games have far better opponent observability and are therefore the least fog-biased trajectory sample available:

| team | stratum | games | unit-rounds | reconstructable | clean n | chained share of delta>=8 bursts | paying-cells 0/1/2/3 |
|---|---|---:|---:|---:|---:|---:|---|
| Tiuntled-1 | highvis | 9 | 9000 | 77.5% | 6092 | 52.6% (n=304) | 68.7%/25.0%/5.7%/0.6% |
| Tiuntled-1 | ordinary | 149 | 146210 | 36.6% | 38226 | 49.8% (n=1572) | 68.9%/26.0%/4.8%/0.3% |
| Tundra-wawa | highvis | 5 | 5000 | 76.8% | 3247 | 51.5% (n=239) | 40.2%/48.4%/10.5%/0.8% |
| Tundra-wawa | ordinary | 107 | 105976 | 45.5% | 30191 | 48.4% (n=1154) | 63.6%/31.4%/4.7%/0.3% |

The two strata agree on the thing this study turns on -- the COMPOSITION of a burst. Chained share of `delta>=8` bursts is 52.6% (high-vision) vs 49.8% (ordinary) for T-1 and 51.5% vs 48.4% for Tundra: a <=3pp spread, and the LESS fog-biased high-vision stratum shows slightly MORE chaining, so the ordinary-game figure is if anything a mild under-estimate rather than a fog artefact. The strata do NOT agree on the FREQUENCY of paying at all: Tundra pays on at least one cell in 59.8% of clean unit-rounds in high-vision games against 36.4% in ordinary games. That is the hit-rate axis again, and it moves with how passive our own build was in that game (the high-vision games are our slow observation probes), so it is a property of the opponent we faced rather than of the measurement -- and it is the one number that must never be quoted from this channel as if it were a population value.

### 3a. Resolving the high-value-cell confound on the `>=8` bucket, symmetrically

This is the number the `>=8` structural bound needs. There are exactly THREE ways a unit can gain >=8 in one round, and the reconstruction separates them:

1. **one fat cell, one bite** -- a single step onto a cell of value >=11 (spawn stacking, or one of the 20 token-2 outer hotspots). `ceil(0.65*11)=8`.
2. **one cell, bitten twice** -- the unit steps off and back on, taking 65% of the remainder a second time. A value-10 cell yields 7 then 2 = 9 across two steps, so this reaches >=8 from an ordinary cell. It costs two of the three steps and extracts at most 90% of one cell.
3. **chained** -- 2 or 3 DISTINCT paying cells on the path. This is the path-harvesting mechanism the hypothesis predicted.

| entity | clean delta>=8 n | (1) one fat cell, one bite | (2) one cell, two bites | (3) chained 2 cells | (3) chained 3 cells | chained total | on token-2 hotspot | ring>=5 share of paying cells |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T-1 (all games) | 1876 | 933 (49.7%) | 0 (0.0%) | 842 (44.9%) | 101 (5.4%) | 50.3% | 92 | 6.2% |
| T-1 (manifest archive only) | 944 | 461 (48.8%) | 0 (0.0%) | 418 (44.3%) | 65 (6.9%) | 51.2% | 60 | 8.4% |
| T-1 (high-vision probe games) | 304 | 144 (47.4%) | 0 (0.0%) | 132 (43.4%) | 28 (9.2%) | 52.6% | 45 | 19.1% |
| T-1 (ordinary games) | 1572 | 789 (50.2%) | 0 (0.0%) | 710 (45.2%) | 73 (4.6%) | 49.8% | 47 | 3.6% |
| Tundra (all games) | 1393 | 711 (51.0%) | 0 (0.0%) | 618 (44.4%) | 64 (4.6%) | 49.0% | 22 | 2.1% |
| Tundra (manifest archive only) | 593 | 303 (51.1%) | 0 (0.0%) | 254 (42.8%) | 36 (6.1%) | 48.9% | 16 | 3.3% |
| Tundra (high-vision probe games) | 239 | 116 (48.5%) | 0 (0.0%) | 108 (45.2%) | 15 (6.3%) | 51.5% | 9 | 5.8% |
| Tundra (ordinary games) | 1154 | 595 (51.6%) | 0 (0.0%) | 510 (44.2%) | 49 (4.2%) | 48.4% | 13 | 1.3% |
| T-1 (only the 18 f18064c games) | 186 | 93 (50.0%) | 0 (0.0%) | 83 (44.6%) | 10 (5.4%) | 50.0% | 7 | 3.8% |
| Tundra (only the 18 f18064c games) | 208 | 102 (49.0%) | 0 (0.0%) | 100 (48.1%) | 6 (2.9%) | 51.0% | 2 | 2.2% |
| OURS f18064c (frTu*, CHANGELOG-validated) | 1154 | 329 (28.5%) | 706 (61.2%) | 117 (10.1%) | 2 (0.2%) | 10.3% | 75 | 8.1% |
| OURS f18064c (t1f*, name-convention) | 1167 | 296 (25.4%) | 713 (61.1%) | 155 (13.3%) | 3 (0.3%) | 13.5% | 58 | 6.2% |
| OURS f18064c (both families, 36 games) | 2321 | 625 (26.9%) | 1419 (61.1%) | 272 (11.7%) | 5 (0.2%) | 11.9% | 133 | 7.1% |
| OURS f18064c restricted to unit-rounds within Chebyshev 2 of an opponent unit (mirror of their observability condition) | 913 | 236 (25.8%) | 561 (61.4%) | 115 (12.6%) | 1 (0.1%) | 12.7% | 24 | 2.6% |
| OURS archive mixture (~100 builds; NOT a baseline) | 3986 | 1249 (31.3%) | 1992 (50.0%) | 713 (17.9%) | 32 (0.8%) | 18.7% | 636 | 21.1% |

So the `>=8` signal is NOT mostly one fat cell for the opponents (about half of it is genuine chaining) and it IS mostly one cell for us -- but our single-cell half is itself split between one fat cell and the same cell bitten twice, and the double-bite mode consumes two of our three steps to extract at most 90% of a single cell where a chain would have extracted 65% of two cells.

Worked examples of `delta>=8` clean bursts (cell_takes = gold taken per distinct cell, base_values = the cell values before the step):

- T-1 game_158872 r99 u0: delta=10 pickup=10 paying_cells=2 takes=[8, 2, 0] base=[0, 11, 2] shape=turn
- T-1 game_158872 r154 u0: delta=12 pickup=12 paying_cells=2 takes=[6, 6, 0] base=[9, 0, 8] shape=turn
- T-1 game_158872 r244 u1: delta=14 pickup=14 paying_cells=3 takes=[6, 6, 2] base=[8, 8, 2] shape=turn
- T-1 game_158872 r253 u0: delta=8 pickup=8 paying_cells=1 takes=[8, 0, 0] base=[0, 11, 0] shape=turn
- T-1 game_158872 r297 u0: delta=11 pickup=11 paying_cells=2 takes=[6, 5, 0] base=[7, 9, 0] shape=turn
- Tundra game_163068 r22 u1: delta=10 pickup=10 paying_cells=3 takes=[6, 2, 2] base=[3, 8, 3] shape=turn
- Tundra game_163068 r105 u0: delta=24 pickup=24 paying_cells=3 takes=[11, 8, 5] base=[12, 7, 16] shape=turn
- Tundra game_163068 r122 u1: delta=8 pickup=8 paying_cells=3 takes=[4, 2, 2] base=[3, 2, 6] shape=turn
- Tundra game_163068 r196 u1: delta=8 pickup=8 paying_cells=1 takes=[8, 0, 0] base=[11, 0, 0] shape=turn
- Tundra game_163068 r201 u1: delta=8 pickup=8 paying_cells=2 takes=[7, 1, 0] base=[1, 10, 0] shape=turn
- OURS f18064c game_175967 r16 u0: delta=8 pickup=8 paying_cells=1 takes=[8, 0] base=[0, 11] shape=reversal
- OURS f18064c game_175967 r36 u1: delta=8 pickup=8 paying_cells=1 takes=[8, 0] base=[8, 0] shape=reversal
- OURS f18064c game_175967 r42 u1: delta=8 pickup=8 paying_cells=1 takes=[8, 0] base=[8, 0] shape=reversal
- OURS f18064c game_175967 r65 u1: delta=8 pickup=8 paying_cells=1 takes=[8, 0] base=[9, 0] shape=reversal
- OURS f18064c game_175967 r66 u0: delta=12 pickup=12 paying_cells=1 takes=[12, 0] base=[13, 0] shape=reversal

## 4. Bias inventory (direction stated for each)

1. **Fog selection (Channel B/C only, direction measured in 2a).** Trajectory statistics only exist where the opponent unit was inside our radius-2 union. Section 2a measures the sign and size of the resulting distortion on the fog-free delta; the visible subset is compared against the fogged-out complement of the same games.
2. **Grid-knowledge selection (Channel C).** Cell-level claims additionally require every path cell to be non-FOG in `start.grid`, i.e. within radius 2 of OUR start positions. This biases toward paths that stay close to us and toward shorter/turning paths over straight 3-step runs, since a straight run's third cell sits at Chebyshev 3. Direction: under-samples long straight dashes.
3. **Board-depletion residual (Channel C).** When the opponent is the slower player, our units and all seven NPCs move first. Only visible NPCs can be replayed, so the reconstructed board can be too rich; that inflates reconstructed pickup. Rounds where the reconstruction disagrees with the logged `pickup` are DISCARDED, which removes the error but also biases the clean subset toward quiet neighbourhoods (fewer NPCs and fewer contested cells).
4. **`pickup` truncation.** Logged `pickup` is fog-truncated whenever the action list is short, and for a small residue of length-3 lists too. Records with `pickup < delta` (provably truncated) are excluded, so the surviving set is biased toward rounds fully observed from the outside.
5. **Grid-value census (1c).** Observation-weighted, so it over-states the prevalence of `v>=11` cells: an uneaten fat cell is recounted every round (see 1c).
6. **Our-side identity.** The archive's 'ours' column spans ~100 experimental builds including deliberately crippled probes; only the CHANGELOG-validated `frTu*` family (and the name-matched `t1f*` family, flagged separately) is used as the f18064c comparison. The archive mixture row is shown for reference only.
7. **Asymmetric reconstructability.** Our own units are always visible to ourselves, so our trajectory sample is near-complete while the opponents' is ~39-47%. Where a like-for-like comparison matters, the `f18064c_near_opponent` row restricts our units to unit-rounds within Chebyshev 2 of an opponent unit, which is the mirror of their observability condition; it moves our numbers by only a few points, so the asymmetry is not what drives the contrast.
8. **Double-bite vs chaining is NOT a fog artefact.** The `n_paying_steps` counter distinguishes 'one cell taken twice' from 'two cells taken once', and both are computed from the same replay, so neither side can be flattered by the other's mechanism.

## 5. Reconciliation with the orchestrator's independent fog-free run

Both runs use the same `gold` channel but difference it differently (END-to-END vs within-row). Agreement on the six f18064c battlefields and on the manifest archive:

| quantity | orchestrator | this script | delta |
|---|---|---|---|
| T-1 manifest archive, mean delta/unit-round | +2.057 | +2.061 | match to rounding |
| T-1 manifest archive, delta>0 | 47.7% | 47.8% | match to rounding |
| T-1 manifest archive, delta>=6 | 15.57% | 15.57% | match to rounding |
| T-1 manifest archive, delta>=8 | 8.20% | 8.18% | match to rounding |
| T-1 manifest archive, delta<0 | 0.40% | 0.40% | match to rounding |
| Tundra manifest archive, mean delta/unit-round | +2.180 | +2.182 | match to rounding |
| Tundra manifest archive, delta>0 | 55.5% | 55.5% | match to rounding |
| Tundra manifest archive, delta>=6 | 16.20% | 16.22% | match to rounding |
| Tundra manifest archive, delta>=8 | 7.55% | 7.57% | match to rounding |
| Tundra manifest archive, delta<0 | 0.28% | 0.28% | match to rounding |
| f18064c pooled, mean delta/unit-round | +1.491 | +1.488 | match to rounding |
| f18064c pooled, hit rate | 34.8% | 34.7% | match to rounding |
| f18064c pooled, yield per hit | 4.668 | 4.668 | match to rounding |
| f18064c pooled, delta>=8 | 6.47% | 6.46% | match to rounding |

Every fog-free figure agrees with the orchestrator's independent run to within rounding, and the per-battlefield table in 1a reproduces all 24 of their cells to <=0.005 gold / <=0.2pp. **There is no discrepancy to arbitrate on the fog-free channel.** The one place where my numbers change the picture is the SIGN of the chaining story on the visible subset (section 3a): the opponents really do chain more than we do inside `>=8` bursts, but they do NOT convert that into a higher `>=8` rate, so it is a mechanism difference rather than an income difference.

## 6. Verdict

**The path-harvesting hypothesis survives only as a description of mechanism, and FAILS as an explanation of the income gap.** T-1: fog-free mean +1.844 gold/unit-round over n=155210 unit-rounds, hit rate 43.6%, yield per hit 4.59, delta>=8 in 7.12%; on the clean visible subset (n=44318) the mean number of PAYING cells per 3-step path is 0.368, 5.3% of clean unit-rounds pay on 2 or more cells, and the delta>=8 bursts (n=1876) split 50.3% chained across >=2 cells / 49.7% one fat cell in one bite / 0.0% one cell bitten twice -- Tundra: fog-free mean +1.870 gold/unit-round over n=110976 unit-rounds, hit rate 49.8%, yield per hit 3.96, delta>=8 in 6.39%; on the clean visible subset (n=33438) the mean number of PAYING cells per 3-step path is 0.446, 5.6% of clean unit-rounds pay on 2 or more cells, and the delta>=8 bursts (n=1393) split 49.0% chained across >=2 cells / 51.0% one fat cell in one bite / 0.0% one cell bitten twice -- OURS f18064c: fog-free mean +1.488 gold/unit-round over n=36000 unit-rounds, hit rate 34.7%, yield per hit 4.67, delta>=8 in 6.46%; on the clean visible subset (n=35386) the mean number of PAYING cells per 3-step path is 0.374, 2.2% of clean unit-rounds pay on 2 or more cells, and the delta>=8 bursts (n=2321) split 11.9% chained across >=2 cells / 26.9% one fat cell in one bite / 61.1% one cell bitten twice. Falsification test, answered explicitly: the hypothesis predicted that opponent bursts would be predominantly chained multi-cell while ours were single-cell. On the clean visible subset that prediction is CONFIRMED in direction (about half of their `>=8` bursts are chained across 2-3 distinct cells versus roughly one eighth of ours, a ~4x difference) but REFUTED in consequence: on the unbiased fog-free channel, over the same 36 f18064c games, our `>=8` rate is 6.46% against T-1's 6.20% and Tundra's 5.12%, our gold per scoring round is 4.67 against their 4.19, and yet we lose. Every gold of the deficit is carried by hit rate -- how often a unit scores at all (34.7% for us, 40.5-41.6% for them on the same battlefields). So the real lever is not 'chain more cells per trip', it is 'be somewhere that has a cell to step on more often'. The trajectory channel names the concrete mechanism behind that: both opponents' three steps are always monotone (zero direction reversals and zero within-round revisits in 44,318 + 33,438 clean unit-rounds), whereas our frozen build reverses in 56.5% of its rounds and folds 76.1% of its 3-move rounds onto only two distinct cells, so 16.6% of our steps land on a cell we already drained this round. We convert local supply at least as well as they do when supply is present (hit rate 57.7% vs Tundra 56.9% and T-1 36.5% at supply=2); we simply oscillate in place instead of travelling to fresh supply.

Artifacts: `sim/reports/path_harvest_opponent.json` (machine-readable, all sample sizes), this file. Re-run with `python3 sim/analyze_opponent_paths.py run`.
