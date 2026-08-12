"""Combined: capture firing flags AND p1 gold in one pass per (map, seed)."""
import json, os, sys, statistics as st, math
ROOT = "/home/Ubiquant220/gr_xmap"
sys.path.insert(0, ROOT); os.chdir(ROOT)
from sim.runner import run_game
import sim.runner as R
from sim.scenario import MapDefinition

SO = ROOT + "/so/landed.so"
NSEEDS = 12
SEEDS = [str(i) for i in range(NSEEDS)]

def load_map(name):
    if name in ("map1","map2","map3"):
        return MapDefinition.by_name(name)
    return MapDefinition.from_json_file(ROOT+"/maps_final_mimic.json", map_name=name)

def run_one(m, seed):
    stood = set()
    fired_per_round = []
    gold_p1_end = 0
    orig = R.round_log_record
    def w(res, *a, **k):
        fired = False
        try:
            for u in res.start.state.player(1).units:
                c = u.position.cell
                if c not in stood:
                    stood.add(c); fired = True
        except Exception:
            pass
        fired_per_round.append(fired)
        try:
            nonlocal gold_p1_end
            gold_p1_end = res.start.state.player(1).gold
        except Exception:
            pass
        return orig(res, *a, **k)
    R.round_log_record = w
    try:
        run_game(SO, "stay", map_source=m, seed=seed, dispatch="fixed", fixed_costs=(200, 100000))
    finally:
        R.round_log_record = orig
    # firing rate r>=100
    late = fired_per_round[100:]
    fr = 100.0 * sum(late) / len(late) if late else 0.0
    return fr, gold_p1_end

def anchor_on_wall(m):
    return (6,8) in m.walls, (11,8) in m.walls

names_official = ["map1","map2","map3"]
maps_fm = json.load(open(ROOT+"/maps_final_mimic.json"))["maps"]
names = list(maps_fm.keys()) + names_official

results = {}
for idx, name in enumerate(names):
    m = load_map(name)
    frs = []; golds = []
    for seed in SEEDS:
        fr, g = run_one(m, seed)
        frs.append(fr); golds.append(g)
    a6, a11 = anchor_on_wall(m)
    iso = maps_fm.get(name, {}).get("counts", {}).get("iso_ratio", None)
    walls = len(m.walls)
    mean_fr = st.mean(frs)
    sem_fr = st.stdev(frs)/math.sqrt(len(frs)) if len(frs)>1 else 0.0
    mean_g = st.mean(golds)
    min_g = min(golds); max_g = max(golds)
    cliff = "ABOVE" if mean_fr > 10.0 else "below"
    results[name] = {
        "walls": walls, "iso_ratio": iso,
        "fire_rate_mean": mean_fr, "fire_rate_sem": sem_fr,
        "fire_rate_min": min(frs), "fire_rate_max": max(frs),
        "cliff_side": cliff,
        "gold_p1_mean": mean_g, "gold_p1_min": min_g, "gold_p1_max": max_g,
        "anchor_6_8_wall": a6, "anchor_11_8_wall": a11,
    }
    print(f"[{idx+1}/{len(names)}] {name:<28} walls={walls:>4} fr={mean_fr:6.2f}% cliff={cliff:>5} gold={mean_g:.0f} a6={a6} a11={a11}", flush=True)

json.dump(results, open("/tmp/final_mimic_results.json","w"), indent=1, default=str)
print("\n=== DONE ===", flush=True)
print(f"Wrote /tmp/final_mimic_results.json ({len(results)} maps)", flush=True)
