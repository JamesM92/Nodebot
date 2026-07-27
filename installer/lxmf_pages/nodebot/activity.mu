#!/usr/bin/python3
# NodeBot NomadNet activity feed page — deployed by install_lxmf.sh
#
# Shows the last 50 public channel messages (merged across all protocols) and
# the last 50 node announces.
# NOTE: PROJECT_DIR_PLACEHOLDER below is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run /deploy-activity.

import glob as _glob
import os
import sqlite3
import configparser
import time as _time

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")

FEED_LIMIT        = 50    # total messages shown in the merged channel feed
PER_CHANNEL_LIMIT = 50    # messages fetched per channel before merging

# Table names to skip when building the channel feed.
# Format: "{db_key}/{table}" e.g. "meshcore/test" to hide the #test channel.
EXCLUDED_TABLES = {"meshcore/test", "meshcore/bot"}

# ── Read config ───────────────────────────────────────────────
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

bot_name     = config.get("bot",     "name",         fallback="NodeBot").strip()
storage_path = os.path.expanduser(config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage"))
announce_db  = os.path.expanduser(config.get("logging", "announce_db",  fallback="").strip())
announce_log = os.path.expanduser(config.get("logging", "announce_log", fallback="").strip())


# ── Channel feed helpers ──────────────────────────────────────

# Meshtastic modem preset → short label (used to build tag display)
_PRESET_ABBR = {
    "LONG_FAST":      "LF",
    "LONG_SLOW":      "LS",
    "LONG_MODERATE":  "LM",
    "LONG_MOD":       "LM",
    "MEDIUM_FAST":    "MF",
    "MEDIUM_SLOW":    "MS",
    "SHORT_FAST":     "SF",
    "SHORT_SLOW":     "SS",
    "SHORT_TURBO":    "ST",
    "VERY_LONG_SLOW": "VLS",
}


def _tag_display(db_key, table):
    """Build a short readable tag from the DB key and table name.

    messages_meshcore.db  / public    → core/public
    messages_meshcore.db  / ch_0      → core/ch.0
    messages_meshtastic_lf.db / broadcast → tastic:LF
    messages_lxmf.db / broadcast      → lxmf
    """
    if db_key == "meshcore":
        label = table.replace("ch_", "ch.")
        return f"core/{label}"
    if db_key.startswith("meshtastic_"):
        preset_key = db_key[len("meshtastic_"):].upper()
        abbr = _PRESET_ABBR.get(preset_key, preset_key)
        return f"tastic:{abbr}"
    if db_key == "lxmf":
        return "lxmf"
    return f"{db_key}/{table}"


def load_all_channels(storage_path, excluded=None, per_ch=PER_CHANNEL_LIMIT):
    """Open every messages_*.db in storage_path, read the last `per_ch` rows from
    each non-DM channel table, and return a single list sorted by ts ascending."""
    excluded = excluded or set()
    entries  = []   # list of (ts, tag_display, row_dict)

    if not os.path.isdir(storage_path):
        return entries

    for fname in sorted(os.listdir(storage_path)):
        if not fname.startswith("messages_") or not fname.endswith(".db"):
            continue
        db_key  = fname[len("messages_"):-len(".db")]
        db_path = os.path.join(storage_path, fname)
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = [r[0] for r in conn.execute("SELECT name FROM channels").fetchall()]
            for table in tables:
                if table == "dm":
                    continue
                excl_key = f"{db_key}/{table}"
                if excl_key in excluded:
                    continue
                tag = _tag_display(db_key, table)
                try:
                    rows = conn.execute(
                        f'SELECT ts, addr, nick, short_name, text, hops, rssi, snr '
                        f'FROM "{table}" ORDER BY id DESC LIMIT ?',
                        (per_ch,)
                    ).fetchall()
                    for r in rows:
                        entries.append((r["ts"], tag, dict(r)))
                except sqlite3.OperationalError:
                    pass
            conn.close()
        except Exception:
            pass

    entries.sort(key=lambda x: x[0])
    return entries


def fmt_channel_row(ts, tag, row, tag_width=0):
    """Format one message row as a Micron output line."""
    time_str = _time.strftime("%H:%M:%S", _time.localtime(ts))
    text     = (row.get("text") or "").replace("|", "│")
    hops     = row.get("hops")

    bracket  = f"[{tag}]"
    pad      = " " * max(0, tag_width - len(bracket))
    ts_out   = f"`F888`!{time_str}`f"
    tag_out  = f" `Ffa6{bracket}`f{pad}"
    hops_out = f" `F888<{int(hops):02d} hops>`f" if hops is not None else ""

    # visible prefix width before the message text: HH:MM:SS + space+tag+pad + hops + space+│+space
    prefix_width = 8 + 1 + tag_width + (10 if hops is not None else 0) + 3
    indent = " " * (prefix_width + 3)

    lines = text.split("\n") if text else []
    if not lines:
        return ts_out + tag_out + hops_out
    msg_out = f" `Faaa│ {lines[0]}`f"
    for line in lines[1:]:
        msg_out += f"\n`Faaa{indent}{line}`f"

    return ts_out + tag_out + hops_out + msg_out


# ── Announce helpers ──────────────────────────────────────────

def _fmt_addr(addr):
    if addr and len(addr) > 12 and all(c in "0123456789abcdefABCDEF" for c in addr):
        return "…" + addr[-12:]
    return addr or "?"


_PROTO_ABBR = {"meshcore": "core", "meshtastic": "tastic"}


def _preset_tag(proto, modem_preset):
    short = _PROTO_ABBR.get(proto, proto)
    if proto == "meshtastic" and modem_preset:
        abbr = _PRESET_ABBR.get(modem_preset.upper(), modem_preset)
        return f"{short}:{abbr}"
    return short


def fmt_announce_row(row):
    proto, addr, nick, short_name, lat, lon, alt, rssi, snr, hops, battery, modem_preset, last_ts = row
    ts      = _time.strftime("%H:%M:%S", _time.localtime(last_ts))
    tag     = _preset_tag(proto, modem_preset)
    addr_s  = _fmt_addr(addr)
    node_id = f"<{addr_s}>"
    if nick and short_name and nick.lower() != short_name.lower():
        node_id += f" ({nick} / {short_name})"
    elif nick:
        node_id += f" ({nick})"
    elif short_name:
        node_id += f" ({short_name})"
    extras = []
    if lat is not None and lon is not None:
        coord = f"{lat:.5f},{lon:.5f}"
        if alt is not None:
            coord += f" alt={int(alt)}m"
        extras.append(f"gps={coord}")
    if rssi is not None:
        extras.append(f"rssi={rssi}")
    if snr is not None:
        extras.append(f"snr={snr}")
    if hops is not None:
        extras.append(f"hops={hops}")
    if battery is not None:
        extras.append(f"bat={battery}%")
    node_gap = "  " if proto == "meshcore" else " "
    ts_out   = f"`F888`!{ts}`f"
    tag_out  = f" `Ffa6[{tag}]`f"
    node_out = f"{node_gap}`F8cf{node_id}`f"
    ext_out  = f" `F888| {' '.join(extras)}`f" if extras else ""
    return ts_out + tag_out + node_out + ext_out


def _announce_db_files(db_path):
    """Return list of per-protocol announce DB paths, falling back to a single DB."""
    if not db_path:
        return []
    d = os.path.dirname(db_path) if not os.path.isdir(db_path) else db_path
    if os.path.isdir(d):
        proto_dbs = sorted(_glob.glob(os.path.join(d, "announces_*.db")))
        if proto_dbs:
            return proto_dbs
    return [db_path] if os.path.isfile(db_path) else []


def load_announces_from_db(db_path, limit):
    files = _announce_db_files(db_path)
    if not files:
        return None
    merged = {}  # addr → row (keep most recent across all proto DBs)
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            rows = conn.execute("""
                SELECT proto, addr,
                    COALESCE(
                        (SELECT nick FROM announces n
                         WHERE n.addr = a.addr AND n.nick IS NOT NULL
                         ORDER BY n.ts DESC LIMIT 1),
                        nick
                    ) AS nick,
                    COALESCE(
                        (SELECT short_name FROM announces n
                         WHERE n.addr = a.addr AND n.short_name IS NOT NULL
                         ORDER BY n.ts DESC LIMIT 1),
                        short_name
                    ) AS short_name,
                    lat, lon, alt, rssi, snr, hops, battery,
                    modem_preset, MAX(ts) AS last_ts
                FROM announces a
                GROUP BY addr
                ORDER BY last_ts DESC
            """).fetchall()
            conn.close()
            for r in rows:
                addr = r[1]
                if addr not in merged or r[-1] > merged[addr][-1]:
                    merged[addr] = r
        except Exception:
            pass
    if not merged:
        return []
    # Sort by last_ts DESC, cap at limit, then return in ASC order for display
    top = sorted(merged.values(), key=lambda r: r[-1], reverse=True)[:limit]
    return sorted(top, key=lambda r: r[-1])


# ── Fetch data ────────────────────────────────────────────────
_all_entries  = load_all_channels(storage_path, excluded=EXCLUDED_TABLES)
channel_feed  = _all_entries[-FEED_LIMIT:]
announce_rows = load_announces_from_db(announce_db, FEED_LIMIT)


# ── Render ────────────────────────────────────────────────────
print(f"`!`F4af{bot_name}`f — Activity Feed`f")
print("-")

# ── Public channel messages ───────────────────────────────────
print(f">Public Channels  (last {FEED_LIMIT})")
if channel_feed:
    _tag_width = max((len(f"[{tag}]") for _, tag, _ in channel_feed), default=0)
    _cur_date = None
    for ts, tag, row in channel_feed:
        d = _time.strftime("%Y-%m-%d", _time.localtime(ts))
        if d != _cur_date:
            if _cur_date is not None:
                print(f"`F666  V─ {d} ─V`f")
            _cur_date = d
        print(fmt_channel_row(ts, tag, row, tag_width=_tag_width))
elif not os.path.isdir(storage_path):
    print("`F888  storage_path not found — check config.ini`f")
else:
    print("`F888  No channel messages recorded yet`f")

print("")

# ── Node announces ────────────────────────────────────────────
print(f">Node Announces  (last {FEED_LIMIT} unique nodes)")
if announce_rows:
    _cur_date = None
    for row in announce_rows:
        d = _time.strftime("%Y-%m-%d", _time.localtime(row[-1]))
        if d != _cur_date:
            if _cur_date is not None:
                print(f"`F666  V─ {d} ─V`f")
            _cur_date = d
        print(fmt_announce_row(row))

elif announce_rows is None and not announce_log:
    print("`F888  announce_db not configured in config.ini`f")
else:
    print("`F888  No announces recorded yet`f")

print("-")
print("`F888NodeBot activity feed  •  edit installer/lxmf_pages/nodebot/activity.mu to customise`f")
print("")
print("`Fbbf`[← Back to index`:/page/index.mu`]`f  `Fbbf`[🔵 Node Map`:/page/nodebot/map.mu`]`f  `Fbbf`[🗺 Path Map`:/page/nodebot/map_paths.mu`]`f  `Fbbf`[📊 County Map`:/page/nodebot/county.mu`]`f  `Fbbf`[📋 Digest`:/page/nodebot/digest.mu`]`f")
