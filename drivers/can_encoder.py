"""
can_encoder.py — CANopen absolute encoder reader for wheel-speed calibration.

Used ONLY by the standalone terminal calibration tool (calibration.py); it is NOT
part of the running controller. It owns the CANable USB adapter (the same physical
device the MGS1600 tape sensor normally uses), so the controller service must be
stopped before this runs — see calibration.py for the guard.

Measurement principle
---------------------
A small friction wheel on the encoder shaft is pressed against the AGV drive-wheel
rim (rolling contact). Rim speeds match at the contact point, so:

    v_ground = omega_enc * r_enc          (r_enc = encoder friction-wheel radius)

The AGV wheel diameter does NOT enter this — only the encoder friction-wheel radius
does. ENCODER_WHEEL_DIAMETER_M below is a PLACEHOLDER; measure r_enc on the real rig,
it scales the reported speed linearly.

Encoder framing (single-turn 12-bit, per the operator's reference unit)
----------------------------------------------------------------------
The encoder streams TPDO1 (COB-ID 0x180 + NODE_ID) carrying a little-endian position
count that wraps every REVOLUTION at COUNTS_PER_REV (4096) — NOT a multi-turn total
range. Velocity is the per-frame delta with the wrap corrected at +/- COUNTS_PER_REV.

Caveats (they bound accuracy):
  - Any slip at the friction contact UNDER-reads speed.
  - External friction contact spins the encoder OPPOSITE the AGV wheel -> a sign flip
    only, handled by DIRECTION_SIGN (set so forward driving reads positive).
"""

import asyncio
import logging
import math
import struct
import time

import can
import serial.tools.list_ports as list_ports

logger = logging.getLogger(__name__)

# ── CANable adapter identity (same USB device the MGS1600 uses) ───────────────
_CANABLE_VID = 0x16D0
_CANABLE_PID = 0x117E
_FALLBACK_PORT = "/dev/ttyACM0"

# ── Encoder constants (operator reference unit) ───────────────────────────────
NODE_ID          = 0x01
BITRATE          = 125_000
TPDO1_COB_ID     = 0x180 + NODE_ID          # 0x181
SDO_TX_COB_ID    = 0x600 + NODE_ID          # host -> encoder
SDO_RX_COB_ID    = 0x580 + NODE_ID          # encoder -> host
NMT_COB_ID       = 0x000

COUNTS_PER_REV   = 4096                      # 12-bit; verified via SDO 0x6501 if available
_SDO_COUNTS_INDEX = 0x6501                   # object holding counts/rev

# Friction-wheel geometry — the ONLY term in v_ground. MEASURE r_enc on the rig.
ENCODER_WHEEL_DIAMETER_M = 0.060             # PLACEHOLDER — confirm on hardware
ENCODER_RADIUS_M         = ENCODER_WHEEL_DIAMETER_M / 2.0

# Sign so forward driving reads positive (external contact reverses rotation).
DIRECTION_SIGN   = -1

_TWO_PI = 2.0 * math.pi


def _find_canable_port():
    """Return the CANable serial device by USB VID/PID, or the fallback path."""
    for port in list_ports.comports():
        if port.vid == _CANABLE_VID and port.pid == _CANABLE_PID:
            logger.debug("CANable2 found at: %s", port.device)
            return port.device
    return _FALLBACK_PORT


class EncoderReader:
    """Reads the CANopen absolute encoder and publishes ground speed onto state.

    Publishes as plain attributes on the shared state object:
        state.encoder_count      last raw position count (0..COUNTS_PER_REV-1)
        state.encoder_rpm        friction-wheel rpm (signed, forward positive)
        state.encoder_v          ground speed in m/s (signed, forward positive)
        state.encoder_connected  True once the bus is open and streaming
        state.encoder_last_rx    epoch of last TPDO frame
    """

    def __init__(self):
        self._counts_per_rev = COUNTS_PER_REV

    @property
    def _rad_per_count(self):
        return _TWO_PI / self._counts_per_rev

    @property
    def _half_rev(self):
        return self._counts_per_rev / 2.0

    async def run(self, state):
        while True:
            channel = _find_canable_port()

            bus      = None
            notifier = None
            try:
                loop = asyncio.get_event_loop()
                bus = await loop.run_in_executor(None, lambda: can.interface.Bus(
                    channel=channel,
                    interface="slcan",
                    bitrate=BITRATE,
                    ttyBaudrate=3_000_000))

                # NMT start-all: Pre-Operational -> Operational
                bus.send(can.Message(
                    arbitration_id=NMT_COB_ID,
                    data=[0x01, NODE_ID],
                    is_extended_id=False))

                # Best-effort geometry verify — never fatal.
                self._verify_counts_per_rev(bus)

                reader   = can.AsyncBufferedReader()
                notifier = can.Notifier(bus, [reader])

                logger.info("Encoder CAN connected on %s (node %d, %d cpr)",
                            channel, NODE_ID, self._counts_per_rev)
                state.encoder_connected = True
                state.encoder_last_rx   = time.time()

                prev_count = None
                prev_ts    = None

                async for msg in reader:
                    if msg.arbitration_id != TPDO1_COB_ID:
                        continue
                    if len(msg.data) >= 4:
                        count = struct.unpack_from("<I", msg.data, 0)[0]
                    elif len(msg.data) >= 2:
                        count = struct.unpack_from("<H", msg.data, 0)[0]
                    else:
                        continue

                    now = time.time()
                    state.encoder_count   = count
                    state.encoder_last_rx = now

                    if prev_count is not None and prev_ts is not None:
                        dt = now - prev_ts
                        if dt > 0:
                            delta = count - prev_count
                            # per-revolution wrap correction
                            if   delta >  self._half_rev: delta -= self._counts_per_rev
                            elif delta < -self._half_rev: delta += self._counts_per_rev

                            omega = DIRECTION_SIGN * (delta * self._rad_per_count) / dt
                            state.encoder_rpm = omega * 60.0 / _TWO_PI
                            state.encoder_v   = omega * ENCODER_RADIUS_M

                    prev_count = count
                    prev_ts    = now

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Encoder CAN error/disconnect: %s, retrying in 2s...", e)
                state.encoder_connected = False

            finally:
                if notifier is not None:
                    try: notifier.stop()
                    except Exception: pass
                if bus is not None:
                    try: bus.shutdown()
                    except Exception: pass
                state.encoder_connected = False

            await asyncio.sleep(2)

    def _verify_counts_per_rev(self, bus):
        """Expedited SDO upload of counts/rev (0x6501 sub 0). Best-effort only."""
        try:
            idx = _SDO_COUNTS_INDEX
            bus.send(can.Message(
                arbitration_id=SDO_TX_COB_ID,
                data=[0x40, idx & 0xFF, (idx >> 8) & 0xFF, 0x00, 0, 0, 0, 0],
                is_extended_id=False))
            deadline = time.time() + 0.5
            while time.time() < deadline:
                resp = bus.recv(timeout=0.2)
                if resp is None:
                    continue
                if resp.arbitration_id != SDO_RX_COB_ID:
                    continue
                if resp.data[0] == 0x80:          # SDO abort
                    logger.debug("SDO 0x%04X aborted — keeping fallback %d cpr",
                                 idx, self._counts_per_rev)
                    return
                # expedited data in bytes 4..7 (little-endian); use 4 bytes
                value = struct.unpack_from("<I", resp.data, 4)[0]
                if value > 0:
                    self._counts_per_rev = value
                    logger.info("Encoder counts/rev read via SDO: %d", value)
                return
        except Exception as e:
            logger.debug("SDO geometry read failed (non-fatal): %s", e)
