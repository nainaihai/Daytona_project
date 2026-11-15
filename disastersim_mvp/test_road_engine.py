"""Road routing on a synthetic street grid (no network)."""
import pytest

from geo import Anchor
from road_engine import build_graph, largest_component, overpass_query, plan_routes
from sim_engine import N, SHELTERS, STARTS, apply_hazards, risk_grid

CENTER = (35.0, 139.0)
ANCHOR = Anchor(STARTS["駅前 (南中央)"], *CENTER)


def street_grid(step=1, extra_isolated=False):
    """Fake Overpass JSON: a street at every `step` cells, covering the sim area."""
    elements, ids = [], {}
    rng = range(-2, N + 2, step)
    for y in rng:
        for x in rng:
            lat, lon = ANCHOR.latlon(x, y)
            ids[(x, y)] = len(ids) + 1
            elements.append({"type": "node", "id": ids[(x, y)], "lat": lat, "lon": lon})
    wid = 10_000
    for y in rng:
        wid += 1
        elements.append({"type": "way", "id": wid, "nodes": [ids[(x, y)] for x in rng]})
    for x in rng:
        wid += 1
        elements.append({"type": "way", "id": wid, "nodes": [ids[(x, y)] for y in rng]})
    if extra_isolated:
        elements += [{"type": "node", "id": 900001, "lat": 36.0, "lon": 140.0},
                     {"type": "node", "id": 900002, "lat": 36.0001, "lon": 140.0},
                     {"type": "way", "id": 99, "nodes": [900001, 900002]}]
    return {"elements": elements}


GRAPH = build_graph(street_grid())
SHELTER_LL = {k: ANCHOR.latlon(*v) for k, v in SHELTERS.items()}


def plan(hazards):
    grid = apply_hazards(risk_grid(3, open_water=False), hazards, open_water=False)[0]
    return plan_routes(GRAPH, grid, ANCHOR, CENTER, SHELTER_LL)


def test_build_graph_and_components():
    g = build_graph(street_grid(extra_isolated=True))
    comp = largest_component(g)
    assert 900001 in g["nodes"] and 900001 not in comp
    with pytest.raises(RuntimeError):
        build_graph({"elements": []})


def test_query_contains_bbox_and_walkable_filter():
    q = overpass_query((35.0, 139.0, 35.01, 139.01))
    assert "35.000000,139.000000,35.010000,139.010000" in q and "footway" in q


def test_routes_start_at_origin_and_reach_shelters():
    p = plan([])
    assert p["best"] == "西の学校"
    for name, r in p["routes"].items():
        assert r["coords"][0] == pytest.approx(list(CENTER), abs=1e-9)
        assert r["coords"][-1] == pytest.approx(list(SHELTER_LL[name]), abs=1e-9)
        assert r["distance_m"] > 0 and 0 <= r["max_risk"] <= 1


def test_closed_area_is_avoided_on_roads():
    closed = {"area": "西の学校周辺", "risk_score": 0.8, "block_probability": 0.9, "uncertainty": 0.2}
    p = plan([closed])
    assert "西の学校" not in p["routes"]
    grid = apply_hazards(risk_grid(3, open_water=False), [closed], open_water=False)[0]
    for r in p["routes"].values():
        for lat, lon in r["coords"][1:-1]:
            fx, fy = ANCHOR.cell_of(lat, lon)
            x, y = round(fx), round(fy)
            if 0 <= x < N and 0 <= y < N:
                assert grid[y][x] < 0.88


def test_detour_is_longer_but_safer_than_shortest():
    flood = {"area": "駅前", "risk_score": 0.85, "block_probability": 0.3, "uncertainty": 0.1}
    p = plan([flood])
    best, sp = p["routes"][p["best"]], p["shortest_to_best"]
    assert sp["distance_m"] < best["distance_m"]      # takes a detour...
    assert best["avg_risk"] < sp["avg_risk"]           # ...to spend less time in the flood


def test_escape_from_blocked_origin():
    p = plan([{"area": "駅前", "risk_score": 0.9, "block_probability": 1.0, "uncertainty": 0.0}])
    assert p["origin_blocked"] and p["best"]


def test_anchor_inverse_roundtrip():
    lat, lon = ANCHOR.latlon(3.0, 9.0)
    assert ANCHOR.cell_of(lat, lon) == pytest.approx((3.0, 9.0))
