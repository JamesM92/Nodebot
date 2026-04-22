# /src/meshbridge/state.py

import time
import threading


class StateStore:
    """
    Lightweight in-memory state layer for MeshBridge.

    Responsibilities:
    - per-sender sessions
    - temporary relay state
    - shared bot metrics
    - safe thread access

    Designed to be swappable with SQLite/Redis later.
    """

    def __init__(self):

        self._lock = threading.RLock()

        self.sessions = {}   # sender -> dict
        self.metrics = {
            "messages_total": 0,
            "commands_total": 0,
            "start_time": time.time()
        }

        print("??? StateStore initialized")

    # =====================================================
    # METRICS
    # =====================================================

    def inc_message(self):

        with self._lock:
            self.metrics["messages_total"] += 1

    def inc_command(self):

        with self._lock:
            self.metrics["commands_total"] += 1

    def get_metrics(self):

        with self._lock:
            uptime = time.time() - self.metrics["start_time"]

            return {
                **self.metrics,
                "uptime_seconds": uptime
            }

    # =====================================================
    # SESSION STORAGE
    # =====================================================

    def set(self, sender, key, value):

        with self._lock:

            if sender not in self.sessions:
                self.sessions[sender] = {}

            self.sessions[sender][key] = {
                "value": value,
                "updated": time.time()
            }

    def get(self, sender, key, default=None):

        with self._lock:

            return (
                self.sessions.get(sender, {})
                .get(key, {})
                .get("value", default)
            )

    def delete(self, sender, key):

        with self._lock:

            if sender in self.sessions:
                self.sessions[sender].pop(key, None)

    def clear_sender(self, sender):

        with self._lock:
            self.sessions.pop(sender, None)

    # =====================================================
    # RELAY SUPPORT (lightweight hooks)
    # =====================================================

    def set_relay(self, sender, data):

        """
        Used by relay plugin to store cross-transport context.
        """

        self.set(sender, "relay", data)

    def get_relay(self, sender):

        return self.get(sender, "relay", None)

    def clear_relay(self, sender):

        self.delete(sender, "relay")

    # =====================================================
    # DEBUG / INSPECTION
    # =====================================================

    def debug_dump(self):

        with self._lock:

            return {
                "metrics": self.metrics,
                "sessions_count": len(self.sessions),
                "session_keys": {
                    s: list(data.keys())
                    for s, data in self.sessions.items()
                }
            }