"""
debug.py — AGV Hardware Debug Monitor
======================================
Reads and prints live data from all hardware subsystems directly to terminal.
No GUI. No control logic. Read-only except for the initial RFID handshake.

Run from the same directory as config.py:
    python debug.py

Toggle what gets printed using the SHOW_* flags below.
Adjust polling intervals with the INTERVAL_* values (in seconds).
To monitor only specific DI or DO channels, edit the WATCH_DI and WATCH_DO lists.
"""

import asyncio
import binascii
import socket
import struct
import time

import can
import serial.tools.list_ports as list_ports
from pymodbus.client import AsyncModbusTcpClient

import config
from drivers.can_mls import parse_tpdo1   # SICK MLS TPDO1 decoder

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — edit these to control what gets printed
# ══════════════════════════════════════════════════════════════════════════════

# ── Channel filters ───────────────────────────────────────────────────────────
# Set to None to print ALL channels, or provide a list of channel indices
# to print only those. Examples:
#   WATCH_DI = None          → print all 16 DI channels
#   WATCH_DI = [0, 1, 3, 10] → print only DI0, DI1, DI3, DI10
#   WATCH_DO = [0, 1, 2, 3, 4, 5] → print only DO0–DO5 (motor direction channels)
WATCH_DI = None
WATCH_DO = None

# ── Subsystem enable flags ────────────────────────────────────────────────────
SHOW_DI   = 1   # Digital Inputs  (read from DIO module)
SHOW_DO   = 0   # Digital Outputs (readback from DIO module)
SHOW_CAN  = 0   # SICK MLS magnetic sensor (LCP steering mm, tape_detected, tracks, marker)
SHOW_RFID = 0   # RFID reader (raw tag hex, decimal value)

INTERVAL_DI = 0.1    # 10 Hz
INTERVAL_DO = 0.2    # 5 Hz  (outputs change slowly; no need to hammer the module)

COMPACT_DI = False
COMPACT_DO = False

SHOW_TIMESTAMP = True

# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def ts():
    """Return a timestamp prefix string if SHOW_TIMESTAMP is enabled."""
    if SHOW_TIMESTAMP:
        return f"[{time.strftime('%H:%M:%S')}] "
    return ""


def _channel_label(prefix, index, named_channels: dict):
    """
    Build a channel label like 'DI04' with an optional semantic name appended.
    named_channels maps index → name string, e.g. {10: 'EMERGENCY', 4: 'FWD'}.
    """
    name = named_channels.get(index)
    label = f"{prefix}{index:02d}"
    if name:
        label += f"({name})"
    return label


# Named channel maps — derived from config so they stay in sync with parameters.json
DI_NAMES = {
    config.DI_EMERGENCY: "EMERGENCY",
    config.DI_FWD:       "FWD",
    config.DI_REV:       "REV",
    config.DI_LEFT:      "LEFT",
    config.DI_RIGHT:     "RIGHT",
}

# Wheel drive is on CANopen now — DO coils carry only pusher + horn.
DO_NAMES = {
    8:  "PUSHER_EXT_1",
    9:  "PUSHER_RET_1",
    10: "PUSHER_RET_2",
    11: "PUSHER_EXT_2",
    12: "REGULAR_HORN",
    13: "ALARM_HORN",
}


def _filter(indices, watch_list):
    """Return only the indices present in watch_list, or all if watch_list is None."""
    if watch_list is None:
        return indices
    return [i for i in indices if i in watch_list]


# ══════════════════════════════════════════════════════════════════════════════
#  SUBSYSTEM COROUTINES
# ══════════════════════════════════════════════════════════════════════════════

async def monitor_di():
    """Poll digital inputs from the DIO Modbus module and print to terminal."""
    client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
    print(f"{ts()}[DI ] Connecting to DIO module at {config.DIO_IP}:{config.MODBUS_PORT}...")

    while True:
        try:
            if not client.connected:
                await client.connect()
                if not client.connected:
                    print(f"{ts()}[DI ] Not connected. Retrying...")
                    await asyncio.sleep(2)
                    continue

            result = await client.read_discrete_inputs(
                address=config.DI_BASE,
                count=config.NUM_DI,
                device_id=config.DEVICE_ID
            )

            if result.isError():
                print(f"{ts()}[DI ] Modbus read error.")
                await asyncio.sleep(INTERVAL_DI)
                continue

            bits = result.bits[:config.NUM_DI]
            channels = _filter(range(config.NUM_DI), WATCH_DI)

            if COMPACT_DI:
                bitstr = ''.join('1' if bits[i] else '0' for i in reversed(range(config.NUM_DI)))
                print(f"{ts()}[DI ] {bitstr}")
            else:
                parts = []
                for i in channels:
                    label = _channel_label("DI", i, DI_NAMES)
                    state = "ON " if bits[i] else "OFF"
                    parts.append(f"{label}={state}")
                print(f"{ts()}[DI ] " + "  ".join(parts))

        except Exception as e:
            print(f"{ts()}[DI ] Exception: {e}")
            client.close()

        await asyncio.sleep(INTERVAL_DI)


async def monitor_do():
    """Readback digital output coil states from the DIO Modbus module."""
    client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
    print(f"{ts()}[DO ] Connecting to DIO module at {config.DIO_IP}:{config.MODBUS_PORT}...")

    while True:
        try:
            if not client.connected:
                await client.connect()
                if not client.connected:
                    print(f"{ts()}[DO ] Not connected. Retrying...")
                    await asyncio.sleep(2)
                    continue

            result = await client.read_coils(
                address=config.DO_BASE,
                count=config.NUM_DO,
                device_id=config.DEVICE_ID
            )

            if result.isError():
                print(f"{ts()}[DO ] Modbus read error.")
                await asyncio.sleep(INTERVAL_DO)
                continue

            bits = result.bits[:config.NUM_DO]
            channels = _filter(range(config.NUM_DO), WATCH_DO)

            if COMPACT_DO:
                bitstr = ''.join('1' if bits[i] else '0' for i in reversed(range(config.NUM_DO)))
                print(f"{ts()}[DO ] {bitstr}")
            else:
                parts = []
                for i in channels:
                    label = _channel_label("DO", i, DO_NAMES)
                    state = "ON " if bits[i] else "OFF"
                    parts.append(f"{label}={state}")
                print(f"{ts()}[DO ] " + "  ".join(parts))

        except Exception as e:
            print(f"{ts()}[DO ] Exception: {e}")
            client.close()

        await asyncio.sleep(INTERVAL_DO)


def _find_canable_port(vid=0x16D0, pid=0x117E):
    """Scan serial ports for the CANable2 adapter by USB VID/PID."""
    for port in list_ports.comports():
        if port.vid == vid and port.pid == pid:
            return port.device
    return None


async def monitor_can():
    loop = asyncio.get_event_loop()

    while True:
        channel = _find_canable_port()
        if channel is None:
            print(f"{ts()}[CAN] CANable2 not found. Retrying in 2s...")
            await asyncio.sleep(2)
            continue

        bus = None
        msg_queue = asyncio.Queue()

        def _reader_thread():
            try:
                while True:
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    loop.call_soon_threadsafe(msg_queue.put_nowait, msg)
            except Exception as e:
                loop.call_soon_threadsafe(msg_queue.put_nowait, e)

        try:
            bus = can.interface.Bus(
                channel=channel,
                interface="slcan",
                bitrate=500_000,
                ttyBaudrate=3_000_000
            )
            bus.send(can.Message(
                arbitration_id=0x000,
                data=[0x01, config.CAN_NODE_ID],
                is_extended_id=False
            ))
            print(f"{ts()}[CAN] Connected on {channel}. NMT sent. Waiting for frames...")

            import threading
            t = threading.Thread(target=_reader_thread, daemon=True)
            t.start()

            while True:
                item = await msg_queue.get()

                if isinstance(item, Exception):
                    raise item

                msg = item
                if msg.arbitration_id != config.SENSOR_COB_ID:
                    continue

                # SICK MLS TPDO1 (8 bytes) — decoded via the shared driver parser.
                frame = parse_tpdo1(msg.data, config.STEERING_LCP, config.LCP_INVALID)
                if frame is None:
                    continue

                tape_str  = "DETECTED  " if frame["tape_detected"] else "NOT DETECT"
                marker_str = f" marker={frame['marker_code']}" if frame["marker_present"] else ""

                print(
                    f"{ts()}[CAN] "
                    f"LCP(steer)={str(frame['left_mm']):>5}mm  2nd={str(frame['right_mm']):>5}mm  "
                    f"Tape={tape_str}  tracks={frame['track_count']} lvl={frame['level']}{marker_str}"
                )

        except Exception as e:
            print(f"{ts()}[CAN] Error: {e}. Retrying in 2s...")
        finally:
            if bus:
                bus.shutdown()
            await asyncio.sleep(2)


async def monitor_rfid():
    """
    Connect to the RFID reader, send the activation command,
    and print every decoded tag frame as it arrives.
    """
    loop = asyncio.get_event_loop()
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            print(f"{ts()}[RFID] Connecting to {config.RFID_IP}:{config.RFID_PORT}...")
            await loop.sock_connect(sock, (config.RFID_IP, config.RFID_PORT))

            await loop.sock_sendall(sock, config.RFID_INIT_CMD)
            print(f"{ts()}[RFID] Connected. Activation command sent. Waiting for tags...")

            while True:
                data = await loop.sock_recv(sock, 1024)
                if not data:
                    print(f"{ts()}[RFID] Connection closed by reader.")
                    break

                hex_data = binascii.hexlify(data).decode().upper()

                # Split on the CF start-of-frame delimiter and process every frame
                # that arrived in this recv() call.
                frames_found = 0
                for part in hex_data.split("CF")[1:]:
                    frame = "CF" + part
                    if len(frame) < 34:
                        # Incomplete frame fragment — discard silently.
                        continue
                    tag = frame[28:34]
                    tag_dec = int(tag, 16)
                    print(
                        f"{ts()}[RFID] Tag detected → hex={tag}  dec={tag_dec}  "
                        f"raw_frame={frame[:34]}"
                    )
                    frames_found += 1

                if frames_found == 0:
                    # recv() returned data but no complete frame — likely a
                    # partial or non-tag protocol message.
                    print(f"{ts()}[RFID] Received data but no complete frame — raw hex: {hex_data[:64]}")

        except Exception as e:
            print(f"{ts()}[RFID] Error: {e}. Retrying in 2s...")
        finally:
            sock.close()

        await asyncio.sleep(2)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print(" AGV Hardware Debug Monitor")
    print("=" * 60)
    print(f" DIO module  : {config.DIO_IP}:{config.MODBUS_PORT}")
    print(f" RFID reader : {config.RFID_IP}:{config.RFID_PORT}")
    print(f" CAN COB-ID  : {config.SENSOR_COB_ID} (0x{config.SENSOR_COB_ID:03X})")
    print("-" * 60)
    print(f" SHOW_DI={SHOW_DI}  SHOW_DO={SHOW_DO}  "
          f"SHOW_CAN={SHOW_CAN}  SHOW_RFID={SHOW_RFID}")
    print(f" WATCH_DI={WATCH_DI}  WATCH_DO={WATCH_DO}")
    print("=" * 60)
    print(" Press Ctrl+C to stop.")
    print()

    tasks = []
    if SHOW_DI:
        tasks.append(asyncio.create_task(monitor_di()))
    if SHOW_DO:
        tasks.append(asyncio.create_task(monitor_do()))
    if SHOW_CAN:
        tasks.append(asyncio.create_task(monitor_can()))
    if SHOW_RFID:
        tasks.append(asyncio.create_task(monitor_rfid()))

    if not tasks:
        print("All SHOW_* flags are False. Nothing to monitor. Exiting.")
        return

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDebug monitor stopped.")