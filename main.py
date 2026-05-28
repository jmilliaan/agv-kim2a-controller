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


class DriverManager:
    """Starts/stops the toggleable hardware drivers (RFID, SLMP) at runtime.

    The Flask server runs in a separate thread, so set_enabled() marshals the
    start/stop onto the asyncio loop via run_coroutine_threadsafe. Toggles are
    NOT persisted — on restart the controller boots from the profile JSON.
    """

    _SPEC = {
        "RFID_ENABLED": {"factory": RFIDReader, "watch": True},
        "SLMP_ENABLED": {"factory": SLMPDriver, "watch": False},
    }

    def __init__(self, state, watched):
        self._state   = state
        self._watched = watched          # shared list read by safety_watchdog
        self._loop    = None
        self._tasks   = {}               # flag -> asyncio.Task
        self._drivers = {}               # flag -> driver instance

    def bind_loop(self, loop):
        self._loop = loop

    def is_running(self, flag):
        t = self._tasks.get(flag)
        return t is not None and not t.done()

    async def _start(self, flag):
        if self.is_running(flag):
            return
        spec = self._SPEC[flag]
        drv  = spec["factory"]()
        self._drivers[flag] = drv
        self._tasks[flag]   = asyncio.create_task(drv.run(self._state))
        if spec["watch"]:
            self._watched.append((drv, config.WATCHDOG_RFID_TIMEOUT_S))
        setattr(config, flag, True)
        logger.info("[DriverManager] %s ENABLED (live)", flag)

    async def _stop(self, flag):
        # Remove from the watchdog first so a cancelled driver can't trip a
        # stale-data fault between cancellation and removal.
        drv = self._drivers.pop(flag, None)
        if drv is not None:
            self._watched[:] = [(d, t) for (d, t) in self._watched if d is not drv]
        task = self._tasks.pop(flag, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        setattr(config, flag, False)
        logger.info("[DriverManager] %s DISABLED (live)", flag)

    async def _apply(self, flag, enabled):
        if enabled:
            await self._start(flag)
        else:
            await self._stop(flag)

    def set_enabled(self, flag, enabled):
        """Thread-safe entry point for the Flask server. Blocks until applied."""
        if flag not in self._SPEC:
            raise ValueError(f"unknown feature flag: {flag}")
        if self._loop is None:
            raise RuntimeError("driver manager loop not bound")
        fut = asyncio.run_coroutine_threadsafe(
            self._apply(flag, bool(enabled)), self._loop)
        return fut.result(timeout=10)


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
    # DIO and CAN are fixed at boot. RFID and SLMP are managed by DriverManager
    # so they can be toggled live from the HMI.
    di_drv   = DIReader()   if config.DIO_ENABLED  else None
    can_drv  = CANReader()  if config.CAN_ENABLED  else None
    do_drv   = DOWriter()   if config.DIO_ENABLED  else None
    ao_drv   = AOWriter()

    for name, enabled in [
        ("DIO (DI+DO)", config.DIO_ENABLED),
        ("CAN sensor",  config.CAN_ENABLED),
        ("RFID",       config.RFID_ENABLED),
        ("SLMP",       config.SLMP_ENABLED),
    ]:
        logger.info("%-12s %s", name, "ENABLED" if enabled else "DISABLED")

    watched = []
    manager = DriverManager(state, watched)
    manager.bind_loop(loop)

    threading.Thread(target=run_server, args=(state, engine, manager), daemon=True).start()

    def terminate_gracefully():
        logger.info("Termination signal received. Cancelling tasks...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, terminate_gracefully)
    loop.add_signal_handler(signal.SIGINT, terminate_gracefully)

    # ── Build task list ───────────────────────────────────────────────────────
    core_tasks = [ao_drv.run(state)]

    if do_drv:
        core_tasks.append(do_drv.run(state))
    if di_drv:
        core_tasks.append(di_drv.run(state))
        watched.append((di_drv, config.WATCHDOG_DI_TIMEOUT_S))
    if can_drv:
        core_tasks.append(can_drv.run(state))
        watched.append((can_drv, config.WATCHDOG_CAN_TIMEOUT_S))

    core_tasks.append(safety_watchdog(state, watched=watched))
    core_tasks.append(rfid_processor(state, engine))
    core_tasks.append(modes.mode_manager(state, engine))

    # RFID and SLMP run under the manager so the HMI can toggle them live.
    # Boots from the profile JSON values; toggles do not persist across restart.
    if config.RFID_ENABLED:
        await manager._apply("RFID_ENABLED", True)
    if config.SLMP_ENABLED:
        await manager._apply("SLMP_ENABLED", True)

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
