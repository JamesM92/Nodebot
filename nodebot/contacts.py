import os
import sqlite3
import threading
import time

_lock = threading.Lock()
_conn = None
_db_path = None

_MAX_BYTES   = 100 * 1024 * 1024   # 100 MB default; override with contacts_max_mb in [bot]
_EVICT_ROWS  = 2000                 # events to drop per enforcement pass
_CHECK_EVERY = 50                   # enforce cap after this many upserts
_write_count = 0

# Minimum seconds between successive event rows of the same type for the same contact.
# DMs are always logged. Adverts / position / nodeinfo are rate-limited so a node
# that announces every 30 s doesn't flood the events table.
_EVENT_COOLDOWN = {
    "dm":       0,
    "advert":   300,   # 5 min
    "position": 300,   # 5 min
    "nodeinfo": 600,   # 10 min
    "channel":  600,   # 10 min
}

# In-memory last-event timestamps: (proto, addr, event_type) → ts
_event_last = {}


def init(storage_path, config=None):
    global _conn, _db_path, _MAX_BYTES
    os.makedirs(storage_path, exist_ok=True)
    _db_path = os.path.join(storage_path, "contacts.db")
    conn = sqlite3.connect(_db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            proto      TEXT    NOT NULL,
            addr       TEXT    NOT NULL,
            pubkey     TEXT,
            name       TEXT,
            short_name TEXT,
            lat        REAL,
            lon        REAL,
            alt        REAL,
            hops       INTEGER,
            rssi       REAL,
            snr        REAL,
            battery    INTEGER,
            first_seen INTEGER NOT NULL,
            last_seen  INTEGER NOT NULL,
            PRIMARY KEY (proto, addr)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         INTEGER NOT NULL,
            proto      TEXT    NOT NULL,
            addr       TEXT    NOT NULL,
            event_type TEXT    NOT NULL,
            name       TEXT,
            short_name TEXT,
            lat        REAL,
            lon        REAL,
            alt        REAL,
            hops       INTEGER,
            rssi       REAL,
            snr        REAL,
            battery    INTEGER
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_proto_addr_ts "
        "ON contact_events (proto, addr, ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_ts ON contact_events (ts)"
    )
    conn.commit()
    _conn = conn

    if config:
        try:
            mb = float(config.get("bot", "contacts_max_mb", fallback="100"))
            _MAX_BYTES = int(mb * 1024 * 1024)
        except Exception:
            pass

    n_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    n_events   = conn.execute("SELECT COUNT(*) FROM contact_events").fetchone()[0]
    db_mb = _db_bytes() / (1024 * 1024)
    print(
        f"[contacts] db: {_db_path} "
        f"({n_contacts} contacts, {n_events} events, {db_mb:.1f} MB / {_MAX_BYTES // (1024*1024)} MB cap)"
    )


def _db_bytes():
    row = _conn.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()").fetchone()
    if row:
        return row[0]
    # Fallback to file size when the PRAGMA cross-join isn't available
    try:
        return os.path.getsize(_db_path)
    except OSError:
        return 0


def _enforce_cap():
    """Evict oldest contact_events rows when the DB exceeds _MAX_BYTES. Called under _lock."""
    global _write_count
    _write_count += 1
    if _write_count < _CHECK_EVERY:
        return
    _write_count = 0

    if _db_bytes() <= _MAX_BYTES:
        return

    _conn.execute("""
        DELETE FROM contact_events WHERE id IN (
            SELECT id FROM contact_events ORDER BY id ASC LIMIT ?
        )
    """, (_EVICT_ROWS,))
    _conn.commit()
    mb_after = _db_bytes() / (1024 * 1024)
    print(
        f"[contacts] evicted {_EVICT_ROWS} oldest events "
        f"(db now {mb_after:.1f} MB, cap={_MAX_BYTES // (1024*1024)} MB)"
    )


def upsert(proto, addr, *, event_type=None, pubkey=None, name=None, short_name=None,
           lat=None, lon=None, alt=None, hops=None, rssi=None, snr=None, battery=None):
    if not _conn:
        return
    now = int(time.time())
    with _lock:
        # Update current-state table
        _conn.execute("""
            INSERT INTO contacts
                (proto, addr, pubkey, name, short_name, lat, lon, alt,
                 hops, rssi, snr, battery, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (proto, addr) DO UPDATE SET
                pubkey     = COALESCE(excluded.pubkey,     pubkey),
                name       = COALESCE(excluded.name,       name),
                short_name = COALESCE(excluded.short_name, short_name),
                lat        = COALESCE(excluded.lat,        lat),
                lon        = COALESCE(excluded.lon,        lon),
                alt        = COALESCE(excluded.alt,        alt),
                hops       = COALESCE(excluded.hops,       hops),
                rssi       = COALESCE(excluded.rssi,       rssi),
                snr        = COALESCE(excluded.snr,        snr),
                battery    = COALESCE(excluded.battery,    battery),
                last_seen  = excluded.last_seen
        """, (proto, addr, pubkey, name, short_name, lat, lon, alt,
              hops, rssi, snr, battery, now, now))

        # Append to history table if an event type was given and cooldown has passed
        if event_type:
            cooldown = _EVENT_COOLDOWN.get(event_type, 300)
            key = (proto, addr, event_type)
            last = _event_last.get(key, 0)
            if cooldown == 0 or (now - last) >= cooldown:
                _event_last[key] = now
                _conn.execute("""
                    INSERT INTO contact_events
                        (ts, proto, addr, event_type, name, short_name,
                         lat, lon, alt, hops, rssi, snr, battery)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, proto, addr, event_type, name, short_name,
                      lat, lon, alt, hops, rssi, snr, battery))

        _conn.commit()
        _enforce_cap()


def seen_set():
    """Return set of 'proto:addr' strings for all stored contacts."""
    if not _conn:
        return set()
    with _lock:
        rows = _conn.execute("SELECT proto, addr FROM contacts").fetchall()
    return {f"{r[0]}:{r[1]}" for r in rows}


def get_name(proto, addr):
    """Return stored display name for proto:addr, or None."""
    if not _conn:
        return None
    with _lock:
        row = _conn.execute(
            "SELECT name FROM contacts WHERE proto=? AND addr=?", (proto, addr)
        ).fetchone()
    return row[0] if row else None


def get_pubkey(proto, addr):
    """Return stored full pubkey for proto:addr, or None."""
    if not _conn:
        return None
    with _lock:
        row = _conn.execute(
            "SELECT pubkey FROM contacts WHERE proto=? AND addr=?", (proto, addr)
        ).fetchone()
    return row[0] if row else None
