// ns300/player.cpp — v2 主线: 300ns 预算的延迟优先重写
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
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int BOMB = -3, OBSTACLE = -1;   // (FOG=-5 窗外, v2 不读)
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

struct Pile { int8_t r, c; uint8_t v; uint8_t seen; };   // seen = round>>1
struct BombM { int8_t r, c; uint8_t seen; };

struct State {
    uint32_t wall[N];        // 障碍位图(观测到即永久)
    uint32_t bombbit[N];     // 炸弹位图(与 bombs 列表同步)
    // 哨兵阻挡位图: 行 0/18 全阻, 位(c+1)=列 c, 位0 与位18+ 恒1(出界)
    // -> passable 纯两次位测试, 零边界分支
    uint32_t bp[N + 2];
    Pile  piles[16];         // v==0 表示空槽
    BombM bombs[8];
    int8_t goal_r[2], goal_c[2];
    uint8_t goal_kind[2];    // 0=无 1=矿堆 2=巡逻
    uint8_t patrol[2];       // 巡逻路点下标
    int8_t last_r[2], last_c[2];
    uint8_t stuck[2];
    int8_t esight_r, esight_c;  // 最近敌人目击
    uint8_t esight_seen;
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

inline bool wallAt(int r, int c) { return (g_s.wall[r] >> c) & 1u; }

inline bool bombAt(int r, int c) { return (g_s.bombbit[r] >> c) & 1u; }
inline void bpRebuildRow(int r) {
    g_s.bp[r + 1] = 0xFFFC0001u | (g_s.wall[r] << 1) | (g_s.bombbit[r] << 1);
}

inline void bombNote(int r, int c) {
    int free_ = -1;
    for (int i = 0; i < 8; ++i) {
        if (g_s.bombs[i].r == r && g_s.bombs[i].c == c && g_s.bombs[i].seen) {
            g_s.bombs[i].seen = now2() ? now2() : 1;
            return;
        }
        if (!g_s.bombs[i].seen) free_ = i;
    }
    if (free_ >= 0) {
        g_s.bombs[free_] = {(int8_t)r, (int8_t)c, now2() ? now2() : (uint8_t)1};
        g_s.bombbit[r] |= 1u << c;
        g_s.bp[r + 1] |= 1u << (c + 1);
    }
}

// 矿堆缓存: 直接映射哈希(槽=(r*31+c)&15), 记/摘 O(1) 零扫描。
// 冲突=覆盖(启发式缓存, 丢一条可接受; v>=5 门槛已滤噪)
inline int pileSlot(int r, int c) { return (r * 31 + c) & 15; }

inline void pileNote(int r, int c, int v) {
    g_s.piles[pileSlot(r, c)] = {(int8_t)r, (int8_t)c, (uint8_t)v, now2()};
}

inline void pileDrop(int r, int c) {
    Pile& p = g_s.piles[pileSlot(r, c)];
    if (p.r == r && p.c == c) p.v = 0;
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
    return escapeStep(r, c, tr, tc, pr, pc);
}

#if defined(NSPROBE) && NSPROBE == 9
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
    if (in->round <= g_s.last_round) {
        memset(&g_s, 0, sizeof(g_s));
        g_s.patrol[1] = 3;
        g_s.bp[0] = g_s.bp[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) bpRebuildRow(r);
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0) {                    // 炸弹波: 旧记忆全部过期
        for (int i = 0; i < 8; ++i) g_s.bombs[i].seen = 0;
        memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
        for (int r = 0; r < N; ++r) bpRebuildRow(r);
    }
#if defined(NSPROBE) && NSPROBE == 9
    unsigned long long t9 = tsc();
#endif
    __builtin_prefetch(&g_s.bp[4]);            // 自身状态热线并行预热
    __builtin_prefetch(&g_s.piles[0]);
    __builtin_prefetch(&g_s.piles[8]);
    // 窗口行预取(与扫描行程一致: 3 行常扫+隔轮扩展)
    for (int u2 = 0; u2 < 2; ++u2) {
        int ur = in->my_units[u2].row, uc = in->my_units[u2].col;
        if (ur < 0) continue;
        int e2 = (in->round ^ u2) & 1;
        int pc0 = uc - 2 < 0 ? 0 : uc - 2;
        int pr0 = ur - 1 - e2 < 0 ? 0 : ur - 1 - e2;
        int pr1 = ur + 1 + e2 >= N ? N - 1 : ur + 1 + e2;
        for (int r2 = pr0; r2 <= pr1; ++r2)
            __builtin_prefetch(&in->grid[r2][pc0]);
    }

    GameOutput out = SAFE_OUT;

    // (缓存对账已迁入单位循环: 用 wv7 校正, 零读格 —— A 段 542 周期的主治)
    // 敌情(便宜: 只记最近一次)
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N) {
            g_s.esight_r = (int8_t)r; g_s.esight_c = (int8_t)c;
            g_s.esight_seen = now2() ? now2() : 1;
        }
    }
#if defined(NSPROBE) && NSPROBE == 9
    if (in->round < 250) g_t[0] += tsc() - t9;
#endif

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;

#if defined(NSPROBE) && NSPROBE == 9
        unsigned long long tb9 = tsc();
#endif
        // ---- wv7 = 7x7 带哨兵边框的窗口值(邻居访问永远合法) ----
        int8_t wv7[49];
        memset(wv7, -1, 49);
        // 定形无分支扫描(遥测定案: 冷BTB与miss乘性耦合, 分支即延迟):
        // 固定 5x5 形状全展开, 边界=钳位读+算术有效掩码, 全程无条件分支 →
        // OoO 自由推测, 加载并行化。恢复全窗口(3行实验的行数论已被证伪)。
        int cb = sc - 2 < 0 ? 0 : (sc - 2 > N - 5 ? N - 5 : sc - 2);
        int cshift = sc - 2 - cb + 3;          // blk 中心偏移
        uint32_t goldm = 0, wallm = 0, bombm = 0, validm = 0;
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
        wallm &= validm;                       // 出界哨兵不是真墙
        if (wallm) {                           // 墙位图: 行片一次并入
            int r0 = sr - 2 < 0 ? 0 : sr - 2, r1 = sr + 2 >= N ? N - 1 : sr + 2;
            int c0 = sc - 2 < 0 ? 0 : sc - 2;
            for (int r = r0; r <= r1; ++r) {
                int b5 = (r - sr + 2) * 5 + 2 - sc;
                uint32_t slice = ((wallm >> (b5 + c0)) & 31u) << c0;
                g_s.wall[r] |= slice;
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
#if defined(NSPROBE) && NSPROBE == 9
        if (in->round < 250) g_t[1] += tsc() - tb9;
        unsigned long long tb2 = tsc();
#endif
        // 对账(零读格): 本单位窗口内的堆/弹用 wv7/bombm 校正
        for (int i = 0; i < 16; ++i) {
            Pile& p = g_s.piles[i];
            if (!p.v) continue;
            int wr = p.r - sr + 2, wc = p.c - sc + 2;
            if ((unsigned)wr > 4u || (unsigned)wc > 4u) continue;
            int v = wv7[(wr + 1) * 7 + (wc + 1)];
            if (v >= 5) { p.v = (uint8_t)v; p.seen = now2(); }
            else p.v = 0;                          // 吃小/吃空/变弹: 摘除
        }
        for (int i = 0; i < 8; ++i) {
            BombM& b = g_s.bombs[i];
            if (!b.seen) continue;
            int wr = b.r - sr + 2, wc = b.c - sc + 2;
            if ((unsigned)wr > 4u || (unsigned)wc > 4u) continue;
            if (!((bombm >> (wr * 5 + wc)) & 1u)) {
                b.seen = 0;
                g_s.bombbit[b.r] &= ~(1u << b.c);
                bpRebuildRow(b.r);
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
        if (in->round < 250) g_t[2] += tsc() - tb2;
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
            bool valid = false;
            if (g_s.goal_kind[u] == 1) {           // 哈希 O(1) 校验
                Pile& p = g_s.piles[pileSlot(g_s.goal_r[u], g_s.goal_c[u])];
                valid = p.v && p.r == g_s.goal_r[u] && p.c == g_s.goal_c[u];
            } else if (g_s.goal_kind[u] == 2) {
                valid = !(sr == g_s.goal_r[u] && sc == g_s.goal_c[u]);
            }
            if (!valid) {
                int best = 0, bi = -1;
                uint8_t t2 = now2();
                // 去重目标(对方单位的矿堆目标)编码成位置码, 无效时取 -1
                int dedup = (u == 1 && g_s.goal_kind[0] == 1)
                                ? (g_s.goal_r[0] * 32 + g_s.goal_c[0]) : -1;
                for (int i = 0; i < 16; ++i) {         // 固定 16 次, 体内无分支
                    const Pile& p = g_s.piles[i];
                    int age2 = (int)t2 - p.seen;
                    int dr_ = p.r - sr, dc_ = p.c - sc;
                    int d = (dr_ < 0 ? -dr_ : dr_) + (dc_ < 0 ? -dc_ : dc_);
                    int pos = p.r * 32 + p.c;
                    // live: 有货 且 年龄<=30 且 非去重目标 (位与代替短路)
                    int live = -(int)((unsigned)(p.v != 0) &
                                      (unsigned)(age2 <= 30) &
                                      (unsigned)(pos != dedup));
                    int s = (p.v * (30 - age2) * REC[d]) & live;
                    int gt = -(int)(s > best);
                    best = (s & gt) | (best & ~gt);
                    bi = (i & gt) | (bi & ~gt);
                }
                if (bi >= 0) {
                    g_s.goal_r[u] = g_s.piles[bi].r; g_s.goal_c[u] = g_s.piles[bi].c;
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
                if (mode == 0) {               // 站在金上: 出去再回来吃
                    for (int a = 0; a < 4; ++a) {
                        if (passable(sr + DR[a], sc + DC[a], tr, tc)) {
                            acts[0] = a; acts[1] = a ^ 1;
                            break;
                        }
                    }
                } else {                        // 站在目标上(幽灵堆): 摘除
                    if (mode == 1) pileDrop(tgr, tgc);
                    g_s.goal_kind[u] = 0;
                }
            } else {
                int r = sr, c = sc, n = 0;
                int pr = g_s.last_r[u], pc = g_s.last_c[u];
                while (n < 3 && !(r == tgr && c == tgc)) {
                    int a = steerStep(r, c, tgr, tgc, tr, tc, pr, pc);
                    if (a < 0) break;
                    acts[n++] = a;
                    pr = r; pc = c;
                    r += DR[a]; c += DC[a];
                }
                if (r == tgr && c == tgc) {
                    if (mode == 0) {
                        if (n < 3) {
                            acts[n] = acts[n - 1] ^ 1;     // 早到: 退一步
                            if (n + 1 < 3) acts[n + 1] = acts[n] ^ 1;   // d1 双吃回进
                        }
                    } else {
                        if (mode == 1) pileDrop(tgr, tgc);
                        g_s.goal_kind[u] = 0;              // 到达: 下轮窗口扫描接管
                    }
                }
            }
        }

#if defined(NSPROBE) && NSPROBE == 5
        probe5_done:;
#endif
#if defined(NSPROBE) && NSPROBE == 9
        if (in->round < 250) g_t[3] += tsc() - tc9;
        (void)td9_last;
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
                    if (besta >= 0) acts[i] = besta;
                }
                int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                if (acts[i] != STAY && nr >= 0 && nr < N && nc >= 0 && nc < N &&
                    !wallAt(nr, nc)) { r = nr; c = nc; }
            }
        }

        // ---- 卡死解困(挡两轮 = 换个活法; 主体冷函数) ----
        unsigned same = (unsigned)((sr == g_s.last_r[u]) &
                                   (sc == g_s.last_c[u]) & (acts[0] == STAY));
        g_s.stuck[u] = (uint8_t)((g_s.stuck[u] + same) & (0u - same));
        if (g_s.stuck[u] >= 2) stuckEscape(u, sr, sc, tr, tc, acts);   // 罕见
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;

        // ---- 防漂移护栏(v1 实证 +80~110/局, 几乎免费) ----
        if (in->my_units_gold[u] >= 300) {
            for (int blk = 0; blk < 3; ++blk) {
                if (acts[blk] == STAY) continue;
                int r = sr, c = sc;
                for (int i = 0; i < 3; ++i) {
                    if (acts[i] == STAY) continue;
                    int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                    if (i == blk || nrr < 0 || nrr >= N || ncc < 0 || ncc >= N ||
                        wallAt(nrr, ncc)) continue;
                    if (bombAt(nrr, ncc)) {
                        for (int j = i; j < 3; ++j) acts[j] = STAY;
                        break;
                    }
                    r = nrr; c = ncc;
                }
            }
        }
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
#if defined(NSPROBE) && NSPROBE == 9
    if (in->round < 250) ++g_tn;
    // vp 信道发射: round 250 起, 4 个 16 位均值(周期), MSB 在前
    if (in->round >= 250 && in->round < 250 + 64 && g_tn > 0) {
        int bi = in->round - 250;
        unsigned long long avg = g_t[bi / 16] / (unsigned long long)g_tn;
        if (bi / 16 == 1 || bi / 16 == 2 || bi / 16 == 3) avg /= 2;  // B/C/D 双单位求均
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
