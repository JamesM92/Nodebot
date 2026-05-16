#!/usr/bin/python3
# NodeBot NomadNet activity feed page — deployed by install_lxmf.sh
#
# Shows the last 50 public channel messages and last 50 node announces.
# NOTE: PROJECT_DIR_PLACEHOLDER below is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run the installer.

import os
import re
import sqlite3
import configparser

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")

FEED_LIMIT = 50

# Meshtastic modem preset → short label
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

# ── Read config ───────────────────────────────────────────────
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

bot_name     = config.get("bot",     "name",        fallback="NodeBot").strip()
channel_log  = os.path.expanduser(config.get("logging", "channel_log",  fallback="").strip())
announce_db  = os.path.expanduser(config.get("logging", "announce_db",  fallback="").strip())
announce_log = os.path.expanduser(config.get("logging", "announce_log", fallback="").strip())


# ── Channel log helpers ───────────────────────────────────────

_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ')

def tail_records(path, n):
    """Return last n log records. A record starts with a timestamp line;
    continuation lines (no timestamp) are joined with embedded newlines."""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.readlines()
    except OSError:
        return []
    records = []
    current = None
    for line in raw:
        line = line.rstrip("\n")
        if _TS_RE.match(line):
            if current is not None:
                records.append(current)
            current = line
        elif current is not None:
            current += "\n" + line
    if current is not None:
        records.append(current)
    return records[-n:]


def build_nick_table_from_db(db_path):
    """Return {addr: nick} from the announce DB (most recent nick per addr)."""
    if not db_path or not os.path.isfile(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT addr, nick FROM announces WHERE nick IS NOT NULL "
            "GROUP BY addr ORDER BY MAX(ts) DESC"
        ).fetchall()
        conn.close()
        return {addr: nick for addr, nick in rows if nick}
    except Exception:
        return {}


def build_nick_table_from_log(log_path):
    """Fallback: parse text announce log for {addr: nick}."""
    if not log_path or not os.path.isfile(log_path):
        return {}
    table = {}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                if len(line) < 20:
                    continue
                rest = line[20:]
                if rest.startswith("["):
                    end = rest.find("]")
                    if end != -1:
                        rest = rest[end+2:]
                addr = rest.split(None, 1)[0] if rest else ""
                m = re.search(r"\(([^)]+)\)", rest)
                if addr and m:
                    table[addr] = m.group(1)
    except OSError:
        pass
    return table


def fmt_channel_line(record, nick_table=None):
    """Format a channel log record for Micron output.

    Log format: timestamp [proto] <addr> (Long Name | Short) (N hops) | message
    Embedded newlines in record = multi-line message continuation.
    """
    parts = record.split("\n", 1)
    first        = parts[0]
    continuation = parts[1] if len(parts) > 1 else ""

    if len(first) < 20:
        return first

    ts   = first[:19]
    rest = first[20:]

    # [tag]
    tag = ""
    if rest.startswith("["):
        end = rest.find("]")
        if end != -1:
            tag  = rest[1:end]
            rest = rest[end+2:]

    # <addr>
    addr = ""
    if rest.startswith("<"):
        end = rest.find(">")
        if end != -1:
            addr = rest[1:end]
            rest = rest[end+1:].lstrip()

    # shorten long hex addresses
    addr_disp = ("…" + addr[-8:]) if len(addr) > 8 else addr

    # (Long Name | Short Name) — optional
    names = ""
    if rest.startswith("(") and "hops)" not in rest.split("(")[1].split(")")[0]:
        end = rest.find(")")
        if end != -1:
            names = rest[1:end]
            rest  = rest[end+1:].lstrip()

    # (N hops) — optional
    hops = ""
    hops_m = re.match(r'\((\d+ hops)\)\s*', rest)
    if hops_m:
        hops = hops_m.group(1)
        rest = rest[hops_m.end():]

    # | message
    if rest.startswith("| "):
        rest = rest[2:]

    ts_out    = f"`F888`!{ts}`f"
    tag_out   = f" `Ffa6[{tag}]`f" if tag else ""
    addr_out  = f" `F8cf<{addr_disp}>`f"
    names_out = f" `Faaa({names})`f" if names else ""
    hops_out  = f" `F888({hops})`f" if hops else ""
    msg_out   = f" | {rest}"

    lines_out = [ts_out + tag_out + addr_out + names_out + hops_out + msg_out]
    if continuation:
        indent = "  "
        for cont in continuation.split("\n"):
            lines_out.append(f"`F888{indent}{cont}`f")

    return "\n".join(lines_out)


# ── Announce helpers ──────────────────────────────────────────

def _fmt_addr(addr):
    """Shorten a hex address to last 8 chars if longer."""
    if addr and len(addr) > 8 and all(c in "0123456789abcdefABCDEF" for c in addr):
        return "…" + addr[-8:]
    return addr or "?"


def _preset_tag(proto, modem_preset):
    """Build the protocol tag, e.g. [meshtastic:LF] or [meshcore]."""
    if proto == "meshtastic" and modem_preset:
        abbr = _PRESET_ABBR.get(modem_preset.upper(), modem_preset)
        return f"{proto}:{abbr}"
    return proto


def fmt_announce_row(row):
    """Format a SQLite announce row for Micron output.

    Row columns: proto, addr, nick, lat, lon, alt, rssi, snr, hops, battery, modem_preset, last_ts
    Output: timestamp  [proto:PRESET]  <addr_short> (nick)  | extras
    """
    proto, addr, nick, lat, lon, alt, rssi, snr, hops, battery, modem_preset, last_ts = row

    import time as _time
    ts = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(last_ts))

    tag     = _preset_tag(proto, modem_preset)
    addr_s  = _fmt_addr(addr)
    node_id = f"<{addr_s}>"
    if nick:
        node_id += f" ({nick})"

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

    ts_out   = f"`F888`!{ts}`f"
    tag_out  = f" `Ffa6[{tag}]`f"
    node_out = f" `F8cf{node_id}`f"
    ext_out  = f" `F888| {' '.join(extras)}`f" if extras else ""

    return ts_out + tag_out + node_out + ext_out


def load_announces_from_db(db_path, limit):
    """Return list of announce rows from SQLite, most recent unique node first."""
    if not db_path or not os.path.isfile(db_path):
        return None  # None = DB not available (vs [] = DB empty)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute("""
            SELECT * FROM (
                SELECT proto, addr,
                    COALESCE(
                        (SELECT nick FROM announces n
                         WHERE n.addr = a.addr AND n.nick IS NOT NULL
                         ORDER BY n.ts DESC LIMIT 1),
                        nick
                    ) AS nick,
                    lat, lon, alt, rssi, snr, hops, battery,
                    modem_preset, MAX(ts) AS last_ts
                FROM announces a
                GROUP BY addr
                ORDER BY last_ts DESC
                LIMIT ?
            ) ORDER BY last_ts ASC
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ── Fetch data ────────────────────────────────────────────────
channel_lines  = tail_records(channel_log, FEED_LIMIT)
announce_rows  = load_announces_from_db(announce_db, FEED_LIMIT)

# ── Render ────────────────────────────────────────────────────
print(f"`!`F4af{bot_name}`f — Activity Feed`f")
print("-")

# ── Public channel messages ───────────────────────────────────
print(f">Public Channel  (last {FEED_LIMIT})")
if channel_lines:
    for rec in channel_lines:
        print(fmt_channel_line(rec))
elif not channel_log:
    print("`F888  channel_log not configured in config.ini`f")
else:
    print("`F888  No messages recorded yet`f")

print("")

# ── Node announces ────────────────────────────────────────────
print(f">Node Announces  (last {FEED_LIMIT} unique nodes)")
if announce_rows:
    for row in announce_rows:
        print(fmt_announce_row(row))
elif announce_rows is None and not announce_log:
    print("`F888  announce_db not configured in config.ini`f")
else:
    print("`F888  No announces recorded yet`f")

print("-")
print("`F888NodeBot activity feed  •  edit installer/lxmf_pages/nodebot/activity.mu to customise`f")
print("")
print("`Fbbf`[← Back to index`:/page/index.mu`]`f")
