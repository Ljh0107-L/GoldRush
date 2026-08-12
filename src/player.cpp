// player.cpp — GoldRush 2.0 反射机 + 三图指纹/慢开局 (2026-08-08)
//              选路器换代: 环距最近选靶 → 32 条 3 步路径的「踏入新格金额」打分 (2026-08-12)
//
// 战绩(换代前): P50 中位 200ns / P90 ~292 / 收入中位 ~1515; 对 Tiuntled-1 先手 85-96%
// 成本模型(设计新算法前必读): 见 INFRA.md —— 壳40ns + 载荷~2ns/条 + 指令×0.1454ns
// ⚠ 0.1454 是全函数**平均**价, 不是删指令的**边际**价: 本函数受依赖链/访存约束(IPC~4),
//   实测删 84 条只换回 5.6 cycles(≈0.025ns/条, 比均价低 6 倍)。删指令≠变快, 验收看 cycles。
//
// ============ 为什么换代(全部是平台实测, 不是推测) ============
// 对手 T-1(model 87478) map1 单局 2302 金 / 我方旧版 2110.7, 且真实对局逐局差 −885。
// 拆开后病灶只有两条, 且指向同一个器官:
//   (a) 移动预算被浪费: 旧版每单位轮只踏上 1.40 个新格、STAY 28.0%、66.3% 的轮重踏 2 格;
//       T-1 踏 2.82 新格、STAY 5.9%、零重踏 ⇒ 中央命中率 43.1% vs 63.0%。
//       主犯不是「站金折返双吃」那一支, 是旧 `SLut` 的**早到折返预折叠**: 靶在曼1 时
//       LUT 生成「靶 → 退回自己 → 再回靶」, 一轮重踏 2 格; 曼2 时重踏 1 格。
//   (b) 座位丢了: 旧版定长 190-200ns, T-1 中位 150ns ⇒ T-1 拿走 78.5%(map1) 的先手,
//       而纯座位价值实测 map1 +355 / map2 +299 / map3 +552 金。座位由**当轮** cost 裁定
//       (dispatch_order[0] 与同轮更小 cost 吻合 8798/8798 = 100.00%, 零例外) ⇒ 不能蓄力,
//       只能压当轮; 且门槛是**中位数**(抢座概率 = P(对手当轮 > 我方当轮))。
// ⇒ 本次换代必须**同时**买回命中率与座位, 即「更聪明」且「更便宜」。预算来源见下节。
//
// ============ 候选集为什么是 48 条(不是 125, 也不是 A*) ============
// 每单位每轮 3 步。全动作空间 5³=125, 但绝大多数是自废: 立即掉头把一步换成一口 22.75%
// 的残值(见引擎语义), 而 STAY 不结算金币。按「踏入的互异新格集合」去重后只剩三族:
//   L 族 24+8=32 条: 3 步全走、不立即掉头、三格全在 5×5 内。终点 24 条落在曼3 的
//       8 个骑士格(每格 3 条) + 8 条折回曼1。(直线三连 4 条被删: 终点切比雪夫 3, **出视野窗**)
//   S 族 12 条: 2 步 + 尾 STAY, 终点曼2 的 8 格。
//   O 族  4 条: 1 步 + 双尾 STAY, 终点曼1 四邻。
// T-1 实测位移分布逐项对上这三族: (2,1) 82.4% = L单调 / **(2,0) 14.8% + (1,1) 1.8% = S 族**
// / (1,0) 0.8% = L折回或 O / (3,0) 类 **0 次** = 被出窗规则排除 / 切比雪夫恒 ≤2(6040/6040
// 零例外)。且它 STAY 步数分布「零 STAY 83.3% / 恰好 1 个 15.8%」与 S 族占比 16.6% 咬合。
// ⚠ S/O 族**不是**为了模仿 T-1 才加的 —— 它们在「含墙窗口」里是**正确性必需**: 见下条。
// 48 条掩码塞进一个 uint64 ⇒ 打分全程位运算 + cmov, 无数据相关分支
// (INFRA §3.9 判例: 同一算法分支形态 +40~50ns、constexpr 表 + cmov 掩码形态 −10ns)。
//
// ============ 含墙窗口的正确性(map3 是压力图: 78 面墙, 中央 9×9 仅 27 格开放) ============
// 陷阱: 被阻的一步 = 原地不动, **但后续动作仍从原位继续执行** ⇒ 实际走位与预算掩码发生
// **偏移**, 不是简单退化 ⇒ 「掩码 AND 金位图」在含墙窗口里会算错分。
// 本版的处理是**结构性**的, 不靠事后修正:
//   只有「三格(或两格/一格)全部可踏入」的序列才进候选 —— 一次 `cand &= ~thru[阻挡格]`
//   即完成判定。被阻序列被**整条剔除**, 故留在 cand 里的每一条都保证逐步按计划执行,
//   掩码与实际走位恒等 ⇒ 偏移问题不存在。
// 排掉它们不损失最优性(被阻路径严格更差: 白扔一步), 而**剔除后候选集不会被过度剪空**,
// 正是因为 S 族与 O 族在场: 走廊尽头 L 族全灭时仍有 2 格/1 格候选可选。
// 只有「四邻皆墙」才会 cand==0(此时任何输出都等价于不动, 无罚金)。
//
// ============ 预算从哪来(净估 −340 条, 与新打分 +100 条相抵后仍下降) ============
// 1. **scan 的标量胶水整块下岗**: 旧 scan 197 条/单位里只有 5 条 vmovdqu + 10 条向量比较是
//    真活(INFRA §2.4), 其余是「把 8-lane mask 对齐成局部 5bit → 查 bestrow → pext 环距重排」。
//    新版把 5 行 movemask 直接拼成**一个 stride-8 的 40 位窗口位图**(bit 8i+j ↔ 窗口格
//    (i-2, j-2)), 列对齐仍用现成的 SCT.lsh 一次 shrx ⇒ 之后全部打分都在 64 位整数域完成。
// 2. **跨轮炸弹记忆 bombbit(42/单位) + blk[19] 位板合成(39) 整块下岗**。理由是硬的:
//    候选路径的格子**全部落在自己 5×5 内**, 而默认视野半径 2 ⇒ 窗口内炸弹/墙当轮直接可见、
//    100% 完整, 不需要记忆。连带 pass01 / steerStep / escapeStep 一起下岗 ——
//    **「受阻自愈」不是被删, 是被「枚举本身认墙」原生取代**(旧版只能事后单步谨慎 + 下轮自愈,
//    新版直接在 32 条里挑一条完全不撞墙的)。INFRA §2.6 记该器官值 +415 金, 不得净损失。
//
// ============ 引擎语义(源: sim/engine.py:1014-1080, 与平台日志逐轮吻合) ============
// * `if not moved: return` ⇒ **STAY 与撞墙/撞边界/撞自己另一单位都不结算金币**。
//   金子只在**踏入**时结算 —— 站在金子上不结算(铁证: 局 214784 round 40, T-1 起点即 1 金格,
//   走开后 pickup=0)。故「脚下残值」只能靠掉头回踩来吃, 而那要花 1 步换 ceil(0.2275v)。
// * 拾取 = ceil(0.65v), 地面留 floor(0.35v) ⇒ 新鲜堆首口 0.65 vs 二口 0.2275
//   ⇒ **一个新鲜金堆 = 2.86 个第二口** ⇒ 本版一律不回头, 与 T-1 零重踏一致。
// * 撞墙只降级为不动, **无罚金** ⇒ 极端死角里「所有路径都撞墙」是安全的(只白费步)。
// * 踏入炸弹烧 ceil(held/10); 踏入 ≥3 NPC 的格烧 ceil(held/20)。
//
// ============ 每轮决策 loop ============
// 入口: moveDecision → try{ decide } catch{ SAFE_OUT }   (输出全路径可证合法, 无钳位)
// 0. 慢开局层(mode!=FAST 才进, 冷路径; map1 锁图后 round≥4 退场, 稳态零接触): 原样保留
//    0.1 学墙(visited 单bit门控) 0.2 三图墙数指纹(map2/3 角落同构 → round 0 买 vp=2 终判)
//    0.3 中央双驻守锚点 (6,8)/(11,8) 0.4 三图皆不吻合 → 陌生图懒学习
// 1. 新局检测(round 回绕) → 重置状态, bpw = 边界哨兵(墙由指纹/学习灌入)
// 2. 对每个单位(双全管线, 无轮换):
//    2.1 scan: 5 行就地 AVX 载入 → 4 个 stride-8 窗口位图
//        (金 v≥1 / v≥2 / v≥5 三档 + 阻挡 v<0 = 墙|弹|雾, 越界由 rvm/colv 并入阻挡)
//    2.2 剔除撞墙/踩弹的路径(pop-loop over 阻挡格 ∩ 可达格)
//    2.3 面值分档贪心: 高档金格优先, 3 轮无分支收敛 (cand &= thru[格], 空交集则不收敛)
//    2.4 平局裁决: 锚点向心 → 跨轮顺向(动量) → ctz(默认取覆盖面最大的单调路径)
//    2.5 解表出动作
// 3. 输出: k=3, order=持金多者先走, vp=慢开局层裁定(稳态恒 0)
//
// ============ 已退役的防御(可证冗余, 详见 CHANGELOG 军规 12) ============
// sanitize 输出钳位 / 入口坐标钳位 / pass01 队友检查(撞位实测 0 轮) —— try/catch 永不下岗
#include <cstdint>
#include <cstring>
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include "game_api.h"

// 消融开关(默认 = 本版行为; 用于把各条病因分别定价, cycles 表见 CHANGELOG)
#ifndef PV_TIER
#define PV_TIER 1        // 1=面值分档贪心  0=不分档(金格按位序取, 隔离「按面值加权」这一刀的价)
#endif
#ifndef PV_LONG
#define PV_LONG 1        // 1=纳入 L 族(3 格/轮, 即「到靶还有余步就接着找下一个靶」)
#endif
#ifndef PV_SHORT
#define PV_SHORT 1       // 1=纳入 S/O 族(2 格/1 格; 含墙窗口的正确性所需, 亦覆盖 T-1 的 (2,0))
#endif
#ifndef PV_MOM
#define PV_MOM 0         // 跨轮顺向平局项。**默认关**: 顺向偏置是纯金子求和的**涌现**产物
                         // (刚扫空的 3 格在身后, 剩下的金自然偏前方), 不必付跨轮状态与每候选加权。
                         // 实测支撑: T-1 「连续两轮位移精确重复」仅 12.8% ≈ 随机 12.5%(无固定路线),
                         // 而同向 54.6%(27σ) —— 后者可由「身后已扫空」单独解释。开=34 条指令。
#endif
#ifndef PV_ANCH
#define PV_ANCH 1        // 1=锚点向心平局项(INFRA §2.6 记该器官 +498 金, 不建议关)
#endif
#ifndef PV_TIE
#define PV_TIE 0         // 平手规则: 0=取覆盖面最大(3 格) 1=取实际移动最少(T-1 式, 自动长出 1-STAY 家族)
                         // T-1 实测: 被放弃的第 3 步在 **97.3%** 的 1-STAY 轮里「往前也没金」(n=655),
                         // 即它只在**白走**时才不动 ⇒ 两档在金币上等价, 只在「空步的位置价值」上分歧,
                         // 而那是未定谳的经验问题 ⇒ 两档都上平台。
#endif
#ifndef PV_GN
#define PV_GN 3          // 面值贪心轮数(一条路径最多踏 3 格 ⇒ 3 轮饱和; 2 轮省 31 条)
#endif
#ifndef PV_RTAB
#define PV_RTAB 1        // 1=阻挡剔除用 5 张定长行表(+1280B rodata) 0=变长 pop-loop。逐位等价, 只差成本
#endif
#ifndef PV_ACTOR
#define PV_ACTOR 1       // 角色格并入阻挡的范围: 0=不并 1=只队友 3=队友+两个可见敌方单位
                         // **落库值 = 1**, 依据是 24 局同窗配对 A/B(两臂入口同校到 0x1950, 排除布局税):
                         //   ACTOR=1 我方 cost 中位 140.8ns / 先手率 61.9% / margin −64.8±51.6
                         //   ACTOR=3 我方 cost 中位 160.0ns / 先手率 39.8% / margin −134.4±80.0
                         //   ⇒ 配对差 +69.7±95.2(0.73σ 不显著), 但座位算术自闭合:
                         //     22.1pp × 0.71 金/轮 × 500 = +78 金预测 vs 实测 +69.7 ⇒ 座位就是通道。
                         //   实测斜率 1.15 pp先手/ns (工作点 141-160ns, T-1 中位 152.5)。
                         //   ACTOR=1 同时把 P90 从 231.7 压到 215.0 —— 极速优化奖按榜 P90 评。
                         // 3 的收益是真的(零配额定价, tests/pathaudit.cpp, n=3000 步/图), 且在慢对手上也成立:
                         //   被敌格挡掉的步 76/94/145 → 3/0/8; 连带撞墙(偏移级联) 32/21/37 → 28/0/10;
                         //   新格 2.82/2.86/2.53 → 2.88/2.98/2.70; 当轮拾取 +27/+79/+36 金;
                         //   成本 +52 条 = 7.6ns ≈ −22 金座位 ⇒ 净 +5~+57 金, 三图同号。
                         //   慢对手场地局回放(64ms/59ms/564μs 三个对手): 敌格挡步 71/4/74 → 0/0/0,
                         //   当轮拾取 +35/+0/+38 金。⇒ **它不是无效, 是买不过座位** ——
                         //   只在「我方与对手 cost 相差 <20ns」的交叉点上净负(那里 1.15pp/ns 最贵)。
                         // ⇒ 若某天我方 cost 远离对手中位(例如再降 40ns), 应重新考虑打开 3。
#endif
#ifndef PV_RICH
#define PV_RICH 0        // 1=还原富度门(持金≥100 才避弹) 0=恒避弹(便宜 50 条; 旧库实测两者 ±0)
#endif

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

// mode: 0=FAST(稳态, 冷层零接触) 1=OPENING(指纹/行军) 2=LAZY(陌生图懒学习)
// map_id: -1 未定 0/1/2 已锁 -2 陌生图
struct alignas(64) State {
    uint32_t bpw[N + 2];     // 墙|边界哨兵位图(bit c+1; 仅冷层用: 指纹比对/行军 BFS/锚点修正)
    int8_t last_r[2], last_c[2];
    int16_t last_round;
    uint8_t mode;
    int8_t map_id;
    uint8_t cand;            // 候选图位掩码(bit m)
    uint8_t vp_buy;          // 本轮 vp 输出(稳态恒 0; 也用作"上轮买了视野"标记)
    int8_t anch_r[2], anch_c[2];
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

// ============ 锚点常量 + 编译期引信 ============
// 为什么需要引信: 存在一条从烘焙墙表到每轮热路径的通路 ——
//   误锁/锁图 → slowTick 里的「锁定灌表」(`bpw[r+1] = 0xFFFC0001u | BAKED_W[m][r]`)
//   → fixAnchor() 用 wallbit() 读 bpw → 写 g_s.anch_r/anch_c
//   → decide() 平局裁决段的 `#if PV_ANCH` 锚点向心项(**每轮、per-unit 热循环内**)。
// ⚠ 互引一律以**符号/代码形状**为主键, 不用行号: 本段自己就是判例 —— 引信落库时
//    这四处全写了行号, 而引信自身插入的 42 行当即让四个行号全部过期(:404/:339/:349/:652
//    各偏 +42), 即「说明地雷在哪」的注释反倒成了死指针。用 grep 找符号永不过期。
// 它今天无害**不是因为冷热分层, 而是因为表里的值恰好如此**: 锚点列 8 ⇒ bit 9 = 0x200,
// 而三张表第 6/11 行六个值(map1 0x4010/0x1040, map2 0x8888/0x0000, map3 0x3de0/0x3de0)
// & 0x200 全为 0(map3 的 0x3de0 里 bit 9 正好是那唯一的洞)。
// ⇒ 重烘 BAKED_W / 加第四张图 / 移动锚点, 这条路会**无声打开**。
// 本引信把"被巧合关住"变成"被设计关住"。
constexpr int ANCH_R0 = 6;    // unit 0 锚点行
constexpr int ANCH_DR = 5;    // 两单位锚点行距(切比雪夫距离 5; 原字面量 5*u)
constexpr int ANCH_C  = 8;    // 锚点列(三图同锚, 刻意为之)

// 语义 = 「BAKED_W[m] 第 r 行第 c 列是否为墙」。**不复用 wallbit()**: 那个读运行期
// g_s.bpw, 不能编译期求值。⚠ 本谓词与 wallbit() / slowTick 的锁定灌表是**两处独立实现
// 同一位约定**(bit c+1), 是未来的漂移点 —— 改任一处必须同步另一处。
constexpr bool bakedWallAt(int m, int r, int c) {
    return ((BAKED_W[m][r] >> (c + 1)) & 1u) != 0u;
}

constexpr bool anchorsClearOfBakedWalls() {
    // ① 内部性条 —— 守护主条**自身的有效性**, 不是形式主义:
    //    slowTick 锁定时灌的是 `0xFFFC0001u | BAKED_W[m][r]`, 哨兵占 bit 0(c=-1)
    //    与 bit>=18(c>=17), 而本谓词只读 BAKED_W、不读哨兵 ⇒ 锚点列一旦挪出 0..N-1,
    //    主条会**静默地读不到哨兵位而误判为「安全」**。
    if (ANCH_C < 0 || ANCH_C > N - 1) return false;
    if (ANCH_R0 < 0 || ANCH_R0 > N - 1) return false;
    if (ANCH_R0 + ANCH_DR < 0 || ANCH_R0 + ANCH_DR > N - 1) return false;
    // ② 行不重合条: 两单位锚点必须是不同格(ANCH_DR==0 会让两单位共锚而不触发任何现有检查)
    if (ANCH_DR == 0) return false;
    // ③ 主条: 所有图 x 所有单位, 锚点格非墙
    for (int m = 0; m < 3; ++m)
        for (int u = 0; u < 2; ++u)
            if (bakedWallAt(m, ANCH_R0 + ANCH_DR * u, ANCH_C)) return false;
    return true;
}
static_assert(anchorsClearOfBakedWalls(),
              "anchor sits on a baked wall: the mis-lock -> fixAnchor -> anch_r/anch_c -> "
              "hot tie-break path is no longer closed. See CHANGELOG: the closure is a "
              "property of BAKED_W's values, not of the cold/hot layering.");

// 锁图后复核墙表的窗口长度。**24 不是"留了 6 倍余量" —— 那个说法是删失(censoring)产物。**
// 去窗重测(3650 局, 每张已知图的每个合法单格编辑): 首次矛盾轮 中位 25 / p90 237 / p95 339
// / max 495, 且 18.9% 永不产生矛盾 ==> **窗口正坐在中位上**, 且 24 之后无悬崖(证据散布 25-499)。
constexpr int VERIFY_ROUNDS = 24;

// map1 开局烘焙路线(BFS 最优 4 轮出角; 起点恒 (0,0)/(16,16); 仅 map_id==0 使用)
constexpr uint8_t ORT_A[2][4][3] = {
    {{1,3,3},{3,1,3},{1,3,1},{3,1,1}},           // u0 (0,0)->(6,6)
    {{0,2,2},{2,0,2},{0,2,0},{2,0,0}},           // u1 (16,16)->(10,10)
};
constexpr int8_t ORT_R[2][4] = {{0,1,2,4},{16,15,14,12}};
constexpr int8_t ORT_C[2][4] = {{0,2,4,5},{16,14,12,11}};

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

// ============ 候选路径表(48 条, 全部 constexpr 生成) ============
// 生成规则(与上文「候选集为什么是 48 条」一致, 改这里等于改战术, 请连同注释一起改):
//   * 每步 ∈ {上,下,左,右}, 尾部可补 STAY(STAY 不结算金币, 只能补在尾部)
//   * 不立即掉头 (a[t+1] != a[t]^1) ⇒ 各格必互不相同, 天然零重踏、无需 0.35^n 递减项
//   * 各格全在自己 5×5 内 (超窗则金额不可知, 且 T-1 实测从不走出去)
//   * 排序 = 覆盖面降序: L单调(0..23) → L折回(24..31) → S(32..43) → O(44..47)
//     ⇒ ctz 天然偏好扫得更开的那条, 平局裁决不必再比长度
struct PathT {
    uint64_t thru[46];       // 格位 → 踏入该格的路径集合(bit p); 45 = 哨兵, 恒 0
    uint64_t cell[48];       // 路径 → 各格位掩码 | (动作 a0|a1<<3|a2<<6) << 52
    uint64_t towR[3], towC[3];  // **可分**方位项: 行/列各按 sgn(想去 − 现在)+1 索引, 语义 = 「不后退」
    int8_t sgi[41];          // sgn(x)+1 查表, 索引 x+20 (x ∈ [-20,20]; 锚点最远 |Δ|=16)
    uint64_t reach;          // 可踏入的格集(20 格: 曼1/2/3; 不含自己与曼4 四角)
    uint64_t famL, famS, famO;   // 三族掩码(消融开关用)
    uint64_t rowok[17], colok[17];   // 越界剔除: 全程留在盘内的路径集(**行/列可分**, 见下)
    uint64_t rclr[5][32];    // 阻挡剔除的常量表: rclr[i][p] = ~⋃{thru[格] : 窗口第 i 行第 j 列被挡}
                             // 把变长 pop-loop 换成 5 次定长查表 ⇒ 恒定成本 + 压 P90(见 decide)
    int8_t rcl[21];          // 行钳位(仅为 AVX 载入地址合法; 幻影数据由 rowok 兜掉)
    constexpr PathT()
        : thru(), cell(), towR(), towC(), sgi(), reach(0),
          famL(0), famS(0), famO(0), rowok(), colok(), rclr(), rcl() {
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
                        if (fam <= 1)      famL |= 1ULL << n;
                        else if (fam == 2) famS |= 1ULL << n;
                        else               famO |= 1ULL << n;
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
        // 越界剔除**可分**: 路径 p 全程在盘内 ⇔ (它的所有行偏移合法) ∧ (所有列偏移合法),
        // 两个条件各只依赖 sr / sc ⇒ 两张 17 项表 + 一次 AND, 取代把「越界」并入阻挡位图
        // 再逐格 pop-loop 的旧写法(单位贴边时那条变长循环能多跑 ~10 次 ≈ +60 条/单位,
        // 且它正是 map2 首版实测反涨 +45 条的来源)。
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
// 候选全集(消融开关按族裁剪; 默认三族全在)
constexpr uint64_t ALLP = (PV_LONG ? PT.famL : 0ULL) | (PV_SHORT ? (PT.famS | PT.famO) : 0ULL);
static_assert(PT.cell[47] != 0, "48 条候选未生成满");
static_assert((PT.reach & ~WM) == 0, "可踏入格必须落在窗口有效位内");
static_assert(PT.thru[WSENT] == 0, "哨兵格位必须无路径");
static_assert(PT.thru[WSELF] == 0, "自己所在格不可被踏入(零重踏由构造保证)");
static_assert(__builtin_popcountll(PT.reach) == 20, "可踏入格应为 20 格");
static_assert(__builtin_popcountll(PT.famL) == 32 && __builtin_popcountll(PT.famS) == 12
              && __builtin_popcountll(PT.famO) == 4, "三族条数应为 32/12/4");
static_assert(ALLP != 0, "候选集不可为空");

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
void slowTick(const GameInput* in) {
    int rad = g_s.vp_buy == 2 ? 4 : 2;           // 上轮买了 9×9 → 本轮窗口半径 4
    g_s.vp_buy = 0;
    unsigned learned = 0;
    unsigned conflict = 0;
    for (int u = 0; u < 2; ++u) {                // 站格单bit门控, 按单位各自判(站过⇒窗口已学)
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        if (g_s.visited[sr] >> (sc + 1) & 1u) continue;
#if !PV_SKIPSEEN
        learned = 1;
#endif
        int r0 = sr - rad < 0 ? 0 : sr - rad, r1 = sr + rad > 16 ? 16 : sr + rad;
        int c0 = sc - rad < 0 ? 0 : sc - rad, c1 = sc + rad > 16 ? 16 : sc + rad;
#if PV_SKIPSEEN
        // 逐行精确跳过: 该行窗口内每格都已观测 => seen/bpw 都不可能改变 => 该行扫描是纯开销。
        // 零学习损失是**构造性**的(只跳没东西可学的行), 不依赖收敛假设 —— 实测新墙最晚出现在
        // round 447, 任何"连续 K 轮无新墙则停"都会在晚期探索的图上误停。
        // 前提: 地形静态。已核 —— 引擎从不改 rows; 炸弹只写 board 且从墙上被过滤掉。
        // 必带 map_id<0: map_id>=0 时本循环做的是 conflict 比对(不写 seen), 那时 seen 已停更,
        // 窗口会"看起来全知"而实际正需要比对 => 跳过会掩盖误锁。
        // learned 降为"是否真扫过任一行": 全行皆跳 => seen/bpw 未变 => 指纹(:435)与
        // fixAnchor(:456) 结果必与上轮相同 => 不置位是精确的, 且这一支顺带消掉整次触发。
        const unsigned wm_ = (unsigned)((1u << (c1 + 2)) - (1u << (c0 + 1)));
        const unsigned skipok_ = (unsigned)(g_s.map_id < 0);
#endif
        for (int r = r0; r <= r1; ++r)
#if PV_SKIPSEEN
        {
            // 雾格永不进 seen => 含雾行 unk!=0 => 不跳。失效方向是"少跳"而非"错跳"。
            if (skipok_ && ((~g_s.seen[r]) & wm_) == 0u) continue;
            learned = 1;
#endif
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
#if PV_SKIPSEEN
        }
#endif
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
#if PV_NOLOCK
            // 只去掉"锁"这一步, **不动指纹淘汰阶段本身**: 唯一候选时按陌生图处置
            // (与上面 cand==0 那条分支同形)。
            // 为什么不是"直接把 map_id 初始化成 -2": 那样会让 fixAnchor 从第 0 轮就开始
            // 生效, 而现役在指纹未判完前(map_id==-1)**不**走 :478 那条锚点重验分支
            // ⇒ 陌生图行为会被无意改掉。实测那个版本在 10 张陌生图上 -43.68 +- 21.71 金、
            // log 120/120 全变 ⇒ 已弃用。
            // 本形态下, 陌生图上 cand 自然归 0 的局**完全不变**, 唯一被改的是"本会锁图"
            // 的局 = 官方三图 + 误锁图 ⇒ 正是本改动的目标范围。
            g_s.map_id = -2;
            fixAnchor(0); fixAnchor(1);
#else
            int m = __builtin_ctz(g_s.cand);     // 唯一候选: 锁图直灌
            g_s.map_id = (int8_t)m;
            for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u | BAKED_W[m][r];
            fixAnchor(0); fixAnchor(1);
#endif
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
void slowMove(const GameInput* in, int u, int sr, int sc, int* acts) {
#if PV_NOROUTE
    // 恒假(map_id 取值域 -2..2)⇒ 只关 map1 烘焙开局路线, **墙表保留** ⇒ 与去锁分离,
    // 用来把 -54.47 金拆成"墙表份额"与"路线份额"。刻意不删 body: 避免 unused 告警,
    // 且保证与 PV_NOROUTE=0 的文本差只在这一行。
    if (g_s.map_id == 99) {
#else
    if (g_s.map_id == 0) {                       // map1: 烘焙路线原样(保逐位等价)
#endif
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
    // 运行时 BFS(已知墙, 雾当可通行) → 取前 3 步
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
    int seq[3 * 3];                              // 全程回溯, 环形存前 3
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
    if (in->round <= g_s.last_round) {           // 新局: 重置; 墙由指纹/学习灌入
        memset(&g_s, 0, sizeof(g_s));
        g_s.bpw[0] = g_s.bpw[N + 1] = ~0u;
        for (int r = 0; r < N; ++r) g_s.bpw[r + 1] = 0xFFFC0001u;
        g_s.mode = 1; g_s.map_id = -1; g_s.cand = 7;
        for (int u = 0; u < 2; ++u) {            // 三图同锚: 中央窗口分驻, 切比雪夫距离 5
            g_s.anch_r[u] = (int8_t)(ANCH_R0 + ANCH_DR * u); g_s.anch_c[u] = (int8_t)ANCH_C;
            g_s.last_r[u] = (int8_t)in->my_units[u].row;   // 首轮 Δ=0 ⇒ 动量项零效应
            g_s.last_c[u] = (int8_t)in->my_units[u].col;   // (不可填锚点: 那会让首轮动量指向反向)
        }
    }
    g_s.last_round = (int16_t)in->round;

    // 慢开局层(学墙/指纹/锚点/vp)。原门控为 `mode != 0` 外套站格门控, 故一旦锁图转 FAST,
    // slowTick 再不被调用 —— 这正是误锁不可自愈的根因(实测 −689 金/局, 静默)。
    // 现为: mode==1 恒进; mode==2(陌生图懒学习)照旧按站格门控长驻; **FAST 只在开局
    // VERIFY_ROUNDS 轮内**按站格门控进入, 用于复核锁定墙表(界 X 的定价: 见 CHANGELOG)。
    if (__builtin_expect(g_s.mode == 1
            || ((g_s.mode == 2 || in->round <= VERIFY_ROUNDS)
                && (!(g_s.visited[in->my_units[0].row] >> (in->my_units[0].col + 1) & 1u)
                 || !(g_s.visited[in->my_units[1].row] >> (in->my_units[1].col + 1) & 1u))), 0))
        slowTick(in);

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
            // 角色格也是阻挡: 引擎 `player_cells_except` 把**任一玩家单位**所在格判为不可进入
            // (sim/engine.py:1030), 只降级为不动、不罚金 —— 但那是白扔一步。
            // grid 不标角色, 故必须从 my_units / visible_enemies 单独并入。
            // 实测(录制轨迹回放, 3000 步/局): 不并入时被队友挡掉 62/64/108 步(map1/2/3)。
            // 口径诚实: 只封「本轮起点格」。若队友先动走开, 这一封是保守的(白让一条候选);
            // 若它没动, 这一封正好省下一步。两者都不会产生非法输出。
            // 恒预测门控形态(INFRA §3 罚则1: 低频功能用门控, 掩码版每轮白付全款) ——
            // 无分支版实测反涨 +9 条, 因为「角色落在 5×5 内」在稳态里几乎恒假。
#if PV_ACTOR
            const Position* act[PV_ACTOR] = {&in->my_units[1 - u]
#if PV_ACTOR > 1
                , &in->visible_enemies[0], &in->visible_enemies[1]
#endif
            };
#pragma GCC unroll 3
            for (int e = 0; e < PV_ACTOR; ++e) {
                unsigned ti = (unsigned)(act[e]->row - sr + 2);
                unsigned tj = (unsigned)(act[e]->col - sc + 2);
                if (__builtin_expect((ti < 5u) & (tj < 5u), 0))
                    bd |= 1ULL << (8u * (ti + 1u) + tj);
            }
#endif
        }
#if PV_RICH
        if (!(in->my_units_gold[u] >= 100)) {    // 富度门: 穷单位对弹「近乎」透明(旧行为)
            uint64_t bomb = 0, m = bd;           // 弹格 = bd 中 grid==-3 的那些, 逐格回读
            while (m) {
                int b = __builtin_ctzll(m); m &= m - 1;
                int rr = sr - 3 + (b >> 3), cc = sc - 2 + (b & 7);
                if ((unsigned)rr < (unsigned)N && (unsigned)cc < (unsigned)N
                    && in->grid[rr][cc] == -3) bomb |= 1ULL << b;
            }
            bd &= ~bomb;
        }
#endif
#if !PV_TIER
        g5 = 0; g2 = 0;                          // 消融: 不分档 ⇒ 贪心按位序取金格, 不比面值
#endif

        // ---- 剔除撞墙/踩弹/出盘的路径(结构性正确: 留下的每条都保证逐步按计划执行) ----
        uint64_t cand = ALLP & PT.rowok[sr] & PT.colok[sc];
#if PV_RTAB
        // 定长版: 逐行 5 位切片查 rclr。与 pop-loop **逐位等价**(AND 结合律), 但成本恒定 ——
        // 变长版在 map3 实测跑 ~13 次/单位轮(墙 78 面), 且循环出口每轮误预测一次。
        cand &= PT.rclr[0][(bd >> 8) & 31] & PT.rclr[1][(bd >> 16) & 31]
              & PT.rclr[2][(bd >> 24) & 31] & PT.rclr[3][(bd >> 32) & 31]
              & PT.rclr[4][(bd >> 40) & 31];
#else
        {
            uint64_t m = bd;
            while (m) {
                int b = __builtin_ctzll(m); m &= m - 1;
                cand &= ~PT.thru[b];
            }
        }
#endif
        // cand==0 ⇔ 四邻皆墙/皆出盘(O 族也全灭) ⇒ 任何输出都等价于不动, 无罚金
        if (__builtin_expect(cand == 0, 0)) cand = 1ULL;

        // ---- 面值分档贪心: 高档金格优先, 3 轮无分支收敛 ----
        // 分档而非精确面值排序: 档内乱序的代价是「同档两格只能取一格时可能取小的那个」,
        // 上限 <1 口拾取; 换来的是全程无回读 grid、无排序网络。档界 (1..3) (4..8) (9..) 对齐
        // ceil(0.65v) 的 1-2 / 3-6 / 6+ 三段(热点格面值可达 20+, 故顶档留给它)。**1 金必须是有效靶**: T-1 实测 3 步内只有 1 金时
        // 仍 70.0% 踏上去(n=410), 而旧版 v1 落脚偏好 0.82 < 基准率 = 主动绕开会自动落袋的钱。
        {
            uint64_t t5 = g5, t2 = g2 & ~g5, t1 = g1 & ~g2;
#pragma GCC unroll 3
            for (int it = 0; it < PV_GN; ++it) { // 一条路径最多踏 3 格 ⇒ 3 轮饱和
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

        // ---- 平局裁决 ----
#if PV_ANCH
        {   // 锚点向心: 中央生成峰驻守(INFRA §2.6 该器官 +498 金, 217.5 金/ns —— 全库最高)
            uint64_t t = cand & PT.towR[PT.sgi[g_s.anch_r[u] - sr + 20]]
                              & PT.towC[PT.sgi[g_s.anch_c[u] - sc + 20]];
            if (t) cand = t;
        }
#endif
#if PV_MOM
        {   // 跨轮顺向: T-1 实测同向 54.6% vs 零假设 37.5%(27σ); 幅度 <1 金(只裁平局),
            // 且**不写任何固定路线** —— 它的「连续两轮位移精确重复」只有 12.8% ≈ 随机 12.5%。
            uint64_t t = cand & PT.towR[PT.sgi[sr - g_s.last_r[u] + 20]]
                              & PT.towC[PT.sgi[sc - g_s.last_c[u] + 20]];
            if (t) cand = t;
        }
#endif
#if PV_TIE
        {   // 平手取「实际移动最少」: 金币打分相同时不白走。等价于把 1-STAY / 2-STAY 家族
            // 当作平手规则的产物而非独立候选(T-1 的 (2,0) 14.8% 与 STAY 15.8% 由此同时得到)。
            uint64_t o = cand & PT.famO, sh = cand & PT.famS;
            cand = o ? o : (sh ? sh : cand);
        }
#endif
        int p = __builtin_ctzll(cand);
        unsigned a6 = (unsigned)(PT.cell[p] >> 52);
        acts[0] = (int)(a6 & 7u);
        acts[1] = (int)((a6 >> 3) & 7u);
        acts[2] = (int)((a6 >> 6) & 7u);

        // ---- 开局行军(盲轮才走; mode==1 才进, 稳态恒不取) ----
        if (__builtin_expect(g_s.mode == 1, 0)) {
            if (!g1) slowMove(in, u, sr, sc, acts);      // g1 已 & reach: 无可达金 = 盲轮
        }
        g_s.last_r[u] = (int8_t)sr; g_s.last_c[u] = (int8_t)sc;
    }

    out.k = 3;
    out.order = in->my_units_gold[0] >= in->my_units_gold[1] ? 0 : 1;
    out.vp = g_s.vp_buy;                         // 稳态恒 0(慢开局层才会置 2)
    return out;
}

}  // namespace

// 布局归一化死垫：入口模 64 必须落在已证最优档 0x10。四档扫描已证 0x20/0x30 各 +11.67ns,
// 故用永不执行的 nop 把 moveDecision 入口移回 0x10 档, 否则测到的 cycles 差会被布局税污染。
// **改动 decide 体积(含任何 PV_* 开关)后必须重新核对并调整垫长**: objdump -d | grep moveDecision,
// 取入口地址 mod 64, 用 PV_PAD 补到 0x10。⚠ 做消融 A/B 时**两臂都要各自校到 0x10**,
// 否则 ±11.67ns 的布局税会冒充成效应本身(而它与座位效应同量级)。
#ifndef PV_PAD
#define PV_PAD 192       // 默认值对应 PV_ACTOR=1 + PV_RTAB=1 (落库构型), 实测入口 0x1950
#endif
#define PV_STR2(x) #x
#define PV_STR(x) PV_STR2(x)
asm(".space " PV_STR(PV_PAD) ", 0x90");

extern "C" GameOutput moveDecision(const GameInput* input) {
    try {
        if (input == nullptr) return SAFE_OUT;
        return decide(input);
    } catch (...) {
        return SAFE_OUT;
    }
}
