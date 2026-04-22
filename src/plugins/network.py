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
    "Show your LXMF address",
    category="network",
    cooldown=300   # ⬅ slightly longer cooldown
)
def whoami(args, sender):

    return f"Your address:\n{sender}"


# -------------------------
# Announce
# -------------------------

@register(
    "announce",
    "Re-announce this bot on all active protocols",
    category="network",
    admin=True,
    cooldown=60
)
def announce(args, sender):

    announced = BOT_INSTANCE.announce_all()

    if not announced:
        return "No transports with announce support are loaded."

    names = ", ".join(t.replace("_adapter", "") for t in announced)
    return f"Announced on: {names}"
