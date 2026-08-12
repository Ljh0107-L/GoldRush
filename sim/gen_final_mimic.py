"""Generate final-mimic map family: response surface over
   wall_count × morphology × isolated_single_cell_ratio.
   Output: JSON array of map entries compatible with sim/maps_unknown.json schema.
   Each entry has name, rows (17x17 chars '0'=open '1'=wall), counts.wall.
   Named final_mimic_<tag>.
"""
import json, random, os

GRID = 17
C = 8

def ring_cells(d):
    cells = []
    r0, c0 = C-d, C-d; r1, c1 = C+d, C+d
    for c in range(c0, c1+1): cells.append((r0, c))
    for r in range(r0+1, r1+1): cells.append((r, c1))
    for c in range(c1-1, c0-1, -1): cells.append((r1, c))
    for r in range(r1-1, r0, -1): cells.append((r, c0))
    return cells

def place_segments(perim, sl, gl, off=0):
    n = len(perim); w = set(); i = off % n
    while i < n:
        for j in range(sl): w.add(perim[(i+j) % n])
        i += sl + gl
    return w

def sanitize(walls):
    """Keep center 3x3 open, corners open, cap segments to <=4."""
    walls = set(walls)
    for r in range(7, 10):
        for c in range(7, 10):
            walls.discard((r, c))
    for cell in [(0,0),(0,16),(16,0),(16,16)]:
        walls.discard(cell)
    g = [[0]*GRID for _ in range(GRID)]
    for (r,c) in walls: g[r][c] = 1
    # break horizontal runs >4
    for r in range(GRID):
        run=0; start=0
        for c in range(GRID):
            if g[r][c]:
                if run==0: start=c
                run+=1
            else:
                if run>4:
                    for k in range(start+4, start+run, 5): g[r][k]=0
                run=0
        if run>4:
            for k in range(start+4, start+run, 5): g[r][k]=0
    # break vertical runs >4
    for c in range(GRID):
        run=0; start=0
        for r in range(GRID):
            if g[r][c]:
                if run==0: start=r
                run+=1
            else:
                if run>4:
                    for k in range(start+4, start+run, 5): g[k][c]=0
                run=0
        if run>4:
            for k in range(start+4, start+run, 5): g[k][c]=0
    return {(r,c) for r in range(GRID) for c in range(GRID) if g[r][c]}

def count_isolated(walls):
    """Count walls with no wall neighbour (4-connectivity)."""
    wset = set(walls)
    iso = 0
    for (r,c) in wset:
        neigh = [(r-1,c),(r+1,c),(r,c-1),(r,c+1)]
        if not any(n in wset for n in neigh):
            iso += 1
    return iso

def to_rows(walls):
    g = [['0']*GRID for _ in range(GRID)]
    for (r,c) in walls: g[r][c] = '1'
    return [''.join(row) for row in g]

def gen_ring(target_walls, iso_target, seed):
    """Concentric-rectangle ring fragments."""
    random.seed(seed)
    walls = set()
    # rings 1..7
    for d in range(1, 8):
        sl = random.choice([2,3,3,4])
        gl = random.choice([4,5,5,6,7])
        off = random.randint(0, 8*d)
        walls |= place_segments(ring_cells(d), sl, gl, off)
    walls = sanitize(walls)
    # add outer ring (row 0,16 / col 0,16) short segments
    outer = [(r,c) for r in (0,16) for c in range(1,16)] + [(r,c) for c in (0,16) for r in range(1,16)]
    random.shuffle(outer)
    n_outer = max(4, target_walls // 10)
    for cell in outer[:n_outer]:
        walls.add(cell)
    walls = sanitize(walls)
    walls = tune_count_and_iso(walls, target_walls, iso_target, seed)
    return walls

def gen_cluster(target_walls, iso_target, seed):
    """Scattered short clusters (2-4 cells)."""
    random.seed(seed)
    walls = set()
    # build clusters of length 2-4
    while len(walls) < target_walls * 0.85:
        r = random.randint(1, 15); c = random.randint(1, 15)
        horiz = random.random() < 0.5
        length = random.randint(2, 4)
        for k in range(length):
            rr, cc = (r, min(16, c+k)) if horiz else (min(16, r+k), c)
            if not (7 <= rr <= 9 and 7 <= cc <= 9):
                walls.add((rr, cc))
    walls = sanitize(walls)
    walls = tune_count_and_iso(walls, target_walls, iso_target, seed)
    return walls

def gen_mixed(target_walls, iso_target, seed):
    """Ring fragments + scattered clusters."""
    random.seed(seed)
    walls = set()
    for d in (2, 4, 6):
        sl = random.choice([2,3,4]); gl = random.choice([5,6,7])
        off = random.randint(0, 8*d)
        walls |= place_segments(ring_cells(d), sl, gl, off)
    while len(walls) < target_walls * 0.7:
        r = random.randint(1,15); c = random.randint(1,15)
        horiz = random.random() < 0.5
        length = random.randint(1, 4)
        for k in range(length):
            rr, cc = (r, min(16, c+k)) if horiz else (min(16, r+k), c)
            if not (7 <= rr <= 9 and 7 <= cc <= 9):
                walls.add((rr, cc))
    walls = sanitize(walls)
    walls = tune_count_and_iso(walls, target_walls, iso_target, seed)
    return walls

def tune_count_and_iso(walls, target_walls, iso_target, seed):
    """Adjust wall count to target_walls (within ±3) and isolated ratio to iso_target band.
    iso_target: 'low' (<10% isolated) or 'high' (>30% isolated)."""
    random.seed(seed + 9999)
    walls = set(walls)
    # count target
    lo, hi = target_walls - 3, target_walls + 3
    for _ in range(2000):
        n = len(walls)
        if n < lo:
            # add a random open cell (not center, not corner)
            cand = [(r,c) for r in range(1,16) for c in range(1,16)
                    if (r,c) not in walls and not (7<=r<=9 and 7<=c<=9)]
            if not cand: break
            walls.add(random.choice(cand))
        elif n > hi:
            walls.remove(random.choice(list(walls)))
        else:
            break
    walls = sanitize(walls)
    # tune isolated ratio
    for _ in range(3000):
        iso = count_isolated(walls)
        ratio = iso / max(1, len(walls))
        if iso_target == 'low' and ratio < 0.10:
            break
        if iso_target == 'high' and ratio > 0.30:
            break
        if iso_target == 'low':
            # reduce isolated: find an isolated wall, add a neighbour
            isolated = [w for w in walls if not any(n in walls for n in [(w[0]-1,w[1]),(w[0]+1,w[1]),(w[0],w[1]-1),(w[0],w[1]+1)])]
            if not isolated: break
            w = random.choice(isolated)
            neigh = [(w[0]-1,w[1]),(w[0]+1,w[1]),(w[0],w[1]-1),(w[0],w[1]+1)]
            valid = [n for n in neigh if 0<=n[0]<17 and 0<=n[1]<17 and n not in walls
                     and not (7<=n[0]<=9 and 7<=n[1]<=9) and n not in [(0,0),(0,16),(16,0),(16,16)]]
            if valid: walls.add(random.choice(valid))
            else: walls.remove(w)
        else:  # high
            # increase isolated: remove a neighbour of a non-isolated wall
            noniso = [w for w in walls if any(n in walls for n in [(w[0]-1,w[1]),(w[0]+1,w[1]),(w[0],w[1]-1),(w[0],w[1]+1)])]
            if not noniso: break
            w = random.choice(noniso)
            neigh = [(w[0]-1,w[1]),(w[0]+1,w[1]),(w[0],w[1]-1),(w[0],w[1]+1)]
            nb = [n for n in neigh if n in walls]
            if nb: walls.remove(random.choice(nb))
    walls = sanitize(walls)
    return walls

WALL_COUNTS = [50, 70, 90, 110]
MORPHS = {'ring': gen_ring, 'cluster': gen_cluster, 'mixed': gen_mixed}
ISO_LEVELS = ['low', 'high']

entries = {}
seed_base = 20260812
for wc in WALL_COUNTS:
    for mname, mfn in MORPHS.items():
        for iso in ISO_LEVELS:
            name = f"final_mimic_{mname}_w{wc}_{iso}"
            seed = seed_base + wc * 100 + hash((mname, iso)) % 1000
            walls = mfn(wc, iso, seed)
            walls = sanitize(walls)
            nwall = len(walls)
            iso_n = count_isolated(walls)
            iso_ratio = iso_n / max(1, nwall)
            # verify
            assert all(0<=r<17 and 0<=c<17 for (r,c) in walls)
            assert (8,8) not in walls
            rows = to_rows(walls)
            assert len(rows) == 17 and all(len(r)==17 for r in rows)
            entries[name] = {
                "rows": rows,
                "counts": {"wall": nwall, "isolated": iso_n, "iso_ratio": round(iso_ratio, 3)},
                "morph": mname,
                "wall_target": wc,
                "iso_target": iso,
                "source": {"generator": "gen_final_mimic.py", "kind": "synthetic_mimic",
                           "note": "structural mimic of 2025 final maps; NOT the originals"},
            }
            print(f"{name}: walls={nwall} isolated={iso_n} ratio={iso_ratio:.2%}")

out = {
    "schema_version": 1, "grid_size": 17,
    "cell_codes": {"0":"open","1":"wall","2":"bomb_candidate"},
    "purpose": "structural-mimic family of 2025 final maps for trigger-rate response surface",
    "maps": entries,
}
os.makedirs("/Users/bytedance/GoldRush/sim", exist_ok=True)
with open("/Users/bytedance/GoldRush/sim/maps_final_mimic.json", "w") as f:
    json.dump(out, f, indent=1)
print(f"\nWrote {len(entries)} maps to sim/maps_final_mimic.json")
