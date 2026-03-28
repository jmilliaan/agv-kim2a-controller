"""
motor_test.py — AGV Motor Direction Verification
==================================================
Standalone synchronous script. No asyncio, no queues, no motion.py.
Directly writes Modbus coils and registers to verify motor wiring.

Run from the same directory as config.py:
    python3 motor_test.py

PURPOSE
-------
Moves the AGV for a short burst to confirm motor direction wiring.
Left motor is commanded FORWARD, right motor is commanded REVERSE.
Watch what physically happens:

  - If the AGV moves FORWARD  → left is wired correctly, right is inverted  (expected)
  - If the AGV SPINS in place → both motors are spinning the same direction, check wiring
  - If the AGV moves BACKWARD → both motors are inverted

After confirming, edit the REVERSED flags below to match your hardware
and re-run to verify the corrected forward motion.

SAFETY
------
Keep hands and feet clear. The AGV will move.
The stop sequence runs in a finally block — Ctrl+C will still stop the motors.
"""

import time
import sys
from pymodbus.client import ModbusTcpClient

# ── Load parameters directly — no config.py dependency ───────────────────────
import json, os
_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_dir, "parameters.json")) as f:
    _p = json.load(f)

DIO_IP     = _p["networking"]["DIO_IP"]
AO_IP      = _p["networking"]["AO_IP"]
PORT       = _p["networking"]["MODBUS_PORT"]
DEVICE_ID  = _p["networking"]["DEVICE_ID"]
DO_BASE    = _p["io_mapping"]["DO_BASE"]
AO_BASE    = _p["io_mapping"]["AO_BASE"]
V_RANGE    = _p["hardware"]["V_RANGE"]
DAC_RES    = _p["hardware"]["DAC_RES"]

# ══════════════════════════════════════════════════════════════════════════════
#  TEST CONFIGURATION — edit these
# ══════════════════════════════════════════════════════════════════════════════

# DO channel assignments (Y-numbers from the hardware label)
CH_L_FWD = 0   # Y00
CH_L_REV = 1   # Y01
CH_L_BRK = 2   # Y02
CH_R_FWD = 3   # Y03
CH_R_REV = 4   # Y04
CH_R_BRK = 5   # Y05

# AO channel assignments
CH_AO_LEFT  = 0
CH_AO_RIGHT = 1

# Test voltage — keep very low for first verification run
TEST_VOLTAGE = 0.5   # volts

# Duration the motors run before auto-stop
RUN_DURATION = 1.0   # seconds

# Motor polarity overrides.
# Set to True if that motor's FWD/REV signals need to be swapped.
# After first run, edit these to fix whichever side spins the wrong way.
LEFT_REVERSED  = False
RIGHT_REVERSED = False

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def volts_to_dac(v):
    return int(v / V_RANGE * DAC_RES)


def write_coil(client, channel, value):
    result = client.write_coil(
        address=DO_BASE + channel,
        value=bool(value),
        slave=DEVICE_ID
    )
    state = "ON " if value else "OFF"
    print(f"  DO{channel:02d} (Y{channel:02d}) = {state}  {'OK' if not result.isError() else 'ERROR'}")
    if result.isError():
        print(f"  !! Modbus error on DO{channel}: {result}")


def write_ao(client, channel, volts):
    dac = volts_to_dac(volts)
    result = client.write_register(
        address=AO_BASE + channel,
        value=dac,
        slave=DEVICE_ID
    )
    print(f"  AO{channel} = {volts:.3f}V (DAC={dac})  {'OK' if not result.isError() else 'ERROR'}")
    if result.isError():
        print(f"  !! Modbus error on AO{channel}: {result}")


def stop_all(dio, ao):
    print("\n[STOP] Zeroing AO channels...")
    write_ao(ao,  CH_AO_LEFT,  0.0)
    write_ao(ao,  CH_AO_RIGHT, 0.0)

    print("[STOP] Releasing all DO channels...")
    for ch in [CH_L_FWD, CH_L_REV, CH_L_BRK, CH_R_FWD, CH_R_REV, CH_R_BRK]:
        write_coil(dio, ch, False)

    print("[STOP] Done.")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 56)
    print(" AGV Motor Direction Test")
    print("=" * 56)
    print(f" DIO module : {DIO_IP}:{PORT}")
    print(f" AO module  : {AO_IP}:{PORT}")
    print(f" Test cmd   : LEFT=FWD  RIGHT=REV")
    print(f" Voltage    : {TEST_VOLTAGE}V")
    print(f" Duration   : {RUN_DURATION}s")
    print(f" L_REVERSED : {LEFT_REVERSED}  R_REVERSED : {RIGHT_REVERSED}")
    print("=" * 56)
    print()

    # ── Connect ───────────────────────────────────────────────────────────────
    print("[INIT] Connecting to DIO module...")
    dio = ModbusTcpClient(DIO_IP, port=PORT)
    if not dio.connect():
        sys.exit(f"FATAL: Could not connect to DIO module at {DIO_IP}:{PORT}")
    print(f"[INIT] DIO connected.")

    print("[INIT] Connecting to AO module...")
    ao = ModbusTcpClient(AO_IP, port=PORT)
    if not ao.connect():
        dio.close()
        sys.exit(f"FATAL: Could not connect to AO module at {AO_IP}:{PORT}")
    print(f"[INIT] AO connected.")
    print()

    try:

        # # ── Step 2: release brakes ────────────────────────────────────────────
        # print("\n[2/4] Releasing brakes...")
        write_coil(dio, CH_L_BRK, 1)
        write_coil(dio, CH_R_BRK, 1)
        time.sleep(3)   # brief pause for mechanical brake release

        # ── Step 1: ensure everything is off before starting ──────────────────
        print("[1/4] Pre-clearing all motor channels...")
        stop_all(dio, ao)
        time.sleep(0.2)

        # ── Step 2: release brakes ────────────────────────────────────────────
        print("\n[2/4] Releasing brakes...")
        write_coil(dio, CH_L_BRK, False)
        write_coil(dio, CH_R_BRK, False)
        time.sleep(0.1)   # brief pause for mechanical brake release

        # ── Step 3: set direction ─────────────────────────────────────────────
        print("\n[3/4] Setting direction: LEFT=FWD  RIGHT=REV")

        # Left motor forward (respects REVERSED flag)
        l_fwd = not LEFT_REVERSED
        write_coil(dio, CH_L_FWD, l_fwd)
        # write_coil(dio, CH_L_REV, not l_fwd)

        # Right motor reverse (respects REVERSED flag)
        r_rev = not RIGHT_REVERSED
        write_coil(dio, CH_R_FWD, r_rev)       # if not reversed: FWD=True means physical reverse
        # write_coil(dio, CH_R_REV, not r_rev)

        # ── Step 4: apply voltage and run ─────────────────────────────────────
        print(f"\n[4/4] Applying {TEST_VOLTAGE}V — running for {RUN_DURATION}s...")
        write_ao(ao, CH_AO_LEFT,  TEST_VOLTAGE)
        write_ao(ao, CH_AO_RIGHT, TEST_VOLTAGE)

        print(f"\n  >>> MOTORS RUNNING — watch direction <<<")
        time.sleep(RUN_DURATION)

    finally:
        # ── Always stop, even on Ctrl+C or exception ─────────────────────────
        stop_all(dio, ao)
        dio.close()
        ao.close()

    print()
    print("=" * 56)
    print(" Test complete. Observe which direction each wheel spun.")
    print()
    print(" Expected result for FORWARD vehicle motion:")
    print("   Left wheel  : spins FORWARD  (drives vehicle forward)")
    print("   Right wheel : spins FORWARD  (drives vehicle forward)")
    print()
    print(" This test commanded LEFT=FWD, RIGHT=REV.")
    print(" If the AGV moved forward, the right motor is wired")
    print(" with inverted polarity — set RIGHT_REVERSED = True")
    print(" and re-run to verify.")
    print("=" * 56)


if __name__ == "__main__":
    main()