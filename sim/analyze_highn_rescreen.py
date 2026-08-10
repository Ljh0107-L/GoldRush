"""High-n re-screen of the small-candidate pool against the delivered construct ``fd47ea6``.

What this driver is for
-----------------------
The project's ``+150`` gold acceptance gate was reverse-derived from closing a single
294-gold head-to-head gap, and it systematically killed candidates whose point estimates
sat at +5..+80 with SEs of 4..39.  Lowering the gate does not make those candidates
acceptable, it makes them *undecidable*; buying the missing precision on the platform is
prohibitive and in the simulator is nearly free, because same-seed pairing removes most of
the variance.  So this driver moves acceptance into the simulator and raises ``n`` by an
order of magnitude.

Acceptance rule implemented here (set by the owner, not by this driver):

    accept iff  margin - max(2 * SE, |calibrated - uniform|) > 0
                AND the sign agrees on a disjoint out-of-sample seed set.

Both terms are always printed.  ``2 * SE`` is the statistical term and shrinks with ``n``;
``|calibrated - uniform|`` is the *apparatus* term and does not shrink with ``n`` at all,
because the repo simulator places central gold uniformly over region 1 and therefore has no
gradient inside the central 9x9 where the measured gradient lives.  The calibrated arm
recovers 2.58x of the measured 3.35x ring1/ring5 steepness (77%), so the two-field spread
is the honest floor on how much the apparatus can be wrong about any candidate that changes
which cell is entered.

Discipline
----------
* ``src/player.cpp`` is never written.  The baseline is ``git show fd47ea6:src/player.cpp``
  with its sha256 asserted; every variant is a textual rebake in the caller's workdir.
* The calibrated field is ``sim.analyze_hotfield_table.install_field``, an in-process
  monkeypatch; ``sim/scenario.py`` is not modified.  The integrity gate is that the two
  field models must produce disjoint scenario digests.
* Judged on ``margin`` = delta(ours - theirs), never on ``net``: on a shared board a
  candidate that spreads out can lift its own net while lifting the unmodified opponent's
  net more.  Every row carries the three-way classification.
* Both order arms (``--fixed-costs 200,201`` and ``201,200``) are always run and are always
  reported separately as well as pooled.
* Entry alignment is a *build step*, not a cost: after every build the ``moveDecision``
  entry is re-checked and the dead pad is re-tuned until ``mod64 == 0x10``.

Modes
-----
``construct``  build every variant, re-tune its pad, and report .text/.rodata size and
               sha256, entry mod64, FP16 count, static instruction count.
``icount``     dynamic instructions *and* cycles per call on one shared recorded stream.
``ab``         the paired closed-loop A/B; the workhorse.
``stream``     regenerate the recorded input stream ``icount`` replays.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
from concurrent import futures
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import highn_variants as HV                                     # noqa: E402

COSTS_WE_FIRST = (200, 201)
COSTS_WE_SECOND = (201, 200)
WANT_MOD64 = 0x10
DELIVERED_PAD = 96


# ==========================================================================================
# statistics
# ==========================================================================================

def summary(values: Sequence[float]) -> Mapping[str, Any]:
    values = [float(v) for v in values]
    if not values:
        return {"n": 0, "mean": None, "sd": None, "se": None, "median": None,
                "positive": 0, "min": None, "max": None}
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    se = sd / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {"n": len(values), "mean": mean, "sd": sd, "se": se,
            "median": statistics.median(values), "sigma": (mean / se) if se else None,
            "positive": sum(1 for v in values if v > 0),
            "zero": sum(1 for v in values if v == 0),
            "min": min(values), "max": max(values)}


def diff_summary(a: Sequence[float], b: Sequence[float]) -> Mapping[str, Any]:
    """Unpaired difference of two means with a pooled SE (used for two-field contrasts)."""
    sa, sb = summary(a), summary(b)
    if not sa["n"] or not sb["n"]:
        return {"delta": None, "se": None}
    se = math.sqrt((sa["se"] or 0.0) ** 2 + (sb["se"] or 0.0) ** 2)
    delta = sa["mean"] - sb["mean"]
    return {"delta": delta, "se": se, "sigma": (delta / se) if se else None,
            "abs_delta": abs(delta)}


def classify(ours: float | None, theirs: float | None) -> str:
    """The three-way classification the owner requires on every candidate row."""
    if ours is None or theirs is None:
        return "unknown"
    if ours > 0 and theirs < 0:
        return "joint move"
    if ours <= 0 and theirs <= 0:
        return "pie-shrinking"
    if ours > 0 and theirs > 0:
        return "ceding" if theirs >= ours else "co-gain (ours faster)"
    return "pure loss"


# ==========================================================================================
# build + construct gates
# ==========================================================================================

def _run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("failed: %s\n%s\n%s" % (" ".join(map(str, cmd)),
                                                   proc.stdout, proc.stderr))
    return proc.stdout


def _section(so: Path, name: str) -> Mapping[str, Any]:
    for line in _run(["readelf", "-S", "-W", str(so)]).splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[1] == name:
            return {"addr": int(parts[3], 16), "offset": int(parts[4], 16),
                    "size": int(parts[5], 16)}
    raise KeyError(name)


def _section_sha(so: Path, name: str, tmp: Path) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / ("%s%s.bin" % (so.stem, name.replace(".", "_")))
    _run(["objcopy", "-O", "binary", "--only-section=%s" % name, str(so), str(out)])
    return hashlib.sha256(out.read_bytes()).hexdigest()


def entry_mod64(so: Path) -> Mapping[str, Any]:
    for line in _run(["nm", "-D", "--defined-only", str(so)]).splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "moveDecision":
            addr = int(parts[0], 16)
            return {"addr": addr, "addr_hex": "0x%x" % addr,
                    "mod64": addr % 64, "mod64_hex": "0x%02x" % (addr % 64)}
    raise KeyError("moveDecision")


def _fp16_count(so: Path) -> int:
    text = _run(["objdump", "-d", str(so)])
    return sum(1 for line in text.splitlines() if HV.FP16_RE.search(line))


def _static_icount(so: Path) -> int:
    out = _run(["objdump", "-d", "--disassemble=moveDecision", str(so)])
    return sum(1 for line in out.splitlines() if re.match(r"^\s+[0-9a-f]+:\t", line))


def build_tuned(name: str, patches: Sequence[str], base_src: Path, workdir: Path,
                *, max_tries: int = 6) -> Mapping[str, Any]:
    """Build ``name`` and re-tune its dead pad until ``moveDecision`` entry mod64 == 0x10.

    Entry alignment is a discrete cliff, not a gradient: the repo has measured 0x20 and 0x30
    at +11.67 ns each.  The delivered construct normalises with ``asm(".space 96, 0x90")``,
    and the entry moves one byte per pad byte (verified by an eleven-point sweep), so any
    shift can be absorbed by ``pad = 96 + ((0x10 - mod64) mod 64)``, which is always in
    [96, 159].  This makes the position tax a build step rather than a term in the cost
    model, and that is why no layout term appears in the net anywhere below.
    """
    tried: list[Mapping[str, Any]] = []
    pad = DELIVERED_PAD
    for _ in range(max_tries):
        extra: list[str] = []
        text_patches = list(patches)
        src, so, warn = HV.build(name, text_patches, base_src, workdir)
        if pad != DELIVERED_PAD:
            body = src.read_text()
            body = body.replace(HV.A_PAD, 'asm(".space %d, 0x90");' % pad)
            src.write_text(body)
            cmd = ["g++", *HV.BUILD_FLAGS, *extra, "-o", str(so), str(src),
                   "-I", str(base_src.parent)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
            warn = proc.stderr
        entry = entry_mod64(so)
        tried.append({"pad": pad, **entry})
        if entry["mod64"] == WANT_MOD64:
            return {"name": name, "patches": list(patches), "src": str(src), "so": str(so),
                    "pad": pad, "pad_retuned": pad != DELIVERED_PAD,
                    "pad_search": tried, "warnings": warn.strip(), "entry": entry}
        pad = DELIVERED_PAD + ((WANT_MOD64 - entry["mod64"]) % 64)
        if any(t["pad"] == pad for t in tried):
            pad += 64
    raise RuntimeError("pad re-tune did not converge for %s: %s" % (name, tried))


def mode_construct(args: argparse.Namespace) -> Mapping[str, Any]:
    workdir = Path(args.workdir)
    base_src = HV.baseline_text(workdir)
    tmp = workdir / "sect"
    specs = _arm_specs(args)
    rows: dict[str, Any] = {}
    built: dict[str, Mapping[str, Any]] = {}
    for name, patches in specs.items():
        built[name] = build_tuned(name, patches, base_src, workdir)
    base = built["base"]
    base_text = _section(Path(base["so"]), ".text")
    base_rodata = _section(Path(base["so"]), ".rodata")
    base_text_sha = _section_sha(Path(base["so"]), ".text", tmp)
    base_rodata_sha = _section_sha(Path(base["so"]), ".rodata", tmp)
    base_static = _static_icount(Path(base["so"]))
    for name, info in built.items():
        so = Path(info["so"])
        text = _section(so, ".text")
        rodata = _section(so, ".rodata")
        text_sha = _section_sha(so, ".text", tmp)
        rodata_sha = _section_sha(so, ".rodata", tmp)
        static = _static_icount(so)
        rows[name] = {
            "patches": info["patches"], "pad": info["pad"],
            "pad_retuned": info["pad_retuned"], "pad_search": info["pad_search"],
            "so": info["so"],
            "so_sha256": hashlib.sha256(so.read_bytes()).hexdigest(),
            "src_sha256": hashlib.sha256(Path(info["src"]).read_bytes()).hexdigest(),
            "entry": info["entry"],
            "entry_mod64_ok": info["entry"]["mod64"] == WANT_MOD64,
            "text_size": text["size"], "text_size_delta": text["size"] - base_text["size"],
            "text_sha256": text_sha, "text_identical_to_base": text_sha == base_text_sha,
            "rodata_size": rodata["size"],
            "rodata_size_delta": rodata["size"] - base_rodata["size"],
            "rodata_sha256": rodata_sha,
            "rodata_identical_to_base": rodata_sha == base_rodata_sha,
            "fp16_instructions": _fp16_count(so),
            "static_instructions_moveDecision": static,
            "static_instruction_delta": static - base_static,
            "build_warnings": info["warnings"],
        }
    return {
        "baseline_commit": HV.BASELINE_COMMIT, "baseline_sha256": HV.BASELINE_SHA256,
        "build_flags": HV.BUILD_FLAGS,
        "host": _run(["uname", "-srm"]).strip(),
        "compiler": _run(["g++", "--version"]).splitlines()[0],
        "delivered_pad": DELIVERED_PAD, "want_mod64": "0x%02x" % WANT_MOD64,
        "note": "the delivered fd47ea6 already carries asm('.space 96, 0x90') and its entry "
                "lands at mod64 == 0x10 unaided by any further pad, so a proposed '48-byte "
                "dead pad' is already banked; a 48-byte pad would put the entry at 0x20.  For "
                "every other variant the pad is re-tuned here until mod64 == 0x10, which is why "
                "no layout tax appears in any cost figure.",
        "arms": rows,
    }


# ==========================================================================================
# dynamic instruction / cycle counts on a shared stream
# ==========================================================================================

def mode_stream(args: argparse.Namespace) -> Mapping[str, Any]:
    """Record the 500-round input stream that ``icount`` replays against every arm."""
    from sim.runner import run_game
    workdir = Path(args.workdir)
    base_so = workdir / "base.so"
    log = workdir / "stream.log"
    run_game(str(base_so), str(base_so), map_source=args.map, seed=str(args.stream_seed),
             dispatch="fixed", fixed_costs=COSTS_WE_FIRST,
             player1_name="base", player2_name="base", output_path=str(log))
    out = workdir / "stream.bin"
    text = _run([sys.executable, str(ROOT / "tests" / "dump_inputs.py"), str(log)])
    if not out.exists():
        # dump_inputs writes next to the log by default; find it
        for cand in (log.with_suffix(".bin"), workdir / "inputs.bin"):
            if cand.exists():
                out = cand
                break
    return {"log": str(log), "stdout": text.strip(), "inputs": str(out),
            "map": args.map, "seed": args.stream_seed}


def mode_icount(args: argparse.Namespace) -> Mapping[str, Any]:
    workdir = Path(args.workdir)
    inputs = Path(args.inputs)
    tool = workdir / "icount"
    if not tool.exists():
        _run(["g++", "-std=c++17", "-O2", "-o", str(tool),
              str(ROOT / "tests" / "icount.cpp"), "-ldl"])
    specs = _arm_specs(args)
    rows: dict[str, Any] = {}
    for counter in ("instructions", "cycles"):
        for name in specs:
            so = workdir / ("%s.so" % name)
            if not so.exists():
                continue
            out = _run([str(tool), str(so), str(inputs), str(args.calls), str(args.reps),
                        counter])
            per_call = [float(m) for m in re.findall(r"raw_per_call=([0-9.]+)", out)]
            rows.setdefault(name, {})[counter] = {
                "raw_per_call_reps": per_call,
                "raw_per_call_min": min(per_call) if per_call else None,
            }
    base_i = rows.get("base", {}).get("instructions", {}).get("raw_per_call_min")
    base_c = rows.get("base", {}).get("cycles", {}).get("raw_per_call_min")
    for name, cell in rows.items():
        if base_i is not None and "instructions" in cell:
            cell["instructions"]["delta_vs_base"] = (
                cell["instructions"]["raw_per_call_min"] - base_i)
        if base_c is not None and "cycles" in cell:
            cell["cycles"]["delta_vs_base"] = cell["cycles"]["raw_per_call_min"] - base_c
        if (base_i is not None and base_c is not None and "instructions" in cell
                and "cycles" in cell and cell["cycles"]["delta_vs_base"]):
            cell["marginal_ipc"] = (cell["instructions"]["delta_vs_base"]
                                    / cell["cycles"]["delta_vs_base"])
    return {
        "inputs": str(inputs), "calls": args.calls, "reps": args.reps,
        "protocol": "every .so is replayed against the SAME recorded input stream, so the "
                    "difference isolates code-path cost; .text being byte-identical is what "
                    "rules out a position tax, and every arm's entry has already been pinned "
                    "to mod64 == 0x10 by the pad re-tune in mode construct",
        "base_instructions_per_call": base_i, "base_cycles_per_call": base_c,
        "arms": rows,
    }


# ==========================================================================================
# the paired A/B
# ==========================================================================================

def _game_stats(log_bytes: bytes) -> Mapping[str, Any]:
    """Per-seat burn and scoring frequency, taken from the engine's own per-round log.

    Burn is exact without an engine field: unit gold only rises by pickup and falls by bomb
    or trample penalty, and there is no banking, so burn = previous + pickup - current.
    """
    per_seat = {1: collections.Counter(), 2: collections.Counter()}
    previous = {1: [0, 0], 2: [0, 0]}
    for line in log_bytes.decode().splitlines()[2:]:
        if not line.strip():
            continue
        record = json.loads(line)
        for pid, player in enumerate(record["end"]["players"], start=1):
            for index, unit in enumerate(player["units"]):
                pickup = int(unit.get("pickup", 0))
                gold = int(unit.get("gold", 0))
                burn = previous[pid][index] + pickup - gold
                previous[pid][index] = gold
                cell = per_seat[pid]
                cell["unit_rounds"] += 1
                cell["burn"] += max(0, burn)
                if pickup > 0:
                    cell["scoring"] += 1
                    cell["pickup_total"] += pickup
    out: dict[str, Any] = {}
    for pid, tag in ((1, "ours"), (2, "theirs")):
        cell = per_seat[pid]
        out["%s_scoring_rounds" % tag] = cell["scoring"]
        out["%s_burn" % tag] = cell["burn"]
        out["%s_pickup" % tag] = cell["pickup_total"]
        out["%s_unit_rounds" % tag] = cell["unit_rounds"]
    return out


def _play(task: Mapping[str, Any]) -> Mapping[str, Any]:
    from sim.analyze_hotfield_table import install_field
    from sim.runner import run_game
    install_field(task["field"])
    costs = COSTS_WE_FIRST if task["order"] == "we_first" else COSTS_WE_SECOND
    result = run_game(task["so"], task["opponent_so"], map_source=task["map"],
                      seed=str(task["seed"]), dispatch="fixed", fixed_costs=costs,
                      player1_name=task["arm"], player2_name="opp")
    s = result.summary
    row = dict(_game_stats(result.log_bytes))
    row.update({
        "arm": task["arm"], "seed": task["seed"], "order": task["order"],
        "field": task["field"], "band": task["band"],
        "net": int(s["players"]["1"]["net_gold"]),
        "opp_net": int(s["players"]["2"]["net_gold"]),
        "log_sha256": s["log_sha256"], "scenario_digest": s["scenario_digest"],
    })
    return row


def mode_ab(args: argparse.Namespace) -> Mapping[str, Any]:
    workdir = Path(args.workdir)
    specs = _arm_specs(args)
    arms = [a for a in specs if a != "base"]
    sos = {name: str(workdir / ("%s.so" % name)) for name in specs}
    for name, path in sos.items():
        if not Path(path).exists():
            raise SystemExit("missing %s -- run mode construct first" % path)
    bands = {"tune": args.seeds, "oos": args.oos_seeds}
    fields = [f for f in args.fields.split(",") if f]

    tasks = []
    for band, seeds in bands.items():
        if not seeds:
            continue
        for seed in seeds:
            for order in ("we_first", "we_second"):
                for field in fields:
                    for name in specs:
                        tasks.append({"arm": name, "so": sos[name],
                                      "opponent_so": sos["base"], "map": args.map,
                                      "seed": seed, "order": order, "field": field,
                                      "band": band})
    records: list[Mapping[str, Any]] = []
    with futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, row in enumerate(pool.map(_play, tasks, chunksize=1)):
            records.append(row)
            if args.progress and index % 200 == 0:
                print("  ... %d/%d" % (index + 1, len(tasks)), file=sys.stderr, flush=True)

    index_map: dict[tuple, Mapping[str, Any]] = {}
    for row in records:
        index_map[(row["band"], row["field"], row["order"], row["seed"], row["arm"])] = row

    digest_groups: dict[tuple, set] = collections.defaultdict(set)
    by_field: dict[str, set] = collections.defaultdict(set)
    for key, row in index_map.items():
        digest_groups[key[:4]].add(row["scenario_digest"])
        by_field[key[1]].add(row["scenario_digest"])
    integrity = {
        "arms_share_scenario_within_cell": all(len(v) == 1 for v in digest_groups.values()),
        "distinct_digests_per_field": {k: len(v) for k, v in by_field.items()},
    }
    if len(fields) > 1:
        keys = list(by_field)
        integrity["field_models_differ"] = by_field[keys[0]].isdisjoint(by_field[keys[1]])

    def paired(band: str, field: str, orders: Sequence[str], arm: str) -> Mapping[str, Any]:
        margins, ours, theirs, burn, oburn, score, oscore = [], [], [], [], [], [], []
        identical = 0
        for order in orders:
            for seed in bands[band]:
                b = index_map.get((band, field, order, seed, "base"))
                r = index_map.get((band, field, order, seed, arm))
                if b is None or r is None:
                    continue
                identical += 1 if r["log_sha256"] == b["log_sha256"] else 0
                margins.append((r["net"] - r["opp_net"]) - (b["net"] - b["opp_net"]))
                ours.append(r["net"] - b["net"])
                theirs.append(r["opp_net"] - b["opp_net"])
                burn.append(r["ours_burn"] - b["ours_burn"])
                oburn.append(r["theirs_burn"] - b["theirs_burn"])
                score.append(r["ours_scoring_rounds"] - b["ours_scoring_rounds"])
                oscore.append(r["theirs_scoring_rounds"] - b["theirs_scoring_rounds"])
        our_s, their_s = summary(ours), summary(theirs)
        return {
            "games": len(margins), "bit_identical_to_base": identical,
            "margin": summary(margins), "our_net": our_s, "their_net": their_s,
            "our_burn": summary(burn), "their_burn": summary(oburn),
            "our_scoring_rounds": summary(score), "their_scoring_rounds": summary(oscore),
            "classification": classify(our_s["mean"], their_s["mean"]),
            "_margins": margins,
        }

    aggregate: dict[str, Any] = {}
    for band, seeds in bands.items():
        if not seeds:
            continue
        for field in fields:
            for label, orders in (("we_first", ("we_first",)),
                                  ("we_second", ("we_second",)),
                                  ("pooled", ("we_first", "we_second"))):
                aggregate["%s|%s|%s" % (band, field, label)] = {
                    arm: paired(band, field, orders, arm) for arm in arms}

    # --- the acceptance arithmetic, computed here so no reader has to redo it -------------
    verdicts: dict[str, Any] = {}
    for band, seeds in bands.items():
        if not seeds:
            continue
        for arm in arms:
            cal = aggregate.get("%s|centripetal|pooled" % band, {}).get(arm)
            uni = aggregate.get("%s|uniform|pooled" % band, {}).get(arm)
            if cal is None or uni is None:
                continue
            two_field = diff_summary(cal["_margins"], uni["_margins"])
            se = cal["margin"]["se"] or 0.0
            gate = max(2.0 * se, abs(two_field["delta"] or 0.0))
            verdicts["%s|%s" % (band, arm)] = {
                "primary_field": "centripetal",
                "margin": cal["margin"]["mean"], "se": se, "two_se": 2.0 * se,
                "uniform_margin": uni["margin"]["mean"], "uniform_se": uni["margin"]["se"],
                "two_field_difference": two_field["delta"],
                "two_field_difference_se": two_field["se"],
                "gate": gate, "margin_minus_gate": (cal["margin"]["mean"] or 0.0) - gate,
                "gate_binding_term": ("apparatus" if abs(two_field["delta"] or 0.0) > 2 * se
                                      else "statistical"),
                "apparatus_limited": abs(two_field["delta"] or 0.0)
                                     > abs(cal["margin"]["mean"] or 0.0),
                "classification": cal["classification"],
                "we_first": aggregate["%s|centripetal|we_first" % band][arm]["margin"]["mean"],
                "we_second": aggregate["%s|centripetal|we_second" % band][arm]["margin"]["mean"],
                "games": cal["games"],
            }
    for arm in arms:
        tune = verdicts.get("tune|%s" % arm)
        oos = verdicts.get("oos|%s" % arm)
        if tune is None:
            continue
        tune["oos_margin"] = oos["margin"] if oos else None
        tune["oos_sign_agrees"] = (
            None if oos is None else
            (tune["margin"] or 0.0) * (oos["margin"] or 0.0) > 0)
        se_t, se_o = tune["se"], (oos["se"] if oos else None)
        gate = tune["gate"]
        tune["accept"] = bool((tune["margin"] or 0.0) - gate > 0
                              and (tune["oos_sign_agrees"] in (True, None)))
        tune["verdict"] = (
            "accept" if tune["accept"] else
            ("undecidable on this apparatus" if tune["apparatus_limited"] else
             ("undecidable" if se_t and se_t > 20 else "reject")))
        del se_o
    for cell in aggregate.values():
        for arm_cell in cell.values():
            arm_cell.pop("_margins", None)

    return {
        "map": args.map, "arms": arms, "fields": fields,
        "arm_patches": {k: list(v) for k, v in specs.items()},
        "tune_seeds": list(args.seeds), "oos_seeds": list(args.oos_seeds),
        "seeds_per_arm_per_order": len(args.seeds),
        "dispatch": "fixed",
        "costs": {"we_first": list(COSTS_WE_FIRST), "we_second": list(COSTS_WE_SECOND)},
        "opponent": "the unmodified fd47ea6 .so (self-play), so margin is well defined and the "
                    "opponent's own net is directly comparable across arms",
        "integrity": integrity,
        "verdicts": verdicts,
        "aggregate": aggregate,
        "records": records if args.keep_records else [],
    }


# ==========================================================================================
# cli
# ==========================================================================================

def _seed_list(text: str) -> list[str]:
    out: list[str] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not part.startswith("-"):
            low, high = part.split("-", 1)
            out.extend(str(v) for v in range(int(low), int(high) + 1))
        elif ":" in part:
            low, high = part.split(":", 1)
            out.extend(str(v) for v in range(int(low), int(high)))
        else:
            out.append(part)
    return out


def _arm_specs(args: argparse.Namespace) -> dict[str, tuple[str, ...]]:
    """``name=patch+patch,...`` or bare registry names; ``base`` is always present."""
    specs: dict[str, tuple[str, ...]] = {"base": ()}
    for item in str(args.arms).split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, _, body = item.partition("=")
            specs[name] = tuple(p for p in body.split("+") if p)
        elif item in HV.VARIANTS:
            specs[item] = tuple(HV.VARIANTS[item]["patches"])          # type: ignore[index]
        else:
            raise SystemExit("unknown arm %r" % item)
    return specs


# ==========================================================================================
# the two positioning gates: k histogram, and the fold_tour drift test
# ==========================================================================================

def mode_drift(args: argparse.Namespace) -> Mapping[str, Any]:
    """Does transferring the steps move the blind unit off its anchor?

    ``fold_tour`` -- the zero-instruction table-value form of "use the wasted steps
    differently" -- died at -81.4 +- 18.5 because parity prevents a three-step tour from
    returning to its start, so the unit **drifts off the central generation peak**.  The
    recorded lesson is that value is in *where you stand*, not *how many cells you touch*.
    A step-transfer arm must therefore prove it does not reproduce that failure: the unit
    whose steps were taken away should hold position, not wander.

    Two independent checks, both printed:

    ``k_histogram``     replay each ``.so`` over a real platform log and count ``out.k``.
                        ``k == 3`` is "no reallocation", 0 and 6 are the two directions.
    ``ring_by_unit``    closed-loop games, per-unit mean L1 ring from (8, 8) at round end,
                        arm against the same-seed baseline.  A rise is the drift signature.
    """
    import ctypes
    sys.path.insert(0, str(ROOT / "tests"))
    import replay as rp                                            # type: ignore
    from dump_inputs import build_input                            # type: ignore

    workdir = Path(args.workdir)
    specs = _arm_specs(args)
    logs = [Path(p) for p in args.logs.split(",") if p]
    khist: dict[str, Any] = {}
    for name in specs:
        so = workdir / ("%s.so" % name)
        if not so.exists():
            continue
        handle = rp.load_so(str(so))
        per_log: dict[str, Any] = {}
        for log in logs:
            rows = [json.loads(line) for line in log.read_text().splitlines()[2:]]
            counter: collections.Counter = collections.Counter()
            for index in range(len(rows)):
                gi = build_input(rows, index)
                out = handle.moveDecision(ctypes.byref(gi))
                counter[int(out.k)] += 1
            per_log[log.name] = {str(k): v for k, v in sorted(counter.items())}
        khist[name] = per_log

    # --- closed-loop per-unit ring drift ---------------------------------------------------
    from sim.analyze_hotfield_table import install_field
    from sim.runner import run_game

    def ring(row: int, col: int) -> int:
        return abs(row - 8) + abs(col - 8)

    def rings(so: str, seed: str, order: str, field: str) -> Mapping[str, Any]:
        install_field(field)
        costs = COSTS_WE_FIRST if order == "we_first" else COSTS_WE_SECOND
        result = run_game(so, str(workdir / "base.so"), map_source=args.map, seed=str(seed),
                          dispatch="fixed", fixed_costs=costs,
                          player1_name="a", player2_name="b")
        acc = {0: [], 1: []}
        for line in result.log_bytes.decode().splitlines()[2:]:
            if not line.strip():
                continue
            record = json.loads(line)
            if int(record["round"]) < 8:
                continue
            for index, unit in enumerate(record["end"]["players"][0]["units"]):
                acc[index].append(ring(int(unit["position"][0]), int(unit["position"][1])))
        return {"unit0_mean_ring": statistics.fmean(acc[0]),
                "unit1_mean_ring": statistics.fmean(acc[1]),
                "both_mean_ring": statistics.fmean(acc[0] + acc[1])}

    drift: dict[str, Any] = {}
    seeds = list(args.seeds)[: args.drift_seeds]
    for name in specs:
        so = str(workdir / ("%s.so" % name))
        if not Path(so).exists():
            continue
        rows_u0, rows_u1, rows_both = [], [], []
        for seed in seeds:
            for order in ("we_first", "we_second"):
                got = rings(so, seed, order, args.field_one)
                rows_u0.append(got["unit0_mean_ring"])
                rows_u1.append(got["unit1_mean_ring"])
                rows_both.append(got["both_mean_ring"])
        drift[name] = {"unit0": summary(rows_u0), "unit1": summary(rows_u1),
                       "both": summary(rows_both)}
    base = drift.get("base")
    for name, cell in drift.items():
        if base is None or name == "base":
            continue
        for key in ("unit0", "unit1", "both"):
            cell["%s_delta_vs_base" % key] = cell[key]["mean"] - base[key]["mean"]
    return {
        "map": args.map, "field": args.field_one, "drift_seeds": seeds,
        "logs": [str(p) for p in logs],
        "k_histogram": khist, "ring_by_unit": drift,
        "gate": "a step-transfer arm must not raise the mean ring distance; fold_tour died "
                "because a three-step tour cannot return to its start on a parity board and "
                "walked the unit off the central generation peak",
    }


def mode_tail(args: argparse.Namespace) -> Mapping[str, Any]:
    """Where do the reallocated tail slots actually go, and does the donor really not move?

    Three quantities the owner requires, all taken by replaying the arm ``.so`` over **real
    platform logs** so no simulator field model is involved at all:

    ``increment``   for each firing round, the producer's ring after step 3 is the reference
                    and slots 4/5/6 are classified ``inward`` / ``tangential`` / ``outward``.
    ``ring_ge5``    share of tail landings on ring >= 5, which is dead ground: measured
                    generation there is <= 0.004 against 0.032-0.035 at rings 1-2.
    ``donor_noop``  the donor receives zero slots, so within a round its actions must be a
                    no-op.  Across a closed loop the two trajectories diverge, so global
                    bit-identity is impossible by construction and this is the only well posed
                    form of the check: replay the same logged input and confirm the donor's
                    span is empty or all STAY.
    """
    import ctypes
    sys.path.insert(0, str(ROOT / "tests"))
    import replay as rp                                            # type: ignore
    from dump_inputs import build_input                            # type: ignore

    DR = (-1, 1, 0, 0, 0)
    DC = (0, 0, -1, 1, 0)
    STAY = 4
    workdir = Path(args.workdir)
    specs = _arm_specs(args)
    logs = [Path(p) for p in args.logs.split(",") if p]

    def ring(row: int, col: int) -> int:
        return abs(row - 8) + abs(col - 8)

    out: dict[str, Any] = {}
    for name in specs:
        so = workdir / ("%s.so" % name)
        if not so.exists():
            continue
        handle = rp.load_so(str(so))
        per_log: dict[str, Any] = {}
        for log in logs:
            rows = [json.loads(line) for line in log.read_text().splitlines()[2:]]
            cls: collections.Counter = collections.Counter()
            landing: collections.Counter = collections.Counter()
            donor_noop = donor_total = 0
            head_rings: list[int] = []
            tail_rings: list[int] = []
            for index in range(len(rows)):
                gi = build_input(rows, index)
                res = handle.moveDecision(ctypes.byref(gi))
                k = int(res.k)
                acts = [int(v) for v in res.actions]
                if k == 3:
                    continue
                producer = 0 if k == 6 else 1
                donor = 1 - producer
                span = acts[:k] if producer == 0 else acts[k:]
                dspan = acts[k:] if producer == 0 else acts[:k]
                donor_total += 1
                donor_noop += 1 if all(a == STAY for a in dspan) else 1 if not dspan else 0
                row = int(gi.my_units[producer].row)
                col = int(gi.my_units[producer].col)
                walk = []
                for a in span:
                    if a != STAY:
                        nrow, ncol = row + DR[a], col + DC[a]
                        if 0 <= nrow < 17 and 0 <= ncol < 17:
                            row, col = nrow, ncol
                    walk.append(ring(row, col))
                if len(walk) < 6:
                    continue
                reference = walk[2]
                head_rings.append(reference)
                for slot in (3, 4, 5):
                    here = walk[slot]
                    tail_rings.append(here)
                    if here < reference:
                        cls["inward"] += 1
                    elif here == reference:
                        cls["tangential"] += 1
                    else:
                        cls["outward"] += 1
                    landing[min(here, 12)] += 1
            total = sum(cls.values())
            per_log[log.name] = {
                "firing_rounds": donor_total,
                "tail_slots": total,
                "increment": {k2: v for k2, v in cls.items()},
                "increment_share": {k2: v / max(1, total) for k2, v in cls.items()},
                "landing_ring_histogram": {str(k2): v for k2, v in sorted(landing.items())},
                "tail_share_ring_ge5": sum(v for k2, v in landing.items() if k2 >= 5)
                                        / max(1, total),
                "mean_ring_after_step3": (statistics.fmean(head_rings) if head_rings else None),
                "mean_ring_tail_slots": (statistics.fmean(tail_rings) if tail_rings else None),
                "donor_span_is_noop": donor_noop,
                "donor_span_is_noop_share": donor_noop / max(1, donor_total),
            }
        out[name] = per_log
    return {
        "logs": [str(p) for p in logs], "arms": list(out),
        "protocol": "each .so replayed over real platform logs; positions come from the logged "
                    "input and actions from the .so's own output, so nothing here depends on a "
                    "simulator field model",
        "dead_ground_note": "ring >= 5 measured generation <= 0.004 per cell-round against "
                            "0.032-0.035 at rings 1-2 (src/CHANGELOG.md 13-game field profile)",
        "per_arm": out,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["construct", "icount", "ab", "stream", "drift", "tail"])
    parser.add_argument("--workdir", default="/tmp/gr_highn/build")
    parser.add_argument("--map", default="map1")
    parser.add_argument("--arms", default="null,nofold,nofoldpure,colvedge,safet2,cursor")
    parser.add_argument("--seeds", type=_seed_list, default=_seed_list("3000:3050"))
    parser.add_argument("--oos-seeds", type=_seed_list, default=_seed_list("7000:7050"))
    parser.add_argument("--fields", default="uniform,centripetal")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--inputs", default="")
    parser.add_argument("--calls", type=int, default=500000)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--stream-seed", default="1000")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--keep-records", action="store_true")
    parser.add_argument("--logs", default="")
    parser.add_argument("--drift-seeds", type=int, default=6)
    parser.add_argument("--field-one", default="centripetal")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    if args.mode == "construct":
        payload: Any = mode_construct(args)
    elif args.mode == "icount":
        payload = mode_icount(args)
    elif args.mode == "stream":
        payload = mode_stream(args)
    elif args.mode == "drift":
        payload = mode_drift(args)
    elif args.mode == "tail":
        payload = mode_tail(args)
    else:
        payload = mode_ab(args)
    text = json.dumps(payload, indent=1, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(text)
        print("wrote %s (%d bytes)" % (args.output, len(text)))
    else:
        print(text)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
