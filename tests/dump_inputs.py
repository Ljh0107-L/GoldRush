"""dump_inputs.py — 把日志逐轮重建的 GameInput 序列化成二进制, 供 bench.cpp 用。

用法: python3 tests/dump_inputs.py logs/game_140521.log  ->  logs/game_140521.bin
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_so import GameInput, MAX_NPCS

N = 17


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
    return gi


if __name__ == '__main__':
    for log in sys.argv[1:]:
        rows = [json.loads(l) for l in open(log).readlines()[2:]]
        out = log.rsplit('.', 1)[0] + '.bin'
        with open(out, 'wb') as f:
            for i in range(len(rows)):
                f.write(bytes(build_input(rows, i)))
        print(f"{out}: {len(rows)} 轮 x {ctypes.sizeof(GameInput)}B")
