# commands.py

import configparser
import importlib
import inspect
import os
import sys
import time
import hashlib
import threading
import traceback

# =====================================================
# CONFIG
# =====================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_HERE, "..", "config.ini")

_config = configparser.ConfigParser()
_config.read(_CONFIG_PATH)

# =====================================================
# GLOBAL REGISTRY
# =====================================================

COMMANDS = {}
BOT_INSTANCE = None
STATE = None

# =====================================================
# PLUGIN CONTROL
# =====================================================

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")

_loaded = {}
_mtimes = {}
_plugin_commands = {}   # {module_name: set of command/alias names owned by that plugin}
_last_scan = 0
SCAN_INTERVAL = _config.getint("plugins", "scan_interval", fallback=5)

PLUGIN_DISABLED = set()
PLUGIN_TIMEOUT_SEC = _config.getfloat("plugins", "timeout_sec", fallback=5.0)

# =====================================================
# BOT / STATE HOOKS
# =====================================================

def set_bot(bot):
    global BOT_INSTANCE
    BOT_INSTANCE = bot


def set_state(state_store):
    global STATE
    STATE = state_store

# =====================================================
# COMMAND REGISTRATION
# =====================================================

def register(name, desc, category="general", admin=False, cooldown=60, aliases=None):

    if aliases is None:
        aliases = []

    def wrapper(func):

        if name in COMMANDS and isinstance(COMMANDS[name], dict):
            existing_mod = getattr(COMMANDS[name]["func"], "__module__", "?")
            print(f"[plugins] warning: '{name}' already registered by '{existing_mod}', overridden by '{func.__module__}'")

        COMMANDS[name] = {
            "func": func,
            "desc": desc,
            "category": category,
            "admin": admin,
            "cooldown": cooldown
        }

        module_name = func.__module__
        _plugin_commands.setdefault(module_name, set()).add(name)

        for a in aliases:
            COMMANDS[a] = name
            _plugin_commands[module_name].add(a)

        return func

    return wrapper

# =====================================================
# ADMIN SYSTEM
# =====================================================

_raw_addrs = _config.get("admin", "addresses", fallback="").strip()
ADMIN_ADDRESSES = {a.strip() for a in _raw_addrs.replace(",", " ").split() if a.strip()}

_raw_password = _config.get("admin", "password", fallback="changeme")
# PBKDF2-HMAC-SHA256 with 260 000 iterations — ~260 000× harder to brute-force
# than bare SHA-256 while remaining deterministic (no stored salt needed).
_PBKDF2_SALT = b"nodebot-v1"
ADMIN_PASSWORD_HASH = hashlib.pbkdf2_hmac(
    "sha256", _raw_password.encode(), _PBKDF2_SALT, 260_000
).hex()

ACTIVE_ADMINS = {}
LOGIN_COOLDOWN = {}
COOLDOWN_TRACKER = {}  # {sender: {cmd_name: last_used_time}}


def is_admin(sender):

    if sender in ADMIN_ADDRESSES:
        return True

    expiry = ACTIVE_ADMINS.get(sender, 0)
    return expiry > time.time()


def admin_login(sender, password):

    now = time.time()

    if LOGIN_COOLDOWN.get(sender, 0) > now:
        return False, "Login cooldown active."

    LOGIN_COOLDOWN[sender] = now + 30

    if hashlib.pbkdf2_hmac("sha256", password.encode(), _PBKDF2_SALT, 260_000).hex() == ADMIN_PASSWORD_HASH:
        ACTIVE_ADMINS[sender] = now + 1800
        return True, "Admin authenticated."

    return False, "Invalid password."


def _cleanup_stale():
    """Prune expired entries from admin and cooldown dicts."""
    now = time.time()

    stale = [k for k, exp in ACTIVE_ADMINS.items() if exp <= now]
    for k in stale:
        del ACTIVE_ADMINS[k]

    stale = [k for k, exp in LOGIN_COOLDOWN.items() if exp <= now]
    for k in stale:
        del LOGIN_COOLDOWN[k]

    for sender in list(COOLDOWN_TRACKER):
        user_cmds = COOLDOWN_TRACKER[sender]
        stale_cmds = []
        for cmd, last_used in user_cmds.items():
            entry = COMMANDS.get(cmd)
            cmd_cooldown = entry.get("cooldown", 0) if isinstance(entry, dict) else 3600
            if now - last_used > cmd_cooldown:
                stale_cmds.append(cmd)
        for cmd in stale_cmds:
            del user_cmds[cmd]
        if not user_cmds:
            del COOLDOWN_TRACKER[sender]

# =====================================================
# PLUGIN SYSTEM (HOT RELOAD)
# =====================================================

def scan_plugins(force=False):

    global _last_scan

    now = time.time()

    if not force and (now - _last_scan) < SCAN_INTERVAL:
        return

    _last_scan = now

    _cleanup_stale()

    if not os.path.isdir(PLUGIN_DIR):
        print("[plugins] plugin directory does not exist")
        return

    try:
        files = os.listdir(PLUGIN_DIR)
    except Exception as e:
        print(f"[plugins] error reading plugin directory: {e}")
        return

    present = {
        f"plugins.{f[:-3]}"
        for f in files
        if f.endswith(".py") and not f.startswith("__")
    }

    # Unregister commands for any plugin whose file has been removed
    for module_name in list(_loaded):
        if module_name not in present:
            cmds = _plugin_commands.pop(module_name, set())
            for cmd in cmds:
                COMMANDS.pop(cmd, None)
            _loaded.pop(module_name, None)
            _mtimes.pop(module_name, None)
            sys.modules.pop(module_name, None)
            print(f"[plugins] removed: {module_name} ({len(cmds)} commands unregistered)")

    for file in files:

        if not file.endswith(".py"):
            continue

        if file.startswith("__"):
            continue

        module_name = f"plugins.{file[:-3]}"
        path = os.path.join(PLUGIN_DIR, file)

        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            continue

        # first load
        if module_name not in sys.modules:
            try:
                importlib.import_module(module_name)
                _loaded[module_name] = True
                _mtimes[module_name] = mtime
            except Exception as e:
                print(f"[plugins] load error {module_name}: {repr(e)}")

        # reload on change
        else:
            if _mtimes.get(module_name, 0) < mtime:
                try:
                    importlib.reload(sys.modules[module_name])
                    _mtimes[module_name] = mtime
                    print(f"[plugins] reloaded: {module_name}")
                except Exception as e:
                    print(f"[plugins] reload error {module_name}: {repr(e)}")


def load_plugins():
    scan_plugins(force=True)
    names = [m.split(".")[-1] for m in sorted(_loaded)]
    print(f"[plugins] loaded {len(names)}: {', '.join(names)}")

# =====================================================
# SANDBOX EXECUTION WRAPPER
# =====================================================

def safe_execute(func, args, sender):

    result_container = {"result": None, "error": None}

    def target():
        try:
            sig = inspect.signature(func)

            if len(sig.parameters) == 2:
                result_container["result"] = func(args, sender)
            else:
                result_container["result"] = func(args)

        except Exception as e:
            result_container["error"] = e
            result_container["trace"] = traceback.format_exc()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(PLUGIN_TIMEOUT_SEC)

    if thread.is_alive():
        return "Plugin timed out.", True

    if result_container["error"]:
        print("Plugin crash:\n", result_container["trace"])
        return "Plugin error.", True

    result = result_container["result"]

    if isinstance(result, tuple):
        return result

    return result, True

# =====================================================
# COMMAND EXECUTION PIPELINE
# =====================================================

def handle_command(message, sender):

    scan_plugins()

    parts = message.strip().split()
    if not parts:
        return None, False

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd not in COMMANDS:
        # Allow trailing colon variants (e.g. "Relay:" → "relay")
        stripped = cmd.rstrip(":")
        if stripped != cmd and stripped in COMMANDS:
            cmd = stripped
        else:
            return None, False

    entry = COMMANDS[cmd]

    # alias resolution
    if isinstance(entry, str):
        cmd = entry
        entry = COMMANDS.get(cmd)

    if not entry or "func" not in entry:
        return None, False

    # admin check
    if entry.get("admin") and not is_admin(sender):
        return "Admin only.", True

    func = entry["func"]

    # plugin disable check
    if func.__name__ in PLUGIN_DISABLED:
        return "Plugin disabled.", True

    # cooldown enforcement (admins bypass cooldowns)
    cooldown = entry.get("cooldown", 0)
    if cooldown > 0 and not is_admin(sender):
        now = time.time()
        user_cooldowns = COOLDOWN_TRACKER.setdefault(sender, {})
        last_used = user_cooldowns.get(cmd, 0)
        remaining = cooldown - (now - last_used)
        if remaining > 0:
            return f"Cooldown: {int(remaining) + 1}s remaining.", True
        user_cooldowns[cmd] = now

    # execute safely in sandbox
    result, ok = safe_execute(func, args, sender)

    # per-command stat tracking
    if BOT_INSTANCE:
        per_cmd = BOT_INSTANCE.state["stats"]["per_command"]
        per_cmd[cmd] = per_cmd.get(cmd, 0) + 1

    # state tracking hook
    if STATE:
        STATE.inc_command()

    return result, ok
