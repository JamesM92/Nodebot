# _example.py — NodeBot plugin template
#
# Files starting with _ are NOT loaded by NodeBot.
# Copy this file, rename it (without the _), and NodeBot will pick it up automatically.
#
# Example:
#   cp _example.py myplugin.py
#   # NodeBot hot-reloads it within a few seconds — no restart needed.
#
# ─────────────────────────────────────────────────────────────────────────────
# MINIMUM WORKING PLUGIN
# ─────────────────────────────────────────────────────────────────────────────
#
# from ..commands import register
#
# @register("hello", "Say hello back")
# def hello(args):
#     return "Hello! 👋"
#
# That's it. Type "hello" in any chat and NodeBot replies.
#
# ─────────────────────────────────────────────────────────────────────────────
# FULL REFERENCE — every option explained
# ─────────────────────────────────────────────────────────────────────────────

from ..commands import register, BOT_INSTANCE

# ------------------------------------------------------------------
# @register() — the decorator that registers a command
#
# Parameters:
#   name      (required) — the word users type to trigger this command
#   desc      (required) — one-line description shown in the help list
#   category  (optional) — groups related commands in the help output
#                          use any short word, e.g. "tools", "fun", "weather"
#                          default: "general"
#   cooldown  (optional) — seconds a user must wait before using this
#                          command again.  default: 60
#                          set to 0 to disable cooldown entirely.
#   admin     (optional) — set to True to restrict to admin users only
#                          default: False
#   aliases   (optional) — list of extra trigger words for this command
#                          e.g. aliases=["hi", "hey"] lets users type those too
# ------------------------------------------------------------------


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 1 — simplest possible command
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "hello",
    "Say hello back",
    category="fun",
    cooldown=30,
)
def hello_cmd(args):
    # Just return a string — NodeBot sends it back to the user.
    return "Hello from NodeBot!"


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 2 — reading words the user typed after the command
#
# If a user sends: "echo testing 1 2 3"
#   args = ["testing", "1", "2", "3"]   — a list of words
#
# args is always a list. If the user typed nothing after the command, it's [].
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "echo",
    "Repeat back what you say",
    category="fun",
    cooldown=10,
)
def echo_cmd(args):
    if not args:
        return "Usage: echo <your message>"

    # Join the words back into a single string
    message = " ".join(args)
    return f"You said: {message}"


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 3 — knowing WHO sent the message
#
# Add a second parameter called "sender" to receive the sender's address.
# NodeBot detects the extra parameter and passes it in automatically.
#
# sender is a string like:
#   "mc:a1b2c3d4"          (MeshCore)
#   "mesh:node123"         (Meshtastic)
#   bytes object           (LXMF/Reticulum — convert with sender.hex())
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "whoami",
    "Show your network address",
    category="fun",
    cooldown=60,
)
def whoami_cmd(args, sender):
    # Convert bytes to hex string (LXMF sends raw bytes)
    if isinstance(sender, (bytes, bytearray)):
        addr = sender.hex()
    else:
        addr = str(sender)

    return f"Your address: {addr}"


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 4 — personalising with the sender's display name
#
# BOT_INSTANCE.state["nicks"] holds display names NodeBot has seen.
# Not every sender will have one — always provide a fallback.
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "greet",
    "Personalised greeting",
    category="fun",
    cooldown=30,
)
def greet_cmd(args, sender):
    if isinstance(sender, (bytes, bytearray)):
        key = sender.hex()
    else:
        key = str(sender)

    # Look up display name; fall back to "traveller" if unknown
    nicks = BOT_INSTANCE.state.get("nicks", {}) if BOT_INSTANCE else {}
    name = nicks.get(key, "traveller")

    return f"Hey, {name}! Good to hear from you."


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 5 — a command with arguments and aliases
#
# Aliases let users trigger the same command with different words.
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "ask",
    "Ask a yes/no question",
    category="fun",
    cooldown=5,
    aliases=["oracle", "8ball"],
)
def ask_cmd(args):
    import random

    if not args:
        return "Ask me a yes/no question. Usage: ask <question>"

    answers = [
        "Yes.", "No.", "Maybe.", "Definitely.", "Absolutely not.",
        "Signs point to yes.", "Ask again later.", "Outlook uncertain.",
    ]
    return random.choice(answers)


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 6 — multi-line responses
#
# Mesh radios have a character limit per message (~190 chars on MeshCore and
# MeshTastic). NodeBot splits long responses automatically at line boundaries,
# so short multi-line responses are fine. Avoid walls of text — keep responses
# to 3-5 lines where possible.
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "weather_tip",
    "Quick weather safety tip",
    category="fun",
    cooldown=60,
)
def weather_tip_cmd(args):
    return (
        "Weather tips:\n"
        "- Check sky before heading out\n"
        "- Lightning: 30/30 rule\n"
        "- Wind chill matters more than temp"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE 7 — admin-only command
#
# Only users listed in config.ini [admin] addresses, or who have logged in
# with the admin password, can run this.
# ─────────────────────────────────────────────────────────────────────────────

@register(
    "reloadtest",
    "Admin: test admin gate",
    category="admin",
    admin=True,
    cooldown=0,
)
def reload_test_cmd(args):
    return "You are an admin."


# ─────────────────────────────────────────────────────────────────────────────
# TIPS
# ─────────────────────────────────────────────────────────────────────────────
#
# • Return None (or nothing) to send no reply — useful if your plugin handles
#   a case silently.
#
# • Returning a string is all you need. NodeBot handles delivery to whatever
#   network the message came from.
#
# • Plugins hot-reload: save the file and NodeBot picks up changes in seconds.
#   No restart needed during development.
#
# • If your plugin crashes, NodeBot replies with "Plugin error." and keeps
#   running. Check the NodeBot log for the traceback.
#
# • Avoid long-running operations (network requests, large loops). Each plugin
#   call has a 5-second timeout. If you need to fetch data, keep it quick or
#   cache the result.
#
# • Cooldowns protect the mesh from spam. A 60-second cooldown is a sensible
#   default. Set higher for commands that send a lot of text.
