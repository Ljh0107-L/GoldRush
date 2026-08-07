# GoldRush 2.0 — 仓库地图

九坤编程掘金争夺赛（17×17 网格夺金，500 回合，延迟决定先手权）。
**私有仓库，含平台凭据（见 AGENT.md），严禁公开。**

## 一分钟看懂结构

```
AGENT.md          ← 作战大脑: 规则逆向/平台物理/实验日志/凭据/操作手册 (最重要)
CHANGELOG.md      ← 版本演进史 (v1 cpp1→cpp27b, v2 ns300→ns322, 含负结果)
README.md         ← 本文件
src/
  game_api.h      ← 官方接口头 (v1/v2 共用)
  v1/             ← 冻结防守版 cpp27b: 已发布天梯, P50 ~3.7μs, 收入 ~2712。只修不改。
  v2/             ← 开发主线 (原 ns300): 300ns 预算契约重写, 当前 ns322 P50 ~1.07μs, 收入 ~1800。
tests/            ← 测试台: replay.py(日志回放) pair_diff.py(逐位对账) bench.cpp(延迟基准)
                    dump_inputs.py profile_replay.py test_so.py baseline_random.py(陪练)
tools/            ← 平台客户端: gr.py(提交/开局/榜单) grlog.py(日志分析)
docs/             ← 官方赛事文档 (只读): 赛制介绍 / FAQ / 示例代码说明
archive/          ← 死原型: ns_prototype.cpp(nsv2 骨架) p0_player.py(Python 基线)
logs/             ← 对局日志 (gitignored, 1.5MB/局): 最新 30 局平铺, 其余在 logs/archive/
```

## 版本标识

- git tag `v1-cpp2`…`v1-cpp27b` 对应 v1 各版本；v2 各实验号(ns3xx)在 CHANGELOG 有逐条记录
- 取历史版本: `git show v1-cpp23d:src/player.cpp`（老 tag 里路径是重组前的 `src/player.cpp`）

## 常用工作流

```
# 开发机编译提交产物 (本机 make local 只测逻辑)
cd src/v2 && make            # 或 src/v1
# 回放对账 (改动是否逐位等价)
python3 tests/pair_diff.py a.so b.so logs/game_X.log
# 平台操作
./tools/gr.py submit --map 1 src/v2/player.so:名字
./tools/gr.py ranklist
```

详细协议（≥4 局批量评测纪律、PGO 流水线、遥测信道）见 AGENT.md。
