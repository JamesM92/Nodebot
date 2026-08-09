#!/usr/bin/env python3
"""Standalone worker — runs map generation in the background and removes the lockfile."""
import configparser as _cp
import os
import sys

if len(sys.argv) < 4:
    sys.exit(1)

# Lower scheduling priority so the bot and NomadNet stay responsive during rendering.
try:
    os.nice(10)
except OSError:
    pass

# Cap numpy / scipy / OpenBLAS thread pools to 1 so they don't spawn one thread
# per CPU core and peg the processor.  Must be set before any numpy import.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_var] = "1"

announce_db  = sys.argv[1]
storage_path = sys.argv[2]
lockfile     = sys.argv[3]
map_type     = sys.argv[4] if len(sys.argv) > 4 else "node"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# How many active (non-outage) days to show on the maps.  The lookback window
# is extended automatically to skip over any downtime gaps.
_cfg = _cp.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), "..", "config.ini"))
try:
    _target_days = int(_cfg.get("logging", "map_active_days", fallback="7"))
except (ValueError, _cp.Error):
    _target_days = 7

try:
    from nodebot import map_gen
    map_gen.init(storage_path)
    # Dynamically extend the lookback to cover _target_days of actual activity,
    # automatically skipping over any outage gaps.
    _ann_files = map_gen._announce_db_files(announce_db)
    _days = map_gen._active_lookback_days(_ann_files, target_days=_target_days)
    if map_type == "paths":
        map_gen.generate_path_map(announce_db, days=_days)
    elif map_type == "county":
        map_gen.generate_county_map(announce_db, days=_days)
    else:
        map_gen.generate(announce_db, days=_days)
finally:
    try:
        os.unlink(lockfile)
    except Exception:
        pass
