# GoldRush 2.0 — 0x8F 战队仓库

九坤编程掘金赛参赛仓库：17×17 网格夺金，500 轮，`得分 = 毛金币 − 视野费`，**每轮决策耗时短者先动** —— 延迟本身是策略变量。全部开发由 AI Agent 完成，人类所有者裁决目标与对外动作。

> ⚠️ **私有仓库，含平台凭据（`AGENT.md §2`，所有者授权入库）。红线：不得转 public。**

## 当前状态（2026-08-13）

| | |
|---|---|
| 现役构型 | **R15**（`src/player.cpp`，2026-08-14）：R14 陌生图策略 + AVX-512 踩踏规避 + 冷足迹三刀（`.text` sha `16be14f2…`）|
| 平台读数 | P50 **130-140ns** · 对 T-1 配对 ΔP50 **−10 ~ −30** · 先手率 54-71% · 踩踏 −43% |
| 公开位 | `model_id 278135`，与 `src/` 同源（`.text` sha 见 CHANGELOG 交付台账） |
| 时间线 | 报名 8.14 截止 · 模拟赛 8.15-16 · **初赛 8.17-21**（循环赛胜率前 16 晋级）· 决赛 9.6 |

## 五分钟上手

```bash
# 保真度硬门(改 sim 前后必跑; 需 logs/gr_data, 缺失时见 AGENT.md §2 恢复路径)
python3 sim/validate.py --repo . --output /tmp/v.json

# 本地跑局(唯一推荐的比较方式 = 同种子配对 A/B + 换座)
python3 sim/cli.py --p1 a.so --p2 b.so --map map1 --seed 1000 --games 30 --paired

# 平台(每日 500 局配额; 探测自由, 动用须所有者批准)
python3 tools/arena.py quota          # 只读
python3 tools/arena.py rank -n 130
```

构建**只能在赛事机**（本机 macOS 构建过 SIGILL 事故）：

```bash
ssh Ubiquant220@8.153.76.120 'cd ~/goldrush/src && \
  g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o player.so player.cpp'
```

## 仓库地图

```
README.md             本页
AGENT.md              作战手册: 凭据 · 机器分工 · 配额 · 军规 · 交付工作流
HANDOFF.md            事实与工具: 规则事实 · sim 用法 · 测量协议 · 数据资产 · 对手逆向

src/
  player.cpp          现役唯一构型(头注释 = 算法流程)
  game_api.h          官方接口头
  INFRA.md            平台成本模型(指令→ns→金币, 设计算法前必读)
  CHANGELOG.md        版本史 · 全部判负记录 · 军规档案(本仓库的记忆)

sim/                  本地模拟器(纯 Python 标准库, 官方 ABI 兼容, 不建模延迟)
  cli.py / engine.py  跑局入口 / 引擎本体
  validate.py         保真度套件(硬门)
  maps*.json          公测图 · 陌生图注册表 · 照片重建终局图
  audit_unknown_maps.py  陌生图形态关(交付前必过)
  analyze_order_sensitivity.py  慢方真实棋盘重构器(已验证 9000/9000)
  probe/              满视野观测探针(对手空间采样)
tests/                验收: pair_diff · verify_construct.sh · 延迟台架(icount/bench/latency)
tools/                arena.py(平台客户端) · gamelog.py · behav.py · batch.sh
docs/                 官方赛制 · FAQ · 初赛条款审计 · 配额规程
logs/                 ⚠️ 缓存目录(会被清空), gr_data 恢复路径见 AGENT.md §2
```

## 读这个仓库的正确顺序

1. **`HANDOFF.md`** —— 规则事实与工具，故意不含策略结论（先形成自己的判断）
2. **`src/player.cpp` 头注释** —— 现役算法一页流程
3. **`src/INFRA.md`** —— 成本模型：为什么这台机器上"省指令 ≠ 变快"
4. **`src/CHANGELOG.md`** —— 全部战役史与判负记录（想知道"为什么不做 X"时查这里，X 大概率已判死并附死因）

## 三条最贵的经验（判例都在 CHANGELOG）

1. **冷足迹 > 指令数**：平台每轮间隔 350ms，L1/L2 全逐出（L3 温）、分支预测器清空 ⇒ `.text`+`.rodata` 的缓存行数与分支计数是一等成本，指令数只是均价代理。三个独立判例。
2. **暖态台架双向失明**：暖态更快的东西可能冷态更慢（PGO/分支化），暖态更慢的可能冷态赚钱（预取）⇒ **平台内窗交错（交替提交、同对手同图、逐局串行）是唯一权威**。
3. **每个数字带着前提走**：构型 hash · 窗口 · 对手 · 图 · gid 与结论同址落库，缺一即视为过期 —— 本仓绝大多数返工源于把有条件的数当常数。
