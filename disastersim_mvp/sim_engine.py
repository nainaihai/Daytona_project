"""Deterministic, fictional flood-response routing engine. NOT for real evacuations.

This file must stay dependency-free (stdlib only): sponsors.py ships it as-is
into a Daytona sandbox and runs the same code there.
"""
import heapq
import math

N = 15
BLOCK_RISK = 0.88  # cells at/above this risk are treated as impassable
STARTS = {"駅前 (南中央)": (7, 12), "川沿い住宅街": (7, 6), "商店街": (12, 11)}
SHELTERS = {"北の丘避難所": (2, 2), "東の高台避難所": (12, 2), "西の学校": (3, 12)}
RIVER_Y = 7
BRIDGES_X = (2, 7, 12)  # the river can only be crossed on these columns

# Named areas of the fictional town, as inclusive rectangles (x0, y0, x1, y1).
# The AI interpreter must map free-text locations onto one of these names.
AREAS = {
    "駅前": {"rect": (5, 11, 9, 14), "desc": "南中央の駅周辺",
             "aliases": ["駅前", "駅の前", "駅周辺", "駅"]},
    "西の橋": {"rect": (1, 6, 3, 8), "desc": "川を渡る西側の橋 (x=2)",
               "aliases": ["西の橋", "西橋", "西側の橋"]},
    "中央橋": {"rect": (6, 6, 8, 8), "desc": "川を渡る中央の橋 (x=7)",
               "aliases": ["中央橋", "中央の橋", "真ん中の橋", "橋"]},
    "東の橋": {"rect": (11, 6, 13, 8), "desc": "川を渡る東側の橋 (x=12)",
               "aliases": ["東の橋", "東橋", "東側の橋"]},
    "川沿い": {"rect": (0, 5, 14, 9), "desc": "東西に流れる川の両岸",
               "aliases": ["川沿い", "河川敷", "堤防", "川"]},
    "西側の道": {"rect": (0, 0, 3, 14), "desc": "街の西端を南北に通る道",
                 "aliases": ["西側の道", "西の道", "西側", "西の方"]},
    "東側の道": {"rect": (11, 0, 14, 14), "desc": "街の東端を南北に通る道",
                 "aliases": ["東側の道", "東の道", "東側", "東の方"]},
    "商店街": {"rect": (10, 9, 14, 13), "desc": "南東の商店街",
               "aliases": ["商店街", "お店", "店"]},
    "北部住宅地": {"rect": (0, 0, 14, 4), "desc": "川の北側の住宅地",
                   "aliases": ["北部住宅地", "北部", "北側", "北の住宅"]},
    "北の丘避難所周辺": {"rect": (1, 1, 3, 3), "desc": "北の丘避難所のまわり",
                         "aliases": ["北の丘", "丘"]},
    "東の高台避難所周辺": {"rect": (11, 1, 13, 3), "desc": "東の高台避難所のまわり",
                           "aliases": ["東の高台", "高台"]},
    "西の学校周辺": {"rect": (2, 11, 4, 13), "desc": "西の学校のまわり",
                     "aliases": ["西の学校", "学校"]},
}
HAZARD_TYPES = ["浸水", "土砂災害", "建物倒壊", "火災", "通行止め", "安全情報", "その他"]


def risk_grid(rain, open_water=True):
    """Create hypothetical flood hazard values on a 0..1 scale.

    open_water=False drops the impassable river line (used when routing on real
    roads, whose real bridges don't line up with the fictional ones); the river
    then only contributes its high-risk band.
    """
    if not 1 <= rain <= 5:
        raise ValueError("rain must be 1..5")
    grid = []
    for y in range(N):
        row = []
        for x in range(N):
            river = 0.54 * math.exp(-abs(y - RIVER_Y) / 1.65)
            if y == RIVER_Y and x in BRIDGES_X:
                river *= 0.6  # bridge deck sits above the water
            basin_east = 0.38 * math.exp(-math.hypot(x - 11, y - 11) / 2.3)
            basin_west = 0.35 * math.exp(-math.hypot(x - 4, y - 11) / 2.1)
            risk = (river + basin_east + basin_west) * rain / 3.0
            if open_water and y == RIVER_Y and x not in BRIDGES_X:
                risk = max(risk, 0.95)  # open water: not walkable
            row.append(round(min(0.99, risk), 3))
        grid.append(row)
    return grid


def area_cells(area):
    x0, y0, x1, y1 = AREAS[area]["rect"]
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def apply_hazards(grid, hazards, open_water=True):
    """Overlay interpreted human reports onto a copy of the risk grid.

    Each hazard: {area, hazard_type, risk_score 0..1, block_probability 0..1,
    uncertainty 0..1, source_text}. Reports are applied in order, so a later
    report about the same area refines an earlier one. The more certain a
    report is, the more it pulls cells toward its reported score. Open water
    (river cells off the bridges) is never made walkable by a report.
    Returns (new_grid, uncertainty_grid).
    """
    out = [row[:] for row in grid]
    unc = [[0.0] * N for _ in range(N)]
    closed_by = {}  # cell -> area whose report closed it
    for h in hazards or []:
        area = h.get("area")
        if area not in AREAS:
            continue
        score = _clamp(h.get("risk_score", 0.5))
        block = _clamp(h.get("block_probability", 0.0))
        uncertainty = _clamp(h.get("uncertainty", 0.5))
        weight = 0.3 + 0.7 * (1.0 - uncertainty)
        closes = block * (1.0 - 0.5 * uncertainty) >= 0.5
        for x, y in area_cells(area):
            if open_water and y == RIVER_Y and x not in BRIDGES_X:
                continue
            # A closure reported for a specific area is only lifted by a later
            # report about that same area, not by a vaguer overlapping one.
            if (x, y) in closed_by and closed_by[(x, y)] != area:
                continue
            base = out[y][x]
            if (x, y) in closed_by:  # same area re-reported: start from terrain
                base = grid[y][x]
                del closed_by[(x, y)]
            new = base + (score - base) * weight
            if closes:
                new = max(new, 0.95)  # likely closed: treat as impassable
                closed_by[(x, y)] = area
            else:
                new += 0.3 * block
            out[y][x] = round(min(0.99, max(0.0, new)), 3)
            unc[y][x] = round(max(unc[y][x], uncertainty), 3)
    return out, unc


def _clamp(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, v))


def is_open_water(x, y):
    return y == RIVER_Y and x not in BRIDGES_X


ESCAPE_PENALTY = 30.0


def pathfind(grid, origin, dest, use_risk=True):
    """4-connected deterministic Dijkstra. Extreme-risk cells are impassable.

    Exception: if the origin itself sits inside a blocked zone, the route may
    walk *out* of that zone (heavily penalised) but can never re-enter a
    blocked cell once it reaches passable ground. Open water is never crossed.
    """
    start = (origin, grid[origin[1]][origin[0]] >= BLOCK_RISK)  # (pos, escaping)
    pq = [(0.0, 0, start)]
    dist = {start: 0.0}
    parent = {}
    while pq:
        cost, steps, state = heapq.heappop(pq)
        if cost > dist[state] + 1e-9:
            continue
        pos, escaping = state
        if pos == dest:
            path = []
            cur = state
            while True:
                path.append(cur[0])
                if cur == start:
                    break
                cur = parent[cur]
            return list(reversed(path)), round(cost, 3)
        x, y = pos
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < N and 0 <= ny < N) or is_open_water(nx, ny):
                continue
            cell_risk = grid[ny][nx]
            blocked = cell_risk >= BLOCK_RISK and (nx, ny) != dest
            # never imply it is safe to stay in or pass through unsafe cells
            if blocked and not escaping:
                continue
            step_cost = 1.0 + (9 * cell_risk**2 if use_risk else 0.0)
            if blocked:
                step_cost += ESCAPE_PENALTY
            nxt = ((nx, ny), escaping and blocked)
            new = cost + step_cost
            if new + 1e-9 < dist.get(nxt, math.inf):
                dist[nxt] = new
                parent[nxt] = state
                heapq.heappush(pq, (new, steps + 1, nxt))
    return [], None


def simulate(rain=3, start_name="駅前 (南中央)", hazards=None):
    if start_name not in STARTS:
        raise ValueError("Unknown start name")
    base_grid = risk_grid(rain)
    grid, uncertainty = apply_hazards(base_grid, hazards)
    origin = STARTS[start_name]
    routes = {}
    for name, dest in SHELTERS.items():
        path, total_cost = pathfind(grid, origin, dest, True)
        if path:
            risks = [grid[y][x] for x, y in path]
            routes[name] = {
                "name": name, "destination": list(dest),
                "path": [list(p) for p in path], "steps": len(path) - 1,
                "max_risk": round(max(risks), 3),
                "avg_risk": round(sum(risks)/len(risks), 3),
                "risk_cost": total_cost,
            }
    best = min(routes, key=lambda n: routes[n]["risk_cost"]) if routes else None
    nearest = None
    if routes:
        nearest = min(routes, key=lambda n: routes[n]["steps"])
    baseline = None
    if best:
        simple, _ = pathfind(grid, origin, SHELTERS[best], False)
        if simple:
            baseline = {
                "path": [list(p) for p in simple],
                "steps": len(simple) - 1,
                "max_risk": round(max(grid[y][x] for x, y in simple), 3),
            }
    return {"rain": rain, "start_name": start_name, "origin": list(origin),
            "grid": grid, "base_grid": base_grid, "uncertainty_grid": uncertainty,
            "hazards": list(hazards or []),
            "shelters": {k: list(v) for k, v in SHELTERS.items()},
            "origin_blocked": grid[origin[1]][origin[0]] >= BLOCK_RISK,
            "routes": routes, "best": best, "nearest_by_steps": nearest,
            "baseline_to_best": baseline,
            "note": "Fictional demonstration; not real-time evacuation guidance."}


if __name__ == "__main__":
    import json, sys
    rain = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    start_name = sys.argv[2] if len(sys.argv) > 2 else "駅前 (南中央)"
    hazards = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    print(json.dumps(simulate(rain, start_name, hazards), ensure_ascii=False))
