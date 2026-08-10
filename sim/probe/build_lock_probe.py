#!/usr/bin/env python3
"""Build instrumented copies of the delivered construct for the lock audit.

The construct under test is ``git show fd47ea6:src/player.cpp`` -- the commit
that contains the mis-lock repair.  ``src/player.cpp`` is owned by the operator
and is never modified here; the source is extracted to a temporary directory and
patched textually, so every difference from the delivered build is explicit and
auditable in one place.

Variants
--------
``base``      the extracted source, unmodified.  Reference for the equivalence
              check that proves the instrumentation is inert.
``probe``     ``base`` + observers.  ``VERIFY_ROUNDS`` untouched (24), so this is
              the *delivered* detection policy with a timeline attached.
``probeinf``  ``probe`` with ``VERIFY_ROUNDS`` raised to 100000, i.e. the
              unbounded re-check.  The round at which its conflict fires is the
              round the delivered build *would* have needed the window to reach.
``force0/1/2`` ``probe`` with the fingerprint round bypassed: the map is locked
              to map1/map2/map3 at round 0 whatever the terrain is.  Used for the
              deliberately-wrong-lock arm of the dry run.

Observers added (read-only; they never influence an action)
----------------------------------------------------------
* per-round timeline of ``map_id`` / ``mode`` / verification scans
* the round the lock is taken and the table it is taken to
* the round the *mechanism* first detects a contradiction (gated on both
  ``visited`` and ``round <= VERIFY_ROUNDS``)
* the round a contradiction is first *visible* -- the same 5x5 window test run
  every round with the ``visited`` gate and the round bound both removed.  This
  is the earliest round any window-based detector could possibly fire, so it
  separates "the window closed too early" from "the evidence was never in view".
* counters for scans and cells compared, which is the quantity the known-map
  premium is proportional to.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SHA = "df270cd3d638046d6a90d4c6ccabd540759d8a66aa5cfa59fecc357db1bae217"
COMMIT = "fd47ea6"

PREFETCH_SHIM = """#pragma once
#define _MM_HINT_T0 0
#define _mm_prefetch(address, hint) ((void)0)
"""

# ---------------------------------------------------------------------------
# instrumentation fragments
# ---------------------------------------------------------------------------

PROBE_STATE = r"""
// ===================== audit instrumentation (read-only) =====================
struct ProbeRec {
    int lock_round;          // round map_id first became >= 0 (-1 = never)
    int lock_to;             // which baked table was locked first (-1 = none)
    int unknown_round;       // round map_id first became -2 (-1 = never)
    int mech_conflict_round; // round the shipped mechanism fired (-1 = never)
    int mech_conflict_count;
    int visible_conflict_round;  // earliest round a contradiction was in a window
    int relock_round;        // round a *second* lock happened after a revision
    int scan_calls;          // per-unit window scans (learn or verify)
    int verify_scans;        // per-unit window scans taken while locked
    int cells_compared;      // cells actually compared against a baked table
    int cells_learned;       // cells written into the online wall bitmap
    int rounds_seen;
    int slowtick_calls;
    signed char map_id_by_round[512];
    unsigned char mode_by_round[512];
    unsigned char scans_by_round[512];
    unsigned char verify_by_round[512];
};
ProbeRec g_pr;

void probeReset() {
    memset(&g_pr, 0, sizeof(g_pr));
    g_pr.lock_round = -1;
    g_pr.lock_to = -1;
    g_pr.unknown_round = -1;
    g_pr.mech_conflict_round = -1;
    g_pr.visible_conflict_round = -1;
    g_pr.relock_round = -1;
}

// The same window test the mechanism performs, with the ``visited`` gate and the
// ``round <= VERIFY_ROUNDS`` bound removed.  Called every round; writes only to
// g_pr, never to g_s, so the played trajectory is unchanged.
void probeVisible(const GameInput* in) {
    if (g_pr.visible_conflict_round >= 0) return;
    if (g_s.map_id < 0) return;
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int r0 = sr - 2 < 0 ? 0 : sr - 2, r1 = sr + 2 > 16 ? 16 : sr + 2;
        int c0 = sc - 2 < 0 ? 0 : sc - 2, c1 = sc + 2 > 16 ? 16 : sc + 2;
        for (int r = r0; r <= r1; ++r)
            for (int c = c0; c <= c1; ++c) {
                int v = in->grid[r][c];
                if (v == -5) continue;
                unsigned isw = (unsigned)(v == -1);
                if (isw ^ ((g_s.bpw[r + 1] >> (c + 1)) & 1u)) {
                    g_pr.visible_conflict_round = in->round;
                    return;
                }
            }
    }
}
// =================== end audit instrumentation =====================
"""

PROBE_EXPORTS = r"""
extern "C" {
int probe_map_id() { return (int)g_s.map_id; }
int probe_mode() { return (int)g_s.mode; }
int probe_lock_round() { return g_pr.lock_round; }
int probe_lock_to() { return g_pr.lock_to; }
int probe_unknown_round() { return g_pr.unknown_round; }
int probe_mech_conflict_round() { return g_pr.mech_conflict_round; }
int probe_mech_conflict_count() { return g_pr.mech_conflict_count; }
int probe_visible_conflict_round() { return g_pr.visible_conflict_round; }
int probe_relock_round() { return g_pr.relock_round; }
int probe_scan_calls() { return g_pr.scan_calls; }
int probe_verify_scans() { return g_pr.verify_scans; }
int probe_cells_compared() { return g_pr.cells_compared; }
int probe_cells_learned() { return g_pr.cells_learned; }
int probe_rounds_seen() { return g_pr.rounds_seen; }
int probe_slowtick_calls() { return g_pr.slowtick_calls; }
int probe_verify_rounds() { return VERIFY_ROUNDS; }
int probe_force_lock() {
#ifdef PROBE_FORCE_LOCK
    return PROBE_FORCE_LOCK;
#else
    return -1;
#endif
}
const signed char* probe_map_id_by_round() { return g_pr.map_id_by_round; }
const unsigned char* probe_mode_by_round() { return g_pr.mode_by_round; }
const unsigned char* probe_scans_by_round() { return g_pr.scans_by_round; }
const unsigned char* probe_verify_by_round() { return g_pr.verify_by_round; }
const unsigned int* probe_bpw() { return g_s.bpw; }
}
"""


def _sub_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit("patch anchor %r matched %d times, expected 1"
                         % (label, text.count(old)))
    return text.replace(old, new)


def instrument(source: str) -> str:
    text = source

    # 1. probe state + the visible-conflict observer, right after the wall tables.
    text = _sub_once(
        text,
        "constexpr int VERIFY_ROUNDS = 24;",
        "constexpr int VERIFY_ROUNDS = 24;\n" + PROBE_STATE,
        "verify_rounds_decl",
    )

    # 2. count per-unit scans, and split learn from verify.
    text = _sub_once(
        text,
        "        learned = 1;\n",
        "        learned = 1;\n"
        "        ++g_pr.scan_calls;\n"
        "        if (g_s.map_id >= 0) ++g_pr.verify_scans;\n"
        "        if (in->round < 512) {\n"
        "            if (g_pr.scans_by_round[in->round] < 255) ++g_pr.scans_by_round[in->round];\n"
        "            if (g_s.map_id >= 0 && g_pr.verify_by_round[in->round] < 255)\n"
        "                ++g_pr.verify_by_round[in->round];\n"
        "        }\n",
        "scan_counter",
    )
    text = _sub_once(
        text,
        "                    conflict |= isw ^ ((g_s.bpw[r + 1] >> (c + 1)) & 1u);",
        "                    ++g_pr.cells_compared;\n"
        "                    conflict |= isw ^ ((g_s.bpw[r + 1] >> (c + 1)) & 1u);",
        "cells_compared",
    )
    text = _sub_once(
        text,
        "                    g_s.seen[r] |= 1u << (c + 1);",
        "                    ++g_pr.cells_learned;\n"
        "                    g_s.seen[r] |= 1u << (c + 1);",
        "cells_learned",
    )

    # 3. the mechanism's own conflict event.
    text = _sub_once(
        text,
        "        g_s.map_id = -2; g_s.cand = 0; g_s.mode = 2;",
        "        if (g_pr.mech_conflict_round < 0) g_pr.mech_conflict_round = in->round;\n"
        "        ++g_pr.mech_conflict_count;\n"
        "        g_s.map_id = -2; g_s.cand = 0; g_s.mode = 2;",
        "mech_conflict",
    )

    # 4. lock / unknown events inside the fingerprint round.
    text = _sub_once(
        text,
        "            g_s.map_id = -2;                     // \u964c\u751f\u56fe: \u61d2\u5b66\u4e60\u4f34\u7ec8\u5c40",
        "            if (g_pr.unknown_round < 0) g_pr.unknown_round = in->round;\n"
        "            g_s.map_id = -2;                     // \u964c\u751f\u56fe: \u61d2\u5b66\u4e60\u4f34\u7ec8\u5c40",
        "unknown_event",
    )
    text = _sub_once(
        text,
        "            g_s.map_id = (int8_t)m;",
        "            if (g_pr.lock_round < 0) { g_pr.lock_round = in->round; g_pr.lock_to = m; }\n"
        "            else if (g_pr.relock_round < 0) g_pr.relock_round = in->round;\n"
        "            g_s.map_id = (int8_t)m;",
        "lock_event",
    )

    # 5. slowTick call counter.
    text = _sub_once(
        text,
        "void slowTick(const GameInput* in) {\n",
        "void slowTick(const GameInput* in) {\n    ++g_pr.slowtick_calls;\n",
        "slowtick_counter",
    )

    # 6. reset on new game, and the per-round timeline + visible observer.
    text = _sub_once(
        text,
        "        g_s.mode = 1; g_s.map_id = -1; g_s.cand = 7;",
        "        g_s.mode = 1; g_s.map_id = -1; g_s.cand = 7;\n"
        "        probeReset();\n"
        "#ifdef PROBE_FORCE_LOCK\n"
        "        {   // deliberately-wrong-lock arm: bypass the fingerprint round\n"
        "            int fm = PROBE_FORCE_LOCK;\n"
        "            g_s.map_id = (int8_t)fm; g_s.cand = (uint8_t)(1u << fm);\n"
        "            for (int r = 0; r < N; ++r)\n"
        "                g_s.bpw[r + 1] = 0xFFFC0001u | BAKED_W[fm][r];\n"
        "            g_pr.lock_round = 0; g_pr.lock_to = fm;\n"
        "        }\n"
        "#endif\n",
        "reset_hook",
    )
    # The visible-conflict observer runs BOTH before and after the gate, so the
    # round it reports is the earliest round a contradiction sat in a unit's
    # window -- including the round in which the mechanism itself fires and then
    # clears the lock, which an after-only observer would miss.
    text = _sub_once(
        text,
        "    if (__builtin_expect(g_s.mode == 1\n",
        "    probeVisible(in);\n"
        "    if (__builtin_expect(g_s.mode == 1\n",
        "pre_gate_hook",
    )
    text = _sub_once(
        text,
        "        slowTick(in);\n",
        "        slowTick(in);\n"
        "    probeVisible(in);\n"
        "    ++g_pr.rounds_seen;\n"
        "    if (in->round < 512) {\n"
        "        g_pr.map_id_by_round[in->round] = g_s.map_id;\n"
        "        g_pr.mode_by_round[in->round] = g_s.mode;\n"
        "    }\n",
        "round_hook",
    )

    # 7. exported readers, after the anonymous namespace closes.
    text = _sub_once(
        text,
        "}  // namespace\n",
        "}  // namespace\n" + PROBE_EXPORTS,
        "exports",
    )
    return text


def extract() -> str:
    raw = subprocess.run(
        ["git", "show", "%s:src/player.cpp" % COMMIT],
        cwd=str(ROOT), check=True, capture_output=True,
    ).stdout
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA:
        raise SystemExit("extracted %s:src/player.cpp sha256=%s, expected %s"
                         % (COMMIT, digest, EXPECTED_SHA))
    return raw.decode("utf-8")


def compile_one(src: Path, out: Path, include: Path, defines: list[str]) -> None:
    cmd = ["clang++", "-O2", "-std=c++17", "-shared", "-fPIC",
           "-I", str(include), "-include", str(include / "immintrin.h"),
           *["-D%s" % d for d in defines], str(src), "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit("compile failed: %s" % " ".join(cmd))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outdir", type=Path, default=Path("/tmp/umr"))
    args = parser.parse_args()
    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    source = extract()
    (outdir / "immintrin.h").write_text(PREFETCH_SHIM, encoding="utf-8")
    shutil.copy2(ROOT / "src" / "game_api.h", outdir / "game_api.h")

    base_src = outdir / "player_base.cpp"
    base_src.write_text(source, encoding="utf-8")
    probe_src = outdir / "player_probe.cpp"
    probe_src.write_text(instrument(source), encoding="utf-8")
    inf_src = outdir / "player_probe_inf.cpp"
    inf_text = instrument(source).replace("constexpr int VERIFY_ROUNDS = 24;",
                                          "constexpr int VERIFY_ROUNDS = 100000;", 1)
    if "VERIFY_ROUNDS = 100000" not in inf_text:
        raise SystemExit("failed to widen VERIFY_ROUNDS")
    inf_src.write_text(inf_text, encoding="utf-8")

    built = {}
    compile_one(base_src, outdir / "base.dylib", outdir, [])
    built["base"] = outdir / "base.dylib"
    compile_one(probe_src, outdir / "probe.dylib", outdir, [])
    built["probe"] = outdir / "probe.dylib"
    compile_one(inf_src, outdir / "probe_inf.dylib", outdir, [])
    built["probe_inf"] = outdir / "probe_inf.dylib"
    for m in (0, 1, 2):
        out = outdir / ("probe_force%d.dylib" % m)
        compile_one(probe_src, out, outdir, ["PROBE_FORCE_LOCK=%d" % m])
        built["probe_force%d" % m] = out

    print("commit          %s" % COMMIT)
    print("source sha256   %s" % hashlib.sha256(source.encode()).hexdigest())
    for name, path in built.items():
        print("%-14s %s  (%d bytes)" % (name, path, path.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
