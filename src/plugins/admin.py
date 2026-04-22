import time
from commands import register, admin_login, ACTIVE_ADMINS, BOT_INSTANCE


@register(
    "admin",
    "Login as admin",
    category="admin",
    cooldown=30
)
def admin_cmd(args, sender):

    if len(args) < 1:
        return "Usage: admin PASSWORD", True

    password = args[0]

    success, msg = admin_login(sender, password)

    return msg, True


@register(
    "lockdown",
    "Toggle lockdown mode",
    category="admin",
    admin=True
)
def lockdown(args, sender):

    bot = BOT_INSTANCE

    if bot is None:
        return "Bot not initialized.", True

    status = bot.toggle_lockdown()

    if status:
        return "Lockdown ON", True
    else:
        return "Lockdown OFF", True


@register(
    "stats",
    "Show usage statistics",
    category="admin",
    admin=True
)
def stats(args, sender):

    bot = BOT_INSTANCE

    if bot is None:
        return "Bot not initialized.", True

    stats_data = bot.state["stats"]

    return (
        "Stats\n"
        f"Total Commands: {stats_data['total']}\n"
        f"Unique Users: {len(stats_data['per_user'])}\n"
        f"Unique Commands: {len(stats_data['per_command'])}"
    ), True


@register(
    "status",
    "Show bot status and active transports",
    category="admin",
    admin=True,
    cooldown=5
)
def status_cmd(args, sender):

    bot = BOT_INSTANCE
    if bot is None:
        return "Bot not initialized.", True

    lockdown = "ON" if bot.lockdown else "OFF"
    transports = list(bot.transports.keys()) if bot.transports else []
    transport_names = ", ".join(t.replace("_adapter", "") for t in transports) or "none"

    now = time.time()
    active_admins = sum(1 for exp in ACTIVE_ADMINS.values() if exp > now)

    import sys
    relay_mod = sys.modules.get("plugins.relay")
    relay_sessions = len(relay_mod.ACTIVE_REPLY_SESSION) if relay_mod else 0

    return (
        f"NodeBot status\n"
        f"Lockdown: {lockdown}\n"
        f"Transports: {transport_names}\n"
        f"Relay sessions: {relay_sessions}\n"
        f"Admin sessions: {active_admins}"
    ), True


