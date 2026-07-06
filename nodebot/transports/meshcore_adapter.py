import asyncio
import collections
import configparser
import os
import re
import threading
import time

from .. import logger, radio_status


CHAN_SOCK_PATH = "/tmp/nodebot_chan.sock"  # nosec B108 — Unix socket, not a temp file
CHAN_BUFFER_MAX = 500


class MeshCoreAdapter:
    """
    MeshCore transport adapter for NodeBot.

    Connects to a MeshCore radio over serial, subscribes to incoming private
    messages, and routes them through the NodeBot engine. Replies are sent back
    via the MeshCore library's async send_msg API.

    The MeshCore library is fully async. This adapter runs its event loop on a
    dedicated thread and bridges the sync engine callback back into that loop
    with asyncio.run_coroutine_threadsafe().

    Public channel messages are kept in a RAM ring buffer and served to
    chanlisten clients over a Unix socket (/tmp/nodebot_chan.sock).
    """

    def __init__(self, storage_path, engine):

        self.storage_path = storage_path
        self.engine = engine

        self._mc = None
        self._loop = None
        self._thread = None
        self.running = False

        self._chan_buffer = collections.deque(maxlen=CHAN_BUFFER_MAX)
        self._chan_clients = set()   # asyncio.StreamWriter instances
        self._recent_msgs = {}       # (pubkey_prefix, text) -> timestamp for DM dedup
        self._recent_chan_msgs = {}  # (sender_ts, sender_id, text[:32]) -> timestamp for channel dedup
        self._seen_contacts = {}     # sender_id -> (rounded_lat, rounded_lon) for announce dedup
        self._last_periodic_announce = 0.0
        self._last_rx_ts = 0.0       # updated on any event from the firmware; used by watchdog
        self._chan_names = {}        # chan_idx -> channel name (populated by _query_channels)

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "..", "config.ini")
        cfg = configparser.ConfigParser()
        cfg.read(_config_path)

        self._node_name = cfg.get("bot", "name", fallback="NodeBot").strip()

        self.port = cfg.get("meshcore", "port", fallback="/dev/meshcore0").strip()
        self.baudrate = int(cfg.get("meshcore", "baudrate", fallback="115200"))

        _channels_raw = cfg.get("meshcore", "channels", fallback="#test").strip()
        self._extra_channels = [c.strip() for c in _channels_raw.split(",") if c.strip()]

        self._gps_mode      = cfg.get("gps", "gps_mode",      fallback="disabled").strip()
        self._gps_lat       = cfg.get("gps", "gps_lat",       fallback="").strip()
        self._gps_lon       = cfg.get("gps", "gps_lon",       fallback="").strip()
        self._gps_alt       = cfg.get("gps", "gps_alt",       fallback="0").strip()
        self._gps_device    = cfg.get("gps", "gps_device",    fallback="").strip()
        self._gps_precision = int(cfg.get("gps", "gps_precision", fallback="4").strip())

        # Track last coordinates pushed to the radio (rounded)
        self._last_gps_lat = None
        self._last_gps_lon = None

        print(f"[meshcore_adapter] port={self.port} baud={self.baudrate} gps_mode={self._gps_mode} gps_precision={self._gps_precision}")

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

        server_task = asyncio.create_task(self._run_chan_server())

        _retry = 0

        try:
            while self.running:
                try:
                    cx = SerialConnection(self.port, self.baudrate)
                    self._mc = MeshCore(cx, auto_reconnect=False)

                    # Subscribe before connect so _on_self_info fires when
                    # send_appstart() triggers SELF_INFO during the handshake.
                    self._mc.subscribe(EventType.SELF_INFO, self._on_self_info)

                    conn_result = await self._mc.connect()
                    if conn_result is None:
                        raise RuntimeError(
                            f"MeshCore handshake timed out on {self.port} — "
                            f"device did not respond to APP_START. "
                            f"If an rNode is also connected, check that the devices "
                            f"are not swapped (wrong device in wrong USB port)."
                        )
                    print(f"[meshcore_adapter] connected to {self.port}")
                    _retry = 0  # reset backoff on successful connect

                    # Yield to the event loop so pending async callbacks
                    # (_on_self_info) can run before the rest of setup.
                    await asyncio.sleep(0.2)

                    await self._mc.ensure_contacts()
                    print(f"[meshcore_adapter] contacts loaded: {len(self._mc.contacts)}")

                    await self._query_channels()
                    await self._set_node_name()
                    await self._set_gps_location()
                    await self._announce_async()

                    sub = self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_message)
                    sub_chan = self._mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_message)
                    sub_rflog = self._mc.subscribe(EventType.RX_LOG_DATA, self._on_rf_log)
                    sub_advert = self._mc.subscribe(EventType.ADVERTISEMENT, self._on_advertisement)
                    sub_path = self._mc.subscribe(EventType.PATH_UPDATE, self._on_path_update)

                    await self._mc.start_auto_message_fetching()
                    print("[meshcore_adapter] listening for messages")
                    radio_status.update("meshcore", "connected")

                    if self.engine:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, lambda: self.engine.flush_outbound("meshcore_adapter")
                        )

                    gps_task = None
                    if self._gps_mode in ("gpsd", "serial", "future"):
                        gps_task = asyncio.create_task(self._gps_update_loop())

                    # Idle — callbacks drive everything from here.
                    # Every 5 seconds dispatch a synthetic MESSAGES_WAITING so
                    # the library's fetch loop drains any channel messages the
                    # radio queued without sending an explicit notification.
                    # Every 15 minutes probe device liveness; reconnect if no
                    # response (handles silent serial disconnects).
                    _POLL_SECS      = 5.0
                    _WATCHDOG_SECS  = 15 * 60
                    _last_poll      = asyncio.get_event_loop().time()
                    _last_watchdog  = asyncio.get_event_loop().time()
                    self._last_rx_ts = time.time()  # seed so first window is fair
                    while self.running:
                        await asyncio.sleep(1)
                        now = asyncio.get_event_loop().time()
                        if now - _last_poll >= _POLL_SECS:
                            _last_poll = now
                            if self._mc:
                                await self._mc.dispatcher.dispatch(
                                    Event(EventType.MESSAGES_WAITING, {})
                                )
                        if now - _last_watchdog >= _WATCHDOG_SECS:
                            _last_watchdog = now
                            elapsed = time.time() - self._last_rx_ts
                            if elapsed > _WATCHDOG_SECS:
                                raise RuntimeError(
                                    f"No firmware events for {int(elapsed)}s — forcing reconnect"
                                )

                    if gps_task:
                        gps_task.cancel()
                        try:
                            await gps_task
                        except asyncio.CancelledError:
                            pass

                    self._mc.unsubscribe(sub)
                    self._mc.unsubscribe(sub_chan)
                    self._mc.unsubscribe(sub_rflog)
                    self._mc.unsubscribe(sub_advert)
                    self._mc.unsubscribe(sub_path)
                    await self._mc.disconnect()
                    break

                except Exception as e:
                    delay = min(10 * (2 ** _retry), 60)
                    _retry += 1
                    print(f"[meshcore_adapter] connection error: {e} — retrying in {delay}s")
                    radio_status.update("meshcore", "error", error=str(e))
                    self._mc = None
                    if self.running:
                        await asyncio.sleep(delay)

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    # =====================================================
    # NODE NAME
    # =====================================================

    async def _set_node_name(self):
        if not self._node_name:
            return
        try:
            await self._mc.commands.set_name(self._node_name)
            print(f"[meshcore_adapter] node name set: {self._node_name}")
        except AttributeError:
            print("[meshcore_adapter] set_name not supported by this meshcore version")
        except Exception as e:
            print(f"[meshcore_adapter] could not set node name: {e}")

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
            print(f"[meshcore_adapter] node pubkey: {pubkey}")
            print(f"[meshcore_adapter] node info saved: mc:{pubkey[:8]}")
        except Exception as e:
            print(f"[meshcore_adapter] could not save node info: {e}")

    # =====================================================
    # GPS LOCATION
    # =====================================================

    async def _set_gps_location(self):
        mode = self._gps_mode
        if mode == "disabled" or mode == "future":
            return

        # Enable location sharing in advertisements (adv_loc_policy=1)
        try:
            await self._mc.commands.set_advert_loc_policy(1)
            print("[meshcore_adapter] GPS: location sharing enabled (adv_loc_policy=1)")
        except Exception as e:
            print(f"[meshcore_adapter] GPS: could not set advert_loc_policy: {e}")

        lat = lon = alt = None

        if mode == "manual":
            try:
                lat = float(self._gps_lat)
                lon = float(self._gps_lon)
                alt = float(self._gps_alt or "0")
            except ValueError:
                print("[meshcore_adapter] GPS: invalid manual coordinates in config, skipping")
                return

        elif mode == "gpsd":
            lat, lon, alt = await asyncio.get_event_loop().run_in_executor(None, self._read_gpsd)
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
        """Round to configured precision, send only when value changed or forced."""
        prec = self._gps_precision
        lat_r = round(lat, prec)
        lon_r = round(lon, prec)
        alt_r = round(alt, 1)

        changed = (lat_r, lon_r) != (self._last_gps_lat, self._last_gps_lon)
        if not changed and not force:
            return

        try:
            await self._mc.commands.set_coords(lat_r, lon_r)
            self._last_gps_lat = lat_r
            self._last_gps_lon = lon_r
            print(f"[meshcore_adapter] GPS pushed: lat={lat_r} lon={lon_r} alt={alt_r}")
            await self._mc.commands.send_advert()
        except Exception as e:
            print(f"[meshcore_adapter] GPS: set_coords failed: {e}")

    async def _gps_update_loop(self):
        """Periodic GPS push — every 5 minutes, or sooner if position changed.
        In 'future' mode, scans for a GPS device every 60 seconds until one appears."""
        UPDATE_INTERVAL = 300   # seconds between forced updates
        CHECK_INTERVAL  = 30    # how often to poll the GPS source
        SCAN_INTERVAL   = 60    # how often to probe ports in 'future' mode

        last_forced = time.time()
        last_scan   = 0.0

        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            if not self._mc:
                break

            loop = asyncio.get_event_loop()
            mode = self._gps_mode

            # ── Auto-discovery for "future" mode ─────────────────
            if mode == "future":
                now = time.time()
                if now - last_scan >= SCAN_INTERVAL:
                    last_scan = now
                    device, _baud = await loop.run_in_executor(None, self._scan_for_gps)
                    if device:
                        print(f"[meshcore_adapter] GPS auto-discovered: {device}")
                        self._gps_mode = "serial"
                        self._gps_device = device
                        # Fall through immediately to take an initial fix
                        lat, lon, alt = await loop.run_in_executor(
                            None, self._read_serial_gps, device, 30
                        )
                        if lat is not None:
                            await self._push_gps(lat, lon, alt, force=True)
                            last_forced = time.time()
                continue   # check again on next tick; mode may have changed

            # ── Normal live GPS polling ───────────────────────────
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

            now = time.time()
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
        """Load channel keys into the parser and enable RF-log decryption."""
        from meshcore.events import EventType as _ET
        found = 0
        for idx in range(8):
            try:
                result = await self._mc.commands.get_channel(idx)
                if result.type == _ET.ERROR:
                    break
                p = result.payload
                name = p.get("channel_name", "")
                h = p.get("channel_hash", "??")
                print(f"[meshcore_adapter] channel {idx}: name={name!r} hash={h}")
                if name:
                    self._chan_names[idx] = name
                found += 1
            except Exception as e:
                print(f"[meshcore_adapter] channel {idx} query error: {e}")
                break
        if found == 0:
            print("[meshcore_adapter] no channels configured on device")

        # Clear duplicate channel entries (same name in multiple slots).
        # This can happen when NodeBot restarts and rewrites a channel that was
        # already configured — keep only the first occurrence, clear the rest.
        seen_names = {}
        for idx in range(1, 8):
            name = self._chan_names.get(idx)
            if not name:
                continue
            if name in seen_names:
                print(f"[meshcore_adapter] clearing duplicate channel slot {idx} ({name!r}, already in slot {seen_names[name]})")
                try:
                    await self._mc.commands.set_channel(idx, "")
                    del self._chan_names[idx]
                except Exception as e:
                    print(f"[meshcore_adapter] could not clear duplicate slot {idx}: {e}")
            else:
                seen_names[name] = idx

        # Configure any extra channels that are not already set on the device.
        # Slots are filled starting at index 1 (slot 0 is always "Public").
        # Skip channels that are already configured in any slot to prevent
        # duplicates accumulating across reconnects.
        already_named = set(self._chan_names.values())
        slot = 1
        for ch_name in self._extra_channels:
            if not ch_name or ch_name in already_named:
                continue
            # Find next empty slot
            while slot < 8 and self._chan_names.get(slot):
                slot += 1
            if slot >= 8:
                print("[meshcore_adapter] no empty channel slots available for extra channels")
                break
            print(f"[meshcore_adapter] configuring channel {slot} = {ch_name!r}")
            try:
                await self._mc.commands.set_channel(slot, ch_name)
                # Re-query this slot so the key is loaded into the parser
                result = await self._mc.commands.get_channel(slot)
                if result.type != _ET.ERROR:
                    p2 = result.payload
                    name2 = p2.get("channel_name", "")
                    h2 = p2.get("channel_hash", "??")
                    print(f"[meshcore_adapter] channel {slot} confirmed: name={name2!r} hash={h2}")
                    if name2:
                        self._chan_names[slot] = name2
                        already_named.add(name2)
            except Exception as e:
                print(f"[meshcore_adapter] channel {slot} configure error: {e}")
            slot += 1

        # Enable RF-log decryption so LOG_DATA packets (0x88) yield plaintext
        # even when the firmware doesn't emit a CHANNEL_MSG_RECV packet.
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
            # Send buffered history
            for entry in list(self._chan_buffer):
                writer.write((json.dumps(entry) + "\n").encode())
            writer.write((json.dumps({"type": "history_end"}) + "\n").encode())
            await writer.drain()

            # Hold connection open until client disconnects
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

    def _maybe_log_contact_announce(self, sender_id, contact, rssi=None, snr=None, hops=None):
        """Log to announce log when a contact is first seen, or position/name changes."""
        if not sender_id:
            return
        # Normalize to 8-char prefix for consistent DB keys regardless of
        # whether sender_id came from an advertisement (pub[:8]) or a contact
        # message payload (pubkey_prefix may be longer).
        log_id = sender_id[:12]
        nick = contact.get("adv_name") if contact else None
        lat  = contact.get("lat")  if contact else None
        lon  = contact.get("lon")  if contact else None
        alt  = contact.get("alt")  if contact else None
        pos_key = (round(lat, 3) if lat else None, round(lon, 3) if lon else None)
        prev = self._seen_contacts.get(log_id)
        prev_nick = prev[0] if prev else None
        prev_pos  = prev[1] if prev else None
        if prev_pos == pos_key and prev_nick == nick:
            return
        self._seen_contacts[log_id] = (nick, pos_key)
        logger.log_announce("meshcore", log_id, nick=nick, lat=lat, lon=lon, alt=alt,
                            rssi=rssi, snr=snr, hops=hops)

    async def _on_advertisement(self, event):
        """Handle incoming advertisement (node announce broadcast)."""
        self._last_rx_ts = time.time()
        try:
            pub = event.payload.get("public_key", "")
            if not pub:
                return
            prefix = pub[:12].lower()
            contact = self._mc.get_contact_by_key_prefix(prefix) if self._mc else None
            self._maybe_log_contact_announce(prefix, contact)
        except Exception as e:
            print(f"[meshcore_adapter] advertisement handler error: {e}")

    async def _on_path_update(self, event):
        """Handle path/position update broadcast."""
        try:
            pub = event.payload.get("public_key", "")
            if not pub:
                return
            prefix = pub[:12].lower()
            contact = self._mc.get_contact_by_key_prefix(prefix) if self._mc else None
            self._maybe_log_contact_announce(prefix, contact)
        except Exception as e:
            print(f"[meshcore_adapter] path update handler error: {e}")

    # =====================================================
    # INBOUND MESSAGE
    # =====================================================

    async def _on_contact_message(self, event):

        try:
            payload = event.payload
            pubkey_prefix = payload.get("pubkey_prefix", "")
            text = payload.get("text", "").strip()

            if not text or not pubkey_prefix:
                return

            # Deduplicate: MeshCore retransmits when ACK is unreliable
            now_ts = time.time()
            dedup_key = (pubkey_prefix, text)
            stale = [k for k, t in self._recent_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_msgs[k]
            if dedup_key in self._recent_msgs:
                print(f"[meshcore_adapter] duplicate from {pubkey_prefix}, ignoring")
                return
            self._recent_msgs[dedup_key] = now_ts

            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix) if self._mc else None
            nick = contact.get("adv_name") if contact else None

            print(f"[meshcore_adapter] msg from {pubkey_prefix}: {text!r}")
            logger.log_dm("meshcore", pubkey_prefix, text, nick=nick)

            self._maybe_log_contact_announce(pubkey_prefix, contact)

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
    # INBOUND CHANNEL MESSAGE (public broadcast)
    # =====================================================

    _SENDER_RE   = re.compile(r'^([0-9A-Fa-f]{2,16}):\s*(.*)', re.DOTALL)
    # Matches "Name: rest" where Name is ≤25 chars with no embedded colon.
    # Used as a fallback display-name when contact lookup fails.
    _TEXT_NAME_RE = re.compile(r'^([^:\n]{1,25}):\s+(.+)', re.DOTALL)

    async def _on_channel_message(self, event):

        self._last_rx_ts = time.time()
        try:
            payload = event.payload
            chan_idx = payload.get("channel_idx", 0)
            raw_text = payload.get("text", "").strip()
            sender_ts = payload.get("sender_timestamp", 0)

            if not raw_text:
                return

            # MeshCore prepends sender pubkey prefix to channel messages: "AABBCCDD: message"
            sender_id = None
            text = raw_text
            m = self._SENDER_RE.match(raw_text)
            if m:
                sender_id = m.group(1).lower()
                text = m.group(2).strip()

            # Dedup across CHANNEL_MSG_RECV and RX_LOG_DATA paths.
            # Key on (sender_ts, text-after-pubkey-prefix) so both paths produce
            # the same key for the same message.  The rflog path never has the
            # pubkey prefix, so using sender_id here would break cross-path dedup.
            # Using the full text (not truncated) means relay copies of the same
            # packet — identical text, same sender_ts — are also suppressed.
            dedup_key = (sender_ts, text)
            now_ts = time.time()
            stale = [k for k, t in self._recent_chan_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_chan_msgs[k]
            if dedup_key in self._recent_chan_msgs:
                return
            self._recent_chan_msgs[dedup_key] = now_ts

            # Try to resolve a friendly name from the contacts list
            sender_name = sender_id
            contact = None
            text_name = None
            if sender_id and self._mc:
                contact = self._mc.get_contact_by_key_prefix(sender_id)
                if contact:
                    sender_name = contact.get("adv_name") or sender_id
                    # Upgrade short text-embedded prefix to full 12-char pubkey
                    full_key = contact.get("public_key", "")
                    if full_key:
                        sender_id = full_key[:12].lower()

            # Fallback: if contact lookup failed, try to extract "Name: message"
            # from the text body (standard MeshCore convention).
            if not contact:
                tn = self._TEXT_NAME_RE.match(text)
                if tn:
                    text_name = tn.group(1).strip()
                    text = tn.group(2).strip()

            rssi = payload.get("RSSI")
            snr  = payload.get("SNR")
            hops = payload.get("hops_away")
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr, hops=hops)
            self._dispatch_channel_entry(chan_idx, sender_name or sender_id or "unknown",
                                         text, rssi, snr, sender_id=sender_id, hops=hops,
                                         text_name=text_name)

        except Exception as e:
            print(f"[meshcore_adapter] channel message error: {e}")

    # =====================================================
    # INBOUND CHANNEL MESSAGE via RF log (fallback path)
    # =====================================================

    async def _on_rf_log(self, event):
        """Handle RX_LOG_DATA events.

        The firmware emits LOG_DATA (0x88) for every received RF packet.
        When decrypt_channels is True and the channel key is loaded, the
        library decrypts GRP_TXT (channel) payloads so we get plaintext
        even if the firmware never emits a CHANNEL_MSG_RECV packet.
        """
        self._last_rx_ts = time.time()
        try:
            p = event.payload
            typename = p.get("payload_typename")

            if typename == "ADVERT":
                adv_key = p.get("adv_key", "")
                if adv_key:
                    prefix = adv_key[:12].lower()
                    contact = self._mc.get_contact_by_key_prefix(prefix) if self._mc else None
                    lat = p.get("adv_lat")
                    lon = p.get("adv_lon")
                    # Merge in contact's stored position if advert didn't carry one
                    if lat is None and contact:
                        lat = contact.get("lat")
                        lon = contact.get("lon")
                    # Synthesise a minimal contact-like dict so _maybe_log_contact_announce
                    # gets the name even if the contact lookup missed.
                    adv_contact = dict(contact) if contact else {}
                    if p.get("adv_name"):
                        adv_contact.setdefault("adv_name", p["adv_name"])
                    if lat is not None:
                        adv_contact["lat"] = lat
                    if lon is not None:
                        adv_contact["lon"] = lon
                    rssi = p.get("rssi")
                    snr  = p.get("snr")
                    hops = p.get("hops_away")
                    self._maybe_log_contact_announce(prefix, adv_contact, rssi=rssi, snr=snr, hops=hops)
                return

            if typename == "TEXT_MSG":
                return

            if typename != "GRP_TXT":
                return

            chan_hash = p.get("chan_hash", "?")
            msg = p.get("message")  # present only when decrypted successfully

            if msg is None:
                # Firmware received a channel message but our device can't
                # decrypt it — the channel key doesn't match.
                print(f"[meshcore_adapter] RF chan msg (undecrypted) — "
                      f"chan_hash={chan_hash}; check channel key on device")
                return

            raw_text = msg.strip() if isinstance(msg, str) else msg.decode("utf-8", "ignore").strip()
            if not raw_text:
                return

            # Strip the "AABBCCDD: message" sender prefix so the text
            # matches what _on_channel_message produces for dedup.
            sender_id = None
            text = raw_text
            m = self._SENDER_RE.match(raw_text)
            if m:
                sender_id = m.group(1).lower()
                text = m.group(2).strip()

            sender_ts = p.get("sender_timestamp", 0)

            # Dedup against messages already seen via CHANNEL_MSG_RECV.
            # Key on (sender_ts, text-after-pubkey-prefix) — same strategy as
            # _on_channel_message — so relay copies and cross-path duplicates
            # share the same key and are suppressed.
            dedup_key = (sender_ts, text)
            now_ts = time.time()
            stale = [k for k, t in self._recent_chan_msgs.items() if now_ts - t > 60]
            for k in stale:
                del self._recent_chan_msgs[k]
            if dedup_key in self._recent_chan_msgs:
                return
            self._recent_chan_msgs[dedup_key] = now_ts

            # Resolve channel index and friendly sender name
            chan_idx = 0
            sender_name = sender_id or chan_hash
            contact = None
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
                        # Upgrade short text-embedded prefix to full 12-char pubkey
                        full_key = contact.get("public_key", "")
                        if full_key:
                            sender_id = full_key[:12].lower()

            # Fallback: extract "Name: message" from text when contact lookup failed
            text_name = None
            if not contact:
                tn = self._TEXT_NAME_RE.match(text)
                if tn:
                    text_name = tn.group(1).strip()
                    text = tn.group(2).strip()

            rssi = p.get("rssi")
            snr  = p.get("snr")
            hops = p.get("hops_away")
            self._maybe_log_contact_announce(sender_id, contact, rssi=rssi, snr=snr, hops=hops)
            self._dispatch_channel_entry(chan_idx, sender_name, text, rssi, snr,
                                         via="rflog", sender_id=sender_id, hops=hops,
                                         text_name=text_name)

        except Exception as e:
            print(f"[meshcore_adapter] RF log handler error: {e}")

    def _dispatch_channel_entry(self, chan_idx, sender, text, rssi, snr, via="push", sender_id=None, hops=None, text_name=None):
        """Build the channel entry dict, log it, buffer it, and push to socket clients."""
        import json

        now = time.time()
        entry = {
            "ts": now,
            "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "proto": "meshcore",
            "chan": chan_idx,
            "sender": sender,
            "text": text,
        }
        if rssi is not None:
            entry["rssi"] = rssi
        if snr is not None:
            entry["snr"] = snr

        print(f"[meshcore_adapter] chan[{chan_idx}] <{sender}> {text!r} (via {via})")
        chan_name = self._chan_names.get(chan_idx)
        proto_tag = f"meshcore/{chan_name}" if chan_name else f"meshcore/chan{chan_idx}"
        addr = sender_id or sender or "unknown"
        # Prefer: contact-resolved name > text-extracted name > nothing
        real_name = sender if (sender_id and sender and sender.lower() != sender_id.lower()) else None
        display_name = real_name or text_name or None
        logger.log_channel(proto_tag, addr, text, long_name=display_name, hops=hops)

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

        if not self._loop or not self._mc:
            print("[meshcore_adapter] not connected, dropping reply")
            if notify_cb:
                notify_cb(False)
            return

        future = asyncio.run_coroutine_threadsafe(
            self._send_async(pubkey_prefix, content),
            self._loop
        )
        try:
            success = future.result(timeout=10)
            if notify_cb:
                notify_cb(bool(success))
        except Exception as e:
            print(f"[meshcore_adapter] send error: {e}")
            if notify_cb:
                notify_cb(False)

    # =====================================================
    # OUTBOUND MESSAGE (async)
    # =====================================================

    async def _send_async(self, pubkey_prefix, content):

        try:
            from meshcore.events import EventType
            if not self._mc:
                return False

            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix)
            if contact is None:
                print(f"[meshcore_adapter] no contact for {pubkey_prefix}, dropping")
                return False

            result = await self._mc.commands.send_msg_with_retry(contact, content)
            if result.type == EventType.ERROR:
                print(f"[meshcore_adapter] send failed to {pubkey_prefix}: {result.payload}")
                return False
            print(f"[meshcore_adapter] sent to {pubkey_prefix}")
            return True

        except Exception as e:
            print(f"[meshcore_adapter] async send error: {e}")
            return False

    # =====================================================
    # ANNOUNCE
    # =====================================================

    _PERIODIC_ANNOUNCE_INTERVAL = 43200  # 12 hours

    def announce(self):
        if not self._loop or not self._mc:
            print("[meshcore_adapter] not connected, cannot announce")
            return
        now = time.time()
        if now - self._last_periodic_announce < self._PERIODIC_ANNOUNCE_INTERVAL:
            return
        self._last_periodic_announce = now
        future = asyncio.run_coroutine_threadsafe(
            self._announce_async(),
            self._loop
        )
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"[meshcore_adapter] announce error: {e}")

    async def _announce_async(self):
        try:
            await self._mc.commands.send_advert()
            print("[meshcore_adapter] announced on network")
        except Exception as e:
            print(f"[meshcore_adapter] announce failed: {e}")

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
