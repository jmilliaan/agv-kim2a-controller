"""
drivers/can_mls.py — SICK MLS / MLSE magnetic line sensor
=========================================================
The SICK MLS is a CANopen device that publishes its line position on TPDO1
(COB-ID 0x180 + node id; factory node 0x0A → 0x18A). It shares the single slcan
adapter with the BLDC wheel drives, so — like the previous MGS1600 reader — it
does NOT open its own bus: it attaches to the shared ``canopen.Network`` owned by
``CANMotorDriver`` and subscribes to the sensor COB-ID.

TPDO1 payload (8 bytes, MLS operating instructions Table 6/7/8):
  B0-1  LCP1   INT16 LE  Line Center Point 1 [mm]   (0x7FFF = not detected)
  B2-3  LCP2   INT16 LE  Line Center Point 2 [mm]
  B4-5  LCP3   INT16 LE  Line Center Point 3 [mm]
  B6    #LCP   UINT8     bits[0:2] = track count, bits[3:7] = marker
                         (bit3 = intro char, bits4-7 = code)
  B7    Status UINT8     bit0 = line good, bits[1:3] = track level,
                         bit4 = sensor flipped, bit5 = polarity,
                         bit6 = reading code, bit7 = event

Steering note: for a *single* detected line the MLS always reports it as **LCP2**
(LCP1 < LCP2 < LCP3). So the PID process variable comes from LCP2. To keep the
existing ``latest_sensor`` schema (PID reads ``sensor["left_mm"]``, dashboard
lamps, tape-loss guards) unchanged, ``left_mm`` carries the selected steering LCP
(LCP2 by default, configurable via ``config.STEERING_LCP``).

Markers: the MLS reports a numeric marker *code*, not the MGS1600 left/right
markers. ``left_marker``/``right_marker`` are kept (always False) for compat; the
code is exposed as ``marker_code`` / ``marker_present`` for the dashboard and any
future coded-marker sequence triggers.

The subscribe callback fires on canopen's notifier thread, so it only does
GIL-safe single-field writes and a thread-safe queue put (call_soon_threadsafe).
"""

import asyncio
import logging
import struct
import time

import config
from drivers.base import SensorDriver

logger = logging.getLogger(__name__)

# TPDO base COB-ID (0x180 + node id) and "no line" sentinel are fixed by the device.
_TPDO1_COB_BASE = 0x180
_DEFAULT_LCP_INVALID = 0x7FFF


def parse_tpdo1(data, steering_lcp: int = 2, lcp_invalid: int = _DEFAULT_LCP_INVALID):
    """Decode an 8-byte MLS TPDO1 payload into the latest_sensor frame dict.

    Pure function (no I/O) so it is unit-testable without a CAN bus. Returns
    None if the payload is not the expected 8 bytes.
    """
    if data is None or len(data) != 8:
        return None

    lcp1, lcp2, lcp3 = struct.unpack_from("<hhh", data, 0)
    nlcp   = data[6]
    status = data[7]

    track_count = nlcp & 0x07
    marker_code = (nlcp >> 3) & 0x1F
    line_good   = bool(status & 0x01)
    level       = (status >> 1) & 0x07

    lcps = {1: lcp1, 2: lcp2, 3: lcp3}
    steer = lcps.get(steering_lcp, lcp2)
    secondary = lcp1 if steering_lcp != 1 else lcp3

    return {
        # ── Steering / status (existing schema — consumers unchanged) ─────────
        "left_mm":        None if steer == lcp_invalid else steer,      # = LCP2 by default
        "right_mm":       None if secondary == lcp_invalid else secondary,
        "tape_detected":  line_good,
        "left_marker":    False,   # MLS has no side markers (coded instead)
        "right_marker":   False,
        "sensor_failure": False,   # no MLS bit; comms loss handled by the watchdog
        # ── MLS-native extras (diagnostics / future coded-marker triggers) ────
        "marker_code":    marker_code,
        "marker_present": marker_code != 0,
        "track_count":    track_count,
        "level":          level,
    }


class CANReader(SensorDriver):

    def __init__(self, motor_driver):
        super().__init__("CAN MLS")
        self._motor = motor_driver   # owns the shared canopen.Network
        self._loop  = None
        self._state = None
        self._steering_lcp = int(getattr(config, "STEERING_LCP", 2))
        self._lcp_invalid  = int(getattr(config, "LCP_INVALID", _DEFAULT_LCP_INVALID))

    async def run(self, state):
        self._loop  = asyncio.get_running_loop()
        self._state = state

        # Wait for CANMotorDriver to bring up the shared bus.
        await self._motor.ready.wait()
        network = self._motor.network

        # NMT "start remote node" for the sensor (mirrors the MLS bring-up POC).
        try:
            network.send_message(0x000, [0x01, config.CAN_NODE_ID])
        except Exception as e:
            logger.warning("MLS NMT start failed: %s", e)

        network.subscribe(config.SENSOR_COB_ID, self._on_frame)
        state.can_last_rx = time.time()
        logger.info("SICK MLS attached to shared CAN bus (COB-ID 0x%03X, LCP%d steering)",
                    config.SENSOR_COB_ID, self._steering_lcp)

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            try:
                network.unsubscribe(config.SENSOR_COB_ID, self._on_frame)
            except Exception:
                pass
            raise

    def _on_frame(self, can_id, data, timestamp):
        """canopen notifier-thread callback. Keep it GIL-safe + non-blocking."""
        frame = parse_tpdo1(data, self._steering_lcp, self._lcp_invalid)
        if frame is None:
            return
        self._state.latest_sensor = frame
        self._state.can_last_rx   = time.time()
        self._record_rx()
        # Hand the frame to the asyncio side thread-safely.
        self._loop.call_soon_threadsafe(self._state.sensor_queue.put_nowait, frame)
