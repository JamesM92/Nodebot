import time
from datetime import datetime, timezone
from ..commands import register, BOT_INSTANCE
from .. import __version__


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


# -------------------------
# Version
# -------------------------

@register(
    "version",
    "NodeBot software version",
    category="tools",
    cooldown=60
)
def version_cmd(args):

    return f"NodeBot v{__version__}"


# -------------------------
# Time
# -------------------------

@register(
    "time",
    "Current date and time",
    category="tools",
    cooldown=10
)
def time_cmd(args):

    now_utc   = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()
    tz_name   = now_local.strftime("%Z")
    return (
        f"{now_local.strftime('%Y-%m-%d %H:%M')} {tz_name} "
        f"({now_utc.strftime('%H:%M')} UTC)"
    )
