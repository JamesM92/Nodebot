#!/usr/bin/python3
# NodeBot NomadNet weekly digest page — deployed by install_lxmf.sh
# NOTE: PROJECT_DIR_PLACEHOLDER is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run /deploy-map.

import os
import sys
import time as _time
import sqlite3
import glob
import configparser
import collections

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"

CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

storage_path = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage"))
announce_db  = os.path.expanduser(
    config.get("logging", "announce_db", fallback="").strip())
bot_name     = config.get("bot", "name", fallback="NodeBot").strip()

WINDOW_DAYS   = 7
now           = _time.time()
week_ago      = now - WINDOW_DAYS * 86400
two_weeks_ago = now - 2 * WINDOW_DAYS * 86400

# ── Locate announce DB files ──────────────────────────────────────────────────
def _db_files():
    if not announce_db:
        return []
    d = announce_db if os.path.isdir(announce_db) else os.path.dirname(announce_db)
    if os.path.isdir(d):
        proto_dbs = sorted(glob.glob(os.path.join(d, "announces_*.db")))
        if proto_dbs:
            return proto_dbs
    return [announce_db] if os.path.isfile(announce_db) else []

# ── Aggregate queries across all announce DBs ─────────────────────────────────
this_week_addrs  = {}   # addr → (nick, proto, count_this_week)
prior_week_addrs = set()
new_this_week    = set()
proto_msg_counts = collections.Counter()  # announce packet count per protocol

for _f in _db_files():
    try:
        _c = sqlite3.connect(f"file:{_f}?mode=ro", uri=True)

        for addr, nick, proto, cnt in _c.execute("""
            SELECT addr, nick, proto, COUNT(*) as cnt
            FROM announces
            WHERE ts >= ?
            GROUP BY addr
            ORDER BY cnt DESC
        """, (week_ago,)).fetchall():
            a = addr.lower()
            p = proto or "unknown"
            prev = this_week_addrs.get(a)
            merged_cnt = (prev[2] if prev else 0) + cnt
            this_week_addrs[a] = (nick or a[:8], p, merged_cnt)
            proto_msg_counts[p] += cnt

        for (addr,) in _c.execute("""
            SELECT DISTINCT addr FROM announces
            WHERE ts >= ? AND ts < ?
        """, (two_weeks_ago, week_ago)).fetchall():
            prior_week_addrs.add(addr.lower())

        for (addr,) in _c.execute("""
            SELECT addr FROM announces
            GROUP BY addr
            HAVING MIN(ts) >= ?
        """, (week_ago,)).fetchall():
            new_this_week.add(addr.lower())

        _c.close()
    except Exception:
        pass

# Per-protocol derived counters
proto_node_counts = collections.Counter()
proto_new_counts  = collections.Counter()
for addr, (nick, proto, cnt) in this_week_addrs.items():
    proto_node_counts[proto] += 1
for addr in new_this_week:
    entry = this_week_addrs.get(addr)
    if entry:
        proto_new_counts[entry[1]] += 1

# ── Paths DB ──────────────────────────────────────────────────────────────────
relay_counts       = collections.Counter()
proto_path_counts  = collections.Counter()
proto_route_counts = collections.Counter()
total_paths        = 0
unique_routes      = 0
paths_db = os.path.join(storage_path, "paths.db")
if os.path.isfile(paths_db):
    try:
        _pc = sqlite3.connect(f"file:{paths_db}?mode=ro", uri=True)
        total_paths   = _pc.execute("SELECT COALESCE(SUM(count),0) FROM paths").fetchone()[0]
        unique_routes = _pc.execute("SELECT COUNT(*) FROM paths").fetchone()[0]
        for path_str, count in _pc.execute("SELECT path_str, count FROM paths").fetchall():
            hops = [h.strip() for h in path_str.split(",")]
            # Paths come from MeshCore "Path:" strings — always meshcore protocol
            proto_path_counts["meshcore"]  += count
            proto_route_counts["meshcore"] += 1
            for hop in hops[1:-1]:
                relay_counts[hop] += count
        _pc.close()
    except Exception:
        pass

# ── Messages DBs — raw counts + hourly activity + per-sender ranking ──────────
proto_raw_msg_counts = collections.Counter()
hour_counts  = [0] * 24
msg_senders  = {}   # (db_key, addr) → [display_name, count]
for _mf in sorted(glob.glob(os.path.join(storage_path, "messages_*.db"))):
    db_key = os.path.basename(_mf)[len("messages_"):-len(".db")]
    try:
        _mc = sqlite3.connect(f"file:{_mf}?mode=ro", uri=True)
        channels = [r[0] for r in _mc.execute(
            "SELECT name FROM channels WHERE name != 'dm'"
        ).fetchall()]
        for _ch in channels:
            try:
                cnt = _mc.execute(
                    f'SELECT COUNT(*) FROM "{_ch}" WHERE ts >= ?', (week_ago,)
                ).fetchone()[0]
                proto_raw_msg_counts[db_key] += cnt
                for h, hcnt in _mc.execute(
                    f"SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER),"
                    f" COUNT(*) FROM \"{_ch}\" WHERE ts >= ? GROUP BY 1",
                    (week_ago,)
                ).fetchall():
                    if 0 <= h <= 23:
                        hour_counts[h] += hcnt
                for addr, nick, scnt in _mc.execute(
                    f'SELECT addr, nick, COUNT(*) FROM "{_ch}" WHERE ts >= ? GROUP BY addr',
                    (week_ago,)
                ).fetchall():
                    key = (db_key, (addr or '').lower())
                    label = nick or (addr[:6] if addr else '?')
                    if key not in msg_senders:
                        msg_senders[key] = [label, 0]
                    msg_senders[key][1] += scnt
                    if nick:
                        msg_senders[key][0] = nick
            except Exception:
                pass
        _mc.close()
    except Exception:
        pass

# ── Telemetry DB ──────────────────────────────────────────────────────────────
tel_db = os.path.join(storage_path, "telemetry.db")
tel_node_count = 0
tel_metrics = []   # (label, avg, min, max, fmt_str)
if os.path.isfile(tel_db):
    try:
        _tc = sqlite3.connect(f"file:{tel_db}?mode=ro", uri=True)
        tel_node_count = _tc.execute(
            "SELECT COUNT(DISTINCT addr) FROM telemetry WHERE ts >= ?", (week_ago,)
        ).fetchone()[0]
        for _lbl, _col, _fmt in [
            ('Battery (%)',       'battery_level',       '{:.0f}'),
            ('Channel util (%)',  'channel_utilization', '{:.1f}'),
            ('Air TX util (%)',   'air_util_tx',         '{:.1f}'),
            ('Temperature (°C)',  'temperature',         '{:.1f}'),
            ('Humidity (%)',      'relative_humidity',   '{:.0f}'),
            ('Pressure (hPa)',    'barometric_pressure', '{:.0f}'),
            ('RSSI (dBm)',        'rssi',                '{:.0f}'),
            ('SNR (dB)',          'snr',                 '{:.1f}'),
        ]:
            row = _tc.execute(
                f"SELECT AVG({_col}), MIN({_col}), MAX({_col})"
                f" FROM telemetry WHERE ts >= ? AND {_col} IS NOT NULL",
                (week_ago,)
            ).fetchone()
            if row and row[0] is not None:
                tel_metrics.append((_lbl, row[0], row[1], row[2], _fmt))
        _tc.close()
    except Exception:
        pass

# ── Nick lookup ───────────────────────────────────────────────────────────────
def _nick(addr_prefix):
    entry = this_week_addrs.get(addr_prefix)
    return entry[0] if entry else addr_prefix[:8]

# ── Display-width helpers (emoji count as 2 columns) ─────────────────────────
import unicodedata as _ud

def _dw(s):
    return sum(2 if _ud.east_asian_width(c) in ('W', 'F') else 1 for c in s)

def _ljust(s, width):
    return s + ' ' * max(0, width - _dw(s))

# ── Bar helper ────────────────────────────────────────────────────────────────
BAR_WIDTH = 18

def _bar(value, max_value):
    if max_value <= 0:
        return ""
    filled = round(BAR_WIDTH * value / max_value)
    return "`F4af" + "█" * filled + "`F333" + "░" * (BAR_WIDTH - filled) + "`f"

# ── Table helper ──────────────────────────────────────────────────────────────
# Protocol columns sorted by node count (most common first)
_protos  = [p for p, _ in sorted(proto_node_counts.items(), key=lambda x: -x[1])]
LABEL_W  = 20
_max_num = max(len(this_week_addrs),
               sum(proto_msg_counts.values()) if proto_msg_counts else 1,
               sum(proto_raw_msg_counts.values()) if proto_raw_msg_counts else 1,
               total_paths or 1, 1)
NUM_W    = max(len(str(_max_num)) + 1, 7)
PCOL_W   = (max(max(len(p) for p in _protos) + 2, 8) if _protos else 8)

def _trow(label, total, by_proto):
    s = f"{label:<{LABEL_W}}{total:>{NUM_W}}"
    for p in _protos:
        s += f"  {by_proto.get(p, 0):>{PCOL_W}}"
    return s

def _thdr():
    s = f"{'':>{LABEL_W}}{'Total':>{NUM_W}}"
    for p in _protos:
        s += f"  {p:>{PCOL_W}}"
    return s

_divw = LABEL_W + NUM_W + len(_protos) * (PCOL_W + 2)

# ── Render ────────────────────────────────────────────────────────────────────
print(f"`!`F4af{bot_name}`f — Weekly Digest`f")
print("-")

if not _db_files():
    print("`F888announce_db not configured in config.ini`f")
else:
    this_week_count  = len(this_week_addrs)
    prior_week_count = len(prior_week_addrs)
    new_count        = len(new_this_week)
    delta            = this_week_count - prior_week_count
    total_ever       = len(set(list(this_week_addrs.keys()) + list(prior_week_addrs)))
    total_msgs       = sum(proto_msg_counts.values())

    date_from = _time.strftime("%b %d", _time.localtime(week_ago))
    date_to   = _time.strftime("%b %d", _time.localtime(now))

    # ── Network overview table ────────────────────────────────────────────────
    print(f">Network Overview  ({date_from} – {date_to})")
    print(f"`Faaa  Unique nodes (all time):  `F4af{total_ever}`f")
    if prior_week_count:
        arrow = "↑" if delta >= 0 else "↓"
        print(f"`Faaa  Prior week node count:   `F888{prior_week_count}  `f`F4af{arrow} {abs(delta)}`f")
    print("")
    print(f"`F888  {_thdr()}`f")
    print(f"`F888  {'─' * _divw}`f")
    print(f"`Faaa  {_trow('Nodes heard', this_week_count, proto_node_counts)}`f")
    print(f"`Faaa  {_trow('New this week', new_count, proto_new_counts)}`f")
    print(f"`Faaa  {_trow('Announcements', total_msgs, proto_msg_counts)}`f")
    total_raw = sum(proto_raw_msg_counts.values())
    if total_raw:
        print(f"`Faaa  {_trow('Messages', total_raw, proto_raw_msg_counts)}`f")
    if total_paths:
        print(f"`Faaa  {_trow('Path observations', total_paths, proto_path_counts)}`f")
        print(f"`Faaa  {_trow('Unique routes', unique_routes, proto_route_counts)}`f")

    print("")

    # ── Most active nodes (by public message count) ───────────────────────────
    print(f">Most Active Nodes  (public messages, last {WINDOW_DAYS} days)")
    sorted_senders = sorted(msg_senders.values(), key=lambda x: -x[1])
    if sorted_senders:
        max_cnt = sorted_senders[0][1]
        nick_w  = max((_dw(s[0]) for s in sorted_senders[:10]), default=8)
        for rank, (name, cnt) in enumerate(sorted_senders[:10], 1):
            bar = _bar(cnt, max_cnt)
            print(f"`F888  {rank:>2}. `F4af{_ljust(name, nick_w)}`f  {bar}  `F4af{cnt}`f")
    else:
        print("`F888  No message data yet`f")

    print("")

    # ── New nodes this week ───────────────────────────────────────────────────
    print(f">New Nodes This Week  ({new_count} first heard)")
    new_detail = []
    for addr in new_this_week:
        entry = this_week_addrs.get(addr)
        if entry:
            new_detail.append((entry[2], addr, entry[0], entry[1]))
    new_detail.sort(reverse=True)

    if new_detail:
        nick_w_new = max((_dw(nick) for _, _, nick, _ in new_detail[:15]), default=20)
        for cnt, addr, nick, proto in new_detail[:15]:
            print(f"`Faaa  {_ljust(nick, nick_w_new)}`F888  {proto:<12}  {cnt} announces`f")
        if len(new_detail) > 15:
            print(f"`F888  … and {len(new_detail)-15} more`f")
    else:
        print("`F888  None`f")

    print("")

    # ── Top relay nodes ───────────────────────────────────────────────────────
    print(">Top Relay Nodes  (paths through)")
    if relay_counts:
        max_r    = relay_counts.most_common(1)[0][1]
        nick_w_r = max((len(_nick(a)) for a in list(relay_counts)[:10]), default=8)
        for relay_id, cnt in relay_counts.most_common(10):
            bar  = _bar(cnt, max_r)
            nick = _nick(relay_id)
            print(f"`Faaa  {nick:<{nick_w_r}}`f  {bar}  `F4af{cnt}`f `F888paths`f")
    else:
        print("`F888  No path relay data yet`f")

    print("")

    # ── Activity by hour ──────────────────────────────────────────────────────
    if any(hour_counts):
        print(">Activity by Hour  (public messages, local time)")
        max_h = max(hour_counts) or 1
        HB = 10
        for _row in range(12):
            h_am, h_pm   = _row, _row + 12
            c_am, c_pm   = hour_counts[h_am], hour_counts[h_pm]
            f_am = round(HB * c_am / max_h)
            f_pm = round(HB * c_pm / max_h)
            b_am = "`F4af" + "█" * f_am + "`F333" + "░" * (HB - f_am) + "`f"
            b_pm = "`F4af" + "█" * f_pm + "`F333" + "░" * (HB - f_pm) + "`f"
            print(f"`F888  {h_am:02d}  `f{b_am}`F888  {c_am:>4}    {h_pm:02d}  `f{b_pm}`F888  {c_pm:>4}`f")
        print("")

    # ── Telemetry summary ─────────────────────────────────────────────────────
    if tel_metrics:
        TL = 20
        TC = 7
        print(f">Telemetry  ({tel_node_count} nodes reporting this week)")
        print(f"`F888  {'':>{TL}}{'Avg':>{TC}}{'Min':>{TC}}{'Max':>{TC}}`f")
        print(f"`F888  {'─' * (TL + TC * 3)}`f")
        for _lbl, _avg, _mn, _mx, _fmt in tel_metrics:
            print(f"`Faaa  {_lbl:<{TL}}`F4af{_fmt.format(_avg):>{TC}}`F888{_fmt.format(_mn):>{TC}}{_fmt.format(_mx):>{TC}}`f")
        print("")

print("")
print("-")
gen_time = _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())
print(f"`F888Generated {gen_time}`f")
print("")
print("`Fbbf`[← Back to Activity`:/page/nodebot/activity.mu`]`f  "
      "`Fbbf`[🔵 Node Map`:/page/nodebot/map.mu`]`f  "
      "`Fbbf`[🗺 Path Map`:/page/nodebot/map_paths.mu`]`f  "
      "`Fbbf`[📊 County Map`:/page/nodebot/county.mu`]`f  "
      "`Fbbf`[📋 Digest`:/page/nodebot/digest.mu`]`f")
print("")
