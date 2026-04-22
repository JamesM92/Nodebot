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

        COMMANDS[name] = {
            "func": func,
            "desc": desc,
            "category": category,
            "admin": admin,
            "cooldown": cooldown
        }

        for a in aliases:
            COMMANDS[a] = name

        return func

    return wrapper

# =====================================================
# ADMIN SYSTEM
# =====================================================

_raw_addrs = _config.get("admin", "addresses", fallback="").strip()
ADMIN_ADDRESSES = {a.strip() for a in _raw_addrs.replace(",", " ").split() if a.strip()}

_raw_password = _config.get("admin", "password", fallback="changeme")
ADMIN_PASSWORD_HASH = hashlib.sha256(_raw_password.encode()).hexdigest()

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

    if hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
        ACTIVE_ADMINS[sender] = now + 1800
        return True, "Admin authenticated."

    return False, "Invalid password."

# =====================================================
# PLUGIN SYSTEM (HOT RELOAD)
# =====================================================

def scan_plugins(force=False):

    global _last_scan

    now = time.time()

    if not force and (now - _last_scan) < SCAN_INTERVAL:
        return

    _last_scan = now

    print(f"Scanning plugins in {PLUGIN_DIR}")

    if not os.path.isdir(PLUGIN_DIR):
        print("Plugin directory does not exist")
        return

    files = []
    try:
        files = os.listdir(PLUGIN_DIR)
    except Exception as e:
        print(f"Error reading plugin directory: {e}")
        return

    print(f"Found {len(files)} items in plugin directory")

    for file in files:

        if not file.endswith(".py"):
            continue

        if file.startswith("__"):
            continue

        module_name = f"plugins.{file[:-3]}"
        path = os.path.join(PLUGIN_DIR, file)

        print(f"Processing plugin file: {file}")

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
                print(f"Plugin loaded: {module_name}")
            except Exception as e:
                print(f"Plugin load error {module_name}: {repr(e)}")

        # reload on change
        else:
            if _mtimes.get(module_name, 0) < mtime:
                try:
                    importlib.reload(sys.modules[module_name])
                    _mtimes[module_name] = mtime
                    print(f"Plugin reloaded: {module_name}")
                except Exception as e:
                    print(f"Plugin reload error {module_name}: {repr(e)}")


def load_plugins():
    scan_plugins(force=True)

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
