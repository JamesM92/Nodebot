import time
import RNS


class LXMFWorker:
    """
    LXMF message bridge worker.

    Responsibilities:
    - Receive LXMF messages via callback
    - Forward messages to MeshBridge engine
    - Provide safe isolation from transport failures
    - Keep systemd process alive cleanly
    """

    def __init__(self, router, engine):

        self.router = router
        self.engine = engine

        self.running = False

        RNS.log("[lxmf_worker] initialized", RNS.LOG_NOTICE)

    # =====================================================
    # START WORKER
    # =====================================================

    def start(self):

        if self.running:
            RNS.log("[lxmf_worker] already running", RNS.LOG_NOTICE)
            return

        self.running = True

        RNS.log("[lxmf_worker] started", RNS.LOG_NOTICE)

        # -----------------------------------------------------
        # REGISTER CALLBACK (SAFE GUARDED)
        # -----------------------------------------------------

        try:
            if self.router and hasattr(self.router, "register_delivery_callback"):

                self.router.register_delivery_callback(self._on_message)

                RNS.log(
                    "[lxmf_worker] delivery callback registered",
                    RNS.LOG_NOTICE
                )

            else:
                RNS.log(
                    "[lxmf_worker] router has no callback support",
                    RNS.LOG_WARNING
                )

        except Exception as e:
            RNS.log(f"[lxmf_worker] callback registration failed: {e}", RNS.LOG_ERROR)

        # -----------------------------------------------------
        # MAIN LOOP (KEEP ALIVE ONLY)
        # -----------------------------------------------------

        self._loop()

    # =====================================================
    # MAIN LOOP (WATCHDOG STYLE)
    # =====================================================

    def _loop(self):

        while self.running:
            try:
                time.sleep(1)

            except Exception as e:
                RNS.log(f"[lxmf_worker] loop error: {e}", RNS.LOG_ERROR)
                time.sleep(2)

    # =====================================================
    # MESSAGE HANDLER (CRITICAL PATH)
    # =====================================================

    def _on_message(self, message):

        try:
            # -------------------------------------------------
            # SAFE CONTENT PARSING
            # -------------------------------------------------

            content = ""

            if hasattr(message, "content"):
                if isinstance(message.content, (bytes, bytearray)):
                    content = message.content.decode("utf-8", errors="ignore")
                else:
                    content = str(message.content)

            sender = getattr(message, "source_hash", "unknown")

            RNS.log(
                f"[lxmf_worker] msg from {RNS.prettyhexrep(sender)}: {content}",
                RNS.LOG_NOTICE
            )

            # -------------------------------------------------
            # ENGINE HANDOFF
            # -------------------------------------------------

            if self.engine:

                try:
                    self.engine.handle_message(
                        sender=sender,
                        message=content,
                        send_callback=self._send_reply
                    )

                except Exception as e:
                    RNS.log(
                        f"[lxmf_worker] engine error: {e}",
                        RNS.LOG_ERROR
                    )

        except Exception as e:
            RNS.log(
                f"[lxmf_worker] message handling error: {e}",
                RNS.LOG_ERROR
            )

    # =====================================================
    # REPLY HANDLER (FUTURE EXTENSION POINT)
    # =====================================================

    def _send_reply(self, sender, message):

        try:
            RNS.log(
                f"[lxmf_worker] reply to {RNS.prettyhexrep(sender)}: {message}",
                RNS.LOG_NOTICE
            )

            # NOTE:
            # Actual LXMF send is handled in LXMFAdapter.send_message()
            # This keeps worker decoupled from transport layer.

        except Exception as e:
            RNS.log(f"[lxmf_worker] send reply error: {e}", RNS.LOG_ERROR)

    # =====================================================
    # STOP WORKER
    # =====================================================

    def stop(self):

        self.running = False

        RNS.log("[lxmf_worker] stopped", RNS.LOG_NOTICE)