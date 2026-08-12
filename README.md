# GoldRush 2.0 — 仓库地图

九坤编程掘金赛（17×17 夺金，延迟定先手权）。**私有仓库，含平台凭据（AGENT.md §2），严禁公开。**

```
AGENT.md          ← 作战手册: 现状/凭据/规则/纪律/开题 (入口必读)
src/
  player.cpp      ← 现役冠军 (头注释=算法流程)
  game_api.h      ← 官方接口头
  INFRA.md        ← 平台成本模型: 每种操作值多少 ns, 算法预算换算表
  CHANGELOG.md    ← 版本史 + 负结果军规 + 策略遗产 (旧版本取用命令在谱系表)
tests/            ← api.py(ctypes桥) · replay · pair_diff · dump_inputs · bench
tools/            ← arena.py(平台客户端, 含 quota) · gamelog.py(战报) · batch.sh(跑批)
docs/             ← 官方赛制 + 我方审计/规程（QUOTA.md = 配额探测）
```

构建：`g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o player.so src/player.cpp -Isrc`

当前阶段：延迟已打到头部（先手权在手），正用 INFRA 预算表做算法/收入的 tradeoff。
