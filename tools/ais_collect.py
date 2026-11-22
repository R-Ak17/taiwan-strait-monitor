"""Collect a snapshot of ships in the Taiwan Strait from AISStream.

Connects to AISStream's WebSocket for a fixed window (default 180 s), keeps the
latest position of every vessel seen, merges in static data (name, type), and
writes:

  latest.geojson        one point per vessel, with flag, category, speed, course
  summary.json          counts per case-study area, by flag and by category
  counts_history.csv    one row per run per area: the time series for Phase 3/5
  vessels_static.json   names and types learned so far (static messages are
                        sent only every few minutes, so they are cached between runs)

Usage
  AISSTREAM_API_KEY=... python3 tools/ais_collect.py --out ais-out --seconds 180
  python3 tools/ais_collect.py --out ais-out --replay sample_messages.jsonl   # offline test

AISStream rules this script follows: the key stays server-side (never in the
browser), the subscription is sent within 3 seconds, messages are read
continuously, and the connection uses compression.
"""
import argparse, asyncio, csv, json, os, sys, time
from datetime import datetime, timezone

URL = "wss://stream.aisstream.io/v0/stream"
# One box over the whole strait, as [[lat, lon], [lat, lon]] (AISStream's order).
STRAIT_BOX = [[21.5, 116.5], [27.5, 122.5]]
# Case-study areas, the same rectangles as the map's study areas: (west, south, east, north).
AREAS = {
    "kinmen": (117.95, 24.30, 118.60, 24.65),
    "matsu": (119.80, 25.90, 120.56, 26.42),
    "penghu": (119.28, 23.15, 119.75, 23.80),
}
MESSAGE_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport",
                 "ShipStaticData", "StaticDataReport"]

# Flag from the MMSI's first three digits (Maritime Identification Digits, ITU).
# Hong Kong and Macao keep their own registries, so they are listed separately from the PRC.
MID = {
    "412": "PRC", "413": "PRC", "414": "PRC", "416": "Taiwan", "477": "Hong Kong", "453": "Macao",
    "351": "Panama", "352": "Panama", "353": "Panama", "354": "Panama", "355": "Panama", "356": "Panama",
    "357": "Panama", "370": "Panama", "371": "Panama", "372": "Panama", "373": "Panama", "374": "Panama",
    "636": "Liberia", "637": "Liberia", "538": "Marshall Islands", "563": "Singapore", "564": "Singapore",
    "565": "Singapore", "566": "Singapore", "431": "Japan", "432": "Japan", "440": "South Korea",
    "441": "South Korea", "548": "Philippines", "574": "Vietnam", "533": "Malaysia", "525": "Indonesia",
    "671": "Togo", "620": "Comoros", "667": "Sierra Leone", "577": "Vanuatu", "215": "Malta",
    "229": "Malta", "248": "Malta", "249": "Malta", "256": "Malta", "209": "Cyprus", "210": "Cyprus",
    "212": "Cyprus", "240": "Greece", "241": "Greece", "237": "Greece", "235": "United Kingdom",
    "232": "United Kingdom", "366": "United States", "367": "United States", "368": "United States",
    "369": "United States", "308": "Bahamas", "309": "Bahamas", "311": "Bahamas", "319": "Cayman Islands",
}

def flag_of(mmsi):
    s = str(mmsi)
    if len(s) != 9 or not s.isdigit():
        return "Unknown"
    return MID.get(s[:3], "Other")

def side_of(flag):
    # The map's colour code: Taiwan (navy), PRC (red), everyone else (grey).
    return "taiwan" if flag == "Taiwan" else "prc" if flag == "PRC" else "other"

def category_of(t):
    # AIS ship-type codes (ITU-R M.1371).
    if t is None:
        return "unknown"
    t = int(t)
    if t == 30: return "fishing"
    if t in (31, 32, 52): return "tug"
    if t == 35: return "military"
    if t == 55: return "law enforcement"
    if t in (36, 37): return "pleasure"
    if 40 <= t <= 49: return "high-speed craft"
    if 60 <= t <= 69: return "passenger"
    if 70 <= t <= 79: return "cargo"
    if 80 <= t <= 89: return "tanker"
    if t in (33, 34, 50, 51, 53, 54, 58, 59): return "service"
    if t == 0: return "unknown"
    return "other"

NAV_STATUS = {0: "under way using engine", 1: "at anchor", 2: "not under command", 3: "restricted manoeuvrability",
              4: "constrained by draught", 5: "moored", 6: "aground", 7: "engaged in fishing", 8: "under way sailing",
              14: "AIS-SART active", 15: "not defined"}

def area_of(lon, lat):
    for name, (w, s, e, n) in AREAS.items():
        if w <= lon <= e and s <= lat <= n:
            return name
    return "strait"

def pick(d, *keys):
    # AISStream's metadata has used both 'latitude' and 'Latitude'; accept either.
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None

class Snapshot:
    def __init__(self, static_cache):
        self.vessels = {}
        self.static = static_cache
        self.messages = 0

    def handle(self, msg):
        self.messages += 1
        mtype = msg.get("MessageType")
        meta = msg.get("MetaData", {}) or {}
        body = (msg.get("Message", {}) or {}).get(mtype, {}) or {}
        mmsi = str(pick(meta, "MMSI", "MMSI_String") or pick(body, "UserID") or "")
        if not mmsi:
            return
        if mtype in ("ShipStaticData", "StaticDataReport"):
            st = self.static.setdefault(mmsi, {})
            name = pick(body, "Name") or pick(body.get("ReportA", {}) if isinstance(body.get("ReportA"), dict) else {}, "Name")
            if name: st["name"] = name.strip()
            t = pick(body, "Type")
            if t is None and isinstance(body.get("ReportB"), dict):
                t = body["ReportB"].get("ShipType")
            if t is not None: st["type"] = int(t)
            for k_out, k_in in (("callsign", "CallSign"), ("destination", "Destination"), ("imo", "ImoNumber")):
                v = pick(body, k_in)
                if v not in (None, "", 0): st[k_out] = v.strip() if isinstance(v, str) else v
            dim = body.get("Dimension")
            if isinstance(dim, dict):
                st["length_m"] = (dim.get("A") or 0) + (dim.get("B") or 0)
            st["seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return
        lat = pick(body, "Latitude") if pick(body, "Latitude") is not None else pick(meta, "latitude", "Latitude")
        lon = pick(body, "Longitude") if pick(body, "Longitude") is not None else pick(meta, "longitude", "Longitude")
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return   # 91/181 mean "not available" in AIS
        sog, cog, hdg = pick(body, "Sog"), pick(body, "Cog"), pick(body, "TrueHeading")
        v = self.vessels.setdefault(mmsi, {"mmsi": mmsi})
        v.update({
            "lat": round(lat, 5), "lon": round(lon, 5),
            "sog": None if sog is None or sog >= 102.3 else round(sog, 1),
            "cog": None if cog is None or cog >= 360 else round(cog, 1),
            "heading": None if hdg is None or hdg == 511 else hdg,
            "nav": NAV_STATUS.get(pick(body, "NavigationalStatus")),
            "time_utc": str(pick(meta, "time_utc") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
            "class": "B" if "ClassB" in mtype else "A",
        })
        name = (pick(meta, "ShipName") or "").strip()
        if name:
            self.static.setdefault(mmsi, {}).setdefault("name", name)

    def features(self):
        out = []
        for mmsi, v in self.vessels.items():
            st = self.static.get(mmsi, {})
            flag = flag_of(mmsi)
            props = {
                "mmsi": mmsi, "name": st.get("name") or None, "flag": flag, "side": side_of(flag),
                "category": category_of(st.get("type")), "type_code": st.get("type"),
                "sog": v.get("sog"), "cog": v.get("cog"), "heading": v.get("heading"), "nav": v.get("nav"),
                "time_utc": v.get("time_utc"), "class": v.get("class"), "area": area_of(v["lon"], v["lat"]),
                "length_m": st.get("length_m"), "destination": st.get("destination"),
            }
            out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]}, "properties": props})
        return out

def summarise(features, started, seconds, messages):
    def count(fs):
        by_side, by_cat = {}, {}
        for f in fs:
            p = f["properties"]
            by_side[p["side"]] = by_side.get(p["side"], 0) + 1
            by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
        return {"vessels": len(fs), "by_side": by_side, "by_category": by_cat}
    areas = {a: count([f for f in features if f["properties"]["area"] == a]) for a in list(AREAS) + ["strait"]}
    return {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "window_started_utc": started,
            "window_seconds": seconds, "messages": messages, "vessels": len(features),
            "all": count(features), "areas": areas, "source": "AISStream (terrestrial AIS, volunteer receivers)"}

async def stream(snapshot, key, seconds):
    import websockets   # pip install websockets
    sub = {"APIKey": key, "BoundingBoxes": [STRAIT_BOX], "FilterMessageTypes": MESSAGE_TYPES}
    deadline = time.monotonic() + seconds
    attempts = 0
    while time.monotonic() < deadline and attempts < 4:
        attempts += 1
        try:
            async with websockets.connect(URL, compression="deflate", open_timeout=15, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))   # must arrive within 3 seconds of connecting
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    if "error" in msg:
                        raise RuntimeError("AISStream error: " + str(msg["error"]))
                    snapshot.handle(msg)
        except RuntimeError:
            raise
        except Exception as e:   # dropped connection: reconnect while time remains
            print(f"connection problem ({e}); retrying", file=sys.stderr)
            await asyncio.sleep(3)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ais-out")
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--replay", help="JSON-lines file of AISStream messages, for offline testing")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    static_path = os.path.join(a.out, "vessels_static.json")
    static = json.load(open(static_path)) if os.path.exists(static_path) else {}
    snap = Snapshot(static)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if a.replay:
        for line in open(a.replay):
            if line.strip():
                snap.handle(json.loads(line))
        seconds = 0
    else:
        key = os.environ.get("AISSTREAM_API_KEY")
        if not key:
            sys.exit("Set AISSTREAM_API_KEY (a GitHub Actions secret), never commit it.")
        asyncio.run(stream(snap, key, a.seconds))
        seconds = a.seconds

    feats = snap.features()
    summary = summarise(feats, started, seconds, snap.messages)
    json.dump({"type": "FeatureCollection", "generated_utc": summary["generated_utc"], "features": feats},
              open(os.path.join(a.out, "latest.geojson"), "w"), separators=(",", ":"), ensure_ascii=False)
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(static, open(static_path, "w"), separators=(",", ":"), ensure_ascii=False)

    hist = os.path.join(a.out, "counts_history.csv")
    new = not os.path.exists(hist)
    with open(hist, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["generated_utc", "area", "vessels", "taiwan", "prc", "other", "cargo", "tanker", "passenger",
                        "fishing", "law_enforcement", "military", "unknown"])
        for area, c in summary["areas"].items():
            s, k = c["by_side"], c["by_category"]
            w.writerow([summary["generated_utc"], area, c["vessels"], s.get("taiwan", 0), s.get("prc", 0), s.get("other", 0),
                        k.get("cargo", 0), k.get("tanker", 0), k.get("passenger", 0), k.get("fishing", 0),
                        k.get("law enforcement", 0), k.get("military", 0), k.get("unknown", 0)])
    print(f"{snap.messages} messages, {len(feats)} vessels: " +
          ", ".join(f"{k} {v['vessels']}" for k, v in summary["areas"].items()))

if __name__ == "__main__":
    main()
