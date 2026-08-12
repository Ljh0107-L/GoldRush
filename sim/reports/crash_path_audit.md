# 落库版崩溃路径防御性审计

> **对象**: 落库版 `src/player.cpp`(整文件 sha `06477c516b7214b4`)
> **目的**: 查是否存在**输入相关**的崩溃路径(初赛是 2–5 张从未见过的地图 ⇒ 前所未见的输入)
> **方法**: 逐处列出所有下标来自 `GameInput` 的数组访问,证有界性
> **纪律**: 只审计不改 `src/`;只报「哪里可能崩、依据是什么、上界是什么」

---

## 0. 落库版实际读取的 `GameInput` 字段

落库构型(`PV_ACTOR=1`,无 snapshot 读取)只读以下字段:

| 字段 | 用途 | 是否作数组下标 |
|---|---|---|
| `in->round` | 模式退场判断、`& 3` | 否(只比较/按位与) |
| `in->my_units[0/1].row` | 行索引 `sr` | **是** |
| `in->my_units[0/1].col` | 列索引 `sc` | **是** |
| `in->my_units_gold[0/1]` | 富度门、order 判断 | 否(只比较) |
| `in->grid[r][c]` | 读地形/金币 | 是,但 r/c 受控(见下) |

**不读**:`visible_enemies`、`visible_npcs`、`num_visible_npcs`、`snapshot.regions[]`。
⇒ 这些字段即使平台送来异常值,也不会被消费。

---

## 1. 逐处清单:下标来自 `GameInput` 的数组访问

### 1.1 单位坐标 `sr` / `sc`(`in->my_units[u].row/col`)

这是**唯一**的输入派生无界下标。以下访问均假设 `sr, sc ∈ [0, 16]`:

| 位置 | 访问 | 数组大小 | 有界性依据 |
|---|---|---|---|
| L406,424,551 | `g_s.visited[sr]` | `visited[17]` | ⚠️ **无显式 clamp**,依赖 ABI 保证 sr∈[0,16] |
| L573 | `PT.rcl[sr + i]`, i∈[0,4] | `rcl[21]` | 若 sr∈[0,16] 则 sr+i∈[0,20] ✅;否则 OOB |
| L656 | `PT.rowok[sr]` | `rowok[17]` | ⚠️ 依赖 sr∈[0,16] |
| L656 | `PT.colok[sc]` | `colok[17]` | ⚠️ 依赖 sc∈[0,16] |
| L567 | `SCT.cb[sc]`, `SCT.lsh[sc]` | `cb[17]`, `lsh[17]` | ⚠️ 依赖 sc∈[0,16] |
| L698 | `PT.sgi[anch_r[u] - sr + 20]` | `sgi[41]` | anch_r∈[0,16],若 sr∈[0,16] 则索引∈[4,36] ✅ |
| L706 | `PT.sgi[sr - last_r[u] + 20]` | `sgi[41]` | 同上 |
| L406,424 | `g_s.visited[sr] >> (sc+1)`, `1u << (sc+1)` | shift 量 | 若 sc∈[0,16] 则 sc+1∈[1,17] ✅(uint32_t);若 sc≥31 或 sc<0 则 UB |
| L489(slowMove) | `vis[sr] \|= 1u << sc` | `vis[17]`, shift | 同上 |

**结论**:`sr`/`sc` 的有界性**完全依赖平台 ABI 保证**(单位坐标必须是 0–16 的有效格点)。
- 若平台始终发合法坐标 ⇒ **全部有界,无崩溃路径**。
- 若平台发越界坐标 ⇒ 上述 6 个数组 OOB 读/写,可能 segfault 或静默破坏 `g_s`。

### 1.2 `in->grid[r][c]` 访问

| 位置 | r/c 来源 | 有界性 |
|---|---|---|
| L410-422 (slowTick) | `r∈[r0,r1]`, `c∈[c0,c1]`,而 `r0=max(0,sr-rad)`, `r1=min(16,sr+rad)` | ✅ **显式 clamp 到 [0,16]** |
| L574 (decide AVX) | `cr = PT.rcl[sr+i]`,而 `rcl[x]=clamp(x-2,0,16)` | ✅ **cr 被 rcl 钳到 [0,16]** |
| L644-646 (bomb 回读) | `rr = sr-3+(b>>3)`, `cc = sc-2+(b&7)`,然后 `if ((unsigned)rr<(unsigned)N && (unsigned)cc<(unsigned)N)` | ✅ **访问前有显式范围检查** |

⇒ **grid 访问全部安全**,即使 sr/sc 越界,slowTick 的 r/c 仍被钳住;decide 的 cr 被 rcl 钳住。

### 1.3 其他数组(下标非输入派生)

| 访问 | 下标来源 | 有界性 |
|---|---|---|
| `g_s.bpw[r+1]`, `g_s.seen[r]` | r 是 slowTick 循环变量 ∈[0,16] | ✅ bpw[19], seen[17] |
| `BAKED_W[m][r]` | m∈{0,1,2}(候选位图), r∈[0,16] | ✅ |
| `PT.cell[p]` | p=ctzll(cand),cand≠0(=1 兜底),p∈[0,47] | ✅ cell[48] |
| `PT.thru[b]` | b=ctzll(m\|(1<<WSENT)),WSENT=45,b∈[0,45] | ✅ thru[46] |
| `PT.rclr[i][p]` | i∈[0,4], p=(bd>>..)&31 | ✅ rclr[5][32] |
| `PT.towR[g]`, `towC[g]` | g=sgi[...]∈{0,1,2} | ✅ towR[3] |
| `out.actions+u*3` | u∈{0,1} | ✅ actions[6] |
| `g_s.anch_r[u]`, `last_r[u]` | u∈{0,1} | ✅ [2] |
| `par[nr*N+nc]`, `q[tail++]` | BFS 邻居,nr/nc∈[0,16];tail≤289 | ✅ par[289],q[289] |

---

## 2. 除法 / 取模

| 位置 | 除数 | 有界性 |
|---|---|---|
| L492 `cur / N`, `cur % N` | N=17(常量) | ✅ 非零 |
| L507-512 `n % 9`, `(n-1)%9` 等 | 9(常量) | ✅ 非零;n≥1(BFS 至少走一步,start==goal 已提前 return) |

⇒ **无除零风险**。

---

## 3. 循环终止

| 循环 | 终止条件 | 有界性 |
|---|---|---|
| slowTick 双循环 | r∈[r0,r1], c∈[c0,c1] | ✅ 有限区间 |
| `slowMove` BFS `while(head<tail && q[head]!=goal)` | `vis` 保证每格最多入队一次 ⇒ tail≤289;goal 不可达时 `head>=tail` 退出(L502) | ✅ 不会死循环 |
| `decide` 贪心 `for(it=0; it<PV_GN; it++)` | PV_GN=3 常量 | ✅ |
| `while(m)` popcount 循环 | 每次清掉一位,最多 64 次 | ✅ |

---

## 4. 栈用量

| 函数 | 局部大数组 | 大小 |
|---|---|---|
| `slowMove` | `par[289]` (289B) + `q[289]` (578B) + `vis[17]` (68B) + `seq[9]` (36B) | **≈ 971 B** |
| `decide` | 无大局部数组(PT/SCT 是 constexpr 全局) | ≈ 几十 B |

调用链:`moveDecision → decide → slowTick → fixAnchor` / `→ slowMove`。**无递归**。
⇒ 总栈深 < 1KB,远低于典型 8MB 栈。**无栈溢出风险**。

---

## 5. 未初始化读

| 变量 | 是否初始化 | 安全依据 |
|---|---|---|
| `par[289]`, `q[289]` (slowMove) | ❌ 故意不初始化 | `par[cur]` 只在回溯时读,而 `cur=goal` 必须曾被 `vis` 标记入队 ⇒ `par[cur]` 已写;`q[head]` 只在 `head<tail` 时读 ⇒ 已写 |
| `vis[17]` | ✅ `= {}` 清零 | |
| `GameOutput out` | ❌ 未初始化 | L555 注释「全字段必写」:k/order/vp/actions 全在返回前赋值;`SAFE_OUT` 兜底 |

⇒ **无未初始化读**。

---

## 6. `ctzll(0)` 防护

`__builtin_ctzll(0)` 是 UB。落库版两处:
- L687 `int b = __builtin_ctzll(m | (1ULL << WSENT))` — WSENT=45 保证非零。✅
- L718 `int p = __builtin_ctzll(cand)` — L673 `if(cand==0) cand=1ULL` 兜底。✅

---

## 7. 平凡解撞击

**方法**:对每处下标访问,问「若下标越界,会崩吗?」。

- **已知安全段**(slowTick 的 grid 访问):r/c 显式 clamp ⇒ 我正确判为安全。✅
- **故意写坏段**(假设去掉 slowTick 的 r0/r1 clamp):r,c 可越界 ⇒ grid 访问 OOB ⇒ 我的方法会标记为「下标来自循环变量但未 clamp」。✅
- **未使用的输入字段**(snapshot.regions[]):落库版不读 ⇒ 即使内容异常也无影响。✅

方法判别力合格。

---

## 8. 裁决

### 唯一的输入相关崩溃风险:单位坐标 `sr`/`sc` 越出 [0,16]

**依据**:6 处数组访问(`visited`、`rowok`、`colok`、`cb`/`lsh`、`rcl`、`sgi`)和 2 处位移量(`sc+1`)直接以 `sr`/`sc` 为下标或移位量,**全程无显式范围检查**。

**上界**:
- 若平台遵守 ABI(单位坐标必为 0–16 有效格点)⇒ **零崩溃路径**,所有访问有界。
- 若平台发越界值 ⇒ OOB 读(可能 segfault)或写 `g_s.visited`(可能破坏状态),行为未定义。

**对 235745 崩溃的含义**:
- 该崩溃无签名(末轮 cost 正常、无攀升),不像越界写导致的状态破坏(那会引发后续行为异常)。
- 若 235745 是坐标越界引起,应在日志中看到坐标异常;但平台侧不一定记录。
- **n=1,无法归因**。

### 其余全部有界

grid 访问、除法、循环、栈、未初始化读、ctzll —— 均已证有界或已防护。

---

## 9. 射程与局限

1. **本审计只覆盖落库构型**(PV_ACTOR=1, 无 snapshot)。若未来打开 `PV_ACTOR>1` 或读 snapshot,需重新审计 `visible_enemies[]`、`snapshot.regions[]` 的下标。
2. **坐标有界性依赖平台 ABI**。若平台行为未文档化,可在入口加一行 `sr = clamp(sr,0,16); sc=clamp(sc,0,16);` 作为防御(但需另行裁定,不在本审计范围)。
3. **未覆盖**:浮点异常(本代码无浮点)、信号(SIGSEGV 不被 try/catch 捕获)、内存分配(无 malloc)。
