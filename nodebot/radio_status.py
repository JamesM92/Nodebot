import configparser
import json
import os
import time

_here = os.path.dirname(os.path.abspath(__file__))
_config_path = os.path.join(_here, "..", "config.ini")
_cfg = configparser.ConfigParser()
_cfg.read(_config_path)
_storage = os.path.expanduser(_cfg.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage"))

STATUS_FILE = os.path.join(_storage, "radio_status.json")


def update(name, status, error=None):
    """Write adapter status to radio_status.json. status: connected|disconnected|error"""
    try:
        existing = {}
        try:
            with open(STATUS_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass
        entry = {"status": status, "updated": int(time.time())}
        if error:
            entry["error"] = str(error)[:200]
        existing[name] = entry
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(existing, f)
    except Exception as e:
        print(f"[radio_status] write failed: {e}")
