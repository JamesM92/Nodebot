import time
from commands import register, BOT_INSTANCE


@register(
    "interfaces",
    "Show active Reticulum interfaces",
    category="network",
    cooldown=60
)
def interfaces(args):

    try:
        import RNS
        ifaces = getattr(RNS.Transport, "interfaces", [])

        if not ifaces:
            return "No active interfaces found."

        return "Active Interfaces:\n" + "\n".join(f"- {i}" for i in ifaces)

    except ImportError:
        return "Reticulum not available."
    except Exception as e:
        return f"Error: {repr(e)}"


@register(
    "neighbors",
    "Show known network neighbors",
    category="network",
    cooldown=60
)
def neighbors(args):

    try:
        import RNS
        nbrs = getattr(
            RNS.Transport,
            "neighbours",
            getattr(RNS.Transport, "neighbors", [])
        )

        if not nbrs:
            return "No neighbors currently known."

        return "Known Neighbors:\n" + "\n".join(f"- {n}" for n in nbrs)

    except ImportError:
        return "Reticulum not available."
    except Exception as e:
        return f"Error: {repr(e)}"


@register(
    "paths",
    "Show known destination paths",
    category="network",
    cooldown=30
)
def paths(args):

    try:
        import RNS
        known = getattr(RNS.Transport, "paths", [])

        if not known:
            return "No known paths."

        return "Known Paths:\n" + "\n".join(f"- {p}" for p in known)

    except ImportError:
        return "Reticulum not available."
    except Exception as e:
        return f"Error: {repr(e)}"


@register(
    "nodeinfo",
    "Show Reticulum node information",
    category="network",
    cooldown=30
)
def nodeinfo(args):

    try:
        import RNS
        ifaces = getattr(RNS.Transport, "interfaces", [])
        nbrs = getattr(
            RNS.Transport,
            "neighbours",
            getattr(RNS.Transport, "neighbors", [])
        )
        known = getattr(RNS.Transport, "paths", [])

        return "\n".join([
            "Reticulum Node Information",
            "---------------------------",
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Interfaces: {len(ifaces)}",
            f"Neighbors: {len(nbrs)}",
            f"Known Paths: {len(known)}",
        ])

    except ImportError:
        return "Reticulum not available."
    except Exception as e:
        return f"Error: {repr(e)}"


@register(
    "announce",
    "Re-announce on all transports",
    category="network",
    admin=True,
    cooldown=30
)
def announce(args, sender):

    bot = BOT_INSTANCE
    if bot is None:
        return "Bot not initialized."

    announced = bot.announce_all()
    if not announced:
        return "No transports with announce support are loaded."

    names = ", ".join(t.replace("_adapter", "") for t in announced)
    return f"Announced on: {names}"
