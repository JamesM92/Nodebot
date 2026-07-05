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
import glob
import datetime

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")
RNS_CONFIG  = os.path.expanduser("~/.reticulum/config")
RNSTATUS    = os.path.join(PROJECT_DIR, ".venv/bin/rnstatus")
if not os.path.isfile(RNSTATUS):
    RNSTATUS = "rnstatus"

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
        m, s = divmod(delta, 60)
        return f"{m}m {s}s ago"
    if delta < 86400:
        h, rem = divmod(delta, 3600)
        return f"{h}h {rem // 60}m ago"
    d, rem = divmod(delta, 86400)
    return f"{d}d {rem // 3600}h ago"

def _dur(ts):
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
        stats["uptime"] = (f"{d}d {h}h {rem // 60}m" if d else
                           f"{h}h {rem // 60}m"       if h else
                           f"{rem // 60}m")
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            raw = f.read()
        total = int(re.search(r"MemTotal:\s+(\d+)", raw).group(1))
        avail = int(re.search(r"MemAvailable:\s+(\d+)", raw).group(1))
        used  = total - avail
        stats["memory"] = f"{used // 1024} MB / {total // 1024} MB  ({100 * used // total}%)"
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

def _rns_ports():
    """Return {iface_name: port_path} from ~/.reticulum/config for RNS-managed serial devices."""
    ports = {}
    try:
        with open(RNS_CONFIG) as f:
            lines = f.readlines()
        current = None
        for line in lines:
            stripped = line.strip()
            m = re.match(r'^\[\[(.+)\]\]', stripped)
            if m:
                current = m.group(1)
            elif current and re.match(r'^port\s*=', stripped):
                port = stripped.split("=", 1)[1].strip()
                if port:
                    ports[current] = port
    except Exception:
        pass
    return ports

RNS_CACHE      = os.path.join(storage, "rns_status_cache.txt")
RNS_CACHE_TTL  = 60  # seconds


def _refresh_rns_cache():
    """Run rnstatus in the background and write output to cache file."""
    try:
        r = subprocess.run([RNSTATUS], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            with open(RNS_CACHE, "w") as f:
                f.write(r.stdout)
    except Exception:
        pass


def _parse_rnstatus():
    """Read rnstatus from cache (refreshing in background if stale)."""
    cache_age = None
    text      = None

    try:
        mtime     = os.path.getmtime(RNS_CACHE)
        cache_age = time.time() - mtime
        with open(RNS_CACHE) as f:
            text = f.read()
    except Exception:
        pass

    if text is None:
        # Cache file missing — run synchronously once to prime it
        try:
            r    = subprocess.run([RNSTATUS], capture_output=True, text=True, timeout=10)
            text = r.stdout
            if text.strip():
                with open(RNS_CACHE, "w") as f:
                    f.write(text)
                cache_age = 0
        except Exception:
            return None, None, None
    elif cache_age is not None and cache_age >= RNS_CACHE_TTL:
        # Cache exists but stale — serve it now, refresh in background
        import threading
        threading.Thread(target=_refresh_rns_cache, daemon=True).start()

    if not text:
        return None, None, None

    interfaces = []
    rns_uptime = None
    current  = None
    prev_key = None

    for line in text.splitlines():
        # Footer: " Uptime is ..."
        m = re.match(r'^\s+Uptime is (.+)$', line)
        if m:
            rns_uptime = m.group(1).strip()
            continue

        # Block header: " TypeName[Name/detail]"
        m = re.match(r'^\s+([A-Za-z ]+)\[(.+)\]', line)
        if m:
            if current is not None:
                interfaces.append(current)
            current  = {"type": m.group(1).strip(), "name": m.group(2).strip(), "props": {}}
            prev_key = None
            continue

        if current is not None:
            # Key : Value line
            m = re.match(r'^\s{4,}([A-Za-z /.()\d]+?)\s*:\s*(.+)$', line)
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                current["props"][key] = val
                prev_key = key
                continue

            # Continuation line (indented value with no key, e.g. the RX traffic row)
            cont = re.match(r'^\s{16,}(\S.*)$', line)
            if cont and prev_key:
                current["props"][prev_key + " cont"] = cont.group(1).strip()

    if current is not None:
        interfaces.append(current)

    return interfaces, rns_uptime, cache_age

def _tty_devices(rns_ports):
    """Return (configured_dict, unclaimed_list).
    configured_dict: {symlink_basename: (label, link_path, real_dev)}
    Covers both NodeBot config.ini and RNS ~/.reticulum/config ports.
    """
    configured = {}
    claimed_reals = set()

    # NodeBot adapters
    for sec in config.sections():
        port = config.get(sec, "port", fallback="").strip()
        if port and os.path.exists(port):
            real = os.path.realpath(port)
            configured[os.path.basename(port)] = (_radio_label(sec), port, real)
            claimed_reals.add(real)

    # RNS-managed interfaces (RNode, etc.)
    for iface_name, port in rns_ports.items():
        if not os.path.exists(port):
            continue
        real = os.path.realpath(port)
        if real not in claimed_reals:
            configured[os.path.basename(port)] = (f"RNS ({iface_name})", port, real)
            claimed_reals.add(real)

    # Unclaimed
    unclaimed = []
    for dev in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        if os.path.realpath(dev) not in claimed_reals:
            unclaimed.append(dev)

    return configured, unclaimed

# ── Radio labels ──────────────────────────────────────────────

def _radio_label(name):
    if name == "meshcore":
        return "MeshCore"
    if name == "lxmf":
        return "LXMF / RNS"
    if name.startswith("meshtastic"):
        preset = config.get(name, "modem_preset", fallback="").strip().upper() if config.has_section(name) else ""
        abbr   = {"LONG_FAST": "LF", "MEDIUM_FAST": "MF", "LONG_SLOW": "LS",
                  "MEDIUM_SLOW": "MS", "SHORT_FAST": "SF"}.get(preset, preset[:4] if preset else "")
        return f"Meshtastic ({abbr})" if abbr else "Meshtastic"
    return name

def _status_color(status):
    return {"connected": "F4f6", "disconnected": "FF60", "error": "FF00"}.get(status, "F888")

# ── Fetch all data ─────────────────────────────────────────────
now_str        = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
radio_st       = _radio_status()
svc            = _service_info()
sys_stats      = _sys_stats()
rns_ports      = _rns_ports()
rns_ifaces, rns_uptime, rns_cache_age = _parse_rnstatus()
cfg_devs, unclaimed    = _tty_devices(rns_ports)

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

active   = svc.get("ActiveState", "unknown")
sub      = svc.get("SubState",    "unknown")
enter_ts = svc.get("ActiveEnterTimestamp", "")

svc_col = "F4f6" if active == "active" else "FF00"
svc_sym = "●"    if active == "active" else "○"
p(f"  `{svc_col}{svc_sym} nodebot.service  [{active} / {sub}]`f")

if enter_ts and enter_ts not in ("n/a", ""):
    try:
        parts  = enter_ts.split()
        ts_str = " ".join(parts[1:3])
        epoch  = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
        p(f"  `F888since {ts_str}  ({_dur(epoch)} up)`f")
    except Exception:
        p(f"  `F888since {enter_ts}`f")

p("")

# ─────────────────────────────────────────────────────────────
# Radio Status  (NodeBot adapter layer)
# ─────────────────────────────────────────────────────────────
p(">Radio Status")
p("")

_order = (["meshcore"]
          + sorted(k for k in radio_st if k.startswith("meshtastic"))
          + ["lxmf"]
          + sorted(k for k in radio_st
                   if k not in ("meshcore", "lxmf") and not k.startswith("meshtastic")))
seen = set()
ordered_radios = [k for k in _order if k in radio_st and not (k in seen or seen.add(k))]

if not ordered_radios:
    p("  `F888No radio status data yet — start NodeBot once to populate`f")
else:
    for radio_name in ordered_radios:
        entry  = radio_st[radio_name]
        status = entry.get("status", "unknown")
        upd    = entry.get("updated", 0)
        err    = entry.get("error", "")
        events = entry.get("events", [])

        col    = _status_color(status)
        sym    = "●" if status == "connected" else ("○" if status == "disconnected" else "!")
        label  = _radio_label(radio_name)

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
                p(f"    `F888{_fmt_ts(ev.get('ts', 0))}`f  "
                  f"`{ev_col}{ev_sym} {ev.get('status','?')}`f  "
                  f"`F888({_age(ev.get('ts', 0))})`f")
        p("")

# ─────────────────────────────────────────────────────────────
# RNS Interfaces  (RNode radio + TCP links)
# ─────────────────────────────────────────────────────────────
rns_age_str = (f"  `F888cached {int(rns_cache_age)}s ago`f" if rns_cache_age and rns_cache_age > 1 else "")
p(f">RNS Interfaces{('  (live)' if not rns_age_str else '')}")
p("")

if rns_ifaces is None:
    p("  `F888rnstatus unavailable`f")
else:
    if rns_uptime:
        p(f"  `F888RNS uptime: {rns_uptime}`f" + (f"  {rns_age_str}" if rns_age_str else ""))
        p("")

    for iface in rns_ifaces:
        itype = iface["type"]
        name  = iface["name"]
        props = iface["props"]

        # Skip the shared instance — it's internal bookkeeping, not a radio
        if "Shared Instance" in itype:
            continue

        status_val = props.get("Status", "?")
        col = "F4f6" if status_val.lower() == "up" else "FF00"
        sym = "●"    if status_val.lower() == "up" else "○"

        # Friendly type tag
        if "RNode" in itype:
            type_tag = "RNode"
        elif "TCP" in itype:
            type_tag = "TCP"
        elif "UDP" in itype:
            type_tag = "UDP"
        else:
            type_tag = itype

        p(f"  `{col}{sym} {name}`f  `F888[{type_tag}]`f")
        p(f"  `F888status: `f`{col}{status_val}`f", end="")

        mode = props.get("Mode")
        rate = props.get("Rate")
        if mode:
            p(f"  `F888mode: {mode}`f", end="")
        if rate:
            p(f"  `F888rate: {rate}`f", end="")
        p("")

        # RNode-specific RF metrics
        if "RNode" in itype:
            port_for_iface = rns_ports.get(name, "")
            if port_for_iface:
                real_tty = os.path.basename(os.path.realpath(port_for_iface)) if os.path.exists(port_for_iface) else "?"
                p(f"  `F888port: {port_for_iface} → {real_tty}`f")

            for key, label in [
                ("Noise Fl.",  "noise floor"),
                ("Intrfrnc.",  "interference"),
                ("Airtime",    "airtime"),
                ("Ch. Load",   "ch. load"),
                ("CPU temp",   "RNode temp"),
                ("Battery",    "battery"),
            ]:
                val = props.get(key)
                if val:
                    p(f"  `F888{label:<14}`f  {val}")

        # Traffic (values already contain ↑/↓ symbols from rnstatus)
        tx = props.get("Traffic")
        rx = props.get("Traffic cont")
        if tx:
            p(f"  `F888{'traffic':<14}`f  {tx}")
        if rx:
            p(f"  `F888{'':<14}`f  {rx}")

        p("")

# ─────────────────────────────────────────────────────────────
# USB / TTY Devices
# ─────────────────────────────────────────────────────────────
p(">USB / TTY Devices")
p("")

if cfg_devs:
    for sym_name, (label, link, real) in sorted(cfg_devs.items()):
        real_base = os.path.basename(real)
        p(f"  `F4f6● `f`Ffa6{os.path.basename(link)}`f → `F888{real_base}`f  `Faaa({label})`f")
else:
    p("  `F888No configured radio devices are currently present`f")

if unclaimed:
    for dev in unclaimed:
        p(f"  `FF60○ `f`Ffa6{os.path.basename(dev)}`f  `F888(no configured adapter)`f")

p("")

# ─────────────────────────────────────────────────────────────
# System
# ─────────────────────────────────────────────────────────────
p(">System")
p("")

for key, label in [("uptime", "Pi uptime"), ("cpu_temp", "CPU temp"),
                   ("memory", "Memory"), ("load", "Load (1/5/15m)"), ("disk", "Disk (/)")]:
    if key in sys_stats:
        p(f"  `F888{label:<14}`f  {sys_stats[key]}")

p("")
p("`l")
p("`Fbbf`[← Back to NodeBot`:/page/nodebot/nodebot.mu`]`f")
p("")
