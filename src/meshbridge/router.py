# /src/meshbridge/router.py

import importlib
import pkgutil
import sys
import os
import time
import threading

import transports


SCAN_INTERVAL = 5


class TransportManager:
    """
    Production-grade hotplug transport router.
    """

    def __init__(self, name="NodeBot"):

        self.name = name

        self.transports = {}
        self.mtimes = {}

        self.running = False
        self.thread = None

        print("?? MeshBridge router initialized")

    # =====================================================
    # START
    # =====================================================

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._loop,
            daemon=True
        )
        self.thread.start()

        print("?? Router started")

    # =====================================================
    # LOOP
    # =====================================================

    def _loop(self):

        while self.running:

            try:
                self._scan()
                time.sleep(SCAN_INTERVAL)

            except Exception as e:
                print("?? Router error:", repr(e))

    # =====================================================
    # SCAN
    # =====================================================

    def _scan(self):

        base = transports.__path__[0]
        seen = {}

        for _, name, _ in pkgutil.iter_modules(transports.__path__):

            if not name.endswith("_adapter"):
                continue

            path = os.path.join(base, name + ".py")

            if not os.path.exists(path):
                continue

            mtime = os.path.getmtime(path)
            seen[name] = mtime

            if name not in self.transports or self.mtimes.get(name) != mtime:
                self._load(name, mtime)

        for name in list(self.transports.keys()):
            if name not in seen:
                self._unload(name)

    # =====================================================
    # LOAD
    # =====================================================

    def _load(self, name, mtime):

        module_path = f"transports.{name}"

        try:
            if module_path in sys.modules:
                mod = importlib.reload(sys.modules[module_path])
            else:
                mod = importlib.import_module(module_path)

            if not hasattr(mod, "Transport"):
                print(f"?? {name} missing Transport")
                return

            if name in self.transports:
                try:
                    self.transports[name].stop()
                except:
                    pass

            t = mod.Transport(self.name)
            t.start()

            self.transports[name] = t
            self.mtimes[name] = mtime

            print(f"?? Loaded transport: {name}")

        except Exception as e:
            print(f"?? Load failed {name}: {repr(e)}")

    # =====================================================
    # UNLOAD
    # =====================================================

    def _unload(self, name):

        t = self.transports.pop(name, None)

        if t:
            try:
                t.stop()
            except:
                pass

        self.mtimes.pop(name, None)

        print(f"?? Unloaded transport: {name}")

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        self.running = False

        for t in self.transports.values():
            try:
                t.stop()
            except:
                pass

        self.transports.clear()