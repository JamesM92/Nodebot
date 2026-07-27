import os
import re
import sqlite3
import threading
import time

_lock         = threading.Lock()
_conns        = {}       # db_key → sqlite3.Connection
_storage_path = None

_MAX_BYTES      = 100 * 1024 * 1024   # 100 MB per DB; override with messages_max_mb in [bot]
_ROWS_PER_TABLE = 10_000              # per-channel row cap; override with messages_rows_per_channel
_ROWS_KEEP      = 9_000               # trim to this when cap is hit
_write_counts   = {}                  # (db_key, table) → writes since last trim check
_CHECK_EVERY    = 25


def init(storage_path, config=None):
    global _storage_path, _MAX_BYTES, _ROWS_PER_TABLE, _ROWS_KEEP
    _storage_path = storage_path
    os.makedirs(storage_path, exist_ok=True)

    if config:
        try:
            mb = float(config.get("bot", "messages_max_mb", fallback="100"))
            _MAX_BYTES = int(mb * 1024 * 1024)
        except Exception:
            pass
        try:
            _ROWS_PER_TABLE = int(config.get("bot", "messages_rows_per_channel", fallback="10000"))
            _ROWS_KEEP = int(_ROWS_PER_TABLE * 0.9)
        except Exception:
            pass

    cap_mb = _MAX_BYTES // (1024 * 1024)
    existing = sorted(
        f for f in os.listdir(storage_path)
        if f.startswith("messages_") and f.endswith(".db")
    )
    if existing:
        for fname in existing:
            db_key = fname[len("messages_"):-len(".db")]
            conn   = _open_db(db_key)
            n      = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
            mb     = _db_bytes(db_key) / (1024 * 1024)
            print(f"[messages] {fname}: {n} channels, {mb:.1f} / {cap_mb} MB")
    else:
        print(f"[messages] storage: {storage_path} ({cap_mb} MB/db, {_ROWS_PER_TABLE} rows/channel)")


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize(s):
    """Lowercase alphanumeric + underscore; strip leading/trailing underscores."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "unknown"


def _split_tag(tag):
    """Derive (db_key, table_name) from a proto_tag.

    'meshcore/Public'   → ('meshcore',       'public')
    'meshcore/0'        → ('meshcore',       'ch_0')
    'meshtastic:LF'     → ('meshtastic_lf',  'broadcast')
    'meshcore/dm'       → ('meshcore',       'dm')
    'lxmf/dm'           → ('lxmf',           'dm')
    """
    if "/" in tag:
        proto, chan = tag.split("/", 1)
        db_key = _sanitize(proto)
        table  = _sanitize(chan)
        if table and table[0].isdigit():
            table = "ch_" + table
    else:
        db_key = _sanitize(tag)
        table  = "broadcast"
    return db_key, table or "unknown"


def _open_db(db_key):
    """Return (possibly cached) connection for this protocol DB. Under _lock or at init."""
    if db_key in _conns:
        return _conns[db_key]
    path = os.path.join(_storage_path, f"messages_{db_key}.db")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            name      TEXT PRIMARY KEY,
            display   TEXT,
            first_msg INTEGER,
            last_msg  INTEGER,
            msg_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    _conns[db_key] = conn
    return conn


def _ensure_table(conn, name, display=None):
    """Create per-channel table if absent. Under _lock."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{name}" (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         INTEGER NOT NULL,
            addr       TEXT    NOT NULL,
            nick       TEXT,
            short_name TEXT,
            text       TEXT    NOT NULL,
            hops       INTEGER,
            rssi       REAL,
            snr        REAL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO channels (name, display, msg_count) VALUES (?, ?, 0)",
        (name, display or name)
    )


def _db_bytes(db_key):
    conn = _conns.get(db_key)
    if not conn:
        return 0
    try:
        row = conn.execute(
            "SELECT page_count * page_size "
            "FROM pragma_page_count(), pragma_page_size()"
        ).fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    try:
        return os.path.getsize(os.path.join(_storage_path, f"messages_{db_key}.db"))
    except OSError:
        return 0


def _maybe_trim(db_key, table):
    """Enforce per-table row cap then per-DB size cap. Under _lock."""
    key = (db_key, table)
    _write_counts[key] = _write_counts.get(key, 0) + 1
    if _write_counts[key] < _CHECK_EVERY:
        return
    _write_counts[key] = 0

    conn = _conns[db_key]

    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if n > _ROWS_PER_TABLE:
        to_drop = n - _ROWS_KEEP
        conn.execute(f"""
            DELETE FROM "{table}" WHERE id IN (
                SELECT id FROM "{table}" ORDER BY id ASC LIMIT ?
            )
        """, (to_drop,))
        print(f"[messages] trimmed {to_drop} rows from {db_key}/{table} (was {n})")

    if _db_bytes(db_key) > _MAX_BYTES:
        _evict_db(db_key, conn)


def _evict_db(db_key, conn):
    """Drop oldest rows from every table in this DB when the file size cap is hit."""
    names = [r[0] for r in conn.execute("SELECT name FROM channels").fetchall()]
    if not names:
        return
    per_table = max(100, 2000 // len(names))
    for name in names:
        conn.execute(f"""
            DELETE FROM "{name}" WHERE id IN (
                SELECT id FROM "{name}" ORDER BY id ASC LIMIT ?
            )
        """, (per_table,))
    conn.commit()
    mb = _db_bytes(db_key) / (1024 * 1024)
    print(
        f"[messages] {db_key}: evicted ~{per_table} rows/channel "
        f"({mb:.1f} / {_MAX_BYTES // (1024*1024)} MB)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def log(tag, addr, text, *, display=None, nick=None, short_name=None,
        hops=None, rssi=None, snr=None):
    """Write a channel message.

    tag — proto_tag string, e.g. 'meshcore/Public', 'meshtastic:LF', 'meshcore/dm'
    """
    if not _storage_path or not text:
        return
    db_key, table = _split_tag(tag)
    now = int(time.time())
    with _lock:
        conn = _open_db(db_key)
        _ensure_table(conn, table, display)
        conn.execute(
            f'INSERT INTO "{table}" '
            f'(ts, addr, nick, short_name, text, hops, rssi, snr) '
            f'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (now, addr, nick, short_name, text, hops, rssi, snr)
        )
        conn.execute(
            "UPDATE channels SET last_msg=?, first_msg=COALESCE(first_msg, ?), "
            "msg_count=msg_count+1 WHERE name=?",
            (now, now, table)
        )
        conn.commit()
        _maybe_trim(db_key, table)


def log_dm(proto, addr, text, *, nick=None):
    """Write a DM into messages_{proto}.db, table 'dm'.

    proto — e.g. 'meshcore', 'meshtastic:LF', 'lxmf'
    """
    log(f"{proto}/dm", addr, text, nick=nick, display="DM")


def recent(tag, limit=50):
    """Return last `limit` messages from a channel, newest-first, as list of dicts."""
    if not _storage_path:
        return []
    db_key, table = _split_tag(tag)
    with _lock:
        conn = _open_db(db_key)
        try:
            rows = conn.execute(
                f'SELECT id, ts, addr, nick, short_name, text, hops, rssi, snr '
                f'FROM "{table}" ORDER BY id DESC LIMIT ?',
                (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def channel_list(proto=None):
    """Return channel metadata rows, optionally filtered to one protocol DB.

    Each row: {'proto': db_key, 'name': table, 'display': ..., 'last_msg': ts, 'msg_count': n}
    """
    if not _storage_path:
        return []
    results = []
    with _lock:
        keys = [_sanitize(proto)] if proto else list(_conns.keys())
        for db_key in keys:
            conn = _conns.get(db_key)
            if not conn:
                continue
            rows = conn.execute(
                "SELECT name, display, first_msg, last_msg, msg_count "
                "FROM channels ORDER BY last_msg DESC"
            ).fetchall()
            for r in rows:
                results.append({**dict(r), "proto": db_key})
    return results
