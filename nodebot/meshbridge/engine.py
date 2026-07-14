# meshbridge/engine.py

import collections
import time
from .. import commands


class NodeBot:
    """
    Core message engine (transport-agnostic).
    """

    def __init__(self, name="NodeBot"):

        self.name = name
        self.lockdown = False
        self.allowlist_mode = False
        self.allowlist = set()

        self.state = {
            "messages":   0,
            "start_time": time.time(),
            "stats": {
                "total":         0,
                "per_user":      {},
                "per_command":   {},
                "per_transport": {},
            },
            "seen":  {},
            "nicks": {},
        }

        self.sessions   = {}
        self.transports = {}   # populated by nodebot.py after transport loading

        # per-transport outbound queue for messages sent while a transport is down
        self._outbound_queue = {}  # adapter_name -> deque of (destination, text, notify_cb)

        commands.set_bot(self)

        print(f"MeshBridge engine initialized: {name}")

    # =====================================================
    # TRANSPORT DETECTION
    # =====================================================

    @staticmethod
    def _detect_transport(sender):
        """Return 'lxmf' | 'meshtastic' | 'meshcore' from sender type/format."""
        if isinstance(sender, (bytes, bytearray)):
            return "lxmf"
        s = str(sender)
        if s.startswith("mesh:"):
            return "meshtastic"
        if s.startswith("lxmf:"):
            return "lxmf"
        if s.startswith("mc:") or s.startswith("meshcore:"):
            return "meshcore"
        # Raw pubkey prefix from MeshCore DMs (no colon)
        return "meshcore"

    # =====================================================
    # MESSAGE ENTRY POINT
    # =====================================================

    def handle_message(self, sender, message, send_callback, nick=None):

        self.state["messages"] += 1
        self.state["stats"]["total"] += 1

        transport = self._detect_transport(sender)
        pt = self.state["stats"]["per_transport"]
        pt[transport] = pt.get(transport, 0) + 1

        if sender not in self.state["stats"]["per_user"]:
            self.state["stats"]["per_user"][sender] = 0
        self.state["stats"]["per_user"][sender] += 1

        sender_key = sender.hex() if isinstance(sender, (bytes, bytearray)) else str(sender)
        is_first = sender_key not in self.state["seen"]
        self.state["seen"][sender_key] = time.time()
        if nick:
            self.state["nicks"][sender_key] = nick

        if not message:
            return

        message = message.strip()

        if self.lockdown and not commands.is_admin(sender):
            send_callback(sender, "Bot is in lockdown mode.")
            return

        if self.allowlist_mode and not commands.is_admin(sender):
            if sender_key not in self.allowlist:
                send_callback(sender, "Bot is restricted.")
                return

        # 1. Commands
        response, handled = commands.handle_command(message, sender)

        if handled:
            if response:
                self._send_chunked(sender, str(response), send_callback, transport)
            return

        # 2. Non-command plugin hooks (relay auto-forward)
        plugin_handled = self._handle_plugins(sender, message, send_callback)

        # 3. First-message greeting — send help if the user's first message
        # wasn't a recognized command and no plugin handled it.
        if not plugin_handled and is_first:
            response, _ = commands.handle_command("help", sender)
            if response:
                self._send_chunked(sender, str(response), send_callback, transport)

    def _send_chunked(self, sender, text, send_callback, transport):
        """Split text for the transport's limit and call send_callback per chunk."""
        if transport == "meshtastic":
            chunks = self._split_text("mesh", text)
        elif transport == "meshcore":
            chunks = self._split_text("mc", text)
        else:
            chunks = [text]
        for chunk in chunks:
            send_callback(sender, chunk)

    # =====================================================
    # PLUGIN HOOK
    # =====================================================

    def _handle_plugins(self, sender, message, send_callback):
        import sys
        relay_mod = sys.modules.get("plugins.relay")
        if relay_mod is None:
            return False

        session_key, _ = relay_mod._resolve_session(sender)
        if not session_key:
            return False

        forwarded = relay_mod.auto_forward(sender, message)
        if not forwarded:
            send_callback(sender, "Relay active. Use: Respond: <message>")
        return True

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
    _MC_MSG_LIMIT   = 160   # MeshCore MAX_TEXT_LEN — composeMsgPacket rejects > 160
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

            line = ""
            for word in para.split(" "):
                trial = (line + " " + word).lstrip() if line else word
                if len(trial) <= limit:
                    line = trial
                else:
                    if line:
                        chunks.append(line)
                    while len(word) > limit:
                        chunks.append(word[:limit])
                        word = word[limit:]
                    line = word
            current = line

        if current:
            chunks.append(current)

        return chunks or [text]

    def _queue_outbound(self, adapter_name, destination, text, notify_cb=None):
        q = self._outbound_queue.setdefault(
            adapter_name, collections.deque(maxlen=50)
        )
        q.append((destination, text, notify_cb))
        print(f"[engine] queued for {adapter_name}: {destination!r} ({len(q)} queued)")

    def flush_outbound(self, adapter_name):
        """Drain the outbound queue for adapter_name, sending each queued message."""
        q = self._outbound_queue.pop(adapter_name, None)
        if not q:
            return 0
        print(f"[engine] flushing {len(q)} queued message(s) for {adapter_name}")
        sent = 0
        for destination, text, notify_cb in q:
            try:
                self.send(destination, text, notify_cb=notify_cb)
                sent += 1
            except Exception as e:
                print(f"[engine] flush send error to {destination}: {e}")
        return sent

    def send(self, destination, text, notify_cb=None):
        """Route an outbound message to a transport by 'proto:addr' destination."""
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
            print(f"[engine] send: adapter '{adapter_name}' not loaded, queuing")
            self._queue_outbound(adapter_name, destination, text, notify_cb)
            return

        if hasattr(adapter, "is_connected") and not adapter.is_connected:
            print(f"[engine] send: {adapter_name} is down, queuing")
            self._queue_outbound(adapter_name, destination, text, notify_cb)
            return

        chunks = self._split_text(proto.lower(), text)
        try:
            for i, chunk in enumerate(chunks):
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
        adapters = [(name, a) for name, a in self.transports.items() if hasattr(a, "announce")]
        for i, (name, adapter) in enumerate(adapters):
            try:
                adapter.announce()
                announced.append(name)
            except Exception as e:
                print(f"[engine] announce failed on {name}: {e}")
            if i < len(adapters) - 1:
                time.sleep(3)
        return announced

    def reload_plugins(self):
        commands.load_plugins()

    def get_plugin_stats(self):
        return {
            "command_count":  len(commands.COMMANDS),
            "plugins_loaded": list(commands._loaded.keys())
        }
