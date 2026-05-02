import time
from commands import register, admin_login, ACTIVE_ADMINS, BOT_INSTANCE, is_admin


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
    "logout",
    "End admin session",
    category="admin"
)
def logout_cmd(args, sender):
    if sender in ACTIVE_ADMINS:
        del ACTIVE_ADMINS[sender]
        return "Admin session ended.", True
    return "No active admin session.", True


@register(
    "reload",
    "Hot-reload all plugins",
    category="admin",
    admin=True
)
def reload_cmd(args, sender):
    bot = BOT_INSTANCE
    if bot is None:
        return "Bot not initialized.", True
    bot.reload_plugins()
    import commands as _cmds
    return f"Plugins reloaded ({len(_cmds._loaded)} loaded).", True


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

    per_t = stats_data.get("per_transport", {})
    transport_lines = "\n".join(
        f"  {t}: {n}" for t, n in sorted(per_t.items())
    ) or "  none"

    return (
        "Stats\n"
        f"Total: {stats_data['total']}\n"
        f"Users: {len(stats_data['per_user'])}\n"
        f"Commands: {len(stats_data['per_command'])}\n"
        f"By transport:\n{transport_lines}"
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

    now = time.time()
    active_admins = sum(1 for exp in ACTIVE_ADMINS.values() if exp > now)

    import sys
    relay_mod = sys.modules.get("plugins.relay")
    relay_sessions = len(relay_mod.ACTIVE_REPLY_SESSION) if relay_mod else 0

    transport_lines = []
    for name, adapter in (bot.transports.items() if bot.transports else []):
        short = name.replace("_adapter", "")
        if hasattr(adapter, "is_connected"):
            state = "up" if adapter.is_connected else "down"
        else:
            state = "running" if getattr(adapter, "running", False) else "stopped"
        transport_lines.append(f"  {short}: {state}")

    transport_block = "\n".join(transport_lines) if transport_lines else "  none"

    return (
        f"NodeBot status\n"
        f"Lockdown: {lockdown}\n"
        f"Transports:\n{transport_block}\n"
        f"Relay sessions: {relay_sessions}\n"
        f"Admin sessions: {active_admins}"
    ), True


@register(
    "broadcast",
    "Send a message to all known relay contacts",
    category="admin",
    admin=True,
    cooldown=60
)
def broadcast_cmd(args, sender):

    if not args:
        return "Usage: broadcast <message>", True

    import sys
    relay_mod = sys.modules.get("plugins.relay")
    if not relay_mod:
        return "Relay plugin not loaded.", True

    targets = list(relay_mod.SEEN_USERS)
    if not targets:
        return "No known contacts to broadcast to.", True

    bot = BOT_INSTANCE
    if not bot:
        return "Bot not initialized.", True

    msg = " ".join(args)
    sent = 0
    failed = 0
    for dest in targets:
        try:
            bot.send(dest, msg)
            sent += 1
        except Exception:
            failed += 1

    result = f"Broadcast sent to {sent} contact(s)."
    if failed:
        result += f" {failed} failed."
    return result, True


@register(
    "allowlist",
    "Manage allowlist mode (restrict bot to approved addresses)",
    category="admin",
    admin=True,
    cooldown=1
)
def allowlist_cmd(args, sender):

    bot = BOT_INSTANCE
    if bot is None:
        return "Bot not initialized.", True

    if not args:
        mode = "ON" if bot.allowlist_mode else "OFF"
        count = len(bot.allowlist)
        return f"Allowlist: {mode} ({count} entries)\nUsage: allowlist on|off|add <addr>|remove <addr>|list", True

    sub = args[0].lower()

    if sub == "on":
        bot.allowlist_mode = True
        return "Allowlist mode ON — only listed addresses can use the bot.", True

    if sub == "off":
        bot.allowlist_mode = False
        return "Allowlist mode OFF.", True

    if sub == "add":
        if len(args) < 2:
            return "Usage: allowlist add <addr>", True
        bot.allowlist.add(args[1])
        return f"Added {args[1]} ({len(bot.allowlist)} total).", True

    if sub == "remove":
        if len(args) < 2:
            return "Usage: allowlist remove <addr>", True
        bot.allowlist.discard(args[1])
        return f"Removed {args[1]}.", True

    if sub == "list":
        if not bot.allowlist:
            return "Allowlist is empty.", True
        return "Allowlist:\n" + "\n".join(sorted(bot.allowlist)), True

    return "Usage: allowlist on|off|add <addr>|remove <addr>|list", True
