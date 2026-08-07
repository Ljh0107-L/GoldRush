// ns.cpp — GoldRush 2.0 纳秒级架构（与 player.cpp 并行 A/B）
//
// 设计从 220ns 预算(≈660周期)倒推（#1 Tiuntled-1 的形态, 见 AGENT.md §6.2c）：
//   - 无 BFS / 无 DFS / 无全图扫描 / 无补丁构建 —— 每回合固定的微型代码路径
//   - 世界更新只扫 2x25 热窗口; 金币列表增量维护
//   - 目标低频重选(死了/到了才重选, 均摊), 走位 = 曼哈顿导向反射
//   - 每步 4 邻格贪心: 拾取收益直接读"本回合输入网格"(精确), 65% 链式模拟
//   - 期望 P50≈P90≈300-500ns -> 对全中游先手
//
// 提交: 开发机 g++ -O3 -march=native -fPIC -shared -o ns.so ns.cpp
#include <cstdint>
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int FOG = -5, BOMB = -3, OBSTACLE = -1;
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

inline int ceil65(int v) { return (v * 65 + 99) / 100; }

struct Cell { int8_t known; int8_t listed; uint16_t seen; };

// 探索锚点(声明前置, World::update 归因用)
constexpr int8_t AR[16] = {4, 4, 4, 8, 8, 12, 12, 12,  1, 1, 1, 8, 8, 15, 15, 15};
constexpr int8_t AC[16] = {4, 8, 12, 4, 12, 4, 8, 12,  1, 8, 15, 1, 15, 1, 8, 15};

struct World {
    Cell cell[N][N];
    uint16_t contested[N][N];
    uint16_t glist[64];
    int gn;
    int round, last_round;
    // 每单位缓存目标
    int8_t ttr[2], ttc[2];      // -1 = 无
    // 探索锚点轮换, 记上次到访回合与产量
    uint16_t anchor_seen[16];
    uint16_t anchor_yield[16];

    World() { reset(); }
    void reset() {
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) { cell[r][c] = {(int8_t)FOG, 0, 0}; contested[r][c] = 0; }
        gn = 0;
        last_round = -1;
        ttr[0] = ttr[1] = -1;
        for (int i = 0; i < 16; ++i) { anchor_seen[i] = 0; anchor_yield[i] = 0; }
    }
    inline bool contestedAt(int r, int c) const { return (uint16_t)round < contested[r][c]; }
    void stamp(int r0, int c0) {
        for (int r = r0 - 3 < 0 ? 0 : r0 - 3; r <= (r0 + 3 >= N ? N - 1 : r0 + 3); ++r)
            for (int c = c0 - 3 < 0 ? 0 : c0 - 3; c <= (c0 + 3 >= N ? N - 1 : c0 + 3); ++c)
                contested[r][c] = (uint16_t)(round + 60);
    }
    inline int gold(int r, int c) const {
        const Cell& x = cell[r][c];
        if (x.known < 1) return 0;
        int a = round - (int)(x.seen - 1);
        return a < 30 ? x.known * (30 - a) / 30 : 0;
    }
    inline bool bomb(int r, int c) const {
        const Cell& x = cell[r][c];
        return x.known == BOMB && round - (int)(x.seen - 1) <= 20;
    }
    inline bool wall(int r, int c) const { return cell[r][c].known == OBSTACLE; }

    void update(const GameInput* in) {
        if (in->round <= last_round) reset();
        last_round = round = in->round;
        for (int u = 0; u < 2; ++u) {
            int ur = in->my_units[u].row, uc = in->my_units[u].col;
            if (ur < 0 || ur >= N || uc < 0 || uc >= N) continue;
            int r0 = ur - 2 < 0 ? 0 : ur - 2, r1 = ur + 2 >= N ? N - 1 : ur + 2;
            int c0 = uc - 2 < 0 ? 0 : uc - 2, c1 = uc + 2 >= N ? N - 1 : uc + 2;
            for (int r = r0; r <= r1; ++r)
                for (int c = c0; c <= c1; ++c) {
                    int v = in->grid[r][c];
                    if (v == FOG) continue;
                    Cell& x = cell[r][c];
                    if (x.known == OBSTACLE) continue;
                    // 记忆>=3金被清空且附近无可见NPC = 对手来过(OPPTRACK)
                    if (v == 0 && x.known >= 3) {
                        bool npc_near = false;
                        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
                            int nr = in->visible_npcs[j].pos.row, nc = in->visible_npcs[j].pos.col;
                            if (nr >= 0 && (nr > r ? nr - r : r - nr) + (nc > c ? nc - c : c - nc) <= 3)
                                { npc_near = true; break; }
                        }
                        if (!npc_near) stamp(r, c);
                    }
                    if (v >= 1 && x.known >= 0 && v > x.known) {   // 刷新事件 -> 归因最近锚点
                        int bi2 = 0, bd = 999;
                        for (int t = 0; t < 16; ++t) {
                            int dd = (AR[t] > r ? AR[t] - r : r - AR[t]) + (AC[t] > c ? AC[t] - c : c - AC[t]);
                            if (dd < bd) { bd = dd; bi2 = t; }
                        }
                        if (anchor_yield[bi2] < 500) anchor_yield[bi2] += 1;
                    }
                    x.known = (int8_t)(v > 127 ? 127 : v);
                    x.seen = (uint16_t)(in->round + 1);
                    if (v >= 1 && !x.listed && gn < 64) {
                        glist[gn++] = (uint16_t)(r * N + c);
                        x.listed = 1;
                    }
                }
        }
    }
};
World g;



// 低频目标重选: 扫列表一次(<=64), 均摊到多轮
void pickTarget(const GameInput* in, int u, int sr, int sc) {
    long best = 0;
    int bi = -1;
    for (int i = 0; i < g.gn; ++i) {
        int idx = g.glist[i], r = idx / N, c = idx % N;
        if (g.cell[r][c].known < 1) {              // 顺手摘除空条目
            g.cell[r][c].listed = 0;
            g.glist[i--] = g.glist[--g.gn];
            continue;
        }
        int v = g.gold(r, c);
        if (v <= 0) continue;
        if (r == g.ttr[1 - u] && c == g.ttc[1 - u]) continue;   // 队友目标
        int d = (r > sr ? r - sr : sr - r) + (c > sc ? c - sc : sc - c);
        long val = v * 100L;
        if (g.contestedAt(r, c)) {
            if (v < 3) continue;
            val /= 3;
        }
        // NPC 折价(保守)
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nr = in->visible_npcs[j].pos.row, nc = in->visible_npcs[j].pos.col;
            if (nr < 0) continue;
            int nd = (nr > r ? nr - r : r - nr) + (nc > c ? nc - c : c - nc);
            if (nd * 13 < d * 10) { val /= 3; break; }
        }
        long score = val / (d + 1);
        if (score > best) { best = score; bi = idx; }
    }
    if (bi >= 0) { g.ttr[u] = (int8_t)(bi / N); g.ttc[u] = (int8_t)(bi % N); return; }
    // 无金: 探索锚点(最久未访优先, 近者破平)
    long bests = -1;
    int ba = 0;
    for (int i = 0; i < 16; ++i) {
        long age = (long)(g.round + 1) - g.anchor_seen[i];
        if (age > 100) age = 100;
        long d = (AR[i] > sr ? AR[i] - sr : sr - AR[i]) + (AC[i] > sc ? AC[i] - sc : sc - AC[i]);
        long s = age * 16 - d * 8 + (long)g.anchor_yield[i] * 8;
        if (s > bests) { bests = s; ba = i; }
    }
    g.anchor_seen[ba] = (uint16_t)(g.round + 1);
    g.ttr[u] = AR[ba]; g.ttc[u] = AC[ba];
}

// ---- 3步微型穷举(活数据直读, 消耗覆盖层 undo) ----
struct Mini {
    const GameInput* in;
    int mr, mc, mygold, tr, tc;
    int npc3_idx[4], npc3n;      // NPC>=3 聚集格(预计算, 罕见)
    int ov_idx[8], ov_left[8], ovn;      // 消耗覆盖
    long best; int bacts[3];

    inline int cg(int r, int c) {
        for (int t = 0; t < ovn; ++t)
            if (ov_idx[t] == r * N + c) return ov_left[t];
        int iv = in->grid[r][c];
        if (iv >= 1) return iv;
        if (iv == FOG) return g.gold(r, c);
        return 0;
    }
    inline bool isbomb(int r, int c) {
        int iv = in->grid[r][c];
        return iv == BOMB || (iv == FOG && g.bomb(r, c));
    }
    void dfs(int r, int c, int depth, int acts[3], long sc) {
        if (sc > best) {
            best = sc;
            for (int t = 0; t < 3; ++t) bacts[t] = t < depth ? acts[t] : STAY;
        }
        if (depth == 3) return;
        for (int a = 0; a < 4; ++a) {
            int nr = r + DR[a], nc = c + DC[a];
            if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
            if (in->grid[nr][nc] == OBSTACLE || g.wall(nr, nc)) continue;
            if (nr == mr && nc == mc) continue;
            bool tramp = false;
            for (int t = 0; t < npc3n; ++t)
                if (npc3_idx[t] == nr * N + nc) { tramp = true; break; }
            if (tramp) continue;
            acts[depth] = a;
            long add = 0;
            int v = cg(nr, nc), undo = -1, undov = 0;
            if (v > 0) {
                int take = ceil65(v);
                add += (long)take * 100;
                for (int t = 0; t < ovn; ++t)
                    if (ov_idx[t] == nr * N + nc) { undo = t; undov = ov_left[t]; ov_left[t] = v - take; break; }
                if (undo < 0 && ovn < 8) { ov_idx[ovn] = nr * N + nc; ov_left[ovn] = v - take; undo = ovn; undov = -12345; ++ovn; }
            }
            bool bombed = false;
            if (isbomb(nr, nc)) { add -= (long)((mygold + 9) / 10) * 200; bombed = true; }
            // 朝目标位移微分(平局裁决)
            int dd0 = (tr > r ? tr - r : r - tr) + (tc > c ? tc - c : c - tc);
            int dd1 = (tr > nr ? tr - nr : nr - tr) + (tc > nc ? tc - nc : nc - tc);
            long move_sc = dd1 < dd0 ? 3 : -1;
            (void)bombed;
            dfs(nr, nc, depth + 1, acts, sc + add + move_sc);
            if (undo >= 0) {
                if (undov == -12345) { --ovn; }
                else ov_left[undo] = undov;
            }
        }
    }
};
Mini g_mini;

void miniDfs(const GameInput* in, int u, int sr, int sc, int mr, int mc,
             int mygold, int tr, int tc, int* acts) {
    (void)u;
    g_mini.in = in; g_mini.mr = mr; g_mini.mc = mc;
    g_mini.mygold = mygold; g_mini.tr = tr; g_mini.tc = tc;
    g_mini.ovn = 0;
    g_mini.npc3n = 0;
    for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
        int nr = in->visible_npcs[j].pos.row, nc = in->visible_npcs[j].pos.col;
        if (nr < 0) continue;
        int cnt = 1;
        for (int k2 = 0; k2 < in->num_visible_npcs && k2 < MAX_NPCS; ++k2)
            if (k2 != j && in->visible_npcs[k2].pos.row == nr && in->visible_npcs[k2].pos.col == nc) ++cnt;
        if (cnt >= 3 && g_mini.npc3n < 4) {
            bool dup = false;
            for (int t = 0; t < g_mini.npc3n; ++t)
                if (g_mini.npc3_idx[t] == nr * N + nc) dup = true;
            if (!dup) g_mini.npc3_idx[g_mini.npc3n++] = nr * N + nc;
        }
    }
    g_mini.best = -1000000;
    g_mini.bacts[0] = g_mini.bacts[1] = g_mini.bacts[2] = STAY;
    int tmp[3];
    g_mini.dfs(sr, sc, 0, tmp, 0);
    for (int t = 0; t < 3; ++t) acts[t] = g_mini.bacts[t];
}

}  // namespace

extern "C" GameOutput moveDecision(const GameInput* in) {
    try {
        if (!in) return SAFE_OUT;
        g.update(in);
        GameOutput out = SAFE_OUT;

        // 邻域抓取的链式模拟: 记录本回合已吃格(最多6)
        int taken_idx[6], taken_left[6], tn = 0;

        for (int u = 0; u < 2; ++u) {
            int sr = in->my_units[u].row, sc = in->my_units[u].col;
            int* acts = out.actions + u * 3;
            acts[0] = acts[1] = acts[2] = STAY;
            if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;
            int mr = in->my_units[1 - u].row, mc = in->my_units[1 - u].col;
            int mygold = in->my_units_gold[u];

            // 目标失效检查(便宜): 无 / 已到 / 空了 / 争抢区
            int tr = g.ttr[u], tc = g.ttc[u];
            if (tr < 0 || (tr == sr && tc == sc) || (g.cell[tr][tc].known == 0)) {
                if (tr >= 0 && g.cell[tr][tc].known == 0 && g.gold(tr, tc) == 0)
                    ;                                    // 到手前被吃的 stamp 在 update 已做
                pickTarget(in, u, sr, sc);
                tr = g.ttr[u]; tc = g.ttc[u];
            }

            // 3步微型穷举(4^3, 活数据直读): 收益=沿途65%链式 - 炸弹 - 目标位移
            miniDfs(in, u, sr, sc, mr, mc, mygold, tr, tc, acts);
            if (false) {
            int r = sr, c = sc;
            for (int s = 0; s < 3; ++s) {
                int besta = STAY;
                long bestsc = 0;
                for (int a = 0; a < 4; ++a) {
                    int nr = r + DR[a], nc = c + DC[a];
                    if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
                    int iv = in->grid[nr][nc];
                    if (iv == OBSTACLE) continue;
                    if (g.wall(nr, nc)) continue;
                    if (nr == mr && nc == mc) continue;              // 队友占位
                    // 本回合已吃过该格 -> 用剩余量
                    int cellgold = iv >= 1 ? iv : (iv == FOG ? g.gold(nr, nc) : 0);
                    for (int t = 0; t < tn; ++t)
                        if (taken_idx[t] == nr * N + nc) { cellgold = taken_left[t]; break; }
                    long sc_ = 0;
                    int gain = cellgold > 0 ? ceil65(cellgold) : 0;
                    sc_ += (long)gain * 100;
                    bool isbomb = (iv == BOMB) || (iv == FOG && g.bomb(nr, nc));
                    if (isbomb) sc_ -= (long)((mygold + 9) / 10) * 200;   // 惩罚x2
                    // NPC>=3 踩踏格跳过(罕见, 直接禁走)
                    int npcs_here = 0;
                    for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j)
                        if (in->visible_npcs[j].pos.row == nr && in->visible_npcs[j].pos.col == nc)
                            ++npcs_here;
                    if (npcs_here >= 3) continue;
                    // 朝目标位移加分(远小于1金, 只作平局裁决)
                    int d0 = (tr > r ? tr - r : r - tr) + (tc > c ? tc - c : c - tc);
                    int d1 = (tr > nr ? tr - nr : nr - tr) + (tc > nc ? tc - nc : nc - tc);
                    if (d1 < d0) sc_ += 30;
                    else sc_ -= 10;
                    if (sc_ > bestsc) { bestsc = sc_; besta = a; }
                }
                if (besta == STAY) {
                    if (s == 0 && tr >= 0) {          // 开步即卡: 弃目标防死锁
                        g.contested[tr][tc] = (uint16_t)(g.round + 20);
                        g.ttr[u] = -1;
                    }
                    break;
                }
                acts[s] = besta;
                int nr = r + DR[besta], nc = c + DC[besta];
                // 记链式消耗
                int iv = in->grid[nr][nc];
                int cg = iv >= 1 ? iv : (iv == FOG ? g.gold(nr, nc) : 0);
                for (int t = 0; t < tn; ++t)
                    if (taken_idx[t] == nr * N + nc) { cg = taken_left[t]; break; }
                if (cg > 0) {
                    int left = cg - ceil65(cg);
                    bool found = false;
                    for (int t = 0; t < tn; ++t)
                        if (taken_idx[t] == nr * N + nc) { taken_left[t] = left; found = true; break; }
                    if (!found && tn < 6) { taken_idx[tn] = nr * N + nc; taken_left[tn] = left; ++tn; }
                }
                r = nr; c = nc;
            }
            }
        }

        out.k = 3;
        out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
        out.vp = 0;
        // 边界层 clamp
        for (int i = 0; i < S; ++i)
            if (out.actions[i] < 0 || out.actions[i] > 4) out.actions[i] = STAY;
        return out;
    } catch (...) {
        return SAFE_OUT;
    }
}
