# /src/meshbridge/transport_guard.py

import time
import traceback
import threading


class TransportGuard:
    """
    Sandbox layer for transport adapters.

    Protects system from:
    - broken receive callbacks
    - send crashes
    - infinite loops
    - repeated adapter failures
    """

    def __init__(self):

        self.failures = {}   # transport -> count
        self.disabled = set()

        self.lock = threading.RLock()

        # tuning
        self.MAX_FAILURES = 5
        self.SEND_TIMEOUT = 1.0
        self.RECEIVE_TIMEOUT = 1.0

        print("??? TransportGuard initialized")

    # =====================================================
    # FAILURE TRACKING
    # =====================================================

    def record_failure(self, transport):

        with self.lock:

            self.failures[transport] = self.failures.get(transport, 0) + 1

            if self.failures[transport] >= self.MAX_FAILURES:
                self.disabled.add(transport)

                print(f"?? Transport disabled: {transport}")

    def is_disabled(self, transport):

        return transport in self.disabled

    def reset(self, transport):

        with self.lock:
            self.failures.pop(transport, None)
            self.disabled.discard(transport)

    # =====================================================
    # SAFE RECEIVE WRAPPER
    # =====================================================

    def safe_receive(self, transport, func, *args, **kwargs):

        if self.is_disabled(transport):
            return

        result = {"ok": True}

        def target():
            try:
                func(*args, **kwargs)
            except Exception:
                result["ok"] = False
                result["trace"] = traceback.format_exc()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(self.RECEIVE_TIMEOUT)

        if t.is_alive():
            result["ok"] = False
            result["trace"] = "receive timeout"

        if not result["ok"]:
            self.record_failure(transport)
            print(f"?? Receive failure [{transport}]:\n{result.get('trace')}")

    # =====================================================
    # SAFE SEND WRAPPER
    # =====================================================

    def safe_send(self, transport, func, destination, message):

        if self.is_disabled(transport):
            return

        result = {"ok": True}

        def target():
            try:
                func(destination, message)
            except Exception:
                result["ok"] = False
                result["trace"] = traceback.format_exc()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(self.SEND_TIMEOUT)

        if t.is_alive():
            result["ok"] = False
            result["trace"] = "send timeout"

        if not result["ok"]:
            self.record_failure(transport)
            print(f"?? Send failure [{transport}]:\n{result.get('trace')}")