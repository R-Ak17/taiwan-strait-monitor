import json, sys, os
import shapely
from shapely.geometry import shape, box, Polygon, mapping
from shapely.validation import make_valid

src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src))
geoms = [shape(g) for g in d['geometries']]
print('geoms', len(geoms), flush=True)

STRAIT = (112, 18, 128, 31)
KINMEN = (117.75, 24.15, 118.85, 24.8)

def polys_of(g):
    if g.is_empty: return []
    if g.geom_type == 'Polygon': return [g]
    return [p for x in getattr(g, 'geoms', []) for p in polys_of(x)]

def clip(b, tol, minarea, cut=None):
    bb = box(*b)
    res = []
    for g in geoms:
        if not g.intersects(bb):
            continue
        c = make_valid(shapely.clip_by_rect(g, *b))
        if cut is not None:
            c = c.difference(shapely.union_all([box(*b) for b in cut]))   # holes where detailed sets go
        c = c.simplify(tol, preserve_topology=True)
        # Drop interior rings (lakes, reservoirs): at this scale they only add noise.
        res.extend(Polygon(p.exterior) for p in polys_of(c) if p.area >= minarea)
    return res

def coast(polys, edges, nd):
    # Coastline = polygon outlines minus the artificial clip-box edges.
    cut = shapely.union_all([box(*e).boundary for e in edges]).buffer(1e-5)
    lines = shapely.union_all([p.exterior for p in polys]).difference(cut)
    return fc_lines(lines, nd)

def rnd(cs, nd): return [[round(x, nd), round(y, nd)] for x, y in cs]

def fc_polys(polys, nd):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [rnd(p.exterior.coords, nd)]}}
        for p in polys]}

def fc_lines(g, nd):
    parts = [g] if g.geom_type == 'LineString' else list(getattr(g, 'geoms', []))
    parts = [l for l in parts if l.geom_type == 'LineString' and len(l.coords) > 1]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString",
          "coordinates": [rnd(l.coords, nd) for l in parts]}}]}

# High-detail boxes for each case study; they are cut out of the coarse strait set.
DETAIL = {
    "kinmen": KINMEN,
    "matsu": (119.75, 25.85, 120.55, 26.45),
    "penghu": (119.25, 23.10, 119.80, 23.85),
}
strait = clip(STRAIT, 0.006, 0.00008, cut=list(DETAIL.values()))
data = {"strait": fc_polys(strait, 3), "coast_strait": coast(strait, [STRAIT] + list(DETAIL.values()), 3)}
for name, b in DETAIL.items():
    polys = clip(b, 0.0002, 0.000001)
    data[name] = fc_polys(polys, 4)
    data["coast_" + name] = coast(polys, [b], 4)
    print(name, len(polys), flush=True)
json.dump(data, open(out, 'w'), separators=(',', ':'))
print('bytes', os.path.getsize(out))
