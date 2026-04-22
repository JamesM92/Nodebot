import configparser
import os
import socket
import threading
import time
import RNS
import LXMF


class LXMFAdapter:
    """
    LXMF transport adapter for NodeBot.

    Connects to whichever RNS instance is already running (NomadNet, rnsd, or
    standalone) — RNS.Reticulum() handles shared-instance detection automatically.
    Maintains a persistent identity so the bot's LXMF address survives restarts.
    """

    def __init__(self, storage_path, engine):

        self.storage_path = storage_path
        self.engine = engine

        self.identity = None
        self.router = None
        self.delivery_destination = None
        self.worker = None
        self.running = False

        # sender_hash (bytes) -> RNS.Destination (OUT) cached from incoming messages
        self._sources = {}

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "..", "config.ini")
        self._config = configparser.ConfigParser()
        self._config.read(_config_path)

        self.display_name = self._config.get("bot", "name", fallback="NodeBot").strip()

        os.makedirs(storage_path, exist_ok=True)

        self._init_rns()   # must be first — everything else depends on RNS being ready

        RNS.log("[lxmf_adapter] initializing...", RNS.LOG_NOTICE)

        self._load_identity()
        self._init_router()

    # =====================================================
    # RNS INITIALIZATION
    # =====================================================

    def _init_rns(self):

        kwargs = {}
        config_dir = self._config.get("rns", "config_dir", fallback="").strip()
        if config_dir:
            kwargs["configdir"] = os.path.expanduser(config_dir)

        # When run manually (outside systemd), give a shared RNS instance
        # (NomadNet / rnsd) a brief window to be ready before connecting.
        # Under systemd the ExecStartPre check in nodebot.service already
        # guarantees the instance is up, so this exits immediately.
        self._wait_for_shared_instance(port=37428, timeout=10)

        # Auto-connects to the shared instance if one is running on the
        # shared_instance_port; otherwise starts a standalone RNS instance.
        self.reticulum = RNS.Reticulum(**kwargs)

        RNS.log("[lxmf_adapter] RNS ready (shared instance or standalone)", RNS.LOG_NOTICE)

    def _wait_for_shared_instance(self, port, timeout=10):
        # RNS 1.1+ on Linux uses an abstract Unix socket (@rns/default)
        # rather than a TCP port. Fall back to TCP for other platforms.
        use_unix = hasattr(socket, "AF_UNIX")

        for attempt in range(timeout):
            try:
                if use_unix:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect("\x00rns/default")
                    s.close()
                else:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        pass

                if attempt > 0:
                    RNS.log("[lxmf_adapter] RNS shared instance ready", RNS.LOG_NOTICE)
                return

            except OSError:
                if attempt == 0:
                    RNS.log(
                        "[lxmf_adapter] waiting for RNS shared instance...",
                        RNS.LOG_NOTICE
                    )
                time.sleep(1)

        RNS.log(
            "[lxmf_adapter] no shared RNS instance found, starting standalone",
            RNS.LOG_NOTICE
        )

    # =====================================================
    # IDENTITY — persistent across restarts
    # =====================================================

    def _load_identity(self):

        identity_path = os.path.join(self.storage_path, "identity")
        identity_txt_path = identity_path + ".txt"

        try:
            if os.path.isfile(identity_path):
                self.identity = RNS.Identity.from_file(identity_path)
                if self.identity is None:
                    raise ValueError("Invalid identity data in file")
                RNS.log(
                    f"[lxmf_adapter] loaded identity {RNS.prettyhexrep(self.identity.hash)}",
                    RNS.LOG_NOTICE
                )
            else:
                self.identity = RNS.Identity()
                self.identity.to_file(identity_path)
                RNS.log(
                    f"[lxmf_adapter] created identity {RNS.prettyhexrep(self.identity.hash)}",
                    RNS.LOG_NOTICE
                )

            # Always keep the plain-text copy in sync
            with open(identity_txt_path, "w") as f:
                f.write(self.identity.get_private_key().hex() + "\n")

        except Exception as e:
            RNS.log(f"[lxmf_adapter] identity init failed: {e}", RNS.LOG_ERROR)
            raise

    # =====================================================
    # ROUTER INITIALIZATION
    # =====================================================

    def _init_router(self):

        if self.router is not None:
            return

        try:
            self.router = LXMF.LXMRouter(
                identity=self.identity,
                storagepath=self.storage_path
            )

            # register_delivery_identity returns the RNS.Destination we use as
            # the "from" address on every outbound message
            self.delivery_destination = self.router.register_delivery_identity(
                self.identity,
                display_name=self.display_name
            )

            RNS.log(
                f"[lxmf_adapter] LXMF ready — bot address: {RNS.prettyhexrep(self.delivery_destination.hash)}",
                RNS.LOG_NOTICE
            )

        except Exception as e:
            RNS.log(f"[lxmf_adapter] router init failed: {e}", RNS.LOG_ERROR)
            raise

    # =====================================================
    # WORKER MANAGEMENT
    # =====================================================

    def start_worker(self):

        if self.worker and self.worker.is_alive():
            RNS.log("[lxmf_adapter] worker already running", RNS.LOG_NOTICE)
            return

        self.running = True
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

        RNS.log("[lxmf_adapter] worker started", RNS.LOG_NOTICE)

        # Announce immediately so other nodes can discover this bot without
        # waiting for the router's background announce cycle.
        try:
            self.router.announce(self.delivery_destination.hash)
            RNS.log("[lxmf_adapter] announced on network", RNS.LOG_NOTICE)
        except Exception as e:
            RNS.log(f"[lxmf_adapter] announce failed: {e}", RNS.LOG_WARNING)

    def _worker_loop(self):

        RNS.log("[lxmf_adapter] worker loop running", RNS.LOG_NOTICE)

        try:
            self.router.register_delivery_callback(self._on_message)
            RNS.log("[lxmf_adapter] delivery callback registered", RNS.LOG_NOTICE)

        except Exception as e:
            RNS.log(f"[lxmf_adapter] callback registration failed: {e}", RNS.LOG_ERROR)

        # LXMF is callback-driven; just keep the thread alive
        while self.running:
            try:
                time.sleep(1)
            except Exception as e:
                RNS.log(f"[lxmf_adapter] worker error: {e}", RNS.LOG_ERROR)
                time.sleep(2)

    # =====================================================
    # MESSAGE HANDLER (inbound)
    # =====================================================

    def _on_message(self, message):

        try:
            content = ""
            if hasattr(message, "content"):
                raw = message.content
                if isinstance(raw, (bytes, bytearray)):
                    content = raw.decode("utf-8", errors="ignore")
                else:
                    content = str(raw)

            sender_hash = getattr(message, "source_hash", None)

            if sender_hash is None:
                RNS.log("[lxmf_adapter] message missing source_hash, dropping", RNS.LOG_WARNING)
                return

            # Cache the sender's OUT destination so we can reply later.
            # LXMF sets message.source to an RNS.Destination(OUT) when the
            # sender's identity is known (recalled from routing tables).
            source = getattr(message, "source", None)
            if source is not None:
                self._sources[sender_hash] = source

            RNS.log(
                f"[lxmf_adapter] msg from {RNS.prettyhexrep(sender_hash)}: {content!r}",
                RNS.LOG_NOTICE
            )

            if self.engine:
                self.engine.handle_message(
                    sender=sender_hash,
                    message=content,
                    send_callback=self._send_reply
                )

        except Exception as e:
            RNS.log(f"[lxmf_adapter] message handling error: {e}", RNS.LOG_ERROR)

    # =====================================================
    # REPLY CALLBACK (called by engine)
    # =====================================================

    def _send_reply(self, sender_hash, content):

        try:
            self.send_message(sender_hash, content)
        except Exception as e:
            RNS.log(f"[lxmf_adapter] send reply error: {e}", RNS.LOG_ERROR)

    # =====================================================
    # OUTBOUND MESSAGE
    # =====================================================

    def send_message(self, destination_hash, content, notify_cb=None):

        try:
            if not self.router:
                raise RuntimeError("LXMF router not initialized")

            # Prefer the destination recalled from a previous incoming message
            dest = self._sources.get(destination_hash)

            if dest is None:
                # Fallback: try to recall the identity for this delivery hash
                dest_identity = RNS.Identity.recall(destination_hash)
                if dest_identity is None:
                    RNS.log(
                        f"[lxmf_adapter] no route to {RNS.prettyhexrep(destination_hash)}, dropping",
                        RNS.LOG_WARNING
                    )
                    if notify_cb:
                        notify_cb(False)
                    return
                dest = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )

            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="ignore")

            msg = LXMF.LXMessage(
                dest,
                self.delivery_destination,
                content,
                desired_method=LXMF.LXMessage.DIRECT
            )

            if notify_cb:
                def _on_delivery(message):
                    notify_cb(message.state == LXMF.LXMessage.STATE_DELIVERED)
                msg.delivery_callback = _on_delivery

            self.router.handle_outbound(msg)

            RNS.log(
                f"[lxmf_adapter] sent to {RNS.prettyhexrep(destination_hash)}",
                RNS.LOG_NOTICE
            )

        except Exception as e:
            RNS.log(f"[lxmf_adapter] send failed: {e}", RNS.LOG_ERROR)
            if notify_cb:
                notify_cb(False)

    # =====================================================
    # ANNOUNCE
    # =====================================================

    def announce(self):
        try:
            self.router.announce(self.delivery_destination.hash)
            RNS.log("[lxmf_adapter] announced on network", RNS.LOG_NOTICE)
        except Exception as e:
            RNS.log(f"[lxmf_adapter] announce failed: {e}", RNS.LOG_WARNING)

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        self.running = False
        RNS.log("[lxmf_adapter] stopped", RNS.LOG_NOTICE)
