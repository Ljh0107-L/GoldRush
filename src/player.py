"""player.py — GoldRush 2.0 · P0 基线

目标不是强，是"绝不判负 + 比随机好"。作为提交链路的首个可用版本。
  - 全程 try/except，任何异常都回落到合法输出
  - 输出强制 sanitize（长度/类型/区间）
  - 无 print、无文件 IO（会计入 P90 延迟）
  - 只用标准库（评测环境没有 game_api.py，import 它会直接判负）

策略：各自 3 步，BFS 找最近的可见金币，避开炸弹与障碍。
动作: 0=上 1=下 2=左 3=右 4=不动
"""

GRID = 17
S = 6
STAY = 4
# 动作 -> (dr, dc)
DELTA = ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0))

FOG, BOMB, OBSTACLE, EMPTY = -5, -3, -1, 0

SAFE = [STAY] * S + [3, 0, 0]


class Player:
    def __init__(self):
        # 预热窗口有 10s，这里预计算邻居表，避免每轮重复建
        self.neighbors = []
        for r in range(GRID):
            for c in range(GRID):
                nb = []
                for a in range(4):
                    dr, dc = DELTA[a]
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < GRID and 0 <= nc < GRID:
                        nb.append((a, nr, nc))
                self.neighbors.append(nb)

    # ---------- 对外接口 ----------

    def MoveDecision(self, game_input):
        try:
            return self._sanitize(self._decide(game_input))
        except Exception:
            return list(SAFE)

    # ---------- 决策 ----------

    def _decide(self, gi):
        grid = gi.grid
        units = list(gi.my_units)

        # 两个角色各 3 步；避免抢同一块金币
        claimed = set()
        plans = []
        for i in range(2):
            r, c = units[i][0], units[i][1]
            plans.append(self._plan(grid, r, c, 3, claimed))

        actions = plans[0] + plans[1]
        # 持币多的先走：先手更可能吃到争议金币，也更该先脱离风险
        order = 0 if gi.my_units_gold[0] >= gi.my_units_gold[1] else 1
        return actions + [3, order, 0]

    def _plan(self, grid, sr, sc, budget, claimed):
        """BFS 找最近金币，返回 budget 个动作。"""
        target = self._nearest_gold(grid, sr, sc, claimed)
        if target is None:
            return [STAY] * budget
        claimed.add(target)

        path = self._path_to(grid, sr, sc, target)
        out = path[:budget]
        while len(out) < budget:
            out.append(STAY)
        return out

    def _bfs(self, grid, sr, sc):
        """返回 prev 字典，用于找路与找目标。可走：空地/金币/迷雾，避开障碍与炸弹。"""
        prev = {(sr, sc): None}
        queue = [(sr, sc)]
        head = 0
        while head < len(queue):
            r, c = queue[head]
            head += 1
            for a, nr, nc in self.neighbors[r * GRID + c]:
                if (nr, nc) in prev:
                    continue
                v = grid[nr][nc]
                if v == OBSTACLE or v == BOMB:
                    continue
                prev[(nr, nc)] = (r, c, a)
                queue.append((nr, nc))
        return prev, queue

    def _nearest_gold(self, grid, sr, sc, claimed):
        prev, order = self._bfs(grid, sr, sc)
        for (r, c) in order:
            if (r, c) == (sr, sc):
                continue
            if (r, c) in claimed:
                continue
            if grid[r][c] >= 1:
                return (r, c)
        return None

    def _path_to(self, grid, sr, sc, target):
        prev, _ = self._bfs(grid, sr, sc)
        if target not in prev:
            return []
        acts = []
        cur = target
        while cur != (sr, sc):
            node = prev[cur]
            if node is None:
                break
            pr, pc, a = node
            acts.append(a)
            cur = (pr, pc)
        acts.reverse()
        return acts

    # ---------- 输出保险 ----------

    @staticmethod
    def _sanitize(out):
        try:
            vals = [int(x) for x in out]
        except Exception:
            return list(SAFE)

        vals = (vals + list(SAFE))[:9]
        for i in range(S):
            if not 0 <= vals[i] <= 4:
                vals[i] = STAY
        if not 0 <= vals[6] <= S:
            vals[6] = 3
        if vals[7] not in (0, 1):
            vals[7] = 0
        if vals[8] not in (0, 1, 2):
            vals[8] = 0
        return vals
