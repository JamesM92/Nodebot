import asyncio
import collections
import configparser
import os
import re
import threading
import time

from .. import logger


CHAN_SOCK_PATH  = "/tmp/nodebot_chan.sock"
CHAN_BUFFER_MAX = 500

_AGC_TX_SECS         = 3 * 60   # send_advert() to reset SX126x AGC lockup (3 min baseline)
_LOG_DATA_STALE_SECS = 45       # proactive AGC reset if no LOG_DATA events for 45 s
_AGC_DEAD_COUNT      = 3        # firmware reboot after this many consecutive failed TX resets
_PING_SECS           = 3 * 60   # get_time() health probe when channel is quiet
_WATCHDOG_SECS       = 25 * 60  # reconnect if no RF events in this window
_PERIODIC_ANN_SECS   = 12 * 3600

# Delays between TABLE_FULL retries (seconds before each attempt).
#
# TABLE_FULL means createDatagram() returned NULL — Dispatcher pool exhausted.
# With rx_delay_base=0 and no-flood announce, the rx_queue stays empty, yet
# pool exhaustion still occurs with queue_len=0 (getFreeCount() is not exposed
# via serial, so the true occupant of all 16 slots is unknown).
#
# Strategy: 4 fast attempts to catch any transient gap, then give up and
# reconnect.  A firmware reboot clears all pool state; the message is
# re-queued and retried after the clean reconnect (see _send_worker).
_SEND_DELAYS = [0.1, 0.3, 0.8, 2.0]


class MeshCoreAdapter:
    """
    MeshCore transport adapter for NodeBot.

    Architecture:
    - Single async event loop on a dedicated thread.
    - Inbound DMs trigger engine.handle_message() in an executor (so the engine's
      sync work doesn't block the event loop), which calls _send_reply() sync.
    - _send_reply() is NON-BLOCKING: it puts the outbound job on an asyncio.Queue
      and returns immediately.  No future.result() — this eliminates the concurrent-
      coroutine bug where a 10 s timeout caused a second retry loop to spawn.
    - A single _send_worker() coroutine drains the queue one job at a time, so
      only one outbound attempt runs at a time.
    - Outbound uses plain send_msg() (one firmware attempt per retry), not
      send_msg_with_retry(), which did 2 rapid-fire internal attempts before
      returning.  Our own retry loop applies increasing delays between attempts.
    - send_msg() accepts a raw hex pubkey string — no contact-table lookup
      required, so replies work even for contacts not previously announced.
    """

    def __init__(self, storage_path, engine):

        self.storage_path = storage_path
        self.engine = engine

        self._mc    = None
        self._loop  = None
        self._thread = None
        self.running = False

        self._send_queue          = None   # asyncio.Queue, created inside event loop
        self._reconnect_requested = False  # set by _send_one to break inner loop
        self._pool_tainted        = False  # TABLE_FULL exhausted retries — firmware reboot needed
        self._ready               = None   # asyncio.Event; set when startup complete, cleared on reconnect
        self._last_rf_event  = time.time()
        self._last_log_data  = time.time()
        self._agc_reset_count = 0  # consecutive proactive resets with no RF recovery

        self._chan_buffer      = collections.deque(maxlen=CHAN_BUFFER_MAX)
        self._chan_clients     = set()
        self._recent_msgs      = {}
        self._recent_chan_msgs = {}
        self._seen_contacts    = {}

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "..", "config.ini")
        cfg = configparser.ConfigParser()
        cfg.read(_config_path)

        self._node_name = cfg.get("bot", "name", fallback="NodeBot").strip()

        self.port     = cfg.get("meshcore", "port",     fallback="/dev/meshcore0").strip()
        self.baudrate = int(cfg.get("meshcore", "baudrate", fallback="115200"))

        self._radio_freq   = float(cfg.get("meshcore", "radio_freq",   fallback="915.0"))
        self._radio_bw     = float(cfg.get("meshcore", "radio_bw",     fallback="250.0"))
        self._radio_sf     = int(cfg.get("meshcore",   "radio_sf",     fallback="9"))
        self._radio_cr     = int(cfg.get("meshcore",   "radio_cr",     fallback="5"))
        self._radio_repeat = int(cfg.get("meshcore",   "radio_repeat", fallback="0"))

        self._gps_mode      = cfg.get("gps", "gps_mode",      fallback="disabled").strip()
        self._gps_lat       = cfg.get("gps", "gps_lat",       fallback="").strip()
        self._gps_lon       = cfg.get("gps", "gps_lon",       fallback="").strip()
        self._gps_alt       = cfg.get("gps", "gps_alt",       fallback="0").strip()
        self._gps_device    = cfg.get("gps", "gps_device",    fallback="").strip()
        self._gps_precision = int(cfg.get("gps", "gps_precision", fallback="4").strip())

        self._last_gps_lat = None
        self._last_gps_lon = None

        print(f"[meshcore_adapter] port={self.port} baud={self.baudrate} "
              f"gps_mode={self._gps_mode} gps_precision={self._gps_precision}")

    # =====================================================
    # HEALTH
    # =====================================================

    @property
    def is_connected(self):
        return self.running and self._mc is not None

    # =====================================================
    # WORKER MANAGEMENT
    # =====================================================

    def start_worker(self):
        if self._thread and self._thread.is_alive():
            print("[meshcore_adapter] worker already running")
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        print("[meshcore_adapter] worker started")

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            print(f"[meshcore_adapter] event loop error: {e}")
        finally:
            self._loop.close()

    # =====================================================
    # ASYNC MAIN
    # =====================================================

    async def _async_main(self):

        from meshcore.meshcore import MeshCore
        from meshcore.serial_cx import SerialConnection
        from meshcore.events import EventType, Event

        self._send_queue = asyncio.Queue()
        self._ready      = asyncio.Event()

        server_task = asyncio.create_task(self._run_chan_server())
        send_task   = asyncio.create_task(self._send_worker())

        _retry      = 0
        _first_boot = True

        try:
            while self.running:
                self._ready.clear()   # block _send_worker until startup completes
                try:
                    cx = SerialConnection(self.port, self.baudrate)
                    self._mc = MeshCore(cx, auto_reconnect=True, max_reconnect_attempts=0)

                    self._mc.subscribe(EventType.SELF_INFO, self._on_self_info)

                    conn_result = await self._mc.connect()
                    if conn_result is None:
                        raise RuntimeError(
                            f"MeshCore handshake timed out on {self.port} — "
                            f"device did not respond to APP_START. "
                            f"Check that the correct device is in the correct USB port."
                        )
                    print(f"[meshcore_adapter] connected to {self.port}")
                    _retry = 0

                    # Reboot the firmware if:
                    #   a) first boot and firmware has been running a while
                    #      (stale pool slots from a previous Python session), OR
                    #   b) TABLE_FULL exhausted all retry attempts — _pool_tainted
                    #      is set so we know the 16-slot pool needs a hard reset.
                    # Python-side reconnect alone does NOT clear the pool; only a
                    # firmware reboot (ESP32 reset) returns all slots to unused[].
                    if _first_boot or self._pool_tainted:
                        _first_boot = False
                        self._pool_tainted = False
                        uptime = 999
                        try:
                            cs = await self._mc.commands.get_stats_core()
                            if cs and cs.payload:
                                uptime = cs.payload.get("uptime_secs", 999)
                        except Exception:
                            pass
                        if uptime > 30:
                            print(f"[meshcore_adapter] startup: firmware uptime={uptime}s — rebooting to clear stale pool state")
                            try:
                                await self._mc.commands.reboot()
                            except Exception:
                                pass
                            await self._mc.disconnect()
                            self._mc = None
                            await asyncio.sleep(8)
                            continue
                        else:
                            print(f"[meshcore_adapter] startup: firmware uptime={uptime}s — pool is fresh, no reboot needed")

                    await asyncio.sleep(0.2)

                    await self._mc.ensure_contacts()
                    print(f"[meshcore_adapter] contacts loaded: {len(self._mc.contacts)}")

                    await self._apply_radio_params()
                    await self._apply_tuning_params()
                    await self._query_channels()
                    await self._set_node_name()

                    sub_dm   = self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_message)
                    sub_chan = self._mc.subscribe(EventType.CHANNEL_MSG_RECV,  self._on_channel_message)
                    sub_rf   = self._mc.subscribe(EventType.RX_LOG_DATA,       self._on_rf_log)

                    await self._mc.start_auto_message_fetching()
                    print("[meshcore_adapter] listening for messages")

                    # Flush pending outbound replies BEFORE announcing.
                    # Announcing triggers PATH responses from all 83+ mesh
                    # neighbours which saturates the 16-slot packet pool.
                    # The pool is clean right now — send any queued replies
                    # while we have the window.
                    if self.engine:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, lambda: self.engine.flush_outbound("meshcore_adapter")
                        )
                    await asyncio.sleep(1)   # let _send_worker drain the queue

                    # GPS and announce go last — these trigger RF traffic
                    await self._set_gps_location()
                    await self._announce_async()

                    # Startup complete — unblock _send_worker
                    self._ready.set()
                    print("[meshcore_adapter] ready")

                    gps_task = None
                    if self._gps_mode in ("gpsd", "serial", "future"):
                        gps_task = asyncio.create_task(self._gps_update_loop())

                    _last_agc  = time.time()
                    _last_ping = time.time()
                    _last_ann  = time.time()
                    _POLL_SECS = 5.0
                    _last_poll = asyncio.get_event_loop().time()
                    self._last_rf_event   = time.time()
                    self._last_log_data   = time.time()
                    self._agc_reset_count = 0

                    while self.running:
                        await asyncio.sleep(1)
                        now      = time.time()
                        loop_now = asyncio.get_event_loop().time()

                        # Periodic MESSAGES_WAITING flush
                        if loop_now - _last_poll >= _POLL_SECS:
                            _last_poll = loop_now
                            if self._mc:
                                await self._mc.dispatcher.dispatch(
                                    Event(EventType.MESSAGES_WAITING, {})
                                )

                        # AGC keepalive — zero-hop TX resets SX126x analog frontend
                        if now - _last_agc >= _AGC_TX_SECS:
                            _last_agc = now
                            try:
                                await self._mc.commands.send_advert(flood=False)
                            except Exception:
                                pass

                        # Proactive AGC reset — if no LOG_DATA events for 90 s the
                        # SX126x analog frontend has likely latched onto interference.
                        # Companion Radio firmware uses startReceive() (not Calibrate)
                        # on TX complete, so TX resets sometimes fail. After
                        # _AGC_DEAD_COUNT consecutive failures, reboot the firmware
                        # for a guaranteed full SX126x power-on calibration.
                        if (now - self._last_log_data >= _LOG_DATA_STALE_SECS
                                and now - _last_agc >= 30):
                            _last_agc = now
                            self._agc_reset_count += 1
                            if self._agc_reset_count >= _AGC_DEAD_COUNT:
                                print(f"[meshcore_adapter] AGC unrecoverable after "
                                      f"{self._agc_reset_count} TX resets — rebooting firmware")
                                self._agc_reset_count    = 0
                                self._pool_tainted        = True
                                self._reconnect_requested = True
                            else:
                                try:
                                    await self._mc.commands.send_advert(flood=False)
                                    print(f"[meshcore_adapter] AGC reset: no rflog events "
                                          f"for 90+ s (attempt {self._agc_reset_count}/"
                                          f"{_AGC_DEAD_COUNT - 1})")
                                except Exception:
                                    pass

                        # Health probe — lightweight, doesn't compete with DM delivery
                        if now - _last_ping >= _PING_SECS:
                            _last_ping = now
                            try:
                                await self._mc.commands.get_time()
                                self._last_rf_event = now
                            except Exception:
                                pass

                        # Reconnect requested by _send_one (AGC lockup detected)
                        if self._reconnect_requested:
                            self._reconnect_requested = False
                            print("[meshcore_adapter] reconnecting: persistent TABLE_FULL (AGC lockup suspected)")
                            break

                        # Watchdog
                        if now - self._last_rf_event >= _WATCHDOG_SECS:
                            print("[meshcore_adapter] watchdog: no RF events, reconnecting")
                            break

                        # Periodic presence announce (no-flood — avoids rx_queue burst)
                        if now - _last_ann >= _PERIODIC_ANN_SECS:
                            _last_ann = now
                            try:
                                await self._mc.commands.send_advert(flood=False)
                                print("[meshcore_adapter] periodic announce (no-flood)")
                            except Exception:
                                pass

                    if gps_task:
                        gps_task.cancel()
                        try:
                            await gps_task
                        except asyncio.CancelledError:
                            pass

                    self._mc.unsubscribe(sub_dm)
                    self._mc.unsubscribe(sub_chan)
                    self._mc.unsubscribe(sub_rf)
                    await self._mc.disconnect()
                    self._mc = None
                    if not self.running:
                        break
                    # watchdog or clean exit from inner loop — reconnect

                except Exception as e:
                    delay = min(10 * (2 ** _retry), 300)
                    _retry += 1
                    print(f"[meshcore_adapter] connection error: {e} — retrying in {delay}s")
                    self._mc = None
                    if self.running:
                        await asyncio.sleep(delay)

        finally:
            send_task.cancel()
            server_task.cancel()
            for t in (send_task, server_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # =====================================================
    # RADIO PARAMS
    # =====================================================

    async def _apply_radio_params(self):
        try:
            from meshcore.events import EventType
            result = await self._mc.commands.set_radio(
                self._radio_freq, self._radio_bw,
                self._radio_sf,   self._radio_cr,
                repeat=self._radio_repeat,
            )
            if result and result.type == EventType.OK:
                print(f"[meshcore_adapter] radio set: freq={self._radio_freq} "
                      f"bw={self._radio_bw} sf={self._radio_sf} "
                      f"cr={self._radio_cr} repeat={self._radio_repeat}")
            else:
                err = (result.payload or {}).get("code_string", "?") if result else "no response"
                print(f"[meshcore_adapter] radio set failed: {err}")
        except Exception as e:
            print(f"[meshcore_adapter] radio set error: {e}")

    async def _apply_tuning_params(self):
        """
        Set rx_delay_base=0 so flood packets bypass the rx_queue delay entirely.

        Dispatcher::checkRecv() normally holds every flood-routed packet in
        rx_queue for calcRxDelay() ms (up to 32 s) for relay timing.  With
        relay disabled (radio_repeat=0) this wastes pool slots on waiting that
        never leads to a retransmit.  rx_delay_base=0 short-circuits the check
        so all flood packets call processRecvPacket() immediately, keeping the
        pool free for outbound DM replies.
        """
        from meshcore.events import EventType
        try:
            res = await self._mc.commands.get_tuning()
            if res and res.payload:
                rx_dly = res.payload.get("rx_delay", 0)
                af     = res.payload.get("airtime_factor", 0)
                print(f"[meshcore_adapter] tuning: rx_delay={rx_dly} airtime_factor={af}")
                if rx_dly != 0:
                    result = await self._mc.commands.set_tuning(0, af)
                    if result and result.type == EventType.OK:
                        print("[meshcore_adapter] tuning: rx_delay set to 0 (rx_queue bypass enabled)")
                    else:
                        err = (result.payload or {}).get("code_string", "?") if result else "no response"
                        print(f"[meshcore_adapter] tuning: set_tuning failed: {err}")
                else:
                    print("[meshcore_adapter] tuning: rx_delay already 0, no change needed")
        except Exception as e:
            print(f"[meshcore_adapter] tuning params error: {e}")

    # =====================================================
    # NODE NAME
    # =====================================================

    async def _set_node_name(self):
        if not self._node_name:
            return
        try:
            await self._mc.commands.set_name(self._node_name)
            print(f"[meshcore_adapter] node name set: {self._node_name}")
        except Exception as e:
            print(f"[meshcore_adapter] set_name error: {e}")

    # =====================================================
    # GPS LOCATION
    # =====================================================

    async def _set_gps_location(self):
        mode = self._gps_mode
        lat = lon = alt = None

        if mode == "manual":
            try:
                lat = float(self._gps_lat)
                lon = float(self._gps_lon)
                alt = float(self._gps_alt)
            except (ValueError, TypeError):
                return

        elif mode == "gpsd":
            lat, lon, alt = await asyncio.get_event_loop().run_in_executor(
                None, self._read_gpsd
            )
            if lat is None:
                print("[meshcore_adapter] GPS: could not get fix from gpsd")
                return

        elif mode == "serial":
            device = self._gps_device
            if not device:
                print("[meshcore_adapter] GPS: serial mode but no gps_device configured, skipping")
                return
            lat, lon, alt = await asyncio.get_event_loop().run_in_executor(
                None, self._read_serial_gps, device
            )
            if lat is None:
                print(f"[meshcore_adapter] GPS: could not get fix from {device}")
                return

        if lat is None:
            return

        await self._push_gps(lat, lon, alt or 0, force=True)

    async def _push_gps(self, lat, lon, alt, force=False):
        prec  = self._gps_precision
        lat_r = round(lat, prec)
        lon_r = round(lon, prec)

        changed = (lat_r, lon_r) != (self._last_gps_lat, self._last_gps_lon)
        if not changed and not force:
            return

        try:
            result = await self._mc.commands.set_coords(lat_r, lon_r)
            from meshcore.events import EventType
            if result and result.type == EventType.OK:
                info = self._mc.self_info or {}
                adv_loc = info.get("adv_loc_policy", "?")
                print(f"[meshcore_adapter] GPS: location sharing enabled (adv_loc_policy={adv_loc})")
            self._last_gps_lat = lat_r
            self._last_gps_lon = lon_r
            print(f"[meshcore_adapter] GPS coords set: lat={lat_r} lon={lon_r} alt={round(alt, 1)}")
            # No send_advert here — caller is responsible for announcing.
            # GPS coords are included automatically in the next advert.
        except Exception as e:
            print(f"[meshcore_adapter] GPS: set_coords failed: {e}")

    async def _gps_update_loop(self):
        UPDATE_INTERVAL = 300
        CHECK_INTERVAL  = 30
        SCAN_INTERVAL   = 60

        last_forced = time.time()
        last_scan   = 0.0

        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            if not self._mc:
                break

            loop = asyncio.get_event_loop()
            mode = self._gps_mode

            if mode == "future":
                now = time.time()
                if now - last_scan >= SCAN_INTERVAL:
                    last_scan = now
                    device, _baud = await loop.run_in_executor(None, self._scan_for_gps)
                    if device:
                        print(f"[meshcore_adapter] GPS auto-discovered: {device}")
                        self._gps_mode  = "serial"
                        self._gps_device = device
                        lat, lon, alt = await loop.run_in_executor(
                            None, self._read_serial_gps, device, 30
                        )
                        if lat is not None:
                            await self._push_gps(lat, lon, alt, force=True)
                            last_forced = time.time()
                continue

            if mode == "gpsd":
                lat, lon, alt = await loop.run_in_executor(None, self._read_gpsd, 15)
            elif mode == "serial":
                lat, lon, alt = await loop.run_in_executor(
                    None, self._read_serial_gps, self._gps_device, 15
                )
            else:
                break

            if lat is None:
                continue

            now   = time.time()
            force = (now - last_forced) >= UPDATE_INTERVAL
            await self._push_gps(lat, lon, alt, force=force)
            if force:
                last_forced = now

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
    # CHANNEL CONFIGURATION
    # =====================================================

    async def _query_channels(self):
        from meshcore.events import EventType as _ET
        found = 0
        for idx in range(8):
            try:
                result = await self._mc.commands.get_channel(idx)
                if result.type == _ET.ERROR:
                    break
                p    = result.payload
                name = p.get("channel_name", "")
                h    = p.get("channel_hash", "??")
                print(f"[meshcore_adapter] channel {idx}: name={name!r} hash={h}")
                found += 1
            except Exception as e:
                print(f"[meshcore_adapter] channel {idx} query error: {e}")
                break
        if found == 0:
            print("[meshcore_adapter] no channels configured on device")
        self._mc.set_decrypt_channel_logs(True)
        print(f"[meshcore_adapter] channel decrypt enabled ({found} channel(s) loaded)")

    # =====================================================
    # CHANNEL SOCKET SERVER
    # =====================================================

    async def _run_chan_server(self):
        try:
            os.unlink(CHAN_SOCK_PATH)
        except FileNotFoundError:
            pass

        try:
            server = await asyncio.start_unix_server(
                self._handle_chan_client, path=CHAN_SOCK_PATH
            )
            print(f"[meshcore_adapter] channel server listening on {CHAN_SOCK_PATH}")
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[meshcore_adapter] channel server error: {e}")
        finally:
            try:
                os.unlink(CHAN_SOCK_PATH)
            except FileNotFoundError:
                pass

    async def _handle_chan_client(self, reader, writer):
        self._chan_clients.add(writer)
        try:
            import json
            for entry in list(self._chan_buffer):
                writer.write((json.dumps(entry) + "\n").encode())
            writer.write((json.dumps({"type": "history_end"}) + "\n").encode())
            await writer.drain()

            while True:
                data = await reader.read(1024)
                if not data:
                    break
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception as e:
            print(f"[meshcore_adapter] channel client error: {e}")
        finally:
            self._chan_clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # =====================================================
    # ANNOUNCE LOGGING
    # =====================================================

    def _maybe_log_contact_announce(self, sender_id, contact, rssi=None, snr=None):
        if not sender_id:
            return
        nick    = contact.get("adv_name") if contact else None
        lat     = contact.get("lat")  if contact else None
        lon     = contact.get("lon")  if contact else None
        alt     = contact.get("alt")  if contact else None
        pos_key = (round(lat, 3) if lat else None, round(lon, 3) if lon else None)
        if self._seen_contacts.get(sender_id) == pos_key:
            return
        self._seen_contacts[sender_id] = pos_key
        logger.log_announce("meshcore", sender_id, nick=nick,
                            lat=lat, lon=lon, alt=alt, rssi=rssi, snr=snr)

    # =====================================================
    # INBOUND DM
    # =====================================================

    async def _on_contact_message(self, event):
        try:
            payload       = event.payload
            pubkey_prefix = payload.get("pubkey_prefix", "")
            text          = payload.get("text", "").strip()

            if not text or not pubkey_prefix:
                return

            self._last_rf_event = time.time()

            now_ts    = time.time()
            dedup_key = (pubkey_prefix, text)
            stale     = [k for k, t in self._recent_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_msgs[k]
            if dedup_key in self._recent_msgs:
                print(f"[meshcore_adapter] duplicate DM from {pubkey_prefix}, ignoring")
                return
            self._recent_msgs[dedup_key] = now_ts

            print(f"[meshcore_adapter] msg from {pubkey_prefix}: {text!r}")
            logger.log_dm("meshcore", pubkey_prefix, text)

            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix) if self._mc else None
            self._maybe_log_contact_announce(pubkey_prefix, contact)
            nick = contact.get("adv_name") if contact else None

            if self.engine:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.engine.handle_message(
                        sender=pubkey_prefix,
                        message=text,
                        send_callback=self._send_reply,
                        nick=nick,
                    )
                )

        except Exception as e:
            print(f"[meshcore_adapter] receive error: {e}")

    # =====================================================
    # INBOUND CHANNEL MESSAGE (push path)
    # =====================================================

    _SENDER_RE = re.compile(r'^([0-9A-Fa-f]{4,16}):\s*(.*)', re.DOTALL)

    async def _on_channel_message(self, event):
        try:
            payload    = event.payload
            chan_idx   = payload.get("channel_idx", 0)
            raw_text   = payload.get("text", "").strip()
            sender_ts  = payload.get("sender_timestamp", 0)

            if not raw_text:
                return

            self._last_rf_event = time.time()

            sender_id = None
            text      = raw_text
            m = self._SENDER_RE.match(raw_text)
            if m:
                sender_id = m.group(1).lower()
                text      = m.group(2).strip()

            dedup_key = (sender_ts, text[:32])
            now_ts    = time.time()
            stale     = [k for k, t in self._recent_chan_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_chan_msgs[k]
            if dedup_key in self._recent_chan_msgs:
                return
            self._recent_chan_msgs[dedup_key] = now_ts

            sender_name = sender_id
            contact     = None
            if sender_id and self._mc:
                contact = self._mc.get_contact_by_key_prefix(sender_id)
                if contact:
                    sender_name = contact.get("adv_name") or sender_id

            rssi = payload.get("RSSI")
            snr  = payload.get("SNR")
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr)
            self._dispatch_channel_entry(chan_idx, sender_name or sender_id or "unknown",
                                         text, rssi, snr)

        except Exception as e:
            print(f"[meshcore_adapter] channel message error: {e}")

    # =====================================================
    # INBOUND CHANNEL MESSAGE (RF-log path)
    # =====================================================

    async def _on_rf_log(self, event):
        try:
            p = event.payload

            self._last_rf_event   = time.time()
            self._last_log_data   = time.time()
            self._agc_reset_count = 0  # RF is flowing again

            if p.get("payload_typename") != "GRP_TXT":
                return

            chan_hash = p.get("chan_hash", "?")
            msg       = p.get("message")

            if msg is None:
                print(f"[meshcore_adapter] RF chan msg (undecrypted) — "
                      f"chan_hash={chan_hash}; check channel key on device")
                return

            raw_text = msg.strip() if isinstance(msg, str) else msg.decode("utf-8", "ignore").strip()
            if not raw_text:
                return

            sender_id = None
            text      = raw_text
            m = self._SENDER_RE.match(raw_text)
            if m:
                sender_id = m.group(1).lower()
                text      = m.group(2).strip()

            sender_ts = p.get("sender_timestamp", 0)
            dedup_key = (sender_ts, text[:32])
            now_ts    = time.time()
            stale     = [k for k, t in self._recent_chan_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_chan_msgs[k]
            if dedup_key in self._recent_chan_msgs:
                return
            self._recent_chan_msgs[dedup_key] = now_ts

            chan_idx    = 0
            sender_name = sender_id or chan_hash
            contact     = None
            if self._mc:
                try:
                    for idx, ch in enumerate(self._mc._reader.packet_parser.channels):
                        if ch.get("channel_hash") == chan_hash:
                            chan_idx = idx
                            break
                except (AttributeError, IndexError):
                    pass
                if sender_id:
                    contact = self._mc.get_contact_by_key_prefix(sender_id)
                    if contact:
                        sender_name = contact.get("adv_name") or sender_id

            rssi = p.get("rssi")
            snr  = p.get("snr")
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr)
            self._dispatch_channel_entry(chan_idx, sender_name, text, rssi, snr, via="rflog")

        except Exception as e:
            print(f"[meshcore_adapter] RF log handler error: {e}")

    def _dispatch_channel_entry(self, chan_idx, sender, text, rssi, snr, via="push"):
        import json

        now   = time.time()
        entry = {
            "ts":     now,
            "when":   time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "proto":  "meshcore",
            "chan":   chan_idx,
            "sender": sender,
            "text":   text,
        }
        if rssi is not None:
            entry["rssi"] = rssi
        if snr is not None:
            entry["snr"] = snr

        chan_name = f"[{chan_idx}]"
        if self._mc:
            try:
                chans = self._mc._reader.packet_parser.channels
                ch    = chans[chan_idx] if chan_idx < len(chans) else {}
                name  = ch.get("channel_name", "")
                h     = ch.get("channel_hash", "")
                chan_name = f"[{chan_idx}] <{name or h}>"
            except Exception:
                pass

        print(f"[meshcore_adapter] chan{chan_name} '{sender}': {text!r} (via {via})")
        logger.log_channel("meshcore", sender, text, chan=chan_idx)

        self._chan_buffer.append(entry)

        msg_bytes = (json.dumps(entry) + "\n").encode()
        dead = set()
        for writer in list(self._chan_clients):
            try:
                writer.write(msg_bytes)
            except Exception:
                dead.add(writer)
        self._chan_clients -= dead

    # =====================================================
    # REPLY CALLBACK (sync — called from engine thread)
    # =====================================================

    def _send_reply(self, pubkey_prefix, content, notify_cb=None):
        """
        Called synchronously from the engine thread.  Must NOT block.

        Puts the outbound job on the asyncio queue and returns immediately.
        The _send_worker coroutine drains the queue one job at a time,
        so concurrent sends are impossible.
        """
        if not self._loop or not self._send_queue:
            print("[meshcore_adapter] not ready, dropping reply")
            if notify_cb:
                notify_cb(False)
            return

        def _enqueue():
            self._send_queue.put_nowait((pubkey_prefix, content, notify_cb, True))

        self._loop.call_soon_threadsafe(_enqueue)

    # =====================================================
    # SEND WORKER (single coroutine — serialises all sends)
    # =====================================================

    async def _send_worker(self):
        """Drains the send queue one job at a time.  Never runs two sends concurrently."""
        while True:
            try:
                job = await self._send_queue.get()
            except asyncio.CancelledError:
                return

            pubkey      = job[0]
            content     = job[1]
            notify_cb   = job[2]
            # retry_on_reconnect: if True and we exhaust retries requesting a
            # reconnect, re-queue the message for one final attempt after the
            # firmware reboot clears all pool state.
            retry_on_reconnect = job[3] if len(job) > 3 else False

            # Wait for startup to complete before touching the serial connection.
            # Reconnects clear _ready; it's re-set only after the full startup
            # sequence finishes.  Without this, retries race with ensure_contacts()
            # and other startup commands on the same serial port.
            if self._ready:
                await self._ready.wait()

            try:
                success = await self._send_one(pubkey, content)
            except Exception as e:
                print(f"[meshcore_adapter] send worker error: {e}")
                success = False

            if not success and retry_on_reconnect and self._reconnect_requested:
                # _send_one exhausted retries and requested a firmware reboot.
                # Re-queue WITHOUT the retry flag so the post-reboot attempt is
                # the last — avoids infinite retry loops.
                #
                # Pre-clear _ready so the re-queued message blocks at _ready.wait()
                # rather than racing ahead while _reconnect_requested is still set
                # (reconnect may not have started for up to 1 second because
                # _async_main polls on a 1-second sleep interval).
                if self._ready:
                    self._ready.clear()
                print(f"[meshcore_adapter] re-queuing reply to {pubkey} for one retry after reconnect")
                self._send_queue.put_nowait((pubkey, content, notify_cb, False))
                self._send_queue.task_done()
                continue   # don't invoke notify_cb yet

            if notify_cb:
                try:
                    notify_cb(success)
                except Exception:
                    pass

            self._send_queue.task_done()

    # =====================================================
    # OUTBOUND (single send with retry)
    # =====================================================

    async def _send_one(self, pubkey, content):
        """
        Send one DM using plain send_msg() (one firmware attempt per outer retry).

        TABLE_FULL means createDatagram() returned NULL — 16-slot pool exhausted.
        With rx_delay_base=0 and no-flood announce the rx_queue stays empty, yet
        TABLE_FULL still occurs with queue_len=0; the true cause is unknown
        (getFreeCount is not exposed via serial).  After 4 fast attempts the
        caller (_send_worker) requests a firmware reboot and re-queues the message
        for one retry against the clean post-reboot pool.  A mid-retry send_advert
        (at attempt 3) also resets the SX126x AGC if that's the blocker.
        """
        from meshcore.events import EventType

        if not self._mc:
            print(f"[meshcore_adapter] not connected, dropping reply to {pubkey}")
            return False

        n = len(_SEND_DELAYS)
        table_full_count = 0

        # Snapshot pool state before the first attempt.  errors is a sticky
        # bitmask (ERR_EVENT_FULL=0x01 set on any past pool exhaustion, cleared
        # only at reboot).  If POOL_FULL is already set here, the pool was
        # exhausted by something before our DM reply attempt — that narrows the
        # root-cause hunt for TABLE_FULL-with-queue_len=0.
        try:
            cs0 = await self._mc.commands.get_stats_core()
            if cs0 and cs0.payload:
                ql0   = cs0.payload.get("queue_len", "?")
                errs0 = cs0.payload.get("errors", 0)
                bits0 = []
                if errs0 & 0x01: bits0.append("POOL_FULL")
                if errs0 & 0x02: bits0.append("CAD_TIMEOUT")
                if errs0 & 0x04: bits0.append("STARTRX_TIMEOUT")
                e0 = "|".join(bits0) if bits0 else "ok"
                print(f"[meshcore_adapter] pre-send stats: queue_len={ql0} fw_err={e0}")
        except Exception:
            pass

        for attempt, delay in enumerate(_SEND_DELAYS):
            await asyncio.sleep(delay)

            if not self._mc or self._reconnect_requested:
                print(f"[meshcore_adapter] disconnected/reconnecting mid-retry, dropping reply to {pubkey}")
                return False

            # After 2 TABLE_FULL in a row, fire an advert to force TX→RX cycle.
            # This breaks SX126x AGC lockup (Dispatcher.checkSend() CAD-busy
            # counter resets on every non-TX tick — a TX is the only escape).
            if table_full_count == 2:
                print(f"[meshcore_adapter] TABLE_FULL x2 — sending AGC reset advert")
                try:
                    await self._mc.commands.send_advert(flood=False)
                    await asyncio.sleep(0.5)   # allow TX→RX to complete
                except Exception:
                    pass

            try:
                result = await self._mc.commands.send_msg(pubkey, content)
            except Exception as e:
                print(f"[meshcore_adapter] send_msg exception: {e}")
                return False

            if result is None:
                print(f"[meshcore_adapter] send_msg returned None for {pubkey}")
                return False

            if result.type == EventType.MSG_SENT:
                print(f"[meshcore_adapter] sent to {pubkey} (attempt {attempt + 1}/{n})")
                return True

            err = (result.payload or {}).get("code_string", "?") if result.payload else "?"

            if err != "ERR_CODE_TABLE_FULL":
                print(f"[meshcore_adapter] send failed to {pubkey}: {err}")
                return False

            table_full_count += 1
            next_delay = _SEND_DELAYS[attempt + 1] if attempt + 1 < n else "—"
            # Log pool diagnostics: queue_len = send_queue only; errors flags
            # include ERR_EVENT_FULL(0x01) CAD_TIMEOUT(0x02) STARTRX_TIMEOUT(0x04).
            try:
                cs = await self._mc.commands.get_stats_core()
                if cs and cs.payload:
                    ql   = cs.payload.get("queue_len", "?")
                    errs = cs.payload.get("errors", 0)
                    bits = []
                    if errs & 0x01: bits.append("POOL_FULL")
                    if errs & 0x02: bits.append("CAD_TIMEOUT")
                    if errs & 0x04: bits.append("STARTRX_TIMEOUT")
                    err_str = "|".join(bits) if bits else "ok"
                else:
                    ql = err_str = "?"
            except Exception:
                ql = err_str = "err"
            print(f"[meshcore_adapter] TABLE_FULL attempt {attempt + 1}/{n} "
                  f"queue_len={ql} fw_err={err_str}, retry in {next_delay}s")

        print(f"[meshcore_adapter] send failed to {pubkey}: TABLE_FULL after {n} attempts — requesting firmware reboot")
        self._pool_tainted        = True   # firmware reboot needed to clear pool
        self._reconnect_requested = True
        return False

    # =====================================================
    # SELF INFO
    # =====================================================

    async def _on_self_info(self, event):
        self._save_node_info(getattr(event, "payload", None))

    def _save_node_info(self, info=None):
        try:
            if info is None:
                info = self._mc.self_info if self._mc else {}
            pubkey = info.get("public_key", "") if info else ""
            if not pubkey:
                print("[meshcore_adapter] public_key not available in self_info")
                return
            import json as _json
            path = os.path.join(self.storage_path, "meshcore_node.json")
            os.makedirs(self.storage_path, exist_ok=True)
            with open(path, "w") as f:
                _json.dump({"public_key": pubkey}, f)
            print(f"[meshcore_adapter] node info saved: mc:{pubkey[:8]}")
        except Exception as e:
            print(f"[meshcore_adapter] could not save node info: {e}")

    # =====================================================
    # ANNOUNCE
    # =====================================================

    async def _announce_async(self):
        try:
            await self._mc.commands.send_advert(flood=False)
            print("[meshcore_adapter] announced to direct neighbours (no-flood)")
        except Exception as e:
            print(f"[meshcore_adapter] announce failed: {e}")

    def announce(self):
        if not self._loop or not self._mc:
            print("[meshcore_adapter] not connected, cannot announce")
            return
        future = asyncio.run_coroutine_threadsafe(self._announce_async(), self._loop)
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"[meshcore_adapter] announce error: {e}")

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):
        self.running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        try:
            os.unlink(CHAN_SOCK_PATH)
        except FileNotFoundError:
            pass
        print("[meshcore_adapter] stopped")
