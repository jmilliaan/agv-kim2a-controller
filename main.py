import asyncio
import logging
import signal
import threading
from pymodbus.client import AsyncModbusTcpClient

import config
from logger import setup_logging
from state import AMRState
import io_hardware
import modes
from rfid_processor import rfid_processor
import slmp_handler
from app.app import run_server

logger = logging.getLogger(__name__)


async def shutdown():
    """Zero all DO and AO outputs via direct Modbus writes, bypassing the queues."""
    logger.info("Shutting down — zeroing all outputs...")
    try:
        dio_client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
        ao_client  = AsyncModbusTcpClient(config.AO_IP,  port=config.MODBUS_PORT)
        await dio_client.connect()
        await ao_client.connect()

        for i in range(config.NUM_DO):
            await dio_client.write_coil(address=config.DO_BASE + i, value=False, device_id=config.DEVICE_ID)
        for i in range(config.NUM_AO):
            await ao_client.write_register(address=config.AO_BASE + i, value=0, device_id=config.DEVICE_ID)

        dio_client.close()
        ao_client.close()
        logger.info("All outputs zeroed. Shutdown complete.")
    except Exception as e:
        logger.error("Shutdown error: %s", e)

async def run():
    setup_logging()

    loop = asyncio.get_running_loop()

    state = AMRState()

    threading.Thread(target=run_server, args=(state,), daemon=True).start()

    def terminate_gracefully():
        logger.info("Termination signal received. Cancelling tasks...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, terminate_gracefully)
    loop.add_signal_handler(signal.SIGINT, terminate_gracefully)

    tasks = asyncio.gather(
        io_hardware.di_reader(state),
        io_hardware.rfid_reader(state),
        io_hardware.can_reader(state),
        io_hardware.do_writer(state),
        io_hardware.ao_writer(state),
        rfid_processor(state),
        modes.mode_manager(state),
        slmp_handler.slmp_handler(state)
    )

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