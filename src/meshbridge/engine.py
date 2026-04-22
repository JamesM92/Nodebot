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

        # Sender is in a relay session but didn't use Respond: — notify, don't forward
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
        "mc":       "meshcore_adapter",
        "meshcore": "meshcore_adapter",
        "lxmf":     "lxmf_adapter",
    }

    # MeshCore practical message size limit
    _MC_MSG_LIMIT = 190

    @staticmethod
    def _split_text(proto, text):
        """Split text into chunks that fit the transport's message limit."""
        limit = NodeBot._MC_MSG_LIMIT if proto in ("mc", "meshcore") else None
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

    def send(self, destination, text):
        """Route an outbound message to a transport by 'proto:addr' destination."""
        # Raw bytes destination — treat as LXMF hash directly
        if isinstance(destination, (bytes, bytearray)):
            adapter = self.transports.get("lxmf_adapter")
            if not adapter:
                print("[engine] send: lxmf_adapter not loaded")
                return
            try:
                adapter.send_message(destination, text)
            except Exception as e:
                print(f"[engine] send error (lxmf bytes): {e}")
            return

        if ":" not in destination:
            print(f"[engine] send: invalid destination '{destination}'")
            return

        proto, addr = destination.split(":", 1)
        adapter_name = self._PROTO_MAP.get(proto.lower())

        if not adapter_name:
            print(f"[engine] send: unknown protocol '{proto}'")
            return

        adapter = self.transports.get(adapter_name)
        if not adapter:
            print(f"[engine] send: adapter '{adapter_name}' not loaded")
            return

        chunks = self._split_text(proto.lower(), text)
        try:
            for chunk in chunks:
                if adapter_name == "lxmf_adapter":
                    adapter.send_message(bytes.fromhex(addr), chunk)
                elif adapter_name == "meshcore_adapter":
                    adapter._send_reply(addr, chunk)
        except Exception as e:
            print(f"[engine] send error to {destination}: {e}")

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
