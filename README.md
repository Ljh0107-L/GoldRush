# GoldRush 2.0 — 仓库地图

九坤编程掘金赛（17×17 夺金，延迟定先手权）。**私有仓库，含平台凭据（AGENT.md §2），严禁公开。**

```
AGENT.md          ← 作战手册: 现状/凭据/规则/平台物理定律/评测纪律/开题清单 (入口必读)
src/
  speed/          ← 速度优先轨 (CHANGELOG.md + v1 现役总冠军 250ns | v2 210ns 技术储备)
  strategy/       ← 策略优先轨 (CHANGELOG.md + v1 天梯防守位 | v2 矿堆农冠军 | v2a 时间框架实验)
  game_api.h      ← 官方接口头
tests/            ← replay / pair_diff / dump_inputs / bench / game_api mock
tools/            ← gr.py 平台客户端 · grlog.py 战报 · runbatch.sh 跑批
docs/             ← 官方赛制文档 | archive/ 史前原型 | logs/ 对局日志(gitignored)
```

- 每个版本目录的 Makefile 头注释 = 战绩档案 + 精确复现 flags
- 历史逐日战报: `git show bc011e0:CHANGELOG.md`（根 CHANGELOG 已退役进 git 历史）
