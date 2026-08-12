"""Generate symmetric final-mimic map family via paired mirror edits from official maps.

Symmetry is preserved by construction: each edit flips a cell AND its mirror image(s).
For both-axis symmetric maps: one edit flips up to 4 cells (1 on each quadrant copy).
For single-axis: up to 2 cells. Cells on the mirror axis flip themselves.

Protected cells (never edited): the 4 player-spawn corners and NPC spawn at (8,8).
"""
import json, random

GRID = 17
C = 8
PROTECTED = {(0,0),(0,16),(16,0),(16,16),(8,8)}

def load_official_walls(name):
    d = json.load(open("sim/maps.json"))
    rows = d["maps"][name]["rows"]
    return [['1' if ch == '1' else '0' for ch in r] for r in rows]

def count_walls(rows):
    return sum(1 for r in rows for c in r if c == '1')

def wall_mod4_check(rows):
    """For both-axis symmetric maps: walls = 4a + 2b + c (axis pairs + center)."""
    center = rows[8][8] == '1'
    axis = 0
    for i in range(17):
        if i == 8: continue
        if rows[8][i] == '1': axis += 1
        if rows[i][8] == '1': axis += 1
    axis_pairs = axis // 2
    off = count_walls(rows) - (axis + (1 if center else 0))
    return center, axis_pairs, off

def paired_edit(rows, n_edits, seed, sym="both"):
    rng = random.Random(seed)
    new = [list(r) for r in rows]
    edits_done = 0
    attempts = 0
    while edits_done < n_edits and attempts < n_edits * 50:
        attempts += 1
        if sym == "both":
            r = rng.randint(0, C); c = rng.randint(0, C)
            cells = {(r,c),(r,16-c),(16-r,c),(16-r,16-c)}
        elif sym == "h":
            r = rng.randint(0, 16); c = rng.randint(0, C)
            cells = {(r,c),(r,16-c)}
        else:
            r = rng.randint(0, C); c = rng.randint(0, 16)
            cells = {(r,c),(16-r,c)}
        if cells & PROTECTED: continue
        for (rr,cc) in cells:
            new[rr][cc] = '1' if new[rr][cc] != '1' else '0'
        edits_done += 1
    return [''.join(r) for r in new]

def is_h_sym(rows): return all(r == r[::-1] for r in rows)
def is_v_sym(rows): return all(rows[i] == rows[16-i] for i in range(GRID))

def count_isolated(walls_set):
    return sum(1 for (r,c) in walls_set if not any(n in walls_set for n in [(r-1,c),(r+1,c),(r,c-1),(r,c+1)]))

DOSES = [4, 16, 64, 160]
entries = {}

# A batch: both-axis symmetric, dose-response from each official map
for base in ("map1","map2","map3"):
    base_rows = load_official_walls(base)
    for dose in DOSES:
        for rep in range(2):
            name = f"final_A_{base}_d{dose}_r{rep}"
            seed = 20260812 + hash((base, dose, rep)) % 100000
            rows = paired_edit(base_rows, dose, seed, sym="both")
            assert is_h_sym(rows) and is_v_sym(rows)
            w = count_walls(rows)
            center, axis_pairs, off = wall_mod4_check(rows)
            assert off % 4 == 0  # mod-4 structural invariant for both-sym maps
            for (r,c) in PROTECTED:
                assert rows[r][c] == '0'
            walls_set = {(r,c) for r in range(17) for c in range(17) if rows[r][c]=='1'}
            iso = count_isolated(walls_set)
            entries[name] = {
                "rows": rows,
                "counts": {"wall": w, "isolated": iso, "iso_ratio": round(iso/max(1,w), 3)},
                "symmetry": "both",
                "base_map": base,
                "edit_dose": dose,
                "anchor_6_8_wall": rows[6][8] == '1',
                "anchor_11_8_wall": rows[11][8] == '1',
                "source": {"generator": "gen_final_sym.py", "method": "paired_mirror_edit",
                           "base": base, "dose": dose},
            }

# B batch: single-axis symmetric (h or v)
for base in ("map1","map2","map3"):
    base_rows = load_official_walls(base)
    for sym in ("h", "v"):
        for dose in [16, 64]:
            for rep in range(2):
                name = f"final_B_{base}_{sym}_d{dose}_r{rep}"
                seed = 20260813 + hash((base, sym, dose, rep)) % 100000
                rows = paired_edit(base_rows, dose, seed, sym=sym)
                if sym == "h": assert is_h_sym(rows)
                else: assert is_v_sym(rows)
                w = count_walls(rows)
                for (r,c) in PROTECTED:
                    assert rows[r][c] == '0'
                walls_set = {(r,c) for r in range(17) for c in range(17) if rows[r][c]=='1'}
                iso = count_isolated(walls_set)
                entries[name] = {
                    "rows": rows,
                    "counts": {"wall": w, "isolated": iso, "iso_ratio": round(iso/max(1,w), 3)},
                    "symmetry": sym,
                    "base_map": base,
                    "edit_dose": dose,
                    "anchor_6_8_wall": rows[6][8] == '1',
                    "anchor_11_8_wall": rows[11][8] == '1',
                    "source": {"generator": "gen_final_sym.py", "method": "paired_mirror_edit",
                               "base": base, "dose": dose, "sym": sym},
                }

out = {"schema_version": 1, "grid_size": 17,
       "cell_codes": {"0":"open","1":"wall","2":"bomb_candidate"},
       "purpose": "symmetric structural-mimic family of 2025 final maps via paired mirror edits",
       "maps": entries}
with open("sim/maps_final_sym.json", "w") as f:
    json.dump(out, f, indent=1)

n = len(entries)
a6 = sum(1 for e in entries.values() if e["anchor_6_8_wall"])
a11 = sum(1 for e in entries.values() if e["anchor_11_8_wall"])
eith = sum(1 for e in entries.values() if e["anchor_6_8_wall"] or e["anchor_11_8_wall"])
ws = sorted(e['counts']['wall'] for e in entries.values())
print(f"Total maps: {n}")
print(f"anchor (6,8) on wall: {a6}/{n}")
print(f"anchor (11,8) on wall: {a11}/{n}")
print(f"either anchor on wall: {eith}/{n} = {100*eith/n:.0f}%")
print(f"wall range: {min(ws)}-{max(ws)}")
print(f"symmetry + mod-4 invariant + protected-cells verified: True")
