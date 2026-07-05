# Writing NodeBot Plugins

NodeBot plugins are the easiest way to add new commands. You don't need to touch any existing code — just drop a new file in the right folder, and NodeBot picks it up automatically while it's running.

---

## What is a plugin?

A plugin is a small Python file that teaches NodeBot a new command. When someone on the mesh types that command, your file's code runs and sends back a reply.

You do not need to understand the rest of NodeBot to write a plugin. You only need to know one pattern.

---

## Quick start

**1. Copy the template**

```bash
cd nodebot/plugins/
cp _example.py myplugin.py
```

**2. Edit `myplugin.py`**

Replace the example code with your own. The minimum is:

```python
from ..commands import register

@register("hello", "Say hello back")
def hello_cmd(args):
    return "Hello from NodeBot!"
```

**3. Save and wait**

NodeBot checks for new or changed plugin files every few seconds. Within about 5 seconds of saving, your command is live. No restart needed.

**4. Test it**

Send `hello` in any chat connected to NodeBot. You'll get `Hello from NodeBot!` back.

---

## The `@register()` decorator explained

The line above your function, starting with `@register(...)`, tells NodeBot about your command. Here is every option:

```python
@register(
    "commandname",          # (required) word the user types
    "One-line description", # (required) shown in the help list
    category="fun",         # groups commands in help output — pick any word
    cooldown=60,            # seconds before the same user can run it again
    admin=False,            # True = only admins can use it
    aliases=["cmd", "c"],   # extra trigger words that do the same thing
)
```

Only the first two arguments are required. Everything else has a sensible default.

---

## Your function's parameters

### `args` — words typed after the command

If a user sends `weather london uk`, then:
- `args[0]` → `"london"`
- `args[1]` → `"uk"`
- `args` → `["london", "uk"]`

If they just typed `weather` with nothing after it, `args` is an empty list `[]`.

Always check `if not args` before using `args[0]`, or you'll get an error.

```python
@register("weather", "Check the weather", cooldown=60)
def weather_cmd(args):
    if not args:
        return "Usage: weather <location>"
    location = " ".join(args)   # "london uk"
    return f"No weather data for {location} yet."
```

### `sender` — who sent the message (optional)

Add `sender` as a second parameter if you need to know who's asking.

```python
@register("myaddress", "Show your address", cooldown=60)
def myaddress_cmd(args, sender):
    if isinstance(sender, (bytes, bytearray)):
        return f"Your address: {sender.hex()}"
    return f"Your address: {sender}"
```

NodeBot detects that your function accepts `sender` and passes it in automatically. You don't need to do anything extra.

---

## What to return

Return a **string** — that's what gets sent back to the user.

```python
return "Simple reply."
```

For multi-line replies, use `\n`:

```python
return "Line one\nLine two\nLine three"
```

Return **`None`** (or just don't return anything) to send no reply.

---

## Message length limits

Mesh radio packets have size limits. NodeBot splits long responses automatically, but you should still keep replies short:

| Network    | Practical limit |
|------------|----------------|
| MeshCore   | ~190 characters |
| Meshtastic | ~220 characters |
| LXMF       | No fixed limit  |

Aim for 3–5 lines max. If you need to show a lot of data, show the most important part and let the user ask for more.

---

## Cooldowns

The `cooldown` value is how many seconds a user must wait before running your command again. This protects the mesh from accidental or intentional spam.

| Situation | Suggested cooldown |
|-----------|-------------------|
| Simple reply (ping, version) | 5–30 seconds |
| Normal commands | 60 seconds |
| Commands that send multiple lines | 120–300 seconds |
| Commands that hit external services | 120–600 seconds |
| Admin-only commands | 0 (no limit) |

Admins always bypass cooldowns.

---

## Accessing bot state

`BOT_INSTANCE` gives you access to things NodeBot already tracks, like display names and who was last seen.

```python
from ..commands import register, BOT_INSTANCE

@register("greet", "Personalised greeting", cooldown=30)
def greet_cmd(args, sender):
    if isinstance(sender, (bytes, bytearray)):
        key = sender.hex()
    else:
        key = str(sender)

    nicks = BOT_INSTANCE.state.get("nicks", {}) if BOT_INSTANCE else {}
    name = nicks.get(key, "traveller")
    return f"Hey, {name}!"
```

Available in `BOT_INSTANCE.state`:

| Key | What it holds |
|-----|--------------|
| `"nicks"` | `{address: display_name}` — known names |
| `"seen"` | `{address: timestamp}` — last activity time |
| `"start_time"` | Unix timestamp of when NodeBot started |
| `"stats"` | Message counts per user, command, transport |

---

## A worked example: a simple FAQ command

Imagine you want users to type `info` and get a description of your mesh node.

```python
from ..commands import register

@register(
    "info",
    "About this mesh node",
    category="local",
    cooldown=60,
)
def info_cmd(args):
    return (
        "W1XYZ NodeBot\n"
        "Located: Hill Farm, grid EN91\n"
        "Nets: LXMF, Meshtastic LF\n"
        "Questions? Contact W1XYZ"
    )
```

Save this as `nodebot/plugins/local_info.py` and it's live in seconds.

---

## Common mistakes

**"My command isn't showing up"**
- Make sure the filename does NOT start with `_` or `__`.
- Check the NodeBot log for a load error — there may be a typo.

**"It crashes with an error"**
- NodeBot replies with `"Plugin error."` and logs the traceback. Run `journalctl -u nodebot -n 50` to see it.
- The most common cause is using `args[0]` without checking `if not args` first.

**"The reply is cut off"**
- Your response was too long for the radio packet. Keep it under 190 characters per logical block, or split it across multiple lines so NodeBot can chunk it properly.

**"I get 'Cooldown: Xs remaining'"**
- NodeBot is enforcing the cooldown you set. Wait it out, or reduce `cooldown=` while testing.

---

## Reference: the full template

See [`nodebot/plugins/_example.py`](../nodebot/plugins/_example.py) for a fully commented template covering all cases. Copy it to get started.
