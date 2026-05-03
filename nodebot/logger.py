# logger.py — optional append-only log files for channel and DM traffic

import os
import tempfile
import threading
import time

_lock = threading.Lock()
_channel_path  = None
_dm_path       = None
_announce_path = None

_DISK_THRESHOLD = 0.90   # fraction — trigger trim above this
_TRIM_FRACTION  = 0.25   # drop oldest 25% of lines when trimming
_CHECK_INTERVAL = 60     # seconds between disk-space checks
_CHECK_WRITES   = 100    # also check after this many writes

_max_bytes = 0           # 0 = disabled; set from max_log_mb config
_writes_since_check = 0
_last_check = 0.0

# When True, skip all LXMF announce logging. NodeBot runs as a shared-instance
# client so all announces arrive via the same local socket — LoRa vs TCP origin
# is indistinguishable. Set False to log all LXMF announces (LoRa and TCP alike).
_announce_local_only = True


def init(config):
    global _channel_path, _dm_path, _announce_path, _max_bytes, _announce_local_only

    channel_raw  = config.get("logging", "channel_log",           fallback="").strip()
    dm_raw       = config.get("logging", "dm_log",                fallback="").strip()
    announce_raw = config.get("logging", "announce_log",          fallback="").strip()
    max_mb_raw   = config.get("logging", "max_log_mb",            fallback="0").strip()
    local_only_raw = config.get("logging", "announce_log_local_only", fallback="true").strip().lower()
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
        (_announce_path, "announce log"),
    ):
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print(f"[logger] {label}: {path}")
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
    """Check disk usage and file sizes; trim as needed. Called under _lock.

    Disk usage (statvfs) is sampled on the interval — relatively expensive.
    File size (stat) is checked on every write — cheap enough to always run.
    """
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


def log_channel(proto, sender, text, chan=None):
    if not _channel_path:
        return
    tag = f"{proto}/chan{chan}" if chan is not None else proto
    _write(_channel_path, f"{_ts()} [{tag}] <{sender}> {text}")


def log_dm(proto, sender, text):
    if not _dm_path:
        return
    _write(_dm_path, f"{_ts()} [{proto}/dm] <{sender}> {text}")


def log_announce(proto, addr, *, nick=None, lat=None, lon=None, alt=None,
                 rssi=None, snr=None, hops=None, battery=None):
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
