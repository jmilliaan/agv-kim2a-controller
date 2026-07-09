import asyncio
import logging
import os
import signal
import threading
from pymodbus.client import AsyncModbusTcpClient

import config
from logger import setup_logging
from state import AMRState
from core.sequence_engine import SequenceEngine
from drivers.modbus_di   import DIReader
from drivers.modbus_do   import DOWriter
from drivers.modbus_ao   import AOWriter
from drivers.can_mgs1600 import CANReader
from drivers.rfid_tcp    import RFIDReader
from drivers.slmp_plc    import SLMPDriver
from safety_watchdog import safety_watchdog
import modes
from rfid_processor import rfid_processor
from horn_controller import horn_controller
from app.app import run_server, stop_server

logger = logging.getLogger(__name__)


async def shutdown():
    """Release the web server port, then zero all DO and AO outputs.

    Each Modbus connect is bounded by a 1 s timeout so an unreachable device
    cannot block the shutdown path indefinitely (which would prevent a clean
    exit on power loss).
    """
    stop_server()   # unblock serve_forever() so the daemon thread exits cleanly
    logger.info("Shutting down — zeroing all outputs...")
    _SHUTDOWN_CONNECT_TIMEOUT_S = 1.0

    # AO
    try:
        ao_client = AsyncModbusTcpClient(config.AO_IP, port=config.MODBUS_PORT)
        try:
            await asyncio.wait_for(ao_client.connect(), timeout=_SHUTDOWN_CONNECT_TIMEOUT_S)
            for i in range(config.NUM_AO):
                await ao_client.write_register(address=config.AO_BASE + i, value=0, device_id=config.DEVICE_ID)
            logger.info("AO outputs zeroed.")
        except asyncio.TimeoutError:
            logger.warning("AO connect timed out during shutdown — skipping zero-out.")
        finally:
            try: ao_client.close()
            except Exception: pass
    except Exception as e:
        logger.error("AO shutdown error: %s", e)

    # DO
    if config.DIO_ENABLED:
        try:
            dio_client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
            try:
                await asyncio.wait_for(dio_client.connect(), timeout=_SHUTDOWN_CONNECT_TIMEOUT_S)
                for i in range(config.NUM_DO):
                    await dio_client.write_coil(address=config.DO_BASE + i, value=False, device_id=config.DEVICE_ID)
                logger.info("DO outputs zeroed.")
            except asyncio.TimeoutError:
                logger.warning("DIO connect timed out during shutdown — skipping zero-out.")
            finally:
                try: dio_client.close()
                except Exception: pass
        except Exception as e:
            logger.error("DO shutdown error: %s", e)

    logger.info("Shutdown complete.")


# ── Controlled restart (HMI button + physical E-stop+RESET-3s combo) ─────────
# systemd unit has Restart=always RestartSec=2, so os._exit(0) here causes
# the service to come back up within a couple of seconds. We zero AO/DO via
# shutdown() first so motors are guaranteed safe across the bounce.
_restart_in_progress = False

async def request_restart(state, reason: str):
    global _restart_in_progress
    if _restart_in_progress:
        return
    _restart_in_progress = True
    try:
        state.log_event("CRITICAL", f"CONTROLLER RESTART requested: {reason}")
        logger.warning("Restart requested (%s) — zeroing outputs then exiting for systemd relaunch",
                       reason)
        # Brief grace so the HTTP response can flush and a held physical button
        # has a chance to be released before the bounce.
        await asyncio.sleep(0.5)
        await shutdown()
    except Exception as exc:
        logger.error("Error during restart shutdown sequence (continuing to exit): %s", exc)
    finally:
        logger.warning("Exiting now — systemd will relaunch in ~2 s")
        os._exit(0)


async def run():
    setup_logging()

    loop = asyncio.get_running_loop()

    state      = AMRState()
    state.loop = loop   # expose for Flask thread → call_soon_threadsafe

    from core.mapping_store import load_rules, compile_to_sequences, OverrideFileCorrupt
    try:
        _override_rules = load_rules(config.AGV_ID)
    except OverrideFileCorrupt as exc:
        _override_rules = None
        # State exists so log to event log; the dashboard's Errors page will
        # show this so the operator can see why their custom mappings vanished.
        state.log_event("ERROR",
            f"Mapping override file corrupt — using profile defaults. "
            f"Bad file moved to {exc.renamed_to or '(could not rename)'}")
        logger.error("Mapping override corrupt: %s", exc)
    if _override_rules is not None:
        _initial_seqs = compile_to_sequences(_override_rules)
        logger.info("Loaded mapping override: %s  (%d rule(s) → %d sequence(s))",
                    config.AGV_ID, len(_override_rules), len(_initial_seqs))
    else:
        _initial_seqs = config.SEQUENCES
        logger.info("No mapping override found — using profile sequences: %s  (%d sequence(s))",
                    config.AGV_ID, len(_initial_seqs))

    engine = SequenceEngine(state, _initial_seqs)

    # ── Instantiate drivers (feature-flag gated) ──────────────────────────────
    di_drv   = DIReader()   if config.DIO_ENABLED  else None
    can_drv  = CANReader()  if config.CAN_ENABLED  else None
    rfid_drv = RFIDReader() if config.RFID_ENABLED else None
    do_drv   = DOWriter()   if config.DIO_ENABLED  else None
    ao_drv   = AOWriter()
    slmp_drv = SLMPDriver() if config.SLMP_ENABLED else None

    for name, enabled in [
        ("DIO (DI+DO)", config.DIO_ENABLED),
        ("CAN sensor",  config.CAN_ENABLED),
        ("RFID",       config.RFID_ENABLED),
        ("SLMP",       config.SLMP_ENABLED),
    ]:
        logger.info("%-12s %s", name, "ENABLED" if enabled else "DISABLED")

    threading.Thread(target=run_server,
                     args=(state, engine, request_restart),
                     daemon=True).start()

    def terminate_gracefully():
        logger.info("Termination signal received. Cancelling tasks...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, terminate_gracefully)
    loop.add_signal_handler(signal.SIGINT, terminate_gracefully)

    # ── Build task list ───────────────────────────────────────────────────────
    watched = []
    core_tasks = [ao_drv.run(state)]

    if do_drv:
        core_tasks.append(do_drv.run(state))
    if di_drv:
        core_tasks.append(di_drv.run(state))
        watched.append((di_drv,   config.WATCHDOG_DI_TIMEOUT_S,   False))  # critical — blocks all modes
    if can_drv:
        core_tasks.append(can_drv.run(state))
        watched.append((can_drv,  config.WATCHDOG_CAN_TIMEOUT_S,  True))   # auto-only — manual still works
    if rfid_drv:
        core_tasks.append(rfid_drv.run(state))
        watched.append((rfid_drv, config.WATCHDOG_RFID_TIMEOUT_S, True))   # auto-only — manual still works
    if slmp_drv:
        core_tasks.append(slmp_drv.run(state))

    core_tasks.append(safety_watchdog(state, watched=watched))
    core_tasks.append(rfid_processor(state, engine))
    core_tasks.append(horn_controller(state))
    core_tasks.append(modes.mode_manager(state, engine, restart_cb=request_restart))

    tasks = asyncio.gather(*core_tasks)

    try:
        await tasks
    except (asyncio.CancelledError, Exception) as exc:
        if not isinstance(exc, asyncio.CancelledError):
            logger.error("Unhandled task exception — forcing shutdown: %s", exc)
        else:
            logger.info("Main loops cancelled. Executing Modbus hardware shutdown...")
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
