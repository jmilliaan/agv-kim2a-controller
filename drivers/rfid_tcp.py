import asyncio
import binascii
import logging
import socket
import time

import config
from drivers.base import SensorDriver

logger = logging.getLogger(__name__)

# Soft warning threshold — if no real bytes arrive (just empty recv timeouts)
# for this long, raise a "RFID silent" flag for the dashboard. Distinct from
# the safety_watchdog's hard timeout (which would block auto mode).
_RFID_SILENT_WARN_S = 60.0


class RFIDReader(SensorDriver):

    def __init__(self):
        super().__init__("RFID TCP")

    async def run(self, state):
        loop = asyncio.get_event_loop()

        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                # Bound the connect attempt so an unreachable reader doesn't
                # block the asyncio loop for the OS TCP timeout (30-120 s).
                await asyncio.wait_for(
                    loop.sock_connect(sock, (config.RFID_IP, config.RFID_PORT)),
                    timeout=2.0)
                await loop.sock_sendall(sock, config.RFID_INIT_CMD)
                logger.info("RFID Connected.")
                # Mark watchdog healthy on connect, and prime the data-traffic
                # timestamp so the silent-warning timer starts fresh.
                self._record_rx()
                state.rfid_last_data_ts = time.time()
                state.rfid_silent_warning = False

                while True:
                    try:
                        data = await asyncio.wait_for(
                            loop.sock_recv(sock, 1024),
                            timeout=2.0)
                    except asyncio.TimeoutError:
                        # No bytes in this 2 s window. Keep the safety_watchdog
                        # happy so the AGV doesn't go into sensor_error during
                        # normal between-tag silence, but track *real* traffic
                        # separately so a dead-but-connected reader is detectable.
                        self._record_rx()
                        if (time.time() - state.rfid_last_data_ts) > _RFID_SILENT_WARN_S:
                            if not state.rfid_silent_warning:
                                state.rfid_silent_warning = True
                                state.log_event("WARNING",
                                    f"RFID reader connected but no data for >{int(_RFID_SILENT_WARN_S)} s "
                                    "— check antenna / reader power")
                                logger.warning("RFID silent for >%.0f s — flagged on dashboard",
                                               _RFID_SILENT_WARN_S)
                        continue

                    if not data:
                        logger.warning("RFID connection closed by peer.")
                        break

                    # Real bytes arrived — update the data-traffic timestamp and
                    # clear any silent-warning flag.
                    state.rfid_last_data_ts = time.time()
                    if state.rfid_silent_warning:
                        state.rfid_silent_warning = False
                        state.log_event("INFO", "RFID data resumed — silent warning cleared")

                    hex_data = binascii.hexlify(data).decode().upper()
                    for packet in hex_data.split("CF")[1:]:
                        packet = "CF" + packet
                        # Reader emits fixed-size 34-hex-char (17-byte) frames.
                        # Drop anything shorter (truncated mid-frame) OR longer
                        # (concatenated noise / stray CF in payload) — a bogus
                        # tag here would fire an automated sequence, so be strict.
                        if len(packet) != 34:
                            if len(packet) > 4:   # skip the trivial trailing-CF split artifact
                                logger.warning("RFID packet wrong length (%d), dropped: %s",
                                               len(packet), packet[:40])
                            continue
                        tag = packet[26:30]
                        if tag == "3130":          # startup echo artifact — not a real tag
                            continue
                        try:
                            tag_dec = int(tag, 16)
                        except ValueError:
                            logger.warning("RFID malformed tag dropped: %r", tag)
                            continue
                        logger.info("RFID tag read: %s (dec=%d)", tag, tag_dec)
                        await state.rfid_queue.put(tag)
                        self._record_rx()

            except asyncio.TimeoutError:
                logger.warning("RFID connect timeout. Retrying in 2 s...")
            except Exception as e:
                logger.warning("RFID connection failed/lost: %s. Retrying in 2s...", e)
            finally:
                try: sock.close()
                except Exception: pass

            await asyncio.sleep(2)


# ── Module-level shim ────────────────────────────────────────────────────────

_instance = RFIDReader()

async def rfid_reader(state):
    await _instance.run(state)
