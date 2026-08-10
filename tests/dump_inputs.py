"""dump_inputs.py — 把日志逐轮重建的 GameInput 序列化成二进制, 供 bench.cpp 用。

用法: python3 tests/dump_inputs.py logs/game_140521.log  ->  logs/game_140521.bin

⚠ 等价性门的覆盖面声明（军规：任何等价性门必须逐字段列出覆盖范围）
------------------------------------------------------------------
`pair_diff.py` 的判据强度**上限就是本文件填了多少输入字段** —— 没填的字段被置零/置空，
于是读取它的构型会拿到**假的** `0/500` 绿灯。`COVERAGE` 是这道门的机读边界，
`verify_construct.sh` 的 check 4 会把它打印出来。

历史事故（8.10）：本文件曾无条件 `snapshot_valid = 0`，导致两个**真实行为改动**
（`RICH` / `POOR`，读 `snapshot.gold_remaining` 改变回落锚点）拿到三图 `pair_diff 0/500`
且 `verify_construct.sh` 四项全过。补齐 snapshot 后重测：`RICH`-vs-`OFF` **534/1500** 显影。
现役 `fd47ea6` 对这四个字段 grep 命中为 0，故历史验收未被污染 —— 但它是活雷。
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import GameInput, MAX_NPCS

N = 17

#: 逐字段覆盖声明。COVERED = 从日志真实重建；UNCOVERED = 置零，门对读它的改动**失明**。
COVERAGE = {
    "round": "COVERED",
    "grid": "COVERED (start-phase fogged grid, 逐格复制)",
    "my_units": "COVERED (prev end phase)",
    "my_units_gold": "COVERED (prev end phase)",
    "gold_opp": "COVERED (prev end phase, 对手双单位持金和)",
    "visible_enemies": "COVERED — 但保真度随源日志：全信息日志里对手位置永远存在，"
                       "故迷雾过滤实际由日志本身提供，不由本文件施加",
    "visible_npcs": "COVERED — 硬编码切比雪夫半径 2；买视野(vp>0)的轮次不忠实",
    "num_visible_npcs": "COVERED",
    "snapshot_valid": "COVERED (engine: round>0 且 round%5==0)",
    "snapshot.window_begin/window_end": "COVERED ([r-5, r-1])",
    "snapshot.regions[].id": "COVERED",
    "snapshot.regions[].gold_remaining": "COVERED in form — start[r] 地面按风车五区求和，"
                                        "与引擎同址语义；⚠ 迷雾日志上是**低估**（雾格 -5 计 0）",
    "snapshot.regions[].occupants": "COVERED in form — start[r] 双方 4 单位 + 全部 NPC；"
                                    "⚠ 迷雾日志上是**低估**（不可见角色无 position）",
    "snapshot.regions[].enter": "UNCOVERED — 置 0",
    "snapshot.regions[].leave": "UNCOVERED — 置 0",
    "snapshot.regions[].gold_generated": "UNCOVERED — 置 0",
    "snapshot.regions[].gold_collected": "UNCOVERED — 置 0",
}
#: 那四个流量字段**故意**留空：引擎语义是错位窗口（`generated` 跨 `begin+1..round`），
#: 而 `sim/README.md §4` 实测「按标签直觉统计生成量仅 17/297 个快照全对」
#: ⇒ 填错比留空更糟（会让 pair_diff 报**伪**分歧）。留空 + 声明，是当前诚实的做法。
#:
#: ⚠ 值保真度 vs 门灵敏度，两者要分清：
#:   * 门的**灵敏度**只要求两个构型看到**同一份**输入字节 ⇒ 有分歧就是行为差异，
#:     所以即使 remaining/occupants 是低估值，`pair_diff` 仍能显影快照读取者（实测 534/1500）。
#:   * 但值的**真实性**依赖源日志：迷雾日志上两者都低估 ⇒ 阈值型读取者可能**欠激发**
#:     （例如 `remaining > 80` 的门在低估值下永不触发，于是看起来"无分歧"）。
#:   ⇒ **要最大灵敏度，请用全信息日志**（`logs/gr_data/full/`），它们没有雾、角色位置齐全。


def region_id(r, c):
    """固定风车五区，与 `sim/engine.py:84` 逐行一致。"""
    if 4 <= r <= 12 and 4 <= c <= 12:
        return 1
    if r <= 3 and c <= 12:
        return 2
    if r >= 4 and c <= 3:
        return 3
    if r >= 13 and c >= 4:
        return 4
    return 5


def build_input(rows, idx):
    row = rows[idx]
    prev = rows[idx - 1]['end'] if idx else row['start']
    gi = GameInput()
    gi.round = row['round']
    sg = row['start']['grid']
    for r in range(N):
        for c in range(N):
            gi.grid[r][c] = sg[r][c]
    upos = []
    for u in range(2):
        p = prev['players'][0]['units'][u]['position'] or (-1, -1)
        gi.my_units[u].row, gi.my_units[u].col = p
        gi.my_units_gold[u] = prev['players'][0]['units'][u]['gold']
        upos.append(p)
    gi.gold_opp = sum(x['gold'] for x in prev['players'][1]['units'])
    ne = 0
    for x in prev['players'][1]['units']:
        p = x['position']
        if p and ne < 2:
            gi.visible_enemies[ne].row, gi.visible_enemies[ne].col = p
            ne += 1
    for i in range(ne, 2):
        gi.visible_enemies[i].row = gi.visible_enemies[i].col = -1
    nn = 0
    for npc in row['start'].get('npcs') or []:
        p = npc.get('position')
        if p and any(abs(p[0] - ur) <= 2 and abs(p[1] - uc) <= 2
                     for ur, uc in upos if ur >= 0) and nn < MAX_NPCS:
            gi.visible_npcs[nn].id = npc['id']
            gi.visible_npcs[nn].pos.row, gi.visible_npcs[nn].pos.col = p
            nn += 1
    gi.num_visible_npcs = nn
    for i in range(nn, MAX_NPCS):
        gi.visible_npcs[i].id = 0
        gi.visible_npcs[i].pos.row = gi.visible_npcs[i].pos.col = -1
    gi.snapshot_valid = 0
    _fill_snapshot(gi, row)
    return gi


def _fill_snapshot(gi, row):
    """按引擎语义重建快照（`sim/engine.py:790-828`，逐区 1485/1485 日志证实）。

    发布条件 `round>0 && round%5==0`（仅 99/500 轮）；`gold_remaining`/`occupants`
    采样 **start[r]** —— 本轮生成后、任何移动前，因此在发布轮上是当轮真值。
    """
    rnd = row['round']
    if rnd <= 0 or rnd % 5:
        gi.snapshot_valid = 0
        gi.snapshot.window_begin = -1
        gi.snapshot.window_end = -1
        for i in range(5):
            rs = gi.snapshot.regions[i]
            rs.id = i + 1
            rs.enter = rs.leave = 0
            rs.gold_generated = rs.gold_collected = 0
            rs.gold_remaining = rs.occupants = 0
        return

    start = row['start']
    remaining = [0] * 5
    grid = start['grid']
    for r in range(N):
        for c in range(N):
            v = grid[r][c]
            if v > 0:                      # 迷雾 -5 / 弹 -3 / 墙 -1 都不是金
                remaining[region_id(r, c) - 1] += v
    occupants = [0] * 5
    for player in start['players']:
        for unit in player['units']:
            pos = unit['position']
            if pos:
                occupants[region_id(pos[0], pos[1]) - 1] += 1
    for npc in start.get('npcs') or []:
        pos = npc.get('position')
        if pos:
            occupants[region_id(pos[0], pos[1]) - 1] += 1

    gi.snapshot_valid = 1
    gi.snapshot.window_begin = rnd - 5
    gi.snapshot.window_end = rnd - 1
    for i in range(5):
        rs = gi.snapshot.regions[i]
        rs.id = i + 1
        rs.gold_remaining = remaining[i]
        rs.occupants = occupants[i]
        rs.enter = rs.leave = 0            # UNCOVERED, 见 COVERAGE
        rs.gold_generated = rs.gold_collected = 0


if __name__ == '__main__':
    for log in sys.argv[1:]:
        rows = [json.loads(l) for l in open(log).readlines()[2:]]
        out = log.rsplit('.', 1)[0] + '.bin'
        with open(out, 'wb') as f:
            for i in range(len(rows)):
                f.write(bytes(build_input(rows, i)))
        print(f"{out}: {len(rows)} 轮 x {ctypes.sizeof(GameInput)}B")
