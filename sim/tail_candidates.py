#!/usr/bin/env python3
"""tail_candidates.py — derive the tail-width candidates from the baseline player.cpp.

Every candidate is produced by **exact-string substitution against the baseline**, and every
anchor is asserted to occur exactly once, so the delivered diff is reproducible from
`git show HEAD:src/player.cpp` and a drifted baseline fails loudly instead of silently
producing a different construct. Nothing here writes to `src/player.cpp`.

Knives, all behaviour-identical by construction:

  `wave`  The `%20` bomb-memory clear moves from an out-of-line `cold` call to an inline
          **masked** clear executed unconditionally. `m = -(round % 20 != 0)` makes `&= m`
          either the identity or exactly the `memset(bombbit, 0, 92)` it replaces, so
          semantics are bit-identical, but the round's *shape* no longer depends on the
          round number. Measured motive: that call costs +8 instructions yet +54..81 TSC
          ticks (about 20x the baseline ticks-per-instruction rate), because the cost is a
          far call into `.text.unlikely` plus the certain misprediction of a branch taken 5%
          of the time. Rounds that skip it pay nothing today, so the mask is a mean-for-tail
          trade priced at roughly +12 hot instructions.

  `blk`   `steerStep` and `escapeStep` stop recomputing `bpw | (rich & bombbit)` per query
          and read `blk[]`, which `decide` has already materialised over the whole reachable
          row range before the steering block runs. `pass01(r,c,rich)` is
          `~((bpw[r+1] | (rich & bombbit[r+3])) >> (c+1)) & 1` and
          `blk[i] = bpw[i] | (rich & bombbit[i+2])`, so `blk[r+1]` is the same word by
          definition; the reachable range is `r in [-1,17]` -> `blk[0..18]` and `blk` has 19
          entries, so no index moves out of bounds. Saves one load, one AND and one OR per
          query: 2 queries in the fallback, 4 more in the escape.
          The two functions also merge into ONE `noinline cold` function. `steerStep` is
          unattributed today, so it is inlined into `decide` twice (once per unit) with
          `escapeStep` as a nested cold call; one out-of-line copy removes that duplication
          from the hot region and removes the nested call from the tail path.

  ⚠️ NOT INCLUDED, and deliberately: reusing the LUT's first passability term as the
  fallback's `ok0`. It looks like the same query but is not. The LUT index is clamped to
  [-3,3] while `steerStep` receives the target unclamped, and the row-vs-column preference
  is `adr >= adc`, so clamping can flip it -- true (dr,dc)=(4,5) gives column-first
  unclamped but row-first after clamping. Blind-anchor targets are routinely further than 3
  away, so this would be a silent behaviour change.

usage: python3 sim/tail_candidates.py BASE_CPP OUT_DIR [--only wave blk both]
"""
import argparse
import os

PASS01 = """inline unsigned pass01(int r, int c, unsigned rich) {
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 3])) >> (c + 1)) & 1u);
}"""

ESCAPE_AND_STEER_OLD = '''__attribute__((noinline, cold))
int escapeStep(int r, int c, int pr, int pc, unsigned rich) {
    // \u56db\u5411\u540c\u65f6\u67e5\u8be2 + \u63a9\u7801\u9009\u9996\u8def\uff0c\u7b49\u4ef7\u4e8e\u65e7 for/continue/early-return\uff0c\u4f46\u65e0\u6570\u636e\u4f9d\u8d56\u5206\u652f\u3002
    unsigned pm = pass01(r - 1, c, rich) |
                  (pass01(r + 1, c, rich) << 1) |
                  (pass01(r, c - 1, rich) << 2) |
                  (pass01(r, c + 1, rich) << 3);
    unsigned back = (unsigned)((r - 1 == pr) & (c == pc)) |
                    ((unsigned)((r + 1 == pr) & (c == pc)) << 1) |
                    ((unsigned)((r == pr) & (c - 1 == pc)) << 2) |
                    ((unsigned)((r == pr) & (c + 1 == pc)) << 3);
    int a = __builtin_ctz((pm & ~back) | 16u);   // bit4 \u54e8\u5175: \u65e0\u8def\u65f6 a=4
    return a - 5 * (a == 4);                    // 4\u2192-1\uff0c\u5176\u4f59\u4fdd\u6301 0..3
}

int steerStep(int r, int c, int gr, int gc, int pr, int pc, unsigned rich) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;
    int ac = 2 + (dcc > 0);
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = pass01(r + DR[p0], c + DC[p0], rich);
    unsigned ok1 = pass01(r + DR[p1], c + DC[p1], rich) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)
        return ok0 ? p0 : p1;
    return (adr | adc) ? escapeStep(r, c, pr, pc, rich) : -1;
}'''

BLK_STEER_NEW = '''// blk \u7248\u901a\u884c\u67e5\u8be2: blk[r+1] == bpw[r+1] | (rich & bombbit[r+3]) \u662f\u540c\u4e00\u4e2a\u5b57,
// \u6545\u4e0e pass01 \u9010\u4f4d\u76f8\u540c; \u7701\u6389\u6bcf\u6b21\u67e5\u8be2\u7684\u4e00\u6b21\u8f7d\u5165 + AND + OR\u3002
inline unsigned passb(const uint32_t* blk, int r, int c) {
    return (~(blk[r + 1] >> (c + 1)) & 1u);
}

// \u53d7\u963b\u94fe\u5408\u5e76: \u65e7\u7248 steerStep \u672a\u6ce8\u91ca\u5c5e\u6027 \u21d2 \u88ab\u5185\u8054\u8fdb decide \u4e24\u4efd(\u6bcf\u5355\u4f4d\u4e00\u4efd),
// \u4e14 escapeStep \u662f\u5d4c\u5957\u51b7\u8c03\u7528\u3002\u5408\u5e76\u4e3a\u5355\u4efd out-of-line \u51b7\u51fd\u6570: \u70ed\u533a\u5c11\u4e24\u4efd\u526f\u672c,
// \u5c3e\u8def\u5f84\u5c11\u4e00\u6b21\u5d4c\u5957\u8c03\u7528\u3002\u884c\u4e3a\u9010\u4f4d\u4e0d\u53d8(\u4ec5\u67e5\u8be2\u6539\u8bfb\u540c\u503c\u7684 blk)\u3002
__attribute__((noinline, cold))
int blkStep(const uint32_t* blk, int r, int c, int gr, int gc, int pr, int pc) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;
    int ac = 2 + (dcc > 0);
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = passb(blk, r + DR[p0], c + DC[p0]);
    unsigned ok1 = passb(blk, r + DR[p1], c + DC[p1]) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)
        return ok0 ? p0 : p1;
    if (!(adr | adc)) return -1;
    // \u9006\u9003\u56db\u5411\u63a9\u7801 + tzcnt \u6052\u5f62(\u4e0e\u65e7 escapeStep \u9010\u4f4d\u76f8\u540c)
    unsigned pm = passb(blk, r - 1, c) |
                  (passb(blk, r + 1, c) << 1) |
                  (passb(blk, r, c - 1) << 2) |
                  (passb(blk, r, c + 1) << 3);
    unsigned back = (unsigned)((r - 1 == pr) & (c == pc)) |
                    ((unsigned)((r + 1 == pr) & (c == pc)) << 1) |
                    ((unsigned)((r == pr) & (c - 1 == pc)) << 2) |
                    ((unsigned)((r == pr) & (c + 1 == pc)) << 3);
    int a = __builtin_ctz((pm & ~back) | 16u);   // bit4 \u54e8\u5175: \u65e0\u8def\u65f6 a=4
    return a - 5 * (a == 4);                    // 4\u2192-1, \u5176\u4f59\u4fdd\u6301 0..3
}'''

CALL_OLD = """                int a = steerStep(sr, sc, tgr, tgc,
                                  g_s.last_r[u], g_s.last_c[u], rich);"""
CALL_NEW = """                int a = blkStep(blk, sr, sc, tgr, tgc,
                                g_s.last_r[u], g_s.last_c[u]);"""

WAVE_FN_OLD = '''__attribute__((noinline, cold))
void waveTick(const GameInput*) {                 // \u70b8\u5f39\u6bcf 20 \u8f6e\u6574\u5957\u91cd\u91c7\u6837
    memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
}

'''

WAVE_CALL_OLD = '''    if (in->round % 20 == 0)                     // \u70b8\u5f39\u6ce2\u6e05 + \u8fdc\u5f81\u5f00\u62d4/\u5f52\u961f(\u9a91\u540c\u4e00\u4f4d\u70b9)
        waveTick(in);'''

WAVE_CALL_NEW = '''    {   // \u70b8\u5f39\u6ce2\u6e05: \u6052\u5f62\u63a9\u7801\u7248\u3002\u65e7\u5f62\u5f0f `if(%20==0) waveTick()` \u628a 5% \u7684\u8f6e
        // \u63a8\u8fdb\u5c3e\u90e8: \u5b83\u53ea\u591a\u8dd1 8 \u6761\u6307\u4ee4\u5374\u591a\u82b1 54..81 TSC ticks(\u7ea6\u57fa\u7ebf\u5355\u4ef7\u7684 20 \u500d),
        // \u56e0\u4e3a\u4ee3\u4ef7\u4e0d\u5728\u6307\u4ee4\u800c\u5728\u8df3\u5165 .text.unlikely \u7684\u8fdc\u8c03\u7528 + 5% \u5206\u652f\u7684\u5fc5\u7136\u8bef\u9884\u6d4b\u3002
        // m==0 \u65f6 `&= m` \u4e0e memset(bombbit,0,92) \u9010\u4f4d\u76f8\u540c, m==~0 \u65f6\u662f\u6052\u7b49 \u21d2 \u884c\u4e3a\u4e0d\u53d8,
        // \u4f46\u6bcf\u8f6e\u5f62\u72b6\u76f8\u540c\u3002\u4ee3\u4ef7\u662f\u6bcf\u8f6e\u767d\u4ed8\u7ea6 12 \u6761\u70ed\u6307\u4ee4(92B = 2 ymm + \u5c3e)\u3002
        uint32_t wm = 0u - (uint32_t)(in->round % 20 != 0);
        for (int i = 0; i < N + 6; ++i) g_s.bombbit[i] &= wm;
    }'''


STEER_SITE_OLD = '''            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
                pext = pa;                       // \u53ef\u7eed\u5199: \u5c3e\u6bb5\u4e0e\u672c\u6bb5\u540c\u8868\u540c\u76ee\u6807
            } else {
                // \u53d7\u963b(\u7f55\u89c1, \u9501\u56fe\u540e\u5899\u5168\u77e5): \u5355\u6b65\u8c28\u614e, \u5176\u4f59 STAY, \u4e0b\u8f6e\u81ea\u6108
                int a = blkStep(blk, sr, sc, tgr, tgc,
                                g_s.last_r[u], g_s.last_c[u]);
                if (a >= 0) acts[0] = a;
            }'''

STEER_SITE_CST = '''            // \u6052\u5f62\u5bfc\u5411: blkStep \u65e0\u6761\u4ef6\u8c03\u7528, \u7ed3\u679c\u7528\u63a9\u7801\u9009\u62e9\u3002\u65e7\u5f62\u5f0f\u7684 `if (ok)`
            // \u662f\u4e00\u4e2a 33-40% \u5206\u652f, \u5b83\u7684\u65b9\u5411\u7531\u5730\u5f62\u6570\u636e\u51b3\u5b9a \u21d2 \u4e0d\u53ef\u9884\u6d4b;
            // \u800c wave \u5b9e\u6d4b\u5df2\u8bc1\u660e "8 \u6761\u6307\u4ee4 + \u4e00\u6b21\u8bef\u9884\u6d4b + \u4e00\u6b21\u8fdc\u8c03\u7528" \u503c 54..81 ticks\u3002
            // \u672c\u53d8\u4f53\u628a\u90a3\u7b14\u4ee3\u4ef7\u4ece 40% \u7684\u8f6e\u6b21\u642c\u5230 100% \u7684\u8f6e\u6b21(\u6bcf\u8f6e\u767d\u4ed8 blkStep),
            // \u7528 P50 \u4e70 P90\u3002\u884c\u4e3a\u9010\u4f4d\u76f8\u540c: acts \u5df2\u9884\u7f6e STAY, \u6545 else \u5206\u652f\u7684\u8bed\u4e49\u5c31\u662f
            // acts[0] = a>=0 ? a : STAY, acts[1..2] = STAY, pext = nullptr\u3002
            int a_ = blkStep(blk, sr, sc, tgr, tgc, g_s.last_r[u], g_s.last_c[u]);
            {
                int okm = -(int)(ok != 0);        // ok \u65f6\u5168 1
                int nam = -(int)(a_ < 0);         // \u65e0\u8def\u65f6\u5168 1
                int fb = (a_ & ~nam) | (STAY & nam);
                acts[0] = (pa[0] & okm) | (fb & ~okm);
                acts[1] = (pa[1] & okm) | (STAY & ~okm);
                acts[2] = (pa[2] & okm) | (STAY & ~okm);
                pext = ok ? pa : nullptr;
            }'''

BLKSTEP_HOT = ('__attribute__((noinline, cold))\nint blkStep(',
               '__attribute__((noinline))\nint blkStep(')


def apply(src, pairs):
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit("anchor found %d times (need 1); baseline drifted:\n  %r"
                             % (n, old[:110]))
        src = src.replace(old, new)
    return src


WAVE = [(WAVE_FN_OLD, ""), (WAVE_CALL_OLD, WAVE_CALL_NEW)]
BLK = [(PASS01, PASS01), (ESCAPE_AND_STEER_OLD, BLK_STEER_NEW), (CALL_OLD, CALL_NEW)]
# `cst` extends `blk`: the steering branch itself becomes constant-shaped. blkStep stops
# being `cold` because it is now on every round, so `.text.unlikely` would be wrong.
CST = BLK + [BLKSTEP_HOT, (STEER_SITE_OLD, STEER_SITE_CST)]
CANDIDATES = {"wave": WAVE, "blk": BLK, "both": WAVE + BLK,
              "cst": CST, "all": WAVE + CST}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_cpp")
    ap.add_argument("out_dir")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    with open(args.base_cpp, encoding="utf-8") as fh:
        base = fh.read()
    os.makedirs(args.out_dir, exist_ok=True)
    for name, pairs in CANDIDATES.items():
        if args.only and name not in args.only:
            continue
        out = os.path.join(args.out_dir, "cand_%s.cpp" % name)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(apply(base, pairs))
        print("wrote %s" % out)


if __name__ == "__main__":
    main()
