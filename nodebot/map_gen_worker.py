#!/usr/bin/env python3
"""Standalone worker — runs map generation in the background and removes the lockfile."""
import sys
import os

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

try:
    from nodebot import map_gen
    map_gen.init(storage_path)
    if map_type == "paths":
        map_gen.generate_path_map(announce_db)
    elif map_type == "county":
        map_gen.generate_county_map(announce_db)
    else:
        map_gen.generate(announce_db)
finally:
    try:
        os.unlink(lockfile)
    except Exception:
        pass
