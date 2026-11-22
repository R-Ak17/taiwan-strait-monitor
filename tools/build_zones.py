"""Build Kinmen zones, the UNCLOS Art. 15 equidistance line and infrastructure points.

Inputs:  data/land.json (OSM land, built earlier)
Outputs: data/zones.json, data/equidistance.json, data/infrastructure.json
Run:     python3 tools/build_zones.py
"""
import json, math
import shapely
from shapely.geometry import Polygon, LineString, Point, MultiPoint, shape, mapping, box
from shapely.ops import unary_union, transform

def dms(d, m, s): return d + m / 60 + s / 3600

# --- Official vertices, read from the Mainland Affairs Council map (drawn from the
#     MND notice of 7 June 2004). (lat d,m,s), (lon d,m,s)
V = {
    '1': ((24, 29, 43), (118, 31, 22)),
    '2': ((24, 28, 15), (118, 31, 45)),   # label clipped on the map: 28'1x" / 31'4x"; midpoint used
    '3': ((24, 25, 37), (118, 32, 46)),   # same point as 22
    '4': ((24, 21, 49), (118, 30, 34)),
    '6': ((24, 19, 20), (118, 27, 52)),
    "6'": ((24, 20, 29), (118, 23, 12)),
    '7': ((24, 17, 39), (118, 19, 11)),
    '8': ((24, 18, 24), (118, 16, 34)),
    '9': ((24, 18, 44), (118, 13, 30)),
    '10': ((24, 21, 37), (118, 9, 19)),
    '11': ((24, 22, 56), (118, 7, 54)),
    '12': ((24, 24, 36), (118, 8, 49)),
    '13': ((24, 26, 15), (118, 11, 49)),
    "13'": ((24, 27, 8), (118, 12, 22)),
    '14': ((24, 28, 41), (118, 13, 54)),
    '15': ((24, 30, 19), (118, 16, 31)),
    '16': ((24, 30, 32), (118, 19, 51)),
    '17': ((24, 31, 55), (118, 23, 4)),
    '18': ((24, 32, 34), (118, 24, 19)),
    '19': ((24, 31, 43), (118, 26, 58)),
    '20': ((24, 31, 6), (118, 27, 41)),
    '21': ((24, 28, 19), (118, 30, 34)),
    '23': ((24, 22, 25), (118, 29, 10)),
    '24': ((24, 20, 20), (118, 27, 26)),
    "24'": ((24, 21, 34), (118, 23, 13)),
    '25': ((24, 18, 44), (118, 19, 11)),
    '26': ((24, 19, 27), (118, 16, 54)),
    '27': ((24, 19, 49), (118, 13, 33)),
}
V['22'] = V['3']
ll = {k: (round(dms(*lo), 6), round(dms(*la), 6)) for k, (la, lo) in V.items()}

# Red line (prohibited) and blue line (restricted), in the order drawn on the map.
PROHIBITED = ['10', '11', '12', '13', "13'", '14', '15', '16', '17', '18', '19', '20', '21', '22', '23', '24', "24'", '25', '26', '27', '10']
RESTRICTED_OUTER = ['10', '11', '12', '13', "13'", '14', '15', '16', '17', '18', '19', '20', '1', '2', '3', '4', '6', "6'", '7', '8', '9', '10']
prohibited = Polygon([ll[k] for k in PROHIBITED])
outer = Polygon([ll[k] for k in RESTRICTED_OUTER])
restricted_band = outer.difference(prohibited)
assert prohibited.is_valid and outer.is_valid, 'zone polygon invalid: check vertex order'

# --- Local metric projection (equirectangular around Kinmen) for distances ---
LAT0 = 24.45
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110574
to_m = lambda x, y, z=None: (x * KX, y * KY)
to_ll = lambda x, y, z=None: (x / KX, y / KY)

land = json.load(open('data/land.json'))
polys = [shape(f['geometry']) for f in land['kinmen']['features']]
# ROC-administered Kinmen group = land touching the official restricted polygon.
roc = [p for p in polys if p.intersects(outer)]
prc = [p for p in polys if not p.intersects(outer)]
print('land polygons: ROC', len(roc), 'PRC', len(prc))

# --- UNCLOS Art. 15 equidistance line, approximated from OSM coastlines ---
def sample(ps, step=150):
    pts = []
    for p in ps:
        ring = transform(to_m, p.exterior)
        n = max(4, int(ring.length // step))
        pts += [ring.interpolate(i / n, normalized=True) for i in range(n)]
    return pts

def ring_pts(lon, lat, r=250):
    # an islet too small for the land data, added as a small ring of coast points
    x, y = to_m(lon, lat)
    return [Point(x + r * math.cos(a / 8 * math.pi), y + r * math.sin(a / 8 * math.pi)) for a in range(16)]

def equidistance(roc, prc, extra_roc, extend_box, study_box):
    """Boundary between the Voronoi regions of ROC-side and PRC-side coast points."""
    roc_pts, prc_pts = sample(roc) + extra_roc, sample(prc)
    seeds = roc_pts + prc_pts
    cells = shapely.voronoi_polygons(MultiPoint(seeds), extend_to=transform(to_m, box(*extend_box)))
    roc_set = set((round(p.x, 3), round(p.y, 3)) for p in roc_pts)
    tree = shapely.STRtree(seeds)
    roc_cells = []
    for c in cells.geoms:   # each cell holds exactly one seed point
        idx = tree.query(c, predicate='contains')
        if len(idx) and (round(seeds[idx[0]].x, 3), round(seeds[idx[0]].y, 3)) in roc_set:
            roc_cells.append(c)
    region = unary_union(roc_cells)
    line = region.boundary.intersection(transform(to_m, box(*study_box)))
    line = shapely.line_merge(line) if line.geom_type == 'MultiLineString' else line
    return transform(to_ll, line).simplify(0.0005), region

# Kinmen. Erdan Island is too small for the 100 m land data; added from Wikipedia coordinates.
eq_line, roc_region = equidistance(roc, prc, ring_pts(118.161306, 24.378028),
                                   (117.7, 24.1, 118.9, 24.85), (117.95, 24.30, 118.60, 24.65))

# Matsu. ROC land = polygons near the archipelago's main islands (Wikipedia coordinates:
# Nangan, Beigan, Juguang, Dongyin, and Liang Island's extreme point); radius in km.
MATSU_ANCHORS = [(119.933, 26.150, 5), (119.989, 26.224, 8), (119.956, 25.967, 6), (120.502, 26.386, 5), (120.227, 26.3475, 3)]
mpolys = [shape(f['geometry']) for f in json.load(open('data/land.json'))['matsu']['features']]
def near_anchor(p):
    pm = transform(to_m, p)
    return any(pm.distance(Point(*to_m(lo, la))) <= r * 1000 for lo, la, r in MATSU_ANCHORS)
m_roc = [p for p in mpolys if near_anchor(p)]
m_prc = [p for p in mpolys if not near_anchor(p)]
print('Matsu land polygons: ROC', len(m_roc), 'PRC', len(m_prc))
# The mainland coast west and north of Matsu lies outside the detail box, so add the
# surrounding coast from the coarser strait-wide land set.
M_EXT = (119.3, 25.5, 121.0, 26.9)
coarse = [shape(f['geometry']) for f in json.load(open('data/land.json'))['strait']['features']]
m_prc += [shapely.clip_by_rect(p, *M_EXT) for p in coarse if p.intersects(box(*M_EXT))]
m_prc = [q for g in m_prc for q in (getattr(g, 'geoms', None) or [g]) if q.geom_type == 'Polygon' and not q.is_empty]
m_eq_line, _ = equidistance(m_roc, m_prc, [], M_EXT, (119.6, 25.8, 120.75, 26.7))

# Does any part of the prohibited/restricted zones lie on the PRC side of the line?
roc_region_ll = transform(to_ll, roc_region)
beyond_p = prohibited.difference(roc_region_ll)
beyond_r = outer.difference(roc_region_ll)
area_km2 = lambda g: transform(to_m, g).area / 1e6
print(f'prohibited area {area_km2(prohibited):.1f} km2, of which beyond equidistance {area_km2(beyond_p):.1f} km2')
print(f'restricted outer area {area_km2(outer):.1f} km2, of which beyond equidistance {area_km2(beyond_r):.1f} km2')

def fc(features): return {"type": "FeatureCollection", "features": features}
def rnd(g, nd=5): return json.loads(json.dumps(mapping(shapely.set_precision(g, 10 ** -nd))))

zones = fc([
    {"type": "Feature", "properties": {"zone": "restricted", "name": "Restricted waters", "area_km2": round(area_km2(restricted_band), 1)}, "geometry": rnd(restricted_band)},
    {"type": "Feature", "properties": {"zone": "prohibited", "name": "Prohibited waters", "area_km2": round(area_km2(prohibited), 1)}, "geometry": rnd(prohibited)},
] + [
    {"type": "Feature", "properties": {"zone": "vertex", "label": k, "estimated": k == '2'}, "geometry": {"type": "Point", "coordinates": list(ll[k])}}
    for k in V if k != '22'
])
json.dump(zones, open('data/zones.json', 'w'), separators=(',', ':'), ensure_ascii=False)

eq = fc([
    {"type": "Feature", "properties": {"case": "kinmen", "name": "Equidistance line (UNCLOS Art. 15 default), computed"}, "geometry": rnd(eq_line)},
    {"type": "Feature", "properties": {"case": "matsu", "name": "Equidistance line (UNCLOS Art. 15 default), computed"}, "geometry": rnd(m_eq_line)},
    {"type": "Feature", "properties": {"name": "Zone area beyond equidistance", "area_km2": round(area_km2(beyond_r), 2)}, "geometry": rnd(beyond_r)},
])
json.dump(eq, open('data/equidistance.json', 'w'), separators=(',', ':'))

# --- Infrastructure: only sites with coordinates from a citable source ---
WP = 'https://en.wikipedia.org/wiki/'
infra = fc([
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.35917, 24.42778]},
     "properties": {"name": "Kinmen Airport (Shangyi)", "side": "ROC", "kind": "airport", "dual_use": True,
                    "note": "Single-runway civil airport sharing its site with the ROC Air Force.", "source": WP + "Kinmen_Airport"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.286417, 24.41625]},
     "properties": {"name": "Shuitou Pier", "side": "ROC", "kind": "port", "dual_use": False,
                    "note": "Ferry terminal: routes to Xiamen (Wutong, Dongdu), Quanzhou and Lieyu.", "source": WP + "Shuitou_Pier"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.28306, 24.45083]},
     "properties": {"name": "Kinmen Bridge", "side": "ROC", "kind": "bridge", "dual_use": False,
                    "note": "About 5 km, Greater Kinmen to Lieyu; opened 30 October 2022.", "source": WP + "Kinmen_Bridge"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.16556, 24.39028]},
     "properties": {"name": "Dadan Island", "side": "ROC", "kind": "military", "dual_use": False,
                    "note": "About 4.4 km from Xiamen. Handed to county control in 2014; sources differ on the current troop presence.", "source": WP + "Dadan_Island"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.161306, 24.378028]},
     "properties": {"name": "Erdan Island", "side": "ROC", "kind": "military", "dual_use": False,
                    "note": "Troops stationed; no residents. Site of 2022 drone incidents.", "source": WP + "Erdan_Island"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.1884, 24.5333]},
     "properties": {"name": "Wutong Ferry Terminal", "side": "PRC", "kind": "port", "dual_use": False,
                    "note": "Xiamen terminal with hourly ferries to Kinmen.", "source": WP + "Wutong_Ferry_Terminal"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [118.12778, 24.54417]},
     "properties": {"name": "Xiamen Gaoqi Airport", "side": "PRC", "kind": "airport", "dual_use": False,
                    "note": "Due to end civil operations when Xiang'an airport opens (24 Dec 2026, per Wikipedia).", "source": WP + "Xiamen_Gaoqi_International_Airport"}},
])
def site(lon, lat, name, side, kind, note, page, dual=False):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "side": side, "kind": kind, "dual_use": dual, "note": note, "source": WP + page}}
infra['features'] += [
    # Matsu
    site(119.95639, 26.157417, "Nangan Airport", "ROC", "airport", "Public airport on Nangan with turboprop flights to Taiwan.", "Nangan_Airport"),
    site(120.0025, 26.22389, "Beigan Airport", "ROC", "airport", "Public airport on Beigan; flights to Taipei Songshan.", "Beigan_Airport"),
    site(120.502, 26.386, "Dongyin", "ROC", "military", "Northernmost Matsu island, about 19 km from the mainland. Soldiers outnumber residents several times; missile deployment reported in 2001.", "Dongyin"),
    # Penghu
    site(119.63, 23.56667, "Penghu Airport (Magong)", "ROC", "airport", "Domestic airport owned by the ROC Air Force; Magong Air Base shares the site.", "Penghu_Airport", dual=True),
    site(119.570556, 23.560528, "Magong Harbor", "ROC", "port", "Main harbour: 9 docks, ferries to Kaohsiung, Tainan and Chiayi; cruise pier added 2015.", "Magong_Harbor"),
    site(119.548417, 23.650972, "Penghu Great Bridge", "ROC", "bridge", "2.5 km bridge linking Baisha and Siyu islands; current deck opened 1996.", "Penghu_Great_Bridge"),
    site(119.4175, 23.21306, "Qimei Airport", "ROC", "airport", "Small public airport on Qimei, Penghu's southernmost inhabited island.", "Qimei_Airport"),
]
infra['features'] += [
    # PRC coast (Fujian)
    site(119.66333, 25.935, "Fuzhou Changle Airport", "PRC", "airport", "Fuzhou's international airport, about 50 km east of the city; 16.1 million passengers in 2025.", "Fuzhou_Changle_International_Airport"),
    site(118.58944, 24.79889, "Quanzhou Jinjiang Airport", "PRC", "airport", "Shared military and civilian airport serving Quanzhou; a XiamenAir focus base.", "Quanzhou_Jinjiang_International_Airport", dual=True),
    site(119.77667, 25.54667, "Pingtan Island", "PRC", "port", "Bridged to the mainland in 2010 and by a road-rail bridge with a high-speed station (2020); Haixia ferries run from Aoqian Wharf to Taichung and Kaohsiung.", "Pingtan_Island"),
    site(119.883696, 26.324039, "Huangqi", "PRC", "port", "Town on the Huangqi peninsula; the Huangqi–Matsu ferry (from December 2015) is the shortest mainland–Matsu crossing.", "Huangqi,_Lianjiang_County"),
    site(119.22611, 26.09, "Port of Fuzhou", "PRC", "port", "Fujian's capital port; deep-water areas at Jiangyin and Luoyuan Bay.", "Port_of_Fuzhou"),
]
CASE_BOXES = {"kinmen": (117.75, 24.15, 118.85, 24.8), "matsu": (119.75, 25.85, 120.55, 26.45), "penghu": (119.25, 23.10, 119.80, 23.85)}
for ft in infra['features']:
    lo, la = ft['geometry']['coordinates']
    ft['properties']['case'] = next((k for k, (a1, b1, a2, b2) in CASE_BOXES.items() if a1 <= lo <= a2 and b1 <= la <= b2), None)
json.dump(infra, open('data/infrastructure.json', 'w'), separators=(',', ':'), ensure_ascii=False)
print('written: zones, equidistance, infrastructure')
