"""
Sequence Engine — declarative, config-driven sequence runner.

Sequences are defined in the profile JSON under "sequences". Each sequence has:
  - a trigger  (rfid | marker | rfid_then_marker)
  - an actions list (ordered steps executed sequentially)
  - a cooldown  (seconds to ignore the same trigger after completion)
  - requires_mode (mode that must be active for the trigger to fire)

Adding a new sequence:  edit the profile JSON — zero Python changes needed.
Adding a new action type: add one method + register it in __init__.

Trigger types
-------------
rfid              : fires immediately when the matching RFID tag is read
marker            : fires when the matching tape marker is detected by auto_mode
rfid_then_marker  : RFID arms the approach (slows down), marker executes the rest
"""

import asyncio
import logging
import time

import config
import motion

logger = logging.getLogger(__name__)


class SequenceTimeout(Exception):
    pass


class SequenceEngine:

    def __init__(self, state, sequence_defs):
        """
        Args:
            state:          AMRState — shared state object
            sequence_defs:  list of sequence dicts loaded from config.SEQUENCES
        """
        self._state     = state
        self._sequences = sequence_defs

        # name → epoch at which the cooldown expires (0.0 = never triggered yet)
        self._cooldowns: dict = {}

        # rfid_then_marker: name → seq_def for sequences that have been RFID-armed
        # and are waiting for their marker
        self._armed: dict = {}

        # name and task of currently executing sequence (one at a time)
        self._active_sequence: str | None       = None
        self._active_task:  asyncio.Task | None = None

        # ── Built-in action registry ──────────────────────────────────────────
        self._actions: dict = {
            "set_speed":         self._act_set_speed,
            "wait_marker":       self._act_wait_marker,
            "stop_agv":          self._act_stop_agv,
            "resume":            self._act_resume,
            "sequence_stop":     self._act_sequence_stop,
            "wait_seconds":      self._act_wait_seconds,
            "plc_request":       self._act_plc_request,
            "wait_plc_complete": self._act_wait_plc_complete,
            "pusher_extend":     self._act_pusher_extend,
            "pusher_retract":    self._act_pusher_retract,
            "end_cycle":         self._act_end_cycle,
            "set_at_home":       self._act_set_at_home,
        }

    # ── Plugin point ─────────────────────────────────────────────────────────

    def register_action(self, name: str, handler):
        """Register a custom action handler so new action types can be added
        without modifying this file.

        handler signature:  async def handler(params: dict) -> None
        """
        self._actions[name] = handler

    # ── Public trigger interface (called by rfid_processor and auto_mode) ────

    def reload_sequences(self, new_defs: list) -> bool:
        """Hot-swap the sequence list. Safe to call from the asyncio loop.

        Refuses if a sequence is currently running (returns False).
        Clears armed and cooldown state — warn the operator that a recently-fired
        rule could re-fire immediately after reload.
        """
        if self._active_sequence is not None:
            logger.warning("[SEQ] reload_sequences refused — '%s' is running",
                           self._active_sequence)
            return False
        self._sequences = list(new_defs)
        self._armed.clear()
        self._cooldowns.clear()
        self._state.pending_sequence = None
        logger.info("[SEQ] Sequences reloaded — %d definition(s) active", len(self._sequences))
        return True

    async def on_rfid_tag(self, tag_hex: str) -> bool:
        """Called by rfid_processor whenever a tag is read.

        Returns True if at least one matching sequence was found (and either
        launched or armed), False if no sequence claimed this tag.
        """
        now = time.time()
        matched = False
        for seq in self._sequences:
            trig = seq["trigger"]

            if trig["type"] == "rfid" and trig["rfid_tag"] == tag_hex:
                matched = True
                if not self._check_preconditions(seq, now):
                    continue
                self._active_task = asyncio.create_task(self._run_sequence(seq))

            elif trig["type"] == "rfid_then_marker" and trig["rfid_tag"] == tag_hex:
                matched = True
                if not self._check_preconditions(seq, now):
                    continue
                # Arm: slow down for approach, record armed state
                self._armed[seq["name"]] = seq
                self._state.speed_mode       = "SLOW"
                self._state.pending_sequence = seq["name"]
                logger.info("[SEQ] Armed '%s' — slowing for marker approach", seq["name"])
        return matched

    async def on_marker(self, side: str):
        """Called by auto_mode whenever a left/right marker is detected in the
        sensor frame.  Fires both armed rfid_then_marker sequences and pure
        marker-triggered sequences."""
        now = time.time()

        # Check armed rfid_then_marker sequences first
        for name, seq in list(self._armed.items()):
            trig = seq["trigger"]
            if trig.get("marker_side") == side:
                del self._armed[name]
                self._state.pending_sequence = None
                logger.info("[SEQ] Marker '%s' detected — launching '%s'", side, name)
                # skip set_speed + wait_marker steps (already done during approach)
                self._active_task = asyncio.create_task(self._run_sequence(seq, skip_approach=True))

        # Check pure marker-triggered sequences
        for seq in self._sequences:
            trig = seq["trigger"]
            if trig["type"] == "marker" and trig.get("marker_side") == side:
                if not self._check_preconditions(seq, now):
                    continue
                self._active_task = asyncio.create_task(self._run_sequence(seq))

    def cancel_armed(self):
        """Disarm all pending rfid_then_marker sequences.
        Called by mode_manager on any mode transition (emergency, manual switch,
        reset) so stale armed states don't survive the mode change."""
        if self._armed:
            logger.info("[SEQ] Disarming %d pending sequence(s)", len(self._armed))
        self._armed.clear()
        self._state.pending_sequence = None

    def cancel_active_sequence(self):
        """Cancel the currently running sequence task, if any.
        Called by mode_manager on emergency, manual switch, or reset so a
        sequence in progress does not outlive the mode that started it."""
        if self._active_task and not self._active_task.done():
            logger.info("[SEQ] Cancelling active sequence '%s' due to mode change",
                        self._active_sequence)
            self._active_task.cancel()
        self._active_task     = None
        self._active_sequence = None
        self._state.sequence_stop = False

    def cancel_cooldowns(self):
        """Clear all sequence cooldowns.
        Called by mode_manager on reset, manual switch, or emergency so tags
        that were recently read don't stay suppressed into the next run."""
        if self._cooldowns:
            logger.info("[SEQ] Clearing %d cooldown(s): %s",
                        len(self._cooldowns), list(self._cooldowns.keys()))
        self._cooldowns.clear()

    # ── Status (for dashboard) ────────────────────────────────────────────────

    def status(self) -> dict:
        """Returns a snapshot for the Flask /api/state response."""
        now = time.time()
        return {
            "active":    self._active_sequence,
            "armed":     list(self._armed.keys()) or None,
            "cooldowns": {
                name: round(exp - now, 1)
                for name, exp in self._cooldowns.items()
                if exp > now
            },
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_preconditions(self, seq: dict, now: float) -> bool:
        """Returns True if the sequence may fire (mode + at_home + cooldown checks)."""
        required_mode = seq.get("requires_mode")
        if required_mode and self._state.current_mode != required_mode:
            logger.debug("[SEQ] '%s' ignored — mode is '%s', need '%s'",
                         seq["name"], self._state.current_mode, required_mode)
            return False

        if "requires_at_home" in seq:
            if seq["requires_at_home"] != self._state.at_home:
                logger.debug("[SEQ] '%s' ignored — requires_at_home=%s but state.at_home=%s",
                             seq["name"], seq["requires_at_home"], self._state.at_home)
                return False

        cooldown_exp = self._cooldowns.get(seq["name"], 0.0)
        if now < cooldown_exp:
            logger.debug("[SEQ] '%s' ignored — cooldown %.1fs remaining",
                         seq["name"], cooldown_exp - now)
            return False

        if self._active_sequence is not None:
            logger.debug("[SEQ] '%s' ignored — '%s' already running",
                         seq["name"], self._active_sequence)
            return False

        return True

    async def _run_sequence(self, seq: dict, skip_approach: bool = False):
        """Execute the action list for a sequence.

        skip_approach=True skips set_speed and wait_marker steps because they
        were already handled during the RFID arm phase.
        """
        name = seq["name"]
        logger.info("[SEQ] Running '%s'", name)
        self._active_sequence = name

        try:
            for action in seq["actions"]:
                atype = action["type"]

                if skip_approach and atype in ("set_speed", "wait_marker"):
                    continue

                handler = self._actions.get(atype)
                if handler is None:
                    logger.warning("[SEQ] '%s' — unknown action type '%s', skipping",
                                   name, atype)
                    continue

                await handler(action)

            logger.info("[SEQ] Completed '%s'", name)

        except asyncio.CancelledError:
            logger.info("[SEQ] Cancelled '%s'", name)
            # Ensure AGV is not left stopped if sequence is cancelled mid-run
            self._state.sequence_stop = False
            raise

        except SequenceTimeout as exc:
            logger.warning("[SEQ] '%s' timed out (%s) — resuming", name, exc)
            self._state.sequence_stop = False
            self._state.speed_mode    = "SLOW"

        finally:
            self._active_sequence = None
            self._active_task     = None
            self._cooldowns[name] = time.time() + seq.get("cooldown_s", 0.0)

    # ── Built-in action handlers ──────────────────────────────────────────────
    # Each handler receives the full action dict (params) from the JSON.

    async def _act_set_speed(self, p: dict):
        self._state.speed_mode = p["speed"]
        logger.info("[SEQ] Speed → %s", p["speed"])

    async def _act_wait_marker(self, p: dict):
        side     = p["side"]
        deadline = time.time() + p.get("timeout", 15.0)
        logger.info("[SEQ] Waiting for %s marker (timeout %.0fs)", side, p.get("timeout", 15.0))
        while time.time() < deadline:
            if self._state.current_mode != "running":
                raise asyncio.CancelledError()
            sen = self._state.latest_sensor
            if sen and sen.get(f"{side}_marker"):
                return
            await asyncio.sleep(0.02)
        raise SequenceTimeout(f"wait_marker side={side}")

    async def _act_stop_agv(self, p: dict):
        logger.info("[SEQ] Stopping AGV")
        self._state.sequence_stop = True

    async def _act_resume(self, p: dict):
        self._state.sequence_stop = False
        speed = p.get("speed")
        if speed is not None:
            self._state.speed_mode = speed
        logger.info("[SEQ] Resuming — speed=%s", speed or "(unchanged)")

    async def _act_sequence_stop(self, p: dict):
        """Timed stop: stop the AGV for `duration` seconds then resume."""
        duration = p["duration"]
        logger.info("[SEQ] Timed stop %.1fs", duration)
        self._state.sequence_stop = True
        await asyncio.sleep(duration)
        self._state.sequence_stop = False
        logger.info("[SEQ] Timed stop ended")

    async def _act_wait_seconds(self, p: dict):
        await asyncio.sleep(p["duration"])

    async def _act_plc_request(self, p: dict):
        seq_num      = p["seq_num"]
        pulse        = p.get("pulse_duration", 1.0)
        self._state.plc_sequence_request      = seq_num
        self._state.plc_sequence_pulse_expire = time.time() + pulse
        logger.info("[SEQ] PLC request seq=%d (pulse %.1fs)", seq_num, pulse)

    async def _act_wait_plc_complete(self, p: dict):
        seq_num       = p["seq_num"]
        clear_timeout = p.get("clear_timeout", 5.0)
        done_timeout  = p.get("done_timeout", 30.0)

        # Phase A: wait for complete flag to go LOW (PLC acknowledged and started)
        logger.info("[SEQ] Waiting PLC seq=%d clear (flag LOW)", seq_num)
        deadline = time.time() + clear_timeout
        while self._state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                logger.warning("[SEQ] PLC seq=%d clear timeout — proceeding", seq_num)
                return
            if self._state.current_mode != "running":
                raise asyncio.CancelledError()
            await asyncio.sleep(0.1)

        # Phase B: wait for complete flag to go HIGH (PLC work done)
        logger.info("[SEQ] Waiting PLC seq=%d done (flag HIGH)", seq_num)
        deadline = time.time() + done_timeout
        while not self._state.plc_sequence_complete[seq_num]:
            if time.time() > deadline:
                logger.warning("[SEQ] PLC seq=%d done timeout — resuming anyway", seq_num)
                return
            if self._state.current_mode != "running":
                raise asyncio.CancelledError()
            await asyncio.sleep(0.1)

        logger.info("[SEQ] PLC seq=%d complete", seq_num)

    async def _act_pusher_extend(self, p: dict):
        assert config.PUSHER_CHANNELS is not None, \
            "pusher_extend requires 'pusher_channels' in profile"
        duration = p.get("duration", 2.0)
        logger.info("[SEQ] Pusher UP (%.1fs)", duration)
        await motion.pusher_up(self._state)
        await asyncio.sleep(duration)
        await motion.pusher_clear(self._state)
        logger.info("[SEQ] Pusher UP complete")

    async def _act_pusher_retract(self, p: dict):
        assert config.PUSHER_CHANNELS is not None, \
            "pusher_retract requires 'pusher_channels' in profile"
        duration = p.get("duration", 2.0)
        logger.info("[SEQ] Pusher DOWN (%.1fs)", duration)
        await motion.pusher_down(self._state)
        await asyncio.sleep(duration)
        await motion.pusher_clear(self._state)
        logger.info("[SEQ] Pusher DOWN complete")

    async def _act_end_cycle(self, p: dict):
        """Signal mode_manager to return to ARMED immediately.
        Used as the last action of the home-arrival sequence."""
        logger.info("[SEQ] end_cycle — requesting return to ARMED")
        self._state.end_cycle_request = True

    async def _act_set_at_home(self, p: dict):
        """Set state.at_home flag to control which tag-10 sequence fires next."""
        value = bool(p.get("value", True))
        logger.info("[SEQ] set_at_home → %s", value)
        self._state.at_home = value
