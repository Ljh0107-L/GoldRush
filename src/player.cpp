// player.cpp — GoldRush 2.0 现役冠军 (2026-08-09 定稿)
//
// 战绩: P50 中位 200ns(读数 170-230) / P90 ~292 / 收入中位 ~1515
//       对 Tiuntled-1 先手 85-96%; 指令 659/调用; 演进史与判决见 CHANGELOG.md
// 成本模型(设计新算法前必读): 见 INFRA.md —— 壳40ns + 载荷~2ns/条 + 指令×0.2ns
//
// ============ 每轮决策 loop ============
// 入口: moveDecision → try{ decide } catch{ SAFE_OUT }   (输出全路径可证合法, 无钳位)
// 1. 新局检测(round 回绕) → 重置状态, bpw = 边界哨兵 | 烘焙墙(map1 常量, 换图须重烘焙!)
// 2. 炸弹波清(每 20 轮): bombbit 清零, 等扫描重建
// 3. 对每个单位(双全管线, 无轮换):
//    3.1 富度门: 持金≥100 才把炸弹并入阻挡(穷单位踩弹烧 10%×0=0, 弹透明)
//    3.2 扫描: 5×5 窗口 5 行就地 AVX 载入(输入行是热的, 无需前置装载),
//        k掩码比较 → goldm 25位(金) / bombm 15位(弹, 仅±1行) → 弹入位图
//    3.3 目标: goldm 按环距优先级 pext 重排 + ctz = 最近金格; 无金 → 分区锚点(6,6)/(10,10)
//    3.4 站金(d==0): 折返双吃 —— 出格再回格, 链式收 35% 残值
//    3.5 行进: LUT 三步导向(constexpr 表, 早到折返已预折叠) + pass01 途经验证;
//        受阻(罕见, 墙已全知, 仅弹/异物) → 单步谨慎 + 下轮自愈
//    3.6 开局行军(round<4): 烘焙 BFS 路线 4 轮出角(墙口袋原爬 9-10 轮);
//        盲轮+位置吻合才生效, 漂移自弃 —— 低频功能用恒预测门控(省功不省位点)
// 4. 输出: k=3, order=持金多者先走, vp=0(不买视野)
//
// ============ 已退役的防御(可证冗余, 详见 CHANGELOG 军规 12) ============
// sanitize 输出钳位 / 入口坐标钳位 / pass01 队友检查(撞位实测 0 轮) —— try/catch 永不下岗
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
    uint32_t bpw[N + 2];     // 墙|边界哨兵位图(bit c+1; 开局灌入烘焙墙后只读)
    uint32_t bombbit[N + 2]; // 炸弹位图(+1 偏移对齐 bpw; 每 20 轮波清)
    int8_t last_r[2], last_c[2];
    int16_t last_round;
};
State g_s;

constexpr int8_t ANCH_R[2] = {6, 10};
constexpr int8_t ANCH_C[2] = {6, 10};

// map1 墙表(40 格, 多局日志比对恒定)。⚠ 换图必须重烘焙:
//   python: 取任意该图日志第 2 行, 值==1 的格按 bit(c+1) 打包成每行 u32
constexpr uint32_t BAKED_W1[N] = {
    0x00004010u, 0x00000000u, 0x0001800cu, 0x00024012u, 0x00002020u, 0x00001040u,
    0x00004010u, 0x00000500u, 0x000028a0u, 0x00000500u, 0x00004010u, 0x00001040u,
    0x00002020u, 0x00024012u, 0x0001800cu, 0x00000000u, 0x00004010u,
};

// 开局烘焙路线(map1 BFS 最优 4 轮出角; 起点恒 (0,0)/(16,16))
constexpr uint8_t ORT_A[2][4][3] = {
    {{1,3,3},{3,1,3},{1,3,1},{3,1,1}},           // u0 (0,0)->(6,6)
    {{0,2,2},{2,0,2},{0,2,0},{2,0,0}},           // u1 (16,16)->(10,10)
};
constexpr int8_t ORT_R[2][4] = {{0,1,2,4},{16,15,14,12}};
constexpr int8_t ORT_C[2][4] = {{0,2,4,5},{16,14,12,11}};

// 扫描边缘常量表: cb(载入列基)/lsh(位对齐)/colv(有效列掩码) 均为 sc 纯函数
struct SctT {
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

struct RclT {                                    // 行钳位表: 2 cmov -> 1 载
    int8_t v[21];
    constexpr RclT() : v() {
        for (int x = 0; x < 21; ++x) { int t = x - 2; v[x] = (int8_t)(t < 0 ? 0 : (t > 16 ? 16 : t)); }
    }
};
constexpr RclT RCL;

struct Dm5T {                                    // 除模5 表
    int8_t d[25], m[25];
    constexpr Dm5T() : d(), m() { for (int x = 0; x < 25; ++x) { d[x] = (int8_t)(x / 5); m[x] = (int8_t)(x % 5); } }
};
constexpr Dm5T DM5;

// 通行 = 非墙 且 (穷 或 非弹)。队友检查已退役(撞位实测 0 轮, 引擎记4+自愈兜底)
inline unsigned pass01(int r, int c, unsigned rich) {
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 1])) >> (c + 1)) & 1u);
}

__attribute__((noinline, cold))
int escapeStep(int r, int c, int pr, int pc, unsigned rich) {
    for (int a = 0; a < 4; ++a) {
        int nr = r + DR[a], nc = c + DC[a];
        if (nr == pr && nc == pc) continue;      // 禁回头格(防振荡)
        if (pass01(nr, nc, rich)) return a;
    }
    return -1;
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
}

// LUT 导向: (dr,dc)∈[-3,3]² 行优先无阻挡模拟
// fact = 动作序列(早到折返 d<3 已预折叠); pdr/pdc = 逐步累计位移(途经验证用)
struct SLut {
    uint8_t fact[7][7][3];
    int8_t  pdr[7][7][3], pdc[7][7][3];
    constexpr SLut() : fact(), pdr(), pdc() {
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
                    fact[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                }
                {   // 早到折返预折叠(语义与旧运行时 fold 块逐位一致)
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
    if (in->round <= g_s.last_round) {           // 新局: 重置 + 烘焙墙直灌
        memset(&g_s, 0, sizeof(g_s));
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u | BAKED_W1[r];
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0)                     // 炸弹波: 弹记忆即弃
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));

    GameOutput out;                              // 全字段必写, 免 SAFE_OUT 拷贝

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        unsigned rich = 0u - (unsigned)(in->my_units_gold[u] >= 100);

        // ---- 扫描: 5 行就地载入 + 掩码提取 ----
        uint32_t goldm = 0;
#if defined(__AVX2__)
        {
            const __m256i vz = _mm256_setzero_si256();
            const __m256i vm3 = _mm256_set1_epi32(-3);
            int lsh = SCT.lsh[sc];
            uint32_t colv = SCT.colv[sc];
            uint32_t bombm = 0;
            int cb = SCT.cb[sc];
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                int cr = RCL.v[rr + 2];
                __m256i vrow = _mm256_loadu_si256(
                    (const __m256i*)&in->grid[cr][cb]);
#if defined(__AVX512VL__)
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
                if (i >= 1 && i <= 3)            // 编译期常量条件: 弹仅记 ±1 行
                bombm |= ((((b8 << 2) >> lsh) & 31u) & rv) << (i * 5);
            }
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {        // 弹行片写(空片写=无操作, 零分支)
                int rr = sr - 2 + i;
                int ri = ((unsigned)rr < (unsigned)N ? rr : 0) + 1;
                int shl = sc - 1;
                uint32_t bsl = (bombm >> (i * 5)) & 31u;
                uint32_t bv = shl >= 0 ? (bsl << shl) : (bsl >> -shl);
                g_s.bombbit[ri] |= bv;
            }
        }
#else
        for (int i = 0; i < 5; ++i) {            // 标量参考(仅本机测试)
            int rr = sr - 2 + i;
            if ((unsigned)rr >= (unsigned)N) continue;
            for (int j = 0; j < 5; ++j) {
                int cc = sc - 2 + j;
                if ((unsigned)cc >= (unsigned)N) continue;
                int v = in->grid[rr][cc];
                if (v > 0) goldm |= 1u << (i * 5 + j);
                else if (v == -3) g_s.bombbit[rr + 1] |= 1u << (cc + 1);
            }
        }
#endif
        // (金格与弹格规则上不共存 —— 目标无需剔弹, 途中避弹由 pass01 管)

        // ---- 目标: 最近金(环距优先) 否则锚点 ----
        int tgr, tgc;
        int blind;
        {
            constexpr uint32_t RM0 = 1u << 12;
            constexpr uint32_t RM1 = (1u<<7)|(1u<<11)|(1u<<13)|(1u<<17);
            constexpr uint32_t RM2 = (1u<<2)|(1u<<6)|(1u<<8)|(1u<<10)|(1u<<14)|(1u<<16)|(1u<<18)|(1u<<22);
            constexpr uint32_t RM3 = (1u<<1)|(1u<<3)|(1u<<5)|(1u<<9)|(1u<<15)|(1u<<19)|(1u<<21)|(1u<<23);
            constexpr uint32_t RM4 = (1u<<0)|(1u<<4)|(1u<<20)|(1u<<24);
#if defined(__BMI2__)
            // pext 重排: 位序=环优先级(1>2>3>4>0), 一次 ctz 完成级联
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
            int i = __builtin_ctz(sel | (uint32_t)(sel == 0));   // 仅空时补位(恒补 bit0 是历史崩盘元凶)
#endif
            int has = -(int)(goldm != 0);
            blind = ~has;
            tgr = ((sr - 2 + DM5.d[i]) & has) | (ANCH_R[u] & ~has);
            tgc = ((sc - 2 + DM5.m[i]) & has) | (ANCH_C[u] & ~has);
        }

        // ---- 导向 ----
        int dr0 = tgr - sr, dc0 = tgc - sc;
        dr0 = dr0 < -3 ? -3 : (dr0 > 3 ? 3 : dr0);   // 钳进 LUT 域:
        dc0 = dc0 < -3 ? -3 : (dc0 > 3 ? 3 : dc0);   // 远目标前3步与逐步串行同构
        int d = (dr0 < 0 ? -dr0 : dr0) + (dc0 < 0 ? -dc0 : dc0);
        if (d == 0) {                            // 站金: 折返双吃
            unsigned pm = pass01(sr - 1, sc, rich) |
                          (pass01(sr + 1, sc, rich) << 1) |
                          (pass01(sr, sc - 1, rich) << 2) |
                          (pass01(sr, sc + 1, rich) << 3);
            if (pm) {
                int a = __builtin_ctz(pm);
                acts[0] = a; acts[1] = a ^ 1;
            }
        } else {
            int ir = dr0 + 3, ic = dc0 + 3;
            const uint8_t* pa = SL.fact[ir][ic];
            const int8_t* xr = SL.pdr[ir][ic];
            const int8_t* xc = SL.pdc[ir][ic];
            unsigned ok = pass01(sr + xr[0], sc + xc[0], rich) &
                          pass01(sr + xr[1], sc + xc[1], rich) &
                          pass01(sr + xr[2], sc + xc[2], rich);
            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
            } else {
                // 受阻(罕见, 墙已全知): 单步谨慎, 其余 STAY, 下轮自愈
                int a = steerStep(sr, sc, tgr, tgc,
                                  g_s.last_r[u], g_s.last_c[u], rich);
                if (a >= 0) acts[0] = a;
            }
        }

        // ---- 开局行军(低频功能: 恒预测门控省功) ----
        if (__builtin_expect(in->round < 4, 0)) {
            int ri = in->round & 3;
            if (blind & -(int)((sr == ORT_R[u][ri]) & (sc == ORT_C[u][ri]))) {
                acts[0] = ORT_A[u][ri][0];
                acts[1] = ORT_A[u][ri][1];
                acts[2] = ORT_A[u][ri][2];
            }
        }
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = 0;
    return out;
}

}  // namespace

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
        return decide(input);
    } catch (...) {
        return SAFE_OUT;
    }
}
