// player.cpp — GoldRush 2.0 主战策略
//
// cpp22 = cpp21 + 金币计数分派: 窗口0金->计划(0.5μs), 恰1金->专用直取+振荡(0.7μs),
//         >=2金才全量DFS(2.5μs) -> P50 目标减半(DFS 336次逐边检查是主成本)。
// cpp21 = cpp20 策略全保留 + 局部穷举改"直读输入"(免补丁构建, 省~1μs) + 砍视野购买。
//         目标: P50 3.7 -> ~1.5μs, 策略无损(3步全枚举语义等价)。
// cpp20 = cpp18 + 争抢章 60->30 轮(A/B 2814>2734); EXPRESS 触发率过低已弃用。
// cpp19 = cpp18 + EXPRESS 快车道: 有有效计划且输入窗口无金时, 跳过补丁+DFS(省1.4μs)
//         直接走缓存计划 -> 赶路回合 ~0.6μs, P50 目标 <2.6μs(凑企鹅线) 且策略无损。
// cpp18 = cpp15 + 刷新热度图(默认开, A/B 基线 2734 vs 2597 且方差骤降)。
// 开关: OPENING=开局冲刺(A/B 均值持平方差巨大, 默认关); ROTATION/TERRITORY(已否决)。
// cpp17 = cpp15 + 轮作制(已被 A/B 否决, 默认关): 外圈(NPC罕至)金币记忆衰减 30->60 轮, 存量堆保持可作目标
//         + 双角色软分区(按出生对角线各守半场, 越界目标估值 x0.8) 减少重复覆盖。
//         依据: #1/#4 后期收入反增(存量轮巡收割), NPC 常驻中心不吃外圈(§6.2b)。
// cpp15 = cpp14 + 避让加码(盖章半径3/时长60/罚3倍) + 争抢区残渣过滤(<3金不去)。
// cpp14 = cpp13 + 导向目标缓存(is_gold=2 表示导向模式, 失效才重扫列表)
//         + 金币列表扫描时顺手 swap-remove 空条目 -> 消灭每轮全列表重扫。
// cpp13 = cpp12 + 争抢热度图(目标被人吃空/目击敌人 -> 周边盖章40轮, 选目标折半)
//         + 探索去中心化(中心是NPC+快手主场, 后手方正确活法=收割外圈存量;
//           实证: P^GPT 43μs全程后手仍 2332 分)。
// cpp12 = cpp11 + 金币目标列表(世界模型增量维护) + 曼哈顿选目标 + 导向走位,
//         BFS 仅在 导向卡死/探索 时兜底 -> 砍掉大部分重规划回合的 BFS。
// cpp11 = cpp10 + NPC竞速回滚保守版(消融)。
// cpp10 = cpp9 + 补丁构建盖章化(先填地形, NPC/占位列表各扫一遍盖上去,
//         省 ~500 次/轮 的逐格线性扫; 环统计移出主循环) -> 压 P50 过 2.7μs 线。
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
    int8_t   listed;  // 已在金币目标列表中
    uint16_t seen;    // 最后观测回合+1；0=从未见过
};
struct World {
    Cell  cell[N][N];
    int   round;
    int   last_round;
    int   last_vp;
    int   vp_spent;
    int   wgold_n[2];          // 各单位 5x5 窗口内金币格数
    int   wgold_r[2], wgold_c[2];  // 恰 1 金时的坐标
    // 金币目标列表(懒惰失效: 取用时校验 gold()>0, 满了就压缩)
    uint16_t glist[96];
    int   gn;
    uint16_t contested[N][N];   // 该格附近有竞争者的有效期(回合号)
    int8_t split_axis;          // 软分区轴: 0 = r+c-16, 1 = c-r (出生时检测)
    int8_t half_sign[2];        // 各角色守的半场符号
    uint8_t yield_[N][N];       // 观测到的金币刷新热度(SPAWNMAP)

    inline bool isContested(int r, int c) const { return (uint16_t)round < contested[r][c]; }
#ifdef OPPTRACK
    void opp_evidence(const GameInput* in, int r, int c) {
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nr = in->visible_npcs[j].pos.row, nc = in->visible_npcs[j].pos.col;
            if (nr < 0) continue;
            int d = (nr > r ? nr - r : r - nr) + (nc > c ? nc - c : c - nc);
            if (d <= 3) return;               // 可能是 NPC 吃的, 不算证据
        }
        stampContested(r, c);
    }
#endif
    void stampContested(int cr0, int cc0) {
#ifdef CONTEST60
        constexpr int DUR = 60;
#else
        constexpr int DUR = 30;   // A/B: 30 均 2814 > 60 均 2734
#endif
        for (int r = cr0 - 3 <= 0 ? 0 : cr0 - 3; r <= (cr0 + 3 >= N ? N - 1 : cr0 + 3); ++r)
            for (int c = cc0 - 3 <= 0 ? 0 : cc0 - 3; c <= (cc0 + 3 >= N ? N - 1 : cc0 + 3); ++c)
                contested[r][c] = (uint16_t)(round + DUR);
    }

    void compactList() {
        int w = 0;
        for (int i = 0; i < gn; ++i) {
            int r = glist[i] / N, c = glist[i] % N;
            if (cell[r][c].known >= 1) glist[w++] = glist[i];
            else cell[r][c].listed = 0;
        }
        gn = w;
    }

    World() { reset(); }

    void reset();     // 定义在 g_plan 之后(需要清计划)
    void update(const GameInput* in) {
        if (in->round <= last_round) reset();
        if (last_round < 0) {                     // 开局: 按出生位置定分区轴
            int r0 = in->my_units[0].row, c0 = in->my_units[0].col;
            int v0 = r0 + c0 - 16, v1 = c0 - r0;
            split_axis = (v0 < 0 ? -v0 : v0) >= (v1 < 0 ? -v1 : v1) ? 0 : 1;
            for (int u = 0; u < 2; ++u) {
                int rr = in->my_units[u].row, cc = in->my_units[u].col;
                int av = split_axis == 0 ? rr + cc - 16 : cc - rr;
                half_sign[u] = (int8_t)(av >= 0 ? 1 : -1);
            }
            if (half_sign[0] == half_sign[1]) half_sign[1] = (int8_t)-half_sign[0];
        }
        last_round = round = in->round;
        int rad = 2 + last_vp;
        for (int u = 0; u < 2; ++u) {
            wgold_n[u] = 0;
            int ur = in->my_units[u].row, uc = in->my_units[u].col;
            if (ur < 0 || ur >= N || uc < 0 || uc >= N) continue;
            int r0 = ur - rad < 0 ? 0 : ur - rad, r1 = ur + rad >= N ? N - 1 : ur + rad;
            int c0 = uc - rad < 0 ? 0 : uc - rad, c1 = uc + rad >= N ? N - 1 : uc + rad;
            for (int r = r0; r <= r1; ++r)
                for (int c = c0; c <= c1; ++c) {
                    int v = in->grid[r][c];
                    if (v == FOG) continue;
                    if (cell[r][c].known == OBSTACLE) continue;   // 障碍永久
                    // 可见格金币比记忆多 = 刷新事件(拾取只会变少); A/B: 2734 vs 2597
                    if (v >= 1 && cell[r][c].known >= 0 && v > cell[r][c].known &&
                        yield_[r][c] < 250) yield_[r][c] += 2;
#ifdef OPPTRACK
                    // 记忆≥3金突然清零且附近无可见NPC = 对手来过, 实时盖争抢章
                    if (v == 0 && cell[r][c].known >= 3) opp_evidence(in, r, c);
#endif
                    cell[r][c].known = (int8_t)(v > 127 ? 127 : v);
                    cell[r][c].seen = (uint16_t)(in->round + 1);
                    if (v >= 1) {
                        if (++wgold_n[u] == 1) { wgold_r[u] = r; wgold_c[u] = c; }
                        if (!cell[r][c].listed) {
                            if (gn >= 96) compactList();
                            if (gn < 96) { glist[gn++] = (uint16_t)(r * N + c); cell[r][c].listed = 1; }
                        }
                    }
                }
        }
    }
    inline int age(const Cell& x) const { return x.seen ? round - (int)(x.seen - 1) : 999; }
    inline int gold(int r, int c) const {
        const Cell& x = cell[r][c];
        if (x.known < 1) return 0;
        int a = age(x);
#ifdef ROTATION
        // 外圈 NPC 罕至, 存量堆衰减慢一倍(轮作收割的基础)
        int cr = r > 8 ? r - 8 : 8 - r, cc = c > 8 ? c - 8 : 8 - c;
        int T = (cr >= 6 || cc >= 6) ? 60 : 30;
#elif defined(T45)
        int T = 45;
#else
        int T = 30;
#endif
        return a < T ? x.known * (T - a) / T : 0;
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

// (LocalSearch 补丁版已由 MiniLocal 直读版取代, 旧实现见 git 历史)

// ---------- cpp21: 直读输入的 3 步穷举(免补丁构建) ----------
struct MiniLocal {
    const GameInput* in;
    int unit_gold;
    int ov_idx[8], ov_left[8], ovn;    // 本路径消耗覆盖(undo)
    int best; int bacts[3];

    inline int cellGold(int r, int c) const {
        for (int t = 0; t < ovn; ++t)
            if (ov_idx[t] == r * N + c) return ov_left[t];
        int iv = in->grid[r][c];
        if (iv >= 1) return iv;                    // 视野内: 精确值
        if (iv == FOG) return g_w.gold(r, c);      // 视野外: 记忆估值
        return 0;
    }
    inline bool cellBomb(int r, int c) const {
        int iv = in->grid[r][c];
        return iv == BOMB || (iv == FOG && g_w.bomb(r, c));
    }
    inline bool cellBlocked(int r, int c) const {
        return in->grid[r][c] == OBSTACLE || g_w.wall(r, c) || g_m.blocked(r, c);
    }

    void dfs(int r, int c, int depth, int acts[3], int sc) {
        if (sc > best) {
            best = sc;
            for (int t = 0; t < 3; ++t) bacts[t] = t < depth ? acts[t] : STAY;
        }
        if (depth == 3) return;
        for (int a = 0; a < 4; ++a) {
            int nr = r + DR[a], nc = c + DC[a];
            if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
            if (cellBlocked(nr, nc)) continue;
            if (g_m.npcs(nr, nc) >= 3) continue;   // 踩踏格禁走(罕见)
            acts[depth] = a;
            int add = 0, undo = -1, undov = 0;
            int v = cellGold(nr, nc);
            if (v > 0) {
                int take = ceilPct(v, 65);
                add += take * 10;
                bool found = false;
                for (int t = 0; t < ovn; ++t)
                    if (ov_idx[t] == nr * N + nc) { undo = t; undov = ov_left[t]; ov_left[t] = v - take; found = true; break; }
                if (!found && ovn < 8) { ov_idx[ovn] = nr * N + nc; ov_left[ovn] = v - take; undo = ovn; undov = -12345; ++ovn; }
            }
            // 惩罚 x2: 3 步视界看不到"绕一轮再拿", 补偿短视
            if (cellBomb(nr, nc)) add -= ceilPct(unit_gold, 10) * 20;
            dfs(nr, nc, depth + 1, acts, sc + add);
            if (undo >= 0) {
                if (undov == -12345) --ovn;
                else ov_left[undo] = undov;
            }
        }
    }
    int run(const GameInput* in_, int sr, int sc, int gold_now, int* acts_out) {
        in = in_;
        unit_gold = gold_now;
        ovn = 0;
        best = 0;
        bacts[0] = bacts[1] = bacts[2] = STAY;
        int tmp[3];
        dfs(sr, sc, 0, tmp, 0);
        for (int t = 0; t < 3; ++t) acts_out[t] = bacts[t];
        return best;
    }
};
MiniLocal g_mini;

int steerStep(int r, int c, int tr, int tc);   // 前置声明(定义在后)

// 恰 1 金专用: 直取 + d==1 时 enter-leave-enter 双吃; 返回等效收益(>0 即农耕)
int singleGold(const GameInput* in, int sr, int sc, int gr, int gc, int* acts) {
    acts[0] = acts[1] = acts[2] = STAY;
    int v = in->grid[gr][gc];
    if (v < 1) return 0;
    int d = (gr > sr ? gr - sr : sr - gr) + (gc > sc ? gc - sc : sc - gc);
    if (d == 0) {                                   // 站在金上: 吃不到, 离开再回来
        for (int a = 0; a < 4; ++a) {
            int nr = sr + DR[a], nc = sc + DC[a];
            if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
            if (!passable(nr, nc) || g_m.npcs(nr, nc) >= 3) continue;
            acts[0] = a;
            acts[1] = a ^ 1;                        // 反方向(0<->1, 2<->3)回来吃
            return ceilPct(v, 65);
        }
        return 0;
    }
    int r = sr, c = sc, n = 0;
    while (n < 3 && !(r == gr && c == gc)) {
        int a = steerStep(r, c, gr, gc);
        if (a < 0) return 0;                        // 被挡: 交给通用机器
        acts[n++] = a;
        r += DR[a]; c += DC[a];
    }
    if (!(r == gr && c == gc)) return 1;            // 3步没到: 也算在途(继续走)
    int gain = ceilPct(v, 65);
    if (n <= 1) {                                    // d==1: 还剩>=2步, 出来再进去双吃
        int back = acts[n - 1] ^ 1;
        int rem = v - gain;
        if (rem > 0 && n + 1 < 3) {
            acts[n] = back;
            acts[n + 1] = back ^ 1;
            gain += ceilPct(rem, 65);
        }
    } else if (n == 2) {
        acts[2] = acts[1] ^ 1;                       // 到达后退一步, 备下轮再进
    }
    return gain;
}

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

int steerStep(int r, int c, int tr, int tc);
#ifdef OPENING
// 开局冲刺: 中心 9x9 每轮刷金, 先到先吃; 目标 = 中心区靠己方侧的入口点
bool openingSprint(int u, int sr, int sc, int* out) {
    int tr = sr <= 8 ? 5 : 11, tc = sc <= 8 ? 5 : 11;   // 靠自己侧的中心区入口
    if (u == 1) { tc = sc <= 8 ? 6 : 10; }              // 两角色错开一列
    int r = sr, c = sc, n = 0;
    out[0] = out[1] = out[2] = STAY;
    while (n < 3 && !(r == tr && c == tc)) {
        int a = steerStep(r, c, tr, tc);
        if (a < 0) break;
        out[n++] = a;
        r += DR[a]; c += DC[a];
    }
    return n > 0;
}
#endif
bool steerCached(int u, int sr, int sc, int* out) {
    Plan& p = g_plan[u];
    if (p.is_gold != 2) return false;
    if (g_w.round - (int)p.born > 12 || g_w.gold(p.tr, p.tc) <= 0 ||
        g_w.isContested(p.tr, p.tc)) {
        if (g_w.gold(p.tr, p.tc) <= 0 && g_w.round - (int)p.born <= 12)
            g_w.stampContested(p.tr, p.tc);      // 到手前被吃 = 有竞争者
        p.is_gold = 0;
        return false;
    }
    int r = sr, c = sc, n = 0;
    out[0] = out[1] = out[2] = STAY;
    while (n < 3 && !(r == p.tr && c == p.tc)) {
        int a = steerStep(r, c, p.tr, p.tc);
        if (a < 0) { p.is_gold = 0; return n > 0; }   // 卡住: 已走的步保留, 下轮重规划
        out[n++] = a;
        r += DR[a]; c += DC[a];
    }
    if (r == p.tr && c == p.tc) p.is_gold = 0;        // 到达, 下轮就地开采
    if (n == 0) { p.is_gold = 0; return false; }
    g_m.claim(p.tr, p.tc);
    return true;
}

// 从缓存计划弹出至多 3 步; 校验通过返回 true 并填 acts
bool followPlan(int u, int sr, int sc, int* out) {
    Plan& p = g_plan[u];
    if (p.len == 0 || p.pos >= p.len) return false;
    if (p.er != sr || p.ec != sc) { p.len = 0; return false; }      // 碰撞过, 作废
    if (g_w.round - (int)p.born > 10) { p.len = 0; return false; }  // 过期
    if (p.is_gold && g_w.gold(p.tr, p.tc) == 0) {          // 目标已空: 有竞争者出没
        g_w.stampContested(p.tr, p.tc);
        p.len = 0;
        return false;
    }
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

// ---------- 金币目标: 从列表曼哈顿选取(无 BFS) ----------
int g_cur_unit = 0;    // 当前规划中的角色(软分区用)
int pickGoldTarget(const GameInput* in, int sr, int sc) {
    long best = 0;
    int bi = -1;
    for (int i = 0; i < g_w.gn; ++i) {
        int idx = g_w.glist[i], r = idx / N, c = idx % N;
        if (g_w.cell[r][c].known < 1) {          // 已确认空: 顺手摘除
            g_w.cell[r][c].listed = 0;
            g_w.glist[i--] = g_w.glist[--g_w.gn];
            continue;
        }
        int v = g_w.gold(r, c);
        if (v <= 0 || g_m.claimed(r, c)) continue;
        if (r == sr && c == sc) continue;
        int d = (r > sr ? r - sr : sr - r) + (c > sc ? c - sc : sc - c);
        long val = v * 100L;
        int nb = 0;
        for (int a2 = 0; a2 < 4; ++a2) {
            int rr = r + DR[a2], cc2 = c + DC[a2];
            if (rr >= 0 && rr < N && cc2 >= 0 && cc2 < N) nb += g_w.gold(rr, cc2);
        }
        val += nb * 50L;
        if (g_w.isContested(r, c)) val /= 2;    // 有竞争者的区域折半
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nrr = in->visible_npcs[j].pos.row, ncc = in->visible_npcs[j].pos.col;
            if (nrr < 0) continue;
            int nd = (nrr > r ? nrr - r : r - nrr) + (ncc > c ? ncc - c : c - ncc);
#ifdef NPCD2
            if (nd * 13 < d * 10) { val /= 2; break; }
#else
            if (nd * 13 < d * 10) { val /= 3; break; }
#endif
        }
        long score = val / (d + 1);
        if (score > best) { best = score; bi = idx; }
    }
    return bi;
}

// 曼哈顿导向一步: 只朝缩短距离的方向走; 全被挡返回 -1(交给 BFS)
int steerStep(int r, int c, int tr, int tc) {
    int drr = tr - r, dcc = tc - c;
    int ar = drr < 0 ? 0 : 1, ac = dcc < 0 ? 2 : 3;
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int p0 = adr >= adc ? ar : ac, p1 = adr >= adc ? ac : ar;
    if (adr && adc) {
        int nr = r + DR[p0], nc = c + DC[p0];
        if (passable(nr, nc)) return p0;
        nr = r + DR[p1]; nc = c + DC[p1];
        if (passable(nr, nc)) return p1;
    } else {
        int a = adr ? ar : ac;
        int nr = r + DR[a], nc = c + DC[a];
        if (passable(nr, nc)) return a;
    }
    return -1;
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
        if (g_w.isContested(r, c)) val /= 2;    // 有竞争者的区域折半
        // NPC折价(保守版): NPC 常驻中心离金更近, 放宽竞速=白跑送人头
        // (139155-58 消融: 放宽后拾取次数 567->479)
        for (int j = 0; j < in->num_visible_npcs && j < MAX_NPCS; ++j) {
            int nrr = in->visible_npcs[j].pos.row, ncc = in->visible_npcs[j].pos.col;
            if (nrr < 0) continue;
            int nd = (nrr > r ? nrr - r : r - nrr) + (ncc > c ? ncc - c : c - ncc);
            if (nd * 13 < d * 10) { val /= 3; break; }
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
#ifdef YIELD80
            s += (long)g_w.yield_[r][c] * 80;    // 历史高产区优先巡回(权重加倍实验)
#else
            s += (long)g_w.yield_[r][c] * 40;    // 历史高产区优先巡回
#endif
            if (g_w.isContested(r, c)) s /= 2;
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
        for (int c = 0; c < N; ++c) cell[r][c] = {(int8_t)FOG, 0, 0};
    last_round = -1;
    last_vp = 0;
    vp_spent = 0;
    gn = 0;
    for (int r = 0; r < N; ++r)
        for (int c = 0; c < N; ++c) { contested[r][c] = 0; yield_[r][c] = 0; }
    g_plan[0].len = g_plan[1].len = 0;
}

GameOutput decide(const GameInput* in) {
    g_w.update(in);
    g_m.bn = g_m.nn = g_m.cn = 0;
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N && g_m.bn < 8) {
            g_m.br[g_m.bn] = (int8_t)r; g_m.bc[g_m.bn] = (int8_t)c; ++g_m.bn;
            g_w.stampContested(r, c);          // 敌人出没区, 选目标折半
        }
    }
    for (int i = 0; i < in->num_visible_npcs && i < MAX_NPCS; ++i) {
        int r = in->visible_npcs[i].pos.row, c = in->visible_npcs[i].pos.col;
        if (r >= 0 && r < N && c >= 0 && c < N && g_m.nn < 8) {
            g_m.nr[g_m.nn] = (int8_t)r; g_m.nc[g_m.nn] = (int8_t)c; ++g_m.nn;
        }
    }

#if defined(PROBE_LEVEL) && PROBE_LEVEL == 1
    return SAFE_OUT;                       // 只测 update+marks
#endif
#if defined(PROBE_LEVEL) && PROBE_LEVEL == 3
    for (int u2 = 0; u2 < 2; ++u2) {       // 测 update+marks+build+DFS
        int sr2 = in->my_units[u2].row, sc2 = in->my_units[u2].col;
        if (sr2 < 0 || sr2 >= N || sc2 < 0 || sc2 >= N) continue;
        int tmp3[3];
        g_local.run(sr2, sc2, in->my_units_gold[u2], tmp3);
    }
    return SAFE_OUT;
#endif
    GameOutput out = SAFE_OUT;
    int stale = 0, cells = 0;

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        if (sr < 0 || sr >= N || sc < 0 || sc >= N) continue;

        g_cur_unit = u;
        // 队友占位(自撞=浪费步)
        int tr = in->my_units[1 - u].row, tc = in->my_units[1 - u].col;
        int saved_bn = g_m.bn;
        if (tr >= 0 && tr < N && tc >= 0 && tc < N && g_m.bn < 8) {
            g_m.br[g_m.bn] = (int8_t)tr; g_m.bc[g_m.bn] = (int8_t)tc; ++g_m.bn;
        }

#ifdef EXPRESS
        // 快车道: 有有效行程 且 视野窗口(输入直读)无金 -> 跳过补丁+DFS
        if (g_plan[u].is_gold == 2 || (g_plan[u].len > 0 && g_plan[u].pos < g_plan[u].len)) {
            bool any_gold = false;
            int wr0 = sr - 2 < 0 ? 0 : sr - 2, wr1 = sr + 2 >= N ? N - 1 : sr + 2;
            int wc0 = sc - 2 < 0 ? 0 : sc - 2, wc1 = sc + 2 >= N ? N - 1 : sc + 2;
            for (int r2 = wr0; r2 <= wr1 && !any_gold; ++r2)
                for (int c2 = wc0; c2 <= wc1; ++c2)
                    if (in->grid[r2][c2] >= 1) { any_gold = true; break; }
            if (!any_gold) {
                if (steerCached(u, sr, sc, acts)) { g_m.bn = saved_bn; continue; }
                if (followPlan(u, sr, sc, acts)) { g_m.bn = saved_bn; continue; }
            }
        }
#endif
        int gain;
        int wg = g_w.wgold_n[u];
        if (wg == 0) {
            gain = 0;                                // 窗口无金: 直接走计划/目标机器
            acts[0] = acts[1] = acts[2] = STAY;
        } else if (wg == 1 && !g_w.bomb(g_w.wgold_r[u], g_w.wgold_c[u]) &&
                   !g_m.claimed(g_w.wgold_r[u], g_w.wgold_c[u])) {
            gain = singleGold(in, sr, sc, g_w.wgold_r[u], g_w.wgold_c[u], acts);
            if (gain == 0)
                gain = g_mini.run(in, sr, sc, in->my_units_gold[u], acts);
        } else {
            gain = g_mini.run(in, sr, sc, in->my_units_gold[u], acts);
        }
        (void)stale; (void)cells;
        if (gain > 0) {
            g_plan[u].len = 0;                    // 就地开采, 旧行程作废
            g_plan[u].is_gold = 0;
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                if (acts[i] != STAY && nrr >= 0 && nrr < N && ncc >= 0 && ncc < N &&
                    !g_w.wall(nrr, ncc) && !g_m.blocked(nrr, ncc)) {
                    r = nrr; c = ncc;
                    g_m.claim(r, c);
                }
            }
#ifdef OPENING
        } else if (in->round < 12 && g_w.gn == 0 && g_local.best_score == 0 &&
                   openingSprint(u, sr, sc, acts)) {
#endif
        } else if (steerCached(u, sr, sc, acts)) {   // 导向目标缓存命中: 最廉价路径
        } else if (!followPlan(u, sr, sc, acts)) {  // 廉价路径: 无 BFS
            int gi_ = pickGoldTarget(in, sr, sc);
            bool steered = false;
            if (gi_ >= 0) {
                int gtr = gi_ / N, gtc = gi_ % N;
                int r = sr, c = sc;
                steered = true;
                for (int i = 0; i < 3 && !(r == gtr && c == gtc); ++i) {
                    int a = steerStep(r, c, gtr, gtc);
                    if (a < 0) { steered = false; break; }
                    acts[i] = a;
                    r += DR[a]; c += DC[a];
                }
                if (steered) {
                    g_m.claim(gtr, gtc);
                    Plan& p = g_plan[u];        // 缓存导向目标(不存路径)
                    p.len = 0; p.is_gold = 2;
                    p.tr = (int8_t)gtr; p.tc = (int8_t)gtc;
                    p.born = (uint16_t)g_w.round;
                } else acts[0] = acts[1] = acts[2] = STAY;
            }
            int ttr = -1, ttc = -1;
            if (steered) {
            } else if (globalTarget(in, sr, sc, acts, &ttr, &ttc)) {
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
    // cpp21: 视野购买砍掉(A/B 中性 + 顶级选手全不买 + 省 ring 统计的常数)
    out.vp = 0;
    g_w.last_vp = 0;
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
