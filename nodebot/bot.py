import configparser
import importlib
import os
import signal
import sys
import time

from .meshbridge.engine import NodeBot as MeshEngine
from .meshbridge.state import StateStore
from . import commands as _commands
from . import logger as _logger


class NodeBot:
    """
    Protocol-agnostic orchestrator for NodeBot MeshBridge.

    Knows nothing about RNS, LXMF, Meshtastic, or any other protocol.
    Loads the engine, discovers transport adapters, and runs the event loop.
    Each adapter owns its own network-stack initialization.
    """

    def __init__(self):

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "config.ini")
        _config = configparser.ConfigParser()
        _config.read(_config_path)
        self._config = _config

        print("[nodebot] initializing")

        raw_path = self._config.get("bot", "storage_path", fallback="~/.nodebot/lxmf_storage")
        self.storage_path = os.path.expanduser(raw_path)
        self.transports = {}
        self.running = False

        self.engine = MeshEngine(name=self._config.get("bot", "name", fallback="NodeBot"))
        self._state = StateStore()
        _commands.set_state(self._state)

        _logger.init(self._config)
        self._load_transports()
        self.engine.transports = self.transports
        self._start()
        _commands.load_plugins()

        signal.signal(signal.SIGUSR1, self._handle_sigusr1)
        signal.signal(signal.SIGTERM, lambda s, f: self._shutdown())

        print("[nodebot] running")

        self._main_loop()

    # =====================================================
    # TRANSPORT LOADER
    # =====================================================

    def _load_transports(self):

        transport_dir = os.path.join(os.path.dirname(__file__), "transports")

        if not os.path.isdir(transport_dir):
            print("[nodebot] no transports directory found")
            return

        pass  # transports loaded as nodebot.transports package

        for file in os.listdir(transport_dir):

            if not file.endswith(".py") or file.startswith("_"):
                continue

            name = file[:-3]

            try:
                try:
                    module = importlib.import_module(f'nodebot.transports.{name}')
                except Exception as e:
                    print(f"[nodebot] transport {name} import failed: {e}")
                    continue

                adapter_class_names = [
                    attr for attr in dir(module)
                    if "adapter" in attr.lower() and not attr.startswith("_")
                ]

                if not adapter_class_names:
                    print(f"[nodebot] transport {name}: no adapter class found, skipping")
                    continue

                try:
                    adapter_class = getattr(module, adapter_class_names[0])
                    adapter = adapter_class(storage_path=self.storage_path, engine=self.engine)
                except Exception as e:
                    print(f"[nodebot] transport {name} failed to initialize: {e}")
                    continue

                self.transports[name] = adapter
                print(f"[nodebot] loaded transport: {name}")

            except Exception as e:
                print(f"[nodebot] transport {name} unexpected error: {e}")

    # =====================================================
    # START TRANSPORTS
    # =====================================================

    def _start(self):

        print("[nodebot] starting transports")

        for name, adapter in self.transports.items():
            try:
                if hasattr(adapter, "start_worker"):
                    adapter.start_worker()
                print(f"[nodebot] started: {name}")
            except Exception as e:
                print(f"[nodebot] transport {name} start failed: {e}")

        if not self.transports:
            print("[nodebot] warning: no transports loaded")

        self.running = True

    # =====================================================
    # SIGNAL HANDLERS
    # =====================================================

    def _handle_sigusr1(self, signum, frame):
        print("[nodebot] SIGUSR1 received — announcing on all transports")
        announced = self.engine.announce_all()
        if announced:
            print(f"[nodebot] announced on: {', '.join(announced)}")
        else:
            print("[nodebot] no transports with announce support")

    # =====================================================
    # MAIN LOOP
    # =====================================================

    _HOUSEKEEPING_INTERVAL = 60    # session expiry check (seconds)
    _ANNOUNCE_INTERVAL     = 300   # periodic re-announce (seconds)

    def _main_loop(self):

        print("[nodebot] event loop running")

        last_housekeeping = time.time()
        last_announce     = time.time()

        while self.running:
            try:
                time.sleep(5)

                now = time.time()

                if now - last_housekeeping >= self._HOUSEKEEPING_INTERVAL:
                    last_housekeeping = now
                    self._housekeeping()

                if now - last_announce >= self._ANNOUNCE_INTERVAL:
                    last_announce = now
                    self.engine.announce_all()

            except KeyboardInterrupt:
                print("[nodebot] shutdown requested")
                self._shutdown()
                break

            except Exception as e:
                print(f"[nodebot] loop error: {e}")

    def _housekeeping(self):
        import sys
        relay_mod = sys.modules.get("nodebot.plugins.relay")
        if relay_mod:
            try:
                relay_mod._expire_sessions()
            except Exception as e:
                print(f"[nodebot] housekeeping error: {e}")

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def _shutdown(self):

        self.running = False
        print("[nodebot] shutting down")

        try:
            for name, adapter in self.transports.items():
                try:
                    if hasattr(adapter, "stop"):
                        adapter.stop()
                    print(f"[nodebot] stopped: {name}")
                except Exception as e:
                    print(f"[nodebot] transport {name} stop error: {e}")

            print("[nodebot] stopped")

        except Exception as e:
            print(f"[nodebot] shutdown error: {e}")


# =====================================================
# ENTRYPOINT
# =====================================================

if __name__ == "__main__":
    try:
        bot = NodeBot()
    except KeyboardInterrupt:
        print("[nodebot] shutdown requested")
    except Exception as e:
        print(f"[nodebot] error: {e}")
        sys.exit(1)
