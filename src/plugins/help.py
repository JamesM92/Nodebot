import math
from commands import register, COMMANDS

PAGE_SIZE = 5


def build_categories():

    grouped = {}

    for name, entry in COMMANDS.items():

        if not isinstance(entry, dict):
            continue

        grouped.setdefault(entry["category"], []).append(name)

    lines = ["📖 COMMAND CATEGORIES\n"]

    for category in sorted(grouped):
        lines.append(f"📦 {category}")

    lines.append("\nUse: help <category>")

    lines.append("\n\nhttps://github.com/JamesM92/LXMF_Bot")
    return "\n".join(lines)


@register(
    "help",
    "Show help menu",
    category="core",
    cooldown=5,
    aliases=["?", "h"]
)
def help_cmd(args):

    if not args:
        return build_categories()

    category = args[0].lower()

    filtered = []

    for name, entry in COMMANDS.items():

        if not isinstance(entry, dict):
            continue

        if entry["category"].lower() == category:
            filtered.append((name, entry))

    if not filtered:
        return f"No commands in '{category}'."

    lines = [f"📂 {category}\n"]

    for name, entry in filtered[:5]:
        admin_flag = " (admin)" if entry["admin"] else ""
        lines.append(f"• {name}{admin_flag} - {entry['desc']}")

    return "\n".join(lines)
