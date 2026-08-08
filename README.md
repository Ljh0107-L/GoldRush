# GoldRush 2.0 — 仓库地图

九坤编程掘金赛（17×17 夺金，延迟定先手权）。**私有仓库，含平台凭据（AGENT.md §2），严禁公开。**

```
AGENT.md          ← 作战手册: 现状/凭据/规则/平台物理定律/评测纪律/开题清单 (入口必读)
src/
  speed/          ← 速度优先轨 (CHANGELOG.md + v1 现役总冠军 250ns, 内含 LOOP.md 算法流程)
  strategy/       ← 策略优先轨 (CHANGELOG.md + v1 矿堆农冠军, 内含 LOOP.md 算法流程)
  game_api.h      ← 官方接口头
tests/            ← replay / pair_diff / dump_inputs / bench / game_api mock
tools/            ← gr.py 平台客户端 · grlog.py 战报 · runbatch.sh 跑批
docs/             ← 官方赛制文档 | logs/ 对局日志(gitignored)
```

- 结构军规: 不设 archive 目录; 每轨只保留现役最强一版, 退役版本 = git 历史 + 轨 CHANGELOG 档案行
- 每个版本目录: Makefile 头注释 = 战绩档案 + 精确复现 flags; LOOP.md = 算法流程
- 历史逐日战报: `git show bc011e0:CHANGELOG.md`（根 CHANGELOG 已退役进 git 历史）
