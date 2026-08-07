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

inline bool bombAt(int r, int c) {
    for (int i = 0; i < 8; ++i)
        if (g_s.bombs[i].r == r && g_s.bombs[i].c == c && g_s.bombs[i].seen) return true;
    return false;
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
    if (free_ >= 0) g_s.bombs[free_] = {(int8_t)r, (int8_t)c, now2() ? now2() : (uint8_t)1};
}

// 矿堆缓存: 只记 v>=6 的堆(小堆不值得专程回访)
void pileNote(int r, int c, int v) {
    int free_ = -1, oldest = -1, oldest_seen = 255;
    for (int i = 0; i < 16; ++i) {
        Pile& p = g_s.piles[i];
        if (p.v && p.r == r && p.c == c) { p.v = (uint8_t)v; p.seen = now2(); return; }
        if (!p.v) { if (free_ < 0) free_ = i; }
        else if (p.seen < oldest_seen) { oldest_seen = p.seen; oldest = i; }
    }
    int slot = free_ >= 0 ? free_ : oldest;
    g_s.piles[slot] = {(int8_t)r, (int8_t)c, (uint8_t)v, now2()};
}

inline void pileDrop(int r, int c) {
    for (int i = 0; i < 16; ++i)
        if (g_s.piles[i].v && g_s.piles[i].r == r && g_s.piles[i].c == c) g_s.piles[i].v = 0;
}

inline bool passable(int r, int c, int tr, int tc) {   // tr/tc = 队友占位
    if (r < 0 || r >= N || c < 0 || c >= N) return false;
    if (wallAt(r, c)) return false;
    if (r == tr && c == tc) return false;
    return !bombAt(r, c);
}

// 曼哈顿导向一步(主方向优先, 被挡换副方向; 全堵时任意可走方向但不回头)
// pr/pc = 上一格(跨轮传入上轮位置), 防 A<->B 振荡; 凹口袋靠这条逃逸出去,
// 代替 v1 的 BFS(预算不允许)。深口袋可能多绕几轮——P50 换 P99, 符合契约。
int steerStep(int r, int c, int gr, int gc, int tr, int tc, int pr, int pc) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr < 0 ? 0 : 1, ac = dcc < 0 ? 2 : 3;
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int p0 = adr >= adc ? ar : ac, p1 = adr >= adc ? ac : ar;
    if (adr && adc) {
        if (passable(r + DR[p0], c + DC[p0], tr, tc)) return p0;
        if (passable(r + DR[p1], c + DC[p1], tr, tc)) return p1;
    } else {
        int a = adr ? ar : ac;
        if (passable(r + DR[a], c + DC[a], tr, tc)) return a;
    }
    for (int a = 0; a < 4; ++a) {                  // 逃逸: 可走且不回头
        int nr = r + DR[a], nc = c + DC[a];
        if (nr == pr && nc == pc) continue;
        if (passable(nr, nc, tr, tc)) return a;
    }
    return -1;
}

GameOutput decide(const GameInput* in) {
    if (in->round <= g_s.last_round) { memset(&g_s, 0, sizeof(g_s)); g_s.patrol[1] = 3; }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0) {                    // 炸弹波: 旧记忆全部过期
        for (int i = 0; i < 8; ++i) g_s.bombs[i].seen = 0;
    }

    GameOutput out = SAFE_OUT;

    // 缓存对账: 24 条记录 x 1 次窗口判定+读格(代替逐格反查)
    {
        int u0r = in->my_units[0].row, u0c = in->my_units[0].col;
        int u1r = in->my_units[1].row, u1c = in->my_units[1].col;
        auto inwin = [&](int r, int c) {
            int d0r = r - u0r, d0c = c - u0c, d1r = r - u1r, d1c = c - u1c;
            return ((unsigned)(d0r + 2) <= 4u && (unsigned)(d0c + 2) <= 4u) ||
                   ((unsigned)(d1r + 2) <= 4u && (unsigned)(d1c + 2) <= 4u);
        };
        for (int i = 0; i < 16; ++i) {
            Pile& p = g_s.piles[i];
            if (p.v && inwin(p.r, p.c)) {
                int g = in->grid[p.r][p.c];
                if (g >= 6) { p.v = (uint8_t)(g > 255 ? 255 : g); p.seen = now2(); }
                else p.v = 0;                        // 吃小/吃空/变弹: 摘除
            }
        }
        for (int i = 0; i < 8; ++i) {
            BombM& b = g_s.bombs[i];
            if (b.seen && inwin(b.r, b.c) && in->grid[b.r][b.c] != BOMB) b.seen = 0;
        }
    }

    // 敌情(便宜: 只记最近一次)
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N) {
            g_s.esight_r = (int8_t)r; g_s.esight_c = (int8_t)c;
            g_s.esight_seen = now2() ? now2() : 1;
        }
    }

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;

        // ---- 窗口扫描(预算大头): 窗口值缓存 + 金格收集 + 增量更新持久结构 ----
        // 逐格只做增量记录; 缓存失效统一对账(教训: 逐格反查=半个预算)
        int8_t wv[25];                     // 窗口局部值(簇加成/尾步填充复用)
        int8_t gr_[8], gc_[8]; int gn_ = 0;
        int r0 = sr - 2 < 0 ? 0 : sr - 2, r1 = sr + 2 >= N ? N - 1 : sr + 2;
        int c0 = sc - 2 < 0 ? 0 : sc - 2, c1 = sc + 2 >= N ? N - 1 : sc + 2;
        memset(wv, -1, 25);                // 出界视作不可走
        for (int r = r0; r <= r1; ++r) {
            const int* row = in->grid[r];
            int base = (r - sr + 2) * 5 - sc + 2;
            for (int c = c0; c <= c1; ++c) {
                int v = row[c];
                wv[base + c] = (int8_t)(v > 127 ? 127 : v);
                if (v > 0) {
                    if (gn_ < 8) { gr_[gn_] = (int8_t)r; gc_[gn_] = (int8_t)c; ++gn_; }
                    if (v >= 5) pileNote(r, c, v);
                } else if (v == OBSTACLE) {
                    g_s.wall[r] |= 1u << c;
                } else if (v == BOMB) {
                    bombNote(r, c);
                }
            }
        }
        // 采集打分(带簇加成: 邻格金折半计入, v1 实证有效)
        int bestr = -1, bestc = -1, bests = 0;
        for (int i = 0; i < gn_; ++i) {
            int r = gr_[i], c = gc_[i];
            int wi = (r - sr + 2) * 5 + (c - sc + 2);
            int v = wv[wi];
            int nb = 0;
            if (wi >= 5 && wv[wi - 5] > 0) nb += wv[wi - 5];
            if (wi < 20 && wv[wi + 5] > 0) nb += wv[wi + 5];
            if (wi % 5 && wv[wi - 1] > 0) nb += wv[wi - 1];
            if (wi % 5 != 4 && wv[wi + 1] > 0) nb += wv[wi + 1];
            int d = (r > sr ? r - sr : sr - r) + (c > sc ? c - sc : sc - c);
            int sc_ = (v * 2 + nb) * REC[d];
            if (sc_ > bests) { bests = sc_; bestr = r; bestc = c; }
        }

#if defined(NSPROBE) && NSPROBE == 1
        (void)bests; continue;               // 探针1: 只测 扫描+对账(站桩)
#endif
#if defined(NSPROBE) && NSPROBE == 3
        {   // 探针3: 扫描+对账+轮转走位(窗口移动) —— 分离"输入新鲜区冷读"成本
            (void)bests;
            int a = (in->round / 4 + u * 2) & 3;
            acts[0] = acts[1] = acts[2] = a;
            continue;
        }
#endif
        // ---- 决策核 ----
        if (bestr >= 0) {
            // 闭式采集(v1 singleGold 语义): 直取 + 早到振荡
            int d = (bestr > sr ? bestr - sr : sr - bestr) +
                    (bestc > sc ? bestc - sc : sc - bestc);
            g_s.goal_kind[u] = 0;                    // 就地有活干, 目标层休眠
            if (d == 0) {                          // 站在金上: 出去再回来吃
                for (int a = 0; a < 4; ++a) {
                    if (passable(sr + DR[a], sc + DC[a], tr, tc)) {
                        acts[0] = a; acts[1] = a ^ 1;
                        break;
                    }
                }
            } else {
                int r = sr, c = sc, n = 0;
                int pr = g_s.last_r[u], pc = g_s.last_c[u];
                while (n < 3 && !(r == bestr && c == bestc)) {
                    int a = steerStep(r, c, bestr, bestc, tr, tc, pr, pc);
                    if (a < 0) break;
                    acts[n++] = a;
                    pr = r; pc = c;
                    r += DR[a]; c += DC[a];
                }
                if (r == bestr && c == bestc && n < 3) {
                    acts[n] = acts[n - 1] ^ 1;     // 早到: 退一步
                    if (n + 1 < 3) acts[n + 1] = acts[n] ^ 1;   // d1 双吃回进
                }
            }
        } else {
            // 无金: 持久目标(矿堆缓存优先, 否则巡逻) —— v1 的 2.2μs 目标层在此
            // 收缩成 16 条缓存记录的一次线性打分
            bool valid = false;
            if (g_s.goal_kind[u] == 1) {             // 现有矿堆目标仍然成立?
                for (int i = 0; i < 16; ++i)
                    if (g_s.piles[i].v && g_s.piles[i].r == g_s.goal_r[u] &&
                        g_s.piles[i].c == g_s.goal_c[u]) { valid = true; break; }
            } else if (g_s.goal_kind[u] == 2) {
                valid = !(sr == g_s.goal_r[u] && sc == g_s.goal_c[u]);
            }
            if (!valid) {
                int best = 0, bi = -1;
                uint8_t t2 = now2();
                for (int i = 0; i < 16; ++i) {
                    Pile& p = g_s.piles[i];
                    if (!p.v) continue;
                    int age2 = (int)t2 - p.seen;
                    if (age2 > 30) { p.v = 0; continue; }          // ~60 轮过期
                    if (u == 1 && g_s.goal_kind[0] == 1 &&           // 目标去重
                        p.r == g_s.goal_r[0] && p.c == g_s.goal_c[0]) continue;
                    int d = (p.r > sr ? p.r - sr : sr - p.r) +
                            (p.c > sc ? p.c - sc : sc - p.c);
                    int s = p.v * (30 - age2) * REC[d];   // (常数 /30 不影响排序)
                    if (s > best) { best = s; bi = i; }
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
            int gr = g_s.goal_r[u], gc = g_s.goal_c[u];
            int r = sr, c = sc, n = 0;
            int pr = g_s.last_r[u], pc = g_s.last_c[u];
            while (n < 3 && !(r == gr && c == gc)) {
                int a = steerStep(r, c, gr, gc, tr, tc, pr, pc);
                if (a < 0) break;
                acts[n++] = a;
                pr = r; pc = c;
                r += DR[a]; c += DC[a];
            }
            if (r == gr && c == gc) {
                if (g_s.goal_kind[u] == 1) pileDrop(gr, gc);   // 到了发现没金(窗口无金分支) = 幽灵堆
                g_s.goal_kind[u] = 0;
            }
        }

        // ---- 尾步填充(v1 实证: 16% 的步在罚站; 复用 wv, 零额外读格) ----
        {
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                if (acts[i] == STAY) {
                    int besta = -1, bv = 0;
                    for (int a = 0; a < 4; ++a) {
                        int nr = r + DR[a], nc = c + DC[a];
                        int ur = nr - sr + 2, uc = nc - sc + 2;
                        if ((unsigned)ur > 4u || (unsigned)uc > 4u) continue;
                        int v = wv[ur * 5 + uc];       // >0 即金(金格无墙无弹)
                        if (v > bv && !(nr == tr && nc == tc)) { bv = v; besta = a; }
                    }
                    if (besta >= 0) acts[i] = besta;
                }
                int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                if (acts[i] != STAY && nr >= 0 && nr < N && nc >= 0 && nc < N &&
                    !wallAt(nr, nc)) { r = nr; c = nc; }
            }
        }

        // ---- 卡死解困(挡两轮 = 换个活法) ----
        if (sr == g_s.last_r[u] && sc == g_s.last_c[u] && acts[0] == STAY) {
            if (++g_s.stuck[u] >= 2) {
                g_s.goal_kind[u] = 0;
                g_s.patrol[u] = (uint8_t)((g_s.patrol[u] + 1) & 7);
                for (int a = 0; a < 4; ++a)
                    if (passable(sr + DR[a], sc + DC[a], tr, tc)) { acts[0] = a; break; }
                g_s.stuck[u] = 0;
            }
        } else g_s.stuck[u] = 0;
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
        return sanitize(decide(input));
    } catch (...) {
        return SAFE_OUT;
    }
}
