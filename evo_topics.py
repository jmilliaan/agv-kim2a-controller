"""
evo_topics.py — the store <-> AGV MQTT contract (single source of truth).

Structurally final per evo-system_mqtt_design.md §3; topic *names* are provisional.
Both the store controller and the (future) AGV MQTT layer must agree on this module.
"""

AGV_IDS = ("agv1", "agv2")
LOOP_IDS = ("l1", "l2", "l3", "l4")

# ── AGV -> Store leaves (the store SUBSCRIBES to these) ───────────────────────
AGV_HEALTH    = "health"      # retained, LWT target
AGV_STATE     = "state"       # retained snapshot
AGV_HEARTBEAT = "heartbeat"   # ~1 Hz
AGV_POS       = "pos"         # one per RFID tag read — arbiter fast path
AGV_EVENT     = "event"       # typed event stream
AGV_ACK       = "ack"         # command acknowledgments
AGV_LEAVES    = (AGV_HEALTH, AGV_STATE, AGV_HEARTBEAT, AGV_POS, AGV_EVENT, AGV_ACK)

# ── Store -> AGV command leaves (the store PUBLISHES these) ───────────────────
CMD_MISSION = "mission"
CMD_TRAFFIC = "traffic"
CMD_CONTROL = "control"

# ── Event types (agv/{id}/event) ─────────────────────────────────────────────
EV_STATE_CHANGE    = "state_change"
EV_CONFIRM         = "confirm"
EV_FAULT_RAISED    = "fault_raised"
EV_FAULT_CLEARED   = "fault_cleared"
EV_TRAFFIC_HOLD    = "traffic_hold"
EV_TRAFFIC_RESUMED = "traffic_resumed"

# ── Command actions ──────────────────────────────────────────────────────────
TRAFFIC_STOP = "stop"
TRAFFIC_GO   = "go"
CONTROL_ACTIONS = ("pause", "resume", "reset", "estop")

# ── ACK statuses ─────────────────────────────────────────────────────────────
ACK_ACCEPTED   = "accepted"
ACK_REJECTED   = "rejected"
ACK_SUPERSEDED = "superseded"

# ── Direction ────────────────────────────────────────────────────────────────
DIR_INBOUND  = "inbound"
DIR_OUTBOUND = "outbound"

# ── Store-state leaves (retained; for monitors / late joiners) ───────────────
STORE_MODE     = "store/state/mode"
MODE_DUAL      = "dual_agv"
MODE_SINGLE    = "single_agv"


# ── Topic builders ───────────────────────────────────────────────────────────

def agv_topic(agv_id: str, leaf: str) -> str:
    return f"agv/{agv_id}/{leaf}"

def agv_wildcard(leaf: str) -> str:
    """Subscription pattern across both AGVs, e.g. agv/+/pos."""
    return f"agv/+/{leaf}"

def cmd_topic(agv_id: str, leaf: str) -> str:
    return f"store/cmd/{agv_id}/{leaf}"

def store_queue_topic(loop_id: str) -> str:
    return f"store/state/queue/{loop_id}"

def store_dispatch_topic(loop_id: str) -> str:
    return f"store/state/dispatch/{loop_id}"


def parse_agv_topic(topic: str):
    """`agv/agv1/pos` -> ('agv1', 'pos'); returns (None, None) if not an AGV topic."""
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "agv":
        return parts[1], parts[2]
    return None, None
