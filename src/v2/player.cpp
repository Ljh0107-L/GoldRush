// src/v2/player.cpp — v2 主线: 300ns 预算的延迟优先重写
//
// ns330 = 阶段2 直线化(np9alt/np12alt 定靶: B2 300-430 + C 350-450 / 主动轮):
//         ①炸弹表 SoA + 固定8行程无分支对账(原 ~16 分支位点 -> 1)
//         ②goal 校验 cmov 链化 ③被动折返/离返 ctz 掩码化 ④fill besta cmov
// ns329 = 代码/输入节食: ①砍敌情块(只写不读, 白读 visible_enemies 线)
//         ②整段砍 NPC 折价(ns325 已证收入中性, 却每轮读 2 条 NPC 输入线)
//         ③砍 wall[] 位图(由 bp&~bombbit 推导, 状态 -68B, 扫描单写)
//         ④输入行前置装载(函数入口发射 5 行 load, A 段账务藏进 miss 阴影;
//           np13 解剖: B1 656 独扫比双扫 421 还贵 = 丢了第二条 miss 流掩护)
// ns327 = 轮换单管线(-DNS_ALT): 每轮只跑 active=round&1 单位的
//         扫描+对账+打分, 被动单位走缓存目标导向/就地折返(吃残堆)。
//         预算重分配: B1/B2 减半+C 大减, 目标 P50 ~800; 收入代价批量实测
// ns326 = D 段瘦身(np12 遥测: fill 211 + stuck/护栏 158 = 369 周期首次入账):
//         fill/护栏位置推进无分支化(墙表掩码索引) + 护栏近弹预筛(搭炸弹
//         对账环便车, 曼哈顿<=3 无弹整段跳过)。语义逐位等价(pair_diff 验)
// ns325 = 窗口折价删除(负结果入册: v2 1μs 行动先于 NPC, 窗口竞速必赢,
//         折价=弃金, ns323 批量 1525 vs ns322 1695 且两局崩盘);
//         仅保留矿堆目标折价(远目标多回合行程, NPC 先吃仍成立)
// ns323 = ns322 + NPC 竞速折价(v1 移植: 目标格 nd*13<d*10 时降权 /4;
//         v1 消融依据: 放宽竞速拾取 567->479。两打分环稀疏内环+nn==0 门控)
//
// ============ 设计契约(用户 2026-08-07 定调) ============
// 落点目标: 平台 P50 <= 300ns。出现策略问题时不加时间, 而是重新审视
// 这 300ns 内哪些思考多余、哪些不可少——预算只能重新分配, 不能扩容。
//
// ============ 预算账本(平台冷态估算) ============
//   调用+边界层(try/catch+sanitize)   ~30ns
//   输入窗口扫描(2 单位 x 25 格)       ~120ns   ← 最大固定成本, 优化方向: 3x3 常扫+外环隔轮
//   决策核(闭式采集 / 持久目标导向)     ~100ns
//   防漂移护栏+装配                    ~30ns
//   ---- 预算合计                     ~280ns
// 重回合(卡死解困等)允许尖刺, 只伤 P99(榜首 P99 也有 1.5μs)。
//
// ============ 常驻状态账本(目标 <= ~256B, 3-4 条缓存线) ============
//   wall[17]  uint32 位图   68B   障碍(永久)
//   piles[16] 4B/条         64B   矿堆缓存(v>=6 才值得记; 全图数组的替代物)
//   bombs[8]  3B/条         24B   炸弹记忆(波后 ~40 轮过期)
//   goals/patrol/stuck/敌情          ~24B
// v1 的 289 格争抢图/热度图/金币列表/BFS 数组全部不复存在——它们的价值
// 用增量结构近似, 近似损失从实战分数里读, 再决定砍谁保谁。
//
// ============ 从 v1 继承的实证结论 ============
//  - 炸弹波 round%20==0 刷新, 记忆 ~40 轮内有效(v1: 20 轮 + 消失观测)
//  - 防漂移截断(FAQ294 被挡步漂移)值 +80~110/局, 且几乎免费 → 保留
//  - 1-2 金闭式采集(singleGold 语义)覆盖大部分采集轮, 84 路 DFS 是多余思考
//  - 目标层每轮重建是 v1 的 2.2μs 大头 → 这里以持久目标+矿堆缓存替代
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
constexpr int BOMB = -3, OBSTACLE = -1;   // (FOG=-5 窗外, v2 不读)
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

struct alignas(64) State {   // 热字段排前 2 条缓存线
    uint32_t bombbit[N];     // 炸弹位图(与 bombs 列表同步)
    // (ns329: wall[] 已砍, 墙位 = bp&~bombbit 推导; bp 即唯一地形真值)
    // 哨兵阻挡位图: 行 0/18 全阻, 位(c+1)=列 c, 位0 与位18+ 恒1(出界)
    // -> passable 纯两次位测试, 零边界分支
    uint32_t bp[N + 2];
    // 矿堆缓存 SoA(每字段 16B = 1 条 XMM): SIMD 对账/打分
    // 32 槽 x 4 字段 = 每字段 32B = 1 条 YMM(对账 AVX2 一次比完)
    int8_t  pr_[32], pc_[32];
    uint8_t pv_[32], ps_[32];
    // 炸弹表 SoA(定长 8): 对账/近弹检测固定行程无分支
    int8_t br_[8], bc_[8];
    uint8_t bseen_[8];
    uint8_t nbombs;          // 已知炸弹数(护栏门控: 无弹免模拟)
    int8_t goal_r[2], goal_c[2];
    uint8_t goal_kind[2];    // 0=无 1=矿堆 2=巡逻
    uint8_t patrol[2];       // 巡逻路点下标
    int8_t last_r[2], last_c[2];
    uint8_t stuck[2];
    int16_t last_round;
};
State g_s;

// 倒数表 REC[d] ~= 4096/(d+1): 打分乘法化(平台老 CPU 的 64 位 idiv ~100 周期,
// 决策核 1μs 之谜的主犯; 排序语义不变)
constexpr uint16_t REC[33] = {4096, 2048, 1365, 1024, 819, 683, 585, 512, 455,
    410, 372, 341, 315, 293, 273, 256, 241, 228, 216, 205, 195, 186, 178, 171,
    164, 158, 152, 146, 141, 137, 132, 128, 124};

// 巡逻路点: 中心十字 + 四象限(金币主产区为中心 9x9, 外圈大堆由矿堆缓存牵引)
constexpr int8_t PATROL_R[8] = {8, 5, 8, 11, 8, 3, 13, 8};
constexpr int8_t PATROL_C[8] = {5, 8, 11, 8, 8, 3, 13, 8};

inline uint8_t now2() { return (uint8_t)(g_s.last_round >> 1); }


inline void bombNote(int r, int c) {   // 稀疏调用(新弹目击), 保持简单
    int free_ = -1;
    for (int i = 0; i < 8; ++i) {
        if (g_s.br_[i] == r && g_s.bc_[i] == c && g_s.bseen_[i]) {
            g_s.bseen_[i] = now2() ? now2() : 1;
            return;
        }
        if (!g_s.bseen_[i]) free_ = i;
    }
    if (free_ >= 0) {
        g_s.br_[free_] = (int8_t)r; g_s.bc_[free_] = (int8_t)c;
        g_s.bseen_[free_] = now2() ? now2() : 1;
        g_s.bombbit[r] |= 1u << c;
        g_s.bp[r + 1] |= 1u << (c + 1);
        ++g_s.nbombs;
    }
}

// 矿堆缓存: 直接映射哈希(槽=(r*31+c)&31), 记/摘 O(1) 零扫描。
// 冲突=覆盖(启发式缓存, 丢一条可接受; v>=5 门槛已滤噪)
inline int pileSlot(int r, int c) { return (r * 31 + c) & 31; }

inline void pileNote(int r, int c, int v) {
    int i = pileSlot(r, c);
    g_s.pr_[i] = (int8_t)r; g_s.pc_[i] = (int8_t)c;
    g_s.pv_[i] = (uint8_t)v; g_s.ps_[i] = now2();
}

inline void pileDrop(int r, int c) {
    int i = pileSlot(r, c);
    if (g_s.pr_[i] == r && g_s.pc_[i] == c) g_s.pv_[i] = 0;
}

inline bool passable(int r, int c, int tr, int tc) {   // tr/tc = 队友占位
    return !((g_s.bp[r + 1] >> (c + 1)) & 1u) && !(r == tr && c == tc);
}
// 0/1 版(无短路分支)
inline unsigned pass01(int r, int c, int tr, int tc) {
    return (~(g_s.bp[r + 1] >> (c + 1)) & 1u) &
           (unsigned)((r != tr) | (c != tc));
}

// 逃逸(罕见): 冷函数, 不占热路径分支位点
__attribute__((noinline, cold))
int escapeStep(int r, int c, int tr, int tc, int pr, int pc) {
    for (int a = 0; a < 4; ++a) {                  // 可走且不回头
        int nr = r + DR[a], nc = c + DC[a];
        if (nr == pr && nc == pc) continue;
        if (passable(nr, nc, tr, tc)) return a;
    }
    return -1;
}

__attribute__((noinline, cold))
void stuckEscape(int u, int sr, int sc, int tr, int tc, int* acts) {
    g_s.goal_kind[u] = 0;
    g_s.patrol[u] = (uint8_t)((g_s.patrol[u] + 1) & 7);
    for (int a = 0; a < 4; ++a)
        if (passable(sr + DR[a], sc + DC[a], tr, tc)) { acts[0] = a; break; }
    g_s.stuck[u] = 0;
}

// 曼哈顿导向一步: 主/副方向算术选择, 仅剩 1 个罕见分支(逃逸门)
int steerStep(int r, int c, int gr, int gc, int tr, int tc, int pr, int pc) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;                              // 下=1 上=0
    int ac = 2 + (dcc > 0);                        // 右=3 左=2
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = pass01(r + DR[p0], c + DC[p0], tr, tc);
    unsigned ok1 = pass01(r + DR[p1], c + DC[p1], tr, tc) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)                                 // 常真(可预测)
        return ok0 ? p0 : p1;                      // cmov 化
    return (adr | adc) ? escapeStep(r, c, tr, tc, pr, pc) : -1;
}

#if defined(NSPROBE) && (NSPROBE == 9 || NSPROBE == 12 || NSPROBE == 13)
unsigned long long g_t[4]; long g_tn;
unsigned long long td9_last;
inline unsigned long long tsc() {
#if defined(__x86_64__)
    unsigned lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
#else
    return 0;
#endif
}
#define TM(k, expr) do { unsigned long long t_ = tsc(); expr; g_t[k] += tsc() - t_; } while (0)
#else
#define TM(k, expr) expr
#endif

GameOutput decide(const GameInput* in) {
#if defined(__AVX2__)
    // 输入行前置装载: 主动单位 5 行 miss 最早发射, 与下方账务并行(软件流水)
    __m256i rowbuf[5];
    int rb_sr = -1, rb_cb = 0;
    {
#ifdef NS_ALT
        const int a0 = in->round & 1;
#else
        const int a0 = 0;
#endif
        int sr0 = in->my_units[a0].row, sc0 = in->my_units[a0].col;
        if (sr0 >= 0 && sr0 < N && sc0 >= 0 && sc0 < N) {
            int cb = sc0 - 2 < 0 ? 0 : (sc0 - 2 > N - 5 ? N - 5 : sc0 - 2);
            rb_sr = sr0; rb_cb = cb;
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr0 - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                rowbuf[i] = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
            }
        }
    }
#endif
    if (in->round <= g_s.last_round) {
        memset(&g_s, 0, sizeof(g_s));
        g_s.patrol[1] = 3;
        g_s.bp[0] = g_s.bp[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bp[r + 1] = 0xFFFC0001u;
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0) {                    // 炸弹波: 旧记忆全部过期
        memset(g_s.bseen_, 0, 8);
        for (int r = 0; r < N; ++r) g_s.bp[r + 1] &= ~(g_s.bombbit[r] << 1);
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
        g_s.nbombs = 0;
    }
#if defined(NSPROBE) && (NSPROBE == 9 || NSPROBE == 13)
    unsigned long long t9 = tsc();
#endif
    __builtin_prefetch(&g_s.bp[4]);            // 自身状态热线并行预热
    __builtin_prefetch(g_s.pr_);
    // 窗口行预取(与扫描行程一致: 3 行常扫+隔轮扩展)
    for (int u2 = 0; u2 < 2; ++u2) {
#ifdef NS_ALT
        if (u2 != (in->round & 1)) continue;   // 轮换: 被动单位输入行不碰
#endif
        int ur = in->my_units[u2].row, uc = in->my_units[u2].col;
        if (ur < 0) continue;
        int pc0 = uc - 2 < 0 ? 0 : uc - 2;
        int pr0 = ur - 2 < 0 ? 0 : ur - 2;
        int pr1 = ur + 2 >= N ? N - 1 : ur + 2;
        for (int r2 = pr0; r2 <= pr1; ++r2)
            __builtin_prefetch(&in->grid[r2][pc0]);
    }

    GameOutput out = SAFE_OUT;

    // (缓存对账已迁入单位循环: 用 wv7 校正, 零读格 —— A 段 542 周期的主治)
    // (ns329: 敌情块已砍 —— 只写不读, 每轮白读 visible_enemies 输入线)
#if defined(NSPROBE) && (NSPROBE == 9 || NSPROBE == 13)
    if (in->round < 250) g_t[0] += tsc() - t9;
#endif

    // ===== 阶段1: 双单位背靠背扫描 =====
    // 两组 ~10 条输入线的 miss 在无分支代码下由 OoO 并行发射(MLP),
    // 总等待 ~= 最大值而非串行和(此前 u0扫->u0决->u1扫 把 miss 串行化了)
    int8_t wv7s[2][49];
    uint32_t goldms[2] = {0, 0}, bombms[2] = {0, 0};
#if defined(NSPROBE) && (NSPROBE == 9 || NSPROBE == 13)
    unsigned long long tb9 = tsc();
#endif
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int8_t* wv7 = wv7s[u];
        memset(wv7, -1, 49);
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
#ifdef NS_ALT
        if (u != (in->round & 1)) continue;    // 轮换: 被动单位本轮不扫描
#endif
        int cb = sc - 2 < 0 ? 0 : (sc - 2 > N - 5 ? N - 5 : sc - 2);
        int cshift = sc - 2 - cb + 3;          // blk 中心偏移(标量路径用)
        (void)cshift;
        uint32_t goldm = 0, wallm = 0, bombm = 0, validm = 0;
#if defined(__AVX2__)
        {   // SIMD 行扫描: 每行 1 次载入 + 3 次比较 + movemask(热功 ~-200 周期)
            const __m256i vz = _mm256_setzero_si256();
            const __m256i vm1 = _mm256_set1_epi32(-1);
            const __m256i vm3 = _mm256_set1_epi32(-3);
            int lsh = 2 + (sc - 2 - cb);           // 列对齐移位 ∈[0,4]
            int lo = sc - 2 < 0 ? -(sc - 2) : 0;
            int hix = sc + 2 > N - 1 ? sc + 2 - (N - 1) : 0;
            uint32_t colv = ((31u >> hix) & (31u << lo)) & 31u;
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
#ifdef NS_ALT
                // 轮换下扫描单位==装载单位(公式同源), 无条件用寄存器行:
                // 消掉展开环里的 5 个比较分支位点(ns329 首测 +50ns 的疑犯)
                __m256i vrow = rowbuf[i];
                (void)cr;
#else
                __m256i vrow = (sr == rb_sr && cb == rb_cb)
                    ? rowbuf[i]
                    : _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
#endif
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
                    li &= ~(li >> 31);             // max(0, li)
                    int v = tmp[li];
                    int m = -(int)((rv >> j) & 1u);
                    wv7[(i + 1) * 7 + (j + 1)] = (int8_t)((v & m) | ~m);
                }
            }
        }
#else
        {
            int32_t blk[12];
            blk[0] = blk[1] = blk[2] = -1;
            blk[8] = blk[9] = blk[10] = blk[11] = -1;
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                int cr = rr < 0 ? 0 : (rr > N - 1 ? N - 1 : rr);
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                memcpy(blk + 3, &in->grid[cr][cb], 5 * sizeof(int32_t));
#pragma GCC unroll 5
                for (int j = 0; j < 5; ++j) {
                    int b = i * 5 + j;
                    uint32_t colok =
                        (uint32_t)0 - ((unsigned)(sc - 2 + j) < (unsigned)N);
                    uint32_t m = rowok & colok;
                    int v = (blk[cshift + j] & (int)m) | (int)~m;   // 无效=-1
                    wv7[(i + 1) * 7 + (j + 1)] = (int8_t)v;
                    goldm  |= (uint32_t)(v > 0) << b;
                    wallm  |= (uint32_t)(v == OBSTACLE) << b;
                    bombm  |= (uint32_t)(v == BOMB) << b;
                    validm |= (m & 1u) << b;
                }
            }
        }
#endif
        wallm &= validm;                       // 出界哨兵不是真墙
        if (wallm) {                           // 墙并入 bp(唯一地形真值)
            int r0 = sr - 2 < 0 ? 0 : sr - 2, r1 = sr + 2 >= N ? N - 1 : sr + 2;
            int c0 = sc - 2 < 0 ? 0 : sc - 2;
            for (int r = r0; r <= r1; ++r) {
                int b5 = (r - sr + 2) * 5 + 2 - sc;
                uint32_t slice = ((wallm >> (b5 + c0)) & 31u) << c0;
                g_s.bp[r + 1] |= slice << 1;
            }
        }
        {                                      // 炸弹: 稀疏, 逐位登记
            uint32_t bm = bombm;
            while (bm) {
                int i = __builtin_ctz(bm); bm &= bm - 1;
                bombNote(sr - 2 + i / 5, sc - 2 + i % 5);
            }
        }
        goldms[u] = goldm; bombms[u] = bombm;
    }
#if defined(NSPROBE) && NSPROBE == 9
    (void)tb9;                                   // B1 已测(421), 槽位让给拆分
#endif
#if defined(NSPROBE) && NSPROBE == 13
    if (in->round < 250) g_t[1] += tsc() - tb9;  // B1 主动扫描段
#endif

    // (ns329: NPC 折价整段砍除 —— ns325 批量证收入中性, 却每轮读 2 条输入线)

    // ===== 阶段2: 决策 =====
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        const int8_t* wv7 = wv7s[u];
        uint32_t goldm = goldms[u], bombm = bombms[u];
        (void)bombm;
#if defined(NSPROBE) && NSPROBE == 9
        unsigned long long tb2 = tsc();
#endif
#if defined(NSPROBE) && NSPROBE == 13
        unsigned long long t13 = tsc();
        int slot13 = (u == (in->round & 1)) ? 2 : 3;
#endif
#ifdef NS_ALT
        if (u != (in->round & 1)) {            // 被动路径: 缓存导向, 零输入读
            unsigned nearbomb = 0;
#pragma GCC unroll 8
            for (int i = 0; i < 8; ++i) {
                int adr_ = g_s.br_[i] - sr, adc_ = g_s.bc_[i] - sc;
                adr_ = adr_ < 0 ? -adr_ : adr_; adc_ = adc_ < 0 ? -adc_ : adc_;
                nearbomb |= (unsigned)(g_s.bseen_[i] != 0) &
                            (unsigned)(adr_ + adc_ <= 3);
            }
            if (g_s.goal_kind[u] == 0) {       // 无目标: 就地折返(ctz 化)
                unsigned pm = pass01(sr - 1, sc, tr, tc) |
                              (pass01(sr + 1, sc, tr, tc) << 1) |
                              (pass01(sr, sc - 1, tr, tc) << 2) |
                              (pass01(sr, sc + 1, tr, tc) << 3);
                if (pm) {
                    int a = __builtin_ctz(pm);
                    acts[0] = a; acts[1] = a ^ 1;
                }
            } else {
                int tgr = g_s.goal_r[u], tgc = g_s.goal_c[u];
                int d = (tgr > sr ? tgr - sr : sr - tgr) +
                        (tgc > sc ? tgc - sc : sc - tgc);
                if (d == 0) {                  // 站上目标: 摘除, 交回主动轮
                    if (g_s.goal_kind[u] == 1) pileDrop(tgr, tgc);
                    g_s.goal_kind[u] = 0;
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
                    (void)n;
                    if ((r == tgr) & (c == tgc)) {
                        if (g_s.goal_kind[u] == 1) pileDrop(tgr, tgc);
                        g_s.goal_kind[u] = 0;
                    }
                }
            }
            // 卡死计数与主动轮共用
            unsigned same = (unsigned)((sr == g_s.last_r[u]) &
                                       (sc == g_s.last_c[u]) & (acts[0] == STAY));
            g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
            if (g_s.stuck[u] >= 2) stuckEscape(u, sr, sc, tr, tc, acts);
            g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
            // 防漂移护栏(近弹才模拟)
            if (in->my_units_gold[u] >= 300 && nearbomb) {
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
#if defined(NSPROBE) && NSPROBE == 13
            if (in->round < 250) g_t[slot13] += tsc() - t13;
#endif
            continue;                          // 被动单位到此为止
        }
#endif
        // 对账(零读格): 本单位窗口内的堆用 wv7 校正
#if defined(__AVX2__)
        {   // SIMD: 一次比较得到"窗口内且有货"的堆位图, 逐位校正(稀疏)
            __m256i vr_ = _mm256_loadu_si256((const __m256i*)g_s.pr_);
            __m256i vc_ = _mm256_loadu_si256((const __m256i*)g_s.pc_);
            __m256i vv_ = _mm256_loadu_si256((const __m256i*)g_s.pv_);
            __m256i wr_ = _mm256_sub_epi8(vr_, _mm256_set1_epi8((char)(sr - 2)));
            __m256i wc_ = _mm256_sub_epi8(vc_, _mm256_set1_epi8((char)(sc - 2)));
            __m256i z8 = _mm256_setzero_si256();
            __m256i in5r = _mm256_cmpgt_epi8(_mm256_set1_epi8(5),
                                             _mm256_max_epu8(wr_, z8));
            in5r = _mm256_and_si256(in5r,
                                    _mm256_cmpgt_epi8(wr_, _mm256_set1_epi8(-1)));
            __m256i in5c = _mm256_cmpgt_epi8(_mm256_set1_epi8(5),
                                             _mm256_max_epu8(wc_, z8));
            in5c = _mm256_and_si256(in5c,
                                    _mm256_cmpgt_epi8(wc_, _mm256_set1_epi8(-1)));
            __m256i has = _mm256_cmpgt_epi8(vv_, z8);
            uint32_t hits = (uint32_t)_mm256_movemask_epi8(
                _mm256_and_si256(_mm256_and_si256(in5r, in5c), has));
            while (hits) {
                int i = __builtin_ctz(hits); hits &= hits - 1;
                int wr = g_s.pr_[i] - sr + 2, wc = g_s.pc_[i] - sc + 2;
                int v = wv7[(wr + 1) * 7 + (wc + 1)];
                if (v >= 5) { g_s.pv_[i] = (uint8_t)v; g_s.ps_[i] = now2(); }
                else g_s.pv_[i] = 0;
            }
        }
#else
        for (int i = 0; i < 32; ++i) {
            if (!g_s.pv_[i]) continue;
            int wr = g_s.pr_[i] - sr + 2, wc = g_s.pc_[i] - sc + 2;
            if ((unsigned)wr > 4u || (unsigned)wc > 4u) continue;
            int v = wv7[(wr + 1) * 7 + (wc + 1)];
            if (v >= 5) { g_s.pv_[i] = (uint8_t)v; g_s.ps_[i] = now2(); }
            else g_s.pv_[i] = 0;                   // 吃小/吃空/变弹: 摘除
        }
#endif
        // 炸弹对账: 固定 8 行程无分支(原 ~16 分支位点), 摘除位稀疏后处理
        unsigned nearbomb = 0;
        {
            uint32_t badm = 0;
#pragma GCC unroll 8
            for (int i = 0; i < 8; ++i) {
                int wr = g_s.br_[i] - sr + 2, wc = g_s.bc_[i] - sc + 2;
                unsigned seen_ = (unsigned)(g_s.bseen_[i] != 0);
                unsigned inw = ((unsigned)wr <= 4u) & ((unsigned)wc <= 4u) & seen_;
                unsigned idx = (unsigned)(wr * 5 + wc) & (0u - inw) & 31u;
                unsigned present = (bombm >> idx) & inw;
                badm |= (inw & ~present & 1u) << i;
                int adr_ = wr < 2 ? 2 - wr : wr - 2;
                int adc_ = wc < 2 ? 2 - wc : wc - 2;
                nearbomb |= seen_ & (unsigned)(adr_ + adc_ <= 3);
            }
            while (badm) {                     // 罕见: 吃空/幽灵弹摘除
                int i = __builtin_ctz(badm); badm &= badm - 1;
                g_s.bseen_[i] = 0;
                g_s.bombbit[g_s.br_[i]] &= ~(1u << g_s.bc_[i]);
                g_s.bp[g_s.br_[i] + 1] &= ~(1u << (g_s.bc_[i] + 1));
                --g_s.nbombs;
            }
        }

        // 采集打分(簇加成): 只走置位金格
        constexpr int8_t MD[25] = {4,3,2,3,4, 3,2,1,2,3, 2,1,0,1,2,
                                   3,2,1,2,3, 4,3,2,3,4};
        int bestr = -1, bestc = -1, bests = 0;
        int gn_ = 0;
        {
            uint32_t gm = goldm;
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
                if (v >= 5) pileNote(sr - 2 + i / 5, sc - 2 + i % 5, v);
            }
        }

#if defined(NSPROBE) && NSPROBE == 9
        if (in->round < 250) g_t[0 + u] += tsc() - tb2;   // B2 按单位拆分
        unsigned long long tc9 = tsc();
#endif
#if defined(NSPROBE) && NSPROBE == 1
        (void)bests; continue;               // 探针1: 只测 扫描+对账(站桩)
#endif
#if defined(NSPROBE) && NSPROBE == 3
        {   // 探针3: +轮转走位(窗口移动)
            int a = (in->round / 4 + u * 2) & 3;
            acts[0] = acts[1] = acts[2] = a;
            continue;
        }
#endif
        // ---- 统一决策核: 目标格 = 窗口最优金格 或 持久目标; 导向共用 ----
#if defined(NSPROBE) && NSPROBE == 6
        bestr = -1;                          // 探针6: 采集支砍除
#endif
        int tgr, tgc, mode;                  // mode: 0=采集 1=矿堆 2=巡逻
        if (bestr >= 0) {
            tgr = bestr; tgc = bestc; mode = 0;
            g_s.goal_kind[u] = 0;            // 就地有活干, 目标层休眠
        } else {
#if defined(NSPROBE) && NSPROBE == 5
            {   // 探针5: 目标支砍除, 轮转走位
                int a = (in->round / 4 + u * 2) & 3;
                acts[0] = acts[1] = acts[2] = a;
                goto probe5_done;
            }
#endif
            // goal 校验 cmov 链(消 2 分支位点): 哈希/巡逻两式并算, 按 kind 选
            int gk_ = g_s.goal_kind[u];
            int pi_ = pileSlot(g_s.goal_r[u], g_s.goal_c[u]);
            unsigned vh_ = (unsigned)(g_s.pv_[pi_] != 0) &
                           (unsigned)(g_s.pr_[pi_] == g_s.goal_r[u]) &
                           (unsigned)(g_s.pc_[pi_] == g_s.goal_c[u]);
            unsigned vp_ = (unsigned)!((sr == g_s.goal_r[u]) &
                                       (sc == g_s.goal_c[u]));
            bool valid = gk_ == 1 ? (bool)vh_ : (gk_ == 2 ? (bool)vp_ : false);
            if (!valid) {
                int best = 0, bi = -1;
                uint8_t t2 = now2();
                // 去重目标(对方单位的矿堆目标)编码成位置码, 无效时取 -1
                int dedup = (u == 1 && g_s.goal_kind[0] == 1)
                                ? (g_s.goal_r[0] * 32 + g_s.goal_c[0]) : -1;
#if defined(__AVX2__)
                {   // SIMD 预计算 live/d/age, 只对活槽做标量打分(通常 <=8 个)
                    __m256i pr8 = _mm256_loadu_si256((const __m256i*)g_s.pr_);
                    __m256i pc8 = _mm256_loadu_si256((const __m256i*)g_s.pc_);
                    __m256i pv8 = _mm256_loadu_si256((const __m256i*)g_s.pv_);
                    __m256i ps8 = _mm256_loadu_si256((const __m256i*)g_s.ps_);
                    __m256i z = _mm256_setzero_si256();
                    __m256i age8 = _mm256_sub_epi8(_mm256_set1_epi8((char)t2), ps8);
                    __m256i dr8 = _mm256_abs_epi8(
                        _mm256_sub_epi8(pr8, _mm256_set1_epi8((char)sr)));
                    __m256i dc8 = _mm256_abs_epi8(
                        _mm256_sub_epi8(pc8, _mm256_set1_epi8((char)sc)));
                    __m256i d8v = _mm256_add_epi8(dr8, dc8);
                    __m256i live8 = _mm256_and_si256(
                        _mm256_cmpgt_epi8(pv8, z),
                        _mm256_cmpgt_epi8(_mm256_set1_epi8(31), age8));
                    int8_t d8a[32], age8a[32];
                    _mm256_storeu_si256((__m256i*)d8a, d8v);
                    _mm256_storeu_si256((__m256i*)age8a, age8);
                    uint32_t lm = (uint32_t)_mm256_movemask_epi8(live8);
                    while (lm) {
                        int i = __builtin_ctz(lm); lm &= lm - 1;
                        int pos = g_s.pr_[i] * 32 + g_s.pc_[i];
                        int s = (pos != dedup)
                                    ? g_s.pv_[i] * (30 - age8a[i]) * REC[d8a[i]] : 0;
                        int gt = -(int)(s > best);
                        best = (s & gt) | (best & ~gt);
                        bi = (i & gt) | (bi & ~gt);
                    }
                }
#else
                for (int i = 0; i < 32; ++i) {         // 固定 32 次, 体内无分支
                    int age2 = (int)t2 - g_s.ps_[i];
                    int dr_ = g_s.pr_[i] - sr, dc_ = g_s.pc_[i] - sc;
                    int d = (dr_ < 0 ? -dr_ : dr_) + (dc_ < 0 ? -dc_ : dc_);
                    int pos = g_s.pr_[i] * 32 + g_s.pc_[i];
                    int live = -(int)((unsigned)(g_s.pv_[i] != 0) &
                                      (unsigned)(age2 <= 30) &
                                      (unsigned)(pos != dedup));
                    int s = (g_s.pv_[i] * (30 - age2) * REC[d]) & live;
                    int gt = -(int)(s > best);
                    best = (s & gt) | (best & ~gt);
                    bi = (i & gt) | (bi & ~gt);
                }
#endif
                if (bi >= 0) {
                    g_s.goal_r[u] = g_s.pr_[bi]; g_s.goal_c[u] = g_s.pc_[bi];
                    g_s.goal_kind[u] = 1;
                } else {                            // 无堆可去: 巡逻
                    uint8_t& pi = g_s.patrol[u];
                    if (sr == PATROL_R[pi] && sc == PATROL_C[pi]) pi = (uint8_t)((pi + 1) & 7);
                    g_s.goal_r[u] = PATROL_R[pi]; g_s.goal_c[u] = PATROL_C[pi];
                    g_s.goal_kind[u] = 2;
                }
            }
            tgr = g_s.goal_r[u]; tgc = g_s.goal_c[u]; mode = g_s.goal_kind[u];
        }
        {
            int d = (tgr > sr ? tgr - sr : sr - tgr) +
                    (tgc > sc ? tgc - sc : sc - tgc);
            if (d == 0) {
                if (mode == 0) {               // 站在金上: 出去再回来吃(ctz 化)
                    unsigned pm = pass01(sr - 1, sc, tr, tc) |
                                  (pass01(sr + 1, sc, tr, tc) << 1) |
                                  (pass01(sr, sc - 1, tr, tc) << 2) |
                                  (pass01(sr, sc + 1, tr, tc) << 3);
                    if (pm) {
                        int a = __builtin_ctz(pm);
                        acts[0] = a; acts[1] = a ^ 1;
                    }
                } else {                        // 站在目标上(幽灵堆): 摘除
                    if (mode == 1) pileDrop(tgr, tgc);
                    g_s.goal_kind[u] = 0;
                }
            } else {
                // 定形 3 步掩码导向(消 while/break 分支位点; 语义与循环版等价:
                // a<0 或到达后 steerStep 输入不变 -> 输出不变 -> 恒 STAY)
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
                    n -= m;                          // m=-1 时 +1
                }
                int arrived = (int)((r == tgr) & (c == tgc));
                if (arrived & (int)(mode == 0)) {
                    if (n > 0 && n < 3) {
                        acts[n] = acts[n - 1] ^ 1;     // 早到: 退一步
                        if (n + 1 < 3) acts[n + 1] = acts[n] ^ 1;   // d1 双吃回进
                    }
                } else if (arrived) {
                    if (mode == 1) pileDrop(tgr, tgc);
                    g_s.goal_kind[u] = 0;              // 到达: 下轮窗口扫描接管
                }
            }
        }

#if defined(NSPROBE) && NSPROBE == 5
        probe5_done:;
#endif
#if defined(NSPROBE) && NSPROBE == 9
        if (in->round < 250) g_t[2 + u] += tsc() - tc9;   // C 按单位拆分
        (void)td9_last;
#endif
#if defined(NSPROBE) && NSPROBE == 12
        unsigned long long t12a = tsc();
#endif
        // ---- 尾步填充(v1 实证: 16% 的步在罚站; 复用 wv, 零额外读格) ----
        if (gn_ > 0) {
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                if (acts[i] == STAY) {                 // 可预测门(多数轮非 STAY)
                    int besta = -1, bv = 0;
#pragma GCC unroll 4
                    for (int a = 0; a < 4; ++a) {      // 无分支 max 选择
                        int nr = r + DR[a], nc = c + DC[a];
                        int ur = nr - sr + 3, uc = nc - sc + 3;
                        unsigned inw = ((unsigned)ur <= 6u) & ((unsigned)uc <= 6u);
                        int idx = (ur * 7 + uc) & -(int)inw;   // 出窗读 wv7[0](被掩码)
                        int v = wv7[idx];
                        int okm = -(int)(inw & (unsigned)(v > bv) &
                                         (unsigned)!(nr == tr && nc == tc));
                        bv = (v & okm) | (bv & ~okm);
                        besta = (a & okm) | (besta & ~okm);
                    }
                    acts[i] = besta >= 0 ? besta : acts[i];   // cmov
                }
                // 位置推进无分支化(墙表掩码索引; 出界读 wall[0] 被 inb 掩掉)
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
#if defined(NSPROBE) && NSPROBE == 12
        unsigned long long t12b = tsc();
        if (in->round < 250) g_t[0 + u] += t12b - t12a;   // fill 按单位
#endif

        // ---- 卡死解困(挡两轮 = 换个活法; 主体冷函数) ----
        unsigned same = (unsigned)((sr == g_s.last_r[u]) &
                                   (sc == g_s.last_c[u]) & (acts[0] == STAY));
        g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
        if (g_s.stuck[u] >= 2) stuckEscape(u, sr, sc, tr, tc, acts);   // 罕见
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;

        // ---- 防漂移护栏(v1 实证 +80~110/局, 几乎免费) ----
        // 近弹预筛: 3 步路径只可达曼哈顿<=3, 近域无弹 -> 整段免模拟(语义等价)
        if (in->my_units_gold[u] >= 300 && nearbomb) {
            for (int blk = 0; blk < 3; ++blk) {
                if (acts[blk] == STAY) continue;
                int r = sr, c = sc;
                for (int i = 0; i < 3; ++i) {
                    // 步推进无分支化: 唯一保留分支 = 撞弹截断(罕见)
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
#if defined(NSPROBE) && NSPROBE == 12
        if (in->round < 250) g_t[2 + u] += tsc() - t12b;  // stuck+护栏 按单位
#endif
#if defined(NSPROBE) && NSPROBE == 13
        if (in->round < 250) g_t[slot13] += tsc() - t13;  // 阶段2 主动路径
#endif
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = 0;
#if defined(NSPROBE) && NSPROBE == 11
    // 判决: 决策全算, 行为规律化(匀速走位) —— 分离"混沌访问模式"的冷成本
    for (int u2 = 0; u2 < 2; ++u2) {
        int a = (in->round / 4 + u2 * 2) & 3;
        out.actions[u2 * 3] = out.actions[u2 * 3 + 1] = out.actions[u2 * 3 + 2] = a;
    }
#endif
#if defined(NSPROBE) && (NSPROBE == 9 || NSPROBE == 12 || NSPROBE == 13)
    if (in->round < 250) ++g_tn;
    // vp 信道发射: round 250 起, 4 个 16 位均值(周期), MSB 在前
    if (in->round >= 250 && in->round < 250 + 64 && g_tn > 0) {
        int bi = in->round - 250;
        unsigned long long avg = g_t[bi / 16] / (unsigned long long)g_tn;
        if (avg > 65535) avg = 65535;
        out.vp = (int)((avg >> (15 - bi % 16)) & 1);
    }
#endif
    return out;
}

GameOutput sanitize(GameOutput o) {
    for (int i = 0; i < S; ++i)
        if (o.actions[i] < 0 || o.actions[i] > 4) o.actions[i] = STAY;
    if (o.k < 0 || o.k > 6) o.k = 3;
    if (o.order != 0 && o.order != 1) o.order = 0;
    if (o.vp < 0 || o.vp > 2) o.vp = 0;
    return o;
}

}  // namespace

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
#if defined(NSPROBE) && NSPROBE == 10
        // 判决实验: 流水线跑两遍。P50 只微涨=固定冷启动主导; 翻倍=线性工作量
        volatile GameOutput sink = decide(input);
        (void)sink;
#endif
        return sanitize(decide(input));
    } catch (...) {
        return SAFE_OUT;
    }
}
