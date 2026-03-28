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
import time
import config


_TROLLEY_SEQ_IGNORE_SECONDS = 10.0
_TROLLEY_SEQ_TIMEOUT       = 30.0


async def rfid_processor(state):
    _seq_stop_task    = None
    _seq_ignore_until = {}   # seq_num (int) → epoch time

    async def _sequence_stop_timer():
        await asyncio.sleep(config.SEQUENCE_STOP_DELAY)
        state.sequence_stop = False
        print("[RFID] Sequence stop complete — resuming AUTO.")

    async def _run_trolley_sequence(seq_num: int):
        # ── Ignore window ─────────────────────────────────────────────────────
        ignore_until = _seq_ignore_until.get(seq_num, 0.0)
        remaining    = ignore_until - time.time()
        if remaining > 0:
            print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — ignore window active "
                  f"({remaining:.1f}s left). Skipped.")
            return

        # ── Only act in AUTO running mode ─────────────────────────────────────
        if state.current_mode != "running":
            print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — AGV not RUNNING. Ignored.")
            return

        print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — stopping AGV, "
              f"requesting PLC sequence {seq_num}.")

        # ── Stop AGV and send 1-second sequence pulse ─────────────────────────
        state.sequence_stop             = True
        state.plc_sequence_request      = seq_num
        state.plc_sequence_pulse_expire = time.time() + 1.0

        # ── Phase 1: wait for complete flag to go LOW (PLC started) ──────────
        # Necessary when the flag is still HIGH from the previous run.
        # The PLC clears it automatically when it begins executing the sequence.
        deadline = time.time() + 5.0
        while state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — PLC did not "
                      f"clear complete flag in 5s. Aborting.")
                state.sequence_stop = False
                return
            if state.current_mode != "running":
                print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — mode changed "
                      f"during wait (phase 1). Aborting.")
                state.sequence_stop = False
                return
            await asyncio.sleep(0.1)

        # ── Phase 2: wait for complete flag to go HIGH (PLC finished) ─────────
        deadline = time.time() + _TROLLEY_SEQ_TIMEOUT
        while not state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — timeout waiting "
                      f"for complete flag. Resuming anyway.")
                break
            if state.current_mode != "running":
                print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — mode changed "
                      f"during wait (phase 2). Aborting.")
                state.sequence_stop = False
                return
            await asyncio.sleep(0.1)
        else:
            print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — complete. Resuming AGV.")

        # ── Resume AGV and start ignore window ────────────────────────────────
        state.sequence_stop         = False
        _seq_ignore_until[seq_num]  = time.time() + _TROLLEY_SEQ_IGNORE_SECONDS
        print(f"[RFID] TROLLEY_SEQUENCE_{seq_num:02d} — "
              f"ignoring tag for {_TROLLEY_SEQ_IGNORE_SECONDS:.0f}s.")

    async def _execute_command(cmd: str):
        nonlocal _seq_stop_task

        if cmd.startswith("TROLLEY_SEQUENCE_"):
            seq_num = int(cmd.split("_")[2])
            await _run_trolley_sequence(seq_num)

        elif cmd == "RF_CMD_01":
            state.speed_mode = "SLOW"
            print(f"[RFID] CMD_01 — speed → SLOW ({config.AUTO_TARGET_SLOW_SPEED} m/s)")

        elif cmd == "RF_CMD_02":
            state.speed_mode = "HIGH"
            print(f"[RFID] CMD_02 — speed → HIGH ({config.AUTO_TARGET_HIGH_SPEED} m/s)")

        elif cmd == "RF_CMD_03":
            state.speed_mode = "EXTRA_SLOW"
            print(f"[RFID] CMD_03 — speed → EXTRA_SLOW ({config.AUTO_TARGET_EXTRA_SLOW_SPEED} m/s)")

        elif cmd == "RF_CMD_04":
            state.speed_mode = "HIGH"
            print(f"[RFID] CMD_04 — speed → HIGH ({config.AUTO_TARGET_HIGH_SPEED} m/s)")

        elif cmd == "RF_CMD_05":
            if _seq_stop_task and not _seq_stop_task.done():
                _seq_stop_task.cancel()
                try:
                    await _seq_stop_task
                except asyncio.CancelledError:
                    pass
            state.sequence_stop = True
            print(f"[RFID] CMD_05 — sequence stop. Resuming in {config.SEQUENCE_STOP_DELAY}s.")
            _seq_stop_task = asyncio.create_task(_sequence_stop_timer())

        elif cmd == "RF_CMD_00":
            print("[RFID] CMD_00 — home stop [NOT YET IMPLEMENTED]")

        else:
            print(f"[RFID] Unknown command '{cmd}' — no action.")

    print("[RFID Processor] Started.")
    while True:
        tag = await state.rfid_queue.get()
        cmd = config.RFID_COMMANDS.get(tag)
        if cmd is None:
            print(f"[RFID] Unrecognised tag: {tag} — ignored.")
        else:
            await _execute_command(cmd)