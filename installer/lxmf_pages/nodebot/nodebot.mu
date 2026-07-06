#!/usr/bin/python3
# NodeBot NomadNet info page — deployed by install_lxmf.sh
#
# NOTE: PROJECT_DIR_PLACEHOLDER below is substituted at install time.
# Do not edit the deployed copy directly — edit this template and re-run the installer.

import os
import re
import glob
import json
import importlib.util
import configparser

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")
README_PATH = os.path.join(PROJECT_DIR, "README.md")

# ── Read config ───────────────────────────────────────────────
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

bot_name  = config.get("bot", "name",         fallback="NodeBot").strip()
storage   = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage").strip()
)
mc_port     = config.get("meshcore", "port", fallback="").strip()

# ── Protocol install detection ────────────────────────────────
def _pkg_installed(name):
    """True if package is importable from system Python or the project venv."""
    if importlib.util.find_spec(name) is not None:
        return True
    pattern = os.path.join(PROJECT_DIR, ".venv/lib/python*/site-packages", name)
    return bool(glob.glob(pattern))

lxmf_available   = _pkg_installed("RNS")
mesh_lib_present = _pkg_installed("meshtastic")
mc_available     = bool(mc_port) and _pkg_installed("meshcore")

# ── Collect all configured Meshtastic adapters ────────────────
# Scans [meshtastic], [meshtastic1], [meshtastic2], … in config.
mesh_adapters = []  # list of (label, port, preset, json_path)
for sec in config.sections():
    if sec == "meshtastic" or re.match(r"^meshtastic\d+$", sec):
        port   = config.get(sec, "port",         fallback="").strip()
        preset = config.get(sec, "modem_preset", fallback="LONG_FAST").strip()
        if port and mesh_lib_present:
            json_name = f"{sec}_lora.json"
            mesh_adapters.append((sec, port, preset, json_name))

# ── LXMF address ─────────────────────────────────────────────
lxmf_addr = None
if lxmf_available:
    id_path = os.path.join(storage, "identity")
    if os.path.isfile(id_path):
        try:
            import RNS
            identity  = RNS.Identity.from_file(id_path)
            dest_hash = RNS.Destination.hash(identity, "lxmf", "delivery")
            lxmf_addr = dest_hash.hex()
        except Exception:
            lxmf_addr = "unavailable"
    else:
        lxmf_addr = "start NodeBot once to generate"

# ── Meshtastic node IDs + contact URL (one per configured adapter) ───────────
def _mesh_info_for(json_name):
    """Return (node_addr_str, contact_url_or_none) from a lora JSON file."""
    lora_json = os.path.join(storage, json_name)
    if os.path.isfile(lora_json):
        try:
            with open(lora_json) as f:
                d = json.load(f)
            num      = d.get("my_node_num")
            pk_b64   = d.get("public_key_b64url", "")
            node_str = "mesh:{:08x}".format(int(num)) if num else None
            url      = f"https://meshtastic.org/v/{pk_b64}" if pk_b64 else None
            if node_str:
                return node_str, url
        except Exception:
            pass
    return "mesh:[start NodeBot to populate]", None

# ── MeshCore ─────────────────────────────────────────────────
mc_addr         = None
mc_contact_url  = None
if mc_available:
    mc_json = os.path.join(storage, "meshcore_node.json")
    if os.path.isfile(mc_json):
        try:
            import urllib.parse
            with open(mc_json) as f:
                d = json.load(f)
            pubkey = d.get("public_key", "")
            if pubkey:
                mc_addr = "mc:" + pubkey[:8]
                mc_contact_url = (f"meshcore://contact/add"
                                  f"?name={urllib.parse.quote(bot_name)}"
                                  f"&public_key={pubkey}"
                                  f"&type=1")
        except Exception:
            pass
    if mc_addr is None:
        mc_addr = "mc:[start NodeBot to populate]"

# ── README → Micron conversion ────────────────────────────────
def md_to_micron(text):
    out = []
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()

        if line.startswith("```"):
            in_code = not in_code
            out.append("")
            continue

        if in_code:
            out.append("  " + line)
            continue

        if line.startswith("### "):
            out.append("")
            out.append("`_" + line[4:] + "`_")
            continue

        if line.startswith("## "):
            out.append(">")
            out.append("`!" + line[3:] + "`!")
            out.append("")
            continue

        if line.startswith("# "):
            continue  # top-level title already shown in header

        if line.rstrip("-") == "" and len(line) >= 3:
            out.append(">")
            continue

        # Table separator rows — skip
        if re.match(r'^\|[-| :]+\|$', line):
            continue

        # Table data rows — flatten to text
        # Strip backticks from cells — unknown backtick tokens eat the next char
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [re.sub(r'`([^`\n]+)`', r'\1', c) for c in cells]
            out.append("  " + "   ".join(c for c in cells if c))
            continue

        # Convert markdown bullets to Micron-safe bullets
        # Micron treats any line starting with '-' as a horizontal rule
        if re.match(r'^\s*- ', line):
            line = re.sub(r'^(\s*)- ', r'\1+ ', line)

        # Inline bold and code
        line = re.sub(r'\*\*(.+?)\*\*', r'`!\1`!', line)
        line = re.sub(r'`([^`\n]+)`',   r'\1',      line)

        out.append(line)

    return "\n".join(out)

# ── Output ────────────────────────────────────────────────────
print("#!c=0")  # disable client-side caching so contact URLs always stay fresh
print("`!" + bot_name + "`!")
print("Multi-Protocol Mesh Network Node")
print("`l")
print("")
print(">")
print("`!Network Addresses`!")
print("")
print("`_Node Name`_")
print(bot_name)
print("")
if lxmf_available:
    print("`_LXMF / Reticulum`_")
    print("lxmf:" + (lxmf_addr or "unavailable"))
    print("")
if mesh_adapters:
    print("`_Meshtastic`_")
    for _sec, _port, _preset, _json in mesh_adapters:
        _addr, _contact_url = _mesh_info_for(_json)
        _preset_label = _preset.replace("_", " ").title()
        if len(mesh_adapters) > 1:
            print(f"{_preset_label}:  {_addr}")
        else:
            print(f"{_addr}  ({_preset_label})")
        if _contact_url:
            print(f"     `Fad8{_contact_url}`f")
    print("")
if mc_addr is not None:
    print("`_MeshCore`_")
    print(mc_addr)
    if mc_contact_url:
        print(f"     `Fad8{mc_contact_url}`f")
    print("")
print("`Fbbf`[Activity Feed`:/page/nodebot/activity.mu`]`f")
print("`Fbbf`[System Diagnostics`:/page/nodebot/diag.mu`]`f")
print("")

if os.path.isfile(README_PATH):
    with open(README_PATH, encoding="utf-8") as f:
        readme = f.read()
    print(md_to_micron(readme))
else:
    print(">")
    print("README not found at " + README_PATH)

print("")
print(">")
print("`[github.com/JamesM92/NodeBot`Fbbf`]")
