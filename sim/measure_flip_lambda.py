"""lambda done right: the flip condition depends on THEIR cost distribution, which is a
property of them and 100% recorded, and on OUR cost, which we know for the current
construct (P50 ~204ns).  So hold their measured per-round costs and substitute our
CURRENT cost -- this is construct-independent and immune to the stale-slot contamination
that makes the naive version read lambda=0.
"""
import json,glob,collections,statistics as st,re
OUR_NOW=204          # current construct P50, measured
per=collections.defaultdict(list)
for f in sorted(glob.glob("logs/game_*.log")):
    try:
        L=open(f).read().splitlines()
        if len(L)<10: continue
        hd=json.loads(L[0]); n1,n2=hd.get("player1",""),hd.get("player2","")
        seat=0 if n1=="player220" else (1 if n2=="player220" else None)
        if seat is None: continue
        opp=n2 if seat==0 else n1
        for x in L[2:]:
            if not x.strip(): continue
            r=json.loads(x)
            if r.get("round",0)<4: continue
            ps=(r.get("end") or {}).get("players")
            if not ps or len(ps)<2: continue
            c=ps[1-seat].get("cost")
            if c is not None: per[opp].append(c)
    except Exception: pass

# exclude our own experimental constructs (broadened: they carry version-ish names)
SELF=re.compile(r'^(v\d+|c\d+|probeobs|champff4|mimic|frTu|t1f|cpp\d|g47v|player220|rem\d|co1m|I08|E88|S40|B40|H3B|SFP|SPC|FUS|BLK|QA$|b00|capgr|terra|task|grv|gp\d|a1?$|n$|test$|unified$|scout$|vv$|playerv|pv5)',re.I)
real={k:v for k,v in per.items() if len(v)>=400 and not SELF.match(k)}
DS=(9,15,20,30)
print(f"opponents with >=400 clean rounds, after excluding our own constructs: {len(real)}\n")
print(f"{'opponent':18s} {'rnds':>6s} {'theirP50':>9s} {'theirP90':>9s} {'f_now':>7s} " + " ".join(f'{"flip@"+str(d):>8s}' for d in DS))
print("-"*82)
lam={d:[] for d in DS}; fnow=[]
for k,v in sorted(real.items(), key=lambda x:st.median(x[1])):
    n=len(v); p50=st.median(v); p90=sorted(v)[int(.9*n)]
    f=sum(1 for c in v if OUR_NOW<c)/n     # we move first when strictly faster (ties->P1)
    fnow.append(f)
    fl={d:sum(1 for c in v if 0 < c-OUR_NOW <= d)/n for d in DS}
    for d in DS: lam[d].append(fl[d])
    print(f"{k[:18]:18s} {n:6d} {p50:9.0f} {p90:9.0f} {f:6.1%} " + " ".join(f"{fl[d]:7.2%}" for d in DS))
print(f"\n=== TEAM-EQUAL-WEIGHTED (round-robin caliber), n={len(fnow)} opponents ===")
print(f"  our current first-mover rate f = mean {st.mean(fnow):.3%}  median {st.median(fnow):.3%}  min {min(fnow):.3%}")
for d in DS:
    a=lam[d]; nz=[x for x in a if x>0]
    print(f"  Delta={d:2d}ns : lambda = {st.mean(a):6.3%}  median {st.median(a):6.3%}  max {max(a):6.3%}  ({len(nz)}/{len(a)} teams affected at all)")
print("\n=== cost of the cursor form under BOTH weightings ===")
DIFF=(4.674-2.834)*2*500   # both-first vs both-second income/unit-round x 2 units x 500 rounds
print(f"  full loss if a round flips: {DIFF:.0f} gold/game (order-fragility anchor 4.674 vs 2.834)")
for d,lbl in ((9,"+62 instr ~ +9ns"),(15,"+15ns"),(30,"+30ns")):
    L=st.mean(lam[d])
    print(f"  {lbl:18s} field-weighted cost = {-L*DIFF:7.1f} gold   (unconditional 1.6/instr would say {-1.6*62 if d==9 else float('nan'):.0f})")
