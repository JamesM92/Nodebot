#!/usr/bin/python3
# NodeBot NomadNet admin page — DM / relay log review
# Not linked from any other page. Requires session password.
# NOTE: PROJECT_DIR_PLACEHOLDER is substituted at deploy time.

import os
import hmac
import json
import re
import time as _time
import configparser

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"

CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

storage_path   = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage"))
bot_name       = config.get("bot", "name", fallback="NodeBot").strip()
admin_password = config.get("admin", "password", fallback="").strip()
dm_log_raw     = config.get("logging", "dm_log", fallback="").strip()
dm_log_path    = os.path.expanduser(dm_log_raw) if dm_log_raw else ""

SESSION_FILE = os.path.join(storage_path, "admin_sessions.json")
SESSION_TTL  = 8 * 3600

# ── Request context ───────────────────────────────────────────────────────────
link_id    = os.environ.get("link_id", "")
field_pw   = os.environ.get("field_password", "")
field_days = max(1, min(int(os.environ.get("field_days", "30") or "30"), 365))
cutoff_ts  = _time.time() - field_days * 86400

# ── Session store ─────────────────────────────────────────────────────────────
def _sessions():
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save(sessions):
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception:
        pass

def _is_authed():
    if not link_id:
        return False
    return (_time.time() - _sessions().get(link_id, 0)) < SESSION_TTL

def _grant():
    now = _time.time()
    s   = {k: v for k, v in _sessions().items() if (now - v) < SESSION_TTL}
    s[link_id] = now
    _save(s)

# ── Auth ──────────────────────────────────────────────────────────────────────
authed = _is_authed()
bad_pw = False

if not authed and field_pw:
    if admin_password and hmac.compare_digest(field_pw, admin_password):
        _grant()
        authed = True
    else:
        bad_pw = True

# ── Log parser ────────────────────────────────────────────────────────────────
# Line formats (all written by logger.py):
#   inbound DM:   YYYY-MM-DD HH:MM:SS [proto/dm] <addr> [Nick |] text
#   outbound DM:  YYYY-MM-DD HH:MM:SS [proto/dm>] <addr> text
#   relay fwd:    YYYY-MM-DD HH:MM:SS [relay] <src> > dst | text

_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[([^\]]+)\] (.+)$'
)
_DM_IN_RE  = re.compile(r'^<([^>]+)> (?:(.+?) \| )?(.+)$')
_DM_OUT_RE = re.compile(r'^<([^>]+)> (.+)$')
_RELAY_RE  = re.compile(r'^<([^>]+)> > ([^ |]+) \| (.+)$')


def _parse_log(path, since_ts):
    """Return list of (ts_float, kind, addr, peer, text) sorted oldest-first.

    kind: 'in', 'out', 'relay'
    addr: primary party (sender for 'in', recipient for 'out', source for 'relay')
    peer: relay destination (only for 'relay')
    """
    entries = []
    if not path or not os.path.isfile(path):
        return entries

    try:
        # Tail: read last 200 KB to avoid loading giant logs
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - 200 * 1024)
            f.seek(start)
            if start > 0:
                f.readline()   # skip partial first line
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return entries

    for line in raw.splitlines():
        m = _LINE_RE.match(line.rstrip())
        if not m:
            continue
        ts_str, tag, rest = m.group(1), m.group(2), m.group(3)
        try:
            ts = _time.mktime(_time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if ts < since_ts:
            continue

        if tag.endswith("/dm>"):
            # outbound bot reply
            proto = tag[:-len("/dm>")]
            mo = _DM_OUT_RE.match(rest)
            if mo:
                entries.append((ts, "out", proto, mo.group(1), None, mo.group(2)))

        elif tag.endswith("/dm"):
            # inbound DM
            proto = tag[:-len("/dm")]
            mo = _DM_IN_RE.match(rest)
            if mo:
                nick = mo.group(2) or ""
                entries.append((ts, "in", proto, mo.group(1), nick, mo.group(3)))

        elif tag == "relay":
            mo = _RELAY_RE.match(rest)
            if mo:
                entries.append((ts, "relay", "relay", mo.group(1), mo.group(2), mo.group(3)))

    return entries


def _wrap(text, width=58):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            if cur:
                lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


# ── Render ────────────────────────────────────────────────────────────────────
print(f"`!`F4af{bot_name}`f — Admin`f")
print("-")

if not authed:
    if bad_pw:
        print("`Fcc4  Incorrect password`f")
        print("")
    if not admin_password:
        print("`Fcc4  No password configured`f")
        print("`F888  Add  password = <value>  under [admin] in config.ini`f")
    else:
        print("`F888  Password`f")
        print("`B333`<!|password`>`b")
        print("")
        print("`Fbbf`[  Authenticate`:/page/nodebot/admin.mu`password]`f")

else:
    entries = _parse_log(dm_log_path, cutoff_ts)

    # Group by conversation key: (proto, addr) for DMs, (src, dst) for relay
    # Build ordered list of (conv_key, last_ts, rows)
    convs   = {}   # key -> [rows]
    key_ord = []   # insertion order

    for row in entries:
        ts, kind, proto, addr, peer, text = row
        if kind == "relay":
            key = ("relay", addr, peer or "")
        else:
            key = (proto, addr, "")
        if key not in convs:
            convs[key] = []
            key_ord.append(key)
        convs[key].append(row)

    # Sort conversations by most recent message
    key_ord.sort(key=lambda k: -convs[k][-1][0])

    total = sum(len(v) for v in convs.values())
    relay_count = sum(1 for k in convs if k[0] == "relay")
    dm_count    = total - relay_count

    print(f"`F888{total} messages  ·  {dm_count} DM  {relay_count} relay  ·  last {field_days} days`f")

    if not dm_log_path:
        print("`Fcc4  dm_log not configured in config.ini`f")
    elif not os.path.isfile(dm_log_path):
        print("`Fcc4  dm.log not found — outbound logging active after next bot restart`f")

    print("-")

    if not key_ord:
        print("`F888  No messages in this window`f")
    else:
        shown = 0
        for key in key_ord:
            if shown >= 20:
                remaining = len(key_ord) - shown
                print("")
                print(f"`F888  … {remaining} more conversations — narrow the window`f")
                break

            rows = convs[key]
            kind0 = rows[0][1]
            proto, addr, peer = key

            # Conversation header
            print("")
            if kind0 == "relay":
                print(f"`Faaa{addr}`F888 > `Faaa{peer}`F888  [relay]`f")
            else:
                last_nick = next((r[4] for r in reversed(rows) if r[4]), "")
                label = last_nick or addr
                print(f"`Faaa{label}`F888  {addr}  ({proto})`f")

            shown += 1

            for ts, kind, proto, addr, nick, text in rows[-30:]:
                dt = _time.strftime("%m-%d %H:%M", _time.localtime(ts))
                if kind == "in":
                    arrow = "`Faaa←`f"
                    txt_color = "`Fddd"
                elif kind == "out":
                    arrow = "`F4af→`f"
                    txt_color = "`Fbbf"
                else:
                    arrow = "`F888↔`f"
                    txt_color = "`F888"

                lines = _wrap(text)
                print(f"`F888{dt}  `f{arrow}  {txt_color}{lines[0]}`f")
                for cont in lines[1:]:
                    print(f"              {txt_color}{cont}`f")

    print("")
    print("-")
    print("`F888Days back`f")
    print(f"`B333`<days`{field_days}>`b")
    print("")
    print("`Fbbf`[  Refresh`:/page/nodebot/admin.mu`days]`f")

print("")
print("-")
print(f"`F888{_time.strftime('%Y-%m-%d %H:%M UTC', _time.gmtime())}`f")
print("")
