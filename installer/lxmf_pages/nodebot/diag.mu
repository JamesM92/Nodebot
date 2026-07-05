#!/usr/bin/python3
# NodeBot NomadNet system diagnostics page — deployed by install_lxmf.sh
#
# NOTE: PROJECT_DIR_PLACEHOLDER below is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run the installer.

import os
import json
import subprocess
import time
import configparser
import re

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")

config = configparser.ConfigParser()
config.read(CONFIG_PATH)

bot_name = config.get("bot", "name", fallback="NodeBot").strip()
storage  = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage").strip()
)
STATUS_FILE = os.path.join(storage, "radio_status.json")

# ── Helpers ───────────────────────────────────────────────────

def _age(ts):
    delta = int(time.time()) - int(ts)
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        m = delta // 60
        s = delta % 60
        return f"{m}m {s}s ago"
    if delta < 86400:
        h = delta // 3600
        m = (delta % 3600) // 60
        return f"{h}h {m}m ago"
    d = delta // 86400
    h = (delta % 86400) // 3600
    return f"{d}d {h}h ago"

def _dur(ts):
    """Elapsed time since ts, without 'ago'."""
    return _age(ts).replace(" ago", "")

def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))

def _radio_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _service_info():
    try:
        r = subprocess.run(
            ["systemctl", "show", "nodebot",
             "--property=ActiveState,SubState,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        props = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v.strip()
        return props
    except Exception:
        return {}

def _sys_stats():
    stats = {}
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        d, rem = divmod(int(secs), 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            stats["uptime"] = f"{d}d {h}h {m}m"
        elif h:
            stats["uptime"] = f"{h}h {m}m"
        else:
            stats["uptime"] = f"{m}m"
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            raw = f.read()
        total = int(re.search(r"MemTotal:\s+(\d+)", raw).group(1))
        avail = int(re.search(r"MemAvailable:\s+(\d+)", raw).group(1))
        used  = total - avail
        pct   = 100 * used // total
        stats["memory"] = f"{used // 1024} MB / {total // 1024} MB  ({pct}%)"
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            stats["cpu_temp"] = f"{int(f.read().strip()) / 1000:.1f} °C"
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
        stats["load"] = f"{p[0]}  {p[1]}  {p[2]}"
    except Exception:
        pass
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        parts = r.stdout.strip().splitlines()[-1].split()
        stats["disk"] = f"{parts[2]} / {parts[1]}  ({parts[4]} used)"
    except Exception:
        pass
    return stats

def _tty_devices():
    """Return list of (symlink_name, real_dev) for configured radio ports,
    plus any unclaimed /dev/tty* devices."""
    configured = {}
    for sec in config.sections():
        port = config.get(sec, "port", fallback="").strip()
        if port and os.path.exists(port):
            try:
                real = os.path.realpath(port)
                configured[os.path.basename(port)] = (sec, port, real)
            except Exception:
                configured[os.path.basename(port)] = (sec, port, port)

    # Also find any TTY devices not claimed by a configured adapter
    unclaimed = []
    import glob
    for dev in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        real = os.path.realpath(dev)
        claimed = any(real == r for _, _, r in configured.values())
        if not claimed:
            unclaimed.append(dev)

    return configured, unclaimed

# ── Radio labels (config section → display name) ──────────────
_RADIO_LABELS = {
    "meshcore":    "MeshCore",
    "lxmf":        "LXMF / RNS",
}

def _radio_label(name):
    if name in _RADIO_LABELS:
        return _RADIO_LABELS[name]
    if name.startswith("meshtastic"):
        sec = name
        preset = config.get(sec, "modem_preset", fallback="").strip().upper() if config.has_section(sec) else ""
        abbr   = {"LONG_FAST": "LF", "MEDIUM_FAST": "MF", "LONG_SLOW": "LS",
                  "MEDIUM_SLOW": "MS", "SHORT_FAST": "SF"}.get(preset, preset[:4] if preset else "")
        suffix = f" ({abbr})" if abbr else ""
        return f"Meshtastic{suffix}"
    return name

def _status_color(status):
    return {"connected": "F4f6", "disconnected": "FF60", "error": "FF00"}.get(status, "F888")

# ── Fetch all data ─────────────────────────────────────────────
now_str   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
radio_st  = _radio_status()
svc       = _service_info()
sys_stats = _sys_stats()
cfg_devs, unclaimed = _tty_devices()

p = print

# ─────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────
p(f"`!`F4af{bot_name}`f — System Diagnostics`!")
p(f"`F888{now_str}`f")
p("`l")
p("")

# ─────────────────────────────────────────────────────────────
# NodeBot Service
# ─────────────────────────────────────────────────────────────
p(">NodeBot Service")
p("")

active    = svc.get("ActiveState", "unknown")
sub       = svc.get("SubState",    "unknown")
enter_ts  = svc.get("ActiveEnterTimestamp", "")

svc_color = "F4f6" if active == "active" else "FF00"
svc_sym   = "●" if active == "active" else "○"
p(f"  `{svc_color}{svc_sym} nodebot.service  [{active} / {sub}]`f")

if enter_ts and enter_ts != "n/a":
    try:
        # systemd format: "Sat 2026-07-05 18:53:11 EDT"
        # Strip day-of-week and timezone for parsing
        parts = enter_ts.split()
        if len(parts) >= 4:
            ts_str = " ".join(parts[1:3])  # "2026-07-05 18:53:11"
            import datetime
            dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            epoch = dt.timestamp()
            p(f"  `F888since {ts_str}  ({_dur(epoch)} up)`f")
    except Exception:
        p(f"  `F888since {enter_ts}`f")

p("")

# ─────────────────────────────────────────────────────────────
# Radio Status
# ─────────────────────────────────────────────────────────────
p(">Radio Status")
p("")

# Canonical order: meshcore, meshtastic, meshtastic1, ..., lxmf
_order = ["meshcore"] + sorted(
    [k for k in radio_st if k.startswith("meshtastic")]
) + ["lxmf"] + sorted(
    [k for k in radio_st if k not in ("meshcore", "lxmf") and not k.startswith("meshtastic")]
)
seen = set()
ordered_radios = [k for k in _order if k in radio_st and k not in seen and not seen.add(k)]

if not ordered_radios:
    p("  `F888No radio status data yet — start NodeBot once to populate`f")
else:
    for radio_name in ordered_radios:
        entry  = radio_st[radio_name]
        status = entry.get("status", "unknown")
        upd    = entry.get("updated", 0)
        err    = entry.get("error", "")
        events = entry.get("events", [])

        label  = _radio_label(radio_name)
        col    = _status_color(status)
        sym    = "●" if status == "connected" else ("○" if status == "disconnected" else "!")

        # Port from config
        port_str = ""
        if config.has_section(radio_name):
            port = config.get(radio_name, "port", fallback="").strip()
            if port:
                port_str = f"  `F888{port}`f"

        p(f"  `{col}{sym} {label}`f{port_str}")
        p(f"  `F888status: `f`{col}{status}`f  `F888— {_age(upd)}`f")
        if err:
            p(f"  `FFF0error: {err[:80]}`f")

        if events:
            p(f"  `F888event history:`f")
            for ev in reversed(events):
                ev_col = _status_color(ev.get("status", ""))
                ev_sym = "●" if ev.get("status") == "connected" else "○"
                ev_ts  = _fmt_ts(ev.get("ts", 0))
                ev_age = _age(ev.get("ts", 0))
                p(f"    `F888{ev_ts}`f  `{ev_col}{ev_sym} {ev.get('status','?')}`f  `F888({ev_age})`f")
        p("")

# ─────────────────────────────────────────────────────────────
# USB / TTY Devices
# ─────────────────────────────────────────────────────────────
p(">USB / TTY Devices")
p("")

if cfg_devs:
    for sym_name, (sec, link, real) in sorted(cfg_devs.items()):
        real_base = os.path.basename(real)
        label     = _radio_label(sec)
        p(f"  `F4f6● `f`Ffa6{os.path.basename(link)}`f → `F888{real_base}`f  `Faaa({label})`f")
else:
    p("  `F888No configured radio devices are currently present`f")

if unclaimed:
    for dev in unclaimed:
        p(f"  `FF60○ `f`Ffa6{os.path.basename(dev)}`f  `F888(no configured adapter)`f")

p("")

# ─────────────────────────────────────────────────────────────
# System Info
# ─────────────────────────────────────────────────────────────
p(">System")
p("")

_labels = [
    ("uptime",   "Pi uptime"),
    ("cpu_temp", "CPU temp"),
    ("memory",   "Memory"),
    ("load",     "Load (1/5/15m)"),
    ("disk",     "Disk (/)"),
]
for key, label in _labels:
    if key in sys_stats:
        p(f"  `F888{label:<14}`f  {sys_stats[key]}")

p("")
p("`l")
p("`Fbbf`[← Back to NodeBot`:/page/nodebot/nodebot.mu`]`f")
p("")
