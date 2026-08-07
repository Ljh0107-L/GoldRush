// player.cpp — GoldRush 2.0 主战策略（C++ 为主战语言，见 AGENT.md §6.1）
//
// 当前版本 = Python p0greedy 的等价移植：贪心 BFS 找最近可见金币，
// 两角色各 3 步、目标去重、持币多者先行。价值在于把 C++ 提交链路和
// 防御性结构先立起来，策略本身后续在 decide() 里迭代。
//
// 结构分层（新策略只动"策略层"，边界层不要碰）：
//   moveDecision()  ← extern "C" 边界层：兜底 + sanitize，绝不让非法输出逃出去
//     └─ decide()   ← 策略层
//          ├─ World ← 跨回合常驻状态（对局内 .so 不重载，P1 世界模型挂这里）
//          └─ Bfs   ← 静态缓冲，热路径零堆分配
//
// 编译：必须在开发机(8.153.76.120, x86_64)上 make 出 player.so 再提交；
//       本机(macOS/arm64)只能 make check 做语法检查。
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
// 动作 -> (dr, dc)，下标即动作编码 0=上 1=下 2=左 3=右 4=不动
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr int BOMB = -3, OBSTACLE = -1;

constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

// ---------- 跨回合常驻状态（P1 世界模型在此扩展） ----------
struct World {
    int last_round = -1;
    // P1: known_grid[N][N] / last_seen[N][N] / 障碍永久化 / 金币置信度衰减
};
World g_world;

// ---------- BFS：全静态缓冲，不触堆 ----------
struct Bfs {
    int  visit_tag[N][N] = {};   // 与 cur_tag 比较代替 memset 清零
    int  cur_tag = 0;
    signed char prev_act[N][N];
    short qr[N * N], qc[N * N];
    int  qlen = 0;

    // 从 (sr,sc) 洪泛；可走 = 非障碍非炸弹（迷雾可走）。qr/qc 即访问序（近→远）。
    void run(const int grid[N][N], int sr, int sc) {
        ++cur_tag;
        qlen = 0;
        qr[qlen] = (short)sr; qc[qlen] = (short)sc; ++qlen;
        visit_tag[sr][sc] = cur_tag;
        prev_act[sr][sc] = -1;
        for (int head = 0; head < qlen; ++head) {
            int r = qr[head], c = qc[head];
            for (int a = 0; a < 4; ++a) {
                int nr = r + DR[a], nc = c + DC[a];
                if (nr < 0 || nr >= N || nc < 0 || nc >= N) continue;
                if (visit_tag[nr][nc] == cur_tag) continue;
                int v = grid[nr][nc];
                if (v == OBSTACLE || v == BOMB) continue;
                visit_tag[nr][nc] = cur_tag;
                prev_act[nr][nc] = (signed char)a;
                qr[qlen] = (short)nr; qc[qlen] = (short)nc; ++qlen;
            }
        }
    }

    // 回溯 (tr,tc) 的动作序列，正序写入 out（最多 cap 个），返回写入数；不可达返回 0。
    int pathTo(int sr, int sc, int tr, int tc, int* out, int cap) const {
        if (visit_tag[tr][tc] != cur_tag) return 0;
        int tmp[N * N];
        int len = 0;
        int r = tr, c = tc;
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

// 为一个角色规划 budget 步：BFS 找最近未被认领的金币格。
// claimed 是 17x17 标记，认领后置位，避免两角色抢同一块。
void planUnit(const int grid[N][N], int sr, int sc, int budget,
              bool claimed[N][N], int* out) {
    for (int i = 0; i < budget; ++i) out[i] = STAY;
    if (sr < 0 || sr >= N || sc < 0 || sc >= N) return;

    g_bfs.run(grid, sr, sc);
    for (int i = 1; i < g_bfs.qlen; ++i) {          // i=0 是起点自身
        int r = g_bfs.qr[i], c = g_bfs.qc[i];
        if (grid[r][c] >= 1 && !claimed[r][c]) {
            claimed[r][c] = true;
            g_bfs.pathTo(sr, sc, r, c, out, budget); // 不足 budget 的尾部保持 STAY
            return;
        }
    }
}

// ---------- 策略层：新想法都改这里 ----------
GameOutput decide(const GameInput* in) {
    GameOutput out = SAFE_OUT;
    g_world.last_round = in->round;

    bool claimed[N][N] = {};
    planUnit(in->grid, in->my_units[0].row, in->my_units[0].col, 3, claimed, out.actions);
    planUnit(in->grid, in->my_units[1].row, in->my_units[1].col, 3, claimed, out.actions + 3);

    out.k = 3;
    // 持币多的先走：更可能吃到争议金币，也更该先脱离风险
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
