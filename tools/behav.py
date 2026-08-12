#!/usr/bin/env python3
"""behav.py — GoldRush 2.0 平台日志「行为学」指标（纯标准库, 零平台配额）

把已下载的对局日志变成本项目五道验收门要的数字。只读日志、不提交对局、不碰网络。

用法:
  ./behav.py LOG...                 每局一行摘要
  ./behav.py --agg LOG...           追加「按 map 聚合」表 (mean ± sem, 带 n)
  ./behav.py --json LOG...          机读 JSON (每局全字段 + 按 map 聚合)
  ./behav.py --agg-only --agg LOG.. 只要聚合表
  ./behav.py --me 2 LOG...          换视角(只在全信息日志里有意义, 见下)

================================ 日志格式(实测, 非照抄文档) =========================
行1  {"player1": 名, "player2": 名}            —— player1 = 日志所有者
行2  17x17 字符矩阵(真实地形): "0" 空地 "1" 墙 "2" 外圈金币热点
行3+ 每行一个回合 JSON: {"round": r, "start": {...}, "end": {...}}
  start / end 同构:
    grid            17x17 int, 带迷雾: -5 雾 / -3 炸弹 / -1 墙 / 0 空 / >=1 地面金额
                    (角色不进 grid; start.grid 就是本轮决策输入)
    players[]       {id, cost(ns), gold, vision_spent, order?, units[]}
                    units[] = {position:[r,c] | null, gold, actions[], pickup}
    npcs[]          可见 NPC: {id, position, actions, pickup, cost}
    npc             npcs[0] 的重复(或 null), 无额外信息
    overlap_events  实测 50 局 / 25000 轮**全空**, 零信息(别指望它给对手位置)
    vision_r        仅 start 有, {"我方id": 视野半径}; 2=默认5x5 / 3=7x7 / 4=9x9
  end 额外有: dispatch_order(先手方在 [0]) / trample_events[] / burned
  start.units[].actions 是**上一轮**的动作(状态延续), 不是本轮请求 —— 别当请求用。

关键语义(全部由 sim/engine.py 与 50 局 / 25000 轮 / 50000 单位轮的逐步重构校验确认,
零重构失配):
  * 动作码 0=上(row-1) 1=下(row+1) 2=左(col-1) 3=右(col+1) 4=不动。
  * 每回合玩家共 6 步, 由 GameOutput.k 切给两个单位(实测全部 3+3),
    order 字段给出哪个单位先走完自己的那几步。
  * end.units[].actions 是**降级后的实际动作**: 撞墙/出界/撞任意其它玩家单位 → 记 4。
    ⇒ 「请求 4」与「请求方向被挡降级成 4」在日志里**不可直接区分**(见 STAY 一节)。
  * 只有成功移动进入的格子才结算金币; amount = ceil(0.65 * 地面值), 余 35% 留在地上。
  * ⚠ 视角过滤: 对手 units[].position 绝大多数轮为 null。**但不是恒为 null** ——
    对手单位进入我方视野时, 该轮 position / actions / pickup 会被泄露
    (实测 rf_map1_214996 轮 496-498、rf_map2_215006 轮 187-189, 对手单位在 (0,16);
     本批 15000 对手单位轮里 position 可见率仅 13.0%)。
    ⇒ 所有位置类统计仍只能对日志所有者做; 对手只有 cost/gold/vision_spent 逐轮可用。
    本脚本在判断「撞玩家单位」时会用上这些偶发可见的对手位置。
  * ✅ 但对手 **units[].gold 是 100% 可见的**(15000 对手单位轮零缺失, 与 position 无关)。
    想绕过视角过滤拿对手的「每单位轮拾取」通道, 就用逐单位 gold 的**正向增量**
    (实测 h_map1_214889 对手正向增量 2285 vs 末轮持金 2261, 差额 = 烧损), 别用 position。

本脚本对 me(默认=1, 即日志所有者) 一方计算所有位置类指标。

================================== 八类指标的口径 ==================================
1 新格数/单位轮  new_cells_per_ur
    该单位轮里成功移动踏入的格子, **轮内去重**, 并**剔除本单位该轮的起点格**
    (原地弹回起点不算新格)。另给 new_cells_incl_start_per_ur = 不剔起点格的口径,
    以及 fresh_per_ur = 更严的跨轮口径(该格在最近 --fresh 轮内本单位没踏入过)。
2 STAY 率  stay_rate = 生效为不动的步数 / 总步数(总步数 = sum len(actions), 每轮 6)。
    主动/被动的拆分在本日志格式下**严格不可辨识**(日志只留降级后动作), 故给一对界:
      stay_active_lb_rate  四邻全通处的不动步 / 总步  —— 主动 STAY 的**下界**
      stay_bump_ub_rate    其余不动步 / 总步          —— 被动(撞墙降级)的**上界**
    另给一个同分布独立性假设下的点估计(stay_*_est_rate); 一旦被夹到界上,
    stay_split_model_clipped=true, 那时**只读界**。
3 重踏率  retread_per_move  = 轮内重复踏入同一格的次数 / 成功移动步数
          retread_per_step  = 同分子 / 总步数
          retread_ur_frac   = 至少重踏 1 格的**单位轮**占比
          retread_round_frac= 至少一个单位重踏 1 格的**轮**占比
          wasted2_ur_frac   = 「该单位轮 3 步只换来 1 个新格」的占比(= 2 步没换新格)
                              —— 这一项才是历史基线里说的「该轮重踏 2 格」
4 cost 分位  剔除 round < --warmup(默认 4, 即 0..3 预热)后取 P10/25/50/75/90/99/max,
    我方与对手各一套(对手 cost 视角可见)。分位 = 线性插值。
5 座位  first_rate = end.dispatch_order[0] == 我方 id 的轮占比。
6 收入  gold_me/gold_opp 取**末轮 end** 的 gold; net = gold - vision_spent;
    pickup_sum = 我方所有单位所有轮的 pickup 之和;
    烧损 burn_calc = pickup_sum - (末轮 gold - 首轮 start gold)  ← 主口径, 不用负 delta;
    burn_field = sum(end.burned) —— 注意 **end.burned 是全场量(含对手)**,
    只有对手不烧钱(如静止陪练)时两者才该相等; burn_field_matches_calc 会自动核对。
7 命中率/每口金额  hit_rate_all = pickup>0 的单位轮 / 单位轮; gold_per_hit_all = pickup_sum / 命中单位轮。
    中央(默认 = 与 (8,8) 切比雪夫距离 <= 4, 即 engine 的 region 1) 三种拆法都给:
      *_central_end   按该单位轮**末位置**是否在中央分组(基线用的就是这个)
      *_central_start 按**起位置**分组
      *_cell_central  逐格归因: 重放我方每一步、查 start.grid 地面值、按 ceil(0.65v) 结算,
                      只统计落在中央格的拾取; 自检字段 cell_attr_exact_frac 给出
                      「重算金额 == 日志 pickup」的单位轮占比。**我方非先手时 NPC 先动,
                      start.grid 已过期 ⇒ 自检会掉到 ~85-93%, 此时逐格口径不可信。**
8 d<=r 驻留  d_le_r_end_frac = 末位置切比雪夫距离 <= --central-r 的单位轮占比(另给起位置版)。

聚合: 按 map 分组(文件名 mapN, 缺失则退化为地形 sha1 指纹), 每格给 mean ± sem 与 n,
sem = sd/sqrt(n), sd 为样本标准差(ddof=1)。**默认绝不跨 map 合池**; --pool 才给一列
跨图混合的逐局等权均值(不是重新取的中位数), 只为跟历史基线对账。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque

GRID = 17
CENTER = (8, 8)
# 0=上 1=下 2=左 3=右 4=不动
DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))
STAY = 4
WALL_CH = "1"
HOTSPOT_CH = "2"
FOG = -5                 # start.grid 里「看不见」的编码; -3 炸弹 / -1 墙 / 0 空 / >=1 金额
PCTS = (10, 25, 50, 75, 90, 99)
POOL_KEY = "zALL(mixed-maps)"


# --------------------------------------------------------------------------- io

def load(path):
    """返回 (names, terrain_rows, rounds)。terrain 归一成 list[list[str]]。"""
    with open(path) as fh:
        text = fh.read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        raise ValueError("日志少于 3 行, 不是对局日志")
    names = json.loads(lines[0])
    raw = json.loads(lines[1])
    terrain = [list(row) if isinstance(row, str) else [str(x) for x in row] for row in raw]
    if len(terrain) != GRID or any(len(r) != GRID for r in terrain):
        raise ValueError("行2 不是 17x17 地形")
    rounds = [json.loads(ln) for ln in lines[2:]]
    return names, terrain, rounds


def terrain_sig(terrain):
    flat = "".join("".join(row) for row in terrain)
    return hashlib.sha1(flat.encode()).hexdigest()[:8]


def map_label(path, terrain):
    """优先用文件名里的 mapN; 否则退化成地形指纹 (禁止把不同图合池)。"""
    m = re.search(r"map([0-9A-Za-z]+)", os.path.basename(path))
    if m:
        return "map" + m.group(1)
    return "sig:" + terrain_sig(terrain)


def game_label(path):
    m = re.search(r"([0-9]{4,})", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


# ------------------------------------------------------------------- numerics

def pctl(vals, p):
    """线性插值分位 (与 numpy 默认 'linear' 一致)。空表返回 nan。"""
    if not vals:
        return float("nan")
    v = sorted(vals)
    if len(v) == 1:
        return float(v[0])
    k = (len(v) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(v[int(k)])
    return float(v[lo] + (v[hi] - v[lo]) * (k - lo))


def safe_div(a, b):
    return float(a) / b if b else float("nan")


def cheb(rc, center=CENTER):
    return max(abs(rc[0] - center[0]), abs(rc[1] - center[1]))


# --------------------------------------------------------------- per-game core

def blocked_dirs(rc, walls, occupied):
    """返回被挡的方向集合 (出界 / 墙 / 其它已知玩家单位所在格)。"""
    out = []
    for d in range(4):
        r = rc[0] + DELTAS[d][0]
        c = rc[1] + DELTAS[d][1]
        if not (0 <= r < GRID and 0 <= c < GRID):
            out.append(d)
        elif walls[r][c]:
            out.append(d)
        elif (r, c) in occupied:
            out.append(d)
    return out


def analyse(path, me=1, central_r=4, warmup=4, fresh_window=3):
    names, terrain, rounds = load(path)
    walls = [[terrain[r][c] == WALL_CH for c in range(GRID)] for r in range(GRID)]
    opp = 2 if me == 1 else 1
    p_names = {1: names.get("player1", "p1"), 2: names.get("player2", "p2")}

    # --- 累加器 -------------------------------------------------------------
    ur = 0                       # 单位轮数
    steps = 0                    # 总步数 (= sum len(actions))
    moves = 0                    # 成功移动步数
    stays = 0                    # 生效为不动的步数
    stay_by_b = Counter()        # 不动步按「被挡方向数 b」分布
    eff_count = Counter()        # 生效方向 d 的步数 (d=0..3)
    open_count = Counter()       # 方向 d 未被挡的步数 (全部步, 不只不动步)

    new_cells = 0                # 主口径: 轮内去重、剔除本轮起点格
    new_cells_incl = 0           # 轮内去重, 起点格若被重新踏入也算
    fresh_cells = 0              # 严口径: 最近 fresh_window 轮内本单位未踏入过
    retreads = 0                 # 轮内重复踏入同一格的次数 = moves - 轮内去重格数
    ur_with_retread = 0
    rounds_with_retread = 0
    newc_hist = Counter()        # 每单位轮的新格数分布
    wasted_hist = Counter()      # 每单位轮 (步数 - 新格数) 分布

    cost = {me: [], opp: []}
    cost_all = {me: [], opp: []}
    first = 0
    dispatch_seen = 0

    pickup_sum = 0
    hits = 0                     # pickup>0 的单位轮
    burned_field = 0
    trample_events = 0

    c_end_ur = c_end_hit = c_end_pk = 0     # 中央 = 单位轮末位置 cheb<=r
    c_start_ur = c_start_hit = c_start_pk = 0
    d_le_end = d_le_start = 0

    cell_ok = cell_ur = 0        # 逐格归因自检
    cell_fog_entries = 0
    cell_ev_all = cell_gold_all = 0
    cell_ev_c = cell_gold_c = 0
    cell_hit_c = 0               # 至少有一次中央拾取的单位轮
    recon_bad = 0
    opp_pos_seen = 0

    hist = defaultdict(lambda: deque(maxlen=max(1, fresh_window)))   # 单位 -> 最近若干轮踏入集
    start_gold = None
    last_end = None
    n_units = 0

    for rd in rounds:
        rnum = rd.get("round", 0)
        st = rd.get("start") or {}
        en = rd.get("end") or {}
        S = {p.get("id"): p for p in (st.get("players") or [])}
        E = {p.get("id"): p for p in (en.get("players") or [])}
        last_end = en
        if start_gold is None:
            start_gold = (S.get(me) or {}).get("gold", 0)

        for pid in (me, opp):
            pl = E.get(pid)
            if pl is None:
                continue
            cost_all[pid].append(pl.get("cost", 0) or 0)
            if rnum >= warmup:
                cost[pid].append(pl.get("cost", 0) or 0)

        dorder = en.get("dispatch_order") or []
        if dorder:
            dispatch_seen += 1
            if dorder[0] == me:
                first += 1
        burned_field += en.get("burned") or 0
        trample_events += len(en.get("trample_events") or [])

        mine_s = S.get(me) or {}
        mine_e = E.get(me) or {}
        us = mine_s.get("units") or []
        ue = mine_e.get("units") or []
        if len(us) != len(ue) or not ue:
            continue
        n_units = max(n_units, len(ue))
        if any(u.get("position") is None for u in us):
            continue                                  # 视角过滤: 该方位置不可见
        opp_cells = set()
        for u in (S.get(opp) or {}).get("units") or []:
            if u.get("position") is not None:
                opp_pos_seen += 1
                opp_cells.add(tuple(u["position"]))

        order = mine_e.get("order", mine_s.get("order", 0)) or 0
        if order not in (0, 1):
            order = 0

        live = [tuple(u["position"]) for u in us]      # 逐步推进的实时位置
        origin = list(live)
        ground = [row[:] for row in (st.get("grid") or [])]
        have_grid = len(ground) == GRID and all(len(r) == GRID for r in ground)

        entered = {i: [] for i in range(len(ue))}
        got = {i: 0 for i in range(len(ue))}
        got_central = {i: 0 for i in range(len(ue))}

        idx_order = [order, 1 - order] if len(ue) == 2 else list(range(len(ue)))
        for ui in idx_order:
            acts = ue[ui].get("actions") or []
            for a in acts:
                steps += 1
                here = live[ui]
                occupied = {live[j] for j in range(len(live)) if j != ui} | opp_cells
                blk = set(blocked_dirs(here, walls, occupied))
                for d in range(4):
                    if d not in blk:
                        open_count[d] += 1
                if a == STAY:
                    stays += 1
                    stay_by_b[len(blk)] += 1
                    continue
                eff_count[a] += 1
                moves += 1
                nr = here[0] + DELTAS[a][0]
                nc = here[1] + DELTAS[a][1]
                live[ui] = (nr, nc)
                entered[ui].append((nr, nc))
                if have_grid:
                    val = ground[nr][nc]
                    if val == FOG:
                        cell_fog_entries += 1
                    elif val > 0:
                        amt = (65 * val + 99) // 100
                        ground[nr][nc] = val - amt
                        got[ui] += amt
                        cell_ev_all += 1
                        cell_gold_all += amt
                        if cheb((nr, nc)) <= central_r:
                            cell_ev_c += 1
                            cell_gold_c += amt
                            got_central[ui] += amt

        for ui in range(len(ue)):
            if live[ui] != tuple(ue[ui]["position"]):
                recon_bad += 1

        any_retread = False
        for ui in range(len(ue)):
            ur += 1
            ent = entered[ui]
            uniq = set(ent)
            nb = len(uniq - {origin[ui]})
            new_cells += nb
            new_cells_incl += len(uniq)
            rep = len(ent) - len(uniq)
            retreads += rep
            if rep > 0:
                ur_with_retread += 1
                any_retread = True
            newc_hist[nb] += 1
            wasted_hist[len(ue[ui].get("actions") or []) - nb] += 1

            recent = set()
            for prev in hist[ui]:
                recent |= prev
            fresh_cells += len(uniq - {origin[ui]} - recent)
            hist[ui].append(set(ent))

            pk = ue[ui].get("pickup") or 0
            pickup_sum += pk
            if pk > 0:
                hits += 1
            ed = cheb(tuple(ue[ui]["position"]))
            sd = cheb(origin[ui])
            if ed <= central_r:
                d_le_end += 1
                c_end_ur += 1
                c_end_pk += pk
                if pk > 0:
                    c_end_hit += 1
            if sd <= central_r:
                d_le_start += 1
                c_start_ur += 1
                c_start_pk += pk
                if pk > 0:
                    c_start_hit += 1

            if have_grid:
                cell_ur += 1
                if got[ui] == pk:
                    cell_ok += 1
            if got_central[ui] > 0:
                cell_hit_c += 1
        if any_retread:
            rounds_with_retread += 1

    # --- 方向请求率 -> 主动/被动 STAY 点估计 --------------------------------
    # 日志只有降级后的动作, 所以「请求就是 4」与「请求方向被挡→4」严格不可分。
    # 可辨识的是一对界:
    #   b=0 (四邻全通) 的不动步  必然  是请求 4        -> 主动 STAY 下界
    #   其余不动步        可能  是撞墙降级             -> 被动 STAY 上界
    # 另给一个模型点估计 (假设: 请求方向分布在步间同分布、且与该步被挡情况独立):
    #   生效动作 d(!=4) 只可能来自请求 d, 且该步 d 必未被挡  =>
    #     pi_d = #(生效 d) / #(d 未被挡的步)
    #   期望被挡降级步数 = sum_step sum_{d in B(step)} pi_d
    #                   = sum_d pi_d * (steps - open_count[d])
    # 该独立性假设在目标导向策略上会被违背(想去的方向恰恰常被挡), 所以点估计一律夹到
    # [下界, 上界] 里; 一旦被夹住, clipped 标志会置真 —— 那就只能读界, 别读点估。
    stay_open = stay_by_b[0]                 # b=0 ⇒ 任何方向都能走 ⇒ 必是请求 4
    stay_boxed = stay_by_b[4]                # 四面全挡 ⇒ 请求任何方向都会降级
    pi = {}
    for d in range(4):
        pi[d] = safe_div(eff_count[d], open_count[d])
    exp_bumps = 0.0
    for d in range(4):
        if not math.isnan(pi[d]):
            exp_bumps += pi[d] * (steps - open_count[d])
    bump_ub = float(stays - stay_open)
    bump_est = min(bump_ub, max(0.0, exp_bumps))
    clipped = bool(exp_bumps > bump_ub + 1e-9 or exp_bumps < -1e-9)
    active_est = stays - bump_est

    fin_players = (last_end or {}).get("players") or []
    F = {p.get("id"): p for p in fin_players}
    gold_me = (F.get(me) or {}).get("gold", 0) or 0
    gold_opp = (F.get(opp) or {}).get("gold", 0) or 0
    vis_me = (F.get(me) or {}).get("vision_spent", 0) or 0
    vis_opp = (F.get(opp) or {}).get("vision_spent", 0) or 0
    start_gold = start_gold or 0
    burn_calc = pickup_sum - (gold_me - start_gold)

    out = {
        "path": os.path.abspath(path),
        "game": game_label(path),
        "map": map_label(path, terrain),
        "terrain_sig": terrain_sig(terrain),
        "obstacles": sum(row.count(WALL_CH) for row in terrain),
        "hotspots": sum(row.count(HOTSPOT_CH) for row in terrain),
        "me": me,
        "me_name": p_names.get(me),
        "opp_name": p_names.get(opp),
        "n_rounds": len(rounds),
        "n_units": n_units,
        "unit_rounds": ur,
        "steps": steps,
        "moves": moves,
        "recon_mismatch": recon_bad,
        "opp_positions_visible": opp_pos_seen,

        # 1. 新格数 / 单位轮
        "new_cells_per_ur": safe_div(new_cells, ur),
        "new_cells_incl_start_per_ur": safe_div(new_cells_incl, ur),
        "fresh_window": fresh_window,
        "fresh_per_ur": safe_div(fresh_cells, ur),
        "new_cells_hist": {str(k): v for k, v in sorted(newc_hist.items())},
        "wasted_steps_hist": {str(k): v for k, v in sorted(wasted_hist.items())},

        # 2. STAY 率
        "stay_rate": safe_div(stays, steps),
        "stay_active_lb_rate": safe_div(stay_open, steps),
        "stay_bump_ub_rate": safe_div(stays - stay_open, steps),
        "stay_active_est_rate": safe_div(active_est, steps),
        "stay_bump_est_rate": safe_div(bump_est, steps),
        "stay_split_model_clipped": clipped,
        "stay_bump_model_raw_rate": safe_div(exp_bumps, steps),
        "stay_boxed_rate": safe_div(stay_boxed, steps),
        "stay_by_blocked_dirs": {str(k): v for k, v in sorted(stay_by_b.items())},
        "req_rate_by_dir": {str(d): pi[d] for d in range(4)},

        # 3. 重踏率
        "retread_per_move": safe_div(retreads, moves),
        "retread_per_step": safe_div(retreads, steps),
        "retread_ur_frac": safe_div(ur_with_retread, ur),
        "retread_round_frac": safe_div(rounds_with_retread, len(rounds)),
        "wasted2_ur_frac": safe_div(wasted_hist[2], ur),

        # 4. cost 分位 (剔除 round < warmup)
        "cost_warmup_dropped": warmup,
        "cost_me_n": len(cost[me]),
        "cost_me_max": float(max(cost[me])) if cost[me] else float("nan"),
        "cost_opp_n": len(cost[opp]),
        "cost_opp_max": float(max(cost[opp])) if cost[opp] else float("nan"),

        # 5. 座位
        "first_rate": safe_div(first, dispatch_seen),
        "first_rounds": first,
        "dispatch_rounds": dispatch_seen,

        # 6. 收入 / 烧损
        "start_gold_me": start_gold,
        "gold_me": gold_me,
        "gold_opp": gold_opp,
        "vision_spent_me": vis_me,
        "vision_spent_opp": vis_opp,
        "net_me": gold_me - vis_me,
        "net_opp": gold_opp - vis_opp,
        "pickup_sum": pickup_sum,
        "burn_calc": burn_calc,
        "burn_field": burned_field,
        "burn_field_matches_calc": burn_calc == burned_field,
        "trample_events": trample_events,

        # 7. 命中率 / 每口金额
        "hit_rate_all": safe_div(hits, ur),
        "gold_per_hit_all": safe_div(pickup_sum, hits),
        "mean_pickup_per_ur": safe_div(pickup_sum, ur),
        "hit_rate_central_end": safe_div(c_end_hit, c_end_ur),
        "gold_per_hit_central_end": safe_div(c_end_pk, c_end_hit),
        "hit_rate_central_start": safe_div(c_start_hit, c_start_ur),
        "gold_per_hit_central_start": safe_div(c_start_pk, c_start_hit),
        "hit_rate_cell_central": safe_div(cell_hit_c, ur),
        "gold_per_hit_cell_central": safe_div(cell_gold_c, cell_hit_c),
        "cell_events_all": cell_ev_all,
        "cell_gold_all": cell_gold_all,
        "gold_per_cell_event_all": safe_div(cell_gold_all, cell_ev_all),
        "cell_events_central": cell_ev_c,
        "cell_gold_central": cell_gold_c,
        "gold_per_cell_event_central": safe_div(cell_gold_c, cell_ev_c),
        "cell_attr_exact_frac": safe_div(cell_ok, cell_ur),
        "cell_fog_entries": cell_fog_entries,

        # 8. d<=r 驻留
        "central_r": central_r,
        "d_le_r_end_frac": safe_div(d_le_end, ur),
        "d_le_r_start_frac": safe_div(d_le_start, ur),
    }
    for p in PCTS:
        out["cost_me_p%d" % p] = pctl(cost[me], p)
        out["cost_opp_p%d" % p] = pctl(cost[opp], p)
    out["cost_me_p50_raw_all_rounds"] = pctl(cost_all[me], 50)
    out["cost_opp_p50_raw_all_rounds"] = pctl(cost_all[opp], 50)
    return out


# ------------------------------------------------------------------ aggregate

AGG_KEYS = [
    ("新格/单位轮", "new_cells_per_ur", "%7.4f"),
    ("新格(含起点)", "new_cells_incl_start_per_ur", "%7.4f"),
    ("跨轮新鲜/单位轮", "fresh_per_ur", "%7.4f"),
    ("STAY 率", "stay_rate", "%7.4f"),
    ("  主动 STAY 下界", "stay_active_lb_rate", "%7.4f"),
    ("  被动 STAY 上界", "stay_bump_ub_rate", "%7.4f"),
    ("  主动 STAY 点估", "stay_active_est_rate", "%7.4f"),
    ("  被动 STAY 点估", "stay_bump_est_rate", "%7.4f"),
    ("  四面全挡步占比", "stay_boxed_rate", "%7.4f"),
    ("重踏/成功移动", "retread_per_move", "%7.4f"),
    ("重踏/总步", "retread_per_step", "%7.4f"),
    ("≥1 重踏的单位轮占比", "retread_ur_frac", "%7.4f"),
    ("≥1 重踏的轮占比", "retread_round_frac", "%7.4f"),
    ("恰好 2 步无新格的单位轮占比", "wasted2_ur_frac", "%7.4f"),
    ("我方 cost P10", "cost_me_p10", "%7.1f"),
    ("我方 cost P25", "cost_me_p25", "%7.1f"),
    ("我方 cost P50", "cost_me_p50", "%7.1f"),
    ("我方 cost P75", "cost_me_p75", "%7.1f"),
    ("我方 cost P90", "cost_me_p90", "%7.1f"),
    ("我方 cost P99", "cost_me_p99", "%7.1f"),
    ("我方 cost max", "cost_me_max", "%7.1f"),
    ("对手 cost P10", "cost_opp_p10", "%9.1f"),
    ("对手 cost P25", "cost_opp_p25", "%9.1f"),
    ("对手 cost P50", "cost_opp_p50", "%9.1f"),
    ("对手 cost P75", "cost_opp_p75", "%9.1f"),
    ("对手 cost P90", "cost_opp_p90", "%9.1f"),
    ("对手 cost P99", "cost_opp_p99", "%9.1f"),
    ("对手 cost max", "cost_opp_max", "%9.1f"),
    ("先手率", "first_rate", "%7.4f"),
    ("我方 gold", "gold_me", "%8.1f"),
    ("我方 vision_spent", "vision_spent_me", "%7.1f"),
    ("我方 net(gold-vision)", "net_me", "%8.1f"),
    ("对手 gold", "gold_opp", "%8.1f"),
    ("对手 net", "net_opp", "%8.1f"),
    ("sum(pickup)", "pickup_sum", "%8.1f"),
    ("烧损(pickup-Δgold)", "burn_calc", "%7.1f"),
    ("烧损(end.burned 求和)", "burn_field", "%7.1f"),
    ("踩踏事件", "trample_events", "%7.1f"),
    ("命中率(全场)", "hit_rate_all", "%7.4f"),
    ("每口金额(全场)", "gold_per_hit_all", "%7.4f"),
    ("mean pickup/单位轮", "mean_pickup_per_ur", "%7.4f"),
    ("命中率(中央,末位置)", "hit_rate_central_end", "%7.4f"),
    ("每口金额(中央,末位置)", "gold_per_hit_central_end", "%7.4f"),
    ("命中率(中央,起位置)", "hit_rate_central_start", "%7.4f"),
    ("每口金额(中央,起位置)", "gold_per_hit_central_start", "%7.4f"),
    ("命中率(中央,逐格归因)", "hit_rate_cell_central", "%7.4f"),
    ("每口金额(中央,逐格归因)", "gold_per_hit_cell_central", "%7.4f"),
    ("每次拾取金额(全场,逐格)", "gold_per_cell_event_all", "%7.4f"),
    ("每次拾取金额(中央,逐格)", "gold_per_cell_event_central", "%7.4f"),
    ("逐格归因自检命中率", "cell_attr_exact_frac", "%7.4f"),
    ("d<=r 驻留(末位置)", "d_le_r_end_frac", "%7.4f"),
    ("d<=r 驻留(起位置)", "d_le_r_start_frac", "%7.4f"),
]


def stats(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "sd": float("nan"), "sem": float("nan"), "values": []}
    mean = sum(vals) / n
    if n > 1:
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
        sem = sd / math.sqrt(n)
    else:
        sd = float("nan")
        sem = float("nan")
    return {"n": n, "mean": mean, "sd": sd, "sem": sem,
            "values": [float(v) for v in vals]}


def aggregate(games, keys=None, pool=False):
    """按 map 分组。**默认绝不跨 map 合池。**

    pool=True 时额外给一个 "ALL(mixed-maps)" 组: 它是**逐局标量的等权均值 ± sem**,
    不是把不同图的原始样本混在一起重新取分位/中位数(那是被明令禁止的口径)。
    只用于跟历史基线对账, 不得用于判优。
    """
    keys = keys or [k for _, k, _ in AGG_KEYS]
    groups = defaultdict(list)
    for g in games:
        groups[g["map"]].append(g)
    if pool and len(groups) > 1:
        groups[POOL_KEY] = list(games)
    out = {}
    for mp, gs in groups.items():
        blk = {"n_games": len(gs), "games": [g["game"] for g in gs],
               "terrain_sigs": sorted({g["terrain_sig"] for g in gs}),
               "metrics": {}}
        for k in keys:
            blk["metrics"][k] = stats([g.get(k) for g in gs])
        out[mp] = blk
    return out


# --------------------------------------------------------------------- output

HDR = ("%-26s %-7s %5s %7s %7s %7s %7s %7s %7s %8s %8s %8s %10s %6s %7s %7s %7s %6s %7s %6s %7s %6s" %
       ("log", "map", "R", "new/ur", "frsh/ur", "stay%", "ret/mv", "retUR%", "w2UR%",
        "cP50", "cP90", "cP99", "oppP50", "1st%", "net", "gold", "pk", "burn",
        "hit%", "/hit", "cHit%", "c/hit"))


def _n(x, spec, width):
    """nan 一律渲染成右对齐的 '-', 别拿 nan 冒充 0。"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "%*s" % (width, "-")
    return spec % x


def fmt_line(g):
    return " ".join([
        "%-26s" % os.path.basename(g["path"])[:26],
        "%-7s" % g["map"],
        "%5d" % g["n_rounds"],
        _n(g["new_cells_per_ur"], "%7.3f", 7),
        _n(g.get("fresh_per_ur"), "%7.3f", 7),
        _n(100 * g["stay_rate"], "%6.2f%%", 7),
        _n(g["retread_per_move"], "%7.3f", 7),
        _n(100 * g["retread_ur_frac"], "%6.2f%%", 7),
        _n(100 * g["wasted2_ur_frac"], "%6.2f%%", 7),
        _n(g["cost_me_p50"], "%8.1f", 8),
        _n(g["cost_me_p90"], "%8.1f", 8),
        _n(g["cost_me_p99"], "%8.1f", 8),
        _n(g["cost_opp_p50"], "%10.1f", 10),
        _n(100 * g["first_rate"], "%5.1f%%", 6),
        "%7d" % g["net_me"],
        "%7d" % g["gold_me"],
        "%7d" % g["pickup_sum"],
        "%6d" % g["burn_calc"],
        _n(100 * g["hit_rate_all"], "%6.2f%%", 7),
        _n(g["gold_per_hit_all"], "%6.3f", 6),
        _n(100 * g["hit_rate_central_end"], "%6.2f%%", 7),
        _n(g["gold_per_hit_central_end"], "%6.3f", 6),
    ])


def print_games(games, warn=True):
    print(HDR)
    print("-" * len(HDR))
    for g in games:
        print(fmt_line(g))
    if warn:
        for g in games:
            msgs = []
            if g["recon_mismatch"]:
                msgs.append("轨迹重构失配 %d 单位轮(动作码或字段语义有变)" % g["recon_mismatch"])
            if not g["burn_field_matches_calc"]:
                msgs.append("end.burned 求和 %d != pickup-Δgold %d (end.burned 是全场量, 含对手)"
                            % (g["burn_field"], g["burn_calc"]))
            if g["cell_attr_exact_frac"] < 0.98:
                msgs.append("逐格归因自检仅 %.1f%% (先手不在我方 / 迷雾多 ⇒ 中央逐格口径不可信)"
                            % (100 * g["cell_attr_exact_frac"]))
            if g["opp_positions_visible"]:
                msgs.append("对手位置可见 %d 次(单位进我方视野时泄露), 碰撞判定已计入"
                            % g["opp_positions_visible"])
            if g["stay_split_model_clipped"]:
                msgs.append("主动/被动 STAY 点估被夹到可辨识界(模型 raw=%.4f, 界=[%.4f,%.4f]) "
                            "⇒ 只读上下界, 别读点估"
                            % (g["stay_bump_model_raw_rate"], 0.0, g["stay_bump_ub_rate"]))
            if msgs:
                print("  ! %s: %s" % (os.path.basename(g["path"]), "; ".join(msgs)))


def print_agg(agg, central_r=4, fresh_window=3):
    maps = sorted(agg)
    width = 26
    head = "%-*s" % (width, "指标 (mean ± sem)")
    for mp in maps:
        tag = "ALL(mixed)" if mp == POOL_KEY else mp
        head += "  %-26s" % ("%s (n=%d)" % (tag, agg[mp]["n_games"]))
    print()
    print("=" * len(head))
    print("按 map 聚合  —— 中央 = 与 (8,8) 切比雪夫距离 <= %d; 跨轮新鲜窗 = %d 轮; 禁止跨 map 合池"
          % (central_r, fresh_window))
    if POOL_KEY in agg:
        print("⚠ ALL(mixed) 列混了不同图, 只是逐局标量的等权均值 ± sem(不是重新取的中位数), "
              "仅供与历史基线对账, 不得用于判优。")
    print("=" * len(head))
    print(head)
    print("-" * len(head))
    for label, key, spec in AGG_KEYS:
        row = "%-*s" % (width, label)
        any_val = False
        for mp in maps:
            s = agg[mp]["metrics"].get(key)
            if not s or s["n"] == 0:
                row += "  %-26s" % "-"
                continue
            any_val = True
            m = s["mean"]
            sem = s["sem"]
            txt = (spec % m).strip()
            if s["n"] > 1 and not math.isnan(sem):
                txt += " ± " + (spec % sem).strip()
            txt += "  n=%d" % s["n"]
            row += "  %-26s" % txt
        if any_val:
            print(row)
    print("-" * len(head))
    for mp in maps:
        tag = "ALL(mixed)" if mp == POOL_KEY else mp
        print("%s: n=%d  局=%s  地形指纹=%s" %
              (tag, agg[mp]["n_games"], ",".join(agg[mp]["games"]),
               ",".join(agg[mp]["terrain_sigs"])))


# ----------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="behav.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="GoldRush 平台日志行为学指标 (纯标准库, 只读, 零平台配额)",
        epilog=__doc__.split("=====", 1)[-1] if "=====" in __doc__ else None,
    )
    ap.add_argument("logs", nargs="+", help="对局日志路径")
    ap.add_argument("--me", type=int, default=1, choices=(1, 2),
                    help="视角: 1=日志所有者(默认); 2 只在全信息日志里有意义")
    ap.add_argument("--central-r", type=int, default=4,
                    help="中央定义: 与 (8,8) 的切比雪夫距离 <= R (默认 4, 即 engine 的 region 1)")
    ap.add_argument("--warmup", type=int, default=4,
                    help="cost 分位剔除 round < N 的预热轮 (默认 4, 即剔 0..3)")
    ap.add_argument("--fresh", type=int, default=3,
                    help="跨轮新鲜格的回溯窗口轮数 (默认 3)")
    ap.add_argument("--agg", action="store_true", help="追加按 map 聚合表 (mean ± sem, 带 n)")
    ap.add_argument("--agg-only", action="store_true", help="只打聚合表, 不打逐局行")
    ap.add_argument("--pool", action="store_true",
                    help="额外给一列跨图混合的逐局等权均值 ± sem (仅供与历史基线对账, 不得判优)")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON (逐局全字段 + 聚合)")
    ap.add_argument("--no-warn", action="store_true", help="不打印口径告警")
    a = ap.parse_args(argv)

    games = []
    errs = []
    for path in a.logs:
        try:
            games.append(analyse(path, me=a.me, central_r=a.central_r,
                                 warmup=a.warmup, fresh_window=a.fresh))
        except Exception as exc:                                  # noqa: BLE001
            errs.append((path, repr(exc)))
    if not games:
        for path, e in errs:
            print("解析失败 %s: %s" % (path, e), file=sys.stderr)
        return 1

    agg = aggregate(games, [k for _, k, _ in AGG_KEYS], pool=a.pool)

    if a.json:
        json.dump({"games": games, "agg_by_map": agg,
                   "caliber": {"me": a.me, "central_r": a.central_r,
                               "warmup_rounds_dropped": a.warmup,
                               "fresh_window": a.fresh,
                               "sem": "sd/sqrt(n), sd 为样本标准差(ddof=1)",
                               "percentile": "线性插值(numpy 'linear')"},
                   "errors": [{"path": p, "error": e} for p, e in errs]},
                  sys.stdout, ensure_ascii=False, indent=1, default=str)
        sys.stdout.write("\n")
        return 0

    if not a.agg_only:
        print_games(games, warn=not a.no_warn)
    if a.agg or a.agg_only:
        print_agg(agg, central_r=a.central_r, fresh_window=a.fresh)
    for path, e in errs:
        print("解析失败 %s: %s" % (path, e), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
