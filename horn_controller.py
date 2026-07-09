"""
horn_controller.py — Audible horn controller
=============================================
Drives two DO channels:
  - regular_horn : steady ON while AGV is in auto mode (running / reverse)
  - alarm_horn   : steady ON instead of regular when an alarm is active
                   during auto mode

Both horns are OFF outside of auto modes (manual / armed / emergency / idle).

Alarm conditions (any one triggers the alarm horn):
  - state.system_error    (critical driver lost — DIO)
  - state.sensor_error    (auto-only driver lost — CAN/RFID)
  - state.bumper_active   (impact bumper triggered)
  - lidar inner zone hit  (DI[DI_LIDAR_STOP] is high, when configured)
  - tape lost             (latest CAN frame reports tape_detected=False)

The task only writes to do_queue when the desired channel state changes,
so it does not flood the DO writer.
"""

import asyncio
import logging

import config

logger = logging.getLogger(__name__)

_AUTO_MODES = ("running", "reverse")
_POLL_S     = 0.1   # 100 ms — fast enough for a horn, slow enough to be cheap


def _alarm_active(state) -> bool:
    if state.system_error or state.sensor_error or state.bumper_active:
        return True
    di = state.latest_di
    if (config.DI_LIDAR_STOP is not None
            and di is not None
            and len(di) > config.DI_LIDAR_STOP
            and di[config.DI_LIDAR_STOP]):
        return True
    sen = state.latest_sensor
    if state.current_mode in _AUTO_MODES and sen is not None and not sen.get("tape_detected", True):
        return True
    return False


async def horn_controller(state):
    horn = config.HORN_CHANNELS
    if not horn:
        logger.info("[HORN] No horn_channels configured — controller idle.")
        return

    reg_ch   = horn.get("regular_horn")
    alarm_ch = horn.get("alarm_horn")
    if reg_ch is None and alarm_ch is None:
        logger.warning("[HORN] horn_channels present but no channels mapped — idle.")
        return

    # Track last commanded state so we only enqueue on change.
    last_reg   = None
    last_alarm = None

    logger.info("[HORN] Started. regular=DO%s alarm=DO%s",
                reg_ch, alarm_ch)

    while True:
        # Master gate: operator can silence both horns from the params page,
        # independently of mode / alarm / demo. Forces outputs LOW; the change
        # is propagated through the same enqueue-on-change path below.
        if not state.horn_enabled:
            want_reg, want_alarm = False, False
        else:
            in_auto = state.current_mode in _AUTO_MODES
            if in_auto:
                alarm = _alarm_active(state)
                want_reg, want_alarm = (False, True) if alarm else (True, False)
            else:
                want_reg, want_alarm = False, False

        if reg_ch is not None and want_reg != last_reg:
            await state.do_queue.put((reg_ch, want_reg))
            last_reg = want_reg
        if alarm_ch is not None and want_alarm != last_alarm:
            await state.do_queue.put((alarm_ch, want_alarm))
            last_alarm = want_alarm

        await asyncio.sleep(_POLL_S)
