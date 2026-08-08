"""api.py — GameInput/GameOutput 的 ctypes 结构(与 game_api.h 逐字段一致)
加载 src/player.so, 验证 C++ 版输出合法性并测延迟。

编译: 见 src/INFRA.md 构建命令; 冒烟: python3 tests/api.py

结构体布局须与 src/game_api.h 逐字段一致, 改头文件时同步改这里。
"""
import ctypes
import os
import sys
import time

GRID_SIZE, MAX_NPCS, S, REGION_COUNT = 17, 7, 6, 5

SO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "player.so")


class Position(ctypes.Structure):
    _fields_ = [("row", ctypes.c_int), ("col", ctypes.c_int)]


class NpcInfo(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int), ("pos", Position)]


class RegionStat(ctypes.Structure):
    _fields_ = [(k, ctypes.c_int) for k in
                ("id", "enter", "leave", "gold_generated",
                 "gold_collected", "gold_remaining", "occupants")]


class Snapshot(ctypes.Structure):
    _fields_ = [("window_begin", ctypes.c_int), ("window_end", ctypes.c_int),
                ("regions", RegionStat * REGION_COUNT)]


class GameInput(ctypes.Structure):
    _fields_ = [
        ("round", ctypes.c_int),
        ("grid", (ctypes.c_int * GRID_SIZE) * GRID_SIZE),
        ("my_units", Position * 2),
        ("my_units_gold", ctypes.c_int * 2),
        ("gold_opp", ctypes.c_int),
        ("visible_enemies", Position * 2),
        ("num_visible_npcs", ctypes.c_int),
        ("visible_npcs", NpcInfo * MAX_NPCS),
        ("snapshot_valid", ctypes.c_int),
        ("snapshot", Snapshot),
    ]


class GameOutput(ctypes.Structure):
    _fields_ = [("actions", ctypes.c_int * S), ("k", ctypes.c_int),
                ("order", ctypes.c_int), ("vp", ctypes.c_int)]


def make_input(round_idx):
    gi = GameInput()
    gi.round = round_idx
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            gi.grid[r][c] = -5
    for r in range(3):
        for c in range(3):
            gi.grid[r][c] = 0
    gi.grid[1][1] = 5    # 金币
    gi.grid[2][2] = -1   # 障碍
    gi.grid[0][2] = -3   # 炸弹
    gi.my_units[0] = Position(0, 0)
    gi.my_units[1] = Position(16, 16)
    gi.my_units_gold[0], gi.my_units_gold[1] = 3, 0
    gi.gold_opp = 7
    gi.visible_enemies[0] = Position(1, 2)
    gi.visible_enemies[1] = Position(-1, -1)
    gi.num_visible_npcs = 0
    gi.snapshot_valid = 0
    return gi


def check_output(o):
    acts = list(o.actions)
    assert all(0 <= a <= 4 for a in acts), f"actions 非法: {acts}"
    assert 0 <= o.k <= S, f"k 非法: {o.k}"
    assert o.order in (0, 1), f"order 非法: {o.order}"
    assert o.vp in (0, 1, 2), f"vp 非法: {o.vp}"
    return acts


def main():
    if not os.path.exists(SO_PATH):
        sys.exit("未找到 %s — 先在 src/v1/ 下 make local(本机) 或 make(开发机)" % SO_PATH)
    lib = ctypes.CDLL(SO_PATH)
    lib.moveDecision.argtypes = [ctypes.POINTER(GameInput)]
    lib.moveDecision.restype = GameOutput

    print("########## 基本输出合法性 ##########")
    gi = make_input(0)
    o = lib.moveDecision(ctypes.byref(gi))
    acts = check_output(o)
    print(f"返回: actions={acts} k={o.k} order={o.order} vp={o.vp}")

    print("\n########## 空指针兜底 ##########")
    o = lib.moveDecision(None)
    check_output(o)
    print(f"NULL 输入回落到 actions={list(o.actions)} — OK")

    print("\n########## 500 轮压测(全轮合法) ##########")
    for i in range(500):
        check_output(lib.moveDecision(ctypes.byref(make_input(i))))
    print("500 轮全部合法。")

    print("\n########## 延迟(含 ctypes 调用开销, 仅供相对参考) ##########")
    gi = make_input(2)
    ref = ctypes.byref(gi)
    N = 20000
    t0 = time.perf_counter()
    for _ in range(N):
        lib.moveDecision(ref)
    us = (time.perf_counter() - t0) / N * 1e6
    print(f"平均 {us:.2f}us/轮 (评测机为纯 C 调用, 无 ctypes 开销, 实际更快)")

    print("\n全部通过。")


if __name__ == "__main__":
    main()
