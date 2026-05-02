from commands import register


@register(
    "about",
    "About NodeBot",
    category="core",
    cooldown=30
)
def about(args, sender):

    return (
        "NodeBot\n"
        "Multi-protocol mesh network chatbot.\n"
        "\n"
        "Supported networks:\n"
        "  LXMF / Reticulum\n"
        "  Meshtastic\n"
        "  MeshCore\n"
        "\n"
        "Cross-network relay, plugin hot-reload,\n"
        "GPS position sharing, and more.\n"
        "\n"
        "github.com/JamesM92/NodeBot"
    )
