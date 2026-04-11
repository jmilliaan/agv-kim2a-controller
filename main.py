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
from drivers.modbus_ao   import AOWriter
from drivers.can_mgs1600 import CANReader
from drivers.rfid_tcp    import RFIDReader
from drivers.slmp_plc    import SLMPDriver
from safety_watchdog import safety_watchdog
import modes
from rfid_processor import rfid_processor
from app.app import run_server

logger = logging.getLogger(__name__)


async def shutdown():
    """Zero all DO and AO outputs via direct Modbus writes, bypassing the queues."""
    logger.info("Shutting down — zeroing all outputs...")
    try:
        ao_client = AsyncModbusTcpClient(config.AO_IP, port=config.MODBUS_PORT)
        await ao_client.connect()
        for i in range(config.NUM_AO):
            await ao_client.write_register(address=config.AO_BASE + i, value=0, device_id=config.DEVICE_ID)
        ao_client.close()

        if config.DIO_ENABLED:
            dio_client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
            await dio_client.connect()
            for i in range(config.NUM_DO):
                await dio_client.write_coil(address=config.DO_BASE + i, value=False, device_id=config.DEVICE_ID)
            dio_client.close()
        logger.info("All outputs zeroed. Shutdown complete.")
    except Exception as e:
        logger.error("Shutdown error: %s", e)

async def run():
    setup_logging()

    loop = asyncio.get_running_loop()

    state  = AMRState()
    engine = SequenceEngine(state, config.SEQUENCES)

    logger.info("Loaded profile: %s  (%d sequence(s) defined)",
                config.AGV_ID, len(config.SEQUENCES))

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

    threading.Thread(target=run_server, args=(state, engine), daemon=True).start()

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
        watched.append((di_drv, config.WATCHDOG_DI_TIMEOUT_S))
    if can_drv:
        core_tasks.append(can_drv.run(state))
        watched.append((can_drv, config.WATCHDOG_CAN_TIMEOUT_S))
    if rfid_drv:
        core_tasks.append(rfid_drv.run(state))
        watched.append((rfid_drv, config.WATCHDOG_RFID_TIMEOUT_S))
    if slmp_drv:
        core_tasks.append(slmp_drv.run(state))

    core_tasks.append(safety_watchdog(state, watched=watched))
    core_tasks.append(rfid_processor(state, engine))
    core_tasks.append(modes.mode_manager(state, engine))

    tasks = asyncio.gather(*core_tasks)

    try:
        await tasks
    except asyncio.CancelledError:
        logger.info("Main loops cancelled. Executing Modbus hardware shutdown...")
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
