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
