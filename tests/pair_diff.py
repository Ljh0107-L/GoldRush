"""pair_diff.py — 两个 .so 同输入并行回放, 统计输出分歧(等价重构应为 0)。

用法: python3 tests/pair_diff.py a.so b.so logs/game_X.log [logs/game_Y.log ...]
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay as rp
from dump_inputs import build_input
from test_so import GameInput


def pair_diff(so_a, so_b, logs):
    a = rp.load_so(so_a)
    b = rp.load_so(so_b)
    total_diff = 0
    for log in logs:
        rows = [json.loads(l) for l in open(log).readlines()[2:]]
        diff = 0
        first = None
        for idx in range(len(rows)):
            gi = build_input(rows, idx)
            gi2 = GameInput.from_buffer_copy(gi)
            o1 = a.moveDecision(ctypes.byref(gi))
            o2 = b.moveDecision(ctypes.byref(gi2))
            if (list(o1.actions) != list(o2.actions) or o1.k != o2.k
                    or o1.order != o2.order or o1.vp != o2.vp):
                diff += 1
                if first is None:
                    first = (rows[idx]['round'], list(o1.actions), list(o2.actions))
        print(f"{log}: 分歧 {diff}/{len(rows)}" +
              (f"  首例 r{first[0]}: {first[1]} vs {first[2]}" if first else ""))
        total_diff += diff
    return total_diff


if __name__ == '__main__':
    sys.exit(1 if pair_diff(sys.argv[1], sys.argv[2], sys.argv[3:]) else 0)
