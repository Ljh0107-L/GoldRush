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
#ifdef PROFILE
#include <ctime>
#endif
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int FOG = -5, BOMB = -3, OBSTACLE = -1;

constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

inline int ceilPct(int v, int pct) { return (v * pct + 99) / 100; }

#ifdef PROFILE
// -DPROFILE: 逐轮记录 decide() 周期数 + 组件命中位掩码(不改行为)。
// 用 tests/profile_replay.py 驱动真实对局输入, 得到"分支频率x耗时"矩阵。
struct Prof {
    unsigned long long cyc[600];
    int flags[600];
    int n;
};
Prof g_prof;
inline unsigned long long profNow() {
#if defined(__x86_64__)
    unsigned lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (unsigned long long)ts.tv_sec * 1000000000ull + ts.tv_nsec;
#endif
}
#define PROF_FLAG(bit) (g_prof.n < 600 ? (void)(g_prof.flags[g_prof.n] |= (bit)) : (void)0)
// 位: 1=DFS 2=singleGold 4=steerCached命中 8=followPlan命中 16=pickGold扫描
//     32=globalBFS 64=explore 128=本地收益>0 256=wg0 512=wg1 1024=wg2 2048=尾步填充改动
#else
#define PROF_FLAG(bit) ((void)0)
#endif

// ---------- 跨回合常驻世界模型(打包 4B/格, 全图 1156B) ----------
struct Cell {
    int8_t   known;   // 最后观测值(-5雾 -3炸弹 -1障碍 0空 1..127金)
    int8_t   listed;  // 已在金币目标列表中
    uint16_t seen;    // 最后观测回合+1；0=从未见过
};
struct World {
    Cell  cell[N][N];
    int   round;
    int   wave;                // 最近一次炸弹刷新波的回合(round - round%20)
    int   last_round;
    int   last_vp;
    int   vp_spent;
    int   wgold_n[2];          // 各单位 5x5 窗口内金币格数
    int   wgold_r[2], wgold_c[2];  // 恰 1 金时的坐标
    int8_t wg_r[2][8], wg_c[2][8]; // 窗口内前 8 个金币格坐标(候选路径评估用)
    uint8_t wg_v[2][8];            // 对应可见金量
    // 窗口紧凑缓冲(update 顺手填, 25B/单位 = 1 条缓存线): 与 in->grid 同编码。
    // DFS/候选评估的热读走这里, 免去每轮冷读散装网格 ~25 条缓存线(平台冷态主成本)。
    int8_t wbuf[2][25];
    int8_t wbase_r[2], wbase_c[2]; // 缓冲左上角(=单位位置-2)
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
        wave = round - round % 20;
        int rad = 2 + last_vp;
        for (int u = 0; u < 2; ++u) {
            wgold_n[u] = 0;
            int ur = in->my_units[u].row, uc = in->my_units[u].col;
            if (ur < 0 || ur >= N || uc < 0 || uc >= N) continue;
            wbase_r[u] = (int8_t)(ur - 2); wbase_c[u] = (int8_t)(uc - 2);
            for (int i = 0; i < 25; ++i) wbuf[u][i] = OBSTACLE;   // 出界=不可走
            int r0 = ur - rad < 0 ? 0 : ur - rad, r1 = ur + rad >= N ? N - 1 : ur + rad;
            int c0 = uc - rad < 0 ? 0 : uc - rad, c1 = uc + rad >= N ? N - 1 : uc + rad;
            for (int r = r0; r <= r1; ++r)
                for (int c = c0; c <= c1; ++c) {
                    int v = in->grid[r][c];
                    {   // 紧凑缓冲(rad>2 时只存 5x5 核心)
                        int dr2 = r - ur + 2, dc2 = c - uc + 2;
                        if ((unsigned)dr2 < 5u && (unsigned)dc2 < 5u)
                            wbuf[u][dr2 * 5 + dc2] = (int8_t)(v > 127 ? 127 : v);
                    }
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
                        if (wgold_n[u] < 8) {
                            wg_r[u][wgold_n[u]] = (int8_t)r;
                            wg_c[u][wgold_n[u]] = (int8_t)c;
                            wg_v[u][wgold_n[u]] = (uint8_t)(v > 255 ? 255 : v);
                        }
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
    // 陈旧格: 上一波炸弹刷新(mod20==0)之后没再看过 -> 可能藏新炸弹(~6.5%)
    inline bool stale(int r, int c) const {
        const Cell& x = cell[r][c];
        return x.seen == 0 || (int)(x.seen - 1) < wave;
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

int g_cur_unit = 0;    // 当前决策中的角色(软分区/窗口缓冲选择用)

// ---------- cpp27: 3 步穷举, 局部坐标直接索引 + 惰性记忆化 ----------
// 语义与 cpp21 直读版逐位等价(同一搜索树/同一评分), 只砍每节点常数:
//  - 覆盖层 ov_idx 线性扫 -> eaten[49] 直接索引
//  - 金币/旗标(墙/炸弹/踩踏)每格首次访问算一次, 记忆化(3步可达域仅 25 格)
//  - 炸弹罚 run() 里预计算(每 run 常量)
// 7x7 补丁以单位为中心; 3 步内的"尝试移动"永远落在补丁内, 无需边界检查
// (深度 d<=2 的出发格离中心 <=2, 目标 <=3)。地图边界格标记为不可走。
struct MiniLocal {
    const GameInput* in;
    int pen;                   // 炸弹罚(本 run 常量)
    int best; int bacts[3];
    int16_t eaten[49];         // 路径消耗覆盖: -1=未动
    int16_t gmemo[49];         // 金币记忆化: -1=未算
    uint8_t fmemo[49];         // 旗标: 0xFF=未算; 位 1=不可走 2=炸弹 4=踩踏
    int8_t  base_r, base_c;    // 补丁左上角(中心-3)

    static constexpr int DIDX[4] = {-7, 7, -1, 1};   // 与 DR/DC 同序: 上下左右

    inline uint8_t flagsAt(int pi) {
        uint8_t f = fmemo[pi];
        if (f != 0xFF) return f;
        int pr = pi / 7, pc = pi % 7;
        int r = base_r + pr, c = base_c + pc;
        int wr = pr - 1, wc = pc - 1;          // 5x5 窗口缓冲坐标
        f = 0;
        if ((unsigned)wr < 5u && (unsigned)wc < 5u) {
            int v = g_w.wbuf[g_cur_unit][wr * 5 + wc];   // 热读 1 条缓存线
            if (v == OBSTACLE || g_m.blocked(r, c)) f |= 1;
            if (v == BOMB) f |= 2;
            if (g_m.npcs(r, c) >= 3) f |= 4;
        } else if (r < 0 || r >= N || c < 0 || c >= N) f = 1;
        else {                                  // 7x7 边缘环(距离3): 冷读兜底
            int iv = in->grid[r][c];
            if (iv == OBSTACLE || g_w.wall(r, c) || g_m.blocked(r, c)) f |= 1;
            if (iv == BOMB || (iv == FOG && g_w.bomb(r, c))) f |= 2;
            if (g_m.npcs(r, c) >= 3) f |= 4;
        }
        fmemo[pi] = f;
        return f;
    }
    inline int goldAt(int pi) {
        int g = gmemo[pi];
        if (g >= 0) return g;
        int pr = pi / 7, pc = pi % 7;
        int wr = pr - 1, wc = pc - 1;
        if ((unsigned)wr < 5u && (unsigned)wc < 5u) {
            int v = g_w.wbuf[g_cur_unit][wr * 5 + wc];
            g = v >= 1 ? v : 0;                 // 窗口内无雾, 语义等价
        } else {
            int r = base_r + pr, c = base_c + pc;
            int iv = in->grid[r][c];
            g = iv >= 1 ? iv : (iv == FOG ? g_w.gold(r, c) : 0);
        }
        gmemo[pi] = (int16_t)g;
        return g;
    }

    void dfs(int pi, int depth, int acts[3], int sc) {
        if (sc > best) {
            best = sc;
            for (int t = 0; t < 3; ++t) bacts[t] = t < depth ? acts[t] : STAY;
        }
        if (depth == 3) return;
        for (int a = 0; a < 4; ++a) {
            int ni = pi + DIDX[a];
            uint8_t f = flagsAt(ni);
            if (f & 5) continue;               // 不可走/踩踏
            acts[depth] = a;
            int add = 0;
            int prev_e = eaten[ni];
            int v = prev_e >= 0 ? prev_e : goldAt(ni);
            if (v > 0) {
                int take = ceilPct(v, 65);
                add = take * 10;
                eaten[ni] = (int16_t)(v - take);
            }
            if (f & 2) add -= pen;
            dfs(ni, depth + 1, acts, sc + add);
            eaten[ni] = (int16_t)prev_e;
        }
    }
    int run(const GameInput* in_, int sr, int sc, int gold_now, int* acts_out) {
        in = in_;
        int bp = ceilPct(gold_now, 10) * 20;   // 惩罚x2 + 下限(防零持币白穿)
        pen = bp < 60 ? 60 : bp;
        base_r = (int8_t)(sr - 3); base_c = (int8_t)(sc - 3);
        for (int i = 0; i < 49; ++i) { eaten[i] = -1; gmemo[i] = -1; fmemo[i] = 0xFF; }
        best = 0;
        bacts[0] = bacts[1] = bacts[2] = STAY;
        int tmp[3];
        dfs(24, 0, tmp, 0);                    // 中心 = (3,3) = 24
        for (int t = 0; t < 3; ++t) acts_out[t] = bacts[t];
        return best;
    }
};
constexpr int MiniLocal::DIDX[4];
MiniLocal g_mini;

int steerStep(int r, int c, int tr, int tc);   // 前置声明(定义在后)

// ---------- cpp27: 接近型回合的候选路径评估(免盲 DFS) ----------
// 窗口最近金距离>=2 时, 3 步内的最优动作 ≈ 面向某金格的导向路径
// (含途中拾取 + 到位后顺吃相邻/退步备双吃)。逐候选精确评分(与 DFS 同公式),
// ~n 次导向 vs 84 路径树。近身混战(mind<2)仍用全量 DFS(链式吃的真正价值区)。
inline int wread(const GameInput* in, int u, int r, int c) {   // 窗口热读, 窗外冷读
    int wr = r - g_w.wbase_r[u], wc = c - g_w.wbase_c[u];
    if ((unsigned)wr < 5u && (unsigned)wc < 5u) return g_w.wbuf[u][wr * 5 + wc];
    if (r < 0 || r >= N || c < 0 || c >= N) return OBSTACLE;
    return in->grid[r][c];
}

int candEval(const GameInput* in, int u, int sr, int sc, int* acts_out) {
    int n = g_w.wgold_n[u] < 8 ? g_w.wgold_n[u] : 8;
    int best = 0;
    int bacts[3] = {STAY, STAY, STAY};
    for (int i = 0; i < n; ++i) {
        int gr = g_w.wg_r[u][i], gc = g_w.wg_c[u][i];
        if (g_m.claimed(gr, gc)) continue;
        int d0 = (gr > sr ? gr - sr : sr - gr) + (gc > sc ? gc - sc : sc - gc);
        if (d0 < 2 || d0 > 3) continue;
        int acts[3] = {STAY, STAY, STAY};
        int r = sr, c = sc, k = 0, score = 0;
        bool ok = true;
        while (k < 3 && !(r == gr && c == gc)) {
            int a = steerStep(r, c, gr, gc);
            if (a < 0) { ok = false; break; }
            acts[k++] = a;
            r += DR[a]; c += DC[a];
            int iv = wread(in, u, r, c);             // 途中拾取(与 DFS 同估值)
            int v = iv >= 1 ? iv : (iv == FOG ? g_w.gold(r, c) : 0);
            if (v > 0) score += ceilPct(v, 65) * 10;
        }
        if (!ok || score == 0) continue;             // 被挡/没吃到: 不构成收益
        if (k < 3) {
            // 剩一步: 顺吃相邻未认领金格, 否则退一步(下轮再进吃残量)
            int besta = -1, bestv = 0;
            for (int a2 = 0; a2 < 4; ++a2) {
                int rr = r + DR[a2], cc2 = c + DC[a2];
                if (rr < 0 || rr >= N || cc2 < 0 || cc2 >= N) continue;
                if (!passable(rr, cc2) || g_m.npcs(rr, cc2) >= 3) continue;
                int iv2 = wread(in, u, rr, cc2);
                if (iv2 >= 1 && iv2 > bestv && !g_m.claimed(rr, cc2)) {
                    bestv = iv2; besta = a2;
                }
            }
            if (besta >= 0) { acts[k] = besta; score += ceilPct(bestv, 65) * 10; }
            else acts[k] = acts[k - 1] ^ 1;
        }
        if (score > best) {
            best = score;
            bacts[0] = acts[0]; bacts[1] = acts[1]; bacts[2] = acts[2];
        }
    }
    if (best > 0) {
        acts_out[0] = bacts[0]; acts_out[1] = bacts[1]; acts_out[2] = bacts[2];
    }
    return best;
}

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
    // 备胎目标: pick 扫描时顺存第二名; 主目标吃空/失效时直接顶上, 免重扫
    // (平台探针实证: 目标层执行 ~2.2μs 且骑在 P50 边界, 频率就是延迟)
    int8_t alt_tr, alt_tc;
    uint16_t alt_born;    // 0 = 无备胎
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
    for (int attempt = 0; attempt < 2; ++attempt) {
        bool bad = g_w.round - (int)p.born > 12 || g_w.gold(p.tr, p.tc) <= 0 ||
                   g_w.isContested(p.tr, p.tc);
        if (!bad) {
            int r = sr, c = sc, n = 0;
            out[0] = out[1] = out[2] = STAY;
            while (n < 3 && !(r == p.tr && c == p.tc)) {
                int a = steerStep(r, c, p.tr, p.tc);
                if (a < 0) { p.is_gold = 0; return n > 0; }   // 卡住: 下轮重规划
                out[n++] = a;
                r += DR[a]; c += DC[a];
            }
            // 到达不清目标: 采集期间保留, 吃空后经 gold<=0 分支换备胎(粘性闭环)
            if (n > 0) { g_m.claim(p.tr, p.tc); return true; }
            // n==0 站在目标上却无本地收益: 当失效处理, 落到备胎
        } else if (g_w.gold(p.tr, p.tc) <= 0 && g_w.round - (int)p.born <= 12 &&
                   !(p.tr == sr && p.tc == sc)) {
            g_w.stampContested(p.tr, p.tc);      // 未到手就被吃 = 竞争证据
        }
        p.is_gold = 0;
        // 备胎顶上(免 pick+gBFS 重扫): 仍新鲜、有金、无争抢、未认领
        if (p.alt_born && g_w.round - (int)p.alt_born <= 12 &&
            g_w.gold(p.alt_tr, p.alt_tc) > 0 &&
            !g_w.isContested(p.alt_tr, p.alt_tc) &&
            !g_m.claimed(p.alt_tr, p.alt_tc)) {
            p.tr = p.alt_tr; p.tc = p.alt_tc;
            p.born = p.alt_born; p.alt_born = 0;
            p.is_gold = 2;
            continue;
        }
        return false;
    }
    return false;
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

// ---------- 金币目标: 从列表曼哈顿选取(无 BFS); alt_out 顺带输出第二名 ----------
int pickGoldTarget(const GameInput* in, int sr, int sc, int* alt_out = nullptr) {
    long best = 0, best2 = 0;
    int bi = -1, bi2 = -1;
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
        if (score > best) { best2 = best; bi2 = bi; best = score; bi = idx; }
        else if (score > best2) { best2 = score; bi2 = idx; }
    }
    if (alt_out) *alt_out = bi2;
    return bi;
}

// 曼哈顿导向一步: 只朝缩短距离的方向走; 全被挡返回 -1(交给 BFS)
// (cpp23 曾在此加"富时绕陈旧格"——探索方向必然全陈旧, 行军蛇形, 基线-600, 已撤)
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
int g_full_cool[2] = {0, 0};   // 全图 BFS 冷却(每单位每 3 轮至多一次, 压 P90 尾)

bool globalTarget(const GameInput* in, int sr, int sc, int* out, int* otr, int* otc) {
    long best = 0;
    int bi = -1;
    // 两段式: 先 9 步(常见情形, 便宜); 无目标再全图(恢复外圈大堆远征, 摊薄进计划缓存)
    // 全图段带冷却: 反复找不到目标时(explore 会兜底)别每轮都付 ~10μs 的全图扫
    int max_stage = 2;
    if (g_w.round < g_full_cool[g_cur_unit]) max_stage = 1;
    for (int stage = 0; stage < max_stage && bi < 0; ++stage) {
    if (stage == 1) g_full_cool[g_cur_unit] = g_w.round + 3;
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
    g_plan[0].is_gold = g_plan[1].is_gold = 0;
    g_plan[0].alt_born = g_plan[1].alt_born = 0;
    g_full_cool[0] = g_full_cool[1] = 0;
}

GameOutput decide(const GameInput* in) {
    g_w.update(in);
    g_m.bn = g_m.nn = g_m.cn = 0;
    for (int i = 0; i < 2; ++i) {
        int r = in->visible_enemies[i].row, c = in->visible_enemies[i].col;
        if (r >= 0 && r < N && c >= 0 && c < N && g_m.bn < 8) {
            g_m.br[g_m.bn] = (int8_t)r; g_m.bc[g_m.bn] = (int8_t)c; ++g_m.bn;
#ifndef NOSIGHTSTAMP
            // 目击盖章: vs 中心蹲守型对手会把最肥刷新区自我禁区化(见 §10), 可关
            g_w.stampContested(r, c);          // 敌人出没区, 选目标折半
#endif
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
    for (int u2 = 0; u2 < 2; ++u2) {       // 测 update+marks+DFS(直读版)
        g_cur_unit = u2;
        int sr2 = in->my_units[u2].row, sc2 = in->my_units[u2].col;
        if (sr2 < 0 || sr2 >= N || sc2 < 0 || sc2 >= N) continue;
        int tmp3[3];
        g_mini.run(in, sr2, sc2, in->my_units_gold[u2], tmp3);
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
            PROF_FLAG(256);
            gain = 0;                                // 窗口无金: 直接走计划/目标机器
            acts[0] = acts[1] = acts[2] = STAY;
        } else if (wg == 1 && !g_w.bomb(g_w.wgold_r[u], g_w.wgold_c[u]) &&
                   !g_m.claimed(g_w.wgold_r[u], g_w.wgold_c[u])) {
            PROF_FLAG(512);
            PROF_FLAG(2);
            gain = singleGold(in, sr, sc, g_w.wgold_r[u], g_w.wgold_c[u], acts);
            if (gain == 0) {
                PROF_FLAG(1);
                gain = g_mini.run(in, sr, sc, in->my_units_gold[u], acts);
            }
        } else {
            PROF_FLAG(1024);
            int mind = 99;
            for (int i2 = 0; i2 < g_w.wgold_n[u] && i2 < 8; ++i2) {
                int dd = (g_w.wg_r[u][i2] > sr ? g_w.wg_r[u][i2] - sr : sr - g_w.wg_r[u][i2]) +
                         (g_w.wg_c[u][i2] > sc ? g_w.wg_c[u][i2] - sc : sc - g_w.wg_c[u][i2]);
                if (dd < mind) mind = dd;
            }
#ifndef NOCANDEVAL
            if (mind >= 2) {                          // 接近型: 候选评估代替盲 DFS
                PROF_FLAG(2048);
                gain = candEval(in, u, sr, sc, acts);
                if (gain == 0) {                      // 全部被挡/够不到: 回退
                    PROF_FLAG(1);
                    gain = g_mini.run(in, sr, sc, in->my_units_gold[u], acts);
                }
            } else
#endif
            {
                PROF_FLAG(1);
                (void)mind;
                gain = g_mini.run(in, sr, sc, in->my_units_gold[u], acts);
            }
        }
        (void)stale; (void)cells;
        if (gain > 0) {
            PROF_FLAG(128);
            g_plan[u].len = 0;                    // 就地开采, BFS 路径作废(位置将偏移)
#ifdef NOSTICKY
            g_plan[u].is_gold = 0;                // 旧行为: 导向目标一并作废(对照用)
#endif
            // 粘性导向(默认): is_gold==2 的导向目标保留(steerCached 自带校验),
            // 采集结束后直接续走, 免掉 pick+gBFS 重建(profiling: 缓存命中率 11%->?)
            int r = sr, c = sc;
            for (int i = 0; i < 3; ++i) {
                int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                if (acts[i] != STAY && nrr >= 0 && nrr < N && ncc >= 0 && ncc < N &&
                    !g_w.wall(nrr, ncc) && !g_m.blocked(nrr, ncc)) {
                    r = nrr; c = ncc;
                    g_m.claim(r, c);
                }
            }
#if defined(PROBE_LEVEL) && (PROBE_LEVEL == 5 || PROBE_LEVEL == 6)
        } else {  // 探针: 目标层砍除, 用轮转走位保持巡图(状态分布接近实战)
            int a = (g_w.round / 4 + u * 2) & 3;
            acts[0] = acts[1] = acts[2] = a;
        }
#else
#ifdef OPENING
        } else if (in->round < 12 && g_w.gn == 0 && g_local.best_score == 0 &&
                   openingSprint(u, sr, sc, acts)) {
#endif
        } else if (steerCached(u, sr, sc, acts)) {   // 导向目标缓存命中: 最廉价路径
            PROF_FLAG(4);
        } else if (followPlan(u, sr, sc, acts)) {
            PROF_FLAG(8);
        } else {                                     // 目标层: 扫列表(+可能 BFS)
            PROF_FLAG(16);
            int alt_ = -1;
            int gi_ = pickGoldTarget(in, sr, sc, &alt_);
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
                    if (alt_ >= 0) {            // 备胎(第二名)顺带入库
                        p.alt_tr = (int8_t)(alt_ / N);
                        p.alt_tc = (int8_t)(alt_ % N);
                        p.alt_born = (uint16_t)g_w.round;
                    } else p.alt_born = 0;
                } else acts[0] = acts[1] = acts[2] = STAY;
            }
            int ttr = -1, ttc = -1;
            if (steered) {
            } else if (PROF_FLAG(32), globalTarget(in, sr, sc, acts, &ttr, &ttc)) {
                storePlan(u, sr, sc, ttr, ttc, true);
                g_plan[u].pos = 3 <= g_plan[u].len ? 3 : g_plan[u].len;  // 本轮已执行前3步
                { int r = sr, c = sc;
                  for (int i = 0; i < g_plan[u].pos; ++i) { r += DR[(int)g_plan[u].acts[i]]; c += DC[(int)g_plan[u].acts[i]]; }
                  g_plan[u].er = (int8_t)r; g_plan[u].ec = (int8_t)c; }
            } else {
                PROF_FLAG(64);
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
#endif
#if !(defined(PROBE_LEVEL) && PROBE_LEVEL == 5)
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
        // 防漂移(实测 6 局被炸 25 次的主因): 引擎对被挡的步只跳过、后续步照走,
        // (探针 P5 时随尾步填充一起砍除)
        // 落点会整体偏移一格。可信的阻挡场景下, 后续落点若是已知炸弹就从该步截断。
        // 可信 = 被挡步目标是雾, 或 3 格内有可见 NPC/单位(动态实体一轮能走 3 步)。
        // 无门控版触发 93 次/局(实际事件 ~4 次), 基线 -300; 门控是必需的。
        // 持币 300+: 单次被炸 ≥30 金 >> 截断浪费的 1-2 步(~3-6 金), 期望明确为正;
        // 低持币时期望反而为负, 不做。
        if (in->my_units_gold[u] >= 300) {
            for (int blk = 0; blk < 3; ++blk) {
                if (acts[blk] == STAY) continue;
                int br2 = sr, bc2 = sc;                    // 被挡步的出发点
                {
                    int r = sr, c = sc;
                    for (int i = 0; i < blk; ++i) {
                        int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                        if (acts[i] != STAY && nrr >= 0 && nrr < N && ncc >= 0 &&
                            ncc < N && !g_w.wall(nrr, ncc)) { r = nrr; c = ncc; }
                    }
                    br2 = r + DR[acts[blk]]; bc2 = c + DC[acts[blk]];
                }
                if (br2 < 0 || br2 >= N || bc2 < 0 || bc2 >= N) continue;
                bool plausible = g_w.cell[br2][bc2].known == FOG;
                for (int i2 = 0; i2 < g_m.nn && !plausible; ++i2) {
                    int dr2 = g_m.nr[i2] - br2, dc2 = g_m.nc[i2] - bc2;
                    if ((dr2 < 0 ? -dr2 : dr2) <= 3 && (dc2 < 0 ? -dc2 : dc2) <= 3)
                        plausible = true;
                }
                for (int i2 = 0; i2 < g_m.bn && !plausible; ++i2) {
                    int dr2 = g_m.br[i2] - br2, dc2 = g_m.bc[i2] - bc2;
                    if ((dr2 < 0 ? -dr2 : dr2) <= 3 && (dc2 < 0 ? -dc2 : dc2) <= 3)
                        plausible = true;
                }
                if (!plausible) continue;
                int r = sr, c = sc;
                for (int i = 0; i < 3; ++i) {
                    if (acts[i] == STAY) continue;
                    int nrr = r + DR[acts[i]], ncc = c + DC[acts[i]];
                    if (i == blk || nrr < 0 || nrr >= N || ncc < 0 || ncc >= N ||
                        g_w.wall(nrr, ncc)) continue;      // 该步被挡/无效: 原地
                    if (g_w.bomb(nrr, ncc)) {
                        for (int j = i; j < 3; ++j) acts[j] = STAY;
                        g_plan[u].len = 0;                 // 截断后计划错位, 作废
                        break;
                    }
                    r = nrr; c = ncc;
                }
            }
        }
#endif
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
#ifdef PROFILE
        if (g_prof.n < 600) {
            g_prof.flags[g_prof.n] = 0;
            unsigned long long t0 = profNow();
            GameOutput o = sanitize(decide(input));
            g_prof.cyc[g_prof.n] = profNow() - t0;
            ++g_prof.n;
            return o;
        }
#endif
        return sanitize(decide(input));
    } catch (...) {
        return SAFE_OUT;
    }
}

#ifdef PROFILE
extern "C" int profRounds() { return g_prof.n; }
extern "C" unsigned long long profCyc(int i) { return i >= 0 && i < g_prof.n ? g_prof.cyc[i] : 0; }
extern "C" int profFlags(int i) { return i >= 0 && i < g_prof.n ? g_prof.flags[i] : 0; }
extern "C" void profReset() { g_prof.n = 0; }
#endif
