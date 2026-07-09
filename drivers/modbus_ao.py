import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient

import config
from drivers.base import ActuatorDriver

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 2.0
_WRITE_RETRY_LIMIT = 3
_WRITE_RETRY_BACKOFF_S = 0.2


class AOWriter(ActuatorDriver):

    async def run(self, state):
        client = AsyncModbusTcpClient(config.AO_IP, port=config.MODBUS_PORT)

        try:
            while True:
                channel_no, target_v = await state.ao_queue.get()
                dac_value = int(target_v / config.V_RANGE * config.DAC_RES)

                # Retry the consumed command up to N times. Previously a single failure
                # silently dropped the write, so the AGV believed the new voltage was
                # commanded when in fact nothing changed at the DAC.
                attempt = 0
                while True:
                    try:
                        if not client.connected:
                            await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT_S)

                        await client.write_register(
                            address=config.AO_BASE + channel_no,
                            value=dac_value,
                            device_id=config.DEVICE_ID)

                        if state.driver_write_fault:
                            logger.info("AO Writer recovered.")
                            state.driver_write_fault = False
                            state.driver_write_fault_detail = ""
                            state.log_event("INFO", "AO writer recovered — outputs restored")
                        break

                    except Exception as e:
                        attempt += 1
                        if attempt >= _WRITE_RETRY_LIMIT:
                            if not state.driver_write_fault:
                                state.driver_write_fault = True
                                state.driver_write_fault_detail = f"AO: {e}"
                                state.log_event("ERROR",
                                    f"AO writer fault — last 3 commands failed: {e}")
                            logger.error("AO Writer giving up after %d attempts (ch=%d v=%.3f): %s",
                                         attempt, channel_no, target_v, e)
                            try:
                                client.close()
                            except Exception:
                                pass
                            break

                        logger.warning("AO Writer attempt %d failed (ch=%d): %s. Reconnecting...",
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

async def ao_writer(state):
    await AOWriter().run(state)
