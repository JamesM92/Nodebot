import glob
import os
import time


def scan_for_gps(exclude_port=None):
    """Probe serial ports for NMEA GPS sentences.

    exclude_port: real path of a port to skip (e.g. the radio's own port).
    Returns (device, baud) or (None, None).
    """
    import serial as _serial

    candidates = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(sorted(glob.glob(pattern)))
    for fixed in ("/dev/ttyAMA0", "/dev/serial0"):
        if os.path.exists(fixed):
            candidates.append(fixed)

    exclude_real = os.path.realpath(exclude_port) if exclude_port else None

    for port in candidates:
        if exclude_real and (os.path.realpath(port) == exclude_real or port == exclude_port):
            continue
        for baud in (9600, 4800, 115200):
            try:
                s = _serial.Serial(port, baud, timeout=2)
                deadline = time.time() + 6
                while time.time() < deadline:
                    try:
                        line = s.readline().decode("ascii", errors="ignore").strip()
                    except Exception:
                        continue
                    if line.startswith(("$GPGGA", "$GNGGA", "$GPRMC", "$GNRMC")):
                        s.close()
                        return port, baud
                s.close()
            except Exception:
                pass
    return None, None


def read_gpsd(timeout=30):
    """Read a GPS fix from gpsd over TCP. Returns (lat, lon, alt) or (None, None, None)."""
    import socket
    import json

    try:
        s = socket.create_connection(("127.0.0.1", 2947), timeout=5)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        deadline = time.time() + timeout
        buf = ""
        while time.time() < deadline:
            s.settimeout(max(1, deadline - time.time()))
            try:
                chunk = s.recv(4096).decode("utf-8", errors="ignore")
            except OSError:
                break
            buf += chunk
            for line in buf.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("class") == "TPV" and obj.get("mode", 0) >= 2:
                    lat = obj.get("lat")
                    lon = obj.get("lon")
                    alt = float(obj.get("alt") or 0)
                    if lat is not None and lon is not None:
                        s.close()
                        return lat, lon, alt
            buf = buf.split("\n")[-1]
        s.close()
    except Exception as e:
        print(f"[gps_reader] gpsd read error: {e}")
    return None, None, None


def read_serial_gps(device, timeout=30):
    """Read a GPS fix from a serial NMEA device. Returns (lat, lon, alt) or (None, None, None)."""
    import serial

    def _parse_gga(line):
        parts = line.split(",")
        if len(parts) < 10:
            return None
        try:
            if int(parts[6]) == 0:
                return None
            raw_lat, lat_hem = parts[2], parts[3]
            raw_lon, lon_hem = parts[4], parts[5]
            alt = float(parts[9]) if parts[9] else 0.0
            lat = float(raw_lat[:2]) + float(raw_lat[2:]) / 60.0
            if lat_hem == "S":
                lat = -lat
            lon = float(raw_lon[:3]) + float(raw_lon[3:]) / 60.0
            if lon_hem == "W":
                lon = -lon
            return lat, lon, alt
        except Exception:
            return None

    for baud in (9600, 4800, 115200):
        try:
            s = serial.Serial(device, baud, timeout=2)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    line = s.readline().decode("ascii", errors="ignore").strip()
                except Exception:
                    continue
                if line.startswith(("$GPGGA", "$GNGGA")):
                    result = _parse_gga(line)
                    if result:
                        s.close()
                        return result
            s.close()
        except Exception:
            pass
    return None, None, None
