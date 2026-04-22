import time
from commands import register


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
