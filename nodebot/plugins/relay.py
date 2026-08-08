
# plugins/relay.py

import configparser
import json
import os
import time
from ..commands import register, BOT_INSTANCE
from .. import contacts as _contacts
from .. import logger as _logger

# =====================================================
# PERSISTENCE
# =====================================================

def _compute_state_file():
    _here = os.path.dirname(os.path.abspath(__file__))
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_here, "..", "..", "config.ini"))
    raw = cfg.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage")
    return os.path.join(os.path.expanduser(os.path.dirname(raw)), "relay_state.json")

_STATE_FILE = _compute_state_file()


def _load_state():
    global BLOCKED_USERS, TIMED_BLOCKS, CONTACT_ALIASES
    try:
        with open(_STATE_FILE, "r") as f:
            data = json.load(f)
        BLOCKED_USERS   = set(data.get("blocked_users", []))
        TIMED_BLOCKS    = {k: float(v) for k, v in data.get("timed_blocks", {}).items()}
        CONTACT_ALIASES = data.get("contact_aliases", {})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[relay] state load error: {e}")


def _save_state():
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump({
                "blocked_users":   list(BLOCKED_USERS),
                "timed_blocks":    TIMED_BLOCKS,
                "contact_aliases": CONTACT_ALIASES,
            }, f, indent=2)
    except Exception as e:
        print(f"[relay] state save error: {e}")


# =====================================================
# STATE
# =====================================================

LAST_CONTACT = {}
SEEN_USERS = _contacts.seen_set()
ACTIVE_REPLY_SESSION = set()
BLOCKED_USERS = set()          # permanent blocks
TIMED_BLOCKS = {}              # addr -> expiry timestamp
RECENT_RELAYS = {}
MESSAGE_HISTORY = {}
SESSION_TIMESTAMPS = {}  # key -> last activity timestamp
CONTACT_ALIASES = {}     # name -> proto:addr

LOOP_TIMEOUT = 30
SESSION_TTL  = 3600  # seconds of inactivity before a session expires

_load_state()


# =====================================================
# HELPERS
# =====================================================

def parse_target(target):
    if ":" not in target:
        return None, None
    proto, addr = target.split(":", 1)
    return proto.lower(), addr.lower()


# Per-protocol address lengths.
#   short_len — prefix used after first contact (minimum accepted length)
#   full_len  — full pubkey length accepted for first/cold contact
_PROTO_ADDR = {
    #          short  full
    "lxmf": (  16,    32),
    "mc":   (  12,    64),
    "mesh": (   8,    64),
}


def _resolve_addr(proto, addr):
    """Resolve an address for relay — prefix lookup or first-contact full pubkey.

    Returns (send_addr, note, error_str).
    send_addr and error_str are mutually exclusive (exactly one is non-None).
    note is set whenever a full pubkey is given (known or not) to remind the
    sender of the prefix for future messages.
    """
    info = _PROTO_ADDR.get(proto)
    if info is None:
        return addr, None, None   # unknown protocol — pass through

    short_len, full_len = info
    addr = addr.lower()
    n = len(addr)

    if n < short_len:
        if full_len > short_len:
            return None, None, (
                f"{proto} addresses must be {short_len} chars (prefix) "
                f"or {full_len} chars (full pubkey for first contact)."
            )
        return None, None, f"{proto} addresses must be exactly {short_len} chars."

    if n == short_len:
        if f"{proto}:{addr}" not in SEEN_USERS:
            if full_len > short_len:
                return None, None, (
                    f"'{addr}' hasn't been seen by NodeBot. "
                    f"Provide the full {full_len}-char pubkey for first contact."
                )
            return None, None, (
                f"'{addr}' hasn't been seen by NodeBot. "
                f"They must message NodeBot before you can relay to them."
            )
        return addr, None, None

    # Between prefix and full — only LXMF allows an intermediate prefix search
    if n < full_len:
        if proto != "lxmf":
            return None, None, (
                f"Use the {short_len}-char prefix or full {full_len}-char pubkey for {proto}."
            )
        matches = [s[len(proto) + 1:] for s in SEEN_USERS
                   if s.startswith(f"{proto}:{addr}")]
        if not matches:
            return None, None, (
                f"No {proto} contact matching '{addr}' has been seen by NodeBot. "
                f"Provide the full {full_len}-char address for first contact."
            )
        if len(matches) > 1:
            return None, None, (
                f"Prefix '{addr}' matches {len(matches)} {proto} contacts — use more characters."
            )
        return matches[0], None, None

    if n == full_len:
        prefix = addr[:short_len]
        note = f"Use relay {proto}:{prefix} for future messages."
        return prefix, note, None

    return None, None, f"Address too long for {proto} (max {full_len} chars)."


def format_message(sender, message):
    if isinstance(sender, (bytes, bytearray)):
        sender = "lxmf:" + sender.hex()
    if ":" in sender:
        proto, addr = sender.split(":", 1)
    else:
        proto, addr = "unknown", sender
    return f"[{proto}]\n{addr}\n\n{message}\n\nRespond: <msg>"


def intro_message():
    return "Cross-network relay.\nReply: Respond: <msg>"


def is_blocked(addr):
    """Return True if addr is permanently or temporarily blocked."""
    if addr in BLOCKED_USERS:
        return True
    expiry = TIMED_BLOCKS.get(addr)
    if expiry is not None:
        if time.time() < expiry:
            return True
        del TIMED_BLOCKS[addr]   # expired — prune silently
    return False


def is_loop(sender, target):
    key = (sender, target)
    now = time.time()
    last = RECENT_RELAYS.get(key)
    if last and (now - last) < LOOP_TIMEOUT:
        return True
    RECENT_RELAYS[key] = now
    return False


def activate_session(user):
    ACTIVE_REPLY_SESSION.add(user)
    SESSION_TIMESTAMPS[user] = time.time()


def clear_session(user):
    ACTIVE_REPLY_SESSION.discard(user)
    LAST_CONTACT.pop(user, None)
    SESSION_TIMESTAMPS.pop(user, None)


def _expire_sessions():
    """Remove sessions that have been idle longer than SESSION_TTL."""
    now = time.time()
    expired = [k for k, ts in SESSION_TIMESTAMPS.items() if now - ts > SESSION_TTL]
    for key in expired:
        peer = LAST_CONTACT.get(key)
        clear_session(key)
        if peer:
            clear_session(peer)


def store_history(user, msg):
    MESSAGE_HISTORY.setdefault(user, [])
    MESSAGE_HISTORY[user].append(msg)
    MESSAGE_HISTORY[user] = MESSAGE_HISTORY[user][-10:]


def send_message(destination, text, notify_cb=None, sender=None):
    if hasattr(BOT_INSTANCE, "send"):
        BOT_INSTANCE.send(destination, text, notify_cb=notify_cb)
    if sender:
        _logger.log_relay(sender, destination, text)


def _make_delivery_cb(notify_sender):
    def _cb(success):
        import threading
        msg = "Relay: delivered" if success else "Relay: delivery failed"
        threading.Thread(target=send_message, args=(notify_sender, msg), daemon=True).start()
    return _cb


# Canonical info for each transport: (proto_prefix, display_name)
_PROTO_INFO = {
    "lxmf_adapter":        ("lxmf",  "LXMF/Reticulum"),
    "meshcore_adapter":    ("mc",    "MeshCore"),
    "meshtastic_adapter":  ("mesh",  "Meshtastic"),
    "meshtastic_adapter2": ("mesh",  "Meshtastic"),
}


def _own_addr(adapter_name, adapter):
    """Return NodeBot's own address prefix for an adapter, or None if unavailable."""
    try:
        if adapter_name == "lxmf_adapter":
            dd = getattr(adapter, "delivery_destination", None)
            if dd and dd.hash:
                return dd.hash.hex()[:16]
        elif adapter_name == "meshcore_adapter":
            mc = getattr(adapter, "_mc", None)
            info = (mc.self_info if mc else None) or {}
            pk = info.get("public_key", "")
            if pk:
                return pk[:12]
        elif adapter_name in ("meshtastic_adapter", "meshtastic_adapter2"):
            num = getattr(adapter, "_my_node_num", None)
            if num is not None:
                return f"{num:08x}"
    except Exception:
        pass
    return None


def _relay_help():
    """Return usage text listing only the transports currently loaded."""
    transports = getattr(BOT_INSTANCE, "transports", {}) if BOT_INSTANCE else {}

    lines = [
        "Cross-network relay.",
        "Usage: relay <protocol:address> <message>",
        "Chain: relay <nodebot> relay <target> <message>",
        "",
        "Address formats:",
        "  Full pubkey needed for first contact.",
        "  mc:<12-char prefix>   or 64-char pubkey",
        "  mesh:<8-char prefix>  or 64-char pubkey",
        "  lxmf:<16-char prefix> or 32-char pubkey",
    ]

    return "\n".join(lines)


def auto_forward(sender, message):
    """Forward a message from a relay session participant to their peer.

    Called by engine._handle_plugins when a non-command arrives from someone
    in an active relay session. Returns True if forwarded, False if no session.
    This is how reply chains work across multiple NodeBot hops.
    """
    if isinstance(sender, (bytes, bytearray)):
        sender = "lxmf:" + sender.hex()
    sender_str = str(sender).lower()

    session_key, normalized = _resolve_session(sender_str)
    if not session_key:
        return False

    _upgrade_session(session_key, normalized)
    effective_sender = normalized if normalized else session_key

    destination = LAST_CONTACT.get(effective_sender)
    if not destination:
        return False

    if is_loop(effective_sender, destination):
        return False

    store_history(effective_sender, message)
    store_history(destination, message)

    LAST_CONTACT[destination] = effective_sender
    activate_session(destination)
    SESSION_TIMESTAMPS[effective_sender] = time.time()

    send_message(destination, message, sender=effective_sender)
    return True


def _resolve_session(sender):
    """Find an existing session that matches sender.

    Expired sessions (idle > SESSION_TTL) are removed before lookup.

    Handles the case where the session was stored with a short proto-prefixed
    key (e.g. 'mc:091733a4') but the incoming sender is the full raw prefix
    without a protocol tag (e.g. '091733a4cc53').

    Returns (session_key, normalized_key) or (None, None).
    - session_key:    the key currently in ACTIVE_REPLY_SESSION
    - normalized_key: the full 'proto:addr' form to upgrade to
    """
    _expire_sessions()

    if isinstance(sender, (bytes, bytearray)):
        sender = "lxmf:" + sender.hex()

    sender_str = str(sender).lower()

    # Exact match (already normalised or fully tagged)
    if sender_str in ACTIVE_REPLY_SESSION:
        return sender_str, sender_str

    # Case-insensitive exact match on original value
    if sender in ACTIVE_REPLY_SESSION:
        return sender, sender

    # Prefix match: stored key is 'mc:091733a4', sender is '091733a4cc53'
    # (no colon — raw MeshCore pubkey prefix)
    if ":" not in sender_str:
        for key in list(ACTIVE_REPLY_SESSION):
            if ":" in key:
                k_proto, k_addr = key.split(":", 1)
                k_addr_lower = k_addr.lower()
                if sender_str.startswith(k_addr_lower) or k_addr_lower.startswith(sender_str):
                    return key, f"{k_proto}:{sender_str}"

    return None, None


def _upgrade_session(old_key, new_key):
    """Promote a partial session key to the full normalised key in-place."""
    if old_key == new_key or old_key not in ACTIVE_REPLY_SESSION:
        return
    dest = LAST_CONTACT.pop(old_key, None)
    ACTIVE_REPLY_SESSION.discard(old_key)
    ACTIVE_REPLY_SESSION.add(new_key)
    LAST_CONTACT[new_key] = dest
    if dest is not None:
        LAST_CONTACT[dest] = new_key


# =====================================================
# MAIN RELAY
# =====================================================

@register(
    "relay",
    (
        "Send cross-network message\n\n"
        "Usage: relay <protocol:address> <message>\n"
        "Chain: relay <nodebot> relay <target> <message>\n\n"
        "Run 'relay' with no args for active protocol examples."
    ),
    category="relay",
    cooldown=5
)
def relay_cmd(args, sender):

    norm = ("lxmf:" + sender.hex()) if isinstance(sender, (bytes, bytearray)) else str(sender)
    if is_blocked(norm):
        return "You are blocked from using relay."

    if len(args) < 2:
        return _relay_help()

    target_raw = args[0]
    message = " ".join(args[1:])

    # Resolve alias if target has no colon (not a proto:addr)
    if ":" not in target_raw:
        resolved = CONTACT_ALIASES.get(target_raw.lower())
        if resolved:
            target_raw = resolved
        else:
            return f"Unknown alias '{target_raw}'. Use relay <proto:addr> or add with: alias add <name> <proto:addr>"

    proto, addr = parse_target(target_raw)
    if not proto or not addr:
        return "Invalid format. Use protocol:address"

    raw_addr = addr
    addr, first_contact_note, err = _resolve_addr(proto, addr)
    if err:
        return err

    # If a full pubkey was given (first contact), persist it to the contacts DB now
    # so the prefix resolves across restarts even before the contact replies.
    if first_contact_note:
        proto_info = _PROTO_ADDR.get(proto)
        if proto_info and len(raw_addr) == proto_info[1]:
            _contacts.upsert(proto, addr, pubkey=raw_addr)

    destination = f"{proto}:{addr}"

    # Normalise sender to 'proto:addr' string for consistent storage
    if isinstance(sender, (bytes, bytearray)):
        norm_sender = "lxmf:" + sender.hex()
    elif ":" not in str(sender):
        norm_sender = "mc:" + str(sender).lower()
    else:
        norm_sender = str(sender).lower()

    if is_loop(norm_sender, destination):
        return "Relay blocked (loop detected)"

    LAST_CONTACT[norm_sender] = destination
    LAST_CONTACT[destination] = norm_sender

    activate_session(destination)
    SEEN_USERS.add(destination)
    _contacts.upsert(*destination.split(":", 1))

    # Chained relay: payload is itself a relay command. Send it raw so the
    # next NodeBot processes it as a command rather than a human message.
    if message.lower().startswith("relay "):
        payload = message
    else:
        payload = format_message(norm_sender, message)

    store_history(norm_sender, payload)
    store_history(destination, payload)

    send_message(destination, payload, notify_cb=_make_delivery_cb(norm_sender), sender=norm_sender)

    return first_contact_note


# =====================================================
# RESPOND (primary)
# =====================================================

@register(
    "respond",
    "Reply to last relay contact",
    category="relay",
    cooldown=2
)
def respond_cmd(args, sender):

    session_key, normalized = _resolve_session(sender)
    if not session_key:
        return "No active relay session."

    _upgrade_session(session_key, normalized)
    effective_sender = normalized

    if effective_sender not in LAST_CONTACT:
        return "No previous contact."

    if not args:
        return "Usage: respond <message>"

    destination = LAST_CONTACT[effective_sender]
    message = " ".join(args)

    if is_loop(effective_sender, destination):
        return "Relay blocked (loop detected)"

    LAST_CONTACT[destination] = effective_sender
    activate_session(destination)

    payload = format_message(effective_sender, message)

    store_history(effective_sender, payload)
    store_history(destination, payload)

    send_message(destination, payload, notify_cb=_make_delivery_cb(effective_sender), sender=effective_sender)

    return None


# =====================================================
# RESPOND: shortcut
# =====================================================

@register(
    "respond:",
    "Quick reply shortcut",
    category="relay",
    cooldown=1
)
def respond_colon(args, sender):

    session_key, normalized = _resolve_session(sender)
    if not session_key:
        return "No active relay session."

    _upgrade_session(session_key, normalized)
    effective_sender = normalized

    if effective_sender not in LAST_CONTACT:
        return "No previous contact."

    message = " ".join(args).strip()

    if not message:
        return "Usage: Respond: <message>"

    destination = LAST_CONTACT[effective_sender]

    if is_loop(effective_sender, destination):
        return "Relay blocked (loop detected)"

    LAST_CONTACT[destination] = effective_sender
    activate_session(destination)

    payload = format_message(effective_sender, message)

    store_history(effective_sender, payload)
    store_history(destination, payload)

    send_message(destination, payload, notify_cb=_make_delivery_cb(effective_sender), sender=effective_sender)

    return None


# =====================================================
# INFO COMMAND
# =====================================================

@register(
    "relayinfo",
    "Show current relay session info",
    category="relay",
    cooldown=2
)
def relay_info(args, sender):

    session_key, normalized = _resolve_session(sender)
    effective = normalized or session_key

    if not effective or effective not in LAST_CONTACT:
        return "No active relay session."

    return f"Active session:\n{LAST_CONTACT[effective]}"


# =====================================================
# CLEAR SESSION
# =====================================================

@register(
    "relayclear",
    "Clear current relay session",
    category="relay",
    cooldown=2
)
def relay_clear(args, sender):

    session_key, normalized = _resolve_session(sender)
    if session_key:
        clear_session(session_key)
    if normalized and normalized != session_key:
        clear_session(normalized)
    return "Relay session cleared."


# =====================================================
# BLOCK USER
# =====================================================

@register(
    "relayblock",
    "Block a sender from relay (optionally timed)",
    category="relay",
    admin=True,
    cooldown=1
)
def relay_block(args, sender):

    if not args:
        return "Usage: relayblock <proto:addr> [minutes]"

    target = args[0]
    if len(args) >= 2:
        try:
            minutes = float(args[1])
        except ValueError:
            return "minutes must be a number"
        TIMED_BLOCKS[target] = time.time() + minutes * 60
        _save_state()
        return f"Blocked {target} for {minutes:.0f}m"
    else:
        BLOCKED_USERS.add(target)
        _save_state()
        return f"Blocked {target} (permanent)"


@register(
    "relayunblock",
    "Unblock a sender from relay",
    category="relay",
    admin=True,
    cooldown=1
)
def relay_unblock(args, sender):

    if not args:
        return "Usage: relayunblock <protocol:address>"

    target = args[0]
    removed = False
    if target in BLOCKED_USERS:
        BLOCKED_USERS.discard(target)
        removed = True
    if target in TIMED_BLOCKS:
        del TIMED_BLOCKS[target]
        removed = True
    if not removed:
        return f"{target} is not blocked."
    _save_state()
    return f"Unblocked {target}"


@register(
    "relayblocked",
    "List blocked relay users",
    category="relay",
    admin=True,
    cooldown=2
)
def relay_blocked_list(args, sender):

    now = time.time()
    lines = []

    for addr in sorted(BLOCKED_USERS):
        lines.append(f"{addr} (permanent)")

    expired = [a for a, exp in TIMED_BLOCKS.items() if exp <= now]
    for addr in expired:
        del TIMED_BLOCKS[addr]

    for addr, exp in sorted(TIMED_BLOCKS.items()):
        remaining = int(exp - now)
        if remaining < 60:
            label = f"{remaining}s"
        else:
            label = f"{remaining // 60}m"
        lines.append(f"{addr} (expires in {label})")

    if not lines:
        return "No blocked users."

    return "Blocked:\n" + "\n".join(lines)


# =====================================================
# HISTORY
# =====================================================

@register(
    "relayhistory",
    "Show last relay messages",
    category="relay",
    cooldown=2
)
def relay_history(args, sender):

    if isinstance(sender, (bytes, bytearray)):
        sender = "lxmf:" + sender.hex()

    history = MESSAGE_HISTORY.get(str(sender).lower())

    if not history:
        return "No history available."

    return "\n\n---\n\n".join(history[-5:])


# =====================================================
# SESSION LIST (admin)
# =====================================================

@register(
    "relaysessions",
    "List active relay sessions",
    category="relay",
    admin=True,
    cooldown=2
)
def relay_sessions_cmd(args, sender):

    _expire_sessions()

    if not ACTIVE_REPLY_SESSION:
        return "No active relay sessions."

    now = time.time()
    lines = [f"Active sessions: {len(ACTIVE_REPLY_SESSION)}"]
    seen = set()

    for key in sorted(ACTIVE_REPLY_SESSION):
        peer = LAST_CONTACT.get(key)
        pair = tuple(sorted([key, peer or "?"]))
        if pair in seen:
            continue
        seen.add(pair)

        idle = int(now - SESSION_TIMESTAMPS.get(key, now))
        if idle < 60:
            age = f"{idle}s"
        else:
            age = f"{idle // 60}m"

        lines.append(f"{key} <-> {peer or '?'} (idle {age})")

    return "\n".join(lines)


# =====================================================
# ALIAS / CONTACTS
# =====================================================

@register(
    "alias",
    (
        "Manage relay contact aliases\n\n"
        "Usage:\n"
        "  alias add <name> <proto:addr>\n"
        "  alias list\n"
        "  alias remove <name>\n\n"
        "Then use: relay <name> <message>"
    ),
    category="relay",
    cooldown=2
)
def alias_cmd(args, sender):

    if not args:
        return "Usage: alias add|list|remove ..."

    sub = args[0].lower()

    if sub == "list":
        if not CONTACT_ALIASES:
            return "No aliases defined."
        lines = [f"{name} -> {addr}" for name, addr in sorted(CONTACT_ALIASES.items())]
        return "\n".join(lines)

    if sub == "add":
        if len(args) < 3:
            return "Usage: alias add <name> <proto:addr>"
        name = args[1].lower()
        addr = args[2].lower()
        if ":" not in addr:
            return "Address must be proto:addr (e.g. mc:091733a4)"
        CONTACT_ALIASES[name] = addr
        _save_state()
        return f"Alias saved: {name} -> {addr}"

    if sub == "remove":
        if len(args) < 2:
            return "Usage: alias remove <name>"
        name = args[1].lower()
        if name not in CONTACT_ALIASES:
            return f"No alias '{name}'."
        del CONTACT_ALIASES[name]
        _save_state()
        return f"Alias removed: {name}"

    return "Usage: alias add|list|remove ..."
