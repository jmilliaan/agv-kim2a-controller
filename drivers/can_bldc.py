"""
drivers/can_bldc.py — BLVD-KRD CiA-402 wheel-drive actuator
===========================================================
Owns the single shared ``canopen.Network`` (one CANable2 / slcan adapter,
500 kbit/s) and the two BLVD-KRD drives (left = node 1, right = node 2).
Consumes ``state.motor_queue`` and writes CiA-402 *Target velocity* (0x60FF);
reads back *Velocity actual value* (0x606C) and *Statusword* (0x6041) for
telemetry and fault detection.

The SICK MLS magnetic sensor shares this same bus (see drivers/can_mls.py):
``CANReader`` does NOT open slcan a second time — it subscribes to the
``Network`` this driver owns (keyed on SENSOR_COB_ID). Bus ownership lives here.

motor_queue protocol (tuples):
    ("left",  rpm)   — set left  wheel logical Target velocity (signed; + = forward)
    ("right", rpm)   — set right wheel logical Target velocity
    ("brake", True)  — Cat-1 stop: zero both + CiA-402 Quick stop (e-brake holds)
    ("brake", False) — release the brake / re-enable OPERATION ENABLED
A velocity command also implicitly releases a prior brake (mirrors the legacy
"next drive command clears the brake coil" behaviour).

Golden rule: every blocking canopen call (SDO/NMT/402 transition, connect)
runs through ``loop.run_in_executor`` so the asyncio loop is never blocked.
"""

import asyncio
import logging
import os
import time

import canopen

import config

logger = logging.getLogger(__name__)

# Object dictionary indices (BLVD-KRD_CANopen_V400.eds, CiA-402).
OBJ_STATUSWORD       = 0x6041
OBJ_MODES_OF_OP      = 0x6060
OBJ_VELOCITY_ACTUAL  = 0x606C
OBJ_TARGET_VELOCITY  = 0x60FF
OBJ_PROFILE_ACCEL    = 0x6083
OBJ_PROFILE_DECEL    = 0x6084
OBJ_QUICKSTOP_DECEL  = 0x6085

MODE_PROFILE_VELOCITY = 3

# Statusword bit 3 = Fault (CiA-402).
_SW_FAULT_BIT = 0x0008

# EDS lives under can_bldc/ at the package root (not the cwd).
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _eds_path() -> str:
    return os.path.join(_PKG_ROOT, "can_bldc", config.MOTOR_CAN["EDS"])


class CANMotorDriver:
    """Two-wheel BLVD-KRD CiA-402 drive over a shared CANopen network."""

    def __init__(self):
        mc = config.MOTOR_CAN
        self._channel   = mc.get("CHANNEL")
        self._bitrate   = int(mc.get("BITRATE", 500_000))
        self._tty_baud  = int(mc.get("TTY_BAUDRATE", 3_000_000))
        self._accel     = int(mc.get("PROFILE_ACCEL", 2000))
        self._decel     = int(mc.get("PROFILE_DECEL", 2000))
        self._qs_decel  = int(mc.get("QUICKSTOP_DECEL", 2000))
        self._max_rpm   = int(config.MOTOR_MAX_RPM)
        # logical side → {node_id, invert}
        self._sides     = {"left": mc["left"], "right": mc["right"]}
        self._loop_dt   = max(0.02, float(config.DT))

        self.network = None                 # canopen.Network, owned here
        self._nodes  = {}                   # side → canopen.BaseNode402
        self._targets = {"left": 0, "right": 0}
        self._braked  = False
        # Set once the bus is up so CANReader can attach to the shared network.
        self.ready = asyncio.Event()

    # ── Blocking bring-up (runs in executor) ──────────────────────────────────

    def _connect(self):
        """Open the bus, add both drives, walk each to OPERATION ENABLED."""
        channel = self._channel or _find_canable_port()
        if channel is None:
            raise RuntimeError("CANable2 adapter not found and no CHANNEL configured")

        net = canopen.Network()
        net.connect(interface="slcan", channel=channel,
                    bitrate=self._bitrate, ttyBaudrate=self._tty_baud)

        eds = _eds_path()
        for side, cfg in self._sides.items():
            node = canopen.BaseNode402(int(cfg["node_id"]), eds)
            net.add_node(node)
            self._nodes[side] = node

        self.network = net
        for side, node in self._nodes.items():
            self._bring_up_node(node)
            logger.info("BLVD-KRD %s (node %d) → OPERATION ENABLED",
                        side, node.id)

    def _bring_up_node(self, node):
        node.nmt.state = "PRE-OPERATIONAL"
        time.sleep(0.3)
        node.nmt.state = "OPERATIONAL"
        time.sleep(0.3)

        if node.state == "FAULT":
            node.fault_reset()
            time.sleep(0.5)

        node.state = "READY TO SWITCH ON"
        time.sleep(0.2)
        node.state = "SWITCHED ON"
        time.sleep(0.2)
        node.state = "OPERATION ENABLED"
        time.sleep(0.2)

        node.sdo[OBJ_MODES_OF_OP].raw     = MODE_PROFILE_VELOCITY
        node.sdo[OBJ_PROFILE_ACCEL].raw   = self._accel
        node.sdo[OBJ_PROFILE_DECEL].raw   = self._decel
        node.sdo[OBJ_QUICKSTOP_DECEL].raw = self._qs_decel

    # ── Blocking per-cycle IO (runs in executor) ──────────────────────────────

    def _io_cycle(self):
        """Write targets, read back telemetry+status. Returns per-side dict."""
        tel = {}
        for side, node in self._nodes.items():
            invert = bool(self._sides[side].get("invert", False))
            rpm = self._targets[side]
            rpm = max(-self._max_rpm, min(self._max_rpm, int(rpm)))
            if invert:
                rpm = -rpm
            if not self._braked:
                node.sdo[OBJ_TARGET_VELOCITY].raw = rpm

            sw     = node.sdo[OBJ_STATUSWORD].raw
            actual = node.sdo[OBJ_VELOCITY_ACTUAL].raw
            if invert:
                actual = -actual
            tel[side] = {"statusword": sw, "actual": actual,
                         "fault": bool(sw & _SW_FAULT_BIT)}
        return tel

    def _apply_brake(self):
        """Zero both drives and trigger a CiA-402 Quick stop (e-brake holds)."""
        for node in self._nodes.values():
            try:
                node.sdo[OBJ_TARGET_VELOCITY].raw = 0
                node.state = "QUICK STOP ACTIVE"
            except Exception as e:
                logger.error("Quick stop failed on node %d: %s", node.id, e)

    def _release_brake(self):
        """Re-walk both drives back to OPERATION ENABLED after a Quick stop."""
        for node in self._nodes.values():
            try:
                if node.state == "FAULT":
                    node.fault_reset()
                    time.sleep(0.2)
                node.state = "OPERATION ENABLED"
            except Exception as e:
                logger.error("Brake release failed on node %d: %s", node.id, e)

    def _recover_fault(self, side):
        """Reset a faulted drive and re-walk to OPERATION ENABLED."""
        node = self._nodes[side]
        node.fault_reset()
        time.sleep(0.3)
        self._bring_up_node(node)

    def safe_stop(self):
        """Synchronous shutdown: zero both, Quick stop, down to SWITCHED ON,
        then drop the bus. Called from main.shutdown() via run_in_executor."""
        if self.network is None:
            return
        for node in self._nodes.values():
            try:
                node.sdo[OBJ_TARGET_VELOCITY].raw = 0
                node.state = "QUICK STOP ACTIVE"
                time.sleep(0.1)
                node.state = "SWITCHED ON"
            except Exception as e:
                logger.error("safe_stop error on node %d: %s", node.id, e)
        try:
            self.network.disconnect()
        except Exception as e:
            logger.error("Network disconnect error: %s", e)

    # ── Async task ─────────────────────────────────────────────────────────────

    async def run(self, state):
        loop = asyncio.get_running_loop()

        while self.network is None:
            try:
                await loop.run_in_executor(None, self._connect)
            except Exception as e:
                logger.error("CAN motor bring-up failed: %s — retrying in 2s", e)
                await asyncio.sleep(2)

        self.ready.set()
        logger.info("CANMotorDriver online — both wheels on CANopen")

        while True:
            # Drain motor_queue keep-latest per side; act on control messages.
            while not state.motor_queue.empty():
                key, val = state.motor_queue.get_nowait()
                if key == "brake":
                    if val:
                        self._braked = True
                        await loop.run_in_executor(None, self._apply_brake)
                    elif self._braked:
                        self._braked = False
                        await loop.run_in_executor(None, self._release_brake)
                else:  # "left" / "right"
                    if self._braked:
                        # A fresh velocity command releases the brake (legacy semantics).
                        self._braked = False
                        await loop.run_in_executor(None, self._release_brake)
                    self._targets[key] = val

            try:
                tel = await loop.run_in_executor(None, self._io_cycle)
            except Exception as e:
                logger.error("CAN motor IO error: %s", e)
                await asyncio.sleep(self._loop_dt)
                continue

            state.motor_actual_rpm = {s: tel[s]["actual"]     for s in tel}
            state.motor_statusword = {s: tel[s]["statusword"] for s in tel}

            faulted = [s for s in tel if tel[s]["fault"]]
            if faulted:
                if state.motor_fault != faulted[0]:
                    logger.error("BLVD-KRD drive FAULT on %s — attempting recovery",
                                 ", ".join(faulted))
                    state.log_event("ERROR",
                                    f"DRIVE FAULT ({', '.join(faulted)}) — Cat-1 stop")
                state.motor_fault = faulted[0]
                for side in faulted:
                    try:
                        await loop.run_in_executor(None, self._recover_fault, side)
                    except Exception as e:
                        logger.error("Fault recovery failed on %s: %s", side, e)
            elif state.motor_fault is not None:
                logger.info("BLVD-KRD drives recovered — clearing motor_fault")
                state.motor_fault = None

            await asyncio.sleep(self._loop_dt)


# ── CANable2 auto-detection (used when CHANNEL is not configured) ──────────────

def _find_canable_port(vid=0x16D0, pid=0x117E):
    import serial.tools.list_ports as list_ports
    for port in list_ports.comports():
        if port.vid == vid and port.pid == pid:
            logger.debug("CANable2 found at: %s", port.device)
            return port.device
    return None
