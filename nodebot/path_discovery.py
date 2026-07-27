"""Background path discovery for MeshCore — periodic path surveys of GPS nodes.

Tracks contact attempts in a SQLite DB and logs results to path_discovery.log.
Candidate selection: GPS nodes from the announce DB that have no entry in paths.db
as a sender, sorted oldest-first (longest in DB, least recently heard from).

Status lifecycle:
  (no row)  → contacted  → responded
                         → no_response (timeout after 30 min)
  Nodes flagged no_response or reached _MAX_ATTEMPTS are skipped forever.
"""

import os
import sqlite3
import threading
import time

_conn      = None
_lock      = threading.Lock()
_log_path  = None

_TIMEOUT_SECS = 30 * 60
_MAX_ATTEMPTS = 3


def init(storage_path):
    global _conn, _log_path
    db_path   = os.path.join(storage_path, "path_discovery.db")
    _log_path = os.path.join(storage_path, "path_discovery.log")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    with _lock:
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS discoveries (
                addr          TEXT NOT NULL,
                proto         TEXT NOT NULL DEFAULT 'meshcore',
                status        TEXT NOT NULL DEFAULT 'pending',
                first_attempt REAL,
                last_attempt  REAL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                note          TEXT,
                PRIMARY KEY (addr, proto)
            )
        """)
        _conn.commit()
    _write_log(f"[init] db={db_path}")


def _write_log(msg):
    if not _log_path:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def expire_stale_contacts():
    """Mark contacted-but-silent entries as no_response after timeout."""
    if not _conn:
        return 0
    cutoff = time.time() - _TIMEOUT_SECS
    with _lock:
        cur = _conn.execute("""
            UPDATE discoveries
            SET status = 'no_response', note = 'timeout'
            WHERE status = 'contacted' AND last_attempt < ?
        """, (cutoff,))
        n = cur.rowcount
        _conn.commit()
    if n:
        _write_log(f"[expire] flagged {n} node(s) no_response (timeout)")
    return n


def mark_contacted(addr, proto='meshcore'):
    """Record that we sent a path-discovery request to addr."""
    if not _conn:
        return
    now = time.time()
    with _lock:
        _conn.execute("""
            INSERT INTO discoveries
                (addr, proto, status, first_attempt, last_attempt, attempt_count)
            VALUES (?, ?, 'contacted', ?, ?, 1)
            ON CONFLICT(addr, proto) DO UPDATE SET
                status        = 'contacted',
                last_attempt  = excluded.last_attempt,
                attempt_count = attempt_count + 1,
                first_attempt = COALESCE(first_attempt, excluded.first_attempt)
        """, (addr, proto, now, now))
        _conn.commit()
    _write_log(f"[contact] path request sent → {addr} ({proto})")


def mark_responded(addr, proto='meshcore', path_str=None):
    """Record that addr returned a path response."""
    if not _conn:
        return
    with _lock:
        _conn.execute("""
            INSERT INTO discoveries (addr, proto, status)
            VALUES (?, ?, 'responded')
            ON CONFLICT(addr, proto) DO UPDATE SET status = 'responded'
        """, (addr, proto))
        _conn.commit()
    note = f" path={path_str}" if path_str else ""
    _write_log(f"[response] path received ← {addr} ({proto}){note}")


def get_next_candidate(announce_db_files, paths_db_path):
    """Return the 12-char addr of the best uncontacted GPS node to query.

    Returns None when no suitable candidate exists.
    """
    if not _conn:
        return None

    # 6-char prefixes of nodes already known as path senders
    known_senders = set()
    if paths_db_path and os.path.isfile(paths_db_path):
        try:
            pc = sqlite3.connect(f"file:{paths_db_path}?mode=ro", uri=True)
            for (sid,) in pc.execute(
                "SELECT DISTINCT sender_id FROM paths WHERE sender_id != ''"
            ).fetchall():
                if sid:
                    known_senders.add(sid.lower()[:6])
            pc.close()
        except Exception:
            pass

    # GPS nodes from announce DBs, oldest-first
    candidates = []
    for f in announce_db_files:
        if not os.path.isfile(f):
            continue
        try:
            ac = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            rows = ac.execute("""
                SELECT addr, MIN(ts) AS first_ts FROM announces
                WHERE lat IS NOT NULL AND lon IS NOT NULL
                GROUP BY addr ORDER BY first_ts ASC
            """).fetchall()
            ac.close()
            for addr, first_ts in rows:
                a = addr.lower()
                if a[:6] not in known_senders:
                    candidates.append((first_ts, a))
        except Exception:
            pass

    if not candidates:
        return None

    candidates.sort()

    one_hour_ago = time.time() - 3600
    with _lock:
        for _, addr in candidates:
            row = _conn.execute(
                "SELECT status, attempt_count, last_attempt "
                "FROM discoveries WHERE addr=? AND proto='meshcore'",
                (addr,)
            ).fetchone()
            if row:
                status, count, last_attempt = row
                if status in ('responded', 'no_response'):
                    continue
                if count >= _MAX_ATTEMPTS:
                    continue
                if last_attempt and last_attempt > one_hour_ago:
                    continue
            return addr
    return None
