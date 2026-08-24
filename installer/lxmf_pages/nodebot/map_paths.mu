#!/usr/bin/python3
# NodeBot NomadNet path map page — deployed by install_lxmf.sh
# NOTE: PROJECT_DIR_PLACEHOLDER is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run /deploy-map.

import os
import sys
import time
import configparser
import subprocess

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"

CONFIG_PATH  = os.path.join(PROJECT_DIR, "config.ini")
config       = configparser.ConfigParser()
config.read(CONFIG_PATH)

storage_path = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage"))
announce_db  = os.path.expanduser(
    config.get("logging", "announce_db", fallback="").strip())
bot_name     = config.get("bot", "name", fallback="NodeBot").strip()

NOMAD_FILES  = os.path.expanduser("~/.nomadnetwork/storage/files")
MAP_PATH     = os.path.join(NOMAD_FILES, "nodebot", "map_paths.png")
LOCKFILE     = os.path.join(storage_path, "map_paths_generating.lock")
MAP_MAX_AGE  = 1800  # seconds (30 min)
LOCK_TIMEOUT = 120   # treat lock as stale after this

VENV_PYTHON  = os.path.join(PROJECT_DIR, ".venv", "bin", "python3")
WORKER       = os.path.join(PROJECT_DIR, "nodebot", "map_gen_worker.py")


def _map_age():
    if not os.path.isfile(MAP_PATH):
        return None
    if os.path.getsize(MAP_PATH) < 1024:
        return None
    return time.time() - os.path.getmtime(MAP_PATH)


def _is_generating():
    if not os.path.isfile(LOCKFILE):
        return False
    return (time.time() - os.path.getmtime(LOCKFILE)) < LOCK_TIMEOUT


def _start_generation():
    if os.path.isfile(LOCKFILE):
        try:
            os.unlink(LOCKFILE)
        except OSError:
            pass
    with open(LOCKFILE, "w") as f:
        f.write("")
    subprocess.Popen(
        [VENV_PYTHON, WORKER, announce_db, storage_path, LOCKFILE, "paths"],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ── Render ────────────────────────────────────────────────────
print(f"`!`F4af{bot_name}`f — Path Map`f")
print("-")

age   = _map_age()
fresh = age is not None and age <= MAP_MAX_AGE

if fresh:
    mins = int(age // 60)
    secs = int(age % 60)
    print(f"`F888Map generated {mins}m {secs}s ago`f")
    print("")
    print("`F4af`!Download`f")
    print("-")
    print("`Faaa  Click below to download the PNG to your device:`f")
    print("")
    print("`F4af`[  ⬇  map_paths.png`:/file/nodebot/map_paths.png`]`f")
    print("")
    print("`F888Bright green = GPS node  •  Yellow = estimated position from path data`f")
    print("`F888Line weight indicates how frequently that link was traversed.`f")
    print("")
    print("`F888Map will auto-regenerate after 5 minutes on next visit.`f")

elif _is_generating():
    print("`F888Path map generation is in progress…`f")
    print("")
    print("`Faaa  Usually takes 5–15 seconds. Refresh to check.`f")
    print("")
    print("`Fbbf`[  ↻  Refresh`:/page/nodebot/map_paths.mu`]`f")

else:
    _start_generation()
    print("`F888Path map generation started in the background.`f")
    print("")
    print("`Faaa  Refresh this page in ~15 seconds to get the download link.`f")
    if age is None:
        print("`Faaa  (First run also fetches state boundary data ~500 KB if not cached)`f")
    print("")
    print("`Fbbf`[  ↻  Refresh`:/page/nodebot/map_paths.mu`]`f")

print("-")
print("`Fbbf`[← Back to Activity`:/page/nodebot/activity.mu`]`f  "
      "`Fbbf`[🔵 Node Map`:/page/nodebot/map.mu`]`f  "
      "`Fbbf`[📊 County Map`:/page/nodebot/county.mu`]`f  "
      "`Fbbf`[📋 Digest`:/page/nodebot/digest.mu`]`f")
print("")
