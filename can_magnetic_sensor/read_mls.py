"""
read_mls.py — minimal SICK MLS reader (proof of concept)
=========================================================
Reads TPDO1 from the SICK MLS magnetic line sensor and prints it.
No encoder, no threads, no classes. Just a receive loop.

Bus:  PC (CANable) ── SICK MLS  Node 0x0A  COB-ID 0x18A  125 kbit/s

  pip install python-can

TPDO1 payload (0x18A), 8 bytes:
  B0-B1  LCP1   INT16 LE   Line Center Point 1 [mm]  (0x7FFF = not detected)
  B2-B3  LCP2   INT16 LE   Line Center Point 2 [mm]
  B4-B5  LCP3   INT16 LE   Line Center Point 3 [mm]
  B6     #LCP   UINT8      bits[2:0]=track count, bits[7:3]=marker
  B7     Status UINT8      bit0=line_good, bits[3:1]=level, bit7=event
"""

import struct
import can  # pip install python-can

PORT        = "COM12"
BITRATE     = 125_000
MLS_NODE    = 0x0A
MLS_ID      = 0x180 + MLS_NODE   # 0x18A
LCP_INVALID = 0x7FFF


def fmt_lcp(v):
    return "  ---" if v == LCP_INVALID else f"{v:+5}"


def main():
    bus = can.interface.Bus(
        interface="slcan",
        channel=PORT,
        bitrate=BITRATE,
        sleep_after_open=2.0,
    )

    # NMT Start Remote Node (broadcast) -> nodes go Operational, TPDOs start
    bus.send(can.Message(arbitration_id=0x000, data=[0x01, 0x00], is_extended_id=False))

    print(f"Reading SICK MLS on {PORT} (COB-ID 0x{MLS_ID:03X}). Ctrl+C to stop.\n")

    try:
        while True:
            msg = bus.recv(timeout=1.0)
            if msg is None or msg.arbitration_id != MLS_ID or len(msg.data) != 8:
                continue

            lcp1, lcp2, lcp3 = struct.unpack_from("<hhh", msg.data, 0)
            tracks = msg.data[6] & 0x07
            marker = (msg.data[6] >> 3) & 0x1F
            status = msg.data[7]
            line_good = bool(status & 0x01)
            level     = (status >> 1) & 0x07

            print(
                f"LCP1={fmt_lcp(lcp1)} LCP2={fmt_lcp(lcp2)} LCP3={fmt_lcp(lcp3)} mm"
                f" | tracks={tracks} marker={marker} lvl={level}"
                f" {'[LINE OK]' if line_good else '[no line]'}"
            )
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
