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
    // 迷你矿堆缓存(16 槽直映射哈希): 外圈堆积存量的记忆(v36)
    int8_t pr_[16], pc_[16];
    uint8_t pv_[16], ps_[16];
    uint16_t plive;          // 活堆位掩码(稀疏门控: 无堆零成本)
    uint8_t banybomb;        // 已知炸弹存在标志(护栏全局门控)
    int8_t plan[2][3];       // v37: 主动轮预算的"下轮 3 步"(被动轮直接回放)
    uint8_t plan_ok[2];
    uint8_t vbought;         // v49: 上轮买过视野 -> 本轮读外环找矿堆
    int16_t last_round;
};

inline int pileSlot(int r, int c);
State g_s;

inline int pileSlot(int r, int c) { return (r * 31 + c) & 15; }
inline uint8_t nowq() { return (uint8_t)(((uint16_t)g_s.last_round >> 2) | 1); }

// 打分距离倒数表(排序语义, 免除法)
constexpr uint16_t REC[8] = {4096, 2048, 1365, 1024, 819, 683, 585, 512};

// v36 角色分工(收入节奏解剖: 赢家暴击轮贡献 64-72%, 全是矿堆农):
// u0 中心环抢新刷(先手红利); u1 外环收堆积存量(矿堆无争议, 后手也稳赚)
constexpr int8_t PRW[2][4] = {{5, 5, 10, 10}, {3, 3, 13, 13}};
constexpr int8_t PCW[2][4] = {{6, 10, 10, 6}, {3, 13, 13, 3}};

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
    if (in->round % 20 == 0 && g_s.banybomb) {   // 炸弹波: 位图记忆即弃(有弹才清)
        for (int r = 0; r < N; ++r) g_s.bp[r + 1] &= ~(g_s.bombbit[r] << 1);
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
        g_s.banybomb = 0;
    }

    GameOutput out = SAFE_OUT;

    // ===== 扫描: 纯掩码(v39) (轮换=仅主动; 双扫=背靠背 MLP) =====
    uint32_t goldms[2] = {0, 0};
    // 直读工具: 界内取 grid 值(负值=雾/墙/弹, 用者自行钳 0), 界外=0
    auto gv = [in](int r, int c) -> int {
        unsigned ok = ((unsigned)r < (unsigned)N) & ((unsigned)c < (unsigned)N);
        int v = in->grid[r & -(int)ok][c & -(int)ok];
        return v & -(int)ok;
    };
    for (int su = 0; su < 2; ++su) {
        uint32_t goldm = 0;
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
                }   // (v39: wv7 数值缓冲已砍 —— 打分对稀疏金格直读 grid, L1 已热)
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
                        g_s.banybomb = 1;
                    }
                }
#ifndef NS3MIN
                {   // 矿堆登记(v36/v39): 窗口内 v>=5 记(直读 grid)
                    uint32_t gm = goldm;
                    while (gm) {
                        int i = __builtin_ctz(gm); gm &= gm - 1;
                        int v = gv(sr - 2 + i / 5, sc - 2 + i % 5);
                        if (v >= 5) {
                            int gr_ = sr - 2 + i / 5, gc_ = sc - 2 + i % 5;
                            int k = pileSlot(gr_, gc_);
                            g_s.pr_[k] = (int8_t)gr_; g_s.pc_[k] = (int8_t)gc_;
                            g_s.pv_[k] = (uint8_t)v;  g_s.ps_[k] = nowq();
                            g_s.plive |= (uint16_t)(1u << k);
                        }
                    }
                }
#endif
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

#ifdef NS3VP
    if (g_s.vbought) {                 // 7x7 外环探针: 只为找堆(pileNote), 不动主机器
        g_s.vbought = 0;
        for (int su = 0; su < 2; ++su) {
            int sr = in->my_units[su].row, sc = in->my_units[su].col;
            if ((unsigned)sr >= (unsigned)N) continue;
            for (int k = -3; k <= 3; ++k) {       // 上下两行 + 左右两列
                int v1_ = gv(sr - 3, sc + k), v2_ = gv(sr + 3, sc + k);
                if (v1_ >= 5) { int gr_ = sr - 3, gc_ = sc + k; int q = pileSlot(gr_, gc_);
                    g_s.pr_[q]=(int8_t)gr_; g_s.pc_[q]=(int8_t)gc_; g_s.pv_[q]=(uint8_t)v1_; g_s.ps_[q]=nowq(); g_s.plive|=(uint16_t)(1u<<q); }
                if (v2_ >= 5) { int gr_ = sr + 3, gc_ = sc + k; int q = pileSlot(gr_, gc_);
                    g_s.pr_[q]=(int8_t)gr_; g_s.pc_[q]=(int8_t)gc_; g_s.pv_[q]=(uint8_t)v2_; g_s.ps_[q]=nowq(); g_s.plive|=(uint16_t)(1u<<q); }
                if (k >= -2 && k <= 2) {
                    int v3_ = gv(sr + k, sc - 3), v4_ = gv(sr + k, sc + 3);
                    if (v3_ >= 5) { int gr_ = sr + k, gc_ = sc - 3; int q = pileSlot(gr_, gc_);
                        g_s.pr_[q]=(int8_t)gr_; g_s.pc_[q]=(int8_t)gc_; g_s.pv_[q]=(uint8_t)v3_; g_s.ps_[q]=nowq(); g_s.plive|=(uint16_t)(1u<<q); }
                    if (v4_ >= 5) { int gr_ = sr + k, gc_ = sc + 3; int q = pileSlot(gr_, gc_);
                        g_s.pr_[q]=(int8_t)gr_; g_s.pc_[q]=(int8_t)gc_; g_s.pv_[q]=(uint8_t)v4_; g_s.ps_[q]=nowq(); g_s.plive|=(uint16_t)(1u<<q); }
                }
            }
        }
    }
#endif
    // ===== 双单位决策(共用代码; 被动 goldm 视为 0) =====
#ifdef NS3VP
    unsigned g_blind = 1;   // 双单位均纯巡游 = 盲轮
#endif
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        const int act_ = (active < 0) | (u == active);
#if !defined(NS3NOPLAN) && !defined(NS3MIN)
        if (!act_ && g_s.plan_ok[u]) {           // v37: 被动轮回放缓存计划
            acts[0] = g_s.plan[u][0];
            acts[1] = g_s.plan[u][1];
            acts[2] = g_s.plan[u][2];
            g_s.plan_ok[u] = 0;
            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
            continue;
        }
#endif
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
                int gr_ = sr - 2 + i / 5, gc_ = sc - 2 + i % 5;
                int v = gv(gr_, gc_);
#ifdef NS3NONB
                int sc_ = v * REC[MD[i]];        // 簇加成砍除实验
#else
                int nu = gv(gr_ - 1, gc_), nd2 = gv(gr_ + 1, gc_);
                int nl = gv(gr_, gc_ - 1), nr2 = gv(gr_, gc_ + 1);
                int nb = (nu > 0 ? nu : 0) + (nd2 > 0 ? nd2 : 0) +
                         (nl > 0 ? nl : 0) + (nr2 > 0 ? nr2 : 0);
                int sc_ = (v * 2 + nb) * REC[MD[i]];
#endif
                if (sc_ > bests) {
                    bests = sc_; bestr = gr_; bestc = gc_;
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
        // 目标 = 窗口最优金格 > 活矿堆(u1 矿堆农) > 巡游路点
        // v47: 三候选无条件计算 + 掩码级联(消 4 分支位点/单位)
        int tgr, tgc;
        {
            int bi = -1;
#ifndef NS3MIN
            if (bestr < 0 && g_s.plive) {   // 门控=省功(v47 无条件跑 +300ops 判负)
                int bsc = 0;
                uint8_t tq = nowq();
                uint32_t lm = g_s.plive;
                while (lm) {
                    int k = __builtin_ctz(lm); lm &= lm - 1;
                    int fresh = -(int)((uint8_t)(tq - g_s.ps_[k]) <= 10);  // ~40轮
                    g_s.plive &= (uint16_t)~(((~fresh) & 1) << k);         // 过期顺手清
                    int dr_ = g_s.pr_[k] - sr, dc_ = g_s.pc_[k] - sc;
                    dr_ = dr_ < 0 ? -dr_ : dr_; dc_ = dc_ < 0 ? -dc_ : dc_;
                    int d_ = dr_ + dc_; d_ = d_ > 31 ? 31 : d_;
                    int sc2 = (g_s.pv_[k] * (64 - d_ * 2)) & fresh;
                    unsigned cen = ((unsigned)(g_s.pr_[k] - 4) <= 8u) &
                                   ((unsigned)(g_s.pc_[k] - 4) <= 8u);
                    sc2 &= -(int)((u == 1) | cen);
                    int gt = -(int)(sc2 > bsc);
                    bsc = (sc2 & gt) | (bsc & ~gt);
                    bi = (k & gt) | (bi & ~gt);
                }
            }
#endif
            // 巡游路点(无条件推进逻辑: 掩码化)
            uint8_t& pi = g_s.patrol[u];
            unsigned here = (unsigned)((sr == PRW[u][pi]) & (sc == PCW[u][pi]));
            pi = (uint8_t)((pi + here) & 3);
            // 掩码级联: 窗口金 > 矿堆 > 路点
            int mw = -(int)(bestr >= 0);
            int mp = -(int)(bi >= 0) & ~mw;
#ifdef NS3VP
            g_blind &= (unsigned)(~(mw | mp)) & 1u;   // 该单位有金/堆则非盲
#endif
            int pilr = g_s.pr_[bi & 15], pilc = g_s.pc_[bi & 15];
            tgr = (bestr & mw) | (pilr & mp) | (PRW[u][pi] & ~mw & ~mp);
            tgc = (bestc & mw) | (pilc & mp) | (PCW[u][pi] & ~mw & ~mp);
            // 到达矿堆目标 -> 摘除(掩码化)
            unsigned atp = (unsigned)((sr == tgr) & (sc == tgc)) & (unsigned)(mp & 1);
            g_s.pv_[bi & 15] &= (uint8_t)(atp - 1u);
            g_s.plive &= (uint16_t)~((atp & 1u) << (bi & 15));
        }
        {
            int d = (tgr > sr ? tgr - sr : sr - tgr) +
                    (tgc > sc ? tgc - sc : sc - tgc);
            if (d == 0) {                          // 站上目标(金格或锚点): 折返
#ifdef NS3BARE
                ;
#else
                unsigned pm = pass01(sr - 1, sc, tr, tc) |
                              (pass01(sr + 1, sc, tr, tc) << 1) |
                              (pass01(sr, sc - 1, tr, tc) << 2) |
                              (pass01(sr, sc + 1, tr, tc) << 3);
                if (pm) {
                    int a = __builtin_ctz(pm);
                    acts[0] = a; acts[1] = a ^ 1;
                }
#endif
            } else {
#ifdef NS3LUT
                // 直线快路径: 轴差>=3 时前 3 步必同向; 3 格直线包络精确查 bp
                {
                    int dr0 = tgr - sr, dc0 = tgc - sc;
                    int adr0 = dr0 < 0 ? -dr0 : dr0, adc0 = dc0 < 0 ? -dc0 : dc0;
                    int sgr = dr0 > 0 ? 1 : -1, sgc = dc0 > 0 ? 1 : -1;
                    if (adr0 >= adc0 + 3) {
                        unsigned ok = pass01(sr + sgr, sc, tr, tc) &
                                      pass01(sr + 2 * sgr, sc, tr, tc) &
                                      pass01(sr + 3 * sgr, sc, tr, tc);
                        if (ok) {
                            int a = dr0 > 0 ? 1 : 0;
                            acts[0] = acts[1] = acts[2] = a;
                            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
                            goto steer_fast;
                        }
                    } else if (adc0 >= adr0 + 3) {
                        unsigned ok = pass01(sr, sc + sgc, tr, tc) &
                                      pass01(sr, sc + 2 * sgc, tr, tc) &
                                      pass01(sr, sc + 3 * sgc, tr, tc);
                        if (ok) {
                            int a = dc0 > 0 ? 3 : 2;
                            acts[0] = acts[1] = acts[2] = a;
                            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
                            goto steer_fast;
                        }
                    }
                }
#endif
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
#ifdef NS3LUT
            steer_fast:;
#endif
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
                        (void)inw;
                        int v = gv(nr, nc);
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

#if !defined(NS3NOPLAN) && !defined(NS3MIN)
        if (act_) {                              // v37: 预算下轮 3 步(缓存计划)
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {        // 本轮走完后的终点
                int a = acts[i];
                int nr = r + DR[a], nc = c + DC[a];
                unsigned ok = (~(g_s.bp[nr + 1] >> (nc + 1)) & 1u) |
                              (unsigned)(a == STAY);
                int m = -(int)ok;
                r = (nr & m) | (r & ~m); c = (nc & m) | (c & ~m);
            }
            int pr2 = sr, pc2 = sc, n2 = 0;
#pragma GCC unroll 3
            for (int i = 0; i < 3; ++i) {
                int notdone = (int)((r != tgr) | (c != tgc));
                int a = steerStep(r, c, tgr, tgc, tr, tc, pr2, pc2);
                int m = -(notdone & (int)(a >= 0));
                g_s.plan[u][i] = (int8_t)((a & m) | (STAY & ~m));
                int nr = r + DR[g_s.plan[u][i]], nc = c + DC[g_s.plan[u][i]];
                pr2 = (r & m) | (pr2 & ~m); pc2 = (c & m) | (pc2 & ~m);
                r = (nr & m) | (r & ~m); c = (nc & m) | (c & ~m);
                n2 -= m;
            }
            (void)n2;
            g_s.plan_ok[u] = 1;
        }
#endif
#ifndef NS3BARE
        // 卡死解困
        unsigned same = (unsigned)((sr == g_s.last_r[u]) &
                                   (sc == g_s.last_c[u]) & (acts[0] == STAY));
        g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
        if (g_s.stuck[u] >= 2) stuckEscape(u, sr, sc, tr, tc, acts);
#endif
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;

        // 防漂移护栏: 近弹预筛(行位图 7 行并查, 无列表)
#if defined(NS3BARE)
        if (0) {
#elif defined(NS3GUARD1)
        if (act_ && g_s.banybomb && in->my_units_gold[u] >= 300) {
#else
        if (g_s.banybomb && in->my_units_gold[u] >= 300) {
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
#ifdef NS3VP
    // 盲轮买视野: 两单位都无金无堆(纯巡游)时 vp=1 (2金/轮, 下轮 7x7 找矿堆)
    // (Convergens 198/啊对对对 119/若叶 72 的视野投资全是正 ROI 的旁证)
    out.vp = (int)g_blind;
    g_s.vbought = (uint8_t)g_blind;
#else
    out.vp = 0;
#endif
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
