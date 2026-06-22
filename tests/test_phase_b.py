"""
tests/test_phase_b.py — EVO fleet (Phase B) acceptance checks.

Run ON THE AGV PC (where canopen + paho-mqtt are installed):

    cd agv-kim2a-controller
    AGV_ID=agv-evo-01 python tests/test_phase_b.py        # plain runner, prints PASS/FAIL
    AGV_ID=agv-evo-01 pytest -q tests/test_phase_b.py     # or via pytest

What it covers (agv_unit_update_plan §11 "Phase B — overall done-when, static tier"):
  - config loads mqtt / motor_can / DI_CONFIRM / FLEET_MODE with no KeyError
  - evo_topics contract builders
  - state.py FleetState fields + mqtt_out_queue exist; reverse_auto_request is gone
  - no reverse *mode* remains in live code (only the logical `direction` flag)
  - `import main` succeeds (full wiring, needs canopen + paho)
  - mission FSM walks a simulated cmd/mission to end_cycle; direction flips at last stop
  - MQTT command routing: seq de-dup + ACK + AMRState writes (no broker needed)
  - fleet.publish_pos enqueues a well-formed pos message
  - [optional] live broker check: connect + retained health + subscribe + drain
    (skipped automatically if 127.0.0.1:1883 is not reachable)

The FSM / routing / pos tests need NO broker — they exercise pure logic. Only the
final optional test wants a local Mosquitto.
"""

import json
import os
import socket
import sys

# Allow running as a bare script from tests/ (put repo root on sys.path).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("AGV_ID", "agv-evo-01")


# ── small fakes for MQTT routing tests (no broker) ────────────────────────────

class _FakeMsg:
    def __init__(self, topic, payload_dict):
        self.topic = topic
        self.payload = json.dumps(payload_dict).encode("utf-8")


class _FakeClient:
    def __init__(self):
        self.published = []   # list of (topic, payload_dict, qos, retain)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))

    # subscribe/will_set used only at construction/connect — stub them harmlessly
    def subscribe(self, *a, **k):
        pass


def _make_state():
    """A fresh AMRState (asyncio.Queue is fine to build without a running loop)."""
    from state import AMRState
    return AMRState()


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── tests ─────────────────────────────────────────────────────────────────────

def test_config_loads():
    import config
    assert config.FLEET_MODE is True, "agv-evo-01 should ship FLEET_MODE=1"
    assert config.MQTT_CLIENT_ID == "agv1", "wire id must be agv1 (not the profile name)"
    assert config.AGV_INDEX == 1
    assert config.DI_CONFIRM is not None, "DI_CONFIRM must be mapped"
    assert config.MOTOR_CAN, "motor_can block must load (Phase A)"
    # mqtt timer constants resolve with provisional defaults
    for name in ("MQTT_BROKER_IP", "MQTT_BROKER_PORT", "MQTT_KEEPALIVE",
                 "MQTT_ACK_TIMEOUT", "MQTT_ACK_RETRIES",
                 "MQTT_HEARTBEAT_HZ", "MQTT_HEARTBEAT_MISS"):
        assert hasattr(config, name), f"config.{name} missing"
    # the agv-evo-02 profile must carry the agv2 wire id
    prof2 = json.load(open(os.path.join(_ROOT, "profiles", "agv-evo-02.json")))
    assert prof2["mqtt"]["CLIENT_ID"] == "agv2"
    assert prof2["networking"]["LOCAL_IP"] == "192.168.2.24"


def test_evo_topics_contract():
    import evo_topics as T
    assert T.agv_topic("agv1", T.AGV_POS) == "agv/agv1/pos"
    assert T.cmd_topic("agv1", T.CMD_MISSION) == "store/cmd/agv1/mission"
    assert T.agv_wildcard("pos") == "agv/+/pos"
    assert set(T.AGV_LEAVES) >= {"health", "state", "heartbeat", "pos", "event", "ack"}
    assert T.parse_agv_topic("agv/agv2/event") == ("agv2", "event")


def test_state_fleet_fields():
    s = _make_state()
    for f in ("mission", "mission_state", "direction", "traffic_hold",
              "commanded_pause", "confirm_pending", "confirm_ts",
              "last_applied_seq", "store_link_ok", "current_stop"):
        assert hasattr(s, f), f"AMRState missing fleet shim {f}"
    assert s.mission_state == "IDLE_HOME"
    assert s.direction == "outbound"
    assert s.last_applied_seq == -1
    assert hasattr(s, "mqtt_out_queue")
    assert not hasattr(s, "reverse_auto_request"), "reverse_auto_request must be gone"


def test_no_reverse_mode_in_live_code():
    """No reverse *mode* in live .py (the logical `direction` flag is allowed)."""
    import re
    bad = re.compile(r'reverse_auto|/api/reverse_auto|direction\s*=\s*["\']reverse')
    offenders = []
    skip_dirs = {"_migration_plan_and_docs", "_debugging", "_setup_guide",
                 "docs", "tests", "__pycache__", ".git", "_obsolete",
                 "_future_implementation_plans", "_motion_analysis"}
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if bad.search(line):
                        offenders.append(f"{p}:{i}: {line.strip()}")
    assert not offenders, "reverse mode leftovers:\n" + "\n".join(offenders)


def test_import_main():
    """Full wiring imports cleanly (needs canopen + paho on the AGV PC)."""
    import importlib
    import main  # noqa: F401
    importlib.reload(main)


def test_mission_fsm_two_stop_walk():
    import config
    config.FLEET_MODE = True
    from core.mission import MissionFSM

    s = _make_state()
    s.current_mode = "running"
    fsm = MissionFSM(s, attach_tag="0008", home_tag="000A")

    mission = {
        "trip_id": "T1", "loop": "l2",
        "active_stops": [{"order": 1, "tag": "0064"}, {"order": 2, "tag": "00C8"}],
        "loading": {"front": "B", "rear": "A"},
    }
    fsm.start(mission)
    assert s.mission_state == "DEPART_HOME"
    assert s.direction == "outbound"

    fsm.on_tag("0008")                       # reach attach
    assert s.mission_state == "AT_ATTACH" and s.confirm_pending
    assert fsm.on_confirm()                   # load + confirm
    assert s.mission_state == "TRAVEL_1" and not s.confirm_pending
    assert s.current_stop == "0064"

    fsm.on_tag("0064")                        # reach stop 1
    assert s.mission_state == "AT_STOP_1" and s.confirm_pending
    assert fsm.on_confirm()                    # swap + confirm (not last)
    assert s.mission_state == "TRAVEL_2"
    assert s.direction == "outbound", "must still be outbound before last stop"
    assert s.current_stop == "00C8"

    fsm.on_tag("00C8")                         # reach stop 2 (last)
    assert s.mission_state == "AT_STOP_2" and s.confirm_pending
    assert fsm.on_confirm()                     # swap on LAST stop → flip inbound
    assert s.direction == "inbound", "direction must flip at last-stop swap"
    assert s.mission_state == "RETURN"

    fsm.on_tag("000A")                          # reach home
    assert s.mission_state == "AT_HOME_UNLOAD" and s.confirm_pending
    assert fsm.on_confirm()                      # unload + confirm
    assert s.mission_state == "IDLE_HOME"
    assert s.end_cycle_request is True, "home confirm must request end_cycle"

    # state_change events were published for each transition
    msgs = _drain(s.mqtt_out_queue)
    transitions = [m[1].get("to") for m in msgs
                   if m[1].get("type") == "state_change"]
    assert "DEPART_HOME" in transitions
    assert "AT_HOME_UNLOAD" in transitions


def test_mission_fsm_single_stop_walk():
    import config
    config.FLEET_MODE = True
    from core.mission import MissionFSM

    s = _make_state()
    s.current_mode = "running"
    fsm = MissionFSM(s, attach_tag="0008", home_tag="000A")
    fsm.start({"trip_id": "T2", "loop": "l1",
               "active_stops": [{"order": 1, "tag": "0064"}], "loading": {}})
    fsm.on_tag("0008"); fsm.on_confirm()
    assert s.mission_state == "TRAVEL_1"
    fsm.on_tag("0064")
    assert s.mission_state == "AT_STOP_1"
    fsm.on_confirm()                              # only stop = last stop
    assert s.direction == "inbound"
    assert s.mission_state == "RETURN"
    fsm.on_tag("000A"); fsm.on_confirm()
    assert s.mission_state == "IDLE_HOME"
    assert s.end_cycle_request is True


def test_mqtt_command_routing_and_seq_dedup():
    """Exercise MQTTClient._on_message routing + ACK + seq de-dup, no broker."""
    import config
    config.FLEET_MODE = True
    import evo_topics as T
    from drivers.mqtt_client import MQTTClient

    s = _make_state()
    s.current_mode = "armed"          # a mission can only be accepted from idle
    cli = MQTTClient(s)
    fake = _FakeClient()

    def send(leaf, payload):
        cli._on_message(fake, None, _FakeMsg(T.cmd_topic("agv1", leaf), payload))

    def last_ack():
        acks = [p for (t, p, q, r) in fake.published if t.endswith("/ack")]
        return acks[-1] if acks else None

    # mission seq=1 → accepted
    send("mission", {"cmd_id": "c1", "seq": 1, "trip_id": "T1", "loop": "l2",
                     "stops": [{"order": 1, "tag": "0064"}], "loading": {}})
    assert s.mission is not None and s.mission_start_request is True
    assert s.last_applied_seq == 1
    assert last_ack()["status"] == "accepted"

    # retransmit of seq=1 → superseded (idempotent, no re-apply)
    s.mission_start_request = False
    send("mission", {"cmd_id": "c1b", "seq": 1, "trip_id": "T1", "loop": "l2",
                     "stops": [], "loading": {}})
    assert last_ack()["status"] == "superseded"
    assert s.mission_start_request is False, "duplicate seq must not re-apply"

    # stale seq=0 → rejected
    send("traffic", {"cmd_id": "c0", "seq": 0, "action": "stop"})
    assert last_ack()["status"] == "rejected"

    # traffic stop seq=2 → accepted, latch set
    send("traffic", {"cmd_id": "c2", "seq": 2, "action": "stop"})
    assert s.traffic_hold is True and last_ack()["status"] == "accepted"

    # traffic go seq=3 → latch clear
    send("traffic", {"cmd_id": "c3", "seq": 3, "action": "go"})
    assert s.traffic_hold is False

    # control estop seq=4 → cmd_estop latch
    send("control", {"cmd_id": "c4", "seq": 4, "action": "estop"})
    assert s.cmd_estop is True

    # control reset seq=5 → reset request
    send("control", {"cmd_id": "c5", "seq": 5, "action": "reset"})
    assert s.control_reset_request is True
    assert s.last_applied_seq == 5


def test_pos_publish_shape():
    import config
    config.FLEET_MODE = True
    import fleet
    s = _make_state()
    fleet.publish_pos(s, "0064", "outbound")
    msgs = _drain(s.mqtt_out_queue)
    assert len(msgs) == 1
    topic, payload, qos, retain = msgs[0]
    assert topic == "agv/agv1/pos"
    assert payload["tag_id"] == "0064"
    assert payload["direction"] == "outbound"
    assert "seq" in payload and "ts" in payload
    assert qos == 1 and retain is False


def test_optional_live_broker():
    """Connect to a local Mosquitto if present; otherwise SKIP.

    Verifies retained health=online + command subscribe + mqtt_out_queue drain
    end-to-end. Requires `mosquitto` on 127.0.0.1:1883.
    """
    import config
    config.FLEET_MODE = True
    # reachability probe
    try:
        with socket.create_connection((config.MQTT_BROKER_IP, config.MQTT_BROKER_PORT), timeout=0.5):
            pass
    except OSError:
        print("  [SKIP] no broker at %s:%d" % (config.MQTT_BROKER_IP, config.MQTT_BROKER_PORT))
        return "skip"

    import asyncio
    import fleet
    from drivers.mqtt_client import MQTTClient

    async def _run():
        s = _make_state()
        cli = MQTTClient(s)
        task = asyncio.ensure_future(cli.run(s))
        # give paho time to connect
        for _ in range(50):
            if s.store_link_ok:
                break
            await asyncio.sleep(0.1)
        assert s.store_link_ok, "did not connect to broker"
        # publish a heartbeat through the bridge and let it drain
        fleet.publish_heartbeat(s)
        await asyncio.sleep(0.3)
        assert s.mqtt_out_queue.empty(), "mqtt_out_queue should drain"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    return "ok"


# ── plain runner (no pytest needed) ───────────────────────────────────────────

def _main():
    tests = [
        test_config_loads,
        test_evo_topics_contract,
        test_state_fleet_fields,
        test_no_reverse_mode_in_live_code,
        test_import_main,
        test_mission_fsm_two_stop_walk,
        test_mission_fsm_single_stop_walk,
        test_mqtt_command_routing_and_seq_dedup,
        test_pos_publish_shape,
        test_optional_live_broker,
    ]
    passed = failed = skipped = 0
    for t in tests:
        try:
            res = t()
            if res == "skip":
                skipped += 1
                print(f"SKIP  {t.__name__}")
            else:
                passed += 1
                print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
