"""
rfid_processor.py — RFID tag dispatcher
========================================
Consumes tags from state.rfid_queue and forwards them to the SequenceEngine.

All sequence behaviour (what happens when a tag is read) is now defined in the
profile JSON under "sequences".  No Python changes are needed to add or modify
sequences — edit the profile, restart.

To add a new action type (e.g. "activate_gripper"), add one method to
core/sequence_engine.py and register it there.
"""

import logging

logger = logging.getLogger(__name__)


async def rfid_processor(state, engine):
    """Thin dispatcher: reads tags from the queue and passes to the engine.

    Args:
        state:  AMRState shared object
        engine: SequenceEngine instance (core/sequence_engine.py)
    """
    logger.info("[RFID Processor] Started.")
    while True:
        tag = await state.rfid_queue.get()
        state.latest_rfid_tag = tag
        logger.debug("[RFID] Tag read: %s", tag)
        await engine.on_rfid_tag(tag)
