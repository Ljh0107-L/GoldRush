// player.cpp — GoldRush 2.0 现役冠军 + 三图指纹/慢开局 (2026-08-08)
//
// 战绩: P50 中位 200ns(读数 170-230) / P90 ~292 / 收入中位 ~1515
//       对 Tiuntled-1 先手 85-96%; 演进史与判决见 CHANGELOG.md
//       指令(perf 实测, tests/icount.cpp): map1/2/3 = 799/892/924 每调用; 本地 hot 约 208-238 cycles
// 成本模型(设计新算法前必读): 见 INFRA.md —— 壳40ns + 载荷~2ns/条 + 指令×0.1454ns
// ⚠ 0.1454 是全函数**平均**价, 不是删指令的**边际**价: 本函数受依赖链/访存约束(IPC~4),
//   实测删 84 条只换回 5.6 cycles(≈0.025ns/条, 比均价低 6 倍)。删指令≠变快, 验收看 cycles。
//
// ============ 每轮决策 loop ============
// 入口: moveDecision → try{ decide } catch{ SAFE_OUT }   (输出全路径可证合法, 无钳位)
// 0. 慢开局层(mode!=FAST 才进, 冷路径; map1 锁图后 round≥4 退场, 稳态零接触):
//    0.1 学墙: 任一单位站上新格才触发(visited 单bit门控, 站过⇒窗口已学, 零漏报);
//        窗口半径 2(默认)/4(9×9 视野轮), v==-1 入墙位图, v!=-5 入 seen
//    0.2 指纹: (bpw ^ 候选墙表) & seen 逐行比对淘汰; 唯一候选 → 锁图直灌全墙表;
//        map1 角落 5×5 即可区分(round 0 锁, 行为与旧版逐位一致);
//        map2/3 角落同构 → round 0 买 vp=2, round 1 用 9×9 终判(全图可区分, 实测)
//    0.3 中央双驻守: 三图共用 (6,8)/(11,8), 快照远征调度已下岗、守卫位点暂留保布局
//    0.4 三图全不吻合 → 陌生图模式: 懒学习伴终局(稳态只付 visited 门控 ~+5ns), 行军窗自然导向
// 1. 新局检测(round 回绕) → 重置状态, bpw = 边界哨兵(墙由指纹/学习灌入)
// 2. %20 冷位点: 仅清空炸弹波记忆; 已判负快照远征不再置状态
// 3. 对每个单位(双全管线, 无轮换):
//    3.1 富度门: 持金≥100 才把炸弹并入阻挡(弹对穷单位「近乎」透明)
//        ⚠ 该门的原始前提「穷单位烧 10%×0=0」**已证伪**: 引擎实收 (held+9)//10 向上取整,
//        故持金 10-99 的单位每次踩弹仍烧 1-9 金。全局烧损 219.4 金/局, 其中 99.4% 落在
//        miss(零拾取)单位轮。门槛 100 是从错误前提推出的, 但改门槛是行为改动、须单独定价,
//        故此处只更正注释、不动常量(CHANGELOG 8.10 记账)。
//        ⚠⚠ 更强的一条: **前提已证伪 ≠ 阈值可改进**。阈值的消融已实测为**非杠杆** ——
//        「永不避弹」贡献 +344/+374/+212(避弹本体是高价值安全器官, 不可删), 而
//        「永远避弹」相对当前阈值只有 −8±23 / +6±13 / +38±26(**与 0 相容**)。
//        即: 避弹有用, 但阈值设在哪里几乎不影响结果 ⇒ **调这个立即数买不到那 219.4 金**;
//        要减烧损必须换机制。**不要顺着上面这条正确诊断去改阈值**(会重演一次判负)。
//        出处: sim/reports/subsystem_value_audit.md:81-82。
//        🛡 **硬约束**: 避弹器官不得"顺带"牺牲。依据是**同种子配对消融**——「永不避弹」三图
//        贡献 +344/+374/+212 金(subsystem_value_audit.md:81); 该消融是我方构型自身带/不带
//        该器官的配对比较, 不跨队、不涉行动顺序, **不受先手混淆影响**。
//        (曾另引 Ausdroid f≈1 三分解「烧损差 +145 金」作依据, 现已降级: 该批 f=0.997, 是
//         "我方先手 vs 对手后手", 我方后手仅 18 个单位轮, 无法同序比较 ⇒ 一致但不构成证据。)
//        ⇒ **任何改动若降低避弹有效性, 必须单独证明收益 > 避弹损失, 不得"顺带"牺牲。**
//        判例: unsafe 绕路省指令只查首步炸弹, 后两步撞弹多烧 424 金 → 判负 −373(军规 23)。
//    3.2 扫描: 5×5 窗口 5 行就地 AVX 载入 → goldm 25位(金) / 弹记**全 5 行**
//        ⚠ 此处曾长期写作「bombm 15位(弹, 仅±1行)」——**那是修复前的旧设计, 描述自己写错了**。
//        现役实现见 :456(`bslv[5]` 按行暂存, 取代 bombm 的 15 位打包)与 :481-484
//        (`#pragma GCC unroll 5` 对 i=0..4 全部 5 行写 `g_s.bombbit[rr+3]`), 覆盖整个 5×5。
//        ±1 行记法正是被 158287 局烧 335 金那一案打掉的(见 :478 修复注记)。
//        代价已实付: 8.10 有人据这条过期注释提出「扩大弹记覆盖」候选, 而该机制已在役三代。
//    3.3 目标: goldm 按环距优先级 pext 重排 + ctz = 最近金格; 无金 → 中央生成峰双驻守
//    3.4 站金(d==0): 折返双吃 —— 出格再回格, 链式收 35% 残值
//    3.5 行进: LUT 三步导向(constexpr 表, 早到折返已预折叠) + pass01 途经验证;
//        受阻 → 单步谨慎 + 下轮自愈; 逃逸四向掩码+ctz 恒形选首路(消灭循环早退尾部)
//    3.6 开局行军(盲轮才走): map1 = 烘焙 BFS 路线 4 轮出角(位置吻合门控, 漂移自弃);
//        map2/3/指纹未定 = 运行时 BFS(已知墙, 雾当可通行) 3 步/轮, 窗口 round<8
// 4. 输出: k=3, order=持金多者先走, vp=慢开局层裁定(稳态恒 0)
//
// ============ 已退役的防御(可证冗余, 详见 CHANGELOG 军规 12) ============
// sanitize 输出钳位 / 入口坐标钳位 / pass01 队友检查(撞位实测 0 轮) —— try/catch 永不下岗
#include <cstdint>
#include <cstring>
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include "game_api.h"

namespace {

constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

// mode: 0=FAST(稳态, 冷层零接触) 1=OPENING(指纹/行军) 2=LAZY(陌生图懒学习)
// map_id: -1 未定 0/1/2 已锁 -2 陌生图
struct alignas(64) State {
    uint32_t bpw[N + 2];     // 墙|边界哨兵位图(bit c+1; 指纹锁图或在线学习灌入)
    uint32_t bombbit[N + 6]; // 炸弹位图(带±2垫行, 写入下标 r+3; 免行钳位 cmov)
    int8_t last_r[2], last_c[2];
    int16_t last_round;
    uint8_t mode;
    int8_t map_id;
    uint8_t cand;            // 候选图位掩码(bit m)
    uint8_t vp_buy;          // 本轮 vp 输出(稳态恒 0; 也用作"上轮买了视野"标记)
    int8_t anch_r[2], anch_c[2];
    uint8_t exc;             // 突击进行中(waveTick 置/清)
    int8_t sv_ar, sv_ac;     // u1 基地锚点备份(远征结束恢复)
    int16_t rem_prev[4];     // 上次快照外区(id 2-5)存量
    int16_t exc_t0;          // 远征开拔轮(超时归队判据)
    int8_t exc_reg;          // 远征目标区 2-5(0=未出征)
    int8_t exc_br, exc_bc;   // 远征车道 B 端点(抵 A 后切换)
    uint8_t exc_wp;          // 0=去程 A 段 1=已切 B 段
    uint32_t seen[N];        // 已观测格(bit c+1; 指纹比对掩码)
    uint32_t visited[N];     // 站过的格(bit c+1; 学墙门控)
};
State g_s;

// 三图墙表(bit c+1)。map1 = 多局日志比对恒定; map2/3 = 8.8 探针局 156675/156676。
// 新官方图入表方法: 取该图日志第 2 行, 值==1 的格按 bit(c+1) 打包成每行 u32。
// 不入表也能跑(陌生图模式在线学墙), 入表才享受锁图 +0ns。
constexpr uint32_t BAKED_W[3][N] = {
    {0x00004010u, 0x00000000u, 0x0001800cu, 0x00024012u, 0x00002020u, 0x00001040u,
     0x00004010u, 0x00000500u, 0x000028a0u, 0x00000500u, 0x00004010u, 0x00001040u,
     0x00002020u, 0x00024012u, 0x0001800cu, 0x00000000u, 0x00004010u},
    {0x00000000u, 0x00000000u, 0x00008888u, 0x00000000u, 0x00002220u, 0x00000000u,
     0x00008888u, 0x00000000u, 0x00002020u, 0x00000000u, 0x00008888u, 0x00000000u,
     0x00002220u, 0x00000000u, 0x00008888u, 0x00000000u, 0x00000000u},
    {0x00000000u, 0x00000000u, 0x0000e038u, 0x0000e038u, 0x00003de0u, 0x00003de0u,
     0x00003de0u, 0x00000000u, 0x000038e0u, 0x00000000u, 0x00003de0u, 0x00003de0u,
     0x00003de0u, 0x0000e038u, 0x0000e038u, 0x00000000u, 0x00000000u},
};
constexpr uint32_t INTERIOR = 0x0003FFFEu;       // bit 1..17 = c 0..16
// 锁图后复核墙表的窗口长度。**24 不是"留了 6 倍余量" —— 那个说法是删失(censoring)产物。**
// 去窗重测(3650 局, 每张已知图的每个合法单格编辑): 首次矛盾轮 中位 25 / p90 237 / p95 339
// / max 495, 且 18.9% 永不产生矛盾 ==> **窗口正坐在中位上**, 且 24 之后无悬崖(证据散布 25-499)。
// "实测总在 round 3-4"是 mimic1/2/3 这一类的属性(中央错 17/13/29 格), 不是分布的属性;
// 现役报告的检出轮 max **恰好等于 24 本身**, 就是删失的签名。
// 超窗后 FAST 稳态只付 mode/round 两次比较, 不再载入 visited。
constexpr int VERIFY_ROUNDS = 24;



// map1 开局烘焙路线(BFS 最优 4 轮出角; 起点恒 (0,0)/(16,16); 仅 map_id==0 使用)
constexpr uint8_t ORT_A[2][4][3] = {
    {{1,3,3},{3,1,3},{1,3,1},{3,1,1}},           // u0 (0,0)->(6,6)
    {{0,2,2},{2,0,2},{0,2,0},{2,0,0}},           // u1 (16,16)->(10,10)
};
constexpr int8_t ORT_R[2][4] = {{0,1,2,4},{16,15,14,12}};
constexpr int8_t ORT_C[2][4] = {{0,2,4,5},{16,14,12,11}};

// 扫描边缘常量表: cb(载入列基)/lsh(位对齐)/colv(有效列掩码) 均为 sc 纯函数
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

struct TabsT {                                   // 表合一: 单基址消灭 xmm 指针停放(§3.4)
    int8_t rclv[21];                             // 行钳位
    int8_t d5[25], m5[25];                       // 除模5
    uint8_t remap[26];                           // pext 环距重排
    uint16_t bestrow[5][32];                     // 行级选择: 切片→(环距优先级<<5|窗位)
    constexpr TabsT() : rclv(), d5(), m5(), remap(), bestrow() {
        for (int x = 0; x < 21; ++x) { int t = x - 2; rclv[x] = (int8_t)(t < 0 ? 0 : (t > 16 ? 16 : t)); }
        for (int x = 0; x < 25; ++x) { d5[x] = (int8_t)(x / 5); m5[x] = (int8_t)(x % 5); }
        constexpr uint8_t rm[26] = {
            7,11,13,17, 2,6,8,10,14,16,18,22, 1,3,5,9,15,19,21,23, 0,4,20,24, 12, 12};
        for (int x = 0; x < 26; ++x) remap[x] = rm[x];
        uint8_t prio[25] = {};
        for (int k = 0; k < 25; ++k) prio[rm[k]] = (uint8_t)k;
        for (int i = 0; i < 5; ++i)
            for (int s = 0; s < 32; ++s) {
                uint16_t best = 0xFFFF;
                for (int j = 0; j < 5; ++j)
                    if (s >> j & 1) {
                        uint16_t v = (uint16_t)((prio[i * 5 + j] << 5) | (i * 5 + j));
                        if (v < best) best = v;
                    }
                bestrow[i][s] = best;
            }
    }
};
constexpr TabsT TT;

__attribute__((noinline, cold))
void excNext(const GameInput*) {                 // 抵锚事件: 车道切换/归队
    if (g_s.exc_wp == 0) {                       // 抵 A 切 B
        g_s.exc_wp = 1;
        g_s.anch_r[1] = g_s.exc_br; g_s.anch_c[1] = g_s.exc_bc;
        return;
    }
    g_s.exc = 0; g_s.exc_reg = 0;                // 扫完: 归队复锚
    g_s.anch_r[1] = g_s.sv_ar; g_s.anch_c[1] = g_s.sv_ac;
}

// 通行 = 非墙 且 (穷 或 非弹)。队友检查已退役(撞位实测 0 轮, 引擎记4+自愈兜底)
inline unsigned pass01(int r, int c, unsigned rich) {
    return (~((g_s.bpw[r + 1] | (rich & g_s.bombbit[r + 3])) >> (c + 1)) & 1u);
}

__attribute__((noinline, cold))
int escapeStep(int r, int c, int pr, int pc, unsigned rich) {
    // 四向同时查询 + 掩码选首路，等价于旧 for/continue/early-return，但无数据依赖分支。
    unsigned pm = pass01(r - 1, c, rich) |
                  (pass01(r + 1, c, rich) << 1) |
                  (pass01(r, c - 1, rich) << 2) |
                  (pass01(r, c + 1, rich) << 3);
    unsigned back = (unsigned)((r - 1 == pr) & (c == pc)) |
                    ((unsigned)((r + 1 == pr) & (c == pc)) << 1) |
                    ((unsigned)((r == pr) & (c - 1 == pc)) << 2) |
                    ((unsigned)((r == pr) & (c + 1 == pc)) << 3);
    int a = __builtin_ctz((pm & ~back) | 16u);   // bit4 哨兵: 无路时 a=4
    return a - 5 * (a == 4);                    // 4→-1，其余保持 0..3
}

int steerStep(int r, int c, int gr, int gc, int pr, int pc, unsigned rich) {
    int drr = gr - r, dcc = gc - c;
    int ar = drr > 0;
    int ac = 2 + (dcc > 0);
    int adr = drr < 0 ? -drr : drr, adc = dcc < 0 ? -dcc : dcc;
    int rowf = adr >= adc;
    int p0 = rowf ? ar : ac, p1 = rowf ? ac : ar;
    unsigned ok0 = pass01(r + DR[p0], c + DC[p0], rich);
    unsigned ok1 = pass01(r + DR[p1], c + DC[p1], rich) &
                   (unsigned)((adr != 0) & (adc != 0));
    if (ok0 | ok1)
        return ok0 ? p0 : p1;
    return (adr | adc) ? escapeStep(r, c, pr, pc, rich) : -1;
}

// LUT 导向: (dr,dc)∈[-3,3]² 行优先无阻挡模拟
// fact = 动作序列(早到折返 d<3 已预折叠); pdr/pdc = 逐步累计位移(途经验证用)
struct SLut {
    static constexpr int SLW = 6;
    uint8_t fact[7][7][SLW];
    int8_t  pdr[7][7][SLW], pdc[7][7][SLW];
    constexpr SLut() : fact(), pdr(), pdc() {
        for (int dr = -3; dr <= 3; ++dr)
            for (int dc = -3; dc <= 3; ++dc) {
                int r = 0, c = 0;
                for (int i = 0; i < SLW; ++i) {
                    int rr = dr - r, cc = dc - c;
                    int adr = rr < 0 ? -rr : rr, adc = cc < 0 ? -cc : cc;
                    uint8_t a = STAY;
                    if (adr | adc) {
                        if (adr >= adc) { a = rr > 0 ? 1 : 0; r += rr > 0 ? 1 : -1; }
                        else            { a = cc > 0 ? 3 : 2; c += cc > 0 ? 1 : -1; }
                    }
                    fact[dr + 3][dc + 3][i] = a;
                    pdr[dr + 3][dc + 3][i] = (int8_t)r;
                    pdc[dr + 3][dc + 3][i] = (int8_t)c;
                }
                {   // 早到折返预折叠(语义与旧运行时 fold 块逐位一致)
                    int d = (dr < 0 ? -dr : dr) + (dc < 0 ? -dc : dc);
                    if (d > 0 && d < SLW)
                        for (int t = d; t < SLW; ++t)
                            fact[dr + 3][dc + 3][t] =
                                (uint8_t)(fact[dr + 3][dc + 3][t - 1] ^ 1);
                }
            }
    }
};
constexpr SLut SL;

// ============ 慢开局冷层(mode!=FAST 才进; 稳态 FAST 下零接触) ============

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
void waveTick(const GameInput*) {                 // 炸弹每 20 轮整套重采样
    memset(g_s.bombbit, 0, sizeof(g_s.bombbit));
}

__attribute__((noinline, cold))
void slowTick(const GameInput* in) {
    int rad = g_s.vp_buy == 2 ? 4 : 2;           // 上轮买了 9×9 → 本轮窗口半径 4
    g_s.vp_buy = 0;
    unsigned learned = 0;
    unsigned conflict = 0;
    for (int u = 0; u < 2; ++u) {                // 站格单bit门控, 按单位各自判(站过⇒窗口已学)
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        if (g_s.visited[sr] >> (sc + 1) & 1u) continue;
        learned = 1;
        int r0 = sr - rad < 0 ? 0 : sr - rad, r1 = sr + rad > 16 ? 16 : sr + rad;
        int c0 = sc - rad < 0 ? 0 : sc - rad, c1 = sc + rad > 16 ? 16 : sc + rad;
        for (int r = r0; r <= r1; ++r)
            for (int c = c0; c <= c1; ++c) {
                int v = in->grid[r][c];
                if (v == -5) continue;           // 雾: 无信息
                // grid 语义: -5雾 -3弹 -1墙 0空 >=1金。只有 -1 是墙; 炸弹/金币/空地
                // 一律非墙 —— 若把弹或金误读成墙, 已知图上会每局误退, 比原病更糟。
                unsigned isw = (unsigned)(v == -1);
                if (g_s.map_id >= 0) {           // 已锁图: 只比对, 不改表
                    conflict |= isw ^ ((g_s.bpw[r + 1] >> (c + 1)) & 1u);
                } else {                         // 未锁图: 照旧学墙 + 记 seen
                    g_s.seen[r] |= 1u << (c + 1);
                    if (isw) g_s.bpw[r + 1] |= 1u << (c + 1);
                }
            }
        g_s.visited[sr] |= 1u << (sc + 1);
    }
    if (__builtin_expect(conflict != 0, 0)) {    // 锁定表被实测否证 → 不可逆退回懒学习
        g_s.map_id = -2; g_s.cand = 0; g_s.mode = 2;
        for (int r = 0; r < N; ++r) {            // 清掉幻影墙: 挡住合法走位的才是有害的那半
            g_s.bpw[r + 1] = 0xFFFC0001u;        // 只留边界哨兵, 与新局重置同形
            g_s.seen[r] = 0; g_s.visited[r] = 0; // 重新观测, 保守假设「什么都还不知道」
        }
        fixAnchor(0); fixAnchor(1);
        return;                                  // 本轮不再走淘汰/退场逻辑
    }
    if (g_s.map_id == -1 && learned) {           // 指纹淘汰赛(seen 没变就不必重判)
        for (int m = 0; m < 3; ++m) {
            if (!(g_s.cand >> m & 1)) continue;
            for (int r = 0; r < N; ++r)
                if (((g_s.bpw[r + 1] & INTERIOR) ^ BAKED_W[m][r]) & g_s.seen[r]) {
                    g_s.cand &= (uint8_t)~(1u << m);
                    break;
                }
        }
        if (g_s.cand == 0) {
            g_s.map_id = -2;                     // 陌生图: 懒学习伴终局
            fixAnchor(0); fixAnchor(1);
        } else if (!(g_s.cand & (g_s.cand - 1))) {
            int m = __builtin_ctz(g_s.cand);     // 唯一候选: 锁图直灌
            g_s.map_id = (int8_t)m;
            for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u | BAKED_W[m][r];
            fixAnchor(0); fixAnchor(1);
        }
        if (in->round == 0 && g_s.map_id < 0)
            g_s.vp_buy = 2;                      // 角落区分不了/陌生图 → 买下一轮 9×9
    } else if (g_s.map_id == -2 && learned) {
        fixAnchor(0); fixAnchor(1);              // 学到新墙才需要重验锚点/重烘流场
    }
    // 模式退场: map1 与旧版同窗(4 轮); 其余图行军窗 8 轮; 未锁/陌生图转懒学习长驻
    if (g_s.map_id == 0) { if (in->round >= 4) g_s.mode = 0; }
    else if (g_s.map_id > 0) { if (in->round >= 8) g_s.mode = 0; }
    else if (in->round >= 8) {
        if (g_s.mode != 2)        g_s.mode = 2;
    }
}

__attribute__((noinline, cold))
void slowMove(const GameInput* in, int u, int sr, int sc, unsigned rich, int* acts) {
    (void)rich;
    if (g_s.map_id == 0) {                       // map1: 烘焙路线原样(保逐位等价)
        if (in->round < 4) {
            int ri = in->round & 3;
            if (sr == ORT_R[u][ri] && sc == ORT_C[u][ri]) {
                acts[0] = ORT_A[u][ri][0];
                acts[1] = ORT_A[u][ri][1];
                acts[2] = ORT_A[u][ri][2];
            }
        }
        return;
    }
    if (in->round >= 8) return;                  // 通用行军窗口(map2/3 实测 4-5 轮出角)
    // 运行时 BFS(已知墙, 雾当可通行; 穷单位弹透明, 与主管线富度门一致) → 取前 3 步
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
    if (head >= tail) return;                    // 目标不可达(理论不发生): 交回主管线
    int seq[3 * 3];                              // 只需最后 3 步以上? 全程回溯, 环形存前 3
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
        _mm_prefetch((const char*)&g_s + 128, _MM_HINT_T0);
        _mm_prefetch((const char*)&TT, _MM_HINT_T0);
        _mm_prefetch((const char*)&TT + 64, _MM_HINT_T0);
        _mm_prefetch((const char*)&SCT, _MM_HINT_T0);
        _mm_prefetch((const char*)&SL, _MM_HINT_T0);
        _mm_prefetch((const char*)&SL + 64, _MM_HINT_T0);
        _mm_prefetch((const char*)&SL.pdr, _MM_HINT_T0);
        int r0 = in->my_units[0].row, r1 = in->my_units[1].row;
        _mm_prefetch((const char*)&in->grid[r0 < 2 ? 0 : r0 - 2][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r0][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r1 < 2 ? 0 : r1 - 2][0], _MM_HINT_T0);
        _mm_prefetch((const char*)&in->grid[r1][0], _MM_HINT_T0);
    }
    if (in->round <= g_s.last_round) {           // 新局: 重置; 墙由指纹/学习灌入
        memset(&g_s, 0, sizeof(g_s));
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u;
        g_s.mode = 1; g_s.map_id = -1; g_s.cand = 7;
        for (int u = 0; u < 2; ++u) {            // 三图同锚: 中央窗口分驻, 切比雪夫距离 5
            g_s.anch_r[u] = (int8_t)(6 + 5 * u); g_s.anch_c[u] = 8;
        }
    }
    g_s.last_round = (int16_t)in->round;
    if (in->round % 20 == 0)                     // 炸弹波清 + 远征开拔/归队(骑同一位点)
        waveTick(in);

    {   // 远征抵锚事件 → 冷推进(拔点链/车道切换/归队)。exc 期成段连续, 分支按段预测,
        // 误预测只在段边界 ~10-20 次/局(谱系: v1 B端扎营/v2 回程扫空车道皆判负, 见 CHANGELOG)
        if (__builtin_expect(g_s.exc_reg != 0, 0)) {
            int dr_ = in->my_units[1].row - g_s.anch_r[1];
            int dc_ = in->my_units[1].col - g_s.anch_c[1];
            if ((dr_ < 0 ? -dr_ : dr_) + (dc_ < 0 ? -dc_ : dc_) <= 1) excNext(in);
        }
    }

    // 慢开局层(学墙/指纹/锚点/vp)。原门控为 `mode != 0` 外套站格门控, 故一旦锁图转 FAST,
    // slowTick 再不被调用 —— 这正是误锁不可自愈的根因(实测 −689 金/局, 静默)。
    // 现改为: mode==1 恒进; mode==2(陌生图懒学习)照旧按站格门控长驻; **FAST 只在开局
    // VERIFY_ROUNDS 轮内**按站格门控进入, 用于复核锁定墙表。
    // 为什么限窗: 不限窗会让已知图整局跑 ~90 次窗口扫描、白付约 21-24 cycles/调用(≈ −77 金,
    // 即相对现役 −46 金)—— 这是**确定的**成本; 换来的覆盖(49.7% -> 81.1%)所针对的对手
    // **无可测足迹**(见下), 故按"不拿确定损失换无足迹收益"拒绝加宽。任何界 X 可零配额定价:
    // gold(X) = −31 − 0.838 × (scans(X) − 22.2)。
    // **承重证据已更换**(不再是"轮数余量", 见 :111 处更正):
    //   (a) 逃逸率随错误格数崩塌: k=1 56.9% -> k=3 8.3% -> k>=8 全 0/24; 真正的新官方图会差
    //       数十格 ==> mimic 类(17/13/29 格)必被 r3-4 抓到;
    //   (b) 逃逸者行为上**不可区分**: 未检出误锁的受阻步率 24.03/21.57/30.80% vs 正确锁定对照
    //       24.63/21.13/30.47% ==> 一比特错的表跑满 500 轮不产生可测损失。
    // ==> 即**检出失败的情形恰好是失败不要紧的情形**; (b) 现在是**实测**而非断言。
    // 已知盲区: 两个反对角角落(对手出生点); 棋盘 36-47% 首次比较发生在 r24 之后, 其中
    // 12-35% 永不比较; map1 最软(仅凭 3x3 角即在 r0 锁定, 272 个单格编辑只有 12 个破指纹)。
    if (__builtin_expect(g_s.mode == 1
            || ((g_s.mode == 2 || in->round <= VERIFY_ROUNDS)
                && (!(g_s.visited[in->my_units[0].row] >> (in->my_units[0].col + 1) & 1u)
                 || !(g_s.visited[in->my_units[1].row] >> (in->my_units[1].col + 1) & 1u))), 0))
        slowTick(in);

    GameOutput out;                              // 全字段必写, 免 SAFE_OUT 拷贝

    int cur = 3;                                 // k 当游标: 3 = 交付布局, 0/6 = 已重分配
    int blind0 = 0;                              // u0 是否盲(u1 的尾块要读)
    const uint8_t* ext0 = nullptr;               // u0 的 LUT 计划; 非空才可续写尾段
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        acts[0] = acts[1] = acts[2] = STAY;
        const uint8_t* pext = nullptr;
        unsigned rich = 0u - (unsigned)(in->my_units_gold[u] >= 100);


        // ---- 扫描: 5 行就地载入 + 行级 LUT 选择(pack→pext 串行链退役) ----
        uint16_t rowsel[5];
#if defined(__AVX2__)
        {
            const __m256i v2s = _mm256_set1_epi32(2);   // 挑食: 只标 ≥3 整格(≥2 版判负: 1184视2388)
            const __m256i vm3 = _mm256_set1_epi32(-3);
            int lsh = SCT.lsh[sc];
            uint32_t colv = SCT.colv[sc];
            uint32_t bslv[5];                    // 弹片按行暂存, 取代 bombm 的 15 位打包
            int cb = SCT.cb[sc];
            (void)0;                             // (弹片写已改行内 pop-loop, 见扫描尾)
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {
                int rr = sr - 2 + i;
                uint32_t rowok = (uint32_t)0 - ((unsigned)rr < (unsigned)N);
                int cr = TT.rclv[rr + 2];
                __m256i vrow = _mm256_loadu_si256(
                    (const __m256i*)&in->grid[cr][cb]);
#if defined(__AVX512VL__)
                uint32_t g8 = (uint32_t)_mm256_cmpgt_epi32_mask(vrow, v2s);
                uint32_t b8 = (uint32_t)_mm256_cmpeq_epi32_mask(vrow, vm3);
#else
                uint32_t g8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpgt_epi32(vrow, v2s)));
                uint32_t b8 = (uint32_t)_mm256_movemask_ps(_mm256_castsi256_ps(
                    _mm256_cmpeq_epi32(vrow, vm3)));
#endif
                uint32_t rv = colv & rowok;
                rowsel[i] = TT.bestrow[i][(((g8 << 2) >> lsh) & 31u) & rv];
                bslv[i] = ((((b8 << 2) >> lsh) & 31u) & rv);
                // 弹记全窗: LUT 一轮 3 步, ±1 行记法让第 2/3 步踩盲区雷(158287 烧 335 案)
            }
#pragma GCC unroll 5
            for (int i = 0; i < 5; ++i) {        // 弹行片写(空片写=无操作, 零分支;
                int rr = sr - 2 + i;             //  越界行 bsl 恒 0, 写进垫行无害免钳位)
                // 省掉 pack/unpack 的 <<(i*5) 与 >>(i*5)&31; 位移改 64 位消 cmov(等价性见 golf3)。
                g_s.bombbit[rr + 3] |= (uint32_t)(((uint64_t)bslv[i] << (unsigned)sc) >> 1);
            }
        }
#else
        rowsel[0] = rowsel[1] = rowsel[2] = rowsel[3] = rowsel[4] = 0xFFFF;
        for (int i = 0; i < 5; ++i) {            // 标量参考(仅本机测试)
            int rr = sr - 2 + i;
            if ((unsigned)rr >= (unsigned)N) continue;
            for (int j = 0; j < 5; ++j) {
                int cc = sc - 2 + j;
                if ((unsigned)cc >= (unsigned)N) continue;
                int v = in->grid[rr][cc];
                if (v > 2) {
                    uint16_t e = TT.bestrow[i][1u << j];
                    if (e < rowsel[i]) rowsel[i] = e;
                } else if (v == -3) g_s.bombbit[rr + 3] |= 1u << (cc + 1);
            }
        }
#endif
        // (金格与弹格规则上不共存 —— 目标无需剔弹, 途中避弹由 pass01 管)

        // ---- 目标: 行级 min 收敛(pext 级联退役, 5 独立载入换串行链) ----
        int tgr, tgc;
        int blind;
        {
            uint16_t b01 = rowsel[0] < rowsel[1] ? rowsel[0] : rowsel[1];
            uint16_t b23 = rowsel[2] < rowsel[3] ? rowsel[2] : rowsel[3];
            uint16_t b0123 = b01 < b23 ? b01 : b23;
            uint16_t bv = b0123 < rowsel[4] ? b0123 : rowsel[4];
            int w = bv & 31;
            // 三路目标: 整格(≥3) > 站金残值(标量兜底, 折返双吃) > 锚点
            int has = -(int)(bv != 0xFFFF);
            int standing = -(int)(in->grid[sr][sc] > 1);   // 1金残渣不折返: 回哨位张网
            int selfm = ~has & standing;
            blind = ~has & ~standing;
            tgr = ((sr - 2 + TT.d5[w]) & has) | (sr & selfm) | (g_s.anch_r[u] & blind);
            tgc = ((sc - 2 + TT.m5[w]) & has) | (sc & selfm) | (g_s.anch_c[u] & blind);
        }

        uint32_t blk[N + 2];                     // blocked 位图预合成(扫描后! 含当轮新见弹)
        for (int bi_ = 0; bi_ < N + 2; ++bi_)
            blk[bi_] = g_s.bpw[bi_] | (rich & g_s.bombbit[bi_ + 2]);

        // ---- 导向 ----
        int dr0 = tgr - sr, dc0 = tgc - sc;
        dr0 = dr0 < -3 ? -3 : (dr0 > 3 ? 3 : dr0);   // 钳进 LUT 域:
        dc0 = dc0 < -3 ? -3 : (dc0 > 3 ? 3 : dc0);   // 远目标前3步与逐步串行同构
        {                                        // 折返消融: d==0 由 SL[3][3] 保持 STAY
            int ir = dr0 + 3, ic = dc0 + 3;
            const uint8_t* pa = SL.fact[ir][ic];
            const int8_t* xr = SL.pdr[ir][ic];
            const int8_t* xc = SL.pdc[ir][ic];
            unsigned ok = (~(blk[sr + xr[0] + 1] >> (sc + xc[0] + 1)) & 1u) &
                          (~(blk[sr + xr[1] + 1] >> (sc + xc[1] + 1)) & 1u) &
                          (~(blk[sr + xr[2] + 1] >> (sc + xc[2] + 1)) & 1u);
            if (ok) {
                acts[0] = pa[0]; acts[1] = pa[1]; acts[2] = pa[2];
                pext = pa;                       // 可续写: 尾段与本段同表同目标
            } else {
                // 受阻(罕见, 锁图后墙全知): 单步谨慎, 其余 STAY, 下轮自愈
                int a = steerStep(sr, sc, tgr, tgc,
                                  g_s.last_r[u], g_s.last_c[u], rich);
                if (a >= 0) acts[0] = a;
            }
        }

        // ---- 开局行军/远征转场(盲轮才走; mode==1 才进, 稳态恒不取) ----
        if (__builtin_expect(g_s.mode == 1, 0)) {
            if (blind) slowMove(in, u, sr, sc, rich, acts);
        }
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
        int bd = blind != 0;
        if (u == 0) {
            blind0 = bd; ext0 = pext;
        } else if (bd != blind0) {                // 恰好一个盲 ⇒ 把预算给能用的那个
            const uint8_t* pe = blind0 ? pext : ext0;
            if (!pe) pe = SL.fact[3][3];          // 非 LUT 路径: 六格恒 STAY 的表项
            if (blind0) {                         // u1 是生产者: 头段下移到跨度起点
                out.actions[0] = out.actions[3];
                out.actions[1] = out.actions[4];
                out.actions[2] = out.actions[5];
                cur = 0;
            } else cur = 6;                       // u0 是生产者: 头段已在槽 0, 零搬移
            out.actions[3] = pe[3];
            out.actions[4] = pe[4];
            out.actions[5] = pe[5];
        }
    }

    out.k = cur;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = g_s.vp_buy;                         // 稳态恒 0(慢开局层才会置 2)
    return out;
}

}  // namespace

// 布局归一化死垫：本次改写缩小了 decide, 使 moveDecision 入口模 64 掉出已证最优档 0x10。
// 四档扫描已证 0x20/0x30 各 +11.67ns, 故补 48B 永不执行的 nop 把入口移回 0x10 档,
// 否则测到的 cycles 差会被布局税污染。改动 decide 体积后必须重新核对并调整垫长。
asm(".space 96, 0x90");

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
        return decide(input);
    } catch (...) {
        return SAFE_OUT;
    }
}
