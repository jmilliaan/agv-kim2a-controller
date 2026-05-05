"""
safety_watchdog.py — Hardware driver health monitor
====================================================
Polls a list of (driver, timeout_s, auto_only) tuples every 50 ms.

  auto_only=False (critical):  DIO/Modbus — loss blocks ALL modes including manual.
                                Emergency button can't be read without it.
  auto_only=True  (sensor):    CAN/RFID   — loss only blocks auto/running/reverse.
                                Manual mode stays operational.

Sets state.system_error (critical) or state.sensor_error (auto-only) accordingly.
DO/AO writer, SLMP handler are output-only and are not monitored here.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def safety_watchdog(state, watched: list):
    """
    watched: list of (driver, timeout_s) or (driver, timeout_s, auto_only) tuples.
    auto_only defaults to False when omitted (backwards-compatible).
    """
    while True:
        now = time.time()
        critical_fault = None
        sensor_fault   = None

        for entry in watched:
            driver, timeout_s = entry[0], entry[1]
            auto_only         = entry[2] if len(entry) > 2 else False
            h = driver.get_health()
            if (now - h["last_rx"]) > timeout_s:
                if auto_only:
                    sensor_fault = h["detail"]
                else:
                    critical_fault = h["detail"]

        # ── Critical fault (DIO lost) — zero outputs, block all modes ────────
        if critical_fault is not None:
            if not state.system_error:
                logger.error("WATCHDOG: critical driver '%s' lost — forcing idle", critical_fault)
                state.log_event("ERROR", f"WATCHDOG: critical driver lost: {critical_fault} — AGV stopped")
                state.system_error_detail = critical_fault
                state.system_error = True
                await state.ao_queue.put((0, 0.0))
                await state.ao_queue.put((1, 0.0))
        else:
            if state.system_error:
                logger.info("WATCHDOG: critical driver recovered — clearing system_error")
                state.log_event("INFO", "WATCHDOG: critical driver recovered")
                state.system_error_detail = ""
                state.system_error = False

        # ── Sensor fault (CAN/RFID lost) — block auto only, manual stays up ──
        if sensor_fault is not None:
            if not state.sensor_error:
                logger.warning("WATCHDOG: sensor driver '%s' lost — auto mode blocked", sensor_fault)
                state.log_event("WARNING", f"WATCHDOG: sensor lost: {sensor_fault} — manual still available")
                state.sensor_error_detail = sensor_fault
                state.sensor_error = True
        else:
            if state.sensor_error:
                logger.info("WATCHDOG: sensor driver recovered — clearing sensor_error")
                state.log_event("INFO", "WATCHDOG: sensor driver recovered")
                state.sensor_error_detail = ""
                state.sensor_error = False

        await asyncio.sleep(0.05)
