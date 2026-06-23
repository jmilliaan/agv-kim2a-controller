"""
core/mission.py — EVO mission FSM [EVO]

Elaborates the controller's `running` mode into the mission state machine defined
in evo-system.md §10.3:

    IDLE_HOME → DEPART_HOME → AT_ATTACH(load+confirm) → TRAVEL_1 → AT_STOP_1(swap+confirm)
              → [TRAVEL_2 → AT_STOP_2(swap+confirm)] → RETURN → AT_HOME_UNLOAD(confirm)
              → IDLE_HOME

Design split (agv_unit_update_plan §8 "Fleet mapping authority"):
  - This FSM is a *progress tracker + confirm gate*. It decides WHICH stop is
    next and WHEN the AGV may depart a handoff point.
  - The local SequenceEngine / RFID rules remain authoritative for the BEHAVIOUR
    at each tag (where to stop, pusher actuation, slow zones, corner speed). The
    join is by RFID tag id: mission `stops[].tag` == the AGV's profile sequence tags.

It is event-driven and side-effect-light so it is unit-testable without hardware:
  - `start(mission)`         — a fresh cmd/mission was accepted; begin the trip.
  - `on_tag(tag)`            — call on every localizing tag read (from rfid_processor).
  - `on_confirm(...)`        — call on a confirm press (DI_CONFIRM edge / web fallback).
  - `abort()`               — reset to IDLE_HOME (reset / emergency / end of trip).

Outputs are written to `AMRState` (mission_state, direction, confirm_pending,
confirm_ts, confirm_location, current_stop) and mirrored to MQTT via `fleet`
(state_change / confirm events). The FSM never touches motion — it only opens /
closes the `confirm_pending` gate that `auto_mode` holds on.
"""

import logging
import time

import fleet

logger = logging.getLogger(__name__)

# Confirm-gated handoff locations (for the `confirm` event payload).
LOC_ATTACH = "attach"
LOC_STOP   = "stop"
LOC_HOME   = "home"


class MissionFSM:
    def __init__(self, state, attach_tag=None, home_tag=None):
        self.state = state
        self.attach_tag = attach_tag
        self.home_tag = home_tag
        # progress
        self.mission = None
        self.stops = []          # ordered list of stop tag ids (4-char hex strings)
        self.stop_index = 0      # index of the stop currently being travelled-to / serviced
        self.abort()

    # ── helpers ───────────────────────────────────────────────────────────────
    def _set(self, new_state):
        old = self.state.mission_state
        if old == new_state:
            return
        self.state.mission_state = new_state
        logger.info("[MISSION] %s → %s", old, new_state)
        self.state.log_event("INFO", f"MISSION: {old} → {new_state}")
        fleet.emit_state_change(self.state, old, new_state)

    def _open_gate(self, new_state, location, stop):
        self.state.confirm_pending = True
        self.state.confirm_ts = time.time()
        self.state.confirm_location = location
        self.state.current_stop = stop
        self._set(new_state)

    def _close_gate(self):
        self.state.confirm_pending = False
        self.state.confirm_location = None

    @property
    def active(self):
        return self.state.mission_state not in ("IDLE_HOME",)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def abort(self):
        """Return to safe idle — no mission. Called on reset / emergency / boot."""
        self.mission = None
        self.stops = []
        self.stop_index = 0
        self.state.mission_state = "IDLE_HOME"
        self.state.confirm_pending = False
        self.state.confirm_location = None
        self.state.current_stop = None
        self.state.direction = "outbound"

    def start(self, mission):
        """Begin a newly-accepted mission. Sets DEPART_HOME, outbound."""
        self.mission = mission or {}
        self.stops = [s.get("tag") for s in self.mission.get("active_stops", []) if s.get("tag")]
        self.stop_index = 0
        self.state.direction = "outbound"
        self.state.current_stop = None
        self.state.confirm_pending = False
        self._set("DEPART_HOME")
        logger.info("[MISSION] start trip=%s loop=%s stops=%s",
                    self.mission.get("trip_id"), self.mission.get("loop"), self.stops)

    # ── inputs ────────────────────────────────────────────────────────────────
    def on_tag(self, tag):
        """Advance the FSM on a localizing tag read. Opens confirm gates at the
        attach point, each active stop, and home."""
        ms = self.state.mission_state

        if ms == "DEPART_HOME":
            if self.attach_tag is not None and tag == self.attach_tag:
                self._open_gate("AT_ATTACH", LOC_ATTACH, stop=None)
            return

        if ms.startswith("TRAVEL_"):
            if self.stop_index < len(self.stops) and tag == self.stops[self.stop_index]:
                self._open_gate(f"AT_STOP_{self.stop_index + 1}", LOC_STOP, stop=tag)
            return

        if ms == "RETURN":
            if self.home_tag is not None and tag == self.home_tag:
                self._open_gate("AT_HOME_UNLOAD", LOC_HOME, stop=None)
            return

    def on_confirm(self):
        """A confirm was pressed at a gated handoff — close the gate and advance.
        Returns True if a gate was actually closed (i.e. the press was meaningful)."""
        ms = self.state.mission_state
        if not self.state.confirm_pending:
            return False

        if ms == "AT_ATTACH":
            fleet.emit_confirm(self.state, stop=None, location=LOC_ATTACH)
            self._close_gate()
            self.stop_index = 0
            self.state.current_stop = self.stops[0] if self.stops else None
            self._set("TRAVEL_1")
            return True

        if ms.startswith("AT_STOP_"):
            serviced_tag = self.stops[self.stop_index] if self.stop_index < len(self.stops) else None
            fleet.emit_confirm(self.state, stop=serviced_tag, location=LOC_STOP)
            self._close_gate()
            is_last = (self.stop_index >= len(self.stops) - 1)
            if is_last:
                # Direction flips to INBOUND at the swap on the LAST serviced stop.
                self.state.direction = "inbound"
                self.state.current_stop = None
                self._set("RETURN")
            else:
                self.stop_index += 1
                self.state.current_stop = self.stops[self.stop_index]
                self._set(f"TRAVEL_{self.stop_index + 1}")
            return True

        if ms == "AT_HOME_UNLOAD":
            fleet.emit_confirm(self.state, stop=None, location=LOC_HOME)
            self._close_gate()
            self._set("IDLE_HOME")
            # Hand the terminator to the existing home-arrival path.
            self.state.end_cycle_request = True
            return True

        return False
