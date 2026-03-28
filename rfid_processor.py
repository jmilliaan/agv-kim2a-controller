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
import config


async def rfid_processor(state):
    _seq_stop_task = None

    async def _sequence_stop_timer():
        await asyncio.sleep(config.SEQUENCE_STOP_DELAY)
        state.sequence_stop = False
        print("[RFID] Sequence stop complete — resuming AUTO.")

    async def _execute_command(cmd: str):
        nonlocal _seq_stop_task

        if cmd == "RF_CMD_01":
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