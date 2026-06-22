import asyncio
import logging
import signal
import threading
from pymodbus.client import AsyncModbusTcpClient

import config
from logger import setup_logging
from state import AMRState
from core.sequence_engine import SequenceEngine
from drivers.modbus_di   import DIReader
from drivers.modbus_do   import DOWriter
from drivers.can_bldc    import CANMotorDriver
from drivers.can_mls     import CANReader
from drivers.rfid_tcp    import RFIDReader
from safety_watchdog import safety_watchdog
import modes
from rfid_processor import rfid_processor
from horn_controller import horn_controller
from app.app import run_server, stop_server

logger = logging.getLogger(__name__)


async def _drain_queue(q):
    """No-op consumer for a disabled output queue — keeps it from growing
    unbounded when its writer driver is turned off (bench / no-hardware runs)."""
    while True:
        await q.get()


async def shutdown(motor_drv):
    """Release the web server port, leave the drives safe (0 rpm + Quick stop,
    CiA-402 down to SWITCHED ON), then zero all DO coils."""
    stop_server()   # unblock serve_forever() so the daemon thread exits cleanly
    logger.info("Shutting down — stopping drives and zeroing outputs...")
    try:
        loop = asyncio.get_running_loop()
        if motor_drv is not None:
            await loop.run_in_executor(None, motor_drv.safe_stop)

        if config.DIO_ENABLED:
            dio_client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
            await dio_client.connect()
            for i in range(config.NUM_DO):
                await dio_client.write_coil(address=config.DO_BASE + i, value=False, device_id=config.DEVICE_ID)
            dio_client.close()
        logger.info("Drives stopped, outputs zeroed. Shutdown complete.")
    except Exception as e:
        logger.error("Shutdown error: %s", e)

async def run():
    setup_logging()

    loop = asyncio.get_running_loop()

    state      = AMRState()
    state.loop = loop   # expose for Flask thread → call_soon_threadsafe

    from core.mapping_store import load_rules, compile_to_sequences
    _override_rules = load_rules(config.AGV_ID)
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
    # The motor CAN driver owns the shared CAN bus; the SICK MLS CANReader attaches
    # to that same bus rather than opening its own. When the motor bus is disabled
    # the sensor has no bus, so CAN_ENABLED is forced off too.
    motor_drv = CANMotorDriver() if config.MOTOR_CAN_ENABLED else None
    can_sensor_on = config.CAN_ENABLED and config.MOTOR_CAN_ENABLED
    if config.CAN_ENABLED and not config.MOTOR_CAN_ENABLED:
        logger.warning("CAN sensor needs the motor CAN bus — disabling it (MOTOR_CAN_ENABLED=0)")

    di_drv   = DIReader()            if config.DIO_ENABLED  else None
    can_drv  = CANReader(motor_drv)  if can_sensor_on       else None
    rfid_drv = RFIDReader()          if config.RFID_ENABLED else None
    do_drv   = DOWriter()            if config.DIO_ENABLED  else None

    for name, enabled in [
        ("DIO (DI+DO)", config.DIO_ENABLED),
        ("MOTOR CAN",   config.MOTOR_CAN_ENABLED),
        ("CAN sensor",  can_sensor_on),
        ("RFID",        config.RFID_ENABLED),
    ]:
        logger.info("%-12s %s", name, "ENABLED" if enabled else "DISABLED")

    threading.Thread(target=run_server, args=(state, engine), daemon=True).start()

    def terminate_gracefully():
        logger.info("Termination signal received. Cancelling tasks...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, terminate_gracefully)
    loop.add_signal_handler(signal.SIGINT, terminate_gracefully)

    # ── Build task list ───────────────────────────────────────────────────────
    watched = []
    if motor_drv is not None:
        core_tasks = [motor_drv.run(state)]
    else:
        # No wheel drive: drain motor_queue so motion helpers don't back it up.
        logger.info("MOTOR CAN disabled — wheels offline, draining motor_queue")
        core_tasks = [_drain_queue(state.motor_queue)]

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

    core_tasks.append(safety_watchdog(state, watched=watched))
    core_tasks.append(rfid_processor(state, engine))
    core_tasks.append(horn_controller(state))
    core_tasks.append(modes.mode_manager(state, engine))

    tasks = asyncio.gather(*core_tasks)

    try:
        await tasks
    except (asyncio.CancelledError, Exception) as exc:
        if not isinstance(exc, asyncio.CancelledError):
            logger.error("Unhandled task exception — forcing shutdown: %s", exc)
        else:
            logger.info("Main loops cancelled. Executing hardware shutdown...")
        await shutdown(motor_drv)

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
