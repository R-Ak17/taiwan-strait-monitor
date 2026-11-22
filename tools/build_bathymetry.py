"""Clip Natural Earth 10m bathymetry (depth bands) to the strait.

Input:  a sparse checkout of github.com/nvkelso/natural-earth-vector (path as argv[1])
Output: data/bathymetry.json  (each polygon = sea deeper than `depth` metres)
Run:    python3 tools/build_bathymetry.py /path/to/natural-earth-vector
"""
import json, sys, os
import shapefile, shapely
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

root = os.path.join(sys.argv[1], '10m_physical', 'ne_10m_bathymetry_all')
BOX = (112, 18, 128, 31)
feats = []
for name, depth in [('K_200', 200), ('J_1000', 1000), ('I_2000', 2000), ('H_3000', 3000), ('G_4000', 4000)]:
    r = shapefile.Reader(os.path.join(root, f'ne_10m_bathymetry_{name}.shp'))
    parts = []
    for s in r.shapes():
        g = make_valid(shape(s.__geo_interface__))
        c = shapely.clip_by_rect(g, *BOX)
        if not c.is_empty:
            parts.append(c)
    if not parts:
        continue
    u = make_valid(shapely.union_all(parts)).simplify(0.01, preserve_topology=True)
    u = shapely.set_precision(u, 0.001)
    feats.append({"type": "Feature", "properties": {"depth": depth}, "geometry": mapping(u)})
    print(name, 'ok')
json.dump({"type": "FeatureCollection", "features": feats}, open('data/bathymetry.json', 'w'), separators=(',', ':'))
print('bytes', os.path.getsize('data/bathymetry.json'))
