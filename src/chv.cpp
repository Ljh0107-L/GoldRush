// chv.cpp — ChV 速度构型 (平台 172497 实测: P50 150 / P90 180 / P99 250, 收入 959, 对 T-1 先手 85%)
//
// 双构型体系 (8.10 所有者钦定, AGENT §6): player.cpp = 全能军队 (200ns/1515/三图),
// 本文件 = 路径引擎速度极 —— 先手统治底盘, 融合战役的收入器官承载骨架。
// 谱系: ChRec(160/604) → +富度门避弹(分支形态+40ns判负) → 掩码化加冕 ChV。
// 战役档案与判决细节见 CHANGELOG "融合战役档案" 与 "路径引擎 ChV 档案"。
//
// 架构 (全部 constexpr 先天携带, 零后天缝合 —— 布局悬崖免疫):
//   1. NAV2[2][17] 双叶回路: 每单位一叶 14 格环线 (西/东叶绕中央, 十字走廊同向穿行防对撞),
//      叶外全图 BFS 回指 —— 任意位置 2bit 查表得回环方向。环线=策略, 拾取=走路的副作用;
//      重访周期≈金块寿命, 天然 staleness farming (收入轮频 391/1000 单位轮 = 历史最高)。
//   2. 站金折返: 脚下残值≥2 → 踏出+踏回 (65% 递减吃干), 第 3 步回环。
//   3. 步 0 侧手: 4 邻标量探针, ≥2 顺路偏航 (cmov 链免税已证); 步 1/2 纯轨 ——
//      多步探针会把位置分支污染成数据依赖 (熵税 +150ns, 军规 19, 判例 172674/172812)。
//   4. 弹避掩码: 富度门 RICH_T=100 (穷单位弹透明, 踩弹 10%×0=0), 否决 = ALTD[17][17]
//      constexpr 备选表 (每格×4 方向, 墙静态→表静态, 289B) + cmov 混合, 零数据依赖分支
//      (同算法分支形态 +40-50ns, 判例 172371/172441; 弹税 59%→21.9%)。
//   5. ORT 开局表 rd<4 对位覆写。
//
// 禁改事项 (每条有尸体): 加扫描/多步探针→熵税 (军规 19); 后天缝合→布局悬崖 (六刀全灭案);
//   数据依赖分支→军规 18。任何行为改动 = 整文件重生 + 平台同窗复测 (sim 12 种子 + probe)。
// 陌生图形态 (§0.1 验收答案): W1/NAV2/ALTD/ORT 全 map1 烘焙, 但全部由墙表机械推导 ——
//   新图走再烘焙跑道 (墙表→constexpr 全表重导, 天级); 运行时懒学习形态在 loop.cpp mode1/2
//   已建成未移植。**当前仅 map1 可用, map2/3 未适配 (墙表错→环线残废, 不判负但打烂)。**
#include <cstdint>
#include <cstring>
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include "game_api.h"

#ifndef RICH_T
#define RICH_T 100
#endif

namespace {
constexpr int N = GRID_SIZE;
constexpr int STAY = 4;
constexpr int DR[5] = {-1, 1, 0, 0, 0};
constexpr int DC[5] = {0, 0, -1, 1, 0};
constexpr GameOutput SAFE_OUT = {{STAY, STAY, STAY, STAY, STAY, STAY}, 3, 0, 0};

constexpr uint32_t W1[N] = {
    0x00004010u, 0x00000000u, 0x0001800cu, 0x00024012u, 0x00002020u, 0x00001040u,
    0x00004010u, 0x00000500u, 0x000028a0u, 0x00000500u, 0x00004010u, 0x00001040u,
    0x00002020u, 0x00024012u, 0x0001800cu, 0x00000000u, 0x00004010u,
};

// 被否决方向 a 的备选序: 两垂直向优先(贴弹滑行), 反向兜底
constexpr uint8_t ALT[4][3] = {{2, 3, 1}, {2, 3, 0}, {0, 1, 3}, {0, 1, 2}};

struct T {
    uint64_t NAV2[2][17];                        // 双叶回路 2 位打包 + 线外 BFS 回指
    uint8_t ALTD[17][17];                        // 每格备选方向表: 位(a*2..a*2+1)=方向a被否决时的绕行向
    constexpr T() : NAV2(), ALTD() {
        for (int r = 0; r < 17; ++r)
            for (int c = 0; c < 17; ++c) {
                uint8_t byte = 0;
                for (int a = 0; a < 4; ++a) {
                    int pick = a;                // 无可绕默认原向(退化为旧踩弹行为)
                    for (int t = 0; t < 3; ++t) {
                        int d = ALT[a][t];
                        int nr = r + (d == 0 ? -1 : d == 1 ? 1 : 0);
                        int nc = c + (d == 2 ? -1 : d == 3 ? 1 : 0);
                        if ((unsigned)nr >= 17u || (unsigned)nc >= 17u) continue;
                        if ((W1[nr] >> (nc + 1)) & 1u) continue;
                        pick = d; break;
                    }
                    byte |= (uint8_t)(pick << (a * 2));
                }
                ALTD[r][c] = byte;
            }
        constexpr int8_t LB[2][14][2] = {
            {{6,8},{7,8},{8,8},{9,8},{10,8},{10,7},{10,6},{9,6},{9,5},{8,5},{7,5},{7,6},{6,6},{6,7}},
            {{6,8},{7,8},{8,8},{9,8},{10,8},{10,9},{10,10},{9,10},{9,11},{8,11},{7,11},{7,10},{6,10},{6,9}},
        };
        for (int u = 0; u < 2; ++u) {
            uint8_t nv[17][17] = {};
            int16_t dist[17][17] = {};
            int16_t qq[289] = {};
            int head = 0, tail = 0;
            for (int k = 0; k < 14; ++k) {
                int r = LB[u][k][0], c = LB[u][k][1];
                int nr = LB[u][(k + 1) % 14][0], nc = LB[u][(k + 1) % 14][1];
                nv[r][c] = (uint8_t)(nr == r + 1 ? 1 : (nr == r - 1 ? 0 : (nc == c + 1 ? 3 : 2)));
                dist[r][c] = 1;
                qq[tail++] = (int16_t)(r * 17 + c);
            }
            const int dr_[4] = {-1, 1, 0, 0}, dc_[4] = {0, 0, -1, 1};
            while (head < tail) {
                int cur = qq[head++];
                int r = cur / 17, c = cur % 17;
                for (int a = 0; a < 4; ++a) {
                    int nr = r + dr_[a], nc = c + dc_[a];
                    if ((unsigned)nr >= 17u || (unsigned)nc >= 17u) continue;
                    if (dist[nr][nc] || ((W1[nr] >> (nc + 1)) & 1u)) continue;
                    dist[nr][nc] = 1;
                    nv[nr][nc] = (uint8_t)(a ^ 1);
                    qq[tail++] = (int16_t)(nr * 17 + nc);
                }
            }
            for (int r = 0; r < 17; ++r) {
                uint64_t w = 0;
                for (int c = 0; c < 17; ++c) w |= (uint64_t)(nv[r][c] & 3u) << (c * 2);
                NAV2[u][r] = w;
            }
        }
    }
};
constexpr T TT;

constexpr uint8_t ORT_A[2][4][3] = {
    {{1,3,3},{3,1,3},{1,3,1},{3,1,1}},
    {{0,2,2},{2,0,2},{0,2,0},{2,0,0}},
};
constexpr int8_t ORT_R[2][4] = {{0,1,2,4},{16,15,14,12}};
constexpr int8_t ORT_C[2][4] = {{0,2,4,5},{16,14,12,11}};

GameOutput decide(const GameInput* in) {
    _mm_prefetch((const char*)&TT, _MM_HINT_T0);
    _mm_prefetch((const char*)&TT + 64, _MM_HINT_T0);
    _mm_prefetch((const char*)&TT + 192, _MM_HINT_T0);
    _mm_prefetch((const char*)&TT + 320, _MM_HINT_T0);
    _mm_prefetch((const char*)&TT + 448, _MM_HINT_T0);
    int r0 = in->my_units[0].row, r1 = in->my_units[1].row;
    _mm_prefetch((const char*)&in->grid[r0][0], _MM_HINT_T0);
    _mm_prefetch((const char*)&in->grid[r1][0], _MM_HINT_T0);
    GameOutput out;
    int rd = in->round;
    for (int u = 0; u < 2; ++u) {
        int sr = in->my_units[u].row, sc = in->my_units[u].col;
        int* acts = out.actions + u * 3;
        int own = in->grid[sr][sc];
        int richb = in->my_units_gold[u] >= RICH_T;
        if (own > 1) {                           // 站金折返(残值≥2), 扫描仅墙(ChRec 原形免税)
            int a = -1;
            for (int d = 0; d < 4; ++d) {
                int nr = sr + DR[d], nc = sc + DC[d];
                if ((unsigned)nr >= 17u || (unsigned)nc >= 17u) continue;
                if ((W1[nr] >> (nc + 1)) & 1u) continue;
                a = d; break;
            }
            if (a >= 0) {
                int bm0 = -((in->grid[sr + DR[a]][sc + DC[a]] == -3) & richb);
                int al0 = (TT.ALTD[sr][sc] >> (a * 2)) & 3;
                a = (a & ~bm0) | (al0 & bm0);
                acts[0] = a; acts[1] = a ^ 1;
                int a2 = (int)((TT.NAV2[u][sr] >> (sc * 2)) & 3u);
                int bm2 = -((in->grid[sr + DR[a2]][sc + DC[a2]] == -3) & richb);
                int al2 = (TT.ALTD[sr][sc] >> (a2 * 2)) & 3;
                acts[2] = (a2 & ~bm2) | (al2 & bm2);
            } else acts[0] = acts[1] = acts[2] = STAY;
        } else {
            int pr = sr, pc = sc;
#pragma GCC unroll 3
            for (int s = 0; s < 3; ++s) {
                int a = (int)((TT.NAV2[u][pr] >> (pc * 2)) & 3u);
                if (s == 0) {                    // 步0侧手: 邻格 ≥2 顺路
                    int bd = -1, bv = 1;
                    for (int d = 0; d < 4; ++d) {
                        int nr = pr + DR[d], nc = pc + DC[d];
                        if ((unsigned)nr >= 17u || (unsigned)nc >= 17u) continue;
                        if ((W1[nr] >> (nc + 1)) & 1u) continue;
                        int v = in->grid[nr][nc];
                        if (v > bv) { bv = v; bd = d; }
                    }
                    if (bd >= 0) a = bd;
                }
                int bm = -((in->grid[pr + DR[a]][pc + DC[a]] == -3) & richb);
                int al = (TT.ALTD[pr][pc] >> (a * 2)) & 3;
                a = (a & ~bm) | (al & bm);
                acts[s] = a;
                pr += DR[a]; pc += DC[a];
            }
        }
        if (__builtin_expect(rd < 4, 0)) {
            int ri = rd & 3;
            if (sr == ORT_R[u][ri] && sc == ORT_C[u][ri]) {
                acts[0] = ORT_A[u][ri][0];
                acts[1] = ORT_A[u][ri][1];
                acts[2] = ORT_A[u][ri][2];
            }
        }
    }
    out.k = 3;
    out.order = 0;
    out.vp = 0;
    return out;
}
}  // namespace

extern "C" GameOutput moveDecision(const GameInput* in) {
    try {
        if (in == nullptr) return SAFE_OUT;
        return decide(in);
    } catch (...) {
        return SAFE_OUT;
    }
}
