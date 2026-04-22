# meshbridge/engine.py

import time
import commands


class NodeBot:
    """
    Core message engine (transport-agnostic).
    """

    def __init__(self, name="NodeBot"):

        self.name = name
        self.lockdown = False
        self.state = {"messages": 0, "stats": {"total": 0, "per_user": {}, "per_command": {}}}
        self.sessions = {}
        self.transports = {}   # populated by nodebot.py after transport loading

        commands.set_bot(self)

        print(f"MeshBridge engine initialized: {name}")

    # =====================================================
    # MESSAGE ENTRY POINT
    # =====================================================

    def handle_message(self, sender, message, send_callback):

        self.state["messages"] += 1
        self.state["stats"]["total"] += 1

        if sender not in self.state["stats"]["per_user"]:
            self.state["stats"]["per_user"][sender] = 0
        self.state["stats"]["per_user"][sender] += 1

        if not message:
            return

        message = message.strip()

        if self.lockdown and not commands.is_admin(sender):
            send_callback(sender, "Bot is in lockdown mode.")
            return

        # 1. Commands
        response, handled = commands.handle_command(message, sender)

        if handled:
            if response:
                send_callback(sender, str(response))
            return

        # 2. Future: non-command plugin hooks (relay, etc.)
        self._handle_plugins(sender, message, send_callback)

    # =====================================================
    # PLUGIN HOOK
    # =====================================================

    def _handle_plugins(self, sender, message, send_callback):
        import sys
        relay_mod = sys.modules.get("plugins.relay")
        if relay_mod is None:
            return

        session_key, _ = relay_mod._resolve_session(sender)
        if not session_key:
            return

        # Auto-forward to the session peer. This is how replies chain back
        # through multiple NodeBot hops without any manual Respond: steps.
        forwarded = relay_mod.auto_forward(sender, message)
        if not forwarded:
            # Session found but no peer — sender is a human, remind them.
            send_callback(sender, "Relay active. Use: Respond: <message>")

    # =====================================================
    # LOCKDOWN
    # =====================================================

    def toggle_lockdown(self):
        self.lockdown = not self.lockdown
        return self.lockdown

    # =====================================================
    # SESSION STORAGE
    # =====================================================

    def set_session(self, sender, key, value):

        if sender not in self.sessions:
            self.sessions[sender] = {}

        self.sessions[sender][key] = value

    def get_session(self, sender, key, default=None):
        return self.sessions.get(sender, {}).get(key, default)

    def clear_session(self, sender):
        self.sessions.pop(sender, None)

    # =====================================================
    # PLUGIN MANAGEMENT
    # =====================================================

    # =====================================================
    # ANNOUNCE
    # =====================================================

    # =====================================================
    # OUTBOUND SEND (cross-transport routing)
    # =====================================================

    _PROTO_MAP = {
        "mc":         "meshcore_adapter",
        "meshcore":   "meshcore_adapter",
        "lxmf":       "lxmf_adapter",
        "mesh":       "meshtastic_adapter",
        "meshtastic": "meshtastic_adapter",
    }

    # Per-transport practical message size limits
    _MC_MSG_LIMIT   = 190   # MeshCore
    _MESH_MSG_LIMIT = 220   # Meshtastic (237 byte packet minus overhead)

    @staticmethod
    def _split_text(proto, text):
        """Split text into chunks that fit the transport's message limit."""
        if proto in ("mc", "meshcore"):
            limit = NodeBot._MC_MSG_LIMIT
        elif proto in ("mesh", "meshtastic"):
            limit = NodeBot._MESH_MSG_LIMIT
        else:
            limit = None
        if limit is None or len(text) <= limit:
            return [text]

        chunks = []
        current = ""

        for para in text.split("\n"):
            candidate = (current + "\n" + para).lstrip("\n") if current else para
            if len(candidate) <= limit:
                current = candidate
                continue

            if current:
                chunks.append(current)
                current = ""

            # Word-wrap the paragraph
            line = ""
            for word in para.split(" "):
                trial = (line + " " + word).lstrip() if line else word
                if len(trial) <= limit:
                    line = trial
                else:
                    if line:
                        chunks.append(line)
                    # Hard-cut oversized single tokens (e.g. long hex addresses)
                    while len(word) > limit:
                        chunks.append(word[:limit])
                        word = word[limit:]
                    line = word
            current = line

        if current:
            chunks.append(current)

        return chunks or [text]

    def send(self, destination, text, notify_cb=None):
        """Route an outbound message to a transport by 'proto:addr' destination."""
        # Raw bytes destination — treat as LXMF hash directly
        if isinstance(destination, (bytes, bytearray)):
            adapter = self.transports.get("lxmf_adapter")
            if not adapter:
                print("[engine] send: lxmf_adapter not loaded")
                if notify_cb:
                    notify_cb(False)
                return
            try:
                adapter.send_message(destination, text, notify_cb=notify_cb)
            except Exception as e:
                print(f"[engine] send error (lxmf bytes): {e}")
                if notify_cb:
                    notify_cb(False)
            return

        if ":" not in destination:
            print(f"[engine] send: invalid destination '{destination}'")
            if notify_cb:
                notify_cb(False)
            return

        proto, addr = destination.split(":", 1)
        adapter_name = self._PROTO_MAP.get(proto.lower())

        if not adapter_name:
            print(f"[engine] send: unknown protocol '{proto}'")
            if notify_cb:
                notify_cb(False)
            return

        adapter = self.transports.get(adapter_name)
        if not adapter:
            print(f"[engine] send: adapter '{adapter_name}' not loaded")
            if notify_cb:
                notify_cb(False)
            return

        chunks = self._split_text(proto.lower(), text)
        try:
            for i, chunk in enumerate(chunks):
                # Only attach the callback to the last chunk
                cb = notify_cb if i == len(chunks) - 1 else None
                if adapter_name == "lxmf_adapter":
                    adapter.send_message(bytes.fromhex(addr), chunk, notify_cb=cb)
                elif adapter_name in ("meshcore_adapter", "meshtastic_adapter"):
                    adapter._send_reply(addr, chunk, notify_cb=cb)
        except Exception as e:
            print(f"[engine] send error to {destination}: {e}")
            if notify_cb:
                notify_cb(False)

    # =====================================================
    # ANNOUNCE
    # =====================================================

    def announce_all(self):
        announced = []
        for name, adapter in self.transports.items():
            if hasattr(adapter, "announce"):
                try:
                    adapter.announce()
                    announced.append(name)
                except Exception as e:
                    print(f"[engine] announce failed on {name}: {e}")
        return announced

    def reload_plugins(self):
        commands.load_plugins()

    def get_plugin_stats(self):
        return {
            "command_count": len(commands.COMMANDS),
            "plugins_loaded": list(commands._loaded.keys())
        }
