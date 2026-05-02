import time
from commands import register, BOT_INSTANCE


# -------------------------
# Ping
# -------------------------

@register(
    "ping",
    "Test bot response",
    category="tools",
    cooldown=5
)
def ping(args):

    return "pong"


# -------------------------
# Uptime
# -------------------------

@register(
    "uptime",
    "Show bot uptime",
    category="tools",
    cooldown=120
)
def uptime(args):

    start = BOT_INSTANCE.state.get("start_time", time.time()) if BOT_INSTANCE else time.time()
    seconds = int(time.time() - start)

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"Uptime: {hours}h {minutes}m {seconds}s"
