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
    "Show bot info",
    category="community",
    cooldown=60
)
def info(args):

    return (
        "NodeBot\n"
        "Multi-protocol mesh network chatbot.\n"
        "Type 'help' for available commands."
    )
