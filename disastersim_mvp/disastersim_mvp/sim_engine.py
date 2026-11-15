"""Deterministic, fictional flood-response routing engine. NOT for real evacuations."""
import heapq
import math

N = 15
STARTS = {"駅前 (南中央)": (7, 12), "川沿い住宅街": (7, 7), "商店街": (12, 11)}
SHELTERS = {"北の丘避難所": (2, 2), "東の高台避難所": (12, 2), "西の学校": (3, 12)}


def risk_grid(rain):
    """Create hypothetical flood hazard values on a 0..1 scale."""
    if not 1 <= rain <= 5:
        raise ValueError("rain must be 1..5")
    grid = []
    for y in range(N):
        row = []
        for x in range(N):
            river = 0.54 * math.exp(-abs(y - 7) / 1.65)
            basin_east = 0.38 * math.exp(-math.hypot(x - 11, y - 11) / 2.3)
            basin_west = 0.35 * math.exp(-math.hypot(x - 4, y - 11) / 2.1)
            risk = (river + basin_east + basin_west) * rain / 3.0
            row.append(round(min(0.99, risk), 3))
        grid.append(row)
    return grid


def pathfind(grid, origin, dest, use_risk=True):
    """4-connected deterministic Dijkstra. Extreme-risk cells are impassable."""
    pq = [(0.0, 0, origin)]
    dist = {origin: 0.0}
    parent = {}
    while pq:
        cost, steps, pos = heapq.heappop(pq)
        if cost > dist[pos] + 1e-9:
            continue
        if pos == dest:
            path = []
            cur = pos
            while True:
                path.append(cur)
                if cur == origin:
                    break
                cur = parent[cur]
            return list(reversed(path)), round(cost, 3)
        x, y = pos
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < N and 0 <= ny < N):
                continue
            cell_risk = grid[ny][nx]
            # origin remains allowed even if it lies in a hazard zone; never
            # imply it is safe to remain there, and do not pass unsafe cells.
            if cell_risk >= 0.88 and (nx, ny) != dest:
                continue
            step_cost = 1.0 + (9 * cell_risk**2 if use_risk else 0.0)
            nxt = (nx, ny)
            new = cost + step_cost
            if new + 1e-9 < dist.get(nxt, math.inf):
                dist[nxt] = new
                parent[nxt] = pos
                heapq.heappush(pq, (new, steps + 1, nxt))
    return [], None


def simulate(rain=3, start_name="駅前 (南中央)"):
    if start_name not in STARTS:
        raise ValueError("Unknown start name")
    grid = risk_grid(rain)
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
            "grid": grid, "shelters": {k: list(v) for k, v in SHELTERS.items()},
            "routes": routes, "best": best, "nearest_by_steps": nearest,
            "baseline_to_best": baseline,
            "note": "Fictional demonstration; not real-time evacuation guidance."}


if __name__ == "__main__":
    import json, sys
    rain = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    start_name = sys.argv[2] if len(sys.argv) > 2 else "駅前 (南中央)"
    print(json.dumps(simulate(rain, start_name), ensure_ascii=False))
