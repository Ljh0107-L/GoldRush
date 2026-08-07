// src/v3/player.cpp — v3: 300ns 单内核(2026-08-08 立项)
//
// ============ 立项依据(实测, 见 CHANGELOG 2026-08-08) ============
// 1. 地板实验: 扫描+打分(站桩)=360ns, +走位=390; v2 决策尾(goal/pile/patrol/
//    被动路径)吃 ~410ns —— 全部砍掉, 决策尾预算 <100ns
// 2. 策略解剖: Tundra(290ns/收入2662) 先手时 5.5金/轮 全程无衰减; 我们先手时
//    仅 ~3金/轮 —— 差距在中心 9x9 占位覆盖, 不在复杂策略。外圈矿堆追逐是
//    后手时代遗产, 先手时代中心常驻更优 → 矿堆缓存/巡逻表/目标机器全砍,
//    换双锚点(6,6)/(10,10)中心驻守 —— 延迟与收入同向
// 3. 平台物理(三次验证): 成本 ∝ 分支位点数 x 冷前端; 代码/状态越小溢价越低
//
// ============ 形态 ============
// 轮换单管线(active=round&1): 主动单位 扫描->打分->导向/折返; 被动单位零输入读,
// 与主动共用同一条导向/折返代码(无独立副本)。
// 状态 ~160B: bombbit[17]+bp[19]+杂项 —— 炸弹列表已砍(位图纯记忆, 波清即弃,
// 停留期误差 <=20 轮的保守绕行可接受)。
#include <cstdint>
#include <cstring>
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
    uint32_t bombbit[N];     // 炸弹位图(护栏用; 波清)
    uint32_t bp[N + 2];      // 哨兵阻挡位图: 墙|弹|边界(导向用唯一地形真值)
    int8_t last_r[2], last_c[2];
    uint8_t stuck[2];
    uint8_t patrol[2];       // 巡游路点下标
    int16_t last_round;
};
State g_s;

// 打分距离倒数表(排序语义, 免除法)
constexpr uint16_t REC[8] = {4096, 2048, 1365, 1024, 819, 683, 585, 512};

// 中心双环巡游(v35: 罚站 62% 确诊蹲点病 —— 移动x窗宽=覆盖率):
// u0 上环 / u1 下环, 3 行窗恰好铺满 9x9 主产区, 到点即转向
constexpr int8_t PRW[2][4] = {{5, 5, 7, 7}, {11, 11, 9, 9}};
constexpr int8_t PCW[2][4] = {{6, 10, 10, 6}, {10, 6, 6, 10}};

inline unsigned pass01(int r, int c, int tr, int tc) {
    return (~(g_s.bp[r + 1] >> (c + 1)) & 1u) &
           (unsigned)((r != tr) | (c != tc));
}

__attribute__((noinline, cold))
int escapeStep(int r, int c, int tr, int tc, int pr, int pc) {
    for (int a = 0; a < 4; ++a) {
        int nr = r + DR[a], nc = c + DC[a];
        if (nr == pr && nc == pc) continue;
        if (pass01(nr, nc, tr, tc)) return a;
    }
    return -1;
}

__attribute__((noinline, cold))
void stuckEscape(int u, int sr, int sc, int tr, int tc, int* acts) {
    for (int a = 0; a < 4; ++a)
        if (pass01(sr + DR[a], sc + DC[a], tr, tc)) { acts[0] = a; break; }
    g_s.stuck[u] = 0;
}

int steerStep(int r, int c, int gr, int gc, int tr, int tc, int pr, int pc) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;
    int ac = 2 + (dcc > 0);
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = pass01(r + DR[p0], c + DC[p0], tr, tc);
    unsigned ok1 = pass01(r + DR[p1], c + DC[p1], tr, tc) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)
        return ok0 ? p0 : p1;
    return (adr | adc) ? escapeStep(r, c, tr, tc, pr, pc) : -1;
}

GameOutput decide(const GameInput* in) {
#ifdef NS3DUAL
    const int active = -1;                       // 双扫: 无被动单位
#else
    const int active = in->round & 1;
#endif
#if defined(__AVX2__)
    // 输入行前置装载: miss 最早发射, 账务藏进阴影
    __m256i rowbufs[2][5];
    int rb_oks[2] = {0, 0}, rb_cbs[2] = {0, 0};
    for (int lu = 0; lu < 2; ++lu) {
        if (active >= 0 && lu != active) continue;
        __m256i* rowbuf = rowbufs[lu];
        int sr0 = in->my_units[lu].row, sc0 = in->my_units[lu].col;
        if (sr0 >= 0 && sr0 < N && sc0 >= 0 && sc0 < N) {
            int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
            rb_oks[lu] = 1; rb_cbs[lu] = cb;
#ifdef NS3ROWS
#pragma GCC unroll 3
            for (int i = 1; i < 4; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbuf[i] = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
            rowbuf[0] = rowbuf[4] = _mm256_set1_epi32(-5);   // 视为雾
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbuf[i] = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
#endif
        }
    }
#endif
    if (in->round <= g_s.last_round) {           // 新局
        memset(&g_s, 0, sizeof(g_s));
        g_s.bp[0] = g_s.bp[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bp[r + 1] = 0xFFFC0001u;
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0) {                   // 炸弹波: 位图记忆即弃
        for (int r = 0; r < N; ++r) g_s.bp[r + 1] &= ~(g_s.bombbit[r] << 1);
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
    }

    GameOutput out = SAFE_OUT;

    // ===== 扫描: wv7(7x7 缓冲) + goldm (轮换=仅主动; 双扫=背靠背 MLP) =====
    int8_t wv7s[2][49];
    uint32_t goldms[2] = {0, 0};
    for (int su = 0; su < 2; ++su) {
        int8_t* wv7 = wv7s[su];
        uint32_t goldm = 0;
        memset(wv7, -1, 49);
        if (active >= 0 && su != active) continue;
        int sr = in->my_units[su].row, sc = in->my_units[su].col;
        if (sr >= 0 && sr < N && sc >= 0 && sc < N) {
#if defined(__AVX2__)
            const __m256i* rowbuf = rowbufs[su];
            int rb_cb = rb_cbs[su];
            if (rb_oks[su]) {
                const __m256i vz = _mm256_setzero_si256();
                const __m256i vm1 = _mm256_set1_epi32(-1);
                const __m256i vm3 = _mm256_set1_epi32(-3);
                int cb = rb_cb;
                int lsh = 2 + (sc - 2 - cb);
                int lo = sc - 2 < 0 ? -(sc - 2) : 0;
                int hix = sc + 2 > N - 1 ? sc + 2 - (N - 1) : 0;
                uint32_t colv = ((31u >> hix) & (31u << lo)) & 31u;
                uint32_t wallm = 0, bombm = 0, validm = 0;
#pragma GCC unroll 5
                for (int i = 0; i < 5; ++i) {
                    int rr = sr - 2 + i;
                    uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                    __m256i vrow = rowbuf[i];
                    uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                        _mm256_cmpgt_epi32(vrow, vz)));
                    uint32_t w8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                        _mm256_cmpeq_epi32(vrow, vm1)));
                    uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                        _mm256_cmpeq_epi32(vrow, vm3)));
                    uint32_t rv = colv & rowok;
                    goldm  |= ((((g8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                    wallm  |= ((((w8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                    bombm  |= ((((b8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                    validm |= rv << (i * 5);
                    int32_t tmp[8];
                    _mm256_storeu_si256((__m256i*)tmp, vrow);
#pragma GCC unroll 5
                    for (int j = 0; j < 5; ++j) {
                        int li = j + lsh - 2;
                        li &= ~(li >> 31);
                        int v = tmp[li];
                        int m = -(int)((rv >> j) & 1u);
                        wv7[(i + 1) * 7 + (j + 1)] = (int8_t)((v & m) | ~m);
                    }
                }
                wallm &= validm;
                if (wallm) {                       // 墙并入 bp
                    int r0 = sr - 2 < 0 ? 0 : sr - 2;
                    int r1 = sr + 2 >= N ? N - 1 : sr + 2;
                    int c0 = sc - 2 < 0 ? 0 : sc - 2;
                    for (int r = r0; r <= r1; ++r) {
                        int b5 = (r - sr + 2) * 5 + 2 - sc;
                        uint32_t slice = ((wallm >> (b5 + c0)) & 31u) << c0;
                        g_s.bp[r + 1] |= slice << 1;
                    }
                }
                if (bombm) {                       // 弹并入位图(无列表)
                    uint32_t bm = bombm;
                    while (bm) {
                        int i = __builtin_ctz(bm); bm &= bm - 1;
                        int br = sr - 2 + i / 5, bc = sc - 2 + i % 5;
                        g_s.bombbit[br] |= 1u << bc;
                        g_s.bp[br + 1] |= 1u << (bc + 1);
                    }
                }
            }
#else
            {   // 标量参考路径(本机测试用)
                for (int i = 0; i < 5; ++i) {
                    int rr = sr - 2 + i;
                    if ((unsigned)rr >= (unsigned)N) continue;
                    for (int j = 0; j < 5; ++j) {
                        int cc = sc - 2 + j;
                        if ((unsigned)cc >= (unsigned)N) continue;
                        int v = in->grid[rr][cc];
                        wv7[(i + 1) * 7 + (j + 1)] = (int8_t)v;
                        int b = i * 5 + j;
                        if (v > 0) goldm |= 1u << b;
                        if (v == -1) g_s.bp[rr + 1] |= 1u << (cc + 1);
                        if (v == -3) {
                            g_s.bombbit[rr] |= 1u << cc;
                            g_s.bp[rr + 1] |= 1u << (cc + 1);
                        }
                    }
                }
            }
#endif
        }
        goldms[su] = goldm;
    }

    // ===== 双单位决策(共用代码; 被动 goldm 视为 0) =====
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        const int8_t* wv7 = wv7s[u];
        const int act_ = (active < 0) | (u == active);
        uint32_t gm0 = goldms[u] & (uint32_t)(0 - act_);

        // 采集打分(簇加成): 只走置位金格
        constexpr int8_t MD[25] = {4,3,2,3,4, 3,2,1,2,3, 2,1,0,1,2,
                                   3,2,1,2,3, 4,3,2,3,4};
        int bestr = -1, bestc = -1, bests = 0;
        int gn_ = 0;
        {
            uint32_t gm = gm0;
            while (gm) {
                int i = __builtin_ctz(gm); gm &= gm - 1;
                ++gn_;
                int w = (i / 5 + 1) * 7 + i % 5 + 1;
                int v = wv7[w];
                int nu = wv7[w - 7], nd2 = wv7[w + 7], nl = wv7[w - 1], nr2 = wv7[w + 1];
                int nb = (nu > 0 ? nu : 0) + (nd2 > 0 ? nd2 : 0) +
                         (nl > 0 ? nl : 0) + (nr2 > 0 ? nr2 : 0);
                int sc_ = (v * 2 + nb) * REC[MD[i]];
                if (sc_ > bests) {
                    bests = sc_; bestr = sr - 2 + i / 5; bestc = sc - 2 + i % 5;
                }
            }
        }

#ifdef NS3PROBE
        {   (void)bests; (void)gn_;
            int a = (in->round / 4 + u * 2) & 3;
            acts[0] = acts[1] = acts[2] = a;
            continue;
        }
#endif
        // 目标 = 窗口最优金格, 否则巡游路点(到点即转向, 消灭蹲点罚站)
        int tgr, tgc;
        if (bestr >= 0) { tgr = bestr; tgc = bestc; }
        else {
            uint8_t& pi = g_s.patrol[u];
            unsigned here = (unsigned)((sr == PRW[u][pi]) & (sc == PCW[u][pi]));
            pi = (uint8_t)((pi + here) & 3);
            tgr = PRW[u][pi]; tgc = PCW[u][pi];
        }
        {
            int d = (tgr > sr ? tgr - sr : sr - tgr) +
                    (tgc > sc ? tgc - sc : sc - tgc);
            if (d == 0) {                          // 站上目标(金格或锚点): 折返
                unsigned pm = pass01(sr - 1, sc, tr, tc) |
                              (pass01(sr + 1, sc, tr, tc) << 1) |
                              (pass01(sr, sc - 1, tr, tc) << 2) |
                              (pass01(sr, sc + 1, tr, tc) << 3);
                if (pm) {
                    int a = __builtin_ctz(pm);
                    acts[0] = a; acts[1] = a ^ 1;
                }
            } else {
                int r = sr, c = sc, n = 0;
                int pr = g_s.last_r[u], pc = g_s.last_c[u];
#pragma GCC unroll 3
                for (int i = 0; i < 3; ++i) {
                    int notdone = (int)((r != tgr) | (c != tgc));
                    int a = steerStep(r, c, tgr, tgc, tr, tc, pr, pc);
                    int m = -(notdone & (int)(a >= 0));
                    acts[i] = (a & m) | (STAY & ~m);
                    int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                    pr = (r & m) | (pr & ~m);
                    pc = (c & m) | (pc & ~m);
                    r = (nr & m) | (r & ~m);
                    c = (nc & m) | (c & ~m);
                    n -= m;
                }
                int arrived = (int)((r == tgr) & (c == tgc));
                if (arrived & (int)(bestr >= 0)) {
                    if (n > 0 && n < 3) {          // 早到金格: 退一步/双吃回进
                        acts[n] = acts[n - 1] ^ 1;
                        if (n + 1 < 3) acts[n + 1] = acts[n] ^ 1;
                    }
                }
            }
        }

        // 尾步填充(复用 wv7; 被动 gn_=0 自动跳过)
#ifdef NS3FILL0
        if (0) {
#else
        if (gn_ > 0) {
#endif
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                if (acts[i] == STAY) {
                    int besta = -1, bv = 0;
#pragma GCC unroll 4
                    for (int a = 0; a < 4; ++a) {
                        int nr = r + DR[a], nc = c + DC[a];
                        int ur = nr - sr + 3, uc = nc - sc + 3;
                        unsigned inw = ((unsigned)ur <= 6u) & ((unsigned)uc <= 6u);
                        int idx = (ur * 7 + uc) & -(int)inw;
                        int v = wv7[idx];
                        int okm = -(int)(inw & (unsigned)(v > bv) &
                                         (unsigned)!(nr == tr && nc == tc));
                        bv = (v & okm) | (bv & ~okm);
                        besta = (a & okm) | (besta & ~okm);
                    }
                    acts[i] = besta >= 0 ? besta : acts[i];
                }
                int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                unsigned inb = ((unsigned)nr < (unsigned)N) &
                               ((unsigned)nc < (unsigned)N);
                int ri_ = nr & -(int)inb, ci_ = nc & -(int)inb;
                unsigned w_ = ((g_s.bp[ri_ + 1] >> (ci_ + 1)) &
                               ~(g_s.bombbit[ri_] >> ci_)) & 1u;
                int m = -(int)((unsigned)(acts[i] != STAY) & inb & (w_ ^ 1u));
                r = (nr & m) | (r & ~m); c = (nc & m) | (c & ~m);
            }
        }

        // 卡死解困
        unsigned same = (unsigned)((sr == g_s.last_r[u]) &
                                   (sc == g_s.last_c[u]) & (acts[0] == STAY));
        g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
        if (g_s.stuck[u] >= 2) stuckEscape(u, sr, sc, tr, tc, acts);
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;

        // 防漂移护栏: 近弹预筛(行位图 7 行并查, 无列表)
#ifdef NS3GUARD1
        if (act_ && in->my_units_gold[u] >= 300) {
#else
        if (in->my_units_gold[u] >= 300) {
#endif
            uint32_t near_ = 0;
#pragma GCC unroll 7
            for (int dr2 = -3; dr2 <= 3; ++dr2) {
                int rr = sr + dr2;
                unsigned okr = (unsigned)rr < (unsigned)N;
                near_ |= g_s.bombbit[rr & -(int)okr] & (0u - okr);
            }
            if (near_) {
                for (int blk = 0; blk < 3; ++blk) {
                    if (acts[blk] == STAY) continue;
                    int r = sr, c = sc;
                    for (int i = 0; i < 3; ++i) {
                        int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                        unsigned inb = ((unsigned)nrr < (unsigned)N) &
                                       ((unsigned)ncc < (unsigned)N);
                        int ri = nrr & -(int)inb, ci = ncc & -(int)inb;
                        unsigned wb_ = ((g_s.bp[ri + 1] >> (ci + 1)) &
                                        ~(g_s.bombbit[ri] >> ci)) & 1u;
                        unsigned adv = (unsigned)(acts[i] != STAY) &
                                       (unsigned)(i != blk) & inb & (wb_ ^ 1u);
                        if (adv & ((g_s.bombbit[ri] >> ci) & 1u)) {
                            for (int j = i; j < 3; ++j) acts[j] = STAY;
                            break;
                        }
                        int m = -(int)adv;
                        r = (nrr & m) | (r & ~m); c = (ncc & m) | (c & ~m);
                    }
                }
            }
        }
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = 0;
    return out;
}

GameOutput sanitize(GameOutput o) {   // 全 cmov, 零分支位点
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
