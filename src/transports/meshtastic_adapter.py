import configparser
import glob
import json
import os
import subprocess
import threading
import time


class MeshtasticAdapter:
    """
    Meshtastic transport adapter for NodeBot.

    Connects to a Meshtastic radio over serial (or TCP fallback), receives
    direct messages, and routes them through the NodeBot engine.

    GPS position is pushed from the shared [gps] config at startup and
    updated periodically. Environmental telemetry is broadcast from the
    [telemetry] config section at a configurable interval.
    """

    def __init__(self, storage_path, engine):

        self.storage_path = storage_path
        self.engine = engine

        self._iface = None
        self._thread = None
        self.running = False
        self._my_node_num = None
        self._subscribed = False
        self._disconnected = threading.Event()
        self._lora_configured = False  # only write LoRa config once per process lifetime

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "..", "config.ini")
        cfg = configparser.ConfigParser()
        cfg.read(_config_path)

        self._node_name = cfg.get("bot",         "name",     fallback="NodeBot").strip()
        self.port       = cfg.get("meshtastic",  "port",     fallback="/dev/meshtastic0").strip()
        self.baudrate   = int(cfg.get("meshtastic", "baudrate", fallback="115200").strip())

        # LoRa radio config — applied on connect if region is set
        self._lora_region  = cfg.get("meshtastic", "region",       fallback="").strip()
        self._lora_preset  = cfg.get("meshtastic", "modem_preset", fallback="LONG_FAST").strip()
        self._lora_hops    = int(cfg.get("meshtastic", "hop_limit",  fallback="3").strip())
        self._lora_power   = int(cfg.get("meshtastic", "tx_power",   fallback="0").strip())

        # GPS — shared [gps] section
        self._gps_mode      = cfg.get("gps", "gps_mode",      fallback="disabled").strip()
        self._gps_lat       = cfg.get("gps", "gps_lat",       fallback="").strip()
        self._gps_lon       = cfg.get("gps", "gps_lon",       fallback="").strip()
        self._gps_alt       = cfg.get("gps", "gps_alt",       fallback="0").strip()
        self._gps_device    = cfg.get("gps", "gps_device",    fallback="").strip()
        self._gps_precision = int(cfg.get("gps", "gps_precision", fallback="4").strip())
        self._last_gps_lat  = None
        self._last_gps_lon  = None
        self._last_gps_alt  = None

        # Telemetry — shared [telemetry] section
        self._tel_mode     = cfg.get("telemetry", "mode",             fallback="disabled").strip()
        self._tel_interval = int(cfg.get("telemetry", "interval_minutes", fallback="10").strip()) * 60
        self._tel_script   = cfg.get("telemetry", "script",           fallback="").strip()
        self._tel_weewx    = cfg.get("telemetry", "weewx_db",         fallback="/var/lib/weewx/weewx.sdb").strip()
        self._tel_static   = {
            "temperature": cfg.get("telemetry", "static_temp",     fallback="").strip(),
            "humidity":    cfg.get("telemetry", "static_humidity",  fallback="").strip(),
            "pressure":    cfg.get("telemetry", "static_pressure",  fallback="").strip(),
        }

        print(f"[meshtastic_adapter] port={self.port} region={self._lora_region or 'unset'} "
              f"telemetry={self._tel_mode} gps={self._gps_mode}")

    # =====================================================
    # WORKER MANAGEMENT
    # =====================================================

    def start_worker(self):

        if self._thread and self._thread.is_alive():
            print("[meshtastic_adapter] worker already running")
            return

        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[meshtastic_adapter] worker started")

    def _run(self):

        from pubsub import pub
        import meshtastic.serial_interface

        while self.running:
            try:
                self._iface = meshtastic.serial_interface.SerialInterface(devPath=self.port)
                time.sleep(2)  # Let the interface fully initialise

                info = getattr(self._iface, "myInfo", None)
                self._my_node_num = getattr(info, "my_node_num", None)
                print(f"[meshtastic_adapter] connected — node {self._my_node_num:08x}" if self._my_node_num else "[meshtastic_adapter] connected")

                # Subscriptions — guard against duplicate subscribe on reconnect
                if not self._subscribed:
                    pub.subscribe(self._on_receive,    "meshtastic.receive")
                    pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")
                    self._subscribed = True

                self._disconnected.clear()

                # Configure node identity and GPS
                self._configure_node()

                # Start background loops
                if self._tel_mode != "disabled":
                    threading.Thread(target=self._telemetry_loop, daemon=True).start()

                if self._gps_mode in ("gpsd", "serial", "future"):
                    threading.Thread(target=self._gps_loop, daemon=True).start()

                # Block here until the interface disconnects
                self._disconnected.wait()

                if not self.running:
                    break

                print("[meshtastic_adapter] disconnected — retrying in 10s")
                time.sleep(10)

            except Exception as e:
                print(f"[meshtastic_adapter] connection error: {e} — retrying in 10s")
                self._iface = None
                if self.running:
                    time.sleep(10)

    def _on_disconnect(self, interface, topic=None):
        print("[meshtastic_adapter] connection lost")
        self._iface = None
        self._disconnected.set()

    # =====================================================
    # NODE CONFIGURATION
    # =====================================================

    def _configure_node(self):

        # LoRa radio settings (skipped if region is blank or already applied)
        self._apply_lora_config()

        # Set node name and announce (skipped if name already matches saved state)
        self.announce()

        # Initial GPS push
        self._push_gps(force=True)

        # Initial telemetry send (after a brief delay so the radio is ready)
        if self._tel_mode != "disabled":
            threading.Timer(5.0, self._send_telemetry).start()

    def _apply_lora_config(self):
        if not self._lora_region or self._lora_configured:
            return
        if self._lora_state_matches():
            print("[meshtastic_adapter] LoRa config unchanged, skipping write")
            self._lora_configured = True
            return
        try:
            from meshtastic import config_pb2
            lora = self._iface.localNode.localConfig.lora
            lora.region       = config_pb2.Config.LoRaConfig.RegionCode.Value(self._lora_region)
            lora.modem_preset = config_pb2.Config.LoRaConfig.ModemPreset.Value(self._lora_preset)
            lora.hop_limit    = self._lora_hops
            lora.tx_power     = self._lora_power
            self._iface.localNode.writeConfig("lora")
            self._lora_configured = True
            self._save_lora_state()
            print(f"[meshtastic_adapter] LoRa config applied: region={self._lora_region} "
                  f"preset={self._lora_preset} hops={self._lora_hops} power={self._lora_power}")
        except (KeyError, ValueError) as e:
            print(f"[meshtastic_adapter] LoRa config: invalid value — {e}")
        except Exception as e:
            print(f"[meshtastic_adapter] LoRa config failed: {e}")

    def _lora_state_path(self):
        return os.path.join(self.storage_path, "meshtastic_lora.json")

    def _lora_state_matches(self):
        try:
            with open(self._lora_state_path()) as f:
                saved = json.load(f)
            return (saved.get("region")       == self._lora_region  and
                    saved.get("modem_preset") == self._lora_preset  and
                    saved.get("hop_limit")    == self._lora_hops    and
                    saved.get("tx_power")     == self._lora_power)
        except Exception:
            return False

    def _node_name_matches_saved(self):
        try:
            with open(self._lora_state_path()) as f:
                saved = json.load(f)
            return saved.get("node_name") == self._node_name
        except Exception:
            return False

    def _save_lora_state(self):
        try:
            os.makedirs(self.storage_path, exist_ok=True)
            existing = {}
            try:
                with open(self._lora_state_path()) as f:
                    existing = json.load(f)
            except Exception:
                pass
            existing.update({
                "region":       self._lora_region,
                "modem_preset": self._lora_preset,
                "hop_limit":    self._lora_hops,
                "tx_power":     self._lora_power,
                "node_name":    self._node_name,
            })
            with open(self._lora_state_path(), "w") as f:
                json.dump(existing, f)
        except Exception as e:
            print(f"[meshtastic_adapter] failed to save device state: {e}")

    # =====================================================
    # INBOUND MESSAGE
    # =====================================================

    def _on_receive(self, packet, interface):

        try:
            decoded = packet.get("decoded", {})
            if decoded.get("portnum") != "TEXT_MESSAGE_APP":
                return

            text = decoded.get("text", "").strip()
            if not text:
                return

            from_id = packet.get("fromId", "")  # "!abcd1234"
            to_id   = packet.get("toId",   "")

            # Ignore channel broadcasts
            if not to_id or to_id in ("^all", "!ffffffff"):
                return

            sender = f"mesh:{from_id.lstrip('!').lower()}"

            print(f"[meshtastic_adapter] msg from {sender}: {text!r}")

            if self.engine:
                self.engine.handle_message(
                    sender=sender,
                    message=text,
                    send_callback=self._send_reply
                )

        except Exception as e:
            print(f"[meshtastic_adapter] receive error: {e}")

    # =====================================================
    # OUTBOUND MESSAGE
    # =====================================================

    def _send_reply(self, sender, content, notify_cb=None):

        try:
            if not self._iface:
                if notify_cb:
                    notify_cb(False)
                return

            dest = f"!{sender[5:]}" if str(sender).startswith("mesh:") else str(sender)
            self._iface.sendText(content, destinationId=dest)
            print(f"[meshtastic_adapter] sent to {dest}")
            if notify_cb:
                notify_cb(True)

        except Exception as e:
            print(f"[meshtastic_adapter] send error: {e}")
            if notify_cb:
                notify_cb(False)

    def send_message(self, destination, content, notify_cb=None):
        self._send_reply(destination, content, notify_cb=notify_cb)

    # =====================================================
    # GPS
    # =====================================================

    def _push_gps(self, force=False):

        mode = self._gps_mode
        if mode in ("disabled", "future"):
            return

        lat = lon = alt = None

        if mode == "manual":
            try:
                lat = float(self._gps_lat)
                lon = float(self._gps_lon)
                alt = float(self._gps_alt or "0")
            except ValueError:
                print("[meshtastic_adapter] GPS: invalid manual coordinates, skipping")
                return

        elif mode == "gpsd":
            lat, lon, alt = self._read_gpsd()
            if lat is None:
                print("[meshtastic_adapter] GPS: no gpsd fix")
                return

        elif mode == "serial":
            lat, lon, alt = self._read_serial_gps(self._gps_device)
            if lat is None:
                print(f"[meshtastic_adapter] GPS: no fix from {self._gps_device}")
                return

        if lat is None:
            return

        prec = self._gps_precision
        lat_r = round(lat, prec)
        lon_r = round(lon, prec)
        alt_r = round(alt or 0, 1)

        changed = (lat_r, lon_r) != (self._last_gps_lat, self._last_gps_lon)
        if not changed and not force:
            return

        try:
            self._iface.localNode.setPosition(lat_r, lon_r, int(alt_r))
            self._last_gps_lat = lat_r
            self._last_gps_lon = lon_r
            self._last_gps_alt = int(alt_r)
            print(f"[meshtastic_adapter] GPS pushed: lat={lat_r} lon={lon_r} alt={alt_r}")
        except Exception as e:
            print(f"[meshtastic_adapter] GPS push failed: {e}")

    def _gps_loop(self):

        UPDATE_INTERVAL = 300
        CHECK_INTERVAL  = 30
        SCAN_INTERVAL   = 60

        last_forced = time.time()
        last_scan   = 0.0

        while self.running and not self._disconnected.is_set():
            time.sleep(CHECK_INTERVAL)
            if not self._iface or self._disconnected.is_set():
                break

            mode = self._gps_mode

            if mode == "future":
                now = time.time()
                if now - last_scan >= SCAN_INTERVAL:
                    last_scan = now
                    device, _baud = self._scan_for_gps()
                    if device:
                        print(f"[meshtastic_adapter] GPS auto-discovered: {device}")
                        self._gps_mode = "serial"
                        self._gps_device = device
                        self._push_gps(force=True)
                        last_forced = time.time()
                continue

            now = time.time()
            force = (now - last_forced) >= UPDATE_INTERVAL
            self._push_gps(force=force)
            if force:
                last_forced = now

    # =====================================================
    # TELEMETRY
    # =====================================================

    def _telemetry_loop(self):

        # Initial send is triggered by _configure_node via Timer.
        # This loop handles subsequent periodic sends.
        time.sleep(self._tel_interval)

        while self.running and not self._disconnected.is_set():
            if not self._iface or self._disconnected.is_set():
                break
            self._send_telemetry()
            time.sleep(self._tel_interval)

    def _send_telemetry(self):

        data = self._get_telemetry_data()
        if not data:
            return

        try:
            from meshtastic import telemetry_pb2, portnums_pb2
            t = telemetry_pb2.Telemetry()
            m = t.environment_metrics

            temp = data.get("temperature")
            hum  = data.get("humidity")
            pres = data.get("pressure")

            if temp is not None:
                m.temperature         = float(temp)
            if hum is not None:
                m.relative_humidity   = float(hum)
            if pres is not None:
                m.barometric_pressure = float(pres)

            self._iface.sendData(
                t.SerializeToString(),
                portNum=portnums_pb2.PortNum.TELEMETRY_APP,
                destinationId="^all",
                wantAck=False,
                wantResponse=False
            )
            print(f"[meshtastic_adapter] telemetry sent: temp={temp} hum={hum} pres={pres}")

        except ImportError:
            print("[meshtastic_adapter] telemetry_pb2 not available — library update may be needed")
        except Exception as e:
            print(f"[meshtastic_adapter] telemetry send failed: {e}")

    def _get_telemetry_data(self):

        mode = self._tel_mode

        if mode == "static":
            data = {}
            for key, raw in self._tel_static.items():
                if raw:
                    try:
                        data[key] = float(raw)
                    except ValueError:
                        pass
            return data or None

        elif mode == "script":
            return self._run_telemetry_script()

        elif mode == "weewx":
            return self._read_weewx()

        return None

    def _run_telemetry_script(self):
        import json
        try:
            result = subprocess.run(
                [self._tel_script],
                capture_output=True, text=True, timeout=30
            )
            return json.loads(result.stdout.strip())
        except Exception as e:
            print(f"[meshtastic_adapter] telemetry script error: {e}")
            return None

    def _read_weewx(self):
        import sqlite3
        try:
            conn = sqlite3.connect(self._tel_weewx)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT usUnits, outTemp, outHumidity, barometer "
                "FROM archive ORDER BY dateTime DESC LIMIT 1"
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return None

            data = {}
            us_units = row["usUnits"] == 1  # 1=US/Imperial, 16=metric

            if row["outTemp"] is not None:
                t = row["outTemp"]
                data["temperature"] = round((t - 32) * 5 / 9, 2) if us_units else round(t, 2)

            if row["outHumidity"] is not None:
                data["humidity"] = round(row["outHumidity"], 1)

            if row["barometer"] is not None:
                p = row["barometer"]
                data["pressure"] = round(p * 33.8639, 2) if us_units else round(p, 2)

            return data or None

        except Exception as e:
            print(f"[meshtastic_adapter] weewx read error: {e}")
            return None

    # =====================================================
    # GPS HELPERS
    # =====================================================

    def _scan_for_gps(self):
        from gps_reader import scan_for_gps
        return scan_for_gps(exclude_port=self.port)

    @staticmethod
    def _read_gpsd(timeout=30):
        from gps_reader import read_gpsd
        return read_gpsd(timeout=timeout)

    @staticmethod
    def _read_serial_gps(device, timeout=30):
        from gps_reader import read_serial_gps
        return read_serial_gps(device, timeout=timeout)

    # =====================================================
    # ANNOUNCE / STOP
    # =====================================================

    def announce(self):
        try:
            if not self._iface:
                return
            # setOwner writes device config and may reboot the radio — skip if
            # the name is already set to avoid a reboot on every NodeBot restart.
            if not self._node_name_matches_saved():
                self._iface.localNode.setOwner(long_name=self._node_name)
                self._save_lora_state()
                print(f"[meshtastic_adapter] node name set: {self._node_name}")
            if self._last_gps_lat is not None:
                self._iface.localNode.setPosition(
                    self._last_gps_lat, self._last_gps_lon, self._last_gps_alt or 0
                )
            print("[meshtastic_adapter] announced on network")
        except Exception as e:
            print(f"[meshtastic_adapter] announce failed: {e}")

    def stop(self):
        self.running = False
        self._disconnected.set()
        if self._iface:
            try:
                self._iface.close()
            except Exception:
                pass
        print("[meshtastic_adapter] stopped")
