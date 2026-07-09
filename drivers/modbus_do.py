import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient

import config
from drivers.base import ActuatorDriver

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 2.0
_WRITE_RETRY_LIMIT = 3
_WRITE_RETRY_BACKOFF_S = 0.2


class DOWriter(ActuatorDriver):

    async def run(self, state):
        client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
        state.latest_do = [False] * config.NUM_DO

        try:
            while True:
                channel_no, active_state = await state.do_queue.get()

                attempt = 0
                while True:
                    try:
                        if not client.connected:
                            await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT_S)

                        await client.write_coil(
                            address=config.DO_BASE + channel_no,
                            value=bool(active_state),
                            device_id=config.DEVICE_ID)

                        if 0 <= channel_no < config.NUM_DO:
                            state.latest_do[channel_no] = bool(active_state)

                        if state.driver_write_fault:
                            logger.info("DO Writer recovered.")
                            state.driver_write_fault = False
                            state.driver_write_fault_detail = ""
                            state.log_event("INFO", "DO writer recovered — outputs restored")
                        break

                    except Exception as e:
                        attempt += 1
                        if attempt >= _WRITE_RETRY_LIMIT:
                            if not state.driver_write_fault:
                                state.driver_write_fault = True
                                state.driver_write_fault_detail = f"DO: {e}"
                                state.log_event("ERROR",
                                    f"DO writer fault — last 3 commands failed: {e}")
                            logger.error("DO Writer giving up after %d attempts (ch=%d on=%s): %s",
                                         attempt, channel_no, active_state, e)
                            try:
                                client.close()
                            except Exception:
                                pass
                            break

                        logger.warning("DO Writer attempt %d failed (ch=%d): %s. Reconnecting...",
                                       attempt, channel_no, e)
                        try:
                            client.close()
                        except Exception:
                            pass
                        await asyncio.sleep(_WRITE_RETRY_BACKOFF_S)
        finally:
            # Ensure the socket is released on task cancellation (mode change,
            # emergency, shutdown). Without this, repeated restarts leak FDs
            # and Modbus writes eventually fail across the board.
            try:
                client.close()
            except Exception:
                pass


# ── Module-level shim ────────────────────────────────────────────────────────

async def do_writer(state):
    await DOWriter().run(state)
