#!/usr/bin/env python3
"""tail_trace_patch.py — derive a path-labelling build from a baseline player.cpp.

The tail-width question is "which code path do the slowest rounds fall on". Answering it
needs two things that must NOT be the same binary:

  * the **timings**, which have to come from the unmodified construct, because any
    instrumentation changes footprint and branch shape — the very thing being measured; and
  * the **labels**, which come from this derived build.

Joining them across passes is only legitimate because the label stream is a deterministic
function of the input stream: `decide` is a state machine driven solely by `GameInput`, and
round 0 resets it. `tail_path_bench --mask-only` re-derives the labels twice and refuses to
emit them unless the two passes agree, and `tests/pair_diff.py` must report 0/500 on three
maps for this build against the baseline — otherwise the labels describe a different
program than the one that was timed.

Hooks are exact-string substitutions and every one of them is asserted, so a drift in the
baseline source fails loudly instead of silently mislabelling a path.

Bit layout of `tailTrace()`:
    0  unit 0 took the blocked branch (steerStep)     5  new-game reset ran
    1  unit 1 took the blocked branch (steerStep)     6  unit 0 target was the blind anchor
    2  escapeStep was entered (either unit)           7  unit 1 target was the blind anchor
    3  slowTick ran (slow-start cold layer)           9  unit 0 rich (gold >= 100)
    4  waveTick ran (round % 20 bomb-memory clear)   10  unit 1 rich (gold >= 100)
    8  slowMove ran (opening march / expedition)

usage: python3 sim/tail_trace_patch.py base_player.cpp trace_player.cpp
"""
import sys

#: (anchor, insertion, where) — where is 'after' or 'before'; every anchor must be unique.
HOOKS = [
    # state + declaration
    ("constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};",
     "\nuint32_t g_trace;                                // path label for this round", "after"),
    # escapeStep entry (bit 2)
    ("int escapeStep(int r, int c, int pr, int pc, unsigned rich) {",
     "\n    g_trace |= 1u << 2;", "after"),
    # waveTick entry (bit 4)
    ("void waveTick(const GameInput*) {",
     "\n    g_trace |= 1u << 4;", "after"),
    # slowTick entry (bit 3)
    ("void slowTick(const GameInput* in) {",
     "\n    g_trace |= 1u << 3;", "after"),
    # slowMove entry (bit 8)
    ("void slowMove(const GameInput* in, int u, int sr, int sc, unsigned rich, int* acts) {",
     "\n    g_trace |= 1u << 8;", "after"),
    # decide entry: clear
    ("GameOutput decide(const GameInput* in) {",
     "\n    g_trace = 0;", "after"),
    # new-game reset (bit 5)
    ("        memset(&g_s, 0, sizeof(g_s));",
     "\n        g_trace |= 1u << 5;", "after"),
    # rich gate (bits 9,10)
    ("        unsigned rich = 0u - (unsigned)(in->my_units_gold[u] >= 100);",
     "\n        g_trace |= (rich & 1u) << (9 + u);", "after"),
    # blind target (bits 6,7) — placed after the target block has assigned `blind`
    ("        uint32_t blk[N + 2];",
     "        g_trace |= (unsigned)(blind != 0) << (6 + u);\n", "before"),
    # blocked branch taken (bits 0,1)
    ("            } else {\n"
     "                // \u53d7\u963b(\u7f55\u89c1, \u9501\u56fe\u540e\u5899\u5168\u77e5): \u5355\u6b65\u8c28\u614e, "
     "\u5176\u4f59 STAY, \u4e0b\u8f6e\u81ea\u6108",
     "\n                g_trace |= 1u << u;", "after"),
]

EXPORT = '\nextern "C" uint32_t tailTrace() { return g_trace; }\n'


def patch(src: str) -> str:
    for anchor, insertion, where in HOOKS:
        n = src.count(anchor)
        if n != 1:
            raise SystemExit(
                "hook anchor found %d times (need exactly 1); baseline drifted:\n  %r"
                % (n, anchor[:90]))
        if where == "after":
            src = src.replace(anchor, anchor + insertion)
        else:
            src = src.replace(anchor, insertion + anchor)
    return src + EXPORT


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    with open(sys.argv[1], encoding="utf-8") as fh:
        out = patch(fh.read())
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(out)
    print("wrote %s (%d hooks)" % (sys.argv[2], len(HOOKS)))
