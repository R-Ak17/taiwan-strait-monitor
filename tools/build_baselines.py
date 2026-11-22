"""Taiwan's 1999 baselines (as amended 2009), 12 nm territorial sea and 24 nm contiguous zone,
plus OpenStreetMap submarine cables.

Inputs:  data/land.json; an Overpass export of submarine cables (path as argv[1])
Outputs: data/baselines.json, data/cables.json
Run:     python3 tools/build_baselines.py /path/to/export.geojson
"""
import json, sys
import shapely
from shapely.geometry import Polygon, LineString, Point, shape, mapping
from shapely.ops import transform
from pyproj import Transformer

NM = 1852.0

# Table of the Executive Yuan notice of 10 Feb 1999 (台88內字第06161號), amended
# 18 Nov 2009 (院臺建字第0980097355號). WGS-84. (id, name, lon d m s, lat d m s)
T = [
    ('T1', '三貂角 Sandiao Cape', (122, 0, 27.97), (25, 0, 29.83)),
    ('T2', '棉花嶼 Mianhua Islet', (122, 6, 32.79), (25, 29, 1.87)),
    ('T3', '彭佳嶼 1 Pengjia Islet 1', (122, 5, 9.69), (25, 37, 47.51)),
    ('T4', '彭佳嶼 2 Pengjia Islet 2', (122, 4, 24.24), (25, 37, 57.44)),
    ('T5', '麟山鼻 Linshanbi', (121, 30, 33.94), (25, 17, 29.23)),
    ('T6', '大崛溪 Dajue Creek', (121, 5, 53.57), (25, 4, 5.59)),
    ('T7', '大潭 Datan', (121, 1, 58.49), (25, 1, 57.38)),
    ('T8', '翁公石 Wenggong Rock', (119, 32, 27.41), (23, 47, 15.19)),
    ('T9', '花嶼 1 Hua Islet 1', (119, 19, 6.84), (23, 24, 42.82)),
    ('T10', '花嶼 3 Hua Islet 3', (119, 18, 52.12), (23, 24, 12.56)),
    ('T11', '花嶼 2 Hua Islet 2', (119, 18, 49.10), (23, 23, 57.43)),
    ('T12', '貓嶼 Mao Islet', (119, 19, 6.03), (23, 19, 28.91)),
    ('T13', '七美嶼 Qimei Islet', (119, 24, 58.10), (23, 11, 35.80)),
    ('T14', '琉球嶼 Liuqiu Islet', (120, 21, 13.13), (22, 19, 25.73)),
    ('T15', '七星岩 Qixing Rock', (120, 49, 35.09), (21, 45, 22.76)),
    ('T16', '小蘭嶼 1 Lesser Orchid Island 1', (121, 36, 48.45), (21, 56, 18.23)),
    ('T17', '小蘭嶼 2 Lesser Orchid Island 2', (121, 37, 2.34), (21, 56, 58.88)),
    ('T18', '飛岩 Feiyan Rock', (121, 31, 21.17), (22, 41, 7.53)),
    ('T19', '石梯鼻 Shitibi', (121, 30, 59.79), (23, 28, 59.92)),
    ('T20', '烏石鼻 Wushibi', (121, 51, 43.71), (24, 28, 49.82)),
    ('T21', '米島 Mi Islet', (121, 54, 11.25), (24, 35, 57.69)),
    ('T22', '龜頭岸 Guitou Coast', (121, 57, 52.78), (24, 50, 22.24)),
]
D = [
    ('D1', '西北角 NW corner', (116, 45, 55.79), (20, 46, 3.92)),
    ('D2', '東沙北角 N Dongsha', (116, 42, 36.58), (20, 44, 3.93)),
    ('D3', '東沙南角 S Dongsha', (116, 41, 46.77), (20, 41, 55.20)),
    ('D4', '西南角 SW corner', (116, 45, 16.75), (20, 35, 41.12)),
]
dd = lambda t: t[0] + t[1] / 60 + t[2] / 3600
pts = lambda rows: [(dd(lo), dd(la)) for _, _, lo, la in rows]

def zones(rows, lon0, lat0):
    # Azimuthal equidistant projection centred on the area keeps distances true to
    # well under 0.1% over this range, so 12 and 24 nm buffers are accurate.
    fwd = Transformer.from_crs('EPSG:4326', f'+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84', always_xy=True)
    inv = Transformer.from_crs(f'+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84', 'EPSG:4326', always_xy=True)
    poly = Polygon(pts(rows))
    p = transform(fwd.transform, poly)
    ts = transform(inv.transform, p.buffer(12 * NM, 64))
    cz = transform(inv.transform, p.buffer(24 * NM, 64))
    return poly, ts, cz

tw_base, tw_ts, tw_cz = zones(T, 120.9, 23.6)
ds_base, ds_ts, ds_cz = zones(D, 116.73, 20.7)

# Cross-check: each base point should sit on or near the coast in the land data.
land = json.load(open('data/land.json'))
coast = shapely.union_all([shape(f['geometry']).exterior for k in ('strait', 'kinmen') for f in land[k]['features']])
fwd = Transformer.from_crs('EPSG:4326', '+proj=aeqd +lat_0=23.6 +lon_0=120.9 +datum=WGS84', always_xy=True)
coast_m = transform(fwd.transform, coast)
print('distance from each base point to the nearest mapped coastline:')
far = []
for pid, name, lo, la in T:
    d = transform(fwd.transform, Point(dd(lo), dd(la))).distance(coast_m)
    flag = '' if d < 1500 else '  <-- check (islet may be missing from the 100 m land data)'
    if flag: far.append(pid)
    print(f'  {pid:>4} {name:<28} {d:7.0f} m{flag}')

def f(geom, **props): return {"type": "Feature", "properties": props, "geometry": mapping(shapely.set_precision(geom, 1e-5))}
feats = [
    f(tw_cz.difference(tw_ts), kind='contiguous', name='Contiguous zone (12–24 nm), Taiwan and Penghu'),
    f(tw_ts.difference(tw_base), kind='territorial', name='Territorial sea (12 nm), Taiwan and Penghu'),
    f(LineString(tw_base.exterior.coords), kind='baseline', name='Straight baselines, Taiwan and Penghu (1999/2009)'),
    f(ds_cz.difference(ds_ts), kind='contiguous', name='Contiguous zone, Dongsha'),
    f(ds_ts.difference(ds_base), kind='territorial', name='Territorial sea, Dongsha'),
    f(LineString(ds_base.exterior.coords), kind='baseline', name='Straight baselines, Dongsha'),
] + [f(Point(dd(lo), dd(la)), kind='basepoint', id=pid, name=name, lon_dms=f"{lo[0]}°{lo[1]:02d}′{lo[2]:05.2f}″E", lat_dms=f"{la[0]}°{la[1]:02d}′{la[2]:05.2f}″N") for pid, name, lo, la in T + D]
json.dump({"type": "FeatureCollection", "features": feats}, open('data/baselines.json', 'w'), separators=(',', ':'), ensure_ascii=False)

# --- Cables (OpenStreetMap via Overpass) ---
src = json.load(open(sys.argv[1]))
out = []
for ft in src['features']:
    p = ft['properties']
    g = shape(ft['geometry']).simplify(0.0005)
    out.append({"type": "Feature", "geometry": mapping(shapely.set_precision(g, 1e-5)), "properties": {
        "name": p.get('name') or p.get('seamark:name') or p.get('short_name') or 'Unnamed cable',
        "alt": p.get('seamark:name') if p.get('name') else None,
        "operator": p.get('operator'), "osm": p.get('@id'), "source": p.get('source')}})
json.dump({"type": "FeatureCollection", "features": out, "osm_timestamp": src.get('timestamp')}, open('data/cables.json', 'w'), separators=(',', ':'), ensure_ascii=False)
print('cables:', len(out), [o['properties']['name'] for o in out])
print('flagged points:', far)
