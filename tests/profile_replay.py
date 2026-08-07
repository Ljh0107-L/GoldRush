"""profile_replay.py — 用真实对局输入驱动 -DPROFILE 构建的 .so, 输出
"分支频率 x 耗时"矩阵(逐轮周期数 + 组件位掩码)。

用法(x86 开发机上跑, rdtsc 周期; mac 上跑则为纳秒):
  SO=src/prof.so python3 tests/profile_replay.py logs/game_140521.log [...]

位掩码: 1=DFS 2=singleGold 4=steer缓存命中 8=计划命中 16=pickGold扫描
        32=globalBFS 64=explore 128=本地收益 256/512/1024=wg0/1/2
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay as rp

BITS = [(1, 'DFS'), (2, 'single'), (4, 'steer'), (8, 'plan'),
        (16, 'pick'), (32, 'gBFS'), (64, 'explore'), (128, 'gain'),
        (256, 'wg0'), (512, 'wg1'), (1024, 'wg2')]


def profile(log_path, so_path):
    so = rp.load_so(so_path)
    so.profRounds.restype = ctypes.c_int
    so.profCyc.restype = ctypes.c_ulonglong
    so.profCyc.argtypes = [ctypes.c_int]
    so.profFlags.restype = ctypes.c_int
    so.profFlags.argtypes = [ctypes.c_int]
    so.profReset()

    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        rp.replay(log_path, so_path)   # 同一 .so 已加载, dlopen 复用同实例, 计数器共享

    n = so.profRounds()
    rows = [(so.profCyc(i), so.profFlags(i)) for i in range(n)]
    return rows


def report(rows, name):
    cycs = sorted(c for c, f in rows)
    n = len(rows)
    med = cycs[n // 2]
    p90 = cycs[int(n * 0.9)]
    print(f"\n===== {name}  ({n} 轮) =====")
    print(f"整体: P50 {med}  P90 {p90}  P99 {cycs[int(n*0.99)]}")
    print(f"{'组件':10s} {'频率':>6s} {'有它P50':>9s} {'无它P50':>9s}")
    for bit, label in BITS:
        with_ = sorted(c for c, f in rows if f & bit)
        without = sorted(c for c, f in rows if not f & bit)
        if not with_:
            continue
        wm = with_[len(with_) // 2]
        om = without[len(without) // 2] if without else 0
        print(f"{label:10s} {len(with_)*100//n:5d}% {wm:9d} {om:9d}")
    # 常见组合模式
    from collections import Counter
    pat = Counter()
    for c, f in rows:
        pat[f] += 1
    print("最常见的 6 种组合:")
    for f, cnt in pat.most_common(6):
        labels = '+'.join(l for b, l in BITS if f & b) or '(空)'
        sub = sorted(c for c2, ff in rows if ff == f for c in [c2])
        print(f"  {cnt*100//n:3d}%  P50={sub[len(sub)//2]:8d}  {labels}")


if __name__ == '__main__':
    so_path = os.environ.get('SO', 'src/prof.so')
    for log in sys.argv[1:]:
        rows = profile(log, so_path)
        report(rows, os.path.basename(log))
