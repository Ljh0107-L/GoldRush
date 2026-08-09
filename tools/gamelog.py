#!/usr/bin/env python3
"""gamelog.py — GoldRush 2.0 对局日志分析（纯标准库）

把 gr.py 下载的日志变成可决策的数字：净分、每轮耗时分位数、先手率、
事件统计、金币曲线。这是"提交→对战→取日志→分析→改代码"闭环的分析环节。

用法:
  ./gamelog.py logs/game_137528.log              单局摘要
  ./gamelog.py logs/*.log                        多局逐一摘要
  ./gamelog.py --rounds logs/game_137528.log     追加逐 100 轮金币曲线

日志格式(见 AGENT.md §13):
  行1  {"player1":名, "player2":名}
  行2  17x17 真实地图(字符串): "0"空地 "1"障碍 "2"外圈金币高频热点
       注意: "2" 曾被本注释误标为"炸弹刷新位", 已被官方全信息日志推翻(见 sim/README.md §5.2)。
       实测语义 = 外圈金币生成热点: 20 格承担外圈落点 54%(618/1142), 单格富集约 9.7x;
       炸弹与它无关, 会落在全部非墙 eligible 格上(token-2 格同样会被炸)。
  行3+ 每行一个回合 {round, start:{...}, end:{players[].cost(ns)/gold/vision_spent,
                     dispatch_order, trample_events, burned, ...}}
注意: 日志按视角过滤, 对手 units[].position 为 null, 但 cost/gold/vision_spent 可见。
"""
import argparse
import json
import sys


def _fmt_ns(ns):
    if ns < 1e3:
        return "%dns" % ns
    if ns < 1e6:
        return "%.2fus" % (ns / 1e3)
    return "%.2fms" % (ns / 1e6)


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100))
    return sorted_vals[idx]


def load(path):
    with open(path) as f:
        lines = f.read().splitlines()
    names = json.loads(lines[0])
    truemap = json.loads(lines[1])
    rounds = [json.loads(x) for x in lines[2:] if x.strip()]
    return names, truemap, rounds


def analyze(path, show_rounds=False):
    names, truemap, rounds = load(path)
    p1, p2 = names.get("player1", "p1"), names.get("player2", "p2")
    obstacles = sum(row.count("1") for row in truemap)
    bomb_spawns = sum(row.count("2") for row in truemap)

    costs = {1: [], 2: []}
    first_mover = {1: 0, 2: 0}
    pickups = {1: 0, 2: 0}
    burned_total = 0
    trample_total = 0
    curve = {}          # round -> (gold1, gold2)
    last = None

    for rd in rounds:
        end = rd.get("end") or {}
        for pl in end.get("players", []):
            pid = pl.get("id")
            if pid in costs:
                costs[pid].append(pl.get("cost", 0))
                for u in pl.get("units", []):
                    pickups[pid] += u.get("pickup") or 0
        dorder = end.get("dispatch_order") or []
        if dorder and dorder[0] in first_mover:
            first_mover[dorder[0]] += 1
        burned_total += end.get("burned") or 0
        trample_total += len(end.get("trample_events") or [])
        r = rd.get("round", 0)
        golds = {pl.get("id"): pl.get("gold", 0) for pl in end.get("players", [])}
        if (r + 1) % 100 == 0:
            curve[r + 1] = (golds.get(1, 0), golds.get(2, 0))
        last = end

    finals = {pl.get("id"): pl for pl in (last or {}).get("players", [])}
    total = len(rounds)

    print("=" * 64)
    print("%s   [%s vs %s]   %d 回合   障碍 %d 格 / 炸弹刷新位 %d 格"
          % (path, p1, p2, total, obstacles, bomb_spawns))
    print("-" * 64)
    print("%-4s %-14s %6s %6s %6s   %8s %8s %8s   %5s" %
          ("", "选手", "毛金币", "视野费", "净分", "P50", "P90", "P99", "先手"))
    nets = {}
    for pid, name in ((1, p1), (2, p2)):
        fin = finals.get(pid, {})
        gold = fin.get("gold", 0)
        vis = fin.get("vision_spent", 0)
        nets[pid] = gold - vis
        cs = sorted(costs[pid])
        print("%-4s %-14s %6d %6d %6d   %8s %8s %8s   %d/%d" %
              ("P%d" % pid, name, gold, vis, nets[pid],
               _fmt_ns(_pct(cs, 50)), _fmt_ns(_pct(cs, 90)), _fmt_ns(_pct(cs, 99)),
               first_mover[pid], total))
    if nets.get(1, 0) != nets.get(2, 0):
        wid = 1 if nets[1] > nets[2] else 2
        print("胜者: P%d %s (净分 %d : %d)" % (wid, {1: p1, 2: p2}[wid], nets[1], nets[2]))
    else:
        print("净分相同 %d : %d — 按 P90 低者胜" % (nets.get(1, 0), nets.get(2, 0)))
    print("拾取事件: P1=%d P2=%d   全场炸弹损失(含NPC)=%d   踩踏事件=%d"
          % (pickups[1], pickups[2], burned_total, trample_total))

    if show_rounds and curve:
        print("金币曲线(毛):")
        for r in sorted(curve):
            g1, g2 = curve[r]
            print("  轮%3d   P1=%-5d P2=%-5d 差=%+d" % (r, g1, g2, g1 - g2))


def main():
    ap = argparse.ArgumentParser(description="GoldRush 对局日志分析")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--rounds", action="store_true", help="追加逐100轮金币曲线")
    a = ap.parse_args()
    for path in a.logs:
        try:
            analyze(path, a.rounds)
        except Exception as e:
            print("解析失败 %s: %s" % (path, e), file=sys.stderr)


if __name__ == "__main__":
    main()
