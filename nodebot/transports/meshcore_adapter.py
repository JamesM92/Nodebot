# NOTE: This adapter relies on a custom MeshCore Companion Radio firmware build.
# Standard upstream firmware does NOT call Calibrate(0x7F) after TX on Companion Radio,
# which causes the SX126x AGC to latch onto interference and go deaf.
# Required firmware: github.com/JamesM92/MeshCore  branch: fix/agc-reset-blocked-by-sticky-irq
# Key changes vs upstream:
#   - getAGCResetInterval() = 30000  (enables 30s AGC reset timer, absent in stock companion)
#   - resetAGC() _agc_block_count bypass (forces Calibrate(0x7F) after 3 blocked attempts)
#   - doResetAGC() calls sx126xResetAGC() — warm sleep + Calibrate(0x7F) + re-calibrate image

import asyncio
import collections
import configparser
import os
import re
import threading
import time

from .. import logger
from .. import contacts
from .. import messages
from .. import paths
from .. import path_discovery


CHAN_SOCK_PATH  = "/tmp/nodebot_chan.sock"
CHAN_BUFFER_MAX = 500

_LOG_DATA_STALE_SECS = 90        # fallback AGC reset if no LOG_DATA events for 90s
                                 # firmware handles lockup in ≤90s via _agc_block_count;
                                 # this is a belt-and-suspenders Python-side safety net
_AGC_DEAD_COUNT      = 3        # firmware reboot after this many consecutive failed resets
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
        self._cfg_channels_raw = cfg.get("meshcore", "channels", fallback="").strip()

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

        server_task    = asyncio.create_task(self._run_chan_server())
        send_task      = asyncio.create_task(self._send_worker())
        discovery_task = asyncio.create_task(self._path_discovery_task())

        path_discovery.init(self.storage_path)

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
                    self._log_contact_announces()

                    await self._apply_radio_params()
                    await self._apply_tuning_params()
                    await self._query_channels()
                    await self._set_node_name()

                    sub_dm   = self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_message)
                    sub_chan = self._mc.subscribe(EventType.CHANNEL_MSG_RECV,  self._on_channel_message)
                    sub_rf   = self._mc.subscribe(EventType.RX_LOG_DATA,       self._on_rf_log)
                    sub_adv  = self._mc.subscribe(EventType.NEW_CONTACT,       self._on_new_contact)
                    sub_adv2 = self._mc.subscribe(EventType.ADVERTISEMENT,     self._on_advertisement)

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

                    _last_ping         = time.time()
                    _last_ann          = time.time()
                    _last_stale_reset  = time.time()
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

                        # Fallback AGC reset — firmware handles lockup via _agc_block_count
                        # (forces Calibrate(0x7F) after 3 blocked attempts / ~90s).
                        # This Python-side check catches any remaining edge cases and
                        # reboots the firmware if repeated resets still don't recover.
                        if (now - self._last_log_data >= _LOG_DATA_STALE_SECS
                                and now - _last_stale_reset >= 30):
                            _last_stale_reset = now
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
                    self._mc.unsubscribe(sub_adv)
                    self._mc.unsubscribe(sub_adv2)
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
            discovery_task.cancel()
            for t in (send_task, server_task, discovery_task):
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
        from hashlib import sha256 as _sha256
        from meshcore.events import EventType as _ET

        # Channels to configure from config.ini (comma-separated, e.g. "#test,#chat")
        _cfg_channels = [
            c.strip()
            for c in self._cfg_channels_raw.split(",")
            if c.strip()
        ]

        # ── Phase 1: read what the device currently has ──────────────────
        _EMPTY_HASH = "37"   # sha256(b'\x00'*16)[:1].hex() — firmware default
        slots       = {}     # idx → {"name": str, "hash": str}
        found       = 0
        for idx in range(8):
            try:
                result = await self._mc.commands.get_channel(idx)
                if result.type == _ET.ERROR:
                    break
                p    = result.payload
                name = p.get("channel_name", "")
                h    = p.get("channel_hash", "??")
                slots[idx] = {"name": name, "hash": h}
                print(f"[meshcore_adapter] channel {idx}: name={name!r} hash={h}")
                found += 1
            except Exception as e:
                print(f"[meshcore_adapter] channel {idx} query error: {e}")
                break

        # ── Phase 2: configure any channels missing from the device ───────
        existing_names = {s["name"] for s in slots.values()}
        missing        = [ch for ch in _cfg_channels if ch not in existing_names]

        if missing:
            # Find empty slots (hash == _EMPTY_HASH or slot not yet used)
            empty_slots = [
                idx for idx, s in slots.items()
                if s["hash"] == _EMPTY_HASH and s["name"] == ""
            ]
            for ch_name in missing:
                if not empty_slots:
                    print(f"[meshcore_adapter] no empty slot for channel {ch_name!r} — device full")
                    break
                slot = empty_slots.pop(0)
                try:
                    result = await self._mc.commands.set_channel(slot, ch_name)
                    from meshcore.events import EventType as _ET2
                    if result and result.type == _ET2.OK:
                        # Re-read the slot so reader.py calls newChannel() and
                        # populates the parser's decryption cache for this key.
                        await self._mc.commands.get_channel(slot)
                        secret = _sha256(ch_name.encode()).digest()[:16]
                        h      = _sha256(secret).digest()[:1].hex()
                        print(f"[meshcore_adapter] channel {slot}: set {ch_name!r} hash={h}")
                        found += 1
                    else:
                        print(f"[meshcore_adapter] channel {slot}: set_channel failed for {ch_name!r}")
                except Exception as e:
                    print(f"[meshcore_adapter] channel {slot}: set_channel error: {e}")

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

    def _log_contact_announces(self):
        """Import device contact list into the announce DB and position estimates.

        Inserts GPS contacts not yet in the DB (or with changed GPS), backfills
        nicks on existing rows, and seeds position_estimates for any announce
        rows not yet in the estimates table.
        """
        if not self._mc:
            return
        addr_nick_map = {
            pk[:12]: c["adv_name"]
            for pk, c in self._mc.contacts.items()
            if c.get("adv_name")
        }
        count = logger.backfill_nicks("meshcore", addr_nick_map)
        print(f"[meshcore_adapter] backfilled nicks for {count} contacts in announce DB")

        imported = 0
        for pk, c in self._mc.contacts.items():
            addr = pk[:12]
            nick = c.get("adv_name", "").replace("\0", "").strip() or None
            lat  = c.get("adv_lat") or None
            lon  = c.get("adv_lon") or None
            if lat == 0.0: lat = None
            if lon == 0.0: lon = None
            contacts.upsert("mc", addr, pubkey=pk, name=nick, lat=lat, lon=lon)
            # no event_type — startup bulk load, not a live RF event
            if lat is not None and lon is not None:
                logger.log_announce("meshcore", addr, nick=nick, lat=lat, lon=lon)
                imported += 1

        print(f"[meshcore_adapter] imported GPS for {imported} device contacts")
        seeded = logger.backfill_positions("meshcore")
        if seeded:
            print(f"[meshcore_adapter] seeded position estimates for {seeded} nodes")

    async def _on_new_contact(self, event):
        """Log an announce when an RF advertisement is received (flood or zero-hop).

        PUSH_CODE_NEW_ADVERT packets dispatch NEW_CONTACT — the payload contains
        adv_name/adv_lat/adv_lon directly so no contacts dict lookup is needed.
        """
        try:
            c = event.payload
            pk = c.get("public_key", "")
            if not pk:
                return
            nick = c.get("adv_name", "").replace("\0", "").strip() or None
            if not nick:
                return
            addr = pk[:12]
            lat = c.get("adv_lat") or None
            lon = c.get("adv_lon") or None
            if lat == 0.0:
                lat = None
            if lon == 0.0:
                lon = None
            logger.log_announce("meshcore", addr, nick=nick, lat=lat, lon=lon)
            contacts.upsert("mc", addr, pubkey=pk, name=nick, lat=lat, lon=lon,
                            event_type="advert")
        except Exception as e:
            print(f"[meshcore_adapter] new_contact announce error: {e}")

    async def _on_advertisement(self, event):
        """Log an announce when an RF advertisement is received (via ADVERTISEMENT push)."""
        try:
            pk = event.payload.get("public_key", "")
            if not pk or not self._mc:
                return
            contact = self._mc.contacts.get(pk) or self._mc._pending_contacts.get(pk)
            if not contact:
                return
            nick = contact.get("adv_name", "").replace("\0", "").strip() or None
            if not nick:
                return
            addr = pk[:12]
            lat = contact.get("adv_lat") or None
            lon = contact.get("adv_lon") or None
            if lat == 0.0:
                lat = None
            if lon == 0.0:
                lon = None
            logger.log_announce("meshcore", addr, nick=nick, lat=lat, lon=lon)
            contacts.upsert("mc", addr, pubkey=pk, name=nick, lat=lat, lon=lon,
                            event_type="advert")
        except Exception as e:
            print(f"[meshcore_adapter] advertisement announce error: {e}")

    def _maybe_log_contact_announce(self, sender_id, contact, rssi=None, snr=None):
        # Only log when we have a name — raw sender_id hex prefixes from channel
        # message payloads are not meaningful identifiers in the announce feed.
        if not sender_id or not contact:
            return
        nick = contact.get("adv_name") or None
        if not nick:
            return
        # Normalise to pk[:12] so this always writes to the same DB addr as
        # _log_contact_announces and _on_advertisement (prevents duplicate entries
        # and addr-format churn that pushes startup entries out of the 50-slot cap).
        pk   = contact.get("public_key") or ""
        addr = pk[:12] if len(pk) >= 12 else sender_id
        lat     = contact.get("lat")
        lon     = contact.get("lon")
        alt     = contact.get("alt")
        pos_key = (round(lat, 3) if lat else None, round(lon, 3) if lon else None)
        if self._seen_contacts.get(addr) == pos_key:
            return
        self._seen_contacts[addr] = pos_key
        logger.log_announce("meshcore", addr, nick=nick,
                            lat=lat, lon=lon, alt=alt, rssi=rssi, snr=snr)
        contacts.upsert("mc", addr, pubkey=pk or None, name=nick,
                        lat=lat, lon=lon, alt=alt, rssi=rssi, snr=snr,
                        event_type="advert")

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
            _our_id = None
            try:
                _our_id = (self._mc.self_info or {}).get("pubkey_pre") if self._mc else None
            except Exception:
                pass
            paths.log(text, sender_id=pubkey_prefix, our_id=_our_id)
            path_discovery.mark_responded(pubkey_prefix)
            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix) if self._mc else None
            nick_dm = contact.get("adv_name") if contact else None
            messages.log_dm("meshcore", pubkey_prefix, text, nick=nick_dm)
            self._maybe_log_contact_announce(pubkey_prefix, contact)
            nick = contact.get("adv_name") if contact else None
            pk = contact.get("public_key", "") if contact else ""
            contacts.upsert("mc", pubkey_prefix, pubkey=pk or None, name=nick,
                            event_type="dm")

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

            rssi     = payload.get("RSSI")
            snr      = payload.get("SNR")
            path_len = payload.get("path_len")
            hops     = path_len if (path_len is not None and path_len != 255) else None
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr)
            self._dispatch_channel_entry(chan_idx, sender_name or sender_id or "",
                                         text, rssi, snr, hops=hops,
                                         sender_id=sender_id)

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

            rssi     = p.get("rssi")
            snr      = p.get("snr")
            path_len = p.get("path_len")
            hops     = path_len if (path_len is not None and path_len != 255) else None
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr)
            self._dispatch_channel_entry(chan_idx, sender_name or sender_id or "", text, rssi, snr,
                                         hops=hops, via="rflog", sender_id=sender_id)

        except Exception as e:
            print(f"[meshcore_adapter] RF log handler error: {e}")

    def _dispatch_channel_entry(self, chan_idx, sender, text, rssi, snr, hops=None, via="push",
                                sender_id=None):
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

        chan_label = str(chan_idx)
        if self._mc:
            try:
                chans = self._mc._reader.packet_parser.channels
                ch    = chans[chan_idx] if chan_idx < len(chans) else {}
                name  = ch.get("channel_name", "")
                h     = ch.get("channel_hash", "")
                chan_label = name or h or str(chan_idx)
            except Exception:
                pass

        print(f"[meshcore_adapter] [{chan_label}] '{sender}': {text!r} (via {via})")
        tag = f"meshcore/{chan_label}"
        logger.log_channel(tag, sender, text, hops=hops)
        messages.log(tag, sender, text, hops=hops, rssi=rssi, snr=snr)
        our_id = None
        try:
            our_id = (self._mc.self_info or {}).get("pubkey_pre") if self._mc else None
        except Exception:
            pass
        paths.log(text, sender_id=sender_id, our_id=our_id)

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

            # Wait for the firmware TX queue to drain before queuing the next DM.
            # MSG_SENT means the firmware accepted the message, not that the LoRa
            # packet was transmitted.  At LongFast (SF11/BW500) a 160-byte payload
            # takes ~1.5s on-air, so a fixed 1.5s sleep races the transmitter.
            # Poll queue_len instead: stop as soon as the queue is empty, then add
            # a short guard.  Falls back to a fixed 3s cap if stats aren't available.
            if not self._send_queue.empty():
                for _ in range(20):   # up to 20 × 200ms = 4s
                    await asyncio.sleep(0.2)
                    try:
                        cs = await self._mc.commands.get_stats_core()
                        if cs and cs.payload and cs.payload.get("queue_len", 1) == 0:
                            break
                    except Exception:
                        break
                await asyncio.sleep(0.3)  # brief guard after queue clears

    # =====================================================
    # OUTBOUND (single send with retry)
    # =====================================================

    async def _send_one(self, pubkey, content):
        """
        Send one DM with ACK confirmation and path-failure fallback.

        After MSG_SENT we wait up to suggested_timeout×1.5 (min 5 s) for the
        ACK.  If no ACK arrives the contact's out_path may be stale; we call
        reset_path() once to switch the contact to flood mode, then retry.
        Flood DMs are broadcast and propagated by relay nodes so they can reach
        multi-hop contacts even without an established out_path.

        TABLE_FULL (16-slot datagram pool exhausted) is handled separately: two
        consecutive TABLE_FULLs trigger an AGC-reset advert; four TABLE_FULLs
        exhaust all retries and request a firmware reboot.
        """
        from meshcore.events import EventType

        if not self._mc:
            print(f"[meshcore_adapter] not connected, dropping reply to {pubkey}")
            return False

        n = len(_SEND_DELAYS)
        table_full_count = 0
        path_reset_done  = False  # reset_path is a one-shot per send attempt

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
                expected_ack = (result.payload or {}).get("expected_ack")
                if expected_ack is None:
                    # Older firmware without ACK field — treat MSG_SENT as success.
                    print(f"[meshcore_adapter] sent to {pubkey} (attempt {attempt + 1}/{n})")
                    return True

                suggested_ms = (result.payload or {}).get("suggested_timeout", 5000)
                ack_timeout  = max(5.0, suggested_ms / 1000.0 * 1.5)

                ack = await self._mc.commands.dispatcher.wait_for_event(
                    EventType.ACK,
                    attribute_filters={"code": expected_ack.hex()},
                    timeout=ack_timeout,
                )
                if ack is not None:
                    print(f"[meshcore_adapter] ACK from {pubkey} (attempt {attempt + 1}/{n})")
                    return True

                # No ACK — out_path may be stale or the contact is unreachable
                # on the current path.  Reset to flood once, then retry.
                print(f"[meshcore_adapter] no ACK from {pubkey} after {ack_timeout:.0f}s")
                if not path_reset_done:
                    path_reset_done = True
                    try:
                        contact = self._mc.get_contact_by_key_prefix(pubkey)
                        if not contact:
                            for pk in (self._mc.contacts or {}):
                                if pk.lower().startswith(pubkey.lower()):
                                    contact = self._mc.contacts[pk]
                                    break
                        if contact:
                            print(f"[meshcore_adapter] resetting path to flood for {pubkey}")
                            await self._mc.commands.reset_path(contact)
                            await asyncio.sleep(0.2)
                        else:
                            print(f"[meshcore_adapter] reset_path: contact {pubkey} not found")
                    except Exception as e:
                        print(f"[meshcore_adapter] reset_path error: {e}")
                continue

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

        if table_full_count >= n:
            print(f"[meshcore_adapter] send failed to {pubkey}: TABLE_FULL after {n} attempts — requesting firmware reboot")
            self._pool_tainted        = True   # firmware reboot needed to clear pool
            self._reconnect_requested = True
        else:
            print(f"[meshcore_adapter] no ACK from {pubkey} after path reset — giving up")
        return False

    # =====================================================
    # PATH DISCOVERY
    # =====================================================

    async def _path_discovery_task(self):
        """Hourly background task: send one path-discovery request to the oldest
        GPS node in the announce DB that has no entry in paths.db as a sender.

        Uses send_path_discovery_sync (CMD 0x34) — no DM to the remote node.
        The firmware floods the request; the target node replies with its
        outbound/inbound path data which we log to paths.db for the path map.
        """
        await asyncio.sleep(300)   # 5-min startup delay
        while self.running:
            try:
                await self._ready.wait()

                path_discovery.expire_stale_contacts()

                announce_files = logger.all_announce_db_paths()
                paths_db       = os.path.join(self.storage_path, "paths.db")
                addr           = path_discovery.get_next_candidate(announce_files, paths_db)

                if addr and self._mc:
                    contact = self._mc.get_contact_by_key_prefix(addr)
                    if not contact:
                        for pk in (self._mc.contacts or {}):
                            if pk.lower().startswith(addr.lower()):
                                contact = self._mc.contacts[pk]
                                break

                    if contact:
                        pk = contact.get("public_key", "")
                        if len(pk) >= 64:
                            print(f"[path_discovery] querying path to {addr}")
                            try:
                                event = await self._mc.commands.send_path_discovery_sync(
                                    pk, min_timeout=10
                                )
                            except Exception as exc:
                                print(f"[path_discovery] send error for {addr}: {exc}")
                                event = None

                            path_discovery.mark_contacted(addr)

                            if event and event.payload:
                                pl     = event.payload
                                hlen   = pl.get("out_path_hash_len", 1)
                                n_hops = pl.get("out_path_len", 0)
                                raw    = pl.get("out_path", "")
                                if raw and n_hops > 0:
                                    seg = hlen * 2
                                    segs     = [raw[i:i+seg] for i in range(0, n_hops * seg, seg)]
                                    path_str = ",".join(segs)
                                    our_id   = None
                                    try:
                                        our_id = (self._mc.self_info or {}).get("pubkey_pre")
                                    except Exception:
                                        pass
                                    paths.log(f"Path: {path_str}", sender_id=addr, our_id=our_id)
                                    path_discovery.mark_responded(addr, path_str=path_str)
                                    print(f"[path_discovery] path logged for {addr}: {path_str}")
                                else:
                                    path_discovery.mark_responded(addr)
                                    print(f"[path_discovery] {addr} responded, no path data")
                            else:
                                print(f"[path_discovery] {addr} did not respond "
                                      f"(will retry if attempts remain)")
                        else:
                            print(f"[path_discovery] no full pubkey for {addr}, skipping")
                    else:
                        print(f"[path_discovery] contact not found for {addr}, skipping")
                elif not addr:
                    print("[path_discovery] no candidates — all GPS nodes have path data "
                          "or are exhausted")

            except asyncio.CancelledError:
                return
            except Exception as exc:
                print(f"[path_discovery] task error: {exc}")

            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

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

            # Also write self_info.json for the path map so the bot's own
            # position can be used as a GPS anchor without needing an announce.
            lat = info.get("lat") if info else None
            lon = info.get("lon") if info else None
            pubkey_pre = info.get("pubkey_pre") if info else None
            name = info.get("adv_name") or info.get("name") if info else None
            self_path = os.path.join(self.storage_path, "self_info.json")
            with open(self_path, "w") as f:
                _json.dump({
                    "proto":      "meshcore",
                    "pubkey_pre": pubkey_pre or pubkey[:6],
                    "lat":        lat,
                    "lon":        lon,
                    "name":       name,
                }, f)
            gps_str = f"{lat:.4f},{lon:.4f}" if lat and lon else "no GPS"
            print(f"[meshcore_adapter] self_info saved ({gps_str})")
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
