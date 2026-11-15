"""Route along real OpenStreetMap roads while avoiding (fictional) hazards.

The road network is real (fetched from the Overpass API around the map
location). The risk field it is weighed against is the fictional simulation
grid plus the interpreted reports, projected onto the map with geo.Anchor.
So the *shape* of the route follows real streets, but the hazards, shelters
and therefore the recommendation are still fictional. Stdlib only.
"""
import heapq
import json
import math
import os
import urllib.parse
import urllib.request

from sim_engine import BLOCK_RISK, N

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
WALKABLE = ("trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|"
            "tertiary_link|unclassified|residential|living_street|pedestrian|footway|"
            "path|service|steps|track|cycleway|road")
OUTSIDE_RISK = 0.05      # outside the simulated area: no fictional data, low base risk
RISK_WEIGHT = 9.0        # same shape as the grid engine: cost = len * (1 + 9 r^2)
ESCAPE_PENALTY = 30.0    # per metre, only while leaving a blocked zone you start in


# --------------------------------------------------------------------------
# Data

def overpass_query(bbox):
    s, w, n, e = bbox
    return (f'[out:json][timeout:40];'
            f'way["highway"~"^({WALKABLE})$"]["access"!~"^(private|no)$"]["foot"!="no"]'
            f'({s:.6f},{w:.6f},{n:.6f},{e:.6f});'
            f'(._;>;);out body;')


def fetch_roads(bbox, timeout=60):
    """Download walkable ways in bbox (south, west, north, east). Raises on failure."""
    body = urllib.parse.urlencode({"data": overpass_query(bbox)}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=body,
                                 headers={"User-Agent": "DisasterSim-demo/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network, HTTP 429/504, JSON
        raise RuntimeError(f"OpenStreetMap(Overpass)から道路データを取得できませんでした: {e}") from e


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def build_graph(osm):
    """Overpass JSON -> {"nodes": {id: (lat, lon)}, "adj": {id: [(nbr, metres)]}}."""
    coords = {el["id"]: (el["lat"], el["lon"])
              for el in osm.get("elements", []) if el.get("type") == "node"}
    adj = {}
    for el in osm.get("elements", []):
        if el.get("type") != "way":
            continue
        ids = [i for i in el.get("nodes", []) if i in coords]
        for a, b in zip(ids, ids[1:]):
            if a == b:
                continue
            d = haversine(coords[a], coords[b])
            adj.setdefault(a, []).append((b, d))  # pedestrians ignore oneway
            adj.setdefault(b, []).append((a, d))
    nodes = {i: coords[i] for i in adj}
    if not nodes:
        raise RuntimeError("この範囲に歩行可能な道路データが見つかりませんでした")
    return {"nodes": nodes, "adj": adj}


def largest_component(graph):
    seen, best = set(), set()
    for start in graph["nodes"]:
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            v = stack.pop()
            if v in comp:
                continue
            comp.add(v)
            stack.extend(n for n, _ in graph["adj"][v] if n not in comp)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    return best


def nearest_node(graph, candidates, latlon):
    best = min(candidates, key=lambda i: haversine(graph["nodes"][i], latlon))
    return best, haversine(graph["nodes"][best], latlon)


# --------------------------------------------------------------------------
# Risk projection

class RiskField:
    def __init__(self, grid, anchor):
        self.grid, self.anchor = grid, anchor

    def at(self, latlon):
        fx, fy = self.anchor.cell_of(*latlon)
        x, y = round(fx), round(fy)
        if 0 <= x < N and 0 <= y < N:
            return self.grid[y][x]
        return OUTSIDE_RISK

    def edge(self, a, b):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        return max(self.at(a), self.at(mid), self.at(b))


# --------------------------------------------------------------------------
# Routing

def _dijkstra(graph, field, origin, targets, use_risk):
    """Single-source search over (node, escaping) states. Returns dist, parent."""
    nodes, adj = graph["nodes"], graph["adj"]
    edge_risk = {}
    start = (origin, use_risk and field.at(nodes[origin]) >= BLOCK_RISK)
    dist, parent = {start: 0.0}, {}
    pq = [(0.0, start)]
    remaining = set(targets)
    best_state = {}
    while pq and remaining:
        cost, state = heapq.heappop(pq)
        if cost > dist[state] + 1e-9:
            continue
        node, escaping = state
        if node in remaining:
            remaining.discard(node)
            best_state[node] = state
        for nbr, length in adj[node]:
            key = (node, nbr) if node < nbr else (nbr, node)
            if key not in edge_risk:
                edge_risk[key] = field.edge(nodes[node], nodes[nbr])
            r = edge_risk[key]
            step = length
            nxt_escaping = False
            if use_risk:
                blocked = r >= BLOCK_RISK and nbr not in targets
                if blocked and not escaping:
                    continue
                step = length * (1 + RISK_WEIGHT * r * r)
                if blocked:
                    step += length * ESCAPE_PENALTY
                    nxt_escaping = True
            nxt = (nbr, nxt_escaping)
            new = cost + step
            if new + 1e-9 < dist.get(nxt, math.inf):
                dist[nxt] = new
                parent[nxt] = state
                heapq.heappush(pq, (new, nxt))
    return dist, parent, best_state, edge_risk


def _trace(parent, state):
    out = [state[0]]
    while state in parent:
        state = parent[state]
        out.append(state[0])
    return out[::-1]


def _route_summary(graph, field, node_path, cost):
    pts = [graph["nodes"][i] for i in node_path]
    length, weighted, max_r = 0.0, 0.0, field.at(pts[0])
    for a, b in zip(pts, pts[1:]):
        d = haversine(a, b)
        r = field.edge(a, b)
        length += d
        weighted += d * r
        max_r = max(max_r, r)
    return {"coords": [list(p) for p in pts], "distance_m": round(length, 1),
            "avg_risk": round(weighted / length, 3) if length else round(max_r, 3),
            "max_risk": round(max_r, 3), "risk_cost": round(cost, 1)}


def plan_routes(graph, grid, anchor, origin_latlon, shelters_latlon):
    """Risk-aware road routes from origin to every shelter + distance-only baseline."""
    field = RiskField(grid, anchor)
    comp = largest_component(graph)
    origin, origin_snap = nearest_node(graph, comp, origin_latlon)
    targets, snaps = {}, {}
    for name, ll in shelters_latlon.items():
        node, d = nearest_node(graph, comp, ll)
        targets[name], snaps[name] = node, round(d, 1)
    dist, parent, reached, _ = _dijkstra(graph, field, origin, set(targets.values()), True)
    routes = {}
    for name, node in targets.items():
        if node in reached:
            state = reached[node]
            routes[name] = dict(_route_summary(graph, field, _trace(parent, state), dist[state]),
                                name=name, snap_m=snaps[name])
    best = min(routes, key=lambda k: routes[k]["risk_cost"]) if routes else None
    shortest = None
    if best:
        d2, p2, r2, _ = _dijkstra(graph, field, origin, {targets[best]}, False)
        if targets[best] in r2:
            st = r2[targets[best]]
            shortest = _route_summary(graph, field, _trace(p2, st), d2[st])
    return {"routes": routes, "best": best, "shortest_to_best": shortest,
            "origin_node": list(graph["nodes"][origin]), "origin_snap_m": round(origin_snap, 1),
            "origin_blocked": field.at(graph["nodes"][origin]) >= BLOCK_RISK,
            "graph_size": len(graph["nodes"])}
