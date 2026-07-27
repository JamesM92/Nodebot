# logger.py — optional append-only log files for channel and DM traffic,
#             and a SQLite database for node announces.

import os
import sqlite3
import tempfile
import threading
import time

_lock = threading.Lock()
_channel_path  = None
_dm_path       = None
_announce_path = None   # legacy text log (kept for backward compat)
_announce_conns = {}    # {proto: sqlite3.Connection} — one DB per protocol
_announce_db_dir = None # directory where per-proto announce DBs are stored
_telemetry_conn  = None # single DB for all telemetry frames

_DISK_THRESHOLD = 0.90   # fraction — trigger trim above this
_TRIM_FRACTION  = 0.25   # drop oldest 25% of lines when trimming
_CHECK_INTERVAL = 60     # seconds between disk-space checks
_CHECK_WRITES   = 100    # also check after this many writes

_max_bytes = 0           # 0 = disabled; set from max_log_mb config
_writes_since_check = 0
_last_check = 0.0

_ANNOUNCE_COOLDOWN  = 15 * 60  # suppress repeated announces within this window (seconds)
_ANNOUNCE_MAX_NODES = 500      # unique addresses to keep per protocol
_ANNOUNCE_MAX_HIST  = 3        # announce rows to keep per address

# When True, skip all LXMF announce logging. NodeBot runs as a shared-instance
# client so all announces arrive via the same local socket — LoRa vs TCP origin
# is indistinguishable. Set False to log all LXMF announces (LoRa and TCP alike).
_announce_local_only = True


def _init_announce_db(path):
    """Open (or create) an announce DB at path. Returns the connection."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announces (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL    NOT NULL,
            proto        TEXT    NOT NULL,
            addr         TEXT    NOT NULL,
            nick         TEXT,
            short_name   TEXT,
            lat          REAL,
            lon          REAL,
            alt          REAL,
            rssi         REAL,
            snr          REAL,
            hops         INTEGER,
            battery      INTEGER,
            modem_preset TEXT
        )
    """)
    # Migrate existing DBs that predate the short_name column
    try:
        conn.execute("ALTER TABLE announces ADD COLUMN short_name TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_addr_ts ON announces (addr, ts DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_estimates (
            addr     TEXT PRIMARY KEY,
            proto    TEXT NOT NULL,
            lat      REAL NOT NULL,
            lon      REAL NOT NULL,
            weight   REAL NOT NULL DEFAULT 1.0,
            last_ts  REAL NOT NULL
        )
    """)
    conn.commit()
    print(f"[logger] announce db: {path}")
    return conn


def _get_announce_conn(proto):
    """Return (and lazily open) the announce DB connection for a protocol."""
    if proto in _announce_conns:
        return _announce_conns[proto]
    if not _announce_db_dir:
        return None
    path = os.path.join(_announce_db_dir, f"announces_{proto}.db")
    conn = _init_announce_db(path)
    _announce_conns[proto] = conn
    return conn


def backfill_positions(proto):
    """Seed position_estimates from announces for addrs not yet estimated.

    Runs once on startup after the DB is opened.  Each addr gets weight=1
    seeded from its most recent GPS announce.  Skips addrs already in the
    table so repeated calls are safe.
    """
    conn = _get_announce_conn(proto)
    if conn is None:
        return 0
    try:
        with _lock:
            rows = conn.execute("""
                SELECT proto, addr, lat, lon, MAX(ts) AS last_ts
                FROM announces
                WHERE lat IS NOT NULL AND lon IS NOT NULL
                  AND addr NOT IN (SELECT addr FROM position_estimates)
                GROUP BY addr
            """).fetchall()
            count = 0
            for proto_r, addr, lat, lon, last_ts in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO position_estimates "
                    "(addr, proto, lat, lon, weight, last_ts) VALUES (?,?,?,?,1.0,?)",
                    (addr, proto_r, lat, lon, last_ts)
                )
                count += 1
            conn.commit()
            return count
    except Exception as e:
        print(f"[logger] backfill_positions error: {e}")
        return 0


def all_announce_db_paths():
    """Return sorted list of all per-proto announce DB paths that exist on disk."""
    import glob as _glob
    if not _announce_db_dir:
        return []
    return sorted(_glob.glob(os.path.join(_announce_db_dir, "announces_*.db")))


def init(config):
    global _channel_path, _dm_path, _announce_path, _max_bytes, _announce_local_only
    global _announce_db_dir

    channel_raw     = config.get("logging", "channel_log",              fallback="").strip()
    dm_raw          = config.get("logging", "dm_log",                   fallback="").strip()
    announce_raw    = config.get("logging", "announce_log",             fallback="").strip()
    announce_db_raw = config.get("logging", "announce_db",              fallback="").strip()
    max_mb_raw      = config.get("logging", "max_log_mb",               fallback="0").strip()
    local_only_raw  = config.get("logging", "announce_log_local_only",  fallback="true").strip().lower()
    _announce_local_only = local_only_raw in ("true", "1", "yes")

    _channel_path  = os.path.expanduser(channel_raw)  if channel_raw  else None
    _dm_path       = os.path.expanduser(dm_raw)       if dm_raw       else None
    _announce_path = os.path.expanduser(announce_raw) if announce_raw else None

    try:
        _max_bytes = int(float(max_mb_raw) * 1024 * 1024)
    except ValueError:
        _max_bytes = 0

    for path, label in (
        (_channel_path,  "channel log"),
        (_dm_path,       "dm log"),
        (_announce_path, "announce log (text)"),
    ):
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print(f"[logger] {label}: {path}")

    if announce_db_raw:
        try:
            expanded = os.path.expanduser(announce_db_raw)
            _announce_db_dir = os.path.dirname(expanded)
            os.makedirs(_announce_db_dir, exist_ok=True)
            print(f"[logger] announce db dir: {_announce_db_dir} (per-protocol DBs)")
            _init_telemetry_db(os.path.join(_announce_db_dir, "telemetry.db"))
        except Exception as e:
            print(f"[logger] announce db init error: {e}")

    if _announce_path and _announce_local_only:
        print("[logger] announce log: local-only mode (LXMF TCP announces skipped)")
    if _max_bytes:
        print(f"[logger] max log size: {_max_bytes // (1024*1024)} MB per file")


def _disk_usage(path):
    """Return fraction of disk used (0.0–1.0) for the filesystem containing path."""
    st = os.statvfs(path)
    total = st.f_blocks
    if total == 0:
        return 0.0
    used = total - st.f_bavail
    return used / total


def _trim(path, reason):
    """Drop the oldest _TRIM_FRACTION of lines from path, atomically."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        drop = max(1, int(len(lines) * _TRIM_FRACTION))
        kept = lines[drop:]
        dir_ = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=dir_)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        print(f"[logger] {reason} — trimmed {drop} lines from {os.path.basename(path)}")
    except Exception as e:
        print(f"[logger] trim error ({path}): {e}")


def _maybe_trim():
    """Check disk usage and file sizes; trim as needed. Called under _lock."""
    global _writes_since_check, _last_check

    _writes_since_check += 1
    now = time.monotonic()
    do_disk_check = (
        _writes_since_check >= _CHECK_WRITES or
        (now - _last_check) >= _CHECK_INTERVAL
    )

    if do_disk_check:
        _writes_since_check = 0
        _last_check = now

    disk_over = False
    if do_disk_check:
        probe = _channel_path or _dm_path or _announce_path
        if probe:
            probe_dir = os.path.dirname(probe)
            if os.path.isdir(probe_dir):
                try:
                    disk_over = _disk_usage(probe_dir) >= _DISK_THRESHOLD
                except Exception:
                    pass

    for path in (_channel_path, _dm_path, _announce_path):
        if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
            continue

        size_over = _max_bytes > 0 and os.path.getsize(path) > _max_bytes

        if disk_over and size_over:
            _trim(path, "disk >90% and size cap exceeded")
        elif disk_over:
            _trim(path, "disk >90%")
        elif size_over:
            cap_label = f"{_max_bytes // (1024*1024)} MB" if _max_bytes >= 1024*1024 else f"{_max_bytes // 1024} KB"
            _trim(path, f"size cap ({cap_label})")


def _write(path, line):
    try:
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _maybe_trim()
    except Exception as e:
        print(f"[logger] write error ({path}): {e}")


def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_channel(proto, addr, text, chan=None, long_name=None, short_name=None, hops=None):
    if not _channel_path:
        return
    # If proto already contains a slash (e.g. "meshcore/Public"), use it directly
    tag = proto if ("/" in proto or chan is None) else f"{proto}/chan{chan}"

    # Normalise address to lowercase
    addr = addr.lower() if addr else addr

    # Drop names that are just the address repeated (e.g. meshcore sending hex addr as name)
    addr_stripped = addr.lstrip("!").lower()
    if long_name and long_name.lower().lstrip("!") == addr_stripped:
        long_name = None
    if short_name and short_name.lower().lstrip("!") == addr_stripped:
        short_name = None
    # Drop short_name if it duplicates long_name
    if short_name and long_name and short_name.lower() == long_name.lower():
        short_name = None

    line = f"{_ts()} [{tag}] <{addr}>"

    # Name section: "LongName (ShortName)" — no pipes inside parens
    if long_name and short_name:
        line += f" {long_name} ({short_name})"
    elif long_name:
        line += f" {long_name}"
    elif short_name:
        line += f" ({short_name})"

    # Hops as compact "+N" suffix before the message separator
    if hops is not None:
        line += f" +{hops}"

    line += f" | {text}"
    _write(_channel_path, line)


def log_dm(proto, sender, text, nick=None):
    if not _dm_path:
        return
    line = f"{_ts()} [{proto}/dm] <{sender}>"
    if nick:
        line += f" {nick} |"
    line += f" {text}"
    _write(_dm_path, line)


def backfill_nicks(proto, addr_nick_map):
    """Set nick on existing DB rows that have nick=NULL. Never inserts new rows.

    Returns the number of rows updated.
    """
    conn = _get_announce_conn(proto)
    if conn is None or not addr_nick_map:
        return 0
    try:
        count = 0
        with _lock:
            for addr, nick in addr_nick_map.items():
                cur = conn.execute(
                    "UPDATE announces SET nick=? WHERE proto=? AND addr=? AND nick IS NULL",
                    (nick, proto, addr)
                )
                count += cur.rowcount
            conn.commit()
        return count
    except Exception as e:
        print(f"[logger] backfill_nicks error: {e}")
        return 0


def get_named_addrs(proto=None):
    """Return the set of addrs that already have a nick in the announce DB."""
    try:
        conns = [_get_announce_conn(proto)] if proto else list(_announce_conns.values())
        conns = [c for c in conns if c is not None]
        if not conns:
            return set()
        result = set()
        with _lock:
            for conn in conns:
                rows = conn.execute(
                    "SELECT DISTINCT addr FROM announces WHERE nick IS NOT NULL"
                ).fetchall()
                result.update(r[0] for r in rows)
        return result
    except Exception:
        return set()


def _init_telemetry_db(path):
    global _telemetry_conn
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                  REAL    NOT NULL,
            proto               TEXT    NOT NULL,
            addr                TEXT    NOT NULL,
            battery_level       INTEGER,
            voltage             REAL,
            channel_utilization REAL,
            air_util_tx         REAL,
            temperature         REAL,
            relative_humidity   REAL,
            barometric_pressure REAL,
            rssi                REAL,
            snr                 REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tel_addr_ts ON telemetry (addr, ts DESC)")
    conn.commit()
    _telemetry_conn = conn
    print(f"[logger] telemetry db: {path}")


def log_telemetry(proto, addr, *, battery_level=None, voltage=None,
                  channel_utilization=None, air_util_tx=None,
                  temperature=None, relative_humidity=None,
                  barometric_pressure=None, rssi=None, snr=None):
    if _telemetry_conn is None:
        return
    try:
        with _lock:
            _telemetry_conn.execute("""
                INSERT INTO telemetry
                  (ts, proto, addr, battery_level, voltage,
                   channel_utilization, air_util_tx,
                   temperature, relative_humidity, barometric_pressure,
                   rssi, snr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (time.time(), proto, addr,
                  battery_level, voltage,
                  channel_utilization, air_util_tx,
                  temperature, relative_humidity, barometric_pressure,
                  rssi, snr))
            _telemetry_conn.commit()
    except Exception as e:
        print(f"[logger] telemetry write error: {e}")


def log_announce(proto, addr, *, nick=None, short_name=None, lat=None, lon=None, alt=None,
                 rssi=None, snr=None, hops=None, battery=None, modem_preset=None):
    now = time.time()
    sig = (nick, short_name, lat, lon, rssi, snr, hops, battery)

    conn = _get_announce_conn(proto)
    if conn is not None:
        try:
            with _lock:
                row = conn.execute(
                    "SELECT ts, nick, short_name, lat, lon, rssi, snr, hops, battery "
                    "FROM announces WHERE addr=? ORDER BY ts DESC LIMIT 1",
                    (addr,)
                ).fetchone()
                ts_insert = now
                if row:
                    last_t    = row[0]
                    last_nick = row[1]
                    last_sig  = tuple(row[1:])
                    # Always allow through if we're adding a name that wasn't there before.
                    # Preserve the original timestamp so the "last seen" time reflects when
                    # the node was actually heard, not when the name became known (e.g. on reboot).
                    adding_nick = nick and not last_nick
                    if adding_nick:
                        ts_insert = last_t
                    else:
                        if (now - last_t) < _ANNOUNCE_COOLDOWN and sig == last_sig:
                            return
                conn.execute(
                    "INSERT INTO announces "
                    "(ts, proto, addr, nick, short_name, lat, lon, alt, rssi, snr, hops, battery, modem_preset) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts_insert, proto, addr, nick, short_name, lat, lon, alt, rssi, snr, hops, battery, modem_preset)
                )
                # Keep last _ANNOUNCE_MAX_HIST rows per address
                conn.execute("""
                    DELETE FROM announces WHERE addr=? AND id NOT IN (
                        SELECT id FROM announces WHERE addr=? ORDER BY ts DESC LIMIT ?
                    )
                """, (addr, addr, _ANNOUNCE_MAX_HIST))
                # Keep only _ANNOUNCE_MAX_NODES unique addresses per protocol (by most recent)
                conn.execute("""
                    DELETE FROM announces WHERE addr NOT IN (
                        SELECT addr FROM announces GROUP BY addr
                        ORDER BY MAX(ts) DESC LIMIT ?
                    )
                """, (_ANNOUNCE_MAX_NODES,))
                # Update weighted-average position estimate when GPS is present
                if lat is not None and lon is not None:
                    est = conn.execute(
                        "SELECT lat, lon, weight FROM position_estimates WHERE addr=?",
                        (addr,)
                    ).fetchone()
                    if est:
                        old_lat, old_lon, w = est
                        nw = w + 1.0
                        conn.execute(
                            "UPDATE position_estimates "
                            "SET lat=?, lon=?, weight=?, last_ts=?, proto=? WHERE addr=?",
                            ((old_lat * w + lat) / nw, (old_lon * w + lon) / nw,
                             nw, now, proto, addr)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO position_estimates "
                            "(addr, proto, lat, lon, weight, last_ts) VALUES (?,?,?,?,1.0,?)",
                            (addr, proto, lat, lon, now)
                        )
                conn.commit()
        except Exception as e:
            print(f"[logger] announce db write error: {e}")
        return

    # ── Legacy text log ───────────────────────────────────────
    if not _announce_path:
        return
    line = f"{_ts()} [{proto}] {addr}"
    if nick:
        line += f" ({nick})"
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
    if extras:
        line += " | " + " ".join(extras)
    _write(_announce_path, line)
