
# plugins/relay.py

import time
from commands import register, BOT_INSTANCE, is_admin

# =====================================================
# STATE
# =====================================================

LAST_CONTACT = {}
SEEN_USERS = set()
ACTIVE_REPLY_SESSION = set()
BLOCKED_USERS = set()
RECENT_RELAYS = {}
MESSAGE_HISTORY = {}
SESSION_TIMESTAMPS = {}  # key -> last activity timestamp

LOOP_TIMEOUT = 30
SESSION_TTL  = 3600  # seconds of inactivity before a session expires


# =====================================================
# HELPERS
# =====================================================

def parse_target(target):
    if ":" not in target:
        return None, None
    proto, addr = target.split(":", 1)
    return proto.lower(), addr.lower()


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


def send_message(destination, text, notify_cb=None):
    if hasattr(BOT_INSTANCE, "send"):
        BOT_INSTANCE.send(destination, text, notify_cb=notify_cb)


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

    send_message(destination, message)
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
        "Usage:\n"
        "  relay <protocol:address> <message>\n\n"
        "Chain through another NodeBot:\n"
        "  relay <nodebot> relay <target> <message>\n\n"
        "Examples:\n"
        "  relay lxmf:abc123 Hello\n"
        "  relay mc:091733a4 Hi there\n"
        "  relay mc:28276 relay mc:8276tr Hello\n"
    ),
    category="relay",
    cooldown=5
)
def relay_cmd(args, sender):

    if sender in BLOCKED_USERS:
        return "You are blocked from using relay."

    if len(args) < 2:
        return "Usage: relay <protocol:address> <message>"

    target_raw = args[0]
    message = " ".join(args[1:])

    proto, addr = parse_target(target_raw)
    if not proto or not addr:
        return "Invalid format. Use protocol:address"

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

    # Chained relay: payload is itself a relay command. Send it raw so the
    # next NodeBot processes it as a command rather than a human message.
    if message.lower().startswith("relay "):
        payload = message
    else:
        payload = format_message(norm_sender, message)

    store_history(norm_sender, payload)
    store_history(destination, payload)

    _notify_sender = norm_sender

    def _on_delivery(success):
        import threading
        msg = "Relay: delivered" if success else "Relay: delivery failed"
        threading.Thread(target=send_message, args=(_notify_sender, msg), daemon=True).start()

    send_message(destination, payload, notify_cb=_on_delivery)

    return None


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

    _notify_sender = effective_sender

    def _on_delivery(success):
        import threading
        msg = "Relay: delivered" if success else "Relay: delivery failed"
        threading.Thread(target=send_message, args=(_notify_sender, msg), daemon=True).start()

    send_message(destination, payload, notify_cb=_on_delivery)

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

    _notify_sender = effective_sender

    def _on_delivery(success):
        import threading
        msg = "Relay: delivered" if success else "Relay: delivery failed"
        threading.Thread(target=send_message, args=(_notify_sender, msg), daemon=True).start()

    send_message(destination, payload, notify_cb=_on_delivery)

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
    "Block a sender from relay",
    category="relay",
    admin=True,
    cooldown=1
)
def relay_block(args, sender):

    if not args:
        return "Usage: relayblock <protocol:address>"

    target = args[0]
    BLOCKED_USERS.add(target)
    return f"Blocked {target}"


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
