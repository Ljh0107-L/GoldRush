"""Measure trigger rate, gold generated vs collected, anchors on symmetric family."""
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
    return MapDefinition.from_json_file(ROOT+"/maps_final_sym.json", map_name=name)

class _Accum:
    total = 0

_orig_resolve = None

def run_one(m, seed):
    _Accum.total = 0
    stood = set()
    fired_per_round = []
    final = {}
    orig_rlr = R.round_log_record

    # Patch resolve_round to accumulate generated gold
    from sim import scenario as S
    orig_resolve = S.ScenarioGenerator.resolve_round
    def _patched(self, round_number, state=None):
        ev = orig_resolve(self, round_number, state)
        try:
            _Accum.total += sum(a.value for a in ev.gold_additions)
        except Exception:
            pass
        return ev
    S.ScenarioGenerator.resolve_round = _patched

    def w(res, *a, **k):
        fired = False
        try:
            for u in res.start.state.player(1).units:
                c = (u.position.row, u.position.col)
                if c not in stood:
                    stood.add(c); fired = True
        except Exception:
            pass
        fired_per_round.append(fired)
        try:
            state = res.state
            final["p1"] = state.players[0].gold
            final["p2"] = state.players[1].gold
            final["npc"] = sum(npc.pickup for npc in state.npcs)
            final["ground"] = sum(v for row in state.ground for v in row if v > 0)
        except Exception:
            pass
        return orig_rlr(res, *a, **k)
    R.round_log_record = w
    try:
        run_game(SO, "stay", map_source=m, seed=seed, dispatch="fixed", fixed_costs=(200, 100000))
    finally:
        R.round_log_record = orig_rlr
        S.ScenarioGenerator.resolve_round = orig_resolve

    late = fired_per_round[100:]
    fr = 100.0 * sum(late) / len(late) if late else 0.0
    generated = _Accum.total
    return fr, generated, final

def anchor_on_wall(m):
    return (6,8) in m.walls, (11,8) in m.walls

names_official = ["map1","map2","map3"]
maps_sym = json.load(open(ROOT+"/maps_final_sym.json"))["maps"]
names = list(maps_sym.keys()) + names_official

results = {}
print(f"{'map':<32} {'w':>4} {'fr%':>6} {'side':>5} {'gen':>6} {'p1':>6} {'rate%':>6} {'a6':>5} {'a11':>5}", flush=True)
for idx, name in enumerate(names):
    m = load_map(name)
    frs, gens, p1s, p2s, npcs, grounds = [], [], [], [], [], []
    for seed in SEEDS:
        fr, g, f = run_one(m, seed)
        frs.append(fr); gens.append(g); p1s.append(f.get("p1",0))
        p2s.append(f.get("p2",0)); npcs.append(f.get("npc",0)); grounds.append(f.get("ground",0))
    a6, a11 = anchor_on_wall(m)
    walls = len(m.walls)
    mean_fr = st.mean(frs)
    sem_fr = st.stdev(frs)/math.sqrt(len(frs)) if len(frs)>1 else 0.0
    mean_gen = st.mean(gens); mean_p1 = st.mean(p1s)
    rate = 100.0 * mean_p1 / mean_gen if mean_gen > 0 else 0.0
    cliff = "ABOVE" if mean_fr > 10.0 else "below"
    results[name] = {
        "walls": walls,
        "fire_rate_mean": mean_fr, "fire_rate_sem": sem_fr,
        "cliff_side": cliff,
        "gold_generated_mean": mean_gen,
        "gold_p1_mean": mean_p1, "gold_p1_min": min(p1s), "gold_p1_max": max(p1s),
        "gold_p2_mean": st.mean(p2s),
        "gold_npc_mean": st.mean(npcs),
        "gold_ground_mean": st.mean(grounds),
        "collection_rate_p1": rate,
        "anchor_6_8_wall": a6, "anchor_11_8_wall": a11,
    }
    print(f"{name:<32} {walls:>4} {mean_fr:>6.2f} {cliff:>5} {mean_gen:>6.0f} {mean_p1:>6.0f} {rate:>6.1f} {str(a6):>5} {str(a11):>5}", flush=True)

json.dump(results, open("/tmp/final_sym_results.json","w"), indent=1, default=str)
print(f"\nDONE: {len(results)} maps -> /tmp/final_sym_results.json", flush=True)
