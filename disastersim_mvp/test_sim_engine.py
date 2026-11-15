from sim_engine import STARTS, SHELTERS, simulate, risk_grid

def test_output():
    for rain in range(1, 6):
        for name in STARTS:
            r = simulate(rain, name)
            assert len(r['grid']) == 15
            if r['best']:
                best = r['routes'][r['best']]
                assert best['path'][0] == r['origin']
                assert best['path'][-1] == r['shelters'][r['best']]
                assert best['steps'] == len(best['path']) - 1
                assert 0 <= best['max_risk'] <= 1
            for route in r['routes'].values():
                assert len(route['path']) == route['steps'] + 1

def test_risk_increases():
    a = risk_grid(1)
    b = risk_grid(5)
    assert all(b[y][x] >= a[y][x] for y in range(15) for x in range(15))

from sim_engine import apply_hazards, AREAS, BLOCK_RISK, RIVER_Y, BRIDGES_X


def best_path(r):
    return r['routes'][r['best']]['path'] if r['best'] else None


def test_no_hazards_matches_terrain():
    r = simulate(3, "駅前 (南中央)")
    assert r['grid'] == r['base_grid']


def test_river_only_crossable_on_bridges():
    g = risk_grid(1)
    for x in range(15):
        assert (g[RIVER_Y][x] < BLOCK_RISK) == (x in BRIDGES_X)


def test_flood_report_raises_risk_and_safe_report_lowers_it():
    base = risk_grid(3)
    up, unc = apply_hazards(base, [{"area": "駅前", "risk_score": 0.9, "block_probability": 0, "uncertainty": 0.2}])
    down, _ = apply_hazards(base, [{"area": "商店街", "risk_score": 0.05, "block_probability": 0, "uncertainty": 0.2}])
    assert up[12][7] > base[12][7] and unc[12][7] == 0.2
    assert down[11][12] < base[11][12]
    assert up[0][0] == base[0][0]  # outside the area: untouched


def test_uncertain_reports_have_less_effect():
    base = risk_grid(3)
    sure, _ = apply_hazards(base, [{"area": "駅前", "risk_score": 0.9, "uncertainty": 0.0}])
    vague, _ = apply_hazards(base, [{"area": "駅前", "risk_score": 0.9, "uncertainty": 0.9}])
    assert sure[12][7] > vague[12][7] > base[12][7]


def test_blocked_school_reroutes_north():
    before = simulate(3, "駅前 (南中央)")
    assert before['best'] == "西の学校"
    hz = [{"area": "西の学校周辺", "hazard_type": "通行止め", "risk_score": 0.8,
           "block_probability": 0.9, "uncertainty": 0.2}]
    after = simulate(3, "駅前 (南中央)", hz)
    assert "西の学校" not in after['routes']
    assert after['best'] != before['best']
    assert all(after['grid'][y][x] < BLOCK_RISK for x, y in best_path(after)[1:-1])


def test_vague_safe_report_does_not_reopen_specific_closure():
    closed = {"area": "西の学校周辺", "hazard_type": "通行止め", "risk_score": 0.8,
              "block_probability": 0.9, "uncertainty": 0.2}
    safe_west = {"area": "西側の道", "hazard_type": "安全情報", "risk_score": 0.1,
                 "block_probability": 0, "uncertainty": 0.4}
    r = simulate(3, "駅前 (南中央)", [closed, safe_west])
    assert "西の学校" not in r['routes']
    # ...but a new report about the same area can lift it
    reopened = dict(closed, hazard_type="安全情報", risk_score=0.1, block_probability=0)
    r2 = simulate(3, "駅前 (南中央)", [closed, reopened])
    assert "西の学校" in r2['routes']


def test_closing_all_bridges_isolates_north_from_south():
    hz = [{"area": a, "risk_score": 0.9, "block_probability": 1.0, "uncertainty": 0.0}
          for a in ("西の橋", "中央橋", "東の橋")]
    r = simulate(3, "駅前 (南中央)", hz)
    assert set(r['routes']) == {"西の学校"}


def test_unknown_area_ignored():
    g = risk_grid(2)
    assert apply_hazards(g, [{"area": "火星"}])[0] == g


def test_can_escape_blocked_origin_but_not_reenter():
    hz = [{"area": "駅前", "hazard_type": "浸水", "risk_score": 0.9, "block_probability": 1.0, "uncertainty": 0.0}]
    r = simulate(3, "駅前 (南中央)", hz)
    assert r['origin_blocked'] and r['best']
    path = best_path(r)
    flags = [r['grid'][y][x] >= BLOCK_RISK for x, y in path[:-1]]
    # once the route leaves the blocked zone it never goes back in
    assert flags[0] and flags == sorted(flags, reverse=True)
    for route in r['routes'].values():
        assert not any(y == RIVER_Y and x not in BRIDGES_X for x, y in route['path'])
