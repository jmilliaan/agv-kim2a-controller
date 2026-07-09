#!/usr/bin/env python3
"""
check_encoder.py — quick "is the CAN encoder detected?" probe.

Opens the CANable, sends the NMT start, and waits for a few TPDO1 frames from the
encoder. Prints the raw count and exits 0 if detected, 1 if not.

Requires the CANable to be free — stop the controller first:
    sudo systemctl stop agv-controller.service
    python3 check_encoder.py
"""

import sys
import struct
import can

from drivers.can_encoder import (
    _find_canable_port, BITRATE, NODE_ID, NMT_COB_ID, TPDO1_COB_ID,
)

TIMEOUT_S = 5.0
WANT_FRAMES = 3

def main():
    port = _find_canable_port()
    print(f"CANable port : {port}")
    try:
        bus = can.interface.Bus(channel=port, interface="slcan",
                                bitrate=BITRATE, ttyBaudrate=3_000_000)
    except Exception as e:
        print(f"FAIL: could not open bus: {e}")
        return 1

    try:
        bus.send(can.Message(arbitration_id=NMT_COB_ID,
                             data=[0x01, NODE_ID], is_extended_id=False))
        print(f"Listening for TPDO1 (COB-ID 0x{TPDO1_COB_ID:03X}) for {TIMEOUT_S:.0f}s...")
        seen = 0
        while seen < WANT_FRAMES:
            msg = bus.recv(timeout=TIMEOUT_S)
            if msg is None:
                break
            if msg.arbitration_id != TPDO1_COB_ID:
                continue
            count = (struct.unpack_from("<I", msg.data, 0)[0] if len(msg.data) >= 4
                     else struct.unpack_from("<H", msg.data, 0)[0])
            seen += 1
            print(f"  frame {seen}: count = {count}")

        if seen:
            print(f"OK: encoder detected (node {NODE_ID}).")
            return 0
        print("FAIL: no encoder frames received — check power, wiring, node ID, bitrate.")
        return 1
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
