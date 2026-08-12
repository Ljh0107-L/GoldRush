// player.cpp — GoldRush 2.0 陌生图策略
//
// 所有地图都按未知地图处理：源码不保存地图指纹、墙表或专用路线。策略只使用当前视野在线学习
// 静态墙体，并在第 0 轮购买一次 9x9 视野。开局前 8 轮若局部无金，则根据已知墙体在线 BFS
// 前往两个中央锚点；之后继续懒学习墙体，但不再运行开局行军。
//
// 每个单位每轮从 48 条候选路径中选一条：
//   * 32 条三步路径、12 条两步加 STAY、4 条一步加双 STAY；
//   * 所有踏入格都位于当前 5x5 视野内，且不立即掉头、不重复踩格；
//   * 先剔除越界、墙、炸弹、雾和一个玩家占位（可见敌人优先），再按金币档位收敛候选；
//   * 平手时优先向本单位锚点靠拢，再按表序选择覆盖面更大的路径。
//
// 金币只在成功踏入格子时结算，因此 STAY、撞墙和折返重踩都会浪费步数。完整路径在打分前
// 一次性通过阻挡检查；每单位用一个角色阻挡槽，优先规避首个可见敌人，否则规避队友。
#include <cstdint>
#include <cstring>
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[4] = {-1, 1, 0, 0};
constexpr int DC[4] = {0, 0, -1, 1};
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

// ---- stride-8 窗口位图 ----
// bit (8*(i+1) + j) ↔ 网格格 (sr-2+i, sc-2+j), i,j ∈ 0..4; 自己 = bit 26。
// 选 stride 8 而不是 5: 8-lane movemask 的行片可**一次变量左移**就位(见 scan), 不必逐行乘 5 重排。
// 行基址整体 +8(即行 i 占字节 i+1): 让列对齐量 `8*(i+1)+2-lsh` 恒 ≥6 > 0,
// 于是 4 个掩码 × 5 行只各付 1 条 shlx + 1 条 or —— 省掉旧写法的 `<<2` 与 `>>lsh` 两条。
// 越界列(j<0 或 j>4)会落进相邻字节的高 3 位, 一律被 WM/reach 掩掉, 故无需逐行 &31。
constexpr uint64_t WM = 0x1F1F1F1F1F00ULL;  // 有效位(字节 1..5 的低 5 位)
constexpr int WSELF = 26;
constexpr int WSENT = 45;                   // 哨兵格位(WM 恒不含它 ⇒ thru[45]=0 恒无收敛)

struct alignas(64) State {
    uint32_t bpw[N + 2];     // 在线学到的墙体与边界哨兵(bit c+1)
    int16_t last_round;
    uint8_t opening;         // 前 8 轮允许在无金时执行 BFS 行军
    uint8_t vp_buy;          // 本轮 vp 输出，也标记上一轮是否购买了 9x9
    int8_t anch_r[2], anch_c[2];
    uint32_t seen[N];        // 已观测静态地形的格子(bit c+1)
    uint32_t visited[N];     // 已扫描过视野的单位中心(bit c+1)
};
State g_s;

// 与地图身份无关的中央分驻点；若在线观测发现锚点为墙，fixAnchor 会改到最近的已知可通行格。
constexpr int ANCH_R0 = 6;
constexpr int ANCH_DR = 5;
constexpr int ANCH_C = 8;
static_assert(ANCH_R0 >= 0 && ANCH_R0 + ANCH_DR < N && ANCH_C >= 0 && ANCH_C < N
              && ANCH_DR != 0, "anchors must be distinct cells inside the board");

// 扫描边缘常量表: cb(载入列基)/lsh(位对齐)/colv(有效列 5bit) 均为 sc 纯函数
struct SctT {
    int8_t cb[17], lsh[17]; uint8_t colv[17];
    constexpr SctT() : cb(), lsh(), colv() {
        for (int sc = 0; sc < 17; ++sc) {
            int c = sc - 2 < 0 ? 0 : (sc - 2 > 12 ? 12 : sc - 2);
            cb[sc] = (int8_t)c;
            lsh[sc] = (int8_t)(2 + (sc - 2 - c));
            int lo = sc - 2 < 0 ? -(sc - 2) : 0;
            int hix = sc + 2 > 16 ? sc + 2 - 16 : 0;
            colv[sc] = (uint8_t)(((31u >> hix) & (31u << lo)) & 31u);
        }
    }
};
constexpr SctT SCT;

// ============ 候选路径表（48 条，全部 constexpr 生成） ============
// 生成规则：
//   * 每步 ∈ {上,下,左,右}, 尾部可补 STAY(STAY 不结算金币, 只能补在尾部)
//   * 不立即掉头 (a[t+1] != a[t]^1) ⇒ 各格必互不相同, 天然零重踏、无需 0.35^n 递减项
//   * 各格全在自己 5×5 内，保证金额和阻挡在决策时可见
//   * 排序 = 覆盖面降序: L单调(0..23) → L折回(24..31) → S(32..43) → O(44..47)
//     ⇒ ctz 天然偏好扫得更开的那条, 平局裁决不必再比长度
struct PathT {
    uint64_t thru[46];       // 格位 → 踏入该格的路径集合(bit p); 45 = 哨兵, 恒 0
    uint64_t cell[48];       // 路径 → 各格位掩码 | (动作 a0|a1<<3|a2<<6) << 52
    uint64_t towR[3], towC[3];  // **可分**方位项: 行/列各按 sgn(想去 − 现在)+1 索引, 语义 = 「不后退」
    int8_t sgi[41];          // sgn(x)+1 查表, 索引 x+20 (x ∈ [-20,20]; 锚点最远 |Δ|=16)
    uint64_t reach;          // 可踏入的格集(20 格: 曼1/2/3; 不含自己与曼4 四角)
    uint64_t rowok[17], colok[17];   // 越界剔除: 全程留在盘内的路径集(**行/列可分**, 见下)
    uint64_t rclr[5][32];    // 阻挡剔除表
    int8_t rcl[21];          // 行钳位(仅为 AVX 载入地址合法; 幻影数据由 rowok 兜掉)
    constexpr PathT()
        : thru(), cell(), towR(), towC(), sgi(), reach(0), rowok(), colok(), rclr(), rcl() {
        int n = 0;
        int prmn[48] = {}, prmx[48] = {}, pcmn[48] = {}, pcmx[48] = {};
        for (int fam = 0; fam < 4; ++fam) {      // 0=L单调 1=L折回 2=S 3=O
            int len = fam <= 1 ? 3 : (fam == 2 ? 2 : 1);
            for (int a0 = 0; a0 < 4; ++a0)
                for (int a1 = 0; a1 < 4; ++a1) {
                    if (len >= 2 && a1 == (a0 ^ 1)) continue;
                    if (len < 2 && a1 != 0) continue;             // O 族: 只枚举 a0
                    for (int a2 = 0; a2 < 4; ++a2) {
                        if (len >= 3 && a2 == (a1 ^ 1)) continue;
                        if (len < 3 && a2 != 0) continue;         // S/O 族: 只枚举前 len 个
                        int aa[3] = {a0, a1, a2};
                        int r = 0, c = 0, bits[3] = {0, 0, 0};
                        int rmn = 0, rmx = 0, cmn = 0, cmx = 0;
                        bool ok = true;
                        for (int s = 0; s < len; ++s) {
                            r += DR[aa[s]]; c += DC[aa[s]];
                            if (r < -2 || r > 2 || c < -2 || c > 2) { ok = false; break; }
                            bits[s] = 8 * (r + 3) + (c + 2);      // 行基址 +8(见窗口位图注)
                            if (r < rmn) rmn = r;
                            if (r > rmx) rmx = r;
                            if (c < cmn) cmn = c;
                            if (c > cmx) cmx = c;
                        }
                        if (!ok) continue;                        // 出窗: L 族的 4 条直线三连
                        int man = (r < 0 ? -r : r) + (c < 0 ? -c : c);
                        if (fam == 0 && man != 3) continue;        // 单调优先排前
                        if (fam == 1 && man == 3) continue;
                        uint64_t cm = 0;
                        for (int s = 0; s < len; ++s) {
                            cm |= 1ULL << bits[s];
                            thru[bits[s]] |= 1ULL << n;
                        }
                        unsigned a6 = 0;                           // 尾部补 STAY
                        for (int s = 0; s < 3; ++s)
                            a6 |= (unsigned)(s < len ? aa[s] : STAY) << (3 * s);
                        cell[n] = cm | ((uint64_t)a6 << 52);
                        reach |= cm;
                        prmn[n] = rmn; prmx[n] = rmx; pcmn[n] = cmn; pcmx[n] = cmx;
                        // 可分方位项: 「不朝反方向退」。比 tow[] 宽松(允许该轴零进展),
                        // 换来运行期只付 2 次字节查表 + 2 次 AND, 省掉 quad() 的 setcc 链。
                        for (int g = 0; g < 3; ++g) {
                            int sg = g - 1;
                            if (sg * r >= 0) towR[g] |= 1ULL << n;
                            if (sg * c >= 0) towC[g] |= 1ULL << n;
                        }
                        ++n;
                    }
                }
        }
        // 越界剔除可按行列分离：路径合法 ⇔ 所有行偏移合法且所有列偏移合法。
        for (int s = 0; s < 17; ++s)
            for (int p = 0; p < 48; ++p) {
                if (s + prmn[p] >= 0 && s + prmx[p] <= 16) rowok[s] |= 1ULL << p;
                if (s + pcmn[p] >= 0 && s + pcmx[p] <= 16) colok[s] |= 1ULL << p;
            }
        for (int i = 0; i < 5; ++i)          // 与 pop-loop 逐位等价: AND 可结合可交换, 只是按行分组
            for (int p = 0; p < 32; ++p) {
                uint64_t k = 0;
                for (int j = 0; j < 5; ++j)
                    if (p >> j & 1) k |= thru[8 * (i + 1) + j];
                rclr[i][p] = ~k;
            }
        for (int x = 0; x < 21; ++x) {
            int t = x - 2;
            rcl[x] = (int8_t)(t < 0 ? 0 : (t > 16 ? 16 : t));
        }
        for (int x = 0; x < 41; ++x) {
            int t = x - 20;
            sgi[x] = (int8_t)((t > 0) - (t < 0) + 1);
        }
    }
};
constexpr PathT PT;
constexpr uint64_t ALLP = (1ULL << 48) - 1;
static_assert(PT.cell[47] != 0, "48 条候选未生成满");
static_assert((PT.reach & ~WM) == 0, "可踏入格必须落在窗口有效位内");
static_assert(PT.thru[WSENT] == 0, "哨兵格位必须无路径");
static_assert(PT.thru[WSELF] == 0, "自己所在格不可被踏入(零重踏由构造保证)");
static_assert(__builtin_popcountll(PT.reach) == 20, "可踏入格应为 20 格");
static_assert(__builtin_popcountll(ALLP) == 48, "候选集应包含全部 48 条路径");

// ============ 在线墙体学习与通用开局 ============

inline unsigned wallbit(int r, int c) { return (g_s.bpw[r + 1] >> (c + 1)) & 1u; }

__attribute__((noinline, cold))
void fixAnchor(int u) {                          // 锚点是墙 → 改指最近可通行格
    int tr = g_s.anch_r[u], tc = g_s.anch_c[u];
    if (!wallbit(tr, tc)) return;
    int br = tr, bc = tc, bd = 999;
    for (int r = 0; r < N; ++r)
        for (int c = 0; c < N; ++c) {
            if (wallbit(r, c)) continue;
            int d = (r > tr ? r - tr : tr - r) + (c > tc ? c - tc : tc - c);
            if (d < bd) { bd = d; br = r; bc = c; }
        }
    g_s.anch_r[u] = (int8_t)br; g_s.anch_c[u] = (int8_t)bc;
}

__attribute__((noinline, cold))
void learnVisibleWalls(const GameInput* in) {
    int rad = g_s.vp_buy == 2 ? 4 : 2;           // 上轮买了 9x9 时，本轮输入半径为 4
    g_s.vp_buy = 0;
    unsigned learned = 0;
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        if (g_s.visited[sr] >> (sc + 1) & 1u) continue;
        int r0 = sr - rad < 0 ? 0 : sr - rad, r1 = sr + rad > 16 ? 16 : sr + rad;
        int c0 = sc - rad < 0 ? 0 : sc - rad, c1 = sc + rad > 16 ? 16 : sc + rad;
        const unsigned window_mask = (unsigned)((1u << (c1 + 2)) - (1u << (c0 + 1)));
        for (int r = r0; r <= r1; ++r) {
            if (((~g_s.seen[r]) & window_mask) == 0u) continue;
            learned = 1;
            for (int c = c0; c <= c1; ++c) {
                int v = in->grid[r][c];
                if (v == -5) continue;           // 雾: 无信息
                g_s.seen[r] |= 1u << (c + 1);
                if (v == -1) g_s.bpw[r + 1] |= 1u << (c + 1);
            }
        }
        g_s.visited[sr] |= 1u << (sc + 1);
    }
    if (learned) {
        fixAnchor(0); fixAnchor(1);
    }
    if (in->round == 0) g_s.vp_buy = 2;
    if (in->round >= 8) g_s.opening = 0;
}

__attribute__((noinline, cold))
void planOpeningMove(int u, int sr, int sc, int* acts) {
    // 只把在线学到的墙视为阻挡；尚未看见的格子按可通行处理。
    int start = sr * N + sc;
    int goal = g_s.anch_r[u] * N + g_s.anch_c[u];
    if (start == goal) return;
    uint8_t par[N * N];
    uint16_t q[N * N];
    uint32_t vis[N] = {};
    int head = 0, tail = 0;
    q[tail++] = (uint16_t)start;
    vis[sr] |= 1u << sc;
    while (head < tail && q[head] != goal) {
        int cur = q[head++];
        int r = cur / N, c = cur % N;
        for (int a = 0; a < 4; ++a) {
            int nr = r + DR[a], nc = c + DC[a];
            if ((unsigned)nr >= (unsigned)N || (unsigned)nc >= (unsigned)N) continue;
            if (wallbit(nr, nc) || (vis[nr] >> nc & 1)) continue;
            vis[nr] |= 1u << nc;
            par[nr * N + nc] = (uint8_t)a;
            q[tail++] = (uint16_t)(nr * N + nc);
        }
    }
    if (head >= tail) return;                    // 当前知识下不可达时保留主管线动作
    int seq[3 * 3];                              // 全程回溯，环形保存起点侧前 3 步
    int n = 0;
    for (int cur = goal; cur != start; ++n) {
        int a = par[cur];
        seq[n % 9] = a;
        cur = (cur / N - DR[a]) * N + (cur % N - DC[a]);
    }
    acts[0] = seq[(n - 1) % 9];
    acts[1] = n > 1 ? seq[(n - 2) % 9] : STAY;
    acts[2] = n > 2 ? seq[(n - 3) % 9] : STAY;
}

GameOutput decide(const GameInput* in) {
    {   // 冷启动税对冲: 入口并行预取热工作集(轮间350ms全被逐出, 串行回填≈+200ns)
        _mm_prefetch((const char*)&g_s, _MM_HINT_T0);
        _mm_prefetch((const char*)&g_s + 64, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT + 64, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT.cell, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT.cell + 64, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT.cell + 128, _MM_HINT_T0);
        _mm_prefetch((const char*)&PT.towR, _MM_HINT_T0);
        _mm_prefetch((const char*)&SCT, _MM_HINT_T0);
        int r0 = in->my_units[0].row, r1 = in->my_units[1].row;
        _mm_prefetch((const char*)&in->grid[r0 < 2 ? 0 : r0 - 2][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r0][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r1 < 2 ? 0 : r1 - 2][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r1][0], _MM_HINT_T0);
    }
    if (in->round <= g_s.last_round) {
        memset(&g_s, 0, sizeof(g_s));
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u;
        g_s.opening = 1;
        for (int u = 0; u < 2; ++u) {
            g_s.anch_r[u] = (int8_t)(ANCH_R0 + ANCH_DR * u); g_s.anch_c[u] = (int8_t)ANCH_C;
        }
    }
    g_s.last_round = (int16_t)in->round;

    bool new_view = !(g_s.visited[in->my_units[0].row] >> (in->my_units[0].col + 1) & 1u)
                 || !(g_s.visited[in->my_units[1].row] >> (in->my_units[1].col + 1) & 1u);
    if (__builtin_expect(g_s.opening || new_view, 0)) learnVisibleWalls(in);

    GameOutput out;                              // 全字段必写, 免 SAFE_OUT 拷贝

    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;

        // ---- scan: 5 行就地 AVX 载入 → 4 个 stride-8 窗口位图 ----
        // 每行每掩码只付 movemask + shlx + or 三条(位移量 = 8*(i+1)+2-lsh, 恒 ≥6 见窗口位图注);
        // 越界行/列的幻影数据**不必清**: 它们只能落在 rowok/colok 已剔除的路径上(见下), 无害。
        uint64_t g1 = 0, g2 = 0, g5 = 0, bd = 0;
        {
#if defined(__AVX2__)
            int sh = 10 - SCT.lsh[sc], cb = SCT.cb[sc];
            const __m256i z = _mm256_setzero_si256();
            const __m256i v3 = _mm256_set1_epi32(3);
            const __m256i v8 = _mm256_set1_epi32(8);
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int cr = PT.rcl[sr + i];                         // = clamp(sr-2+i, 0, 16)
                __m256i vr = _mm256_loadu_si256((const __m256i*)&in->grid[cr][cb]);
                unsigned s = (unsigned)(sh + 8 * i);
#if defined(__AVX512VL__)
                uint64_t m1 = (uint64_t)_mm256_cmpgt_epi32_mask(vr, z);   // v >= 1
                uint64_t m2 = (uint64_t)_mm256_cmpgt_epi32_mask(vr, v3);  // v >= 4
                uint64_t m5 = (uint64_t)_mm256_cmpgt_epi32_mask(vr, v8);  // v >= 9
                uint64_t mb = (uint64_t)_mm256_cmpgt_epi32_mask(z, vr);   // v < 0 = 墙|弹|雾
#else
                uint64_t m1 = (uint64_t)(uint32_t)_mm256_movemask_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(vr, z)));
                uint64_t m2 = (uint64_t)(uint32_t)_mm256_movemask_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(vr, v3)));
                uint64_t m5 = (uint64_t)(uint32_t)_mm256_movemask_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(vr, v8)));
                uint64_t mb = (uint64_t)(uint32_t)_mm256_movemask_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(z, vr)));
#endif
                g1 |= m1 << s;
                g2 |= m2 << s;
                g5 |= m5 << s;
                bd |= mb << s;
            }
#else
            for (int i = 0; i < 5; ++i) {                         // 标量参考(仅本机测试)
                int rr = sr - 2 + i;
                if ((unsigned)rr >= (unsigned)N) continue;
                for (int j = 0; j < 5; ++j) {
                    int cc = sc - 2 + j;
                    if ((unsigned)cc >= (unsigned)N) continue;
                    int v = in->grid[rr][cc];
                    uint64_t b = 1ULL << (8 * (i + 1) + j);
                    if (v >= 1) g1 |= b;
                    if (v >= 4) g2 |= b;
                    if (v >= 9) g5 |= b;
                    if (v < 0)  bd |= b;
                }
            }
#endif
            g1 &= PT.reach;                      // 靶只能是可踏入格(顺带清掉越界列的幻影位)
            g2 &= PT.reach;
            g5 &= PT.reach;
            bd &= PT.reach;                      // 只有可踏入格被挡才会毁掉路径
            // grid 不标玩家位置。维持一个角色阻挡槽：看见敌人时优先防止敌人格挡导致
            // 后续动作偏移；没有可见敌人时仍保留队友阻挡。
            const Position& teammate = in->my_units[1 - u];
            const Position& enemy = in->visible_enemies[0];
            const Position& blocker = enemy.row >= 0 ? enemy : teammate;
            unsigned ti = (unsigned)(blocker.row - sr + 2);
            unsigned tj = (unsigned)(blocker.col - sc + 2);
            if (__builtin_expect((ti < 5u) & (tj < 5u), 0))
                bd |= 1ULL << (8u * (ti + 1u) + tj);
        }

        // ---- 剔除会撞上当前阻挡、炸弹或边界的路径 ----
        uint64_t cand = ALLP & PT.rowok[sr] & PT.colok[sc];
        // 每行 5 位阻挡切片查表，定长完成全部路径剔除。
        cand &= PT.rclr[0][(bd >> 8) & 31] & PT.rclr[1][(bd >> 16) & 31]
              & PT.rclr[2][(bd >> 24) & 31] & PT.rclr[3][(bd >> 32) & 31]
              & PT.rclr[4][(bd >> 40) & 31];
        if (__builtin_expect(cand == 0, 0)) {
            acts[0] = acts[1] = acts[2] = STAY;
            continue;
        }

        // ---- 面值分档贪心: 高档金格优先, 3 轮无分支收敛 ----
        // 分档而非精确面值排序: 档内乱序的代价是「同档两格只能取一格时可能取小的那个」,
        // 上限 <1 口拾取；换来全程无回读 grid、无排序网络。档界为 1..3、4..8、9+。
        {
            uint64_t t5 = g5, t2 = g2 & ~g5, t1 = g1 & ~g2;
#pragma GCC unroll 3
            for (int it = 0; it < 3; ++it) {     // 一条路径最多踏 3 格
                uint64_t m = t1;
                if (t2) m = t2;
                if (t5) m = t5;                  // 档优先(cmov, 非分支)
                int b = __builtin_ctzll(m | (1ULL << WSENT));
                uint64_t clr = ~(1ULL << b);
                t5 &= clr; t2 &= clr; t1 &= clr;
                uint64_t t = cand & PT.thru[b];  // 无金/不可达 ⇒ thru=0 ⇒ 不收敛
                if (t) cand = t;
            }
        }

        // ---- 平局裁决：优先不背离本单位锚点 ----
        {
            uint64_t t = cand & PT.towR[PT.sgi[g_s.anch_r[u] - sr + 20]]
                              & PT.towC[PT.sgi[g_s.anch_c[u] - sc + 20]];
            if (t) cand = t;
        }
        int p = __builtin_ctzll(cand);
        unsigned a6 = (unsigned)(PT.cell[p] >> 52);
        acts[0] = (int)(a6 & 7u);
        acts[1] = (int)((a6 >> 3) & 7u);
        acts[2] = (int)((a6 >> 6) & 7u);

        if (__builtin_expect(g_s.opening && !g1, 0))
            planOpeningMove(u, sr, sc, acts);
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = g_s.vp_buy;                         // 仅第 0 轮为 2
    return out;
}

}  // namespace

// moveDecision 的入口需保持在已验证的 mod64=0x10 档。修改函数体后必须在赛事机构建并重校。
asm(".space 212, 0x90");

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
        return decide(input);
    } catch (...) {
        return SAFE_OUT;
    }
}
