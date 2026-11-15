import math

from geo import Anchor, CELL_METERS


def dist_m(a, b):
    dlat = (a[0] - b[0]) * 111_320
    dlon = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


def test_anchor_cell_is_real_location():
    a = Anchor((7, 12), 35.68, 139.76)
    assert a.latlon(7, 12) == (35.68, 139.76)


def test_orientation_and_scale():
    a = Anchor((7, 12), 35.68, 139.76)
    north = a.latlon(7, 11)   # smaller y = north
    east = a.latlon(8, 12)    # larger x = east
    assert north[0] > 35.68 and east[1] > 139.76
    assert abs(dist_m(a.latlon(7, 12), north) - CELL_METERS) < 0.5
    assert abs(dist_m(a.latlon(7, 12), east) - CELL_METERS) < 0.5


def test_geojson_cells():
    gj = Anchor((0, 0), 35.0, 139.0).cells_geojson(15)
    assert len(gj["features"]) == 225
    ring = gj["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    assert all(len(pt) == 2 and 130 < pt[0] < 150 for pt in ring)  # [lon, lat]
