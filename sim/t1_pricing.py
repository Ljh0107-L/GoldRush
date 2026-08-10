"""t1_pricing.py -- gold-to-win-rate bridge vs the benchmark opponent T-1.

Frozen construct ONLY (families t1f1/t1f2/t1f3/t1n2/t1n3), per map, from archived logs.
Map identified by wall-token count on log line 2 (map1=40, map2=24, map3=78); tokens are
STRINGS in the log, not ints.  Margin = end-of-game gold difference from the last round.
Reports the model-free requirement: the shift that moves >=50% of games above zero.
"""
import json,subprocess,statistics as st,collections,sys
WALL={40:'map1',24:'map2',78:'map3'}
FROZ=('t1f1','t1f2','t1f3','t1n1','t1n2','t1n3')
def load(team='Tiuntled-1'):
    paths=subprocess.run(['python3','sim/probe/archive_logs.py','paths','--team',team],
                         capture_output=True,text=True).stdout.split()
    by=collections.defaultdict(list)
    for p in paths:
        try:
            L=open(p).read().splitlines()
            hd=json.loads(L[0]); n1,n2=hd["player1"],hd["player2"]
            g=json.loads(L[1]); g=g['grid'] if isinstance(g,dict) else g
            mp=WALL.get(sum(1 for r in g for c in r if str(c)=="1"))
            if mp is None: continue
            ours,seat=(n1,0) if not n1.startswith("player163") else (n2,1)
            if not ours.startswith(FROZ): continue
            e=json.loads(L[-1])['end']['players']
            by[mp].append((ours,e[seat]['gold']-e[1-seat]['gold']))
        except Exception: pass
    return by
def need50(v):
    k=len(v)
    for d in range(0,900):
        if sum(1 for x in v if x>-d)>=k/2: return d
    return None
if __name__=='__main__':
    by=load()
    print(f"{'map':7s} {'n':>3s} {'mean':>8s} {'median':>8s} {'W/L':>6s} {'50% needs':>10s} {'P(+80)':>7s} {'P(+128)':>8s}")
    print("-"*62)
    allv=[]
    for mp in ('map1','map2','map3'):
        v=[m for _,m in by.get(mp,[])]
        if not v: continue
        allv+=v
        print(f"{mp:7s} {len(v):3d} {st.mean(v):+8.1f} {st.median(v):+8.1f} "
              f"{sum(1 for x in v if x>0):2d}/{sum(1 for x in v if x<=0):<3d} "
              f"{'+'+str(need50(v)):>10s} {sum(1 for x in v if x>-80)/len(v):6.1%} {sum(1 for x in v if x>-128)/len(v):7.1%}")
        print(f"        margins {sorted(v)}")
    print("-"*62)
    print(f"{'POOLED':7s} {len(allv):3d} {st.mean(allv):+8.1f} {st.median(allv):+8.1f} "
          f"{sum(1 for x in allv if x>0):2d}/{sum(1 for x in allv if x<=0):<3d} "
          f"{'+'+str(need50(allv)):>10s} {sum(1 for x in allv if x>-80)/len(allv):6.1%} {sum(1 for x in allv if x>-128)/len(allv):7.1%}")
    print(f"\nmedian is tail-insensitive, mean is not: pooled median {st.median(allv):+.1f} vs mean {st.mean(allv):+.1f}")
