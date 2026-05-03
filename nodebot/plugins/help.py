from ..commands import register, COMMANDS

PAGE_SIZE = 5


def build_categories():

    grouped = {}
    for name, entry in COMMANDS.items():
        if not isinstance(entry, dict):
            continue
        grouped.setdefault(entry["category"], []).append(name)

    lines = ["= COMMANDS =\n"]
    for category in sorted(grouped):
        count = len(grouped[category])
        lines.append(f"[{category}] {count} cmd{'s' if count != 1 else ''}")

    lines.append("\nhelp <category>")
    lines.append("help <category> <page>")
    lines.append("about - project info")
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

    page = 1
    if len(args) >= 2:
        try:
            page = max(1, int(args[1]))
        except ValueError:
            pass

    filtered = [
        (name, entry)
        for name, entry in COMMANDS.items()
        if isinstance(entry, dict) and entry["category"].lower() == category
    ]

    if not filtered:
        return f"No commands in '{category}'."

    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    page_items = filtered[start:start + PAGE_SIZE]

    header = f"[{category}] p{page}/{total_pages}\n"
    lines = [header]
    for name, entry in page_items:
        flag = " *" if entry["admin"] else ""
        lines.append(f"{name}{flag} - {entry['desc']}")

    if total_pages > 1:
        lines.append(f"\nhelp {category} <page>")

    return "\n".join(lines)
