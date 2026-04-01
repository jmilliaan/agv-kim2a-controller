import asyncio
import logging
import time
import pymcprotocol

import config
from drivers.base import ActuatorDriver

logger = logging.getLogger(__name__)

_NUM_SEQ_BITS = 17


def _plc_sync_cycle(plc, state) -> None:
    """
    Single synchronous read/write cycle executed in a thread-pool worker so
    the asyncio event loop is never blocked.
    """
    mode = state.current_mode

    # ── Build M2000–M2005 status vector ──────────────────────────────────────
    status = [
        int(mode not in (None, "emergency")),          # M2000  AGV.READY
        int(state.emergency_active),                   # M2001  AGV.EMERGENCY
        int(state.emergency_active),                   # M2002  AGV.REQ TROLLEY EMERGENCY
        int(mode == "manual"),                         # M2003  AGV.MANUAL
        int(mode in ("armed", "running", "reverse")),  # M2004  AGV.AUTO
        int(mode in ("running", "reverse")),           # M2005  AGV.RUNNING
    ]
    plc.batchwrite_bitunits(headdevice="M2000", values=status)

    # ── Build M2010–M2026 one-hot sequence vector ─────────────────────────────
    seq = [0] * _NUM_SEQ_BITS
    req = state.plc_sequence_request
    if req is not None and 0 <= req < _NUM_SEQ_BITS:
        seq[req] = 1
    plc.batchwrite_bitunits(headdevice="M2010", values=seq)

    # ── Read M0–M4 PLC input bits ─────────────────────────────────────────────
    values = plc.batchread_bitunits(headdevice="M0", readsize=5)
    state.plc_inputs = {
        "M0_PB_START_AUTO": bool(values[0]),
        "M1_MODE_MAN":      bool(values[1]),
        "M2_MODE_AUTO":     bool(values[2]),
        "M3_EMERGENCY":     bool(values[3]),
        "M4_MASTER_ON":     bool(values[4]),
    }

    # ── Read M2040–M2056 sequence complete flags ──────────────────────────────
    complete = plc.batchread_bitunits(headdevice="M2040", readsize=_NUM_SEQ_BITS)
    state.plc_sequence_complete = [bool(v) for v in complete]

    # ── Auto-clear sequence request pulse ────────────────────────────────────
    req = state.plc_sequence_request
    if req is not None:
        if time.time() >= state.plc_sequence_pulse_expire:
            state.plc_sequence_request = None


class SLMPDriver(ActuatorDriver):

    async def run(self, state) -> None:
        loop = asyncio.get_running_loop()

        while True:
            plc = pymcprotocol.Type3E(plctype="Q")
            plc.setaccessopt(commtype="binary")

            try:
                await loop.run_in_executor(
                    None, lambda: plc.connect(config.SLMP_IP, config.SLMP_PORT)
                )
                logger.info("[SLMP] Connected to PLC at %s:%s", config.SLMP_IP, config.SLMP_PORT)

                while True:
                    await loop.run_in_executor(None, _plc_sync_cycle, plc, state)
                    await asyncio.sleep(0.02)

            except Exception as e:
                logger.error("[SLMP] Error: %s. Retrying in 2s...", e)

            finally:
                try:
                    plc.close()
                except Exception:
                    pass

            await asyncio.sleep(2)


# ── Module-level shim ────────────────────────────────────────────────────────

_instance = SLMPDriver()

async def slmp_handler(state):
    await _instance.run(state)
