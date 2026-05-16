#!/usr/bin/python3
# NodeBot discovery / advertisement page for NomadNet.
# Intended audience: people who stumbled across this node with no prior context.
#
# NOTE: PROJECT_DIR below is substituted by install_lxmf.sh at install time.
# Do not edit the deployed copy directly — edit this template and re-run the installer.

import os, json, glob, importlib.util, configparser

PROJECT_DIR = "PROJECT_DIR_PLACEHOLDER"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.ini")

config = configparser.ConfigParser()
config.read(CONFIG_PATH)

bot_name = config.get("bot", "name",         fallback="NodeBot").strip()
storage  = os.path.expanduser(
    config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage").strip()
)
mesh_port = config.get("meshtastic", "port", fallback="").strip()
mc_port   = config.get("meshcore",   "port", fallback="").strip()

# ── Protocol detection ────────────────────────────────────────
def _pkg_installed(name):
    if importlib.util.find_spec(name) is not None:
        return True
    pattern = os.path.join(PROJECT_DIR, ".venv/lib/python*/site-packages", name)
    return bool(glob.glob(pattern))

lxmf_available = _pkg_installed("RNS")
mesh_available  = bool(mesh_port) and _pkg_installed("meshtastic")
mc_available    = bool(mc_port)   and _pkg_installed("meshcore")

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
            lxmf_addr = None

# ── Meshtastic node ID ────────────────────────────────────────
mesh_id = None
if mesh_available:
    lora_json = os.path.join(storage, "meshtastic_lora.json")
    if os.path.isfile(lora_json):
        try:
            with open(lora_json) as f:
                d = json.load(f)
            num = d.get("my_node_num")
            if num:
                mesh_id = "!{:08x}".format(int(num))
        except Exception:
            pass

# ── MeshCore public key prefix ────────────────────────────────
mc_id = None
if mc_available:
    mc_json = os.path.join(storage, "meshcore_node.json")
    if os.path.isfile(mc_json):
        try:
            with open(mc_json) as f:
                d = json.load(f)
            pubkey = d.get("public_key", "")
            if pubkey:
                mc_id = pubkey[:8]
        except Exception:
            pass

# ── Active network summary ────────────────────────────────────
nets = []
if lxmf_available: nets.append("LXMF")
if mesh_available:  nets.append("Meshtastic")
if mc_available:    nets.append("MeshCore")
net_str = " · ".join(nets) if nets else "offline"

p = print

# ─────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────
p("")
p("`F5cc`!" + bot_name + "`!`f")
p("`F888Mesh Network Chatbot`f")
p("`F555" + net_str + "`f")
p("`l")
p("")
p("-")
p("")

# ─────────────────────────────────────────────────────────────
# Hook
# ─────────────────────────────────────────────────────────────
p("You have reached a live mesh network node.")
p("")
p("`!" + bot_name + "`! is an open-source chatbot running on a Raspberry Pi,")
p("connected to the mesh by LoRa radio.")
p("")
p("`F888No internet. No data plan. Just radio waves.`f")
p("")
p("-")
p("")

# ─────────────────────────────────────────────────────────────
# What is it
# ─────────────────────────────────────────────────────────────
p(">What is this?")
p("")
p("  LoRa is a long-range, low-power radio technology. Mesh networks")
p("  built on LoRa can span kilometres with no infrastructure —")
p("  each radio device relays packets for the others.")
p("")
p("  " + bot_name + " is a chatbot that sits on this mesh node,")
p("  listens for messages, and responds to commands. It also bridges")
p("  different mesh radio platforms, so users on different networks")
p("  can reach the same bot — and relay messages to each other.")
p("")
p("-")
p("")

# ─────────────────────────────────────────────────────────────
# Features
# ─────────────────────────────────────────────────────────────
p(">What can it do?")
p("")
p("  `Ffa6+`f  Answer commands — time, location, diagnostics, weather")
p("  `Ffa6+`f  Relay messages between Meshtastic, MeshCore, and LXMF")
p("  `Ffa6+`f  Share the node's GPS position on request")
p("  `Ffa6+`f  Broadcast announcements to all connected networks")
p("  `Ffa6+`f  Run 24/7 on low power — solar or battery friendly")
p("")
p("-")
p("")

# ─────────────────────────────────────────────────────────────
# Try it now
# ─────────────────────────────────────────────────────────────
p(">Try it now")
p("")
p("  Send a message to this bot and try one of these:")
p("")
p("    `Ffa6help`f      list everything the bot can do")
p("    `Ffa6ping`f      check if the bot is alive")
p("    `Ffa6time`f      current time at this node")
p("    `Ffa6about`f     more info about this deployment")
p("    `Ffa6version`f   bot software version")
p("")
p("-")
p("")

# ─────────────────────────────────────────────────────────────
# Addresses
# ─────────────────────────────────────────────────────────────
p(">How to reach " + bot_name)
p("")

if lxmf_available:
    p("  `_LXMF / Reticulum`_")
    if lxmf_addr:
        p("  " + lxmf_addr)
    else:
        p("  start NodeBot once to generate an address")
    p("  Use `[Sideband`] or `[NomadNet`] to connect.")
    p("")

if mesh_available:
    p("  `_Meshtastic`_")
    if mesh_id:
        p("  " + mesh_id)
    else:
        p("  start NodeBot once to populate")
    p("  Use the Meshtastic app. Search for " + bot_name + " on the mesh.")
    p("")

if mc_available:
    p("  `_MeshCore`_")
    if mc_id:
        p("  pubkey prefix: " + mc_id)
    else:
        p("  start NodeBot once to populate")
    p("  Use the MeshCore companion app.")
    p("")

if not any([lxmf_available, mesh_available, mc_available]):
    p("  No protocols are currently active.")
    p("  The node operator may still be configuring this device.")
    p("")

p("-")
p("")

# ─────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────
p(">Run your own node")
p("")
p("`F888NodeBot is free and open-source. Anyone can run one.`f")
p("Raspberry Pi + a LoRa radio module is all you need.")
p("")
p("`[github.com/JamesM92/NodeBot`Fbbf`]")
p("")
p("`F555" + bot_name + " · NodeBot v0.3.2`f")
p("")
