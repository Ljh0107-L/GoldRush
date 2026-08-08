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

#ifdef NS6M
struct alignas(64) State {
    uint64_t bb[N + 2];      // 低32=墙|边界哨兵, 高32=炸弹(NS6M 合并: 1载1写)
    int8_t last_r[2], last_c[2];
    int16_t last_round;
#if defined(NS6P) || defined(NS6Q)
    int8_t plan[2][3];       // 轮换: 主动轮预算的被动轮 3 步(真导向, 非罐头)
    uint8_t plan_ok[2];
#endif
};
#else
struct alignas(64) State {
    uint32_t bpw[N + 2];     // 墙|边界(哨兵位图; 弹不入内)
    uint32_t bombbit[N + 2]; // 炸弹位图(+1 偏移对齐 bpw; 波清)
    int8_t last_r[2], last_c[2];
    int16_t last_round;
#ifdef NS6W
    uint8_t map_ok;          // 0未判 1烘焙采纳 2异图学习
#endif
#if defined(NS6P) || defined(NS6Q)
    int8_t plan[2][3];       // 轮换: 主动轮预算的被动轮 3 步(真导向, 非罐头)
    uint8_t plan_ok[2];
#endif
#ifdef NS6R
    int8_t pile_r, pile_c;   // NS6R: 单槽团队堆记忆(追过的>=5金格; 双管线亦可用)
    uint8_t pile_v, pile_t;  // 存量 / 时间戳(round>>2)
#endif
};
#endif
State g_s;

constexpr int8_t ANCH_R[2] = {6, 10};
constexpr int8_t ANCH_C[2] = {6, 10};

#ifdef NS6RT
// 开局烘焙路线(map1, BFS 最优 4 轮出角; 起点恒 (0,0)/(16,16) 三局核验)
// 盲轮(goldm==0)且位置吻合才生效; 任何漂移(NPC 挡步/中途吃金)自动放弃回落正常管线
constexpr uint8_t ORT_A[2][4][3] = {
    {{1,3,3},{3,1,3},{1,3,1},{3,1,1}},           // u0 (0,0)->(6,6)
    {{0,2,2},{2,0,2},{0,2,0},{2,0,0}},           // u1 (16,16)->(10,10)
};
constexpr int8_t ORT_R[2][4] = {{0,1,2,4},{16,15,14,12}};
constexpr int8_t ORT_C[2][4] = {{0,2,4,5},{16,14,12,11}};
#endif

#ifdef NS6C3
struct SctT {                                    // 扫描边缘常量表: sc 纯函数(cb/lsh/colv)
    int8_t cb[17], lsh[17]; uint8_t colv[17];
    constexpr SctT() : cb(), lsh(), colv() {
        for (int sc = 0; sc < 17; ++sc) {
            int c = sc - 2 < 0 ? 0 : (sc - 2 > 12 ? 12 : sc - 2);
            cb[sc] = (int8_t)c;
            lsh[sc] = (int8_t)(2 + (sc - 2 - c));
            int lo = sc - 2 < 0 ? -(sc - 2) : 0;
            int hix = sc + 2 > 16 ? sc + 2 - 16 : 0;
            colv[sc] = (uint8_t)(((31u >> hix) & (31u << lo)) & 31u);
        }
    }
};
constexpr SctT SCT;
#endif
#ifdef NS6C2
struct RclT {
    int8_t v[21];
    constexpr RclT() : v() {
        for (int x = 0; x < 21; ++x) { int t = x - 2; v[x] = (int8_t)(t < 0 ? 0 : (t > 16 ? 16 : t)); }
    }
};
constexpr RclT RCL;                              // 行钳位 LUT: 2 cmov -> 1 载
struct Dm5T {
    int8_t d[25], m[25];
    constexpr Dm5T() : d(), m() { for (int x = 0; x < 25; ++x) { d[x] = (int8_t)(x / 5); m[x] = (int8_t)(x % 5); } }
};
constexpr Dm5T DM5;                              // 除模5 LUT
#endif

#ifdef NS6W
#ifdef NS6M
#error "NS6W 与 NS6M 不兼容(直接操作 bpw)"
#endif
// map1 墙表烘焙(哨兵位形 bit c+1; 三局日志比对一致, 40 格)
// 开局 8 轮校验已见墙 ⊆ 本表则采纳并跳过墙记账; 异图回落学习模式
constexpr uint32_t BAKED_W1[N] = {
    0x00004010u, 0x00000000u, 0x0001800cu, 0x00024012u, 0x00002020u, 0x00001040u,
    0x00004010u, 0x00000500u, 0x000028a0u, 0x00000500u, 0x00004010u, 0x00001040u,
    0x00002020u, 0x00024012u, 0x0001800cu, 0x00000000u, 0x00004010u,
};
#endif

#ifdef NS6V
#if defined(NS6M) || defined(NS6W) || defined(NS6Q) || defined(NS6P)
#error "NS6V 独立形态"
#endif
// 烘焙墙+边界哨兵(BW[r+1] bit c+1); 异图退化=幻影墙40格+窗内直读, 正赛换图需重烘焙
constexpr uint32_t BAKED_W1[N] = {
    0x00004010u, 0x00000000u, 0x0001800cu, 0x00024012u, 0x00002020u, 0x00001040u,
    0x00004010u, 0x00000500u, 0x000028a0u, 0x00000500u, 0x00004010u, 0x00001040u,
    0x00002020u, 0x00024012u, 0x0001800cu, 0x00000000u, 0x00004010u,
};
struct BWT {
    uint32_t w[N + 2];
    constexpr BWT() : w() {
        w[0] = w[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) w[r + 1] = BAKED_W1[r] | 0xFFFC0001u;
    }
};
constexpr BWT BW;
const GameInput* g_in;               // decide 入口设置; pass01 直读弹/窗内墙
#endif

// 通行 = 非墙 且 (穷 或 非弹) 且 非队友
#ifdef NS6V
inline unsigned pass01(int r, int c, int tr, int tc, unsigned rich) {
    unsigned blocked = (BW.w[r + 1] >> (c + 1)) & 1u;   // 烘焙墙|边界(纯常量)
    unsigned inb = ((unsigned)r < (unsigned)N) & ((unsigned)c < (unsigned)N);
    int v = g_in->grid[r & -(int)inb][c & -(int)inb];   // 窗内实时: 墙/弹直读
    blocked |= inb & ((unsigned)(v == -1) | (rich & (unsigned)(v == -3)));
    return (~blocked & 1u) & (unsigned)((r != tr) | (c != tc));
}
#elif defined(NS6M)
inline unsigned pass01(int r, int c, int tr, int tc, unsigned rich) {
    uint64_t w = g_s.bb[r + 1];
    return (~(((uint32_t)w | (rich & (uint32_t)(w >> 32))) >> (c + 1)) & 1u) &
           (unsigned)((r != tr) | (c != tc));
}
#elif defined(NS6NT)
inline unsigned pass01(int r, int c, int tr, int tc, unsigned rich) {
    (void)tr; (void)tc;                          // 队友检查下岗(-20): 撞位由引擎记4+自愈
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 1])) >> (c + 1)) & 1u);
}
#else
inline unsigned pass01(int r, int c, int tr, int tc, unsigned rich) {
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 1])) >> (c + 1)) & 1u) &
           (unsigned)((r != tr) | (c != tc));
}
#endif

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

// LUT 导向: (dr,dc)∈[-3,3]² 行优先无阻挡模拟(动作+逐步累计位移)
// NS6L(源自 v2 的 v5o 打包): 每格一个 u32 [acts 3x3b @0..8 | (pdr+3) @9..17 | (pdc+3) @18..26]
struct SLut {
    uint8_t act[7][7][3];
    uint8_t fact[7][7][3];   // NS6F: 含早到折返的动作(d<3 预折叠)
    int8_t  pdr[7][7][3], pdc[7][7][3];
    uint32_t pk[7][7];
    constexpr SLut() : act(), fact(), pdr(), pdc(), pk() {
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
                    fact[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                    pk[dr + 3][dc + 3] |= ((uint32_t)a << (i * 3)) |
                        ((uint32_t)(r + 3) << (9 + i * 3)) |
                        ((uint32_t)(c + 3) << (18 + i * 3));
                }
                {   // NS6F: 早到折返预折叠(与运行时 fold 块逐位一致)
                    int d = (dr < 0 ? -dr : dr) + (dc < 0 ? -dc : dc);
                    if (d > 0 && d < 3) {
                        fact[dr + 3][dc + 3][d] =
                            (uint8_t)(fact[dr + 3][dc + 3][d - 1] ^ 1);
                        if (d == 1)
                            fact[dr + 3][dc + 3][2] =
                                (uint8_t)(fact[dr + 3][dc + 3][1] ^ 1);
                    }
                }
            }
    }
};
constexpr SLut SL;

GameOutput decide(const GameInput* in) {
#ifdef NS6V
    g_in = in;
#endif
#if defined(NS6P) || defined(NS6Q)
    const int act6 = in->round & 1;              // 轮换: 每轮一个单位全管线
#endif
#if defined(__AVX2__) && !defined(NS6FU)
    // 输入行前置装载: 行 miss 最早并行发射(轮换: 仅主动单位 5 行, 免溢出税)
    // (NS6FU 融合后此块整体下线: 载荷是热的, 前置无红利, rowbufs 溢出是纯税)
    __m256i rowbufs[2][5];
    int rb_oks[2] = {0, 0}, rb_cbs[2] = {0, 0};
#ifdef NS6Q
    {   // NS6Q: 单块直线装载, 零循环零位点
        const int lu = act6;
        int sr0 = in->my_units[lu].row, sc0 = in->my_units[lu].col;
        sr0 = sr0 < 0 ? 0 : (sr0 > 16 ? 16 : sr0);
        sc0 = sc0 < 0 ? 0 : (sc0 > 16 ? 16 : sc0);
        int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
        rb_oks[lu] = 1; rb_cbs[lu] = cb;
#pragma GCC unroll 5
        for (int i = 0; i < 5; ++i) {
            int rr = sr0 - 2 + i;
            int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
            rowbufs[lu][i] =
                _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
        }
    }
#else
    for (int lu = 0; lu < 2; ++lu) {
#ifdef NS6P
        if (lu != act6) continue;
#endif
        int sr0 = in->my_units[lu].row, sc0 = in->my_units[lu].col;
        sr0 = sr0 < 0 ? 0 : (sr0 > 16 ? 16 : sr0);
        sc0 = sc0 < 0 ? 0 : (sc0 > 16 ? 16 : sc0);
        {
#ifdef NS6C3
            int cb = SCT.cb[sc0];
#else
            int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
#endif
            rb_oks[lu] = 1; rb_cbs[lu] = cb;
#ifdef NS6T5
            {   // 时分五行: 每轮 3 载, 奇偶轮交替上/下半段; 槽位=真实窗口下标
                int ob = (in->round & 1) << 1;   // 0:{0,1,2} / 2:{2,3,4}, 中心行恒载
#pragma GCC unroll 3
                for (int k = 0; k < 3; ++k) {
                    int i = ob + k;
                    int rr = sr0 - 2 + i;
                    int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                    rowbufs[lu][i] =
                        _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
                }
                const __m256i fog = _mm256_set1_epi32(-5);
                rowbufs[lu][ob ^ 3] = fog;       // ob=0 -> 槽3,4 雾; ob=2 -> 槽1,0 雾
                rowbufs[lu][(ob ^ 3) + (ob ? -1 : 1)] = fog;
            }
#elif defined(NS6T3)
#pragma GCC unroll 3
            for (int i = 1; i < 4; ++i) {        // rows3: 6 条访存(理论: miss 链主导延迟)
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbufs[lu][i] =
                    _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
            rowbufs[lu][0] = rowbufs[lu][4] = _mm256_set1_epi32(-5);
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr0 - 2 + i;
#ifdef NS6C2
                int cr = RCL.v[rr + 2];
#else
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
#endif
                rowbufs[lu][i] =
                    _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
#endif
        }
    }
#endif
#endif
    if (in->round <= g_s.last_round) {           // 新局
        memset(&g_s, 0, sizeof(g_s));
#ifdef NS6M
        g_s.bb[0] = g_s.bb[N + 1] = 0xFFFFFFFFull;
        for (int r = 0; r < N; ++r) g_s.bb[r + 1] = 0xFFFC0001ull;
#else
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
#ifdef NS6W
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u | BAKED_W1[r];
#else
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u;
#endif
#endif
    }
    g_s.last_round = (int16_t)in->round;
#ifndef NS6V
    if (in->round % 20 == 0) {                   // 炸弹波: 弹记忆即弃
#ifdef NS6M
        for (int r = 0; r < N + 2; ++r) g_s.bb[r] &= 0xFFFFFFFFull;
#else
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
#endif
    }
#endif  // !NS6V


#ifdef NS6O
    GameOutput out;                              // 全字段必写, 免拷贝(-10)
#else
    GameOutput out = SAFE_OUT;
#endif
#ifdef NS6SHELL
    if (in->round >= 0) return out;              // 消融记账: 壳+前置装载+重置
#endif

#ifdef NS6Q
    {   // NS6Q: 被动单位无条件直线回放(计划缺位=全 STAY), 零位点
        const int pv = 1 - act6;
        int* pacts = out.actions + pv * 3;
        int pm_ = -(int)(g_s.plan_ok[pv] != 0);
        pacts[0] = (g_s.plan[pv][0] & pm_) | (STAY & ~pm_);
        pacts[1] = (g_s.plan[pv][1] & pm_) | (STAY & ~pm_);
        pacts[2] = (g_s.plan[pv][2] & pm_) | (STAY & ~pm_);
        g_s.plan_ok[pv] = 0;
        int psr = in->my_units[pv].row, psc = in->my_units[pv].col;
        psr = psr < 0 ? 0 : (psr > 16 ? 16 : psr);
        psc = psc < 0 ? 0 : (psc > 16 ? 16 : psc);
        g_s.last_r[pv] = (int8_t)psr; g_s.last_c[pv] = (int8_t)psc;
#if defined(NS6S) && defined(__AVX2__)
        {   // 被动微扫 3x5(仅金掩码): 新鲜目标掩码覆盖计划, 零位点
            int cb2 = psc - 2 < 0 ? 0 : (psc - 2 > N - 5 ? N - 5 : psc - 2);
            int lsh2 = 2 + (psc - 2 - cb2);
            int lo2 = psc - 2 < 0 ? -(psc - 2) : 0;
            int hix2 = psc + 2 > N - 1 ? psc + 2 - (N - 1) : 0;
            uint32_t colv2 = ((31u >> hix2) & (31u << lo2)) & 31u;
            uint32_t g15 = 0;
            const __m256i vz2 = _mm256_setzero_si256();
#pragma GCC unroll 3
            for (int i = 0; i < 3; ++i) {
                int rr = psr - 1 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                __m256i vrow = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb2]);
#if defined(NS6K) && defined(__AVX512VL__)
                uint32_t g8 = (uint32_t)_mm256_cmpgt_epi32_mask(vrow, vz2);
#else
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, vz2)));
#endif
                g15 |= ((((g8 << 2) >> lsh2) & 31u) & (colv2 & rowok)) << (i * 5);
            }
            g15 &= ~(1u << 7);                   // 弃中心格(脚下新刷吃不到)
            constexpr uint32_t Q1 = 0x1144u, Q2 = 0x2A2Au, Q3 = 0x4411u;
            uint32_t q1 = g15 & Q1, q2 = g15 & Q2, q3 = g15 & Q3;
            uint32_t n1 = (uint32_t)0 - (q1 != 0);
            uint32_t n2 = ((uint32_t)0 - (q2 != 0)) & ~n1;
            uint32_t n3 = ((uint32_t)0 - (q3 != 0)) & ~n1 & ~n2;
            uint32_t qsel = (q1 & n1) | (q2 & n2) | (q3 & n3);
            int qi = __builtin_ctz(qsel | (uint32_t)(qsel == 0));
            int qdr = qi / 5 - 1, qdc = qi % 5 - 2;
            const uint8_t* za = SL.act[qdr + 3][qdc + 3];
            const int8_t* zr = SL.pdr[qdr + 3][qdc + 3];
            const int8_t* zc = SL.pdc[qdr + 3][qdc + 3];
            unsigned rich2 = 0u - (unsigned)(in->my_units_gold[pv] >= 100);
            int atr = in->my_units[act6].row, atc = in->my_units[act6].col;
            int ok3 = -(int)(g15 != 0) &
                      -(int)(pass01(psr + zr[0], psc + zc[0], atr, atc, rich2) & 1u);
            pacts[0] = (za[0] & ok3) | (pacts[0] & ~ok3);
            pacts[1] = (za[1] & ok3) | (pacts[1] & ~ok3);
            pacts[2] = (za[2] & ok3) | (pacts[2] & ~ok3);
        }
#endif
    }
    for (int u = act6; u <= act6; ++u) {         // 单迭代: 编译器摊平, 无回边
#elif defined(NS6U1)
    for (int u = 0; u < 1; ++u) {                // 消融记账: 单单位管线
#elif defined(NS6D2)
#pragma GCC unroll 2
    for (int u = 0; u < 2; ++u) {                // 直线化二连体(消回边+ILP)
#else
    for (int u = 0; u < 2; ++u) {
#endif
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
#ifndef NS6NC
        sr = sr < 0 ? 0 : (sr > 16 ? 16 : sr);        // 单位恒在板上; cmov 双钳防御
        sc = sc < 0 ? 0 : (sc > 16 ? 16 : sc);
#endif
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
#ifdef NS6P
        if (u != act6) {                         // 被动轮: 回放上轮预算的真计划(~18 指令)
            int m = -(int)(g_s.plan_ok[u] != 0);
            acts[0] = (g_s.plan[u][0] & m) | (STAY & ~m);
            acts[1] = (g_s.plan[u][1] & m) | (STAY & ~m);
            acts[2] = (g_s.plan[u][2] & m) | (STAY & ~m);
            g_s.plan_ok[u] = 0;
            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
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
#ifdef NS6FU
            int cb = 0; (void)cb;                // 融合路径: cb 内联于载入
#else
            int cb = rb_cbs[u];
#endif
#ifdef NS6C3
            int lsh = SCT.lsh[sc];
            uint32_t colv = SCT.colv[sc];
            (void)cb;
#else
            int lsh = 2 + (sc - 2 - cb);
            int lo = sc - 2 < 0 ? -(sc - 2) : 0;
            int hix = sc + 2 > N - 1 ? sc + 2 - (N - 1) : 0;
            uint32_t colv = ((31u >> hix) & (31u << lo)) & 31u;
#endif
#ifdef NS6V
            // 纯金扫描: 墙=常量, 弹=pass01 直读 -> 记账全免
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                __m256i vrow = rowbufs[u][i];
#if defined(NS6K) && defined(__AVX512VL__)
                uint32_t g8 = (uint32_t)_mm256_cmpgt_epi32_mask(vrow, vz);
#else
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, vz)));
#endif
                goldm |= ((((g8 << 2) >> lsh) & 31u) & (colv & rowok)) << (i * 5);
            }
            (void)vm1; (void)vm3;
#else
#ifdef NS6W
            {
            uint32_t bombm2 = 0;
#ifdef NS6FU
            int cb_ = SCT.cb[sc];
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                int cr_ = RCL.v[rr + 2];
                __m256i vrow = _mm256_loadu_si256(
                    (const __m256i*)&in->grid[cr_][cb_]);
#else
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                __m256i vrow = rowbufs[u][i];
#endif
#if defined(NS6K) && defined(__AVX512VL__)
                uint32_t g8 = (uint32_t)_mm256_cmpgt_epi32_mask(vrow, vz);
                uint32_t b8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, vm3);
#else
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, vz)));
                uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm3)));
#endif
                uint32_t rv = colv & rowok;
                goldm |= ((((g8 << 2) >> lsh) & 31u) & rv) << (i * 5);
#ifdef NS6B3
                if (i >= 1 && i <= 3)            // 编译期常量条件: 弹仅 ±1 行(-30)
#endif
                bombm2 |= ((((b8 << 2) >> lsh) & 31u) & rv) << (i * 5);
            }
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                int ri = ((unsigned)rr < (unsigned)N ? rr : 0) + 1;
                int shl = sc - 1;
                uint32_t bsl = (bombm2 >> (i * 5)) & 31u;
                uint32_t bv2 = shl >= 0 ? (bsl << shl) : (bsl >> -shl);
                g_s.bombbit[ri] |= bv2;
            }
            }
#else
            uint32_t wallm = 0, bombm = 0;
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                __m256i vrow = rowbufs[u][i];
#if defined(NS6K) && defined(__AVX512VL__)
                uint32_t g8 = (uint32_t)_mm256_cmpgt_epi32_mask(vrow, vz);
                uint32_t w8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, vm1);
                uint32_t b8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, vm3);
#else
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, vz)));
                uint32_t w8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm1)));
                uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm3)));
#endif
                uint32_t rv = colv & rowok;
                goldm |= ((((g8 << 2) >> lsh) & 31u) & rv) << (i * 5);
#ifndef NS6XW
                wallm |= ((((w8 << 2) >> lsh) & 31u) & rv) << (i * 5);
                bombm |= ((((b8 << 2) >> lsh) & 31u) & rv) << (i * 5);
#endif
            }
            // 墙/弹入位图: 无条件 5 行行片写(零分支; 空片写=无操作)
            // 行片: 窗口行 i 的 5 位 << (sc-2+1); 行索引钳位由 rowok 已保证片为 0
#ifndef NS6XW
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                int ri = ((unsigned)rr < (unsigned)N ? rr : 0) + 1;
                int shl = sc - 1;                // (sc-2)+1, 可为负
                uint32_t wsl = (wallm >> (i * 5)) & 31u;
                uint32_t bsl = (bombm >> (i * 5)) & 31u;
                uint32_t wv = shl >= 0 ? (wsl << shl) : (wsl >> -shl);
                uint32_t bv = shl >= 0 ? (bsl << shl) : (bsl >> -shl);
#ifdef NS6M
                g_s.bb[ri] |= (uint64_t)wv | ((uint64_t)bv << 32);
#else
                g_s.bpw[ri] |= wv;
                g_s.bombbit[ri] |= bv;
#endif
            }
#endif
#endif  // NS6W
#endif  // !NS6V
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
#ifdef NS6M
                else if (v == -1) g_s.bb[rr + 1] |= 1ull << (cc + 1);
                else if (v == -3) g_s.bb[rr + 1] |= 1ull << (cc + 1 + 32);
#else
                else if (v == -1) g_s.bpw[rr + 1] |= 1u << (cc + 1);
                else if (v == -3) g_s.bombbit[rr + 1] |= 1u << (cc + 1);
#endif
            }
        }
#endif
        // (金格与弹格规则上不共存 —— 目标无需剔弹, 途中避弹由 pass01 管)

#ifdef NS6XT
        (void)goldm; g_s.last_r[u]=(int8_t)sr; g_s.last_c[u]=(int8_t)sc; continue;
#endif
        // ---- 目标: 最近金(环掩码) 否则锚点 ----
        int tgr, tgc;
#ifdef NS6RT
        int rt_blind;                            // 盲轮标志提前折算(免 goldm 活跃期拖长)
#endif
#ifdef NS6R
        int vtg = 0;                             // 目标格现值(计划枯竭判定用)
#endif
        {
            constexpr uint32_t RM0 = 1u << 12;
            constexpr uint32_t RM1 = (1u<<7)|(1u<<11)|(1u<<13)|(1u<<17);
            constexpr uint32_t RM2 = (1u<<2)|(1u<<6)|(1u<<8)|(1u<<10)|(1u<<14)|(1u<<16)|(1u<<18)|(1u<<22);
            constexpr uint32_t RM3 = (1u<<1)|(1u<<3)|(1u<<5)|(1u<<9)|(1u<<15)|(1u<<19)|(1u<<21)|(1u<<23);
            constexpr uint32_t RM4 = (1u<<0)|(1u<<4)|(1u<<20)|(1u<<24);
#if defined(NS6X) && defined(__BMI2__)
            // pext 重排: 位序=环优先级(1>2>3>4>0), 一次 ctz 完成级联(纯等价 -40)
            static constexpr uint8_t REMAP[26] = {
                7,11,13,17, 2,6,8,10,14,16,18,22, 1,3,5,9,15,19,21,23, 0,4,20,24, 12, 12};
            uint32_t re = _pext_u32(goldm, RM1) | (_pext_u32(goldm, RM2) << 4) |
                          (_pext_u32(goldm, RM3) << 12) | (_pext_u32(goldm, RM4) << 20) |
                          (((goldm >> 12) & 1u) << 24);
            (void)RM0;
            int i = REMAP[__builtin_ctz(re | (uint32_t)(re == 0)) & 31];
#else
            uint32_t g1 = goldm & RM1, g2 = goldm & RM2, g3 = goldm & RM3;
            uint32_t g4 = goldm & RM4, g0 = goldm & RM0;
            uint32_t m1 = (uint32_t)0 - (g1 != 0);
            uint32_t m2 = ((uint32_t)0 - (g2 != 0)) & ~m1;
            uint32_t m3 = ((uint32_t)0 - (g3 != 0)) & ~m1 & ~m2;
            uint32_t m4 = ((uint32_t)0 - (g4 != 0)) & ~m1 & ~m2 & ~m3;
            uint32_t m0 = ~m1 & ~m2 & ~m3 & ~m4;
            uint32_t sel = (g1 & m1) | (g2 & m2) | (g3 & m3) | (g4 & m4) | (g0 & m0);
            int i = __builtin_ctz(sel | (uint32_t)(sel == 0));   // 仅空时补位(| 1u 恒补是v4d/v5崩盘元凶)
#endif
            int has = -(int)(goldm != 0);
#ifdef NS6RT
            rt_blind = ~has;
#endif
#ifdef NS6C2
            tgr = ((sr - 2 + DM5.d[i]) & has) | (ANCH_R[u] & ~has);
            tgc = ((sc - 2 + DM5.m[i]) & has) | (ANCH_C[u] & ~has);
#else
            tgr = ((sr - 2 + i / 5) & has) | (ANCH_R[u] & ~has);
            tgc = ((sc - 2 + i % 5) & has) | (ANCH_C[u] & ~has);
#endif
#ifdef NS6R
            {   // 迷你堆记忆(全掩码零位点): 盲轮弃锚点奔已知堆; 追的肥格登记入槽
                unsigned fresh = (unsigned)
                    ((uint8_t)((uint8_t)(in->round >> 2) - g_s.pile_t) <= 10u);
                int pvm = -(int)((unsigned)(g_s.pile_v > 0) & fresh);
                int rm = ~has & pvm;             // 无金且有活堆 → 重定向
                tgr = (g_s.pile_r & rm) | (tgr & ~rm);
                tgc = (g_s.pile_c & rm) | (tgc & ~rm);
                int vt = in->grid[tgr][tgc];     // 目标格现值(仅 has 时采信)
                vtg = vt & has;
                int reg = has & -(int)(vt >= 5);
                g_s.pile_r = (int8_t)((tgr & reg) | (g_s.pile_r & ~reg));
                g_s.pile_c = (int8_t)((tgc & reg) | (g_s.pile_c & ~reg));
                g_s.pile_v = (uint8_t)((vt & reg) | (g_s.pile_v & ~reg));
                g_s.pile_t = (uint8_t)(((in->round >> 2) & reg) | (g_s.pile_t & ~reg));
                unsigned at = (unsigned)((sr == g_s.pile_r) & (sc == g_s.pile_c));
                g_s.pile_v &= (uint8_t)(at - 1u);   // 站上堆位即失效(残值下轮重估)
            }
#endif
        }

        // ---- 导向 ----
        int dr0 = tgr - sr, dc0 = tgc - sc;
        dr0 = dr0 < -3 ? -3 : (dr0 > 3 ? 3 : dr0);   // 钳进 LUT 域:
        dc0 = dc0 < -3 ? -3 : (dc0 > 3 ? 3 : dc0);   // 远目标前3步与串行链同构
        int d = (dr0 < 0 ? -dr0 : dr0) + (dc0 < 0 ? -dc0 : dc0);
        if (d == 0) {                            // 站金: 折返双吃
            unsigned pm = pass01(sr - 1, sc, tr, tc, rich) |
                          (pass01(sr + 1, sc, tr, tc, rich) << 1) |
                          (pass01(sr, sc - 1, tr, tc, rich) << 2) |
                          (pass01(sr, sc + 1, tr, tc, rich) << 3);
            if (pm) {
                int a = __builtin_ctz(pm);
                acts[0] = a; acts[1] = a ^ 1;
            }
        } else {
            int ir = dr0 + 3, ic = dc0 + 3;
#ifdef NS6L
            uint32_t pkv = SL.pk[ir][ic];        // 1 载荷替代 3 数组 6+ 载荷(v5o)
            unsigned ok = pass01(sr + (int)((pkv >> 9) & 7) - 3,
                                 sc + (int)((pkv >> 18) & 7) - 3, tr, tc, rich) &
                          pass01(sr + (int)((pkv >> 12) & 7) - 3,
                                 sc + (int)((pkv >> 21) & 7) - 3, tr, tc, rich) &
                          pass01(sr + (int)((pkv >> 15) & 7) - 3,
                                 sc + (int)((pkv >> 24) & 7) - 3, tr, tc, rich);
            if (ok) {
                acts[0] = (int)(pkv & 7); acts[1] = (int)((pkv >> 3) & 7);
                acts[2] = (int)((pkv >> 6) & 7);
#else
#ifdef NS6F
            const uint8_t* pa = SL.fact[ir][ic];     // 含预折返
#else
            const uint8_t* pa = SL.act[ir][ic];
#endif
            const int8_t* xr = SL.pdr[ir][ic];
            const int8_t* xc = SL.pdc[ir][ic];
            unsigned ok = pass01(sr + xr[0], sc + xc[0], tr, tc, rich) &
                          pass01(sr + xr[1], sc + xc[1], tr, tc, rich) &
                          pass01(sr + xr[2], sc + xc[2], tr, tc, rich);
            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
#endif
#ifdef NS6F
                ;                                    // 折返已预折叠进 fact
#else
                // 早到金格(d<3): 折返双吃(掩码写, 零位点)
                int em = -(int)(d < 3);
                int i1 = (d & em) | (2 & ~em);        // em=0 时写 acts[2] 自身值
                int v1 = (acts[i1 - (1 & em)] ^ (1 & em));
                acts[i1] = (v1 & em) | (acts[i1] & ~em);
                int e2 = em & -(int)(d + 1 < 3);      // 仅 d==1
                acts[2] = ((acts[1] ^ 1) & e2) | (acts[2] & ~e2);
#endif
            } else {
                // 受阻(罕见): 单步谨慎, 其余 STAY(下轮恢复) —— steer3 全链蒸发
                int a = steerStep(sr, sc, tgr, tgc, tr, tc,
                                  g_s.last_r[u], g_s.last_c[u], rich);
                if (a >= 0) acts[0] = a;
            }
        }

#ifdef NS5DBG
        if (in->round >= 39 && in->round <= 43) {
            fprintf(stderr, "r%d u%d pos(%d,%d) gold%d rich%u goldm=%08x tg(%d,%d) acts[%d,%d,%d]\n",
                in->round, u, sr, sc, in->my_units_gold[u], rich & 1u,
                goldm, tgr, tgc, acts[0], acts[1], acts[2]);
        }
#endif
#if (defined(NS6P) || defined(NS6Q)) && !defined(NS6PNP)
        {   // 预算被动轮 3 步(廉价版): 复用 LUT, 零函数调用
            // acts 已验证 → 无查表直推终点
            int r = sr + DR[acts[0]] + DR[acts[1]] + DR[acts[2]];
            int c = sc + DC[acts[0]] + DC[acts[1]] + DC[acts[2]];
            int rdr = tgr - r, rdc = tgc - c;
#ifdef NS6Q
            // 零位点: 折返/LUT续走/沿向回退 三案全算, 掩码级联选择
            // (acts 逆推免 pass01 版判负 -500: 150114 vs 150133, 勿复活)
            unsigned pm = pass01(r - 1, c, tr, tc, rich) |
                          (pass01(r + 1, c, tr, tc, rich) << 1) |
                          (pass01(r, c - 1, tr, tc, rich) << 2) |
                          (pass01(r, c + 1, tr, tc, rich) << 3);
            int ba = __builtin_ctz(pm | (uint32_t)(pm == 0));
            int bm = -(int)(pm != 0);
            int b0 = (ba & bm) | (STAY & ~bm);
            int b1 = ((ba ^ 1) & bm) | (STAY & ~bm);
#ifdef NS6R
            // 枯竭重定向(治回声折返): 站上目标但本轮吃后残值<2 → 计划弃折返改奔堆/锚
            int sel0 = -(int)((rdr | rdc) == 0);
            int drain = sel0 & -(int)(vtg < 6);
            unsigned fr2 = (unsigned)
                ((uint8_t)((uint8_t)(in->round >> 2) - g_s.pile_t) <= 10u);
            int pv2 = -(int)((unsigned)(g_s.pile_v > 0) & fr2);
            int nr2 = (g_s.pile_r & pv2) | (ANCH_R[u] & ~pv2);
            int nc2 = (g_s.pile_c & pv2) | (ANCH_C[u] & ~pv2);
            rdr = ((nr2 - r) & drain) | (rdr & ~drain);
            rdc = ((nc2 - c) & drain) | (rdc & ~drain);
#endif
            int pdr0 = rdr < -3 ? -3 : (rdr > 3 ? 3 : rdr);
            int pdc0 = rdc < -3 ? -3 : (rdc > 3 ? 3 : rdc);
            int jr = pdr0 + 3, jc = pdc0 + 3;
            const uint8_t* qa = SL.act[jr][jc];
            const int8_t* qr = SL.pdr[jr][jc];
            const int8_t* qc2 = SL.pdc[jr][jc];
            int lm = -(int)((pass01(r + qr[0], c + qc2[0], tr, tc, rich) &
                             pass01(r + qr[1], c + qc2[1], tr, tc, rich) &
                             pass01(r + qr[2], c + qc2[2], tr, tc, rich)) & 1u);
            int fa = acts[2];                    // 回退: 沿本轮末向续走(治被动站桩)
            int fm = -(int)((pass01(r + DR[fa], c + DC[fa], tr, tc, rich) &
                             (unsigned)(fa != STAY)) & 1u) & ~lm;
            int l0 = (qa[0] & lm) | (fa & fm) | (STAY & ~lm & ~fm);
            int l1 = (qa[1] & lm) | (fa & fm) | (STAY & ~lm & ~fm);
            int l2 = (qa[2] & lm) | (fa & fm) | (STAY & ~lm & ~fm);
            int sel = -(int)((rdr | rdc) == 0);  // 终点=目标 → 折返续吃
            g_s.plan[u][0] = (int8_t)((b0 & sel) | (l0 & ~sel));
            g_s.plan[u][1] = (int8_t)((b1 & sel) | (l1 & ~sel));
            g_s.plan[u][2] = (int8_t)((STAY & sel) | (l2 & ~sel));
#else
            if ((rdr | rdc) == 0) {              // 终点=目标: 计划折返续吃残值
                unsigned pm = pass01(r - 1, c, tr, tc, rich) |
                              (pass01(r + 1, c, tr, tc, rich) << 1) |
                              (pass01(r, c - 1, tr, tc, rich) << 2) |
                              (pass01(r, c + 1, tr, tc, rich) << 3);
                int a = __builtin_ctz(pm | (uint32_t)(pm == 0));
                int m = -(int)(pm != 0);
                g_s.plan[u][0] = (int8_t)((a & m) | (STAY & ~m));
                g_s.plan[u][1] = (int8_t)(((a ^ 1) & m) | (STAY & ~m));
                g_s.plan[u][2] = STAY;
            } else {                             // 剩余位移再查一次 LUT + 途经并行验证
                int pdr0 = rdr < -3 ? -3 : (rdr > 3 ? 3 : rdr);
                int pdc0 = rdc < -3 ? -3 : (rdc > 3 ? 3 : rdc);
                int jr = pdr0 + 3, jc = pdc0 + 3;
                const uint8_t* qa = SL.act[jr][jc];
                const int8_t* qr = SL.pdr[jr][jc];
                const int8_t* qc = SL.pdc[jr][jc];
                unsigned ok2 = pass01(r + qr[0], c + qc[0], tr, tc, rich) &
                               pass01(r + qr[1], c + qc[1], tr, tc, rich) &
                               pass01(r + qr[2], c + qc[2], tr, tc, rich);
                int m = -(int)(ok2 & 1u);
                g_s.plan[u][0] = (int8_t)((qa[0] & m) | (STAY & ~m));
                g_s.plan[u][1] = (int8_t)((qa[1] & m) | (STAY & ~m));
                g_s.plan[u][2] = (int8_t)((qa[2] & m) | (STAY & ~m));
            }
#endif
            g_s.plan_ok[u] = 1;
        }
#endif
#ifdef NS6RT
        // 开局行军(定律3: 4/500 轮的功能用门控省功, 掩码版 +73 指令判负)
        if (__builtin_expect(in->round < 4, 0)) {
            int ri = in->round & 3;
            if (rt_blind & -(int)((sr == ORT_R[u][ri]) & (sc == ORT_C[u][ri]))) {
                acts[0] = ORT_A[u][ri][0];
                acts[1] = ORT_A[u][ri][1];
                acts[2] = ORT_A[u][ri][2];
            }
        }
#endif
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
#ifdef NS6NS
        return decide(input);                    // 输出全路径可证合法(军械库下岗, -24)
#else
        return sanitize(decide(input));
#endif
    } catch (...) {
        return SAFE_OUT;
    }
}
