// player.cpp — GoldRush 2.0 主战策略
//
// cpp2 (P1+P2)：持久世界模型 + 三层决策
//   1) 局部：3 步全路径穷举(≤125 条)，精确模拟沿途拾取 65%/炸弹 10%/踩踏 5%
//   2) 全局：BFS 到已知金币，按年龄衰减估值、按 NPC 距离折价(NPC≈噪声贪心,3步/轮)
//   3) 探索：无目标时朝最"陈旧"的格子走(修复贪心罚站缺陷)，中心 9x9 加成
// 环境统计依据见 AGENT.md §6.2b（全量日志实证）。
//
// 结构分层（新策略只动"策略层"，边界层不要碰）：
//   moveDecision()  ← extern "C" 边界层：兜底 + sanitize
//     └─ decide()   ← 策略层
//
// 编译：开发机(8.153.76.120) make 出 player.so 提交；本机只 make check / make local。
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int FOG = -5, BOMB = -3, OBSTACLE = -1;

constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

inline int ceilPct(int v, int pct) { return (v * pct + 99) / 100; }

// ---------- 跨回合常驻世界模型 ----------
struct World {
    int   known[N][N];   // 最后一次观测值(-3/-1/0/>=1)；FOG=从未见过
    short seen[N][N];    // 最后观测回合；-1=从未
    int   last_round;

    void reset() {
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) { known[r][c] = FOG; seen[r][c] = -1; }
        last_round = -1;
    }
    void update(const GameInput* in) {
        if (in->round <= last_round) reset();          // 新对局(理论上进程会重启,保险)
        last_round = in->round;
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) {
                int v = in->grid[r][c];
                if (v == FOG) continue;                 // 视野外: 保留旧记忆
                if (known[r][c] == OBSTACLE) continue;  // 障碍永久(同图内不变)
                known[r][c] = v;
                seen[r][c] = (short)in->round;
            }
    }
};
World g_world;

// ---------- 每回合的有效视图(由世界模型折算) ----------
struct View {
    int  gold[N][N];     // 估值后的金币量(年龄衰减; 全量日志: NPC 会持续吃掉旧金币)
    bool bomb[N][N];     // 已知炸弹
    bool block[N][N];    // 障碍 + 他人角色(撞上=浪费步)
    int  npcs[N][N];     // 可见 NPC 数(踩踏判定)
    int  round;

    void build(const GameInput* in) {
        round = in->round;
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) {
                int v = g_world.known[r][c];
                int age = (g_world.seen[r][c] < 0) ? 999 : round - g_world.seen[r][c];
                gold[r][c] = (v >= 1 && age < 30) ? v * (30 - age) / 30 : 0;
                bomb[r][c] = (v == BOMB && age <= 20);   // 每20轮刷新一波,旧情报过期
                block[r][c] = (v == OBSTACLE);
                npcs[r][c] = 0;
            }
        for (int i = 0; i < 2; ++i) {
            int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
            if (r >= 0 && r < N && c >= 0 && c < N) block[r][c] = true;
        }
        for (int i = 0; i < in->num_visible_npcs && i < MAX_NPCS; ++i) {
            int r = in->visible_npcs[i].pos.row, c = in->visible_npcs[i].pos.col;
            if (r >= 0 && r < N && c >= 0 && c < N) ++npcs[r][c];
        }
    }
};
View g_view;

// ---------- BFS(静态缓冲,零堆分配) ----------
struct Bfs {
    int  visit_tag[N][N] = {};
    int  cur_tag = 0;
    signed char prev_act[N][N];
    short qr[N * N], qc[N * N];
    int  qlen = 0, dist_[N][N];

    void run(const View& v, int sr, int sc) {
        ++cur_tag;
        qlen = 0;
        qr[qlen] = (short)sr; qc[qlen] = (short)sc; ++qlen;
        visit_tag[sr][sc] = cur_tag;
        prev_act[sr][sc] = -1;
        dist_[sr][sc] = 0;
        for (int head = 0; head < qlen; ++head) {
            int r = qr[head], c = qc[head];
            for (int a = 0; a < 4; ++a) {
                int nr = r + DR[a], nc = c + DC[a];
                if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
                if (visit_tag[nr][nc] == cur_tag) continue;
                if (v.block[nr][nc] || v.bomb[nr][nc]) continue;
                visit_tag[nr][nc] = cur_tag;
                prev_act[nr][nc] = (signed char)a;
                dist_[nr][nc] = dist_[r][c] + 1;
                qr[qlen] = (short)nr; qc[qlen] = (short)nc; ++qlen;
            }
        }
    }
    bool reached(int r, int c) const { return visit_tag[r][c] == cur_tag; }
    int  dist(int r, int c)  const { return reached(r, c) ? dist_[r][c] : 9999; }

    int pathTo(int sr, int sc, int tr, int tc, int* out, int cap) const {
        if (!reached(tr, tc)) return 0;
        int tmp[N * N];
        int len = 0, r = tr, c = tc;
        while (!(r == sr && c == sc)) {
            int a = prev_act[r][c];
            if (a < 0) break;
            tmp[len++] = a;
            r -= DR[a]; c -= DC[a];
        }
        int n = len < cap ? len : cap;
        for (int i = 0; i < n; ++i) out[i] = tmp[len - 1 - i];
        return n;
    }
};
Bfs g_bfs;

// ---------- 局部 3 步穷举：精确模拟沿途收益 ----------
struct LocalSearch {
    int sim_gold[N][N];
    bool sim_bomb[N][N];
    int best_score, best_acts[3];
    int unit_gold;

    // 深度优先枚举 5^3 条动作序列；撞墙/撞人 = 该步原地(与引擎一致)
    void dfs(int r, int c, int depth, int acts[3], int gained, int score) {
        if (depth == 3) {
            if (score > best_score) {
                best_score = score;
                for (int i = 0; i < 3; ++i) best_acts[i] = acts[i];
            }
            return;
        }
        for (int a = 0; a < 5; ++a) {
            int nr = r + DR[a], nc = c + DC[a];
            bool moved = (a != STAY);
            if (nr < 0 || nr >= N || nc < 0 || nc >= N ||
                g_view.block[nr][nc]) { nr = r; nc = c; moved = false; }
            acts[depth] = a;
            int add = 0, undo_gold = -1, sc = score, gn = gained;
            bool undo_bomb = false;
            if (moved && !(nr == r && nc == c)) {
                if (sim_gold[nr][nc] > 0) {
                    add = ceilPct(sim_gold[nr][nc], 65);
                    undo_gold = sim_gold[nr][nc];
                    sim_gold[nr][nc] -= add;
                    gn += add; sc += add * 10;
                }
                // 惩罚项按 2 倍计: 3 步视界看不到"绕一轮再拿"的替代方案, 补偿短视
                if (sim_bomb[nr][nc]) {
                    sc -= ceilPct(unit_gold + gn, 10) * 20;
                    sim_bomb[nr][nc] = false;
                    undo_bomb = true;
                }
                if (g_view.npcs[nr][nc] >= 3)
                    sc -= ceilPct(unit_gold + gn, 5) * 20;
            }
            dfs(nr, nc, depth + 1, acts, gn, sc);
            if (undo_gold >= 0) sim_gold[nr][nc] = undo_gold;
            if (undo_bomb) sim_bomb[nr][nc] = true;
        }
    }

    // 返回最优净收益(score/10≈金币)；acts 输出 3 个动作
    int run(int sr, int sc, int gold_now, int* acts_out) {
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) {
                sim_gold[r][c] = g_view.gold[r][c];
                sim_bomb[r][c] = g_view.bomb[r][c];
            }
        best_score = 0;
        best_acts[0] = best_acts[1] = best_acts[2] = STAY;
        unit_gold = gold_now;
        int acts[3];
        dfs(sr, sc, 0, acts, 0, 0);
        for (int i = 0; i < 3; ++i) acts_out[i] = best_acts[i];
        return best_score;
    }
};
LocalSearch g_local;

// ---------- 全局目标：已知金币按年龄/NPC 折价 ----------
// 返回 true 并填 3 个动作；claimed 防两角色抢同一目标
bool globalTarget(const GameInput* in, int sr, int sc, bool claimed[N][N], int* out) {
    g_bfs.run(g_view, sr, sc);
    long best = 0;
    int br = -1, bc = -1;
    for (int i = 1; i < g_bfs.qlen; ++i) {
        int r = g_bfs.qr[i], c = g_bfs.qc[i];
        int v = g_view.gold[r][c];
        if (v <= 0 || claimed[r][c]) continue;
        int d = g_bfs.dist(r, c);
        long val = v * 100L;
        // NPC 更近则大概率抢不过(NPC 3步/轮、~75%趋金): 折到 1/3
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nr = in->visible_npcs[j].pos.row, nc = in->visible_npcs[j].pos.col;
            if (nr < 0) continue;
            int nd = (nr > r ? nr - r : r - nr) + (nc > c ? nc - c : c - nc);
            if (nd * 13 < d * 10) { val /= 3; break; }
        }
        long score = val / (d + 1);
        if (score > best) { best = score; br = r; bc = c; }
    }
    if (br < 0) return false;
    claimed[br][bc] = true;
    for (int i = 0; i < 3; ++i) out[i] = STAY;
    g_bfs.pathTo(sr, sc, br, bc, out, 3);
    return true;
}

// ---------- 探索：朝最陈旧格子走(消灭罚站) ----------
void explore(int sr, int sc, bool claimed[N][N], int* out) {
    g_bfs.run(g_view, sr, sc);
    long best = -1;
    int br = -1, bc = -1;
    for (int i = 1; i < g_bfs.qlen; ++i) {
        int r = g_bfs.qr[i], c = g_bfs.qc[i];
        if (claimed[r][c]) continue;
        int age = (g_world.seen[r][c] < 0) ? 100
                  : (g_view.round - g_world.seen[r][c] > 60 ? 60
                     : g_view.round - g_world.seen[r][c]);
        long s = (long)age * 100;
        if (r >= 4 && r <= 12 && c >= 4 && c <= 12) s = s * 3 / 2;  // 中心每轮出金
        s /= g_bfs.dist(r, c) + 1;
        if (s > best) { best = s; br = r; bc = c; }
    }
    for (int i = 0; i < 3; ++i) out[i] = STAY;
    if (br >= 0) {
        claimed[br][bc] = true;
        g_bfs.pathTo(sr, sc, br, bc, out, 3);
    }
}

// ---------- 策略层 ----------
GameOutput decide(const GameInput* in) {
    g_world.update(in);
    g_view.build(in);

    GameOutput out = SAFE_OUT;
    bool claimed[N][N] = {};

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;

        // 队友当前格视为占用(自撞=浪费步)
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        bool saved = false;
        if (tr >= 0 && tr < N && tc >= 0 && tc < N) {
            saved = g_view.block[tr][tc];
            g_view.block[tr][tc] = true;
        }

        int gain = g_local.run(sr, sc, in->my_units_gold[u], acts);
        if (gain > 0) {
            // 局部已有净收益; 把途经格标记为已认领, 免得队友重复扑
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                if (nr >= 0 && nr < N && nc >= 0 && nc < N && !g_view.block[nr][nc]) {
                    r = nr; c = nc;
                    claimed[r][c] = true;
                }
            }
        } else if (!globalTarget(in, sr, sc, claimed, acts)) {
            explore(sr, sc, claimed, acts);
        }

        if (tr >= 0 && tr < N && tc >= 0 && tc < N) g_view.block[tr][tc] = saved;
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = 0;
    return out;
}

// ---------- 边界层：兜底 + 逐字段 clamp，永远不要删 ----------
GameOutput sanitize(GameOutput o) {
    for (int i = 0; i < S; ++i)
        if (o.actions[i] < 0 || o.actions[i] > 4) o.actions[i] = STAY;
    if (o.k < 0 || o.k > S) o.k = 3;
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
