from commands import register, admin_login, BOT_INSTANCE


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
        return "🔒 Lockdown ON", True
    else:
        return "🔓 Lockdown OFF", True


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
        "📊 Stats\n"
        f"Total Commands: {stats_data['total']}\n"
        f"Unique Users: {len(stats_data['per_user'])}\n"
        f"Unique Commands: {len(stats_data['per_command'])}"
    ), True
