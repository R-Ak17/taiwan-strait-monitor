"""Daily vessel-presence collector for the Taiwan Strait, using the Global Fishing Watch 4Wings API.

Why GFW and not a live feed: free live AIS (AISStream) has no receivers around Taiwan, so it returned
nothing for the strait. GFW merges satellite and terrestrial AIS, covers the strait, and is free for
non-commercial use (CC BY-NC 4.0, attribution "Powered by Global Fishing Watch"). Its data stop about
96 hours before now, so this layer shows the most recent complete day, not live positions.

What it writes (to --out, published on the ais-data branch):
  latest.geojson      one point per grid cell and flag group for the latest day: presence hours by
                      Taiwan / PRC / other flags (0.1 deg cells strait-wide, 0.01 deg in the three case areas)
  summary.json        that day's totals per area: distinct vessels and presence hours by flag group
  counts_history.csv  one row per day and area, kept across runs (the time series for later phases)

Usage:
  GFW_API_TOKEN=... python3 tools/gfw_collect.py --out ais-out              # latest day
  GFW_API_TOKEN=... python3 tools/gfw_collect.py --out ais-out --backfill 60 # also fill 60 past days of counts
"""
import argparse, csv, io, json, os, sys, time, urllib.error, urllib.parse, urllib.request, zipfile
from datetime import date, datetime, timedelta, timezone

API = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
DATASET = "public-global-presence:latest"
# (west, south, east, north); the same rectangles as the map's study areas.
STRAIT = (116.5, 21.5, 122.5, 27.5)
AREAS = {
    "kinmen": (117.95, 24.30, 118.60, 24.65),
    "matsu": (119.80, 25.90, 120.56, 26.42),
    "penghu": (119.28, 23.15, 119.75, 23.80),
    "strait": STRAIT,
}
LAG_DAYS = 5
_SHOWN = False   # GFW data end ~96 h ago; one extra day so the day is complete

def side_of(flag):
    f = (flag or "").upper()
    return "taiwan" if f == "TWN" else "prc" if f == "CHN" else "other"

def box_geojson(b):
    w, s, e, n = b
    return json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}]})

def request(token, params, box, tries=4):
    url = API + "?" + urllib.parse.urlencode(params)
    body = json.dumps({"geojson": box_geojson(box)}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json", "Content-Language": "en-EN",
            "User-Agent": "taiwan-strait-monitor/1.0 (academic research; github actions)", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                print(f"HTTP {e.code}, retrying: {msg}", file=sys.stderr); time.sleep(10 * (attempt + 1)); continue
            raise RuntimeError(f"GFW API HTTP {e.code}: {msg}")
    if raw[:2] == b"PK":   # some formats come back as a ZIP holding the data and a caveats file
        z = zipfile.ZipFile(io.BytesIO(raw))
        name = next(n for n in z.namelist() if n.lower().endswith(".json"))
        raw = z.read(name)
    data = json.loads(raw)
    global _SHOWN
    if not _SHOWN:   # print one sample so the Actions log shows the real response shape
        _SHOWN = True
        print("first response sample: " + json.dumps(data)[:400], file=sys.stderr)
    return data

def rows_of(obj):
    """The report nests rows as entries -> [{dataset-version: [row, ...]}]; collect every row dict."""
    out = []
    def walk(x):
        if isinstance(x, list):
            for i in x: walk(i)
        elif isinstance(x, dict):
            if any(k in x for k in ("hours", "lat", "vesselId", "vessel_id")):
                out.append(x)
            else:
                for v in x.values(): walk(v)
    walk(obj.get("entries", obj) if isinstance(obj, dict) else obj)
    return out

def base_params(day, **extra):
    p = {"datasets[0]": DATASET, "date-range": f"{day.isoformat()},{(day + timedelta(days=1)).isoformat()}",
         "format": "JSON", "temporal-resolution": "ENTIRE"}
    p.update(extra)
    return p

def area_counts(token, day, box):
    vessels = rows_of(request(token, base_params(day, **{"group-by": "VESSEL_ID", "spatial-aggregation": "true"}), box))
    by_side_v = {"taiwan": 0, "prc": 0, "other": 0}
    for r in vessels:
        by_side_v[side_of(r.get("flag"))] += 1
    flags = rows_of(request(token, base_params(day, **{"group-by": "FLAG", "spatial-aggregation": "true"}), box))
    hours = {"taiwan": 0.0, "prc": 0.0, "other": 0.0}
    for r in flags:
        hours[side_of(r.get("flag"))] += float(r.get("hours") or 0)
    return {"vessels": len(vessels), "by_side": by_side_v, "hours": {k: round(v, 1) for k, v in hours.items()}}

def grid(token, day, box, resolution, tag):
    rows = rows_of(request(token, base_params(day, **{"group-by": "FLAG", "spatial-resolution": resolution}), box))
    cells = {}
    for r in rows:
        if r.get("lat") is None or r.get("lon") is None:
            continue
        key = (round(float(r["lon"]), 3), round(float(r["lat"]), 3), side_of(r.get("flag")))
        c = cells.setdefault(key, {"hours": 0.0, "flags": set()})
        c["hours"] += float(r.get("hours") or 0)
        if r.get("flag"): c["flags"].add(r["flag"])
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
             "properties": {"side": side, "hours": round(c["hours"], 2), "flags": ",".join(sorted(c["flags"])), "res": tag}}
            for (lon, lat, side), c in cells.items()]

def inside(pt, b):
    return b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]

def load_history(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {(r["date"], r["area"]): r for r in csv.DictReader(f)}

HIST_FIELDS = ["date", "area", "vessels", "vessels_taiwan", "vessels_prc", "vessels_other",
               "hours_taiwan", "hours_prc", "hours_other", "source"]

def hist_row(day, area, c):
    return {"date": day.isoformat(), "area": area, "vessels": c["vessels"],
            "vessels_taiwan": c["by_side"]["taiwan"], "vessels_prc": c["by_side"]["prc"], "vessels_other": c["by_side"]["other"],
            "hours_taiwan": c["hours"]["taiwan"], "hours_prc": c["hours"]["prc"], "hours_other": c["hours"]["other"], "source": "GFW"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ais-out")
    ap.add_argument("--backfill", type=int, default=0, help="also fill daily counts for this many earlier days")
    a = ap.parse_args()
    token = os.environ.get("GFW_API_TOKEN", "").strip()
    if not token:
        sys.exit("GFW_API_TOKEN is not set (add it as a repository secret).")
    os.makedirs(a.out, exist_ok=True)
    day = datetime.now(timezone.utc).date() - timedelta(days=LAG_DAYS)

    # Map layer: strait-wide coarse grid, replaced by fine cells inside the three case areas.
    feats = [f for f in grid(token, day, STRAIT, "LOW", "0.1")
             if not any(inside(f["geometry"]["coordinates"], AREAS[k]) for k in ("kinmen", "matsu", "penghu"))]
    for k in ("kinmen", "matsu", "penghu"):
        feats += grid(token, day, AREAS[k], "HIGH", "0.01")
    print(f"{day}: {len(feats)} grid cells", file=sys.stderr)

    areas = {k: area_counts(token, day, b) for k, b in AREAS.items()}
    for k, c in areas.items():
        print(f"  {k}: {c['vessels']} vessels (Taiwan {c['by_side']['taiwan']}, PRC {c['by_side']['prc']}, other {c['by_side']['other']})", file=sys.stderr)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(a.out, "latest.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "generated_utc": now, "data_date": day.isoformat(), "kind": "presence-grid", "features": feats}, f, separators=(",", ":"))
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump({"generated_utc": now, "data_date": day.isoformat(), "kind": "presence-grid", "areas": areas,
                   "source": "Global Fishing Watch, AIS vessel presence (public-global-presence:latest), CC BY-NC 4.0"}, f, indent=1)

    hist_path = os.path.join(a.out, "counts_history.csv")
    hist = load_history(hist_path)
    hist = {k: v for k, v in hist.items() if v.get("source") == "GFW"}   # drop the old AISStream rows
    for k, c in areas.items():
        hist[(day.isoformat(), k)] = hist_row(day, k, c)
    for i in range(1, a.backfill + 1):
        d = day - timedelta(days=i)
        for k, b in AREAS.items():
            if (d.isoformat(), k) in hist:
                continue
            try:
                hist[(d.isoformat(), k)] = hist_row(d, k, area_counts(token, d, b))
            except Exception as e:
                print(f"backfill {d} {k} failed: {e}", file=sys.stderr)
        print(f"backfilled {d}", file=sys.stderr)
    with open(hist_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIST_FIELDS)
        w.writeheader()
        for key in sorted(hist):
            w.writerow({k: hist[key].get(k, "") for k in HIST_FIELDS})

if __name__ == "__main__":
    main()
