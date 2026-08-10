"""
One-time BLE pairing helper for MeshCore companion radio.
Based on meshcore-gui's ble_agent.py (pe1hvh/meshcore-gui, MIT licence).

Run once to pair. After success the bond is cached in BlueZ and
NodeBot connects without prompting again.

Usage:
    python3 pair_meshcore.py <PIN>
    e.g.  python3 pair_meshcore.py 917113
"""
import asyncio
import sys
from bleak import BleakClient, BleakScanner
from dbus_fast.aio import MessageBus
from dbus_fast import BusType
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/meshcore/ble_agent"
DEVICE_MAC  = "3C:0F:02:EC:E6:B9"


class BluezAgent(ServiceInterface):
    """BlueZ Agent1 that returns the configured PIN for all pairing requests."""

    def __init__(self, pin: str = "123456") -> None:
        super().__init__("org.bluez.Agent1")
        self.pin = pin

    @method()
    def Release(self) -> None:
        print("Agent: released")

    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        print(f"Agent: PIN requested for {device}, responding with {self.pin}")
        return self.pin

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        val = int(self.pin)
        print(f"Agent: Passkey requested for {device}, responding with {val}")
        return val

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
        print(f"Agent: Device is displaying passkey {passkey:06d} (we entered {entered})")

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        print(f"Agent: Device is displaying PIN {pincode}")

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        print(f"Agent: Confirming passkey {passkey:06d} for {device}")
        # Silently accept — mirrors meshcore-gui behaviour

    @method()
    def RequestAuthorization(self, device: 'o') -> None:
        print(f"Agent: Authorizing {device}")

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        print(f"Agent: Authorizing service {uuid} for {device}")

    @method()
    def Cancel(self) -> None:
        print("Agent: pairing cancelled by device")


async def main(pin: str):
    # ── 1. Register D-Bus pairing agent ──────────────────────────────────
    print(f"Registering BlueZ pairing agent (PIN={pin})...")
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = BluezAgent(pin)
    bus.export(AGENT_PATH, agent)

    introspection = await bus.introspect("org.bluez", "/org/bluez")
    proxy        = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
    agent_mgr    = proxy.get_interface("org.bluez.AgentManager1")

    await agent_mgr.call_register_agent(AGENT_PATH, "KeyboardOnly")
    await agent_mgr.call_request_default_agent(AGENT_PATH)
    print("Agent registered as default.")

    # ── 2. Connect and pair ───────────────────────────────────────────────
    print(f"\nConnecting to {DEVICE_MAC}...")
    try:
        async with BleakClient(DEVICE_MAC) as client:
            print(f"Connected. BLE services resolved.")
            print("Initiating pairing (BlueZ will invoke agent for PIN)...")
            try:
                result = await asyncio.wait_for(client.pair(), timeout=30.0)
                print(f"\n✓ Pairing successful! result={result}")
                print("  Bond is now cached in BlueZ.")
                print("  NodeBot can now connect without this script.")
            except asyncio.TimeoutError:
                print("\n✗ Pairing timed out after 30s.")
                print("  Check the PIN on the Heltec V3 screen and try again.")
            except Exception as e:
                print(f"\n✗ Pairing error: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"Connection failed: {e}")

    # ── 3. Cleanup ────────────────────────────────────────────────────────
    try:
        await agent_mgr.call_unregister_agent(AGENT_PATH)
    except Exception:
        pass
    bus.disconnect()
    print("\nAgent unregistered.")


if __name__ == "__main__":
    pin = sys.argv[1] if len(sys.argv) > 1 else "123456"
    asyncio.run(main(pin))
