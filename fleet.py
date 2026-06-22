"""
fleet.py — EVO fleet publish helpers (asyncio side) [EVO]

Thin helpers that asyncio tasks (rfid_processor, heartbeat_publisher, mode_manager,
the mission FSM) call to put typed messages onto `state.mqtt_out_queue`. The MQTT
client thread drains that queue and publishes — these helpers NEVER touch paho or
hardware (Key design rule 11). Every helper is a no-op when FLEET_MODE is off, so
callers can stay lean and standalone behaviour is unchanged.

Outgoing `seq` is a single per-process monotonic counter. All callers here run on
the one asyncio loop thread, so the bare counter is GIL-safe (no lock needed).

Payload schemas: evo-system_mqtt_design.md §8 / agv_unit_update_plan §11.
"""

import asyncio
import itertools
import logging
import time

import config
import evo_topics as T

logger = logging.getLogger(__name__)

# Per-AGV monotonic outgoing sequence for ordered streams (pos / heartbeat / event).
_out_seq = itertools.count(0)


def next_seq() -> int:
    return next(_out_seq)


def enqueue(state, leaf: str, payload: dict, qos: int = 1, retain: bool = False):
    """Stamp `ts` and put (topic, payload, qos, retain) on the asyncio→MQTT bridge.

    No-op when FLEET_MODE is off. Uses put_nowait — callers run on the loop thread
    and the queue is unbounded, so this never blocks.
    """
    if not config.FLEET_MODE:
        return
    payload.setdefault("ts", time.time())
    topic = T.agv_topic(config.MQTT_CLIENT_ID, leaf)
    try:
        state.mqtt_out_queue.put_nowait((topic, payload, qos, retain))
    except Exception:
        # The bridge queue is unbounded; a failure here must never break control.
        pass


# ── Typed publishers ──────────────────────────────────────────────────────────

def publish_pos(state, tag_id, direction: str):
    """One publish per localizing RFID tag read — the store arbiter's fast path."""
    enqueue(state, T.AGV_POS, {
        "seq":       next_seq(),
        "tag_id":    tag_id,
        "direction": direction,
    }, qos=1, retain=False)


def publish_heartbeat(state):
    """~1 Hz liveness beat (QoS0, not retained). Absence is the down signal."""
    enqueue(state, T.AGV_HEARTBEAT, {
        "seq":           next_seq(),
        "mission_state": state.mission_state,
        "last_tag":      state.last_rfid_tag,
    }, qos=0, retain=False)


def _fault_block(state):
    """Build the {code, active} fault sub-object from local error state."""
    if state.system_error:
        return {"code": state.system_error_detail or "system_error", "active": True}
    if state.motor_fault:
        return {"code": f"drive_fault:{state.motor_fault}", "active": True}
    if state.sensor_error:
        return {"code": state.sensor_error_detail or "sensor_error", "active": True}
    return {"code": None, "active": False}


def publish_state_snapshot(state):
    """Refresh the retained `agv/{id}/state` truth topic (QoS1, retained)."""
    mission = state.mission or {}
    enqueue(state, T.AGV_STATE, {
        "mission_state": state.mission_state,
        "trip_id":       mission.get("trip_id"),
        "last_tag":      state.last_rfid_tag,
        "direction":     state.direction,
        "current_stop":  state.current_stop,
        "fault":         _fault_block(state),
    }, qos=1, retain=True)


def emit_event(state, ev_type: str, **fields):
    """Publish a typed event on agv/{id}/event (QoS1). `fields` are type-specific."""
    payload = {"seq": next_seq(), "type": ev_type}
    payload.update(fields)
    enqueue(state, T.AGV_EVENT, payload, qos=1, retain=False)


# ── Convenience wrappers for the common events ───────────────────────────────

def emit_state_change(state, from_state, to_state):
    emit_event(state, T.EV_STATE_CHANGE, **{"from": from_state, "to": to_state})
    # The retained snapshot must track every state change.
    publish_state_snapshot(state)


def emit_confirm(state, stop, location):
    emit_event(state, T.EV_CONFIRM, stop=stop, location=location)


def emit_fault_raised(state, code, detail=""):
    emit_event(state, T.EV_FAULT_RAISED, code=code, detail=detail)


def emit_fault_cleared(state, code, detail=""):
    emit_event(state, T.EV_FAULT_CLEARED, code=code, detail=detail)


def emit_traffic_hold(state):
    emit_event(state, T.EV_TRAFFIC_HOLD)


def emit_traffic_resumed(state):
    emit_event(state, T.EV_TRAFFIC_RESUMED)


# ── Heartbeat task ────────────────────────────────────────────────────────────

async def heartbeat_publisher(state):
    """~HEARTBEAT_HZ liveness beat + a periodic retained `state` refresh.

    Started only under FLEET_MODE (see main.py). Its *absence* is the store's
    primary down signal (mqtt_design §7); the retained snapshot keeps late
    joiners / the operator app in sync.
    """
    period = 1.0 / max(0.1, config.MQTT_HEARTBEAT_HZ)
    logger.info("[HEARTBEAT] Started at %.1f Hz.", config.MQTT_HEARTBEAT_HZ)
    # Seed the retained snapshot once at boot so a late joiner has truth.
    publish_state_snapshot(state)
    while True:
        publish_heartbeat(state)
        publish_state_snapshot(state)
        await asyncio.sleep(period)
