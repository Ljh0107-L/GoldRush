// player.cpp — GoldRush 2.0 主战策略
//
// cpp4 (P1+P2+P4+P5)：
//   增量世界模型 + 视野购买 + 7x7补丁上的3步穷举(速度: 镜像局证明先手=拾取质量)
//   三层决策: 3步局部穷举 -> 全局BFS目标(NPC折价) -> 陈旧度探索
//   教训(139006-8): 拾取记账是负优化(视野自动纠错, 记账反而引入误差), 已移除;
//   镜像局先手率随 P50 直接翻转, 延迟就是收入(138955 外战同证)。
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

// ---------- 跨回合常驻世界模型(增量更新) ----------
struct World {
    int   known[N][N];    // 最后观测值(-3/-1/0/>=1)；FOG=从未见过
    short seen[N][N];     // 最后观测回合；-1=从未
    int   round;          // 当前回合
    int   last_round;
    int   last_vp;        // 上一回合买的视野档(决定本轮窗口半径)
    int   vp_spent;       // 本局视野累计花费(硬预算保险)

    World() { reset(); }  // 全局零初始化会让 seen=0 被当成"第0轮见过", 必须显式重置

    void reset() {
        for (int r = 0; r < N; ++r)
            for (int c = 0; c < N; ++c) { known[r][c] = FOG; seen[r][c] = -1; }
        last_round = -1;
        last_vp = 0;
        vp_spent = 0;
    }
    // 只扫两个视野窗口(默认5x5, 买视野后7x7/9x9), 不再全图两遍
    void update(const GameInput* in) {
        if (in->round <= last_round) reset();
        last_round = round = in->round;
        int rad = 2 + last_vp;
        for (int u = 0; u < 2; ++u) {
            int ur = in->my_units[u].row, uc = in->my_units[u].col;
            if (ur < 0 || ur >= N || uc < 0 || uc >= N) continue;
            int r0 = ur - rad < 0 ? 0 : ur - rad, r1 = ur + rad >= N ? N - 1 : ur + rad;
            int c0 = uc - rad < 0 ? 0 : uc - rad, c1 = uc + rad >= N ? N - 1 : uc + rad;
            for (int r = r0; r <= r1; ++r)
                for (int c = c0; c <= c1; ++c) {
                    int v = in->grid[r][c];
                    if (v == FOG) continue;
                    if (known[r][c] == OBSTACLE) continue;   // 障碍永久
                    known[r][c] = v;
                    seen[r][c] = (short)in->round;
                }
        }
    }
    // 估值后的金币量: 年龄衰减(NPC 会持续吃掉旧金币)
    inline int gold(int r, int c) const {
        int v = known[r][c];
        if (v < 1) return 0;
        int age = round - seen[r][c];
        return age < 30 ? v * (30 - age) / 30 : 0;
    }
    inline bool bomb(int r, int c) const {
        return known[r][c] == BOMB && round - seen[r][c] <= 20;  // 每20轮刷新,旧情报过期
    }
    inline bool wall(int r, int c) const { return known[r][c] == OBSTACLE; }
    inline int  age(int r, int c) const { return seen[r][c] < 0 ? 999 : round - seen[r][c]; }
};
World g_w;

// ---------- 稀疏的回合内标记(NPC数 / 他人占位), 用后清零 ----------
struct RoundMarks {
    signed char npcs[N][N] = {};   // 可见NPC数(踩踏判定)
    bool block[N][N] = {};         // 敌方角色 + 队友占位
    short mr[32], mc[32];
    int mn = 0;

    void mark_npc(int r, int c) { if (!npcs[r][c] && !block[r][c]) { mr[mn] = (short)r; mc[mn] = (short)c; ++mn; } ++npcs[r][c]; }
    void mark_block(int r, int c) { if (!npcs[r][c] && !block[r][c]) { mr[mn] = (short)r; mc[mn] = (short)c; ++mn; } block[r][c] = true; }
    void clear() { for (int i = 0; i < mn; ++i) { npcs[mr[i]][mc[i]] = 0; block[mr[i]][mc[i]] = false; } mn = 0; }
};
RoundMarks g_m;

inline bool passable(int r, int c) {
    return !g_w.wall(r, c) && !g_w.bomb(r, c) && !g_m.block[r][c];
}

// ---------- BFS(静态缓冲) ----------
struct Bfs {
    int  visit_tag[N][N] = {};
    int  cur_tag = 0;
    signed char prev_act[N][N];
    short qr[N * N], qc[N * N];
    int  qlen = 0, dist_[N][N];

    void run(int sr, int sc) {
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
                if (!passable(nr, nc)) continue;
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

// ---------- 局部 3 步穷举(7x7 预计算补丁, DFS 热路径只读小数组) ----------
struct LocalSearch {
    static constexpr int P = 7, R = 3;    // 补丁 7x7, 半径 3 = 步数上限
    int  pgold[P][P];
    bool pbomb[P][P], pblock[P][P], pnpc3[P][P];
    int best_score, best_acts[3];
    int unit_gold;

    void build(int sr, int sc) {
        for (int i = 0; i < P; ++i)
            for (int j = 0; j < P; ++j) {
                int r = sr - R + i, c = sc - R + j;
                if (r < 0 || r >= N || c < 0 || c >= N) {
                    pblock[i][j] = true; pgold[i][j] = 0;
                    pbomb[i][j] = pnpc3[i][j] = false;
                    continue;
                }
                pblock[i][j] = g_w.wall(r, c) || g_m.block[r][c];
                pgold[i][j] = g_w.gold(r, c);
                pbomb[i][j] = g_w.bomb(r, c);
                pnpc3[i][j] = g_m.npcs[r][c] >= 3;
            }
    }

    void dfs(int i, int j, int depth, int acts[3], int gained, int score) {
        if (score > best_score) {
            best_score = score;
            for (int t = 0; t < 3; ++t) best_acts[t] = t < depth ? acts[t] : STAY;
        }
        if (depth == 3) return;
        for (int a = 0; a < 4; ++a) {            // STAY 不增加收益, 由上面的前缀更新覆盖
            int ni = i + DR[a], nj = j + DC[a];
            if (pblock[ni >= 0 && ni < P ? ni : 0][nj >= 0 && nj < P ? nj : 0] ||
                ni < 0 || ni >= P || nj < 0 || nj >= P) continue;   // 撞墙=浪费步,剪掉
            acts[depth] = a;
            int undo_gold = 0, sc = score, gn = gained;
            bool undo_bomb = false;
            if (pgold[ni][nj] > 0) {
                int add = ceilPct(pgold[ni][nj], 65);
                pgold[ni][nj] -= add;
                undo_gold = add;
                gn += add; sc += add * 10;
            }
            // 惩罚 x2: 3 步视界看不到"绕一轮再拿"的替代方案, 补偿短视
            if (pbomb[ni][nj]) {
                sc -= ceilPct(unit_gold + gn, 10) * 20;
                pbomb[ni][nj] = false;
                undo_bomb = true;
            }
            if (pnpc3[ni][nj])
                sc -= ceilPct(unit_gold + gn, 5) * 20;
            dfs(ni, nj, depth + 1, acts, gn, sc);
            if (undo_gold) pgold[ni][nj] += undo_gold;
            if (undo_bomb) pbomb[ni][nj] = true;
        }
    }

    int run(int sr, int sc, int gold_now, int* acts_out) {
        build(sr, sc);
        best_score = 0;
        best_acts[0] = best_acts[1] = best_acts[2] = STAY;
        unit_gold = gold_now;
        int acts[3];
        dfs(R, R, 0, acts, 0, 0);
        for (int t = 0; t < 3; ++t) acts_out[t] = best_acts[t];
        return best_score;
    }
};
LocalSearch g_local;

// ---------- 全局目标(NPC 折价) ----------
bool globalTarget(const GameInput* in, int sr, int sc, bool claimed[N][N], int* out) {
    g_bfs.run(sr, sc);
    long best = 0;
    int br = -1, bc = -1;
    for (int i = 1; i < g_bfs.qlen; ++i) {
        int r = g_bfs.qr[i], c = g_bfs.qc[i];
        int v = g_w.gold(r, c);
        if (v <= 0 || claimed[r][c]) continue;
        int d = g_bfs.dist_[r][c];
        long val = v * 100L;
        // NPC 更近则大概率抢不过(3步/轮, ~75%趋金): 折到 1/3
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

// ---------- 探索(陈旧度驱动, 消灭罚站) ----------
void explore(int sr, int sc, bool claimed[N][N], int* out) {
    g_bfs.run(sr, sc);
    long best = -1;
    int br = -1, bc = -1;
    for (int i = 1; i < g_bfs.qlen; ++i) {
        int r = g_bfs.qr[i], c = g_bfs.qc[i];
        if (claimed[r][c]) continue;
        int age = g_w.age(r, c);
        if (age > 60) age = age > 900 ? 100 : 60;      // 从未见过=100
        long s = (long)age * 100;
        if (r >= 4 && r <= 12 && c >= 4 && c <= 12) s = s * 3 / 2;  // 中心每轮出金
        s /= g_bfs.dist_[r][c] + 1;
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
    g_w.update(in);
    g_m.clear();
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N) g_m.mark_block(r, c);
    }
    for (int i = 0; i < in->num_visible_npcs && i < MAX_NPCS; ++i) {
        int r = in->visible_npcs[i].pos.row, c = in->visible_npcs[i].pos.col;
        if (r >= 0 && r < N && c >= 0 && c < N) g_m.mark_npc(r, c);
    }

    GameOutput out = SAFE_OUT;
    bool claimed[N][N] = {};
    int stale7 = 0, cells7 = 0;   // 视野购买判据: 周边 7x7 情报陈旧度

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;

        // 只统计买视野真正新增的一圈(cheb距离=3): 5x5 内永远新鲜会稀释比例;
        // 已知障碍格不算陈旧(内容永不变, 重看无价值)
        for (int r = sr - 3; r <= sr + 3; ++r)
            for (int c = sc - 3; c <= sc + 3; ++c) {
                int dr = r > sr ? r - sr : sr - r, dc = c > sc ? c - sc : sc - c;
                if ((dr == 3 || dc == 3) && r >= 0 && r < N && c >= 0 && c < N
                    && !g_w.wall(r, c)) {
                    ++cells7;
                    if (g_w.age(r, c) > 8) ++stale7;
                }
            }

        // 队友当前格占位(自撞=浪费步)
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        if (tr >= 0 && tr < N && tc >= 0 && tc < N) g_m.mark_block(tr, tc);

        int gain = g_local.run(sr, sc, in->my_units_gold[u], acts);
        if (gain > 0) {
            // 途经格标记认领, 免得队友重复扑; 不做记账(视野下一轮自动纠错)
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int nr = r + DR[acts[i]], nc = c + DC[acts[i]];
                if (acts[i] != STAY && nr >= 0 && nr < N && nc >= 0 && nc < N &&
                    !g_w.wall(nr, nc) && !g_m.block[nr][nc]) {
                    r = nr; c = nc;
                    claimed[r][c] = true;
                }
            }
        } else if (!globalTarget(in, sr, sc, claimed, acts)) {
            explore(sr, sc, claimed, acts);
        }
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    // P4 视野购买: 周边情报过半陈旧时买 7x7(2金币, 只管下一回合)
    // 官方强 bot 实证 ~77 轮/187 金花费是净赚(§6.2b)
    // 硬预算 250 金(官方强 bot 实证花 187 是净赚量级, 超限说明规则失控)
    out.vp = (cells7 > 0 && stale7 * 2 >= cells7 && in->round < 490
              && g_w.vp_spent < 250) ? 1 : 0;
    g_w.vp_spent += out.vp * 2;
    g_w.last_vp = out.vp;
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
