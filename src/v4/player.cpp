// src/v4/player.cpp — v4: 时间框架架构(2026-08-08, 用户三阶段定案)
//
// ============ 设计哲学: 时间为框架, 在预算内增删 ============
// 阶段A 开局(轮0~锚点就位, <=12轮): 预算无限(开局先手无意义 —— 双方 0 金)
//   vp=2 买 9x9 -> 全盒地形吸收 -> BFS 直通分区锚点(炸弹透明: 0金烧0)
//   治两病: 弹口袋 19 轮振荡(145653) / 开局慢进中心
// 阶段B 事件轮(<=30次/局, 触发=有单位无目标): ~700ns 预算
//   vp=1 买 7x7 -> 下轮外环 24 格找矿堆入缓存(矿堆农的望远镜)
// 阶段C 稳态(~90% 回合): 210ns 预算(~560 周期)
//   双单位 5 行掩码扫描 -> 打分argmax > 矿堆 > 双环巡游 -> 3 步导向
//   -> 折返双吃 -> 净位移卡死检测 -> 门控护栏
//
// ============ 实证依据(两天平台数据, 见 CHANGELOG) ============
// - Tiuntled 指纹: 定功率内核(IQR 30-50, 收获=空轮), 肥轮0(3.4-3.8μs 人人都有),
//   400-1500ns 轮=OS抖动(与波/收入/周期全不相关) —— 稳态只有一种形态
// - 位点经济学: 平台冷前端 ~12ns/分支位点; 本地热 bench 看不见此税
// - 收入解剖: 赢家=矿堆农(暴击轮贡献 64-72%); 双扫锁链式收割(v42 收入 2x 轮换)
// - v47 教训: 门控分支的价值是省功不只是省位点 —— 稀疏环保留门控
//
// ============ 210ns(~560周期) 稳态预算账本 ============
//   输入行装载 2x5 行(MLP 并行)     ~150 周期(等待重叠后)
//   掩码扫描+登记 x2                ~120
//   打分+目标级联 x2                ~80
//   导向+折返+卡死 x2               ~120
//   护栏(门控, 常关)+装配+边界      ~90
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
    uint32_t bp[N + 2];      // 阻挡位图: 墙|弹|边界(富单位导向)
    uint32_t bpw[N + 2];     // 墙|边界(穷单位<50金: 炸弹透明, 烧10%x0=0)
    uint32_t bombbit[N];     // 炸弹位图(护栏/波清)
    // 8 槽矿堆缓存(直映射哈希) + 活堆位掩码
    int8_t  pr_[8], pc_[8];
    uint8_t pv_[8], ps_[8];
    uint8_t plive;
    uint8_t banybomb;
    // 卡死/振荡检测
    int8_t last_r[2], last_c[2], r2_[2], c2_[2];
    int16_t last_gold[2];
    uint8_t stuck[2];
    // 阶段机
    uint8_t phase;           // 0=开局A 1=稳态C
    uint8_t vbought;         // 上轮买过视野 -> 本轮外环找堆
    uint8_t evcnt;           // 事件轮计数(<=30)
    uint8_t patrol[2];
    int16_t last_round;
};
State g_s;
const uint32_t* g_bpp = g_s.bp;   // 当前单位导向位图(按持金选)

inline int pileSlot(int r, int c) { return (r * 31 + c) & 7; }
inline uint8_t nowq() { return (uint8_t)(((uint16_t)g_s.last_round >> 2) | 1); }
inline void pileNote(int r, int c, int v) {
    int k = pileSlot(r, c);
    g_s.pr_[k] = (int8_t)r; g_s.pc_[k] = (int8_t)c;
    g_s.pv_[k] = (uint8_t)v; g_s.ps_[k] = nowq();
    g_s.plive |= (uint8_t)(1u << k);
}

// 打分距离倒数表 + 曼哈顿距离表(5x5 窗)
constexpr uint16_t REC[8] = {4096, 2048, 1365, 1024, 819, 683, 585, 512};
constexpr int8_t MD[25] = {4,3,2,3,4, 3,2,1,2,3, 2,1,0,1,2, 3,2,1,2,3, 4,3,2,3,4};

// 双环巡游(v42 实证收入形态) + 分区锚点(A 段 BFS 终点)
constexpr int8_t PRW[2][4] = {{5, 5, 10, 10}, {3, 3, 13, 13}};
constexpr int8_t PCW[2][4] = {{6, 10, 10, 6}, {3, 13, 13, 3}};
constexpr int8_t ANCH_R[2] = {6, 10};
constexpr int8_t ANCH_C[2] = {6, 10};

inline unsigned pass01(int r, int c, int tr, int tc) {
    return (~(g_bpp[r + 1] >> (c + 1)) & 1u) &
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
    g_s.patrol[u] = (uint8_t)((g_s.patrol[u] + 1) & 3);
    g_s.stuck[u] = 0;
}

// LUT 导向(v4.2): (dr,dc)∈[-3,3]² 行优先无阻挡模拟 —— 动作+逐步位置偏移表
struct SLut {
    uint8_t act[7][7][3];
    int8_t  pdr[7][7][3], pdc[7][7][3];   // 第 i 步后的累计位移
    uint8_t mvn[7][7];                    // 实际移动步数(<=3)
    constexpr SLut() : act(), pdr(), pdc(), mvn() {
        for (int dr = -3; dr <= 3; ++dr)
            for (int dc = -3; dc <= 3; ++dc) {
                int r = 0, c = 0, n = 0;
                for (int i = 0; i < 3; ++i) {
                    int rr = dr - r, cc = dc - c;
                    int adr = rr < 0 ? -rr : rr, adc = cc < 0 ? -cc : cc;
                    uint8_t a = STAY;
                    if (adr | adc) {
                        if (adr >= adc) { a = rr > 0 ? 1 : 0; r += rr > 0 ? 1 : -1; }
                        else            { a = cc > 0 ? 3 : 2; c += cc > 0 ? 1 : -1; }
                        ++n;
                    }
                    act[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                }
                mvn[dr + 3][dc + 3] = (uint8_t)n;
            }
    }
};
constexpr SLut SL;

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

// ============ 阶段A: 开局(预算无限) ============
// BFS 到锚点(bpw 图: 炸弹透明), 返回首步方向; 不可达返回 -1
__attribute__((noinline, cold))
int bfsStep(int sr, int sc, int gr, int gc, int tr, int tc) {
    static int16_t q[N * N];
    static int8_t par[N * N];
    memset(par, -1, sizeof(par));
    int qh = 0, qt = 0;
    q[qt++] = (int16_t)(sr * N + sc);
    par[sr * N + sc] = 4;
    while (qh < qt) {
        int cur = q[qh++];
        int r = cur / N, c = cur % N;
        if (r == gr && c == gc) break;
        for (int a = 0; a < 4; ++a) {
            int nr = r + DR[a], nc = c + DC[a];
            if ((unsigned)nr >= (unsigned)N || (unsigned)nc >= (unsigned)N) continue;
            if ((g_s.bpw[nr + 1] >> (nc + 1)) & 1u) continue;
            if (nr == tr && nc == tc) continue;
            if (par[nr * N + nc] >= 0) continue;
            par[nr * N + nc] = (int8_t)a;
            q[qt++] = (int16_t)(nr * N + nc);
        }
    }
    if (par[gr * N + gc] < 0) return -1;
    int r = gr, c = gc, a = 4;
    while (!(r == sr && c == sc)) {
        a = par[r * N + c];
        r -= DR[a]; c -= DC[a];
    }
    return a;
}

// 全盒地形吸收(9x9 视野轮; 预算无限, 标量直读)
__attribute__((noinline, cold))
void absorbTerrain(const GameInput* in) {
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        if ((unsigned)sr >= (unsigned)N) continue;
        for (int r = sr - 4; r <= sr + 4; ++r) {
            if ((unsigned)r >= (unsigned)N) continue;
            for (int c = sc - 4; c <= sc + 4; ++c) {
                if ((unsigned)c >= (unsigned)N) continue;
                int v = in->grid[r][c];
                if (v == -1) {
                    g_s.bp[r + 1] |= 1u << (c + 1);
                    g_s.bpw[r + 1] |= 1u << (c + 1);
                } else if (v == -3) {
                    g_s.bombbit[r] |= 1u << c;
                    g_s.bp[r + 1] |= 1u << (c + 1);
                    g_s.banybomb = 1;
                } else if (v >= 5) {
                    pileNote(r, c, v);
                }
            }
        }
    }
}

GameOutput decide(const GameInput* in) {
#if defined(__AVX2__)
    // 输入行前置装载(阶段C 热路径; 最早发射, 账务藏进 miss 阴影)
    __m256i rowbufs[2][5];
    int rb_oks[2] = {0, 0}, rb_cbs[2] = {0, 0};
    for (int lu = 0; lu < 2; ++lu) {
        __m256i* rowbuf = rowbufs[lu];
        int sr0 = in->my_units[lu].row, sc0 = in->my_units[lu].col;
        if (sr0 >= 0 && sr0 < N && sc0 >= 0 && sc0 < N) {
            int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
            rb_oks[lu] = 1; rb_cbs[lu] = cb;
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbuf[i] = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
        }
    }
#endif
    if (in->round <= g_s.last_round) {           // 新局
        memset(&g_s, 0, sizeof(g_s));
        g_s.patrol[1] = 3;
        g_s.bp[0] = g_s.bp[N + 1] = ~0u;
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r)
            g_s.bp[r + 1] = g_s.bpw[r + 1] = 0xFFFC0001u;
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0 && g_s.banybomb) {   // 炸弹波: 弹记忆即弃
        memcpy(g_s.bp, g_s.bpw, sizeof(g_s.bp));
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
        g_s.banybomb = 0;
    }

    GameOutput out = SAFE_OUT;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;

    // ============ 阶段A: 开局(冷函数, 预算无限) ============
    if (g_s.phase == 0) {
        absorbTerrain(in);                       // 9x9 全盒: 墙/弹/堆全吸收
        int done = 1;
        for (int u = 0; u < 2; ++u) {
            int sr = in->my_units[u].row, sc = in->my_units[u].col;
            int* acts = out.actions + u * 3;
            acts[0] = acts[1] = acts[2] = STAY;
            if ((unsigned)sr >= (unsigned)N) continue;
            int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
            g_bpp = g_s.bpw;                     // 开局 0 金: 炸弹透明
            int adr = sr - ANCH_R[u], adc = sc - ANCH_C[u];
            adr = adr < 0 ? -adr : adr; adc = adc < 0 ? -adc : adc;
            if (adr + adc > 2) done = 0;
            // 逐步 BFS 3 步(每步重查, 预算无限; 途中吃金由 BFS 目标顺带)
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int a = bfsStep(r, c, ANCH_R[u], ANCH_C[u], tr, tc);
                if (a < 0 || a == 4) break;
                acts[i] = a;
                r += DR[a]; c += DC[a];
            }
            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
        }
        if (done || in->round >= 12) g_s.phase = 1;
        out.k = 3;
        out.vp = 2;                              // 开局持续 9x9(费用无所谓)
        return out;
    }

    // ============ 阶段B 后半: 外环找堆(上轮买过视野) ============
    // 直读工具: 界内取值, 界外 0
    auto gv = [in](int r, int c) -> int {
        unsigned ok = ((unsigned)r < (unsigned)N) & ((unsigned)c < (unsigned)N);
        int v = in->grid[r & -(int)ok][c & -(int)ok];
        return v & -(int)ok;
    };
    if (g_s.vbought) {                           // 7x7 外环 24 格找矿堆
        g_s.vbought = 0;
        for (int u = 0; u < 2; ++u) {
            int sr = in->my_units[u].row, sc = in->my_units[u].col;
            if ((unsigned)sr >= (unsigned)N) continue;
            for (int k = -3; k <= 3; ++k) {
                int v1 = gv(sr - 3, sc + k), v2 = gv(sr + 3, sc + k);
                if (v1 >= 5) pileNote(sr - 3, sc + k, v1);
                if (v2 >= 5) pileNote(sr + 3, sc + k, v2);
                if (k >= -2 && k <= 2) {
                    int v3 = gv(sr + k, sc - 3), v4 = gv(sr + k, sc + 3);
                    if (v3 >= 5) pileNote(sr + k, sc - 3, v3);
                    if (v4 >= 5) pileNote(sr + k, sc + 3, v4);
                }
            }
        }
    }

    // ============ 阶段C: 稳态 210ns 内核 ============
    // ---- 扫描: 双单位背靠背纯掩码 ----
    uint32_t goldms[2] = {0, 0};
    for (int su = 0; su < 2; ++su) {
        uint32_t goldm = 0;
        int sr = in->my_units[su].row, sc = in->my_units[su].col;
        if (sr >= 0 && sr < N && sc >= 0 && sc < N) {
#if defined(__AVX2__)
            const __m256i* rowbuf = rowbufs[su];
            if (rb_oks[su]) {
                const __m256i vz = _mm256_setzero_si256();
                const __m256i vm1 = _mm256_set1_epi32(-1);
                const __m256i vm3 = _mm256_set1_epi32(-3);
                int cb = rb_cbs[su];
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
                }
                wallm &= validm;
                if (wallm) {                     // 墙 -> bp+bpw
                    int r0 = sr - 2 < 0 ? 0 : sr - 2;
                    int r1 = sr + 2 >= N ? N - 1 : sr + 2;
                    int c0 = sc - 2 < 0 ? 0 : sc - 2;
                    for (int r = r0; r <= r1; ++r) {
                        int b5 = (r - sr + 2) * 5 + 2 - sc;
                        uint32_t slice = ((wallm >> (b5 + c0)) & 31u) << c0;
                        g_s.bp[r + 1] |= slice << 1;
                        g_s.bpw[r + 1] |= slice << 1;
                    }
                }
                if (bombm) {                     // 弹 -> bombbit+bp
                    uint32_t bm = bombm;
                    while (bm) {
                        int i = __builtin_ctz(bm); bm &= bm - 1;
                        int br = sr - 2 + i / 5, bc = sc - 2 + i % 5;
                        g_s.bombbit[br] |= 1u << bc;
                        g_s.bp[br + 1] |= 1u << (bc + 1);
                        g_s.banybomb = 1;
                    }
                }
                {                                // v>=5 -> 矿堆缓存(稀疏)
                    uint32_t gm = goldm;
                    while (gm) {
                        int i = __builtin_ctz(gm); gm &= gm - 1;
                        int v = gv(sr - 2 + i / 5, sc - 2 + i % 5);
                        if (v >= 5) pileNote(sr - 2 + i / 5, sc - 2 + i % 5, v);
                    }
                }
            }
#else
            for (int i = 0; i < 5; ++i) {        // 标量参考(本机测试)
                int rr = sr - 2 + i;
                if ((unsigned)rr >= (unsigned)N) continue;
                for (int j = 0; j < 5; ++j) {
                    int cc = sc - 2 + j;
                    if ((unsigned)cc >= (unsigned)N) continue;
                    int v = in->grid[rr][cc];
                    if (v > 0) {
                        goldm |= 1u << (i * 5 + j);
                        if (v >= 5) pileNote(rr, cc, v);
                    } else if (v == -1) {
                        g_s.bp[rr + 1] |= 1u << (cc + 1);
                        g_s.bpw[rr + 1] |= 1u << (cc + 1);
                    } else if (v == -3) {
                        g_s.bombbit[rr] |= 1u << cc;
                        g_s.bp[rr + 1] |= 1u << (cc + 1);
                        g_s.banybomb = 1;
                    }
                }
            }
#endif
        }
        goldms[su] = goldm;
    }

    // ---- 决策(双单位) ----
    unsigned blind = 0;                          // 有单位无目标 -> 事件轮触发
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        g_bpp = in->my_units_gold[u] < 50 ? g_s.bpw : g_s.bp;   // 穷=弹透明
        uint32_t gm0 = goldms[u];

#if defined(NS4P) && NS4P == 1
        { (void)gm0; int a = (in->round / 4 + u * 2) & 3;
          acts[0] = acts[1] = acts[2] = a; continue; }
#endif
        // 目标: 最近金格(环掩码 cmov 链, 零内存零环; 210 预算删掉了簇加成打分)
        int bestr = -1, bestc = -1;
        {
            // 距离环掩码(MD==d 的位集合)
            constexpr uint32_t RM0 = 1u << 12;
            constexpr uint32_t RM1 = (1u<<7)|(1u<<11)|(1u<<13)|(1u<<17);
            constexpr uint32_t RM2 = (1u<<2)|(1u<<6)|(1u<<8)|(1u<<10)|(1u<<14)|(1u<<16)|(1u<<18)|(1u<<22);
            constexpr uint32_t RM3 = (1u<<1)|(1u<<3)|(1u<<5)|(1u<<9)|(1u<<15)|(1u<<19)|(1u<<21)|(1u<<23);
            constexpr uint32_t RM4 = (1u<<0)|(1u<<4)|(1u<<20)|(1u<<24);
            uint32_t g1 = gm0 & RM1, g2 = gm0 & RM2, g3 = gm0 & RM3;
            uint32_t g4 = gm0 & RM4, g0 = gm0 & RM0;
            uint32_t sel = g1 ? g1 : (g2 ? g2 : (g3 ? g3 : (g4 ? g4 : g0)));
            int i = __builtin_ctz(sel | 1u);
            int has = -(int)(gm0 != 0);
            bestr = ((sr - 2 + i / 5) & has) | (-1 & ~has);
            bestc = ((sc - 2 + i % 5) & has) | (-1 & ~has);
            // 矿堆登记只查选中格(1 次直读; 全窗登记是 210 预算外的奢侈)
            if (has) {
                int v = gv(bestr, bestc);
                if (v >= 5) pileNote(bestr, bestc, v);
            }
        }

        // 目标级联: 窗口金 > 活矿堆(u0 限中心) > 双环巡游
        int tgr, tgc;
        if (bestr >= 0) {                        // 有金: 整个级联机器全跳(省功)
            tgr = bestr; tgc = bestc;
        } else {
            int bi = -1;
            if (g_s.plive) {                     // 门控=省功(v47 教训)
                int bsc = 0;
                uint8_t tq = nowq();
                uint32_t lm = g_s.plive;
                while (lm) {
                    int k = __builtin_ctz(lm); lm &= lm - 1;
                    int fresh = -(int)((uint8_t)(tq - g_s.ps_[k]) <= 10);
                    g_s.plive &= (uint8_t)~(((~fresh) & 1) << k);
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
            uint8_t& pi = g_s.patrol[u];
            unsigned here = (unsigned)((sr == PRW[u][pi]) & (sc == PCW[u][pi]));
            pi = (uint8_t)((pi + here) & 3);
            int mp = -(int)(bi >= 0);
            int pilr = g_s.pr_[bi & 7], pilc = g_s.pc_[bi & 7];
            tgr = (pilr & mp) | (PRW[u][pi] & ~mp);
            tgc = (pilc & mp) | (PCW[u][pi] & ~mp);
            blind |= ((unsigned)(~mp) & 1u) |
                     (unsigned)(__builtin_popcount(g_s.plive) < 2);
            unsigned atp = (unsigned)((sr == tgr) & (sc == tgc)) & (unsigned)(mp & 1);
            g_s.pv_[bi & 7] &= (uint8_t)(atp - 1u);
            g_s.plive &= (uint8_t)~((atp & 1u) << (bi & 7));
        }

#if defined(NS4P) && NS4P == 2
        { (void)tgr; (void)tgc; int a = (in->round / 4 + u * 2) & 3;
          acts[0] = acts[1] = acts[2] = a; continue; }
#endif
        // 导向 / 折返
        {
            int d = (tgr > sr ? tgr - sr : sr - tgr) +
                    (tgc > sc ? tgc - sc : sc - tgc);
            if (d == 0) {                        // 站上目标: 折返再进(吃 35% 残)
                unsigned pm = pass01(sr - 1, sc, tr, tc) |
                              (pass01(sr + 1, sc, tr, tc) << 1) |
                              (pass01(sr, sc - 1, tr, tc) << 2) |
                              (pass01(sr, sc + 1, tr, tc) << 3);
                if (pm) {
                    int a = __builtin_ctz(pm);
                    acts[0] = a; acts[1] = a ^ 1;
                }
            } else if ((unsigned)(tgr - sr + 3) <= 6u &&
                       (unsigned)(tgc - sc + 3) <= 6u && d >= 3) {
                // LUT 快路径: 三步查表 + 途经格并行验证(一层依赖)
                int ir = tgr - sr + 3, ic = tgc - sc + 3;
                const uint8_t* pa = SL.act[ir][ic];
                const int8_t* xr = SL.pdr[ir][ic];
                const int8_t* xc = SL.pdc[ir][ic];
                unsigned ok = pass01(sr + xr[0], sc + xc[0], tr, tc) &
                              pass01(sr + xr[1], sc + xc[1], tr, tc) &
                              pass01(sr + xr[2], sc + xc[2], tr, tc);
                if (ok) {
                    acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
                } else {                       // 受阻: 回落串行链
                    int r = sr, c = sc;
                    int pr = g_s.last_r[u], pc = g_s.last_c[u];
                    for (int i = 0; i < 3; ++i) {
                        int notdone = (int)((r != tgr) | (c != tgc));
                        int a = steerStep(r, c, tgr, tgc, tr, tc, pr, pc);
                        int m = -(notdone & (int)(a >= 0));
                        acts[i] = (a & m) | (STAY & ~m);
                        int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                        pr = (r & m) | (pr & ~m); pc = (c & m) | (pc & ~m);
                        r = (nr & m) | (r & ~m); c = (nc & m) | (c & ~m);
                    }
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
                    if (n > 0 && n < 3) {        // 早到金格: 折返双吃
                        acts[n] = acts[n - 1] ^ 1;
                        if (n + 1 < 3) acts[n + 1] = acts[n] ^ 1;
                    }
                }
            }
        }

        // 卡死解困(冻结 或 净位移振荡, 在挣钱的折返豁免)
        {
            unsigned earn = (unsigned)(in->my_units_gold[u] != g_s.last_gold[u]);
            unsigned frz = (unsigned)((sr == g_s.last_r[u]) &
                                      (sc == g_s.last_c[u]) & (acts[0] == STAY));
            unsigned osc = (unsigned)((sr == g_s.r2_[u]) & (sc == g_s.c2_[u]));
            unsigned same = (frz | osc) & ~earn & 1u;
            g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
            if (g_s.stuck[u] >= 3) stuckEscape(u, sr, sc, tr, tc, acts);
            g_s.r2_[u] = g_s.last_r[u]; g_s.c2_[u] = g_s.last_c[u];
            g_s.last_gold[u] = (int16_t)in->my_units_gold[u];
            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
        }

        // 防漂移护栏(富+已知有弹才启; 撞弹截断)
        if (g_s.banybomb && in->my_units_gold[u] >= 300) {
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

    // ============ 阶段B 前半: 事件轮触发(买 7x7, 下轮找堆) ============
    out.k = 3;
    out.vp = 0;
    if (blind && g_s.evcnt < 30 && in->round < 480) {
        out.vp = 1;
        g_s.vbought = 1;
        ++g_s.evcnt;
    }
    return out;
}

GameOutput sanitize(GameOutput o) {              // 全 cmov
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
