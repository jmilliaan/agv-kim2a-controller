import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient

import config
from drivers.base import ActuatorDriver

logger = logging.getLogger(__name__)


class DOWriter(ActuatorDriver):

    async def run(self, state):
        client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
        state.latest_do = [False] * config.NUM_DO

        while True:
            channel_no, active_state = await state.do_queue.get()
            try:
                if not client.connected:
                    await client.connect()

                await client.write_coil(
                    address=config.DO_BASE + channel_no,
                    value=bool(active_state),
                    device_id=config.DEVICE_ID)

                if 0 <= channel_no < config.NUM_DO:
                    state.latest_do[channel_no] = bool(active_state)

            except Exception as e:
                logger.error("DO Writer error: %s. Reconnecting...", e)
                client.close()
                await asyncio.sleep(1)


# ── Module-level shim ────────────────────────────────────────────────────────

async def do_writer(state):
    await DOWriter().run(state)
