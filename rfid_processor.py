"""
rfid_processor.py — RFID Command Interpreter
=============================================
Consumes tags from state.rfid_queue and translates them into state flag
changes that auto_mode acts on each control cycle.

Tag → command mapping lives entirely in parameters.json (rfid_commands).
To add a new tag: add an entry to rfid_commands in parameters.json.
To add a new command behaviour: add an elif branch in _execute_command().
No other files need to change.
"""

import asyncio
import logging
import time
import config

logger = logging.getLogger(__name__)


_TROLLEY_SEQ_IGNORE_SECONDS  = 10.0
_TROLLEY_SEQ_APPROACH_TIMEOUT = 15.0   # max time from RFID tag to left marker
_TROLLEY_SEQ_TIMEOUT          = 30.0   # max time waiting for PLC complete flag


async def rfid_processor(state):
    _seq_stop_task    = None
    _seq_ignore_until = {}   # seq_num (int) → epoch time

    async def _sequence_stop_timer():
        await asyncio.sleep(config.SEQUENCE_STOP_DELAY)
        state.sequence_stop = False
        logger.info("[RFID] Sequence stop complete — resuming AUTO.")

    async def _run_trolley_sequence(seq_num: int):
        # ── Ignore window ─────────────────────────────────────────────────────
        ignore_until = _seq_ignore_until.get(seq_num, 0.0)
        remaining    = ignore_until - time.time()
        if remaining > 0:
            logger.debug("[RFID] TROLLEY_SEQUENCE_%02d — ignore window active (%.1fs left). Skipped.",
                         seq_num, remaining)
            return

        # ── Only act in AUTO running mode ─────────────────────────────────────
        if state.current_mode != "running":
            logger.debug("[RFID] TROLLEY_SEQUENCE_%02d — AGV not RUNNING. Ignored.", seq_num)
            return

        # ── Phase 1: slow down and approach to left marker ────────────────────
        logger.info("[RFID] TROLLEY_SEQUENCE_%02d — tag read. Slowing down, waiting for left marker.",
                    seq_num)
        state.speed_mode      = "SLOW"
        state.pending_sequence = seq_num

        deadline = time.time() + _TROLLEY_SEQ_APPROACH_TIMEOUT
        while True:
            if time.time() > deadline:
                logger.warning("[RFID] TROLLEY_SEQUENCE_%02d — timeout waiting for left marker. Resuming HIGH speed.",
                                seq_num)
                state.speed_mode      = "HIGH"
                state.pending_sequence = None
                return
            if state.current_mode != "running":
                logger.info("[RFID] TROLLEY_SEQUENCE_%02d — mode changed during approach. Aborting.", seq_num)
                state.speed_mode      = "HIGH"
                state.pending_sequence = None
                return
            sen = state.latest_sensor
            if sen and sen["left_marker"]:
                break
            await asyncio.sleep(0.02)

        state.pending_sequence = None
        logger.info("[RFID] TROLLEY_SEQUENCE_%02d — left marker detected. Stopping AGV, requesting PLC sequence %d.",
                    seq_num, seq_num)

        # ── Phase 2: stop AGV and send 1-second sequence pulse ────────────────
        state.sequence_stop             = True
        state.plc_sequence_request      = seq_num
        state.plc_sequence_pulse_expire = time.time() + 1.0

        # ── Phase 3: wait for complete flag to go LOW (PLC started) ──────────
        # Necessary when the flag is still HIGH from the previous run.
        deadline = time.time() + 5.0
        while state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                logger.warning("[RFID] TROLLEY_SEQUENCE_%02d — PLC did not clear complete flag in 5s. Aborting.",
                                seq_num)
                state.sequence_stop = False
                state.speed_mode    = "HIGH"
                return
            if state.current_mode != "running":
                logger.info("[RFID] TROLLEY_SEQUENCE_%02d — mode changed during wait (phase 3). Aborting.", seq_num)
                state.sequence_stop = False
                state.speed_mode    = "HIGH"
                return
            await asyncio.sleep(0.1)

        # ── Phase 4: wait for complete flag to go HIGH (PLC finished) ─────────
        deadline = time.time() + _TROLLEY_SEQ_TIMEOUT
        while not state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                logger.warning("[RFID] TROLLEY_SEQUENCE_%02d — timeout waiting for complete flag. Resuming anyway.",
                                seq_num)
                break
            if state.current_mode != "running":
                logger.info("[RFID] TROLLEY_SEQUENCE_%02d — mode changed during wait (phase 4). Aborting.", seq_num)
                state.sequence_stop = False
                state.speed_mode    = "HIGH"
                return
            await asyncio.sleep(0.1)
        else:
            logger.info("[RFID] TROLLEY_SEQUENCE_%02d — complete. Resuming AGV at HIGH speed.", seq_num)

        # ── Resume AGV at HIGH speed and start ignore window ─────────────────
        state.sequence_stop         = False
        state.speed_mode            = "HIGH"
        _seq_ignore_until[seq_num]  = time.time() + _TROLLEY_SEQ_IGNORE_SECONDS
        logger.debug("[RFID] TROLLEY_SEQUENCE_%02d — ignoring tag for %.0fs.",
                     seq_num, _TROLLEY_SEQ_IGNORE_SECONDS)

    async def _execute_command(cmd: str):
        nonlocal _seq_stop_task

        if cmd.startswith("TROLLEY_SEQUENCE_"):
            seq_num = int(cmd.split("_")[2])
            await _run_trolley_sequence(seq_num)

        elif cmd == "RF_CMD_01":
            state.speed_mode = "SLOW"
            logger.info("[RFID] CMD_01 — speed → SLOW (%.2f m/s)", config.AUTO_TARGET_SLOW_SPEED)

        elif cmd == "RF_CMD_02":
            state.speed_mode = "HIGH"
            logger.info("[RFID] CMD_02 — speed → HIGH (%.2f m/s)", config.AUTO_TARGET_HIGH_SPEED)

        elif cmd == "RF_CMD_03":
            state.speed_mode = "EXTRA_SLOW"
            logger.info("[RFID] CMD_03 — speed → EXTRA_SLOW (%.2f m/s)", config.AUTO_TARGET_EXTRA_SLOW_SPEED)

        elif cmd == "RF_CMD_04":
            state.speed_mode = "HIGH"
            logger.info("[RFID] CMD_04 — speed → HIGH (%.2f m/s)", config.AUTO_TARGET_HIGH_SPEED)

        elif cmd == "RF_CMD_05":
            if _seq_stop_task and not _seq_stop_task.done():
                _seq_stop_task.cancel()
                try:
                    await _seq_stop_task
                except asyncio.CancelledError:
                    pass
            state.sequence_stop = True
            logger.info("[RFID] CMD_05 — sequence stop. Resuming in %.1fs.", config.SEQUENCE_STOP_DELAY)
            _seq_stop_task = asyncio.create_task(_sequence_stop_timer())

        elif cmd == "RF_CMD_00":
            logger.warning("[RFID] CMD_00 — home stop [NOT YET IMPLEMENTED]")

        else:
            logger.warning("[RFID] Unknown command '%s' — no action.", cmd)

    logger.info("[RFID Processor] Started.")
    while True:
        tag = await state.rfid_queue.get()
        cmd = config.RFID_COMMANDS.get(tag)
        if cmd is None:
            logger.debug("[RFID] Unrecognised tag: %s — ignored.", tag)
        else:
            await _execute_command(cmd)