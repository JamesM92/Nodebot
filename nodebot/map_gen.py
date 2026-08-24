import glob as _glob
import os
import json
import sqlite3
import time
import urllib.request

import re as _re

_storage_path      = None
_nomad_files       = os.path.expanduser("~/.nomadnetwork/storage/files")
_MAP_SUBPATH        = "nodebot/map.png"
_PATH_MAP_SUBPATH   = "nodebot/map_paths.png"
_COUNTY_MAP_SUBPATH = "nodebot/county_map.png"
_STATES_URL        = ("https://raw.githubusercontent.com/PublicaMundi/"
                      "MappingAPI/master/data/geojson/us-states.json")
_COUNTIES_URL      = ("https://raw.githubusercontent.com/plotly/datasets/"
                      "master/geojson-counties-fips.json")
_MAP_MAX_AGE       = 300   # seconds before cached PNG is stale

_PATH_RE = _re.compile(r'Path:\s*([0-9a-fA-F]{2,8}(?:,[0-9a-fA-F]{2,8})+)')

# Major US cities for reference labels
_CITIES = [
    ("Atlanta",        33.749,  -84.388),
    ("Charlotte",      35.227,  -80.843),
    ("Nashville",      36.162,  -86.781),
    ("Raleigh",        35.779,  -78.638),
    ("Richmond",       37.541,  -77.434),
    ("Columbia SC",    34.000,  -81.035),
    ("Knoxville",      35.961,  -83.921),
    ("Asheville",      35.595,  -82.551),
    ("Greensboro",     36.073,  -79.792),
    ("Greenville SC",  34.852,  -82.394),
    ("Chattanooga",    35.045,  -85.309),
    ("Huntsville",     34.730,  -86.586),
    ("Birmingham",     33.521,  -86.803),
    ("Memphis",        35.149,  -90.048),
    ("Louisville",     38.252,  -85.758),
    ("Lexington KY",   38.040,  -84.503),
    ("Cincinnati",     39.103,  -84.512),
    ("Columbus OH",    39.961,  -82.999),
    ("Charleston WV",  38.350,  -81.633),
    ("Virginia Beach", 36.853,  -75.978),
    ("Washington DC",  38.907,  -77.037),
    ("Baltimore",      39.290,  -76.612),
    ("Pittsburgh",     40.440,  -79.996),
    ("Jacksonville",   30.332,  -81.656),
    ("Tampa",          27.948,  -82.458),
    ("Orlando",        28.538,  -81.379),
    ("Miami",          25.775,  -80.208),
    ("New Orleans",    29.951,  -90.071),
    ("Mobile",         30.694,  -88.043),
    ("Montgomery",     32.361,  -86.279),
    ("Savannah",       32.081,  -81.100),
    ("Augusta GA",     33.474,  -81.975),
    ("Macon",          32.841,  -83.633),
    ("Indianapolis",   39.768,  -86.158),
    ("Chicago",        41.878,  -87.630),
    ("Detroit",        42.331,  -83.046),
    ("St. Louis",      38.627,  -90.198),
    ("Kansas City",    39.099,  -94.578),
    ("Dallas",         32.780,  -96.800),
    ("Houston",        29.760,  -95.370),
    ("San Antonio",    29.424,  -98.494),
    ("Austin",         30.267,  -97.743),
    ("Oklahoma City",  35.467,  -97.517),
    ("Tulsa",          36.154,  -95.993),
    ("Little Rock",    34.746,  -92.290),
    ("Jackson MS",     32.299,  -90.185),
    ("Philadelphia",   39.952,  -75.164),
    ("New York",       40.713,  -74.006),
    ("Boston",         42.360,  -71.058),
]


def init(storage_path, config=None):
    global _storage_path
    _storage_path = storage_path
    geo_dir = os.path.join(storage_path, "geodata")
    os.makedirs(geo_dir, exist_ok=True)

    # Ensure placeholders exist in NomadNet files dir so they're registered at startup
    map_path = os.path.join(_nomad_files, _MAP_SUBPATH)
    if not os.path.isfile(map_path):
        _write_placeholder(map_path)

    path_map_path = os.path.join(_nomad_files, _PATH_MAP_SUBPATH)
    if not os.path.isfile(path_map_path):
        _write_placeholder(path_map_path, "NodeBot path map")

    county_map_path = os.path.join(_nomad_files, _COUNTY_MAP_SUBPATH)
    if not os.path.isfile(county_map_path):
        _write_placeholder(county_map_path, "NodeBot county map")


def map_output_path():
    return os.path.join(_nomad_files, _MAP_SUBPATH)


def map_age():
    """Return age in seconds of the cached map, or None if not generated yet."""
    path = map_output_path()
    if not os.path.isfile(path):
        return None
    age = time.time() - os.path.getmtime(path)
    # Placeholder has 0 bytes; treat as not generated
    if os.path.getsize(path) < 1024:
        return None
    return age


def _write_placeholder(path, label="NodeBot node map"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 400), color=(26, 26, 46))
        draw = ImageDraw.Draw(img)
        draw.text((200, 185), label, fill=(100, 100, 150))
        draw.text((215, 205), "Visit the .mu page to generate", fill=(70, 70, 100))
        img.save(path, "PNG")
    except Exception:
        # Fallback: minimal valid 1×1 PNG
        import struct
        import zlib
        def _chunk(tag, data):
            c = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)
        png = (b"\x89PNG\r\n\x1a\n"
               + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
               + _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
               + _chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)


def _states_geojson():
    path = os.path.join(_storage_path, "geodata", "us_states.json")
    if not os.path.isfile(path):
        print(f"[map_gen] downloading state boundaries to {path}")
        urllib.request.urlretrieve(_STATES_URL, path + ".tmp")  # nosec B310 — hardcoded HTTPS URL
        os.replace(path + ".tmp", path)
    with open(path) as f:
        return json.load(f)


def _counties_geojson():
    """Download (once) and return US county boundary GeoJSON (~12 MB, cached)."""
    path = os.path.join(_storage_path, "geodata", "us_counties.json")
    if not os.path.isfile(path):
        print(f"[map_gen] downloading county boundaries to {path}")
        urllib.request.urlretrieve(_COUNTIES_URL, path + ".tmp")  # nosec B310 — hardcoded HTTPS URL
        os.replace(path + ".tmp", path)
    with open(path) as f:
        return json.load(f)


def _pip_ring(px, py, ring):
    """Ray-casting point-in-polygon for one GeoJSON ring [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _read_node_activity(announce_db_files, days=7):
    """Return dict addr → (avg_rssi_or_None, announce_count_last_N_days)."""
    cutoff = time.time() - days * 86400
    result = {}
    for f in announce_db_files:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for addr, rssi_avg, recent in conn.execute("""
                SELECT addr,
                       AVG(CASE WHEN rssi IS NOT NULL THEN rssi END),
                       SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END)
                FROM announces GROUP BY addr
            """, (cutoff,)).fetchall():
                a = addr.lower()
                prev_rssi, prev_cnt = result.get(a, (None, 0))
                result[a] = (
                    rssi_avg if rssi_avg is not None else prev_rssi,
                    prev_cnt + (recent or 0),
                )
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return result


def _read_node_avg_hops(announce_db_files):
    """Return dict addr → avg_hops (float) across all announces that have a hop count."""
    result = {}
    for f in announce_db_files:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for addr, avg_h in conn.execute(
                "SELECT addr, AVG(hops) FROM announces "
                "WHERE hops IS NOT NULL GROUP BY addr"
            ).fetchall():
                a = addr.lower()
                if avg_h is not None:
                    # Running mean across multiple protocol DBs
                    result[a] = (result[a] + avg_h) / 2 if a in result else avg_h
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return result


def _active_lookback_days(announce_db_files, target_days=7, max_lookback=90):
    """Return the calendar-day span needed to capture *target_days* days that
    have at least one announce record, skipping over outage gaps.

    Example: 7 target days + 3-day outage → returns 10.
    Falls back to *target_days* if the DB has no history yet.
    """
    from datetime import datetime as _dt

    cutoff = time.time() - max_lookback * 86400
    active_dates = set()
    for f in announce_db_files:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for (d,) in conn.execute(
                "SELECT DISTINCT date(ts, 'unixepoch') FROM announces WHERE ts >= ?",
                (cutoff,),
            ).fetchall():
                active_dates.add(d)
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    if not active_dates:
        return target_days

    sorted_dates = sorted(active_dates, reverse=True)  # newest first
    # Pick the target_days-th most recent active date as our cutoff
    oldest = sorted_dates[min(target_days - 1, len(sorted_dates) - 1)]
    oldest_dt = _dt.strptime(oldest, "%Y-%m-%d")
    days = (_dt.utcnow() - oldest_dt).days + 1
    return max(days, target_days)


def _path_stability_scores(paths_db_path):
    """Return dict frozenset({pa, pb}) → stability ∈ [0, 1].

    Stability = 1 means every path that uses this hop is always the same full
    route.  Near 0 means many different route strings pass through the hop
    (path flapping).
    """
    if not paths_db_path or not os.path.isfile(paths_db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{paths_db_path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT path_str, sender_id, count FROM paths WHERE path_str IS NOT NULL"
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    by_sender = {}
    for path_str, sender_id, count in rows:
        sid = (sender_id or "").lower()
        pc = by_sender.setdefault(sid, {})
        pc[path_str] = pc.get(path_str, 0) + (count or 1)

    sender_stability = {
        sid: max(pc.values()) / sum(pc.values())
        for sid, pc in by_sender.items()
        if sum(pc.values()) > 0
    }

    edge_stabs = {}
    for sid, path_counts in by_sender.items():
        stab = sender_stability.get(sid, 1.0)
        for path_str in path_counts:
            segs = [s.lower().strip() for s in path_str.split(",") if s.strip()]
            for i in range(len(segs) - 1):
                edge = frozenset([segs[i], segs[i + 1]])
                edge_stabs.setdefault(edge, []).append(stab)

    return {
        edge: sum(stabs) / len(stabs)
        for edge, stabs in edge_stabs.items()
    }


def _relay_centrality(all_paths, path_node_coords):
    """Betweenness centrality for every node appearing in a path.

    Returns dict node_id → score ∈ [0, 1].  Empty dict if networkx is
    unavailable or there are fewer than 2 nodes.
    """
    try:
        import networkx as nx
    except ImportError:
        return {}

    G = nx.Graph()
    for path_ids in all_paths:
        resolved = [pid for pid in path_ids if pid in path_node_coords]
        for i in range(len(resolved) - 1):
            pa, pb = resolved[i], resolved[i + 1]
            if pa != pb:
                if G.has_edge(pa, pb):
                    G[pa][pb]["weight"] = G[pa][pb]["weight"] + 1
                else:
                    G.add_edge(pa, pb, weight=1)

    if len(G.nodes) < 2:
        return {}

    return nx.betweenness_centrality(G, normalized=True, weight="weight")


def _announce_db_files(announce_db):
    """Return list of announce DB file paths to query.

    Prefers per-protocol DBs (announces_*.db) alongside the configured path.
    Falls back to the configured path itself if no per-proto DBs exist.
    """
    if not announce_db:
        return []
    d = announce_db if os.path.isdir(announce_db) else os.path.dirname(announce_db)
    if os.path.isdir(d):
        proto_dbs = sorted(_glob.glob(os.path.join(d, "announces_*.db")))
        if proto_dbs:
            return proto_dbs
    if os.path.isfile(announce_db):
        return [announce_db]
    return []


def _read_gps_nodes(announce_db):
    """Return (proto, addr, nick, lat, lon) for every GPS node.

    lat/lon come from position_estimates (weighted running average) when
    available, falling back to the latest raw GPS from announces.  nick
    is always taken from announces.
    """
    files = _announce_db_files(announce_db)
    if not files:
        return []
    seen = {}  # addr → (proto, addr, nick, lat, lon)
    for f in files:
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)

            # Raw GPS from announces — provides nick and fallback coords
            ann = {}
            for proto, addr, nick, lat, lon in conn.execute("""
                SELECT proto, addr, nick, lat, lon FROM announces
                WHERE lat IS NOT NULL AND lon IS NOT NULL
                GROUP BY addr ORDER BY MAX(ts) DESC
            """).fetchall():
                ann[addr.lower()] = (proto, addr, nick, lat, lon)

            # Override lat/lon with weighted position estimates when available
            try:
                for addr, proto, lat, lon in conn.execute(
                    "SELECT addr, proto, lat, lon FROM position_estimates"
                ).fetchall():
                    a = addr.lower()
                    if a in ann:
                        _p, _a, nick, _lat, _lon = ann[a]
                        ann[a] = (_p, _a, nick, lat, lon)
                    else:
                        ann[a] = (proto, addr, None, lat, lon)
            except sqlite3.OperationalError:
                pass  # table not yet created in this DB

            conn.close()
            for a, row in ann.items():
                if a not in seen:
                    seen[a] = row
        except Exception:
            pass
    return list(seen.values())


def lockfile_path():
    return os.path.join(_storage_path, "map_generating.lock")


def is_generating():
    lf = lockfile_path()
    if not os.path.isfile(lf):
        return False
    # Treat lock as stale after 120s (crashed worker)
    return (time.time() - os.path.getmtime(lf)) < 120


def generate_async(announce_db):
    """Launch map generation in a detached background subprocess. Returns immediately."""
    import subprocess
    lf = lockfile_path()
    worker = os.path.join(os.path.dirname(__file__), "map_gen_worker.py")
    venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               ".venv", "bin", "python3")
    if not os.path.isfile(venv_python):
        import sys
        venv_python = sys.executable
    with open(lf, "w") as f:
        f.write("")
    subprocess.Popen(
        [venv_python, worker, announce_db, _storage_path, lf],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate(announce_db, days=7):
    """Generate the node map PNG. Returns (node_count, stats_dict)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe

    nodes = _read_gps_nodes(announce_db)
    if not nodes:
        return 0, {}

    # Activity and RSSI per node — used for county heatmap and dot sizing
    announce_files = _announce_db_files(announce_db)
    node_activity  = _read_node_activity(announce_files, days=days)

    lats = [r[3] for r in nodes]
    lons = [r[4] for r in nodes]

    # Filter outliers: keep nodes within 3 standard deviations of mean position
    import statistics
    if len(lats) >= 4:
        mean_lat, std_lat = statistics.mean(lats), statistics.stdev(lats)
        mean_lon, std_lon = statistics.mean(lons), statistics.stdev(lons)
        nodes = [r for r in nodes
                 if abs(r[3] - mean_lat) <= 3 * std_lat
                 and abs(r[4] - mean_lon) <= 3 * std_lon]
    if not nodes:
        return 0, {}

    lats = [r[3] for r in nodes]
    lons = [r[4] for r in nodes]

    pad_lat = max(1.5, (max(lats) - min(lats)) * 0.12)
    pad_lon = max(1.5, (max(lons) - min(lons)) * 0.12)
    lat_min = min(lats) - pad_lat
    lat_max = max(lats) + pad_lat
    lon_min = min(lons) - pad_lon
    lon_max = max(lons) + pad_lon

    fig, ax = plt.subplots(figsize=(14, 9), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")

    # State outlines
    try:
        geojson = _states_geojson()
        for feature in geojson["features"]:
            geom = feature["geometry"]
            polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                     else [geom["coordinates"]])
            for mpoly in polys:
                rings = mpoly if isinstance(mpoly[0][0], list) else [mpoly]
                for ring in rings:
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    ax.plot(xs, ys, color="#5a5a88", linewidth=1.2, zorder=7)
    except Exception as e:
        print(f"[map_gen] state outlines unavailable: {e}")

    # ── County density fill — node count per county ───────────────────────────
    # Teal fill alpha = node count.  RSSI and activity breakdowns live on the
    # dedicated county overview map (county_map.png / county.mu).
    try:
        MARGIN = 0.5
        counties = _counties_geojson()
        features = counties.get("features", [])

        vp_features = []
        for idx, feat in enumerate(features):
            geom  = feat.get("geometry", {})
            gtype = geom.get("type", "")
            raw   = geom.get("coordinates", [])
            outer = (raw[0] if gtype == "Polygon" and raw
                     else (raw[0][0] if gtype == "MultiPolygon" and raw and raw[0]
                           else []))
            n_v = len(outer)
            sample = outer[::max(1, n_v // 8)] if n_v else []
            if any(lon_min - MARGIN <= c[0] <= lon_max + MARGIN and
                   lat_min - MARGIN <= c[1] <= lat_max + MARGIN
                   for c in sample):
                vp_features.append((idx, feat))

        # Pre-compute county bounding boxes once — avoids recomputing for every node
        vp_polys_node = []
        for idx, feat in vp_features:
            geom  = feat.get("geometry", {})
            gtype = geom.get("type", "")
            raw   = geom.get("coordinates", [])
            polys = [raw] if gtype == "Polygon" else (raw if gtype == "MultiPolygon" else [])
            rings = []
            for poly in polys:
                if not poly:
                    continue
                outer = poly[0]
                rings.append((
                    min(c[0] for c in outer), max(c[0] for c in outer),
                    min(c[1] for c in outer), max(c[1] for c in outer),
                    outer,
                ))
            if rings:
                vp_polys_node.append((idx, rings))

        county_count = {}
        for _proto, _addr, _nick, _lat, _lon in nodes:
            for idx, rings in vp_polys_node:
                hit = False
                for bl, br, bb, bt, outer in rings:
                    if bl <= _lon <= br and bb <= _lat <= bt and _pip_ring(_lon, _lat, outer):
                        hit = True
                        break
                if hit:
                    county_count[idx] = county_count.get(idx, 0) + 1
                    break

        # Pre-extract drawing vertex arrays
        from matplotlib.collections import PolyCollection as _PolyC
        _draw_rings_node = [
            (idx, [(c[0], c[1]) for c in outer])
            for idx, rings in vp_polys_node
            for _bl, _br, _bb, _bt, outer in rings
        ]

        # Faint county grid — all viewport counties, borders only
        _all_verts = [xy for _, xy in _draw_rings_node]
        ax.add_collection(_PolyC(_all_verts, facecolors="none",
                                 edgecolors="#556677", linewidths=0.3,
                                 alpha=0.18, zorder=1))

        if county_count:
            max_count = max(county_count.values()) or 1
            _fill_verts  = []
            _fill_colors = []
            _edge_verts  = []
            for idx, xy in _draw_rings_node:
                if idx not in county_count:
                    continue
                fill_alpha = 0.10 + 0.55 * (county_count[idx] / max_count)
                _fill_verts.append(xy)
                _fill_colors.append((0x33 / 255, 0x88 / 255, 0xbb / 255, fill_alpha))
                _edge_verts.append(xy)
            ax.add_collection(_PolyC(_fill_verts, facecolors=_fill_colors,
                                     edgecolors="none", zorder=1))
            ax.add_collection(_PolyC(_edge_verts, facecolors="none",
                                     edgecolors="#3388bb", linewidths=0.3,
                                     alpha=0.35, zorder=1))
    except Exception as _e:
        print(f"[map_gen] county density fill unavailable: {_e}")

    # ── Node dots — size ∝ recent activity ───────────────────────────────────
    # Batch scatter by protocol colour: one PathCollection per group instead
    # of 1 per node (which forces matplotlib to rasterise thousands of objects).
    _PROTO_COLORS = {
        "meshcore":   "#00ccff",
        "meshtastic": "#44ff88",
        "lxmf":       "#ffaa44",
        "mc":         "#00ccff",
        "mesh":       "#44ff88",
    }
    max_act_all = max(
        (node_activity.get(r[1].lower(), (None, 0))[1] for r in nodes),
        default=1,
    ) or 1

    from collections import defaultdict as _dd
    _groups: dict = _dd(lambda: {"lons": [], "lats": [], "sizes": []})
    _labels = []  # (lon, lat, addr_prefix, activity)
    for proto, addr, nick, lat, lon in nodes:
        color = _PROTO_COLORS.get(proto, "#aaaaaa")
        act   = node_activity.get(addr.lower(), (None, 0))[1]
        size  = 7 + 63 * (act / max_act_all)
        _groups[color]["lons"].append(lon)
        _groups[color]["lats"].append(lat)
        _groups[color]["sizes"].append(size)
        _labels.append((lon, lat, addr[:4], act))

    for color, data in _groups.items():
        ax.scatter(data["lons"], data["lats"], s=data["sizes"],
                   color=color, zorder=4, alpha=0.9,
                   linewidths=0.3, edgecolors="#ffffff40")

    # Node address labels — only label the most-active nodes to keep render
    # time reasonable (1776 text objects at 150 DPI ≈ 20 s in savefig).
    _labels.sort(key=lambda x: x[3], reverse=True)
    for lon, lat, tag, _act in _labels[:200]:
        ax.text(lon + 0.04, lat, tag, fontsize=4,
                color="#ccccdd", va="center", zorder=5, alpha=0.85)

    # City reference labels
    for city, clat, clon in _CITIES:
        if lat_min <= clat <= lat_max and lon_min <= clon <= lon_max:
            ax.scatter(clon, clat, s=8, color="#555577", marker="s", zorder=8)
            txt = ax.text(clon + 0.06, clat, city, fontsize=4.5, color="#555577",
                          va="center", zorder=8, style="italic")
            txt.set_path_effects([pe.withStroke(linewidth=1.2, foreground="#1a1a2e")])

    # ── Legend ───────────────────────────────────────────────────────────────
    import matplotlib.patches as _mp
    _lh = [
        # — Protocols —
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#00ccff",
                   markersize=7, linestyle="None", label="MeshCore node"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#44ff88",
                   markersize=7, linestyle="None", label="Meshtastic node"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#ffaa44",
                   markersize=7, linestyle="None", label="LXMF node"),
        # — Node size —
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#778899",
                   markersize=4, linestyle="None", label="● small = low activity"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#778899",
                   markersize=9, linestyle="None", label="● large = high activity"),
        # — County density fill —
        _mp.Patch(facecolor="#3388bb", alpha=0.15, edgecolor="none",
                  label="county: few nodes"),
        _mp.Patch(facecolor="#3388bb", alpha=0.65, edgecolor="none",
                  label="county: many nodes"),
    ]
    ax.legend(handles=_lh, loc="lower right", fontsize=6.5,
              facecolor="#12122a", edgecolor="#3a3a5c", labelcolor="#ccccdd",
              framealpha=0.88, handlelength=1.8, borderpad=0.8,
              labelspacing=0.45)

    _add_scale_bar(ax, lat_min, lat_max, lon_min, lon_max)

    gen_time = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    ax.set_title(f"NodeBot — {len(nodes)} GPS nodes — {gen_time}",
                 color="#9999bb", fontsize=9, pad=6)
    ax.tick_params(colors="#44445a", labelsize=5.5, length=3)
    for spine in ax.spines.values():
        spine.set_edgecolor("#3a3a5c")

    plt.tight_layout(pad=0.4)
    out = map_output_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    return len(nodes), {
        "lat_min": round(lat_min, 4), "lat_max": round(lat_max, 4),
        "lon_min": round(lon_min, 4), "lon_max": round(lon_max, 4),
    }


# ── Path map ──────────────────────────────────────────────────


def path_map_output_path():
    return os.path.join(_nomad_files, _PATH_MAP_SUBPATH)


def path_map_age():
    path = path_map_output_path()
    if not os.path.isfile(path):
        return None
    if os.path.getsize(path) < 1024:
        return None
    return time.time() - os.path.getmtime(path)


def path_map_lockfile_path():
    return os.path.join(_storage_path, "map_paths_generating.lock")


def is_generating_paths():
    lf = path_map_lockfile_path()
    if not os.path.isfile(lf):
        return False
    return (time.time() - os.path.getmtime(lf)) < 120


def generate_path_map_async(announce_db):
    """Launch path map generation in a detached background subprocess."""
    import subprocess
    lf = path_map_lockfile_path()
    worker = os.path.join(os.path.dirname(__file__), "map_gen_worker.py")
    venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               ".venv", "bin", "python3")
    if not os.path.isfile(venv_python):
        import sys
        venv_python = sys.executable
    with open(lf, "w") as f:
        f.write("")
    subprocess.Popen(
        [venv_python, worker, announce_db, _storage_path, lf, "paths"],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def county_map_output_path():
    return os.path.join(_nomad_files, _COUNTY_MAP_SUBPATH)


def county_map_age():
    path = county_map_output_path()
    if not os.path.isfile(path):
        return None
    if os.path.getsize(path) < 1024:
        return None
    return time.time() - os.path.getmtime(path)


def county_map_lockfile_path():
    return os.path.join(_storage_path, "county_map_generating.lock")


def is_generating_county():
    lf = county_map_lockfile_path()
    if not os.path.isfile(lf):
        return False
    return (time.time() - os.path.getmtime(lf)) < 120


def generate_county_map_async(announce_db):
    """Launch county map generation in a detached background subprocess."""
    import subprocess
    lf = county_map_lockfile_path()
    worker = os.path.join(os.path.dirname(__file__), "map_gen_worker.py")
    venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               ".venv", "bin", "python3")
    if not os.path.isfile(venv_python):
        import sys
        venv_python = sys.executable
    with open(lf, "w") as f:
        f.write("")
    subprocess.Popen(
        [venv_python, worker, announce_db, _storage_path, lf, "county"],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate_county_map(announce_db, days=7):
    """Generate a 3-panel county overview PNG: lifetime node count, N-day activity, avg hops.

    Each panel uses the same county boundaries and map extent.  Panels are
    designed for side-by-side comparison so anomalies (e.g. high activity but
    low node count) jump out without having to decode overlaid encodings.

    Panel 1 — Node Count: all GPS nodes ever seen (lifetime).
    Panel 2 — Activity: announce count over the last N days (default 7).
    Panel 3 — Avg Hops: all-time average hop count to reach each county,
               green (direct / low hops) → red (many hops / deep in mesh).
    """
    import statistics
    import matplotlib
    import matplotlib.colors as mcolors
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as _mp

    ann_files     = _announce_db_files(announce_db)
    node_activity = _read_node_activity(ann_files, days=days)
    node_hops     = _read_node_avg_hops(ann_files)
    all_gps_nodes = _read_gps_nodes(announce_db)

    if not all_gps_nodes:
        return 0

    # 3σ outlier filter (same as node map)
    lats = [r[3] for r in all_gps_nodes]
    lons = [r[4] for r in all_gps_nodes]
    if len(lats) >= 4:
        mlat = statistics.mean(lats); slat = statistics.stdev(lats)
        mlon = statistics.mean(lons); slon = statistics.stdev(lons)
        all_gps_nodes = [r for r in all_gps_nodes
                         if abs(r[3] - mlat) <= 3 * slat
                         and abs(r[4] - mlon) <= 3 * slon]
    if not all_gps_nodes:
        return 0

    lats = [r[3] for r in all_gps_nodes]
    lons = [r[4] for r in all_gps_nodes]
    PAD = 1.5
    lat_min = min(lats) - PAD;  lat_max = max(lats) + PAD
    lon_min = min(lons) - PAD;  lon_max = max(lons) + PAD

    # ── Load counties and pre-filter to viewport ──────────────────────────────
    counties    = _counties_geojson()
    vp_features = []
    MARGIN      = 0.5
    for idx, feat in enumerate(counties.get("features", [])):
        geom  = feat.get("geometry", {})
        gtype = geom.get("type", "")
        raw   = geom.get("coordinates", [])
        outer = (raw[0] if gtype == "Polygon" and raw
                 else (raw[0][0] if gtype == "MultiPolygon" and raw and raw[0] else []))
        n_v    = len(outer)
        sample = outer[::max(1, n_v // 8)] if n_v else []
        if any(lon_min - MARGIN <= c[0] <= lon_max + MARGIN and
               lat_min - MARGIN <= c[1] <= lat_max + MARGIN
               for c in sample):
            vp_features.append((idx, feat))

    # ── Pre-compute county polygon bounding boxes (done once, not per node) ──
    # Each entry: (idx, [(bl, br, bb, bt, outer_ring), ...])
    vp_polys = []
    for idx, feat in vp_features:
        geom  = feat.get("geometry", {})
        gtype = geom.get("type", "")
        raw   = geom.get("coordinates", [])
        polys = [raw] if gtype == "Polygon" else (raw if gtype == "MultiPolygon" else [])
        rings = []
        for poly in polys:
            if not poly:
                continue
            outer = poly[0]
            rings.append((
                min(c[0] for c in outer),  # bl
                max(c[0] for c in outer),  # br
                min(c[1] for c in outer),  # bb
                max(c[1] for c in outer),  # bt
                outer,
            ))
        if rings:
            vp_polys.append((idx, rings))

    # ── Assign nodes to counties ──────────────────────────────────────────────
    county_data = {}   # idx → {count, activity, hops: []}
    for _proto, _addr, _nick, _lat, _lon in all_gps_nodes:
        al = _addr.lower()
        _, act_cnt = node_activity.get(al, (None, 0))
        avg_hops   = node_hops.get(al)
        for idx, rings in vp_polys:
            hit = False
            for bl, br, bb, bt, outer in rings:
                if bl <= _lon <= br and bb <= _lat <= bt and _pip_ring(_lon, _lat, outer):
                    hit = True
                    break
            if hit:
                d = county_data.setdefault(idx, {"count": 0, "activity": 0, "hops": []})
                d["count"]    += 1
                d["activity"] += act_cnt or 0
                if avg_hops is not None:
                    d["hops"].append(avg_hops)
                break

    if not county_data:
        return 0

    max_count = max(d["count"]    for d in county_data.values()) or 1
    max_act   = max(d["activity"] for d in county_data.values()) or 1
    # Hops colormap: green (0 hops = direct) → orange → red (deep in mesh)
    HOPS_VMIN = 0.0
    HOPS_VMAX = float(max(
        round(max(
            (sum(d["hops"]) / len(d["hops"]) for d in county_data.values() if d["hops"]),
            default=6.0,
        )),
        1,  # floor at 1 so the colour scale is never degenerate
    ))
    HOPS_CMAP = mcolors.LinearSegmentedColormap.from_list(
        "hops_county", ["#22cc66", "#ffaa00", "#cc2211"])

    # ── Figure: 3 panels side by side ────────────────────────────────────────
    from matplotlib.collections import PolyCollection as _PolyC
    _NO_DATA_RGBA = (0x22 / 255, 0x22 / 255, 0x3a / 255, 1.0)

    fig, axes = plt.subplots(1, 3, figsize=(21, 7), facecolor="#1a1a2e")
    fig.subplots_adjust(left=0.01, right=0.97, top=0.90, bottom=0.03, wspace=0.04)

    PANELS = [
        {"title": "Node Count  (lifetime)",   "color": "#3388bb", "metric": "count"},
        {"title": f"Activity  ({days} d)",    "color": "#cc8822", "metric": "activity"},
        {"title": "Avg Hops  (lifetime)",     "color": None,      "metric": "hops"},
    ]

    def _val(d, metric):
        if metric == "count":
            return d["count"] / max_count
        if metric == "activity":
            return d["activity"] / max_act
        # hops — normalised so the observed max maps to 1.0
        if not d["hops"]:
            return None
        avg = sum(d["hops"]) / len(d["hops"])
        return (avg - HOPS_VMIN) / max(HOPS_VMAX - HOPS_VMIN, 0.001)

    # Pre-extract drawing vertex arrays from vp_polys (already computed above).
    # One entry per outer ring: (county_idx, xy_pairs)
    _draw_rings = [
        (idx, [(c[0], c[1]) for c in outer])
        for idx, rings in vp_polys
        for _bl, _br, _bb, _bt, outer in rings
    ]

    for pi, (ax, panel) in enumerate(zip(axes, PANELS)):
        ax.set_facecolor("#1a1a2e")
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xticks([]);  ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_edgecolor("#3a3a5c")
        ax.set_title(panel["title"], color="#ccccdd", fontsize=12, pad=5,
                     fontweight="bold")

        # Build per-polygon RGBA fill colours then draw the whole panel in two
        # PolyCollection calls (fills + borders) instead of one ax.fill/ax.plot
        # per county — dramatically faster for 1000+ polygons.
        _verts  = []
        _fcolors = []
        for idx, xy in _draw_rings:
            d = county_data.get(idx)
            v = _val(d, panel["metric"]) if d else None
            _verts.append(xy)
            if v is None:
                _fcolors.append(_NO_DATA_RGBA)
            elif panel["color"]:
                r, g, b = mcolors.to_rgb(panel["color"])
                _fcolors.append((r, g, b, 0.08 + 0.80 * v))
            else:
                rgba = HOPS_CMAP(max(0.0, min(1.0, v)))
                _fcolors.append((rgba[0], rgba[1], rgba[2], 0.85))

        ax.add_collection(_PolyC(_verts, facecolors=_fcolors,
                                 edgecolors="none", zorder=1))
        ax.add_collection(_PolyC(_verts, facecolors="none",
                                 edgecolors="#3a3a58", linewidths=0.25,
                                 alpha=0.6, zorder=2))

        # State borders
        try:
            states = _states_geojson()
            for sfeat in states.get("features", []):
                sgeom  = sfeat.get("geometry", {})
                sgtype = sgeom.get("type", "")
                sraw   = sgeom.get("coordinates", [])
                coords_list = ([sraw] if sgtype == "Polygon"
                               else (sraw if sgtype == "MultiPolygon" else []))
                for mpoly in coords_list:
                    rings = mpoly if isinstance(mpoly[0][0], list) else [mpoly]
                    for ring in rings:
                        ax.plot([p[0] for p in ring], [p[1] for p in ring],
                                color="#5a5a88", linewidth=0.8, zorder=3)
        except Exception:
            pass

        # City reference dots and labels
        for city, clat, clon in _CITIES:
            if lat_min <= clat <= lat_max and lon_min <= clon <= lon_max:
                ax.scatter(clon, clat, s=4, color="#444466", marker="s", zorder=4)
                ax.text(clon + 0.06, clat, city, fontsize=3.2, color="#444466",
                        va="center", zorder=4, style="italic")

        # Legend / colorbar
        if panel["color"]:
            lh = [
                _mp.Patch(facecolor=panel["color"], alpha=0.12,
                          edgecolor="none", label="low"),
                _mp.Patch(facecolor=panel["color"], alpha=0.88,
                          edgecolor="none", label="high"),
                _mp.Patch(facecolor="#22223a", alpha=1.0,
                          edgecolor="#3a3a58", linewidth=0.5, label="no data"),
            ]
            ax.legend(handles=lh, loc="lower right", fontsize=6.5,
                      facecolor="#12122a", edgecolor="#3a3a5c",
                      labelcolor="#ccccdd", framealpha=0.88)
        else:
            sm = plt.cm.ScalarMappable(
                cmap=HOPS_CMAP,
                norm=mcolors.Normalize(vmin=HOPS_VMIN, vmax=HOPS_VMAX))
            sm.set_array([])
            cb = fig.colorbar(sm, ax=ax, orientation="vertical",
                              fraction=0.025, pad=0.02, shrink=0.55)
            cb.set_label("hops", color="#ccccdd", fontsize=7)
            cb.ax.yaxis.set_tick_params(color="#ccccdd")
            plt.setp(plt.getp(cb.ax.axes, "yticklabels"),
                     color="#ccccdd", fontsize=6)
            ax.text(0.02, 0.02, "dark = no hop data  •  green = direct  •  red = deep mesh",
                    transform=ax.transAxes, fontsize=5.5, color="#666688",
                    va="bottom", ha="left")

        if pi == 0:
            _add_scale_bar(ax, lat_min, lat_max, lon_min, lon_max)

    gen_time = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    fig.suptitle(f"NodeBot — County Overview  •  {gen_time}",
                 color="#9999cc", fontsize=11, y=0.97)

    out = county_map_output_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return len(county_data)


def _parse_paths_from_pathsdb(since=0):
    """Read paths from paths.db seen within the given time window.

    ``since`` is a Unix timestamp; only rows with last_seen >= since are
    returned (0 = no filter, return all).  Returns full paths including
    the sender endpoint (prepended) and our bot node (appended) when
    available, giving the complete hop chain:
      [sender] → relay1 → … → relayN → [our_node]
    """
    if not _storage_path:
        return []
    db_path = os.path.join(_storage_path, "paths.db")
    if not os.path.isfile(db_path):
        return []
    result = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT path_str, sender_id, our_id FROM paths WHERE last_seen >= ?",
            (since,),
        ).fetchall()
        conn.close()
        for path_str, sender_id, our_id in rows:
            ids = [x.strip() for x in path_str.split(",") if x.strip()]
            if len(ids) < 2:
                continue
            if sender_id:
                ids = [sender_id.lower()] + ids
            if our_id:
                ids = ids + [our_id.lower()]
            result.append(ids)
    except Exception:
        pass
    return result


def _parse_paths_from_messages(messages_db, since=0):
    """Return [(path_ids, rssi)] from Path: strings in channel messages.

    ``since`` is a Unix timestamp; only messages with ts >= since are
    considered (0 = no filter).  rssi is the signal strength of the full
    message as received by our bot (last-hop RSSI), or None if not recorded.
    """
    if not os.path.isfile(messages_db):
        return []
    paths = []
    try:
        conn = sqlite3.connect(f"file:{messages_db}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM channels").fetchall() if r[0] != "dm"]
        except Exception:
            tables = []
        for table in tables:
            try:
                rows = conn.execute(
                    f'SELECT text, rssi FROM "{table}" WHERE ts >= ?'  # nosec B608
                    f' ORDER BY id DESC LIMIT 2000',
                    (since,),
                ).fetchall()
                for text, rssi in rows:
                    if text and "Path:" in text:
                        m = _PATH_RE.search(text)
                        if m:
                            ids = [x.strip().lower() for x in m.group(1).split(",")]
                            if len(ids) >= 2:
                                paths.append((ids, rssi))
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return paths


def _path_estimates_db():
    if not _storage_path:
        return None
    return os.path.join(_storage_path, "path_estimates.db")


def _update_path_estimates(fresh):
    """Weighted-average {node_id: (lat, lon)} into the persistent path estimate store.

    First call: node sits at its interpolated position (weight=1).
    Each subsequent call: position converges via running weighted average.
    Returns the post-update {node_id: (lat, lon)}.
    """
    db = _path_estimates_db()
    if not db or not fresh:
        return {}
    now = time.time()
    updated = {}
    try:
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS estimates (
                node_id TEXT PRIMARY KEY,
                lat     REAL NOT NULL,
                lon     REAL NOT NULL,
                weight  REAL NOT NULL DEFAULT 1.0,
                last_ts REAL NOT NULL
            )
        """)
        conn.commit()
        for node_id, (new_lat, new_lon) in fresh.items():
            row = conn.execute(
                "SELECT lat, lon, weight FROM estimates WHERE node_id=?", (node_id,)
            ).fetchone()
            if row:
                old_lat, old_lon, w = row
                nw = w + 1.0
                avg_lat = (old_lat * w + new_lat) / nw
                avg_lon = (old_lon * w + new_lon) / nw
                conn.execute(
                    "UPDATE estimates SET lat=?, lon=?, weight=?, last_ts=? WHERE node_id=?",
                    (avg_lat, avg_lon, nw, now, node_id)
                )
                updated[node_id] = (avg_lat, avg_lon)
            else:
                conn.execute(
                    "INSERT INTO estimates (node_id, lat, lon, weight, last_ts) "
                    "VALUES (?,?,?,1.0,?)",
                    (node_id, new_lat, new_lon, now)
                )
                updated[node_id] = (new_lat, new_lon)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[map_gen] update path estimates error: {e}")
    return updated


def _build_prefix_candidates(announce_db):
    """Build {hex_prefix → [(lat, lon), ...]} with ALL GPS candidates per prefix.

    Short path IDs (1–2 bytes = 2–4 hex chars) may match multiple GPS-announcing
    nodes that share the same prefix.  Callers use this to pick the geographically
    nearest candidate given path context, rather than taking the first match.

    Address-length rules (same as _build_prefix_gps_map):
      • 12-char MeshCore → candidates at 2, 4, 6, 8, 12
      • 8-char Meshtastic → candidates at 4, 6, 8 (no 2-char, avoids false relay matches)
    """
    import json as _json

    files = _announce_db_files(announce_db)
    candidates = {}  # prefix → [(lat, lon)]

    for f in files:
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)

            # Raw GPS from announces as base; position_estimates override with averages
            gps_map = {}
            for addr, lat, lon in conn.execute(
                "SELECT addr, lat, lon FROM announces "
                "WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY addr"
            ).fetchall():
                gps_map[addr.lower()] = (lat, lon)
            try:
                for addr, lat, lon in conn.execute(
                    "SELECT addr, lat, lon FROM position_estimates"
                ).fetchall():
                    gps_map[addr.lower()] = (lat, lon)
            except sqlite3.OperationalError:
                pass  # table not yet created

            conn.close()
            for addr, (lat, lon) in gps_map.items():
                alen = len(addr)
                if alen == 12:
                    plens = (2, 4, 6, 8, 12)
                elif alen == 8:
                    plens = (4, 6, 8)
                else:
                    plens = tuple(p for p in (4, 6, 8, 12) if p <= alen)
                for plen in plens:
                    pfx = addr[:plen]
                    candidates.setdefault(pfx, []).append((lat, lon))
        except Exception:
            pass

    # Inject bot's own GPS (written by meshcore_adapter at startup)
    if _storage_path:
        try:
            si_path = os.path.join(_storage_path, "self_info.json")
            with open(si_path) as f:
                si = _json.load(f)
            lat, lon = si.get("lat"), si.get("lon")
            pid = (si.get("pubkey_pre") or "").lower()
            if lat and lon and pid:
                for plen in (2, 4, 6):
                    if len(pid) >= plen:
                        pfx = pid[:plen]
                        candidates.setdefault(pfx, []).append((lat, lon))
                print(f"[map_gen] bot GPS injected: {pid} → {lat:.4f},{lon:.4f}")
        except (FileNotFoundError, KeyError, ValueError):
            pass

    return candidates


def _build_prefix_gps_map(announce_db, all_paths=None):
    """Build {hex_prefix → (lat, lon)} lookup for matching path node IDs.

    For short (≤4-char) path IDs with multiple GPS candidates, selects the
    candidate geographically nearest to the other GPS-resolved nodes in the
    same path (context-aware disambiguation).  Falls back to the first match
    when there is no context or only one candidate.
    """
    candidates = _build_prefix_candidates(announce_db)

    if not all_paths:
        # No path context: take the first (only) candidate per prefix
        return {pfx: cs[0] for pfx, cs in candidates.items() if cs}

    # Context-aware resolution: for each path, resolve short IDs using the
    # centroid of 6+-char (high-confidence) GPS matches in the same path.
    # Accumulate votes across all paths; majority coordinate wins per prefix.
    short_votes = {}  # prefix → [(lat, lon)]  accumulated nearest-match votes

    for path_ids in all_paths:
        # Resolve high-confidence (≥6 char) IDs first — these are unambiguous
        anchor_coords = []
        for pid in path_ids:
            if len(pid) >= 6:
                opts = candidates.get(pid, [])
                if opts:
                    anchor_coords.append(opts[0])  # unique at 6+ chars

        # For short IDs (≤4 chars), pick nearest to path's anchor centroid
        for pid in path_ids:
            if len(pid) > 4:
                continue
            opts = candidates.get(pid, [])
            if not opts:
                continue
            if len(opts) == 1:
                short_votes.setdefault(pid, []).append(opts[0])
            elif anchor_coords:
                cx = sum(c[0] for c in anchor_coords) / len(anchor_coords)
                cy = sum(c[1] for c in anchor_coords) / len(anchor_coords)
                nearest = min(opts, key=lambda c: (c[0]-cx)**2 + (c[1]-cy)**2)
                short_votes.setdefault(pid, []).append(nearest)
            else:
                short_votes.setdefault(pid, []).append(opts[0])

    # Build result: unambiguous entries direct from candidates,
    # short IDs replaced by their voted nearest coordinate (centroid of votes)
    result = {pfx: cs[0] for pfx, cs in candidates.items() if cs and len(pfx) > 4}
    for pid, votes in short_votes.items():
        lat = sum(v[0] for v in votes) / len(votes)
        lon = sum(v[1] for v in votes) / len(votes)
        result[pid] = (lat, lon)
    return result


def _estimate_unknown_positions(all_paths, prefix_gps):
    """Estimate positions for unknown path nodes using a two-pass centroid approach.

    Pass 1 — requires 3+ GPS-known neighbors: weighted centroid of those GPS coords.
    Pass 2 — requires 2 GPS + 2+ pass-1 estimates: weighted centroid of all of them.
    Nodes that don't meet either threshold are dropped (too uncertain to place).
    Weight for each neighbor = 1 / hop_distance within the path.
    """
    # Build per-node neighbor map: pid → {other_pid: minimum hop distance seen}
    nbr_map = {}  # only built for unknown (non-GPS) nodes
    for path_ids in all_paths:
        for i, pid in enumerate(path_ids):
            if prefix_gps.get(pid) is not None:
                continue
            if pid not in nbr_map:
                nbr_map[pid] = {}
            for j, other_pid in enumerate(path_ids):
                if other_pid == pid:
                    continue
                d = abs(i - j)
                if d < nbr_map[pid].get(other_pid, float('inf')):
                    nbr_map[pid][other_pid] = d

    def _centroid(coord_dist_pairs):
        """Inverse-hop-distance weighted centroid."""
        total_w = sum(1.0 / d for _, d in coord_dist_pairs)
        lat = sum(c[0] / d for c, d in coord_dist_pairs) / total_w
        lon = sum(c[1] / d for c, d in coord_dist_pairs) / total_w
        return (lat, lon)

    # Pass 1: 3+ GPS neighbors → GPS-only centroid
    pass1 = {}
    gps_nbrs_cache = {}  # pid → [(gps_coord, hop_dist), ...]

    for pid, nbrs in nbr_map.items():
        gps_nbrs = [(prefix_gps[n], d) for n, d in nbrs.items()
                    if prefix_gps.get(n) is not None]
        gps_nbrs_cache[pid] = gps_nbrs
        if len(gps_nbrs) >= 3:
            pass1[pid] = _centroid(gps_nbrs)

    # Pass 2: 2 GPS + 2+ pass-1 estimates → mixed centroid
    pass2 = {}
    for pid, nbrs in nbr_map.items():
        if pid in pass1:
            continue
        gps_nbrs = gps_nbrs_cache[pid]
        if len(gps_nbrs) < 2:
            continue
        est_nbrs = [(pass1[n], d) for n, d in nbrs.items() if n in pass1]
        if len(est_nbrs) < 2:
            continue
        pass2[pid] = _centroid(gps_nbrs + est_nbrs)

    # Pass 3: linear interpolation along paths between any two resolved nodes.
    # For each segment [left_gps ... unknown_nodes ... right_gps], proportionally
    # place unknown nodes on the straight line between the two anchors.
    # Accumulate multiple estimates per node then average them.
    interp_acc = {}  # pid → [(lat, lon)]

    for path_ids in all_paths:
        # Build index → resolved-coord map for this path
        resolved = {}
        for i, pid in enumerate(path_ids):
            coord = prefix_gps.get(pid) or pass1.get(pid) or pass2.get(pid)
            if coord is not None:
                resolved[i] = coord

        res_indices = sorted(resolved)
        if len(res_indices) < 2:
            continue

        for i, pid in enumerate(path_ids):
            if resolved.get(i) is not None:
                continue  # already has a position
            if pass1.get(pid) is not None or pass2.get(pid) is not None:
                continue  # covered by earlier passes
            # Find bounding resolved indices
            left_pairs  = [(ri, resolved[ri]) for ri in res_indices if ri < i]
            right_pairs = [(ri, resolved[ri]) for ri in res_indices if ri > i]
            if not left_pairs or not right_pairs:
                continue
            li, lcoord = left_pairs[-1]
            ri, rcoord = right_pairs[0]
            t = (i - li) / (ri - li)
            lat = lcoord[0] + t * (rcoord[0] - lcoord[0])
            lon = lcoord[1] + t * (rcoord[1] - lcoord[1])
            interp_acc.setdefault(pid, []).append((lat, lon))

    pass3 = {
        pid: (sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs))
        for pid, cs in interp_acc.items()
    }

    # pass1 > pass2 > pass3 (GPS centroid takes precedence over interpolation)
    return {**pass3, **pass2, **pass1}


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def _add_scale_bar(ax, lat_min, lat_max, lon_min, lon_max):
    """Draw a geographic scale bar in the lower-left corner of *ax*.

    Picks a round-number distance (~20 % of the map width) in miles,
    converts it to degrees of longitude at the centre latitude, and
    renders a horizontal bar with end ticks and a text label.
    """
    import math
    import matplotlib.patheffects as _pe

    lat_c = (lat_min + lat_max) / 2.0
    map_w_km = _haversine_km(lat_c, lon_min, lat_c, lon_max)

    _KM_PER_MI = 1.60934
    target_km = map_w_km * 0.20
    nice_mi = [5, 10, 25, 50, 100, 150, 200, 250, 500]
    chosen_mi = min(nice_mi, key=lambda m: abs(m * _KM_PER_MI - target_km))
    chosen_km = chosen_mi * _KM_PER_MI

    cos_lat = math.cos(math.radians(lat_c))
    d_lon = chosen_km / (111.32 * max(cos_lat, 1e-6))

    # 3 % from left edge, 4 % from bottom (in data coordinates)
    x0 = lon_min + 0.03 * (lon_max - lon_min)
    y0 = lat_min + 0.04 * (lat_max - lat_min)
    x1 = x0 + d_lon
    tick_h = (lat_max - lat_min) * 0.010

    ax.plot([x0, x1], [y0, y0], color="#ccccdd", linewidth=2.5,
            solid_capstyle="butt", zorder=20)
    for xk in (x0, x1):
        ax.plot([xk, xk], [y0 - tick_h, y0 + tick_h],
                color="#ccccdd", linewidth=1.5, zorder=20)
    lbl = ax.text((x0 + x1) / 2, y0 + tick_h * 1.8, f"{chosen_mi} mi",
                  color="#ccccdd", fontsize=6, ha="center", va="bottom",
                  zorder=20)
    lbl.set_path_effects([_pe.withStroke(linewidth=1.2, foreground="#1a1a2e")])


def _fit_path_loss(announce_db):
    """Fit RSSI = alpha − 10·n·log10(d_km) from GPS node announce RSSI.

    Uses our bot position (self_info.json) as origin.
    Returns (alpha, n_exp) tuple, or None if there is not enough data.
    """
    import math
    import json as _json

    if not _storage_path:
        return None
    try:
        with open(os.path.join(_storage_path, "self_info.json")) as f:
            si = _json.load(f)
        bot_lat, bot_lon = si.get("lat"), si.get("lon")
        if not bot_lat or not bot_lon:
            return None
    except Exception:
        return None

    points = []  # (log10_d, rssi)
    for f in _announce_db_files(announce_db):
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT lat, lon, rssi FROM announces "
                "WHERE lat IS NOT NULL AND lon IS NOT NULL AND rssi IS NOT NULL"
            ).fetchall()
            conn.close()
            for lat, lon, rssi in rows:
                d = _haversine_km(bot_lat, bot_lon, lat, lon)
                if 0.05 < d < 500:
                    points.append((math.log10(d), float(rssi)))
        except Exception:
            pass

    if len(points) < 3:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom < 1e-9:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    n_exp = -slope / 10.0
    alpha = my - slope * mx

    if not (1.5 <= n_exp <= 5.0):
        return None

    print(f"[map_gen] path loss fit: alpha={alpha:.1f} dBm, n={n_exp:.2f} ({n} pts)")
    return (alpha, n_exp)


def _rssi_relay_estimates(paths_with_rssi, prefix_gps, path_loss):
    """Estimate the penultimate relay's position using last-hop message RSSI.

    The message RSSI is the signal strength of the final hop to our bot, so it
    directly encodes the distance from the last relay to us.  Once we know that
    distance we project the relay in the direction of the path's GPS anchor(s).

    Returns {node_id: (lat, lon)}.
    """
    import math
    import json as _json

    alpha, n_exp = path_loss
    try:
        with open(os.path.join(_storage_path, "self_info.json")) as f:
            si = _json.load(f)
        bot_lat, bot_lon = si.get("lat"), si.get("lon")
        if not bot_lat or not bot_lon:
            return {}
    except Exception:
        return {}

    # Accumulate distance + direction hints per unknown penultimate relay
    relay_hits = {}  # node_id → [(d_km, bot_lat, bot_lon, anchor_coord)]

    for path_ids, rssi in paths_with_rssi:
        if rssi is None or len(path_ids) < 3:
            continue

        # Last node must resolve to a known position (typically our bot)
        last_coord = prefix_gps.get(path_ids[-1])
        if last_coord is None:
            continue
        b_lat, b_lon = last_coord

        penult = path_ids[-2]
        if penult in prefix_gps:
            continue  # already GPS-resolved

        # Distance from RSSI: d = 10^((alpha − rssi) / (10·n))
        try:
            d_km = 10.0 ** ((alpha - float(rssi)) / (10.0 * n_exp))
        except Exception:
            continue
        if not (0.05 <= d_km <= 300):
            continue

        # Direction anchor: earliest GPS-resolved node in the path (not the bot)
        anchor_coord = None
        for pid in path_ids[:-1]:
            if pid != penult and pid in prefix_gps:
                anchor_coord = prefix_gps[pid]
                break
        if anchor_coord is None:
            continue

        relay_hits.setdefault(penult, []).append((d_km, b_lat, b_lon, anchor_coord))

    estimates = {}
    for node_id, hits in relay_hits.items():
        avg_d = sum(h[0] for h in hits) / len(hits)

        # Aggregate direction vectors (bot → anchor) across all path observations
        dx_sum = dy_sum = 0.0
        b_lat_ref = b_lon_ref = None
        for _, b_lat, b_lon, (a_lat, a_lon) in hits:
            if b_lat_ref is None:
                b_lat_ref, b_lon_ref = b_lat, b_lon
            dx = (a_lat - b_lat) * 111.0
            dy = (a_lon - b_lon) * 111.0 * math.cos(math.radians(b_lat))
            norm = math.sqrt(dx * dx + dy * dy)
            if norm > 0.01:
                dx_sum += dx / norm
                dy_sum += dy / norm

        norm = math.sqrt(dx_sum * dx_sum + dy_sum * dy_sum)
        if norm < 0.01 or b_lat_ref is None:
            continue
        ux, uy = dx_sum / norm, dy_sum / norm

        new_lat = b_lat_ref + (avg_d * ux) / 111.0
        new_lon = b_lon_ref + (avg_d * uy) / (111.0 * math.cos(math.radians(b_lat_ref)))
        estimates[node_id] = (new_lat, new_lon)
        print(f"[map_gen] RSSI estimate: {node_id} @ {new_lat:.4f},{new_lon:.4f} "
              f"({avg_d:.1f} km, {len(hits)} obs)")

    return estimates


def generate_path_map(announce_db, days=7):
    """Generate the path connectivity map PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    import statistics

    # All GPS nodes from announce DB (background layer + 2σ anchor)
    all_gps_nodes = _read_gps_nodes(announce_db)

    # Restrict paths to the requested time window (default: last 7 days).
    since = time.time() - days * 86400

    # Collect paths first so we can pass them for nearest-match disambiguation.
    # _parse_paths_from_messages now returns [(path_ids, rssi)].
    all_paths = _parse_paths_from_pathsdb(since=since)
    paths_with_rssi = []
    if _storage_path and os.path.isdir(_storage_path):
        for fname in sorted(os.listdir(_storage_path)):
            if fname.startswith("messages_") and fname.endswith(".db"):
                pw = _parse_paths_from_messages(
                    os.path.join(_storage_path, fname), since=since)
                paths_with_rssi.extend(pw)
                all_paths.extend(ids for ids, _ in pw)

    # Prefix lookup: pass all_paths so short (≤4-char) IDs pick the nearest GPS candidate
    prefix_gps = _build_prefix_gps_map(announce_db, all_paths)

    # Fit path loss model then pre-seed working_gps with RSSI-based distance
    # estimates for the last relay before our bot.  These are better anchors
    # than pure hop-count interpolation for the subsequent multi-round pass.
    path_loss    = _fit_path_loss(announce_db)
    rssi_anchors = {}
    if path_loss and paths_with_rssi:
        rssi_anchors = _rssi_relay_estimates(paths_with_rssi, prefix_gps, path_loss)

    working_gps = dict(prefix_gps)
    for pid, coord in rssi_anchors.items():
        if pid not in working_gps:
            working_gps[pid] = coord
            for plen in (2, 4, 6):
                if len(pid) > plen and pid[:plen] not in working_gps:
                    working_gps[pid[:plen]] = coord
    _MAX_ROUNDS = 5
    for _round in range(_MAX_ROUNDS):
        new_est = _estimate_unknown_positions(all_paths, working_gps)
        if not new_est:
            break
        added_this_round = {}
        for pid, coord in new_est.items():
            if pid not in working_gps:
                added_this_round[pid] = coord
                # Add prefix-expanded lookups so subsequent rounds can match
                # shorter path IDs against this longer estimated ID.
                for plen in (2, 4, 6):
                    if len(pid) > plen:
                        pfx = pid[:plen]
                        if pfx not in working_gps:
                            added_this_round[pfx] = coord
        if not added_this_round:
            break
        working_gps.update(added_this_round)

    # Fresh estimates from this generation run
    fresh_estimates = {k: v for k, v in working_gps.items() if k not in prefix_gps}

    # Accumulate into persistent weighted-average store.
    # First call: node sits at interpolated midpoint (weight=1).
    # Each subsequent call: position converges via running average.
    estimated_gps = _update_path_estimates(fresh_estimates) or fresh_estimates

    # Resolve path nodes — GPS always wins over estimated
    gps_known_ids = set()   # path node IDs with real GPS
    estimated_ids = set()   # path node IDs with interpolated position
    path_node_coords = {}   # pid → (lat, lon)

    for path_ids in all_paths:
        for pid in path_ids:
            coord = prefix_gps.get(pid)
            if coord is not None:
                # GPS known — always upgrade even if previously estimated
                path_node_coords[pid] = coord
                gps_known_ids.add(pid)
                estimated_ids.discard(pid)
            elif pid not in path_node_coords and pid in estimated_gps:
                if len(pid) > 2:  # 1-byte IDs are too ambiguous without confirmed GPS
                    path_node_coords[pid] = estimated_gps[pid]
                    estimated_ids.add(pid)

    # Build edge data. Our node is the receiver, at the END of each path.
    # dist_from_us for edge starting at orig_idx = (path_len - 1) - orig_idx.
    # Fewer hops from us → thicker line.
    edges = {}          # (pa, pb) → {'count': int, 'dist_sum': int}
    edge_dir_votes = {} # (pa, pb) → int; positive = edge[0] is the far/source end
    for path_ids in all_paths:
        n = len(path_ids)
        # Keep original indices so we can measure distance from our node (end)
        resolved = [(pid, idx) for idx, pid in enumerate(path_ids)
                    if pid in path_node_coords]
        for i in range(len(resolved) - 1):
            pa, orig_i = resolved[i]
            pb, _      = resolved[i + 1]
            if pa != pb:
                dist_from_us = max(1, (n - 1) - orig_i)
                edge = tuple(sorted([pa, pb]))
                if edge not in edges:
                    edges[edge] = {'count': 0, 'dist_sum': 0}
                    edge_dir_votes[edge] = 0
                edges[edge]['count']    += 1
                edges[edge]['dist_sum'] += dist_from_us
                # pa appears earlier in the path → it is further from our bot.
                # Accumulate votes so the majority direction wins at draw time.
                edge_dir_votes[edge] += 1 if pa == edge[0] else -1

    # 2σ outlier filter anchored on GPS nodes (more reliable than estimated)
    gps_lats = [r[3] for r in all_gps_nodes]
    gps_lons = [r[4] for r in all_gps_nodes]

    if not gps_lats and not path_node_coords:
        return 0, {}

    mean_lat = mean_lon = std_lat = std_lon = None
    if len(gps_lats) >= 4:
        mean_lat, std_lat = statistics.mean(gps_lats), statistics.stdev(gps_lats)
        mean_lon, std_lon = statistics.mean(gps_lons), statistics.stdev(gps_lons)
        all_gps_nodes = [r for r in all_gps_nodes
                         if abs(r[3] - mean_lat) <= 3 * std_lat
                         and abs(r[4] - mean_lon) <= 3 * std_lon]
        path_node_coords = {
            pid: c for pid, c in path_node_coords.items()
            if abs(c[0] - mean_lat) <= 3 * std_lat
            and abs(c[1] - mean_lon) <= 3 * std_lon
        }
        edges = {e: d for e, d in edges.items()
                 if e[0] in path_node_coords and e[1] in path_node_coords}
        edge_dir_votes = {e: v for e, v in edge_dir_votes.items() if e in edges}
        gps_known_ids &= set(path_node_coords)
        estimated_ids  &= set(path_node_coords)

    # Compute bbox from all GPS nodes + estimated path nodes
    all_lats = [r[3] for r in all_gps_nodes] + [
        path_node_coords[p][0] for p in estimated_ids if p in path_node_coords]
    all_lons = [r[4] for r in all_gps_nodes] + [
        path_node_coords[p][1] for p in estimated_ids if p in path_node_coords]

    if not all_lats:
        return 0, {}

    pad_lat = max(1.5, (max(all_lats) - min(all_lats)) * 0.12)
    pad_lon = max(1.5, (max(all_lons) - min(all_lons)) * 0.12)
    lat_min, lat_max = min(all_lats) - pad_lat, max(all_lats) + pad_lat
    lon_min, lon_max = min(all_lons) - pad_lon, max(all_lons) + pad_lon

    fig, ax = plt.subplots(figsize=(14, 9), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")

    # State outlines
    try:
        geojson = _states_geojson()
        for feature in geojson["features"]:
            geom = feature["geometry"]
            polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                     else [geom["coordinates"]])
            for mpoly in polys:
                rings = mpoly if isinstance(mpoly[0][0], list) else [mpoly]
                for ring in rings:
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    ax.plot(xs, ys, color="#5a5a88", linewidth=1.2, zorder=7)
    except Exception as e:
        print(f"[map_gen] state outlines unavailable: {e}")

    # ── Pre-compute analytics needed by the rendering layers ──────────────────
    # Path stability per edge, relay centrality, and per-node activity.
    _paths_db = os.path.join(_storage_path, "paths.db") if _storage_path else ""
    edge_stability = _path_stability_scores(_paths_db)
    centrality     = _relay_centrality(all_paths, path_node_coords)

    _ann_files  = _announce_db_files(announce_db)
    node_activity = _read_node_activity(_ann_files, days=days)
    max_act_path  = max(
        (node_activity.get(r[1].lower(), (None, 0))[1] for r in all_gps_nodes),
        default=1,
    ) or 1
    max_centrality = max(centrality.values(), default=1.0) or 1.0

    # Build addr6_lookup: short prefix → full 6-char label from announce DB.
    # Used to expand 4-char path IDs to the full 3-byte prefix for display.
    addr6_lookup = {}   # prefix (any length) → addr[:6]
    proto_lookup  = {}  # prefix (any length) → proto string
    for proto, addr, _, _, _ in all_gps_nodes:
        al = addr.lower()
        for plen in (2, 4, 6, 8, 12):
            if len(al) >= plen:
                pfx = al[:plen]
                if pfx not in addr6_lookup:
                    addr6_lookup[pfx] = al[:6]
                if pfx not in proto_lookup:
                    proto_lookup[pfx] = proto

    # Which full GPS addrs are already represented in path_node_coords
    # (so we can skip double-labelling them in the background loop).
    path_covered_addrs = set()
    for _, addr, _, _, _ in all_gps_nodes:
        al = addr.lower()
        for pid in path_node_coords:
            if al.startswith(pid) or pid.startswith(al[:len(pid)]):
                path_covered_addrs.add(al)
                break

    # ── Layer 1: all GPS nodes — size ∝ recent activity ─────────────────────
    _PROTO_COLORS = {
        "meshcore": "#00ccff", "mc": "#00ccff",
        "meshtastic": "#44ff88", "mesh": "#44ff88",
        "lxmf": "#ffaa44",
    }
    # Batch scatter by protocol colour — one PathCollection per group.
    from collections import defaultdict as _dd2
    _gps_groups: dict = _dd2(lambda: {"lons": [], "lats": [], "sizes": []})
    _bg_labels = []  # (lon, lat, addr6, activity) for background (non-path) GPS nodes
    for proto, addr, nick, lat, lon in all_gps_nodes:
        color = _PROTO_COLORS.get(proto, "#aaaaaa")
        act   = node_activity.get(addr.lower(), (None, 0))[1]
        size  = 7 + 48 * (act / max_act_path)
        _gps_groups[color]["lons"].append(lon)
        _gps_groups[color]["lats"].append(lat)
        _gps_groups[color]["sizes"].append(size)
        al = addr.lower()
        if al not in path_covered_addrs:
            _bg_labels.append((lon, lat, al[:6], act))

    for color, data in _gps_groups.items():
        ax.scatter(data["lons"], data["lats"], s=data["sizes"],
                   color=color, zorder=4, alpha=0.75,
                   linewidths=0.3, edgecolors="#ffffff30")

    # ── Layer 2: path edges — chamfered leading edge to show flow direction ─────
    # Each dash/segment is a 4-vertex parallelogram: the trailing end (far from
    # our bot) is a square perpendicular cut; the leading end (toward our bot)
    # is diagonally chamfered, giving each segment a directional wedge shape.
    # Stability still drives solid / dashed / dotted spacing.
    import math as _math
    from matplotlib.collections import PolyCollection as _PC

    # Linewidth (pts) → geographic data-unit conversion via axis geometry.
    _ax_pos  = ax.get_position()
    _ax_wpts = _ax_pos.width  * 14.0 * 72   # axis width  in points
    _ax_hpts = _ax_pos.height *  9.0 * 72   # axis height in points
    _xlim = ax.get_xlim();  _ylim = ax.get_ylim()
    _lpp  = (_xlim[1] - _xlim[0]) / _ax_wpts   # lon-degrees per point
    _lapp = (_ylim[1] - _ylim[0]) / _ax_hpts   # lat-degrees per point

    def _chamfered_polys(lon_s, lat_s, lon_d, lat_d, lw, stab):
        """Return list of [[P1,P2,P3,P4], ...] chamfered quad polygons.

        Source end (trailing, square) is lon_s/lat_s; destination end
        (leading, chamfered) is lon_d/lat_d.
        """
        dlon = lon_d - lon_s
        dlat = lat_d - lat_s
        dx_sc = dlon / _lpp          # direction in screen-points
        dy_sc = dlat / _lapp
        sc_len = _math.sqrt(dx_sc ** 2 + dy_sc ** 2)
        if sc_len < 0.5:
            return []
        # Unit direction and left-normal, each scaled back to data coords
        d_lon_pp = (dx_sc / sc_len) * _lpp
        d_lat_pp = (dy_sc / sc_len) * _lapp
        n_lon_pp = (-dy_sc / sc_len) * _lpp
        n_lat_pp = ( dx_sc / sc_len) * _lapp
        hw = lw / 2
        hw_dlon = hw * n_lon_pp;  hw_dlat = hw * n_lat_pp
        cd = lw * 1.5              # chamfer depth ≈ 45° for a square line
        # Build dash ranges in screen-points along the edge
        if stab >= 0.75:
            ranges = [(0.0, sc_len)]
        elif stab >= 0.4:
            don, doff = lw * 4.0, lw * 3.0
            ranges = []; t = 0.0
            while t < sc_len:
                ranges.append((t, min(t + don, sc_len))); t += don + doff
        else:
            don, doff = lw * 2.0, lw * 4.0
            ranges = []; t = 0.0
            while t < sc_len:
                ranges.append((t, min(t + don, sc_len))); t += don + doff
        polys = []
        for t0, t1 in ranges:
            f0 = t0 / sc_len;  f1 = t1 / sc_len
            p0l = lon_s + f0 * dlon;  p0a = lat_s + f0 * dlat
            p1l = lon_s + f1 * dlon;  p1a = lat_s + f1 * dlat
            cd_a = min(cd, t1 - t0)        # clip chamfer to dash length
            c_dlon = cd_a * d_lon_pp;  c_dlat = cd_a * d_lat_pp
            # Trailing end: square (both corners at full width)
            # Leading end: top corner pulled back by chamfer depth
            P1 = (p0l + hw_dlon, p0a + hw_dlat)               # trailing top
            P2 = (p0l - hw_dlon, p0a - hw_dlat)               # trailing bottom
            P3 = (p1l - hw_dlon, p1a - hw_dlat)               # leading bottom (at tip)
            P4 = (p1l + hw_dlon - c_dlon, p1a + hw_dlat - c_dlat)  # leading top (chamfered)
            polys.append([P1, P2, P3, P4])
        return polys

    mean_dist_per_edge = {
        e: d['dist_sum'] / d['count'] for e, d in edges.items()
    }
    min_d = min(mean_dist_per_edge.values()) if mean_dist_per_edge else 1
    max_d = max(mean_dist_per_edge.values()) if mean_dist_per_edge else 1
    dist_range = max(max_d - min_d, 1)

    for (pa, pb), data in edges.items():
        lat_a, lon_a = path_node_coords[pa]
        lat_b, lon_b = path_node_coords[pb]
        t_d   = (mean_dist_per_edge[(pa, pb)] - min_d) / dist_range
        lw    = 2.5 - 1.8 * t_d    # 2.5 → 0.7
        alpha = 0.50 - 0.32 * t_d  # 0.50 → 0.18
        proto_a = proto_lookup.get(pa) or ("meshtastic" if len(pa) == 8 else "meshcore")
        proto_b = proto_lookup.get(pb) or ("meshtastic" if len(pb) == 8 else "meshcore")
        edge_color = _PROTO_COLORS.get(proto_a if proto_a == proto_b else proto_a, "#aaaaaa")
        stab = edge_stability.get(frozenset([pa, pb]), 1.0)
        if stab < 0.75:
            alpha *= 0.75 if stab >= 0.4 else 0.55
        # Direction: src = far-from-bot (square/trailing end), dst = near bot (chamfered end)
        if edge_dir_votes.get((pa, pb), 0) >= 0:
            lon_src, lat_src, lon_dst, lat_dst = lon_a, lat_a, lon_b, lat_b
        else:
            lon_src, lat_src, lon_dst, lat_dst = lon_b, lat_b, lon_a, lat_a
        polys = _chamfered_polys(lon_src, lat_src, lon_dst, lat_dst, lw, stab)
        if polys:
            pc = _PC(polys, facecolors=[edge_color] * len(polys),
                     edgecolors='none', alpha=alpha, zorder=3)
            ax.add_collection(pc)

    # ── Layer 3: estimated-position nodes — ring size ∝ centrality ───────────
    for pid in estimated_ids:
        if pid not in path_node_coords:
            continue
        lat, lon = path_node_coords[pid]
        proto = proto_lookup.get(pid)
        if not proto:
            proto = "meshtastic" if len(pid) == 8 else "meshcore"
        ring_color = _PROTO_COLORS.get(proto, "#aaaaaa")
        c_score = centrality.get(pid, 0.0) / max_centrality
        ring_s  = 130 + 170 * c_score   # 130 → 300
        ring_lw = 1.2 + 1.8 * c_score   # 1.2 → 3.0
        ax.scatter(lon, lat, s=ring_s, facecolors="none", edgecolors=ring_color,
                   linewidths=ring_lw, zorder=5, alpha=0.80)

    # ── Layer 4: path-GPS node highlights — size ∝ centrality + activity ─────
    for pid in gps_known_ids:
        lat, lon = path_node_coords[pid]
        proto  = proto_lookup.get(pid, "meshcore")
        color  = _PROTO_COLORS.get(proto, "#aaaaaa")
        c_score = centrality.get(pid, 0.0) / max_centrality
        # Find matching GPS addr for activity lookup
        full_addr = next(
            (r[1].lower() for r in all_gps_nodes
             if r[1].lower().startswith(pid) or pid.startswith(r[1].lower()[:len(pid)])),
            pid,
        )
        act    = node_activity.get(full_addr, (None, 0))[1]
        act_s  = (act / max_act_path) ** 0.5
        dot_s  = 12 + 50 * act_s + 58 * c_score   # 12 → 120
        ax.scatter(lon, lat, s=dot_s, color=color, zorder=5, alpha=1.0,
                   linewidths=0.8, edgecolors="#ffffff80")

    # ── Layer 5: labels ───────────────────────────────────────────────────────
    # Path nodes always get a bright label — these are the nodes the map is
    # actually showing routes through.  GPS known = protocol colour, estimated
    # position nodes = amber.
    for pid, (lat, lon) in path_node_coords.items():
        label = addr6_lookup.get(pid, pid)[:6]
        if pid in gps_known_ids:
            proto = proto_lookup.get(pid, "meshcore")
            color = _PROTO_COLORS.get(proto, "#aaaaaa")
        else:
            color = "#ccaa33"
        txt = ax.text(lon + 0.05, lat, label, fontsize=4.0, color=color,
                      va="center", zorder=9)
        txt.set_path_effects([pe.withStroke(linewidth=1.0, foreground="#1a1a2e")])

    # Background GPS nodes (not in any path): dim label, top-100 most active
    # only — the rest appear as dots.  Sorted most-active first so the busiest
    # background nodes are identifiable without cluttering the path area.
    _bg_labels.sort(key=lambda x: x[3], reverse=True)
    for lon, lat, label, _act in _bg_labels[:100]:
        txt = ax.text(lon + 0.05, lat, label, fontsize=4.0, color="#555566",
                      va="center", zorder=8)
        txt.set_path_effects([pe.withStroke(linewidth=1.0, foreground="#1a1a2e")])

    # City reference labels
    for city, clat, clon in _CITIES:
        if lat_min <= clat <= lat_max and lon_min <= clon <= lon_max:
            ax.scatter(clon, clat, s=8, color="#555577", marker="s", zorder=8)
            txt = ax.text(clon + 0.06, clat, city, fontsize=4.5, color="#555577",
                          va="center", zorder=8, style="italic")
            txt.set_path_effects([pe.withStroke(linewidth=1.2, foreground="#1a1a2e")])

    # ── Legend ───────────────────────────────────────────────────────────────
    _lh = [
        # — GPS nodes —
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#00ccff",
                   markersize=6, linestyle="None", label="MeshCore (GPS)"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#44ff88",
                   markersize=6, linestyle="None", label="Meshtastic (GPS)"),
        # — Estimated nodes —
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="none",
                   markeredgecolor="#00ccff", markeredgewidth=1.5,
                   markersize=9, linestyle="None", label="MeshCore (estimated pos)"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="none",
                   markeredgecolor="#44ff88", markeredgewidth=1.5,
                   markersize=9, linestyle="None", label="Meshtastic (estimated pos)"),
        # — Node size / centrality —
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#778899",
                   markersize=4, linestyle="None", label="● small = low activity / centrality"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#778899",
                   markersize=9, linestyle="None", label="● large = high activity / centrality"),
        # — Path lines —
        plt.Line2D([0], [0], color="#00ccff", linewidth=2.0,
                   label="MeshCore path (chamfer → bot direction)"),
        plt.Line2D([0], [0], color="#44ff88", linewidth=2.0,
                   label="Meshtastic path (chamfer → bot direction)"),
        # — Path stability —
        plt.Line2D([0], [0], color="#aaaaaa", linewidth=1.5, linestyle="solid",
                   label="path stable (consistent route)"),
        plt.Line2D([0], [0], color="#aaaaaa", linewidth=1.5, linestyle=(0, (5, 3)),
                   label="path variable"),
        plt.Line2D([0], [0], color="#aaaaaa", linewidth=1.5, linestyle=(0, (2, 4)),
                   label="path flapping"),
    ]
    ax.legend(handles=_lh, loc="lower right", fontsize=6.5,
              facecolor="#12122a", edgecolor="#3a3a5c", labelcolor="#ccccdd",
              framealpha=0.88, handlelength=2.0, borderpad=0.8,
              labelspacing=0.45)

    _add_scale_bar(ax, lat_min, lat_max, lon_min, lon_max)

    n_gps_bg   = len(all_gps_nodes)
    n_pathed   = len(path_node_coords)
    n_est      = len(estimated_ids)
    gen_time   = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    ax.set_title(
        f"NodeBot — {n_gps_bg} GPS nodes  •  {n_pathed} pathed"
        f" ({n_est} estimated)  •  {len(edges)} links  •  {gen_time}",
        color="#9999bb", fontsize=9, pad=6,
    )
    ax.tick_params(colors="#44445a", labelsize=5.5, length=3)
    for spine in ax.spines.values():
        spine.set_edgecolor("#3a3a5c")

    plt.tight_layout(pad=0.4)
    out = path_map_output_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    return n_pathed, {"edges": len(edges), "paths": len(all_paths),
                      "gps_total": n_gps_bg, "estimated": n_est}
