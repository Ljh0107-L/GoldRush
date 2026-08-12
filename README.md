# GoldRush 2.0 — 仓库地图

九坤编程掘金赛（17×17 夺金，延迟定先手权）。**私有仓库，含平台凭据（AGENT.md §2），严禁公开。**

战役结论与负结果在 `src/CHANGELOG.md`；一次性分析脚本与 `sim/reports/` 只活在 git 历史。

```
AGENT.md              作战手册（入口）
HANDOFF.md            规则事实 · 工具用法 · 数据在哪
README.md             本页

src/
  player.cpp          现役全能军队（头注释 = 算法流程）
  chv.cpp             现役速度构型
  game_api.h          官方接口头
  INFRA.md            平台成本模型（含 .so 身份表与回滚判据）
  CHANGELOG.md        版本史 · 负结果 · 军规

sim/                  本地模拟器（不建模延迟）
  cli.py / engine.py  跑局
  validate.py         保真度套件
  maps.json           三张公测图
  probe/              满视野观测探针
tests/                pair_diff · verify_construct · 延迟台架
tools/                arena.py（含 quota）· gamelog.py
docs/                 官方赛制 / FAQ / 初赛审计 / 配额探测
logs/                 对局日志（gitignore）
```

构建（必须在赛事机）：

```bash
g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -o player.so src/player.cpp -Isrc
```

探测配额：`python3 tools/arena.py quota`（只读）。动用须所有者批准。规程 `docs/QUOTA.md`。
