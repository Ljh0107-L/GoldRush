// src/v5/player.cpp — v5: 200ns 信封版(2026-08-08)
//
// 目的: 先量出 200ns 在平台上到底装得下什么(可用资源勘定), 算法从预算向上生长。
// 三元门禁: 本地计算 <=250 周期 / 分支位点 <=15 / .text <=4KB
//
// 功能(极简但正确):
//   - 双单位每轮 5x5 掩码扫描(rowbuf 前置装载, miss 并行)
//   - 最近金格目标(环掩码 cmov 链, 零内存); 无金 -> 分区锚点
//   - LUT 三步导向(途经格并行验证) + 罕见回落串行链
//   - 站金折返双吃(65% 链式)
//   - 炸弹规避 = 按持金气门: 穷(<100)弹透明(烧10%x0=0), 富绕行
//     (一条 AND 替代 v3/v4 的双位图+护栏+波重建全家桶)
// 砍掉(以后按预算加回): 矿堆缓存/巡逻表/护栏/卡死机/阶段A B/视野/敌情/快照
#include <cstdint>
#include <cstring>
#ifdef NS5DBG
#include <cstdio>
#endif
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

struct alignas(64) State {
    uint32_t bpw[N + 2];     // 墙|边界(哨兵位图; 弹不入内)
    uint32_t bombbit[N + 2]; // 炸弹位图(+1 偏移对齐 bpw; 波清)
    int8_t last_r[2], last_c[2];
    int8_t can0[2], can1[2], can2[2];   // v5s: 罐头 3 步(主动轮存, 被动轮回放)
    int16_t last_round;
};
State g_s;

constexpr int8_t ANCH_R[2] = {6, 10};
constexpr int8_t ANCH_C[2] = {6, 10};

// 通行 = 非墙 且 (穷 或 非弹) 且 非队友
inline unsigned pass01(int r, int c, int tr, int tc, unsigned rich) {
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 1])) >> (c + 1)) & 1u) &
           (unsigned)((r != tr) | (c != tc));
}

__attribute__((noinline, cold))
int escapeStep(int r, int c, int tr, int tc, int pr, int pc, unsigned rich) {
    for (int a = 0; a < 4; ++a) {
        int nr = r + DR[a], nc = c + DC[a];
        if (nr == pr && nc == pc) continue;
        if (pass01(nr, nc, tr, tc, rich)) return a;
    }
    return -1;
}

int steerStep(int r, int c, int gr, int gc, int tr, int tc,
              int pr, int pc, unsigned rich) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;
    int ac = 2 + (dcc > 0);
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = pass01(r + DR[p0], c + DC[p0], tr, tc, rich);
    unsigned ok1 = pass01(r + DR[p1], c + DC[p1], tr, tc, rich) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)
        return ok0 ? p0 : p1;
    return (adr | adc) ? escapeStep(r, c, tr, tc, pr, pc, rich) : -1;
}

// LUT 导向: (dr,dc)∈[-3,3]² 行优先无阻挡模拟 —— v5o 打包: 每格一个 u32
// [acts 3x3b @0..8 | (pdr+3) 3x3b @9..17 | (pdc+3) 3x3b @18..26]
struct SLut {
    uint8_t act[7][7][3];
    int8_t  pdr[7][7][3], pdc[7][7][3];
    uint32_t pk[7][7];
    constexpr SLut() : act(), pdr(), pdc(), pk() {
        for (int dr = -3; dr <= 3; ++dr)
            for (int dc = -3; dc <= 3; ++dc) {
                int r = 0, c = 0;
                for (int i = 0; i < 3; ++i) {
                    int rr = dr - r, cc = dc - c;
                    int adr = rr < 0 ? -rr : rr, adc = cc < 0 ? -cc : cc;
                    uint8_t a = STAY;
                    if (adr | adc) {
                        if (adr >= adc) { a = rr > 0 ? 1 : 0; r += rr > 0 ? 1 : -1; }
                        else            { a = cc > 0 ? 3 : 2; c += cc > 0 ? 1 : -1; }
                    }
                    act[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                    pk[dr + 3][dc + 3] |= ((uint32_t)a << (i * 3)) |
                        ((uint32_t)(r + 3) << (9 + i * 3)) |
                        ((uint32_t)(c + 3) << (18 + i * 3));
                }
            }
    }
};
constexpr SLut SL;

GameOutput decide(const GameInput* in) {
#ifdef NS5ALT
    const int act5 = in->round & 1;
#endif
#if defined(__AVX2__)
    // 输入行前置装载: 10 条行 miss 最早并行发射
    __m256i rowbufs[2][5];
    int rb_oks[2] = {0, 0}, rb_cbs[2] = {0, 0};
    for (int lu = 0; lu < 2; ++lu) {
#ifdef NS5ALT
        if (lu != act5) continue;              // 轮换: 被动单位零行装载
#endif
        int sr0 = in->my_units[lu].row, sc0 = in->my_units[lu].col;
        sr0 = sr0 < 0 ? 0 : (sr0 > 16 ? 16 : sr0);
        sc0 = sc0 < 0 ? 0 : (sc0 > 16 ? 16 : sc0);
        {
            int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
            rb_oks[lu] = 1; rb_cbs[lu] = cb;
#ifdef NS5ROWS3
#pragma GCC unroll 3
            for (int i = 1; i < 4; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbufs[lu][i] =
                    _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbufs[lu][i] =
                    _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
#endif
        }
    }
#endif
    if (in->round <= g_s.last_round) {           // 新局
        memset(&g_s, 0, sizeof(g_s));
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u;
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0)                     // 炸弹波: 弹记忆即弃
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));

    GameOutput out = SAFE_OUT;

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        sr = sr < 0 ? 0 : (sr > 16 ? 16 : sr);        // 单位恒在板上; cmov 双钳防御
        sc = sc < 0 ? 0 : (sc > 16 ? 16 : sc);
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
#ifdef NS5ALT
        if (u != act5) {                       // 被动轮: 罐头回放(~12 指令)
            acts[0] = g_s.can0[u]; acts[1] = g_s.can1[u]; acts[2] = g_s.can2[u];
            continue;
        }
#endif
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        unsigned rich = 0u - (unsigned)(in->my_units_gold[u] >= 100);

        // ---- 掩码扫描 ----
        uint32_t goldm = 0;
#if defined(__AVX2__)
        {
            const __m256i vz = _mm256_setzero_si256();
            const __m256i vm1 = _mm256_set1_epi32(-1);
            const __m256i vm3 = _mm256_set1_epi32(-3);
            int cb = rb_cbs[u];
            int lsh = 2 + (sc - 2 - cb);
            int lo = sc - 2 < 0 ? -(sc - 2) : 0;
            int hix = sc + 2 > N - 1 ? sc + 2 - (N - 1) : 0;
            uint32_t colv = ((31u >> hix) & (31u << lo)) & 31u;
            uint32_t wallm = 0, bombm = 0;
#ifdef NS5ROWS3
#pragma GCC unroll 3
            for (int i = 1; i < 4; ++i) {
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
#endif
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                __m256i vrow = rowbufs[u][i];
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, vz)));
                uint32_t w8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm1)));
                uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm3)));
                uint32_t rv = colv & rowok;
                goldm |= ((((g8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                wallm |= ((((w8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                bombm |= ((((b8 << 2) >> lsh) & 31u) & rv) << (i * 5);
            }
            // 墙/弹入位图: 无条件 5 行行片写(零分支; 空片写=无操作)
            // 行片: 窗口行 i 的 5 位 << (sc-2+1); 行索引钳位由 rowok 已保证片为 0
#ifdef NS5ROWS3
#pragma GCC unroll 3
            for (int i = 1; i < 4; ++i) {
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
#endif
                int rr = sr - 2 + i;
                int ri = ((unsigned)rr < (unsigned)N ? rr : 0) + 1;
                int shl = sc - 1;                // (sc-2)+1, 可为负
                uint32_t wsl = (wallm >> (i * 5)) & 31u;
                uint32_t bsl = (bombm >> (i * 5)) & 31u;
                uint32_t wv = shl >= 0 ? (wsl << shl) : (wsl >> -shl);
                uint32_t bv = shl >= 0 ? (bsl << shl) : (bsl >> -shl);
                g_s.bpw[ri] |= wv;
                g_s.bombbit[ri] |= bv;
            }
        }
#else
        for (int i = 0; i < 5; ++i) {            // 标量参考(本机测试)
            int rr = sr - 2 + i;
            if ((unsigned)rr >= (unsigned)N) continue;
            for (int j = 0; j < 5; ++j) {
                int cc = sc - 2 + j;
                if ((unsigned)cc >= (unsigned)N) continue;
                int v = in->grid[rr][cc];
                if (v > 0) goldm |= 1u << (i * 5 + j);
                else if (v == -1) g_s.bpw[rr + 1] |= 1u << (cc + 1);
                else if (v == -3) g_s.bombbit[rr + 1] |= 1u << (cc + 1);
            }
        }
#endif
        // (金格与弹格规则上不共存 —— 目标无需剔弹, 途中避弹由 pass01 管)

        // ---- 目标: 最近金(环掩码) 否则锚点 ----
        int tgr, tgc;
        {
            constexpr uint32_t RM0 = 1u << 12;
            constexpr uint32_t RM1 = (1u<<7)|(1u<<11)|(1u<<13)|(1u<<17);
            constexpr uint32_t RM2 = (1u<<2)|(1u<<6)|(1u<<8)|(1u<<10)|(1u<<14)|(1u<<16)|(1u<<18)|(1u<<22);
            constexpr uint32_t RM3 = (1u<<1)|(1u<<3)|(1u<<5)|(1u<<9)|(1u<<15)|(1u<<19)|(1u<<21)|(1u<<23);
            constexpr uint32_t RM4 = (1u<<0)|(1u<<4)|(1u<<20)|(1u<<24);
            uint32_t g1 = goldm & RM1, g2 = goldm & RM2, g3 = goldm & RM3;
            uint32_t g4 = goldm & RM4, g0 = goldm & RM0;
            uint32_t m1 = (uint32_t)0 - (g1 != 0);
            uint32_t m2 = ((uint32_t)0 - (g2 != 0)) & ~m1;
            uint32_t m3 = ((uint32_t)0 - (g3 != 0)) & ~m1 & ~m2;
            uint32_t m4 = ((uint32_t)0 - (g4 != 0)) & ~m1 & ~m2 & ~m3;
            uint32_t m0 = ~m1 & ~m2 & ~m3 & ~m4;
            uint32_t sel = (g1 & m1) | (g2 & m2) | (g3 & m3) | (g4 & m4) | (g0 & m0);
            int i = __builtin_ctz(sel | (uint32_t)(sel == 0));   // 仅空时补位(| 1u 恒补是v4d/v5崩盘元凶)
            int has = -(int)(goldm != 0);
            tgr = ((sr - 2 + i / 5) & has) | (ANCH_R[u] & ~has);
            tgc = ((sc - 2 + i % 5) & has) | (ANCH_C[u] & ~has);
        }

        // ---- 导向(v5n 全无分支): LUT 恒查 + cmov 回落 + 折返掩码合流 ----
        int dr0 = tgr - sr, dc0 = tgc - sc;
        dr0 = dr0 < -3 ? -3 : (dr0 > 3 ? 3 : dr0);
        dc0 = dc0 < -3 ? -3 : (dc0 > 3 ? 3 : dc0);
        int d = (dr0 < 0 ? -dr0 : dr0) + (dc0 < 0 ? -dc0 : dc0);
        {
            int ir = dr0 + 3, ic = dc0 + 3;
            uint32_t K = SL.pk[ir][ic];
            // 位图行提升: sr-1..sr+1 与目标途经行(<=sr±3) —— 覆盖全部 pass01
            // 途经/邻格行范围 sr-3..sr+3; 提升 7 行成本高, 折中: 用行索引直读
            // (v5o: pass01 宏内联为位测试, 行地址由编译器 CSE; 关键是消字节载)
            int x0r = sr + (int)((K >> 9) & 7) - 3, x0c = sc + (int)((K >> 18) & 7) - 3;
            int x1r = sr + (int)((K >> 12) & 7) - 3, x1c = sc + (int)((K >> 21) & 7) - 3;
            int x2r = sr + (int)((K >> 15) & 7) - 3, x2c = sc + (int)((K >> 24) & 7) - 3;
            unsigned okl = pass01(x0r, x0c, tr, tc, rich) &
                           pass01(x1r, x1c, tr, tc, rich) &
                           pass01(x2r, x2c, tr, tc, rich);
            int ar_ = dr0 > 0, ac_ = 2 + (dc0 > 0);
            int adr_ = dr0 < 0 ? -dr0 : dr0, adc_ = dc0 < 0 ? -dc0 : dc0;
            int rowf_ = adr_ >= adc_;
            int p0_ = rowf_ ? ar_ : ac_, p1_ = rowf_ ? ac_ : ar_;
            unsigned o0 = pass01(sr + DR[p0_], sc + DC[p0_], tr, tc, rich);
            unsigned o1 = pass01(sr + DR[p1_], sc + DC[p1_], tr, tc, rich) &
                          (unsigned)((adr_ != 0) & (adc_ != 0));
            int fb = o0 ? p0_ : (o1 ? p1_ : STAY);
            int ml = -(int)(okl != 0);
            int a0 = ((int)(K & 7) & ml) | (fb & ~ml);
            int a1 = ((int)((K >> 3) & 7) & ml) | (STAY & ~ml);
            int a2 = ((int)((K >> 6) & 7) & ml) | (STAY & ~ml);
            int e1 = -(int)(d == 1) & ml;
            int e2 = -(int)(d == 2) & ml;
            a1 = ((a0 ^ 1) & e1) | (a1 & ~e1);
            a2 = (((a1 ^ 1)) & (e1 | e2)) | (a2 & ~(e1 | e2));
            unsigned pm = pass01(sr - 1, sc, tr, tc, rich) |
                          (pass01(sr + 1, sc, tr, tc, rich) << 1) |
                          (pass01(sr, sc - 1, tr, tc, rich) << 2) |
                          (pass01(sr, sc + 1, tr, tc, rich) << 3);
            int sa = __builtin_ctz(pm | (uint32_t)(pm == 0));
            int hv = -(int)(pm != 0);
            int z = -(int)(d == 0);
            acts[0] = ((((sa & hv) | (STAY & ~hv))) & z) | (a0 & ~z);
            acts[1] = (((((sa ^ 1) & hv) | (STAY & ~hv))) & z) | (a1 & ~z);
            acts[2] = (STAY & z) | (a2 & ~z);
#ifdef NS5ALT
            {   // v5s 肥罐头: 从本轮终点(r3,c3)向原目标续算下轮 3 步
                int mvz = ~z;                    // z=站金轮: 终点=原地
                int r3 = sr + ((dr0 & ml & mvz)) + ((DR[a0 & 7] & ~ml & mvz));
                int c3 = sc + ((dc0 & ml & mvz)) + ((DC[a0 & 7] & ~ml & mvz));
                int nr_ = tgr - r3, nc_ = tgc - c3;
                nr_ = nr_ < -3 ? -3 : (nr_ > 3 ? 3 : nr_);
                nc_ = nc_ < -3 ? -3 : (nc_ > 3 ? 3 : nc_);
                uint32_t K2 = SL.pk[nr_ + 3][nc_ + 3];
                // 终点站金(nr_==nc_==0): 用自家位图做折返对
                unsigned pm3 = pass01(r3 - 1, c3, tr, tc, rich) |
                               (pass01(r3 + 1, c3, tr, tc, rich) << 1) |
                               (pass01(r3, c3 - 1, tr, tc, rich) << 2) |
                               (pass01(r3, c3 + 1, tr, tc, rich) << 3);
                int sa3 = __builtin_ctz(pm3 | (uint32_t)(pm3 == 0));
                int hv3 = -(int)(pm3 != 0);
                int z3 = -(int)((nr_ | nc_) == 0);
                g_s.can0[u] = (int8_t)(((((sa3 & hv3) | (STAY & ~hv3))) & z3) |
                                       ((int)(K2 & 7) & ~z3));
                g_s.can1[u] = (int8_t)((((((sa3 ^ 1) & hv3) | (STAY & ~hv3))) & z3) |
                                       ((int)((K2 >> 3) & 7) & ~z3));
                g_s.can2[u] = (int8_t)((STAY & z3) | ((int)((K2 >> 6) & 7) & ~z3));
            }
#endif
        }
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = 0;
    return out;
}

GameOutput sanitize(GameOutput o) {
    for (int i = 0; i < S; ++i)
        o.actions[i] = (unsigned)o.actions[i] > 4u ? STAY : o.actions[i];
    o.k = (unsigned)o.k > 6u ? 3 : o.k;
    o.order = (unsigned)o.order > 1u ? 0 : o.order;
    o.vp = (unsigned)o.vp > 2u ? 0 : o.vp;
    return o;
}

}  // namespace

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
        return sanitize(decide(input));
    } catch (...) {
        return SAFE_OUT;
    }
}
