# 每日配额探测

> 2026-08-12 落库。权威源 = 平台 `GET /api/user/get_user_info`，
> 不是对局列表、不是 ledger、也不是任何「留 N 局」的本地护栏。

## 1. 官方规则

> `docs/FAQ.md:366` — 每支队伍每天最多可发起 **500** 场对局，北京时间每日 0 点重置。
> **仅统计主动发起的对局**；被其他队伍挑战的场次不占用次数。

重置边界：北京 0 点 = **UTC 16:00**。

## 2. 权威接口（只读，不耗配额）

```
GET /api/user/get_user_info
```

| 字段 | 含义 |
|---|---|
| `today_initiated` | 本窗口已主动发起的局数 |
| `daily_initiate_limit` | 日上限（公测为 500） |

**剩余 = `daily_initiate_limit - today_initiated`。** 这两个字段是余额的唯一权威；
ledger 只记意图（曾与平台差 132 局）。

⛔ **禁止数 `GET /api/user/get_game_list_1` 的行数。** 列表含别人挑战我方防守位的局，
那些不占配额。8.10 实测同一时刻列表 498 条 vs `today_initiated` 470，差 28 条正是防守局。

## 3. 怎么读

```bash
python3 tools/arena.py quota
```

等价手写（`tools/arena.py` 的 `call()`）：

```bash
python3 -c "
import sys; sys.path.insert(0,'tools')
from arena import call
u = call('GET','/api/user/get_user_info')
used, limit = u['today_initiated'], u['daily_initiate_limit']
print(used, '/', limit, 'remaining', limit - used)"
```

库内其它读法：`sim/field_sample.py::quota_state`、`sim/run_rikka_batch.py::quota`。
它们读的是同一对字段；批次脚本里的 `RESERVE` **不是**平台规则。

## 4. 纪律（所有者 2026-08-12 更正）

1. **探测自由。** 本 GET 只读，不发起对局，不须批准。
2. **动用须所有者批准。** `submit` / `POST /api/user/add_model_1` 才消耗配额。
3. ⛔ **不是「剩余 20 局冻结」。** 那个 20 是 `sim/run_rikka_batch.py` 一次批次的本地
   `RESERVE = 20`，被误写成所有者约束。平台没有「必须留 20」；余额以本接口为准。

## 5. 自博弈也计入

自博弈走 `add_model_1`，属于主动发起，**计入** `today_initiated`
（`FAQ:366`「仅统计主动发起」）。若要独立验证：提交前读一次、提交成功后再读一次，
差分应为 +1。探测本身不耗配额。

## 6. 响应形状（快照，余额会变）

2026-08-12 实测样例（只读；**不要把这里的 84/500 当当前余额**）：

```json
{
  "id": 220,
  "name_cn": "0x8F",
  "today_initiated": 84,
  "daily_initiate_limit": 500,
  "cost1": 240,
  "cost2": 0,
  "cost3": 0
}
```

当时剩余 416。`cost1/2/3` 是赛段延迟位，不是配额；配额相关就上面两个字段。
