"""Persistent store for observed mesh paths (Path: strings from MeshCore).

Each row records a unique (relay_path, sender) pair with the bot's own ID,
first/last seen timestamps, and occurrence count.
"""
import os
import re
import sqlite3
import threading
import time as _time

_conn    = None
_lock    = threading.Lock()
_PATH_RE = re.compile(r'Path:\s*([0-9a-fA-F]{2,8}(?:,[0-9a-fA-F]{2,8})+)')


def init(storage_path):
    global _conn
    db_path = os.path.join(storage_path, "paths.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    with _lock:
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS paths (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                path_str   TEXT    NOT NULL,
                sender_id  TEXT    NOT NULL DEFAULT '',
                our_id     TEXT,
                first_seen INTEGER NOT NULL,
                last_seen  INTEGER NOT NULL,
                count      INTEGER NOT NULL DEFAULT 1,
                UNIQUE(path_str, sender_id)
            )
        """)
        _conn.commit()


def log(text, sender_id=None, our_id=None):
    """Extract and store any Path: string found in message text.

    sender_id: hex prefix of the node that originated the path message.
    our_id:    hex prefix of our bot node (receiver / path endpoint).
    """
    if not _conn or not text or 'Path:' not in text:
        return
    m = _PATH_RE.search(text)
    if not m:
        return
    path_str = m.group(1).lower()
    sid = (sender_id or '').lower()
    ts = int(_time.time())
    with _lock:
        try:
            _conn.execute("""
                INSERT INTO paths (path_str, sender_id, our_id, first_seen, last_seen, count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(path_str, sender_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    our_id    = COALESCE(excluded.our_id, our_id),
                    count     = count + 1
            """, (path_str, sid, our_id, ts, ts))
            _conn.commit()
        except Exception:
            pass
