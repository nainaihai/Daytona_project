"""Place the fictional simulation grid on a real map (display only).

The hazards, river, shelters and routes stay fictional. We only choose where
on a real basemap to draw them: the simulated start cell is pinned to the
viewer's real location (or a default point), and every cell is CELL_METERS
wide. Coordinates never leave the browser/Streamlit session; they are not
sent to Daytona or Nosana.
"""
import math

CELL_METERS = 60.0
DEFAULT_CENTER = (35.6812, 139.7671)  # 東京駅付近（位置情報が無いときの既定値）
M_PER_DEG_LAT = 111_320.0


class Anchor:
    """Maps grid (x, y) to (lat, lon); y grows southward like the grid."""

    def __init__(self, cell, lat, lon, cell_m=CELL_METERS):
        self.cx, self.cy = cell
        self.lat, self.lon = float(lat), float(lon)
        self.dlat = cell_m / M_PER_DEG_LAT
        self.dlon = cell_m / (M_PER_DEG_LAT * math.cos(math.radians(self.lat)))

    def latlon(self, x, y):
        return (self.lat - (y - self.cy) * self.dlat,
                self.lon + (x - self.cx) * self.dlon)

    def cell_of(self, lat, lon):
        """Inverse of latlon(): fractional grid coordinates."""
        return (self.cx + (lon - self.lon) / self.dlon,
                self.cy - (lat - self.lat) / self.dlat)

    def bbox(self, n, margin=3):
        """(south, west, north, east) covering the grid plus `margin` cells."""
        north, west = self.latlon(-margin, -margin)
        south, east = self.latlon(n - 1 + margin, n - 1 + margin)
        return (south, west, north, east)

    def path(self, cells):
        pts = [self.latlon(x, y) for x, y in cells]
        return [p[0] for p in pts], [p[1] for p in pts]

    def rect(self, x0, y0, x1, y1):
        """Closed outline around inclusive cell rectangle."""
        corners = [(x0 - .5, y0 - .5), (x1 + .5, y0 - .5), (x1 + .5, y1 + .5),
                   (x0 - .5, y1 + .5), (x0 - .5, y0 - .5)]
        return self.path(corners)

    def cells_geojson(self, n):
        feats = []
        for y in range(n):
            for x in range(n):
                lats, lons = self.rect(x, y, x, y)
                feats.append({"type": "Feature", "id": f"{x},{y}", "properties": {},
                              "geometry": {"type": "Polygon",
                                           "coordinates": [[[lo, la] for la, lo in zip(lats, lons)]]}})
        return {"type": "FeatureCollection", "features": feats}

    def center(self, n):
        return self.latlon((n - 1) / 2, (n - 1) / 2)
