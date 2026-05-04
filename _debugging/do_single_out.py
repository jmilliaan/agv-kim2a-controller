"""
do_single_out.py — Pull a single DO pin high via Modbus TCP.
Usage:  python do_single_out.py <channel>
Example: python do_single_out.py 4
Ctrl+C to exit — all DO outputs are set back to LOW on exit.
"""

import sys
import asyncio
from pymodbus.client import AsyncModbusTcpClient

HOST      = "192.168.1.31"
PORT      = 502
DEVICE_ID = 1
DO_BASE   = 0
NUM_DO    = 16


async def main(channel: int):
    client = AsyncModbusTcpClient(HOST, port=PORT)
    await client.connect()
    if not client.connected:
        print(f"ERROR: Could not connect to {HOST}:{PORT}")
        return

    print(f"Connected to {HOST}:{PORT}")
    print(f"Setting DO channel {channel} HIGH. Press Ctrl+C to stop.")

    await client.write_coil(address=DO_BASE + channel, value=True, device_id=DEVICE_ID)
    print(f"DO[{channel}] = HIGH")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nClearing all DO outputs...")
        for i in range(NUM_DO):
            await client.write_coil(address=DO_BASE + i, value=False, device_id=DEVICE_ID)
        client.close()
        print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python do_single_out.py <channel>")
        sys.exit(1)

    ch = int(sys.argv[1])
    if not (0 <= ch < NUM_DO):
        print(f"ERROR: channel must be 0–{NUM_DO - 1}")
        sys.exit(1)

    try:
        asyncio.run(main(ch))
    except KeyboardInterrupt:
        pass
