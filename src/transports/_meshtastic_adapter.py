# transports/meshtastic_adapter.py

import time
import threading

from meshbridge import NodeBot
import commands


class Transport:
    """
    Meshtastic transport adapter for MeshBridge.

    Responsibilities:
    - connect to Meshtastic device/client
    - receive messages
    - normalize sender
    - pass to NodeBot engine
    - send replies back

    No routing or command logic here.
    """

    def __init__(self, name="NodeBot"):

        self.name = name

        self.core = NodeBot(name)

        self.client = None
        self.running = False

        self._init_client()

        commands.load_plugins()

        print("?? Meshtastic adapter initialized")

    # =====================================================
    # INIT CLIENT
    # =====================================================

    def _init_client(self):

        try:
            # Try modern Meshtastic Python API
            import meshtastic
            import meshtastic.serial_interface

            try:
                self.client = meshtastic.serial_interface.SerialInterface()

            except Exception:
                # fallback: try TCP interface if available
                try:
                    self.client = meshtastic.tcp_interface.TCPInterface()
                except Exception:
                    self.client = None

        except Exception as e:
            print("?? Meshtastic init failed:", repr(e))
            self.client = None

    # =====================================================
    # START
    # =====================================================

    def start(self):

        self.running = True

        if not self.client:
            print("?? Meshtastic client not available")
            return

        try:
            # register callback (Meshtastic standard pattern)
            if hasattr(self.client, "onReceive"):
                self.client.onReceive = self._on_receive

            elif hasattr(self.client, "registerCallback"):
                self.client.registerCallback(self._on_receive)

        except Exception as e:
            print("?? Meshtastic callback setup failed:", repr(e))

        print("?? Meshtastic adapter started")

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        self.running = False

        try:
            if self.client and hasattr(self.client, "close"):
                self.client.close()

        except Exception as e:
            print("?? Meshtastic stop error:", repr(e))

        self.client = None

        print("?? Meshtastic adapter stopped")

    # =====================================================
    # RECEIVE CALLBACK
    # =====================================================

    def _on_receive(self, packet, interface=None):

        if not self.running:
            return

        try:
            # Meshtastic packet structure varies by version
            msg = getattr(packet, "decoded", None)

            if msg is None:
                return

            text = getattr(msg, "text", None)
            if not text:
                return

            # sender resolution (multiple fallbacks)
            sender_id = (
                getattr(packet, "fromId", None)
                or getattr(packet, "from", None)
                or getattr(packet, "sender", None)
                or getattr(packet, "source", None)
            )

            if sender_id is None:
                return

            sender = self.core.normalize_sender(sender_id, "mt")
            message = str(text).strip()

        except Exception as e:
            print("?? Meshtastic receive error:", repr(e))
            return

        # pass into core engine
        self.core.handle_message(sender, message, self.send)

    # =====================================================
    # SEND
    # =====================================================

    def send(self, destination, message):

        try:
            if ":" in destination:
                proto, destination = destination.split(":", 1)

                if proto != "mt":
                    return

            if not self.client:
                print("?? Meshtastic client not available")
                return

            # Try common Meshtastic send APIs

            if hasattr(self.client, "sendText"):
                self.client.sendText(message, destinationId=destination)
                return

            if hasattr(self.client, "send_message"):
                self.client.send_message(destination, message)
                return

            if hasattr(self.client, "send"):
                self.client.send(destination, message)
                return

            print("?? Meshtastic: no supported send method")

        except Exception as e:
            print("?? Meshtastic send error:", repr(e))

    # =====================================================
    # OPTIONAL LOOP (debug mode)
    # =====================================================

    def run(self):

        print("?? Meshtastic adapter running")

        try:
            while self.running:
                time.sleep(1)

        except KeyboardInterrupt:
            self.stop()