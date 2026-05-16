import configparser
import glob
import json
import os
import subprocess
import threading
import time

from .. import logger

_PRESET_ABBR = {
    "LONG_FAST":      "LF",
    "LONG_SLOW":      "LS",
    "LONG_MODERATE":  "LM",
    "LONG_MOD":       "LM",
    "MEDIUM_FAST":    "MF",
    "MEDIUM_SLOW":    "MS",
    "SHORT_FAST":     "SF",
    "SHORT_SLOW":     "SS",
    "SHORT_TURBO":    "ST",
    "VERY_LONG_SLOW": "VLS",
}


class MeshtasticAdapter:
    """
    Meshtastic transport adapter for NodeBot.

    Connects to a Meshtastic radio over serial (or TCP fallback), receives
    direct messages, and routes them through the NodeBot engine.

    GPS position is pushed from the shared [gps] config at startup and
    updated periodically. Environmental telemetry is broadcast from the
    [telemetry] config section at a configurable interval.

    CONFIG_SECTION can be overridden by subclasses to read from a different
    config section (e.g. "meshtastic1" for a second radio).
    """

    CONFIG_SECTION = "meshtastic"

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

        sec = self.CONFIG_SECTION

        self._node_name = cfg.get("bot", "name", fallback="NodeBot").strip()
        self.port       = cfg.get(sec, "port",     fallback="").strip()
        self.baudrate   = int(cfg.get(sec, "baudrate", fallback="115200").strip())

        # LoRa radio config — applied on connect if region is set
        self._lora_region  = cfg.get(sec, "region",       fallback="").strip()
        self._lora_preset  = cfg.get(sec, "modem_preset", fallback="LONG_FAST").strip()
        self._lora_hops    = int(cfg.get(sec, "hop_limit",  fallback="3").strip())
        self._lora_power   = int(cfg.get(sec, "tx_power",   fallback="0").strip())

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
        self._tel_static   = {
            "temperature": cfg.get("telemetry", "static_temp",     fallback="").strip(),
            "humidity":    cfg.get("telemetry", "static_humidity",  fallback="").strip(),
            "pressure":    cfg.get("telemetry", "static_pressure",  fallback="").strip(),
        }

        if not self.port:
            print(f"[meshtastic_adapter] [{sec}] port not configured — adapter disabled")
            return

        print(f"[meshtastic_adapter] [{sec}] port={self.port} region={self._lora_region or 'unset'} "
              f"telemetry={self._tel_mode} gps={self._gps_mode}")

    # =====================================================
    # WORKER MANAGEMENT
    # =====================================================

    def start_worker(self):

        if not self.port:
            return

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

        _retry = 0

        while self.running:
            _iface_attempt = None
            try:
                _iface_attempt = meshtastic.serial_interface.SerialInterface(devPath=self.port)
                self._iface = _iface_attempt
                time.sleep(2)  # Let the interface fully initialise

                info = getattr(self._iface, "myInfo", None)
                self._my_node_num = getattr(info, "my_node_num", None)
                print(f"[meshtastic_adapter] connected — node {self._my_node_num:08x}" if self._my_node_num else "[meshtastic_adapter] connected")
                _retry = 0  # reset backoff on successful connect

                # Subscriptions — guard against duplicate subscribe on reconnect
                if not self._subscribed:
                    pub.subscribe(self._on_receive,    "meshtastic.receive")
                    pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")
                    self._subscribed = True

                self._disconnected.clear()

                if self.engine:
                    self.engine.flush_outbound("meshtastic_adapter")

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

                # _on_disconnect clears self._iface but the object's reader thread
                # still holds the serial port open. Close all SerialInterface
                # instances via the GC graph before the next connect attempt.
                try:
                    import gc
                    import meshtastic.serial_interface as _msi
                    for obj in gc.get_objects():
                        if isinstance(obj, _msi.SerialInterface):
                            try:
                                obj.close()
                            except Exception:
                                pass
                    gc.collect()
                except Exception:
                    pass
                self._subscribed = False

                delay = min(10 * (2 ** _retry), 300)
                _retry += 1
                print(f"[meshtastic_adapter] disconnected — retrying in {delay}s")
                time.sleep(delay)

            except Exception as e:
                delay = min(10 * (2 ** _retry), 300)
                _retry += 1
                print(f"[meshtastic_adapter] connection error: {e} — retrying in {delay}s")
                for iface in (_iface_attempt, self._iface):
                    if iface is not None:
                        try:
                            iface.close()
                        except Exception:
                            pass
                _iface_attempt = None
                self._iface = None
                # SerialInterface opens the port with exclusive=True before starting
                # a reader thread that holds a reference to self. If __init__ throws
                # (e.g. connection timeout), the assignment never completes so we have
                # no handle. Walk the GC object graph to find and close any orphaned
                # SerialInterface that still holds the port open.
                try:
                    import gc
                    import meshtastic.serial_interface as _msi
                    for obj in gc.get_objects():
                        if isinstance(obj, _msi.SerialInterface):
                            try:
                                obj.close()
                            except Exception:
                                pass
                    gc.collect()
                except Exception:
                    pass
                self._subscribed = False
                if self.running:
                    time.sleep(delay)

    def _on_disconnect(self, interface, topic=None):
        if interface is not self._iface:
            return
        print("[meshtastic_adapter] connection lost")
        try:
            if interface is not None:
                interface.close()
        except Exception:
            pass
        self._iface = None
        self._disconnected.set()

    # =====================================================
    # NODE CONFIGURATION
    # =====================================================

    def _configure_node(self):

        # LoRa radio settings (skipped if region is blank or already applied)
        self._apply_lora_config()

        # Set node name and announce (skipped if name already matches saved state).
        # Must run before the unconditional _save_lora_state below, otherwise
        # _save_lora_state writes node_name to the file first and announce()
        # sees it as already applied, causing setOwner to be permanently skipped.
        self.announce()

        # Always persist node num — _apply_lora_config skips the save when
        # settings are unchanged, so my_node_num would never be written.
        self._save_lora_state()

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
        return os.path.join(self.storage_path, f"{self.CONFIG_SECTION}_lora.json")

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
            update = {
                "region":       self._lora_region,
                "modem_preset": self._lora_preset,
                "hop_limit":    self._lora_hops,
                "tx_power":     self._lora_power,
                "node_name":    self._node_name,
            }
            if self._my_node_num is not None:
                update["my_node_num"] = self._my_node_num
            existing.update(update)
            with open(self._lora_state_path(), "w") as f:
                json.dump(existing, f)
        except Exception as e:
            print(f"[meshtastic_adapter] failed to save device state: {e}")

    # =====================================================
    # INBOUND MESSAGE
    # =====================================================

    def _on_receive(self, packet, interface):

        if interface is not self._iface:
            return

        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum", "")
            from_id = packet.get("fromId", "")

            if portnum == "POSITION_APP":
                self._log_position_announce(packet, decoded, from_id)
                return

            if portnum == "NODEINFO_APP":
                self._log_nodeinfo_announce(packet, decoded, from_id)
                return

            if portnum != "TEXT_MESSAGE_APP":
                return

            text = decoded.get("text", "").strip()
            if not text:
                return

            to_id   = packet.get("toId",   "")

            # Log and ignore channel broadcasts
            if not to_id or to_id in ("^all", "!ffffffff"):
                addr = from_id.lstrip("!").lower()
                node_info = (self._iface.nodes or {}).get(from_id, {}) if self._iface else {}
                user = node_info.get("user", {})
                long_name  = user.get("longName")  or None
                short_name = user.get("shortName") or None
                hops = packet.get("hopLimit")
                preset_abbr = _PRESET_ABBR.get(self._lora_preset.upper(), self._lora_preset)
                proto_tag = f"meshtastic:{preset_abbr}"
                logger.log_channel(proto_tag, addr, text,
                                   long_name=long_name, short_name=short_name, hops=hops)
                return

            sender = f"mesh:{from_id.lstrip('!').lower()}"

            nick = None
            if self._iface:
                node_info = (self._iface.nodes or {}).get(from_id, {})
                nick = node_info.get("user", {}).get("longName") or None

            print(f"[meshtastic_adapter] msg from {sender}: {text!r}")
            logger.log_dm("meshtastic", sender, text)

            if self.engine:
                self.engine.handle_message(
                    sender=sender,
                    message=text,
                    send_callback=self._send_reply,
                    nick=nick,
                )

        except Exception as e:
            print(f"[meshtastic_adapter] receive error: {e}")

    def _log_position_announce(self, packet, decoded, from_id):
        try:
            pos = decoded.get("position", {})
            if not pos:
                return
            lat_i = pos.get("latitudeI") or pos.get("latitude_i", 0)
            lon_i = pos.get("longitudeI") or pos.get("longitude_i", 0)
            if not lat_i or not lon_i:
                return
            lat = lat_i / 1e7
            lon = lon_i / 1e7
            alt     = pos.get("altitude")
            battery = pos.get("batteryLevel") or pos.get("battery_level")
            rssi    = packet.get("rxRssi")
            snr     = packet.get("rxSnr")
            hops    = packet.get("hopLimit")
            addr    = from_id.lstrip("!").lower() if from_id else "unknown"
            logger.log_announce(
                "meshtastic", addr,
                lat=lat, lon=lon, alt=alt,
                rssi=rssi, snr=snr, hops=hops, battery=battery,
                modem_preset=self._lora_preset,
            )
        except Exception as e:
            print(f"[meshtastic_adapter] position announce log error: {e}")

    def _log_nodeinfo_announce(self, packet, decoded, from_id):
        try:
            user = decoded.get("user", {})
            if not user:
                return
            long_name  = user.get("longName")  or user.get("long_name",  "")
            short_name = user.get("shortName") or user.get("short_name", "")
            nick  = long_name or short_name or None
            rssi  = packet.get("rxRssi")
            snr   = packet.get("rxSnr")
            hops  = packet.get("hopLimit")
            node_id = user.get("id", from_id or "").lstrip("!").lower()
            addr = from_id.lstrip("!").lower() if from_id else node_id or "unknown"
            logger.log_announce(
                "meshtastic", addr,
                nick=nick, rssi=rssi, snr=snr, hops=hops,
                modem_preset=self._lora_preset,
            )
        except Exception as e:
            print(f"[meshtastic_adapter] nodeinfo announce log error: {e}")

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
            self._iface.localNode.setFixedPosition(lat_r, lon_r, int(alt_r))
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

    # =====================================================
    # GPS HELPERS
    # =====================================================

    def _scan_for_gps(self):
        from ..gps_reader import scan_for_gps
        return scan_for_gps(exclude_port=self.port)

    @staticmethod
    def _read_gpsd(timeout=30):
        from ..gps_reader import read_gpsd
        return read_gpsd(timeout=timeout)

    @staticmethod
    def _read_serial_gps(device, timeout=30):
        from ..gps_reader import read_serial_gps
        return read_serial_gps(device, timeout=timeout)

    # =====================================================
    # HEALTH
    # =====================================================

    @property
    def is_connected(self):
        return self.running and self._iface is not None

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
                self._iface.localNode.setFixedPosition(
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
