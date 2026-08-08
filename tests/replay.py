"""replay.py — 把下载的对局日志逐轮喂给本机 player.so, 复现线上决策。

用法:
  python3 tests/replay.py logs/game_140135.log            # 全局回放, 报告动作吻合率
  python3 tests/replay.py logs/game_140135.log 377        # 到 377 轮为止, 打印该轮细节
  SO=... python3 tests/replay.py ...                      # 指定 .so

原理: 日志第 R 条记录的 start.grid 就是我们当轮的决策输入(已验证雾窗
与上轮终点位置对齐)。单位位置/持币取上一条记录的 end。NPC 过滤到
切比雪夫半径 2(与 vision_r 一致)。快照置 0(cpp22 未用)。

局限: 若线上曾因超时/异常走了 SAFE_OUT, 回放会从那轮起分叉。
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import (GameInput, GameOutput, Position, NpcInfo,
                     GRID_SIZE, MAX_NPCS, SO_PATH)

N = GRID_SIZE


def load_so(path):
    so = ctypes.CDLL(path)
    so.moveDecision.argtypes = [ctypes.POINTER(GameInput)]
    so.moveDecision.restype = GameOutput
    return so


def replay(log_path, so_path, stop_round=None, verbose_round=None):
    lines = open(log_path).readlines()
    rows = [json.loads(l) for l in lines[2:]]
    so = load_so(so_path)

    match = miss = 0
    mismatches = []
    for idx, row in enumerate(rows):
        rnd = row['round']
        if stop_round is not None and rnd > stop_round:
            break
        prev = rows[idx - 1]['end'] if idx else row['start']
        me_prev = prev['players'][0]
        opp_prev = prev['players'][1]

        gi = GameInput()
        gi.round = rnd
        sg = row['start']['grid']
        for r in range(N):
            for c in range(N):
                gi.grid[r][c] = sg[r][c]
        upos = []
        for u in range(2):
            p = me_prev['units'][u]['position']
            if p is None:
                p = (-1, -1)
            gi.my_units[u].row, gi.my_units[u].col = p
            gi.my_units_gold[u] = me_prev['units'][u]['gold']
            upos.append(p)
        gi.gold_opp = sum(u['gold'] for u in opp_prev['units'])
        ne = 0
        for u in opp_prev['units']:
            p = u['position']
            if p is not None and ne < 2:
                gi.visible_enemies[ne].row, gi.visible_enemies[ne].col = p
                ne += 1
        for i in range(ne, 2):
            gi.visible_enemies[i].row = gi.visible_enemies[i].col = -1
        nn = 0
        for npc in row['start'].get('npcs') or []:
            p = npc.get('position')
            if not p:
                continue
            if any(abs(p[0] - ur) <= 2 and abs(p[1] - uc) <= 2
                   for ur, uc in upos if ur >= 0):
                if nn < MAX_NPCS:
                    gi.visible_npcs[nn].id = npc['id']
                    gi.visible_npcs[nn].pos.row, gi.visible_npcs[nn].pos.col = p
                    nn += 1
        gi.num_visible_npcs = nn
        for i in range(nn, MAX_NPCS):
            gi.visible_npcs[i].id = 0
            gi.visible_npcs[i].pos.row = gi.visible_npcs[i].pos.col = -1
        gi.snapshot_valid = 0

        out = so.moveDecision(ctypes.byref(gi))
        got = list(out.actions)
        logged = ((row['end']['players'][0]['units'][0].get('actions') or []) +
                  (row['end']['players'][0]['units'][1].get('actions') or []))
        if logged:
            if got == logged:
                match += 1
            else:
                miss += 1
                if len(mismatches) < 10:
                    mismatches.append((rnd, got, logged))
        if verbose_round is not None and rnd == verbose_round:
            print(f"r{rnd}: 复放输出 {got} (k={out.k} order={out.order}) "
                  f"线上 {logged}")
            print(f"  单位 {upos} 持币 {list(gi.my_units_gold)}")
    total = match + miss
    print(f"动作吻合 {match}/{total}" + (f"  首个分叉: r{mismatches[0][0]}" if mismatches else ""))
    for rnd, got, logged in mismatches:
        print(f"  r{rnd}: 复放 {got}  线上 {logged}")
    return match, miss


if __name__ == '__main__':
    log = sys.argv[1]
    vr = int(sys.argv[2]) if len(sys.argv) > 2 else None
    so_path = os.environ.get('SO', SO_PATH)
    replay(log, so_path, stop_round=vr, verbose_round=vr)
