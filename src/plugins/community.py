from commands import register


@register(
    "echo",
    "Echo back your message",
    category="community",
    cooldown=60
)
def echo(args, sender):

    if not args:
        return "Usage: echo MESSAGE"

    return " ".join(args)


@register(
    "info",
    "Show community node info",
    category="community",
    cooldown=60
)
def info(args):

    return (
        "Community Mesh Node\n"
        "Provides network diagnostics and utility services.\n"
        "Type 'help' for available commands."
    )
