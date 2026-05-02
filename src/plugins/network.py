import time
from commands import register, BOT_INSTANCE


# -------------------------
# Node Time
# -------------------------

@register(
    "time",
    "Show node time",
    category="network",
    cooldown=120   # ⬅ command-level cooldown (seconds)
)
def node_time(args):

    return time.strftime("%Y-%m-%d %H:%M:%S")


# -------------------------
# Who Am I
# -------------------------

@register(
    "whoami",
    "Show your network address",
    category="network",
    cooldown=300
)
def whoami(args, sender):

    return f"Your address:\n{sender}"


@register(
    "seen",
    "Show when users were last active",
    category="network",
    cooldown=30
)
def seen_cmd(args, sender):

    seen = BOT_INSTANCE.state.get("seen", {}) if BOT_INSTANCE else {}

    if not seen:
        return "No users seen yet."

    def _fmt(ts):
        age = int(time.time() - ts)
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m ago"
        return f"{age // 3600}h ago"

    if args:
        target = args[0].lower()
        matches = [(k, v) for k, v in seen.items() if target in k.lower()]
        if not matches:
            return f"No record of '{target}'"
        pairs = sorted(matches, key=lambda x: -x[1])[:5]
    else:
        pairs = sorted(seen.items(), key=lambda x: -x[1])[:5]

    nicks = BOT_INSTANCE.state.get("nicks", {}) if BOT_INSTANCE else {}
    lines = []
    for addr, ts in pairs:
        nick = nicks.get(addr)
        label = f"{addr} ({nick})" if nick else addr
        lines.append(f"{label}: {_fmt(ts)}")
    return "\n".join(lines)
