import asyncio
import collections
import configparser
import os
import threading
import time


CHAN_SOCK_PATH = "/tmp/nodebot_chan.sock"
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
        self._recent_msgs = {}       # (pubkey_prefix, text) -> timestamp for dedup

        _here = os.path.dirname(os.path.abspath(__file__))
        _config_path = os.path.join(_here, "..", "..", "config.ini")
        cfg = configparser.ConfigParser()
        cfg.read(_config_path)

        self.port = cfg.get("meshcore", "port", fallback="/dev/meshcore0").strip()
        self.baudrate = int(cfg.get("meshcore", "baudrate", fallback="115200"))

        print(f"[meshcore_adapter] port={self.port} baud={self.baudrate}")

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
        from meshcore.events import EventType

        server_task = asyncio.create_task(self._run_chan_server())

        try:
            while self.running:
                try:
                    cx = SerialConnection(self.port, self.baudrate)
                    self._mc = MeshCore(cx, auto_reconnect=True, max_reconnect_attempts=0)

                    await self._mc.connect()
                    print(f"[meshcore_adapter] connected to {self.port}")

                    await self._mc.ensure_contacts()
                    print(f"[meshcore_adapter] contacts loaded: {len(self._mc.contacts)}")

                    sub = self._mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_message)
                    sub_chan = self._mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_message)

                    await self._mc.start_auto_message_fetching()
                    print("[meshcore_adapter] listening for messages")

                    # Idle — callbacks drive everything from here
                    while self.running:
                        await asyncio.sleep(1)

                    self._mc.unsubscribe(sub)
                    self._mc.unsubscribe(sub_chan)
                    await self._mc.disconnect()
                    break

                except Exception as e:
                    print(f"[meshcore_adapter] connection error: {e} — retrying in 10s")
                    self._mc = None
                    if self.running:
                        await asyncio.sleep(10)

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

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

            print(f"[meshcore_adapter] msg from {pubkey_prefix}: {text!r}")

            if self.engine:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.engine.handle_message(
                        sender=pubkey_prefix,
                        message=text,
                        send_callback=self._send_reply
                    )
                )

        except Exception as e:
            print(f"[meshcore_adapter] receive error: {e}")

    # =====================================================
    # INBOUND CHANNEL MESSAGE (public broadcast)
    # =====================================================

    async def _on_channel_message(self, event):

        import json
        import re

        _SENDER_RE = re.compile(r'^([0-9A-Fa-f]{4,16}):\s*(.*)', re.DOTALL)

        try:
            payload = event.payload
            chan_idx = payload.get("channel_idx", 0)
            raw_text = payload.get("text", "").strip()

            if not raw_text:
                return

            # MeshCore prepends sender pubkey prefix to channel messages: "AABBCCDD: message"
            sender_id = None
            text = raw_text
            m = _SENDER_RE.match(raw_text)
            if m:
                sender_id = m.group(1).lower()
                text = m.group(2).strip()

            # Try to resolve a friendly name from the contacts list
            sender_name = sender_id
            if sender_id and self._mc:
                contact = self._mc.get_contact_by_key_prefix(sender_id)
                if contact:
                    sender_name = contact.get("adv_name") or sender_id

            now = time.time()
            entry = {
                "ts": now,
                "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "proto": "meshcore",
                "chan": chan_idx,
                "sender": sender_name or "unknown",
                "text": text,
            }

            rssi = payload.get("RSSI")
            snr = payload.get("SNR")
            if rssi is not None:
                entry["rssi"] = rssi
            if snr is not None:
                entry["snr"] = snr

            print(f"[meshcore_adapter] chan[{chan_idx}] <{sender_name or '?'}> {text!r}")

            self._chan_buffer.append(entry)

            # Broadcast to connected chanlisten clients
            msg_bytes = (json.dumps(entry) + "\n").encode()
            dead = set()
            for writer in list(self._chan_clients):
                try:
                    writer.write(msg_bytes)
                except Exception:
                    dead.add(writer)
            self._chan_clients -= dead

        except Exception as e:
            print(f"[meshcore_adapter] channel message error: {e}")

    # =====================================================
    # REPLY CALLBACK (sync — called from engine thread)
    # =====================================================

    def _send_reply(self, pubkey_prefix, content):

        if not self._loop or not self._mc:
            print("[meshcore_adapter] not connected, dropping reply")
            return

        future = asyncio.run_coroutine_threadsafe(
            self._send_async(pubkey_prefix, content),
            self._loop
        )
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"[meshcore_adapter] send error: {e}")

    # =====================================================
    # OUTBOUND MESSAGE (async)
    # =====================================================

    async def _send_async(self, pubkey_prefix, content):

        try:
            if not self._mc:
                return

            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix)
            if contact is None:
                print(f"[meshcore_adapter] no contact for {pubkey_prefix}, dropping")
                return

            await self._mc.commands.send_msg(contact, content)
            print(f"[meshcore_adapter] sent to {pubkey_prefix}")

        except Exception as e:
            print(f"[meshcore_adapter] async send error: {e}")

    # =====================================================
    # ANNOUNCE
    # =====================================================

    def announce(self):
        if not self._loop or not self._mc:
            print("[meshcore_adapter] not connected, cannot announce")
            return
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
