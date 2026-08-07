// player.cpp — GoldRush 2.0 主战策略
//
// cpp9 = cpp8 + 两段式BFS(9步无目标则全图, 恢复外圈大堆远征) + 行军目标衰减校验。
//        教训(139155-58): 镜像局赢的是相对抢夺, 不是绝对效率; 评估改用固定基线+外战。
// cpp8 = cpp7 + 尾步填充(把尾部STAY贪心替换为进入相邻金格; 实证16%的步在罚站,
//        其中大头是吃完堆/到达目标后的收尾步)。
// cpp7 = cpp6 + NPC竞速修正/目标簇加成/无金补丁跳过DFS。
// cpp6 (P5 计划缓存)：探针实测(139118)推翻冷缓存论——平台纯返回 30ns、
//   世界模型更新+补丁读 110ns。延迟大头是"赶路回合"的全图 BFS+扫描(每轮 2 次)。
//   因此: 远程目标的 BFS 路径缓存为多轮计划, 之后每轮弹 3 步 + 补丁廉价校验,
//   仅在 计划失效/走完/过期/本地有收益 时重规划 -> BFS 频率 ~1/5-1/10,
//   P50 直奔亚微秒(这也是榜首 230ns P90 的形态: 重计算只占少数回合)。
//   数据结构沿用 cpp5 压位(世界 4B/格, BFS 压位, 小列表标记)。
//
// 结构分层（新策略只动"策略层"，边界层不要碰）：
//   moveDecision()  ← extern "C" 边界层：兜底 + sanitize
//     └─ decide()   ← 策略层
//
// 编译：开发机(8.153.76.120) make 出 player.so 提交；本机只 make check / make local。
#include <cstdint>
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int FOG = -5, BOMB = -3, OBSTACLE = -1;

constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

inline int ceilPct(int v, int pct) { return (v * pct + 99) / 100; }

// ---------- 跨回合常驻世界模型(打包 4B/格, 全图 1156B) ----------
struct Cell {
    int8_t   known;   // 最后观测值(-5雾 -3炸弹 -1障碍 0空 1..127金)
    uint16_t seen;    // 最后观测回合+1；0=从未见过
};
struct World {
    Cell  cell[N][N];
    int   round;
    int   last_round;
    int   last_vp;
    int   vp_spent;

    World() { reset(); }

    void reset();     // 定义在 g_plan 之后(需要清计划)
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
                    if (cell[r][c].known == OBSTACLE) continue;   // 障碍永久
                    cell[r][c].known = (int8_t)(v > 127 ? 127 : v);
                    cell[r][c].seen = (uint16_t)(in->round + 1);
                }
        }
    }
    inline int age(const Cell& x) const { return x.seen ? round - (int)(x.seen - 1) : 999; }
    inline int gold(int r, int c) const {
        const Cell& x = cell[r][c];
        if (x.known < 1) return 0;
        int a = age(x);
        return a < 30 ? x.known * (30 - a) / 30 : 0;
    }
    inline bool bomb(int r, int c) const {
        const Cell& x = cell[r][c];
        return x.known == BOMB && age(x) <= 20;
    }
    inline bool wall(int r, int c) const { return cell[r][c].known == OBSTACLE; }
};
World g_w;

// ---------- 回合内小列表(寄存器/单 cache line 级, 无大数组清零) ----------
struct Marks {
    int8_t br[8], bc[8]; int bn;      // 占位: 敌人+队友
    int8_t nr[8], nc[8]; int nn;      // 可见 NPC 位置(可重叠)
    int8_t cr[8], cc[8]; int cn;      // 已认领目标

    inline bool blocked(int r, int c) const {
        for (int i = 0; i < bn; ++i) if (br[i] == r && bc[i] == c) return true;
        return false;
    }
    inline int npcs(int r, int c) const {
        int k = 0;
        for (int i = 0; i < nn; ++i) k += (nr[i] == r && nc[i] == c);
        return k;
    }
    inline bool claimed(int r, int c) const {
        for (int i = 0; i < cn; ++i) if (cr[i] == r && cc[i] == c) return true;
        return false;
    }
    inline void claim(int r, int c) { if (cn < 8) { cr[cn] = (int8_t)r; cc[cn] = (int8_t)c; ++cn; } }
};
Marks g_m;

inline bool passable(int r, int c) {
    return !g_w.wall(r, c) && !g_w.bomb(r, c) && !g_m.blocked(r, c);
}

// ---------- BFS(压位, 仅回退路径时触碰) ----------
struct Bfs {
    uint16_t visit[N * N] = {};
    uint16_t epoch = 0;
    int8_t  prev[N * N];
    uint8_t dist[N * N];
    uint16_t q[N * N];
    int qlen = 0;

    // maxd: 距离上限(默认9) — 更远的目标经 年龄衰减+距离折价 后无竞争力, 扫描减半
    void run(int sr, int sc, int maxd = 9) {
        if (++epoch == 0) { for (int i = 0; i < N * N; ++i) visit[i] = 0; epoch = 1; }
        qlen = 0;
        int s = sr * N + sc;
        q[qlen++] = (uint16_t)s;
        visit[s] = epoch;
        prev[s] = -1;
        dist[s] = 0;
        for (int head = 0; head < qlen; ++head) {
            int idx = q[head], r = idx / N, c = idx % N;
            if (dist[idx] >= maxd) continue;
            for (int a = 0; a < 4; ++a) {
                int nrr = r + DR[a], ncc = c + DC[a];
                if (nrr < 0 || nrr >= N || ncc < 0 || ncc >= N) continue;
                int ni = nrr * N + ncc;
                if (visit[ni] == epoch) continue;
                if (!passable(nrr, ncc)) continue;
                visit[ni] = epoch;
                prev[ni] = (int8_t)a;
                dist[ni] = (uint8_t)(dist[idx] + 1);
                q[qlen++] = (uint16_t)ni;
            }
        }
    }
    int pathTo(int sr, int sc, int tr, int tc, int* out, int cap) const {
        int s = sr * N + sc, t = tr * N + tc;
        if (visit[t] != epoch) return 0;
        int tmp[64];
        int len = 0;
        while (t != s && len < 64) {
            int a = prev[t];
            if (a < 0) break;
            tmp[len++] = a;
            t -= DR[a] * N + DC[a];
        }
        int n = len < cap ? len : cap;
        for (int i = 0; i < n; ++i) out[i] = tmp[len - 1 - i];
        return n;
    }
};
Bfs g_bfs;

// ---------- 局部 3 步穷举(7x7 补丁) + 视野环陈旧度顺路统计 ----------
struct LocalSearch {
    static constexpr int P = 7, R = 3;
    int8_t pgold[P][P];
    int8_t pflag[P][P];               // bit0=block bit1=bomb bit2=npc>=3
    int best_score, best_acts[3];
    int unit_gold;
    int ring_stale, ring_cells;       // 补丁边界(cheb=3) = 买视野新增的环
    bool has_gold;                    // 补丁内有金才值得跑 DFS

    void build(int sr, int sc) {
        ring_stale = ring_cells = 0;
        has_gold = false;
        for (int i = 0; i < P; ++i)
            for (int j = 0; j < P; ++j) {
                int r = sr - R + i, c = sc - R + j;
                if (r < 0 || r >= N || c < 0 || c >= N) {
                    pflag[i][j] = 1; pgold[i][j] = 0;
                    continue;
                }
                const Cell& x = g_w.cell[r][c];
                int a = g_w.age(x);
                int8_t f = 0;
                if (x.known == OBSTACLE || g_m.blocked(r, c)) f |= 1;
                if (x.known == BOMB && a <= 20) f |= 2;
                if (g_m.npcs(r, c) >= 3) f |= 4;
                pflag[i][j] = f;
                pgold[i][j] = (int8_t)(x.known >= 1 && a < 30 ? x.known * (30 - a) / 30 : 0);
                if (pgold[i][j] > 0) has_gold = true;
                if ((i == 0 || i == P - 1 || j == 0 || j == P - 1) && x.known != OBSTACLE) {
                    ++ring_cells;
                    if (a > 8) ++ring_stale;
                }
            }
    }

    void dfs(int i, int j, int depth, int acts[3], int gained, int score) {
        if (score > best_score) {
            best_score = score;
            for (int t = 0; t < 3; ++t) best_acts[t] = t < depth ? acts[t] : STAY;
        }
        if (depth == 3) return;
        for (int a = 0; a < 4; ++a) {
            int ni = i + DR[a], nj = j + DC[a];
            if (ni < 0 || ni >= P || nj < 0 || nj >= P || (pflag[ni][nj] & 1)) continue;
            acts[depth] = a;
            int undo_gold = 0, sc = score, gn = gained;
            bool undo_bomb = false;
            if (pgold[ni][nj] > 0) {
                int add = ceilPct(pgold[ni][nj], 65);
                pgold[ni][nj] -= (int8_t)add;
                undo_gold = add;
                gn += add; sc += add * 10;
            }
            // 惩罚 x2: 3 步视界看不到"绕一轮再拿"的替代方案, 补偿短视
            if (pflag[ni][nj] & 2) {
                sc -= ceilPct(unit_gold + gn, 10) * 20;
                pflag[ni][nj] &= (int8_t)~2;
                undo_bomb = true;
            }
            if (pflag[ni][nj] & 4)
                sc -= ceilPct(unit_gold + gn, 5) * 20;
            dfs(ni, nj, depth + 1, acts, gn, sc);
            if (undo_gold) pgold[ni][nj] += (int8_t)undo_gold;
            if (undo_bomb) pflag[ni][nj] |= 2;
        }
    }

    int run(int sr, int sc, int gold_now, int* acts_out) {
        build(sr, sc);
        best_score = 0;
        best_acts[0] = best_acts[1] = best_acts[2] = STAY;
        acts_out[0] = acts_out[1] = acts_out[2] = STAY;
        if (!has_gold) return 0;              // 赶路回合快速通道
        unit_gold = gold_now;
        int acts[3];
        dfs(R, R, 0, acts, 0, 0);
        for (int t = 0; t < 3; ++t) acts_out[t] = best_acts[t];
        return best_score;
    }
};
LocalSearch g_local;

// ---------- 多轮计划缓存(砍掉赶路回合的 BFS) ----------
struct Plan {
    int8_t acts[64];      // 完整路径动作
    int8_t len, pos;      // 总长/已执行
    int8_t er, ec;        // 当前应在位置(校验用; 不符=发生过碰撞, 作废)
    int8_t tr, tc;        // 目标格
    int8_t is_gold;       // 1=金币目标(目标空了要作废) 0=探索目标
    uint16_t born;        // 制定回合(过期重规划)
};
Plan g_plan[2];

// 从缓存计划弹出至多 3 步; 校验通过返回 true 并填 acts
bool followPlan(int u, int sr, int sc, int* out) {
    Plan& p = g_plan[u];
    if (p.len == 0 || p.pos >= p.len) return false;
    if (p.er != sr || p.ec != sc) { p.len = 0; return false; }      // 碰撞过, 作废
    if (g_w.round - (int)p.born > 10) { p.len = 0; return false; }  // 过期
    if (p.is_gold && g_w.gold(p.tr, p.tc) == 0) { p.len = 0; return false; }  // 目标已空/太旧
    int r = sr, c = sc, n = 0;
    int tmp[3];
    while (n < 3 && p.pos + n < p.len) {
        int a = p.acts[p.pos + n];
        int nr = r + DR[a], nc = c + DC[a];
        if (nr < 0 || nr >= N || nc < 0 || nc >= N ||
            g_w.wall(nr, nc) || g_w.bomb(nr, nc) || g_m.blocked(nr, nc)) {
            p.len = 0; return false;                                 // 路上有新障碍, 作废
        }
        tmp[n++] = a;
        r = nr; c = nc;
    }
    if (n == 0) { p.len = 0; return false; }
    for (int i = 0; i < 3; ++i) out[i] = i < n ? tmp[i] : STAY;
    p.pos = (int8_t)(p.pos + n);
    p.er = (int8_t)r; p.ec = (int8_t)c;
    g_m.claim(p.tr, p.tc);                                           // 维持目标去重
    return true;
}

void storePlan(int u, int sr, int sc, int tr, int tc, bool is_gold) {
    Plan& p = g_plan[u];
    int full[64];
    int len = g_bfs.pathTo(sr, sc, tr, tc, full, 64);
    int n = len < 64 ? len : 64;
    for (int i = 0; i < n; ++i) p.acts[i] = (int8_t)full[i];
    p.len = (int8_t)n;
    p.pos = 0;
    p.er = (int8_t)sr; p.ec = (int8_t)sc;
    p.tr = (int8_t)tr; p.tc = (int8_t)tc;
    p.is_gold = (int8_t)is_gold;
    p.born = (uint16_t)g_w.round;
}

// ---------- 全局目标(NPC 折价) ----------
bool globalTarget(const GameInput* in, int sr, int sc, int* out, int* otr, int* otc) {
    long best = 0;
    int bi = -1;
    // 两段式: 先 9 步(常见情形, 便宜); 无目标再全图(恢复外圈大堆远征, 摊薄进计划缓存)
    for (int stage = 0; stage < 2 && bi < 0; ++stage) {
    g_bfs.run(sr, sc, stage == 0 ? 9 : 32);
    for (int h = 1; h < g_bfs.qlen; ++h) {
        int idx = g_bfs.q[h], r = idx / N, c = idx % N;
        int v = g_w.gold(r, c);
        if (v <= 0 || g_m.claimed(r, c)) continue;
        int d = g_bfs.dist[idx];
        // 簇加成: 目标四邻的金币折半计入(富矿区优先于孤立小堆)
        long val = v * 100L;
        {
            int nb = 0;
            for (int a2 = 0; a2 < 4; ++a2) {
                int rr = r + DR[a2], cc2 = c + DC[a2];
                if (rr >= 0 && rr < N && cc2 >= 0 && cc2 < N) nb += g_w.gold(rr, cc2);
            }
            val += nb * 50L;
        }
        // NPC竞速: 我方比对手快时执行序=我方->NPC->对方, 平距离我们赢;
        // NPC 近 2 步以上才折价(实证 NPC 趋金率仅~75%, 平手抢得过)
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nrr = in->visible_npcs[j].pos.row, ncc = in->visible_npcs[j].pos.col;
            if (nrr < 0) continue;
            int nd = (nrr > r ? nrr - r : r - nrr) + (ncc > c ? ncc - c : c - ncc);
            if (nd + 2 < d) { val /= 3; break; }
        }
        long score = val / (d + 1);
        if (score > best) { best = score; bi = idx; }
    }
    }
    if (bi < 0) return false;
    g_m.claim(bi / N, bi % N);
    for (int i = 0; i < 3; ++i) out[i] = STAY;
    g_bfs.pathTo(sr, sc, bi / N, bi % N, out, 3);
    *otr = bi / N; *otc = bi % N;
    return true;
}

// ---------- 探索(陈旧度驱动; 复用 globalTarget 刚跑的 BFS) ----------
void explore(int sr, int sc, int* out, int* otr, int* otc) {
    long best = -1;
    int bi = -1;
    *otr = -1; *otc = -1;
    // 两遍: 先只考虑距离>=4的边疆(计划够长, BFS摊薄到多轮); 无候选再放开
    for (int pass = 0; pass < 2 && bi < 0; ++pass) {
        for (int h = 1; h < g_bfs.qlen; ++h) {
            int idx = g_bfs.q[h], r = idx / N, c = idx % N;
            if (pass == 0 && g_bfs.dist[idx] < 4) continue;
            if (g_m.claimed(r, c)) continue;
            int a = g_w.age(g_w.cell[r][c]);
            if (a > 60) a = a > 900 ? 100 : 60;
            long s = (long)a * 100;
            if (r >= 4 && r <= 12 && c >= 4 && c <= 12) s = s * 3 / 2;
            s /= g_bfs.dist[idx] + 1;
            if (s > best) { best = s; bi = idx; }
        }
    }
    for (int i = 0; i < 3; ++i) out[i] = STAY;
    if (bi >= 0) {
        g_m.claim(bi / N, bi % N);
        g_bfs.pathTo(sr, sc, bi / N, bi % N, out, 3);
        *otr = bi / N; *otc = bi % N;
    }
}

// ---------- 策略层 ----------
void World::reset() {
    for (int r = 0; r < N; ++r)
        for (int c = 0; c < N; ++c) cell[r][c] = {(int8_t)FOG, 0};
    last_round = -1;
    last_vp = 0;
    vp_spent = 0;
    g_plan[0].len = g_plan[1].len = 0;
}

GameOutput decide(const GameInput* in) {
    g_w.update(in);
    g_m.bn = g_m.nn = g_m.cn = 0;
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N && g_m.bn < 8) {
            g_m.br[g_m.bn] = (int8_t)r; g_m.bc[g_m.bn] = (int8_t)c; ++g_m.bn;
        }
    }
    for (int i = 0; i < in->num_visible_npcs && i < MAX_NPCS; ++i) {
        int r = in->visible_npcs[i].pos.row, c = in->visible_npcs[i].pos.col;
        if (r >= 0 && r < N && c >= 0 && c < N && g_m.nn < 8) {
            g_m.nr[g_m.nn] = (int8_t)r; g_m.nc[g_m.nn] = (int8_t)c; ++g_m.nn;
        }
    }

    GameOutput out = SAFE_OUT;
    int stale = 0, cells = 0;

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;

        // 队友占位(自撞=浪费步)
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        int saved_bn = g_m.bn;
        if (tr >= 0 && tr < N && tc >= 0 && tc < N && g_m.bn < 8) {
            g_m.br[g_m.bn] = (int8_t)tr; g_m.bc[g_m.bn] = (int8_t)tc; ++g_m.bn;
        }

        int gain = g_local.run(sr, sc, in->my_units_gold[u], acts);
        stale += g_local.ring_stale;
        cells += g_local.ring_cells;
        if (gain > 0) {
            g_plan[u].len = 0;                    // 就地开采, 旧行程作废
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                if (acts[i] != STAY && nrr >= 0 && nrr < N && ncc >= 0 && ncc < N &&
                    !g_w.wall(nrr, ncc) && !g_m.blocked(nrr, ncc)) {
                    r = nrr; c = ncc;
                    g_m.claim(r, c);
                }
            }
        } else if (!followPlan(u, sr, sc, acts)) {  // 廉价路径: 无 BFS
            int ttr = -1, ttc = -1;
            if (globalTarget(in, sr, sc, acts, &ttr, &ttc)) {
                storePlan(u, sr, sc, ttr, ttc, true);
                g_plan[u].pos = 3 <= g_plan[u].len ? 3 : g_plan[u].len;  // 本轮已执行前3步
                { int r = sr, c = sc;
                  for (int i = 0; i < g_plan[u].pos; ++i) { r += DR[(int)g_plan[u].acts[i]]; c += DC[(int)g_plan[u].acts[i]]; }
                  g_plan[u].er = (int8_t)r; g_plan[u].ec = (int8_t)c; }
            } else {
                explore(sr, sc, acts, &ttr, &ttc);
                if (ttr >= 0) {
                    storePlan(u, sr, sc, ttr, ttc, false);
                    g_plan[u].pos = 3 <= g_plan[u].len ? 3 : g_plan[u].len;
                    int r = sr, c = sc;
                    for (int i = 0; i < g_plan[u].pos; ++i) { r += DR[(int)g_plan[u].acts[i]]; c += DC[(int)g_plan[u].acts[i]]; }
                    g_plan[u].er = (int8_t)r; g_plan[u].ec = (int8_t)c;
                }
            }
        }
        // 尾步填充: 尾部 STAY 若有相邻金格(未认领), 改为进入它
        {
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                if (acts[i] == STAY) {
                    int besta = -1, bestv = 0;
                    for (int a2 = 0; a2 < 4; ++a2) {
                        int rr = r + DR[a2], cc2 = c + DC[a2];
                        if (rr < 0 || rr >= N || cc2 < 0 || cc2 >= N) continue;
                        if (g_w.wall(rr, cc2) || g_w.bomb(rr, cc2) || g_m.blocked(rr, cc2)) continue;
                        if (g_m.npcs(rr, cc2) >= 3) continue;
                        int v = g_w.gold(rr, cc2);
                        if (v > bestv && !g_m.claimed(rr, cc2)) { bestv = v; besta = a2; }
                    }
                    if (besta >= 0) {
                        acts[i] = besta;
                        g_plan[u].len = 0;      // 偏离计划, 作废防错位
                    }
                }
                int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                if (acts[i] != STAY && nrr >= 0 && nrr < N && ncc >= 0 && ncc < N &&
                    !g_w.wall(nrr, ncc) && !g_m.blocked(nrr, ncc)) {
                    r = nrr; c = ncc;
                    if (g_w.gold(r, c) > 0) g_m.claim(r, c);
                }
            }
        }
        g_m.bn = saved_bn;
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    // P4 视野购买: 环情报过半陈旧时买 7x7; 硬预算 250 金(官方强 bot 实证 187 净赚)
    out.vp = (cells > 0 && stale * 2 >= cells && in->round < 490
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
