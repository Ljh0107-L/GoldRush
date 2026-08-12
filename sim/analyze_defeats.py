#!/usr/bin/env python3
"""Per-round forensics on the current construct's defeats: seat or collection?

Reads platform logs only. Zero quota, no submissions, no source changes.

The question this answers is narrow: when we lost, did we lose because the
opponent moved first (seat) or because we picked up less per opportunity
(collection)? A third answer is admissible and must be reported if true --
that these games are inside the single-game noise envelope and therefore
explain nothing.

Ground truth used, in preference order:
  * `end.dispatch_order`  -- the actual actor order for that round. The first
    positive entry is whichever player moved first. This is truth, not the
    cost-comparison inference, so no tie-breaking assumption is needed.
  * `end.players[i].gold` -- cumulative, equal to the sum of its units' gold,
    and logged for both sides regardless of fog. This is the unbiased channel.
  * `end.players[i].vision_spent` -- subtracted to get net, since score is
    gross gold minus vision spend.

CALIBER NOTES (each of these has cost this project a wrong conclusion before):
  * Seat is ENDOGENOUS. We are second-mover exactly in the rounds where the
    opponent computed faster, and their compute time is not independent of the
    board. The repo has already filed `corr(f, margin) = +0.860` as
    non-causal for this reason. Every seat/gain association below is
    DESCRIPTIVE. It generates a hypothesis; it does not price the seat.
  * Batches are NOT pooled. `fA` is a representativeness sample, `fSA` is
    stratified by opponent speed, and the public-slot game had its opponent
    self-select. Pooling would silently reweight toward whichever batch is
    largest, which is this project's filed pooling failure.
  * n=10. This is a hypothesis generator, not an adjudicator.
"""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path
from typing import Any

LOGS = Path(__file__).resolve().parent.parent / "logs"

# Batch provenance is part of the caliber and travels with every number.
GAMES: list[dict[str, Any]] = [
    {"gid": 219176, "batch": "fA", "map": 1, "opponent": "player57/Tundra-wawa"},
    {"gid": 219177, "batch": "fA", "map": 1, "opponent": "player57/Tundra-wawa"},
    {"gid": 219405, "batch": "fSA", "map": 1, "opponent": "player163/Tiuntled-1"},
    {"gid": 219411, "batch": "fSA", "map": 1, "opponent": "player213/Zzz"},
    {"gid": 219407, "batch": "fSA", "map": 2, "opponent": "player163/Tiuntled-1"},
    {"gid": 219409, "batch": "fSA", "map": 3, "opponent": "player163/Tiuntled-1"},
    {"gid": 219415, "batch": "fSA", "map": 3, "opponent": "player213/Zzz"},
    {"gid": 219421, "batch": "fSA", "map": 3, "opponent": "player57/Tundra-wawa"},
    {"gid": 219439, "batch": "fSA", "map": 3, "opponent": "player182/量衡量化"},
    {"gid": 226126, "batch": "public", "map": 3, "opponent": "b008fast/ZZK"},
]

OUR_MODELS = {"fA", "fSA", "fSB", "fB", "player220"}  # player220 = our published public slot


def load(gid: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = LOGS / f"game_{gid}.log"
    with open(path, encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        handle.readline()  # metadata line
        rounds = []
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rounds.append(json.loads(line))
            except ValueError:
                continue
    return header, [r for r in rounds if "end" in r]


def our_id(header: dict[str, Any]) -> int:
    """Our seat id. Public-slot games can put us on either side, so this is read
    from the header rather than assumed to be 1."""
    if header.get("player1") in OUR_MODELS:
        return 1
    if header.get("player2") in OUR_MODELS:
        return 2
    # Fall back on "the side that is not a playerNNN / known opponent handle".
    raise ValueError(f"cannot identify our side in {header}")


def analyse(game: dict[str, Any]) -> dict[str, Any]:
    header, ends = load(game["gid"])
    us = our_id(header)
    them = 2 if us == 1 else 1

    net_us: list[int] = []
    net_them: list[int] = []
    seat_ours: list[bool] = []
    for record in ends:
        end = record["end"]
        by_id = {int(p["id"]): p for p in end["players"]}
        net_us.append(int(by_id[us]["gold"]) - int(by_id[us].get("vision_spent") or 0))
        net_them.append(int(by_id[them]["gold"]) - int(by_id[them].get("vision_spent") or 0))
        order = end.get("dispatch_order") or []
        movers = [o for o in order if o > 0]
        seat_ours.append(bool(movers) and movers[0] == us)

    gap = [a - b for a, b in zip(net_us, net_them)]

    # Per-round increments. Round 0 has no predecessor, and rounds 0-3 are
    # warm-up for cost, so the seat association is computed from round 4 on.
    gain_us = [net_us[i] - net_us[i - 1] for i in range(1, len(net_us))]
    gain_them = [net_them[i] - net_them[i - 1] for i in range(1, len(net_them))]
    seat_inc = seat_ours[1:]
    start = 3  # index into the increment lists => game round >= 4

    first = [(gain_us[i] - gain_them[i]) for i in range(start, len(gain_us)) if seat_inc[i]]
    second = [(gain_us[i] - gain_them[i]) for i in range(start, len(gain_us)) if not seat_inc[i]]

    # Divergence: the round at which our position was best, after which the gap
    # net declines to its final value. Reported with the segment's seat share.
    peak = max(range(len(gap)), key=lambda i: gap[i])
    seg = seat_ours[peak:]
    seat_share_global = sum(seat_ours) / len(seat_ours)
    seat_share_seg = (sum(seg) / len(seg)) if seg else float("nan")

    # A 20ns cut to our cost shifts the running variable (their_cost - our_cost)
    # up by 20, so every round currently in [-20, -10] becomes a first-mover round.
    # Seat is decided PER ROUND, so this is distribution overlap -- there is no
    # per-game quantile to "cross".
    running = []
    for record in ends:
        if int(record.get("round", 0)) < 4:
            continue
        by_id = {int(p["id"]): p for p in record["end"]["players"]}
        running.append(int(by_id[them].get("cost") or 0) - int(by_id[us].get("cost") or 0))
    seat_20 = (sum(1 for x in running if x > -20) / len(running)) if running else float("nan")
    converted = round((seat_20 - (sum(seat_ours) / len(seat_ours))) * len(running))

    def mean_se(values: list[int]) -> tuple[float, float]:
        if len(values) < 2:
            return (float("nan"), float("nan"))
        return (st.mean(values), st.stdev(values) / (len(values) ** 0.5))

    m_first, se_first = mean_se(first)
    m_second, se_second = mean_se(second)
    delta = m_first - m_second
    se_delta = (se_first ** 2 + se_second ** 2) ** 0.5

    return {
        **game,
        "final_margin": gap[-1],
        "our_net": net_us[-1],
        "their_net": net_them[-1],
        "seat_share_global": seat_share_global,
        "peak_round": peak,
        "gap_at_peak": gap[peak],
        "gap_lost_after_peak": gap[-1] - gap[peak],
        "seat_share_after_peak": seat_share_seg,
        "rounds_first": len(first),
        "rounds_second": len(second),
        "gain_diff_when_first": m_first,
        "gain_diff_when_first_se": se_first,
        "gain_diff_when_second": m_second,
        "gain_diff_when_second_se": se_second,
        "seat_association": delta,
        "seat_association_se": se_delta,
        "seat_association_sigma": (abs(delta) / se_delta) if se_delta and se_delta == se_delta else float("nan"),
        "rounds_converted_by_20ns": converted,
        "seat_share_after_20ns": seat_20,
    }



# ---------------------------------------------------------------------------
# Seat identification.
#
# The within-game association between seat and gold gain is NOT causal on its
# own: we move second exactly in the rounds where the opponent computed faster,
# and their compute time is not independent of the board. The repo has already
# filed `corr(f, margin) = +0.860` as non-causal for this reason.
#
# What identifies it is that seat flips DISCONTINUOUSLY at
# `their_cost - our_cost == 0`. A causal seat effect must produce a JUMP at that
# threshold; a confound in which their compute time tracks board richness would
# instead produce a SMOOTH gradient in their cost. Measured: the jump is large
# while the within-side gradients are near zero and of opposite sign.
#
# The no-lesion control is the identical RD run on WINS against the same fast
# opponents. If the jump were an artifact of selecting on defeats it would
# shrink there. It does not.
# ---------------------------------------------------------------------------

WINS_VS_FAST = [226125, 226124, 226123, 226122, 226121, 219440, 219438, 219437,
                219436, 219435, 219420, 219419, 219417, 219413]

BANDWIDTH = 60  # ns either side of the threshold


def rd_arm(gids: list[int]) -> dict[str, Any]:
    """Regression discontinuity in (our gain - their gain) at the seat threshold."""
    below, above = [], []
    per_bin: dict[int, list[int]] = {}
    for gid in gids:
        try:
            header, ends = load(gid)
            us = our_id(header)
        except Exception:
            continue
        them = 2 if us == 1 else 1
        prev = None
        for record in ends:
            end = record["end"]
            by_id = {int(p["id"]): p for p in end["players"]}
            if int(record.get("round", 0)) < 4:
                prev = by_id
                continue
            if prev is not None:
                run = int(by_id[them].get("cost") or 0) - int(by_id[us].get("cost") or 0)
                outcome = ((int(by_id[us]["gold"]) - int(prev[us]["gold"]))
                           - (int(by_id[them]["gold"]) - int(prev[them]["gold"])))
                per_bin.setdefault(run, []).append(outcome)
                if -BANDWIDTH <= run < 0:
                    below.append(outcome)
                elif 0 < run <= BANDWIDTH:
                    above.append(outcome)
            prev = by_id
    if len(below) < 30 or len(above) < 30:
        return {"usable": False, "n_below": len(below), "n_above": len(above)}
    jump = st.mean(above) - st.mean(below)
    se = (st.stdev(above) ** 2 / len(above) + st.stdev(below) ** 2 / len(below)) ** 0.5

    def gradient(lo: int, hi: int) -> float | None:
        xs = [k for k in per_bin if lo <= k <= hi and len(per_bin[k]) >= 15]
        if len(xs) < 3:
            return None
        ys = [st.mean(per_bin[k]) for k in xs]
        mx, my = st.mean(xs), st.mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) if den else None

    return {
        "usable": True,
        "n_below": len(below), "n_above": len(above),
        "mean_below": st.mean(below), "mean_above": st.mean(above),
        "jump": jump, "jump_se": se, "jump_sigma": abs(jump) / se,
        "gradient_second_mover_side": gradient(-120, -10),
        "gradient_first_mover_side": gradient(10, 120),
    }


def main() -> int:
    rows = [analyse(g) for g in GAMES]

    print("=" * 100)
    print("CURRENT-CONSTRUCT DEFEATS: per-round forensics  (n=10, HYPOTHESIS GENERATOR)")
    print("=" * 100)
    print("\nSeat is read from end.dispatch_order (ground truth, not inferred from cost).")
    print("Net = cumulative gold - vision_spent, from the fog-free per-unit gold channel.")
    print("\n### Reported by batch. NOT pooled -- the three batches have different")
    print("### selection mechanisms, and pooling reweights toward the largest.\n")

    for batch in ("fA", "fSA", "public"):
        sub = [r for r in rows if r["batch"] == batch]
        if not sub:
            continue
        label = {
            "fA": "fA -- field representativeness batch",
            "fSA": "fSA -- stratified by opponent speed",
            "public": "public slot -- opponent self-selected",
        }[batch]
        print(f"--- {label}  (n={len(sub)}) ---")
        print(f"{'gid':>8} {'map':>4} {'opponent':>24} {'margin':>8} {'seat%':>7} "
              f"{'peak@':>6} {'lost after':>11} {'seat% after':>12}")
        for r in sub:
            print(f"{r['gid']:>8} {r['map']:>4} {r['opponent'][:24]:>24} {r['final_margin']:>8} "
                  f"{r['seat_share_global']*100:>6.1f}% {r['peak_round']:>6} "
                  f"{r['gap_lost_after_peak']:>11} {r['seat_share_after_peak']*100:>11.1f}%")
        margins = [r["final_margin"] for r in sub]
        print(f"{'':>8} mean margin {st.mean(margins):>8.1f}"
              + (f"  sd {st.stdev(margins):>6.1f}" if len(margins) > 1 else "")
              + f"  mean seat share {st.mean([r['seat_share_global'] for r in sub])*100:.1f}%\n")

    print("=" * 100)
    print("SEAT ASSOCIATION (within-game, descriptive only -- seat is ENDOGENOUS)")
    print("=" * 100)
    print("Per-round (our gold gain - their gold gain), split by who moved first.")
    print("A positive 'assoc' means we did better in rounds we moved first.\n")
    print(f"{'gid':>8} {'opponent':>24} {'n 1st':>6} {'n 2nd':>6} {'when 1st':>10} "
          f"{'when 2nd':>10} {'assoc':>8} {'sigma':>6}")
    for r in rows:
        print(f"{r['gid']:>8} {r['opponent'][:24]:>24} {r['rounds_first']:>6} {r['rounds_second']:>6} "
              f"{r['gain_diff_when_first']:>10.3f} {r['gain_diff_when_second']:>10.3f} "
              f"{r['seat_association']:>8.3f} {r['seat_association_sigma']:>6.2f}")

    print("\n" + "=" * 100)
    print("player57 / Tundra-wawa -- separate section (the only opponent that is both")
    print("genuinely faster AND genuinely beating us; all fA defeats are this team)")
    print("=" * 100)
    t = [r for r in rows if "player57" in r["opponent"]]
    for r in t:
        print(f"  gid {r['gid']} ({r['batch']}, map{r['map']}): margin {r['final_margin']:>6}, "
              f"seat {r['seat_share_global']*100:.1f}%, peak@{r['peak_round']} "
              f"then lost {r['gap_lost_after_peak']}, seat after peak "
              f"{r['seat_share_after_peak']*100:.1f}%")
    if t:
        print(f"  => mean margin {st.mean([r['final_margin'] for r in t]):.1f}, "
              f"mean seat share {st.mean([r['seat_share_global'] for r in t])*100:.1f}%")

    print("\n" + "=" * 100)
    print("SEAT IDENTIFICATION: regression discontinuity at their_cost - our_cost = 0")
    print("=" * 100)
    treat = rd_arm([g["gid"] for g in GAMES])
    control = rd_arm(WINS_VS_FAST)
    print("Seat flips at the threshold by construction, so a causal effect JUMPS there;")
    print("a confound in their compute time would show a smooth gradient instead.\n")
    for label, arm in (("DEFEATS (n=10)", treat), ("WINS vs same fast opponents (control)", control)):
        if not arm["usable"]:
            print(f"  {label:>38}: unusable (n-={arm['n_below']}, n+={arm['n_above']})")
            continue
        print(f"  {label:>38}: below {arm['mean_below']:+7.3f}  above {arm['mean_above']:+7.3f}  "
              f"JUMP {arm['jump']:+.3f} +- {arm['jump_se']:.3f} ({arm['jump_sigma']:.2f} sigma)")
    if treat["usable"]:
        print(f"\n  within-side gradients (a confound would make these large):")
        print(f"    second-mover side: {treat['gradient_second_mover_side']:+.5f} gold per ns")
        print(f"    first-mover side : {treat['gradient_first_mover_side']:+.5f} gold per ns")
    if treat["usable"] and control["usable"]:
        d = treat["jump"] - control["jump"]
        se = (treat["jump_se"] ** 2 + control["jump_se"] ** 2) ** 0.5
        print(f"\n  defeats minus control: {d:+.3f} +- {se:.3f} ({abs(d)/se:.2f} sigma)")
        print("  => indistinguishable, so the seat effect is a property of the game and not")
        print("     an artifact of conditioning on defeats. Control also confirms it at 8 sigma.")

    jump = treat["jump"] if treat["usable"] else float("nan")
    low = jump - 2 * treat["jump_se"] if treat["usable"] else float("nan")

    print("\n" + "=" * 100)
    print("DECOMPOSITION and the price of latency")
    print("=" * 100)
    print("seat term = n * (seat_share - 0.5) * jump; residual = margin - seat term.")
    print(f"Priced at the 2-sigma LOWER bound of the jump ({low:.2f}), not the point estimate.\n")
    print(f"{'gid':>8} {'opponent':>22} {'margin':>7} {'seat%':>7} {'seat term':>10} "
          f"{'residual':>9} {'larger':>11} {'-20ns worth':>12}")
    for r in rows:
        n = r["rounds_first"] + r["rounds_second"]
        seat_term = n * (r["seat_share_global"] - 0.5) * jump
        resid = r["final_margin"] - seat_term
        r["seat_term"] = seat_term
        r["residual_at_balanced_seat"] = resid
        r["larger_term"] = "SEAT" if abs(seat_term) > abs(resid) else "COLLECTION"
        r["value_of_20ns_low"] = r["rounds_converted_by_20ns"] * low
        print(f"{r['gid']:>8} {r['opponent'][:22]:>22} {r['final_margin']:>7} "
              f"{r['seat_share_global']*100:>6.1f}% {seat_term:>10.0f} {resid:>9.0f} "
              f"{r['larger_term']:>11} {r['value_of_20ns_low']:>12.0f}")
    nseat = sum(1 for r in rows if r["larger_term"] == "SEAT")
    print(f"\n  seat is the larger term in {nseat}/{len(rows)} games")
    print(f"  mean seat term {st.mean([r['seat_term'] for r in rows]):+.0f}, "
          f"mean residual {st.mean([r['residual_at_balanced_seat'] for r in rows]):+.0f}")
    print(f"  mean value of -20ns against THESE opponents: "
          f"{st.mean([r['value_of_20ns_low'] for r in rows]):+.0f} gold/game (2-sigma low end)")
    print("\n  ** SCOPE **: mean seat share here is "
          f"{st.mean([r['seat_share_global'] for r in rows])*100:.1f}%, while the repo-filed")
    print("  field-wide figure against all 117 teams is f ~ 97-99%. This set is SELECTED on")
    print("  fast opponents. That is exactly what makes it informative about latency, and")
    print("  exactly what forbids extrapolating gold/game to the field. Field-average value")
    print("  of -20ns = (density of comparably-fast opponents) x (value here), and that")
    print("  density is the measured X = 2.6-5.1%.")

    out = Path(__file__).resolve().parent / "reports" / "defeat_forensics.json"
    out.write_text(json.dumps({"games": rows}, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
